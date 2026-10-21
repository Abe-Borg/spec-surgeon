/**
 * The workbench's small rules (lib/workbench.ts): card titles, page counts,
 * the run folder, the kind tags.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Plan, PlanGroup } from "../src/types.ts";
import {
  ACCEPT,
  groupTitle,
  kindTag,
  pageCount,
  parentFolder,
  planHasContent,
  runFolder,
  stagesNote,
  trayTitle,
} from "../src/lib/workbench.ts";

const group = (overrides: Partial<PlanGroup> = {}): PlanGroup => ({
  id: "g",
  label: "",
  count: 0,
  files: [],
  changes: [],
  assumptions: [],
  ...overrides,
});

test("the run folder is the parent of the first copy, with either separator", () => {
  assert.equal(parentFolder("C:\\Users\\abe\\runs\\1\\outputs\\a.docx"), "C:\\Users\\abe\\runs\\1\\outputs");
  assert.equal(parentFolder("/home/abe/runs/1/outputs/a.docx"), "/home/abe/runs/1/outputs");
  assert.equal(parentFolder("/home/abe/runs/1/outputs/"), "/home/abe/runs/1");
  assert.equal(parentFolder("a.docx"), "");
  assert.equal(runFolder({ results: [] }), "");
  assert.equal(
    runFolder({ results: [{ source: "s", output: "C:/runs/2/outputs/a.docx", name: "a.docx" }] }),
    "C:/runs/2/outputs",
  );
});

test("the page count reads 'Pages: 12 → 13 (+1)' and flags growth only", () => {
  assert.deepEqual(pageCount({ pages_before: 12, pages_after: 13 }), { text: "Pages: 12 → 13 (+1)", flag: "+1", grew: true });
  assert.deepEqual(pageCount({ pages_before: 12, pages_after: 12 }), { text: "Pages: 12 → 12", flag: "", grew: false });
  assert.deepEqual(pageCount({ pages_before: 12, pages_after: 10 }), { text: "Pages: 12 → 10 (−2)", flag: "−2", grew: false });
  assert.deepEqual(pageCount({ pages_before: null, pages_after: 10 }), { text: "", flag: "", grew: false });
});

test("one layout reads as the plan; several read as '<label> · N documents'", () => {
  assert.equal(groupTitle(group({ label: "Styled, PR1 articles", count: 8 }), false), "Here’s what will change");
  assert.equal(groupTitle(group({ label: "Styled, PR1 articles", count: 8 }), true), "Styled, PR1 articles · 8 documents");
  assert.equal(groupTitle(group({ label: "Typed numbers", count: 1 }), true), "Typed numbers · 1 document");
  assert.equal(groupTitle(group({ label: "  ", files: ["a", "b"], count: undefined as unknown as number }), true), "One layout · 2 documents");
});

test("the ordered-steps note appears only past one stage", () => {
  assert.equal(stagesNote({ stages: 1 }), "");
  assert.equal(stagesNote({ stages: 3 }), "in 3 ordered steps");
  assert.equal(stagesNote(null), "");
});

test("a plan has content when any group has a change or an assumption, or a target or a file set aside exists", () => {
  const empty: Plan = { groups: [group()], targets: [], set_aside: [], stages: 1 };
  assert.equal(planHasContent(empty), false);
  assert.equal(planHasContent(null), false);
  assert.equal(planHasContent({ ...empty, groups: [group({ changes: [{ title: "t", summary: "s", details: [] }] })] }), true);
  assert.equal(planHasContent({ ...empty, groups: [group({ assumptions: ["a"] })] }), true);
  assert.equal(planHasContent({ ...empty, targets: [{ title: "t", source: "s", details: [] }] }), true);
  assert.equal(planHasContent({ ...empty, set_aside: [{ name: "n", reason: "r" }] }), true);
});

test("the kind tag names a template, a picture or a table; documents carry none", () => {
  assert.equal(kindTag({ kind: "document", name: "a.docx" }), "");
  assert.equal(kindTag({ kind: "template", name: "t.docx" }), "Template");
  assert.equal(kindTag({ kind: "reference", name: "logo.PNG" }), "Picture");
  assert.equal(kindTag({ kind: "reference", name: "photo.jpeg" }), "Picture");
  assert.equal(kindTag({ kind: "reference", name: "values.xlsx" }), "Table");
  assert.equal(kindTag({ kind: "reference", name: "values.csv" }), "Table");
});

test("the tray title counts accepted Word documents only", () => {
  const doc = { path: "a", name: "a.docx", kind: "document" as const, state: "ok" as const, notes: [], reason: "", current: "a" };
  assert.equal(trayTitle({ attachments: [] }), "Your documents");
  assert.equal(
    trayTitle({ attachments: [doc, { ...doc, state: "note" }, { ...doc, state: "refused" }, { ...doc, kind: "template" }] }),
    "Your documents · 2",
  );
});

test("the browser fallback filters match what the app accepts", () => {
  assert.equal(ACCEPT.documents, ".docx");
  assert.equal(ACCEPT.template, ".docx");
  for (const ext of ["png", "jpg", "jpeg", "gif", "xlsx", "csv", "json"]) assert.ok(ACCEPT.references.includes(`.${ext}`));
});
