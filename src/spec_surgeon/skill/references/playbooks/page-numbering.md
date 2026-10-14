# Playbook — page numbering across cover/body sections

Recurring symptom after cover/body templating: the cover footer shows `<section number> - 1` and the first body page also shows `- 1`, then 2, 3… Cause: the cover is section 1 with the `{PAGE}` field in its own footer part, and the first body section carries `<w:pgNumType w:start="1"/>` (restart). Ask which behavior is wanted (one question), then do ONE of:

* Cover unnumbered (typical for spec covers): resolve the cover section's default `footerReference` → its footer part; delete just the right-aligned paragraph containing the PAGE field (`fldChar`/`instrText " PAGE "`) from that cover footer part only. Body footer untouched; body still starts at 1.
* Continuous numbering (cover=1, body starts at 2): in `document.xml`, find the first body section's `sectPr` and delete its `<w:pgNumType w:start="1"/>` element. Footers untouched.

Either way, the full pipeline applies: acquire fresh (mtimes move; Word may have renamed parts — resolve via rels), snapshot, transform, run the whole verification suite (the continuous-numbering option has zero legitimate text diffs in `document.xml`; the unnumbered-cover option touches only a footer part), render the first pages and actually look at the corners, deliver, write back with the fresh identity.

## Generalization [unproven]

The same diagnosis applies to any multi-section document: for each section, read its `sectPr` for `pgNumType` (`start`, `fmt`, `chapStyle`) and its footer references (own vs inherited — `element-types.md` → Sections), and for each footer part find the PAGE/NUMPAGES fields. Lay the table out (section → footer part → fields → restart) before proposing a change; the fix is always one deletion or one attribute change in exactly one place.
