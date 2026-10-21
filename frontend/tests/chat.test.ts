/**
 * What the chat shows (lib/chat.ts): where the choices and chips go and when,
 * the pause card, what the composer may do, and the status strip's words.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Message, View } from "../src/types.ts";
import {
  COMPOSER_NO_DOCUMENTS,
  COMPOSER_NO_KEY,
  STARTERS,
  WORK_TODO,
  composerState,
  costPill,
  endedLive,
  liveHasContent,
  pauseFor,
  promptsFor,
  spentText,
  statusLabel,
  stepsLabel,
  todoFor,
  transcript,
  withLeaving,
} from "../src/lib/chat.ts";
import { EMPTY_VIEW, INITIAL_STATE, emptyLive, reduce, type UiState } from "../src/lib/view.ts";

const message = (id: string, role: Message["role"], text: string): Message => ({ id, role, text, at: 1 });

function state(overrides: Partial<View> = {}): UiState {
  return reduce(INITIAL_STATE, { type: "view", view: { ...EMPTY_VIEW, ...overrides } });
}

const question: Partial<View> = {
  stage: "question",
  messages: [message("u1", "user", "hi"), message("a1", "assistant", "Body only?")],
  questions: ["Body only?"],
  choices: [{ id: "c1", label: "Only the body", words: [], detail: "" }],
  chips: ["Everything", "Just the body"],
};

test("choices and chips appear only in the question stage, under the latest assistant message", () => {
  const items = transcript(state(question));
  assert.deepEqual(
    items.map((i) => i.kind),
    ["message", "message", "prompts"],
  );
  const prompts = items[2];
  assert.equal(prompts.kind, "prompts");
  if (prompts.kind === "prompts") {
    assert.deepEqual(prompts.chips, ["Everything", "Just the body"]);
    assert.equal(prompts.choices[0].id, "c1");
    assert.deepEqual(prompts.questions, ["Body only?"]);
  }
  assert.equal(promptsFor(state({ ...question, stage: "preview" })), null);
  assert.equal(promptsFor(state({ ...question, questions: [], choices: [], chips: [] })), null);
});

test("sending clears the chips: the next job starts without them, and none show while busy", () => {
  const sent = reduce(state(question), { type: "job_started", userText: "Just the body" });
  assert.equal(promptsFor(sent), null);
  const items = transcript(sent);
  assert.deepEqual(
    items.map((i) => i.kind),
    ["message", "message", "message", "live"],
  );
  assert.equal(promptsFor(state({ ...question, busy: true })), null);
});

test("chips alone, without a question or choices, still show", () => {
  const prompts = promptsFor(state({ stage: "question", chips: ["Go on"] }));
  assert.equal(prompts?.kind, "prompts");
});

test("the error bubble follows the transcript and precedes the prompts", () => {
  let s = reduce(state(question), { type: "job_started", userText: "x" });
  s = reduce(s, { type: "job_failed", message: "busy" });
  // Not busy, but the stage was left at question with its chips cleared by the send.
  const kinds = transcript(s).map((i) => i.kind);
  assert.deepEqual(kinds, ["message", "message", "message", "error"]);
});

test("the pause card shows in the budget stage, or whenever the budget says paused, with Continue when the action allows", () => {
  const paused = state({
    stage: "budget",
    budget: { paused: true, steps_per_pause: 48 },
    cost: { usd: 2.41, text: "", calls: 48 },
    action: { label: "Continue", token: "t", kind: "continue" },
  });
  const card = pauseFor(paused);
  assert.equal(card?.kind, "pause");
  if (card?.kind === "pause") {
    assert.equal(card.stepsPerPause, 48);
    assert.equal(card.costText, "about $2.41");
    assert.equal(card.canContinue, true);
  }
  assert.equal(transcript(paused).at(-1)?.kind, "pause");
  assert.equal(pauseFor(state({ stage: "budget", busy: true })), null);
  assert.equal(pauseFor(state({ stage: "preview", budget: { paused: true, steps_per_pause: 48 } }))?.kind, "pause");
  assert.equal(pauseFor(state({ stage: "preview" })), null);
  const noToken = pauseFor(state({ stage: "budget", action: { label: "", token: "", kind: "none" } }));
  assert.equal(noToken?.kind === "pause" && noToken.canContinue, false);
});

test("the composer is disabled only without a key or without an accepted document, and says why", () => {
  const doc = { path: "a", name: "a.docx", kind: "document" as const, state: "ok" as const, notes: [], reason: "", current: "a" };
  assert.deepEqual(composerState({ ...EMPTY_VIEW, connection: { ready: false, origin: "none" } }), {
    canSend: false,
    reason: COMPOSER_NO_KEY,
  });
  assert.deepEqual(composerState({ ...EMPTY_VIEW, connection: { ready: true, origin: "saved" } }), {
    canSend: false,
    reason: COMPOSER_NO_DOCUMENTS,
  });
  // A refused document, a template or a picture does not count.
  assert.equal(
    composerState({
      ...EMPTY_VIEW,
      connection: { ready: true, origin: "saved" },
      attachments: [
        { ...doc, state: "refused" },
        { ...doc, kind: "template" },
        { ...doc, kind: "reference", name: "logo.png" },
      ],
    }).canSend,
    false,
  );
  assert.deepEqual(composerState({ ...EMPTY_VIEW, connection: { ready: true, origin: "saved" }, attachments: [doc] }), {
    canSend: true,
    reason: "",
  });
  // Busy does not disable the composer: typing ahead is allowed.
  assert.equal(composerState({ ...EMPTY_VIEW, busy: true, connection: { ready: true, origin: "saved" }, attachments: [doc] }).canSend, true);
});

test("spending reads as the window says it: an estimate, never a bill", () => {
  assert.equal(spentText({ usd: 0, text: "", calls: 0 }), "less than $0.01");
  assert.equal(spentText({ usd: 0.004, text: "", calls: 1 }), "less than $0.01");
  assert.equal(spentText({ usd: 0.126, text: "", calls: 3 }), "about $0.13");
  assert.equal(spentText({ usd: null, text: "about $1.00", calls: 3 }), "about $1.00");
  assert.equal(spentText({ usd: null, text: "", calls: 3 }), "an amount the price table cannot estimate");
  assert.equal(spentText(null), "an amount the price table cannot estimate");
  assert.equal(costPill({ usd: 0.126, text: "", calls: 3 }), "≈ $0.13");
  assert.equal(costPill({ usd: 0, text: "", calls: 0 }), "≈ $0.00");
  assert.equal(costPill({ usd: null, text: "about $1", calls: 0 }), "≈ about $1");
  assert.equal(costPill(null), "—");
});

test("the status strip prefers the server's sentence, then the kind's label, then Working…", () => {
  assert.equal(statusLabel({ kind: "reading", text: "" }), "Reading the documents…");
  assert.equal(statusLabel({ kind: "creating", text: "Creating 12 copies…" }), "Creating 12 copies…");
  assert.equal(statusLabel({ kind: "whatever", text: "" }), "Working…");
  assert.equal(statusLabel(null), "");
  assert.equal(statusLabel({ kind: "working", text: "" }), "Working…");
  for (const kind of ["thinking", "reading", "planning", "checking", "previewing", "rendering", "looking", "creating", "recovering", "resuming"]) {
    assert.notEqual(statusLabel({ kind, text: "" }), "Working…", `${kind} has its own label`);
  }
});

test("an empty live bubble after a message renders nothing until something arrives", () => {
  assert.equal(liveHasContent(emptyLive()), false);
  assert.equal(liveHasContent({ ...emptyLive(), status: { kind: "working", text: "" } }), true);
  assert.equal(liveHasContent({ ...emptyLive(), steps: [{ kind: "activity", text: "x" }] }), true);
  assert.equal(liveHasContent({ ...emptyLive(), text: "x" }), true);
  assert.equal(liveHasContent({ ...emptyLive(), thinking: "x" }), true);
});

test("the three starters are requests in the user's own words, each with a title", () => {
  assert.equal(STARTERS.length, 3);
  for (const starter of STARTERS) {
    assert.ok(starter.title.length > 0);
    assert.ok(starter.text.length > 20);
  }
});

test("the to-do card closes the transcript once copies are saved and documents wait, never during a pass or a job", () => {
  const done: Partial<View> = {
    stage: "done",
    messages: [message("u1", "user", "go"), message("a1", "app", "1 copy is ready.")],
    todo: [{ path: "/b", name: "B.docx" }],
  };
  const items = transcript(state(done));
  assert.deepEqual(items.map((i) => i.kind), ["message", "message", "todo"]);
  const card = items[2];
  if (card.kind === "todo") assert.deepEqual(card.names, ["B.docx"]);
  assert.equal(todoFor(state({ ...done, focus: ["/b"] })), null);             // the pass is under way
  assert.equal(todoFor(state({ ...done, busy: true })), null);
  assert.equal(todoFor(state({ ...done, stage: "preview" })), null);
  assert.equal(todoFor(state({ ...done, stage: "question" })), null);
  assert.ok(todoFor(state({ ...done, stage: "resume" })));
  assert.equal(todoFor(state({ ...done, todo: [] })), null);
  assert.equal(WORK_TODO, "Work on the documents I set aside");               // the server's exact words
});

test("the live bubble takes the key of the message it becomes, and a sent message keeps its key", () => {
  let s = reduce(state({ messages: [message("m0", "assistant", "Hello.")] }), { type: "job_started", userText: "Bold the title" });
  s = reduce(s, { type: "event", event: { type: "text_delta", text: "I'll bold" } });
  const live = transcript(s);
  assert.deepEqual(live.map((i) => [i.kind, i.key]), [["message", "entry-0"], ["message", "entry-1"], ["live", "entry-2"]]);
  // The reply finishes in the same entry; the next live bubble waits under the next key.
  s = reduce(s, { type: "event", event: { type: "message", message: message("m2", "assistant", "I'll bold the title.") } });
  assert.deepEqual(transcript(s).map((i) => [i.kind, i.key]), [["message", "entry-0"], ["message", "entry-1"], ["message", "entry-2"], ["live", "entry-3"]]);
  // The server's copy of the optimistic user bubble (a new id) is the same entry.
  const done = reduce(s, {
    type: "event",
    event: {
      type: "done",
      state: { ...EMPTY_VIEW, messages: [message("m0", "assistant", "Hello."), message("m1", "user", "Bold the title"), message("m2", "assistant", "I'll bold the title.")] },
    },
  });
  const items = transcript(done);
  assert.deepEqual(items.map((i) => i.key), ["entry-0", "entry-1", "entry-2"]);
  assert.equal(items[1].kind === "message" && items[1].message.id, "m1");
});

test("the folded line names a reply's making", () => {
  const activity = { kind: "activity" as const, text: "Reading" };
  const said = { kind: "said" as const, text: "I'll read it." };
  assert.equal(stepsLabel([activity, said, activity], "hmm"), "2 steps · thinking");
  assert.equal(stepsLabel([activity], ""), "1 step");
  assert.equal(stepsLabel([], "hmm"), "Thinking");
  assert.equal(stepsLabel([said], ""), "Working notes");
  assert.equal(stepsLabel([], ""), "");
});

test("a working line whose job ended without a message in its place folds away where it was", () => {
  const user = message("m0", "user", "Bold the title");
  let s = reduce(state({ messages: [user] }), { type: "job_started", userText: null });
  s = reduce(s, { type: "event", event: { type: "status", kind: "previewing", text: "" } });
  const during = transcript(s);
  const end = (messages: Message[]) =>
    transcript(reduce(s, { type: "event", event: { type: "done", state: { ...EMPTY_VIEW, ...question, messages } } }));
  // The job ends with its question and no message under the bubble's key: it stays after the messages, before the card.
  const idle = end([user]);
  const ended = endedLive(during, idle);
  assert.equal(ended?.key, "entry-1");
  assert.equal(ended?.live.status?.kind, "previewing");
  assert.deepEqual(withLeaving(idle, ended).map((i) => [i.kind, i.key, i.kind === "live" && i.leaving]), [
    ["message", "entry-0", false],
    ["live", "entry-1", true],
    ["prompts", "prompts", false],
  ]);
  // A message under its key: the bubble carries on as that message, nothing folds.
  assert.equal(endedLive(during, end([user, message("m1", "assistant", "Stopped.")])), null);
  // A key taken again later (the next job's first message) drops the folding bubble.
  const next = end([user, message("m1", "user", "Go on")]);
  assert.equal(withLeaving(next, ended), next);
  // An empty bubble (after a finished reply), or one already folding: nothing to fold.
  const replied = transcript(reduce(s, { type: "event", event: { type: "message", message: message("m1", "assistant", "ok") } }));
  assert.equal(endedLive(replied, end([user, message("m1", "assistant", "ok")])), null);
  assert.equal(endedLive(withLeaving(idle, ended), idle), null);
});
