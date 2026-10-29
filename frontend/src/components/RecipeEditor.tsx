import type { Recipe, RecipeInputs, SavedRecipe, View } from "../types";
import { fieldRows } from "../lib/recipes";
import {
  MODE_BADGE,
  attachedFields,
  editChanged,
  editProblem,
  editableFields,
  editedSteps,
  formChips,
  modeNote,
  modeOfSteps,
  moveStep,
  needsText,
  setRemoved,
  type RecipeEditState,
} from "../lib/savedRecipes";
import { FieldInput } from "./RecipeFields";

interface Props {
  recipe: SavedRecipe;
  /** Safe mode's buttons (GET /api/recipes): a button step's form is that button's. */
  catalog: Recipe[];
  view: Pick<View, "attachments" | "template">;
  edit: RecipeEditState;
  busy: boolean;
  onChange: (next: RecipeEditState) => void;
  onSave: () => void;
  onCancel: () => void;
}

const inputClass =
  "w-full rounded-lg border border-edge bg-bg px-2.5 py-1.5 text-sm text-ink outline-none transition-colors focus:border-accent";

const small =
  "rounded-lg border border-edge bg-raised px-2.5 py-1 text-xs text-ink transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";

const icon =
  "rounded-md border border-edge bg-raised px-1.5 py-px text-xs text-ink-dim transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-30";

const link =
  "text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:pointer-events-none disabled:opacity-50";

/**
 * Manage → Edit: a recipe's name, what it is for, and its steps — moved up or down, removed (greyed, put back with one
 * click until saved) or, for a button, given another form. A step saved from a request keeps the words and plan you
 * approved; it is moved or removed whole. Nothing changes until Save, and every later run is previewed as before.
 */
export default function RecipeEditor({ recipe, catalog, view, edit, busy, onChange, onSave, onCancel }: Props) {
  const problem = editProblem(edit, recipe, catalog);
  const changed = editChanged(edit, recipe);
  const kept = editedSteps(edit, recipe);
  const mode = kept.length ? modeOfSteps(kept) : recipe.mode;
  const needs = needsText([...new Set(kept.flatMap((s) => s.needs))]);
  const setField = (i: number, name: string, value: RecipeInputs[string]) =>
    onChange({ ...edit, steps: edit.steps.map((s, j) => (j === i && s.inputs ? { ...s, inputs: { ...s.inputs, [name]: value } } : s)) });

  return (
    <form
      className="flex flex-col gap-2.5"
      aria-label={`Edit ${recipe.name}`}
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem && changed) onSave();
      }}
    >
      <label className="block">
        <span className="mb-1 block text-[12px] font-medium text-ink-dim">Name</span>
        <input className={inputClass} value={edit.name} maxLength={120} onChange={(e) => onChange({ ...edit, name: e.target.value })} />
      </label>
      <label className="block">
        <span className="mb-1 block text-[12px] font-medium text-ink-dim">What it’s for</span>
        <input className={inputClass} value={edit.note} maxLength={300} onChange={(e) => onChange({ ...edit, note: e.target.value })} />
      </label>

      <div>
        <span className="mb-1 block text-[12px] font-medium text-ink-dim">Steps, in the order they run</span>
        <ol className="flex flex-col gap-1.5">
          {edit.steps.map((entry, i) => {
            const step = recipe.steps[entry.from];
            if (!step) return null;
            const button = step.kind === "button" ? catalog.find((b) => b.id === step.button) : undefined;
            const fields = button ? editableFields(button) : [];
            const open = edit.open === i && !entry.removed && !!button && !!entry.inputs && fields.length > 0;
            const chips = step.kind === "button" ? formChips(button, entry.inputs ?? undefined) : [];
            const number = edit.steps.slice(0, i + 1).filter((s) => !s.removed).length;
            return (
              <li
                key={entry.from}
                className={`flex items-start gap-2.5 rounded-xl border bg-surface px-3 py-2 ${open ? "border-accent/60" : "border-edge"} ${entry.removed ? "opacity-55" : ""}`}
              >
                <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/20 text-[10.5px] font-bold text-accent">
                  {entry.removed ? "–" : number}
                </span>
                <div className="min-w-0 flex-1">
                  <p className={`text-[13px] font-semibold break-words text-ink ${entry.removed ? "line-through" : ""}`}>
                    {step.kind === "button" ? step.label : `“${step.text}”`}
                  </p>
                  {step.kind === "plan" && !entry.removed && (
                    <p className="mt-0.5 text-[12px] leading-snug text-ink-dim">
                      Runs as you approved it{step.places.length ? `; places ${step.places.map((w) => `“${w}”`).join(", ")}` : ""}. To change
                      it, ask again and save that run.
                    </p>
                  )}
                  {!open && !entry.removed && chips.length > 0 && (
                    <p className="mt-0.5 text-[12px] leading-snug break-words text-ink-dim">{chips.join(" · ")}</p>
                  )}
                  {entry.removed && <p className="mt-0.5 text-[12px] text-ink-dim">Removed when you save</p>}
                  {open && button && entry.inputs && (
                    <div className="mt-2 flex flex-col gap-2">
                      {fieldRows(fields).map((row) => (
                        <div key={row[0].name} className={row.length > 1 ? "grid grid-cols-2 gap-2 sm:grid-cols-4" : ""}>
                          {row.map((field) => (
                            <FieldInput
                              key={field.name}
                              recipe={button}
                              field={field}
                              inputs={entry.inputs as RecipeInputs}
                              view={view}
                              onChange={(name, value) => setField(i, name, value)}
                            />
                          ))}
                        </div>
                      ))}
                      {attachedFields(button) && (
                        <p className="text-[12px] leading-snug text-ink-dim">
                          What it reads from the template, picture or table is chosen in its run form, with them attached.
                        </p>
                      )}
                    </div>
                  )}
                </div>
                <div className="flex flex-none flex-col items-end gap-1.5">
                  <div className="flex gap-1">
                    <button type="button" className={icon} aria-label={`Move step ${number} up`} disabled={busy || i === 0} onClick={() => onChange(moveStep(edit, i, -1))}>
                      ↑
                    </button>
                    <button
                      type="button"
                      className={icon}
                      aria-label={`Move step ${number} down`}
                      disabled={busy || i === edit.steps.length - 1}
                      onClick={() => onChange(moveStep(edit, i, 1))}
                    >
                      ↓
                    </button>
                  </div>
                  {fields.length > 0 && !entry.removed && (
                    <button type="button" className={link} disabled={busy} onClick={() => onChange({ ...edit, open: open ? null : i })}>
                      {open ? "Done" : "Change"}
                    </button>
                  )}
                  <button type="button" className={link} disabled={busy} onClick={() => onChange(setRemoved(edit, i, !entry.removed))}>
                    {entry.removed ? "Put back" : "Remove"}
                  </button>
                </div>
              </li>
            );
          })}
        </ol>
      </div>

      <p className="text-[12px] leading-snug text-ink-dim">
        {[needs ? needs[0].toUpperCase() + needs.slice(1) + "." : "", mode !== recipe.mode ? `Saved, it becomes ${MODE_BADGE[mode]}: ${modeNote(mode)}` : ""]
          .filter(Boolean)
          .join(" ")}{" "}
        Every run is previewed and approved as before.
      </p>

      <div className="flex items-center justify-end gap-2">
        {(problem || !changed) && <p className="mr-auto text-xs text-ink-dim">{problem || "Nothing changed yet."}</p>}
        <button type="button" className={small} onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" className={small} disabled={busy || !!problem || !changed}>
          Save
        </button>
      </div>
    </form>
  );
}
