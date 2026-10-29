/**
 * The guided tour's manifest (adapted from Build-a-Spec's frontend/src/lib/tour.ts, copy-based reuse): chapters
 * and steps over the practice workspace the server builds from the bundled showcase (src/spec_surgeon/tutorial.py).
 *
 * The tour is a fixed track the user only watches: a step points at a real control (`anchor`, a `data-tour` value
 * in the page), names the capabilities it teaches (`capabilities`, the ids of lib/capabilities.ts), and says in a
 * few sentences what the control does. A chapter names the chapter state the server shows for it (`scenario`, one
 * of tutorial.SCENARIOS); the page asks for it when the chapter begins. Nothing is fabricated: a step whose control
 * is not on screen says so beside the workbench, and a step whose subject spends money is marked optional. Every
 * number a step quotes comes from lib/trustFacts.ts.
 */
import type { View } from "../types.ts";
import type { EndUserCapabilityId } from "./capabilities.ts";
import { TRUST_FACTS as F } from "./trustFacts.ts";

/**
 * Bump whenever chapter or step ORDER changes: the resume record (lib/tourStorage.ts) keeps numeric indexes, and a
 * stale one would land a reload on another chapter than the one it was left at. A bump discards it.
 */
export const TOUR_VERSION = 3;

/** The version a finished tour is recorded as (settings.json `tutorial_version`); bump it when the tour changes
 *  enough that someone who finished the old one should be invited again. */
export const TOUR_COMPLETION_VERSION = 2;

/** The chapter states the server builds (src/spec_surgeon/tutorial.py SCENARIOS; tests/tour.test.ts pins the two). */
export type TourScenario = "start" | "button" | "copies" | "find" | "conversation" | "question" | "plan" | "todo";

/** The form the Safe-mode chapter opens on the page before its button is pressed: the template button, with the
 *  value the server's `button` state was built with (tutorial.STAMP_VALUE; the test pins it). */
export const TOUR_FORM = {
  recipe: "headers_footers_template",
  slot: "ISSUED FOR PERMIT",
  value: "ISSUED FOR CONSTRUCTION 2026-03-01",
} as const;

/** What a held conversation control says while the tour runs (the composer, the choices, the chips, Continue). */
export const TOUR_HOLD = "Held still during the tour: its conversation is a recording, and nothing here is sent.";

/** Optional marks a step whose subject costs money to run for real; the badge says so before the reader goes
 *  looking for the control afterwards. Explanatory is everything else. */
export type TourMode = "optional" | "explanatory";

/** What the view must hold for a step's control to be on screen; unmet, the step keeps its words beside the
 *  workbench and says the control is not available, never a pretend one. */
export type TourNeeds = "samples" | "set-aside" | "action" | "results" | "left-untouched" | "todo" | "question" | "occurrences";

export interface TourStep {
  id: string;
  capabilities: readonly EndUserCapabilityId[];
  mode: TourMode;
  /** A `data-tour` value in the page. */
  anchor: string;
  needs?: TourNeeds;
  /** The page opens the Safe-mode form (TOUR_FORM) when this step is entered. */
  prepare?: "safe-form";
  title: string;
  body: string;
  placement?: "top" | "bottom" | "left" | "right";
  continueLabel?: string;
  optionalReason?: string;
}

export interface TourChapter {
  id: string;
  title: string;
  scenario: TourScenario;
  steps: readonly TourStep[];
}

