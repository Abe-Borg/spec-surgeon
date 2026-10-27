import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";
import type { PauseReason } from "../types";

interface Props {
  reason: Exclude<PauseReason, "">;
  /** The conversation's estimated spending, this request's, and the spending check ("" when it is off). */
  costText: string;
  requestText: string;
  limitText: string;
  canContinue: boolean;
  busy: boolean;
  onContinue: () => void;
}

const TITLES: Record<Props["reason"], string> = {
  limit: "Paused at your spending check",
  unpriced: "Paused: the spending check cannot be kept",
  interrupted: "Work paused; your progress is saved",
};

/**
 * Work paused with its progress saved (docs/CONVERSATIONAL_DESKTOP.md, "Connection and resource use"): the request
 * reached the spending check the user set in Settings, the price table cannot estimate the model that answered, or the
 * app closed while work was running. Nothing runs until Continue; Continue is the action here, so the workbench's spot
 * stays empty while this card is up.
 */
export default function PauseCard({ reason, costText, requestText, limitText, canContinue, busy, onContinue }: Props) {
  const label = reason === "limit" && limitText ? `Continue (up to ${limitText} more)` : "Continue";
  const tip = !canContinue
    ? "Nothing to continue right now."
    : reason === "limit" && limitText
      ? `Allows this request up to ${limitText} more and picks it up where it stopped.`
      : reason === "interrupted"
        ? "Picks the conversation up where it stopped."
        : "Goes on without a spending check for this request.";
  return (
    <div className="chat-in rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3" role="status">
      <p className="font-[family-name:var(--font-display)] text-base font-semibold text-ink">{TITLES[reason]}</p>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-dim">
        {reason === "limit" &&
          (limitText
            ? `This request has spent ${requestText}. The next AI step could take it past your ${limitText} spending ` +
              `check, so it was not started; your progress is saved. Continue allows up to ${limitText} more for ` +
              "this request. To end it here, do nothing: no AI step runs until you continue."
            : `This request has spent ${requestText} and reached the spending check, which is now off in Settings. ` +
              "Continue goes on without a check for this request.")}
        {reason === "unpriced" &&
          "The price table cannot estimate the model that answered, so this request’s spending cannot be checked. " +
            "Continue goes on without a spending check for this request; Stop stays available while it runs."}
        {reason === "interrupted" &&
          "The app closed while work was running, so the last AI step’s spending may not have been recorded. " +
            "Continue picks up where it stopped."}{" "}
        Estimated spending in this conversation: {costText}; provider charges can differ.
      </p>
      <div className="mt-3">
        <Tip tip={tip}>
          <button type="button" className={primaryBtn} disabled={!canContinue || busy} onClick={onContinue}>
            {label}
          </button>
        </Tip>
      </div>
    </div>
  );
}
