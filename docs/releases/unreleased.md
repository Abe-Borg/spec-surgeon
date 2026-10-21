# Unreleased

Changes since v1.5.0.

## The window is one conversation that delivers

- **One request, one plan.** Edits that touch the same parts of a file (a font change and a header/footer template,
  restyling beside direct formatting, a numbering change beside a restyle) are composed into ordered steps by the
  engine itself and previewed, approved and run as one plan. Nothing is asked of you in two steps any more.
- **The next request builds on the result.** After copies are created, the next thing you ask for is applied on top
  of them; your originals stay as they are. "Start over from the originals" is one tap away.
- **You see the work.** The app shows what it is reading, the model's reply as it is written, the plan before any page
  renders, the pages as they render and the copies as they are written. Stop keeps what landed.
- **Defaults first.** The assistant states its assumptions and goes ahead; it asks at most three questions, each with
  the answer it recommends, and offers replies you can tap.
- **A new window.** The Qt window is gone. The window is now a web page in a native window (Edge WebView2 on
  Windows), in the style of Build-a-Spec: a chat on the left, your documents, the plan, the pages and the copies on
  the right.
- **Fewer, faster model calls.** Every operation's parameters ride the cached prompt instead of a round trip per
  operation; the conversation runs at the model's default effort; the thinking summary is shown.
- **Render once.** One Word instance renders a whole batch; renders are cached by content, so the run never renders
  again what the preview rendered.
- **A tapped reply is your decision, never your wording.** The short replies the assistant offers are its words:
  tapping one sends it as your answer, but words that go into a document are always ones you typed or picked.
- **A document attached after a run** is an original beside the copies; the window says so, and it gets only the
  next change (start over from the originals to give it every change). Removing the last copy returns to the
  originals. A reopened conversation whose copies are gone says so.
- **Closing the window while work runs** stops the running step (its progress is saved) and nothing goes on
  without a window. An update downloaded while work runs waits for **Restart to update**. A request refused
  before it was recorded (no API key yet) comes back into the composer. A link in a reply opens in your browser,
  never in place of the app.

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

Pending on the owner's machine: the installed Windows app with WebView2 and Word, a live request over real
specifications, and the measured time and cost per request.
