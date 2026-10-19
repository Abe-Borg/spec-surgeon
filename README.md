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
Start menu. The installer includes Python, Qt (the window), the editing engine, Word integration and the Anthropic
client; no separate Python installation or terminal commands are needed. From then on the app keeps itself up to date:
a banner offers each new version, and **Help → Check for updates…** checks at any time. It installs for the current
user without administrator access, offers a desktop shortcut and includes an uninstaller. Example EditSpecs are
available from **Spec Surgeon → Example edit specs** in the Start menu.

Microsoft Word or LibreOffice is still needed for page rendering; neither is installed by this setup program. Typed
requests need your Anthropic API key: enter it in the window's **Settings** and tick *Remember on this computer* to
keep it in Windows Credential Manager. Saved EditSpecs run without one. The installer is unsigned, so Windows may show a
publisher prompt on the first manual install.

**Python package / development setup** (Python 3.10+ on Windows, macOS or Linux; from the project folder):

```powershell
py -3 -m pip install -e .            # core: lxml, pymupdf
py -3 -m pip install -e ".[gui]"     # the window: PySide6 (Qt 6) — spec-surgeon-gui
py -3 -m pip install -e ".[word]"    # Windows: adds pywin32 so Word is the renderer/oracle (recommended)
py -3 -m pip install -e ".[agent]"   # optional: anthropic SDK for typed requests (`do`, the window), `advise`
py -3 -m pip install -e ".[dev]"     # pytest + python-docx (fixtures)
```

or `py -3 -m pip install -r requirements.txt` for everything. Start the window with `spec-surgeon-gui` (or
`spec-surgeon gui`); a copy installed this way is not updated in-app, the banner shows the `pip install -U` command
instead. On Linux and macOS use `python3` in place of `py -3`.

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
| `SPEC_SURGEON_CONFIG_DIR` | where the window keeps `settings.json`, your library, downloaded updates and its logs |
| `SPEC_SURGEON_THEME` | `light` or `dark`: force the window's colour scheme instead of following Windows |

## The window: four steps

![The first step: choose the files](docs/gui-files.png)

1. **Files.** Pick the folder with the .docx files (work on a copy the first few times). Optionally attach a
   **template spec**: an architect's spec, or a prepared header/footer template with highlighted or `{{NAME}}`
   placeholders, for requests like "make the header and footer match the template spec". It is left out of the batch.
   Choose a results folder; previews, edited copies, records and reports land there.
