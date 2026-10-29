import { useState } from "react";
import type { Recipe, RecipeInputs, RecipeMode, SavedRecipe, View } from "../types";
import { MODE_BADGE, dateText, editPayload, needsText, openEdit, stepsText, type RecipeEditState } from "../lib/savedRecipes";
import { ModalShell, quietBtn } from "./ModalShell";
import RecipeEditor from "./RecipeEditor";

interface Props {
  recipes: SavedRecipe[];
  /** Safe mode's buttons (GET /api/recipes): the editor changes a button step's form. */
  catalog: Recipe[];
  view: Pick<View, "attachments" | "template">;
  busy: boolean;
  /** Each change answers with the view (`POST /api/saved-recipes/change`); false when it was refused (said by a notice). */
  onEdit: (recipe: SavedRecipe, rev: string, name: string, note: string, steps: { step: number; inputs?: RecipeInputs }[]) => Promise<boolean>;
  onDuplicate: (recipe: SavedRecipe) => Promise<boolean>;
  onDelete: (recipe: SavedRecipe) => Promise<boolean>;
  onClose: () => void;
}

const BADGE: Record<RecipeMode, string> = {
  safe: "border-ok/60 text-ok",
  default: "border-accent/60 text-accent",
  obliterate: "border-err/70 text-err",
};

const small =
  "rounded-lg border border-edge bg-raised px-2.5 py-1 text-xs text-ink transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";

/**
 * Your recipes: every recipe the window lists, with the mode it belongs to, its steps, when it was saved and last ran,
 * and Edit (its name, what it is for, and its steps moved, removed or, for a button, given another form), Duplicate
 * and Delete (asked once). Kept on this computer, beside the settings; nothing else is offered.
 */
export default function ManageRecipes({ recipes, catalog, view, busy, onEdit, onDuplicate, onDelete, onClose }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<RecipeEditState | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  return (
    <ModalShell title="Your recipes" onClose={onClose} wide>
      <div className="flex flex-col gap-1.5 text-sm" data-capability="recipes.manage">
        {recipes.length === 0 && (
          <p className="text-[13px] leading-relaxed text-ink-dim">
            No recipes yet. Run some changes, then Save as a recipe… under What you’ve done so far.
          </p>
        )}
        {recipes.map((recipe) => {
          const editingThis = editing?.id === recipe.id;
          const ran = recipe.last_run ? `last ran ${dateText(recipe.last_run)}, on ${recipe.last_documents} document${recipe.last_documents === 1 ? "" : "s"}` : "never run";
          return (
            <div key={recipe.id} className="rounded-xl border border-edge bg-raised px-3 py-2.5">
              {editingThis && editing ? (
                <RecipeEditor
                  recipe={recipe}
                  catalog={catalog}
                  view={view}
                  edit={editing}
                  busy={busy}
                  onChange={setEditing}
                  onCancel={() => setEditing(null)}
                  onSave={() =>
                    void onEdit(recipe, editing.rev, editing.name.trim(), editing.note.trim(), editPayload(editing, recipe, catalog)).then(
                      (ok) => ok && setEditing(null),
                    )
                  }
                />
              ) : (
                <div className="flex items-start gap-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    aria-expanded={open === recipe.id}
                    onClick={() => setOpen(open === recipe.id ? null : recipe.id)}
                  >
                    <span className="block font-semibold break-words text-ink">{recipe.name}</span>
                    <span className="mt-0.5 block text-xs text-ink-dim">
                      {[stepsText(recipe.steps.length), needsText(recipe.needs), `saved ${dateText(recipe.saved)}`, ran].filter(Boolean).join(" · ")}
                    </span>
                    {recipe.note && <span className="mt-0.5 block text-xs break-words text-ink-dim">{recipe.note}</span>}
                  </button>
                  <span className={`mt-0.5 flex-none rounded-full border px-2 py-px text-[10px] font-bold tracking-wide ${BADGE[recipe.mode]}`}>
                    {MODE_BADGE[recipe.mode]}
                  </span>
                </div>
              )}
              {open === recipe.id && !editingThis && (
                <ol className="mt-2 flex flex-col gap-1 border-l-2 border-accent/30 pl-3">
                  {recipe.steps.map((step, i) => (
                    <li key={i} className="text-[12.5px] leading-snug break-words text-ink-dim">
                      <span className="mr-1.5 font-semibold text-accent">{i + 1}</span>
                      {step.kind === "button" ? step.text : `“${step.text}”`}
                    </li>
                  ))}
                </ol>
              )}
              {!recipe.runs_here && !editingThis && <p className="mt-1 text-xs text-ink-dim">{recipe.why_not_here}</p>}
              {!editingThis && (
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <button
                    type="button"
                    className={small}
                    disabled={busy}
                    onClick={() => setEditing(openEdit(recipe))}
                    data-capability="recipes.edit"
                  >
                    Edit
                  </button>
                  <button type="button" className={small} disabled={busy} onClick={() => void onDuplicate(recipe)}>
                    Duplicate
                  </button>
                  {deleting === recipe.id ? (
                    <>
                      <span className="ml-1 text-xs text-ink">Delete “{recipe.name}”? It cannot be brought back.</span>
                      <button
                        type="button"
                        className={`${small} border-err/50 text-err`}
                        disabled={busy}
                        onClick={() => void onDelete(recipe).then(() => setDeleting(null))}
                      >
                        Delete
                      </button>
                      <button type="button" className={small} onClick={() => setDeleting(null)}>
                        Keep it
                      </button>
                    </>
                  ) : (
                    <button type="button" className={`${small} text-err`} disabled={busy} onClick={() => setDeleting(recipe.id)}>
                      Delete
                    </button>
                  )}
                </div>
              )}
            </div>
          );
        })}
        <div className="mt-2 flex items-center gap-3 border-t border-edge pt-3">
          <p className="min-w-0 flex-1 text-xs leading-snug text-ink-dim">
            Kept on this computer, beside your settings: the steps and what you typed or approved, never a document.
          </p>
          <button type="button" className={quietBtn} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </ModalShell>
  );
}
