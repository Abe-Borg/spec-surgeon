/**
 * The one button (docs/WEB_API.md, `action.kind`; docs/CONVERSATIONAL_DESKTOP.md,
 * "One action"). It lives in the conversation pane, above the composer. Every
 * path ends in the same spot: while a job runs the composer shows "Stop now"
 * (in Safe mode, which has no composer, the action bar does: SAFE_STOP_TIP);
 * otherwise the server's action, with its label, or nothing. Create counts the
 * copies it makes (`action.copies`: the documents without a preview of their
 * own too); a document the user marked with the red X on its preview is taken
 * off that count and named in the label: it is set aside when the copies are
 * created.
 *
 * Pure, so `node --test` can pin every label.
 */
import type { View } from "../types.ts";

export type ActionButton =
  | { kind: "stop"; label: string; tip: string }
  | {
      kind: "approve" | "continue" | "save";
      label: string;
      token: string;
      /** Clicking does nothing while true; `tip` says why. */
      disabled: boolean;
      tip: string;
      /** Approve: the documents Create runs without a preview of their own (`action.unpreviewed`). */
      unpreviewed?: number;
      /** Approve: every document Create would run carries the red X, so no copy is made. */
      none?: boolean;
    };

export const STOP_TIP =
  "Cuts off the AI step that is running and starts no other. Your progress is saved.";
/** Safe mode calls no AI: its Stop ends the work itself (the server's `stopping`). */
export const SAFE_STOP_TIP =
  "Ends the work that is running at once: the preview being made, the copies being created and checked, or an export. Nothing it made is kept; your documents and copies stay as they were.";
/** The sentence beside Safe mode's Stop now, in the action bar while a job runs. */
export const SAFE_STOP_SENTENCE = "Working on it. Stop now ends it at once and keeps nothing it made.";
export const DRAFT_TIP = "Send your new message before approving a plan.";
export const APPROVE_TIP =
  "Creates an edited copy of every document from the plan and pages shown here. Your originals stay intact.";
export const BLOCKING_TIP =
  "A visual finding is marked as blocking: this confirms you looked at the flagged pages shown here.";
export const CONTINUE_TIP =
  "Checks the documents against your request again before anything can be approved.";
export const SAVE_TIP = "Saves the copies that are already verified while a question is still open.";
export const UNTICKS_TIP =
  "Update the preview first: the plan and pages shown here still change the find-and-replace occurrences you unticked.";

/** Include all, after the approve sentence while a preview carries the red X. */
export const INCLUDE_ALL_TIP = "Takes back every red X: every document gets its copy.";

/** The approve tip when every document Create would run carries the red X. */
export const NONE_TIP =
  "Every document this plan would edit carries the red X: no copy is created, and they go on your to-do list. Your originals stay intact.";

/** The approve tip when `held` previews carry the red X. */
export function heldTip(held: number): string {
  return (
    `Creates an edited copy of every document except the ${held === 1 ? "one" : held} marked with the red X; ` +
    `${held === 1 ? "it goes" : "they go"} on your to-do list for after. Your originals stay intact.`
  );
}

const DEFAULT_LABELS: Record<"approve" | "continue" | "save", string> = {
  approve: "Create edited copies",
  continue: "Continue",
  save: "Save ready copies",
};

/** The default label of an action kind, taking the blocking variant into account. */
export function defaultLabel(kind: "approve" | "continue" | "save", blocking: boolean): string {
  if (kind === "approve" && blocking) return "I reviewed the flagged pages — create copies";
  return DEFAULT_LABELS[kind];
}

/** The approve label with the number of copies Create makes: "Create 30 edited copies", the blocking variant too. */
export function countedLabel(copies: number, blocking: boolean): string {
  const what = copies === 0 ? "no copies" : `${copies} ${blocking ? "" : "edited "}${copies === 1 ? "copy" : "copies"}`;
  return blocking ? `I reviewed the flagged pages — create ${what}` : `Create ${what}`;
}

/** The copies the server counts for Create, or null when it counts none (recovery, an older server). */
function serverCopies(action: View["action"]): number | null {
  return typeof action.copies === "number" && Number.isInteger(action.copies) && action.copies > 0 ? action.copies : null;
}

/** A count the server sends, or 0. */
function count(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : 0;
}

/**
 * @param view the server's view
 * @param busy a job is running (the page's own flag; `view.busy` while attached to a stream)
 * @param draft the composer holds an unsent message
 * @param held how many previews carry the red X (taken off the approve label's count, and named on it)
 * @param unticksPending the find-and-replace ticks on the page differ from the previewed plan's (lib/occurrences.ts)
 */
