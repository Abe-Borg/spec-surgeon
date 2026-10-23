# Spec Surgeon — Implementation plan: three modes

2026-10-23. From the owner's conversation of 2026-10-22/23 about what the app cannot do and why. Other people will
use the app: most of them in a button-driven **Safe** mode with no AI, many in **Default** (the conversation as it
is), almost none in a hidden **Obliterate** mode. The owner also asked for a pre-filled wording field in Default
mode and a "match body formatting to template" button, and decided what stays out of reach.

## Decisions (the owner's, and the ones the owner delegated)

1. Out of reach stays out of reach: shapes, SmartArt, charts, equations, embedded objects, data-bound content
   controls and the theme. **Text boxes are not pursued.** The README's unmodelled gaps (run and cell borders, tab
   stops in styles, conditional table-style formatting, East Asian fonts) are skipped; refusal logs from live runs
   decide later.
2. The app never makes up a header; it copies the user's template's. Values read from each file stay read from it.
3. **Safe mode** offers buttons, each a fixed recipe built in code (`recipes.py`), never by a model. First set: match
   headers and footers to template, match font to template, find and replace, accept all tracked changes, remove
   all comments, remove highlighting, name files by section number and title, remove headers and footers. Second
   set: add or remove a watermark, swap the logo, remove hidden text, remove empty paragraphs, turn off Track
   Changes, set the document title property. Dropped: proofing language, page margins, END OF SECTION.
4. **Tracked changes in Safe mode: clean copies by default** (delegated). The preview and the checks already show and
   prove every change, and tracked changes left in a file sent to a client is the common mistake. The three buttons
   whose operation can track (find and replace, remove hidden text, remove empty paragraphs) offer one checkbox, off
   each time.
5. **Templates in Safe mode: both kinds, told apart without a model** (delegated). A prepared template (yellow
   highlight or `{{NAME}}` placeholders) fills the section number and title from each document (`SECTION_NUMBER`,
   `SECTION_TITLE` and an explicit alias table: `XX XX XX`, `XXXXXX`, `SECTION NUMBER`, `SECTION TITLE`, …) and asks
   for every other placeholder's value in the button's form; an ordinary specification as template uses the
   reference markers; a template with nothing to fill is refused at the button with the reason.
6. **Default mode** stays the conversation and gains a pre-filled editable wording field (M4): Claude drafts, the
   user edits and confirms, and the confirmed text is the user's.
