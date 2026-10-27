import type { OccurrenceDoc } from "../types";
import { occurrenceCounts, placeLabel, UPDATE_TIP, type Unticks } from "../lib/occurrences";
import Markdown from "./Markdown";
import { primaryBtn } from "./ModalShell";
import PaperCard from "./PaperCard";
import Tip from "./Tip";

interface Props {
  docs: OccurrenceDoc[];
  unticks: Unticks;
  /** The ticks can be changed and sent: the plan awaits approval and nothing runs. */
  canUntick: boolean;
  /** The ticks differ from the previewed plan's. */
  pending: boolean;
  onToggle: (key: string) => void;
  onUpdate: () => void;
}

/**
 * "Find and replace, occurrence by occurrence": every occurrence the waiting plan found, per document — every
 * document of the plan, not the sampled ones only — each with a checkbox. Unticked, an occurrence is left as it is;
 * Update the preview makes the plan and pages again with the unticked ones left out, and the one button waits until
 * then. A document the plan set aside lists every occurrence found, so the occurrence the reason names can
 * be unticked and the document then plans.
 */
export default function OccurrencesCard({ docs, unticks, canUntick, pending, onToggle, onUpdate }: Props) {
  const counts = occurrenceCounts(unticks, docs);
  const summary = `${counts.total} occurrence${counts.total === 1 ? "" : "s"} in ${counts.documents} document${counts.documents === 1 ? "" : "s"}`;
  return (
    <div data-tour="occurrences" data-capability="preview.occurrences">
      <PaperCard
        title="Find and replace, occurrence by occurrence"
        subtitle={`${summary}. Untick an occurrence to leave it as it is, then update the preview; the copies change only the ticked ones.`}
        aside={
          <div className="flex items-center gap-2">
            <span className="text-[11px] text-paper-dim tabular-nums">{counts.unticked} unticked</span>
            <Tip tip={canUntick ? (pending ? UPDATE_TIP : "The preview already shows these ticks.") : "The ticks can be changed while the preview waits for you."}>
              <button
                type="button"
                className={primaryBtn}
                disabled={!canUntick || !pending}
                onClick={onUpdate}
              >
                Update the preview
              </button>
            </Tip>
          </div>
        }
      >
        <div className="flex flex-col gap-4">
          {docs.map((doc) => (
            <section key={doc.source} aria-label={doc.name}>
              <p className="text-sm font-medium text-paper-ink">{doc.name}</p>
              {doc.status === "refused" && (
                <p className="mt-0.5 text-[12px] leading-relaxed text-err">
                  Set aside: <Markdown text={doc.reason} paper inline /> Untick the occurrence it names to let the
                  others go ahead.
                </p>
              )}
              <ul className="mt-1.5 divide-y divide-paper-edge">
                {doc.items.map((item) => {
                  const unticked = !!unticks[item.key];
                  return (
                    <li key={item.key} className="py-1.5 first:pt-0 last:pb-0">
                      <label className={`flex cursor-pointer items-start gap-2.5 ${unticked ? "opacity-70" : ""}`}>
                        <input
                          type="checkbox"
                          className="mt-1 h-3.5 w-3.5 shrink-0 accent-accent"
                          checked={!unticked}
                          disabled={!canUntick}
                          onChange={() => onToggle(item.key)}
                          aria-label={`${unticked ? "Replace" : "Leave"} “${item.match}” in ${placeLabel(item)} of ${doc.name}`}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="block text-[11px] text-paper-dim">
                            {placeLabel(item)}
                            {unticked && <span className="ml-1.5 rounded-full border border-paper-edge px-1.5 py-px text-[10px] font-medium">left as it is</span>}
                          </span>
                          <span className="block text-[13px] leading-relaxed text-paper-ink">
                            {item.before}{item.before ? " " : ""}
                            <mark className={`rounded px-0.5 ${unticked ? "bg-paper-edge" : "bg-warn/30"}`}>{item.match}</mark>
                            {item.after ? " " : ""}{item.after}
                            {!unticked && (
                              <span className="text-paper-dim"> → {item.replace === "" ? "(removed)" : `“${item.replace}”`}</span>
                            )}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </PaperCard>
    </div>
  );
}