export const TOUR: readonly TourChapter[] = [
  {
    id: "workspace",
    title: "Your practice workspace",
    scenario: "start",
    steps: [
      {
        id: "welcome",
        capabilities: ["tour.start", "tour.controls"],
        mode: "explanatory",
        anchor: "workbench",
        placement: "left",
        title: "A practice workspace, not your files",
        body:
          "The tour runs on four bundled specifications, a template, a logo and a table, in a workspace of its own. Your own conversation was set aside untouched the moment it began and comes back exactly as it was when the tour ends. Nothing here is sent to Claude and nothing you do here spends anything. The tour is a fixed track: it walks the window in order, Continue moves on when you are ready, and every real control stays clickable. Back goes a step back; End returns your conversation at once.",
      },
      {
        id: "top-bar",
        capabilities: ["help.topics"],
        mode: "explanatory",
        anchor: "help-nav",
        placement: "bottom",
        title: "The top bar",
        body:
          "Beside the title, a quiet line says what the conversation works on: how many documents, a template, a picture, whether the next request builds on copies. Then the guide: How to use, Workflows, How it works, Why trust it and About, one dialog with a tab strip, and Tour, which starts this tour again at any time.",
      },
      {
        id: "modes",
        capabilities: ["header.modes"],
        mode: "explanatory",
        anchor: "mode-switch",
        placement: "bottom",
        title: "Two modes, and a hidden third",
        body:
          "Safe puts buttons for the common changes where the message box is and calls no AI at all: no key needed, nothing spent. Default is the conversation: describe any change in your own words, an AI model plans it, you look at the pages and approve. The window opens in the mode you last used. Obliterate, hidden unless Settings shows it, is the conversation without the guardrails that protect your documents' content. The tour switches modes for you as its chapters need, so the switch is held still while it runs.",
      },
      {
        id: "spending",
        capabilities: ["header.spending", "header.connection", "header.stop"],
        mode: "explanatory",
        anchor: "spend-pill",
        placement: "bottom",
        title: "Spending and the connection, always in view",
        body:
          `The pill estimates this conversation's spending as it accrues, from a built-in price table, never a bill. While work runs in Default mode a red Stop appears beside it: the kill switch, which cuts off the running AI step at once and starts no other. The next pill says whether an API key is available; in Safe mode it says no AI is used. Your spending check (Settings) pauses a request before it could spend more than $${F.spendingCheckDefaultUsd} until you continue.`,
      },
      {
        id: "settings",
        capabilities: [
          "header.settings",
          "settings.api-key",
          "settings.spending-check",
          "settings.fast-responses",
          "settings.appearance",
          "settings.advanced",
        ],
        mode: "explanatory",
        anchor: "settings",
        placement: "bottom",
        title: "Settings",
        body:
          `One dialog: AI connection (paste your key; Remember securely keeps it in Windows Credential Manager), Spending check (the most one request may spend, or off), Fast responses (the same model writing up to ${F.fastSpeedup} times faster at ${F.fastPriceFactor} times the price, off until you turn it on), Appearance (five colour schemes, applied at once and kept) and Advanced, which shows Obliterate mode in the top bar. Nothing else is configurable.`,
      },
    ],
  },
  {
    id: "documents",
    title: "Your documents",
    scenario: "start",
    steps: [
      {
        id: "documents",
        capabilities: ["documents.add"],
        mode: "explanatory",
        anchor: "tray-documents",
        placement: "left",
        title: "Every file is checked when it arrives",
        body:
          `Add Word documents opens a file dialog, and dropping .docx files anywhere in the window attaches them too; only the files you attach are edited, nothing is picked up from their folders. The moment a file arrives it is read, in milliseconds and with no AI call. One file here is greyed out with its reason: it is not a Word package at all, so it never enters a plan. The rest are listed by name; a .docx is opened up to ${F.maxDocxMB} MiB.`,
      },
      {
        id: "folder",
        capabilities: ["documents.folder"],
        mode: "explanatory",
        anchor: "tray-documents",
        placement: "left",
        title: "A whole folder at once",
        body:
          "Add a folder of documents opens a folder dialog and attaches every .docx file the folder holds, as if you had picked them one by one: each is checked on arrival the same way. Its subfolders are not entered; the window says how many documents they hold, and you add those folders too if you want them. Word's own lock files are skipped. Nothing is remembered about the folder.",
      },
      {
        id: "template",
        capabilities: ["documents.template"],
        mode: "explanatory",
        anchor: "tray-template",
        placement: "left",
        title: "One template, never edited",
        body:
          "A template is one reference document to copy from: an architect's headers and footers, a firm's fonts and styles. It is never part of the batch. This one is a prepared template, with placeholders for the section number and title that each document fills from its own first lines, and one placeholder the form asks you to type. An ordinary specification works as a template too: wherever its headers show its own number and title, each document's own go.",
      },
      {
        id: "references",
        capabilities: ["documents.references"],
        mode: "explanatory",
        anchor: "tray-references",
        placement: "left",
        title: "Pictures and tables for the edit to use",
        body:
          `A picture (.png, .jpg, .gif, .bmp) can be a new logo to swap in; a table (.xlsx, .csv, .json) can hold project values to fill in or pairs of words to find and replace. Each is tagged Picture or Table. Claude is told about them and can read the tables; values a table gives are yours, never Claude's words. One over ${F.maxTableOrPictureMB} MB is listed as too big and never read.`,
      },
      {
        id: "remove",
        capabilities: ["documents.remove"],
        mode: "explanatory",
        anchor: "tray-documents",
        placement: "left",
        title: "Removing, and what a change to the list does",
        body:
          "The ✕ beside a file takes it out of this conversation; the file itself is not touched. Adding or removing a document after a plan exists voids that plan: the conversation says so and the button reads Continue until the documents have been checked against your request again. Each section of this list folds to one line once a plan exists; click its title to open it.",
      },
    ],
  },
  {
    id: "safe-buttons",
    title: "Safe mode: the buttons",
    scenario: "start",
    steps: [
      {
        id: "note",
        capabilities: ["safe.note", "safe.default"],
        mode: "explanatory",
        anchor: "safe-note",
        placement: "right",
        title: "What Safe mode is, and where to go for more",
        body:
          "The note above the buttons says what Safe mode is: fixed buttons, no AI, no API key needed. It also says what it cannot do. For anything else, Default mode takes a change you describe in plain English and needs a Claude API key; the note's buttons switch to it or open Settings to add the key. Hide folds the note to one line, and What is Safe mode? opens it again.",
      },
      {
        id: "buttons",
        capabilities: ["safe.buttons"],
        mode: "explanatory",
        anchor: "safe-buttons",
        placement: "right",
        title: "The common changes, one button each",
        body:
          "Headers and footers from your template, a watermark, the logo; fonts, page size and margins; find and replace, the issue stamp, END OF SECTION, units kept on their line; accepting or rejecting tracked changes, removing comments, highlighting, hidden text, blank lines, links and unused styles, tidying spaces, quotes and fields; file names and properties, the proofing language and Track Changes. A button that cannot be used yet says why when the pointer rests on it, such as no template attached. No model is called by any of them.",
      },
      {
        id: "form",
        capabilities: ["safe.form"],
        mode: "explanatory",
        anchor: "safe-form",
        prepare: "safe-form",
        placement: "right",
        title: "A short form, then Preview",
        body:
          "The tour opened Match headers and footers to template and filled its one field for you. The template's section number and title come from each document; its other placeholder, the issue stamp, is typed here, exactly as it should appear. Every word a button places is one you typed in its form or read from a file you attached. Going back to All buttons keeps what you typed; Clear form empties it. Preview shows the pages before and after and makes no copy yet.",
      },
    ],
  },
  {
    id: "preview",
    title: "The preview, before anything changes",
    scenario: "button",
    steps: [
      {
        id: "previews",
        capabilities: ["preview.pages", "zoom.controls"],
        mode: "explanatory",
        anchor: "previews",
        needs: "samples",
        placement: "left",
        title: "Sample pages, before and after",
        body:
          `The button's plan was built in code from the form, passed the same gate every plan passes, and was applied and checked in memory on sample documents; the sources were not touched. Each card says Checked when its sample passed every check, shows the page count before and after, and renders the pages side by side when Word or LibreOffice is on this computer (click a page to enlarge it: Fit, 100% and Ctrl+wheel). Up to ${F.sampleFiles} samples are shown, at least one per layout; without a renderer the card says the pages were not checked.`,
      },
      {
        id: "marks",
        capabilities: ["preview.marks"],
        mode: "explanatory",
        anchor: "review-marks",
        needs: "samples",
        placement: "left",
        title: "The green checkmark and the red X",
        body:
          "Every preview carries two marks. The green checkmark says the pages look right. The red X sets that document aside for more work: when you create the copies it gets none and goes on your to-do list, and the button says so. The marks belong to the plan on screen; a new plan starts the review over. Only the page remembers a mark until you press the button.",
      },
      {
        id: "left-untouched",
        capabilities: ["preview.left-untouched"],
        mode: "explanatory",
        anchor: "left-untouched",
        needs: "set-aside",
        placement: "left",
        title: "Left untouched, with the reason",
        body:
          "One of the four specifications is built the way another firm writes them, with no SECTION line, so the template's placeholders cannot be read from it. Rather than guess, the engine leaves it out before anything runs and says why, in plain words. The preview of the others stands. A document whose preview did not pass its checks while the others' did is listed here too, its pages still shown so you see why: it gets no copy, and never holds back the others. In Default mode a document you leave out of a request by name is listed here as well.",
      },
      {
        id: "action",
        capabilities: ["action.primary", "action.discard"],
        mode: "explanatory",
        anchor: "action-bar",
        needs: "action",
        placement: "top",
        title: "One button, tied to what is on screen",
        body:
          "Create edited copies approves exactly the plan and pages shown: if a document or the plan changed since the preview, the click is refused until you have looked again, and a failed check blocks it with its reason. Discard preview withdraws a preview and creates nothing, in every mode; in Default and Obliterate the request ends with it, and Drop this request ends one still open at a question. The button's own sentence is your line in the chat above, and the changes it made join the history the next request hears. The tour leaves this preview as it is; the next chapter shows the copies it would make.",
      },
    ],
  },
  {
    id: "copies",
    title: "Your edited copies",
    scenario: "copies",
    steps: [
      {
        id: "results",
        capabilities: ["results.copies", "results.folder"],
        mode: "explanatory",
        anchor: "results",
        needs: "results",
        placement: "left",
        title: "Copies, checked word for word",
        body:
          "These copies were really made from the practice documents by the same engine that edits yours: each from its original, each checked by an independent checker that shares no code with the editing, and delivered only because it passed. Click one to open it in Word; the folder button opens the run's folder. Originals are never written. A copy that fails a check is moved aside and never delivered. The tour marked one document with the red X before creating these, so it got no copy.",
      },
      {
        id: "export",
        capabilities: ["header.export", "header.export-kinds"],
        mode: "explanatory",
        anchor: "header-export",
        needs: "results",
        placement: "bottom",
        title: "Save the copies with Export",
        body:
          "The copies live in the window's scratch folder until you save them. Export, here in the top bar, offers three choices, then asks for a folder. Word (Tracked Changes ON) writes each copy as it is, byte for byte what the checker passed. Word (Tracked Changes OFF) accepts every tracked change and keeps the comments. PDF (Clean) removes the comments, accepts the tracked changes and makes a PDF with Word or LibreOffice. The clean versions are checked like any copy; your copies stay as they are, and no attached file is written over. The button counts the copies not saved as Word, and the close asks once before they go. The practice copies are not yours, so the button is held still during the tour.",
      },
      {
        id: "working-line",
        capabilities: ["results.start-over", "results.undo"],
        mode: "explanatory",
        anchor: "working-line",
        needs: "results",
        placement: "left",
        title: "The next change builds on these copies",
        body:
          "After a run the copies are the working set: the next button or request is made on them, and your originals stay as they are. Start over from the originals goes back to your files; Undo the last button (Undo the last change in Default and Obliterate) goes back one run at a time, so the next button or request builds on the copies from before it. Neither writes or deletes a file: the copies an undone run made stay where they were saved.",
      },
      {
        id: "hand-off",
        capabilities: ["results.hand-off"],
        mode: "explanatory",
        anchor: "left-untouched-button",
        needs: "left-untouched",
        placement: "left",
        title: "Left untouched by the last button",
        body:
          "The document the button could not change exactly waits here with the engine's reason, beside the copies, until the next button or a hand-off. Try these in Default mode puts it on your to-do list and switches to Default mode, where you describe what should be different and an AI model plans it.",
      },
      {
        id: "todo-safe",
        capabilities: ["todo.card"],
        mode: "explanatory",
        anchor: "todo-card",
        needs: "todo",
        placement: "right",
        title: "Set aside for more work",
        body:
          "The document marked with the red X is on your to-do list, with why. In Safe mode there is no conversation to plan it again, so the card offers Default mode. Documents on the list are tagged To do among your documents and stay out of every plan until you work on them or put one back. Nothing on the list is changed.",
      },
    ],
  },
  {
    id: "find",
    title: "Find and replace, occurrence by occurrence",
    scenario: "find",
    steps: [
      {
        id: "occurrences",
        capabilities: ["preview.occurrences", "preview.occurrences-all"],
        mode: "explanatory",
        anchor: "occurrences",
        needs: "occurrences",
        placement: "left",
        title: "Every occurrence, with a checkbox",
        body:
          "The Find and replace button lists every occurrence it found, in every document of the plan, with the words around it. Untick one to leave it as it is: a historical reference, a line that should keep the old wording. The box above the list ticks or unticks them all at once (and the box beside a document's name, that document's). Update the preview makes the plan and pages again with the unticked ones left out, and Create edited copies waits until you do. The checker confirms each unticked occurrence was left untouched. Here one in the second document is already unticked. A document the plan set aside lists what it found too, so the occurrence its reason names can be unticked.",
      },
    ],
  },
  {
    id: "conversation",
    title: "Default mode: the conversation",
    scenario: "conversation",
    steps: [
      {
        id: "composer",
        capabilities: ["chat.composer", "chat.stop"],
        mode: "optional",
        anchor: "composer",
        placement: "top",
        title: "Describe the result in your own words",
        body:
          "Type what you want and press Enter (Shift+Enter starts a new line). Spec Surgeon reads the documents before asking anything they can answer, goes ahead on sensible defaults and says what it assumed, and asks at most three questions, each with the answer it recommends. While work runs the box shows Stop instead of Send and the reply streams in as it is written, with a line above it naming each step. During the tour the box is held still: its conversation is a recording and sends nothing.",
        optionalReason: "A real message needs your API key and spends on the model.",
      },
      {
        id: "starters",
        capabilities: ["chat.starters"],
        mode: "explanatory",
        anchor: "starters",
        placement: "right",
        title: "Three ways in, and this tour",
        body:
          "An empty conversation offers three starter requests; clicking one puts its words in the box for you to edit and send. Above them sits the chip that opens this tour; once you have finished it, the chip invites you to take it again. Typing your own opening does the same job as a starter.",
      },
    ],
  },
  {
    id: "questions",
    title: "Questions and answers",
    scenario: "question",
    steps: [
      {
        id: "question-card",
        capabilities: ["chat.choices"],
        mode: "explanatory",
        anchor: "prompts",
        needs: "question",
        placement: "right",
        title: "One thing to clarify",
        body:
          "When something needs your decision, a card shows the question and its answers as buttons; you can click one, type its number, or answer in your own words. A choice that would put words into the documents shows those exact words beneath its button, and picking it approves exactly those words and nothing else. Edit wording opens a drafted wording in a box: Use this wording approves your version, never the draft you replaced. This conversation is a bundled example, as its first line says.",
      },
      {
        id: "chips",
        capabilities: ["chat.chips"],
        mode: "explanatory",
        anchor: "prompt-chips",
        needs: "question",
        placement: "right",
        title: "Suggested replies",
        body:
          "The chips are short replies you can tap. A tapped reply is your decision and is sent as your answer, but it is never approved wording: the words are Claude's, and words that go into a document are always ones you typed or picked. 'Leave out' keeps a document out of this one request; 'set aside' puts it on your to-do list until you come back to it.",
      },
      {
        id: "pauses",
        capabilities: ["chat.pause"],
        mode: "optional",
        anchor: "composer",
        placement: "top",
        title: "Stop, and the pause at your spending check",
        body:
          `Stop cuts off the AI step that is running at once; your progress is saved and Continue picks the conversation up where it stopped. Before each AI step the app adds the most that step could cost to what the request has spent; a step that could pass your spending check is not started, and a card in the chat says what the request spent and offers Continue (up to $${F.spendingCheckDefaultUsd} more by default). The amounts are estimates from the app's price table.`,
        optionalReason: "A live run is what pauses; the tour runs none.",
      },
    ],
  },
  {
    id: "plan",
    title: "The plan in Default mode",
    scenario: "plan",
    steps: [
      {
        id: "plan-pages",
        capabilities: ["preview.pages"],
        mode: "explanatory",
        anchor: "previews",
        needs: "samples",
        placement: "left",
        title: "The chat says what will change; the pages show it",
        body:
          "When the plan is ready the assistant says in plain words what will change and where, and the window shows the before and after pages of sample documents; there is no separate card listing the changes. This plan is the worked example 'change every font to Arial', built in code for the tour and previewed by the ordinary path; in your own work Claude writes the plan from your words, the app validates it, and the preview follows. Changes that touch the same parts of a file become ordered steps inside one plan; you still press one button.",
      },
      {
        id: "left-out",
        capabilities: ["preview.left-untouched"],
        mode: "explanatory",
        anchor: "left-untouched",
        needs: "set-aside",
        placement: "left",
        title: "A document you leave out",
        body:
          "The request said to leave out 21 05 00 for now, so the plan covers the others and 21 05 00 is listed here, exactly as it is; your next request covers it again unless you leave it out again. A file whose result the engine cannot predict is listed here too, with the reason, and goes on your to-do list when you create the others' copies.",
      },
      {
        id: "red-x",
        capabilities: ["action.primary", "preview.marks"],
        mode: "explanatory",
        anchor: "action-bar",
        needs: "action",
        placement: "top",
        title: "Create, with some set aside",
        body:
          "Mark a preview with the red X and the button's label counts it: that document goes on your to-do list and the others get their copies. A document that is not part of the displayed plan can never be set aside or run. If Claude's visual check of the pages flags something as blocking, the button changes to say you reviewed the flagged pages: a separate, explicit decision.",
      },
    ],
  },
  {
    id: "todo",
    title: "The to-do list",
    scenario: "todo",
    steps: [
      {
        id: "todo-card",
        capabilities: ["todo.card"],
        mode: "explanatory",
        anchor: "todo-card",
        needs: "todo",
        placement: "right",
        title: "Work on them when you are ready",
        body:
          "The copies were created for every document but the one marked with the red X, which waits here with why. Work on it now sends 'Work on the documents I set aside': a pass over those documents alone, from the request they were set aside from, with the assistant told why each one waited. Tell it what should be different, or continue to plan them again as before. The pass ends when their copies are created.",
      },
      {
        id: "todo-list",
        capabilities: ["todo.put-back"],
        mode: "explanatory",
        anchor: "todo-list",
        needs: "todo",
        placement: "left",
        title: "The same list beside the copies",
        body:
          "Every document on the list says why it is there: the red X, your word to the assistant, a plan that could not be applied to it, or a copy that could not be made. Put back takes one off without a pass: it is among the documents the next request works on, as it is. Start over from the originals clears the list. The list is kept in the saved conversation.",
      },
      {
        id: "building-on",
        capabilities: ["results.copies", "results.start-over"],
        mode: "explanatory",
        anchor: "working-line",
        needs: "results",
        placement: "left",
        title: "Each request builds on the last",
        body:
          "Your next request is made on these copies, and the assistant is told what each copy already carries, so it plans only what is new; a change some copies got and others did not is named per document. Each run's copies go into their own timestamped folder under the app's local data, so earlier copies remain. Start over from the originals is one tap away.",
      },
    ],
  },
  {
    id: "help",
    title: "Help, trust, about and updates",
    scenario: "conversation",
    steps: [
      {
        id: "help-topics",
        capabilities: ["help.topics", "help.trust"],
        mode: "explanatory",
        anchor: "help-nav",
        placement: "bottom",
        title: "The guide, and why to trust it",
        body:
          "How to use walks each mode step by step; Workflows lists the typical operations of each mode, whether the app can do each, and recipes for the usual jobs; How it works is the mechanics in plain words. Why trust it gives eight claims, each with the mechanism that makes it true, and I'm not convinced opens the long answer: every action with what runs, what is sent, which AI is involved and what bounds it. Every claim there is pinned to the code by a test.",
      },
      {
        id: "about",
        capabilities: ["about.updates", "about.diagnostics", "update.banner", "window.reload"],
        mode: "optional",
        anchor: "help-nav",
        placement: "bottom",
        title: "About: updates and diagnostics",
        body:
          `About shows the version, the model, the release notes and the licenses. Check for updates asks GitHub for a new version (the app also checks on its own at most once every ${F.updateCheckHours} hours, carrying nothing about your documents); a new one shows as a banner above the panes with Download, and Not now hides it. Open diagnostic logs and Save a diagnostic bundle are what to send when something went wrong; nothing is ever uploaded. If the page ever fails to draw, Reload the window redraws it while the work behind it carries on.`,
        optionalReason: "Downloading an update closes the app to install it.",
      },
      {
        id: "obliterate",
        capabilities: ["obliterate.warning"],
        mode: "explanatory",
        anchor: "settings",
        placement: "bottom",
        title: "Obliterate mode, behind a warning",
        body:
          "Settings → Advanced → Show Obliterate mode adds a third choice to the switch. Choosing it opens its warning every time: what it grants (Claude may write wording itself, a custom method may change anything Word stores, a copy that fails a check is delivered marked UNVERIFIED), what it costs, and what still holds (copies only, the sandbox, the independent checks on every copy). Turn on Obliterate works only once the box under it is ticked, and the app never opens in it.",
      },
    ],
  },
  {
    id: "finish",
    title: "Finish",
    scenario: "conversation",
    steps: [
      {
        id: "finish",
        capabilities: ["header.new-conversation", "tour.finish"],
        mode: "explanatory",
        anchor: "new-conversation",
        placement: "bottom",
        title: "Your conversation comes back",
        body:
          "There is one ending, and this is it: your own conversation returns exactly as it was before the tour, the same documents, messages and copies, and the practice workspace is discarded. Finish now, press End on any step, or press New conversation, which ends the tour first; every one of them puts your conversation back. New conversation then archives it and starts an empty one. Help → Tour starts this tour again at any time, at any chapter.",
        continueLabel: "Finish and return to my conversation",
      },
    ],
  },
] as const;

