/**
 * Export anyway (Obliterate mode alone; the owner's decision of 2026-11-10, docs/BACKLOG.md O7): what the Export menu
 * offers under its three kinds once a clean kind — Word (Tracked Changes OFF) or PDF (Clean) — has left a MARKED
 * copy without a file. Word makes that kind on a private copy with its own fixed commands; nothing is planned,
 * predicted or checked, so the file is unchecked and the chat names it so.
 *
 * The server decides what is offered (`view.export.anyway`: the mode, the marks, the copy as it was when the export
 * left it); this module only reads it defensively and writes the menu's words. Pure, so `node --test` can pin it.
 */
import type { ExportAnyway, View } from "../types.ts";

/** The heading of the menu's second group, and what it means in one breath. */
export const ANYWAY_HEAD = "Made by Word, unchecked";
export const ANYWAY_MEANS =
  "For marked copies the checked way left without a file. No check proves what Word makes; the chat says what Word did and what the app read in its file.";

const KINDS = ["accepted", "pdf"] as const;

/** The choices to show: the server's, in its order, in Obliterate mode alone, each well formed (a kind Word makes, a
 *  label, at least one copy) and a kind at most once. An older server, or any other mode, offers none. */
export function anywayChoices(view: Pick<View, "export"> & Partial<Pick<View, "mode">>): ExportAnyway[] {
  if (view.mode !== "obliterate") return [];
  const offered: unknown = view.export?.anyway;
  if (!Array.isArray(offered)) return [];
  const seen = new Set<string>();
  const out: ExportAnyway[] = [];
  for (const raw of offered as Partial<ExportAnyway>[]) {
    if (!raw || typeof raw !== "object") continue;
    const kind = raw.kind;
    if (!KINDS.includes(kind as (typeof KINDS)[number]) || seen.has(kind as string)) continue;
    const copies = Array.isArray(raw.copies) ? raw.copies.filter((c) => typeof c === "string" && c !== "") : [];
    if (typeof raw.label !== "string" || !raw.label || copies.length === 0) continue;
    seen.add(kind as string);
    out.push({
      kind: kind as ExportAnyway["kind"],
      label: raw.label,
      does: typeof raw.does === "string" ? raw.does : "",
      copies,
      blocked: typeof raw.blocked === "string" ? raw.blocked : "",
    });
  }
  return out;
}

/** The copies a choice is for, by name: all of them up to three, then the first two and how many more. */
export function anywayCopies(copies: string[]): string {
  if (copies.length <= 3) return copies.join(", ");
  return `${copies.slice(0, 2).join(", ")} and ${copies.length - 2} more`;
}

/** The line under a choice's label: which copies, and what Word does to a private copy of each. */
export function anywayDetail(choice: ExportAnyway): string {
  const what = choice.does ? ` ${choice.does}` : "";
  return `For ${anywayCopies(choice.copies)}.${what}`;
}
