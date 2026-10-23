# Spec Surgeon

Surgical edits to Word (.docx) specification files, a few files or dozens at a time, with an independent check and a
verification record for every file: from one word or one property up to whole articles, tables, pictures and
sections.

Spec Surgeon is for people who maintain construction specifications: the engineer or designer who has to change the
font, the header and footer, the issue stamp, the numbering or a defined term across a whole set of sections, and who
cannot afford a single file in which a paragraph went missing or a word appeared that nobody wrote. The transform is
deterministic Python over the document XML. The expected result is predicted *before* the transform runs. A separate
verifier, sharing no code with the transform, reads only the before and after packages and asserts that prediction.
A file is delivered only when every check passes and the source has not moved since it was read. No language model
is in the per-file loop; Claude is optional, and its job is to turn a plain-English request into a specification you
approve, to look at preview pages, and to investigate files the engine set aside. Safe mode does the common changes
from buttons, with no AI at all.

It runs as a Windows desktop app and as a Python library (Windows, macOS or Linux). There is no command line.

## Install

**Windows 10/11 (64-bit):** download `SpecSurgeon-<version>-Setup-x64.exe` from
[GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases), run it, then launch **Spec Surgeon** from the
Start menu. The installer includes Python, the window (a local server and its page, shown in a native WebView2
window; the WebView2 runtime ships with Windows 10/11), the editing engine, Word integration and the Anthropic
client; no separate Python installation or terminal commands are needed. From then on the app keeps itself up to date:
a banner offers each new version, and **About → Check for updates** checks at any time. It installs for the current
user without administrator access, offers a desktop shortcut and includes an uninstaller. Example EditSpecs are
available from **Spec Surgeon → Example edit specs** in the Start menu.

Microsoft Word or LibreOffice is still needed for page rendering; neither is installed by this setup program. Safe
mode needs no API key. Default mode's typed requests need your Anthropic API key: enter it in the window's **Settings** and tick *Remember securely on this
computer* to keep it in Windows Credential Manager. The installer is unsigned, so Windows may show a publisher prompt on the first manual install.

**Python package / development setup** (Python 3.10+ on Windows, macOS or Linux; from the project folder):

```powershell
py -3 -m pip install -e .            # core: lxml, PyMuPDF 1.x (2.0 loads an AI layout model the app never uses)
py -3 -m pip install -e ".[gui]"     # the window: FastAPI, uvicorn, pywebview (WebView2 on Windows) — spec-surgeon-gui
py -3 -m pip install -e ".[word]"    # Windows: adds pywin32 so Word is the renderer/oracle (recommended)
py -3 -m pip install -e ".[agent]"   # optional: anthropic SDK for typed requests in the window
py -3 -m pip install -e ".[dev]"     # pytest, pytest-xdist (pytest -n auto: every core) + python-docx (fixtures)
```

or `py -3 -m pip install -r requirements.txt` for everything. Start the window with `spec-surgeon-gui` (or
`py -3 -m spec_surgeon`); a copy installed this way is not updated in-app, the banner shows the `pip install -U` command
instead. On Linux and macOS use `python3` in place of `py -3`. Without pywebview the window opens in the default
browser instead of its own window.

The window is a web page served by a local server on your computer (the contract is
[docs/WEB_API.md](docs/WEB_API.md)). The built page is committed under `src/spec_surgeon/web/static/`, so a
checkout runs as is; Node 22 is needed only to rebuild it after changing `frontend/` (`cd frontend`, then
`npm ci` and `npm run build`).

Rendering needs one of Microsoft Word (Windows, with `pywin32`) or LibreOffice (any platform; set
`SPEC_SURGEON_SOFFICE` if it is not on `PATH`). Without either, the engine still runs every XML check; the two
render checks are recorded as N/A with the reason.

| Variable | Purpose |
|---|---|
| `SPEC_SURGEON_AUTHOR` | default tracked-change author (else the OS user name) |
| `SPEC_SURGEON_SOFFICE` | path to `soffice` / `soffice.exe` |
| `SPEC_SURGEON_WORD_INSTANCES` | how many private Word instances may render a batch side by side (default 3, at most one per two cores; `1` renders every file in one instance) |
| `ANTHROPIC_API_KEY` | typed requests, Claude's visual check, custom transforms and recovery; or enter it in Settings |
| `SPEC_SURGEON_MODEL` | the model every Claude call uses (default `claude-opus-5-5`) |
| `SPEC_SURGEON_NO_UPDATE_CHECK` | any value: the window never checks for updates on its own |
| `SPEC_SURGEON_CONFIG_DIR` | where the app keeps `settings.json`, the current and archived conversations, downloaded updates and its logs |

