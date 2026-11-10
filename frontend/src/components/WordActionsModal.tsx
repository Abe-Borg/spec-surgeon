import { useState } from "react";
import type { Attachment, WordMenu } from "../types";
import {
  WORD_FIRST,
  WORD_RUN,
  WORD_SAYS,
  WORD_TITLE,
  everyLabel,
  runHeld,
  toggleAction,
  wordHeld,
  wordPatience,
} from "../lib/wordActions";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  /** The document the button was pressed on. */
  document: Attachment;
  /** The menu the server sent with the view (Obliterate mode only): the actions, and whether Word is there. */
  menu: WordMenu;
  /** How many other documents are in scope: the scope box offers them when there are any. */
  others: number;
  busy: boolean;
  onClose: () => void;
  /** The actions in the order they will run, and whether the other documents get them too. */
  onRun: (actions: string[], every: boolean) => void;
}

/**
 * Obliterate's menu of named Word actions for one document (lib/wordActions.ts): a fixed list, each with what it
 * does, ticked in the order they will run. Nothing here is typed or composed — the menu is all Word can be asked.
 * Run starts a job with no AI call: Word makes the copy now, and the preview that follows lists what it changed.
 */
export default function WordActionsModal({ document, menu, others, busy, onClose, onRun }: Props) {
  const [chosen, setChosen] = useState<string[]>([]);
  const [every, setEvery] = useState(false);
  const held = runHeld(menu, chosen, busy);
  return (
    <ModalShell title={`${WORD_TITLE} — ${document.name}`} onClose={onClose} wide>
      <div className="flex flex-col gap-3 text-sm" data-tour="word-actions">
        <ul className="list-disc space-y-0.5 pl-4 text-xs leading-relaxed text-ink-dim">
          {WORD_SAYS.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        {!menu.available && (
          <p className="rounded-lg border border-err/40 bg-err/5 px-3 py-2 text-xs text-err" role="alert">
            {menu.why}
          </p>
        )}
        <ul className="flex flex-col gap-1.5">
          {menu.actions.map((action) => {
            const at = chosen.indexOf(action.id);
            const why = wordHeld(chosen, action.id);
            return (
              <li key={action.id}>
                <label
                  className={`flex items-start gap-2 rounded-lg border border-edge bg-raised px-3 py-2 ${
                    why || !menu.available ? "cursor-not-allowed opacity-60" : "cursor-pointer"
                  }`}
                >
                  <input
                    type="checkbox"
                    className="mt-0.5 h-3.5 w-3.5 flex-none accent-[var(--color-err)]"
                    checked={at >= 0}
                    disabled={!!why || !menu.available || busy}
                    onChange={() => setChosen((now) => toggleAction(now, action.id))}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-xs font-semibold text-ink">
                      {action.label}
                      {at >= 0 && (
                        <span className="rounded-full bg-err/10 px-1.5 text-[11px] font-normal text-err" aria-label={`Runs number ${at + 1}`}>
                          {at + 1}
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-xs leading-relaxed text-ink-dim">{action.does}</span>
                    {action.id === "repair" && at >= 0 && chosen.length > 1 && (
                      <span className="mt-0.5 block text-[11px] text-ink-faint">{WORD_FIRST}</span>
                    )}
                    {why && <span className="mt-0.5 block text-[11px] text-ink-faint">{why}</span>}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
        {others > 0 && (
          <label className="flex cursor-pointer items-start gap-2 text-xs text-ink">
            <input
              type="checkbox"
              className="mt-0.5 h-3.5 w-3.5 flex-none accent-[var(--color-err)]"
              checked={every}
              disabled={busy}
              onChange={(e) => setEvery(e.target.checked)}
              data-capability="word.every"
            />
            {everyLabel(others)}
          </label>
        )}
        <p className="text-[11px] text-ink-faint">{wordPatience(menu.minutes)}</p>
        <div className="flex justify-end gap-2">
          <button type="button" className={quietBtn} onClick={onClose}>
            Cancel
          </button>
          <Tip tip={held || "Word makes the copy now; a preview of what it changed comes before anything is created"}>
            <button
              type="button"
              className={primaryBtn}
              disabled={!!held}
              onClick={() => onRun(chosen, every && others > 0)}
              data-capability="word.run"
            >
              {WORD_RUN}
            </button>
          </Tip>
        </div>
      </div>
    </ModalShell>
  );
}
