# The Specification Formatter's modes in Default mode (N1–N7)

The owner's decision (2026-10-29): the work of the Specification Formatter (`Abe-Borg/spec-template-normalizer`)
comes into Spec Surgeon, rebuilt under Spec Surgeon's invariants, not copied. Its code is not moved: it edits XML as
text, verifies inside the engine and pins another SDK, and each of those breaks an invariant here. What comes across
is what it does for the user, op by op, in **Default mode** (and Obliterate, which has the same conversation).
**Safe mode is not touched**: no button, form or reading of Safe mode changes; where a change reaches an op or a
reader Safe mode also uses, it is an optional parameter whose default is today's behaviour, and Safe mode's own tests
pin that.

| The Formatter's mode | In Spec Surgeon |
| --- | --- |
| Format only: the architect's role styles, page, headers and footers on a consultant's specification | the ops that exist (`apply_style`, `map_styles`, `set_page_setup`, `apply_header_footer_template`, `convert_numbering`), the template's role styles from its own reading, and the paragraphs the structure reader cannot place given a role by N1's classifier through N2's selector; N3 lets the header template carry the even/odd page switch |
| CSI to Canadian, with a template | `convert_numbering` (automatic numbers) or `convert_typed_numbers` with N6 (typed CSI numbers) onto the template's list, every number's counters kept |
| CSI to Canadian, no template | the same, onto the bundled CSC PageFormat scheme the user picks in the chat (N5) |
| Canadian back to CSI | `convert_numbering` onto the bundled CSI scheme (N5) or the template's list, read by N4: automatic numbers stay automatic; **no literal marker is written** |
| The architect's theme and compatibility settings | not carried: the theme is out of reach by decision, and so are the compatibility settings; the assistant says so (catalog assumption) and the theme's visible effect on the imported styles is pinned as resolved fonts, as the header template and the outline already do |

