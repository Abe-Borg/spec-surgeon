import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Change, Plan, PlanGroup, Target } from "../types";
import type { ActionButton } from "../lib/action";
import { groupTitle, stagesNote } from "../lib/workbench";
import { primaryBtn } from "./ModalShell";
import Tip from "./Tip";

/** True for 600 ms after `key` changes: the changed-block tint, then it settles. */
export function useFlash(key: unknown): boolean {
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (key === null || key === undefined) return;
    setFlash(true);
    const timer = window.setTimeout(() => setFlash(false), 600);
    return () => window.clearTimeout(timer);
  }, [key]);
  return flash;
}

/** The one button of the workbench, in the plan card's top right (or the
 *  action bar when no plan card exists). "Stop now" takes its place while busy. */
export function ActionSpot({
  button,
  onAct,
  onStop,
}: {
  button: ActionButton | null;
  onAct: (token: string) => void;
  onStop: () => void;
}) {
  if (!button) return null;
  if (button.kind === "stop") {
    return (
      <Tip tip={button.tip}>
        <button type="button" className={primaryBtn} onClick={onStop}>
          {button.label}
        </button>
      </Tip>
    );
  }
  return (
    <Tip tip={button.tip}>
      <button
        type="button"
        className={primaryBtn}
        disabled={button.disabled}
        onClick={() => onAct(button.token)}
      >
        {button.label}
      </button>
    </Tip>
  );
}

