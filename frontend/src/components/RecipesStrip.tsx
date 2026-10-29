import { useState } from "react";
import type { Recipe, RecipeInputs, SavedRecipe, View } from "../types";
import type { RecipeRunPick } from "../lib/api";
import { MODE_BADGE, composerRecipes, openRun, recentRecipes, recipeBlocked, recipeMeta, type RecipeRunState } from "../lib/savedRecipes";
import { TOUR_HOLD } from "../lib/tour";
import { ModalShell } from "./ModalShell";
import RecipeRunForm from "./RecipeRunForm";
import Tip from "./Tip";

interface Props {
  view: Pick<View, "saved_recipes" | "mode" | "attachments" | "template" | "recipe_blocked">;
  catalog: Recipe[];
  busy: boolean;
  tourActive: boolean;
  onRun: (recipe: SavedRecipe, picks: RecipeRunPick[]) => void;
  onSaveForms: (recipe: SavedRecipe, forms: (RecipeInputs | null)[]) => Promise<boolean>;
  onManage: () => void;
}

const BADGE: Record<string, string> = {
  safe: "border-ok/60 text-ok",
  default: "border-accent/60 text-accent",
  obliterate: "border-err/70 text-err",
};

function Play() {
  return (
    <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M7 4v16l13-8z" />
    </svg>
  );
}

/**
 * Recipes, beside the message box in Default and Obliterate: the few last run as chips and All recipes, a list of every
 * recipe that runs here (Safe mode's and Default's; Obliterate's greyed unless in Obliterate mode) with Manage recipes.
 * Choosing one opens its run form in a dialog; Preview there runs it as one plan, with no AI call. A request still
 * open holds them (the view's `recipe_blocked`).
 */
export default function RecipesStrip({ view, catalog, busy, tourActive, onRun, onSaveForms, onManage }: Props) {
  const [listing, setListing] = useState(false);
  const [run, setRun] = useState<RecipeRunState | null>(null);
  const all = composerRecipes(view);
  const recent = recentRecipes(all, 2);
  const running = run ? all.find((r) => r.id === run.id) ?? null : null;
  if (all.length === 0) return null;
  const held = busy || tourActive;
  const why = (recipe: SavedRecipe) =>
    tourActive ? TOUR_HOLD : busy ? "Wait for the current work to finish" : view.recipe_blocked || recipeBlocked(recipe, catalog, view);
  const open = (recipe: SavedRecipe) => {
    setListing(false);
    setRun(openRun(recipe, catalog, view));
  };

  return (
    <div className="relative mb-2.5 w-full" aria-label="Your recipes" data-capability="recipes.mine">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-0.5 text-[11px] font-semibold tracking-wider text-ink-dim uppercase">Recipes</span>
        {recent.map((recipe) => (
          <Tip key={recipe.id} tip={why(recipe) || recipeMeta(recipe)}>
            <button
              type="button"
              disabled={held || !!why(recipe)}
              onClick={() => open(recipe)}
              className="inline-flex max-w-[13rem] items-center gap-1.5 rounded-full border border-accent/30 bg-raised py-1 pr-3 pl-1.5 text-[12.5px] font-medium text-ink transition-colors hover:border-accent hover:bg-accent/10 disabled:pointer-events-none disabled:opacity-45"
            >
              <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/20 text-accent">
                <Play />
              </span>
              <span className="truncate">{recipe.name}</span>
            </button>
          </Tip>
        ))}
        <button
          type="button"
          aria-expanded={listing}
          disabled={held}
          onClick={() => setListing(!listing)}
          className="rounded-full border border-edge px-3 py-1 text-[12.5px] text-ink-dim transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-45"
        >
          All recipes {listing ? "▴" : "▾"}
        </button>
      </div>

      {listing && (
        <>
          <div className="fixed inset-0 z-40" aria-hidden onClick={() => setListing(false)} />
          <div
            role="dialog"
            aria-label="All recipes"
            className="absolute bottom-full left-0 z-50 mb-2 max-h-[60vh] w-full overflow-y-auto rounded-2xl border border-edge bg-surface p-2 shadow-2xl"
            onKeyDown={(e) => e.key === "Escape" && setListing(false)}
          >
            {all.map((recipe) => {
              const blocked = why(recipe);
              return (
                <Tip key={recipe.id} tip={blocked || recipe.note || recipeMeta(recipe)} className="flex w-full">
                  <button
                    type="button"
                    disabled={!!blocked}
                    onClick={() => open(recipe)}
                    className="flex w-full items-start gap-2.5 rounded-xl px-2.5 py-2 text-left transition-colors hover:bg-raised disabled:opacity-50"
                  >
                    <span className={`mt-0.5 flex-none rounded-full border px-1.5 py-px text-[9.5px] font-bold tracking-wide ${BADGE[recipe.mode]}`}>
                      {MODE_BADGE[recipe.mode]}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[13.5px] font-semibold break-words text-ink">{recipe.name}</span>
                      <span className="block text-xs text-ink-dim">{blocked || recipeMeta(recipe)}</span>
                    </span>
                  </button>
                </Tip>
              );
            })}
            <div className="mt-1 border-t border-edge px-2.5 pt-2 pb-1">
              <button
                type="button"
                onClick={() => {
                  setListing(false);
                  onManage();
                }}
                className="text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
                data-capability="recipes.manage"
              >
                Manage recipes…
              </button>
            </div>
          </div>
        </>
      )}

      {running && run && (
        <ModalShell title="Run a recipe" onClose={() => setRun(null)} wide>
          <RecipeRunForm
            recipe={running}
            catalog={catalog}
            view={view}
            busy={busy}
            run={run}
            onRunChange={setRun}
            onPreview={(picks) => {
              setRun(null);
              onRun(running, picks);
            }}
            onSaveForms={(forms) => onSaveForms(running, forms)}
            tourActive={tourActive}
          />
        </ModalShell>
      )}
    </div>
  );
}
