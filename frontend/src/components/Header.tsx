// Adapted from Build-a-Spec's frontend/src/components/Header.tsx (copy-based reuse): the title with its context
// line, the help topics beside it, then the pills and the buttons on the right.
import { memo, useRef } from "react";
import type { ExportKind, Mode, View } from "../types";
import { costPill, dollars, requestPill } from "../lib/chat";
import { HELP_TOPICS, sessionHeading, type HelpTopic } from "../lib/help";
import { MODE_LABELS, MODE_TIPS } from "../lib/modes";
import { useFit } from "../lib/useFit";
import { exportLabel } from "../lib/workbench";
import ExportMenu from "./ExportMenu";
import Tip from "./Tip";

interface Props {
  view: View;
  busy: boolean;
  /** "v1.7.0 available" for an update set aside with the banner's "Not now"; "" for none (lib/help.updatePill). */
  updatePill: string;
  onDownloadUpdate: () => void;
  /** Export, the menu's choice: the edited copies written to a folder the native dialog asks for, in that kind. */
  onExport: (kind: ExportKind) => void;
  onNewConversation: () => void;
  onOpenSettings: () => void;
  onOpenHelp: (topic: HelpTopic) => void;
  onMode: (mode: Mode) => void;
  /** The kill switch beside the spending: cuts off the AI step that is running and starts no other. */
  onStop: () => void;
  /** The guided tour runs: it switches modes as its chapters need, so the switch is held still. */
  tourActive: boolean;
  onStartTour: () => void;
}

const ORIGIN_TIPS: Record<string, string> = {
  saved: "A key remembered securely on this computer is available. It has not been tested with Anthropic.",
  environment: "A key from the ANTHROPIC_API_KEY environment variable is available. It has not been tested with Anthropic.",
  session: "A key entered for this session is available. It has not been tested with Anthropic.",
  none: "No API key is available. Add one in Settings to start.",
};

/**
 * What gives way, in this order, while the bar holds more than the window's width (lib/useFit.ts measures it, whatever
 * the bar holds: the spending, Stop, a third mode, an update, the copies to save). The title, every help topic, the
 * mode switch and every button stay; only room and words go, and what a control no longer says, its hover text does.
 */
const GIVE_WAY = [
  "context line", // the documents tray lists the same
  "spacing",
  "spending words", // "this conversation"
  "key words", // "Key available" → "Key"; a missing key keeps "Add your API key" whole
  "help words", // one word per topic (lib/help.ts, `short`), the full label on hover
  "button words", // "New conversation" → "New", "Export · 3 unsaved" → "Export · 3"
  "dividers",
  "request words", // "this request ≈ $0.40 of $20.00" → "≈ $0.40 of $20.00"
  "closer spacing",
  "update pill", // set aside with the banner's Not now, kept one click away while anything else can go; About offers it too
] as const;

/** The connection pill's words, in full and in fewer. */
function keyWords(view: View): [string, string] {
  if (view.mode === "safe") return ["No AI in Safe mode", "No AI"];
  if (!view.connection?.ready) return ["Add your API key", "Add your API key"];
  return view.connection.origin === "none" ? ["Credentials available", "Credentials"] : ["Key available", "Key"];
}

