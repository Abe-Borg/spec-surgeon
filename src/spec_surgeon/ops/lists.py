"""The shared list (numbering) model — transform side (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §0, §2.1).

Numbering is the one formatting family the engine did not model before batch 5. Every numbering op predicts with this
module; its V10 kind recomputes with verify/lists.py, which implements the same definitions with its own code (rule 1;
CLAUDE.md lists them among the definitions that must stay identical on both sides, and tests/test_lists.py compares the
two on every paragraph of the numbering fixtures and on thousands of random list definitions).

Definitions
-----------

* **Whole number**: an attribute value made of an optional ``-`` and one or more ASCII digits (``whole``). An **ilvl**
  is a whole number 0–8.
* **Numbering part**: the main part's ``numbering`` relationship. None → every paragraph is unnumbered.
* **abstractNum**: a ``w:abstractNum`` child of the numbering root whose ``w:abstractNumId`` is whole (others define
  nothing). **Unreadable** when two abstractNums share the id, or one of its ``w:lvl`` children has a ``w:ilvl`` that is
  not an ilvl, or two of them share an ilvl, or a level's ``w:start/@w:val`` / ``w:lvlRestart/@w:val`` is present and not
  whole (a negative restart counts as not whole). It carries ``w:numStyleLink`` (a list defined through a numbering
  style: **unsupported**) and ``w:styleLink``.
* **num**: a ``w:num`` child of the root whose ``w:numId`` is whole (others define nothing; a numId that no such
  ``w:num`` carries is **dangling**). **Unreadable** when two nums share the id, its ``w:abstractNumId/@w:val`` is
  missing or not whole, one of its ``w:lvlOverride`` has a ``w:ilvl`` that is not an ilvl, two share an ilvl, or a
  ``w:startOverride/@w:val`` is present and not whole. Per ``w:lvlOverride``: its ``startOverride`` (supported) and its
  **full override level** (a ``w:lvl`` child). Full override levels are NOT modelled (Word does not read them as ECMA-376
  describes — MS-OI29500 says it ignores ``w:lvlRestart`` there): that level of that num is unsupported.
* **Level definition** of ``(numId, ilvl)`` (``ListDefs.level``): the num's full override level when it has one (a
  ``LevelDef`` whose only fields read are ``pstyle`` — for the linked level — and ``unsupported``), else the
  abstractNum's ``w:lvl`` of that ilvl, else none (**missing**: the paragraph counts and shows no number). Fields:
  ``start`` (``w:start/@w:val``, default 0), ``fmt`` (``w:numFmt/@w:val``, default ``decimal``), ``text``
  (``w:lvlText/@w:val``, None when absent), ``restart`` (``w:lvlRestart/@w:val``, None when absent), ``legal``
  (``w:isLgl`` present and ``w:val`` not 0/false/off), ``suff`` (``w:suff/@w:val``, default ``tab``), ``pstyle``
  (``w:pStyle/@w:val``). **Unsupported** (``LevelDef.unsupported`` holds the reason): a level holding
  ``mc:AlternateContent`` (Word 2010's custom formats such as ``001``), a ``fmt`` outside ``FORMATS``, a ``suff`` other
  than tab/space/nothing, a full override level.
* **A paragraph's style** (``paragraph_style``): ``w:pPr/w:pStyle/@w:val`` — an empty or missing value names no style —,
  else (no ``w:pStyle``) the default paragraph style. A style chain is the style and its ``w:basedOn`` ancestors, nearest
  first, cycle-safe.
* **A paragraph's numbering** ``(numId, ilvl)`` or none (``paragraph_numbering``):
  1. *direct*: the paragraph's own ``w:pPr/w:numPr`` — its ``w:numId`` (d_num) and ``w:ilvl`` (d_lvl), each when present;
  2. *style*: along the paragraph's style chain, the first style whose own ``w:pPr/w:numPr`` holds a ``w:numId``: s_num,
     that style's own ``w:ilvl`` (s_lvl, may be none) and its id (s_style);
  3. numId = d_num when present, else s_num; none → unnumbered; not whole → **unreadable**; 0 → unnumbered; no numbering
     part → unnumbered; a dangling numId → ``Numbering.dangling`` (unnumbered for counting, reported by the audit);
  4. ilvl = d_lvl when present; else, when numId came from the style: s_lvl when present, else the **linked level** (the
     lowest ilvl whose level definition names s_style in its ``pstyle``), else 0; when numId came from the paragraph and it
     has no ilvl: 0. An ilvl that is not an ilvl → **unreadable**.
* **Formats** (``format_value``): ``decimal`` str(n); ``decimalZero`` ``0`` + str(n) when 0 ≤ n ≤ 9, else str(n);
  ``upperLetter``/``lowerLetter`` (n ≥ 1) the letter of (n−1) mod 26 repeated (n−1) div 26 + 1 times (27 → AA, 28 → BB);
  ``upperRoman``/``lowerRoman`` (1–3999) the standard numerals; ``bullet`` the level's text as written; ``none`` "".
  Another format, or a value out of its range → None (unsupported).

Counting (``count``; design §0.2)
---------------------------------

A paragraph is **counted** (``counted``) unless it lies in a text box (a ``w:txbxContent`` ancestor) or its mark is a
pending deletion (``w:pPr/w:rPr`` holds ``w:del`` or ``w:moveFrom``); hidden paragraphs count. Over the main part's
paragraphs in ``root.iter(w:p)`` order, each counted paragraph with a numbering that is not dangling is a
``NumberedPar``. A **list instance** is a numId. For a paragraph at level L of instance N:

1. every level k > L of N holding a current value loses it when it restarts after L (``restarts``): ``restart`` None →
   restarts; 0 → never; r ≥ 1 → restarts when L ≤ r − 1, and is ignored (as None) when r − 1 ≥ k — Word's reading
   (ECMA-376 §17.9.11). LibreOffice ignores ``w:lvlRestart`` (0 and r ≥ 1 alike): wherever a level carries one, the
   render proofs differ from Word (``has_restart_rule`` lists those levels, for a flag);
2. level L's value = its start (``start_of``: N's ``startOverride`` for L when it has one, else the level's ``start``, else
   0) when it has no current value, else the current value + 1;
3. the **counters** = the values of levels 0…L (a level with no current value shows its start); the **rendered number**
   (``render``) = the level's ``text`` with each ``%j`` (j = 1…9) replaced by level j−1's value in level j−1's format —
   in decimal when level L is ``legal``; no ``text`` (or a missing level) → "". A ``%j`` naming a level with NO current
   value in N makes the paragraph not evaluated (``skipped``, below).

``NumberedPar.suff``/``fmt`` are level L's own whenever level L is defined, is not a full override level and the instance
could be counted (none of the first three issues below); else None.

**Not evaluated** (``NumberedPar.not_evaluated`` = the reason, ``NumberedPar.issue`` = one of the codes below; the
counters and the rendered number are None unless said), checked in this order:

* ``unreadable`` — the paragraph's own numId or ilvl (or its style's) cannot be read. It changes no count, so every LATER
  paragraph of that instance (an unknown numId: of every instance) is not evaluated either (``earlier``);
* ``earlier`` — see above;
* ``unreadable`` / ``unsupported`` — the instance (``ListDefs.problem``): the num or its abstractNum is unreadable, the
  abstractNum is not defined or carries ``w:numStyleLink``;
* ``shared`` (``SHARED``) — the num shares its abstractNum with ANOTHER num that numbers a counted paragraph: Word
  continues one count across such nums, LibreOffice keeps them apart (design §0.2); counting still runs per numId;
* ``override`` — a level 0…L of N is a full override level (its value is in the counters);
* ``unsupported`` — level L is unsupported, or its text names a level that is missing or unsupported, or a value it shows
  is out of its format's range. The counters are kept (the count itself is certain);
* ``skipped`` (``SKIPPED``; ``skipped_level``) — its text names a level that has no current value in N: never reached,
  or reset by a restart ("1.1.1" right after a PART). Word shows such a level as 0 ("1.0.1"), LibreOffice shows its start
  and then continues from it, a model showing the start would agree with neither — so the number is not offered. The
  reason names the level; the counters are kept (the start stands for the missing value).

Scheme and typed numbers (design §0.3, §2.1)
--------------------------------------------

* ``resolve_scheme``: ``levels`` (1–9 paragraph-style references, level 0 first) form a **scheme** when every one resolves
  (``style_import.resolve_style``) to a paragraph style whose own numbering (``style_numbering``: steps 2–4 on the style
  chain alone) is the SAME num at ilvl = its position, the num has no instance problem, every level is defined, supported,
  not ``bullet`` and has ``text``, and every level's pattern can be made. Else ``JobException`` with the caller's code
  (``bad-params`` for a template — the same for every file —, ``scheme`` for the document); a malformed ``levels`` is
  always ``bad-params``.
* **Level pattern** (``pattern_of``): the level's text with literal characters escaped and each ``%j`` the pattern of
  level j−1's format (``FORMAT_PATTERNS``; ``[0-9]+`` for every ``%j`` when the level is ``legal``); a ``%j`` naming a
  missing, unsupported or ``bullet`` level → no pattern.
* **Typed number** (``typed_number``) on a paragraph's run text (batch 1's definition, ``textedit.run_text``): for each
  scheme level, ``[ \\t]*`` at position 0, then a NON-EMPTY match of the level's pattern, then ``[ \\t\\xa0]+`` or the end of
  the run text — the regular expression ``[ \\t]*(pattern)(?:[ \\t\\xa0]+|\\Z)`` matched at position 0, its backtracking
  part of the definition (a break, ``\\n``, is neither whitespace nor the end). One level matching → its ``TypedNumber``
  (``token`` the number as typed, ``end`` the end of the prefix: position 0 to the end of the whitespace); several →
  ``ambiguous`` (``level`` None, ``levels`` all of them); none → None.
* **Visible line** (``visible_line``, design §2.4): the rendered number, its suffix (tab → ``\\t``, space → `` ``, nothing
  → ""), then the run text — or the run text alone for an unnumbered paragraph; every run of spaces, tabs and NBSPs
  collapsed to one space, leading and trailing spaces stripped.
* **Main abstractNum** (``main_abstract``, design §1 ``from: "main"``): the abstractNum numbering the most counted
  paragraphs at a non-``bullet`` level (a paragraph whose level is missing or whose list cannot be read does not count);
  a tie → the one of the first such paragraph.
* **A clean destination list** (``list_is_clean``): the num is readable and has no instance problem, carries no
  ``w:lvlOverride`` at all, its abstractNum is numbered by no OTHER num that numbers a counted paragraph, and every level
  its abstractNum defines is supported.

API (for the batch-5 ops)
-------------------------
``ListDefs.load(pkg)`` / ``ListDefs(root, part=None)``
    The numbering part read once. ``.present`` (a numbering part, or extra definitions), ``.part``, ``.defined(num_id)``,
    ``.problem(num_id) -> (issue, reason) | None``, ``.abstract_of(num_id)``, ``.nums_of_abstract(abs_id)`` (document
    order), ``.level(num_id, ilvl) -> LevelDef | None``, ``.levels_of(num_id) -> {ilvl: LevelDef}``,
    ``.start_override(num_id, ilvl)``, ``.has_overrides(num_id)`` (any ``w:lvlOverride``), ``.is_override(num_id,
    ilvl)`` (a full override level), ``.unreadable_nums`` / ``.unreadable_abstracts``
    ({id: reason}); ``.with_extra(abstracts=(), nums=())`` — a new ListDefs with more definitions read as if they stood
    in the numbering part (an imported list that is not in the document yet: ``style_import.build_copies``' copies).
``paragraph_style(p, sheet)`` / ``paragraph_numbering(p, sheet, defs) -> Numbering | None`` /
``style_numbering(style_id, sheet, defs) -> Numbering | None``
    ``sheet`` is anything with ``.styles`` ({styleId: w:style}) and ``.defaults`` ({type: styleId}) — a
    ``selectors.StyleSheet``, or a stand-in for the planned output's styles. ``Numbering``: ``num_id``, ``ilvl``,
    ``source`` (direct | style | override), ``style`` (s_style), ``dangling``, ``unreadable`` (reason).
``counted(p) -> bool``
``count(root, sheet, defs, override=None) -> [NumberedPar]``
    ``override``: {paragraph ordinal: (num_id, ilvl) or None} replacing those paragraphs' numbering (the planned output;
    supply an imported list's definitions with ``defs.with_extra``). ``NumberedPar``: ``index`` (ordinal in
    ``root.iter(w:p)``), ``num_id``, ``ilvl``, ``counters`` (tuple), ``text`` (rendered), ``suff``, ``fmt``,
    ``not_evaluated`` (reason), ``issue`` (code), ``abstract_id``, ``source``, ``style``; ``.evaluated``.
``format_value(fmt, n, text=None) -> str | None``
``resolve_scheme(levels, sheet, defs, where="this file", code="scheme") -> Scheme``
    ``Scheme``: ``num_id``, ``abstract_id``, ``levels`` ([``SchemeLevel``: ``style``, ``num_id``, ``ilvl``, ``level``
    (``LevelDef``), ``pattern``]), ``level_defs`` (every level of the num).
``level_pattern(scheme, k) -> str`` / ``pattern_of(level_defs, k) -> (pattern | None, reason | None)``
``typed_number(run_text, scheme) -> TypedNumber | None``
    ``TypedNumber``: ``level`` (None when ambiguous), ``token``, ``token_start``, ``end``, ``levels``; ``.ambiguous``.
``main_abstract(pars) -> int | None``
``visible_line(number_text, run_text, suff=None) -> str``
``list_is_clean(defs, num_id, counted_pars) -> str | None``
``has_restart_rule(defs, num_id) -> [ilvl]``
    The levels of the list whose definition carries a ``w:lvlRestart`` (LibreOffice ignores it: flag them).
Lower-level pieces (each a module function, so a test can plant a defect in it): ``whole``, ``read_level``,
``style_chain``, ``linked_level``, ``render``, ``skipped_level``, ``restarts``, ``start_of``, ``counted``;
``ListDefs.is_override``; constants ``FORMATS``, ``SUFFIXES``, ``FORMAT_PATTERNS``, ``SHARED`` / ``SKIPPED`` (every
``shared`` / ``skipped`` reason starts with it), ``OVERRIDE_REASON``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from ..package import W, DocxPackage
from .base import JobException
from .style_import import numbering_part, resolve_style

MC_ALTERNATE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent"
FALSY = ("0", "false", "off")
FORMATS = ("decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman", "bullet", "none")
SUFFIXES = {"tab": "\t", "space": " ", "nothing": ""}
FORMAT_PATTERNS = {"decimal": "[0-9]+", "decimalZero": "[0-9]{2,}", "upperLetter": "[A-Z]+", "lowerLetter": "[a-z]+",
                   "upperRoman": "[IVXLCDM]+", "lowerRoman": "[ivxlcdm]+", "none": ""}
SHARED = "shared list definition"
SKIPPED = "skipped level"
OVERRIDE_REASON = ("is a full override level (w:lvlOverride/w:lvl); Word does not read those as the standard describes, "
                   "so its numbers are not modelled")
_WHOLE = re.compile(r"-?[0-9]+")
_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
          (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def whole(v: Any) -> Optional[int]:
    """The whole number an attribute value spells (an optional ``-``, then ASCII digits), else None."""
    return int(v) if isinstance(v, str) and _WHOLE.fullmatch(v) else None


def _ilvl(v: Any) -> Optional[int]:
    n = whole(v)
    return n if n is not None and 0 <= n <= 8 else None


def _val(el) -> Optional[str]:
    return el.get(W + "val") if el is not None else None


# ---- the definitions --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelDef:
    ilvl: int
    start: int = 0
    fmt: str = "decimal"
    text: Optional[str] = None
    restart: Optional[int] = None
    legal: bool = False
    suff: str = "tab"
    pstyle: Optional[str] = None
    unsupported: Optional[str] = None     # the reason, or None


def read_level(lvl, ilvl: int) -> Tuple[Optional[LevelDef], Optional[str]]:
    """(the level, None) or (None, why its definition is unreadable)."""
    start_el, restart_el = lvl.find(W + "start"), lvl.find(W + "lvlRestart")
    start, restart = 0, None
    if start_el is not None and start_el.get(W + "val") is not None:
        start = whole(start_el.get(W + "val"))
        if start is None:
            return None, f"level {ilvl}'s start {start_el.get(W + 'val')!r} is not a whole number"
    if restart_el is not None and restart_el.get(W + "val") is not None:
        restart = whole(restart_el.get(W + "val"))
        if restart is None or restart < 0:
            return None, f"level {ilvl}'s restart rule {restart_el.get(W + 'val')!r} is not a whole number ≥ 0"
    fmt = _val(lvl.find(W + "numFmt")) or "decimal"
    text_el = lvl.find(W + "lvlText")
    lgl = lvl.find(W + "isLgl")
    suff = _val(lvl.find(W + "suff")) or "tab"
    ps = lvl.find(W + "pStyle")
    unsupported = None
    if any(True for _ in lvl.iter(MC_ALTERNATE)):
        unsupported = "holds mc:AlternateContent (a Word 2010+ custom number format)"
    elif fmt not in FORMATS:
        unsupported = f"has the number format {fmt!r}, which is not supported"
    elif suff not in SUFFIXES:
        unsupported = f"has the suffix {suff!r}, which is not supported"
    return LevelDef(ilvl=ilvl, start=start, fmt=fmt, text=_val(text_el), restart=restart,
                    legal=lgl is not None and _val(lgl) not in FALSY, suff=suff, pstyle=_val(ps),
                    unsupported=unsupported), None


@dataclass
class _Abstract:
    levels: Dict[int, LevelDef]
    num_style_link: Optional[str]
    style_link: Optional[str]


@dataclass
class _Num:
    abstract_id: int
    start_overrides: Dict[int, int]
    override_levels: Dict[int, LevelDef]       # full override levels: unsupported, pstyle kept
    overrides: List[int]                       # the ilvl of every w:lvlOverride


class ListDefs:
    """The list definitions of a numbering part (and any ``extra`` ones). See the module docstring."""

    def __init__(self, root=None, part: Optional[str] = None, extra: Sequence[Any] = ()):
        self.root, self.part, self._extra = root, part, list(extra)
        self.present = root is not None or bool(self._extra)
        kids = [c for c in (list(root) if root is not None else []) + self._extra if isinstance(c.tag, str)]
        self.abstracts: Dict[int, _Abstract] = {}
        self.nums: Dict[int, _Num] = {}
        self.unreadable_abstracts: Dict[int, str] = {}
        self.unreadable_nums: Dict[int, str] = {}
        self._num_order: List[int] = []
        seen_abs: Dict[int, int] = {}
        seen_num: Dict[int, int] = {}
        for c in kids:
            if c.tag == W + "abstractNum" and whole(c.get(W + "abstractNumId")) is not None:
                a = whole(c.get(W + "abstractNumId"))
                seen_abs[a] = seen_abs.get(a, 0) + 1
            elif c.tag == W + "num" and whole(c.get(W + "numId")) is not None:
                n = whole(c.get(W + "numId"))
                seen_num[n] = seen_num.get(n, 0) + 1
                if n not in self._num_order:
                    self._num_order.append(n)
        for c in kids:
            if c.tag == W + "abstractNum" and whole(c.get(W + "abstractNumId")) is not None:
                a = whole(c.get(W + "abstractNumId"))
                if seen_abs[a] > 1:
                    self.unreadable_abstracts[a] = f"abstractNum {a} is defined {seen_abs[a]} times"
                    continue
                parsed, why = self._read_abstract(c)
                if why:
                    self.unreadable_abstracts[a] = f"abstractNum {a}: {why}"
                else:
                    self.abstracts[a] = parsed
            elif c.tag == W + "num" and whole(c.get(W + "numId")) is not None:
                n = whole(c.get(W + "numId"))
                if seen_num[n] > 1:
                    self.unreadable_nums[n] = f"list {n} is defined {seen_num[n]} times"
                    continue
                parsed, why = self._read_num(c)
                if why:
                    self.unreadable_nums[n] = f"list {n}: {why}"
                else:
                    self.nums[n] = parsed

    @staticmethod
    def _read_abstract(el) -> Tuple[Optional[_Abstract], Optional[str]]:
        levels: Dict[int, LevelDef] = {}
        for lvl in el.findall(W + "lvl"):
            k = _ilvl(lvl.get(W + "ilvl"))
            if k is None:
                return None, f"a level's w:ilvl {lvl.get(W + 'ilvl')!r} is not a whole number 0–8"
            if k in levels:
                return None, f"level {k} is defined twice"
            ld, why = read_level(lvl, k)
            if why:
                return None, why
            levels[k] = ld
        return _Abstract(levels, _val(el.find(W + "numStyleLink")), _val(el.find(W + "styleLink"))), None

    @staticmethod
    def _read_num(el) -> Tuple[Optional[_Num], Optional[str]]:
        a = whole(_val(el.find(W + "abstractNumId")))
        if a is None:
            return None, f"its abstractNumId {_val(el.find(W + 'abstractNumId'))!r} is not a whole number"
        starts: Dict[int, int] = {}
        full: Dict[int, LevelDef] = {}
        ilvls: List[int] = []
        for o in el.findall(W + "lvlOverride"):
            k = _ilvl(o.get(W + "ilvl"))
            if k is None:
                return None, f"a level override's w:ilvl {o.get(W + 'ilvl')!r} is not a whole number 0–8"
            if k in ilvls:
                return None, f"level {k} is overridden twice"
            ilvls.append(k)
            so = o.find(W + "startOverride")
            if so is not None and so.get(W + "val") is not None:
                v = whole(so.get(W + "val"))
                if v is None:
                    return None, f"level {k}'s start override {so.get(W + 'val')!r} is not a whole number"
                starts[k] = v
            lvl = o.find(W + "lvl")
            if lvl is not None:
                full[k] = LevelDef(ilvl=k, pstyle=_val(lvl.find(W + "pStyle")), unsupported=OVERRIDE_REASON)
        return _Num(a, starts, full, ilvls), None

    @classmethod
    def load(cls, pkg: DocxPackage) -> "ListDefs":
        part = numbering_part(pkg)
        return cls(pkg.xml(part) if part else None, part)

    def with_extra(self, abstracts: Iterable[Any] = (), nums: Iterable[Any] = ()) -> "ListDefs":
        return ListDefs(self.root, self.part, self._extra + list(abstracts) + list(nums))

    # ---- queries ----
    def defined(self, num_id: Optional[int]) -> bool:
        return num_id in self.nums or num_id in self.unreadable_nums

    def problem(self, num_id: int) -> Optional[Tuple[str, str]]:
        """(issue, reason) when the paragraphs of list ``num_id`` cannot be evaluated at all, else None."""
        if num_id in self.unreadable_nums:
            return "unreadable", self.unreadable_nums[num_id]
        if num_id not in self.nums:
            return "unreadable", f"list {num_id} is not defined"
        a = self.nums[num_id].abstract_id
        if a in self.unreadable_abstracts:
            return "unreadable", f"list {num_id}: {self.unreadable_abstracts[a]}"
        if a not in self.abstracts:
            return "unreadable", f"list {num_id} names abstractNum {a}, which is not defined"
        if self.abstracts[a].num_style_link is not None:
            return "unsupported", (f"list {num_id} is defined through the numbering style "
                                   f"{self.abstracts[a].num_style_link!r} (w:numStyleLink), which is not modelled")
        return None

    def abstract_of(self, num_id: Optional[int]) -> Optional[int]:
        n = self.nums.get(num_id)
        return n.abstract_id if n is not None else None

    def nums_of_abstract(self, abs_id: int) -> List[int]:
        return [n for n in self._num_order if n in self.nums and self.nums[n].abstract_id == abs_id]

    def level(self, num_id: Optional[int], ilvl: int) -> Optional[LevelDef]:
        n = self.nums.get(num_id)
        if n is None:
            return None
        if ilvl in n.override_levels:
            return n.override_levels[ilvl]
        a = self.abstracts.get(n.abstract_id)
        return a.levels.get(ilvl) if a is not None else None

    def levels_of(self, num_id: int) -> Dict[int, LevelDef]:
        return {k: lv for k in range(9) for lv in [self.level(num_id, k)] if lv is not None}

    def start_override(self, num_id: Optional[int], ilvl: int) -> Optional[int]:
        n = self.nums.get(num_id)
        return n.start_overrides.get(ilvl) if n is not None else None

    def has_overrides(self, num_id: Optional[int]) -> bool:
        n = self.nums.get(num_id)
        return bool(n.overrides) if n is not None else False

    def is_override(self, num_id: Optional[int], ilvl: int) -> bool:
        """Whether level ``ilvl`` of list ``num_id`` is a full override level (unsupported)."""
        n = self.nums.get(num_id)
        return n is not None and ilvl in n.override_levels


# ---- a paragraph's numbering -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Numbering:
    num_id: Optional[int]
    ilvl: Optional[int]
    source: str                         # direct | style | override
    style: Optional[str] = None         # the style whose w:numPr gave the list (source "style")
    dangling: bool = False
    unreadable: Optional[str] = None


def paragraph_style(p, sheet) -> Optional[str]:
    """``w:pStyle``'s value (an empty or missing one names no style), else the default paragraph style."""
    ps = p.find(f"{W}pPr/{W}pStyle")
    if ps is None:
        return sheet.defaults.get("paragraph")
    return ps.get(W + "val") or None


