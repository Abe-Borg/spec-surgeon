/**
 * What the chat pane shows, decided from the page state (lib/view.ts):
 * the transcript items in order, where the choices and chips go, what the
 * composer may do, and the words of the status strip.
 *
 * Pure, so `node --test` can pin every rule.
 */
import type { Choice, Message, View } from "../types.ts";
import type { Aside, LiveReply, UiState } from "./view.ts";

export type ChatItem =
  | { kind: "message"; message: Message; aside: Aside | null }
  | { kind: "live"; live: LiveReply }
  | { kind: "error"; text: string }
  | { kind: "prompts"; questions: string[]; choices: Choice[]; chips: string[] }
  | { kind: "pause"; stepsPerPause: number; costText: string; canContinue: boolean };

/** The choices and chips to offer right now, or null when none apply. */
export function promptsFor(state: UiState): ChatItem | null {
  const { view, live } = state;
  if (view.busy || live) return null;
  if (view.stage !== "question") return null;
  const questions = view.questions ?? [];
  const choices = view.choices ?? [];
  const chips = view.chips ?? [];
  if (questions.length === 0 && choices.length === 0 && chips.length === 0) return null;
  return { kind: "prompts", questions, choices, chips };
}

/** The pause card: work paused with its progress saved; Continue picks it up. */
export function pauseFor(state: UiState): ChatItem | null {
  const { view } = state;
  if (view.busy) return null;
  if (view.stage !== "budget" && !view.budget?.paused) return null;
  return {
    kind: "pause",
    stepsPerPause: view.budget?.steps_per_pause ?? 48,
    costText: spentText(view.cost),
    canContinue: view.action?.kind === "continue" && !!view.action.token,
  };
}

/**
 * The transcript, oldest first: the server's messages, then the live bubble
 * while a job runs, the last failure, and the choices and chips under the
 * latest assistant message when a question is open.
 */
export function transcript(state: UiState): ChatItem[] {
  const items: ChatItem[] = state.view.messages.map((message) => ({
    kind: "message",
    message,
    aside: state.asides[message.id] ?? null,
  }));
  if (state.live) items.push({ kind: "live", live: state.live });
  if (state.error) items.push({ kind: "error", text: state.error });
  const prompts = promptsFor(state);
  if (prompts) items.push(prompts);
  const pause = pauseFor(state);
  if (pause) items.push(pause);
  return items;
}

/** True when a live bubble has anything to show; an empty one after a
 *  `message` frame renders nothing until the next status or delta. */
export function liveHasContent(live: LiveReply): boolean {
  return !!(live.text || live.thinking || live.status || live.activities.length);
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
