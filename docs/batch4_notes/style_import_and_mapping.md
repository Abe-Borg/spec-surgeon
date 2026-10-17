# Batch 4, item 31 ★ — shared style import (§5) and `map_styles` (§7): integration notes

Built on c3f6f82 (the batch-4 groundwork). Files: `src/spec_surgeon/ops/style_import.py`, `src/spec_surgeon/ops/map_styles.py`,
`src/spec_surgeon/verify/styleimport.py` (V10 `styles_imported`), `src/spec_surgeon/verify/restyle.py` (V10 `mapped_styles`,
`v_resolve_style`), the registration lines in `ops/__init__.py` and at the end of `verify/props.py`, and
`tests/test_style_import.py` (41 tests) + `tests/test_map_styles.py` (22 tests). Nothing in catalog.py, compiler.py,
report.py, profile.py, README.md, CLAUDE.md or examples/ was touched — the texts for them are below.

**One existing test fails until (a) is integrated:** `tests/test_planner.py::test_catalog_and_schema_cover_every_op`
requires a non-empty `COMMON_ASSUMPTIONS[...]` for every registered op (catalog.py, not mine to edit). With the entry
below it passes.

## (a) `catalog.COMMON_ASSUMPTIONS["map_styles"]`

```python
    "map_styles": [
        "every use of the style changes: paragraphs whose style it is (with the default paragraph style — Normal — also "
        "the paragraphs that name no style), runs and paragraph marks in a character style, tables in a table style, in "
        "the body, headers, footers, footnotes, endnotes and comments unless a scope is given",
        "your styles come from the template file: a style this file lacks is imported with the styles it is based on, "
        "linked to and followed by, and its list numbering (copied with new list ids); a style this file already has keeps "
        "this file's definition unless existing is 'replace'",
        "nothing else changes: the architect's style definitions stay (removing unused styles is its own operation, a "
        "second stage), and field codes that name a style (a TOC \\t switch, STYLEREF) are flagged, not edited",
        "a style the file lacks, or that nothing uses, is skipped and noted (allow_missing); a 'to' of another type, or a "
        "name two styles share, sets the file aside",
        "plain formatting (a whole-document change); tracked on request — one Formatted: Style revision per paragraph, run "
        "or mark (a table's style cannot be tracked)",
    ],
```

## (b) Guidance paragraph for the compiler's SYSTEM prompt

```text
- Style mapping ("the architect's Heading 1 becomes our PRT", "use our office styles"): map_styles with map [{from, to}]
— from: the architect's style ids from styles_in_use (or their names), to: the user's style. "Your/our styles" come from
a template file listed among the INPUT FILES or the attached template spec: put its path in template; a to the file
lacks is imported with what it needs (based-on / linked / next styles and its list numbering). existing "keep" (default)
leaves a style the file already has as the file defines it; "replace" only when the user says their definition must win.
A default paragraph style as from (Normal) maps every paragraph without a style too — name it only when the user means
the body text. Mapping changes the paragraphs' style: set_properties, set_tab_stops, set_paragraph_props or
apply_style on the same paragraphs, and any change of the from/to style definitions, are separate specs (offer a chain);
removing the now-unused architect's styles is a second stage (remove_unused_styles), never the same spec. Field codes
naming a mapped style (TOC \t, STYLEREF) are flagged, not edited — say so in assumptions. Untracked by default; tracked
only on request.
```

## (c) README section (user's view)

~~~markdown
#### `map_styles` — the architect's styles become yours (item 31 ★)

```json
{"op": "map_styles",
 "params": {"map": [{"from": "Heading 1", "to": "PRT"}, {"from": "Arch Article", "to": "ART"},
                    {"from": "Arch Strong", "to": "Char Mine"}, {"from": "ArchTable", "to": "TblMine"}],
            "template": "OFFICE STYLES.docx"}}
```

* **`map`** — pairs of styles, by id or by name (a name is matched case-insensitively; two styles of one name set the file
  aside — name it by its id). **Every use changes**: a paragraph style on every paragraph whose style it is (the default
  paragraph style — Normal — also on the paragraphs that name no style), a character style on every run and paragraph mark,
  a table style on every table — in the body, headers, footers, footnotes, endnotes and comments (`select.scope` narrows
  it). Nothing else: the architect's style definitions stay where they are (removing unused styles is its own operation, a
  second stage of a chain), and so do `basedOn`/`next` links that name them.
