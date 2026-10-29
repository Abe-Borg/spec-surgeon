import { memo, useEffect, useMemo, useState, type Dispatch, type ReactNode, type SetStateAction } from "react";
import type { Recipe, RecipeInputs, SavedRecipe, View } from "../types";
import {
  availability,
  clearForm,
  fieldRows,
  grouped,
  leaveForm,
  missing,
  openForm,
  payload,
  pictures,
  refreshInputs,
  tables,
  type SafeFormState,
} from "../lib/recipes";
import { SWITCH_TO_DEFAULT, safeNoteWords } from "../lib/safeNote";
import { ranLabel, timesRun } from "../lib/workbench";
import { TOUR_HOLD } from "../lib/tour";
import { primaryBtn } from "./ModalShell";
import type { RecipeRunPick } from "../lib/api";
import { openRun, recipeBlocked, recipeMeta, safeRecipes, stepShort, type RecipeRunState } from "../lib/savedRecipes";
import { FieldInput } from "./RecipeFields";
import RecipeRunForm from "./RecipeRunForm";
import SafeNote from "./SafeNote";
import Tip from "./Tip";

interface Props {
  recipes: Recipe[];
  view: Pick<View, "attachments" | "template" | "stage" | "working" | "saved_recipes">;
  busy: boolean;
  /** Previews the button's change: a job with no AI call (docs/WEB_API.md, `POST /api/recipe`). */
  onPreview: (recipe: Recipe, inputs: RecipeInputs) => void;
  /** Withdraws the preview waiting for approval (docs/WEB_API.md, `POST /api/discard`). */
  onDiscard: () => void;
  form: SafeFormState;
  onFormChange: Dispatch<SetStateAction<SafeFormState>>;
  /** The note above the buttons (SafeNote): whether a key is set, whether it is folded, and its three ways out. */
  keyReady: boolean;
  noteHidden: boolean;
  tourActive: boolean;
  onNoteHidden: (hidden: boolean) => void;
  onDefaultMode: () => void;
  onAddKey: () => void;
  /** A saved recipe previewed as one plan (`POST /api/saved-recipes/run`); its changed forms kept in it; the dialog
   *  that edits, duplicates and deletes recipes. */
  onRunRecipe: (recipe: SavedRecipe, picks: RecipeRunPick[], rev?: string) => void;
  onSaveRecipeForms: (recipe: SavedRecipe, forms: (RecipeInputs | null)[], rev?: string) => Promise<string | null>;
  onManageRecipes: () => void;
}

export type { SafeFormState };

/** One of Safe mode's buttons: a tile that fills its column, the accent and an arrow on hover. */
const tileClass =
  "group/tile flex h-full min-h-11 w-full items-center gap-2 rounded-xl border border-edge bg-raised px-3.5 py-2 " +
  "text-left text-[13px] leading-snug font-medium text-ink shadow-sm transition-[color,background-color,border-color,box-shadow,transform] " +
  "duration-150 ease-[var(--ease-glide)] hover:-translate-y-px hover:border-accent hover:bg-accent/10 hover:text-ink " +
  "hover:shadow-md focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 " +
  "focus-visible:outline-accent active:translate-y-0 disabled:pointer-events-none disabled:opacity-40 disabled:shadow-none";

function Chevron() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden
      className="flex-none -translate-x-1 text-accent opacity-0 transition-[opacity,transform] duration-150 group-hover/tile:translate-x-0 group-hover/tile:opacity-100 group-focus-visible/tile:translate-x-0 group-focus-visible/tile:opacity-100">
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

/** Each group's mark beside its name; a group the server adds later gets the plain one. */
const GROUP_ICONS: Record<string, ReactNode> = {
  "Headers & footers": (
    <>
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8 7h8M8 17h8" />
    </>
  ),
  "Fonts & formatting": <path d="M5 7V5h14v2M12 5v14M9 19h6" />,
  Text: <path d="M4 6h16M4 10h16M4 14h10M4 18h7" />,
  "Clean up": (
    <>
      <path d="M11 3l1.7 4.3L17 9l-4.3 1.7L11 15l-1.7-4.3L5 9l4.3-1.7z" />
      <path d="M18 14l.9 2.1L21 17l-2.1.9L18 20l-.9-2.1L15 17l2.1-.9z" />
    </>
  ),
  "Files & properties": (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </>
  ),
};

