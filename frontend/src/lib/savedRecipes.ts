/**
 * Saved recipes on the page (docs/CONVERSATIONAL_DESKTOP.md, Saved recipes): which runs of What you've done so far a
 * recipe can keep, what a step's saved form says in a few words, which recipes each mode lists, and what a recipe's run
 * form still needs before Preview. The server decides everything again (`saved_recipes.build_run`, then the one gate);
 * these rules only keep the page honest about what it can send.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { DoneStep, Recipe, RecipeInputs, RecipeMode, RecipeStep, SavedRecipe, View } from "../types.ts";
import { availability, fieldShown, filledLines, missing, payload, refreshInputs } from "./recipes.ts";

/** The runs a recipe can keep: those with steps (a custom method, a plan bound to particular documents, cannot). */
export function savableRuns(done: DoneStep[]): DoneStep[] {
  return done.filter((d) => !!d.id && (d.steps ?? []).length > 0);
}

/** The mode a recipe made of these steps belongs to (src/spec_surgeon/saved_recipes.py, `recipe_mode`). */
export function modeOfSteps(steps: RecipeStep[]): RecipeMode {
  if (steps.some((s) => s.kind === "plan" && s.unguarded)) return "obliterate";
  if (steps.some((s) => s.kind === "plan")) return "default";
  return "safe";
}

/** Where a recipe of that mode can run, in one sentence. */
export function modeNote(mode: RecipeMode): string {
  if (mode === "safe") return "A Safe-mode recipe: it runs in every mode, with no AI.";
  if (mode === "default") {
    return "A Default-mode recipe: it runs in Default and Obliterate mode, with no AI. Safe mode runs only recipes made of its own buttons.";
  }
  return "An Obliterate recipe: it runs only in Obliterate mode, and its copies are checked like any other.";
}

/** The mode's badge on a recipe. */
export const MODE_BADGE: Record<RecipeMode, string> = { safe: "SAFE", default: "DEFAULT", obliterate: "OBLITERATE" };

/** The recipes Safe mode's panel shows as tiles: its own, made of buttons. */
export function safeRecipes(view: Pick<View, "saved_recipes">): SavedRecipe[] {
  return (view.saved_recipes ?? []).filter((r) => r.mode === "safe");
}

/** The recipes beside the message box in Default and Obliterate: the ones that run here, then Obliterate's greyed. */
export function composerRecipes(view: Pick<View, "saved_recipes" | "mode">): SavedRecipe[] {
  const all = view.saved_recipes ?? [];
  return [...all.filter((r) => r.runs_here), ...all.filter((r) => !r.runs_here && r.mode === "obliterate")];
}

/** The few the strip shows as chips: most recently run, then most recently saved. */
export function recentRecipes(recipes: SavedRecipe[], count = 3): SavedRecipe[] {
  const when = (r: SavedRecipe) => r.last_run || r.saved || "";
  return recipes.filter((r) => r.runs_here).slice().sort((a, b) => (when(b) < when(a) ? -1 : when(b) > when(a) ? 1 : 0)).slice(0, count);
}

const NEED_WORDS: Record<string, string> = { template: "a template", picture: "a picture", table: "a table" };

/** "needs a template", "needs a template and a picture"; "" when it needs nothing beside the documents. */
export function needsText(needs: string[]): string {
  const words = needs.map((n) => NEED_WORDS[n] ?? n);
  if (words.length === 0) return "";
  return `needs ${words.length === 1 ? words[0] : `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`}`;
}

/** "5 steps", "1 step". */
export function stepsText(n: number): string {
  return `${n} step${n === 1 ? "" : "s"}`;
}

/** A tile's line under the name: its steps and what it needs. */
export function recipeMeta(recipe: SavedRecipe): string {
  const need = needsText(recipe.needs);
  return [stepsText(recipe.steps.length), need].filter(Boolean).join(" · ");
}

