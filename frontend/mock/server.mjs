#!/usr/bin/env node
/**
 * A Node-only mock of the window's API (docs/WEB_API.md): canned data and
 * scripted SSE jobs, so the page can be developed, tested and screenshotted
 * without the Python server.
 *
 *   node mock/server.mjs                       # http://127.0.0.1:8765/?token=dev-token
 *   PORT=9000 MOCK_DELAY=0 node mock/server.mjs
 *
 * The job a request runs is chosen by its text: "question" asks one thing
 * with choices and chips; "fail" ends in an error; "pause" ends in a budget
 * pause; anything else plans and previews. A choice leads to the plan job;
 * the primary action creates copies (or continues after a pause).
 *
 * The guided tour's routes (GET /api/tutorial/status, POST /api/tutorial/start|scenario|restore) swap the view for a
 * canned scenario per chapter and put the conversation back at the end, the shape of the real server's practice
 * workspace; the real one builds each chapter's state on the bundled showcase through the engine.
 *
 * Mock-only routes (never in the real server): POST /mock/scenario {name}
 * sets the view to a canned scenario (empty, attached, question, preview,
 * layouts, done, todo, paused, update-available, update-ready, up-to-date,
 * no-key, safe, safe-done, and Obliterate's below); POST /mock/reset; GET /mock/page-reports (what the page sent to
 * /api/diagnostics). A check for updates finds 1.7.0, except after
 * the up-to-date scenario, where it answers "You're up to date".
 *
 * Obliterate mode's own tools (docs/WEB_API.md: `hands_on`, `word`, `attachments[].rescue` and `salvaged`,
 * `export.anyway`, and their routes) answer from canned data, with no Word and no file system behind them:
 *  - the hands-on tools read a small package made up for each document (its main part runs to two pages). Save this
 *    part and Export answer as if the native folder dialog had returned a folder. Replace this part previews the file
 *    it is given: its words are compared with the canned part's, a file that is not well-formed XML fails V1, and
 *    Create keeps the file, so the browser then shows it in the edited copy;
 *  - a Word action finds in each canned document what WORD_FINDS says; a document with nothing to do gets no copy;
 *  - Try to rescue takes any password that does not start with "wrong";
 *  - a clean export (Word, Tracked Changes OFF; PDF, Clean) leaves a copy delivered UNVERIFIED without a file, which
 *    is what Export anyway is then offered for.
 * Their scenarios: obliterate-offered (Default mode, Obliterate shown in the top bar), obliterate (the mode on),
 * obliterate-rescue (refused documents with something to try), obliterate-no-word (Word cannot be reached) and
 * obliterate-done (three copies, one UNVERIFIED).
 *
 * No dependencies: http, fs, path, zlib. Preview images are PNGs drawn here.
 */
