# Batch 2 notes — `set_document_properties` and `set_document_state` (items 20, 21)

Builder F's notes for the integration: README text, CLAUDE.md lines, explanations, assumptions, words placed, hints for
Claude, and the departures from `docs/BUILTIN_OPS_BATCH2_DESIGN.md`. Code: `ops/docprops.py` (transform),
`verify/docprops.py` (kinds `document_properties`, `document_state`); tests: `tests/test_docprops.py` (54);
worked examples: `examples/specs/set-title-clear-authors.json`, `examples/specs/mark-final-tracking-off.json`.

## (a) README text

#### `set_document_properties` — title, company, custom properties; clear the author names

The method used to say *the document properties are Word's — never touch them*. At your request this operation now
changes them — **only when you ask for it by name**, and only the properties you name; every one of them is checked
afterwards, and everything else in those parts must be exactly as it was.

* `set` — `title`, `subject`, `author`, `keywords`, `comments`, `category`, `status`, `last_modified_by`, `company`,
  `manager`, each to your exact words (one line, at most 255 characters, no character you cannot see). Word keeps the
  first eight in `docProps/core.xml` and company and manager in `docProps/app.xml`.
* `clear` — the same names; the property is removed. "Clear the author names" is `"clear": ["author",
  "last_modified_by"]`. A property already empty is already clear. Tracked changes and comments keep their authors'
  names — the plan says so when a file has any (`resolve_tracked_changes` and `remove_comments` deal with those).
* `custom` — custom text properties (*File → Info → Properties → Advanced → Custom*), `{"Project Number": "1003"}`;
  an existing one keeps its place and takes the value (names are matched without regard to case, as Word does), a new
  one is added. A file without custom properties gets a `docProps/custom.xml` holding exactly the ones you asked for.
  A custom property that holds a number, a date or yes/no is refused (`property-type`) — change it in Word.
* `clear_custom` — custom properties to remove.
* A value already as you asked changes nothing.

```json
{"op": "set_document_properties",
 "params": {"set": {"title": "Wet-Pipe Sprinkler Systems", "company": "ACME Fire Protection"},
            "clear": ["author", "last_modified_by"],
            "custom": {"Project Number": "1003"}}}
```

**Fields that show a property** — a TITLE field, `DOCPROPERTY Title`, `DOCPROPERTY "Project Number"` in a header —
keep the value they showed when the file was last saved until Word updates fields (F9, or printing with *Update
fields*). The plan flags every one of them. LibreOffice draws such a field with the new value at once, so the preview
shows the new title; for a file with such a field the plan drops the page-layout reference (V7) with that reason.
Mode: `untracked` when words are set (your words go into the file), `formatting` when only clearing. V10
(`document_properties`) reads every asked property again with its own code — the exact text, an absent element for a
cleared one, the next pid and Word's property-set id for a new custom property — and compares everything else in
`core.xml`, `app.xml` and `custom.xml` element for element (the member residual). A created `custom.xml` is compared
whole with what it must hold, and the package relationships and `[Content_Types].xml` with the source plus exactly its
one relationship and its one override. (`examples/specs/set-title-clear-authors.json`.)

#### `set_document_state` — Track Changes off (or on), Mark as Final

The method used to say *Track Changes is your choice — never toggle it*. This operation toggles it — **only when you
ask** — and marks files as final:

* `"track_changes": "off"` removes the setting Word opens a file with (`w:trackRevisions`); `"on"` adds it, in the
  place Word's schema gives it in `settings.xml`. A file whose tracking is **locked with a password** (*Review → Track
  Changes → Lock Tracking*) is refused (`protected`): unlock it in Word first. Pending tracked changes are not touched
  — accepting or rejecting them is `resolve_tracked_changes`.
* `"final": true` does what Word's *File → Info → Protect Document → Mark as Final* does: the custom property
  `_MarkAsFinal` (yes) and the Status property `Final`; Word then opens the file read-only with a *Marked as Final* bar
  (anyone can click *Edit Anyway* — it is a signal, not protection). A file without custom properties gets a
  `docProps/custom.xml` holding just that one. `"final": false` takes both markers off (a status other than *Final* is
  yours and stays).

```json
{"op": "set_document_state", "params": {"track_changes": "off", "final": true}}
```

