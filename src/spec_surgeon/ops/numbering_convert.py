"""The destination of a numbering conversion — transform side (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §0.3, §1,
§2; shared by ``convert_numbering`` and ``convert_typed_numbers``). The verifier re-derives all of it with its own code
(verify/numberingconvert.py); nothing there comes from here (rule 1).

Definitions
-----------

* **Parameters** (``scheme_params``): ``levels`` — 1–9 paragraph-style references (id or name), level 0 first;
  ``template`` — the example file holding them (an input pinned by md5, rule 12); ``existing`` (``keep`` | ``replace``,
  only with a template: ``bad-params`` otherwise).
* **The scheme in the template** (a template given): ``lists.resolve_scheme`` over the template's styles and lists —
  refused ``bad-params`` (the example file is the same for every document: fatal), and so is a template list that is
  not a clean destination (``lists.list_is_clean`` with no counted paragraphs: overrides, an unsupported level).
* **The style of each level in the planned output**: with ``existing: "keep"`` the document's own style when the
  reference resolves in the document (``style_import.resolve_style``), else the template's level style (imported); with
  ``"replace"`` always the template's (requested, so the import replaces the document's definition). Without a
  template the document's own styles. **Requested** = the template level styles taken from the template; the import is
  batch 4's (``style_import.plan_import``: their closure and their list, new list ids above every id the document
  defines or refers to).
* **The planned output's styles and lists** (``Destination.sheet`` / ``Destination.defs``): the source's styles with the
  import's copies (``style_import.build_copies``) in place — imported ones added, replaced ones swapped — and the
  source's list definitions with the copied ``w:abstractNum``/``w:num`` read as if they stood in the numbering part
  (``lists.ListDefs.with_extra``).
* **The scheme in the planned output** (``Destination.scheme``): ``lists.resolve_scheme`` over the planned output with
  each level's style id — refused ``scheme`` (it depends on the document: a held style that is not numbered by the same
  list at the right level). The **destination list** is its num; it must pass ``lists.list_is_clean`` against the
  SOURCE count (refused ``numbering-unsupported``: overrides, an abstractNum another num shares with counted paragraphs,
  an unsupported level).
* **A held level must be the example file's** (F6 of the batch-5 review): with a template and ``existing: "keep"``, a
  level whose style this file already has (the file's own definition stays) must number like the template's level —
  the same ``level_pattern_sig`` (its text, legal, the formats of the levels its text names); else ``scheme``, naming the
  styles and pointing at ``existing: "replace"`` (an architect's MasterSpec styles of the example file's ids would
  otherwise leave the document unconverted and pass).
* **No numbering part**: an import the scheme needs into a file without ``word/numbering.xml`` refuses
  ``style-numbering`` with a plain reason (rule 10: a part is never created).
* **The list number's look** (``look_of``, ``number_look``, ``number_symbolic``; convert_typed_numbers' display rule):
  Word formats a list number with the paragraph mark's run properties — the mark's own rPr over ``planned_levels`` (the
  NEW style's chain) — and the level's ``w:lvl/w:rPr`` on top. ``linked_level_styles``: level styles naming their list
  without a ``w:ilvl`` (LibreOffice renders them at level 0: flagged).
* **The number's own formatting** (``level_display_problem``): a scheme level whose ``w:lvl/w:rPr`` turns on
  ``w:vanish``, ``w:caps`` or ``w:smallCaps`` (the element present, ``w:val`` not 0/false/off) shows its number hidden or
  in capitals — not what the model renders: refused, ``bad-params`` in the template, ``scheme`` for a held list.
* **How a number is shown** (convert_typed_numbers, ``planned_mark_toggles``): Word formats a list number with the
  paragraph MARK's run properties — in the planned output the mark's own ``w:pPr/w:rPr`` over docDefaults, the table
  style chain, the NEW paragraph style's chain and the mark's character style chain; ``vanish``, ``caps``, ``smallCaps``
  as toggles (direct wins; exactly one style level → that; none → off; more → None, ambiguous). ``shows_lowercase``: a
  level whose number can hold a lower-case letter (a lower-case literal of its text, or a ``%j`` of a
  ``lowerLetter``/``lowerRoman`` level, unless the level is legal).

API
---
``scheme_params(params) -> (levels, template, existing)``
``destination(pkg, sheet, defs, source_count, levels, template, existing) -> Destination``
    ``Destination``: ``scheme`` (``lists.Scheme`` over the planned output), ``sids`` (the style id of each level),
    ``sheet`` (the planned output's styles: ``.styles``, ``.defaults``, ``.part``), ``defs`` (the planned output's
    ``ListDefs``), ``tpl`` (``TemplateStyles`` or None), ``requested``, ``imp`` (``StyleImport`` or None), ``held`` (the
    destination list is the document's own), ``restart_levels`` (``lists.has_restart_rule`` of the destination: flag).
``row(np) -> list``
    A counted paragraph's number as plans and V10 compare it: ``[index, num_id, ilvl, counters, text, suff, issue]``.
``visible(np_or_none, run_text) -> str``
    The visible line (design §2.4) of a paragraph with that number (None: unnumbered); a numbered paragraph that is not
    evaluated is ``"\\x00<issue> list <num> level <ilvl>\\x00" + run text`` — equal only to itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..package import W, DocxPackage
from . import lists
from .base import JobException
from .effective import FALSE, PropResolver
from .selectors import explicit_font_names
from .style_import import StyleImport, TemplateStyles, build_copies, numbering_part, plan_import, resolve_style

DISPLAY_TOGGLES = ("vanish", "caps", "smallCaps")


@dataclass
class PlannedSheet:
    """The planned output's styles as the list model reads them (``styles``, ``defaults``) — the source's with the
    import's copies in place; ``root`` the source's styles root (its docDefaults: an import never changes them)."""
    styles: Dict[str, Any]
    defaults: Dict[str, str]
    part: Optional[str]
    root: Any = None


def _abstract_el(els, abs_id: Optional[int]):
    for a in els:
        if isinstance(a.tag, str) and a.tag == W + "abstractNum" and lists.whole(a.get(W + "abstractNumId")) == abs_id:
            return a
    return None


def level_display_problem(abstract_el, k: int) -> Optional[str]:
    """Why level ``k``'s number would not show as the model renders it: its ``w:lvl/w:rPr`` turns on vanish, caps or
    smallCaps; else None."""
    if abstract_el is None:
        return None
    for lvl in abstract_el.findall(W + "lvl"):
        if lists.whole(lvl.get(W + "ilvl")) != k:
            continue
        rpr = lvl.find(W + "rPr")
        for e in DISPLAY_TOGGLES:
            el = rpr.find(W + e) if rpr is not None else None
            if el is not None and el.get(W + "val") not in FALSE:
                return f"level {k}'s number is formatted {e} (w:lvl/w:rPr): it would not show as the list renders it"
    return None


def _toggle(levels: Sequence[Any], direct, element: str, top=None) -> Optional[bool]:
    for c in (top, direct):
        d = c.find(W + element) if c is not None else None
        if d is not None:
            return d.get(W + "val") not in FALSE
    found = [c.find(W + element) for c in levels if c is not None and c.find(W + element) is not None]
    if not found:
        return False
    return found[0].get(W + "val") not in FALSE if len(found) == 1 else None


def _color(el) -> Tuple[Any, ...]:
    """A w:color as compared: (val upper-cased, auto ≡ 000000, themeColor, themeShade, themeTint); none → black."""
    if el is None:
        return ("000000", None, None, None)
    val = (el.get(W + "val") or "auto").upper()
    return ("000000" if val == "AUTO" else val, el.get(W + "themeColor"), el.get(W + "themeShade"),
            el.get(W + "themeTint"))


def look_of(levels: Sequence[Any], direct, top=None) -> Dict[str, Any]:
    """How a run (or a list number) looks (F1 of the batch-5 review): ``levels`` the style levels' rPr elements, lowest
    first (docDefaults, the table, paragraph and character style chains, each root-most first), ``direct`` its own rPr,
    ``top`` — for a list number — the level's ``w:lvl/w:rPr`` (above the mark's properties). Toggles (``LOOK_TOGGLES``):
    ``top``, then ``direct`` win; exactly one style level → that; none → off; more → None (ambiguous). The others: the
    nearest of ``top``, ``direct``, the levels highest first that holds the element — ``vertAlign`` (default
    ``baseline``), ``position`` (``0``), ``sz`` (``20``), ``u`` (``none``; present without a value: ``single``),
    ``color`` (``_color``), ``font`` (``w:rFonts`` ascii, else ``theme:`` + asciiTheme; default none)."""
    out: Dict[str, Any] = {e: _toggle(levels, direct, e, top) for e in LOOK_TOGGLES}
    stack = [top, direct] + list(reversed(list(levels)))

    def nearest(element: str):
        return next((c.find(W + element) for c in stack if c is not None and c.find(W + element) is not None), None)
    for e, default in (("vertAlign", "baseline"), ("position", "0"), ("sz", "20")):
        el = nearest(e)
        out[e] = (el.get(W + "val") if el is not None else None) or default
    el = nearest("u")
    out["u"] = "none" if el is None else (el.get(W + "val") or "single")
    out["color"] = _color(nearest("color"))
    out["font"] = None
    for c in stack:
        rf = c.find(W + "rFonts") if c is not None else None
        if rf is not None and (rf.get(W + "ascii") or rf.get(W + "asciiTheme")):
            out["font"] = rf.get(W + "ascii") or "theme:" + rf.get(W + "asciiTheme")
            break
    return out


LOOK_TOGGLES = ("vanish", "specVanish", "caps", "smallCaps", "strike", "dstrike", "b", "i")
LOOK_REFUSED = ("strike", "dstrike", "vertAlign", "position", "color", "sz")   # a difference refuses the file
LOOK_FLAGGED = ("b", "i", "u", "font")                                          # a difference is flagged


def planned_levels(pr: PropResolver, p, style_id: str, rpr) -> List[Any]:
    """The style levels (rPr elements, lowest first) of a run or the paragraph mark (``rpr`` its own rPr) of ``p`` in
    the planned output, with ``style_id`` as the paragraph's style: docDefaults, the table style chain, ``style_id``'s
    chain, the character style chain (``w:rStyle``, else the default character style) — ``pr`` a ``PropResolver`` over
    the planned output's styles."""
    res = pr.res
    levels = [res.dd_rpr]
    ts = res._table_style(p)
    rs = rpr.find(W + "rStyle") if rpr is not None else None
    for sid in ([ts] if ts else []) + [style_id, (rs.get(W + "val") if rs is not None else None)
                                       or res.defaults.get("character")]:
        levels += [st.find(W + "rPr") for st in res.chain(sid)]
    return levels


