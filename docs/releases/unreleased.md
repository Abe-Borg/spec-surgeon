# Unreleased

Changes since v1.7.0.

## Fixes

- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
- **Bookmarks survive removing notes and hidden text.** Removing specifier notes, hidden text or tagged paragraphs
  without tracked changes no longer breaks a bookmark that starts in a removed paragraph and ends in text that stays,
  or the other way round: that end of the bookmark now stays where the paragraph was, so cross-references to it keep
  working. The same holds for a bookmark end inside a removed hidden field. A bookmark lying wholly in what is removed
  still goes with it.
