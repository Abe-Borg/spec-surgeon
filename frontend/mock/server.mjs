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
 * Mock-only routes (never in the real server): POST /mock/scenario {name}
 * sets the view to a canned scenario (empty, attached, question, preview,
 * layouts, done, todo, paused, update-available, update-ready, up-to-date,
 * no-key, safe, safe-done); POST /mock/reset; GET /mock/page-reports (what the page sent to
 * /api/diagnostics). A check for updates finds 1.7.0, except after
 * the up-to-date scenario, where it answers "You're up to date".
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
const TEMPLATE = { path: "C:/Specs/Templates/Office master 2026.docx" };
const PICTURE = { path: "C:/Specs/Riverside/logo-2026.png", notes: ["A PNG picture, 600 × 200 pixels, for the edit to use."] };

const RUN_DIR = "C:/Users/abe/AppData/Local/Spec Surgeon/runs/20261020-143512/outputs";
/** Safe mode's buttons, as the real server lists them: generated from ``recipes.catalog()`` and pinned to it by
 *  tests/test_recipes.py, so the mock never offers a button the app does not have. */
const RECIPES = JSON.parse(readFileSync(resolve(HERE, "recipes.json"), "utf8"));
const TEMPLATE_INFO = {
  kind: "prepared",
  typed_slots: ["ISSUED FOR PERMIT"],
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
  }));
}

// document_task.OBLITERATE_ON / OBLITERATE_OFF
const OBLITERATE_ON = "Obliterate mode is on. Claude may write wording and custom methods that change anything Word stores, and a copy whose checks fail is delivered marked UNVERIFIED instead of being held back. Copies only; your originals are never written. What was planned before is void.";
const OBLITERATE_OFF = "Obliterate mode is off: every placed word is yours again, custom methods are formatting only, and a copy whose checks fail is held back. What was planned in Obliterate mode is void.";

function results() {
  return DOCS.map((d) => ({ source: d.path, output: `${RUN_DIR}/${name(d.path)}`, name: name(d.path) }));
}

const WARNINGS = [
  "21 05 00 Common Work Results, page 7: the last line of article 1.4 moved to the next page after the font change; nothing is cut off.",
  "21 05 00 Common Work Results, page 16: the new page carries only the end-of-section line.",
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
    blocking: false,
    results: [],
    action: { label: "", token: "", kind: "none", can_hold: false },
    todo: [],
    focus: [],
    working: { generation: 0, from_originals: true, can_start_over: false },
    budget: { paused: false, steps_per_pause: 48 },
    update: { state: "none", version: "", notes: "", progress: 0, error: "" },
    mode: "default",
    modes: ["safe", "default"],
    template: NO_TEMPLATE,
    left_untouched: [],
  };
}

/** The reply that begins the pass over the to-do list; the real server recognises these exact words. */
const WORK_TODO = "Work on the documents I set aside";
const HELD_REASON = "You marked its preview as needing more work.";

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
    v.working = { generation: 1, from_originals: false, can_start_over: true };
    v.left_untouched = [{ path: DOCS[2].path, name: name(DOCS[2].path), reason: "Editing is restricted in this file (Review → Restrict Editing); remove the restriction in Word, then run again." }];
    return v;
  },
  question: () => {
    const v = SCENARIOS.attached();
    v.stage = "question";
    v.cost = { usd: 0.07, text: "about $0.07", calls: 2 };
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
    v.cost = { usd: 0.21, text: "about $0.21", calls: 5 };
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
    v.action = { label: "Create edited copies", token: "tok-approve-1", kind: "approve", can_hold: true };
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
    v.cost = { usd: 0.34, text: "about $0.34", calls: 8 };
    v.messages = [...v.messages, msg("app", "3 edited copies created and verified · 14:35")];
    v.results = results();
    v.action = { label: "", token: "", kind: "none", can_hold: false };
    v.working = { generation: 1, from_originals: false, can_start_over: true };
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
    v.messages = [
      ...v.messages.slice(0, -1),
      msg("app", "3 document copies are ready. You can open them, describe another change to make on top of them, or start over from the originals. 1 was delivered UNVERIFIED: a check failed, and each one's failed checks are beside it."),
    ];
    return v;
  },
  todo: () => {
    const v = SCENARIOS.done();
    const aside = DOCS[1];
    v.results = v.results.filter((r) => r.source !== aside.path);
    v.todo = [{ path: aside.path, name: name(aside.path), why: "held", reason: HELD_REASON }];
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
    v.budget = { paused: true, steps_per_pause: 48 };
    v.cost = { usd: 2.41, text: "about $2.41", calls: 48 };
    v.messages = [...v.messages, msg("app", "Work paused after 48 AI steps; your progress is saved.")];
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
};

