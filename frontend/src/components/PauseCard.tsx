import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  stepsPerPause: number;
  costText: string;
  canContinue: boolean;
  busy: boolean;
  onContinue: () => void;
}

/**
 * Work paused with its progress saved (docs/CONVERSATIONAL_DESKTOP.md,
 * "Connection and resource use"): the conversation used its allowance of AI
 * steps, or the app closed while work was running. Continue is the action
 * here, so the workbench's spot stays empty while this card is up.
 */
export default function PauseCard({ stepsPerPause, costText, canContinue, busy, onContinue }: Props) {
  return (
    <div className="card-in rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3" role="status">
      <p className="font-[family-name:var(--font-display)] text-base font-semibold text-ink">
        Work paused; your progress is saved
      </p>
      <p className="mt-1.5 text-sm leading-relaxed text-ink-dim">
        The conversation used {stepsPerPause} AI steps since you last said to go on, or the app closed
        while work was running. Continue allows up to {stepsPerPause} more and picks up where it stopped.
        Estimated spending so far: {costText}; provider charges can differ.
      </p>
      <div className="mt-3">
        <Tip
          tip={
            canContinue
              ? `Allows up to ${stepsPerPause} more AI steps and picks the conversation up where it stopped.`
              : "Nothing to continue right now."
          }
        >
          <button type="button" className={primaryBtn} disabled={!canContinue || busy} onClick={onContinue}>
            Continue
          </button>
        </Tip>
      </div>
    </div>
  );
}
