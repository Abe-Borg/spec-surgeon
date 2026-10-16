"""V10 kinds ``removed_hyperlinks`` and ``unlinked_fields`` — built-in batch 2, verifier side
(docs/BUILTIN_OPS_BATCH2_DESIGN.md §9, §10), independent of ops/links.py (rule 1): this module's own reading of what a
link is, which ones the op selects, which runs carry the link style, which addresses leave the file — and the item
model (verify/edits.py, verify/markup.py) to check every changed paragraph is exactly the source paragraph with those
links (or fields) gone and nothing else.

* Link element ``w:hyperlink``: external with an ``r:id``, internal without. Link field: a field (verify/fields.py)
  whose type is HYPERLINK, internal when its instruction holds the switch ``\\l`` outside double quotes (any case).
* Link style: a character style whose name is Hyperlink or FollowedHyperlink (any case).
* Runs of a link element: its ``w:r`` whose paragraph is the link's; of a field: those holding a child (not ``w:rPr``)
  met after its ``separate`` and before its ``end`` walking the paragraph in document order (a simple field: the runs
  in it).
* A removed relationship: a hyperlink-type relationship a selected link element named that nothing left in the part
  names (any attribute in the relationships namespace).
"""
from __future__ import annotations

import copy
import posixpath
from typing import Any, Dict, List, Optional, Sequence, Set

from .reader import R, REL, W
from .edits import part_revision_ids, v_items
from .fields import V_NEVER_UNLINK, VField, v_fields
from .markup import check_paragraph, check_relationships, drop, restyle, rpr_signature, unwrap
from .props import Ctx, Styles

V_DEFAULT_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]
V_HYPERLINK_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"