def planned_mark_toggles(pr: PropResolver, p, style_id: str) -> Dict[str, Optional[bool]]:
    """The paragraph mark's effective vanish/caps/smallCaps in the planned output, with ``style_id`` as its paragraph
    style (``pr`` a ``PropResolver`` over the planned output's styles)."""
    mrpr = p.find(f"{W}pPr/{W}rPr")
    levels = planned_levels(pr, p, style_id, mrpr)
    return {e: _toggle(levels, mrpr, e) for e in DISPLAY_TOGGLES}


def number_look(pr: PropResolver, p, style_id: str, level_rpr) -> Dict[str, Any]:
    """How the list number of ``p`` looks in the planned output: the paragraph mark's properties (its own rPr over
    ``planned_levels``) with the level's ``w:lvl/w:rPr`` on top — Word and LibreOffice format the number so."""
    mrpr = p.find(f"{W}pPr/{W}rPr")
    return look_of(planned_levels(pr, p, style_id, mrpr), mrpr, level_rpr)


def number_symbolic(sheet, p, style_id: str, level_rpr, preserve: Sequence[str]) -> bool:
    """set_font's symbol rule for the list number in the planned output: an explicit font name (any slot) of the level's
    rPr, the mark's own rPr, the mark's character style chain (``w:rStyle``) or ``style_id``'s chain is a preserved
    (symbol) font. ``sheet`` the planned output's styles (``.styles``)."""
    wanted = {f.casefold() for f in preserve}
    mrpr = p.find(f"{W}pPr/{W}rPr")
    names = explicit_font_names(level_rpr) + explicit_font_names(mrpr)
    rs = mrpr.find(W + "rStyle") if mrpr is not None else None
    for sid in (rs.get(W + "val") if rs is not None else None, style_id):
        names += [n for _, st in lists.style_chain(sheet, sid) for n in explicit_font_names(st.find(W + "rPr"))]
    return any(n.casefold() in wanted for n in names)


