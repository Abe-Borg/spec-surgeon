import { Fragment, memo, useEffect, useMemo, useState } from "react";
import type { FindReplacePair, Recipe, RecipeField, RecipeInputs, View } from "../types";
import { availability, fieldShown, grouped, initialInputs, missing, payload, pictures, refreshInputs, tables } from "../lib/recipes";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  recipes: Recipe[];
  view: Pick<View, "attachments" | "template">;
  busy: boolean;
  /** Previews the button's change: a job with no AI call (docs/WEB_API.md, `POST /api/recipe`). */
  onPreview: (recipe: Recipe, inputs: RecipeInputs) => void;
}

const inputClass =
  "w-full rounded-lg border border-edge bg-raised px-2.5 py-1.5 text-sm text-ink outline-none transition-colors " +
  "placeholder:text-ink-faint focus:border-accent";

function Label({ field }: { field: RecipeField }) {
  return <span className="mb-1 block text-[12px] font-medium text-ink-dim">{field.label}</span>;
}

function Help({ text }: { text?: string }) {
  return text ? <span className="mt-1 block text-[11px] leading-snug text-ink-faint">{text}</span> : null;
}

function Pairs({ value, onChange }: { value: FindReplacePair[]; onChange: (pairs: FindReplacePair[]) => void }) {
  const set = (i: number, key: keyof FindReplacePair, text: string) =>
    onChange(value.map((p, j) => (j === i ? { ...p, [key]: text } : p)));
  return (
    <div className="flex flex-col gap-1.5">
      {value.map((pair, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input
            aria-label={`Find, row ${i + 1}`}
            className={inputClass}
            value={pair.find}
            placeholder="Find"
            onChange={(e) => set(i, "find", e.target.value)}
          />
          <span aria-hidden className="text-ink-faint">
            →
          </span>
          <input
            aria-label={`Replace with, row ${i + 1}`}
            className={inputClass}
            value={pair.replace}
            placeholder="Replace with (empty deletes)"
            onChange={(e) => set(i, "replace", e.target.value)}
          />
          <button
            type="button"
            aria-label={`Remove row ${i + 1}`}
            disabled={value.length === 1}
            onClick={() => onChange(value.filter((_, j) => j !== i))}
            className="rounded px-1.5 text-ink-faint transition-colors hover:text-ink disabled:opacity-30"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...value, { find: "", replace: "" }])}
        className="self-start text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      >
        Add a row
      </button>
    </div>
  );
}

function FieldInput({
  recipe,
  field,
  inputs,
  view,
  onChange,
}: {
  recipe: Recipe;
  field: RecipeField;
  inputs: RecipeInputs;
  view: Pick<View, "attachments" | "template">;
  onChange: (name: string, value: RecipeInputs[string]) => void;
}) {
  if (!fieldShown(recipe, field, inputs)) return null;
  const value = inputs[field.name];
  switch (field.kind) {
    case "text":
      return (
        <label className="block">
          <Label field={field} />
          <input
            className={inputClass}
            value={String(value ?? "")}
            placeholder={field.placeholder}
            onChange={(e) => onChange(field.name, e.target.value)}
          />
          <Help text={field.help} />
        </label>
      );
    case "choice":
      return (
        <label className="block">
          <Label field={field} />
          <select className={inputClass} value={String(value ?? "")} onChange={(e) => onChange(field.name, e.target.value)}>
            {(field.options ?? []).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <Help text={field.help} />
        </label>
      );
    case "check":
      return (
        <label className="flex items-start gap-2 text-sm text-ink">
          <input
            type="checkbox"
            className="mt-1 accent-[var(--color-accent)]"
            checked={value === true}
            onChange={(e) => onChange(field.name, e.target.checked)}
          />
          <span>
            {field.label}
            <Help text={field.help} />
          </span>
        </label>
      );
    case "picture":
    case "table": {
      const files = field.kind === "picture" ? pictures(view) : tables(view);
      const none = field.kind === "picture" ? field.optional_choice : "No table";
      return (
        <label className="block">
          <Label field={field} />
          <select className={inputClass} value={String(value ?? "")} onChange={(e) => onChange(field.name, e.target.value)}>
            {(!field.required || files.length === 0) && <option value="">{none || "Choose…"}</option>}
            {files.map((f) => (
              <option key={f.path} value={f.path}>
                {f.name}
              </option>
            ))}
          </select>
          <Help text={field.help} />
        </label>
      );
    }
    case "pairs":
      return (
        <div>
          <Label field={field} />
          <Pairs value={Array.isArray(value) ? (value as FindReplacePair[]) : []} onChange={(p) => onChange(field.name, p)} />
        </div>
      );
    case "slots": {
      const slots = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string>) : {};
      const names = Object.keys(slots);
      if (names.length === 0) {
        return (
          <p className="text-[12px] leading-relaxed text-ink-faint">
            {view.template?.kind === "reference"
              ? "Your template is an ordinary specification: its own section number and title become each document's own."
              : "Each document's own section number and title fill the template's placeholders."}
          </p>
        );
      }
      return (
        <div className="flex flex-col gap-2">
          <p className="text-[12px] leading-relaxed text-ink-faint">
            The section number and title come from each document. Type the template's other values exactly as they
            should appear:
          </p>
          {names.map((slot) => (
            <label key={slot} className="block">
              <span className="mb-1 block text-[12px] font-medium text-ink-dim">“{slot}”</span>
              <input
                className={inputClass}
                value={slots[slot]}
                onChange={(e) => onChange(field.name, { ...slots, [slot]: e.target.value })}
              />
            </label>
          ))}
        </div>
      );
    }
    case "roles": {
      const body = view.template?.body;
      if (!body || body.order.length === 0) return null;
      const chosen = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string>) : {};
      const from = [body.page && `pages ${body.page}`, body.font && `text in ${body.font}`].filter(Boolean).join("; ");
      return (
        <div>
          <Label field={field} />
          <div className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] items-center gap-x-3 gap-y-1.5">
            {body.order.map((role) => (
              <Fragment key={role}>
                <span className="text-[12px] text-ink-dim">{body.labels[role] ?? role}</span>
                <select
                  aria-label={body.labels[role] ?? role}
                  className={inputClass}
                  value={chosen[role] ?? ""}
                  onChange={(e) => onChange(field.name, { ...chosen, [role]: e.target.value })}
                >
                  <option value="">(none)</option>
                  {body.styles.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </Fragment>
            ))}
          </div>
          <Help text={field.help} />
          {from && <Help text={`From your template: ${from}.`} />}
        </div>
      );
    }
    default:
      return null;
  }
}

