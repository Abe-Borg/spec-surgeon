// Adapted from Build-a-Spec's frontend/src/components/SuggestedPrompts.tsx (copy-based reuse).
import { useId, useState } from "react";
import { checkWording } from "../lib/wording";
import type { Choice } from "../types";
import Markdown from "./Markdown";

/** Each choice and chip lands a beat after the one before it, once the card has risen in. */
function stagger(index: number) {
  return { animationDelay: `${120 + Math.min(index, 8) * 45}ms` };
}

interface Props {
  questions: string[];
  choices: Choice[];
  chips: string[];
  busy: boolean;
  /** A choice carries approval semantics: picking one may approve its exact words — or, ``wording``, the user's edit
   *  of its one drafted wording, which is then approved instead of the draft. */
  onChoose: (choice: Choice, wording?: string) => void;
  /** A chip is one of the model's suggested replies, sent as the user's decision (never as their wording). */
  onSend: (text: string, chip?: boolean) => void;
}

/** Claude's drafted wording in a box the user may change; "Use this wording" picks the choice with it. */
function WordingEditor({ choice, busy, onChoose, onCancel }: {
  choice: Choice;
  busy: boolean;
  onChoose: (choice: Choice, wording?: string) => void;
  onCancel: () => void;
}) {
  const draft = choice.words[0] ?? "";
  const [text, setText] = useState(draft);
  const id = useId();
  const check = checkWording(draft, text);
  const use = () => {
    if (!check.error && !busy) onChoose(choice, check.edited ?? undefined);
  };
  return (
    <div className="mt-1 ml-3 border-l-2 border-accent/60 pl-3">
      <label htmlFor={id} className="text-[10px] font-medium tracking-wide text-ink-faint uppercase">
        Your wording
      </label>
      <textarea
        id={id}
        rows={2}
        value={text}
        autoFocus
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            use();
          }
        }}
        className="mt-1 w-full resize-y rounded-lg border border-edge bg-raised px-2.5 py-1.5 text-sm text-ink outline-none transition-colors focus:border-accent"
      />
      <p className={`mt-0.5 text-[11px] ${check.error ? "text-err" : "text-ink-faint"}`}>
        {check.error || "Picking approves exactly these words; the draft is not used unless you leave it as it is."}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={use}
          disabled={busy || Boolean(check.error)}
          className="rounded-lg bg-accent px-3 py-1 text-xs font-medium text-on-accent transition-colors hover:bg-accent-hover disabled:opacity-40"
        >
          Use this wording
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-2 py-1 text-xs text-ink-dim transition-colors hover:text-ink"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

/**
 * The question block under the latest assistant message: the question(s),
 * the choices as buttons — a choice's exact wording shown beneath it — and
 * the one-tap reply chips. Everything here is cleared the moment the user
 * answers (the reducer drops it on `job_started`). The questions and the
 * choices' labels are the model's text, rendered as markdown.
 */
export default function Prompts({ questions, choices, chips, busy, onChoose, onSend }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  return (
    <div className="chat-in rounded-2xl border border-edge bg-surface/60 px-4 py-3" role="group" aria-label="One thing to clarify">
      {questions.length > 0 && (
        <div className="mb-3">
          <p className="text-[11px] font-medium tracking-wide text-ink-faint uppercase">
            One thing to clarify
          </p>
          {questions.map((q, index) => (
            <Markdown key={`${index}-${q}`} text={q} className="mt-1 text-[0.925rem] leading-relaxed text-ink" />
          ))}
        </div>
      )}
      {choices.length > 0 && (
        <div className="flex flex-col gap-2">
          {choices.map((choice, index) => (
            <div key={choice.id || `${index}-${choice.label}`}>
              <button
                type="button"
                onClick={() => onChoose(choice)}
                disabled={busy || editing === choice.id}
                title={
                  editing === choice.id
                    ? "You are editing its wording: press Use this wording below, or Cancel to pick the draft as offered"
                    : choice.detail || (choice.words.length ? "Picking this approves exactly the words beneath it" : "")
                }
                style={stagger(index)}
                className="prompt-chip-in w-full rounded-xl border border-edge bg-raised px-4 py-2.5 text-left text-sm text-ink transition-colors hover:border-accent/70 hover:bg-raised/80 disabled:pointer-events-none disabled:opacity-40"
              >
                <span className="mr-2 text-ink-faint tabular-nums">{index + 1}.</span>
                <Markdown text={choice.label} inline />
                {choice.detail && (
                  <span className="mt-0.5 block text-[11px] text-ink-faint">
                    <Markdown text={choice.detail} inline />
                  </span>
                )}
              </button>
              {editing === choice.id && choice.editable ? (
                <WordingEditor choice={choice} busy={busy} onChoose={onChoose} onCancel={() => setEditing(null)} />
              ) : (
                choice.words.length > 0 && (
                  <div className="mt-1 ml-3 border-l-2 border-edge pl-3 text-[12px] text-ink-dim">
                    <span className="text-[10px] font-medium tracking-wide text-ink-faint uppercase">
                      Exact wording:
                    </span>
                    {choice.editable && (
                      <button
                        type="button"
                        onClick={() => setEditing(choice.id)}
                        disabled={busy}
                        className="ml-2 text-[11px] text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:opacity-40"
                      >
                        Edit wording
                      </button>
                    )}
                    {choice.words.map((word, i) => (
                      <p key={`${i}-${word}`} className="mt-0.5 whitespace-pre-wrap">
                        “{word}”
                      </p>
                    ))}
                  </div>
                )
              )}
            </div>
          ))}
        </div>
      )}
      {chips.length > 0 && (
        <div
          role="group"
          aria-label="Suggested replies"
          className={`flex flex-wrap items-center gap-2 ${choices.length > 0 || questions.length > 0 ? "mt-3" : ""}`}
        >
          <span aria-hidden className="text-xs text-ink-faint">
            ✦
          </span>
          {chips.map((chip, index) => (
            <button
              key={chip}
              type="button"
              onClick={() => onSend(chip, true)}
              disabled={busy}
              title={chip}
              style={stagger(choices.length + index)}
              className="prompt-chip-in max-w-full truncate rounded-full border border-accent/50 bg-accent/10 px-3 py-1 text-xs text-ink transition-colors hover:border-accent hover:bg-accent/15 disabled:pointer-events-none disabled:opacity-40"
            >
              {chip}
            </button>
          ))}
        </div>
      )}
      <p className="mt-3 text-[11px] text-ink-faint">
        Pick an answer, or describe what you want in your own words below.
      </p>
    </div>
  );
}
