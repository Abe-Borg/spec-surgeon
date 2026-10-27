/**
 * The one button (lib/action.ts): its label from the server's action, the
 * blocking variant, Stop now while busy, and the hold while a draft waits.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { View } from "../src/types.ts";
import { APPROVE_TIP, BLOCKING_TIP, DRAFT_TIP, PDF_TIP, STOP_TIP, actionButton, actionSentence, canDiscard, defaultLabel, heldTip } from "../src/lib/action.ts";
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

test("Discard shows only while a Safe-mode preview waits and no work runs", () => {
  assert.equal(canDiscard(view({ mode: "safe", stage: "preview" }), false), true);
  assert.equal(canDiscard(view({ mode: "safe", stage: "preview" }), true), false);
  assert.equal(canDiscard(view({ mode: "safe", stage: "done" }), false), false);
  assert.equal(canDiscard(view({ mode: "default", stage: "preview" }), false), false);
});

test("a Save clean PDFs preview says PDFs on its button, its tip and the sentence beside it", () => {
  const pdf = view({ action: { label: "Save clean PDFs", token: "t", kind: "approve", can_hold: true, export: "pdf" } });
  const button = actionButton(pdf, false, false);
  assert.equal(button?.label, "Save clean PDFs");
  assert.equal(button?.tip, PDF_TIP);
  assert.ok(button && button.kind === "approve" && button.pdf);
  if (button) assert.equal(actionSentence(button, 0), "If the pages look right, save the clean PDFs.");
  const held = actionButton(pdf, false, false, 1);
  assert.equal(held?.label, "Save clean PDFs (1 set aside)");
  assert.equal(held?.tip, heldTip(1, true));
  assert.match(heldTip(1, true), /^Saves a clean PDF of every document except the one marked with the red X/);
  if (held) assert.match(actionSentence(held, 1), /the rest get their PDFs now\.$/);
  // an ordinary preview is unchanged
  const copies = actionButton(view({ action: { label: "Create edited copies", token: "t", kind: "approve" } }), false, false);
  assert.equal(copies?.tip, APPROVE_TIP);
  assert.ok(copies && copies.kind === "approve" && !copies.pdf);
});
