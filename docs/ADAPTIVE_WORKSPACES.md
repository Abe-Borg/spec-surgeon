# Persistent multi-project workspaces

Header/footer style reconciliation and the supporting-template inputs available to generated
recovery methods are documented in [Template compatibility and recovery](TEMPLATE_STYLE_RECOVERY.md).

Workspaces retain project boundaries, requests, evidence, user decisions, candidate outputs,
and remaining problems above the existing EditSpec, chain and recovery engine. Originals remain
untouched; exporting verified copies is a separate action. Available starting in v0.8.0.
Workspaces are a command-line feature (`spec-surgeon workspace`). The conversation window does not
open them: it works on the documents you attach, with its own fixed allowance
([the conversational desktop](CONVERSATIONAL_DESKTOP.md)).

## Start and inspect

Create a workspace from the project folders, inspect project/document assignments, and set each
project's request and optional template. Investigations run until they finish, reach a limit or
need an answer. Ready candidates remain available while other documents need more work. Reopen
the same workspace to continue.

```text
spec-surgeon workspace create "Projects/Job A" "Projects/Job B" --out "editing-workspace"
spec-surgeon workspace show "editing-workspace" --json
```

Folders are searched recursively by default (`--no-recursive` limits discovery to direct files).
Word lock files and recognized results directories are excluded. Each document records a stable
ID, source hash, original path, structural fingerprint, and classification evidence. Unreadable
files remain visible as unresolved items.

Project context comes from the nearest `PROJECT.json` inside the selected input roots. Different
metadata files remain separate even when documents share a structure. Without metadata,
folder-based assignments are provisional. Inspect them before setting scoped goals; matching
section numbers or template shapes do not establish authoritative project membership.

Use IDs from `show --json` to correct assignments and set project-specific instructions:

```text
spec-surgeon workspace assign "editing-workspace" --project PROJECT_ID --documents DOCUMENT_ID
spec-surgeon workspace context "editing-workspace" --project PROJECT_ID --context @project-facts.json
spec-surgeon workspace goal "editing-workspace" --project PROJECT_ID --request "Apply this project's issue stamp" --template "Job A/template.docx"
```

`goal` also accepts `--spec`, `--chain`, or `--adaptive`, and repeatable `--answer` values for
approved wording. Contexts, templates and answers are scoped to that project's work. Changing
a goal invalidates affected candidates instead of treating old output as current.

## Investigate, answer, and resume

Use `workspace plan` to investigate a request. The agent can inspect every document,
page through XML and paragraphs, render available pages, and write isolated read-only analysis
scripts. It proposes exact effects, source scope, literal wording, evidence, and risks. Review the
complete proposal and approve its specific plan ID before running it. Drafting a different request
does not erase an approved result. Approval changes only the selected documents' active goals.

```text
spec-surgeon workspace plan "editing-workspace" --project PROJECT_ID --request "Make each section title Arial 11 bold" --documents DOCUMENT_ID
spec-surgeon workspace show "editing-workspace" --json
spec-surgeon workspace approve-plan "editing-workspace" --project PROJECT_ID --plan PLAN_ID
```

`plan --template path.docx` attaches a template for inspection and content pinning. Every literal
replacement, insertion, or template fill is exposed for review. Source or context changes make a
proposal stale. Agent summaries and archived conversations cannot approve a different goal.

```text
spec-surgeon workspace run "editing-workspace"
spec-surgeon workspace answer "editing-workspace" --project PROJECT_ID --text "Use the issue date from the cover"
spec-surgeon workspace run "editing-workspace"
```

Answers can be narrowed with `--question-id` and `--files`. Restarting does not require
re-importing the collection. The workspace and recovery stores retain evidence, candidate
history, questions, and completed work. Source or candidate drift must be resolved; saved
status never waives current identity and verification checks.

When a source is repaired outside the program, reacquire it explicitly. Unaffected verified files
retain their results; dependent targets and candidates are re-established against the changed source.

