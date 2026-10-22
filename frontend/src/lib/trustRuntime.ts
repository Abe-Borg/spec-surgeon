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
const recoveryModel = `${MODEL_NAME} (${F.model}) by default, ${F.recoveryEffort} effort, up to ${grouped(F.maxOutputTokens)} output tokens per call.`;

export const RUNTIME_CARDS: RuntimeCard[] = [
  {
    covers: ["B1", "B2"],
    title: "Opening the app",
    automatic: true,
    you: "Start Spec Surgeon.",
    runs:
      "The last conversation is moved to the conversations folder beside settings.json and the window opens empty. " +
      "If the previous session never closed, the chat says so and the app log records what it was doing. Old " +
      `installer downloads are deleted. If ${F.updateCheckHours} hours have passed since the last attempt, one ` +
      "update check starts in the background.",
    sent:
      `Only the update check: one request to ${F.githubApiHost} for the newest release (falling back to the ` +
      `${F.githubHost} releases page when GitHub limits the rate). It carries the app's version in its User-Agent ` +
      "and nothing about your documents, your key or your conversation.",
    ai: NONE,
    bounds:
      `At most one automatic check every ${F.updateCheckHours} hours, ${F.updateCheckTimeoutSeconds} s to answer. ` +
      `The environment variable ${F.noUpdateCheckEnv} turns it off. A failed check is written to the app log and ` +
      "changes nothing.",
  },
  {
    covers: ["A1"],
    title: "Connecting your API key",
    you: "Settings → paste your key → Save, with or without “Remember securely on this computer”.",
    runs:
      "The key is held in the app's memory for this session. Ticked, it is also written to Windows Credential " +
      `Manager under “${F.credentialTarget}”. A key already in the ANTHROPIC_API_KEY environment variable is used first.`,
    sent: "Nothing. Saving the key makes no request; it is first used by the next request you send.",
    ai: NONE,
    bounds:
      "The key is never written to settings.json, a conversation, a record or a log (it is scrubbed from logs by value " +
      "and by pattern). The window has no button that deletes a remembered key: remove it in Credential Manager.",
  },
  {
    covers: ["A3", "B3"],
    title: "Adding documents, a template, a picture or a table",
    you: "Add Word documents, Add a template (optional), Add a picture or table (optional), or a drop anywhere in the window.",
    runs:
      "A file you pick is used where it is. A file you drop is copied into the attachments folder beside settings.json " +
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
      "for. Dropped copies stay in the attachments folder until you delete them.",
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
      "or submits a plan. The app validates the plan: its operations and settings, every file it " +
      "names, every word it would place, and a dry run on representative files. A rejected plan goes back to Claude " +
      `with the reason. An accepted plan is turned into a preview automatically: up to ${F.autoSteps} internal steps ` +
      "per action (the preview, and when needed a custom method or an outcome plan; their own cards follow).",
    sent:
      `To the Anthropic API (${F.anthropicHost}, unless ${F.apiAddressEnv} is set): your request and earlier answers, what the copies already carry, ` +
      "the file names, the full path of an attached template, picture or table, a profile of each kind of document " +
      "(styles, headings, header and footer text, sample paragraphs), an overview of every document (its section " +
      "number and title, its header and footer lines, its File Properties title), and whatever Claude's tools read: " +
      "paragraph text, XML, table rows, and page images rendered on your computer.",
    ai: conversationModel,
    bounds:
      `No spending ceiling. Every call counts toward a pause after ${F.stepsPerPause} AI steps. Stop now cuts the ` +
      "running call off. A call that fails appends nothing to the conversation; the error is shown, and Continue " +
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
      "button; picking it approves exactly those words. A tapped suggested reply answers the question but is never " +
      "approved wording: it was written by Claude.",
    sent: "The same as sending a request: your answer joins the conversation's history.",
    ai: conversationModel,
    bounds: `As for a request: the ${F.stepsPerPause}-step pause, Stop now, and nothing changes in a file.`,
  },
  {
    covers: ["B5"],
    title: "The preview",
    automatic: true,
    you: "Nothing: it follows a plan the app accepted.",
    runs:
      "Every attached file is planned from its original: the engine works out exactly what will change, or sets the " +
      "file aside with a reason under Left untouched. A document you asked to leave out of the request is not " +
      `planned, and is named there too. Representative samples (${F.sampleFiles} files, or every file in a smaller batch; ` +
      "at least one per layout, so more when there are more layouts) are edited in memory and checked by the independent checker. Their before and after " +
      "pages are rendered by Microsoft Word (a new, invisible instance, never the Word you have open) or, without " +
      "Word, LibreOffice.",
    sent: "Nothing. Planning, editing the samples, checking and rendering all run on your computer.",
    ai: NONE,
    bounds:
      `Word has ${F.wordTimeoutSeconds} s per file, LibreOffice ${F.libreOfficeTimeoutSeconds} s per batch. A render ` +
      "that fails is reported and the preview stands without that page. Nothing is written to your files.",
  },
  {
    covers: ["B6"],
    title: "Claude's visual check of the preview",
    automatic: true,
    you: "Nothing: it follows every rendered preview.",
    runs:
      `One call shows Claude up to ${F.visualPagesPerSample} pages of each sample, before and after, with your ` +
      "request and the plan. Its findings appear beside the pages, by file and page. A finding Claude marks as " +
      "blocking changes the button to “I reviewed the flagged pages — create copies”.",
    sent:
      `To the Anthropic API: the page images (at most ${F.pageImageLongEdgePx} pixels on the long side), the sample ` +
      "names, your request, the plan's summary and assumptions, and a summary of the conversation.",
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
      "no words, no new parts, no tracked changes. A failure goes back to Claude with the reason.",
    sent: "To the Anthropic API: the request, the sample files' XML and text that its tools read, and each failure's reason.",
    ai: conversationModel,
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
      "file, or a sample fails its check), the app continues on its own. A file the plan refuses while the others " +
      "plan does not start it: that file waits under Left untouched beside the preview of the others.",
    runs:
      "Claude proposes the outcomes to reach (what should change, where, with what wording). The app checks the " +
      "proposal, carries it out without waiting for a pick, and tries methods on copies of your files: built-in " +
      "operations or, when they fall short, a custom method that may make exactly the changes the outcomes name, " +
      "their wording included (not only formatting). Each copy is checked by the independent checker against those " +
      "outcomes; the plan's effects and pages are shown before anything is delivered.",
    sent: "To the Anthropic API: the request, your decisions, the file names, the profiles and the overview of every " +
      "document, and what its tools read.",
    ai: recoveryModel,
    bounds:
      `Counts toward the pause; the trials get at most ${F.recoveryAttemptsPerGroup} attempts per group of files, ` +
      "on copies only. The wording in Claude's proposed outcomes is treated as " +
      "approved on this path without a separate pick: read the plan before you approve it.",
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
    covers: ["A8", "B9", "B13"],
    title: "Create edited copies",
    you: "Create edited copies (or “I reviewed the flagged pages — create copies”).",
    runs:
      "The click approves exactly the plan and files on screen: if a file or the plan changed since the preview, it " +
      "is refused. Every approved file is edited from its original, checked by the independent checker (its checks " +
      "V1–V10) and rendered. A copy that passes goes to a new timestamped folder; one that fails is moved to failed " +
      "and never delivered. Then, if any copy failed, the app looks at those files again on its own (recovery) and " +
      "comes back with a question or a new plan.",
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
    covers: ["A10"],
    title: "Continue",
    you: "Continue, after a pause, a Stop or a failed call.",
    runs:
      `After a pause the allowance grows by ${F.stepsPerPause} more AI steps. Then the work goes on where it stopped: ` +
      "the conversation, a custom method being prepared, or a recovery.",
    sent: "What the step that resumes sends (see the cards above).",
    ai: `${MODEL_NAME} by default, at the effort of the work it resumes.`,
    bounds: "The same pause, Stop now and copies-only rules. A recovery goes on only if its files have not changed since it started.",
  },
  {
    covers: ["A11"],
    title: "Stop now, or closing the window while work runs",
    you: "Stop now, or the window's ✕.",
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
      "A copy opens in its own application (usually Word); the folder opens in Explorer. A page opens larger in the " +
      "window, from the picture already rendered on your computer.",
    sent: "Nothing.",
    ai: NONE,
    bounds: "The window's server only serves PNG pictures from the run folder or the app's own folder.",
  },
  {
    covers: ["A13", "A14", "A15"],
    title: "Start over, New conversation, Work on the documents I set aside",
    you: "Start over from the originals; New conversation; Work on the documents I set aside.",
    runs:
      "Start over makes your originals the working set again; the plan is void. New conversation archives this " +
      "conversation in the conversations folder and starts an empty one. Working on the set-aside documents makes " +
      "them the only documents in scope and repeats their request.",
    sent: "Nothing, until you send the next request.",
    ai: NONE,
    bounds: "No copy and no original is deleted or changed.",
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
      `app's logs and event journals, settings.json, the current and ${F.bundleRecentConversations} most recent ` +
      `conversations, and the text records and logs of the ${F.bundleRecentRuns} newest runs.`,
    sent: "Nothing. The app never uploads a log or a bundle; sending it is yours to decide.",
    ai: NONE,
    bounds:
      "Folders of copies, samples and page pictures are left out, but the conversations' saved histories can hold " +
      "page images and document text: treat a bundle like your documents.",
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
