# Spec Surgeon

Fast, provably lossless surgical edits to Word (.docx) specification files — a few files or dozens — with a mandatory verification record per file.

Spec Surgeon is the engine form of the `docx-surgical-edit` method: the transform is a deterministic Python/lxml script, the verifier is an independent script that reads only the before/after packages, the expected result is predicted *before* the transform runs, and the output is never written back unless every check passes and the source has not moved. No language model is in the per-file loop; the model's jobs are to build new job modules, to advise on exceptions, and (optionally) to turn a natural-language request into a job call.

**Start here: the window.** Install it on Windows from [GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases) and open **Spec Surgeon** from the Start menu (or run `spec-surgeon-gui`). The window has four steps: **Files → Describe the change → Preview → Run & results**. Pick the specs folder, type what should change (or load a spec file), look at the preview, then run. Once installed, it updates itself: a banner offers each new version, and **Help → Check for updates…** checks at any time. Setup on Windows and a walkthrough: ["The window"](#the-window-gui--start-here); updates: ["Updates"](#updates).

**Status: v0.4.0 — a new Qt window, in-app updates and an installable Windows app; the mixed-quality batch workflow from v0.3.x is included.** v0.4.0 replaces the first-cut Tk window with a Qt 6 (PySide6) window. It has step navigation, preview and results tables, before/after page images in the app, light/dark themes and remembered settings. The API key can be kept in Windows Credential Manager. The installed app checks GitHub Releases for updates and installs them with one click, and v0.4.0 also ships the v0.3.1 audit fixes. The natural-language editor prototype is complete through plan Phase 6 (op library, templating, profiles and dry runs, the Claude compiler with preview and approval, Tier B generated transforms, the exception advisor), with the Phase 7 GUI, set-aside reports and retries, messy-document matching, and paragraph insertion/deletion. What remains needs your machine and your files: Word COM rendering, a read-only sweep over real project specs, and live runs of the Claude layer (all marked [unproven] below). The original v0.1.0 engine capabilities remain: two job modules, the nine-check verification suite (now extended through V10), batch orchestration, Word/LibreOffice rendering, geometry assertion, guarded in-place write-back, and the optional Claude layer.

**Packaged releases:** download the Windows installer, wheel, or source archive from [GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases). [v0.4.0 release notes](docs/releases/v0.4.0.md) (and [v0.3.0](docs/releases/v0.3.0.md)) include installation instructions. The source archive includes the worked examples, documentation, and complete test fixtures. Windows packaging scripts live in `packaging/windows/` in the repository and are included in subsequent source releases. Release tags (`v<version>`) trigger tests with Qt (offscreen)/LibreOffice and on Windows, package builds and metadata checks, a clean wheel installation, an installed Windows app smoke test (the Qt window's Preview → Run, an offline updater download-and-verify, and a real in-app update that must reopen the app), and publication of the distributions with SHA-256 checksums (the `SHA256SUMS` file the in-app updater checks every download against). Before tagging, keep `pyproject.toml` and `spec_surgeon.__version__` in sync and add `docs/releases/v<version>.md`; publication stops if any check fails.

**NL editor, Phase 1 (operation library foundation) — done.** The engine now has a composable operation library (`ops/`: `remove_header_footer`, `set_font`, `set_style_font`, `set_doc_defaults_font`, `set_paragraph_props`, `set_page_setup`, plus the two v0.1.0 jobs as `fix_page_numbering` and `replace_text`), hand-written **EditSpecs** executed end to end by `spec-surgeon run-spec`, a tenth check (**V10 — property assertions and residual identity**), and V1/V2/V4/V5/V7 extensions for member removal, text-scope union, every revision kind and layout-reference modes. The v0.1.0 jobs and CLI keep working unchanged (they are thin aliases now).

**NL editor, Phase 2 (header/footer templating) — done.** A new op, `apply_header_footer_template`, gives every section the headers/footers of a project template file: the template's parts are copied in as new parts, their placeholder **slots** (yellow highlight *and* `{{NAME}}` — decision §14.2: both) are filled by rule from each spec's own content (never typed), tab stops are rescaled to each section's text width, the template's look is **pinned** as direct formatting wherever the spec's styles would render it differently, missing styles are imported, and the old headers/footers go. The verifier re-derives all of it independently (`verify/template.py`, three new V10 kinds), V5a validates the added parts against the template's own baseline, V1 checks every part's content type against the relationship that points at it, and V7's `template` reference compares each output page's header and footer with the template's headers/footers rendered alone. 

**NL editor, Phase 3 (profiler, planner, catalog) — done.** `spec-surgeon profile` describes a batch per structure pattern (sections, what every header/footer shows, template slots, styles in use with their effective fonts, fonts, counts — a few KB per pattern, what the compiler will read); `spec-surgeon plan-spec` dry-runs an EditSpec over a folder (per-file plan summary or the exception it would raise; nothing written); `spec-surgeon ops --catalog` prints the machine-readable op catalog with the EditSpec JSON schema. 

**NL editor, Phase 4 (compiler, preview, `do`) — done.** `spec-surgeon do "make the header/footer look like this template" "C:\…\Specs" --template T.docx` compiles the request into an EditSpec with Claude (one tool-use conversation per batch; the model reads profiles and the op catalog, never XML), checks it (validation, *the words it would place must be the user's own*, a dry plan per structure pattern), shows a preview (per-pattern plan summaries, exceptions, sample files applied and verified in memory, before/after page images), and runs the verified batch only after approval. The live API path is **[unproven]** in the development container (no credentials there); the loop is tested with a scripted client.

**NL editor, Phase 5 (Tier B — generated transforms) — done, off by default.** When no op can express a request, `spec-surgeon do … --allow-codegen` lets Claude write one op module against a restricted API; it is AST-whitelisted, run in a separate isolated process on copies of one sample file per pattern, held to hard limits (formatting only), and must pass the full independent verification suite — failures go back to the model, at most three attempts. A passing op runs over the batch the same way (sandbox + verifier per file) and writes outputs only, never in place; the module is cached beside the run for a human to review and, if worth keeping, promote into `ops/` with tests.

**Template specs and font sizes (v0.4.0) — done.** Attach a **template spec** — an ordinary spec from the architect or any other firm, nothing highlighted, no `{{placeholders}}` — and ask "make the header and footer match the template spec": every discipline spec gets the architect's header and footer, with the architect's own section number and title replaced by each spec's own (`slot_markers: ["reference"]`: the fill rules run on the template spec itself to find its own values, then on each spec). Everything else in the architect's header and footer — firm, project, phase, logo, page number — is copied as it is, and the architect's number or title written any other way ("087100", a STYLEREF field) is refused rather than copied into every spec. Ask "match the font of the template spec": the three font ops now set **sizes** too (`size`, `size_from`, and `only_where_defined` on `set_font`), so 9 pt text becomes the template's 10 pt Arial while 8 pt headers and a 12 pt note keep their size, and the `effective_font` post-condition proves it run by run. With it come the window's *Template spec* picker and two example chips, a template-spec summary for the compiler (`read_template`: the template spec's own number and title, where its headers/footers show them, its body font and size), two worked examples and an example architect's spec. Details: ["Template specs: match the architect's spec"](#template-specs-match-the-architects-spec) and ["Font sizes"](#font-sizes-match-the-template-specs-font).

---

## Contents

1. [How it works](#how-it-works)
2. [Install](#install) / [The window (GUI) — start here](#the-window-gui--start-here) / [Updates](#updates)
3. [Quick start](#quick-start)
4. [Commands](#commands)
5. [Jobs](#jobs)
6. [Operations and EditSpecs (NL editor, Phase 1)](#operations-and-editspecs-nl-editor-phase-1) — incl. [Header/footer templating (Phase 2)](#headerfooter-templating-phase-2), [Template specs: match the architect's spec](#template-specs-match-the-architects-spec) and [Font sizes](#font-sizes-match-the-template-specs-font)
7. [The verification record](#the-verification-record)
8. [Output folder layout](#output-folder-layout)
9. [Performance](#performance)
10. [Exceptions and the agent layer (Tier 2/3)](#exceptions-and-the-agent-layer-tier-23)
11. [Renderers](#renderers)
12. [The overwrite guard](#the-overwrite-guard)
13. [Library API (for a GUI)](#library-api-for-a-gui)
14. [Adding a job module](#adding-a-job-module) / [Adding an op](#adding-an-op)
15. [Project layout](#project-layout)
16. [Tests](#tests)
17. [Known limits of the prototype](#known-limits-of-the-prototype)
18. [Roadmap — natural-language editing](#roadmap--natural-language-editing)
19. [Relationship to the docx-surgical-edit skill](#relationship-to-the-docx-surgical-edit-skill)

---

## How it works

Every file goes through the same pipeline, whether the batch is one file or a hundred:

```
acquire ─► fingerprint ─► plan ─► apply ─► verify ─► render ─► geometry ─► deliver ─► record
 (identity)  (ms/file)   (predict  (lxml)   (V1–V5,   (Word /   (V7)       (guarded,   (per-file
                          first)             V8)       LO)                  V9)         .md/.json)
```

Three tiers of who does the work:

| Tier | Who | When |
|---|---|---|
| 1 — deterministic engine | this package, no model | every file whose structure the job module understands (the common case) |
| 2 — exceptions | a human, or `spec-surgeon agent` for a written recommendation | a file the job refuses to guess about (`exceptions/<name>.exception.json`) |
| 3 — new job types | you + Claude, using the bundled skill | a request no job module handles yet; the result is a new module in `jobs/` |

Design rules carried from the method, enforced in code:

* **Predict first.** `Job.plan()` computes, from the source alone, the exact text diffs per block, the parts that may change, and the revision-count delta. The verifier asserts that prediction; it never derives "expected" from what happened.
* **Independent verifier.** `spec_surgeon/verify/` imports nothing from `jobs/`, `ops/`, `editspec.py`, `ooxml.py` or `package.py` (a test enforces it). It has its own zip reader, rels resolution, text extraction, selector resolution, style-chain and theme lookup, so a transform bug cannot hide behind the same bug in the check.
* **No generated characters.** Formatting jobs add zero text. Content jobs move the user's approved wording (verbatim) into the right place in the right tracked-change grammar.
* **Repack by substitution.** The output zip has the source's member order, compression, and bytes for every member not deliberately changed. Inert junk (`[trash]/*.dat`, duplicate ids) is preserved byte-identically.
* **Never write back blind.** In-place delivery re-checks the source's size/mtime, looks for Word's `~$` lock file, writes a temp file beside the source and `os.replace`s it. Never forces.

---

## Install

**Windows 10/11 (64-bit):** download `SpecSurgeon-<version>-Setup-x64.exe` from [GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases), run it, then launch **Spec Surgeon** from the Start menu. The installer includes Python, Qt (the window), the editing engine, Word integration, and the Anthropic client; no separate Python installation or terminal commands are needed. From then on the app keeps itself up to date (see [Updates](#updates)); installing a newer setup EXE by hand over an older one also works and keeps your settings and results. It installs for the current user without administrator access, offers a desktop shortcut, and includes an uninstaller. Example EditSpecs are available from **Spec Surgeon → Example edit specs** in the Start menu. User documents and result folders are kept when the app is uninstalled.

Microsoft Word or LibreOffice is still needed for page rendering; neither is installed by this setup program. Typed requests still need your Anthropic API key. Enter it in the window's **Settings**; tick *Remember on this computer* to keep it in Windows Credential Manager. Saved EditSpecs run without one. The installer is unsigned, so Windows may show a publisher/SmartScreen prompt on the first manual install. Updates installed from inside the app do not trigger that prompt; see [Updates](#updates) for what is checked instead.

**Python package / development setup:**

Requires Python 3.10+ (Windows, macOS, Linux). From the project folder:

```powershell
py -3 -m pip install -e .            # core: lxml, pymupdf
py -3 -m pip install -e ".[gui]"     # the window: PySide6 (Qt 6) — spec-surgeon-gui
py -3 -m pip install -e ".[word]"    # Windows: adds pywin32 so Word is the renderer/oracle (recommended)
py -3 -m pip install -e ".[agent]"   # optional: anthropic SDK for `ask` / `agent`
py -3 -m pip install -e ".[dev]"     # pytest + python-docx (fixtures)
```

or `py -3 -m pip install -r requirements.txt` for everything.

Rendering needs one of: Microsoft Word (Windows, with `pywin32`) or LibreOffice (any platform; set `SPEC_SURGEON_SOFFICE` if it is not on `PATH` or in the default install location). Without either, the engine still runs every XML check; V6/V7 are recorded as N/A with the reason.

Environment variables:

| Variable | Purpose |
|---|---|
| `SPEC_SURGEON_AUTHOR` | default tracked-change author (else the OS user name) |
| `SPEC_SURGEON_SOFFICE` | path to `soffice` / `soffice.exe` |
| `ANTHROPIC_API_KEY` | for typed requests (`do`, the window), `advise`, `ask` / `agent` — or enter it in the window's Settings (for one session, or remembered in Windows Credential Manager; a key set here wins) |
| `SPEC_SURGEON_MODEL` | model for `do` (the compiler), `ask` / `agent` (default `claude-opus-5-5`) |
| `SPEC_SURGEON_NO_UPDATE_CHECK` | any value: the window never checks for updates on its own (Help → Check for updates… still works) |
| `SPEC_SURGEON_CONFIG_DIR` | where the window keeps `settings.json`, downloaded updates and its error log (default `%APPDATA%\Spec Surgeon` and `%LOCALAPPDATA%\Spec Surgeon`); the tests use it |
| `SPEC_SURGEON_QT_STYLE` | force a Qt style for the window (e.g. `windows11`, `fusion`) — diagnostics only |

---

## The window (GUI) — start here

![The Spec Surgeon window after a preview](docs/gui.png)

**Set up once (Windows installer):** run the setup EXE from [GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases), then open **Spec Surgeon** from the Start menu. For typed requests, enter your Anthropic API key in **Settings** (the *Set API key…* link on the *Describe the change* step opens it). To work without an API key, choose **Load saved spec…** and load an example from the Start menu's **Example edit specs** folder. After that the app updates itself (see [Updates](#updates)).

**Alternative: set up from source (Windows):**

1. Install Python 3.12 or newer from python.org (tick "Add python.exe to PATH").
2. Get this repository onto your machine (clone it, or download the ZIP from GitHub and unzip it), open a terminal in its folder and run:
   ```powershell
   py -3 -m pip install -e ".[gui,word,agent]"
   ```
   (`gui` is the window — Qt 6 via PySide6; `word` lets Word render the before/after pages; `agent` is the Claude SDK for typed requests.)
3. For typed requests, set your Anthropic API key once (then open a new terminal), or enter it in the window's **Settings** (for the session, or remembered in Windows Credential Manager):
   ```powershell
   setx ANTHROPIC_API_KEY "sk-ant-…"
   ```
4. Start it: press Win+R, type `spec-surgeon-gui`, Enter (or run `spec-surgeon gui` in a terminal). To pin it, make a desktop shortcut to `spec-surgeon-gui.exe` in Python's `Scripts` folder (`py -3 -c "import sys,os; print(os.path.join(sys.prefix,'Scripts'))"` prints where it is). A copy installed this way is not updated in-app: the update banner tells you the `pip install -U …` command instead.

**Use it** — four steps down the left side; each shows a check mark when it is done, and you can click any of them:

1. **Files**
   - **Specs folder:** the folder with the .docx files. Work on a *copy* of a project folder the first few times. Drop a folder onto the field or use **Browse…**. The step counts the Word files it will process and lists them; the template file and Word's `~$` lock files are left out.
   - **Template spec (optional):** a spec from the architect or another firm (an ordinary spec — nothing to prepare), or a prepared header/footer template with highlighted / `{{NAME}}` placeholders. It is for requests like "make the header and footer match the template spec", "match the font of the template spec" or "make the header/footer look like this template". It is left out of the batch even when it sits in the specs folder (same path, or a copy with the same bytes).
   - **Results folder:** where previews, edited copies, records and reports land. The default is `%USERPROFILE%\SpecSurgeon_out\<timestamp>`, and each preview gets its own time-stamped folder. The folders are remembered for next time.
2. **Describe the change**
   - Type the request in plain English ("remove all headers and footers", "change all fonts to Arial", "make the header and footer match the template spec", "match the font of the template spec", "make the header/footer look like this template"), or click one of the example chips. A chip that mentions the template spec reminds you to choose one on *Files* when none is set.
   - No API key? Use **Load saved spec…** with one of `examples\specs\*.json` instead, or a spec saved from an earlier preview's `preview\spec.json`. A loaded spec shows as a strip under the request. Typing a request instead compiles a new spec, and **Remove** drops the loaded one.
   - Options: *Render pages (Word or LibreOffice)* makes before/after page images and checks layout (slower). *Allow AI-written transforms* is off by default and explained below.
   - A line at the bottom says where the API key comes from: your Windows environment, saved on this computer, this session only, or none.
   - Press **Preview** (or Ctrl+Enter).
3. **Preview**
   - Claude turns the request into a spec. The engine plans every file (nothing is written to your files), applies the spec to one or two sample files in memory and verifies them. With *Render pages* it also makes before/after page images.
   - The page shows:
     - tiles counting the files, the files that plan cleanly, the files set aside, and the structure patterns;
     - the spec's **assumptions to check** (and renderer warnings);
     - a table of **every file**: its pattern, *Planned* or *Exception*, and what changes or why it is refused (plain English; the tooltip has the engine's diagnosis). *Show exceptions only* filters it;
     - each **sample file** with its verification result and before/after pages (click a page to zoom: Fit, 100 %, Ctrl + mouse wheel);
     - the approval token.
   - If Claude needs something from you (e.g. the exact stamp wording), an **Answers** box appears on *Describe the change* with the questions. Type one answer per line and press Preview again.
   - **Open preview folder** shows the preview files (`PREVIEW.md`, `spec.json`, samples, images).
4. **Run & results**
   - By default the edited files go to the results folder and **your files are not touched**. Choose **Write verified results back over the originals (guarded)** to have verified files written back: only files that pass every check, never a file open in Word or changed since the preview. You confirm before it starts, and the choice is never remembered.
   - **Run** is enabled only while the preview is current. Changing the folder, the template, the request or the answers after a preview disables it ("press Preview again"). Run itself still refuses if a file — or the template spec — changed on disk since the preview (the approval token).
   - A progress bar counts the files as they are verified.
   - Then:
     - **PASS / FAIL / EXCEPTION tiles** and a **results table**, with *Show problems only*. Double-click a file to open its edited copy, or its verification record for a failure.
     - **Open results folder** has `BATCH_REPORT.md`, `outputs\`, `records\` (one verification record per file), `failed\` and `render\`.
     - **View report** shows `BATCH_REPORT.md` in the app; **Open failed/ folder** appears when something failed.
     - **Load set-aside files** narrows the next pass to the files that were set aside or failed, with their failure reasons sent to Claude.
     - **Advise on exceptions:** for files the engine refused, Claude writes a recommendation or a corrected spec into `advice\`.

![The results step after a run](docs/gui-results.png)

*Allow AI-written transforms* (off by default, and off again at every start): when no built-in operation can do the request, Claude may write a one-off transform. It is tested on sample files first and only ever writes edited copies to the results folder; *Write back over the originals* is unavailable for it.

**Also in the window:**
- **Errors** appear on the step that caused them, in plain words. The **Activity log**, collapsed at the bottom, holds everything the engine printed (Copy, Save…, Clear). Unexpected errors also go to `%LOCALAPPDATA%\Spec Surgeon\logs\spec-surgeon.log`.
- **Menus:** **File** has Load saved spec… (Ctrl+O), Open results folder, Settings… (Ctrl+,) and Exit. **Help** has Check for updates…, What's new in this version (the bundled release notes), User guide, Report a problem and About (versions and folders).
- **Looks:** light or dark follows Windows, or choose it in Settings; the accent colour is your Windows accent; it is sharp on high-DPI screens. The app's own Windows 11-style theme is the same on Windows 10 and 11. *Settings → Use Windows' own controls* switches to Qt's native Windows 11 style instead (Windows 11 only).

**Settings** (sidebar or File → Settings…):
- **Appearance:** colours (follow Windows, light, dark) and Windows' own controls.
- **Results:** the default results folder.
- **Anthropic API key:** typed into a masked field. *Remember on this computer* saves it in Windows Credential Manager (target `Spec Surgeon/ANTHROPIC_API_KEY`); *Forget saved key* removes it. A key already set in the Windows environment (`setx`) takes precedence.
  The installer includes the Windows time-zone support that Credential Manager needs when reading the saved key.
- **Updates:** check automatically once a day, the last check, the skipped version (with Clear), and Check now.

**What it remembers, and where:** the window keeps `%APPDATA%\Spec Surgeon\settings.json` (plain JSON you can read or delete). It holds the folders, the template, the render option, the last request, the theme, the window size, the update preferences and the update in progress. It never stores the API key, and never stores the two risky switches (write back over the originals, AI-written transforms); both are off at every start. A corrupt settings file is set aside as `settings.json.bad` and the defaults are used.

The window is a thin layer over the library calls (`gui.Session`): everything it does is what `do`, `run-spec` and `advise` do on the command line.

---

## Updates

The installed app keeps itself up to date from this repository's [GitHub Releases](https://github.com/Abe-Borg/spec-surgeon/releases).

**How it works:**

1. **Check.** About four seconds after the window opens, and at most once a day, it asks GitHub for the newest published release. Drafts and pre-releases are never offered. **Help → Check for updates…** (or the sidebar's *Check for updates*) checks at any time and always reports: "you're up to date", the update banner, or why it couldn't check.
2. **Offer.** A banner above the page shows the new version with **What's new** (the release notes), **Install now**, **Later** (until the next start) and **Skip this version** (no more automatic offers for that version; a manual check still shows it).
3. **Download.** *Install now* is unavailable while a Preview or Run is in progress, and refuses while another Spec Surgeon window or `spec-surgeon.exe` command is open. It downloads `SpecSurgeon-<version>-Setup-x64.exe` into `%LOCALAPPDATA%\Spec Surgeon\updates\`, with a progress bar and Cancel.
4. **Install.** The window starts Setup detached (`/SILENT … /RELAUNCH=1`) and closes. Setup shows its progress window, waits for every Spec Surgeon process to exit (up to 60 s, using the app's named mutex), installs over the same folder without administrator rights, and reopens Spec Surgeon.
5. **Afterwards.** The next start says "Spec Surgeon was updated to X" (with What's new), or "The update to X didn't finish" with **Try again** and **Open install log** (`%LOCALAPPDATA%\Spec Surgeon\updates\install-<version>.log`). Old downloads are cleaned up.

**What is checked before anything runs:**
- The installer's name must be exactly `SpecSurgeon-<version>-Setup-x64.exe` for the release's version.
- Its SHA-256 must be listed for that exact name in the release's `SHA256SUMS`, which is fetched first: no installer byte is downloaded without a known hash.
- When GitHub reports its own checksum for the asset, the two must agree.
- The size must match.
- Anything else deletes the download and shows the reason.
- The file is hashed again immediately before Setup starts. Only HTTPS is used, redirects included.

**What those checks do and don't protect against.** They catch corruption, truncation and a mangled transfer. They do **not** protect against someone taking over the GitHub account or the release workflow, because the checksums come from the same release. Signing the installer (Authenticode) closes that gap; the updater has a `verify_signature` hook for it, and the installer is unsigned today. Files the app downloads carry no "downloaded from the internet" mark, so Windows SmartScreen does not prompt for them; the checks above are the gate.

**What the check sends:** one HTTPS request to `api.github.com` with the app's version in the User-Agent. Nothing about your files, projects or key is sent. GitHub allows 60 such requests an hour per network address; if an office shares one address and hits that limit, the app reads the newest version from the `github.com/…/releases/latest` redirect instead.

**Networks:** the app uses the Windows certificate store, so a company's HTTPS-inspection certificate that Windows trusts works. Proxies set in Windows (or `HTTPS_PROXY`) are used; automatic proxy scripts (PAC/WPAD) are not evaluated. Behind one, the check fails with "Couldn't reach GitHub" and a link to the releases page, where the installer can be downloaded by hand.

**Turning it off:** Settings → Updates → *Check for updates automatically*, or set `SPEC_SURGEON_NO_UPDATE_CHECK=1`. Manual checks still work.

**Copies installed with pip** (from source) are not updated in-app. The banner's Install now shows the `"<this copy's python>" -m pip install -U "spec-surgeon[gui,word,agent] @ <wheel URL>"` command and the release page. The command names the interpreter the window is running in, so it updates the same environment on Windows, macOS or Linux (`pythonw.exe` becomes `python.exe`).

**For the maintainer — publishing an update:** bump `pyproject.toml` and `spec_surgeon.__version__`, add `docs/releases/v<version>.md` (it becomes the banner's What's new), and push the tag `v<version>`. The release workflow tests, builds the installer, runs the installed-app checks (including a real in-app update that must reopen the app), and publishes the installer with `SHA256SUMS` as the latest release. Installed copies see it within a day. v0.3.x has no updater: users on it install v0.4.0 by hand once.

---

## Quick start

```powershell
# what jobs exist and what they take
spec-surgeon jobs

# milliseconds per file: how many structure patterns are in this folder?
spec-surgeon fingerprint "C:\Projects\AVC11\Specs"

# dry run — the predicted diff list per file, nothing written
spec-surgeon plan page-numbering "C:\Projects\AVC11\Specs" --params @examples\jobs\page-numbering-cover-unnumbered.json

# the real thing: outputs + records + renders into an output folder (sources untouched)
spec-surgeon run page-numbering "C:\Projects\AVC11\Specs" --params '{"option":"cover-unnumbered"}' --out C:\Temp\ss-run1

# a tracked wording change in body + headers + footers, written back IN PLACE (guarded)
spec-surgeon run tracked-replace "C:\Projects\AVC11\Specs\21*.docx" --params @examples\jobs\tracked-replace-grc.json --in-place
```

Inputs may be files, folders (all `*.docx`, lock files skipped), or globs, mixed. File names with en-dashes and doubled spaces are fine — the engine never retypes a name.

EditSpecs (the NL editor's programs; Phase 1 runs hand-written ones):

```powershell
# what operations exist, their selectors, params, and which checks they feed
spec-surgeon ops

# "change all font to Arial" on a folder — outputs + records into an output folder, sources untouched
spec-surgeon run-spec examples\specs\change-all-font-arial.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-fonts

# "remove all headers/footers", written back in place (guarded), no render
spec-surgeon run-spec examples\specs\remove-all-headers-footers.json "C:\Projects\AVC11\Specs\21*.docx" --in-place --no-render

# "make the header/footer look like this template" (edit the spec's template path and slot_fills first);
# the template file itself is skipped if it sits in the folder being processed
spec-surgeon run-spec examples\specs\apply-header-footer-template.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-template

# "make the header and footer match the template spec": the architect's spec as the template (point the spec's
# "template" at your architect's spec; the example uses examples\templates\08 71 00 - DOOR HARDWARE (ACME).docx)
spec-surgeon run-spec examples\specs\match-template-spec-header-footer.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-arch

# "match the font of the template spec" (Arial 10 for the 9 pt text; edit font/size/size_from to your template and specs)
spec-surgeon run-spec examples\specs\match-template-spec-font.json "C:\Projects\AVC11\Specs" --out C:\Temp\ss-arch-font
```

Plain English (Phase 4; needs `pip install -e ".[agent]"` and `ANTHROPIC_API_KEY` or an `ant auth login` profile):

```powershell
# compile → preview → asks "Run this on N file(s)? [y/N]" → the verified batch
spec-surgeon do "remove all headers and footers" "C:\Projects\AVC11\Specs" --out C:\Temp\ss-do

# templating; the compiler asks for anything it would have to guess (e.g. the stamp wording) — answer and re-run
spec-surgeon do "make the header/footer look like this template" "C:\Projects\AVC11\Specs" --template "C:\Projects\AVC11\00 00 00 TEMPLATE.docx"
spec-surgeon do "make the header/footer look like this template" "C:\Projects\AVC11\Specs" --template "…" --answer "stamp: ISSUED FOR CONSTRUCTION  2026-02-15"

# the architect's spec as the template spec: their header/footer with each spec's own number and title; their font
spec-surgeon do "make the header and footer match the template spec" "C:\Projects\AVC11\Specs" --template "C:\Projects\AVC11\Architect\08 71 00 - Door Hardware.docx"
spec-surgeon do "match the font of the template spec" "C:\Projects\AVC11\Specs" --template "C:\Projects\AVC11\Architect\08 71 00 - Door Hardware.docx"

# run later exactly what the preview showed (refused if the spec or any file changed since)
spec-surgeon run-spec C:\Temp\ss-do\preview\spec.json "C:\Projects\AVC11\Specs" --approval 3f9a0c1d2e4b5a69
```

---

## Commands

| Command | What it does |
|---|---|
| `spec-surgeon jobs` | list job modules and their parameters |
| `spec-surgeon fingerprint FILES… [--json]` | structure fingerprint per file + pattern grouping (ms/file) |
| `spec-surgeon plan JOB FILES… --params JSON\|@file [--json]` | dry run: predicted diff list, revision delta, notes, flags, exceptions; no outputs |
| `spec-surgeon run JOB FILES… --params … [--out DIR] [--in-place] [--workers N] [--renderer auto\|word\|libreoffice\|none] [--no-render]` | the pipeline; exit code 0 only if every file passed |
| `spec-surgeon ops [--catalog]` | list the operation library: each op's selector, params, and how it feeds the checks; `--catalog` prints the machine-readable catalog (ops, selector grammar, common assumptions, the EditSpec JSON schema, the worked examples) |
| `spec-surgeon profile FILES… [--json]` | document profile per structure pattern: sections (page, margins, text width, titlePg, numbering), what each header/footer shows (part, text, fields, template slots, drawings, pending revisions), styles in use with effective font/size and a sample, fonts (theme, docDefaults, direct fonts in runs), counts — bounded to ~6 KB per pattern |
| `spec-surgeon plan-spec SPEC.json FILES… [--json] [--per-file]` | dry run of an EditSpec: every file is fingerprinted and planned (predict first), nothing is applied or written; prints per pattern a plan summary (members removed/added, text changes, revision deltas, assertions, mode, layout reference, flags) or the exception the real run would raise. Exit 0 when every file plans, 1 when some raise |
| `spec-surgeon run-spec SPEC.json FILES… [--out DIR] [--in-place] [--workers N] [--renderer …] [--no-render]` | execute an EditSpec: plan every op on every file (predict first), merge with conflict detection, apply, verify (V1–V10), render, deliver. Refuses (exit 2) a spec with open `questions`, `unsupported`, unknown keys or ops; exit 0 only if every file passed |
| `spec-surgeon verify SOURCE OUTPUT --plan plan.json` | re-run the suite on any source/output pair (e.g. after a manual fix) |
| `spec-surgeon do "request" FILES… [--template T] [--answer A]… [--out DIR] [--yes] [--in-place] [--no-render] [--model M]` | natural language end to end: compile (Claude) → preview (`OUT/preview/`: `PREVIEW.md`, `spec.json`, sample outputs, before/after images) → approval (`[y/N]` prompt, `--yes`, or later with `run-spec --approval TOKEN`) → the verified batch in `OUT/run/`. Exit 2 when the compiler asks questions (re-run with `--answer`), says unsupported, or fails |
| `spec-surgeon run-spec … --approval TOKEN` | run only if the spec and every file are exactly as they were when the preview computed TOKEN |
| `spec-surgeon do … --allow-codegen` | Tier B: when the compiler answers *unsupported*, generate a transform, prove it on samples, and (after approval) run it over the batch — outputs only, never in place |
| `spec-surgeon gui` / `spec-surgeon-gui` | the window (see "The window"; needs the `gui` extra, which the Windows installer includes — without it the command says how to install it); `spec-surgeon-gui` opens without a console window on Windows |
| `spec-surgeon advise OUT_DIR [--file NAME] [--model M]` | for every exception a batch wrote (`OUT_DIR/exceptions/`): Claude reads the diagnosis and the file and answers with a recommendation for a human or a re-compiled spec for that file's structure pattern → `OUT_DIR/advice/<name>.advice.md` (+ `.spec.json`). Nothing is applied |
| `spec-surgeon ask "…"` | natural-language request → job + params JSON to confirm (needs API key) |
| `spec-surgeon agent EXCEPTION.json` | written recommendation for one exception, grounded in the skill (needs API key) |

`--params` takes inline JSON or `@path\to\file.json`. On PowerShell, inline JSON is easiest with single quotes outside and double quotes inside: `--params '{"option":"continuous"}'`.

---

## Jobs

### `page-numbering` (formatting mode)

Fixes the recurring cover/body symptom (cover shows `21 05 00 - 1`, first body page also `- 1`).

| param | values | effect |
|---|---|---|
| `option` | `cover-unnumbered` | delete the paragraph carrying the PAGE field from the cover section's own footer part (body footer untouched; body still starts at 1). If that paragraph is the footer's only one, its runs are removed and the empty paragraph kept (schema needs one). |
| | `continuous` | remove `<w:pgNumType w:start>` from the first body section (element removed if `start` was its only attribute, else just the attribute). Zero text diffs. |

Exceptions: single-section document; cover has no footer of its own; the cover footer part is shared with a body section; no PAGE field; more than one PAGE paragraph. The plan flags any tracked changes that would disappear with the deleted paragraph.

### `tracked-replace` (content mode; or untracked on request)

A surgical wording change as a tracked revision.

| param | default | notes |
|---|---|---|
| `find` | required | exact live text (what a reader sees with changes accepted; never matches inside pending deletions, field instructions, or cached field results) |
| `replace` | required | the approved wording, verbatim; `""` = pure deletion |
| `scope` | `["document"]` | any of `document, headers, footers, footnotes, endnotes, comments` — parts resolved via rels |
| `occurrences` | `all` | or `first` |
| `match_case` | `true` | |
| `mode` | `tracked` | `untracked` writes plain text (no revision marks); V5b switches to enumerated-list mode and the record says so |
| `author` | env / OS user | tracked-change author |
| `allow_missing` | `false` | if true, a file with no occurrence is a no-op instead of an exception |

Grammar implemented (all from the paid-for lessons):

* plain text → `<w:del>old</w:del><w:ins>new</w:ins>`, deletions before insertions, rPr and run attributes copied onto every fragment, `xml:space="preserve"` wherever edge whitespace exists, non-text run children (tabs, breaks) carried into the right fragment;
* text inside someone's pending insertion → the deletion is nested **inside** that insertion and the insertion is split around it (`<w:ins ORIG>prefix <w:del NEW>old</w:del></w:ins><w:ins NEW>new</w:ins><w:ins ORIG-author, new id>suffix</w:ins>`), which is what Word writes itself — accept-all gives the new wording, reject-all returns to the pre-insertion original;
* a target split across identically formatted runs → merged within that paragraph only (document stays byte-identical elsewhere); split across differently formatted runs → exception;
* revision ids continue from the maximum across all parts; `w:date` follows the file's own convention (Word's local-time-with-Z, plus `w16du:dateUtc` when the file uses it).

Predicted revision delta per occurrence: plain `+1 ins / +1 del` (pure deletion `0 / +1`); inside an insertion `+1 del`, `+1 ins` for the new text, `+1 ins` for the re-wrapped suffix when anything of the original insertion follows the target.

Since Phase 1 both jobs are thin aliases (`jobs/`) over the ops `fix_page_numbering` and `replace_text` (`ops/`); the CLI, `run_batch(job_name, params)` and the library API are unchanged. Two behaviours tightened on the way, both found by the stricter verifier:

* a **tracked** replacement in a run that carries a pending tracked *formatting* change (`w:rPrChange`) is now an exception (code `tracked-format`): splitting the run would have copied that revision, with the same `w:id`, onto every fragment. Untracked replacements there are fine (the `w:t` is edited in place);
* `fix_page_numbering` (`continuous`) states its sectPr change as a V10 `section_props` assertion, and the cover-footer deletion predicts every revision kind inside the deleted content, not just `w:ins`/`w:del`.

---

## Operations and EditSpecs (NL editor, Phase 1)

The natural-language editor (design: `docs/NL_SURGICAL_EDITOR_PLAN.md`) compiles a request into an **EditSpec** — a JSON program over a library of deterministic, individually verified **operations**. Phase 1 builds the library and runs hand-written specs; the compiler arrives in Phase 4.

### How an EditSpec runs (per file)

```
validate spec ─► plan every op on the SOURCE ─► merge fragments ─► apply in spec order ─► verify V1–V10 ─► render ─► deliver
 (questions?      (each op: PlanFragment —        (conflicts →       (working copy:          (one merged
  unknown keys?)   diffs, parts, members,          exception;         each op sees the        plan)
                   assertions, rev delta)          revision ids)      previous op's result)
```

* **Op interface** (`ops/base.py`): `plan(pkg, select, params) → PlanFragment` (every Plan field plus `members_removed`, `members_added`, `property_assertions`, `touches`, revision ids needed) and `apply(work, fragment)`, which mutates a working copy of the package (`DocxPackage.copy()`) through its overlay API.
* **Merge** (`merge_fragments`): unions intended parts, member changes, assertions; sums revision deltas; hands each op a disjoint range of revision ids; decides the merged mode and the geometry reference; **refuses conflicts** — the same block's text touched by two ops, the same property family of the same target touched twice, or anything touched in a member another op removes/restructures (code `conflict`). The engine never guesses an order for overlapping edits; split such requests into two specs. Since the 2026-10-15 sweep the font and paragraph ops also declare a `verify` touch on every part in their scope (V10 re-resolves their selection there even where they change nothing), so "remove all headers and footers and change all fonts to Arial" with the font change scoped over the headers is a plan-time `conflict` naming the op to narrow — not a clean plan that fails V10 after the fact. Two ops may verify the same part; only removing or restructuring it conflicts.
* **Mode**: `formatting` unless an op writes characters; tracked content ops cannot be combined with untracked text edits in surviving parts (V5b's reject-view check would see them as violations).

### The operations

| Op | Mode | Select | Params | What it does | Predicted / asserted |
|---|---|---|---|---|---|
| `remove_header_footer` | formatting | `sections` (`all` \| [i]), `kinds` (header, footer), `types` (default, first, even) | — | removes the selected `headerReference`/`footerReference` elements, then every part no longer referenced: the part, its `.rels`, its content-type override, its relationship in `document.xml.rels`, and media nothing else uses | `members_removed` exact; every block of a removed part a predicted deletion; revisions inside removed parts as negative deltas, **flagged by name**; `section_hf_refs` assertion; V7 against the source render (body must not move) |
| `set_font` | formatting | `scope`, `runs` (`all` \| `{has_direct_font, font_in, not_in_revisions}`), `paragraphs` (predicates) | `font` (ascii+hAnsi+cs) or per-slot `ascii`/`hAnsi`/`cs`/`eastAsia`; `size` (pt), `size_from` (pt list); `only_where_defined` (false); `clear_theme_fonts` (true); `preserve_fonts`; `skip_sym_runs` (true); `in_revisions` (include \| exclude); `include_paragraph_marks` (true); `tracked`; `author` | sets `w:rFonts` on the selected runs and paragraph marks (only on those with a direct font when `only_where_defined`) and `w:sz` on those that set their own size ([Font sizes](#font-sizes-match-the-template-specs-font)); never touches **symbol runs** (see below) | `run_fonts` assertion with the predicted run/mark/sized counts; tracked: `+n w:rPrChange` |
| `set_style_font` | formatting | `styles`: `all` \| `all-paragraph-and-character` \| `{ids}` \| `{types}` | font params; `size`, `size_from`; `only_where_defined` (true) | sets `w:rFonts` and/or `w:sz` in style definitions — by default each only where the style already defines it (others inherit); symbol styles left alone | `style_fonts` assertion with the exact style-id list and the styles that get the size; `styles.xml` only |
| `set_doc_defaults_font` | formatting | — | font params; `size`, `size_from` | sets `docDefaults/rPrDefault/rPr/rFonts` and/or `w:sz` (created in schema order when absent; with `size_from` the size only when its own size is listed) | `doc_defaults_fonts` assertion |
| `set_paragraph_props` | formatting | `scope`, `paragraphs` | `spacing` {before, after, line, lineRule, beforeLines, afterLines}, `ind` {left, right, start, end, firstLine, hanging}, `jc`; `tracked`; `author` | merges values into existing `w:spacing`/`w:ind` (a style's `before="240"` survives), complete pPr schema order (section-break paragraphs keep their `w:sectPr` last); `firstLine` and `hanging` replace each other | `paragraph_props` assertion; tracked: `+n w:pPrChange` with the old pPr as snapshot |
| `set_page_setup` | formatting | `sections` | `margins` {top, right, bottom, left, header, footer, gutter} (twips), `size` {w, h}, `orientation` | merges into `w:pgMar` / `w:pgSz`; orientation sets `w:orient` and swaps w/h when they disagree (margins are not rotated) | one `section_props` assertion per section |
| `replace_text` | content \| untracked | `scope` | as the `tracked-replace` job (+ `tracked` bool) | v0.1.0 tracked-replace | as today |
| `fix_page_numbering` | formatting | — | `option` | v0.1.0 page-numbering | as today + `section_props` for `continuous` |
| `apply_header_footer_template` | formatting, or untracked with a `literal` fill | `sections: "all"` only | `template`, `template_sections`, `slot_fills`; `kinds`, `slot_markers` (`highlight`/`braces`, or `["reference"]` for a [template spec](#template-specs-match-the-architects-spec)), `highlight_colors`, `rescale_tabs`, `align_header_footer_distance`, `align_title_page`, `import_missing_styles` (see below) | copies the template's header/footer parts in as new parts (+ rels, media), fills their slots from the document's own content, rescales tabs, pins the template's look, imports missing styles, removes the old parts | `members_removed`/`members_added` exact; old parts' blocks predicted deletions, new parts' blocks predicted insertions with the filled text; `hf_template_refs`, one `template_parts` per copy, `styles_added`, `section_props` for aligned distances; V5a against the template's baseline; V7 against the template render |

Exceptions the new ops raise (never a guess): `inheritance` (a removal would leave a selected section still *showing* a header/footer through inheritance from an earlier section, or would change what an unselected section shows — an empty replacement part would be needed and the op never creates one; first-page parts count only where `titlePg` is on, even-page parts only with `evenAndOddHeaders`, and an invisible inheritance change is flagged instead), `structure` (a header part carrying something other than images/hyperlinks; a section without `w:pgMar` when margins are set; no styles part), `tracked-format` (a tracked change on a run/paragraph that already carries someone's `w:rPrChange`/`w:pPrChange`, or on a paragraph mark that is itself a pending revision), `theme-font` (explicit font with `clear_theme_fonts: false` where a theme attribute would override it), `conflict-property` (spacing before/after on an "auto" paragraph; `left` on an indent written with `start`), `not-found`, `bad-params`, `conflict`.

**Tracked or plain** (plan §14.1, as assumed for Phase 1 — see the plan): whole-document formatting is applied plain; formatting that targets particular paragraphs (a `paragraphs` predicate in the selector) is tracked by default (`w:rPrChange` / `w:pPrChange` holding a snapshot of the old properties); an explicit `tracked` always wins and the record says which rule applied. Style definitions, docDefaults and page setup are never tracked (`tracked: true` is refused for them — Word does not track style-definition changes, and `w:sectPrChange` is not implemented).

### Header/footer templating (Phase 2)

`apply_header_footer_template` is the skill's header-footer-templating playbook as an op. One spec, one template file, every spec in the folder:

```json
{"op": "apply_header_footer_template", "select": {"sections": "all"},
 "params": {
   "template": "../templates/00_00_00_TEMPLATE.docx",
   "template_sections": {"cover": 0, "body": 1},
   "slot_fills": {
     "XX XX XX":          {"rule": "regex", "source": "footer.default", "pattern": "^(\\d{2} \\d{2} \\d{2})"},
     "SECTION_NUMBER":    {"rule": "regex", "source": "footer.default", "pattern": "^(\\d{2} \\d{2} \\d{2})"},
     "SECTION_TITLE":     {"rule": "paragraph_after", "anchor_regex": "^SECTION \\d{2} \\d{2} \\d{2}$"},
     "ISSUED FOR PERMIT": {"rule": "literal", "value": "ISSUED FOR CONSTRUCTION  2026-02-15"}}}}
```

(`examples/specs/apply-header-footer-template.json`, with `examples/templates/00_00_00_TEMPLATE.docx` — the fixture template, both slot conventions.) A relative `template` path is resolved against the spec file's folder.

**Which template part goes where.** `template_sections` maps target sections to template sections: `{"cover": i, "body": j}` — target section 0 gets template section *i*, every other section *j* — or `{"all": i}`. For each kind in `kinds` (default header and footer) and each type the template section *displays* — `default` always, `first` when that section has `titlePg`, `even` when the template has `evenAndOddHeaders` — the part it shows (inheritance from earlier template sections resolved) is copied in as a **new** part (`word/header{n}.xml`, `word/footer{n}.xml`, the first free numbers), with its `.rels`, its images under fresh `word/media/image{n}.*` names, a content-type override and a relationship from `document.xml`. One copy is shared by the target sections that map to the same template part at the same text width. Every target section then references exactly its copies; the old parts of the selected kinds go with their rels, overrides and orphaned media — pending revisions inside them are discarded **and flagged by name** (the same removal code as `remove_header_footer`). The document body (the revision-history table included) is not touched.

**Slots** (decision §14.2 — both conventions; `slot_markers` can narrow it):

* `highlight` — a maximal sequence of consecutive runs whose direct `w:highlight` is in `highlight_colors` (default `yellow`). Name: `NAME` when the highlighted text, stripped, is `{{NAME}}`; otherwise the stripped highlighted text itself (`"XX XX XX"`, `"ISSUED FOR PERMIT"`) — rsid churn that split it over several runs is fine.
* `braces` — every `{{NAME}}` (letters, digits, underscore) in the paragraph's display text, wherever the run boundaries fall (`{{SECTION_` + `NUMBER}}` in two runs is one slot), also inside a run with other text and inside table cells.
* A `{{NAME}}` inside a highlight span is the same slot only when the highlighted text is exactly that placeholder; any other overlap is refused (`slot-overlap`). Runs a slot covers or cuts through may hold only `w:rPr` and `w:t` (`slot-structure`); slots inside textboxes are refused (`slot-in-textbox`). (Reference slots of a [template spec](#template-specs-match-the-architects-spec) may sit in runs that also hold a tab or a field character; those are cut around the value.)
* **Display text** of a paragraph: its `w:t` whose nearest `w:p` is that paragraph, outside `w:del`/`w:moveFrom`, in document order (no field instructions, no deleted text). Slot offsets and fill extraction use it.
* Filling a slot: runs a slot boundary cuts are split (properties carried), the slot's runs collapse into the first one, which receives the fill and — for a highlight slot — loses its highlight. Everything else in the run (its character style, bold…) stays.

**Fill rules** — evaluated on each target, so every spec gets its own values:

| Rule | Params | Value |
|---|---|---|
| `literal` | `value` | the user's approved words, verbatim — the op's mode becomes `untracked` and the plan flags the words |
| `regex` | `pattern`, `source` (`document` \| `header.<type>` \| `footer.<type>`, default `document`), optional `section` | `re.search` on the display text of every paragraph of the source (headers/footers as *shown* by each section, or one section's); group 1 when the pattern has groups. At least one match (`not-found`), all matches equal (`ambiguous`) |
| `paragraph_after` | `anchor_regex`, `source`, `section` | exactly one paragraph matches the anchor (`not-found` / `ambiguous`); the value is the display text of the next paragraph that is not blank |

Values are stripped of surrounding whitespace (`"strip": false` keeps it) and must not be empty. Every template slot needs a rule (`unfilled-slot`), every rule a slot in the headers/footers of the template sections the spec maps (`unknown-slot` — a typo'd slot name must not pass silently). Since the 2026-10-15 sweep "the sections the spec maps" means every section named in `template_sections`, not only the ones a particular file uses: a single-section spec in a batch mapped to a cover+body template uses the cover's parts alone, and the body's `SECTION_NUMBER` / `SECTION_TITLE` rules are still the batch's rules, so that file plans (and the compiler no longer rejects the whole batch's spec because of it).

**Tab stops.** Unless `rescale_tabs` is false, the stops of every non-table paragraph of a copy are rescaled from the template section's text width (page width − left − right − gutter) to the target section's: `right`/`end` stops keep their distance from the right margin (`pos + (W_target − W_template)`), `center` stops scale (`floor(pos × W_target / W_template + 0.5)`), others stay. The resulting stops are pinned as direct `w:tabs` (with `clear` entries for stops the target's styles would add).

**Pinning — the template's look survives the target's styles.** Copied paragraphs keep their style names, but the target's `Header`/`Footer`/`Normal`, docDefaults and theme can render them differently (the fixture's template footer is 7 pt with 6 pt before; the target's Footer style says 10 pt). For every top-level paragraph, its paragraph mark and every run of a copy, the plan resolves the **effective** value of each pinnable property in the template package and in the target package (with imported styles) and writes the template's value as direct formatting wherever they differ:

* paragraph: spacing `before`/`after`/`line`/`lineRule`, indentation `left`/`right`/first line (firstLine = +v, hanging = −v; `start`/`end` are aliases), `jc` (start/end = left/right), tab stops;
* run and paragraph mark: fonts `ascii`/`hAnsi` (theme references resolved through the theme part), `sz`, `color` (val, themeColor, themeShade, themeTint), and the toggles `b`, `i`, `caps`, `smallCaps`.

Resolution order, lowest first: docDefaults → table style chain (inside a table: its `tblStyle`, else the default table style) → paragraph style chain (`pStyle`, else the default paragraph style) → character style chain (runs; `rStyle`, else the default character style) → direct formatting; nearest style wins, per attribute for spacing and indentation; tab stops fold, `clear` removing an inherited stop. Toggles: direct formatting wins; among styles a toggle set at exactly one level takes that value, nowhere → off, at more than one level → the op refuses (`toggle-ambiguous`: Word XORs them, §17.7.3, and that is not modelled). Unspecified values take Word's defaults (spacing 0/0/240/auto, indents 0, `jc` left, size 20 half-points, Times New Roman, color auto). Textbox content is copied untouched. The definition is implemented twice — `ops/effective.py` (transform) and `verify/template.py` `Eff` (verifier) — and a test checks they agree on every paragraph and run of three different packages.

**Styles.** Styles the copied parts name (`pStyle`/`rStyle`/`tblStyle`) and their `basedOn`/`link`/`next` closure that the target lacks are imported, appended to `styles.xml` with extension markup (attributes/elements outside the `w`/`r`/`xml` namespaces) stripped. Existing target styles are **never overwritten** — the pins make the copies look right under the target's own definitions. Refused: a missing *default* style, a style with numbering (`structure`), or any missing style with `import_missing_styles: false` (`missing-style`).

**Section properties.** By default each section's `w:pgMar/@w:header` and `@w:footer` follow its template section (`align_header_footer_distance`; asserted by V10 `section_props`) and so does `titlePg` (`align_title_page`; when off and they differ: `title-page`). Page size and margins stay the target's. `evenAndOddHeaders` is document-wide: if the template and the target disagree the op refuses (`even-odd`) rather than flip a setting nobody asked about.

**Refused template content** (`structure` unless noted): tracked revisions in a template part (`template-revisions` — accept or reject them in the template first), bookmarks, comments, footnote/endnote references, `altChunk`, sub-documents, OLE objects, numbered paragraphs, data-bound content controls, relationships other than images and hyperlinks. Drawing ids (`wp:docPr/@id`) are renumbered above the target's highest so Word does not repair the file. A template table wider than the target's text width is flagged (table widths are not rescaled).

**The template file** is pinned by md5 in the plan: apply refuses if it changed after planning (`internal`), V10 fails if it changed before verification. In a batch, input files that *are* a spec's template (same path, or same bytes — a copy in the project folder) are skipped and listed in the report.

**EditSpec `expect`** gained `text_diffs_in_kept_parts` (predicted text changes in parts that exist before and after — 0 for templating: all text changes are whole old parts out, whole new parts in).

### Template specs: match the architect's spec

A **template spec** is an ordinary spec from the architect (or any other firm) used as the header/footer template: nothing in it is highlighted and it has no `{{placeholders}}` — its own section number and title are simply typed into its header and footer. With `slot_markers: ["reference"]` the same op treats *those values* as the slots:

```json
{"op": "apply_header_footer_template", "select": {"sections": "all"},
 "params": {
   "template": "../templates/08 71 00 - DOOR HARDWARE (ACME).docx",
   "template_sections": {"all": 0},
   "slot_markers": ["reference"],
   "slot_fills": {
     "SECTION_NUMBER": {"rule": "regex", "source": "document",
                        "pattern": "^SECTION\\s+(\\d{2}\\s?\\d{2}\\s?\\d{2}(?:\\.\\d{2})?)\\b"},
     "SECTION_TITLE":  {"rule": "first_match", "rules": [
        {"rule": "paragraph_after", "source": "document", "anchor_regex": "^SECTION\\s+\\d{2}\\s?\\d{2}\\s?\\d{2}(?:\\.\\d{2})?\\s*$"},
        {"rule": "regex", "source": "document", "pattern": "^SECTION\\s+\\d{2}\\s?\\d{2}\\s?\\d{2}(?:\\.\\d{2})?\\s*[-–—:]\\s*(.+)$"}]}}}}
```

(`examples/specs/match-template-spec-header-footer.json` with `examples/templates/08 71 00 - DOOR HARDWARE (ACME).docx`; the two rules are the catalog's `reference_fills`.) On the architect's spec "SECTION 08 71 00 – DOOR HARDWARE" with a footer "DOOR HARDWARE ⇥ 08 71 00 - {PAGE}", every fire-protection spec gets the architect's header and footer reading "SECTION 21 13 13 – WET-PIPE SPRINKLER SYSTEMS" and "WET-PIPE SPRINKLER SYSTEMS ⇥ 21 13 13 - {PAGE}"; the firm and project line, the phase, the logo, the tab stops and the PAGE field are the architect's, as they are.

**How the values are found.**

1. **The template spec's own values.** Each fill rule is run on the template spec itself (the same `extract_fill` as on every target) — here "08 71 00" and "DOOR HARDWARE". A rule can name the template's text directly with `in_template` instead; a `literal` fill needs it (e.g. `{"rule": "literal", "value": "ISSUED FOR PERMIT", "in_template": "100% CONSTRUCTION DOCUMENTS"}` replaces the architect's phase with your approved words and makes the op `untracked`, as any literal does). A rule that finds nothing in the template spec is refused (`template-slot`), and so is a value with fewer than 3 letters/digits (too short to locate safely).
2. **Where the header/footer shows them.** In every paragraph of every copied part, an occurrence of a value is its words in order with any whitespace between them, in any case, in the paragraph's **located text**: the display text with a separator wherever a run holds something other than text (a tab, a break, a drawing, a field character). An occurrence never spans a separator, and it must stand on its own: the character on each side is absent, whitespace, a separator, or a mark that does not itself touch a letter or digit. So "08 71 00" is found in "SECTION 08 71 00 – …" and after a tab, but not inside "08 71 00.01", "SECTION08 71 00" or "08 71 00/DOOR".
3. **Each spec's own values** come from the same rules run on that spec, as with any template.

**What is refused instead of copied** (these files go to clients; the architect's number must never end up in a fire-protection spec):

* `template-value-left` — the template spec's own value appears anywhere else in the copied parts *in another form*: the text between the found occurrences, cut into runs of digits and runs of letters (case ignored, never across a separator), contains consecutive pieces that spell the value ("087100", "08-71-00", "SECTION08 71 00", "08 71 00.01" for "08 71 00"). The spelling may start or end inside a run of letters ("DOORHARDWARECONSULTANT", "Door Hardwares" for "DOOR HARDWARE"), never inside a run of digits. Copying it would put the architect's number or title into every spec. A phone number like "(408) 710-0123" or a job number "1087100-01" does not spell it and is copied as it is.
* `slot-in-field` — the value is shown by a Word field (any run between a field's begin and end, or inside a simple field — e.g. a STYLEREF of the title): a replaced field result would come back the next time Word updates fields.
* `slot-overlap` — occurrences of two values share words; `slot-in-textbox` — a value inside a text box; `slot-structure` — an occurrence across runs in different containers (a hyperlink, a content control); `unknown-slot` — a value that appears in none of the copied headers/footers (drop the rule or fix it).

**What the preview shows.** The plan's notes list the template spec's own values, the fill for this spec, and every paragraph holding an occurrence with the occurrences in ⟦ ⟧ — `word/header1.xml: «SECTION ⟦08 71 00⟧ – ⟦DOOR HARDWARE⟧»` — so a value found inside a longer phrase (a title that also starts another line) is visible before anything runs. When the header shows the value in another case or spacing than the template's own value ("Door Hardware" for "DOOR HARDWARE"), it is still found, and the plan flags that each spec's value is placed as that spec writes it.

**Filling.** A run that holds the value and other content (the title, a tab, then the number) is cut at the value's edges: the value's text becomes a run of its own holding the fill, and everything else in the run — its other text, the tab, a field character — stays, in order, in runs of their own with the same properties. Everything else is the general templating behaviour above: tab rescaling, pins, style import, old parts removed, header/footer distances and titlePg following the template.

**Verification.** V10's `template_parts` re-derives all of it from the template file, the source and the output alone (`verify/template.py`, which shares no code with the op): it runs each rule on the template itself (or takes its `in_template` text) and requires the plan's template values; locates the occurrences with its own located text, word bounds and field detection; refuses a value left in another form; re-extracts every fill from the source; and compares every run of every paragraph — text and other content **in order** — with its own model of the cut (V10 used to compare a run's non-text children apart from its text; it now compares the sequence, for every templating mode). V1, V2, V5a and V7 (the template's headers/footers rendered alone, page for page) apply as for any template.

**The compiler** recognizes a template spec: `read_template` returns, beside the template's profile, a `template_spec` summary — whether it is a prepared template (its slots) or a spec, its own section number and title by the catalog's `reference_fills`, every header/footer paragraph showing them (⟦ ⟧-marked), anything reference slots would refuse there, and its body text font and size. The system prompt tells the model to use `slot_markers: ["reference"]` with those rules for a template spec, and a rule that finds nothing in the template spec (`template-slot`) is a compile error, sent back to the model once.

### Profiles, dry runs and the op catalog (Phase 3)

* **`profile.py`** — `profile(pkg)` describes one file for the compiler (and for you): per section the page size, margins, text width, section type, titlePg, page numbering and which header/footer part each type *shows* (and whether the section references it itself or inherits it); per header/footer part its text (tabs shown as ⇥), fields, template slots (both conventions; a slot the op would refuse is listed as unusable), drawings, tables, pending revisions; the paragraph styles in use with count, effective font/size (resolved with `ops/effective.py`), bold/caps, alignment, spacing and a sample; theme fonts, docDefaults, the direct fonts in runs, and `body_text` — the body text's effective font and size by share of characters (v0.4.0); counts (paragraphs, empty ones, tables, drawings, textboxes, numbered paragraphs, pending revisions, notes/comments parts); the first body paragraphs. `bound()` keeps it under `MAX_PROFILE_BYTES` (6000) — always: samples go first, then structure, each structural cut listed in `truncated`, and as a last resort only identity, settings, counts and (since v0.4.0) `fonts.body_text` remain. `profile_batch(files)` groups files by structure pattern (one package open at a time) and profiles one representative each; files that cannot be read are listed under `unreadable`. The profile is descriptive only — no op reads it.
* **`planner.py`** — `plan_batch(inputs, spec)` validates the spec, skips the spec's template files, and plans the spec on every file (`plan_spec`, predict first); each file gets `summarize_plan()` lines or its exception. `BatchPlan.to_text()` / `to_dict()` are what `plan-spec` prints and what the preview (Phase 4) shows. Planning is fast (≈ 3–35 ms per file) and runs in-process; a file that cannot even be read is its own `crash` exception record, never an abort of the batch.
* **`catalog.py`** — `op_catalog()` (ops with mode, description, select/params schemas, verification profile and the **common assumptions** a compiled spec should state for each; the selector grammar; modes; expect checks; the rules a compiler must follow; the worked examples from `examples/specs/`) and `editspec_json_schema()` (Appendix A of the plan with every op's own select/params schema attached as `oneOf`, unknown keys refused). `validate_spec` stays the authority — it also checks what a schema cannot (mode vs ops, expect vs plan, geometry reference vs ops).

### Natural-language editing: compile, preview, approve (Phase 4)

**The compiler** (`compiler.py`, `compile_request(request, files, template=None, answers=(), model=None, client=None)`) runs one tool-use conversation with Claude (default `claude-opus-5-5`, effort `high`, server-side refusal fallback on, the system prompt — rules + the whole op catalog — cached). The model gets the request, the user's earlier answers, project memory, the template's path, and the batch summary (up to four pattern profiles inline). Read-only tools: `inspect_profile(pattern_id)`, `list_styles(file)`, `sample_paragraphs(file, style?, regex?, n?)`, `show_part_text(file, part)`, `read_template()` (since v0.4.0 with a `template_spec` summary: a prepared template's slots, or a template spec's own number/title, where they show and what would be refused, and its body font/size). It ends with `ask_user(questions)` (the compile returns `questions`; nothing runs) or `submit_editspec(spec)`. A submitted spec is accepted only when:

1. `validate_spec` passes (or the spec carries `questions` → status `questions`, or `unsupported` + `proposal` → status `unsupported`);
2. **every word an op would place is traceable** — `replace_text.replace` and every `literal` template fill must appear verbatim (whitespace-insensitive) in the request, the user's answers or project memory; otherwise the model is told to use the user's words or ask (the contract: no generated characters);
3. a **dry plan** on one representative file per structure pattern: a fatal spec-level error (`spec`, `spec-mode`, `geometry`, `conflict`, `bad-params`, `unknown-slot`, `slot-overlap`, and since v0.4.0 `template-slot` — a reference fill rule that finds nothing in the template spec, the same for every file) on any file rejects the spec; every other refusal (`unfilled-slot`, `expect`, `not-found`, `inheritance`, `even-odd`, `spans-runs`, `tracked-format` …) is file-specific and is accepted as long as at least one pattern plans cleanly — the preview shows them and the batch sets those files aside. A spec that plans on none of the patterns, or an empty batch, is rejected with each file's refusal (v0.3.1).

A rejected spec goes back once with the errors (as an `is_error` tool result); a second rejection fails the compile. A refusal, a cut-off answer, or ending twice without submitting also fails it. Status, spec, questions, errors, tool calls and token usage are written to `OUT/compile.json`.

**Project memory** (decision on plan §14.4: yes): a `PROJECT.json` in the specs folder — e.g. `{"stamp": "ISSUED FOR CONSTRUCTION  2026-02-15", "author": "Abraham Borg", "template": "…", "notes": ["…"]}` — is given to the compiler and counts as the user's words for the traceability check (so a stamp stated there need not be re-typed).

**The preview** (`preview.py`, `make_preview(spec, inputs, out_dir)`): the batch dry plan (per-pattern summaries and exceptions); sample files — one plannable file per structure pattern, topped up to `review.sample_files` (default 2, max 3) — planned, applied and verified **in memory** with outputs written only to `preview/samples/`; source and output rendered and composed into side-by-side PNGs (`preview/images/`, page 1 and the first page whose words changed); `PREVIEW.md`, `preview.json`, `spec.json`. Rendering uses Word when available, otherwise LibreOffice **with a visible warning** (decision on §14.5: fall back, never silently). The **approval token** is a hash of the spec, of every planned file's path, size and modification time, and — since the 2026-10-15 sweep — of every template file the spec reads (path, size, modification time and content hash: the template is an input like the source); `run-spec --approval TOKEN` and the window's Run refuse when anything changed since, a re-saved or replaced template spec included.

### Tier B: generated transforms (Phase 5)

Off unless `--allow-codegen` is given (decision on plan §14.3). `codegen.py`:

* **`generate_op(request, proposal, samples, out_dir)`** — Claude (same model and fallback as the compiler) gets the request, the compiler's reason/proposal, the samples' profiles, the rules, the whitelist and two reference ops (`set_page_setup.py`, `set_paragraph_props.py`), and answers with `submit_op(source, select, params)`. Each submission is checked and tried on every sample; the problems (whitelist violations, sandbox errors, hard-limit violations, failing checks with their summaries) go back as an error result; at most 3 attempts.
* **`check_source`** — imports only `from M import names` for `M` in `ALLOWED_FROM` (the `Op`/`PlanFragment`/`BlockDiff`/`JobException` interface, `DocxPackage` + namespaces + `serialize_xml`/`parse_xml`, named `ooxml` helpers, named selector helpers, `lxml.etree`, a few `typing`/`dataclasses`/`collections` names) and plain `import` of `re`, `copy`, `math`, `itertools`, `functools`, `json`; no `open`/`exec`/`eval`/`compile`/`getattr`/`setattr`/`__import__`/`globals`/`type`/…; no dunder attributes except `__init__`; no file-reading/-writing attributes (`from_path`, `write_bytes`, `read_text`, `parse`, `write`, `output_bytes`, `repack`, …); exactly one `Op` subclass with `plan` and `apply`.
* **`run_in_sandbox`** — `python -I -m spec_surgeon.sandbox_runner` in a temp directory with a stripped environment and a 60 s timeout, on a **copy** of the file; the runner re-checks the source, executes it with reduced builtins and an import hook admitting only the whitelisted modules, runs `plan` then `apply`, and writes `out.docx` + `plan.json`. The parent process verifies — the generated code never sees the verifier.
* **`hard_limits`** — no text change in surviving parts (generated ops are formatting-only in this prototype), no added members, no tracked insertions/deletions added or dropped, no `settings.xml`, no `geometry_reference: none` without a reason.
* **`run_generated(g, inputs, out_dir)`** — sandbox + hard limits + full suite per file; verified outputs to `out_dir/outputs`, records to `out_dir/records`; never in place. The module and its attempt history are saved as `generated_ops/<hash>.py` / `.history.json`.

This is a prototype sandbox: it keeps an honest model inside the engine's API; it is **not** a security boundary against hostile code. The real limits are the separate process, copy-only inputs, the timeout, outputs-only delivery and the verifier.

### Selector grammar

Resolved by the engine — twice, independently: by `ops/selectors.py` for the transform and by `verify/props.py` for V10. Both implement exactly this:

* `scope` — part kinds `document | headers | footers | footnotes | endnotes | comments`, resolved via rels.
* `paragraphs` — `"all"` or an object whose keys are AND-ed: `style_in` [styleIds] (no `w:pStyle` = the default paragraph style), `text_regex` (Python `re.search` on the **block text** of CLAUDE.md — `w:t` + `w:delText` + `w:instrText` + `w:delInstrText` + `m:t`), `in_table` (bool), `is_empty` (block text == ""), `index_range` [start, end) over the part's paragraphs in document order.
* `runs` — `"all"` or `{has_direct_font: bool, font_in: [names], not_in_revisions: bool}`.
* `sections` — `"all"` or [sectPr ordinals]; `styles` — as in the table above.
* **Symbol runs** are never changed by a font op: a run is one when any explicit font name (any slot, case-insensitive) in its direct `w:rFonts`, in its character-style chain, or in its paragraph's style chain is in `preserve_fonts` (default: Symbol, Wingdings, Wingdings 2/3, Webdings, Marlett, MT Extra, ZapfDingbats, Zapf Dingbats, Bookshelf Symbol 7, MS Reference Specialty, MS Outlook), or when it holds a `w:sym`. A style is a symbol style under the same rule over its `basedOn` chain. Theme font references are never symbol fonts.

Unknown selector keys are refused on both sides (never ignored).

### EditSpec format

Appendix A of the plan, executed by `editspec.py`:

```json
{
  "request": "change all font to Arial",
  "summary": "…what will happen, in plain English…",
  "assumptions": ["Symbol/Wingdings runs are left alone", "eastAsia slot unchanged", "font size unchanged"],
  "questions": [],
  "applies_to": {"or": "all"},
  "mode": "formatting",
  "ops": [
    {"op": "set_font", "select": {"scope": ["document", "headers", "footers"], "runs": {"has_direct_font": true}},
     "params": {"font": "Arial"}, "expect": {"text_diffs": 0}},
    {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial"}},
    {"op": "set_doc_defaults_font", "params": {"font": "Arial"}}
  ],
  "assertions": [{"kind": "effective_font", "value": "Arial", "scope": ["document", "headers", "footers"]}],
  "review": {"sample_files": 2, "geometry_reference": "none"}
}
```

Rules enforced before or while planning (nothing runs when one is broken):

* `questions` non-empty or `unsupported` present → refused (exit 2), nothing read or written;
* unknown keys at spec or op level, unknown ops → refused (a typo must not silently change what runs);
* each op's `select` and `params` are checked against the op's declared schema (`spec-surgeon ops` lists it): unknown keys, wrong types and values outside an enum are refused — `"occurrences": "frist"` would otherwise have behaved as `"all"`. `tracked: false` is accepted on ops that are never tracked; `tracked: true` there is refused. `replace_text` also checks its own enums, so the v0.1.0 `--params` path refuses the same typos (`bad-params`);
* `mode` must equal what the ops imply (code `spec-mode`);
* `expect` per op: numeric checks the plan must reproduce on each file — `text_diffs`, `text_diffs_outside_removed_parts`, `text_diffs_in_kept_parts`, `members_added`, `members_removed`, `rev_delta` (code `expect`); a value is an exact integer or, since v0.3.0, a range `{"min": n}` / `{"max": n}` / `{"min": n, "max": n}` (non-negative, `min ≤ max`) for batches whose files legitimately differ in how many places change; `rev_delta` maps revision kinds (`ins`, `del`, `ins_mark`, `del_mark`, `rPrChange`, `pPrChange`, …; misspelled kinds are refused) to the same forms; string values are prose, recorded in the record's judgment calls;
* `review.geometry_reference` must agree with the ops (code `geometry`): a spec can neither drop the layout reference an op keeps nor claim one an op gives up by design (checked only where the file actually changes);
* `applies_to.pattern_ids` (without `"or": "all"`): a file with another structure pattern is an exception (code `pattern`);
* `assertions` (spec level) are **post-conditions only** — Phase 1 knows `effective_font` — so a spec can add V10 checks but can never exempt anything from V10's residual comparison.

`examples/specs/change-all-font-arial.json` and `examples/specs/remove-all-headers-footers.json` are the two worked examples from the plan, ready to run.

**"Change all fonts" is three ops plus a post-condition.** `set_font` on runs that carry their own font, `set_style_font` on every style that defines one, `set_doc_defaults_font` at the bottom of the chain — and the `effective_font` assertion proves the composition: V10 resolves every run's and paragraph mark's *effective* font (docDefaults → table style → paragraph style chain → character style chain → direct formatting, theme attributes through `theme1.xml`) in source and output and requires every non-symbol slot to be Arial and every symbol slot unchanged. Leave out `set_font` and V10 fails on the runs that still say "Arial Narrow". Blocks whose text a `replace_text` in the same spec edits are checked too: their runs cannot be paired after the split, so every output text run of such a block must resolve to the asserted font, or to a preserved symbol font that block already had.

### Font sizes: match the template spec's font

The three font ops set the font **size** (`w:sz`) as well as the font. Sizes go only where a level already sets one, so a style's or the document's size keeps governing text that inherits it:

| Op | `size` (points, whole or half) goes to | Notes |
|---|---|---|
| `set_font` | selected runs and paragraph marks that carry their own `w:sz` (listed in `size_from` when given) | with no font given, only those runs are selected; `only_where_defined: true` puts the **font** only on runs that carry a direct font too (the `has_direct_font` runs), so one op handles both |
| `set_style_font` | selected styles that define `w:sz` (listed in `size_from` when given) | per property: a style gets the font where it defines one and the size where it defines one; `only_where_defined: false` sets the size on every selected style (unless `size_from` is given) |
| `set_doc_defaults_font` | `docDefaults/rPrDefault` | its own size is its `w:sz`, or 10 pt when it sets none; with `size_from` it changes only when that size is listed |

`size_from` (a list of point sizes) is what makes "match the template spec's font" safe: `{"size": 10, "size_from": [9]}` turns the 9 pt text 10 pt and leaves the 8 pt headers and footers, a 12 pt note and the 26 pt title as they are. The complex-script size (`w:szCs`) is left alone, as the East Asian font slot is; symbol runs and symbol styles are never touched. A size must be whole or half points between 1 and 1638 (`bad-params`).

```json
"ops": [
  {"op": "set_font", "select": {"scope": ["document", "headers", "footers", "footnotes", "endnotes"], "runs": "all"},
   "params": {"font": "Arial", "size": 10, "size_from": [9], "only_where_defined": true}},
  {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Arial", "size": 10, "size_from": [9]}},
  {"op": "set_doc_defaults_font", "params": {"font": "Arial", "size": 10, "size_from": [9]}}],
"assertions": [
  {"kind": "effective_font", "value": "Arial", "slots": ["ascii", "hAnsi", "cs"], "size": 10, "size_from": [9],
   "scope": ["document", "headers", "footers", "footnotes", "endnotes"], "include_paragraph_marks": true}]
```

(`examples/specs/match-template-spec-font.json`.) The compiler takes the font and size from the template spec's `body_text` (read_template) and `size_from` from the batch's: every profile now carries `fonts.body_text`, the body text's effective font and size by share of characters (`[{"font": "Century Gothic", "size_pt": 9.0, "share": 0.65}, …]`).

**Verification.** `run_fonts`, `style_fonts` and `doc_defaults_fonts` re-derive, from the source, which runs, marks and styles get the font and which get the size (and how many), and check each one: `w:rFonts` exactly as asserted (or untouched where only the size applies), `w:sz` exactly the new value where the size applies, everything else in the run properties identical — a `w:szCs` changed along fails, and so does a size given to a run or style outside `size_from`. The `effective_font` post-condition gained `size` and `size_from` (and `value` became optional): V10 resolves every text run's and paragraph mark's effective size (docDefaults → table style → paragraph style chain → character style chain → direct; 10 pt when nothing sets one) in source and output; with `size_from`, text that resolved to a listed size must now resolve to `size` and all other text must keep its size; symbol runs (a preserved font in their chain, or a `w:sym`) are exempt. Leave out `set_font` and the runs that set their own 9 pt fail it; assert "everything 10 pt" without `size_from` and the 12 pt note fails it.

---

## The verification record

One per file, in `records/<name>.record.md` and `.json`. It is the audit artifact — the batch report links to it, and it is what to read when someone asks "how do I know nothing else changed?"

| Check | What it asserts |
|---|---|
| **V1** Package hash whitelist | changed members ⊆ the plan's intended parts; members removed/added **exactly** as the plan's `members_removed`/`members_added` (nothing otherwise); member order = source order minus removals, additions appended in plan order; **package integrity vs the source's own baseline**: no new dangling relationship (a rels target that is not a member), no new `r:id`/`r:embed` naming no relationship (checked on every member whose bytes or rels changed), no content-type override for a missing member, no member without a content type, no member that was referenced in the source and is now orphaned, and (Phase 2) **no part whose content type disagrees with the relationship that points at it** — a header added without its override would be typed `application/xml` by the `xml` Default, and Word repairs such a file |
| **V2** Text preservation | per-block text (w:t + w:delText + w:instrText + w:delInstrText + m:t) across every text part — document, headers, footers, footnotes, endnotes, comments — differs from the source exactly as the predicted diff list says, count and content. The text scope is the **union** of source and output text parts: a part that stops being a text part (removed, or merely unreferenced) has every block deleted, a new one every block inserted — and those must be in the predicted list |
| **V3** Structural children | per-block counts of w:tab (only inside w:r), w:br, w:cr, hyphens, w:sym, field chars, references, drawings — identical on every aligned block, or changed exactly as the plan declared |
| **V4** Revision accounting | **every revision kind** moves by its predicted delta (default 0): w:ins / w:del / w:moveFrom / w:moveTo as content wrappers, the same under a w:rPr as paragraph-mark revisions (`ins_mark` …), w:rPrChange, w:pPrChange, w:sectPrChange, table property/cell revisions; source text parts before, output text parts after; w:ins/w:del ids unique across parts, and every *new* revision id unique across all kinds |
| **V5a** XSD vs source baseline | changed parts validated against the vendored ECMA-376/ISO 29500 transitional schemas (ignorable namespaces stripped); zero errors the source did not already have. A part **added** from a template (plan `template_baselines`) is judged against the template part it was copied from — errors the template already has are not the op's |
| **V5b** Untracked-edit check | tracked jobs: the reject-all view of the output equals the reject-all view of the source, char for char (parts present on one side only are accounted for by V1/V2). Formatting/untracked jobs: no w:ins/w:del added, V2's list is the whole account, and the number of new tracked property changes (w:rPrChange/w:pPrChange) equals the plan's |
| **V5c** Revision grammar | w:delText only inside w:del, no w:t inside w:del, id + author on every wrapper, `xml:space` on edge whitespace; property-change revisions carry id + author and exactly one snapshot child (judged against the source's baseline — Word omits ids on some pPrChange) |
| **V6** Render and look | both source and output rendered with the same renderer; changed pages rasterized to `render/pages/`; first pages of the whole batch on one contact sheet |
| **V7** Geometry assertion | word positions on the output render vs the source render: max \|Δy\|, \|Δx\| ≤ 0.5 pt on every page the plan did not declare as expected-to-change. Formatting jobs assert it; content jobs record it as informational (a wording change reflows what follows). The plan's `geometry_reference` decides the mode: `source` (default, as described), `none` — N/A for ops that change layout by design (font, spacing, page setup), **with the op's reason in the record; FAIL if no reason** — and `template` — the **output's headers/footers rendered alone against the template's, page for page**. The verifier writes a copy of the template and of the output whose body is only the section breaks — three pages per section, each section starting on a new page with numbering restarted at 1 — so page 3k+v of either copy is section k's v-th page (first/even/odd variants line up) and every word on it is header or footer text (the real extent of each region is known, a footer taller than the bottom margin included). Output section k is paired with template section map[k] from the spec's own `template_sections`; only the templated kinds are compared. Per page and region: where the template shows nothing the output must show nothing; otherwise words must exist within 2 pt of the template region's extent (else "no words where the template's header is"), and the region's outer edge — the header's top line, the footer's bottom line measured from the page bottom — and every shared word must sit within 0.5 pt of the template's. Fills differ from placeholders and are simply not matched; x is not asserted (fills and rescaled tabs move words). FAIL when either render or the section map is missing, or the pages cannot be paired |
| **V8** Accept / reject simulation | content jobs: accept-all view equals the predicted accepted view; reject-all equals the original. N/A when no revisions were added |
| **V9** Overwrite guard | at delivery: source size/mtime unchanged since acquisition, no `~$` lock file, atomic replace |
| **V10** Property assertions | (`verify/props.py`, runs on **every** plan) **(a)** each of the plan's declarative `property_assertions` is re-resolved independently — selector, symbol-run exclusion, style chains — the selected-target count must equal the plan's, and every selected target must carry exactly the asserted change: the asserted attributes set, theme attributes on the slots set removed, every other attribute and child identical to the source; tracked changes need a `w:rPrChange`/`w:pPrChange` (last child, id, author) whose snapshot *is* the source properties, so reject-all restores them. Kinds: `run_fonts`, `style_fonts`, `doc_defaults_fonts`, `paragraph_props`, `section_props`, `section_hf_refs`, `effective_font` (resolved font of every run/mark through docDefaults → table style → paragraph style → character style → direct, with theme fonts), and for templating (`verify/template.py`): `hf_template_refs` (every section references exactly the predicted copies through the output's own rels; titlePg as predicted), `template_parts` (per copy: the template file's md5; the sections using the part and both text widths read from the files; the slots re-detected and **every fill re-extracted from the source by its rule** and compared with the plan's; every block's text = template block with slots replaced; the part skeleton outside top-level paragraph content identical; per paragraph the properties outside the pinnable set identical, the effective pinnable properties of paragraph, mark and every run equal to the template's — tabs rescaled by the declared rule — and the run sequence equal to the template's with each slot collapsed to one fill run whose highlight is gone; `wp:docPr` ids unique in the whole output), `styles_added` (exactly the listed styles are new, each equal to the template's definition minus extension markup). **(b) Residual identity:** in every changed text part and in `styles.xml`, strip what the assertions cover and remove the blocks with predicted text changes; everything left must be identical, element for element, attribute for attribute. Text-edited blocks must keep their pPr and introduce no run-property signature the source block lacked. An unknown assertion kind is a FAIL |

A file is delivered only when no check is FAIL. N/A always carries its reason.

The tests plant defects to prove the verifier sees them: a dropped `xml:space="preserve"` (V5c), a removed tab (V3), a stray character in an untouched paragraph (V2 + V5b), tracked-but-unapproved wording (V2 + V8), a flat sibling deletion instead of the nested form (V5b/V8), an extra zip member (V1).

Phase 1 added (`tests/test_verifier_ext.py`, `tests/test_ops.py`): an unpredicted or unperformed part removal (V1), a removed part's text missing from the predicted list (V2), a relationship left dangling, an `r:id` naming a removed relationship, a part left orphaned (V1), an unpredicted added member (V1), a stray `w:rPrChange` (V4 + V5b + V10), a run turned bold that every other check misses (V10), a media file dropped that the body still shows (V1), a remover that forgets orphaned media in plan *and* apply alike (V1), a header reference removed from an unselected section (V10), a run outside the selector changed (V10), a symbol run changed (V10), a theme attribute left to override the new font (V10), a tracked-change snapshot that would not restore the original (V10), a symbol style changed (V10), paragraph defaults touched by a font change (V10), spacing replaced instead of merged (V10), a section break's `w:sectPr` dropped (V10), an unselected section's margin changed (V10), a page-margin attribute dropped (V10), an incomplete "all fonts" composition (V10 `effective_font`) — and a **selector bug shared by plan and apply** (symbol detection disabled on the transform side, so the plan "predicts" the damage): V10's own selection disagrees with the plan's count and the changed dingbat run is unasserted.

Phase 2 added (`tests/test_template.py`): a fill typed into the output (V2 + V10); a fill **typed by plan and apply alike** — another spec's title, so V2 sees exactly the predicted text (V10 re-extracts the fill from the source and disagrees); a new footer without its content-type override (V1); tabs not rescaled by plan and apply alike (V10); font size left unpinned by plan and apply alike (V10, the footer would render at 10 pt instead of the template's 7 pt); pins dropped by apply (V10); a drawing id colliding with the body's picture (V10); an imported style altered (V10); old parts left in the package (V1 orphaned); a slot's highlight left on the fill (V10); cover and body copies swapped (V10); the template file edited after planning (V10); and, rendered, a header 1" from the page edge where the template's is 0.5", a header 1 pt lower, and a footer 0.25" higher (V7, and only V7) — also when the template has a blank first-page variant that a "best matching page" search would have paired every page with.

---

## Output folder layout

```
<out>/
  BATCH_REPORT.md           summary table, structure patterns, exceptions, failures, flags, where to look
  batch.json                the same, machine-readable
  outputs/<name>.docx       edited files that passed every check (also copied over the source with --in-place)
  failed/<name>.docx        (v0.3.1) edited copies that FAILED verification — kept for diagnosis only, never delivered
  records/<name>.record.md  the verification record, one per file (+ .json)
  plans/<name>.plan.json    the prediction, as written before the transform ran
  exceptions/<name>.exception.json   structured diagnosis for Tier 2
  render/source/*.pdf  render/output/*.pdf  render/pages/*.png  render/contact_sheet.png
  render/template/          (templating) the template's headers/footers-only copy and its render — V7's reference
  render/output-hf/         (templating) each output's headers/footers-only copy and its render — compared with it
```

Default `--out` is `.\SpecSurgeon_out\<timestamp>`.

---

## Performance

Measured on the synthetic fixtures (3-page specs with tracked changes, a cover section and a body section), 2 CPU cores, LibreOffice renderer:

| Stage | 62 files |
|---|---|
| acquire + fingerprint + plan + apply + verify (V1–V5, V8), 4 workers | 1.8 s total (≈ 28 ms per file single-threaded) |
| render 124 PDFs (source + output), geometry, rasters, contact sheet | 26.7 s |
| deliver | < 0.1 s |
| **total** | **28.5 s** |

Rendering is the only real cost and it is parallel (`--workers`) and optional (`--no-render` → V6/V7 N/A). Longer real specs render slower per page; the XML stages scale with document size but stay in the tens of milliseconds. The pipeline is the same for 1 file or 100; only the render time grows.

**EditSpecs (Phase 1)**, measured in the development container (4 cores, LibreOffice 7 for the rendered runs), 24 synthetic specs, `run-spec … --no-render --workers 4`, V1–V5, V8, V10 all run:

| Spec | 24 files, wall | per file, warm, single-threaded | where the time goes |
|---|---|---|---|
| `remove-all-headers-footers.json` | 0.47 s | ≈ 50 ms | plan + apply ≈ 13 ms; verify ≈ 35 ms |
| `change-all-font-arial.json` | 1.32 s | ≈ 175 ms | verify dominates: V5a validates the changed `styles.xml` (python-docx's template carries ~160 style definitions) and V10 resolves the effective font of every run and walks the residual `styles.xml` |

| `apply-header-footer-template.json` (Phase 2) | 2.07 s | ≈ 250 ms | plan ≈ 33 ms, apply ≈ 23 ms; verify ≈ 190 ms — V5a on the changed `styles.xml` (one style imported) and the four new parts, V10's per-run effective-property resolution on both packages |

XSD results are cached per worker by content hash, so files from one template validate their shared parts once. A rendered smoke run of both specs on two files (one of them the hard-case fixture) takes ~5 s; header/footer removal measured 0.00 pt body movement (V7 asserted against the source render). Templating 12 synthetic specs with rendering (LibreOffice, 4 workers): 8.9 s, all PASS, worst header/footer |Δy| vs the template render 0.00 pt — templating renders three PDFs per file (source, output, the output's headers/footers alone) plus the template once.

---

## Exceptions and the agent layer (Tier 2/3)

The engine never guesses. When a job cannot proceed deterministically on a file it raises a `JobException` with a code and details, the batch continues, and the diagnosis lands in `exceptions/<name>.exception.json` (source, job, params, fingerprint, reason, offsets, live text). Codes today: `structure`, `not-found`, `ambiguous`, `shared-part`, `spans-runs`, `field-result`, `nested-container`, `bad-params`, `crash`. Since Phase 1 also: `conflict` (two ops in a spec overlap), `inheritance`, `tracked-format`, `theme-font`, `conflict-property` (see the operations table), `spec-mode`, `expect`, `geometry`, `pattern` (an EditSpec that does not fit the file), `internal` (an op's apply-time cross-check disagreed with its plan — never delivered); since Phase 2 `unfilled-slot`, `unknown-slot`, `slot-overlap`, `slot-structure`, `slot-in-textbox`, `template-revisions`, `even-odd`, `title-page`, `missing-style`, `toggle-ambiguous` (see "Header/footer templating"); and, before any file is read, `questions`, `unsupported`, `spec` for an unusable EditSpec. EditSpec exception records carry the whole spec.

Two optional model-backed commands (install the `agent` extra and set `ANTHROPIC_API_KEY`):

* `spec-surgeon ask "change FM Global to the GRC wording in all 21-series specs"` → a job + params JSON with a confidence and any questions it must ask first. It never invents replacement wording; if you did not give the exact text, it asks.
* `spec-surgeon agent exceptions\X.exception.json` → a written recommendation grounded in the bundled skill (contract, checklist, lessons): what the structure is, whether and how the edit can be done safely, the predicted diff list and revision delta, what to verify, and the one question a human must answer first. The agent does not edit files in this prototype.

Tier 3 — a request no module handles — is development: use the skill (`src/spec_surgeon/skill/`) interactively on the few files in question; the deliverable is a new module in `jobs/` plus tests, and the next time that request is Tier 1.

---

## Renderers

| Backend | When chosen (`--renderer auto`) | Notes |
|---|---|---|
| `word` | Windows + `pywin32` + Word installed | a **new private Word instance** (`DispatchEx`), invisible, working on copies in a temp folder; never attaches to your running Word. Word's rendering is the truth LibreOffice approximates. `WordSession.accept_reject_text()` is available as an oracle for nested revisions. Marked [unproven] in the skill until it has run on a real job. |
| `libreoffice` | `soffice` found | batch conversion per process, N parallel profiles; LibreOffice caveats from the lessons apply (nested revisions rendered as live text, pStyle-linked numbering, page counts) |
| none | | V6/V7 N/A |

---

## The overwrite guard

`--in-place` writes verified outputs over their sources. Before each write the guard:

1. re-checks the source's size and `mtime_ns` against the values recorded at acquisition — any drift fails V9 with "rebase" (re-run the job on the current file; it is cheap);
2. looks for Word's owner lock file (`~$` + the name with its first one or two characters replaced) beside the document — present means the file is open in Word, and Word's next save would silently overwrite the delivery;
3. refuses a **read-only** file (Windows' read-only attribute, or a POSIX mode without owner write — judged from the file's mode, so an administrator sees the same answer as everyone else): a read-only spec is its author's "do not change this"; clear the attribute or run in copies mode (since the 2026-10-15 sweep);
4. writes a temp file in the same directory and `os.replace`s it (atomic; OneDrive sees one new version) — a replace the file system refuses (a lock held by another program, access denied, a share that went away) is reported as that file's V9 failure with the reason, the temp file removed and the original untouched; it never stops the batch (since the 2026-10-15 sweep);
5. re-acquires and records the new identity.

Without `--in-place`, outputs stay in `<out>/outputs/` and V9 only confirms the sources did not move during the run. OneDrive/SharePoint version history remains the last-resort rollback. Since v0.3.1 an output that fails any check is moved out of `outputs/` into `<out>/failed/` before delivery, and so is an output whose V9 guard fails afterwards (the source drifted during the run, or the in-place write-back was refused because the file was open in Word), so copying the outputs folder onto a project share can never pick up a file the suite rejected; the batch report, the CLI summary and the window all name the folder.

---

## Library API (for a GUI)

```python
from pathlib import Path
from spec_surgeon.batch import run_batch, expand_inputs
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.package import DocxPackage
from spec_surgeon.jobs import get_job, REGISTRY, JobException

# pre-flight: group files by structure pattern before showing the user anything
fps = {f: fingerprint(DocxPackage.from_path(f)) for f in expand_inputs([r"C:\Projects\AVC11\Specs"])}

# dry run for one file
plan = get_job("tracked-replace").plan(DocxPackage.from_path(f), {"find": "FM Global", "replace": "GRC"})

# the batch (returns BatchResult with per-file FileResult: status, plan, exception, record, delivered_to, timings)
batch = run_batch([r"C:\Projects\AVC11\Specs"], "page-numbering", {"option": "cover-unnumbered"},
                  Path(r"C:\Temp\ss-run1"), workers=4, renderer="auto", in_place=False,
                  progress=lambda msg: print(msg))
for fr in batch.files:
    print(fr.source.name, fr.status, fr.record.to_markdown() if fr.record else fr.exception)
```

`run_batch` uses a process pool; a GUI entry point must sit under `if __name__ == "__main__":` on Windows. Progress is a callback; the GUI can drive a progress bar from it (the window does: `ui/rows.ProgressTracker` reads its `"N file(s); transform+verify …"` and `"  done NAME"` lines). The parameter form for a job is its `params_schema` (JSON-schema-like) on the job class.

The window itself is built on three Qt-free pieces you can reuse:

```python
from spec_surgeon.gui import Session          # everything the window does: files, compile, preview, run, set-aside, advise
from spec_surgeon import updater, settings    # update check/download/verify/install; remembered settings

s = Session(folder=Path(r"C:\Projects\AVC11\Specs"))
s.load_spec(Path("examples/specs/remove-all-headers-footers.json"))
print(s.make_preview(render=False, log=print))                  # the preview text; s.preview holds the object
print(s.run(in_place=False, render=False, log=print))           # s.last_batch holds the BatchResult

r = updater.check_for_update()                                    # CheckResult: "update" | "current" | "none"
if r.status == "update":
    dl = updater.download_installer(r.info, settings.updates_dir())   # name, size and SHA-256 checked
```

EditSpecs and single ops:

```python
from spec_surgeon.editspec import load_spec, validate_spec, plan_spec, apply_spec
from spec_surgeon.ops import OPS, get_op, merge_fragments
from spec_surgeon.verify import run_suite

spec = load_spec(r"examples\specs\change-all-font-arial.json")
validate_spec(spec)                                   # raises SpecError (questions, unknown keys/ops …)

pkg = DocxPackage.from_path(f)
plan, fragments = plan_spec(pkg, spec)               # predict first; raises JobException with a code
out = apply_spec(pkg, fragments).output_bytes()       # working copy, ops applied in spec order
record = run_suite(pkg.data, out, plan.to_dict(), str(f), "out.docx")

# the batch: same pipeline, spec instead of job/params
batch = run_batch([r"C:\Projects\AVC11\Specs"], "edit-spec", {}, Path(r"C:\Temp\ss-fonts"), spec=spec)

# natural language (Phase 4–6): compile → preview → run; advise on exceptions
from spec_surgeon.compiler import compile_request
from spec_surgeon.preview import make_preview
from spec_surgeon.advisor import advise_batch
res = compile_request("remove all headers and footers", expand_inputs([r"C:\Projects\AVC11\Specs"]))  # status: ok | questions | unsupported | failed
pv = make_preview(res.spec, [r"C:\Projects\AVC11\Specs"], Path(r"C:\Temp\ss-pv"))   # pv.to_text(), pv.samples, pv.token
advice = advise_batch(Path(r"C:\Temp\ss-run1"))                     # per exception: recommendation | respec

# one op, by hand
frag = get_op("set_paragraph_props").plan(pkg, {"paragraphs": {"style_in": ["Heading1"]}}, {"spacing": {"before": 240}})

# templating: the plan's notes say which part went where, every fill and its rule, how many values were pinned
frag = get_op("apply_header_footer_template").plan(pkg, {"sections": "all"},
                                                   {"template": r"C:\Projects\AVC11\00 00 00 TEMPLATE.docx",
                                                    "template_sections": {"cover": 0, "body": 1},
                                                    "slot_fills": {"SECTION_NUMBER": {"rule": "regex", "source": "footer.default",
                                                                                      "pattern": r"^(\d{2} \d{2} \d{2})"}}})
print(frag.notes, frag.flags)
```

`DocxPackage` is now a source view plus a working overlay: `copy()`, `write(part, bytes)`, `write_xml(part, root)`, `remove_member(name)` (drops its content-type override too), `add_member(name, data, content_type)` (adds an Override unless the extension's Default already matches), `edit_rels(part, add=[{type, target, target_mode}], remove=[rIds]) → new rIds` (never reuses an id), `relationships()`, `content_types()`, `unreferenced_media()` (media that a relationship targeted in the source and nothing targets now), `output_bytes()` (repack by member substitution; removed members left out, added ones appended), and the unchanged `repack(substitutions)`.

---

## Adding a job module

1. Create `src/spec_surgeon/jobs/<name>.py` with a `Job` subclass: `name`, `mode` (`formatting` | `content`), `description`, `params_schema`, `plan()`, `apply()`.
2. `plan()` reads the source through `DocxPackage` and the transform-side helpers in `ooxml.py`, and returns a `Plan`: `intended_parts`, `diffs` (one `BlockDiff` per block whose verifier-defined text changes — `before`/`after` in `block_full_text()` terms, `struct_after` if structural children change), `accepted_views` for content jobs, `rev_delta`, `geometry_pages_expected_to_change`, `notes`, `flags`, and whatever `apply()` needs in `transform_data`. Raise `JobException(reason, code, details)` whenever the answer would be a guess.
3. `apply()` returns `{part_name: bytes}` for the changed parts only, serialized with `serialize_xml()`.
4. Register it in `jobs/__init__.py`.
5. Write a fixture that exercises its hard cases and a test that runs `plan → apply → run_suite` and asserts `record.passed`; add at least one planted-defect test if the job introduces new grammar.
6. Never import anything from `verify/` in a job, and never import `ooxml.py` or `jobs/` from `verify/`.

The skill's playbooks (`skill/references/playbooks/`) describe the next candidates: numbering-scheme conversion (header/footer/cover templating is now the `apply_header_footer_template` op).

## Adding an op

New work goes into `ops/` (a job is only an alias for the v0.1.0 CLI):

1. Create `src/spec_surgeon/ops/<name>.py` with an `Op` subclass decorated `@register`: `name`, `mode`, `description`, `params_schema`, `select_schema`, `verification_profile` (which checks it feeds and how — shown by `spec-surgeon ops`), `plan()`, `apply()`. Import it at the bottom of `ops/__init__.py`.
2. `plan(pkg, select, params)` starts from `self.fragment(select, params)` and fills, analytically from the source: `intended_parts`, `diffs`, `members_removed`/`members_added`, `rev_delta` (every revision kind it adds or removes — `ooxml.count_revisions` uses V4's definition), `property_assertions` (one per property family it changes — if V10 has no kind for it yet, add the kind to `verify/props.py` first, independently), `touches` (for conflict detection), `rev_ids_needed`/`rev_id_base` if it writes revisions, `geometry_reference` (+ `geometry_reason` when `none`), notes and flags. Resolve selectors with `ops/selectors.py`; anything that would be a guess raises `JobException(reason, code, details)`.
3. `apply(work, fragment)` mutates the working package through its overlay (`write_xml`, `remove_member`, `add_member`, `edit_rels`), reading parts from `work` (an earlier op in the spec may have changed them). Revision ids start at `fragment.transform_data["first_rev_id"]`.
4. Fixture with the hard cases (`tests/make_fixtures.py`), an end-to-end test (plan → merge → apply → `run_suite`, `record.passed`), and at least one planted defect per new grammar — ideally one wrong *output* and one wrong *implementation* (a subclass with a buggy `apply`, or a transform-side helper monkeypatched in plan and apply alike).
5. Never import `verify/` from an op, and never import `ops/`, `editspec.py`, `ooxml.py`, `package.py` or `jobs/` from `verify/`.

---

## Project layout

```
spec-surgeon/
├── pyproject.toml, requirements.txt, README.md, CLAUDE.md
├── docs/NL_SURGICAL_EDITOR_PLAN.md   design + phased plan for the natural-language editor (Phase 1 status inside)
├── docs/audits/                      the 2026-10-14 independent audit of v0.3.0: report, filled checklist, probe scripts
├── docs/releases/                    release notes per version (v0.3.0, v0.3.1, v0.4.0 — the update banner shows them)
├── packaging/windows/                PyInstaller spec, Inno Setup script, launchers, the installed-app smoke test,
│                                     make_icon.py + SpecSurgeon.ico; packaging/gui_screenshot.py makes docs/gui*.png
├── examples/jobs/*.json              ready-made --params files
├── examples/specs/*.json             ready-made EditSpecs (change-all-font-arial, remove-all-headers-footers,
│                                     apply-header-footer-template, apply-header-footer-template-fallback — the
│                                     mixed-batch example with a first_match chain and an expect range;
│                                     match-template-spec-header-footer and match-template-spec-font — a template spec)
├── examples/templates/               00_00_00_TEMPLATE.docx — the fixture header/footer template (both slot conventions);
│                                     "08 71 00 - DOOR HARDWARE (ACME).docx" — an architect's spec used as a template spec
├── src/spec_surgeon/
│   ├── cli.py                        command line
│   ├── batch.py                      orchestration (pool, render, deliver, records, report)
│   ├── report.py                     BATCH_REPORT.md / batch.json
│   ├── package.py                    DocxPackage: zip, rels, sections, working overlay (members, rels, content types), repack-by-substitution
│   ├── editspec.py                   EditSpec load/validate → plan every op → merge → apply (Phase 1 executor)
│   ├── profile.py                    per-pattern document profiles (Phase 3); body text font/size; template_spec_summary
│   ├── planner.py                    batch dry-run planning + plan summaries (Phase 3)
│   ├── catalog.py                    op catalog + EditSpec JSON schema (Phase 3)
│   ├── compiler.py                   request → EditSpec with Claude: tools, submission checks (Phase 4)
│   ├── preview.py                    preview, sample apply/verify/render, approval token (Phase 4)
│   ├── codegen.py                    Tier B: whitelist, sandbox, hard limits, generate/verify loop (Phase 5)
│   ├── sandbox_runner.py             the isolated process that runs a generated op (Phase 5)
│   ├── advisor.py                    exception → recommendation or re-compiled spec (Phase 6)
│   ├── gui.py                        the window's logic (Session) and entry point; no Qt (Phase 7)
│   ├── ui/                           the Qt window (v0.4.0): app.py (MainWindow, build_window, main), pages.py (the four
│   │                                 steps), widgets.py, models.py, rows.py (tables + progress, no Qt), theme.py,
│   │                                 worker.py (background jobs), update_ui.py (banner, checks, install hand-off),
│   │                                 settings_dialog.py, resources/ (icon.svg, icon-256.png)
│   ├── updater.py                    GitHub release check, verified download, detached installer launch (no Qt)
│   ├── settings.py                   settings.json and the app's folders (%APPDATA% / %LOCALAPPDATA%)
│   ├── winapp.py                     Windows helpers: the app mutex, AppUserModelID, message box
│   ├── credentials.py                the API key in Windows Credential Manager
│   ├── ooxml.py                      transform-side helpers (text views, run surgery, revision stamps, paragraph-local merge)
│   ├── fingerprint.py                structure fingerprint + pattern id
│   ├── guard.py                      acquisition identity, lock-file check, atomic guarded write-back
│   ├── agent.py                      optional Claude layer: ask / advise
│   ├── ops/                          base.py (Op, Plan, PlanFragment, merge_fragments, JobException), selectors.py,
│   │                                 fix_page_numbering, replace_text, remove_header_footer, set_font, set_style_font,
│   │                                 set_doc_defaults_font, set_paragraph_props, set_page_setup,
│   │                                 apply_header_footer_template (+ slots.py — incl. reference slots and carve —,
│   │                                 effective.py, hf_common.py),
│   │                                 delete_paragraphs, insert_paragraph (v0.3.0)
│   ├── jobs/                         base.py (Job, OpJob), page_numbering.py, tracked_replace.py — v0.1.0 aliases over ops/
│   ├── verify/                       INDEPENDENT: reader.py, textcheck.py, xsd.py, geometry.py, suite.py, props.py (V10),
│   │                                 template.py (V10 templating kinds)
│   ├── render/                       word_com.py, libreoffice.py
│   ├── schemas/                      vendored ECMA-376 / ISO 29500 transitional XSDs
│   └── skill/                        the docx-surgical-edit skill (the method; used by the agent layer)
└── tests/                            make_fixtures.py (synthetic specs, build_rich op fixture), test_engine.py,
                                      test_ops_base.py, test_package.py, test_verifier_ext.py, test_ops.py,
                                      test_editspec.py, test_template.py, test_independence.py, test_planner.py,
                                      test_compiler.py, test_codegen.py, test_advisor.py, test_gui.py,
                                      test_adaptive_messy.py (v0.3.0), test_audit_fixes.py (v0.3.1),
                                      test_updater.py, test_settings.py, test_ui_rows.py, test_packaging.py,
                                      test_credentials.py, qt_support.py, data/ (v0.4.0),
                                      test_reference_template.py, test_font_size.py, test_template_spec.py
                                      (v0.4.0: template specs and font sizes)
```

---

## Tests

```powershell
py -3 -m pip install -e ".[dev]"
py -3 -m pytest -q -s
```

Covers both jobs on fixtures with a cover/body section split, pending insertions and deletions from other authors, a target inside a pending insertion, a target split across runs, fields, and a tab inside a run; the exceptions; the guard (lock file, drift, atomic write); a 24-file batch; an in-place batch with an exception; and the planted-defect set. Fixtures are built by `tests/make_fixtures.py` (python-docx + lxml); `python tests/make_fixtures.py OUT_DIR --count 60` makes a folder to play with.

Phase 1 (72 tests in total, ~15 s; the rendered geometry test is skipped without LibreOffice):

| File | Covers |
|---|---|
| `test_ops_base.py` | fragment merge order, every conflict rule, mode rules (tracked + untracked text, untracked diffs in surviving vs removed parts), disjoint revision-id ranges, geometry merge, jobs ≡ ops aliases |
| `test_package.py` | working overlay, repack with removals/additions (order, byte identity of untouched members), content-type overrides, `edit_rels`, `unreferenced_media` (shared image kept, header-only logo reported) |
| `test_verifier_ext.py` | each V1/V2/V4/V5b/V10 extension catching a planted defect the v0.1.0 suite could not see; V7 reference modes |
| `test_ops.py` | every op end to end on `build_rich()` plus its planted defects (see the verification record section), the inheritance and tracked-format exceptions, the effective-font post-condition |
| `test_editspec.py` | spec validation and plan-time refusals (questions, unsupported, typos, mode, expect, geometry, pattern, conflict), both example specs on the hard-case fixture, **24-file `run-spec` batches** for both (timing printed with `-s`), in-place delivery, and (with LibreOffice) the rendered geometry of header/footer removal |
| `test_independence.py` | `verify/` imports nothing from `jobs/`, `ops/`, `editspec.py`, `ooxml.py`, `package.py` |

Phase 2 (116 tests in total, ~50 s with LibreOffice present):

| File | Covers |
|---|---|
| `test_template.py` | `apply_header_footer_template` end to end on `build_template()` and `build()` targets — fills from each spec, both slot conventions (a highlighted slot over three runs, a `{{NAME}}` over two runs, one inside a run with other text, one in a footer table cell), HFStamp style imported, tab stops 9360→8640 / →7920 and with rescaling off, pinned 7 pt footer, a pending revision in an old footer predicted and flagged, `{"all": i}` sharing one copy, footers only, header/footer distance alignment; 18 refusals (missing/unknown/ambiguous fills, bad mappings, partial selection, even/odd mismatch, template revisions, overlapping slot, tab inside a slot, bookmark, template changed before apply); the planted defects listed under "The verification record"; the transform-side and verifier-side effective-property resolvers agreeing on every paragraph/run of three packages; the headers/footers-only V7 reference documents; V7 pairing pages by section and variant (synthetic PDFs: a blank template variant can no longer stand in for a displaced header — Codex review on PR #2); the example spec through `run_batch` with the template copy in the folder skipped; and (LibreOffice) V7 against the template render, aligned and misaligned, including a template whose cover's first page is blank (titlePg without a first-page header) |
| `test_editspec.py` | + the templating example spec is valid and its template path resolves; 24-file `run-spec` batch for it |

Phase 7, first cut (166 tests; the real-window test is skipped where Tk or a display is missing):

| File | Covers |
|---|---|
| `test_gui.py` | the window's logic (`Session`): request → preview → run with the sources untouched; Run refusing before a preview and after a file changed; a guarded in-place run; questions, unsupported, and an AI-written transform end to end; plain errors (no folder, no .docx, empty request). With Tk and a display (Xvfb in the container): the real window loads a spec, Preview and Run are clicked, and the log shows the plan and `PASS 2`; preview and Run use the same filtered file set (a template file in the specs folder is neither previewed nor run); an AI-written transform refuses files it was not tested on |

Phase 6 (158 tests, ~65 s with LibreOffice):

| File | Covers |
|---|---|
| `test_advisor.py` | a real batch exception (`expect`) advised on with a scripted client: a recommendation; a re-compiled spec pinned to the file's pattern after one rejection (it still raised on the file); a re-compiled spec that keeps failing; one that would place new words refused (the model then recommends); `advise` over a batch folder and its CLI |

Phase 5 (153 tests):

| File | Covers |
|---|---|
| `test_codegen.py` | the whitelist (10 rejected sources: `import os`, `subprocess`, a non-whitelisted name from an allowed module, `open`, dunder escapes, `eval`, `output_bytes`, `etree.parse`, relative imports, no Op class) and the runtime import guard; a reference generated op (header distance) passing the full suite in the sandbox with the source file untouched; an op that changes an unasserted property failing V10, one predicting text changes failing the hard limits, bad params refused; the sandbox timeout; the hard limits; the generate → feedback → pass loop (scripted client) and running the passing op over a batch (outputs only, sources untouched); giving up after three attempts; `do --allow-codegen` end to end and the "Tier B is off" message without it |

Phase 4 (135 tests):

| File | Covers |
|---|---|
| `test_compiler.py` | the compile loop against a scripted fake client (no API calls): request shape (model, refusal fallback, catalog in the system prompt), tool results returned in one message, a rejected spec going back once and failing the second time, a wrong mode caught by the dry plan, invented replacement words refused until the user supplies them, questions / unsupported / refusal / silence outcomes, the read-only tools (and a bad tool input as an error result), a templating request with `PROJECT.json` memory; the preview (samples verified in memory, files written, the approval token changing with the spec or a touched file; with LibreOffice, before/after images); `do` end to end (preview only → `run-spec --approval` wrong/right token → `--yes` run → a question exits 2) |

Phase 3 (125 tests):

| File | Covers |
|---|---|
| `test_planner.py` | profiles (sections, shown vs inherited parts, header text, styles with effective fonts, direct fonts, template slots, the size bound dropping samples before structure), grouping by pattern, the batch dry run (summaries, an `even-odd` exception, nothing written, the spec's template skipped), the catalog covering every op, the example specs validating against the published JSON schema (and a typo'd param failing it), the `profile` / `plan-spec` / `ops --catalog` CLI |

`build_template()` (`make_fixtures.py OUT_DIR --template`) is the template fixture: a cover section (header "MICROSOFT — AVC11 DATA CENTER⇥[ISSUED FOR PERMIT]" with a character style the targets lack, direct tabs clearing the Header style's center stop, a template-only logo; footer "SECTION [XX][ XX][ XX]") and a body section (header "{{SECTION_NUMBER}} – {{SECTION_TITLE}}⇥[ISSUED FOR PERMIT]" on the Header style's stops; footer "{{SECTION_NUMBER}} - PAGE" plus a 1×2 table and an empty paragraph), 1" side margins (targets have 1.25"), Header 8 pt, Footer 7 pt with 6 pt before. `build()` gained `footer_revision`, `side_margin_twips`, `header_distance_twips`, `footer_distance_twips` and `body_picture` for the targets.

`build_rich()` (`make_fixtures.py OUT_DIR --rich`) is the op fixture: a cover section with first-page and default headers/footers (a header-only logo, an image shared with the body, Jeremy's pending insertion in a header), a body section with its own default and even-page headers and a default-footer reference to the cover's footer part (shared part), a third section that inherits everything; docDefaults with theme fonts, Normal in explicit Times New Roman, Heading 1 with theme major fonts, a Courier paragraph style, a Wingdings character style, direct explicit and direct theme fonts, a direct Wingdings run, a `w:sym` run, direct-font runs inside Jeremy's insertion and Jack's deletion, a run carrying an earlier `w:rPrChange`, a paragraph mark with its own font, existing spacing and a hanging indent, a table, and an empty paragraph.

---

v0.3.1 adds `tests/test_audit_fixes.py` (11 tests, 189 in total): one test per audit fix, each with its planted output defect and, where a definition is implemented on both the transform and the verifier side (inherited formatting of an inserted paragraph, the new selectors), a planted implementation defect shared by plan and apply that V10 must catch. The audit's own probe scripts under `docs/audits/probes/` reproduce every finding outside pytest.

v0.4.0 — the Qt window and updates (252 tests: 251 pass and the Credential Manager test skips off Windows, ~100 s in the container). The Qt tests run offscreen (`QT_QPA_PLATFORM=offscreen`, no display needed). They skip where PySide6 or a library it needs is missing, unless `SPEC_SURGEON_REQUIRE_QT=1` (CI sets it). In the Linux container, `apt-get install libegl1` was needed for Qt to load:

```powershell
py -3 -m pip install -e ".[dev,gui]"
$env:QT_QPA_PLATFORM = "offscreen"; py -3 -m pytest -q -s
```

| File | Covers |
|---|---|
| `test_updater.py` | no network (a scripted opener) and nothing started (a recording popen): versions; the real v0.3.0 release JSON (`tests/data/`) parsed; drafts, pre-releases, non-`X.Y.Z` tags, wrong-version and path-character installer names refused; the request headers; 404 / rate limit (with its reset time) / offline / TLS / bad JSON / HTTP errors in plain words; the rate-limit fallback via the `releases/latest` redirect; skipped versions (quiet on automatic checks, shown on manual ones); the once-a-day rule and its switches; `SHA256SUMS` parsing (CRLF, `*`, upper case, conflicting duplicates); the download (progress, no `.part` left) and every refusal — wrong hash, short, long, a reported size above the 500 MB ceiling (nothing fetched), no checksum line (no installer byte fetched), GitHub digest disagreeing, oversized checksum file, cancel — each leaving nothing behind; HTTPS-only redirects; the Setup command line (quoting paths with spaces and en-dashes); the installer's environment (no API key, no frozen-runtime variables); the launch flags (detached, new group, break-away with a fallback); refusing a copy not installed by Setup and a file changed after the check; cleanup; the pip instructions |
| `test_settings.py` | round trip, hand-edited files with wrong types and unknown keys (incl. a malformed pending update, which must not break start-up), a corrupt file set aside, atomic writes, `SPEC_SURGEON_CONFIG_DIR`, and that the key and the risky switches are not settings |
| `test_ui_rows.py` | the preview and results tables from a real preview and run (copies vs written back, a broken file explained in plain English) and the progress bar reading `run_batch`'s real log lines |
| `test_gui.py` | + the real Qt window offscreen: Preview → Run with the tables filled and the sources untouched; Run enabled only while the preview is current; Claude's questions in the Answers box; an error shown on its step; the template left out of the file count; the update banner from an automatic check, Skip (saved, quiet next time, shown by a manual check); "up to date" and "couldn't check"; Install blocked during a job; the install hand-off (download → launcher → pending update saved → the window closes); a refused launch unblocking the window; a pip copy getting instructions; the post-update banners; what is remembered (folders, request, render) and what is not (the key, write-back, AI-written transforms); closing during a job asks first; without Qt `spec-surgeon-gui` explains how to install it; the engine, CLI and pure modules never import Qt; only `ui/` imports PySide6 |
| `test_packaging.py` | the names the app, installer and release workflow share: installer file name ↔ updater, app mutex, `/RELAUNCH`, `[InstallDelete] {app}\_internal`, AppUserModelID, the icon (sizes parsed from the .ico) and UI resources, `SHA256SUMS` over the installer, version ↔ release notes |
| `test_credentials.py` | Windows only: a Credential Manager round trip on a throw-away target |

On Windows, `packaging/windows/validate_installer.ps1` (run by the build) installs the app and runs `SpecSurgeon.exe --installer-smoke-test` twice:
- **GUI mode:**
  - the Qt window on Windows' real platform previews and runs a spec through its buttons;
  - the process pool, the sandbox and the bundled resources work;
  - the `windows11` style is available, Credential Manager works, and the mutex and AppUserModelID are set;
  - the updater downloads and verifies an installer from a canned local release and refuses a tampered one;
  - a screenshot is saved as an artifact.
- **Update mode:**
  - the window hands over to the freshly built installer exactly as *Install now* does;
  - the script then checks that Setup waited, installed over the app, logged success, and **reopened Spec Surgeon**.

It then reinstalls and uninstalls as before.

v0.4.0 — template specs and font sizes (316 tests: 315 pass and the Credential Manager test skips off Windows, ~110 s in the container):

| File | Covers |
|---|---|
| `test_reference_template.py` | a template spec (`build_reference_spec()`) applied to `build()` targets with `slot_markers: ["reference"]`: each spec's own number and title in the architect's header and footer, the firm line run for run, the footer's tab and PAGE field kept in order around the filled runs, the plan's template values and ⟦ ⟧ notes; several specs each getting their own values; a title written in another case found and flagged; `in_template` with a literal (untracked); look-alikes neither replaced nor refused (a phone number, longer job numbers, the title's words in another phrase); refusals — the value left in another form (no spaces, hyphens, run into a longer number, run into a word, the title run into another word, glued into a longer word, its plural), the value shown by a STYLEREF field, a literal without `in_template`, an empty or too-short `in_template`, a rule that finds nothing in the template spec, a value shown nowhere, overlapping values, mixed markers, `in_template` without reference slots; the transform's and the verifier's locators agreeing on every paragraph of four template variants, and their leftover rules on crafted and 20 000 random cases; planted defects — the architect's number left in an output, a tab moved inside its run, the footer's occurrences missed by plan and apply alike, a wrong template value shared by plan and apply, word bounds ignored by plan and apply alike ("08 71 00.01" → "21 05 00.01"), a leftover rule that never cuts a word shared by plan and apply ("DOORHARDWARECONSULTANT" copied), a carve that drops the tab — and a run holding several `w:t` around a tab cut exactly as the verifier models it |
| `test_font_size.py` | "match the template's font" on `build_sized()`: Arial 10 for the 9 pt text while 8 pt headers, a 12 pt note, symbol runs and symbol styles keep their size; `w:szCs` left alone; no direct font added to a run that only had a size; size-only `set_font` selecting only runs with their own size; `only_where_defined` per property on styles; docDefaults left alone when its size is not listed; bad sizes refused; the post-condition's size validated; the transform's and the verifier's size rules agreeing; planted defects — a size outside `size_from` changed in the output, `w:szCs` changed along, a header style resized, `size_from` ignored by plan and apply alike, sizes added to styles by plan and apply alike — and the effective-size post-condition catching an incomplete composition and an over-broad assertion |
| `test_template_spec.py` | what `read_template` tells the compiler (own values and ⟦ ⟧ occurrences, the refusals reference slots would meet, a prepared template's slots), `fonts.body_text` in every profile and in the last-resort profile, the catalog's `reference_fills` / `in_template` / worked examples, both worked examples through `run_batch` (the template spec in the specs folder skipped), and the compiler loop with a scripted client: a template spec read and a reference spec accepted; a rule that finds nothing in the template spec rejected as `template-slot` and fixed on the second submission |
| `test_audit_fixes.py` | + `in_template` among the catalog's documented fill-rule keys |

`build_reference_spec()` (`make_fixtures.py OUT_DIR --reference` — it wrote `examples/templates/08 71 00 - DOOR HARDWARE (ACME).docx`) is an architect's spec: one section, 1" side margins, Normal Arial 10 pt, a header "ACME ARCHITECTS ⇥ AVC11 DATA CENTER – 100% CONSTRUCTION DOCUMENTS" / "SECTION 08 71 00 – DOOR HARDWARE" (options: the title in another case, shown by a STYLEREF field, an extra header line) and a footer "DOOR HARDWARE ⇥ 08 71 00 - {PAGE}" with the title and number in one run around the tab; `REFERENCE_FILLS` fit it and `build()`. `build_sized()` is a 9 pt discipline spec with 8 pt Header/Footer/SpecNote styles, runs that set their own 9 pt (one with a direct font and a `w:szCs`), a 12 pt note, a direct Wingdings run and a `w:sym` run at 9 pt, a paragraph mark at 9 pt and a 9 pt Wingdings character style.

## Known limits of the prototype

* **Template specs** (`slot_markers: ["reference"]`): a value the architect's header/footer shows in another form ("087100", hyphens, run into other text), through a field (STYLEREF, a document property) or in a text box is refused, not converted — fix the template spec (or name the exact text with `in_template`). The same rule refuses a short one-word title that also sits inside a longer word of the header (the title "DOORS" with a project called "OUTDOORS PAVILION"): the engine cannot tell that from the title glued to other text, and refusing is the safe side. An occurrence is replaced wherever its words stand on their own, also inside a longer phrase ("DOOR HARDWARE SCHEDULE" for the title "DOOR HARDWARE"): check the ⟦ ⟧ notes in the preview. Each spec's value is placed as that spec writes it — a spec whose number is "210500" gets "210500" where the architect wrote "08 71 00". Text in DrawingML shapes (`a:t`, WordArt, charts) is not searched.
* **Font sizes:** the size goes only where a run, style or docDefaults sets one; list-number sizes in `numbering.xml`, the complex-script size (`w:szCs`) and conditional table-style formatting are not changed or modelled. The effective-size post-condition does not model `tblStylePr` either.
* Two job modules. Header/footer templating is now an op (`apply_header_footer_template`, Phase 2, run through EditSpecs); numbering conversion is described in the skill's playbooks but not yet implemented.
* `tracked-replace` edits text whose run sits directly under the paragraph, a hyperlink, an sdt content, or a `w:ins`; a target inside an insertion that wraps a hyperlink/sdt is an exception. Paragraph-mark revisions (deleting whole paragraphs tracked) are not implemented.
* V8's accept/reject simulation is per block and does not merge paragraphs across tracked paragraph marks (no current job creates those).
* The Word renderer/oracle is written to the skill's rules but has not yet been exercised on a real job (the container this was built in has no Word). The LibreOffice path is exercised by the tests.
* The pandoc oracle is used by the tests when pandoc is present, not by the engine.
* The agent commands produce text for a human; they do not edit files.
* The desktop GUI and a standalone Windows x64 installer are included; other platforms use pip.

Phase 1 (operation library) limits:

* **Conflicts are refused, not ordered.** Two ops touching the same block's text, the same property family of the same target, or anything in a member another op removes, make the spec an exception (e.g. "replace FM Global" + "all fonts Arial" where FM Global appears: run them as two specs). Ordered application is a Phase 3 planner topic.
* `remove_header_footer` never creates an empty header/footer part, so it refuses removals that inheritance would undo or spread (`inheritance`); header parts holding anything but images and hyperlinks (charts, OLE, SmartArt) are refused (`structure`). `titlePg`/`evenAndOddHeaders` and margins are left alone.
* Tracked formatting covers runs (`w:rPrChange`) and paragraphs (`w:pPrChange`) only; styles, docDefaults and page setup are plain (`w:sectPrChange` not implemented). A tracked change on a target that already has someone's property change is refused (one snapshot only).
* The effective-font resolver ignores conditional table-style formatting (`w:tblStylePr`) and numbering-level fonts (bullets/numbers keep their numbering-definition fonts by design); `effective_font` checks runs that carry text (`w:t`/`w:delText`) and paragraph marks.
* `word/stylesWithEffects.xml` (Word 2010's compatibility copy) is not updated by `set_style_font`; the plan flags it.
* ~~V7 `template` reference is plumbing only until Phase 2~~ — done in Phase 2 (see the V7 row).
* EditSpec `expect` checks a small numeric vocabulary; prose is recorded, not checked. `applies_to` checks pattern ids; per-pattern compilation is Phase 3. There is no `plan-spec` dry run yet (Phase 3) — `run-spec` without `--in-place` writes only into the output folder.
* The fixtures have no textboxes (`mc:AlternateContent`), footnotes or comments; the ops and V10 handle them by construction (all `w:r`/`w:p` in both branches; every text part), but no fixture proves it yet.

Window (Phase 7) limits:

* One window, one job at a time; long steps run in the background and the progress bar and log show progress, but there is no cancel button (close the window to abandon a preview — it asks first; a run that started finishes its files).
* ~~No settings are remembered between sessions (folders, key); the API key typed into the window lives only in that session's environment.~~ Since v0.4.0 the folders, request, render option, theme and window size are remembered (`settings.json`), and the key can be remembered in Windows Credential Manager (Settings). The risky switches are deliberately never remembered.
* Tested in the Linux container offscreen (Qt) and by the Windows installer build's real-window Preview → Run smoke test and in-app update test. Word COM rendering and live Claude API calls remain **[unproven]**; the Windows runner does not have Word or API credentials.
* The look was checked from offscreen screenshots in the container (the app's own theme, Inter instead of Segoe UI). Qt's native `windows11` style (Settings → Use Windows' own controls) is available on the Windows build but its look with this layout has not been seen on a Windows 11 screen — **[unproven]**.
* The Windows build runner is Windows Server 2022, so the CI smoke test uses the app's own theme; nothing in CI looks at Windows 11 itself.

Updates (v0.4.0) limits:

* The installer is unsigned: the SHA-256 checks prove the download is the file the release lists, not who published it (see [Updates](#updates)). Signing is a later step; the updater's `verify_signature` hook is where it goes.
* PAC/WPAD automatic proxy scripts are not evaluated; behind one, checks fail with a plain message and the releases link.
* Only the latest non-draft, non-pre-release GitHub release is offered (no beta channel, no downgrade).
* v0.3.x has no updater: one manual install of v0.4.0 is needed.
* The first real in-app update from one published version to the next happens after v0.4.0 is released; CI proves the mechanism by updating the installed app to the installer it just built.

Phase 6 limits and what is left:

* The advisor's live path is **[unproven]** (no credentials in the development container). A re-compiled spec is only written, never run — read the advice, then `run-spec` it.
* Plan Phase 6 items 2 and 3 need your machine: run `spec-surgeon plan-spec SPEC "C:\…\past project\Specs"` and `spec-surgeon profile` read-only over past projects (CLT11, AVC11, LVL16) to find structures the fixtures lack, and exercise the Word renderer (`--renderer word`); each surprise becomes a fixture.
* Phase 7 (the GUI) is yours: everything it needs is a plain Python call (`profile_batch`, `compile_request`, `make_preview`, `run_batch`, `advise_batch`) — see "Library API".

Phase 5 limits:

* Generated ops are formatting-only and can only make changes some V10 assertion kind covers (an unasserted change fails V10 by design); text-changing generated transforms are not allowed in this prototype.
* The sandbox is not a security boundary (see above); no CPU/memory limit beyond the timeout (Windows has no `resource` module).
* Generated ops are not registered and not usable inside EditSpecs; they run alone via `do --allow-codegen`.
* The live Claude path is **[unproven]** here, as for the compiler.

Phase 4 limits:

* The live Claude path is **[unproven]**: the development container has no API credentials; the loop, tools and checks are tested with a scripted client. Expect prompt tuning once it runs on real requests.
* One compile per batch (the model sees up to four pattern profiles inline and can inspect the rest); per-pattern compilation is not automatic — a spec that does not fit a pattern shows up as exceptions in the preview.
* The traceability check covers the words ops place today (`replace_text.replace`, literal template fills). A new op that places text must add its params to `compiler.placed_words`.
* The preview's approval is a CLI prompt or a token; the GUI (Phase 7) will drive the same calls.

Phase 3 limits:

* The profile is a summary, not the document: it shows one representative file per structure pattern; styles beyond the 25 most used and header/footer text beyond six lines per part are left out.
* Selector resolution stays inside each op (all through `ops/selectors.py`); the plan's "centralised planner selector resolution" was not needed. Overlapping ops are still refused, not ordered.
* `catalog.EXAMPLES_DIR` reads the worked examples from the repository's `examples/specs/` (an editable install).

Phase 2 (templating) limits:

* `sections: "all"` only — a template applies to whole documents; `template_sections` maps cover/body or all.
* Table widths in copied parts are not rescaled (flagged when wider than the target's text width); tab stops inside tables are not rescaled.
* Slots must be plain text runs (no tabs, fields or drawings inside a slot) and not inside textboxes; textbox content is copied untouched (and not pinned).
* Pinning covers the property list above. Not modelled, so not pinned: conditional table-style formatting (`tblStylePr`), borders/shading, `keepNext`/`widowControl`, east-Asian/complex-script fonts and sizes, underline/strike and other toggles, character spacing. A template whose header/footer depends on those *and* whose target styles differ in them can look different; V7 against the template render is the check that sees it.
* A toggle (`b`, `i`, `caps`, `smallCaps`) set at more than one style level in either package is refused rather than XOR-evaluated.
* Old headers/footers go with any pending revisions in them (flagged by name). Templating is not tracked (`tracked: false` only) — Word cannot track a header part being replaced.
* V7 `template` costs one extra render per file (the output's headers/footers alone). It matches words by text; a header made only of slot placeholders still has its outer edge checked, but no anchor words. Fills that wrap to more lines than the placeholder are not position-checked beyond the first (header) / last (footer) line.
* `evenAndOddHeaders` differing between template and document is refused; page size and margins are left as they are.
---

## Roadmap — natural-language editing

The design and implementation plan for driving this engine from plain-English requests ("remove all headers/footers", "change all font to Arial", "make the header/footer look like this template") at batch scale is in `docs/NL_SURGICAL_EDITOR_PLAN.md`: the model compiles a request into an EditSpec over a library of verified operations once per batch; the engine plans, applies and verifies it per file. Roughly 85% of v0.1.0 carries over; the plan lists what is new, phase by phase.

Progress: **Phase 1 (operation library foundation) is done** — see "Operations and EditSpecs" above and the "Phase 1 status" section of the plan, which lists every place the implementation departs from the plan and why. **Phase 2 (the `apply_header_footer_template` op and V7's template reference) is done** — "Header/footer templating" above, "Phase 2 status" in the plan. **Phase 3 (profiler, dry-run planner, op catalog + EditSpec schema, `profile`/`plan-spec` CLI) is done** — "Profiles, dry runs and the op catalog" above, "Phase 3 status" in the plan. **Phase 4 (compiler, preview, `do`) is done** — "Natural-language editing" above, "Phase 4 status" in the plan. **Phase 5 (Tier B) is done, off by default** — "Tier B: generated transforms" above, "Phase 5 status" in the plan. **Phase 6 (the advisor) is done**; its real-file sweeps and the Word path need your machine — "Phase 6 status" in the plan. **Phase 7, first cut: the window** (`spec-surgeon-gui`) — "The window" above. **Phase 7, second cut (v0.4.0): the Qt window, remembered settings, and in-app updates** — "The window" and "Updates" above.

## Relationship to the docx-surgical-edit skill

The skill is the method; Spec Surgeon is the engine that runs the method's Tier 1 without a model. The skill's verification checklist is the engine's test suite (V1–V9 map one-to-one), its lessons are the engine's known pitfalls (most are cited in code comments where they apply), its playbooks are the backlog of job modules, and the skill itself ships inside the package so the agent layer and future development sessions work from the same text. The skill's rule "scripts are disposable" applies to one-off sessions; within this engine the transform is reused across every file of a batch by design — what is locked in is the verification suite, not any particular implementation, and a future model can rewrite a module against the same tests.


---

## Mixed-quality batches, messy specs, and the iterative set-aside loop (v0.3.0)

Real discipline batches often mix clean, template-derived `.docx` specifications with heavily edited, OCR/PDF-converted, or unstyled ("all-`Normal`") files. v0.3.0 adds four capabilities so the engine can process heterogeneous batches safely without weakening V1–V10:

1. **Body-aware fingerprinting and honest profiling (`fingerprint.py`, `profile.py`)**:
   - `fingerprint(pkg)` computes `body_hygiene` (`style_regime`, `numbering_regime`, `section_anchor_shape`, `run_fragmentation_ratio`, `has_nbsp`, `has_multi_space`, `has_rPrChange_in_body`, `has_pPrChange_in_body`, `has_proof_err`). Files whose body *structure* diverges from the standard layout (an inline section header, a styled regime, numPr numbering) get a distinct `pattern_id`; the whitespace and fragmentation flags are descriptive only (a file whose only mess is NBSPs keeps the clean pattern and is caught at plan time by the set-aside sieve instead).
   - `profile(pkg)` preserves internal multiple spaces and non-breaking spaces (`\u00a0`) in paragraph samples instead of collapsing them, and surfaces a `hygiene` block when whitespace/structural anomalies exist.
   - `editspec.py` `expect` checks accept `{"min": ..., "max": ...}` range objects alongside exact integers.
   - `compiler.py` accepts a valid `EditSpec` whenever at least one pattern in the batch plans cleanly, allowing Pass 1 to run on the clean majority and set aside the messy minority. Since v0.3.1 every non-fatal refusal counts as pattern-sensitive, so a spec that plans on none of the patterns (or an empty batch) is rejected with each file's refusal instead of being accepted and setting every file aside.

2. **Set-aside manifest and iterative "Rinse & Repeat" passes (`report.py`, `batch.py`, `cli.py`, `gui.py`)**:
   - Every batch run writes `<out_dir>/set_aside.json` (and `<out_dir>/SET_ASIDE_REPORT.md` when files are set aside) grouping untouched files by failure code and pattern with plain-English explanations.
   - CLI: `spec-surgeon retry <OUT_DIR>` (or `spec-surgeon do "request" --from-set-aside <OUT_DIR>`) runs the next pass on only the set-aside files and feeds their Pass 1 failure diagnostics (`PRIOR PASS SET-ASIDE DIAGNOSTICS`) into the compiler.
   - GUI (`gui.py`): **Load set-aside files** (`Session.load_set_aside()`) narrows the active file list to the set-aside files from the previous run and pre-loads their failure reasons for the next **Preview → Run** pass.

3. **Garbage-tolerant matching, noise-tolerant run coalescing, and fallback slot chains (`ooxml.py`, `ops/replace_text.py`, `ops/slots.py`, `verify/template.py`)**:
   - `replace_text` supports `flexible_whitespace: true` (matching ASCII spaces against `[\s\u00a0]+` — spaces, tab *characters* and NBSPs inside `w:t`, not `w:tab` elements — hyphens against `[-–—]`, and straight/smart quotes), `ignore_formatting_noise: true` (ignoring the non-visual `rPr` noise `w:lang`, `w:noProof`, `w:webHidden` and a redundant `w:szCs`/auto color during paragraph-local run merging, and dropping the inert `w:proofErr`/`w:lastRenderedPageBreak` markers between the runs it merges; since v0.3.1 `w:cs`, `w:eastAsianLayout` and `w:snapToGrid` are *not* noise, because they change the font, size or pitch the reader sees), and `allow_tracked_format_split: true` (every split fragment keeps a copy of the `w:rPrChange` with a fresh `w:id`; V4 fails on any duplicated id).
   - `apply_header_footer_template` (`ops/slots.py` and `verify/template.py`, independently) supports `first_match` fallback rule chains (`{"rule": "first_match", "rules": [...]}`), `flexible_whitespace` matching (default `true`), and multi-anchor consensus for `paragraph_after`. Since v0.3.1 whitespace normalization serves matching and consensus only: the fill keeps the document's own characters (a non-breaking space in the title stays a non-breaking space in the header) whenever the raw match succeeded. `examples/specs/apply-header-footer-template-fallback.json` is the worked example for a mixed batch.

4. **Messy-document selectors and structural paragraph operations (`ops/selectors.py`, `verify/props.py`, `ops/delete_paragraphs.py`, `ops/insert_paragraph.py`)**:
   - Paragraph selectors (implemented independently in `ops/selectors.py` and `verify/props.py`) add `has_bold`, `is_all_caps`, `after_heading_regex`, and `between_paragraphs` (`{"start_regex": "...", "end_regex": "...", "include_bounds": bool}`), plus tab/NBSP-normalized regex matching.
   - `delete_paragraphs`: tracked (runs wrapped in `w:del` with `w:delText`, paragraph mark marked with `w:pPr/w:rPr/w:del`) or untracked paragraph deletion, verified by V2, V4, V5b, V8, and V10 (`deleted_paragraphs`).
   - `insert_paragraph`: tracked or untracked paragraph insertion `before` or `after` matched anchor paragraphs with verbatim traceability enforcement (`compiler.placed_words`), verified by V2, V4, V5b, V8, and V10 (`inserted_paragraphs`). Since v0.3.1 the new paragraph inherits the anchor's formatting by default (`formatting: "inherit"`): a copy of the anchor's paragraph properties (style, indents, spacing, tabs, list numbering — never its section break, `pPrChange` or paragraph-mark revisions) and of the run properties of the anchor's first text run (minus any `rPrChange`), so in a directly formatted all-`Normal` document the inserted clause looks like its neighbours; `formatting: "none"` copies only the paragraph style. V10's `inserted_paragraphs` re-derives both from the source anchor with its own code and compares.
   - `delete_paragraphs` in tracked mode (since v0.3.1) accepts paragraphs holding bookmark anchors (Word's `_GoBack`) and proofing markers — they stay in place after the `w:del` wrapper; hyperlinks, content controls, fields and comment anchors are still refused.

### The 2026-10-15 hands-on sweep of v0.4.0

`docs/audits/2026-10-15_v0.4.0_hands_on_sweep.md` is a hands-on test of v0.4.0 against the author's own expectation list (the everyday requests, the architect's spec as template, fonts and sizes, paragraph insertion and deletion, the traps, the safety features, messy batches) plus a read of every module, with the EditSpecs the compiler would produce for each request run through `plan-spec` and `run-spec` on synthetic specs that hold every phrase the list needs. Most rows behaved as the list says; the sweep found and fixed these (`tests/test_sweep_fixes.py` pins each):

| Finding | Fix |
|---|---|
| S1 the approval token did not cover the template: a template spec edited (or replaced by the architect's next issue) after the preview ran un-previewed, its new header lines copied into every spec | the token hashes every template file the spec reads (path, size, mtime, content); `run-spec --approval` and the window's Run refuse until a new preview |
| S2 "remove all headers and footers and change all fonts to Arial" planned cleanly and failed V10 on every file (the font op asserted over the headers the other op removed) | font and paragraph ops declare a `verify` touch on every part in their scope; a removal of such a part is a plan-time `conflict` that names the op to narrow (the compiler's dry plan sees it and fixes the scope) |
| S3 a single-section spec in a batch mapped to a cover+body template was refused with `unknown-slot` (the body section's rules named slots that file did not use), and since that code is fatal the compiler rejected the whole batch's spec | `unknown-slot` is judged against every template section the spec maps; the single-section file plans and verifies |
| S4 a read-only source was written back on Linux (root), and on Windows `os.replace` onto a read-only file would have raised a raw `PermissionError` out of `run_batch` after other files were already written | the guard refuses read-only files with a plain reason; any `OSError` of the replace becomes that file's V9 failure, the temp file removed, the batch continuing |
| S5 the preview summary said "text: no block changes text" for a pure tracked deletion ("delete the words 'or approved equal'") | the summary says "N block(s) get a tracked deletion (the deleted words stay visible as a redline until accepted)" |
| S6 `rpr_selected` spelled `return False` as `return True if False else False` | spelled plainly |

Design notes recorded there, not changed: a `replace_text` occurrence inside a field (a TOC or REF result) refuses the whole file rather than skipping that occurrence; a targeted paragraph selector such as `{"in_table": false}` makes `set_paragraph_props` tracked by default (decision §14.1), so "justify all body paragraphs" becomes tracked paragraph changes unless the spec says `tracked: false`.

### Independent audit and the v0.3.1 fixes

`docs/audits/2026-10-14_v0.3.0_independent_audit.md` is an independent audit of the v0.3.0 work (13 ranked findings, per-row verdicts for the author's three-tab checklist, the filled-in checklist beside it, and probe scripts under `docs/audits/probes/` that reproduce every finding). v0.3.1 applies its suggested fixes:

| Finding | Fix |
|---|---|
| F1 V4 missed duplicate revision ids on split `rPrChange` copies that reused a source id | V4 now fails on any revision id (every kind, every text part) used more often in the output than in the source |
| F2 the compiler gate accepted specs that plan on zero files for 22 unclassified refusal codes | every non-fatal refusal is pattern-sensitive; `planned_ok == 0` and empty batches are rejected with each file's refusal |
| F3 failed-verification copies sat in `outputs/` beside passing ones | moved to `failed/`; the report, CLI and window say so; `verify-fail` and nine more codes have plain-English explanations |
| F4 `w:cs` treated as non-visual noise | the noise set is `w:lang`, `w:noProof`, `w:webHidden` only |
| F5 the compiler's catalog never described `first_match`, `occurrence`, `flexible_whitespace` or `expect` ranges | `fill_rules` and `expect_syntax` in the catalog, a real `slot_fills` description, a worked mixed-batch example |
| F6 `insert_paragraph` copied only `pStyle` | `formatting: "inherit"` (default) copies the anchor's paragraph and run properties; V10 verifies them independently |
| F7 tracked `delete_paragraphs` refused bookmarked paragraphs | bookmark anchors and proofing markers are inert |
| F9 `expect` accepted `min > max`, negatives and misspelled revision kinds | refused as `spec` |
| F10 noise merging stripped every proofing marker in the paragraph | only the markers between two runs that merge are removed |
| F11 fills normalised NBSP to a space | fills keep the document's own characters; normalisation is for matching and consensus only |
| F13 the v0.3.0 tests were positive-path only | `tests/test_audit_fixes.py`: every fix with its planted output defect and, where a definition lives on both sides, a planted implementation defect shared by plan and apply |