* **`template`** — your styles. A style the file lacks is **imported** with everything it needs: the styles it is based on,
  linked to (a paragraph style's character twin) and followed by, and — for a numbered style — its list: the template's
  `w:num` and `w:abstractNum`, copied into the file's `numbering.xml` with new ids (above the file's own, so they never
  collide), and the styles that list's levels name. A style the file already has **keeps the file's definition**
  (`existing: "keep"`, the default) or is **replaced in its place** by yours (`existing: "replace"`). The template is an
  input like a template spec: pinned by md5 at plan time, refused at apply if it changed (`internal`), re-read by the check,
  hashed by the preview's approval token; a relative path is the spec's folder.
* **What is skipped**: a pair whose `from` the file lacks, or that nothing uses, is skipped and noted (`allow_missing`,
  default true; false refuses `not-found`) — and imports nothing. A pair whose two styles are one is skipped.
* **What sets the file aside**: a `to` of another type than its `from` (`style-mismatch`); the default character or table
  style as `from` (text without a style uses it implicitly — nothing to map); two pairs naming one style; importing a style
  whose name another style holds (`style-import` — Word finds styles by name); replacing a default style; a list the import
  cannot copy (`style-numbering`: no numbering part in the file, a picture bullet, a list defined through a numbering style).
  A `to` the template lacks is a spec error (`bad-params`).
