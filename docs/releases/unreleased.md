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

Pending on the owner's machine: the installed Windows app without `spec-surgeon.exe`.
