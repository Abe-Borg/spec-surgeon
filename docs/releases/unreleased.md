# Unreleased

Changes since v1.6.0.

## The window

- Every preview card carries a green checkmark (looks good) and a red X (needs more work). **Create edited
  copies** runs the documents that look right; the X'd ones get no copy and wait on a to-do list ("Create edited
  copies (2 set aside)"). Every file that runs is still held to the exact identity the user approved; a file
  outside the approval never runs. Once the other copies are saved, the chat shows **Set aside for more work**
  and offers **Work on them now**: the set-aside documents are planned alone, from the request they were set
  aside from, with the assistant told that the user saw and set aside that plan and shown it; the pass ends when
  their copies are created. The list and the pass survive a restart; Start over clears them.
- The one button (**Create edited copies**, **Continue**, **Save N ready copies**) lives in the conversation,
  above the box you type in, with one sentence on what it does. The plan card carries no button.
- **Your documents** folds to one line (its count and a summary) once a plan exists and opens on a click; a
  document on the to-do list is tagged **To do**, one in the pass **Working on**.
- The **Hide plan** button is gone; the plan and the pages are always shown beside the conversation.
- Markdown renders in both panes: the assistant's replies and questions, the choices, the plan's sentences and
  settings, the assumptions, the findings and the reasons a file was left untouched.

## The contract (`docs/WEB_API.md`)

- `POST /api/primary` takes `hold`: the previewed documents to set aside. The view carries `action.can_hold`,
  `todo` and `focus`. The reply "Work on the documents I set aside" begins the pass over the to-do list.