2. **Describe the change.** Type the request in plain English ("remove all headers and footers", "change all fonts
   to Arial", "make the header/footer look like this template"), or load a saved spec, or pick a recipe from **Your
   library**. When Claude needs something it cannot guess (the exact stamp wording, which sections) it asks in a chat
   under the request, with choices; a word Claude suggests goes into your documents only when you pick the choice
   that shows those exact words.
3. **Preview.** Claude turns the request into an EditSpec; the engine plans every file (nothing is written to your
   files), applies the spec to sample files in memory, verifies them and, with *Render pages* on, shows before/after
   page images. The page shows every file as *Planned* or *Exception* with the reason, the spec's assumptions to
   check, each sample's verification result, Claude's advisory visual check of the sample pages, and an approval
   token. For ordinary typed requests a separate **outcome review** lists the exact targets in every document for
   you to approve before Run. If the preview is not what you meant, say so in the chat and it is previewed again.
4. **Run & results.** By default the edited files go to the results folder and your files are not touched. Choose
   **Write verified results back over the originals (guarded)** to have passing files written back: never a file open
   in Word, never one changed since the preview. Run is enabled only while the preview is current; changing the
   folder, template, request or options disables it until you preview again. Afterwards: PASS / FAIL / EXCEPTION
   tiles, a results table (double-click a failed file to see where it stopped and why), the batch report, the
   diagnostics, and **Load set-aside files** to run the next pass over the files that did not go through.

![The Preview step](docs/gui.png)

**File → Built-in workflows…** configures repeatable style, numbering, table and proofing-language work without an
API key; **File → Office profiles…** applies an office standard with a per-paragraph review; **File → Spec QA
audits…** runs report-only checks. Each goes through the same preview, approval and verified run.

## The command line

```powershell
spec-surgeon ops --catalog                                   # every operation, its selectors and parameters, the EditSpec schema
spec-surgeon profile "C:\Projects\AVC11\Specs"               # what the files look like, per structure pattern
spec-surgeon plan-spec examples\specs\change-all-font-arial.json "C:\Projects\AVC11\Specs"   # dry run, nothing written
spec-surgeon run-spec examples\specs\change-all-font-arial.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-fonts
spec-surgeon run-spec examples\specs\remove-all-headers-footers.json "C:\Specs\21*.docx" --in-place --no-render
spec-surgeon do "change all fonts to Arial" "C:\Projects\AVC11\Specs" --out C:\Temp\ss-run   # compile, preview, y/N, run
spec-surgeon chain --stage match-font.json --stage "recipe:Architect header" "C:\Specs" --out C:\Temp\ss-chain
spec-surgeon library list | show NAME | use NAME FILES... | delete NAME
spec-surgeon workflows list | show NAME | use NAME FILES...
spec-surgeon verify SOURCE.docx OUTPUT.docx --plan plan.json   # re-run the suite on any pair
spec-surgeon diagnose OUT_DIR [--file NAME]                   # why a file did not pass
spec-surgeon advise OUT_DIR                                   # Claude's recommendation per exception
spec-surgeon retry OUT_DIR                                    # the next pass over the files a batch set aside
spec-surgeon audit "C:\Specs" --out SPEC_QA.md                # report-only specification QA
```

Inputs may be files, folders (every `*.docx`, lock files skipped) or globs, mixed. Every command exits 0 only when
every file passed. `do` compiles with Claude, writes a preview under `OUT/preview/`, asks "Run this on N file(s)?"
(`--yes` without a terminal) and refuses to run if any file or template changed since the preview.

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

## EditSpecs, recipes and the keyless path

An **EditSpec** is a JSON program over the operation library: a request, a summary, assumptions, a mode, a list of
operations with their selectors and parameters, numeric expectations the plan must reproduce on every file, and
post-conditions. Claude writes one from your request; you can also write one by hand or edit the `spec.json` a
preview wrote. `examples/specs/` holds a worked example for every operation. Unknown keys, unknown operations,
wrong parameter types, open questions and two operations that touch the same thing are refused before anything runs.

Operations cover fonts and sizes; header/footer templating from a template or an architect's spec; page numbering;
tracked text replacement and replacement tables; placeholders and site variables; MasterSpec option groups; tagged
paragraphs; issue stamps; END OF SECTION lines; output names; logo swaps; comments, highlighting, hidden text,
specifier notes, blank paragraphs, hyperlinks, fields, watermarks, document properties, track changes and Mark as
Final; bold, caps, colour and pagination flags; tab stops; borders and shading; style spacing, style application and
style mapping from a template; clearing direct formatting; the proofing language; unused styles; numbering schemes,
typed numbers, list levels and a numbering audit; table properties and table styles; page setup and sections chosen
by content; character hygiene; and review comments with wording you supply. `spec-surgeon ops` lists them all.

**Recipes.** Save a previewed spec to **Your library** under a name and use it again on another project, from the
window or with `spec-surgeon library use`. A **chain** is several recipes run one after another, each stage a full
verified run on the files the stage before it verified. A **snippet** is a saved paragraph inserted after an anchor.
One of **your operations** is a custom transform Claude wrote that passed verification; it is tried again on the
current files before every use. The library lives under `%APPDATA%\Spec Surgeon\library\`.

**Without an API key** the app runs in keyless mode: saved specs, your library, built-in workflows, office
profiles, Preview, Run, verification, set-aside passes, reports and diagnostics all work; typing a request, custom
transforms, Claude's visual check and advice on set-aside files need a key and say so in place. Someone with a key can
describe a change once, preview it and save it as a recipe; everyone else reuses it with no key at all.

![Choose the change: the second step without an API key](docs/gui-keyless.png)

## Limits

- **Nothing here has been proven on your files yet.** The engine, the window and the installer are tested on
  synthetic fixtures and in CI; the live Claude path, Word rendering and the first real batches are the user's to
  run. Expect fixes from the first real runs of every operation.
- A PASS is the engine's proof of the predicted change, not acceptance of the result. Look at the before/after
  pages. Claude's visual check is advisory and can be wrong both ways.
- Where Word's behaviour is uncertain, or Word and LibreOffice differ (shared list definitions, overridden levels,
  linked lists, hidden numbered paragraphs), the engine refuses rather than predicts; LibreOffice proofs can differ
  from Word there.
- Conflicting operations are refused, not ordered: run them as a chain. Edits inside text boxes, replacements across
  tabs or line breaks, tracked table or numbering changes, run and table-cell borders, and tab stops in styles are not
  supported.
- Header/footer templating pins the properties it models; conditional table-style formatting, east-Asian and
  complex-script fonts and sizes are not modelled. A toggle set at more than one style level is refused.
- Custom transforms are plain formatting on text parts and the styles part, write edited copies only, and run in a
  sandbox that is not an operating-system security boundary.
- Your library is local to one computer and one Windows user; recipes reference template files by absolute path.
- The installer is unsigned: the checksum checks prove the download is the file the release lists, not who published it.
- Costs shown for Claude calls are estimates from a built-in price table.

## Where everything else is

- [docs/releases/](docs/releases/) — release notes per version, what the update banner shows.
- [docs/audits/](docs/audits/) — independent audits, sweeps and their responses.
- [docs/history/](docs/history/) — the previous [CLAUDE.md](docs/history/CLAUDE-2026-10-18.md) and
  [README.md](docs/history/README-2026-10-18.md): five days of design decisions, definitions and change notes.
- [docs/NL_SURGICAL_EDITOR_PLAN.md](docs/NL_SURGICAL_EDITOR_PLAN.md) — the design and phased plan of the
  natural-language editor; the current round's plan is `docs/PLAN-2026-10-18-live-runs.md`.
- [docs/RELIABILITY.md](docs/RELIABILITY.md) — the review flow, what PASS means and the remaining evidence limits;
  [docs/BUILTIN_WORKFLOWS.md](docs/BUILTIN_WORKFLOWS.md), [docs/OFFICE_PROFILES.md](docs/OFFICE_PROFILES.md),
  [docs/RECOVERY.md](docs/RECOVERY.md), [docs/TEXT_HYGIENE_AND_SPEC_QA.md](docs/TEXT_HYGIENE_AND_SPEC_QA.md) — the
  user-facing guides to those features; the `docs/BUILTIN_OPS_BATCH*_DESIGN.md` notes define every operation.
- [src/spec_surgeon/skill/SKILL.md](src/spec_surgeon/skill/SKILL.md) — the `docx-surgical-edit` method the engine
  implements; its verification checklist is the suite.
- [CLAUDE.md](CLAUDE.md) — the invariants and working notes for anyone changing the code.

## License

Spec Surgeon is free software under the [MIT License](LICENSE), Copyright © 2026 Abraham Borg. It comes as is,
without warranty of any kind, so check every delivered file yourself. The installed app bundles open-source
components under their own licenses; the About box lists them. Contact: abrahamborg@gmail.com ·
[GitHub](https://github.com/Abe-Borg/spec-surgeon) · [LinkedIn](https://www.linkedin.com/in/abrahamborg/).
