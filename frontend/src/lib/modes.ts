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
  "Claude may write a custom method that changes anything Word stores: text in any part, text boxes, shapes, structure — not only formatting. The method predicts nothing and just does the work, so you approve the copies it made, not a plan; each is delivered marked UNPROVEN.",
  "A copy whose checks fail is delivered anyway, marked UNVERIFIED with the checks it failed, instead of being held back; the mark stays on every later copy built on it.",
];

/** What Obliterate costs. */
export const OBLITERATE_COSTS: readonly string[] = [
  "No proof that the rest of the file is untouched: a failed check is a warning, not a stop, and a custom method's copy is checked only for being a file Word opens.",
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
  "Before you approve a custom method, its copy of every document is made, and the chat and each copy's card list what it changes — the words added and removed, and what else differs — as the independent checker read it, never as the method describes it. Create delivers those same copies.",
];

/** The box the user ticks before Obliterate turns on. */
export const OBLITERATE_ACKNOWLEDGE =
  "I understand: in Obliterate mode a copy can lose or change content without being stopped, and I check every copy myself before I use it.";

/** What choosing a mode in the switch does: Obliterate opens its warning first; the others switch at once. */
export function modeChoice(current: Mode, chosen: Mode): "none" | "warn" | "switch" {
  if (current === chosen) return "none";
  return chosen === "obliterate" ? "warn" : "switch";
}

/** Why the switch to `chosen` waits ("" when it does not): while a run's saved copies wait on its recovery (the view's
 *  `recovery_blocked`), a switch across Obliterate voids the plan and the check with it, and Safe mode cannot go on
 *  with a check. Safe to Default stays open: that is where the check goes on (`DocumentTask.set_mode`). */
export function modeHeld(view: { mode: Mode; recovery_blocked?: string }, chosen: Mode): string {
  const blocked = view.recovery_blocked ?? "";
  if (!blocked || chosen === view.mode) return "";
  const crossing = (view.mode === "obliterate") !== (chosen === "obliterate");
  return crossing || chosen === "safe" ? blocked : "";
}

/** The conversation's modes (the message box, the AI): Default and Obliterate. */
export function converses(mode: Mode): boolean {
  return mode !== "safe";
}

/** A to-do row's line about the copy the app kept for it (the view's `failed_copy`), or "" when none is kept. */
export function failedCopyLine(failed: readonly string[] | undefined): string {
  if (!failed || failed.length === 0) return "";
  return `A copy was made and failed ${failed.join("; ")}. Obliterate mode can take it as it is, marked UNVERIFIED.`;
}

/** What the warning says above its lists when Obliterate is turned on from a to-do row, for that one document. */
export function obliterateSubject(name: string): string {
  return `For ${name}: Obliterate mode turns on and the pass begins over that one document. Every other document, copy and to-do entry stays as it is.`;
}

/** The warning's line about the API key when it opens from a to-do row: without a key, the copy the app kept can
 *  still be taken (no AI is used for that); anything else in the mode needs the key. "" with a key. */
export function obliterateKeyNote(keyReady: boolean, failedCopy: boolean): string {
  if (keyReady) return "";
  return failedCopy
    ? "No API key is connected: you can take the copy that failed as it is (no AI is used for that), but anything else in Obliterate mode needs the key."
    : "Obliterate mode uses the AI: connect your API key in Settings first.";
}

/** A copy's mark that says a custom method made it in Obliterate mode (the server's `UNPROVEN_MARK`, or that mark
 *  carried from an earlier copy this one builds on). Every other mark is a failed check. */
export function isUnprovenMark(mark: string): boolean {
  return mark.includes("UNPROVEN");
}

/** The failed checks among a copy's marks: what makes it UNVERIFIED. */
export function failedMarks(marks: readonly string[] | undefined): string[] {
  return (marks ?? []).filter((mark) => !isUnprovenMark(mark));
}

/** The one word a copy's badge says: UNVERIFIED when a check failed, UNPROVEN when a custom method made it and no
 *  check failed, "" for a copy checked against its plan. */
export function copyMark(marks: readonly string[] | undefined): "" | "UNVERIFIED" | "UNPROVEN" {
  if (!marks || marks.length === 0) return "";
  return failedMarks(marks).length > 0 ? "UNVERIFIED" : "UNPROVEN";
}

/** What UNPROVEN means, wherever the page says it. */
export const UNPROVEN_MEANS =
  "a custom method made it, and nothing was predicted to hold it to: what it changed was shown to you before you approved it, not proven";

/** A copy's marks in one line, or "" for a verified copy. */
export function unverifiedLine(marks: readonly string[] | undefined): string {
  if (!marks || marks.length === 0) return "";
  const failed = failedMarks(marks);
  if (failed.length === 0) return `UNPROVEN — ${UNPROVEN_MEANS}. Check it in Word before you use it.`;
  const shown = failed.length < marks.length ? " A custom method made it, so the rest of what it changed was shown to you, not proven." : "";
  return `UNVERIFIED — ${failed.join("; ")} failed.${shown} Check it in Word before you use it.`;
}

/** The results card's first sentences: how the copies listed were checked, and what each mark among them means. */
export function resultsLead(results: readonly { unverified?: string[] }[]): string {
  const marks = results.map((r) => copyMark(r.unverified));
  if (!marks.some(Boolean)) return "Each copy was checked against the plan, word for word. Click one to open it in Word; your originals stay intact. ";
  const said = [marks.some((m) => m === "") ? "A copy with no mark was checked against the plan." : ""];
  if (marks.includes("UNVERIFIED")) said.push("A copy marked UNVERIFIED failed a check and was delivered anyway (Obliterate mode).");
  if (marks.includes("UNPROVEN")) said.push("A copy marked UNPROVEN was made by a custom method that predicted nothing: what it changed was shown to you, and nothing proved it.");
  return `${said.filter(Boolean).join(" ")} Check a marked copy in Word before you use it. Your originals stay intact. `;
}

/** A preview card's badge: the word, its tone and its hover text. A copy a custom method made in Obliterate mode is
 *  never "Checked": nothing was predicted to check it against. */
export function sampleBadge(sample: { passed: boolean; unproven?: boolean; set_aside?: boolean; no_copy?: boolean }): {
  label: string;
  tone: "ok" | "warn" | "err";
  title: string;
} {
  if (sample.no_copy) {
    return { label: "No copy", tone: "err", title: "The custom method could not make a copy of this document, so none is created. It goes on your to-do list with the reason." };
  }
  if (!sample.passed) {
    return {
      label: "Did not pass",
      tone: "err",
      title: sample.unproven
        ? "A check of this copy's file failed. In Obliterate mode the copy is still created, marked UNVERIFIED with the checks it failed."
        : sample.set_aside
          ? "This sample did not pass every check, so no copy of it is made; the other documents can still get theirs."
          : "This sample did not pass every check; the plan needs another look.",
    };
  }
  if (sample.unproven) {
    return {
      label: "Not proven",
      tone: "warn",
      title: "A custom method made this copy. The independent reader found no fault in its file and lists what it changed; nothing proves the change is the one you asked for. It is created marked UNPROVEN.",
    };
  }
  return { label: "Checked", tone: "ok", title: "This sample was applied and verified in memory; the source was not touched." };
}

/** The heading over a custom method's other changes on its preview card. */
export const OTHER_CHANGES_TITLE = "What else this copy changes, as the independent reader found it:";
