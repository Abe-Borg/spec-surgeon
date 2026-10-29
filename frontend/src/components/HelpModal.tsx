// Adapted from Build-a-Spec's frontend/src/components/HelpModal.tsx (copy-based reuse): the dialog, its tab strip and
// the presentational helpers (now in helpParts.tsx). The words are Spec Surgeon's own (docs/CONVERSATIONAL_DESKTOP.md).
// Why trust it (WhyTrustIt.tsx) opens the "I'm not convinced" dossier (TrustDossier.tsx), which stacks on this dialog;
// both are pinned to the code by docs/TRUST_CLAIMS.md and its tests. About takes over the old About box (release
// notes, license) and the old Help menu's Check for updates and Open diagnostic logs.
import { useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { About, Update } from "../types";
import { getAbout } from "../lib/api";
import { useDialogFocus } from "../lib/dialogFocus";
import { HELP_TITLES, HELP_TOPICS, modelLabel, releaseNotesBody, updateStatus, type HelpTopic } from "../lib/help";
import { OBLITERATE_COSTS, OBLITERATE_GRANTS, OBLITERATE_KEPT } from "../lib/modes";
import { TOUR } from "../lib/tour";
import {
  DEFAULT_LIMITS,
  DEFAULT_OPERATIONS,
  EVERY_MODE,
  OBLITERATE_INTRO,
  OBLITERATE_OPERATIONS,
  SAFE_LIMITS,
  SAFE_OPERATIONS,
  TYPICAL_INTRO,
  verdictTone,
  type TypicalOperation,
} from "../lib/typicalOperations";
import { primaryBtn, quietBtn } from "./ModalShell";
import Tip from "./Tip";
import { Guide, Lead, Points, Recipe, Steps, Tag } from "./helpParts";
import WhyTrustIt from "./WhyTrustIt";
import TrustDossier from "./TrustDossier";

interface Props {
  topic: HelpTopic | null;
  onClose: () => void;
  onNavigate: (topic: HelpTopic) => void;
  /** The view's version, until /api/about answers. */
  version: string;
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
  onSaveBundle: () => void;
  /** What the last Save a diagnostic bundle said ("" before one). */
  bundleNote: string;
  /** Start the guided tour at a chapter (lib/tour.ts ids); the dialog closes first. */
  onStartTourAt: (chapter: string) => void;
}

/** A link from the About page opens in the system browser: in the native window a plain anchor would navigate the
 *  app's own page away. */
const EXTERNAL = { target: "_blank", rel: "noreferrer noopener" } as const;
const REMARK_PLUGINS = [remarkGfm];
const MARKDOWN_COMPONENTS = {
  a: ({ node: _node, ...props }: React.ComponentProps<"a"> & { node?: unknown }) => <a {...props} {...EXTERNAL} />,
};

/* --- per-topic content --- */

function HowToUse({ onStartTourAt }: { onStartTourAt: (chapter: string) => void }) {
  return (
    <div className="space-y-4">
      <Lead>
        Spec Surgeon edits your Word specifications in batches. It has two everyday modes and a third that is hidden;
        the switch is in the top bar, beside these help topics. In every mode you look at the before-and-after pages
        and press one button for edited copies, and every copy is checked.
      </Lead>
      <Guide title="Three modes" tagline="Choose by what you need to change.">
        <Points
          items={[
            {
              t: "Safe — buttons, no AI",
              d: "What you control: the common changes, one button each — headers and footers from your template, the body formatting from your template (its outline styles and numbering, page, font and paragraph spacing), the font from your template or one you name, the page size and margins, find and replace, the issue stamp, END OF SECTION, numbers kept with their units, accepting or rejecting tracked changes, removing comments, highlighting, hidden text, blank lines, hyperlinks or unused styles, tidying spaces, quotes and dashes, freezing fields, removing headers and footers, a watermark, a logo, file names, the title, author and company properties, the proofing language and the Track Changes setting. What it costs: only what the buttons do. A document a button cannot change exactly is left untouched with the reason, and can be handed to Default mode. No AI is used, so it needs no API key and spends nothing.",
            },
            {
              t: "Default — a conversation",
              d: "What you control: any change you can describe in your own words, from one word to whole tables, sections and pictures, on documents built in different ways. What it costs: an Anthropic API key and the AI’s spending, shown as it accrues in the top bar; and the AI can misread a request, so you read what it plans and look at the pages before you approve. The same checks guard every copy.",
            },
            {
              t: "Obliterate — the conversation without guardrails (hidden)",
              d: (
                <>
                  Shown only when Settings → Advanced → <Tag>Show Obliterate mode</Tag> is on, and chosen only after its
                  warning, every time; the app never opens in it. What you control: {OBLITERATE_GRANTS.join(" ")} What
                  it costs: {OBLITERATE_COSTS.join(" ")} What still holds: {OBLITERATE_KEPT.join(" ")}
                </>
              ),
            },
          ]}
        />
      </Guide>
      <Steps
        items={[
          {
            t: "Safe mode: choose Safe in the top bar",
            d: "The buttons take the place of the message box, under a short note on what Safe mode is and how to get more: Default mode, with a Claude API key. Hide folds the note to one line. The window remembers the mode you last used, and whether the note is folded.",
          },
          {
            t: "Add your documents, and what a button needs",
            d: (
              <>
                Drop <Tag>.docx</Tag> files anywhere in the window. The template buttons need <Tag>Add a template</Tag>;
                swapping the logo needs the new picture; find and replace can read a table of pairs you attach.
              </>
            ),
          },
          {
            t: "Pick a button, fill in its short form, press Preview",
            d: "A button that cannot be used yet says why when you hover over it. Its form asks only for your words and choices: the words to find and their replacements, the issue stamp as it reads now and as it should, the watermark’s words, a template’s values, a font’s name, the margins. Tracked changes are off unless you tick the box. Going back to All buttons keeps what you typed in each form; Clear form empties it.",
          },
          {
            t: "Look at the pages, then create the copies",
            d: (
              <>
                <Tag>Left untouched</Tag> names any document the change cannot be made on, and why. Not what you
                wanted? <Tag>Discard preview</Tag> withdraws it and nothing is created. After the copies are created,{" "}
                <Tag>Try these in Default mode</Tag> puts those documents on your to-do list in Default mode. Press
                another button to change the copies further, or <Tag>Undo the last button</Tag> to go back to the
                copies from before it; nothing is deleted. <Tag>What you've done so far</Tag>, under the copies, lists
                every button you ran, oldest first, and a button you already ran carries a checkmark.
              </>
            ),
          },
          {
            t: "Save the buttons you ran as a recipe",
            d: (
              <>
                <Tag>Save as a recipe…</Tag>, on What you've done so far, names the buttons you ran and keeps them
                with what you filled in. The recipe joins <Tag>My recipes</Tag> above the buttons: open it on other
                documents, change a step's form or skip a step for this run, and one preview shows the pages after
                every step. <Tag>Manage</Tag> duplicates or deletes recipes, and <Tag>Edit</Tag> changes one: its name,
                what it is for, and its steps moved, removed or given another form. <Tag>Add to a saved recipe</Tag>,
                in the save dialog, puts new changes after a recipe's steps.
              </>
            ),
          },
        ]}
      />
      <Lead>Default mode, a typical first pass:</Lead>
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
            t: "Check the list of files",
            d: "Each file is checked the moment it arrives — in milliseconds, with no AI call — and listed by its name. A refused file stays greyed out with the reason and never enters a plan; the guide below says what to do about one.",
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
            d: "Type what you want and press Enter (Shift+Enter starts a new line). Spec Surgeon reads the documents before asking anything they can answer, goes ahead on sensible defaults and says what it assumed, and is told to ask at most three questions, each with the answer it recommends. Click a choice, type its number, tap a suggested reply, or answer in your own words.",
          },
          {
            t: "Look at the pages",
            d: (
              <>
                The conversation says in plain words what it will change. Beside it are rendered before-and-after pages
                for sample documents with their page counts — click a page to enlarge it — and{" "}
                <Tag>Left untouched</Tag> names any file the plan cannot cover, and why. Not what you wanted? Say what
                should be different, or <Tag>Discard preview</Tag> withdraws it: nothing is created, and your next
                message starts a new request. While the assistant is asking a question, <Tag>Drop this request</Tag>{" "}
                does the same.
              </>
            ),
          },
          {
            t: "Create edited copies",
            d: (
              <>
                One button approves exactly what is on screen, and its label counts the copies it makes: the
                documents without a preview of their own too, less any you set aside with the red X. The change is
                made on copies, every copy is checked before it is delivered, and your originals are never written.{" "}
                <Tag>Your edited copies</Tag> lists them; click one to open it in Word.
              </>
            ),
          },
          {
            t: "Keep going",
            d: (
              <>
                Ask for the next change and it is made on those copies; <Tag>What you've done so far</Tag>, under
                them, lists every request they carry, and <Tag>Save as a recipe…</Tag> keeps your requests with the
                plans you approved, to run again on other documents from <Tag>Recipes</Tag> beside the message box,
                with no AI. <Tag>Undo the last change</Tag> goes back to
                the copies from before it, one run at a time, and nothing is deleted;{" "}
                <Tag>Start over from the originals</Tag> is one tap away, and <Tag>New conversation</Tag> in the top bar
                starts an empty one. The copies live in the
                window’s scratch folder until you save them: <Tag>Export</Tag> in the top bar writes them to a folder
                you choose, and closing the window deletes what was never saved, after asking once.
              </>
            ),
          },
        ]}
      />
      <section className="rounded-xl border border-edge bg-raised/40 p-4" data-capability="tour.start">
        <h4 className="font-[family-name:var(--font-display)] text-[15px] font-semibold text-ink">The guided tour</h4>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          A fixed track through every part of the window, shown on bundled practice documents in a workspace of its
          own: real previews and real copies from the same engine, no AI call, and nothing of yours touched. Your
          conversation is set aside when it starts and comes back exactly as it was when it ends. Start at the
          beginning or at any chapter.
        </p>
        <button type="button" onClick={() => onStartTourAt(TOUR[0].id)} className={`${primaryBtn} mt-3`}>
          Start the guided tour
        </button>
        <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Tour chapters">
          {TOUR.map((chapter, index) => (
            <button
              key={chapter.id}
              type="button"
              onClick={() => onStartTourAt(chapter.id)}
              className="rounded-md border border-edge bg-surface px-2 py-1 text-[11px] text-ink-dim transition-colors hover:border-accent hover:text-accent"
              title={`Start at chapter ${index + 1}: ${chapter.title}`}
            >
              {index + 1}. {chapter.title}
            </button>
          ))}
        </div>
      </section>
      <Guide
        title="When a file arrives"
        tagline="Each file is listed by its name; only a refusal says more."
      >
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
      <Guide title="Spending, stopping and pauses" tagline="Every AI step is yours to watch, to limit and to stop.">
        <Points
          items={[
            {
              t: "You see it",
              d: "The pill in the top bar shows the estimated spending of this conversation as it accrues — an estimate from a built-in price table, never a bill. While work runs the chat shows the assistant’s words, a summary of its thinking and one plain sentence per thing it is doing.",
            },
            {
              t: "Stop — the kill switch",
              d: "While work runs, Stop beside the spending in the top bar (and the stop button in the chat) cuts off the AI step that is running at once and starts no other. Your progress is saved; Continue picks the conversation up where it stopped.",
            },
            {
              t: "Your spending check",
              d: "Settings → Spending check is the most one request (a message, a pick, an approval) may spend, as the app estimates it. An AI step that could take the request past it is not started: the work pauses with its progress saved, and Continue allows the amount again. You type the amount, or turn it off.",
            },
          ]}
        />
      </Guide>
      <p className="text-xs text-ink-faint">
        Every AI step runs inside work you started in Default or Obliterate mode: a message, a choice, or the button. Safe mode makes
        no AI call at all, and checking a file when it arrives makes none either. The app’s only request on its own is a once-a-day check for a new version, which carries
        nothing about your documents.
      </p>
    </div>
  );
}