7. **Obliterate** (the owner's name), hidden behind Settings because other people use the app: checks warn instead
   of block, Claude may write any code and any words; it lifts the guardrails that protect a document's content,
   never those that protect the computer or the originals (copies only, the source guard, the sandbox, no `force`).
   This reverses, for Obliterate only, the full-control and custom-envelope plans' "Do not" lines on widening custom
   methods (`docs/PLAN-2026-10-21-full-control.md`, `docs/PLAN-2026-10-22-custom-envelope.md`: "never more kinds of
   change"); in Safe and Default they stand. Built-in operations keep refusing what they cannot predict in every mode.
8. **Match body formatting to template** (Safe, no model): page setup, the template's style per role, its numbering
   (automatic and typed, "1.01" converting to a "1.1" template when the counters agree), paragraph spacing, indents
   and alignment cleared so the styles govern (bold, italic and underlined words stay), the font matched; no
   wording, no tables; a counter that would shift sets the file aside; a one-time role confirmation per template.

## Items

| Item | Goal | Expected outcome |
| --- | --- | --- |
| M1 — Safe mode | The common changes as buttons, no AI call | `recipes.py` (14 buttons, `template_kind`); `DocumentTask` `recipe` action through `compiler.compose_submission` (the one gate), `set_mode`, `hand_off`, `left_untouched`; `Session.model_free` (no visual check, no recovery of a button's run); `Settings.mode` (Safe at the first start); routes `/api/recipes`, `/api/recipe`, `/api/mode`, `/api/handoff`; the page's mode switch, `SafePanel`, the hand-off card; How to use for both modes; trust ledger A23–A25, T52, T53 |
| M2 — numbering engine | What matching a template's body needs from the numbering operations | `convert_numbering` `select.include_tables` and `counters: "keep"`; `convert_typed_numbers` `match: "counters"`; each behind its V10 twin (E1, E3, G1). The layout plan's own `set_aside` (E2) and the refactors were not needed: the button leaves a document out with its reason (`Session.left_out_reasons`) and composes through the existing gate |
| M3 — match body formatting | The button | `template_roles.py` (detection and validation; the roles form starts from the detection each time — nothing is kept on disk), `body_match.py` (per file and per layout, each layout through the gate), the roles form |
| M4 — pre-filled wording | Claude's draft, the user's words | An editable choice wording; `/api/choose` carries the confirmed text; invariant 6 reworded |
| M5 — Obliterate | Complete control, hidden | Settings → Advanced; the warning each time; unguarded words, custom methods and delivery; CLAUDE.md "Modes"; trust surfaces per mode |

Order: M1, M2, M3, M4, M5; one pull request each. M2 and M4 start from master; M3 builds on M1 and M2, M5 on M1.

## Acceptance

Each item on fixtures: every button through the gate, the preview and the run (`record.passed`) with
`vision.review_samples` and `llm.client` raising; a planted defect for every rule (a button's preview that calls the
visual check fails its test). On the owner's machine: a real mixed batch (several projects, good and badly built
files) through each Safe button with Word rendering; the Left untouched reasons recorded below; the copies open in
Word without a repair prompt.

## Do not, in this round

- Let a Safe-mode button call a model, or place a word the user did not type or attach.
- Remember Obliterate across a start, or offer it without its warning.
- Add a `force` flag, or let any mode write over an original.
- Touch text boxes.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| M1 — Safe mode | implemented; live run pending | claude/quirky-fermat-6zwam8 | 2026-10-23: `recipes.py` (14 buttons; prepared, reference and unusable templates told apart by `template_kind`); `Session.outcome_source`/`model_free`; `DocumentTask` `recipe`, `set_mode`, `hand_off`, `left_untouched`, Safe mode refuses send/choose and continues no conversation; `compiler.PLANS_ON_NONE` (a button no document can take previews each one's reason); `Settings.mode`; the four routes; `SafePanel`, the mode switch, the hand-off card, How to use and Workflows; trust A23–A25, T40, T52, T53. `tests/test_recipes.py` (every button with no AI call; dirty files left untouched with reasons; typed words the only words placed; the mock's list pinned to the app's), `tests/test_web_server.py`, `frontend/tests/recipes.test.ts`, flows (switch, a form, preview, copies, hand-off). Merged with master's to-do binning (41155ea): a button's refused or failed documents stay in scope and are not put on the list by themselves; the hand-off gives them the list's `why`/`reason`; X'ing every previewed document in Safe mode creates nothing and starts no conversation (tested). Codex review: a Default preview approved in Safe mode takes no AI step; a removed document leaves the untouched list and cannot be handed over; the form drops a picture or table no longer attached (each tested). Needs the owner's machine: a real mixed batch through each button with Word. |
| M2 — numbering engine | implemented | claude/quirky-fermat-6zwam8-numbering-engine | 2026-10-23: E1 `convert_numbering` `select.include_tables`, E3 `counters: "keep"`, G1 `convert_typed_numbers` `match: "counters"` (lenient dashes and digits, typed values against the predicted counters), each with its verifier twin; planted-defect tests (table test off, counters check off, value check skipped), wrong outputs, both sides reading values alike. Needs the owner's machine: real MasterSpec files ("1.01", "PART 1 –") through both operations — part of M3's run. |
| M3 — match body formatting | implemented; live run pending | claude/quirky-fermat-6zwam8-body-formatting | 2026-10-23: `template_roles.py`, `body_match.py`, the `body_formatting` button (roles form, page/font/clear boxes), `Session.left_out_reasons` (a button's per-document reasons beside the preview and in the hand-off), `ChainFile.exception`/`record` (a plan per layout whose layout runs as ordered steps crashed at Create — a master defect the button found). Tested on fixtures: a styled outline ("1.01" → "1.1"), a typed one, a messy one left out with its reason, refusals (no outline, mixed, deeper than the template, a shared style id, a landscape page keeps its pages), the majority rule within a pattern, no AI call, the template never written. Needs the owner's machine: a real mixed batch (office masters, MasterSpec, Heading-styled) through the button with Word, the Left untouched reasons recorded here. 2026-10-23 (review of Abe-Borg/spec-surgeon#100): the font step keeps to the body outside tables (`set_font` there, `set_style_font` on styles nothing else uses, the defaults only when nothing else could inherit them) and the imported styles are pinned to the template's font and size in a last step (`template_look`); the style guard reads table cells the conversion skips and every other part. Tested: a header and a cell in a replaced style refused, the header part byte-identical and the cell's font kept end to end, each rule with a planted defect. Then (review of #103): body text the plan does not restyle, on a style a table or header shares, takes the body font on its runs in the last step (tested end to end; planted defect caught). |
| M4 — pre-filled wording | implemented | claude/quirky-fermat-6zwam8-wording (Abe-Borg/spec-surgeon#101) | 2026-10-23: an editable choice wording (`Choice.editable`), `/api/choose` `wording`, `conversation.edited_wording`, invariant 6 reworded; tested in its own pull request. |
| M5 — Obliterate | implemented; live run pending | claude/quirky-fermat-6zwam8-obliterate | 2026-10-23: `Session.unguarded`; `compose_submission(unguarded=)` (no placed-words trace), `Conversation(unguarded=)` (its prompt says so; a custom method offered past the formatting limits), `codegen` unguarded trials (no `hard_limits`, `UNGUARDED_LIMITS`, failed checks listed in `FileTrial.unverified`), `batch`/`chain`/`layouts` `deliver_unverified` (copies only), `DocumentTask` modes, `set_mode(acknowledged=)`, `unverified` beside each result, no recovery; `Settings.show_obliterate`, `launch_mode`; Settings → Advanced, the warning, the strip, the UNVERIFIED mark, How to use; trust A28, T56 and T4/T5/T6/T25/T45 scoped. `tests/test_obliterate.py` (14), `frontend/tests/modes.test.ts`, flows. Needs the owner's machine: a request Default refuses (a text box, Claude's own wording) through Obliterate with Word, and a copy marked UNVERIFIED opened in Word. |
