// Adapted from Build-a-Spec's frontend/src/components/ModalShell.tsx (copy-based reuse).
import { useRef, type ReactNode } from "react";
import { useDialogFocus } from "../lib/dialogFocus";

/**
 * The app's standard modal shell: a dimmed backdrop (click closes), a title
 * row with ✕, and the body. Keyboard behavior comes from `useDialogFocus`
 * (Escape, Tab containment, focus restoration). Initial focus lands on the
 * panel itself, not a button, so Enter does nothing a stray keypress would
 * regret.
 */
export function ModalShell({
  title,
  onClose,
  children,
  wide,
  xwide,
  marker,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  /** Wider still (the zoomed preview). Wins over `wide`. */
  xwide?: boolean;
  /** `data-dialog` on the dialog: the guided tour stamps its own dialogs so its Escape guard can tell them apart. */
  marker?: string;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  // Always open: every consumer renders the shell conditionally.
  useDialogFocus(true, panelRef, panelRef, onClose);
  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-6 pt-20"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      data-dialog={marker}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className={
          "flex max-h-full w-full flex-col rounded-2xl border border-edge bg-surface shadow-2xl outline-none " +
          (xwide ? "max-w-[min(96vw,1400px)]" : wide ? "max-w-xl" : "max-w-md")
        }
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-edge px-5 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-base font-semibold">
            {title}
          </h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-0.5 text-ink-dim transition-colors hover:bg-raised hover:text-ink"
          >
            ✕
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

/** The one filled button of an area. */
export const primaryBtn =
  "rounded-lg bg-accent px-3.5 py-1.5 text-sm font-medium text-on-accent transition-colors " +
  "hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-40";
/** Every other button: outlined, accent on hover. */
export const quietBtn =
  "rounded-lg border border-edge bg-raised px-3.5 py-1.5 text-sm text-ink " +
  "transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";
/** A quiet button on the paper surface. */
export const paperBtn =
  "rounded-lg border border-paper-edge bg-white/60 px-3 py-1 text-xs text-paper-ink " +
  "transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";
