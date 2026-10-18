# Text hygiene and specification QA — items 41–49

Available starting in v0.8.0.

| Item | Operation or audit | Behavior |
|---|---|---|
| 41 | `normalize_whitespace` | Collapse repeated ordinary spaces and trim trailing ordinary spaces. |
| 42 | `normalize_punctuation` | Choose `quotes: straight/curly`, `dash: en/em` for `--`, and `section_separator: space/hyphen/en/em`. |
| 43 | `protect_nonbreaking_spaces` | Protect values with named units (`psi` by default) and spaced section numbers such as `21 13 19`. |
| 44 | `capitalize_defined_terms` | Claude proposes individual occurrences; each user pick authorizes one occurrence in one unchanged source file. |
| 45 ★ | `placeholders` | Every bracketed option, `<Insert…>`, `XXXX`, `TBD` and `{{variable}}`, per section. |
| 46 ★ | `cross_references` | Section references, target existence and title comparison, duplicate identities, and sections with no incoming reference. |
| 47 ★ | `standards` | Each NFPA, UL, FM, ASTM and ASME mention, designation and edition as written; missing information is explicit. |
| 48 | `readiness` | Comments, tracked changes, effective highlighting, hidden text and placeholders, per file. |
| 49 | `project_identity` | Exact variants of project name, number and date across files, with source locations. |
| — | `duplicate_provisions` | Exact and near-duplicate sibling requirements with matching numeric tokens, where the hierarchy is unambiguous. |
| — | `internal_references` | Explicit article and paragraph references within the current section; uncertain regions remain unassessed. |

## Character edits

Load `examples/specs/normalize-whitespace.json`, `normalize-punctuation.json` or
`nonbreaking-spaces.json` in the desktop window, or use `plan-spec` and `run-spec`.
Typed requests also compile to these operations. Saved specs work without an API key.

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

Ask Claude to review capitalization of terms such as Owner and Work. Its `propose_defined_terms`
tool returns paged candidates with exact before/after text and context. Claude offers a separate
**Approve this change** choice for each desired occurrence. The app builds the choice from the
inspected document. A broad request, an unpicked proposal, or approval of the same word elsewhere
grants no approval. Source changes invalidate the proposal. Executing the approved spec needs no model call.
Only letters whose case changes are edited, preserving formatting on the other letters of a split word.
Put capitalization first in a chain. Later stages read new output paths and bytes, so they require
fresh proposals and approvals after those earlier edits have finished; stale approvals are never transferred.

V10 `character_hygiene` independently derives edits from source text and declared choices,
compares complete paragraph item models, and checks exact occurrence predictions and counts.
The existing package, text, structure, revision and residual checks still run.

## Report-only audits

Choose **File → Spec QA audits…**. No API key is required. The viewer shows all seven reports
and exports Markdown or JSON. It never writes an edited DOCX.
Optional project name, number and issue-date fields let you rerun with expected values
to find similar unlabeled header or cover variants.

**Use project context JSON…** imports one explicitly selected project's approved
standards register, either a context object containing `project_evidence` or the
evidence object exported by `spec-surgeon evidence … inspect`. The viewer displays
the selected source and register-entry count. Identity reruns retain that exact
snapshot; **Clear project context** removes it from the next report. Workspace
projects are never merged implicitly, and importing a register does not edit a
source document or change a saved project's facts.

```sh
spec-surgeon audit Specs --out SPEC_QA.md
spec-surgeon audit Specs --audit placeholders --audit standards --json
spec-surgeon audit Specs --identity '{"project_name":"Harbor Tower","project_number":"H-123","date":"2026-10-17"}' --out SPEC_QA.json
spec-surgeon audit Specs --audit duplicate_provisions --audit internal_references --out STRUCTURE_QA.md
spec-surgeon audit Specs --audit standards --project-context project-context.json --out STANDARDS_QA.json
```

