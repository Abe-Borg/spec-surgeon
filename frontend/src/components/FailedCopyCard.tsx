import { USE_FAILED_COPY } from "../lib/chat";
import { TOUR_HOLD } from "../lib/tour";
import type { FailedCopy } from "../types";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  items: FailedCopy[];
  busy: boolean;
  /** Sends the reply that takes the copy, as a chip: the user's decision, never their wording. */
  onSend: (text: string, chip?: boolean) => void;
  /** The guided tour runs: nothing is sent (its conversation is a recording). */
  tourActive?: boolean;
}

/**
 * Obliterate's free rung, in the chat: during a pass over a document whose copy a run or a preview made and held back
 * because it failed a check, one button takes that copy as it is, marked UNVERIFIED (the same as typing “Use the copy
 * that failed, marked UNVERIFIED”). The app checks that the copy and the file it was made from are unchanged first;
 * no AI is used.
 */
export default function FailedCopyCard({ items, busy, onSend, tourActive = false }: Props) {
  const several = items.length > 1;
  const held = busy || tourActive;
  return (
    <div
      className="chat-in rounded-2xl border border-err/40 bg-err/5 px-4 py-3"
      role="status"
      aria-label="The copy that failed is kept"
    >
      <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">
        {several ? "The copies that failed are kept" : "The copy that failed is kept"}
      </p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item.path} className="text-sm text-ink">
            <span className="block">{item.name}</span>
            <span className="block text-xs leading-snug text-ink-dim">Failed {item.failed.join("; ")}.</span>
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">
        {several ? "Each copy was" : "The copy was"} made and held back because it failed a check. You can take{" "}
        {several ? "them as they are" : "it as it is"}, marked UNVERIFIED: no AI is used, no check vouches for{" "}
        {several ? "them" : "it"}, and Undo the last change takes {several ? "them" : "it"} off again. Or tell me what
        to do instead.
      </p>
      <div className="mt-3">
        <Tip tip={tourActive ? TOUR_HOLD : busy ? "Finish or stop the current work first" : `Sends “${USE_FAILED_COPY}”`}>
          <button
            type="button"
            className={primaryBtn}
            disabled={held}
            onClick={() => onSend(USE_FAILED_COPY, true)}
            data-capability="todo.failed-copy"
          >
            {several ? "Use the copies that failed" : "Use the copy that failed"}
          </button>
        </Tip>
      </div>
    </div>
  );
}
