/**
 * The workbench's small rules: how a page count reads, where the run folder
 * is, what an attachment's tag says.
 *
 * Pure, so `node --test` can pin them.
 */
import type { Attachment, Plan, Result, Sample, View } from "../types.ts";

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

/** The Export button's label: "Export", with the count of working copies not saved yet when there are any. */
export function exportLabel(view: Pick<View, "export">): string {
  const unsaved = view.export?.unsaved ?? 0;
  return unsaved > 0 ? `Export · ${unsaved} unsaved` : "Export";
}

/** The Export button's hover text: what it does, and how many copies it would save; held still during the tour. */
export function exportTip(view: Pick<View, "export"> & Partial<Pick<View, "tutorial">>, busy: boolean): string {
  if (view.tutorial?.active) return "Held still during the tour: the practice copies are not yours to save.";
  if (busy) return "Finish or stop the current work first";
  const state = view.export ?? { folder: "", unsaved: 0, copies: 0 };
  if (state.copies === 0) {
    return "Nothing to save yet: create edited copies first. Copies are kept only while this window is open, until you save them.";
  }
  const head = "Save the edited copies to a folder you choose.";
  if (state.unsaved === 0) {
    return `${head} All ${state.copies === 1 ? "1 copy is" : `${state.copies} copies are`} saved${state.folder ? ` to ${state.folder}` : ""}.`;
  }
  const count = `${state.unsaved} of ${state.copies} ${state.copies === 1 ? "copy" : "copies"} ${state.unsaved === 1 ? "is" : "are"} not saved yet`;
  return `${head} ${count}, and only a saved copy survives closing the window.`;
}

/** A copy's saved state, as the server reports it (`lib/workbench.ts` reads nothing else of a result). */
export type SavedState = Pick<Result, "output" | "saved_to" | "changed_since_saved" | "saved_file_changed">;

/** A copy is saved while a file with its current bytes sits where Export put it: `saved_to` names that file,
 *  `changed_since_saved` says the copy's bytes moved since (edited in Word from the window's link), and
 *  `saved_file_changed` says the saved file no longer holds the bytes Export wrote. */
export function isSaved(result: SavedState): boolean {
  return !!result.saved_to && !result.changed_since_saved && !result.saved_file_changed;
}

/** The file a copy's link opens (`POST /api/open`): the file Export saved while it still holds the copy's current
 *  bytes, else the working copy in the window's scratch folder — the one with the newer content, or the only one. */
export function openTarget(result: SavedState): string {
  return isSaved(result) ? (result.saved_to ?? "") : result.output;
}

/** The hover text of a copy's link: which file opens, and why. */
export function copyTip(result: SavedState): string {
  if (isSaved(result)) return `Opens the saved file: ${result.saved_to}`;
  if (result.saved_to && result.changed_since_saved) {
    return `Opens the working copy, which changed since it was saved to ${result.saved_to} (Export saves it again): ${result.output}`;
  }
  if (result.saved_to) {
    return `Opens the working copy; the file saved to ${result.saved_to} no longer holds it (changed or replaced since Export, which saves this copy again, beside it): ${result.output}`;
  }
  return `Opens the working copy, kept in the window's scratch folder until it closes (Export saves it): ${result.output}`;
}

/** The hover text of the CHANGED badge beside a copy saved before and edited since. */
export function changedTip(result: SavedState): string {
  return `Changed since it was saved to ${result.saved_to}. Export saves it again, beside the file saved before.`;
}

/** The hover text of the NOT SAVED badge beside a copy whose saved file no longer holds it. */
export function notSavedTip(result: SavedState): string {
  return `The file saved to ${result.saved_to} no longer holds this copy (changed or replaced since Export). Export saves it again, beside that file.`;
}

/** The line under "Your edited copies": where they were saved, or that they are not saved yet (a copy changed since it
 *  was saved, or whose saved file is gone, is not saved as it is now). */
export function copiesNote(view: { results: SavedState[] }): string {
  const results = view.results ?? [];
  const saved = results.filter(isSaved);
  if (results.length === 0) return "";
  if (saved.length === results.length) {
    const folder = parentFolder(saved[0].saved_to ?? "");
    return folder ? `Saved to ${folder}.` : "Saved.";
  }
  const left = results.length - saved.length;
  const changed = results.filter((r) => !!r.saved_to && r.changed_since_saved).length;
  const replaced = results.filter((r) => !!r.saved_to && !r.changed_since_saved && r.saved_file_changed).length;
  const count = saved.length === 0 ? "Not saved yet" : `${left} of ${results.length} not saved yet`;
  const whys: string[] = [];
  if (changed > 0) whys.push(`${changed} changed since ${changed === 1 ? "it was" : "they were"} saved`);
  if (replaced > 0) whys.push(`${replaced} whose saved file changed since`);
  const why = whys.length > 0 ? ` (${whys.join(", ")})` : "";
  return `${count}${why}: Export, in the top bar, saves them to a folder you choose. Until then they live only in this window's scratch folder, which goes when the window closes.`;
}

/** The folder of the clean PDFs: the parent of the first one, or "" with none. */
export function pdfFolder(view: Partial<Pick<View, "pdfs">>): string {
  const first = view.pdfs?.[0]?.output ?? "";
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

export interface UndoLink {
  disabled: boolean;
  tip: string;
}

/** Safe mode's "Undo the last button", beside Start over: null when there is nothing to undo; disabled, with the
 *  server's reason, while it cannot be undone (a preview waits, work runs). */
export function undoLink(view: Pick<View, "mode" | "working">, busy: boolean): UndoLink | null {
  const undo = view.working?.undo ?? "";
  if (view.mode !== "safe" || !undo) return null;
  if (busy) return { disabled: true, tip: "Finish or stop the current work first" };
  const blocked = view.working.undo_blocked ?? "";
  if (blocked) return { disabled: true, tip: blocked };
  return {
    disabled: false,
    tip: `Goes back to the copies from before “${undo}”. Nothing is deleted: the copies it made stay where they were saved.`,
  };
}
