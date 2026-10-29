/**
 * The window's modes on the page (lib/modes.ts, lib/view.ts): Obliterate is chosen only through its warning, kept
 * apart from the conversation's other mode nowhere else, and a copy delivered with failed checks says so.
 */
import assert from "node:assert/strict";
import test from "node:test";

import type { Mode } from "../src/types.ts";
import {
  MODE_LABELS,
  MODE_TIPS,
  OBLITERATE_ACKNOWLEDGE,
  OBLITERATE_COSTS,
  OBLITERATE_GRANTS,
  OBLITERATE_KEPT,
  converses,
  modeChoice,
  modeHeld,
  unverifiedLine,
} from "../src/lib/modes.ts";
import { normalizeView } from "../src/lib/view.ts";

const MODES: Mode[] = ["safe", "default", "obliterate"];

test("every mode has a label and a tip", () => {
  for (const mode of MODES) {
    assert.ok(MODE_LABELS[mode]);
    assert.ok(MODE_TIPS[mode].startsWith(`${MODE_LABELS[mode]} mode:`));
  }
});

test("Obliterate opens its warning from any mode; the others switch at once", () => {
  assert.equal(modeChoice("default", "obliterate"), "warn");
  assert.equal(modeChoice("safe", "obliterate"), "warn");
  assert.equal(modeChoice("obliterate", "obliterate"), "none");
  assert.equal(modeChoice("obliterate", "default"), "switch");
  assert.equal(modeChoice("safe", "default"), "switch");
  assert.equal(modeChoice("default", "safe"), "switch");
  assert.equal(converses("safe"), false);
  assert.equal(converses("default"), true);
  assert.equal(converses("obliterate"), true);
});

test("the warning names what it grants, what it costs and what still holds", () => {
  assert.equal(OBLITERATE_GRANTS.length, 3);
  assert.ok(OBLITERATE_GRANTS.some((g) => g.includes("wording")));
  assert.ok(OBLITERATE_GRANTS.some((g) => g.includes("custom method")));
  assert.ok(OBLITERATE_GRANTS.some((g) => g.includes("UNVERIFIED")));
  assert.ok(OBLITERATE_COSTS.length >= 3);
  assert.ok(OBLITERATE_KEPT.some((k) => k.includes("originals are never written")));
  assert.ok(OBLITERATE_ACKNOWLEDGE.startsWith("I understand"));
  // the copy never types a number itself (trustFacts.ts holds them)
  for (const line of [...OBLITERATE_GRANTS, ...OBLITERATE_COSTS, ...OBLITERATE_KEPT]) assert.doesNotMatch(line, /\d/);
});

test("the view keeps Obliterate and a copy's failed checks; an unknown mode reads as Default", () => {
  const v = normalizeView({
    mode: "obliterate",
    modes: ["safe", "default", "obliterate", "reckless"],
    results: [{ source: "a", output: "b", name: "b", unverified: ["V2 (Text preservation)"] }],
  });
  assert.equal(v.mode, "obliterate");
  assert.deepEqual(v.modes, ["safe", "default", "obliterate"]);
  assert.deepEqual(v.results[0].unverified, ["V2 (Text preservation)"]);
  assert.equal(normalizeView({ mode: "reckless" }).mode, "default");
  assert.equal(
    unverifiedLine(["V2 (Text preservation)", "V10 (Property assertions)"]),
    "UNVERIFIED — V2 (Text preservation); V10 (Property assertions) failed. Check it in Word before you use it.",
  );
  assert.equal(unverifiedLine([]), "");
  assert.equal(unverifiedLine(undefined), "");
});

test("while a run's saved copies wait on its recovery, the switches that would end the check wait with its reason", () => {
  const why = "Some copies from this request are saved while the rest are checked again.";
  const view = (mode: Mode, recovery_blocked = why) => ({ mode, recovery_blocked });
  // across Obliterate the plan is void, the check with it; Safe mode cannot go on with a check
  assert.equal(modeHeld(view("default"), "obliterate"), why);
  assert.equal(modeHeld(view("default"), "safe"), why);
  assert.equal(modeHeld(view("obliterate"), "default"), why);
  assert.equal(modeHeld(view("obliterate"), "safe"), why);
  assert.equal(modeHeld(view("safe"), "obliterate"), why);
  // Safe to Default stays open (a window reopened in Safe mode goes on with the check there); the mode it is in too
  assert.equal(modeHeld(view("safe"), "default"), "");
  assert.equal(modeHeld(view("default"), "default"), "");
  for (const mode of MODES) for (const chosen of MODES) assert.equal(modeHeld(view(mode, ""), chosen), "");
  assert.equal(normalizeView({ recovery_blocked: why }).recovery_blocked, why);
  assert.equal(normalizeView({}).recovery_blocked, "");
  assert.equal(normalizeView({ recovery_blocked: 3 }).recovery_blocked, "");
});
