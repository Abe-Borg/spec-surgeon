/**
 * Help → Workflows → Typical operations (lib/typicalOperations.ts): every row is complete, every verdict starts
 * with a word the colour rule knows, and every Safe row names real buttons — the labels of mock/recipes.json,
 * which tests/test_recipes.py pins to the app's catalog.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_LIMITS,
  DEFAULT_OPERATIONS,
  EVERY_MODE,
  OBLITERATE_INTRO,
  OBLITERATE_OPERATIONS,
  SAFE_LIMITS,
  SAFE_OPERATIONS,
  TYPICAL_INTRO,
  verdictTone,
} from "../src/lib/typicalOperations.ts";

const catalog = JSON.parse(readFileSync(new URL("../mock/recipes.json", import.meta.url), "utf8")) as {
  recipes: { label: string }[];
};
const buttons = new Set(catalog.recipes.map((r) => r.label));

test("every row has a task, a verdict and a note", () => {
  for (const rows of [SAFE_OPERATIONS, DEFAULT_OPERATIONS, OBLITERATE_OPERATIONS]) {
    assert.ok(rows.length > 0);
    for (const row of rows) {
      assert.ok(row.task.trim() && row.can.trim() && row.note.trim(), row.task);
      assert.match(row.can, /^(Yes|Partly|No)\b/, row.can);
    }
  }
});

test("a Safe row names the buttons that do it, by their labels in the catalog", () => {
  for (const row of SAFE_OPERATIONS) {
    assert.ok(row.buttons && row.buttons.length > 0, row.task);
    for (const b of row.buttons) assert.ok(buttons.has(b), `${row.task}: no button “${b}”`);
  }
  for (const row of [...DEFAULT_OPERATIONS, ...OBLITERATE_OPERATIONS]) assert.equal(row.buttons, undefined, row.task);
});

test("the verdict's colour follows its first word", () => {
  assert.equal(verdictTone("Yes"), "ok");
  assert.equal(verdictTone("Yes, narrowly"), "ok");
  assert.equal(verdictTone("Partly"), "warn");
  assert.equal(verdictTone("No, by design"), "err");
});

test("the words around the tables say what the lists are not, and what Obliterate also does", () => {
  assert.match(TYPICAL_INTRO, /not an exhaustive list/);
  assert.match(OBLITERATE_INTRO, /everything Safe and Default modes can do/);
  for (const text of [SAFE_LIMITS, DEFAULT_LIMITS, EVERY_MODE]) assert.ok(text.trim());
});
