"""V10 kind ``styles_imported`` — the shared style import, verified (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §5).

This module's own reading of the import's definitions (transform side: ops/style_import.py; CLAUDE.md lists them among the
definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1): the template is re-read
(``template._template``: md5 pinned), and from the assertion's ``requested`` template style ids and ``existing`` mode
alone this module re-derives

* the closure (requested styles; what every COPIED closure style names through ``w:basedOn`` / ``w:link`` / ``w:next``
  and the ``w:pStyle`` of every level of the numbering definition it uses — ``w:abstractNum/w:lvl`` and
  ``w:num/w:lvlOverride/w:lvl`` — to a fixpoint; a style the document holds and does not replace is not walked);
* imported (closure styles the SOURCE lacks, template order), replaced (``existing: "replace"``: the requested styles the
  source holds), held (the rest);
* every refusal the op must have raised (a ValueError here: a held style that is another style under the template
  style's id (names compared whitespace-collapsed and casefolded), a default style imported or replaced, a replaced
  style of another type, a name another style holds after the import, ``mc:AlternateContent`` in a copy, numbering the op
  cannot copy — no numbering part, a missing num/abstractNum, ``w:numStyleLink`` / ``w:styleLink``,
  ``w:lvlPicBulletId``, a numId that is not a whole number, a level naming a held style the document numbers with its
  own list);
* the new numbering ids (template numId first met → above every numId the source defines or refers to — ``v_used_ids``
  —, …; its abstractNum first met → above every abstractNumId defined or referred to, …; none → 1 / 0) and every expected
  copy (the template's definition, extension markup stripped — ``template.strip_extensions`` —, the ids set).

It fails when those differ from the plan's predictions; when an imported style is not new, not there exactly once, not
equal to its expected copy, or not appended (after every style the source had, in the template's order); when a replaced
style is not equal to its expected copy or left its place (the source styles keep their order in the output); when a
held style the copies name is missing from the output; and — the
numbering member is under no residual — when the output's numbering part is not exactly the source's with the expected
abstractNums after its last abstractNum (else before its first num, else before ``w:numIdMacAtCleanup``, else at the end)
and the expected nums after its last num (else before ``w:numIdMacAtCleanup``, else at the end) — and, when it copies no
list, when the numbering part changed at all (``check_numbering_unchanged``; skipped only when another assertion of
the plan owns the member's comparison: a ``styles_imported`` assertion that copies lists, or set_list_level's
``list_level`` — built-in batch 5 —, each comparing the member whole). It registers the imported
styles (``added_styles``), the replaced ones (``replaced_styles``) and the numbering part (``verified_members``).

``check_requested`` (reused by every op kind over the import — ``mapped_styles`` and apply_style's): the op's own kind
derives which template styles the op needed and this checks the ``styles_imported`` assertion of the same op carries
exactly those (and the same mode and template), or that there is none when nothing is needed — and then that the
numbering part is unchanged (``check_numbering_unchanged``: numbering.xml is under no residual).
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import STYLES_REL, W
from .props import Ctx, Styles, first_difference, sig
from .template import _template, strip_extensions

NUMBERING_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
MC_ALTERNATE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
V_TRUE = {"1", "true", "on"}


def v_type(st) -> str:
    return st.get(W + "type") or "paragraph"


def v_name(st) -> Optional[str]:
    e = st.find(W + "name")
    return e.get(W + "val") if e is not None else None


def _v_is_default(st) -> bool:
    return (st.get(W + "default") or "") in V_TRUE


def _v_refs(st) -> List[str]:
    return [e.get(W + "val") for e in (st.find(W + t) for t in ("basedOn", "link", "next"))
            if e is not None and e.get(W + "val")]


def _v_whole(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _v_num_id(st) -> Optional[int]:
    """A style's own numbering id (w:pPr/w:numPr/w:numId), None when absent or 0; ValueError when not a whole number."""
    e = st.find(W + "pPr/" + W + "numPr/" + W + "numId")
    if e is None:
        return None
    n = _v_whole(e.get(W + "val"))
    if n is None:
        raise ValueError(f"style {st.get(W + 'styleId')}: numbering id {e.get(W + 'val')!r} is not a whole number "
                         f"(the op must refuse: style-numbering)")
    return n if n != 0 else None


