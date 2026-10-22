"""Document profiles — what the compiler (and a human) needs to know about a batch, without a model and without
reading every file in full (docs/history/NL_SURGICAL_EDITOR_PLAN.md §2, Phase 3).

``profile(pkg)`` describes one file: section layout (page, margins, text width, titlePg, numbering) and what each
section's headers/footers show (part names as resolved through the rels, their display text and fields, any
template slots), settings, fonts (theme, docDefaults, direct fonts in use), the paragraph styles in use with their
effective font/size and a sample paragraph, numbering, counts, hygiene/whitespace signals, the first paragraphs of
the body, and ``roles`` — how the file encodes each specification role (styles, typed or automatic markers, examples)
with the concrete selector per role the file makes unambiguous (``structure.role_summary``). It is bounded
(``MAX_PROFILE_BYTES``): long text is truncated and samples are dropped before structure is.

Text is shown as typed. Where Word shows a line's lowercase letters as capitals through formatting (w:caps or
w:smallCaps, direct or from a style), the line is named in ``shown_in_capitals`` (a header/footer view) or
``first_paragraphs_shown_in_capitals`` (``shown_case``): typed capitals and displayed capitals call for different
edits, and the text alone cannot tell them apart.

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
from .ops.effective import AmbiguousToggle, PropResolver, Resolver, text_width, theme_fonts
from .ops.hf_common import KINDS, TYPES, effective_refs
from .ops.selectors import StyleSheet
from .ops.base import JobException
from .ops.slots import display_text, extract_fill, field_runs, find_slots, mark_slots, nearest_p, value_tokens
from .package import NS, W, DocxPackage
from .structure import role_summary

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


CASE_WORDS = {"caps": "all capitals", "smallCaps": "small capitals"}


def shown_case(p, props: PropResolver) -> Optional[str]:
    """How Word shows the lowercase letters typed in a paragraph (its runs outside deletions, not those of a nested
    paragraph): "all capitals" or "small capitals" when every run holding one has w:caps or w:smallCaps in effect —
    directly or from its styles (``props``: set_properties' resolver) —, "partly capitals" when only some of them do,
    "capitals and small capitals" for a mix of the two; None when none does, or when no letter is typed in lowercase
    (typed capitals are the text itself, not formatting). Profiles show text as typed; this says how it looks."""
    seen = []
    for r in p.iter(W + "r"):
        if nearest_p(r) is not p or any(a.tag in (W + "del", W + "moveFrom") for a in r.iterancestors()):
            continue
        if not any(c.islower() for t in r.findall(W + "t") for c in (t.text or "")):
            continue
        rpr = r.find(W + "rPr")
        seen.append(next((e for e in ("caps", "smallCaps") if props.run_prop(rpr, p, e)[0] is True), None))
    if not seen or set(seen) == {None}:
        return None
    if None in seen:
        return "partly capitals"
    return CASE_WORDS[seen[0]] if len(set(seen)) == 1 else "capitals and small capitals"


PART_LINES = 6           # lines of a header/footer view (the rest counted in ``more_lines``)
PART_LINES_THINNED = 2   # what the size bound leaves of each view before it drops the text
PART_LINES_CUT = "header/footer lines beyond the first two (each view's more_lines counts them)"


def _part_view(pkg: DocxPackage, part: str, props: PropResolver) -> Dict[str, Any]:
    root = pkg.xml(part)
    lines = [(_shown_text(p), p) for p in paragraphs(root) if nearest_p(p) is None]
    lines = [(t, p) for t, p in lines if t.strip()]
    view: Dict[str, Any] = {"part": part, "text": [_clip(t) for t, _ in lines[:PART_LINES]]}
    if len(lines) > PART_LINES:
        view["more_lines"] = len(lines) - PART_LINES
    caps = {str(i): c for i, c in enumerate(shown_case(p, props) for _, p in lines[:PART_LINES]) if c}
    if caps:
        view["shown_in_capitals"] = caps
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
    try:
        tabs = res.paragraph(sample_p, direct=False)["tabs"]     # the stops the style gives (set_tab_stops clears them)
    except (TypeError, ValueError):
        tabs = ()
    if tabs:
        e["tab_stops"] = [f"{val} {pos}" + (f" {leader}" if leader and leader != "none" else "")
                          for pos, val, leader in tabs]
    if st is not None:                       # batch 4: the border box and shading the style gives (set_borders_shading)
        sides, shd = [], None
        for level in reversed(sheet.lineage(sid)):
            ppr = level.find(W + "pPr")
            bdr = ppr.find(W + "pBdr") if ppr is not None else None
            for side in (bdr if bdr is not None else []):
                if not isinstance(side.tag, str):
                    continue
                name = etree_local(side.tag)
                if name in sides:
                    sides.remove(name)
                if side.get(W + "val") not in ("nil", "none"):
                    sides.append(name)
            sh = ppr.find(W + "shd") if ppr is not None else None
            if sh is not None:                 # the nearest level wins; a theme fill is what Word renders (Codex on #31)
                theme = sh.get(W + "themeFill")
                fill = sh.get(W + "fill")
                if sh.get(W + "val") == "nil":
                    shd = None
                elif theme:
                    shd = theme + "".join(f" {k} {sh.get(W + 'themeFill' + k.capitalize())}" for k in ("shade", "tint")
                                          if sh.get(W + "themeFill" + k.capitalize()))
                else:
                    shd = fill if fill and fill != "auto" else None
        if sides:
            e["borders"] = sides
        if shd:
            e["shading"] = shd
    if para["jc"] != "left":
        e["jc"] = para["jc"]
    if para["before"] != "0" or para["after"] != "0":
        e["spacing_twips"] = {"before": _int(para["before"], 0), "after": _int(para["after"], 0)}
    txt = display_text(sample_p)
    if txt.strip():
        e["sample"] = _clip(txt, 70)
    return e


_SNAPSHOTS = {W + "rPrChange", W + "pPrChange", W + "tblPrChange", W + "trPrChange", W + "tcPrChange", W + "sectPrChange"}


def _in_snapshot(el) -> bool:
    """Inside a tracked property change's snapshot (what reject-all would restore, not what the file shows)."""
    return any(a.tag in _SNAPSHOTS for a in el.iterancestors())


def _named(sheet: StyleSheet, sid: str, count: int) -> Dict[str, Any]:
    e: Dict[str, Any] = {"id": sid, "count": count}
    st = sheet.styles.get(sid)
    name = st.find(W + "name") if st is not None else None
    if name is not None and name.get(W + "val") and name.get(W + "val") != sid:
        e["name"] = name.get(W + "val")
    if st is None:
        e["missing_definition"] = True
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
    props = PropResolver(sheet)

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
                    parts_seen[v["part"]] = _part_view(pkg, v["part"], props)

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
    # batch 4: what map_styles can map — over every text part (its default scope), runs and paragraph marks alike,
    # never a tracked change's snapshot (Codex on #31)
    other_styles: Dict[str, Counter] = {"character": Counter(), "table": Counter()}
    for part in pkg.text_parts():
        proot = pkg.xml(part)
        for el in proot.iter(W + "rStyle"):
            holder = el.getparent().getparent() if el.getparent() is not None else None
            if holder is not None and holder.tag in (W + "r", W + "pPr") and el.get(W + "val") \
                    and not _in_snapshot(el):
                other_styles["character"][el.get(W + "val")] += 1
        for el in proot.iter(W + "tblStyle"):
            if el.get(W + "val") and not _in_snapshot(el):
                other_styles["table"][el.get(W + "val")] += 1

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
    styles_unused = None
    if sheet.part:                     # batch 4: what remove_unused_styles would find (all / the document's own)
        try:
            from .ops.remove_unused_styles import analyze
            unused = analyze(pkg, sheet.part, [], "all", ("paragraph", "character", "table", "numbering"))["removed"]
            custom = analyze(pkg, sheet.part, [], "custom", ("paragraph", "character", "table", "numbering"))["removed"]
            styles_unused = {"all": len(unused), "custom": len(custom)}
        except Exception:  # noqa: BLE001 — descriptive only: a styles, numbering or settings part the op would refuse
            pass               # (or that will not parse) leaves the counts out; the file still profiles
    kinds = pkg.parts_by_kind()
    for k in ("footnotes", "endnotes", "comments"):
        if kinds.get(k):
            counts[k + "_parts"] = kinds[k]
    firsts = [p for p in body if display_text(p).strip()][:8]
    first = [_clip(_shown_text(p)) for p in firsts]
    first_caps = {str(i): c for i, c in enumerate(shown_case(p, props) for p in firsts) if c}

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
        **{f"{kind}_styles_in_use": [_named(sheet, sid, n) for sid, n in c.most_common(10)]
           for kind, c in other_styles.items() if c},
        "counts": counts,
        "first_paragraphs": first,
    }
    if first_caps:
        prof["first_paragraphs_shown_in_capitals"] = first_caps
    if styles_unused is not None:      # not under counts: the size bound drops it before any section (counts stay)
        prof["styles_unused"] = styles_unused
    try:
        marked = markup(pkg)
    except Exception as e:  # noqa: BLE001 — descriptive only: a file the summary cannot read still profiles
        marked = {"error": f"{type(e).__name__}: {e}"}
    if marked:
        prof["markup"] = marked
    try:
        tables = table_summary(pkg, root, sheet)
    except Exception as e:  # noqa: BLE001 — descriptive only, like markup
        tables = {"error": f"{type(e).__name__}: {e}"}
    if tables:
        prof["tables"] = tables
    try:
        lists = list_summary(pkg, root, sheet)
    except Exception as e:  # noqa: BLE001 — descriptive only, like markup
        lists = {"error": f"{type(e).__name__}: {e}"}
    if lists:
        prof["lists"] = lists
    try:                               # how this file encodes each role, and the selector per role it makes unambiguous
        roles = role_summary(pkg)
    except Exception as e:  # noqa: BLE001 — descriptive only, like markup: a modified overlay or an unreadable list
        roles = {"error": f"{type(e).__name__}: {e}"}   # definition leaves the summary out; the file still profiles
    if roles:
        prof["roles"] = roles
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


