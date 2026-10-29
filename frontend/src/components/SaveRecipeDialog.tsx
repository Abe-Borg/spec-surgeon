import { useMemo, useState } from "react";
import type { DoneStep, Recipe } from "../types";
import { formChips, modeNote, modeOfSteps, needsText, savableRuns } from "../lib/savedRecipes";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";

interface Props {
  /** What you've done so far, oldest first (the view's `working.done`). */
  done: DoneStep[];
  /** Safe mode's buttons, to say what a button step's form holds. */
  catalog: Recipe[];
  busy: boolean;
  onSave: (name: string, note: string, runs: string[]) => Promise<boolean>;
  onClose: () => void;
}

const inputClass =
  "w-full rounded-lg border border-edge bg-bg px-3 py-2 text-sm text-ink outline-none transition-colors " +
  "placeholder:text-ink-faint focus:border-accent";

/**
 * Save as a recipe: a name, what it is for, and the runs of What you've done so far to keep, oldest first (every run a
 * recipe can keep is ticked; one it cannot keep says why and cannot be ticked). Each run shows what the recipe keeps of
 * it: a button and what its form held, or the user's words and the plan they approved, with the words it places.
 */
export default function SaveRecipeDialog({ done, catalog, busy, onSave, onClose }: Props) {
  const savable = useMemo(() => savableRuns(done), [done]);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [ticked, setTicked] = useState<Set<string>>(() => new Set(savable.map((d) => d.id as string)));
  const [saving, setSaving] = useState(false);
  const chosen = done.filter((d) => d.id && ticked.has(d.id));
  const steps = chosen.flatMap((d) => d.steps ?? []);
  const mode = modeOfSteps(steps);
  const places = [...new Set(steps.flatMap((s) => s.places))];
  const reason = !name.trim() ? "Type a name for the recipe." : steps.length === 0 ? "Tick at least one change to save." : "";
  const toggle = (id: string) =>
    setTicked((t) => {
      const next = new Set(t);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const save = async () => {
    if (reason || saving) return;
    setSaving(true);
    try {
      if (await onSave(name.trim(), note.trim(), chosen.map((d) => d.id as string))) onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalShell title="Save as a recipe" onClose={onClose} wide>
      <form
        className="flex flex-col gap-3.5 text-sm"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label className="block">
          <span className="mb-1.5 block text-[12.5px] font-semibold text-ink">Name</span>
          <input className={inputClass} value={name} maxLength={120} placeholder="Division 21 · Issued for Construction" onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-[12.5px] font-semibold text-ink">
            What it’s for <span className="font-normal text-ink-dim">(optional)</span>
          </span>
          <input className={inputClass} value={note} maxLength={300} onChange={(e) => setNote(e.target.value)} />
        </label>

        <div>
          <div className="mb-2 flex items-baseline justify-between gap-3">
            <span className="text-[12.5px] font-semibold text-ink">Steps, in the order you ran them</span>
            <span className="text-xs text-ink-dim">
              {chosen.length} of {savable.length} ticked
            </span>
          </div>
          <ol className="flex flex-col gap-1.5">
            {done.map((run, i) => {
              const can = !!run.id && (run.steps ?? []).length > 0;
              const on = can && ticked.has(run.id as string);
              return (
                <li key={run.id ?? i} className={`flex items-start gap-3 rounded-xl border border-edge bg-raised px-3 py-2.5 ${can ? "" : "opacity-60"}`}>
                  <input
                    type="checkbox"
                    className="mt-0.5 h-4 w-4 flex-none accent-[var(--color-accent)]"
                    checked={on}
                    disabled={!can}
                    onChange={() => run.id && toggle(run.id)}
                    aria-label={`Keep “${run.request}”`}
                  />
                  <span className="mt-0.5 w-4 flex-none text-xs font-semibold text-ink-dim">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    {run.name ? (
                      <p className="text-[13.5px] font-medium break-words text-ink">
                        <span className="mr-1.5 rounded-full border border-accent/40 px-1.5 py-px text-[10.5px] font-semibold text-accent">Recipe</span>
                        {run.name}
                      </p>
                    ) : (
                      <p className="text-[13.5px] font-medium break-words text-ink">{(run.steps ?? [])[0]?.kind === "plan" ? `“${run.request}”` : run.request}</p>
                    )}
                    {!can && <p className="mt-0.5 text-[12.5px] leading-snug text-ink-dim">{run.why_not}</p>}
                    {(run.steps ?? []).map((step, j) => {
                      const button = step.kind === "button" ? catalog.find((b) => b.id === step.button) : undefined;
                      const chips = [
                        ...(run.name ? [step.kind === "button" ? step.text : `“${step.text}”`] : []),
                        ...(step.kind === "button" ? formChips(button, step.inputs) : step.plan),
                      ];
                      const need = needsText(step.needs);
                      return (
                        <div key={j} className={run.name ? "mt-1 border-l-2 border-accent/30 pl-2.5" : "mt-1"}>
                          <div className="flex flex-wrap gap-1.5">
                            {need && (
                              <span className="rounded-full border border-warn/50 bg-warn/10 px-2 py-px text-[11.5px] text-warn">
                                {need[0].toUpperCase() + need.slice(1)} when you run it
                              </span>
                            )}
                            {chips.map((chip, k) => (
                              <span key={k} className="rounded-full border border-edge bg-surface px-2 py-px text-[11.5px] break-words text-ink-dim">
                                {chip}
                              </span>
                            ))}
                            {step.kind === "plan" && (
                              <span className="rounded-full border border-ok/50 bg-ok/10 px-2 py-px text-[11.5px] text-ok">Runs as approved · no AI</span>
                            )}
                            {step.unguarded && (
                              <span className="rounded-full border border-err/50 bg-err/10 px-2 py-px text-[11.5px] text-err">Approved in Obliterate mode</span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </li>
              );
            })}
          </ol>
        </div>

        <p className="text-[12.5px] leading-relaxed text-ink-dim">
          {places.length > 0 ? (
            <>
              Every word this recipe places is one approved when you ran it: {places.map((w) => `“${w}”`).join(", ")}.{" "}
            </>
          ) : null}
          It keeps the buttons and what you filled in, or your words and the plans you approved — never your documents, the
          template or a picture. Kept on this computer, beside your settings. {steps.length > 0 ? modeNote(mode) : ""}
        </p>

        <div className="flex items-center justify-end gap-2 border-t border-edge pt-3">
          {reason && <p className="mr-auto text-xs text-ink-dim">{reason}</p>}
          <button type="button" className={quietBtn} onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className={primaryBtn} disabled={!!reason || saving || busy} data-capability="recipes.save">
            Save recipe
          </button>
        </div>
      </form>
    </ModalShell>
  );
}
