# Playbook — header/footer/cover templating and style transplant

Spec Surgeon's built-in importer reconciles localized Word style IDs automatically. Use
`inspect_template_styles` to inspect the proposed mapping; submit the original template with
`apply_header_footer_template`. Different IDs or translated linked-character names alone do
not require a manual Word rebuild. Existing destination styles remain intact, missing dependencies
are isolated from collisions, and independent verification re-derives the mapping from the inputs.

Applying a project template's headers, footers, and cover design to one or more existing documents, filling the template's placeholder slots from each document's own content. Formatting mode: every fill value is extracted from the document by a deterministic rule, never typed. This playbook is a sequence; the lessons it depends on are in `ooxml-lessons.md` and are cited by section rather than repeated.

## Before the transform

1. Confirm per-project facts (`per-project-setup.md`): template file, which stamp each document gets, that cover revision-history tables are per-document content and stay, body-vs-cover margin differences.
2. Resolve every header/footer part of the target by `sectPr` → rels (lessons §1). Read `w:titlePg` per section and `w:evenAndOddHeaders` in `settings.xml` (§1).
3. Region-scoped revision accounting (§2): count `w:ins`/`w:del` inside every part you will replace; any pending tracked change in a replaced header/footer gets baked into accepted-state text — predict it and flag it by name.
4. Identify placeholder slots in the template by their marker formatting, assert each slot's fragment concatenation equals the expected placeholder (§4), and write the extraction rule for each fill (§7). Hand-predict every fill value in the expected-diff list.
5. Plan the style import: list the template styles the transplant relies on, check `styleId`/`numId`/`abstractNumId` collisions (§1), decide what to drop (mc-ignorable extension elements, §6), and prove the host's docDefaults → Normal chain matches the template's for the properties that matter instead of importing Normal (§6).

## The transform

* Copy each header/footer part with its `.rels`, referenced media, and `[Content_Types].xml` override (§1).
* Collapse each slot to one run carrying the fill; `xml:space="preserve"` wherever a fill has edge spaces (§4).
* Pin spacing explicitly on every transplanted paragraph including table-cell paragraphs and on every imported style, merging into existing spacing elements (§6).
* Rescale right-aligned tab stops to the body section's text width where body pages reuse the cover design (§7).
* Leave the document's own revision-history table, body text, and pending tracked changes untouched; the predicted part set is the header/footer parts, their rels, media, `[Content_Types].xml`, `styles.xml` (if styles were imported), and nothing in `document.xml` beyond the `sectPr` references if they had to change.

## Verification specifics

* V7 geometry is mandatory on this job type — it is the check that catches "same words, wrong spacing" (§8), and the anchor words span header, cover, and footer.
* V2 scope includes every header/footer part, old and new names.
* V4 per region, with the baked-in changes (if any) listed by name in the report.
* After the transform, run `playbooks/page-numbering.md`'s diagnosis — the cover/body page-number symptom is a known consequence of this job.
* Multi-document jobs end with a cross-file sweep: rendered first pages, stamps, and header/footer geometry compared across every delivered file.
