# Batch 4, items 26–27 — notes for the integrator

Builder's notes for `set_borders_shading` (item 26, design §1) and the `styles` target of `set_paragraph_props` (item 27,
design §2). Everything below is text to paste into the files this builder was told not to edit (`catalog.py`,
`compiler.py`, `report.py`, `README.md`, `CLAUDE.md`), plus what was decided differently from the design note and why.

Code: `src/spec_surgeon/ops/set_borders_shading.py` (op), `src/spec_surgeon/verify/borders.py` (V10 kind
`borders_shading`), `src/spec_surgeon/ops/set_paragraph_props.py` (styles target), `src/spec_surgeon/verify/props.py`
(`_paragraph_props` → `_paragraph_props_styles`, `_v_merged`, `_v_style_spacing_refusal`). Registration: one import line in
`ops/__init__.py`, `BORDER_KINDS` at the end of `verify/props.py`. Tests: `tests/test_borders_shading.py` (37),
`tests/test_style_paragraph_props.py` (25).

**One test fails until the catalog is updated:** `tests/test_planner.py::test_catalog_and_schema_cover_every_op` asserts
that every op has `common_assumptions` — paste (a) into `catalog.COMMON_ASSUMPTIONS` and it passes.

## (a) `COMMON_ASSUMPTIONS`

```python
    "set_borders_shading": [
        "the asked borders are the paragraph's whole border box: a side not named has no border, so a side its style "
        "shows is cancelled with a 'nil' side (the style keeps its definition); 'none' removes every border (or the "
        "shading)",
        "border size is in eighths of a point (4 = ½ pt, Word's default; 2–96), the space between border and text in "
        "points (1 by default); colours are 6 hex digits or 'auto'",
        "a paragraph or style that already looks as asked is left alone (no change, no tracked-change marker)",
        "direct formatting on the selected paragraphs by default (tracked when they are targeted, so the reviewer sees "
        "'Formatted: Border'); with select.styles the paragraph style definitions change instead (never tracked) — "
        "paragraphs with their own direct borders/shading and styles based on it that define their own keep theirs "
        "(flagged)",
    ],
```

and add to the existing `"set_paragraph_props"` list:

```python
        "with select.styles the values go into the paragraph style definitions instead (never tracked): every paragraph "
        "of that style follows unless it sets its own value (flagged); a style already as asked is left alone; spacing "
        "the style (or a style it is based on, or the document defaults) gives in lines, or as 'auto', sets the file "
        "aside rather than being silently ignored by Word",
```

Also (design §9): `CUSTOM_TRANSFORM_LIMITS["families"]["paragraph borders and shading"] = "borders_shading"` and the
"cannot" list's `"highlight, borders, shading"` becomes `"highlight"`. (`verify/borders.py` checks only what
`set_borders_shading` writes; a custom transform emitting a `borders_shading` assertion is checked the same way.)

## (b) Guidance for the compiler's SYSTEM prompt

