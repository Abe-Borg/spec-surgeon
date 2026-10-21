"""V10 kinds ``inserted_section_breaks`` and ``removed_section_breaks`` — insert_section_break and remove_section_break,
verified (built-in batch 6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §8).

This module's own reading of the two ops (transform side: ops/insert_section_break.py, ops/remove_section_break.py and
ops/section_breaks.py). Nothing here comes from ops/ (rule 1):

* a section's START is its ``w:type`` and its page-number restart (``w:pgNumType/@w:start``); the rest of its sectPr is
  its setup. A start is written in place (``_v_give_start``): the w:type replaced where it stands, inserted in the
  bundled schema's order where there is none, removed for a next-page start; the restart written, or removed with a
  w:pgNumType left with nothing;
* a body paragraph's previous block is its nearest preceding sibling element that is not a range mark (``_V_MARKS``);
* insert: the matches are the SOURCE paragraphs ``para_matches`` chooses. Each must be a child of w:body (in a text box,
  a table or a content control: the op must refuse) whose previous block is a paragraph holding no sectPr and whose
  mark is no tracked insertion, deletion or move, in a section (``tabs._v_sections``) whose sectPr has no
  w:sectPrChange. The expected main part is the source with, per split section and in document order, the first match's
  previous paragraph given the section's sectPr as it is, every later one's a copy with the asked start and no restart,
  and the section's own sectPr given that start too — each copy as the last child of the w:pPr before a w:pPrChange (the
  schema's order), a w:pPr made first in a paragraph without one;
* remove: the chosen sections (``props._v_resolve_sections``; ``"all"`` is every section but the last; the last chosen:
  the op must refuse); each run a..b of them joins section b + 1, which takes section a's start and, after its own
  references, a copy of each header/footer reference of a kind and type it lacks — the latest joined section's —; every
  chosen sectPr leaves its paragraph (a w:pPr left with nothing goes). The relationships that go are those of the main
  part the source's tree names and the expected tree no longer does; the parts that go, those nothing that stays
  targets any more (each removed part's own targets in turn), with their .rels.

Each kind compares the expected main part WHOLE with the output's (``ctx.verified_parts``): another op changing the
main part beside these ops is a plan-time conflict (their whole-member touch), never something this check would see.
The remove kind also checks the main part's relationships and the content types (``markup``) and the parts the plan
removes against the re-derived ones; V1 holds the output to the plan's ``members_removed``.
"""
from __future__ import annotations

import copy
import posixpath
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import R, REL, W
from .markup import check_content_types, check_relationships, tree_difference
from .props import Ctx, PartView, _nearest_p, _v_resolve_sections, para_matches, sect_prs
from .tableprops import v_insert, v_order
from .tabs import _v_sections

_V_MARKS = {W + t for t in ("bookmarkStart", "bookmarkEnd", "commentRangeStart", "commentRangeEnd", "permStart",
                            "permEnd", "proofErr", "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart",
                            "moveToRangeEnd", "customXmlInsRangeStart", "customXmlInsRangeEnd",
                            "customXmlDelRangeStart", "customXmlDelRangeEnd", "customXmlMoveFromRangeStart",
                            "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd")}
_V_STARTS = {"next_page": None, "continuous": "continuous", "even_page": "evenPage", "odd_page": "oddPage"}
_V_REFS = (W + "headerReference", W + "footerReference")


def _v_prev(el):
    q = el.getprevious()
    while q is not None and (not isinstance(q.tag, str) or q.tag in _V_MARKS):
        q = q.getprevious()
    return q


def _v_mark_tracked(p) -> bool:
    rpr = p.find(W + "pPr/" + W + "rPr")
    return rpr is not None and any(c.tag in (W + "ins", W + "del", W + "moveFrom", W + "moveTo") for c in rpr)


def _v_start(sp) -> Tuple[Any, Optional[str]]:
    pg = sp.find(W + "pgNumType")
    return sp.find(W + "type"), (None if pg is None else pg.get(W + "start"))