LIST_LIMIT = 4           # lists described one by one (the most used first)
NUMBERED_STYLE_LIMIT = 20
TYPED_NUMBER = re.compile(r"^[ \t]*(?:PART\s+\d+|ARTICLE\s+\d+|\d+(?:\.\d+)+\.?|\d+[.)]|\(\d+\)|[A-Za-z][.)]|"
                          r"\([A-Za-z]\)|[IVXLC]{2,}[.)]|[ivxlc]{2,}[.)])"
                          r"(?:[ \t\u00a0]+|$)")


def list_summary(pkg: DocxPackage, root, sheet: StyleSheet) -> Dict[str, Any]:
    """What built-in batch 5's numbering operations would find (descriptive; the ops count with ops/lists.py
    themselves): the lists that number the body (by list definition, the most used first — each level's format, the
    styles numbered at it, how many paragraphs, the first number shown; how many paragraphs the engine does not
    evaluate and why), the **numbered styles** (a paragraph style whose own numbering names a list level, with the
    number its first paragraph would show: ``PART 1 -``, ``1.1``, ``A.`` — what a scheme's ``levels`` are named from in an
    example file) and how many unnumbered paragraphs start with a typed number."""
    from .ops.lists import ListDefs, count, counted, paragraph_numbering, render, start_of, style_numbering
    from .ops.style_import import style_type
    from .ops.textedit import run_text
    defs = ListDefs.load(pkg)
    out: Dict[str, Any] = {}
    if defs.present:
        pars = count(root, sheet, defs)
        groups: Dict[Any, List[Any]] = {}
        for np_ in pars:
            groups.setdefault(np_.abstract_id if np_.abstract_id is not None else f"num {np_.num_id}", []).append(np_)
        described = []
        for key, ps in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:LIST_LIMIT]:
            levels: Dict[int, Dict[str, Any]] = {}
            for np_ in ps:
                if np_.ilvl is None:
                    continue
                e = levels.setdefault(np_.ilvl, {"level": np_.ilvl, "format": np_.fmt, "paragraphs": 0,
                                                 "styles": Counter(), "first": None})
                e["paragraphs"] += 1
                if np_.style:
                    e["styles"][np_.style] += 1
                if e["first"] is None and np_.evaluated and np_.text:
                    e["first"] = np_.text
            for e in levels.values():
                e["styles"] = [sid for sid, _ in e["styles"].most_common(3)]
                if e["first"] is None:
                    e.pop("first")
            d: Dict[str, Any] = {"definition": key, "lists": sorted({np_.num_id for np_ in ps if np_.num_id is not None}),
                                 "paragraphs": len(ps), "levels": [levels[k] for k in sorted(levels)]}
            issues = Counter(np_.issue for np_ in ps if not np_.evaluated)
            if issues:
                d["not_evaluated"] = dict(issues)
            described.append(d)
        if described:
            out["lists"] = described
        if len(groups) > LIST_LIMIT:
            out["more_lists"] = len(groups) - LIST_LIMIT
        numbered = []
        for sid, st in sheet.styles.items():
            if style_type(st) != "paragraph":
                continue
            nb = style_numbering(sid, sheet, defs)
            if nb is None or nb.num_id is None or nb.dangling or nb.unreadable or nb.ilvl is None:
                continue
            lv = defs.level(nb.num_id, nb.ilvl)
            e = {"style": sid, "list": nb.num_id, "level": nb.ilvl}
            name = st.find(W + "name")
            if name is not None and name.get(W + "val") and name.get(W + "val") != sid:
                e["name"] = name.get(W + "val")
            if lv is None or lv.fmt == "bullet":
                continue                              # a bullet is never a numbered level of a scheme
            if lv.text is not None and not lv.unsupported:
                shown, _ = render(lv.text, lv.legal, lambda k, n=nb.num_id: start_of(defs, n, k),
                                  lambda k, n=nb.num_id: defs.level(n, k))
                if shown is not None:
                    e["shows"] = shown
            numbered.append(e)
        used = {np_.style for np_ in pars if np_.style}
        per_list = Counter(e["list"] for e in numbered)
        # a style the body numbers with, or one of an outline (two or more styles numbered by one list); Word's unused
        # single-level List Number styles are noise here
        numbered = [e for e in numbered if e["style"] in used or per_list[e["list"]] > 1]
        if numbered:                          # the lists with the most numbered styles first (an outline scheme)
            numbered.sort(key=lambda e: (-per_list[e["list"]], e["list"], e["level"], e["style"]))
            out["numbered_styles"] = numbered[:NUMBERED_STYLE_LIMIT]
    typed = 0
    for p in root.iter(W + "p"):
        if counted(p) and nearest_p(p) is None and paragraph_numbering(p, sheet, defs) is None \
                and TYPED_NUMBER.match(run_text(p)):
            typed += 1
    if typed:
        out["typed_numbers"] = typed
    return out