import http from "node:http";
import { readFileSync, existsSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import zlib from "node:zlib";

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = resolve(HERE, "../../src/spec_surgeon/web/static");
const PORT = Number(process.env.PORT || 8765);
const HOST = process.env.HOST || "127.0.0.1";
const TOKEN = process.env.MOCK_TOKEN || "dev-token";
const DELAY = process.env.MOCK_DELAY === undefined ? 120 : Number(process.env.MOCK_DELAY);
const VERSION = "1.6.0";

// --- Canned data ----------------------------------------------------------

const DOCS = [
  { path: "C:/Specs/Riverside/21 13 13 Wet-Pipe Sprinkler Systems.docx", notes: [] },
  {
    path: "C:/Specs/Riverside/21 05 00 Common Work Results for Fire Suppression.docx",
    notes: ["Numbers are typed by hand, not automatic list numbering."],
  },
  {
    path: "C:/Specs/Riverside/21 31 13 Electric-Drive, Centrifugal Fire Pumps.docx",
    notes: ["Editing is restricted in Word; edited copies keep the restriction.", "The file has 3 comments."],
  },
];
const REFUSED = {
  path: "C:/Specs/Riverside/21 00 00 Table of contents.docx",
  reason: "21 00 00 Table of contents.docx cannot be opened as a Word package; open it in Word and save it again as .docx.",
};
// Two more documents triage refuses, each with something Obliterate's Try to rescue can try (triage.py's sentences)
const ENCRYPTED = {
  path: "C:/Specs/Riverside/21 22 00 Clean-Agent Fire-Extinguishing Systems.docx",
  reason: "21 22 00 Clean-Agent Fire-Extinguishing Systems.docx is password-protected; remove the password in Word and save it again as .docx.",
};
const DOCTYPED = {
  path: "C:/Specs/Riverside/21 12 00 Fire-Suppression Standpipes.docx",
  reason: "21 12 00 Fire-Suppression Standpipes.docx: word/document.xml carries an XML document type declaration (<!DOCTYPE …>), which Word never writes, so its text cannot be read exactly; open the file in Word and save it again as .docx, or get it again from where it came from.",
};
const TEMPLATE = { path: "C:/Specs/Templates/Office master 2026.docx" };
const PICTURE = { path: "C:/Specs/Riverside/logo-2026.png", notes: ["A PNG picture, 600 × 200 pixels, for the edit to use."] };

const RUN_DIR = "C:/Users/abe/AppData/Local/Spec Surgeon/runs/20261020-143512/outputs";
/** Safe mode's buttons, as the real server lists them: generated from ``recipes.catalog()`` and pinned to it by
 *  tests/test_recipes.py, so the mock never offers a button the app does not have. */
const RECIPES = JSON.parse(readFileSync(resolve(HERE, "recipes.json"), "utf8"));
const TEMPLATE_INFO = {
  kind: "prepared",
  typed_slots: ["ISSUED FOR PERMIT"],
  slot_roles: { "XX XX XX": "number", SECTION_NUMBER: "number", SECTION_TITLE: "title" },
  reason: "",
  // what "Match body formatting to template" reads from it (template_roles.detect)
  body: {
    roles: { section_id: "SCT", section_title: "TTL", part: "PRT", article: "ART", paragraph: "PR1", subparagraph: "PR2",
             end_of_section: "EOS" },
    styles: [["ART", "ART"], ["EOS", "End of Section"], ["Normal", "Normal"], ["PR1", "PR1"], ["PR2", "PR2"],
             ["PRT", "PRT"], ["SCT", "Section Number"], ["TTL", "Section Title"]].map(([id, name]) => ({ id, name })),
    labels: { section_id: "Section number line", section_title: "Section title", part: "PART", article: "Article (1.1)",
              paragraph: "Paragraph (A.)", subparagraph: "Subparagraph (1.)", subparagraph_2: "Subparagraph (a.)",
              end_of_section: "END OF SECTION" },
    order: ["section_id", "section_title", "part", "article", "paragraph", "subparagraph", "subparagraph_2",
            "end_of_section"],
    page: "8.5 × 11 in portrait, margins 1 in",
    font: "Arial 10 pt",
    problems: [],
  },
};
const NO_TEMPLATE = { kind: "none", typed_slots: [], reason: "" };

function name(path) {
  return path.split(/[\\/]/).pop();
}

function attachment(path, kind, extra = {}) {
  return {
    path,
    name: name(path),
    kind,
    state: extra.reason ? "refused" : extra.notes && extra.notes.length ? "note" : "ok",
    notes: extra.notes ?? [],
    reason: extra.reason ?? "",
    current: path,
  };
}

const PLAN = {
  groups: [
    {
      id: "g1",
      label: "",
      count: 3,
      files: DOCS.map((d) => d.path),
      changes: [
        {
          title: "Change fonts",
          summary: "Set the font of every run in the body, the headers and the footers to Arial 10 pt, keeping bold and italic.",
          details: [
            "Runs matched: every run in the body, header and footer parts (3 documents).",
            "Font: Arial, East Asian and complex-script fonts included.",
            "Size: 10 pt (20 half-points); the size of heading styles is not changed.",
            "Theme font references are replaced by the explicit font name.",
          ],
        },
        {
          title: "Replace the footer text",
          summary: "In every footer, replace the text “Riverside Medical Center — Issued for Bid” with “Riverside Medical Center — Issued for Construction”.",
          details: [
            "Paragraphs matched: paragraphs in the footer parts whose text contains the exact phrase (1 per document).",
            "Replacement: whole-phrase, case-sensitive, formatting of the first run kept.",
            "Expected: exactly 1 replacement per footer, 3 footers in all.",
          ],
        },
        {
          title: "Put the logo in the header",
          summary: "Replace the picture in the first-page header with logo-2026.png at the same size (2.0 × 0.67 inches).",
          details: [
            "Pictures matched: the first picture in the first-page header part.",
            "New picture: logo-2026.png (PNG, 600 × 200 pixels), scaled to 2.0 × 0.67 inches.",
            "Media member replaced in place; relationships unchanged.",
          ],
        },
      ],
      assumptions: [
        "Headings keep their style's size; only the font family changes there.",
        "“Issued for Bid” appears once per footer, as the triage of each file shows.",
      ],
    },
  ],
  targets: [
    {
      title: "21 13 13 Wet-Pipe Sprinkler Systems.docx",
      source: DOCS[0].path,
      details: ["Footer: “Riverside Medical Center — Issued for Bid” (page footer, all sections).", "First-page header: one picture, 2.0 × 0.67 in."],
    },
    {
      title: "21 05 00 Common Work Results for Fire Suppression.docx",
      source: DOCS[1].path,
      details: ["Footer: “Riverside Medical Center — Issued for Bid” (page footer, section 1 of 2)."],
    },
  ],
  set_aside: [],
  stages: 1,
};

const PLAN_TWO_LAYOUTS = {
  groups: [
    { ...PLAN.groups[0], id: "g1", label: "Styled, PR1 articles", count: 2, files: DOCS.slice(0, 2).map((d) => d.path) },
    {
      id: "g2",
      label: "Normal paragraphs, typed numbers",
      count: 1,
      files: [DOCS[2].path],
      changes: [
        {
          title: "Change fonts",
          summary: "Set the font of every run in the body, the headers and the footers to Arial 10 pt, keeping bold and italic.",
          details: ["Runs matched: every run in the body, header and footer parts (1 document)."],
        },
        {
          title: "Replace the footer text",
          summary: "In every footer, replace the text “Issued for Bid” with “Issued for Construction”.",
          details: ["Paragraphs matched: paragraphs in Normal style whose text contains the exact phrase (1)."],
        },
      ],
      assumptions: ["Article titles are Normal paragraphs with a typed number; they are left as they are."],
    },
  ],
  targets: PLAN.targets,
  set_aside: [
    {
      name: "21 00 00 Table of contents.docx",
      reason: "No layout covers this file: its body has no paragraphs. Attach it on its own, or ask for a plan that covers it.",
    },
  ],
  stages: 2,
};

function samples() {
  return DOCS.slice(0, 2).map((d, i) => ({
    source: d.path,
    name: name(d.path),
    images: [`/api/image?path=${encodeURIComponent(`${RUN_DIR}/preview/${i + 1}.png`)}`],
    pages_before: 12 + i * 4,
    pages_after: 12 + i * 4 + (i === 1 ? 1 : 0),
    passed: true,
    failures: [],
    // the page-layout verdict (check V7), as docs/WEB_API.md describes it
    geometry:
      i === 0
        ? { status: "PASS", note: "Page layout checked on the rendered pages: the words sit where they did." }
        : {
            status: "NA",
            at: "run",
            note: "Page layout of a plan done in steps is checked step by step when the copies are created; the preview renders the last step's pages only.",
          },
  }));
}

/**
 * Create, counted as the server counts it (docs/WEB_API.md, `action.copies` and `action.unpreviewed`): the documents
 * it runs — those in scope, less the plan's Left untouched and the samples set aside — and how many show no preview.
 */
function approveAction(v, token) {
  const out = (a) =>
    (v.plan?.set_aside ?? []).some((i) => i.path === a.path || i.name === a.name) ||
    v.samples.some((sv) => (sv.set_aside || sv.no_copy) && (sv.source === a.path || sv.source === a.current));
  const runs = v.attachments.filter(
    (a) =>
      a.kind === "document" &&
      a.state !== "refused" &&
      (v.focus.length ? v.focus.includes(a.path) : !v.todo.some((t) => t.path === a.path)) &&
      !out(a),
  );
  const shown = (a) => v.samples.some((sv) => sv.source === a.path || sv.source === a.current);
  return {
    label: "Create edited copies",
    token,
    kind: "approve",
    can_hold: true,
    copies: runs.length,
    unpreviewed: runs.filter((a) => !shown(a)).length,
  };
}

/** A sample that did not pass its checks (V2 and V7), as the server shows it. */
function failSample(sample) {
  sample.passed = false;
  sample.failures = [
    { check: "V2", reason: "The copy's wording does not match the change predicted by the plan." },
    { check: "V7", reason: "The page layout does not match the layout predicted by the plan." },
  ];
  sample.geometry = {
    status: "FAIL",
    note: "Page layout differs from what the plan predicts: reference = source render; max |Δy| 11.60pt on asserted pages (tol 0.5pt); MOVED pages [3, 4]",
  };
}

/** The find-and-replace list of a waiting plan (docs/WEB_API.md `occurrences`): every occurrence per document. */
function occurrences(skipped = []) {
  const item = (source, paragraph, before, match, after, heading = "", part = "word/document.xml") => ({
    key: `${source}\t${part}\t${paragraph}\t${before.length}\t${before.length + match.length}\t${match}`,
    part, paragraph, start: before.length, end: before.length + match.length,
    where: part === "word/document.xml" ? "the body" : "a footer", heading, before, match, after,
    find: "FM Global", replace: "FM Approvals", skipped: false, op_index: 0,
  });
  const docs = DOCS.slice(0, 3).map((d, i) => ({
    source: d.path, name: name(d.path), status: "planned", reason: "",
    items: [
      item(d.path, 12, "A. The Owner's insurer is", "FM Global", "and all equipment shall be FM Approved.", "1.03"),
      item(d.path, 13, "B. The auditor is", "FM Global", "and reviews the hydraulic calculations.", "1.03"),
      item(d.path, 16 + i, "E. See Section 21 10 00 for", "FM Global", "coordination.", "1.05"),
    ],
  }));
  for (const doc of docs) for (const it of doc.items) it.skipped = skipped.includes(it.key);
  return docs;
}

// document_task.OBLITERATE_ON / OBLITERATE_OFF
const OBLITERATE_ON = "Obliterate mode is on. Claude may write wording and custom methods that change anything Word stores, and a copy whose checks fail is delivered marked UNVERIFIED instead of being held back. A custom method predicts nothing: you approve the copies it made, and each is delivered marked UNPROVEN. Claude may also plan a raw XML edit: the app proves the edit itself, and lists the words it changes before you approve. Word itself can run named actions on a copy, from the Word actions menu beside each document or in a plan of Claude's: nothing is predicted for them, so you approve the copies Word made. Copies only; your originals are never written. What was planned before is void.";
const OBLITERATE_OFF = "Obliterate mode is off: every placed word is yours again, custom methods are formatting only, raw XML edits and Word actions are refused, and a copy whose checks fail is held back. What was planned in Obliterate mode is void.";

// --- Obliterate's own tools: the app's words (document_task.py, word_actions.py, rescue.py, exports.py, preview.py) ---

const HANDS_ON_ON = "Hands-on tools are on. Parts, beside each document, shows the parts of its file read-only, with a search; Save this part writes one out as it is, to a folder you choose; Replace this part puts a file of yours in a part's place, byte for byte, after a preview that lists the words it changes. None of them uses AI, and nothing in the window edits XML.";
const HANDS_ON_OFF = "Hands-on tools are off.";
const HANDS_ON_NEEDED = "The hands-on tools are off. Obliterate mode's warning turns them on, or its strip above the message box.";
const HANDS_ON_OBLITERATE_ONLY = "The hands-on tools are Obliterate mode's: turn Obliterate on first.";
const PART_ONLY_THERE = (doc) => `The part was replaced in ${doc} only; this document was not asked about.`;
const PART_NO_WORDS_CHANGE = "The replaced part changes no words: the independent checker read the same text in the copy as in the original — the words of every paragraph and any other text between tags.";
const PART_WORDS_CHANGE_HEAD = "Before you approve — the words the replaced part changes, as the independent checker read them in the copy:";
const PART_OTHER_CHANGES_HEAD = "Before you approve — what else the replaced part changes beside the words, as the independent reader found it in the copy:";
const RAW_UNREAD = (doc) => `The words this changes could not be listed for ${doc}: the copy could not be read part by part. Its preview says which check failed.`;
const WHY_PART = "It replaced a part of one document with a file you chose that time (a hands-on tool), so it cannot be repeated on others.";
const MAX_PART_BYTES = 64 * 1024 * 1024;   // ops/replace_part.MAX_PART_BYTES

const WORD_LABEL = "Word actions";
const WORD_OBLITERATE_ONLY = "The Word actions are Obliterate mode's: turn Obliterate on first.";
const WORD_ONLY_THERE = (doc) => `Word ran its actions on ${doc} only; this document was not asked about.`;
const NO_WORD = "Microsoft Word is not installed on this computer (or the app cannot reach it), and the Word actions are Word's own commands: they run nowhere else.";
const NOTHING_TO_DO = "Word found nothing to do in it";
const WORD_MINUTES = 5;                    // word_actions.TIMEOUT_S, in minutes
const WORD_UNPROVEN_MARK = "UNPROVEN (Word made it: what it changed was shown to you, not proven)";
const WORD_WORDS_CHANGE_HEAD = "Before you approve — the words Word's actions change, as the independent checker read them in each copy:";
const WORD_OTHER_CHANGES_HEAD = "Before you approve — what else Word's actions change beside the words, as the independent reader found it in each copy:";
const WORD_ACCOUNT_HEAD = "What Word itself says it did (Word's own count, not the independent reader's; the lists above are what the reader found):";
const NO_WORDS_CHANGE = "No words change: the independent checker read the same words in the sample copies as in the originals, in the body, headers, footers, notes and comments.";
const NO_COPY_HEAD = "No copy could be made of these, so none is created; each goes on your to-do list when the others are created:";
const WHY_WORD = "Word made its copies, and you approved each copy as it was shown, not a plan the engine checks. A recipe keeps only plans the engine runs and checks as they are written; run the Word actions again when you need them.";
/** The menu (word_actions.MENU, the actions it offers): what each does, and Word's own account for one and for several. */
const WORD_MENU = [
  { id: "repair", label: "Open and repair, then save", does: "Word opens the copy with its Open and Repair command and saves it again.", one: "opened with Open and Repair and saved again", many: "opened with Open and Repair and saved again" },
  { id: "remove_protection", label: "Remove protection", does: "Word turns off the copy's editing restrictions, its read-only recommendation and its Marked as Final state. A restriction set with a password is not removed: Word needs the password.", one: "{n} protection setting turned off", many: "{n} protection settings turned off" },
  { id: "accept_changes", label: "Accept all tracked changes", does: "Word accepts every tracked change in the copy.", one: "{n} tracked change accepted", many: "{n} tracked changes accepted" },
  { id: "reject_changes", label: "Reject all tracked changes", does: "Word rejects every tracked change in the copy.", one: "{n} tracked change rejected", many: "{n} tracked changes rejected" },
  { id: "update_fields", label: "Update fields", does: "Word updates every field in the copy: tables of contents, cross-references, page numbers, dates. A field that reads another file or asks a question is left as it is.", one: "{n} field updated", many: "{n} fields updated" },
  { id: "unlink_fields", label: "Unlink fields", does: "Word replaces every field in the body, the notes and the body's text boxes with the text it shows. Fields in headers and footers stay live: their page numbers and running titles.", one: "{n} field replaced with its text", many: "{n} fields replaced with their text" },
  { id: "numbers_to_text", label: "Turn automatic numbers into typed text", does: "Word turns the copy's automatic list numbers into typed text.", one: "{n} numbered paragraph given a typed number", many: "{n} numbered paragraphs given typed numbers" },
  { id: "upgrade_compatibility", label: "Leave compatibility mode", does: "Word converts the copy to its current file format (its Convert command), leaving compatibility mode.", one: "converted out of compatibility mode", many: "converted out of compatibility mode" },
  { id: "textboxes_to_text", label: "Turn text boxes into plain text", does: "Word puts the text of each text box into the document as ordinary paragraphs, where the box is anchored, and deletes the box: in the body and in headers and footers.", one: "{n} text box turned into plain text", many: "{n} text boxes turned into plain text" },
  { id: "delete_shapes", label: "Delete drawn shapes", does: "Word deletes every drawn shape, in the body and in headers and footers: text boxes with their text, lines, AutoShapes, WordArt, SmartArt, groups, canvases and watermarks. Pictures, charts and embedded objects stay.", one: "{n} drawn shape deleted", many: "{n} drawn shapes deleted" },
];
/** What Word finds to do, by action, in each of the three canned documents (any other document: as the first). The
 *  second has no tracked change and types its numbers by hand; only the third has its editing restricted. Open and
 *  Repair always saves the file again, whatever it counts. */
const WORD_FINDS = {
  remove_protection: [0, 0, 1],
  accept_changes: [2, 0, 4],
  reject_changes: [2, 0, 4],
  update_fields: [3, 3, 5],
  unlink_fields: [1, 1, 2],
  numbers_to_text: [38, 0, 41],
  upgrade_compatibility: [0, 1, 0],
  textboxes_to_text: [0, 1, 0],
  delete_shapes: [0, 1, 1],
};
/** What the independent reader lists for a copy an action changed (`samples[].text_changes` and `changes`), canned. */
const WORD_SHOWS = {
  repair: () => ({ words: [], other: ["Word wrote every part of the file again; the words, tables and pictures read the same."] }),
  remove_protection: () => ({ words: [], other: ["Settings: the editing restriction (document protection) is no longer in the settings part."] }),
  accept_changes: (n) => ({
    words: [{ part: "word/document.xml", where: "body paragraph 14", paragraph: 13, removed: ["Issued for Bid"], added: ["Issued for Construction"], settled: true }],
    other: [`Tracked changes: ${n} in the original, none in the copy.`],
  }),
  reject_changes: (n) => ({
    words: [{ part: "word/document.xml", where: "body paragraph 14", paragraph: 13, removed: ["Issued for Construction"], added: ["Issued for Bid"], settled: true }],
    other: [`Tracked changes: ${n} in the original, none in the copy.`],
  }),
  update_fields: () => ({ words: [{ part: "word/document.xml", where: "body paragraph 3", paragraph: 2, removed: ["12"], added: ["13"] }], other: [] }),
  unlink_fields: (n) => ({ words: [], other: [`Fields: ${n} field${n === 1 ? "" : "s"} in the body ${n === 1 ? "is" : "are"} now the text ${n === 1 ? "it" : "they"} showed.`] }),
  numbers_to_text: (n) => ({
    words: [
      { part: "word/document.xml", where: "body paragraph 4", paragraph: 3, removed: [], added: ["1.1"] },
      { part: "word/document.xml", where: "body paragraph 5", paragraph: 4, removed: [], added: ["A."] },
    ],
    other: [`Numbering: ${n} paragraphs no longer carry automatic numbering.`],
  }),
  upgrade_compatibility: () => ({ words: [], other: ["Settings: the compatibility settings changed; the file is in Word's current format."] }),
  textboxes_to_text: (n) => ({
    words: [{ part: "word/document.xml", where: "body paragraph 9", paragraph: 8, removed: [], added: ["NOTE TO SPECIFIER"], block: "new" }],
    other: [`Drawings: ${n} text box${n === 1 ? " is" : "es are"} no longer there.`],
  }),
  delete_shapes: (n) => ({
    words: [{ part: "word/header1.xml", where: "header", paragraph: 0, removed: ["DRAFT"], added: [] }],
    other: [`Drawings: ${n} drawn shape${n === 1 ? " is" : "s are"} no longer there.`],
  }),
};

const RESCUE_LABEL = "Try to rescue";
const RESCUE_OBLITERATE_ONLY = "Try to rescue is Obliterate mode's: turn Obliterate on first.";
const RESCUE_WORD_KINDS = ["format", "password", "unopenable"];   // Word's; `doctype` is the app's own mending
const RESCUE_UNPROVEN = "Nothing proves the rescue kept everything your file held: the file could not be read as it was, so there is nothing to compare the result with. Open both in Word and compare them before you rely on it.";
const NO_PASSWORD_NOW = "The rescued document has NO password: it sits in this window's scratch folder until the window closes, and every copy made from it is unprotected too. Keep them as carefully as the original.";
const NO_MACROS = "A .docx holds no macros: the ones in your file are not in the rescued document.";
const RESCUE_STOPPED = "Stopped. Nothing was rescued: the file is as it was, still refused, and nothing else in this conversation changed.";
const APP_FOLDERS = "C:/Users/abe/AppData/Local/Spec Surgeon/";   // the app's own: nothing the user asks for is saved here
const RESCUED_ROOT = `${APP_FOLDERS}scratch/w-1/rescued`;
const SALVAGED_LINE = (from) => `SALVAGED: the app rescued it from ${from}, a file it could not read as it was, so nothing proves the rescued document holds everything that file did; its copies are checked against the rescued document.`;

const ANYWAY_OBLITERATE_ONLY = "Export anyway is Obliterate mode's: turn Obliterate on first.";
const ANYWAY_KINDS = "Export anyway makes Word (Tracked Changes OFF) or PDF (Clean).";
const ANYWAY_NOTHING = "There is nothing to export anyway. It is offered for a marked copy (UNVERIFIED, UNPROVEN or SALVAGED) that this kind of export left without a file: run that export first.";
const ANYWAY_LABELS = { accepted: "Word (Tracked Changes OFF), made by Word", pdf: "PDF (Clean), made by Word" };
const ANYWAY_DOES = {
  accepted: "Word accepts every tracked change on a private copy and saves it; comments stay.",
  pdf: "Word deletes every comment and accepts every tracked change on a private copy, and the PDF is made from that.",
};
const EXPORT_STOPPED = "Stopped. Nothing was saved: the files are written only once every one is made and checked. Your edited copies are as they were.";
const PREVIEW_STOPPED = "Stopped. The preview was not finished, so it is withdrawn: nothing was created, and your documents and copies are as they were before you pressed the button. Press a button to preview again.";

function results() {
  return DOCS.map((d) => ({ source: d.path, output: `${RUN_DIR}/${name(d.path)}`, name: name(d.path), saved_to: "" }));
}

const WARNINGS = [
  "21 05 00 Common Work Results, page 7: the last line of article 1.4 moved to the next page after the font change; nothing is cut off.",
  "21 05 00 Common Work Results, page 16: the new page carries only the end-of-section line.",
];
/** The notes under Worth knowing (docs/WEB_API.md `worth_knowing`): the footer's old words, which the plan's footer
 *  replacement leaves in a body, as the server words it (ops/elsewhere.py, C3). They follow the rule of `samples`. */
const WORTH_KNOWING = [
  {
    name: name(DOCS[0].path),
    text: "“Riverside Medical Center — Issued for Bid” also occurs in the body (1×); this change leaves it as it is.",
  },
];

let seq = 0;
const now = () => Date.now() / 1000;
const msg = (role, text) => ({ id: `m${++seq}`, role, text, at: now() });

function emptyView() {
  return {
    version: VERSION,
    busy: false,
    stage: "empty",
    connection: { ready: true, origin: "saved" },
    cost: { usd: 0, text: "less than $0.01", calls: 0 },
    attachments: [],
    messages: [],
    plan: null,
    questions: [],
    choices: [],
    chips: [],
    samples: [],
    warnings: [],
    worth_knowing: [],
    blocking: false,
    results: [],
    pdf: { available: true, renderer: "Microsoft Word", reason: "" },
    export: { folder: "", unsaved: 0, copies: 0 },
    action: { label: "", token: "", kind: "none", can_hold: false },
    todo: [],
    focus: [],
    working: { generation: 0, from_originals: true, can_start_over: false },
    budget: { paused: false, reason: "" },
    discard: "",
    update: { state: "none", version: "", notes: "", progress: 0, error: "" },
    mode: "default",
    modes: ["safe", "default"],
    hands_on: false, // Obliterate's hands-on tools; every answer settles it and `word` for the mode (obliterateFields)
    word: null,
    template: NO_TEMPLATE,
    left_untouched: [],
    tutorial: { active: false, id: "", scenario: "", finished_version: 0 }, // GET /api/state refreshes it from settings
    safe_note_hidden: false, // GET /api/state refreshes it from settings
  };
}

/** The guided tour (web/server.py's practice workspace): the conversation set aside, a scenario per chapter. */
let tutorial = null; // { id, requestId, original: view, scenario }
const TUTORIAL_SCENARIOS = {
  start: () => SCENARIOS.safe(),
  button: () => {
    const v = SCENARIOS.safe();
    v.stage = "preview";
    v.messages = [msg("user", "Headers and footers from the template · ISSUED FOR PERMIT → ISSUED FOR CONSTRUCTION 2026-03-01")];
    v.samples = samples();
    // the plan refused the other firm's document, as the server's chapter does (no section number line to fill)
    v.plan = { groups: [], targets: [], set_aside: [{ path: DOCS[2].path, name: name(DOCS[2].path), reason: "No paragraph of this document starts with SECTION and its section number (like “SECTION 21 13 13”), so its own section number cannot be read for the headers and footers. Its first line reads “PROJECT MANUAL”." }], stages: 1 };
    v.action = approveAction(v, "tok-tour-button");
    return v;
  },
  copies: () => SCENARIOS["safe-done"](),
  find: () => {
    const v = SCENARIOS.safe();
    v.stage = "preview";
    v.messages = [msg("user", "Find and replace: “FM Global” → “FM Approvals”"), msg("app", "1 occurrence will be left as it is; the preview is being made again.")];
    v.samples = samples();
    v.occurrences = occurrences([occurrences()[1].items[0].key]);
    v.plan = { groups: [], targets: [], set_aside: [], stages: 1 };
    v.action = approveAction(v, "tok-tour-find");
    return v;
  },
  conversation: () => SCENARIOS.attached(),
  question: () => SCENARIOS.question(),
  plan: () => {
    const v = SCENARIOS.preview();
    v.plan = { ...PLAN, set_aside: [{ path: DOCS[2].path, name: name(DOCS[2].path), reason: "You left it out of this request (“Leave out 21 05 00 for now”)." }] };
    v.action = approveAction(v, v.action.token);
    return v;
  },
  todo: () => SCENARIOS.todo(),
};
function tutorialBlock() {
  return { active: !!tutorial, id: tutorial?.id ?? "", scenario: tutorial?.scenario ?? "", finished_version: settings.tutorial_version };
}
function tutorialView(kind) {
  const v = TUTORIAL_SCENARIOS[kind]();
  v.connection = tutorial.original.connection;
  v.update = tutorial.original.update;
  v.cost = { usd: 0, text: "less than $0.01", calls: 0 };
  v.tutorial = tutorialBlock();
  return v;
}

/** The reply that begins the pass over the to-do list; the real server recognises these exact words. */
const WORK_TODO = "Work on my to-do list";
const HELD_REASON = "You marked its preview as needing more work.";

/** Where each document stands (docs/WEB_API.md, `standing`): a grid from the operations asked for and, per document,
 *  its badges and one attempt per operation (`ran`, `failed`, `skipped` below). */
const ran = (mode, extra = {}) => ({ state: "done", mode, why: "", reason: "", marks: [], note: "", first: "", ...extra });
const failed = (mode, why, reason) => ({ state: "failed", mode, why, reason, marks: [], note: "", first: "" });
const skipped = (why, reason, mode = "") => ({ state: "not_run", mode, why, reason, marks: [], note: "", first: "" });
function standingView(operations, rows) {
  const documents = rows.map(([path, badges, ...cells]) => ({
    path, name: name(path), badges,
    cells: cells.map((c) => ({ ...(Array.isArray(c) ? c[c.length - 1] : c), attempts: Array.isArray(c) ? c : c.state === "not_run" && !c.mode ? [] : [c] })),
  }));
  for (const d of documents) d.owed = d.cells.filter((c) => c.state === "failed").length;
  return {
    operations: operations.map((o, i) => ({
      id: `op-${i + 1}`, number: i + 1, recipe: "", name: "", ...o,
      done: documents.filter((d) => d.cells[i].state === "done").length,
      failed: documents.filter((d) => d.cells[i].state === "failed").length,
    })),
    documents,
    owed: { cells: documents.reduce((n, d) => n + d.owed, 0), documents: documents.filter((d) => d.owed > 0).length },
  };
}

const SCENARIOS = {
  empty: () => emptyView(),
  attached: () => {
    const v = emptyView();
    v.attachments = [
      ...DOCS.map((d) => attachment(d.path, "document", d)),
      attachment(REFUSED.path, "document", REFUSED),
      attachment(TEMPLATE.path, "template"),
      attachment(PICTURE.path, "reference", PICTURE),
    ];
    v.template = TEMPLATE_INFO;
    return v;
  },
  /** Safe mode with the documents, the template and a picture attached: the buttons, no conversation. */
  safe: () => {
    const v = SCENARIOS.attached();
    v.mode = "safe";
    v.connection = { ready: false, origin: "none" };
    return v;
  },
  /** A Safe-mode button ran: two copies, one document left untouched and offered to Default mode. */
  "safe-done": () => {
    const v = SCENARIOS.safe();
    v.stage = "done";
    v.messages = [msg("user", "Remove highlighting"), msg("app", "2 document copies are ready. Open them, press another button to change them further, or start over from the originals. Left untouched: 21 31 13 Electric-Drive, Centrifugal Fire Pumps.docx — each one's reason is beside the copies.")];
    v.results = results().slice(0, 2);
    v.export = { folder: "", unsaved: v.results.length, copies: v.results.length };
    v.working = {
      generation: 1, from_originals: false, can_start_over: true,
      done: [{ id: "run-s1", request: "Remove highlighting", recipe: "remove_highlight", documents: 2, of: 3, not: [{ name: name(DOCS[2].path), why: "left untouched" }], name: "", steps: [buttonStep("remove_highlight", {}, "Remove highlighting")], buttons: ["remove_highlight"], why_not: "" }],
    };
    v.left_untouched = [{ path: DOCS[2].path, name: name(DOCS[2].path), reason: "Editing is restricted in this file (Review → Restrict Editing); remove the restriction in Word, then run again." }];
    v.standing_fixed = true;
    v.standing = standingView(
      [{ request: "Remove highlighting", mode: "safe", recipe: "remove_highlight" }],
      [
        [DOCS[0].path, [], ran("safe")],
        [DOCS[1].path, [], ran("safe")],
        [DOCS[2].path, [], failed("safe", "refused", v.left_untouched[0].reason)],
      ],
    );
    return v;
  },
  /** Where each document stands after three buttons, a pass in Default mode and one document taken to Obliterate:
   *  what worked on what, in which mode, and what is still owed. */
  standing: () => {
    const v = SCENARIOS["safe-done"]();
    const restricted = v.left_untouched[0].reason;
    const noSection = "No paragraph of this document starts with SECTION and its section number (like “SECTION 21 13 13”), so the template's section number cannot be read from it.";
    v.left_untouched = [];
    v.results = results();
    v.export = { folder: "", unsaved: v.results.length, copies: v.results.length };
    v.standing = standingView(
      [
        { request: "Remove all headers and footers", mode: "safe", recipe: "remove_headers_footers" },
        { request: "Remove watermarks", mode: "safe", recipe: "remove_watermarks" },
        { request: "Match headers and footers to the template", mode: "safe", recipe: "headers_footers_template" },
      ],
      [
        [DOCS[0].path, [], ran("safe"), ran("safe"), ran("safe")],
        [DOCS[1].path, ["UNPROVEN"], ran("safe"), ran("safe"), [failed("safe", "refused", noSection), failed("default", "failed", "Its copy could not be made: the plan could not be applied to it."), ran("obliterate", { marks: ["UNPROVEN (a custom method made it: what it changed was shown to you, not proven)"] })]],
        [DOCS[2].path, [], [failed("safe", "refused", restricted), ran("default")], ran("safe"), failed("safe", "refused", noSection)],
      ],
    );
    for (const d of v.standing.documents) for (const c of d.cells) if (c.state === "failed") c.obliterate = "";
    return v;
  },
  question: () => {
    const v = SCENARIOS.attached();
    v.stage = "question";
    v.cost = { usd: 0.07, text: "about $0.07", calls: 2, context: { tokens: 48_210, window: 1_000_000, rollover: 900_000, live: true } };
    v.messages = [
      msg("user", "Change every font to Arial 10 pt and update the footer to say Issued for Construction."),
      msg(
        "assistant",
        "I read the three documents and the template. All three use Arial already in the body, but the headers and footers carry Times New Roman at 9 pt, and the footer line reads “Riverside Medical Center — Issued for Bid”.\n\nBefore I plan this, one thing to clarify.",
      ),
    ];
    v.questions = ["Should the font change cover the headers, footers and the section title too, or only the body text?"];
    v.choices = [
      { id: "c1", label: "Everything — body, headers, footers and the section title", words: [], detail: "" },
      { id: "c2", label: "Only the body text", words: [], detail: "Headers and footers keep Times New Roman 9 pt." },
      {
        id: "c3",
        label: "Everything, and reword the footer",
        words: ["Riverside Medical Center — Issued for Construction, 20 October 2026"],
        detail: "",
        editable: true,
      },
    ];
    v.chips = ["Everything, including the headers", "Just the body", "I don’t know — use your default"];
    return v;
  },
  preview: () => {
    const v = SCENARIOS.attached();
    v.stage = "preview";
    v.cost = { usd: 0.21, text: "about $0.21", calls: 5, context: { tokens: 142_304, window: 1_000_000, rollover: 900_000, live: true } };
    v.messages = [
      msg("user", "Change every font to Arial 10 pt and update the footer to say Issued for Construction."),
      msg("assistant", "I read the three documents and the template. Before I plan this, one thing to clarify."),
      msg("user", "Everything — body, headers, footers and the section title"),
      msg(
        "assistant",
        "Here is the plan. Three changes, the same on every document: the fonts, the footer line, and the new logo from the picture you attached. Two documents are rendered as samples; the second grows by one page because its last article now wraps.\n\nIf the pages look right, create the edited copies.",
      ),
    ];
    v.plan = PLAN;
    v.samples = samples();
    v.warnings = WARNINGS;
    v.worth_knowing = WORTH_KNOWING;
    v.action = approveAction(v, "tok-approve-1");
    return v;
  },
  layouts: () => {
    const v = SCENARIOS.preview();
    v.plan = PLAN_TWO_LAYOUTS;
    return v;
  },
  done: () => {
    const v = SCENARIOS.preview();
    v.stage = "done";
    v.worth_knowing = [];
    v.cost = { usd: 0.34, text: "about $0.34", calls: 8 };
    v.messages = [...v.messages, msg("app", "3 edited copies created and verified · 14:35")];
    v.results = results();
    v.export = { folder: "", unsaved: v.results.length, copies: v.results.length };
    v.action = { label: "", token: "", kind: "none", can_hold: false };
    v.working = {
      generation: 1, from_originals: false, can_start_over: true,
      done: [(() => {
        const request = v.messages.find((m) => m.role === "user")?.text ?? "";
        return { id: "run-d1", request, recipe: "", documents: 3, of: 3, not: [], name: "", buttons: [], why_not: "",
          steps: [{ kind: "plan", label: "Your request", text: request, plan: ["Sets every font to Arial 10 pt, in the body, the headers and the footers.", "Replaces the footer line with “Riverside Medical Center — Issued for Construction”."], needs: [], places: ["Riverside Medical Center — Issued for Construction"], unguarded: false, files: [] }] };
      })()],
    };
    v.attachments = v.attachments.map((a) =>
      a.kind === "document" && a.state !== "refused" ? { ...a, current: `${RUN_DIR}/${a.name}` } : a,
    );
    return v;
  },
  /** Obliterate mode (shown in Settings → Advanced): three copies, one delivered UNVERIFIED with the check it failed. */
  "obliterate-done": () => {
    const v = SCENARIOS.done();
    settings = { ...settings, show_obliterate: true };
    v.mode = "obliterate";
    v.modes = ["safe", "default", "obliterate"];
    v.results = results().map((r, i) => (i === 1 ? { ...r, unverified: ["V2 (Text preservation)"] } : r));
    v.export = { folder: "", unsaved: v.results.length, copies: v.results.length };
    v.messages = [
      ...v.messages.slice(0, -1),
      msg("app", "3 document copies are ready. You can open them, describe another change to make on top of them, or start over from the originals. 1 was delivered UNVERIFIED: a check failed, and each one's failed checks are beside it."),
    ];
    return v;
  },
  /** Default mode with Obliterate shown in the top bar (Settings → Advanced): where its warning is opened from. */
  "obliterate-offered": () => {
    const v = SCENARIOS.attached();
    settings = { ...settings, show_obliterate: true };
    v.modes = ["safe", "default", "obliterate"];
    return v;
  },
  /** Obliterate mode on, the documents attached and nothing done yet: Word actions beside each document, Try to
   *  rescue beside the refused one, and the hands-on tools off until their switch turns them on. */
  obliterate: () => {
    const v = SCENARIOS["obliterate-offered"]();
    v.mode = "obliterate";
    return v;
  },
  /** …with two more refused documents: one protected with a password, one with a document type declaration. */
  "obliterate-rescue": () => {
    const v = SCENARIOS.obliterate();
    const at = v.attachments.findIndex((a) => a.state === "refused") + 1;
    v.attachments.splice(at, 0, attachment(ENCRYPTED.path, "document", ENCRYPTED), attachment(DOCTYPED.path, "document", DOCTYPED));
    return v;
  },
  /** …on a computer where Word cannot be reached: the menu says why, and so does a rescue that needs Word. */
  "obliterate-no-word": () => {
    const v = SCENARIOS["obliterate-rescue"]();
    wordAvailable = false;
    return v;
  },
  todo: () => {
    const v = SCENARIOS.done();
    const aside = DOCS[1];
    v.results = v.results.filter((r) => r.source !== aside.path);
    v.todo = [{ path: aside.path, name: name(aside.path), why: "held", reason: HELD_REASON }];
    v.working = { ...v.working, done: v.working.done.map((run) => ({ ...run, documents: 2, not: [{ name: name(aside.path), why: "set aside" }] })) };
    v.attachments = v.attachments.map((a) => (a.path === aside.path ? { ...a, current: a.path } : a));
    v.messages = [
      ...v.messages,
      msg("app", `On your to-do list: ${name(aside.path)}. When you’re ready to work on it, say “${WORK_TODO}” and tell me what should be different.`),
    ];
    return v;
  },
  paused: () => {
    const v = SCENARIOS.preview();
    v.stage = "budget";
    v.budget = { paused: true, reason: "limit" };
    v.cost = { usd: 18.41, text: "about $18.41", calls: 41, request: 17.62, limit: 20 };
    v.action = { label: "Continue", token: "tok-continue-1", kind: "continue", can_hold: false };
    return v;
  },
  "update-available": () => {
    const v = SCENARIOS.attached();
    v.update = { state: "available", version: "1.7.0", notes: "Faster previews: one Word instance per batch.", progress: 0, error: "" };
    return v;
  },
  "update-ready": () => {
    const v = SCENARIOS.attached();
    v.update = { state: "ready", version: "1.7.0", notes: "", progress: 100, error: "" };
    return v;
  },
  "up-to-date": () => {
    const v = SCENARIOS.attached();
    updateFound = false;
    return v;
  },
  "no-key": () => {
    const v = SCENARIOS.attached();
    v.connection = { ready: false, origin: "none" };
    settings = { ...settings, key_present: false, origin: "none" };
    return v;
  },
  "environment-key": () => {
    const v = SCENARIOS.attached();
    settings = { ...settings, key_present: true, origin: "environment" };
    v.connection = { ready: true, origin: "environment" };
    return v;
  },
  // One sample of two did not pass: the server sets it aside under Left untouched (`samples[].set_aside`) and Create
  // makes the other's copy.
  "failed-preview": () => {
    const v = SCENARIOS.preview();
    v.mode = "safe";
    failSample(v.samples[0]);
    v.samples[0].set_aside = true;
    v.plan = {
      ...v.plan,
      set_aside: [
        {
          name: v.samples[0].name,
          reason:
            "Its preview did not pass its checks, so no copy of it is made: The copy's wording does not match the change predicted by the plan (V2); The page layout does not match the layout predicted by the plan (V7).",
        },
      ],
    };
    v.action = approveAction(v, v.action.token);
    return v;
  },
  // Every sample did not pass: nothing shows the plan working, so Create is unavailable and says why.
  "failed-every-preview": () => {
    const v = SCENARIOS.preview();
    v.mode = "safe";
    v.samples.forEach(failSample);
    v.action.disabled_reason = `Create is unavailable: ${v.samples[0].name} did not pass its preview checks (V2, V7). Choose another change or prepare a new preview before creating copies.`;
    return v;
  },
};

let view = emptyView();
// Safe mode's ways back, as the app keeps them (docs/WEB_API.md, `POST /api/discard`, `POST /api/undo`): what the window
// showed before a button's preview cleared it, and the working set before each button's run, newest last.
let beforeButton = null;
let undoStack = [];
let lastRequest = "";
let lastRecipe = "";   // the button whose preview waits ("" for a request): its run's line in What you've done so far
function resetGoingBack() {
  beforeButton = null;
  undoStack = [];
}
function undoFields() {
  const top = undoStack[undoStack.length - 1];
  const blocked = !top ? "" : view.stage === "preview" ? "Discard the preview first."
    : ["question", "budget"].includes(view.stage) ? "Drop the request that is open first, or finish it." : "";
  return { undo: top ? top.request : "", undo_blocked: blocked };
}
/** What you've done so far with this run's line added (docs/WEB_API.md, `working.done`): the documents attached now,
 *  those it delivered, and why each other one got none. */
function doneWith(request, delivered, untouched, own = null) {
  const docs = view.attachments.filter((a) => a.kind === "document" && a.state !== "refused");
  const not = docs
    .filter((a) => !delivered.some((d) => d.source === a.path))
    .map((a) => ({ name: a.name, why: view.todo.some((t) => t.path === a.path) && !untouched.some((u) => u.path === a.path) ? "set aside" : "left untouched" }));
  const reasons = Object.fromEntries(untouched.map((u) => [u.name, u.reason]));
  const steps = lastRecipeRun
    ? lastRecipeRun.steps
    : lastRecipe
      ? [buttonStep(lastRecipe, lastInputs, request)]
      : [{ kind: "plan", label: "Your request", text: request, plan: own ? own.sentences : ["Sets the text of the documents as you asked."], needs: [], places: [], unguarded: view.mode === "obliterate", files: [] }];
  const run = {
    id: `run-${++seq}`, request, recipe: lastRecipe, documents: docs.length - not.length, of: docs.length, not,
    mode: view.mode, reasons,
    name: lastRecipeRun ? lastRecipeRun.name : "", steps,
    buttons: steps.filter((st) => st.kind === "button").map((st) => st.button),
    // one of Obliterate's own actions is no recipe step (saved_recipes.WHY_WORD, WHY_PART)
    why_not: own ? own.why_not : "",
    // what the record of where each document stands reads beside `not` (standingOf; the page reads neither)
    ...(own ? { apart: own.apart, whys: Object.fromEntries(untouched.map((u) => [u.name, u.why])) } : {}),
    marks: Object.fromEntries(delivered.filter((d) => (d.unverified ?? []).length > 0).map((d) => [d.name, d.unverified])),
  };
  lastRecipeRun = null;
  return [...(view.working.done ?? []), run];
}

// --- Saved recipes (src/spec_surgeon/saved_recipes.py): kept across scenarios like the settings ---------------------

let lastInputs = {};          // the form the waiting button preview was built from
let lastRecipeRun = null;     // the saved recipe whose preview waits: { id, name, steps }
function buttonStep(id, inputs, request) {
  const button = RECIPES.recipes.find((r) => r.id === id);
  return { kind: "button", label: button?.label ?? id, text: request, plan: [], needs: [...(button?.needs ?? [])], places: [], unguarded: false, button: id, inputs: { ...inputs } };
}
function modeOfSteps(steps) {
  if (steps.some((st) => st.kind === "plan" && st.unguarded)) return "obliterate";
  return steps.some((st) => st.kind === "plan") ? "default" : "safe";
}
let savedRecipes = [
  {
    id: "r-seed-ifc", name: "Division 21 · Issued for Construction", note: "Bid set to IFC: office headers and footers, clean copies",
    saved: "2026-10-27T15:02:00+00:00", last_run: "", last_documents: 0,
    steps: [
      buttonStep("headers_footers_template", { slots: {} }, "Match headers and footers to the template Office master 2026.docx"),
      buttonStep("accept_changes", { action: "accept" }, "Accept all tracked changes"),
      buttonStep("remove_comments", {}, "Remove all comments"),
    ],
  },
  {
    id: "r-seed-nfpa", name: "Riverside closeout: NFPA 13 2025 edition", note: "",
    saved: "2026-10-28T18:40:00+00:00", last_run: "2026-10-28T19:05:00+00:00", last_documents: 5,
    steps: [{ kind: "plan", label: "Your request", text: "Replace “NFPA 13, 2022 edition” with “NFPA 13, 2025 edition” everywhere, headers and footers too.", plan: ["Replaces “NFPA 13, 2022 edition” with “NFPA 13, 2025 edition” in the body, the headers and the footers."], needs: [], places: ["NFPA 13, 2025 edition"], unguarded: false, files: [] }],
  },
];
const RANK = { safe: 0, default: 1, obliterate: 2 };
/** What an editor opened on (saved_recipes.revision): its name, note and steps, never when it last ran. */
function revOf(r) {
  const text = JSON.stringify({ name: r.name, note: r.note, steps: r.steps });
  let h = 0;
  for (let i = 0; i < text.length; i++) h = (h * 31 + text.charCodeAt(i)) | 0;
  return (h >>> 0).toString(16);
}
function recipesOf(v) {
  return savedRecipes
    .map((r) => ({ ...r, mode: modeOfSteps(r.steps) }))
    .filter((r) => r.mode !== "obliterate" || settings.show_obliterate)
    .map((r) => {
      const here = RANK[v.mode] >= RANK[r.mode];
      return {
        ...r, rev: revOf(r), needs: [...new Set(r.steps.flatMap((st) => st.needs))], runs_here: here,
        why_not_here: here ? "" : r.mode === "obliterate" ? "It was saved in Obliterate mode and runs only there." : "It was saved in Default mode: Safe mode runs only recipes made of its own buttons.",
      };
    });
}
function recipeBlockedOf(v) {
  if (v.mode === "safe") return "";
  if (v.focus?.length) return "Finish the pass over your to-do list first.";
  const kind = discardOf(v);
  return kind === "preview" ? "Discard the preview first, or create its copies." : kind === "request" ? "Drop the request that is open first, or finish it." : "";
}
/** What Discard withdraws now, as the app says it (docs/WEB_API.md, `discard`). */
/** What Export would save, as the app counts it (docs/WEB_API.md, `export`): the copies the window lists, and those not
 *  saved as Word with their tracked changes. */
function exportOf(v) {
  const results = v.results ?? [];
  const anyway = anywayOf(v);
  return {
    folder: v.export?.folder ?? "", unsaved: results.filter((r) => !r.saved_to).length, copies: results.length,
    ...(anyway.length ? { anyway } : {}),   // absent when there is nothing to offer, as the server leaves it out
  };
}
function discardOf(v) {
  if (v.busy) return "";
  if (v.stage === "preview") return "preview";
  if (v.mode !== "safe" && ["question", "resume", "budget"].includes(v.stage)) return "request";
  return "";
}

// --- Obliterate's own tools: what the mock keeps for them, and the view's fields (docs/WEB_API.md) ---------------

let wordAvailable = true;          // whether Word can be reached; the obliterate-no-word scenario turns it off
/** The plan the app built itself that waits for approval — Word actions, or Replace this part: `{ source: "word" |
 *  "hands-on", deliver: [{path, marks}], failed: [{path, name, reason, why}], apart: {name: reason}, sentences,
 *  why_not, replace?: {key, part} }`. Create delivers the copies it shows; no model is ever asked about it. */
let ownPlan = null;
const replacedParts = new Map();   // `${document path}\t${part}` → the file a created Replace this part put there
const savedParts = new Map();      // where Save this part wrote → what it wrote there
let rescues = 0;
const exportMissed = { accepted: new Set(), pdf: new Set() };   // the copies (`output`) a clean export left without a file
function resetObliterate() {
  ownPlan = null;
  replacedParts.clear();
  savedParts.clear();
  exportMissed.accepted.clear();
  exportMissed.pdf.clear();
}

const WORD_ACTIONS = Object.fromEntries([
  ...WORD_MENU,
  // Export anyway's alone, on no menu (word_actions.MENU, `offered=False`)
  { id: "delete_comments", label: "Delete all comments", does: "Word deletes every comment in the copy, with its replies.", one: "{n} comment deleted", many: "{n} comments deleted" },
].map((action) => [action.id, action]));
const listed = (names) => (names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`);
const failedMarks = (r) => (r.unverified ?? []).filter((m) => !m.includes("UNPROVEN"));
const salvagedOf = (v, source) => v.attachments.find((a) => a.kind === "document" && a.path === source)?.salvaged;
/** A copy no check proves as it is: delivered UNVERIFIED or UNPROVEN, or made from a document the app rescued. */
const marked = (v, r) => (r.unverified ?? []).length > 0 || !!salvagedOf(v, r.source);

/** The menu of named Word actions as the view carries it (`word`). */
function wordMenu() {
  return { available: wordAvailable, why: wordAvailable ? "" : NO_WORD, actions: WORD_MENU.map(({ id, label, does }) => ({ id, label, does })), minutes: WORD_MINUTES };
}
/** What Try to rescue would try for a refused document (`attachments[].rescue`), read off why triage refused it;
 *  "" when there is nothing to try. */
function rescueKind(a) {
  if (a.kind !== "document" || a.state !== "refused") return "";
  if (/\.(doc|docm)$/i.test(a.path)) return "format";
  if (a.reason.includes("password-protected")) return "password";
  if (a.reason.includes("document type declaration")) return "doctype";
  return a.reason.includes("cannot be opened as a Word package") ? "unopenable" : "";
}
/** Export anyway's copies for a clean kind: in Obliterate mode alone, each a marked copy that kind's checked export
 *  left without a file. */
function anywayCopies(v, kind) {
  return v.mode === "obliterate" ? (v.results ?? []).filter((r) => exportMissed[kind]?.has(r.output) && marked(v, r)) : [];
}
function anywayOf(v) {
  return Object.keys(ANYWAY_LABELS).flatMap((kind) => {
    const copies = anywayCopies(v, kind);
    return copies.length ? [{ kind, label: ANYWAY_LABELS[kind], does: ANYWAY_DOES[kind], copies: copies.map((r) => r.name), blocked: wordAvailable ? "" : NO_WORD }] : [];
  });
}
/** The view's fields for Obliterate's tools: `hands_on` and `word` in that mode alone, `rescue` on a refused document
 *  with something to try (that mode alone too), and its mark's sentence on each copy of a rescued document. */
function obliterateFields(v) {
  const on = v.mode === "obliterate";
  v.hands_on = on && v.hands_on === true;
  v.word = on ? wordMenu() : null;
  for (const a of v.attachments) {
    const kind = on ? rescueKind(a) : "";
    if (kind) a.rescue = kind;
    else delete a.rescue;
  }
  for (const r of v.results ?? []) {
    const record = salvagedOf(v, r.source);
    if (record) r.salvaged = SALVAGED_LINE(record.from);
    else delete r.salvaged;
  }
}

// --- The hands-on tools' reading half: a package made up for each document (src/spec_surgeon/hands_on.py) --------

const PAGE_LINES = 400;            // hands_on.PAGE_LINES, LINE_CHARS, SEARCH_HITS, QUERY_CHARS
const LINE_CHARS = 2000;
const SEARCH_HITS = 200;
const QUERY_CHARS = 200;
const XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n';
const NS_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"';
const NS_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"';
const REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const PKG = "http://schemas.openxmlformats.org/package/2006";
const WML = "application/vnd.openxmlformats-officedocument.wordprocessingml";
const RELS_TYPE = "application/vnd.openxmlformats-package.relationships+xml";
const SPEC_PARTS = [
  ["GENERAL", ["SUMMARY", "REFERENCES", "SUBMITTALS", "QUALITY ASSURANCE"]],
  ["PRODUCTS", ["MANUFACTURERS", "MATERIALS", "COMPONENTS", "ACCESSORIES"]],
  ["EXECUTION", ["EXAMINATION", "INSTALLATION", "FIELD QUALITY CONTROL", "CLEANING"]],
];
const SPEC_LINES = [
  "Section includes the materials, installation and testing of the work shown on the Drawings.",
  "Comply with the codes and standards adopted by the authority having jurisdiction.",
  "Submit product data and shop drawings before the work starts.",
  "Deliver materials in the manufacturer's original packaging, and store them off the ground and under cover.",
];
const esc = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const unesc = (text) => text.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&amp;/g, "&");
const para = (text, style) => `<w:p><w:pPr><w:pStyle w:val="${style}"/></w:pPr><w:r><w:t>${esc(text)}</w:t></w:r></w:p>`;

/** The members of a document's file, in the package's own order: `{name, type, xml, text, size}`, `text` the part's
 *  own bytes as text (null for a part that is not text). Made up from the document's name and triage's notes: the
 *  one whose numbers are typed has no numbering part, the restricted one says so in its settings, and the one
 *  with comments has a comments part. */
function packageOf(a) {
  const notes = a.notes ?? [];
  const number = /^\d\d \d\d \d\d/.exec(a.name)?.[0] ?? "00 00 00";
  const title = a.name.replace(/^\d\d \d\d \d\d\s*/, "").replace(/\.docx$/i, "");
  const typed = notes.some((n) => n.includes("typed by hand"));
  const restricted = notes.some((n) => n.includes("restricted"));
  const commented = notes.some((n) => n.includes("comment"));
  const body = [para(`SECTION ${number}`, "SCT"), para(title.toUpperCase(), "TTL")];
  SPEC_PARTS.forEach(([part, articles], i) => {
    body.push(para(`PART ${i + 1} - ${part}`, "PRT"));
    articles.forEach((article, j) => {
      body.push(para(typed ? `${i + 1}.${j + 1} ${article}` : article, "ART"));
      SPEC_LINES.forEach((line, k) => body.push(para(typed ? `${"ABCD"[k]}. ${line}` : line, "PR1")));
    });
  });
  body.push(para(`END OF SECTION ${number}`, "EOS"));
  const style = (id, label) => `<w:style w:type="paragraph" w:styleId="${id}"><w:name w:val="${label}"/><w:basedOn w:val="Normal"/></w:style>`;
  const members = [
    ["_rels/.rels", RELS_TYPE, `<Relationships xmlns="${PKG}/relationships"><Relationship Id="rId1" Type="${REL}/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="${PKG}/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="${REL}/extended-properties" Target="docProps/app.xml"/></Relationships>`],
    ["word/document.xml", `${WML}.document.main+xml`, `<w:document ${NS_W} ${NS_R}><w:body>${body.join("")}<w:sectPr><w:headerReference w:type="default" r:id="rId4"/><w:footerReference w:type="default" r:id="rId5"/><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr></w:body></w:document>`],
    ["word/_rels/document.xml.rels", RELS_TYPE, `<Relationships xmlns="${PKG}/relationships"><Relationship Id="rId1" Type="${REL}/styles" Target="styles.xml"/><Relationship Id="rId2" Type="${REL}/settings" Target="settings.xml"/>${typed ? "" : `<Relationship Id="rId3" Type="${REL}/numbering" Target="numbering.xml"/>`}<Relationship Id="rId4" Type="${REL}/header" Target="header1.xml"/><Relationship Id="rId5" Type="${REL}/footer" Target="footer1.xml"/><Relationship Id="rId6" Type="${REL}/image" Target="media/image1.png"/>${commented ? `<Relationship Id="rId7" Type="${REL}/comments" Target="comments.xml"/>` : ""}</Relationships>`],
    ["word/styles.xml", `${WML}.styles+xml`, `<w:styles ${NS_W}><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/><w:sz w:val="20"/></w:rPr></w:style>${[["SCT", "Section Number"], ["TTL", "Section Title"], ["PRT", "PRT"], ["ART", "ART"], ["PR1", "PR1"], ["EOS", "End of Section"], ["Header", "header"], ["Footer", "footer"]].map(([id, label]) => style(id, label)).join("")}</w:styles>`],
    ["word/settings.xml", `${WML}.settings+xml`, `<w:settings ${NS_W}><w:zoom w:percent="100"/>${restricted ? '<w:documentProtection w:edit="readOnly" w:enforcement="1"/>' : ""}<w:defaultTabStop w:val="720"/><w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>`],
    ...(typed ? [] : [["word/numbering.xml", `${WML}.numbering+xml`, `<w:numbering ${NS_W}><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="multilevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="PART %1"/></w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1.%2"/></w:lvl><w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="%3."/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>`]]),
    ...(commented ? [["word/comments.xml", `${WML}.comments+xml`, `<w:comments ${NS_W}>${["Confirm with the Owner.", "Coordinate with Division 26.", "Check against the Drawings."].map((text, i) => `<w:comment w:id="${i}" w:author="Reviewer"><w:p><w:r><w:t>${text}</w:t></w:r></w:p></w:comment>`).join("")}</w:comments>`]] : []),
    ["word/header1.xml", `${WML}.header+xml`, `<w:hdr ${NS_W}>${para(`${number} ${title}`, "Header")}</w:hdr>`],
    ["word/footer1.xml", `${WML}.footer+xml`, `<w:ftr ${NS_W}>${para("Riverside Medical Center — Issued for Bid", "Footer")}</w:ftr>`],
    ["word/media/image1.png", "image/png", null],
    ["docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml", `<cp:coreProperties xmlns:cp="${PKG}/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>${esc(title)}</dc:title><dc:creator>Riverside Design Team</dc:creator><cp:revision>14</cp:revision></cp:coreProperties>`],
    ["docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml", `<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Microsoft Office Word</Application><Pages>12</Pages></Properties>`],
  ];
  const types = `<Types xmlns="${PKG}/content-types"><Default Extension="rels" ContentType="${RELS_TYPE}"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>${members.filter(([name, type]) => name.endsWith(".xml") && type !== RELS_TYPE).map(([name, type]) => `<Override PartName="/${name}" ContentType="${type}"/>`).join("")}</Types>`;
  return [["[Content_Types].xml", "", types], ...members].map(([name, type, text]) =>
    text === null
      ? { name, type, xml: false, text: null, size: 18_432 }
      : { name, type, xml: true, text: XML_HEAD + text, size: Buffer.byteLength(XML_HEAD + text) },
  );
}

