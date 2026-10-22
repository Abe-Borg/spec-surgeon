# Spec Surgeon — Implementation plan: what a custom method may format (Option A)

2026-10-22. The owner reopened, for formatting only, the full-control round's decision not to widen the custom
transform's limits (`docs/PLAN-2026-10-21-full-control.md`, "Do not"): a custom method may do more kinds of
formatting, never more kinds of change. A family is added only where an independent V10 kind proves, without knowing
what the method's code meant, that the content of what it touches is the source's.

## Why the line is where it is

A custom method writes `plan()` and `apply()` from one reading of the request; nothing independent knows its intent.
What protects a client's file is what the checker proves whatever the code does: V2/V3 every word and structure the
source's, no new member, no revision (`codegen.hard_limits`), only parts V10's residual covers, and every changed
property family checked "exactly this change" by a V10 kind that resolves its targets with its own code. A method
chooses its targets by its own logic and names them explicitly (an `index_range` per paragraph, table or row); the
kind checks those targets got exactly the asked properties and nothing else changed. A wrong choice of targets shows
in the preview, like a wrong font today; it can never change a word.

## Findings

| # | What the code shows | Kind |
| --- | --- | --- |
| 1 | `CUSTOM_TRANSFORM_LIMITS` (2026-10-17) lists highlight under "cannot". V10's `properties` kind has verified highlight, superscript and subscript, hidden text, character spacing and double strikethrough since 2026-10-21 (`set_properties`); the limits never caught up, so the conversation turns such requests away as beyond a custom method | drift (A1) |
| 2 | Table appearance has V10 kinds (`table_props`: borders, cell margins, width, header rows, row splitting; `table_cells`: cell shading, width, vertical alignment, no-wrap; `applied_table_style`), each with its own selection (tables and rows by index range among others) and a residual that strips only the checked elements, so cell text, rows, cells, the grid and merges stay compared whole. None is a custom-method family | missing family (A2) |
| 3 | List label fonts have `numbering_fonts`, but it derives its levels from a `set_font`/`set_style_font` selection, so a method cannot name the levels it chose; label size, bold or colour have no kind; `hard_limits` refuses any change of the numbering part | missing kind and part (A3) |
| 4 | The trust surfaces say a custom method changes "formatting only: no words, no new parts, no tracked changes" (T25, `trustRuntime.ts`, the dossier); still true after A1–A3, but the parts it may change gain the numbering part (A3) | trust surfaces (A4) |

## Items

| Item | Goal | Expected outcome |
| --- | --- | --- |
| A1 — the limits say what the checker proves | Highlight and the other run properties `properties` verifies are custom-method formatting | The `properties` family names every element the kind checks (highlight, superscript/subscript, hidden text, character spacing, double strikethrough); `cannot` loses highlight. A test pins every family to a kind in `verify.props.KINDS` and every run or paragraph property the family's words name to the kind's own element list, so the two cannot drift apart again. A scripted method (test source, run through the real trial: `check_source`, sandbox, `hard_limits`, the suite) that highlights paragraphs it picks by its own logic passes; a planted wrong colour, an unlisted paragraph highlighted and a changed word each fail |
| A2 — table appearance | Borders, shading, cell margins, widths, alignment, header rows and row splitting of tables, rows and cells the method picks | `table_props`, `table_cells` and `applied_table_style` become families, the targets named by index range (one assertion per table, row or column range; assertions on one table never touch one element twice). A new kind only if the trial shows the three cannot describe a listed target. Scripted methods: every other row of the schedules shaded, the first column of tables whose first row reads "Equipment" set to 1.5 in; planted: a cell's text changed, a merged cell split, a grid column added, an unlisted row shaded, each failing V2/V3 or V10 |
| A3 — list label formatting | The fonts, size, bold, italic and colour of the list labels at levels the method picks | A new V10 kind `numbering_labels` (verify/, its own reading): the plan lists levels (`w:abstractNum` id and `w:ilvl`, or `w:num` id and an override's `w:ilvl`) with the label run properties written, from a closed list (`rFonts`, `sz`/`szCs`, `b`/`bCs`, `i`/`iCs`, `color`); each listed level's `w:rPr` is the source's with exactly those written (schema order from the bundled XSD), every other element of the numbering part the source's — `numFmt`, `lvlText`, `start`, `lvlRestart`, `isLgl`, `suff`, `lvlJc`, the level's `pPr`, `pStyle`, the `num`/`abstractNum` graph, overrides — so every number Word shows is the source's. A theme font overrides a font name beside it, so a font slot written loses its theme attribute (`asciiTheme`, `hAnsiTheme`, `eastAsiaTheme`, `cstheme`) as `numbering_fonts`' `clear_theme` does, and the kind checks what Word shows as well as the XML: each listed level's label resolves to the asserted font in every slot written; a method that keeps the theme attribute must refuse (from review). `hard_limits` lets the numbering part (found through the main part's relationship) change exactly when a `numbering_labels` assertion names it. Refused (the method raises, the kind says the op must refuse): a level not uniquely defined, a numbering part the main part does not relate, a label in a symbol font losing it. Scripted method: labels in Arial 10 where the list text is Arial (the 10-18 run's Calibri labels); planted: `lvlText` changed, a start changed, an unlisted level changed, a level's indent changed, a themed label given only `w:ascii`/`w:hAnsi` (its theme attribute kept, so Word still shows the theme font) |
| A4 — the words around it | Every surface that says what a custom method may do says it | `CUSTOM_TRANSFORM_LIMITS` (families and rules: "the document's text parts, its styles part and, for list labels, its numbering part"), the codegen prompt (it reads the limits), the catalog, `trustRuntime.ts` and `TrustDossier.tsx` (formatting only, now naming table appearance and list labels), `docs/TRUST_CLAIMS.md` T25 (its sources gain the new kinds), the README's custom-method paragraph and the release notes; rides with each of A1–A3 |

Order: A1 (the drift, small), A2, A3; A4 with each. One pull request per item.

## Decisions (the recommendations, taken)

1. Formatting only, as before: no words, no new member, no revision, no settings, document properties, theme or fonts
   table. The only new part is the numbering part, and only behind A3's kind.
2. Named targets, never a re-derived intent: the verifier cannot re-derive a method's logic, so it checks the named
   targets got exactly the named properties and that nothing else changed.
3. Out of reach for a custom method still: picture sizes, list levels, number formats, numbering conversions and
   restarts (each changes what Word shows as a number or how a page lays out pictures), tracked formatting.
4. Each item ships with a scripted method through the real trial and a planted defect for every rule (a wrong value,
   an unlisted target, a changed word or number, a changed structure).

## Acceptance

Each item on fixtures as above. On the owner's machine: one request per family that no built-in operation expresses
("shade every other row of the equipment schedules light grey"; "make the list numbers Arial 10 where the list text is
Arial"), offered as a custom method, passes, opens in Word without a repair prompt and looks as asked.

## Do not, in this round

- Let a custom method change a word, a number Word shows, a table's structure or any part beyond A3's.
- Add a family without a V10 kind that proves the content of what it touches is the source's.
- Raise the custom method's sandbox limits (60 s, 2,048 MB, 50 tries; T25's numbers stay).

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| A1 — the limits say what the checker proves | planned | — | — |
| A2 — table appearance | planned | — | — |
| A3 — list label formatting | planned | — | — |
| A4 — the words around it | planned | — | — |
