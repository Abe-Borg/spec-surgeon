/**
 * Safe mode's buttons on the page (lib/recipes.ts): what can be pressed with what is attached, the form a button
 * opens with, and what it still needs before Preview.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, Recipe, View } from "../src/types.ts";
import {
  EMPTY_SAFE_FORM,
  availability,
  clearForm,
  fieldRows,
  fieldShown,
  filledFromEachDocument,
  filledLines,
  grouped,
  initialInputs,
  leaveForm,
  missing,
  openForm,
  payload,
  pictures,
  refreshInputs,
  tables,
} from "../src/lib/recipes.ts";
import { ADD_KEY, SWITCH_TO_DEFAULT, safeNoteWords } from "../src/lib/safeNote.ts";
import { EMPTY_VIEW, normalizeView } from "../src/lib/view.ts";

const file = (name: string, kind: Attachment["kind"], state: Attachment["state"] = "ok"): Attachment => ({
  path: `C:/Specs/${name}`,
  name,
  kind,
  state,
  notes: [],
  reason: "",
  current: `C:/Specs/${name}`,
});

const view = (attachments: Attachment[], template: View["template"] = EMPTY_VIEW.template) => ({
  attachments,
  template,
});

const recipe = (overrides: Partial<Recipe>): Recipe => ({
  id: "remove_highlight",
  label: "Remove highlighting",
  group: "Clean up",
  summary: "Removes Word's highlighter colour.",
  needs: [],
  fields: [],
  ...overrides,
});

const HEADERS = recipe({
  id: "headers_footers_template",
  label: "Match headers and footers to template",
  group: "Headers & footers",
  needs: ["template"],
  fields: [{ name: "slots", kind: "slots", label: "Values", required: false }],
});
const FIND = recipe({
  id: "find_replace",
  label: "Find and replace",
  group: "Text",
  fields: [
    { name: "pairs", kind: "pairs", label: "Find → replace with", required: false },
    { name: "table", kind: "table", label: "Or a table", required: false },
    { name: "tracked", kind: "check", label: "Tracked", required: false, default: false },
    { name: "whole_word", kind: "check", label: "Whole words", required: false, default: true },
  ],
});
const LOGO = recipe({
  id: "swap_logo",
  needs: ["picture"],
  fields: [
    { name: "image", kind: "picture", label: "New logo", required: true },
    { name: "old", kind: "picture", label: "Old logo", required: false, optional_choice: "Every picture in the headers" },
  ],
});
const WATERMARK = recipe({
  id: "watermark",
  fields: [
    { name: "action", kind: "choice", label: "Watermark", required: false, default: "add",
      options: [{ value: "add", label: "Add" }, { value: "remove", label: "Remove" }] },
    { name: "text", kind: "text", label: "Watermark words", required: false },
  ],
});

test("a button waits for the documents and for what it needs", () => {
  assert.deepEqual(availability(recipe({}), view([])), { ready: false, reason: "Add the Word documents first." });
  assert.equal(availability(recipe({}), view([file("a.docx", "document", "refused")])).ready, false);
  const docs = [file("a.docx", "document")];
  assert.equal(availability(recipe({}), view(docs)).ready, true);
  assert.equal(availability(HEADERS, view(docs)).reason, "Attach your template first.");
  const withTemplate = [...docs, file("t.docx", "template")];
  assert.equal(availability(HEADERS, view(withTemplate, { kind: "prepared", typed_slots: [], reason: "" })).ready, true);
  assert.deepEqual(
    availability(HEADERS, view(withTemplate, { kind: "unusable", typed_slots: [], reason: "t.docx has no header." })),
    { ready: false, reason: "t.docx has no header." },
  );
  assert.equal(availability(LOGO, view(docs)).reason, "Attach the new picture first.");
  assert.equal(availability(LOGO, view([...docs, file("logo.png", "reference")])).ready, true);
});

test("pictures and tables are told apart by their names", () => {
  const v = view([file("logo.PNG", "reference"), file("pairs.csv", "reference"), file("x.bmp", "reference", "refused")]);
  assert.deepEqual(pictures(v).map((p) => p.name), ["logo.PNG"]);
  assert.deepEqual(tables(v).map((p) => p.name), ["pairs.csv"]);
});

test("a form opens with its defaults, an empty pair row, the template's slots and the first picture", () => {
  const v = view([file("a.docx", "document"), file("logo.png", "reference")], {
    kind: "prepared",
    typed_slots: ["ISSUED FOR PERMIT"],
    reason: "",
  });
  assert.deepEqual(initialInputs(FIND, v), { pairs: [{ find: "", replace: "" }], table: "", tracked: false, whole_word: true });
  assert.deepEqual(initialInputs(HEADERS, v), { slots: { "ISSUED FOR PERMIT": "" } });
  assert.deepEqual(initialInputs(LOGO, v), { image: "C:/Specs/logo.png", old: "" });
  assert.deepEqual(initialInputs(WATERMARK, v), { action: "add", text: "" });
});

test("a form keeps what was typed when attachments change, and never keeps a file no longer attached", () => {
  const before = view([file("a.docx", "document"), file("logo.png", "reference"), file("new.png", "reference")], {
    kind: "prepared",
    typed_slots: ["ISSUED FOR PERMIT", "DATE"],
    reason: "",
  });
  const typed = { image: "C:/Specs/new.png", old: "C:/Specs/logo.png" };
  // the chosen new logo removed: the first picture still attached; the old one stays chosen
  const after = view([file("a.docx", "document"), file("logo.png", "reference")]);
  assert.deepEqual(refreshInputs(LOGO, typed, after), { image: "C:/Specs/logo.png", old: "C:/Specs/logo.png" });
  assert.deepEqual(refreshInputs(LOGO, typed, before), typed);
  // a table removed: no table chosen; the typed rows stay
  const rows = { pairs: [{ find: "ACME", replace: "Contoso" }], table: "C:/Specs/pairs.csv", tracked: true, whole_word: false };
  assert.deepEqual(refreshInputs(FIND, rows, view([file("a.docx", "document")])), { ...rows, table: "" });
  // a template value stays while the template asks for it; a new one starts empty
  const later = view([file("a.docx", "document")], { kind: "prepared", typed_slots: ["DATE", "PHASE"], reason: "" });
  assert.deepEqual(refreshInputs(HEADERS, { slots: { "ISSUED FOR PERMIT": "BID", DATE: "2026" } }, later),
    { slots: { DATE: "2026", PHASE: "" } });
});

test("the form names the placeholders each document fills itself, so it never asks for them", () => {
  assert.equal(filledFromEachDocument({ slot_roles: { "00 00 00": "number", "Specification Name": "title" } }),
    "Each document fills in its own section number at “00 00 00”, and its own title at “Specification Name”.");
  assert.equal(filledFromEachDocument({ slot_roles: { "XX XX XX": "number", SECTION_NUMBER: "number", NUMBER: "number" } }),
    "Each document fills in its own section number at “XX XX XX”, “SECTION_NUMBER” and “NUMBER”.");
  assert.equal(filledFromEachDocument({ slot_roles: { TITLE: "title" } }), "Each document fills in its own title at “TITLE”.");
  assert.equal(filledFromEachDocument({ slot_roles: {} }), "");
  assert.equal(filledFromEachDocument(undefined), "");
});

const BODY = recipe({
  id: "body_formatting",
  label: "Match body formatting to template",
  group: "Fonts & formatting",
  needs: ["template"],
  fields: [
    { name: "roles", kind: "roles", label: "Your template's style for each part of a section", required: false },
    { name: "page", kind: "check", label: "Page size and margins", required: false, default: true },
  ],
});
const bodyInfo = (roles: Record<string, string>, problems: string[] = []) => ({
  kind: "none" as const,
  typed_slots: [],
  reason: "",
  body: {
    roles,
    styles: [{ id: "PRT", name: "PRT" }, { id: "ART", name: "ART" }, { id: "TTL", name: "Section Title" }],
    labels: { part: "PART", article: "Article (1.1)", section_title: "Section title" },
    order: ["section_title", "part", "article"],
    page: "8.5 × 11 in portrait, margins 1 in",
    font: "Arial 10 pt",
    problems,
  },
});

test("the body button starts from the template's roles and says why it cannot be pressed", () => {
  const files = [file("a.docx", "document"), file("T.docx", "template")];
  const ok = view(files, bodyInfo({ part: "PRT", article: "ART", section_title: "TTL" }));
  assert.equal(availability(BODY, ok).ready, true);
  assert.deepEqual(initialInputs(BODY, ok), { roles: { part: "PRT", article: "ART", section_title: "TTL" }, page: true });
  const bad = view(files, bodyInfo({}, ["T.docx: no style stands for PART."]));
  assert.deepEqual(availability(BODY, bad), { ready: false, reason: "T.docx: no style stands for PART." });
  assert.equal(availability(BODY, view([file("a.docx", "document")])).reason, "Attach your template first.");
});

test("a role chosen by hand stays while the template still has that style", () => {
  const files = [file("a.docx", "document"), file("T.docx", "template")];
  const v = view(files, bodyInfo({ part: "PRT", article: "ART" }));
  const typed = { roles: { part: "PRT", article: "ART", section_title: "", paragraph: "Gone" }, page: false };
  assert.deepEqual(refreshInputs(BODY, typed, v), { roles: { part: "PRT", article: "ART", section_title: "" }, page: false });
});

test("a form says what it still needs, and sends only filled rows", () => {
  assert.equal(missing(FIND, { pairs: [{ find: "", replace: "" }], table: "" }),
    "Type a pair of words to find and replace, or choose a table.");
  assert.equal(missing(FIND, { pairs: [{ find: "", replace: "x" }], table: "" }), "Each row needs the words to find.");
  assert.equal(missing(FIND, { pairs: [], table: "C:/Specs/pairs.csv" }), "");
  const ready = { pairs: [{ find: "ACME", replace: "Contoso" }, { find: "", replace: "" }], table: "" };
  assert.equal(missing(FIND, ready), "");
  assert.deepEqual(payload(FIND, ready).pairs, [{ find: "ACME", replace: "Contoso" }]);
  assert.equal(missing(HEADERS, { slots: { "ISSUED FOR PERMIT": " " } }), "Type the value for “ISSUED FOR PERMIT”.");
  assert.equal(missing(LOGO, { image: "", old: "" }), "Choose the new logo.");
  assert.equal(missing(WATERMARK, { action: "add", text: "" }), "Type the watermark words.");
  assert.equal(missing(WATERMARK, { action: "remove", text: "" }), "");
  assert.equal(fieldShown(WATERMARK, WATERMARK.fields[1], { action: "remove" }), false);
});

test("the buttons keep the server's order in their groups", () => {
  const g = grouped([HEADERS, recipe({ id: "a", group: "Clean up" }), recipe({ id: "b", group: "Clean up" })]);
  assert.deepEqual(g.map((x) => [x.group, x.recipes.length]), [["Headers & footers", 1], ["Clean up", 2]]);
});

test("the view carries the mode, the template and what a button left untouched", () => {
  const v = normalizeView({ mode: "safe", modes: ["safe", "default", "nope"], left_untouched: [{ path: "p", name: "n", reason: "r" }] });
  assert.equal(v.mode, "safe");
  assert.deepEqual(v.modes, ["safe", "default"]);
  assert.equal(v.template.kind, "none");
  assert.equal(v.left_untouched.length, 1);
  assert.equal(normalizeView({}).mode, "default");
});

test("going back to a button finds what was typed in its form, until the form is cleared", () => {
  const docs = view([file("a.docx", "document"), file("pairs.csv", "reference")]);
  let form = openForm(EMPTY_SAFE_FORM, FIND, docs);
  assert.deepEqual(form.inputs, initialInputs(FIND, docs));
  const typed = { pairs: [{ find: "ACME", replace: "Contoso" }], table: "C:/Specs/pairs.csv", tracked: true, whole_word: false };
  form = { ...form, inputs: typed };
  form = leaveForm(form);
  assert.equal(form.recipe, null);
  assert.deepEqual(form.inputs, {});
  // another button opens fresh, and leaving it keeps its own values beside the first one's
  form = leaveForm({ ...openForm(form, WATERMARK, docs), inputs: { action: "add", text: "DRAFT" } });
  assert.deepEqual(Object.keys(form.kept).sort(), ["find_replace", "watermark"]);
  form = openForm(form, FIND, docs);
  assert.equal(form.recipe, "find_replace");
  assert.deepEqual(form.inputs, typed);
  // what was kept is taken up to the attachments as they are now: a table no longer attached is not sent
  assert.deepEqual(openForm(leaveForm(form), FIND, view([file("a.docx", "document")])).inputs, { ...typed, table: "" });
  // Clear form: the form as it first opens, and nothing kept for that button any more
  form = clearForm(form, FIND, docs);
  assert.deepEqual(form.inputs, initialInputs(FIND, docs));
  assert.equal("find_replace" in form.kept, false);
  assert.deepEqual(form.kept.watermark, { action: "add", text: "DRAFT" });
  // leaving the button list changes nothing
  assert.equal(leaveForm(EMPTY_SAFE_FORM), EMPTY_SAFE_FORM);
});

const PDF = recipe({ id: "clean_pdf", label: "Save clean PDFs", group: "Files & properties", needs: ["pdf"] });

test("Save clean PDFs waits for a program that makes PDFs, and says why", () => {
  const docs = [file("a.docx", "document")];
  const pdf = (available: boolean, reason = "") => ({ ...view(docs), pdf: { available, renderer: available ? "Microsoft Word" : "", reason } });
  assert.equal(availability(PDF, pdf(true)).ready, true);
  assert.deepEqual(availability(PDF, pdf(false, "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found.")), {
    ready: false,
    reason: "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found.",
  });
  assert.match(availability(PDF, pdf(false)).reason, /Microsoft Word or LibreOffice/);
  // the documents come first, and a server that says nothing about PDFs never holds the button back
  assert.equal(availability(PDF, { ...view([]), pdf: { available: false, renderer: "", reason: "x" } }).reason, "Add the Word documents first.");
  assert.equal(availability(PDF, view(docs)).ready, true);
  assert.equal(availability(PDF, normalizeView({ attachments: docs })).ready, true);
});

const STAMP = recipe({
  id: "issue_stamp",
  label: "Change the issue stamp",
  group: "Text",
  fields: [
    { name: "current", kind: "lines", label: "The stamp as it reads now", required: true },
    { name: "text", kind: "text", label: "The new stamp", required: true },
  ],
});
const PAGE = recipe({
  id: "page_setup",
  label: "Page size and margins",
  group: "Fonts & formatting",
  fields: [
    { name: "paper", kind: "choice", label: "Paper", required: false, default: "letter",
      options: [{ value: "letter", label: "Letter" }] },
    ...["top", "bottom", "left", "right"].map((side) => ({
      name: `margin_${side}`, kind: "text" as const, label: `${side} margin (in)`, required: true, default: "1", row: "margins",
    })),
    { name: "header_footer", kind: "text", label: "Header and footer distance", required: false },
  ],
});

test("a list field opens with one empty row, needs one filled row, and is sent without the empty ones", () => {
  const v = view([file("a.docx", "document")]);
  assert.deepEqual(initialInputs(STAMP, v), { current: [""], text: "" });
  assert.equal(missing(STAMP, { current: ["", "  "], text: "ISSUED FOR BID" }), "Type the stamp as it reads now.");
  assert.equal(missing(STAMP, { current: ["ISSUED FOR PERMIT"], text: " " }), "Type the new stamp.");
  assert.equal(missing(STAMP, { current: ["ISSUED FOR PERMIT"], text: "ISSUED FOR BID" }), "");
  assert.deepEqual(payload(STAMP, { current: ["ISSUED FOR PERMIT", "", " "], text: "ISSUED FOR BID" }),
    { current: ["ISSUED FOR PERMIT"], text: "ISSUED FOR BID" });
  assert.deepEqual(filledLines("not a list"), []);
  // a list typed in a form is kept when the attachments change
  assert.deepEqual(refreshInputs(STAMP, { current: ["A", "B"], text: "C" }, v), { current: ["A", "B"], text: "C" });
});

test("the four margins share one row, every other field has its own", () => {
  const rows = fieldRows(PAGE.fields).map((row) => row.map((f) => f.name));
  assert.deepEqual(rows, [["paper"], ["margin_top", "margin_bottom", "margin_left", "margin_right"], ["header_footer"]]);
  assert.deepEqual(initialInputs(PAGE, view([])), {
    paper: "letter", margin_top: "1", margin_bottom: "1", margin_left: "1", margin_right: "1", header_footer: "",
  });
  assert.equal(missing(PAGE, { ...initialInputs(PAGE, view([])), margin_left: "" }), "Type the left margin (in).");
});

test("the Safe mode note says what Safe mode is, what it cannot do, and that Default mode needs an API key", () => {
  const without = safeNoteWords(false);
  const withKey = safeNoteWords(true);
  for (const words of [without, withKey]) {
    assert.match(words.tags, /no AI/);
    assert.match(words.cannot, /anything that isn't a button/);
    assert.match(words.more, /Default mode/);
    assert.match(words.more, /Claude API key/);
    assert.match(words.tail, /Default mode/);
  }
  // no key: the filled button adds one, and the folded line says Default mode needs it
  assert.equal(without.primary, "key");
  assert.match(without.key, /No API key yet/);
  assert.match(without.line, /with a Claude API key/);
  // a key already set: the one filled button switches modes, and nothing asks for a key
  assert.equal(withKey.primary, "default");
  assert.match(withKey.key, /Default mode is ready/);
  assert.doesNotMatch(withKey.line, /API key/);
  assert.equal(ADD_KEY, "Add a Claude API key");
  assert.equal(SWITCH_TO_DEFAULT, "Switch to Default mode");
  // an older server says nothing: the note is open
  assert.equal(normalizeView({}).safe_note_hidden, false);
  assert.equal(normalizeView({ safe_note_hidden: true }).safe_note_hidden, true);
  assert.equal(normalizeView({ safe_note_hidden: "yes" }).safe_note_hidden, false);
});
