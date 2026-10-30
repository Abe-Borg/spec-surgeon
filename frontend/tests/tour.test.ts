/**
 * The guided tour's contract (lib/tour.ts, lib/capabilities.ts, the server's src/spec_surgeon/tutorial.py):
 *
 * - every end-user capability is carried by a control in the page (`data-capability`) AND taught by a tour step, and
 *   nothing in the page or the tour names a capability the vocabulary lacks — a new control fails here until the tour
 *   covers it;
 * - every step points at an anchor (`data-tour`) the page really has;
 * - every chapter's scenario is one the server builds, and the Safe-mode form the tour prepares is the button, slot
 *   and value the server's own chapter ran;
 * - the conversation's controls are held still while the tour runs, with one wording;
 * - the words stay short, and the numbers come from the trust facts, never typed.
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { END_USER_CAPABILITIES } from "../src/lib/capabilities.ts";
import {
  TOUR,
  TOUR_COMPLETION_VERSION,
  TOUR_FIND_FORM,
  TOUR_FORM,
  TOUR_HOLD,
  TOUR_VERSION,
  anchorSelector,
  capabilityCoverage,
  chapterIndex,
  preparedInputs,
  preparedRecipe,
  stepNeedsMet,
} from "../src/lib/tour.ts";
import { TRUST_FACTS } from "../src/lib/trustFacts.ts";
import { EMPTY_VIEW, normalizeView } from "../src/lib/view.ts";

// fileURLToPath, not `.pathname`: on Windows the latter is "/D:/…", which join() turns into "D:\D:\…" (CI, #122).
const here = fileURLToPath(new URL(".", import.meta.url));
const read = (path: string) => readFileSync(join(here, path), "utf8");
const srcDir = join(here, "../src");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(tsx?|css)$/.test(entry)) out.push(full);
  }
  return out;
}
const sources = walk(srcDir).map((path) => ({ path, text: readFileSync(path, "utf8") }));
const steps = TOUR.flatMap((chapter) => chapter.steps.map((step) => ({ chapter, step })));

/** Literal `data-capability="…"` values across the page; the page never computes one. */
function pageCapabilities(): Map<string, string[]> {
  const found = new Map<string, string[]>();
  for (const { path, text } of sources) {
    for (const match of text.matchAll(/data-capability=(?:"([^"]+)"|\{([^}]*)\})/g)) {
      assert.ok(match[1], `${path}: data-capability is a literal string, never computed (${match[0]})`);
      found.set(match[1], [...(found.get(match[1]) ?? []), path]);
    }
  }
  return found;
}

/** `data-tour` values across the page: literals, plus the tray's `tray-${id}` over its three sections. */
function pageAnchors(): Set<string> {
  const anchors = new Set<string>();
  for (const { path, text } of sources) {
    for (const match of text.matchAll(/data-tour=(?:"([^"]+)"|\{`([^`]*)`\})/g)) {
      if (match[1]) anchors.add(match[1]);
      else if (match[2] === "tray-${id}") {
        const sections = read("../src/lib/workbench.ts").match(/export type TraySection = ([^;]+);/);
        assert.ok(sections, "the tray's sections are a named union");
        for (const id of sections[1].matchAll(/"([a-z]+)"/g)) anchors.add(`tray-${id[1]}`);
      } else assert.fail(`${path}: data-tour is a literal, or the tray's template (${match[0]})`);
    }
  }
  return anchors;
}

