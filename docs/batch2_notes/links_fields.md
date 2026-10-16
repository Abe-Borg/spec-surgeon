# Batch 2 notes — `remove_hyperlinks` (item 18) and `unlink_fields` (item 19)

Builder E's notes for the coordinator: the text to merge into README.md, CLAUDE.md, `report.FAILURE_EXPLANATIONS`,
`catalog.COMMON_ASSUMPTIONS`, `compiler.placed_words` and the SYSTEM prompt, and the departures from
`docs/BUILTIN_OPS_BATCH2_DESIGN.md` §9–§10. Code: `src/spec_surgeon/ops/links.py`, `src/spec_surgeon/verify/links.py`;
tests: `tests/test_links.py` (fixtures `tests/fx_links.py`); examples: `examples/specs/remove-hyperlinks.json`,
`examples/specs/unlink-docproperty-fields.json`.

## (a) README text

#### `remove_hyperlinks` — the links come out, the words stay

`which`: `external` (the default — web addresses and e-mail: a `w:hyperlink` with a relationship, a `HYPERLINK` field
without the `\l` switch), `internal` (links to a place in the document — a bookmark, a table of contents' entries: a
`w:hyperlink` with only an anchor, a `HYPERLINK \l` field) or `all`; `keep_style` (default false); `scope` (default the
body, headers, footers, footnotes and endnotes); `allow_missing` (default **true**).