let view = emptyView();
let settings = { key_present: true, origin: "saved", can_remember: true, out_root: "C:/Users/abe/AppData/Local/Spec Surgeon/runs", logs_dir: "C:/Users/abe/AppData/Roaming/Spec Surgeon/logs", theme: "clay", fast_mode: false, show_obliterate: false, fast: { model: "claude-opus-5-5", available: true, speedup: 2.5, price_factor: 2, standard_usd_per_mtok: [4, 20], fast_usd_per_mtok: [8, 40], refused: null } };
const THEMES = ["clay", "blueprint", "graphite", "carbon", "linen"];
function keySettings() { return { ...settings, can_remove_key: ["saved", "session"].includes(settings.origin) }; }
let running = null; // { stop: boolean }
let downloadTimer = null;
let updateFound = true; // what the next check for updates finds; the up-to-date scenario turns it off

// --- Helpers --------------------------------------------------------------

function json(res, status, body) {
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
    res.write(`data: ${JSON.stringify({ type: "done", state: view })}\n\n`);
    res.end();
  };
  // `resend`: the request's text when it was refused before it was recorded (the real server sends it when no
  // user message with that text was kept); the page takes it back into the composer.
  const fail = (message, resend = "") => {
    view.busy = false;
    running = null;
    res.write(`data: ${JSON.stringify({ type: "error", message, state: view, ...(resend ? { resend } : {}) })}\n\n`);
    res.end();
  };
  return { job, emit, text, end, fail };
}

function spend(usd, calls) {
  view.cost = { usd: Math.round((view.cost.usd + usd) * 100) / 100, text: "", calls: view.cost.calls + calls, fast: settings.fast_mode };
  view.cost.text = view.cost.usd < 0.005 ? "less than $0.01" : `about $${view.cost.usd.toFixed(2)}`;
  return view.cost;
}

async function questionJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  view.plan = null;
  view.samples = [];
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
  view.action = { label: "Create edited copies", token: `tok-approve-${++seq}`, kind: "approve", can_hold: true };
  await s.emit({ type: "cost", cost: spend(0.14, 3) });
  s.end();
}

/** A Safe-mode button: no AI call, so no words stream — the checks, the plan and the pages, then the one button.
 *  The last document is set aside with a reason, as the real engine does for one it cannot change exactly. */
async function recipeJob(res, recipe) {
  const s = startStream(res);
  view.messages.push(msg("user", recipe.label));
  view.plan = null;
  view.samples = [];
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
  await s.emit({ type: "status", kind: "rendering", text: "" });
  view.samples = samples();
  await s.emit({ type: "samples", samples: view.samples, warnings: [], blocking: false });
  view.stage = "preview";
  view.action = { label: "Create edited copies", token: `tok-approve-${++seq}`, kind: "approve", can_hold: true };
  s.end();
}

/** “Work on the documents I set aside”: the pass over the to-do list begins — those documents alone, the request
 *  they were set aside from, Continue to plan them again as before or a message saying what should differ. */
async function todoJob(res, userText) {
  const s = startStream(res);
  view.messages.push(msg("user", userText));
  const names = view.todo.map((item) => item.name);
  view.focus = view.todo.map((item) => item.path);
  view.todo = [];
  view.plan = null;
  view.samples = [];
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
  const note = msg("app", "Work paused after 48 AI steps; your progress is saved.");
  view.messages.push(note);
  await s.emit({ type: "message", message: note });
  view.stage = "budget";
  view.budget = { paused: true, steps_per_pause: 48 };
  view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
  await s.emit({ type: "cost", cost: spend(1.9, 48) });
  s.end();
}

