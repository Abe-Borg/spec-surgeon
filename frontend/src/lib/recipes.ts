/**
 * Safe mode's buttons on the page (docs/CONVERSATIONAL_DESKTOP.md, Safe mode): which button can be pressed with what is
 * attached, the form each one starts with, and what the form still needs before Preview. The server decides
 * everything again (`recipes.build`, then the one gate every plan passes); these rules only keep the panel honest
 * about what it can send.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { FindReplacePair, Recipe, RecipeField, RecipeInputs, View } from "../types.ts";
import { kindTag } from "./workbench.ts";

export interface Availability {
  ready: boolean;
  /** Why the button cannot be pressed yet; "" when it can. */
  reason: string;
}

type Attached = Pick<View, "attachments" | "template">;

const usable = (view: Pick<View, "attachments">) => view.attachments.filter((a) => a.state !== "refused");

/** The attached pictures and tables, by path, as a form chooses among them. */
export function pictures(view: Pick<View, "attachments">): { path: string; name: string }[] {
  return usable(view)
    .filter((a) => a.kind === "reference" && kindTag(a) === "Picture")
    .map((a) => ({ path: a.path, name: a.name }));
}

export function tables(view: Pick<View, "attachments">): { path: string; name: string }[] {
  return usable(view)
    .filter((a) => a.kind === "reference" && kindTag(a) === "Table")
    .map((a) => ({ path: a.path, name: a.name }));
}

/** Whether a button can be pressed with what is attached, and why not. */
export function availability(recipe: Recipe, view: Attached): Availability {
  if (!usable(view).some((a) => a.kind === "document")) {
    return { ready: false, reason: "Add the Word documents first." };
  }
  if (recipe.needs.includes("template")) {
    if (!usable(view).some((a) => a.kind === "template")) {
      return { ready: false, reason: "Attach your template first." };
    }
    if (recipe.id === "headers_footers_template" && view.template?.kind === "unusable") {
      return { ready: false, reason: view.template.reason || "This template has nothing to fill in." };
    }
    if (recipe.id === "body_formatting" && view.template?.body?.problems?.length) {
      return { ready: false, reason: view.template.body.problems[0] };
    }
  }
  if (recipe.needs.includes("picture") && pictures(view).length === 0) {
    return { ready: false, reason: "Attach the new picture first." };
  }
  return { ready: true, reason: "" };
}

/** The form a button opens with: each field's default, the first attached picture as the new one. */
export function initialInputs(recipe: Recipe, view: Attached): RecipeInputs {
  const inputs: RecipeInputs = {};
  for (const field of recipe.fields) {
    switch (field.kind) {
      case "pairs":
        inputs[field.name] = [{ find: "", replace: "" }];
        break;
      case "slots":
        inputs[field.name] = Object.fromEntries((view.template?.typed_slots ?? []).map((slot) => [slot, ""]));
        break;
      case "roles":
        inputs[field.name] = { ...(view.template?.body?.roles ?? {}) };
        break;
      case "check":
        inputs[field.name] = field.default === true;
        break;
      case "picture":
        inputs[field.name] = field.required ? (pictures(view)[0]?.path ?? "") : "";
        break;
      case "table":
        inputs[field.name] = "";
        break;
      default:
        inputs[field.name] = typeof field.default === "string" ? field.default : "";
    }
  }
  return inputs;
}

/**
 * The open form again after the attachments changed: what was typed stays (a template value only while the template
 * still asks for it), and a picture or table that is no longer attached is chosen afresh as the form would open with
 * it, never sent stale.
 */
export function refreshInputs(recipe: Recipe, current: RecipeInputs, view: Attached): RecipeInputs {
  const out = initialInputs(recipe, view);
  const files = new Set([...pictures(view), ...tables(view)].map((f) => f.path));
  for (const field of recipe.fields) {
    if (!(field.name in current)) continue;
    const value = current[field.name];
    if (field.kind === "slots") {
      const typed = (value ?? {}) as Record<string, string>;
      const slots = out[field.name] as Record<string, string>;
      for (const slot of Object.keys(slots)) if (slot in typed) slots[slot] = typed[slot];
    } else if (field.kind === "roles") {
      // a choice stays while the template still has that style ("" — none — always does)
      const chosen = (value ?? {}) as Record<string, string>;
      const known = new Set((view.template?.body?.styles ?? []).map((s) => s.id));
      const roles = out[field.name] as Record<string, string>;
      for (const [role, style] of Object.entries(chosen)) if (style === "" || known.has(style)) roles[role] = style;
    } else if (field.kind === "picture" || field.kind === "table") {
      if (value && files.has(String(value))) out[field.name] = value;
    } else {
      out[field.name] = value;
    }
  }
  return out;
}

