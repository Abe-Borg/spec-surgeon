/**
 * The window's modes in words (CLAUDE.md, Modes): the switch's labels and tips, and Obliterate's warning
 * — what it grants, what it costs and what still holds — said the same way in the warning, in How to use and in the
 * trust dossier. Obliterate is offered only when Settings → Advanced shows it, and is chosen only after its warning,
 * every time; a launch never opens in it.
 */
import type { Mode } from "../types.ts";

export const MODE_LABELS: Record<Mode, string> = { safe: "Safe", default: "Default", obliterate: "Obliterate" };

/** What each mode grants and costs, in one line each (How to use says it in full). */
export const MODE_TIPS: Record<Mode, string> = {
  safe: "Safe mode: buttons for the common changes. No AI is used; every change is checked before you get a copy.",
  default:
    "Default mode: describe any change in your own words. An AI model plans it, you look at the previews and approve it; every change is checked.",
  obliterate:
    "Obliterate mode: the conversation without the guardrails that protect your documents' content. Its warning shows each time you choose it.",
};

/** What Obliterate grants: the three guardrails it lifts. */
export const OBLITERATE_GRANTS: readonly string[] = [
  "Claude may write wording itself — drafts, rewrites, new paragraphs — and place it. It no longer has to be yours, but every word it will place must be in the reply you read before you approve: the app refuses a plan whose words are not there.",
  "Claude may write a custom method that changes anything Word stores: text in any part, text boxes, shapes, structure — not only formatting.",
  "A copy whose checks fail is delivered anyway, marked UNVERIFIED with the checks it failed, instead of being held back; the mark stays on every later copy built on it.",
];

/** What Obliterate costs. */
export const OBLITERATE_COSTS: readonly string[] = [
  "No proof that the rest of the file is untouched: a failed check is a warning, not a stop.",
  "Content can be lost or invented without a refusal, and a copy may need Word's repair to open.",
  "The same request can give different results from one run to the next.",
  "More AI spending: custom methods are written and tested on your files.",
];

/** What holds in every mode, Obliterate included. */
export const OBLITERATE_KEPT: readonly string[] = [
  "Copies only: your originals are never written.",
  "A source that changed while the work ran is never delivered over.",
  "A custom method still runs in a separate process, under the same import whitelist and time and memory limits, writing only in its own folder.",
  "The independent checks still run on every copy, and their results are kept beside it.",
];

/** The box the user ticks before Obliterate turns on. */
export const OBLITERATE_ACKNOWLEDGE =
  "I understand: in Obliterate mode a copy can lose or change content without being stopped, and I check every copy myself before I use it.";

/** What choosing a mode in the switch does: Obliterate opens its warning first; the others switch at once. */
export function modeChoice(current: Mode, chosen: Mode): "none" | "warn" | "switch" {
  if (current === chosen) return "none";
  return chosen === "obliterate" ? "warn" : "switch";
}

/** The conversation's modes (the message box, the AI): Default and Obliterate. */
export function converses(mode: Mode): boolean {
  return mode !== "safe";
}

/** A copy's failed checks in one line, or "" for a verified copy. */
export function unverifiedLine(failed: readonly string[] | undefined): string {
  if (!failed || failed.length === 0) return "";
  return `UNVERIFIED — ${failed.join("; ")} failed. Check it in Word before you use it.`;
}
