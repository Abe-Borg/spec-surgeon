import type { ActionButton, DiscardButton } from "../lib/action";
import { INCLUDE_ALL_TIP, SAFE_STOP_SENTENCE, actionSentence } from "../lib/action";
import Collapse from "./Collapse";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  button: ActionButton | null;
  /** Safe mode's kill switch while a job runs (`lib/action.SAFE_STOP_TIP`): it has no composer to hold Stop now. */
  stop?: { tip: string } | null;
  onStop?: () => void;
  /** How many previews carry the red X. */
  held: number;
  /** Include all: takes every red X back, so every document gets its copy. Shown while a preview carries one. */
  onIncludeAll?: () => void;
  /** How many previews the server set aside: they did not pass while others did; no copy of them is made. */
  aside?: number;
  onAct: (token: string) => void;
  /** Discard (``lib/action.discardButton``): withdraws the preview, or a conversation's open request; nothing created. */
  discard?: DiscardButton | null;
  onDiscard?: () => void;
}

/**
 * The one button, in the conversation pane above the composer: Create edited
 * copies (with how many), Continue or Save ready copies, with one sentence on
 * what it does. While a preview carries the red X, Include all follows the
 * sentence and takes every X back.
 * While a preview waits, Discard preview sits beside it (alone when the
 * preview has nothing to create); while a conversation's request is open with
 * nothing to approve, Drop this request does. While a job runs the composer shows Stop
 * now and this bar is empty — in Safe mode, which has no composer, this bar holds Stop
 * now instead; the pause card owns Continue while work is paused.
 * The bar slides open and shut, so the conversation above it eases instead of
 * jumping.
 */
export default function ActionBar({ button, stop, onStop, held, onIncludeAll, aside = 0, onAct, discard, onDiscard }: Props) {
  const primary = button && button.kind !== "stop" ? button : null;
  const includeAll = primary?.kind === "approve" && held > 0 && onIncludeAll ? onIncludeAll : null;
  const dropping = discard && onDiscard ? discard : null;
  const stopping = stop && onStop ? stop : null;
  const shown = !!primary || !!dropping || !!stopping;
  return (
    <Collapse open={shown}>
      {stopping && (
        <div className="border-t border-edge bg-surface/70 px-4 py-2.5" role="region" aria-label="Work running">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
            <p className="text-xs leading-snug text-ink-dim">{SAFE_STOP_SENTENCE}</p>
            <Tip tip={stopping.tip}>
              <button
                type="button"
                onClick={onStop}
                aria-label="Stop now"
                data-capability="action.stop"
                className="shrink-0 rounded-lg border border-err/60 bg-err/10 px-3 py-1.5 text-xs font-medium whitespace-nowrap text-err transition-colors hover:bg-err/20"
              >
                Stop now
              </button>
            </Tip>
          </div>
        </div>
      )}
      {shown && !stopping && (
        <div className="border-t border-edge bg-surface/70 px-4 py-2.5" role="region" aria-label="Your decision" data-tour="action-bar">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
            <p className="text-xs leading-snug text-ink-dim">
              {primary ? actionSentence(primary, held, primary.kind === "approve" ? aside : 0) : dropping?.sentence}
              {includeAll && (
                <>
                  {" "}
                  <button
                    type="button"
                    className="font-medium text-accent underline underline-offset-2 hover:text-accent-hover"
                    title={INCLUDE_ALL_TIP}
                    onClick={includeAll}
                    data-capability="action.include-all"
                  >
                    Include all
                  </button>
                </>
              )}
            </p>
            <div className="flex shrink-0 items-center gap-2">
              {dropping && (
                <Tip tip={dropping.tip}>
                  <button type="button" className={quietBtn} onClick={onDiscard} data-capability="action.discard">
                    {dropping.label}
                  </button>
                </Tip>
              )}
              {primary && (
                <Tip tip={primary.tip}>
                  <button
                    type="button"
                    className={primaryBtn}
                    disabled={primary.disabled}
                    onClick={() => onAct(primary.token)}
                    data-capability="action.primary"
                  >
                    {primary.label}
                  </button>
                </Tip>
              )}
            </div>
          </div>
        </div>
      )}
    </Collapse>
  );
}
