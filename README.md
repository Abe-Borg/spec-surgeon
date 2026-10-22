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
approve, to look at preview pages, and to investigate files the engine set aside.

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

Microsoft Word or LibreOffice is still needed for page rendering; neither is installed by this setup program. Typed
requests need your Anthropic API key: enter it in the window's **Settings** and tick *Remember securely on this
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
   Each preview carries a green checkmark (looks good) and a red X (needs more work: set it aside for after the
   others).
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

Besides Word documents you can attach pictures and tables for the edit to use, such as a new logo to swap in or
insert, a schedule to place as a table, or a spreadsheet of project values to fill in; Claude is told about them and can read the tables, and nothing
beside your documents is ever looked for.

Connect an Anthropic API key once in **Settings**. There is no spending limit: every AI step runs Claude Opus 5.5
with room for its full 128,000-token answer (the conversation at the model's default effort, recovery — the planning
after a preview that set files aside included — and custom methods at high, and no step at max; every operation's
settings ride the cached prompt, so a plan is one or two calls; that prompt is cached for an hour, so a follow-up request reads it instead of paying for it again). Instead, the
header shows the estimated spending as it accrues, **Stop now** cuts off the AI step that is running at once, and
work pauses after every 48 AI steps until you press **Continue**, which picks the conversation up where it stopped. A call that fails for a
passing reason (an overloaded service, a dropped connection) is sent again automatically. Every request needs
the key: without one the window keeps your draft and asks you to connect one in Settings.

**Settings → Appearance** picks the window's colour scheme: Warm Clay (the default), Blueprint, Graphite & Teal,
Carbon & Hi-Vis or Linen (the light one). The window changes at once and opens in the scheme you chose from then on.

**When something goes wrong.** **About → Save a diagnostic bundle** writes one zip (and shows it in Explorer) with
everything a diagnosis reads: the app's log and its event journals, crash logs, its settings, your current and last
conversations, and the logs, records and reports of your recent runs. No document, picture or page image goes in
it; conversations and records can quote your words and your documents' text, so it is yours to share or not. The
log says what the window itself went through: each request and job as it starts and ends, every error in the window's
page, a page that stopped answering (a frozen window) with where every part of the app was at that moment, a job
that stopped moving or did not obey Stop, the window closing, and at the next start what a session that never closed
was doing last. If the window ever fails to draw, it shows **Reload the window** instead of going blank: the app
behind it keeps running, so the conversation and any work in progress carry on.

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
fields, section breaks, comments and notes inside them; moving paragraphs and the tables between them; placeholders
and site variables; MasterSpec option groups; tagged paragraphs; issue stamps; END OF SECTION lines; output names;
inserting, removing, resizing and swapping pictures; inserting and deleting tables, rows and columns, cell shading,
alignment and widths, and merged cells; comments (adding, replying, resolving, removing), highlighting, hidden text,
specifier notes, footnotes and endnotes, blank paragraphs, hyperlinks, fields (inserting page numbers, dates, style
references and properties, or unlinking them), watermarks, document properties, track changes and Mark as Final;
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
- Custom transforms are plain formatting on text parts and the styles part, write edited copies only, and run in a
  sandbox that is not an operating-system security boundary.
- The installer is unsigned: the checksum checks prove the download is the file the release lists, not who published it.
- Costs shown for Claude calls are estimates from a built-in price table.

## Where everything else is

- [docs/CONVERSATIONAL_DESKTOP.md](docs/CONVERSATIONAL_DESKTOP.md) — the window guide.
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

Spec Surgeon is free software under the [MIT License](LICENSE), Copyright © 2026 Abraham Borg. It comes as is,
without warranty of any kind, so check every delivered file yourself. The installed app bundles open-source
components under their own licenses; the About box lists them. Contact: abrahamborg@gmail.com ·
[GitHub](https://github.com/Abe-Borg/spec-surgeon) · [LinkedIn](https://www.linkedin.com/in/abrahamborg/).
