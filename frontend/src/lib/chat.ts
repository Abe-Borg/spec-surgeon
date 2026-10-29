/**
 * What the chat pane shows, decided from the page state (lib/view.ts):
 * the transcript items in order, where the choices and chips go, what the
 * composer may do, and the words of the status strip.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { Choice, Message, PauseReason, TodoItem, View } from "../types.ts";
import type { Aside, LiveReply, Step, UiState } from "./view.ts";

/** Every item carries the key the chat renders it under. Messages are keyed by their place in the conversation,
 *  not their id: the optimistic user bubble (`local-…`) and the server's copy of it (`m…`) are one bubble, and the
 *  live bubble takes the key of the message it becomes, so a finished reply carries on in the same bubble. */
export type ChatItem =
  | { kind: "message"; key: string; message: Message; aside: Aside | null }
  /** `leaving`: the bubble of a job that ended without a message in its place, kept while it folds away. */
  | { kind: "live"; key: string; live: LiveReply; leaving?: boolean }
  | { kind: "error"; key: string; text: string }
  | { kind: "prompts"; key: string; questions: string[]; choices: Choice[]; chips: string[] }
  | {
      kind: "pause";
      key: string;
      reason: Exclude<PauseReason, "">;
      /** The conversation's estimated spending, this request's, and the spending check ("" when it is off). */
      costText: string;
      requestText: string;
      limitText: string;
      canContinue: boolean;
    }
  | { kind: "todo"; key: string; items: TodoItem[] };

/** The key of the conversation's entry at `index` (a message, or the live bubble after the last one). */
export function entryKey(index: number): string {
  return `entry-${index}`;
}

/** The reply that begins the pass over the to-do list; the server recognises these exact words
 *  (`document_task.WORK_TODO`). Sent as a chip: the user's decision, never their wording. */
export const WORK_TODO = "Work on the documents I set aside";

/** The reply that ends a recovery by putting what it has not fixed on the to-do list (`document_task.SET_ASIDE_REST`);
 *  the server offers it as a chip once the ready copies are saved. */
export const SET_ASIDE_REST = "Set the rest aside for later";

/** The to-do card: documents set aside (the red X, at the user's word, or because the plan refused them or their copy
 *  could not be made) wait; whenever nothing runs and no pass is under way the card offers to begin the pass. */
export function todoFor(state: UiState): ChatItem | null {
  const { view } = state;
  if (view.busy || state.live) return null;
  if ((view.todo ?? []).length === 0 || (view.focus ?? []).length > 0) return null;
  if (view.stage !== "done" && view.stage !== "empty" && view.stage !== "resume") return null;
  return { kind: "todo", key: "todo", items: view.todo };
}

/** The choices and chips to offer right now, or null when none apply. */
export function promptsFor(state: UiState): ChatItem | null {
  const { view, live } = state;
  if (view.busy || live) return null;
  if (view.stage !== "question") return null;
  const questions = view.questions ?? [];
  const choices = view.choices ?? [];
  const chips = view.chips ?? [];
  if (questions.length === 0 && choices.length === 0 && chips.length === 0) return null;
  return { kind: "prompts", key: "prompts", questions, choices, chips };
}

/** The pause card: work paused with its progress saved; Continue picks it up. */
export function pauseFor(state: UiState): ChatItem | null {
  const { view } = state;
  if (view.busy) return null;
  if (view.stage !== "budget" && !view.budget?.paused) return null;
  const limit = view.cost?.limit;
  return {
    kind: "pause",
    key: "pause",
    reason: view.budget?.reason || "interrupted",
    costText: spentText(view.cost),
    requestText: spentText(view.cost ? { ...view.cost, usd: view.cost.request ?? null } : null),
    limitText: typeof limit === "number" ? dollars(limit) : "",
    canContinue: view.action?.kind === "continue" && !!view.action.token,
  };
}

/**
 * The transcript, oldest first: the server's messages, then the live bubble
 * while a job runs, the last failure, and the choices and chips under the
 * latest assistant message when a question is open.
 */
export function transcript(state: UiState): ChatItem[] {
  const items: ChatItem[] = state.view.messages.map((message, index) => ({
    kind: "message",
    key: entryKey(index),
    message,
    aside: state.asides[message.id] ?? null,
  }));
  if (state.live) items.push({ kind: "live", key: entryKey(state.view.messages.length), live: state.live });
  if (state.error) items.push({ kind: "error", key: "error", text: state.error });
  const prompts = promptsFor(state);
  if (prompts) items.push(prompts);
  const pause = pauseFor(state);
  if (pause) items.push(pause);
  const todo = todoFor(state);
  if (todo) items.push(todo);
  return items;
}

/** The live bubble of `before` that is gone from `after` with nothing in its place (its job ended without a
 *  message under its key), when it showed anything: the chat folds it away instead of dropping it. */
export function endedLive(before: readonly ChatItem[], after: readonly ChatItem[]): { key: string; live: LiveReply } | null {
  const live = before.find((item) => item.kind === "live");
  if (!live || live.kind !== "live" || live.leaving || !liveHasContent(live.live)) return null;
  if (after.some((item) => item.key === live.key || item.kind === "live")) return null;
  return { key: live.key, live: live.live };
}

