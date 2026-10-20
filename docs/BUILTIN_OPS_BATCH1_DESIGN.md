# Built-in operations, batch 1 — design note

2026-10-16. Abraham's request: ten prebuilt operations, the first batch of several ("let's do it in manageable chunks").
Status: **built** on branch `claude/bold-cori-xrt4yw` — all ten items, with their tests (963 in the suite once merged with master) — decisions below
were taken on Abraham's behalf, as in earlier rounds, and can each be changed. Reading order: CLAUDE.md hard rules 1–4, 8–13, the "Block-text definition", "Slots" and "Fill
rules"; `docs/SET_PROPERTIES_DESIGN.md` for the shape of a verifier-first op.

| # | Asked for | Built as | Mode |
|---|---|---|---|
| 1 | Fill project variables from a per-project table | `fill_variables` | content (tracked) / untracked |
| 2 | Replacement table, typed or attached, tracked or not | `replace_table` | content / untracked |
| 3 | Resolve MasterSpec `[option] [option]` choices | `resolve_options` | content / untracked |
| 4 | Keep or delete paragraphs tagged for a system type | `filter_tagged_paragraphs` | content / untracked |
| 5 | Update the issue stamp wherever it sits | `set_issue_stamp` | content / untracked |
| 6 | Every file ends with "END OF SECTION xx xx xx" | `ensure_end_of_section` | content / untracked |
| 7 | Header/footer, title line and file name agree | `make_values_agree` | content / untracked |
| 8 | Swap a logo image in every header, fitted | `swap_image` | formatting |
| 9 | Snippet library: insert a saved paragraph after an anchor | library kind `snippet` + `insert_paragraph` | content / untracked |
| 10 | Name output files by convention from their content | `name_output` | formatting |

## 0. What all of them share

### 0.1 One text-edit core, verified by one independent model

Items 1, 2, 3, 4, 5, 6 and 7 all change words *inside* existing paragraphs. Today that is `replace_text` alone, which
needs each match inside one `w:t` and whose edited blocks V10 only checks loosely (pPr unchanged, no new run-property
signature). Batch 1 adds one core for every in-paragraph edit and a verifier model strict enough to say *exactly this
change and nothing else* for every edited paragraph.

**Run text** (a both-sides definition; transform `ops/textedit.py`, verifier `verify/edits.py`): the paragraph's runs
(nearest `w:p` is the paragraph, document order), skipping runs inside `w:del`/`w:moveFrom`; per run child in order:
`w:t` → its characters; `w:tab`/`w:ptab` → `\t`; `w:br`/`w:cr` → `\n`; `w:noBreakHyphen` → `-`; `w:softHyphen` and
`w:lastRenderedPageBreak` → nothing (transparent); any other child (field characters, field code, drawings, symbols,
note references…) → `\x00`. Paragraph-level elements between runs (bookmarks, proofing marks, comment ranges) are not in
it. Rules and patterns of the new ops are matched against run text, so a header pattern can say `\t` where the header has
a tab.

**An edit** is `(start, end, text)` over run text, plus `drop_highlight` (a filled highlight slot loses its highlight).
Rules every edit obeys (plan refuses, V10 fails):

* `start < end`; edits of one paragraph never overlap;
* every position in `[start, end)` is a character of a `w:t` (never a tab, break, hyphen element, field or drawing:
  `structure`, saying what is in the way);
* no touched run (one holding a character of the range) lies in a field — between a complex field's begin and end over
  the whole part, or inside `w:fldSimple` (`field-result`);
* between the first and last character of a range there is no other text-bearing element of the block (`w:delText`,
  `w:instrText`, `m:t`) — so the range is contiguous in the block text (`structure`);
* tracked: every touched run's parent is the paragraph itself (not a hyperlink, content control or someone's pending
  insertion: `nested-container`), no touched run carries `w:rPrChange` (`tracked-format`), and no transparent element
  sits strictly inside a range (`structure`).

