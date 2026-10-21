// Adapted from Build-a-Spec's frontend/src/components/MessageBubble.tsx (copy-based reuse).
import { memo, useEffect, useRef, useState } from "react";
import type { Message } from "../types";
import type { Aside, LiveReply } from "../lib/view";
import { liveHasContent } from "../lib/chat";
import { splitStableTail, useSmoothText } from "../lib/useSmoothText";
import Markdown from "./Markdown";
import StatusStrip from "./StatusStrip";

/** Collapsible thinking summary, muted + italic, distinct from the answer.
 *  Auto-expands while the model thinks with nothing written yet, then
 *  collapses once real text starts (still expandable afterward). */
function ThinkingBlock({ text, autoExpand }: { text: string; autoExpand: boolean }) {
  const [open, setOpen] = useState(autoExpand);
  const wasAuto = useRef(autoExpand);
  useEffect(() => {
    if (wasAuto.current && !autoExpand) setOpen(false);
    wasAuto.current = autoExpand;
  }, [autoExpand]);

  return (
    <div className="mb-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] font-medium tracking-wide text-ink-faint uppercase transition-colors hover:text-ink-dim"
      >
        <span className="text-[9px]">{open ? "▾" : "▸"}</span>
        Thinking
      </button>
      {open && (
        <div className="mt-1 border-l-2 border-edge pl-3 text-[0.82rem] leading-relaxed text-ink-faint italic whitespace-pre-wrap">
          {text}
        </div>
      )}
    </div>
  );
}

/** The model's activity, one quiet italic line each ("↳ Reading the template's header"). */
function ActivityLines({ lines }: { lines: string[] }) {
  if (lines.length === 0) return null;
  return (
    <ul className="my-1.5 space-y-0.5 text-[0.8rem] text-ink-faint italic">
      {lines.map((line, index) => (
        <li key={`${index}-${line}`} className="flex gap-1.5">
          <span aria-hidden className="not-italic">
            ↳
          </span>
          <span>{line}</span>
        </li>
      ))}
    </ul>
  );
}

/** What a finished reply keeps of its making, folded under it. */
function AsideBlock({ aside }: { aside: Aside }) {
  const [open, setOpen] = useState(false);
  const steps = aside.activities.length;
  const label =
    steps > 0
      ? `${steps} ${steps === 1 ? "step" : "steps"}${aside.thinking ? " · thinking" : ""}`
      : "Thinking";
  return (
    <div className="mt-2">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-[11px] font-medium tracking-wide text-ink-faint uppercase transition-colors hover:text-ink-dim"
      >
        <span className="text-[9px]">{open ? "▾" : "▸"}</span>
        {label}
      </button>
      {open && (
        <div className="mt-1 border-l-2 border-edge pl-3">
          <ActivityLines lines={aside.activities} />
          {aside.thinking && (
            <div className="text-[0.82rem] leading-relaxed text-ink-faint italic whitespace-pre-wrap">
              {aside.thinking}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md border border-edge bg-raised px-4 py-2.5 text-[0.925rem] leading-relaxed whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

/** The app's own note: a compact, muted, centred pill — an event, not the model speaking. */
export function AppNote({ text }: { text: string }) {
  return (
    <div className="flex justify-center">
      <span className="max-w-[90%] rounded-full border border-edge bg-surface/50 px-3 py-1 text-center text-[11px] text-ink-faint">
        <Markdown text={text} inline />
      </span>
    </div>
  );
}

export function ErrorBubble({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-err/40 bg-err/10 px-4 py-3 text-sm text-err" role="alert">
      <p>{text}</p>
      <p className="mt-1 text-[11px] text-err/80">
        Nothing was changed. You can send the request again, or open the diagnostic logs from Help.
      </p>
    </div>
  );
}

/** The bubble a running job writes into. */
export function LiveBubble({ live }: { live: LiveReply }) {
  const smoothed = useSmoothText(live.text, true);
  if (!liveHasContent(live)) return null;
  const [prefix, tail] = splitStableTail(smoothed);
  const hasBody = smoothed.length > 0;
  return (
    <div className="text-[0.925rem] leading-[1.65]">
      {live.thinking && <ThinkingBlock text={live.thinking} autoExpand={!hasBody} />}
      {live.status && <StatusStrip status={live.status} />}
      <ActivityLines lines={live.activities} />
      {/* The settled prefix is parsed once per paragraph break (Markdown is memoized), not on every frame. */}
      <Markdown text={prefix} />
      {tail ? (
        <span className="streaming-caret whitespace-pre-wrap">{tail}</span>
      ) : (
        !hasBody && !live.status && !live.thinking && <span className="streaming-caret" />
      )}
    </div>
  );
}

function MessageBubble({ message, aside }: { message: Message; aside: Aside | null }) {
  if (message.role === "user") return <UserBubble text={message.text} />;
  if (message.role === "app") return <AppNote text={message.text} />;
  return (
    <div className="text-[0.925rem]">
      <Markdown text={message.text || "…"} />
      {aside && <AsideBlock aside={aside} />}
    </div>
  );
}

/**
 * Memoized on purpose: every streaming update replaces only the live bubble,
 * so a delta re-renders one bubble instead of every bubble.
 */
export default memo(MessageBubble);