def style_chain(sheet, sid: Optional[str]) -> List[Tuple[str, Any]]:
    """(id, w:style) of the style and its basedOn ancestors, nearest first, cycle-safe."""
    out: List[Tuple[str, Any]] = []
    seen = set()
    while sid and sid not in seen and sid in sheet.styles:
        seen.add(sid)
        st = sheet.styles[sid]
        out.append((sid, st))
        b = st.find(W + "basedOn")
        sid = b.get(W + "val") if b is not None else None
    return out


def _styled(sheet, sid: Optional[str]):
    """(s_num element, s_lvl element or None, s_style) — the first style of the chain whose own numPr holds a numId."""
    for s, st in style_chain(sheet, sid):
        numpr = st.find(f"{W}pPr/{W}numPr")
        if numpr is not None and numpr.find(W + "numId") is not None:
            return numpr.find(W + "numId"), numpr.find(W + "ilvl"), s
    return None


def linked_level(defs: ListDefs, num_id: int, style_id: Optional[str]) -> Optional[int]:
    """The lowest ilvl of list ``num_id`` whose level definition names ``style_id`` in its w:pStyle, else None."""
    for k in range(9):
        lv = defs.level(num_id, k)
        if lv is not None and style_id and lv.pstyle == style_id:
            return k
    return None