export function actionButton(view: View, busy: boolean, draft: boolean, held = 0, unticksPending = false): ActionButton | null {
  if (busy) return { kind: "stop", label: "Stop now", tip: view.mode === "safe" ? SAFE_STOP_TIP : STOP_TIP };
  const action = view.action;
  if (!action || action.kind === "none" || !action.kind) return null;
  const kind = action.kind;
  if (kind !== "approve" && kind !== "continue" && kind !== "save") return null;
  let label = (action.label ?? "").trim() || defaultLabel(kind, view.blocking);
  const counted = kind === "approve" && held > 0;
  const copies = kind === "approve" ? serverCopies(action) : null;
  const left = copies === null ? null : Math.max(0, copies - held);
  if (left !== null) label = countedLabel(left, view.blocking);
  if (counted) label = `${label} (${held} set aside)`;
  const counts = kind === "approve" ? { unpreviewed: count(action.unpreviewed), none: left === 0 && counted } : {};
  if (draft) return { kind, label, token: action.token, disabled: true, tip: DRAFT_TIP, ...counts };
  if (unticksPending && kind === "approve")
    return { kind, label, token: action.token, disabled: true, tip: UNTICKS_TIP, ...counts };
  if (action.disabled_reason)
    return { kind, label, token: action.token, disabled: true, tip: action.disabled_reason, ...counts };
  const tip =
    kind === "approve"
      ? view.blocking
        ? BLOCKING_TIP
        : counted
          ? left === 0
            ? NONE_TIP
            : heldTip(held)
          : APPROVE_TIP
      : kind === "continue"
        ? CONTINUE_TIP
        : SAVE_TIP;
  return { kind, label, token: action.token, disabled: !action.token, tip, ...counts };
}

/** What the approve sentence adds when `aside` previews did not pass while others did: they stay as they are. */
export function asideSentence(aside: number): string {
  if (aside <= 0) return "";
  const one = aside === 1;
  return (
    ` ${one ? "One document" : `${aside} documents`} whose preview did not pass ${one ? "gets" : "get"} no ` +
    `copy: ${one ? "it stays as it is" : "they stay as they are"}, under Left untouched.`
  );
}

/** What the approve sentence adds when Create also runs `unpreviewed` documents that show no preview of their own. */
export function unpreviewedSentence(unpreviewed: number): string {
  if (unpreviewed <= 0) return "";
  return unpreviewed === 1
    ? " One more document, not previewed, gets the same plan."
    : ` ${unpreviewed} more documents, not previewed, get the same plan.`;
}

/** The sentence beside the button, in the conversation pane. `aside`: previews the server set aside (`set_aside`). */
export function actionSentence(button: ActionButton, held: number, aside = 0): string {
  if (button.kind !== "stop" && button.disabled) return button.tip;
  switch (button.kind) {
    case "approve":
      return (
        (button.none
          ? `${held === 1 ? "Its preview carries" : "Every preview carries"} the red X: no copy is created, and ${held === 1 ? "it goes" : "they go"} on your to-do list.`
          : held > 0
            ? `${held === 1 ? "One document" : `${held} documents`} marked with the red X ${held === 1 ? "goes" : "go"} on your to-do list; the rest get their copies now.`
            : "If the pages look right, create the edited copies.") +
        unpreviewedSentence(button.unpreviewed ?? 0) +
        asideSentence(aside)
      );
    case "continue":
      return "The documents are checked against your request again before anything can be approved.";
    case "save":
      return "The copies that are already verified can be saved while a question is still open.";
    default:
      return "";
  }
}

export const DISCARD_TIP = "Withdraws this preview. Nothing is created, and the form keeps what you typed.";
/** The sentence when a button's preview has nothing to create: every document was left untouched. */
export const DISCARD_ONLY = "No document can take this change as it is. Discard the preview to choose another button.";
/** Default and Obliterate: Discard beside a conversation's preview. */
export const DISCARD_PLAN_TIP =
  "Withdraws this plan and its preview. Nothing is created; your next message starts a new request.";
export const DISCARD_PLAN_ONLY = "Discard the preview to ask for something else.";
/** Default and Obliterate: a request still open with nothing to approve (a question, a pause, work to continue). */
export const DROP_TIP =
  "Drops the request that is open: Claude’s question, or the work waiting to continue. Nothing is created; your next message starts a new request.";
export const DROP_ONLY = "Answer in the chat, or drop this request to ask for something else.";
export const DROP_PAUSED = "Continue above, or drop this request to ask for something else.";

export interface DiscardButton {
  label: string;
  tip: string;
  /** The sentence beside it when there is no other button. */
  sentence: string;
}

/**
 * Discard, beside the one button (docs/WEB_API.md, `discard` and `POST /api/discard`): the server says what it would
 * withdraw now — a preview in every mode, or in Default and Obliterate a request still open with nothing to approve —
 * and nothing shows while work runs.
 */
export function discardButton(view: Pick<View, "mode" | "stage" | "discard">, busy: boolean): DiscardButton | null {
  if (busy) return null;
  if (view.discard === "preview") {
    return view.mode === "safe"
      ? { label: "Discard preview", tip: DISCARD_TIP, sentence: DISCARD_ONLY }
      : { label: "Discard preview", tip: DISCARD_PLAN_TIP, sentence: DISCARD_PLAN_ONLY };
  }
  if (view.discard === "request") {
    return { label: "Drop this request", tip: DROP_TIP, sentence: view.stage === "budget" ? DROP_PAUSED : DROP_ONLY };
  }
  return null;
}