Repeat `--audit` to select reports; omit it for all seven. `--identity @identity.json` reads
canonical values with optional `project_name`, `project_number` and `date` keys.
Identity JSON must be an object; arrays, booleans, numbers, strings and `null` are invalid.
Identity detection uses labels and custom properties. Dates in headers/footers and opening
paragraphs are candidates. Canonical values also find similar unlabeled header/cover values.
Each candidate retains its detection basis; unknown identity is explicit. Different date
spellings are variants even when they denote the same day.

Reports inspect current text, including pending insertions and excluding pending deletions;
all pending revisions are counted separately. Body, headers, footers, notes, comments,
tables and text boxes are read, including orphan Word text parts. Findings retain file,
section, part and zero-based positions in JSON; human reports use one-based positions.
A filename-only section identity is marked unconfirmed. References without a stated title
say so instead of claiming a title match. Self-references do not count as incoming references.
Compact section numbers such as `210500` resolve to the same identity as `21 05 00`.

Standard editions are recorded verbatim within the citation's sentence or clause, including
short ASTM edition suffixes. Years in subsequent sentences are excluded. Latest editions
and AHJ adoption are not inferred. Use the existing approved replacement operation (item 2)
for a later explicit change, for example **NFPA 13, 2025 Edition**, or the AHJ-adopted edition.

`--project-context project-context.json` optionally compares observed standards against the
user-approved register in `project_evidence.standards`. The comparison retains the cited
location and the register entry's edition, source and approval basis. Project-wide entries
apply everywhere; exact document-path and section scopes apply when those identities are
known. Discipline scope is not inferred. Conflicting applicable entries, missing editions
and missing applicable register entries remain `unassessed`. A mismatch is a review finding;
it does not update the document or claim that a standard has been adopted by an authority.

The two structural audits use a read-only index of ordinary main-body paragraphs. It reads
effective automatic numbering before typed markers and retains the original package part,
zero-based paragraph ordinal, full wording and a location that can be found in Word. Every
paragraph is classified, unresolved or excluded. Tables, drawings, textboxes, fields,
hidden text and pending revisions are excluded from classification. Unmarked paragraphs,
unsupported numbering and ambiguous Roman markers require review. Repeated logical labels
make their hierarchy ambiguous. This index supplies evidence, never editing authority.

`duplicate_provisions` compares requirements only under the same unambiguous immediate
parent. It removes recognized labels from the comparison and requires identical numeric
tokens, so requirements for 4-inch and 6-inch pipe are not flagged as duplicates. Exact
normalized wording and similarity of at least 0.92 are reported; near matches need at least
six words in each provision. A document has a ceiling of 50,000 sibling comparisons, with
remaining affected occurrences explicitly unassessed. Similarity never authorizes deletion.

`internal_references` resolves explicit singular Article/Paragraph references such as
`Article 2.3`, `Paragraph 2.3.A.1` and article-relative `Paragraph A.1`. Targets are local to
the current section occurrence; repeated labels produce `ambiguous_target`. Known standard
citations and references qualified by another section are reported as external references.
Missing targets are reported only when the section's hierarchy is fully assessed; otherwise
the result is `unassessed`. Plural lists, ranges, named headings and bare numeric citations
remain outside coverage. Each audit reports coverage and every unassessed occurrence, so
an incomplete hierarchy cannot produce an apparently clean assessment. JSON schema version
2 adds these reports while retaining the existing audit fields.

Readiness states are `not_ready`, `incomplete` or `no_findings`. Ambiguous inherited hidden
formatting, embedded objects and missing section identity remain limitations. Unreadable files
are errors and make `complete` false. No findings is not an issue certification.
Tracked changes include the containing paragraph's location and current text when available;
structural revisions without a containing paragraph retain their part location.
Exit codes: 0 for a completed scan (findings allowed), 1 for unreadable files, 2 for invalid inputs.
Source hashes identify the inspected bytes.

Tests cover tracked/untracked characters, split runs, per-occurrence approval, stale sources,
conflicts, a shared plan/apply defect caught independently, cross-file references, standards,
readiness markup, identity variants, unreadable files, mixed automatic/typed hierarchy,
excluded regions, repeated labels, numeric guards, external citations, scoped standards
register comparisons and source-byte preservation.
