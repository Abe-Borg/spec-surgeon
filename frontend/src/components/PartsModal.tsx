import { useCallback, useEffect, useRef, useState } from "react";
import type { Attachment, PartInfo, PartPage, PartSearch, PartsListing } from "../types";
import * as api from "../lib/api";
import {
  PARTS_READ_ONLY,
  REPLACE_ORIGINAL_TIP,
  REPLACE_PART,
  REPLACE_PART_TIP,
  SAVE_PART,
  SAVE_PART_TIP,
  WHICH_LABELS,
  partLeaf,
  partSize,
  partsByFolder,
  searchSummary,
} from "../lib/handsOn";
import { ModalShell, primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  document: Attachment;
  /** Work runs, or the tour does: the two buttons that act are held; reading is not. */
  busy: boolean;
  onClose: () => void;
  /** Save this part: the native folder dialog, then the part's own bytes written there. */
  onSave: (part: string, which: api.PartsFile) => void;
  /** Replace this part: the chosen file takes the part's place in a copy, after a preview. */
  onReplace: (part: string, file: File) => void;
}

const failureText = (failure: unknown): string => (failure instanceof Error ? failure.message : String(failure));

/**
 * Obliterate's hands-on part browser (docs/BACKLOG.md O4; lib/handsOn.ts): the parts one document's file is made
 * of, one of them as text a page at a time, and a search across them. It is read-only — nothing here edits XML.
 * Two buttons act on the part shown: **Save this part…** writes its own bytes to a folder the user chooses, and
 * **Replace this part…** puts a file of the user's in its place, byte for byte, after a preview. No AI is used.
 */
