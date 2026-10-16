# `set_properties` — design note (built-in operations round, part 1)

2026-10-16. Status: **proposed, awaiting Abraham's approval** — nothing below is built yet. Reading order: CLAUDE.md
hard rules 2, 3, 8, 9 and 16, the "Effective pinnable properties" definition, and the plan's Phase 8 status
(`set_properties` and its generic V10 kind, ideas #22–23, deferred to this round).

## Why

Three requests the engine refuses today, and a custom transform cannot take (no V10 kind checks them):

- "Make every PART 1 – GENERAL / PART 2 – PRODUCTS / PART 3 – EXECUTION heading bold and keep it with the next paragraph"
- "Make the article titles all caps and dark blue"
- "Turn off italic in the body text"

One operation, `set_properties`, and one V10 assertion kind, `properties`, built verifier-first.

What it never does: it adds no characters — "all caps" is `w:caps`, a display property; the letters stay exactly as typed
(rule 4 holds). It never changes a paragraph's style, never accepts or rejects a revision, never touches numbering,
borders, shading, highlight or hidden text.

## 1. Properties in v1

| group | property | value | written as |
|---|---|---|---|
| `run` | `b`, `i`, `caps`, `smallCaps`, `strike` | true / false | `<w:b/>` (on) · `<w:b w:val="0"/>` (off) |
| `run` | `u` | single, double, words, dotted, dash, wave, none | `<w:u w:val="…"/>`; other attributes of an existing `w:u` kept |
| `run` | `color` | 6 hex digits, or `auto` | `<w:color w:val="002060"/>` — exactly that attribute: `themeColor`/`themeTint`/`themeShade` are removed (a theme colour overrides the value in Word, as a theme font overrides a font name) |
| `paragraph` | `keepNext`, `keepLines`, `widowControl`, `pageBreakBefore` | true / false | `<w:keepNext/>` · `<w:keepNext w:val="0"/>` |

`run` properties go on runs, paragraph marks and style `rPr`; `paragraph` properties on a paragraph's `pPr` and a
paragraph style's `pPr`. Elements are created in `RPR_ORDER` / `PPR_ORDER`. `false` means explicitly off; there is no
"remove the direct value and let the style decide" in v1 (its effect would depend on the chain — `false` is certain).

Left out, and why: `highlight` (the template slot marker and a reviewer's tool, not client formatting); `vanish`
(hides text from the reader — loss by another name); `bCs`/`iCs` (complex-script variants, left alone like `w:szCs`);
`dstrike`, `outline`, `shadow`, `emboss`, `imprint` (legacy effects); character spacing, kerning, position, scale;
`vertAlign` (super/subscript needs run splitting); borders and shading (`bdr`, `shd`, `pBdr` — their own round);
`numPr`/`outlineLvl` (the numbering round); `contextualSpacing` and the spacing family (`set_paragraph_props`); theme
colours and underline colours; changing `pStyle`/`rStyle` (a different operation — it changes everything at once).

## 2. Targets, and how "bold these headings" maps

Two target families, chosen by the selector; one family per op call (mixing them is `bad-params`):

- **Direct formatting** — `select.scope`, `select.paragraphs`, `select.runs` (set_font's grammar, unchanged). `run`
  properties go on every selected run of the selected paragraphs and on their paragraph marks
  (`include_paragraph_marks`, default true); `paragraph` properties on each selected paragraph's `pPr`. Symbol runs
  (set_font's definition: `preserve_fonts`, `w:sym`) never get `caps`/`smallCaps` — case-mapping a Wingdings character
  changes the glyph — but do get the other run properties (a bold bullet is still the same bullet). Runs inside pending
  revisions are included unless `in_revisions: "exclude"`.
- **Style definitions** — `select.styles: {"ids": [...]}` (explicit ids only; paragraph and character styles;
  `paragraph` properties only on paragraph styles). Writes the style's `rPr`/`pPr` in `styles.xml`. Never tracked.

**A target that already shows the asked value is left alone and not counted.** "Already" is the *effective* value
(direct → character-style chain → paragraph-style chain → table style → docDefaults; toggle rule in §3), so "bold the
headings" on headings whose style is already bold changes nothing, and tracked mode records "Formatted: Bold" only where
Word itself would have recorded a change. A run whose effective toggle is ambiguous counts as needing the change (direct
formatting wins, so the result is certain either way). Note: `caps` is a property, not a look — a title already typed in
capitals still gets `w:caps` (no visible change); the compiler narrows with `is_all_caps: false` when that matters.

**Compiler guidance (recommended):** direct formatting on the selected paragraphs is the default — targeted, hence
tracked (§14.1), hence visible to the reviewer in Word as "Formatted: Bold, Keep with next". The style target when the
user talks about the style, or wants every paragraph of that style to change now and in future. The choice is stated in
`assumptions`.

**Style-target certainty** (plan-time refusals, re-checked by V10): the plan enumerates every run, mark and paragraph in
every text part whose style chain includes the selected style — styles *based on* it inherit the change (Heading2 based
on Heading1), which the plan notes — and refuses when the result would not be the asked value everywhere:
`toggle-ambiguous` when a toggle would then be specified at more than one style level for some run (docDefaults, table
style, paragraph style, character style — Word XORs them; not modelled, as today); `conflict-property` when a direct
value on a run, mark or paragraph using the style disagrees with the asked value (Word keeps direct formatting; the
paragraphs target overrides it, or the user clears it in Word).

## 3. The V10 kind `properties` — exactly these properties on exactly these targets, nothing else

One kind, `target: "direct" | "styles"`. Everything is re-derived in `verify/props.py` with its own selectors, style
chains and a resolver of its own (nothing from `ops/`, rule 1):

1. **Selection and counts.** The scope's parts, paragraphs (`para_matches`), runs (`target_matches`, the symbol rule,
   revisions), marks — and *which properties each target gets*, from the SOURCE by the same "already" rule.
   `expected_runs`, `expected_marks`, `expected_paragraphs` (direct) or `expected_ids` (styles) must equal the plan's.
2. **Each target, whole.** For every property it gets, the output element equals the expected one built from the source
   element and the asked value (table in §1). Every other child of the `rPr`/`pPr` — including asked properties the
   target already had — identical to the source. Tracked: `w:rPrChange`/`w:pPrChange` as the last child, with `w:id`,
   the author and a snapshot equal to the source properties; a source already carrying one is a FAIL (the op must
   refuse); plain: the change marker unchanged. The same strictness as `run_fonts`/`paragraph_props` — their
   change-marker tails become shared verifier helpers, byte-for-byte the same checks.
3. **Effective result** (new; today's kinds have none): in the OUTPUT, each asked property resolves to the asked value
   on every target — for a style target, on every run/mark/paragraph whose chain includes the style, across the text
   parts present in the output. A toggle the verifier cannot resolve (two style levels) is "could not evaluate" = FAIL.
4. **Strip only what was compared whole**, then residual identity as today: target run/mark `rPr`; the asked `pPr`
   children + `pPrChange` of target paragraphs; target style `rPr`; the asked `pPr` children of target styles (a new
   `strip_style_ppr`). A non-target run in a selected paragraph (already bold) stays in the residual, so a no-op
   `rPrChange` on it fails.

**Both-sides definition** (to be added to CLAUDE.md's list): the "gets"/expected-element rule above and the op's
effective resolver — toggles (`b`, `i`, `caps`, `smallCaps`, `strike`): direct wins; specified at exactly one style
level → that value; nowhere → off; more than one → ambiguous (`None`: the style target refuses, the direct target
sets); `u` and `color`: nearest level wins (defaults none / `auto`); paragraph flags: nearest level wins, defaults off
except `widowControl` on (ECMA-376 §17.3.1.44). Transform: new functions in `ops/effective.py` (the pinnable
`Resolver.run/paragraph` outputs are untouched — the templating op pins their diff); verifier: `verify/props.py`. A test
compares the two on every run, mark and paragraph of the fixtures, ambiguity included.

## 4. Overlap with set_font / set_paragraph_props, and rule 9

Ownership: `set_font` — `rFonts`, `sz`; `set_paragraph_props` — `spacing`, `ind`, `jc`; `set_properties` — the table in
§1. Nothing is owned twice; `has_bold` and `is_all_caps` stay selectors.

Touches, with today's aspects: `(part, block, "rPr")`, `"markRPr"`, `"pPr"`, `(part, "scope", "verify")` per scope part;
styles `(styles.xml, "style:ID", "rPr" | "pPr")`. So `set_properties` run props + `set_paragraph_props` on the same
paragraphs is allowed and verified (rPr vs pPr); `set_font` + `set_properties` on the same paragraphs is `conflict`; two
pPr ops on one paragraph are `conflict`.

**Recommendation: keep today's granularity.** Each kind proves "whole `rPr`/`pPr` = source + exactly my change", and a
tracked change is one `rPrChange`/`pPrChange` per target — two ops on one target cannot both hold. Finer aspects
(`rPr:b` vs `rPr:rFonts`) would need every kind to tolerate what another assertion covers on the same target, plus a
tracked-aware conflict rule: a verifier refactor that weakens a simple check. v1 instead lets one `set_properties` call
carry all of its properties (bold + keep-with-next + colour on one selection is one op, never a conflict with itself);
"Arial and bold on the same headings" is two specs, which the compiler already knows to say. Follow-up, recorded.

## 5. Tracked vs plain; an existing rPrChange/pPrChange

§14.1 unchanged: plain for a whole-document selection, tracked when `paragraphs` narrows it; explicit `tracked` wins;
style targets refuse `tracked: true`. Tracked: one `w:rPrChange` per changed run/mark and one `w:pPrChange` per changed
paragraph, snapshot = the properties before (`ooxml.property_change`). A target already carrying someone's
`rPrChange`/`pPrChange`: tracked → `tracked-format` (two snapshots cannot be expressed); plain → changed and flagged
(reject-all restores the older snapshot). A paragraph mark that is itself a pending revision + tracked →
`tracked-format`. Exactly as set_font / set_paragraph_props today.

Layout reference: `none` when a property that reflows text or pages is set (`b`, `i`, `caps`, `smallCaps`, the paragraph
flags); `source` when only `color`, `u` or `strike` change — V7 then still proves the pages did not move.

## 6. What changes around it

- `catalog.py`: `COMMON_ASSUMPTIONS["set_properties"]`; `CUSTOM_TRANSFORM_LIMITS` gains the family "bold, italic,
  underline, caps, small caps, strikethrough and text colour of selected text or styles; keep-with-next, keep lines
  together, widow control, page break before" → `properties`, and "bold, italic, underline, caps" / "text colour" leave
  `cannot` (highlight, borders, shading stay). The schema comes from the op: `run`/`paragraph` objects with per-property
  types and enums, so a misspelled property or a bad value is refused by `validate_spec`.
- `compiler.SYSTEM`: one line — caps is display formatting (no letters retyped); direct formatting by default, the
  style when the user asks for it; one `set_properties` per selection carrying all its properties.
- `profile._style_entry`: `italic` and `color` beside `bold`/`caps`, so Claude can see where italic or a colour comes from.
- `report.FAILURE_EXPLANATIONS`: `conflict-property` (missing today; `set_paragraph_props` raises it too).
- `examples/specs/bold-part-headings.json` (worked example); README (operations table, a section of its own, codes, test
  count); CLAUDE.md (the both-sides definition, rule 8's kinds, "Where things are"); the plan's status section.
- Not changed: the selector grammar, `Resolver`/`Eff` outputs, `set_font`/`set_paragraph_props`, the codegen whitelist
  (a custom transform may now emit a `properties` assertion and be verified).

## 7. Tests (step 3)

`tests/make_fixtures.build_formatted()`: direct italic runs and an italic paragraph style used by some body text; a bold
character style inside a heading (the ambiguity case); a heading with one word `w:b w:val="0"` (direct override); a run
with `w:color` + `themeColor`; an underlined run with an underline colour; a paragraph with `keepNext` already; a run
with an existing `rPrChange`; a Wingdings bullet and a `w:sym` run in a paragraph to be bolded and capitalised; a
Heading2 based on Heading1; the heading style used in a header part; a table paragraph — plus the PART headings of
`build_rich` for the end-to-end example.

Per property family (toggles, `u`, `color`, paragraph flags) and per target (direct plain, direct tracked, style `rPr`,
style `pPr`): end to end `record.passed`; a wrong OUTPUT (element missing, wrong `val`, theme attribute left, a
neighbouring property changed, a no-op `rPrChange` on an already-bold run, a wrong snapshot, a non-target run changed, an
inherited Heading2 left ambiguous); a wrong IMPLEMENTATION shared by plan and apply (the transform's on/off writer, its
"already" resolver, its symbol rule and its certainty checks, each monkeypatched) — every one caught by V10 alone. The
resolver agreement test; conflicts (`set_font` + `set_properties` → `conflict`; `set_properties` + `set_paragraph_props`
→ passes); `validate_spec` refusing bad values; the `CUSTOM_TRANSFORM_LIMITS` ↔ `KINDS` mapping test unchanged.

## Decisions for Abraham (each with the recommendation above)

1. Property list of §1 — in: b, i, caps, smallCaps, strike, u, color; keepNext, keepLines, widowControl, pageBreakBefore. Out: the rest, for the reasons given.
2. Two target families in one op (direct formatting; style definitions by id); direct formatting is the compiler's default.
3. "Already at the asked value" targets are skipped and not counted (effective value; ambiguity = set it).
4. Symbol runs: no `caps`/`smallCaps`; the other run properties apply.
5. `false` = explicitly off; no "clear" value in v1.
6. Rule 9: today's aspect granularity stays — font and bold on the same paragraphs are two specs in v1.
7. Style targets refuse when the effective result is not certain (`toggle-ambiguous`, `conflict-property`); V10 re-checks the effective result for every target of either family.
8. Layout reference `source` for colour/underline/strike alone, `none` otherwise.
9. Profile gains `italic` and `color` per style in use.
