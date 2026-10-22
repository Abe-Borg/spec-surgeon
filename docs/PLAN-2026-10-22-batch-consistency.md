# Spec Surgeon — Implementation plan: batch consistency

2026-10-22. From the owner's live run over a Division 21 batch (21 05 00, 21 05 17, 21 05 29, 21 05 53, 21 12 00,
21 13 16, 21 31 13, 28 46 21 and others) with a header/footer template. After the run, the footer of 21 05 53 showed
its title in mixed case while the other files showed capitals. The assistant's own write-up listed what kept it from
noticing or fixing that, and other limits it ran into. Each point was checked against the code; this plan is what
follows from it. The full-control round (`docs/PLAN-2026-10-21-full-control.md`) keeps its live-run rows.

## Findings

| # | The write-up said | What the code shows | Kind |
| --- | --- | --- | --- |
| 1 | The footer title of 21 05 53 is in mixed case: the template fill copied the first page's title as typed, and the later rename wording was mixed case too | Correct, and the copying is by design (CLAUDE.md 6: a fill is the file's own words, a literal is the user's). A fix existed: `set_properties` caps on that footer paragraph (formatting, no letter retyped), or capitalised wording the user gives. Nothing told the assistant this file's footer would look different from the others', and the prompt never said how "capitals like the others" is done | guidance and a missing flag (C1, C4) |
| 2 | There are no case options for a template fill or a replacement | A case option that retypes letters would place characters nobody approved. Capitals as a display property are the honest option, and `apply_header_footer_template` cannot ask for them | op gap (C4) |
| 3 | Nothing compares the files and flags the one that looks different | True. Profiles are per structure pattern (one representative file each), only four patterns are shown up front, and nothing lists every file's headers and footers side by side | missing (C2) |
| 4 | The summaries show what is displayed, so typed and displayed capitals cannot be told apart | The other way round, but the gap is real: the summaries showed the text as typed and never said what Word displays (only a style's caps, in `styles_in_use`) | fixed (C1) |
| 5 | The footer summaries disagree: 21 05 53 lists four lines, 21 12 00, 21 05 17, 21 05 29, 21 31 13 and others only two | A bug: `profile.bound` cut every header/footer view to its first two lines when a file's summary ran over its size limit (`MAX_PROFILE_BYTES`), and said nothing about it. Larger files (most real specifications) were cut, smaller ones were not. The files are very likely fine | fixed (C1) |
| 6 | The rename touched only the footer; the first page still reads "Identification for Fire Suppression Systems"; no step warned | True: `replace_text` and a literal template fill change only their scope and say nothing about the same words elsewhere in the file | missing flag (C3) |
| 7 | Old titles from other projects sit in File Properties ("SECTION PP-16622 - REMOTE POWER PANELS", "SECTION 211313 - WET-PIPE SPRINKLER SYSTEMS" in 21 12 00) | The summaries show the title (`markup.properties.title`), but nothing compares it with the file's own section. `set_document_properties` takes typed words only, so fixing a batch means typing every title | missing check, op gap (C2, C5) |
| 8 | Numbering cannot be checked in files with unresolved tracked changes and in shared lists (21 31 13) | Two causes. A shared list (two list instances on one definition) is a deliberate limit: Word continues one count across them and LibreOffice does not. Tracked changes are a limit that reaches too far: the role reading (`structure.py`) marks a whole list unresolved when any of its paragraphs carries any revision, a wording redline inside a run included, which cannot change a number | by design (shared); too broad (C7) |
| 9 | Tracked deletions are refused in files with unresolved tracked changes | Overstated by the prompt itself ("On a file with pending tracked changes a tracked deletion refuses"). The op refuses only a paragraph or table that itself carries a pending revision. Word can also delete inside someone's insertion (a deletion nested in it, as `replace_text` already writes) | prompt fixed (C1); op gap (C6) |
| 10 | Three files (21 13 16, 21 05 00, 28 46 21) were never summarised | THE BATCH shows the first four patterns' profiles; the rest read "(call inspect_profile)", and the assistant did not call it | missing (C2) |

## Principles for this round

1. **Describe, don't decide.** The overview and the new flags say what the files show and where they differ. They
   change nothing and choose nothing: the assistant names them, the user decides.
2. **Capitals are a display property** unless the user gives capitalised wording. No option retypes a file's letters
   in another case.
3. **A summary never omits silently.** Whatever a size bound cuts is named where the reader looks (`truncated`, a
   count beside the cut list).
4. The usual rules hold: predictions from the source, a V10 kind written first for any new parameter, the verifier
   independent, every refusal code explained.

## Workstreams

| Item | Goal | Expected outcome |
| --- | --- | --- |
| C1 — truthful summaries | The summaries say how text looks and what they left out | `profile.shown_case`: a header/footer view's `shown_in_capitals` and the profile's `first_paragraphs_shown_in_capitals` name the lines Word shows in capitals through formatting (all, small, partly), and `read_paragraphs` / `sample_paragraphs` rows carry `shown_in_capitals`; a view longer than six lines counts the rest in `more_lines`; the size bound's two-line thinning sets `more_lines` and is recorded in `truncated`; the prompt says text is shown as typed, how to offer capitals like the others, and that a tracked deletion refuses only what itself holds pending tracked changes |
| C2 — batch overview | Every file side by side, the odd one named, no model call | `profile.batch_overview(files)` (descriptive, transform side): per file, the section number and title as the reference fills read them, each displayed header/footer's lines (capitals marked as in C1), the File Properties title. Flags: a title or header/footer line whose letters show in a different case from most files' same line; a header/footer missing a line most files have (a template line that did not take) or holding one they do not; a Properties title naming another section number, or none of the file's own title words. In THE BATCH up front for every file (bounded, cut lines counted), and a `compare_files` tool for the rest; computed again over the working set after each run, so the next request sees the copies. The prompt: read the flags before a header/footer, title or properties request and name them in the reply |
| C3 — wording left elsewhere | A change kept to one place says where the old words still are | `replace_text` flags each place outside its scope or paragraph selection where `find` (the same matching options) also occurs ("also in the body, 1×; not changed"); `apply_header_footer_template` with a literal fill flags where the file's own value, read by the reference rule, still differs. The preview text the assistant reads carries the flags; the prompt says to relay them with an offer. Flags only: no prediction changes, no verifier change |
| C4 — capitals like the template | One step for "show the title in capitals like the others" | `apply_header_footer_template` flags a fill whose letters are in a different case from the template's own value (the template shows "IDENTIFICATION …", the file's title reads "Identification …"); a new `caps_fills: [slot names]` puts `w:caps` on each named slot's filled run: zero characters, the fill still the file's words. V10 in `verify/template.py` first, with its own reading of the slot runs; a describe case; a `COMMON_ASSUMPTIONS` entry; the prompt prefers it to a separate `set_properties` step |
| C5 — properties from the document | Stale Properties titles fixed for a whole batch in one request | `set_document_properties` takes a value built from the file's own section number and title (the reference fill rules, read as a template fill reads them) with the user's own words between them (`compiler.placed_words` traces those); a file whose number or title the rules do not find is set aside (`not-found`). V10 in `verify/docprops.py` reads the fills with the verifier's own template reading |
| C6 — tracked deletion over pending revisions | Fewer refusals in redlined files | The deletion core deletes as Word does over someone's pending revisions: text in a pending insertion gets a deletion nested inside it (`replace_text`'s grammar), text already deleted stays as it is, a paragraph mark inserted by someone gets a deletion beside its insertion. Still refused: move ranges, pending row and cell changes, and anything whose accept-all and reject-all views cannot both be predicted. Both sides (`ops/deletion.py` ↔ `verify/deletion.py`); round trips as in `tests/test_batch6_round_trip.py` (accept-all reads like the untracked deletion of the accepted source, reject-all like the source) |
| C7 — numbering evidence beside redlines | Lists in redlined files read their numbers again | In `structure.py` a list is disturbed only by revisions that can change a number: an inserted, deleted or moved paragraph mark, a tracked paragraph-property change, a numbering change. A wording redline inside a run no longer makes the list unresolved, and a paragraph with one still takes part in roles when both views fit the same role. Roles are planning evidence only (never the authority of an edit); the verifier is unchanged |

