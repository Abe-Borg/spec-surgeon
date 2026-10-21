import type { ActionButton } from "../lib/action";
import { actionSentence } from "../lib/action";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  button: ActionButton | null;
  /** How many previews carry the red X. */
  held: number;
  onAct: (token: string) => void;
}

/**
 * The one button, in the conversation pane above the composer: Create edited
 * copies, Continue or Save ready copies, with one sentence on what it does.
 * While a job runs the composer shows Stop now and this bar is empty; the
 * pause card owns Continue while work is paused.
 */
export default function ActionBar({ button, held, onAct }: Props) {
  if (!button || button.kind === "stop") return null;
  return (
    <div className="border-t border-edge bg-surface/70 px-4 py-2.5" role="region" aria-label="Your decision">
      <div className="mx-auto flex max-w-3xl items-center justify-between gap-4">
        <p className="text-xs leading-snug text-ink-dim">{actionSentence(button, held)}</p>
        <div className="shrink-0">
          <Tip tip={button.tip}>
            <button type="button" className={primaryBtn} disabled={button.disabled} onClick={() => onAct(button.token)}>
              {button.label}
            </button>
          </Tip>
        </div>
      </div>
    </div>
  );
}