/** The parts of the file the browser reads: the attached original, or (`current`) the copy the next change is made
 *  on, which holds whatever a created Replace this part put in a part's place. */
function partsOf(a, which) {
  return packageOf(a).map((p) => (which === "original" ? p : (replacedParts.get(`${a.path}\t${p.name}`) ?? p)));
}

/** Why text does not read as XML, near enough for a mock (one root, every tag closed in its turn); "" when it does. */
function xmlProblem(text) {
  const tokens = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[[\s\S]*?\]\]>|<(\/?)([A-Za-z_][\w:.-]*)((?:\s+[\w:.-]+\s*=\s*(?:"[^"<]*"|'[^'<]*'))*)\s*(\/?)>|<|&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)/g;
  const open = [];
  let roots = 0;
  let last = 0;
  for (let m = tokens.exec(text); m; m = tokens.exec(text)) {
    if (open.length === 0 && text.slice(last, m.index).replace(/^﻿/, "").trim()) return "text outside the root element";
    last = tokens.lastIndex;
    if (m[0] === "<" || m[0] === "&") return `a stray ${m[0]} at character ${m.index + 1}`;
    if (!m[2]) continue;               // a comment, a processing instruction or a CDATA section
    if (m[1]) {
      if (open.pop() !== m[2]) return `</${m[2]}> closes an element that is not open`;
    } else {
      if (open.length === 0 && ++roots > 1) return "more than one root element";
      if (!m[4]) open.push(m[2]);
    }
  }
  if (open.length) return `<${open[open.length - 1]}> is never closed`;
  if (text.slice(last).trim()) return "text after the root element";
  return roots === 1 ? "" : "no root element";
}

/** XML indented for reading, as the browser shows a part: an element per line, one that holds only text on one line. */
function indentXml(text) {
  const tokens = text.match(/<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<[^>]+>|[^<]+/g) ?? [];
  const lines = [];
  let depth = 0;
  for (let i = 0; i < tokens.length; i++) {
    const token = tokens[i];
    const pad = "  ".repeat(depth);
    if (token.startsWith("<?")) continue;                       // the declaration: not part of the tree
    if (!token.startsWith("<")) {
      if (token.trim()) lines.push(pad + token.trim());
    } else if (token.startsWith("</")) {
      depth = Math.max(0, depth - 1);
      lines.push("  ".repeat(depth) + token);
    } else if (token.startsWith("<!--") || token.endsWith("/>")) {
      lines.push(pad + token);
    } else if (tokens[i + 1]?.startsWith("</")) {
      lines.push(pad + token + tokens[++i]);
    } else if (tokens[i + 1] !== undefined && !tokens[i + 1].startsWith("<") && tokens[i + 2]?.startsWith("</")) {
      lines.push(pad + token + tokens[i + 1] + tokens[i + 2]);
      i += 2;
    } else {
      lines.push(pad + token);
      depth += 1;
    }
  }
  return lines;
}

/** A part as the browser shows it: `{lines, note}` (hands_on.Reader.shown). */
function shownOf(p) {
  if (!p.xml || p.text === null) {
    return { lines: [], note: `Not text: ${p.type || "no declared type"}, ${p.size.toLocaleString("en-US")} bytes. Save it out to open it.` };
  }
  const problem = xmlProblem(p.text);
  const lines = problem ? p.text.split(/\r?\n/) : indentXml(p.text);
  return {
    lines: lines.map((line) => (line.length <= LINE_CHARS ? line : `${line.slice(0, LINE_CHARS)} … (${(line.length - LINE_CHARS).toLocaleString("en-US")} more characters on this line)`)),
    note: problem ? `Not well-formed XML (${problem}): shown as its own text.` : "Shown indented for reading; Save writes the part's own bytes.",
  };
}

/** One page of a part (hands_on.Reader.page). */
function pageOf(p, page) {
  const shown = shownOf(p);
  const total = shown.lines.length;
  const pages = Math.max(1, Math.ceil(total / PAGE_LINES));
  const at = Math.min(Math.max(1, Number.isInteger(page) ? page : 1), pages);
  const start = (at - 1) * PAGE_LINES;
  return { part: p.name, size: p.size, type: p.type, xml: p.xml, lines: shown.lines.slice(start, start + PAGE_LINES), first: start + 1, page: at, pages, total, note: shown.note };
}

/** Every shown line of every text part that holds the words, letters compared without case (hands_on.Reader.search). */
function searchOf(parts, query) {
  const needle = String(query ?? "").split(/\s+/).filter(Boolean).join(" ").toLowerCase().slice(0, QUERY_CHARS);
  if (!needle) return { query: "", matches: [], more: false };
  const matches = [];
  for (const p of parts) {
    if (!p.xml) continue;
    const lines = shownOf(p).lines;
    for (let i = 0; i < lines.length; i++) {
      const at = lines[i].toLowerCase().indexOf(needle);
      if (at < 0) continue;
      if (matches.length >= SEARCH_HITS) return { query: needle, matches, more: true };
      const start = Math.max(0, at - 80);
      matches.push({ part: p.name, line: i + 1, page: Math.floor(i / PAGE_LINES) + 1, text: (start ? "…" : "") + lines[i].slice(start, at + needle.length + 120).trim() });
    }
  }
  return { query: needle, matches, more: false };
}

/** `{a}` — the attached document a hands-on tool is asked about — or `{error}` in the server's words: the tools are
 *  off, or the path is not one of the documents (document_task._hands_on_document). */
function handsOnDocument(path) {
  if (view.mode !== "obliterate" || !view.hands_on) return { error: HANDS_ON_NEEDED };
  const a = view.attachments.find((x) => x.kind === "document" && x.state !== "refused" && x.path === path);
  return a ? { a } : { error: "That is not one of the documents attached here." };
}

/** The words of each paragraph of a part, in order: what the independent checker reads. */
function wordsOf(text) {
  return [...text.matchAll(/<w:p[ >][\s\S]*?<\/w:p>/g)].map((p) =>
    [...p[0].matchAll(/<w:t(?:\s[^>]*)?>([^<]*)<\/w:t>/g)].map((t) => unesc(t[1])).join(""),
  );
}
/** `samples[].text_changes` for a replaced part: one entry per paragraph whose words differ, the stretch that does. */
function partTextChanges(part, before, after) {
  const was = wordsOf(before);
  const is = wordsOf(after);
  const where = (i) => (part === "word/document.xml" ? `body paragraph ${i + 1}` : /\/header\d*\.xml$/.test(part) ? "header" : /\/footer\d*\.xml$/.test(part) ? "footer" : part);
  const out = [];
  for (let i = 0; i < Math.max(was.length, is.length); i++) {
    if (was[i] === is[i]) continue;
    const entry = { part, where: where(i), paragraph: i, removed: [], added: [] };
    if (was[i] === undefined) {
      entry.block = "new";
      if (is[i]) entry.added = [is[i]];
    } else if (is[i] === undefined) {
      entry.block = "gone";
      if (was[i]) entry.removed = [was[i]];
    } else {
      const x = was[i].split(" ");
      const y = is[i].split(" ");
      let head = 0;
      while (head < x.length && head < y.length && x[head] === y[head]) head++;
      let tail = 0;
      while (tail < x.length - head && tail < y.length - head && x[x.length - 1 - tail] === y[y.length - 1 - tail]) tail++;
      const gone = x.slice(head, x.length - tail).join(" ");
      const come = y.slice(head, y.length - tail).join(" ");
      if (gone) entry.removed = [gone];
      if (come) entry.added = [come];
    }
    out.push(entry);
  }
  return out;
}

// --- Word's side: what its actions find in a document, and what is tried for a refused file ----------------------

/** Why the actions cannot run as asked, in the menu's words (word_actions.check); "" when they can. */
function wordProblem(actions) {
  if (!Array.isArray(actions) || actions.length === 0 || !actions.every((id) => typeof id === "string")) return "Choose at least one Word action.";
  const seen = [];
  for (const id of actions) {
    const action = WORD_MENU.find((x) => x.id === id);
    if (!action) return `'${id}' is not a Word action on the menu (${WORD_MENU.map((x) => x.id).join(", ")}).`;
    if (seen.includes(id)) return `${action.label} is listed twice; an action runs once.`;
    seen.push(id);
  }
  if (seen.includes("repair") && seen[0] !== "repair") return `${WORD_ACTIONS.repair.label} comes first: it is how Word opens the copy.`;
  if (seen.includes("accept_changes") && seen.includes("reject_changes")) return "Accepting and rejecting every tracked change cannot both run: after one, none is left for the other.";
  return "";
}
/** How many things an action finds to do in a document (WORD_FINDS; a document's comments for Export anyway's own). */
function wordFound(a, id) {
  if (id === "delete_comments") return (a.notes ?? []).some((n) => n.includes("comment")) ? 3 : 0;
  const row = WORD_FINDS[id];
  const i = DOCS.findIndex((d) => d.path === a.path);
  return row ? row[i < 0 ? 0 : i] : 1;
}
/** Word's own account of what its actions did to a document's copy, one line per action (word_actions.account). */
function wordAccount(a, actions) {
  return actions.map((id) => {
    const action = WORD_ACTIONS[id];
    const n = wordFound(a, id);
    if (id === "repair") return action.one;
    return n ? (n === 1 ? action.one : action.many).replace("{n}", n.toLocaleString("en-US")) : `${action.label}: nothing to do`;
  });
}
/** One document's sample of a Word run (`samples[]` with `made_by: "word"`): its copy as the independent reader
 *  read it, or no copy when Word found nothing to do in it. */
function wordSample(a, actions, index) {
  const account = wordAccount(a, actions);
  const base = { source: a.path, name: a.name, images: [], pages_before: null, pages_after: null, failures: [], geometry: null, text_changes: null, unproven: true, changes: [], no_copy: false, raw_xml: false, raw_what: "", made_by: "word", word_account: [] };
  if (!actions.some((id) => id === "repair" || wordFound(a, id) > 0)) {
    return { ...base, passed: false, no_copy: true, failures: [{ check: "Preview", reason: `${NOTHING_TO_DO}: ${account.join("; ")}.` }] };
  }
  const shows = actions.filter((id) => id === "repair" || wordFound(a, id) > 0).map((id) => WORD_SHOWS[id](wordFound(a, id)));
  return {
    ...base,
    images: [`/api/image?path=${encodeURIComponent(`${RUN_DIR}/preview/${(index % 2) + 1}.png`)}`],
    pages_before: 12, pages_after: 12, passed: true,
    geometry: { status: "NA", note: "Word saved the whole file again: its pages are shown, not compared with a prediction." },
    text_changes: shows.flatMap((x) => x.words), changes: shows.flatMap((x) => x.other), word_account: account,
  };
}

/** What a rescue did, one sentence (rescue.py): the app's own mending, or Word's, with Word's own account. */
const RESCUE_STEPS = {
  doctype: () => "The app wrote word/document.xml again without the document type declaration; every other part is your file's, byte for byte.",
  format: (a) => `Word opened a private copy of the ${/\.[^.]+$/.exec(a.name)[0].toLowerCase()} file and saved it as a .docx (Word's own account: saved as a Word document).`,
  password: () => "Word opened a private copy with the password you typed and saved it as a .docx without one (Word's own account: saved as a Word document).",
  unopenable: () => "Word opened a private copy with Open and Repair and saved what it could read as a .docx (Word's own account: opened with Open and Repair and saved again).",
};

/** Why a plan of the app's own (a part replacement, Word actions) cannot be made now; "" when it can: a preview that
 *  waits or a request that is open is finished or dropped first (document_task._hands_on_blocked). */
function ownPlanBlocked() {
  if (view.stage === "preview") return "Discard the preview first, or create its copies.";
  if (["question", "budget"].includes(view.stage)) return "Drop the request that is open first, or finish it.";
  return "";
}
/** The documents a plan covers now: those of a pass over the to-do list, else every one that is not set aside. */
function documentsInScope() {
  return view.attachments.filter(
    (a) => a.kind === "document" && a.state !== "refused" && (view.focus.length ? view.focus.includes(a.path) : !view.todo.some((t) => t.path === a.path)),
  );
}
/** Where the window rests with nothing waiting: on its copies, else empty. */
const restingStage = () => (view.results.length && view.working.generation ? "done" : "empty");
/** An own plan that waits is withdrawn (the hands-on tools turned off, the documents changed): there is nothing to
 *  continue, since the app plans it again only when it is asked again. */
function withdrawOwn() {
  ownPlan = null;
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.action = { label: "", token: "", kind: "none", can_hold: false };
  view.stage = restingStage();
}
let settings = { key_present: true, origin: "saved", can_remember: true, export_folder: "", logs_dir: "C:/Users/abe/AppData/Roaming/Spec Surgeon/logs", theme: "clay", fast_mode: false, show_obliterate: false, tutorial_version: 0, safe_note_hidden: false, spending_limit_usd: 20, spending_default_usd: 20, fast: { model: "claude-opus-5-5", available: true, speedup: 2.5, price_factor: 2, standard_usd_per_mtok: [4, 20], fast_usd_per_mtok: [8, 40], refused: null } };
const THEMES = ["clay", "blueprint", "graphite", "carbon", "linen"];
function keySettings() { return { ...settings, can_remove_key: ["saved", "session"].includes(settings.origin) }; }
let running = null; // { stop: boolean }
let downloadTimer = null;
let updateFound = true; // what the next check for updates finds; the up-to-date scenario turns it off

// --- Helpers --------------------------------------------------------------

/** Where each document stands, from what the mock keeps: each run of What you've done so far is an operation; a
 *  document its run left untouched or the red X set aside failed it (the to-do list), one attached since was not run.
 *  A cell the pass later fixed (`fixed`) or the user waived (`waived`) reads so. A scenario that sets `standing`
 *  itself (`standing_fixed`) keeps it. */
function standingOf(v) {
  if (v.standing_fixed) return v.standing;
  const docs = v.attachments.filter((a) => a.kind === "document" && a.state !== "refused");
  const runs = v.working?.done ?? [];
  const waived = v.waived ?? [];
  // a document's badges: the mark its copy carries (`results[].unverified`, a rescued document's own), then set aside
  const badges = (a) => {
    const marks = (v.results ?? []).find((r) => r.source === a.path)?.unverified ?? [];
    return [
      ...(a.salvaged ? ["SALVAGED"] : []),
      ...(marks.some((m) => !m.includes("UNPROVEN")) ? ["UNVERIFIED"] : marks.length ? ["UNPROVEN"] : []),
      ...(v.todo.some((t) => t.path === a.path) ? ["set aside"] : []),
    ];
  };
  const rows = docs.map((a) => [
    a.path,
    badges(a),
    ...runs.map((run) => {
      const mode = run.mode ?? (run.recipe || v.mode === "safe" ? "safe" : v.mode);
      const miss = (run.not ?? []).find((n) => n.name === a.name);
      const reason = run.reasons?.[a.name];
      // one of Obliterate's own actions, asked for another document: it was not run on this one
      if (miss && run.apart?.[a.name]) return skipped("not part of it", "This was not run on it.");
      const fail = miss?.why === "set aside" ? failed(mode, "held", HELD_REASON)
        : miss?.why === "left untouched" ? failed(mode, run.whys?.[a.name] ?? "refused", reason ?? "It could not be changed exactly, so it was left as it is.")
        : null;
      if (run.fixed?.[a.name]) return [fail ?? failed(mode, "refused", reason ?? "It could not be changed."), ran(run.fixed[a.name])];
      if (fail && waived.includes(`${run.id}\t${a.path}`)) return [fail, { ...skipped("waived", "You said it is not needed on this one.", mode), state: "waived" }];
      if (fail) return fail;
      return miss ? skipped("attached later", "It was attached after this was done.") : ran(mode, run.marks?.[a.name] ? { marks: run.marks[a.name] } : {});
    }),
  ]);
  const standing = standingView(runs.map((run) => ({ id: run.id, request: run.request, mode: run.mode ?? (run.recipe ? "safe" : v.mode === "safe" ? "default" : v.mode), recipe: run.recipe ?? "", name: run.name ?? "" })), rows);
  if (settings.show_obliterate) {
    for (const d of standing.documents) for (const c of d.cells) if (c.state === "failed") c.obliterate = v.focus.length ? "Finish the pass over your to-do list first, or drop it." : "";
  }
  return standing;
}

/** The to-do list: every failed cell of the record, oldest operation first. */
function owedOf(v) {
  const standing = standingOf(v);
  return standing.operations.flatMap((op, i) => standing.documents.filter((d) => d.cells[i].state === "failed").map((d) => ({ operation: op.id, path: d.path, name: d.name, request: op.request })));
}

/** The fields of the view that follow from the rest of it, settled as every answer that carries the view is sent. */
function settle() {
  obliterateFields(view);
  view.standing = standingOf(view);
  view.discard = discardOf(view);
  view.export = exportOf(view);
  view.saved_recipes = recipesOf(view);
  view.recipe_blocked = recipeBlockedOf(view);
}

function json(res, status, body) {
  if (body === view) settle();
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(data),
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, X-Spec-Surgeon-Token",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolveBody) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolveBody(Buffer.concat(chunks)));
  });
}