```text
spec-surgeon workspace refresh "editing-workspace" --documents DOCUMENT_ID
spec-surgeon workspace run "editing-workspace"
```

Unambiguous built-in methods receive a deterministic first pass. An unreadable ZIP or unresolved
target stays visible with its diagnosis, while other documents retain their verified results.
Remaining document investigations take turns in bounded model-call slices. The next document's
turn is saved before a provider call, so a small budget or restart does not repeatedly favor the
first file. Internal scheduling slices retain the same attempt and spending allowances.
Older shared recovery sessions may need per-document revalidation when their original source set
changes; archived artifacts are retained.

Standalone recovery also resumes from its existing folder:

```text
spec-surgeon resolve "earlier-recovery"
```

Visual acknowledgement is never restored from disk. After reopening, inspect blocking findings
before acknowledging export. A changed goal or input requires fresh dependent verification.

## Resource policy

| Allowance | Default |
| --- | --- |
| Candidate attempts per problem group | 50 |
| Model calls per investigation | 512 |
| Output tokens per call | 64,000 |
| Estimated total tokens | 20,000,000 |
| Estimated model spend per investigation | $250 |
| Reasoning effort | Maximum |
| Estimated working context before a new epoch | 900,000 tokens |
| Read-only analyzer time / memory | 120 seconds / 1 GiB |
| Read-only analyzer input / result | 128 MiB / 16 MiB |

These are allowances, not quotas to consume. Independent projects receive separate investigation
allowances shared by their document investigations; ten projects can therefore use up to ten configured spending allowances, subject to
the other limits. Review actual usage in investigation records. Provider usage, fallback models,
and incomplete responses can make accounting estimates differ from the final bill.