## The window: documents and a conversation

![The conversation: Word documents, an optional template and a request](docs/conversation-start.png)

The window is one conversation. On the left is the chat, with the one button above the box you type in; on the right
are your documents (optionally with one template) and the before/after pages. The top bar, in the style of Build-a-Spec, carries the in-app guide —
**How to use**, **Workflows**, **How it works** and **About** (the version, the model, the release notes, the license,
**Check for updates**, **Open diagnostic logs** and **Save a diagnostic bundle**) — beside a line saying what the conversation works on ("3
documents · 1 template · building on your copies"), the estimated spending, the connection, **New conversation**
and Settings.

1. **Add Word documents.** Drop `.docx` files anywhere in the window, or use **Add Word documents**. Files can come
   from different folders, including files with the same name. Only the files you attach are edited; nothing is
   picked up from their folders.
2. **Check the list of files.** The moment a file is attached, Spec Surgeon reads it (milliseconds, no model
   call) and lists it by its name. What it finds (typed numbers instead of automatic list numbering, no paragraph
   styles, text boxes in the headers or footers, an older compatibility mode, editing restrictions, pending tracked
   changes, comments, irregular spacing, an empty body) is not shown in the list. A file that does not open
   as a Word package, has a part with an XML document type declaration (which Word never writes), or has no
   paragraph at all in its body, stays in the list greyed out with the reason and never enters a plan. A `.docm`,
   `.doc` or `~$` lock file is not attached; a notice asks for a `.docx`.
3. **Optionally add a template.** One reference `.docx` for requests such as "Match the headers and fonts to this
   template. Keep our technical wording."
4. **Describe the result.** Type what you want and press **Send** (Ctrl+Enter). Spec Surgeon inspects the documents,
   goes ahead on sensible defaults (and says what it assumed), asks at most three questions, each with the answer it
   recommends, and prepares a plan — while you watch its words flow in as they are written, with one line above them
   naming each step as it happens (the steps and its thinking summary fold under that line once the reply is written).
   Before it plans, it reads every document side by side (no model call): each one's section number and title, every
   header and footer line, and the File Properties title. It names the document that differs from most of the others
   (a footer title in other capitals, a header or footer line missing or extra, a Properties title naming another
   section) and offers a fix; nothing changes until you ask for it.
   A question comes as a card with buttons and short replies you can tap: click one, type its number, or answer in
   your own words. A choice that would put words into the documents shows those exact words; picking it approves
   exactly those words (a tapped reply never does). Changes that touch the same parts of a file — the template's
   header and footer beside its font, say — are composed into ordered steps by the engine; nothing is asked of you
   in two steps.
5. **Look at the pages.** The chat says in plain words what will change. Beside it: rendered before/after pages for
   sample documents with their page counts, and **Left untouched**: any file the plan cannot cover, with the reason.
   When a change is kept to one place and the same words remain elsewhere (the footer renamed while the first page
   keeps the old title, a header filled with a title the document does not have), a note beside the pages names the
   document and where; Spec Surgeon offers to change those too when you reply, and never does it unasked. A header or
   footer fill that will show in another case than the template's (a title typed "Identification for …" where the
   template shows "DOOR HARDWARE") is noted the same way; "show it in capitals like the template" makes it look so
   through formatting, without retyping a letter. Word has no title-case look, so when the template shows its title
   in title case ("Specification Name") and your files type theirs in capitals, matching it is wording: Spec Surgeon
   drafts each document's title in the template's case as one choice you can pick or correct, and one plan then
   puts each document's own approved title in its footer.
   A file the plan cannot cover is listed under **Left untouched** with the reason, and the preview of the others
   stands; the assistant names it in its next reply and says what would let it take part. In a plan done in steps
   (the template's headers and footers, then the fonts), a sample the later step refuses is listed there too.
   Each preview carries a green checkmark (looks good) and a red X (needs more work: set it aside for after the
   others). To leave documents out of a request altogether, say so ("leave out 20, and 23 for now"): the plan covers
   the others, the ones you named stay exactly as they are under **Left untouched**, and your next request covers
   them again unless you leave them out again.
6. **Create edited copies.** One button, in the conversation above the box you type in, approves the displayed plan.
   The engine applies it to every file you did not set aside, verifies each one independently, and lists the copies
   under **Your edited copies**; click one to open it in Word. Originals are never written. A file that fails a
   check is not delivered; Spec Surgeon checks it again internally and comes back with a question or a new plan,
   never a method to choose. The documents you set aside wait on a to-do list; once the other copies are saved, the
   conversation offers to work on them: they are planned alone, from the same request, and you say what should be
   different.

**Mixed batches.** Documents from different projects are often built differently: styled article titles in one,
plain paragraphs with typed numbers in another. For such a batch the plan has one specification per layout, and the
previews cover at least one sample of each. One button still approves all of them. A document built differently
from every layout the plan covers is left untouched with a sentence.

Keep chatting: the next thing you ask for is made on the copies the last run created, your originals stay as they
are, and **Start over from the originals** is one tap away. Each run's copies go into their own folder, so earlier
copies remain. Every start of the app is a clean one: the last conversation is archived on this computer (the
`conversations` folder beside `settings.json`) and never reopened, and the copies stay where they were saved.

![The plan in the conversation and before/after pages, with one action](docs/conversation-preview.png)

**Two modes, and a hidden third.** The switch is in the top bar, beside the help topics, and the window opens in the
mode you last used (Safe the first time; never Obliterate).

- **Safe — buttons, no AI.** What you control: the common changes, one button each — *Match headers and footers to
  template*, *Remove headers and footers*, *Add or remove a watermark*, *Swap the logo*, *Match body formatting to
  template*, *Match font to template*,
  *Find and replace* (typed pairs or an attached table; body, headers and footers), *Accept all tracked changes*,
  *Remove all comments*, *Remove highlighting*, *Remove hidden text*, *Remove empty paragraphs*, *Name files by
  section number and title*, *Set the document title property* and *Turn off Track Changes*. Each button has a short
  form (your words and choices only), then **Preview**, the same pages, checks and **Create edited copies** as Default
  mode. What it costs: only what the buttons do. The copies are clean unless you tick *Show my changes as tracked
  changes in Word* (offered where the change can be tracked). The template buttons take either a prepared template
  (yellow-highlighted or `{{NAME}}` placeholders: the section number and title come from each document, every other
  placeholder's value is typed in the form) or an ordinary specification whose headers and footers show its own
  section number and title. No model is called — no plan, no visual check, no recovery — so it needs no API key and
  spends nothing.
- **Match body formatting to template** (Safe mode) makes each document look like your template without touching a
  word or a table. The form shows the template's style for each part of a section (the section number line, the
  title, PART, articles, paragraphs and subparagraphs, END OF SECTION), read from the template and changeable. Each
  document's outline takes those styles and the template's numbering, and every number keeps its value: a "1.01"
  article shows as the template's "1.1", but a list that would renumber sets the document aside. An outline typed as
  text converts when its numbers are the ones the list counts (a typo, a skip or a duplicate sets it aside). With its
  boxes ticked (the default) it also sets the template's page size and margins (a document with a landscape page keeps
  its pages), its body font, and lets the styles set paragraph spacing, indents and alignment — bold, italic and
  underlined words stay. Documents built alike run as one plan and the rest are planned their own way; a document it
  cannot read (no outline, typed and Word numbers mixed, an outline deeper than the template's, one of its styles
  shared with paragraphs it does not restyle) is left untouched with the reason.
