import { useState } from "react";
import {
  attemptLine,
  cellLabel,
  cellSymbol,
  cellTone,
  modeName,
  operationTitle,
  owedLine,
  visibleRows,
  type CellTone,
} from "../lib/standing";
import type { Standing } from "../types";
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
  "set aside": "It waits on your to-do list, out of every plan, until you work on it or put it back",
};

/**
 * Where each document stands: every operation asked for across (a Safe-mode button, a request, a saved recipe's
 * run), every document down, and in each cell whether that document got it — done (and the mode that did it),
 * not done (the reason kept, however many buttons came after) or not run on it. A mark opens its reason and every
 * attempt made. Read-only: it changes nothing and decides nothing.
 */
export default function StandingCard({ standing, safe }: { standing: Standing; safe: boolean }) {
  const [owedOnly, setOwedOnly] = useState(false);
  const [picked, setPicked] = useState<{ path: string; id: string } | null>(null);
  const operations = standing.operations;
  const rows = visibleRows(standing, owedOnly && standing.owed.documents > 0);
  const pickedRow = picked ? rows.find((row) => row.path === picked.path) : undefined;     // a row the filter hides shows no reason
  const pickedIndex = picked ? operations.findIndex((op) => op.id === picked.id) : -1;
  const pickedCell = pickedRow && pickedIndex >= 0 ? pickedRow.cells[pickedIndex] : undefined;
  const canFilter = standing.owed.documents > 0 && standing.owed.documents < standing.documents.length;

  return (
    <PaperCard
      title="Where each document stands"
      subtitle={
        (safe ? "Every button you ran, across; " : "Every change you asked for, across; ") +
        "every document, down. ✓ done, ✗ not done, – not run on it. Click a mark for the reason."
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
                    <span className="mt-0.5 flex flex-wrap gap-1">
                      {row.badges.map((badge) => (
                        <Tip key={badge} tip={BADGE_TIP[badge] ?? badge}>
                          <span className={`rounded-full border px-1.5 py-px text-[9.5px] font-semibold tracking-wide uppercase ${BADGE[badge] ?? "border-paper-edge text-paper-dim"}`}>
                            {badge}
                          </span>
                        </Tip>
                      ))}
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

      {pickedRow && pickedCell && (
        <div className="mt-3 rounded-lg border border-paper-edge bg-white/50 px-3 py-2" role="status" aria-label="This mark's reason">
          <p className="text-xs font-semibold break-words text-paper-ink">
            {pickedRow.name} · {operations[pickedIndex].number}. {operations[pickedIndex].name || operations[pickedIndex].request}
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
