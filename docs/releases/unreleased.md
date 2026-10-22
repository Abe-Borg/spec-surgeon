# Unreleased

Changes since v1.8.0.

## Why trust it?

- **Help → Why trust it** gives the short answer: eight claims, each with the mechanism that makes it true. Its
  **I'm not convinced — show me exactly what runs →** button opens the long one on top of it: where every change comes
  from, what leaves your computer and where it goes (with a diagram), one card per button and per automatic behaviour
  saying what runs, what is sent, which AI is involved and what bounds it, what the AI may touch, what the app does not
  do, and how to check all of it yourself. `docs/TRUST_CLAIMS.md` lists every claim with the code behind it.

## Diagnostics for the window itself

- **One file to send when something goes wrong.** **About → Save a diagnostic bundle** writes one zip and shows it in
  Explorer: the app's log and event journals, crash logs, its settings, the current and last three conversations,
  and the logs, records and reports of the five newest runs, with a manifest of what is in it and what was left out.
  Folders of copies, samples and page pictures are left out; conversations and records can quote your words and your
  documents' text, and a conversation's saved history can hold the page images Claude was shown, so treat a bundle
  like your documents.
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

## Seeing the whole batch

- **Every document side by side, before anything is planned.** Spec Surgeon now reads each attached document's
  section number and title, every header and footer line, and its File Properties title, and names the one that
  differs from most of the others: a footer title shown in other capitals (21 05 53's mixed-case footer title in the
  first Division 21 run), a header or footer line missing or extra, a File Properties title naming another section
  or none of the words of the document's title. Before, only the first four layouts were summarised and nothing
  compared the files. The comparison changes nothing; the assistant names what bears on your request and offers a
  fix. After a run, the next request compares the edited copies.
- **A change kept to one place says where the old words remain.** Renaming a title in the footer only now notes, beside
  the pages, every document whose first page (or header, or note) still reads the old words, and the assistant offers
  to change them too. A header or footer filled with a section number or title you typed notes a document whose own
  differs. A rename that finds nothing where you asked says where the words are instead. Nothing extra is changed.
- **Capitals like the template, in one step.** A header or footer filled with a document's own title now shows it in
  capitals when you ask ("show the title in capitals like the template"), through formatting: no letter is retyped,
  so the words are still the document's own. A note beside the pages names each document whose title would otherwise
  show in another case than the template's (21 05 53's "Identification for Fire Suppression Systems" where the
  template shows "DOOR HARDWARE").
- **File Properties titles fixed for a whole batch in one request.** "Set every file's title to SECTION number – title"
  now puts each document's own section number and title in place, between your words. Before, every title had to be
  typed. A document whose number or title cannot be found is set aside, never guessed.
- **Custom methods can highlight.** When no built-in operation can express a highlight request, Spec Surgeon can now
  write a one-off custom method for it, as it already could for bold, fonts or spacing. The list of what a custom
  method may do had fallen behind the checker: highlight, superscript and subscript, hidden text, character spacing
  and double strikethrough were already checked, but the list still said highlight could not be done.
- **Custom methods can format tables.** Table borders, cell margins, widths, shading, vertical alignment, repeated
  header rows and rows kept whole are now within a custom method's reach, on the tables, rows and cells it picks
  ("shade every other row of the equipment schedules"). Every cell's text, the rows, the merged cells and the grid
  are checked unchanged; a method that touches them is refused.
- **Tracked deletions go through redlined paragraphs.** Deleting paragraphs as a tracked change no longer sets aside a
  paragraph that holds someone's pending tracked changes. It deletes over them as Word does: text they inserted is
  deleted inside their insertion (rejecting it still removes all of it), text they deleted stays as it was, their
  formatting changes stay, and a note beside the preview names whose changes lay in the deleted text. Text moved with
  tracked changes, an inserted or deleted table row or cell, and a paragraph that must stay whose paragraph mark
  someone deleted are still set aside, with the reason.

## Fixes

- **Each reply shows once.** A reply that asks nothing (plain words, a list of findings, a failure) used to show
  twice: in the chat and again in the **One thing to clarify** card. It now shows in the chat alone, and the card
  holds only questions, answers and suggested replies. A question of several lines (a list of options) shows
  whole in the card and no longer leaks its later lines into the chat, and a reply's own "• " bullet lines are
  no longer dropped from the chat.
