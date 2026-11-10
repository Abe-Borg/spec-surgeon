/**
 * Obliterate's hands-on tools in words (CLAUDE.md, Modes; docs/BACKLOG.md O4): a read-only browser of a document's
 * parts, saving one out as it is, and putting a file of the user's in a part's place. They are off unless the user
 * turns them on — in Obliterate's warning, or in its strip above the message box — and they go with the mode.
 * Nothing in the window edits XML, and none of the tools uses AI.
 */
import type { PartInfo } from "../types.ts";

export const HANDS_ON_LABEL = "Hands-on tools";

/** The toggle in Obliterate's warning. */
export const HANDS_ON_TOGGLE = "Also turn on the hands-on tools";

/** What the toggle turns on, said beside it and in the dossier. */
export const HANDS_ON_SAYS: readonly string[] = [
  "Parts, beside each document, shows the parts its file is made of — the XML as text, with a search. It is read-only: nothing in the window edits XML.",
  "Save this part writes one part out as it is, to a folder you choose, for you to change in an editor of your own.",
  "Replace this part puts a file of yours in a part's place, byte for byte, in a copy. The app proves the copy holds exactly your file's bytes and that no other part changed; what the new part does to the words is not predicted: it is listed for you from the copy before you approve. A part that is no longer a file Word opens is made as you gave it, fails its check, and is delivered marked UNVERIFIED.",
  "None of them uses AI or sends anything anywhere. Claude is never told of them and cannot use them.",
];

/** The strip's switch, and why it waits. */
export function handsOnTip(on: boolean, busy: boolean): string {
  if (busy) return "Finish or stop the current work first";
  return on
    ? "Turn the hands-on tools off: Parts leaves the documents, and a part replacement that waits is withdrawn"
    : "Turn on the hands-on tools: browse a document's parts read-only, save one out, replace one with a file of yours. No AI is used";
}

/** A document's Parts button. */
export const PARTS_BUTTON = "Parts";
export const partsTip = (name: string, busy: boolean): string =>
  busy ? "Finish or stop the current work first" : `Show the parts of ${name}, read-only`;

/** The browser's own sentences. */
export const PARTS_READ_ONLY = "Read-only. To change a part: save it out, edit the file in an editor of your own, then replace the part with it.";
export const WHICH_LABELS = { current: "The edited copy", original: "The original" } as const;
export const SAVE_PART = "Save this part…";
export const REPLACE_PART = "Replace this part…";
export const SAVE_PART_TIP = "Write this part's own bytes to a folder you choose. Nothing in the document changes";
export const REPLACE_PART_TIP =
  "Choose a file of yours to take this part's place, byte for byte. A preview comes first: nothing changes until you approve it";
export const REPLACE_ORIGINAL_TIP = "A part is replaced in the edited copy, the file the next change is made on: switch to it first";

/** A member's size as the list shows it. */
export function partSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The parts under the folders they lie in, in the package's own order: `[folder, parts]` pairs, "" for the root. */
export function partsByFolder(parts: readonly PartInfo[]): [string, PartInfo[]][] {
  const groups: [string, PartInfo[]][] = [];
  for (const part of parts) {
    const cut = part.name.lastIndexOf("/");
    const folder = cut < 0 ? "" : part.name.slice(0, cut);
    const found = groups.find(([name]) => name === folder);
    if (found) found[1].push(part);
    else groups.push([folder, [part]]);
  }
  return groups;
}

/** A part's own name, without its folder. */
export const partLeaf = (name: string): string => name.slice(name.lastIndexOf("/") + 1);

/** What the search says above its matches. */
export function searchSummary(query: string, matches: number, more: boolean): string {
  if (!query) return "";
  if (matches === 0) return `No part holds “${query}”.`;
  return `${matches}${more ? "+" : ""} line${matches === 1 && !more ? "" : "s"} hold “${query}”${more ? " (the first ones are listed)" : ""}.`;
}
