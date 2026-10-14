VERIFICATION RECORD — 21 05 00 (1) – COMMON WORK RESULTS FOR FIRE SUPPRESSION.docx
Source:   /tmp/claude-0/-home-claude/74d4df4a-761a-5522-907d-7c75fe02aec7/scratchpad/fx60/21 05 00 (1) – COMMON WORK RESULTS FOR FIRE SUPPRESSION.docx
Output:   /tmp/claude-0/-home-claude/74d4df4a-761a-5522-907d-7c75fe02aec7/scratchpad/out60/outputs/21 05 00 (1) – COMMON WORK RESULTS FOR FIRE SUPPRESSION.docx
Mode:     content    Job: tracked-replace
Predicted part changes: ['word/document.xml']
Predicted revision delta: ins +5, del +4

Predicted diff list (written before the transform ran):
  D1. word/document.xml / block 12: "A.The Owner's insurer is FM Global and all equipment shall be FM Approved." → "A.The Owner's insurer is FM Globalthe Owner's risk consultant – GRC (Global Risk Consultants) and all equipment shall be FM Approved."
       (1 occurrence(s); tracked)
  D2. word/document.xml / block 13: 'B.The auditor is FM Global and reviews the hydraulic calculations.' → "B.The auditor is FM Globalthe Owner's risk consultant – GRC (Global Risk Consultants) and reviews the hydraulic calculations."
       (1 occurrence(s); tracked)
  D3. word/document.xml / block 14: "C.Sprinklers: UL listed and FM Approved per the Owner's insurer FM Global requirements as indicated." → "C.Sprinklers: UL listed and FM Approved per the Owner's insurer FM Globalthe Owner's risk consultant – GRC (Global Risk Consultants) requirements as indicated."
       (1 occurrence(s); tracked)
  D4. word/document.xml / block 16: 'E.See Section  REF _Ref123 \\h 21 10 00 for FM Global coordination.' → "E.See Section  REF _Ref123 \\h 21 10 00 for FM Globalthe Owner's risk consultant – GRC (Global Risk Consultants) coordination."
       (1 occurrence(s); tracked)

V1 Package hash whitelist............ PASS
    changed ['word/document.xml'] ⊆ intended ['word/document.xml']: yes; added none; removed none; member order kept
V2 Text preservation................. PASS
    blocks compared: 49 across 4 part(s); diffs found: 4; matches predicted list exactly: yes
V3 Structural children............... PASS
    w:tab(in w:r)/w:br/w:cr/noBreakHyphen/sym/fldChar/… per block: identical on every aligned block, or changed exactly as the plan declared
V4 Revision accounting............... PASS
    w:ins 1→6 (Δ+5, predicted +5); w:del 1→5 (Δ+4, predicted +4); ids unique
V5a XSD vs source baseline............ PASS
    validated 2 changed part(s); baseline errors {'[Content_Types].xml': 0, 'word/document.xml': 0}; new errors: 0
V5b Untracked-edit check (reject view) PASS
    reject-all view of output == reject-all view of source, char-for-char: yes
V5c Revision grammar.................. PASS
    w:delText only inside w:del, no w:t inside w:del, id+author on every wrapper, xml:space on edge whitespace: ok
V8 Accept / reject simulation........ PASS
    accept-all == predicted accepted view: yes; reject-all == original: yes; oracle: XML simulation
V6 Render and look................... PASS
    renderer libreoffice; pages 3→3; rasterized pages [1, 2, 3] → pages/; page-count delta is not evidence of body change (XML checks are character truth)
V7 Geometry assertion................ NA
    N/A for a content edit (text reflows after the change). Informational: max |Δy| 39.60pt, max |Δx| 277.32pt; pages with movement [2, 3]; pages 3→3
V9 Overwrite guard................... PASS
    source unchanged since acquisition; output written to /tmp/claude-0/-home-claude/74d4df4a-761a-5522-907d-7c75fe02aec7/scratchpad/out60/outputs/21 05 00 (1) – COMMON WORK RESULTS FOR FIRE SUPPRESSION.docx

RESULT: PASS

Judgment calls:
  - 4 occurrence(s) of 'FM Global' → "the Owner's risk consultant – GRC (Global Risk Consultants)" (tracked) across 1 part(s).
  - Revision ids start at 102; author 'Abraham Borg'; w16du:dateUtc written.