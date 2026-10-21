# Unreleased

Changes since v1.7.0.

## Fixes

- Deleting paragraphs without tracked changes no longer breaks a bookmark that starts or ends in a paragraph
  Word must keep (the last paragraph of a table cell, of the document or of its last section, or a paragraph
  between two tables): that end of the bookmark now stays, so cross-references to it keep working.