def _v_give_start(sp, type_el, restart: Optional[str]) -> None:
    """``sp`` given the start (``type_el``, ``restart``) — see the module docstring."""
    order = v_order("CT_SectPr")
    old = sp.find(W + "type")
    new = None
    if type_el is not None:
        new = copy.deepcopy(type_el)
        new.tail = None
    if old is not None:
        if new is None:
            sp.remove(old)
        else:
            sp.insert(sp.index(old), new)
            sp.remove(old)
    elif new is not None:
        v_insert(sp, new, order)
    pg = sp.find(W + "pgNumType")
    if restart is not None:
        if pg is None:
            pg = etree.Element(W + "pgNumType")
            v_insert(sp, pg, order)
        pg.set(W + "start", restart)
    elif pg is not None and W + "start" in pg.attrib:
        del pg.attrib[W + "start"]
        if not len(pg.attrib) and not len(pg):
            sp.remove(pg)


def _v_break_el(asked: str):
    val = _V_STARTS[asked]
    if val is None:
        return None
    el = etree.Element(W + "type")
    el.set(W + "val", val)
    return el


def _v_targeted(pred) -> bool:
    return isinstance(pred, dict) and bool(set(pred) - {"case_insensitive", "flexible_whitespace"})


def _v_compare_whole(ctx: Ctx, kind: str, expected, errs: List[str]) -> None:
    main = ctx.src.main_part()
    if main not in ctx.out.names:
        errs.append(f"{kind}: {main} is missing from the output")
        return
    diff = tree_difference(expected, ctx.out.xml(main))
    if diff:
        errs.append(f"{kind}: {main} is not the source with exactly the predicted section change: {diff}")
    ctx.verified_parts.add(main)                     # compared whole: the residual leaves it out


# ---- insert_section_break ----------------------------------------------------------------------------------------------

def _v_insertions(ctx: Ctx, pred, errs: List[str]) -> List[Dict[str, int]]:
    """The breaks the user's selection asks for over the SOURCE, each (match, carrier, section); a match the op must
    refuse is an error and no break."""
    root = ctx.src.xml(ctx.src.main_part())
    body = root.find(W + "body")
    view = PartView(root)
    at = {p: i for i, p in enumerate(view.pars)}
    secs = sect_prs(root)
    of = _v_sections(root)
    out: List[Dict[str, int]] = []
    for bi, p in enumerate(view.pars):
        if not para_matches(pred, p, bi, view, ctx.src_styles):
            continue
        refuse = None
        prev = _v_prev(p)
        if _nearest_p(p) is not None:
            refuse = "is in a text box"
        elif p.getparent() is not body:
            refuse = "is not a top-level paragraph of the body"
        elif prev is None:
            refuse = "is the body's first block"
        elif prev.tag != W + "p":
            refuse = f"follows a {etree.QName(prev).localname}, not a paragraph"
        elif prev.find(W + "pPr/" + W + "sectPr") is not None:
            refuse = "already starts a section"
        elif _v_mark_tracked(prev):
            refuse = "follows a paragraph whose mark is a tracked change"
        elif secs[of[bi]].find(W + "sectPrChange") is not None:
            refuse = f"lies in section {of[bi]}, which carries a tracked section change"
        if refuse:
            errs.append(f"inserted_section_breaks: paragraph {bi} {refuse} — the op must refuse")
            continue
        out.append({"paragraph": bi, "carrier": at[prev], "section": of[bi]})
    return out


