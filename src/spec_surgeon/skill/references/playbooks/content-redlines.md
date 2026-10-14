# Playbook — content redlines (tracked wording changes)

When the job changes what the document SAYS rather than how it is formatted, the no-generated-text rule bends only as far as the user's explicit approval of the exact words. What worked on the 21 05 00 GRC / FM Global fix (2026-09-23):

* Provenance first. The project's own artifacts explain why the wrong text is there. The `.baspec` project file is a zip whose `project.json` holds the authoring-session history, the drawing digest, and `project_facts` — it showed the FM-Global-as-insurer language came from an "assume it is FM Global" instruction and quoted the drawings' code-basis table verbatim. Read it before proposing anything. For other document families, the equivalent is whatever records how the document was authored (a previous issue, an email thread, a change log).
* Borrow wording from a sibling document that already says it right (21 10 00 had "the Owner's risk consultant – GRC (Global Risk Consultants)" and "UL listed and/or FM Approved"). Borrowed phrasing is the closest a content edit gets to extracted-never-typed; keep its punctuation (en dash, apostrophe style) as found, and match the surrounding paragraph's conventions where the two differ.
* Separate the contradiction from the collateral. Passages that name the wrong party (insurer, auditor, mandatory approval) are the fix; passages that cite the same body's documents as design criteria are the user's judgment call — list them, recommend, leave them alone by default.
* Ask ONE question, lead with it, include a recommendation and the exact before → after text, then proceed on the answer. An inventory with the question buried at the bottom reads as stalling.
* Prefer a pure deletion plus a pure insertion over a wholesale replacement where the sentence allows ("UL listing and/or " inserted; " required to be FM Approved" struck) — faster to review and the reject view stays trivially right.
* Hand-write the accepted-view string of every edited paragraph into the verifier before running the transform; that prediction is what the job is judged against.

## Expected revision deltas per paragraph

As recorded on the GRC job — all four shapes are edits to text that is itself a pending insertion:

| Edit shape | `w:ins` | `w:del` |
|---|---|---|
| Split-with-replacement (nested `ins > del`, new `ins`, re-wrapped suffix `ins`) | +2 | +1 |
| Whole-insertion replacement | +1 | +1 |
| Pure insertion inside | +2 | 0 |
| Pure deletion inside | +1 | +1 |

The extra `+1 ins` in the "inside" rows is the re-wrapped suffix of the surrounding insertion. Outside any existing insertion the shapes are simpler: pure insertion +1 / 0, pure deletion 0 / +1, replacement +1 / +1. Predict per paragraph from the structure actually in front of you, and let V4 assert the sum.

## Mechanics

* Tracked-change grammar, nested-revision structure, date convention, `xml:space="preserve"`, run splitting: `ooxml-lessons.md` §2–4. Read them before writing the transform; every one of them has bitten.
* Do not run `merge_runs` on the working copy; assert each target substring lies inside a single `w:t`, and merge only the target paragraph if it does not.
* V8 (accept/reject simulation) is mandatory, with pandoc as the independent oracle; LibreOffice is not an oracle for nested revisions.
* V5 runs with `--author` — the untracked-edit check is the whole point on this job type.
* The delivered file keeps every revision pending. The user accepts and rejects in Word.
