/**
 * The one button (docs/WEB_API.md, `action.kind`; docs/CONVERSATIONAL_DESKTOP.md,
 * "One action"). Every path ends in the same spot: while a job runs it is
 * "Stop now"; otherwise the server's action, with its label, or nothing.
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
 */
export function actionButton(view: View, busy: boolean, draft: boolean): ActionButton | null {
  if (busy) return { kind: "stop", label: "Stop now", tip: STOP_TIP };
  const action = view.action;
  if (!action || action.kind === "none" || !action.kind) return null;
  const kind = action.kind;
  if (kind !== "approve" && kind !== "continue" && kind !== "save") return null;
  const label = (action.label ?? "").trim() || defaultLabel(kind, view.blocking);
  if (draft) return { kind, label, token: action.token, disabled: true, tip: DRAFT_TIP };
  const tip =
    kind === "approve"
      ? view.blocking
        ? BLOCKING_TIP
        : APPROVE_TIP
      : kind === "continue"
        ? CONTINUE_TIP
        : SAVE_TIP;
  return { kind, label, token: action.token, disabled: !action.token, tip };
}
