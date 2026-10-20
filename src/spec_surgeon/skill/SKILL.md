---
name: docx-surgical-edit
description: Method for making surgical, provably lossless edits to existing Word documents (.docx) — tracked-change redlines, header/footer/cover templating, numbering-scheme conversion, page-numbering fixes, style and formatting changes, content merges, field and table edits — where every character of the original must survive and the output goes back to a client or a shared folder. Use this whenever the user asks to edit, fix, reformat, redline, merge, re-template, renumber, or "make a change to" an existing .docx (specifications, contracts, reports, anything), especially when the file has tracked changes, lives in OneDrive/SharePoint, or the user says anything like "don't lose anything", "surgical", "track the change", or "I will lose my job". Do NOT use for creating a new .docx from scratch or for merely reading one — the docx skill covers those; this skill layers a verification contract on top of it.
---

# Surgical .docx editing

Standing method for editing existing Word documents without losing or inventing a character. Proven across real jobs — tracked-changes content merges, header/footer/cover templating, numbering-scheme conversion, tracked content redlines inside pending insertions — on files that go to clients. The user's words: "I will lose my job" if content is silently lost or invented. Treat that as the spec.

This skill ships no scripts by design (see "Scripts are disposable"). It ships a contract, a verification suite, a lessons library, and job playbooks. Sections or bullets marked **[unproven]** were added when the method was generalized (2026-10-13) and have not yet survived a real job; apply the full verification suite to them the first time and, once they hold up, remove the mark.

## The contract

Never let the language model write, retype, or "reconstruct" document text. Every character in an output file must come from one of the source files, moved or re-styled by a deterministic Python script over the unpacked OOXML. The model decides what transform to run; the script is the transform.

The contract has two modes, and the first thing to settle on any job is which one applies:

* **Formatting mode** — the job changes how the document looks or is structured (styles, numbering, headers/footers, templates, page numbering, merges of existing text). Zero generated characters. The only synthesized characters permitted to date: single spaces at word-join boundaries in redlines. If a formatting job seems to require generating text, stop and ask the user first.
* **Content mode** — the job changes what the document says. Generated characters are limited to the exact words the user explicitly approved (before → after, shown and confirmed), and every one of them lands inside a tracked revision. Borrowed wording from a sibling document is preferred over typed wording. See `references/playbooks/content-redlines.md`.

A third variant, **untracked edits**, exists only when the user explicitly asks for an untracked change; it is covered in `references/playbooks/untracked-edits.md` and changes the verification arithmetic, not the contract.

## Scripts are disposable — the contract is not

Every session writes its own transform and verification scripts fresh, against the current files. Never persist bespoke scripts into the project for reuse, and never treat a prior session's scripts as authoritative — frozen code bakes in stale assumptions (part names, folder layout) and constrains better future models. The doc's own rule already applies one level down: resolve everything from the file in front of you.

What persists instead is this skill: the objectives, the verification suite as a mandatory specification, and the paid-for lessons. The method's safety has never depended on the transform being right — it depends on the verification catching whatever the transform got wrong. Three disciplines keep a fresh implementation honest:

* Implement the entire verification suite, every job. If a check is inconvenient for a particular job, the check wins; a failing assertion gets root-caused, never explained away or weakened to pass.
* Independence: the verifier is a separate script sharing no helper code with the transform, reading only the before/after packages — so the two cannot share a failure mode.
* Predict first: write the enumerated expected-diff list BEFORE running the transform; verification asserts that prediction. Deriving the expected list from what actually changed is confirmation, not verification.

The one persisted code layer is the docx skill's maintained scripts (merge_runs, validate, soffice, accept_changes) — upstream tooling, resolved fresh each session. What each provides, and what to substitute when it is absent, is in `references/environments.md`.

## Intake — classify the job before touching anything

Settle these in the first exchange (one message, questions led with a recommendation, never buried):

1. **Mode**: formatting, content, or untracked (above).
2. **Job type** → which playbook to load:

   | Job | Playbook |
   |---|---|
   | Wording change under tracked changes | `playbooks/content-redlines.md` |
   | Part/article/list numbering scheme change | `playbooks/numbering-conversion.md` |
   | Cover/body page numbers wrong | `playbooks/page-numbering.md` |
   | Apply a header/footer/cover template, transplant styles | `playbooks/header-footer-templating.md` |
   | User explicitly wants a plain, untracked change | `playbooks/untracked-edits.md` |
   | Edit touches fields, content controls, comments, footnotes, images, tables, sections | `references/element-types.md` (per-element traps) |
   | Anything else | No playbook; the contract and verification suite still apply in full |

3. **Environment**: which of the setups in `references/environments.md` this session is in — it decides how files are acquired, what tooling exists, and the exact shape of the overwrite guard.
4. **Per-project facts** the job depends on (template file, numbering exemplar, issue stamp, author name, which file is "latest"). The spec-document instance of this list is `references/per-project-setup.md`; write the answers in a short project notes file (template there) so later sessions on the same project apply them instead of re-asking.
5. **Scope of the text-preservation check**: list every text-bearing part the transform will touch — `document.xml`, headers/footers (resolved via rels, never guessed), footnotes/endnotes, comments, textbox content — so the verifier's scope is decided before the transform exists.

## Pipeline per job

Environment-neutral; the mechanics of steps 1 and 6 vary by environment and are spelled out in `references/environments.md`.

