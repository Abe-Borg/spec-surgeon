# Text hygiene — items 41–44

The report-only specification QA audits (items 45–49 and the structure audits) were a command-line feature and
were removed with it on 2026-10-21.

Available starting in v0.8.0.

| Item | Operation | Behavior |
|---|---|---|
| 41 | `normalize_whitespace` | Collapse repeated ordinary spaces and trim trailing ordinary spaces. |
| 42 | `normalize_punctuation` | Choose `quotes: straight/curly`, `dash: en/em` for `--`, and `section_separator: space/hyphen/en/em`. |
| 43 | `protect_nonbreaking_spaces` | Protect values with named units (`psi` by default) and spaced section numbers such as `21 13 19`. |
| 44 | `capitalize_defined_terms` | Claude proposes individual occurrences; each user pick authorizes one occurrence in one unchanged source file. |

## Character edits

The worked examples are `examples/specs/normalize-whitespace.json`, `normalize-punctuation.json` and
`nonbreaking-spaces.json`. In the conversation window, a typed request compiles to these operations. The app never
normalizes a file on its own: whitespace, punctuation, typed numbers, direct formatting and
pending revisions change only when the request asks for it, as a change shown in the plan
before approval.

The default scope is body, headers and footers; `select.scope` can also name footnotes,
endnotes and comments. `select.paragraphs` narrows the scope. Tabs, structural line breaks,
fields, drawings and numbering are never rewritten as text. Unsupported edit structure
is refused. Nothing to change is a no-op (`allow_missing: false` refuses it).

Edits are tracked by default. For requested clean copies, use `tracked: false` and spec mode
`untracked`. Text operations on one paragraph run in separate chain stages.
The plan and preview list **every exact character change for every file**, including
part, paragraph and character position. JSON quoting exposes `\u00a0` and empty strings.
No new words are generated.

Curly quotes use the preceding character to distinguish opening/closing marks and apostrophes.
Straight quotes after digits stay as possible inch/foot marks, explicitly noted for review.
Dash conversion affects `--`, not longer hyphen sequences. Section numbers keep their digits
and optional `.NN` suffix. Unit matching is case-sensitive; pass the exact symbols in `units`.

Ask for a review of capitalization of terms such as Owner and Work in the conversation window.
The model's `propose_defined_terms` tool returns paged candidates with
exact before/after text and context, and offers a separate choice for each occurrence; in the
window each is a button. The app builds the choice from the inspected document, never from the
model's words. A broad request, an unpicked proposal, or approval of the same word elsewhere
grants no approval. Source changes invalidate the proposal. Executing the approved spec needs no model call.
Only letters whose case changes are edited, preserving formatting on the other letters of a split word.
Put capitalization first in a chain. Later stages read new output paths and bytes, so they require
fresh proposals and approvals after those earlier edits have finished; stale approvals are never transferred.

V10 `character_hygiene` independently derives edits from source text and declared choices,
compares complete paragraph item models, and checks exact occurrence predictions and counts.
The existing package, text, structure, revision and residual checks still run.
