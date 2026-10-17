# Built-in operations, batch 4 — design note

2026-10-17. Abraham's request, the fourth chunk of prebuilt operations (items 26–32 of his list; ★ = he starred it):

| # | Asked for | Built as | Mode |
|---|---|---|---|
| 26 | Paragraph borders and shading | **new op `set_borders_shading`** (direct formatting or paragraph styles) + V10 kind `borders_shading` | formatting |
| 27 | Spacing and indents set on the styles themselves | **`set_paragraph_props` gains a `styles` target** (spacing, indentation, alignment in paragraph style definitions) — V10 `paragraph_props` gains its styles branch | formatting |
| 28 | Clear direct formatting so text follows its styles again | **new op `clear_direct_formatting`** + V10 kind `cleared_formatting` | formatting |
| 29 | Set the proofing language for all text | **new op `set_language`** + V10 kind `language` | formatting |
| 30 ★ | Apply a style to paragraphs picked by appearance or pattern (bold, all caps, regex, "after a heading") | **new op `apply_style`** (the existing paragraph selector grammar; the style from the document or imported from a template; optionally clearing the paragraphs' direct formatting) + V10 kind `applied_style` | formatting |
| 31 ★ | Style mapping: the architect's style A becomes your style B everywhere, importing your styles from a template | **new op `map_styles`** + V10 kind `mapped_styles`, over **shared style import** (`ops/style_import.py` ↔ `verify/styleimport.py`, V10 kind `styles_imported`: closure, numbering definitions) | formatting |
| 32 | Remove unused styles | **new op `remove_unused_styles`** + V10 kind `removed_styles` | formatting |

Decisions below were taken on Abraham's behalf, as in batches 1–3, and can each be changed. Reading order: CLAUDE.md hard
rules 1–3, 8–10, the "Selector grammar", "Effective pinnable properties" and "Formatting properties" definitions,
`docs/SET_PROPERTIES_DESIGN.md`, `docs/BUILTIN_OPS_BATCH3_DESIGN.md`.

No operation in this batch writes a character. Every one is plain formatting by default where §14.1 says so (whole
document) and tracked where it says so (a `paragraphs` predicate narrows the selection), except `set_language`,
`remove_unused_styles` and style-definition changes, which are never tracked (Word does not track them either), and
`map_styles`, plain by default (a whole-document change by nature; `tracked: true` records each paragraph's and run's
change).

## 0. Shared verifier hooks (verify/props.py `Ctx`)

* `replaced_styles` — style ids a kind compared WHOLE with its own expected definition (an imported style replacing the
  document's own): the styles residual removes them from both sides before comparing.
* `removed_styles` — style ids a kind expects GONE: the residual removes them from the source copy only — an output that
  still holds one differs, and fails.
* `strip_tbl_style` — part → ordinals (among all `w:tbl` of the part, document order) of tables whose
  `w:tblPr/w:tblStyle` a kind checked: removed from both sides before the part's residual.
* `numbering.xml` is not a text part and not under the residual: an op that changes it must have its kind compare the
  WHOLE member with its own expected tree and register it in `verified_members` (as batch 2's side parts).

## 0.1 Plan-time conflicts (rule 9) — the new aspect `pStyle`

A paragraph-style change (`apply_style`, `map_styles`) changes how EVERY inherited property of the paragraph resolves.
The ops that change a paragraph's style touch `(part, block, "pPr")` and `(part, block, "pStyle")`. An op whose
prediction or post-condition reads a paragraph's style chain declares `(part, block, "verify:pStyle")` for that
paragraph: `set_properties` (direct target: the effective value before and the post-condition after), `set_tab_stops`
(inherited stops, post-condition) and `set_borders_shading` (the styles' border box). `verify:pStyle` conflicts with a
`pStyle` change of that paragraph (and with `text`), nothing else. Not declared, as for `set_font` ("Plan-time conflicts
for what an op reads" 5): the symbol-run rule's read of the style chain (verified on the SOURCE).

An op that names an existing style declares `(styles part, "style:<id>", "verify")`: `apply_style` (the style it
applies), `map_styles` (each `from` and each `to` the document holds), and now `insert_paragraph` / `ensure_end_of_section`
with an explicit `style`. `remove_unused_styles` declares each removal as `(styles, "style:<id>", a)` for `a` in
`remove`, `rPr`, `pPr`, `tblPr` — so a style another op in the same spec applies, maps to, inserts with or changes is a
plan-time conflict, never a dangling reference.

## 1. `set_borders_shading` — paragraph borders and shading (item 26)

```json
{"op": "set_borders_shading",
 "select": {"paragraphs": {"text_regex": "^NOTE"}},
 "params": {"borders": {"top": {"style": "single", "size": 4, "space": 1, "color": "auto"},
                        "bottom": {"style": "single"}},
            "shading": {"fill": "D9D9D9"}}}
```

* Targets, one per call (as `set_properties`): **direct** — `select.scope` / `paragraphs`; **styles** —
  `select.styles: {"ids": [...]}`, paragraph styles only, alone.
* `borders`: `"none"`, or an object whose keys are sides `top | left | bottom | right | between | bar`, each a border
  `{style, size, space, color}` — `style` default `single` (one of `single, thick, double, dotted, dashed, dotDash,
  dotDotDash, triple, thinThickSmallGap, thickThinSmallGap, wave, doubleWave, dashSmallGap, threeDEmboss, threeDEngrave,
  outset, inset`), `size` eighths of a point 2–96 (default 4), `space` points 0–31 (default 1), `color` `auto` or
  RRGGBB (default `auto`). **The asked object is the paragraph's whole border box**: a side not named has no border.
* `shading`: `"none"`, or `{fill, pattern, color}` — `fill` `auto` or RRGGBB, `pattern` default `clear` (one of `clear,
  solid, pct5, pct10, pct12, pct15, pct20, pct25, pct30, pct35, pct37, pct40, pct45, pct50, pct55, pct60, pct62, pct65,
  pct70, pct75, pct80, pct85, pct87, pct90, pct95, horzStripe, vertStripe, diagStripe, reverseDiagStripe, horzCross,
  diagCross`), `color` (the pattern's colour) default `auto`.
* At least one of the two. `tracked` / `author` as `set_paragraph_props` (direct only; styles are never tracked).

### 1.1 Definitions (both sides; transform `ops/set_borders_shading.py`, verifier `verify/borders.py`)

* **Levels below a direct target**: docDefaults `pPr` → table style chain (the paragraph's table: its `tblStyle`, else
  the default table style) → paragraph style chain (`pStyle`, else the default paragraph style), each chain root-most
  first — set_properties' paragraph levels. **Below a style target**: docDefaults `pPr` → the style's `basedOn` ancestors
  (not the style itself), root-most first.
* **A visible side**: a side element whose `w:val` is not `nil` or `none` (absent `w:val` counts as visible).
* **Expected `w:pBdr`** (when `borders` is asked): the sides in the order `top, left, bottom, right, between, bar` —
  an asked side as `{w:val: style, w:sz: size, w:space: space, w:color: color}` exactly; a side not asked that ANY level
  below specifies visibly as `{w:val: "nil"}`; else absent. No side → no `w:pBdr`. (Word's per-side vs per-element
  inheritance of paragraph borders is not modelled: writing `nil` for every side any lower level shows makes the
  rendered box the asked one under either reading.)
* **Visible shading**: a `w:shd` that is not `w:val="nil"` and not (`w:val="clear"` with `w:fill` absent or `auto` and
  no `w:themeFill`). **Expected `w:shd`** (when `shading` is asked): asked → exactly `{w:val: pattern, w:color: color,
  w:fill: fill}`; `"none"` → `{w:val: "clear", w:color: "auto", w:fill: "auto"}` when the nearest lower level that
  specifies a `w:shd` shows visible shading, else absent.
* **Already**: the target's own element (the paragraph's direct `w:pBdr`/`w:shd`, the style's own) is exactly the
  expected one (`sig` equal; both absent counts). A target with nothing to change is left alone and not counted.
* **Writing**: the old element goes, the expected one is inserted in `PPR_ORDER` (none → nothing inserted).
* Tracked: one `w:pPrChange` per changed paragraph (snapshot = the source pPr without rPr, sectPr, pPrChange); an
  existing `w:pPrChange` refuses tracked (`tracked-format`) and is flagged when plain — exactly `set_paragraph_props`.
* Style target: flagged (never refused) — the number of paragraphs using a changed style whose own direct `w:pBdr` /
  `w:shd` keeps its own value (direct formatting wins), and of styles based on it that define their own.

### 1.2 Verification (V10 `borders_shading`)

Re-derives the selection, the levels, the expected elements and "already" from the SOURCE with its own code; fails when
the changed targets or their count differ from the plan's, when a changed target's output `w:pBdr`/`w:shd` is not exactly
the expected one, when any other pPr child changed, or when the `w:pPrChange` breaks the rules. It strips `pBdr`, `shd`
and `pPrChange` of the changed paragraphs (`strip_ppr`), `pBdr`/`shd` of changed styles (`strip_style_ppr`).

Touches: direct — `(part, block, "pPr")` + `(part, block, "pBdr" | "shd")` per changed paragraph, `(part, block,
"verify:pStyle")` per selected paragraph, `(part, "scope", "verify")` per part in scope; styles — `(styles,
"style:<id>", "pPr")`. Layout reference `none` when a border changes (borders take space), `source` for shading alone.

## 2. `set_paragraph_props` on styles (item 27)

`select: {"styles": {"ids": [...]}}` — alone, paragraph styles only — with the op's `spacing` / `ind` / `jc` merged into
each style's OWN `w:pPr` exactly as for a paragraph (the attributes asked set, the others kept; `firstLine` removes
`hanging`/`hangingChars` and vice versa). Never tracked (`tracked: true` is `bad-params`). A style whose own values are
already the asked ones is left alone (noted). Refusals (file-specific `conflict-property`, as for paragraphs): a side
asked in points where the style's own spacing has `…Autospacing` on, or where the style, its `basedOn` ancestors or
docDefaults set `beforeLines`/`afterLines` (unless the spec sets the lines too); an `ind` alias the style's own `w:ind`
uses the other spelling of. Flagged: per style, the paragraphs using it whose own direct spacing/indentation/alignment
keeps its own value. V10 `paragraph_props` with `target: "styles"`: each changed style's `w:pPr` = the source's with the
asked values merged, every other child identical; `strip_style_ppr` of the asked elements. Touches `(styles,
"style:<id>", "pPr")`.

## 3. `clear_direct_formatting` (item 28)

```json
{"op": "clear_direct_formatting", "select": {"scope": ["document"], "paragraphs": "all"},
 "params": {"run": true, "paragraph": true, "keep": ["b"], "also": ["highlight"]}}
```

* `select`: `scope`, `paragraphs`, `runs` (set_properties' grammar; `runs` narrows the runs and marks only).
* `run` (default true): clear the run formatting of the selected runs and paragraph marks; `paragraph` (default true):
  clear the selected paragraphs' paragraph formatting; `keep`: property names never cleared by this op; `also`: opt-in
  property names cleared too; `include_paragraph_marks` (default true), `in_revisions` (`include`|`exclude`),
  `skip_sym_runs`, `preserve_fonts`, `tracked`, `author` — set_properties' meanings.

### 3.1 Definitions (both sides; transform `ops/clear_formatting.py`, verifier `verify/clearing.py`)

* **Cleared run properties** (default): `rFonts, b, bCs, i, iCs, caps, smallCaps, strike, dstrike, outline, shadow,
  emboss, imprint, color, spacing, w, kern, sz, szCs, u, effect, bdr, shd, fitText, em`. **Opt-in** (`also`):
  `highlight` (a review cue and the templating slot marker), `vertAlign`, `position` (superscripts carry meaning: m²,
  footnote numbers). **Never**: everything else — `rStyle`, the revision marks `ins/del/moveFrom/moveTo`, `rPrChange`,
  `vanish`/`specVanish`/`webHidden` (hidden text stays hidden), `noProof`, `lang`, `rtl`, `cs`, `eastAsianLayout`,
  `snapToGrid`, `oMath`, and any element outside the `w` namespace.
* **Cleared paragraph properties** (default): `keepNext, keepLines, pageBreakBefore, widowControl, suppressLineNumbers,
  pBdr, shd, tabs, suppressAutoHyphens, spacing, ind, contextualSpacing, mirrorIndents, jc, textDirection, textAlignment,
  textboxTightWrap`. **Opt-in**: `outlineLvl` (TOC levels), `framePr`, `suppressOverlap` (frames). **Never**: `pStyle`,
  `numPr` (list numbers), `sectPr`, `pPrChange`, `rPr` (the mark — cleared as a run), `cnfStyle`, `divId`, `bidi`, the
  East Asian typography flags (`kinsoku, wordWrap, overflowPunct, topLinePunct, autoSpaceDE, autoSpaceDN,
  adjustRightInd, snapToGrid`), and any element outside the `w` namespace.
* `keep` and `also` take names from the cleared and opt-in lists only (`bad-params` otherwise; one name in both is
  `bad-params`). The set for a target = (default ∪ also) − keep.
* **Symbol runs** (set_font's definition — a preserved font in the run's direct rFonts, character style chain or
  paragraph style chain; or a `w:sym` when `skip_sym_runs`) never lose `rFonts`, `caps` or `smallCaps`: their glyphs
  depend on them. Symbol paragraph marks the same (without the `w:sym` rule).
* A target's result = its source element with the cleared children removed, in order; plain: an emptied run `w:rPr` is
  removed, an emptied mark `w:rPr` is removed and then an emptied `w:pPr`; tracked: a `w:rPrChange` (snapshot = the source
  rPr without rPrChange) / `w:pPrChange` (snapshot = the source pPr without rPr, sectPr, pPrChange) per changed target.
  An existing change marker refuses tracked (`tracked-format`), is flagged plain; a tracked change on a paragraph mark that
  is itself a pending revision refuses (`tracked-format`). A target with nothing to clear is untouched and uncounted.

### 3.2 Verification (V10 `cleared_formatting`)

Re-derives the selection, the symbol rule and each target's cleared set from the SOURCE with the user's own `keep`/`also`
and this module's defaults; fails when the changed targets or their counts differ from the plan's, or when an output
`w:rPr`/`w:pPr` is not exactly the source's minus the cleared children (the change marker by the shared rules; absent when
emptied and plain). Strips the checked runs, marks and pPr children for the residual. Touches `(part, block, "rPr" |
"markRPr" | "pPr")` per changed target, `(part, "scope", "verify")`. Layout reference `none` when anything changes.

## 4. `set_language` (item 29)

```json
{"op": "set_language", "params": {"language": "en-US"}}
```

* `language` → `w:lang/@w:val`, `east_asian` → `@w:eastAsia`, `complex_script` → `@w:bidi` — at least one; each a tag
  `^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$`. `replace_only`: a list of tags — only values in it change (casefolded).
* No selector: all text. Never tracked (`tracked: true` → `bad-params`).

### 4.1 Definitions (transform `ops/set_language.py`, verifier `verify/language.py`)

* **Levels**: docDefaults `rPrDefault/rPr`, every style's own `w:rPr` (any type), every run's and paragraph mark's own
  `w:rPr` in every text part. Never: `w:rPrChange` snapshots, numbering levels, `w:sdtPr`, `w:tblStylePr`.
* Per asked attribute A = V: a level gets A when its own `w:lang` carries A with a value ≠ V (and, with `replace_only`,
  listed). docDefaults without `replace_only` gets A when its value is absent or ≠ V — `w:docDefaults`,
  `w:rPrDefault`, `w:rPr`, `w:lang` created in schema order as needed (inside styles.xml — never a new part; a document
  without a styles part refuses, `structure`). A level without an own `w:lang` is left alone (it inherits). The other
  attributes of a `w:lang` stay.
* **Effective language** of a run or mark, per attribute: the first of direct → character style chain (rStyle, else the
  default character style) → paragraph style chain (pStyle, else default) → table style chain → docDefaults whose
  `w:lang` carries the attribute; else none.

### 4.2 Verification (V10 `language`)

Re-derives every level's change from the SOURCE; fails when the changed levels differ from the plan's, when a changed
`w:lang` is not the source's with exactly the asked attributes set, when anything else in its rPr changed; and — a
post-condition on the OUTPUT — when a run or mark of any text part does not resolve to V (with `replace_only`: when one
that resolved to a listed value in the source does not resolve to V, or any resolves to a listed value). Strips the
checked rPr (runs, marks, styles, docDefaults). Touches `(part, block, "rPr" | "markRPr")`, `(styles, "style:<id>",
"rPr")`, `(styles, "docDefaults", "rPr")`, `(part, "scope", "verify")` for every text part and `(styles, "*", "verify")`
(the post-condition reads every style chain: an imported style beside it is a conflict). A language op beside any other
run-property op on the same runs is a conflict (both change rPr): a chain stage of its own. Layout reference `source`,
`none` when `east_asian`/`complex_script` is asked or the settings turn automatic hyphenation on.

## 5. Style import (shared by `apply_style` and `map_styles`)

* **Resolution of a style reference** (id or name): the style whose `w:styleId` equals it exactly; else the one whose
  `w:name` equals it casefolded (two → `style-mismatch`); else none.
* **Closure** from the requested template styles: `basedOn`, `link`, `next` of every closure style, and the `w:pStyle`
  of every level of the numbering definition an imported or replaced closure style uses (below) — to a fixpoint.
* **Imported** = closure styles whose id the document lacks, in the template's style order; **replaced** (only with
  `existing: "replace"`) = the requested styles the document holds. Refused (`style-import`): an imported style that is
  a default (`w:default`) in the template; a replaced style that is a default in the document; an imported style whose
  name (casefolded) another document style with another id holds; an extension element carrying text (as the templating
  op's `strip_foreign`). Copies are the template's definitions with extension markup stripped (`strip_foreign` ↔
  `template.strip_extensions`).
* **Numbering**: an imported or replaced style whose own `w:pPr/w:numPr/w:numId` is a number other than 0 brings the
  template's `w:num` with that id and its `w:abstractNum`. Refused (`style-numbering`): the document has no numbering
  part; the template's num or abstractNum is missing; the abstractNum has `w:numStyleLink` or `w:styleLink`; a level (or a
  `w:lvlOverride/w:lvl`) has `w:lvlPicBulletId`. **New ids**: going through the imported and replaced styles in the
  template's style order, each template numId met for the first time gets the next numId above the document's largest
  (`max + 1`, …) and its abstractNum, met for the first time, the next abstractNumId above the document's largest; copies
  stripped of extension markup, their id attributes (`w:abstractNumId` of the abstractNum; `w:numId` and the child
  `w:abstractNumId/@w:val` of the num) set to the new ids; the style copies' `w:numId/@w:val` set to the new numId. New
  abstractNums go after the document's last abstractNum (or before its first num), new nums after its last num (before
  `w:numIdMacAtCleanup`).
* Imported styles are appended to the styles part in that order; a replaced style takes its old place.

V10 `styles_imported` (verify/styleimport.py): the template's md5; the closure, imported and replaced sets and the id maps
re-derived; the new styles exactly the imported ones, each equal to its expected copy, replaced ones equal to theirs
(`added_styles`, `replaced_styles`); the numbering member compared whole with the source plus exactly the expected copies
(`verified_members`). Touches `(styles, "style:<id>", "add")` per imported style, `(styles, "style:<id>", a)` for `a` in
`replace, rPr, pPr, tblPr` per replaced style, `(numbering, "*", "add")` when numbering is imported. The template is an
input like a template spec (rule 12: pinned by md5, hashed by the approval token).

## 6. `apply_style` (item 30)

```json
{"op": "apply_style", "select": {"paragraphs": {"has_bold": true, "is_all_caps": true}},
 "params": {"style": "PRT", "template": "OFFICE STYLES.docx", "direct_formatting": "clear"}}
```

* `select`: `scope`, `paragraphs` — the whole paragraph grammar (`has_bold`, `is_all_caps`, `text_regex`,
  `after_heading_regex`, `between_paragraphs`, `style_in`, …).
* `style` (id or name); `template` (optional: where `style` comes from when the document lacks it — imported, §5);
  `existing` (`keep` | `replace`, with `template`); `direct_formatting`: `keep` (default) | `clear` — the paragraphs'
  direct paragraph formatting and their runs' and marks' direct run formatting are cleared by §3's default rules.
* The style must be a paragraph style (`style-mismatch` in the document; `bad-params` when the template's is not, or the
  template lacks it); not found and no template → `missing-style`.
* A paragraph whose style (pStyle, else the default) already is the asked one is left alone (with `clear`: its formatting
  is still cleared when there is some). The new `w:pStyle` goes first in the pPr (a pPr is created first in the
  paragraph). Tracked per §14.1 (targeted → tracked): one `w:pPrChange` per changed paragraph, `w:rPrChange` per cleared
  run/mark. A paragraph with its own `w:numPr` keeps it (flagged: it overrides the style's numbering).
* V10 `applied_style`: selection, style resolution (and the import, by `styles_imported`), each changed paragraph's pPr =
  the source's with `w:pStyle` set (and, with clear, the cleared children gone), runs and marks by §3.2. Touches §0.1.

## 7. `map_styles` (item 31)

```json
{"op": "map_styles",
 "params": {"map": [{"from": "Heading 1", "to": "PRT"}, {"from": "ArchArticle", "to": "ART"}],
            "template": "OFFICE STYLES.docx"}}
```

* `select`: optional `scope` (default every text part: document, headers, footers, footnotes, endnotes, comments).
* `map`: `[{from, to}]` (ids or names); `template`, `existing` as §6; `allow_missing` (default true: a pair whose `from`
  this document lacks is skipped and noted); `tracked` (default false), `author`.
* Two pairs with one `from`, or a `to` that is another pair's `from`, are `bad-params` (and `style-mismatch` when this
  file's resolution makes them collide). `from` and `to` of one type, else `style-mismatch`; a default character or table
  style as `from` is `style-mismatch` (it has no references to map); a default paragraph style as `from` maps the
  paragraphs without a `w:pStyle` too. `to` not in the document and no template → `missing-style`; not in the template →
  `bad-params`.
* Changes: paragraph style — every paragraph whose style (pStyle, else the default) is `from` gets `w:pStyle` = `to`;
  character style — every run and paragraph mark whose `w:rStyle` is `from` gets `to`; table style — every table whose
  `w:tblStyle` is `from` gets `to`. Nothing else: the `from` definition stays (item 32 removes it when unused), `basedOn`
  / `next` / numbering links naming it stay, field instructions naming it (STYLEREF, TOC) are flagged.
* Tracked: `w:pPrChange` per paragraph, `w:rPrChange` per run/mark; a table style with tracked → `tracked-format`.
* V10 `mapped_styles`: the pairs resolved with its own code, every element re-derived and its change checked exactly
  (`strip_ppr {pStyle, pPrChange}`, `strip_runs`, `strip_marks`, `strip_tbl_style`).

## 8. `remove_unused_styles` (item 32)

* `keep`: ids or names never removed; `which`: `all` (default) | `custom` (only `w:customStyle` on); `types`: the style
  types considered (default all four).
* **Used** = (a) default styles; (b) the `w:val` of every element named `pStyle`, `rStyle`, `tblStyle`, `numStyleLink`,
  `styleLink`, `defaultTableStyle` or `clickAndTypeStyle` anywhere in every text part, the numbering part and the
  settings part (snapshots of tracked changes included); (c) every style whose id, `w:name` or one of its `w:aliases`
  (comma-separated, stripped), casefolded, occurs in the casefolded concatenation of a text part's `w:instrText` /
  `w:delInstrText` texts and `w:fldSimple/@w:instr` values (TOC `\t`, STYLEREF); (d) `keep`; closed under `basedOn`,
  `next`, `link`. **Removed** = every `w:style` with a `w:styleId` not used, of a listed type, custom when asked.
* Nothing unused → a no-op with a note (`allow_missing` default true; false → `not-found`).
* V10 `removed_styles` re-derives the used set and the removable list, compares it with the plan's, registers
  `removed_styles` for the residual, and checks on the OUTPUT that every style reference (b) resolves to a defined style.
  Flagged: `word/stylesWithEffects.xml` (Word 2010's copy) is not updated.

## 9. Around them

* `report.FAILURE_EXPLANATIONS`: `style-mismatch`, `style-import`, `style-numbering`.
* `catalog`: `COMMON_ASSUMPTIONS` for every new op; `CUSTOM_TRANSFORM_LIMITS` gains "paragraph borders and shading" →
  `borders_shading` (the "cannot" list loses "borders, shading").
* `compiler.SYSTEM`: when to use each op (style ids from the profiles; "your styles" come from a template file listed
  among the INPUT FILES or the attached template spec; mapping and removing unused styles are two specs — offer a chain;
  `set_language` alone in its spec).
* `profile`: `markup.languages` (the `w:lang` values in use), `styles_unused` (how many styles nothing uses).
* Worked examples and an office styles template fixture (`examples/templates/OFFICE STYLES.docx`).

## Decisions for Abraham (each with the recommendation taken)

1. Borders replace the paragraph's whole border box (a side not named has none); `"none"` removes every border.
2. Borders/shading and spacing/indents on styles never refuse because a paragraph's direct formatting keeps its own value
   — they flag it (clearing direct formatting is the next step, item 28).
3. Clearing direct formatting keeps hidden text hidden, list numbers, superscripts/subscripts, highlight and outline
   levels unless asked (`also`), and never touches symbol fonts.
4. The proofing language changes every level that names a language plus the document default; runs that name none
   inherit. Never tracked.
5. Applying a style keeps direct formatting by default; `direct_formatting: "clear"` makes the paragraphs follow the new
   style completely.
6. Mapping imports your styles with their numbering definitions; a style the document already has keeps the document's
   definition unless `existing: "replace"`.
7. "Unused" counts field instructions that name a style, links, `basedOn`/`next` chains and tracked-change snapshots as
   uses — never removes something a reject-all or a field update could bring back into use.
