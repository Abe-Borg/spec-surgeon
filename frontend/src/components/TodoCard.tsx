import { WORK_TODO } from "../lib/chat";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  names: string[];
  busy: boolean;
  /** Sends the reply that begins the pass, as a chip: the user's decision, never their wording. */
  onSend: (text: string, chip?: boolean) => void;
  /** Safe mode: the documents wait for Default mode, where the conversation can plan them. */
  onDefaultMode?: () => void;
}

/**
 * The to-do list, in the chat: the documents the user set aside with the red X
 * wait here while the others get their copies; one button begins the pass over
 * them (the same as typing “Work on the documents I set aside”).
 */
export default function TodoCard({ names, busy, onSend, onDefaultMode }: Props) {
  const several = names.length > 1;
  return (
    <div className="chat-in rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3" role="status" aria-label="Your to-do list">
      <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">Set aside for more work</p>
      <ul className="mt-1 space-y-0.5">
        {names.map((name) => (
          <li key={name} className="text-sm text-ink">
            {name}
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
          {several ? "These documents were" : "This document was"} set aside when the copies were created. When you
          work on {several ? "them" : "it"}, tell me what should be different; the other copies stay as they are.
        </p>
      )}
      <div className="mt-3">
        {onDefaultMode ? (
          <Tip tip={busy ? "Finish the current work first" : "Switches to Default mode, where the conversation is"}>
            <button type="button" className={primaryBtn} disabled={busy} onClick={onDefaultMode}>
              Go to Default mode
            </button>
          </Tip>
        ) : (
          <Tip tip={busy ? "Finish or stop the current work first" : `Sends “${WORK_TODO}”`}>
            <button type="button" className={primaryBtn} disabled={busy} onClick={() => onSend(WORK_TODO, true)}>
              {several ? "Work on them now" : "Work on it now"}
            </button>
          </Tip>
        )}
      </div>
    </div>
  );
}
