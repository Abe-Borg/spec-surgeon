# Reviewed office profiles

An office profile packages an original Word template with explicit role-to-style
mappings and optional office policies. It is portable data: it does not store code,
model instructions, credentials, absolute template paths or execution approvals.
Reference documents and target documents remain unchanged; applications deliver
verified copies.

## Desktop and command line

Select the target files in the main window, then open **File → Office profiles…**.
Choose **Create profile…**, select a reference document, and map the roles to explicit
office styles. The form also offers explicit margins for every section. **Open profile…**
loads an existing bundle; **Import…** and **Export…** move it as a ZIP. Advanced policy
settings use the CLI/API and can then be loaded in the desktop.

Choose **Analyze and review**. Each row shows the original paragraph and proposed role;
unresolved rows require a role or **Leave this paragraph unchanged**. After changing choices,
analyze again. Approve the complete review, choose **Preview**, and inspect the records and
rendered pages through **Open results / preview pages**. The second approval enables
**Run verified copies**. Changing the scope, source, profile, output location or rendering
settings requires the corresponding fresh review or preview. Every run uses a new folder.

Equivalent commands (replace the two approval placeholders with the inspected digests):

```sh
spec-surgeon profiles create reference.docx --name "Office standard" --roles @roles.json --direct-formatting replace_style_owned --out profiles/office
spec-surgeon profiles review profiles/office specs/ --out office-review.json
spec-surgeon profiles preview office-review.json --out office-job
spec-surgeon profiles run office-review.json --out office-job --approval REVIEW_DIGEST --preview-approval PREVIEW_DIGEST
spec-surgeon profiles export profiles/office --out office-profile.zip
spec-surgeon profiles import office-profile.zip --out profiles/imported-office
```

`roles.json` can contain `{"part":"Office Part","article":"Office Article","paragraph":"Office Paragraph"}`.
Use `--choices @choices.json` on `review` to resolve uncertain paragraphs; the format below
maps absolute source paths to zero-based paragraph choices. `--policies @policies.json`
on `create` supplies the policy array. For explicitly unrendered execution, use `--no-render`
on both `preview` and `run`; this does not establish visual acceptance.

The CLI regenerates preview evidence before execution and refuses a changed approval digest.
Its approvals cover document content, all rendered pages, checks and the application build.
Office profiles do not call a model for visual review; reports explicitly record this limit.

## Selective direct formatting

`keep` leaves direct formatting in place. `clear` uses the existing eligible-formatting
cleanup rules. `replace_style_owned` removes only eligible direct properties explicitly
supplied by the destination style or its `basedOn` ancestors. It resolves the final imported
style definitions, not document defaults or a linked character style. Font slots, spacing
and indent attributes are handled individually, so unowned attributes remain.

Hidden text, symbol fonts, superscripts, highlighting, numbering and revision protections
remain. Borders and tab stops with partial or uncertain coverage are preserved and flagged.
The verifier derives the expected removals independently from the original properties and
destination styles. This policy currently applies to paragraph `apply_style`, not table or
numbering operations.

## Create and move a profile

The Python API is in `spec_surgeon.office_profiles`:

```python
from pathlib import Path
from spec_surgeon.office_profiles import (
    create_profile, export_profile, import_profile, load_profile,
)

profile = create_profile(
    Path("profiles/office"),
    Path("reference.docx"),
    "Office specification standard",
    {"part": "Office Part", "article": "Office Article", "paragraph": "Office Paragraph"},
    direct_formatting="replace_style_owned",
    policies=[{"type": "page_setup", "margins": {"left": 1440, "right": 1440}}],
)
export_profile(profile, Path("office-profile.zip"))
portable = import_profile(Path("office-profile.zip"), Path("another-location/office"))
assert portable.digest == profile.digest
```