def _v_para(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def v_switches(instruction: str) -> List[str]:
    """The switches of a field instruction, scanned by hand: a double quote opens a quoted token that runs to the next
    quote (or the end); other tokens run to the next space or quote; a switch is an unquoted token starting with ``\\``."""
    out: List[str] = []
    s, i = instruction or "", 0
    while i < len(s):
        if s[i].isspace():
            i += 1
        elif s[i] == '"':
            j = s.find('"', i + 1)
            i = len(s) if j < 0 else j + 1
        else:
            j = i
            while j < len(s) and not s[j].isspace() and s[j] != '"':
                j += 1
            if s[i] == "\\":
                out.append(s[i:j])
            i = j
    return out


def v_field_internal(f: VField) -> bool:
    return "\\l" in [x.lower() for x in v_switches(f.instruction)]


def v_link_styles(styles: Styles) -> Set[str]:
    out = set()
    for sid, st in styles.by_id.items():
        nm = st.find(W + "name")
        if st.get(W + "type") != "character" or nm is None:
            continue
        if (nm.get(W + "val") or "").casefold() in ("hyperlink", "followedhyperlink"):
            out.add(sid)
    return out


def v_result_runs(f: VField, p) -> List[Any]:
    if f.simple is not None:
        return [r for r in f.simple.iter(W + "r") if _v_para(r) is p]
    if f.separate is None or f.end is None:
        return []
    out: List[Any] = []
    inside = False
    for el in p.iter():
        if el is f.separate:
            inside = True
            continue
        if el is f.end:
            break
        if not inside or not isinstance(el.tag, str) or el.tag == W + "rPr":
            continue
        r = el.getparent()
        if r is not None and r.tag == W + "r" and _v_para(r) is p and not any(x is r for x in out):
            out.append(r)
    return out


def _v_link_runs(h, p) -> List[Any]:
    return [r for r in h.iter(W + "r") if _v_para(r) is p]


def _v_refusal(f: VField, nested_in_code_too: bool = True) -> Optional[str]:
    """The refusal the op owed this field (textbox, then the field's own, then sitting in another field's code)."""
    p = f.paragraph()
    if p is not None and _v_para(p) is not None:
        return "textbox"
    code = f.refusal()
    if code:
        return code
    if nested_in_code_too and f.parent is not None and f.in_parent_code:
        return "nested-field"
    return None


def _v_restyled_sig(runs: Sequence[Any], styles: Set[str]) -> Dict[int, Any]:
    """id(run) → the run-property signature it must have once a link style's w:rStyle is off it."""
    out: Dict[int, Any] = {}
    for r in runs:
        rpr = r.find(W + "rPr")
        rs = rpr.find(W + "rStyle") if rpr is not None else None
        if rs is not None and rs.get(W + "val") in styles:
            c = copy.deepcopy(rpr)
            c.remove(c.find(W + "rStyle"))
            out[id(r)] = rpr_signature(c)
    return out


def _v_rels(ctx: Ctx, part: str) -> Dict[str, Dict[str, Optional[str]]]:
    d, b = posixpath.split(part)
    member = posixpath.join(d, "_rels", b + ".rels")
    if member not in ctx.src.names:
        return {}
    return {r.get("Id"): {"type": r.get("Type"), "mode": r.get("TargetMode")}
            for r in ctx.src.xml(member).findall(REL + "Relationship")}


def _v_rels_member(part: str) -> str:
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")


def _v_check_part_rels(ctx: Ctx, part: str, removed: List[str], kind: str, errs: List[str]) -> None:
    """The part's rels member is the source's minus exactly ``removed`` (checked whenever it changed or should have)."""
    member = _v_rels_member(part)
    if member not in ctx.src.names:
        if removed:
            errs.append(f"{kind}: {part} has no relationships, yet {removed} should go")
        return
    if removed or member not in ctx.out.names or ctx.src.read(member) != ctx.out.read(member):
        check_relationships(ctx.src, ctx.out, member, removed, [], f"{kind} {part}", errs)
        ctx.verified_members.add(member)


def _v_scope(a: Dict[str, Any]) -> List[str]:
    """The scope as the user asked it (absent: this module's default)."""
    s = a.get("scope") or V_DEFAULT_SCOPE
    return [s] if isinstance(s, str) else list(s)


def _removed_hyperlinks(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    which = a.get("which", "external")
    if which not in ("external", "internal", "all"):
        raise ValueError(f"unknown 'which' {which!r}")
    keep = bool(a.get("keep_style", False))
    styles = set() if keep else v_link_styles(ctx.src_styles)
    want = (lambda internal: True) if which == "all" else (lambda internal: internal == (which == "internal"))
    n_links = n_fields = 0
    blocks: Dict[str, List[int]] = {}
    removed_rels: Dict[str, List[str]] = {}
    for part in ctx.scope_parts(_v_scope(a)):
        if part not in ctx.both:
            errs.append(f"removed_hyperlinks: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"removed_hyperlinks: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        index = {id(p): i for i, p in enumerate(sv.pars)}
        hs = [h for h in sv.root.iter(W + "hyperlink") if want(h.get(R + "id") is None)]
        fs = [f for f in v_fields(sv.root) if f.type == "HYPERLINK" and want(v_field_internal(f))]
        by_par: Dict[int, Dict[str, List[Any]]] = {}
        refused = False
        for h in hs:
            p = _v_para(h)
            if p is None or _v_para(p) is not None:
                errs.append(f"removed_hyperlinks: {part}: a selected link outside a paragraph or in a text box — the op "
                            f"must refuse")
                refused = True
                continue
            by_par.setdefault(index[id(p)], {"links": [], "fields": []})["links"].append(h)
        for f in fs:
            why = _v_refusal(f)
            if why:
                errs.append(f"removed_hyperlinks: {part}: the HYPERLINK field {f.instruction.strip()[:60]!r} — "
                            f"{why}: the op must refuse")
                refused = True
                continue
            by_par.setdefault(index[id(f.paragraph())], {"links": [], "fields": []})["fields"].append(f)
        if refused:
            continue
        old_ids = part_revision_ids(sv.root)
        for bi in sorted(by_par):
            p, lh, lf = sv.pars[bi], by_par[bi]["links"], by_par[bi]["fields"]
            items = v_items(p)
            runs = [r for h in lh for r in _v_link_runs(h, p)] + [r for f in lf for r in v_result_runs(f, p)]
            items = restyle(items, _v_restyled_sig(runs, styles))
            items = drop(items, elements=[m for f in lf if f.simple is None for m in f.markers()])
            for container in lh + [f.simple for f in lf if f.simple is not None]:
                items = unwrap(items, container)
            check_paragraph(p, ov.pars[bi], items, old_ids, f"removed_hyperlinks {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
        if by_par:
            blocks[part] = sorted(by_par)
        n_links += len(hs)
        n_fields += len(fs)
        # the addresses that leave the file: hyperlink relationships a removed link named and nothing left names
        gone = {id(h) for h in hs}
        still = {v for el in sv.root.iter() if isinstance(el.tag, str) and id(el) not in gone
                 for k, v in el.attrib.items() if k.startswith(R)}
        rels = _v_rels(ctx, part)
        named = sorted({h.get(R + "id") for h in hs if h.get(R + "id")})
        removed = [rid for rid in named if rid not in still and rid in rels and rels[rid]["type"] == V_HYPERLINK_REL]
        if removed:
            removed_rels[_v_rels_member(part)] = removed
        _v_check_part_rels(ctx, part, removed, "removed_hyperlinks", errs)
    if blocks != {k: list(v) for k, v in (a.get("expected_blocks") or {}).items()}:
        errs.append(f"removed_hyperlinks: changed paragraphs {blocks} here, the plan predicted {a.get('expected_blocks')}")
    if (n_links, n_fields) != (a.get("expected_links"), a.get("expected_fields")):
        errs.append(f"removed_hyperlinks: {n_links} link(s) and {n_fields} field(s) here, the plan predicted "
                    f"{a.get('expected_links')} and {a.get('expected_fields')}")
    if removed_rels != {k: list(v) for k, v in (a.get("removed_relationships") or {}).items()}:
        errs.append(f"removed_hyperlinks: addresses to remove {removed_rels} here, the plan predicted "
                    f"{a.get('removed_relationships')}")
    return (f"removed_hyperlinks ({which}): {n_links} link(s), {n_fields} HYPERLINK field(s); "
            f"{sum(len(v) for v in removed_rels.values())} address(es) removed")


def v_types(raw: Any) -> Any:
    """``"all"``, or the field types upper-cased; ValueError where the op had to refuse."""
    if raw == "all":
        return "all"
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or any(not isinstance(t, str) or not t.strip() for t in raw):
        raise ValueError(f"unusable field types {raw!r}")
    out = {t.strip().upper() for t in raw}
    if out & set(V_NEVER_UNLINK):
        raise ValueError(f"page-number fields {sorted(out & set(V_NEVER_UNLINK))} were asked for — the op must refuse")
    return out


def _unlinked_fields(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    types = v_types(a.get("types"))
    pick = (lambda t: t not in V_NEVER_UNLINK) if types == "all" else (lambda t: t in types)
    found: Dict[str, Dict[str, List[str]]] = {}
    n = 0
    for part in ctx.scope_parts(_v_scope(a)):
        if part not in ctx.both:
            errs.append(f"unlinked_fields: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"unlinked_fields: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        index = {id(p): i for i, p in enumerate(sv.pars)}
        by_par: Dict[int, List[VField]] = {}
        refused = False
        for f in v_fields(sv.root):
            if not pick(f.type):
                continue
            why = _v_refusal(f)
            if why:
                errs.append(f"unlinked_fields: {part}: the field {f.instruction.strip()[:60]!r} — {why}: the op must "
                            f"refuse")
                refused = True
                continue
            by_par.setdefault(index[id(f.paragraph())], []).append(f)
        if refused:
            continue
        old_ids = part_revision_ids(sv.root)
        for bi in sorted(by_par):
            p, fs = sv.pars[bi], by_par[bi]
            items = drop(v_items(p), elements=[m for f in fs if f.simple is None for m in f.markers()])
            for f in fs:
                if f.simple is not None:
                    items = unwrap(items, f.simple)
            check_paragraph(p, ov.pars[bi], items, old_ids, f"unlinked_fields {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
            n += len(fs)
        if by_par:
            found[part] = {str(bi): [f.instruction for f in fs] for bi, fs in sorted(by_par.items())}
    if found != (a.get("expected") or {}):
        errs.append(f"unlinked_fields: the fields to turn into text here {found}, the plan predicted {a.get('expected')}")
    return f"unlinked_fields: {n} field(s) turned into text ({'all but page numbers' if types == 'all' else sorted(types)})"


LINK_KINDS = {"removed_hyperlinks": _removed_hyperlinks, "unlinked_fields": _unlinked_fields}
