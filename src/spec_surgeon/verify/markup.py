"""What built-in batch 2's kinds share — verifier side, independent of ops/ (rule 1). Definitions:
docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.

* **Item transforms** (§1.3) over the item model of verify/edits.py: every kind derives the expected items of a paragraph
  from the SOURCE paragraph's items with these and compares them with the output's (``check_paragraph``):
  ``drop`` (items whose element or run is in a set), ``unwrap`` (a container gone from every chain below it),
  ``restyle`` (a new run-property signature for the items of some runs), ``tracked_delete`` (whole runs into a NEW
  ``w:del``: ``w:t`` → deleted text, ``w:instrText`` → ``w:delInstrText``), ``insert``.
* **Effective hidden and colour** (§1.6): ``v_hidden`` — the ``w:vanish`` toggle over this module's levels (direct
  wins; exactly one style level → that; none → off; more → None, ambiguous); ``v_color`` — the nearest level's
  ``w:color``; ``v_color_in`` — a 6-digit hex, a theme colour name or ``auto``, case-insensitive.
* **Whole comparisons**: ``tree_difference`` (the residual's significance rules), ``check_relationships`` and
  ``check_content_types`` (a member equal to the source's with exactly the named entries removed/added).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import CT, REL, VPackage, W
from .edits import Item, compare_items, v_items
from .props import FALSY, Styles, _residual_difference, _v_resolve, _v_run_levels, sig

NEW_DEL = W + "del"


# ---- item transforms ---------------------------------------------------------------------------------------------

def drop(items: Sequence[Item], elements: Iterable[Any] = (), runs: Iterable[Any] = ()) -> List[Item]:
    """The items without those standing for one of ``elements`` (a run child or a paragraph-level element) or lying
    in one of ``runs``."""
    els, rs = {id(e) for e in elements}, {id(r) for r in runs}
    return [it for it in items if not ((it.el is not None and id(it.el) in els) or (it.run is not None and id(it.run) in rs))]


def unwrap(items: Sequence[Item], container: Any) -> List[Item]:
    """The items with ``container`` (a w:hyperlink, a w:fldSimple, a w:ins …) gone from every chain below it."""
    out = []
    for it in items:
        if any(e is container for e in it.chain_els):
            k = next(i for i, e in enumerate(it.chain_els) if e is container)
            chain = it.ctx[2][:k] + it.ctx[2][k + 1:]
            els = it.chain_els[:k] + it.chain_els[k + 1:]
            it = Item(it.kind, it.value, (it.ctx[0], it.ctx[1], chain), it.pos, it.tag, it.run, it.el, it.live, els)
        out.append(it)
    return out


def restyle(items: Sequence[Item], signatures: Dict[int, Any]) -> List[Item]:
    """The items of the runs in ``signatures`` (``id(run)`` → the run's expected run-property signature, None for no
    properties — ``rpr_signature``) with that signature; the rest unchanged. E.g. the link style off."""
    out = []
    for it in items:
        if it.run is not None and id(it.run) in signatures:
            it = Item(it.kind, it.value, (signatures[id(it.run)], it.ctx[1], it.ctx[2]), it.pos, it.tag, it.run, it.el,
                      it.live, it.chain_els)
        out.append(it)
    return out


def _deleted_value(it: Item) -> Any:
    if it.tag == W + "instrText" and it.el is not None:
        c = copy.deepcopy(it.el)
        c.tag = W + "delInstrText"
        return sig(c)
    return it.value


def tracked_delete(items: Sequence[Item], runs: Iterable[Any], author: Optional[str]) -> List[Item]:
    """Whole runs deleted as a tracked change: every item of each run gains a NEW ``w:del`` (by ``author``, new id, a
    date — ``edits._ctx_matches``); its characters become deleted text; a ``w:instrText`` becomes ``w:delInstrText``."""
    rs = {id(r) for r in runs}
    out = []
    for it in items:
        if it.run is not None and id(it.run) in rs:
            chain = it.ctx[2] + (("NEW", NEW_DEL, author),)
            if it.kind == "t":
                it = Item("dt", it.value, (it.ctx[0], it.ctx[1], chain), None, it.tag, it.run, it.el, False,
                          it.chain_els + (None,))
            else:
                it = Item(it.kind, _deleted_value(it), (it.ctx[0], it.ctx[1], chain), None, it.tag, it.run, it.el,
                          False, it.chain_els + (None,))
        out.append(it)
    return out


def insert(items: Sequence[Item], index: int, new: Sequence[Item]) -> List[Item]:
    out = list(items)
    out[index:index] = list(new)
    return out


def rpr_signature(rpr) -> Any:
    """The item model's run-property signature (an empty or missing w:rPr is none)."""
    if rpr is None or (not len(rpr) and not rpr.attrib):
        return None
    return sig(rpr)


def check_paragraph(src_p, out_p, expected: Sequence[Item], old_ids: Set[str], where: str, errs: List[str],
                    expected_ppr: Any = "source") -> bool:
    """``out_p`` must be exactly the expected items, with the source's attributes and ``w:pPr`` (or ``expected_ppr``, an
    element or None). True when it is."""
    ok = True
    want = sig(src_p.find(W + "pPr")) if expected_ppr == "source" else sig(expected_ppr)
    if want != sig(out_p.find(W + "pPr")):
        errs.append(f"{where}: paragraph properties changed")
        ok = False
    if dict(src_p.attrib) != dict(out_p.attrib):
        errs.append(f"{where}: paragraph attributes changed")
        ok = False
    d = compare_items(expected, v_items(out_p), old_ids)
    if d:
        errs.append(f"{where}: not exactly the predicted change — {d}")
        ok = False
    return ok


