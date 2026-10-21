# Unreleased

Changes since v1.6.0.

## No command line

- **The `spec-surgeon` command is gone**, and with it the features only it offered: the recipe library (recipes,
  chains, saved operations, snippets), `advise`, `retry` and `resolve` (recovery runs inside the window), the
  `memory` commands, campaign workspaces and project evidence, built-in workflows, office profiles, specification
  QA audits, the evaluation tools, the diagnostics bundle (`diagnose`) and the two named v0.1.0 jobs. The installer
  no longer ships `spec-surgeon.exe`; `python -m spec_surgeon` opens the window. Nothing changes in the window.
- **Reports** no longer tell you to run a command: a set-aside file's guidance says what to do next, and a defect
  asks for `DIAGNOSTICS.md`, the `logs/` folder and the app's logs (**Help → Open diagnostic logs**).
- **Repair memory** still runs in the window, but nothing in the app can inspect, accept, reject or forget a
  remembered case any more.

## A smaller test suite

- 430 of the 4,114 tests covered the removed features, dead code or duplicated checks and are gone (3,684 remain), and
  the test files named after review rounds are folded into the files for the features they test (175 → 137 files).
- Pull-request CI runs the Windows suite once instead of twice; the installer job skips it when the Windows test job
  has just run it.

Pending on the owner's machine: the installed Windows app without `spec-surgeon.exe`.
