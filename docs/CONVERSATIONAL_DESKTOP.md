# The conversational desktop

Spec Surgeon is one window: a web page shown in a native window (Microsoft's WebView2 control through pywebview;
the default browser when pywebview is not installed) and served by a local server on your computer — nothing
leaves the machine but the model calls you ask for (the contract between the page and the server is
`docs/WEB_API.md`). On the left are the Word documents you attached, optionally one reference template, and any
pictures or tables you attached for the edit to use. On the right are a conversation and one button. You add
documents, describe the result, read the plan, look at the before/after pages and create edited copies. Nothing
else is asked of you: no operations, recipes, methods or settings beyond an API key.

![The conversation starts with Word documents, an optional template, and a request](conversation-start.png)

![A readable plan and rendered before-and-after pages, followed by one action to create copies](conversation-preview.png)

## Attachments and triage notes

Drop `.docx` files anywhere in the window or use **Add Word documents**. Dropping a file attaches that file
only; it never selects the other documents in its folder, and tables or pictures beside the documents are not
listed. Files from different folders can be used together, including files with the same name. Each file has
a remove button. **Add a template (optional)** attaches one reference document; it is never part of the batch,
and if one of the documents is a copy of the template the window says so and asks you to remove one of them.

**Add a picture or table (optional)**, or dropping one, attaches a file for the edit to use: a picture
(`.png`, `.jpg`, `.jpeg`, `.gif`), for example a new logo to put in place of the old one, or a table
(`.xlsx`, `.csv`, `.json`), for example a sheet of project names and numbers to fill in. It is listed under the
template with one line saying what it is ("A PNG picture, 600 × 200 pixels, for the edit to use."); one that
cannot be read or is larger than 20 MB is refused with the reason. Claude is told about each one (a picture's
format and size, a table's sheets and columns) and can read the tables, but nothing beside your documents is
ever looked for. A plan that uses one names it, and
the approval covers its exact content: changing the file after the preview means the plan must be checked again.
Values a table gives are yours, not Claude's words.

The moment a file is attached, Spec Surgeon reads it. This takes milliseconds and makes no model call. Under
the file name it shows one of three states:

- Nothing. The file is fine.
- A note, one plain sentence each, in this order when several apply: editing is restricted in Word (edited
  copies keep the restriction); saved in an older compatibility mode; no paragraph styles in use, so changes
  pick paragraphs by appearance and text; numbers typed by hand, not automatic list numbering; text split into
  many small runs; text boxes in the headers or footers; how many tracked changes are pending; the file has
  comments; irregular spacing (non-breaking or doubled spaces).
- Refused, greyed out with the reason: the file cannot be opened as a Word package, is a Strict Open XML
  document, is larger than the app opens, or has no text in its body. The reason says what to do, usually to
  open the file in Word and save it again as `.docx`. A refused file never enters a plan.

A `.docm`, `.doc` or `~$` lock file is not attached; a notice asks for a `.docx`. A template that would be
refused is not attached either.

The planner receives the same facts for each structural layout in the batch (styles, numbering, spacing,
fragmentation, and how that layout marks its section title, PART headings and article titles), so it plans with
them instead of discovering them. A note describes the file, not the edit: a file with a note is edited like
any other, and a file without one can still be set aside at plan time.

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
wording, the conversation may offer exact draft wording as a choice; it is applied only if you pick it.

The assistant goes ahead on sensible defaults and says what it assumed; it asks at most three questions per
turn, each with the answer it recommends, and offers short replies you can tap. A tapped reply is your decision
and is sent as your reply, but it is never approved wording: words that go into a document are always ones you
typed or picked. Changes that touch the same parts of a file — a font change beside a header/footer template,
restyling beside direct formatting — are composed into ordered steps by the engine itself, each step planned and
verified on the step before it; the plan says "in N ordered steps" and nothing is asked of you in two steps.

**The next request builds on the result.** After copies are created, the next thing you ask for is made on those
copies; your originals stay as they are, and the assistant is told what was already done so it plans only what is
new. **Start over from the originals** (a reply you can tap, or type) returns to your files. A document attached
after a run is an original beside the copies: the window says so, and it gets only the next change.

While work runs you see it: the assistant's words as they are written, a summary of its thinking, one plain
sentence per thing it is doing ("Reading your documents…", "Writing the plan…"), the plan the moment it is
ready, the pages as they render and the copies as they are written. **Stop now** keeps what landed.

## The plan in prose

When the plan is ready, the card **Here's what will change** lists one sentence per change. Each sentence is
written by the operation that will make the change, from the same specification the preview ran: units are
inches and points, exact text is quoted, and selections read as "paragraphs in the Normal style". Assumptions
the plan rests on appear beneath the changes as "Assuming: …". **Every setting** opens every setting of every
change for the reader who wants it all (a long pattern is not spelled out in the sentence; it is here). **Where
exactly** lists the passages each document contributes, such as the value a template field takes from each file.
A plan the engine composed into ordered steps says so under its title ("in 2 ordered steps"); it is still one plan
with one button.

JSON, approval hashes, operation catalogs, recipes and recovery workspaces are not part of the window.

## Layouts for a mixed batch

Documents from different projects are built differently: article titles in a PR1 style in one, Heading 2 in
another, Normal paragraphs with typed numbers in a third. One selector cannot say "the article titles" for all
of them. When one specification cannot express the request for every structure pattern in the batch, the plan
has one specification per layout. Each is validated and dry-planned on that layout's documents only; every
delivered file still has exactly one specification, and the independent verifier is unchanged.

The window then shows **Your documents use N layouts** and one card per layout, titled with how you would
recognise those documents and how many there are ("Styled, PR1 articles · 8 documents"), each with its own
changes, assumptions and the names of its files. The preview covers at least one sample per layout. One button
approves all of them. A document whose structure no layout covers is listed under **Left untouched** with a
sentence: attach it on its own, or ask for a plan that covers it.

## Previews and page counts

Each sample document gets a card **Preview · name** with rendered before/after pages; click a page to enlarge
it. The card shows the page count before and after and flags growth ("Pages: 12 → 13 (+1)"). Samples are
applied and verified in memory; sources are never touched. Rendering is requested automatically and uses
Microsoft Word when available, otherwise LibreOffice. If no renderer can provide usable page evidence, a card
says that page appearance was not checked and asks you to check the edited copies in Word.

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

Every path ends in the same button. Its label says what it does:

- **Create edited copies**: approve the displayed plan and preview. The engine applies the specification to
  every document, verifies each one independently, and delivers only the files that pass every check.
- **Continue**: the documents or the plan changed, or a saved conversation was reopened; the documents are
  checked against the request again before anything can be approved.
- **Save N ready copies**: during recovery, save the copies that are already verified while a question about
  other files is still open.
- **Continue** after a pause: the conversation has used 48 AI steps since you last said to go on, or the app
  closed while work was running (see below).
- **Stop now**, while work runs: the kill switch (see below).

The button is tied to what is on screen. If a document, the template, the plan, a preview or a visual finding
changes, the displayed action is void and the window says "The documents or plan changed. Review the updated
plan first." Typing a new message disables the button until the message is sent.

## What is left untouched, and why

Originals are never written; the window always creates copies. Each run's copies go into a timestamped folder
under the app's local data (`%LOCALAPPDATA%\Spec Surgeon\runs` on Windows), so earlier copies remain. **Your
edited copies** lists them; double-click one to open it in Word.

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

Typed requests need an Anthropic API key. **Settings** opens one dialog, **AI connection**: paste the key and
tick **Remember securely on this computer** to keep it in Windows Credential Manager. Nothing else is
configurable there. Without a key the composer keeps your draft, **Send** stays disabled and the footer says
"Connect your Anthropic API key in Settings to start." The command line runs saved specifications without a key.

There is no spending limit. Every AI step (one call to Claude Opus 5.5, whether for the conversation, a
custom method, the visual check or recovery) may write up to 128,000 tokens, the most the model writes. The
conversation runs at the model's default reasoning effort (medium: its sentences and plans are short and the
operations' settings ride the cached prompt, so a plan is one or two calls); recovery and custom methods think at
high. The instructions and the operations' settings (the same for every conversation) are cached for an hour, so the
next request after a run, a start over or a changed attachment reads them instead of paying for them again; the
conversation itself is cached for five minutes between its calls, and the one-call checks (the visual check, the
recovery critic) are not cached at all beyond the instructions. Three things keep the spending in your hands instead:

- **You see it.** While work runs the chat shows the assistant's words and its thinking summary as they are
  written, a sentence per step, and the header the estimated spending so far. A step that is still running is
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
refuses is never sent again.

## Saved conversations

The window writes `conversation.json` in its configuration folder (`%APPDATA%\Spec Surgeon` on Windows, or
`SPEC_SURGEON_CONFIG_DIR` when set): the attachments with their triage state, the messages, your decisions and
approved wording, the result locations, the working set (which copy stands for each original, and the changes
already made to it), the spending ledger and the location of a recovery checkpoint. It can contain document
text. It never contains the API key, an approval to deliver, or an acknowledgement of a visual finding; those
belong to one displayed preview only. A reopened conversation whose copies can no longer be found says so and
starts the next change from your originals.

On reopening, the conversation and the result links come back. Every attachment is triaged again, since a file
may have changed, moved or been fixed; a file that is gone is marked with where it used to be. The button
reads **Continue**: the documents are checked against the request again before any new approval, and a
recovery checkpoint is reloaded and revalidated. If the app closed in the middle of work, before the last
step's usage was recorded, the conversation reopens paused and continuing needs your **Continue**. **New
conversation** archives the current journal under
`conversations\` and starts empty. An unsent draft is kept in `settings.json`.

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

The window has no other mode. The command line is unchanged; office profiles, specification QA audits, project
evidence, built-in workflows, campaign workspaces and recovery workspaces are command-line features.

Validation uses generated Word fixtures (including a family of deliberately dirty files), scripted model
responses, the real engine and verifier, and the window's server driven in-process (FastAPI's test client, no
browser); the page's own rules are tested in `frontend/` with Node. It does not establish live-model success
rates or acceptance of rendered Word layouts; those remain the owner's to run on real files.
