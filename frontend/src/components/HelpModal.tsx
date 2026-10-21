// Adapted from Build-a-Spec's frontend/src/components/HelpModal.tsx (copy-based reuse): the dialog, its tab strip and
// the presentational helpers. The words are Spec Surgeon's own (docs/CONVERSATIONAL_DESKTOP.md); Why trust it is
// not here yet. About takes over the old About box (release notes, license) and the old Help menu's Check for
// updates and Open diagnostic logs.
import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { About, Update } from "../types";
import { getAbout } from "../lib/api";
import { useDialogFocus } from "../lib/dialogFocus";
import { HELP_TITLES, HELP_TOPICS, modelLabel, releaseNotesBody, updateStatus, type HelpTopic } from "../lib/help";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";

interface Props {
  topic: HelpTopic | null;
  onClose: () => void;
  onNavigate: (topic: HelpTopic) => void;
  /** The view's version, until /api/about answers. */
  version: string;
  stepsPerPause: number;
  busy: boolean;
  update: Update | null;
  /** About started a check while this window was open; its answer is shown, whenever About is reopened. */
  updateAsked: boolean;
  /** A check started from About has not settled yet. */
  checkingUpdate: boolean;
  onCheckUpdate: () => void;
  onDownloadUpdate: () => void;
  onInstallUpdate: () => void;
  onOpenLogs: () => void;
}

/** A link from the About page opens in the system browser: in the native window a plain anchor would navigate the
 *  app's own page away. */
const EXTERNAL = { target: "_blank", rel: "noreferrer noopener" } as const;
const REMARK_PLUGINS = [remarkGfm];
const MARKDOWN_COMPONENTS = {
  a: ({ node: _node, ...props }: React.ComponentProps<"a"> & { node?: unknown }) => <a {...props} {...EXTERNAL} />,
};

/* --- small presentational helpers, all on the existing palette --- */

