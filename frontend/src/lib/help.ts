/**
 * The top bar's small rules: the help topics, the line beside the title, the
 * model's name on the About page, and what the About page and the update pill
 * say about updates.
 *
 * Pure, so `node --test` can pin them.
 */
import type { Update, View } from "../types.ts";
import { kindTag } from "./workbench.ts";

/** The help topics the top bar opens. */
export type HelpTopic = "how-to-use" | "workflows" | "how-it-works" | "why-trust-it" | "about";

/** One list for the top bar's buttons and the help dialog's tab strip. */
export const HELP_TOPICS: { id: HelpTopic; label: string }[] = [
  { id: "how-to-use", label: "How to use" },
  { id: "workflows", label: "Workflows" },
  { id: "how-it-works", label: "How it works" },
  { id: "why-trust-it", label: "Why trust it" },
  { id: "about", label: "About" },
];

export const HELP_TITLES: Record<HelpTopic, string> = {
  "how-to-use": "How to use Spec Surgeon",
  workflows: "Typical workflows",
  "how-it-works": "How it works",
  "why-trust-it": "Why trust it?",
  about: "About",
};

const counted = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * The quiet line beside the title: what this conversation works on, such as
 * "3 documents · 1 template · 1 picture · building on your copies". Files
 * triage refused are not counted. "" until a document is attached.
 */
export function sessionHeading(view: Pick<View, "attachments" | "working">): string {
  const usable = (view.attachments ?? []).filter((a) => a.state !== "refused");
  const documents = usable.filter((a) => a.kind === "document").length;
  if (documents === 0) return "";
  const parts = [counted(documents, "document", "documents")];
  const templates = usable.filter((a) => a.kind === "template").length;
  if (templates > 0) parts.push(counted(templates, "template", "templates"));
  const references = usable.filter((a) => a.kind === "reference");
  const pictures = references.filter((a) => kindTag(a) === "Picture").length;
  const tables = references.length - pictures;
  if (pictures > 0) parts.push(counted(pictures, "picture", "pictures"));
  if (tables > 0) parts.push(counted(tables, "table", "tables"));
  if (view.working && !view.working.from_originals) parts.push("building on your copies");
  return parts.join(" · ");
}

/** "claude-opus-5-5" → "Claude Opus 5.5"; any other id as given; "" for none. */
export function modelLabel(id: string | undefined): string {
  const model = (id ?? "").trim();
  const match = /^claude-(opus|sonnet|haiku|fable)-(\d+)-(\d+)(?:$|[-@])/i.exec(model);
  if (!match) return model;
  const family = match[1].charAt(0).toUpperCase() + match[1].slice(1).toLowerCase();
  return `Claude ${family} ${match[2]}.${match[3]}`;
}

/**
 * A check you asked for has its answer, or there is nothing more to wait
 * for: an update was found (or is already downloading, ready or installing,
 * when no check runs), or the server wrote the check's answer. Updated,
 * failed and error are earlier outcomes a new check does not change, so they
 * do not settle it.
 */
export function checkSettled(update: Update | null | undefined): boolean {
  if (!update) return false;
  if (["available", "downloading", "ready", "installing"].includes(update.state)) return true;
  return !!update.manual_result;
}

export interface UpdateStatus {
  /** One sentence, or "" when there is nothing to say. */
  text: string;
  tone: "quiet" | "accent" | "ok" | "err";
  /** The one button beside it, mirroring the update banner's. */
  action: "download" | "install" | null;
  /** 0–100 while downloading, else null. */
  progress: number | null;
}

/**
 * What the About page says beside Check for updates. `asked`: About started
 * a check while this window was open (the dialog may have been closed and
 * reopened since); `checking`: that check has not settled yet. An answer to
 * a check asked for before the window opened is not shown.
 */
export function updateStatus(update: Update | null | undefined, asked: boolean, checking: boolean): UpdateStatus {
  const none: UpdateStatus = { text: "", tone: "quiet", action: null, progress: null };
  const u = update;
  const version = u?.version ? `Version ${u.version}` : "A new version";
  switch (u?.state) {
    case "available":
      return { ...none, text: `${version} is available.`, tone: "accent", action: "download" };
    case "downloading": {
      const progress = Math.max(0, Math.min(100, Math.round(u.progress ?? 0)));
      return { ...none, text: `Downloading ${version.toLowerCase()}… ${progress}%`, tone: "accent", progress };
    }
    case "ready":
      return { ...none, text: `${version} is downloaded and checked.`, tone: "ok", action: "install" };
    case "installing":
      return { ...none, text: `Closing to install ${version.toLowerCase()}…`, tone: "accent" };
  }
  if (asked && checking) return { ...none, text: "Checking for updates…" };
  if (asked && u?.manual_result) {
    const failed = /^couldn.t check/i.test(u.manual_result);
    return { ...none, text: u.manual_result, tone: failed ? "err" : "quiet" };
  }
  switch (u?.state) {
    case "updated":
      return { ...none, text: `Updated to ${version.toLowerCase()}.`, tone: "ok" };
    case "failed":
      return { ...none, text: `The update to ${u.version || "the new version"} was not installed.`, tone: "err" };
    case "error":
      return { ...none, text: u.error ? `The update was not installed: ${u.error}` : "The update was not installed.", tone: "err" };
  }
  if (asked) return { ...none, text: "The check has not answered yet. Try again in a moment." };
  return none;
}

/**
 * The top bar's update pill: an update you set aside with the banner's "Not
 * now" stays one click from Download, as "v1.7.0 available". "" while the
 * banner itself shows it, and for every other state (the banner keeps those).
 */
export function updatePill(update: Update | null | undefined, bannerShown: boolean): string {
  if (!update || update.state !== "available" || bannerShown) return "";
  return update.version ? `v${update.version} available` : "Update available";
}

/** The release notes under About's "What's new in 1.6.0": without their own
 *  leading title ("# Spec Surgeon 1.6.0"), which would say it twice. */
export function releaseNotesBody(notes: string | undefined): string {
  return (notes ?? "").replace(/^\s*#{1,6}[ \t]+[^\n]*(?:\n+|$)/, "").trim();
}
