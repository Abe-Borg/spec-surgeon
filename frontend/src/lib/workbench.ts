/**
 * The workbench's small rules: how a page count reads, where the run folder
 * is, what an attachment's tag says.
 *
 * Pure, so `node --test` can pin them.
 */
import type { Attachment, Plan, Sample, View } from "../types.ts";

/** The parent folder of a path, with either separator; "" when there is none. */
export function parentFolder(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  if (cut <= 0) return "";
  return trimmed.slice(0, cut);
}

/** The run folder: the parent of the first edited copy, or "" with no results. */
export function runFolder(view: Pick<View, "results">): string {
  const first = view.results?.[0]?.output ?? "";
  return first ? parentFolder(first) : "";
}

export interface PageCount {
  /** "Pages: 12 → 13 (+1)", or "" when a count is missing. */
  text: string;
  /** The growth flag, "+1" / "−2"; "" when unchanged or unknown. */
  flag: string;
  grew: boolean;
}

export function pageCount(sample: Pick<Sample, "pages_before" | "pages_after">): PageCount {
  const before = sample.pages_before;
  const after = sample.pages_after;
  if (typeof before !== "number" || typeof after !== "number") return { text: "", flag: "", grew: false };
  const change = after - before;
  const flag = change === 0 ? "" : change > 0 ? `+${change}` : `−${Math.abs(change)}`;
  const text = `Pages: ${before} → ${after}${flag ? ` (${flag})` : ""}`;
  return { text, flag, grew: change > 0 };
}

/** The plan has anything to show: a change, an assumption, a target or a file set aside. */
export function planHasContent(plan: Plan | null): boolean {
  if (!plan) return false;
  return (
    plan.groups.some((g) => g.changes.length > 0 || g.assumptions.length > 0) ||
    plan.targets.length > 0 ||
    plan.set_aside.length > 0
  );
}

/** The kind tag beside an attachment's name; documents carry none. */
export function kindTag(attachment: Pick<Attachment, "kind" | "name">): string {
  switch (attachment.kind) {
    case "template":
      return "Template";
    case "reference":
      return /\.(png|jpe?g|gif|bmp)$/i.test(attachment.name) ? "Picture" : "Table";
    default:
      return "";
  }
}

/** "Your documents · 3": the accepted Word documents, or the bare title. */
export function trayTitle(view: Pick<View, "attachments">): string {
  const n = view.attachments.filter((a) => a.kind === "document" && a.state !== "refused").length;
  return n > 0 ? `Your documents · ${n}` : "Your documents";
}

/** The file-input filter for each pick kind (the fallback when no native dialog exists). */
export const ACCEPT: Record<"documents" | "template" | "references", string> = {
  documents: ".docx",
  template: ".docx",
  references: ".png,.jpg,.jpeg,.gif,.bmp,.xlsx,.csv,.json",
};

/** The scope tag beside a document's name: waiting on the to-do list, or in the pass over it. */
export function scopeTag(
  attachment: Pick<Attachment, "path" | "kind">,
  view: Pick<View, "todo" | "focus">,
): "" | "To do" | "Working on" {
  if (attachment.kind !== "document") return "";
  if ((view.focus ?? []).includes(attachment.path)) return "Working on";
  if ((view.todo ?? []).some((item) => item.path === attachment.path)) return "To do";
  return "";
}

/** The tray's three sections, each with its own fold: the documents, the template, the pictures and tables. */
export type TraySection = "documents" | "template" | "references";

/** The attachments one section lists, refused ones included. */
export function sectionAttachments(section: TraySection, view: Pick<View, "attachments">): Attachment[] {
  const kind = section === "documents" ? "document" : section === "template" ? "template" : "reference";
  return view.attachments.filter((a) => a.kind === kind);
}

/** "Pictures and tables · 2": the accepted pictures and tables, or the bare title. */
export function referencesTitle(view: Pick<View, "attachments">): string {
  const n = view.attachments.filter((a) => a.kind === "reference" && a.state !== "refused").length;
  return n > 0 ? `Pictures and tables · ${n}` : "Pictures and tables";
}

/**
 * The one line a folded section shows: "3 documents · 1 left out · 1 to do",
 * the template's name, or "1 picture · 1 table"; "none" for an empty
 * template or pictures section, "" for no documents.
 */
export function traySummary(section: TraySection, view: Pick<View, "attachments" | "todo" | "focus">): string {
  const listed = sectionAttachments(section, view);
  const usable = listed.filter((a) => a.state !== "refused");
  const refused = listed.length - usable.length;
  const parts: string[] = [];
  if (section === "documents") {
    if (usable.length > 0) parts.push(`${usable.length} ${usable.length === 1 ? "document" : "documents"}`);
  } else if (section === "template") {
    if (usable.length > 0) parts.push(usable[0].name);
  } else {
    const pictures = usable.filter((a) => kindTag(a) === "Picture").length;
    const tables = usable.length - pictures;
    if (pictures > 0) parts.push(`${pictures} ${pictures === 1 ? "picture" : "pictures"}`);
    if (tables > 0) parts.push(`${tables} ${tables === 1 ? "table" : "tables"}`);
  }
  if (refused > 0) parts.push(`${refused} left out`);
  if (section === "documents") {
    if ((view.focus ?? []).length > 0) parts.push(`working on ${view.focus.length}`);
    else if ((view.todo ?? []).length > 0) parts.push(`${view.todo.length} to do`);
  } else if (parts.length === 0) {
    parts.push("none");
  }
  return parts.join(" · ");
}
