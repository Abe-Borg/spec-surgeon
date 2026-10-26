/**
 * The workbench's small rules (lib/workbench.ts): page counts, the run
 * folder, the kind tags.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, Plan, PlanGroup } from "../src/types.ts";
import {
  ACCEPT,
  kindTag,
  pageCount,
  parentFolder,
  planHasContent,
  referencesTitle,
  runFolder,
  scopeTag,
  sectionAttachments,
  traySummary,
  trayTitle,
  undoLink,
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

test("the scope tag says a document waits on the to-do list or is in the pass over it", () => {
  const scope = { todo: [{ path: "/b", name: "b.docx" }], focus: [] as string[] };
  assert.equal(scopeTag({ path: "/a", kind: "document" }, scope), "");
  assert.equal(scopeTag({ path: "/b", kind: "document" }, scope), "To do");
  assert.equal(scopeTag({ path: "/b", kind: "template" }, scope), "");
  assert.equal(scopeTag({ path: "/b", kind: "document" }, { todo: [], focus: ["/b"] }), "Working on");
  assert.equal(scopeTag({ path: "/b", kind: "document" }, { todo: scope.todo, focus: ["/b"] }), "Working on");
});

test("each folded section sums its own attachments up in one line", () => {
  const doc = (path: string, extra: Partial<Attachment> = {}): Attachment => ({
    path, name: path.split("/").pop() ?? path, kind: "document", state: "ok", notes: [], reason: "", current: path, ...extra,
  });
  const attachments = [
    doc("/a.docx"), doc("/b.docx"), doc("/c.docx", { state: "refused" }),
    doc("/t.docx", { kind: "template" }), doc("/logo.png", { kind: "reference" }), doc("/values.xlsx", { kind: "reference" }),
    doc("/broken.png", { kind: "reference", state: "refused" }),
  ];
  const view = { attachments, todo: [], focus: [] };
  assert.equal(traySummary("documents", view), "2 documents · 1 left out");
  assert.equal(traySummary("template", view), "t.docx");
  assert.equal(traySummary("references", view), "1 picture · 1 table · 1 left out");
  assert.equal(traySummary("documents", { attachments: [doc("/a.docx")], todo: [{ path: "/a.docx", name: "a.docx" }], focus: [] }), "1 document · 1 to do");
  assert.equal(traySummary("documents", { attachments: [doc("/a.docx")], todo: [], focus: ["/a.docx"] }), "1 document · working on 1");
  assert.equal(traySummary("documents", { attachments: [], todo: [], focus: [] }), "");
  assert.equal(traySummary("template", { attachments: [doc("/a.docx")], todo: [], focus: [] }), "none");
  assert.equal(traySummary("references", { attachments: [doc("/a.docx")], todo: [{ path: "/a.docx", name: "a.docx" }], focus: [] }), "none");
  assert.equal(traySummary("template", { attachments: [doc("/t.docx", { kind: "template", state: "refused" })], todo: [], focus: [] }), "1 left out");
});

test("each section lists its own kind, and the pictures title counts the accepted ones", () => {
  const doc = (path: string, extra: Partial<Attachment> = {}): Attachment => ({
    path, name: path.split("/").pop() ?? path, kind: "document", state: "ok", notes: [], reason: "", current: path, ...extra,
  });
  const attachments = [
    doc("/a.docx"), doc("/t.docx", { kind: "template" }),
    doc("/logo.png", { kind: "reference" }), doc("/broken.png", { kind: "reference", state: "refused" }),
  ];
  assert.deepEqual(sectionAttachments("documents", { attachments }).map((a) => a.path), ["/a.docx"]);
  assert.deepEqual(sectionAttachments("template", { attachments }).map((a) => a.path), ["/t.docx"]);
  assert.deepEqual(sectionAttachments("references", { attachments }).map((a) => a.path), ["/logo.png", "/broken.png"]);
  assert.equal(referencesTitle({ attachments }), "Pictures and tables · 1");
  assert.equal(referencesTitle({ attachments: [] }), "Pictures and tables");
});

test("Undo the last button shows in Safe mode when there is something to undo, with why it waits", () => {
  const working = { generation: 2, from_originals: false, can_start_over: true, undo: "Remove highlighting", undo_blocked: "" };
  const link = undoLink({ mode: "safe", working }, false);
  assert.equal(link?.disabled, false);
  assert.ok(link?.tip.includes("“Remove highlighting”") && link.tip.includes("Nothing is deleted"));
  assert.deepEqual(undoLink({ mode: "safe", working: { ...working, undo_blocked: "Discard the preview first." } }, false), {
    disabled: true,
    tip: "Discard the preview first.",
  });
  assert.equal(undoLink({ mode: "safe", working }, true)?.disabled, true);
  assert.equal(undoLink({ mode: "safe", working: { ...working, undo: "" } }, false), null);
  assert.equal(undoLink({ mode: "default", working }, false), null);
  // an older server sends no undo: no link
  assert.equal(undoLink({ mode: "safe", working: { generation: 1, from_originals: false, can_start_over: true } }, false), null);
});