def _numbering(d_num, d_lvl, styled, defs: ListDefs) -> Optional[Numbering]:
    if not defs.present:
        return None
    if d_num is not None:
        source, s_style, num_el = "direct", None, d_num
    elif styled is not None:
        num_el, s_lvl_el, s_style = styled
        source = "style"
    else:
        return None
    n = whole(num_el.get(W + "val"))
    if n is None:
        who = "its" if source == "direct" else f"style {s_style}'s"
        return Numbering(None, None, source, s_style,
                         unreadable=f"{who} numbering id {num_el.get(W + 'val')!r} is not a whole number")
    if n == 0:
        return None
    if not defs.defined(n):
        return Numbering(n, None, source, s_style, dangling=True)
    lvl_el = d_lvl if d_lvl is not None else (styled[1] if source == "style" else None)
    if lvl_el is not None:
        k = _ilvl(lvl_el.get(W + "val"))
        if k is None:
            who = "its" if lvl_el is d_lvl else f"style {s_style}'s"
            return Numbering(n, None, source, s_style,
                             unreadable=f"{who} list level {lvl_el.get(W + 'val')!r} is not a whole number 0–8")
    elif source == "style":
        k = linked_level(defs, n, s_style)
        k = 0 if k is None else k
    else:
        k = 0
    return Numbering(n, k, source, s_style)


