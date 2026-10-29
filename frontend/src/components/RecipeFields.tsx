import { Fragment } from "react";
import type { FindReplacePair, Recipe, RecipeField, RecipeInputs, View } from "../types";
import { fieldShown, filledFromEachDocument, pictures, tables } from "../lib/recipes";

/**
 * One field of a Safe-mode button's form, as the panel renders it (text, a choice, a checkbox, a picture or table among
 * the attached ones, find-and-replace pairs, list rows, a template's placeholders, a template's styles per role). Used
 * by a button's own form (SafePanel) and by a saved recipe's step when its form is changed for a run (RecipeRunForm).
 */
export const inputClass =
  "w-full rounded-lg border border-edge bg-raised px-2.5 py-1.5 text-sm text-ink outline-none transition-colors " +
  "placeholder:text-ink-faint focus:border-accent";

function Label({ field }: { field: RecipeField }) {
  return <span className="mb-1 block text-[12px] font-medium text-ink-dim">{field.label}</span>;
}

function Help({ text }: { text?: string }) {
  return text ? <span className="mt-1 block text-[13px] leading-snug text-ink-dim">{text}</span> : null;
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

function Lines({ label, value, placeholder, onChange }: {
  label: string;
  value: string[];
  placeholder?: string;
  onChange: (lines: string[]) => void;
}) {
  const rows = value.length ? value : [""];
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((line, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <input
            aria-label={`${label}, row ${i + 1}`}
            className={inputClass}
            value={line}
            placeholder={i === 0 ? placeholder : undefined}
            onChange={(e) => onChange(rows.map((l, j) => (j === i ? e.target.value : l)))}
          />
          <button
            type="button"
            aria-label={`Remove row ${i + 1}`}
            disabled={rows.length === 1}
            onClick={() => onChange(rows.filter((_, j) => j !== i))}
            className="rounded px-1.5 text-ink-faint transition-colors hover:text-ink disabled:opacity-30"
          >
            ✕
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...rows, ""])}
        className="self-start text-xs text-accent underline decoration-accent/40 underline-offset-2 hover:decoration-accent"
      >
        Add a row
      </button>
    </div>
  );
}

export function FieldInput({
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
    case "lines":
      return (
        <div>
          <Label field={field} />
          <Lines
            label={field.label}
            value={Array.isArray(value) ? (value as string[]).filter((l) => typeof l === "string") : []}
            placeholder={field.placeholder}
            onChange={(lines) => onChange(field.name, lines)}
          />
          <Help text={field.help} />
        </div>
      );
    case "slots": {
      const slots = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string>) : {};
      const names = Object.keys(slots);
      const filled = filledFromEachDocument(view.template);
      if (names.length === 0) {
        return (
          <p className="text-[13px] leading-snug text-ink-dim">
            {view.template?.kind === "reference"
              ? "Your template is an ordinary specification: its own section number and title become each document's own."
              : filled
                ? `${filled} Nothing to type.`
                : "Each document's own section number and title fill the template's placeholders."}
          </p>
        );
      }
      return (
        <div className="flex flex-col gap-2">
          <p className="text-[13px] leading-snug text-ink-dim">
            {filled
              ? `${filled} Type the template's other placeholders exactly as they should appear, the same in every copy:`
              : "No placeholder in your template is named for the section number or title (such as “00 00 00” or " +
                "“SECTION TITLE”), so none is filled from each document. Type each one exactly as it should appear, " +
                "the same in every copy:"}
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
          <div className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] items-center gap-x-3 gap-y-1">
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
