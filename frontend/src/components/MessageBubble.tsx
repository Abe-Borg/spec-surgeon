// Adapted from Build-a-Spec's frontend/src/components/MessageBubble.tsx (copy-based reuse).
import { memo, useLayoutEffect, useRef, useState } from "react";
import type { Message } from "../types";
import type { Aside, LiveReply, Step } from "../lib/view";
import { noteShape, statusLabel, stepsLabel } from "../lib/chat";
import { freshFrom } from "../lib/reveal";
import { useReveal } from "../lib/useReveal";
import Collapse from "./Collapse";
import Markdown from "./Markdown";
import RevealMarkdown from "./RevealMarkdown";

/** The three pulsing dots of a working line. */
function Dots() {
  return (
    <span className="status-dots" aria-hidden="true">
      <span />
      <span />
      <span />
    </span>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={`chev ${open ? "is-open" : ""}`} width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <path d="M3.5 2 6.8 5 3.5 8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * The line above a reply that names its making. While the job works it is the "app is not frozen" line: pulsing
 * dots and what the app is doing right now, each new sentence easing in over the last. Otherwise it is the folded
 * summary ("3 steps · thinking") that opens the log.
 */
function WorkLine({
  working,
  label,
  open,
  expandable,
  onToggle,
}: {
  working: boolean;
  label: string;
  open: boolean;
  expandable: boolean;
  onToggle: () => void;
}) {
  const inner = (
    <>
      <span className="work-icon">{working ? <Dots /> : <Chevron open={open} />}</span>
      <span className="work-label-wrap" aria-live="polite">
        <span key={label} className={`work-label ${working ? "is-working" : ""}`}>
          {label}
        </span>
      </span>
      {working && expandable && <Chevron open={open} />}
    </>
  );
  if (!expandable) return <div className="work-head">{inner}</div>;
  return (
    <button type="button" className="work-head is-button" aria-expanded={open} onClick={onToggle}>
      {inner}
    </button>
  );
}

/** The thinking summary, its words easing in as they arrive. While the model thinks, a short window that follows
 *  the newest line, the older ones fading out at its top. */
function ThinkingText({ text, live }: { text: string; live: boolean }) {
  const reveal = useReveal(text);
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (live) el.scrollTop = el.scrollHeight;
    // The top fades only when there is more above it than the window shows.
    el.dataset.clipped = el.scrollHeight > el.clientHeight + 1 ? "1" : "0";
  }, [reveal.shown, live]);
  const { shown, marks, times } = reveal;
  // Settled words are one text; only the pieces still fading are spans (keyed by where they begin).
  const fresh = freshFrom(marks, times, performance.now(), shown.length);
  const pieces = [];
  for (let k = 0; k < marks.length; k += 1) {
    if (marks[k] < fresh) continue;
    const end = k + 1 < marks.length ? marks[k + 1] : shown.length;
    pieces.push(
      <span key={marks[k]} className="reveal">
        {shown.slice(marks[k], end)}
      </span>,
    );
  }
  return (
    <div ref={ref} className={`thinking-text ${live ? "is-live" : ""}`}>
      {shown.slice(0, Math.min(fresh, shown.length))}
      {pieces}
    </div>
  );
}

/** The log of a reply's making: one quiet line per activity ("↳ Reading the template's header"), the words said
 *  along the way, and the thinking summary. */
function WorkLog({ steps, thinking, live }: { steps: { step: Step; index: number }[]; thinking: string; live: boolean }) {
  return (
    <div className="work-log">
      {steps.length > 0 && (
        <ul className="work-steps">
          {steps.map(({ step, index }) =>
            step.kind === "activity" ? (
              <li key={index} className="work-step">
                <span aria-hidden="true" className="not-italic">
                  ↳
                </span>
                <span>{step.text}</span>
              </li>
            ) : (
              <li key={index} className="work-said">
                <Markdown text={step.text} />
              </li>
            ),
          )}
        </ul>
      )}
      {thinking && <ThinkingText text={thinking} live={live} />}
    </div>
  );
}

/**
 * An assistant reply, from the job's first status to the finished message, in one bubble: the line naming its
 * making on top, the log under it (open while the model thinks with no words yet, folded once words come), the
 * words earlier model calls said along the way, then the reply. Words come on at a reading pace and fade in; the
 * finished message carries on from the words already shown, and what belonged only to the work folds away. A
 * bubble whose job ended without a message (`leaving`) folds away whole.
 */
