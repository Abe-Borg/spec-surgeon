import { memo, useEffect, useRef } from "react";
import type { Attachment, View } from "../types";
import type { Mark, Reviews } from "../lib/review";
import { planHasContent, runFolder, undoLink } from "../lib/workbench";
import { unverifiedLine } from "../lib/modes";
import DocumentsTray from "./DocumentsTray";
import Markdown from "./Markdown";
import PaperCard from "./PaperCard";
import PreviewCard from "./PreviewCard";
import Tip from "./Tip";
import { paperBtn } from "./ModalShell";

interface Props {
  view: View;
  busy: boolean;
  uploading: boolean;
  /** The user's marks on the previews, by sample source. */
  reviews: Reviews;
  /** The marks can be given right now (the plan awaits approval, nothing runs). */
  canReview: boolean;
  onMark: (source: string, mark: Mark) => void;
  onAdd: (kind: "documents" | "template" | "references") => void;
  onRemove: (attachment: Attachment) => void;
  onOpen: (path: string) => void;
  onStartOver: () => void;
  /** Safe mode's Undo the last button: the working set back to the copies from before it. */
  onUndo: () => void;
  /** Takes a document off the to-do list, back among the documents the next request works on. */
  onPutBack: (path: string) => void;
  onZoom: (src: string, title: string) => void;
  /** Safe mode's "Try these in Default mode" for what the last button left untouched. */
  onHandOff: (paths: string[]) => void;
}

/** Faux grey bars where the previews will appear; pulsing while a plan is being worked out. */
function PreviewPlaceholder({
  working,
  started,
  hasDocuments,
  safe,
}: {
  working: boolean;
  started: boolean;
  hasDocuments: boolean;
  /** Safe mode: a button, not a request, brings the previews. */
  safe: boolean;
}) {
  const bar = (w: string, i: number) => (
    <div
      key={i}
      className={`h-2.5 rounded-full bg-paper-edge ${working ? "skeleton-line" : ""}`}
      style={{ width: w, animationDelay: working ? `${i * 0.15}s` : undefined }}
    />
  );
  return (
    <section className="paper px-5 py-4" aria-label={working ? "Working out the plan" : "The previews will appear here"}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="font-[family-name:var(--font-display)] text-[1.05rem] font-semibold text-paper-faint">
            {working ? "Working out the plan…" : "Previews"}
          </h2>
          <p className="mt-0.5 text-xs text-paper-faint">
            {working
              ? "The pages before and after appear here as they render."
              : safe
                ? hasDocuments
                  ? "Pick a button; the previews appear here."
                  : "Add your documents and pick a button; the previews appear here."
                : started
                  ? "The previews appear here once your request is clear."
                  : hasDocuments
                    ? "Say what you want to change; the previews appear here."
                    : "Add your documents and say what you want; the previews appear here."}
          </p>
        </div>
      </div>
      <div className="mt-4 flex flex-col gap-2.5">
        {["72%", "88%", "54%", "80%"].map(bar)}
      </div>
    </section>
  );
}