- **Default — a conversation.** What you control: any change you can describe, from one word to whole tables,
  sections and pictures, on documents built in different ways. What it costs: an Anthropic API key and the AI's
  spending, and the AI can misread a request, so you read what it plans and look at the pages before you approve.
- **Obliterate — the conversation without guardrails (hidden).** Off the switch until Settings → **Advanced** →
  *Show Obliterate mode* is on, and chosen only after its warning, every time (tick *I understand*, then **Turn on
  Obliterate**). What you control: Claude may write wording itself (it is in the plan you approve); when no operation
  fits, Claude may write a custom method that changes anything Word stores, not only formatting; and a copy that fails
  a check is delivered anyway, marked **UNVERIFIED** with the checks it failed, instead of being held back. What it
  costs: no proof that the rest of the file is untouched, content can be lost or invented without a refusal, a copy
  may need Word's repair, results can differ from run to run, and more AI spending. What still holds: copies only
  (your originals are never written), a source that changed is never delivered over, the custom method's sandbox, and
  the independent checks on every copy, their results kept beside it. **Back to Default** (or any other mode) leaves
  it; the app never opens in it.

On a batch from different projects, Safe mode's buttons work on every document they can change exactly and leave
the rest untouched, each with the engine's reason — a template's section line it cannot find, an odd-and-even pages
setting that differs from the template's, a phrase whose formatting changes partway through. Nothing is guessed and
nothing is half-edited: a copy passes every check or is not made. After the copies are created, **Try these in
Default mode** puts the untouched documents on your to-do list in Default mode, where the conversation can plan them.