**Untracked result**: the range's characters are removed from their `w:t`s and `text` is written where the first one
was, in that run (its properties; minus `w:highlight` when `drop_highlight`). Emptied `w:t` elements and runs left with
nothing but `w:rPr` are removed; containers never are. No run is split (a `drop_highlight` edit must cover whole runs —
highlight slots do).
**Tracked result**: each touched run is split at the range's edges; each piece inside the range goes into its own
`w:del` (text → `w:delText`); `text`, when not empty, follows the last of them as one run (a copy of the first touched
run's attributes and properties, highlight dropped when asked) in one `w:ins`. Deletions before insertions, as Word
writes them. Revision delta: `del` +1 per touched run, `ins` +1 per edit with text.

**The item model** (verifier). A paragraph is a sequence of items in document order: one item per character of every
`w:t`/`w:delText`/`w:instrText`/`w:delInstrText` of its runs, one item per other run child (its signature), one item per
paragraph-level non-run element (bookmark, proofing mark, …). Every run item carries its *context*: the run's property
signature, the run's attributes, and the chain of containers between the run and the paragraph (tag + attributes). The
expected output is derived from the SOURCE paragraph and the edits the verifier derived itself:

* untracked: the range's character items vanish; `text`'s characters appear at the position of the first one, with
  its context (highlight removed from the property signature when `drop_highlight`); everything else stays, in order;
* tracked: the range's character items become `w:delText` items whose container chain gains a NEW `w:del` (author
  checked, id and date present — V4 checks the ids are unique and new); `text` follows them in the first item's context
  plus a NEW `w:ins`.

The output paragraph's items must equal the expected items one for one, its `w:pPr` and attributes must be the source's.
Run boundaries are free (a run split into identical runs is the same items) — what can never pass is a character that
moved, changed, lost or gained formatting, a tab or field that moved, a revision on the wrong text, or anything else in
the paragraph that changed. The residual comparison already removes text-edited blocks; the kinds register the blocks
they checked so the residual removes exactly those (`verified_blocks`), even when paragraph deletions shifted the
alignment.

**V2 alignment** gains one strictly additive step: in a region where the text alignment found unequal-length
replacements, the plan's *predicted* untracked deletions (source indices) and insertions (output indices) are set aside
and, when the remaining counts are equal, the remaining blocks are paired in order and compared exactly. Without it, an
untracked paragraph deletion next to an edited paragraph (item 4) reports "deleted + inserted" instead of "changed".
Nothing is excused: every pair is compared against the predictions as before, and any other shape falls back to today's
reporting.

### 0.2 Input files

Tables (`.json`, `.csv`, `.xlsx`) and images are inputs exactly like a template (rule 12): pinned by md5 in the plan,
apply refuses a changed file (`table-changed`, `image-changed`), V10 re-reads and compares, the approval token hashes
them (`editspec.spec_input_files` — every op's `template` plus `INPUT_FILE_PARAMS`; `spec_templates` stays templates only,
for the batch filter and `set_aside.json`), relative paths resolve against a spec file's folder, recipes store them
absolute. In a conversation Claude sees as **INPUT FILES** only the tables and pictures named explicitly for the batch
(`inputs.py`: path, a table's sheets and headers, a picture's format and size) and reads one with `read_input_file` —
only a listed file, by its exact path. (Changed 2026-10-19: files beside the documents are no longer discovered or
listed; a spec still names a file by its path.)
Each side has its own reader (rule 1): `ops/tables.py` / `verify/tables.py`, `ops/images.py` / `verify/images.py`.

**Table readers** (both sides, no new dependency): JSON — an object `{name: text}` (or the object at a dotted `key`),
or for pairs a list of `{find, replace}` / `[find, replace]` or an object `{find: replace}`; values must be strings.
CSV — UTF-8 (BOM allowed), first row = headers. XLSX — the named sheet (default: the first), first row = headers,
shared/inline/formula-string cells as stored; a number is accepted only when Excel shows it as typed (General or "0"
format, plain digits with an optional decimal part) and taken as its stored digits; booleans, errors, dates and other
number formats are refused (`table-value`: "format the column as Text"). Columns are picked by their header text.
Values that are empty, or hold a control character or a character the reader cannot see (`conversation.invisible`'s
rule), are refused.