> **Borders and shading** (`set_borders_shading`): "box the notes", "a rule under the title", "grey shading behind the
> notes". `borders` is the paragraph's WHOLE border box — name every side that should show (`top`, `left`, `bottom`,
> `right`, `between` for the line between two bordered paragraphs, `bar`); a side not named is removed even where the
> style shows it; `"none"` removes every border. Sizes are eighths of a point (4 = ½ pt, 8 = 1 pt, 12 = 1½ pt), `space` is
> points, colours 6 hex digits or `auto`. `shading` is `{fill}` (6 hex digits; `D9D9D9` is Word's 15% grey, `F2F2F2` its 5%)
> with an optional `pattern` (clear by default) and pattern `color`; `"none"` removes it. Choose the paragraphs by their
> style (`style_in`, from the profiles) or a text pattern (`text_regex`), or change the style itself with
> `select: {"styles": {"ids": [...]}}` when the user says "the note style" / "all notes everywhere" — alone in its call,
> paragraph styles only, never tracked. Spacing, indents and alignment **in a style** are `set_paragraph_props` with the
> same `select.styles` (twips: 240 = 12 pt; a style whose spacing is given in lines — `beforeLines`/`afterLines`, here or in
> a style it is based on — needs those set too, e.g. `"afterLines": 0`). Borders/shading, spacing/indents, tab stops and
> keep-with-next/page-break flags on the same paragraphs are separate specs (one paragraph property op per paragraph —
> offer a chain); the same holds for a style-definition change and a direct borders change on paragraphs of that style
> (or of a style based on it). Bold, italic or colour on the same paragraphs share a spec.

## (c) README section (the user's view)

### Built-in operations, batch 4 (items 26–27): paragraph borders and shading; spacing and indents in styles

| # | You ask | Operation | Mode |
|---|---|---|---|
| 26 | Paragraph borders and shading (box the specifier notes, a rule under the title, grey behind the notes) | the **new operation `set_borders_shading`** — on chosen paragraphs, or in paragraph styles | formatting |
| 27 | Spacing and indents set on the styles themselves | `set_paragraph_props` gains a **`styles` target** | formatting |

#### `set_borders_shading` — borders and shading on paragraphs or in their styles

```json
{"op": "set_borders_shading",
 "select": {"paragraphs": {"style_in": ["ArchNote"]}},
 "params": {"borders": {"top": {"style": "single", "size": 4, "space": 1, "color": "auto"},
                        "bottom": {"style": "single"}},
            "shading": {"fill": "D9D9D9"}}}
```

* **`borders`** — `"none"`, or the paragraph's **whole border box**: an object whose keys are the sides that show
  (`top`, `left`, `bottom`, `right`, `between`, `bar`), each `{style, size, space, color}` — style `single` by default
  (also `thick`, `double`, `dotted`, `dashed`, `triple`, `wave`, `thinThickSmallGap`, …), size in eighths of a point
  (2–96, default 4 = ½ pt), space in points between the border and the text (0–31, default 1), colour `auto` or six hex
  digits. A side you do not name has **no** border: where the paragraph's style shows one there, the op writes a `nil`
  side that cancels it, so the box you see is exactly the one you asked for, whatever the style says.
* **`shading`** — `"none"`, or `{fill, pattern, color}`: `fill` `auto` or six hex digits (`D9D9D9` is Word's 15 % grey),
  `pattern` `clear` by default (`solid`, `pct10` …), `color` the pattern's colour (`auto` by default). `"none"` writes a
  clear shading where the style shows one (and removes the paragraph's own otherwise).
* **Two targets, one per call**, as `set_properties`: direct formatting on the selected paragraphs (`scope`,
  `paragraphs`), tracked when particular paragraphs are targeted (§14.1: Word shows "Formatted: Border"), plain for a
  whole-document selection; or **paragraph style definitions** (`"select": {"styles": {"ids": ["ArchNote"]}}`, alone,
  never tracked — Word does not track style changes). Paragraphs that keep their own direct borders/shading, and styles
  based on the changed one that define their own, keep theirs — flagged in the preview, never refused.
* **Already as asked = untouched.** A paragraph or style whose own box and shading already are the asked ones is left
  alone and not counted ("already as asked, left alone: 15 paragraph(s)"); only the element that differs is written (a
  paragraph whose box is right but whose shading is not keeps its box untouched). A paragraph already carrying someone's
  pending paragraph change refuses tracked (`tracked-format`) and is changed-and-flagged when plain.
* **Layout reference**: `none` when a border changes (a border takes space and moves the text), the source render for
  shading alone.
* **Overlaps.** Borders/shading, spacing/indents, tab stops and keep-with-next flags all live in a paragraph's
  properties: on the same paragraphs they are separate specs (a chain). A style-definition change (any of the paragraph
  properties of a style) beside direct borders on paragraphs of that style, or of a style it is based on, is a plan-time
  `conflict` too: the cancelling `nil` sides are computed from the styles. Bold or colour on the same paragraphs, or on
  the same style, share a spec.
* **Verified by V10's `borders_shading` kind** (`verify/borders.py`, nothing shared with the op): the selection, the
  levels below each target (the document defaults, the table style, the paragraph style chain — or a style's own
  ancestors), the expected border box and shading and the "already" rule are re-derived from the source; each changed
  paragraph or style must hold exactly the expected elements and nothing else changed; and in the output every selected
  paragraph or style must *show* the asked box — under either reading of how Word inherits paragraph borders (the
  nearest box decides every side, or each side is decided on its own) — and the asked shading.

#### Spacing, indents and alignment in paragraph styles (`set_paragraph_props` with `select.styles`)

```json
{"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchArticle"]}},
 "params": {"spacing": {"before": 240, "after": 120}, "ind": {"left": 0}}}
```

The values merge into each style's own paragraph properties exactly as they would into a paragraph's (the attributes
asked set, the others kept; `firstLine` removes `hanging` and vice versa); every paragraph of the style — and of the
styles based on it — follows unless it sets the value itself (flagged in the preview: "1 paragraph(s) using style
ArchPara … keep their own direct spacing/indentation/alignment"). Never tracked; a style already as asked is left alone.
A style whose spacing is `auto` (`…Autospacing`), or given in lines on the style, a style it is based on or the document
defaults (`beforeLines`/`afterLines` win over points in Word), is set aside (`conflict-property`) unless the spec sets
the lines value too; so is an indentation asked as `left` where the style writes `start` (and the reverse). V10's
`paragraph_props` kind checks each changed style's paragraph properties (the merge, nothing else changed), re-derives
the refusals, and checks that every selected style shows the asked values in the output.

**Not in these items** (recorded): borders on part of a paragraph (run borders), page borders, table cell borders and
shading, theme colours as a border or shading colour to set, tab stops in styles.

## (d) CLAUDE.md both-sides definitions (for "Built-in operations batch 4")

- **Paragraph borders and shading** (`set_borders_shading`). *Levels below*: a direct target — docDefaults pPr → table
  style chain (tblStyle, else the default table style) → paragraph style chain (pStyle, else the default), each root-most
  first; a style target — docDefaults pPr → its basedOn ancestors root-most first (not itself). *Visible side*: a side
  element whose `w:val` is not `nil`/`none` (absent = visible). *Visible shading*: a `w:shd` not `w:val="nil"` and not
  (`clear` with `w:fill` absent or `auto` and no `w:themeFill`). *Expected pBdr*: sides in the order top, left, bottom,
  right, between, bar — an asked side exactly `{val, sz, space, color}`, a side not asked that ANY lower level shows
  visibly `{val: nil}`, else absent; none → no pBdr. *Expected shd*: asked → exactly `{val, color, fill}`; `"none"` →
  `{clear, auto, auto}` when the NEAREST lower level specifying a `w:shd` shows visible shading, else absent. *Already*:
  the own element sig-equal to the expected one (both absent counts). Only the elements that differ are written (old
  removed, expected inserted in `PPR_ORDER`); an emptied paragraph pPr (plain) or style pPr goes. Post-condition (verifier
  only): every selected target's output shows the asked box under both inheritance readings (the nearest level holding a
  pBdr decides all sides / the nearest level naming a side decides it) and the asked shading (nearest `w:shd`; none
  visible for `"none"`). Transform: `ops/set_borders_shading.clean_params/paragraph_levels/style_levels/side_visible/
  shading_visible/expected_borders/expected_shading/same_element/decide/write_elements`; verifier: `verify/borders.
  _v_asked/_v_below_paragraph/_v_below_style/_v_side_shows/_v_shd_shows/_v_box/_v_shade/_v_gets/_check_elements/
  _check_shown`. `tests/test_borders_shading.py` compares the two decisions on every paragraph and paragraph style of two
  fixtures for four asks, and plants shared defects (levels without the document defaults or the table style, a box
  without nil sides, an attribute-blind already rule, clear-auto counted as shading, a style's levels including itself,
  a wrong default size, a wrong default scope).
- **Spacing, indents and alignment on styles** (`set_paragraph_props`, `select.styles`). *Merge*: the asked attributes
  set, the others kept, `firstLine` drops `hanging`/`hangingChars` and vice versa, `jc` gets `w:val` — the paragraphs'
  merge (`set_paragraph_props.merged_attrs`/`write_values`, now shared by the direct path) ↔ `verify/props._v_merged`.
  *Already*: every asked element present in the style's own pPr with the merged attributes equal to its own. *Refusals*
  (`conflict-property`): `before`/`after` asked where the style's own spacing has `…Autospacing` on, or where the style,
  its ancestors or docDefaults set `…Lines` (the lines not asked); an `ind` alias whose other spelling the style's own
  `w:ind` uses — `set_paragraph_props.style_refusal` ↔ `verify/props._v_style_spacing_refusal` (V10 fails "the op must
  refuse"). `tests/test_style_paragraph_props.py` plants a merge that keeps `hanging`, refusal rules that forget the
  ancestors or Autospacing, and a merge that writes nothing.

Rule 9 text to add (touches): `set_borders_shading` touches `(part, bi, "pPr")` + `(part, bi, "pBdr"|"shd")` per changed
paragraph (only the elements that change), reads `(part, bi, "verify:pStyle")` and `(part, bi, "verify:pBdr"|"verify:shd")`
of every selected paragraph (its post-condition reads them in the output), `(part, "scope", "verify")` per part in scope
and `(styles, "style:<id>"|"docDefaults", "verify:pPr")` of every level below a selected paragraph; the styles target
touches `(styles, "style:<id>", "pPr")` per changed style and reads `verify:pPr` of the ancestors, docDefaults and every
unchanged selected style. `set_paragraph_props` styles touches `(styles, "style:<id>", "pPr")` per changed style and
reads `verify:pPr` of every selected style — and, when `before`/`after` is asked without its lines value, of the
ancestors and docDefaults; its direct path now reads `verify:pPr` of the paragraph style lineage under the same condition
(its lines rule reads them, and the styles target can set `…Lines` on a style in the same spec).

## (e) Refusal codes without a `FAILURE_EXPLANATIONS` entry

None. The codes raised are `bad-params`, `not-found`, `structure`, `tracked-format` (set_borders_shading) and
`conflict-property`, `bad-params`, `not-found`, `structure` (set_paragraph_props styles) — all explained already.
`tests/test_borders_shading.py::test_every_refusal_code_of_set_borders_shading_is_explained` scans the op module.

## (f) Deviations from the design note, and why

1. **Reads of the styles below a target (rule 9).** Not in §1.2: the direct target also declares
   `(styles, "style:<id>", "verify:pPr")` for every style in the levels below each selected paragraph (paragraph and
   table chains) and `(styles, "docDefaults", "verify:pPr")`; the styles target the same for the ancestors (and every
   selected style it leaves alone). The expected `nil` sides and clear shading are computed from those levels, and this
   batch's own styles target (and `set_properties`/`set_paragraph_props` styles, a replaced imported style) can change
   them in the same spec: without the read, "box the NOTE paragraphs" beside "ArchNote gets a between border" planned
   and the box shown was not the asked one (`test_without_the_style_reads_the_post_condition_still_catches_a_stale_box`
   plants it). The batch-3 precedent ("not declared … no op changes either today") no longer holds for borders. Cost:
   any style pPr change (e.g. `set_properties` keepNext on Normal) beside direct borders on a paragraph whose chain
   includes that style is a conflict — conservative; a finer aspect vocabulary for style pPr changes (pBdr/shd vs flags vs
   spacing) is the recorded follow-up.
2. **A post-condition and per-paragraph reads, as set_tab_stops** (Codex on #28). V10 checks on the OUTPUT that every
   selected target — already-as-asked ones included — shows the asked box under both readings of border inheritance and
   the asked shading; the op declares `(part, bi, "verify:pBdr"|"verify:shd")` for every selected paragraph, so a second
   borders op on a paragraph the first leaves alone is a plan-time conflict. (`clear_direct_formatting` touches only
   `pPr`; it could also declare `pBdr`/`shd` when it clears them so the read catches it at plan time — the post-condition
   catches it after apply either way.)
3. **Only the elements that change are written and stripped.** §1.2 says the kind strips `pBdr`, `shd` and `pPrChange`
   of the changed paragraphs; this build strips only the elements that change (+ `pPrChange`) and touches `pBdr`/`shd`
   only for them, so an asked element that was already right stays under the residual — stricter.
4. **An emptied pPr goes** (a paragraph's, plain, after its border and shading are removed; a style's). The design is
   silent; an empty `<w:pPr/>` would be harmless but noise.
5. **Parameter decisions** (the design gives none): `borders: {}` is `bad-params` (say `"none"`); `shading.fill` is
   required (no default given); colours are upper-cased (as `set_properties`); unknown parameter keys and a non-boolean
   `tracked` are `bad-params`; a style target of another type is `bad-params` (as `set_properties`).
6. **`set_paragraph_props` direct path reads the style lineage** when its lines rule reads it (`before`/`after` asked
   without the lines): the new styles target can set `beforeLines`/`afterLines` on a style in the same spec, and the
   direct op would then pass while Word uses the style's lines (`test_without_the_lines_read_…` plants the silent pass).
   Recorded, not changed: the direct path's lines rule still does not read docDefaults (the styles target does, per the
   design).
7. **The `paragraph_props` styles branch re-derives the refusals** (Autospacing, lines, alias: "the op must refuse") and
   adds a post-condition (every selected style shows the asked values in the output); the direct branch is unchanged.
8. **The merge is one function.** `set_paragraph_props.apply` now merges through `merged_attrs`/`write_values`, shared
   with the styles target (same attributes, same order). `set_paragraph_props.py` is a codegen example op: its only new
   import is `STYLE_ORDER`, already in `codegen.ALLOWED_FROM` (`tests/test_codegen.py` passes).
9. **Flags.** The styles targets count paragraphs whose style chain includes the changed style ("using it or a style
   based on it"). For §2, styles based on the changed one that set their own value are a note (the design lists only the
   paragraphs' flag).

## (g) Test count

62 new tests (37 in `tests/test_borders_shading.py`, 25 in `tests/test_style_paragraph_props.py`). Full suite in the
container: see the builder's report (1661 pass, 4 skip, and `test_catalog_and_schema_cover_every_op` failing until (a) is
pasted into `catalog.py` → 1662 pass).

## (h) Fix round after the adversarial review (A2, A3, A4, A6, A7)

* **A2 — `…Chars` win in Word.** `ind` takes `leftChars`, `rightChars`, `startChars`, `endChars`, `firstLineChars`,
  `hangingChars`; an indentation side asked in twips where the target or a level it inherits from gives that side in
  characters is `conflict-property` unless the spec gives it in characters too (Chars the merge itself removes from the
  target excepted). The first-line and hanging families are exclusive as wholes (`firstLineChars` + `hanging` is
  `bad-params`) and the merge drops the whole other family. Both targets; `inherited_refusal` ↔ `_v_inherited_refusal`.
* **A3 — Autospacing through the ancestors.** The nearest level naming `…Autospacing` decides (an explicit `0` on the
  style turns an inherited `1` off — written and verified), on both targets.
* **Levels**: one definition for both targets and both sides — a paragraph's own pPr, its paragraph style chain, its
  table's style chain, docDefaults; a style's own pPr, its ancestors, docDefaults. The direct path therefore now also
  reads the table style chain and docDefaults for the lines rule (it read only the paragraph style chain), and declares
  those reads; V10's direct branch re-derives the refusals too (it did not before).
* **A4** — `verify/borders._v_paragraph_style_id`: an empty or missing `w:pStyle/@w:val` names no style (template.Eff).
* **A6** — the styles assertion carries the raw `params` and `select`; `_v_ppr_values` reads them; `tracked: true`
  fails; each asked element must occur once (styles and direct branch).
* **A7** — a style id of another type in this file is `style-mismatch` (both style targets).
