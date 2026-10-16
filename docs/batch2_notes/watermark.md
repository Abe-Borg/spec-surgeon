# Batch 2 notes — watermark (item 16): `add_watermark`, `remove_watermark`

Builder D's notes for the coordinator: what goes into README.md, CLAUDE.md, `report.FAILURE_EXPLANATIONS`,
`catalog.COMMON_ASSUMPTIONS`, `compiler.placed_words` and the SYSTEM prompt, and where the build departs from
`docs/BUILTIN_OPS_BATCH2_DESIGN.md` §7. Code: `src/spec_surgeon/ops/watermark.py` (transform),
`src/spec_surgeon/verify/watermark.py` (verifier, kinds `added_watermark` and `removed_watermark`), tests
`tests/test_watermark.py`, worked example `examples/specs/add-draft-watermark.json`.

## (a) README text

### Watermarks: "DRAFT", "NOT FOR CONSTRUCTION"

**`add_watermark`** draws your words faintly behind every page, the way Word's *Design → Watermark* does: diagonal
(or horizontal), Calibri, silver, half transparent, as large as fits the page — each of those can be changed (`font`,
`color` as 6 hex digits, `opacity` from 0.05 to 1, `layout`, `size` in points). The words are placed exactly as you
typed them; they are one line of at most 100 characters.

- It goes into every header your document shows — the cover's first-page header, the body's header, even-page headers —
  as the first thing in each header's first paragraph, so nothing in the header moves. A section whose pages show no
  header gets no watermark, and the preview says so (add a header in Word first). A file with no header at all is set
  aside: Spec Surgeon never creates a header.
- A file that already has a watermark is set aside unless you say to replace it (`replace: true`): then the old one is
  taken out and yours goes in, in one step.
- `auto` size: the box keeps the proportions of your words in the font (measured with Calibri's, Arial's or Times New
  Roman's character widths; another font is measured with Calibri's, and the preview says the size is estimated) and is
  as large as fits the smallest page of the sections that show that header. A size in points that does not fit a file's
  page sets that file aside (`watermark-size`), never the whole batch.
- A watermark is not a tracked change in Word, so the spec's mode is `untracked`; the preview flags that the words go on
  every page.