Order: C2 and C3 first (the misses of this run), then C4, C6, C5, C7.

## Acceptance

Each item on fixtures: the flag or the change end to end (plan → merge → apply → `run_suite` passes for the op
items), a wrong output and a shared wrong implementation caught for every new rule, describe cases and the catalog
for new parameters. On the owner's machine: the same Division 21 batch again. Before anything runs, the assistant
names 21 05 53's footer title and the stale Properties titles; a footer-only rename says the first page still reads
the old title; "capitals like the others" is one plan with one sentence.

## Do not, in this round

- Retype a file's letters in another case. Capitals are `w:caps` unless the user gives the wording.
- Let the overview choose, order or change anything. It describes; the user decides.
- Let the role reading (C7) become evidence for the verifier.
- Add a `force` flag or loosen what C6 cannot predict.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| C1 — truthful summaries | implemented; live run pending | claude/happy-noether-rupofa | 2026-10-22: `profile.shown_case` names lines shown in capitals through formatting (header/footer views, first paragraphs, `read_paragraphs`, `sample_paragraphs`); a header/footer view counts lines past six in `more_lines`, and the size bound's two-line thinning sets it and records `PART_LINES_CUT` in `truncated` (dropped again when the text goes altogether); the prompt's tracked-deletion line now matches the op, and it teaches capitals like the others. `tests/test_profile_display.py`: typed vs displayed capitals, partly and small capitals, a thinned four-line footer, a nine-line footer, both paragraph tools. Needs the owner's machine: the Division 21 batch again, to see the assistant read a cut footer as cut and offer capitals for 21 05 53. |
| C2 — batch overview | planned | — | — |
| C3 — wording left elsewhere | planned | — | — |
| C4 — capitals like the template | planned | — | — |
| C5 — properties from the document | planned | — | — |
| C6 — tracked deletion over pending revisions | planned | — | — |
| C7 — numbering evidence beside redlines | planned | — | — |
| R1 — revision id seed (owner's bug report, outside C1–C7) | fixed; live run pending | claude/amazing-sagan-6ntrj0 | 2026-10-22: `ooxml.max_revision_id` seeds new revision ids from the highest numeric `w:id` on any element of every well-formed XML part (styles and numbering included; a text part that is not well-formed still raises) instead of a fixed tag list that left out `w:tblGridChange` and `w:tblPrExChange`, so a tracked edit in a file whose table columns were resized under Track Changes no longer reuses that id and is no longer set aside by V4; transform side only, verifier unchanged. `tests/test_revision_seed.py`: both table changes end to end (tracked `replace_text`, four edits taking ids 8..15, `record.passed`), a styles.xml property change and a bookmark raise the seed, a malformed non-text part is skipped, the old seed shared by plan and apply caught by V4; 5 of its 6 tests fail on the old seed. Open, for a decision: V4 does not count `w:tblPrExChange` (`OTHER_REV`), so a collision with one passes the suite. Needs the owner's machine: a redlined specification with columns resized under Track Changes, a tracked replacement, opened in Word. |
