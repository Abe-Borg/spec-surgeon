/**
 * Obliterate's rescue at attach in words (CLAUDE.md, Modes; docs/BACKLOG.md O6): a document the attach-time check
 * refuses offers **Try to rescue** in Obliterate mode. The app tries the one thing that could make it a file the
 * engine takes — its own mending, or Word converting or repairing a private copy — and what comes of it is attached
 * in the refused file's place, marked SALVAGED for as long as it is attached, on itself and on every copy made from
 * it. The user's file is read, never written. No AI is used.
 */
import type { Attachment, Mode, Salvaged, WordMenu } from "../types.ts";

export const RESCUE_BUTTON = "Try to rescue";

/** What each kind of rescue tries, said on the button's tip and in the password dialog. */
export const RESCUE_TRIES: Record<string, string> = {
  doctype: "The app writes the part again without its document type declaration.",
  "no-paragraph": "The app adds the empty paragraph a document's body must have.",
  format: "Word opens a private copy and saves it as a Word document (.docx).",
  strict: "Word opens a private copy and saves it as an ordinary Word document (.docx).",
  password: "Word opens a private copy with the password you type, and saves it without one.",
  unopenable: "Word opens a private copy with its Open and Repair command and saves what it could read.",
  "too-large": "The app raises its size limits for this window so the file can be opened as it is.",
};

/** The rescues that are Word's: they need Microsoft Word on this computer. */
export const RESCUE_NEEDS_WORD: readonly string[] = ["format", "strict", "password", "unopenable"];

/** A refused document offers the button in Obliterate mode, when there is something to try. */
export function canRescue(attachment: Pick<Attachment, "kind" | "state" | "rescue">, mode: Mode): boolean {
  return mode === "obliterate" && attachment.kind === "document" && attachment.state === "refused" && !!attachment.rescue;
}

/** The button's tip: what will be tried, that the file is only read, and when Word is needed and not there. */
export function rescueTip(attachment: Pick<Attachment, "name" | "rescue">, busy: boolean, word: WordMenu | null): string {
  if (busy) return "Finish or stop the current work first";
  const kind = attachment.rescue ?? "";
  const tries = RESCUE_TRIES[kind] ?? "The app tries what could make this a file it can take.";
  if (RESCUE_NEEDS_WORD.includes(kind) && word && !word.available) {
    return `${tries} Microsoft Word is not available on this computer, so this cannot be tried here`;
  }
  return `${tries} Your file is read, never changed; what comes of it is attached in its place, marked SALVAGED. No AI is used`;
}

/** The password dialog's own sentences. */
export const PASSWORD_TITLE = "The document's password";
export const PASSWORD_SAYS: readonly string[] = [
  "This document is protected with a password, so Word needs it to open a private copy.",
  "The password is given to Word for this one attempt and kept nowhere: it is not saved, not written to any log or record, and never sent to an AI model.",
  "The rescued document will have NO password. It sits in this window's scratch folder until the window closes, and every copy made from it is unprotected too.",
];
export const PASSWORD_GO = "Try to rescue";

/** The files the document picker takes: Word documents, and in Obliterate mode the kinds only a rescue opens. */
export function documentsAccept(mode: Mode): string {
  return mode === "obliterate" ? ".docx,.doc,.docm" : ".docx";
}

/** A rescued document's tag, and what it means. */
export const SALVAGED_TAG = "Salvaged";
export const SALVAGED_MARK = "SALVAGED";
export function salvagedTip(salvaged: Salvaged): string {
  const steps = salvaged.steps.join(" ");
  const notes = salvaged.notes.length > 0 ? ` ${salvaged.notes.join(" ")}` : "";
  return `Rescued from ${salvaged.from}, a file the app could not read as it was. ${steps} Nothing proves it holds everything that file did: compare the two in Word. Its copies are checked against this rescued document.${notes}`;
}
