"""The shared list (numbering) model — verifier side (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §0, §2.1).

This module's OWN reading of the list definitions the numbering ops predict with (transform side: ops/lists.py — its
docstring states every rule; CLAUDE.md lists them among the definitions that must stay identical on both sides). Nothing
here comes from ops/ (rule 1): the numbering part is read through ``reader.VPackage``, the styles through
``props.Styles``, run text through ``edits.v_paragraph_text``, and every rule below is written again:

* whole numbers (``-`` then ASCII digits), ilvls 0–8; abstractNums and nums keyed by whole ids (others define nothing);
  unreadable ids (twice defined; a level's or override's ilvl not 0–8 or repeated; a start / lvlRestart (≥ 0) /
  startOverride value present and not whole; a num's abstractNumId not whole); an abstractNum carrying
  ``w:numStyleLink`` or ``w:styleLink`` unsupported (a numbering style's list: Word may join the counts); a FULL override
  level (``w:lvlOverride/w:lvl``) unsupported — only its ``w:pStyle`` is read (the linked level);
* a level: start 0, decimal, no text, no restart, not legal, suffix tab, by default; unsupported for
  ``mc:AlternateContent``, a format outside the eight, a suffix outside tab/space/nothing;
* a paragraph's numbering: its own numPr (numId, ilvl), else the first style on its chain (pStyle — empty names none —,
  else the default paragraph style; basedOn, cycle-safe) whose own numPr holds a numId, that style's ilvl, else the lowest
  level naming that style (``linked``), else 0; a direct numId without ilvl → 0, unless the first ``w:numPr/w:ilvl``
  along the style chain is there and not 0 — then the level is AMBIGUOUS (Word may take the style's), counted
  ``ambiguous-level`` and blocking the rest of the list like an unreadable one; a list named by a style and no own
  ilvl: the level read as a unit (above: the numId's style's ilvl, else its link, else 0) and the level read element by
  element (``_v_by_element``: the first own ilvl along the chain, nearest first — or the link at the numId's style
  itself when it has no ilvl —, else 0) must agree, else AMBIGUOUS the same way (a nearer style holding only an ilvl;
  an ilvl beyond a numId style with neither an ilvl nor a link); a level from the link under both readings is marked
  ``linked`` (LibreOffice ignores links); 0 or none → unnumbered; undefined → dangling;
* formats: a negative value is shown by no format (decimal included);
* counting over ``root.iter(w:p)``, text boxes and pending-deletion marks left out, per numId: restarts (none → always,
  0 → never, r ≥ 1 → when L ≤ r − 1, ignored when r − 1 ≥ k — Word's reading; LibreOffice ignores ``w:lvlRestart``
  altogether, so its renders differ from Word wherever a level carries one: ``v_has_restart_rule`` lists those levels),
  start = startOverride → level start → 0 (a level with a startOverride that starts a SECOND time in its num holds an
  uncertain value until reset: Word may use w:start on later restarts), the counters, the rendered number
  (``%1``–``%9``, decimal when the level is legal), and the not-evaluated codes in ops/lists.py's order: ``unreadable``
  / ``ambiguous-level``, ``earlier``, ``unreadable`` / ``unsupported`` (the list), ``shared``, ``override``,
  ``start-override`` (a level 0…L or a named level holds an uncertain value; counters None), ``unsupported`` (the
  level, a level its text names, a value) and
  ``skipped`` (a ``%j`` naming a level with no current value — never reached, or restarted: Word, LibreOffice and a model
  showing the start disagree there; the counters are kept; or a paragraph AT, or naming, a level that a deeper paragraph
  was counted without — ``_v_skipped_below`` —, until a restart resets it: LibreOffice started that level then, Word
  starts it when it next comes; the counters are None);
* the scheme (raises ValueError: the op must refuse ``scheme`` / ``bad-params``), the level patterns, typed numbers,
  the visible line, the main abstractNum and the clean destination list.

API (verifier names are ``v_``-prefixed): ``VListDefs(root)`` / ``v_list_defs(pkg)`` (``.present``, ``.defined``,
``.problem``, ``.abstract_of``, ``.nums_of_abstract``, ``.level``, ``.levels_of``, ``.start_override``,
``.has_overrides``, ``.is_override``, ``.with_extra``), ``v_styles(pkg)``, ``v_paragraph_style``,
``v_paragraph_numbering(p, styles, defs)``, ``v_style_numbering(sid, styles, defs)``, ``v_counted(p)``,
``v_count(root, styles, defs, override=None)`` → [``VNumbered``], ``v_format_value(fmt, n, text=None)``,
``v_resolve_scheme(levels, styles, defs)`` → ``VScheme``, ``v_level_pattern(scheme, k)``,
``v_typed_number(run_text, scheme)`` → ``VTyped`` or None, ``v_main_abstract(pars)``,
``v_visible_line(number, run_text, suff=None)``, ``v_list_is_clean(defs, num_id, pars)``,
``v_has_restart_rule(defs, num_id)``; ``V_SHARED`` / ``V_SKIPPED`` / ``V_START_OVERRIDE`` start the ``shared`` /
``skipped`` / ``start-override`` reasons; ``VNumbering.ambiguous``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .reader import STYLES_REL, W
from .props import Styles
from .restyle import v_resolve_style

V_NUMBERING_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"
V_ALTERNATE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
V_OFF = {"0", "false", "off"}
V_FORMATS = {"decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman", "bullet", "none"}
V_SUFFIX = {"tab": "\t", "space": " ", "nothing": ""}
V_SHARED = "shared list definition"
V_SKIPPED = "skipped level"
V_START_OVERRIDE = "start override restarted"
V_OVERRIDE = "is a full override level (w:lvlOverride/w:lvl), not modelled"
_V_DIGITS = set("0123456789")
_V_THOUSANDS = ["", "M", "MM", "MMM"]
_V_HUNDREDS = ["", "C", "CC", "CCC", "CD", "D", "DC", "DCC", "DCCC", "CM"]
_V_TENS = ["", "X", "XX", "XXX", "XL", "L", "LX", "LXX", "LXXX", "XC"]
_V_ONES = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX"]
_V_PATTERN = {"decimal": "[0-9]+", "decimalZero": "[0-9][0-9]+", "upperLetter": "[A-Z]+", "lowerLetter": "[a-z]+",
              "upperRoman": "[IVXLCDM]+", "lowerRoman": "[ivxlcdm]+", "none": ""}


def _v_int(v) -> Optional[int]:
    if not isinstance(v, str) or not v:
        return None
    digits = v[1:] if v[0] == "-" else v
    if not digits or any(c not in _V_DIGITS for c in digits):
        return None
    return int(v)


def _v_lvl_index(v) -> Optional[int]:
    n = _v_int(v)
    return n if n is not None and n in range(9) else None


def _v_attr(parent, tag: str) -> Optional[str]:
    e = parent.find(W + tag)
    return None if e is None else e.get(W + "val")


class VLevel(NamedTuple):
    ilvl: int
    start: int
    fmt: str
    text: Optional[str]
    restart: Optional[int]
    legal: bool
    suff: str
    pstyle: Optional[str]
    unsupported: Optional[str]


def _v_level(lvl, k: int) -> Tuple[Optional[VLevel], Optional[str]]:
    sv, rv = _v_attr(lvl, "start"), _v_attr(lvl, "lvlRestart")
    start = 0 if sv is None else _v_int(sv)
    if start is None:
        return None, f"level {k}: start {sv!r} is not a whole number"
    restart = None if rv is None else _v_int(rv)
    if rv is not None and (restart is None or restart < 0):
        return None, f"level {k}: lvlRestart {rv!r} is not a whole number ≥ 0"
    fmt = _v_attr(lvl, "numFmt") or "decimal"
    suff = _v_attr(lvl, "suff") or "tab"
    legal_el = lvl.find(W + "isLgl")
    why = None
    if next(lvl.iter(V_ALTERNATE), None) is not None:
        why = "holds mc:AlternateContent (a custom format)"
    elif fmt not in V_FORMATS:
        why = f"uses the format {fmt!r} (not modelled)"
    elif suff not in V_SUFFIX:
        why = f"uses the suffix {suff!r} (not modelled)"
    return VLevel(k, start, fmt, _v_attr(lvl, "lvlText"), restart,
                  legal_el is not None and legal_el.get(W + "val") not in V_OFF, suff, _v_attr(lvl, "pStyle"), why), None


class VListDefs:
    """The verifier's reading of a numbering root (plus ``extra`` w:abstractNum / w:num elements)."""

    def __init__(self, root=None, extra: Sequence[Any] = ()):
        self.root, self._extra = root, list(extra)
        self.present = root is not None or len(self._extra) > 0
        kids = [c for c in ([] if root is None else list(root)) + self._extra if isinstance(c.tag, str)]
        abs_els: Dict[int, List[Any]] = {}
        num_els: Dict[int, List[Any]] = {}
        self._order: List[int] = []
        for c in kids:
            if c.tag == W + "abstractNum" and _v_int(c.get(W + "abstractNumId")) is not None:
                abs_els.setdefault(_v_int(c.get(W + "abstractNumId")), []).append(c)
            elif c.tag == W + "num" and _v_int(c.get(W + "numId")) is not None:
                nid = _v_int(c.get(W + "numId"))
                num_els.setdefault(nid, []).append(c)
                if nid not in self._order:
                    self._order.append(nid)
        # abstractNums: aid → {"levels": {k: VLevel}, "link": numStyleLink} | bad reason
        self.abstracts: Dict[int, Dict[str, Any]] = {}
        self.bad_abstracts: Dict[int, str] = {}
        for aid, els in abs_els.items():
            if len(els) != 1:
                self.bad_abstracts[aid] = f"abstractNum {aid} appears {len(els)} times"
                continue
            levels: Dict[int, VLevel] = {}
            problem = None
            for lvl in els[0]:
                if lvl.tag != W + "lvl":
                    continue
                k = _v_lvl_index(lvl.get(W + "ilvl"))
                if k is None or k in levels:
                    problem = f"a w:lvl with ilvl {lvl.get(W + 'ilvl')!r} (not 0–8, or repeated)"
                    break
                got, problem = _v_level(lvl, k)
                if problem:
                    break
                levels[k] = got
            if problem:
                self.bad_abstracts[aid] = f"abstractNum {aid}: {problem}"
            else:
                self.abstracts[aid] = {"levels": levels, "link": _v_attr(els[0], "numStyleLink"),
                                       "behind": _v_attr(els[0], "styleLink")}
        # nums: nid → {"abs": aid, "starts": {k: v}, "full": {k: VLevel}, "overridden": [k]} | bad reason
        self.nums: Dict[int, Dict[str, Any]] = {}
        self.bad_nums: Dict[int, str] = {}
        for nid, els in num_els.items():
            if len(els) != 1:
                self.bad_nums[nid] = f"num {nid} appears {len(els)} times"
                continue
            aid = _v_int(_v_attr(els[0], "abstractNumId"))
            if aid is None:
                self.bad_nums[nid] = f"num {nid}: abstractNumId is not a whole number"
                continue
            entry: Dict[str, Any] = {"abs": aid, "starts": {}, "full": {}, "overridden": []}
            for o in els[0]:
                if o.tag != W + "lvlOverride":
                    continue
                k = _v_lvl_index(o.get(W + "ilvl"))
                if k is None or k in entry["overridden"]:
                    self.bad_nums[nid] = f"num {nid}: a level override with ilvl {o.get(W + 'ilvl')!r}"
                    break
                entry["overridden"].append(k)
                sov = _v_attr(o, "startOverride")
                if sov is not None:
                    if _v_int(sov) is None:
                        self.bad_nums[nid] = f"num {nid}: startOverride {sov!r} is not a whole number"
                        break
                    entry["starts"][k] = _v_int(sov)
                full = o.find(W + "lvl")
                if full is not None:
                    entry["full"][k] = VLevel(k, 0, "decimal", None, None, False, "tab", _v_attr(full, "pStyle"),
                                              V_OVERRIDE)
            if nid not in self.bad_nums:
                self.nums[nid] = entry

    def with_extra(self, abstracts: Sequence[Any] = (), nums: Sequence[Any] = ()) -> "VListDefs":
        return VListDefs(self.root, self._extra + list(abstracts) + list(nums))

    def defined(self, nid) -> bool:
        return nid in self.nums or nid in self.bad_nums

    def problem(self, nid: int) -> Optional[Tuple[str, str]]:
        if nid in self.bad_nums:
            return "unreadable", self.bad_nums[nid]
        entry = self.nums.get(nid)
        if entry is None:
            return "unreadable", f"num {nid} is not defined"
        aid = entry["abs"]
        if aid in self.bad_abstracts:
            return "unreadable", self.bad_abstracts[aid]
        if aid not in self.abstracts:
            return "unreadable", f"num {nid}: abstractNum {aid} is not defined"
        if self.abstracts[aid]["link"] is not None:
            return "unsupported", f"num {nid}: its abstractNum is a numStyleLink to {self.abstracts[aid]['link']!r}"
        if self.abstracts[aid]["behind"] is not None:
            return "unsupported", (f"num {nid}: its abstractNum is behind the numbering style "
                                   f"{self.abstracts[aid]['behind']!r} (w:styleLink)")
        return None

    def abstract_of(self, nid) -> Optional[int]:
        return self.nums[nid]["abs"] if nid in self.nums else None

    def nums_of_abstract(self, aid: int) -> List[int]:
        return [nid for nid in self._order if nid in self.nums and self.nums[nid]["abs"] == aid]

    def level(self, nid, k: int) -> Optional[VLevel]:
        entry = self.nums.get(nid)
        if entry is None:
            return None
        if k in entry["full"]:
            return entry["full"][k]
        absn = self.abstracts.get(entry["abs"])
        return None if absn is None else absn["levels"].get(k)

    def levels_of(self, nid: int) -> Dict[int, VLevel]:
        out = {}
        for k in range(9):
            lv = self.level(nid, k)
            if lv is not None:
                out[k] = lv
        return out

    def start_override(self, nid, k: int) -> Optional[int]:
        entry = self.nums.get(nid)
        return None if entry is None else entry["starts"].get(k)

    def has_overrides(self, nid) -> bool:
        return nid in self.nums and len(self.nums[nid]["overridden"]) > 0

    def is_override(self, nid, k: int) -> bool:
        return nid in self.nums and k in self.nums[nid]["full"]


