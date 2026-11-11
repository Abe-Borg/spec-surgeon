import { WORK_TODO } from "../lib/chat";
import type { OwedItem } from "../lib/standing";
import { TOUR_HOLD } from "../lib/tour";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

/** How many lines of the to-do list the card shows before it counts the rest (the grid lists every one). */
const SHOWN = 6;

interface Props {
  items: OwedItem[];
  busy: boolean;
  /** Why the pass waits ("" when it does not): a run's saved copies wait on its recovery (the view's
   *  `recovery_blocked`). */
  blocked?: string;
  /** Sends the reply that begins the pass, as a chip: the user's decision, never their wording. */
  onSend: (text: string, chip?: boolean) => void;
  /** Safe mode: there is no conversation to plan them again, so the button turns to Default mode and begins the
   *  pass there. */
  onWorkInDefault?: () => void;
  /** The guided tour runs: the pass is not begun and the mode not switched (the tour does both as it needs). */
  tourActive?: boolean;
}

/**
 * The to-do list, in the chat: what could not be done on a document is still owed — the crosses of Where each
 * document stands, whoever could not do it (a button, a plan, a check, or your own red X or word). Each line is a
 * document, the change it still owes and why; one button begins the pass over the oldest of them (the same as
 * typing “Work on my to-do list”). A document the engine could not change stays among the documents the next
 * button or request works on; only one you set aside yourself waits out of them.
 */
export default function TodoCard({ items, busy, blocked = "", onSend, onWorkInDefault, tourActive = false }: Props) {
  const several = items.length > 1;
  const held = busy || tourActive;
  const more = items.length - SHOWN;
  return (
    <div
      className="chat-in rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3"
      role="status"
      aria-label="Your to-do list"
      data-tour="todo-card"
    >
      <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">On your to-do list</p>
      <ul className="mt-1 space-y-1">
        {items.slice(0, SHOWN).map((item) => (
          <li key={`${item.operation}\t${item.path}`} className="text-sm text-ink">
            <span className="block break-words">
              {item.name}
              {item.request && <span className="text-ink-dim"> · {item.request}</span>}
            </span>
            {item.reason && <span className="block text-xs leading-snug text-ink-dim">{item.reason}</span>}
          </li>
        ))}
      </ul>
      {more > 0 && <p className="mt-1 text-xs text-ink-dim">and {more} more: Where each document stands lists every one.</p>}
      {onWorkInDefault ? (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          Safe mode has no conversation to plan {several ? "these" : "this"} again: Default mode does, with an AI
          model, and you say what should be different. The documents stay among those your next button works on.
        </p>
      ) : (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          {several ? "These stay" : "This stays"} owed until you work on {several ? "them" : "it"} or mark{" "}
          {several ? "one" : "it"} as not needed, beside the documents. The pass takes one change at a time, the
          oldest first; tell me what should be different. The other copies stay as they are.
        </p>
      )}
      <div className="mt-3">
        {onWorkInDefault ? (
          <Tip tip={tourActive ? TOUR_HOLD : busy ? "Finish the current work first" : blocked || "Switches to Default mode and begins the pass over the oldest of them"}>
            <button type="button" className={primaryBtn} disabled={held || !!blocked} onClick={onWorkInDefault} data-capability="todo.card">
              {several ? "Work on them in Default mode" : "Work on it in Default mode"}
            </button>
          </Tip>
        ) : (
          <Tip tip={tourActive ? TOUR_HOLD : busy ? "Finish or stop the current work first" : blocked || `Sends “${WORK_TODO}”`}>
            <button
              type="button"
              className={primaryBtn}
              disabled={held || !!blocked}
              onClick={() => onSend(WORK_TODO, true)}
              data-capability="todo.card"
            >
              {several ? "Work on them now" : "Work on it now"}
            </button>
          </Tip>
        )}
      </div>
    </div>
  );
}
