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

- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
