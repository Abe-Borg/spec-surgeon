# Unreleased

Changes since v1.12.0.

## Find and replace, occurrence by occurrence

- The Find and replace button (and any plan with a find-and-replace) lists every occurrence it found under the
  previews, in every document of the plan, with the words around it and the article it sits under. Untick an
  occurrence to leave it as it is, then **Update the preview**: the plan is made again with the unticked ones left
  out, and **Create edited copies** waits until you do. The checker confirms on every copy that each unticked
  occurrence was left untouched and every other one replaced; nothing can be left out that you did not untick.
- A document the plan set aside (two pairs overlapping on the same words, an occurrence inside a hyperlink that a
  tracked change cannot take, text inside a text box) lists every occurrence it found too, so you can untick the
  one its reason names and the document goes ahead with the others.
- Each copy's record lists every occurrence as replaced or left at your word, and the batch report counts them.
- **Add a folder of documents** attaches a folder's own Word documents at once; its subfolders are not entered, and
  the chat says how many documents they hold.
- The guided tour has a chapter on the occurrence list.

## A copy is saved while a file with its content sits where Export put it

- Export records, with each copy's destination, a fingerprint of the bytes it wrote. A copy whose content changed
  since — opened through the window's link, which opens the working copy in the scratch folder, then edited and
  saved in Word — reads **CHANGED** beside its name and counts as unsaved again: the close question asks about it,
  New conversation asks about it, and the next Export writes it beside the file saved before (never over it). A copy
  whose saved file was written over, truncated or edited after Export reads **NOT SAVED** and counts the same way
  (the next Export writes it beside that file); one whose saved file is gone is unsaved again, plainly. Before, a
  copy once exported counted as saved whatever became of it, and closing the window could delete an edit the
  exported file never had.
- Clicking a copy opens the saved file while it holds the copy's current content, else the working copy in the
  scratch folder (the one with the newer content); the link's hover text says which.
- The close reads every copy's and every saved file's bytes again, not the last fingerprint; a conversation
  restored from a window that never closed checks the same way, and an older journal's export entries are read as
  saved, with the fingerprint taken from the saved file while it is there.

## A custom method says what it is for, and long paragraphs are selected again

- When no built-in operation fits and the app writes a custom method on its own, the chat now says Claude's reason
  and what the method is to do, in Claude's words, before it is written. Before, both lived only in the plan's data,
  which the window does not show, so Create could stand enabled over a method whose purpose nobody had read. In
  Obliterate mode, a method kept with failed checks (or with a check the writer kept with a reason) also has that
  said in the chat before Create, ahead of the words its copies add and remove.
- An approved outcome that deletes or formats long paragraphs uses the built-in operation again. The two-second
  pattern deadline added earlier in this release also held every pattern to 1,000 characters, and the app selects
  such paragraphs by their own whole words: five ordinary submittal paragraphs, or one long one, came back refused
  and went to a custom method. A pattern that names one exact text has no length limit now; the deadline still
  holds it, and every other pattern keeps the 1,000-character limit.
- The patterns Claude gives its read tools (sampling, reading and finding paragraphs) and an outcome's locators are
  searched with the same two-second deadline; a pattern that runs past it is answered as an error instead of
  holding the window's work.

## Change your mind in Default and Obliterate mode

- **Discard preview**, beside Create, now works in Default and Obliterate mode as it does in Safe mode: the plan and
  its preview are withdrawn and nothing is created. The request ends there, and your next message starts a new one;
  the assistant is told the last one was withdrawn, so "the same, but italic" still reads.
- **Drop this request** sits in the same place while the assistant is asking a question, a request waits to
  continue, or the work paused at your spending check: it ends that request with nothing created.
- **Undo the last change**, beside Start over, goes back before the last run that made copies, one run at a time, as
  Undo the last button does in Safe mode. Nothing is deleted: the copies an undone run made stay where they were.
- Discarding during a pass over your to-do list puts those documents back on the list as they were.

## Spending: a call that never connected costs nothing

- A call that failed before it reached Anthropic (no network, a name that does not resolve, a proxy or firewall that
  refused the connection) no longer counts its largest possible cost as spent. Before, three such attempts could show
  about $12 spent with nothing sent, and trip the spending check. Every other failed call still keeps its estimate,
  because it may have been billed.

## The deep-dive stress test: a review read whole, a custom method's pages checked, a template's distances kept

- Claude's visual check is never shown as clean when its answer could not be read. An answer that left out its
  summary or its list of findings was read as "no findings": in one run Claude put its whole review, two blocking
  findings among it, under an extra key, and the preview showed a clean check. Now such an answer is a skipped check,
  said as one; an answer whose only key holds the whole review is read as that review, every finding kept.
- A custom method's sample pages get the page-layout check a plan's get, by what the method's own plan says will move,
  and its copies are rendered and compared before any is delivered. Before, the preview said the layout "is checked
  when the copies are created", and the copies went out with that check never made. A sample whose words moved where
  its plan says none do shows "Did not pass", the chat says so before the button, and Create is unavailable; in
  Obliterate mode its copies are delivered marked UNVERIFIED with that check beside them.
- A header/footer template request that keeps each document's own header and footer distances is checked against
  those distances. Before, the page-layout check always measured the headers and footers against the template's
  distances, so every such copy was held although it was exactly as asked, and the app spent its calls repairing
  correct copies. A copy whose distances moved anyway, though the request kept them, still fails the check.

## The stress test's follow-ups: parts no check reads, page counts, refused calls

- In Obliterate mode, a custom method's copy that changes a part of the Word file no check reads (a chart, an embedded
  file, stored data behind a content control, the theme) fails a check of its own, **UNREAD**, naming those parts, and
  is delivered marked UNVERIFIED. Before, such a copy passed every check although nothing had looked at what those
  parts now hold. Claude hears the failure and may keep it with a reason you read beside each copy, as with any other
  failed check in that mode. Safe and Default mode are unchanged: a custom method there may not touch those parts.
- Claude's visual check is told how many pages each sample has and which of them it is shown, so it no longer says a
  page "was not rendered" when it was rendered and simply not shown to it.
- A call Anthropic refuses for its key or its permission (an "unauthorized" answer) no longer counts its largest
  possible cost as spent: no model ran, and nothing is billed. Before, a few such answers could show several dollars
  spent and trip the spending check. Every other failed call still keeps its estimate.
