"""Op: clear_direct_formatting — remove the direct formatting of selected text and paragraphs so they follow their styles
again (built-in batch 4, item 28 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §3).

Formatting mode: zero text diffs; the op only removes property elements, it never writes one (apart from the change
markers of tracked mode).

Parameters: ``run`` (default true) — clear the run formatting of the selected runs and of their paragraph marks
(``include_paragraph_marks``, default true); ``paragraph`` (default true) — clear the selected paragraphs' paragraph
formatting; ``keep`` — names never cleared by this op; ``also`` — opt-in names cleared too; ``in_revisions``,
``skip_sym_runs``, ``preserve_fonts``, ``tracked``, ``author`` — set_properties' meanings. ``select``: ``scope``,
``paragraphs``, ``runs`` (``runs`` narrows the runs and marks only).

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's own
reading is verify/clearing.py):

* **Cleared run properties** (``RUN_CLEARED``, by default) — fonts, bold/italic and their complex-script twins, caps,
  small caps, strike, double strike, outline, shadow, emboss, imprint, colour, character spacing, scale, kerning, size
  and complex-script size, underline, text effect, border, shading, fit text, emphasis mark. **Opt-in**
  (``RUN_OPT_IN``, only through ``also``): ``highlight`` (a review cue and the templating slot marker), ``vertAlign``,
  ``position`` (superscripts carry meaning: m², footnote numbers). **Never**: everything else — ``rStyle``, the
  revision marks, ``rPrChange``, ``vanish``/``specVanish``/``webHidden`` (hidden text stays hidden), ``noProof``,
  ``lang``, ``rtl``, ``cs``, ``eastAsianLayout``, ``snapToGrid``, ``oMath`` and any element outside the ``w``
  namespace.
* **Cleared paragraph properties** (``PARA_CLEARED``) — keep with next, keep lines together, page break before, widow
  control, suppress line numbers, borders, shading, tab stops, suppress hyphenation, spacing, indentation, contextual
  spacing, mirror indents, alignment, text direction, vertical text alignment, text-box tight wrap. **Opt-in**
  (``PARA_OPT_IN``): ``outlineLvl`` (TOC levels), ``framePr``, ``suppressOverlap`` (frames). **Never**: ``pStyle``,
  ``numPr`` (list numbers), ``sectPr``, ``pPrChange``, ``rPr`` (the mark — cleared as a run), ``cnfStyle``, ``divId``,
  ``bidi``, the East Asian typography flags and any element outside the ``w`` namespace.
* ``keep`` and ``also`` take names from the cleared and opt-in lists only (``bad-params`` otherwise; one name in both is
  ``bad-params``). The set for a target = (default ∪ also) − keep, per target type; a name both lists hold (``shd``,
  ``spacing``) applies to runs and to paragraphs alike.
* **Symbol runs** (set_font's definition — ``selectors.is_symbol_rpr``: a preserved font in the run's direct rFonts, its
  character style chain or its paragraph's style chain; or a ``w:sym`` when ``skip_sym_runs``) never lose ``rFonts``,
  ``caps`` or ``smallCaps``: their glyphs depend on them. Symbol paragraph marks the same (without the ``w:sym`` rule).
* A target's result = its source element with the cleared children removed, in order. Plain: an emptied run ``w:rPr``
  is removed; an emptied mark ``w:rPr`` is removed and then an emptied ``w:pPr`` (a pPr this op changed). Tracked: a
  ``w:rPrChange`` (snapshot = the source rPr without rPrChange) per changed run or mark, a ``w:pPrChange`` (snapshot =
  the source pPr without rPr, sectPr, pPrChange) per changed paragraph. An existing change marker refuses tracked
  (``tracked-format``) and is flagged when plain; a tracked change on a paragraph mark that is itself a pending revision
  refuses (``tracked-format``). A target with nothing to clear is untouched and not counted.

Tracked or plain (§14.1): plain for whole-document selections, tracked when ``paragraphs`` narrows the selection;
``tracked`` overrides. Verified by V10's ``cleared_formatting`` kind (verify/clearing.py), which re-derives all of the
above on its own.

**Reuse** (``apply_style``'s ``direct_formatting: "clear"``, item 30, clears by the DEFAULT rules and sets ``w:pStyle``
in the same pPr — one ``w:pPrChange`` per paragraph when tracked):

* ``RUN_CLEARED``, ``RUN_OPT_IN``, ``PARA_CLEARED``, ``PARA_OPT_IN``, ``SYMBOL_KEPT`` — the name sets;
  ``cleared_names(keep, also) -> (run names, paragraph names)`` — validated sets (``bad-params``).
* ``ClearSettings`` — everything that decides a paragraph's targets besides the paragraph itself; ``ClearSettings()``
  is the default rules (every cleared name, marks included, revisions included, the default symbol fonts, w:sym runs
  are symbol runs).
* ``paragraph_clearing(p, runs, sheet, settings) -> ParagraphClearing`` — for ONE paragraph (``runs`` = its runs,
  ``selectors.runs_by_paragraph``), the runs (index, names), the mark (names or None) and the pPr children (names) that
  are cleared, symbol rule included; ``.changed`` is True when anything is. Reads the SOURCE only (plan side).
* ``check_markers(p, runs, d, tracked, where, ppr_changes=None) -> (runs/marks, paragraphs)`` — the change-marker
  refusals (tracked) and flag counts (plain); pass ``ppr_changes=True`` when the caller changes the pPr anyway (its
  w:pStyle), so an existing w:pPrChange refuses or is flagged even when nothing in the pPr is cleared.
* ``clear_paragraph(p, runs, d, stamp=None, next_id=0, ppr_edit=None) -> next_id`` — applies one paragraph's decision
  (on the working copy); ``stamp`` (a ``RevisionStamp``) makes it tracked, consuming ids from ``next_id``. ``ppr_edit``
  (a callable taking the pPr, created first in the paragraph when absent) runs after the cleared children are removed
  and before the one ``w:pPrChange`` is appended, so another op's own change (``w:pStyle``) shares the snapshot; the
  pPr then counts as changed. ``changes_needed(d, ppr_changes=False)`` is the number of revision ids it consumes
  tracked.
* ``clear_rpr(rpr, names, stamp=None, next_id=0) -> (next_id, emptied)`` — one rPr (a run's or a mark's).
* ``emptying_reads(p, d, tracked) -> [names]`` — the pPr children a plain mark-emptying clear depends on (see below).

Touches (rule 9): ``(part, block, "rPr")`` per block with a cleared run, ``(part, block, "markRPr")`` per cleared mark,
``(part, block, "pPr")`` and ``(part, block, <name>)`` for every pPr child cleared, ``(part, "scope", "verify")`` per part
in scope; and, for a paragraph whose mark a plain clear empties while its pPr is otherwise untouched, the soft reads
``(part, block, "verify-props:<name>")`` of ``emptying_reads`` — the pPr then goes when nothing else is left, which
depends on those children should another op remove them.
"""
from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from ..package import W, DocxPackage
from ..ooxml import (RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id, paragraph_ppr, paragraphs,
                     property_change)
