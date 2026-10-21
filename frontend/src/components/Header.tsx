// Adapted from Build-a-Spec's frontend/src/components/Header.tsx (copy-based reuse).
import { memo, useEffect, useRef, useState } from "react";
import type { View } from "../types";
import { costPill } from "../lib/chat";
import Tip from "./Tip";

interface Props {
  view: View;
  busy: boolean;
  workbenchOpen: boolean;
  onToggleWorkbench: () => void;
  onNewConversation: () => void;
  onOpenSettings: () => void;
  onOpenAbout: () => void;
  onOpenLogs: () => void;
  onCheckUpdates: () => void;
}

const ORIGIN_TIPS: Record<string, string> = {
  saved: "Connected with the key remembered securely on this computer.",
  environment: "Connected with the key from the ANTHROPIC_API_KEY environment variable.",
  session: "Connected with a key entered for this session only.",
  none: "No API key is connected. Connect one in Settings to start.",
};

function Header({
  view,
  busy,
  workbenchOpen,
  onToggleWorkbench,
  onNewConversation,
  onOpenSettings,
  onOpenAbout,
  onOpenLogs,
  onCheckUpdates,
}: Props) {
  const [helpOpen, setHelpOpen] = useState(false);
  const helpRef = useRef<HTMLDivElement>(null);
  const ready = !!view.connection?.ready;
  const calls = view.cost?.calls ?? 0;

  // The Help menu closes on a click anywhere else and on Escape.
  useEffect(() => {
    if (!helpOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!helpRef.current?.contains(e.target as Node)) setHelpOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelpOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [helpOpen]);

  const menuItem =
    "block w-full px-3 py-1.5 text-left text-xs text-ink-dim transition-colors hover:bg-surface hover:text-ink";

  return (
    <header className="flex items-center gap-3 border-b border-edge bg-surface px-5 py-2.5">
      <h1 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight">
        Spec Surgeon
      </h1>
      <div className="flex-1" />
      <div className="flex flex-none items-center gap-2">
        <span
          title={`An estimate from a built-in price table, never a bill. ${calls} AI ${calls === 1 ? "step" : "steps"} so far in this conversation.`}
          className="rounded-full border border-edge bg-raised px-3 py-1 text-xs text-ink-dim tabular-nums"
        >
          {costPill(view.cost)}
          <span className="ml-1 text-ink-faint">this conversation</span>
        </span>
        <button
          type="button"
          onClick={onOpenSettings}
          title={ORIGIN_TIPS[view.connection?.origin ?? "none"] ?? ORIGIN_TIPS.none}
          className="flex items-center gap-2 rounded-full border border-edge bg-raised px-3 py-1 text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent"
        >
          <span className={`h-2 w-2 rounded-full ${ready ? "bg-ok" : "bg-warn"}`} aria-hidden />
          {ready ? "Connected" : "Connect your API key"}
        </button>
        <Tip
          tip={
            workbenchOpen
              ? "Hide the plan and pages to give the conversation the whole window"
              : "Show the documents, the plan and the pages"
          }
        >
          <button
            type="button"
            onClick={onToggleWorkbench}
            aria-pressed={workbenchOpen}
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs text-ink transition-colors hover:border-accent hover:text-accent"
          >
            {workbenchOpen ? "Hide plan ▸" : "◂ Show plan"}
          </button>
        </Tip>
        <Tip
          tip={
            busy
              ? "Finish or stop the current work first"
              : "Archives this conversation and starts an empty one. Your documents and copies stay where they are."
          }
        >
          <button
            type="button"
            onClick={onNewConversation}
            disabled={busy}
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          >
            New conversation
          </button>
        </Tip>
        <div className="relative" ref={helpRef}>
          <button
            type="button"
            onClick={() => setHelpOpen((o) => !o)}
            aria-haspopup="menu"
            aria-expanded={helpOpen}
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs text-ink transition-colors hover:border-accent hover:text-accent"
          >
            Help ▾
          </button>
          {helpOpen && (
            <div
              role="menu"
              className="absolute right-0 z-30 mt-1 w-52 rounded-md border border-edge bg-raised py-1 shadow-lg"
            >
              <button type="button" role="menuitem" className={menuItem} onClick={() => { setHelpOpen(false); onCheckUpdates(); }}>
                Check for updates
              </button>
              <button type="button" role="menuitem" className={menuItem} onClick={() => { setHelpOpen(false); onOpenLogs(); }}>
                Open diagnostic logs
              </button>
              <div className="my-1 border-t border-edge/60" />
              <button type="button" role="menuitem" className={menuItem} onClick={() => { setHelpOpen(false); onOpenAbout(); }}>
                About Spec Surgeon
              </button>
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={onOpenSettings}
          title="Settings — your AI connection"
          aria-label="Settings"
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-edge bg-raised text-ink-dim transition-colors hover:border-accent hover:text-accent"
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
        </button>
      </div>
    </header>
  );
}

// Memoized: a streamed delta changes only the live bubble, so the header keeps its last render.
export default memo(Header);