def _inserted_section_breaks(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    select, params = a.get("select"), a.get("params")
    if not isinstance(select, dict) or set(select) - {"paragraphs"} or not _v_targeted(select.get("paragraphs")):
        raise ValueError(f"select {select!r}: a targeted 'paragraphs' selector only — the op must refuse")
    if not isinstance(params, dict) or set(params) - {"type", "allow_missing"}:
        raise ValueError(f"params {params!r}: only type and allow_missing")
    asked = params.get("type", "next_page")
    allow = params.get("allow_missing", False)
    if asked not in _V_STARTS or not isinstance(allow, bool):
        raise ValueError(f"params {params!r} — the op must refuse")
    want = _v_insertions(ctx, select["paragraphs"], errs)
    if a.get("breaks") != want:
        errs.append(f"inserted_section_breaks: the selection asks for breaks {want} here, the plan has "
                    f"{a.get('breaks')}")
    if len(want) != a.get("expected_breaks", -1):
        errs.append(f"inserted_section_breaks: {len(want)} break(s) here, the plan predicted {a.get('expected_breaks')}")
    main = ctx.src.main_part()
    src_root = ctx.src.xml(main)
    if not want:
        if not allow:
            errs.append("inserted_section_breaks: no paragraph matches — the op must refuse (not-found) unless "
                        "allow_missing")
        return "inserted_section_breaks: no break asked here"
    expected = copy.deepcopy(src_root)
    pars, secs = list(expected.iter(W + "p")), sect_prs(expected)
    start = _v_break_el(asked)
    split: Dict[int, List[Dict[str, int]]] = {}
    for b in want:
        split.setdefault(b["section"], []).append(b)
    for g in sorted(split):
        own = copy.deepcopy(secs[g])
        own.tail = None
        for k, b in enumerate(split[g]):
            piece = copy.deepcopy(own)
            if k:
                _v_give_start(piece, start, None)
            carrier = pars[b["carrier"]]
            ppr = carrier.find(W + "pPr")
            if ppr is None:
                ppr = etree.Element(W + "pPr")
                carrier.insert(0, ppr)
            v_insert(ppr, piece, v_order("CT_PPr"))
        _v_give_start(secs[g], start, None)
    _v_compare_whole(ctx, "inserted_section_breaks", expected, errs)
    n = len(sect_prs(ctx.out.xml(main))) if main in ctx.out.names else -1
    if n != a.get("sections", -1) or n != len(sect_prs(src_root)) + len(want):
        errs.append(f"inserted_section_breaks: the output has {n} section(s), expected "
                    f"{len(sect_prs(src_root)) + len(want)} (the plan says {a.get('sections')})")
    return (f"inserted_section_breaks: {len(want)} break(s) ({asked}) in section(s) {sorted(split)} re-derived; the "
            f"main part compared whole")


# ---- remove_section_break ----------------------------------------------------------------------------------------------

def _v_runs(chosen: Sequence[int]) -> List[Tuple[int, int]]:
    runs: List[List[int]] = []
    for i in sorted(chosen):
        if runs and runs[-1][-1] + 1 == i:
            runs[-1].append(i)
        else:
            runs.append([i])
    return [(r[0], r[-1]) for r in runs]


def _v_refs(sp) -> List[Any]:
    return [c for c in sp if c.tag in _V_REFS]


def _v_key(ref) -> Tuple[str, str]:
    return etree.QName(ref).localname, ref.get(W + "type", "default")


def _v_used_ids(root) -> Set[str]:
    return {v for el in root.iter() if isinstance(el.tag, str) for k, v in el.attrib.items() if k.startswith(R)}


def _v_rels(pkg, part: str) -> Tuple[str, Dict[str, Optional[str]]]:
    """(the part's rels member, its relationships: Id → internal target or None)."""
    d, b = posixpath.split(part)
    member = posixpath.join(d, "_rels", b + ".rels")
    out: Dict[str, Optional[str]] = {}
    if member in pkg.names:
        for rel in pkg.xml(member).findall(REL + "Relationship"):
            t = rel.get("Target") or ""
            internal = rel.get("TargetMode", "Internal") == "Internal"
            out[rel.get("Id")] = (t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(d, t))) \
                if internal else None
    return member, out


def _v_gone_members(pkg, main: str, gone: Sequence[str]) -> List[str]:
    """The members that leave with the main part's relationships ``gone``: every target nothing that stays targets
    any more (a removed part's own targets in turn), with its .rels — in package order."""
    _, main_rels = _v_rels(pkg, main)
    edges = [(owner, target) for _, owner, _, target in pkg.all_relationships() if target and owner != main]
    edges += [(main, t) for rid, t in main_rels.items() if t and rid not in gone]
    removed: Set[str] = set()
    candidates = {main_rels[rid] for rid in gone if main_rels.get(rid)}
    while True:
        new = {c for c in candidates if c in pkg.names and c not in removed
               and not any(t == c and o not in removed and o != c for o, t in edges)}
        if not new:
            break
        removed |= new
        candidates |= {t for o, t in edges if o in new}
    rels = {posixpath.join(posixpath.dirname(m), "_rels", posixpath.basename(m) + ".rels") for m in removed}
    gone_members = removed | {r for r in rels if r in pkg.names}
    return [m for m in pkg.names if m in gone_members]


