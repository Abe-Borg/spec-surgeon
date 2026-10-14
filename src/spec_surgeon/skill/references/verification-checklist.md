# Verification suite — mandatory checklist

Non-negotiable, all of it — the specification every session's fresh implementation must satisfy in full, under the independence and predict-first disciplines in SKILL.md. Every check is run; a check that does not apply to a job is recorded as N/A with the reason, never silently dropped. Each check records its actual numbers, because the filled-in checklist is pasted into the report and is the durable audit artifact after the scripts evaporate.

The verifier is a separate script sharing no helper code with the transform, reading only the before/after packages. The expected-diff list (V2), expected revision deltas (V4), and intended part set (V1) are written BEFORE the transform runs.

## The record

Fill this in literally. One record per output file.

```
VERIFICATION RECORD — <output file name>
Source:   <path>  size <bytes>  mtime <value>  md5 <hash>
Output:   <path>  size <bytes>  md5 <hash>
Mode:     formatting | content | untracked
Job type: <playbook name or "general">
Text-preservation scope (parts): document.xml, <header/footer parts by resolved name>, footnotes.xml, endnotes.xml, comments.xml, ...

Predicted diff list (written before the transform ran):
  D1. <part> / <block locator>: "<before>" → "<after>"
  D2. ...
Predicted part changes: {<part>, <part>, ...}
Predicted revision delta: w:ins +<n>, w:del +<n>  (per region if more than one)

V1 Package hash whitelist ............ PASS | FAIL
    changed parts: {...}   intended: {...}   added: none   removed: none
V2 Text preservation ................. PASS | FAIL
    blocks compared: <n>   diffs found: <n>   matches predicted list exactly: yes/no
V3 Structural children ............... PASS | FAIL
    w:tab (in w:r only) pre/post: n/n   w:br: n/n   w:noBreakHyphen: n/n
V4 Revision accounting ............... PASS | FAIL
    w:ins pre/post: n/n (Δ+n, predicted +n)   w:del pre/post: n/n (Δ+n, predicted +n)
V5 XSD + redlining validator ......... PASS | FAIL
    source baseline errors: <n> (<list>)   output errors: <n>   new vs baseline: 0
    --author "<name>" run: yes/no (required on tracked jobs)   result: <text>
V6 Render and look ................... PASS | FAIL
    renderer: <soffice | Word>   pages viewed: <list>   programmatic audit: <what, result>
V7 Geometry .......................... PASS | FAIL | N/A (<reason>)
    anchors: <n>   max |Δy| vs template: <pt>
V8 Accept / reject simulation ........ PASS | FAIL | N/A (<reason>)
    accept view == expected: yes/no   reject view == original: yes/no   oracle: <xml sim | pandoc | both>
V9 Overwrite guard ................... PASS | FAIL
    source re-checked: size <bytes> mtime <value> (unchanged / drifted→rebased)   Word lock file present: no
```

## The checks

### V1 — Package hash whitelist

Re-hash every file in the package; assert the changed set ⊆ the parts you intended to change, nothing added/removed unexpectedly. Catches: a transform that touched a part it had no business touching, repack side-effects (`zip -r` directory entries), dropped members (`[trash]/*.dat`, `customXml/`, media). Record the changed, intended, added, and removed sets.

### V2 — Text preservation

Extract per-block text from every text-bearing part the transform touches — `document.xml` plus headers/footers, footnotes/endnotes, comments, and textbox content as applicable — concatenating both `w:t` and `w:delText` so tracked-deleted text counts — pre vs post; assert byte-identity except an explicitly enumerated diff list, and assert that list exactly (count and content), not "roughly". Textbox text inside `mc:AlternateContent` exists twice (Choice + Fallback); both branches are in scope. Record the block count, the number of diffs found, and whether they match the predicted list one-for-one.

### V3 — Structural children

Count structural children per block (`w:tab`, `w:br`, `w:noBreakHyphen`): they render as characters but never appear in `w:t`, so a `w:t`-only check cannot see one being dropped. When counting structural children, count only `w:tab` whose parent is `w:r` — the tab-stop definitions in `pPr/w:tabs` use the same element name and silently inflate the count. Record pre/post counts per element type.

### V4 — Tracked-change accounting

Count `<w:ins ` / `<w:del ` pre vs post; assert the exact expected delta. On jobs that remove or replace regions containing tracked changes, count per region and predict the exact post-transform totals, so every revision that disappears is deliberate and on the record. Replacing a header/footer part wholesale bakes any pending tracked change inside it into accepted-state plain text — flag it by name. Expected deltas per edit shape are tabulated in `playbooks/content-redlines.md`.

### V5 — XSD + redlining validator

`validate.py out.docx --original src.docx`; add `--author "<tracked-change author>"` on tracked jobs (it undoes the new revisions and compares text to the original — any text changed outside a tracked revision is reported; this is the failure mode that would end careers). Run it on the SOURCE files too, and judge outputs only on errors the source doesn't already have — real files ship with inert junk that isn't yours to fix (`[trash]/*.dat` zip members, duplicate documenttasks ids): preserve it byte-identically, report it, move on. Record the source baseline error list and the output error list; the assertion is "no new errors".

When `validate.py` is not available, `environments.md` says what to substitute; the XSD half and the redlining half are both required.

### V6 — Render and look

soffice → pdf → `pdftoppm -jpeg` → actually view pages (the pages that changed and the pages around them; substitutes per environment in `environments.md`). `pdftotext -layout` for programmatic audits (e.g., regex the article numbers and assert per-part sequences are 1..n with no gaps). `pdftotext` is a structure auditor, not character truth — it mangles ligatures and dashes, so never "fix" a glyph oddity seen only in its output; character truth is the XML, visual truth is the rendered image. LibreOffice page counts are not Word's; a page-count delta in the proof render is not evidence of body change. Record which pages were viewed and what the programmatic audit asserted.

### V7 — Geometry assertion

Render the template and every output, pick ~10 anchor words spanning header, cover, and footer, and assert y-position deltas vs the template render ≈ 0.0pt (`pdftotext -bbox`). This catches the class of defect the text checks are blind to: identical content, wrong spacing. N/A is legitimate only when the job has no layout reference (no template, no "should look like X" file) — say so.

### V8 — Accept / reject simulation

For merges: simulate accept-all and reject-all views from the XML and assert accept == new source content, reject == original char-for-char. The same two simulations apply to redlines that edit inside an existing tracked insertion, with pandoc `--track-changes=accept|reject` as the independent oracle; LibreOffice (renders and `accept_changes.py`) is NOT an oracle for nested revisions. N/A is legitimate only for jobs with zero revision delta (V4 Δ = 0/0).

### V9 — Overwrite guard

Immediately before writing back: re-check the source's size and mtime against the values recorded at acquisition; confirm no `~$<name>.docx` lock file beside it. If drifted: re-acquire, rebase, re-verify (V1–V8 again), and only then write back against the fresh identity. Record both the re-checked identity and the lock-file result.

## When a check fails

Verifier bugs look exactly like transform bugs. Root-cause by reading the actual strings before touching either side; a corrected check must be at least as strict as the one it replaces, and the transform must pass an independent route (accept view, pandoc) before you conclude the verifier was the one at fault. Never weaken a check to pass; never explain a failure away.