function Header({
  view,
  busy,
  updatePill,
  onDownloadUpdate,
  onExport,
  onNewConversation,
  onOpenSettings,
  onOpenHelp,
  onMode,
  onStop,
  tourActive,
  onStartTour,
}: Props) {
  const safe = view.mode === "safe";
  const ready = !!view.connection?.ready;
  const calls = view.cost?.calls ?? 0;
  const fast = !!view.cost?.fast;
  const limit = view.cost?.limit;
  const request = busy && !safe ? requestPill(view.cost) : "";
  const heading = sessionHeading(view);
  const [keyFull, keyShort] = keyWords(view);

  // Everything whose width the bar measures, in full: a change starts the giving way again from nothing.
  const bar = useRef<HTMLElement>(null);
  const shown = [heading, costPill(view.cost), request, fast, keyFull, exportLabel(view), view.modes, busy, updatePill];
  const fit = useFit(bar, JSON.stringify(shown), GIVE_WAY.length);
  const gone = (what: (typeof GIVE_WAY)[number]) => fit > GIVE_WAY.indexOf(what);
  const tight = gone("spacing");
  const closer = gone("closer spacing");

  return (
    <header
      ref={bar}
      className={`flex items-center border-b border-edge bg-surface px-5 py-3 ${closer ? "gap-2" : tight ? "gap-3" : "gap-4"}`}
      data-tour="header"
    >
      <div className="flex flex-none items-baseline gap-3">
        <h1 className="font-[family-name:var(--font-display)] text-lg font-semibold tracking-tight whitespace-nowrap">
          Spec Surgeon
        </h1>
        {heading && !gone("context line") && (
          <span className="max-w-[22rem] truncate text-xs text-ink-dim" title={heading}>
            {heading}
          </span>
        )}
      </div>
      {!gone("dividers") && <span className="h-5 w-px flex-none bg-edge" aria-hidden="true" />}
      <nav className="flex flex-none items-center gap-0.5" aria-label="Help" data-tour="help-nav" data-capability="help.topics">
        <Tip
          tip={
            tourActive
              ? "The tour is running"
              : busy
                ? "Finish or stop the current work first"
                : "A guided tour of the window on bundled practice documents; your conversation is set aside and comes back when it ends"
          }
        >
          <button
            type="button"
            onClick={onStartTour}
            disabled={tourActive || busy}
            data-capability="tour.start"
            className={`rounded-md px-2 py-1 text-xs whitespace-nowrap text-accent transition-colors hover:bg-raised disabled:pointer-events-none disabled:opacity-60 ${tight ? "" : "lg:px-2.5"}`}
          >
            Tour
          </button>
        </Tip>
        {HELP_TOPICS.map((t) => {
          const short = gone("help words") && t.short !== t.label;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onOpenHelp(t.id)}
              title={short ? t.label : undefined}
              className={`rounded-md px-2 py-1 text-xs whitespace-nowrap text-ink-dim transition-colors hover:bg-raised hover:text-ink ${tight ? "" : "lg:px-2.5"}`}
            >
              {short ? t.short : t.label}
            </button>
          );
        })}
      </nav>
      {!gone("dividers") && <span className="h-5 w-px flex-none bg-edge" aria-hidden="true" />}
      <div
        className="flex flex-none items-center rounded-lg border border-edge bg-raised p-0.5"
        role="group"
        aria-label="Mode"
        data-tour="mode-switch"
        data-capability="header.modes"
      >
        {(view.modes ?? ["safe", "default"]).map((mode) => (
          <Tip
            key={mode}
            tip={
              busy
                ? "Finish the current work first"
                : tourActive
                  ? "The tour switches modes as its chapters need"
                  : MODE_TIPS[mode]
            }
          >
            <button
              type="button"
              aria-pressed={view.mode === mode}
              disabled={busy || tourActive}
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
      <div className={`flex flex-none items-center ${closer ? "gap-1.5" : tight ? "gap-2" : "gap-2 lg:gap-3"}`}>
        {updatePill && !gone("update pill") && (
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
          data-tour="spend-pill"
          data-capability="header.spending"
        >
          {costPill(view.cost)}
          {!gone("spending words") && <span className="ml-1 text-ink-faint">this conversation</span>}
          {request && (
            <span className="ml-1.5 text-ink-faint">· {gone("request words") ? requestPill(view.cost, true) : request}</span>
          )}
          {fast && <span className="ml-1.5 font-medium text-accent">Fast</span>}
        </span>
        {busy && !safe && (
          <Tip tip="Stop now — the kill switch: cuts off the AI step that is running at once and starts no other. Your progress is saved; nothing in your files changes.">
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop all AI work"
              data-capability="header.stop"
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
          aria-label={keyFull}
          className="flex items-center gap-2 rounded-full border border-edge bg-raised px-3 py-1 text-xs whitespace-nowrap text-ink-dim transition-colors hover:border-accent hover:text-accent"
          data-tour="connection"
          data-capability="header.connection"
        >
          <span className={`h-2 w-2 rounded-full ${safe ? "bg-ink-faint" : ready ? "bg-ok" : "bg-warn"}`} aria-hidden />
          {gone("key words") ? keyShort : keyFull}
        </button>
        <ExportMenu view={view} busy={busy} onExport={onExport} short={gone("button words")} />
        <Tip
          tip={
            busy
              ? "Finish or stop the current work first"
              : "Starts an empty conversation. Your originals stay where they are; copies you have not saved with Export stay in the window's scratch folder until it closes."
          }
        >
          <button
            type="button"
            onClick={onNewConversation}
            disabled={busy}
            aria-label="New conversation"
            data-tour="new-conversation"
            data-capability="header.new-conversation"
            className="rounded-lg border border-edge bg-raised px-3 py-1.5 text-xs whitespace-nowrap text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
          >
            {gone("button words") ? "New" : "New conversation"}
          </button>
        </Tip>
        <button
          type="button"
          onClick={onOpenSettings}
          title="Settings — your AI connection"
          aria-label="Settings"
          data-tour="settings"
          data-capability="header.settings"
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