TABLE_LIMIT = 8          # tables listed one by one (the rest are counted by style)
OFF_VALUES = ("0", "false", "off")


def _on(el) -> bool:
    return el is not None and el.get(W + "val") not in OFF_VALUES


def table_summary(pkg: DocxPackage, root, sheet: StyleSheet) -> Dict[str, Any]:
    """What built-in batch 5's table operations would find in the body (descriptive; set_table_props and
    apply_table_style read the file themselves): how many tables, their styles, and the first few one by one — index
    (the table selector's ordinal), style, rows, header rows that repeat, rows that may break across pages, nested, the
    first row's text. Nothing when the body has no table."""
    tables = list(root.iter(W + "tbl"))
    if not tables:
        return {}
    default = sheet.defaults.get("table")
    styles: Counter = Counter()
    listed: List[Dict[str, Any]] = []
    for k, tbl in enumerate(tables):
        ts = tbl.find(f"{W}tblPr/{W}tblStyle")
        sid = ts.get(W + "val") if ts is not None and ts.get(W + "val") else (default or "(none)")
        styles[sid] += 1
        if len(listed) >= TABLE_LIMIT:
            continue
        rows = tbl.findall(W + "tr")
        header = 0
        for tr in rows:
            if not _on(tr.find(f"{W}trPr/{W}tblHeader")):
                break
            header += 1
        cells = rows[0].findall(W + "tc") if rows else []
        first = " | ".join(" ".join(display_text(p) for p in c.iter(W + "p")).strip() for c in cells)
        entry = {"index": k, "style": sid, "rows": len(rows), "header_rows": header,
                 "rows_may_split": sum(1 for tr in rows if not _on(tr.find(f"{W}trPr/{W}cantSplit"))),
                 "first_row": _clip(first)}
        q = tbl.getparent()
        while q is not None and q.tag != W + "tbl":
            q = q.getparent()
        if q is not None:
            entry["nested"] = True
        listed.append(entry)
    out: Dict[str, Any] = {"count": len(tables), "styles": dict(styles.most_common(6)), "first": listed}
    if len(tables) > TABLE_LIMIT:
        out["more"] = len(tables) - TABLE_LIMIT
    return out


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
    langs = _languages(pkg, kinds)
    if langs:
        out["languages"] = langs
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


