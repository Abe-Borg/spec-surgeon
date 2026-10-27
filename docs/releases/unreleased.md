# Unreleased

Changes since v1.11.1.

## Safe mode: change your mind

- **Discard preview** sits beside **Create edited copies** while a button's preview waits. It withdraws the preview,
  creates nothing, and shows the copies you had before. **All buttons** does the same while a preview waits.
- Each button's form keeps what you typed when you go back to all the buttons. **Clear form** empties it.
- **Undo the last button**, beside **Start over from the originals**, goes back one button at a time: the next button
  builds on the copies from before it. Nothing is deleted; the copies the undone button made stay in their folder.

## The outcome plan is yours to pick

- When no ordinary plan fits and the assistant proposes the outcomes it would check instead, they are now offered as
  one choice, **Use these outcomes and continue**, with every word they would place beneath it. Nothing runs until
  you pick it, and the pick approves those words as any pick does. Before, the app took that plan on its own and
  treated its wording as approved.
- The documents you left out of a request stay out of the outcome plan and its trials; they wait under Left
  untouched, and the assistant is told they are out of scope instead of asking which to do or guessing for them.
- The tracked changes an outcome plan makes carry your own author name (the one every other change uses), never a
  name built into the app.

## Obliterate mode

- Wording Claude writes itself must be in its reply to you, whole, before the plan can be previewed: the app traces
  every placed word to your own words or to that reply and refuses a plan whose words are in neither. The window
  shows no plan card, so the chat is where you read what will be placed.
- A copy delivered UNVERIFIED keeps its mark on every later copy built on it, in every mode, saying *in an earlier
  copy* with the checks that failed; the assistant hears it too when it plans the next change. Start over, Undo or
  removing the document clears it. Before, a later verified copy cleared the mark although it had been checked
  against the unverified one.
- A custom method's failed checks can no longer be kept as "part of the change" when they mean a member, a revision
  or XML the method got wrong, or a source that moved (V1, V4, V5a, V5c, V9): Claude is told to fix the method
  instead.
- The sandbox a custom method runs in now has `bytes`, `bytearray`, `hex`, `bin`, `oct`, `pow` and the arithmetic,
  lookup and text exceptions, so a method that reads a picture's bytes or writes a media member no longer fails on
  its first line; Claude is told exactly which builtins exist. Nothing that reaches the file system was added.

## Fixes

- Two Safe-mode runs started within the same second no longer share a results folder (the second one was refused).
