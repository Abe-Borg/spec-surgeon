/**
 * The one button (lib/action.ts): its label from the server's action, the
 * blocking variant, Stop now while busy, and the hold while a draft waits.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { View } from "../src/types.ts";
import { APPROVE_TIP, BLOCKING_TIP, DRAFT_TIP, STOP_TIP, actionButton, defaultLabel } from "../src/lib/action.ts";
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

test("an unknown kind from a newer server shows no button rather than a wrong one", () => {
  const button = actionButton(view({ action: { label: "x", token: "t", kind: "deliver" as View["action"]["kind"] } }), false, false);
  assert.equal(button, null);
});