function Lead({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-ink-dim">{children}</p>;
}

/** A label the window shows, set apart in the text. */
function Tag({ children }: { children: ReactNode }) {
  return <span className="rounded bg-raised px-1.5 py-0.5 text-[12px] text-ink">{children}</span>;
}

/** Numbered, badge-led steps (getting started). */
function Steps({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ol className="mt-3 space-y-3">
      {items.map((s, i) => (
        <li key={i} className="flex gap-3">
          <span className="mt-0.5 flex h-5 w-5 flex-none items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent tabular-nums">
            {i + 1}
          </span>
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{s.t}</span>
            {s.d ? <> — {s.d}</> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Bulleted point list. */
function Points({ items }: { items: { t: string; d?: ReactNode }[] }) {
  return (
    <ul className="mt-3 space-y-3">
      {items.map((p, i) => (
        <li key={i} className="flex gap-2.5">
          <span className="mt-[7px] h-1.5 w-1.5 flex-none rounded-full bg-accent" />
          <div className="text-sm leading-relaxed text-ink-dim">
            <span className="font-medium text-ink">{p.t}</span>
            {p.d ? <> — {p.d}</> : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** A titled workflow recipe card. */
function Recipe({ title, tagline, steps }: { title: string; tagline: string; steps: ReactNode[] }) {
  return (
    <div className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">{title}</h4>
      <p className="mt-0.5 text-xs text-ink-faint">{tagline}</p>
      <ol className="mt-3 space-y-1.5">
        {steps.map((s, i) => (
          <li key={i} className="flex gap-2.5 text-sm text-ink-dim">
            <span className="text-ink-faint tabular-nums">{i + 1}.</span>
            <span className="leading-relaxed">{s}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A bordered guide box with a title and a line under it. */
function Guide({ title, tagline, children }: { title: string; tagline: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-edge bg-raised/40 p-4">
      <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">{title}</h4>
      <p className="mt-0.5 text-xs text-ink-faint">{tagline}</p>
      {children}
    </section>
  );
}

/** Two-column rows: what you see, and what it means. */
function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="mt-3 overflow-hidden rounded-lg border border-edge text-xs">
      {rows.map(([see, means], i) => (
        <div key={i} className={"grid gap-1 px-3 py-2 sm:grid-cols-2 sm:gap-3" + (i > 0 ? " border-t border-edge" : "")}>
          <dt className="leading-relaxed text-ink">{see}</dt>
          <dd className="leading-relaxed text-ink-dim">{means}</dd>
        </div>
      ))}
    </dl>
  );
}

/* --- per-topic content --- */

function HowToUse({ stepsPerPause }: { stepsPerPause: number }) {
  return (
    <div className="space-y-4">
      <Lead>
        Spec Surgeon is a conversation about your Word specifications. You attach the documents, say what should
        change in plain words, read the plan and the before-and-after pages, and press one button for edited copies.
        A typical first pass:
      </Lead>
      <Steps
        items={[
          {
            t: "Connect your Anthropic API key",
            d: (
              <>
                Click the gear (Settings), or the <Tag>Connect your API key</Tag> pill in the top bar, and paste your{" "}
                <span className="font-mono text-xs">sk-ant-…</span> key. Tick <Tag>Remember securely on this computer</Tag>{" "}
                to keep it in your operating system’s credential store (Windows Credential Manager) so you do not enter
                it again.
              </>
            ),
          },
          {
            t: "Add your Word documents",
            d: (
              <>
                Drop <Tag>.docx</Tag> files anywhere in the window, or use <Tag>Add Word documents</Tag>. They can come
                from different folders, even with the same name. Only the files you attach are edited; nothing is picked
                up from their folders.
              </>
            ),
          },
          {
            t: "Read the note under each file",
            d: "Each file is checked the moment it arrives — in milliseconds, with no AI call. A note describes the file, and the file is still edited like any other; a refused file stays greyed out with the reason and never enters a plan. The guide below says what each one means.",
          },
          {
            t: "Add a template, a picture or a table, if the change needs one",
            d: (
              <>
                <Tag>Add a template (optional)</Tag> attaches one reference document to copy from, such as an
                architect’s headers and footers; it is never edited itself. <Tag>Add a picture or table (optional)</Tag>{" "}
                attaches something for the edit to use: a new logo (<Tag>.png</Tag>, <Tag>.jpg</Tag>, <Tag>.gif</Tag>)
                or a sheet of project names and numbers (<Tag>.xlsx</Tag>, <Tag>.csv</Tag>, <Tag>.json</Tag>).
              </>
            ),
          },
          {
            t: "Describe the result",
            d: "Type what you want and press Enter (Shift+Enter starts a new line). Spec Surgeon reads the documents before asking anything they can answer, goes ahead on sensible defaults and says what it assumed, and asks at most three questions, each with the answer it recommends. Click a choice, type its number, tap a suggested reply, or answer in your own words.",
          },
          {
            t: "Read the plan and look at the pages",
            d: (
              <>
                <Tag>Here’s what will change</Tag> gives one sentence per change, in inches and points with exact text
                quoted, and the assumptions beneath it. <Tag>Every setting</Tag> opens the rest for the reader who wants
                it all. Below are rendered before-and-after pages for sample documents with their page counts — click a
                page to enlarge it — and <Tag>Left untouched</Tag> names any file the plan cannot cover, and why.
              </>
            ),
          },
          {
            t: "Create edited copies",
            d: (
              <>
                One button approves exactly what is on screen. The change is made on copies, every copy is checked
                before it is delivered, and your originals are never written. <Tag>Your edited copies</Tag> lists them;
                click one to open it in Word.
              </>
            ),
          },
          {
            t: "Keep going",
            d: (
              <>
                Ask for the next change and it is made on those copies. <Tag>Start over from the originals</Tag> is one
                tap away, and <Tag>New conversation</Tag> in the top bar archives this one and starts empty. The
                conversation, your decisions and the copies come back when you reopen the app.
              </>
            ),
          },
        ]}
      />
      <Guide
        title="Reading the note under a file"
        tagline="What Spec Surgeon found when the file arrived. A note is information, not a refusal."
      >
        <Rows
          rows={[
            ["Editing is restricted in Word", "The edited copies keep the restriction."],
            ["Saved in an older compatibility mode", "Told so you know; the file is edited like any other."],
            [
              "No paragraph styles in use",
              "Changes pick paragraphs by how they look and what they say, not by style name.",
            ],
            ["Numbers typed by hand", "The numbering is typed text, not Word’s automatic list numbering."],
            [
              "Text split into many small runs, irregular spacing",
              "Common in files edited for years; told so you know.",
            ],
            ["Text boxes in the headers or footers", "Worth a look on the before-and-after pages."],
            ["Tracked changes pending, comments", "Told so you know; the file is still edited like any other."],
            ["Nothing in the body yet", "An empty document is taken: ask to add to it, or to change its headers, footers and page setup."],
          ]}
        />
        <p className="mt-3 text-xs leading-relaxed text-ink-dim">
          Tidying a file — typed numbers into automatic numbering, clearing direct formatting or spacing, settling
          tracked changes — is never automatic. It is only ever offered to you as a first step, and runs only if you
          accept it.
        </p>
        <p className="mt-3 rounded-lg border-l-2 border-warn bg-warn/10 px-3 py-2 text-xs leading-relaxed text-ink-dim">
          <span className="font-medium text-warn">Refused, greyed out:</span> the file does not open as a Word
          package, is a Strict Open XML document, is larger than the app opens, or has no paragraph at all in its body. The reason
          says what to do — usually open it in Word and save it again as <Tag>.docx</Tag>. A <Tag>.doc</Tag>,{" "}
          <Tag>.docm</Tag> or <Tag>~$</Tag> lock file is not attached at all.
        </p>
      </Guide>
      <Guide title="Spending, stopping and pauses" tagline="Every AI step is yours to watch and to stop.">
        <Points
          items={[
            {
              t: "You see it",
              d: "The pill in the top bar shows the estimated spending of this conversation as it accrues — an estimate from a built-in price table, never a bill. While work runs the chat shows the assistant’s words, a summary of its thinking and one plain sentence per thing it is doing.",
            },
            {
              t: "Stop now",
              d: "While work runs, the button cuts off the AI step that is running at once and starts no other. Your progress is saved; Continue picks the conversation up where it stopped.",
            },
            {
              t: `A pause every ${stepsPerPause} AI steps`,
              d: `There is no spending limit. Instead, work pauses after every ${stepsPerPause} AI steps with its progress saved, and the button reads Continue.`,
            },
          ]}
        />
      </Guide>
      <p className="text-xs text-ink-faint">
        Every AI step runs inside work you started: a message, a choice, or the button. Checking a file when it arrives
        makes no AI call. The app’s only request on its own is a once-a-day check for a new version, which carries
        nothing about your documents.
      </p>
    </div>
  );
}

function Workflows({ stepsPerPause }: { stepsPerPause: number }) {
  return (
    <div className="space-y-4">
      <Lead>
        Every request ends the same way — a plan you can read, pages you can look at, one button. Pick the recipe that
        matches the job in front of you.
      </Lead>
      <Recipe
        title="Change the fonts across a set"
        tagline="One property, every section, nothing else touched."
        steps={[
          "Attach the sections: drop the whole set at once; files from several folders are fine.",
          "Say it plainly: “Change every font to Arial 10 pt, keeping bold and italic.”",
          "If it asks whether the headers and footers are included, answer, or let its recommended answer stand.",
          "Read the sentence for the change, look at a sample’s pages, then Create edited copies.",
        ]}
      />
      <Recipe
        title="Match the headers and footers to a template"
        tagline="Your sections, the architect’s look."
        steps={[
          "Attach the sections, then Add a template (optional) with the document whose headers and footers you want.",
          "Ask: “Match the headers, footers and fonts to this template. Keep our technical wording.”",
          "Changes that touch the same parts of a file — the template’s header beside a font change — are put in order by Spec Surgeon itself. The plan says “in 2 ordered steps”; it is still one plan and one button.",
          "If one of your documents is a copy of the template, the window says so: remove one of them.",
        ]}
      />
      <Recipe
        title="Swap a logo"
        tagline="The picture you attach, at the size you approve."
        steps={[
          "Attach the sections and Add a picture or table (optional) with the new logo (.png, .jpg or .gif).",
          "Ask: “Replace the logo in the first-page header with this one, at the same size.”",
          "The plan names the picture and its size in inches. Your approval covers that exact file: change it after the preview and the plan is checked again.",
        ]}
      />
      <Recipe
        title="Fill in project values from a sheet"
        tagline="The values come from your table, never from the AI."
        steps={[
          "Attach the sections and the sheet (.xlsx, .csv or .json) with the project names and numbers.",
          "Say what goes where: “Put the project name and number from this sheet in every footer.”",
          "The plan names the sheet it uses. Check the values on a sample’s pages before you approve.",
        ]}
      />
      <Recipe
        title="Change an issue stamp or a phrase"
        tagline="Exact words, placed exactly as approved."
        steps={[
          "Ask: “Change ‘Issued for Bid’ to ‘Issued for Construction’ everywhere.”",
          "The plan quotes the old and the new text exactly.",
          "Words that go into a document are always ones you typed or picked. A choice that would place words shows them beneath its button, and picking it approves exactly those words; a suggested reply you tap answers a question but never becomes document wording.",
        ]}
      />
      <Recipe
        title="A mixed batch from different projects"
        tagline="Different structures, one approval."
        steps={[
          "Attach them together. Documents are often built differently — styled article titles in one, plain paragraphs with typed numbers in another.",
          "When one plan cannot describe the change for all of them, the window shows Your documents use N layouts: one card per layout, each with its own changes, assumptions and sample pages.",
          "One button approves all of them.",
          "A document built differently from every layout is listed under Left untouched with a sentence: attach it on its own, or ask for a plan that covers it.",
        ]}
      />
      <Recipe
        title="One change after another"
        tagline="Each request builds on the last result."
        steps={[
          "After the copies are created, ask for the next change. It is made on those copies, and the assistant is told what was already done, so it plans only what is new.",
          "Each run’s copies go into their own folder, so earlier copies remain.",
          "Start over from the originals — a reply you can tap, or the link under the copies — goes back to your files.",
          "A document attached after a run is an original beside the copies, and gets only the next change.",
        ]}
      />
      <Recipe
        title="When a file is left untouched"
        tagline="Left out with a reason, never delivered half-right."
        steps={[
          "When it arrives: a refused file is greyed out with the reason and the fix.",
          "At plan time: a file whose result cannot be predicted is listed under Left untouched before anything runs.",
          "At delivery: a copy that fails a check is never delivered. Spec Surgeon looks at it again on its own and comes back with a question or a new plan, never a method for you to choose.",
          "While that question is open, Save N ready copies keeps the copies that already passed.",
        ]}
      />
      <Recipe
        title="Stopped, paused or closed"
        tagline="Your progress is saved every time."
        steps={[
          "Stop now cuts off the running AI step at once; Continue picks up where it stopped.",
          `Work pauses after every ${stepsPerPause} AI steps until you press Continue.`,
          "Close the app mid-work and the conversation reopens paused, with every attachment checked again. Press Continue: the documents are checked against the request before anything new can be approved.",
        ]}
      />
    </div>
  );
}

function HowItWorks() {
  return (
    <div className="space-y-4">
      <Lead>
        A conversation on the left; your documents, the plan, the pages and the copies on the right. Under the hood:
      </Lead>
      <Points
        items={[
          {
            t: "Claude plans, the engine edits",
            d: "Claude turns your request into a plan you read and approve; it never edits a file itself. The edits are made by deterministic code working directly on the document’s XML.",
          },
          {
            t: "The result is predicted first",
            d: "Before anything is changed, the plan works out from the original exactly what will change in every file. After the edit, a separate checker that shares no code with the editing reads the original and the copy and confirms that prediction — that change, and nothing else. A copy that does not match is never delivered.",
          },
          {
            t: "Copies, never originals",
            d: "The window always creates copies, each run in its own timestamped folder under the app’s local data, so earlier copies remain. A file left out is named with the reason.",
          },
          {
            t: "One plan, even when changes overlap",
            d: "Changes that touch the same parts of a file are cut into ordered steps in the order you asked for them, each step planned and checked on the step before it. You see one plan and press one button.",
          },
          {
            t: "A plan per layout for a mixed batch",
            d: "When documents are built differently, each structure gets its own plan, checked on that layout’s documents only. Every delivered copy still follows exactly one plan.",
          },
          {
            t: "Every file is read when it arrives",
            d: "Attaching a file runs a quick check of its styles, numbering, spacing and structure — no AI call. The plan starts from those facts instead of discovering them.",
          },
          {
            t: "Pages rendered once",
            d: "The before-and-after pages come from Microsoft Word when it is installed (a private, invisible copy, never your open Word), otherwise LibreOffice. Pages are kept by the document’s exact content, so creating the copies does not render again what the preview already rendered. Claude also looks at the pages; what it flags is advice for you to check.",
          },
          {
            t: "One model, with room to think",
            d: "Every AI step runs Claude Opus 5.5 with room for its full answer: the conversation at the model’s default effort, the harder recovery work at a higher one. A plan usually takes one or two calls.",
          },
          {
            t: "Saved as you go",
            d: "The conversation, your decisions and approved wording, the attachments, the working copies and the result locations are saved on this computer and come back when you reopen the app. Your API key and any approval to deliver are never saved with them.",
          },
          {
            t: "One window, no modes",
            d: "Everything here happens in the conversation. There is no other mode and nothing to run outside this window.",
          },
        ]}
      />
    </div>
  );
}

function AboutTopic({
  version,
  busy,
  update,
  updateAsked,
  checkingUpdate,
  onCheckUpdate,
  onDownloadUpdate,
  onInstallUpdate,
  onOpenLogs,
}: {
  version: string;
  busy: boolean;
  update: Update | null;
  updateAsked: boolean;
  checkingUpdate: boolean;
  onCheckUpdate: () => void;
  onDownloadUpdate: () => void;
  onInstallUpdate: () => void;
  onOpenLogs: () => void;
}) {
  const [about, setAbout] = useState<About | null>(null);
  const [error, setError] = useState("");
  const [fullLicense, setFullLicense] = useState(false);

  useEffect(() => {
    let alive = true;
    getAbout()
      .then((a) => alive && setAbout(a))
      .catch((failure: unknown) => alive && setError(failure instanceof Error ? failure.message : String(failure)));
    return () => {
      alive = false;
    };
  }, []);

  const shownVersion = about?.version || version;
  const status = updateStatus(update, updateAsked, checkingUpdate);
  const model = modelLabel(about?.model);
  const statusTone = {
    quiet: "text-ink-faint",
    accent: "text-ink",
    ok: "text-ok",
    err: "text-err",
  }[status.tone];

  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-3">
        <h3 className="font-[family-name:var(--font-display)] text-xl font-semibold text-ink">
          {about?.name || "Spec Surgeon"}
        </h3>
        <span className="text-sm text-ink-dim">{shownVersion ? `v${shownVersion}` : "…"}</span>
      </div>
      <Lead>
        {about?.tagline || "Surgical edits to Word specifications with independent verification records."} You attach
        Word specifications and describe the change in plain words; Claude prepares a plan you approve, and the engine
        makes edited copies and checks each one before it is delivered.
      </Lead>
      {error && (
        <p className="text-xs text-err" role="alert">
          {error}
        </p>
      )}
      <dl className="space-y-2 rounded-xl border border-edge bg-raised/40 p-4 text-sm">
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">AI model</dt>
          <dd className="text-ink">{model || "Claude Opus 5.5"}</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">Pages</dt>
          <dd className="text-ink">Microsoft Word, or LibreOffice without it</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">Documents</dt>
          <dd className="text-ink">Word .docx; originals never written</dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-28 flex-none text-ink-faint">License</dt>
          <dd className="text-ink">{about?.license_name || "MIT License"}</dd>
        </div>
      </dl>

      <div className="space-y-3 pt-1">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onCheckUpdate}
            disabled={checkingUpdate}
            className={quietBtn}
          >
            Check for updates
          </button>
          <Tip tip="Opens the folder of the app’s logs, to send to whoever diagnoses a problem. They never contain your documents.">
            <button type="button" onClick={onOpenLogs} className={quietBtn}>
              Open diagnostic logs
            </button>
          </Tip>
          {status.text && !status.action && status.progress === null && (
            <span className={`text-xs ${statusTone}`} role="status">
              {status.text}
            </span>
          )}
        </div>
        {(status.action || status.progress !== null || update?.state === "installing") && (
          <div className="rounded-xl border border-accent/50 bg-accent/10 p-4">
            <p className="text-sm font-medium text-ink" role="status">
              {status.text}
            </p>
            {update?.notes && status.action === "download" && (
              <div className="md mt-1 max-h-40 overflow-y-auto text-xs leading-relaxed text-ink-dim">
                <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS}>
                  {update.notes}
                </ReactMarkdown>
              </div>
            )}
            {status.progress !== null && (
              <div className="update-progress mt-2 max-w-sm">
                <span style={{ width: `${status.progress}%` }} />
              </div>
            )}
            {status.action === "download" && (
              <>
                <Tip tip={busy ? "Finish or stop the current work first — the installer closes the app" : "Download and install the update"} className="mt-3">
                  <button type="button" className={primaryBtn} disabled={busy} onClick={onDownloadUpdate}>
                    Download and install
                  </button>
                </Tip>
                <p className="mt-2 text-xs text-ink-faint">
                  Downloads the installer, checks it against the release’s SHA-256 checksums, and only then runs it.
                  The app closes so the installer can replace it, and reopens when it is done.
                </p>
              </>
            )}
            {status.action === "install" && (
              <Tip
                tip={busy ? "Finish or stop the current work first — the installer closes the app" : "Close Spec Surgeon and install the update"}
                className="mt-3"
              >
                <button type="button" className={primaryBtn} disabled={busy} onClick={onInstallUpdate}>
                  Restart to update
                </button>
              </Tip>
            )}
            {update?.text && <p className="mt-2 text-xs text-ink-faint">{update.text}</p>}
          </div>
        )}
      </div>

      <section className="rounded-xl border border-edge bg-raised/40 p-4">
        <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
          What’s new{shownVersion ? ` in ${shownVersion}` : ""}
        </h4>
        <div className="md mt-2 max-h-72 overflow-y-auto pr-2 text-sm text-ink-dim">
          {about ? (
            releaseNotesBody(about.release_notes) ? (
              <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS}>
                {releaseNotesBody(about.release_notes)}
              </ReactMarkdown>
            ) : (
              <p>No release notes for this version.</p>
            )
          ) : (
            !error && <p className="text-ink-faint">Loading…</p>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-edge bg-raised/40 p-4">
        <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">
          {about?.license_name || "License"}
        </h4>
        {about?.license_summary && about.license_summary.length > 0 && (
          <ul className="mt-2 space-y-1.5">
            {about.license_summary.map((line) => (
              <li key={line} className="flex gap-2.5 text-sm leading-relaxed text-ink-dim">
                <span className="mt-[8px] h-1.5 w-1.5 flex-none rounded-full bg-accent" />
                {line}
              </li>
            ))}
          </ul>
        )}
        <button
          type="button"
          onClick={() => setFullLicense((open) => !open)}
          aria-expanded={fullLicense}
          className="mt-2 text-xs text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent"
        >
          {fullLicense ? "Hide the full license" : "Read the full license"}
        </button>
        {fullLicense && (
          <pre className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-edge bg-surface p-3 font-[family-name:var(--font-body)] text-xs leading-relaxed text-ink-dim">
            {about?.license || (error ? "" : "Loading…")}
          </pre>
        )}
        {about?.components && about.components.length > 0 && (
          <>
            <p className="mt-4 text-[11px] font-medium tracking-wide text-ink-faint uppercase">Open-source components</p>
            <ul className="mt-1 space-y-0.5 text-xs text-ink-dim">
              {about.components.map((c) => (
                <li key={c.name}>
                  {c.url ? (
                    <a href={c.url} {...EXTERNAL} className="text-ink-dim underline decoration-edge underline-offset-2 hover:text-accent">
                      {c.name}
                    </a>
                  ) : (
                    c.name
                  )}
                  <span className="text-ink-faint"> — {c.license}</span>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <div className="space-y-1.5 border-t border-edge pt-4 text-xs text-ink-faint">
        <p>
          {about?.copyright || "© 2026 Abraham Borg"}. {about?.license_name || "MIT License"}.
          {about?.email && (
            <>
              {" "}
              <a href={`mailto:${about.email}`} {...EXTERNAL} className="text-accent underline underline-offset-2 hover:text-accent-hover">
                {about.email}
              </a>
            </>
          )}
        </p>
        <p className="flex flex-wrap gap-x-4 gap-y-1">
          {about?.linkedin && (
            <a href={about.linkedin} {...EXTERNAL} className="text-accent underline underline-offset-2 hover:text-accent-hover">
              LinkedIn
            </a>
          )}
          {about?.github && (
            <a href={about.github} {...EXTERNAL} className="text-accent underline underline-offset-2 hover:text-accent-hover">
              GitHub
            </a>
          )}
        </p>
      </div>
    </div>
  );
}

export default function HelpModal({
  topic,
  onClose,
  onNavigate,
  version,
  stepsPerPause,
  busy,
  update,
  updateAsked,
  checkingUpdate,
  onCheckUpdate,
  onDownloadUpdate,
  onInstallUpdate,
  onOpenLogs,
}: Props) {
  // Escape, Tab containment and focus restoration through the one hook every dialog uses.
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  useDialogFocus(Boolean(topic), panelRef, panelRef, onClose);

  // A new topic starts at its top, not wherever the last one was scrolled to.
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [topic]);

  if (!topic) return null;

  return (
    <div
      className="fixed inset-0 z-[70] flex items-start justify-center bg-black/50 p-6 pt-16"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={HELP_TITLES[topic]}
    >
      <div
        ref={panelRef}
        tabIndex={-1}
        className="flex max-h-[80vh] w-full max-w-2xl flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl outline-none"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header: title + close */}
        <div className="flex items-center justify-between border-b border-edge px-6 py-3">
          <h2 className="font-[family-name:var(--font-display)] text-lg font-semibold">{HELP_TITLES[topic]}</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg px-2 py-1 text-ink-dim transition-colors hover:text-ink"
            title="Close"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Tab strip — jump between topics without reopening */}
        <div className="flex flex-wrap gap-1 border-b border-edge px-4 py-2" role="tablist" aria-label="Help topics">
          {HELP_TOPICS.map((t) => {
            const active = t.id === topic;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onNavigate(t.id)}
                className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                  active ? "bg-accent/15 text-accent" : "text-ink-dim hover:bg-raised hover:text-ink"
                }`}
              >
                {t.label}
              </button>
            );
          })}
        </div>

        {/* Scrollable body */}
        <div ref={bodyRef} role="tabpanel" aria-label={HELP_TITLES[topic]} className="overflow-y-auto px-6 py-5">
          {topic === "how-to-use" && <HowToUse stepsPerPause={stepsPerPause} />}
          {topic === "workflows" && <Workflows stepsPerPause={stepsPerPause} />}
          {topic === "how-it-works" && <HowItWorks />}
          {topic === "about" && (
            <AboutTopic
              version={version}
              busy={busy}
              update={update}
              updateAsked={updateAsked}
              checkingUpdate={checkingUpdate}
              onCheckUpdate={onCheckUpdate}
              onDownloadUpdate={onDownloadUpdate}
              onInstallUpdate={onInstallUpdate}
              onOpenLogs={onOpenLogs}
            />
          )}
        </div>
      </div>
    </div>
  );
}
