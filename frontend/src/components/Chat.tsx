// Adapted from Build-a-Spec's frontend/src/components/Chat.tsx (copy-based reuse).
import { useCallback, useEffect, useRef, useState, type ReactNode, type WheelEvent } from "react";
import type { Choice, Mode } from "../types";
import type { ActionButton, DiscardButton } from "../lib/action";
import { STARTERS, endedLive, withLeaving, type ChatItem } from "../lib/chat";
import { TOUR_HOLD } from "../lib/tour";
import type { LiveReply } from "../lib/view";
import { usePrefersReducedMotion } from "../lib/useReveal";
import ChatMessage, { ErrorBubble } from "./MessageBubble";
import ActionBar from "./ActionBar";
import Composer from "./Composer";
import Prompts from "./Prompts";
import PauseCard from "./PauseCard";
import TodoCard from "./TodoCard";

interface Props {
  items: ChatItem[];
  busy: boolean;
  canSend: boolean;
  sendReason: string;
  /** The one button (Create edited copies, Continue, Save ready copies); the pause card owns Continue while paused. */
  button: ActionButton | null;
  /** How many previews carry the red X. */
  held: number;
  /** Include all: takes every red X back. */
  onIncludeAll?: () => void;
  /** How many previews the server set aside: they did not pass while others did (`samples[].set_aside`). */
  aside?: number;
  onSend: (text: string, chip?: boolean) => void;
  onChoose: (choice: Choice, wording?: string) => void;
  onAct: (token: string) => void;
  onContinue: () => void;
  onStop: () => void;
  prefill?: { text: string; nonce: number };
  onPrefill: (text: string) => void;
  draft: string;
  onDraftChange: (text: string) => void;
  /** Safe mode: the buttons (``panel``) take the composer's place, and nothing here talks to the model. */
  mode: Mode;
  panel?: ReactNode;
  onMode: (mode: Mode) => void;
  /** Discard, beside the one button: a waiting preview, or a conversation's open request (``lib/action.discardButton``). */
  discard?: DiscardButton | null;
  onDiscard?: () => void;
  /** The guided tour runs: the conversation's controls are held still (its conversation is a recording). */
  tourActive: boolean;
  /** This computer finished the tour (the chip then invites to take it again); null until the first view. */
  tourFinished: boolean | null;
  onStartTour: () => void;
}

