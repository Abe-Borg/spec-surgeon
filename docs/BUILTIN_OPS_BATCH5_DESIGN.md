# Built-in operations, batch 5 — design note

2026-10-17. Abraham's request, the fifth chunk of prebuilt operations (items 33–40 of his list; ★ = he starred it):

| # | Asked for | Built as | Mode |
|---|---|---|---|
| 33 ★ | Convert to your PART / 1.1 / A. / 1. / a. scheme from an example file | **new op `convert_numbering`** + V10 kind `converted_numbering`, over the **shared list model** (§0) and the **scheme** (§0.3); the example file's styles come in through batch 4's style import | formatting |
| 34 ★ | Manual to automatic numbering: typed "A." and "1." become real list numbers; the typed characters removed exactly as predicted, no new words | **new op `convert_typed_numbers`** + V10 kind `typed_numbers` — the scheme's levels, the batch-1 edit core (extended to delete a tab, §2.3) and the post-condition "the document reads the same" | tracked by default (text is removed) |
| 35 | Restart numbering per PART; change a level's format or zero-padding | **new op `set_list_level`** + V10 kind `list_level` (a level's `numFmt`, zero padding, `start`, `lvlRestart` in `numbering.xml`) | formatting |
| 36 | (audit) Numbering gaps, duplicates, empty numbered paragraphs that use up a number | **new op `audit_numbering`** — report only (flags); opt-in `unnumber_empty` fix + V10 kind `unnumbered_empty` | none / formatting |
| 37 | Table borders, cell margins, width, repeat header row, rows that don't split across pages | **new op `set_table_props`** + V10 kind `table_props`, over a **table selector** (§5.1) | formatting |
| 38 | Apply a table style to every table | **new op `apply_table_style`** + V10 kind `applied_table_style` (the style from the document or imported, batch 4 §5) | formatting |
| 39 | Different first page on/off; header/footer distance from the page edge | **`set_page_setup` gains `different_first_page`**; the distances are its existing `margins.header` / `margins.footer` | formatting |
| 40 | Landscape pages for chosen sections (schedules) | **`set_page_setup`'s `sections` selector gains a content form** (`containing`, `with_tables`, `index_range`) re-derived by V10 | formatting |

Decisions below were taken on Abraham's behalf, as in batches 1–4, and can each be changed (the list is at the end).
Reading order: CLAUDE.md hard rules 1–3, 8–12, "Run text and in-paragraph edits", "Style import", batch 4's design §0–§6,
`src/spec_surgeon/skill/references/playbooks/numbering-conversion.md` (the written procedure item 33 implements) and
`ooxml-lessons.md` §6.

No operation in this batch writes a character. `convert_typed_numbers` REMOVES characters — the typed number and the
whitespace after it — and only where the list it puts in their place shows exactly that number (§2.4). The scheme's
numbering text (`w:lvlText`, e.g. `PART %1 -`) is the user's own: it comes from their example file or their document,
never from the engine (rule 4).

## 0. The shared list model (`ops/lists.py` ↔ `verify/lists.py`)

Numbering was the one formatting family the engine never modelled (batch 3 rule 5: "do not improve this into reading
numbering.xml without a both-sides definition of list levels (pStyle links, lvlOverride, numStyleLink) and its tests").
This batch writes that definition, once, on both sides; every numbering op predicts with the transform side and its
V10 kind recomputes with the verifier side. `tests/test_lists.py` compares the two on every paragraph of the numbering
fixtures and on random list definitions, and plants shared defects in each rule.

### 0.1 Definitions

* **Numbering part**: the main part's `numbering` relationship. None → every paragraph is unnumbered.
* **num**: a `w:num` child of the root with a whole-number `w:numId` → its `w:abstractNumId/@w:val` (whole number) and
  its overrides: per `w:lvlOverride` with a whole `w:ilvl` 0–8, `startOverride` (`w:startOverride/@w:val`, whole number,
  or none) and a full override level (its `w:lvl` child, or none). **abstractNum**: a `w:abstractNum` child with a whole
  `w:abstractNumId` → its levels (`w:lvl` children by whole `w:ilvl` 0–8), `w:numStyleLink`, `w:styleLink`. Two
  definitions with one id, an id or ilvl that is not a whole number in range: that id is **unreadable**.
* **A paragraph's numbering** `(numId, ilvl)` or none:
  1. *direct*: the paragraph's own `w:pPr/w:numPr` — its `w:numId/@w:val` (d_num) and `w:ilvl/@w:val` (d_lvl), each
     when present (not a whole number → unreadable);
  2. *style*: along the paragraph style chain (`w:pStyle`'s value — an empty one names no style —, else the default
     paragraph style; nearest first, basedOn, cycle-safe) the first style whose own `w:pPr/w:numPr` holds a `w:numId`:
     s_num, that style's own `w:ilvl` (s_lvl, may be none) and its id (s_style);
  3. numId = d_num when present, else s_num; none, or 0 → unnumbered;
  4. ilvl = d_lvl when present; else, when numId came from the style: s_lvl when present, else the **linked level** —
     the lowest ilvl whose level definition (0.1, *level*) names s_style in its `w:pStyle` —, else 0; when numId came
     from the paragraph and it has no ilvl: 0.
  A numId no `w:num` defines is **dangling**: unnumbered for counting, reported by the audit.
* **Level definition** of `(numId, ilvl)`: the abstractNum's level ilvl (abstractNum = the num's). A num with a FULL
  override level for ilvl (`w:lvlOverride/w:lvl`) makes that level **unsupported**: Word does not read an override
  level as ECMA-376 describes it (MS-OI29500: it ignores `w:lvlRestart` there, and by Codex's reading of §17.9 its
  `w:start` too), so the engine does not predict it (Codex on #32). An abstractNum with `w:numStyleLink` (a list defined
  through a numbering style) is **unsupported**; so is a level holding `mc:AlternateContent` (Word 2010's custom formats
  such as `001`). A missing level → the paragraph shows no number (it still counts — 0.2). Its fields: `start` (`w:start/@w:val`,
  default 0), `fmt` (`w:numFmt/@w:val`, default `decimal`), `text` (`w:lvlText/@w:val`, none when absent), `restart`
  (`w:lvlRestart/@w:val`, whole, or none), `legal` (`w:isLgl` on: present and `w:val` not 0/false/off), `suff`
  (`w:suff/@w:val`, default `tab`), `pstyle` (`w:pStyle/@w:val`).
