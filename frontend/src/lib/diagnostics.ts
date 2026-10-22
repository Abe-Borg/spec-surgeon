/**
 * What the page tells the app about itself (docs/WEB_API.md, `/api/diagnostics`;
 * the server's side is src/spec_surgeon/web/watch.py).
 *
 * The page runs in the native window's WebView2, where nobody sees its
 * console: an error it did not catch, a drawing that failed or a page that
 * froze used to leave no trace in the app's logs. It now reports its load and
 * unload, its visibility, a heartbeat every `BEAT_MS` with the long tasks that
 * blocked it, every error nobody caught, and a job stream that ended early.
 * The server journals each and watches the heartbeat: a showing page that goes
 * silent is named in the app log as frozen.
 *
 * The same error repeating (an animation frame that throws sixty times a
 * second) is sent once, then counted and sent again at most every
 * `REPEAT_WINDOW_MS` with its count. Reporting never throws and never
 * reports itself.
 *
 * Pure parts (`describeFailure`, `createReporter`, `createTally`) are pinned
 * by `node --test`; `installPageDiagnostics` wires them to the window.
 */

/** The heartbeat; web/watch.py's PAGE_BEAT_S says the same. */
export const BEAT_MS = 10_000;
/** A report waits this long for others to go with it (an error burst is one request). */
export const FLUSH_MS = 1_500;
/** The same error is sent at most this often; the repeats in between are counted. */
export const REPEAT_WINDOW_MS = 30_000;
/** Reports per request (the server takes no more). */
export const MAX_BATCH = 25;
/** Reports kept while the app cannot be reached; the oldest go first. */
export const MAX_QUEUE = 100;
/** Distinct errors remembered for counting repeats; past this the memory starts over. */
const MAX_SIGNATURES = 500;

export interface Report {
  kind: "loaded" | "unload" | "visibility" | "beat" | "error" | "rejection" | "render" | "stream";
  [field: string]: unknown;
}

const CAP = 2000;
const STACK_CAP = 8000;

function cut(text: string, n: number): string {
  return text.length <= n ? text : text.slice(0, n - 1) + "…";
}

/** A message and a stack for anything thrown: an Error, a string, an object with a message, or anything at all. */
export function describeFailure(value: unknown): { message: string; stack: string } {
  try {
    if (value instanceof Error) {
      const name = value.name || "Error";
      const head = !value.message ? name : value.message.startsWith(name) ? value.message : `${name}: ${value.message}`;
      return { message: cut(head, CAP), stack: cut(String(value.stack ?? ""), STACK_CAP) };
    }
    if (typeof value === "string") return { message: cut(value, CAP), stack: "" };
    if (value && typeof value === "object") {
      const m = (value as { message?: unknown }).message;
      if (typeof m === "string") return { message: cut(m, CAP), stack: "" };
      return { message: cut(JSON.stringify(value) ?? String(value), CAP), stack: "" };
    }
    return { message: cut(String(value), CAP), stack: "" };
  } catch {
    return { message: "(a failure that could not be described)", stack: "" };
  }
}

const REPEATING = new Set(["error", "rejection"]);

function signature(r: Report): string {
  return [r.kind, r.message, r.source, r.line, r.column].map((v) => String(v ?? "")).join("|");
}

export interface Reporter {
  /** Queue a report; a repeat of an error sent less than `REPEAT_WINDOW_MS` ago is only counted. */
  report(r: Report): void;
  /** Send what is queued (and the counts of repeats that are due); resolves when sent, or failed and dropped. */
  flush(keepalive?: boolean): Promise<void>;
  /** What is waiting to be sent. */
  pending(): readonly Report[];
}

export function createReporter(options: {
  send: (reports: Report[], keepalive: boolean) => Promise<void>;
  now: () => number;
}): Reporter {
  const queue: Report[] = [];
  const seen = new Map<string, { sentAt: number; extra: number; report: Report }>();
  let sending = false;

  const push = (r: Report) => {
    queue.push(r);
    if (queue.length > MAX_QUEUE) queue.splice(0, queue.length - MAX_QUEUE);
  };

  return {
    report(r) {
      if (!REPEATING.has(r.kind)) {
        push(r);
        return;
      }
      const key = signature(r);
      const now = options.now();
      const last = seen.get(key);
      if (last && now - last.sentAt < REPEAT_WINDOW_MS) {
        last.extra += 1;
        return;
      }
      push({ ...r, count: 1 + (last?.extra ?? 0) });
      if (seen.size >= MAX_SIGNATURES && !last) seen.clear();
      seen.set(key, { sentAt: now, extra: 0, report: r });
    },
    async flush(keepalive = false) {
      const now = options.now();
      for (const [key, last] of seen) {
        if (last.extra > 0 && now - last.sentAt >= REPEAT_WINDOW_MS) {
          push({ ...last.report, count: last.extra });
          seen.set(key, { ...last, sentAt: now, extra: 0 });
        }
      }
      if (sending && !keepalive) return;
      sending = true;
      try {
        while (queue.length > 0) {
          const batch = queue.splice(0, MAX_BATCH);
          try {
            await options.send(batch, keepalive);
          } catch {
            return; // the app is closing or gone: what was not sent is dropped, never retried in a loop
          }
        }
      } finally {
        sending = false;
      }
    },
    pending() {
      return queue;
    },
  };
}

