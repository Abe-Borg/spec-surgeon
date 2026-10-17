"""V10 kind ``cleared_formatting`` — clear_direct_formatting, verified (built-in batch 4, item 28 —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §3).

This module's own reading of the op's definitions (transform side: ops/clear_formatting.py; CLAUDE.md lists them among
the definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* the cleared, opt-in and symbol-kept name sets (``V_RUN_CLEARED``, ``V_RUN_OPT_IN``, ``V_PARA_CLEARED``,
  ``V_PARA_OPT_IN``, ``V_SYMBOL_KEPT``) and the default symbol fonts (``V_DEFAULT_PRESERVE``) are this module's own;
* ``keep``/``also`` are read from the user's own parameters (``v_cleared_sets``: (default ∪ also) − keep per target
  type; unknown names or a name in both → ``ValueError``), with this module's defaults for everything absent (run and
  paragraph true, marks included, revisions included, w:sym runs are symbol runs; tracked: as asked, else when
  ``paragraphs`` narrows the selection);
* the selection (``props.para_matches``, ``props.target_matches`` with the run's effective colour), the symbol rule
  (``props.symbolic`` — a preserved font in the direct rFonts, the character style chain or the paragraph style chain
  — or a ``w:sym`` when ``skip_sym_runs``; marks without the ``w:sym`` rule) and each target's cleared set
  (``v_decide``) are re-derived from the SOURCE.

Fails when the changed targets or their counts differ from the plan's; when an output ``w:rPr`` is not exactly the
source's minus the cleared children, in order (absent when emptied and plain; the ``w:rPrChange`` by the shared
run-property rules); where the op changed the pPr (it declares ``pPr`` and every cleared name), when the output pPr's
other children are not exactly the source's minus the cleared ones (the mark's ``w:rPr`` in its place only where the op
also changed the mark — it declares ``markRPr``; another op may give the mark an rPr or empty it), the ``w:pPrChange`` by
the shared paragraph-property rules; and when a paragraph whose pPr or mark it changed is left with an empty ``w:pPr`` in
the OUTPUT (an emptied one goes — judged on the output, so the result is the same whichever op of the spec applied
first). The checked runs (``strip_runs``), marks (``strip_marks``) and the cleared pPr children with ``pPrChange``
(``strip_ppr``) leave the residual, which compares everything else.

**Reuse** (``apply_style``'s ``direct_formatting: "clear"`` — the DEFAULT rules, its own ``w:pStyle`` in the same pPr):

* ``v_cleared_sets(keep=None, also=None) -> (run names, paragraph names)``; ``v_cleared_sets()`` is the default rules.
* ``v_decide(p, runs, styles, run_names, para_names, *, runs_pred="all", exclude_rev=False, marks=True, preserve=None,
  skip_sym=True) -> VClearing`` — one SOURCE paragraph's targets (``runs`` = its runs, ``PartView.runs[bi]``):
  ``runs`` {run index: names}, ``mark`` names or None, ``ppr`` names; ``.changed``.
* ``check_runs_and_mark(ctx, part, bi, oi, d, tracked, author, errs) -> (runs checked, marks checked, mark_stays)`` —
  every cleared run and the mark of source block ``bi`` against output block ``oi``: exactly the source rPr minus the
  cleared children, the change-marker rules (tracked: a w:rPrChange whose snapshot is the source rPr; plain: the
  marker unchanged, an emptied rPr absent); registers ``ctx.strip_runs`` / ``ctx.strip_marks``. ``mark_stays`` — the
  mark's w:rPr is still in the output pPr (what ``v_expected_ppr`` needs).
* ``v_expected_ppr(S, gone, mark_stays, mark=True) -> list`` — the signature sequence the output pPr must show: the
  source pPr's children without the cleared ones and without ``w:pPrChange`` (the change marker, checked apart), the
  mark's w:rPr as the placeholder ``MARK`` (its content is ``check_runs_and_mark``'s) when it stays. ``mark=False``
  leaves the mark out altogether — pass it when the caller's op does not change the mark (``d.mark is None``): the mark
  is then another op's or the residual's. ``v_with_pstyle(seq, style_id)`` puts a ``w:pStyle`` first in such a sequence
  (replacing one), for another kind's expectation.
* ``check_cleared_ppr(S, O, expected, tracked, author, where, errs, changed=True, mark=True)`` — the output pPr ``O``
  against an expected sequence built with the same ``mark`` (``v_ppr_sequence(O, mark)`` compared; an absent ``O`` is the
  empty sequence), the w:pPrChange rules when ``changed`` (tracked: present, its snapshot the source pPr without
  rPr/sectPr/pPrChange; plain or unchanged: as in the source — a source marker may not vanish with the pPr), and an
  empty output ``O`` is an error (``check_empty_ppr``). Call it only where the op changed the pPr (the other children
  are compared). The caller registers ``ctx.strip_ppr``.
* ``check_empty_ppr(O, where, errs)`` — the output rule alone, for a paragraph where the op changed only the mark.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from lxml import etree

from .reader import W
from .props import (REVISION_WRAPPERS, Ctx, _check_ppr_change, _check_rpr_change, _has_ancestor, _local,
                    _v_mark_in_revision, _v_run_prop, para_matches, sig, symbolic, target_matches)

V_RUN_CLEARED = frozenset({"rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline",
                           "shadow", "emboss", "imprint", "color", "spacing", "w", "kern", "sz", "szCs", "u", "effect",
                           "bdr", "shd", "fitText", "em"})
V_RUN_OPT_IN = frozenset({"highlight", "vertAlign", "position"})
V_PARA_CLEARED = frozenset({"keepNext", "keepLines", "pageBreakBefore", "widowControl", "suppressLineNumbers", "pBdr",
                            "shd", "tabs", "suppressAutoHyphens", "spacing", "ind", "contextualSpacing", "mirrorIndents",
                            "jc", "textDirection", "textAlignment", "textboxTightWrap"})
V_PARA_OPT_IN = frozenset({"outlineLvl", "framePr", "suppressOverlap"})
V_SYMBOL_KEPT = frozenset({"rFonts", "caps", "smallCaps"})
V_DEFAULT_PRESERVE = frozenset(f.casefold() for f in (
    "Symbol", "Wingdings", "Wingdings 2", "Wingdings 3", "Webdings", "Marlett", "MT Extra", "ZapfDingbats",
    "Zapf Dingbats", "Bookshelf Symbol 7", "MS Reference Specialty", "MS Outlook"))
MARK = ("#mark",)                    # the paragraph mark's w:rPr in a pPr sequence (its content is checked as a run)


def _v_names(v: Any, what: str) -> Set[str]:
    if v is None:
        return set()
    if not isinstance(v, (list, tuple, set, frozenset)) or not all(isinstance(x, str) for x in v):
        raise ValueError(f"{what} must be a list of property names")
    unknown = set(v) - (V_RUN_CLEARED | V_RUN_OPT_IN | V_PARA_CLEARED | V_PARA_OPT_IN)
    if unknown:
        raise ValueError(f"{what}: unknown property name(s) {sorted(unknown)} (the op must refuse)")
    return set(v)


def v_cleared_sets(keep: Optional[Iterable[str]] = None, also: Optional[Iterable[str]] = None) -> Tuple[Set[str], Set[str]]:
    """(run names, paragraph names) = (default ∪ also) − keep, per target type."""
    k = _v_names(keep, "keep")
    a = _v_names(also, "also")
    if k & a:
        raise ValueError(f"{sorted(k & a)} named in both keep and also (the op must refuse)")
    run = (set(V_RUN_CLEARED) | (a & (V_RUN_CLEARED | V_RUN_OPT_IN))) - k
    para = (set(V_PARA_CLEARED) | (a & (V_PARA_CLEARED | V_PARA_OPT_IN))) - k
    return run, para


def _v_gone(container, names: Set[str]) -> Set[str]:
    """The names of ``container``'s w: children that are in ``names``."""
    if container is None:
        return set()
    return {_local(c) for c in container if _local(c) and _local(c) in names}


