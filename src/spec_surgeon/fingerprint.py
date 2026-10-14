"""Structure fingerprint — milliseconds per file, computed before anything else.

Two outputs:
* ``details``   — everything a planner or a human needs to decide whether a file is "the usual",
                  including ``body_hygiene`` (style regime, numbering regime, section-anchor shape,
                  run fragmentation, and whitespace/revision anomalies).
* ``pattern_id`` — a short hash of the structural facts that decide whether the proven transform
  applies (section layout, header/footer wiring, field kinds, revision presence, and body structural
  regime). Files sharing a pattern_id with a file that passed verification are the fast path; novel
  pattern_ids are surfaced before the batch runs so exceptions are found up front, not after.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, List

from lxml import etree

from .package import NS, W, DocxPackage

_PAGE_FIELD = re.compile(r"\bPAGE\b")
_REF_FIELD = re.compile(r"\b(REF|SEQ|TOC|HYPERLINK|NUMPAGES|SECTIONPAGES|STYLEREF)\b")
_MANUAL_NUM_RE = re.compile(r"^\s*(?:PART\s+(?:\d+|[IVX]+)|\d+\.\d+(?:\.\d+)?|[A-Z]\.|\d+\.)(?:[\s\t\u00a0]+|$)")
_SECTION_STANDALONE_RE = re.compile(r"^\s*SECTION[\s\u00a0]+\d{2}[\s\u00a0]*\d{2}[\s\u00a0]*\d{2}\s*$", re.IGNORECASE)
_SECTION_INLINE_RE = re.compile(r"^\s*SECTION[\s\u00a0]+\d{2}[\s\u00a0]*\d{2}[\s\u00a0]*\d{2}[\s\u00a0]*[-–—:]+[\s\u00a0]*\S+", re.IGNORECASE)
_SECTION_ANY_RE = re.compile(r"\bSECTION[\s\u00a0]+\d{2}[\s\u00a0]*\d{2}[\s\u00a0]*\d{2}\b", re.IGNORECASE)


def _field_kinds(root: etree._Element) -> List[str]:
    kinds = set()
    for it in root.iter(W + "instrText"):
        txt = (it.text or "").strip()
        for m in re.finditer(r"\b([A-Z]{2,})\b", txt):
            kinds.add(m.group(1))
            break
    for fs in root.iter(W + "fldSimple"):
        txt = (fs.get(W + "instr") or "").strip()
        m = re.match(r"\s*([A-Z]{2,})", txt)
        if m:
            kinds.add(m.group(1))
    return sorted(kinds)


def _count(root: etree._Element, tag: str) -> int:
    return sum(1 for _ in root.iter(W + tag))


def _live_p_text(p: etree._Element) -> str:
    out = []
    for r in p.iter(W + "r"):
        q = r.getparent()
        nested_or_del = False
        while q is not None and q is not p:
            if q.tag in (W + "p", W + "del", W + "moveFrom"):
                nested_or_del = True
                break
            q = q.getparent()
        if nested_or_del:
            continue
        for c in r:
            if c.tag == W + "t" and c.text:
                out.append(c.text)
            elif c.tag in (W + "tab", W + "ptab"):
                out.append("\t")
    return "".join(out)


def _body_hygiene(doc: etree._Element) -> Dict[str, Any]:
    style_counts: Counter = Counter()
    non_empty_p = 0
    total_runs = 0
    numpr_count = 0
    manual_num_count = 0
    has_nbsp = False
    has_multi_space = False
    anchor_shapes: List[str] = []

    for p in doc.iter(W + "p"):
        # skip nested textbox paragraphs for top-level body structural metrics
        q = p.getparent()
        in_nested_p = False
        in_table = False
        while q is not None:
            if q.tag == W + "p":
                in_nested_p = True
                break
            if q.tag == W + "tc":
                in_table = True
            q = q.getparent()
        if in_nested_p:
            continue
        ps = p.find(f"{W}pPr/{W}pStyle")
        sid = ps.get(W + "val") if ps is not None and ps.get(W + "val") else "Normal"
        txt = _live_p_text(p)
        if "\u00a0" in txt:
            has_nbsp = True
        if "  " in txt.replace("\t", " "):
            has_multi_space = True
        runs = [r for r in p.iter(W + "r") if r.getparent() is p or (r.getparent() is not None and r.getparent().getparent() is p)]
        if txt.strip():
            non_empty_p += 1
            total_runs += len(runs)
            style_counts[sid] += 1
            if p.find(f"{W}pPr/{W}numPr") is not None:
                numpr_count += 1
            elif _MANUAL_NUM_RE.match(txt):
                manual_num_count += 1
            prefix = "in_table_" if in_table else ""
            if _SECTION_STANDALONE_RE.match(txt):
                anchor_shapes.append(prefix + "standalone")
            elif _SECTION_INLINE_RE.match(txt):
                anchor_shapes.append(prefix + "inline")
            elif _SECTION_ANY_RE.search(txt):
                anchor_shapes.append(prefix + "embedded")

    custom_styles = sorted(s for s in style_counts if s != "Normal")
    normal_ratio = round(style_counts.get("Normal", 0) / max(1, non_empty_p), 2)
    style_regime = "styled" if custom_styles and normal_ratio < 0.9 else "normal_only"

    if numpr_count and manual_num_count:
        numbering_regime = "mixed"
    elif numpr_count:
        numbering_regime = "numPr"
    elif manual_num_count:
        numbering_regime = "manual_text"
    else:
        numbering_regime = "none"

    section_anchor_shape = anchor_shapes[0] if anchor_shapes else "missing"
    dominant_style = style_counts.most_common(1)[0][0] if style_counts else "Normal"
    frag_ratio = round(total_runs / max(1, non_empty_p), 2)

    return {
        "style_regime": style_regime,
        "dominant_style": dominant_style,
        "normal_ratio": normal_ratio,
        "custom_styles": custom_styles[:12],
        "numbering_regime": numbering_regime,
        "section_anchor_shape": section_anchor_shape,
        "run_fragmentation_ratio": frag_ratio,
        "has_nbsp": has_nbsp,
        "has_multi_space": has_multi_space,
        "has_rPrChange_in_body": _count(doc, "rPrChange") > 0,
        "has_pPrChange_in_body": _count(doc, "pPrChange") > 0,
        "has_proof_err": _count(doc, "proofErr") > 0,
    }


def fingerprint(pkg: DocxPackage) -> Dict[str, Any]:
    main = pkg.main_part()
    doc = pkg.xml(main)
    sections = pkg.sections()
    parts = pkg.text_parts()

    per_part: Dict[str, Dict[str, Any]] = {}
    for part in parts:
        root = pkg.xml(part)
        per_part[part] = {
            "paragraphs": _count(root, "p"),
            "ins": _count(root, "ins"),
            "del": _count(root, "del"),
            "fields": _field_kinds(root),
            "textboxes": sum(1 for _ in root.iter("{%s}AlternateContent" % NS["mc"])),
            "sdt": _count(root, "sdt"),
        }

    hygiene = _body_hygiene(doc)

    details = {
        "file": pkg.name,
        "main_part": main,
        "sections": [
            {
                "index": s.index,
                "headers": s.header_refs,
                "footers": s.footer_refs,
                "titlePg": s.title_pg,
                "pgNumStart": s.pg_num_start,
                "pgNumFmt": s.pg_num_fmt,
            }
            for s in sections
        ],
        "evenAndOddHeaders": pkg.settings_flag("evenAndOddHeaders"),
        "trackRevisions": pkg.settings_flag("trackRevisions"),
        "parts": per_part,
        "body_hygiene": hygiene,
        "has_comments": any(p.endswith("comments.xml") for p in parts),
        "has_footnotes": any(p.endswith("footnotes.xml") for p in parts),
        "junk_members": [m for m in pkg.member_names if m.startswith("[trash]") or m.endswith(".dat")],
        "member_count": len(pkg.member_names),
    }

    # Pattern: structure only (not counts that vary file to file), so files from the same template
    # family group together, while files with different body structure / anchor shapes separate.
    def _fields_of(refs: Dict[str, str]) -> Dict[str, List[str]]:
        return {typ: per_part.get(part, {}).get("fields", []) for typ, part in sorted(refs.items())}

    pattern_src: Dict[str, Any] = {
        "sections": [
            {
                "headers": _fields_of(s.header_refs),
                "footers": _fields_of(s.footer_refs),
                "titlePg": s.title_pg,
                "pgNumStart": s.pg_num_start is not None,
            }
            for s in sections
        ],
        "evenAndOddHeaders": details["evenAndOddHeaders"],
        "has_pending_revisions_in_hf": any(
            (d["ins"] or d["del"]) for p, d in per_part.items() if "header" in p or "footer" in p
        ),
        "has_textboxes_in_hf": any(d["textboxes"] for p, d in per_part.items() if "header" in p or "footer" in p),
    }
    # Include body structural regime when it differs from the default baseline so messy/divergent body
    # layouts never hide behind the same pattern_id as standard specs.
    body_sig = (hygiene["style_regime"], hygiene["numbering_regime"], hygiene["section_anchor_shape"])
    if body_sig != ("normal_only", "manual_text", "standalone"):
        pattern_src["body_structure"] = {
            "style_regime": hygiene["style_regime"],
            "numbering_regime": hygiene["numbering_regime"],
            "section_anchor_shape": hygiene["section_anchor_shape"],
            "custom_styles": hygiene["custom_styles"],
        }
    pattern_id = hashlib.sha1(json.dumps(pattern_src, sort_keys=True).encode()).hexdigest()[:10]
    details["pattern_id"] = pattern_id
    details["pattern"] = pattern_src
    return details
