# Unreleased

Changes since v1.6.0.

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

## Lighter on your computer

- **No more unused AI model.** PyMuPDF 2.0 (released 2026-10-19, two days before v1.6.0 was built) loads a
  page-layout neural network, with onnxruntime and numpy, every time it is imported. Spec Surgeon never uses it, but
  v1.6.0 carried it: about 75 MB more memory and 13 more threads in the window, and roughly 160 MB more in the
  installer. PyMuPDF is now kept below 2.0, and the installed-app check fails if the model ever comes back.
- **One worker by default.** Planning, applying and verifying a batch used one thread per processor core (up to
  eight). Python runs that work one thread at a time, so the extra threads only cost memory: on eight 53-page
  sections, eight threads took 19% longer than one and 2.7 times the memory (471 MB against 177 MB). Batches now
  use one worker; rendering keeps its own parallel workers.
- **A quiet window while it waits.** While Word renders, the window used to redraw itself on every screen refresh:
  the reply's typing animation kept running with nothing to type, and the status line's shimmer repainted the text
  each frame. The typing animation now stops when it has caught up, the status line fades gently instead, and the
  side panel, header and input box no longer redraw on every streamed word. Measured on the page: about 1% of a
  processor core while waiting, down from 6–8%. Finished messages are no longer re-read each time the window
  refreshes.

Pending on the owner's machine: the installed Windows app without `spec-surgeon.exe`, and the rebuilt installer's
memory and processor use with WebView2 and Word.