Besides Word documents you can attach pictures and tables for the edit to use, such as a new logo to swap in or
insert, a schedule to place as a table, or a spreadsheet of project values to fill in; Claude is told about them and can read the tables, and nothing
beside your documents is ever looked for.

For Default mode, connect an Anthropic API key once in **Settings**. There is no spending limit: every AI step runs Claude Opus 5.5
with room for its full 128,000-token answer (the conversation, and the visual check and custom methods it runs inside
a turn, at medium effort; the outcome planning when no ordinary plan fits (no operation for the request, a plan that
covers no file, a sample that fails its check, or a preview whose every sample is refused), and recovery, at high;
no step at max; every operation's settings ride the cached prompt, so a plan is one or two calls; that prompt is cached for an
hour, so a follow-up request reads it instead of paying for it again). Instead, the
header shows the estimated spending as it accrues, **Stop now** cuts off the AI step that is running at once, and
work pauses after every 48 AI steps until you press **Continue**, which picks the conversation up where it stopped. A call that fails for a
passing reason (an overloaded service, a dropped connection) is sent again automatically. Every request needs
the key: without one the window keeps your draft and asks you to connect one in Settings.

**Settings → Fast responses** trades money for time. It is off until you turn it on, and Settings shows both sides
before you do: Claude Opus 5.5 writes its answers (and its thinking) up to 2.5 times faster, the same model at the
same effort, every plan checked, previewed and verified exactly as before; and every AI step costs twice as much ($8
instead of $4 per million input tokens and $40 instead of $20 per million output tokens; the cache discounts apply
on top). Turning it on takes a second click on a button that names the price; one click turns it off. It speeds up
Claude's writing, not the moment before it starts and not the work on your computer. When Anthropic's fast capacity
is busy, a step runs at standard speed and price at once instead of waiting; if Anthropic refuses fast mode for your
key, every step runs at standard speed and Settings says why. While it is in effect, the spending estimate in the top
bar is priced at the fast rates and says **Fast**. The choice is kept for the next start. Switching it during a
conversation makes the next step re-read the conversation once without the cache discount (the two speeds are
cached separately).

**Settings → Appearance** picks the window's colour scheme: Warm Clay (the default), Blueprint, Graphite & Teal,
Carbon & Hi-Vis or Linen (the light one). The window changes at once and opens in the scheme you chose from then on.

**Many documents at once.** The work on your computer is shared over its cores. A batch of three or more documents is
edited and checked in worker processes of the app, one per core (at most eight), each document from start to finish
in one of them, so its copy and its verification record are exactly what they would be alone; a preview plans twelve
or more documents, and checks three or more samples, the same way. Word renders the pages in up to three private,
invisible instances side by side (one per two cores; never the Word you have open). On eight 50-page sections and
four cores, creating the copies (before rendering) took 1.6 s instead of 4.6 s. The environment variable
`SPEC_SURGEON_WORD_INSTANCES=1` keeps Word to one instance.

**When something goes wrong.** **About → Save a diagnostic bundle** writes one zip (and shows it in Explorer) with
everything a diagnosis reads: the app's log and its event journals, crash logs, its settings, your current and last
conversations, and the logs, records and reports of your recent runs. Folders of copies and
page pictures are left out, but conversations and records can quote your words and your documents' text, and a
conversation's saved history can hold the page images Claude was shown, so it is yours to share or not. The
log says what the window itself went through: each request and job as it starts and ends, every error in the window's
page, a page that stopped answering (a frozen window) with where every part of the app was at that moment, a job
that stopped moving or did not obey Stop, the window closing, and at the next start what a session that never closed
was doing last. If the window ever fails to draw, it shows **Reload the window** instead of going blank: the app
behind it keeps running, so the conversation and any work in progress carry on.

