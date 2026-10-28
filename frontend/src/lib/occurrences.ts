/**
 * Find and replace, occurrence by occurrence (docs/WEB_API.md `occurrences`, `POST /api/occurrences`): the waiting
 * plan lists every occurrence per document; the page keeps the user's ticks until Update the preview sends the
 * whole unticked set, and the plan is made again with those occurrences left as they are. The ticks belong to one
 * preview (its token and its list) and start over when another arrives; until they are sent, the one button is held
 * back, because the plan and pages on screen still change the unticked ones.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { OccurrenceDoc, View } from "../types.ts";

/** The keys unticked on the page. */
export type Unticks = Record<string, true>;

/** The list a set of ticks belongs to: the action token and every key, in order. */
export function occurrencesKey(view: Pick<View, "occurrences" | "action">): string {
  return [view.action?.token ?? "", ...(view.occurrences ?? []).flatMap((d) => d.items.map((i) => i.key))].join("\n");
}

/** The ticks as the previewed plan has them: every occurrence the plan left as it is. */
export function previewedUnticks(view: Pick<View, "occurrences">): Unticks {
  const out: Unticks = {};
  for (const doc of view.occurrences ?? []) for (const item of doc.items) if (item.skipped) out[item.key] = true;
  return out;
}

/** Untick an occurrence, or tick an unticked one back. */
export function toggleUntick(unticks: Unticks, key: string): Unticks {
  const next = { ...unticks };
  if (next[key]) delete next[key];
  else next[key] = true;
  return next;
}

/** The unticked keys, in the list's order, among the occurrences shown. */
export function untickedKeys(unticks: Unticks, docs: OccurrenceDoc[]): string[] {
  return docs.flatMap((d) => d.items.map((i) => i.key)).filter((key) => unticks[key]);
}

/** The ticks on the page differ from the previewed plan's: Update the preview is due, and the one button waits. */
export function unticksPending(unticks: Unticks, view: Pick<View, "occurrences">): boolean {
  const docs = view.occurrences ?? [];
  const previewed = previewedUnticks(view);
  const shown = untickedKeys(unticks, docs);
  const was = untickedKeys(previewed, docs);
  return shown.length !== was.length || shown.some((key, i) => key !== was[i]);
}

export interface OccurrenceCounts {
  documents: number;
  total: number;
  unticked: number;
  refused: number;
}

export function occurrenceCounts(unticks: Unticks, docs: OccurrenceDoc[]): OccurrenceCounts {
  return {
    documents: docs.length,
    total: docs.reduce((n, d) => n + d.items.length, 0),
    unticked: untickedKeys(unticks, docs).length,
    refused: docs.filter((d) => d.status === "refused").length,
  };
}

/** The list can be worked: a plan with occurrences awaits approval (not a recovery's save) and nothing runs. */
export function canUntick(view: Pick<View, "action" | "occurrences">, busy: boolean): boolean {
  return !busy && view.action?.kind === "approve" && (view.occurrences ?? []).length > 0;
}

/** "the body, paragraph 12 · 2.01" — where an occurrence sits, for the list. */
export function placeLabel(item: { where: string; paragraph: number; heading: string }): string {
  const place = `${item.where}, paragraph ${item.paragraph + 1}`;
  return item.heading ? `${place} · ${item.heading}` : place;
}

export const UPDATE_TIP = "Makes the plan and the pages again with the unticked occurrences left as they are.";
export const UNTICKS_TIP = "Update the preview first: the plan and pages shown here still change the occurrences you unticked.";