def v_list_defs(pkg) -> VListDefs:
    part = pkg.related(V_NUMBERING_REL)
    return VListDefs(pkg.xml(part) if part else None)


def v_styles(pkg) -> Styles:
    part = pkg.related(STYLES_REL)
    return Styles(pkg.xml(part) if part else None)


# ---- a paragraph's numbering -----------------------------------------------------------------------------------------

class VNumbering(NamedTuple):
    num_id: Optional[int]
    ilvl: Optional[int]
    source: str
    style: Optional[str]
    dangling: bool
    unreadable: Optional[str]
    ambiguous: bool = False
    linked: bool = False


def v_paragraph_style(p, styles: Styles) -> Optional[str]:
    ppr = p.find(W + "pPr")
    ps = ppr.find(W + "pStyle") if ppr is not None else None
    if ps is None:
        return styles.default.get("paragraph")
    v = ps.get(W + "val")
    return v if v else None


def _v_from_styles(styles: Styles, sid: Optional[str]):
    for st in styles.chain(sid):
        nid = st.find(W + "pPr/" + W + "numPr/" + W + "numId")
        if nid is not None:
            return nid, st.find(W + "pPr/" + W + "numPr/" + W + "ilvl"), st.get(W + "styleId")
    return None


def _v_chain_ilvl(styles: Styles, sid: Optional[str]):
    """The nearest style's own w:numPr/w:ilvl along the chain of ``sid``, or None."""
    found = [st.find(W + "pPr/" + W + "numPr/" + W + "ilvl") for st in styles.chain(sid)]
    found = [e for e in found if e is not None]
    return found[0] if found else None


