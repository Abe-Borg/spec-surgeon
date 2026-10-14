# Playbook — numbering-scheme conversions

Converting a document's part/article/paragraph numbering from one scheme to another (e.g., lettered sub-levels to decimal, or one firm's spec convention to another's). Formatting mode: the rendered numbers change, the text does not.

* Confirm the exact target scheme against an exemplar document from the project before building anything: how parts number, article zero-padding and restart-per-part behavior, and whether deeper levels are decimal (`.1/.2/.3`) or lettered (`A./a.`). Describe the pattern back to the user and get explicit confirmation.
* Implementation that worked: dedicated styles for part/article/list levels bound to an abstractNum cloned from the exemplar, with fonts stripped from the numbering definition so the body font inherits.
* After a conversion, in-text cross-references written against the old scheme go stale (e.g., lettered refs like "2.15D" or "section 3.4C" when the new scheme is all-decimal). Regex-scan for them and flag for the user — rewriting them is content editing and needs their go-ahead.
* Source documents can carry pre-existing numbering glitches (e.g., two articles rendered with the same number). Clean autonumbering then shifts later numbers relative to the old rendering — report the shift; it's a correction, not a defect, but the user needs to know.
* Template files can carry their own typos. Flag them; don't silently fix — the no-generated-text rule applies to templates too.
* Cross-references come in two kinds. Literal text refs (above) need tracked edits; REF/SEQ/TOC fields display cached results (the text between the field's separate and end markers) that go stale after renumbering — the instruction and the cached result are separate things, renders show the stale cache, and Word refreshes on update. Flag stale field results rather than editing them. Deleting paragraphs can also orphan bookmarks and break REF targets ("Error! Reference source not found").
* An empty paragraph carrying a numbered style still consumes a number — a known cause when the 1..n sequence check fails with a gap that has no visible text at it.

## Verification specifics

* V2 expects zero text diffs in `document.xml` (numbers are not text); the predicted part set is `styles.xml`, `numbering.xml`, and `document.xml` (pStyle/numPr changes only).
* V6 programmatic audit: `pdftotext -layout`, regex the article numbers, assert per-part sequences are 1..n with no gaps. Render-check numbering in LibreOffice with the pStyle-linked `w:ilvl` lesson in mind (`ooxml-lessons.md` §6).
* `styleId`/`numId`/`abstractNumId` collision check before inserting the cloned definitions (lessons §1).
* If the document had `trackRevisions` on, decide with the user whether the style changes are tracked (`pPrChange`) or plain; either way V4's predicted delta is written before the transform.
