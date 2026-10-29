/**
 * The one button (docs/WEB_API.md, `action.kind`; docs/CONVERSATIONAL_DESKTOP.md,
 * "One action"). It lives in the conversation pane, above the composer. Every
 * path ends in the same spot: while a job runs the composer shows "Stop now";
 * otherwise the server's action, with its label, or nothing. A document the
 * user marked with the red X on its preview is counted in the label: it is set
 * aside when the copies are created.
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
    };

export const STOP_TIP =
  "Cuts off the AI step that is running and starts no other. Your progress is saved.";
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

/**
 * @param view the server's view
 * @param busy a job is running (the page's own flag; `view.busy` while attached to a stream)
 * @param draft the composer holds an unsent message
 * @param held how many previews carry the red X (counted on the approve label only)
 * @param unticksPending the find-and-replace ticks on the page differ from the previewed plan's (lib/occurrences.ts)
 */
export function actionButton(view: View, busy: boolean, draft: boolean, held = 0, unticksPending = false): ActionButton | null {
  if (busy) return { kind: "stop", label: "Stop now", tip: STOP_TIP };
  const action = view.action;
  if (!action || action.kind === "none" || !action.kind) return null;
  const kind = action.kind;
  if (kind !== "approve" && kind !== "continue" && kind !== "save") return null;
  let label = (action.label ?? "").trim() || defaultLabel(kind, view.blocking);
  const counted = kind === "approve" && held > 0;
  if (counted) label = `${label} (${held} set aside)`;
  if (draft) return { kind, label, token: action.token, disabled: true, tip: DRAFT_TIP };
  if (unticksPending && kind === "approve") return { kind, label, token: action.token, disabled: true, tip: UNTICKS_TIP };
  if (action.disabled_reason) return { kind, label, token: action.token, disabled: true, tip: action.disabled_reason };
  const tip =
    kind === "approve"
      ? view.blocking
        ? BLOCKING_TIP
        : counted
          ? heldTip(held)
          : APPROVE_TIP
      : kind === "continue"
        ? CONTINUE_TIP
        : SAVE_TIP;
  return { kind, label, token: action.token, disabled: !action.token, tip };
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

/** The sentence beside the button, in the conversation pane. `aside`: previews the server set aside (`set_aside`). */
export function actionSentence(button: ActionButton, held: number, aside = 0): string {
  if (button.kind !== "stop" && button.disabled) return button.tip;
  switch (button.kind) {
    case "approve":
      return (
        (held > 0
          ? `${held === 1 ? "One document" : `${held} documents`} marked with the red X ${held === 1 ? "goes" : "go"} on your to-do list; the rest get their copies now.`
          : "If the pages look right, create the edited copies.") + asideSentence(aside)
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
