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
