# Template compatibility and generated recovery

A template created in another Word language can use `Encabezado`, `Piedepgina`, and
`Fuentedeprrafopredeter` where a destination uses `Header`, `Footer`, and
`DefaultParagraphFont`. These are document-local identifiers. Treating the missing
localized ID as a new document-wide default made the old importer refuse an otherwise
usable template. Turning style import off could not resolve the references either.

`apply_header_footer_template` now reconciles style identities automatically:

1. A default style maps to the destination's unique default of the same type.
2. Other styles identify a candidate by exact ID with matching type/name, or a unique matching type/name.
   Names are compared with normalized whitespace and case.
3. Reciprocal links identify localized character styles through their matched paragraph style.
   Candidates are reused only when their inherited formatting outside the properties the operation
   pins is compatible. Borders, shading, underline and other unpinned properties must match.
   Table styles (including conditional formatting) and styles used in nested paragraphs require
   complete formatting compatibility because ordinary paragraph/run pins do not cover them fully.
   Incompatible candidates are imported under private IDs instead. The comparison retains inheritance
   layers conservatively rather than flattening properties whose inheritance semantics are not modeled.
4. Dependencies of a reused destination style stay with that destination definition. Only
   dependencies needed by imported definitions are copied.
5. Unrelated ID collisions and dangling destination references reserve their IDs. Imported
   styles receive deterministic private IDs, and ambiguous names receive distinct names.
   Filling a dangling body reference must never change the body as a side effect.
6. If the destination has no matching default, a required template definition may be copied
   as a nondefault style. An import never introduces a document-wide default.
   Copied paragraphs, runs, paragraph marks and tables that used an imported default implicitly
   receive explicit references to its private copy. The destination's own defaults stay unchanged.

The operation rewrites references in copied parts and imported definitions, then calculates
formatting pins against the mapped destination styles. It records the mapping in the plan.
Existing destination definitions and the template file are not rewritten. Setting
`import_missing_styles: false` still permits equivalent-style reuse; it forbids new definitions.
Malformed duplicate IDs, ambiguous default roles, and unsupported numbering dependencies
remain explicit failures rather than guesses.

The transformation and verification implementations are separate:
`ops/template_styles.py` and `verify/template_styles.py`. V10 derives the allowed mapping
and exact imported definitions from the two original inputs. It checks rewritten references
and effective properties against the original template, and ties dependency roots to copied
parts rather than arbitrary roots in a generated plan. The final outcome verifier derives
the selected template parts and complete imported graph again, without using the transform plan.
Tests include a deliberately incorrect mapping shared by planning and application; verification
rejects it.

## Resources available to agents

`inspect_template_styles` is a read-only tool in compilation, recovery, project planning and
code generation. It returns mappings, required imports and source/template hashes. It is a
style preflight; the actual operation still validates selected sections, slots, fields and
the entire package. Different style IDs alone do not justify asking for a rebuilt template.

Generated methods with an approved independent outcome contract can read the approved
templates in both `plan` and `apply` through `pkg.inputs[absolute_template_path]`. Values are
immutable bytes, supplied from snapshots whose SHA256 matches the contract. The writer can
also inspect those templates with the existing XML tools. Ordinary filesystem access is not
added. Drift, an unapproved path, or changing the input mapping is rejected.

Governed writer instructions include the independent assertion kind names and, for a
header/footer goal, the template operation as implementation reference. Pure style, slot,
formatting and package-graph helpers are available through its import whitelist. The old formatting-only
instructions apply to legacy custom transforms without an outcome contract. A legacy fallback
limit no longer produces the blanket statement that a new built-in is required. The legacy
chat and project recovery remain separate entry points; this change does not silently approve
or execute a new project goal from a rejected chat recipe.

This extends a supported class of recovery, not arbitrary self-modification of the verifier.
An agent may adapt its method and use supplied resources, while the approved outcome and
independent checks remain fixed. A missing verification capability must be identified
specifically; additional model attempts cannot create that capability by themselves.

## Validation

Automated coverage uses synthetic documents for localized IDs in both directions, translated
linked names, absent defaults, existing equivalent styles with import disabled, conflicting IDs,
ambiguous names, dangling body references, forged mappings, input drift, and sandbox input access.
An adapted template method also completes a real transplant in the sandbox and passes the
independent outcome check; a method that only inspects inputs fails that same check.
No client template or specification is committed as a test fixture. Model orchestration tests
use scripted clients and do not establish live-provider performance or Microsoft Word rendering.

Validation on 2026-10-18 covered 3,107 collected cases across the full run and follow-up
runs: 3,106 passed; Windows Credential Manager was the only remaining skip. The initial
run exposed two tests tied to the previous refusal/diagnostic behavior and missing optional
Qt dependencies. The tests now check successful isolation plus rejection of a deliberately
unsafe output, and all previously failed/skipped cases were rerun after installing Qt.

The original supplied template also passed structural and independent outcome verification
unchanged on 12 synthetic specifications with different fonts, margins, section counts,
destination style identities and old header/footer logos. A one-page LibreOffice rendering
matched the manually normalized template control pixel for pixel. These were generated test
specifications, not the user's original batch of 12 specifications.

PR review additionally covers same-name styles with different inherited borders, shading,
underline, table/conditional formatting and nested-paragraph properties. A deliberately faulty
transform that skips compatibility or omits explicit private-default bindings is rejected by
both the ordinary suite and the independent final-outcome verifier. CI portability fixes retain
the original fixture ZIP bytes for the unchanged-template test and read source/JSON as UTF-8.