/** The long tasks between two heartbeats. */
export function createTally() {
  let count = 0;
  let total = 0;
  let max = 0;
  return {
    add(ms: number) {
      if (!(ms > 0)) return;
      count += 1;
      total += ms;
      max = Math.max(max, ms);
    },
    take() {
      const out = { count, total_ms: Math.round(total), max_ms: Math.round(max) };
      count = total = max = 0;
      return out;
    },
  };
}

let active: { reporter: Reporter; soon: () => void } | null = null;

/** Report from anywhere in the page (the error boundary, a job stream); a no-op before installation. `now`: send at once. */
export function reportPage(r: Report, now = false): void {
  if (!active) return;
  try {
    active.reporter.report(r);
    if (now) void active.reporter.flush();
    else active.soon();
  } catch {
    // reporting never fails the page
  }
}

/**
 * Wire the reporter to the window: errors and promises nobody caught, the
 * load, visibility changes, the unload (sent with `keepalive` so it outlives
 * the page), long tasks, and the heartbeat. Returns the reporter and a
 * function that undoes it all.
 */
export function installPageDiagnostics(
  win: Window,
  options: { send: (reports: Report[], keepalive: boolean) => Promise<void>; version?: string },
): { reporter: Reporter; dispose: () => void } {
  const reporter = createReporter({ send: options.send, now: () => win.performance.now() });
  const tally = createTally();
  let timer: number | null = null;
  const soon = () => {
    if (timer !== null) return;
    timer = win.setTimeout(() => {
      timer = null;
      void reporter.flush();
    }, FLUSH_MS);
  };
  active = { reporter, soon };
  const visible = () => win.document.visibilityState === "visible";

  const onError = (e: ErrorEvent) => {
    const { message, stack } = describeFailure(e.error ?? e.message);
    reporter.report({ kind: "error", message: e.message || message, stack, source: e.filename, line: e.lineno, column: e.colno });
    soon();
  };
  const onRejection = (e: PromiseRejectionEvent) => {
    const { message, stack } = describeFailure(e.reason);
    reporter.report({ kind: "rejection", message, stack });
    soon();
  };
  const onVisibility = () => {
    reporter.report({ kind: "visibility", visible: visible() });
    void reporter.flush();
  };
  const onHide = () => {
    reporter.report({ kind: "unload", visible: false });
    void reporter.flush(true);
  };
  win.addEventListener("error", onError);
  win.addEventListener("unhandledrejection", onRejection);
  win.document.addEventListener("visibilitychange", onVisibility);
  win.addEventListener("pagehide", onHide);

  let observer: PerformanceObserver | null = null;
  try {
    if (typeof PerformanceObserver !== "undefined" && PerformanceObserver.supportedEntryTypes?.includes("longtask")) {
      observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) tally.add(entry.duration);
      });
      observer.observe({ type: "longtask", buffered: true });
    }
  } catch {
    observer = null;
  }

  let reduced = false;
  try {
    reduced = !!win.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  } catch {
    reduced = false;
  }
  reporter.report({
    kind: "loaded",
    visible: visible(),
    user_agent: win.navigator.userAgent,
    viewport: `${win.innerWidth}x${win.innerHeight}`,
    reduced_motion: reduced,
    version: options.version ?? "",
  });
  void reporter.flush();
  const beat = win.setInterval(() => {
    reporter.report({ kind: "beat", visible: visible(), long_tasks: tally.take() });
    void reporter.flush();
  }, BEAT_MS);

  return {
    reporter,
    dispose() {
      win.removeEventListener("error", onError);
      win.removeEventListener("unhandledrejection", onRejection);
      win.document.removeEventListener("visibilitychange", onVisibility);
      win.removeEventListener("pagehide", onHide);
      win.clearInterval(beat);
      if (timer !== null) win.clearTimeout(timer);
      observer?.disconnect();
      if (active?.reporter === reporter) active = null;
    },
  };
}
