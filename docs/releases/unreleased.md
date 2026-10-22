# Unreleased

Changes since v1.7.0.

## The window

- **Replies flow in.** The assistant's words come onto the page at a reading pace and fade in as they arrive,
  the words of a question or a plan included: those were written inside the model's question or submission and
  used to land all at once when the reply finished; they now stream as the model writes them. A finished reply
  carries on from the words already shown instead of being drawn again in another layout.
- One line above a reply names what the app is doing now ("Reading the template", "Writing the plan…"), each new
  step easing in over the last; once the reply is written it folds into a summary ("3 steps · thinking") that
  opens the log: what the assistant did, the words it said along the way before its reply, and its thinking
  summary, which shows in a short window that follows its newest line while the model thinks. A working line
  whose job ended without a reply folds away instead of vanishing.
- New messages, notes and cards rise in; the choices and reply chips land one after another; the button above
  the box slides open and shut; the view eases down to new content, and stays where you are while you read
  further up. With Windows' animation effects off (reduced motion), everything shows at once.

## Fixes

- **A comment's author is the name you give.** The reviewer name and initials Word shows on a comment or a reply are
  checked like any other wording: a name you did not type is refused, and the assistant asks you for it.
