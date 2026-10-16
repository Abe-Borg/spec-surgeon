"""Document profiles — what the compiler (and a human) needs to know about a batch, without a model and without
reading every file in full (docs/NL_SURGICAL_EDITOR_PLAN.md §2, Phase 3).

``profile(pkg)`` describes one file: section layout (page, margins, text width, titlePg, numbering) and what each
section's headers/footers show (part names as resolved through the rels, their display text and fields, any
template slots), settings, fonts (theme, docDefaults, direct fonts in use), the paragraph styles in use with their
effective font/size and a sample paragraph, numbering, counts, hygiene/whitespace signals, and the first paragraphs
of the body. It is bounded (``MAX_PROFILE_BYTES``): long text is truncated and samples are dropped before structure is.

``profile_batch(files)`` groups files by structure pattern (fingerprint.pattern_id) and profiles one
representative per pattern — the compiler sees one profile per pattern, not one per file.

``fonts.body_text`` is what "the document's font" means to a reader: the body text's effective font and size by
share of characters. ``template_spec_summary(pkg)`` is what a template file offers the compiler: prepared slots, or
(an ordinary spec from another firm) its own section number/title by the catalog's ``REFERENCE_FILLS`` and every
place its headers/footers show them, with any refusal reference slots would meet there; its body text font/size.

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
from .ops.base import JobException
from .ops.slots import display_text, extract_fill, field_runs, find_slots, mark_slots, nearest_p, value_tokens
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


def _int(v, default=None):
    """An attribute's integer, or ``default`` when it is missing or not a number (a file Word tolerates must not
    stop the profile — the profile is descriptive, and the file still fingerprints and plans)."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _section(pkg: DocxPackage, i: int, sp, shown, own) -> Dict[str, Any]:
    sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
    g = lambda el, k: _int(el.get(W + k)) if el is not None and el.get(W + k) not in (None, "") else None
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
        e["size_pt"] = _int(eff["sz"], 0) / 2
        if eff["b"]:
            e["bold"] = True
        if eff["caps"]:
            e["caps"] = True
        if eff["i"]:
            e["italic"] = True
        val, theme, shade, tint = eff["color"]
        if val not in (None, "auto") or theme:
            e["color"] = val or "auto"                 # explicit, or the RGB Word cached for the theme colour
            if theme:                                  # Word renders the theme colour (shade/tint applied), not val
                e["color_theme"] = theme + (f" shade {shade}" if shade else "") + (f" tint {tint}" if tint else "")
    except AmbiguousToggle:
        e["font"] = "(toggle set at several style levels)"
    para = res.paragraph(sample_p)
    if para["jc"] != "left":
        e["jc"] = para["jc"]
    if para["before"] != "0" or para["after"] != "0":
        e["spacing_twips"] = {"before": _int(para["before"], 0), "after": _int(para["after"], 0)}
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
            dd["size_pt"] = _int(sz.get(W + "val"), 0) / 2

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
        "fonts": {"theme": fonts, "doc_defaults": dd, "direct_in_runs": dict(direct.most_common(10)),
                  "body_text": body_text_fonts(pkg)},
        "styles_in_use": styles_in_use,
        "counts": counts,
        "first_paragraphs": first,
    }
    try:
        marked = markup(pkg)
    except Exception as e:  # noqa: BLE001 — descriptive only: a file the summary cannot read still profiles
        marked = {"error": f"{type(e).__name__}: {e}"}
    if marked:
        prof["markup"] = marked
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


W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
VML = "{urn:schemas-microsoft-com:vml}"
COMMENTS_EX_REL = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
APP_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
CUSTOM_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
DC = "{http://purl.org/dc/elements/1.1/}"
CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"


def _package_part(pkg: DocxPackage, rel_type: str) -> Optional[str]:
    for r in pkg.rels("").values() if pkg.has("_rels/.rels") else []:
        if r.type == rel_type and r.resolved and pkg.has(r.resolved):
            return r.resolved
    return None


