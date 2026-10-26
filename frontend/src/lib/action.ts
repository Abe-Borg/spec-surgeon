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
 */
export function actionButton(view: View, busy: boolean, draft: boolean, held = 0): ActionButton | null {
  if (busy) return { kind: "stop", label: "Stop now", tip: STOP_TIP };
  const action = view.action;
  if (!action || action.kind === "none" || !action.kind) return null;
  const kind = action.kind;
  if (kind !== "approve" && kind !== "continue" && kind !== "save") return null;
  let label = (action.label ?? "").trim() || defaultLabel(kind, view.blocking);
  const counted = kind === "approve" && held > 0;
  if (counted) label = `${label} (${held} set aside)`;
  if (draft) return { kind, label, token: action.token, disabled: true, tip: DRAFT_TIP };
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

/** The sentence beside the button, in the conversation pane. */
export function actionSentence(button: ActionButton, held: number): string {
  if (button.kind !== "stop" && button.disabled) return button.tip;
  switch (button.kind) {
    case "approve":
      return held > 0
        ? `${held === 1 ? "One document" : `${held} documents`} marked with the red X ${held === 1 ? "goes" : "go"} on your to-do list; the rest get their copies now.`
        : "If the pages look right, create the edited copies.";
    case "continue":
      return "The documents are checked against your request again before anything can be approved.";
    case "save":
      return "The copies that are already verified can be saved while a question is still open.";
    default:
      return "";
  }
}
