# Word/OOXML lessons already paid for

Every bullet here was learned on a real job and is reproduced as written. They are grouped by theme so a session can read the groups its job touches; read the whole file when something behaves unexpectedly, because the trigger for a lesson is rarely the thing you were looking at. Job references (e.g., "21 05 00 GRC", "28 31 00") are provenance — the documents where the lesson was paid for.

## Contents

1. Part resolution and the package
2. Tracked-changes grammar
3. Nested revisions and which oracle to trust
4. Runs, text, and whitespace
5. Paragraph properties and schema order
6. Styles, numbering, and template import
7. Headers, footers, and fills
8. Rendering and LibreOffice behavior
9. Process and debugging
10. Working-environment traps

## 1. Part resolution and the package

* Never assume part filenames across sessions (header2.xml etc.). Resolve via `sectPr` → `headerReference`/`footerReference` `r:id` → `word/_rels/document.xml.rels`.
* Text lives outside `document.xml`: headers/footers, footnotes/endnotes, and comments are separate parts, and textbox text inside `mc:AlternateContent` exists twice (Choice + Fallback branches) — an edit that hits one branch but not the other makes Word and LibreOffice show different documents.
* Copying a header/footer part means also copying its `.rels`, any referenced media (logos), and the `[Content_Types].xml` override — a missing override produces a file Word wants to repair. Check for `styleId`/`numId`/`abstractNumId` collisions with the target document before inserting cloned styles or numbering.
* `w:evenAndOddHeaders` lives in `settings.xml` and is global; `w:titlePg` (different first page) is per-section — check both when wiring template headers, or parity behavior changes silently.
* Repack by copying the source zip's members in their original order and compression and substituting only the changed parts (Python `zipfile`); `zip -r` adds directory entries the source never had and trips the package whitelist.
* Real files ship with inert junk that isn't yours to fix (`[trash]/*.dat` zip members, duplicate documenttasks ids): preserve it byte-identically, report it, move on.
* Subtree c14n fails on attributes whose namespace is declared only on the document root ("w14:paraId not declared in scope"): roundtrip the element through tostring→fromstring first to materialize the declarations locally.

## 2. Tracked-changes grammar

* Tracked-changes grammar: runs wrapped in `w:ins`/`w:del` (text inside del becomes `w:delText`), unique `w:id`, `w:author="<author>"`, ISO date. Deleted paragraph = del on every run plus `<w:pPr><w:rPr><w:del/></w:rPr></w:pPr>` on the mark; revision element goes first inside rPr. Deletions before insertions at a replacement point. `pPrChange` (old pPr snapshot, last child of pPr) tracks format changes.
* Word omits `w:id` on some `pPrChange` elements; validator flags it; adding sequential ids is safe.
* Working documents frequently have `trackRevisions` ON and carry the user's own live tracked changes from their editing — these must survive untouched through any transform.
* Match the file's own revision-date convention before writing yours: Word writes `w:date` as LOCAL time with a `Z` suffix and the true UTC in `w16du:dateUtc`. Read an existing revision element and copy the pattern, or the reviewing pane shows your edits hours off.
* Region-scoped revision accounting: before removing or replacing regions that contain tracked changes, count w:ins/w:del per region and predict the exact post-transform totals, so every revision that disappears is deliberate and on the record. Replacing a header/footer part wholesale bakes any pending tracked change inside it into accepted-state plain text — flag it by name.

## 3. Nested revisions and which oracle to trust

