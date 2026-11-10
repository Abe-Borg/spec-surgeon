/**
 * The preview card's list of the words a custom method changes (lib/wordChanges.ts): the server's `text_changes`
 * entries worded for the card, nothing when a sample carries none, "no words change" when the list is empty.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { TextChange } from "../src/types.ts";
import {
  NO_WORDS_CHANGE,
  RAW_NO_WORDS_CHANGE,
  RAW_WORDS_CHANGE_TITLE,
  WORDS_CHANGE_TITLE,
  wordChangeLine,
  wordChangeLines,
} from "../src/lib/wordChanges.ts";

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

test("words in the copy's tracked changes say so, beside any other note", () => {
  assert.equal(wordChangeLine({ ...title, tracked: true }).note, "in the copy's tracked changes");
  assert.equal(wordChangeLine({ ...title, removed: ["black steel"], added: [], tracked: true }).removed, "\u201cblack steel\u201d");
  assert.equal(wordChangeLine({ ...title, removed: [], added: ["See the portal."], block: "new", tracked: true }).note,
    "a new paragraph, in the copy's tracked changes");
});

test("a sample without the comparison shows nothing; an empty list is 'no words change'", () => {
  assert.equal(wordChangeLines({}), null);
  assert.equal(wordChangeLines({ text_changes: null }), null);
  assert.deepEqual(wordChangeLines({ text_changes: [] }), []);
  assert.equal(wordChangeLines({ text_changes: [footer, title] })?.length, 2);
  assert.match(NO_WORDS_CHANGE, /^No words change/);
  assert.match(WORDS_CHANGE_TITLE, /independent checker/);
});

test("a raw XML edit's words are listed under its own heading, never the method's", () => {
  assert.match(RAW_WORDS_CHANGE_TITLE, /^Words the raw XML edit changes, as the independent checker read them:$/);
  assert.match(RAW_NO_WORDS_CHANGE, /^The raw XML edit changes no words/);
  assert.match(RAW_NO_WORDS_CHANGE, /in the parts it edits/);
  for (const said of [RAW_WORDS_CHANGE_TITLE, RAW_NO_WORDS_CHANGE]) assert.doesNotMatch(said, /method/);
  // "no words" is said of every text node the checker compared, and of attributes it says where to read them
  assert.match(RAW_NO_WORDS_CHANGE, /any other text between tags/);
  assert.match(RAW_NO_WORDS_CHANGE, /attributes are in the edit's own sentence/);
});

test("text that is not a paragraph's words is listed where the server says it is", () => {
  const chart: TextChange = {
    part: "word/charts/chart1.xml", where: "word/charts/chart1.xml, other text (not a paragraph's words)",
    paragraph: null, removed: ["Flow test results"], added: [], other: true,
  };
  assert.deepEqual(wordChangeLine(chart), {
    where: "word/charts/chart1.xml, other text (not a paragraph's words)", removed: "\u201cFlow test results\u201d", added: "", note: "",
  });
  assert.equal(wordChangeLine({ ...chart, more: 3 }).note, "3 more not listed");
  assert.equal(wordChangeLine({ ...chart, removed: [], unread: true }).note, "its text could not be read in the copy");
});