from . import register
from .base import VERIFY_PROPS, JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import (DEFAULT_PRESERVE_FONTS, StyleSheet, check_paragraph_selector, check_run_selector, in_revision,
                        is_symbol_rpr, is_targeted, mark_in_revision, paragraph_selected, rpr_selected,
                        runs_by_paragraph, scope_parts)

RUN_CLEARED: Tuple[str, ...] = (
    "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike", "outline", "shadow", "emboss",
    "imprint", "color", "spacing", "w", "kern", "sz", "szCs", "u", "effect", "bdr", "shd", "fitText", "em")
RUN_OPT_IN: Tuple[str, ...] = ("highlight", "vertAlign", "position")
PARA_CLEARED: Tuple[str, ...] = (
    "keepNext", "keepLines", "pageBreakBefore", "widowControl", "suppressLineNumbers", "pBdr", "shd", "tabs",
    "suppressAutoHyphens", "spacing", "ind", "contextualSpacing", "mirrorIndents", "jc", "textDirection",
    "textAlignment", "textboxTightWrap")
PARA_OPT_IN: Tuple[str, ...] = ("outlineLvl", "framePr", "suppressOverlap")
SYMBOL_KEPT: FrozenSet[str] = frozenset({"rFonts", "caps", "smallCaps"})      # a symbol run's glyphs depend on them
KNOWN_NAMES: FrozenSet[str] = frozenset(RUN_CLEARED + RUN_OPT_IN + PARA_CLEARED + PARA_OPT_IN)
# paragraph properties a formatting op may take away (this op, set_tab_stops' tabs, set_borders_shading's pBdr/shd —
# each declares the element by name); pStyle, numPr, sectPr … are never removed, so a pPr holding one never empties
REMOVABLE_PPR: FrozenSet[str] = frozenset(PARA_CLEARED + PARA_OPT_IN)
REFLOW_REASON = ("clearing direct formatting changes fonts, sizes, spacing and indents back to the styles' values; text "
                 "reflows, so the source render is no layout reference")
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}


