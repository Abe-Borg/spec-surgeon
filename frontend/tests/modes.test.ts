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
  failedCopyLine,
  modeChoice,
  modeHeld,
  OTHER_CHANGES_TITLE,
  UNPROVEN_MEANS,
  copyMark,
  failedMarks,
  isUnprovenMark,
  obliterateKeyNote,
  obliterateSubject,
  resultsLead,
  sampleBadge,
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

test("a copy a custom method made is UNPROVEN, never UNVERIFIED unless a check failed, and never unmarked", () => {
  const unproven = "UNPROVEN (a custom method made it: what it changed was shown to you, not proven)";
  const earlier = `in an earlier copy: ${unproven}`;
  assert.equal(isUnprovenMark(unproven), true);
  assert.equal(isUnprovenMark(earlier), true);
  assert.equal(isUnprovenMark("V1 (Package integrity)"), false);
  assert.equal(copyMark(undefined), "");
  assert.equal(copyMark([]), "");
  assert.equal(copyMark([unproven]), "UNPROVEN");
  assert.equal(copyMark([earlier]), "UNPROVEN"); // a later copy built on it keeps the mark
  assert.equal(copyMark(["V1 (Package integrity)", unproven]), "UNVERIFIED");
  assert.deepEqual(failedMarks(["V1 (Package integrity)", unproven]), ["V1 (Package integrity)"]);
  assert.equal(unverifiedLine([unproven]), `UNPROVEN — ${UNPROVEN_MEANS}. Check it in Word before you use it.`);
  assert.equal(
    unverifiedLine(["V1 (Package integrity)", unproven]),
    "UNVERIFIED — V1 (Package integrity) failed. A custom method made it, so the rest of what it changed was shown to you, not proven. Check it in Word before you use it.",
  );
  // the results card says what each mark among the copies means, and only those
  const plain = resultsLead([{ unverified: [] }, {}]);
  assert.match(plain, /^Each copy was checked against the plan, word for word\./);
  assert.doesNotMatch(plain, /UNPROVEN|UNVERIFIED/);
  const shown = resultsLead([{ unverified: [unproven] }]);
  assert.match(shown, /A copy marked UNPROVEN was made by a custom method that predicted nothing/);
  assert.doesNotMatch(shown, /UNVERIFIED|checked against the plan/);
  const mixed = resultsLead([{ unverified: [] }, { unverified: ["V2 (Text preservation)"] }, { unverified: [unproven] }]);
  assert.match(mixed, /A copy with no mark was checked against the plan\. A copy marked UNVERIFIED failed a check .* A copy marked UNPROVEN/);
  assert.match(mixed, /Your originals stay intact\. $/);
});

test("a preview card's badge never calls a custom method's Obliterate copy checked", () => {
  assert.deepEqual(
    [sampleBadge({ passed: true }).label, sampleBadge({ passed: true }).tone],
    ["Checked", "ok"],
  );
  const shown = sampleBadge({ passed: true, unproven: true });
  assert.deepEqual([shown.label, shown.tone], ["Not proven", "warn"]);
  assert.match(shown.title, /nothing proves the change is the one you asked for/);
  assert.match(shown.title, /marked UNPROVEN/);
  const broken = sampleBadge({ passed: false, unproven: true });
  assert.deepEqual([broken.label, broken.tone], ["Did not pass", "err"]);
  assert.match(broken.title, /still created, marked UNVERIFIED/);
  const none = sampleBadge({ passed: false, unproven: true, no_copy: true });
  assert.deepEqual([none.label, none.tone], ["No copy", "err"]);
  assert.match(none.title, /goes on your to-do list/);
  // Safe and Default are as they were
  assert.match(sampleBadge({ passed: false }).title, /the plan needs another look/);
  assert.match(sampleBadge({ passed: false, set_aside: true }).title, /no copy of it is made/);
  assert.ok(OTHER_CHANGES_TITLE.endsWith(":"));
  // the warning says a custom method's copies are approved as copies, and what their mark is
  assert.ok(OBLITERATE_GRANTS.some((g) => g.includes("UNPROVEN") && g.includes("predicts nothing")));
  assert.ok(OBLITERATE_KEPT.some((k) => k.includes("Create delivers those same copies")));
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

test("a to-do row offers Obliterate only when the server says so, names the copy it kept, and warns for that document", () => {
  // the server's words reach the page as they are: the row's button state and the checks the kept copy failed
  const v = normalizeView({
    todo: [
      { path: "a", name: "A.docx", why: "failed", reason: "Its preview did not pass its checks.", obliterate: "", failed_copy: ["V2 (Text preservation)"] },
      { path: "b", name: "B.docx", why: "refused", reason: "The plan could not be applied to it.", obliterate: "Finish the pass over your to-do list first, or drop it." },
      { path: "c", name: "C.docx", why: "held", reason: "You marked its preview as needing more work." },
    ],
  });
  assert.equal(v.todo[0].obliterate, "");
  assert.deepEqual(v.todo[0].failed_copy, ["V2 (Text preservation)"]);
  assert.match(v.todo[1].obliterate ?? "", /Finish the pass/);
  assert.equal(v.todo[2].obliterate, undefined); // Obliterate hidden in Settings: the row reads as it always did
  assert.equal(
    failedCopyLine(["V2 (Text preservation)"]),
    "A copy was made and failed V2 (Text preservation). Obliterate mode can take it as it is, marked UNVERIFIED.",
  );
  assert.equal(failedCopyLine([]), "");
  assert.equal(failedCopyLine(undefined), "");
  // the warning names the one document, and says what happens to the others
  assert.match(obliterateSubject("A.docx"), /^For A\.docx: Obliterate mode turns on and the pass begins over that one document\./);
  assert.match(obliterateSubject("A.docx"), /Every other document, copy and to-do entry stays as it is\./);
  // without a key the copy that failed can still be taken (no AI is used for that); anything else needs the key
  assert.equal(obliterateKeyNote(true, false), "");
  assert.equal(obliterateKeyNote(true, true), "");
  assert.equal(obliterateKeyNote(false, false), "Obliterate mode uses the AI: connect your API key in Settings first.");
  assert.match(obliterateKeyNote(false, true), /you can take the copy that failed as it is \(no AI is used for that\)/);
});
