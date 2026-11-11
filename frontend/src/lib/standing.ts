/**
 * Where each document stands (docs/WEB_API.md, `standing`): the words and marks of the grid the workbench shows —
 * every operation asked for across, every document down, each cell done, failed or not run. The server keeps the
 * record and words every reason; this file only reads it.
 */
import type { Mode, Standing, StandingAttempt, StandingCell, StandingOperation, StandingRow, View } from "../types.ts";

export const EMPTY_STANDING: Standing = { operations: [], documents: [], owed: { cells: 0, documents: 0 } };

const STATES = ["done", "failed", "not_run"] as const;
const MODES = ["safe", "default", "obliterate"] as const;

function attemptOf(raw: unknown): StandingAttempt | null {
  const a = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const text = (value: unknown) => (typeof value === "string" ? value : "");
  if (!STATES.includes(a.state as StandingAttempt["state"])) return null;
  return {
    state: a.state as StandingAttempt["state"],
    mode: MODES.includes(a.mode as Mode) ? (a.mode as Mode) : "",
    why: text(a.why),
    reason: text(a.reason),
    marks: Array.isArray(a.marks) ? a.marks.filter((m): m is string => typeof m === "string") : [],
    note: text(a.note),
    first: text(a.first),
  };
}

/**
 * The record as the server sent it, or an empty one: from an older server (which sends none), or when what came does
 * not read as a grid — an operation without its id, a document whose cells are not one per operation. A grid that
 * cannot be trusted to line up is not drawn.
 */
export function normalizeStanding(raw: unknown): Standing {
  const s = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  if (!Array.isArray(s.operations) || !Array.isArray(s.documents)) return EMPTY_STANDING;
  const operations: StandingOperation[] = [];
  for (const [i, value] of s.operations.entries()) {
    const op = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    if (typeof op.id !== "string" || !op.id || !MODES.includes(op.mode as Mode)) return EMPTY_STANDING;
    operations.push({
      id: op.id,
      number: i + 1,
      request: typeof op.request === "string" ? op.request : "",
      mode: op.mode as Mode,
      recipe: typeof op.recipe === "string" ? op.recipe : "",
      name: typeof op.name === "string" ? op.name : "",
      done: 0,
      failed: 0,
    });
  }
  const documents: StandingRow[] = [];
  for (const value of s.documents) {
    const row = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
    if (typeof row.path !== "string" || !Array.isArray(row.cells) || row.cells.length !== operations.length) return EMPTY_STANDING;
    const cells: StandingCell[] = [];
    for (const raw of row.cells) {
      const cell = attemptOf(raw);
      if (!cell) return EMPTY_STANDING;
      const attempts = Array.isArray((raw as Record<string, unknown>).attempts) ? ((raw as Record<string, unknown>).attempts as unknown[]) : [];
      cells.push({ ...cell, attempts: attempts.map(attemptOf).filter((a): a is StandingAttempt => a !== null) });
    }
    documents.push({
      path: row.path,
      name: typeof row.name === "string" && row.name ? row.name : row.path,
      cells,
      owed: cells.filter((c) => c.state === "failed").length,
      badges: Array.isArray(row.badges) ? row.badges.filter((b): b is string => typeof b === "string") : [],
    });
  }
  for (const [i, op] of operations.entries()) {
    op.done = documents.filter((row) => row.cells[i].state === "done").length;
    op.failed = documents.filter((row) => row.cells[i].state === "failed").length;
  }
  return {
    operations,
    documents,
    owed: { cells: documents.reduce((n, row) => n + row.owed, 0), documents: documents.filter((row) => row.owed > 0).length },
  };
}

/** The record of the view; an empty one from an older server. */
export function standingOf(view: Pick<View, "standing">): Standing {
  return view.standing ?? EMPTY_STANDING;
}

/** How a cell looks: done, done with a mark (UNVERIFIED, UNPROVEN), failed, or not run. */
export type CellTone = "done" | "marked" | "failed" | "none";

export function cellTone(cell: Pick<StandingAttempt, "state" | "marks">): CellTone {
  if (cell.state === "done") return cell.marks.length > 0 ? "marked" : "done";
  return cell.state === "failed" ? "failed" : "none";
}

/** The mark a cell shows. */
export function cellSymbol(cell: Pick<StandingAttempt, "state">): string {
  return cell.state === "done" ? "✓" : cell.state === "failed" ? "✗" : "–";
}

export function modeName(mode: Mode | ""): string {
  return mode === "safe" ? "Safe" : mode === "obliterate" ? "Obliterate" : mode === "default" ? "Default" : "";
}

/** UNVERIFIED when a mark names a failed check, UNPROVEN when every mark is that one; "" with no mark. */
export function markName(marks: readonly string[]): string {
  if (marks.length === 0) return "";
  return marks.every((m) => m.includes("UNPROVEN")) ? "UNPROVEN" : "UNVERIFIED";
}

/** One attempt in a sentence: what happened, in which mode, and why. */
export function attemptLine(attempt: StandingAttempt): string {
  const mode = modeName(attempt.mode);
  const where = mode ? ` in ${mode} mode` : "";
  if (attempt.state === "done") {
    const mark = markName(attempt.marks);
    const failed = attempt.marks.filter((m) => !m.includes("UNPROVEN"));
    return (
      `Done${where}` +
      (attempt.note ? ` (${attempt.note})` : "") +
      (mark === "UNVERIFIED" ? `, delivered UNVERIFIED: ${failed.join(", ")} failed` : mark ? ", marked UNPROVEN: what changed was shown to you, not proven" : "") +
      "." +
      (attempt.first ? ` It failed first and a second check fixed it: ${attempt.first}` : "")
    );
  }
  if (attempt.state === "failed") return `Not done${where}. ${attempt.reason}`.trim();
  return `Not run${where}. ${attempt.reason}`.trim();
}

/** What a cell's button says to a screen reader and on hover: the document, the operation, and how it stands. */
export function cellLabel(row: Pick<StandingRow, "name">, operation: Pick<StandingOperation, "number" | "request">, cell: StandingCell): string {
  const state = cell.state === "done" ? (cell.marks.length ? `done, ${markName(cell.marks)}` : "done") : cell.state === "failed" ? "not done" : "not run";
  return `${row.name}, ${operation.number}. ${operation.request}: ${state}`;
}

/** The line under the grid: what is still owed. */
export function owedLine(standing: Pick<Standing, "owed" | "operations">): string {
  const { cells, documents } = standing.owed;
  if (cells === 0) return "Nothing is owed: nothing that was run on a document failed.";
  return `Still owed: ${cells} ${cells === 1 ? "operation" : "operations"} on ${documents} ${documents === 1 ? "document" : "documents"}.`;
}

/** The rows the grid lists: every document, or only those that still owe something. */
export function visibleRows(standing: Pick<Standing, "documents">, owedOnly: boolean): StandingRow[] {
  return owedOnly ? standing.documents.filter((row) => row.owed > 0) : standing.documents;
}

/** A column's heading: the saved recipe's name, or the request, cut to fit. */
export function operationTitle(operation: Pick<StandingOperation, "request" | "name">, limit = 44): string {
  const text = (operation.name || operation.request || "(no request kept)").trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trimEnd()}…` : text;
}