@dataclass
class VClearing:
    runs: Dict[int, Set[str]] = field(default_factory=dict)
    mark: Optional[Set[str]] = None
    ppr: Set[str] = field(default_factory=set)

    @property
    def changed(self) -> bool:
        return bool(self.runs or self.mark or self.ppr)


def v_decide(p, runs: List[Any], styles, run_names: Set[str], para_names: Set[str], *, runs_pred: Any = "all",
             exclude_rev: bool = False, marks: bool = True, preserve: Optional[Set[str]] = None,
             skip_sym: bool = True) -> VClearing:
    """One SOURCE paragraph's targets. ``preserve`` casefolded (default: ``V_DEFAULT_PRESERVE``)."""
    preserve = V_DEFAULT_PRESERVE if preserve is None else preserve
    d = VClearing()
    if run_names:
        for ri, r in enumerate(runs):
            rpr = r.find(W + "rPr")
            in_rev = _has_ancestor(r, REVISION_WRAPPERS, p)
            if (exclude_rev and in_rev) or not target_matches(
                    runs_pred, rpr, in_rev, preserve, lambda rpr=rpr: _v_run_prop(rpr, p, styles, "color")[0]):
                continue
            sym = symbolic(rpr, p, styles, preserve) or (skip_sym and r.find(W + "sym") is not None)
            gone = _v_gone(rpr, run_names - V_SYMBOL_KEPT if sym else run_names)
            if gone:
                d.runs[ri] = gone
        if marks:
            mrpr = p.find(W + "pPr/" + W + "rPr")
            m_rev = _v_mark_in_revision(mrpr)
            if not (exclude_rev and m_rev) and target_matches(
                    runs_pred, mrpr, m_rev, preserve, lambda: _v_run_prop(mrpr, p, styles, "color")[0]):
                msym = symbolic(mrpr, p, styles, preserve)
                gone = _v_gone(mrpr, run_names - V_SYMBOL_KEPT if msym else run_names)
                if gone:
                    d.mark = gone
    if para_names:
        d.ppr = _v_gone(p.find(W + "pPr"), para_names)
    return d