/** A field shows only when it applies: the watermark's words only when adding one. */
export function fieldShown(recipe: Recipe, field: RecipeField, inputs: RecipeInputs): boolean {
  if (recipe.id === "watermark" && field.name === "text") return inputs.action !== "remove";
  return true;
}

function filledPairs(value: unknown): FindReplacePair[] {
  if (!Array.isArray(value)) return [];
  return (value as FindReplacePair[]).filter((p) => p.find.trim() !== "" || p.replace.trim() !== "");
}

/** What the form still needs before Preview, in one sentence; "" when it can be sent. */
export function missing(recipe: Recipe, inputs: RecipeInputs): string {
  for (const field of recipe.fields) {
    if (!fieldShown(recipe, field, inputs)) continue;
    const value = inputs[field.name];
    if (field.kind === "text" && (field.required || recipe.id === "watermark") && !String(value ?? "").trim()) {
      return `Type ${field.label.charAt(0).toLowerCase()}${field.label.slice(1)}.`;
    }
    if (field.kind === "slots" && value && typeof value === "object" && !Array.isArray(value)) {
      const empty = Object.entries(value as Record<string, string>).find(([, v]) => !String(v).trim());
      if (empty) return `Type the value for “${empty[0]}”.`;
    }
    if (field.kind === "picture" && field.required && !value) return `Choose the ${field.label.toLowerCase()}.`;
  }
  if (recipe.id === "find_replace") {
    const pairs = filledPairs(inputs.pairs);
    if (pairs.length === 0 && !inputs.table) return "Type a pair of words to find and replace, or choose a table.";
    if (pairs.some((p) => !p.find.trim())) return "Each row needs the words to find.";
  }
  return "";
}

/** The form as sent: empty find-and-replace rows dropped. */
export function payload(recipe: Recipe, inputs: RecipeInputs): RecipeInputs {
  const out: RecipeInputs = { ...inputs };
  if (recipe.id === "find_replace") out.pairs = filledPairs(inputs.pairs);
  return out;
}

/** The buttons in their groups, in the server's order. */
export function grouped(recipes: Recipe[]): { group: string; recipes: Recipe[] }[] {
  const out: { group: string; recipes: Recipe[] }[] = [];
  for (const recipe of recipes) {
    const last = out[out.length - 1];
    if (last && last.group === recipe.group) last.recipes.push(recipe);
    else out.push({ group: recipe.group, recipes: [recipe] });
  }
  return out;
}

/** The Safe panel's form: the button whose form is open, what it holds, and what each button's form held when it was
 *  left (All buttons), so going back to a button finds what was typed. Kept by the page only, until New conversation
 *  or Start over. */
export interface SafeFormState {
  recipe: string | null;
  inputs: RecipeInputs;
  kept: Record<string, RecipeInputs>;
}

export const EMPTY_SAFE_FORM: SafeFormState = { recipe: null, inputs: {}, kept: {} };

/** A button's form opened: what it held when it was left, taken up to the attachments as they are now, or a new
 *  form. */
export function openForm(form: SafeFormState, recipe: Recipe, view: Attached): SafeFormState {
  const kept = form.kept[recipe.id];
  return { ...form, recipe: recipe.id, inputs: kept ? refreshInputs(recipe, kept, view) : initialInputs(recipe, view) };
}

/** Back to all the buttons: the open form's values are kept for when its button is chosen again. */
export function leaveForm(form: SafeFormState): SafeFormState {
  return form.recipe === null
    ? form
    : { recipe: null, inputs: {}, kept: { ...form.kept, [form.recipe]: form.inputs } };
}

/** The open form as it first opens, what was typed in it gone. */
export function clearForm(form: SafeFormState, recipe: Recipe, view: Attached): SafeFormState {
  const kept = { ...form.kept };
  delete kept[recipe.id];
  return { recipe: recipe.id, inputs: initialInputs(recipe, view), kept };
}
