# Built-in operations, batch 3 — design note

2026-10-16. Abraham's request, the third chunk of prebuilt operations (items 22–25 of his list; ★ = he starred it):

| # | Asked for | Built as | Mode |
|---|---|---|---|
| 22 ★ | Bold, italic, underline, caps or small caps on chosen paragraphs (article titles bold) | `set_properties` (exists since the built-in round's part 1) — guidance and worked examples | formatting |
| 23 ★ | Page-flow settings: keep with next, keep lines together, page break before, widow/orphan control (each PART on a new page, no stranded article titles) | `set_properties` paragraph flags (exist) — guidance for "each PART on a new page" and "no stranded titles", worked example | formatting |
| 24 | Text colour (editor's blue text to black) | `set_properties` `run.color` (exists) + **new run selector `color_in`** (both sides) | formatting |
| 25 | Tab stops on chosen paragraphs | **new op `set_tab_stops`** + V10 kind `tab_stops` | formatting |

Status: **built** (see "Built" at the end). Decisions below were taken on Abraham's behalf, as in batches 1 and 2, and can
each be changed. Reading order: CLAUDE.md hard rules 1–3, 8 and 9, the "Selector grammar" and "Effective pinnable
properties" definitions, `docs/SET_PROPERTIES_DESIGN.md`.

## 0. What already existed, and what was missing

Items 22 and 23 are the two ideas (#22–23 of the original list) that `set_properties` was designed and built for
(PR #16/#18): `run` `b`, `i`, `u`, `caps`, `smallCaps` (and `strike`, `color`) on selected paragraphs' runs and marks, or
in style definitions; `paragraph` `keepNext`, `keepLines`, `widowControl`, `pageBreakBefore`. Nothing new is needed to
*do* them. Two things were checked end to end on the fixtures instead:

* **"Each PART on a new page"** must not push PART 1 away from the section title on page 1. The selector grammar already
  says it: `{"text_regex": "^PART\\s+\\d", "after_heading_regex": "^PART\\s+\\d"}` selects every PART heading after the
  first (or `style_in` with the PART style plus the same `after_heading_regex`). The compiler is told so, and the
  worked example `examples/specs/part-on-new-page.json` does it, together with keep-with-next on the article titles.
* **"No stranded article titles"** is `keepNext` on the article-title paragraphs (their style id, or a text pattern for
  typed numbers); `keepLines` too when the user asks that a paragraph never splits. Widow/orphan control is on by
  default in Word (`widowControl` absent anywhere = on — ECMA-376 §17.3.1.44), so "turn it on" changes only paragraphs
  or styles that turned it off; the plan says how many were already on.

Item 24 had a gap: `set_properties` can *set* a colour, but cannot *choose the runs by their colour*. "Editor's blue text
to black" over a whole document would also recolour hyperlinks, red text and every other coloured run. §1 adds the
selector. Item 25 had no operation at all: §2.

## 1. The run selector `color_in` (both sides)

`select.runs: {"color_in": ["0000FF", "accent1", …]}` — for `set_properties` and `set_font` (the two ops that take a run
selector). A run (or paragraph mark) is selected when its **effective colour** — the nearest level that sets `w:color`:
direct formatting → character style chain (rStyle, else the default character style) → paragraph style chain (pStyle,
else the default) → table style chain → docDefaults, default `("auto", None, None, None)`; set_properties' colour rule —
matches the list: its `w:themeColor` name or its `w:val` (6 hex digits, or `auto`), case-insensitive, a leading `#`
ignored. That is batch 2's colour-list definition (`remove_notes`' `color_in`) over set_properties' resolver; a run
whose colour comes from a style is chosen like one with direct colour. Every entry must be 6 hex digits, `auto` or a
theme colour name (`bad-params`). The colour is read from the SOURCE (the selection is), so "blue → automatic" selects
the blue runs and the post-condition proves they are automatic afterwards.

Transform: `ops/selectors.rpr_selected(pred, rpr, revised, p, sheet)` with `effective_color` (`PropResolver.run_prop(rpr,
p, "color")`, one resolver kept on the sheet — so `set_font.py`, an example a custom transform copies, imports nothing
new), `check_run_selector` (validates the list); verifier:
`verify/props.target_matches(…, color_of=)` with `_v_run_prop(rpr, p, styles, "color")`, its own list reading
(`_v_sel_colors`, `_v_sel_color_in`). A test plants a transform matcher that forgets the style chain and one that
forgets theme names; V10's counts and effective checks catch both.

Whether a whole-document recolour is tracked stays §14.1's rule: `runs` narrowing alone is not "targeted" (plain), a
`paragraphs` predicate is (tracked). "Black" is `auto` (Word's Automatic, what body text normally has) unless the user
asks for an explicit black (`000000`); the compiler says which in assumptions.

## 2. `set_tab_stops` — tab stops on chosen paragraphs

```json
{"op": "set_tab_stops",
 "select": {"scope": ["footers"], "paragraphs": "all"},
 "params": {"set": [{"pos": "right_margin", "align": "right"}], "clear": [4680], "clear_others": false}}
```

* `set` — stops to have: `{"pos", "align", "leader"}`; `pos` in twips from the left margin (1440 = 1 inch, −31680…31680)
  or `"right_margin"` / `"center"`; `align` `left | center | right | decimal | bar`; `leader` `none` (default) `| dot |
  hyphen | underscore | heavy | middleDot`.
* `clear` — positions (same forms) where no stop may be in effect afterwards.
* `clear_others` — every other stop in effect (from the paragraph or its styles) is cleared, so the paragraph ends with
  exactly the `set` stops.
* `tracked` / `author` as `set_paragraph_props` (§14.1: plain for a whole-document selection, tracked when
  `paragraphs` narrows it).
* `select`: `scope` and `paragraphs` (set_paragraph_props' grammar). Direct formatting only; tab stops in style
  definitions are not in this batch.

At least one of the three; two `set` stops or a `set` and a `clear` position that are the same number are `bad-params`;
two that resolve to one position in a file (`right_margin` = 9360 beside an explicit 9360) refuse that file
(`tab-position`).

### 2.1 Definitions (both sides; transform `ops/set_tab_stops.py`, verifier `verify/tabs.py`)

* **Own stops**: the paragraph's `w:pPr/w:tabs/w:tab` entries, each `(pos, val, leader)` (`leader` absent = `none`); a
  `val` of `clear` is a *clear entry*. Two own entries at one position, or one without a whole-number position, refuse
  the file (`structure`).
* **Inherited stops**: the existing tab fold without the paragraph's own pPr — docDefaults → table style chain →
  paragraph style chain, each level's `clear` removing an earlier stop at its position (the "Effective pinnable
  properties" definition: transform `effective.Resolver.paragraph(p, direct=False)`, verifier
  `template.Eff.para(p, with_direct=False)`; already tested to agree).
* **In effect** = the inherited stops folded with the own entries.
* **Numbered**: numbering in effect (the existing definition: the first `w:numPr/w:numId` of own pPr → style chain →
  docDefaults; 0 = off). The list definition can add tab stops between the styles and the paragraph, and the op does not
  read list definitions — so on a numbered paragraph only the paragraph's own entries count as certain.
* **Margin positions**: `right_margin` = the text width W of the paragraph's section (page width − left − right − gutter,
  the existing text-width definition), `center` = W // 2. The section of a body paragraph: of its outermost paragraph
  (itself unless in a text box), the number of paragraphs holding a `w:pPr/w:sectPr` before it in document order indexes
  the document's sections (paragraph section breaks in order, then the body's). A header/footer paragraph: every section
  that shows the part (own reference or inherited, any type) — all must have one W, else `tab-position`; a part no section
  shows, a section without page size or margins, or a margin position in footnotes, endnotes or comments, is
  `tab-position` too.

### 2.2 The rule

For a selected paragraph, with S = the `set` stops (position → (align, leader)) and C = the `clear` positions, plus — with
`clear_others` — every position in effect that is not in S:

* a position X in S is **already** when (numbered: the own entry at X is exactly S[X]; else: the stop in effect at X is
  exactly S[X]); otherwise the own entries at X go and `(X, align, leader)` is written;
* a position X in C is **already** when (numbered: the own entry at X is a clear entry; else: no stop is in effect at X);
  otherwise the own entries at X go and a clear entry at X is written when a stop is inherited at X or the paragraph is
  numbered (the list may set one) — else nothing (the stop was the paragraph's own).

A paragraph with nothing to do is left alone and not counted ("already as asked", noted). A changed paragraph's own
entries are the untouched source entries (as they were) and the new ones, **sorted by position** (stable); none left →
`w:tabs` removed (the schema needs at least one); a new `w:tabs` goes in schema order. A new stop is
`<w:tab w:val="…" [w:leader="…" when not none] w:pos="…"/>`, a clear entry `<w:tab w:val="clear" w:pos="…"/>`. Tracked:
one `w:pPrChange` per changed paragraph, snapshot = the source pPr (without rPr, sectPr, pPrChange); an existing
`pPrChange` refuses tracked (`tracked-format`) and is flagged when plain — exactly `set_paragraph_props`. Numbered
paragraphs under `clear_others` are flagged: their list definition's own stops (if any) are not cleared.

Values are compared exactly (`start` is not `left`: in a right-to-left paragraph it is the right side).

### 2.3 Verification (V10 `tab_stops`)

`verify/tabs.py` re-derives everything from the SOURCE with its own code (rule 1): the scope's parts, the selection
(`para_matches`), own entries, inherited fold (`template.Eff`), numbered (`removal._v_numbered`), the sections and widths
(`props.sect_prs`, `template._width`, `template._refs_shown`) and the rule. It fails when the changed paragraphs or their
count differ from the plan's; when a changed paragraph's output `w:tabs` is not exactly the expected entries (untouched
entries by full signature, new ones by exact attributes, in order; absent when none); when the `pPrChange` breaks
`set_paragraph_props`' rules; and — a post-condition on the OUTPUT — when a `set` position does not show the asked stop,
a cleared position still shows one (numbered: has no own clear entry), or, with `clear_others`, any other stop is in
effect. It strips only `tabs` and `pPrChange` of the changed paragraphs; the residual compares everything else, so a
neighbouring pPr child, an unselected paragraph or a no-op `pPrChange` on an already-right paragraph fails.

Touches: `(part, block, "pPr")` per changed paragraph (so `set_paragraph_props` or `set_properties` paragraph flags on the
same paragraph are a `conflict` — one spec each, or a chain), `(part, "scope", "verify")` per part in scope; with a
margin position, the reads `(main, "sectPr:N", "verify:pgSz"/"verify:pgMar")` of every section it measured and
`verify:hfref` of every section for a header/footer part (so a `set_page_setup` beside it is a plan-time conflict, never
a stale position). Not declared, as for `set_font` (CLAUDE.md "Plan-time conflicts for what an op reads" 5): the reads of
the style chains' tabs and numbering — no op changes either today. Layout reference `none` when anything changes (a tab
stop moves text). Zero text diffs.

## 3. Around them

* `catalog.py`: `COMMON_ASSUMPTIONS["set_tab_stops"]`; `SELECTOR_GRAMMAR["runs"]` names `color_in`;
  `CUSTOM_TRANSFORM_LIMITS` gains "tab stops of paragraphs" → `tab_stops`.
* `compiler.SYSTEM`: "each PART on a new page" (every PART after the first), "no stranded titles", "blue → black"
  (`runs.color_in` from the profiles' colours, `auto` for black), tab stops (twips, `right_margin`/`center`,
  `clear_others`), and the pPr conflict with `set_paragraph_props`/paragraph flags.
* `profile`: a style in use shows its inherited tab stops (`tab_stops`), so Claude can name the positions to clear.
* `report.FAILURE_EXPLANATIONS`: `tab-position`.
* Worked examples: `part-on-new-page.json`, `editors-blue-to-black.json`, `footer-right-tab.json`.

## Decisions for Abraham (each with the recommendation taken)

1. Items 22–23 need no new operation; they are `set_properties` (built earlier) plus guidance and examples.
2. "Each PART on a new page" = page break before on every PART heading after the first; PART 1 stays under the title.
3. Colour selection by the *effective* colour (a style's blue counts), hex or theme name; "black" = Automatic.
4. Tab stops are a new op (`set_tab_stops`), direct formatting only; positions in twips or `right_margin`/`center`.
5. `clear_others` leaves a numbered paragraph's list-definition stops alone (flagged); an explicit `clear` writes a clear
   entry on numbered paragraphs.
6. Tab stops and spacing/indents/keep-with-next on the same paragraphs are two specs (rule 9's pPr granularity, unchanged).

## Built

All of the above, with `tests/test_tab_stops.py` and `tests/test_color_selector.py` (planted defects in the edit rule,
the margin resolution, the section reader, the numbered rule, the colour matcher; the two section readers compared).
Not in this batch: tab stops in style definitions, bold/colour on part of a paragraph (a run-in heading), a list-level
selector, theme colours as a value to set.
