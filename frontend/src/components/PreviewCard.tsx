import type { Sample } from "../types";
import { imageSrc } from "../lib/api";
import type { Mark } from "../lib/review";
import { pageCount } from "../lib/workbench";
import { NO_WORDS_CHANGE, WORDS_CHANGE_TITLE, wordChangeLines } from "../lib/wordChanges";
import PaperCard from "./PaperCard";

interface Props {
  sample: Sample;
  /** The user's mark on this preview: it gets its copy (where every preview starts), or it is set aside. */
  mark?: Mark;
  /** The marks can be given: the plan awaits approval, Create is available and nothing runs. */
  canReview: boolean;
  onMark: (source: string, mark: Mark) => void;
  onZoom: (src: string, title: string) => void;
}

function Check() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="m5 12 5 5L20 7" />
    </svg>
  );
}

function Cross() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

/** The two marks: a green checkmark (it gets its copy: filled from the start) and a red X (needs more work; set
 *  aside). The checkmark, or the X a second time, takes the X back. */
function ReviewMarks({ name, mark, onMark }: { name: string; mark: Mark; onMark: (mark: Mark) => void }) {
  const base =
    "flex h-7 w-7 items-center justify-center rounded-full border transition-colors disabled:pointer-events-none";
  const held = mark === "hold";
  return (
    <div role="group" aria-label={`Review ${name}`} className="flex items-center gap-1" data-tour="review-marks" data-capability="preview.marks">
      <button
        type="button"
        aria-pressed={!held}
        aria-label={`Gets a copy · ${name}`}
        title={held ? "Take back the red X: it gets its copy with the others" : GETS_COPY_TITLE}
        onClick={() => onMark("good")}
        className={`${base} ${held ? "border-ok/50 bg-white/60 text-ok hover:bg-ok/15" : "border-ok bg-ok text-white"}`}
      >
        <Check />
      </button>
      <button
        type="button"
        aria-pressed={held}
        aria-label={`Set aside · ${name}`}
        title={held ? "Take back the red X: it gets its copy with the others" : SET_ASIDE_TITLE}
        onClick={() => onMark("hold")}
        className={`${base} ${held ? "border-err bg-err text-white" : "border-err/50 bg-white/60 text-err hover:bg-err/15"}`}
      >
        <Cross />
      </button>
    </div>
  );
}

/**
 * The words a custom method's copy adds and removes, under the sample: the independent checker's reading of the
 * copy against its original (`samples[].text_changes`), "Words removed … · Words added …" per paragraph, or that
 * no words change. An ordinary plan's sample carries none (the chat's sentences say what changes) and shows nothing.
 */
