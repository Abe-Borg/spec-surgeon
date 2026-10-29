import type { Recipe, RecipeInputs, SavedRecipe, View } from "../types";
import { fieldRows } from "../lib/recipes";
import {
  changedForms,
  formChips,
  formsChanged,
  keptSteps,
  modeNote,
  runPicks,
  runProblem,
  runStale,
  stepProblem,
  stepsText,
  type RecipeRunState,
} from "../lib/savedRecipes";
import { TOUR_HOLD } from "../lib/tour";
import { primaryBtn } from "./ModalShell";
import { FieldInput } from "./RecipeFields";
import Tip from "./Tip";

interface Props {
  recipe: SavedRecipe;
  /** Safe mode's buttons (GET /api/recipes): a button step's form is that button's. */
  catalog: Recipe[];
  view: Pick<View, "attachments" | "template">;
  busy: boolean;
  run: RecipeRunState;
  onRunChange: (next: RecipeRunState) => void;
  /** Previews the recipe as one plan, no AI call (docs/WEB_API.md, `POST /api/saved-recipes/run`). */
  onPreview: (picks: { skip: boolean; inputs?: RecipeInputs }[]) => void;
  /** The changed button forms kept in the recipe (`POST /api/saved-recipes/change`, action `inputs`): the recipe's new
   *  revision, or null when the save was refused. */
  onSaveForms?: (forms: (RecipeInputs | null)[]) => Promise<string | null>;
  /** Back to every button (Safe mode); the dialog in Default mode has its own ✕. */
  onBack?: () => void;
  tourActive?: boolean;
}

const link =
  "text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:pointer-events-none disabled:opacity-50";

/**
 * A saved recipe's run form: its steps in order, each with what it keeps (a button's form, or the user's words and the
 * plan they approved), whether what it needs is attached, a switch to skip it this time and, for a button, its form to
 * change for this run. Preview sends the whole recipe as one plan: one preview of the copies after every step.
 */