/** A date as the list shows it: "Oct 29, 2026"; "" for none. */
export function dateText(iso: string): string {
  if (!iso) return "";
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** The short name a step shows in a tile's strip: a button's label, or the first words of a request. */
export function stepShort(step: RecipeStep, limit = 28): string {
  const text = step.kind === "button" ? step.label : step.text;
  return text.length <= limit ? text : `${text.slice(0, limit - 1)}…`;
}

const quote = (s: string) => `“${s}”`;
const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;

/** What a button step's saved form says, a few words per field the button shows (the chips under the step). */
export function formChips(button: Recipe | undefined, inputs: RecipeInputs | undefined): string[] {
  if (!button || !inputs) return [];
  const out: string[] = [];
  for (const field of button.fields) {
    if (!fieldShown(button, field, inputs)) continue;
    const value = inputs[field.name];
    switch (field.kind) {
      case "text":
        if (typeof value === "string" && value.trim()) out.push(`${field.label}: ${value.trim()}`);
        break;
      case "choice": {
        const option = (field.options ?? []).find((o) => o.value === value);
        if (option) out.push(`${field.label}: ${option.label}`);
        break;
      }
      case "check":
        if (value === true) out.push(field.label);
        break;
      case "lines": {
        const lines = filledLines(value);
        if (lines.length) out.push(`${field.label}: ${lines.map(quote).join(", ")}`);
        break;
      }
      case "pairs": {
        const pairs = Array.isArray(value) ? (value as { find: string; replace: string }[]).filter((p) => p.find?.trim()) : [];
        if (pairs.length) {
          const first = pairs.slice(0, 2).map((p) => `${quote(p.find)} → ${quote(p.replace)}`).join(", ");
          out.push(pairs.length > 2 ? `${first} and ${pairs.length - 2} more` : first);
        }
        break;
      }
      case "slots": {
        const slots = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string>) : {};
        for (const [slot, typed] of Object.entries(slots)) if (typed.trim()) out.push(`${quote(slot)}: ${typed.trim()}`);
        break;
      }
      case "picture":
      case "table":
        if (typeof value === "string" && value) out.push(`${field.label}: ${fileName(value)}`);
        break;
      default:
        break;
    }
  }
  return out;
}

/** A recipe's run form: per saved step, skipped this time or not, and the form a button step runs with; which step's
 *  form is open; the forms as saved, to tell what changed; and `rev`, the recipe as the form opened it (its picks are
 *  by position, so a recipe whose steps were moved, removed or added since cannot take them). */
export interface RecipeRunState {
  id: string;
  rev: string;
  skip: boolean[];
  inputs: (RecipeInputs | null)[];
  saved: (RecipeInputs | null)[];
  open: number | null;
}

type Attached = Pick<View, "attachments" | "template">;

/** The run form a recipe opens with: every step kept, each button's saved form taken up to what is attached now (a
 *  template's placeholders it no longer has, a picture or table no longer attached, chosen afresh). */
export function openRun(recipe: SavedRecipe, catalog: Recipe[], view: Attached): RecipeRunState {
  const inputs = recipe.steps.map((step) => {
    if (step.kind !== "button") return null;
    const button = catalog.find((b) => b.id === step.button);
    return button ? refreshInputs(button, step.inputs ?? {}, view) : { ...(step.inputs ?? {}) };
  });
  return { id: recipe.id, rev: recipe.rev ?? "", skip: recipe.steps.map(() => false), inputs, saved: inputs.map((i) => (i ? { ...i } : null)), open: null };
}

/** The recipe was edited (here or in another window) since its run form opened: the form's picks no longer line up. */
export function runStale(recipe: SavedRecipe, run: RecipeRunState): boolean {
  return !!recipe.rev && !!run.rev && recipe.rev !== run.rev;
}

/** Why a step cannot run with what is attached and its form as it is; "" when it can. */
export function stepProblem(step: RecipeStep, inputs: RecipeInputs | null, catalog: Recipe[], view: Attached): string {
  if (step.kind === "button") {
    const button = catalog.find((b) => b.id === step.button);
    if (!button) return "This version of the app has no such button.";
    const ready = availability(button, view);
    if (!ready.ready) return ready.reason;
    return missing(button, inputs ?? {});
  }
  const usable = view.attachments.filter((a) => a.state !== "refused");
  for (const file of step.files ?? []) {
    if (file.kind === "template" && !usable.some((a) => a.kind === "template")) return `Attach a template first (it used ${file.name}).`;
    if ((file.kind === "picture" || file.kind === "table") && !usable.some((a) => a.kind === "reference")) {
      return `Attach the ${file.kind} it uses (${file.name}).`;
    }
  }
  return "";
}