* Editing text that is itself a pending insertion (e.g., yesterday's redline): nest the deletion INSIDE the insertion and split the original insertion around it — `<w:ins ORIGINAL><w:r>prefix</w:r><w:del NEW><w:r><w:delText>old</w:delText></w:r></w:del></w:ins>` `<w:ins NEW>replacement</w:ins>` `<w:ins ORIGINAL author/date, new id>suffix + trailing runs</w:ins>`. This is what Word itself writes when an insertion gets deleted (28 31 00 carries 31 of them from Jeremy/Jack); accept-all yields the new wording and reject-all returns to the pre-insertion original. A flat sibling `w:del` instead resurrects text that never existed on reject-all. Same-author nesting is schema-valid and Word renders it as a deletion.
* LibreOffice does not understand nested revisions: it renders `ins > del` text as live (underlined or plain, never struck) and `accept_changes.py` keeps it. For nested redlines the render proves layout only; pandoc `--track-changes=accept/reject` plus the XML simulation are the oracles that agree with Word.
* For merges: simulate accept-all and reject-all views from the XML and assert accept == new source content, reject == original char-for-char. The same two simulations apply to redlines that edit inside an existing tracked insertion, with pandoc `--track-changes=accept|reject` as the independent oracle; LibreOffice (renders and `accept_changes.py`) is NOT an oracle for nested revisions.

## 4. Runs, text, and whitespace

* Any run created or moved with leading/trailing spaces needs `xml:space="preserve"` on its `w:t`, or Word discards the space on open — silent single-character loss, exactly the failure mode this method exists to prevent.
* Splitting a run drops its non-text children unless you carry them: `w:lastRenderedPageBreak` is inert and regenerates, but a `w:tab` or `w:br` is content — move it into the right fragment and count it. Copy the proto run's attributes and rPr onto every fragment.
* For a surgical redline do NOT run merge_runs on the working copy — it rewrites runs document-wide and destroys the byte-level "untouched" comparison; instead assert each target substring lies inside a single `w:t` (true for all five targets on the 21 05 00 GRC job) and merge only the target paragraph if it does not.
* Template placeholders fragment: a slot like "X.0.0" can be five runs ('X','.','0','.','0') from rsid churn. Identify slots by their marker formatting (highlight=yellow here), assert the fragment concatenation equals the expected placeholder, then collapse to one run carrying the fill.
* Autonumbered headings carry no number in their text: "PART 1 - GENERAL" is a paragraph whose `w:t` is just "GENERAL". Never search for rendered numbering text; match text plus pStyle, and derive boundaries two independent ways.
* When counting structural children, count only `w:tab` whose parent is `w:r` — the tab-stop definitions in `pPr/w:tabs` use the same element name and silently inflate the count.

## 5. Paragraph properties and schema order

* pPr child order is schema-enforced (pStyle first … rPr, sectPr, pPrChange last). Preserve `sectPr` inside pPr when rewriting a section-break paragraph's properties.
* pPr insertion needs the COMPLETE schema order table — `jc`, `textAlignment`, `outlineLvl`, `rPr`, `sectPr`, `pPrChange` all come AFTER `spacing`/`ind`. An abbreviated table put spacing after jc; only the XSD check caught it.
* Parse and re-serialize with lxml (`xml_declaration=True, encoding='UTF-8', standalone=True`); never regex-edit XML, never pretty-print.

## 6. Styles, numbering, and template import

* LibreOffice does not implement pStyle-linked numbering levels. A style's `numPr` needs an explicit `w:ilvl`, or LO renders everything at level 0 (Word tolerates either). Always render-check numbering in LO since that's our proofing tool.
* When importing template styles into a host document, drop mc-ignorable extension elements and attributes (w14:ligatures, w14:paraId, ...) — with a hard stop if any foreign element carries document text. Keeps the host's conventions, avoids undeclared-prefix issues; the verifier compares equality modulo the same rule.
* Never import Normal — the entire body inherits it. Instead prove the effective chain (docDefaults → Normal) matches the template's for the properties the transplant relies on. Themes can differ wildly (Aptos vs Calibri/Cambria here) while explicit rFonts in Normal/docDefaults make the effective font identical. Overwrite any styleId only after proving zero surviving usage in the content that remains.
* LibreOffice renders paragraphs with no explicit spacing at different default pitch in different host documents — some single, some ~double — and the trigger never surfaced in settings/styles/docDefaults diffs. Pin transplanted content explicitly: `w:spacing w:after="0" w:line="240" w:lineRule="auto"` (Word's own defaults, so Word rendering is provably unchanged) on every transplanted paragraph INCLUDING table-cell paragraphs, and on every imported style. Merge attributes into an existing spacing element rather than replacing it, so explicit values like a style's `before="240"` survive.
* An empty paragraph carrying a numbered style still consumes a number — a known cause when the 1..n sequence check fails with a gap that has no visible text at it.
* Cross-references come in two kinds. Literal text refs need tracked edits; REF/SEQ/TOC fields display cached results (the text between the field's separate and end markers) that go stale after renumbering — the instruction and the cached result are separate things, renders show the stale cache, and Word refreshes on update. Flag stale field results rather than editing them. Deleting paragraphs can also orphan bookmarks and break REF targets ("Error! Reference source not found").

## 7. Headers, footers, and fills

* Fill values are extracted, never typed: derive each fill from the source XML by a deterministic rule AND hand-predict it in the expected-diff list; the transform asserts the two agree. Extraction rules must survive real-file junk: footers with no tab between fields (strip a prefix by regex, don't split on tabs), NBSP-laced cover lines (prefer the footer's plain-space section number), leading spaces on titles (trim at slot boundaries and put the trim in the diff list).
* Cover revision-history tables (and any per-document table a template carries sample rows for) are per-document content — never overwrite them with the template's sample rows.
* Template files can carry their own typos. Flag them; don't silently fix — the no-generated-text rule applies to templates too.
* Body pages reuse the cover design with right-aligned tab stops rescaled to the body section's text width — cover and body margins usually differ.

## 8. Rendering and LibreOffice behavior

* LO's tracked-changes view prints dual numbers like `2.05[2.04]` (current[if-rejected]) and `[.1]` for inserted items. Display artifact, not a defect — compare against a pre-edit render before "fixing" anything.
* LO page counts move when footer height or margins change, and LO's count is not Word's count. A page-count delta in the proof render is not evidence of body change — the XML identity checks are the character truth.
* `pdftotext` is a structure auditor, not character truth — it mangles ligatures and dashes, so never "fix" a glyph oddity seen only in its output; character truth is the XML, visual truth is the rendered image.
* `pdftotext -bbox` turns "looks off" into numbers: extract per-word yMin and diff anchor words against the template's render. It both detects a rendering deviation and proves the fix.
* When a rendering difference resists diagnosis by reading XML, stop theorizing and run the experiment: patch the suspected property into a scratch copy, re-render, measure. Minutes, and it converts a theory into a mechanism.
* The LO render's page numbers can differ from Word's by one — say so when pointing the user at a page.

## 9. Process and debugging

* Verifier bugs look exactly like transform bugs. Two of three initial failures on the 21 05 00 GRC job were the verifier's own (zip directory entries; a splice check that stripped the first occurrence of "applied" instead of the inserted one). Root-cause by reading the actual strings before touching either side; a corrected check must be at least as strict as the one it replaces, and the transform must pass an independent route (accept view, pandoc) before you conclude the verifier was the one at fault.
* Hand-write the accepted-view string of every edited paragraph into the verifier before running the transform; that prediction is what the job is judged against.
* If Word ever offers to "repair" a delivered file, that delivery has failed — never issue a repaired file (repair can silently drop parts); root-cause it instead.
* Word/OneDrive re-saves shuffle things without content change (part renames like header1→header4, run merging, `pPrChange` ids appearing, `rsid` churn, `w:proofErr` and `w:lastRenderedPageBreak` scattering). A diff against a re-saved file must be read with that in mind before concluding the user edited anything.

## 10. Working-environment traps

* Never name a working script after a stdlib module (inspect.py, types.py, ...): scripts run from the working dir shadow stdlib imports inside third-party libraries — `from lxml import etree` imported a script named inspect.py and executed the whole job as a side effect.
* `~` in bash = /root in the cloud container; Write/Read tool paths may resolve elsewhere (e.g., /home/claude). Keep one working dir and use absolute paths.
* Folder and file names have contained en-dashes (not hyphens) and doubled spaces — copy them exactly as listed; never retype them from memory.
* Word keeps an owner lock file `~$<name>.docx` beside the document while it is open; an empty `ls ~$*` in the folder = closed.
