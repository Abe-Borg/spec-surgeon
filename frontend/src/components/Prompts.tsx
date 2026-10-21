// Adapted from Build-a-Spec's frontend/src/components/SuggestedPrompts.tsx (copy-based reuse).
import type { Choice } from "../types";
import Markdown from "./Markdown";

interface Props {
  questions: string[];
  choices: Choice[];
  chips: string[];
  busy: boolean;
  /** A choice carries approval semantics: picking one may approve its exact words. */
  onChoose: (choice: Choice) => void;
  /** A chip is one of the model's suggested replies, sent as the user's decision (never as their wording). */
  onSend: (text: string, chip?: boolean) => void;
}

/**
 * The question block under the latest assistant message: the question(s),
 * the choices as buttons — a choice's exact wording shown beneath it — and
 * the one-tap reply chips. Everything here is cleared the moment the user
 * answers (the reducer drops it on `job_started`). The questions and the
 * choices' labels are the model's text, rendered as markdown.
 */
export default function Prompts({ questions, choices, chips, busy, onChoose, onSend }: Props) {
  return (
    <div className="card-in rounded-2xl border border-edge bg-surface/60 px-4 py-3" role="group" aria-label="One thing to clarify">
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
                disabled={busy}
                title={choice.detail || (choice.words.length ? "Picking this approves exactly the words beneath it" : "")}
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
              {choice.words.length > 0 && (
                <div className="mt-1 ml-3 border-l-2 border-edge pl-3 text-[12px] text-ink-dim">
                  <span className="text-[10px] font-medium tracking-wide text-ink-faint uppercase">
                    Exact wording:
                  </span>
                  {choice.words.map((word, i) => (
                    <p key={`${i}-${word}`} className="mt-0.5 whitespace-pre-wrap">
                      “{word}”
                    </p>
                  ))}
                </div>
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
          {chips.map((chip) => (
            <button
              key={chip}
              type="button"
              onClick={() => onSend(chip, true)}
              disabled={busy}
              title={chip}
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