def paragraph_numbering(p, sheet, defs: ListDefs) -> Optional[Numbering]:
    numpr = p.find(f"{W}pPr/{W}numPr")
    d_num = numpr.find(W + "numId") if numpr is not None else None
    d_lvl = numpr.find(W + "ilvl") if numpr is not None else None
    styled = _styled(sheet, paragraph_style(p, sheet)) if d_num is None else None
    return _numbering(d_num, d_lvl, styled, defs)


def style_numbering(style_id: Optional[str], sheet, defs: ListDefs) -> Optional[Numbering]:
    """The numbering a paragraph of this style without its own numPr gets (steps 2–4 on the style chain alone)."""
    return _numbering(None, None, _styled(sheet, style_id), defs)


# ---- formats and the rendered number ---------------------------------------------------------------------------------

def _roman(n: int) -> str:
    out = []
    for v, s in _ROMAN:
        while n >= v:
            out.append(s)
            n -= v
    return "".join(out)


def format_value(fmt: str, n: int, text: Optional[str] = None) -> Optional[str]:
    """The counter value ``n`` shown in format ``fmt`` (``text``: the level's own text, shown as written by ``bullet``);
    None when the format is not supported or ``n`` is out of its range."""
    if fmt == "decimal":
        return str(n)
    if fmt == "decimalZero":
        return "0" + str(n) if 0 <= n <= 9 else str(n)
    if fmt in ("upperLetter", "lowerLetter"):
        if n < 1:
            return None
        letter = chr(ord("A") + (n - 1) % 26) * ((n - 1) // 26 + 1)
        return letter if fmt == "upperLetter" else letter.lower()
    if fmt in ("upperRoman", "lowerRoman"):
        if not 1 <= n <= 3999:
            return None
        r = _roman(n)
        return r if fmt == "upperRoman" else r.lower()
    if fmt == "bullet":
        return text or ""
    if fmt == "none":
        return ""
    return None


def render(text: str, legal: bool, value_of: Callable[[int], int],
           level_of: Callable[[int], Optional[LevelDef]]) -> Tuple[Optional[str], Optional[str]]:
    """(the rendered number, None) or (None, why it cannot be): ``text`` with each ``%j`` replaced by level j−1's value
    (``value_of``) in its format (``level_of``) — decimal when ``legal``."""
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            k = int(text[i + 1]) - 1
            ref = level_of(k)
            if ref is None:
                return None, f"its number text {text!r} names level {k}, which the list does not define"
            if ref.unsupported:
                return None, f"its number text names level {k}, which {ref.unsupported}"
            v = value_of(k)
            s = str(v) if legal else format_value(ref.fmt, v, ref.text)
            if s is None:
                return None, f"level {k}'s value {v} cannot be shown in its format {ref.fmt!r}"
            out.append(s)
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out), None


