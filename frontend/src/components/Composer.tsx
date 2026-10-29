// Adapted from Build-a-Spec's frontend/src/components/Composer.tsx (copy-based reuse).
import { memo, useEffect, useRef, type ReactNode } from "react";
import { TOUR_HOLD } from "../lib/tour";
import Tip from "./Tip";

interface Props {
  /** A job is running: the spot shows Stop; typing ahead is allowed. */
  busy: boolean;
  /** Sending is possible at all (a key is connected, a document is attached). */
  canSend: boolean;
  /** Why not, for the disabled button's hover. */
  reason: string;
  onSend: (text: string) => void;
  onStop: () => void;
  /** External prefill (a starter card): sets the text and focuses. The
   *  nonce fires the effect even when the same text is requested twice. */
  prefill?: { text: string; nonce: number };
  /** Owned by the window, so switching to Safe mode does not discard an unsent message. */
  value: string;
  onChange: (text: string) => void;
  /** The guided tour runs: Send is held still (its conversation is a recording). */
  tourActive?: boolean;
  /** What sits above the message box, inside its band: the saved recipes (RecipesStrip). */
  above?: ReactNode;
}

const MAX_HEIGHT = 220; // about nine lines

function Composer({
  busy,
  canSend,
  reason,
  onSend,
  onStop,
  prefill,
  value,
  onChange,
  tourActive = false,
  above,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow up to ~9 lines; measured again when the pane changes width,
  // since a wrapped placeholder or line reflows with it.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const grow = () => {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`;
    };
    grow();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(grow);
    if (el.parentElement) observer.observe(el.parentElement);
    return () => observer.disconnect();
  }, [value, busy, canSend]);

  // The window stores a starter card's text; its nonce asks to focus it. Remounting after a mode switch must not
  // replace a draft the user has since edited with an older starter's words.
  useEffect(() => {
    if (!prefill || prefill.nonce === 0) return;
    const el = ref.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => {
        const end = el.value.length;
        el.setSelectionRange(end, end);
      });
    }
  }, [prefill?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const send = () => {
    const text = value.trim();
    if (!text || busy || !canSend || tourActive) return;
    onChange("");
    onSend(text);
  };

  const sendTip = tourActive ? TOUR_HOLD : !canSend ? reason : value.trim() ? "Send · Enter" : "Type a request first";

  return (
    <div className="border-t border-edge bg-surface p-4" data-tour="composer">
      <div className="mx-auto max-w-3xl">
        {above}
        <div className="flex items-end gap-2 rounded-2xl border border-edge bg-bg p-2 focus-within:border-accent/70">
          <textarea
            ref={ref}
            rows={1}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              // Enter sends, and so does Ctrl+Enter; Shift+Enter is a new line.
              if (e.key !== "Enter" || e.shiftKey) return;
              e.preventDefault();
              send();
            }}
            aria-label="Describe a change or answer a question"
            data-capability="chat.composer"
            placeholder={
              busy
                ? "Type your next message; send it when the current work ends…"
                : "What would you like to do with these documents?"
            }
            className="max-h-[220px] min-h-[2.4rem] w-full resize-none bg-transparent px-2 py-1.5 text-[0.925rem] leading-relaxed outline-none placeholder:text-ink-faint"
          />
          {busy ? (
            <Tip tip="Stop now — cuts off the AI step that is running and starts no other. Your progress is saved.">
              <button
                type="button"
                onClick={onStop}
                aria-label="Stop now"
                data-capability="chat.stop"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent transition-colors hover:bg-accent-hover"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
                  <rect x="5" y="5" width="14" height="14" rx="2.5" />
                </svg>
              </button>
            </Tip>
          ) : (
            <Tip tip={sendTip}>
              <button
                type="button"
                onClick={send}
                disabled={!canSend || !value.trim() || tourActive}
                aria-label="Send"
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-on-accent transition-colors hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-30"
              >
                <svg
                  width="16"
                  height="16"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.4"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M12 19V5" />
                  <path d="m5 12 7-7 7 7" />
                </svg>
              </button>
            </Tip>
          )}
        </div>
        <p className="mt-2 flex items-baseline justify-between gap-3 px-1 text-[11px] text-ink-faint">
          {tourActive ? (
            <span>{TOUR_HOLD}</span>
          ) : canSend ? (
            <>
              <span>Edited copies are saved separately. Your originals stay intact.</span>
              <span className="shrink-0 text-ink-faint/70" title="Shift+Enter starts a new line">
                Ctrl+Enter sends
              </span>
            </>
          ) : (
            <span>{reason}</span>
          )}
        </p>
      </div>
    </div>
  );
}

// Memoized: a streamed delta changes only the live bubble, so the composer keeps its last render.
export default memo(Composer);