def _removed_section_breaks(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    select, params = a.get("select"), a.get("params")
    if not isinstance(select, dict) or set(select) - {"sections"}:
        raise ValueError(f"select {select!r}: only 'sections'")
    if not isinstance(params, dict) or set(params) - {"allow_missing"} or \
            not isinstance(params.get("allow_missing", False), bool):
        raise ValueError(f"params {params!r}: only allow_missing (true or false)")
    main = ctx.src.main_part()
    src_root = ctx.src.xml(main)
    body = src_root.find(W + "body")
    secs = sect_prs(src_root)
    n = len(secs)
    sections = select.get("sections", "all")
    if sections is None or sections == "all":
        chosen = list(range(n - 1))
    else:
        chosen = _v_resolve_sections(ctx.src, sections, ctx.src_styles)
        if n - 1 in chosen:
            errs.append(f"removed_section_breaks: the selection chooses the last section ({n - 1}), which ends the "
                        f"document at no break — the op must refuse")
            return "removed_section_breaks: the last section chosen"
    if a.get("chosen") != chosen:
        errs.append(f"removed_section_breaks: the selection chooses sections {chosen} here, the plan chose "
                    f"{a.get('chosen')}")
    for i in chosen:
        ppr = secs[i].getparent()
        p = ppr.getparent() if ppr is not None and ppr.tag == W + "pPr" else None
        if p is None or p.getparent() is not body:
            errs.append(f"removed_section_breaks: section {i}'s break is not in a top-level body paragraph — the op "
                        f"must refuse")
        elif secs[i].find(W + "sectPrChange") is not None or _v_mark_tracked(p):
            errs.append(f"removed_section_breaks: section {i}'s break carries a pending tracked change — the op must "
                        f"refuse")
    if not chosen:
        if isinstance(sections, dict) and not params.get("allow_missing", False):
            errs.append("removed_section_breaks: no section matches — the op must refuse (not-found) unless "
                        "allow_missing")
        return "removed_section_breaks: no break to remove here"
    runs = []
    for first, last in _v_runs(chosen):
        have = {_v_key(r) for r in _v_refs(secs[last + 1])}
        moved = []
        for i in range(last, first - 1, -1):
            for k, r in enumerate(_v_refs(secs[i])):
                if _v_key(r) not in have:
                    have.add(_v_key(r))
                    moved.append([i, k])
        runs.append({"first": first, "last": last, "target": last + 1, "moved": moved})
    if a.get("runs") != runs:
        errs.append(f"removed_section_breaks: the sections join as {runs} here, the plan has {a.get('runs')}")
    expected = copy.deepcopy(src_root)
    e_secs = sect_prs(expected)
    for run in runs:
        target = e_secs[run["target"]]
        copies = [copy.deepcopy(_v_refs(e_secs[i])[k]) for i, k in run["moved"]]
        _v_give_start(target, *_v_start(e_secs[run["first"]]))
        at = max((target.index(r) + 1 for r in _v_refs(target)), default=0)
        for offset, r in enumerate(copies):
            r.tail = None
            target.insert(at + offset, r)
    for i in chosen:
        ppr = e_secs[i].getparent()
        ppr.remove(e_secs[i])
        if not len(ppr) and not len(ppr.attrib):
            ppr.getparent().remove(ppr)
    _v_compare_whole(ctx, "removed_section_breaks", expected, errs)
    rels_member, main_rels = _v_rels(ctx.src, main)
    gone = sorted(rid for rid in (_v_used_ids(src_root) - _v_used_ids(expected)) if rid in main_rels)
    if sorted(a.get("gone_rids") or []) != gone:
        errs.append(f"removed_section_breaks: relationships no longer used here {gone}, the plan removes "
                    f"{a.get('gone_rids')}")
    members = _v_gone_members(ctx.src, main, gone)
    if sorted(a.get("members_removed") or []) != sorted(members):
        errs.append(f"removed_section_breaks: the members that leave here are {members}, the plan removes "
                    f"{a.get('members_removed')}")
    if gone:
        check_relationships(ctx.src, ctx.out, rels_member, gone, [], "removed_section_breaks", errs)
        _, overrides = ctx.src.content_types()
        check_content_types(ctx.src, ctx.out, [m for m in members if m in overrides], {}, "removed_section_breaks",
                            errs)
    out_n = len(sect_prs(ctx.out.xml(main))) if main in ctx.out.names else -1
    if out_n != n - len(chosen) or out_n != a.get("sections", -1):
        errs.append(f"removed_section_breaks: the output has {out_n} section(s), expected {n - len(chosen)}")
    return (f"removed_section_breaks: the breaks of sections {chosen} re-derived ({len(runs)} join(s), "
            f"{sum(len(r['moved']) for r in runs)} reference(s) moved, {len(members)} member(s) gone); the main part "
            f"compared whole")


SECTION_KINDS = {"inserted_section_breaks": _inserted_section_breaks,
                 "removed_section_breaks": _removed_section_breaks}