def level_pattern_sig(level_defs: Dict[int, Any], k: int) -> Tuple[Any, ...]:
    """What level ``k``'s numbers look like (F6): its text, legal, and the format of every level its text names
    (decimal when legal)."""
    lv = level_defs.get(k)
    if lv is None:
        return (None,)
    return (lv.text, lv.legal, tuple((j, "decimal" if lv.legal else (level_defs[j].fmt if j in level_defs else None))
                                     for j in lists.named_levels(lv.text)))


def level_sample(level_defs: Dict[int, Any], k: int) -> str:
    """Level ``k``'s number with every level it names at 1 — "PART 1 -", "1.1", "a." (for messages)."""
    lv = level_defs.get(k)
    if lv is None or lv.text is None:
        return "?"
    text, out, i = lv.text, [], 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            ref = level_defs.get(int(text[i + 1]) - 1)
            out.append(lists.format_value("decimal" if lv.legal or ref is None else ref.fmt, 1) or "?")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def shows_lowercase(scheme: lists.Scheme, k: int) -> bool:
    """Level ``k``'s number can hold a lower-case letter (capitals would change it)."""
    lv = scheme.level_defs[k]
    text, i = lv.text or "", 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            ref = scheme.level_defs.get(int(text[i + 1]) - 1)
            if not lv.legal and ref is not None and ref.fmt in ("lowerLetter", "lowerRoman"):
                return True
            i += 2
        else:
            if text[i].islower():
                return True
            i += 1
    return False


