import type { Sample } from "../types";
import { imageSrc } from "../lib/api";
import { pageCount } from "../lib/workbench";
import { PaperCard } from "./PlanCards";

interface Props {
  sample: Sample;
  onZoom: (src: string, title: string) => void;
}

/**
 * "Preview · name": the page count before and after with the growth flag,
 * and the rendered before/after pages; a click opens the zoom.
 */
export default function PreviewCard({ sample, onZoom }: Props) {
  const pages = pageCount(sample);
  const title = `Preview · ${sample.name || sample.source.split(/[\\/]/).pop() || ""}`;
  return (
    <PaperCard
      title={title}
      aside={
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
      }
    >
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
        <div className="mt-2 flex flex-col gap-2">
          {sample.images.map((url, index) => {
            const src = imageSrc(url);
            return (
              <button
                key={`${index}-${url}`}
                type="button"
                onClick={() => onZoom(src, title)}
                title="Enlarge these pages"
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
  );
}
