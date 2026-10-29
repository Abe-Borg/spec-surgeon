/**
 * The workbench's small rules (lib/workbench.ts): page counts, the run
 * folder, the kind tags.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Attachment, Plan, PlanGroup } from "../src/types.ts";
import {
  ACCEPT,
  changedTip,
  copyTip,
  isSaved,
  kindTag,
  notSavedTip,
  openTarget,
  pageCount,
  parentFolder,
  planHasContent,
  referencesTitle,
  copiesNote,
  EXPORT_CHOICES,
  exportBlocked,
  exportChoiceBlocked,
  exportLabel,
  exportTip,
  runFolder,
  scopeTag,
  sectionAttachments,
  traySummary,
  trayTitle,
  undoLink,
  doneCoverage,
  doneSteps,
  doneSubtitle,
  ranLabel,
  timesRun,
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

test("Undo shows in every mode when there is something to undo, with why it waits", () => {
  const working = { generation: 2, from_originals: false, can_start_over: true, undo: "Remove highlighting", undo_blocked: "" };
  const link = undoLink({ mode: "safe", working }, false);
  assert.equal(link?.disabled, false);
  assert.equal(link?.label, "Undo the last button");
  assert.ok(link?.tip.includes("“Remove highlighting”") && link.tip.includes("Nothing is deleted"));
  assert.deepEqual(undoLink({ mode: "safe", working: { ...working, undo_blocked: "Discard the preview first." } }, false), {
    label: "Undo the last button",
    disabled: true,
    tip: "Discard the preview first.",
  });
  assert.equal(undoLink({ mode: "safe", working }, true)?.disabled, true);
  assert.equal(undoLink({ mode: "safe", working: { ...working, undo: "" } }, false), null);
  for (const mode of ["default", "obliterate"] as const) {
    assert.equal(undoLink({ mode, working }, false)?.label, "Undo the last change");
    assert.equal(undoLink({ mode, working }, false)?.disabled, false);
    assert.equal(undoLink({ mode, working: { ...working, undo_blocked: "Drop the request that is open first, or finish it." } }, false)?.disabled, true);
  }
  // an older server sends no undo: no link
  assert.equal(undoLink({ mode: "safe", working: { generation: 1, from_originals: false, can_start_over: true } }, false), null);
});

test("the Export button says how many copies are not saved yet, and its hover text says what it would do", () => {
  assert.equal(exportLabel({ export: { folder: "", unsaved: 0, copies: 0 } }), "Export");
  assert.equal(exportLabel({ export: { folder: "", unsaved: 2, copies: 3 } }), "Export · 2 unsaved");
  assert.equal(exportLabel({ export: { folder: "", unsaved: 2, copies: 3 } }, true), "Export · 2");
  assert.equal(exportLabel({ export: { folder: "", unsaved: 0, copies: 3 } }, true), "Export");
  assert.equal(exportTip({ export: { folder: "", unsaved: 0, copies: 0 } }, true), "Finish or stop the current work first");
  assert.match(exportTip({ export: { folder: "", unsaved: 0, copies: 0 } }, false), /^Nothing to save yet/);
  const head = "Save the edited copies to a folder you choose: as Word with tracked changes on or off, or as clean PDFs.";
  assert.equal(
    exportTip({ export: { folder: "", unsaved: 1, copies: 1 } }, false),
    `${head} 1 of 1 copy is not saved yet as Word with their tracked changes, and only such a saved copy survives closing the window.`,
  );
  assert.equal(
    exportTip({ export: { folder: "", unsaved: 2, copies: 3 } }, false),
    `${head} 2 of 3 copies are not saved yet as Word with their tracked changes, and only such a saved copy survives closing the window.`,
  );
  assert.equal(exportTip({ export: { folder: "C:/Saved", unsaved: 0, copies: 3 } }, false), `${head} All 3 copies are saved to C:/Saved.`);
  assert.equal(exportTip({ export: { folder: "", unsaved: 0, copies: 1 } }, false), `${head} All 1 copy is saved.`);
  assert.match(exportTip({ export: { folder: "", unsaved: 1, copies: 1 }, tutorial: { active: true } as never }, false), /^Held still during the tour/);
});

test("Export opens its menu whenever the window lists copies and no work runs; the tour holds it still", () => {
  const copies = (n: number) => ({ export: { folder: "", unsaved: n, copies: n } });
  assert.equal(exportBlocked(copies(2), false), "");
  assert.equal(exportBlocked(copies(2), true), "Finish or stop the current work first");
  assert.match(exportBlocked(copies(0), false), /^Nothing to save yet/);
  assert.match(exportBlocked({ ...copies(2), tutorial: { active: true } as never }, false), /^Held still during the tour/);
  // an older server that says nothing about Export: nothing to save
  assert.match(exportBlocked({ export: undefined as never }, false), /^Nothing to save yet/);
});

test("the Export menu offers the owner's three choices, in order, and PDF (Clean) waits for a program that makes PDFs", () => {
  assert.deepEqual(
    EXPORT_CHOICES.map((c) => [c.kind, c.label]),
    [
      ["word", "Word (Tracked Changes ON)"],
      ["accepted", "Word (Tracked Changes OFF)"],
      ["pdf", "PDF (Clean)"],
    ],
  );
  for (const c of EXPORT_CHOICES) assert.ok(c.detail.trim().endsWith("."), c.kind);
  assert.match(EXPORT_CHOICES[1].detail, /Comments stay/);
  const none = { pdf: { available: false, renderer: "", reason: "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found." } };
  assert.equal(exportChoiceBlocked("pdf", none), none.pdf.reason);
  assert.match(exportChoiceBlocked("pdf", { pdf: { available: false, renderer: "", reason: "" } }), /Microsoft Word or LibreOffice/);
  assert.equal(exportChoiceBlocked("word", none), "");
  assert.equal(exportChoiceBlocked("accepted", none), "");
  assert.equal(exportChoiceBlocked("pdf", { pdf: { available: true, renderer: "LibreOffice", reason: "" } }), "");
  // a server that says nothing about PDFs never holds the choice back; it answers at the choice
  assert.equal(exportChoiceBlocked("pdf", {}), "");
});

test("the copies note says where they were saved, or that they are kept only in the scratch folder", () => {
  const copy = (name: string, saved_to = "", changed_since_saved = false, saved_file_changed = false) => ({
    source: `s/${name}`,
    output: `C:/scratch/run/outputs/${name}`,
    name,
    saved_to,
    changed_since_saved,
    saved_file_changed,
  });
  assert.equal(copiesNote({ results: [] }), "");
  assert.match(copiesNote({ results: [copy("a.docx")] }), /^Not saved yet: Export, in the top bar, saves them to a folder you choose, as Word with tracked changes on or off or as clean PDFs\. Until saved as Word with their tracked changes they live only in this window's scratch folder/);
  assert.match(copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx"), copy("b.docx")] }), /^1 of 2 not saved yet: Export/);
  assert.equal(copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx"), copy("b.docx", "C:/Saved/b.docx")] }), "Saved to C:/Saved.");
  // a copy changed since it was saved (opened from the window and edited in Word) is not saved as it is now
  assert.match(copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx", true)] }), /^Not saved yet \(1 changed since it was saved\): Export/);
  assert.match(
    copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx", true), copy("b.docx", "C:/Saved/b.docx", true), copy("c.docx", "C:/Saved/c.docx")] }),
    /^2 of 3 not saved yet \(2 changed since they were saved\): Export/,
  );
  assert.match(copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx", true), copy("b.docx"), copy("c.docx", "C:/Saved/c.docx")] }), /^2 of 3 not saved yet \(1 changed since it was saved\): Export/);
  // a saved file that no longer holds its copy (written over, truncated or edited after Export)
  assert.match(copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx", false, true)] }), /^Not saved yet \(1 whose saved file changed since\): Export/);
  assert.match(
    copiesNote({ results: [copy("a.docx", "C:/Saved/a.docx", true), copy("b.docx", "C:/Saved/b.docx", false, true), copy("c.docx", "C:/Saved/c.docx")] }),
    /^2 of 3 not saved yet \(1 changed since it was saved, 1 whose saved file changed since\): Export/,
  );
});

test("a copy's link opens the saved file while it holds the copy's bytes, else the working copy, and says which", () => {
  const working = "C:/scratch/run/outputs/a.docx";
  const copy = (saved_to = "", changed_since_saved = false, saved_file_changed = false) => ({ output: working, saved_to, changed_since_saved, saved_file_changed });
  assert.equal(isSaved(copy()), false);
  assert.equal(isSaved(copy("C:/Saved/a.docx")), true);
  assert.equal(isSaved(copy("C:/Saved/a.docx", true)), false);
  assert.equal(isSaved(copy("C:/Saved/a.docx", false, true)), false);
  // never saved, or an older server without the flag: the working copy
  assert.equal(openTarget(copy()), working);
  assert.equal(openTarget({ output: working }), working);
  assert.equal(openTarget({ output: working, saved_to: "C:/Saved/a.docx" }), "C:/Saved/a.docx");
  // saved and unchanged: the saved file; changed since: the working copy, the one with the newer content
  assert.equal(openTarget(copy("C:/Saved/a.docx")), "C:/Saved/a.docx");
  assert.equal(openTarget(copy("C:/Saved/a.docx", true)), working);
  // the saved file no longer holds the copy (written over, truncated or edited after Export): the working copy
  assert.equal(openTarget(copy("C:/Saved/a.docx", false, true)), working);
  assert.equal(copyTip(copy()), `Opens the working copy, kept in the window's scratch folder until it closes (Export saves it): ${working}`);
  assert.equal(copyTip(copy("C:/Saved/a.docx")), "Opens the saved file: C:/Saved/a.docx");
  assert.equal(
    copyTip(copy("C:/Saved/a.docx", true)),
    `Opens the working copy, which changed since it was saved to C:/Saved/a.docx (Export saves it again): ${working}`,
  );
  assert.equal(changedTip(copy("C:/Saved/a.docx", true)), "Changed since it was saved to C:/Saved/a.docx. Export saves it again, beside the file saved before.");
  assert.equal(
    copyTip(copy("C:/Saved/a.docx", false, true)),
    `Opens the working copy; the file saved to C:/Saved/a.docx no longer holds it (changed or replaced since Export, which saves this copy again, beside it): ${working}`,
  );
  assert.equal(notSavedTip(copy("C:/Saved/a.docx", false, true)), "The file saved to C:/Saved/a.docx no longer holds this copy (changed or replaced since Export). Export saves it again, beside that file.");
});



test("What you've done so far: each run with how many documents carry it, and why the others do not", () => {
  const all = { request: "Remove all comments", recipe: "remove_comments", documents: 3, of: 3, not: [] };
  assert.equal(doneCoverage(all), "All 3 documents");
  assert.equal(doneCoverage({ ...all, documents: 1, of: 1 }), "1 document");
  assert.equal(
    doneCoverage({
      ...all,
      documents: 1,
      not: [
        { name: "21 31 13.docx", why: "left untouched" },
        { name: "21 13 19.docx", why: "attached later" },
      ],
    }),
    "1 of 3 documents · not 21 31 13.docx (left untouched); 21 13 19.docx (attached later)",
  );
  const many = ["a", "b", "c", "d", "e"].map((n) => ({ name: `${n}.docx`, why: "set aside" }));
  assert.equal(
    doneCoverage({ ...all, documents: 1, of: 6, not: many }),
    "1 of 6 documents · not a.docx, b.docx, c.docx and 2 more (set aside)",
  );
  assert.equal(
    doneCoverage({ ...all, documents: 0, of: 1, not: [{ name: "a.docx", why: "attached later" }] }),
    "None of the documents attached now · not a.docx (attached later)",
  );
});

test("the list's subtitle names Undo only while there is one, in the mode's words", () => {
  const working = { generation: 1, from_originals: false, can_start_over: true, undo: "Remove all comments", undo_blocked: "" };
  assert.equal(
    doneSubtitle({ mode: "safe" }, undoLink({ mode: "safe", working }, false)),
    "Every button you ran since the originals, oldest first. Undo the last button takes the newest off; Start over clears the list.",
  );
  assert.equal(
    doneSubtitle({ mode: "default" }, undoLink({ mode: "default", working }, false)),
    "Every change you made since the originals, oldest first. Undo the last change takes the newest off; Start over clears the list.",
  );
  assert.equal(doneSubtitle({ mode: "obliterate" }, null), "Every change you made since the originals, oldest first. Start over clears the list.");
});

test("a Safe-mode button carries its checkmark while a run of the working set is its own", () => {
  const step = { request: "Remove all comments", recipe: "remove_comments", documents: 2, of: 2, not: [] };
  const working = { generation: 3, from_originals: false, can_start_over: true };
  const view = { working: { ...working, done: [step, { ...step, recipe: "" }, step] } };
  assert.equal(timesRun(view, "remove_comments"), 2);
  assert.equal(timesRun(view, "accept_changes"), 0);
  assert.deepEqual(doneSteps({ working }), []);          // an older server sends no list
  assert.equal(ranLabel(1), "You ran it once on these copies (What you've done so far).");
  assert.equal(ranLabel(2), "You ran it 2 times on these copies (What you've done so far).");
});
