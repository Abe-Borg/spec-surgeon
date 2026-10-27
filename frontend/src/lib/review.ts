/**
 * The review of the previews: a green checkmark says a document's pages look
 * right, a red X says it needs more work. Marks belong to one set of previews
 * (the samples and the action they lead to); a new plan or preview starts the
 * review over. At "Create edited copies" the documents marked with the X are
 * sent as `hold`: they go on the to-do list and the others get their copies.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { Sample, View } from "../types.ts";

export type Mark = "good" | "hold";
/** Marks by the sample's `source`. */
export type Reviews = Record<string, Mark>;

/** The marks set aside while the guided tour holds the view, with the key of the previews they belong to. */
export interface KeptReviews {
  key: string;
  reviews: Reviews;
}

/** The marks to put back once the tour has returned the view: the kept ones when the view's key is the one they
 *  belong to and there is anything to put back; otherwise null, and the review starts over as for any new preview. */
export function keptAcrossTour(kept: KeptReviews | null, key: string): Reviews | null {
  if (!kept || kept.key !== key || Object.keys(kept.reviews).length === 0) return null;
  return kept.reviews;
}

/** The previews a set of marks belongs to: the action token and the samples' sources. */
export function reviewKey(view: Pick<View, "samples" | "action">): string {
  return [view.action?.token ?? "", ...view.samples.map((s) => s.source)].join("\n");
}

/** Set a mark; the same mark a second time clears it. */
export function setMark(reviews: Reviews, source: string, mark: Mark): Reviews {
  const next = { ...reviews };
  if (next[source] === mark) delete next[source];
  else next[source] = mark;
  return next;
}

/** The sources marked with the X, in preview order, among the previews shown. */
export function heldSources(reviews: Reviews, samples: Pick<Sample, "source">[]): string[] {
  return samples.map((s) => s.source).filter((source) => reviews[source] === "hold");
}

export interface ReviewCounts {
  good: number;
  hold: number;
  unmarked: number;
}

export function reviewCounts(reviews: Reviews, samples: Pick<Sample, "source">[]): ReviewCounts {
  const counts = { good: 0, hold: 0, unmarked: 0 };
  for (const sample of samples) {
    const mark = reviews[sample.source];
    if (mark === "good") counts.good += 1;
    else if (mark === "hold") counts.hold += 1;
    else counts.unmarked += 1;
  }
  return counts;
}

/** The previews can be marked: the plan awaits approval (not a recovery's save) and nothing runs. */
export function canReview(view: Pick<View, "action">, busy: boolean): boolean {
  return !busy && view.action?.kind === "approve" && view.action.can_hold === true;
}