const tutorialPy = read("../../src/spec_surgeon/tutorial.py");
const pyTuple = (name: string) => {
  const match = tutorialPy.match(new RegExp(`^${name} = \\(([^)]*)\\)`, "m"));
  assert.ok(match, `${name} in tutorial.py`);
  return [...match[1].matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
};
const pyString = (name: string) => {
  const match = tutorialPy.match(new RegExp(`^${name} = "([^"]+)"`, "m"));
  assert.ok(match, `${name} in tutorial.py`);
  return match[1];
};

test("the capability vocabulary is a set of dotted ids, each once", () => {
  assert.equal(new Set(END_USER_CAPABILITIES).size, END_USER_CAPABILITIES.length);
  for (const id of END_USER_CAPABILITIES) assert.match(id, /^[a-z]+\.[a-z-]+$/, id);
});

test("every capability is in the page and in the tour, and neither names one the vocabulary lacks", () => {
  const vocabulary = new Set<string>(END_USER_CAPABILITIES);
  const page = pageCapabilities();
  for (const [id, paths] of page) assert.ok(vocabulary.has(id), `${paths[0]} carries ${id}, which lib/capabilities.ts lacks`);
  for (const id of vocabulary) assert.ok(page.has(id), `no control in the page carries data-capability="${id}"`);
  const taught = capabilityCoverage();
  for (const id of vocabulary) assert.ok(taught.has(id), `no tour step teaches ${id}`);
  for (const { chapter, step } of steps) {
    assert.ok(step.capabilities.length > 0, `${chapter.id}/${step.id} teaches at least one capability`);
    for (const id of step.capabilities) assert.ok(vocabulary.has(id), `${chapter.id}/${step.id} names ${id}`);
  }
});

test("every step's anchor is a data-tour the page has, and the overlay's fallback is one too", () => {
  const anchors = pageAnchors();
  for (const { chapter, step } of steps) {
    assert.ok(anchors.has(step.anchor), `${chapter.id}/${step.id} points at data-tour="${step.anchor}", which the page lacks`);
    assert.equal(anchorSelector(step), `[data-tour="${step.anchor}"]`);
  }
  assert.ok(anchors.has("workbench"));
  assert.match(read("../src/components/TourOverlay.tsx"), /FALLBACK_SELECTOR = '\[data-tour="workbench"\]'/);
});

test("chapter and step ids are unique, and chapterIndex finds them", () => {
  assert.equal(new Set(TOUR.map((c) => c.id)).size, TOUR.length);
  const ids = steps.map(({ chapter, step }) => `${chapter.id}/${step.id}`);
  assert.equal(new Set(ids).size, ids.length);
  TOUR.forEach((chapter, index) => {
    assert.equal(chapterIndex(chapter.id), index);
    assert.equal(chapterIndex(index), index);
  });
  // Clamped into the manifest: an unknown id opens the first chapter, an index past the end the last.
  assert.equal(chapterIndex("no-such-chapter"), 0);
  assert.equal(chapterIndex(99), TOUR.length - 1);
  assert.equal(TOUR[0].scenario, "start");
  assert.equal(TOUR[TOUR.length - 1].id, "finish");
  assert.ok(TOUR_COMPLETION_VERSION <= TOUR_VERSION);
});

test("every chapter's scenario is one the server builds, and the dev mock builds the same set", () => {
  const scenarios = pyTuple("SCENARIOS");
  assert.deepEqual(scenarios, ["start", "button", "copies", "find", "conversation", "question", "plan", "todo"]);
  for (const chapter of TOUR) assert.ok(scenarios.includes(chapter.scenario), `${chapter.id}: ${chapter.scenario}`);
  for (const scenario of scenarios) assert.ok(TOUR.some((c) => c.scenario === scenario), `no chapter shows ${scenario}`);
  // Safe-mode chapters come first, then the conversation's: the server switches modes once.
  const safe = pyTuple("SAFE_SCENARIOS");
  const modes = TOUR.map((c) => (safe.includes(c.scenario) ? "safe" : "default"));
  assert.equal(modes.lastIndexOf("safe") < modes.indexOf("default"), true, "Safe chapters precede Default ones");
  const mock = read("../mock/server.mjs");
  const mockKeys = [...mock.matchAll(/^  ([a-z]+): \(\) =>/gm)].map((m) => m[1]);
  for (const scenario of scenarios) assert.ok(mockKeys.includes(scenario), `the dev mock builds ${scenario}`);
  assert.match(mock, /case "POST \/api\/tutorial\/start"/);
  assert.match(mock, /case "POST \/api\/tutorial\/restore"/);
});

test("the Safe-mode forms the tour prepares are the buttons and values the server's chapters ran", () => {
  assert.equal(TOUR_FORM.recipe, pyString("BUTTON_RECIPE"));
  assert.equal(TOUR_FORM.value, pyString("STAMP_VALUE"));
  assert.match(tutorialPy, new RegExp(`BUTTON_INPUTS[^\\n]*\\{"slots": \\{"${TOUR_FORM.slot}": STAMP_VALUE\\}\\}`));
  assert.equal(TOUR_FIND_FORM.recipe, pyString("FIND_RECIPE"));
  assert.match(
    tutorialPy,
    new RegExp(`FIND_INPUTS[^\\n]*\\{"pairs": \\[\\{"find": "${TOUR_FIND_FORM.find}", "replace": "${TOUR_FIND_FORM.replace}"\\}\\]\\}`),
  );
  const prepared = (kind: string) => steps.filter(({ step }) => step.prepare === kind);
  assert.equal(prepared("safe-form").length, 1);
  assert.equal(prepared("safe-form")[0].step.anchor, "safe-form");
  assert.equal(prepared("safe-form")[0].chapter.scenario, "start");
  // The find chapter opens Find and replace, so the panel shows the button its previews and occurrences came from.
  assert.equal(prepared("find-form").length, 1);
  assert.equal(prepared("find-form")[0].chapter.scenario, "find");
  assert.equal(prepared("find-form")[0].chapter.steps[0], prepared("find-form")[0].step);
  assert.equal(preparedRecipe("safe-form"), TOUR_FORM.recipe);
  assert.equal(preparedRecipe("find-form"), TOUR_FIND_FORM.recipe);
  // Over the inputs each button opens with: the template's slot filled, the one pair typed, everything else kept.
  assert.deepEqual(preparedInputs("safe-form", { slots: { OTHER: "" }, tracked: false }), {
    slots: { OTHER: "", [TOUR_FORM.slot]: TOUR_FORM.value },
    tracked: false,
  });
  assert.deepEqual(preparedInputs("find-form", { pairs: [{ find: "", replace: "" }], match_case: true }), {
    pairs: [{ find: TOUR_FIND_FORM.find, replace: TOUR_FIND_FORM.replace }],
    match_case: true,
  });
  // App opens the step's form with those values.
  const app = read("../src/App.tsx");
  assert.match(app, /recipes\.find\(\(r\) => r\.id === preparedRecipe\(prepare\)\)/);
  assert.match(app, /inputs: preparedInputs\(prepare, opened\.inputs\)/);
});

test("the tour opens on Safe mode's buttons with the user's form set aside, and puts that form back when it ends", () => {
  const app = read("../src/App.tsx");
  // Every way in sets the user's form aside first: the panel shows the buttons the Safe-mode chapters point at.
  assert.match(app, /if \(tour\.active \|\| tourFormRef\.current\) return;\s*tourFormRef\.current = safeFormRef\.current;\s*setSafeForm\(EMPTY_SAFE_FORM\);/);
  assert.match(app, /const onStartTour = \(\) => \{\s*setFormAsideForTour\(\);\s*tour\.start\(\);/);
  assert.match(app, /setFormAsideForTour\(\);\s*tour\.startAtChapter\(chapter\);/);
  assert.doesNotMatch(app, /onStartTour=\{tour\.start\}/);
  // The end puts it back once (the buttons after a reload mid-tour, which had none to keep).
  assert.match(app, /const form = tourFormRef\.current;\s*tourFormRef\.current = null;\s*setSafeForm\(form \?\? EMPTY_SAFE_FORM\);/);
});

test("a step's needs are read off the view, and unmet on an empty one", () => {
  for (const { chapter, step } of steps) {
    if (step.needs) assert.equal(stepNeedsMet(step, EMPTY_VIEW), false, `${chapter.id}/${step.id} needs ${step.needs}`);
    else assert.equal(stepNeedsMet(step, EMPTY_VIEW), true);
  }
  const sample = { source: "a", name: "a", images: [], pages_before: 1, pages_after: 1, passed: true, failures: [], geometry: null };
  const full = normalizeView({
    ...EMPTY_VIEW,
    samples: [sample],
    plan: { groups: [], targets: [], set_aside: [{ path: "e", name: "e", reason: "x" }], stages: 1 },
    left_untouched: [{ path: "b", name: "b", reason: "x" }],
    action: { label: "Create", token: "t", kind: "approve", can_hold: true },
    occurrences: [{ source: "a", name: "a", status: "planned", reason: "", items: [] }],
    results: [{ source: "a", output: "c", name: "a" }],
    working: { ...EMPTY_VIEW.working, generation: 1, done: [{ request: "r", recipe: "", documents: 1, of: 1, not: [] }] },
    todo: [{ path: "d", name: "d", why: "held", reason: "x" }],
    stage: "question",
    questions: ["Which?"],
  });
  for (const { step } of steps) assert.equal(stepNeedsMet(step, full), true, step.id);
});

test("the conversation's controls are held still during the tour, in one wording", () => {
  assert.match(TOUR_HOLD, /recording/);
  const holds: Array<[string, RegExp[]]> = [
    ["../src/components/Composer.tsx", [/tourActive/, /disabled=\{!canSend \|\| !value\.trim\(\) \|\| tourActive\}/, /TOUR_HOLD/]],
    ["../src/components/Prompts.tsx", [/const held = busy \|\| tourActive;/, /TOUR_HOLD/]],
    ["../src/components/TodoCard.tsx", [/const held = busy \|\| tourActive;/, /TOUR_HOLD/]],
    ["../src/components/PauseCard.tsx", [/disabled=\{!canContinue \|\| busy \|\| tourActive\}/, /TOUR_HOLD/]],
    ["../src/components/Header.tsx", [/disabled=\{busy \|\| tourActive \|\| !!modeHeld\(view, mode\)\}/, /onStartTour/]],
    ["../src/components/Chat.tsx", [/disabled=\{busy \|\| tourActive\}/, /TOUR_HOLD/, /data-tour="tour-chip"/, /chip-pulse/]],
  ];
  for (const [path, patterns] of holds) {
    const text = read(path);
    for (const pattern of patterns) assert.match(text, pattern, `${path} ${pattern}`);
  }
  // The server refuses a send or choice in the practice workspace too; the page's hold is the first line.
  assert.match(tutorialPy, /if action in \("send", "choose"\):\s*\n\s*raise ValueError\(NO_CONVERSATION\)/);
  // New conversation ends a running tour first.
  const app = read("../src/App.tsx");
  assert.match(app, /if \(tourActive\) \{\s*void tour\.abort\(\)/);
  // The X marks on the user's own previews survive the tour: set aside as it begins, put back with the same previews.
  assert.match(app, /tourReviewsRef\.current = \{ key: keyRef\.current, reviews: reviewsRef\.current \}/);
  assert.match(app, /const kept = keptAcrossTour\(tourReviewsRef\.current, keyRef\.current\);\s*tourReviewsRef\.current = null;\s*if \(kept\) setReviews\(kept\);/);
});

test("the tour's dialogs carry the marker its Escape guard reads, and the chip's pulse honours reduced motion", () => {
  const overlay = read("../src/components/TourOverlay.tsx");
  assert.match(overlay, /const TOUR_DIALOG = "tour"/);
  assert.match(overlay, /marker=\{TOUR_DIALOG\}/);
  assert.match(read("../src/components/ModalShell.tsx"), /data-dialog=\{marker\}/);
  const css = read("../src/index.css");
  assert.match(css, /\.chip-pulse \{\s*animation: chip-pulse/);
  assert.match(css, /prefers-reduced-motion: reduce[\s\S]*\.chip-pulse \{\s*animation: none;/);
});

test("progress is kept under one key, per tour version, and completion is the server's setting", () => {
  const storage = read("../src/lib/tourStorage.ts");
  assert.match(storage, /PROGRESS_KEY = "spec-surgeon:tour-progress"/);
  assert.match(storage, /expectedVersion/);
  const hook = read("../src/lib/useTour.ts");
  assert.match(hook, /saveTutorialVersion\(TOUR_COMPLETION_VERSION\)/);
  assert.match(hook, /clearTourProgress\(\)/);
  assert.match(read("../src/lib/api.ts"), /tutorial_version: version/);
  assert.match(read("../../src/spec_surgeon/settings.py"), /tutorial_version: int = 0/);
});

test("Help's How to use lists every chapter as a way in, and the top bar and the empty conversation start the tour", () => {
  const help = read("../src/components/HelpModal.tsx");
  assert.match(help, /TOUR\.map\(\(chapter, index\) =>/);
  assert.match(help, /onStartTourAt\(chapter\.id\)/);
  assert.match(help, /data-capability="tour\.start"/);
  assert.match(read("../src/components/Header.tsx"), /data-capability="tour\.start"/);
  assert.match(read("../src/components/Chat.tsx"), /data-capability="tour\.start"/);
});

test("the words stay short, quote no typed number, and the recorded chapters say so", () => {
  const typed = /\$\d|\b\d+ (MB|hours|times|files|documents)\b/;
  for (const { chapter, step } of steps) {
    assert.ok(step.body.length <= 700, `${chapter.id}/${step.id}: ${step.body.length} characters`);
    assert.ok(step.title.length <= 60, `${chapter.id}/${step.id}: title`);
    if (step.mode === "optional") assert.ok(step.optionalReason, `${chapter.id}/${step.id}: an optional step says why`);
  }
  const manifest = read("../src/lib/tour.ts");
  const bodies = manifest.match(/body:\s*(?:"[^"]*"|`[^`]*`)/g) ?? [];
  for (const body of bodies) {
    if (body.startsWith("body: \"")) assert.doesNotMatch(body, typed, `a number typed into the copy: ${body.slice(0, 80)}`);
  }
  for (const key of ["spendingCheckDefaultUsd", "fastSpeedup", "maxDocxMB", "sampleFiles", "updateCheckHours"] as const) {
    assert.ok(TRUST_FACTS[key] !== undefined);
    assert.match(manifest, new RegExp(`F\\.${key}\\b`), `the tour quotes ${key} from the trust facts`);
  }
  const recorded = TOUR.filter((c) => !["start", "button", "copies"].includes(c.scenario));
  assert.ok(recorded.some((c) => c.steps.some((s) => /recording|bundled example/.test(s.body))));
});
