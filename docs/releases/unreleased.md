# Unreleased

Changes since v1.6.0.

## Lower model cost for follow-up requests

- **The instructions are paid for once an hour, not once per request.** The assistant's instructions and the list of
  operations (about 40,000 tokens, the same for every conversation) now carry their own one-hour cache breakpoint. A
  follow-up after copies are created, "Start over from the originals", or a changed attachment starts a new
  conversation; its first call now reads those instructions from the cache (on Claude Opus 5.5, about $0.01 instead
  of about $0.20, and a faster first reply) instead of writing them again. Before, only the growing conversation was
  cached, for five minutes, so every new conversation paid for the whole prompt again.
- **One-call checks no longer pay for caching they never reuse.** The visual check of the preview pages and the
  recovery critic are single calls; their page images and evidence are now billed as ordinary input rather than at
  the cache-write price.
- **The estimated spending prices one-hour cache writes at their own rate** (twice the input price), and each call's
  record in `conversation.json` and `compile.json` shows how many of its cache writes were for the hour.
