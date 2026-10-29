/**
 * The dossier's "What runs when you click" cards, as data: one card per user action and per automatic behaviour in
 * the inventory of docs/TRUST_CLAIMS.md (§1a, §1b), each answering the same five rows in the same order so the cards
 * can be compared rather than read. `ai` is exactly "None." wherever no model runs. Pure, so `node --test` can check
 * every card (frontend/tests/trust.test.ts).
 *
 * If the implementation changes, this changes with it. A trust document that has drifted from the code is worse than
 * none.
 */
import { MODEL_NAME, TRUST_FACTS as F, grouped } from "./trustFacts.ts";

export interface RuntimeCard {
  /** The inventory ids it answers (A… user actions, B… automatic behaviours). */
  covers: string[];
  title: string;
  /** "Automatic" for a behaviour no click starts directly. */
  automatic?: boolean;
  you: string;
  runs: string;
  sent: string;
  ai: string;
  bounds: string;
}

export const NONE = "None.";

/** The model as it ships; SPEC_SURGEON_MODEL replaces it (the dossier's engine section says so). */
const conversationModel = `${MODEL_NAME} (${F.model}) by default, ${F.conversationEffort} effort, up to ${grouped(F.maxOutputTokens)} output tokens per call.`;
const recoveryModel = `${MODEL_NAME} (${F.model}) by default, ${F.recoveryEffort} effort (${F.codegenEffort} while it writes a custom method), up to ${grouped(F.maxOutputTokens)} output tokens per call.`;
const codegenModel = `${MODEL_NAME} (${F.model}) by default, ${F.codegenEffort} effort while it writes the method (the conversation's next call is back at ${F.conversationEffort}), up to ${grouped(F.maxOutputTokens)} output tokens per call.`;