function GroupIcon({ group }: { group: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      {GROUP_ICONS[group] ?? <rect x="5" y="5" width="14" height="14" rx="3" />}
    </svg>
  );
}

/** A saved recipe's mark: layers, one over the next. */
function RecipeIcon({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="m12 3 9 5-9 5-9-5z" />
      <path d="m3 13 9 5 9-5" />
    </svg>
  );
}

/**
 * My recipes, above the buttons: each Safe-mode recipe the user saved as a tile with its steps in order, greyed with the
 * reason while what it needs is not attached; an empty list says where recipes come from. Manage opens the dialog.
 */
function MyRecipes({
  mine,
  catalog,
  view,
  busy,
  tourActive,
  onOpen,
  onManage,
}: {
  mine: SavedRecipe[];
  catalog: Recipe[];
  view: Pick<View, "attachments" | "template">;
  busy: boolean;
  tourActive: boolean;
  onOpen: (recipe: SavedRecipe) => void;
  onManage: () => void;
}) {
  return (
    <section aria-label="My recipes" data-tour="safe-recipes" data-capability="recipes.mine">
      <div className="mb-2.5 flex items-center gap-2.5">
        <span className="flex h-6 w-6 flex-none items-center justify-center rounded-md bg-accent/15 text-accent">
          <RecipeIcon size={14} />
        </span>
        <h3 className="text-[11.5px] font-semibold tracking-wider text-ink-dim uppercase">
          My recipes{mine.length ? ` · ${mine.length}` : ""}
        </h3>
        <span aria-hidden className="h-px min-w-0 flex-1 bg-edge" />
        <Tip tip={tourActive ? TOUR_HOLD : "Edit, duplicate or delete your recipes"}>
          <button
            type="button"
            disabled={busy || tourActive}
            onClick={onManage}
            className="text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
            data-capability="recipes.manage"
          >
            Manage
          </button>
        </Tip>
      </div>
      {mine.length === 0 ? (
        <p className="text-[12.5px] leading-snug text-ink-dim">
          Run some buttons, then <span className="text-ink">Save as a recipe…</span> under What you’ve done so far: the
          buttons and what you filled in become one recipe here, to run on other documents in one preview.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {mine.map((recipe) => {
            const why = recipeBlocked(recipe, catalog, view);
            const blocked = !!why;
            return (
              <Tip key={recipe.id} tip={busy ? "Wait for the current work to finish" : why || recipe.note || "Opens its steps; nothing runs until you preview"}>
                <button
                  type="button"
                  disabled={busy || blocked}
                  onClick={() => onOpen(recipe)}
                  className="group/tile flex w-full items-start gap-3 rounded-[14px] border border-accent/30 bg-raised px-3.5 py-3 text-left shadow-sm transition-[border-color,background-color,box-shadow,transform] duration-150 ease-[var(--ease-glide)] hover:-translate-y-px hover:border-accent hover:bg-accent/10 hover:shadow-md focus-visible:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-45 disabled:shadow-none"
                >
                  <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[10px] bg-accent/20 text-accent">
                    <RecipeIcon />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-sm font-semibold break-words text-ink">{recipe.name}</span>
                    <span className="mt-0.5 block text-xs text-ink-dim">{blocked ? why : recipeMeta(recipe)}</span>
                    <span className="mt-2 flex flex-wrap items-center gap-1">
                      {recipe.steps.map((step, i) => (
                        <span key={i} className="flex items-center gap-1">
                          {i > 0 && <span aria-hidden className="text-[11px] text-ink-faint">›</span>}
                          <span className="inline-flex items-center gap-1.5 rounded-full border border-edge bg-surface py-0.5 pr-2 pl-0.5 text-[11.5px] text-ink-dim">
                            <span className="flex h-[15px] w-[15px] items-center justify-center rounded-full bg-accent/20 text-[9.5px] font-bold text-accent">
                              {i + 1}
                            </span>
                            {stepShort(step)}
                          </span>
                        </span>
                      ))}
                    </span>
                  </span>
                  <span className="flex flex-none items-center gap-1 self-center text-[12.5px] font-semibold text-accent">
                    Run
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M9 6l6 6-6 6" />
                    </svg>
                  </span>
                </button>
              </Tip>
            );
          })}
        </div>
      )}
    </section>
  );
}

