import type { ActionButton } from "../lib/action";
import { actionSentence, DISCARD_ONLY, DISCARD_TIP } from "../lib/action";
import Collapse from "./Collapse";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  button: ActionButton | null;
  /** How many previews carry the red X. */
  held: number;
  onAct: (token: string) => void;
  /** Safe mode's Discard while a button's preview waits: withdraws it, nothing created. */
  onDiscard?: () => void;
}

/**
 * The one button, in the conversation pane above the composer: Create edited
 * copies, Continue or Save ready copies, with one sentence on what it does.
 * While a Safe-mode button's preview waits, Discard sits beside it (alone when
 * the preview has nothing to create). While a job runs the composer shows Stop
 * now and this bar is empty; the pause card owns Continue while work is paused.
 * The bar slides open and shut, so the conversation above it eases instead of
 * jumping.
 */
export default function ActionBar({ button, held, onAct, onDiscard }: Props) {
  const primary = button && button.kind !== "stop" ? button : null;
  const shown = !!primary || !!onDiscard;
  return (
    <Collapse open={shown}>
      {shown && (
        <div className="border-t border-edge bg-surface/70 px-4 py-2.5" role="region" aria-label="Your decision" data-tour="action-bar">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
            <p className="text-xs leading-snug text-ink-dim">{primary ? actionSentence(primary, held) : DISCARD_ONLY}</p>
            <div className="flex shrink-0 items-center gap-2">
              {onDiscard && (
                <Tip tip={DISCARD_TIP}>
                  <button type="button" className={quietBtn} onClick={onDiscard} data-capability="action.discard">
                    Discard preview
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
