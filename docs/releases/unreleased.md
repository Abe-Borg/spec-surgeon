# Unreleased

Changes since v1.7.0.

## Fixes

- **Bookmarks survive a deletion.** Deleting paragraphs without tracked changes no longer breaks a bookmark that
  starts or ends in a paragraph Word must keep (the last paragraph of a table cell, of the document or of its last
  section, or a paragraph between two tables): that end of the bookmark now stays, so cross-references to it keep
  working.
- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