def _v_lists(root) -> Tuple[Dict[int, Any], Dict[int, Any]]:
    """(numId → w:num, abstractNumId → w:abstractNum) of a numbering root (whole-number ids only)."""
    nums, abstracts = {}, {}
    if root is None:
        return nums, abstracts
    for c in root:
        if c.tag == W + "num" and _v_whole(c.get(W + "numId")) is not None:
            nums.setdefault(_v_whole(c.get(W + "numId")), c)
        elif c.tag == W + "abstractNum" and _v_whole(c.get(W + "abstractNumId")) is not None:
            abstracts.setdefault(_v_whole(c.get(W + "abstractNumId")), c)
    return nums, abstracts


def _v_num_abstract(num) -> Optional[int]:
    e = num.find(W + "abstractNumId")
    return _v_whole(e.get(W + "val")) if e is not None else None


def _v_definition(nums, abstracts, n: int, sid: str) -> Tuple[Any, Any]:
    num = nums.get(n)
    absn = abstracts.get(_v_num_abstract(num)) if num is not None else None
    if num is None or absn is None:
        raise ValueError(f"template style {sid}: list {n} is not defined in the template (the op must refuse: "
                         f"style-numbering)")
    return num, absn


def _v_levels(num, absn) -> List[Any]:
    return [lv for lv in absn if lv.tag == W + "lvl"] + \
        [lv for o in num if o.tag == W + "lvlOverride" for lv in o if lv.tag == W + "lvl"]


def _v_key(name: Optional[str]) -> Optional[str]:
    """A style name as this module compares names: whitespace collapsed, casefolded (None for none)."""
    return " ".join(name.split()).casefold() if name is not None else None


def _v_has_alternate(el) -> bool:
    return any(True for _ in el.iter(MC_ALTERNATE))


def _v_chain_list(styles: Styles, sid: str) -> Optional[int]:
    """The list a style of the document numbers with: the first w:pPr/w:numPr/w:numId on its basedOn chain (itself
    first), a whole number other than 0; else None."""
    for st in styles.chain(sid):
        e = st.find(W + "pPr/" + W + "numPr/" + W + "numId")
        if e is not None:
            n = _v_whole(e.get(W + "val"))
            return n if n else None
    return None


def v_used_ids(pkg, numbering_root) -> Tuple[int, int]:
    """(largest numId, largest abstractNumId) the document defines OR refers to (-1 when none): every w:numId/@w:val of
    its text parts, its styles part and its numbering part, w:num/@w:numId, w:numIdMacAtCleanup/@w:val;
    w:abstractNum/@w:abstractNumId and every w:abstractNumId/@w:val."""
    num_ids: List[int] = []
    abs_ids: List[int] = []
    parts = list(pkg.text_parts())
    sp = pkg.related(STYLES_REL)
    if sp:
        parts.append(sp)
    for part in parts:
        for e in pkg.xml(part).iter():
            if e.tag == W + "numId":
                num_ids.append(_v_whole(e.get(W + "val")))
    if numbering_root is not None:
        for e in numbering_root.iter():
            if e.tag in (W + "numId", W + "numIdMacAtCleanup"):
                num_ids.append(_v_whole(e.get(W + "val")))
            elif e.tag == W + "num":
                num_ids.append(_v_whole(e.get(W + "numId")))
            elif e.tag == W + "abstractNum":
                abs_ids.append(_v_whole(e.get(W + "abstractNumId")))
            elif e.tag == W + "abstractNumId":
                abs_ids.append(_v_whole(e.get(W + "val")))
    return (max([n for n in num_ids if n is not None], default=-1),
            max([a for a in abs_ids if a is not None], default=-1))