/** The verdict column: the words as written, coloured by their first word (lib/typicalOperations.verdictTone). */
function Verdict({ can }: { can: string }) {
  const tone = verdictTone(can);
  const cls = tone === "err" ? "text-err" : tone === "warn" ? "text-warn" : "text-ok";
  return <span className={`font-medium ${cls}`}>{can}</span>;
}

/** One mode's typical operations: the job, (Safe) the buttons that do it, whether the app can, and what to know. */
function OperationsTable({ rows, withButtons }: { rows: readonly TypicalOperation[]; withButtons?: boolean }) {
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full table-fixed border-collapse text-xs leading-relaxed">
        <thead>
          <tr className="border-b border-edge text-left text-[11px] uppercase tracking-wide text-ink-faint">
            <th scope="col" className={`${withButtons ? "w-[26%]" : "w-[32%]"} py-1.5 pr-3 font-medium`}>
              Typical operation
            </th>
            {withButtons ? (
              <th scope="col" className="w-[22%] py-1.5 pr-3 font-medium">
                Buttons
              </th>
            ) : null}
            <th scope="col" className="w-[14%] py-1.5 pr-3 font-medium">
              Can it?
            </th>
            <th scope="col" className="py-1.5 font-medium">
              What to know
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.task} className="border-b border-edge/60 align-top">
              <td className="py-2 pr-3 text-ink">{row.task}</td>
              {withButtons ? (
                <td className="py-2 pr-3">
                  <div className="flex flex-wrap gap-1">
                    {(row.buttons ?? []).map((b) => (
                      <Tag key={b}>{b}</Tag>
                    ))}
                  </div>
                </td>
              ) : null}
              <td className="py-2 pr-3">
                <Verdict can={row.can} />
              </td>
              <td className="py-2 text-ink-dim">{row.note}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A heading between a mode's table and the next. */