The context allowance controls when a conversation archives its complete history and starts a new
epoch; it is separate from the investigation's 20-million-token total allowance. The default prioritizes
sustained investigation with the configured [Opus 5.5 model's 1-million-token context](https://platform.claude.com/docs/en/models/opus-5-5/overview),
leaving room for the configured 64,000-token output and estimation error. It is an application estimate,
not a provider token count. When choosing a model with a smaller context window, also set
`--max-context-tokens` to fit that model with output headroom. Saved investigations retain their saved
settings; resume with `--max-context-tokens 900000` to increase an older investigation's allowance.

Use `--budget`, `--max-attempts`, `--max-calls`, `--max-output-tokens`, `--max-total-tokens`,
`--max-context-tokens`, `--model`, and `--effort` on `workspace plan`, `workspace run` or `resolve`. These flags exist only on
the command line; the conversation window has no resource controls.
The `do` and `retry` commands also accept `--max-context-tokens` and `--max-output-tokens` alongside `--model`;
both chat and noninteractive compilation, plus an approved custom-transform handoff, honor those token limits.
Choose a response limit supported by the model and leave additional room for token-estimation error.
Resource pauses preserve work. Increasing an allowance does not weaken validation.
Saved resource settings are restored when reopening a workspace or investigation. CLI flags
override only the settings supplied: lowering a budget remains effective on later continuations.
Use `--render`, `--memory`, and `--no-codegen` to reverse previously saved choices.

An explicitly resumed investigation receives a new continuation allowance; prior spending is
retained in the resource history. These defaults are not a lifetime spending cap for repeatedly
resuming a workspace. `workspace run` exits with status 2 when a project with a goal still needs
work, while preserving its progress and explaining the remaining issue in the report.
Planning continuations have their own recorded allowance; project totals include both planning and
execution history. Read-only analysis limits are configurable through `AnalysisLimits` in the Python API.

The ordinary conversation and generated-operation writer also have expanded iteration allowances.
Equivalent failed proposals without new evidence trigger a stall response. Different useful
attempts can continue beyond the old two-rejection/three-attempt limits. Inspection tools provide
pageable evidence in addition to compact initial summaries. The conversation's image allowance
defaults to one million estimated image tokens and is configurable through its Python API;
model-call spending and token limits still apply where configured.

Automatic evidence sampling selects eight documents when available and expands to cover every
detected structural pattern. An explicit positive `review.sample_files` count overrides that
selection. Sampling affects investigation and visual review; every delivered file still receives
the independent verification suite.

## Immutable outcomes and adaptable methods

The approved contract describes what the final document must contain. Each document receives its own
source-bound target evidence; the implementation can change without weakening the result. The current
independently verified effect families are:

| Effect | What is fixed by approval |
| --- | --- |
| Formatting | Font, size, supported run/paragraph properties, scope and target count |
| Section-title role | The title associated with that source's SECTION marker, including table layouts |
| Text replacement | Exact old/new wording, scope, match count and tracked-change choice |
| Paragraph insertion/deletion | Exact inserted wording or selected deletions, position, scope and tracking |
| Page setup | Section selection (including content selectors), margins, page size, orientation and different-first-page choice |
| Header/footer template | Template SHA-256, section mapping, fills and alignment rules |
| Header/footer removal | Sections, header/footer kinds and reference types |
| Paragraph style and style mapping | Source selection, destination styles, import policy and pinned template |
| Proofing language | Exact language tags and optional existing-language filter across the document |
| Table properties and style | Source table selection, explicit formatting/style settings and any pinned template |
| Automatic and typed numbering conversion | Source numbering, destination level styles, conversion policy and any pinned exemplar |

The additional effect types are `paragraph_style`, `style_mapping`, `proofing_language`, `table_format`,
`table_style`, `numbering`, and `typed_numbering`. Each uses explicit `select` and `params` settings from the
corresponding operation and `expected_count`: an exact count or `"all"` original matches. For `"all"`, each file's
binding freezes its own count. Template paths and SHA-256 hashes live on the effect, not inside `params`.
The planner's read-only `inspect_goal_effect` tool returns count evidence for one document within the approved
planning scope. It does not approve the effect or edit the document.

These effects require stable paragraph and package structure. Wording changes, paragraph insertions/deletions,
and header/footer replacement/removal must be approved separately when combined with them. Final independent
checks may refuse overlapping effects whose joint result cannot satisfy both requirements. A source-bound count,
template pin, or final effect is never dropped to make a candidate pass. [Built-in workflows](BUILTIN_WORKFLOWS.md)
provide a separate keyless way to configure common chains using the existing preview and approval path.

Within one outcome contract, table styling can combine with repeat-header-row and row-splitting settings.
Table styling plus borders, margins, width or cell overrides requires separate approved goals because the
independent proof checks each original table property container as a whole. The built-in table workflow handles
that sequence as an ordinary two-stage chain with per-stage verification.

Proofing language can combine with page setup, table properties and paragraph-only formatting; run/font formatting
and style changes require separate goals. An effect that clears direct formatting can combine only with page setup.
These combinations are checked before approval so a plan does not promise an unsupported joint proof.

For example, this applies the same role to differently titled specifications:

```json
{
  "kind": "document_effects",
  "effects": [{
    "id": "section-title",
    "type": "format",
    "target": {
      "scope": ["document"],
      "locators": [{"role": "section_title"}],
      "cardinality": 1
    },
    "font": {"name": "Arial", "size": 11},
    "run": {"b": true},
    "tracked": false
  }]
}
```

Locators can also use exact text, contained text, regex, explicit style IDs, table membership and
neighboring text anchors. A style hint is supporting evidence; an explicit style-ID predicate is a
requirement. Ambiguity becomes a recorded question. To choose the exact targets of one effect:

```text
spec-surgeon workspace bind "editing-workspace" --document DOCUMENT_ID --effect EFFECT_ID --candidates CANDIDATE_ID
```

Agents may replan built-in operations, build a sequence, or generate a sandboxed custom method.
Custom methods are available automatically for independently supported contracts; `--no-codegen`
disables them. Tasks without that contract (an EditSpec or chain alone) keep their stricter custom-method policy. A separate
critic reviews model-authored candidates and can request more work, but cannot certify an output
that fails verification. Dynamic read-only analysis and persisted evidence are also available while
diagnosing unsupported structures.

Original-to-final verification independently resolves the approved targets and checks the desired
effects, literal wording, revisions, package relationships, template media/styles and all preserved
content. It does not trust a method's account of its own edits. Arbitrary outcomes outside these
verified families remain unresolved with an explanation; agents cannot silently replace them with
a weaker supported request. Difficult files may still need an explicit target choice, a new reviewed
plan, source repair, or a future verification capability.

The section-title role recognizes six-digit SECTION markers and their supported adjacent/table/inline
title layouts. It does not guess titles in unmarked documents. Those cases can use a reviewed exact,
regex, style or anchored locator after investigation.

The current template boundary supports images and external hyperlinks. Embedded objects, charts and
other internal template dependencies require additional graph verification; text-box fill slots are
also unsupported. Templates cannot import a missing style when that would change an existing body or
numbering reference. Effects that edit a header/footer being replaced by a template are refused in the
same contract. Tracked run-format changes combined with text editing in the same paragraph also need
additional composition coverage. These cases remain explicit unresolved work rather than disappearing
from the requested outcome.

## Explicit section-title compatibility goal

The earlier compact goal remains available for an explicitly named title. Save
this as `title-goal.json`:

```json
{
  "kind": "format_section_title",
  "target": {"title": "FIRE SUPPRESSION", "section_number": "21 13 13"},
  "run": {"b": true}
}
```

```text
spec-surgeon workspace goal "editing-workspace" --project PROJECT_ID --request "Make the FIRE SUPPRESSION section title bold" --adaptive @title-goal.json
spec-surgeon workspace run "editing-workspace" --no-render
```

The title is explicit approved text. The resolver locates it in each document, including table
paragraphs, and compiles a version-bound target to ordinary verified operations. An optional
explicit `style_id` remains a required predicate. Ambiguous matches require a recorded target
choice rather than editing every plausible title. Source changes invalidate the binding.

Use the document and candidate IDs in the saved question to make that choice:

```text
spec-surgeon workspace bind "editing-workspace" --document DOCUMENT_ID --candidate CANDIDATE_ID
```

Independent final checks compare the original and candidate against the target contract. All
ordinary package, text, revision, and preservation verification remains in place. This goal
does not use a language model and works without an API key.

Recovery of an EditSpec or chain without an independent goal contract still pins its operation effects. Additional
tokens cannot bypass independent verification or authorize invented document wording.

## Long investigations and evidence

Provider histories, including signed reasoning blocks and paired tool results, are preserved intact.
When the configured working context is reached, the program seals that epoch and starts a new
conversation with the approved contract, source pins, decisions and diagnostic excerpts. Agents can
page through the complete archived exchange. The archive is evidence, never a new source of authority.

Model-written analyzers operate on copied, pinned inputs in an isolated process. They cannot modify
the original documents or access unrelated files, network connections, or subprocesses. Linux uses a
syscall allowlist (requiring `libseccomp.so.2`); Windows uses a Job Object plus restricted-language and audit guards. Unsupported
isolation environments refuse execution. Analysis results are content-addressed, paginated and checked
for source drift after reopening.
Workspace-specific generated methods remain in their investigation with their contract, bindings and
trial records. The general saved-operation library currently refuses to save or load those methods
without their workspace authority; ordinary standalone formatting operations retain their library workflow.

## Review and export

```text
spec-surgeon workspace export "editing-workspace" --out "reviewed-copies" --yes
```

Only ready, verified candidates are exportable. `--yes` approves export; it does not acknowledge
blocking visual findings. Use `--ack-review` only after inspecting them. Outputs are organized
by project so same-named documents from different jobs remain distinct.

The source hashes, verified candidate bytes, and current goal remain part of delivery checks.
Rendering and model visual review supplement the independent verifier and human review of the
complete intended result.

Automated tests use synthetic DOCX files and scripted model replies. Real-project repair quality,
live model economics, and Microsoft Word rendering fidelity still require the separate
[real-batch evaluation](RECOVERY_EVALUATION.md).