async function readJson(req) {
  const raw = (await readBody(req)).toString("utf8");
  if (!raw.trim()) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pageReports = [];

function tokenOf(req, url) {
  return req.headers["x-spec-surgeon-token"] || url.searchParams.get("token") || "";
}

// --- PNG: a side-by-side before/after picture, drawn here -----------------

const CRC_TABLE = new Uint32Array(256).map((_, n) => {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const b of buf) c = CRC_TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([len, body, crc]);
}
function encodePng(width, height, rgb) {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (width * 3 + 1)] = 0;
    rgb.copy(raw, y * (width * 3 + 1) + 1, y * width * 3, (y + 1) * width * 3);
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 2; // RGB
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** Two rendered pages side by side: the original and the edited copy. */
function previewPng(variant) {
  const W = 1200;
  const H = 780;
  const rgb = Buffer.alloc(W * H * 3, 0xf3);
  const fill = (x0, y0, w, h, [r, g, b]) => {
    for (let y = Math.max(0, y0); y < Math.min(H, y0 + h); y++) {
      for (let x = Math.max(0, x0); x < Math.min(W, x0 + w); x++) {
        const i = (y * W + x) * 3;
        rgb[i] = r;
        rgb[i + 1] = g;
        rgb[i + 2] = b;
      }
    }
  };
  const page = (x0, after) => {
    fill(x0, 20, 540, 740, [255, 255, 255]);
    fill(x0, 20, 540, 1, [214, 210, 200]);
    fill(x0, 759, 540, 1, [214, 210, 200]);
    fill(x0, 20, 1, 740, [214, 210, 200]);
    fill(x0 + 539, 20, 1, 740, [214, 210, 200]);
    // header: a logo block and a title line
    fill(x0 + 50, 56, after ? 96 : 70, 32, after ? [217, 119, 87] : [150, 150, 150]);
    fill(x0 + 170, 68, 280, 8, [120, 120, 120]);
    // body: paragraphs of text lines
    let y = 130;
    const seeded = (n) => ((n * 9301 + 49297 + variant * 131) % 233280) / 233280;
    for (let p = 0; p < 7; p++) {
      const lines = 3 + Math.floor(seeded(p) * 4);
      fill(x0 + 50, y, 140 + Math.floor(seeded(p + 20) * 120), 7, after ? [60, 60, 60] : [90, 90, 90]);
      y += 18;
      for (let l = 0; l < lines; l++) {
        const w = 440 - (l === lines - 1 ? Math.floor(seeded(p * 7 + l) * 260) : 0);
        fill(x0 + 50, y, w, after ? 5 : 6, [170, 170, 170]);
        y += after ? 13 : 15;
      }
      y += 16;
      if (y > 660) break;
    }
    // footer
    fill(x0 + 50, 712, after ? 300 : 240, 6, after ? [217, 119, 87] : [160, 160, 160]);
    fill(x0 + 470, 712, 20, 6, [160, 160, 160]);
  };
  page(40, false);
  page(620, true);
  // the gutter label
  fill(596, 20, 8, 740, [230, 226, 216]);
  return encodePng(W, H, rgb);
}

// --- Jobs -----------------------------------------------------------------

function startStream(res) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-store",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });
  const job = { stop: false, res };
  running = job;
  view.busy = true;
  const emit = async (frame) => {
    res.write(`data: ${JSON.stringify(frame)}\n\n`);
    if (DELAY > 0) await sleep(DELAY);
  };
  const text = async (reply, step = 18) => {
    for (let i = 0; i < reply.length; i += step) {
      if (job.stop) return;
      await emit({ type: "text_delta", text: reply.slice(0, i + step).slice(i) });
    }
  };
  const end = () => {
    view.busy = false;
    running = null;
    settle();
    res.write(`data: ${JSON.stringify({ type: "done", state: view })}\n\n`);
    res.end();
  };
  // `resend`: the request's text when it was refused before it was recorded (the real server sends it when no
  // user message with that text was kept); the page takes it back into the composer.
  const fail = (message, resend = "") => {
    view.busy = false;
    running = null;
    settle();
    res.write(`data: ${JSON.stringify({ type: "error", message, state: view, ...(resend ? { resend } : {}) })}\n\n`);
    res.end();
  };
  return { job, emit, text, end, fail };
}