@dataclass
class Destination:
    scheme: lists.Scheme
    sids: List[str]
    sheet: Any
    defs: lists.ListDefs
    tpl: Optional[TemplateStyles] = None
    requested: List[str] = field(default_factory=list)
    imp: Optional[StyleImport] = None
    held: bool = True
    restart_levels: List[int] = field(default_factory=list)
    level_rprs: Dict[int, Any] = field(default_factory=dict)    # level → its w:lvl/w:rPr (None when it has none)
    linked: List[str] = field(default_factory=list)             # level styles (k ≥ 1) whose numPr names no w:ilvl (F3)


def _level_rprs(abstract_el, n: int) -> Dict[int, Any]:
    out: Dict[int, Any] = {k: None for k in range(n)}
    for lvl in (abstract_el.findall(W + "lvl") if abstract_el is not None else []):
        k = lists.whole(lvl.get(W + "ilvl"))
        if k in out and out[k] is None:
            out[k] = lvl.find(W + "rPr")
    return out


def replaced_in_chain(sheet, p, replaced) -> List[str]:
    """F14: the styles of ``p``'s style chain (``lists.paragraph_style``, nearest first) whose definition the import
    replaces (``existing: "replace"``, never tracked) — a tracked conversion of such a paragraph is refused: reject-all
    would leave its old style under the new definition."""
    return [s for s, _ in lists.style_chain(sheet, lists.paragraph_style(p, sheet)) if s in replaced]


def linked_level_styles(sheet, sids: Sequence[str]) -> List[str]:
    """The scheme's level styles (level 1 and deeper) whose numbering comes from a style ``w:numPr`` without ``w:ilvl``
    — the level linked through ``w:lvl/w:pStyle``: Word shows them right, LibreOffice renders them at level 0
    (ooxml-lessons §6; F3, a flag)."""
    out = []
    for k, sid in enumerate(sids):
        if k == 0:
            continue
        for _, st in lists.style_chain(sheet, sid):
            numpr = st.find(f"{W}pPr/{W}numPr")
            if numpr is not None and numpr.find(W + "numId") is not None:
                if numpr.find(W + "ilvl") is None:
                    out.append(sid)
                break
    return out


def scheme_params(params: Dict[str, Any]) -> Tuple[Any, Optional[str], str]:
    levels = params.get("levels")
    if not isinstance(levels, list) or not 1 <= len(levels) <= 9 or \
            not all(isinstance(x, str) and x.strip() for x in levels):
        raise JobException("levels must be a list of 1 to 9 paragraph styles (names or ids), level 0 first",
                           "bad-params")
    if len(set(levels)) != len(levels):
        raise JobException(f"levels names a style twice: {levels}", "bad-params")
    template = params.get("template")
    if template is not None and (not isinstance(template, str) or not template.strip()):
        raise JobException("template must be the path of a .docx/.dotx file", "bad-params")
    existing = params.get("existing", "keep")
    if existing not in ("keep", "replace"):
        raise JobException("existing must be 'keep' or 'replace'", "bad-params")
    if "existing" in params and template is None:
        raise JobException("'existing' applies only with a template", "bad-params")
    return levels, template, existing


