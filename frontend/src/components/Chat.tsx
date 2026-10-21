// Adapted from Build-a-Spec's frontend/src/components/Chat.tsx (copy-based reuse).
import { useEffect, useRef } from "react";
import type { Choice } from "../types";
import { STARTERS, type ChatItem } from "../lib/chat";
import MessageBubble, { ErrorBubble, LiveBubble } from "./MessageBubble";
import Composer from "./Composer";
import Prompts from "./Prompts";
import PauseCard from "./PauseCard";

interface Props {
  items: ChatItem[];
  busy: boolean;
  canSend: boolean;
  sendReason: string;
  onSend: (text: string, chip?: boolean) => void;
  onChoose: (choice: Choice) => void;
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
  onSend,
  onChoose,
  onContinue,
  onStop,
  prefill,
  onPrefill,
  onDraftChange,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const pinnedRef = useRef(true);
  const empty = items.length === 0;

  // Stay pinned to the bottom on new items unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (el && pinnedRef.current) el.scrollTop = el.scrollHeight;
  }, [items]);

  // While a reply streams, the smoothed text grows between updates (via
  // requestAnimationFrame inside the bubble). Follow the bottom when the
  // content actually GROWS — a ResizeObserver fires after layout, only on a
  // size change — only while pinned, never yanking scroll from a reader.
  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    const el = scrollRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      if (pinnedRef.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    if (contentRef.current) observer.observe(contentRef.current);
    return () => observer.disconnect();
  }, [empty]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    pinnedRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  return (
    <section className="flex min-w-[400px] flex-1 basis-[46%] flex-col border-r border-edge" aria-label="Conversation">
      <div
        ref={scrollRef}
        onScroll={onScroll}
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
            {items.map((item, index) => {
              switch (item.kind) {
                case "message":
                  return <MessageBubble key={item.message.id} message={item.message} aside={item.aside} />;
                case "live":
                  return <LiveBubble key="live" live={item.live} />;
                case "error":
                  return <ErrorBubble key={`error-${index}`} text={item.text} />;
                case "prompts":
                  return (
                    <Prompts
                      key="prompts"
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
                      key="pause"
                      stepsPerPause={item.stepsPerPause}
                      costText={item.costText}
                      canContinue={item.canContinue}
                      busy={busy}
                      onContinue={onContinue}
                    />
                  );
                default:
                  return null;
              }
            })}
          </div>
        )}
      </div>
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
