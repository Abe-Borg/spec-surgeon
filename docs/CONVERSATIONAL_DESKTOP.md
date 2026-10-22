# The conversational desktop

Spec Surgeon is one window: a web page shown in a native window (Microsoft's WebView2 control through pywebview;
the default browser when pywebview is not installed) and served by a local server on your computer — nothing
leaves the machine but the model calls you ask for (the contract between the page and the server is
`docs/WEB_API.md`). On the left is the conversation, with the one button above the box you type in. On the right
are the Word documents you attached (a section that folds to one line once a plan exists; click its title to open
it), optionally one reference template, any pictures or tables you attached for the edit to use, and the before/after
pages. You add documents, describe the result, look at the pages, mark each preview with a green checkmark or a red
X, and create edited copies. Nothing else is asked of you: no operations, methods or settings beyond an API key.
Both panes render what the assistant writes as formatted text (lists, bold, links), in the chat and on the cards
beside it alike.

![The conversation starts with Word documents, an optional template, and a request](conversation-start.png)

![The plan in the conversation and rendered before-and-after pages, followed by one action to create copies](conversation-preview.png)

## The top bar

The top bar follows Build-a-Spec's. Beside the title, a quiet line says what the conversation works on ("3
documents · 1 template · 1 picture · building on your copies"; files triage refused are not counted). It shows from
1280 px wide and shortens with an ellipsis before anything else gives way. Then the in-app guide, one dialog with a
tab strip:

- **How to use** — a first pass in eight steps, what a refused file means, and how spending, Stop now and the
  pause work.
- **Workflows** — recipes for the usual jobs: fonts across a set, headers and footers from a template, a logo, values
  from a sheet, an issue stamp, a mixed batch, one change after another, a file left untouched, stopped or paused work.