def destination(pkg: DocxPackage, sheet, defs: lists.ListDefs, source_count: Sequence[lists.NumberedPar],
                levels: List[str], template: Optional[str], existing: str) -> Destination:
    tpl = None
    requested: List[str] = []
    imp = None
    copies: Dict[str, Any] = {}
    out_defs = defs
    if template is not None:
        tpl = TemplateStyles.load(template)
        tdefs = lists.ListDefs(tpl.numbering, tpl.numbering_part)
        tscheme = lists.resolve_scheme(levels, tpl.sheet, tdefs, "the template", "bad-params")
        why = lists.list_is_clean(tdefs, tscheme.num_id, [])
        t_abs = _abstract_el(list(tpl.numbering) if tpl.numbering is not None else [], tscheme.abstract_id)
        why = why or next((x for x in (level_display_problem(t_abs, k) for k in range(len(levels))) if x), None)
        if why:
            raise JobException(f"the template's list for these levels cannot be a destination: {why}", "bad-params",
                               {"num_id": tscheme.num_id})
        sids = []
        for k, ref in enumerate(levels):
            did = resolve_style(sheet.styles, ref, "this file")
            tid = tscheme.levels[k].style
            if existing == "replace" or did is None:
                sids.append(tid)
                requested.append(tid)
            else:
                sids.append(did)
        if requested:
            if numbering_part(pkg) is None:
                raise JobException("this file has no list definitions part (word/numbering.xml); Word creates one when "
                                   "any list is used — the engine never creates a part (rule 10): number one paragraph "
                                   "in Word, save, and run again", "style-numbering")
            imp = plan_import(pkg, tpl, requested, existing)
            if imp.changes:
                copies, abstracts, nums = build_copies(tpl, imp.imported, imp.replaced, imp.num_map, imp.abs_map)
                out_defs = defs.with_extra(abstracts, nums)
    else:
        sids = list(levels)
    planned = PlannedSheet(styles={**sheet.styles, **copies}, defaults=dict(sheet.defaults), part=sheet.part,
                           root=getattr(sheet, "root", None))
    scheme = lists.resolve_scheme(sids, planned, out_defs, "this file", "scheme")
    sids = [sl.style for sl in scheme.levels]
    held = not (imp is not None and str(scheme.num_id) in imp.num_map.values())
    if held:                                  # an imported list is the template's, checked above
        own = _abstract_el(list(defs.root) if defs.root is not None else [], scheme.abstract_id)
        why = next((x for x in (level_display_problem(own, k) for k in range(len(sids))) if x), None)
        if why:
            raise JobException(f"this file: {why}", "scheme", {"num_id": scheme.num_id})
    else:
        own = _abstract_el(list(tpl.numbering), tscheme.abstract_id)
    if tpl is not None:
        # F6: a level style this file holds (existing keep) must number as the example file's level does
        differ = [(k, sid) for k, sid in enumerate(sids) if sid not in requested
                  and level_pattern_sig(scheme.level_defs, k) != level_pattern_sig(tscheme.level_defs, k)]
        if differ:
            raise JobException("this file already has the scheme's style(s) "
                               + ", ".join(f"{sid} (level {k}: this file numbers it like "
                                           f"{level_sample(scheme.level_defs, k)!r}, the example file like "
                                           f"{level_sample(tscheme.level_defs, k)!r})" for k, sid in differ)
                               + " — with existing 'keep' this file's own definitions stay and the scheme would not be "
                                 "applied; use existing 'replace' to take the example file's", "scheme",
                               {"styles": [s for _, s in differ]})
    why = lists.list_is_clean(out_defs, scheme.num_id, source_count)
    if why:
        raise JobException(f"the destination list (list {scheme.num_id}{', this file’s own' if held else ''}) cannot "
                           f"number the converted paragraphs: {why}", "numbering-unsupported",
                           {"num_id": scheme.num_id})
    return Destination(scheme=scheme, sids=sids, sheet=planned, defs=out_defs, tpl=tpl, requested=requested, imp=imp,
                       held=held, restart_levels=lists.has_restart_rule(out_defs, scheme.num_id),
                       level_rprs=_level_rprs(own, len(sids)), linked=linked_level_styles(planned, sids))


def row(np_: lists.NumberedPar) -> List[Any]:
    return [np_.index, np_.num_id, np_.ilvl, list(np_.counters) if np_.counters is not None else None, np_.text,
            np_.suff, np_.issue]


def visible(np_: Optional[lists.NumberedPar], run_text: str) -> str:
    if np_ is None:
        return lists.visible_line(None, run_text)
    if not np_.evaluated:
        return f"\x00{np_.issue} list {np_.num_id} level {np_.ilvl}\x00" + run_text
    return lists.visible_line(np_.text, run_text, np_.suff)


def shown(text: Optional[str], limit: int = 60) -> str:
    """A paragraph's text for a note or a flag: whitespace collapsed, cut."""
    s = " ".join((text or "").replace("\x00", "¤").split())
    return s if len(s) <= limit else s[:limit - 1] + "…"
