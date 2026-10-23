// Adapted from Build-a-Spec's frontend/src/components/Chat.tsx (copy-based reuse).
import { useCallback, useEffect, useRef, useState, type WheelEvent } from "react";
import type { Choice } from "../types";
import type { ActionButton } from "../lib/action";
import { STARTERS, endedLive, withLeaving, type ChatItem } from "../lib/chat";
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
  onSend: (text: string, chip?: boolean) => void;
  onChoose: (choice: Choice, wording?: string) => void;
  onAct: (token: string) => void;
  onContinue: () => void;
  onStop: () => void;
  prefill?: { text: string; nonce: number };
  onPrefill: (text: string) => void;
  onDraftChange: (hasDraft: boolean) => void;
}

export default function Chat({
  items,
  busy,
  canSend,
  sendReason,
  button,
  held,
  onSend,
  onChoose,
  onAct,
  onContinue,
  onStop,
  prefill,
  onPrefill,
  onDraftChange,
}: Props) {
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
  const entries = withLeaving(items, leaving);

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

  return (
    <section className="flex min-w-[400px] flex-1 basis-[46%] flex-col border-r border-edge" aria-label="Conversation">
      <div
        ref={scrollRef}
        onScroll={onScroll}
        onWheel={onWheel}
        style={{ overflowAnchor: "none" }}
        className="flex-1 overflow-y-auto px-5 py-6"
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
            <div className="mt-6 flex w-full max-w-md flex-col gap-2 text-left">
              {STARTERS.map((starter) => (
                <button
                  key={starter.title}
                  type="button"
                  onClick={() => onPrefill(starter.text)}
                  title="Puts this request in the box; edit it, then send"
                  className="rounded-xl border border-edge bg-surface px-4 py-2.5 transition-colors hover:border-accent/70 hover:bg-raised"
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
                      onChoose={onChoose}
                      onSend={onSend}
                    />
                  );
                case "pause":
                  return (
                    <PauseCard
                      key={item.key}
                      stepsPerPause={item.stepsPerPause}
                      costText={item.costText}
                      canContinue={item.canContinue}
                      busy={busy}
                      onContinue={onContinue}
                    />
                  );
                case "todo":
                  return <TodoCard key={item.key} names={item.names} busy={busy} onSend={onSend} />;
                default:
                  return null;
              }
            })}
          </div>
        )}
      </div>
      <ActionBar button={paused ? null : button} held={held} onAct={onAct} />
      <Composer
        busy={busy}
        canSend={canSend}
        reason={sendReason}
        onSend={onSend}
        onStop={onStop}
        prefill={prefill}
        onDraftChange={onDraftChange}
      />
    </section>
  );
}
