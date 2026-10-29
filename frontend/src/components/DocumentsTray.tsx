import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Attachment, TodoItem } from "../types";
import {
  kindTag,
  referencesTitle,
  scopeTag,
  sectionAttachments,
  traySummary,
  trayTitle,
  type TraySection,
} from "../lib/workbench";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  attachments: Attachment[];
  /** Documents waiting on the to-do list, and the documents of the pass over it (attached originals). */
  todo: TodoItem[];
  focus: string[];
  busy: boolean;
  /** An upload is in flight (the browser fallback or a drop). */
  uploading: boolean;
  /** A plan or copies exist: the tray folds to its title line once, so the plan
   *  and the pages have the room; a click opens it again. */
  compact: boolean;
  onAdd: (kind: "documents" | "template" | "references") => void;
  /** Add a folder of documents: the native folder dialog, then the folder's own .docx files. */
  onAddFolder: () => void;
  onRemove: (attachment: Attachment) => void;
  /** Remove all documents: every Word document out of this conversation; the template and pictures stay. */
  onRemoveAll: () => void;
  /** Edited copies exist: the confirmation says they leave the list with their documents. */
  hasCopies: boolean;
  /** Why the attachments cannot change now ("" when they can): a run's saved copies wait on its recovery (the
   *  view's `recovery_blocked`); attaching or removing would end that check first. */
  blocked: string;
}

const BUSY_TIP = "Finish or stop the current work first";

/** A button on a section's title line: smaller than the ones under it, so the line still reads as a title. */
const titleBtn =
  "shrink-0 rounded-lg border border-edge bg-raised px-2.5 py-1 text-xs text-ink transition-colors " +
  "hover:border-accent hover:text-accent disabled:pointer-events-none disabled:opacity-40";

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5" />
    </svg>
  );
}

function Tag({ text, tone }: { text: string; tone?: "warn" | "accent" }) {
  const color =
    tone === "warn" ? "border-warn/60 text-warn" : tone === "accent" ? "border-accent/60 text-accent" : "border-edge text-ink-dim";
  return (
    <span className={`shrink-0 rounded-full border px-2 py-px text-[10px] font-medium tracking-wide uppercase ${color}`}>
      {text}
    </span>
  );
}

function AttachmentCard({
  attachment,
  scope,
  busy,
  busyTip,
  showKind,
  onRemove,
}: {
  attachment: Attachment;
  scope: "" | "To do" | "Working on";
  busy: boolean;
  /** Why the ✕ waits while `busy`. */
  busyTip: string;
  /** The kind tag (Picture, Table); the template's own section needs none. */
  showKind: boolean;
  onRemove: (attachment: Attachment) => void;
}) {
  const refused = attachment.state === "refused";
  const tag = showKind ? kindTag(attachment) : "";
  return (
    <li
      className={`card-in flex gap-3 rounded-xl border px-3 py-2.5 ${
        refused ? "items-start border-err/30 bg-err/5" : "items-center border-edge bg-surface"
      }`}
    >
      <span className={`text-ink-faint ${refused ? "mt-0.5" : ""}`}>
        <FileIcon />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={`truncate text-sm ${refused ? "text-ink-faint" : "text-ink"}`} title={attachment.path}>
            {attachment.name}
          </span>
          {tag && <Tag text={tag} />}
          {scope === "To do" && <Tag text="To do" tone="warn" />}
          {scope === "Working on" && <Tag text="Working on" tone="accent" />}
        </div>
        {refused && (
          <p className="mt-0.5 text-[12px] leading-snug text-err">{attachment.reason || "This file was left out."}</p>
        )}
      </div>
      <Tip tip={busy ? busyTip : `Remove ${attachment.name} from this conversation`}>
        <button
          type="button"
          onClick={() => onRemove(attachment)}
          disabled={busy}
          aria-label={`Remove ${attachment.name}`}
          data-capability="documents.remove"
          className="rounded-md px-1.5 py-0.5 text-ink-faint transition-colors hover:bg-raised hover:text-ink disabled:pointer-events-none disabled:opacity-40"
        >
          ✕
        </button>
      </Tip>
    </li>
  );
}

/**
 * One section of the tray with its own fold: a title line (with, folded, a
 * one-line summary) and, open, a card per attachment and the button that adds
 * more. It folds by itself once a plan or copies exist and opens on a click;
 * once the user has opened or folded it, their choice stands. `action` sits on
 * the title line and `notice` under it, both shown folded or open: what must
 * stay one click away when the list has folded (Remove all documents).
 */
function Section({
  id,
  title,
  summary,
  compact,
  action,
  notice,
  children,
}: {
  id: TraySection;
  title: string;
  summary: string;
  compact: boolean;
  action?: ReactNode;
  notice?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current) setCollapsed(compact);
  }, [compact]);
  const toggle = () => {
    touched.current = true;
    setCollapsed((c) => !c);
  };
  const bodyId = `tray-${id}-body`;
  return (
    <section aria-label={title.split(" · ")[0]} data-tour={`tray-${id}`}>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          aria-controls={bodyId}
          title={collapsed ? "Show each file and the way to add more" : "Fold this to one line"}
          className="group flex min-w-0 flex-1 items-baseline gap-2 rounded-lg text-left transition-colors hover:text-accent"
        >
          <span
            aria-hidden
            className={`inline-block text-[10px] text-ink-faint transition-transform group-hover:text-accent ${collapsed ? "" : "rotate-90"}`}
          >
            ▸
          </span>
          <h2 className="font-[family-name:var(--font-display)] text-base font-semibold text-ink group-hover:text-accent">
            {title}
          </h2>
          {collapsed && summary && <span className="min-w-0 truncate text-[11px] text-ink-faint">{summary}</span>}
        </button>
        {action}
      </div>
      {notice}
      {!collapsed && <div id={bodyId}>{children}</div>}
    </section>
  );
}

