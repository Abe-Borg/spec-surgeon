import { useState } from "react";
import { failedCopyLine } from "../lib/modes";
import {
  attemptLine,
  cellLabel,
  cellSymbol,
  cellTone,
  modeName,
  operationTitle,
  owedLine,
  visibleRows,
  workBlocked,
  workLabel,
  type CellTone,
} from "../lib/standing";
import { TOUR_HOLD } from "../lib/tour";
import type { ObliterateTarget, Standing, View } from "../types";
import { paperBtn } from "./ModalShell";
import PaperCard from "./PaperCard";
import Tip from "./Tip";

const TONE: Record<CellTone, string> = {
  done: "border-ok/50 bg-ok/10 text-ok",
  marked: "border-warn/70 bg-warn/15 text-paper-ink",
  failed: "border-err/50 bg-err-tint text-err",
  none: "border-paper-edge/60 text-paper-faint",
};

const BADGE: Record<string, string> = {
  SALVAGED: "border-warn/60 bg-warn/15 text-paper-ink",
  UNVERIFIED: "border-err/50 bg-err-tint text-err",
  UNPROVEN: "border-warn/60 bg-warn/15 text-paper-ink",
  "set aside": "border-paper-edge text-paper-dim",
};

const BADGE_TIP: Record<string, string> = {
  SALVAGED: "The app rescued this document from a file it could not read as it was; nothing proves it holds everything that file did",
  UNVERIFIED: "A copy of it was delivered with a failed check; the failed checks are beside its copy",
  UNPROVEN: "A copy of it was made in Obliterate mode; what changed was shown to you, not proven",
  "set aside": "You set it aside: it waits out of every plan until you work on it or put it back",
};

const linkBtn =
  "text-xs text-paper-ink underline decoration-paper-faint underline-offset-2 transition-colors " +
  "hover:decoration-paper-ink disabled:pointer-events-none disabled:opacity-50";

interface Props {
  standing: Standing;
  view: Pick<View, "mode" | "busy" | "focus" | "stage" | "recovery_blocked">;
  /** The guided tour runs: the pass is not begun and nothing is changed (the tour does what its chapters need). */
  tourActive?: boolean;
  /** Work on it: the pass over one failed cell, or (with only an operation) over every document that owes it. */
  onWork: (operation?: string, path?: string) => void;
  /** Not needed on this one (`on`), or back on the to-do list. */
  onWaive: (operation: string, path: string, on: boolean) => void;
  /** A failed cell's Obliterate this one (offered only while Settings shows Obliterate mode). */
  onObliterate: (target: ObliterateTarget) => void;
  /** A document the user set aside, back among the documents the next request works on. */
  onPutBack: (path: string) => void;
}

/**
 * Where each document stands: every operation asked for across (a Safe-mode button, a request, a saved recipe's
 * run), every document down, and in each cell whether that document got it — done (and the mode that did it), not
 * done (the reason kept, however many buttons came after) or not run on it. The crosses are the to-do list: a mark
 * opens its reason, every attempt made, and what can be done about it — work on it (in Default mode from Safe),
 * Obliterate this one, or mark it as not needed on this document.
 */