async function primaryJob(res, token, hold = []) {
  const s = startStream(res);
  if (view.action.kind === "continue") {
    view.budget = { paused: false, steps_per_pause: 48 };
    await s.emit({ type: "status", kind: "resuming", text: "" });
    const note = msg("app", "Continuing where the conversation stopped.");
    view.messages.push(note);
    await s.emit({ type: "message", message: note });
    view.stage = view.plan ? "preview" : "empty";
    view.action = view.plan
      ? { label: "Create edited copies", token: `tok-approve-${++seq}`, kind: "approve", can_hold: true }
      : { label: "", token: "", kind: "none", can_hold: false };
    await s.emit({ type: "cost", cost: spend(0.02, 1) });
    return s.end();
  }
  // The documents marked with the red X: on the to-do list, no copy; the others run. The pass over the to-do list
  // (`focus`) ends with its copies.
  const inScope = view.attachments.filter(
    (a) => a.kind === "document" && a.state !== "refused" && (view.focus.length ? view.focus.includes(a.path) : !view.todo.some((t) => t.path === a.path)),
  );
  const held = inScope.filter((a) => hold.includes(a.current) || hold.includes(a.path));
  const running = inScope.filter((a) => !held.includes(a));
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
    view.warnings = [];
    view.focus = view.todo.map((item) => item.path);
    view.todo = [];
    view.stage = "resume";
    view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
    return s.end();
  }
  await s.emit({ type: "status", kind: "creating", text: "" });
  await s.emit({ type: "status", kind: "checking", text: "" });
  await s.emit({ type: "activity", text: "Verifying each edited copy against the plan" });
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
  const fresh = results().filter((r) => running.some((a) => a.path === r.source) && !asideByButton.some((x) => x.path === r.source));
  if (view.mode === "safe") {
    // A button's run: the app's own note, no AI words; what it left untouched waits beside the copies.
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
    view.action = { label: "", token: "", kind: "none", can_hold: false };
    view.working = { generation: view.working.generation + 1, from_originals: false, can_start_over: true };
    return s.end();
  }
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
  view.working = { generation: view.working.generation + 1, from_originals: false, can_start_over: true };
  view.attachments = view.attachments.map((a) =>
    running.some((r) => r.path === a.path) ? { ...a, current: `${RUN_DIR}/${a.name}` } : a,
  );
  await s.emit({ type: "cost", cost: spend(0.12, 2) });
  void token;
  s.end();
}

/** Adding or removing a document after a plan exists voids that plan: the
 *  button reads Continue until the documents are checked again. */
function voidPlan() {
  if (!view.plan) return;
  view.action = { label: "Continue", token: `tok-continue-${++seq}`, kind: "continue", can_hold: false };
  view.messages.push(msg("app", "The documents changed. Review the updated plan first."));
}

// --- Multipart (just enough for the upload route) -------------------------