/** A paper card: the workbench's content surface. */
export function PaperCard({
  title,
  subtitle,
  aside,
  tone,
  flash,
  children,
}: {
  title: string;
  subtitle?: string;
  /** What sits at the card's top right (the action button). */
  aside?: ReactNode;
  tone?: "error" | "muted";
  flash?: boolean;
  children?: ReactNode;
}) {
  const toneClass =
    tone === "error" ? "border-err/50 bg-[#fbeeec]" : tone === "muted" ? "" : "";
  return (
    <section
      className={`paper card-in px-5 py-4 ${toneClass} ${flash ? "changed-block" : ""}`}
      aria-label={title}
    >
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

function ChangeRow({ change }: { change: Change }) {
  const details = change.details?.filter((d) => d && d.trim()) ?? [];
  return (
    <li className="py-2.5 first:pt-0 last:pb-0">
      {change.title && (
        <p className="text-[11px] font-medium tracking-wide text-paper-dim uppercase">{change.title}</p>
      )}
      <p className="mt-0.5 text-[0.925rem] leading-relaxed text-paper-ink">{change.summary}</p>
      {details.length > 0 && (
        <details className="group mt-1">
          <summary className="cursor-pointer list-none text-[11px] text-paper-dim transition-colors hover:text-accent">
            <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
            Every setting
          </summary>
          <ul className="mt-1 ml-3 space-y-0.5 border-l-2 border-paper-edge pl-3 text-[12px] leading-relaxed text-paper-dim">
            {details.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </li>
  );
}

function GroupBody({ group, several }: { group: PlanGroup; several: boolean }) {
  const files = group.files ?? [];
  return (
    <>
      {group.changes.length > 0 ? (
        <ul className="divide-y divide-paper-edge">
          {group.changes.map((change, index) => (
            <ChangeRow key={`${index}-${change.title}-${change.summary}`} change={change} />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-paper-dim">No change is planned for these documents.</p>
      )}
      {group.assumptions.length > 0 && (
        <ul className="mt-3 space-y-1">
          {group.assumptions.map((assumption, index) => (
            <li key={`${index}-${assumption}`} className="text-[12px] leading-relaxed text-paper-dim">
              <span className="font-medium">Assuming:</span> {assumption}
            </li>
          ))}
        </ul>
      )}
      {several && files.length > 0 && (
        <details className="group mt-3">
          <summary className="cursor-pointer list-none text-[11px] text-paper-dim transition-colors hover:text-accent">
            <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
            {files.length === 1 ? "The document" : `The ${files.length} documents`}
          </summary>
          <ul className="mt-1 ml-3 space-y-0.5 border-l-2 border-paper-edge pl-3 text-[12px] text-paper-dim">
            {files.map((file) => (
              <li key={file} className="truncate" title={file}>
                {file.split(/[\\/]/).pop()}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function Targets({ targets }: { targets: Target[] }) {
  if (targets.length === 0) return null;
  return (
    <details className="group mt-3 border-t border-paper-edge pt-3">
      <summary className="cursor-pointer list-none text-[12px] text-paper-dim transition-colors hover:text-accent">
        <span className="mr-1 inline-block transition-transform group-open:rotate-90">▸</span>
        Where exactly
        <span className="ml-1 text-paper-faint">— the passages each document contributes</span>
      </summary>
      <ul className="mt-2 space-y-2">
        {targets.map((target, index) => (
          <li key={`${index}-${target.title}`} className="text-[12px] leading-relaxed">
            <p className="font-medium text-paper-ink">{target.title}</p>
            {target.details?.map((line, i) => (
              <p key={`${i}-${line}`} className="text-paper-dim">
                {line}
              </p>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}

interface Props {
  plan: Plan;
  button: ActionButton | null;
  onAct: (token: string) => void;
  onStop: () => void;
}

/**
 * "Here's what will change": one sentence per change, every setting on
 * demand, the assumptions beneath, and the ONE filled button at the top
 * right. A mixed batch shows one card per layout, with the button on the
 * line that introduces them.
 */
export default function PlanCards({ plan, button, onAct, onStop }: Props) {
  // Keyed by content, not identity: the view that ends a job and the state
  // fetched after it carry the same plan as new objects.
  const planKey = useMemo(() => JSON.stringify(plan), [plan]);
  const flash = useFlash(planKey);
  const groups = plan.groups ?? [];
  const several = groups.length > 1;
  const note = stagesNote(plan);
  const spot = <ActionSpot button={button} onAct={onAct} onStop={onStop} />;

  if (groups.length === 0) {
    return (
      <PaperCard title="Here’s what will change" aside={spot} flash={flash}>
        <p className="text-sm text-paper-dim">The plan has no changes yet.</p>
        <Targets targets={plan.targets ?? []} />
      </PaperCard>
    );
  }

  if (!several) {
    return (
      <PaperCard
        title={groupTitle(groups[0], false)}
        subtitle={note || undefined}
        aside={spot}
        flash={flash}
      >
        <GroupBody group={groups[0]} several={false} />
        <Targets targets={plan.targets ?? []} />
      </PaperCard>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-4 px-1">
        <div>
          <h2 className="font-[family-name:var(--font-display)] text-[1.05rem] font-semibold text-ink">
            Your documents use {groups.length} layouts
          </h2>
          <p className="mt-0.5 text-xs text-ink-dim">Each layout gets its own plan; one decision covers them all.</p>
          {note && <p className="mt-0.5 text-[11px] text-ink-faint">{note}</p>}
        </div>
        <div className="shrink-0">{spot}</div>
      </div>
      {groups.map((group, index) => (
        <PaperCard key={group.id || `${index}-${group.label}`} title={groupTitle(group, true)} flash={flash}>
          <GroupBody group={group} several />
        </PaperCard>
      ))}
      {(plan.targets?.length ?? 0) > 0 && (
        <PaperCard title="Where exactly" subtitle="The passages each document contributes.">
          <ul className="space-y-2">
            {plan.targets.map((target, index) => (
              <li key={`${index}-${target.title}`} className="text-[12px] leading-relaxed">
                <p className="font-medium text-paper-ink">{target.title}</p>
                {target.details?.map((line, i) => (
                  <p key={`${i}-${line}`} className="text-paper-dim">
                    {line}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </PaperCard>
      )}
    </div>
  );
}