function ModeHeading({ children }: { children: ReactNode }) {
  return <h5 className="mt-5 text-sm font-semibold text-ink">{children}</h5>;
}

function Workflows() {
  return (
    <div className="space-y-4">
      <Lead>
        Every request ends the same way — a plan you can read, pages you can look at, one button. First, what most
        people use each mode for and whether the app can do it; then the recipes for the usual jobs.
      </Lead>
      <Guide title="Typical operations" tagline={TYPICAL_INTRO}>
        <ModeHeading>Safe mode</ModeHeading>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          No model call, no key. A document a button cannot change exactly is listed under <Tag>Left untouched</Tag>{" "}
          with the reason, and one click hands it to Default.
        </p>
        <OperationsTable rows={SAFE_OPERATIONS} withButtons />
        <p className="mt-3 text-xs leading-relaxed text-ink-dim">{SAFE_LIMITS}</p>
        <ModeHeading>Default mode</ModeHeading>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">
          The conversation plus the operation library. Every request ends in the same preview, one button and the
          independent checks.
        </p>
        <OperationsTable rows={DEFAULT_OPERATIONS} />
        <p className="mt-3 text-xs leading-relaxed text-ink-dim">{DEFAULT_LIMITS}</p>
        <ModeHeading>Obliterate mode</ModeHeading>
        <p className="mt-1 text-xs leading-relaxed text-ink-dim">{OBLITERATE_INTRO}</p>
        <OperationsTable rows={OBLITERATE_OPERATIONS} />
        <p className="mt-3 text-xs leading-relaxed text-ink-dim">{EVERY_MODE}</p>
      </Guide>
      <Recipe
        title="Safe mode: clean up a set before issue"
        tagline="One button at a time, each building on the last."
        steps={[
          "Choose Safe in the top bar and drop the whole set in. Add your template if the headers, footers or font should match it.",
          "Accept or reject all tracked changes, then Remove all comments, then Remove highlighting — each one previewed, then created.",
          "Change the issue stamp to the new wording, and Add END OF SECTION where missing.",
          "Match body formatting to template (its outline styles and numbering, page and font), then Match headers and footers to template.",
          "Name files by section number and title last, so the copies carry their names.",
          "Save the issue with Export, in the top bar: PDF (Clean) writes a PDF of each copy with its comments removed and its tracked changes accepted, made by Word (or LibreOffice) from a checked clean copy; Word (Tracked Changes ON) and Word (Tracked Changes OFF) save the Word copies with their tracked changes, or with every one accepted. The copies in the window keep their comments and tracked changes.",
          "A document a button left untouched keeps its reason beside the copies; Try these in Default mode hands it to the conversation.",
        ]}
      />
      <Recipe
        title="Asking in Default mode"
        tagline="Five requests that work, word for word."
        steps={[
          "Change the fonts across a set — “Change every font to Arial 10 pt, keeping bold and italic.” If it asks whether the headers and footers are included, answer, or let its recommended answer stand.",
          "Match the headers and footers to a template — Add a template (optional) with the document whose headers and footers you want, then “Match the headers, footers and fonts to this template. Keep our technical wording.” Changes that touch the same parts of a file are put in order by Spec Surgeon itself; it is still one plan and one button. If one of your documents is a copy of the template, the window says so: remove one of them.",
          "Swap a logo — Add a picture or table (optional) with the new logo (.png, .jpg or .gif), then “Replace the logo in the first-page header with this one, at the same size.” Check the picture and its size on a sample’s pages. Your approval covers that exact file: change it after the preview and the plan is checked again.",
          "Fill in project values from a sheet — attach the sheet (.xlsx, .csv or .json) with the project names and numbers, then say what goes where: “Put the project name and number from this sheet in every footer.” The values come from your table, never from the AI.",
          "Change an issue stamp or a phrase — “Change ‘Issued for Bid’ to ‘Issued for Construction’ everywhere.” Check the new text on a sample’s pages before you approve. Words that go into a document are ones you typed or picked: a choice that would place words shows them beneath its button, and picking it approves exactly those words — or, after Edit wording, exactly your version of them. A suggested reply you tap answers a question but never becomes document wording. When no ordinary plan fits, an outcome plan Claude proposes is offered the same way, every word it would place beneath it, and runs only once you pick it.",
        ]}
      />
      <Recipe
        title="A mixed batch from different projects"
        tagline="Different structures, one approval."
        steps={[
          "Attach them together. Documents are often built differently — styled article titles in one, plain paragraphs with typed numbers in another.",
          "When one plan cannot describe the change for all of them, the plan has one part per layout, and the previews show sample pages from each.",
          "One button approves all of them.",
          "A document built differently from every layout is listed under Left untouched with a sentence: attach it on its own, or ask for a plan that covers it.",
        ]}
      />
      <Recipe
        title="One change after another"
        tagline="Each request builds on the last result."
        steps={[
          "Several changes in one request are fine: “remove the headers and footers, empty the articles, then Arial 12 pt.” Changes that touch the same parts of a file become ordered steps, in the order you asked, each planned and checked on the step before it; you still see one plan and press one button. There is no limit on how many.",
          "After the copies are created, ask for the next change. It is made on those copies, and the assistant is told what was already done, so it plans only what is new.",
          "The copies live in the window’s scratch folder until you save them: Export, in the top bar, writes them to a folder you choose.",
          "Start over from the originals — a reply you can tap, or the link under the copies — goes back to your files.",
          "Undo the last button (Safe mode) or Undo the last change (Default and Obliterate) goes back one run at a time: the next button or request builds on the copies from before it, and the copies the undone run made stay in the scratch folder until the window closes.",
          "What you've done so far, under the copies, lists each button or request the copies carry since the originals, oldest first, with how many of the documents carry it and why any other does not. Undo takes the newest off and Start over clears it; in Safe mode a button you already ran carries a checkmark.",
          "Changed your mind before the copies exist? Discard preview withdraws a waiting preview in every mode, and in Default and Obliterate Drop this request ends a request that is still open (the assistant's question, a pause at the spending check, a reply waiting to continue). Nothing is created, and your next message starts a new request; the assistant is told the last one was withdrawn.",
          "A document attached after a run is an original beside the copies, and gets only the next change.",
        ]}
      />
      <Recipe
        title="Saved recipes"
        tagline="The same changes on the next set of documents, in one preview."
        steps={[
          "Save as a recipe, on What you've done so far, keeps the changes you tick under a name, oldest first: in Safe mode each button with what you filled in its form; in Default and Obliterate each request in your words with the plan you approved and the words it places.",
          "Safe mode lists your recipes under My recipes, above the buttons; Default and Obliterate beside the message box (All recipes lists every one). Opening one shows its steps: change a button's form or skip a step for this run, then Preview.",
          "A recipe runs as one plan: every step is built from the documents attached now, checked by the same gate as any plan, and previewed once, with the pages after every step; one Create makes the copies, and Undo takes the whole recipe off. No AI is called.",
          "A step that needs a template, picture or table uses the one attached when it runs. A document a step cannot take is left untouched in Safe mode, and waits on your to-do list in Default and Obliterate, where you can ask the assistant about it.",
          "A recipe runs in its own mode and in any with fewer guardrails: Safe recipes everywhere, Default ones in Default and Obliterate, Obliterate ones only in Obliterate. A change Claude wrote a custom method for, or planned with values read from particular documents, cannot be kept, and the dialog says why.",
          "Recipes are kept on this computer beside your settings, never with a document; Manage duplicates or deletes them, and Edit changes one's name, what it is for and its steps (moved up or down, removed, or for a button given another form). A step saved from a request runs as you approved it: it is moved or removed whole, never rewritten.",
          "Save as a recipe can also add the changes you tick to the end of a recipe you already have (Add to a saved recipe).",
        ]}
      />
      <Recipe
        title="When a file is left untouched"
        tagline="Left out with a reason, never delivered half-right."
        steps={[
          "When it arrives: a refused file is greyed out with the reason and the fix.",
          "When you ask: a document you leave out of a request (“leave out 20”), or every other one when you keep a request to one (“only 23”), is not planned or changed, and is listed under Left untouched; the next request covers it again.",
          "For later: a document you set aside (“set 20 aside for later”) goes on your to-do list at once, with your reason, and stays out of every request until you work on it or put it back.",
          "At plan time: a file whose result cannot be predicted is listed under Left untouched before anything runs, and the preview of the others stands; the assistant says why in its next reply. When you create the others’ copies, it goes on your to-do list with that reason.",
          "At preview time: a document whose preview did not pass its checks while another’s did keeps its pages, marked Did not pass with the failed checks, and is listed under Left untouched; it gets no copy and never holds back the others. Only when no preview passes is Create unavailable.",
          "At delivery: a copy that fails a check is never delivered. Spec Surgeon looks at it again on its own and comes back with a question or a new plan, never a method for you to choose. A failed copy nothing will check again goes on your to-do list.",
          "While that question is open, Save N ready copies keeps the copies that already passed; then Set the rest aside for later puts the others on your to-do list and ends the request.",
          "Your to-do list says why each document is there. Work on the documents I set aside plans them on their own; Put back returns one to the others as it is. Nothing on the list is changed.",
        ]}
      />
      <Recipe
        title="Stopped, paused or closed"
        tagline="Stop and pause keep your place; closing ends the conversation."
        steps={[
          "Stop cuts off the running AI step at once; Continue picks up where it stopped.",
          "Work pauses before a request could spend more than your spending check (Settings) until you press Continue.",
          "Closing the app ends the conversation and deletes its scratch folder, after one question if a copy was never saved: save what you want to keep with Export first. A window that never closed is restored at the next start.",
        ]}
      />
    </div>
  );
}