def v_derive_import(src_styles: Styles, src_numbering, t_styles: Styles, t_numbering, requested: Sequence[str],
                    existing: str, used: Optional[Tuple[int, int]] = None) -> Dict[str, Any]:
    """The import re-derived from the SOURCE and the template: closure, imported, replaced, held, num_map, abs_map, the
    expected copies (``styles``: id → element, ``abstracts`` / ``nums``: in new-id order). ValueError where the op must
    refuse. ``t_numbering``: the template's numbering root, or a function giving it (read only when a copied style is
    numbered). ``used``: ``v_used_ids`` of the document (the largest numId and abstractNumId it defines or refers to);
    None reads the numbering root's own definitions only."""
    if existing not in ("keep", "replace"):
        raise ValueError(f"existing {existing!r} (the op must refuse)")
    if t_styles.root is None:
        raise ValueError("the template has no styles part (the op must refuse)")
    req = list(dict.fromkeys(requested))
    absent = [s for s in req if s not in t_styles.by_id]
    if absent:
        raise ValueError(f"the template has no style {absent} (the op must refuse)")
    t_order: List[str] = []
    for st in t_styles.root.findall(W + "style"):
        sid = st.get(W + "styleId")
        if sid and sid not in t_order:
            t_order.append(sid)
    lists: Dict[str, Any] = {}

    def t_lists() -> Tuple[Dict[int, Any], Dict[int, Any]]:
        if "v" not in lists:
            lists["v"] = _v_lists(t_numbering() if callable(t_numbering) else t_numbering)
        return lists["v"]

    def is_copied(sid: str) -> bool:
        return sid not in src_styles.by_id or (existing == "replace" and sid in req)

    members: Set[str] = set()
    frontier: Set[str] = set(req)
    while frontier:
        reached: Set[str] = set()
        for sid in frontier:
            if sid in members or sid not in t_styles.by_id:
                continue
            members.add(sid)
            if not is_copied(sid):
                continue
            st = t_styles.by_id[sid]
            reached.update(_v_refs(st))
            n = _v_num_id(st)
            if n is not None:
                num, absn = _v_definition(*t_lists(), n, sid)
                for lv in _v_levels(num, absn):
                    ps = lv.find(W + "pStyle")
                    if ps is not None and ps.get(W + "val"):
                        reached.add(ps.get(W + "val"))
        frontier = reached - members
    imported = [s for s in t_order if s in members and s not in src_styles.by_id]
    replaced = [s for s in t_order if s in members and s in src_styles.by_id and existing == "replace" and s in req]
    held = sorted(s for s in members if not is_copied(s))
    copied = [s for s in t_order if s in set(imported) | set(replaced)]
    if req and src_styles.root is None:
        raise ValueError("the document has no styles part (the op must refuse: style-import)")
    for s in held:
        if _v_key(v_name(src_styles.by_id[s])) != _v_key(v_name(t_styles.by_id[s])):
            raise ValueError(f"{s} is {v_name(t_styles.by_id[s])!r} in the template and {v_name(src_styles.by_id[s])!r} "
                             f"in the document — two styles under one id (the op must refuse: style-import)")
    for s in imported:
        if _v_is_default(t_styles.by_id[s]):
            raise ValueError(f"{s} is a default style in the template (the op must refuse: style-import)")
    for s in replaced:
        a, b = src_styles.by_id[s], t_styles.by_id[s]
        if _v_is_default(a) or _v_is_default(b) or v_type(a) != v_type(b):
            raise ValueError(f"{s} cannot be replaced: a default style, or of another type (the op must refuse: "
                             f"style-import)")
    for s in copied:
        if _v_has_alternate(t_styles.by_id[s]):
            raise ValueError(f"{s} holds mc:AlternateContent (the op must refuse: style-import)")
    copies = {s: strip_extensions(t_styles.by_id[s]) for s in copied}
    owners: Dict[str, Set[str]] = {}
    for s, st in list(src_styles.by_id.items()):
        if s not in copies and v_name(st):
            owners.setdefault(_v_key(v_name(st)), set()).add(s)
    for s, c in copies.items():
        if v_name(c):
            owners.setdefault(_v_key(v_name(c)), set()).add(s)
    for s, c in copies.items():
        if v_name(c) and owners[_v_key(v_name(c))] - {s}:
            raise ValueError(f"{s} is named {v_name(c)!r}, which another style holds after the import (the op must "
                             f"refuse: style-import)")
    # numbering
    numbered = [(s, _v_num_id(t_styles.by_id[s])) for s in copied]
    numbered = [(s, n) for s, n in numbered if n is not None]
    num_map: Dict[str, str] = {}
    abs_map: Dict[str, str] = {}
    abstracts: List[Any] = []
    nums: List[Any] = []
    if numbered:
        if src_numbering is None:
            raise ValueError("numbered styles and no numbering part in the document (the op must refuse: "
                             "style-numbering)")
        t_nums, t_abs = t_lists()
        held_set = set(held)
        for s, n in numbered:
            num, absn = _v_definition(t_nums, t_abs, n, s)
            if absn.find(W + "numStyleLink") is not None or absn.find(W + "styleLink") is not None:
                raise ValueError(f"list {n} links a numbering style (the op must refuse: style-numbering)")
            if any(lv.find(W + "lvlPicBulletId") is not None for lv in _v_levels(num, absn)):
                raise ValueError(f"list {n} has a picture bullet (the op must refuse: style-numbering)")
            if _v_has_alternate(num) or _v_has_alternate(absn):
                raise ValueError(f"list {n} holds mc:AlternateContent (the op must refuse: style-numbering)")
            for lv in _v_levels(num, absn):
                ps = lv.find(W + "pStyle")
                lvl_style = ps.get(W + "val") if ps is not None else None
                if lvl_style in held_set and _v_chain_list(src_styles, lvl_style) is not None:
                    raise ValueError(f"list {n} names {lvl_style} for a level, which the document numbers with its own "
                                     f"list (the op must refuse: style-numbering)")
        if used is None:
            s_nums, s_abs = _v_lists(src_numbering)
            used = (max(s_nums, default=-1), max(s_abs, default=-1))
        new_num = max(used[0], 0) + 1
        new_abs = used[1] + 1
        for s, n in numbered:
            if str(n) in num_map:
                continue
            num_map[str(n)] = str(new_num)
            new_num += 1
            a = _v_num_abstract(t_nums[n])
            if str(a) not in abs_map:
                abs_map[str(a)] = str(new_abs)
                new_abs += 1
        for old, new in sorted(abs_map.items(), key=lambda kv: int(kv[1])):
            c = strip_extensions(t_abs[int(old)])
            c.set(W + "abstractNumId", new)
            abstracts.append(c)
        for old, new in sorted(num_map.items(), key=lambda kv: int(kv[1])):
            c = strip_extensions(t_nums[int(old)])
            c.set(W + "numId", new)
            c.find(W + "abstractNumId").set(W + "val", abs_map[str(_v_num_abstract(t_nums[int(old)]))])
            nums.append(c)
    for s, c in copies.items():
        e = c.find(W + "pPr/" + W + "numPr/" + W + "numId")
        n = _v_num_id(c)
        if e is not None and n is not None:
            e.set(W + "val", num_map[str(n)])
    return {"closure": members, "imported": imported, "replaced": replaced, "held": held, "num_map": num_map,
            "abs_map": abs_map, "styles": copies, "abstracts": abstracts, "nums": nums}