def skipped_level(text: str, has_value: Callable[[int], bool]) -> Optional[int]:
    """The first level a ``%j`` of ``text`` names that has no current value (``has_value``), else None."""
    i = 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            k = int(text[i + 1]) - 1
            if not has_value(k):
                return k
            i += 2
        else:
            i += 1
    return None


def has_restart_rule(defs: ListDefs, num_id: int) -> List[int]:
    """The levels of list ``num_id`` whose definition carries a ``w:lvlRestart`` value. The model reads it as Word does;
    LibreOffice ignores it, so the render proofs of those levels differ from Word — flag them."""
    return [k for k, lv in sorted(defs.levels_of(num_id).items()) if lv.restart is not None]


def restarts(restart: Optional[int], L: int, k: int) -> bool:
    """Whether level ``k`` (> L) starts again after a paragraph at level ``L`` (its ``w:lvlRestart``: ``restart``)."""
    if restart is None:
        return True
    if restart == 0:
        return False
    if restart - 1 >= k:
        return True                                     # ignored: as none
    return L <= restart - 1


def start_of(defs: ListDefs, num_id: int, ilvl: int) -> int:
    """The value level ``ilvl`` of list ``num_id`` starts at: the num's startOverride, else the level's start, else 0."""
    so = defs.start_override(num_id, ilvl)
    if so is not None:
        return so
    lv = defs.level(num_id, ilvl)
    return lv.start if lv is not None else 0