/** The transcript with a bubble that is folding away kept where it was, after the messages, until its key is
 *  taken again (the next job's first message). */
export function withLeaving(items: ChatItem[], leaving: { key: string; live: LiveReply } | null): ChatItem[] {
  if (!leaving || items.some((item) => item.key === leaving.key)) return items;
  const at = items.findIndex((item) => item.kind !== "message");
  const cut = at === -1 ? items.length : at;
  return [...items.slice(0, cut), { kind: "live", key: leaving.key, live: leaving.live, leaving: true }, ...items.slice(cut)];
}

/** True when a live bubble has anything to show; an empty one after a
 *  `message` frame renders nothing until the next status or delta. */
export function liveHasContent(live: LiveReply): boolean {
  return !!(live.text || live.thinking || live.status || live.steps.length);
}

/** The folded line above a reply that names its making: "3 steps · thinking", "Thinking", "1 step". */
export function stepsLabel(steps: readonly Step[], thinking: string): string {
  const n = steps.filter((step) => step.kind === "activity").length;
  const count = n > 0 ? `${n} ${n === 1 ? "step" : "steps"}` : "";
  if (count && thinking) return `${count} · thinking`;
  if (count) return count;
  if (thinking) return "Thinking";
  return steps.length > 0 ? "Working notes" : "";
}

export const COMPOSER_NO_KEY = "Connect your Anthropic API key in Settings to start.";
export const COMPOSER_NO_DOCUMENTS = "Add a Word document first.";

export interface ComposerState {
  /** Sending is possible (a key is connected and a document is attached). */
  canSend: boolean;
  /** Why not, for the disabled button's hover; "" when it can. */
  reason: string;
}

/** Accepted documents: attached Word files that were not refused. */
export function acceptedDocuments(view: View): number {
  return view.attachments.filter((a) => a.kind === "document" && a.state !== "refused").length;
}

export function composerState(view: View): ComposerState {
  if (!view.connection?.ready) return { canSend: false, reason: COMPOSER_NO_KEY };
  if (acceptedDocuments(view) === 0) return { canSend: false, reason: COMPOSER_NO_DOCUMENTS };
  return { canSend: true, reason: "" };
}

/** The estimated spending as the window says it (an estimate from the price
 *  table, never a bill). Mirrors the Qt window's `spent_text`. */
export function spentText(cost: View["cost"] | null | undefined): string {
  const usd = cost?.usd;
  if (typeof usd !== "number" || Number.isNaN(usd)) {
    return cost?.text || "an amount the price table cannot estimate";
  }
  return usd < 0.005 ? "less than $0.01" : `about $${usd.toFixed(2)}`;
}

/** An amount as the spending check says it: "$20.00". */
export function dollars(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

/**
 * Beside the cost pill while a request runs: "this request ≈ $0.40 of $20.00" (in the top bar's fewer words
 * "≈ $0.40 of $20.00"; "" with the check off).
 */
export function requestPill(cost: View["cost"] | null | undefined, short = false): string {
  const limit = cost?.limit;
  if (typeof limit !== "number") return "";
  const spent = cost?.request;
  return `${short ? "" : "this request "}${typeof spent === "number" && !Number.isNaN(spent) ? `≈ ${dollars(spent)}` : "≈ ?"} of ${dollars(limit)}`;
}

/** The cost pill: "≈ $0.12" (or "≈ $0.00" before anything was spent). */
export function costPill(cost: View["cost"] | null | undefined): string {
  const usd = cost?.usd;
  if (typeof usd !== "number" || Number.isNaN(usd)) return cost?.text ? `≈ ${cost.text}` : "—";
  return `≈ $${usd.toFixed(2)}`;
}

const STATUS_LABELS: Record<string, string> = {
  working: "Working…",
  thinking: "Thinking…",
  reading: "Reading the documents…",
  planning: "Working out the plan…",
  checking: "Checking the result…",
  previewing: "Preparing the preview…",
  rendering: "Rendering the pages…",
  looking: "Looking at the pages…",
  creating: "Creating your edited copies…",
  recovering: "Checking the files that still need work…",
  resuming: "Continuing where the conversation stopped…",
};

/** The status strip's words: the server's sentence when it sent one, else the kind's label. */
export function statusLabel(status: { kind: string; text?: string } | null): string {
  if (!status) return "";
  const text = (status.text ?? "").trim();
  if (text) return text;
  return STATUS_LABELS[status.kind] ?? "Working…";
}

/** The list of starter requests on the empty chat; clicking one fills the composer. */
export const STARTERS: readonly { title: string; text: string }[] = [
  {
    title: "Match a template",
    text: "Match the headers, footers and fonts to the attached template. Keep our wording.",
  },
  {
    title: "Change the fonts",
    text: "Change every font to Arial 10 pt, headings included.",
  },
  {
    title: "Fix the cover and footer",
    text: "Put the project name and number on the first page and in every footer.",
  },
];
