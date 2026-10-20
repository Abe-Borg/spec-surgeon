# The conversational desktop

Spec Surgeon is one window. On the left are the Word documents you attached and, optionally, one reference
template. On the right are a conversation and one button. You add documents, describe the result, read the
plan, look at the before/after pages and create edited copies. Nothing else is asked of you: no operations,
recipes, methods or settings beyond an API key.

![The conversation starts with Word documents, an optional template, and a request](conversation-start.png)

![A readable plan and rendered before-and-after pages, followed by one action to create copies](conversation-preview.png)

## Attachments and triage notes

Drop `.docx` files anywhere in the window or use **Add Word documents**. Dropping a file attaches that file
only; it never selects the other documents in its folder, and tables or pictures beside the documents are not
listed. Files from different folders can be used together, including files with the same name. Each file has
a remove button. **Add a template (optional)** attaches one reference document; it is never part of the batch,
and if one of the documents is a copy of the template the window says so and asks you to remove one of them.

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

After a result, a new message revises the whole result against the originals; earlier requested changes are
kept unless you reverse them.

## The plan in prose

When the plan is ready, the card **Here's what will change** lists one sentence per change. Each sentence is
written by the operation that will make the change, from the same specification the preview ran: units are
inches and points, exact text is quoted, and selections read as "paragraphs in the Normal style". Assumptions
the plan rests on appear beneath the changes as "Assuming: …". **See every setting** opens every setting of
every change for the reader who wants it all. **Where exactly** lists the passages each document contributes,
such as the value a template field takes from each file.

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

## One action

Every path ends in the same button. Its label says what it does:

- **Create edited copies**: approve the displayed plan and preview. The engine applies the specification to
  every document, verifies each one independently, and delivers only the files that pass every check.
- **Continue**: the documents or the plan changed, or a saved conversation was reopened; the documents are
  checked against the request again before anything can be approved.
- **Save N ready copies**: during recovery, save the copies that are already verified while a question about
  other files is still open.
- **Allow another estimated $10**: the conversation reached its allowance (see below).
- **Stop after current step**, while work runs.

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
still need work…"). Recovery works on copies, within the same allowance, and ends where everything else ends:
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

Each conversation has one allowance that covers every model call it makes: the conversation itself, custom
methods, the visual check and recovery. It starts at an estimated $10, 48 calls, 64,000 output tokens per call
and one million tokens in total. When it is reached, work pauses with its progress saved and the button reads
**Allow another estimated $10**; accepting extends the same ledger by $10, 48 calls and one million tokens. The
amounts are estimates from a built-in price table, not provider billing caps. Usage that could not be
confirmed is counted conservatively.

While work runs the button reads **Stop after current step**. Stopping takes effect before the next model
call; a response already in flight or a file operation already running finishes. The status line shows the
current step, the elapsed seconds and the estimated spend so far.

## Saved conversations

The window writes `conversation.json` in its configuration folder (`%APPDATA%\Spec Surgeon` on Windows, or
`SPEC_SURGEON_CONFIG_DIR` when set): the attachments with their triage state, the messages, your decisions and
approved wording, the result locations, the spending ledger and the location of a recovery checkpoint. It can
contain document text. It never contains the API key, an approval to deliver, or an acknowledgement of a
visual finding; those belong to one displayed preview only.

On reopening, the conversation and the result links come back. Every attachment is triaged again, since a file
may have changed, moved or been fixed; a file that is gone is marked with where it used to be. The button
reads **Continue**: the documents are checked against the request again before any new approval, and a
recovery checkpoint is reloaded and revalidated. If the app stopped in the middle of work before its usage was
recorded, continuing needs a new explicit allowance. **New conversation** archives the current journal under
`conversations\` and starts empty. An unsent draft is kept in `settings.json`.

## Capability and verification limits

The window does not make arbitrary Word requests verifiable. The supported operations, their composition
limits, the template rules and the custom-method limits still apply; a request outside them is explained or
clarified, not approximated. Draft wording the conversation proposes is applied only when you pick it, and
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
responses, the real engine and verifier, and offscreen Qt. It does not establish live-model success rates or
acceptance of rendered Word layouts; those remain the owner's to run on real files.