/** The step's control is on screen for the view the server sent; false keeps the step's words beside the workbench. */
export function stepNeedsMet(step: Pick<TourStep, "needs">, view: View): boolean {
  switch (step.needs) {
    case undefined:
      return true;
    case "samples":
      return view.samples.length > 0;
    case "set-aside":
      return (view.plan?.set_aside.length ?? 0) > 0;
    case "action":
      return view.action.kind !== "none" && !!view.action.token;
    case "results":
      return view.results.length > 0;
    case "left-untouched":
      return (view.left_untouched ?? []).length > 0;
    case "todo":
      return (view.todo ?? []).length > 0;
    case "occurrences":
      return (view.occurrences ?? []).length > 0;
    case "question":
      return view.stage === "question";
  }
}

/** The `data-tour` selector of a step. */
export function anchorSelector(step: Pick<TourStep, "anchor">): string {
  return `[data-tour="${step.anchor}"]`;
}

/** Every capability the manifest teaches. */
export function capabilityCoverage(): ReadonlySet<EndUserCapabilityId> {
  return new Set(TOUR.flatMap((chapter) => chapter.steps.flatMap((step) => step.capabilities)));
}

/** The chapter's index for an id or an index, clamped into the manifest. */
export function chapterIndex(chapter: string | number): number {
  const requested = typeof chapter === "number" ? chapter : TOUR.findIndex((c) => c.id === chapter);
  return Math.min(Math.max(requested, 0), TOUR.length - 1);
}
