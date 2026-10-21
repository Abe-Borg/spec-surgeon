import { useState } from "react";
import type { Attachment } from "../types";
import { kindTag, trayTitle } from "../lib/workbench";
import { quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  attachments: Attachment[];
  busy: boolean;
  /** An upload is in flight (the browser fallback or a drop). */
  uploading: boolean;
  /** A plan or copies exist: the tray folds to one line of chips so the plan
   *  and the pages have the room; the cards come back on request. */
  compact: boolean;
  onAdd: (kind: "documents" | "template" | "references") => void;
  onRemove: (attachment: Attachment) => void;
}

const BUSY_TIP = "Finish or stop the current work first";

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function AttachmentCard({
  attachment,
  busy,
  onRemove,
}: {
  attachment: Attachment;
  busy: boolean;
  onRemove: (attachment: Attachment) => void;
}) {
  const refused = attachment.state === "refused";
  const tag = kindTag(attachment);
  return (
    <li
      className={`card-in flex items-start gap-3 rounded-xl border px-3 py-2.5 ${
        refused ? "border-err/30 bg-err/5" : "border-edge bg-surface"
      }`}
    >
      <span className="mt-0.5 text-ink-faint">
        <FileIcon />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`truncate text-sm ${refused ? "text-ink-faint" : "text-ink"}`} title={attachment.path}>
            {attachment.name}
          </span>
          {tag && (
            <span className="shrink-0 rounded-full border border-edge px-2 py-px text-[10px] font-medium tracking-wide text-ink-dim uppercase">
              {tag}
            </span>
          )}
        </div>
        {refused ? (
          <p className="mt-0.5 text-[12px] leading-snug text-err">{attachment.reason || "This file was left out."}</p>
        ) : (
          attachment.notes?.map((note, index) => (
            <p key={`${index}-${note}`} className="mt-0.5 text-[12px] leading-snug text-ink-faint">
              {note}
            </p>
          ))
        )}
      </div>
      <Tip tip={busy ? BUSY_TIP : `Remove ${attachment.name} from this conversation`}>
        <button
          type="button"
          onClick={() => onRemove(attachment)}
          disabled={busy}
          aria-label={`Remove ${attachment.name}`}
          className="rounded-md px-1.5 py-0.5 text-ink-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40"
        >
          ✕
        </button>
      </Tip>
    </li>
  );
}

/** The folded tray: one chip per attachment, the notes on hover. */
function AttachmentChip({
  attachment,
  busy,
  onRemove,
}: {
  attachment: Attachment;
  busy: boolean;
  onRemove: (attachment: Attachment) => void;
}) {
  const refused = attachment.state === "refused";
  const tag = kindTag(attachment);
  const title = [attachment.name, ...(refused ? [attachment.reason] : attachment.notes ?? [])].filter(Boolean).join("\n");
  return (
    <li
      title={title}
      className={`flex max-w-full items-center gap-1.5 rounded-full border py-0.5 pr-1 pl-2.5 text-xs ${
        refused ? "border-err/30 text-ink-faint" : "border-edge bg-surface text-ink-dim"
      }`}
    >
      <span className="truncate">{attachment.name}</span>
      {tag && <span className="shrink-0 text-[10px] tracking-wide text-ink-faint uppercase">{tag}</span>}
      {refused && <span className="shrink-0 text-[10px] tracking-wide text-err uppercase">left out</span>}
      <Tip tip={busy ? BUSY_TIP : `Remove ${attachment.name} from this conversation`}>
        <button
          type="button"
          onClick={() => onRemove(attachment)}
          disabled={busy}
          aria-label={`Remove ${attachment.name}`}
          className="rounded-full px-1 text-ink-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40"
        >
          ✕
        </button>
      </Tip>
    </li>
  );
}

/**
 * The attachments: each one a card with its name, what triage found, a kind
 * tag and a way to remove it; three ways to add more. Dropping a file
 * anywhere in the window works too (App owns the drop ring).
 */
export default function DocumentsTray({ attachments, busy, uploading, compact, onAdd, onRemove }: Props) {
  const held = busy || uploading;
  const holdTip = uploading ? "Reading the files you added…" : BUSY_TIP;
  const hasTemplate = attachments.some((a) => a.kind === "template");
  const [expanded, setExpanded] = useState(false);
  const folded = compact && !expanded && attachments.length > 0;
  const noted = attachments.filter((a) => a.state !== "ok").length;

  return (
    <section aria-label="Your documents">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-[family-name:var(--font-display)] text-base font-semibold text-ink">
          {trayTitle({ attachments })}
        </h2>
        {compact && attachments.length > 0 && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={!folded}
            className="text-[11px] text-ink-faint transition-colors hover:text-ink-dim"
            title={folded ? "Show each document with what was found in it" : "Fold the documents to one line"}
          >
            {folded ? `Details${noted ? ` · ${noted} with notes` : ""} ▾` : "Fold ▴"}
          </button>
        )}
      </div>
      {attachments.length > 0 &&
        (folded ? (
          <ul className="mt-2 flex flex-wrap items-center gap-1.5">
            {attachments.map((attachment) => (
              <AttachmentChip
                key={`${attachment.kind}:${attachment.path}`}
                attachment={attachment}
                busy={held}
                onRemove={onRemove}
              />
            ))}
          </ul>
        ) : (
          <ul className="mt-3 flex flex-col gap-2">
            {attachments.map((attachment) => (
              <AttachmentCard
                key={`${attachment.kind}:${attachment.path}`}
                attachment={attachment}
                busy={held}
                onRemove={onRemove}
              />
            ))}
          </ul>
        ))}
      <div className={`flex flex-wrap items-center gap-2 ${folded ? "mt-2" : "mt-3"}`}>
        <Tip tip={held ? holdTip : "Attach one or more .docx files to edit"}>
          <button type="button" className={folded ? `${quietBtn} px-2.5 py-1 text-xs` : quietBtn} disabled={held} onClick={() => onAdd("documents")}>
            Add Word documents
          </button>
        </Tip>
        <Tip
          tip={
            held
              ? holdTip
              : hasTemplate
                ? "Replaces the template; it is never part of the batch"
                : "One reference document to match; it is never part of the batch"
          }
        >
          <button type="button" className={folded ? `${quietBtn} px-2.5 py-1 text-xs` : quietBtn} disabled={held} onClick={() => onAdd("template")}>
            {hasTemplate ? "Replace the template" : "Add a template (optional)"}
          </button>
        </Tip>
        <Tip tip={held ? holdTip : "A new logo, or a spreadsheet of values, for the edit to use"}>
          <button type="button" className={folded ? `${quietBtn} px-2.5 py-1 text-xs` : quietBtn} disabled={held} onClick={() => onAdd("references")}>
            Add a picture or table (optional)
          </button>
        </Tip>
      </div>
      {attachments.length === 0 && (
        <p className="mt-3 text-[12px] text-ink-faint">
          Or drop .docx files, pictures or spreadsheets anywhere in this window.
        </p>
      )}
    </section>
  );
}
