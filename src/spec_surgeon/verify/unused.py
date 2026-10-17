"""V10 kind ``removed_styles`` — remove_unused_styles, verified (built-in batch 4, item 32 —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §8).

This module's own reading of the op's definitions (transform side: ops/remove_unused_styles.py; CLAUDE.md lists them
among the definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* the parameters are read from the user's own words with this module's defaults (keep none, which ``all``, every type);
* the style table and its duplicate-id refusal (``_v_styles``), a style's names — id, ``w:name``, aliases, casefolded
  (``_v_names``) —, the scanned parts (``_v_parts``), the referencing elements (``_v_refs``), a part's field instruction
  text (``_v_instr``), the styles fields name or a field update applies (``_v_named``), the keep entries (``_v_kept``),
  eligibility (``_v_eligible``), the basedOn/next/link closure (``_v_reach``) and the removable list (``_v_removable``).

Fails when the removable list differs from the plan's; when a style it says goes is still defined in the output, or one
that stays is not; and — a post-condition on the OUTPUT — when a style-referencing element of a text, numbering or
settings part, a ``w:basedOn``/``w:next``/``w:link`` of a style, or a field instruction (by name, or a field update's
TOC/INDEX/TOA styles) names a style the source defined and the output no longer does: removal never leaves a dangling
reference. Registers ``ctx.removed_styles`` (its OWN list) for the styles residual, which then requires the output's
styles part to be the source's minus exactly those definitions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .props import Ctx
from .reader import VPackage, W

_V_TYPES = ("paragraph", "character", "table", "numbering")
_V_ON = {"1", "true", "on"}
_V_REF_TAGS = tuple(W + n for n in ("pStyle", "rStyle", "tblStyle", "numStyleLink", "styleLink", "defaultTableStyle",
                                    "clickAndTypeStyle"))
_V_LINKS = ("basedOn", "next", "link")
_V_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_V_SIDE_RELS = (_V_REL + "numbering", _V_REL + "settings")
_V_FIELD_APPLIES = {"toc": {"toc %d" % i for i in range(1, 10)} | {"table of figures"},
                    "index": {"index %d" % i for i in range(1, 10)} | {"index heading"},
                    "toa": {"table of authorities", "toa heading"}}

Rows = List[Tuple[Optional[str], Any]]


def _v_words(params: Dict[str, Any]) -> Tuple[List[str], str, Set[str]]:
    keep = params.get("keep", [])
    which = params.get("which", "all")
    types = params.get("types", list(_V_TYPES))
    if not isinstance(keep, list) or any(not isinstance(k, str) or not k.strip() for k in keep):
        raise ValueError(f"bad keep {keep!r}")
    if which not in ("all", "custom"):
        raise ValueError(f"bad which {which!r}")
    if not isinstance(types, list) or not types or any(t not in _V_TYPES for t in types):
        raise ValueError(f"bad types {types!r}")
    return keep, which, set(types)


def _v_styles(root) -> Rows:
    """(styleId or None, element) of every top-level w:style; ValueError for two with one id (the op must refuse)."""
    rows: Rows = []
    ids: Set[str] = set()
    for el in root.iterchildren(W + "style"):
        sid = el.get(W + "styleId")
        if sid is not None:
            if sid in ids:
                raise ValueError(f"style {sid!r} is defined twice (the op must refuse)")
            ids.add(sid)
        rows.append((sid, el))
    return rows


def _v_names(sid: Optional[str], el) -> Set[str]:
    words = [sid] if sid else []
    name = el.find(W + "name")
    if name is not None and name.get(W + "val"):
        words.append(name.get(W + "val"))
    aliases = el.find(W + "aliases")
    if aliases is not None and aliases.get(W + "val"):
        words.extend(aliases.get(W + "val").split(","))
    return {w.strip().casefold() for w in words if w.strip()}


def _v_eligible(sid: Optional[str], el, which: str, types: Set[str]) -> bool:
    if sid is None or (el.get(W + "type") or "paragraph") not in types:
        return False
    return which == "all" or el.get(W + "customStyle") in _V_ON


def _v_parts(pkg: VPackage) -> Tuple[List[str], List[str]]:
    """(text parts, numbering and settings parts) through the main part's relationships."""
    side: List[str] = []
    for r in pkg.rels_targets(pkg.main_part()).values():
        if r["type"] in _V_SIDE_RELS and r["target"] in pkg.names and r["target"] not in side:
            side.append(r["target"])
    return pkg.text_parts(), side


def _v_refs(root) -> List[Tuple[str, str]]:
    """(element's local name, its w:val) of every style-referencing element anywhere in the part."""
    return [(el.tag[len(W):], el.get(W + "val")) for el in root.iter(*_V_REF_TAGS) if el.get(W + "val") is not None]


def _v_instr(root) -> str:
    out: List[str] = []
    for el in root.iter(W + "instrText", W + "delInstrText", W + "fldSimple"):
        if el.tag == W + "fldSimple":
            out.append(el.get(W + "instr") or "")
        else:
            out.append(el.text or "")
    return "".join(out).casefold()


def _v_named(rows: Rows, text: str) -> Set[str]:
    """The ids a part's instruction text names (a name as a substring) or whose field update applies them."""
    if not text:
        return set()
    applied: Set[str] = set()
    for key, names in _V_FIELD_APPLIES.items():
        if key in text:
            applied |= names
    return {sid for sid, el in rows if sid is not None
            and (any(n in text for n in _v_names(sid, el)) or _v_names(sid, el) & applied)}


