# Spec Surgeon

Surgical edits to Word (.docx) specification files, a few files or dozens at a time, with an independent check and a
verification record for every file.

Spec Surgeon is for people who maintain construction specifications: the engineer or designer who has to change the
font, the header and footer, the issue stamp, the numbering or a defined term across a whole set of sections, and who
cannot afford a single file in which a paragraph went missing or a word appeared that nobody wrote. The transform is
deterministic Python over the document XML. The expected result is predicted *before* the transform runs. A separate
verifier, sharing no code with the transform, reads only the before and after packages and asserts that prediction.
A file is delivered only when every check passes and the source has not moved since it was read. No language model
is in the per-file loop; Claude is optional, and its job is to turn a plain-English request into a specification you
approve, to look at preview pages, and to advise on files the engine set aside.

It runs as a Windows desktop app, as a command line on Windows, macOS or Linux, and as a Python library.

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
computer* to keep it in Windows Credential Manager. Saved EditSpecs run through the command line without one. The
installer is unsigned, so Windows may show a publisher prompt on the first manual install.

**Python package / development setup** (Python 3.10+ on Windows, macOS or Linux; from the project folder):

```powershell
py -3 -m pip install -e .            # core: lxml, pymupdf
py -3 -m pip install -e ".[gui]"     # the window: FastAPI, uvicorn, pywebview (WebView2 on Windows) — spec-surgeon-gui
py -3 -m pip install -e ".[word]"    # Windows: adds pywin32 so Word is the renderer/oracle (recommended)
py -3 -m pip install -e ".[agent]"   # optional: anthropic SDK for typed requests (`do`, the window), `advise`
py -3 -m pip install -e ".[dev]"     # pytest + python-docx (fixtures)
```

or `py -3 -m pip install -r requirements.txt` for everything. Start the window with `spec-surgeon-gui` (or
`spec-surgeon gui`); a copy installed this way is not updated in-app, the banner shows the `pip install -U` command
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
| `ANTHROPIC_API_KEY` | typed requests, Claude's visual check, custom transforms and `advise`; or enter it in Settings |
| `SPEC_SURGEON_MODEL` | the model every Claude call uses (default `claude-opus-5-5`; `--model` overrides per command) |
| `SPEC_SURGEON_NO_UPDATE_CHECK` | any value: the window never checks for updates on its own |
| `SPEC_SURGEON_CONFIG_DIR` | where the app keeps `settings.json`, the saved conversation, the command-line library, downloaded updates and its logs |

## The window: documents and a conversation

![The conversation: Word documents, an optional template and a request](docs/conversation-start.png)

