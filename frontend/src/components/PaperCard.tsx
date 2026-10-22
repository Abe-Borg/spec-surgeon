import type { ReactNode } from "react";

/** A paper card: the workbench's content surface. */
export default function PaperCard({
  title,
  subtitle,
  aside,
  tone,
  children,
}: {
  title: string;
  subtitle?: string;
  /** What sits at the card's top right (a pill, the review marks). */
  aside?: ReactNode;
  tone?: "error" | "muted";
  children?: ReactNode;
}) {
  const toneClass = tone === "error" ? "border-err/50 bg-err-tint" : "";
  return (
    <section className={`paper card-in px-5 py-4 ${toneClass}`} aria-label={title}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2
            className={`font-[family-name:var(--font-display)] text-[1.05rem] font-semibold leading-snug ${
              tone === "error" ? "text-err" : "text-paper-ink"
            }`}
          >
            {title}
          </h2>
          {subtitle && <p className="mt-0.5 text-xs text-paper-dim">{subtitle}</p>}
        </div>
        {aside && <div className="shrink-0">{aside}</div>}
      </div>
      {children && <div className="mt-3">{children}</div>}
    </section>
  );
}
