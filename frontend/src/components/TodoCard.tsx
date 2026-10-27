import { WORK_TODO } from "../lib/chat";
import { TOUR_HOLD } from "../lib/tour";
import type { TodoItem } from "../types";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  items: TodoItem[];
  busy: boolean;
  /** Sends the reply that begins the pass, as a chip: the user's decision, never their wording. */
  onSend: (text: string, chip?: boolean) => void;
  /** Safe mode: the documents wait for Default mode, where the conversation can plan them. */
  onDefaultMode?: () => void;
  /** The guided tour runs: the pass is not begun and the mode not switched (the tour does both as it needs). */
  tourActive?: boolean;
}

/**
 * The to-do list, in the chat: the documents set aside (the red X on a preview, at your word, or because the plan
 * could not be applied to them or their copy could not be made) wait here, each with why; one button begins the
 * pass over them (the same as typing “Work on the documents I set aside”).
 */
export default function TodoCard({ items, busy, onSend, onDefaultMode, tourActive = false }: Props) {
  const several = items.length > 1;
  const held = busy || tourActive;
  return (
    <div
      className="chat-in rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3"
      role="status"
      aria-label="Your to-do list"
      data-tour="todo-card"
    >
      <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">Set aside for more work</p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item.path} className="text-sm text-ink">
            <span className="block">{item.name}</span>
            {item.reason && <span className="block text-xs leading-snug text-ink-dim">{item.reason}</span>}
          </li>
        ))}
      </ul>
      {onDefaultMode ? (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          {several ? "These documents wait" : "This document waits"} for Default mode, where you describe what should
          be different and an AI model plans it. The other copies stay as they are.
        </p>
      ) : (
        <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
          {several ? "They wait" : "It waits"} here, out of every plan, until you work on {several ? "them" : "it"} or
          put {several ? "them" : "it"} back. When you work on {several ? "them" : "it"}, tell me what should be
          different; the other copies stay as they are.
        </p>
      )}
      <div className="mt-3">
        {onDefaultMode ? (
          <Tip tip={tourActive ? TOUR_HOLD : busy ? "Finish the current work first" : "Switches to Default mode, where the conversation is"}>
            <button type="button" className={primaryBtn} disabled={held} onClick={onDefaultMode} data-capability="todo.card">
              Go to Default mode
            </button>
          </Tip>
        ) : (
          <Tip tip={tourActive ? TOUR_HOLD : busy ? "Finish or stop the current work first" : `Sends “${WORK_TODO}”`}>
            <button
              type="button"
              className={primaryBtn}
              disabled={held}
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