# ---- counting --------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class NumberedPar:
    index: int
    num_id: Optional[int]
    ilvl: Optional[int]
    counters: Optional[Tuple[int, ...]]
    text: Optional[str]
    suff: Optional[str]
    fmt: Optional[str]
    not_evaluated: Optional[str] = None
    issue: Optional[str] = None
    abstract_id: Optional[int] = None
    source: str = "direct"
    style: Optional[str] = None

    @property
    def evaluated(self) -> bool:
        return self.not_evaluated is None


def counted(p) -> bool:
    """Not in a text box, its mark not a pending deletion (hidden paragraphs count)."""
    q = p.getparent()
    while q is not None:
        if q.tag == W + "txbxContent":
            return False
        q = q.getparent()
    mark = p.find(f"{W}pPr/{W}rPr")
    return mark is None or (mark.find(W + "del") is None and mark.find(W + "moveFrom") is None)


def _numbering_at(index: int, p, sheet, defs: ListDefs, override) -> Optional[Numbering]:
    if override is not None and index in override:
        o = override[index]
        if o is None or not defs.present or o[0] == 0:
            return None
        return Numbering(o[0], o[1], "override", None, dangling=not defs.defined(o[0]))
    return paragraph_numbering(p, sheet, defs)


def count(root, sheet, defs: ListDefs, override: Optional[Mapping[int, Optional[Tuple[int, int]]]] = None
          ) -> List[NumberedPar]:
    """Every counted, numbered (not dangling) paragraph of ``root`` with its counters and rendered number (§0.2)."""
    found: List[Tuple[int, Numbering]] = []
    for index, p in enumerate(root.iter(W + "p")):
        if not counted(p):
            continue
        nb = _numbering_at(index, p, sheet, defs, override)
        if nb is not None and not nb.dangling:
            found.append((index, nb))
    users: Dict[int, set] = {}                          # abstractNum → the nums numbering a counted paragraph
    for _, nb in found:
        a = defs.abstract_of(nb.num_id)
        if a is not None:
            users.setdefault(a, set()).add(nb.num_id)

    out: List[NumberedPar] = []
    states: Dict[int, Dict[int, int]] = {}
    poisoned: Dict[int, str] = {}
    poisoned_all: Optional[str] = None
    for index, nb in found:
        n, L = nb.num_id, nb.ilvl
        a = defs.abstract_of(n)

        def par(counters=None, text=None, suff=None, fmt=None, why=None, issue=None):
            return NumberedPar(index, n, L, counters, text, suff, fmt, why, issue, a, nb.source, nb.style)
        if nb.unreadable is not None:
            out.append(par(why=nb.unreadable, issue="unreadable"))
            if n is None:
                poisoned_all = poisoned_all or f"an earlier paragraph's list cannot be read ({nb.unreadable})"
            else:
                poisoned.setdefault(n, f"an earlier paragraph of list {n} cannot be read ({nb.unreadable})")
            continue
        early = poisoned_all or poisoned.get(n)
        if early:
            out.append(par(why=early, issue="earlier"))
            continue
        prob = defs.problem(n)
        if prob:
            out.append(par(why=prob[1], issue=prob[0]))
            continue
        state = states.setdefault(n, {})
        for k in sorted(state):
            if k > L and restarts(_restart(defs, n, k), L, k):
                del state[k]
        state[L] = start_of(defs, n, L) if L not in state else state[L] + 1
        lv = defs.level(n, L)
        own = lv is not None and not defs.is_override(n, L)
        suff, fmt = (lv.suff, lv.fmt) if own else (None, None)
        if len(users.get(a, ())) > 1:
            others = sorted(m for m in users[a] if m != n)
            out.append(par(suff=suff, fmt=fmt, issue="shared",
                           why=f"{SHARED}: list {n} shares abstractNum {a} with list(s) {others}, which number other "
                               f"paragraphs — Word continues one count across them, LibreOffice keeps them apart"))
            continue
        over = [k for k in range(L + 1) if defs.is_override(n, k)]
        if over:
            out.append(par(suff=suff, fmt=fmt, issue="override", why=f"level {over[0]} of list {n} {OVERRIDE_REASON}"))
            continue
        counters = tuple(state[k] if k in state else start_of(defs, n, k) for k in range(L + 1))
        if lv is None:
            out.append(par(counters=counters, text=""))
            continue
        if lv.unsupported:
            out.append(par(counters=counters, suff=suff, fmt=fmt, issue="unsupported",
                           why=f"level {L} of list {n} {lv.unsupported}"))
            continue
        if lv.text is None:
            out.append(par(counters=counters, text="", suff=suff, fmt=fmt))
            continue
        text, why = render(lv.text, lv.legal, lambda k: state[k] if k in state else start_of(defs, n, k),
                           lambda k: defs.level(n, k))
        if why:
            out.append(par(counters=counters, suff=suff, fmt=fmt, why=f"list {n} level {L}: {why}", issue="unsupported"))
            continue
        gap = skipped_level(lv.text, lambda k: k in state)
        if gap is not None:
            out.append(par(counters=counters, suff=suff, fmt=fmt, issue="skipped",
                           why=f"{SKIPPED}: the number text {lv.text!r} of list {n} level {L} names level {gap}, which "
                               f"has no value yet (never reached, or restarted) — Word shows 0 there, LibreOffice its "
                               f"start"))
            continue
        out.append(par(counters=counters, text=text, suff=suff, fmt=fmt))
    return out