def _languages(pkg: DocxPackage, kinds) -> Dict[str, Any]:
    """The proofing languages the file names (set_language): the document default, and the w:lang values runs/marks
    and styles carry directly (counted)."""
    out: Dict[str, Any] = {}
    sheet = StyleSheet(pkg)
    if sheet.root is not None:
        dd = sheet.root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}lang")
        if dd is not None and dd.get(W + "val"):
            out["default"] = dd.get(W + "val")
        in_styles = Counter(st.find(f"{W}rPr/{W}lang").get(W + "val") for st in sheet.styles.values()
                            if st.find(f"{W}rPr/{W}lang") is not None and st.find(f"{W}rPr/{W}lang").get(W + "val"))
        if in_styles:
            out["styles"] = dict(in_styles.most_common(5))
    direct: Counter = Counter()
    for part in pkg.text_parts():
        for el in pkg.xml(part).iter(W + "lang"):
            holder = el.getparent().getparent() if el.getparent() is not None else None
            if el.getparent().tag == W + "rPr" and holder is not None and holder.tag in (W + "r", W + "pPr") \
                    and el.get(W + "val") and not _in_snapshot(el):
                direct[el.get(W + "val")] += 1
    if direct:
        out["direct"] = dict(direct.most_common(5))
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


def template_styles(pkg: DocxPackage, limit: int = 60) -> List[Dict[str, Any]]:
    """The styles a template offers apply_style / map_styles (batch 4): custom styles first, then the ones its body
    uses — id, name when it differs, type, basedOn, numbered (its own numPr). Descriptive only."""
    sheet = StyleSheet(pkg)
    if sheet.root is None:
        return []
    used = {el.get(W + "val") for part in pkg.text_parts() for tag in ("pStyle", "rStyle", "tblStyle")
            for el in pkg.xml(part).iter(W + tag)}
    rows = []
    for sid, st in sheet.styles.items():
        custom = st.get(W + "customStyle") in ("1", "true", "on")
        if not custom and sid not in used:
            continue
        e: Dict[str, Any] = {"id": sid, "type": st.get(W + "type", "paragraph")}
        name = st.find(W + "name")
        if name is not None and name.get(W + "val") and name.get(W + "val") != sid:
            e["name"] = name.get(W + "val")
        based = st.find(W + "basedOn")
        if based is not None and based.get(W + "val"):
            e["basedOn"] = based.get(W + "val")
        num = st.find(f"{W}pPr/{W}numPr/{W}numId")
        if num is not None and num.get(W + "val") not in (None, "0"):
            e["numbered"] = True
        rows.append((not custom, e))
    rows.sort(key=lambda r: r[0])
    return [e for _, e in rows[:limit]]


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
    styles = template_styles(pkg)
    if styles:
        out["styles"] = styles
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
    cut recorded in ``truncated`` (what was omitted). Header/footer lines are not a sample: a view thinned to its first
    two lines counts the rest in its ``more_lines`` and the cut is recorded (``PART_LINES_CUT``) — read silently, a
    footer of two lines looks like a different footer from one of four. As a last resort only the file's identity,
    settings, counts, the body text's font/size (``fonts.body_text`` — what "match the template's font" needs), the
    role selectors and the omission list remain.

    ``roles`` thins like a sample: its examples go with the style samples, then its markers and unresolved reasons
    with the hygiene signals, and the rest (counts, numbering, styles, selectors) after the list and table summaries
    and before any section is cut — silently, like ``hygiene`` and ``styles_unused``: a derived summary, not the file's
    structure. Its selectors come back in the last resort (what a planner needs most from a profile that small)."""
    omitted: List[str] = []
    stash: Dict[str, Any] = {}

    def size():
        return len(json.dumps(prof, ensure_ascii=False))

    def role_entries():
        r = prof.get("roles")
        return (r.get("roles") or {}).values() if isinstance(r, dict) and isinstance(r.get("roles"), dict) else []

    def thin_role_examples(n):
        for e in role_entries():
            if isinstance(e, dict) and "examples" in e:
                e["examples"] = e["examples"][:n]
                if not e["examples"]:
                    e.pop("examples")

    def thin_roles():                         # the core stays: count, numbering, styles, selectors, unresolved count
        for e in role_entries():
            if isinstance(e, dict):
                e.pop("markers", None)
        r = prof.get("roles")
        if isinstance(r, dict) and isinstance(r.get("unresolved"), dict):
            r["unresolved"].pop("reasons", None)

    def drop_roles():
        r = prof.pop("roles", None)
        if isinstance(r, dict) and r.get("selectors"):
            stash["roles"] = {"selectors": r["selectors"]}

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

    def cut_first(n):                         # a sample: silently, with the capitals of the lines that go
        prof["first_paragraphs"] = prof["first_paragraphs"][:n]
        caps = {k: c for k, c in (prof.pop("first_paragraphs_shown_in_capitals", None) or {}).items() if int(k) < n}
        if caps:
            prof["first_paragraphs_shown_in_capitals"] = caps

    def thin_part_text(n):                    # never silently: a missing footer line reads as a different footer
        cut = False
        for v in prof["header_footer_parts"].values():
            if len(v.get("text") or []) > n:
                v["more_lines"] = v.get("more_lines", 0) + len(v["text"]) - n
                v["text"] = v["text"][:n]
                caps = {k: c for k, c in (v.pop("shown_in_capitals", None) or {}).items() if int(k) < n}
                if caps:
                    v["shown_in_capitals"] = caps
                cut = True
        if cut:
            omitted.append(PART_LINES_CUT)

    def drop_part_text():
        if any(v.get("text") for v in prof["header_footer_parts"].values()):
            omitted.append("header/footer text")
        if PART_LINES_CUT in omitted:         # said by the line above now
            omitted.remove(PART_LINES_CUT)
        for v in prof["header_footer_parts"].values():
            for k in ("text", "more_lines", "shown_in_capitals"):
                v.pop(k, None)

    def thin_styles():
        if len(prof["styles_in_use"]) > 6:
            omitted.append(f"{len(prof['styles_in_use']) - 6} style(s) in use")
        prof["styles_in_use"] = [{k: st[k] for k in ("id", "count", "font", "size_pt") if k in st}
                                 for st in prof["styles_in_use"][:6]]

    def thin_other_styles():
        for key in ("character_styles_in_use", "table_styles_in_use"):
            if len(prof.get(key) or []) > 4:
                omitted.append(f"{len(prof[key]) - 4} {key.split('_')[0]} style(s) in use")
                prof[key] = prof[key][:4]
            if prof.get(key):
                prof[key] = [{k: st[k] for k in ("id", "count") if k in st} for st in prof[key]]

    def drop_other_styles():                  # before any section or part is cut: they matter more
        for key in ("character_styles_in_use", "table_styles_in_use"):
            if prof.pop(key, None):
                omitted.append(key.replace("_", " "))
        prof.pop("styles_unused", None)       # two counts, descriptive: silently, like the samples

    def thin_tables():
        t = prof.get("tables")
        if t and len(t.get("first") or []) > 3:
            omitted.append(f"{len(t['first']) - 3} table view(s)")
            t["first"] = t["first"][:3]

    def drop_tables():                        # the table count stays under counts
        if prof.pop("tables", None):
            omitted.append("tables")

    def thin_lists():
        lists = prof.get("lists")
        if lists and len(lists.get("lists") or []) > 1:
            omitted.append(f"{len(lists['lists']) - 1} list view(s)")
            lists["lists"] = lists["lists"][:1]

    def drop_lists():
        if prof.pop("lists", None):
            omitted.append("lists")

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
        lambda: cut_first(3),
        lambda: [st.pop("sample", None) for st in prof["styles_in_use"]],
        lambda: thin_role_examples(1),
        lambda: prof.__setitem__("styles_in_use", prof["styles_in_use"][:12]),
        lambda: thin_part_text(PART_LINES_THINNED),
        lambda: cut_first(0),
        lambda: thin_role_examples(0),
        lambda: prof["fonts"].__setitem__("direct_in_runs", dict(list(prof["fonts"]["direct_in_runs"].items())[:4])),
        lambda: prof.pop("hygiene", None),
        thin_roles,
        thin_markup,
        thin_tables,
        thin_lists,
        drop_part_text,
        thin_styles,
        thin_other_styles,
        drop_other_styles,
        drop_tables,
        drop_lists,
        drop_roles,
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
        if "roles" not in prof and stash.get("roles"):
            prof["roles"] = stash["roles"]
        keep = {k: prof[k] for k in ("file", "pattern_id", "settings", "counts", "markup", "roles") if k in prof}
        body = (prof.get("fonts") or {}).get("body_text")
        prof.clear()
        prof.update(keep)
        if body:
            prof["fonts"] = {"body_text": body}
        prof["truncated"] = omitted + ["everything but identity, settings, counts, the body text's font/size and "
                                       "the role selectors (profile limit)"]
        for k in ("markup", "roles", "counts", "settings", "fonts"):     # an absurdly small limit: identity only
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
