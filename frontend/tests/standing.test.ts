/**
 * Where each document stands (lib/standing.ts): the grid's words and marks over the server's record, and the
 * record normalized so a grid that would not line up is never drawn.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  EMPTY_STANDING,
  attemptLine,
  cellLabel,
  cellSymbol,
  cellTone,
  markName,
  normalizeStanding,
  operationTitle,
  owedItems,
  owedLine,
  standingOf,
  visibleRows,
  workBlocked,
  workLabel,
} from "../src/lib/standing.ts";
import { normalizeView } from "../src/lib/view.ts";
import type { StandingAttempt } from "../src/types.ts";

const attempt = (over: Partial<StandingAttempt> = {}): StandingAttempt => ({
  state: "done", mode: "safe", why: "", reason: "", marks: [], note: "", first: "", ...over,
});
const cell = (over: Partial<StandingAttempt> = {}) => ({ ...attempt(over), attempts: [attempt(over)] });
const notRun = { ...attempt({ state: "not_run", mode: "", why: "attached later", reason: "It was attached after this was done." }), attempts: [] };

const RAW = {
  operations: [
    { id: "a1", number: 1, request: "Remove all headers and footers", mode: "safe", recipe: "remove_headers_footers", name: "", done: 1, failed: 1 },
    { id: "b2", number: 2, request: "Make the title bold.", mode: "default", recipe: "", name: "", done: 2, failed: 0 },
  ],
  documents: [
    { path: "C:/a.docx", name: "a.docx", cells: [cell(), cell({ mode: "default" })], owed: 0, badges: [] },
    {
      path: "C:/b.docx", name: "b.docx", owed: 1, badges: ["set aside"],
      cells: [cell({ state: "failed", why: "refused", reason: "It has no header to remove." }), cell({ mode: "obliterate", marks: ["V2 (Text preservation)"] })],
    },
    { path: "C:/c.docx", name: "c.docx", cells: [notRun, notRun], owed: 0, badges: [] },
  ],
  owed: { cells: 1, documents: 1 },
};

test("the record reads as the server sent it, with its counts taken from its cells", () => {
  const standing = normalizeStanding(RAW);
  assert.deepEqual(standing.operations.map((o) => [o.number, o.done, o.failed]), [[1, 1, 1], [2, 2, 0]]);
  assert.deepEqual(standing.owed, { cells: 1, documents: 1 });
  assert.deepEqual(standing.documents.map((r) => r.owed), [0, 1, 0]);
  assert.equal(standingOf(normalizeView({ standing: RAW })).documents.length, 3);
});

test("an older server's view, or a record that would not line up, draws no grid", () => {
  assert.equal(standingOf(normalizeView({})), EMPTY_STANDING);
  assert.equal(normalizeStanding(null), EMPTY_STANDING);
  assert.equal(normalizeStanding({ operations: [{ id: "", mode: "safe" }], documents: [] }), EMPTY_STANDING);
  assert.equal(normalizeStanding({ operations: [{ id: "a", mode: "turbo" }], documents: [] }), EMPTY_STANDING);
  // a row with a cell too few, or a cell in no state the grid knows
  assert.equal(normalizeStanding({ ...RAW, documents: [{ ...RAW.documents[0], cells: [cell()] }] }), EMPTY_STANDING);
  assert.equal(normalizeStanding({ ...RAW, documents: [{ ...RAW.documents[0], cells: [cell(), { state: "maybe" }] }] }), EMPTY_STANDING);
});

test("a cell's mark and tone say done, done with a mark, not done or not run", () => {
  assert.deepEqual([cellSymbol(attempt()), cellTone(attempt())], ["✓", "done"]);
  assert.deepEqual([cellSymbol(attempt({ marks: ["V2 (Text preservation)"] })), cellTone(attempt({ marks: ["V2 (Text preservation)"] }))], ["✓", "marked"]);
  assert.deepEqual([cellSymbol(attempt({ state: "failed" })), cellTone(attempt({ state: "failed" }))], ["✗", "failed"]);
  assert.deepEqual([cellSymbol(notRun), cellTone(notRun)], ["–", "none"]);
  assert.equal(markName([]), "");
  assert.equal(markName(["UNPROVEN (a custom method made it: what it changed was shown to you, not proven)"]), "UNPROVEN");
  assert.equal(markName(["V2 (Text preservation)", "UNPROVEN (Word made it)"]), "UNVERIFIED");
});

test("an attempt reads as one sentence: what happened, in which mode, and why", () => {
  assert.equal(attemptLine(attempt()), "Done in Safe mode.");
  assert.equal(
    attemptLine(attempt({ state: "failed", why: "refused", reason: "It has no header to remove." })),
    "Not done in Safe mode. It has no header to remove.",
  );
  assert.equal(attemptLine(notRun), "Not run. It was attached after this was done.");
  assert.equal(
    attemptLine(attempt({ mode: "obliterate", marks: ["V2 (Text preservation)"], note: "the copy that failed, taken as it is" })),
    "Done in Obliterate mode (the copy that failed, taken as it is), delivered UNVERIFIED: V2 (Text preservation) failed.",
  );
  assert.match(attemptLine(attempt({ mode: "obliterate", marks: ["UNPROVEN (Word made it)"] })), /marked UNPROVEN: what changed was shown to you, not proven\.$/);
  assert.equal(
    attemptLine(attempt({ mode: "default", first: "Its copy could not be made: V2." })),
    "Done in Default mode. It failed first and a second check fixed it: Its copy could not be made: V2.",
  );
});

test("a cell's label names the document, the operation and how it stands", () => {
  const standing = normalizeStanding(RAW);
  const [a, b, c] = standing.documents;
  const [first, second] = standing.operations;
  assert.equal(cellLabel(a, first, a.cells[0]), "a.docx, 1. Remove all headers and footers: done");
  assert.equal(cellLabel(b, first, b.cells[0]), "b.docx, 1. Remove all headers and footers: not done");
  assert.equal(cellLabel(b, second, b.cells[1]), "b.docx, 2. Make the title bold.: done, UNVERIFIED");
  assert.equal(cellLabel(c, second, c.cells[1]), "c.docx, 2. Make the title bold.: not run");
});

test("the line under the grid counts what is owed, and the filter lists only those documents", () => {
  const standing = normalizeStanding(RAW);
  assert.equal(owedLine(standing), "On your to-do list: 1 operation on 1 document.");
  assert.equal(owedLine({ ...standing, owed: { cells: 7, documents: 6 } }), "On your to-do list: 7 operations on 6 documents.");
  assert.equal(owedLine({ ...standing, owed: { cells: 0, documents: 0 } }), "Your to-do list is empty: nothing that was run on a document is still not done.");
  assert.deepEqual(visibleRows(standing, false).map((r) => r.name), ["a.docx", "b.docx", "c.docx"]);
  assert.deepEqual(visibleRows(standing, true).map((r) => r.name), ["b.docx"]);
});

test("a column is headed by the recipe's name or the request, cut to fit", () => {
  assert.equal(operationTitle({ request: "Remove all comments", name: "" }), "Remove all comments");
  assert.equal(operationTitle({ request: "1. One; 2. Two", name: "Issue set" }), "Issue set");
  assert.equal(operationTitle({ request: "", name: "" }), "(no request kept)");
  const long = operationTitle({ request: "x".repeat(80), name: "" }, 20);
  assert.equal(long.length, 20);
  assert.ok(long.endsWith("…"));
});

test("the to-do list is the failed cells: each with its document, what it owes and why, oldest operation first", () => {
  const standing = normalizeStanding(RAW);
  assert.deepEqual(owedItems(standing), [
    { path: "C:/b.docx", name: "b.docx", operation: "a1", number: 1, request: "Remove all headers and footers", reason: "It has no header to remove." },
  ]);
  assert.deepEqual(owedItems(normalizeStanding(null)), []);
});

test("a cell the user waived reads as not needed, is off the to-do list, and carries no hand-off", () => {
  const waived = { ...attempt({ state: "waived", mode: "default", why: "waived", reason: "You said it is not needed on this one." }) };
  const raw = {
    ...RAW,
    documents: [
      RAW.documents[0],
      { ...RAW.documents[1], cells: [{ ...waived, attempts: [attempt({ state: "failed", reason: "No." }), waived], obliterate: "" }, RAW.documents[1].cells[1]] },
      RAW.documents[2],
    ],
  };
  const standing = normalizeStanding(raw);
  const cell = standing.documents[1].cells[0];
  assert.deepEqual([cellSymbol(cell), cellTone(cell), cell.obliterate], ["–", "none", undefined]);
  assert.equal(attemptLine(cell), "Not needed on this one: your decision. It is off your to-do list.");
  assert.equal(cellLabel(standing.documents[1], standing.operations[0], cell), "b.docx, 1. Remove all headers and footers: not needed");
  assert.deepEqual(standing.owed, { cells: 0, documents: 0 });
  assert.deepEqual(owedItems(standing), []);
});

test("a failed cell carries Obliterate's hand-off only when the server sends it", () => {
  const failedCell = { ...RAW.documents[1].cells[0], obliterate: "", failed_copy: ["V2 (Text preservation)", 7] };
  const standing = normalizeStanding({ ...RAW, documents: [RAW.documents[0], { ...RAW.documents[1], cells: [failedCell, RAW.documents[1].cells[1]] }, RAW.documents[2]] });
  assert.equal(standing.documents[1].cells[0].obliterate, "");
  assert.deepEqual(standing.documents[1].cells[0].failed_copy, ["V2 (Text preservation)"]);
  assert.equal(normalizeStanding(RAW).documents[1].cells[0].obliterate, undefined);     // Obliterate hidden in Settings
});

test("working on a cell is named for the mode, and waits while anything else is under way", () => {
  assert.equal(workLabel("safe"), "Work on it in Default mode");
  assert.equal(workLabel("default"), "Work on it now");
  assert.equal(workLabel("obliterate", true), "Work on them now");
  const rest = { busy: false, focus: [], stage: "done" as const, recovery_blocked: "" };
  assert.equal(workBlocked(rest), "");
  assert.match(workBlocked({ ...rest, busy: true }), /current work/);
  assert.match(workBlocked({ ...rest, focus: ["/a"] }), /pass over your to-do list/);
  assert.match(workBlocked({ ...rest, stage: "preview" }), /discard the preview/);
  assert.equal(workBlocked({ ...rest, recovery_blocked: "Finish it first." }), "Finish it first.");
});