def _v_kept(rows: Rows, keep: Sequence[str]) -> Set[str]:
    wanted = {k.strip().casefold() for k in keep}
    return {sid for sid, el in rows if sid is not None and _v_names(sid, el) & wanted}


def _v_reach(ids: Set[str], els: List[Any], by_id: Dict[str, Any]) -> Set[str]:
    """``ids`` and the ids of ``els``, closed under w:basedOn / w:next / w:link."""
    reached = set(ids) | {el.get(W + "styleId") for el in els if el.get(W + "styleId") is not None}
    frontier = list(els) + [by_id[i] for i in ids if i in by_id]
    while frontier:
        nxt = []
        for el in frontier:
            for tag in _V_LINKS:
                c = el.find(W + tag)
                v = c.get(W + "val") if c is not None else None
                if v is not None and v not in reached:
                    reached.add(v)
                    if v in by_id:
                        nxt.append(by_id[v])
        frontier = nxt
    return reached


def _v_removable(pkg: VPackage, rows: Rows, keep: Sequence[str], which: str,
                 types: Set[str]) -> Tuple[List[str], Set[str]]:
    """(the styles that go, in the part's order; the ids that stay) — from the SOURCE."""
    by_id = {sid: el for sid, el in rows if sid is not None}
    text_parts, side = _v_parts(pkg)
    used = {sid for sid, el in rows if sid is not None and el.get(W + "default") in _V_ON}
    for part in text_parts + side:
        root = pkg.xml(part)
        used |= {v for _, v in _v_refs(root)}
        if part in text_parts:
            used |= _v_named(rows, _v_instr(root))
    used |= _v_kept(rows, keep)
    stays = _v_reach(used, [el for sid, el in rows if not _v_eligible(sid, el, which, types)], by_id)
    return [sid for sid, el in rows if _v_eligible(sid, el, which, types) and sid not in stays], stays


def _v_dangling(ctx: Ctx, src_rows: Rows, out_rows: Rows) -> List[str]:
    """Post-condition on the OUTPUT: every reference to a style the source defined still finds its definition."""
    src_ids = {sid for sid, _ in src_rows if sid is not None}
    out_ids = {sid for sid, _ in out_rows if sid is not None}
    gone = src_ids - out_ids
    if not gone:
        return []
    problems: List[str] = []
    text_parts, side = _v_parts(ctx.out)
    for part in text_parts + side:
        root = ctx.out.xml(part)
        for local, v in _v_refs(root):
            if v in gone:
                problems.append(f"{part}: a w:{local} names style {v!r}, which the output no longer defines")
        if part in text_parts:
            for sid in sorted(_v_named([r for r in src_rows if r[0] in gone], _v_instr(root))):
                problems.append(f"{part}: a field instruction names style {sid!r} (or a field update applies it), "
                                f"which the output no longer defines")
    for sid, el in out_rows:
        for tag in _V_LINKS:
            c = el.find(W + tag)
            if c is not None and c.get(W + "val") in gone:
                problems.append(f"style {sid!r}: its w:{tag} names style {c.get(W + 'val')!r}, which the output no "
                                f"longer defines")
    return problems


def _removed_styles(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    keep, which, types = _v_words(a.get("params") or {})
    planned = list(a.get("expected_removed") or [])
    if ctx.src_styles_root is None:
        if planned:
            errs.append(f"removed_styles: the source has no styles part, the plan removes {planned}")
        return "removed_styles: no styles part — nothing to remove"
    if ctx.styles_part is None:
        errs.append("removed_styles: the output has no styles part where the source has one")
        return "removed_styles: no common styles part"
    if a.get("styles_part") != ctx.styles_part:
        errs.append(f"removed_styles: the styles part is {ctx.styles_part} here, the plan says {a.get('styles_part')}")
    rows = _v_styles(ctx.src_styles_root)
    removed, stays = _v_removable(ctx.src, rows, keep, which, types)
    if sorted(removed) != sorted(planned):
        extra, missed = sorted(set(planned) - set(removed)), sorted(set(removed) - set(planned))
        errs.append(f"removed_styles: {len(removed)} style(s) are unused here, the plan removes {len(planned)}"
                    + (f"; the plan removes {extra[:12]}, which are used here" if extra else "")
                    + (f"; unused here and kept by the plan: {missed[:12]}" if missed else ""))
    ctx.removed_styles.update(removed)             # the styles residual: output = source minus exactly these
    try:
        out_rows = _v_styles(ctx.out_styles_root)
    except ValueError as e:
        errs.append(f"removed_styles: the output's styles part is malformed — {e}")
        out_rows = [(el.get(W + "styleId"), el) for el in ctx.out_styles_root.iterchildren(W + "style")]
    out_ids = {sid for sid, _ in out_rows if sid is not None}
    left = [sid for sid in removed if sid in out_ids]
    if left:
        errs.append(f"removed_styles: {len(left)} unused style(s) are still defined in the output: {left[:12]}")
    lost = [sid for sid, _ in rows if sid is not None and sid not in removed and sid not in out_ids]
    if lost:
        errs.append(f"removed_styles: {len(lost)} style(s) that stay are gone from the output: {lost[:12]}")
    problems = _v_dangling(ctx, rows, out_rows)
    errs.extend(f"removed_styles: {p}" for p in problems[:12])
    if len(problems) > 12:
        errs.append(f"removed_styles: … and {len(problems) - 12} more dangling reference(s)")
    return (f"removed_styles: {len(removed)} unused style(s) removed ({which}; {', '.join(t for t in _V_TYPES if t in types)}); "
            f"{len(stays & {sid for sid, _ in rows if sid is not None})} stay; no reference left dangling")


UNUSED_KINDS = {"removed_styles": _removed_styles}