function Workbench({
  view,
  busy,
  uploading,
  reviews,
  canReview,
  onMark,
  onAdd,
  onRemove,
  onOpen,
  onStartOver,
  onUndo,
  onPutBack,
  onZoom,
  onHandOff,
}: Props) {
  const safe = view.mode === "safe";
  const undo = undoLink(view, busy);
  const untouched = view.left_untouched ?? [];
  const hasPlan = planHasContent(view.plan);
  const folder = runFolder(view);
  const setAside = view.plan?.set_aside ?? [];
  const todo = view.todo ?? [];
  const scrollRef = useRef<HTMLElement>(null);
  const previewsRef = useRef<HTMLDivElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);

  // The newest thing scrolls into view as it lands: the previews when they
  // render (keyed by their pages, so a refetch of the same previews moves
  // nothing), the copies when they are written.
  const previewsKey = view.samples.map((s) => `${s.source}\t${s.images.join("\t")}`).join("\n");
  useEffect(() => {
    if (previewsKey) previewsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [previewsKey]);
  const resultsKey = view.results.map((r) => r.output).join("\n");
  useEffect(() => {
    if (resultsKey) resultsRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [resultsKey]);

  return (
    <section
      ref={scrollRef}
      className="flex min-w-[480px] flex-1 basis-[54%] flex-col overflow-y-auto bg-bg"
      aria-label="Your documents and the pages"
    >
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-5 px-6 py-6">
        <DocumentsTray
          attachments={view.attachments}
          todo={todo}
          focus={view.focus ?? []}
          busy={busy}
          uploading={uploading}
          compact={hasPlan || view.results.length > 0}
          onAdd={onAdd}
          onRemove={onRemove}
        />

        {view.samples.length === 0 && view.results.length === 0 && (busy || !hasPlan) && (
          <PreviewPlaceholder
            working={busy}
            started={view.messages.length > 0}
            hasDocuments={view.attachments.length > 0}
            safe={safe}
          />
        )}

        {view.samples.length > 0 && (
          <div ref={previewsRef} className="flex scroll-mt-6 flex-col gap-5">
            {canReview && (
              <p className="-mb-2 px-1 text-[12px] text-ink-faint">
                Look at each preview. The green checkmark says it looks right; the red X sets that document aside for
                more work after the others are created.
              </p>
            )}
            {view.samples.map((sample) => (
              <PreviewCard
                key={sample.source || sample.name}
                sample={sample}
                mark={reviews[sample.source]}
                canReview={canReview}
                onMark={onMark}
                onZoom={onZoom}
              />
            ))}
          </div>
        )}

        {view.warnings.length > 0 && (
          <PaperCard
            title={view.blocking ? "Please check" : "About these checks"}
            subtitle={
              view.blocking
                ? "A visual finding is marked as blocking. Look at the flagged pages before creating copies."
                : "Findings from the automated checks and the visual check of the rendered pages; advisory."
            }
            tone={view.blocking ? "error" : "muted"}
          >
            <ul className="space-y-1.5">
              {view.warnings.map((warning, index) => (
                <li key={`${index}-${warning}`} className="flex gap-2 text-[0.9rem] leading-relaxed text-paper-ink">
                  <span aria-hidden className={view.blocking ? "text-err" : "text-paper-dim"}>
                    •
                  </span>
                  <Markdown text={warning} paper inline />
                </li>
              ))}
            </ul>
          </PaperCard>
        )}

        {setAside.length > 0 && (
          <PaperCard
            title="Left untouched"
            subtitle={
              safe
                ? "These documents are not part of the change; each line says why. Once the copies are created, you can hand them to Default mode."
                : "These documents are not part of the plan; each line says why."
            }
          >
            <ul className="divide-y divide-paper-edge">
              {setAside.map((item, index) => (
                <li key={`${index}-${item.name}`} className="py-2 first:pt-0 last:pb-0">
                  <p className="text-sm font-medium text-paper-ink">{item.name}</p>
                  <p className="text-[12px] leading-relaxed text-paper-dim">
                    <Markdown text={item.reason} paper inline />
                  </p>
                </li>
              ))}
            </ul>
          </PaperCard>
        )}

        {untouched.length > 0 && (
          <PaperCard
            title="Left untouched by the last button"
            subtitle="These documents got no copy from it; each line says why. Default mode can plan them with an AI model."
            aside={
              <Tip
                tip={
                  busy
                    ? "Finish the current work first"
                    : "Puts these documents on your to-do list and switches to Default mode, where you describe what should be different"
                }
              >
                <button type="button" className={paperBtn} disabled={busy} onClick={() => onHandOff(untouched.map((u) => u.path))}>
                  Try these in Default mode
                </button>
              </Tip>
            }
          >
            <ul className="divide-y divide-paper-edge">
              {untouched.map((item) => (
                <li key={item.path} className="py-2 first:pt-0 last:pb-0" title={item.path}>
                  <p className="text-sm font-medium text-paper-ink">{item.name}</p>
                  <p className="text-[12px] leading-relaxed text-paper-dim">
                    <Markdown text={item.reason} paper inline />
                  </p>
                </li>
              ))}
            </ul>
          </PaperCard>
        )}

        {view.results.length > 0 && (
          <div ref={resultsRef} className="scroll-mt-6">
          <PaperCard
            title="Your edited copies"
            subtitle={
              view.results.some((r) => (r.unverified ?? []).length > 0)
                ? "Each copy was checked against the plan. A copy marked UNVERIFIED failed a check and was delivered anyway (Obliterate mode): check it in Word before you use it. Your originals stay intact."
                : "Each copy was checked against the plan, word for word. Click one to open it in Word; your originals stay intact."
            }
            aside={
              folder ? (
                <Tip tip={`Open the folder with these copies: ${folder}`}>
                  <button
                    type="button"
                    onClick={() => onOpen(folder)}
                    aria-label="Open the folder with these copies"
                    className="flex h-8 w-8 items-center justify-center rounded-lg border border-paper-edge bg-white/60 text-paper-dim transition-colors hover:border-accent hover:text-accent"
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
                    </svg>
                  </button>
                </Tip>
              ) : undefined
            }
          >
            <ul className="divide-y divide-paper-edge">
              {view.results.map((result) => (
                <li key={result.output}>
                  <button
                    type="button"
                    onClick={() => onOpen(result.output)}
                    title={result.output}
                    className="flex w-full items-center gap-2 py-2 text-left text-sm text-paper-ink transition-colors hover:text-accent"
                  >
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden className="shrink-0 text-paper-dim">
                      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
                      <path d="M14 3v5h5" />
                    </svg>
                    <span className="truncate">{result.name || result.output.split(/[\\/]/).pop()}</span>
                    {(result.unverified ?? []).length > 0 && (
                      <span
                        className="ml-auto flex-none rounded-full border border-err/50 bg-err/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-err"
                        title={unverifiedLine(result.unverified)}
                      >
                        UNVERIFIED
                      </span>
                    )}
                  </button>
                  {(result.unverified ?? []).length > 0 && (
                    <p className="pb-2 pl-6 text-[11px] leading-snug text-err">{unverifiedLine(result.unverified)}</p>
                  )}
                </li>
              ))}
            </ul>
          </PaperCard>
          </div>
        )}

        {todo.length > 0 && (
          <PaperCard
            title="Set aside for more work"
            subtitle={
              (view.focus ?? []).length > 0
                ? "These wait for a pass of their own, out of the plan you are working on now. Put one back to have it join the other documents when this pass ends."
                : "These documents wait here, out of every plan, until you work on them (the conversation offers it) or put one back. Nothing in them is changed."
            }
            tone="muted"
          >
            <ul className="divide-y divide-paper-edge">
              {todo.map((item) => (
                <li key={item.path} className="flex items-start gap-3 py-2 first:pt-0 last:pb-0">
                  <div className="min-w-0 flex-1" title={item.path}>
                    <p className="truncate text-sm text-paper-ink">{item.name}</p>
                    {item.reason && <p className="mt-0.5 text-xs leading-snug text-paper-dim">{item.reason}</p>}
                  </div>
                  <Tip tip={busy ? "Finish or stop the current work first" : "Back among the documents your next request works on, as it is"}>
                    <button
                      type="button"
                      onClick={() => onPutBack(item.path)}
                      disabled={busy}
                      aria-label={`Put back ${item.name}`}
                      className="shrink-0 text-xs text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
                    >
                      Put back
                    </button>
                  </Tip>
                </li>
              ))}
            </ul>
          </PaperCard>
        )}

        {view.working?.generation > 0 && (
          <p className="text-[12px] text-ink-faint">
            {safe ? "The next button builds on these copies." : "Your next request builds on these copies."}
            {view.working.can_start_over && (
              <>
                {" "}
                <Tip tip={busy ? "Finish or stop the current work first" : "The working set returns to the originals; the plan is void."}>
                  <button
                    type="button"
                    onClick={onStartOver}
                    disabled={busy}
                    className="text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
                  >
                    Start over from the originals
                  </button>
                </Tip>
              </>
            )}
            {undo && (
              <>
                {" · "}
                <Tip tip={undo.tip}>
                  <button
                    type="button"
                    onClick={onUndo}
                    disabled={undo.disabled}
                    className="text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent disabled:pointer-events-none disabled:opacity-50"
                  >
                    Undo the last button
                  </button>
                </Tip>
              </>
            )}
          </p>
        )}
      </div>
    </section>
  );
}

// Memoized: a streamed delta changes only the live bubble, so the workbench keeps its last render.
export default memo(Workbench);
