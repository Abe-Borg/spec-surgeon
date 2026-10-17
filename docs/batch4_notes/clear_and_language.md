# Batch 4, items 28 and 29 — integration notes (clear_direct_formatting, set_language)

Built from `docs/BUILTIN_OPS_BATCH4_DESIGN.md` §3 and §4. Code: `src/spec_surgeon/ops/clear_formatting.py` ↔
`src/spec_surgeon/verify/clearing.py` (V10 kind `cleared_formatting`); `src/spec_surgeon/ops/set_language.py` ↔
`src/spec_surgeon/verify/language.py` (V10 kind `language`). Registered in `ops/__init__.py` and at the end of
`verify/props.py`. Tests: `tests/test_clear_formatting.py` (28), `tests/test_language.py` (16).

**Integration blocker you must clear:** `tests/test_planner.py::test_catalog_and_schema_cover_every_op` fails until
`catalog.COMMON_ASSUMPTIONS` holds the two entries below (every registered op needs non-empty assumptions; I was not
allowed to edit `catalog.py`). With the entries pasted in, that test passes (checked locally, not committed).

## (a) `catalog.COMMON_ASSUMPTIONS`

```python
    "clear_direct_formatting": [
        "clearing direct formatting makes the text follow its styles again: fonts, sizes, bold/italic/underline, colour, "
        "caps and the like go from the selected runs and paragraph marks; spacing, indents, alignment, borders, shading, "
        "tab stops and keep/page-break flags from the selected paragraphs — the styles themselves are not changed",
        "hidden text stays hidden, list numbers (numPr), character and paragraph styles and the proofing language are "
        "never touched; highlight, superscript/subscript (vertAlign, position) and outline levels stay unless asked "
        "('also')",
        "symbol and Wingdings runs (and w:sym runs) keep their font and caps — their glyphs depend on them; their other "
        "direct formatting is cleared",
        "'keep' and 'also' take element names: bold is b and bCs, italic i and iCs, size sz and szCs — name both twins "
        "to keep bold, italic or the size; shd and spacing name the run's and the paragraph's alike",
        "a run or paragraph with nothing to clear is left alone; plain for the whole document, tracked (Formatted: …) "
        "when particular paragraphs are targeted",
    ],
    "set_language": [
        "the proofing language changes on every style, run and paragraph mark that names a language, and on the "
        "document default; text that names none inherits the default — nothing else changes and no text is retyped",
        "replace_only changes only the listed languages (e.g. British English to US English, French quotations stay "
        "French); without it every named language becomes the asked one",
        "language is the Latin-text language (w:val); east_asian and complex_script set the East Asian and "
        "complex-script languages only when asked",
        "never tracked (Word does not track a language change); a spec of its own — beside another operation that "
        "changes run properties or styles it is a conflict (offer a chain)",
    ],
```

## (b) `compiler.SYSTEM` guidance (one paragraph, in the style of the batch-3 bullets)

```
- "Clear the direct formatting" / "make the text follow its styles again" / "remove the manual formatting" is
clear_direct_formatting: select scope/paragraphs (the whole paragraph grammar) and runs (narrows the runs and marks
only); run (default true) clears run formatting of runs and paragraph marks, paragraph (default true) clears paragraph
formatting. It never touches styles, list numbers, hidden text or the language; highlight, superscripts (vertAlign,
position) and outline levels stay unless the user asks — then also ["highlight"], ["vertAlign", "position"],
["outlineLvl"]. keep takes element names: "but keep the bold" is keep ["b", "bCs"], "keep the sizes" keep ["sz",
"szCs"]. Say in assumptions what stays. Clearing and other formatting of the same paragraphs (set_properties,
set_font, set_paragraph_props, set_tab_stops) are separate specs: offer a chain, clearing first. "Set the language to
US English" / "proofing language" is set_language {language: "en-US"} — alone in its spec (it changes run properties
and styles everywhere); "British spelling to US" keeps other languages with replace_only ["en-GB"]; the profiles'
markup.languages shows the languages in use. Never tracked.
```

## (c) README section (user's view; for "Built-in operations, batch 4")

```markdown
#### `clear_direct_formatting` — text follows its styles again (item 28)

```json
{"op": "clear_direct_formatting", "select": {"paragraphs": "all"},
 "params": {"keep": ["b", "bCs"], "also": ["highlight"]}}
