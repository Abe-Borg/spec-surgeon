/**
 * The words a custom method's sample copy adds and removes, as the preview card shows them (lib/wordChanges.ts).
 * The entries come from the server's `samples[].text_changes` — the independent checker's own comparison of the
 * copy with its original (docs/WEB_API.md), never the method's or the model's account — and the page only words them.
 */
import type { Sample, TextChange } from "../types";

export interface WordChangeLine {
  /** The place in plain words: "footer", "body paragraph 12". */
  where: string;
  /** The words removed, each stretch quoted: “FIRE”, “WET”; "" when none. */
  removed: string;
  /** The words added, each stretch quoted; "" when none. */
  added: string;
  /** What else the line should say: the whole paragraph is new or gone, or only the spacing changed. */
  note: string;
}

export const NO_WORDS_CHANGE = "No words change: the checker read the same words in this copy as in the original.";
export const WORDS_CHANGE_TITLE = "Words the method changes, as the independent checker read them:";

const quote = (words: string[]): string => words.map((w) => `\u201c${w}\u201d`).join(", ");

/** One entry as a line of the card. */
export function wordChangeLine(change: TextChange): WordChangeLine {
  const removed = quote(change.removed ?? []);
  const added = quote(change.added ?? []);
  let note = "";
  if (change.block === "new") note = added ? "a new paragraph" : "a new paragraph without words";
  else if (change.block === "gone") note = removed ? "the whole paragraph" : "an empty paragraph removed";
  else if (!removed && !added) note = "only the spacing changes, no words";
  return { where: change.where || change.part, removed, added, note };
}

/** The card's lines for a sample: null when the sample carries no comparison (an ordinary plan's sample, or an
 *  older server), [] when the method changes no words. */
export function wordChangeLines(sample: Pick<Sample, "text_changes">): WordChangeLine[] | null {
  const changes = sample.text_changes;
  if (!Array.isArray(changes)) return null;
  return changes.map(wordChangeLine);
}
