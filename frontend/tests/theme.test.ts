/**
 * The colour schemes (lib/theme.ts) against the stylesheet that draws them:
 * every scheme redefines every colour token, the picker's swatches are the
 * scheme's own colours, the query string carries the choice, and no
 * component names a colour of its own (a literal would ignore the scheme).
 */
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import test from "node:test";

import { DEFAULT_THEME, THEMES, resolveTheme, searchWithTheme, themeFromSearch } from "../src/lib/theme.ts";

const CSS = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");

/** `--color-name: value;` pairs of one block's body. */
function tokens(body: string): Map<string, string> {
  return new Map([...body.matchAll(/--color-([a-z-]+):\s*([^;]+);/g)].map((m) => [m[1], m[2].trim()]));
}

function block(selector: string): string {
  const start = CSS.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `no ${selector} block in index.css`);
  return CSS.slice(start, CSS.indexOf("}", start));
}

const BASE = tokens(block("@theme"));

function schemeTokens(id: string): Map<string, string> {
  return id === DEFAULT_THEME ? BASE : tokens(block(`:root[data-theme="${id}"]`));
}

test("the schemes have unique ids and Warm Clay comes first, as the default", () => {
  assert.equal(new Set(THEMES.map((t) => t.id)).size, THEMES.length);
  assert.equal(DEFAULT_THEME, "clay");
  assert.equal(THEMES.length, 5);
});

test("every other scheme redefines every colour token of @theme, and only those", () => {
  assert.ok(BASE.size >= 20);
  for (const t of THEMES.filter((t) => t.id !== DEFAULT_THEME)) {
    const own = schemeTokens(t.id);
    assert.deepEqual([...own.keys()].sort(), [...BASE.keys()].sort(), t.id);
    assert.match(block(`:root[data-theme="${t.id}"]`), /color-scheme:\s*(dark|light);/, t.id);
  }
  const blocks = [...CSS.matchAll(/:root\[data-theme="([a-z]+)"\]/g)].map((m) => m[1]);
  assert.deepEqual(blocks.sort(), THEMES.filter((t) => t.id !== DEFAULT_THEME).map((t) => t.id).sort());
});

test("each card in the picker shows its scheme's own background, paper and accent", () => {
  for (const t of THEMES) {
    const own = schemeTokens(t.id);
    assert.deepEqual(t.swatch, { bg: own.get("bg"), paper: own.get("paper"), accent: own.get("accent") }, t.id);
  }
});

test("the query string's theme is read, defaulted and replaced without losing the token", () => {
  assert.equal(themeFromSearch("?token=t&api=http://127.0.0.1:9&theme=carbon"), "carbon");
  assert.equal(themeFromSearch("?token=t"), DEFAULT_THEME);
  assert.equal(themeFromSearch("theme=dark"), DEFAULT_THEME);
  assert.equal(resolveTheme(7), DEFAULT_THEME);
  assert.equal(resolveTheme("linen"), "linen");
  const next = new URLSearchParams(searchWithTheme("?token=t&theme=clay", "blueprint"));
  assert.equal(next.get("token"), "t");
  assert.equal(next.get("theme"), "blueprint");
  assert.equal(new URLSearchParams(searchWithTheme("", "linen")).get("theme"), "linen");
});

test("no component names a colour of its own", () => {
  const dir = new URL("../src/components/", import.meta.url);
  const files = readdirSync(dir).filter((f) => f.endsWith(".tsx")).map((f) => new URL(f, dir));
  files.push(new URL("../src/App.tsx", import.meta.url));
  const literal = /#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3}(?:[0-9a-fA-F]{2})?)?\b|\b(?:rgba?|hsla?)\(/;
  for (const file of files) {
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, i) => assert.doesNotMatch(line, literal, `${file.pathname.split("/").pop()}:${i + 1}`));
  }
});
