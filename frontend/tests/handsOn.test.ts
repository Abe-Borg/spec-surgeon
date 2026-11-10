/**
 * Obliterate's hands-on tools in words (lib/handsOn.ts): what the warning's toggle says it turns on, the list of
 * parts under their folders, a part's size, and what a search says. And the words a replaced part's listing is
 * shown under (lib/wordChanges.ts, lib/modes.ts).
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { PartInfo } from "../src/types.ts";
import {
  HANDS_ON_SAYS,
  HANDS_ON_TOGGLE,
  REPLACE_PART,
  SAVE_PART,
  handsOnTip,
  partLeaf,
  partSize,
  partsByFolder,
  partsTip,
  searchSummary,
} from "../src/lib/handsOn.ts";
import {
  OTHER_CHANGES_TITLE,
  PART_OTHER_CHANGES_TITLE,
  RAW_OTHER_CHANGES_TITLE,
  canTurnOnObliterate,
  obliterateKeyNote,
  otherChangesTitle,
} from "../src/lib/modes.ts";
import {
  NO_WORDS_CHANGE,
  PART_NO_WORDS_CHANGE,
  PART_WORDS_CHANGE_TITLE,
  RAW_WORDS_CHANGE_TITLE,
  WORDS_CHANGE_TITLE,
  wordsHeadings,
} from "../src/lib/wordChanges.ts";

const part = (name: string, size = 10, xml = true): PartInfo => ({ name, size, type: "", xml });

test("the toggle says what it turns on: reading, saving, replacing, and that no AI is used", () => {
  assert.match(HANDS_ON_TOGGLE, /hands-on tools/);
  const said = HANDS_ON_SAYS.join(" ");
  assert.match(said, /read-only: nothing in the window edits XML/);
  assert.match(said, /Save this part writes one part out as it is/);
  assert.match(said, /Replace this part puts a file of yours in a part's place, byte for byte, in a copy/);
  assert.match(said, /not predicted: it is listed for you from the copy before you approve/);
  assert.match(said, /delivered marked UNVERIFIED/);
  assert.match(said, /None of them uses AI/);
  assert.match(said, /Claude is never told of them and cannot use them/);
  // the copy types no number: every one it quotes comes from trustFacts
  for (const line of HANDS_ON_SAYS) assert.doesNotMatch(line, /\d/);
  assert.equal(SAVE_PART, "Save this part…");
  assert.equal(REPLACE_PART, "Replace this part…");
});

test("the strip's switch and a document's Parts say what a click does, or why it waits", () => {
  assert.match(handsOnTip(false, false), /^Turn on the hands-on tools/);
  assert.match(handsOnTip(false, false), /No AI is used/);
  assert.match(handsOnTip(true, false), /a part replacement that waits is withdrawn/);
  assert.equal(handsOnTip(true, true), "Finish or stop the current work first");
  assert.equal(partsTip("a.docx", false), "Show the parts of a.docx, read-only");
});

test("the parts are listed under the folders they lie in, in the package's own order", () => {
  const parts = [part("[Content_Types].xml"), part("_rels/.rels"), part("word/document.xml"), part("word/styles.xml"),
    part("word/media/image1.png", 2048, false), part("docProps/core.xml"), part("word/settings.xml")];
  assert.deepEqual(
    partsByFolder(parts).map(([folder, list]) => [folder, list.map((p) => partLeaf(p.name))]),
    [["", ["[Content_Types].xml"]], ["_rels", [".rels"]], ["word", ["document.xml", "styles.xml", "settings.xml"]],
      ["word/media", ["image1.png"]], ["docProps", ["core.xml"]]],
  );
  assert.equal(partLeaf("plain.xml"), "plain.xml");
});

test("a part's size reads in the unit that fits it", () => {
  assert.equal(partSize(512), "512 B");
  assert.equal(partSize(1536), "1.5 KB");
  assert.equal(partSize(48211), "47 KB");
  assert.equal(partSize(3 * 1024 * 1024), "3.0 MB");
});

test("a search says how many lines hold the words, and when only the first are listed", () => {
  assert.equal(searchSummary("", 0, false), "");
  assert.equal(searchSummary("gutter", 0, false), "No part holds “gutter”.");
  assert.equal(searchSummary("gutter", 1, false), "1 line hold “gutter”.".replace("line hold", "line hold"));
  assert.match(searchSummary("w:p", 200, true), /^200\+ lines hold “w:p” \(the first ones are listed\)\.$/);
});

test("a replaced part's listing has its own headings, never a method's or a raw XML edit's", () => {
  assert.deepEqual(wordsHeadings({ raw_xml: true, raw_what: "part" }), { title: PART_WORDS_CHANGE_TITLE, none: PART_NO_WORDS_CHANGE });
  assert.deepEqual(wordsHeadings({ raw_xml: true, raw_what: "xml" }).title, RAW_WORDS_CHANGE_TITLE);
  assert.deepEqual(wordsHeadings({ raw_xml: true }).title, RAW_WORDS_CHANGE_TITLE);
  assert.deepEqual(wordsHeadings({}), { title: WORDS_CHANGE_TITLE, none: NO_WORDS_CHANGE });
  assert.match(PART_WORDS_CHANGE_TITLE, /^Words the replaced part changes, as the independent checker read them:$/);
  assert.match(PART_NO_WORDS_CHANGE, /^The replaced part changes no words/);
  assert.equal(otherChangesTitle({ raw_xml: true, raw_what: "part" }), PART_OTHER_CHANGES_TITLE);
  assert.equal(otherChangesTitle({ raw_xml: true, raw_what: "xml" }), RAW_OTHER_CHANGES_TITLE);
  assert.equal(otherChangesTitle({ unproven: true, raw_xml: true, raw_what: "part" }), OTHER_CHANGES_TITLE);
  assert.equal(otherChangesTitle({ unproven: true }), OTHER_CHANGES_TITLE);
});

test("the warning can be confirmed without a key for what uses no AI", () => {
  // the mode always holds something that uses no AI (the Word actions beside each document), so no key is needed
  // to turn it on; the note says what works without one and what does not
  assert.equal(canTurnOnObliterate(true, false, false), true);
  assert.equal(canTurnOnObliterate(false, false, false), true);
  assert.equal(canTurnOnObliterate(false, true, false), true);
  assert.equal(canTurnOnObliterate(false, false, true), true);
  assert.equal(obliterateKeyNote(true, false, true), "");
  assert.match(obliterateKeyNote(false, false, true), /the Word actions beside each document and the hands-on tools work without one \(no AI is used for them\)/);
  assert.match(obliterateKeyNote(false, false, true), /the conversation in Obliterate mode needs the key/);
  assert.match(obliterateKeyNote(false, false), /^No API key is connected: the Word actions beside each document work without one/);
});
