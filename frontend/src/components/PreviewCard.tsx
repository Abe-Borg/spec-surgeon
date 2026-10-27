import type { Sample } from "../types";
import { imageSrc } from "../lib/api";
import type { Mark } from "../lib/review";
import { pageCount } from "../lib/workbench";
import PaperCard from "./PaperCard";

interface Props {
  sample: Sample;
  /** The user's mark on this preview: looks good, or needs more work (set aside). */
  mark?: Mark;
  /** The marks can be given: the plan awaits approval and nothing runs. */
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

/** The two marks: a green checkmark (looks good) and a red X (needs more work; set aside). */
function ReviewMarks({ name, mark, onMark }: { name: string; mark?: Mark; onMark: (mark: Mark) => void }) {
  const base =
    "flex h-7 w-7 items-center justify-center rounded-full border transition-colors disabled:pointer-events-none";
  return (
    <div role="group" aria-label={`Review ${name}`} className="flex items-center gap-1" data-tour="review-marks" data-capability="preview.marks">
      <button
        type="button"
        aria-pressed={mark === "good"}
        aria-label={`Looks good · ${name}`}
        title="Looks good: its copy is created with the others"
        onClick={() => onMark("good")}
        className={`${base} ${mark === "good" ? "border-ok bg-ok text-white" : "border-ok/50 bg-white/60 text-ok hover:bg-ok/15"}`}
      >
        <Check />
      </button>
      <button
        type="button"
        aria-pressed={mark === "hold"}
        aria-label={`Needs more work · ${name}`}
        title="Needs more work: set it aside; it goes on your to-do list after the others are created"
        onClick={() => onMark("hold")}
        className={`${base} ${mark === "hold" ? "border-err bg-err text-white" : "border-err/50 bg-white/60 text-err hover:bg-err/15"}`}
      >
        <Cross />
      </button>
    </div>
  );
}

/**
 * "Preview · name": the page count before and after with the growth flag,
 * the rendered before/after pages (a click opens the zoom), and the two marks
 * — the green checkmark says the pages look right, the red X sets the
 * document aside for more work after the others are created.
 */
export default function PreviewCard({ sample, mark, canReview, onMark, onZoom }: Props) {
  const pages = pageCount(sample);
  const name = sample.name || sample.source.split(/[\\/]/).pop() || "";
  const title = `Preview · ${name}`;
  return (
    <div data-tour="preview-card">
    <PaperCard
      title={title}
      subtitle={
        mark === "hold"
          ? "Set aside: it goes on your to-do list; the other copies are created first."
          : mark === "good"
            ? "Looks good."
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
                : "This sample did not pass every check; the plan needs another look."
            }
          >
            {sample.passed ? "Checked" : "Did not pass"}
          </span>
          {canReview && <ReviewMarks name={name} mark={mark} onMark={(m) => onMark(sample.source, m)} />}
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
        <div className={`mt-2 flex flex-col gap-2 ${mark === "hold" ? "opacity-70" : ""}`}>
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