def markup(pkg: DocxPackage) -> Dict[str, Any]:
    """What built-in batch 2's clean-up operations would find (descriptive; the ops read the file themselves):
    comments (count, resolved threads, authors), pending tracked changes by author (and the dates they span), highlight
    colours, hidden runs, hyperlinks, field types, a watermark's words, direct run colours, the document properties
    and whether the file is marked final. Only what the file has is listed."""
    from .ops.fields import fields_of
    from .ops.markup import Look
    from .ooxml import revision_kind
    out: Dict[str, Any] = {}
    kinds = pkg.parts_by_kind()
    look = Look(StyleSheet(pkg))
    comments = kinds.get("comments") or []
    if comments:
        croot = pkg.xml(comments[0])
        authors = Counter(c.get(W + "author") or "?" for c in croot.findall(W + "comment"))
        if authors:
            entry: Dict[str, Any] = {"count": sum(authors.values()), "authors": dict(authors.most_common(6))}
            ex = [r.resolved for r in pkg.rels(pkg.main_part()).values()
                  if r.type == COMMENTS_EX_REL and r.resolved and pkg.has(r.resolved)]
            if ex:
                done = sum(1 for e in pkg.xml(ex[0]).iter(W15 + "commentEx")
                           if e.get(W15 + "done") in ("1", "true", "on") and not e.get(W15 + "paraIdParent"))
                entry["resolved_threads"] = done
            out["comments"] = entry
    revs: Dict[str, Counter] = {}
    dates: List[str] = []
    highlight: Counter = Counter()
    colors: Counter = Counter()
    hidden = 0
    links = Counter()
    fields: Counter = Counter()
    for part in pkg.text_parts():
        root = pkg.xml(part)
        for el in root.iter():
            k = revision_kind(el)
            if k:
                revs.setdefault(el.get(W + "author") or "?", Counter())[k] += 1
                if el.get(W + "date"):
                    dates.append(el.get(W + "date")[:10])
        if part in comments:
            continue
        for h in root.iter(W + "highlight"):
            holder = h.getparent().getparent() if h.getparent() is not None else None
            if holder is not None and holder.tag in (W + "r", W + "pPr") and h.get(W + "val") not in (None, "none"):
                highlight[h.get(W + "val")] += 1
        for p in paragraphs(root):
            for r in p.findall(f".//{W}r"):
                if nearest_p(r) is not p or not any(c.tag == W + "t" for c in r):
                    continue
                rpr = r.find(W + "rPr")
                c = rpr.find(W + "color") if rpr is not None else None
                if c is not None and (c.get(W + "val") or "auto").lower() != "auto":
                    colors[c.get(W + "themeColor") or c.get(W + "val")] += 1
                try:
                    if look.hidden(rpr, p):
                        hidden += 1
                except Exception:  # noqa: BLE001 — descriptive only
                    pass
        for hl in root.iter(W + "hyperlink"):
            links["external" if hl.get(f"{{{NS['r']}}}id") else "internal"] += 1
        for f in fields_of(root):
            fields[f.type or "?"] += 1
            if f.type == "HYPERLINK":
                links["field"] += 1
    if revs:
        out["tracked_changes"] = {a: dict(c) for a, c in sorted(revs.items(), key=lambda kv: -sum(kv[1].values()))[:6]}
        if dates:
            out["tracked_change_dates"] = [min(dates), max(dates)]
    if highlight:
        out["highlight_colors"] = dict(highlight.most_common(8))
    if hidden:
        out["hidden_runs"] = hidden
    if colors:
        out["run_colors"] = dict(colors.most_common(6))
    if links:
        out["hyperlinks"] = dict(links)
    if fields:
        out["fields"] = dict(fields.most_common(10))
    marks = []
    for part in kinds.get("headers") or []:
        for shape in pkg.xml(part).iter(VML + "shape"):
            sid = shape.get("id") or ""
            if sid.startswith("PowerPlusWaterMarkObject"):
                tp = shape.find(VML + "textpath")
                marks.append(_clip(tp.get("string") if tp is not None else "", 40))
            elif sid.startswith("WordPictureWatermark"):
                marks.append("(picture)")
    if marks:
        out["watermark"] = list(dict.fromkeys(marks))
    props: Dict[str, Any] = {}
    core = _package_part(pkg, CORE_REL)
    if core:
        root = pkg.xml(core)
        for key, tag in (("title", DC + "title"), ("author", DC + "creator"), ("last_modified_by", CP + "lastModifiedBy"),
                         ("status", CP + "contentStatus")):
            el = root.find(tag)
            if el is not None and (el.text or "").strip():
                props[key] = _clip(el.text, 50)
    app = _package_part(pkg, APP_REL)
    if app:
        el = pkg.xml(app).find("{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}Company")
        if el is not None and (el.text or "").strip():
            props["company"] = _clip(el.text, 50)
    custom = _package_part(pkg, CUSTOM_REL)
    if custom:
        names = [p.get("name") for p in pkg.xml(custom) if isinstance(p.tag, str) and p.get("name")]
        if "_MarkAsFinal" in names:
            out["marked_final"] = True
        names = [n for n in names if n != "_MarkAsFinal"]
        if names:
            props["custom"] = names[:10]
    if props:
        out["properties"] = props
    settings = next((r.resolved for r in pkg.rels(pkg.main_part()).values()
                     if r.type.endswith("/settings") and r.resolved and pkg.has(r.resolved)), None)
    if settings:
        prot = pkg.xml(settings).find(W + "documentProtection")
        if prot is not None and prot.get(W + "edit") == "trackedChanges" and \
                prot.get(W + "enforcement") in ("1", "true", "on"):
            out["track_changes_locked"] = True
    return out


