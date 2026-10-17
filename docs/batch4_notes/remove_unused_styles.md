# Batch 4 notes — `remove_unused_styles` (item 32)

Files: `src/spec_surgeon/ops/remove_unused_styles.py` (the op and the transform-side definitions),
`src/spec_surgeon/verify/unused.py` (`UNUSED_KINDS`: the V10 kind `removed_styles`), `tests/test_remove_unused_styles.py`.
Registration: one import line in `src/spec_surgeon/ops/__init__.py`; the kind module imported and `KINDS.update`d at the
end of `src/spec_surgeon/verify/props.py`. Nothing else in the engine changed (the `Ctx.removed_styles` hook and its
residual handling were the groundwork commit's).

## (a) COMMON_ASSUMPTIONS

```python
"remove_unused_styles": [
    "a style is used when a paragraph, run or table names it (body, headers, footers, footnotes, endnotes, comments — "
    "pending tracked changes included, a reject-all would bring them back), when the numbering or the settings name it, "
    "when a field instruction names it (STYLEREF, TOC \\t — by id, name or alias, any case), or when it is a default style",
    "a style that a staying style is based on, followed by (next) or linked to stays too",
    "a TOC, INDEX or TOA field keeps the TOC 1–9 / Table of Figures, Index 1–9 / Index Heading, Table of Authorities / "
    "TOA Heading styles a field update would apply",
    "which: all removes unused Word built-in styles too (Word brings back its own definition when one is used later); "
    "which: custom removes only the document's own (customStyle) styles — say which in the assumptions",
    "'keep' names styles never removed (ids or names, any case); styles the user mentions as theirs go there",
    "used is judged on the file before this spec: after mapping or applying styles, remove unused styles as a chain's last "
    "stage",
    "styles.xml only, never tracked; a file with nothing unused is left as it is",
],
```

## (b) Guidance for the compiler's SYSTEM prompt

> **Remove unused styles** (`remove_unused_styles`, no selector): "clean up the styles", "remove the architect's leftover
> styles", "delete unused styles". Default `which: "all"` removes every style nothing uses, Word's unused built-ins
> (Heading 4–9, the table gallery) included — Word brings back its own definition if one is used later; use `which:
> "custom"` when the user means the document's own styles ("the architect's styles", "the custom styles"), `types` when
> they name a kind ("unused table styles" → `["table"]`). Styles the user wants to keep whatever happens go in `keep`
> (ids or names from the profiles' `styles_in_use` / `styles_unused`). Used = named by a paragraph, run, table, the
> numbering, the settings or a field instruction (tracked-change snapshots included), a default style, or reached from
> one by basedOn / next / link — never say it removes a style a field or a reject-all could bring back. It judges "used"
> on the file BEFORE the spec: when the user also maps or applies styles ("map the architect's styles to ours and remove
> the rest"), offer a **chain** with `remove_unused_styles` as the last stage (in one spec the old styles would stay, and
> a style another op applies or changes is a plan-time conflict). It is a spec of its own beside
> `apply_header_footer_template` (which plans from the styles part as a whole). Never tracked; place no words.

## (c) README section (user's view)

#### `remove_unused_styles` — remove the styles nothing uses (item 32)

```json
{"op": "remove_unused_styles", "params": {"which": "custom", "keep": ["Arch Note"]}}
```

An architect's spec carries dozens of style definitions nobody uses any more — the template's leftovers, Word's unused
built-ins, the table-style gallery. The operation removes them from `styles.xml` and touches nothing else: no paragraph,
no numbering, no setting changes, and nothing on any page moves (the source render stays the layout reference).

* **What counts as used.** A style stays when a paragraph, run or table names it — in the body, headers, footers,
  footnotes, endnotes or comments, **pending tracked changes included** (the old style in a "Formatted: Style" change
  comes back on reject-all) —, when the numbering names it (a list level's style, a list linked to a numbering style),
  when the settings name it (the default table style, Click and Type's style), when a **field instruction** names it
  (`STYLEREF "Arch Field Only"`, a TOC's `\t "Arch Article,2"` — by id, name or alias, in any case), when it is a
  **default style**, when you list it in `keep`, or when a style that stays is **based on it, followed by it or linked
  to it** (so "Heading 1" keeps "Heading 1 Char" and its base "Normal"). A document with a **TOC, INDEX or TOA field**
  keeps the styles a field update applies ("toc 1"–"toc 9", "Table of Figures", "index 1"–"index 9", "Index Heading",
  "Table of Authorities", "TOA Heading"): removing a customised "TOC 3" would undo it at the next update.
* **`which`** — `all` (the default) removes every unused style, Word's built-ins included (Word brings back its own
  definition if one is used later); `custom` removes only the document's own styles (Word's *custom style* mark). A
  built-in that stays keeps what it links to, so "custom" never strands Word's "Heading 4" without its "Heading 4 Char".
* **`types`** — the kinds considered: `paragraph`, `character`, `table`, `numbering` (default all four). Styles of the
  other kinds stay, and so does everything they are based on or linked to.
* **`keep`** — ids or names never removed (any case; a name that matches no style is said in the preview).
* **The preview lists** how many go, by kind, and their ids; which styles stay only because a field names them; how many
  of the removed are Word built-ins; and a flag when the file has Word 2010's `stylesWithEffects.xml` copy (not updated;
  Word 2013 and later ignore it).
* **A file with nothing unused** is left as it is ("nothing to do"); `allow_missing: false` sets it aside. A file that
  defines one style twice is set aside (`structure`: which definition Word uses is not certain). Never tracked — Word
  does not track style definitions either.
* **Overlaps.** "Used" means used in the file as it is before the spec: a style another operation in the same spec stops
  using stays. To map or apply your styles and then remove the architect's leftovers, run them as a
  [chain](#chains-several-recipes-one-after-another) with `remove_unused_styles` last. A style another operation in the
  same spec applies, maps to, inserts a paragraph with or changes, beside its removal, is a plan-time `conflict`; so is
  the header/footer templating op (it plans from the whole styles part).
* **Verified by V10's `removed_styles` kind** (`verify/unused.py`, nothing shared with the op): the used styles and the
  list that goes are worked out again from the source with the verifier's own reading of every rule above and compared
  with the plan; the output's styles part must be the source's minus exactly those definitions (every other style
  byte-for-byte the same, in order); and on the output, every style reference of the text, numbering and settings parts,
  every basedOn / next / link and every style a field names must still find its definition — the operation can never
  leave a dangling style reference.

## (d) CLAUDE.md entries

Both-sides definition (under "Built-in operations batch 4"):

- **Unused styles** (`remove_unused_styles`). Styles = the top-level `w:style` of the styles part; two with one
  `w:styleId` refuse (`structure`); type `w:type` (default paragraph); default/custom = `w:default`/`w:customStyle` on
  (1/true/on); a style's names = its id, `w:name/@w:val` and each comma-separated stripped entry of `w:aliases/@w:val`,
  casefolded, empty ones dropped. Scanned parts = every text part + every numbering and settings part (main part's
  relationships). Referenced = the `w:val` of every W-namespace `pStyle`, `rStyle`, `tblStyle`, `numStyleLink`,
  `styleLink`, `defaultTableStyle`, `clickAndTypeStyle` anywhere in a scanned part (snapshots included). Named by fields
  = per text part, the casefolded concatenation in document order of `w:instrText`/`w:delInstrText` texts and
  `w:fldSimple/@w:instr`: a style one of whose names is a substring; field-generated = a text part whose instruction
  text holds `toc` keeps the styles named `toc 1`…`toc 9`, `table of figures`; `index`: `index 1`…`index 9`,
  `index heading`; `toa`: `table of authorities`, `toa heading`. Keep = a style one of whose names equals an entry,
  stripped and casefolded. Used = every default ∪ referenced ∪ named ∪ field-generated ∪ kept. Eligible = has an id,
  type in `types` (default all four), custom when `which: custom`. Stays = used ∪ every non-eligible style (id-less
  included), closed under the `w:val` of `w:basedOn`/`w:next`/`w:link`. Removed = eligible styles not staying, in part
  order. Transform: `ops/remove_unused_styles.style_table/style_names/eligible/scanned_parts/references/instruction_text/
  named_in_fields/field_generated/kept_by/seed_styles/closure/analyze`; verifier: `verify/unused._v_styles/_v_names/
  _v_eligible/_v_parts/_v_refs/_v_instr/_v_named/_v_kept/_v_reach/_v_removable` (+ the output post-condition
  `_v_dangling`: every reference — scanned parts, every output style's basedOn/next/link, field names and field-generated
  names — to a style the SOURCE defined must find it in the output). The kind registers its OWN list in
  `ctx.removed_styles`, checks each is absent and every staying style present (the residual skips a byte-identical styles
  part). `tests/test_remove_unused_styles.py` compares the two on seven fixtures × five parameter sets and plants shared
  defects (snapshots skipped, fields ignored, case-sensitive names, no closure, a closure seeded by the used styles alone,
  numbering or settings not scanned, the field-update table forgotten, a default `which`, a case-sensitive keep).

Rule 9 (append a batch-4 sentence): `remove_unused_styles` touches `(styles, "style:<id>", a)` for `a` in `remove`,
`rPr`, `pPr`, `tblPr` per removed style and declares no reads: its kind re-derives from the SOURCE, and the ops that ADD
a reference to a style declare `(styles, "style:<id>", "verify")` (`insert_paragraph`/`ensure_end_of_section` with a
style, `apply_style`, `map_styles`); `apply_header_footer_template` reads the styles part whole. A style another op in
the same spec stops using stays (judged on the source; the plan says so).

"Where things are": `ops/remove_unused_styles.py` (`remove_unused_styles`, `analyze`, `clean_params`, `FIELD_STYLES`,
`REF_NAMES`); `verify/unused.py` (`UNUSED_KINDS`: `removed_styles`).

## (e) Refusal codes without a FAILURE_EXPLANATIONS entry

None: the op raises `bad-params`, `not-found` and `structure` (all explained) and `internal` (apply, when a style to
remove is no longer in the working copy — an internal error by `diagnostics.classify`, deliberately unexplained as in
batches 1–2). Suggested wording addition for `structure`: "… (two comments with one id, a watermark sharing its run with
other content, a style defined twice)".

## (f) Deviations from the design (§8) and why

1. **Stays = closure of the used styles AND of every style the op does not consider.** §8 read literally closes only the
   used set, then removes unused styles of the listed types / custom ones. With `which: custom` (or `types`) that leaves
   dangling links: python-docx's built-in "Heading 4" (not custom) stays, its unused linked "Heading 4 Char" is custom and
   would go — the output's `w:link` names a removed style. A style the op does not consider stays whatever it decides, so
   what it names must stay too. With the defaults (`all`, every type) the two readings agree. Planted:
   `test_a_shared_closure_seeded_by_the_used_styles_alone_is_caught`.
2. **Field-generated styles** (extension, keeps more): decision 7 says nothing a field update could bring back into use
   goes; a TOC update applies "toc 1"…"toc 9" (and "table of figures"), an INDEX update "index 1"…"index 9" / "index
   heading", a TOA update "table of authorities" / "toa heading" — removing a customised unused "TOC 3" would undo the
   customisation at the next update. Detected by substring (`toc`, `index`, `toa`) in the part's instruction text, like
   §8 (c); matched by name/alias (Word stores built-in names in English whatever the language). Planted:
   `test_a_shared_rule_that_forgets_the_field_update_styles_is_caught`.
3. **The output post-condition is wider than §8 (b) and relative to the source.** It checks the scanned parts' references
   AND every output style's `basedOn`/`next`/`link` AND the styles field instructions name (by name, alias or the
   field-update table); a reference "dangles" only when the source defined its style and the output does not — a
   reference that already dangled in the source is not the op's doing (otherwise a file with one such reference could
   never pass).