export const RUNTIME_CARDS: RuntimeCard[] = [
  {
    covers: ["B1", "B2"],
    title: "Opening the app",
    automatic: true,
    you: "Start Spec Surgeon.",
    runs:
      "The window opens empty in a new scratch folder under the app's local data, and what an earlier window left " +
      "under scratch is deleted — except the folder of a window that never closed, which is taken over instead: " +
      "its conversation and copies come back, and the chat says so, as it says when the previous session never " +
      `closed (the app log records what it was doing). Old installer downloads are deleted. If ${F.updateCheckHours} ` +
      "hours have passed since the last attempt, one update check starts in the background.",
    sent:
      `Only the update check: one request to ${F.githubApiHost} for the newest release (falling back to the ` +
      `${F.githubHost} releases page when GitHub limits the rate). It carries the app's version in its User-Agent ` +
      "and nothing about your documents, your key or your conversation.",
    ai: NONE,
    bounds:
      "Only the app's own scratch folders are deleted, never a file you attached by path or saved with Export; a " +
      "folder another open window holds is left alone. " +
      `At most one automatic check every ${F.updateCheckHours} hours, ${F.updateCheckTimeoutSeconds} s to answer. ` +
      `The environment variable ${F.noUpdateCheckEnv} turns it off. A failed check is written to the app log and ` +
      "changes nothing.",
  },
  {
    covers: ["A33"],
    title: "Taking the guided tour",
    you: "Tour in the top bar, the chip at the top of an empty conversation, or Help → How to use → Start the guided tour (or a chapter); then Continue, Back, End the tour, and at the end Finish and return to my conversation.",
    runs:
      "Your conversation is set aside untouched and a practice workspace takes its place: copies of the bundled " +
      "practice documents (four short specifications, a file the app refuses, a header/footer template, a logo and " +
      "a table of values) in a folder of their own under the app's data folder. Each chapter's state is built there " +
      "by the same engine as your own work — triage on arrival, a real Safe-mode button through the same gate to a " +
      "real preview, a real run that writes checked copies — and the conversation chapters show a recorded example " +
      "that says so, with Send and the answer buttons held still. When the tour ends, your conversation comes back " +
      "exactly as it was and the practice folder is deleted. Finishing it is remembered in settings.json, so the chip " +
      "stops inviting; a reload during the tour picks it up where it was from the browser's local storage.",
    sent: NONE + " Nothing leaves the computer; the tour needs no API key.",
    ai: NONE,
    bounds:
      "No model call in any chapter, and none possible: the practice workspace refuses a message or a choice before " +
      "anything could be sent. No file of yours is read or written; the practice copies are the app's own. New " +
      "conversation during a tour ends it first.",
  },
  {
    covers: ["A1"],
    title: "Saving or removing your API key",
    you: "Settings → paste your key → Save, with or without “Remember securely on this computer”; Remove app key to forget it.",
    runs:
      "The key is held in the app's memory for this session. Ticked, it is also written to Windows Credential " +
      `Manager under “${F.credentialTarget}”. Availability does not mean authentication was tested. Remove app key ` +
      "deletes the app's stored/session key and restores any launch-time environment key; external auth tokens and " +
      "SDK profiles stay intact. Removal is refused while work runs or the saved key cannot be deleted securely.",
    sent: "Nothing. Saving the key makes no request; it is first used by the next request you send.",
    ai: NONE,
    bounds:
      "The key is never written to settings.json, a conversation, a record or a log (it is scrubbed from logs by value " +
      "and by pattern). Removal does not revoke the key at Anthropic and makes no provider request.",
  },
  {
    covers: ["A3", "A36", "B3"],
    title: "Adding documents, a folder of them, a template, a picture or a table",
    you: "Add Word documents, Add a folder of documents, Add a template (optional), Add a picture or table (optional), or a drop anywhere in the window.",
    runs:
      "A file you pick is used where it is. A folder you pick gives its own .docx files, as if you had picked them " +
      "one by one: its subfolders are not entered (the chat says how many documents they hold), Word's lock files " +
      "are skipped, and nothing about the folder is remembered. A file you drop is copied into the window's scratch folder (attachments) " +
      "and used from there. Each file is then checked on arrival: does it open as a Word package, is it a .docx (not " +
      ".doc or .docm, not a ~$ lock file), does any part carry an XML document type declaration, does the body have a " +
      "paragraph. A refused file stays greyed out with the reason and never enters a plan. The rest are listed, with " +
      "notes such as typed numbers or tracked changes. Two attached documents with the same name are named once in " +
      "the chat, with both folders, in case one of them was meant as the template.",
    sent: "Nothing.",
    ai: NONE,
    bounds:
      `Milliseconds per file. A .docx is opened up to ${F.maxDocxMB} MiB; a picture or table over ` +
      `${F.maxTableOrPictureMB} MB is listed as too big and never read. Nothing beside the files you attach is looked ` +
      "for. A dropped copy stays in the scratch folder until the window closes; the file you dropped is untouched.",
  },
  {
    covers: ["A4"],
    title: "Removing an attachment",
    you: "The ✕ beside a file.",
    runs:
      "The file leaves this conversation; any plan is void and is checked again with the next request. If it was " +
      "the last file with an edited copy, the next change starts from your originals.",
    sent: "Nothing.",
    ai: NONE,
    bounds: "The file itself is not deleted or changed, and neither is a copy made of it earlier.",
  },
  {
    covers: ["A5", "B4", "B15"],
    title: "Sending a request or an answer",
    you: "Type what you want, then Send or Enter.",
    runs:
      "The conversation asks Claude. Claude reads the documents through read-only tools (paragraph text, a part's " +
      "XML, styles, a header or footer, the template, an attached table, rendered pages) and either asks you a question " +
      "or submits a plan. At your word (“set this one aside for later”) it can also put documents on your to-do list: they " +
      "leave this request and every later one until you work on them or put them back, and nothing in them changes. " +
      "The app validates the plan: its operations and settings, every file it " +
      "names, every word it would place, and a dry run on representative files. A rejected plan goes back to Claude " +
      `with the reason. An accepted plan is turned into a preview automatically: up to ${F.autoSteps} internal steps ` +
      "per action (the preview, and when needed a custom method or an outcome plan; their own cards follow).",
    sent:
      `To the Anthropic API (${F.anthropicHost}, unless ${F.apiAddressEnv} is set): your request and earlier answers, what the copies already carry, ` +
      "the last few requests Claude answered without a plan (your words and the first sentence of each answer), " +
      "the file names (and the names on your to-do list, with why each waits), the full path of an attached template, " +
      "picture or table, a profile of each kind of document " +
      "(styles, headings, header and footer text, sample paragraphs), an overview of every document (its section " +
      "number and title, its header and footer lines, its File Properties title), and whatever Claude's tools read: " +
      "paragraph text, XML, table rows, and page images rendered on your computer.",
    ai: conversationModel,
    bounds:
      "Your spending check (Settings): an AI step that could take this request past it is not started, and the work " +
      "waits for Continue. Stop cuts the running call off. A call that fails appends nothing to the conversation; " +
      "the error is shown, and Continue " +
      "goes on from where it stopped. If Claude declines to answer, the chat says so in plain words and nothing is " +
      "changed. When it declines a follow-up reply straight away, before reading anything, that reply is taken back " +
      "out of the conversation, so it is not sent again with your next one; a first request, or a reply declined " +
      "after Claude has read something, stays in the history and goes out with what follows.",
  },
  {
    covers: ["A6"],
    title: "Answering a question",
    you: "Click a choice, type its number, tap a suggested reply, or answer in your own words.",
    runs:
      "Your answer goes back into the same conversation. A choice that would place words shows them beneath its " +
      "button; picking it approves exactly those words — or, after Edit wording, exactly your version of them, " +
      "never the draft you replaced. A tapped suggested reply answers the question but is never " +
      "approved wording: it was written by Claude.",
    sent: "The same as sending a request: your answer joins the conversation's history.",
    ai: conversationModel,
    bounds: "As for a request: an answer is a request of its own for the spending check, Stop, and nothing changes in a file.",
  },
  {
    covers: ["B5", "B16"],
    title: "The preview",
    automatic: true,
    you: "Nothing: it follows a plan the app accepted.",
    runs:
      "Every attached file is planned from its original: the engine works out exactly what will change, or sets the " +
      "file aside with a reason under Left untouched (when you create the others' copies, it goes on your to-do list " +
      "with that reason). A document you asked to leave out of the request, by name or by keeping the request to " +
      `others (“only this one”), is not planned, and is named there too. Representative samples (${F.sampleFiles} files, or every file in a smaller batch; ` +
      "at least one per layout, so more when there are more layouts) are edited in memory and checked by the independent checker, their rendered pages compared as the run compares them (a layout that moved fails the sample; a sample says when that check waits for the run). " +
      "The same comparison counts the words shown on every page of the original and of the copy, less what the plan " +
      "removes: a word the original's pages show that the copy's pages do not is named in the chat and in " +
      "Claude's visual check — a warning, never a failed check, because a layout change moves words " +
      "between pages and header and footer words repeat once per page. Their before and after " +
      `pages are rendered by Microsoft Word (new, invisible instances, up to ${F.wordInstances} side by side, never the Word you have open) or, without ` +
      `Word, LibreOffice. From ${F.planProcessMinFiles} files the plans, and from ${F.processMinFiles} samples the samples, are worked out side by ` +
      "side in the app's own worker processes, each file whole in one of them. A failed preview names its check " +
      "and explains what it checks. Common refusals use plain words; the original technical evidence stays in diagnostics.",
    sent: "Nothing. Planning, editing the samples, checking and rendering all run on your computer.",
    ai: NONE,
    bounds:
      `Word has ${F.wordTimeoutSeconds} s per file, LibreOffice ${F.libreOfficeTimeoutSeconds} s per batch. A render ` +
      `that fails is reported and the preview stands without that page. At most ${F.maxProcessWorkers} worker processes, ` +
      `one per core, ending with the work. A text pattern in the plan (a selector's text_regex, a fill's pattern …) ` +
      `is at most ${grouped(F.maxPatternChars)} characters (unless it names one exact text, as the app's own selection of whole ` +
      `paragraphs does) and has ${F.regexTimeoutSeconds} s on each text it is searched on; past that ` +
      "the file is set aside with the reason (in the checker, a failed check), never searched on without a limit. " +
      "Nothing is written to your files.",
  },
  {
    covers: ["B6"],
    title: "Claude's visual check of the preview",
    automatic: true,
    you: "Nothing: it follows every rendered preview.",
    runs:
      `One call shows Claude up to ${F.visualPagesPerSample} pages of each sample, before and after, with your ` +
      "request and the plan, and tells it how many pages each sample has, so it says a page it was not shown was " +
      "not shown. Its findings are kept in the preview's records, by file and page, and are not listed in the " +
      "window. A finding Claude marks as blocking lists them beside the pages and changes the button to " +
      "“I reviewed the flagged pages — create copies”.",
    sent:
      `To the Anthropic API: the page images (at most ${F.pageImageLongEdgePx} pixels on the long side), the sample ` +
      "names and their page counts, your request, the plan's summary and assumptions, and a summary of the " +
      "conversation.",
    ai: conversationModel,
    bounds:
      "One call per preview, counted toward the pause. It is advice: it can stop nothing permanently and pass " +
      "nothing. If it fails, the preview says it was unavailable.",
  },
  {
    covers: ["B7"],
    title: "A custom method, when no built-in operation fits",
    automatic: true,
    you:
      "Nothing beyond your request: when Claude says no built-in operation can do it, but a formatting-only " +
      "change could, the app prepares the custom method without asking first.",
    runs:
      "Claude writes one small Python program (an operation) after reading sample files. The app checks the program's " +
      "text (only whitelisted modules, no file, process or network calls), runs it on copies of the samples in a " +
      "separate Python process, and checks every result with the independent checker. It may change formatting only: " +
      "no words, no new parts, no tracked changes. A failure goes back to Claude with the reason. Before it is " +
      "written, the chat says Claude's reason and what the method is to do, in Claude's words. Before you approve, " +
      "the chat and each sample's card list the words the method's sample copies add and remove, taken from the " +
      "independent checker's own comparison of each copy with its original (check V2), never from the method or the " +
      "model, or say that no words change. In Obliterate mode " +
      "(its own card) it is offered whenever any method could make the change, may change anything Word stores, " +
      "and may use the template, pictures and tables you attached (a change to a part no check reads, such as a chart, " +
      "an embedded file, stored data or the theme, is a failed check of its own, UNREAD); a result whose only " +
      "failures are its checks goes " +
      `back to Claude up to ${F.obliterateCheckRounds} times to fix them or keep each with a reason you read beside ` +
      "every copy (the package-integrity checks V1, V4, V5a, V5c and V9 cannot be kept with a reason: they go back " +
      "each round); after those rounds the latest method is used, and its copies are delivered marked UNVERIFIED " +
      "with those failures listed, which the chat says before you approve.",
    sent: "To the Anthropic API: the request, the sample files' XML and text that its tools read, and each failure's " +
      "reason; in Obliterate mode also what its tools read of the files you attached.",
    ai: codegenModel,
    bounds:
      `Up to ${F.customMethodAttempts} submissions, each sandbox run ${F.customMethodSeconds} s and ` +
      `${F.customMethodMemoryMB} MB. Every call counts toward the pause. The method edits nothing of yours: its copies ` +
      "are delivered only after your click, each checked again.",
  },
  {
    covers: ["B8"],
    title: "An outcome plan, when the built-in plan cannot be made",
    automatic: true,
    you:
      "Nothing beyond your request: when no ordinary plan fits (Claude finds no operation for it, the plan covers no " +
      "file, or no sample passes its checks), the app asks Claude for a plan of outcomes on " +
      "its own, then offers it to you as one choice, Use these outcomes and continue, with every word it would place " +
      "beneath it; nothing is tried until you pick it. A file the plan " +
      "refuses while the others plan does not start it, at whichever step of a plan done in steps, and neither does " +
      "a sample that fails its checks while another passes: that " +
      "file waits under Left untouched beside the preview of the others, and goes on your to-do list when you " +
      "create theirs. The documents you left out of the request stay out of the outcomes.",
    runs:
      "Claude proposes the outcomes to reach (what should change, where, with what wording). The app checks the " +
      "proposal and shows it; once you pick it, the app tries methods on copies of your files: built-in " +
      "operations or, when they fall short, a custom method that may make exactly the changes the outcomes name, " +
      "their wording included (not only formatting). Each copy is checked by the independent checker against those " +
      "outcomes; the plan's effects and pages are shown before anything is delivered.",
    sent: "To the Anthropic API: the request, your decisions, the file names, the profiles and the overview of every " +
      "document, and what its tools read.",
    ai: recoveryModel,
    bounds:
      `Counts toward the pause; the trials get at most ${F.recoveryAttemptsPerGroup} attempts per group of files, ` +
      "on copies only. The wording in Claude's proposed outcomes is approved by your pick of the choice, as any " +
      "choice's is: read it beneath the choice before you pick.",
  },
  {
    covers: ["A28"],
    title: "Obliterate mode: showing it, choosing it, leaving it",
    you:
      "Settings → Advanced → Show Obliterate mode, then Show it; then Obliterate in the top bar, tick the box under " +
      "its warning and press Turn on Obliterate. Back to Default, another mode in the top bar, or hiding it again " +
      "leaves it.",
    runs:
      "Showing it is kept in settings.json; being in it never is, so the next start opens in Safe or Default. While " +
      "it is on, the conversation's gate traces placed words to you or to the reply you read (Claude may write " +
      "wording; every word it places must be in its reply, or the plan is refused), a custom method may change " +
      "anything Word stores and use the files you attached (its " +
      `failed checks go back to Claude up to ${F.obliterateCheckRounds} times first, and the words its sample copies ` +
      "add and remove are listed in the chat and under each sample before you approve, from the independent " +
      "checker's own record of each copy), and a copy that fails its check is " +
      "delivered marked UNVERIFIED instead of being held back, with no recovery after it; its preview page already " +
      "says Did not pass, and the plan stands. The mark stays on every later copy built on it, in every mode, " +
      "until Start over, Undo or removing the document. Entering or leaving it " +
      "voids the plan on screen.",
    sent: "Nothing when you choose it. The conversation then sends what it sends in Default mode.",
    ai: NONE,
    bounds:
      "Copies only; your originals are never written, and a source that changed while the work ran is never " +
      `delivered over. A custom method still runs in its own process (${F.customMethodSeconds} s, ` +
      `${F.customMethodMemoryMB} MB) under the import whitelist, and the independent checker still runs on every ` +
      "copy; its results are kept beside it.",
  },
  {
    covers: ["A23", "A24", "A25", "A29", "A30", "A37"],
    title: "Safe mode: choosing a mode, pressing a button, changing your mind (in every mode), handing documents over",
    you:
      "Choose Safe or Default in the top bar; in Safe mode, read or Hide the note above the buttons (What is Safe " +
      "mode? opens it again), and use its Add a Claude API key or Switch to Default mode; pick a button, fill in " +
      "its form and press Preview; " +
      "Discard preview, All buttons or Clear form; Undo the last button; Try these in Default mode beside what a " +
      "button left untouched. In Default and Obliterate: Discard preview, Drop this request and Undo the last change.",
    runs:
      "Choosing a mode keeps it in settings.json for the next start, and so is whether the note above the buttons " +
      "is folded; the note's Add a Claude API key opens Settings, and its Switch to Default mode is the top bar's " +
      "switch. Switching modes keeps your unsent message and " +
      "Safe-mode form in this window; an unsent message holds Create only in a conversation mode. A button builds its plan in code from your form " +
      "and your attachments; the plan passes the same gate as a conversation's (its settings, the files it reads, " +
      "every word it would place traced to what you typed) and the ordinary preview runs, without Claude's visual " +
      "check. Create edited copies then makes and checks the copies as always. Discard preview (or All buttons " +
      "while a preview waits) withdraws it: nothing is created, and the window shows the copies it showed before. " +
      "Each button's form keeps what you typed until Clear form, New conversation or Start over. Undo the last " +
      "button makes the copies from before that button the ones the next button builds on again (your originals, " +
      "when it was the first), and takes off the to-do list the documents its ✗ put there. In Default and " +
      "Obliterate the same two work on a conversation: Discard preview, or Drop this request while a question, a " +
      "pause or a reply to continue is open, ends the request with nothing created (no AI call; the next request is " +
      "told it was withdrawn, and a pass over the to-do list puts its documents back on the list), and Undo the " +
      "last change goes back before the last run that delivered copies. Handing over puts " +
      "documents on your to-do list and switches to Default mode.",
    sent: "Nothing. A button never reaches the network.",
    ai: NONE,
    bounds:
      "The buttons and what they do are fixed in the app. A document a button cannot change exactly is left " +
      "untouched with the reason; nothing an AI does follows, neither a visual check nor recovery. Discard and Undo " +
      "write and delete no file: the copies an undone run made stay where they were saved. Undo goes back one " +
      "run at a time, never while a preview waits or a request is open, and refuses when a copy it would go back to " +
      "is gone. Discard is not offered while the documents are being checked again after a run: save the ready " +
      "copies, or set the rest aside for later.",
  },
  {
    covers: ["A35"],
    title: "Find and replace, occurrence by occurrence",
    you:
      "Untick an occurrence in the list under the previews (or every one at once with the box above the list), then " +
      "Update the preview.",
    runs:
      "The list shows every occurrence a find-and-replace plan found, in every document of the plan, with the words " +
      "around it. Update the preview writes the unticked occurrences into the plan (each by its file, paragraph and " +
      "place in the text), the plan passes the same gate again, and the preview is made again with those " +
      "occurrences left as they are; the checker then confirms, on every copy, that each unticked occurrence was " +
      "left untouched and every other one replaced, and each copy's record lists them all. A document the plan set " +
      "aside lists what it found too, so the occurrence its reason names can be unticked and the document goes ahead.",
    sent: "Nothing in Safe mode. In Default mode the new preview's visual check, as any preview's.",
    ai: "None to make the plan again. In Default mode the preview's visual check runs as always (advisory).",
    bounds:
      "Only the page remembers your ticks until you press Update the preview, and Create edited copies waits while " +
      "they differ from the preview's. The gate refuses an unticked occurrence the window did not record from your " +
      "own ticks, in every mode: no request can leave occurrences out on its own.",
  },
  {
    covers: ["A7"],
    title: "Marking a preview",
    you: "The ✓ or ✗ on a document's preview.",
    runs: "A ✗ marks the document to be set aside when you create the copies; it goes on your to-do list instead.",
    sent: "Nothing.",
    ai: NONE,
    bounds: "Only the page remembers the mark, until you press the button.",
  },
  {
    covers: ["A8", "B9", "B13", "B18"],
    title: "Create edited copies",
    you: "Create edited copies (or “I reviewed the flagged pages — create copies”).",
    runs:
      "The click approves exactly the plan and files on screen: if a file or the plan changed since the preview, it " +
      "is refused. In Safe and Default modes a document whose preview failed a check is left as it is, under Left " +
      "untouched with its failed checks, while the others are created; a preview none of whose samples passed " +
      "blocks Create, with its reason beside the button. Every approved file is edited from its original, checked " +
      `by the independent checker (its checks V1–V10) and rendered; ${F.processMinFiles} files or more are shared ` +
      "over the app's own worker processes, one per " +
      `core (at most ${F.maxProcessWorkers}), each file edited and checked whole in one of them, and Word renders over ` +
      `up to ${F.wordInstances} instances side by side. A copy that passes goes to a new timestamped folder; one that fails is moved to failed ` +
      "and never delivered. Then, if any copy failed, the app looks at those files again on its own (recovery) and " +
      "comes back with a question or a new plan — except after a Safe-mode button, whose copies get no recovery. A " +
      "file the preview refused or whose preview failed, or one whose copy failed with nothing to check it again, " +
      "goes on your to-do list with the reason; a click that is refused moves nothing there. In Obliterate mode a copy that " +
      "fails its check is delivered anyway, marked UNVERIFIED with the checks it failed, and no recovery runs.",
    sent:
      "Nothing to make and check the copies. Recovery, if it runs, sends the failed files' names and full paths, their " +
      `profiles and overview, failure reasons, what its tools read, page images, and up to ${F.pastRepairCasesSent} matching ` +
      "repair cases from earlier work kept on this computer.",
    ai: `None to make and check the copies. Recovery: ${recoveryModel}`,
    bounds:
      `Your originals are never written. Recovery tries at most ${F.recoveryAttemptsPerGroup} attempts per group of ` +
      `files and pauses a group after ${F.stallRepetitions} equivalent rejected tries; its calls count toward the ` +
      "pause. Its copies are delivered only after another click.",
  },
  {
    covers: ["A9"],
    title: "Save ready copies",
    you: "Save N ready copies, while a question about other documents is still open.",
    runs: "The copies recovery has already checked are exported to a new folder; the rest stay in the conversation.",
    sent: "Nothing.",
    ai: NONE,
    bounds: "Only copies that passed the independent checker. A later save goes to another new folder.",
  },
  {
    covers: ["A10", "A31"],
    title: "Your spending check, and Continue",
    you:
      "Settings → Spending check → type an amount in US dollars → Apply, or Turn off. Continue, after a pause at the " +
      "check, a Stop or a failed call.",
    runs:
      "The amount is kept in settings.json and applies from the next request (a message, a pick, an approval, a " +
      "button). Before each AI step the app adds the most that step could cost (its whole input and the longest " +
      "answer it may write) to what the request has spent; a step that could pass the amount is not started, and the " +
      "work pauses with its progress saved. Continue lets the request spend the amount again on top of what it spent " +
      "(when the price table cannot estimate the model that answered, it goes on without a check), and the work goes " +
      "on where it stopped: the conversation, a custom method being prepared, or a recovery.",
    sent: "Nothing when you set it. Continue: what the step that resumes sends (see the cards above).",
    ai: `None to set it. Continue: ${MODEL_NAME} by default, at the effort of the work it resumes.`,
    bounds:
      `$${F.spendingCheckDefaultUsd} a request until you change it. The amounts are estimates from the app's price ` +
      "table, never a bill; Anthropic's console is the exact figure. Off, nothing pauses the work for its spending. " +
      "Stop and the copies-only rules hold either way, and a recovery goes on only if its files have not changed " +
      "since it started.",
  },
  {
    covers: ["A11"],
    title: "Stop (the kill switch), or closing the window while work runs",
    you: "Stop beside the spending in the top bar, the stop button in the chat, or the window's ✕.",
    runs:
      `The running AI call is cut off: the app checks for a stop every ${F.stopPollSeconds} s and closes the ` +
      "connection, so Anthropic stops writing. No further call starts. Your progress is saved; Continue picks it up. " +
      `Closing the window does the same and waits up to ${F.closeWaitSeconds} s for the work to stop. Checking or ` +
      "writing copies does not stop part-way: it finishes first. Then the app's process ends; nothing of it stays " +
      "running.",
    sent: "Nothing new. The cut connection is the only signal.",
    ai: NONE,
    bounds:
      "The stopped call's spending is estimated from what the connection reported plus at most " +
      `${F.stoppedOutputTokensPerSecond} output tokens per second it ran. Anthropic may bill what was produced before ` +
      `the stop. If work ignores Stop for ${F.stopGraceSeconds} s, the app log records where every part of the app was.`,
  },
  {
    covers: ["B10"],
    title: "A call that failed for a passing reason",
    automatic: true,
    you: "Nothing.",
    runs:
      "A call that failed because the service was overloaded, rate-limited, gave a server error or dropped the " +
      `connection is sent again: up to ${F.retries} more times, after ${F.retryDelaysSeconds[0]} s and ` +
      `${F.retryDelaysSeconds[1]} s (longer if Anthropic asks, up to ${F.retryAfterCapSeconds} s). Nothing from a ` +
      "failed attempt is acted on, and the words it showed are taken back.",
    sent: "The same request again.",
    ai: "The same as the call it repeats.",
    bounds:
      "A request Anthropic refused (a bad key, an invalid request) is never re-sent. Each attempt counts toward the " +
      "pause, and a failed attempt keeps its estimated cost, because it may have been billed.",
  },
  {
    covers: ["A12", "A16"],
    title: "Opening a copy or enlarging a page",
    you: "Click a copy, the folder button, or a page in a preview.",
    runs:
      "A copy opens in its own application (usually Word): the file Export saved as Word (Tracked Changes ON), " +
      "while it still holds the copy's current bytes, else the working copy in the window's scratch folder (the one " +
      "with the newer content; the link's hover text says which). The folder button opens that run's folder in " +
      "Explorer. A page opens larger in the window, from the picture already rendered on your computer.",
    sent: "Nothing.",
    ai: NONE,
    bounds:
      "The window's server only serves PNG pictures from the scratch folder or the app's own folder. A working copy " +
      "opened from the scratch folder goes with it when the window closes, and one edited there after it was saved " +
      "counts as unsaved again (CHANGED beside it); save it with Export to keep it.",
  },
  {
    covers: ["A32", "A34"],
    title: "Export: Word (Tracked Changes ON), Word (Tracked Changes OFF), PDF (Clean)",
    you:
      "Export in the top bar, then one of its three choices, then a folder in the dialog (it opens where you last " +
      "saved).",
    runs:
      "Export covers every edited copy the window lists: the ones the next request builds on, and those a run " +
      "still checking other documents has already delivered. Word (Tracked Changes ON) writes each into that " +
      "folder under its planned name, byte for byte the file the checker passed, its tracked changes and comments " +
      "kept. Word (Tracked Changes OFF) and PDF (Clean) first make a clean version of each copy in the scratch " +
      "folder, from a plan fixed in the app: every tracked change accepted (comments kept), or every comment " +
      "removed and then every tracked change accepted. The clean versions pass the same gate and are checked by " +
      "the same independent checker as any copy; for PDF (Clean), Word (a new, invisible instance) or LibreOffice " +
      "turns each one that passed into a PDF, through the render cache, and the app checks each PDF opens, has " +
      "pages and carries no note. Your choice in the menu approves that fixed plan; the copies in the window are " +
      "not changed and nothing joins the history. A file already there with the same bytes is that " +
      "file, saved; a different file under the name is left alone and the new one is numbered beside it. Each copy's " +
      "destination is recorded with a fingerprint of the bytes written, in the conversation's journal: a copy " +
      "is saved while a file with its current bytes sits where Export put it: the copy and the saved file are both " +
      "compared with the fingerprint. The copies list says SAVED while they agree, CHANGED once the copy's bytes " +
      "moved (a copy opened from the window and edited in Word; the next Export writes it again, beside the file " +
      "saved before), NOT SAVED once the saved file no longer holds them (written over, truncated or edited since), " +
      "and nothing once the saved file is gone; only Word (Tracked Changes ON) saves a copy this way, since the " +
      "other two write files made from it. The folder is kept in settings.json for the next dialog.",
    sent: "Nothing.",
    ai: NONE,
    bounds:
      "Never over an input, and never into the app's scratch folder (it would go with it): a folder where a file " +
      "would land on an attached document or on a working copy, or that lies inside the scratch tree, refuses the " +
      "whole export, and nothing is written. A copy whose clean version cannot be made exactly (a change Word must " +
      "settle itself, a comment a tracked deletion would take away), does not pass every check, keeps a tracked " +
      "change anywhere (one in a style or list definition, which the accept step leaves for Word), or whose PDF does " +
      "not pass its own, gets no file and is named with why; a PDF that failed is kept apart in pdf-failed in the " +
      "scratch folder. PDF (Clean) needs Word or LibreOffice on this computer and says so before it asks for a " +
      "folder. Each file is written whole, then renamed into place. The copies in the scratch folder are unchanged, " +
      "and nothing is written until you choose in the menu.",
  },
  {
    covers: ["B19"],
    title: "Closing the window",
    automatic: true,
    you: "Close the window.",
    runs:
      "If any edited copy is not saved as it is now (never saved with Export → Word (Tracked Changes ON), changed since, its " +
      "saved file changed since, or gone; every copy and every saved file is read again here), one native " +
      "question first: OK closes and gives the " +
      "copies up, Cancel keeps the window open. Then a running AI step is cut off, the settings are saved, and the " +
      "window's scratch folder — every session folder, dropped file and the conversation's journal — is deleted.",
    sent: "Nothing.",
    ai: NONE,
    bounds:
      "Only the app's own scratch folder is deleted; what you saved with Export, the render cache, the logs and " +
      "settings.json stay. A folder a close could not delete whole (a copy held open in Word) is swept at the next " +
      "start, and a window that never closed keeps its folder for the next start to restore.",
  },
  {
    covers: ["A13", "A14", "A15", "A26", "A27"],
    title: "Start over, New conversation, and your to-do list",
    you:
      "Start over from the originals; New conversation; Work on the documents I set aside; Put back beside a document " +
      "on the to-do list; Set the rest aside for later (offered while files are checked again, once the ready copies " +
      "are saved).",
    runs:
      "Start over makes your originals the working set again; the plan is void and the to-do list is cleared. New " +
      "conversation starts an empty one over the same scratch folder, after one question when a copy is not saved " +
      "as it is now (the old conversation's copies stay in the scratch folder until the window closes). Both clear " +
      "unsent messages and staged forms after the reset succeeds. Start over keeps the attached originals; " +
      "New conversation clears the attachments. The to-do list " +
      "holds the documents set aside, each with why: the ✗ on its preview, your word to the assistant, a plan or a " +
      "Safe-mode button that could not be applied to it, or a copy that could not be made. Working on them makes them " +
      "the only documents in scope and repeats the request they were set aside from; Claude is told why each one " +
      "waited. Put back returns one to the other documents as it is. Set the rest aside puts what recovery has not " +
      "fixed on the list and ends the request; the copies already saved stay as they are.",
    sent: "Nothing, until you send the next request.",
    ai: NONE,
    bounds:
      "No copy and no original is deleted or changed. The to-do list is kept in the saved conversation; Start over " +
      "clears it.",
  },
  {
    covers: ["A17", "A18", "A20"],
    title: "Checking for, downloading and installing an update",
    you: "About → Check for updates; Download / Download and install; Restart to update; Not now.",
    runs:
      "Check asks GitHub for the newest release. Download fetches the release's SHA256SUMS and the installer named " +
      "for that version, and checks the installer's size and SHA-256 against both the list and GitHub's own digest. " +
      "It is checked again just before Setup starts. Setup runs, the app closes, and Setup reopens it. Not now hides " +
      "the banner. A link in the notes or on About opens in your own browser.",
    sent:
      `Requests to ${F.githubApiHost} and ${F.githubHost} (the file itself comes from GitHub's download host), with ` +
      "the app's version in the User-Agent. Nothing about your documents.",
    ai: NONE,
    bounds:
      `HTTPS only, redirects included; at most ${F.maxInstallerMB} MiB; any mismatch deletes the download. Only a ` +
      "copy installed by Setup updates itself, and never while work runs. The installer is unsigned: the checks prove " +
      "it is the file the release lists, not who published it.",
  },
  {
    covers: ["A19"],
    title: "Open diagnostic logs, Save a diagnostic bundle",
    you: "About → Open diagnostic logs; About → Save a diagnostic bundle.",
    runs:
      "The logs folder opens in Explorer. The bundle is one zip saved on your computer and shown in Explorer: the " +
      "app's logs and event journals, settings.json, this conversation's journal from the scratch folder, and the " +
      `text records and logs of the ${F.bundleRecentRuns} newest session folders in it.`,
    sent: "Nothing. The app never uploads a log or a bundle; sending it is yours to decide.",
    ai: NONE,
    bounds:
      "Folders of copies, samples and page pictures are left out, but the conversation's saved history can hold " +
      "page images and document text: treat a bundle like your documents.",
  },
  {
    covers: ["A22", "B17"],
    title: "Fast responses",
    you: "Settings → Fast responses → the button that names the price; one click on the switch turns it off.",
    runs:
      "The choice is kept in settings.json and applies from the next AI step: each call to a model Anthropic serves " +
      "fast asks for its fast mode, the same model at the same effort. A fast call Anthropic's fast capacity cannot " +
      "take is sent again at once at standard speed and price; if Anthropic refuses fast mode for your key, every call " +
      "runs at standard speed and Settings says why.",
    sent: "Nothing when you switch it. Later requests carry one more setting, the speed; what they carry does not change.",
    ai: NONE,
    bounds:
      `Up to ${F.fastSpeedup} times faster writing, at ${F.fastPriceFactor} times the price on every token; the ` +
      "spending pill uses the fast prices and says Fast. Off until you turn it on.",
  },
  {
    covers: ["A2", "A21"],
    title: "Colour scheme, Reload the window",
    you: "Settings → Appearance; Reload the window (shown only if the window fails to draw).",
    runs: "The scheme is saved in settings.json and applied at once. Reload redraws the page; the work behind it carries on.",
    sent: "Nothing.",
    ai: NONE,
    bounds: "Neither touches a document or the conversation.",
  },
  {
    covers: ["B11", "B12", "B14"],
    title: "The window watching itself",
    automatic: true,
    you: "Nothing.",
    runs:
      `The page tells the app's own server that it loaded, every error it did not catch, and a heartbeat every ` +
      `${F.pageBeatSeconds} s. A watchdog writes to the app log when a showing page is silent for ` +
      `${F.pageSilentSeconds} s or a job makes no progress for ${F.jobQuietSeconds} s. While work or a download runs, ` +
      `the page asks the server for news every ${F.pagePollSeconds} s. Pages rendered before are served from the render cache.`,
    sent: `Nothing leaves your computer: all of it goes to the window's own server on ${F.loopback}.`,
    ai: NONE,
    bounds:
      `The app log keeps ${F.appLogMB} MiB plus ${F.appLogBackups} older files; the event journals ` +
      `${F.eventJournalMB} MiB per file for the ${F.eventJournalProcesses} most recent past sessions; the render cache ` +
      `${F.renderCacheGB} GiB.`,
  },
];