`OfficeProfile` exposes `root`, `manifest`, `name`, `digest`, `template`, and
`asset_path(asset_id)`. `load_profile(path)` verifies the complete bundle whenever
it is used. The manifest contains a schema version, original filename, size and
SHA256 for every asset, a role/style map, direct-formatting and tracking choices,
and declarative policies. Additional `.docx`/`.dotx` references may be supplied in
`assets={"numbering": Path("numbering.docx")}`. A templated policy can name that
reference with `template_asset: "numbering"`.

Creation/import stage and validate the complete bundle before publishing a new
directory. Export creates a new ZIP atomically. Existing destinations are refused.
Imports reject traversal paths, symlinks, duplicate/case-colliding members,
undeclared files, excess members and excess expanded sizes. Every loaded asset
must be an original readable Word package with the recorded bytes. Modifying a
bundle's template invalidates it; create a new profile version instead.

Role keys are the structure analyzer's `section_id`, `section_title`, `part`,
`article`, `paragraph`, `subparagraph`, `subparagraph_2`, `subparagraph_3`,
`subparagraph_4`, and `end_of_section`. Destination values are literal paragraph
style IDs or names present in the template. Existing destination definitions are
replaced with the template definitions under the engine's style-import rules.
Replacing a document's default style is refused; use explicit office styles.
Style definition replacement also affects other existing uses of those styles
and inherited formatting; the review's requested effects record this choice.

## Review exact targets for each application

```python
from spec_surgeon.office_profiles import prepare_review, review_text, save_review

sources = [Path("specs/section.docx").resolve()]
review = prepare_review(profile, sources)
print(review_text(review))
save_review(review, Path("reviews/office-review.json"))
```

The complete review includes every paragraph's source part, zero-based internal
index, original text, proposed role, evidence and disposition. Displayed paragraph
numbers start at one. `review["documents"]` contains each source's identity,
analysis, targets, unresolved paragraphs, manual choices, contract, binding and
compiled chain. `status` is `PROPOSED` or `NEEDS_INPUT`; the latter is not executable.

Plain prose and uncertain hierarchy remain unresolved. Explicitly choose a role
or exclude each unresolved paragraph. There is no inferred fallback style. For
example, this choice excludes the fifth paragraph in that original document:

```python
review = prepare_review(profile, sources, choices={str(sources[0]): {4: "exclude"}})
```

The choice file/API uses zero-based indices. A supported role may be chosen
instead of `exclude`. A region excluded because the analyzer does not support it
(for example a table or drawing/textbox paragraph) cannot be promoted by a manual
choice. The current application requires choices for all unresolved paragraphs,
because their unknown roles could fall in the requested scope. Policy-only
profiles with an empty role map do not require unrelated structural choices.

The classifier proposes meaning. It does not authorize a change and is never
called by the independent outcome verifier. Each selected original paragraph
becomes an exact original paragraph selection. Consecutive paragraphs requesting
identical style parameters and the same template share one half-open
`index_range: [first, last + 1]` effect and chain stage, with an exact target count.
Excluded, unsupported or unmapped paragraphs always break a group. The review
still displays every individual paragraph and its disposition. Contracts are
bound to source SHA256 and requested effects. Existing goal compilation builds
separate ordinary operation stages for those ranges, avoiding overlapping
operations or assumptions about how intermediate styles change selection.

## Preview, approve and execute

```python
from spec_surgeon.office_profiles import preview_review, execute_review

previews = preview_review(review, Path("job/preview"))
# Present review_text(review), every preview and any visual findings to the user.
# Only the user's affirmative choices supply these two approval values:
review_approval = review["digest"]
preview_approvals = {source: preview.token for source, preview in previews.items()}

results = execute_review(
    review, Path("job/run"), approval=review_approval,
    previews=previews, preview_approvals=preview_approvals,
)
```

The assignment of approval values above illustrates API wiring, not permission to
automatically approve. The CLI and desktop require separate outcome and preview
approval. Saving or loading a review does not approve it.

