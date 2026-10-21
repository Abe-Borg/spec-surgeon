# Unreleased

Changes since v1.7.0.

## Fixes

- **New wording must be your own whole words.** Words the assistant places in a document are checked against what
  you typed as whole words: a piece cut out of a longer word or number ("shall not" out of "shall notify", "50" out of
  "500") is no longer taken as yours, so the assistant asks you for the wording instead.
- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