```

* **What goes** — from the selected runs and their paragraph marks (`run`, default true): fonts, bold, italic, caps,
  small caps, strike, double strike, outline, shadow, emboss, engrave, colour, character spacing, scale, kerning, size,
  underline, text effects, character borders and shading, fit text, emphasis marks. From the selected paragraphs
  (`paragraph`, default true): keep with next, keep lines together, page break before, widow control, line-number
  suppression, borders, shading, tab stops, hyphenation suppression, spacing, indents, contextual spacing, mirror
  indents, alignment, text direction, vertical alignment, text-box tight wrap. The style's values apply again.
* **What stays, always** — character and paragraph styles, list numbers (`numPr`), hidden text (stays hidden), the
  proofing language, right-to-left and complex-script switches, East Asian typography, section breaks, and anything
  Word stores outside its own namespace. **Stays unless you ask** (`also`): highlight (a review cue — and the templating
  slot marker), superscript/subscript and raised/lowered text (m², footnote numbers), outline levels (the table of
  contents) and frames.
* **`keep`** names what must not be cleared (element names: bold is `b` + `bCs`, italic `i` + `iCs`, size `sz` +
  `szCs`; `shd` and `spacing` name the run's and the paragraph's alike). A name that is neither cleared nor opt-in, or a
  name in both `keep` and `also`, is refused (`bad-params`).
* **Symbol fonts are never touched**: a Symbol/Wingdings run (by its own font, its character style or its paragraph
  style) and a run holding a `w:sym` keep their font and caps — their glyphs depend on them; their other formatting is
  cleared.
* **Plain or tracked** as the other formatting (§14.1): plain for the whole document, tracked when particular paragraphs
  are targeted — one `w:rPrChange` per cleared run or mark, one `w:pPrChange` per cleared paragraph, so Word shows
  "Formatted: …" and reject-all brings the formatting back. Plain: a run left with no properties loses its `w:rPr`, a
  paragraph left with none loses its `w:pPr`. Someone's pending formatting change refuses tracked (`tracked-format`) and
  is kept and flagged plain. Already clean runs and paragraphs are left alone ("nothing to clear, left alone: …").
* **Overlaps.** Clearing and any other formatting of the same paragraphs (bold, fonts, spacing, tab stops) are separate
  specs — a chain, clearing first.
* **Verified by V10's `cleared_formatting` kind** (`verify/clearing.py`, nothing shared with the op): the selection, the
  symbol rule and each target's cleared set are re-derived from the source with the user's own `keep`/`also`; each
  cleared run, mark and paragraph must be exactly the source minus the cleared children, in order — no more gone, none
  left, nothing added, an emptied element absent (plain) — with the tracked-change rules. Everything else is the
  residual's.

#### `set_language` — the proofing language (item 29)

```json
{"op": "set_language", "params": {"language": "en-US", "replace_only": ["en-GB"]}}
```

* **`language`** (Latin text, `w:lang w:val`), **`east_asian`** (`w:eastAsia`), **`complex_script`** (`w:bidi`) — at
  least one, each a language tag (`en-US`, `fr-CA`, `ja-JP`). **`replace_only`** — only these languages change, so
  "British spelling to US" leaves a French quotation French.
* **Where it changes**: every style, run and paragraph mark that names a language, and the document default (created in
  `styles.xml` when the file has none — never a new part). Text that names no language inherits the default. A
  language's other attributes stay (setting the Latin language keeps the East Asian one). Never: tracked-change
  snapshots, list numbering, content-control properties, conditional table formatting.
* **Never tracked** (Word does not track a language change either; `tracked: true` is refused). A spec of its own:
  beside another operation that changes run properties of the same text or any style it is a plan-time `conflict`.
* **Layout reference**: the source render, unless East Asian or complex-script languages are set or automatic
  hyphenation is on (the language picks the hyphenation dictionary) — then none.
* **Verified by V10's `language` kind** (`verify/language.py`): every level's change re-derived from the source (the
  same levels, the same attributes), each changed `w:lang` exactly the source's with the asked attributes and nothing
  else in its properties, a created document default in its schema place; and — on the output — every run and paragraph
  mark of every text part must resolve to the asked language through its styles and the default (with `replace_only`:
  none may still resolve to a listed language). The effective language exists only on the verifier's side.
```

## (d) CLAUDE.md both-sides entries

