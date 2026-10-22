/**
 * What the page tells the app about itself (lib/diagnostics.ts): anything thrown described, the same error sent once
 * and then counted, batches the server takes, a send that fails dropped rather than retried, long tasks tallied, and
 * the window's events wired and undone.
 */
import assert from "node:assert/strict";
import test from "node:test";

import {
  BEAT_MS,
  MAX_BATCH,
  MAX_QUEUE,
  REPEAT_WINDOW_MS,
  createReporter,
  createTally,
  describeFailure,
  installPageDiagnostics,
  reportPage,
  type Report,
} from "../src/lib/diagnostics.ts";

test("anything thrown becomes a message and a stack", () => {
  const e = new TypeError("x is undefined");
  const d = describeFailure(e);
  assert.equal(d.message, "TypeError: x is undefined");
  assert.ok(d.stack.includes("x is undefined"));
  assert.deepEqual(describeFailure("plain words"), { message: "plain words", stack: "" });
  assert.equal(describeFailure({ message: "from an object" }).message, "from an object");
  assert.equal(describeFailure({ code: 7 }).message, '{"code":7}');
  assert.equal(describeFailure(undefined).message, "undefined");
  assert.equal(describeFailure(new RangeError("")).message, "RangeError");
  const cyclic: Record<string, unknown> = {};
  cyclic.self = cyclic;
  assert.equal(describeFailure(cyclic).message, "(a failure that could not be described)");
  assert.ok(describeFailure("x".repeat(5000)).message.length <= 2000);
});

function harness() {
  const sent: Report[][] = [];
  let t = 0;
  let fail = false;
  const reporter = createReporter({
    send: async (reports) => {
      if (fail) throw new Error("app gone");
      sent.push(reports);
    },
    now: () => t,
  });
  return {
    reporter,
    sent,
    advance: (ms: number) => (t += ms),
    failing: (on: boolean) => (fail = on),
  };
}

test("the same error is sent once, then counted and sent again with its count", async () => {
  const h = harness();
  const boom: Report = { kind: "error", message: "boom", source: "index.js", line: 3, column: 7 };
  for (let i = 0; i < 60; i += 1) h.reporter.report(boom);
  h.reporter.report({ ...boom, line: 4 });              // another place: another error
  await h.reporter.flush();
  assert.deepEqual(
    h.sent.flat().map((r) => [r.line, r.count]),
    [
      [3, 1],
      [4, 1],
    ],
  );
  h.advance(REPEAT_WINDOW_MS - 1);
  await h.reporter.flush();
  assert.equal(h.sent.flat().length, 2);                 // not yet due
  h.advance(1);
  await h.reporter.flush();
  assert.deepEqual(h.sent.flat().slice(2).map((r) => [r.line, r.count]), [[3, 59]]);
  h.advance(REPEAT_WINDOW_MS);
  h.reporter.report(boom);
  await h.reporter.flush();
  assert.deepEqual(h.sent.flat().slice(3).map((r) => [r.line, r.count]), [[3, 1]]);
});

test("other reports are never folded together, and go in batches the server takes", async () => {
  const h = harness();
  for (let i = 0; i < MAX_BATCH + 5; i += 1) h.reporter.report({ kind: "beat", visible: true });
  await h.reporter.flush();
  assert.deepEqual(h.sent.map((b) => b.length), [MAX_BATCH, 5]);
  assert.equal(h.reporter.pending().length, 0);
});

test("a send that fails is dropped, never retried in a loop, and the queue is bounded", async () => {
  const h = harness();
  h.failing(true);
  for (let i = 0; i < MAX_QUEUE + 30; i += 1) h.reporter.report({ kind: "visibility", visible: i % 2 === 0 });
  assert.equal(h.reporter.pending().length, MAX_QUEUE);
  await h.reporter.flush();
  assert.equal(h.reporter.pending().length, MAX_QUEUE - MAX_BATCH);
  h.failing(false);
  await h.reporter.flush();
  assert.equal(h.reporter.pending().length, 0);
});