- **How it works** — the mechanics in plain words.
- **About** — the version, the model, the release notes, the license and the open-source components, the author's
  links, **Check for updates** (it says "Checking for updates…" until the check answers, then the answer or the update
  with **Download and install**), **Open diagnostic logs** and **Save a diagnostic bundle** (below, "When the window
  has a problem"). These replace the old Help menu and About box.

On the right: the estimated spending of the conversation, the connection pill (it opens Settings),
**New conversation** and the gear. An update set aside with the banner's **Not now** stays in the top bar as a pill
("v1.7.0 available — install") from 1280 px wide; below that, About offers it.

## Attachments and triage

Drop `.docx` files anywhere in the window or use **Add Word documents**. Dropping a file attaches that file
only; it never selects the other documents in its folder, and tables or pictures beside the documents are not
listed. Files from different folders can be used together, including files with the same name. Each file has
a remove button. **Add a template (optional)** attaches one reference document; it is never part of the batch,
and if one of the documents is a copy of the template the window says so and asks you to remove one of them.

**Add a picture or table (optional)**, or dropping one, attaches a file for the edit to use: a picture
(`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`), for example a new logo to put in place of the old one or a detail to
insert, or a table (`.xlsx`, `.csv`, `.json`), for example a sheet of project names and numbers to fill in, or a
schedule to place in the documents as a table. It is listed under the
template by its name, tagged Picture or Table; one that
cannot be read or is larger than 20 MB is refused with the reason. Claude is told about each one (a picture's
format and size, a table's sheets and columns) and can read the tables, but nothing beside your documents is
ever looked for. A plan that uses one names it, and
the approval covers its exact content: changing the file after the preview means the plan must be checked again.
Values a table gives are yours, not Claude's words.

The moment a file is attached, Spec Surgeon reads it. This takes milliseconds and makes no model call. The
list shows each file by its name only (with a Template, Picture or Table tag, and To do or Working on while the
to-do list is in use):

- Accepted. What the check found stays out of the list: editing restricted in Word (edited copies keep the
  restriction); saved in an older compatibility mode; no paragraph styles in use, so changes pick paragraphs by
  appearance and text; numbers typed by hand, not automatic list numbering; text split into many small runs;
  text boxes in the headers or footers; pending tracked changes; comments; irregular spacing (non-breaking or
  doubled spaces); nothing in the body yet (an empty document is taken: a request can add to it, or change its
  headers, footers and page setup). The server still reports these as the attachment's `notes`; the window does
  not show them.
- Refused, greyed out with the reason: the file cannot be opened as a Word package, is a Strict Open XML
  document, is larger than the app opens, has a part with an XML document type declaration (`<!DOCTYPE …>`, which
  Word never writes; the reason names the part), or has no paragraph at all in its body. The reason says what to do,
  usually to open the file in Word and save it again as `.docx`. A refused file never enters a plan.

A `.docm`, `.doc` or `~$` lock file is not attached; a notice asks for a `.docx`. A template that would be
refused is not attached either.

The planner receives the same facts for each structural layout in the batch (styles, numbering, spacing,
fragmentation, and how that layout marks its section title, PART headings and article titles), so it plans with
them instead of discovering them. The check's findings describe the file, not the edit: a file with any of them
is edited like any other, and a file with none can still be set aside at plan time.

Adding or removing a document after a plan exists voids that plan. The conversation says so, and the button
reads **Continue** until the documents have been checked against your request again.

## The conversation

Type what you want in the box at the bottom and press **Send** (Ctrl+Enter). Spec Surgeon inspects the
documents and the template before asking anything their contents can answer. What it writes in the chat is a
message to you in plain words; operation and selector names never appear there.

When something needs your decision, a card titled **One thing to clarify** shows the question and its answers
as buttons. Click one, type its number, or answer in your own words. A choice that would put words into the
documents shows those exact words beneath the button; picking it approves exactly those words and nothing
else. Questions are asked once, and per layout, never per file. For an explicit request to draft or rewrite
wording, the conversation may offer exact draft wording as a choice; it is applied only if you pick it. A reply
that asks nothing (an answer, an apology, a list of findings) stays in the chat alone; the card never repeats it.

The assistant goes ahead on sensible defaults and says what it assumed; it asks at most three questions per
turn, each with the answer it recommends, and offers short replies you can tap. A tapped reply is your decision
and is sent as your reply, but it is never approved wording: words that go into a document are always ones you
typed or picked. Changes that touch the same parts of a file — a font change beside a header/footer template,
restyling beside direct formatting — are composed into ordered steps by the engine itself, each step planned and
verified on the step before it; nothing is asked of you in two steps.

**The next request builds on the result.** After copies are created, the next thing you ask for is made on those
copies; your originals stay as they are, and the assistant is told what was already done so it plans only what is
new. **Start over from the originals** (a reply you can tap, or type) returns to your files. A document attached
after a run is an original beside the copies: the window says so, and it gets only the next change.

While work runs you see it: the assistant's words as they are written, flowing in at a reading pace; above them
one line naming what it is doing ("Reading your documents…", "Writing the plan…"), which folds into a summary of
its steps, the words it said along the way and its thinking once the reply is written; the pages as they render
and the copies as they are written. **Stop now** keeps what landed.

## The plan

When the plan is ready, the assistant says in the chat, in plain words, what will change and where, and the
window shows the rendered before/after pages of sample documents. The window has no separate card listing the
changes: the pages are what you review. A plan the engine composed into ordered steps is still one plan with one
button.

JSON, approval hashes, operation catalogs and recovery internals are not part of the window.

## Layouts for a mixed batch

Documents from different projects are built differently: article titles in a PR1 style in one, Heading 2 in
another, Normal paragraphs with typed numbers in a third. One selector cannot say "the article titles" for all
of them. When one specification cannot express the request for every structure pattern in the batch, the plan
has one specification per layout. Each is validated and dry-planned on that layout's documents only; every
delivered file still has exactly one specification, and the independent verifier is unchanged.

The preview covers at least one sample per layout, and one button approves all of them. A document whose structure no layout covers is listed under **Left untouched** with a
sentence: attach it on its own, or ask for a plan that covers it.

## Previews and page counts

Each sample document gets a card **Preview · name** with rendered before/after pages; click a page to enlarge
it. The card shows the page count before and after and flags growth ("Pages: 12 → 13 (+1)"). Samples are
applied and verified in memory; sources are never touched. Rendering is requested automatically and uses
Microsoft Word when available, otherwise LibreOffice. If no renderer can provide usable page evidence, a card
says that page appearance was not checked and asks you to check the edited copies in Word.

The pages are those of the plan in front of you, and nothing older. Sending a message or picking a choice takes
the plan and its pages off the window while the reply is worked out, and a new plan's pages replace them; once
copies are created, their pages leave with the plan. During recovery, a document whose copy you already saved
drops out of the previews, and its findings out of **About these checks**, while the conversation goes on about
the rest.

## The green checkmark, the red X and the to-do list

Every preview card carries two marks. The green checkmark says the pages look right. The red X says this
document needs more work: it is **set aside**. When you create the edited copies, the documents you set aside
get no copy; they go on a to-do list, and the button says so ("Create edited copies (2 set aside)"). Every
other document is created and verified as usual, each one still held to the exact file the preview was made
from; a document that is not part of the displayed plan can never be set aside or run.

Once the other copies are saved, the conversation shows **Set aside for more work** with the documents'
names and one button, **Work on them now** (the same as typing “Work on the documents I set aside”). The pass
over them begins: they are the only documents in the plan, tagged **Working on** in the documents section; the
request they were set aside from stands; the assistant is told that you looked at the preview of that plan and
set these documents aside, and is shown the plan you saw. Tell it what should be different for them, or
**Continue** to plan them again as before. The pass ends when their copies are created; the other copies stay
as they are and the whole set is the working set again. A document you set aside once more goes back on the
list. Documents set aside from different requests are worked on one request at a time, the oldest first; the
others wait and are offered again. The assistant always hears what each copy carries: a change that some copies
got and others did not is named per document, never claimed for all. Documents on the to-do list are tagged **To do** and are out of every plan until you work on them;
removing one drops it from the list, and **Start over from the originals** clears the list. Setting every
document aside creates nothing and begins the pass at once. The list and the pass are kept in the saved
conversation.

Claude's visual check of the rendered pages is advisory. Its findings appear under **About these checks** with
their file and page. A finding it marks as blocking changes that card's title to **Please check** and the
button to **I reviewed the flagged pages — create copies**; that is a separate, explicit decision on the
displayed preview.

Rendering. Word renders a whole batch in one private, invisible instance (never your open Word), with a
deadline per file: a file Word gets stuck on is reported as not rendered and the rest carry on in a fresh
instance. LibreOffice keeps its user profiles under the app's config folder (`render-profiles`) instead of
creating one per run. Rendered pages are kept in a cache beside the session folders (`<output root>/.render-cache`,
at most 2 GB, oldest first out), keyed by the document's exact bytes and the renderer's version: the run does not
render again what the preview rendered, and a source you attach again unchanged is not rendered again either — a
file whose content differs in any way is always rendered afresh. The preview and run logs say how many pages came
from the cache. `SPEC_SURGEON_RENDER_CACHE=0` turns the cache off (see `docs/RENDER_PIPELINE.md`).

## One action

Every path ends in the same button, in the conversation pane above the box you type in, with one sentence on
what it does. Its label says what it does:

- **Create edited copies**: approve the displayed plan and preview. The engine applies the specification to
  every document you did not set aside with the red X, verifies each one independently, and delivers only the
  files that pass every check. "(N set aside)" on the label counts the documents that go on the to-do list.
- **Continue**: the documents or the plan changed; the documents are checked against the request again before
  anything can be approved.
- **Save N ready copies**: during recovery, save the copies that are already verified while a question about
  other files is still open.
- **Continue** after a pause: the conversation has used 48 AI steps since you last said to go on, or the app
  closed while work was running (see below).
- **Stop now**, while work runs: the kill switch, in the composer (see below).

The button is tied to what is on screen. If a document, the template, the plan, a preview or a visual finding
changes, the displayed action is void and the window says "The documents or plan changed. Review the updated
plan first." Typing a new message disables the button until the message is sent. The marks on the previews
belong to the displayed plan too: a new plan or preview starts the review over.

## What is left untouched, and why

Originals are never written; the window always creates copies. Each run's copies go into a timestamped folder
under the app's local data (`%LOCALAPPDATA%\Spec Surgeon\runs` on Windows), so earlier copies remain. **Your
edited copies** lists them; click one to open it in Word.

A file can be left out at three points, each time with one sentence that names the file and the reason:

- At attach time, when triage refuses it (above).
- At plan time, when the operation cannot predict its result on that file, or when no layout covers its
  structure. These appear under **Left untouched** before anything runs; no paid investigation precedes them.
- At delivery, when a file fails a verification check. It is never delivered. Spec Surgeon then checks it
  again internally (below).

## Recovery and custom methods run internally

After **Create edited copies**, a file that did not pass goes straight into recovery ("Checking the files that
still need work…"). Recovery works on copies, within the same conversation, and ends where everything else ends:
a question in the chat (for example "Which passage(s) in Spec.docx should change? Reply with the number, or
numbers separated by commas.") or the same button. While a question is open, the copies that are already
verified can be saved. The independent checks and the source-change guards are the same as for a first run.

With a plan per layout, recovery takes one layout at a time, each against that layout's own specification: the
first layout whose documents did not pass is investigated at once, the others wait and are checked by themselves
after the first layout's copies are saved (the queue is kept in the saved conversation). A document that no layout
covered has no specification to recover against; it stays as it was and is named under **Left untouched**.

When no built-in operation fits a request, Spec Surgeon may prepare a custom method internally, prove it on
the sample documents, and show its result as a preview like any other. You never choose a method; you see what
will change and decide once. Custom methods write edited copies only and run under the same verification.

Normalizing a file (typed numbers to automatic numbering, clearing direct formatting, whitespace, pending
tracked changes) is never automatic. It is only ever offered to you as an explicit first stage and runs only if
you accept it.

## Connection and resource use

Typed requests need an Anthropic API key. **Settings** opens one dialog. **AI connection**: paste the key and
tick **Remember securely on this computer** to keep it in Windows Credential Manager. **Appearance**: the window's
colour scheme, one of Warm Clay (the default), Blueprint, Graphite & Teal, Carbon & Hi-Vis and Linen (the light
one); a click recolours the window at once and `settings.json` keeps it, so the next start opens in it. Nothing
else is configurable there. Without a key the composer keeps your draft, **Send** stays disabled and the footer
says "Connect your Anthropic API key in Settings to start."

There is no spending limit. Every AI step (one call to Claude Opus 5.5, whether for the conversation, a
custom method, the visual check or recovery) may write up to 128,000 tokens, the most the model writes. The
conversation runs at the model's default reasoning effort (medium: its sentences and plans are short and the
operations' settings ride the cached prompt, so a plan is one or two calls); recovery (the outcome planning after a
preview that set files aside included) and custom methods think at high. No step runs at max: `resources.EFFORTS`
refuses it (the owner's decision, 2026-10-22). The instructions and the operations' settings (the same for every
conversation) are cached for an hour, so the next request after a run, a start over or a changed attachment reads them instead of paying for them again; the
conversation itself is cached for five minutes between its calls, and the one-call checks (the visual check, the
recovery critic) are not cached at all beyond the instructions. Three things keep the spending in your hands instead:

- **You see it.** While work runs the chat shows the assistant's words and its thinking summary as they are
  written, a sentence per step, and the top bar the estimated spending so far. A step that is still running is
  counted when it ends. The amounts are estimates from a built-in price table, not provider bills.
- **Stop now.** While work runs the button is the kill switch. It cuts off the AI step that is running at
  once, so Claude stops writing, and starts no other; a document check or file operation already running
  finishes first, since it costs nothing. A step that was cut off is counted from the input it reported and at
  most what it could have written in the time it ran. Your progress is saved, and **Continue** picks the
  conversation up where it stopped.
- **A pause every 48 AI steps.** Work pauses with its progress saved and the button reads **Continue**;
  continuing allows 48 more and the conversation goes on where it stopped, with everything it had found.

A call that fails for a passing reason (the service is overloaded or rate-limited, a server error, a dropped
connection or a response cut off in transit) is sent again automatically, up to twice, after 5 and then 20
seconds (longer when the service asks for it). Every attempt counts as an AI step. A request the service
refuses is never sent again. When Claude declines to answer a reply (Anthropic's checks flag it, for instance as
asking Claude to reveal its own reasoning), the chat says so in plain words and the reply is taken back out of the
conversation, its wording no longer approved, so it does not go out again with the next message; rephrase it and
send it again.

## Conversations and a clean start

The window writes `conversation.json` in its configuration folder (`%APPDATA%\Spec Surgeon` on Windows, or
`SPEC_SURGEON_CONFIG_DIR` when set) as the conversation goes: the attachments with their triage state, the
messages, your decisions and approved wording, the result locations, the working set (which copy stands for each
original, and the changes already made to it), the to-do list and the pass over it, the spending ledger and the
location of a recovery checkpoint. It can contain document text. It never contains the API key, an approval to
deliver, or an acknowledgement of a visual finding; those belong to one displayed preview only.

Every start of the app is a clean one: an empty conversation, nothing attached. The last conversation's journal
is moved under `conversations\` (named by the time it was set aside) and never reopened; the edited copies stay
in their run folders. **New conversation** does the same mid-session. A journal that cannot be moved (another
program holds the file open) is left exactly as it is, and that session keeps no journal of its own rather than
write over it. After a crash the first message says the
previous session ended unexpectedly, where Python's fault handler wrote what it saw, and where its conversation
was archived.

## When the window has a problem

The window's page runs in WebView2, where nobody sees its console, so the page reports on itself to the app
(`frontend/src/lib/diagnostics.ts` → `POST /api/diagnostics` → `web/watch.py`) and the app writes it down in the logs
folder (`%LOCALAPPDATA%\Spec Surgeon\logs`): `spec-surgeon.log` for a person, `app-events-<pid>.jsonl` for a query.

- **What it records.** Every request the page makes (route, status, time; never a body), each job as it starts and
  ends, Stop, the window's open and close, the page's loads and reloads (with the WebView2 version), every error in
  the page that nothing caught, a job stream the page lost before its end, and a page blocked for 2 s or more (its
  long tasks, sent with a heartbeat every 10 s).
- **A frozen window.** A showing page that sends nothing for 45 s is written down as frozen, with where every thread
  of the app was at that moment, and again when it answers. A hidden or minimised window is never called frozen, and
  a computer waking from sleep restarts the clocks.
- **A stuck job.** A job that shows no progress for 5 minutes (no words, no step, no log line) gets every thread's
  stack in the log, again every 15 minutes it stays quiet; so does a job still running 30 s after Stop, and one that
  did not stop within a minute of the window closing.
- **A window that fails to draw.** Instead of a blank window, a screen says the window ran into a problem, with
  **Reload the window**, **Save a diagnostic bundle** and **Open diagnostic logs**. The error and the parts of the
  window it happened in go to the log. The app behind the page keeps running: a reload finds the conversation as it
  was, and a job in progress carries on (the page follows it until it ends).
- **A session that never closed.** The next start writes, in `spec-surgeon.log`, what that session was doing last
  (the last events of its journal) and whether its crash log caught a native crash; an empty crash log means the
  process was ended from outside (Task Manager, signing out, a power cut) or stopped answering and was closed.
- **Save a diagnostic bundle** (About, or the error screen) writes `spec-surgeon-diagnostics-<time>.zip` under
  `%LOCALAPPDATA%\Spec Surgeon\diagnostics` and shows it in Explorer: the logs folder, `settings.json`, the current
  and last three conversations, and from the five newest run folders their logs, records, reports and conversation
  records. `MANIFEST.txt` lists what is in it and what was left out. Documents, pictures, page images and the folders
  that hold them (`outputs`, `failed`, `samples` and the like) never go in; conversations and records can quote your
  words and your documents' text.

## Capability and verification limits

The window does not make arbitrary Word requests verifiable. The supported operations, the template rules and
the custom-method limits still apply; a request outside them is explained or clarified, not approximated.
Operations that touch the same part of a file are never ordered by guess inside one specification: the engine
cuts them into ordered steps in the order they were listed, and a step that still cannot be planned sets the
file aside with the reason. Draft wording the conversation proposes is applied only when you pick it, and
picking it never authorizes invented project facts, technical requirements or citations.

The paragraph roles the planner reads (what an article title or a PART heading looks like in each layout) are
evidence for writing concrete selectors with required counts. They are never the authority for an edit: the
verifier re-resolves the concrete selectors on its own, and the role classifier plays no part in that check.

A PASS means the engine found exactly the predicted change and nothing else, not that the result is what you
wanted. Automated page comparison and Claude's visual check are not human acceptance in Word; look at the
pages, and open the copies.

The window has no other mode, and there is no command line: it was removed on 2026-10-21 together with the
features only it offered (the recipe library, office profiles, specification QA audits, project evidence, built-in
workflows, campaign and recovery workspaces, evaluations).

Validation uses generated Word fixtures (including a family of deliberately dirty files), scripted model
responses, the real engine and verifier, and the window's server driven in-process (FastAPI's test client, no
browser); the page's own rules are tested in `frontend/` with Node. It does not establish live-model success
rates or acceptance of rendered Word layouts; those remain the owner's to run on real files.