function spend(usd, calls) {
  view.cost = {
    usd: Math.round((view.cost.usd + usd) * 100) / 100,
    text: "",
    calls: view.cost.calls + calls,
    fast: settings.fast_mode,
    request: Math.round(((view.cost.request ?? 0) + usd) * 100) / 100,
    limit: settings.spending_limit_usd,
  };
  view.cost.text = view.cost.usd < 0.005 ? "less than $0.01" : `about $${view.cost.usd.toFixed(2)}`;
  return view.cost;
}

async function questionJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.blocking = false;
  view.action = { label: "", token: "", kind: "none" };
  // Two model calls, as the real server streams them: the first says what it will do and reads; the second (its
  // `thinking` status starts it) writes the reply, and the first call's words are set aside as a step.
  await s.emit({ type: "status", kind: "thinking", text: "Thinking…" });
  await s.text("I'll read the documents and the template first.", 6);
  await s.emit({ type: "activity", text: "Reading the three documents and their headers and footers" });
  await s.emit({ type: "activity", text: "Reading the template's header and footer" });
  await s.emit({ type: "status", kind: "thinking", text: "Thinking…" });
  for (const t of ["The body is Arial already; ", "the headers carry Times New Roman 9 pt. ", "Whether the change covers them is the user's call."]) {
    if (s.job.stop) break;
    await s.emit({ type: "thinking_delta", text: t });
  }
  const reply =
    "I read the three documents and the template. All three use Arial already in the body, but the headers and footers carry Times New Roman at 9 pt, and the footer line reads “Riverside Medical Center — Issued for Bid”.\n\nBefore I plan this, one thing to clarify.";
  await s.text(reply);
  if (s.job.stop) {
    view.messages.push(msg("assistant", "Stopped. No AI step runs until you continue; your progress is saved."));
    view.stage = "question";
    return s.end();
  }
  const m = msg("assistant", reply);
  view.messages.push(m);
  await s.emit({ type: "message", message: m });
  const q = SCENARIOS.question();
  view.stage = "question";
  view.questions = q.questions;
  view.choices = q.choices;
  view.chips = q.chips;
  await s.emit({ type: "question", questions: view.questions, choices: view.choices, chips: view.chips });
  await s.emit({ type: "cost", cost: spend(0.07, 2) });
  s.end();
}

async function planJob(res, userText, layouts = false) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  view.questions = [];
  view.choices = [];
  view.chips = [];
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.blocking = false;
  view.action = { label: "", token: "", kind: "none" };
  await s.emit({ type: "status", kind: "reading", text: "" });
  await s.emit({ type: "activity", text: "Reading the documents' styles and numbering" });
  await s.emit({ type: "status", kind: "planning", text: "" });
  await s.emit({ type: "activity", text: "Checking the plan on each document" });
  if (s.job.stop) {
    view.messages.push(msg("assistant", "Stopped. No AI step runs until you continue; your progress is saved."));
    return s.end();
  }
  const reply =
    "Here is the plan. Three changes, the same on every document: the fonts, the footer line, and the new logo from the picture you attached. Two documents are rendered as samples; the second grows by one page because its last article now wraps.\n\nIf the pages look right, create the edited copies.";
  await s.text(reply);
  const m = msg("assistant", reply);
  view.messages.push(m);
  await s.emit({ type: "message", message: m });
  view.plan = layouts ? PLAN_TWO_LAYOUTS : PLAN;
  await s.emit({ type: "plan", plan: view.plan });
  await s.emit({ type: "status", kind: "previewing", text: "" });
  await s.emit({ type: "status", kind: "rendering", text: "" });
  await s.emit({ type: "status", kind: "looking", text: "" });
  await s.emit({ type: "activity", text: "Looking at the rendered pages of 2 samples" });
  if (s.job.stop) {
    view.messages.push(msg("assistant", "Stopped before the preview finished; your progress is saved."));
    return s.end();
  }
  view.samples = samples();
  view.warnings = WARNINGS;
  view.blocking = false;
  await s.emit({ type: "samples", samples: view.samples, warnings: view.warnings, blocking: view.blocking });
  view.stage = "preview";
  view.action = approveAction(view, `tok-approve-${++seq}`);
  await s.emit({ type: "cost", cost: spend(0.14, 3) });
  s.end();
}

/** A Safe-mode button: no AI call, so no words stream — the checks, the plan and the pages, then the one button.
 *  The last document is set aside with a reason, as the real engine does for one it cannot change exactly. */
/** Stop in Safe mode (src/spec_surgeon/document_task.py, `_stopped`): a preview being made is withdrawn and the window
 *  shows what it showed before the button; nothing is created. */
function safeStopped(s) {
  const before = beforeButton ?? { results: view.results, left_untouched: view.left_untouched };
  beforeButton = null;
  view.results = before.results;
  view.left_untouched = before.left_untouched;
  view.plan = null;
  view.samples = [];
  view.occurrences = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.stage = view.results.length ? "done" : "empty";
  view.action = { label: "", token: "", kind: "none", can_hold: false };
  view.messages.push(msg("app", PREVIEW_STOPPED));
  return s.end();
}

async function recipeJob(res, recipe, inputs = {}) {
  const s = startStream(res);
  lastInputs = inputs;
  lastRecipeRun = null;
  view.messages.push(msg("user", recipe.label));
  if (!beforeButton) beforeButton = { results: view.results, left_untouched: view.left_untouched };
  lastRequest = recipe.label;
  lastRecipe = recipe.id;
  view.results = [];
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.left_untouched = [];
  view.action = { label: "", token: "", kind: "none" };
  await s.emit({ type: "status", kind: "checking", text: "Checking the documents…" });
  await s.emit({ type: "status", kind: "previewing", text: "" });
  const docs = view.attachments.filter((a) => a.kind === "document" && a.state !== "refused");
  const aside = docs.length > 1 ? docs[docs.length - 1] : null;
  view.plan = {
    groups: [{ id: "", label: "", count: docs.length, files: [], changes: [{ title: recipe.label, summary: recipe.summary, details: [] }], assumptions: [] }],
    targets: [],
    set_aside: aside ? [{ name: aside.name, reason: "Editing is restricted in this file (Review → Restrict Editing); remove the restriction in Word, then run again." }] : [],
    stages: 1,
  };
  await s.emit({ type: "plan", plan: view.plan });
  // the pages render one sample at a time: room for Stop now to land (tools/flows.mjs)
  for (let i = 0; i < 25 && !s.job.stop; i++) await s.emit({ type: "status", kind: "rendering", text: "" });
  if (s.job.stop) return safeStopped(s);
  view.samples = samples();
  view.occurrences = recipe.id === "find_replace" ? occurrences() : [];
  await s.emit({ type: "samples", samples: view.samples, occurrences: view.occurrences, warnings: [], blocking: false });
  view.stage = "preview";
  view.action = approveAction(view, `tok-approve-${++seq}`);
  view.working = { ...view.working, ...undoFields() };
  s.end();
}

/** A saved recipe: its kept steps as one plan, one preview (src/spec_surgeon/document_task.py, `_saved_recipe`). */
async function savedRecipeJob(res, record, steps) {
  const s = startStream(res);
  const request = `Run the recipe “${record.name}”`;
  view.messages.push(msg("user", request));
  view.messages.push(msg("app", `Its ${steps.length} step${steps.length === 1 ? "" : "s"}, in order, planned without AI: ${steps.map((st, i) => `${i + 1}. ${st.text}`).join("; ")}.`));
  if (view.mode === "safe" && !beforeButton) beforeButton = { results: view.results, left_untouched: view.left_untouched };
  lastRequest = request;
  lastRecipe = "";
  lastRecipeRun = { id: record.id, name: record.name, steps };
  view.results = [];
  view.samples = [];
  view.left_untouched = [];
  await s.emit({ type: "status", kind: "checking", text: "Checking the documents…" });
  const docs = view.attachments.filter((a) => a.kind === "document" && a.state !== "refused");
  view.plan = {
    groups: [{ id: "", label: "", count: docs.length, files: [], changes: steps.map((st) => ({ title: st.label, summary: st.text, details: [] })), assumptions: [] }],
    targets: [], set_aside: [], stages: steps.length,
  };
  await s.emit({ type: "plan", plan: view.plan });
  view.samples = samples();
  await s.emit({ type: "samples", samples: view.samples, occurrences: [], warnings: [], blocking: false });
  view.stage = "preview";
  view.action = approveAction(view, `tok-approve-${++seq}`);
  view.working = { ...view.working, ...undoFields() };
  const saved = savedRecipes.find((r) => r.id === record.id);
  if (saved) Object.assign(saved, { last_run: new Date().toISOString(), last_documents: docs.length });
  s.end();
}

/** “Work on the documents I set aside”: the pass over the to-do list begins — those documents alone, the request
 *  they were set aside from, Continue to plan them again as before or a message saying what should differ. */
async function todoJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  const owed = owedOf(view);
  const pass = owed.filter((item) => item.operation === owed[0]?.operation);
  const names = pass.map((item) => item.name);
  view.focus = pass.map((item) => item.path);
  view.todo = view.todo.filter((t) => !view.focus.includes(t.path));
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  const several = names.length > 1;
  const note = msg(
    "app",
    (several ? `Working on the ${names.length} documents you set aside: ${names.join(", ")}. ` : `Working on ${names[0]}, which you set aside. `) +
      `Tell me what should be different for ${several ? "them" : "it"}, or continue to plan ${several ? "them" : "it"} again as before. The other copies stay as they are.`,
  );
  view.messages.push(note);
  await s.emit({ type: "message", message: note });
  view.stage = "resume";
  view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
  s.end();
}

async function failJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  await s.emit({ type: "status", kind: "reading", text: "" });
  await s.emit({ type: "status", kind: "planning", text: "" });
  await s.text("I started working out the plan, but ");
  await s.emit({ type: "cost", cost: spend(0.03, 1) });
  // Nothing of this job is kept: the server rolled back; the view is as before the send.
  view.messages.pop();
  s.fail("The AI service could not be reached after three attempts. Nothing was changed; you can send the request again.", userText);
}

async function pauseJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  await s.emit({ type: "status", kind: "planning", text: "" });
  await s.emit({ type: "activity", text: "Checking the files that still need work" });
  await s.emit({ type: "status", kind: "recovering", text: "" });
  view.stage = "budget";
  view.budget = { paused: true, reason: "limit" };
  view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
  await s.emit({ type: "cost", cost: spend(17.4, 38) });
  s.end();
}

/** The unticks taken into the waiting plan: the list marked, the pages made again, a new token. */
async function occurrencesJob(res, skip) {
  const s = startStream(res);
  if (view.stage !== "preview" || (view.occurrences ?? []).length === 0) {
    view.messages.push(msg("app", "There is no preview to update."));
    s.end();
    return;
  }
  const known = new Set(view.occurrences.flatMap((d) => d.items.map((i) => i.key)));
  if (!skip.every((k) => known.has(k))) {
    view.messages.push(msg("app", "An occurrence to leave is not in the preview's list; look at the list again."));
    s.end();
    return;
  }
  await s.emit({ type: "status", kind: "checking", text: "Checking the documents…" });
  const n = skip.length;
  view.messages.push(msg("app", n ? `${n} occurrence${n === 1 ? "" : "s"} will be left as ${n === 1 ? "it is" : "they are"}; the preview is being made again.` : "Every occurrence is back in; the preview is being made again."));
  view.samples = [];
  view.worth_knowing = [];
  await s.emit({ type: "plan", plan: view.plan });
  await s.emit({ type: "status", kind: "rendering", text: "" });
  view.samples = samples();
  view.occurrences = occurrences(skip);
  await s.emit({ type: "samples", samples: view.samples, occurrences: view.occurrences, warnings: [], blocking: false });
  view.action = { ...view.action, token: `tok-approve-${++seq}` };
  s.end();
}

// --- Obliterate's own jobs: no AI call in any of them ---------------------------------------------------------------

const CHECK_NAMES = { V1: "Package hash whitelist", V2: "Text preservation" };   // a failed check's mark: "V2 (Text preservation)"
const quoted = (words) => words.map((w) => `“${w}”`).join(", ");
/** One entry of `text_changes` as the chat says it. */
function changePhrase(entry) {
  const said = [entry.removed.length ? `words removed ${quoted(entry.removed)}` : "", entry.added.length ? `words added ${quoted(entry.added)}` : ""].filter(Boolean);
  return said.length ? said.join("; ") : "only the spacing changes, no words";
}
/** What the chat says before the approval button of an own plan (preview.py): the words its copies change as the
 *  independent checker read them, what else they change, Word's own account, and the documents without a copy. */
function ownLists(shown, heads) {
  const out = [];
  const read = shown.filter((sv) => !sv.no_copy && Array.isArray(sv.text_changes));
  const words = read.flatMap((sv) => sv.text_changes.map((entry) => `• ${sv.name}, ${entry.where}: ${changePhrase(entry)}`));
  if (read.length) out.push(words.length ? [heads.words, ...words].join("\n\n") : heads.none);
  const other = shown.flatMap((sv) => (sv.changes ?? []).map((line) => `• ${sv.name}: ${line}`));
  if (other.length) out.push([heads.other, ...other].join("\n\n"));
  const unread = shown.filter((sv) => sv.raw_xml && sv.text_changes === null);
  if (unread.length) out.push(RAW_UNREAD(listed(unread.map((sv) => sv.name))));
  const account = shown.filter((sv) => !sv.no_copy && (sv.word_account ?? []).length > 0);
  if (account.length) out.push([WORD_ACCOUNT_HEAD, ...account.map((sv) => `• ${sv.name}: ${sv.word_account.join("; ")}.`)].join("\n\n"));
  const none = shown.filter((sv) => sv.no_copy);
  if (none.length) out.push([NO_COPY_HEAD, ...none.map((sv) => `• ${sv.name}: ${sv.failures[0].reason.replace(/\.$/, "")}.`)].join("\n\n"));
  return out;
}

/** An own plan's preview on screen: the plan and its pages, the app's lists of what the copies change, then the one
 *  button. `shown`: its samples, every document it is for; `apart`: the documents it leaves out, each with why. */
async function showOwnPlan(s, { source, request, title, summary, shown, apart, heads, why_not, replace }) {
  const say = async (text) => {
    const m = msg("app", text);
    view.messages.push(m);
    await s.emit({ type: "message", message: m });
  };
  view.plan = {
    groups: [{ id: "", label: "", count: shown.filter((sv) => !sv.no_copy).length, files: [], changes: [{ title, summary, details: [] }], assumptions: [] }],
    targets: [],
    set_aside: Object.entries(apart).map(([name, reason]) => ({ name, reason })),
    stages: 1,
  };
  await s.emit({ type: "plan", plan: view.plan });
  view.samples = shown;
  view.occurrences = [];
  view.warnings = [];
  view.worth_knowing = [];
  view.blocking = false;
  await s.emit({ type: "samples", samples: view.samples, occurrences: [], warnings: [], worth_knowing: [], blocking: false });
  for (const text of ownLists(shown, heads)) await say(text);
  ownPlan = {
    source, why_not, replace, apart,
    sentences: [summary],
    deliver: shown.filter((sv) => !sv.no_copy).map((sv) => ({ path: sv.source, marks: [...(sv.unproven ? [WORD_UNPROVEN_MARK] : []), ...sv.failures.map((f) => `${f.check} (${CHECK_NAMES[f.check] ?? f.check})`)] })),
    failed: shown.filter((sv) => sv.no_copy).map((sv) => ({ path: sv.source, name: sv.name, reason: sv.failures[0].reason, why: "failed" })),
  };
  lastRequest = request;
  lastRecipe = "";
  lastRecipeRun = null;
  view.stage = "preview";
  view.action = approveAction(view, `tok-approve-${++seq}`);
  view.working = { ...view.working, ...undoFields() };
}

/** What every own job starts by clearing: the question and the pages of whatever was on screen. */
function clearForOwnPlan() {
  view.questions = [];
  view.choices = [];
  view.chips = [];
  view.plan = null;
  view.samples = [];
  view.worth_knowing = [];
  view.warnings = [];
  view.blocking = false;
  view.action = { label: "", token: "", kind: "none", can_hold: false };
}

/** Replace this part (document_task._replace_part): the one-operation plan the window builds, previewed. The copy is
 *  proven to hold the file's bytes; what they change in the words is read from the copy and listed. A choice that
 *  cannot be used is said in one message and nothing is previewed. */
async function replacePartJob(res, a, part, file) {
  const s = startStream(res);
  const refuse = (why) => {
    view.messages.push(msg("app", `Replace this part: ${why}`));
    return s.end();
  };
  const blocked = ownPlanBlocked();
  if (blocked) return refuse(blocked);
  if (!documentsInScope().includes(a)) return refuse(`${a.name} is set aside, out of every plan. Put it back, or use Obliterate this one, first.`);
  const text = part.xml ? file.data.toString("utf8") : null;
  if (part.xml ? text === part.text : part.replacedWith === file.name && part.size === file.data.length) {
    return refuse(`The file holds exactly what ${part.name} already holds, so nothing would change. Save your changes to the file, then choose it again.`);
  }
  await s.emit({ type: "status", kind: "checking", text: "Checking the document…" });
  const request = `Replace the part ${part.name} of ${a.name} with my file ${file.name}.`;
  const summary = `Replace the part “${part.name}” with the file “${file.name}”, byte for byte. Not tracked; no other part of the document changes.`;
  view.messages.push(msg("user", request));
  const planned = msg("app", `Planned without AI: ${summary} The copy is checked byte for byte against your file. What the new part changes in the words is not predicted: it is read from the copy and listed below, before the button.`);
  view.messages.push(planned);
  await s.emit({ type: "message", message: planned });
  clearForOwnPlan();
  await s.emit({ type: "status", kind: "previewing", text: "" });
  await s.emit({ type: "status", kind: "rendering", text: "" });
  // a part that is no longer well-formed XML is placed as it was given: nothing of it can be read, and V1 fails
  const problem = part.xml ? xmlProblem(text) : "";
  const sample = {
    source: a.path, name: a.name, images: problem ? [] : [`/api/image?path=${encodeURIComponent(`${RUN_DIR}/preview/1.png`)}`],
    pages_before: problem ? null : 12, pages_after: problem ? null : 12,
    passed: !problem,
    failures: problem ? [{ check: "V1", reason: `The copy's part ${part.name} is not well-formed XML (${problem}), so Word would not open the file.` }] : [],
    geometry: { status: "NA", note: "No page-layout comparison for this change: a replaced part can move or change what the pages show." },
    text_changes: problem ? null : part.xml ? partTextChanges(part.name, part.text, text) : [],
    unproven: false,
    changes: [part.xml
      ? `Parts: ${part.name} is your file now, ${file.data.length.toLocaleString("en-US")} bytes where there were ${part.size.toLocaleString("en-US")}; no other part changed.`
      : `Parts: ${part.name} is your file now, ${file.data.length.toLocaleString("en-US")} bytes where there were ${part.size.toLocaleString("en-US")}; it is not text, so it was compared by its bytes only.`],
    no_copy: false, raw_xml: true, raw_what: "part", made_by: "", word_account: [],
  };
  await showOwnPlan(s, {
    source: "hands-on", request, title: "Replace a part", summary, shown: [sample],
    apart: Object.fromEntries(documentsInScope().filter((x) => x !== a).map((x) => [x.name, PART_ONLY_THERE(a.name)])),
    heads: { words: PART_WORDS_CHANGE_HEAD, none: PART_NO_WORDS_CHANGE, other: PART_OTHER_CHANGES_HEAD },
    why_not: WHY_PART,
    replace: { key: `${a.path}\t${part.name}`, part: { name: part.name, type: part.type, xml: part.xml, text, size: file.data.length, replacedWith: file.name } },
  });
  s.end();
}

/** Word actions (document_task._word_actions): Word makes each planned document's copy now, and the copies are
 *  previewed, each read by the independent reader and none proven. A document Word finds nothing to do in gets no
 *  copy and the reason; no copy at all, and nothing is previewed. Stop ends it between documents. */
