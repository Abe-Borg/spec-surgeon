// Obliterate's named Word actions on the page (lib/wordActions.ts): the order the ticked actions run in, what holds
// an action or the Run button, and the words a copy Word made is shown with.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  WORD_ACCOUNT_TITLE,
  WORD_BOTH,
  WORD_NONE,
  WORD_OTHER_CHANGES_TITLE,
  WORD_SAYS,
  everyLabel,
  runHeld,
  toggleAction,
  wordHeld,
  wordPatience,
  wordRequest,
  wordTip,
} from "../src/lib/wordActions.ts";
import {
  OBLITERATE_COSTS,
  OBLITERATE_GRANTS,
  OBLITERATE_KEPT,
  UNPROVEN_MEANS,
  WORD_UNPROVEN_MEANS,
  copyMark,
  madeByWord,
  otherChangesTitle,
  resultsLead,
  sampleBadge,
  unverifiedLine,
} from "../src/lib/modes.ts";
import { normalizeView } from "../src/lib/view.ts";
import { WORDS_CHANGE_TITLE, WORD_WORDS_CHANGE_TITLE, wordChangeLine, wordsHeadings } from "../src/lib/wordChanges.ts";
import type { WordMenu } from "../src/types.ts";

const MENU: WordMenu = {
  available: true,
  why: "",
  minutes: 5,
  actions: [
    { id: "repair", label: "Open and repair, then save", does: "Word opens the copy with its Open and Repair command and saves it again." },
    { id: "accept_changes", label: "Accept all tracked changes", does: "Word accepts every tracked change in the copy." },
    { id: "reject_changes", label: "Reject all tracked changes", does: "Word rejects every tracked change in the copy." },
    { id: "unlink_fields", label: "Unlink fields", does: "Word replaces every field in the body with the text it shows." },
  ],
};
const WORD_MARK = "UNPROVEN (Word made it: what it changed was shown to you, not proven)";
const METHOD_MARK = "UNPROVEN (a custom method made it: what it changed was shown to you, not proven)";

test("actions run in the order ticked, with Open and Repair first", () => {
  let chosen: string[] = [];
  chosen = toggleAction(chosen, "unlink_fields");
  chosen = toggleAction(chosen, "accept_changes");
  assert.deepEqual(chosen, ["unlink_fields", "accept_changes"]);
  chosen = toggleAction(chosen, "repair");
  assert.deepEqual(chosen, ["repair", "unlink_fields", "accept_changes"]);
  // accepting and rejecting every tracked change cannot both run: the second is held, and says why
  assert.equal(wordHeld(chosen, "reject_changes"), WORD_BOTH);
  assert.deepEqual(toggleAction(chosen, "reject_changes"), chosen);
  assert.equal(wordHeld(chosen, "accept_changes"), "");
  chosen = toggleAction(chosen, "accept_changes");
  assert.deepEqual(chosen, ["repair", "unlink_fields"]);
  assert.equal(wordHeld(chosen, "reject_changes"), "");
});

test("Run waits for a choice, for Word, and for the work in hand", () => {
  assert.equal(runHeld(MENU, [], false), WORD_NONE);
  assert.equal(runHeld(MENU, ["repair"], false), "");
  assert.equal(runHeld(MENU, ["repair"], true), "Finish or stop the current work first");
  const none: WordMenu = { ...MENU, available: false, why: "Microsoft Word is not installed on this computer." };
  assert.equal(runHeld(none, ["repair"], false), none.why);
  assert.match(runHeld(null, ["repair"], false), /not available/);
  assert.match(wordTip("A.docx", false, none), /Microsoft Word is not available on this computer/);
  assert.match(wordTip("A.docx", false, MENU), /^Have Microsoft Word itself run named actions on a copy of A\.docx\. No AI is used$/);
  assert.equal(wordTip("A.docx", true, MENU), "Finish or stop the current work first");
});

test("the request and the scope are said as the server says them", () => {
  assert.equal(wordRequest("A.docx", ["Unlink fields"], false, 3), "Run in Word on A.docx: Unlink fields.");
  assert.equal(
    wordRequest("A.docx", ["Accept all tracked changes", "Unlink fields"], true, 3),
    "Run in Word on every document (3): Accept all tracked changes; then Unlink fields.",
  );
  assert.equal(wordRequest("A.docx", ["Unlink fields"], true, 1), "Run in Word on A.docx: Unlink fields.");
  assert.equal(everyLabel(1), "Also run on the other document");
  assert.equal(everyLabel(4), "Also run on the other 4 documents");
  assert.equal(wordPatience(5), "Word may take up to 5 minutes a document. Stop ends it.");
});

