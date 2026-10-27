/**
 * The review of the previews (lib/review.ts): the marks belong to one set of
 * previews, a second tap clears a mark, the X'd sources are what `hold` sends,
 * and marks can be given only while the plan awaits approval.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { View } from "../src/types.ts";
import { canReview, heldSources, keptAcrossTour, reviewCounts, reviewKey, setMark } from "../src/lib/review.ts";
import { EMPTY_VIEW } from "../src/lib/view.ts";

const sample = (source: string) => ({ source, name: source, images: [], pages_before: 1, pages_after: 1, passed: true });

function view(overrides: Partial<View> = {}): View {
  return { ...EMPTY_VIEW, ...overrides };
}

test("a mark is set, replaced by the other, and cleared by a second tap of the same", () => {
  let reviews = setMark({}, "a", "good");
  assert.deepEqual(reviews, { a: "good" });
  reviews = setMark(reviews, "a", "hold");
  assert.deepEqual(reviews, { a: "hold" });
  reviews = setMark(reviews, "b", "hold");
  reviews = setMark(reviews, "a", "hold");
  assert.deepEqual(reviews, { b: "hold" });
});

test("the held sources are the X'd previews, in preview order, among the previews shown", () => {
  const reviews = { a: "hold" as const, b: "good" as const, c: "hold" as const, gone: "hold" as const };
  assert.deepEqual(heldSources(reviews, [sample("c"), sample("a"), sample("b")]), ["c", "a"]);
  assert.deepEqual(heldSources({}, [sample("a")]), []);
  assert.deepEqual(reviewCounts(reviews, [sample("a"), sample("b"), sample("c"), sample("d")]), { good: 1, hold: 2, unmarked: 1 });
});

test("the review key follows the action token and the samples: a new plan or preview starts over", () => {
  const first = view({ samples: [sample("a")], action: { label: "", token: "t1", kind: "approve" } });
  assert.equal(reviewKey(first), reviewKey({ ...first }));
  assert.notEqual(reviewKey(first), reviewKey({ ...first, action: { ...first.action, token: "t2" } }));
  assert.notEqual(reviewKey(first), reviewKey({ ...first, samples: [sample("a"), sample("b")] }));
});

test("marks can be given only while an approvable plan says so, and never while a job runs", () => {
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve", can_hold: true } }), false), true);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve", can_hold: true } }), true), false);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve" } }), false), false);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "save", can_hold: true } }), false), false);
});

test("marks set aside for the guided tour come back with the previews they belong to, and only those", () => {
  const kept = { key: "tok-1\na.docx\nb.docx", reviews: { "a.docx": "hold" as const } };
  assert.deepEqual(keptAcrossTour(kept, "tok-1\na.docx\nb.docx"), { "a.docx": "hold" });
  // another preview (a new token, other samples) starts the review over
  assert.equal(keptAcrossTour(kept, "tok-2\na.docx\nb.docx"), null);
  assert.equal(keptAcrossTour(kept, "tok-1\na.docx"), null);
  // nothing kept, or nothing marked, puts nothing back
  assert.equal(keptAcrossTour(null, "tok-1\na.docx\nb.docx"), null);
  assert.equal(keptAcrossTour({ key: "tok-1\na.docx\nb.docx", reviews: {} }, "tok-1\na.docx\nb.docx"), null);
});