async function wordActionsJob(res, a, actions, every) {
  const s = startStream(res);
  const say = async (text) => {
    const m = msg("app", text);
    view.messages.push(m);
    await s.emit({ type: "message", message: m });
  };
  const blocked = ownPlanBlocked() || (wordAvailable ? "" : NO_WORD);
  if (blocked) {
    view.messages.push(msg("app", `${WORD_LABEL}: ${blocked}`));
    return s.end();
  }
  const scope = documentsInScope();
  if (!scope.includes(a)) {
    view.messages.push(msg("app", view.focus.length
      ? `${WORD_LABEL}: a pass over your to-do list is open on ${view.focus.map(name).join(", ")}, and ${a.name} is not part of it. Finish the pass or drop it first.`
      : `${WORD_LABEL}: ${a.name} is set aside, out of every plan. Put it back, or use Obliterate this one, first.`));
    return s.end();
  }
  const several = every && scope.length > 1;
  const targets = several ? scope : [a];
  const request = `Run in Word on ${several ? `every document (${scope.length})` : a.name}: ${actions.map((id) => WORD_ACTIONS[id].label).join("; then ")}.`;
  await s.emit({ type: "status", kind: "checking", text: "Word is working on a copy…" });
  view.messages.push(msg("user", request));
  await say(`Planned without AI. ${actions.map((id) => `Word action, ${WORD_ACTIONS[id].label}: ${WORD_ACTIONS[id].does}`).join(" ")} Word saves the whole file again, so nothing is predicted: Word makes each copy now, on a private copy of the document, and what it changes is read from that copy and listed below, before the button. Up to ${WORD_MINUTES} minutes a document.`);
  clearForOwnPlan();
  const shown = [];
  for (const doc of targets) {
    // Word takes its time over each document: room for Stop now to land
    for (let i = 0; i < 8 && !s.job.stop; i++) await s.emit({ type: "status", kind: "previewing", text: `Word is working on a copy of ${doc.name}…` });
    if (s.job.stop) {
      view.stage = restingStage();
      view.messages.push(msg("app", PREVIEW_STOPPED));
      return s.end();
    }
    shown.push(wordSample(doc, actions, shown.length));
  }
  if (shown.every((sv) => sv.no_copy)) {
    view.stage = restingStage();
    await say(`Word made no copy, so there is nothing to preview or create: ${shown.map((sv) => `${sv.name} — ${sv.failures[0].reason.replace(/\.$/, "")}`).join("; ")}.`);
    return s.end();
  }
  await showOwnPlan(s, {
    source: "word", request, title: WORD_LABEL,
    summary: `${actions.map((id) => WORD_ACTIONS[id].does).join(" ")} Not tracked. Word saves the whole file again, so nothing is predicted.`,
    shown,
    apart: several ? {} : Object.fromEntries(scope.filter((x) => x !== a).map((x) => [x.name, WORD_ONLY_THERE(a.name)])),
    heads: { words: WORD_WORDS_CHANGE_HEAD, none: NO_WORDS_CHANGE, other: WORD_OTHER_CHANGES_HEAD },
    why_not: WHY_WORD,
  });
  s.end();
}

/** Try to rescue (document_task._rescue): the one thing that could make a refused document a file the engine takes.
 *  What comes of it is attached in the refused file's place, marked SALVAGED; a rescue that makes no document leaves
 *  the file refused and says why. The password is read here and goes nowhere: no message or answer carries it. */
async function rescueJob(res, a, password) {
  const s = startStream(res);
  const kind = rescueKind(a);
  const end = (text) => {
    view.messages.push(msg("app", text));
    return s.end();
  };
  if (kind === "password" && !password) {
    return end(`${RESCUE_LABEL}: ${a.name} is protected with a password. Press ${RESCUE_LABEL} again and type it: it is given to Word for this one attempt and kept nowhere.`);
  }
  for (let i = 0; i < 8 && !s.job.stop; i++) await s.emit({ type: "status", kind: "checking", text: "Trying to rescue the file…" });
  if (s.job.stop) return end(RESCUE_STOPPED);
  if (RESCUE_WORD_KINDS.includes(kind) && !wordAvailable) return end(`${RESCUE_LABEL}: ${a.name} was not rescued. ${NO_WORD.replace(/\.$/, "")}. The file is as it was.`);
  if (kind === "password" && password.toLowerCase().startsWith("wrong")) {
    return end(`${RESCUE_LABEL}: Word says that is not the password of ${a.name}. Nothing was rescued; press it again to try another.`);
  }
  const rescued = `${a.name.replace(/\.[^.]+$/, "")}.docx`;
  const steps = [RESCUE_STEPS[kind](a)];
  const notes = [...(kind === "password" ? [NO_PASSWORD_NOW] : []), ...(/\.docm$/i.test(a.name) ? [NO_MACROS] : [])];
  const fresh = { ...attachment(`${RESCUED_ROOT}/20261110-1015${String(rescues++ % 60).padStart(2, "0")}/${rescued}`, "document"), salvaged: { from: a.name, kind, steps, notes } };
  view.attachments = view.attachments.map((x) => (x === a ? fresh : x));   // in the refused file's own place
  voidPlan();
  end(`Rescued ${a.name}. ${steps.join(" ")} It is attached as ${rescued}, marked SALVAGED; your file was read and not changed. It holds 212 paragraphs, 3 tables and 1 picture or drawn object in its body, with 1 header part and 1 footer part. ${RESCUE_UNPROVEN}${notes.map((note) => ` ${note}`).join("")}`);
}

/** What the chat adds after a clean export that left marked copies without a file, in Obliterate mode
 *  (document_task._anyway_offer): Export can have Word make them anyway, and where. */
function anywayOffer(kind) {
  const names = anywayCopies(view, kind).map((r) => r.name);
  if (!names.length) return "";
  const one = names.length === 1;
  return ` ${listed(names)} ${one ? "is a marked copy" : "are marked copies"}, so in Obliterate mode Export can have Word make ${one ? "it" : "them"} anyway, unchecked: Export → “${ANYWAY_LABELS[kind]}”.` +
    (wordAvailable ? "" : " That needs Microsoft Word, which the app cannot reach on this computer.");
}

/** Export anyway (document_task.export with `anyway`): the clean kind made by Word for the marked copies that kind's
 *  checked export left without a file. Nothing is planned or checked, so the chat names each file UNCHECKED, with
 *  Word's own count and what the app read in the file Word saved. Stop ends it with nothing saved. */
async function exportAnywayJob(res, kind, folder) {
  const s = startStream(res);
  const copies = anywayCopies(view, kind);
  for (let i = 0; i < 8 && !s.job.stop; i++) await s.emit({ type: "status", kind: "creating", text: "Word is making the clean copies…" });
  if (s.job.stop) {
    view.messages.push(msg("app", EXPORT_STOPPED));
    return s.end();
  }
  if (kind === "pdf") await s.emit({ type: "status", kind: "rendering", text: "Saving the clean PDFs…" });
  const one = copies.length === 1;
  const each = `a private copy of ${one ? "it" : "each"}`;
  const what = kind === "accepted" ? `${copies.length} Word cop${one ? "y" : "ies"} with every tracked change accepted` : `${copies.length} clean PDF${one ? "" : "s"}`;
  const how = kind === "accepted"
    ? `Word accepted every tracked change on ${each} and saved that`
    : `Word deleted every comment and accepted every tracked change on ${each}, and the PDF${one ? " was" : "s were"} made from that by Microsoft Word`;
  let said = `Saved ${what} to ${folder}, made by Word and UNCHECKED: the checked way could not make ${one ? "it" : "them"}, so ${how}. No check proves ${one ? "the file is" : "the files are"} your edited cop${one ? "y" : "ies"} with nothing else changed.`;
  for (const r of copies) {
    const doc = view.attachments.find((a) => a.path === r.source) ?? { path: r.source, notes: [] };
    const account = wordAccount(doc, kind === "pdf" ? ["delete_comments", "accept_changes"] : ["accept_changes"]);
    said += ` ${kind === "pdf" ? r.name.replace(/\.docx$/i, ".pdf") : r.name} — Word's own count: ${account.join("; ")}. The app read the file Word saved: its words are your edited copy's with every tracked change accepted, and no tracked change ${kind === "pdf" ? "or comment " : ""}is left.`;
    exportMissed[kind].delete(r.output);   // made: no longer offered
  }
  view.messages.push(msg("app", `${said} Your edited copies are as they were.`));
  view.export = { ...view.export, folder };
  s.end();
}

async function primaryJob(res, token, hold = []) {
  const s = startStream(res);
  if (view.action.kind === "continue") {
    view.budget = { paused: false, reason: "" };
    await s.emit({ type: "status", kind: "resuming", text: "" });
    const limit = settings.spending_limit_usd;
    const note = msg(
      "app",
      typeof limit === "number"
        ? `Continuing: this request may spend up to $${limit.toFixed(2)} more before the spending check asks again.`
        : "Continuing without a spending check for this request: it is off in Settings.",
    );
    view.messages.push(note);
    await s.emit({ type: "message", message: note });
    view.stage = view.plan ? "preview" : "empty";
    view.action = view.plan
      ? approveAction(view, `tok-approve-${++seq}`)
      : { label: "", token: "", kind: "none", can_hold: false };
    await s.emit({ type: "cost", cost: spend(0.02, 1) });
    return s.end();
  }
  // The documents marked with the red X: on the to-do list, no copy; the others run. The pass over the to-do list
  // (`focus`) ends with its copies. A plan of the app's own (Word actions, Replace this part) runs on the documents
  // it showed a copy of, and Create delivers those copies.
  const own = tutorial ? null : ownPlan;
  const inScope = documentsInScope();
  const held = inScope.filter((a) => hold.includes(a.current) || hold.includes(a.path));
  const running = inScope.filter((a) => !held.includes(a) && (!own || own.deliver.some((d) => d.path === a.path)));
  const copyOf = (a) => {
    const marks = own?.deliver.find((d) => d.path === a.path)?.marks ?? [];
    return { source: a.path, output: `${RUN_DIR}/${a.name}`, name: a.name, saved_to: "", ...(marks.length ? { unverified: marks } : {}) };
  };
  const todoBefore = [...view.todo];
  for (const a of held) {
    if (!view.todo.some((t) => t.path === a.path)) view.todo.push({ path: a.path, name: a.name, why: "held", reason: HELD_REASON });
  }
  view.focus = [];
  if (running.length === 0) {
    const note = msg("app", "You set every document aside, so no copy was created. Tell me what should be different, and I’ll plan them again.");
    view.messages.push(note);
    await s.emit({ type: "message", message: note });
    view.plan = null;
    view.samples = [];
    view.worth_knowing = [];
    view.warnings = [];
    view.focus = view.todo.map((item) => item.path);
    view.todo = [];
    view.stage = "resume";
    view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
    if (own) ownPlan = null;
    return s.end();
  }
  await s.emit({ type: "status", kind: "creating", text: "" });
  await s.emit({ type: "status", kind: "checking", text: "" });
  await s.emit({ type: "activity", text: "Verifying each edited copy against the plan" });
  if (s.job.stop && view.mode === "safe") {
    // Safe mode's kill switch ends the run itself: no copy, the preview waits again, the X'd documents back off the list
    view.todo = todoBefore;
    view.messages.push(msg("app", "Stopped. No copy was created: copies reach your documents only once every one is made and checked, and none of this run's did. The preview is still waiting; create the copies again, or discard it."));
    return s.end();
  }
  if (s.job.stop) {
    view.messages.push(msg("app", "Stopped. The copies that were already verified are kept."));
    return s.end();
  }
  // A button's run: the documents its plan set aside, found by name among the attachments (the view names them only).
  const asideByButton = view.mode === "safe"
    ? (view.plan?.set_aside ?? []).flatMap((x) => {
        const a = view.attachments.find((d) => d.kind === "document" && d.name === x.name);
        return a ? [{ path: a.path, name: x.name, reason: x.reason }] : [];
      })
    : [];
  const fresh = running.filter((a) => !asideByButton.some((x) => x.path === a.path)).map(copyOf);
  // a copy made again is no longer the one a clean export left without a file: Export anyway is offered for none of them
  for (const copy of fresh) for (const missed of Object.values(exportMissed)) missed.delete(copy.output);
  if (view.mode === "safe") {
    // A button's run: the app's own note, no AI words; what it left untouched waits beside the copies. What it
    // replaces is kept for Undo.
    undoStack.push({
      request: lastRequest || "the last button",
      generation: view.working.generation,
      results: beforeButton ? beforeButton.results : view.results,
      left_untouched: beforeButton ? beforeButton.left_untouched : view.left_untouched,
      currents: view.attachments.map((a) => [a.path, a.current]),
      done: view.working.done ?? [],
      replaced: [...replacedParts],
    });
    const doneSoFar = doneWith(lastRequest || "the last button", fresh, asideByButton);
    beforeButton = null;
    view.results = [...view.results.filter((r) => !fresh.some((f) => f.source === r.source)), ...fresh];
    await s.emit({ type: "results", results: view.results });
    view.left_untouched = asideByButton.map((x) => ({ path: x.path, name: x.name, reason: x.reason }));
    const left = view.left_untouched.length ? ` Left untouched: ${view.left_untouched.map((x) => x.name).join(", ")} — each one's reason is beside the copies.` : "";
    const done = msg("app", `${fresh.length} document ${fresh.length === 1 ? "copy is" : "copies are"} ready. Open them, press another button to change them further, or start over from the originals.${left}`);
    view.messages.push(done);
    await s.emit({ type: "message", message: done });
    view.stage = "done";
    view.plan = null;
    view.samples = [];
    view.worth_knowing = [];
    view.action = { label: "", token: "", kind: "none", can_hold: false };
    view.working = { generation: view.working.generation + 1, from_originals: false, can_start_over: true, done: doneSoFar };
    view.attachments = view.attachments.map((a) =>
      running.some((r) => r.path === a.path) ? { ...a, current: `${RUN_DIR}/${a.name}` } : a,
    );
    view.working = { ...view.working, ...undoFields() };
    lastRecipe = "";
    return s.end();
  }
  // A conversation's run: what it replaces is kept for Undo, as a button's is.
  undoStack.push({
    request: lastRequest || "the last change",
    generation: view.working.generation,
    results: view.results,
    left_untouched: view.left_untouched,
    currents: view.attachments.map((a) => [a.path, a.current]),
    done: view.working.done ?? [],
    replaced: [...replacedParts],
  });
  if (own) {
    // One of Obliterate's own actions: the copies shown are delivered as they are, with their marks; no AI words follow.
    // A document Word could make no copy of owes the action (a failed cell); one it was not asked about does not.
    ownPlan = null;
    const doneSoFar = doneWith(lastRequest || "the last change", fresh, own.failed, own);
    if (own.replace && fresh.length) replacedParts.set(own.replace.key, own.replace.part);
    view.results = [...view.results.filter((r) => !fresh.some((f) => f.source === r.source)), ...fresh];
    await s.emit({ type: "results", results: view.results });
    const n = fresh.length;
    const unverified = fresh.filter((r) => failedMarks(r).length > 0).length;
    const done = msg("app", `${n} document ${n === 1 ? "copy is" : "copies are"} ready. You can open ${n === 1 ? "it" : "them"}, describe another change to make on top of ${n === 1 ? "it" : "them"}, or start over from the originals.` +
      (own.source === "word" ? ` ${n === 1 ? "It is" : "Each is"} marked UNPROVEN: Word made it, and what it changed was shown to you, not proven.` : "") +
      (unverified ? ` ${unverified} ${unverified === 1 ? "was" : "were"} delivered UNVERIFIED: a check failed, and each one's failed checks are beside it.` : "") +
      (own.failed.length ? ` No copy was made of ${listed(own.failed.map((x) => x.name))}; ${own.failed.length === 1 ? "its reason is" : "each one's reason is"} in Where each document stands.` : ""));
    view.messages.push(done);
    await s.emit({ type: "message", message: done });
    view.stage = "done";
    view.plan = null;
    view.samples = [];
    view.worth_knowing = [];
    view.action = { label: "", token: "", kind: "none", can_hold: false };
    view.working = { generation: view.working.generation + 1, from_originals: false, can_start_over: true, done: doneSoFar };
    view.attachments = view.attachments.map((a) => (running.some((r) => r.path === a.path) ? { ...a, current: `${RUN_DIR}/${a.name}` } : a));
    view.working = { ...view.working, ...undoFields() };
    return s.end();
  }
  const doneSoFar = doneWith(lastRequest || "the last change", fresh, []);
  view.results = [...view.results.filter((r) => !fresh.some((f) => f.source === r.source)), ...fresh];
  await s.emit({ type: "results", results: view.results });
  const note = msg("app", `${fresh.length} edited ${fresh.length === 1 ? "copy" : "copies"} created and verified`);
  view.messages.push(note);
  await s.emit({ type: "message", message: note });
  const reply = held.length
    ? `Done. The ${fresh.length === 1 ? "copy" : `${fresh.length} copies`} passed every check; your originals were not touched. The ${held.length === 1 ? "document" : "documents"} you set aside got no copy yet.`
    : "Done. All three copies passed every check and are in the run folder; your originals were not touched. Your next request builds on these copies.";
  await s.text(reply);
  const m = msg("assistant", reply);
  view.messages.push(m);
  await s.emit({ type: "message", message: m });
  if (view.todo.length) {
    const names = view.todo.map((item) => item.name).join(", ");
    const several = view.todo.length > 1;
    const offer = msg("app", `On your to-do list: ${names}. When you’re ready to work on ${several ? "them" : "it"}, say “${WORK_TODO}” and tell me what should be different.`);
    view.messages.push(offer);
    await s.emit({ type: "message", message: offer });
  }
  view.stage = "done";
  view.action = { label: "", token: "", kind: "none", can_hold: false };
  view.working = { generation: view.working.generation + 1, from_originals: false, can_start_over: true, done: doneSoFar };
  view.attachments = view.attachments.map((a) =>
    running.some((r) => r.path === a.path) ? { ...a, current: `${RUN_DIR}/${a.name}` } : a,
  );
  view.working = { ...view.working, ...undoFields() };
  await s.emit({ type: "cost", cost: spend(0.12, 2) });
  void token;
  s.end();
}

/** Obliterate mode alone takes an old .doc or a .docm as a document: refused as triage refuses it, to be rescued. */
const rescuable = (path, template) => view.mode === "obliterate" && !template && /\.(doc|docm)$/i.test(path);
const notDocx = (path) => `${name(path)} is not a .docx file: .docm and .doc files are not supported; save it as .docx in Word.`;

/** Adding or removing a document after a plan exists voids that plan: the
 *  button reads Continue until the documents are checked again. */
function voidPlan() {
  if (ownPlan && !tutorial) return withdrawOwn();
  if (!view.plan) return;
  view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
  view.messages.push(msg("app", "The documents changed. Review the updated plan first."));
}

// --- Multipart (just enough for the upload route) -------------------------

function parseMultipart(body, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType || "");
  const boundary = m ? m[1] || m[2] : null;
  const files = [];
  const fields = {};    // every plain field, by name
  const uploads = [];   // every file: the field it came in, its name and its bytes
  let template = false;
  if (!boundary) return { files, template, fields, uploads };
  const parts = body.toString("latin1").split(`--${boundary}`);
  for (const part of parts) {
    const headerEnd = part.indexOf("\r\n\r\n");
    if (headerEnd === -1) continue;
    const headers = part.slice(0, headerEnd);
    const value = part.slice(headerEnd + 4).replace(/\r\n$/, "");
    const fname = /filename="([^"]*)"/i.exec(headers);
    const field = /name="([^"]*)"/i.exec(headers);
    if (fname) {
      files.push(Buffer.from(fname[1], "latin1").toString("utf8"));
      uploads.push({ field: field ? field[1] : "", name: Buffer.from(fname[1], "latin1").toString("utf8"), data: Buffer.from(value, "latin1") });
    } else if (field) {
      fields[field[1]] = Buffer.from(value, "latin1").toString("utf8");
      if (field[1] === "template") template = value.trim() === "true";
    }
  }
  return { files, template, fields, uploads };
}

// --- Static files ---------------------------------------------------------

const MIME = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".map": "application/json" };

function serveStatic(res, pathname) {
  const rel = pathname === "/" ? "index.html" : pathname.replace(/^\/+/, "");
  const file = resolve(STATIC, rel);
  if (!file.startsWith(STATIC) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end(existsSync(join(STATIC, "index.html")) ? "Not found" : "No built page: run `npm run build` first.");
    return;
  }
  res.writeHead(200, { "Content-Type": MIME[extname(file)] || "application/octet-stream", "Cache-Control": "no-store" });
  res.end(readFileSync(file));
}