The window is one conversation. On the left is the chat; on the right are your documents (optionally with one
template), the plan, the pages and one button. The top bar, in the style of Build-a-Spec, carries the in-app guide —
**How to use**, **Workflows**, **How it works** and **About** (the version, the model, the release notes, the license,
**Check for updates** and **Open diagnostic logs**) — beside a line saying what the conversation works on ("3
documents · 1 template · building on your copies"), the estimated spending, the connection, **Hide plan**, **New
conversation** and Settings.

1. **Add Word documents.** Drop `.docx` files anywhere in the window, or use **Add Word documents**. Files can come
   from different folders, including files with the same name. Only the files you attach are edited; nothing is
   picked up from their folders.
2. **Read the note under each file.** The moment a file is attached, Spec Surgeon reads it (milliseconds, no model
   call) and says what it found in one line each: typed numbers instead of automatic list numbering, no paragraph
   styles, text boxes in the headers or footers, an older compatibility mode, editing restrictions, pending tracked
   changes, comments, irregular spacing, an empty body. A file that does not open as a Word package, or has no
   paragraph at all in its body, stays in the list greyed out with the reason and never enters a plan. A `.docm`, `.doc` or `~$` lock file is not
   attached; a notice asks for a `.docx`.
3. **Optionally add a template.** One reference `.docx` for requests such as "Match the headers and fonts to this
   template. Keep our technical wording."
4. **Describe the result.** Type what you want and press **Send** (Ctrl+Enter). Spec Surgeon inspects the documents,
   goes ahead on sensible defaults (and says what it assumed), asks at most three questions, each with the answer it
   recommends, and prepares a plan — while you watch its words, its thinking summary and each step as they happen.
   A question comes as a card with buttons and short replies you can tap: click one, type its number, or answer in
   your own words. A choice that would put words into the documents shows those exact words; picking it approves
   exactly those words (a tapped reply never does). Changes that touch the same parts of a file — the template's
   header and footer beside its font, say — are composed into ordered steps by the engine; nothing is asked of you
   in two steps.
5. **Review the plan.** **Here's what will change** lists one sentence per change, written by the operation that will
   make it, in inches and points, never a setting name. Assumptions appear beneath the changes; **See every setting**
   opens the full list. Below it: rendered before/after pages for sample documents with their page counts, and
   **Left untouched**: any file the plan cannot cover, with the reason.
6. **Create edited copies.** One button approves the displayed plan. The engine applies it to every file, verifies
   each one independently, and lists the copies under **Your edited copies**; click one to open it in Word.
   Originals are never written. A file that fails a check is not delivered; Spec Surgeon checks it again internally
   and comes back with a question or a new plan, never a method to choose.

**Mixed batches.** Documents from different projects are often built differently: styled article titles in one,
plain paragraphs with typed numbers in another. For such a batch the plan has one card per layout ("Styled, PR1
articles · 8 documents"), each with its own changes, assumptions and preview. One button still approves all of
them. A document built differently from every layout the plan covers is left untouched with a sentence.

Keep chatting: the next thing you ask for is made on the copies the last run created, your originals stay as they
are, and **Start over from the originals** is one tap away. Each run's copies go into their own folder, so earlier
copies remain. The conversation, your decisions, the attachments, the working copies and the result locations are
saved locally and come back when you reopen the app, with every attachment checked again.

![The plan in prose and before/after pages, with one action](docs/conversation-preview.png)

Besides Word documents you can attach pictures and tables for the edit to use, such as a new logo to swap in
or a spreadsheet of project values to fill in; Claude is told about them and can read the tables, and nothing
beside your documents is ever looked for.

Connect an Anthropic API key once in **Settings**. There is no spending limit: every AI step runs Claude Opus 5.5
with room for its full 128,000-token answer (the conversation at the model's default effort, recovery and custom
methods at high; every operation's settings ride the cached prompt, so a plan is one or two calls). Instead, the
header shows the estimated spending as it accrues, **Stop now** cuts off the AI step that is running at once, and
work pauses after every 48 AI steps until you press **Continue**, which picks the conversation up where it stopped. A call that fails for a
passing reason (an overloaded service, a dropped connection) is sent again automatically. Typed requests need
the key; saved specifications run through the command line without one.

See [the window guide](docs/CONVERSATIONAL_DESKTOP.md) for triage notes, pictures and tables, layouts, previews,
saved conversations, recovery, resource use and verification limits.

## The command line

```powershell
spec-surgeon ops --catalog                                   # every operation, its selectors and parameters, the EditSpec schema
spec-surgeon profile "C:\Projects\AVC11\Specs"               # what the files look like, per structure pattern
spec-surgeon plan-spec examples\specs\change-all-font-arial.json "C:\Projects\AVC11\Specs"   # dry run, nothing written
spec-surgeon run-spec examples\specs\change-all-font-arial.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-fonts
spec-surgeon run-spec examples\specs\remove-all-headers-footers.json "C:\Specs\21*.docx" --in-place --no-render
spec-surgeon do "change all fonts to Arial" "C:\Projects\AVC11\Specs" --out C:\Temp\ss-run   # compile, preview, y/N, run
spec-surgeon chain --stage match-font.json --stage "recipe:Architect header" "C:\Specs" --out C:\Temp\ss-chain
spec-surgeon library list                                    # your recipes, chains, operations and snippets
spec-surgeon library show NAME
spec-surgeon library use NAME "C:\Specs" --out C:\Temp\ss-recipe
spec-surgeon library delete NAME
spec-surgeon workflows list                                  # built-in workflows, no API key needed
spec-surgeon workflows show NAME
spec-surgeon workflows use NAME "C:\Specs" --inputs @inputs.json --out C:\Temp\ss-workflow
spec-surgeon profiles create OFFICE.docx --name "Office" --roles @roles.json --out C:\Profiles\office   # office standards
spec-surgeon structure "C:\Specs"                            # read-only paragraph roles and unresolved evidence
spec-surgeon audit "C:\Specs" --out SPEC_QA.md                # report-only specification QA
spec-surgeon verify SOURCE.docx OUTPUT.docx --plan plan.json   # re-run the suite on any pair
spec-surgeon diagnose OUT_DIR [--file NAME]                   # why a file did not pass
spec-surgeon advise OUT_DIR                                   # Claude's recommendation per exception
spec-surgeon retry OUT_DIR                                    # the next pass over the files a batch set aside
spec-surgeon resolve OUT_DIR                                  # bounded investigation of the files still set aside, then export
spec-surgeon workspace create "C:\Projects" --out C:\Temp\ss-workspace   # a persistent multi-project batch
```

Inputs may be files, folders (every `*.docx`, lock files skipped) or globs, mixed. Every command exits 0 only when
every file passed. `do` compiles with Claude, writes a preview under `OUT/preview/`, asks "Run this on N file(s)?"
(`--yes` without a terminal) and refuses to run if any file or template changed since the preview. Office
profiles, specification QA audits, project evidence (`spec-surgeon evidence`), built-in workflows, campaign
workspaces and recovery workspaces are command-line features; the window does not show them.

## The verification record

One per file, `records/<name>.record.md` and `.json` in the results folder. It is the audit artifact: what to read
when someone asks "how do I know nothing else changed?"

| Check | What it asserts |
|---|---|
| **V1** Package whitelist | only the plan's intended parts changed; members removed or added exactly as predicted; member order kept; no new dangling relationship or content-type override |
| **V2** Text preservation | per-paragraph text across every text part (body, headers, footers, footnotes, endnotes, comments) differs from the source exactly as the predicted diff list says, count and content |
| **V3** Structural children | per-paragraph counts of tabs, breaks, hyphens, symbols, field characters, references and drawings identical, or changed exactly as declared |
| **V4** Revision accounting | every kind of tracked change moves by its predicted delta (default 0); every new revision id unique |
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

## EditSpecs, recipes and the keyless command line

An **EditSpec** is a JSON program over the operation library: a request, a summary, assumptions, a mode, a list of
operations with their selectors and parameters, numeric expectations the plan must reproduce on every file, and
post-conditions. Claude writes one from your request; you can also write one by hand or edit the `spec.json` a
preview wrote. `examples/specs/` holds a worked example for every operation. Unknown keys, unknown operations,
wrong parameter types, open questions and two operations that touch the same thing are refused before anything runs.
A **layout plan** is several EditSpecs in one file (`"kind": "layouts"`, one spec per structure pattern); `plan-spec`
and `run-spec` accept one, plan each spec on the files of its own pattern, and set aside a file no layout covers.

Operations cover fonts and sizes; header/footer templating from a template or an architect's spec; page numbering;
tracked text replacement and replacement tables; placeholders and site variables; MasterSpec option groups; tagged
paragraphs; issue stamps; END OF SECTION lines; output names; logo swaps; comments, highlighting, hidden text,
specifier notes, blank paragraphs, hyperlinks, fields, watermarks, document properties, track changes and Mark as
Final; bold, caps, colour and pagination flags; tab stops; borders and shading; style spacing, style application and
style mapping from a template; clearing direct formatting; the proofing language; unused styles; numbering schemes,
typed numbers, list levels and a numbering audit; table properties and table styles; page setup and sections chosen
by content; character hygiene; and review comments with wording you supply. `spec-surgeon ops` lists them all. Every
operation also describes itself in one plain sentence; that sentence is what the window's plan shows.

**Recipes.** `spec-surgeon do ... --save-as NAME` saves the previewed spec as a recipe; `spec-surgeon library use
NAME` runs it again on another project. A **chain** is several recipes run one after another, each stage a full
verified run on the files the stage before it verified. A **snippet** is a saved paragraph inserted after an anchor.
One of **your operations** is a custom transform Claude wrote that passed verification; it is tried again on the
current files before every use. The library lives under `%APPDATA%\Spec Surgeon\library\`; the window never shows
or offers it.

**Without an API key** the command line still runs saved specs, your library, built-in workflows, office profiles,
verification, set-aside passes, reports and diagnostics; `do`, `advise`, custom transforms and Claude's visual
check need a key and say so. The window needs a key for every request: without one it keeps your draft and asks you
to connect one in Settings. Someone with a key can describe a change once, preview it and save it as a recipe;
everyone else reuses it at the command line with no key at all.

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
  it; on the command line such a specification is refused and a chain runs them in order. Edits inside text boxes,
  replacements across tabs or line breaks, tracked table or numbering changes, run and table-cell borders, and tab
  stops in styles are not supported.
- Header/footer templating pins the properties it models; conditional table-style formatting, east-Asian and
  complex-script fonts and sizes are not modelled. A toggle set at more than one style level is refused.
- Custom transforms are plain formatting on text parts and the styles part, write edited copies only, and run in a
  sandbox that is not an operating-system security boundary.
- The command-line library is local to one computer and one Windows user; recipes reference template files by
  absolute path.
- The installer is unsigned: the checksum checks prove the download is the file the release lists, not who published it.
- Costs shown for Claude calls are estimates from a built-in price table.

## Where everything else is

- [docs/CONVERSATIONAL_DESKTOP.md](docs/CONVERSATIONAL_DESKTOP.md) — the window guide.
- [docs/releases/](docs/releases/) — release notes per version, what the update banner shows.
- [docs/audits/](docs/audits/) — independent audits, sweeps and their responses.
- [docs/history/](docs/history/) — the previous [CLAUDE.md](docs/history/CLAUDE-2026-10-18.md) and
  [README.md](docs/history/README-2026-10-18.md), five days of design decisions, definitions and change notes, and
  [the design and phased plan of the natural-language editor](docs/history/NL_SURGICAL_EDITOR_PLAN.md). The
  current round's plan is `docs/PLAN-2026-10-19-ez-mode.md`.
- [docs/RELIABILITY.md](docs/RELIABILITY.md) — the review flow, what PASS means and the remaining evidence limits;
  [docs/BUILTIN_WORKFLOWS.md](docs/BUILTIN_WORKFLOWS.md), [docs/OFFICE_PROFILES.md](docs/OFFICE_PROFILES.md),
  [docs/RECOVERY.md](docs/RECOVERY.md), [docs/TEXT_HYGIENE_AND_SPEC_QA.md](docs/TEXT_HYGIENE_AND_SPEC_QA.md) — the
  guides to those command-line features; the `docs/BUILTIN_OPS_BATCH*_DESIGN.md` notes define every operation.
- [src/spec_surgeon/skill/SKILL.md](src/spec_surgeon/skill/SKILL.md) — the `docx-surgical-edit` method the engine
  implements; its verification checklist is the suite.
- [CLAUDE.md](CLAUDE.md) — the invariants and working notes for anyone changing the code.

## License

Spec Surgeon is free software under the [MIT License](LICENSE), Copyright © 2026 Abraham Borg. It comes as is,
without warranty of any kind, so check every delivered file yourself. The installed app bundles open-source
components under their own licenses; the About box lists them. Contact: abrahamborg@gmail.com ·
[GitHub](https://github.com/Abe-Borg/spec-surgeon) · [LinkedIn](https://www.linkedin.com/in/abrahamborg/).