* **Flagged for review**: field codes naming a mapped style (a TOC `\t` switch, STYLEREF) — not edited, they no longer find
  the paragraphs until you edit them; runs still in the mapped style's linked character style ("Heading 1 Char"); an
  imported style based on a style this file defines differently from the template (it renders on the file's own);
  `word/stylesWithEffects.xml` (Word 2010's copy) not updated; earlier tracked formatting changes on restyled paragraphs.
* **Plain** by default — a whole-document change; `tracked: true` records one *Formatted: Style* revision per paragraph,
  run or mark (`w:pPrChange` / `w:rPrChange`, the snapshot the old properties); a table style cannot be tracked here.
* **Overlaps**: a paragraph's style decides every inherited property, so `set_properties`, `set_tab_stops` and
  `set_paragraph_props` on the same paragraphs, a change of a `from`/`to` style definition, removing a style an imported one
  relies on, and the header/footer templating op (which plans from the styles part as a whole) are plan-time `conflict`s
  — separate specs, a chain.
* **Verified by** V10's `mapped_styles` (`verify/restyle.py`: the pairs resolved, every paragraph, run, mark and table
  re-derived and its change checked exactly — `w:pStyle` first in the pPr, everything else identical, the change markers
  by the shared rules) and `styles_imported` (`verify/styleimport.py`: the closure, the imported and replaced styles and the
  new list ids re-derived from the template; each copy compared whole with the template's definition, imported styles
  appended in the template's order, replaced ones in their old place; `numbering.xml` compared whole with the file's own
  plus exactly the copied lists in their places). Nothing is shared with the op.

Refusals in plain words: `style-mismatch`, `style-import`, `style-numbering` (`report.FAILURE_EXPLANATIONS`).
~~~

## (d) CLAUDE.md both-sides definition entries

```markdown
- **Style import** (built-in batch 4 §5; shared by `map_styles` and `apply_style`). Resolution of a style reference: exact
  `w:styleId`, else the one `w:name` equal casefolded (two → `style-mismatch`), else none. Copied = imported ∪ replaced.
  Closure: the requested template styles; for every COPIED closure style its `basedOn`/`link`/`next` targets and the
  `w:pStyle` of every level of its numbering definition (`abstractNum/lvl`, `num/lvlOverride/lvl`), to a fixpoint; a style
  the document holds and does not replace is in the closure (held) but not walked; a reference the template does not
  define is skipped. Imported = closure styles the document lacks (template order); replaced (`existing: "replace"`) = the
  requested styles the document holds; refusals `style-import` (no styles part, an imported template default, a replaced
  style that is a default on either side or of another type, a copy's casefolded name another style holds after the import,
  an extension element carrying text) and `style-numbering` (no numbering part, a numId not a whole number, num/abstractNum
  missing, `numStyleLink`/`styleLink`, `lvlPicBulletId` in a level or a `lvlOverride` level). New ids: copied styles in
  template order, each template numId first met → the document's largest numId + 1… (none → 1), its abstractNum first met
  → largest abstractNumId + 1… (none → 0). Copies: extension markup stripped, ids set. Places: abstractNums after the last
  abstractNum (else before the first num, else before `numIdMacAtCleanup`, else at the end), nums after the last num (else
  before `numIdMacAtCleanup`, else at the end); imported styles appended, replaced in place. Transform:
  `ops/style_import.resolve_style/closure_of/style_links/numbering_styles/own_num_id/check_definition/next_ids/allocate_ids/
  build_copies/insert_numbering/plan_import/apply_import`; verifier: `verify/restyle.v_resolve_style`,
  `verify/styleimport.v_derive_import/_v_refs/_v_levels/_v_num_id/_v_lists/v_expected_numbering/check_styles_imported`.
  The style copies use `apply_header_footer_template.strip_foreign` ↔ `template.strip_extensions`.
  `tests/test_style_import.py` compares the resolvers on every style of both fixtures, the derivations on 14 requests ×
  2 documents and the placements on 6 numbering shapes, and plants shared defects (closure without link/next, without list
  levels, walking held styles; an allocator reusing ids).
- **Style mapping** (`map_styles`, §7). Pairs: one `from` twice or a `to` that is another pair's `from` (as written) →
  `bad-params`; with a template every `to` resolves in the template (none → `bad-params`); per file `from` resolves in the
  file (none → `missing`), the style mapped to = the template's (replace) / the file's when `to` resolves there, else the
  template's, else `missing-style` (keep); `from` = it → `same`; one type of paragraph/character/table, no default character
  or table style as `from`, no two `from` resolving to one style, no style mapped to that is another's `from` →
  `style-mismatch`. Elements: paragraphs whose style (`w:pStyle/@w:val`, else the default paragraph style) is a paragraph
  `from`; runs (nearest paragraph) and paragraph marks whose `w:rStyle` is a character `from`; tables whose
  `w:tblPr/w:tblStyle` is a table `from`; a pair without elements is `unused` and imports nothing. The change: `w:val` set
  (a new `w:pStyle` first in the pPr, a pPr first in the paragraph); tracked: `pPrChange` / `rPrChange` with the source
  snapshot. Transform: `ops/map_styles.clean_params/paragraph_style_of/run_style_of/table_style_of/mapped_holders/
  set_paragraph_style/set_run_style`; verifier: `verify/restyle._v_map/_v_pairs/_v_holders/_v_rstyle/_v_tblstyle/
  _check_paragraph/_check_rpr/_check_table/_mapped_styles`. `tests/test_map_styles.py` plants shared defects (resolution by
  id only, unstyled paragraphs forgotten for the default style, marks forgotten, tables forgotten, a tracked default, a
  default scope without the headers).
```

Rule-9 text for CLAUDE.md (the batch-4 paragraph of rule 9): `map_styles` touches `(part, bi, "pPr")` and
`(part, bi, "pStyle")` per restyled paragraph, `(part, bi, "rPr")` per paragraph with restyled runs, `(part, bi, "markRPr")`,
`(part, "tbl:<k>", "tblPr")`, `(part, "scope", "verify")` per part in scope, and reads `(styles, "style:<id>", "verify")`
of every `from` and of every `to` the document holds; the import touches `(styles, "style:<id>", "add")` per imported
style, `(styles, "style:<id>", a)` for `a` in `replace, rPr, pPr, tblPr` per replaced style, `(numbering, "*", "add")`
when lists are copied, and reads `(styles, "style:<id>", "verify")` of every held closure style (a style the document
already holds that a copy names through basedOn/link/next or an imported list level names) — so `remove_unused_styles` or
a style-definition op on it in the same spec is a plan-time conflict.

## (e) `report.FAILURE_EXPLANATIONS`

```python
    # ---- built-in batch 4 (docs/BUILTIN_OPS_BATCH4_DESIGN.md) ----
    "style-mismatch": (
        "The Styles Named Do Not Fit Together",
        "A style named in the spec does not fit this file: it is a paragraph style where a character or table style is "
        "needed (or the other way round), it is the file's default character or table style (nothing names it, so there "
        "is nothing to map), two styles of this file — or of the template — carry that name, or two pairs end up naming "
        "the same style here. Name the style by its id, or fix the pairs.",
    ),
    "style-import": (
        "A Style Cannot Be Imported From the Template",
        "Bringing the style in would break something: another style of this file already carries its name (Word finds "
        "styles by name), it is a default style (importing or replacing it would change every paragraph without a style), "
        "it would replace a style of another kind, or the template's definition holds extension content with text. "
        "Rename one of the styles in Word, or keep this file's own definition.",
    ),
    "style-numbering": (
        "The Style's List Numbering Cannot Be Copied",
        "The style is numbered and its list cannot be brought along: this file has no numbering part to add it to (a part "
        "is never created), the template does not define the list, or the list uses a picture bullet or is defined "
        "through a numbering style. Apply the numbering in Word first, or map to an unnumbered style.",
    ),
```

## (f) The reuse API for `apply_style`

Transform side (`spec_surgeon.ops.style_import`):

* `resolve_style(styles: Dict[str, w:style], ref: str, where: str = "") -> Optional[str]` — `styles` is
  `StyleSheet(pkg).styles` or `TemplateStyles.sheet.styles`. Raises `JobException("style-mismatch")` on two name matches.
* `TemplateStyles.load(path) -> TemplateStyles` (`bad-params`: missing, not a .docx, no styles part) — `.path` (resolved),
  `.md5`, `.data`, `.pkg`, `.sheet`, `.numbering`, `.order`. `TemplateStyles.from_bytes(data, path)` for a fresh read.
* `style_type(st) -> str` (`w:type`, default paragraph), `style_name(st)`, `is_default(st)`.
* `plan_import(pkg, tpl, requested: Sequence[str], existing: str = "keep") -> StyleImport` — `requested` are TEMPLATE ids
  (resolve first); with `keep`, request only styles the document lacks (a held requested style is simply kept); with
  `replace`, request the template's style even when the document holds it. Raises `bad-params` (a requested id the template
  lacks, a bad mode), `style-import`, `style-numbering`.
