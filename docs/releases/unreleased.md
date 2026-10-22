# Unreleased

Changes since v1.7.0.

## Fixes

- **Tracked edits beside resized table columns.** A file in which someone resized table columns (or changed a row's
  table settings) with Track Changes on is no longer set aside after a tracked edit: the new tracked changes now take
  numbers no mark already in the file uses, where before they could take that column change's number and fail the
  revision check.
- **New wording must be your own whole words.** Words the assistant places in a document are checked against what
  you typed as whole words: a piece cut out of a longer word or number ("shall not" out of "shall notify", "50" out of
  "500") is no longer taken as yours, so the assistant asks you for the wording instead.
- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
- **Accepting or rejecting a tracked table row keeps merged cells' text.** A tracked row deletion (or insertion)
  whose row holds the text of a cell merged down over the rows below it is no longer accepted (or rejected) away
  with that text: the file is set aside with an explanation, as deleting that row is. Resolve that row in Word,
  or unmerge the cells first.
- **Bookmarks survive removing notes and hidden text.** Removing specifier notes, hidden text or tagged paragraphs
  without tracked changes no longer breaks a bookmark that starts in a removed paragraph and ends in text that stays,
  or the other way round: that end of the bookmark now stays where the paragraph was, so cross-references to it keep
  working. The same holds for a bookmark end inside a removed hidden field. A bookmark lying wholly in what is removed
  still goes with it.
- **Only files you attached are used.** A table, picture or template the assistant names in a plan must be one you
  attached; a file anywhere else on the computer is refused, so its contents can never reach your documents
  without you choosing it. The assistant asks you to attach the file instead.
- **Bookmarks survive a deletion.** Deleting paragraphs without tracked changes no longer breaks a bookmark that
  starts or ends in a paragraph Word must keep (the last paragraph of a table cell, of the document or of its last
  section, or a paragraph between two tables): that end of the bookmark now stays, so cross-references to it keep
  working.
