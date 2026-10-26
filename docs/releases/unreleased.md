# Unreleased

Changes since v1.11.1.

## Safe mode: change your mind

- **Discard preview** sits beside **Create edited copies** while a button's preview waits. It withdraws the preview,
  creates nothing, and shows the copies you had before. **All buttons** does the same while a preview waits.
- Each button's form keeps what you typed when you go back to all the buttons. **Clear form** empties it.
- **Undo the last button**, beside **Start over from the originals**, goes back one button at a time: the next button
  builds on the copies from before it. Nothing is deleted; the copies the undone button made stay in their folder.

## Fixes

- Two Safe-mode runs started within the same second no longer share a results folder (the second one was refused).