* `StyleImport`: `.imported`, `.replaced`, `.held`, `.closure`, `.num_map`, `.abs_map` (template id → new id, strings),
  `.flags` (a copy based on a style the file defines differently; stylesWithEffects.xml; a shared list nsid), `.notes`,
  `.changes` (bool). When `.changes`, the op adds to its fragment: `frag.touches += imp.touches()`,
  `frag.intended_parts += imp.intended_parts()`, `frag.property_assertions.append(imp.assertion())`, `imp.notes`/`imp.flags`,
  and keeps `imp.transform_data()` in its transform data. Touches: `(styles, "style:<id>", "add")` per imported,
  `(styles, "style:<id>", a)` for `a` in `replace, rPr, pPr, tblPr` per replaced, `(numbering, "*", "add")` with lists,
  `(styles, "style:<id>", "verify")` per held closure style. The assertion is
  `{"kind": "styles_imported", styles_part, numbering_part, template, template_md5, requested, existing, imported, replaced,
  num_map, abs_map}`.
* `apply_import(pkg, data) -> None` — call first in `apply` with the stored `transform_data()` (None → nothing); re-reads
  the template, refuses a changed file (`internal`), rebuilds the copies, appends imported styles, replaces in place,
  inserts the lists.

Verifier side:

* `verify.restyle.v_resolve_style(styles: props.Styles, ref) -> Optional[str]` (ValueError on two name matches).
* `verify.styleimport.check_requested(ctx, a, requested, existing, template, errs, tag)` — the op's own kind derives the
  template ids it needed (for apply_style: the applied style, when it comes from the template — every time with
  `replace`, only when the file lacks it with `keep`) and this checks the `styles_imported` assertion with the same
  `op_index` carries exactly them (and the same mode and template), or that there is none when none are needed. The
  `styles_imported` kind itself needs nothing from the op beyond its assertion (`test_the_import_api_and_its_kind_need_
  nothing_from_the_op` runs it with no other assertion).
* `verify.styleimport.v_type(st)`, `v_name(st)`; `v_derive_import(...)` if a kind needs the derivation itself.

## (g) Deviations from the design, and why

