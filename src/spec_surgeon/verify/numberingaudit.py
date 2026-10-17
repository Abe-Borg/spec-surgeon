"""V10 kind ``unnumbered_empty`` — audit_numbering's fix, verified (built-in batch 5, item 36 —
docs/BUILTIN_OPS_BATCH5_DESIGN.md §4).

This module's own reading of the fix's definitions (transform side: ops/audit_numbering.py). Nothing here comes from ops/
(rule 1): the count is verify/lists.py's, the paragraph-property order is read from the bundled schema itself
(``xsd.sequence_order``: CT_PPrBase, then rPr, sectPr, pPrChange), and from the user's own parameters (``tracked`` false
by default) this module re-derives

* the **empty numbered paragraphs** (``_v_empties``): counted paragraphs of the main part the model evaluates, showing a
  non-empty number that is not a ``bullet``, whose content is blank (``_v_blank_content``: only ``w:pPr``, ``w:proofErr``,
  ``w:bookmarkStart``/``w:bookmarkEnd`` and runs of ``w:rPr``, ``w:lastRenderedPageBreak`` and ``w:t`` holding nothing but
  spaces and no-break spaces);
* every refusal the op must have raised (a ValueError here): such a paragraph's own ``w:numPr`` holding anything but
  ``w:ilvl`` / ``w:numId``.

It fails when the paragraphs differ from the plan's (``changed``); when a changed paragraph's output ``w:numPr`` is not
exactly ``<w:numPr><w:numId w:val="0"/></w:numPr>``, not where the source's stood (or, when it had none, not where the
schema's order puts it among the source's children), or when the ``w:pPrChange`` breaks the paragraph-property rules
(present exactly when tracked, its snapshot the source pPr without rPr, sectPr, pPrChange); and, post-condition, when the
verifier's own prediction (the source counted with those paragraphs unnumbered) differs from the plan's ``predicted``
rows or its count of the OUTPUT differs from that prediction.

Exactly what the fix changes is checked here — the numPr and the pPrChange, which alone leave the residual
(``strip_ppr``). Every other child of the changed paragraphs' pPr — the mark's ``w:rPr``, a ``w:sectPr``, spacing … — is
left to the residual, which compares it with the source after the OTHER ops' kinds have stripped what THEY checked (a
set_font over the marks, a set_page_setup on a section break): not weaker — a change nobody checked still fails there —,
and an op beside the fix that changes the mark or the section break plans and passes instead of failing here.
"""
from __future__ import annotations

from typing import Any, Dict, List

from lxml import etree

from .reader import W
from .props import Ctx, _check_ppr_change, _local, sig
from .lists import v_count, v_list_defs
from .xsd import sequence_order

V_BLANK = {" ", " "}
V_INERT = {"pPr", "proofErr", "bookmarkStart", "bookmarkEnd"}


def _v_ppr_rank() -> Dict[str, int]:
    names = [etree.QName(t).localname for t in sequence_order("ISO-IEC29500-4_2016/wml.xsd", "CT_PPrBase")]
    names += ["rPr", "sectPr", "pPrChange"]
    return {n: k for k, n in enumerate(names)}


def _v_blank_content(p) -> bool:
    for c in p:
        if not isinstance(c.tag, str):
            continue
        if c.tag.startswith(W) and _local(c) in V_INERT:
            continue
        if c.tag != W + "r":
            return False
        for k in c:
            if not isinstance(k.tag, str) or k.tag in (W + "rPr", W + "lastRenderedPageBreak"):
                continue
            if k.tag == W + "t" and all(ch in V_BLANK for ch in (k.text or "")):
                continue
            return False
    return True


def _v_empties(rows, pars) -> List[int]:
    return [r.index for r in rows if r.not_evaluated is None and r.text not in (None, "") and r.fmt != "bullet"
            and _v_blank_content(pars[r.index])]


def _v_rows(rows) -> List[List[Any]]:
    return [[r.index, r.num_id, r.ilvl, list(r.counters) if r.counters is not None else None, r.text, r.issue]
            for r in rows]


def _v_expected_numpr():
    el = etree.Element(W + "numPr")
    etree.SubElement(el, W + "numId").set(W + "val", "0")
    return el