def _names_list(v: Any, what: str) -> List[str]:
    if v is None:
        return []
    if not isinstance(v, (list, tuple, set, frozenset)) or not all(isinstance(x, str) for x in v):
        raise JobException(f"'{what}' must be a list of property names", "bad-params")
    unknown = sorted(set(v) - KNOWN_NAMES)
    if unknown:
        raise JobException(f"'{what}': unknown property name(s) {unknown}; known: run {list(RUN_CLEARED)} (+ opt-in "
                           f"{list(RUN_OPT_IN)}), paragraph {list(PARA_CLEARED)} (+ opt-in {list(PARA_OPT_IN)})",
                           "bad-params", {"names": unknown})
    return list(v)


def cleared_names(keep: Optional[Iterable[str]] = None, also: Optional[Iterable[str]] = None
                  ) -> Tuple[FrozenSet[str], FrozenSet[str]]:
    """(run names, paragraph names) cleared = (default ∪ also) − keep, per target type; ``keep``/``also`` validated:
    names from the cleared and opt-in lists only, none in both (``bad-params``)."""
    keep_l = _names_list(keep, "keep")
    also_l = _names_list(also, "also")
    both = sorted(set(keep_l) & set(also_l))
    if both:
        raise JobException(f"{both} named in both 'keep' and 'also'", "bad-params", {"names": both})
    run_all, para_all = set(RUN_CLEARED) | set(RUN_OPT_IN), set(PARA_CLEARED) | set(PARA_OPT_IN)
    run = (set(RUN_CLEARED) | (set(also_l) & run_all)) - set(keep_l)
    para = (set(PARA_CLEARED) | (set(also_l) & para_all)) - set(keep_l)
    return frozenset(run), frozenset(para)


@dataclass(frozen=True)
class ClearSettings:
    """What decides a paragraph's targets besides the paragraph itself. ``ClearSettings()`` = §3's default rules."""
    run_names: FrozenSet[str] = frozenset(RUN_CLEARED)      # empty: runs and marks are not cleared (run: false)
    para_names: FrozenSet[str] = frozenset(PARA_CLEARED)    # empty: the pPr is not cleared (paragraph: false)
    runs: Any = "all"                                      # the run selector (narrows the runs and marks)
    in_revisions: str = "include"                          # include | exclude
    marks: bool = True                                     # include_paragraph_marks
    preserve: Tuple[str, ...] = tuple(DEFAULT_PRESERVE_FONTS)
    skip_sym: bool = True                                  # a run holding a w:sym is a symbol run


@dataclass
class ParagraphClearing:
    """One paragraph's targets: ``runs`` — (index among the paragraph's runs, names cleared) of every run with something
    to clear; ``mark`` — names cleared from the paragraph mark's rPr, None when the mark is untouched; ``ppr`` — names
    cleared from the pPr; ``already`` — selected runs/marks/paragraphs with nothing to clear."""
    runs: List[Tuple[int, List[str]]] = field(default_factory=list)
    mark: Optional[List[str]] = None
    ppr: List[str] = field(default_factory=list)
    already: Counter = field(default_factory=Counter)
    # apply_style's selective policy can retain part of a compound property.
    # The names in runs/mark/ppr still enumerate every changed property family.
    run_replacements: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    mark_replacements: Dict[str, Any] = field(default_factory=dict)
    ppr_replacements: Dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return bool(self.runs or self.mark or self.ppr)


def gone_names(container, names: Iterable[str]) -> List[str]:
    """The names (sorted, once each) of ``container``'s w: children that are in ``names``."""
    if container is None:
        return []
    wanted = set(names)
    return sorted({c.tag[len(W):] for c in container
                   if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in wanted})