1. **The closure walks only through COPIED styles** (imported or replaced). §5 says "basedOn, link, next of every closure
   style"; a style the document holds and keeps is not copied, so its template links do not matter — walking them would
   import copies nobody needs (e.g. the template's Normal handing over to a style the file lacks), and could create a
   one-sided link pair. Held closure styles are still members (they get the `verify` read and the "defined differently"
   flag). Pinned by `test_a_shared_closure_that_walks_through_held_styles_is_caught`.
2. **With a template, every `to` must resolve in the template** (`bad-params` otherwise), even one the file holds under
   `keep`. §7's "not in the template → bad-params" read literally depends on whether this file has the style — a
   file-specific `bad-params`, which the compiler treats as fatal for the whole spec (batch 2 rule 5). Checking the template
   alone keeps the code file-independent; a `to` that is neither in the file nor in the template stays `missing-style` only
   without a template.
3. **Resolution of `to`**: `keep` → the file's style when `to` resolves in the file, else the template's (imported);
   `replace` → always the template's (replaced when the file holds that id, else imported). §6/§7 do not spell out which
   side resolves first.
4. **Two extra `style-import` refusals** for replaced styles: a template definition that is a default (two defaults of one
   type would result), and a template definition of another type than the file's style of that id (every reference would
   point at the wrong kind). The name-clash rule is checked after the import, copies included (two copies of one name, a
   replaced style's new name). §5 names only "a replaced style that is a default in the document".
5. **A pair with no element is `unused`**: skipped like a missing `from`, noted, and it **imports nothing**
   (`allow_missing: false` refuses it, `not-found`). §7 says only missing `from` styles are skipped; importing styles nobody
   is mapped to would add unused styles.
6. **A numbering style (`w:type="numbering"`) as `from`** is `style-mismatch` (it has no paragraph/run/table references).
7. **`w:numId` that is not a whole number** in a copied style refuses `style-numbering`; extension text in a numbering copy
   refuses `style-numbering` (§5 names `style-import` for styles only).
8. **Held closure styles are read with plain `verify`** (every family), per the coordinator's request after the
   remove_unused_styles build: a `set_style_font` / `set_properties` on the file's Normal beside an import into a
   Normal-based style is now a plan-time conflict too. The `styles_imported` kind also checks every held style is still in
   the output.
9. **Field-instruction flag** matches a `from`'s id or name as a whole word, casefolded, in every text part's instructions
   (not only the scope) — headers' STYLEREF fields name body styles.
10. **Parameters** also refused as `bad-params`: unknown selector keys (only `scope`), a non-bool `allow_missing`/`tracked`,
    a `map` entry with keys other than `from`/`to` or empty names, `existing: "replace"` without `template`.
11. **The `stylesWithEffects.xml` flag** is raised only when styles are imported or replaced (otherwise styles.xml does not
    change and the copy stays consistent).

## (h) Tests

63 new tests: `tests/test_style_import.py` 41, `tests/test_map_styles.py` 22. Full suite in the container
(`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest -q -s -p no:cacheprovider`, 2026-10-17): 1660 passed, 4
skipped, 3 failed — `test_planner.py::test_catalog_and_schema_cover_every_op` (needs (a) in catalog.py; it passes with the
entry, checked in memory) and two `test_audit2_fixes.py` sandbox tests whose subprocess imported the MAIN checkout while it
held merge-conflict markers (`/home/user/spec-surgeon/src/spec_surgeon/ops/__init__.py`, line 36); re-run, both pass. So
1663 pass + 4 skip (1600 + 63) once (a) is integrated.

## Fix round (adversarial review, 2026-10-17)

Merged `origin/claude/nifty-curie-cf22ol` (880ac35) first; each finding was reproduced with the reviewer's script before
the fix, and each fix has a test (a planted defect where the rule is new). Tests: `tests/test_style_import.py` 41 → 59,
`tests/test_map_styles.py` 22 → 29.

| # | Fix | Where (transform ↔ verifier) | Tests |
|---|---|---|---|
| C1 | numbering.xml compared whole even when no list is copied: `styles_imported` with an empty derived `num_map`, and `check_requested` when the op needs no import (so apply_style is covered too), require source == output and register the member — unless another `styles_imported` assertion of the plan copies lists (that one compares source + its lists) | `verify/styleimport.check_numbering_unchanged` | `test_c1_*` (4) |
| C2 | a HELD style (requested or closure) that is another style under the template style's id (names by `name_key`) refuses `style-import`, naming both; names now resolve by `name_key` on both sides (whitespace collapsed, casefolded — the import's one way of comparing names), so the same style under a whitespace variant resolves to the file's own and nothing is requested | `style_import.same_style`, `resolve_style` ↔ `styleimport.v_derive_import`, `restyle.v_resolve_style` | `test_c2_*` (3), agreement test |
| C3 | `(styles, "name:<name_key>", "add")` per imported copy and per replaced style whose name changes | `StyleImport.touches` / `new_names` | `test_c3_*` (2) |
| C5a | new numIds/abstractNumIds above every id DEFINED OR REFERENCED (every `w:numId/@w:val` of the text parts, styles and numbering parts, `w:numIdMacAtCleanup`; every `w:abstractNumId/@w:val`) | `style_import.used_ids`/`next_ids` ↔ `styleimport.v_used_ids` | `test_c5a_*` (2) |
| C5b | **refused** (`style-numbering`): a copied list's level naming a HELD style the file numbers with its own list (first numId on its basedOn chain) — it would split that numbering; a held level style the file does not number is flagged | `style_import.numbered_in` ↔ `styleimport._v_chain_list` | `test_c5b_*` (3) |
| C5c | **refused**: `mc:AlternateContent` anywhere in a copy (`style-import` for a style, `style-numbering` for a list) — stripping drops the Fallback with the Choice, keeping the Fallback alone turns "01, 02" into "1, 2" | `style_import.stripped` ↔ `styleimport._v_has_alternate` | `test_c5c_*` (2) |
| C6 | the template's numbering part is parsed only when a copied style is numbered; malformed → **`bad-params`** (not `style-import`) | `TemplateStyles.numbering` (lazy), verifier `t_num()` lazy | `test_c6_*` |
| C8 | map_styles' apply refuses a changed template (`internal`) whenever the plan read one, imported or not | `style_import.check_template_unchanged`, `map_styles.apply` | `test_c8_*` |
| C9 | **refused** (`structure`): a character `from` also used in `w:sdtPr/w:rPr`, `w:sdtEndPr/w:rPr`, `m:r/w:rPr` (parts in scope) or `w:lvl/w:rPr` (numbering) | `map_styles.unmapped_uses` ↔ `restyle._v_unmapped` | `test_c9_*` (4) |
| D2 | `_check_paragraph` never compares the paragraph mark's rPr: `_check_rpr` checks it when the kind restyled the mark, else the residual does (unless the kind that changed it registered it) | `verify/restyle._check_paragraph` | `test_d2_*` (2) |

