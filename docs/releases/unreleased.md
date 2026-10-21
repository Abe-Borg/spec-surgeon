# Unreleased

Changes since v1.7.0.

## Fixes

- **Accepting or rejecting a tracked table row keeps merged cells' text.** A tracked row deletion (or insertion)
  whose row holds the text of a cell merged down over the rows below it is no longer accepted (or rejected) away
  with that text: the file is set aside with an explanation, as deleting that row is. Resolve that row in Word,
  or unmerge the cells first.
