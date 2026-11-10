// Obliterate's rescue at attach on the page (lib/rescue.ts): when a refused document offers Try to rescue, what
// the button and the password dialog say, and what a rescued document's mark says.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  PASSWORD_SAYS,
  RESCUE_NEEDS_WORD,
  RESCUE_TRIES,
  SALVAGED_TAG,
  canRescue,
  documentsAccept,
  rescueTip,
  salvagedTip,
} from "../src/lib/rescue.ts";
import { OBLITERATE_COSTS, OBLITERATE_GRANTS, OBLITERATE_KEPT } from "../src/lib/modes.ts";
import type { WordMenu } from "../src/types.ts";

const WORD: WordMenu = { available: true, why: "", actions: [], minutes: 5 };
const NO_WORD: WordMenu = { ...WORD, available: false, why: "Microsoft Word is not installed on this computer." };

test("a refused document offers the rescue in Obliterate mode alone, when there is something to try", () => {
  const refused = { kind: "document", state: "refused", rescue: "doctype" } as const;
  assert.equal(canRescue(refused, "obliterate"), true);
  assert.equal(canRescue(refused, "default"), false);
  assert.equal(canRescue(refused, "safe"), false);
  assert.equal(canRescue({ kind: "document", state: "refused" }, "obliterate"), false);
  assert.equal(canRescue({ kind: "document", state: "ok", rescue: "doctype" }, "obliterate"), false);
  assert.equal(canRescue({ kind: "template", state: "refused", rescue: "doctype" }, "obliterate"), false);
  // the picker takes the kinds only a rescue opens in that mode alone
  assert.equal(documentsAccept("obliterate"), ".docx,.doc,.docm");
  assert.equal(documentsAccept("default"), ".docx");
  assert.equal(documentsAccept("safe"), ".docx");
});

test("the button says what will be tried, that the file is only read, and when Word is missing", () => {
  assert.deepEqual(Object.keys(RESCUE_TRIES).sort(), ["doctype", "format", "no-paragraph", "password", "strict", "too-large", "unopenable"]);
  assert.ok(RESCUE_NEEDS_WORD.every((kind) => /^Word opens a private copy/.test(RESCUE_TRIES[kind])));
  const tip = rescueTip({ name: "Old.doc", rescue: "format" }, false, WORD);
  assert.match(tip, /^Word opens a private copy and saves it as a Word document \(\.docx\)\. Your file is read, never changed/);
  assert.match(tip, /attached in its place, marked SALVAGED\. No AI is used$/);
  assert.match(rescueTip({ name: "Old.doc", rescue: "format" }, false, NO_WORD), /Microsoft Word is not available on this computer/);
  // the app's own rescues need no Word
  assert.doesNotMatch(rescueTip({ name: "A.docx", rescue: "doctype" }, false, NO_WORD), /not available/);
  assert.equal(rescueTip({ name: "A.docx", rescue: "doctype" }, true, WORD), "Finish or stop the current work first");
});

test("the password dialog says where the password goes and that the rescued document has none", () => {
  assert.ok(PASSWORD_SAYS.some((line) => /given to Word for this one attempt and kept nowhere/.test(line)));
  assert.ok(PASSWORD_SAYS.some((line) => /never sent to an AI model/.test(line)));
  assert.ok(PASSWORD_SAYS.some((line) => /will have NO password/.test(line)));
  const modal = readFileSync(new URL("../src/components/RescuePasswordModal.tsx", import.meta.url), "utf8");
  assert.match(modal, /type="password"/);
  assert.match(modal, /autoComplete="off"/);
  assert.match(modal, /data-capability="rescue\.password"/);
  // the value stays in the dialog: nothing writes it to storage
  assert.doesNotMatch(modal, /localStorage|sessionStorage/);
  const app = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /localStorage[^\n]*password|password[^\n]*localStorage/i);
});

test("a rescued document's mark says what it was rescued from and that nothing proves it", () => {
  const tip = salvagedTip({ from: "Old.doc", kind: "format", steps: ["Word opened a private copy of the .doc file and saved it as a .docx."], notes: [] });
  assert.equal(SALVAGED_TAG, "Salvaged");
  assert.match(tip, /^Rescued from Old\.doc, a file the app could not read as it was\. Word opened a private copy/);
  assert.match(tip, /Nothing proves it holds everything that file did: compare the two in Word\./);
  assert.match(tip, /Its copies are checked against this rescued document\.$/);
  const noted = salvagedTip({ from: "S.docx", kind: "password", steps: ["x."], notes: ["The rescued document has NO password."] });
  assert.match(noted, /The rescued document has NO password\.$/);
  const tray = readFileSync(new URL("../src/components/DocumentsTray.tsx", import.meta.url), "utf8");
  assert.match(tray, /data-capability="rescue\.try"/);
  assert.match(tray, /data-salvaged="document"/);
  const bench = readFileSync(new URL("../src/components/Workbench.tsx", import.meta.url), "utf8");
  assert.match(bench, /data-salvaged="copy"/);
});

test("the warning says what a rescue grants, what it costs and what still holds", () => {
  assert.ok(OBLITERATE_GRANTS.some((line) => /^A file the app refuses when you attach it may be rescued/.test(line)));
  assert.ok(OBLITERATE_GRANTS.some((line) => /Your file is read and never changed, and no AI is used\.$/.test(line)));
  assert.ok(OBLITERATE_COSTS.some((line) => /marked SALVAGED for good, and so is every copy made from it/.test(line)));
  assert.ok(OBLITERATE_COSTS.some((line) => /A document rescued with its password has none afterwards/.test(line)));
  assert.ok(OBLITERATE_KEPT.some((line) => /given to Word for that one attempt and kept nowhere/.test(line)));
  assert.ok(OBLITERATE_KEPT.some((line) => /A lock file, a folder, a missing file and any other kind of file are still refused/.test(line)));
});