def body_text_fonts(pkg: DocxPackage, limit: int = 3) -> List[Dict[str, Any]]:
    """The body text's effective (font, size) by share of characters, most used first."""
    sheet = StyleSheet(pkg)
    res = Resolver(sheet, theme_fonts(pkg))
    count: Counter = Counter()
    for p in paragraphs(pkg.xml(pkg.main_part())):
        for r in p.iter(W + "r"):
            if nearest_p(r) is not p or any(a.tag in (W + "del", W + "moveFrom") for a in r.iterancestors()):
                continue
            n = sum(len(t.text or "") for t in r.findall(W + "t"))
            if not n:
                continue
            try:
                eff = res.run(r.find(W + "rPr"), p)
            except AmbiguousToggle:
                continue
            count[(eff["ascii"], _int(eff["sz"], 0))] += n
    total = sum(count.values()) or 1
    return [{"font": f, "size_pt": sz / 2, "share": round(c / total, 2)} for (f, sz), c in count.most_common(limit)]


def template_spec_summary(pkg: DocxPackage) -> Dict[str, Any]:
    """What a template file offers the compiler (descriptive only; the op re-derives everything it uses)."""
    from .catalog import REFERENCE_FILLS
    secs = DocxPackage.sect_prs(pkg.xml(pkg.main_part()))
    parts: List[str] = []
    for shown in effective_refs(pkg, secs):
        for part in shown.values():
            if part and part not in parts and pkg.has(part):
                parts.append(part)
    prepared = []
    for part in parts:
        for p in paragraphs(pkg.xml(part)):
            try:
                prepared += [s.name for s in find_slots(p, ("highlight", "braces"), ("yellow",), part)]
            except JobException:
                prepared.append("(unusable)")
    out: Dict[str, Any] = {"body_text": body_text_fonts(pkg)}
    if prepared:
        out["kind"] = "prepared template: use the default slot_markers"
        out["prepared_slots"] = list(dict.fromkeys(prepared))
        return out
    out["kind"] = "a spec (no highlighted or {{NAME}} placeholders): use slot_markers ['reference']"
    values: Dict[str, str] = {}
    own: Dict[str, Any] = {}
    for name, rule in REFERENCE_FILLS.items():
        try:
            values[name] = extract_fill(pkg, name, rule)
            own[name] = values[name]
        except JobException as e:
            own[name] = f"(not found by reference_fills: {e.reason})"
    out["own_values"] = own
    usable = {n: v for n, v in values.items() if len("".join(value_tokens(v))) >= 3}
    shown_at, problems = [], []
    for part in parts:
        root = pkg.xml(part)
        in_field = field_runs(root)
        for p in paragraphs(root):
            try:
                sl = find_slots(p, ["reference"], [], part, ref_values=usable, in_field=in_field)
            except JobException as e:
                problems.append(f"[{e.code}] {e.reason}")
                continue
            if sl:
                shown_at.append(f"{part}: «{mark_slots(p, sl)}»")
    out["shown_in_headers_footers"] = shown_at or "(none — its headers/footers do not show them)"
    if problems:
        out["problems"] = problems[:6]
    return out


