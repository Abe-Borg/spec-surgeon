/**
 * The page's reducer (lib/view.ts): every frame of the job stream, the
 * optimistic bubbles, and the rule that the view ending a job is authoritative.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { JobEvent, Message, View } from "../src/types.ts";
import { EMPTY_VIEW, INITIAL_STATE, normalizeView, reduce, type UiState } from "../src/lib/view.ts";

const message = (id: string, role: Message["role"], text: string): Message => ({ id, role, text, at: 1 });

function view(overrides: Partial<View> = {}): View {
  return { ...EMPTY_VIEW, ...overrides };
}

function loaded(overrides: Partial<View> = {}): UiState {
  return reduce(INITIAL_STATE, { type: "view", view: view(overrides) });
}

function run(state: UiState, ...events: JobEvent[]): UiState {
  return events.reduce((s, event) => reduce(s, { type: "event", event }), state);
}

test("the first view marks the page loaded and leaves no live bubble when idle", () => {
  const state = loaded({ version: "1.6.0" });
  assert.equal(state.loaded, true);
  assert.equal(state.view.version, "1.6.0");
  assert.equal(state.live, null);
});

test("a busy view with no stream attached gets a working bubble, so the window shows Stop", () => {
  const state = loaded({ busy: true });
  assert.ok(state.live);
  assert.equal(state.live?.status?.kind, "working");
});

test("job_started adds the user bubble, an empty live bubble, and clears the question", () => {
  const before = loaded({
    stage: "question",
    questions: ["Body only?"],
    choices: [{ id: "c1", label: "Body", words: [], detail: "" }],
    chips: ["Everything"],
  });
  const state = reduce(before, { type: "job_started", userText: "Everything" });
  assert.equal(state.view.busy, true);
  assert.equal(state.view.messages.length, 1);
  assert.equal(state.view.messages[0].role, "user");
  assert.equal(state.view.messages[0].text, "Everything");
  assert.deepEqual(state.live, { text: "", thinking: "", status: null, activities: [] });
  assert.deepEqual(state.view.questions, []);
  assert.deepEqual(state.view.choices, []);
  assert.deepEqual(state.view.chips, []);
  assert.equal(state.error, null);
});

test("the primary action starts a job without a user bubble", () => {
  const state = reduce(loaded(), { type: "job_started", userText: null });
  assert.equal(state.view.messages.length, 0);
  assert.ok(state.live);
});

test("local user bubbles never reuse an id", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "one" });
  state = reduce(state, { type: "job_ended" });
  state = reduce(state, { type: "job_started", userText: "two" });
  const ids = state.view.messages.map((m) => m.id);
  assert.equal(new Set(ids).size, 2);
});

test("status, activity, thinking and text deltas fill the live bubble; text clears the status", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "status", kind: "reading", text: "" },
    { type: "activity", text: "Reading the template's header" },
    { type: "activity", text: "  " },
    { type: "thinking_delta", text: "The body " },
    { type: "thinking_delta", text: "is Arial." },
  );
  assert.equal(state.live?.status?.kind, "reading");
  assert.deepEqual(state.live?.activities, ["Reading the template's header"]);
  assert.equal(state.live?.thinking, "The body is Arial.");
  state = run(state, { type: "text_delta", text: "Here " }, { type: "text_delta", text: "we go." });
  assert.equal(state.live?.text, "Here we go.");
  assert.equal(state.live?.status, null);
});

test("message finalizes the bubble: appended to the transcript, its aside kept, a fresh bubble waits", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "activity", text: "Reading the documents" },
    { type: "thinking_delta", text: "hmm" },
    { type: "text_delta", text: "Done." },
    { type: "message", message: message("m2", "assistant", "Done.") },
  );
  assert.equal(state.view.messages.length, 2);
  assert.equal(state.view.messages[1].id, "m2");
  assert.deepEqual(state.asides.m2, { thinking: "hmm", activities: ["Reading the documents"] });
  assert.deepEqual(state.live, { text: "", thinking: "", status: null, activities: [] });
});

test("a message with a known id replaces that message instead of appending", () => {
  let state = loaded({ messages: [message("m1", "assistant", "draft")] });
  state = reduce(state, { type: "job_started", userText: null });
  state = run(state, { type: "message", message: message("m1", "assistant", "final") });
  assert.deepEqual(
    state.view.messages.map((m) => m.text),
    ["final"],
  );
});

test("an app note is appended and keeps no aside", () => {
  let state = reduce(loaded(), { type: "job_started", userText: null });
  state = run(state, { type: "message", message: message("n1", "app", "Work paused") });
  assert.equal(state.view.messages.at(-1)?.role, "app");
  assert.equal(state.asides.n1, undefined);
});

test("plan, samples, results, question and cost update the view live", () => {
  const plan = { groups: [], targets: [], set_aside: [], stages: 2 };
  const samples = [{ source: "a", name: "a.docx", images: [], pages_before: 1, pages_after: 2, passed: true }];
  const results = [{ source: "a", output: "out/a.docx", name: "a.docx" }];
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "plan", plan },
    { type: "samples", samples, warnings: ["w"], blocking: true },
    { type: "results", results },
    { type: "question", questions: ["q?"], choices: [{ id: "c", label: "C", words: ["x"], detail: "" }], chips: ["a"] },
    { type: "cost", cost: { usd: 0.5, text: "about $0.50", calls: 3 } },
  );
  assert.deepEqual(state.view.plan, plan);
  assert.deepEqual(state.view.samples, samples);
  assert.deepEqual(state.view.warnings, ["w"]);
  assert.equal(state.view.blocking, true);
  assert.deepEqual(state.view.results, results);
  assert.equal(state.view.stage, "question");
  assert.deepEqual(state.view.questions, ["q?"]);
  assert.equal(state.view.choices[0].words[0], "x");
  assert.deepEqual(state.view.chips, ["a"]);
  assert.equal(state.view.cost.usd, 0.5);
});

test("done replaces the whole view and ends the bubble; the local user bubble gives way to the server's", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "typed" });
  state = run(state, { type: "text_delta", text: "partial" });
  const final = view({ stage: "preview", messages: [message("s1", "user", "typed"), message("s2", "assistant", "ok")] });
  state = run(state, { type: "done", state: final });
  assert.equal(state.live, null);
  assert.equal(state.view.busy, false);
  assert.equal(state.view.stage, "preview");
  assert.deepEqual(
    state.view.messages.map((m) => m.id),
    ["s1", "s2"],
  );
  assert.equal(state.error, null);
});

test("error replaces the view with the rolled-back state and keeps the message as an error bubble", () => {
  let state = reduce(loaded({ messages: [message("m1", "user", "old")] }), { type: "job_started", userText: "new" });
  state = run(state, { type: "status", kind: "planning", text: "" });
  state = run(state, { type: "error", message: "The service could not be reached.", state: view({ messages: [message("m1", "user", "old")] }) });
  assert.equal(state.live, null);
  assert.equal(state.view.busy, false);
  assert.equal(state.error, "The service could not be reached.");
  assert.deepEqual(
    state.view.messages.map((m) => m.id),
    ["m1"],
  );
  // The next job clears the error.
  state = reduce(state, { type: "job_started", userText: "again" });
  assert.equal(state.error, null);
});

test("a stream that ends without done or error leaves the page idle for a refetch", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = reduce(state, { type: "job_ended" });
  assert.equal(state.live, null);
  assert.equal(state.view.busy, false);
});

test("a job that fails to start reports its message and leaves the page idle", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = reduce(state, { type: "job_failed", message: "busy" });
  assert.equal(state.error, "busy");
  assert.equal(state.view.busy, false);
  assert.equal(state.live, null);
});

test("asides survive the authoritative view, keyed by message id", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "activity", text: "Reading" },
    { type: "message", message: message("m9", "assistant", "ok") },
    { type: "done", state: view({ messages: [message("m9", "assistant", "ok")] }) },
  );
  assert.deepEqual(state.asides.m9?.activities, ["Reading"]);
});

test("an update patch moves only the update block", () => {
  const state = reduce(loaded({ stage: "preview" }), {
    type: "update",
    update: { state: "downloading", version: "1.7.0", notes: "", progress: 40, error: "" },
  });
  assert.equal(state.view.update.state, "downloading");
  assert.equal(state.view.update.progress, 40);
  assert.equal(state.view.stage, "preview");
});

test("an unknown frame type is ignored", () => {
  const state = reduce(loaded(), { type: "job_started", userText: "go" });
  const after = reduce(state, { type: "event", event: { type: "something_new" } as unknown as JobEvent });
  assert.deepEqual(after, state);
});

test("normalizeView fills every list and block a server might leave out", () => {
  const v = normalizeView({ version: "2", stage: "preview", plan: { groups: [{ id: "g" }] }, cost: { usd: 1 } });
  assert.equal(v.version, "2");
  assert.equal(v.stage, "preview");
  assert.deepEqual(v.attachments, []);
  assert.deepEqual(v.messages, []);
  assert.deepEqual(v.plan?.targets, []);
  assert.equal(v.plan?.stages, 1);
  assert.equal(v.cost.usd, 1);
  assert.equal(v.cost.calls, 0);
  assert.equal(v.action.kind, "none");
  assert.equal(v.update.state, "none");
  assert.equal(normalizeView(null).stage, "empty");
  assert.equal(normalizeView({ plan: null }).plan, null);
});