def paragraph_clearing(p, runs: Sequence, sheet: StyleSheet, settings: ClearSettings = ClearSettings()
                       ) -> ParagraphClearing:
    """The targets of ONE paragraph (selected by the caller) under ``settings``, read from the SOURCE. ``runs`` are the
    paragraph's runs (``runs_by_paragraph``). Symbol runs and marks never lose ``SYMBOL_KEPT``."""
    d = ParagraphClearing()
    preserve = list(settings.preserve)
    if settings.run_names:
        for ri, r in enumerate(runs):
            rpr = r.find(W + "rPr")
            revised = in_revision(r, p)
            if (settings.in_revisions == "exclude" and revised) or not rpr_selected(settings.runs, rpr, revised, p, sheet):
                continue
            sym = is_symbol_rpr(rpr, p, sheet, preserve) or (settings.skip_sym and r.find(W + "sym") is not None)
            names = gone_names(rpr, settings.run_names - SYMBOL_KEPT if sym else settings.run_names)
            if names:
                d.runs.append((ri, names))
            else:
                d.already["runs"] += 1
        if settings.marks:
            mrpr = p.find(W + "pPr/" + W + "rPr")
            revised = mark_in_revision(p)
            if not (settings.in_revisions == "exclude" and revised) and rpr_selected(settings.runs, mrpr, revised, p,
                                                                                       sheet):
                msym = is_symbol_rpr(mrpr, p, sheet, preserve)
                names = gone_names(mrpr, settings.run_names - SYMBOL_KEPT if msym else settings.run_names)
                if names:
                    d.mark = names
                else:
                    d.already["marks"] += 1
    if settings.para_names:
        names = gone_names(p.find(W + "pPr"), settings.para_names)
        if names:
            d.ppr = names
        else:
            d.already["paragraphs"] += 1
    return d


def check_markers(p, runs: Sequence, d: ParagraphClearing, tracked: bool, where: str,
                  ppr_changes: Optional[bool] = None) -> Tuple[int, int]:
    """Existing change markers on the targets of ``d``: tracked → ``tracked-format`` (two snapshots cannot be
    expressed; a paragraph mark that is itself a pending revision would copy that revision into the snapshot); plain →
    counted, returned as (runs and marks, paragraphs) for the plan's flag."""
    older_r = older_p = 0
    for ri, _ in d.runs:
        rpr = runs[ri].find(W + "rPr")
        if rpr is not None and rpr.find(W + "rPrChange") is not None:
            if tracked:
                raise JobException(f"{where}: a run to clear already carries a pending tracked formatting change "
                                   f"(w:rPrChange); a second tracked change cannot be expressed", "tracked-format",
                                   {"where": where})
            older_r += 1
    if d.mark:
        mrpr = p.find(W + "pPr/" + W + "rPr")
        if tracked and mark_in_revision(p):
            raise JobException(f"{where}: the paragraph mark is itself a pending revision; a tracked formatting change "
                               f"on it would copy that revision into the snapshot", "tracked-format", {"where": where})
        if mrpr is not None and mrpr.find(W + "rPrChange") is not None:
            if tracked:
                raise JobException(f"{where}: the paragraph mark already carries a pending tracked formatting change "
                                   f"(w:rPrChange)", "tracked-format", {"where": where})
            older_r += 1
    if (bool(d.ppr) if ppr_changes is None else ppr_changes):
        ppr = p.find(W + "pPr")
        if ppr is not None and ppr.find(W + "pPrChange") is not None:
            if tracked:
                raise JobException(f"{where} already carries a pending tracked paragraph change (w:pPrChange)",
                                   "tracked-format", {"where": where})
            older_p += 1
    return older_r, older_p