Every source receives its own full-chain preview. In addition to stage V1–V10
checks, the preview independently verifies the approved original-to-final effects
(`OFFICE_OUTCOMES`). Execution checks the review digest, profile, original files,
scope, exact bindings, current previews, preview tokens and render settings again.
Blocking visual findings need a separate current acknowledgement. No classifier
result, successful stage or model opinion exempts the final outcome check.

Execution uses `run_chain(..., in_place=False, goal_contract=..., goal_bindings=...)`
in disjoint `file-0001`, `file-0002`, ... folders. Existing snapshot, source-drift,
verified-output and independent delivery checks remain in force. Only files that
pass every stage and the original-to-final goal are delivered in each chain's
`outputs/`; failures remain in its reports and set-aside records. The root run
folder also retains `office-review.json`.

A run needs a new destination. Inputs, profile assets, previews and run folders
must not overlap. Preview objects are consumed on the first execution attempt,
including a failed attempt; a retry requires a fresh preview and explicit approval.
This workflow does not invoke optional model visual review; every preview records
that it was not run. Inspect the rendered pages before approving execution.
Automated rendering does not establish human visual acceptance or Word-specific
fidelity.

The CLI's preview approval digest covers the reviewed input tokens, current
application Python files, actual resulting document members, verification
statuses/outcomes, actual renderer and every rendered PDF page's pixels. A run
regenerates the preview and refuses if that evidence differs. PDF creation
metadata and ZIP timestamps are excluded because they do not describe document
contents. For newly generated paragraph/run formatting revision markers only,
their creation dates are normalized so a later execution can reproduce an
approved tracked preview. Existing source and reference revision dates, marker
IDs, authors, contents and all other member bytes remain in the evidence. If a
new marker ID collides with one in a reference document, its date stays literal;
a changed date then requires a fresh approval rather than being ignored.

## Optional policies and current composition limits

Policies use the existing approved-effect grammar, omitting `id`, `expected_count`,
`template`, and `template_sha256`. A fresh review supplies those source counts and
template pins. Supported policy types are:

| Policy | Example fields |
| --- | --- |
| `page_setup` | `sections`, `margins`, `size`, `orientation`, `different_first_page` |
| `header_footer_template` | `params` containing existing template choices and fill rules |
| `table_format` | `select`, `params` containing borders, margins, width or row policies |
| `table_style` | `select`, `params.style`, optional `template_asset` |
| `proofing_language` | `params.language`, optional East Asian/complex-script language |
| `numbering` | `params.levels`, source/level mapping, optional `template_asset` |

No arbitrary recipes, executable paths or external template paths are accepted.
Use at most one policy of each type; combine its settings in that policy. Creation,
import and loading validate the complete combination before publishing or using a
profile. Unsupported combinations are rejected with a reason and a request to use
separate profiles. Source-specific method gaps still return `NEEDS_INPUT` during
the fresh review; successful creation never guarantees every source can be edited.

The supported composition subset is:

| Settings | Permitted companions |
| --- | --- |
| Role mappings with `clear` or `replace_style_owned` | Other disjoint role targets and page setup |
| Role mappings with `keep` | Page setup, table style, and table formatting; table style can accompany only row-level table formatting |
| Header/footer template | Page setup; explicit header/footer distances require `align_header_footer_distance: false` |
| Proofing language | Page setup and table formatting |
| Table style | Page setup and row-only `repeat_header_rows`/`cant_split` table formatting, when its direct-formatting policy is `keep` |
| Numbering | Page setup only, with an empty role map |

A role's destination style must exist, be a nondefault paragraph style, and have
no numbered style in its `basedOn`/`link`/`next` dependency graph. Repeated template
imports of numbered styles cannot yet establish one original-to-final numbering
graph. Use nonnumbered role styles and apply a separate numbering-only profile.
Numbering profiles validate their bundled destination scheme at creation. Table
style policies likewise require a matching table style in their chosen asset.

These limits are never converted into recipe-only execution. The reference
repositories are neither imported at runtime nor modified. Saved reviews must use
`.json`; an existing destination is replaceable only if it is a valid office review,
protecting documents and unrelated configuration files from accidental overwrite.
