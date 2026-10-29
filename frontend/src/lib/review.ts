/**
 * The review of the previews. Every preview Create can make a copy of starts
 * with its green checkmark filled: it gets its copy, so nothing needs ticking
 * for Create to make every copy. The red X says a document needs more work;
 * the checkmark, or a second X, takes it back. Marks belong to one set of
 * previews (the samples and the action they lead to); a new plan or preview
 * starts the review over. At "Create edited copies" the documents marked with
 * the X are sent as `hold`: they go on the to-do list and the others get their
 * copies. A preview the server set aside (`set_aside`: it did not pass while
 * another did) carries no marks; no copy of it is made either way.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { Sample, View } from "../types.ts";

/** A preview's mark: it gets its copy (the checkmark, where every preview starts) or it is set aside (the X). */
export type Mark = "good" | "hold";
/** The previews marked with the X, by the sample's `source`; every other preview gets its copy. */
export type Reviews = Record<string, "hold">;

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

/** A preview's mark: the X when it carries one, else the checkmark it starts with. */
export function markOf(reviews: Reviews, source: string): Mark {
  return reviews[source] === "hold" ? "hold" : "good";
}

/** Set a mark: the X sets the preview aside; the checkmark, or the X a second time, takes it back. */
export function setMark(reviews: Reviews, source: string, mark: Mark): Reviews {
  const next = { ...reviews };
  if (mark === "hold" && next[source] !== "hold") next[source] = "hold";
  else delete next[source];
  return next;
}

/** The sources marked with the X, in preview order, among the previews shown (never one the server set aside). */
export function heldSources(reviews: Reviews, samples: Pick<Sample, "source" | "set_aside">[]): string[] {
  return samples.filter((s) => !s.set_aside).map((s) => s.source).filter((source) => reviews[source] === "hold");
}

/** How many previews the server set aside because they did not pass while another did: no copy of them is made. */
export function setAsideCount(samples: Pick<Sample, "set_aside">[]): number {
  return samples.filter((s) => s.set_aside === true).length;
}

/** The previews can be marked: the plan awaits approval (not a recovery's save), Create is available (a card says it
 *  gets its copy only when Create can make it) and nothing runs. */
export function canReview(view: Pick<View, "action">, busy: boolean): boolean {
  return !busy && view.action?.kind === "approve" && view.action.can_hold === true && !view.action.disabled_reason;
}