* A link element is replaced by what it holds — the same runs, in the same place, with their own formatting; a link field
  is turned into the text it shows (`unlink_fields`' rule below). Not one visible word changes.
* The linked words lose the **Hyperlink** (and **FollowedHyperlink**) character style — Word's built-in names, whatever
  the style's id — so they look like the text around them; `keep_style: true` keeps the blue underlined look. Words that
  carry their own blue or underline (direct formatting, not the style) keep it and are flagged.
* An address no link uses any more **leaves the file**: its relationship is removed from the part's relationships. An
  address something else still uses — a picture's click link, a link that stays (another `which`) — stays (noted).
  A picture's own click link is not a text link and is never removed.
* A link inside a text box (Word keeps two copies of it) sets the file aside (`textbox`); so does a link field that runs
  over two paragraphs (`field-span`), sits inside another field's code (`nested-field`) or lies in a pending tracked
  change (`existing-revisions`). A link inside a pending insertion is unwrapped inside it (the insertion stays). Links a
  table of contents shows are removed when asked (`internal`/`all`) and flagged: Word puts them back when the table of
  contents is updated.
* A file with no such link is left exactly as it is, with a note ("nothing to do") — this is a clean-up run over whole
  batches; `allow_missing: false` sets such a file aside instead (`not-found`).
* Layout reference `source`: removing a link moves nothing (the render must match the source's).

V10 (`removed_hyperlinks`): the links found again with the verifier's own code (its own reading of `\l`, of the link
style, of a field's result), every changed paragraph checked item by item — the link gone from every container chain,
the link style gone from exactly the linked runs, the field's code and characters gone, every other item where it was —
and the relationships part compared with the source minus exactly the addresses that should go.
(`examples/specs/remove-hyperlinks.json`.)

```json
{"op": "remove_hyperlinks", "params": {"which": "external"}}
```

#### `unlink_fields` — fields become the text they show

`types`: the field types to convert — `["DOCPROPERTY"]`, `["DOCPROPERTY", "REF", "DATE"]` (any case) — or `"all"`
(every type but the page numbers); `scope` (default the body, headers, footers, footnotes and endnotes);
`allow_missing` (default **true**).

* What each field shows now stays, with its formatting; the field code and the field characters go (a simple field is
  replaced by its result). Word no longer updates the text — from the document properties, a bookmark, the date.
* **Page numbers are never converted**: PAGE, NUMPAGES, SECTIONPAGES and PAGEREF are refused when named (`bad-params`)
  and skipped by `"all"` — they would freeze at the page they showed when the file was saved.
* Refused (the file is set aside): a field inside a text box (`textbox`), one that runs over more than one paragraph — a
  table of contents, an index (`field-span`), one whose code holds another field or that sits inside another field's
  code (`nested-field`: converting it would change what the outer field does), a form field someone fills in
  (`form-field`), one whose code or characters lie in a pending tracked change (`existing-revisions`).
* A field that shows nothing (an index entry `XE`, a property with no value) is removed whole — flagged. A DATE, TIME,
  CREATEDATE, SAVEDATE or PRINTDATE field keeps the date it showed when the file was last saved (noted). A field inside
  what another field shows (a DOCPROPERTY inside a link field's text) is converted on its own and flagged: Word puts it
  back when the outer field is updated, unless that one is converted too.
* A file with no field of those types is left exactly as it is, with a note; `allow_missing: false` sets it aside.
* Layout reference `none`: the source's render may show a recomputed field (LibreOffice recomputes some), the output
  shows the saved text.

V10 (`unlinked_fields`): the fields parsed again with the verifier's own parser (`verify/fields.py`), every changed
paragraph checked item by item — the begin, separate and end characters and the field's own code gone, a simple field
unwrapped, the result and everything else where it was — and the plan's list of fields per paragraph compared.
(`examples/specs/unlink-docproperty-fields.json`.)

```json
{"op": "unlink_fields", "params": {"types": ["DOCPROPERTY"]}}
```

## (b) CLAUDE.md lines (both-sides definitions)

- **Links (`remove_hyperlinks`, batch 2).** Link element = `w:hyperlink`: external with an `r:id`, internal without.
  Link field = a field (the batch-2 field definition) of type HYPERLINK: internal when its instruction holds the switch
  `\l` — an unquoted token, any case; tokens are `"…"` (an unterminated quote runs to the end) or runs of other non-space
  characters, a switch an unquoted token starting with `\`. Link style = a character style whose `w:name` is Hyperlink
  or FollowedHyperlink (casefolded). The runs of a link element: its `w:r` whose nearest `w:p` is the link's paragraph;
  of a link field: its *result runs* — the paragraph's runs holding a child (not `w:rPr`) after the field's `separate`
  and before its `end` in document order (a simple field: the runs in it). Removed addresses: hyperlink-type
  relationships a removed link element named that no other element of the part names (any attribute in the r:
  namespace). Transform: `ops/links.instruction_switches/field_is_internal/link_style_ids/link_runs/result_runs/
  addresses_to_remove`; verifier: `verify/links.v_switches/v_field_internal/v_link_styles/_v_link_runs/v_result_runs`
  (+ the inline rule in `_removed_hyperlinks`). `tests/test_links.py` compares the switch readers on 600 random
  instructions, the styles, result runs and field classification on the fixtures, and plants shared defects (every
  field read as external, another style taken for the link style, an address still in use planned away, the field
  result left out of the restyle).
- **Unlinking fields (`unlink_fields`, also `remove_hyperlinks` for link fields).** `types` upper-cased, `"all"` = every
  type but PAGE/NUMPAGES/SECTIONPAGES/PAGEREF (never converted; refused when named). A selected field is refused when
  inside a text box (`textbox`), then by `Field.refusal`/`VField.refusal` (`field-span`, `nested-field`, `form-field`,
  `existing-revisions`), then when it sits in another field's code (`nested-field`). Transform: `ops/links.
  normalize_types/type_selected/_field_refusal`, `ops/fields.unlink`; verifier: `verify/links.v_types/_v_refusal`.
- **Locating at apply** (both link and field ops): the plan stores, per paragraph, the place of each chosen link and
  field within the paragraph (`paragraph_links`/`paragraph_fields`) with its identity (`r:id`/anchor, instruction);
  apply finds them again in the working copy by paragraph and refuses (`internal`) if they moved. Never whole-part
  ordinals: an earlier op in the same spec (the other of the two, unlinking HYPERLINK fields) shifts them.
- Rule 9 touches: `(part, "scope", "verify")` for every part in scope (the kinds re-derive over each), `(part, block,
  "text")` per changed paragraph, `(rels member, "*", "rels")` when an address goes. So `remove_header_footer` beside
  either op over the headers is a plan-time conflict, and the two ops on one link field too.

## (c) FAILURE_EXPLANATIONS entries (new codes)

```python
"field-span": (
    "Field Runs Over Several Paragraphs",
    "The field to convert or remove begins in one paragraph and ends in another (a table of contents, an index) or "
    "never ends. It is turned into text only inside one paragraph; update or unlink it in Word (select it, "
    "Ctrl+Shift+F9).",
),
"nested-field": (
    "Field Inside Another Field's Code",
    "The field holds another field in its code, or sits inside another field's code (an IF or a formula built from it). "
    "Turning one of them into text would change what the other does; convert the outer field in Word.",
),
"form-field": (
    "Form Field",
    "The field is a form field someone fills in (a text box, check box or drop-down of a protected form); turning it "
    "into text would remove the control. Leave it, or convert it in Word.",
),
```

Existing codes these ops also raise, whose current text is written for paragraph deletion and could be widened:
`existing-revisions` ("…or a field whose code or characters lie in someone's pending tracked change: accept or
reject it first"), `textbox` ("…a link or field inside a text box: Word keeps two copies of it"), `not-found` (only
with `allow_missing: false`).

## (d) COMMON_ASSUMPTIONS

```python
"remove_hyperlinks": [
    "external links (web addresses, e-mail) only unless the user said internal links or all — links to a place in the "
    "document (bookmarks, a table of contents) stay",
    "the linked words stay exactly and lose the blue underlined Hyperlink style (keep_style keeps it); words with their "
    "own blue or underline keep it (flagged); a picture's click link stays",
    "an address no link uses any more is removed from the file",
    "a link in a text box, or a link field over two paragraphs or in a pending tracked change, sets the file aside; a file "
    "with no such link is left as it is",
],
"unlink_fields": [
    "only the named field types (or all but the page numbers) become the text they show now, with its formatting; Word no "
    "longer updates that text",
    "page numbers (PAGE, NUMPAGES, SECTIONPAGES, PAGEREF) are never converted",
    "a field over two paragraphs (a table of contents), inside another field's code, a form field or one in a pending "
    "tracked change sets the file aside; a field that shows nothing is removed (flagged); a file with no such field is "
    "left as it is",
],
```

## (e) Words placed

None — both ops place no characters (mode `formatting`). `compiler.placed_words` needs no entry.

## (f) SYSTEM-prompt hints

- "Remove (the) hyperlinks / links but keep the text" is `remove_hyperlinks` (`which` external by default; "all links",
  "including the table of contents" → `all`/`internal`). It keeps the words and takes the link style off them unless the
  user wants them to keep looking like links (`keep_style`).
- "Convert / unlink / freeze the DOCPROPERTY (or REF, DATE …) fields to plain text" is `unlink_fields` with those
  `types` (the profiles' header/footer `fields` list the types in use); "all fields" is `"all"` — page numbers never.
  A table of contents cannot be converted (it spans paragraphs). Both ops leave a file without such links or fields as
  it is (`allow_missing` true by default).

## (g) Departures from the design note (§9, §10)

1. **`allow_missing` defaults to true** for both ops (the coordinator's decision): a file with nothing to remove plans
   as a no-op with a note; `allow_missing: false` keeps the `not-found` refusal. Both tested.
2. **`nested-field` also when a selected field sits inside another field's code** (e.g. a MERGEFIELD in an IF's
   condition, a HYPERLINK in an IF): converting it would change the outer field's instruction. For both ops.
3. **`textbox` is checked before `Field.refusal`** for fields (the shared parser says `field-span` for a field in a text
   box; the ops say `textbox`, the clearer reason). A link element outside any paragraph is `structure`.
4. **Links and fields are found again at apply by paragraph** (place within the paragraph + identity), not by ordinals
   over the part: whole-part ordinals broke when `remove_hyperlinks` and `unlink_fields` ran in one spec (caught by the
   apply-time identity check). Composition in both orders is tested.
5. **A tokenizer detail**: an unterminated double quote quotes to the end of the instruction (both sides).
6. **Flags not in the note**: a link or link field that sits in what another (not converted) field shows — a table of
   contents' entries — is flagged ("Word puts it back when that field is updated"); so is a field converted inside an
   unconverted field's result.
7. Only hyperlink-type relationships are removed (a relationship of another type a `w:hyperlink` names stays, noted).

## Shared-file note

`tests/test_planner.py::test_catalog_and_schema_cover_every_op` fails in this worktree until `catalog.COMMON_ASSUMPTIONS`
gets the entries in (d) — `catalog.py` is a shared file the coordinator edits. No shared file was changed.
