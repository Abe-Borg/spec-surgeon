# Built-in workflows

Available starting in Spec Surgeon 1.0 (v1.0.0).

Built-in workflows are a command-line feature. They turn common combinations of operations into named starting
points. They ship with the installed package and work without an API key or a previously populated personal
library. Each produces an ordinary chain: preview every stage, inspect the proposed changes, approve execution, and
receive independently verified copies.

The conversation window has no workflow picker and no operation browser. There you attach the documents, type the
result you want, and the app compiles the operations itself; see [Reliability and approved outcomes](RELIABILITY.md).
`spec-surgeon ops --catalog` lists every operation offline, with its selectors, parameters and limits.

`workflows use` loads a workflow with its inputs, previews it, and runs only after confirmation. `--save-as NAME`
saves the configured workflow as a chain in your library (`spec-surgeon library`). A template is an input to the
preview's approval digest and is excluded from the edited file set.

| Workflow ID | Purpose | Explicit choices |
| --- | --- | --- |
| `architect-styles` | Map architect styles to office styles | Example document, source/destination map, regions, existing-style policy; unused-style removal is optional |
| `match-numbering` | Convert automatic numbering to an example scheme | Example document, level styles, source numbering, optional level mapping and direct-formatting policy |
| `format-tables` | Apply a table style and then layout settings | Table selection, style, optional example document and exact properties |
| `proofing-language` | Set Word's proofing language | Language tag and optional existing languages to replace |

## Command line

```text
spec-surgeon workflows list
spec-surgeon workflows show architect-styles
spec-surgeon workflows show format-tables --json
```

`show` lists every input and default. Pass inputs as an inline JSON object or `@inputs.json`. Template paths in an
input file resolve relative to that file, so a saved configuration can live beside its example document.

For office styles, an input file might contain:

```json
{
  "template": "Office styles.docx",
  "style_map": [{"from": "ArchitectHeading", "to": "OfficeHeading"}],
  "existing": "keep",
  "scope": ["document"],
  "unused_styles": "keep"
}
```

Use the actual style IDs or names from your documents. `existing: "replace"` explicitly takes definitions from the
example even when the destination already contains that style. `unused_styles: "custom"` requests a second stage
removing unused custom styles after mapping; the default removes none. Preview flags formatting that prevents a
requested style from appearing as expected.

```text
spec-surgeon workflows use architect-styles "Project specs" --inputs @office-workflow.json --out preview-office
spec-surgeon workflows use architect-styles "Project specs" --inputs @office-workflow.json --out run-office --yes --save-as "Office style conversion"
```

Without `--yes`, a noninteractive command stops after preview. In an interactive terminal it asks for confirmation.
The approved run checks source and template identity again. Outputs are copies by default; `--in-place` explicitly
requests the existing guarded write-back behavior. `--no-render` skips visual rendering and records that limitation;
`--no-review` skips the optional model visual review. Deterministic verification still runs on every output.

A small proofing-language configuration needs only:

```json
{"language": "en-GB"}
```

This changes spelling metadata, not the document's wording. Table and numbering inputs are deliberately explicit:
inspect `workflows show ID` instead of assuming a universal office numbering scheme, table width or style name.

## Workspace planning

`spec-surgeon workspace plan` uses reviewed outcome contracts (see [Adaptive workspaces](ADAPTIVE_WORKSPACES.md)).
The expanded effect families are `paragraph_style`, `style_mapping`, `proofing_language`, `table_format`,
`table_style`, `numbering` and `typed_numbering`. Page setup also supports a different first page and selection by
section content.

Each new effect records explicit selection rules and settings, a target count, and the SHA-256 of any attached
template. `expected_count: "all"` approves all original matches while each document retains its own source-bound
count. An exact integer requires that count. Planning inspection can determine counts without editing the files or
granting approval. The plan shows the complete settings and template pins before approval.

The independent verifier re-resolves the approved selection and requested result against the original source.
Changing the implementation does not authorize extra edits or drop a requested effect. Some combinations require
separate reviewed goals when overlapping edits cannot be checked independently; the planner must explain those
limits rather than silently approximate. Numbering whose display cannot be predicted is still refused or flagged
according to the operation's existing rules. Microsoft Word appearance requires human review.

Built-in workflows use the ordinary chain path. Adaptive outcome contracts provide an additional original-to-final
check for supported workspace goals; a workflow's per-stage PASS alone does not claim arbitrary natural-language
request satisfaction. Use the [evaluation workflow](WORKFLOW_EVALUATION.md) to measure human-accepted results on real
project batches before claiming time or cost savings.
