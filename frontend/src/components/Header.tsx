// Adapted from Build-a-Spec's frontend/src/components/Header.tsx (copy-based reuse): the title with its context
// line, the help topics beside it, then the pills and the buttons on the right.
import { memo } from "react";
import type { Mode, View } from "../types";
import { costPill, dollars, requestPill } from "../lib/chat";
import { HELP_TOPICS, sessionHeading, type HelpTopic } from "../lib/help";
import { MODE_LABELS, MODE_TIPS } from "../lib/modes";
import Tip from "./Tip";

interface Props {
  view: View;
  busy: boolean;
  /** "v1.7.0 available" for an update set aside with the banner's "Not now"; "" for none (lib/help.updatePill). */
  updatePill: string;
  onDownloadUpdate: () => void;
  onNewConversation: () => void;
  onOpenSettings: () => void;
  onOpenHelp: (topic: HelpTopic) => void;
  onMode: (mode: Mode) => void;
  /** The kill switch beside the spending: cuts off the AI step that is running and starts no other. */
  onStop: () => void;
}

const ORIGIN_TIPS: Record<string, string> = {
  saved: "A key remembered securely on this computer is available. It has not been tested with Anthropic.",
  environment: "A key from the ANTHROPIC_API_KEY environment variable is available. It has not been tested with Anthropic.",
  session: "A key entered for this session is available. It has not been tested with Anthropic.",
  none: "No API key is available. Add one in Settings to start.",
};

function Header({
  view,
  busy,
  updatePill,
  onDownloadUpdate,
  onNewConversation,
  onOpenSettings,
  onOpenHelp,
  onMode,
  onStop,
}: Props) {
  const safe = view.mode === "safe";
  const ready = !!view.connection?.ready;
  const calls = view.cost?.calls ?? 0;
  const fast = !!view.cost?.fast;
  const limit = view.cost?.limit;
  const request = busy && !safe ? requestPill(view.cost) : "";
  // The context line steps aside for the update pill: both only fit from 1280 px, and not together.
  const heading = updatePill ? "" : sessionHeading(view);

  return (
    <header className="flex items-center gap-4 border-b border-edge bg-surface px-5 py-3">
      {/* The one block that gives way on a narrow window: the context line truncates. */}
      <div className="flex min-w-0 items-baseline gap-3">
        <h1 className="flex-none font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight whitespace-nowrap">
          Spec Surgeon
        </h1>
        {heading && (
          <span className="hidden max-w-[22rem] min-w-0 truncate text-xs text-ink-dim xl:inline" title={heading}>
            {heading}
          </span>
        )}
      </div>
      <span className="h-5 w-px flex-none bg-edge" aria-hidden="true" />
      <nav className="flex flex-none items-center gap-0.5" aria-label="Help">
        {HELP_TOPICS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onOpenHelp(t.id)}
            className="rounded-md px-2 py-1 text-xs whitespace-nowrap text-ink-dim transition-colors hover:bg-raised hover:text-ink lg:px-2.5"
          >
            {t.label}
          </button>
        ))}
      </nav>
      <span className="h-5 w-px flex-none bg-edge" aria-hidden="true" />
      <div className="flex flex-none items-center rounded-lg border border-edge bg-raised p-0.5" role="group" aria-label="Mode">
        {(view.modes ?? ["safe", "default"]).map((mode) => (
          <Tip key={mode} tip={busy ? "Finish the current work first" : MODE_TIPS[mode]}>
            <button
              type="button"
              aria-pressed={view.mode === mode}
              disabled={busy}
              onClick={() => view.mode !== mode && onMode(mode)}
              className={`rounded-md px-2.5 py-0.5 text-xs whitespace-nowrap transition-colors disabled:pointer-events-none ${
                view.mode === mode
                  ? mode === "obliterate"
                    ? "bg-err text-white"
                    : "bg-accent text-on-accent"
                  : mode === "obliterate"
                    ? "text-err hover:text-err"
                    : "text-ink-dim hover:text-ink"
              }`}
            >
              {MODE_LABELS[mode]}
            </button>
          </Tip>
        ))}
      </div>
      <div className="flex-1" />
      <div className="flex flex-none items-center gap-2 lg:gap-3">
        {/* From 1280 px; below that About offers the same update. */}
        {updatePill && (
          <span className="hidden xl:inline-flex">
          <Tip
            tip={
              busy
                ? "Finish or stop the current work first — the installer closes the app"
                : "Downloads the update, checks it against the release’s SHA-256 checksums, then closes Spec Surgeon to install it; it reopens when it is done."
            }
          >
            <button
              type="button"
              onClick={onDownloadUpdate}
              disabled={busy}
              className="rounded-full border border-accent/60 bg-accent/15 px-3 py-1 text-xs whitespace-nowrap text-accent transition-colors hover:bg-accent/25 disabled:pointer-events-none disabled:opacity-60"
            >
              {updatePill} — install
            </button>
          </Tip>
          </span>
        )}
        <span
          title={
            `Estimated spending in this conversation — an estimate from a built-in price table, never a bill. ${calls} AI ${calls === 1 ? "step" : "steps"} so far.` +
            (typeof limit === "number"
              ? ` Spending check (Settings): the work pauses before one request could spend more than ${dollars(limit)}.`
              : " The spending check is off (Settings).") +
            (fast ? " Fast responses is in effect (Settings): AI steps are priced at the fast rate." : "")
          }
          className="rounded-full border border-edge bg-raised px-3 py-1 text-xs whitespace-nowrap text-ink-dim tabular-nums"
        >
          {costPill(view.cost)}
          <span className="ml-1 hidden text-ink-faint xl:inline">this conversation</span>
          {request && <span className="ml-1.5 text-ink-faint">· {request}</span>}
          {fast && <span className="ml-1.5 font-medium text-accent">Fast</span>}
        </span>
        {busy && !safe && (
          <Tip tip="Stop now — the kill switch: cuts off the AI step that is running at once and starts no other. Your progress is saved; nothing in your files changes.">
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop all AI work"
              className="rounded-full border border-err/60 bg-err/10 px-3 py-1 text-xs font-medium whitespace-nowrap text-err transition-colors hover:bg-err/20"
            >
              Stop
            </button>
          </Tip>
        )}
        <button
          type="button"
          onClick={onOpenSettings}
          title={
            safe
              ? "Safe mode makes no AI call, so it needs no API key. Default mode does; connect one in Settings."
              : ready && view.connection?.origin === "none"
                ? "SDK credentials are available. No connection test has been run."
                : (ORIGIN_TIPS[view.connection?.origin ?? "none"] ?? ORIGIN_TIPS.none)
          }
          className="flex items-center gap-2 rounded-full border border-edge bg-raised px-3 py-1 text-xs whitespace-nowrap text-ink-dim transition-colors hover:border-accent hover:text-accent"
        >
          <span className={`h-2 w-2 rounded-full ${safe ? "bg-ink-faint" : ready ? "bg-ok" : "bg-warn"}`} aria-hidden />
          {safe ? "No AI in Safe mode" : ready ? (view.connection?.origin === "none" ? "Credentials available" : "Key available") : "Add your API key"}
        </button>
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
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs whitespace-nowrap text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          >
            New conversation
          </button>
        </Tip>
        <button
          type="button"
          onClick={onOpenSettings}
          title="Settings — your AI connection"
          aria-label="Settings"
          className="flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-edge bg-raised text-ink-dim transition-colors hover:border-accent hover:text-accent"
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