### 0.3 Words placed

`compiler.placed_words` gains every typed word the new ops place: `replace_table.pairs[].replace`,
`fill_variables.values`, `set_issue_stamp.text`, `ensure_end_of_section.text` (its literal parts, `{number}` aside).
Words that come from a table file, a template, the documents themselves (agreeing values, kept options, extracted
numbers) or a snippet the user picked are not Claude's (rules 11 and 13).

## 1. `fill_variables` (item 1)

`{{NAME}}` placeholders and highlighted text (CLAUDE.md "Slots": `highlight`/`braces`, detection reused on both sides)
in the body, headers and footers (`scope`, default all three) are filled from `values` (typed: placed words) and/or a
`table` file: `{"file", "sheet"?, "key"?, "name_column"?, "value_column"?}` — names down one column, one column per
site (e.g. `value_column: "VA3"`): one pass turns a master set into a site set. Typed `values` win over the table.
A slot whose name has no value: `unfilled-slot` (or, with `unknown: "leave"`, left as it is and flagged). Each slot is
one edit (its text → the value; highlight slots lose the highlight; the value takes the slot's first run's formatting).
Refusals: the slot rules (`slot-overlap`, `slot-structure`, `slot-in-field`), textbox paragraphs, and the core's.
V10 kind `filled_variables`: the slots re-detected (`verify/template.slots_of`), the table re-read, every value
re-derived, the item model per paragraph, the count per part.

## 2. `replace_table` (item 2)

`pairs: [{find, replace, regex?, match_case? (true), whole_word? (true), flexible_whitespace? (false)}]` and/or a
`pairs_table` file (`find_column`, `replace_column`). All pairs are found in the SOURCE at once (simultaneous: "Owner" →
"Client" and "Client" → "Owner" swap), in run text, in `scope` (default body, headers, footers) and an optional
paragraph selector. Two matches that overlap: `ambiguous`. `whole_word`: where the match's first (last) character is a
letter, digit or `_`, the character before (after) it is not. A match that would have been found had a tab, line break
or non-breaking-hyphen element been the character it shows (the *reader view*: `\t`→space, `\n`→space, `\x00` kept)
but is not found in run text is refused (`spans-structure`), never silently skipped. Each match is one edit.
`allow_missing` (default false) makes a file with no match a no-op. V10 kind `replaced_text` re-finds every match with
its own matcher.

## 3. `resolve_options` (item 3)

An **option** is `[…]` whose inside holds no `[`, `]`, tab, break or `\x00`, and at least one non-space character; a
**group** is a maximal run of options separated only by spaces/no-break spaces. A group whose `[` sits inside another
bracket pair (depth > 0) is *nested*. `rules: [{match: regex, action: "pick" | "drop", match_case? (false)}]`: for each
group, the first rule whose `match` finds (re.search) some option decides:

* `pick` — exactly one option matches (else `ambiguous`, naming the options): the group becomes that option's text: two
  deletions (everything before its text, everything after it, within the group) — the kept words are the document's own;
* `drop` — the matching options go: when they are all of the group, the group and the spaces right after it (or, when
  none follow, right before it) are deleted; otherwise each one with the spaces between it and its next option (or its
  previous one, for the last); the rest keep their brackets.

Groups no rule touches stay as they are. A nested group or one in a field that a rule touches is refused
(`nested-options`, `field-result`). Deletion only — no word is placed. V10 kind `resolved_options` re-parses groups and
re-applies the rules.

## 4. `filter_tagged_paragraphs` (item 4)