def etree_local(qname: str) -> str:
    return qname.split("}", 1)[1] if "}" in qname else qname


def bound(prof: Dict[str, Any], limit: int = MAX_PROFILE_BYTES) -> Dict[str, Any]:
    """Shrink until the JSON fits ``limit`` — always. Samples go first (silently), then structure, each structural
    cut recorded in ``truncated`` (what was omitted), and as a last resort only the file's identity, settings,
    counts, the body text's font/size (``fonts.body_text`` — what "match the template's font" needs) and the
    omission list remain."""
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

    def thin_markup():
        m = prof.get("markup")
        if not m:
            return
        cut = False
        for k, v in list(m.items()):
            if isinstance(v, dict) and len(v) > 3:
                m[k] = dict(list(v.items())[:3])
                cut = True
            elif isinstance(v, list) and len(v) > 3 and k != "tracked_change_dates":
                m[k] = v[:3]
                cut = True
        if cut:
            omitted.append("markup details beyond the first three of each kind")

    steps = [
        lambda: prof.__setitem__("first_paragraphs", prof["first_paragraphs"][:3]),
        lambda: [st.pop("sample", None) for st in prof["styles_in_use"]],
        lambda: prof.__setitem__("styles_in_use", prof["styles_in_use"][:12]),
        lambda: [v.__setitem__("text", v["text"][:2]) for v in prof["header_footer_parts"].values() if "text" in v],
        lambda: prof.__setitem__("first_paragraphs", []),
        lambda: prof["fonts"].__setitem__("direct_in_runs", dict(list(prof["fonts"]["direct_in_runs"].items())[:4])),
        lambda: prof.pop("hygiene", None),
        thin_markup,
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
        keep = {k: prof[k] for k in ("file", "pattern_id", "settings", "counts", "markup") if k in prof}
        body = (prof.get("fonts") or {}).get("body_text")
        prof.clear()
        prof.update(keep)
        if body:
            prof["fonts"] = {"body_text": body}
        prof["truncated"] = omitted + ["everything but identity, settings, counts and the body text's font/size "
                                       "(profile limit)"]
        for k in ("markup", "counts", "settings", "fonts"):     # an absurdly small limit: identity only
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
        # the representative is the first file of the pattern that profiles; a file whose XML the profiler cannot
        # read (a margin that is not a number…) is listed as unprofiled and the next file stands in for the pattern
        order = list(fs)
        prof, unprofiled = None, []
        for f in fs:
            try:
                prof = profile(DocxPackage.from_path(f))      # only the representative is reopened
                if f is not order[0]:
                    order = [f] + [x for x in order if x is not f]
                break
            except Exception as e:  # noqa: BLE001
                unprofiled.append(f"{f.name}: {type(e).__name__}: {e}")
        # ``files`` are the names the model's tools address files by; ``paths`` the actual files, in the same order
        # (two folders can hold a file of the same name — by name alone one of them would shadow the other)
        entry: Dict[str, Any] = {"pattern_id": pid, "count": len(fs), "files": [f.name for f in order],
                                 "paths": [str(f) for f in order],
                                 "profile": prof if prof is not None else
                                 {"error": "no file of this pattern could be profiled", "files": unprofiled[:3]}}
        if unprofiled:
            entry["unprofiled"] = unprofiled
        patterns.append(entry)
    out: Dict[str, Any] = {"files": len(files), "patterns": patterns}
    if unreadable:
        out["unreadable"] = unreadable
    return out