- **Clearing direct formatting** (`clear_direct_formatting`; design `docs/BUILTIN_OPS_BATCH4_DESIGN.md` §3). Cleared run
  names (default) `rFonts b bCs i iCs caps smallCaps strike dstrike outline shadow emboss imprint color spacing w kern sz
  szCs u effect bdr shd fitText em`, opt-in `highlight vertAlign position`; cleared paragraph names `keepNext keepLines
  pageBreakBefore widowControl suppressLineNumbers pBdr shd tabs suppressAutoHyphens spacing ind contextualSpacing
  mirrorIndents jc textDirection textAlignment textboxTightWrap`, opt-in `outlineLvl framePr suppressOverlap`; never
  everything else (w: namespace only). The set for a target = (default ∪ also) − keep, per target type (`keep`/`also`
  from the cleared and opt-in lists only, none in both: `bad-params`). Symbol runs (set_font's definition + `w:sym` when
  `skip_sym_runs`) and symbol marks (without the w:sym rule) never lose `rFonts`/`caps`/`smallCaps`. Result: the source
  element minus the cleared children, in order; plain — an emptied run rPr removed, an emptied mark rPr removed and then
  an emptied pPr (one the op changed); tracked — rPrChange (snapshot the source rPr) / pPrChange (snapshot the source pPr
  without rPr, sectPr, pPrChange); existing markers refuse tracked (`tracked-format`), flagged plain; a tracked change on a
  mark that is a pending revision refuses. Transform: `ops/clear_formatting.RUN_CLEARED/RUN_OPT_IN/PARA_CLEARED/
  PARA_OPT_IN/SYMBOL_KEPT`, `cleared_names`, `gone_names`, `paragraph_clearing`, `check_markers`, `clear_rpr`,
  `clear_paragraph`; verifier: `verify/clearing.V_RUN_CLEARED/V_RUN_OPT_IN/V_PARA_CLEARED/V_PARA_OPT_IN/V_SYMBOL_KEPT/
  V_DEFAULT_PRESERVE`, `v_cleared_sets`, `_v_gone`, `v_decide`, `check_cleared_rpr`, `check_runs_and_mark`,
  `v_expected_ppr`, `check_cleared_ppr` (the symbol rule through `props.symbolic`). `tests/test_clear_formatting.py::
  test_transform_and_verifier_decisions_agree` compares the two decisions on every paragraph of four fixtures under four
  settings and plants shared defects (vanish, vertAlign or numPr in the cleared sets; a symbol rule that forgets the
  paragraph style chain or w:sym; an empty default symbol-font list; marks skipped; emptied elements left).
- **Proofing language** (`set_language`; design §4). Levels: docDefaults' rPrDefault/rPr, every style's own rPr (keyed by
  styleId, the last of a repeated id; a style without one left alone), every run's and paragraph mark's own rPr in every
  text part; never rPrChange snapshots, numbering, sdtPr, tblStylePr. A level gets attribute A = V when its own w:lang
  carries A with a value ≠ V (exact; with `replace_only`, casefolded in the list); docDefaults without replace_only also
  when absent (docDefaults/rPrDefault/rPr/lang created in schema order; no styles part → `structure`); the lang's other
  attributes stay. Transform: `ops/set_language.clean_params`, `language_levels`, `level_gets`, `write_lang`,
  `doc_defaults_rpr`; verifier: `verify/language._v_asked`, `_v_levels`, `_v_gets`, `_check_lang_rpr`,
  `_check_doc_defaults_frame`. The **effective language** of the post-condition (per attribute: direct → character style
  chain (rStyle, else default) → paragraph style chain (pStyle, else default) → table style chain → docDefaults, the first
  whose w:lang carries it) exists only on the verifier side (`verify/language.v_effective_lang`), as the effective font.
  `tests/test_language.py` plants shared defects (docDefaults skipped, styles skipped, a case-sensitive replace_only, a
  writer that drops the lang's other attributes).
- Rule 9 additions: `clear_direct_formatting` touches `(part, bi, "rPr")` per block with a cleared run, `(part, bi,
  "markRPr")` per cleared mark, `(part, bi, "pPr")` **and `(part, bi, <name>)` for every cleared pPr child** per cleared
  paragraph (so `set_tab_stops`' `verify:tabs` read of a paragraph whose tabs it clears is a plan-time conflict), and
  `(part, "scope", "verify")` per part in scope. `set_language` touches `(part, bi, "rPr" | "markRPr")`, `(styles,
  "style:<id>", "rPr")`, `(styles, "docDefaults", "rPr")`, `(part, "scope", "verify")` for every text part and `(styles,
  "*", "verify")`.

## (e) Refusal codes without a `report.FAILURE_EXPLANATIONS` entry

None. The two ops raise only `bad-params`, `tracked-format` (clear) and `bad-params`, `structure` (language) — all
explained already. Both test files scan their op module with `ast` and fail on a code without an explanation.

## (f) The reuse API for `apply_style` (`direct_formatting: "clear"`)

Transform side, `spec_surgeon.ops.clear_formatting` (documented in the module docstring, "Reuse"):

- `ClearSettings()` — §3's DEFAULT rules (frozen dataclass: `run_names`, `para_names`, `runs="all"`,
  `in_revisions="include"`, `marks=True`, `preserve=DEFAULT_PRESERVE_FONTS`, `skip_sym=True`); `cleared_names(keep=None,
  also=None) -> (run names, paragraph names)` validates and builds custom sets.
- `paragraph_clearing(p, runs, sheet, settings=ClearSettings()) -> ParagraphClearing` — ONE paragraph, from the SOURCE
  (`runs` = `selectors.runs_by_paragraph(root, pars)[bi]`, `sheet` = `StyleSheet(pkg)`): `.runs` [(run index, names)],
  `.mark` names or None, `.ppr` names, `.already` Counter, `.changed`. Store it in transform_data (lists) and rebuild it
  at apply time as `ParagraphClearing(runs=[(ri, names) …], mark=…, ppr=…)` (what `ClearDirectFormatting.apply` does).
- `check_markers(p, runs, d, tracked, where, ppr_changes=None) -> (runs/marks flagged, paragraphs flagged)` — raises
  `tracked-format` when tracked (existing rPrChange on a cleared run/mark, existing pPrChange on a changed pPr, a cleared
  mark that is a pending revision). Pass `ppr_changes=True` when the pPr changes anyway (the new pStyle): an existing
  pPrChange then refuses/flags even if no pPr child is cleared.
- `changes_needed(d, ppr_changes=False) -> int` — revision ids `clear_paragraph` consumes tracked (runs + mark + one
  pPrChange); add it to `rev_ids_needed`, and to `rev_delta` (`rPrChange` = runs + mark, `pPrChange` = 1 per paragraph).
- `clear_paragraph(p, runs, d, stamp=None, next_id=0, ppr_edit=None) -> next_id` — applies one decision on the working
  copy. `stamp` (`ooxml.RevisionStamp`) makes it tracked. `ppr_edit(ppr)` is called with the paragraph's pPr (created
  first in the paragraph when absent) after the cleared children are removed and BEFORE the one `w:pPrChange` is
  appended — so the snapshot is the source pPr (without rPr, sectPr, pPrChange) and covers both the clearing and the
  pStyle change. With `ppr_edit` the pPr counts as changed (one pPrChange tracked; never removed as empty — it holds the
  pStyle). Order of ids: runs, then the pPrChange, then the mark's rPrChange.