def emptying_reads(p, d: ParagraphClearing, tracked: bool) -> List[str]:
    """What a plain clear that empties the paragraph mark (and changes nothing else in the pPr) depends on: it removes
    the pPr when nothing else is left (``clear_paragraph``), so whether the pPr goes depends on its other children should
    another op remove them — and an op that removes one leaves an emptied pPr by its own rule, which differs by order.
    Returns the names of those other children, to declare as soft reads (``verify-props:<name>``): an op removing or
    changing one of them (it declares the element by name) is then a plan-time conflict. Only when every other child is
    a property some op may remove (``REMOVABLE_PPR``) — a pStyle, numPr or sectPr keeps the pPr in either order — and
    none when the pPr holds only the mark (an op adding a child gives the same file in either order)."""
    if tracked or d.mark is None or d.ppr:
        return []
    ppr = p.find(W + "pPr")
    mrpr = ppr.find(W + "rPr") if ppr is not None else None
    if mrpr is None or mrpr.attrib:
        return []
    wanted = set(d.mark)
    if any(not (isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in wanted) for c in mrpr):
        return []                                           # the mark keeps something: the pPr stays
    others = [c for c in ppr if c is not mrpr]
    if ppr.attrib or not others:
        return []
    names = set()
    for c in others:
        if not (isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in REMOVABLE_PPR):
            return []
        names.add(c.tag[len(W):])
    return sorted(names)


def changes_needed(d: ParagraphClearing, ppr_changes: bool = False) -> int:
    """How many change markers (revision ids) ``clear_paragraph`` writes tracked: one per run, the mark, the pPr."""
    return len(d.runs) + (1 if d.mark else 0) + (1 if (d.ppr or ppr_changes) else 0)


def _empty(el) -> bool:
    return el is not None and not len(el) and not el.attrib


def clear_rpr(rpr, names: Iterable[str], stamp: Optional[RevisionStamp] = None, next_id: int = 0,
              replacements: Optional[Dict[str, Any]] = None) -> Tuple[int, bool]:
    """Remove ``rpr``'s w: children named in ``names`` (in place). Tracked (``stamp``): a w:rPrChange — snapshot = the
    rPr before, without rPrChange — appended as the last child. Returns (the next revision id, emptied: True when the
    rPr has nothing left — the caller removes it, plain)."""
    wanted = set(names)
    chg = None
    if stamp is not None:
        chg = property_change("rPrChange", "rPr", [c for c in rpr if c.tag != W + "rPrChange"], stamp, next_id)
        next_id += 1
    for c in list(rpr):
        if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in wanted:
            replacement = (replacements or {}).get(c.tag[len(W):])
            if replacement is None:
                rpr.remove(c)
            else:
                rpr.replace(c, copy.deepcopy(replacement))
    if chg is not None:
        rpr.append(chg)
    return next_id, _empty(rpr)


def clear_paragraph(p, runs: Sequence, d: ParagraphClearing, stamp: Optional[RevisionStamp] = None, next_id: int = 0,
                    ppr_edit: Optional[Callable[[Any], None]] = None) -> int:
    """Apply one paragraph's decision ``d`` (``paragraph_clearing`` on the source) to the working copy ``p`` / ``runs``.
    Plain: emptied run rPr removed; an emptied mark rPr removed, then an emptied pPr (one this call changed). Tracked:
    one w:rPrChange per run and mark, one w:pPrChange for the pPr. ``ppr_edit(ppr)`` — another op's own change in the
    same pPr (apply_style's w:pStyle) — runs after the cleared children are removed and before the w:pPrChange is
    appended. Returns the next revision id."""
    for ri, names in d.runs:
        r = runs[ri]
        rpr = r.find(W + "rPr")
        next_id, emptied = clear_rpr(rpr, names, stamp, next_id, d.run_replacements.get(ri))
        if emptied and stamp is None:
            r.remove(rpr)
    ppr = p.find(W + "pPr")
    ppr_changes = bool(d.ppr) or ppr_edit is not None
    chg = None
    if ppr_changes:
        if ppr is None:
            ppr = paragraph_ppr(p)
        if stamp is not None:
            snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
            chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
            next_id += 1
        gone = set(d.ppr)
        for c in list(ppr):
            if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in gone:
                replacement = d.ppr_replacements.get(c.tag[len(W):])
                if replacement is None:
                    ppr.remove(c)
                else:
                    ppr.replace(c, copy.deepcopy(replacement))
        if ppr_edit is not None:
            ppr_edit(ppr)
    if d.mark:
        mrpr = ppr.find(W + "rPr")
        next_id, emptied = clear_rpr(mrpr, d.mark, stamp, next_id, d.mark_replacements)
        if emptied and stamp is None:
            ppr.remove(mrpr)
    if chg is not None:
        ppr.append(chg)
    if stamp is None and (ppr_changes or d.mark) and _empty(ppr):
        p.remove(ppr)
    return next_id