/**
 * The attachments, in three sections that fold on their own: the Word
 * documents, the template, and the pictures and tables. Each lists its files
 * as cards with a name, a kind tag, a to-do or working-on tag and a way to
 * remove it (a refused one says why; triage's notes are not shown), and has
 * its own button to add more. Each folds by itself once a plan or copies
 * exist and opens on a click. Dropping a file anywhere in the window works
 * too (App owns the drop ring).
 */
export default function DocumentsTray({
  attachments,
  todo,
  focus,
  busy,
  uploading,
  compact,
  onAdd,
  onAddFolder,
  onRemove,
  onRemoveAll,
  hasCopies,
  blocked,
}: Props) {
  const held = busy || uploading || !!blocked;
  const holdTip = uploading ? "Reading the files you added…" : busy ? BUSY_TIP : blocked;
  const hasTemplate = attachments.some((a) => a.kind === "template");
  const view = { attachments, todo, focus };
  // Remove all documents asks once before it acts: a list of a dozen documents is one click from empty
  const documentCount = sectionAttachments("documents", view).length;
  const [confirmingRemoveAll, setConfirmingRemoveAll] = useState(false);
  useEffect(() => {
    if (held || documentCount < 2) setConfirmingRemoveAll(false);
  }, [held, documentCount]);

  const cards = (section: TraySection) => {
    const listed = sectionAttachments(section, view);
    if (listed.length === 0) return null;
    return (
      <ul className="mt-3 flex flex-col gap-2">
        {listed.map((attachment) => (
          <AttachmentCard
            key={`${attachment.kind}:${attachment.path}`}
            attachment={attachment}
            scope={scopeTag(attachment, view)}
            busy={held}
            busyTip={holdTip}
            showKind={section !== "template"}
            onRemove={onRemove}
          />
        ))}
      </ul>
    );
  };

  return (
    <div className="flex flex-col gap-4" aria-label="Your attachments" role="group">
      <Section
        id="documents"
        title={trayTitle(view)}
        summary={traySummary("documents", view)}
        compact={compact && attachments.length > 0}
        action={
          // on the title line, so the list folded once a plan exists still has it (the owner's run of 2026-10-29:
          // the button sat in the folded body and every document was taken out by its ✕)
          documentCount >= 2 && (
            <Tip tip={held ? holdTip : "Take every Word document out of this conversation; the template, pictures and tables stay"}>
              <button
                type="button"
                className={titleBtn}
                disabled={held}
                onClick={() => setConfirmingRemoveAll((c) => !c)}
                aria-expanded={confirmingRemoveAll}
                data-capability="documents.remove-all"
              >
                Remove all documents
              </button>
            </Tip>
          )
        }
        notice={
          confirmingRemoveAll && (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-xl border border-edge bg-surface px-3 py-2.5" role="group" aria-label="Confirm removing all documents">
              <span className="w-full text-[12px] leading-snug text-ink-dim">
                Remove all {documentCount} documents from this conversation? The files themselves are not touched.
                {hasCopies && " Their edited copies leave the list with them; export any you want to keep first."}
              </span>
              <button type="button" className={quietBtn} onClick={() => setConfirmingRemoveAll(false)}>
                Keep them
              </button>
              <button
                type="button"
                className={primaryBtn}
                disabled={held}
                onClick={() => {
                  setConfirmingRemoveAll(false);
                  onRemoveAll();
                }}
              >
                Remove all {documentCount}
              </button>
            </div>
          )
        }
      >
        {cards("documents")}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Tip tip={held ? holdTip : "Attach one or more .docx files to edit"}>
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("documents")} data-capability="documents.add">
              Add Word documents
            </button>
          </Tip>
          <Tip tip={held ? holdTip : "Attach every .docx file in a folder (not its subfolders)"}>
            <button type="button" className={quietBtn} disabled={held} onClick={onAddFolder} data-capability="documents.folder">
              Add a folder of documents
            </button>
          </Tip>
        </div>
        {attachments.length === 0 && (
          <p className="mt-3 text-[12px] text-ink-faint">
            Or drop .docx files, pictures or spreadsheets anywhere in this window.
          </p>
        )}
      </Section>
      <Section id="template" title="Template" summary={traySummary("template", view)} compact={compact}>
        {cards("template")}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Tip
            tip={
              held
                ? holdTip
                : hasTemplate
                  ? "Replaces the template; it is never part of the batch"
                  : "One reference document to match; it is never part of the batch"
            }
          >
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("template")} data-capability="documents.template">
              {hasTemplate ? "Replace the template" : "Add a template (optional)"}
            </button>
          </Tip>
        </div>
      </Section>
      <Section id="references" title={referencesTitle(view)} summary={traySummary("references", view)} compact={compact}>
        {cards("references")}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Tip tip={held ? holdTip : "A new logo, or a spreadsheet of values, for the edit to use"}>
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("references")} data-capability="documents.references">
              Add a picture or table (optional)
            </button>
          </Tip>
        </div>
      </Section>
    </div>
  );
}
