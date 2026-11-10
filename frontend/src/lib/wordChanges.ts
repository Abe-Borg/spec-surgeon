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
  /** What else the line should say: the whole paragraph is new or gone, only the spacing changed, or the words sit
   *  in the copy's tracked changes. */
  note: string;
}

export const NO_WORDS_CHANGE = "No words change: the checker read the same words in this copy as in the original.";
export const WORDS_CHANGE_TITLE = "Words the method changes, as the independent checker read them:";
/** The same two sentences for a plan with a raw XML edit (`samples[].raw_xml`): its words are listed, not predicted. */
export const RAW_NO_WORDS_CHANGE =
  "The raw XML edit changes no words: in the parts it edits, the checker read the same text in this copy as in the original — the words of every paragraph and any other text between tags. Values it sets in attributes are in the edit's own sentence.";
export const RAW_WORDS_CHANGE_TITLE = "Words the raw XML edit changes, as the independent checker read them:";
/** And for a part replaced with a file of the user's (`samples[].raw_what` "part", Obliterate's hands-on tools). */
export const PART_NO_WORDS_CHANGE =
  "The replaced part changes no words: the checker read the same text in this copy as in the original — the words of every paragraph and any other text between tags.";
export const PART_WORDS_CHANGE_TITLE = "Words the replaced part changes, as the independent checker read them:";

/** The two sentences a sample's word list is shown under: a custom method's, a raw XML edit's, or a replaced part's. */
export function wordsHeadings(sample: Pick<Sample, "raw_xml" | "raw_what">): { title: string; none: string } {
  if (!sample.raw_xml) return { title: WORDS_CHANGE_TITLE, none: NO_WORDS_CHANGE };
  return sample.raw_what === "part"
    ? { title: PART_WORDS_CHANGE_TITLE, none: PART_NO_WORDS_CHANGE }
    : { title: RAW_WORDS_CHANGE_TITLE, none: RAW_NO_WORDS_CHANGE };
}

const quote = (words: string[]): string => words.map((w) => `\u201c${w}\u201d`).join(", ");

/** One entry as a line of the card. */
export function wordChangeLine(change: TextChange): WordChangeLine {
  const removed = quote(change.removed ?? []);
  const added = quote(change.added ?? []);
  const notes: string[] = [];
  if (change.other) {
    if (change.unread) notes.push("its text could not be read in the copy");
    if (change.more) notes.push(`${change.more} more not listed`);
  } else if (change.block === "new") notes.push(added ? "a new paragraph" : "a new paragraph without words");
  else if (change.block === "gone") notes.push(removed ? "the whole paragraph" : "an empty paragraph removed");
  else if (!removed && !added) notes.push("only the spacing changes, no words");
  if (change.tracked) notes.push("in the copy's tracked changes");
  return { where: change.where || change.part, removed, added, note: notes.join(", ") };
}

/** The card's lines for a sample: null when the sample carries no comparison (an ordinary plan's sample, or an
 *  older server), [] when the method changes no words. */
export function wordChangeLines(sample: Pick<Sample, "text_changes">): WordChangeLine[] | null {
  const changes = sample.text_changes;
  if (!Array.isArray(changes)) return null;
  return changes.map(wordChangeLine);
}
