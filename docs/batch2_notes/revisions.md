# Batch 2 notes — `resolve_tracked_changes` (item 13)

Builder B's notes for the integration (README, CLAUDE.md, report, catalog, compiler). Code: `src/spec_surgeon/ops/revisions.py`,
`src/spec_surgeon/verify/revisions.py`; tests: `tests/test_revisions.py` (fixtures `tests/fx_revisions.py`); worked example:
`examples/specs/accept-changes-by-author.json`.

## (a) README text

### Accept or reject tracked changes (`resolve_tracked_changes`)

This is the one operation that accepts or rejects tracked changes — and it runs only when you ask for it: "accept all of
Jeremy's changes", "reject everything Jack did after October 1", "accept all changes in the headers". The method used to
say that only you accept or reject, in Word. At Abraham's request (built-in operations batch 2) the rule is now: **never
on the engine's or Claude's own initiative — only when you ask for it by name**, and the preview says exactly what will
be accepted or rejected before anything runs ("ACCEPTS 15 tracked change(s) — deletion 5 (Jack 5); insertion 4
(Jeremy 4); …").

What you can ask for:

* `action`: `accept` or `reject` (required).
* `authors`: only the changes by these people — matched to the name Word shows on each change (case and spacing
  ignored). Leave it out for everyone.
* `on_or_after` / `before`: only the changes made in a date range — `YYYY-MM-DD`, or with a time (`2026-09-23T14:30`),
  read from Word's own clock on each change (`on_or_after` includes the day, `before` does not). Under a date filter a
  change that carries no date is left as it is (the preview flags how many).
* `select.scope`: which parts — by default all of them: body, headers, footers, footnotes, endnotes, comments.
* `allow_missing` (default **true**): a file in the batch with no change matching the request is left as it is ("no
  tracked change by Jeremy: nothing to do"). Set it to false to have such a file set aside instead.

What accepting and rejecting do — the same as Word's Accept/Reject:

| Tracked change | Accept | Reject |
|---|---|---|
| inserted text | stays, as plain text | goes |
| deleted text | goes | comes back, as plain text |
| moved text | stays where it was moved to | goes back where it came from |
| a paragraph inserted whole | stays | goes |
| a paragraph deleted whole | goes | comes back |
| a formatting change (run or paragraph) | the new formatting stays | the old formatting comes back |
| a change of section or table properties | the new properties stay | left for Word (refused) |

A change inside text that goes, goes with it (rejecting Jeremy's insertion also removes Jack's deletion inside it). A
changed paragraph's formatting record is dropped once it is empty. Word's `_GoBack` bookmark (where the cursor was last
saved) goes with text that goes; Word makes a new one on save.

What it leaves for Word (the file is set aside with the reason; nothing is written):

* a tracked table row or cell insertion/deletion, a tracked change inside an equation, or — when rejecting — a change of
  section or table properties (`revision-kind`);
* an accept or reject that would **join two paragraphs**: a deleted paragraph mark with text still on both sides, a
  paragraph that would go but still holds a bookmark, a picture or a comment, or the last paragraph of a cell, before a
  table or at the end of the document — Word decides whose formatting the joined paragraph keeps (`paragraph-merge`); one
  carrying a section break (`section-break`);
* a moved passage the authors/dates asked choose only partly (its two ends by different people) — a move is resolved
  whole (`partial-move`);
* text that would go while it holds a footnote or comment mark, one end of a bookmark, or part of a field — removing it
  would orphan the note or comment or break the bookmark or field (`marks-in-change`).

How it is verified: every paragraph whose text changes (accepted deletions, rejected insertions, paragraphs that go) is
predicted before anything runs (V2), and so is every change of tabs, breaks or field marks (V3); the tracked-change
count must fall by exactly the predicted number of each kind (V4) and no tracked change may be added (V5b); and the
verifier resolves every part of the original again **with its own code** and compares the result with the output part,
element for element (V10, `resolved_changes`). The tests also compare the result with pandoc's own accept/reject of the
original. Accepting or rejecting touches whole parts, so it runs as a spec of its own — or as the first stage of a chain,
with the other changes after it.

## (b) CLAUDE.md lines

Both-sides definition (add to "Other definitions that must stay identical on both sides"):

- **Resolving tracked changes** (`resolve_tracked_changes`, batch 2). Revision elements: `ins`/`del`/`moveFrom`/`moveTo`, the
  property changes (`rPrChange`, `pPrChange`, `sectPrChange`, `tblPrChange`, `trPrChange`, `tcPrChange`, `tblGridChange`,
  `numberingChange`) and the cell revisions, outside any property change's snapshot. By parent: under a `w:rPr` a
  paragraph mark's when that rPr is a `w:pPr`'s, else an equation's (refused); under `w:trPr` a row's, under `w:numPr` a
  numbering marker's (refused); else content. Chosen: author in the list (casefolded, whitespace collapsed) and
  wall-clock `w:date` in range (`YYYY-MM-DD[THH:MM[:SS[.f]]]` then optional `Z`/offset, ignored; user dates
  `YYYY-MM-DD[THH:MM[:SS]]`; `on_or_after` inclusive, `before` exclusive; unreadable date under a date filter → not
  chosen). Moves: a piece belongs to the innermost open range of its kind (document order, paired by id); the ranges
  sharing a name and their pieces are chosen whole (`partial-move`), their range marks go with them. Order: content
  (accept unwraps ins/moveTo, drops del/moveFrom; reject the reverse), then deleted text left in no deletion becomes live
  (`delText`→`t`, `delInstrText`→`instrText`, only what was inside an unwrapped deletion), the move ranges, property
  changes (accept drops; reject: rPr = the mark's own markers then the snapshot, pPr = the snapshot then rPr and sectPr),
  marks (kept side: marker goes; other side: the paragraph goes when its resolved content is only empty runs
  (`rPr`/`lastRenderedPageBreak`), proofing marks, wrappers of those and `_GoBack`, a paragraph follows it — block-level
  marks skipped — and it has no sectPr; else `paragraph-merge` / `section-break`), then an emptied rPr then pPr goes.
  Content that goes may hold no note/comment mark, no single end of a bookmark/permission/move range (`_GoBack` goes
  whole) and no part of a complex field (`marks-in-change`). Transform: `ops/revisions.classify`, `Selection`,
  `_moves`, `_marks_in_removed`, `_leftover`, `analyze` (decisions), `predict`/`gone_revisions` (analytic V2/V3/V4),
  `resolve` (in-place surgery); verifier: `verify/revisions._v_class`, `_v_when`, `_v_decide`, `_v_bare`, `_v_build` (a
  REBUILD of the part from the decisions, never an in-place edit), `_resolved_changes`. `tests/test_revisions.py` checks
  the outputs against pandoc's accept/reject and plants shared defects (selection, semantics, the wrong paragraph, a skipped
  join or anchor check, a wrong count).

Rules:

- Accepting or rejecting is never done unasked (the method's rule, design note §0): the op is chosen only when the user
  asks to accept or reject; its flags list what is resolved by kind and author.
- Its touches are `(part, "*", "text")` for every part it changes: a spec of its own (or a chain's first stage).
- `apply` re-runs `analyze` on the working copy (the parts are the op's alone) and refuses (`internal`) if it chooses a
  different number of changes than the plan counted.
- V10 `resolved_changes` compares every part in scope WHOLE (`ctx.verified_parts`): its revision counts per V4 kind and the
  number chosen must equal the plan's (`expected_gone`, `expected_selected`).

## (c) FAILURE_EXPLANATIONS entries

```python
"revision-kind": (
    "Tracked Change Left for Word",
    "The file holds a tracked change this operation does not accept or reject: a table row or cell insertion or "
    "deletion, a change inside an equation, or (when rejecting) a change of section or table properties. Accept or "
    "reject that one in Word (Review tab), then run again — or name other authors or dates.",
),
"paragraph-merge": (
    "Accepting Would Join Paragraphs",
    "Accepting or rejecting a paragraph mark here would join the paragraph to the next one: it still holds text, a "
    "bookmark, a picture or a comment, or it is the last paragraph of its cell, before a table, or of the document. Word "
    "decides whose formatting a joined paragraph keeps, so this change is left for Word: resolve it there, then run again.",
),
"partial-move": (
    "Move Only Partly Chosen",
    "A moved passage (cut and pasted with Track Changes on) has its two ends or its pieces by different authors or "
    "dates, so the authors or dates asked choose only part of it. A move is accepted or rejected whole: resolve it in "
    "Word, or widen the request to include it.",
),
"marks-in-change": (
    "Change Holds an Anchor",
    "The text this accept or reject would remove holds a footnote or comment mark, one end of a bookmark, or part of a "
    "field. Removing it would leave the note or comment without its place in the text, or break the bookmark or field. "
    "Resolve this change in Word, then run again.",
),
```

`section-break`, `not-found`, `bad-params`, `internal` already exist (the existing `section-break` text speaks of deleting or
inserting; it fits — "a paragraph that would go carries a section break").

## (d) COMMON_ASSUMPTIONS

```python
"resolve_tracked_changes": [
    "only when the user asks to accept or reject tracked changes — never as a side effect of another request; "
    "everyone's changes unless authors or dates are named",
    "authors are matched by the name Word shows on each change (case and spacing ignored); dates by Word's own clock on "
    "each change, on_or_after inclusive and before exclusive; under a date filter a change without a date stays (flagged)",
    "accept: inserted and moved text stays as plain text, deleted text and paragraphs go, new formatting stays; reject: "
    "inserted text and paragraphs go, deleted text comes back, moved text returns, the old formatting comes back",
    "the body, headers, footers, footnotes, endnotes and comments are covered (scope narrows it); tracked changes inside "
    "style or list definitions stay as they are",
    "a file with no matching change is left as it is (allow_missing, the default); a table row/cell change, a change in "
    "an equation, an accept that would join two paragraphs or take a footnote/comment anchor away sets the file aside",
    "it runs as a spec of its own (or the first stage of a chain): it changes the parts it touches as a whole",
],
```

## (e) Words placed

None (`compiler.placed_words` needs no entry): accepting keeps the document's own inserted words, rejecting brings back
its own deleted words.

## (f) SYSTEM-prompt hints

- Accepting or rejecting tracked changes is `resolve_tracked_changes` — ONLY when the user asks to accept or reject
  (never to "clean up", "finalize" or as part of another change unless those words are said). State in assumptions how you
  read the authors (as the profiles' revisions name them) and the dates (Word's clock; on_or_after inclusive, before
  exclusive). It is a spec of its own: when the user also wants other changes, make it the first stage of a chain.
- The profiles' `pending_revisions` counts tell you which files have tracked changes; a file without any is left as it is.

## (g) Departures from the design note

1. `allow_missing` defaults to **true** (coordinator's decision): a file with nothing chosen plans as a no-op with a note;
   `allow_missing: false` keeps `not-found`.
2. A new refusal, `marks-in-change`: content that goes may hold no footnote/endnote/comment mark, no single end of a
   bookmark, permission or move range, and no part of a complex field (it would orphan the note or comment, or break the
   bookmark or field). Word's `_GoBack` bookmark is the exception: when one end lies in text that goes, both ends go.
3. A paragraph that would go while carrying a section break is refused with the existing `section-break` code (the
   design listed it with `paragraph-merge`).
4. Rejecting a `pPrChange` whose snapshot holds tracked changes is refused (`revision-kind`), like an rPrChange snapshot
   with markers.
5. "A paragraph follows it" skips block-level marks (bookmarks, comment ranges, permissions, move ranges, proofing marks,
   custom-XML range marks) between the two paragraphs; any other block (a table, a content control, `w:sectPr`) refuses.
6. Under `paragraph-merge`, revision wrappers left in the paragraph count as content only through what they hold — an
   empty still-pending wrapper goes with the paragraph (its count is in the V4 delta).
7. `tests/test_planner.py::test_catalog_and_schema_cover_every_op` fails on this branch until `catalog.COMMON_ASSUMPTIONS`
   gets the entry above (a shared file, left to the integration).