**Help → Why trust it** says in eight points why the copies can be trusted and what runs without you, and **I'm not
convinced — show me exactly what runs →** opens the long answer: every action with what runs, what is sent, which AI
(if any) is involved and what bounds it, what leaves your computer, what Claude may touch, and how to check all of it
yourself. [docs/TRUST_CLAIMS.md](docs/TRUST_CLAIMS.md) is the ledger behind it: every claim, the code that makes it
true, and the test that checks it — and the findings where the code falls short of what one would hope.

See [the window guide](docs/CONVERSATIONAL_DESKTOP.md) for triage, pictures and tables, layouts, previews,
conversations and a clean start, recovery, resource use, verification limits and what the window logs about itself.

## Without the window

There is no command line. Developers and coding agents drive the engine from Python, and the test suite
(`python -m pytest -n auto`, one worker per core) is how a change is checked; see [CLAUDE.md](CLAUDE.md). A batch
from Python:

```python
from pathlib import Path
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import load_spec

spec = load_spec(Path("examples/specs/change-all-font-arial.json"))
result = run_batch([r"C:\Projects\AVC11\Specs"], "edit-spec", {}, Path(r"C:\Temp\ss-fonts"), spec=spec)
print(result.counts())          # {'PASS': ..., 'FAIL': ..., 'EXCEPTION': ..., 'PENDING': ...}
```

Inputs may be files, folders (every `*.docx`, lock files skipped) or globs, mixed. The results folder holds the
verified copies under `outputs/`, a verification record per file under `records/`, `BATCH_REPORT.md` and
`DIAGNOSTICS.md`; nothing is written over an original. Files are planned, edited and verified one at a time unless
`workers=N` asks for more: that work holds one processor core whatever the thread count, so extra threads only add
memory (rendering runs its own parallel renderers either way).

## The verification record

One per file, `records/<name>.record.md` and `.json` in the results folder. It is the audit artifact: what to read
when someone asks "how do I know nothing else changed?"

| Check | What it asserts |
|---|---|
| **V1** Package whitelist | only the plan's intended parts changed; members removed or added exactly as predicted; member order kept; no new dangling relationship or content-type override |
| **V2** Text preservation | per-paragraph text across every text part (body, headers, footers, footnotes, endnotes, comments) differs from the source exactly as the predicted diff list says, count and content |
| **V3** Structural children | per-paragraph counts of tabs, breaks, hyphens, symbols, field characters, references and drawings identical, or changed exactly as declared |
| **V4** Revision accounting | every kind of tracked change moves by its predicted delta (default 0); every new revision id unique, reusing no id of a bookmark, comment or revision anywhere in the source (styles and numbering included), and no larger than the largest Word reads (2,147,483,647) |
| **V5a** Schema vs baseline | changed parts validated against the vendored ECMA-376 schemas; zero errors the source did not already have |
| **V5b** Untracked-edit check | tracked edits: the reject-all view of the output equals the source character for character; formatting edits: no insertions or deletions added |
| **V5c** Revision grammar | deleted text only inside deletions, id and author on every wrapper, `xml:space` on edge whitespace |
| **V6** Render comparison | source and output rendered with the same renderer, changed pages rasterized; automated evidence, not human acceptance |
| **V7** Geometry | word positions on every page the plan did not expect to change move by at most 0.5 pt; N/A with the op's reason where layout changes by design |
| **V8** Accept / reject | the accept-all view equals the predicted view and the reject-all view equals the original |
| **V9** Overwrite guard | at delivery the source's size, time and digest are unchanged since it was read; no lock file; atomic replace |
| **V10** Property assertions and residual | every asserted formatting change is re-resolved independently, target counts equal the plan's, each target carries exactly that change; after stripping what the assertions cover, the changed parts must be identical to the source |

