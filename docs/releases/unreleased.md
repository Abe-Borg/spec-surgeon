# Unreleased

Changes since v1.14.0.

## What you've done so far

A new card under the edited copies, **What you've done so far**, lists every change the copies carry since the
originals, oldest first, in every mode: each Safe-mode button as your line in the chat says it, or each request you
typed in Default and Obliterate.

- Under each line, how many of the documents attached now carry it, and which do not and why: left untouched, set aside
  on the to-do list, or attached later.
- The newest line is marked LATEST. **Undo the last button** (or **Undo the last change**) takes it off; **Start over
  from the originals** clears the list.
- In Safe mode, a button you already ran on these copies carries a checkmark, so a button is not pressed twice by
  mistake and the steps of a long batch need not be remembered.
- The list is kept in the saved conversation, and the guided tour and Help describe it.

## Saved recipes

**Save as a recipe…**, on What you've done so far, keeps the changes you tick under a name, to run again on the next
set of documents in one preview.

- **Safe mode** keeps each button with what you filled in its form. Your recipes appear under **My recipes**, above the
  buttons, as tiles that show their steps in order.
- **Default and Obliterate** keep each request in your words with the plan you approved and the words it places. Your
  recipes appear beside the message box: the two you ran last, and **All recipes**.
- Opening a recipe shows its steps. Change a button's form or skip a step for this run only (**Save these changes to
  the recipe** keeps a change), then **Preview**: every step is built from the documents attached now, checked by the
  same gate as any plan and previewed once, with the pages after every step. One **Create edited copies** makes the
  copies, What you've done so far lists the recipe as one entry with its steps, and one Undo takes it off.
- No AI is called to run a recipe, in any mode.
- A step that needs a template, picture or table uses the one attached when it runs. A document a step cannot take is
  left untouched in Safe mode and waits on your to-do list in Default and Obliterate.
- A recipe runs in the mode it was saved in or one with fewer guardrails: Safe recipes in every mode, Default ones in
  Default and Obliterate (never in Safe mode), Obliterate ones only in Obliterate.
- A change Claude wrote a custom method for, or planned with values read from particular documents, cannot be kept, and
  the dialog says why.
- **Manage** renames, duplicates and deletes recipes. They are kept in `recipes.json` beside `settings.json`, never with
  a document. Recipe files are not exported or imported.