- `clear_rpr(rpr, names, stamp=None, next_id=0) -> (next_id, emptied)` for a single rPr.

Verifier side, `spec_surgeon.verify.clearing` (module docstring, "Reuse"):

- `v_cleared_sets(keep=None, also=None) -> (run names, paragraph names)`; `v_cleared_sets()` = the default rules.
- `v_decide(p, runs, styles, run_names, para_names, *, runs_pred="all", exclude_rev=False, marks=True, preserve=None,
  skip_sym=True) -> VClearing` (`.runs` {ri: names}, `.mark` names or None, `.ppr` names, `.changed`) — from the SOURCE
  (`p`, `runs` = `PartView.pars[bi]`, `.runs[bi]`; `styles` = `ctx.src_styles`).
- `check_runs_and_mark(ctx, part, bi, oi, d, tracked, author, errs) -> (runs checked, marks checked, mark_stays)` —
  checks every cleared run and the mark (exactly source minus cleared, plain emptied → absent, rPrChange rules), registers
  `ctx.strip_runs` / `ctx.strip_marks`. The caller checks the run structure first (same run count) and counts.
- `v_expected_ppr(S, gone, mark_stays) -> list` — the signature sequence of the output pPr (cleared children and
  pPrChange left out, `MARK` for the mark's rPr when it stays); `v_with_pstyle(seq, style_id)` puts the pStyle first
  (replacing one). `check_cleared_ppr(S, O, expected, tracked, author, where, errs, changed=True)` compares
  (`v_ppr_sequence(O)`), applies the pPrChange rules when `changed` (tracked → present, snapshot = source pPr), and plain
  → `O` absent exactly when nothing is left. The caller registers `ctx.strip_ppr[part][bi] |= {"pStyle", "pPrChange"} |
  d.ppr`.
- `tests/test_clear_formatting.py::test_the_reuse_api_clears_and_sets_a_style_in_one_paragraph_change` drives both
  sides exactly this way (tracked, a pStyle via `ppr_edit`, one pPrChange, the verifier's expectation with
  `v_with_pstyle` passing and failing without it).

## (g) Deviations from the design, and interpretations

1. **`keep`/`also` names are element names** (the design's lists): `keep: ["b"]` keeps `w:b` but clears `w:bCs`. Not
   expanded into the twins as set_properties does — the compiler guidance and assumptions say to name both. A name both
   lists hold (`shd`, `spacing`) applies to runs and paragraphs alike.
2. **An emptied pPr is removed (plain) whether the paragraph clearing or the mark's removal emptied it** — the design
   names only "an emptied mark rPr removed and then an emptied pPr"; only a pPr this op changed is removed (a source's
   own empty `<w:pPr/>` is left alone).
3. **Extra touches for clear**: `(part, bi, <cleared pPr name>)` per cleared paragraph besides `"pPr"` — e.g. `tabs`,
   `pBdr`, `shd`, `keepNext` — so an op that reads one of those children in the output on a paragraph it finds already
   as asked (`set_tab_stops`' `verify:tabs`, `set_borders_shading`' `verify:pBdr`/`verify:shd`) is a plan-time conflict
   instead of a V10 failure after apply (`test_clearing_beside_other_formatting_of_the_same_paragraphs_conflicts`: the
   tab op alone finds the footer paragraph already as asked; beside the clear it is refused, naming `tabs`). No
   per-element touches for runs or marks: no op declares a family read of a run property today (set_properties' gap is
   recorded below); add `(part, bi, "markRPr:<name>")`-style aspects when one does.
4. **set_language refuses a file without a styles part always** (`structure`), also with `replace_only` (where the
   document default would not be needed) — the design's sentence read unconditionally.
5. **set_language "≠ V" is exact**: a run with `en-us` is rewritten as `en-US`; `replace_only` is casefolded. The
   post-condition with `replace_only` reads "any resolves to a listed value" as "a listed value other than V" (V itself
   may be listed). It maps source runs to output runs by the text alignment and run index; a paragraph whose run count
   another op changed gets the output-side checks only.
6. **set_language: a style without `w:styleId` is left alone** (cannot be referenced); a repeated id: the last one, as
   both style readers keep it.
7. **set_language refuses unknown parameters in the op itself** (`bad-params`), beside the spec schema; `tracked` must be
   absent or false (the design: `tracked: true` → `bad-params`).
8. **Layout reference `none` only when something changes** (both ops), as set_properties.
9. **Stricter than the design**: the language kind checks the created containers' places relative to the source
   (`docDefaults` where the source had it, created: first in `w:styles`; `rPrDefault` likewise, created: before
   `pPrDefault`), which the residual cannot see when it drops them as empty; the clear kind checks the output pPr's
   whole child sequence including the mark's place.
10. **Verifier defaults are its own** (batch 2 rule 3): the assertions carry only the user's own parameters (keys
    present); the clear kind has its own default symbol-font list (`V_DEFAULT_PRESERVE`) instead of the transform's
    resolved list (set_properties' assertion carries the resolved list).
11. `run: false` with `also` naming run opt-ins is accepted silently (nothing is cleared from runs).

**Recorded follow-ups (not changed — outside these items):**

- *set_properties leaves already-as-asked targets undeclared.* Its post-condition reads every selected run's, mark's and
  paragraph's value in the output, but it touches nothing for a target already as asked. `clear_direct_formatting` is
  the first op that can REMOVE a property another op found already in place: `set_properties {run: {caps: true},
  include_paragraph_marks: false}` + `clear_direct_formatting` on the SECTION title plans cleanly and fails V10 after
  apply ("w:caps resolves to False … asked True" — loud, the file goes to `failed/`, but late). Fix on set_properties'
  side: declare `(part, bi, "verify:rPr")` per block with a selected run, `"verify:markRPr"` per selected mark,
  `"verify:pPr"` per paragraph with paragraph flags asked.
- *set_language's whole-document post-condition* reads every run of every text part; an op adding runs with copied run
  properties elsewhere in the same spec (`insert_paragraph` inheriting an anchor whose language changes) plans and fails
  the post-condition after apply. The design's `(part, "scope", "verify")` touches are kept; the guidance says
  `set_language` alone in its spec.

## (h) Test count

44 new tests (`tests/test_clear_formatting.py` 28, `tests/test_language.py` 16). Full suite in the container (`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest -q -s`), with the two catalog entries absent: 1643 passed, 1 failed (`test_planner.py::test_catalog_and_schema_cover_every_op` — the missing `COMMON_ASSUMPTIONS` above), 4 skipped (baseline 1600 + 4 skipped). With the two entries pasted into `catalog.py` that test passes too (checked: `test_planner.py` + both new files, 54 passed), so 1644 passed, 4 skipped once the catalog is integrated.
