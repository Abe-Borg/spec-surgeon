import { memo } from "react";
import { ADD_KEY, SAFE_NOTE_HIDE, SAFE_NOTE_SHOW, SWITCH_TO_DEFAULT, safeNoteWords } from "../lib/safeNote";
import { TOUR_HOLD } from "../lib/tour";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  /** The window has an API key (the view's `connection.ready`). */
  keyReady: boolean;
  /** Folded to its one line (settings.json `safe_note_hidden`). */
  hidden: boolean;
  busy: boolean;
  /** The guided tour runs: the note is shown, never changed, and the mode stays as the tour set it. */
  tourActive: boolean;
  /** Hide, or What is Safe mode? (`POST /api/settings {"safe_note_hidden"}`). */
  onHidden: (hidden: boolean) => void;
  /** Switch to Default mode, as the top bar's switch does. */
  onDefaultMode: () => void;
  /** Settings, where the API key is added. */
  onAddKey: () => void;
}

const linkBtn =
  "text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent " +
  "disabled:pointer-events-none disabled:opacity-50";

function Shield() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden className="flex-none text-accent">
      <path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6z" />
      <path d="M9 12l2 2 4-4" />
    </svg>
  );
}

/**
 * The note above Safe mode's buttons: what Safe mode is, what it cannot do, and Default mode with a Claude API key
 * for more (lib/safeNote.ts holds its words). Hide folds it to one line; What is Safe mode? opens it again.
 */
function SafeNote({ keyReady, hidden, busy, tourActive, onHidden, onDefaultMode, onAddKey }: Props) {
  const words = safeNoteWords(keyReady);
  const holdTip = tourActive ? TOUR_HOLD : busy ? "Wait for the current work to finish" : "";
  const toggle = (
    <Tip tip={holdTip || (hidden ? "Opens the note about Safe mode" : "Folds this note to one line; the window remembers")}>
      <button type="button" disabled={tourActive} onClick={() => onHidden(!hidden)} className={linkBtn}>
        {hidden ? SAFE_NOTE_SHOW : SAFE_NOTE_HIDE}
      </button>
    </Tip>
  );

  if (hidden) {
    return (
      <div
        className="flex flex-wrap items-center gap-2 rounded-lg border border-edge bg-bg px-3 py-1.5 text-[12.5px] text-ink-dim"
        role="note"
        aria-label="About Safe mode"
        data-tour="safe-note"
        data-capability="safe.note"
      >
        <Shield />
        <span className="min-w-0 flex-1">
          <span className="font-semibold text-ink">{words.title}:</span> {words.line}
        </span>
        {toggle}
      </div>
    );
  }

  const switchButton = (filled: boolean) => (
    <Tip key="default" tip={holdTip || "Default mode: describe the change in your own words; the buttons give way to the message box"}>
      <button
        type="button"
        disabled={busy || tourActive}
        onClick={onDefaultMode}
        className={filled ? primaryBtn : quietBtn}
        data-capability="safe.default"
      >
        {SWITCH_TO_DEFAULT}
      </button>
    </Tip>
  );
  const keyButton = (
    <Tip key="key" tip={tourActive ? TOUR_HOLD : "Opens Settings, where you add your Claude API key"}>
      <button type="button" disabled={tourActive} onClick={onAddKey} className={primaryBtn} data-capability="safe.default">
        {ADD_KEY}
      </button>
    </Tip>
  );

  return (
    <div
      className="flex flex-col gap-2 rounded-xl border border-edge bg-bg px-3.5 py-3"
      role="note"
      aria-label="About Safe mode"
      data-tour="safe-note"
      data-capability="safe.note"
    >
      <div className="flex items-center gap-2">
        <Shield />
        <span className="text-[13.5px] font-semibold text-ink">{words.title}</span>
        <span className="min-w-0 flex-1 text-[11.5px] text-ink-faint">· {words.tags}</span>
        {toggle}
      </div>
      <p className="text-[13px] leading-snug text-ink-dim">{words.what}</p>
      <p className="text-[13px] leading-snug text-ink-dim">
        <span className="font-semibold text-ink">What it can&apos;t do:</span> {words.cannot}
      </p>
      <div className="flex flex-col gap-2 border-t border-edge pt-2.5">
        <p className="text-[13px] leading-snug text-ink-dim">
          <span className="font-semibold text-ink">Need more control?</span> {words.more}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {words.primary === "key" ? [keyButton, switchButton(false)] : switchButton(true)}
        </div>
        <p className="flex items-center gap-1.5 text-[12px] text-ink-faint">
          <span className={`h-2 w-2 rounded-full ${keyReady ? "bg-ok" : "bg-warn"}`} aria-hidden />
          {words.key}
        </p>
      </div>
    </div>
  );
}

export default memo(SafeNote);
