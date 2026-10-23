/**
 * Safe mode's buttons on the page (lib/recipes.ts): what can be pressed with what is attached, the form a button
 * opens with, and what it still needs before Preview.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, Recipe, View } from "../src/types.ts";
import { availability, fieldShown, grouped, initialInputs, missing, payload, pictures, tables } from "../src/lib/recipes.ts";
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
  assert.equal(missing(WATERMARK, { action: "add", text: "" }), "Type watermark words.");
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