export default function Chat({
  items,
  busy,
  canSend,
  sendReason,
  button,
  held,
  onIncludeAll,
  aside = 0,
  onSend,
  onChoose,
  onAct,
  onContinue,
  onStop,
  prefill,
  onPrefill,
  onDraftChange,
  draft,
  mode,
  panel,
  onMode,
  discard,
  onDiscard,
  tourActive,
  tourFinished,
  onStartTour,
}: Props) {
  const safe = mode === "safe";
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const lastTopRef = useRef(0);
  const followRef = useRef<number | null>(null);
  const reduced = usePrefersReducedMotion();
  const empty = items.length === 0;
  const paused = items.some((item) => item.kind === "pause");

  // A working line whose job ended without a message in its place folds away instead of vanishing: kept under its
  // key, in the same list (so it stays the same bubble), for as long as the fold takes. Worked out while rendering
  // (React's pattern for state that follows a prop), so no frame ever renders the list without it.
  const [leaving, setLeaving] = useState<{ key: string; live: LiveReply } | null>(null);
  const [shown, setShown] = useState(items);
  if (shown !== items) {
    setShown(items);
    const ended = endedLive(shown, items);
    if (ended) setLeaving(ended);
  }
  useEffect(() => {
    if (!leaving) return;
    const timer = window.setTimeout(() => setLeaving(null), 450);
    return () => window.clearTimeout(timer);
  }, [leaving]);
  // Safe mode has no conversation: no question cards, no pause (no AI step runs there).
  const entries = withLeaving(items, leaving).filter((item) => !safe || (item.kind !== "prompts" && item.kind !== "pause"));

  const stopFollowing = () => {
    if (followRef.current !== null) cancelAnimationFrame(followRef.current);
    followRef.current = null;
  };

  // Follow the bottom while pinned: the view eases down to new content over a few frames instead of jumping
  // (a card that lands, a line the reply grows by), and jumps only when more than a screen is waiting (a
  // conversation opened from history). Never while the reader has scrolled up.
  const follow = useCallback(() => {
    const el = scrollRef.current;
    if (!el || !pinnedRef.current || followRef.current !== null) return;
    let last = 0;
    const tick = (now: number) => {
      const box = scrollRef.current;
      if (!box || !pinnedRef.current) {
        followRef.current = null;
        return;
      }
      const bottom = box.scrollHeight - box.clientHeight;
      const gap = bottom - box.scrollTop;
      if (gap <= 1) {
        followRef.current = null;
        return;
      }
      const dt = last ? Math.min(now - last, 64) : 16;
      last = now;
      const step = reduced || gap > box.clientHeight ? gap : Math.max(1, gap * (1 - Math.exp(-dt / 110)));
      box.scrollTop = Math.min(bottom, box.scrollTop + step);
      lastTopRef.current = box.scrollTop;
      followRef.current = requestAnimationFrame(tick);
    };
    followRef.current = requestAnimationFrame(tick);
  }, [reduced]);

  useEffect(() => stopFollowing, []);

  // A message the user just sent brings the view back down to it, wherever they were reading.
  let sentKey = "";
  for (let i = items.length - 1; i >= 0; i -= 1) {
    const item = items[i];
    if (item.kind !== "message") continue;
    sentKey = item.message.role === "user" ? item.key : "";
    break;
  }
  useEffect(() => {
    if (sentKey) pinnedRef.current = true;
  }, [sentKey]);

  // New items, and every change of size after layout (the reply growing frame by frame inside its bubble, a
  // fold opening or closing): follow while pinned.
  useEffect(() => {
    follow();
  }, [items, follow]);

  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const el = scrollRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => follow());
    observer.observe(el);
    if (contentRef.current) observer.observe(contentRef.current);
    return () => observer.disconnect();
  }, [empty, follow]);

  // Pinned near the bottom; unpinned the moment the reader moves up (the follower only ever moves down, and a
  // fold closing at the bottom only ever lands the view on it).
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const top = el.scrollTop;
    if (el.scrollHeight - top - el.clientHeight < 80) pinnedRef.current = true;
    else if (top < lastTopRef.current - 1) pinnedRef.current = false;
    lastTopRef.current = top;
  };

  const onWheel = (event: WheelEvent) => {
    if (event.deltaY < 0) stopFollowing();
  };

  // Safe mode's buttons and forms are the work surface. An empty conversation does not keep a transcript
  // column open for them: the header already says there is no AI, and a centered hero would only crowd the buttons.
  const showTranscript = !(empty && safe);

  return (
    <section
      className="flex min-h-0 min-w-[400px] flex-1 basis-[46%] flex-col border-r border-edge"
      aria-label="Conversation"
      data-tour="chat-pane"
    >
      {showTranscript && (
        <div
          ref={scrollRef}
          onScroll={onScroll}
          onWheel={onWheel}
          style={{ overflowAnchor: "none" }}
          className={safe ? "max-h-[18vh] min-h-0 shrink-0 overflow-y-auto px-5 py-3" : "flex-1 overflow-y-auto px-5 py-6"}
        >
          {empty ? (
            <div className="flex h-full flex-col items-center justify-center text-center">
              <p className="font-[family-name:var(--font-display)] text-[1.7rem] leading-tight text-ink">
                Less document work. More done.
              </p>
              <p className="mt-3 max-w-md text-sm leading-relaxed text-ink-dim">
                Add your Word files and tell me what you want to change. I&apos;ll check the documents, work
                out a plan, and show you what will happen.
              </p>
              <div className="mt-6 flex w-full max-w-md flex-col gap-2 text-left" data-tour="starters" data-capability="chat.starters">
                <button
                  type="button"
                  onClick={onStartTour}
                  disabled={busy || tourActive}
                  data-tour="tour-chip"
                  data-capability="tour.start"
                  title="A guided tour of the window on bundled practice documents; your conversation is set aside and comes back when it ends"
                  className={`rounded-xl border border-accent/50 bg-accent/10 px-4 py-2.5 text-left transition-colors hover:border-accent hover:bg-accent/15 disabled:pointer-events-none disabled:opacity-40 ${
                    tourActive || tourFinished !== false ? "" : "chip-pulse"
                  }`}
                >
                  <span className="block text-sm text-ink">
                    🧭 {tourFinished ? "Take the guided tour again" : "New to Spec Surgeon? Take the guided tour"}
                  </span>
                  <span className="mt-0.5 block text-[11px] text-ink-faint">
                    {tourActive
                      ? "You are taking it right now"
                      : "Every part of the window, on bundled practice documents · no AI call · nothing of yours is touched"}
                  </span>
                </button>
                {STARTERS.map((starter) => (
                  <button
                    key={starter.title}
                    type="button"
                    onClick={() => onPrefill(starter.text)}
                    disabled={busy || tourActive}
                    title={tourActive ? TOUR_HOLD : "Puts this request in the box; edit it, then send"}
                    className="rounded-xl border border-edge bg-surface px-4 py-2.5 transition-colors hover:border-accent/70 hover:bg-raised disabled:pointer-events-none disabled:opacity-40"
                  >
                    <span className="block text-[11px] font-medium tracking-wide text-ink-faint uppercase">
                      {starter.title}
                    </span>
                    <span className="mt-0.5 block text-sm leading-snug text-ink-dim">“{starter.text}”</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div ref={contentRef} className="mx-auto flex max-w-3xl flex-col gap-5">
              {entries.map((item) => {
                switch (item.kind) {
                  case "message":
                    return <ChatMessage key={item.key} message={item.message} live={null} aside={item.aside} />;
                  case "live":
                    return <ChatMessage key={item.key} message={null} live={item.live} aside={null} leaving={item.leaving} />;
                  case "error":
                    return <ErrorBubble key={item.key} text={item.text} />;
                  case "prompts":
                    return (
                      <Prompts
                        key={item.key}
                        questions={item.questions}
                        choices={item.choices}
                        chips={item.chips}
                        busy={busy}
                        tourActive={tourActive}
                        onChoose={onChoose}
                        onSend={onSend}
                      />
                    );
                  case "pause":
                    return (
                      <PauseCard
                        key={item.key}
                        reason={item.reason}
                        costText={item.costText}
                        requestText={item.requestText}
                        limitText={item.limitText}
                        canContinue={item.canContinue}
                        busy={busy}
                        tourActive={tourActive}
                        onContinue={onContinue}
                      />
                    );
                  case "todo":
                    return (
                      <TodoCard
                        key={item.key}
                        items={item.items}
                        busy={busy}
                        tourActive={tourActive}
                        onSend={onSend}
                        onDefaultMode={safe ? () => onMode("default") : undefined}
                      />
                    );
                  default:
                    return null;
                }
              })}
            </div>
          )}
        </div>
      )}
      <ActionBar
        button={paused || (safe && button?.kind === "stop") ? null : button}
        held={held}
        onIncludeAll={onIncludeAll}
        aside={aside}
        onAct={onAct}
        discard={discard}
        onDiscard={onDiscard}
      />
      {safe ? (
        <div
          className={`flex min-h-0 flex-1 flex-col bg-surface px-4 pt-2.5 pb-2 ${showTranscript ? "border-t border-edge" : ""}`}
          role="region"
          aria-label="Safe mode"
        >
          <div className="mx-auto min-h-0 w-full max-w-3xl flex-1 overflow-y-auto">{panel}</div>
        </div>
      ) : (
        <>
        {mode === "obliterate" && (
          <div
            className="flex items-center gap-3 border-t border-err/50 bg-err/10 px-4 py-1.5 text-[11px] text-ink"
            role="note"
            aria-label="Obliterate mode"
          >
            <span className="min-w-0 flex-1">
              <span className="font-semibold text-err">Obliterate mode.</span> Claude may write wording and change
              anything Word stores; a copy whose checks fail is delivered marked UNVERIFIED. Copies only — your
              originals are never written.
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => onMode("default")}
              className="flex-none rounded-md border border-err/50 px-2 py-0.5 text-err transition-colors hover:bg-err/15 disabled:pointer-events-none disabled:opacity-60"
            >
              Back to Default
            </button>
          </div>
        )}
        <Composer
          busy={busy}
          canSend={canSend}
          tourActive={tourActive}
          reason={sendReason}
          onSend={onSend}
          onStop={onStop}
          prefill={prefill}
          value={draft}
          onChange={onDraftChange}
        />
        </>
      )}
    </section>
  );
}