/**
 * Safe mode's buttons, where the composer is in Default mode: each common change in a group, a short form for the
 * one chosen, and Preview. No AI call is made: the server builds the change from the form (recipes.py) and the
 * previews, the checks and the one button that creates the copies are the same as in Default mode.
 */
function SafePanel({
  recipes,
  view,
  busy,
  onPreview,
  onDiscard,
  form,
  onFormChange,
  keyReady,
  noteHidden,
  tourActive,
  onNoteHidden,
  onDefaultMode,
  onAddKey,
  onRunRecipe,
  onSaveRecipeForms,
  onManageRecipes,
}: Props) {
  const chosen = recipes.find((recipe) => recipe.id === form.recipe) ?? null;
  const inputs = form.inputs;
  const groups = useMemo(() => grouped(recipes), [recipes]);
  const mine = safeRecipes(view);
  // a saved recipe's run form, kept by the panel while it is open (All buttons closes it)
  const [run, setRun] = useState<RecipeRunState | null>(null);
  const running = run ? mine.find((r) => r.id === run.id) ?? null : null;

  // A form opened with what was attached then; a template, picture or table attached or removed since is taken
  // into its fields, and a file no longer attached is never sent.
  const slotsKey = (view.template?.typed_slots ?? []).join("\n") + JSON.stringify(view.template?.body?.roles ?? {});
  const filesKey = [...pictures(view), ...tables(view)].map((f) => f.path).join("\n");
  useEffect(() => {
    if (chosen) onFormChange((current) => current.recipe === chosen.id
      ? { ...current, inputs: refreshInputs(chosen, current.inputs, view) }
      : current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotsKey, filesKey, chosen?.id]);

  const open = (recipe: Recipe) => onFormChange((current) => openForm(current, recipe, view));
  // Back to all the buttons keeps what this form holds; a preview waiting for approval is withdrawn with it, so no
  // Create is left standing for a form that is closed.
  const waiting = view.stage === "preview";
  const back = () => {
    if (waiting) onDiscard();
    onFormChange(leaveForm);
  };
  const change = (name: string, value: RecipeInputs[string]) => onFormChange((current) => ({
    ...current, inputs: { ...current.inputs, [name]: value },
  }));

  if (running && run) {
    return (
      <RecipeRunForm
        recipe={running}
        catalog={recipes}
        view={view}
        busy={busy}
        run={run}
        onRunChange={setRun}
        onPreview={(picks) => onRunRecipe(running, picks, run.rev)}
        onSaveForms={(forms) => onSaveRecipeForms(running, forms, run.rev)}
        onBack={() => {
          if (view.stage === "preview") onDiscard();
          setRun(null);
        }}
        tourActive={tourActive}
      />
    );
  }

  if (chosen) {
    const ready = availability(chosen, view);
    const needs = ready.ready ? missing(chosen, inputs) : ready.reason;
    return (
      <div className="flex flex-col gap-2.5" aria-label={chosen.label} data-tour="safe-form" data-capability="safe.form">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-ink">{chosen.label}</h2>
            <p className="mt-0.5 text-[13px] leading-snug text-ink-dim">{chosen.summary}</p>
          </div>
          <div className="flex shrink-0 items-center gap-3">
            <Tip tip="Empties this form, as it was when it first opened">
              <button
                type="button"
                onClick={() => onFormChange((current) => clearForm(current, chosen, view))}
                className="text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
              >
                Clear form
              </button>
            </Tip>
            <Tip tip={waiting ? "Discards the preview and shows every button; this form keeps what you typed" : "Shows every button; this form keeps what you typed"}>
              <button
                type="button"
                disabled={busy}
                onClick={back}
                className="text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
              >
                All buttons
              </button>
            </Tip>
          </div>
        </div>
        {fieldRows(chosen.fields).map((row) =>
          row.length === 1 ? (
            <FieldInput key={row[0].name} recipe={chosen} field={row[0]} inputs={inputs} view={view} onChange={change} />
          ) : (
            <div key={row[0].name} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {row.map((field) => (
                <FieldInput key={field.name} recipe={chosen} field={field} inputs={inputs} view={view} onChange={change} />
              ))}
            </div>
          ),
        )}
        <div className="flex items-center justify-between gap-3">
          <p className="min-w-0 flex-1 text-[13px] leading-snug text-ink-dim">
            {needs || "Nothing changes until you look at the previews and create the copies."}
          </p>
          <Tip tip={busy ? "Wait for the current work to finish" : needs || "Shows the pages before and after; no copy is made yet"}>
            <button
              type="button"
              className={primaryBtn}
              disabled={busy || !!needs}
              onClick={() => onPreview(chosen, payload(chosen, inputs))}
            >
              Preview
            </button>
          </Tip>
        </div>
      </div>
    );
  }

  const tail = safeNoteWords(keyReady).tail;
  return (
    <div className="flex flex-col gap-7 pb-3">
      <SafeNote
        keyReady={keyReady}
        hidden={noteHidden}
        busy={busy}
        tourActive={tourActive}
        onHidden={onNoteHidden}
        onDefaultMode={onDefaultMode}
        onAddKey={onAddKey}
      />
      <MyRecipes
        mine={mine}
        catalog={recipes}
        view={view}
        busy={busy}
        tourActive={tourActive}
        onOpen={(recipe) => setRun(openRun(recipe, recipes, view))}
        onManage={onManageRecipes}
      />
      <div className="flex flex-col gap-6" aria-label="Safe mode's buttons" data-tour="safe-buttons" data-capability="safe.buttons">
        {groups.map(({ group, recipes: items }) => (
          <section key={group} aria-label={group}>
            <div className="mb-2.5 flex items-center gap-2.5">
              <span className="flex h-6 w-6 flex-none items-center justify-center rounded-md bg-accent/15 text-accent">
                <GroupIcon group={group} />
              </span>
              <h3 className="text-[11.5px] font-semibold tracking-wider text-ink-dim uppercase">{group}</h3>
              <span aria-hidden className="h-px min-w-0 flex-1 bg-edge" />
            </div>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(12.5rem,1fr))] gap-2">
              {items.map((recipe) => {
                const ready = availability(recipe, view);
                const ran = timesRun(view, recipe.id);
                return (
                  <Tip
                    key={recipe.id}
                    tip={busy ? "Wait for the current work to finish" : `${ready.reason || recipe.summary}${ran ? ` ${ranLabel(ran)}` : ""}`}
                    className="h-full"
                  >
                    <button type="button" disabled={busy || !ready.ready} onClick={() => open(recipe)} className={tileClass}>
                      <span className="min-w-0 flex-1">{recipe.label}</span>
                      {ran > 0 && (
                        <span
                          role="img"
                          aria-label={ranLabel(ran)}
                          className="flex h-[18px] w-[18px] flex-none items-center justify-center rounded-full border border-ok/50 bg-ok/10 text-ok"
                        >
                          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                            <path d="M20 6 9 17l-5-5" />
                          </svg>
                        </span>
                      )}
                      <Chevron />
                    </button>
                  </Tip>
                );
              })}
            </div>
          </section>
        ))}
      </div>
      <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-t border-edge pt-4 text-[12.5px] text-ink-faint">
        <span>{tail}</span>
        <Tip tip={tourActive ? TOUR_HOLD : busy ? "Wait for the current work to finish" : "The buttons give way to the message box"}>
          <button
            type="button"
            disabled={busy || tourActive}
            onClick={onDefaultMode}
            className="text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
            data-capability="safe.default"
          >
            {SWITCH_TO_DEFAULT}
          </button>
        </Tip>
      </p>
    </div>
  );
}

/** The fields the user already filled that still exist in the form. */
export default memo(SafePanel);