def _restart(defs: ListDefs, num_id: int, k: int) -> Optional[int]:
    lv = defs.level(num_id, k)
    return lv.restart if lv is not None else None


def main_abstract(pars: Sequence[NumberedPar]) -> Optional[int]:
    """The abstractNum numbering the most counted paragraphs at a non-bullet level; a tie → the first such paragraph's."""
    tally: Dict[int, int] = {}
    first: Dict[int, int] = {}
    for i, p in enumerate(pars):
        if p.abstract_id is None or p.fmt is None or p.fmt == "bullet":
            continue
        tally[p.abstract_id] = tally.get(p.abstract_id, 0) + 1
        first.setdefault(p.abstract_id, i)
    if not tally:
        return None
    best = max(tally.values())
    return min((first[a], a) for a, c in tally.items() if c == best)[1]


def list_is_clean(defs: ListDefs, num_id: int, counted_pars: Sequence[NumberedPar]) -> Optional[str]:
    """Why list ``num_id`` cannot be the list an op numbers paragraphs with (None: it can): unreadable or undefined, an
    instance problem, any ``w:lvlOverride`` on it, its abstractNum shared with another num numbering a counted paragraph
    of ``counted_pars``, an unsupported level."""
    prob = defs.problem(num_id)
    if prob:
        return prob[1]
    if defs.has_overrides(num_id):
        return f"list {num_id} overrides levels of its definition (w:lvlOverride)"
    a = defs.abstract_of(num_id)
    others = sorted({p.num_id for p in counted_pars if p.abstract_id == a and p.num_id is not None
                     and p.num_id != num_id})
    if others:
        return (f"{SHARED}: list {num_id} shares abstractNum {a} with list(s) {others}, which number paragraphs of this "
                f"file — Word and LibreOffice number them differently")
    for k, lv in sorted(defs.abstracts[a].levels.items()):
        if lv.unsupported:
            return f"level {k} of list {num_id} {lv.unsupported}"
    return None


def visible_line(number_text: Optional[str], run_text: str, suff: Optional[str] = None) -> str:
    """What a reader sees of a paragraph (§2.4): the number, its suffix and the run text (or the run text alone when
    ``number_text`` is None); runs of spaces, tabs and NBSPs collapsed to one space; leading/trailing spaces stripped."""
    s = run_text if number_text is None else number_text + SUFFIXES.get(suff or "", "") + run_text
    return re.sub("[ \t ]+", " ", s).strip(" ")


# ---- the scheme ------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SchemeLevel:
    style: str
    num_id: int
    ilvl: int
    level: LevelDef
    pattern: str


@dataclass
class Scheme:
    num_id: int
    abstract_id: Optional[int]
    levels: List[SchemeLevel]
    level_defs: Dict[int, LevelDef] = field(default_factory=dict)


def pattern_of(level_defs: Mapping[int, LevelDef], k: int) -> Tuple[Optional[str], Optional[str]]:
    """(the regular expression of level ``k``'s typed number, None) or (None, why there is none)."""
    lv = level_defs.get(k)
    if lv is None:
        return None, f"level {k} is not defined"
    if lv.text is None:
        return None, f"level {k} has no number text"
    parts: List[str] = []
    literal: List[str] = []
    text, i = lv.text, 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            j = int(text[i + 1]) - 1
            ref = level_defs.get(j)
            if ref is None:
                return None, f"level {k}'s number text names level {j}, which the list does not define"
            if ref.unsupported:
                return None, f"level {k}'s number text names level {j}, which {ref.unsupported}"
            if lv.legal:
                pat = FORMAT_PATTERNS["decimal"]
            elif ref.fmt == "bullet":
                return None, f"level {k}'s number text names level {j}, a bullet: no typed number stands for it"
            else:
                pat = FORMAT_PATTERNS[ref.fmt]
            parts.append(re.escape("".join(literal)))
            literal = []
            parts.append(pat)
            i += 2
        else:
            literal.append(text[i])
            i += 1
    parts.append(re.escape("".join(literal)))
    return "".join(parts), None