def clean_params(params: Dict[str, Any]) -> ClearSettings:
    """The op's parameters → the settings (run selector filled in by the caller); validated (``bad-params``)."""
    for k in ("run", "paragraph", "include_paragraph_marks", "skip_sym_runs"):
        if k in params and not isinstance(params[k], bool):
            raise JobException(f"'{k}' must be true or false", "bad-params")
    run, para = params.get("run", True), params.get("paragraph", True)
    if not run and not para:
        raise JobException("nothing to clear: 'run' and 'paragraph' are both false", "bad-params")
    run_names, para_names = cleared_names(params.get("keep"), params.get("also"))
    in_revs = params.get("in_revisions", "include")
    if in_revs not in ("include", "exclude"):
        raise JobException("in_revisions must be 'include' or 'exclude'", "bad-params")
    preserve = params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS)
    if not isinstance(preserve, list) or not all(isinstance(f, str) for f in preserve):
        raise JobException("preserve_fonts must be a list of font names", "bad-params")
    return ClearSettings(run_names=run_names if run else frozenset(), para_names=para_names if para else frozenset(),
                         in_revisions=in_revs, marks=bool(params.get("include_paragraph_marks", True)),
                         preserve=tuple(preserve), skip_sym=bool(params.get("skip_sym_runs", True)))


USER_PARAMS = ("run", "paragraph", "keep", "also", "include_paragraph_marks", "in_revisions", "skip_sym_runs",
               "preserve_fonts", "tracked")