def _v_numpr_place(S, O, where: str, errs: List[str]) -> None:
    """The output numPr stands where the source's stood, or — none in the source — before the first source child the
    schema orders after it (an unknown child ranks after sectPr, before pPrChange)."""
    rank = _v_ppr_rank()
    o_kids = [c for c in O if isinstance(c.tag, str)]
    o_at = next(i for i, c in enumerate(o_kids) if c.tag == W + "numPr")
    s_kids = [c for c in (S if S is not None else []) if isinstance(c.tag, str)]
    s_at = next((i for i, c in enumerate(s_kids) if c.tag == W + "numPr"), None)
    if s_at is None:
        own = rank["numPr"]
        unknown = rank["sectPr"] + 0.5
        s_at = next((i for i, c in enumerate(s_kids)
                     if (rank.get(_local(c), unknown) if c.tag.startswith(W) else unknown) > own), len(s_kids))
    if o_at != s_at:
        errs.append(f"{where}: w:numPr is child {o_at} of the output pPr, expected child {s_at}")


def _unnumbered_empty(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tag = "unnumbered_empty"
    params = a.get("params") or {}
    if params.get("unnumber_empty") is not True:
        raise ValueError("the fix was not asked (unnumber_empty) — no assertion should exist")
    tracked = params.get("tracked", False) is True
    author = a.get("author")
    main = ctx.src.main_part()
    if a.get("part") != main:
        errs.append(f"{tag}: the main part is {main} here, the plan says {a.get('part')}")
        return f"{tag}: part mismatch"
    sv, ov = ctx.view(main)
    defs = v_list_defs(ctx.src)
    rows = v_count(sv.root, ctx.src_styles, defs)
    empties = _v_empties(rows, sv.pars)
    if empties != list(a.get("changed") or []):
        errs.append(f"{tag}: the empty numbered paragraphs here are {empties}, the plan's are {a.get('changed')}")
    expected_numpr = sig(_v_expected_numpr())
    for bi in empties:
        p = sv.pars[bi]
        where = f"{tag}: {main} block {bi}"
        S = p.find(W + "pPr")
        s_numpr = S.find(W + "numPr") if S is not None else None
        if s_numpr is not None and any(isinstance(c.tag, str) and c.tag not in (W + "ilvl", W + "numId")
                                       for c in s_numpr):
            raise ValueError(f"block {bi}'s numbering holds a revision or another element (the op must refuse)")
        oi = ctx.maps[main].get(bi)
        if oi is None:
            errs.append(f"{where}: no counterpart in the output")
            continue
        O = ov.pars[oi].find(W + "pPr")
        if O is None or O.find(W + "numPr") is None:
            errs.append(f"{where}: no w:numPr in the output (numbering off needs w:numId 0)")
            continue
        if len(O.findall(W + "numPr")) != 1 or sig(O.find(W + "numPr")) != expected_numpr:
            errs.append(f"{where}: the output w:numPr is not exactly <w:numPr><w:numId w:val=\"0\"/></w:numPr>")
        _v_numpr_place(S, O, where, errs)
        _check_ppr_change(S, O, tracked, author, where, errs)
        ctx.strip_ppr.setdefault(main, {}).setdefault(bi, set()).update({"numPr", "pPrChange"})

    # ---- the numbers: the verifier's own prediction, the plan's, the output's ----
    own = _v_rows(v_count(sv.root, ctx.src_styles, defs, {bi: None for bi in empties}))
    planned = a.get("predicted") or []
    if own != planned:
        diff = next(((x, y) for x, y in zip(own, planned) if x != y), None)
        errs.append(f"{tag}: the plan's predicted numbers are not the verifier's — first difference (verifier, plan) "
                    f"{diff}" + (f" (counts {len(own)} / {len(planned)})" if diff is None else ""))
    got = _v_rows(v_count(ov.root, ctx.out_styles, v_list_defs(ctx.out)))
    if got != own:
        diff = next(((x, y) for x, y in zip(own, got) if x != y), None)
        errs.append(f"{tag}: the output's numbers are not the predicted ones — first difference (predicted, output) "
                    f"{diff}" + (f" (counts {len(own)} / {len(got)})" if diff is None else ""))
    was = {r.index: r.text for r in rows}
    moved = sum(1 for x in own if x[0] in was and was[x[0]] != x[4])
    return (f"{tag}: {len(empties)} empty numbered paragraph(s) unnumbered "
            f"({'tracked' if tracked else 'plain'}); {moved} number(s) move, every counted paragraph recounted")


AUDIT_KINDS = {"unnumbered_empty": _unnumbered_empty}
