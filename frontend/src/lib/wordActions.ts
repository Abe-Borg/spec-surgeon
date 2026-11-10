/**
 * Obliterate's named Word actions in words (CLAUDE.md, Modes; docs/BACKLOG.md O5): a fixed menu of actions that
 * Microsoft Word itself runs on a copy of a document. The user picks them here, beside a document, or Claude names
 * them in a plan. Word saves the whole file again, so nothing is predicted: every copy is made before approval,
 * what it changes is listed from the copy by the independent reader, and each is delivered marked UNPROVEN.
 * Picking them from this menu uses no AI.
 */
import type { WordMenu } from "../types.ts";

/** A document's button, in Obliterate mode. */
export const WORD_BUTTON = "Word actions";
export function wordTip(name: string, busy: boolean, menu: WordMenu | null): string {
  if (busy) return "Finish or stop the current work first";
  if (menu && !menu.available) return `Word actions for ${name}: Microsoft Word is not available on this computer`;
  return `Have Microsoft Word itself run named actions on a copy of ${name}. No AI is used`;
}

/** The dialog's own sentences. */
export const WORD_TITLE = "Word actions";
export const WORD_SAYS: readonly string[] = [
  "Microsoft Word itself runs the actions you tick, in the order shown, on a private copy of the document. Your file is never opened by it, macros stay disabled, and no AI is used.",
  "Word saves the whole file again, so nothing is predicted. The copy is made now and read by the independent reader: the words it adds and removes and what else it changes are listed before you approve, and Create delivers that same copy, marked UNPROVEN.",
];
export const WORD_RUN = "Run in Word";
export const WORD_NONE = "Tick at least one action.";
export const WORD_BOTH = "After one of these, no tracked change is left for the other.";
export const WORD_FIRST = "Runs first: it is how Word opens the copy.";

/** The scope box: the other documents get the actions too. */
export function everyLabel(others: number): string {
  return others === 1 ? "Also run on the other document" : `Also run on the other ${others} documents`;
}

/** How long Word may take, said under the list. */
export function wordPatience(minutes: number): string {
  return `Word may take up to ${minutes} minutes a document. Stop ends it.`;
}

/** An action cannot be ticked beside the one that leaves it nothing to do; "" when it can. */
export function wordHeld(chosen: readonly string[], id: string): string {
  if (chosen.includes(id)) return "";
  const other = id === "accept_changes" ? "reject_changes" : id === "reject_changes" ? "accept_changes" : "";
  return other && chosen.includes(other) ? WORD_BOTH : "";
}

/** The chosen actions after a tick or an untick, in the order they will run: the order ticked, with Open and
 *  Repair first whenever it is there. */
export function toggleAction(chosen: readonly string[], id: string): string[] {
  if (chosen.includes(id)) return chosen.filter((x) => x !== id);
  if (wordHeld(chosen, id)) return [...chosen];
  return id === "repair" ? [id, ...chosen] : [...chosen, id];
}

/** The request as the chat says it while Word works (the server says its own once the job starts). */
export function wordRequest(name: string, labels: readonly string[], every: boolean, documents: number): string {
  const where = every && documents > 1 ? `every document (${documents})` : name;
  return `Run in Word on ${where}: ${labels.join("; then ")}.`;
}

/** Why Run waits; "" when it can be pressed. */
export function runHeld(menu: WordMenu | null, chosen: readonly string[], busy: boolean): string {
  if (busy) return "Finish or stop the current work first";
  if (!menu || !menu.available) return menu?.why || "Microsoft Word is not available on this computer.";
  return chosen.length === 0 ? WORD_NONE : "";
}

/** What says, on a copy's card and in its lists, that Word made it. */
export const WORD_OTHER_CHANGES_TITLE = "What else Word's actions change in this copy, as the independent reader found it:";
export const WORD_ACCOUNT_TITLE = "What Word itself says it did (Word's own count, not the independent reader's):";