`tag_regex` (required; named group `tag` or group 1) is searched in each paragraph's run text (`scope`, default the
body); the captured text split by `tag_separator` (default `\s*[/,;]\s*`) gives the paragraph's systems. With
`keep: [...]` a tagged paragraph stays when one of its systems is listed; with `delete: [...]` it stays when one of its
systems is *not* listed (casefolded, whitespace collapsed). Untagged paragraphs always stay. Deleted paragraphs follow
`delete_paragraphs`' rules (tracked: runs in `w:del`, mark deleted; untracked: removed; section breaks, fields, existing
revisions in tracked mode, non-run content, a table cell's last paragraph refused). Kept tagged paragraphs lose the
tag (the regex's whole match) when `strip_tags` (default true). The plan lists every system found and what happened to
it. V10 kind `tagged_paragraphs`.

## 5. `set_issue_stamp` (item 5)

`stamp`: a regex (`pattern`) or a list of exact phrases (`any_of`), `text`: the new wording, `scope` default body +
headers + footers ("wherever it sits"). Every match → `text` (the replacement core of §2). At least one per file unless
`allow_missing`. V10: `replaced_text`.

## 6. `ensure_end_of_section` (item 6)

`number`: a fill rule (CLAUDE.md "Fill rules": regex / paragraph_after / first_match, document or header/footer) — the
number is the file's own, never typed or converted; `text` (default `"END OF SECTION {number}"`; `{number}` exactly
once); `detect_regex` (default `(?i)^\s*END\s+OF\s+SECTION\b`). The body's last content block (the last top-level
paragraph with text, a drawing or an object, or table, or content control; empty paragraphs are skipped):

* a paragraph that matches `detect_regex` and reads exactly the expected text → nothing to do;
* one that matches but reads otherwise ("End of Section 21 13 13") → its text (first to last non-space) is replaced;
* otherwise, if an earlier body paragraph matches → `eos-not-last` (content follows the line; the file is set aside);
* otherwise a paragraph is inserted after that block: `formatting: "style"` (default) — only `style` (a style id the file
  has; default the default paragraph style); `"copy"` — the paragraph properties of the one paragraph matching
  `copy_from_regex` (without section break, numbering, pending revisions) and its first text run's properties.

V10 kind `end_of_section` re-derives the number, the block, the case and the result.

## 7. `make_values_agree` (item 7) and `name_output` (item 10)

**Places** are named finders: `{in: "document" | "headers" | "footers" | "filename", pattern: regex with named groups
(number, title, …), required?: bool}`; one place is the **truth**. Every match of the truth's pattern must agree (all
occurrences, compared with whitespace collapsed; else `ambiguous`). In every other place, each named group that does not
*agree* with the truth's value is replaced by it: values agree when their letters and digits, casefolded and run
together, are the same ("211319" ≡ "21 13 19", "Preaction Sprinkler Systems" ≡ "PREACTION SPRINKLER SYSTEMS") — such a
place is left alone, never converted. A value placed in another written form than the place used is flagged. The file
name place matches the file's stem; when it disagrees, the output file gets the corrected name.

`name_output`: `pattern` (e.g. `"{number} - {title}"`, `.docx` added), `values` (fill rules), `transforms`
(`digits`, `upper`, `lower`, `title`; default as written) — a file name is not document text, so a named, visible
transform is allowed there (never in a document). Names with `\ / : * ? " < > |`, control characters, a trailing dot or
space, a Windows device name or over 200 characters are refused (`bad-name`).

The plan carries `output_name`; one op per spec may set it (a conflict otherwise). The batch takes every file's name
from its plan before the workers start (a light pre-pass of the same transform-side code); two files that would get the
same name are both refused (`name-collision`); a spec that names files never writes back in place (copies only). The
verifier derives the name again from the SOURCE (its own fill rules and transforms) and compares it with the name the
output was written under (V10 kind `output_name`). A rename-only file is delivered (not a no-op).

## 8. `swap_image` (item 8)

`image`: the new picture (PNG, JPEG or GIF), `match`: `{"image": the old logo file}` | `{"md5": …}` | `{"name_regex": …}`
(docPr name/description) | `"all"`; `scope` default headers; `fit`: `contain` (default — the largest size with the new
picture's proportions inside the old picture's box; EMU, rounded half up) or `stretch` (the old box). Each matched
`a:blip` keeps its relationship id; the relationship's target becomes a new media member (the new picture's bytes; its
content type from the extension), `wp:extent` and `a:xfrm/a:ext` take the new size; the old media member is removed when
nothing references it any more. Refused (`structure`): a linked picture, an SVG picture (its PNG fallback is not what
Word shows), a cropped one (`a:srcRect`), one inside `mc:AlternateContent` or VML. V10 kind `image_swaps`: the matches
re-derived (own md5 of the media), the new media's bytes equal the image file, the sizes re-computed by the verifier's own
image reader, everything else in each drawing unchanged; the residual masks only those extents. Layout reference `none`.
Rule 10 holds: the added member is the op's whole purpose, predicted in `members_added`, a copy of the user's file.