function Reply({
  text,
  live,
  aside,
  leaving = false,
}: {
  text: string;
  live: LiveReply | null;
  aside: Aside | null;
  leaving?: boolean;
}) {
  const working = live !== null;
  const steps = live ? live.steps : (aside?.steps ?? []);
  const thinking = (live ? live.thinking : (aside?.thinking ?? "")).trim();
  const status = live?.status ?? null;
  const reveal = useReveal(text);
  const [toggled, setToggled] = useState<boolean | null>(null);

  const said = steps.filter((step) => step.kind === "said");
  const hasWords = reveal.shown.length > 0;
  // While the job works, the words said along the way stand above the reply; afterwards they are in the log.
  const logged = steps.map((step, index) => ({ step, index })).filter(({ step }) => !working || step.kind === "activity");
  const loggable = logged.length > 0 || thinking !== "";
  const busyLine = working && status !== null;
  // Opens by itself for the thinking alone: the working line already names the activity of the moment.
  const autoOpen = working && !hasWords && said.length === 0 && thinking !== "";
  const open = loggable && (toggled ?? autoOpen);
  // The caret follows the words while they are written; before any word, the working line says what happens.
  const caret = reveal.flowing || (working && status === null && hasWords);

  if (!busyLine && !loggable && !hasWords && !(working && said.length > 0)) return null;
  return (
    <Collapse open={!leaving}>
      <div className="reply chat-in text-[0.925rem]">
        <Collapse open={busyLine || loggable}>
          <div className="pb-1.5">
            <WorkLine
              working={busyLine}
              label={busyLine ? statusLabel(status) : stepsLabel(steps, thinking)}
              open={open}
              expandable={loggable}
              onToggle={() => setToggled(!open)}
            />
          </div>
        </Collapse>
        <Collapse open={open}>
          <div className="pb-2.5">
            <WorkLog steps={logged} thinking={thinking} live={working} />
          </div>
        </Collapse>
        <Collapse open={working && said.length > 0} enter={false}>
          <div className="said-words">
            {said.map((step, index) => (
              <Markdown key={index} text={step.text} className="said-in" />
            ))}
          </div>
        </Collapse>
        <RevealMarkdown text={reveal.shown} marks={reveal.marks} times={reveal.times} caret={caret} />
      </div>
    </Collapse>
  );
}

export function UserBubble({ text }: { text: string }) {
  return (
    <div className="chat-in flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-md border border-edge bg-raised px-4 py-2.5 text-[0.925rem] leading-relaxed whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

/**
 * The app's own note: a compact, muted, centred pill — an event, not the model speaking. A note of several
 * paragraphs (the words a custom method changes, said before the approval button) is a block the user reads, not
 * a pill: its paragraphs stay apart and its words are quoted in full.
 */
export function AppNote({ text }: { text: string }) {
  if (noteShape(text) === "block") {
    return (
      <div className="chat-in rounded-xl border border-edge bg-surface/50 px-4 py-2.5 text-xs leading-relaxed text-ink-dim" data-note="block">
        <Markdown text={text} />
      </div>
    );
  }
  return (
    <div className="chat-in flex justify-center">
      <span className="max-w-[90%] rounded-full border border-edge bg-surface/50 px-3 py-1 text-center text-[11px] text-ink-faint">
        <Markdown text={text} inline />
      </span>
    </div>
  );
}

export function ErrorBubble({ text }: { text: string }) {
  return (
    <div className="chat-in rounded-xl border border-err/40 bg-err/10 px-4 py-3 text-sm text-err" role="alert">
      <p>{text}</p>
      <p className="mt-1 text-[11px] text-err/80">
        Nothing was changed. You can send the request again, or open the diagnostic logs from About.
      </p>
    </div>
  );
}

/**
 * One entry of the conversation: a message, or the reply a running job writes. The live reply and the message it
 * becomes are the same entry (the chat keys them alike, lib/chat.ts), so the reply carries on in its bubble.
 */
function ChatMessage({
  message,
  live,
  aside,
  leaving = false,
}: {
  message: Message | null;
  live: LiveReply | null;
  aside: Aside | null;
  leaving?: boolean;
}) {
  if (live) return <Reply text={live.text} live={live} aside={null} leaving={leaving} />;
  if (!message) return null;
  if (message.role === "user") return <UserBubble text={message.text} />;
  if (message.role === "app") return <AppNote text={message.text} />;
  return <Reply text={message.text || "…"} live={null} aside={aside} />;
}

/**
 * Memoized on purpose: every streaming update replaces only the live entry,
 * so a delta re-renders one bubble instead of every bubble.
 */
export default memo(ChatMessage);
