# Unreleased

Changes since v1.6.0.

## The window

- Every preview card carries a green checkmark (looks good) and a red X (needs more work). **Create edited
  copies** runs the documents that look right; the X'd ones get no copy and wait on a to-do list ("Create edited
  copies (2 set aside)"). Every file that runs is still held to the exact identity the user approved; a file
  outside the approval never runs. Once the other copies are saved, the chat shows **Set aside for more work**
  and offers **Work on them now**: the set-aside documents are planned alone, from the request they were set
  aside from (one request at a time), with the assistant told that the user saw and set aside that plan and
  shown it; the pass ends when their copies are created. The changes each copy carries are kept per document, so
  a change some copies got and others did not is named per document in the next request. The list and the pass
  survive a restart; Start over clears them.
- The one button (**Create edited copies**, **Continue**, **Save N ready copies**) lives in the conversation,
  above the box you type in, with one sentence on what it does. The plan card carries no button.
- **Your documents** folds to one line (its count and a summary) once a plan exists and opens on a click; a
  document on the to-do list is tagged **To do**, one in the pass **Working on**.
- The **Hide plan** button is gone; the plan and the pages are always shown beside the conversation.
- Markdown renders in both panes: the assistant's replies and questions, the choices, the plan's sentences and
  settings, the assumptions, the findings and the reasons a file was left untouched.

## The contract (`docs/WEB_API.md`)

- `POST /api/primary` takes `hold`: the previewed documents to set aside. The view carries `action.can_hold`,
  `todo` and `focus`. The reply "Work on the documents I set aside" begins the pass over the to-do list.
## The top bar matches Build-a-Spec's

- **An in-app guide in the top bar.** **How to use**, **Workflows**, **How it works** and **About** open one help
  dialog with a tab strip: a first pass in eight steps, what each note under a file means, how spending, Stop now and
  the pause work, recipes for the usual jobs (fonts across a set, headers and footers from a template, a logo, values
  from a sheet, an issue stamp, a mixed batch, one change after another, a file left untouched, stopped or paused
  work) and how it works in plain words.
- **About replaces the Help menu.** The version, the model, the release notes, the license and its open-source
  components, the author's links, **Check for updates** and **Open diagnostic logs** are on one page. A check you
  ask for now answers there: "Checking for updates…", then "You're up to date", why it could not check, or the
  update with **Download and install**.
- **What the conversation works on**, beside the title from 1280 px wide: "3 documents · 1 template · building on
  your copies".
- **An update you set aside stays one click away.** After **Not now** on the update banner, the top bar keeps a
  "v1.7.0 available — install" pill (from 1280 px wide; About offers it at any width).

## Full control: from one word to whole tables, sections and pictures

Spec Surgeon now refuses only what it cannot predict, never what is large: deleting an article, a PART or the whole
body is planned, previewed and verified like any other edit. Every new operation predicts its result before it runs
and is checked by the independent verifier; content changes are tracked by default, as Word records them.

- **Delete anything a selection covers.** "Delete article 2.3", "delete PART 3 to the end", "delete everything":
  tables wholly inside the selection go with it, as do whole fields, comments and notes; a section break in it joins
  the sections, as Word does. A selection can now run from a heading up to, not including, the next one. Footnotes
  and endnotes can be removed, all or by their text.
- **Move content.** A stretch of paragraphs, with the tables between them, goes before or after another paragraph:
  untracked, or tracked as a deletion where it was and an insertion where it goes.
- **Richer text.** New text may hold tabs and line breaks; one request inserts several paragraphs, each with its own
  style, with bold, italic, underlined or superscript words. Superscript and subscript, hidden text, highlight,
  character spacing and double strikethrough can be set on text or in styles.
- **Tables.** Insert a table from an attached spreadsheet or CSV (or typed rows); add and delete rows; delete tables
  and columns; shade cells, set their vertical alignment and column widths; merge cells. Tracked row insertions and
  deletions can be accepted or rejected.
