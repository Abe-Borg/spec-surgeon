# Unreleased

Changes since v1.7.0.

## The window

- **Five colour schemes.** Settings → Appearance offers Warm Clay (the default, unchanged), Blueprint, Graphite &
  Teal, Carbon & Hi-Vis and Linen, a light scheme. A click recolours the window at once; `settings.json` keeps the
  choice and the next start opens in it, without a dark flash before a light scheme.
- **A clean start every time.** The window no longer reopens the last conversation: each start archives it under
  `conversations\` beside `settings.json` and opens an empty one, as **New conversation** does. Edited copies stay
  where they were saved. A conversation file another program holds open is left exactly as it is.

## Fixes

- **Accepting or rejecting a tracked table row keeps merged cells' text.** A tracked row deletion (or insertion)
  whose row holds the text of a cell merged down over the rows below it is no longer accepted (or rejected) away
  with that text: the file is set aside with an explanation, as deleting that row is. Resolve that row in Word,
  or unmerge the cells first.
- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
- **Bookmarks survive removing notes and hidden text.** Removing specifier notes, hidden text or tagged paragraphs
  without tracked changes no longer breaks a bookmark that starts in a removed paragraph and ends in text that stays,
  or the other way round: that end of the bookmark now stays where the paragraph was, so cross-references to it keep
  working. The same holds for a bookmark end inside a removed hidden field. A bookmark lying wholly in what is removed
  still goes with it.