def v_expected_numbering(src_root, abstracts: Sequence[Any], nums: Sequence[Any]):
    """The source numbering root with the copies in their places (a new tree)."""
    out = copy.deepcopy(src_root)
    kids = list(out)

    def last_of(tag: str) -> Optional[int]:
        hits = [i for i, c in enumerate(kids) if c.tag == W + tag]
        return hits[-1] if hits else None

    def first_of(tags: Sequence[str]) -> Optional[int]:
        return next((i for i, c in enumerate(kids) if c.tag in {W + t for t in tags}), None)
    a_at = last_of("abstractNum")
    a_at = a_at + 1 if a_at is not None else first_of(("num", "numIdMacAtCleanup"))
    a_at = len(kids) if a_at is None else a_at
    kids[a_at:a_at] = [copy.deepcopy(a) for a in abstracts]
    n_at = last_of("num")
    n_at = n_at + 1 if n_at is not None else first_of(("numIdMacAtCleanup",))
    n_at = len(kids) if n_at is None else n_at
    kids[n_at:n_at] = [copy.deepcopy(n) for n in nums]
    for c in list(out):
        out.remove(c)
    for c in kids:
        out.append(c)
    return out


def _v_numbering_root(pkg) -> Tuple[Optional[str], Any]:
    part = pkg.related(NUMBERING_REL)
    return part, (pkg.xml(part) if part else None)