export default function StandingCard({ standing, view, tourActive = false, onWork, onWaive, onObliterate, onPutBack }: Props) {
  const [owedOnly, setOwedOnly] = useState(false);
  const [picked, setPicked] = useState<{ path: string; id: string } | null>(null);
  const safe = view.mode === "safe";
  const operations = standing.operations;
  const rows = visibleRows(standing, owedOnly && standing.owed.documents > 0);
  const pickedRow = picked ? rows.find((row) => row.path === picked.path) : undefined; // a row the filter hides shows no reason
  const pickedIndex = picked ? operations.findIndex((op) => op.id === picked.id) : -1;
  const pickedCell = pickedRow && pickedIndex >= 0 ? pickedRow.cells[pickedIndex] : undefined;
  const pickedOp = pickedIndex >= 0 ? operations[pickedIndex] : undefined;
  const canFilter = standing.owed.documents > 0 && standing.owed.documents < standing.documents.length;
  const blocked = workBlocked(view);
  const held = !!blocked || tourActive;
  const copyLine = pickedCell ? failedCopyLine(pickedCell.failed_copy) : "";

  return (
    <PaperCard
      title="Where each document stands"
      subtitle={
        (safe ? "Every button you ran, across; " : "Every change you asked for, across; ") +
        "every document, down. ✓ done, ✗ not done, – not run on it. The crosses are your to-do list: click one for the reason and what you can do."
      }
    >
      <div className="max-h-[28rem] overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-left">
          <thead>
            <tr>
              <th scope="col" className="sticky top-0 left-0 z-20 min-w-[9rem] bg-paper pr-3 pb-2 align-bottom text-[11px] font-medium tracking-wide text-paper-dim uppercase">
                Document
              </th>
              {operations.map((op) => (
                <th key={op.id} scope="col" title={op.request} className="sticky top-0 z-10 min-w-[7rem] max-w-[11rem] bg-paper px-2 pb-2 align-top font-normal">
                  <span className="flex items-center gap-1.5">
                    <span aria-hidden className="flex h-5 w-5 flex-none items-center justify-center rounded-full border border-paper-edge text-[10.5px] font-semibold text-paper-dim">
                      {op.number}
                    </span>
                    <span className="text-[10px] font-semibold tracking-wide text-paper-dim uppercase">{modeName(op.mode)}</span>
                  </span>
                  <span className="mt-1 line-clamp-2 block text-xs leading-snug font-medium break-words text-paper-ink">
                    {operationTitle(op)}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.path}>
                <th scope="row" title={row.path} className="sticky left-0 z-10 max-w-[14rem] border-t border-paper-edge bg-paper py-1.5 pr-3 text-left align-middle font-normal">
                  <span className="block truncate text-sm text-paper-ink">{row.name}</span>
                  {row.badges.length > 0 && (
                    <span className="mt-0.5 flex flex-wrap items-center gap-1">
                      {row.badges.map((badge) => (
                        <Tip key={badge} tip={BADGE_TIP[badge] ?? badge}>
                          <span className={`rounded-full border px-1.5 py-px text-[9.5px] font-semibold tracking-wide uppercase ${BADGE[badge] ?? "border-paper-edge text-paper-dim"}`}>
                            {badge}
                          </span>
                        </Tip>
                      ))}
                      {row.badges.includes("set aside") && (
                        <Tip tip={view.busy ? "Finish or stop the current work first" : view.recovery_blocked || "Back among the documents your next request works on, as it is; what it owes stays on your to-do list"}>
                          <button
                            type="button"
                            className={linkBtn}
                            disabled={view.busy || !!view.recovery_blocked}
                            onClick={() => onPutBack(row.path)}
                            aria-label={`Put ${row.name} back`}
                            data-capability="todo.put-back"
                          >
                            Put back
                          </button>
                        </Tip>
                      )}
                    </span>
                  )}
                </th>
                {row.cells.map((cell, i) => {
                  const op = operations[i];
                  const on = picked?.path === row.path && picked?.id === op.id;
                  return (
                    <td key={op.id} className="border-t border-paper-edge px-2 py-1.5 align-middle">
                      <button
                        type="button"
                        aria-pressed={on}
                        aria-label={cellLabel(row, op, cell)}
                        title={attemptLine(cell)}
                        onClick={() => setPicked(on ? null : { path: row.path, id: op.id })}
                        data-capability="standing.cell"
                        className={`flex h-7 min-w-[2.75rem] items-center justify-center gap-1 rounded-md border px-1.5 text-xs font-semibold transition-shadow ${TONE[cellTone(cell)]} ${on ? "ring-2 ring-accent/60" : "hover:ring-1 hover:ring-accent/40"}`}
                      >
                        <span aria-hidden>{cellSymbol(cell)}</span>
                        {cell.state === "done" && <span className="text-[10px] font-medium">{modeName(cell.mode)}</span>}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pickedRow && pickedCell && pickedOp && (
        <div className="mt-3 rounded-lg border border-paper-edge bg-white/50 px-3 py-2" role="status" aria-label="This mark's reason" data-tour="standing-detail">
          <p className="text-xs font-semibold break-words text-paper-ink">
            {pickedRow.name} · {pickedOp.number}. {pickedOp.name || pickedOp.request}
          </p>
          {pickedCell.attempts.length <= 1 ? (
            <p className="mt-1 text-xs leading-relaxed break-words text-paper-dim">{attemptLine(pickedCell.attempts[0] ?? pickedCell)}</p>
          ) : (
            <ol className="mt-1 list-decimal space-y-0.5 pl-4">
              {pickedCell.attempts.map((attempt, i) => (
                <li key={i} className="text-xs leading-relaxed break-words text-paper-dim">
                  {attemptLine(attempt)}
                </li>
              ))}
            </ol>
          )}
          {pickedCell.state === "failed" && (
            <>
              {pickedCell.obliterate !== undefined && copyLine && <p className="mt-1 text-xs leading-snug text-paper-dim">{copyLine}</p>}
              <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1.5">
                <Tip
                  tip={
                    tourActive
                      ? TOUR_HOLD
                      : blocked ||
                        (safe
                          ? "Switches to Default mode and begins the pass over this document: you say what should be different, and an AI model plans it"
                          : "Begins the pass over this document alone: tell the assistant what should be different")
                  }
                >
                  <button type="button" className={paperBtn} disabled={held} onClick={() => onWork(pickedOp.id, pickedRow.path)} data-capability="todo.work">
                    {workLabel(view.mode)}
                  </button>
                </Tip>
                {pickedOp.failed > 1 && (
                  <Tip tip={tourActive ? TOUR_HOLD : blocked || `Begins the pass over the ${pickedOp.failed} documents this was not done on`}>
                    <button type="button" className={linkBtn} disabled={held} onClick={() => onWork(pickedOp.id)} data-capability="todo.work">
                      All {pickedOp.failed} it was not done on
                    </button>
                  </Tip>
                )}
                {pickedCell.obliterate !== undefined && (
                  <Tip
                    tip={
                      tourActive
                        ? TOUR_HOLD
                        : view.busy
                          ? "Finish or stop the current work first"
                          : pickedCell.obliterate ||
                            (view.mode === "obliterate"
                              ? "Begins the pass over this one document, in Obliterate mode"
                              : "Opens Obliterate mode's warning; then the pass begins over this one document")
                    }
                  >
                    <button
                      type="button"
                      onClick={() => onObliterate({ path: pickedRow.path, name: pickedRow.name, operation: pickedOp.id, failed_copy: pickedCell.failed_copy })}
                      disabled={view.busy || tourActive || !!pickedCell.obliterate}
                      aria-label={`Obliterate ${pickedRow.name}`}
                      data-capability="todo.obliterate"
                      className="text-xs text-err underline decoration-err/40 underline-offset-2 transition-colors hover:decoration-err disabled:pointer-events-none disabled:opacity-50"
                    >
                      Obliterate this one
                    </button>
                  </Tip>
                )}
                <Tip tip={tourActive ? TOUR_HOLD : view.busy ? "Finish or stop the current work first" : "Takes it off your to-do list. Nothing in the document changes, and you can put it back"}>
                  <button type="button" className={linkBtn} disabled={view.busy || tourActive} onClick={() => onWaive(pickedOp.id, pickedRow.path, true)} data-capability="todo.waive">
                    Not needed on this one
                  </button>
                </Tip>
              </div>
            </>
          )}
          {pickedCell.state === "waived" && (
            <div className="mt-2">
              <Tip tip={tourActive ? TOUR_HOLD : view.busy ? "Finish or stop the current work first" : "Puts it back on your to-do list, as it was"}>
                <button type="button" className={linkBtn} disabled={view.busy || tourActive} onClick={() => onWaive(pickedOp.id, pickedRow.path, false)} data-capability="todo.waive">
                  Put it back on my to-do list
                </button>
              </Tip>
            </div>
          )}
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <p className={`text-xs ${standing.owed.cells > 0 ? "font-medium text-paper-ink" : "text-paper-dim"}`}>{owedLine(standing)}</p>
        {canFilter && (
          <Tip tip={owedOnly ? "Lists every document again" : "Lists only the documents with something not done"}>
            <button
              type="button"
              className={paperBtn}
              aria-pressed={owedOnly}
              onClick={() => setOwedOnly(!owedOnly)}
              data-capability="standing.filter"
            >
              {owedOnly ? "Show every document" : "Show only these"}
            </button>
          </Tip>
        )}
      </div>
    </PaperCard>
  );
}