Every row is PASS, FAIL or N/A, and N/A always carries its reason. A file is delivered only when no check is FAIL;
one that fails is moved to `failed/` for diagnosis and never delivered. The record's **WHY IT FAILED** section names
the paragraph, the characters and the check in specifics, and `DIAGNOSTICS.md` adds where the file stopped and its
trace. The record distinguishes checks that were performed from checks that were N/A, and automated rendering from
a person looking at the pages: a PASS means the engine found exactly the predicted change and nothing else, not that
the result is what you wanted.

## EditSpecs

An **EditSpec** is a JSON program over the operation library: a request, a summary, assumptions, a mode, a list of
operations with their selectors and parameters, numeric expectations the plan must reproduce on every file, and
post-conditions. Claude writes one from your request; you can also write one by hand or edit the `spec.json` a
preview wrote. `examples/specs/` holds a worked example for every operation. Unknown keys, unknown operations,
wrong parameter types, open questions and two operations that touch the same thing are refused before anything runs.
A **layout plan** is several EditSpecs in one file (`"kind": "layouts"`, one spec per structure pattern): each spec is
planned on the files of its own pattern, and a file no layout covers is set aside.

Operations cover fonts and sizes; header/footer templating from a template or an architect's spec; page numbering;
tracked text replacement (whole words unless you say otherwise; new text may hold tabs and line breaks) and replacement tables; inserting paragraphs, with
styles and bold, italic or superscript segments; deleting paragraphs, from one to the whole body, with the tables,
fields, section breaks, comments and notes inside them (tracked, over someone's pending tracked changes as Word
deletes over them); moving paragraphs and the tables between them; placeholders
and site variables; MasterSpec option groups; tagged paragraphs; issue stamps; END OF SECTION lines; output names;
inserting, removing, resizing and swapping pictures; inserting and deleting tables, rows and columns, cell shading,
alignment and widths, and merged cells; comments (adding, replying, resolving, removing), highlighting, hidden text,
specifier notes, footnotes and endnotes, blank paragraphs, hyperlinks, fields (inserting page numbers, dates, style
references and properties, or unlinking them), watermarks, document properties (a title built from each
document's own section number and title included), track changes and Mark as Final;
bold, italic, caps, superscript and subscript, hidden text, highlight, character spacing, colour and pagination
flags; tab stops; borders and shading; style spacing, style application and style mapping from a template; clearing
direct formatting; the proofing language; unused styles; numbering schemes, typed numbers, list levels and a
numbering audit; table properties and table styles; page setup, columns, line numbers and section breaks inserted or
removed; character hygiene; and review comments with wording you supply. A large change is an edit like any other:
deleting a whole PART, or everything, is planned, previewed and verified the same way.
Every operation also describes itself in one plain sentence; those sentences are how each edited copy's changes are
recorded for the requests that follow.

## Limits

- **Nothing here has been proven on your files yet.** The engine, the window and the installer are tested on
  synthetic fixtures (including a family of deliberately dirty files) and in CI; the live Claude path, Word
  rendering and the first real batches are the user's to run. Expect fixes from the first real runs of every operation.
- Safe mode's buttons do only what each says. A document a button cannot change exactly is left untouched with the
  reason; no AI looks at it there, and it can be handed to Default mode. Text boxes, shapes and the other objects
  out of reach stay out of reach in every mode.
- A PASS is the engine's proof of the predicted change, not acceptance of the result. Look at the before/after
  pages. Claude's visual check is advisory and can be wrong both ways.
- Normalizing a file (typed numbers, direct formatting, whitespace, pending tracked changes) is never automatic; it
  runs only as a first stage you were offered and accepted.
- Where Word's behaviour is uncertain, or Word and LibreOffice differ (shared list definitions, overridden levels,
  linked lists, hidden numbered paragraphs), the engine refuses rather than predicts; LibreOffice proofs can differ
  from Word there.
- Operations that touch the same part of a file are never ordered by guess inside one specification: the window
  composes them into ordered steps in the order they were listed, each step planned and verified on the one before
  it. Edits inside text boxes, finding text across tabs or line breaks, tracked cell-level table changes, tracked
  numbering changes, run and table-cell borders, and tab stops in styles are not supported. Shapes, SmartArt, charts,
  equations, embedded objects, data-bound content controls and the theme are out of reach by decision: an edit that
  would touch one is refused with a reason, to be made in Word.
- Column deletion, cell merges, cell properties, section breaks and picture sizes are never tracked (Word does not
  track them as such); inserted fields carry no result until Word updates them (page numbers at once, other fields
  when the file opens).
