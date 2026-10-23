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
import { quietBtn } from "./ModalShell";
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
  showKind,
  onRemove,
}: {
  attachment: Attachment;
  scope: "" | "To do" | "Working on";
  busy: boolean;
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

/**
 * One section of the tray with its own fold: a title line (with, folded, a
 * one-line summary) and, open, a card per attachment and the button that adds
 * more. It folds by itself once a plan or copies exist and opens on a click;
 * once the user has opened or folded it, their choice stands.
 */
function Section({
  id,
  title,
  summary,
  compact,
  children,
}: {
  id: TraySection;
  title: string;
  summary: string;
  compact: boolean;
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
    <section aria-label={title.split(" · ")[0]}>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={!collapsed}
        aria-controls={bodyId}
        title={collapsed ? "Show each file and the way to add more" : "Fold this to one line"}
        className="group flex w-full items-baseline gap-2 rounded-lg text-left transition-colors hover:text-accent"
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
export default function DocumentsTray({ attachments, todo, focus, busy, uploading, compact, onAdd, onRemove }: Props) {
  const held = busy || uploading;
  const holdTip = uploading ? "Reading the files you added…" : BUSY_TIP;
  const hasTemplate = attachments.some((a) => a.kind === "template");
  const view = { attachments, todo, focus };

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
            showKind={section !== "template"}
            onRemove={onRemove}
          />
        ))}
      </ul>
    );
  };

  return (
    <div className="flex flex-col gap-4" aria-label="Your attachments" role="group">
      <Section id="documents" title={trayTitle(view)} summary={traySummary("documents", view)} compact={compact && attachments.length > 0}>
        {cards("documents")}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Tip tip={held ? holdTip : "Attach one or more .docx files to edit"}>
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("documents")}>
              Add Word documents
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
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("template")}>
              {hasTemplate ? "Replace the template" : "Add a template (optional)"}
            </button>
          </Tip>
        </div>
      </Section>
      <Section id="references" title={referencesTitle(view)} summary={traySummary("references", view)} compact={compact}>
        {cards("references")}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Tip tip={held ? holdTip : "A new logo, or a spreadsheet of values, for the edit to use"}>
            <button type="button" className={quietBtn} disabled={held} onClick={() => onAdd("references")}>
              Add a picture or table (optional)
            </button>
          </Tip>
        </div>
      </Section>
    </div>
  );
}