/**
 * Safe mode's buttons, where the composer is in Default mode: each common change in a group, a short form for the
 * one chosen, and Preview. No AI call is made: the server builds the change from the form (recipes.py) and the
 * previews, the checks and the one button that creates the copies are the same as in Default mode.
 */
function SafePanel({ recipes, view, busy, onPreview }: Props) {
  const [chosen, setChosen] = useState<Recipe | null>(null);
  const [inputs, setInputs] = useState<RecipeInputs>({});
  const groups = useMemo(() => grouped(recipes), [recipes]);

  // A form opened with what was attached then; a template, picture or table attached or removed since is taken
  // into its fields, and a file no longer attached is never sent.
  const slotsKey = (view.template?.typed_slots ?? []).join("\n") + JSON.stringify(view.template?.body?.roles ?? {});
  const filesKey = [...pictures(view), ...tables(view)].map((f) => f.path).join("\n");
  useEffect(() => {
    if (chosen) setInputs((current) => refreshInputs(chosen, current, view));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slotsKey, filesKey]);

  const open = (recipe: Recipe) => {
    setChosen(recipe);
    setInputs(initialInputs(recipe, view));
  };
  const change = (name: string, value: RecipeInputs[string]) => setInputs((current) => ({ ...current, [name]: value }));

  if (chosen) {
    const ready = availability(chosen, view);
    const needs = ready.ready ? missing(chosen, inputs) : ready.reason;
    return (
      <div className="flex flex-col gap-3" aria-label={chosen.label}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-sm font-semibold text-ink">{chosen.label}</h2>
            <p className="mt-0.5 text-[12px] leading-snug text-ink-dim">{chosen.summary}</p>
          </div>
          <button
            type="button"
            onClick={() => setChosen(null)}
            className="shrink-0 text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
          >
            All buttons
          </button>
        </div>
        {chosen.fields.map((field) => (
          <FieldInput key={field.name} recipe={chosen} field={field} inputs={inputs} view={view} onChange={change} />
        ))}
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] text-ink-faint">{needs || "Nothing changes until you look at the previews and create the copies."}</p>
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

  return (
    <div className="flex flex-col gap-3" aria-label="Safe mode's buttons">
      {groups.map(({ group, recipes: items }) => (
        <div key={group}>
          <p className="mb-1.5 text-[11px] font-medium tracking-wide text-ink-faint uppercase">{group}</p>
          <div className="flex flex-wrap gap-1.5">
            {items.map((recipe) => {
              const ready = availability(recipe, view);
              return (
                <Tip key={recipe.id} tip={busy ? "Wait for the current work to finish" : ready.reason || recipe.summary}>
                  <button
                    type="button"
                    disabled={busy || !ready.ready}
                    onClick={() => open(recipe)}
                    className="rounded-lg border border-edge bg-raised px-2.5 py-1 text-xs text-ink transition-colors hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40"
                  >
                    {recipe.label}
                  </button>
                </Tip>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

/** The fields the user already filled that still exist in the form. */
export default memo(SafePanel);