test("a copy Word made says so: its badge, its lists and its mark", () => {
  const shown = sampleBadge({ passed: true, unproven: true, made_by: "word" });
  assert.equal(shown.label, "Not proven");
  assert.match(shown.title, /^Word made this copy, running the named actions and saving the whole file again/);
  assert.match(sampleBadge({ passed: true, unproven: true }).title, /^A custom method made this copy/);
  assert.match(sampleBadge({ passed: true, no_copy: true, made_by: "word" }).title, /^Word could not make a copy of this document, or found nothing to do in it/);
  assert.equal(otherChangesTitle({ unproven: true, made_by: "word" }), WORD_OTHER_CHANGES_TITLE);
  assert.equal(wordsHeadings({ made_by: "word" }).title, WORD_WORDS_CHANGE_TITLE);
  assert.equal(wordsHeadings({}).title, WORDS_CHANGE_TITLE);
  // a tracked change Word accepted or rejected was the original's: never said to sit in the copy's tracked changes
  const settled = wordChangeLine({ part: "word/document.xml", where: "body paragraph 6", paragraph: 5, removed: ["black steel"], added: [], settled: true });
  assert.match(JSON.stringify(settled), /a tracked change of the original, settled in the copy/);
  assert.doesNotMatch(JSON.stringify(settled), /in the copy's tracked changes/);
  assert.match(WORD_ACCOUNT_TITLE, /Word's own count, not the independent reader's/);
  // the mark names who made the copy, now or in an earlier copy this one builds on
  assert.equal(copyMark([WORD_MARK]), "UNPROVEN");
  assert.equal(madeByWord([WORD_MARK]), true);
  assert.equal(madeByWord(["in an earlier copy: " + WORD_MARK]), true);
  assert.equal(madeByWord([METHOD_MARK]), false);
  assert.equal(unverifiedLine([WORD_MARK]), `UNPROVEN — ${WORD_UNPROVEN_MEANS}. Check it in Word before you use it.`);
  assert.equal(unverifiedLine([METHOD_MARK]), `UNPROVEN — ${UNPROVEN_MEANS}. Check it in Word before you use it.`);
  assert.match(unverifiedLine(["V1 (Package integrity)", WORD_MARK]), /^UNVERIFIED — V1 \(Package integrity\) failed\. Word made it, so the rest/);
  assert.match(resultsLead([{ unverified: [WORD_MARK] }]), /made by a custom method or by Word's named actions, with nothing predicted/);
});

test("the warning says what Word may do, what that costs and what still holds", () => {
  assert.ok(OBLITERATE_GRANTS.some((line) => /^Microsoft Word itself may change a copy, through a fixed menu of named actions/.test(line)));
  assert.ok(OBLITERATE_GRANTS.some((line) => /you approve the copies Word made, and each is delivered marked UNPROVEN/.test(line)));
  assert.ok(OBLITERATE_COSTS.some((line) => /A copy Word made is checked for less still: that it opens as a file/.test(line)));
  assert.ok(OBLITERATE_KEPT.some((line) => /no script or command is written for it, by you or by Claude/.test(line)));
  assert.ok(OBLITERATE_KEPT.some((line) => /Create never runs it again: the copy you were shown is the copy delivered/.test(line)));
  assert.ok(WORD_SAYS.some((line) => /Your file is never opened by it, macros stay disabled, and no AI is used/.test(line)));
});

test("the menu reaches the page in Obliterate mode alone", () => {
  const raw = { mode: "obliterate", word: { available: true, why: "", minutes: 5, actions: [{ id: "repair", label: "Open and repair, then save", does: "x" }, { label: "no id" }] } };
  assert.deepEqual(normalizeView(raw).word, { available: true, why: "", minutes: 5, actions: [{ id: "repair", label: "Open and repair, then save", does: "x" }] });
  assert.equal(normalizeView({ ...raw, mode: "default" }).word, null);
  assert.equal(normalizeView({ ...raw, mode: "safe" }).word, null);
  assert.equal(normalizeView({ mode: "obliterate" }).word, null); // an older server
});

test("the button, the dialog's Run and its scope box carry their capabilities", () => {
  const tray = readFileSync(new URL("../src/components/DocumentsTray.tsx", import.meta.url), "utf8");
  const modal = readFileSync(new URL("../src/components/WordActionsModal.tsx", import.meta.url), "utf8");
  assert.match(tray, /data-capability="word\.actions"/);
  assert.match(modal, /data-capability="word\.run"/);
  assert.match(modal, /data-capability="word\.every"/);
  // nothing is typed in the dialog: the menu is all Word can be asked
  assert.doesNotMatch(modal, /<textarea|type="text"/);
});
