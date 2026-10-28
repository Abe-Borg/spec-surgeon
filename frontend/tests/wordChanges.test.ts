/**
 * The preview card's list of the words a custom method changes (lib/wordChanges.ts): the server's `text_changes`
 * entries worded for the card, nothing when a sample carries none, "no words change" when the list is empty.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { TextChange } from "../src/types.ts";
import { NO_WORDS_CHANGE, WORDS_CHANGE_TITLE, wordChangeLine, wordChangeLines } from "../src/lib/wordChanges.ts";

const footer: TextChange = { part: "word/footer1.xml", where: "footer", paragraph: 0, removed: [], added: ["Coordination draft."] };
const title: TextChange = { part: "word/document.xml", where: "body paragraph 2", paragraph: 1, removed: ["FIRE"], added: ["WATER"] };

test("a sentence added to the footer reads as words added, quoted exactly, under the part's name", () => {
  assert.deepEqual(wordChangeLine(footer), { where: "footer", removed: "", added: "\u201cCoordination draft.\u201d", note: "" });
});

test("a replaced word lists what was removed and what was added", () => {
  assert.deepEqual(wordChangeLine(title), { where: "body paragraph 2", removed: "\u201cFIRE\u201d", added: "\u201cWATER\u201d", note: "" });
  const two: TextChange = { ...title, removed: ["FIRE", "WET"], added: ["WATER"] };
  assert.equal(wordChangeLine(two).removed, "\u201cFIRE\u201d, \u201cWET\u201d");
});

test("a whole paragraph that is new or gone, and a change of spacing alone, say so", () => {
  assert.equal(wordChangeLine({ ...title, removed: [], added: ["See the portal."], block: "new" }).note, "a new paragraph");
  assert.equal(wordChangeLine({ ...title, removed: [], added: [], block: "new" }).note, "a new paragraph without words");
  assert.equal(wordChangeLine({ ...title, removed: ["Attached."], added: [], block: "gone" }).note, "the whole paragraph");
  assert.equal(wordChangeLine({ ...title, removed: [], added: [], block: "gone" }).note, "an empty paragraph removed");
  assert.equal(wordChangeLine({ ...title, removed: [], added: [] }).note, "only the spacing changes, no words");
  assert.equal(wordChangeLine({ ...title, where: "" }).where, "word/document.xml");
});

test("a sample without the comparison shows nothing; an empty list is 'no words change'", () => {
  assert.equal(wordChangeLines({}), null);
  assert.equal(wordChangeLines({ text_changes: null }), null);
  assert.deepEqual(wordChangeLines({ text_changes: [] }), []);
  assert.equal(wordChangeLines({ text_changes: [footer, title] })?.length, 2);
  assert.match(NO_WORDS_CHANGE, /^No words change/);
  assert.match(WORDS_CHANGE_TITLE, /independent checker/);
});