1. **Acquire.** Get the source file(s) into the working area and record each file's identity: path exactly as listed (names have contained en-dashes and doubled spaces — copy, never retype), size, mtime, md5. Confirm which candidate is "latest" when several exist, and say which you chose.
2. **Unpack and baseline.** `unzip` the .docx. Snapshot baselines before touching anything: md5 of every file in the package + a copy of every part you intend to touch. Run the XSD validator on the SOURCE now, so output errors can be judged against the source's own baseline.
3. **Discover.** Dump every paragraph's text with `{+ins+}`/`[-del-]` markers; locate targets by text plus `pStyle` (autonumbered headings carry no number in their text); resolve header/footer parts via `sectPr` → `r:id` → rels; derive boundaries two independent ways. For a surgical redline do NOT run `merge_runs` on the working copy — it rewrites runs document-wide and destroys the byte-level "untouched" comparison; instead assert each target substring lies inside a single `w:t` and merge only the target paragraph if it does not. Note `merge_runs` processes `document.xml` only.
4. **Predict.** Write the enumerated expected-diff list — every block whose text will change and its exact post-transform string, every part whose hash will change, the exact `w:ins`/`w:del` delta per region, any fill values derived by rule — before writing the transform. This list is what the job is judged against and is reproduced verbatim in the report.
5. **Transform.** Python/lxml script. Parse and re-serialize with lxml (`xml_declaration=True, encoding='UTF-8', standalone=True`); never regex-edit XML, never pretty-print. Repack by copying the source zip's members in their original order and compression and substituting only the changed parts (Python `zipfile`); `zip -r` adds directory entries the source never had and trips the package whitelist. Never name a working script after a stdlib module (`inspect.py`, `types.py`, ...).
6. **Verify.** The full suite in `references/verification-checklist.md`, every check, with the actual numbers recorded. Non-negotiable: it is the specification every session's fresh implementation must satisfy in full, under the independence and predict-first disciplines above.
7. **Deliver, guarded.** Send the file to the user first; then write it back to its source location only after the overwrite guard passes (below). Delivered files never have tracked revisions accepted or rejected on the model's own initiative — accept-all copies are verification artifacts only; the user accepts and rejects in Word, or asks for it by name (below).
8. **Report.** What changed, what provably didn't, judgment calls, and things flagged for review, per `references/reporting.md`. Name each verification check with its result and include the enumerated diff list verbatim — the scripts evaporate with the session, so the report is the durable audit artifact.

Multi-file jobs end with a cross-file consistency sweep: compare rendered first pages, stamps, and header/footer geometry across all delivered files.

## The overwrite guard (has fired for real — respect it)

Before writing back to the source location, re-check the source. If mtime/size moved since you acquired it: re-acquire, unpack, diff against the base you built on. Word/OneDrive re-saves shuffle things without content change (part renames like header1→header4, run merging, `pPrChange` ids appearing, `rsid` churn, `w:proofErr` and `w:lastRenderedPageBreak` scattering), but users also make real edits mid-session (e.g., adding a revision-table row). The correct move is to rebase: re-run your (fully scripted, therefore cheap) transform on the current disk file, re-verify, then write back against the fresh identity. Never write over a drifted file, never use a force/overwrite flag.

New-file deliveries (copies into a new folder) still get the guard's first half: re-check that the SOURCES haven't drifted since acquisition, because the outputs derive from them; only the write-back identity check drops away.

The guard has a blind spot the mtime check cannot cover: the file being open in Word at write-back time. Word holds the whole document in memory, so the user's next save after the write-back replaces the delivered file wholesale — no conflict, no merge, no warning. Confirm the file is closed before writing back, and have the user reopen fresh afterward. The concrete check: Word keeps an owner lock file `~$<name>.docx` beside the document while it is open; no lock file = closed. How to run that check in each environment is in `references/environments.md`.

Two safety-net facts. If Word ever offers to "repair" a delivered file, that delivery has failed — never issue a repaired file (repair can silently drop parts); root-cause it instead. And OneDrive/SharePoint keep version history — the last-resort rollback if something slips through everything above.

## Durable defaults (carry across projects — do not re-ask)

* Tracked-change author: **Abraham Borg** (also the `--author` value for the validator on tracked jobs).
* Save mode: overwrite in place at the original path, guarded (identity recorded at acquisition + the overwrite-guard re-check).
* Delivery: send the file to the user first, then write back to the source path.
* Delivered files never have tracked revisions accepted or rejected on the model's own initiative. The user accepts and rejects in Word — or asks for it explicitly (Spec Surgeon's `resolve_tracked_changes`, 2026-10-16: "accept all of Jeremy's changes"); then exactly the asked revisions are resolved, the preview lists them by kind and author, and the output is verified against an independent resolution of the source. The same holds for the two other things the method kept out of reach until then: document properties (`references/element-types.md`) and the track-changes setting — never touched as a side effect, changed only when the user asks for exactly that.
* Code/standard edition references inside documents are deliberate pins — never "helpfully" update them.

## Reference index

Load what the job needs; everything here is small enough to read whole.

* `references/verification-checklist.md` — the nine checks, as a literal pass/fail checklist with the numbers each must record. Read on every job.
* `references/ooxml-lessons.md` — the paid-for Word/OOXML lessons library, grouped by theme. Read the groups relevant to the job; read it whole when something behaves unexpectedly.
* `references/element-types.md` — per-element traps for fields, content controls, comments, footnotes, images, tables, sections, bookmarks. **[unproven]** Read when the edit touches one of them.
* `references/environments.md` — how acquisition, tooling, rendering, and the overwrite guard work in each environment (cloud container + device bridge; cloud container + device shell; local session on the user's Windows machine; attachment-only). Read at intake.
* `references/per-project-setup.md` — the questions that differ per project (spec-document instance) and a notes-file template for recording answers.
* `references/reporting.md` — report structure and style.
* `references/playbooks/` — job-type playbooks listed in the intake table.