Mode `formatting` (no words of yours go in). V10 (`document_state`): the setting re-read — none left for off, exactly
`<w:trackRevisions/>` for on, and an added one in its place in the schema's order (checked against the bundled schema
itself: V5a cannot judge the order of `settings.xml`, see the note below) — and the two Mark-as-Final markers; the
member residual compares everything else in `settings.xml`, `core.xml` and `custom.xml`.
(`examples/specs/mark-final-tracking-off.json`.)

Both operations are refused in one spec when they would touch the same property (the Status, say) or both add custom
properties (they would take the same new pid) — run them as two specs or chain stages.

## (b) CLAUDE.md lines

Under "Other definitions that must stay identical on both sides" (built-in batch 2):

- **Document properties** (`set_document_properties`, `set_document_state`). Core (the package's `core-properties`
  relationship): `title` dc:title, `subject` dc:subject, `author` dc:creator, `keywords` cp:keywords, `comments`
  dc:description, `category` cp:category, `status` cp:contentStatus, `last_modified_by` cp:lastModifiedBy; app
  (`extended-properties`): `company` Company, `manager` Manager. Set: the top-level element's children go, its text is
  the value, its attributes stay; a missing one is created at the end of the root (xsd:all); clear: removed — absent
  or empty (no text, no child elements) is already clear. Custom (`custom-properties`): matched by name casefolded;
  an existing text property (`vt:lpwstr`/`vt:lpstr`) keeps its value tag; another type → `property-type`; a new one
  is appended with fmtid `{D5CDD505-2E9C-101B-9397-08002B2CF9AE}`, pid = the source's largest pid (at least 1) + 1, +1
  per earlier new one in the request's order, `vt:lpwstr`. Mark as Final = custom `_MarkAsFinal` `<vt:bool>true</vt:bool>`
  + `cp:contentStatus` `Final`; unmarking removes `_MarkAsFinal` and a `Final` status only. Track Changes: off removes
  `w:trackRevisions` (on unless w:val is 0/false/off); on inserts `<w:trackRevisions/>` in CT_Settings order or removes
  an off one's w:val; a lock (`w:documentProtection w:edit="trackedChanges"`, enforcement 1/true/on) refuses off
  (`protected`). Transform: `ops/docprops.PROPERTIES`, `Changes`, `CustomProps`, `SETTINGS_ORDER` (the transform's
  table); verifier: `verify/docprops._V_PROPERTIES`, `_Check`, `_settings_order()` (read from the bundled
  `wml.xsd` itself). `tests/test_docprops.py::test_the_property_tables_and_the_settings_order_agree` pins the tables
  equal; the planted-defect tests share a wrong table, fmtid, schema order, Final marker and on/off reading between plan
  and apply.

Rule 10 (add, as its third case): `set_document_properties` / `set_document_state` may add **`docProps/custom.xml`**
— only that member, only when the file has no custom-properties part, holding only the asked properties, with its
package relationship (`_rels/.rels`, Target `docProps/custom.xml`) and its content-type override — predicted in
`members_added`; V10 compares the created part whole with the verifier's own expected part and the package rels and
`[Content_Types].xml` with the source plus exactly those entries (`verified_members`). A `docProps/custom.xml` member no
relationship names is refused (`malformed-properties`), never overwritten.

Rule 9 (touches): `(member, "property:<local name or casefolded custom name>", "value")` per changed property;
`(settings, "trackRevisions", "value")`; adding new custom properties to an existing part also touches
`(custom part, "pid", "add")` (two ops would take the same pids); creating `custom.xml` touches it, `_rels/.rels` and
`[Content_Types].xml` as whole members.

The V5a gap (recorded, not fixed here — `verify/xsd.py` is shared): `_validate` drops every message that contains
"hyphenationZone". libxml2's "This element is not expected. Expected is one of (…)" for a `settings.xml` element out
of CT_Settings order lists every element still allowed — `hyphenationZone` among them whenever the misplaced element
sits before it — so **V5a never reports an out-of-order settings element before `hyphenationZone`** (and stops
validating that part's later content there, hiding their errors too). `document_state` therefore checks the place of an
inserted `w:trackRevisions` itself (`verify/docprops._in_order`). Narrowing the filter to messages that start with
`Element '{…}hyphenationZone'` would close it, with one consequence to weigh: fixing an order error can then unmask a
latent error the source's validation never reached (python-docx's default `<w:zoom w:val="bestFit"/>` lacks the
required `w:percent`; `make_fixtures.build_rich` puts `w:trackRevisions` first) and fail V5a on a correct output.