def _v_left(S, gone: Set[str]) -> List[Any]:
    """The children of a source rPr that stay (everything not cleared, comments and foreign elements included)."""
    return [c for c in (S if S is not None else []) if not (_local(c) and _local(c) in gone)]


def check_cleared_rpr(S, O, gone: Set[str], tracked: bool, author, where: str, errs: List[str]) -> None:
    """One run's or mark's output rPr ``O``: the source rPr ``S`` minus exactly the children named in ``gone``, in order;
    plain and nothing left → ``O`` absent; the w:rPrChange by the shared rules."""
    left = _v_left(S, gone)
    if not tracked and not left and not (S is not None and S.attrib):
        if O is not None:
            errs.append(f"{where}: an emptied w:rPr is left in place (plain: it must be removed)")
        return
    if O is None:
        errs.append(f"{where}: no w:rPr in the output (expected {[_local(c) or c.tag for c in left]})")
        return
    exp = [sig(c) for c in left if _local(c) != "rPrChange"]
    got = [sig(c) for c in O if _local(c) != "rPrChange"]
    if exp != got:
        names_got = [_local(c) or str(c.tag) for c in O if _local(c) != "rPrChange"]
        names_exp = [_local(c) or str(c.tag) for c in left if _local(c) != "rPrChange"]
        still = sorted(set(names_got) & gone)
        lost = sorted(set(names_exp) - set(names_got))
        errs.append(f"{where}: run properties are {names_got}, expected {names_exp}"
                    + (f" — cleared but still there: {still}" if still else "")
                    + (f" — kept but missing: {lost}" if lost else "")
                    + ("" if still or lost else " — a kept property changed or moved"))
    if dict(O.attrib) != (dict(S.attrib) if S is not None else {}):
        errs.append(f"{where}: w:rPr attributes changed")
    _check_rpr_change(S, O, tracked, author, where, errs)


def check_runs_and_mark(ctx: Ctx, part: str, bi: int, oi: int, d: VClearing, tracked: bool, author,
                        errs: List[str]) -> Tuple[int, int, bool]:
    """Every cleared run and the mark of source block ``bi`` against output block ``oi`` (run structure unchanged — the
    caller checked); registers what was checked for the residual. Returns (runs, marks, the mark's rPr stays)."""
    sv, ov = ctx.view(part)
    for ri, gone in sorted(d.runs.items()):
        ctx.strip_runs.setdefault(part, set()).add((bi, ri))
        check_cleared_rpr(sv.runs[bi][ri].find(W + "rPr"), ov.runs[oi][ri].find(W + "rPr"), gone, tracked, author,
                          f"{part} block {bi} run {ri}", errs)
    s_m = sv.pars[bi].find(W + "pPr/" + W + "rPr")
    stays = s_m is not None
    if d.mark is not None:
        ctx.strip_marks.setdefault(part, set()).add(bi)
        check_cleared_rpr(s_m, ov.pars[oi].find(W + "pPr/" + W + "rPr"), d.mark, tracked, author,
                          f"{part} block {bi} paragraph mark", errs)
        stays = tracked or bool(_v_left(s_m, d.mark)) or bool(s_m is not None and s_m.attrib)
    return len(d.runs), int(d.mark is not None), stays


