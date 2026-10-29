import { useState } from "react";
import type { RecipeMode, SavedRecipe } from "../types";
import { MODE_BADGE, dateText, needsText, stepsText } from "../lib/savedRecipes";
import { ModalShell, quietBtn } from "./ModalShell";

interface Props {
  recipes: SavedRecipe[];
  busy: boolean;
  /** Each change answers with the view (`POST /api/saved-recipes/change`); false when it was refused (said by a notice). */
  onRename: (recipe: SavedRecipe, name: string, note: string) => Promise<boolean>;
  onDuplicate: (recipe: SavedRecipe) => Promise<boolean>;
  onDelete: (recipe: SavedRecipe) => Promise<boolean>;
  onClose: () => void;
}

const BADGE: Record<RecipeMode, string> = {
  safe: "border-ok/60 text-ok",
  default: "border-accent/60 text-accent",
  obliterate: "border-err/70 text-err",
};

const inputClass =
  "w-full rounded-lg border border-edge bg-bg px-2.5 py-1.5 text-sm text-ink outline-none transition-colors focus:border-accent";

const small =
  "rounded-lg border border-edge bg-raised px-2.5 py-1 text-xs text-ink transition-colors hover:border-accent hover:text-accent " +
  "disabled:pointer-events-none disabled:opacity-40";

/**
 * Your recipes: every recipe the window lists, with the mode it belongs to, its steps, when it was saved and last ran,
 * and Rename, Duplicate and Delete (asked once). Kept on this computer, beside the settings; nothing else is offered.
 */
export default function ManageRecipes({ recipes, busy, onRename, onDuplicate, onDelete, onClose }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; name: string; note: string } | null>(null);
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
                <form
                  className="flex flex-col gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void onRename(recipe, editing.name, editing.note).then((ok) => ok && setEditing(null));
                  }}
                >
                  <label className="block">
                    <span className="mb-1 block text-[12px] font-medium text-ink-dim">Name</span>
                    <input className={inputClass} value={editing.name} maxLength={120} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[12px] font-medium text-ink-dim">What it’s for</span>
                    <input className={inputClass} value={editing.note} maxLength={300} onChange={(e) => setEditing({ ...editing, note: e.target.value })} />
                  </label>
                  <div className="flex justify-end gap-2">
                    <button type="button" className={small} onClick={() => setEditing(null)}>
                      Cancel
                    </button>
                    <button type="submit" className={small} disabled={busy || !editing.name.trim()}>
                      Save
                    </button>
                  </div>
                </form>
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
                  <button type="button" className={small} disabled={busy} onClick={() => setEditing({ id: recipe.id, name: recipe.name, note: recipe.note })}>
                    Rename
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