## (c) FAILURE_EXPLANATIONS entries

```python
"no-part": (
    "The File Has No Such Properties Part",
    "The file has no document-properties part (or no settings part) for what was asked — another program wrote it "
    "without one. Open it in Word, save it, and run again.",
),
"protected": (
    "Track Changes Is Locked",
    "Track Changes is locked in this file with a password (Review → Track Changes → Lock Tracking), so it cannot be "
    "turned off here. Unlock it in Word, then run again.",
),
"property-type": (
    "A Custom Property Is Not Text",
    "The custom property to set holds a number, a date or a yes/no value in this file (or Mark as Final's property "
    "is not a yes/no), so a text value cannot replace it. Change it in Word (File → Info → Properties → Advanced → "
    "Custom), or leave it out.",
),
"malformed-properties": (
    "The File's Properties Part Is Irregular",
    "This file's document properties or settings hold something Word itself does not write — one property twice, two "
    "custom properties with the same name, an unreadable property number, or a custom-properties file nothing refers "
    "to. Open the file in Word, save it, and run again.",
),
```

## (d) COMMON_ASSUMPTIONS

```python
"set_document_properties": [
    "only the named properties change, to the user's exact words; a property already as asked is left alone",
    "'clear the author names' means Author and Last Modified By; tracked changes and comments keep their authors' names "
    "(flagged when the file has any)",
    "fields that show a changed property (TITLE, DOCPROPERTY …) keep their saved value until Word updates fields "
    "(flagged); custom properties are matched by name without case; a file without custom properties gets them",
    "a custom property that holds a number, a date or yes/no in a file sets that file aside",
],
"set_document_state": [
    "track changes off/on is the setting the file opens with — pending tracked changes are neither accepted nor "
    "rejected; a file whose tracking is locked with a password is set aside",
    "Mark as Final sets the custom property _MarkAsFinal and the Status 'Final', as Word does; unmarking removes both "
    "(another Status stays)",
],
```

## (e) Words placed (`compiler.placed_words`)

```python
if op == "set_document_properties":
    for key in ("set", "custom"):
        if isinstance(params.get(key), dict):
            out += [v for v in params[key].values() if isinstance(v, str) and v]
```

(`set_document_state` places none: `Final` and `true` are Word's own markers, not the user's words. Custom property
NAMES are labels, not document text — not placed words.)

## (f) SYSTEM-prompt hints

- Document properties (title, subject, author, company, manager, status, custom properties) are
  set_document_properties, and Track Changes on/off and Mark as Final are set_document_state — ONLY when the user asks
  for them; never add them to another request. Every value is the user's exact words. "Clear the author names" is
  clear ["author", "last_modified_by"]. Say in assumptions that fields showing a changed property keep their saved
  value until Word updates fields, and that pending tracked changes are not accepted by turning Track Changes off.
- Do not set `review.geometry_reference` for set_document_properties: a file with a field showing a changed property
  plans with `none`, the others with `source`.
- set_document_properties and set_document_state on the same property (the Status with Mark as Final), or both adding
  custom properties, are two specs.

## (g) Departures from the design note

1. **Layout reference per file**: `source`, but `none` for a file where a field (any text part) shows a property this op
   changes — LibreOffice draws TITLE / DOCPROPERTY fields with the live value (checked: the source render shows the old
   title, the output's the new), so V7 would report the words beside them as moved. The worked examples therefore leave
   `review.geometry_reference` out.
2. **Empty means clear**: clearing a property whose element is empty (python-docx's `<cp:category/>`,
   `<cp:lastModifiedBy/>`) changes nothing — Word shows it as no value.
3. **Two new codes**: `property-type` (a custom property of another type — file-specific, so never `bad-params`; the
   coordinator's correction) and `malformed-properties` (one property twice, two custom properties with one name, an
   unreadable pid, a custom part without relationship, two `w:trackRevisions`) — the existing `structure` explanation
   speaks of paragraph content and would mislead.
4. **Custom names** are matched casefolded (Word's own rule); an existing property keeps its stored name and value tag
   (`vt:lpstr` stays `vt:lpstr`).
5. **Keywords** (`cp:keywords`, mixed content): setting them replaces any `cp:value` children by the text.
6. **The settings order** is checked by the verifier itself (above), not left to V5a.
7. `set_document_state` refuses `track_changes: "off"` on a locked file even when tracking is already off (the lock is
   checked first, on both sides).