// --- Routing --------------------------------------------------------------

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host || "127.0.0.1"}`);
  const path = url.pathname;
  if (req.method === "OPTIONS") return json(res, 204, {});

  if (path.startsWith("/mock/")) {
    const body = await readJson(req);
    if (path === "/mock/reset") {
      view = emptyView();
      resetGoingBack();
      resetObliterate();
      wordAvailable = true;
      running = null;
      return json(res, 200, view);
    }
    if (path === "/mock/scenario") {
      const make = SCENARIOS[body.name];
      if (!make) return json(res, 400, { error: `Unknown scenario; one of ${Object.keys(SCENARIOS).join(", ")}.` });
      settings = { ...settings, key_present: true, origin: "saved" };
      updateFound = true;
      resetObliterate();
      wordAvailable = true;   // the obliterate-no-word scenario turns it off
      view = make();
      running = null;
      resetGoingBack();
      return json(res, 200, view);
    }
    if (path === "/mock/health") return json(res, 200, { ok: true, token: TOKEN });
    if (path === "/mock/page-reports") return json(res, 200, { reports: pageReports });
    return json(res, 404, { error: "No such mock route." });
  }

  if (!path.startsWith("/api")) return serveStatic(res, path);

  if (tokenOf(req, url) !== TOKEN) return json(res, 401, { error: "The page is not connected to its app." });

  const busyRefusal = () => json(res, 409, { error: "busy" });

  switch (`${req.method} ${path}`) {
    case "GET /api/state":
      view.tutorial = tutorialBlock();
      view.safe_note_hidden = settings.safe_note_hidden;
      return json(res, 200, view);
    case "GET /api/tutorial/status":
      return json(res, 200, tutorialBlock());
    case "POST /api/tutorial/start": {
      const body = await readJson(req);
      if (tutorial && tutorial.requestId && tutorial.requestId === body.request_id) return json(res, 200, view);
      if (tutorial) return json(res, 409, { error: "A tour is already running; end it before starting another." });
      if (view.busy) return busyRefusal();
      tutorial = { id: `tour-${Date.now()}`, requestId: body.request_id || "", original: view, scenario: "start" };
      view = tutorialView("start");
      return json(res, 200, view);
    }
    case "POST /api/tutorial/scenario": {
      const body = await readJson(req);
      if (!tutorial || body.tutorial_id !== tutorial.id) return json(res, 409, { error: "That tour has ended." });
      if (view.busy) return busyRefusal();
      if (!TUTORIAL_SCENARIOS[body.kind]) return json(res, 400, { error: `Unknown tour scenario ${body.kind}.` });
      tutorial.scenario = body.kind;
      view = tutorialView(body.kind);
      return json(res, 200, view);
    }
    case "POST /api/tutorial/restore": {
      const body = await readJson(req);
      if (tutorial && body.tutorial_id && body.tutorial_id !== tutorial.id) return json(res, 409, { error: "That tour has ended." });
      if (tutorial) {
        if (view.busy) return busyRefusal();
        view = tutorial.original;
        tutorial = null;
      }
      view.tutorial = tutorialBlock();
      return json(res, 200, view);
    }
    case "POST /api/attach": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const paths = Array.isArray(body.paths) ? body.paths : [];
      for (const p of paths) {
        if (rescuable(p, body.template)) {
          // Obliterate alone: attached refused, with Try to rescue beside it (docs/WEB_API.md, `POST /api/pick`)
          if (!view.attachments.some((a) => a.path === p)) view.attachments.push(attachment(p, "document", { reason: notDocx(p) }));
          continue;
        }
        if (!/\.(docx|png|jpe?g|gif|xlsx|csv|json)$/i.test(p)) return json(res, 400, { error: `${name(p)} is not a .docx; save it as a Word document first.` });
        const kind = body.template ? "template" : /\.docx$/i.test(p) ? "document" : "reference";
        if (kind === "template") {
          view.attachments = view.attachments.filter((a) => a.kind !== "template");
          view.template = TEMPLATE_INFO;
        }
        if (!view.attachments.some((a) => a.path === p)) view.attachments.push(attachment(p, kind, kind === "reference" ? PICTURE : {}));
      }
      voidPlan();
      return json(res, 200, view);
    }
    case "POST /api/upload": {
      if (view.busy) return busyRefusal();
      const { files, template } = parseMultipart(await readBody(req), req.headers["content-type"]);
      for (const f of files) {
        const kind = template ? "template" : /\.docx$/i.test(f) ? "document" : "reference";
        const p = `C:/Users/abe/AppData/Roaming/Spec Surgeon/attachments/20261020-1500/${f}`;
        if (rescuable(f, template)) {
          view.attachments.push(attachment(p, "document", { reason: notDocx(f) }));
          continue;
        }
        if (kind === "template") view.attachments = view.attachments.filter((a) => a.kind !== "template");
        const extra = /\.docx$/i.test(f) ? (f.toLowerCase().includes("empty") ? REFUSED : {}) : PICTURE;
        if (!/\.(docx|png|jpe?g|gif|xlsx|csv|json)$/i.test(f)) return json(res, 400, { error: `${f} is not a .docx; save it as a Word document first.` });
        view.attachments.push(attachment(p, kind, extra));
      }
      if (files.length) voidPlan();
      return json(res, 200, view);
    }
    case "POST /api/attach-folder": {
      if (view.busy) return busyRefusal();
      if (!process.env.MOCK_PICK) return json(res, 501, { error: "This window has no native folder dialog; drop the files on the page instead." });
      for (const d of DOCS) if (!view.attachments.some((a) => a.path === d.path)) view.attachments.push({ ...d });
      view.messages.push(msg("app", `${DOCS.length} documents added from Specs.`));
      return json(res, 200, view);
    }
    case "POST /api/pick":
      if (process.env.MOCK_PICK) {
        const body = await readJson(req);
        const paths = body.kind === "template" ? [TEMPLATE.path] : body.kind === "references" ? [PICTURE.path] : DOCS.map((d) => d.path);
        return json(res, 200, { paths });
      }
      return json(res, 501, { error: "No native file dialog here." });
    case "POST /api/remove": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      view.attachments = view.attachments.filter((a) => a.path !== body.path);
      voidPlan();
      return json(res, 200, view);
    }
    case "POST /api/remove-documents": {
      if (view.busy) return busyRefusal();
      const removed = view.attachments.filter((a) => a.kind === "document").length;
      view.attachments = view.attachments.filter((a) => a.kind !== "document");
      voidPlan();
      if (removed) view.messages.push(msg("app", `${removed} documents removed from this conversation; the files themselves are untouched.`));
      return json(res, 200, view);
    }
    case "GET /api/recipes":
      return json(res, 200, RECIPES);
    case "POST /api/recipe": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const recipe = RECIPES.recipes.find((r) => r.id === body.recipe);
      if (!recipe) return json(res, 400, { error: "There is no such button." });
      if (view.mode !== "safe") return json(res, 400, { error: "The buttons are Safe mode's; switch to Safe mode to use them." });
      return recipeJob(res, recipe, body.inputs && typeof body.inputs === "object" ? body.inputs : {});
    }
    case "POST /api/saved-recipes/run": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const record = recipesOf(view).find((r) => r.id === body.id);
      if (!record) return json(res, 400, { error: "There is no such recipe; it may have been deleted in another window." });
      if (!record.runs_here) return json(res, 400, { error: `${record.name}: ${record.why_not_here}` });
      const blocked = recipeBlockedOf(view);
      if (blocked) return json(res, 400, { error: blocked });
      if (body.rev && body.rev !== record.rev) return json(res, 400, { error: "The recipe changed since its form was opened; open it again." });
      const picks = Array.isArray(body.steps) ? body.steps : record.steps.map(() => ({ skip: false }));
      if (picks.length !== record.steps.length) return json(res, 400, { error: "The recipe changed since its form was opened; open it again." });
      const kept = record.steps.flatMap((st, i) => (picks[i]?.skip ? [] : [st.kind === "button" && picks[i]?.inputs ? { ...st, inputs: picks[i].inputs } : st]));
      if (!kept.length) return json(res, 400, { error: "Every step is skipped; keep at least one." });
      return savedRecipeJob(res, record, kept);
    }
    case "POST /api/saved-recipes/save": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const name = typeof body.name === "string" ? body.name.trim() : "";
      const target = body.into ? savedRecipes.find((r) => r.id === body.into) : null;
      if (body.into && !target) return json(res, 400, { error: "There is no such recipe; it may have been deleted in another window." });
      if (!name && !target) return json(res, 400, { error: "Type a name for the recipe." });
      const runs = (view.working.done ?? []).filter((d) => Array.isArray(body.runs) && body.runs.includes(d.id));
      if (!runs.length) return json(res, 400, { error: "Tick the changes to save; the list changed since the dialog opened." });
      const steps = runs.flatMap((d) => d.steps ?? []);
      if (target) {
        const before = modeOfSteps(target.steps);
        target.steps = [...target.steps, ...steps];
        const after = modeOfSteps(target.steps);
        const moved = after === before ? "" : after === "default" ? " It is now a Default-mode recipe: it runs in Default and Obliterate mode." : after === "obliterate" ? " It is now an Obliterate recipe: it runs only in Obliterate mode." : "";
        view.messages.push(msg("app", `Added ${steps.length} step${steps.length === 1 ? "" : "s"} to the recipe “${target.name}”: it has ${target.steps.length} now.${moved}`));
        return json(res, 200, view);
      }
      savedRecipes.push({ id: `r-${++seq}`, name, note: typeof body.note === "string" ? body.note.trim() : "", saved: new Date().toISOString(), last_run: "", last_documents: 0, steps });
      view.messages.push(msg("app", `Saved the recipe “${name}”: ${steps.length} step${steps.length === 1 ? "" : "s"}.`));
      return json(res, 200, view);
    }
    case "POST /api/saved-recipes/change": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const i = savedRecipes.findIndex((r) => r.id === body.id);
      if (i < 0) return json(res, 400, { error: "There is no such recipe; it may have been deleted in another window." });
      if (body.action === "rename") {
        if (typeof body.name !== "string" || !body.name.trim()) return json(res, 400, { error: "Type a name for the recipe." });
        savedRecipes[i] = { ...savedRecipes[i], name: body.name.trim(), note: typeof body.note === "string" ? body.note.trim() : savedRecipes[i].note };
      } else if (body.action === "edit") {
        const record = savedRecipes[i];
        if (body.rev !== revOf(record)) return json(res, 400, { error: "The recipe changed since you opened it, perhaps in another window; open it again." });
        if (typeof body.name !== "string" || !body.name.trim()) return json(res, 400, { error: "Type a name for the recipe." });
        const entries = Array.isArray(body.steps) ? body.steps : [];
        if (!entries.length) return json(res, 400, { error: "Keep at least one step, or delete the recipe." });
        const seen = new Set();
        const steps = [];
        for (const entry of entries) {
          const st = record.steps[entry?.step];
          if (!st || seen.has(entry.step)) return json(res, 400, { error: "The recipe's steps could not be read." });
          seen.add(entry.step);
          if (entry.inputs && st.kind !== "button") return json(res, 400, { error: `Step ${entry.step + 1} was saved from a request and runs as you approved it; only a button's form can be changed.` });
          steps.push(entry.inputs ? { ...st, inputs: entry.inputs } : st);
        }
        savedRecipes[i] = { ...record, name: body.name.trim(), note: typeof body.note === "string" ? body.note.trim() : record.note, steps };
      } else if (body.action === "duplicate") {
        savedRecipes.push({ ...savedRecipes[i], id: `r-${++seq}`, name: `${savedRecipes[i].name} (copy)`, saved: new Date().toISOString(), last_run: "", last_documents: 0 });
      } else if (body.action === "delete") {
        savedRecipes.splice(i, 1);
      } else if (body.action === "inputs") {
        if (body.rev && body.rev !== revOf(savedRecipes[i])) return json(res, 400, { error: "The recipe changed since its form was opened; open it again." });
        const forms = Array.isArray(body.inputs) ? body.inputs : [];
        savedRecipes[i] = { ...savedRecipes[i], steps: savedRecipes[i].steps.map((st, j) => (forms[j] && st.kind === "button" ? { ...st, inputs: forms[j] } : st)) };
      } else return json(res, 400, { error: "Unknown recipe change." });
      return json(res, 200, view);
    }
    case "POST /api/mode": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      if (!["safe", "default", "obliterate"].includes(body.mode)) return json(res, 400, { error: "There is no such mode." });
      if (body.mode === "obliterate" && !settings.show_obliterate) return json(res, 400, { error: "Obliterate mode is hidden. Settings → Advanced shows it." });
      if (body.mode === "obliterate" && body.acknowledged !== true) return json(res, 400, { error: "Read the Obliterate warning and confirm it first." });
      const crossing = (view.mode === "obliterate") !== (body.mode === "obliterate");
      view.mode = body.mode;
      if (body.mode !== "safe") view.connection = { ready: settings.key_present, origin: settings.origin };
      if (crossing) {
        view.messages.push(msg("app", body.mode === "obliterate" ? OBLITERATE_ON : OBLITERATE_OFF));
        view.stage = view.stage === "preview" ? "idle" : view.stage;
        view.plan = null;
        view.samples = [];
        view.worth_knowing = [];
        view.action = { label: "", token: "", kind: "none", can_hold: false };
        // the hands-on tools: on with the mode only when the warning's own box was ticked (the literal `true`), and
        // gone with it; what the app planned itself under the other rules is void
        ownPlan = null;
        view.hands_on = body.mode === "obliterate" && body.hands_on === true;
        if (view.hands_on) view.messages.push(msg("app", HANDS_ON_ON));
      }
      return json(res, 200, view);
    }
    case "POST /api/hands-on": {
      // the hands-on tools' own switch, in Obliterate's strip; never kept
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      if (view.mode !== "obliterate") return json(res, 400, { error: HANDS_ON_OBLITERATE_ONLY });
      const on = body.on === true;
      if (on !== (view.hands_on === true)) {
        if (!on && ownPlan?.source === "hands-on") withdrawOwn();   // a part replacement that waits is withdrawn
        view.hands_on = on;
        view.messages.push(msg("app", on ? HANDS_ON_ON : HANDS_ON_OFF));
      }
      return json(res, 200, view);
    }
    case "POST /api/parts": {
      // read-only, so it answers while work runs too
      const body = await readJson(req);
      const found = handsOnDocument(body.path);
      if (found.error) return json(res, 400, { error: found.error });
      const which = body.which === "original" ? "original" : "current";
      return json(res, 200, {
        path: found.a.path, document: found.a.name, which, has_copy: found.a.current !== found.a.path,
        parts: partsOf(found.a, which).map(({ name: part, size, type, xml }) => ({ name: part, size, type, xml })),
      });
    }
    case "POST /api/part": {
      const body = await readJson(req);
      const found = handsOnDocument(body.path);
      if (found.error) return json(res, 400, { error: found.error });
      const part = partsOf(found.a, body.which === "original" ? "original" : "current").find((x) => x.name === body.part);
      if (!part) return json(res, 400, { error: `${found.a.name} has no part named ${body.part ?? ""}.` });
      return json(res, 200, pageOf(part, body.page));
    }
    case "POST /api/parts/search": {
      const body = await readJson(req);
      const found = handsOnDocument(body.path);
      if (found.error) return json(res, 400, { error: found.error });
      return json(res, 200, searchOf(partsOf(found.a, body.which === "original" ? "original" : "current"), body.query));
    }
    case "POST /api/part/save": {
      // Save this part: its own bytes into a folder. The mock has no dialog and writes no file: it answers as if the
      // dialog had returned a folder, and remembers what "is there" so a second save finds it.
      if (view.busy) return busyRefusal();
      if (tutorial) return json(res, 409, { error: "End the tour first; this saves a part of your own documents." });
      const body = await readJson(req);
      const found = handsOnDocument(body.path);
      if (found.error) return json(res, 400, { error: found.error });
      const which = body.which === "original" ? "original" : "current";
      const part = partsOf(found.a, which).find((x) => x.name === body.part);
      if (!part) return json(res, 400, { error: `${found.a.name} has no part named ${body.part ?? ""}.` });
      const folder = typeof body.folder === "string" && body.folder ? body.folder : "C:/Users/abe/Documents/Saved parts";
      if (folder.startsWith(APP_FOLDERS)) {
        return json(res, 400, { error: `${folder} is inside the app's scratch folder, which is deleted when the window closes; nothing was saved. Choose a folder of your own.` });
      }
      const base = `${found.a.name.replace(/\.docx$/i, "")} - ${part.name.replace(/^\/+|\/+$/g, "").replace(/[\\/]/g, "_")}`;
      const bytes = part.text ?? `${part.size} bytes`;
      let dest = `${folder}/${base}`;
      // a file there with the same bytes is that file; one with other bytes is left, and this one numbered beside it
      for (let n = 2; savedParts.has(dest) && savedParts.get(dest) !== bytes; n++) dest = `${folder}/${base.replace(/(\.[^.]+)?$/, (ext) => ` (${n})${ext}`)}`;
      const already = savedParts.has(dest);
      savedParts.set(dest, bytes);
      const of = which === "current" && found.a.current !== found.a.path ? "its edited copy" : "the original";
      view.messages.push(msg("app", `Saved the part ${part.name} of ${found.a.name} (${of}), as it is, to ${dest}${already ? " (it was already there, unchanged)." : "."} Change it in an editor of your own; Replace this part then puts it back, after a preview.`));
      return json(res, 200, view);
    }
    case "POST /api/part/replace": {
      // Replace this part: multipart `path`, `part` and `file`; the one-operation plan is built and previewed
      if (view.busy) return busyRefusal();
      if (tutorial) return json(res, 409, { error: "End the tour first; this changes a copy of your own documents." });
      const { fields, uploads } = parseMultipart(await readBody(req), req.headers["content-type"]);
      const found = handsOnDocument(fields.path ?? "");
      if (found.error) return json(res, 400, { error: found.error });
      const part = partsOf(found.a, "current").find((x) => x.name === fields.part);
      if (!part) return json(res, 400, { error: `${found.a.name} has no part named ${fields.part ?? ""}.` });
      const file = uploads.find((u) => u.field === "file");
      if (!file) return json(res, 400, { error: "Choose the file that takes the part's place." });
      if (file.data.length > MAX_PART_BYTES) {
        return json(res, 400, { error: `${file.name} is ${file.data.length.toLocaleString("en-US")} bytes; a part is replaced with a file of at most ${MAX_PART_BYTES.toLocaleString("en-US")}.` });
      }
      return replacePartJob(res, found.a, part, file);
    }
    case "POST /api/rescue": {
      // Try to rescue: a refused document with something to try, in Obliterate mode alone
      if (view.busy) return busyRefusal();
      if (tutorial) return json(res, 409, { error: "End the tour first; this attaches a document to your own conversation." });
      const body = await readJson(req);
      if (body.password !== undefined && body.password !== null && typeof body.password !== "string") return json(res, 400, { error: "The password must be text." });
      if (view.mode !== "obliterate") return json(res, 400, { error: RESCUE_OBLITERATE_ONLY });
      const a = view.attachments.find((x) => x.kind === "document" && x.path === body.path);
      if (!a) return json(res, 400, { error: "That file is not attached." });
      if (!rescueKind(a)) return json(res, 400, { error: `There is nothing to rescue in ${a.name}.` });
      return rescueJob(res, a, body.password || "");
    }
    case "POST /api/word-actions": {
      // Word actions: the named actions, in that order, on a copy of the document (of each in scope with `every`)
      if (view.busy) return busyRefusal();
      if (tutorial) return json(res, 409, { error: "End the tour first; this changes a copy of your own documents." });
      const body = await readJson(req);
      if (!Array.isArray(body.actions) || !body.actions.every((id) => typeof id === "string")) return json(res, 400, { error: "Choose at least one Word action." });
      if (view.mode !== "obliterate") return json(res, 400, { error: WORD_OBLITERATE_ONLY });
      const a = view.attachments.find((x) => x.kind === "document" && x.state !== "refused" && x.path === body.path);
      if (!a) return json(res, 400, { error: "That document is not attached." });
      const problem = wordProblem(body.actions);
      if (problem) return json(res, 400, { error: problem });
      return wordActionsJob(res, a, body.actions, body.every === true);
    }
    case "POST /api/todo/work": {
      // a failed cell's Work on it: the pass over that cell, over an operation, or over the list; from Safe mode the
      // window turns to Default mode first
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const owed = owedOf(view).filter((x) => (!body.operation || x.operation === body.operation) && (!body.path || x.path === body.path));
      if (!owed.length) return json(res, 400, { error: "That is not on your to-do list." });
      if (view.focus.length) return json(res, 400, { error: "Finish the pass over your to-do list first, or drop it." });
      if (view.stage === "preview") return json(res, 400, { error: "Create the copies, or discard the preview, first." });
      if (view.mode === "safe") {
        view.mode = "default";
        view.connection = { ready: settings.key_present, origin: settings.origin };
        view.messages.push(msg("app", "Switched to Default mode, where the conversation plans a change with an AI model."));
      }
      const pass = owed.filter((x) => x.operation === owed[0].operation);
      view.focus = pass.map((x) => x.path);
      view.todo = view.todo.filter((t) => !view.focus.includes(t.path));
      view.stage = "resume";
      view.action = { label: "Continue", token: "tok-pass", kind: "continue", can_hold: false };
      const names = pass.map((x) => x.name).join(", ");
      view.messages.push(msg("app", `${pass.length > 1 ? `Working on the ${pass.length} documents from your to-do list: ${names}.` : `Working on ${names}, from your to-do list.`} What ${pass.length > 1 ? "they still owe" : "it still owes"}: “${owed[0].request}”. Tell me what should be different, or continue to plan ${pass.length > 1 ? "them" : "it"} again as before. The other copies stay as they are.`));
      return json(res, 200, view);
    }
    case "POST /api/todo/waive": {
      // Not needed on this one, or back on the to-do list
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const key = `${body.operation}\t${body.path}`;
      const waived = view.waived ?? [];
      const on = body.on !== false;
      if (on && !owedOf(view).some((x) => x.operation === body.operation && x.path === body.path)) return json(res, 400, { error: "That is not on your to-do list." });
      if (!on && !waived.includes(key)) return json(res, 400, { error: "That was not marked as not needed." });
      view.waived = on ? [...waived, key] : waived.filter((k) => k !== key);
      const doc = view.attachments.find((a) => a.path === body.path);
      view.messages.push(msg("app", on ? `That change is marked as not needed on ${doc?.name ?? "it"} and is off your to-do list. Nothing in the document changed.` : `That change is back on your to-do list for ${doc?.name ?? "it"}.`));
      return json(res, 200, view);
    }
    case "POST /api/send": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const text = String(body.text || "");
      if (view.mode === "safe") {
        // As the real server: the job runs and ends in an error, nothing recorded.
        const job = startStream(res);
        return job.fail("Safe mode has no conversation: press a button to make a change, or switch to Default mode to type a request.");
      }
      const key = text.toLowerCase();
      if (!["question", "resume", "budget"].includes(view.stage)) {
        lastRequest = text;   // a new request (Undo names it)
        lastRecipe = "";
      }
      if (key === WORK_TODO.toLowerCase() && owedOf(view).length) return todoJob(res, text);
      if (key.includes("question")) return questionJob(res, text);
      if (key.includes("fail")) return failJob(res, text);
      if (key.includes("pause")) return pauseJob(res, text);
      return planJob(res, text, key.includes("layout"));
    }
    case "POST /api/choose": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const choice = view.choices.find((c) => c.id === body.choice_id);
      if (!choice) return json(res, 400, { error: "That choice is no longer open." });
      if (body.wording !== undefined && (typeof body.wording !== "string" || !choice.editable)) {
        return json(res, 400, { error: "Only a choice that offers one wording can have its wording edited." });
      }
      return planJob(res, body.wording === undefined ? choice.label : `${choice.label} — “${body.wording}”`);
    }
    case "POST /api/primary": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      if (!body.token || body.token !== view.action.token) {
        return json(res, 409, { error: "The documents or plan changed. Review the updated plan first." });
      }
      const hold = Array.isArray(body.hold) ? body.hold.filter((p) => typeof p === "string") : [];
      if (hold.length && !view.action.can_hold) return json(res, 409, { error: "A document cannot be set aside right now." });
      return primaryJob(res, body.token, hold);
    }
    case "POST /api/occurrences": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      if (!Array.isArray(body.skip) || !body.skip.every((k) => typeof k === "string")) {
        return json(res, 400, { error: "The unticked occurrences could not be read." });
      }
      return occurrencesJob(res, body.skip);
    }
    case "POST /api/stop":
      if (!running) return json(res, 409, { error: "Nothing is running." });
      running.stop = true;
      return json(res, 200, { ok: true });
    case "POST /api/put-back": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const entry = view.todo.find((t) => t.path === body.path);
      if (!entry) return json(res, 400, { error: "That document is not set aside." });
      view.todo = view.todo.filter((t) => t.path !== body.path);
      view.messages.push(msg("app", `${entry.name} is no longer set aside: it is back among the documents your next request works on. What it still owes stays on your to-do list.`));
      return json(res, 200, view);
    }
    case "POST /api/todo/obliterate": {
      // a failed cell's Obliterate this one: the mode for that one document, then the pass over it alone
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const entry = owedOf(view).find((x) => x.path === body.path && (!body.operation || x.operation === body.operation));
      if (!entry) return json(res, 400, { error: "That document is not on your to-do list." });
      if (!settings.show_obliterate) return json(res, 400, { error: "Obliterate mode is hidden. Settings → Advanced shows it." });
      if (view.focus.length) return json(res, 400, { error: "Finish the pass over your to-do list first, or drop it." });
      if (view.mode !== "obliterate") {
        if (body.acknowledged !== true) return json(res, 400, { error: "Read the Obliterate warning and confirm it first." });
        view.mode = "obliterate";
        view.connection = { ready: settings.key_present, origin: settings.origin };
        view.messages.push(msg("app", OBLITERATE_ON));
        view.hands_on = body.hands_on === true;
        if (view.hands_on) view.messages.push(msg("app", HANDS_ON_ON));
      }
      view.todo = view.todo.filter((t) => t.path !== body.path);
      view.focus = [body.path];
      view.messages.push(msg("app", `Working on ${entry.name}, from your to-do list. Tell me what should be different for it, or continue to plan it again as before. The other copies stay as they are.`));
      return json(res, 200, view);
    }
    case "POST /api/discard": {
      if (view.busy) return busyRefusal();
      const withdrawn = discardOf(view);
      if (!withdrawn) {
        return json(res, 400, { error: view.mode === "safe" ? "There is no preview to discard." : "There is nothing to discard: no preview or request is waiting." });
      }
      if (view.mode !== "safe") {
        // Default and Obliterate: the request ends with nothing created; the next message is a new request
        ownPlan = null;
        view.plan = null;
        view.samples = [];
        view.worth_knowing = [];
        view.warnings = [];
        view.questions = [];
        view.choices = [];
        view.chips = [];
        view.budget = { paused: false, reason: "" };
        view.action = { label: "", token: "", kind: "none", can_hold: false };
        view.stage = view.results.length && view.working.generation ? "done" : "empty";
        view.working = { ...view.working, ...undoFields() };
        view.messages.push(msg("app", (withdrawn === "preview" ? "Preview discarded" : "Request dropped") +
          "; nothing was created. Your next message starts a new request" +
          (view.working.generation ? "; the copies you have stay as they are." : ".")));
        return json(res, 200, view);
      }
      if (beforeButton) {
        view.results = beforeButton.results;
        view.left_untouched = beforeButton.left_untouched;
      }
      beforeButton = null;
      view.plan = null;
      view.samples = [];
      view.worth_knowing = [];
      view.warnings = [];
      view.action = { label: "", token: "", kind: "none", can_hold: false };
      view.stage = view.results.length && view.working.generation ? "done" : "empty";
      view.working = { ...view.working, ...undoFields() };
      view.messages.push(msg("app", "Preview discarded; nothing was created. Change the form and preview again, or choose another button."));
      return json(res, 200, view);
    }
    case "POST /api/undo": {
      if (view.busy) return busyRefusal();
      const top = undoStack[undoStack.length - 1];
      if (!top) return json(res, 400, { error: "There is nothing to undo." });
      if (undoFields().undo_blocked) return json(res, 400, { error: undoFields().undo_blocked });
      undoStack.pop();
      // the copies go back: the parts a later Replace this part put there with them, and what Export anyway offered
      if (top.replaced) {
        replacedParts.clear();
        for (const [key, part] of top.replaced) replacedParts.set(key, part);
      }
      for (const missed of Object.values(exportMissed)) missed.clear();
      const currents = new Map(top.currents);
      view.attachments = view.attachments.map((a) => (currents.has(a.path) ? { ...a, current: currents.get(a.path) } : a));
      view.results = top.results;
      view.left_untouched = top.left_untouched;
      view.stage = view.results.length && top.generation ? "done" : "empty";
      view.working = {
        generation: top.generation,
        from_originals: top.generation === 0,
        can_start_over: top.generation > 0 || view.todo.length > 0,
        done: top.done ?? [],
      };
      view.working = { ...view.working, ...undoFields() };
      const upcoming = view.mode === "safe" ? "The next button" : "Your next request";
      view.messages.push(msg("app", `Undid “${top.request}”. ` +
        (top.generation ? `${upcoming} builds on the copies from before it.` : `${upcoming} works on your originals again.`) +
        " The copies it made stay where they were saved; nothing was deleted."));
      return json(res, 200, view);
    }
    case "POST /api/start-over":
      if (view.busy) return busyRefusal();
      resetGoingBack();
      resetObliterate();
      view.working = { generation: 0, from_originals: true, can_start_over: false, done: [] };
      view.plan = null;
      view.samples = [];
      view.worth_knowing = [];
      view.warnings = [];
      view.results = [];
      view.left_untouched = [];
      view.todo = [];
      view.focus = [];
      view.action = { label: "", token: "", kind: "none", can_hold: false };
      view.stage = view.messages.length ? "resume" : "empty";
      view.attachments = view.attachments.map((a) => ({ ...a, current: a.path }));
      view.messages.push(msg("app", "Starting over from the originals. " +
        (view.mode === "safe" ? "Pick a button to start." : "Tell me what you'd like to change.")));
      return json(res, 200, view);
    case "POST /api/new-conversation": {
      if (view.busy) return busyRefusal();
      if (tutorial) return json(res, 409, { error: "End the tour first; your conversation comes back when it does." });
      const keep = view.update;
      const mode = view.mode, modes = view.modes;
      view = emptyView();
      resetGoingBack();
      resetObliterate();
      view.update = keep;
      view.mode = mode;
      view.modes = modes;
      return json(res, 200, view);
    }
    case "POST /api/export": {
      // Export's three kinds (docs/WEB_API.md): Word (Tracked Changes ON) answers with the view; Word (Tracked
      // Changes OFF) and PDF (Clean) make and check their clean copies first, so they answer with the job stream.
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const kind = typeof body.kind === "string" && body.kind ? body.kind : "word";
      const folder = "C:/Users/abe/Documents/Saved copies";
      if (body.anyway === true) {
        // Export anyway (Obliterate mode alone): the clean kind made by Word for the marked copies that kind's
        // checked export left without a file; each refusal before the folder is asked for
        if (view.mode !== "obliterate") return json(res, 400, { error: ANYWAY_OBLITERATE_ONLY });
        if (!(kind in ANYWAY_LABELS)) return json(res, 400, { error: ANYWAY_KINDS });
        if (anywayCopies(view, kind).length === 0) return json(res, 400, { error: ANYWAY_NOTHING });
        if (!wordAvailable) return json(res, 400, { error: NO_WORD });
        settings.export_folder = folder;
        return exportAnywayJob(res, kind, folder);
      }
      if (!["word", "accepted", "pdf"].includes(kind)) return json(res, 400, { error: "Export saves Word with tracked changes on or off, or a clean PDF." });
      if (view.results.length === 0) return json(res, 400, { error: "There are no edited copies to save yet." });
      settings.export_folder = folder;
      if (kind === "word") {
        view.results = view.results.map((r) => ({ ...r, saved_to: `${folder}/${r.name}` }));
        view.export = { folder, unsaved: 0, copies: view.results.length };
        view.messages.push(msg("app", `Saved ${view.results.length} copies to ${folder}.`));
        return json(res, 200, view);
      }
      const s = startStream(res);
      await s.emit({ type: "status", kind: "creating", text: "Creating and checking the clean copies…" });
      if (kind === "pdf") await s.emit({ type: "status", kind: "rendering", text: "Saving the clean PDFs…" });
      // A copy delivered UNVERIFIED does not pass the clean run's checks either, so it gets no file and is named with
      // why; the others are written. What this kind left out is what Export anyway may then be offered for.
      const missing = view.results.filter((r) => failedMarks(r).length > 0);
      const made = view.results.filter((r) => !missing.includes(r));
      exportMissed[kind].clear();
      for (const r of missing) exportMissed[kind].add(r.output);
      const n = made.length;
      const shown = made.filter((r) => (r.unverified ?? []).length > 0).length;
      const salvaged = made.filter((r) => salvagedOf(view, r.source)).length;
      const done = msg("app", (n === 0
        ? `No ${kind === "pdf" ? "PDF" : "file"} was saved.`
        : kind === "pdf"
          ? `Saved ${n} clean PDF${n === 1 ? "" : "s"} to ${folder}, made by Microsoft Word: every comment removed and every tracked change accepted, each from a copy the checks passed. Your edited copies are as they were.`
          : `Saved ${n} Word cop${n === 1 ? "y" : "ies"} with every tracked change accepted to ${folder}. Comments stay; your edited copies are as they were.`) +
        (shown ? ` ${shown} ${shown === 1 ? "was" : "were"} made from an edited copy marked UNPROVEN: a custom method made it, and what it changed was shown to you, not proven.` : "") +
        (salvaged ? ` ${salvaged} ${salvaged === 1 ? "was" : "were"} made from a document the app rescued (SALVAGED): nothing proves the rescued document holds everything the file it came from did.` : "") +
        (missing.length ? ` Not saved: ${missing.map((r) => `${r.name} — it did not pass the independent checks (${failedMarks(r).map((m) => m.split(" ")[0]).join(", ")})`).join("; ")}.` : "") +
        anywayOffer(kind));
      view.messages.push(done);
      await s.emit({ type: "message", message: done });
      view.export = { ...view.export, folder };
      return s.end();
    }
    case "POST /api/open": {
      const body = await readJson(req);
      if (typeof body.path !== "string" || !body.path) return json(res, 404, { error: "That copy was moved or deleted. Continue the conversation to create a new result." });
      console.log(`[mock] open ${body.path}`);
      return json(res, 200, { ok: true });
    }
    case "GET /api/image": {
      const p = url.searchParams.get("path") || "";
      if (!p.startsWith(RUN_DIR)) return json(res, 404, { error: "No such picture." });
      const png = previewPng(p.endsWith("2.png") ? 2 : 1);
      res.writeHead(200, { "Content-Type": "image/png", "Content-Length": png.length, "Cache-Control": "no-store", "Access-Control-Allow-Origin": "*" });
      return res.end(png);
    }
    case "GET /api/settings":
      return json(res, 200, keySettings());
    case "POST /api/settings/remove-key":
      if (view.busy) return busyRefusal();
      if (!["saved", "session"].includes(settings.origin)) return json(res, 400, { error: "No app-managed key to remove." });
      settings = { ...settings, key_present: false, origin: "none" };
      view.connection = { ready: false, origin: "none" };
      return json(res, 200, keySettings());
    case "POST /api/settings": {
      const body = await readJson(req);
      if ("theme" in body) {
        if (!THEMES.includes(body.theme)) return json(res, 400, { error: "There is no such colour scheme." });
        settings = { ...settings, theme: body.theme };
        if (!("api_key" in body)) return json(res, 200, keySettings());
      }
      if ("tutorial_version" in body) {
        const v = body.tutorial_version;
        if (typeof v !== "number" || !Number.isInteger(v) || v < 0) return json(res, 400, { error: "The tour's version is a whole number." });
        settings = { ...settings, tutorial_version: v };
        view.tutorial = tutorialBlock();
        if (Object.keys(body).length === 1) return json(res, 200, keySettings());
      }
      if ("safe_note_hidden" in body) {
        if (typeof body.safe_note_hidden !== "boolean") return json(res, 400, { error: "The Safe mode note is hidden or shown." });
        settings = { ...settings, safe_note_hidden: body.safe_note_hidden };
        view.safe_note_hidden = body.safe_note_hidden;
        if (Object.keys(body).length === 1) return json(res, 200, keySettings());
      }
      if ("show_obliterate" in body) {
        if (typeof body.show_obliterate !== "boolean") return json(res, 400, { error: "Show Obliterate mode is on or off." });
        settings = { ...settings, show_obliterate: body.show_obliterate };
        view.modes = body.show_obliterate ? ["safe", "default", "obliterate"] : ["safe", "default"];
        if (!body.show_obliterate && view.mode === "obliterate") {
          view.mode = "default";
          view.hands_on = false;
          if (ownPlan) withdrawOwn();
          view.messages.push(msg("app", OBLITERATE_OFF));
        }
        if (!("api_key" in body)) return json(res, 200, keySettings());
      }
      if ("spending_limit_usd" in body) {
        const limit = body.spending_limit_usd;
        if (limit !== null && (typeof limit !== "number" || !Number.isFinite(limit) || limit < 0)) {
          return json(res, 400, { error: "The spending check is an amount in US dollars, or off." });
        }
        settings = { ...settings, spending_limit_usd: limit === 0 ? null : limit };
        view.cost = { ...view.cost, limit: settings.spending_limit_usd };
        if (!("api_key" in body)) return json(res, 200, keySettings());
      }
      if ("fast_mode" in body) {
        if (typeof body.fast_mode !== "boolean") return json(res, 400, { error: "Fast responses is on or off." });
        settings = { ...settings, fast_mode: body.fast_mode };
        view.cost = { ...view.cost, fast: body.fast_mode };
        if (!("api_key" in body)) return json(res, 200, keySettings());
      }
      const key = String(body.api_key || "");
      if (!key.trim()) return json(res, 200, keySettings());
      settings = { ...settings, key_present: true, origin: body.remember ? "saved" : "session" };
      view.connection = { ready: true, origin: settings.origin };
      return json(res, 200, keySettings());
    }
    case "GET /api/about":
      return json(res, 200, {
        version: VERSION,
        license: readFileSync(resolve(HERE, "../../LICENSE"), "utf8"),
        release_notes: `## Spec Surgeon ${VERSION}\n\n- The window is a web page in the style of Build-a-Spec.\n- One Word instance per batch and a render cache make previews faster.\n- Pictures and tables can be attached for the edit to use.\n`,
        credits: ["Python: PSF License", "lxml: BSD-3-Clause", "PyMuPDF (MuPDF): AGPL-3.0", "Anthropic Python SDK: Apache-2.0; contributions made before version 2.0.0 also under the MIT License", "pywebview: BSD-3-Clause"],
        name: "Spec Surgeon",
        tagline: "Surgical edits to Word specifications with independent verification records.",
        github: "https://github.com/Abe-Borg/spec-surgeon",
        author: "Abraham Borg",
        email: "abrahamborg@gmail.com",
        linkedin: "https://www.linkedin.com/in/abrahamborg/",
        copyright: "© 2026 Abraham Borg",
        license_name: "MIT License",
        license_summary: [
          "Free to use, copy, change and share, for any purpose, including commercially.",
          "Keep the copyright notice and this license text with every copy or substantial part of it.",
          "Provided as is, without warranty of any kind: the author is not liable for anything that happens through its use. Check every delivered file yourself.",
        ],
        model: "claude-opus-5-5",
        components: [
          { name: "Python", license: "PSF License", url: "https://docs.python.org/3/license.html" },
          { name: "pywebview (the native window)", license: "BSD-3-Clause", url: "https://github.com/r0x0r/pywebview/blob/master/LICENSE" },
          { name: "lxml", license: "BSD-3-Clause", url: "https://github.com/lxml/lxml/blob/master/LICENSE.txt" },
          { name: "PyMuPDF (MuPDF)", license: "AGPL-3.0", url: "https://github.com/pymupdf/PyMuPDF/blob/main/COPYING" },
          { name: "Anthropic Python SDK", license: "Apache-2.0; contributions made before version 2.0.0 also under the MIT License", url: "https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE" },
        ],
      });
    case "GET /api/update":
      return json(res, 200, view.update);
    case "POST /api/update/check":
      // As the real flow does: a check you asked for clears the last answer until its own lands.
      view.update = { ...view.update, manual_result: "" };
      setTimeout(() => {
        if (!updateFound) view.update = { ...view.update, manual_result: `You're up to date — Spec Surgeon ${VERSION} is the newest version.` };
        else if (view.update.state === "none") view.update = { state: "available", version: "1.7.0", notes: "Faster previews: one Word instance per batch.", progress: 0, error: "", manual_result: "" };
      }, 1500);
      return json(res, 200, view.update);
    case "POST /api/update/download":
      if (view.update.state !== "available") return json(res, 409, { error: "There is no update to download." });
      view.update = { ...view.update, state: "downloading", progress: 0 };
      clearInterval(downloadTimer);
      downloadTimer = setInterval(() => {
        view.update.progress = Math.min(100, view.update.progress + 20);
        if (view.update.progress >= 100) {
          view.update = { ...view.update, state: "ready" };
          clearInterval(downloadTimer);
        }
      }, 700);
      return json(res, 200, view.update);
    case "POST /api/update/install":
      if (view.busy) return busyRefusal();
      console.log("[mock] install update (the app would quit here)");
      return json(res, 200, { ok: true });
    case "POST /api/logs":
      console.log("[mock] open logs folder");
      return json(res, 200, { ok: true });
    case "POST /api/diagnostics": {
      // The page's own reports (lib/diagnostics.ts): taken, and kept for GET /mock/page-reports.
      const body = await readJson(req);
      const reports = Array.isArray(body.reports) ? body.reports.slice(0, 25) : [];
      pageReports.push(...reports);
      if (pageReports.length > 500) pageReports.splice(0, pageReports.length - 500);
      return json(res, 200, { ok: true, taken: reports.length });
    }
    case "POST /api/diagnostics/bundle":
      console.log("[mock] save a diagnostic bundle");
      return json(res, 200, { path: "C:/Users/you/AppData/Local/Spec Surgeon/diagnostics/spec-surgeon-diagnostics-mock.zip",
                              name: "spec-surgeon-diagnostics-mock.zip", files: 12, skipped: 3 });
    case "POST /api/quit":
      if (view.busy) return busyRefusal();
      console.log("[mock] quit");
      return json(res, 200, { ok: true });
    default:
      return json(res, 404, { error: "No such route." });
  }
}

const server = http.createServer((req, res) => {
  handle(req, res).catch((failure) => {
    console.error(failure);
    if (!res.headersSent) json(res, 500, { error: "The mock server hit an error; see its console." });
    else res.end();
  });
});

server.listen(PORT, HOST, () => {
  console.log(`Spec Surgeon mock server on http://${HOST}:${PORT}/?token=${TOKEN}`);
  console.log(`Built page: ${existsSync(join(STATIC, "index.html")) ? STATIC : "(not built yet — npm run build)"}`);
});