export default function PartsModal({ document, busy, onClose, onSave, onReplace }: Props) {
  const [which, setWhich] = useState<api.PartsFile>("current");
  const [listing, setListing] = useState<PartsListing | null>(null);
  const [selected, setSelected] = useState("");
  const [page, setPage] = useState<PartPage | null>(null);
  const [problem, setProblem] = useState("");
  const [query, setQuery] = useState("");
  const [found, setFound] = useState<PartSearch | null>(null);
  const [mark, setMark] = useState(0);
  const fileRef = useRef<HTMLInputElement>(null);
  const linesRef = useRef<HTMLOListElement>(null);

  const show = useCallback(
    (part: string, number = 1, line = 0) => {
      setSelected(part);
      setMark(line);
      api
        .getPart(document.path, part, number, which)
        .then((next) => {
          setPage(next);
          setProblem("");
        })
        .catch((failure: unknown) => setProblem(failureText(failure)));
    },
    [document.path, which],
  );

  // The list of the file being read; the main part opens first.
  useEffect(() => {
    let live = true;
    setListing(null);
    setPage(null);
    setFound(null);
    api
      .getParts(document.path, which)
      .then((next) => {
        if (!live) return;
        setListing(next);
        setProblem("");
        const first = next.parts.find((p) => p.name === "word/document.xml") ?? next.parts.find((p) => p.xml) ?? next.parts[0];
        if (first) show(first.name);
      })
      .catch((failure: unknown) => live && setProblem(failureText(failure)));
    return () => {
      live = false;
    };
  }, [document.path, which, show]);

  // A search result's line scrolls into view once its page is shown.
  useEffect(() => {
    if (!mark || !page) return;
    linesRef.current?.querySelector(`[data-line="${mark}"]`)?.scrollIntoView({ block: "center" });
  }, [mark, page]);

  const search = () => {
    const words = query.trim();
    if (!words) {
      setFound(null);
      return;
    }
    api
      .searchParts(document.path, words, which)
      .then((next) => {
        setFound(next);
        setProblem("");
      })
      .catch((failure: unknown) => setProblem(failureText(failure)));
  };

  const info: PartInfo | undefined = listing?.parts.find((p) => p.name === selected);
  const original = which === "original" && !!listing?.has_copy;

  return (
    <ModalShell title={`Parts of ${document.name}`} onClose={onClose} xwide>
      <div className="flex flex-col gap-3 text-sm" data-hands-on="parts">
        <p className="text-xs leading-relaxed text-ink-dim">{PARTS_READ_ONLY}</p>
        <div className="flex flex-wrap items-center gap-2">
          {listing?.has_copy && (
            <div className="flex overflow-hidden rounded-lg border border-edge text-xs" role="group" aria-label="Which file">
              {(["current", "original"] as const).map((file) => (
                <button
                  key={file}
                  type="button"
                  aria-pressed={which === file}
                  onClick={() => setWhich(file)}
                  className={`px-2.5 py-1 transition-colors ${which === file ? "bg-raised font-semibold text-ink" : "text-ink-dim hover:bg-raised"}`}
                >
                  {WHICH_LABELS[file]}
                </button>
              ))}
            </div>
          )}
          <form
            className="flex min-w-0 flex-1 items-center gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              search();
            }}
          >
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search every part"
              aria-label="Search every part"
              className="min-w-0 flex-1 rounded-lg border border-edge bg-bg px-2.5 py-1 text-xs text-ink outline-none focus:border-accent"
            />
            <button type="submit" className={quietBtn}>
              Search
            </button>
          </form>
        </div>
        {problem && (
          <p className="text-xs text-err" role="alert">
            {problem}
          </p>
        )}
        <div className="flex min-h-0 gap-3" style={{ height: "56vh" }}>
          <nav className="w-64 flex-none overflow-y-auto rounded-lg border border-edge bg-bg p-2 text-xs" aria-label="Parts">
            {found && (
              <div className="mb-2 border-b border-edge pb-2">
                <p className="mb-1 text-ink-dim">{searchSummary(found.query, found.matches.length, found.more)}</p>
                <ul className="space-y-1">
                  {found.matches.map((m, index) => (
                    <li key={`${index}-${m.part}-${m.line}`}>
                      <button
                        type="button"
                        onClick={() => show(m.part, m.page, m.line)}
                        className="block w-full rounded px-1 py-0.5 text-left hover:bg-raised"
                        title={`${m.part}, line ${m.line}`}
                      >
                        <span className="text-ink-faint">
                          {partLeaf(m.part)}:{m.line}
                        </span>{" "}
                        <span className="font-mono text-[11px] text-ink [overflow-wrap:anywhere]">{m.text}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!listing && !problem && <p className="text-ink-faint">Reading the file…</p>}
            {listing &&
              partsByFolder(listing.parts).map(([folder, parts]) => (
                <div key={folder || "/"} className="mb-1.5">
                  <p className="px-1 text-[11px] uppercase tracking-wide text-ink-faint">{folder || "(package root)"}</p>
                  <ul>
                    {parts.map((part) => (
                      <li key={part.name}>
                        <button
                          type="button"
                          onClick={() => show(part.name)}
                          aria-current={selected === part.name}
                          title={`${part.name} — ${part.type || "no declared type"}`}
                          className={`flex w-full items-baseline justify-between gap-2 rounded px-1.5 py-0.5 text-left transition-colors ${
                            selected === part.name ? "bg-raised font-semibold text-ink" : "text-ink-dim hover:bg-raised"
                          }`}
                        >
                          <span className="truncate">{partLeaf(part.name)}</span>
                          <span className="flex-none text-[11px] text-ink-faint">{partSize(part.size)}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
          </nav>
          <div className="flex min-w-0 flex-1 flex-col rounded-lg border border-edge bg-bg">
            <div className="flex flex-wrap items-center gap-2 border-b border-edge px-3 py-1.5 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono text-ink" title={selected}>
                {selected || "Choose a part"}
              </span>
              {info && <span className="text-ink-faint">{partSize(info.size)}</span>}
              {page && page.pages > 1 && (
                <span className="flex items-center gap-1 text-ink-dim">
                  <button type="button" className={quietBtn} disabled={page.page <= 1} onClick={() => show(selected, page.page - 1)}>
                    ‹
                  </button>
                  Page {page.page} of {page.pages}
                  <button type="button" className={quietBtn} disabled={page.page >= page.pages} onClick={() => show(selected, page.page + 1)}>
                    ›
                  </button>
                </span>
              )}
            </div>
            {page?.note && <p className="border-b border-edge px-3 py-1 text-[11px] text-ink-faint">{page.note}</p>}
            <ol ref={linesRef} className="min-h-0 flex-1 overflow-auto px-1 py-1 font-mono text-[11px] leading-relaxed text-ink" aria-label="The part as text">
              {(page?.lines ?? []).map((line, index) => {
                const number = (page?.first ?? 1) + index;
                return (
                  <li key={number} data-line={number} className={`flex gap-2 whitespace-pre ${number === mark ? "bg-warn/20" : ""}`}>
                    <span className="w-12 flex-none select-none text-right text-ink-faint">{number}</span>
                    <span className="min-w-0">{line}</span>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            aria-hidden
            tabIndex={-1}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file && selected) onReplace(selected, file);
            }}
          />
          <Tip tip={busy ? "Finish or stop the current work first" : SAVE_PART_TIP}>
            <button
              type="button"
              className={quietBtn}
              disabled={busy || !selected}
              onClick={() => onSave(selected, which)}
              data-capability="handson.save-part"
            >
              {SAVE_PART}
            </button>
          </Tip>
          <Tip tip={busy ? "Finish or stop the current work first" : original ? REPLACE_ORIGINAL_TIP : REPLACE_PART_TIP}>
            <button
              type="button"
              className={primaryBtn}
              disabled={busy || !selected || original}
              onClick={() => fileRef.current?.click()}
              data-capability="handson.replace-part"
            >
              {REPLACE_PART}
            </button>
          </Tip>
        </div>
      </div>
    </ModalShell>
  );
}
