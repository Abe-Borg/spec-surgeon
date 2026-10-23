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
8. **Match body formatting to template** (Safe, no model): page setup, the template's style per role, its numbering
   (automatic and typed, "1.01" converting to a "1.1" template when the counters agree), paragraph spacing, indents
   and alignment cleared so the styles govern (bold, italic and underlined words stay), the font matched; no
   wording, no tables; a counter that would shift sets the file aside; a one-time role confirmation per template.

## Items

| Item | Goal | Expected outcome |
| --- | --- | --- |
| M1 — Safe mode | The common changes as buttons, no AI call | `recipes.py` (14 buttons, `template_kind`); `DocumentTask` `recipe` action through `compiler.compose_submission` (the one gate), `set_mode`, `hand_off`, `left_untouched`; `Session.model_free` (no visual check, no recovery of a button's run); `Settings.mode` (Safe at the first start); routes `/api/recipes`, `/api/recipe`, `/api/mode`, `/api/handoff`; the page's mode switch, `SafePanel`, the hand-off card; How to use for both modes; trust ledger A22–A24, T48, T49 |
| M2 — numbering engine | What matching a template's body needs from the numbering operations | `convert_numbering` `select.include_tables` and `counters: "keep"`; `convert_typed_numbers` `match: "counters"`; a layout plan's own `set_aside`; `numbering_convert.template_scheme`, `compiler.compose_after`, `structure.role_summary(rows=)`; each behind its V10 twin |
| M3 — match body formatting | The button | `template_roles.py` (detection, validation, the confirmed record under the data folder by content digest), `body_match.py` (per file and per layout, the chain), the roles form |
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
| M1 — Safe mode | implemented; live run pending | claude/quirky-fermat-6zwam8 | 2026-10-23: `recipes.py` (14 buttons; prepared, reference and unusable templates told apart by `template_kind`); `Session.outcome_source`/`model_free`; `DocumentTask` `recipe`, `set_mode`, `hand_off`, `left_untouched`, Safe mode refuses send/choose and continues no conversation; `compiler.PLANS_ON_NONE` (a button no document can take previews each one's reason); `Settings.mode`; the four routes; `SafePanel`, the mode switch, the hand-off card, How to use and Workflows; trust A22–A24, T40, T48, T49. `tests/test_recipes.py` (every button with no AI call; dirty files left untouched with reasons; typed words the only words placed; the mock's list pinned to the app's), `tests/test_web_server.py`, `frontend/tests/recipes.test.ts`, flows (switch, a form, preview, copies, hand-off). Needs the owner's machine: a real mixed batch through each button with Word. |
| M2 — numbering engine | planned | — | — |
| M3 — match body formatting | planned | — | — |
| M4 — pre-filled wording | planned | — | — |
| M5 — Obliterate | planned | — | — |
