/**
 * The note above Safe mode's buttons (docs/CONVERSATIONAL_DESKTOP.md, Safe mode): what Safe mode is, what it cannot
 * do, and that Default mode with a Claude API key is the way to more. Open, it is a short card; hidden, one line that
 * still points to Default mode (settings.json `safe_note_hidden`). Its words depend on one thing only: whether the
 * window has an API key (the view's `connection.ready`).
 *
 * Pure, so `node --test` can pin the wording.
 */

export interface SafeNoteWords {
  title: string;
  tags: string;
  what: string;
  cannot: string;
  more: string;
  /** Whether a key is set, said under the buttons. */
  key: string;
  /** The filled button: Add a Claude API key without a key, Switch to Default mode with one. */
  primary: "key" | "default";
  /** The one line the hidden note keeps. */
  line: string;
  /** The line under every button. */
  tail: string;
}

export const SAFE_NOTE_SHOW = "What is Safe mode?";
export const SAFE_NOTE_HIDE = "Hide";
export const ADD_KEY = "Add a Claude API key";
export const SWITCH_TO_DEFAULT = "Switch to Default mode";

export function safeNoteWords(keyReady: boolean): SafeNoteWords {
  return {
    title: "Safe mode",
    tags: "no AI · no API key needed",
    what:
      "Each button below makes one common change, built in code from the short form you fill in. You see the pages " +
      "before and after, and nothing is created until you approve. Every word a button places is one you typed or " +
      "one from a file you attached.",
    cannot:
      "anything that isn't a button. Rewriting or moving paragraphs, editing tables, adding an article, or any " +
      "change that depends on reading what each document says.",
    more:
      "In Default mode you describe the change in plain English. Claude plans it, and you get the same previews, " +
      "checks and approval. It needs a Claude API key.",
    key: keyReady
      ? "Your API key is set, so Default mode is ready."
      : "No API key yet. Default mode needs one; you get it from your Anthropic account.",
    primary: keyReady ? "default" : "key",
    line: keyReady
      ? "fixed buttons, no AI. For any other change, use Default mode."
      : "fixed buttons, no AI. For any other change, use Default mode with a Claude API key.",
    tail: "Don't see the change you need? In Default mode you describe it in your own words.",
  };
}
