"""Document profiles — what the compiler (and a human) needs to know about a batch, without a model and without
reading every file in full (docs/NL_SURGICAL_EDITOR_PLAN.md §2, Phase 3).

``profile(pkg)`` describes one file: section layout (page, margins, text width, titlePg, numbering) and what each
section's headers/footers show (part names as resolved through the rels, their display text and fields, any
template slots), settings, fonts (theme, docDefaults, direct fonts in use), the paragraph styles in use with their
effective font/size and a sample paragraph, numbering, counts, hygiene/whitespace signals, and the first paragraphs
of the body. It is bounded (``MAX_PROFILE_BYTES``): long text is truncated and samples are dropped before structure is.

``profile_batch(files)`` groups files by structure pattern (fingerprint.pattern_id) and profiles one
representative per pattern — the compiler sees one profile per pattern, not one per file.

The profile is descriptive only. Nothing in it is used to plan an edit: ops read the package themselves.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .fingerprint import fingerprint
from .ooxml import count_revisions, paragraphs
from .ops.effective import AmbiguousToggle, Resolver, text_width, theme_fonts
from .ops.hf_common import KINDS, TYPES, effective_refs
from .ops.selectors import StyleSheet
from .ops.slots import display_text, find_slots, nearest_p
from .package import NS, W, DocxPackage

MAX_PROFILE_BYTES = 6000
TEXT_CLIP = 90
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"


def _clip(s: str, n: int = TEXT_CLIP) -> str:
    """Clip text to ``n`` characters while preserving internal multiple spaces and non-breaking spaces (\\u00a0)
    so the compiler sees actual whitespace anomalies instead of a sanitized lie."""
    s = re.sub(r"[\r\n]+", " ", (s or "").strip())
    return s if len(s) <= n else s[:n - 1] + "…"


def _fields(root) -> List[str]:
    out = []
    for it in root.iter(W + "instrText"):
        word = (it.text or "").strip().split(" ")[0]
        if word and word.isupper() and word not in out:
            out.append(word)
    for fs in root.iter(W + "fldSimple"):
        word = (fs.get(W + "instr") or "").strip().split(" ")[0]
        if word and word not in out:
            out.append(word)
    return out


def _shown_text(p) -> str:
    """Display text with tabs shown as " ⇥ " (a header's left/right halves stay readable)."""
    out = []
    for r in p.iter(W + "r"):
        if nearest_p(r) is not p or any(a.tag in (W + "del", W + "moveFrom") for a in r.iterancestors()):
            continue
        for c in r:
            if c.tag == W + "t":
                out.append(c.text or "")
            elif c.tag in (W + "tab", W + "ptab"):
                out.append(" ⇥ ")
    return "".join(out)


def _part_view(pkg: DocxPackage, part: str) -> Dict[str, Any]:
    root = pkg.xml(part)
    lines = [_shown_text(p) for p in paragraphs(root) if nearest_p(p) is None]
    view: Dict[str, Any] = {"part": part, "text": [_clip(t) for t in lines if t.strip()][:6]}
    f = _fields(root)
    if f:
        view["fields"] = f
    slots = []
    for p in paragraphs(root):
        try:
            slots += [s.name for s in find_slots(p, ("highlight", "braces"), ("yellow",), part)]
        except Exception as e:                                    # a slot the op would refuse: say so
            slots.append(f"(unusable slot: {getattr(e, 'reason', e)})")
    if slots:
        view["slots"] = list(dict.fromkeys(slots))
    if any(True for _ in root.iter(WP + "docPr")):
        view["drawings"] = sum(1 for _ in root.iter(WP + "docPr"))
    if any(True for _ in root.iter(W + "tbl")):
        view["tables"] = sum(1 for _ in root.iter(W + "tbl"))
    revs = count_revisions(root)
    if revs:
        view["pending_revisions"] = revs
    return view


def _section(pkg: DocxPackage, i: int, sp, shown, own) -> Dict[str, Any]:
    sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
    g = lambda el, k: int(el.get(W + k)) if el is not None and el.get(W + k) not in (None, "") else None
    typ = sp.find(W + "type")
    title = sp.find(W + "titlePg")
    pg = sp.find(W + "pgNumType")
    out: Dict[str, Any] = {
        "index": i,
        "page_twips": {"w": g(sz, "w"), "h": g(sz, "h"), **({"orient": sz.get(W + "orient")} if sz is not None and sz.get(W + "orient") else {})},
        "margins_twips": {k: g(mar, k) for k in ("top", "right", "bottom", "left", "header", "footer", "gutter")},
        "text_width_twips": text_width(sp),
        "type": typ.get(W + "val") if typ is not None else "nextPage",
        "titlePg": title is not None and title.get(W + "val") not in ("0", "false", "off"),
    }
    if pg is not None and (pg.get(W + "start") or pg.get(W + "fmt")):
        out["page_numbering"] = {k: pg.get(W + k) for k in ("start", "fmt") if pg.get(W + k)}
    for kind in KINDS:
        shows: Dict[str, Any] = {}
        for typ_ in TYPES:
            part = shown.get((kind, typ_))
            if part is None:
                continue
            shows[typ_] = {"part": part, "own": (kind, typ_) in own}
        out[kind + "s"] = shows
    return out


def _style_entry(sheet: StyleSheet, res: Resolver, sid: str, count: int, sample_p) -> Dict[str, Any]:
    st = sheet.styles.get(sid)
    e: Dict[str, Any] = {"id": sid, "count": count}
    if st is not None:
        name = st.find(W + "name")
        if name is not None and name.get(W + "val") and name.get(W + "val") != sid:
            e["name"] = name.get(W + "val")
        based = st.find(W + "basedOn")
        if based is not None:
            e["basedOn"] = based.get(W + "val")
    else:
        e["missing_definition"] = True
    try:
        eff = res.run(None, sample_p)
        e["font"] = eff["ascii"] if eff["ascii"] == eff["hAnsi"] else f"{eff['ascii']} / {eff['hAnsi']}"
        e["size_pt"] = int(eff["sz"]) / 2
        if eff["b"]:
            e["bold"] = True
        if eff["caps"]:
            e["caps"] = True
    except AmbiguousToggle:
        e["font"] = "(toggle set at several style levels)"
    para = res.paragraph(sample_p)
    if para["jc"] != "left":
        e["jc"] = para["jc"]
    if para["before"] != "0" or para["after"] != "0":
        e["spacing_twips"] = {"before": int(para["before"]), "after": int(para["after"])}
    txt = display_text(sample_p)
    if txt.strip():
        e["sample"] = _clip(txt, 70)
    return e


def profile(pkg: DocxPackage, fp: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fp = fp or fingerprint(pkg)
    main = pkg.main_part()
    root = pkg.xml(main)
    secs = DocxPackage.sect_prs(root)
    shown = effective_refs(pkg, secs)
    sheet = StyleSheet(pkg)
    fonts = theme_fonts(pkg)
    res = Resolver(sheet, fonts)

    sections = []
    for i, sp in enumerate(secs):
        own = set()
        for el in sp:
            if el.tag in (W + "headerReference", W + "footerReference"):
                own.add(("header" if el.tag == W + "headerReference" else "footer", el.get(W + "type", "default")))
        sections.append(_section(pkg, i, sp, shown[i], own))
    parts_seen: Dict[str, Dict[str, Any]] = {}
    for s in sections:
        for kind in KINDS:
            for v in s[kind + "s"].values():
                if v["part"] not in parts_seen and pkg.has(v["part"]):
                    parts_seen[v["part"]] = _part_view(pkg, v["part"])

    body = [p for p in paragraphs(root) if nearest_p(p) is None]
    by_style: Dict[str, List[Any]] = {}
    numbered = 0
    for p in body:
        ps = p.find(f"{W}pPr/{W}pStyle")
        sid = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph", "(none)")
        by_style.setdefault(sid, []).append(p)
        if p.find(f"{W}pPr/{W}numPr") is not None:
            numbered += 1
    styles_in_use = []
    for sid, ps_ in sorted(by_style.items(), key=lambda kv: -len(kv[1]))[:25]:
        sample = next((p for p in ps_ if display_text(p).strip()), ps_[0])
        styles_in_use.append(_style_entry(sheet, res, sid, len(ps_), sample))

    direct = Counter()
    for part in pkg.text_parts():
        for rf in pkg.xml(part).iter(W + "rFonts"):
            if rf.getparent() is not None and rf.getparent().tag == W + "rPr" and rf.getparent().getparent() is not None \
                    and rf.getparent().getparent().tag == W + "r":
                for k in ("ascii", "hAnsi"):
                    if rf.get(W + k):
                        direct[rf.get(W + k)] += 1
                        break
                else:
                    if rf.get(W + "asciiTheme"):
                        direct["theme:" + rf.get(W + "asciiTheme")] += 1
    dd_rpr = sheet.root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr") if sheet.root is not None else None
    dd = {}
    if dd_rpr is not None:
        rf, sz = dd_rpr.find(W + "rFonts"), dd_rpr.find(W + "sz")
        if rf is not None:
            dd["fonts"] = {etree_local(k): v for k, v in rf.attrib.items()}
        if sz is not None:
            dd["size_pt"] = int(sz.get(W + "val")) / 2

    counts = {
        "paragraphs": len(body),
        "empty_paragraphs": sum(1 for p in body if not display_text(p).strip()),
        "tables": sum(1 for _ in root.iter(W + "tbl")),
        "drawings": sum(1 for part in pkg.text_parts() for _ in pkg.xml(part).iter(WP + "docPr")),
        "textboxes": sum(1 for part in pkg.text_parts() for _ in pkg.xml(part).iter("{%s}AlternateContent" % NS["mc"])),
        "numbered_paragraphs": numbered,
        "pending_revisions": count_revisions(root),
    }
    kinds = pkg.parts_by_kind()
    for k in ("footnotes", "endnotes", "comments"):
        if kinds.get(k):
            counts[k + "_parts"] = kinds[k]
    first = [_clip(_shown_text(p)) for p in body if display_text(p).strip()][:8]

    prof: Dict[str, Any] = {
        "file": Path(pkg.name).name,
        "pattern_id": fp["pattern_id"],
        "settings": {"evenAndOddHeaders": pkg.settings_flag("evenAndOddHeaders"),
                     "trackRevisions": pkg.settings_flag("trackRevisions")},
        "sections": sections,
        "header_footer_parts": parts_seen,
        "fonts": {"theme": fonts, "doc_defaults": dd, "direct_in_runs": dict(direct.most_common(10))},
        "styles_in_use": styles_in_use,
        "counts": counts,
        "first_paragraphs": first,
    }
    hygiene = fp.get("body_hygiene") or {}
    anomalies = {}
    if hygiene.get("has_nbsp"):
        anomalies["has_nbsp"] = True
    if hygiene.get("has_multi_space"):
        anomalies["has_multi_space"] = True
    if hygiene.get("section_anchor_shape") and hygiene["section_anchor_shape"] != "standalone":
        anomalies["section_anchor_shape"] = hygiene["section_anchor_shape"]
    if hygiene.get("style_regime"):
        anomalies["style_regime"] = hygiene["style_regime"]
    if hygiene.get("numbering_regime") and hygiene["numbering_regime"] != "manual_text":
        anomalies["numbering_regime"] = hygiene["numbering_regime"]
    if hygiene.get("run_fragmentation_ratio", 0) > 2.5:
        anomalies["run_fragmentation_ratio"] = hygiene["run_fragmentation_ratio"]
    if anomalies not in ({}, {"style_regime": "normal_only"}) and anomalies != {"style_regime": "styled"}:
        prof["hygiene"] = anomalies
    return bound(prof)


def etree_local(qname: str) -> str:
    return qname.split("}", 1)[1] if "}" in qname else qname


def bound(prof: Dict[str, Any], limit: int = MAX_PROFILE_BYTES) -> Dict[str, Any]:
    """Shrink until the JSON fits ``limit`` — always. Samples go first (silently), then structure, each structural
    cut recorded in ``truncated`` (what was omitted), and as a last resort only the file's identity, settings,
    counts and the omission list remain."""
    omitted: List[str] = []

    def size():
        return len(json.dumps(prof, ensure_ascii=False))

    def cut_sections(n):
        secs = prof["sections"]
        if len(secs) > n:
            omitted.append(f"sections {n}–{len(secs) - 1} ({len(secs) - n} omitted)")
            prof["sections"] = secs[:n]

    def cut_parts(n):
        parts = prof["header_footer_parts"]
        if len(parts) > n:
            omitted.append(f"{len(parts) - n} header/footer part view(s)")
            prof["header_footer_parts"] = dict(list(parts.items())[:n])

    def drop_part_text():
        if any(v.get("text") for v in prof["header_footer_parts"].values()):
            omitted.append("header/footer text")
        for v in prof["header_footer_parts"].values():
            v.pop("text", None)

    def thin_styles():
        if len(prof["styles_in_use"]) > 6:
            omitted.append(f"{len(prof['styles_in_use']) - 6} style(s) in use")
        prof["styles_in_use"] = [{k: st[k] for k in ("id", "count", "font", "size_pt") if k in st}
                                 for st in prof["styles_in_use"][:6]]

    steps = [
        lambda: prof.__setitem__("first_paragraphs", prof["first_paragraphs"][:3]),
        lambda: [st.pop("sample", None) for st in prof["styles_in_use"]],
        lambda: prof.__setitem__("styles_in_use", prof["styles_in_use"][:12]),
        lambda: [v.__setitem__("text", v["text"][:2]) for v in prof["header_footer_parts"].values() if "text" in v],
        lambda: prof.__setitem__("first_paragraphs", []),
        lambda: prof["fonts"].__setitem__("direct_in_runs", dict(list(prof["fonts"]["direct_in_runs"].items())[:4])),
        lambda: prof.pop("hygiene", None),
        drop_part_text,
        thin_styles,
        lambda: cut_sections(6),
        lambda: cut_parts(8),
        lambda: cut_sections(2),
        lambda: cut_parts(3),
    ]
    for step in steps:
        prof["truncated"] = omitted
        if size() <= limit:
            break
        step()
    prof["truncated"] = omitted
    if size() > limit:
        keep = {k: prof[k] for k in ("file", "pattern_id", "settings", "counts") if k in prof}
        prof.clear()
        prof.update(keep)
        prof["truncated"] = omitted + ["everything but identity, settings and counts (profile limit)"]
        for k in ("counts", "settings"):              # an absurdly small limit: identity only
            if size() <= limit:
                break
            prof.pop(k, None)
        if size() > limit:
            prof["truncated"] = [f"{len(omitted) + 1} omission(s) (profile limit)"]
        if size() > limit:
            prof["file"] = _clip(prof.get("file", ""), 24)
    if not prof["truncated"]:
        prof.pop("truncated")
    return prof


def profile_batch(files: Sequence[Path]) -> Dict[str, Any]:
    """{"files": n, "patterns": [{"pattern_id", "count", "files": [names], "profile": <representative>}]} — the
    most common pattern first."""
    groups: Dict[str, List[Path]] = {}
    unreadable: List[Dict[str, str]] = []
    for f in files:                       # one package open at a time: memory does not grow with the batch
        try:
            pkg = DocxPackage.from_path(f)
            pid = fingerprint(pkg)["pattern_id"]
        except Exception as e:  # noqa: BLE001 — an unreadable file is listed, not fatal
            unreadable.append({"file": Path(f).name, "error": f"{type(e).__name__}: {e}"})
            continue
        finally:
            pkg = None
        groups.setdefault(pid, []).append(Path(f))
    patterns = []
    for pid, fs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        rep = DocxPackage.from_path(fs[0])            # only the representative is reopened
        patterns.append({"pattern_id": pid, "count": len(fs), "files": [f.name for f in fs], "profile": profile(rep)})
    out: Dict[str, Any] = {"files": len(files), "patterns": patterns}
    if unreadable:
        out["unreadable"] = unreadable
    return out
