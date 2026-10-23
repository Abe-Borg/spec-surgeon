/**
 * The trust surfaces (Help → Why trust it, and the "I'm not convinced" dossier it opens): every runtime card answers
 * the same five rows, every action and automatic behaviour in the claims ledger has a card, the button opens the
 * dossier, and nothing in them loads an outside asset. tests/test_trust_facts.py pins the numbers to the Python code.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { HELP_TITLES, HELP_TOPICS, modelLabel } from "../src/lib/help.ts";
import { MODEL_NAME, TRUST_FACTS } from "../src/lib/trustFacts.ts";
import { NONE, RUNTIME_CARDS } from "../src/lib/trustRuntime.ts";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
const level1 = read("../src/components/WhyTrustIt.tsx");
const dossier = read("../src/components/TrustDossier.tsx");
const diagram = read("../src/components/TrustDiagram.tsx");
const runtime = read("../src/lib/trustRuntime.ts");
const helpModal = read("../src/components/HelpModal.tsx");
const ledger = read("../../docs/TRUST_CLAIMS.md");

test("Why trust it is a help topic, with its title", () => {
  const topic = HELP_TOPICS.find((t) => t.id === "why-trust-it");
  assert.ok(topic, "the topic is listed");
  assert.equal(topic.label, "Why trust it");
  assert.equal(HELP_TITLES["why-trust-it"], "Why trust it?");
});

test("Level 1 ends with the button, and the button opens the dossier on top of help", () => {
  assert.match(level1, /Not convinced\?/);
  assert.match(level1, /Fair\. The points above are claims — here is the mechanism behind each one, action by action, plus how to\s+audit any of it yourself\./);
  assert.match(level1, /onClick=\{onDeepDive\}[\s\S]*I’m not convinced — show me exactly what runs →/);
  assert.match(helpModal, /<WhyTrustIt onDeepDive=\{\(\) => setDeepDive\(true\)\} \/>/);
  assert.match(helpModal, /<TrustDossier open=\{deepDive\} onClose=\{\(\) => setDeepDive\(false\)\} \/>/);
  // a sibling of the help dialog, so the dossier's backdrop closes only the dossier
  assert.ok(helpModal.lastIndexOf("<TrustDossier") > helpModal.lastIndexOf('role="tabpanel"'));
  // both dialogs share the one Escape and focus mechanism
  assert.match(dossier, /useDialogFocus\(open, dialogRef, closeButtonRef, onClose\)/);
  assert.match(dossier, /role="dialog"[\s\S]*aria-modal="true"[\s\S]*aria-labelledby="trust-dossier-title"/);
});

test("Level 1 has a lead and six to eight points, one of them on what runs without you", () => {
  const points = level1.match(/\bt: "/g) ?? [];
  assert.ok(points.length >= 6 && points.length <= 8, `${points.length} points`);
  assert.match(level1, /t: "What runs without you"/);
  assert.match(level1, /<Lead>/);
});

test("every runtime card answers the five rows, and says None. where no model runs", () => {
  assert.ok(RUNTIME_CARDS.length >= 12 && RUNTIME_CARDS.length <= 25, `${RUNTIME_CARDS.length} cards`);
  for (const card of RUNTIME_CARDS) {
    for (const row of ["you", "runs", "sent", "ai", "bounds"] as const) {
      assert.ok(card[row] && card[row].trim().length > 3, `${card.title}: ${row}`);
    }
    assert.ok(card.covers.length > 0, `${card.title} names what it covers`);
    if (/^None\b/.test(card.ai)) assert.ok(card.ai === NONE || card.ai.startsWith("None to "), card.title);
  }
  // the five rows, in order, in the card's markup
  assert.match(
    dossier,
    /row\("You do", card\.you\)[\s\S]*row\("What runs", card\.runs\)[\s\S]*row\("What is sent", card\.sent\)[\s\S]*row\("AI involved", card\.ai, card\.ai === NONE\)[\s\S]*row\("Bounded by", card\.bounds\)/,
  );
  assert.ok(RUNTIME_CARDS.filter((c) => c.ai === NONE).length >= 8, "None. is written out where it is true");
});

test("every action and automatic behaviour in the ledger has a card", () => {
  const inventory = new Set([...ledger.matchAll(/^\| ([AB]\d+) \|/gm)].map((m) => m[1]));
  assert.ok(inventory.size >= 30, `${inventory.size} items in the ledger's inventory`);
  const covered = new Set(RUNTIME_CARDS.flatMap((c) => c.covers));
  const missing = [...inventory].filter((id) => !covered.has(id));
  assert.deepEqual(missing, [], "inventory items without a card");
  const unknown = [...covered].filter((id) => !inventory.has(id));
  assert.deepEqual(unknown, [], "cards covering items the ledger does not list");
});

test("the dossier has its twelve sections, each with an anchor, a kicker and a title", () => {
  const sections = [...dossier.matchAll(/<Section id="([a-z]+)" kicker="([^"]+)" title="([^"]+)">/g)].map((m) => m[1]);
  const rail = [...dossier.matchAll(/\{ id: "([a-z]+)", label: "[^"]+" \}/g)].map((m) => m[1]);
  assert.deepEqual(sections, rail, "the contents rail lists every section, in order");
  assert.equal(sections.length, 12);
  for (const needed of ["Not convinced?", "Still not convinced?", "Things that are not happening", "Further reading"]) {
    if (needed === "Not convinced?") continue;
    assert.ok(dossier.includes(needed), needed);
  }
  assert.match(dossier, /title="Still not convinced\?" tone="ok">\s*Good\./);
  assert.match(dossier, /The exact claim/);
  assert.match(dossier, /leftTitle="Leaves your machine"[\s\S]*rightTitle="Stays on your machine"/);
});

test("the diagram is inline SVG, says itself in words, and dashes what is automatic or optional", () => {
  assert.match(diagram, /<svg viewBox="[\d ]+" className="w-full" role="img" aria-label=\{DIAGRAM_LABEL\}>/);
  assert.match(diagram, /strokeDasharray/);
  assert.match(diagram, /var\(--color-/);
  assert.match(dossier, /<TrustDiagram \/>/);
});

test("the trust surfaces load no outside asset: the further-reading links are the only outside references", () => {
  for (const [name, text] of [["WhyTrustIt", level1], ["TrustDossier", dossier], ["TrustDiagram", diagram], ["trustRuntime", runtime]]) {
    assert.doesNotMatch(text, /<img|<script|<link|<iframe|\bsrc=|url\((?!#)|@import|fetch\(|XMLHttpRequest|new Image|<image/, name);
    const urls = [...text.matchAll(/https?:\/\/[^\s"'`)<>]+/g)].map((m) => m[0]);
    const allowed = [
      "https://www.anthropic.com/legal/privacy",
      "https://www.anthropic.com/legal/commercial-terms",
      "https://trust.anthropic.com",
      "https://docs.claude.com",
      "https://${F.githubHost}/${F.repo}/releases",
      "https://${F.githubHost}/${F.repo}",
      "https://…",
    ];
    for (const url of urls) assert.ok(allowed.includes(url), `${name}: unexpected outside reference ${url}`);
  }
  // the outside links open in the system browser, never in the window
  assert.match(dossier, /<a href=\{href\} target="_blank" rel="noopener noreferrer"/);
});

test("the model's name in the copy is the configured model's", () => {
  assert.equal(modelLabel(TRUST_FACTS.model), MODEL_NAME);
});