function WordChanges({ sample, name }: { sample: Sample; name: string }) {
  const lines = wordChangeLines(sample);
  if (lines === null) return null;
  if (lines.length === 0) {
    return (
      <p className="mb-1 text-xs leading-relaxed text-paper-dim" data-words="none">
        {NO_WORDS_CHANGE}
      </p>
    );
  }
  return (
    <div className="mb-2 text-xs leading-relaxed" data-words="changed">
      <p className="font-medium text-paper-ink">{WORDS_CHANGE_TITLE}</p>
      <ul className="mt-1 space-y-1" aria-label={`Words changed in ${name}`}>
        {lines.map((line, index) => (
          <li key={`${index}-${line.where}`}>
            <span className="text-paper-dim">{line.where}</span>
            {line.note && <span className="text-paper-dim"> ({line.note})</span>}
            {line.removed && (
              <>
                {" — "}
                <span className="text-err">Words removed</span> {line.removed}
              </>
            )}
            {line.added && (
              <>
                {line.removed ? " · " : " — "}
                <span className="text-ok">Words added</span> {line.added}
              </>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The checkmark's hover text while the preview gets its copy, as every preview does until it carries the X. */
export const GETS_COPY_TITLE = "Gets a copy: every preview does until you mark it with the red X";
/** The X's hover text while the preview gets its copy. */
export const SET_ASIDE_TITLE =
  "Needs more work: set it aside; it gets no copy now and goes on your to-do list after the others are created";
/** The subtitle of a preview that gets its copy: where every preview starts. */
export const GETS_COPY_SUBTITLE = "Gets a copy when you create the edited copies.";
/** The subtitle of a preview marked with the red X. */
export const HELD_SUBTITLE = "Set aside: it goes on your to-do list; the other copies are created first.";

/** The subtitle of a preview the server set aside: it did not pass while another did. */
export const SET_ASIDE_SUBTITLE =
  "Left untouched: it did not pass its checks, so no copy of it is made. The other documents' copies can still be created.";

/**
 * "Preview · name": the page count before and after with the growth flag,
 * the words a custom method's copy adds and removes (or that none change),
 * the rendered before/after pages (a click opens the zoom), and the two marks
 * — the green checkmark, filled from the start, says it gets its copy; the
 * red X sets the document aside for more work after the others are created,
 * and the checkmark or a second X takes it back. A preview the
 * server set aside (it did not pass while another did) keeps its pages and
 * its failed checks, so you see why, but carries no marks: no copy of it is
 * made, and it is listed under Left untouched.
 */
export default function PreviewCard({ sample, mark, canReview, onMark, onZoom }: Props) {
  const pages = pageCount(sample);
  const name = sample.name || sample.source.split(/[\\/]/).pop() || "";
  const title = `Preview · ${name}`;
  const aside = sample.set_aside === true;
  return (
    <div data-tour="preview-card">
    <PaperCard
      title={title}
      subtitle={
        aside
          ? SET_ASIDE_SUBTITLE
          : mark === "hold"
            ? HELD_SUBTITLE
            : canReview
              ? GETS_COPY_SUBTITLE
              : undefined
      }
      aside={
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full border px-2 py-px text-[10px] font-medium tracking-wide uppercase ${
              sample.passed ? "border-ok/50 text-ok" : "border-err/50 text-err"
            }`}
            title={
              sample.passed
                ? "This sample was applied and verified in memory; the source was not touched."
                : aside
                  ? "This sample did not pass every check, so no copy of it is made; the other documents can still get theirs."
                  : "This sample did not pass every check; the plan needs another look."
            }
          >
            {sample.passed ? "Checked" : "Did not pass"}
          </span>
          {canReview && !aside && <ReviewMarks name={name} mark={mark ?? "good"} onMark={(m) => onMark(sample.source, m)} />}
        </div>
      }
    >
      {!sample.passed && (sample.failures ?? []).length > 0 && (
        <ul className="mb-2 space-y-1 text-xs leading-relaxed text-err" aria-label={`Failed checks for ${name}`}>
          {sample.failures!.map((failure) => (
            <li key={failure.check}>{failure.reason} <span className="text-paper-dim">({failure.check})</span></li>
          ))}
        </ul>
      )}
      <WordChanges sample={sample} name={name} />
      {sample.geometry?.note && (
        <p className={`mb-1 text-xs leading-relaxed ${sample.geometry.status === "FAIL" ? "text-err" : "text-paper-dim"}`}>
          {sample.geometry.note}
        </p>
      )}
      {pages.text && (
        <p className="text-xs text-paper-dim tabular-nums">
          {pages.text.replace(/ \(.*\)$/, "")}
          {pages.flag && (
            <span
              className={`ml-1.5 rounded-full border px-1.5 py-px text-[10px] font-medium ${
                pages.grew ? "border-warn/60 text-warn" : "border-paper-edge text-paper-dim"
              }`}
              title={pages.grew ? "The edited copy has more pages than the original" : "The page count changed"}
            >
              {pages.flag}
            </span>
          )}
        </p>
      )}
      {sample.images.length > 0 ? (
        <div className={`mt-2 flex flex-col gap-2 ${mark === "hold" || aside ? "opacity-70" : ""}`}>
          {sample.images.map((url, index) => {
            const src = imageSrc(url);
            return (
              <button
                key={`${index}-${url}`}
                type="button"
                onClick={() => onZoom(src, title)}
                title="Enlarge these pages"
                data-capability="preview.pages"
                className="group overflow-hidden rounded-lg border border-paper-edge bg-white transition-colors hover:border-accent"
              >
                <img src={src} alt={`Before and after pages of ${sample.name}`} className="block w-full" loading="lazy" />
              </button>
            );
          })}
          <p className="text-[11px] text-paper-faint">Before on the left, after on the right. Click to enlarge.</p>
        </div>
      ) : (
        <p className="mt-2 text-xs text-paper-dim">
          Page appearance was not checked for this document; open the edited copy in Word.
        </p>
      )}
    </PaperCard>
    </div>
  );
}
