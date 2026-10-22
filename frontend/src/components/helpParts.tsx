// The help dialog's presentational helpers, on the existing palette: HelpModal's topics and Why trust it share them.
import type { ReactNode } from "react";

/* --- small presentational helpers, all on the existing palette --- */

export function Lead({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-dim">{children}</p>;
}

/** A label the window shows, set apart in the text. */
export function Tag({ children }: { children: ReactNode }) {
  return <span className="rounded bg-raised px-1.5 py-0.5 text-[12px] text-ink">{children}</span>;
}

/** Numbered, badge-led steps (getting started). */
export function Steps({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ol className="mt-3 space-y-3">
      {items.map((s, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
            {i + 1}
          </span>
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{s.t}</span>
            {s.d ? <> — {s.d}</> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Bulleted point list. */
export function Points({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ul className="mt-3 space-y-3">
      {items.map((p, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-accent" />
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{p.t}</span>
            {p.d ? <> — {p.d}</> : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** A titled workflow recipe card. */
export function Recipe({ title, tagline, steps }: { title: string; tagline: string; steps: ReactNode[] }) {
  return (
    <div className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">{title}</h4>
      <p className="mt-0.5 text-xs text-ink-faint">{tagline}</p>
      <ol className="mt-3 space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2.5 text-sm text-ink-dim">
            <span className="text-ink-faint tabular-nums">{i + 1}.</span>
            <span className="leading-relaxed">{s}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A bordered guide box with a title and a line under it. */
export function Guide({ title, tagline, children }: { title: string; tagline: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">{title}</h4>
      <p className="mt-0.5 text-xs text-ink-faint">{tagline}</p>
      {children}
    </section>
  );
}
