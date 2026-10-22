/**
 * What Settings says about fast mode (lib/fastMode.ts): both sides, from the server's own price table — the speed-up,
 * then the prices side by side — a sentence when the setting cannot work, and a confirming button that names the price.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { fastWords, times, usd } from "../src/lib/fastMode.ts";
import type { FastOffer } from "../src/types.ts";

const OFFER: FastOffer = {
  model: "claude-opus-5-5",
  available: true,
  speedup: 2.5,
  price_factor: 2,
  standard_usd_per_mtok: [4, 20],
  fast_usd_per_mtok: [8, 40],
  refused: null,
};

test("the benefit names the model and the speed-up, and says the checks do not change", () => {
  const w = fastWords(OFFER);
  assert.match(w.benefit, /Claude Opus 5\.5 writes its answers up to 2\.5× faster/);
  assert.match(w.benefit, /checked, previewed and verified exactly as before/);
});

test("the price is the server's: both prices side by side, and the multiple in words", () => {
  const w = fastWords(OFFER);
  assert.match(w.cost, /^Every AI step costs twice as much:/);
  assert.match(w.cost, /\$8 instead of \$4 per million input tokens/);
  assert.match(w.cost, /\$40 instead of \$20 per million output tokens/);
  assert.match(w.cost, /about \$1 costs about \$2\./);
  assert.equal(w.confirm, "Turn on — twice the price");
  assert.equal(w.unavailable, "");
  const pricier = fastWords({ ...OFFER, price_factor: 3, fast_usd_per_mtok: [12, 60] });
  assert.match(pricier.cost, /costs 3 times as much: \$12 instead of \$4/);
  assert.equal(pricier.confirm, "Turn on — 3 times the price");
});

test("a model that is not served fast, and a key that was refused, are said plainly", () => {
  const sonnet = fastWords({ ...OFFER, model: "claude-sonnet-5-5", available: false, fast_usd_per_mtok: null });
  assert.match(sonnet.unavailable, /Claude Sonnet 5\.5 is not served fast/);
  assert.match(sonnet.cost, /^Every AI step costs twice as much: A conversation/);   // no prices it does not have
  const refused = fastWords({ ...OFFER, refused: "PermissionDeniedError: speed not allowed · HTTP 403" });
  assert.match(refused.unavailable, /refused fast mode for this API key/);
  assert.match(refused.unavailable, /speed not allowed/);
});

test("the notes say what it does not speed up and how it behaves when fast capacity is busy", () => {
  const notes = fastWords(OFFER).notes.join(" ");
  assert.match(notes, /not the work done on your computer/);
  assert.match(notes, /runs at standard speed and standard price instead of waiting/);
  assert.match(notes, /cached separately/);
});

test("money and multiples read as words", () => {
  assert.equal(usd(8), "$8");
  assert.equal(usd(0.4), "$0.40");
  assert.equal(times(2), "twice");
  assert.equal(times(2.5), "2.5 times");
});