## 9. Snippets (item 9)

A **snippet** is a third library kind (`snippets/<slug>.json` + `.meta.json`): `text`, `style` (optional), `formatting`
(`inherit`/`none`), saved typed (`library add-snippet NAME --text …`) or taken from a document's paragraph
(`--from FILE --paragraph REGEX`). It is inserted by `insert_paragraph` (text verbatim; `allow_missing: true` = "every
file that has the anchor") from the command line without a key (`library use SNIPPET FILES… --after REGEX`). As built on
2026-10-16 the window had a snippet editor and Claude could name a snippet in the conversation (the pick approved its
words); since 2026-10-19 the desktop has no library view and the conversation offers no library items: a `recipe`,
`operation` or `snippet` choice from the model is dropped and the model is told to choose the implementation
internally. A name still belongs to one item across all kinds. As built, the spec
`library.snippet_spec` makes carries `allow_missing: true` and `expect: {"text_diffs": {"max": 1}}`: a file where the
anchor matches twice is set aside, never given two copies. A snippet is one plain paragraph: no tab, line break, field
or character the user cannot see (`snippet_text_problem`; insert_paragraph places it in one run).

## Decisions taken (each can be changed)

1. One text-edit core and one verifier item model for every in-paragraph edit; `replace_text` is left as it is.
2. Text-changing ops are tracked by default (as `replace_text`, `delete_paragraphs`, `insert_paragraph`); `tracked:
   false` gives clean copies — e.g. "turn the master into the site set".
3. Tables: JSON, CSV and XLSX, read by our own code (no new dependency); numbers only as Excel shows them; dates refused.
4. Replacement pairs: simultaneous, whole words and exact case by default, overlaps refused, near-misses refused.
5. Options: first applicable rule per group; `pick` must be unambiguous; nested groups refused; no words placed.
6. Tags: a regex the compiler fits to the user's convention (sampled from the files); `keep` or `delete` lists; a
   paragraph tagged for several systems stays while one of them stays.
7. END OF SECTION: replace a wrong line, insert a missing one, refuse a misplaced one; the number from the file itself.
8. Agreement: equal letters/digits = agree (left alone); the truth's text placed verbatim elsewhere.
9. File names: transforms allowed (a name is not document text), collisions refused, copies only.
10. Logos: proportions kept inside the old box, top-left; the old picture's alt text is left as it was (flagged).
11. Snippets: a library kind inserted by `insert_paragraph`, from the command line (the conversation offer was
    removed 2026-10-19).

Not in this batch, recorded: tags that are styles or highlight colours; tags that cover a whole article; editing inside
text boxes; replacing across tabs/breaks; `w:tab` inside a replacement; snippets with rich run formatting; renaming in
place.

## After merging master (2026-10-16): reads and chains

Master gained, while this batch was built, plan-time conflicts for what an op READS (`ops/base.verify_family`) and
chains (`chain.py`). Merged without changing either: the ops that extract a value now declare what they read —
`name_output` and `ensure_end_of_section` the paragraphs their fill rules take a value from (`slots.fill_reads`, as
`verify:text`), `ensure_end_of_section` also the end of the body and its copy-from paragraph, `make_values_agree` every
paragraph a place matched and, with a file-name place, the output's name — so rewriting the line a name or a number comes
from in the same spec is a plan-time conflict (run it first, as its own spec or an earlier chain stage). A chain keeps a
renaming stage's name in its `outputs/` and refuses `in_place` with a renaming stage before anything is written; its
approval token covers every stage's tables and pictures; neither snippets nor chains are offered in a conversation
(snippets were, until 2026-10-19).