* **Formats** (a counter value n → text): `decimal` str(n); `decimalZero` `0`+str(n) when 0 ≤ n ≤ 9, else str(n);
  `upperLetter`/`lowerLetter` (n ≥ 1) the letter of (n−1) mod 26 repeated (n−1) div 26 + 1 times (Word: 27 → AA, 28 →
  BB); `upperRoman`/`lowerRoman` (1–3999) the standard numerals; `bullet` → the level's text as written; `none` → "".
  Any other format, or a value out of its range → **unsupported** (that paragraph's number is not evaluated).

### 0.2 Counting and the rendered number

Over the main part's paragraphs in `root.iter(w:p)` order, leaving out paragraphs inside a text box (`w:txbxContent`)
and paragraphs whose mark is a pending deletion (`w:pPr/w:rPr/w:del` or `w:moveFrom`: the accepted view); hidden
paragraphs count (Word counts them). A **list instance** is a numId (decision: per numId — see "Word and LibreOffice"
below). For each numbered paragraph at level L of instance N:

1. every level k > L of N whose definition restarts after L gets no current value (it starts again): `restart` none →
   restarts (k > L); `restart` 0 → never; `restart` r ≥ 1 → restarts when L ≤ r − 1, and is ignored (as none) when
   r − 1 ≥ k;
2. level L's value = its start (N's `startOverride` for L when N has one, else the level's `start`) when it has no
   current value, else the current value + 1;
3. the **rendered number** = the level's `text` with each `%j` (j = 1…9) replaced by level j−1's current value of N
   (a level with none yet shows its start) in level j−1's format — in `decimal` when the level is `legal`; the
   **counters** = the current values of levels 0…L. No `text` → "" (the paragraph still counts). Unsupported anywhere in
   the text → the paragraph is "not evaluated".

The model returns, per counted numbered paragraph: its index (`root.iter(w:p)` ordinal), numId, ilvl, counters,
rendered text, `suff`, and "not evaluated" with a reason.

**Word and LibreOffice.** Word continues one count across nums that share an abstractNum (unless a num restarts with a
`startOverride`); LibreOffice keeps each num separate. The model counts per numId, and a paragraph whose num shares its
abstractNum with another num that numbers a counted paragraph is **not evaluated** ("Word and LibreOffice number these
differently") — the audit flags those lists. An op never predicts through one (Codex on #32): the **destination list**
of a conversion (§1, §2: the scheme's num) must not be such a list, and must have no `w:lvlOverride` at all, else the
file refuses (`numbering-unsupported`); an imported scheme gets fresh ids and is never shared. `set_list_level` with
`start`/`restart` on such a list refuses the same way (a format change does not change counts, so it plans).

**Plans see what V10 checks.** Every post-condition below that compares numbers over the whole document is first
computed by the plan over the planned output, analytically, and the plan refuses (or, where the design says so, flags)
before anything is written — V10 never discovers a number change the plan did not predict (Codex on #32: converting a
typed item ahead of paragraphs the destination list already numbers shifts them).

### 0.3 The scheme ("your PART / 1.1 / A. / 1. / a.")

`levels`: 1–9 paragraph-style references (id or name, batch 4's `resolve_style`), level 0 first. Resolved in the
`template` (the example file) when one is given — the style import of batch 4 (§5 there) brings each style the
document lacks with its basedOn/link/next closure and its list (new ids); `existing: keep` (default) uses a style the
document already has as the document defines it — else in the document. The levels form a **scheme** when every one
is a paragraph style whose own numbering (the style chain alone: rule 0.1 step 2 and 4) is the SAME num at ilvl = its
position, the levels supported (0.1) and their `text` present. Else refused: from the template `bad-params` (the
example file is the same for every document: fatal), when it depends on the document (a held style) `scheme`.

The **level pattern** of scheme level k: its `text` with literal characters escaped and each `%j` the pattern of level
j−1's format (`decimal`/legal `[0-9]+`, `decimalZero` `[0-9]{2,}`, letters `[A-Z]+` / `[a-z]+`, roman `[IVXLCDM]+` /
`[ivxlcdm]+`, `none` empty); a `bullet` level has no pattern (refused as a scheme level: `bad-params`/`scheme`).

Profiles show the numbered styles of a file (`profile.lists`: per list its levels — style, format, a rendered sample
such as `PART 1 -`, `1.1`, `A.` — and how many paragraphs each numbers) so Claude can name the levels from the example
file read with `read_template`.

## 1. `convert_numbering` (item 33 ★)

```json
{"op": "convert_numbering",
 "params": {"template": "NUMBERING SCHEME.docx", "levels": ["PRT", "ART", "PR1", "PR2", "PR3"]}}
```

* `from`: which paragraphs are the document's numbering — `"main"` (default): every counted paragraph numbered by any
  num of the **main abstractNum** (the one numbering the most counted paragraphs at a non-`bullet` level; a tie → the
  one of the first such paragraph), its level = its ilvl; or `{"styles": [[ids/names of level 0], [level 1], …]}` —
  every counted paragraph whose paragraph style (pStyle, else default) is in level k's list (resolved in the file;
  missing ones skipped, noted). `level_map` (optional `{"<source level>": <scheme level>}`) re-maps levels; default
  identity. A source level beyond the scheme → `scheme-depth` (file-specific).
* `template`, `levels`, `existing` (§0.3); `direct_formatting`: `keep` (default) | `clear` (apply_style's: the
  paragraphs' direct paragraph formatting and runs' run formatting by clear_direct_formatting's default rules);
  `tracked` (§14.1: plain — not targeted — unless asked), `author`.
* A converted paragraph's change: `w:pStyle` = its scheme level's style (first in the pPr), its own `w:numPr` removed
  (the style's numbering applies), cleared children gone when `clear`; tracked → one `w:pPrChange` (snapshot before
  all of it). Already as asked (style already the level's, no own numPr, nothing to clear): untouched, uncounted.
* Refused: a converted paragraph whose list is unsupported or not evaluated (`numbering-unsupported`); a paragraph whose
  own numPr has only an ilvl (`numbering-unsupported` — it relies on the style for its list).
* **Predicted numbers**: the model run over the planned output (the source with each converted paragraph numbered by
  the scheme's num at its level; the scheme's definitions — the template's for an imported list). Flagged: every
  converted paragraph whose counters differ from its source counters ("PART 2 article 3 was shown as 2.04 and becomes
  2.3" — a shift from a source glitch, the playbook's "a correction, not a defect"), every paragraph whose text starts
  with a typed number (double numbering), REF/PAGEREF/NOTEREF fields whose bookmark lies in a converted paragraph (their
  cached number goes stale), and each occurrence in another paragraph's text of a converted paragraph's OLD rendered
  number that changes ("as specified in 1.05" — written cross-references are content: flagged, never edited). Every
  counted paragraph's number is predicted: other lists keep theirs, and a paragraph the destination list already
  numbered (a held scheme) that the conversion shifts is flagged by name.
* **V10 `converted_numbering`**: re-derives the scheme, the source paragraphs, their levels and each change from the
  SOURCE with `verify/lists.py`; each changed paragraph's pPr = the source's with pStyle set, numPr gone (and the cleared
  children, `clearing.check_runs_and_mark`/`check_cleared_ppr`); the import by `styles_imported` (`check_requested`);
  **post-condition on the OUTPUT**: the verifier predicts every counted paragraph's counters and rendered number from
  the SOURCE with its own model (the converted paragraphs given the destination list), its prediction must equal the
  plan's, and its own count of the output must equal both. Strips
  `pStyle`, `numPr`, `pPrChange` (and the cleared names) of the changed paragraphs.
* Touches (rule 9): per changed paragraph `(main, bi, "pPr")`, `"pStyle"`, `"numPr"` (+ cleared names, `rPr`,
  `markRPr`), `uses+`/`uses-` as apply_style; the reads of §6.
* Layout reference `none`.

## 2. `convert_typed_numbers` (item 34 ★)

```json
{"op": "convert_typed_numbers",
 "params": {"template": "NUMBERING SCHEME.docx", "levels": ["PRT", "ART", "PR1", "PR2", "PR3"], "tracked": false}}
```

* `select`: `paragraphs` (the paragraph grammar; default all), `include_tables` (default false: a typed "1." in a
  table cell is left alone). Main document only.
* `template`, `levels`, `existing`, `direct_formatting`, `tracked` (default **true** — characters are removed; batch 1
  rule 2), `author`.

### 2.1 Typed numbers (both sides: `ops/lists.typed_number` ↔ `verify/lists.v_typed_number`)

A **candidate** is a counted paragraph of the main part that is unnumbered (§0.1), selected, not in a table unless
`include_tables`. Its **typed number**: on its run text (batch 1's definition), `^[ \t]*` then a match of exactly one
scheme level's pattern followed by `[ \t ]+` or the end of the run text; the **prefix** = from position 0 to the end
of that whitespace. A paragraph matching no level is not converted; one matching two levels' patterns refuses the file
(`ambiguous`: the typed text cannot say its level — e.g. a scheme with both a letter and a roman level and a typed
"I."). A numbered paragraph whose text starts with a match is flagged (double number), never touched.

### 2.2 The change

Each converted paragraph: the prefix removed by the in-paragraph edit core (one edit `(0, end, "")`), `w:pStyle` = the
level's style, own `w:numPr` (a `numId` 0 one) removed, the cleared children gone with `clear`. Tracked: the prefix in
`w:del`, one `w:pPrChange`; plain: gone.

### 2.3 The edit core deletes a tab (`ops/textedit.py` ↔ `verify/edits.py`)

Typed lists separate the number with a tab (`w:tab`). The core gains, behind an explicit keyword (`tabs=True` on
`check_edits`/`plan_paragraph`/`apply_part_edits` and the verifier's `v_check_edits`/`expected_items`/
`check_edited_paragraph`), one relaxation: a pure deletion (`text == ""`) may cover a run's `w:tab` children (never a
`w:ptab`, break or any other element). Untracked: the `w:tab` is removed with the characters (V3: the block's `tab`
count drops — the plan declares `struct_after`); tracked: it goes into the `w:del` with the deleted characters. The
item model's expected items follow the same rule. Every other op keeps the strict core (the keyword defaults off).
Planted-defect tests: a tab left behind, a tab outside the range taken, a ptab accepted.

### 2.4 The guarantee: the document reads the same

The plan predicts every counted paragraph's number over the planned output (§0.2) and **refuses the file**
(`numbering-mismatch`, naming the first paragraphs) unless the visible line (below) of EVERY counted paragraph of the
main part is unchanged — a converted one shows its typed number exactly, and a paragraph the destination list already
numbered keeps its number (Codex on #32) — and no converted level's `suff` is `nothing` (the separator was whitespace). V10 `typed_numbers` re-derives candidates,
typed numbers, prefixes and levels from the SOURCE with its own code, checks every converted paragraph with the item
model (`check_edited_paragraph`, tabs on) and its pPr as §1, and — **post-condition on the OUTPUT, over every counted
paragraph of the main part** — its **visible line** (rendered number, then the accepted run text; every run of
spaces, tabs and NBSPs collapsed to one space, leading/trailing whitespace stripped) equals the source's. A typo in the
typed numbers (two "B."s, a skipped letter) therefore refuses the file instead of being renumbered silently; the audit
(§4) shows where.

Touches: `(main, bi, "text")`, `"pPr"`, `"pStyle"`, `"numPr"` per converted paragraph; the reads of §6 (and
`verify:text` of every candidate: another op editing a typed number in the same spec is a conflict). Layout `none`.

## 3. `set_list_level` (item 35)

```json
{"op": "set_list_level", "params": {"style": "ART", "zero_pad": true, "restart": "after_higher"}}
```

* The level: `style` (a paragraph style of the document, id or name) — the level its own numbering names (§0.1 steps 2
  and 4); `level` (optional) another level of the same list. The style must be numbered (`not-found`, file-specific
  — allow_missing default true makes a file without it a no-op with a note).
* Changes (at least one): `format` (`decimal`, `decimalZero`, `upperLetter`, `lowerLetter`, `upperRoman`,
  `lowerRoman`); `zero_pad` (true → `decimalZero` where the format is `decimal`; false → `decimal` where it is
  `decimalZero`; refused beside `format`); `start` (whole ≥ 0; letters/roman ≥ 1); `restart`: `"after_higher"` (no
  `w:lvlRestart` — the level restarts after any higher level: "restart numbering per PART" for articles numbered with
  their PART), `"never"` (`w:lvlRestart w:val="0"`), `{"after_level": k}` (k < level: `w:lvlRestart w:val="k+1"`).
* Written in the abstractNum's `w:lvl` (CT_Lvl order: start, numFmt, lvlRestart, pStyle, isLgl, suff, lvlText,
  lvlPicBulletId, legacy, lvlJc, pPr, rPr); `w:numFmt` keeps no `w:format` attribute. Refused: the list is unsupported;
  a num of that abstractNum has a full override level for it (`numbering-override`); flagged: a num's `startOverride`
  for the level beside `start` (the override still wins in that list), every other num sharing the abstractNum (all
  their paragraphs change).
* Already as asked → no-op with a note. Never tracked (`w:numberingChange` is not written: `bad-params`).
* **V10 `list_level`**: re-derives the level from the SOURCE (own model), compares the WHOLE numbering member with its
  own expected tree (`verified_members`), and, post-condition, recounts the output: every counted paragraph's counters
  equal the source's (a format change never changes a count) unless `start`/`restart` was asked, in which case they
  equal the plan's predicted ones; every rendered number equals the plan's prediction.
* Touches: `(numbering, "*", "lvl")` (a whole-member change: any other op touching the numbering part is a conflict),
  `(styles, "style:<id>", "verify")` of the style. Layout `none` (numbers change width).

"Restart numbering per PART" when PART paragraphs are not in the articles' list (typed, or another list) cannot be done
by a level's definition: the plan says so ("no higher level of this list numbers a paragraph: the restart changes
nothing") and points at converting the numbering first (items 33/34). Restarting by inserting `w:num` instances with
`startOverride` is not in this batch (Word and LibreOffice would number the result differently — §0.2).

## 4. `audit_numbering` (item 36, an audit)

```json
{"op": "audit_numbering", "params": {}}
```

Report only by default: the plan changes nothing and carries its findings as flags (the preview and the report's
"Flagged for review"), notes a summary (lists, numbered paragraphs, typed numbers checked). Findings:

* **empty numbered paragraph** — a counted paragraph showing a number (non-empty rendered number, not `bullet`) whose
  content is blank (remove_empty_paragraphs' content rule: only `w:pPr`, proofing marks, bookmarks, runs of `w:rPr` /
  `w:lastRenderedPageBreak` / `w:t` of spaces and NBSPs): "‘C.’ is shown on an empty line and uses up a number";
* **gap / duplicate / goes back** in the **visible sequence**: every counted paragraph with a visible number — its
  rendered number, or its typed number (the typed grammar `PART n`, `ARTICLE n`, `n.n…`, `n.`, `n)`, `(n)`, `A.`,
  `a.`, `(a)`, `a)`, roman `II.` … `ii.` of two or more letters; a single `I.` reads as a letter) — gets a **shape**
  (the number with each component replaced by its class: `PART <1>`, `<1>.<1>`, `<A>.`) and a value (its last
  component). Walking the document with a stack of open shapes: a shape already open closes every deeper one and its
  value must be the previous + 1 (greater → gap, equal → duplicate, smaller → goes back unless it is the first value
  after a shallower shape intervened); a new shape opens with value 1 (else "starts at …");
* **typed number on a numbered paragraph** (double numbering); **hidden numbered paragraph** (uses a number without
  showing); **dangling numId**; **not evaluated** (unsupported formats, numStyleLink lists); **Word/LibreOffice differ**
  (§0.2).

`unnumber_empty: true` removes the number from each empty numbered paragraph: its own `w:numPr` becomes
`<w:numPr><w:numId w:val="0"/></w:numPr>` (numbering off, the paragraph and its spacing stay); plain unless `tracked`.
The later numbers of its list move up by one — predicted and flagged. **V10 `unnumbered_empty`** re-derives the empty
numbered paragraphs with its own model and content rule, checks each one's pPr (numPr exactly that; everything else
identical; the pPrChange rules), recounts the output (every other paragraph's rendered number equals the plan's
prediction). Report-only plans need no kind: nothing changes and the residual proves it.

Touches: the reads of §6; with the fix `(main, bi, "pPr")`, `"numPr"` per changed paragraph. Layout `source`
(report only) / `none` (fix).

## 5. Tables (items 37–38)

### 5.1 The table selector (both sides: `ops/table_select.py` ↔ `verify/tableselect.py`)

`select.scope` (default `["document"]`; headers, footers, footnotes, endnotes, comments allowed) and `select.tables`:
`"all"` (default) or `{index_range: [a, b], style_in: [ids], first_row_regex, text_regex, nested}` — a table's
**ordinal** = its index among `root.iter(w:tbl)` of its part (nested tables included, document order; `index_range`
inclusive, 0-based); its **style** = `w:tblPr/w:tblStyle/@w:val`, else the default table style; `first_row_regex`
searches the first `w:tr`'s text (each cell's paragraphs' block texts joined by a space, cells joined by a tab);
`text_regex` any paragraph of the table (nearest table); `nested: false` leaves out tables inside a cell. All given keys
must hold. A table touched by a table op is named `(part, "tbl:<ordinal>")` (batch 4's convention); its rows
`tr:<ordinal>:<r>`, cells `tc:<ordinal>:<r>:<c>` (row index among the table's own `w:tr`, cell index among the row's
own `w:tc`).

### 5.2 `set_table_props` (item 37)

```json
{"op": "set_table_props", "select": {"tables": "all"},
 "params": {"borders": {"top": {"style": "single", "size": 4}, "bottom": {"style": "single", "size": 4},
                        "insideH": {"style": "single", "size": 4}},
            "cell_margins": {"left": 115, "right": 115}, "width": {"pct": 100},
            "repeat_header_rows": 1, "cant_split": true}}
```

* `borders`: `"none"` or sides `top | left | bottom | right | insideH | insideV` (batch 4's border object: `style`,
  `size` eighths of a point, `space` points, `color`) — **the table's whole border set** (as paragraph borders): an
  asked side exactly; a side not asked that the table style chain (tblStyle else default table style, basedOn root-first,
  `w:tblPr/w:tblBorders`) shows visibly → `{w:val: nil}`; else absent; nothing → no `w:tblBorders`. A table whose own
  `w:tblBorders` spells a side `start`/`end` refuses (`conflict-property`: say left/right). Conditional formatting of
  the style (`w:tblStylePr`) is not modelled: flagged when it has borders.
* `cell_margins`: `{top, left, bottom, right}` twips → `w:tblCellMar` **merged** (the asked sides set as
  `{w:w, w:type: dxa}`, the others kept); the alias rule as borders.
* `width`: `{"pct": 1–100}` (`w:tblW w:type="pct" w:w=pct×50`), `{"twips": n}` (`dxa`) or `"auto"` (`auto`, `w:w` 0).
* `repeat_header_rows`: n ≥ 0 — the first n rows carry `w:trPr/w:tblHeader`, every later row none (n > rows → the
  table refuses, `table-structure`); `cant_split`: true/false — every row's `w:trPr/w:cantSplit` present / absent.
* `clear_cell_overrides` (default false): with `borders`, every cell's `w:tcPr/w:tcBorders` goes; with
  `cell_margins`, every cell's `w:tcMar`. Without it, cells that override are flagged ("12 cells keep their own
  borders").
* Already as asked (own element equal to the expected one; both absent counts) → untouched, uncounted. An element is
  written in schema order (CT_TblPr, CT_TrPr, CT_TcPr sequences); an emptied `w:trPr`/`w:tcPr` goes. Never tracked
  (`tracked: true` → `bad-params`; `w:tblPrChange`/`w:trPrChange` are not written); an existing change marker on a
  changed table or row is flagged (reject-all would restore the older snapshot).
* **V10 `table_props`**: re-derives the selection, the levels, the expected elements and "already" from the SOURCE with
  its own code; fails when the changed tables/rows/cells or their counts differ from the plan's or an output element is
  not exactly the expected one; post-condition: every selected table's output shows the asked borders (nearest-level
  reading), margins, width and rows. New `Ctx` hooks (`strip_tbl`: part → {ordinal: tblPr child names}, `strip_tr`:
  part → {(ordinal, r): trPr child names}, `strip_tc`: part → {(ordinal, r, c): tcPr child names}) remove the checked
  elements from both sides before the residual; an emptied trPr/tcPr is dropped on both sides.
* Touches: `(part, "tbl:<k>", "tblBorders" | "tblCellMar" | "tblW")`, `(part, "tr:<k>:<r>", "tblHeader" | "cantSplit")`,
  `(part, "tc:<k>:<r>:<c>", "tcBorders" | "tcMar")` per changed element; reads `(part, "tbl:<k>", "verify-props:tblPr")`
  (its style: map_styles/apply_table_style beside it conflict) and `(styles, "style:<id>", "verify")` of its style chain,
  soft reads of its own asked elements on every selected table, `(part, "scope", "verify")`. Layout `none`.

### 5.3 `apply_table_style` (item 38)

```json
{"op": "apply_table_style", "params": {"style": "TblMine", "template": "OFFICE STYLES.docx"}}
```

* `select` as §5.1; `style`, `template`, `existing` — apply_style's resolution (a table style: `style-mismatch` in the
  document, `bad-params` in the template; missing → `missing-style`), imported by batch 4's style import;
  `direct_formatting`: `keep` (default) | `clear` — the table's own `w:tblBorders`, `w:shd`, `w:tblCellMar` and every
  cell's `w:tcBorders`, `w:shd`, `w:tcMar` go so the style shows. `w:tblLook` (which conditional parts show) is left
  as it is. Never tracked (`bad-params`).
* A table whose style already is the asked one and (with clear) nothing to clear → untouched, uncounted. `w:tblStyle`
  first in `w:tblPr`.
* **V10 `applied_table_style`**: selection, resolution, each table's tblPr = the source's with tblStyle set (and the
  cleared children gone), cells exactly; the import by `styles_imported` (`check_requested`). Strips via
  `strip_tbl_style`, `strip_tbl`, `strip_tc`.
* Touches `(part, "tbl:<k>", "tblPr")` (map_styles' aspect for a table style — paragraph ops in the table read it),
  `uses+`/`uses-`, per cleared element its name; the import's touches. Layout `none`.

## 6. Plan-time conflicts (rule 9) for the list model

An op whose prediction counts (33–36) declares what the count reads:

* `(numbering, "*", "verify")` — the numbering part whole (the import of another op, another list op);
* `(main, "*", "verify-props:pStyle")` and `(main, "*", "verify-props:numPr")` — any paragraph restyled or given numbering
  by another op changes the count (whole-member soft family reads);
* `(main, bi, "verify:numPr")` for every counted numbered paragraph — a tracked deletion of one (a `text` change) is a
  conflict (untracked insertions and deletions are whole-member `text` changes: rule 1 already);
* `(styles, "style:<id>", "verify")` of every style on the chains of numbered paragraphs and of the scheme.

Ops changing a paragraph's numbering touch `(part, bi, "numPr")` (and `pPr`); ops changing `numbering.xml` touch it
whole. Batch 4's import already touches `(numbering, "*", "add")`.

## 7. Sections (items 39–40): `set_page_setup` grows

* `different_first_page`: true → the selected sections' `w:titlePg` present with no `w:val`; false → absent. Touch
  `(main, "sectPr:N", "titlePg")` (fix_page_numbering and the templating op read it: a conflict). Notes per section:
  what its first page shows after the change (resolved through inheritance, `hf_common.effective_refs`); flagged: a
  first page that will show NO header/footer (titlePg on, no first-page part anywhere before it) or an EARLIER section's
  first-page part. V10 `section_props` gains element specs `{"present": true, "remove": [attrs]}` and
  `{"absent": true}`.
* Header/footer distance from the page edge: `margins.header` / `margins.footer` (twips), as before — the catalog says
  so and an example shows it.
* `select.sections` keeps `"all"` and `[indices]` and gains `{containing: <paragraph predicate>, with_tables: bool,
  index_range: [a, b]}` — a section (its index among `sect_prs`) is selected when every given key holds: `containing`
  — a paragraph of the section (`paragraph_sections` ↔ `tabs._v_sections`) matches the paragraph selector grammar;
  `with_tables` — the section holds a table (its first paragraph's section); `index_range` inclusive. Both sides:
  `ops/selectors.resolve_sections` ↔ `verify/props._v_resolve_sections`; the assertion carries the user's `select`
  (raw) and V10 re-derives the list. No section matched → `not-found` (allow_missing default false).
  Reads: `(main, "*", "verify:text")` when `containing` reads text, `(main, "*", "verify-props:pStyle")` when it reads
  styles.
* Landscape: flagged when a header/footer part a changed section shows is also shown by a section of the other
  orientation ("its right-aligned items stay where the portrait page puts them"). Margins are still not rotated (give
  them in the same op).

## 8. Around them

* `report.FAILURE_EXPLANATIONS`: `scheme`, `scheme-depth`, `numbering-mismatch`, `numbering-unsupported`,
  `numbering-override`, `table-structure`.
* `catalog`: `COMMON_ASSUMPTIONS` per new op (and set_page_setup's new keys); worked examples in `examples/specs/`;
  `examples/templates/NUMBERING SCHEME.docx` (the example file: PRT/ART/PR1/PR2/PR3 numbered `PART %1 -`, `%1.%2`,
  `%3.`, `%4.`, `%5.` — upper letter, decimal, lower letter — by one list).
* `compiler.SYSTEM`: when to use each op; the scheme's levels come from `read_template`'s numbered styles; typed
  numbers are converted only where the list would show the same numbers; the audit before a conversion; numbering ops
  are specs of their own beside restyling; mapping styles and converting numbering are two ways to the same end (prefer
  convert_numbering when the user speaks of numbering, map_styles when of styles).
* `profile`: `lists` (§0.3) and the typed-number count; `tables` (count, styles, header rows, rows that may split).

## Decisions for Abraham (each with the recommendation taken)

1. The scheme comes from **your styles** (an example file or the document): the engine never writes list definitions
   of its own, so "PART %1 -" is always your text.
2. Typed numbers become automatic only when the list would show **exactly** the typed numbers; a typo refuses the file
   (the audit says where) rather than being renumbered silently.
3. Converting numbering keeps the paragraphs' own indents and fonts unless `direct_formatting: clear`; shifts from
   source glitches are corrected and flagged, written cross-references flagged, never edited.
4. "Restart per PART" is the article level's restart rule; restarting a list that does not hold the PARTs waits for a
   conversion first.
5. The audit reports; it changes something only when asked (`unnumber_empty`).
6. Table borders replace the table's whole border set (as paragraph borders); cell overrides are flagged, cleared only
   on request. Table changes are never tracked.
7. A table style keeps `w:tblLook`; `clear` removes the table's and its cells' own borders, shading and margins.
8. Different-first-page on a section without a first-page header/footer shows a blank one (flagged); margins are not
   rotated for landscape.
9. Not in this batch: inserting section breaks around a schedule, restarting lists with new `w:num` instances, tracked
   table or numbering changes, numbering in headers/footers/text boxes, numStyleLink lists, custom (`001`) formats.

## Built (2026-10-17): what the build and the review changed

Built verifier-first by a list-model builder (the groundwork), then four builders in parallel — the numbering conversions
(33–34), list levels and the audit (35–36), tables (37–38), sections (39–40) — each put through an adversarial review.
Every finding was reproduced before it was fixed, and each fix is pinned by a test that fails without it. Each module's
docstring is the full definition; the departures from the sections above:

**The list model (§0)** — `ops/lists.py` ↔ `verify/lists.py`, compared on every paragraph of ten fixture variants and
~10,000 random paragraphs, with planted shared defects in each rule. Departures: a value that is present but not a whole
number makes its definition unreadable; an unreadable paragraph makes the rest of its list `earlier`. **Not evaluated**
grew from the design's list to every case where Word's behaviour is uncertain or Word and LibreOffice differ: full
override levels (Codex on #32), shared abstractNums (Codex), a `%j` naming a level with no value (`skipped` — LibreOffice
shows the level's start and continues from it, and a level a deeper paragraph reached while it had no value stays
uncertain for its next paragraph), a startOverride level starting a second time (Word may apply the override only once),
lists defined through a numbering style (`numStyleLink` and the `styleLink` definition behind it), a paragraph whose
level the two readings of numPr inheritance (as a unit / element by element) give differently (`ambiguous-level`), and
negative values. `list_is_clean` refuses any `w:lvlOverride` on a destination. `has_restart_rule` names the levels whose
restart rule LibreOffice ignores. `count_reads` reads the chain of EVERY counted paragraph (an import replacing the
definition of an unnumbered note's style with one based on a numbered style changed the count unseen).

**The plans check what V10 checks** (Codex on #32): the conversions predict every counted paragraph's number over the
planned output and refuse (typed) or flag (scheme) before anything is written.

**Tables (§5)**: border `space` defaults to 0 (Word's value for tables); whole-element aspects `tblProps`/`trPr`/`tcPr`
beside the element aspects; rows get an explicit `w:val="0"` where the style chain turns a row property on; the style
chain is read as `verify:tblPr` + `verify:replace` (+ `verify:add` of a missing style) instead of a bare read. Review:
`apply_table_style`'s clear also removes the rows' own `w:tblPrEx` borders/shading/margins (hook `strip_tblprex`),
soft-reads everything it would clear on every selected table and checks on the output that nothing is left (a
`set_table_props` beside it planned and passed with the style not showing); the style is imported only when a selected
table is restyled (with `replace` and a selection matching nothing, unselected tables changed look); `clear_cell_overrides`
removes only the competing sides (never diagonals, never unasked margin sides); flags count rows and cells inside content
controls; right-to-left tables refuse borders and margins like `start`/`end`; header rows of floating or nested tables
and a style's conditional row settings are flagged; a table in a text box (under "all") or shorter than the header rows
asked is left alone and flagged instead of refusing the file.

**Sections (§7)**: one `section_props` assertion per op carrying the user's raw `select`/`params` and `per_section`
specs; the landscape flag reads the header/footer types a section displays. Review: `package.sections()` reads
`<w:titlePg w:val="0"/>` as off (fix_page_numbering's cover-unnumbered edited a footer the cover never showed and
passed — V10 gains `cover_unnumbered`, which reads what the cover shows in the output); `remove_header_footer` declares
its reads of every titlePg; the verifier's selector reads an empty pStyle as no style (as the transform does); `style_in`
must be a list; `with_tables` declares no text read; a set_page_setup assertion without its `select` fails V10; a
content selection choosing every section is flagged; the landscape example matches a SCHEDULE heading, not "Schedule 40
pipe".

**The conversions (§1, §2)**: both take `allow_missing` (default true); `from.styles` converts only numbered paragraphs
(removing an unnumbered one's numPr would make a number appear); one style named for two levels refuses `ambiguous`;
tracked typed conversion with `direct_formatting: "clear"` refuses (one run would carry a tracked deletion and a tracked
formatting change the item model cannot check); `convert_typed_numbers` reads the whole main part's text (a stronger read
than §2.4's per-candidate one); a new code `typed-format`. Review: the list number takes the paragraph mark's formatting —
a number whose planned look differs from the typed run's (both read in the planned output, over the new style, so a
scheme style sizing text and number alike still converts) in strike, vertical alignment, position, colour, size or a
symbol font refuses, a different bold/italic/underline/font is flagged (a white, 1 pt, superscript, struck or Wingdings
mark used to pass); caps/smallCaps refuse only a typed number holding a lower-case letter (All Caps PART titles refused
whole files); specVanish counts as hidden; an own numPr holding only an ilvl converts (it refused the builder's own
fixture); a held style numbering differently from the template's level with `existing: "keep"` refuses `scheme` (an
architect using MasterSpec's PRT/ART… was silently not converted); a non-converted paragraph whose number would change
refuses (a partial `from.styles` conversion renumbered the paragraphs left behind); a source paragraph not evaluated
converts when its level is known and its whole list moves (one Word "Restart numbering" refused the file), flagged, and
a paragraph skipped before and after at an unchanged level converts with its own flag (`skipped_both`); tracked
conversion over a style the import replaces refuses (reject-all showed "PART 1 - PART 1 - GENERAL"); linked scheme levels
and a new style turning caps on are flagged; mismatch reasons name the nearest typed-looking line that matched no level
(an en dash in "PART 1 – GENERAL").

**List levels and the audit (§3, §4)**: a new file-specific code `list-level` (a start of 0 on a letter or roman
level, an `after_level` not above the level, a change leaving an evaluated paragraph unevaluated); the audit's blank rule
allows bookmarks (its own both-sides rule); the owner rule (`NUMBERING_OWNER_KINDS`) lets the import's no-copy check stand
down beside `list_level`, which compares numbering.xml whole. Review: a style inheriting only its level from the style it
is based on is `ambiguous-level` where the two readings of numPr inheritance differ (it was read at level 0 and
`set_list_level` changed the wrong level — every check passing); a change that reaches no paragraph of the style (they are
numbered by another list) is flagged; `unnumbered_empty` checks only the numPr it writes and leaves the mark and a section
break to the residual (beside a font change it planned and failed V10); renumbering flags stale REF fields and written
references (`ops/numbering_flags.py`, shared with convert_numbering); `{after_level: level−1}` is "after any higher
level"; the audit judges typed numbers only (the main list's automatic numbers are context), skips table cells, reads
roman numerals, reads "PART 1 -" and "PART 2" as one sequence, checks a typed number's prefix against its PART, puts the
list-level findings before the 30-flag cap and anchors an empty paragraph on the previous body paragraph; linked levels
are flagged (LibreOffice shows them at level 0).

**Merging**: `check_conflicts` takes each distinct touch once (a conversion over 2,000 paragraphs took 16 s to merge).

**Decisions left for Abraham** (recorded, not built): a typed spec without `word/numbering.xml` is refused (rule 10: the
engine never creates a part — Word creates the part once any list is used); a scheme with both a roman and a letter
level refuses roman typed PARTs as ambiguous (a typed "II." also reads as the letter level). And, from the merge with master
below: compact the op catalog or raise the default context allowance.

**Tests and the merge with master**: 2429 tests pass on the batch alone, 4 skip (the Windows-only Credential Manager round
trip and three tests that need the `anthropic` package). Master's adaptive workspaces and correlated diagnostics (#29,
#33) landed beside it; merged, 2838 pass and the same 4 skip. The merge found one interaction: every conversation's system
prompt carries the whole op catalog, and this batch's six operations, their schema and nine worked examples took the
first request from about 86,500 to about 105,000 estimated tokens of master's 120,000-token working context
(`resources.DEFAULT_CONTEXT_TOKENS`, 3 bytes a token, 4,784 an image), so a conversation viewing a third page starts a new
context epoch. `tests/test_vision.py`'s image-allowance test failed on that rollover; it now sets its own allowance, as
master's rollover test already did, and `tests/test_contexts.py` guards the headroom left (the first request plus two page
images must fit). Whether to compact the catalog (the schema repeats every op's parameters; the worked examples are
sent whole) or raise the allowance is left for Abraham.