**Literal CSI markers** (the Formatter's `canadian_to_csi`, writing `PART 1`, `1.1`, `A.` into the text) are not
built: they would be the first characters the engine types that no user source supplied (invariant 6), and typed
numbers go stale where automatic ones renumber. Moving the list onto a CSI list shows the same numbers. If a real job
needs typed numbers, that op gets its own design here first.

**A typed Canadian number** (`.1` typed at the start of a paragraph) carries no depth in its shape: `.1` is the same at
every level below the article. Such a document is left unresolved by the reader (N4) and refused by the typed-number
conversion; PageFormat documents are automatic in practice.

## N1 — the outline tool: the structure reader, and Claude's reading of what it leaves

A chat tool, `read_outline(file)`, in the conversation's tools (Default and Obliterate; Safe mode has no chat).

* **The reader first.** `structure.analyze_structure(pkg, canadian=True)` over the file as the batch has it
  (unmodified bytes). Its classified paragraphs are final: nothing below overrides one. Excluded paragraphs (tables,
  hidden text, structural redlines, empty) are never sent and never get a role. One exception, the Formatter's lesson
  that a deterministic rule must be right before it is final (`outline.demote_unshaped`): a TYPED number the reader
  read as a PART or an article stands only when the rest of the line is shaped like a heading (empty, or starting with
  a capital or a digit, not ending like a sentence, at most `HEADING_WORDS` words), and a section line naming a second
  SECTION is a cross-reference; those go to Claude to decide. "1.5 times the pipe diameter shall be maintained." and
  "PART 1 of the Contract Documents shall govern." are its close negatives. Automatic numbering is never demoted, and
  the reader itself, which Safe mode uses, is unchanged.
* **Claude's reading of the rest.** The paragraphs the reader leaves `unresolved` go to one structured call
  (`outline.classify`), in windows of at most `OUTLINE_CHUNK` paragraphs to decide, each with the paragraphs within
  `OUTLINE_CONTEXT` of it as context (their reader role shown, never decided again) and the outline above restated at
  each gap, so a window's size never grows with how far apart its paragraphs lie. The system
  prompt and the one tool (`submit_roles`) are constants, byte-stable and cached; the call is `one_shot`, on the
  conversation's model, at `resources.OUTLINE_EFFORT` (`high`, as outcome planning), inside the request's spending
  check (`budget_scope` is already open), stopped by Stop (`cancellation_scope`). The prompt carries the Formatter's
  lessons: automatic numbering is stronger evidence than text; a cross-reference ("as specified in Section 23 05
  00", "PART 1 of the Contract Documents shall govern") is not a heading; a heading-shaped sentence is not a heading;
  a Roman numeral and a letter are told apart by the previous sibling; editorial notes, specifier notes and prose
  that is no part of the outline are `none`, with a reason.
* **Coverage.** The answer names every paragraph it was asked about exactly once, each a role from
  `structure.ROLES` or `none` with a reason. A window whose answer misses, repeats or invents a paragraph, or names a
  role outside the vocabulary, is asked again once (`OUTLINE_RETRIES`); still wrong, its paragraphs stay unplaced and
  the result says why. An answer about any paragraph that was not asked is dropped.
* **Bounded.** A file with more than `OUTLINE_MAX` paragraphs to decide is not sent: the tool says so and the reader's
  result stands alone.
* **What the chat model gets back**: per role, the paragraph indices the reader placed and the ones Claude proposes,
  and one `indices` selector (N2) covering both; the paragraphs left unplaced with why; and a note that a proposal is
  Claude's reading, which the plan's summary names and the preview shows, approved only with the plan. A role is a
  selection, never wording: invariant 6 holds.
* **Paid once.** Results are kept per file content digest for the conversation's batch: asking again costs nothing.
  The call's usage joins the conversation's usage; its exchange is kept in the conversation's record without
  document images (there are none).
* **No call** when the reader leaves nothing to decide (a clean automatic document, Canadian ones included).

## N2 — the `indices` paragraph selector

`{"paragraphs": {"indices": [i, j, …]}}`: the paragraphs whose ordinals in the part (the same count as
`index_range`'s and `ooxml.paragraphs`') are listed; distinct whole numbers ≥ 0, at most `MAX_INDICES`, AND-ed with
the other keys like any. Positional like `index_range` (its touches read where paragraphs are). Written twice:
`ops/selectors.py` and the verifier's own reading in `verify/props.py`; a planted divergence is caught. `prose`
describes it as "N chosen paragraphs" with the first few numbers, and the review table names it.

## N3 — the header template carries the even/odd page switch, when asked

`apply_header_footer_template` gains `even_odd`: `"refuse"` (default, today's behaviour: a document whose
`w:evenAndOddHeaders` differs from the template's is set aside, code `even-odd`) or `"template"`: the document takes
the template's switch with its headers and footers. Only with both kinds (headers and footers) templated — one kind
alone would leave the other kind's even pages empty or dormant (refused, `even-odd`); only where the document relates a
settings part (none: refused). Written in the settings part the document relates, at its place in CT_Settings
(`docprops.SETTINGS_ORDER`); turned off by removing it. Predicted (the settings member, the switch's value) and
checked by the verifier's own reading of both settings parts (`verify/template.py`); the rest of the settings part is
V10's member residual. The op's sentence says it ("and the even-page switch is turned on/off as the template has it").

## N4 — the structure reader reads Canadian PageFormat numbering

`analyze_structure(pkg, canadian=False)` and `role_summary(pkg, canadian=False)`; only the outline tool passes
`True` (the profile, Safe mode's buttons and `template_roles` keep today's reading). With it, an automatically
numbered paragraph whose rendered marker is a dot and a number (`.1`, PageFormat's paragraph and subparagraph levels)
takes the depth one below the nearest earlier placed paragraph of the same list at the level above (its list level
less one): `paragraph` under an article, `subparagraph` under it, and so on to `subparagraph_4`; deeper, or without
such a parent, it stays unresolved with the reason. `PART 1` and `1.1` are read as today. A typed `.1` stays
unresolved with its own reason (the note on typed Canadian numbers, above): it carries no depth.

## N5 — the bundled schemes, picked in the chat

Two example files ship with the app (`examples/schemes/`), built byte-stable by `tests/make_scheme_examples.py` like
the tour's showcase, each with one numbered paragraph style per level on one list and a paragraph of each:

* **CSC PageFormat** — the Formatter's built-in nine levels: `PART %1`, `%1.%2`, `.%3` … `.%9`, every level decimal,
  starting at 1, no restart override.
* **CSI** — `PART %1`, `%1.%2`, `%3.` (A), `%4.` (1), `%5.` (a), `%6)` (1), `%7)` (a), `(%8)` (1), `(%9)` (a): the
  Formatter's CSI markers.

The assistant offers one through `ask_user` as a choice of kind `scheme` (`scheme: "csc" | "csi"`), whose label the
app writes ("Use the built-in CSC PageFormat scheme as the template"). It is an app choice (`APP_CHOICE_KINDS`):
picking it in the window copies the file into the scratch folder, attaches it as the template (replacing any template,
which the choice's detail says) and plans the request again with the decisions so far. The scheme's numbering text
then comes from a file the user attached by their pick: the ops and the gate (`_attached_checks`) are unchanged.

## N6 — typed CSI numbers onto another list

`convert_typed_numbers` gains `typed_as: "csi"` (with `match: "counters"` only): each candidate's typed number is read
by the CSI shape of its level (`PART n`; `n.n` or `n.0n`; `A.`; `1.`; `a.`; `1)`; `a)`; `(1)`; `(a)` for scheme levels
0–8) instead of the destination list's own shape, and converts when the values its shape names are the destination
list's predicted counters at that place: typed `A.` becomes PageFormat's `.1`, typed `1.01` its `1.1`. A paragraph
whose typed number reads at two levels is refused (`ambiguous`), as today. Everything else in the op holds: characters
are only removed, every other counted paragraph reads exactly as before, and the verifier reads the CSI shapes with its
own code (`verify/typednumbers.py`).

## N7 — the four modes as requests

Worked EditSpecs in `examples/specs/` (CSI to Canadian with a template and with the bundled scheme, Canadian to CSI,
formatting to a template with the outline tool's selectors), the assistant's guidance (the catalog's assumptions and
the system prompt's few lines on the outline tool and the schemes), description cases, and end-to-end tests on
synthetic CSI and Canadian documents in both directions.

## Acceptance

On the owner's machine, with Word: a consultant's CSI specification formatted to a real architect template (its
roles from the reader and the outline tool, the template's headers with its even/odd switch); a CSI batch converted to
CSC PageFormat with an architect's Canadian template and with the bundled scheme; a PageFormat document converted
back to CSI. Every copy opens in Word without a repair prompt and its numbers read as the source's counters.