- **Pictures.** Insert an attached picture (PNG, JPEG, GIF or BMP) in its own paragraph, remove pictures, resize them.
- **Sections.** Insert and remove section breaks; set columns, line numbers, vertical alignment and how a section
  starts.
- **Fields and comments.** Insert page numbers ("Page X of Y"), dates, the file name, a style reference or a document
  property, at your placeholder or at the end of a paragraph; Word fills in the result. Reply to comments with your
  words and resolve or reopen threads.
- **An empty document is taken** with a note instead of refused, so a blank shell can be built on.
- Still out of reach by decision, and refused with a reason: shapes, SmartArt, charts, equations, embedded objects,
  data-bound content controls, the theme, and edits inside text boxes.

Not yet run in Word on real specifications: every new operation is tested on synthetic documents and rendered with
LibreOffice; the first real runs are yours.

Upgrading from 1.6.0 starts from 1.6.0's own window: the update banner's **Download**, or **Help ▾ → Check for
updates** in its top bar.

## No command line

- **The `spec-surgeon` command is gone**, and with it the features only it offered: the recipe library (recipes,
  chains, saved operations, snippets), `advise`, `retry` and `resolve` (recovery runs inside the window), the
  `memory` commands, campaign workspaces and project evidence, built-in workflows, office profiles, specification
  QA audits, the evaluation tools, the diagnostics bundle (`diagnose`) and the two named v0.1.0 jobs. The installer
  no longer ships `spec-surgeon.exe`; `python -m spec_surgeon` opens the window. Nothing changes in the window.
- **Reports** no longer tell you to run a command: a set-aside file's guidance says what to do next, and a defect
  asks for `DIAGNOSTICS.md`, the `logs/` folder and the app's logs (**About → Open diagnostic logs**).
- **Repair memory** still runs in the window, but nothing in the app can inspect, accept, reject or forget a
  remembered case any more.

## A smaller test suite

- 430 of the 4,114 tests covered the removed features, dead code or duplicated checks and are gone (3,684 remain), and
  the test files named after review rounds are folded into the files for the features they test (175 → 137 files).
- Pull-request CI runs the Windows suite once instead of twice; the installer job skips it when the Windows test job
  has just run it.

Pending on the owner's machine: the installed Windows app without `spec-surgeon.exe`.

## Lower model cost for follow-up requests

- **The instructions are paid for once an hour, not once per request.** The assistant's instructions and the list of
  operations (about 40,000 tokens, the same for every conversation) now carry their own one-hour cache breakpoint. A
  follow-up after copies are created, "Start over from the originals", or a changed attachment starts a new
  conversation; its first call now reads those instructions from the cache (on Claude Opus 5.5, about $0.01 instead
  of about $0.20, and a faster first reply) instead of writing them again. Before, only the growing conversation was
  cached, for five minutes, so every new conversation paid for the whole prompt again.
- **One-call checks no longer pay for caching they never reuse.** The visual check of the preview pages and the
  recovery critic are single calls; their page images and evidence are now billed as ordinary input rather than at
  the cache-write price.
- **The estimated spending prices one-hour cache writes at their own rate** (twice the input price), and each call's
  record in `conversation.json` and `compile.json` shows how many of its cache writes were for the hour.

## Diagnostics

- **The app log says how every job ended.** `spec-surgeon.log` gets one line at the end of each piece of work in the
  window: what was asked for (send, choose or create copies), where it left the conversation (a plan to approve, a
  question, finished copies, the 48-step pause, ready to continue), whether you stopped it, the type of the error
  when it failed, the AI steps used so far, the estimated spending and how long it took. It never records your words
  or the documents' text.
- **The app log says when the window closed.** A clean exit writes `Spec Surgeon closed` again, as the Qt window did
  before 1.6.0; a session that ends without it did not close cleanly.
