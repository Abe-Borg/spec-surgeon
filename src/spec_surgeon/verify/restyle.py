"""V10 kind ``mapped_styles`` — map_styles, verified (built-in batch 4, item 31 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §7),
and the style-reference resolution the restyling kinds share (``v_resolve_style``; apply_style's kind reuses it).

This module's own reading of the op's definitions (transform side: ops/map_styles.py; CLAUDE.md lists them among the
definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* **Resolution** (``v_resolve_style``): the style whose ``w:styleId`` equals the reference exactly; else the one whose
  ``w:name`` equals it with whitespace collapsed and casefolded (two → ValueError: the op must refuse
  ``style-mismatch``); else none.
* **Uses the op does not restyle** (``_v_unmapped``): a character ``from`` in ``w:sdtPr/w:rPr``, ``w:sdtEndPr/w:rPr`` or
  ``m:r/w:rPr`` of a part in scope, or in ``w:lvl/w:rPr`` of the numbering part — the op must refuse (``structure``).
* **The pairs**, from the user's own ``map`` / ``template`` / ``existing`` / ``allow_missing`` / ``tracked`` with this
  module's defaults (keep, true, false): the parameter rules (one ``from`` twice, a ``to`` that is another pair's
  ``from``, replace without a template), every ``to`` in the template when one is given, ``from`` in the SOURCE (none →
  missing), the style mapped to (replace: the template's; keep: the source's when ``to`` resolves there, else the
  template's, else the op must refuse), ``same``, one type of paragraph/character/table, no default character or table
  style as ``from``, no two ``from`` resolving to one style, no style mapped to that is another pair's ``from``.
* **The elements** over the parts in scope (default: every text part): paragraphs whose style (``w:pStyle``, else the
  default paragraph style) is a paragraph ``from``; runs (grouped by their nearest paragraph) and paragraph marks whose
  ``w:rStyle`` is a character ``from``; tables whose ``w:tblPr/w:tblStyle`` is a table ``from``. A pair without elements
  is ``unused``.

Fails when the pairs (status and the two styles), the changed elements or their counts differ from the plan's; when a
changed paragraph's output pPr is not the first child of the paragraph, does not begin with a ``w:pStyle`` that is the
source's with ``w:val`` the style mapped to (``{w:val}`` when the source had none), or differs in any other child but the
paragraph mark's rPr (the residual compares that, unless the kind that changed it registered it); when a
changed run's or mark's output rPr is not the source's with exactly ``w:rStyle/@w:val`` changed (children in the same
order); when a changed table's ``w:tblStyle`` is not the source's with ``w:val`` changed, in its place; when a change marker
breaks the shared rules (tracked: one ``w:pPrChange`` / ``w:rPrChange`` per element, its snapshot the source; plain:
markers unchanged); when a style mapped to is missing from the output's styles. The template styles the op needed
(active pairs whose style comes from the template) are cross-checked with the op's ``styles_imported`` assertion
(``styleimport.check_requested``), which verifies the import itself. Registers ``strip_ppr`` {pStyle, pPrChange} of the
changed paragraphs, ``strip_runs``, ``strip_marks`` and ``strip_tbl_style`` — exactly what it checked.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .reader import STYLES_REL, W
from .props import Ctx, Styles, _check_ppr_change, _check_rpr_change, _local, _plain, _pstyle, sig
from .styleimport import check_requested, v_name, v_type
from .template import _template

V_SCOPE = ("document", "headers", "footers", "footnotes", "endnotes", "comments")
V_MAPPABLE = {"paragraph", "character", "table"}
V_MATH_RUN = "{http://schemas.openxmlformats.org/officeDocument/2006/math}r"
V_NUMBERING_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"


def v_resolve_style(styles: Styles, ref: Any) -> Optional[str]:
    """The id of the style ``ref`` names: exact w:styleId, else the one w:name equal with whitespace collapsed and
    casefolded, else None; two styles of that name → ValueError (the op must refuse: style-mismatch)."""
    if not isinstance(ref, str) or not ref:
        return None
    if ref in styles.by_id:
        return ref
    want = " ".join(ref.split()).casefold()
    named = sorted(sid for sid, st in styles.by_id.items()
                   if v_name(st) is not None and " ".join(v_name(st).split()).casefold() == want)
    if len(named) > 1:
        raise ValueError(f"two styles are named {ref!r}: {named} (the op must refuse: style-mismatch)")
    return named[0] if named else None


def _v_map(params: Dict[str, Any]) -> List[Tuple[str, str]]:
    raw = params.get("map")
    if not isinstance(raw, list) or not raw:
        raise ValueError("map must be a non-empty list (the op must refuse: bad-params)")
    out = []
    for m in raw:
        if not isinstance(m, dict) or sorted(m) != ["from", "to"] or any(not isinstance(m[k], str) or not m[k].strip()
                                                                         for k in m):
            raise ValueError(f"bad pair {m!r} (the op must refuse: bad-params)")
        out.append((m["from"], m["to"]))
    froms = [f for f, _ in out]
    if len(froms) != len(set(froms)) or any(t != f and t in froms for f, t in out):
        raise ValueError("a 'from' twice, or a 'to' that is another pair's 'from' (the op must refuse: bad-params)")
    return out


def _v_holders(p, view_runs) -> List[Tuple[str, Optional[int], Any]]:
    held = [("run", i, r.find(W + "rPr")) for i, r in enumerate(view_runs)]
    return held + [("mark", None, p.find(W + "pPr/" + W + "rPr"))]


def _v_rstyle(rpr) -> Optional[str]:
    e = rpr.find(W + "rStyle") if rpr is not None else None
    return e.get(W + "val") if e is not None else None


def _v_unmapped(ctx: Ctx, parts: List[str], chars) -> List[Tuple[str, str]]:
    """(part, style) of every use of a character ``from`` the op does not restyle: w:sdtPr/w:rPr, w:sdtEndPr/w:rPr and
    m:r/w:rPr in the parts in scope, w:lvl/w:rPr in the numbering part (the op must refuse them)."""
    found: List[Tuple[str, str]] = []
    for part in parts:
        for el in ctx.src.xml(part).iter():
            if el.tag in (W + "sdtPr", W + "sdtEndPr", V_MATH_RUN) and _v_rstyle(el.find(W + "rPr")) in chars:
                found.append((part, _v_rstyle(el.find(W + "rPr"))))
    npart = ctx.src.related(V_NUMBERING_REL)
    if npart:
        for lvl in ctx.src.xml(npart).iter(W + "lvl"):
            if _v_rstyle(lvl.find(W + "rPr")) in chars:
                found.append((npart, _v_rstyle(lvl.find(W + "rPr"))))
    return found


def _v_tblstyle(tbl) -> Optional[str]:
    e = tbl.find(W + "tblPr/" + W + "tblStyle")
    return e.get(W + "val") if e is not None else None


def _v_pairs(ctx: Ctx, pairs, t_styles: Optional[Styles], existing: str) -> List[Dict[str, Any]]:
    t_to: Dict[str, str] = {}
    if t_styles is not None:
        for _, t in pairs:
            tid = v_resolve_style(t_styles, t)
            if tid is None:
                raise ValueError(f"the template has no style {t!r} (the op must refuse: bad-params)")
            t_to[t] = tid
    rows: List[Dict[str, Any]] = []
    for f, t in pairs:
        row: Dict[str, Any] = {"from": f, "to": t, "status": "missing", "from_id": None, "to_id": None, "type": None,
                               "source": None}
        rows.append(row)
        fid = v_resolve_style(ctx.src_styles, f)
        if fid is None:
            continue
        if existing == "replace":
            to_id, source = t_to[t], "template"
        else:
            mine = v_resolve_style(ctx.src_styles, t)
            if mine is not None:
                to_id, source = mine, "document"
            elif t_styles is not None:
                to_id, source = t_to[t], "template"
            else:
                raise ValueError(f"{t!r} is in neither the file nor a template (the op must refuse: missing-style)")
        row.update(from_id=fid, to_id=to_id, source=source)
        if fid == to_id:
            row["status"] = "same"
            continue
        a_type = v_type(ctx.src_styles.by_id[fid])
        b_type = v_type(ctx.src_styles.by_id[to_id] if source == "document" else t_styles.by_id[to_id])
        if a_type not in V_MAPPABLE or a_type != b_type or (
                a_type != "paragraph" and ctx.src_styles.default.get(a_type) == fid):
            raise ValueError(f"{f!r} → {t!r}: {a_type} → {b_type} (the op must refuse: style-mismatch)")
        row.update(type=a_type, status="mapped")
    froms = [r["from_id"] for r in rows if r["status"] == "mapped"]
    if len(froms) != len(set(froms)) or any(r["to_id"] in froms for r in rows if r["status"] == "mapped"):
        raise ValueError("two pairs resolve to one style, or a chain (the op must refuse: style-mismatch)")
    return rows


def _check_paragraph(S, o_p, to: str, tracked: bool, author, where: str, errs: List[str], mark_checked: bool) -> None:
    """The paragraph's pPr: w:pStyle as expected, first; every other child identical — except the paragraph mark's rPr,
    which this check never compares: ``_check_rpr`` checks it whole when this kind restyled the mark
    (``mark_checked``), and otherwise the residual compares it (``strip_ppr`` leaves it in) — unless another op's kind
    changed it and registered it (``set_font``, ``set_language`` on the same paragraph: their change is theirs to check,
    rule 9 lets the two ops share the paragraph)."""
    O = o_p.find(W + "pPr")
    kids = [c for c in o_p if isinstance(c.tag, str)]
    if O is None or not kids or kids[0] is not O:
        errs.append(f"{where}: the output paragraph has no pPr as its first child")
        return
    o_kids = [c for c in O if isinstance(c.tag, str)]
    ps = O.find(W + "pStyle")
    s_ps = S.find(W + "pStyle") if S is not None else None
    want = {**(dict(s_ps.attrib) if s_ps is not None else {}), W + "val": to}
    if ps is None or not o_kids or o_kids[0] is not ps or dict(ps.attrib) != want or len(ps):
        errs.append(f"{where}: w:pStyle should be the first child of the pPr with {_plain(want)}, got "
                    f"{_plain(dict(ps.attrib)) if ps is not None else None}")
    skip = ("pStyle", "pPrChange", "rPr")
    rest_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in skip]
    rest_o = [sig(c) for c in O if _local(c) not in skip]
    if mark_checked and [_local(c) for c in (S if S is not None else []) if _local(c) != "pPrChange"] != \
            [_local(c) for c in O if _local(c) not in ("pPrChange",) and not (_local(c) == "pStyle" and s_ps is None)]:
        errs.append(f"{where}: the paragraph mark's rPr left its place in the pPr")
    if rest_s != rest_o:
        errs.append(f"{where}: paragraph properties other than w:pStyle changed")
    _check_ppr_change(S, O, tracked, author, where, errs)


def _check_rpr(S, O, to: str, tracked: bool, author, where: str, errs: List[str]) -> None:
    if O is None or S is None:
        errs.append(f"{where}: no rPr in the {'output' if O is None else 'source'}")
        return
    names_s = [_local(c) for c in S if _local(c) != "rPrChange"]
    names_o = [_local(c) for c in O if _local(c) != "rPrChange"]
    s_rs, o_rs = S.find(W + "rStyle"), O.find(W + "rStyle")
    want = {**dict(s_rs.attrib), W + "val": to}
    if o_rs is None or dict(o_rs.attrib) != want or len(o_rs) or names_s != names_o:
        errs.append(f"{where}: w:rStyle should be {_plain(want)} in the source's place, got "
                    f"{_plain(dict(o_rs.attrib)) if o_rs is not None else None}")
    if [sig(c) for c in S if _local(c) not in ("rStyle", "rPrChange")] != \
            [sig(c) for c in O if _local(c) not in ("rStyle", "rPrChange")]:
        errs.append(f"{where}: run properties other than w:rStyle changed")
    _check_rpr_change(S, O, tracked, author, where, errs)


def _check_table(s_t, o_t, to: str, where: str, errs: List[str]) -> None:
    s_pr, o_pr = s_t.find(W + "tblPr"), o_t.find(W + "tblPr")
    s_ts = s_pr.find(W + "tblStyle")
    o_ts = o_pr.find(W + "tblStyle") if o_pr is not None else None
    want = {**dict(s_ts.attrib), W + "val": to}
    if o_ts is None or dict(o_ts.attrib) != want or len(o_ts) or list(s_pr).index(s_ts) != list(o_pr).index(o_ts):
        errs.append(f"{where}: w:tblStyle should be {_plain(want)} in the source's place, got "
                    f"{_plain(dict(o_ts.attrib)) if o_ts is not None else None}")


def _mapped_styles(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "mapped_styles"
    params = a.get("params") or {}
    sel = a.get("select") or {}
    if set(sel) - {"scope"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'scope'})}")
    scope = sel.get("scope", list(V_SCOPE))
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"{tag}: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    pairs = _v_map(params)
    existing = params.get("existing", "keep")
    allow = params.get("allow_missing", True)
    tracked = params.get("tracked", False)
    if existing not in ("keep", "replace") or not isinstance(allow, bool) or not isinstance(tracked, bool):
        raise ValueError("bad existing / allow_missing / tracked (the op must refuse: bad-params)")
    t_styles = None
    if params.get("template") is not None:
        md5, tpl = _template(a["template"])
        if md5 != a.get("template_md5"):
            errs.append(f"{tag}: template file changed since planning (md5 {md5} ≠ {a.get('template_md5')})")
            return tag
        tsp = tpl.related(STYLES_REL)
        t_styles = Styles(tpl.xml(tsp) if tsp else None)
    elif existing == "replace":
        raise ValueError("existing 'replace' without a template (the op must refuse: bad-params)")
    rows = _v_pairs(ctx, pairs, t_styles, existing)
    live = [r for r in rows if r["status"] == "mapped"]
    left = _v_unmapped(ctx, parts, {r["from_id"] for r in live if r["type"] == "character"})
    if left:
        raise ValueError(f"character style {left[0][1]} is also used in {left[0][0]} where the op does not restyle "
                         f"it (a content control, an equation or a list level; the op must refuse: structure)")
    by_type = {k: {r["from_id"]: r["to_id"] for r in live if r["type"] == k} for k in V_MAPPABLE}
    default_p = ctx.src_styles.default.get("paragraph")
    author = a.get("author")
    used: Dict[str, int] = {}
    changed: Dict[str, Dict[str, list]] = {}
    counts = {"paragraphs": 0, "runs": 0, "marks": 0, "tables": 0}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"{tag}: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        got: Dict[str, list] = {"paragraphs": [], "runs": [], "marks": [], "tables": []}
        for bi, p in enumerate(sv.pars):
            style = _pstyle(p) or default_p
            want_p = by_type["paragraph"].get(style)
            hits = [(kind, ri, by_type["character"][_v_rstyle(rpr)]) for kind, ri, rpr in _v_holders(p, sv.runs[bi])
                    if _v_rstyle(rpr) in by_type["character"]]
            if want_p is None and not hits:
                continue
            where = f"{part} block {bi}"
            if want_p is not None:
                got["paragraphs"].append(bi)
                used[style] = used.get(style, 0) + 1
            for kind, ri, _ in hits:
                rs = _v_rstyle(p.find(W + "pPr/" + W + "rPr") if kind == "mark" else sv.runs[bi][ri].find(W + "rPr"))
                used[rs] = used.get(rs, 0) + 1
                (got["marks"].append(bi) if kind == "mark" else got["runs"].append([bi, ri]))
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"{tag}: {where} is restyled but also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None:
                errs.append(f"{tag}: {where} has no counterpart in the output")
                continue
            o_p = ov.pars[oi]
            if want_p is not None:
                ctx.strip_ppr.setdefault(part, {}).setdefault(bi, set()).update({"pStyle", "pPrChange"})
                _check_paragraph(p.find(W + "pPr"), o_p, want_p, tracked, author, where, errs,
                                 any(kind == "mark" for kind, _, _ in hits))
            if hits and len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"{where}: run structure changed")
                continue
            for kind, ri, to in hits:
                if kind == "mark":
                    m = p.find(W + "pPr/" + W + "rPr")
                    if tracked and any(m.find(W + t) is not None for t in ("ins", "del", "moveFrom", "moveTo")):
                        raise ValueError(f"{where}: a tracked change of a paragraph mark that is a pending revision "
                                         f"(the op must refuse: tracked-format)")
                    ctx.strip_marks.setdefault(part, set()).add(bi)
                    _check_rpr(p.find(W + "pPr/" + W + "rPr"), o_p.find(W + "pPr/" + W + "rPr"), to, tracked, author,
                               f"{where} paragraph mark", errs)
                else:
                    ctx.strip_runs.setdefault(part, set()).add((bi, ri))
                    _check_rpr(sv.runs[bi][ri].find(W + "rPr"), ov.runs[oi][ri].find(W + "rPr"), to, tracked, author,
                               f"{where} run {ri}", errs)
        s_tables, o_tables = list(sv.root.iter(W + "tbl")), list(ov.root.iter(W + "tbl"))
        for k, tbl in enumerate(s_tables):
            ts = _v_tblstyle(tbl)
            if ts not in by_type["table"]:
                continue
            got["tables"].append(k)
            used[ts] = used.get(ts, 0) + 1
            if tracked:
                raise ValueError(f"{part} table {k}: a tracked table style change (the op must refuse: tracked-format)")
            if len(o_tables) != len(s_tables):
                errs.append(f"{tag}: {part} has {len(o_tables)} table(s) in the output, {len(s_tables)} in the source")
                continue
            ctx.strip_tbl_style.setdefault(part, set()).add(k)
            _check_table(tbl, o_tables[k], by_type["table"][ts], f"{part} table {k}", errs)
        for key in counts:
            counts[key] += len(got[key])
        if any(got.values()):
            changed[part] = got
    for r in live:
        if not used.get(r["from_id"]):
            r["status"] = "unused"
    if not allow and any(r["status"] in ("missing", "unused") for r in rows):
        raise ValueError("a pair with nothing to map and allow_missing false (the op must refuse: not-found)")
    plan_rows = [{k: r.get(k) for k in ("from", "to", "status", "from_id", "to_id", "type", "source")}
                 for r in a.get("pairs") or []]
    mine = [{k: r.get(k) for k in ("from", "to", "status", "from_id", "to_id", "type", "source")} for r in rows]
    if mine != plan_rows:
        errs.append(f"{tag}: the pairs resolve to {[(r['from'], r['status'], r['from_id'], r['to_id']) for r in mine]} "
                    f"here, plan says {[(r['from'], r['status'], r['from_id'], r['to_id']) for r in plan_rows]}")
    norm = lambda d: {p: {k: sorted(map(tuple, v)) if k == "runs" else sorted(v) for k, v in g.items() if v}
                      for p, g in (d or {}).items() if any(g.values())}
    if norm(changed) != norm(a.get("changed")):
        errs.append(f"{tag}: the elements restyled here {norm(changed)} are not the plan's {norm(a.get('changed'))}")
    expected = a.get("expected") or {}
    for key, n in counts.items():
        if n != expected.get(key, -1):
            errs.append(f"{tag}: {n} {key} restyled here, plan predicted {expected.get(key)}")
    active = [r for r in rows if r["status"] == "mapped"]
    requested = list(dict.fromkeys(r["to_id"] for r in active if r["source"] == "template"))
    check_requested(ctx, a, requested, existing, a.get("template"), errs, tag)
    for r in active:
        if r["to_id"] not in ctx.out_styles.by_id:
            errs.append(f"{tag}: the output has no style {r['to_id']} for what was mapped to it")
    return (f"{tag}: " + ", ".join(f"{r['from_id']}→{r['to_id']}" for r in active) + f" on {counts['paragraphs']} "
            f"paragraph(s), {counts['runs']} run(s), {counts['marks']} mark(s), {counts['tables']} table(s)"
            + (" (tracked)" if tracked else ""))


RESTYLE_KINDS = {"mapped_styles": _mapped_styles}
