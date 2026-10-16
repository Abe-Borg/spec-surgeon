# Batch 2 notes — `remove_comments` and `remove_highlight` (builder A)

Built on 4fa4991 (the shared machinery). Files: `src/spec_surgeon/ops/comments.py`, `ops/highlight.py`,
`verify/comments.py` (`COMMENT_KINDS`), `verify/highlight.py` (`HIGHLIGHT_KINDS`), `tests/fx_comments.py` (fixtures),
`tests/test_comments.py`, `tests/test_highlight.py`, `examples/specs/remove-resolved-comments.json`,
`examples/specs/remove-highlighting.json`.

## (a) README text

#### `remove_comments` — clean files for the client

Comments come out of the documents — **all** of them (`"which": "all"`, the default), only the **resolved** threads
(`"which": "resolved"` — what Word marks with *Resolve*), or the comments of named **authors** (`"which": "authors",
"authors": ["Jack"]`, case and spacing ignored). A removed comment takes its replies with it (a thread never keeps a
reply whose comment went); removing one author's reply leaves the thread's first comment and the other replies alone.
The document's text is unchanged: only the comments and their marks in the text go — the range start and end marks and
the little reference mark Word draws at the end of the commented words (its run too, when it holds nothing else).

What Word keeps about a comment lives in up to four parts: `comments.xml` (the text), `commentsExtended.xml` (threads and
the resolved flag), `commentsIds.xml` and `commentsExtensible.xml` (Word 2016+ bookkeeping). Every entry of a removed
comment goes from every one of them, matched by the comment's paragraph ids. **No part is ever removed**, even when
every comment goes (an empty `comments.xml` is valid and Word opens it), and no relationship changes — so a picture or a
link that only a removed comment used stays in the package, unused (the plan flags it). A pending tracked change
*inside* a removed comment's text goes with it (predicted, flagged).

A file with nothing to remove — no comments, no resolved thread, none by those authors — is **left as it is** (a note
says "nothing to do"): clean-up operations run over whole batches where many files have nothing to remove.
`"allow_missing": false` sets such a file aside instead (`not-found`). A file from Word 2010 or older keeps no resolved
state (no `commentsExtended.xml`): `resolved` finds nothing there, and the note says why.

Refused: a mark of a removed comment inside a text box (`textbox` — Word keeps two copies of a text box; remove that
comment in Word), two comments sharing an id or a paragraph id (`structure`).

```json
{"op": "remove_comments", "params": {"which": "resolved"}}
```

V10 (`removed_comments`, `verify/comments.py`) reads the comments, threads and resolved flags again with its own code and
must arrive at the same comments; it then compares `comments.xml` and every side part **whole** with the source minus
exactly those entries, checks every paragraph that held a mark item by item (exactly the marks gone, nothing else), and
hands marks that sit between paragraphs to the residual as expected removals. V2 sees every paragraph of a removed
comment deleted; V3 sees each paragraph that lost a reference mark as a structure-only change the plan declared.
(`examples/specs/remove-resolved-comments.json`.)

#### `remove_highlight` — the highlighter off

Word's highlighter (Home → *Text Highlight Color*; `w:highlight`) comes off text — every colour (the default) or the
listed ones (`"colors": ["yellow"]`; Word's names: yellow, green, cyan, magenta, blue, red, darkBlue, darkCyan,
darkGreen, darkMagenta, darkRed, darkYellow, darkGray, lightGray, black, white). Body, headers, footers, footnotes and
endnotes by default (`select.scope`; comments too when asked). Every highlighted run is included — inside pending
insertions and deletions too (a deletion rejected later must not bring a highlight back), inside hyperlinks and text
boxes (both of Word's copies) — and every highlighted paragraph mark. An explicit "no highlight" (`none`) is not a
highlight and stays. The text and every other formatting stay exactly as they were; an emptied `w:rPr` (and an emptied
paragraph `w:pPr`) is removed.

Plain by default (whole-document formatting, decision §14.1). `"tracked": true` records each change as Word's
*Formatted* revision (`w:rPrChange` with the old formatting as its snapshot, so reject-all brings the highlight back); a
run that already carries someone's tracked formatting change is then refused (`tracked-format`: a second one cannot be
expressed — accept or reject it first, or remove the highlight plainly). A **style** that highlights its text is never
changed (flagged: change the style in Word). Paragraph or text *shading* (`w:shd`) is not highlighting and is not
touched. A file without such highlighting is left as it is (`"allow_missing": false` sets it aside: `not-found`).