def _v_by_element(styles: Styles, sid: Optional[str], list_style: Optional[str], defs: "VListDefs", nid: int):
    """The element-by-element level of a paragraph of style ``sid`` whose list ``nid`` style ``list_style`` names: the
    first own w:numPr/w:ilvl along the chain — or the linked level at ``list_style`` itself when it has no ilvl —; None
    when neither (the level is then 0)."""
    for st in styles.chain(sid):
        own = st.find(W + "pPr/" + W + "numPr/" + W + "ilvl")
        if own is not None:
            return own
        if st.get(W + "styleId") == list_style:
            link = _v_linked_level(defs, nid, list_style)
            if link is not None:
                return link
    return None


def _v_linked_level(defs: VListDefs, nid: int, sid: Optional[str]) -> Optional[int]:
    hits = [k for k, lv in defs.levels_of(nid).items() if sid and lv.pstyle == sid]
    return min(hits) if hits else None


def _v_resolve(own_num, own_lvl, from_style, defs: VListDefs, chain_lvl=None, by_element=None) -> Optional[VNumbering]:
    if not defs.present:
        return None
    if own_num is not None:
        el, src, sid, style_lvl = own_num, "direct", None, None
    elif from_style is not None:
        el, style_lvl, sid = from_style
        src = "style"
    else:
        return None
    nid = _v_int(el.get(W + "val"))
    if nid is None:
        return VNumbering(None, None, src, sid, False, f"numId {el.get(W + 'val')!r} cannot be read")
    if nid == 0:
        return None
    if not defs.defined(nid):
        return VNumbering(nid, None, src, sid, True, None)
    lvl_el = own_lvl if own_lvl is not None else style_lvl
    if lvl_el is not None:
        k = _v_lvl_index(lvl_el.get(W + "val"))
        if k is None:
            return VNumbering(nid, None, src, sid, False, f"ilvl {lvl_el.get(W + 'val')!r} cannot be read")
        if own_lvl is not None:
            return VNumbering(nid, k, src, sid, False, None)
        is_linked = False
    elif src == "direct":
        if chain_lvl is not None and _v_int(chain_lvl.get(W + "val")) != 0:
            return VNumbering(nid, None, src, sid, False, f"no own ilvl, the style chain's ilvl is "
                                                          f"{chain_lvl.get(W + 'val')!r}", True)
        return VNumbering(nid, 0, src, sid, False, None)
    else:
        link = _v_linked_level(defs, nid, sid)
        k, is_linked = (0, False) if link is None else (link, True)
    if by_element is not None:                 # read element by element: the same level, or ambiguous
        e = by_element(nid, sid)
        other = 0 if e is None else (e if isinstance(e, int) else _v_lvl_index(e.get(W + "val")))
        if other != k:
            return VNumbering(nid, None, src, sid, False,
                              f"style {sid} gives list {nid} level {k} as a unit, a style's own level "
                              f"{e if isinstance(e, int) or e is None else e.get(W + 'val')!r} element by element",
                              True)
    return VNumbering(nid, k, src, sid, False, None, False, is_linked)