4. **Every style with `w:default` on counts as a default**, not only the first of its type (keeps more).
5. **`keep` matching**: an entry, stripped and casefolded, against the style's id, name and aliases, all casefolded —
   more generous than §5's resolution (id exactly, else name); it can only keep more. Entries that name no style are a
   note, never a refusal.
6. **No styles part**: a no-op with a note (there is nothing to remove); `allow_missing: false` → `not-found`, as for a
   file whose styles are all used.
7. **Two definitions of one style id refuse (`structure`)** on both sides (the verifier raises "the op must refuse"):
   which definition Word uses is not certain, and removing "the" style would be a guess. §8 is silent.
8. **Id-less `w:style` elements** are never removed and count as staying (their basedOn/next/link keep their targets),
   so the post-condition holds for them too.
9. **The reference scan matches W-namespace elements only** (the schema's) and ignores one without `w:val`.
10. **The kind checks absence and presence itself**: the styles residual runs only when the styles part's bytes differ,
    so an output that removed nothing at all would otherwise pass it; the kind fails it ("N unused style(s) are still
    defined in the output") and fails a staying style that is gone.
11. **Plan-time reads not declared** (as the design says): the op declares only its removals. Cross-op note for the
    style-import builder: an imported style's `basedOn`/`next`/`link`, or a numbering level's `w:pStyle`, naming a style
    the DOCUMENT already holds (and nothing else uses) should be declared `(styles, "style:<id>", "verify")` by the import
    — otherwise `apply_style`/`map_styles` beside `remove_unused_styles` plans, and only this kind's post-condition (a
    dangling basedOn / numbering pStyle) fails it after apply.
12. **Unknown parameters are refused by the op too** (`bad-params`; `validate_spec` already refuses them in a spec).

Known failing test outside my files: `tests/test_planner.py::test_catalog_and_schema_cover_every_op` requires a
`COMMON_ASSUMPTIONS` entry for every registered op; `catalog.py` was off limits — (a) above is the entry. It passes once
the entry is added (checked locally by adding it, then reverted).

## (g) Tests

`tests/test_remove_unused_styles.py`: 50 tests (end to end — also once through `run_batch` from a validated spec —: defaults, `which: custom`, `types`, `keep` by id and by
name, single-use styles, basedOn ancestors, numbering and settings references, aliases + `w:fldSimple` + case, the field
update styles; no-op file, no styles part, duplicate ids, 11 parameter refusals, refusal codes explained; wrong outputs;
10 wrong implementations shared by plan and apply; the two readings agree on 7 fixtures; conflicts beside
`set_properties`, `set_style_font`, `insert_paragraph`, a second `remove_unused_styles`, the templating op, and two
combinations that plan and verify).

Full suite in the container (`PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3 -m pytest -q -s`): 1648 passed, 4
skipped, 1 failed — `tests/test_planner.py::test_catalog_and_schema_cover_every_op`, the missing `COMMON_ASSUMPTIONS`
entry (above); that run had 49 of this file's tests (the batch test came after), so with the catalog entry: 1650 pass,
4 skip (baseline 1600 + 50).
