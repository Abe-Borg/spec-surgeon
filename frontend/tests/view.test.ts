/**
 * The page's reducer (lib/view.ts): every frame of the job stream, the
 * optimistic bubbles, and the rule that the view ending a job is authoritative.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { JobEvent, Message, View } from "../src/types.ts";
import { EMPTY_VIEW, INITIAL_STATE, normalizeView, notesOf, reduce, type UiState } from "../src/lib/view.ts";

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

test("job_started adds the user bubble, a live bubble with its working line, and clears the question", () => {
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
  // The working line shows the moment the request is sent, before the first frame names the work.
  assert.deepEqual(state.live, { text: "", thinking: "", status: { kind: "working", text: "" }, steps: [], call: 0 });
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

const PLAN = { groups: [{ id: "", label: "", count: 1, files: [], changes: [{ title: "Bold", summary: "Bold the title.", details: [] }], assumptions: [] }], targets: [], set_aside: [], stages: 1 };
const SAMPLE = { source: "a", name: "a.docx", images: ["/api/image?path=a"], pages_before: 1, pages_after: 1, passed: true };

const NOTE = { name: "a.docx", text: "“TITLE” also occurs in the body (1×); this change leaves it as it is." };

test("a message sent over a preview takes the old plan and pages off the workbench at once", () => {
  const before = loaded({ stage: "preview", plan: PLAN, samples: [SAMPLE], warnings: ["Advisory"], notes: [NOTE], blocking: true });
  const state = reduce(before, { type: "job_started", userText: "Also make it italic" });
  assert.equal(state.view.plan, null);
  assert.deepEqual(state.view.samples, []);
  assert.deepEqual(state.view.warnings, []);
  assert.deepEqual(state.view.notes, []);
  assert.equal(state.view.blocking, false);
});

test("creating copies keeps the approved plan and pages on screen until the job's view", () => {
  const before = loaded({ stage: "preview", plan: PLAN, samples: [SAMPLE], warnings: ["Advisory"] });
  const state = reduce(before, { type: "job_started", userText: null });
  assert.deepEqual(state.view.plan, PLAN);
  assert.deepEqual(state.view.samples, [SAMPLE]);
  assert.deepEqual(state.view.warnings, ["Advisory"]);
});

test("a new plan clears the pages of the plan before it; its own pages follow", () => {
  const next = { ...PLAN, stages: 2 };
  const fresh = { ...SAMPLE, source: "b", name: "b.docx" };
  let state = reduce(loaded({ stage: "preview", plan: PLAN, samples: [SAMPLE], warnings: ["Old finding"], notes: [NOTE], blocking: true }),
    { type: "job_started", userText: null });
  state = run(state, { type: "plan", plan: next });
  assert.deepEqual(state.view.plan, next);
  assert.deepEqual(state.view.samples, []);
  assert.deepEqual(state.view.warnings, []);
  assert.deepEqual(state.view.notes, []);
  assert.equal(state.view.blocking, false);
  state = run(state, { type: "samples", samples: [fresh], warnings: [], blocking: false });
  assert.deepEqual(state.view.samples, [fresh]);
  assert.deepEqual(state.view.notes, []); // an older server's frame carries none
  state = run(state, { type: "samples", samples: [fresh], warnings: ["x"], notes: [NOTE], blocking: false });
  assert.deepEqual(state.view.notes, [NOTE]);
});

test("the Worth knowing notes keep their text and document name, and drop what is not a note", () => {
  const plan = { name: "", text: "A file changed while this preview was being made." };
  assert.deepEqual(notesOf([NOTE, plan, { text: "No name" }, { name: "b.docx" }, { name: "c", text: "" }, "loose", null, 3]),
    [NOTE, plan, { name: "", text: "No name" }]);
  assert.deepEqual(notesOf(undefined), []);
  assert.deepEqual(notesOf({ text: "not a list" }), []);
  assert.deepEqual(normalizeView({ notes: [NOTE, { bad: 1 }] }).notes, [NOTE]);
});

test("local user bubbles never reuse an id", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "one" });
  state = reduce(state, { type: "job_ended" });
  state = reduce(state, { type: "job_started", userText: "two" });
  const ids = state.view.messages.map((m) => m.id);
  assert.equal(new Set(ids).size, 2);
});

test("status, activity, thinking and text deltas fill the live bubble; an activity names the work, text clears it", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "status", kind: "reading", text: "" },
    { type: "activity", text: "Reading the template's header" },
    { type: "activity", text: "  " },
    { type: "thinking_delta", text: "The body " },
    { type: "thinking_delta", text: "is Arial." },
  );
  assert.deepEqual(state.live?.status, { kind: "working", text: "Reading the template's header" });
  assert.deepEqual(state.live?.steps, [{ kind: "activity", text: "Reading the template's header" }]);
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
  assert.deepEqual(state.asides.m2, { thinking: "hmm", steps: [{ kind: "activity", text: "Reading the documents" }] });
  assert.deepEqual(state.live, { text: "", thinking: "", status: null, steps: [], call: 0 });
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
  assert.deepEqual(state.asides.m9?.steps, [{ kind: "activity", text: "Reading" }]);
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
  assert.deepEqual(v.notes, []);
  assert.deepEqual(v.plan?.targets, []);
  assert.equal(v.plan?.stages, 1);
  assert.equal(v.cost.usd, 1);
  assert.equal(v.cost.calls, 0);
  assert.equal(v.action.kind, "none");
  assert.equal(v.update.state, "none");
  assert.equal(normalizeView(null).stage, "empty");
  assert.equal(normalizeView({ plan: null }).plan, null);
});

test("a retrying status drops the abandoned attempt's words and thinking", () => {
  const state = run(
    loaded(),
    { type: "status", kind: "thinking" },
    { type: "thinking_delta", text: "first thoughts" },
    { type: "text_delta", text: "I'll make the title bold and" },
    { type: "status", kind: "retrying", text: "Sending again…" },
    { type: "text_delta", text: "I'll make the title bold." },
  );
  assert.equal(state.live?.text, "I'll make the title bold.");
  assert.equal(state.live?.thinking, "");
  assert.equal(state.live?.status, null); // the reply being written clears the strip, as always
});

test("a fresh view keeps the message objects already shown, so their bubbles are not parsed again", () => {
  const first = message("m1", "user", "Make the title bold.");
  const second = message("m2", "assistant", "Done — the **title** is bold.");
  const before = loaded({ messages: [first, second] });
  // The same conversation as new JSON (a refresh, the job's `done`), with one reply edited and one added.
  const again = before.view.messages.map((m) => ({ ...m }));
  const refreshed = reduce(before, { type: "view", view: view({ messages: again }) });
  assert.equal(refreshed.view.messages[0], first);
  assert.equal(refreshed.view.messages[1], second);
  const edited = { ...second, text: "Done — the **title** is bold and centered." };
  const third = message("m3", "assistant", "Anything else?");
  const done = run(before, { type: "done", state: view({ messages: [{ ...first }, edited, third] }) });
  assert.equal(done.view.messages[0], first);
  assert.notEqual(done.view.messages[1], second);
  assert.equal(done.view.messages[1].text, edited.text);
  assert.deepEqual(done.view.messages[2], third);
  assert.equal(done.live, null);
});

test("a view without the to-do fields (an older server) normalizes to an empty list and no pass", () => {
  const v = normalizeView({ version: "1.6.0", action: { label: "x", token: "t", kind: "approve" } });
  assert.deepEqual(v.todo, []);
  assert.deepEqual(v.focus, []);
  assert.equal(v.action.can_hold, false);
  const full = normalizeView({ todo: [{ path: "/a", name: "a.docx" }], focus: ["/a", 3], action: { label: "x", token: "t", kind: "approve", can_hold: true } });
  assert.deepEqual(full.todo, [{ path: "/a", name: "a.docx" }]);                // an entry from before it said why
  const said = normalizeView({ todo: [{ path: "/a", name: "a.docx", why: "asked", reason: "You asked to set it aside for later." }] });
  assert.deepEqual(said.todo[0], { path: "/a", name: "a.docx", why: "asked", reason: "You asked to set it aside for later." });
  assert.deepEqual(full.focus, ["/a"]);
  assert.equal(full.action.can_hold, true);
});

test("a new model call sets the last call's words aside as a step, where that call began", () => {
  let state = reduce(loaded(), { type: "job_started", userText: "go" });
  state = run(
    state,
    { type: "status", kind: "thinking", text: "Thinking…" },
    { type: "thinking_delta", text: "Look at the template." },
    { type: "text_delta", text: "I'll read the template first. " },
    { type: "activity", text: "Reading the template" },
    { type: "status", kind: "thinking", text: "Thinking…" },
    { type: "thinking_delta", text: "The headers differ." },
    { type: "text_delta", text: "The headers use Times New Roman." },
  );
  // Not the reply the turn ends with: a step before the tool it called, and the new call writes afresh.
  assert.deepEqual(state.live?.steps, [
    { kind: "said", text: "I'll read the template first." },
    { kind: "activity", text: "Reading the template" },
  ]);
  assert.equal(state.live?.text, "The headers use Times New Roman.");
  assert.equal(state.live?.thinking, "Look at the template.\n\nThe headers differ.");
  // The finished reply keeps every step under its id.
  state = run(state, { type: "message", message: message("m1", "assistant", "The headers use Times New Roman.") });
  assert.equal(state.asides.m1?.steps.length, 2);
});

test("a call that wrote no words leaves no step, and a resent attempt keeps the steps before it", () => {
  let state = run(
    loaded(),
    { type: "status", kind: "thinking" },
    { type: "activity", text: "Reading the documents" },
    { type: "status", kind: "thinking" },
    { type: "text_delta", text: "I'll make the title bold and" },
    { type: "status", kind: "retrying", text: "Sending again…" },
  );
  assert.deepEqual(state.live?.steps, [{ kind: "activity", text: "Reading the documents" }]);
  assert.equal(state.live?.text, "");
  state = run(state, { type: "text_delta", text: "I'll make the title bold." });
  assert.equal(state.live?.text, "I'll make the title bold.");
});

test("the view says whether this computer can make PDFs for Export; an older server holds nothing back", () => {
  const v = normalizeView({
    pdf: { available: false, renderer: "", reason: "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found." },
  });
  assert.equal(v.pdf.available, false);
  assert.equal(normalizeView({}).pdf.available, true);
  assert.equal("pdfs" in EMPTY_VIEW, false);
});
