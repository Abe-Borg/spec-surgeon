# Per-project setup — confirm at the start of each project

These were decided fresh on past projects and will differ; never carry them over silently. The list below is the specification-document instance (construction specs with cover pages, issue stamps, part/article numbering). For other document families, build the equivalent list at intake: the facts the job depends on that no file in front of you can settle.

Once a setup answer is given, write it in a short project notes file (template at the end) so later sessions on the same project apply it instead of re-asking.

## Questions for specification documents

* Specs folder location inside the connected project folder, and which files are in scope.
* Header/footer/cover source of truth: locate the project's template (past projects used a file named like `00_00_00_SPECIFICATION HEADER AND FOOTER TEMPLATE.docx`). Body pages reuse the cover design with right-aligned tab stops rescaled to the body section's text width — cover and body margins usually differ.
* Cover PROJECT REVISION HISTORY tables are per-spec content — never overwrite them with the template's sample rows.
* Issue stamp text and date, per revision issue. The template's own stamp may belong to a different or future issue — confirm which stamp each file gets before applying.
* Numbering scheme, confirmed against an exemplar (see `playbooks/numbering-conversion.md`).
* Code and standard edition references (NFPA etc.): specs deliberately pin the editions adopted by the project's jurisdiction/AHJ, which often trail the latest published editions. Never "helpfully" update edition years — confirm the jurisdiction's adopted editions for the project and leave the pins alone unless the user directs otherwise.
* Which file is "latest" when several candidates exist (root working copy vs. `*-temp/` REDLINE exports from the authoring tool): choose by mtime, verify the root copy is newer than every export, and say which you chose.
* Provenance artifacts: the project's `.baspec` file (a zip whose `project.json` holds the authoring-session history, the drawing digest, and `project_facts`) explains why text is the way it is. Read it before proposing content changes.

## Questions for any document family

* Which file(s) are in scope and which is authoritative when several exist.
* Whether the document carries live tracked changes from someone else and whose they are (they must survive untouched).
* Tracked-change author to use (default in SKILL.md) and whether the edit should be tracked at all.
* Any template or exemplar the output must match geometrically (feeds V7).
* Who reviews the output and in what view (All Markup, Simple Markup, accepted) — it decides what the report points at.

## Project notes template

Keep this short, in a `PROJECT_NOTES.md` beside the specs (or in the project's own CLAUDE.md when the project keeps one):

```
# <Project> — docx editing notes
Specs folder: <exact path as listed>
In scope: <files>
Template: <exact file name>   Stamp for current issue: "<text>" dated <date>
Numbering scheme: <description confirmed against <exemplar file>>
Edition pins: <as adopted by <jurisdiction>; do not update>
Latest-file rule: <root copy vs exports; how decided>
Tracked-change author: <name>
Decisions log:
- <date>: <decision, who made it>
```
