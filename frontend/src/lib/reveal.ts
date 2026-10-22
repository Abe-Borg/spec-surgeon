/**
 * How a reply's words come onto the page.
 *
 * Words arrive in network bursts, and a question's or a plan's words can
 * arrive all at once in the message that finishes the reply. The page shows
 * them at a reading pace instead: each animation frame reveals part of the
 * backlog (`revealStep`), every revealed piece fades in, and a finished
 * reply carries on from the words already shown (`alignShown`) instead of
 * replacing them. A reply is rendered as blocks (`splitBlocks`), so a settled
 * paragraph is parsed once and only the growing one renders each frame.
 *
 * Pure, so `node --test` can pin every rule.
 */

/** How long a revealed piece takes to fade in (ms); `.reveal` in index.css runs the same length. */
export const FADE_MS = 520;
/** The backlog drains with this time constant (s): a burst of 800 characters is shown in about a second. */
export const REVEAL_TAU = 0.3;
/** The slowest pace (characters a second), so the last words of a burst never trickle. */
export const REVEAL_MIN_RATE = 150;

/** How many characters to reveal in a frame `dt` seconds long, with `backlog` characters still to show. A
 *  fraction: the caller carries what is left over into the next frame, so the pace is the same at any refresh
 *  rate. */
export function revealStep(backlog: number, dt: number): number {
  if (backlog <= 0) return 0;
  const seconds = Math.max(dt, 0);
  const eased = backlog * (1 - Math.exp(-seconds / REVEAL_TAU));
  return Math.min(backlog, Math.max(eased, REVEAL_MIN_RATE * seconds));
}

/** `end`, moved past a surrogate pair it would split. */
export function safeCut(text: string, end: number): number {
  if (end <= 0 || end >= text.length) return Math.max(0, Math.min(end, text.length));
  const code = text.charCodeAt(end - 1);
  return code >= 0xd800 && code < 0xdc00 ? end + 1 : end;
}

const isSpace = (ch: string) => ch === " " || ch === "\n" || ch === "\t" || ch === "\r" || ch === "\f" || ch === "\v";

/**
 * Where the words already shown end in `target`, matched character for
 * character whatever the spacing between them (the finished reply joins its
 * parts with its own spacing): `at` is the offset in `target` just past the
 * last matched character, and `whole` says whether every shown word was found.
 */
export function alignShown(shown: string, target: string): { at: number; whole: boolean } {
  if (target.startsWith(shown)) return { at: shown.length, whole: true };
  let j = 0;
  let at = 0;
  for (let i = 0; i < shown.length; i += 1) {
    const ch = shown[i];
    if (isSpace(ch)) continue;
    while (j < target.length && isSpace(target[j])) j += 1;
    if (j >= target.length || target[j] !== ch) return { at, whole: false };
    j += 1;
    at = j;
  }
  return { at, whole: true };
}

/** One block of a reply's markdown: the slice of the text from `start`. */
export interface Block {
  start: number;
  text: string;
}

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
/** An indented line after a blank line carries on the block above (a list item's paragraph, indented code). */
const CONTINUES = /^(?: {2,}|\t)/;
/** A link reference or footnote definition (`[doc]: https://…`, `[^1]: …`): what refers to it may be in any block. */
const DEFINITION = /^ {0,3}\[[^\]\n]+\]:/m;

/**
 * The reply's markdown cut at its blank lines into blocks that render the
 * same apart as together: a fenced code block stays whole, and an indented
 * line after a blank line stays with the block above it. A reply that
 * defines a link reference or a footnote stays one block, since what refers
 * to the definition can be anywhere in it. Each block keeps its offset,
 * which names it while the text grows.
 */
export function splitBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  let start = -1;
  let end = 0;
  let gap = false;
  let fence = "";
  let pos = 0;
  for (;;) {
    const nl = text.indexOf("\n", pos);
    const lineEnd = nl === -1 ? text.length : nl;
    const line = text.slice(pos, lineEnd);
    if (fence) {
      end = lineEnd;
      const close = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line);
      if (close && close[1][0] === fence[0] && close[1].length >= fence.length) fence = "";
    } else if (line.trim() === "") {
      if (start >= 0) gap = true;
    } else {
      if (start >= 0 && gap && !CONTINUES.test(line)) {
        blocks.push({ start, text: text.slice(start, end) });
        start = -1;
      }
      if (start < 0) start = pos;
      gap = false;
      end = lineEnd;
      const open = FENCE_OPEN.exec(line);
      if (open) fence = open[1];
    }
    if (nl === -1) break;
    pos = nl + 1;
  }
  if (start >= 0) blocks.push({ start, text: text.slice(start, end) });
  if (blocks.length > 1 && DEFINITION.test(text)) {
    const last = blocks[blocks.length - 1];
    return [{ start: blocks[0].start, text: text.slice(blocks[0].start, last.start + last.text.length) }];
  }
  return blocks;
}

/** The marks inside `[start, end)`, ascending, made relative to `start`. */
export function marksIn(marks: readonly number[], start: number, end: number): number[] {
  let lo = 0;
  let hi = marks.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (marks[mid] < start) lo = mid + 1;
    else hi = mid;
  }
  const out: number[] = [];
  for (let i = lo; i < marks.length && marks[i] < end; i += 1) out.push(marks[i] - start);
  return out;
}

/**
 * The offset from which revealed pieces are still fading in at `now`: the
 * mark of the oldest piece revealed less than `FADE_MS` ago, or `length`
 * when every piece has settled.
 */
export function freshFrom(marks: readonly number[], times: readonly number[], now: number, length: number): number {
  let i = times.length;
  while (i > 0 && now - times[i - 1] < FADE_MS) i -= 1;
  return i < marks.length ? marks[i] : length;
}

/** Two mark lists that name the same pieces (marks only ever grow, so the ends decide). */
export function sameMarks(a: readonly number[], b: readonly number[]): boolean {
  return a === b || (a.length === b.length && (a.length === 0 || (a[0] === b[0] && a[a.length - 1] === b[b.length - 1])));
}