@register
class ClearDirectFormatting(Op):
    name = "clear_direct_formatting"
    mode = "formatting"
    description = ("Clear the direct formatting of selected text and paragraphs so they follow their styles again: "
                   "fonts, sizes, bold/italic/underline, colour, caps and the like on runs and paragraph marks; "
                   "spacing, indents, alignment, borders, shading, tab stops and keep/page-break flags on paragraphs. "
                   "Hidden text stays hidden; list numbers, styles, language, highlight, superscripts and outline levels "
                   "stay unless asked ('also'); symbol fonts are never touched. Adds no characters; a target with "
                   "nothing to clear is left alone.")
    params_schema = {"type": "object", "properties": {
        "run": {"type": "boolean", "default": True,
                "description": "clear the run formatting of the selected runs and their paragraph marks"},
        "paragraph": {"type": "boolean", "default": True,
                      "description": "clear the selected paragraphs' paragraph formatting"},
        "keep": {"type": "array", "items": {"type": "string"},
                 "description": "property names never cleared (element names: b and bCs for bold, i and iCs for "
                                "italic, sz and szCs for size, rFonts, color, u, spacing, ind, jc, pBdr, shd, tabs, …)"},
        "also": {"type": "array", "items": {"type": "string"},
                 "description": "opt-in names cleared too: highlight, vertAlign, position (runs); outlineLvl, framePr, "
                                "suppressOverlap (paragraphs)"},
        "include_paragraph_marks": {"type": "boolean", "default": True},
        "in_revisions": {"type": "string", "enum": ["include", "exclude"], "default": "include",
                         "description": "runs inside pending w:ins/w:del: clear them too, or leave them"},
        "skip_sym_runs": {"type": "boolean", "default": True,
                          "description": "a run holding a w:sym is a symbol run (never loses its font or caps)"},
        "preserve_fonts": {"type": "array", "items": {"type": "string"},
                           "description": "symbol/dingbat fonts whose runs never lose rFonts/caps/smallCaps (default: "
                                          "Symbol, Wingdings…)"},
        "tracked": {"type": "boolean", "description": "w:rPrChange / w:pPrChange per change; default: plain for "
                                                      "whole-document selections, tracked when paragraphs are targeted"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "runs": {"description": "'all' or {has_direct_font, font_in, not_in_revisions, color_in} — narrows the runs and "
                                "paragraph marks only"},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:rPrChange per run/mark cleared, +1 w:pPrChange per paragraph",
        "V10": "cleared_formatting: selection, symbol rule and each target's cleared set re-derived from the source; "
               "counts and targets matched; each output rPr/pPr = the source's minus exactly the cleared children "
               "(absent when emptied, plain); residual identical",
        "V7": "none when anything is cleared (the text reflows)"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        unknown = set(raw_select) - {"scope", "paragraphs", "runs"}
        if unknown:
            raise JobException(f"clear_direct_formatting: unknown selector key(s) {sorted(unknown)}", "bad-params")
        settings = clean_params(params)
        runs_pred, para_pred = raw_select.get("runs", "all"), raw_select.get("paragraphs")
        check_run_selector(runs_pred)
        check_paragraph_selector(para_pred)
        settings = replace(settings, runs=runs_pred)
        scope = raw_select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        tracked = bool(params.get("tracked", is_targeted(raw_select)))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, List[list]] = {}
        expected: Dict[str, Dict[str, list]] = {}
        n_runs = n_marks = n_paras = 0
        already: Counter = Counter()
        older_r = older_p = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            chosen: List[list] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(para_pred, p, bi, block_full_text(p), sheet):
                    continue
                d = paragraph_clearing(p, groups[bi], sheet, settings)
                already.update(d.already)
                if not d.changed:
                    continue
                o_r, o_p = check_markers(p, groups[bi], d, tracked, f"{part} paragraph {bi}")
                older_r, older_p = older_r + o_r, older_p + o_p
                chosen.append([bi, [list(x) for x in d.runs], d.mark, d.ppr])
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
                exp = expected[part] = {"runs": [], "marks": [], "paras": []}
                for bi, runs_, mark, ppr in chosen:
                    exp["runs"] += [[bi, ri] for ri, _ in runs_]
                    if runs_:
                        frag.touches.append((part, str(bi), "rPr"))
                    if mark:
                        exp["marks"].append(bi)
                        frag.touches.append((part, str(bi), "markRPr"))
                        frag.touches += [(part, str(bi), f"{VERIFY_PROPS}:{name}")
                                         for name in emptying_reads(pars[bi], ParagraphClearing(mark=mark, ppr=ppr),
                                                                    tracked)]
                    if ppr:
                        exp["paras"].append(bi)
                        # the pPr as a whole, and each cleared child by name: an op reading one of them in the
                        # output (set_tab_stops' verify:tabs) is a plan-time conflict, never a failure after apply
                        frag.touches.append((part, str(bi), "pPr"))
                        frag.touches += [(part, str(bi), name) for name in ppr]
                n_runs += len(exp["runs"])
                n_marks += len(exp["marks"])
                n_paras += len(exp["paras"])
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
        frag.property_assertions.append({
            "kind": "cleared_formatting", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in USER_PARAMS if k in params}, "author": author,
            "expected": expected, "expected_runs": n_runs, "expected_marks": n_marks, "expected_paragraphs": n_paras})
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author}
        n = n_runs + n_marks + n_paras
        if tracked and n:
            if n_runs + n_marks:
                frag.rev_delta["rPrChange"] = n_runs + n_marks
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if n:
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.notes.append(f"direct formatting cleared from {n_runs} run(s), {n_marks} paragraph mark(s) and {n_paras} "
                          f"paragraph(s) across {len(targets)} part(s); "
                          f"{'tracked (w:rPrChange / w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append("nothing to clear, left alone: " + ", ".join(f"{v} {k}" for k, v in sorted(already.items())))
        kept_opt = [x for x in RUN_OPT_IN if x not in settings.run_names] if settings.run_names else []
        kept_opt += [x for x in PARA_OPT_IN if x not in settings.para_names] if settings.para_names else []
        frag.notes.append("never cleared: character and paragraph styles, list numbers (numPr), hidden text (vanish), "
                          "language, section breaks" + (f"; kept unless asked ('also'): {kept_opt}" if kept_opt else "")
                          + (f"; kept as asked: {sorted(params['keep'])}" if params.get("keep") else ""))
        if settings.run_names:
            frag.notes.append(f"symbol runs (fonts {list(settings.preserve)}"
                              + (", w:sym runs" if settings.skip_sym else "") + ") keep rFonts, caps and smallCaps: "
                              "their glyphs depend on them")
        if older_r or older_p:
            frag.flags.append(f"{older_r} cleared run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change (w:rPrChange / w:pPrChange) from an earlier reviewer; "
                              f"reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, chosen in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, runs_, mark, ppr in chosen:
                d = ParagraphClearing(runs=[(ri, names) for ri, names in runs_], mark=mark, ppr=ppr)
                next_id = clear_paragraph(pars[bi], groups[bi], d, stamp, next_id)
            pkg.write_xml(part, root)
