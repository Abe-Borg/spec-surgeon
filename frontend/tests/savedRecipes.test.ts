/**
 * Saved recipes on the page (lib/savedRecipes.ts): which runs a recipe can keep, which recipes each mode lists, what a
 * button step's saved form says, and what a recipe's run form still needs and sends.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, DoneStep, Recipe, RecipeStep, SavedRecipe, View } from "../src/types.ts";
import {
  changedForms,
  composerRecipes,
  formChips,
  formsChanged,
  keptSteps,
  modeNote,
  modeOfSteps,
  needsText,
  openRun,
  recentRecipes,
  recipeBlocked,
  recipeMeta,
  runPicks,
  runProblem,
  safeRecipes,
  savableRuns,
  stepShort,
} from "../src/lib/savedRecipes.ts";
import { saveRecipeBlocked, timesRun } from "../src/lib/workbench.ts";
import { EMPTY_VIEW, normalizeView } from "../src/lib/view.ts";

const file = (name: string, kind: Attachment["kind"]): Attachment => ({
  path: `C:/Specs/${name}`, name, kind, state: "ok", notes: [], reason: "", current: `C:/Specs/${name}`,
});
const DOCS = [file("21 13 13.docx", "document")];
const attached = (attachments: Attachment[] = DOCS) => ({ attachments, template: EMPTY_VIEW.template });

const TITLE: Recipe = {
  id: "title_property", label: "Set the document title property", group: "Files & properties", summary: "", needs: [],
  fields: [{ name: "pattern", kind: "text", label: "Title", required: true, default: "SECTION {number} - {title}" }],
};
const HEADERS: Recipe = {
  id: "headers_footers_template", label: "Match headers and footers to template", group: "Headers & footers", summary: "",
  needs: ["template"], fields: [{ name: "slots", kind: "slots", label: "Values", required: false }],
};
const ACCEPT: Recipe = {
  id: "accept_changes", label: "Accept or reject all tracked changes", group: "Clean up", summary: "", needs: [],
  fields: [{ name: "action", kind: "choice", label: "Every tracked change", required: false, default: "accept",
    options: [{ value: "accept", label: "Accept it" }, { value: "reject", label: "Reject it" }] }],
};
const CATALOG = [TITLE, HEADERS, ACCEPT];

const button = (id: string, inputs = {}, text = id): RecipeStep => ({
  kind: "button", label: CATALOG.find((r) => r.id === id)?.label ?? id, text, plan: [], needs: CATALOG.find((r) => r.id === id)?.needs ?? [],
  places: [], unguarded: false, button: id, inputs,
});
const plan = (unguarded = false, files: { kind: string; name: string }[] = []): RecipeStep => ({
  kind: "plan", label: "Your request", text: "Make the titles bold", plan: ["Apply bold"], needs: files.map((f) => f.kind),
  places: [], unguarded, files,
});
const saved = (steps: RecipeStep[], over: Partial<SavedRecipe> = {}): SavedRecipe => ({
  id: "r1", name: "Recipe", note: "", mode: modeOfSteps(steps), saved: "2026-10-28T10:00:00+00:00", last_run: "",
  last_documents: 0, steps, needs: [...new Set(steps.flatMap((s) => s.needs))], runs_here: true, why_not_here: "", ...over,
});

test("only the runs with steps can be kept, and the steps name the recipe's mode", () => {
  const run = (id: string | undefined, steps: RecipeStep[]): DoneStep => ({ id, request: "r", recipe: "", documents: 1, of: 1, not: [], steps });
  assert.deepEqual(savableRuns([run("a", [button("accept_changes")]), run("b", []), run(undefined, [plan()])]).map((r) => r.id), ["a"]);
  assert.equal(modeOfSteps([button("accept_changes")]), "safe");
  assert.equal(modeOfSteps([button("accept_changes"), plan()]), "default");
  assert.equal(modeOfSteps([plan(), plan(true)]), "obliterate");
  assert.match(modeNote("default"), /Safe mode runs only recipes made of its own buttons/);
});

test("Safe mode's panel lists its own recipes; the message box lists what runs here and Obliterate's greyed", () => {
  const safeOne = saved([button("accept_changes")], { id: "s" });
  const def = saved([plan()], { id: "d", runs_here: false, why_not_here: "Safe mode runs only…" });
  const obl = saved([plan(true)], { id: "o", runs_here: false, why_not_here: "runs only there" });
  const view = normalizeView({ saved_recipes: [safeOne, def, obl], mode: "safe" });
  assert.deepEqual(safeRecipes(view).map((r) => r.id), ["s"]);
  const inDefault = { saved_recipes: [safeOne, { ...def, runs_here: true }, obl], mode: "default" as const };
  assert.deepEqual(composerRecipes(inDefault).map((r) => r.id), ["s", "d", "o"]);
  assert.deepEqual(recentRecipes(composerRecipes(inDefault)).map((r) => r.id), ["s", "d"]);   // Obliterate's never a chip
  const later = { ...def, runs_here: true, last_run: "2026-10-29T09:00:00+00:00" };
  assert.deepEqual(recentRecipes([safeOne, later], 1).map((r) => r.id), ["d"]);
  assert.deepEqual(normalizeView({}).saved_recipes, []);                                   // an older server: none
});

test("a button step's saved form reads as a few words per field", () => {
  assert.deepEqual(formChips(ACCEPT, { action: "reject" }), ["Every tracked change: Reject it"]);
  assert.deepEqual(formChips(TITLE, { pattern: "{number}" }), ["Title: {number}"]);
  assert.deepEqual(formChips(HEADERS, { slots: { "ISSUED FOR PERMIT": "ISSUED FOR CONSTRUCTION", EMPTY: " " } }), [
    "“ISSUED FOR PERMIT”: ISSUED FOR CONSTRUCTION",
  ]);
  assert.deepEqual(formChips(undefined, {}), []);
  assert.equal(needsText(["template", "picture"]), "needs a template and a picture");
  assert.equal(recipeMeta(saved([button("headers_footers_template"), button("accept_changes")])), "2 steps · needs a template");
  assert.equal(stepShort(button("accept_changes")), "Accept or reject all tracke…");
});

test("a run form opens on the saved forms, says what is missing, and sends one entry per step", () => {
  const recipe = saved([button("title_property", { pattern: "SECTION {number}" }), button("accept_changes", { action: "accept" })]);
  const run = openRun(recipe, CATALOG, attached());
  assert.deepEqual(run.inputs, [{ pattern: "SECTION {number}" }, { action: "accept" }]);
  assert.equal(runProblem(recipe, run, CATALOG, attached()), "");
  assert.equal(runProblem(recipe, run, CATALOG, attached([])), "Add the Word documents first.");
  const emptied = { ...run, inputs: [{ pattern: " " }, run.inputs[1]] };
  assert.equal(runProblem(recipe, emptied, CATALOG, attached()), "Step 1: Type the title.");
  assert.equal(recipeBlocked(recipe, CATALOG, attached()), "");                 // a value to type never greys a tile
  const skipped = { ...emptied, skip: [true, false] };
  assert.equal(runProblem(recipe, skipped, CATALOG, attached()), "");            // a skipped step needs nothing
  assert.equal(keptSteps(skipped), 1);
  assert.deepEqual(runPicks(recipe, skipped, CATALOG), [{ skip: true }, { skip: false, inputs: { action: "accept" } }]);
  assert.equal(runProblem(recipe, { ...run, skip: [true, true] }, CATALOG, attached()), "Every step is skipped; keep at least one.");
  assert.equal(formsChanged(run), false);
  const changed = { ...run, inputs: [{ pattern: "{number}" }, run.inputs[1]] };
  assert.equal(formsChanged(changed), true);
  assert.deepEqual(changedForms(recipe, changed, CATALOG), [{ pattern: "{number}" }, null]);
});

test("a recipe that needs a file not attached, or runs elsewhere, is greyed with the reason", () => {
  const headers = saved([button("headers_footers_template", { slots: {} })]);
  assert.equal(recipeBlocked(headers, CATALOG, attached()), "Step 1: Attach your template first.");
  const withTemplate = attached([...DOCS, file("Office master.docx", "template")]);
  assert.equal(recipeBlocked(headers, CATALOG, withTemplate), "");
  const reads = saved([plan(false, [{ kind: "picture", name: "logo.png" }])]);
  assert.equal(recipeBlocked(reads, CATALOG, attached()), "Step 1: Attach the picture it uses (logo.png).");
  const elsewhere = saved([plan()], { runs_here: false, why_not_here: "It was saved in Obliterate mode and runs only there." });
  assert.equal(recipeBlocked(elsewhere, CATALOG, attached()), "It was saved in Obliterate mode and runs only there.");
});

test("a button run inside a recipe checkmarks its tile, and Save as a recipe says when nothing can be kept", () => {
  const working = { generation: 2, from_originals: false, can_start_over: true };
  const recipeRun: DoneStep = { id: "x", request: "Run the recipe “R”", recipe: "", documents: 1, of: 1, not: [], name: "R",
    steps: [button("accept_changes"), button("title_property")], buttons: ["accept_changes", "title_property"], why_not: "" };
  const own: DoneStep = { id: "y", request: "Accept all tracked changes", recipe: "accept_changes", documents: 1, of: 1, not: [], buttons: ["accept_changes"] };
  const view: Pick<View, "working"> = { working: { ...working, done: [recipeRun, own] } };
  assert.equal(timesRun(view, "accept_changes"), 2);
  assert.equal(timesRun(view, "title_property"), 1);
  assert.equal(saveRecipeBlocked(view), "");
  const custom: DoneStep = { id: "z", request: "r", recipe: "", documents: 1, of: 1, not: [], steps: [], why_not: "Claude wrote a custom method for it." };
  assert.equal(saveRecipeBlocked({ working: { ...working, done: [custom] } }), "Claude wrote a custom method for it.");
});