# ---- effective hidden and colour -----------------------------------------------------------------------------------

def v_toggle(levels: Sequence[Tuple[str, Any]], direct, element: str) -> Optional[bool]:
    """A toggle's effective value: direct wins; specified at exactly one level → that; nowhere → off; more → None."""
    d = direct.find(W + element) if direct is not None else None
    if d is not None:
        return d.get(W + "val") not in FALSY
    specs = [L.find(W + element) for _, L in levels if L is not None and L.find(W + element) is not None]
    if len(specs) == 1:
        return specs[0].get(W + "val") not in FALSY
    return False if not specs else None


def v_hidden(rpr, p, styles: Styles) -> Optional[bool]:
    """A run's (or, with the mark's ``w:pPr/w:rPr``, a paragraph mark's) effective ``w:vanish``; None = ambiguous."""
    return v_toggle(_v_run_levels(rpr, p, styles), rpr, "vanish")


def v_mark_hidden(p, styles: Styles) -> Optional[bool]:
    ppr = p.find(W + "pPr")
    return v_hidden(ppr.find(W + "rPr") if ppr is not None else None, p, styles)


def v_color(rpr, p, styles: Styles) -> Tuple[Any, ...]:
    return _v_resolve(_v_run_levels(rpr, p, styles), rpr, "color")[0]


def v_color_names(colors: Iterable[str]) -> Set[str]:
    return {str(c).strip().lstrip("#").casefold() for c in colors if str(c).strip()}


def v_color_in(color: Tuple[Any, ...], wanted: Set[str]) -> bool:
    val, theme = (color[0] or ""), (color[1] or "")
    return (bool(theme) and theme.casefold() in wanted) or (bool(val) and val.casefold() in wanted)


# ---- whole comparisons -------------------------------------------------------------------------------------------

def tree_difference(a, b) -> Optional[str]:
    """None when the two trees are the same (the residual's significance rules), else where they first differ."""
    return _residual_difference(a, b)


def _rel_tuple(el) -> Tuple[Any, ...]:
    return tuple(sorted(el.attrib.items()))


def check_relationships(src: VPackage, out: VPackage, member: str, removed_ids: Iterable[str],
                        added: Sequence[Dict[str, str]], where: str, errs: List[str]) -> None:
    """The rels ``member`` of the output is the source's with exactly the relationships ``removed_ids`` gone and, after
    the source's (in their order), exactly ``added`` (each ``{"Type", "Target"}`` — its ``Id`` new) — nothing else."""
    removed = set(removed_ids)
    s_rels = [r for r in src.xml(member).findall(REL + "Relationship")] if member in src.names else []
    if member not in out.names:
        errs.append(f"{where}: {member} is missing from the output")
        return
    o_rels = out.xml(member).findall(REL + "Relationship")
    missing = removed - {r.get("Id") for r in s_rels}
    if missing:
        errs.append(f"{where}: relationship(s) {sorted(missing)} to remove are not in the source's {member}")
    kept = [_rel_tuple(r) for r in s_rels if r.get("Id") not in removed]
    got = [_rel_tuple(r) for r in o_rels]
    if got[:len(kept)] != kept:
        errs.append(f"{where}: {member}: the relationships that stay are not the source's, in order")
        return
    new = o_rels[len(kept):]
    if len(new) != len(added):
        errs.append(f"{where}: {member} gained {len(new)} relationship(s), expected {len(added)}")
        return
    old_ids = {r.get("Id") for r in s_rels}
    for el, want in zip(new, added):
        attrs = dict(el.attrib)
        rid = attrs.pop("Id", None)
        if rid is None or rid in old_ids or rid in {r.get("Id") for r in o_rels if r is not el}:
            errs.append(f"{where}: {member}: a new relationship's id {rid!r} is not new and unique")
        if attrs != dict(want):
            errs.append(f"{where}: {member}: a new relationship is {attrs}, expected {dict(want)}")


def check_content_types(src: VPackage, out: VPackage, removed_overrides: Iterable[str],
                        added_overrides: Dict[str, str], where: str, errs: List[str]) -> None:
    """``[Content_Types].xml`` of the output is the source's with exactly the Overrides for ``removed_overrides`` gone
    and exactly ``added_overrides`` (member → content type) appended — Defaults untouched."""
    name = "[Content_Types].xml"
    s_root, o_root = src.xml(name), out.xml(name)
    gone = {"/" + m.lstrip("/") for m in removed_overrides}
    expected = [_rel_tuple(e) for e in s_root if isinstance(e.tag, str)
                and not (e.tag == CT + "Override" and e.get("PartName") in gone)]
    expected += [tuple(sorted({"PartName": "/" + m.lstrip("/"), "ContentType": ct}.items()))
                 for m, ct in added_overrides.items()]
    got = [_rel_tuple(e) for e in o_root if isinstance(e.tag, str)]
    tags_ok = [e.tag for e in o_root if isinstance(e.tag, str)] == \
        [e.tag for e in s_root if isinstance(e.tag, str) and not (e.tag == CT + "Override" and e.get("PartName") in gone)] \
        + [CT + "Override"] * len(added_overrides)
    if got != expected or not tags_ok:
        errs.append(f"{where}: [Content_Types].xml is not the source's with exactly the predicted overrides "
                    f"(removed {sorted(gone)}, added {sorted(added_overrides)})")


def elements_outside_paragraphs(root, tags: Set[str]) -> List[Any]:
    """Every element with one of ``tags`` that sits outside any paragraph (a body-level range mark)."""
    out = []
    for el in root.iter(*tags):
        q = el.getparent()
        while q is not None and q.tag != W + "p":
            q = q.getparent()
        if q is None:
            out.append(el)
    return out
