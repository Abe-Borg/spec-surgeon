import type { ActionButton, DiscardButton } from "../lib/action";
import { actionSentence } from "../lib/action";
import Collapse from "./Collapse";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  button: ActionButton | null;
  /** How many previews carry the red X. */
  held: number;
  onAct: (token: string) => void;
  /** Discard (``lib/action.discardButton``): withdraws the preview, or a conversation's open request; nothing created. */
  discard?: DiscardButton | null;
  onDiscard?: () => void;
}

/**
 * The one button, in the conversation pane above the composer: Create edited
 * copies, Continue or Save ready copies, with one sentence on what it does.
 * While a preview waits, Discard preview sits beside it (alone when the
 * preview has nothing to create); while a conversation's request is open with
 * nothing to approve, Drop this request does. While a job runs the composer shows Stop
 * now and this bar is empty; the pause card owns Continue while work is paused.
 * The bar slides open and shut, so the conversation above it eases instead of
 * jumping.
 */
export default function ActionBar({ button, held, onAct, discard, onDiscard }: Props) {
  const primary = button && button.kind !== "stop" ? button : null;
  const dropping = discard && onDiscard ? discard : null;
  const shown = !!primary || !!dropping;
  return (
    <Collapse open={shown}>
      {shown && (
        <div className="border-t border-edge bg-surface/70 px-4 py-2.5" role="region" aria-label="Your decision" data-tour="action-bar">
          <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
            <p className="text-xs leading-snug text-ink-dim">{primary ? actionSentence(primary, held) : dropping?.sentence}</p>
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