Decided differently from the request:

* **C6 uses `bad-params`**, not `style-import`: an unreadable template (not a zip, no styles part) already refuses
  `bad-params`, which `compiler.FATAL_SPEC_CODES` holds (the same for every file); `style-import` stays file-specific (a
  name clash depends on the file), so it cannot be fatal. And the numbering part is read lazily: a template with a broken
  numbering part still serves an unnumbered style.
* **Name resolution changed with C2**: `resolve_style` ↔ `v_resolve_style` match `w:name` whitespace-collapsed and
  casefolded (design §5 said "casefolded"). Without it, a file holding the template's style under a whitespace variant
  of its name would request an import with nothing to copy, and apply_style (which emits the import only when it changes
  something) would then fail V10. CLAUDE.md's "Style import" definition should say "by name_key (whitespace collapsed,
  casefolded)".
* **C9 refuses with `structure`** (no new code); its explanation text in report.py could name content controls,
  equations and list levels.

For the coordinator (files I may not edit):

* `verify/applystyle.py` has the D2 pattern too: review_d's `combos.py` (with `SRCROOT` at this worktree) still shows
  `apply_style + set_font (all)` planning and failing V10 ("paragraph properties are ['pStyle', 'rPr'], expected
  ['pStyle']") — 4 combinations.
* review_c's `pREP.py` (replace Heading1 by map_styles + set_properties `i: false` on a Heading1 paragraph) plans and fails
  V10: set_properties (direct) reads the paragraph's style chain (its post-condition) but declares no read of the style
  definitions, so the `replace`/`rPr` touches of the replaced Heading1 meet nothing.
* CLAUDE.md definitions to update: the import's names (`name_key`), the held-identity refusal, the level-style refusal,
  `used_ids`, `mc:AlternateContent`, lazy template numbering (`bad-params`), `check_numbering_unchanged` (numbering is
  compared by the import kinds even with no list copied), map_styles' `unmapped_uses`.

Full suite after the fix round (`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest -q -s -p no:cacheprovider`):
1895 passed, 4 skipped (the container's usual four).