function parseMultipart(body, contentType) {
  const m = /boundary=(?:"([^"]+)"|([^;]+))/i.exec(contentType || "");
  const boundary = m ? m[1] || m[2] : null;
  const files = [];
  let template = false;
  if (!boundary) return { files, template };
  const parts = body.toString("latin1").split(`--${boundary}`);
  for (const part of parts) {
    const headerEnd = part.indexOf("\r\n\r\n");
    if (headerEnd === -1) continue;
    const headers = part.slice(0, headerEnd);
    const value = part.slice(headerEnd + 4).replace(/\r\n$/, "");
    const fname = /filename="([^"]*)"/i.exec(headers);
    const field = /name="([^"]*)"/i.exec(headers);
    if (fname) files.push(Buffer.from(fname[1], "latin1").toString("utf8"));
    else if (field && field[1] === "template") template = value.trim() === "true";
  }
  return { files, template };
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
      running = null;
      return json(res, 200, view);
    }
    if (path === "/mock/scenario") {
      const make = SCENARIOS[body.name];
      if (!make) return json(res, 400, { error: `Unknown scenario; one of ${Object.keys(SCENARIOS).join(", ")}.` });
      settings = { ...settings, key_present: true, origin: "saved" };
      updateFound = true;
      view = make();
      running = null;
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
      return json(res, 200, view);
    case "POST /api/attach": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const paths = Array.isArray(body.paths) ? body.paths : [];
      for (const p of paths) {
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
        if (kind === "template") view.attachments = view.attachments.filter((a) => a.kind !== "template");
        const extra = /\.docx$/i.test(f) ? (f.toLowerCase().includes("empty") ? REFUSED : {}) : PICTURE;
        if (!/\.(docx|png|jpe?g|gif|xlsx|csv|json)$/i.test(f)) return json(res, 400, { error: `${f} is not a .docx; save it as a Word document first.` });
        view.attachments.push(attachment(p, kind, extra));
      }
      if (files.length) voidPlan();
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
    case "GET /api/recipes":
      return json(res, 200, RECIPES);
    case "POST /api/recipe": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const recipe = RECIPES.recipes.find((r) => r.id === body.recipe);
      if (!recipe) return json(res, 400, { error: "There is no such button." });
      if (view.mode !== "safe") return json(res, 400, { error: "The buttons are Safe mode's; switch to Safe mode to use them." });
      return recipeJob(res, recipe);
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
        view.action = { label: "", token: "", kind: "none", can_hold: false };
      }
      return json(res, 200, view);
    }
    case "POST /api/handoff": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const paths = Array.isArray(body.paths) ? body.paths : [];
      const chosen = view.left_untouched.filter((x) => paths.includes(x.path));
      if (!chosen.length || chosen.length !== paths.length) return json(res, 400, { error: "Those documents are not the ones the last button left untouched." });
      for (const x of chosen) if (!view.todo.some((t) => t.path === x.path)) view.todo.push({ path: x.path, name: x.name });
      view.left_untouched = view.left_untouched.filter((x) => !paths.includes(x.path));
      view.mode = "default";
      view.messages.push(msg("app", `Handed over to Default mode: ${chosen.map((x) => x.name).join(", ")}. Say “${WORK_TODO}” and tell me what should be different; the conversation plans them with an AI model.`));
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
      if (key === WORK_TODO.toLowerCase() && view.todo.length) return todoJob(res, text);
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
    case "POST /api/stop":
      if (!running) return json(res, 409, { error: "Nothing is running." });
      running.stop = true;
      return json(res, 200, { ok: true });
    case "POST /api/put-back": {
      if (view.busy) return busyRefusal();
      const body = await readJson(req);
      const entry = view.todo.find((t) => t.path === body.path);
      if (!entry) return json(res, 400, { error: "That document is not on your to-do list." });
      view.todo = view.todo.filter((t) => t.path !== body.path);
      view.messages.push(msg("app", `${entry.name} is off your to-do list and back among the documents your next request works on.`));
      return json(res, 200, view);
    }
    case "POST /api/start-over":
      if (view.busy) return busyRefusal();
      view.working = { generation: 0, from_originals: true, can_start_over: false };
      view.plan = null;
      view.samples = [];
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
      const keep = view.update;
      const mode = view.mode, modes = view.modes;
      view = emptyView();
      view.update = keep;
      view.mode = mode;
      view.modes = modes;
      return json(res, 200, view);
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
      if ("show_obliterate" in body) {
        if (typeof body.show_obliterate !== "boolean") return json(res, 400, { error: "Show Obliterate mode is on or off." });
        settings = { ...settings, show_obliterate: body.show_obliterate };
        view.modes = body.show_obliterate ? ["safe", "default", "obliterate"] : ["safe", "default"];
        if (!body.show_obliterate && view.mode === "obliterate") {
          view.mode = "default";
          view.messages.push(msg("app", OBLITERATE_OFF));
        }
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
        credits: ["Python: PSF License", "lxml: BSD-3-Clause", "PyMuPDF (MuPDF): AGPL-3.0", "Anthropic Python SDK: MIT", "pywebview: BSD-3-Clause"],
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
          { name: "Anthropic Python SDK", license: "MIT", url: "https://github.com/anthropics/anthropic-sdk-python/blob/main/LICENSE" },
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