def _style_ids(root) -> List[str]:
    return [st.get(W + "styleId") for st in (root.findall(W + "style") if root is not None else [])]


def check_styles_imported(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "styles_imported"
    if ctx.styles_part is None or a.get("styles_part") != ctx.styles_part:
        errs.append(f"{tag}: the styles part is {ctx.styles_part!r} here, the plan says {a.get('styles_part')!r}")
        return tag
    md5, tpl = _template(a["template"])
    if md5 != a.get("template_md5"):
        errs.append(f"{tag}: template file changed since planning (md5 {md5} ≠ {a.get('template_md5')})")
        return tag
    tsp = tpl.related(STYLES_REL)
    t_styles = Styles(tpl.xml(tsp) if tsp else None)

    def t_num():                                       # read only when a copied style is numbered
        part = tpl.related(NUMBERING_REL)
        try:
            return tpl.xml(part) if part else None
        except etree.XMLSyntaxError as e:
            raise ValueError(f"the template's numbering part is not well-formed ({e}) (the op must refuse)")
    s_npart, s_num = _v_numbering_root(ctx.src)
    o_npart, _ = _v_numbering_root(ctx.out)
    d = v_derive_import(ctx.src_styles, s_num, t_styles, t_num, a.get("requested") or [], a.get("existing", "keep"),
                        used=v_used_ids(ctx.src, s_num))
    for key in ("imported", "replaced"):
        if list(d[key]) != list(a.get(key) or []):
            errs.append(f"{tag}: {key} {d[key]} here, plan predicted {a.get(key)}")
    for key in ("num_map", "abs_map"):
        if d[key] != dict(a.get(key) or {}):
            errs.append(f"{tag}: list ids {key} {d[key]} here, plan predicted {a.get(key)}")
    src_ids, out_ids = _style_ids(ctx.src_styles_root), _style_ids(ctx.out_styles_root)
    out_els = ctx.out_styles_root.findall(W + "style") if ctx.out_styles_root is not None else []
    for sid in d["imported"] + d["replaced"]:
        new = sid in d["imported"]
        if new and sid in src_ids:
            errs.append(f"{tag}: {sid} is imported but the source already had it")
        if out_ids.count(sid) != 1:
            errs.append(f"{tag}: {sid} occurs {out_ids.count(sid)} time(s) in the output's styles, expected once")
            continue
        got = next(st for st in out_els if st.get(W + "styleId") == sid)
        if sig(got) != sig(d["styles"][sid]):
            errs.append(f"{tag}: {'imported' if new else 'replaced'} style {sid} is not the template's definition "
                        f"(extension markup stripped, list ids set): {first_difference(d['styles'][sid], got)}")
        (ctx.added_styles if new else ctx.replaced_styles).add(sid)
    src_set, out_set = set(src_ids), set(out_ids)
    if [s for s in out_ids if s in src_set] != [s for s in src_ids if s in out_set]:
        errs.append(f"{tag}: the source's styles are not in their own order in the output (a replaced style takes its "
                    f"old place)")
    held_at = [i for i, s in enumerate(out_ids) if s in src_set]
    at = [out_ids.index(s) for s in d["imported"] if s in out_ids]
    if held_at and any(i < max(held_at) for i in at):
        errs.append(f"{tag}: imported styles {d['imported']} are not appended after the styles the source had")
    if at != sorted(at):
        errs.append(f"{tag}: imported styles are not in the template's order {d['imported']}")
    gone = [s for s in d["held"] if s not in out_set]
    if gone:
        errs.append(f"{tag}: the output lacks {gone}, which the copies name (basedOn / link / next / a list level)")
    if d["num_map"]:
        if s_npart is None or s_npart != o_npart or s_npart != a.get("numbering_part"):
            errs.append(f"{tag}: numbering part {s_npart!r} → {o_npart!r} (plan: {a.get('numbering_part')!r})")
        else:
            want = v_expected_numbering(s_num, d["abstracts"], d["nums"])
            got = ctx.out.xml(o_npart)
            if sig(want) != sig(got):
                errs.append(f"{tag}: {o_npart} is not the source's with exactly the imported lists in their places: "
                            f"{first_difference(want, got)}")
            ctx.verified_members.add(o_npart)
    else:
        check_numbering_unchanged(ctx, errs, tag, a)
    return (f"{tag}: from {Path(a['template']).name} imported {d['imported']}, replaced {d['replaced']}"
            + (f", lists {d['num_map']} / {d['abs_map']}" if d["num_map"] else ""))


NUMBERING_OWNER_KINDS = ("list_level",)


def check_numbering_unchanged(ctx: Ctx, errs: List[str], tag: str, exclude: Optional[Dict[str, Any]] = None) -> None:
    """``numbering.xml`` is under no residual: an op over the style import that copies no list must leave it exactly as
    it was — compared whole and registered (``verified_members``) — unless another assertion of the plan OWNS the
    member's comparison: a ``styles_imported`` assertion that copies lists (its own check compares the member whole with
    the source plus its lists), or one of ``NUMBERING_OWNER_KINDS`` (built-in batch 5: ``list_level`` — set_list_level's
    kind compares the member whole with the source plus exactly its one level's change). Standing down is not weaker:
    the owner's whole comparison fails on ANY other change to the member — a list this op copied in secret included —
    exactly as this one would, and an op changing numbering beside it is a plan-time conflict (the import with lists
    touches ``(numbering, "*", "add")``, set_list_level ``(numbering, "*", "lvl")``: both whole-member changes)."""
    for x in ctx.plan.get("property_assertions", []) or []:
        if x is exclude:
            continue
        if (x.get("kind") == "styles_imported" and x.get("num_map")) or x.get("kind") in NUMBERING_OWNER_KINDS:
            return
    s_part, o_part = ctx.src.related(NUMBERING_REL), ctx.out.related(NUMBERING_REL)
    if s_part is None and o_part is None:
        return
    if s_part != o_part:
        errs.append(f"{tag}: the numbering part is {s_part!r} in the source and {o_part!r} in the output")
        return
    if ctx.src.read(s_part) != ctx.out.read(o_part):
        want, got = ctx.src.xml(s_part), ctx.out.xml(o_part)
        if sig(want) != sig(got):
            errs.append(f"{tag}: {o_part} changed although no list is copied: {first_difference(want, got)}")
    ctx.verified_members.add(o_part)


def check_requested(ctx: Ctx, a: Dict[str, Any], requested: Sequence[str], existing: str, template: Optional[str],
                    errs: List[str], tag: str) -> None:
    """``requested`` — the template styles the op's own kind derived it needed: the ``styles_imported`` assertion of the
    same op (``op_index``) must carry exactly those, the same ``existing`` and the same template; none when none — and
    then the numbering part is left exactly as it was (``check_numbering_unchanged``)."""
    mine = [x for x in ctx.plan.get("property_assertions", []) or []
            if x.get("kind") == "styles_imported" and x.get("op_index") == a.get("op_index")]
    if not requested:
        if mine:
            errs.append(f"{tag}: a style import is asserted ({mine[0].get('requested')}) but nothing needs one here")
        check_numbering_unchanged(ctx, errs, tag)
        return
    if len(mine) != 1:
        errs.append(f"{tag}: the template styles {list(requested)} are needed here and {len(mine)} styles_imported "
                    f"assertion(s) of this op check their import")
        return
    m = mine[0]
    if sorted(m.get("requested") or []) != sorted(requested):
        errs.append(f"{tag}: the import asserts {m.get('requested')}, the styles needed here are {list(requested)}")
    if m.get("existing", "keep") != existing:
        errs.append(f"{tag}: the import's existing mode {m.get('existing')!r} is not the op's {existing!r}")
    if template is None or Path(m.get("template") or "").resolve() != Path(template).resolve():
        errs.append(f"{tag}: the import reads {m.get('template')!r}, the op {template!r}")


IMPORT_KINDS = {"styles_imported": check_styles_imported}