```json
{"op": "remove_highlight", "params": {"colors": ["yellow"]}}
```

V10 (`removed_highlight`, `verify/highlight.py`) reads the colours you asked for and the scope itself (its own defaults:
every colour, body + headers + footers + notes), re-selects every target and checks each output `w:rPr` is the source's
without the highlight — every other child unchanged, in order — and the change marker as plain/tracked demand; those
`w:rPr` are then stripped from the residual, which proves the rest of each part identical.
(`examples/specs/remove-highlighting.json`.)

## (b) CLAUDE.md lines

Both-sides definitions (add to "Other definitions that must stay identical on both sides"):

- **Comments** (`remove_comments`, built-in batch 2). A comment = a `w:comment` child of the comments part (the main
  part's `comments` relationship); its paraIds = the `w14:paraId` of its paragraphs (every `w:p` descendant). Its entry =
  the first `w15:commentEx` (direct child of `commentsExtended.xml`'s root, document order) whose `w15:paraId` is one of
  its paraIds; its parent = the comment owning the entry's `w15:paraIdParent` (none when no comment owns it, or itself);
  its root = parents followed, cycle-safe; resolved = the root's entry has `w15:done` in `1/true/on`. Selection: `all` |
  `resolved` (root resolved) | `authors` (author casefolded, whitespace collapsed, listed); then every reply below a
  selected comment, recursively. Removed: those `w:comment`; every `w15:commentEx` / `w16cid:commentId` (direct children)
  whose paraId is a removed comment's paraId; every `w16cex:commentExtensible` whose `durableId` such a `commentId`
  carried; in every other text part every `w:commentRangeStart`/`End`/`w:commentReference` with a removed id, and a
  reference's run when it then holds only `w:rPr`. Parts and relationships never removed. Refused: a mark in a text box
  (nested paragraph or inside `w:txbxContent`: `textbox`); duplicate comment ids, a missing id, a paraId two comments
  share, a reference outside a run (`structure`). Transform: `ops/comments.CommentBook` (`select`, `parent`, `root_of`,
  `resolved`), `side_removals`, `marks_of`, `in_text_box`; verifier: `verify/comments._VComments` (`chosen`, `parent`,
  `root_of`, `problems`), `_v_side_expected`. `tests/test_comments.py::test_the_transform_and_verifier_select_the_same_comments`
  compares the two selections on both fixtures for every `which`; planted defects there share a reply-blind thread
  reading, a misread `done`, a mark finder that misses range ends, side entries matched by one paragraph only.
- **Highlight targets** (`remove_highlight`). Every `w:r` whose nearest paragraph is in scope (`PartView.runs` /
  `selectors.runs_by_paragraph` — deleted runs, hyperlinks, text-box copies included) and every paragraph mark
  (`w:pPr/w:rPr`) whose OWN `w:rPr` has a direct `w:highlight` child with `w:val` in the colours (default: Word's 16
  colours; `none` never). Result: that element removed; plain — an emptied run `w:rPr` removed, an emptied mark `w:rPr`
  removed and then an emptied `w:pPr`; tracked — `w:rPrChange` (snapshot = the source rPr) appended, a target already
  carrying one refused (`tracked-format`). Transform: `ops/highlight.highlighted`, `strip_highlight`, `canonical_colors`;
  verifier: `verify/highlight._v_lit`, `_v_check`, `_v_colors`.

Rules / patterns worth recording:

- **An assertion carries the user's own words, not the transform's reading of them** (batch 2): `removed_highlight` gets
  `asked_colors` / `asked_scope` and `removed_comments` gets `asked_which` / `asked_authors` exactly as the spec gave them
  (None = the default), and the verifier applies its OWN defaults and normalisation — a colour, scope or default misread on
  the transform side reaches plan and apply alike but not the check (`test_a_colour_reading_that_takes_dark_yellow_for_yellow_is_caught`,
  `test_a_default_scope_that_forgets_the_headers_is_caught`, `test_a_default_that_reads_all_as_resolved_is_caught`).
  The plan's own reading (`colors`, `expected_ids`) is still asserted and compared.
- `allow_missing` defaults to **true** for both ops (coordinator's decision for batch-2 clean-up ops): a file with nothing
  to remove plans as a no-op with a "nothing to do" note; `false` keeps the `not-found` refusal.
- remove_comments touches: the comments part and each side part `(member, "*", "remove")`, `(part, str(bi), "text")` per
  paragraph with a mark, `(part, "comment-mark:<id>", "remove")` per mark between blocks. remove_highlight: `(part, bi,
  "rPr")` / `(part, bi, "markRPr")` — a font or `set_properties` change of the same paragraphs is a plan-time conflict.

## (c) FAILURE_EXPLANATIONS

No new refusal codes: both ops raise only `not-found`, `bad-params`, `structure`, `textbox`, `tracked-format` (and the
merge's `conflict`), all already in `report.FAILURE_EXPLANATIONS`. If the `textbox` / `structure` texts are worded for
other ops, these lines could be appended to them: `textbox` — "…or a comment whose mark sits in a text box (remove that
comment in Word)"; `structure` — "…or comments that share an id (Word would not know which mark is whose)".

## (d) COMMON_ASSUMPTIONS

```python
"remove_comments": [
    "'all' removes every comment; 'resolved' only threads Word marked resolved (a file from Word 2010 or older keeps no "
    "resolved state, so it has none); 'authors' the named authors' comments — a removed comment takes its replies",
    "the document's text is unchanged: only the comments and their marks in the text go; the comment parts stay (emptied "
    "if every comment goes), and a picture only a removed comment used stays in the file, unused",
    "a file with no comment to remove is left as it is (allow_missing false sets it aside instead)",
],
"remove_highlight": [
    "highlighting is Word's highlighter (w:highlight) — every colour unless the user named colours; paragraph and text "
    "shading are not highlighting and stay",
    "body, headers, footers, footnotes and endnotes; highlighted text inside pending tracked changes and text boxes is "
    "included; a style that highlights its text is not changed (flagged)",
    "plain formatting change unless the user asked for it tracked; a file without such highlighting is left as it is",
],
```

## (e) Words placed

None — both ops place no characters (`compiler.placed_words` needs no entry).

## (f) SYSTEM-prompt hints

- "Remove (the resolved / Jack's / all) comments" is `remove_comments` (`which` all | resolved | authors with
  `authors`); it never changes document text, so it is a formatting spec and can share a spec with other formatting
  ops — but a text edit of a paragraph that holds a comment mark is a separate spec (plan-time conflict).
- "Take the highlighting off" is `remove_highlight`; name `colors` only when the user named colours (Word's names:
  yellow, green, cyan, magenta, blue, red, darkBlue, darkCyan, darkGreen, darkMagenta, darkRed, darkYellow, darkGray,
  lightGray, black, white). Shading is not highlighting. A font or set_properties change of the same text is a second spec.
- Both leave a file with nothing to remove as it is; set `allow_missing: false` only when the user wants such files
  reported.

## (g) Departures from the design note

1. `allow_missing` defaults to **true** for both ops (coordinator's decision), not false; §2/§3 should say so.
2. The assertions carry the raw parameters (`asked_*`) and the verifier applies its own defaults (rule above) — stricter
   than the design note says.
3. Highlight colours in a spec must be Word's exact names (the params schema's enum, which documents them for Claude);
   `canonical_colors` itself folds case for direct callers. §3's "any case" is true of the op function, not of a spec.
4. A mark inside `w:txbxContent` but outside any nested paragraph is also refused as `textbox` (the design note only
   named nested paragraphs).
5. Known failing test until integration: `tests/test_planner.py::test_catalog_and_schema_cover_every_op` requires
   `catalog.COMMON_ASSUMPTIONS` for every op — add the lines in (d) (a shared file, left to the coordinator).