test("long tasks are tallied between beats", () => {
  const tally = createTally();
  tally.add(60);
  tally.add(2400.4);
  tally.add(0);
  tally.add(Number.NaN);
  assert.deepEqual(tally.take(), { count: 2, total_ms: 2460, max_ms: 2400 });
  assert.deepEqual(tally.take(), { count: 0, total_ms: 0, max_ms: 0 });
});

/** Just enough of a window for installPageDiagnostics. */
function fakeWindow() {
  const target = new EventTarget();
  const doc = Object.assign(new EventTarget(), { visibilityState: "visible" });
  const timers = new Map<number, () => void>();
  const once = new Set<number>();
  let next = 1;
  const win = Object.assign(target, {
    document: doc,
    navigator: { userAgent: "Mozilla/5.0 Edg/141.0" },
    innerWidth: 1440,
    innerHeight: 900,
    performance: { now: () => 0 },
    matchMedia: () => ({ matches: false }),
    setTimeout: (fn: () => void) => {
      timers.set(next, fn);
      once.add(next);
      return next++;
    },
    clearTimeout: (id: number) => timers.delete(id),
    setInterval: (fn: () => void, ms: number) => {
      assert.equal(ms, BEAT_MS);
      timers.set(next, fn);
      return next++;
    },
    clearInterval: (id: number) => timers.delete(id),
  });
  const runTimers = () => {
    for (const [id, fn] of [...timers]) {
      if (once.delete(id)) timers.delete(id);
      fn();
    }
  };
  return { win: win as unknown as Window, doc, timers, runTimers };
}

test("the window's load, errors, visibility, beat and unload are reported, and undone on dispose", async () => {
  const { win, doc, timers, runTimers } = fakeWindow();
  const sent: Array<{ reports: Report[]; keepalive: boolean }> = [];
  const { dispose } = installPageDiagnostics(win, {
    send: async (reports, keepalive) => {
      sent.push({ reports, keepalive });
    },
    version: "1.8.1",
  });
  await Promise.resolve();
  const loaded = sent[0].reports[0];
  assert.equal(loaded.kind, "loaded");
  assert.equal(loaded.user_agent, "Mozilla/5.0 Edg/141.0");
  assert.equal(loaded.viewport, "1440x900");
  assert.equal(loaded.version, "1.8.1");

  const error = Object.assign(new Event("error"), {
    message: "TypeError: boom",
    error: new TypeError("boom"),
    filename: "assets/index.js",
    lineno: 3,
    colno: 7,
  });
  win.dispatchEvent(error);
  win.dispatchEvent(Object.assign(new Event("unhandledrejection"), { reason: "nobody caught me" }));
  reportPage({ kind: "stream", action: "send", reason: "cut" });
  runTimers();                                            // the flush timer, and the beat
  await new Promise((r) => setTimeout(r, 0));
  const kinds = sent.flatMap((s) => s.reports.map((r) => r.kind));
  assert.deepEqual(kinds.slice(1, 5), ["error", "rejection", "stream", "beat"]);
  const sentError = sent.flatMap((s) => s.reports).find((r) => r.kind === "error")!;
  assert.equal(sentError.source, "assets/index.js");
  assert.equal(sentError.line, 3);

  doc.visibilityState = "hidden";
  doc.dispatchEvent(new Event("visibilitychange"));
  win.dispatchEvent(new Event("pagehide"));
  await new Promise((r) => setTimeout(r, 0));
  const last = sent.at(-1)!;
  assert.ok(sent.some((s) => s.reports.some((r) => r.kind === "visibility" && r.visible === false)));
  assert.equal(last.reports.at(-1)!.kind, "unload");
  assert.equal(last.keepalive, true);

  dispose();
  assert.equal(timers.size, 0);
  const before = sent.length;
  win.dispatchEvent(error);
  reportPage({ kind: "stream", action: "send", reason: "cut" }, true);
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(sent.length, before);                      // nothing reported once undone
});