def v_paragraph_numbering(p, styles: Styles, defs: VListDefs) -> Optional[VNumbering]:
    numpr = p.find(W + "pPr/" + W + "numPr")
    own_num = None if numpr is None else numpr.find(W + "numId")
    own_lvl = None if numpr is None else numpr.find(W + "ilvl")
    chain_lvl = None
    mine = v_paragraph_style(p, styles)
    if own_num is not None and own_lvl is None:
        chain_lvl = _v_chain_ilvl(styles, mine)
    return _v_resolve(own_num, own_lvl, _v_from_styles(styles, mine) if own_num is None else None, defs, chain_lvl,
                      lambda nid, list_style: _v_by_element(styles, mine, list_style, defs, nid))


def v_style_numbering(sid: Optional[str], styles: Styles, defs: VListDefs) -> Optional[VNumbering]:
    return _v_resolve(None, None, _v_from_styles(styles, sid), defs,
                      by_element=lambda nid, list_style: _v_by_element(styles, sid, list_style, defs, nid))


# ---- formats ---------------------------------------------------------------------------------------------------------

def _v_letters(n: int) -> str:
    out = ""
    for _ in range((n - 1) // 26 + 1):
        out += "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[(n - 1) % 26]
    return out


def v_format_value(fmt: str, n: int, text: Optional[str] = None) -> Optional[str]:
    if n < 0:                       # Word shows no negative number, in any format
        return None
    if fmt == "none":
        return ""
    if fmt == "bullet":
        return "" if text is None else text
    if fmt == "decimal":
        return "%d" % n
    if fmt == "decimalZero":
        return "%02d" % n if n in range(10) else "%d" % n
    if fmt in ("upperLetter", "lowerLetter"):
        if n <= 0:
            return None
        s = _v_letters(n)
        return s.lower() if fmt == "lowerLetter" else s
    if fmt in ("upperRoman", "lowerRoman"):
        if n <= 0 or n >= 4000:
            return None
        s = _V_THOUSANDS[n // 1000] + _V_HUNDREDS[n // 100 % 10] + _V_TENS[n // 10 % 10] + _V_ONES[n % 10]
        return s.lower() if fmt == "lowerRoman" else s
    return None


def _v_placeholders(text: str) -> List[Tuple[str, Any]]:
    """The text cut into ("lit", char) and ("lvl", k) pieces (``%1``–``%9`` → k = 0–8)."""
    out: List[Tuple[str, Any]] = []
    i = 0
    while i < len(text):
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if text[i] == "%" and nxt and nxt in "123456789":
            out.append(("lvl", ord(nxt) - ord("1")))
            i += 2
        else:
            out.append(("lit", text[i]))
            i += 1
    return out


def _v_render(text: str, legal: bool, value, level) -> Tuple[Optional[str], Optional[str]]:
    shown = []
    for kind, x in _v_placeholders(text):
        if kind == "lit":
            shown.append(x)
            continue
        ref = level(x)
        if ref is None:
            return None, f"%{x + 1} names an undefined level"
        if ref.unsupported is not None:
            return None, f"%{x + 1} names a level that {ref.unsupported}"
        got = v_format_value("decimal" if legal else ref.fmt, value(x), ref.text)
        if got is None:
            return None, f"%{x + 1}: {value(x)} has no {ref.fmt} form"
        shown.append(got)
    return "".join(shown), None


def _v_skipped(text: str, vals: Dict[int, int]) -> Optional[int]:
    """The first level a ``%j`` of ``text`` names that holds no value in ``vals``, else None."""
    for kind, x in _v_placeholders(text):
        if kind == "lvl" and x not in vals:
            return x
    return None


def _v_skipped_below(vals: Dict[int, int], at: int) -> List[int]:
    """The levels below ``at`` without a value when a paragraph at ``at`` is counted (LibreOffice starts them then)."""
    return [k for k in range(at) if k not in vals]


def _v_override_restart(nth: int, override: Optional[int]) -> bool:
    """A level's value is uncertain from its second start on when its num gives it a startOverride."""
    return nth >= 2 and override is not None


def v_has_restart_rule(defs: "VListDefs", nid: int) -> List[int]:
    """The levels of num ``nid`` whose definition carries a w:lvlRestart value (LibreOffice ignores it)."""
    return sorted(k for k, lv in defs.levels_of(nid).items() if lv.restart is not None)


def _v_restarts(restart: Optional[int], at: int, k: int) -> bool:
    """Level k (> at) loses its value after a paragraph at level ``at``."""
    if restart is not None and restart >= 1 and restart <= k:
        return at < restart
    return restart != 0


def _v_start_of(defs: VListDefs, nid: int, k: int) -> int:
    so = defs.start_override(nid, k)
    if so is not None:
        return so
    lv = defs.level(nid, k)
    return 0 if lv is None else lv.start


# ---- counting --------------------------------------------------------------------------------------------------------

class VNumbered(NamedTuple):
    index: int
    num_id: Optional[int]
    ilvl: Optional[int]
    counters: Optional[Tuple[int, ...]]
    text: Optional[str]
    suff: Optional[str]
    fmt: Optional[str]
    not_evaluated: Optional[str]
    issue: Optional[str]
    abstract_id: Optional[int]
    source: str
    style: Optional[str]
    linked: bool = False


def v_counted(p) -> bool:
    for anc in p.iterancestors():
        if anc.tag == W + "txbxContent":
            return False
    mark = p.find(W + "pPr/" + W + "rPr")
    if mark is None:
        return True
    return all(c.tag not in (W + "del", W + "moveFrom") for c in mark)


def _v_at(index: int, p, styles: Styles, defs: VListDefs, override) -> Optional[VNumbering]:
    if override and index in override:
        got = override[index]
        if got is None or got[0] == 0 or not defs.present:
            return None
        return VNumbering(got[0], got[1], "override", None, not defs.defined(got[0]), None)
    return v_paragraph_numbering(p, styles, defs)


def v_count(root, styles: Styles, defs: VListDefs,
            override: Optional[Mapping[int, Optional[Tuple[int, int]]]] = None) -> List[VNumbered]:
    numbered = []
    for index, p in enumerate(root.iter(W + "p")):
        if v_counted(p):
            nb = _v_at(index, p, styles, defs, override)
            if nb is not None and not nb.dangling:
                numbered.append((index, nb))
    nums_by_abs: Dict[int, set] = {}
    for _, nb in numbered:
        aid = defs.abstract_of(nb.num_id)
        if aid is not None:
            nums_by_abs.setdefault(aid, set()).add(nb.num_id)

    out: List[VNumbered] = []
    values: Dict[int, Dict[int, int]] = {}
    started: Dict[int, Dict[int, int]] = {}         # nid → level → starts so far
    uncertain: Dict[int, set] = {}                  # nid → levels whose value Word may give otherwise
    passed_over: Dict[int, set] = {}                # nid → levels a deeper paragraph was numbered without
    blocked: Dict[Any, str] = {}            # nid (or "*" for every list) → why later paragraphs are not evaluated
    for index, nb in numbered:
        nid, at, aid = nb.num_id, nb.ilvl, defs.abstract_of(nb.num_id)

        def row(issue=None, why=None, counters=None, text=None, suff=None, fmt=None):
            return VNumbered(index, nid, at, counters, text, suff, fmt, why, issue, aid, nb.source, nb.style,
                             nb.linked)
        if nb.unreadable:
            out.append(row("ambiguous-level" if nb.ambiguous else "unreadable", nb.unreadable))
            blocked.setdefault("*" if nid is None else nid, f"an earlier numbered paragraph: {nb.unreadable}")
            continue
        if "*" in blocked or nid in blocked:
            out.append(row("earlier", blocked.get("*") or blocked[nid]))
            continue
        trouble = defs.problem(nid)
        if trouble is not None:
            out.append(row(trouble[0], trouble[1]))
            continue
        vals = values.setdefault(nid, {})
        counts, doubt = started.setdefault(nid, {}), uncertain.setdefault(nid, set())
        over = passed_over.setdefault(nid, set())
        for k in [k for k in sorted(set(vals) | over) if k > at]:
            lv_k = defs.level(nid, k)
            if _v_restarts(None if lv_k is None else lv_k.restart, at, k):
                vals.pop(k, None)
                doubt.discard(k)
                over.discard(k)
        over |= set(_v_skipped_below(vals, at))
        if at in vals:
            vals[at] += 1
        else:
            vals[at] = _v_start_of(defs, nid, at)
            counts[at] = counts.get(at, 0) + 1
            if _v_override_restart(counts[at], defs.start_override(nid, at)):
                doubt.add(at)
            else:
                doubt.discard(at)
        lv = defs.level(nid, at)
        if lv is None or defs.is_override(nid, at):
            suff = fmt = None
        else:
            suff, fmt = lv.suff, lv.fmt
        if len(nums_by_abs.get(aid, ())) >= 2:
            out.append(row("shared", f"{V_SHARED}: abstractNum {aid} numbers paragraphs through nums "
                                     f"{sorted(nums_by_abs[aid])}", suff=suff, fmt=fmt))
            continue
        overridden = [k for k in range(at + 1) if defs.is_override(nid, k)]
        if overridden:
            out.append(row("override", f"level {overridden[0]} of num {nid} {V_OVERRIDE}", suff=suff, fmt=fmt))
            continue
        names = [x for kind, x in _v_placeholders(lv.text or "") if kind == "lvl"] if lv is not None else []
        shaky = [k for k in sorted(doubt) if k <= at or k in names]
        if shaky:
            out.append(row("start-override", f"{V_START_OVERRIDE}: level {shaky[0]} of num {nid} restarted with a "
                                             f"startOverride", suff=suff, fmt=fmt))
            continue
        unsure = [k for k in sorted(over) if (k == at or k in names) and k in vals]
        if unsure:
            out.append(row("skipped", f"{V_SKIPPED}: level {unsure[0]} of num {nid} was passed over by a deeper "
                                      f"paragraph — LibreOffice and Word number it differently", suff=suff, fmt=fmt))
            continue
        shown = tuple(vals.get(k, _v_start_of(defs, nid, k)) for k in range(at + 1))
        if lv is None:
            out.append(row(counters=shown, text=""))
        elif lv.unsupported is not None:
            out.append(row("unsupported", f"level {at} {lv.unsupported}", shown, suff=suff, fmt=fmt))
        elif lv.text is None:
            out.append(row(counters=shown, text="", suff=suff, fmt=fmt))
        else:
            text, why = _v_render(lv.text, lv.legal, lambda k: vals.get(k, _v_start_of(defs, nid, k)),
                                  lambda k: defs.level(nid, k))
            gap = None if why else _v_skipped(lv.text, vals)
            if why:
                out.append(row("unsupported", why, shown, None, suff, fmt))
            elif gap is not None:
                out.append(row("skipped", f"{V_SKIPPED}: %{gap + 1} names level {gap}, which has no value yet",
                               shown, None, suff, fmt))
            else:
                out.append(row(None, None, shown, text, suff, fmt))
    return out


def v_main_abstract(pars: Sequence[VNumbered]) -> Optional[int]:
    counts: Dict[int, List[int]] = {}
    for pos, p in enumerate(pars):
        if p.abstract_id is not None and p.fmt not in (None, "bullet"):
            counts.setdefault(p.abstract_id, []).append(pos)
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: (-len(kv[1]), kv[1][0]))
    return ranked[0][0]


def v_list_is_clean(defs: VListDefs, nid: int, pars: Sequence[VNumbered]) -> Optional[str]:
    trouble = defs.problem(nid)
    if trouble is not None:
        return trouble[1]
    if defs.has_overrides(nid):
        return f"num {nid} carries w:lvlOverride"
    aid = defs.abstract_of(nid)
    sharing = sorted({p.num_id for p in pars if p.abstract_id == aid and p.num_id not in (None, nid)})
    if sharing:
        return f"{V_SHARED}: abstractNum {aid} also numbers paragraphs through nums {sharing}"
    for k in sorted(defs.abstracts[aid]["levels"]):
        lv = defs.abstracts[aid]["levels"][k]
        if lv.unsupported is not None:
            return f"level {k} {lv.unsupported}"
    return None


def v_visible_line(number: Optional[str], run_text: str, suff: Optional[str] = None) -> str:
    text = run_text
    if number is not None:
        text = number + V_SUFFIX.get(suff or "", "") + run_text
    out: List[str] = []
    for ch in text:
        blank = ch in " \t "
        if blank and out and out[-1] == " ":
            continue
        out.append(" " if blank else ch)
    while out and out[0] == " ":
        out.pop(0)
    while out and out[-1] == " ":
        out.pop()
    return "".join(out)


# ---- the scheme and typed numbers ------------------------------------------------------------------------------------

class VSchemeLevel(NamedTuple):
    style: str
    num_id: int
    ilvl: int
    level: VLevel
    pattern: str


class VScheme(NamedTuple):
    num_id: int
    abstract_id: Optional[int]
    levels: List[VSchemeLevel]
    level_defs: Dict[int, VLevel]


def _v_pattern(level_defs: Mapping[int, VLevel], k: int) -> str:
    """Level k's typed-number pattern; ValueError when there is none."""
    lv = level_defs.get(k)
    if lv is None or lv.text is None:
        raise ValueError(f"level {k} is undefined or has no number text")
    out = []
    for kind, x in _v_placeholders(lv.text):
        if kind == "lit":
            out.append(re.escape(x))
            continue
        ref = level_defs.get(x)
        if ref is None or ref.unsupported is not None:
            raise ValueError(f"level {k}'s %{x + 1} names an undefined or unsupported level")
        if not lv.legal and ref.fmt == "bullet":
            raise ValueError(f"level {k}'s %{x + 1} names a bullet level")
        out.append(_V_PATTERN["decimal" if lv.legal else ref.fmt])
    return "".join(out)


def v_level_pattern(scheme: VScheme, k: int) -> str:
    return scheme.levels[k].pattern


def v_resolve_scheme(levels: Any, styles: Styles, defs: VListDefs) -> VScheme:
    """The scheme, or ValueError (the op must refuse: ``scheme`` in the document, ``bad-params`` in a template; a
    malformed ``levels`` is always ``bad-params``)."""
    if not isinstance(levels, list) or len(levels) not in range(1, 10) or \
            any(not isinstance(x, str) or x.strip() == "" for x in levels):
        raise ValueError("levels must be 1–9 style references (the op must refuse: bad-params)")
    picked: List[Tuple[str, VNumbering]] = []
    for k, ref in enumerate(levels):
        sid = v_resolve_style(styles, ref)
        if sid is None or (styles.by_id[sid].get(W + "type") or "paragraph") != "paragraph":
            raise ValueError(f"level {k}: {ref!r} is not a paragraph style here")
        nb = v_style_numbering(sid, styles, defs)
        if nb is None or nb.dangling or nb.unreadable:
            raise ValueError(f"level {k}: style {sid} is not numbered by a list this file defines")
        if picked and nb.num_id != picked[0][1].num_id:
            raise ValueError(f"level {k}: style {sid} is numbered by another list")
        if nb.ilvl != k:
            raise ValueError(f"level {k}: style {sid} is at level {nb.ilvl}")
        picked.append((sid, nb))
    nid = picked[0][1].num_id
    if defs.problem(nid) is not None:
        raise ValueError(defs.problem(nid)[1])
    level_defs = defs.levels_of(nid)
    rows = []
    for k, (sid, _) in enumerate(picked):
        lv = level_defs.get(k)
        if lv is None or lv.unsupported is not None or lv.text is None or lv.fmt == "bullet":
            raise ValueError(f"level {k} (style {sid}): undefined, unsupported, without text or a bullet")
        rows.append(VSchemeLevel(sid, nid, k, lv, _v_pattern(level_defs, k)))
    return VScheme(nid, defs.abstract_of(nid), rows, level_defs)


class VTyped(NamedTuple):
    level: Optional[int]
    token: str
    token_start: int
    end: int
    levels: Tuple[int, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.levels) > 1


def v_typed_number(run_text: str, scheme: VScheme) -> Optional[VTyped]:
    """Spaces/tabs at the start, a non-empty match of one level's pattern, then spaces/tabs/NBSPs or the end — the regular
    expression ``[ \\t]*(pattern)([ \\t\\xa0]+|end)`` matched at position 0 (its backtracking is part of the definition)."""
    found = []
    for row in scheme.levels:
        m = re.compile("[ \t]*(" + row.pattern + ")(?:[ \t\u00a0]+|\\Z)").match(run_text)
        if m is not None and m.end(1) > m.start(1):
            found.append((row.ilvl, m))
    if not found:
        return None
    first = found[0][1]
    return VTyped(found[0][0] if len(found) == 1 else None, first.group(1), first.start(1), first.end(),
                  tuple(k for k, _ in found))