**`remove_watermark`** takes the watermark out of every header — Word's text watermarks, its picture watermarks and the
newer drawing kind — or, with `text`, only the ones showing those words (any case or spacing). The paragraph that held
it (and Word's "Watermarks" content control around it) stays, so the header does not move. A file without a watermark
is left as it is (`allow_missing` defaults to true; `false` sets such a file aside).

**How it is verified.** The verifier finds the headers, each header's first paragraph and every watermark again with
its own code; for an added watermark it builds Word's watermark shape again from your words and the asserted look with
its own template, checks the shape's ids are new and occur once in the package, checks the box lies inside every
section's text area and fills it as the rule says, and compares each changed paragraph item by item — exactly one run
added at its start (and, with replace, exactly the old runs gone), nothing else. For a removal, every paragraph that held
a watermark must be the source's with exactly those runs gone. The layout reference is `none`: a watermark is drawn over
every page.

Refused (the file is set aside): a watermark that shares its run with other content, sits in a pending tracked change
or in a text box; taking out a watermark whose run carries a shape definition another shape of the header still uses
(`structure`); a section without a usable page size.

## (b) CLAUDE.md lines

Under "Other definitions that must stay identical on both sides":

- **Watermarks** (`add_watermark`, `remove_watermark`, built-in batch 2; design note §7). A **watermark** is a run of a
  header part whose one content element (any child but `w:rPr`) is a `w:pict`, `w:drawing` or `mc:AlternateContent`
  holding a VML `v:shape` whose `id` starts with `PowerPlusWaterMarkObject` (text) or `WordPictureWatermark` (picture), or
  a DrawingML `wp:docPr` whose `name` does; its **text** is the `string` of the first `v:textpath` that has one (a
  shapetype's own `v:textpath` has none), else the `w:t` of its first `w:txbxContent`, else none (a picture). Refused: a
  watermark shape outside a run, in a run with other content, in a text-box paragraph (`structure`) or under
  `w:ins`/`w:del`/`w:moveFrom`/`w:moveTo` (`existing-revisions`). **Headers** (add): every header part some section shows,
  any type (own references first, else inherited; header-type relationships only). **Anchor**: the part's first `w:p`
  whose every container below `w:hdr` is `w:sdt`/`w:sdtContent` (Word's "Watermarks" content control) — the new run goes
  first after its `w:pPr`. **The run**: `w:rPr/w:noProof` + `w:pict` holding the `_x0000_t136` shapetype (only when the
  part keeps none outside the replaced watermarks) and Word's text-watermark `v:shape` (id `PowerPlusWaterMarkObject<N>`
  new to the package, `o:spid` `_x0000_s<M>` above every `_x0000_s` number of the package, from 2049); ids new and unique
  across `id` and `o:spid` values. **Text area**: (pgSz w − left − right − gutter, pgSz h − |top| − |bottom|) in points.
  **Size**: h/w = 1.55 / the advance width in em (`ops/watermark.WIDTHS`, measured once from Carlito / Liberation Sans /
  Liberation Serif; not re-derived by the verifier — a wrong proportion distorts the letters, which the render shows);
  auto diagonal: (w + h)/√2 = 0.98 × the shorter side of the smallest text area of the showing sections; auto
  horizontal: w = min(0.9 × width, 0.98 × height / proportion); points: h = 1.55 × size, and it must fit (else
  `watermark-size`). **Numbers**: rounded to 2 decimals, written without trailing zeros. **Removing** never takes out the
  only definition of a VML shapetype a shape staying in the part still uses (unless the op brings `_x0000_t136` back).
  Transform: `ops/watermark.watermark_runs/watermark_text/first_paragraph/headers_shown/text_area/box/number/build_run/
  check_shapetype_users`; verifier: `verify/watermark._v_watermarks/_v_text/_v_anchor/_v_shows/_v_area/_fits/_v_num/
  _v_pict/_v_definition_still_used`. `tests/test_watermark.py` compares the two readings (headers, anchors, watermarks,
  areas, numbers — 5,000 random values) and plants shared defects (template, formulas, sizing, header set, anchor, ids,
  detection both ways, the shapetype check).
- Rule 10 gains nothing new: `add_watermark` never creates a header part (`no-header`); it adds runs to existing ones.

Words placed (rule 13 list): `add_watermark.text` (see (e)).

## (c) FAILURE_EXPLANATIONS entries

```python
"watermark-exists": (
    "A Watermark Is Already There",
    "The file already has a watermark (its words are in the reason). Nothing was changed. To swap it for the new "
    "words, ask to replace the watermark; to keep both, add the new one in Word.",
),
"no-header": (
    "No Header to Carry a Watermark",
    "Word draws a watermark from the page header, and this file has no header at all. Spec Surgeon never creates a "
    "header; add one in Word (even an empty one), then run again.",
),
"watermark-size": (
    "Watermark Too Large for This Page",
    "The font size asked for makes the watermark larger than this file's text area (its page size and margins are in "
    "the reason). Use a smaller size, or 'auto' to fit every page.",
),
```

Codes reused (already explained): `structure`, `existing-revisions`, `not-found`, `bad-params`.

## (d) COMMON_ASSUMPTIONS

```python
"add_watermark": [
    "the watermark's words are exactly the user's (one line); Word's look unless asked otherwise: diagonal, Calibri, "
    "silver (C0C0C0), half transparent, sized to fit the page",
    "it goes into every header the document shows (cover first-page header included), first in each header's first "
    "paragraph; pages without a header get none (flagged); a file without any header is set aside",
    "a file that already has a watermark is set aside unless the user asked to replace it (replace true)",
    "a watermark is not a tracked change in Word: the spec is untracked",
],
"remove_watermark": [
    "every watermark in every header goes (text, picture or drawing), or only those showing the stated words",
    "the header's paragraphs stay, so nothing in the header moves",
    "a file without a watermark is left as it is (allow_missing defaults to true)",
],
```

## (e) Words placed

`compiler.placed_words`: `add_watermark` → `params["text"]` when it is a non-empty string. `remove_watermark.text` places
nothing (it only selects).

## (f) SYSTEM-prompt hints for Claude

- A watermark ("DRAFT", "NOT FOR CONSTRUCTION", "PRELIMINARY") is `add_watermark` with `text` = the user's exact words
  (mode `untracked`); when the user did not say the words, ask. Keep Word's look unless the user describes another
  (colour as 6 hex digits, layout diagonal or horizontal, size in points or auto). "Change the watermark to X" is
  `add_watermark` with `replace: true`. "Remove the watermark" is `remove_watermark` (mode `formatting`), with `text`
  only when the user names which.
- An `add_watermark` and tracked word changes are two specs (untracked vs tracked content): offer them as a chain.

## (g) Departures from the design note (§7)

1. **`remove_watermark.allow_missing` defaults to true** (coordinator's decision): a file without a watermark is a
   no-op with the note "no watermark: nothing to do"; `allow_missing: false` keeps `not-found`.
2. **New code `watermark-size`** (coordinator's correction): a size in points that does not fit a file's text area is
   that file's refusal (file-specific), not `bad-params` (which `compiler.FATAL_SPEC_CODES` treats as a spec mistake for
   the whole batch). `bad-params` stays for params wrong for any file (empty text, control/invisible character, more
   than 100 characters, colour not 6 hex digits, opacity outside 0.05–1, size ≤ 0 or over 1000, a font name with quotes
   or semicolons, layout, replace).
3. **The anchor** is the first paragraph whose containers below `w:hdr` are only content controls, not strictly the
   first direct child: Word puts a watermark into an empty header as a "Watermarks" content control around its own
   paragraph, and replacing such a watermark must work (the new run goes where the old one was).
4. **Watermark text** is the first `v:textpath` *with* a `string` (Word's shapetype carries a `v:textpath` without one —
   the agreement test caught the two sides reading this differently).
5. **The shapetype check covers every VML shapetype**, not only `_x0000_t136`: a picture watermark carries
   `_x0000_t75`; taking out the only definition another shape of the part still uses is refused (`structure`);
   `add_watermark` with replace brings `_x0000_t136` back, so that one does not count there.
6. **DrawingML watermarks with a text box**: removing one deletes its text-box paragraphs — predicted as deleted blocks
   (V2) with a whole-part touch — and the holding paragraph loses a `w:drawing` (a structure-only declaration, V3).
7. **Refusals added**: a watermark shape outside a run or in a text-box paragraph (`structure`), one inside a pending
   tracked change (`existing-revisions`).
8. **Reads declared**: `add_watermark` touches `(main, "sectPr:i", "verify:pgSz" / "verify:pgMar" / "verify:hfref")` for
   every section showing a header it sizes for (so `set_page_setup`, `remove_header_footer` or the templating op in the
   same spec is a plan-time conflict).
9. The verifier does not re-derive the box's proportions (the transform's font metrics); it checks the box is the
   asserted one, lies inside every showing section's text area, fills it as the `auto` rule says, and for a size in
   points has h = 1.55 × size.

Known open item for the coordinator: `tests/test_planner.py::test_catalog_and_schema_cover_every_op` fails until
`catalog.COMMON_ASSUMPTIONS` has the two entries in (d) (a shared file this builder did not edit).