def v_expected_ppr(S, gone: Set[str], mark_stays: bool, mark: bool = True) -> List[Any]:
    """The sequence the output pPr must show (``v_ppr_sequence``): the source pPr's children minus the cleared ones and
    ``w:pPrChange``, the mark's rPr as ``MARK`` when it stays. ``mark=False`` leaves the mark's rPr out altogether — the
    caller's op does not change the mark, so its place and content are another op's or the residual's business (a
    ``set_font`` in the same spec may give the mark an rPr it never had)."""
    out: List[Any] = []
    for c in (S if S is not None else []):
        loc = _local(c)
        if loc == "pPrChange":
            continue
        if loc == "rPr":
            if mark and mark_stays:
                out.append(MARK)
            continue
        if loc and loc in gone:
            continue
        out.append(sig(c))
    return out


def v_ppr_sequence(O, mark: bool = True) -> List[Any]:
    """An output pPr as a sequence: each child's signature, the mark's rPr as ``MARK`` (left out with ``mark=False``),
    ``w:pPrChange`` left out; an absent pPr is the empty sequence."""
    return [MARK if _local(c) == "rPr" else sig(c) for c in (O if O is not None else [])
            if _local(c) != "pPrChange" and (mark or _local(c) != "rPr")]


def v_with_pstyle(seq: List[Any], style_id: str) -> List[Any]:
    """``seq`` (an expected pPr sequence) with ``w:pStyle w:val=style_id`` first, replacing any pStyle in it."""
    ps = etree.Element(W + "pStyle")
    ps.set(W + "val", style_id)
    return [sig(ps)] + [x for x in seq if not (isinstance(x, tuple) and x and x[0] == W + "pStyle")]


def check_empty_ppr(O, where: str, errs: List[str]) -> None:
    """The "an emptied pPr goes" rule, judged on the OUTPUT: a ``w:pPr`` with no children and no attributes left in a
    paragraph this op changed is an error (plain: an emptied one is removed; tracked: a changed one holds its marker) —
    whichever op of the spec applied first."""
    if O is not None and not len(O) and not O.attrib:
        errs.append(f"{where}: an emptied w:pPr is left in place (it must be removed)")


def check_cleared_ppr(S, O, expected: List[Any], tracked: bool, author, where: str, errs: List[str],
                      changed: bool = True, mark: bool = True) -> None:
    """The output pPr ``O`` of a paragraph whose pPr this op changed, against ``expected`` (``v_expected_ppr``, possibly
    with another kind's additions — built with the same ``mark``). ``changed``: the pPr itself gets a change (a
    w:pPrChange when tracked). ``mark=False``: the mark's rPr is in neither sequence (the op leaves the mark to others).
    An absent output pPr is an empty one (nothing may be expected of it); an empty one left in place fails
    (``check_empty_ppr``)."""
    def names(seq):
        return ["rPr" if x == MARK else (etree.QName(x[0]).localname if isinstance(x[0], str) and x[0].startswith("{")
                                         else str(x[0])) for x in seq]
    check_empty_ppr(O, where, errs)
    got = v_ppr_sequence(O, mark)
    if got != expected:
        if O is None:
            errs.append(f"{where}: no w:pPr in the output (expected {names(expected)})")
        else:
            errs.append(f"{where}: paragraph properties are {names(got)}, expected {names(expected)}"
                        + ("" if names(got) != names(expected) else " — a kept property changed"))
    present = O if O is not None else etree.Element(W + "pPr")      # absent: no attributes, no marker
    if dict(present.attrib) != (dict(S.attrib) if S is not None else {}):
        errs.append(f"{where}: w:pPr attributes changed")
    _check_ppr_change(S, present, tracked and changed, author, where, errs)