export default function RecipeRunForm({
  recipe,
  catalog,
  view,
  busy,
  run,
  onRunChange,
  onPreview,
  onSaveForms,
  onBack,
  tourActive = false,
}: Props) {
  const problem = runProblem(recipe, run, catalog, view);
  const kept = keptSteps(run);
  const changed = formsChanged(run);
  const held = busy || tourActive;
  const setSkip = (i: number, skip: boolean) =>
    onRunChange({ ...run, skip: run.skip.map((s, j) => (j === i ? skip : s)), open: skip && run.open === i ? null : run.open });
  const setOpen = (i: number | null) => onRunChange({ ...run, open: i });
  const setField = (i: number, name: string, value: RecipeInputs[string]) =>
    onRunChange({ ...run, inputs: run.inputs.map((inputs, j) => (j === i && inputs ? { ...inputs, [name]: value } : inputs)) });

  return (
    <div className="flex flex-col gap-2.5" aria-label={recipe.name} data-capability="recipes.form">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold tracking-wider text-accent uppercase">Recipe · {stepsText(recipe.steps.length)}</p>
          <h2 className="mt-0.5 text-sm font-semibold break-words text-ink">{recipe.name}</h2>
          {recipe.note && <p className="mt-0.5 text-[13px] leading-snug break-words text-ink-dim">{recipe.note}</p>}
        </div>
        {onBack && (
          <Tip tip="Shows every button; this recipe stays as it is saved">
            <button type="button" disabled={busy} onClick={onBack} className={`flex-none ${link}`}>
              All buttons
            </button>
          </Tip>
        )}
      </div>

      <ol className="flex flex-col gap-1.5">
        {recipe.steps.map((step, i) => {
          const skipped = run.skip[i];
          const inputs = run.inputs[i] ?? null;
          const button = step.kind === "button" ? catalog.find((b) => b.id === step.button) : undefined;
          const chips = step.kind === "button" ? formChips(button, inputs ?? undefined) : [];
          const issue = skipped ? "" : stepProblem(step, inputs, catalog, view);
          const open = run.open === i && !skipped && !!button && !!inputs;
          return (
            <li
              key={i}
              className={`flex items-start gap-2.5 rounded-xl border bg-surface px-3 py-2.5 ${open ? "border-accent/60" : "border-edge"} ${skipped ? "opacity-55" : ""}`}
            >
              <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/20 text-[10.5px] font-bold text-accent">
                {i + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className={`text-[13.5px] font-semibold text-ink ${skipped ? "line-through" : ""}`}>{step.label}</p>
                {step.kind === "plan" && (
                  <>
                    <p className="mt-0.5 text-[13px] leading-snug break-words text-ink">“{step.text}”</p>
                    {step.plan.length > 0 && (
                      <ul className="mt-1 list-disc pl-4 text-[12.5px] leading-snug text-ink-dim">
                        {step.plan.map((line, j) => (
                          <li key={j}>{line}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
                {!open && chips.length > 0 && (
                  <p className="mt-0.5 text-[12.5px] leading-snug break-words text-ink-dim">{chips.join(" · ")}</p>
                )}
                {!open && step.kind === "button" && chips.length === 0 && (
                  <p className="mt-0.5 text-[12.5px] text-ink-dim">{button && button.fields.length > 0 ? step.text : "No options"}</p>
                )}
                {step.places.length > 0 && (
                  <p className="mt-0.5 text-[12px] leading-snug break-words text-ink-dim">
                    Places: {step.places.map((w) => `“${w}”`).join(", ")}
                    {step.unguarded ? " (approved in Obliterate mode)" : ""}
                  </p>
                )}
                {skipped && <p className="mt-0.5 text-[12.5px] text-ink-dim">Skipped this time</p>}
                {issue && <p className="mt-1 text-[12.5px] leading-snug text-warn">{issue}</p>}
                {open && button && inputs && (
                  <div className="mt-2 flex flex-col gap-2">
                    {fieldRows(button.fields).map((row) =>
                      row.length === 1 ? (
                        <FieldInput
                          key={row[0].name}
                          recipe={button}
                          field={row[0]}
                          inputs={inputs}
                          view={view}
                          onChange={(name, value) => setField(i, name, value)}
                        />
                      ) : (
                        <div key={row[0].name} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                          {row.map((field) => (
                            <FieldInput
                              key={field.name}
                              recipe={button}
                              field={field}
                              inputs={inputs}
                              view={view}
                              onChange={(name, value) => setField(i, name, value)}
                            />
                          ))}
                        </div>
                      ),
                    )}
                  </div>
                )}
              </div>
              <div className="flex flex-none flex-col items-end gap-1.5">
                {button && button.fields.length > 0 && !skipped && (
                  <button type="button" disabled={busy} onClick={() => setOpen(open ? null : i)} className={link}>
                    {open ? "Done" : "Change"}
                  </button>
                )}
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-dim">
                  <input
                    type="checkbox"
                    className="accent-[var(--color-accent)]"
                    checked={!skipped}
                    disabled={busy}
                    onChange={(e) => setSkip(i, !e.target.checked)}
                  />
                  Run
                </label>
              </div>
            </li>
          );
        })}
      </ol>

      <p className="text-[12.5px] leading-snug text-ink-dim">
        What you change or skip here is for this run; the recipe stays as you saved it.{" "}
        {changed && onSaveForms && !runStale(recipe, run) && (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              void onSaveForms(changedForms(recipe, run, catalog)).then(
                (rev) => rev !== null && onRunChange({ ...run, rev: rev || run.rev, saved: run.inputs.map((i) => (i ? { ...i } : null)) }),
              )
            }
            className={link}
          >
            Save these changes to the recipe
          </button>
        )}
      </p>
      <p className="text-[12px] leading-snug text-ink-faint">{modeNote(recipe.mode)}</p>

      <div className="flex items-center justify-between gap-3 border-t border-edge pt-2.5">
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-ink-dim">
          {problem || `One preview of the copies after ${kept === 1 ? "this step" : kept === 2 ? "both steps" : `all ${kept} steps`}. Nothing changes until you create them.`}
        </p>
        <Tip tip={tourActive ? TOUR_HOLD : busy ? "Wait for the current work to finish" : problem || "Shows the pages before and after every step; no copy is made yet"}>
          <button
            type="button"
            className={primaryBtn}
            disabled={held || !!problem}
            onClick={() => onPreview(runPicks(recipe, run, catalog))}
          >
            {kept === recipe.steps.length ? `Preview ${stepsText(kept)}` : `Preview ${kept} of ${recipe.steps.length}`}
          </button>
        </Tip>
      </div>
    </div>
  );
}
