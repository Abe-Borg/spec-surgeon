// The top bar's Export: one button that opens its three choices (the owner's, 2026-10-29) — Word (Tracked Changes ON),
// Word (Tracked Changes OFF) and PDF (Clean). A choice asks for a folder next (the native dialog, on the server); the
// clean ones make and check their copies first, as a job (docs/WEB_API.md, `POST /api/export`).
import { useEffect, useRef, useState } from "react";
import type { ExportKind, View } from "../types";
import { EXPORT_CHOICES, exportBlocked, exportChoiceBlocked, exportLabel, exportTip } from "../lib/workbench";
import Tip from "./Tip";

interface Props {
  view: View;
  busy: boolean;
  onExport: (kind: ExportKind) => void;
}

export default function ExportMenu({ view, busy, onExport }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const firstRef = useRef<HTMLButtonElement>(null);
  const blocked = exportBlocked(view, busy);

  // The menu closes when work starts, a click lands elsewhere, or Escape is pressed (focus back on the button).
  useEffect(() => {
    if (blocked) setOpen(false);
  }, [blocked]);
  useEffect(() => {
    if (!open) return;
    firstRef.current?.focus();
    const away = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    const escape = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      rootRef.current?.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.focus();
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <Tip tip={open ? "" : exportTip(view, busy)}>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          disabled={!!blocked}
          aria-haspopup="menu"
          aria-expanded={open}
          data-tour="header-export"
          data-capability="header.export"
          className="flex items-center gap-1 rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs whitespace-nowrap text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
        >
          {exportLabel(view)}
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
      </Tip>
      {open && (
        <div
          role="menu"
          aria-label="Export the edited copies as"
          className="absolute right-0 z-40 mt-1.5 w-80 rounded-xl border border-edge bg-surface p-1.5 shadow-lg"
        >
          <p className="px-2.5 pt-1 pb-1.5 text-[11px] leading-snug text-ink-faint">
            Every edited copy, into a folder you choose next. Your copies in the window stay as they are.
          </p>
          {EXPORT_CHOICES.map((choice, index) => {
            const why = exportChoiceBlocked(choice.kind, view);
            return (
              <Tip key={choice.kind} tip={why} className="w-full">
                <button
                  ref={index === 0 ? firstRef : undefined}
                  type="button"
                  role="menuitem"
                  disabled={!!why}
                  onClick={() => {
                    setOpen(false);
                    onExport(choice.kind);
                  }}
                  data-capability="header.export-kinds"
                  className="flex w-full flex-col items-start gap-0.5 rounded-lg px-2.5 py-2 text-left transition-colors hover:bg-raised focus:bg-raised focus:outline-none disabled:pointer-events-none disabled:opacity-40"
                >
                  <span className="text-xs font-medium text-ink">{choice.label}</span>
                  <span className="text-[11px] leading-snug text-ink-dim">{why || choice.detail}</span>
                </button>
              </Tip>
            );
          })}
        </div>
      )}
    </div>
  );
}