def _v_settings(params: Dict[str, Any]) -> Dict[str, Any]:
    """The user's own parameters with this module's defaults."""
    for k in ("run", "paragraph", "include_paragraph_marks", "skip_sym_runs"):
        if k in params and not isinstance(params[k], bool):
            raise ValueError(f"{k} must be true or false")
    run, para = params.get("run", True), params.get("paragraph", True)
    if not run and not para:
        raise ValueError("nothing to clear (the op must refuse)")
    run_names, para_names = v_cleared_sets(params.get("keep"), params.get("also"))
    in_revs = params.get("in_revisions", "include")
    if in_revs not in ("include", "exclude"):
        raise ValueError(f"in_revisions {in_revs!r}")
    preserve = params.get("preserve_fonts")
    return {"run_names": run_names if run else set(), "para_names": para_names if para else set(),
            "exclude_rev": in_revs == "exclude", "marks": bool(params.get("include_paragraph_marks", True)),
            "skip_sym": bool(params.get("skip_sym_runs", True)),
            "preserve": V_DEFAULT_PRESERVE if preserve is None else {str(f).casefold() for f in preserve}}


def _cleared_formatting(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    sel = a.get("select") or {}
    if set(sel) - {"scope", "paragraphs", "runs"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'scope', 'paragraphs', 'runs'})}")
    scope = sel.get("scope", ["document"])
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"cleared_formatting: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    params = a.get("params") or {}
    st = _v_settings(params)
    runs_pred, para_pred = sel.get("runs", "all"), sel.get("paragraphs")
    tracked = bool(params["tracked"]) if "tracked" in params else para_pred not in (None, "all", {})
    author = a.get("author")
    changed: Dict[str, Dict[str, List[Any]]] = {}
    n_runs = n_marks = n_paras = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"cleared_formatting: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        for bi, p in enumerate(sv.pars):
            if not para_matches(para_pred, p, bi, sv, ctx.src_styles):
                continue
            d = v_decide(p, sv.runs[bi], ctx.src_styles, st["run_names"], st["para_names"], runs_pred=runs_pred,
                         exclude_rev=st["exclude_rev"], marks=st["marks"], preserve=st["preserve"],
                         skip_sym=st["skip_sym"])
            if not d.changed:
                continue
            c = changed.setdefault(part, {"runs": [], "marks": [], "paras": []})
            c["runs"] += [[bi, ri] for ri in sorted(d.runs)]
            if d.mark is not None:
                c["marks"].append(bi)
            if d.ppr:
                c["paras"].append(bi)
            where = f"{part} block {bi}"
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"cleared_formatting: {where} is selected but also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"cleared_formatting: {where}: run structure changed")
                continue
            nr, nm, mark_stays = check_runs_and_mark(ctx, part, bi, oi, d, tracked, author, errs)
            n_runs, n_marks = n_runs + nr, n_marks + nm
            S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
            mark = d.mark is not None
            if d.ppr:
                # the pPr's other children compared only where the op changed the pPr (it declares pPr and every
                # cleared name); the mark's rPr only where it changed the mark (markRPr) — the rest is the residual's
                check_cleared_ppr(S, O, v_expected_ppr(S, d.ppr, mark_stays, mark=mark), tracked, author, where, errs,
                                  changed=True, mark=mark)
            elif mark:
                check_empty_ppr(O, where, errs)
            if d.ppr:
                n_paras += 1
                ctx.strip_ppr.setdefault(part, {}).setdefault(bi, set()).update(d.ppr | {"pPrChange"})
    for n, key, what in ((n_runs, "expected_runs", "run(s)"), (n_marks, "expected_marks", "paragraph mark(s)"),
                         (n_paras, "expected_paragraphs", "paragraph(s)")):
        if n != a.get(key, -1):
            errs.append(f"cleared_formatting: {n} {what} cleared here, plan predicted {a.get(key)}")
    planned = {k: {g: sorted(map(tuple, v[g])) if g == "runs" else sorted(v[g]) for g in ("runs", "marks", "paras")}
               for k, v in (a.get("expected") or {}).items()}
    mine = {k: {g: sorted(map(tuple, v[g])) if g == "runs" else sorted(v[g]) for g in ("runs", "marks", "paras")}
            for k, v in changed.items()}
    if mine != planned:
        errs.append(f"cleared_formatting: the targets cleared here {mine} are not the plan's {planned}")
    return (f"cleared_formatting: {n_runs} run(s), {n_marks} mark(s), {n_paras} paragraph(s)"
            + (" (tracked)" if tracked else ""))


CLEARING_KINDS = {"cleared_formatting": _cleared_formatting}