/** What the run form still needs before Preview, in one sentence naming the step; "" when it can be sent. */
export function runProblem(recipe: SavedRecipe, run: RecipeRunState, catalog: Recipe[], view: Attached): string {
  if (runStale(recipe, run)) return "The recipe changed since its form was opened; open it again.";
  if (!recipe.runs_here) return recipe.why_not_here;
  if (!usableDocuments(view)) return "Add the Word documents first.";
  const kept = recipe.steps.map((_, i) => !run.skip[i]);
  if (!kept.some(Boolean)) return "Every step is skipped; keep at least one.";
  for (let i = 0; i < recipe.steps.length; i++) {
    if (!kept[i]) continue;
    const problem = stepProblem(recipe.steps[i], run.inputs[i] ?? null, catalog, view);
    if (problem) return `Step ${i + 1}: ${problem}`;
  }
  return "";
}

/** Why a recipe's tile cannot be opened now — it does not run in this mode, no document is attached, or a step needs a
 *  file that is not attached — in one sentence; "" when it can. A form value still to type is not a reason: the run
 *  form asks for it. */
export function recipeBlocked(recipe: SavedRecipe, catalog: Recipe[], view: Attached): string {
  if (!recipe.runs_here) return recipe.why_not_here;
  if (!usableDocuments(view)) return "Add the Word documents first.";
  for (let i = 0; i < recipe.steps.length; i++) {
    const step = recipe.steps[i];
    if (step.kind === "button") {
      const button = catalog.find((b) => b.id === step.button);
      if (!button) return `Step ${i + 1}: this version of the app has no such button.`;
      const ready = availability(button, view);
      if (!ready.ready) return `Step ${i + 1}: ${ready.reason}`;
    } else {
      const problem = stepProblem(step, null, catalog, view);
      if (problem) return `Step ${i + 1}: ${problem}`;
    }
  }
  return "";
}

function usableDocuments(view: Pick<View, "attachments">): boolean {
  return view.attachments.some((a) => a.kind === "document" && a.state !== "refused");
}

/** How many steps run this time. */
export function keptSteps(run: RecipeRunState): number {
  return run.skip.filter((s) => !s).length;
}

/** What the run sends: one entry per saved step, a button's form as the form holds it (empty rows dropped). */
export function runPicks(recipe: SavedRecipe, run: RecipeRunState, catalog: Recipe[]): { skip: boolean; inputs?: RecipeInputs }[] {
  return recipe.steps.map((step, i) => {
    if (run.skip[i]) return { skip: true };
    const inputs = run.inputs[i];
    if (step.kind !== "button" || !inputs) return { skip: false };
    const button = catalog.find((b) => b.id === step.button);
    return { skip: false, inputs: button ? payload(button, inputs) : inputs };
  });
}

/** A button step's form differs from the one saved (the run form then offers to save it to the recipe). */
export function formsChanged(run: RecipeRunState): boolean {
  return run.inputs.some((inputs, i) => JSON.stringify(inputs) !== JSON.stringify(run.saved[i]));
}

/** The forms "Save these changes to the recipe" sends: a changed button form, null for every other step. */
export function changedForms(recipe: SavedRecipe, run: RecipeRunState, catalog: Recipe[]): (RecipeInputs | null)[] {
  return recipe.steps.map((step, i) => {
    const inputs = run.inputs[i];
    if (step.kind !== "button" || !inputs || JSON.stringify(inputs) === JSON.stringify(run.saved[i])) return null;
    const button = catalog.find((b) => b.id === step.button);
    return button ? payload(button, inputs) : inputs;
  });
}

// --- Editing a saved recipe (Manage → Edit; `POST /api/saved-recipes/change`, action `edit`) -------------------------

/** The editor: the name, what it is for, and every saved step in the order it will have — each with the step it was
 *  (`from`, counted from 0), the form a button step keeps, and whether it is removed (greyed until saved, put back with
 *  one click); which step's form is open; and `rev`, the recipe as the editor opened it (an edit made after another
 *  window changed it is refused). */
export interface RecipeEditState {
  id: string;
  rev: string;
  name: string;
  note: string;
  steps: { from: number; inputs: RecipeInputs | null; removed: boolean }[];
  open: number | null;
}

/** The kinds of field the editor changes: the ones that read nothing attached. A template's placeholders and styles,
 *  and a picture or table, are chosen among the files attached, so they are changed in the recipe's run form. */
const EDITABLE_KINDS = new Set(["text", "lines", "pairs", "choice", "check"]);

/** A button's fields the editor shows to change. */
export function editableFields(button: Recipe): Recipe["fields"] {
  return button.fields.filter((f) => EDITABLE_KINDS.has(f.kind));
}

