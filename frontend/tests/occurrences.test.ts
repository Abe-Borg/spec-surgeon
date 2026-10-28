/**
 * Find and replace, occurrence by occurrence (lib/occurrences.ts): the ticks start as the previewed plan has them,
 * a tap unticks and a second tap puts back, the unticked keys are what Update the preview sends, the one button
 * waits while the ticks differ from the previewed plan's, and the list can be worked only while a plan with
 * occurrences awaits approval.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { OccurrenceDoc, View } from "../src/types.ts";
import {
  canUntick,
  occurrenceCounts,
  occurrencesKey,
  placeLabel,
  previewedUnticks,
  toggleUntick,
  untickedKeys,
  unticksPending,
} from "../src/lib/occurrences.ts";
import { actionButton, UNTICKS_TIP } from "../src/lib/action.ts";
import { EMPTY_VIEW } from "../src/lib/view.ts";

const item = (key: string, skipped = false) => ({
  key, part: "word/document.xml", paragraph: 11, start: 4, end: 9, where: "the body", heading: "2.01",
  before: "H. See the", match: "Owner", after: "portal.", find: "Owner", replace: "Client", skipped, op_index: 0,
});
const docs: OccurrenceDoc[] = [
  { source: "a", name: "a.docx", status: "planned", reason: "", items: [item("a1"), item("a2", true)] },
  { source: "b", name: "b.docx", status: "refused", reason: "runs into a hyperlink", items: [item("b1")] },
];
const view = (overrides: Partial<View> = {}): View => ({
  ...EMPTY_VIEW,
  stage: "preview",
  occurrences: docs,
  action: { label: "Create edited copies", token: "t1", kind: "approve", can_hold: true },
  ...overrides,
});

test("the ticks start as the previewed plan has them, and the key names the token and every occurrence", () => {
  assert.deepEqual(previewedUnticks(view()), { a2: true });
  assert.equal(occurrencesKey(view()), "t1\na1\na2\nb1");
  assert.notEqual(occurrencesKey(view()), occurrencesKey(view({ action: { ...view().action, token: "t2" } })));
  assert.deepEqual(previewedUnticks(EMPTY_VIEW), {});
});

test("a tap unticks, a second tap puts back, and the unticked keys follow the list's order", () => {
  let unticks = toggleUntick({}, "b1");
  assert.deepEqual(unticks, { b1: true });
  unticks = toggleUntick(unticks, "a1");
  assert.deepEqual(untickedKeys(unticks, docs), ["a1", "b1"]);
  unticks = toggleUntick(unticks, "b1");
  assert.deepEqual(untickedKeys(unticks, docs), ["a1"]);
  assert.deepEqual(untickedKeys({ gone: true }, docs), []);
});

test("the ticks are pending while they differ from the previewed plan's, in either direction", () => {
  const v = view();
  assert.equal(unticksPending(previewedUnticks(v), v), false);
  assert.equal(unticksPending({}, v), true);                                   // a2 put back
  assert.equal(unticksPending({ a2: true, b1: true }, v), true);               // b1 unticked
  assert.equal(unticksPending({ a2: true }, EMPTY_VIEW), false);
});

test("the one button waits while the ticks are pending, and says why", () => {
  const button = actionButton(view(), false, false, 0, true);
  assert.ok(button && button.kind === "approve" && button.disabled && button.tip === UNTICKS_TIP);
  const ready = actionButton(view(), false, false, 0, false);
  assert.ok(ready && ready.kind === "approve" && !ready.disabled);
  const resume = actionButton(view({ action: { label: "Continue", token: "t", kind: "continue" } }), false, false, 0, true);
  assert.ok(resume && resume.kind === "continue" && !resume.disabled);       // only an approval is held back
});

test("the list can be worked only while a plan with occurrences awaits approval and nothing runs", () => {
  assert.equal(canUntick(view(), false), true);
  assert.equal(canUntick(view(), true), false);
  assert.equal(canUntick(view({ occurrences: [] }), false), false);
  assert.equal(canUntick(view({ action: { label: "", token: "", kind: "none" } }), false), false);
});

test("the counts and the place label read for the user", () => {
  assert.deepEqual(occurrenceCounts({ a2: true }, docs), { documents: 2, total: 3, unticked: 1, refused: 1 });
  assert.equal(placeLabel(item("x")), "the body, paragraph 12 · 2.01");
  assert.equal(placeLabel({ where: "a header", paragraph: 0, heading: "" }), "a header, paragraph 1");
});
