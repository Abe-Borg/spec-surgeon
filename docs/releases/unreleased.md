# Unreleased

Changes since v1.8.0.

## Fixes

- **Each reply shows once.** A reply that asks nothing (plain words, a list of findings, a failure) used to show
  twice: in the chat and again in the **One thing to clarify** card. It now shows in the chat alone, and the card
  holds only questions, answers and suggested replies. A question of several lines (a list of options) shows
  whole in the card and no longer leaks its later lines into the chat, and a reply's own "• " bullet lines are
  no longer dropped from the chat.
