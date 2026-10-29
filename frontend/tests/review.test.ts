/**
 * The review of the previews (lib/review.ts): every preview starts with its
 * checkmark (it gets its copy), the X sets one aside and the checkmark or a
 * second X takes it back, the marks belong to one set of previews, the X'd
 * sources are what `hold` sends, and marks can be given only while the plan
 * awaits approval and Create is available.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { View } from "../src/types.ts";
import { canReview, heldSources, keptAcrossTour, markOf, reviewKey, setAsideCount, setMark } from "../src/lib/review.ts";
import { EMPTY_VIEW } from "../src/lib/view.ts";

const sample = (source: string) => ({ source, name: source, images: [], pages_before: 1, pages_after: 1, passed: true });

function view(overrides: Partial<View> = {}): View {
  return { ...EMPTY_VIEW, ...overrides };
}

test("every preview starts with the checkmark; the X sets it aside, and the checkmark or a second X takes it back", () => {
  // nothing needs ticking: a preview with no X gets its copy
  assert.equal(markOf({}, "a"), "good");
  let reviews = setMark({}, "a", "good");
  assert.deepEqual(reviews, {});
  reviews = setMark(reviews, "a", "hold");
  assert.deepEqual(reviews, { a: "hold" });
  assert.equal(markOf(reviews, "a"), "hold");
  assert.equal(markOf(reviews, "b"), "good");
  // the checkmark takes the X back
  assert.deepEqual(setMark(reviews, "a", "good"), {});
  // and so does the X a second time
  reviews = setMark(reviews, "b", "hold");
  reviews = setMark(reviews, "a", "hold");
  assert.deepEqual(reviews, { b: "hold" });
});

test("the held sources are the X'd previews, in preview order, among the previews shown", () => {
  const reviews = { a: "hold" as const, c: "hold" as const, gone: "hold" as const };
  assert.deepEqual(heldSources(reviews, [sample("c"), sample("a"), sample("b")]), ["c", "a"]);
  assert.deepEqual(heldSources({}, [sample("a")]), []);
});

test("a preview the server set aside is never sent as held, and is counted apart", () => {
  // it did not pass while another did: no copy of it is made either way, so a mark left on it means nothing
  const reviews = { a: "hold" as const, b: "hold" as const };
  const samples = [{ ...sample("a"), passed: false, set_aside: true }, sample("b"), sample("c")];
  assert.deepEqual(heldSources(reviews, samples), ["b"]);
  assert.equal(setAsideCount(samples), 1);
  assert.equal(setAsideCount([sample("a")]), 0);
});

test("the review key follows the action token and the samples: a new plan or preview starts over", () => {
  const first = view({ samples: [sample("a")], action: { label: "", token: "t1", kind: "approve" } });
  assert.equal(reviewKey(first), reviewKey({ ...first }));
  assert.notEqual(reviewKey(first), reviewKey({ ...first, action: { ...first.action, token: "t2" } }));
  assert.notEqual(reviewKey(first), reviewKey({ ...first, samples: [sample("a"), sample("b")] }));
});

test("marks can be given only while an approvable plan says so, never while a job runs or Create is unavailable", () => {
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve", can_hold: true } }), false), true);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve", can_hold: true } }), true), false);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "approve" } }), false), false);
  assert.equal(canReview(view({ action: { label: "", token: "t", kind: "save", can_hold: true } }), false), false);
  // a card says it gets its copy only when Create can make one
  const blocked = { label: "", token: "t", kind: "approve" as const, can_hold: true, disabled_reason: "Create is unavailable: x." };
  assert.equal(canReview(view({ action: blocked }), false), false);
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