function HowItWorks() {
  return (
    <div className="space-y-4">
      <Lead>
        A conversation on the left; your documents, the pages and the copies on the right. Under the hood:
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
            d: "The window always creates copies, in its scratch folder under the app’s local data, and writes them where you choose only when you press Export. A file left out is named with the reason.",
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
            d: "The before-and-after pages come from Microsoft Word when it is installed (private, invisible copies, up to three side by side, never your open Word), otherwise LibreOffice. Pages are kept by the document’s exact content, so creating the copies does not render again what the preview already rendered. Claude also looks at the pages; what it flags is advice for you to check.",
          },
          {
            t: "Many documents at once",
            d: "Three or more documents are edited and checked side by side, one per core of your computer, each from start to finish on its own, so every copy and its check are exactly what they would be alone.",
          },
          {
            t: "One model, with room to think",
            d: "Every AI step runs Claude Opus 5.5 with room for its full answer: the conversation and the visual check it runs at medium effort; writing a custom method at extra-high, then back; outcome planning and recovery at high. A plan usually takes one or two calls. Settings → Fast responses, off until you turn it on, has the same model write up to 2.5 times faster at twice the price.",
          },
          {
            t: "A clean start every time",
            d: "Each start opens an empty conversation in a fresh scratch folder; the last one’s folder went when its window closed, and the copies you saved with Export stay where you put them. A window that never closed is restored instead, once. Your API key is never saved with the conversation.",
          },
          {
            t: "Five colour schemes",
            d: "Settings → Appearance offers Warm Clay, Blueprint, Graphite & Teal, Carbon & Hi-Vis and Linen. The window changes at once and opens in your choice from then on.",
          },
          {
            t: "Three modes, one engine",
            d: "A Safe-mode button builds its plan in code from your form, with no AI call; in Default mode Claude writes the plan from your words. Either plan is previewed, approved and checked by the same engine, and there is nothing to run outside this window. Obliterate mode, hidden unless Settings → Advanced shows it, uses the same engine and the same checks, but a failed check no longer holds a copy back.",
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
  onSaveBundle,
  bundleNote,
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
  onSaveBundle: () => void;
  bundleNote: string;
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
          <dd className="text-ink">{about?.license_name || "MIT License and GNU AGPL v3"}</dd>
        </div>
      </dl>

      <div className="space-y-3 pt-1">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onCheckUpdate}
            disabled={checkingUpdate}
            className={quietBtn}
            data-capability="about.updates"
          >
            Check for updates
          </button>
          <Tip tip="Opens the folder of the app’s logs, to send to whoever diagnoses a problem. They never contain your documents, though an error message in them can quote a line of one.">
            <button type="button" onClick={onOpenLogs} className={quietBtn} data-capability="about.diagnostics">
              Open diagnostic logs
            </button>
          </Tip>
          <Tip tip="Saves one zip with the app’s logs, its settings, this conversation and the logs and records of its runs, and shows it in Explorer: the one file to send when something went wrong. Folders of copies and page pictures are left out, but the conversation and the records can quote your words and your documents’ text, and the conversation’s saved history can hold page images.">
            <button type="button" onClick={onSaveBundle} className={quietBtn} data-capability="about.diagnostics">
              Save a diagnostic bundle
            </button>
          </Tip>
          {status.text && !status.action && status.progress === null && (
            <span className={`text-xs ${statusTone}`} role="status">
              {status.text}
            </span>
          )}
        </div>
        {bundleNote && (
          <p className="text-xs text-ink-dim" role="status">
            {bundleNote}
          </p>
        )}
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
                  The app closes so the installer can replace it, and reopens when it is done. The installer runs
                  without its pages, so its license page is not shown: the update comes under the licenses below.
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
          Licenses
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
          {about?.copyright || "© 2026 Abraham Borg"}. {about?.license_name || "MIT License and GNU AGPL v3"}.
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
  busy,
  update,
  updateAsked,
  checkingUpdate,
  onCheckUpdate,
  onDownloadUpdate,
  onInstallUpdate,
  onOpenLogs,
  onSaveBundle,
  bundleNote,
  onStartTourAt,
}: Props) {
  // The "I'm not convinced" dossier stacks above this dialog. Local state, so leaving the topic or closing help drops
  // it too.
  const [deepDive, setDeepDive] = useState(false);
  useEffect(() => {
    if (topic !== "why-trust-it") setDeepDive(false);
  }, [topic]);

  // Escape, Tab containment and focus restoration through the one hook every dialog uses. The dossier uses the same
  // hook; the shared dialog stack makes one Escape close only the topmost of the two.
  const panelRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  useDialogFocus(Boolean(topic), panelRef, panelRef, onClose);

  // A new topic starts at its top, not wherever the last one was scrolled to.
  useEffect(() => {
    bodyRef.current?.scrollTo({ top: 0 });
  }, [topic]);

  if (!topic) return null;

  return (
    <>
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
        className={`flex max-h-[80vh] w-full ${topic === "workflows" ? "max-w-4xl" : "max-w-2xl"} flex-col overflow-hidden rounded-2xl border border-edge bg-surface shadow-2xl outline-none`}
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
          {topic === "how-to-use" && <HowToUse onStartTourAt={onStartTourAt} />}
          {topic === "workflows" && <Workflows />}
          {topic === "how-it-works" && <HowItWorks />}
          {topic === "why-trust-it" && <WhyTrustIt onDeepDive={() => setDeepDive(true)} />}
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
              onSaveBundle={onSaveBundle}
              bundleNote={bundleNote}
            />
          )}
        </div>
      </div>
    </div>
    {/* A sibling, not a child: a click on the dossier's own backdrop closes only the dossier. */}
    <TrustDossier open={deepDive} onClose={() => setDeepDive(false)} />
    </>
  );
}