- Header/footer templating pins the properties it models; conditional table-style formatting, east-Asian and
  complex-script fonts and sizes are not modelled. A toggle set at more than one style level is refused.
- Custom transforms are plain formatting on text parts, the styles part and (for list labels only) the numbering
  part, write edited copies only, and run in a sandbox that is not an operating-system security boundary.
- The installer is unsigned: the checksum checks prove the download is the file the release lists, not who published it.
- Costs shown for Claude calls are estimates from a built-in price table.

## Where everything else is

- [docs/CONVERSATIONAL_DESKTOP.md](docs/CONVERSATIONAL_DESKTOP.md) — the window guide.
- [docs/TRUST_CLAIMS.md](docs/TRUST_CLAIMS.md) — the claims ledger behind Help → Why trust it and its long answer.
- [docs/DEVELOPER_DIAGNOSTICS.md](docs/DEVELOPER_DIAGNOSTICS.md) — investigating a failure: what each log and journal
  holds, how to follow one action through them, and what the window records about itself.
- [docs/releases/](docs/releases/) — release notes per version, what the update banner shows.
- [docs/audits/](docs/audits/) — independent audits, sweeps and their responses.
- [docs/history/](docs/history/) — the previous CLAUDE.md files ([2026-10-18](docs/history/CLAUDE-2026-10-18.md),
  [2026-10-20](docs/history/CLAUDE-2026-10-20.md)) and [README.md](docs/history/README-2026-10-18.md), five days of
  design decisions, definitions and change notes, [the design and phased plan of the natural-language
  editor](docs/history/NL_SURGICAL_EDITOR_PLAN.md), and the guides to the command-line features removed on 2026-10-21
  (workspaces, workflows, office profiles, spec QA audits, project evidence, evaluations). The current round's plan is
  `docs/PLAN-2026-10-22-batch-consistency.md` (what the first live run found); the full-control round's,
  `docs/PLAN-2026-10-21-full-control.md`, waits on its live runs.
- [docs/RELIABILITY.md](docs/RELIABILITY.md) — the review flow, what PASS means and the remaining evidence limits;
  [docs/RECOVERY.md](docs/RECOVERY.md) — how files a run set aside are investigated;
  [docs/TEXT_HYGIENE.md](docs/TEXT_HYGIENE.md) — the character hygiene operations; the
  `docs/BUILTIN_OPS_BATCH*_DESIGN.md` notes define every operation.
- [src/spec_surgeon/skill/SKILL.md](src/spec_surgeon/skill/SKILL.md) — the `docx-surgical-edit` method the engine
  implements; its verification checklist is the suite.
- [CLAUDE.md](CLAUDE.md) — the invariants and working notes for anyone changing the code.

## License

Spec Surgeon's own code is free software under the [MIT License](LICENSE), Copyright © 2026 Abraham Borg. It
uses [PyMuPDF](https://github.com/pymupdf/PyMuPDF) and MuPDF by Artifex Software, which are licensed under the
[GNU Affero General Public License v3](src/spec_surgeon/legal/AGPL-3.0.txt) (AGPL) or a commercial license from
Artifex. No commercial license is held, so the Windows app, which bundles PyMuPDF, is distributed as a whole under
the AGPL v3: you may use, change and share it, and whoever shares it passes it on under the AGPL v3 with its
complete source. The MIT License still applies to Spec Surgeon's own code taken by itself.

The installer shows the full terms (the app's `LICENSE.txt`: the notice, the MIT License and the AGPL v3) and
installs them with the app; an in-app update runs the installer silently, so its pages are skipped, and the same
text is under **About** → **Licenses**. Each release page carries the Corresponding Source: the tag's source
archives (this repository, with the build scripts in `packaging/windows`) and the source of the exact PyMuPDF and
MuPDF the installer was built with (`pymupdf-<version>.tar.gz`, `mupdf-<version>-source.tar.gz`, fetched by
`packaging/windows/license_notices.py` and listed in `SHA256SUMS`).

It comes as is, without warranty of any kind, so check every delivered file yourself. The installed app bundles
other open-source components under their own licenses; the About box lists them. Contact: abrahamborg@gmail.com ·
[GitHub](https://github.com/Abe-Borg/spec-surgeon) · [LinkedIn](https://www.linkedin.com/in/abrahamborg/).
