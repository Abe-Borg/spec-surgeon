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
  composerState,
  costPill,
  liveHasContent,
  pauseFor,
  promptsFor,
  spentText,
  statusLabel,
  transcript,
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
  assert.equal(liveHasContent({ ...emptyLive(), activities: ["x"] }), true);
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
