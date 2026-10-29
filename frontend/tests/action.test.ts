/**
 * The one button (lib/action.ts): its label from the server's action, the
 * copies Create makes counted on it, the blocking variant, Stop now while
 * busy, and the hold while a draft waits.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { View } from "../src/types.ts";
import {
  APPROVE_TIP,
  BLOCKING_TIP,
  DISCARD_ONLY,
  DISCARD_PLAN_ONLY,
  DISCARD_PLAN_TIP,
  DISCARD_TIP,
  DRAFT_TIP,
  DROP_ONLY,
  DROP_PAUSED,
  DROP_TIP,
  NONE_TIP,
  STOP_TIP,
  actionButton,
  actionSentence,
  asideSentence,
  countedLabel,
  defaultLabel,
  discardButton,
  heldTip,
  unpreviewedSentence,
} from "../src/lib/action.ts";
import { EMPTY_VIEW } from "../src/lib/view.ts";

function view(overrides: Partial<View> = {}): View {
  return { ...EMPTY_VIEW, ...overrides };
}

test("while busy the spot shows Stop now, whatever the action", () => {
  const button = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), true, false);
  assert.deepEqual(button, { kind: "stop", label: "Stop now", tip: STOP_TIP });
});

test("no action, no button", () => {
  assert.equal(actionButton(view(), false, false), null);
  assert.equal(actionButton(view({ action: { label: "x", token: "t", kind: "none" } }), false, false), null);
});

test("the server's label is shown as given", () => {
  const button = actionButton(view({ action: { label: "Save 2 ready copies", token: "t", kind: "save" } }), false, false);
  assert.equal(button?.kind, "save");
  assert.equal(button?.label, "Save 2 ready copies");
  if (button && button.kind !== "stop") {
    assert.equal(button.token, "t");
    assert.equal(button.disabled, false);
  }
});

test("a blank label falls back to the kind's words, with the blocking variant for approve", () => {
  assert.equal(defaultLabel("approve", false), "Create edited copies");
  assert.equal(defaultLabel("approve", true), "I reviewed the flagged pages — create copies");
  assert.equal(defaultLabel("continue", true), "Continue");
  assert.equal(defaultLabel("save", false), "Save ready copies");
  const approve = actionButton(view({ action: { label: "", token: "t", kind: "approve" }, blocking: true }), false, false);
  assert.equal(approve?.label, "I reviewed the flagged pages — create copies");
  if (approve && approve.kind !== "stop") assert.equal(approve.tip, BLOCKING_TIP);
  const plain = actionButton(view({ action: { label: "  ", token: "t", kind: "approve" } }), false, false);
  if (plain && plain.kind !== "stop") assert.equal(plain.tip, APPROVE_TIP);
});

test("an unsent draft holds the button and says so", () => {
  const button = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), false, true);
  assert.equal(button?.kind, "approve");
  if (button && button.kind !== "stop") {
    assert.equal(button.disabled, true);
    assert.equal(button.tip, DRAFT_TIP);
    assert.equal(button.label, "Create edited copies");
  }
});

test("an action without a token cannot be clicked", () => {
  const button = actionButton(view({ action: { label: "Continue", token: "", kind: "continue" } }), false, false);
  if (button && button.kind !== "stop") assert.equal(button.disabled, true);
});

test("a server verification failure disables Create and explains why", () => {
  const reason = "Create is unavailable: Spec.docx did not pass its preview checks (V2).";
  const button = actionButton(view({ action: {
    label: "Create edited copies", token: "current", kind: "approve", disabled_reason: reason,
  } }), false, false);
  assert.ok(button && button.kind !== "stop");
  assert.equal(button.disabled, true);
  assert.equal(button.tip, reason);
  assert.equal(actionSentence(button, 0), reason);
});

test("previews the server set aside are said beside an available Create, never on its label", () => {
  // one sample of two did not pass: the server leaves it untouched and Create makes the other's copy
  const button = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true } }), false, false);
  assert.ok(button && button.kind !== "stop" && !button.disabled);
  assert.equal(button.label, "Create edited copies");
  assert.equal(
    actionSentence(button, 0, 1),
    "If the pages look right, create the edited copies. One document whose preview did not pass gets no copy: it stays as it is, under Left untouched.",
  );
  assert.ok(actionSentence(button, 1, 2).startsWith("One document marked with the red X goes on your to-do list"));
  assert.ok(actionSentence(button, 1, 2).endsWith(" 2 documents whose preview did not pass get no copy: they stay as they are, under Left untouched."));
  assert.equal(asideSentence(0), "");
  assert.ok(asideSentence(1).includes("gets no copy"));
  // a disabled button says only why
  const blocked = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", disabled_reason: "Create is unavailable: x." } }), false, false)!;
  assert.equal(actionSentence(blocked, 0, 1), "Create is unavailable: x.");
});

test("an unknown kind from a newer server shows no button rather than a wrong one", () => {
  const button = actionButton(view({ action: { label: "x", token: "t", kind: "deliver" as View["action"]["kind"] } }), false, false);
  assert.equal(button, null);
});

test("previews marked with the red X are counted on the approve label and explained in its tip", () => {
  const two = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true } }), false, false, 2);
  assert.equal(two?.label, "Create edited copies (2 set aside)");
  if (two && two.kind !== "stop") assert.equal(two.tip, heldTip(2));
  assert.ok(heldTip(1).includes("the one marked") && heldTip(3).includes("the 3 marked"));
  // the count never touches Continue or Save, nor the label while a draft holds the button
  const cont = actionButton(view({ action: { label: "Continue", token: "t", kind: "continue" } }), false, false, 2);
  assert.equal(cont?.label, "Continue");
  const draft = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), false, true, 1);
  assert.equal(draft?.label, "Create edited copies (1 set aside)");
  if (draft && draft.kind !== "stop") assert.equal(draft.tip, DRAFT_TIP);
  // the blocking variant keeps its own tip; the count still shows
  const blocking = actionButton(view({ action: { label: "", token: "t", kind: "approve" }, blocking: true }), false, false, 1);
  assert.equal(blocking?.label, "I reviewed the flagged pages — create copies (1 set aside)");
  if (blocking && blocking.kind !== "stop") assert.equal(blocking.tip, BLOCKING_TIP);
});

test("the sentence beside the button says what it does, and counts the set-aside documents", () => {
  const approve = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), false, false)!;
  assert.equal(actionSentence(approve, 0), "If the pages look right, create the edited copies.");
  assert.ok(actionSentence(approve, 1).startsWith("One document marked with the red X goes on your to-do list"));
  assert.ok(actionSentence(approve, 3).startsWith("3 documents marked with the red X go on your to-do list"));
  const cont = actionButton(view({ action: { label: "Continue", token: "t", kind: "continue" } }), false, false)!;
  assert.ok(actionSentence(cont, 0).includes("checked against your request again"));
  assert.equal(actionSentence({ kind: "stop", label: "Stop now", tip: STOP_TIP }, 0), "");
});

test("Discard shows what the server says it would withdraw, in every mode, never while work runs", () => {
  const safe = discardButton(view({ mode: "safe", stage: "preview", discard: "preview" }), false);
  assert.deepEqual(safe, { label: "Discard preview", tip: DISCARD_TIP, sentence: DISCARD_ONLY });
  assert.equal(discardButton(view({ mode: "safe", stage: "preview", discard: "preview" }), true), null);
  assert.equal(discardButton(view({ mode: "safe", stage: "done", discard: "" }), false), null);
  for (const mode of ["default", "obliterate"] as const) {
    assert.deepEqual(discardButton(view({ mode, stage: "preview", discard: "preview" }), false), {
      label: "Discard preview",
      tip: DISCARD_PLAN_TIP,
      sentence: DISCARD_PLAN_ONLY,
    });
    assert.deepEqual(discardButton(view({ mode, stage: "question", discard: "request" }), false), {
      label: "Drop this request",
      tip: DROP_TIP,
      sentence: DROP_ONLY,
    });
    assert.equal(discardButton(view({ mode, stage: "budget", discard: "request" }), false)?.sentence, DROP_PAUSED);
    assert.equal(discardButton(view({ mode, stage: "done", discard: "" }), false), null);
  }
});

test("an approval always creates edited copies: PDFs are Export's, never the one button's", () => {
  const copies = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), false, false);
  assert.equal(copies?.tip, APPROVE_TIP);
  if (copies) assert.equal(actionSentence(copies, 0), "If the pages look right, create the edited copies.");
  const held = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true } }), false, false, 1);
  assert.equal(held?.tip, heldTip(1));
  assert.match(heldTip(1), /^Creates an edited copy of every document except the one marked with the red X/);
  if (held) assert.match(actionSentence(held, 1), /the rest get their copies now\.$/);
});

test("Create counts the copies it makes, less the previews marked with the red X", () => {
  const counted = (held: number, copies = 30, unpreviewed = 22, blocking = false) =>
    actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true, copies, unpreviewed }, blocking }), false, false, held);
  const all = counted(0)!;
  assert.ok(all.kind === "approve" && !all.disabled);
  assert.equal(all.label, "Create 30 edited copies");
  assert.equal(all.tip, APPROVE_TIP);
  // the documents without a preview of their own are named in the sentence, never left to guess
  assert.equal(
    actionSentence(all, 0),
    "If the pages look right, create the edited copies. 22 more documents, not previewed, get the same plan.",
  );
  const three = counted(3)!;
  assert.equal(three.label, "Create 27 edited copies (3 set aside)");
  assert.equal(three.tip, heldTip(3));
  assert.equal(
    actionSentence(three, 3),
    "3 documents marked with the red X go on your to-do list; the rest get their copies now. 22 more documents, not previewed, get the same plan.",
  );
  assert.equal(counted(0, 1, 0)?.label, "Create 1 edited copy");
  assert.equal(countedLabel(2, false), "Create 2 edited copies");
  // the blocking variant keeps its own words and tip, with the count
  const blocking = counted(1, 8, 0, true)!;
  assert.equal(blocking.label, "I reviewed the flagged pages — create 7 copies (1 set aside)");
  assert.equal(blocking.tip, BLOCKING_TIP);
  assert.equal(countedLabel(1, true), "I reviewed the flagged pages — create 1 copy");
  assert.equal(unpreviewedSentence(0), "");
  assert.equal(unpreviewedSentence(1), " One more document, not previewed, gets the same plan.");
});

test("every document Create would run carrying the red X says that no copy is made", () => {
  const button = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true, copies: 2, unpreviewed: 0 } }), false, false, 2)!;
  assert.ok(button.kind === "approve");
  assert.equal(button.label, "Create no copies (2 set aside)");
  assert.equal(button.tip, NONE_TIP);
  assert.equal(actionSentence(button, 2), "Every preview carries the red X: no copy is created, and they go on your to-do list.");
  const one = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", can_hold: true, copies: 1 } }), false, false, 1)!;
  assert.equal(actionSentence(one, 1), "Its preview carries the red X: no copy is created, and it goes on your to-do list.");
});

test("without a count from the server the label stays as it was: recovery, an older server, Continue and Save", () => {
  // recovery's approve sends no count (0), an older server sends none at all, and a malformed one is ignored
  for (const copies of [0, undefined, -1, 2.5, "3" as unknown as number]) {
    const button = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", copies } }), false, false, 1);
    assert.equal(button?.label, "Create edited copies (1 set aside)");
  }
  const save = actionButton(view({ action: { label: "Save 2 ready copies", token: "t", kind: "save", copies: 5, unpreviewed: 3 } }), false, false)!;
  assert.equal(save.label, "Save 2 ready copies");
  assert.ok(save.kind === "save" && save.unpreviewed === undefined);
  // a disabled Create still counts, and says only why it is disabled
  const draft = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve", copies: 4, unpreviewed: 1 } }), false, true, 1)!;
  assert.equal(draft.label, "Create 3 edited copies (1 set aside)");
  assert.equal(actionSentence(draft, 1), DRAFT_TIP);
});
