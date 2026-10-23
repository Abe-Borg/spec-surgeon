/**
 * A choice's wording edited before it is picked (docs/PLAN-2026-10-23-modes.md M4): Claude's draft is shown in a box,
 * the user may change it, and the edited text is what picking approves. The server checks it again
 * (`conversation.edited_wording`); this only keeps the box honest about what it can send.
 *
 * Pure, so `node --test` can pin every rule.
 */

/** What the server refuses, said the same way. */
export const WORDING_RULE =
  "The wording must be one line of visible characters (no line breaks, no invisible characters) and cannot be empty.";

/** Characters the user cannot see, or that break the line, as code-point ranges: control characters (a tab, which
 *  shows as a space, excepted), the soft hyphen, zero-width and directional marks, line and paragraph separators, the
 *  word joiners, Hangul fillers, variation selectors and the byte-order mark. */
const INVISIBLE: [number, number][] = [
  [0x0, 0x8], [0xa, 0x1f], [0x7f, 0x9f], [0xad, 0xad], [0x34f, 0x34f], [0x61c, 0x61c], [0x115f, 0x1160],
  [0x17b4, 0x17b5], [0x180b, 0x180f], [0x200b, 0x200f], [0x2028, 0x202e], [0x2060, 0x206f], [0x3164, 0x3164],
  [0xfe00, 0xfe0f], [0xfeff, 0xfeff], [0xffa0, 0xffa0],
];

const invisible = (ch: string) => {
  const code = ch.codePointAt(0) ?? 0;
  return INVISIBLE.some(([a, b]) => code >= a && code <= b);
};

export interface WordingCheck {
  /** Why the text cannot be sent; "" when it can. */
  error: string;
  /** The words to send as the user's edit; null when the text is the draft as offered (an ordinary pick). */
  edited: string | null;
}

/** The box's text against the draft: an error, the edited words, or null (unchanged). Outer spaces never count. */
export function checkWording(draft: string, text: string): WordingCheck {
  const trimmed = text.replace(/^ +| +$/g, "");
  if (!trimmed.trim() || [...trimmed].some(invisible)) return { error: WORDING_RULE, edited: null };
  return { error: "", edited: trimmed === draft ? null : trimmed };
}
