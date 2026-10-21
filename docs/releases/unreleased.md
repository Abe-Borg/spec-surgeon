# Unreleased

Changes since v1.6.0.

## Diagnostics

- **The app log says how every job ended.** `spec-surgeon.log` gets one line at the end of each piece of work in the
  window: what was asked for (send, choose or create copies), where it left the conversation (a plan to approve, a
  question, finished copies, the 48-step pause, ready to continue), whether you stopped it, the type of the error
  when it failed, the AI steps used so far, the estimated spending and how long it took. It never records your words
  or the documents' text.
- **The app log says when the window closed.** A clean exit writes `Spec Surgeon closed` again, as the Qt window did
  before 1.6.0; a session that ends without it did not close cleanly.
