# Unreleased

Changes since v1.8.0.

## Diagnostics for the window itself

- **One file to send when something goes wrong.** **About → Save a diagnostic bundle** writes one zip and shows it in
  Explorer: the app's log and event journals, crash logs, its settings, the current and last three conversations,
  and the logs, records and reports of the five newest runs, with a manifest of what is in it and what was left out.
  No document, picture or page image goes in; conversations and records can quote your words and your documents'
  text.
- **The window's page reports on itself.** Every error in the page that nothing caught, a job the page lost track of,
  and a page blocked for 2 seconds or more now reach the app's log and event journal, with the page's loads and the
  WebView2 version. Before, the page's errors went to a console nobody sees.
- **A frozen window is written down.** A showing window whose page sends nothing for 45 seconds is logged as frozen,
  with where every part of the app was at that moment, and again when it answers. A minimised window, or a computer
  waking from sleep, is never taken for a frozen one.
- **A window that fails to draw shows a way back.** Instead of going blank, it says it ran into a problem and offers
  **Reload the window**, **Save a diagnostic bundle** and **Open diagnostic logs**. The app behind it keeps running,
  so the conversation and any work in progress carry on through the reload.
- **Jobs say when they start, and a stuck one says where.** The log has a line when each request starts (what kind of
  input, never its words), when Stop is pressed, and when the window closes. A job with no progress for 5 minutes, a
  job still running 30 seconds after Stop, and one that does not stop when the window closes leave every thread's
  stack in the log. Every action the page takes leaves a request event in the journal, and a request that fails
  leaves its traceback in the log, where before it went to a console the window does not have.
- **The next start says what an unclean exit was doing.** When the last session never closed, the log now says what it
  was doing last (the final events of its journal) and whether its crash log caught a crash; an empty one means the
  app was ended from outside (Task Manager, signing out, a power cut) or stopped answering and was closed.

## Fixes

- **Each reply shows once.** A reply that asks nothing (plain words, a list of findings, a failure) used to show
  twice: in the chat and again in the **One thing to clarify** card. It now shows in the chat alone, and the card
  holds only questions, answers and suggested replies. A question of several lines (a list of options) shows
  whole in the card and no longer leaks its later lines into the chat, and a reply's own "• " bullet lines are
  no longer dropped from the chat.