def level_pattern(scheme: Scheme, k: int) -> str:
    return scheme.levels[k].pattern


def resolve_scheme(levels: Any, sheet, defs: ListDefs, where: str = "this file", code: str = "scheme") -> Scheme:
    """The scheme ``levels`` name in ``sheet``/``defs`` (§0.3), or ``JobException(code)``."""
    if not isinstance(levels, list) or not 1 <= len(levels) <= 9 or \
            not all(isinstance(x, str) and x.strip() for x in levels):
        raise JobException("levels must be a list of 1 to 9 paragraph styles (names or ids), level 0 first",
                           "bad-params")

    def no(msg: str, **details):
        raise JobException(f"{where}: {msg}", code, details)
    found: List[Tuple[str, Numbering]] = []
    for k, ref in enumerate(levels):
        sid = resolve_style(sheet.styles, ref, where)
        if sid is None:
            no(f"level {k}: there is no style {ref!r}", level=k, style=ref)
        if (sheet.styles[sid].get(W + "type") or "paragraph") != "paragraph":
            no(f"level {k}: {sid} is not a paragraph style", level=k, style=sid)
        nb = style_numbering(sid, sheet, defs)
        if nb is None:
            no(f"level {k}: style {sid} is not numbered", level=k, style=sid)
        if nb.unreadable:
            no(f"level {k}: style {sid}: {nb.unreadable}", level=k, style=sid)
        if nb.dangling:
            no(f"level {k}: style {sid} names list {nb.num_id}, which is not defined", level=k, style=sid)
        if found and nb.num_id != found[0][1].num_id:
            no(f"the levels are numbered by different lists: {found[0][0]} by list {found[0][1].num_id}, {sid} by "
               f"list {nb.num_id}", level=k, style=sid)
        if nb.ilvl != k:
            no(f"style {sid} numbers level {nb.ilvl} of its list, not level {k}", level=k, style=sid)
        found.append((sid, nb))
    num_id = found[0][1].num_id
    prob = defs.problem(num_id)
    if prob:
        no(prob[1], num_id=num_id)
    level_defs = defs.levels_of(num_id)
    out: List[SchemeLevel] = []
    for k, (sid, _) in enumerate(found):
        lv = level_defs.get(k)
        if lv is None:
            no(f"list {num_id} has no level {k} (style {sid})", level=k, style=sid)
        if lv.unsupported:
            no(f"level {k} of list {num_id} {lv.unsupported}", level=k, style=sid)
        if lv.text is None:
            no(f"level {k} of list {num_id} (style {sid}) has no number text", level=k, style=sid)
        if lv.fmt == "bullet":
            no(f"level {k} of list {num_id} (style {sid}) is a bullet, not a number", level=k, style=sid)
        pat, why = pattern_of(level_defs, k)
        if why:
            no(f"style {sid}: {why}", level=k, style=sid)
        out.append(SchemeLevel(style=sid, num_id=num_id, ilvl=k, level=lv, pattern=pat))
    return Scheme(num_id=num_id, abstract_id=defs.abstract_of(num_id), levels=out, level_defs=level_defs)


# ---- typed numbers ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TypedNumber:
    level: Optional[int]              # None when several levels match
    token: str                        # the number as typed (the first match's when ambiguous)
    token_start: int
    end: int                          # the end of the prefix: position 0 to the end of the whitespace after the number
    levels: Tuple[int, ...]

    @property
    def ambiguous(self) -> bool:
        return len(self.levels) > 1


def typed_number(run_text: str, scheme: Scheme) -> Optional[TypedNumber]:
    hits: List[Tuple[int, Any]] = []
    for sl in scheme.levels:
        m = re.match("[ \t]*(" + sl.pattern + ")(?:[ \t ]+|\\Z)", run_text)
        if m is not None and m.group(1):
            hits.append((sl.ilvl, m))
    if not hits:
        return None
    k, m = hits[0]
    return TypedNumber(level=k if len(hits) == 1 else None, token=m.group(1), token_start=m.start(1), end=m.end(),
                       levels=tuple(h[0] for h in hits))


# ---- what a count reads (rule 9; design §6) --------------------------------------------------------------------------

def count_reads(pkg: DocxPackage, sheet, pars: Sequence[NumberedPar], extra_styles: Iterable[str] = ()
                ) -> List[Tuple[str, str, str]]:
    """The reads an op whose prediction counts the main part's numbers must declare (design §6): the numbering part
    whole; the whole-member soft family reads of ``pStyle`` and ``numPr`` over the main part (any paragraph restyled or
    given numbering by another op changes the count); ``verify:numPr`` of every counted numbered paragraph (a tracked
    deletion of one is a ``text`` change of it — a conflict; untracked insertions and deletions are whole-member ``text``
    changes, rule 1); and ``verify:pPr`` of every style on the chains of those paragraphs and of ``extra_styles`` (the
    scheme's) — a style's numbering is in its pPr: a replaced, removed or re-spaced style is a conflict, a font change of
    it (``rPr``) is not. ``pars`` is ``count``'s result over the SOURCE; ``sheet`` the source's ``StyleSheet``."""
    main = pkg.main_part()
    out: List[Tuple[str, str, str]] = []
    part = numbering_part(pkg)
    if part is not None:
        out.append((part, "*", "verify"))
    out += [(main, "*", "verify-props:pStyle"), (main, "*", "verify-props:numPr")]
    every = list(pkg.xml(main).iter(W + "p"))
    styles: List[str] = []
    for np_ in pars:                     # its own style chain: its numbering may come from any level of it
        out.append((main, str(np_.index), "verify:numPr"))
        styles += [sid for sid, _ in style_chain(sheet, paragraph_style(every[np_.index], sheet))]
    for ref in extra_styles:
        styles += [sid for sid, _ in style_chain(sheet, ref)]
    if sheet.part is not None:
        out += [(sheet.part, f"style:{sid}", "verify:pPr") for sid in dict.fromkeys(styles)]
    return out