/** A button has fields the editor leaves to the run form (the template, picture or table it reads). */
export function attachedFields(button: Recipe): boolean {
  return button.fields.some((f) => !EDITABLE_KINDS.has(f.kind));
}

/** The editor a recipe opens with: every step as saved, in its order. */
export function openEdit(recipe: SavedRecipe): RecipeEditState {
  return {
    id: recipe.id,
    rev: recipe.rev ?? "",
    name: recipe.name,
    note: recipe.note,
    steps: recipe.steps.map((step, from) => ({ from, inputs: step.kind === "button" ? { ...(step.inputs ?? {}) } : null, removed: false })),
    open: null,
  };
}

/** The step at `i` moved one place up (`-1`) or down (`1`); the open form follows its step. */
export function moveStep(edit: RecipeEditState, i: number, by: -1 | 1): RecipeEditState {
  const j = i + by;
  if (i < 0 || j < 0 || i >= edit.steps.length || j >= edit.steps.length) return edit;
  const steps = edit.steps.slice();
  [steps[i], steps[j]] = [steps[j], steps[i]];
  const open = edit.open === i ? j : edit.open === j ? i : edit.open;
  return { ...edit, steps, open };
}

/** The step at `i` removed, or put back. */
export function setRemoved(edit: RecipeEditState, i: number, removed: boolean): RecipeEditState {
  return {
    ...edit,
    steps: edit.steps.map((s, j) => (j === i ? { ...s, removed } : s)),
    open: removed && edit.open === i ? null : edit.open,
  };
}

/** The steps the recipe keeps after the edit, as `RecipeStep`s in their new order (to say its mode and what it needs). */
export function editedSteps(edit: RecipeEditState, recipe: SavedRecipe): RecipeStep[] {
  return edit.steps.filter((s) => !s.removed && recipe.steps[s.from]).map((s) => recipe.steps[s.from]);
}

/** Why the edit cannot be saved, in one sentence naming the step; "" when it can. */
export function editProblem(edit: RecipeEditState, recipe: SavedRecipe, catalog: Recipe[]): string {
  if (recipe.rev && edit.rev && recipe.rev !== edit.rev) return "The recipe changed since you opened it, perhaps in another window; cancel and open it again.";
  if (!edit.name.trim()) return "Type a name for the recipe.";
  if (!edit.steps.some((s) => !s.removed)) return "Keep at least one step, or delete the recipe.";
  for (let i = 0; i < edit.steps.length; i++) {
    const { from, inputs, removed } = edit.steps[i];
    const step = recipe.steps[from];
    if (removed || !step || step.kind !== "button" || !inputs) continue;
    const button = catalog.find((b) => b.id === step.button);
    if (!button) continue;
    const problem = missing({ ...button, fields: editableFields(button) }, inputs);
    if (problem) return `Step ${i + 1}: ${problem}`;
  }
  return "";
}

/** The edit differs from the recipe as saved: a name, a note, a step's place, a removed step or a changed form. */
export function editChanged(edit: RecipeEditState, recipe: SavedRecipe): boolean {
  if (edit.name.trim() !== recipe.name || edit.note.trim() !== recipe.note) return true;
  if (edit.steps.some((s, i) => s.removed || s.from !== i)) return true;
  return edit.steps.some((s) => s.inputs && JSON.stringify(s.inputs) !== JSON.stringify(recipe.steps[s.from]?.inputs ?? {}));
}

/** What Save sends: the kept steps in their new order, each with the step it was and, for a button whose form changed,
 *  that form (empty rows dropped). */
export function editPayload(edit: RecipeEditState, recipe: SavedRecipe, catalog: Recipe[]): { step: number; inputs?: RecipeInputs }[] {
  return edit.steps
    .filter((s) => !s.removed)
    .map(({ from, inputs }) => {
      const step = recipe.steps[from];
      if (!step || step.kind !== "button" || !inputs || JSON.stringify(inputs) === JSON.stringify(step.inputs ?? {})) return { step: from };
      const button = catalog.find((b) => b.id === step.button);
      return { step: from, inputs: button ? payload(button, inputs) : inputs };
    });
}

/** The mode a saved recipe would belong to with these steps added to its end (Save as a recipe → Add to). */
export function addedMode(target: SavedRecipe, steps: RecipeStep[]): RecipeMode {
  return modeOfSteps([...target.steps, ...steps]);
}
