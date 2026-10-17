"""Op: audit_numbering — numbering gaps, duplicates and empty numbered paragraphs that use up a number (built-in batch 5,
item 36 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §4). An audit: it reports, and changes something only when asked
(``unnumber_empty``).

Report only (the default): the plan changes nothing — no intended part, no assertion: V1 and the residual prove the
output is the source — and carries its findings as ``flags`` (the preview and the report's "Flagged for review"), one per
finding, naming the paragraph by its text (clipped): the list-level findings first (they are caveats about everything
after them), then the paragraphs' in document order, at most ``MAX_FLAGS`` and then how many more; a note sums up (lists,
numbered paragraphs, typed numbers checked, findings by kind). The main part only (numbering is counted there, design
§0.2); the counts are the shared list model's (``ops/lists.py``).

Findings (``audit``):

* list-level: **not evaluated** — paragraphs the model does not predict, with its reason, one finding per list and reason
  (a shared definition named once for all its lists); **restart rule** — a list numbering counted paragraphs whose levels
  carry ``w:lvlRestart`` (``lists.has_restart_rule``: LibreOffice ignores it, so the proof PDFs may number those levels
  differently from Word); **linked level** — a list whose counted paragraphs take their level from a LINKED level
  (``NumberedPar.linked``: Word follows the link, LibreOffice shows level 0);
* **empty numbered paragraph** (``empty_numbered``) — a counted, evaluated paragraph showing a number (a non-empty rendered
  number, not a ``bullet``) whose content is blank (``blank_content``: only ``w:pPr``, ``w:proofErr``, bookmarks, and runs
  of ``w:rPr`` / ``w:lastRenderedPageBreak`` / ``w:t`` of spaces and no-break spaces — remove_empty_paragraphs' content
  rule, bookmarks allowed as the design says; anything else, a revision included, is content). Named with the previous
  BODY paragraph (not one inside a text box) that has text;
* **gap / duplicate / goes back / starts at / wrong part** in the **visible sequence** of TYPED numbers (``walk``). Every
  unnumbered counted paragraph outside a table cell whose run text starts with a typed number (``typed_shape``, the
  generic grammar below) is judged; the MAIN list's evaluated automatic numbers (``lists.main_abstract``; ``level_shape``:
  the level's text with each ``%j`` its level's class ``<1>`` decimal, decimalZero or legal, ``<A>``/``<a>`` letters,
  ``<I>``/``<i>`` roman, ``none`` dropped; the value is the counter of the last ``%j``) are CONTEXT — they open and close
  shapes in the walk but are never judged themselves: their counts are the model's, restart rules included. Each number
  has a **shape** (``PART <1>``, ``<1>.<1>``, ``<A>.`` …; a keyword shape never keeps the dash after its number, so
  "PART 1 -" and "PART 2" are one sequence), a **value** (its last component) and its **parts** (every component).
  Walking in document order with a stack of open shapes: a shape already open closes every deeper one and its value must
  be the previous + 1 (greater → gap, equal → duplicate, smaller → goes back); any other shape — new, or closed since by a
  shallower one — opens on top and must start at 1 (else "starts at"); a number of several components must agree with
  its context: every component but the last equals the parts of the nearest open shape below it with one component fewer
  ("2.3" under PART 1 → wrong part). Hidden paragraphs (``hidden`` below) and context take part silently. A single letter
  that is also a roman numeral (``I.``, ``v)`` …) reads as roman when it continues an open roman sequence of that shape,
  as a letter when it continues an open letter sequence, as roman 1 when it is ``I`` and no letter sequence of its shape
  is open (it starts a roman list), else as a letter;
* **typed number on a numbered paragraph** (double numbering): a numbered paragraph (not a bullet) whose run text starts
  with a typed number (``typed_shape``) that is followed by a tab or two or more spaces, or that equals the paragraph's
  own rendered number and is not followed by another typed number (a run of initials, "A. O. Smith Corporation.", is a
  name);
* **hidden numbered paragraph**: a counted numbered paragraph whose paragraph mark is hidden (``markup.Look.mark_hidden``:
  the number takes the mark's formatting) — it uses a number without showing it;
* **dangling numId**: a counted paragraph naming a list ``numbering.xml`` does not define (Word shows no number).

**Typed grammar** (advisory, transform side only — the audit changes nothing by it, so it has no verifier twin): on a
paragraph's run text (batch 1's), after ``[ \\t]*``: ``PART n`` / ``ARTICLE n`` (upper case; an optional dash after it
belongs to the token, not the shape), ``n.n…`` (shape ``<1>.<1>``, a trailing dot kept), ``n.`` ``n)`` ``(n)``, roman
numerals of two or more letters ``II.`` ``ii)`` ``(iv)`` (a valid numeral), single letters ``A.`` ``a)`` ``(a)`` (a
single roman letter carries both readings, above); then a space, tab, no-break space or the end. The value is the last
component.

**unnumber_empty: true** — each empty numbered paragraph's own ``w:numPr`` becomes exactly
``<w:numPr><w:numId w:val="0"/></w:numPr>`` (inserted in ``PPR_ORDER`` when it has none, a ``w:pPr`` created first in the
paragraph): numbering off, the paragraph and its spacing stay. Plain unless ``tracked`` (then one ``w:pPrChange`` per
paragraph, its snapshot the source pPr without rPr, sectPr, pPrChange; a paragraph already carrying one refuses
``tracked-format``, plain flags it). A numPr holding a pending numbering revision (``w:numberingChange``, ``w:ins``)
refuses ``tracked-format``, any other child but ``w:ilvl``/``w:numId`` ``structure``. The later numbers of its list move
up: predicted (``lists.count`` with those paragraphs unnumbered) and flagged, with the cross-reference fields and written
references their old numbers leave stale (``numbering_flags``) and the linked levels LibreOffice ignores. Verified by V10's
``unnumbered_empty`` (verify/numberingaudit.py).

Touches (rule 9; design §6): ``lists.count_reads`` (the numbering part whole, the main part's ``pStyle``/``numPr``
families, ``verify:numPr`` of every counted numbered paragraph, the chain styles' ``verify:pPr``), ``verify:text`` of
every paragraph whose typed number the walk read; with the fix ``(main, bi, "pPr")`` and ``"numPr"`` per changed
paragraph and ``(main, "scope", "verify")``. Layout: ``source`` (report only), ``none`` (the fix: numbers move).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, RevisionStamp, file_uses_date_utc, insert_in_order, max_revision_id, paragraph_ppr,
                     property_change)
from . import register
from . import lists
from .base import JobException, Op, PlanFragment
from .hidden import content_runs
from .markup import Look
from .numbering_flags import stale_fields, stale_fields_flag, written_references, written_references_flag
from .replace_text import default_author
from .selectors import StyleSheet
from .textedit import run_text, shown

MAX_FLAGS = 30
LABEL = 50
PARAM_KEYS = {"unnumber_empty", "tracked", "author"}
BLANK = "  "
INERT = (W + "pPr", W + "proofErr", W + "bookmarkStart", W + "bookmarkEnd")
RUN_INERT = (W + "rPr", W + "lastRenderedPageBreak")
NUMPR_KEEPS = (W + "ilvl", W + "numId")
CLASSES = {"decimal": "1", "decimalZero": "1", "upperLetter": "A", "lowerLetter": "a", "upperRoman": "I",
           "lowerRoman": "i"}
LAYOUT_REASON = "an empty paragraph loses its number and the later numbers of its list move up; text may reflow"

_SP = "[ \t ]"
_TAIL = f"(?={_SP}|$)"
_LEAD = "[ \t]*"
_KEYWORD = re.compile(rf"{_LEAD}(PART|ARTICLE){_SP}+([0-9]+)(?:{_SP}*([-–—]))?{_TAIL}")
_DOTTED = re.compile(rf"{_LEAD}([0-9]+(?:\.[0-9]+)+)(\.?){_TAIL}")
_NUM = re.compile(rf"{_LEAD}([0-9]+)([.)]){_TAIL}")
_PNUM = re.compile(rf"{_LEAD}\(([0-9]+)\){_TAIL}")
_ROMAN = re.compile(rf"{_LEAD}([IVXLCDM]{{2,}}|[ivxlcdm]{{2,}})([.)]){_TAIL}")
_PROMAN = re.compile(rf"{_LEAD}\(([IVXLCDM]{{2,}}|[ivxlcdm]{{2,}})\){_TAIL}")
_LETTER = re.compile(rf"{_LEAD}([A-Za-z])([.)]){_TAIL}")
_PLETTER = re.compile(rf"{_LEAD}\(([A-Za-z])\){_TAIL}")
_KEYWORD_SHAPE = re.compile(r"^([A-Z]+ <[1AaIi]>)\s*[-–—]$")


# ---- the typed grammar and the shapes ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Shaped:
    shape: str
    value: int
    token: str                          # the number as shown or typed
    parts: Tuple[int, ...] = ()         # every component's value (the last is ``value``)
    end: int = 0                        # typed: where the token ends in the run text
    alt: Optional["Shaped"] = None      # a single roman letter: its roman reading (this one is the letter's)


_ROMANS: Dict[str, int] = {}


def _roman_value(s: str) -> Optional[int]:
    """The value of a roman numeral written as ``lists.format_value`` writes it (a valid numeral), else None."""
    if not _ROMANS:
        _ROMANS.update({lists.format_value("upperRoman", n): n for n in range(1, 4000)})
    return _ROMANS.get(s.upper())


def typed_shape(text: str) -> Optional[Shaped]:
    """The typed number at the start of a paragraph's run text by the generic grammar (module docstring), else None."""
    m = _KEYWORD.match(text)
    if m:
        n = int(m.group(2))
        return Shaped(f"{m.group(1)} <1>", n, m.group(0).strip(" \t"), (n,), m.end())
    m = _DOTTED.match(text)
    if m:
        parts = tuple(int(x) for x in m.group(1).split("."))
        return Shaped(".".join("<1>" for _ in parts) + m.group(2), parts[-1], m.group(0).strip(" \t"), parts, m.end())
    m = _NUM.match(text)
    if m:
        n = int(m.group(1))
        return Shaped(f"<1>{m.group(2)}", n, m.group(0).strip(" \t"), (n,), m.end())
    m = _PNUM.match(text)
    if m:
        n = int(m.group(1))
        return Shaped("(<1>)", n, m.group(0).strip(" \t"), (n,), m.end())
    for rx, wrap in ((_ROMAN, False), (_PROMAN, True)):
        m = rx.match(text)
        if m and _roman_value(m.group(1)) is not None:
            n = _roman_value(m.group(1))
            cls = "<I>" if m.group(1).isupper() else "<i>"
            return Shaped(f"({cls})" if wrap else cls + m.group(2), n, m.group(0).strip(" \t"), (n,), m.end())
    for rx, wrap in ((_LETTER, False), (_PLETTER, True)):
        m = rx.match(text)
        if m:
            c = m.group(1)
            cls, rcls = ("<A>", "<I>") if c.isupper() else ("<a>", "<i>")
            close = "" if wrap else m.group(2)
            n = ord(c.upper()) - ord("A") + 1
            token = m.group(0).strip(" \t")
            r = _roman_value(c)
            alt = (Shaped(f"({rcls})" if wrap else rcls + close, r, token, (r,), m.end()) if r is not None else None)
            return Shaped(f"({cls})" if wrap else cls + close, n, token, (n,), m.end(), alt)
    return None


def level_shape(defs: lists.ListDefs, np_: lists.NumberedPar) -> Optional[Shaped]:
    """The shape, value and parts of an evaluated paragraph's rendered number (module docstring), else None."""
    lv = defs.level(np_.num_id, np_.ilvl)
    if lv is None or lv.text is None or np_.counters is None or not np_.text:
        return None
    out: List[str] = []
    parts: List[int] = []
    text, i = lv.text, 0
    while i < len(text):
        if text[i] == "%" and i + 1 < len(text) and text[i + 1] in "123456789":
            k = int(text[i + 1]) - 1
            ref = defs.level(np_.num_id, k)
            i += 2
            if ref is not None and ref.fmt == "none" and not lv.legal:
                continue
            cls = "1" if lv.legal else (CLASSES.get(ref.fmt) if ref is not None else None)
            if cls is None or k >= len(np_.counters):
                return None
            out.append(f"<{cls}>")
            parts.append(np_.counters[k])
        else:
            out.append("-" if text[i] in "–—" else text[i])
            i += 1
    if not parts:
        return None
    shape = " ".join("".join(out).split())
    m = _KEYWORD_SHAPE.match(shape)
    return Shaped(m.group(1) if m else shape, parts[-1], np_.text, tuple(parts))


# ---- content and visibility -------------------------------------------------------------------------------------------

def blank_content(p) -> bool:
    """Only w:pPr, proofing marks, bookmarks and runs of w:rPr / w:lastRenderedPageBreak / w:t of spaces and no-break
    spaces (remove_empty_paragraphs' content rule, bookmarks allowed)."""
    for c in p:
        if not isinstance(c.tag, str) or c.tag in INERT:
            continue
        if c.tag != W + "r":
            return False
        for k in c:
            if not isinstance(k.tag, str) or k.tag in RUN_INERT:
                continue
            if k.tag == W + "t" and not (k.text or "").strip(BLANK):
                continue
            return False
    return True


def number_hidden(look: Look, p) -> bool:
    """The paragraph mark is hidden (the number takes the mark's formatting); an ambiguous toggle counts as shown."""
    return look.mark_hidden(p) is True


def paragraph_hidden(look: Look, p) -> bool:
    """Batch 2's hidden paragraph: the mark hidden and every content run hidden."""
    return number_hidden(look, p) and all(look.hidden(r.find(W + "rPr"), p) is True for r in content_runs(p))


def empty_numbered(np_: lists.NumberedPar, p) -> bool:
    return np_.evaluated and bool(np_.text) and np_.fmt != "bullet" and blank_content(p)


def in_table(p) -> bool:
    return any(a.tag == W + "tc" for a in p.iterancestors())


def in_text_box(p) -> bool:
    return any(a.tag == W + "txbxContent" for a in p.iterancestors())


def double_numbered(text: str, typed: Shaped, rendered: Optional[str]) -> bool:
    """A typed number on a numbered paragraph is double numbering when a tab or two or more spaces follow it, or when it
    is the paragraph's own rendered number and no further typed number follows (a run of initials is a name)."""
    rest = text[typed.end:]
    if rest.startswith("\t") or re.match("[  ]{2,}", rest):
        return True
    if rendered is not None and typed.token.strip() == rendered.strip():
        return typed_shape(rest.lstrip(" \t ")) is None
    return False


# ---- the walk ---------------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    index: int
    shaped: Shaped
    label: str
    hidden: bool = False
    context: bool = False               # an automatic number: opens and closes shapes, never judged


@dataclass(frozen=True)
class Finding:
    kind: str     # empty | gap | duplicate | back | start | part | double | hidden | dangling | not-evaluated | restart | linked
    index: Optional[int]
    text: str


def _reading(items: Sequence[Item], k: int, stack: List[str], last: Dict[str, Item]) -> Shaped:
    """Item k's reading: a single roman letter reads as roman when it continues an open roman sequence of its shape, as
    the letter when it continues an open letter sequence, as roman 1 when it is ``I``/``i`` and no letter sequence of its
    shape is open; else as the letter."""
    it = items[k]
    rom = it.shaped.alt
    if rom is None:
        return it.shaped
    if rom.shape in stack and last[rom.shape].shaped.value + 1 == rom.value:
        return rom
    if it.shaped.shape in stack and last[it.shaped.shape].shaped.value + 1 == it.shaped.value:
        return it.shaped
    if rom.value == 1 and it.shaped.shape not in stack:
        return rom                                  # an "I." that continues no letters starts a roman sequence
    return it.shaped


def walk(items: Sequence[Item]) -> List[Finding]:
    """The visible sequence's findings (module docstring): a stack of open shapes; an open shape closes every deeper one
    and must continue by one; any other shape opens on top and must start at 1; a number of several components must
    agree with the open shape below it with one component fewer. Context and hidden items move the sequence silently."""
    stack: List[str] = []
    last: Dict[str, Item] = {}
    out: List[Finding] = []
    for k, it0 in enumerate(items):
        sh = _reading(items, k, stack, last)
        it = replace(it0, shaped=sh)
        s, v = sh.shape, sh.value
        judged = not (it.hidden or it.context)
        if s in stack:
            del stack[stack.index(s) + 1:]
            prev = last[s]
            if judged and v != prev.shaped.value + 1:
                if v > prev.shaped.value + 1:
                    kind, what = "gap", f"shows {sh.token} after {prev.shaped.token} (‘{prev.label}’): a number is skipped"
                elif v == prev.shaped.value:
                    kind, what = "duplicate", f"shows {sh.token} again, as ‘{prev.label}’ before it"
                else:
                    kind, what = "back", f"shows {sh.token} after {prev.shaped.token} (‘{prev.label}’): the numbers go back"
                out.append(Finding(kind, it.index, f"numbering: ‘{it.label}’ {what}"))
        else:
            stack.append(s)
            if judged and v != 1:
                out.append(Finding("start", it.index, f"numbering: ‘{it.label}’ starts its sequence at {sh.token}"))
        if judged and len(sh.parts) > 1:
            outer = next((last[o] for o in reversed(stack[:-1]) if len(last[o].shaped.parts) == len(sh.parts) - 1),
                         None)
            if outer is not None and tuple(sh.parts[:-1]) != tuple(outer.shaped.parts):
                out.append(Finding("part", it.index, f"numbering: ‘{it.label}’ shows {sh.token} under "
                                                      f"{outer.shaped.token} (‘{outer.label}’): it belongs elsewhere"))
        last[s] = it
    return out


# ---- the audit --------------------------------------------------------------------------------------------------------

@dataclass
class Audit:
    pars: List[lists.NumberedPar]
    findings: List[Finding] = field(default_factory=list)
    empties: List[int] = field(default_factory=list)          # paragraph ordinals of the empty numbered paragraphs
    typed_read: List[int] = field(default_factory=list)       # paragraphs whose typed number the walk or a finding read
    lists_used: List[int] = field(default_factory=list)


def _label(p) -> str:
    t = shown(run_text(p))
    return (t[:LABEL] + "…") if len(t) > LABEL else t


def _body_before(every, i: int) -> Optional[str]:
    """The text of the nearest body paragraph (not inside a text box) before paragraph ``i`` that has text."""
    for j in range(i - 1, -1, -1):
        if not in_text_box(every[j]) and _label(every[j]):
            return _label(every[j])
    return None


def list_findings(defs: lists.ListDefs, pars: Sequence[lists.NumberedPar], every, lists_used) -> List[Finding]:
    """Not evaluated (per list and reason; a shared definition once), restart rules, linked levels."""
    out: List[Finding] = []
    grouped: Dict[Tuple[Any, str], List[lists.NumberedPar]] = {}
    for np_ in pars:
        if np_.evaluated:
            continue
        key = (("abstract", np_.abstract_id) if np_.issue == "shared" else ("list", np_.num_id), np_.issue)
        grouped.setdefault(key, []).append(np_)
    for (who, issue), group in grouped.items():
        first = group[0]
        if issue == "shared":
            nums = sorted({g.num_id for g in group})
            head = f"lists {nums} share one definition (abstractNum {first.abstract_id})"
        else:
            head = f"list {first.num_id}" if first.num_id is not None else "a list"
        out.append(Finding("not-evaluated", first.index,
                           f"numbering not evaluated: {head} — {len(group)} paragraph(s), first "
                           f"‘{_label(every[first.index]) or '(empty)'}’: {first.not_evaluated}"))
    for nid in lists_used:
        if not defs.defined(nid) or defs.problem(nid):
            continue
        levels = lists.has_restart_rule(defs, nid)
        if levels:
            first = next(p for p in pars if p.num_id == nid)
            out.append(Finding("restart", first.index,
                               f"numbering: list {nid} (first ‘{_label(every[first.index]) or '(empty)'}’) sets a "
                               f"restart rule (w:lvlRestart) on level(s) {levels}: LibreOffice ignores it, so the proof "
                               f"PDFs may number those levels differently from Word, which follows it"))
        linked = [p for p in pars if p.num_id == nid and p.linked]
        if linked:
            out.append(Finding("linked", linked[0].index,
                               f"numbering: {len(linked)} paragraph(s) of list {nid} take their level from a LINKED "
                               f"level (the style names the list without a level; the list's level names the style), "
                               f"first ‘{_label(every[linked[0].index]) or '(empty)'}’: Word follows the link, "
                               f"LibreOffice shows them at level 0, so the proof PDFs number them differently"))
    return out


def audit(pkg: DocxPackage, sheet: StyleSheet, defs: lists.ListDefs, root) -> Audit:
    every = list(root.iter(W + "p"))
    pars = lists.count(root, sheet, defs)
    res = Audit(pars=pars, lists_used=sorted({p.num_id for p in pars if p.num_id is not None}))
    look = Look(sheet)
    by_index = {p.index: p for p in pars}
    main = lists.main_abstract(pars)
    items: List[Item] = []
    per_par: List[Finding] = []
    for i, p in enumerate(every):
        if not lists.counted(p):
            continue
        np_ = by_index.get(i)
        if np_ is None:
            nb = lists.paragraph_numbering(p, sheet, defs)
            if nb is not None and nb.dangling:
                per_par.append(Finding("dangling", i, f"numbering: ‘{_label(p)}’ names list {nb.num_id}, which "
                                                      f"numbering.xml does not define — Word shows no number"))
            if in_table(p):
                continue                               # a typed "1.5" in a schedule's cell is a value, not a number
            typed = typed_shape(run_text(p))
            if typed is not None:
                res.typed_read.append(i)
                items.append(Item(i, typed, _label(p), paragraph_hidden(look, p)))
            continue
        label = _label(p) or "(empty)"
        hidden = number_hidden(look, p)
        if hidden:
            per_par.append(Finding("hidden", i, f"numbering: ‘{label}’" + (f" ({np_.text})" if np_.text else "")
                                               + " is hidden but uses up a number"))
        if np_.fmt != "bullet":
            text = run_text(p)
            typed = typed_shape(text)
            if typed is not None:
                res.typed_read.append(i)
                if double_numbered(text, typed, np_.text):
                    per_par.append(Finding("double", i, f"numbering: ‘{label}’ is numbered automatically"
                                                       + (f" ({np_.text})" if np_.text else "")
                                                       + f" and starts with a typed ‘{typed.token}’ — double numbering"))
        if empty_numbered(np_, p):
            res.empties.append(i)
            before = _body_before(every, i)
            per_par.append(Finding("empty", i, f"numbering: ‘{np_.text}’ is shown on an empty line"
                                               + (f" (after ‘{before}’)" if before else "") + " and uses up a number"))
        if np_.evaluated and np_.fmt != "bullet" and np_.abstract_id == main:
            shaped = level_shape(defs, np_)
            if shaped is not None:
                items.append(Item(i, shaped, label, hidden, context=True))
    per_par += walk(items)
    res.findings = (list_findings(defs, pars, every, res.lists_used)
                    + sorted(per_par, key=lambda f: (f.index, f.kind)))
    return res


def unnumbered_numpr():
    el = etree.Element(W + "numPr")
    etree.SubElement(el, W + "numId").set(W + "val", "0")
    return el


def unnumber(p, stamp: Optional[RevisionStamp], rev_id: int) -> None:
    """The paragraph's own w:numPr becomes ``<w:numPr><w:numId w:val="0"/></w:numPr>`` (tracked: one w:pPrChange)."""
    ppr = paragraph_ppr(p)
    chg = None
    if stamp is not None:
        snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
        chg = property_change("pPrChange", "pPr", snap, stamp, rev_id)
    old = ppr.find(W + "numPr")
    if old is not None:
        ppr.replace(old, unnumbered_numpr())
    else:
        insert_in_order(ppr, unnumbered_numpr(), PPR_ORDER)
    if chg is not None:
        ppr.append(chg)


def _clean_params(params: Dict[str, Any]) -> Tuple[bool, bool]:
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise JobException(f"audit_numbering: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                           "bad-params")
    fix = params.get("unnumber_empty", False)
    tracked = params.get("tracked", False)
    if not isinstance(fix, bool) or not isinstance(tracked, bool):
        raise JobException("unnumber_empty and tracked must be true or false", "bad-params")
    if tracked and not fix:
        raise JobException("'tracked' applies to the fix only (unnumber_empty: true); the audit itself changes nothing",
                           "bad-params")
    return fix, tracked


@register
class AuditNumbering(Op):
    name = "audit_numbering"
    mode = "formatting"
    description = ("Audit the numbering: gaps, duplicates and numbers that go back in what the reader sees (automatic "
                   "and typed numbers), empty numbered paragraphs that use up a number, typed numbers on automatically "
                   "numbered paragraphs (double numbering), hidden numbered paragraphs, lists numbering.xml does not "
                   "define, lists the engine cannot predict and restart rules LibreOffice ignores. Report only: the "
                   "findings are flagged for review and nothing changes — unless unnumber_empty is asked, which turns "
                   "the number off on each empty numbered paragraph (its later numbers move up).")
    params_schema = {"type": "object", "properties": {
        "unnumber_empty": {"type": "boolean", "default": False,
                           "description": "turn numbering off (w:numId 0) on every empty numbered paragraph; the later "
                                          "numbers of its list move up"},
        "tracked": {"type": "boolean", "default": False,
                    "description": "with unnumber_empty: one w:pPrChange per paragraph"},
        "author": {"type": "string"}}}
    select_schema = {"description": "none — the main document's numbering is audited"}
    verification_profile = {
        "V1": "report only: nothing changes; the fix: the main part only", "V2": "zero text diffs",
        "V10": "report only: no kind (the residual proves nothing changed); unnumbered_empty: the empty numbered "
               "paragraphs re-derived from the source, each one's pPr = the source's with numPr exactly numId 0 (and "
               "the pPrChange rules), every counted paragraph recounted on the output and equal to both predictions",
        "V7": "source (report only); none with the fix"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException(f"audit_numbering takes no selector keys, got {sorted(select)}", "bad-params")
        fix, tracked = _clean_params(params)
        author = params.get("author") or default_author()
        frag = self.fragment(select, {**params, "tracked": tracked} if fix else params)
        sheet = StyleSheet(pkg)
        defs = lists.ListDefs.load(pkg)
        main = pkg.main_part()
        root = pkg.xml(main)
        every = list(root.iter(W + "p"))
        res = audit(pkg, sheet, defs, root)
        frag.touches += lists.count_reads(pkg, sheet, res.pars)
        frag.touches += [(main, str(i), "verify:text") for i in res.typed_read]

        kinds: Dict[str, int] = {}
        for f in res.findings:
            kinds[f.kind] = kinds.get(f.kind, 0) + 1
        frag.flags += [f.text for f in res.findings[:MAX_FLAGS]]
        if len(res.findings) > MAX_FLAGS:
            frag.flags.append(f"numbering: … and {len(res.findings) - MAX_FLAGS} more finding(s) — fix these and run the "
                              f"audit again")
        evaluated = sum(1 for p in res.pars if p.evaluated)
        numbered = {p.index for p in res.pars}
        typed = sum(1 for i in res.typed_read if i not in numbered)
        frag.notes.append(f"numbering audit: {len(res.lists_used)} list(s) number {len(res.pars)} counted paragraph(s) "
                          f"({evaluated} evaluated); {typed} typed number(s) checked; {len(res.findings)} finding(s)"
                          + (": " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) if kinds else ""))
        if not fix:
            frag.notes.append("report only: nothing changes (unnumber_empty: true removes the number from each empty "
                              "numbered paragraph)")
            return frag

        # ---- the fix: numbering off on every empty numbered paragraph ----
        older = 0
        for i in res.empties:
            p = every[i]
            ppr = p.find(W + "pPr")
            numpr = ppr.find(W + "numPr") if ppr is not None else None
            if numpr is not None:
                pending = [c for c in numpr if c.tag in (W + "numberingChange", W + "ins")]
                if pending:
                    raise JobException(f"{main} paragraph {i}: its numbering carries a pending tracked change; resolve "
                                       f"it in Word first", "tracked-format", {"paragraph": i})
                other = [etree.QName(c).localname for c in numpr if isinstance(c.tag, str) and c.tag not in NUMPR_KEEPS]
                if other:
                    raise JobException(f"{main} paragraph {i}: its numbering holds {other}", "structure",
                                       {"paragraph": i})
            if ppr is not None and ppr.find(W + "pPrChange") is not None:
                if tracked:
                    raise JobException(f"{main} paragraph {i} already carries a pending tracked paragraph change "
                                       f"(w:pPrChange)", "tracked-format", {"paragraph": i})
                older += 1
        n = len(res.empties)
        after = lists.count(root, sheet, defs, {i: None for i in res.empties})
        was = {p.index: p for p in res.pars}
        moved = [(was[p.index], p) for p in after if p.index in was and was[p.index].text != p.text]
        frag.property_assertions.append({
            "kind": "unnumbered_empty", "part": main,
            "params": {k: params[k] for k in ("unnumber_empty", "tracked") if k in params},
            "author": author, "changed": list(res.empties),
            "predicted": [[p.index, p.num_id, p.ilvl, list(p.counters) if p.counters is not None else None, p.text,
                           p.issue] for p in after]})
        frag.transform_data = {"part": main, "paragraphs": list(res.empties), "tracked": tracked, "author": author}
        frag.touches.append((main, "scope", "verify"))
        if n:
            frag.intended_parts = [main]
            for i in res.empties:
                frag.touches += [(main, str(i), "pPr"), (main, str(i), "numPr")]
            frag.geometry_reference, frag.geometry_reason = "none", LAYOUT_REASON
            if tracked:
                frag.rev_delta["pPrChange"] = n
                frag.rev_ids_needed = n
                frag.rev_id_base = max_revision_id(pkg) + 1
                frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        frag.notes.append(f"{n} empty numbered paragraph(s) lose their number (w:numId 0: numbering off; the paragraph "
                          f"and its spacing stay); {'tracked (w:pPrChange)' if tracked else 'plain'}"
                          if n else "no empty numbered paragraph: nothing to change")
        for b, a in moved[:MAX_FLAGS]:
            frag.flags.append(f"numbering after the fix: ‘{_label(every[b.index]) or '(empty)'}’ {b.text} → "
                              f"{a.text if a.text is not None else '(not evaluated)'}")
        if len(moved) > MAX_FLAGS:
            frag.flags.append(f"numbering after the fix: … and {len(moved) - MAX_FLAGS} more paragraph(s) change number")
        changed = [b.index for b, _ in moved] + list(res.empties)
        for flag in (stale_fields_flag(stale_fields(root, every, changed), "paragraphs whose number the fix changes"),
                     written_references_flag(written_references(every, [(b.index, b.text, a.text) for b, a in moved]))):
            if flag:
                frag.flags.append("numbering after the fix: " + flag)
        if older:
            frag.flags.append(f"{older} changed paragraph(s) already carried a pending tracked paragraph change "
                              f"(w:pPrChange); reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td.get("paragraphs"):
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        root = pkg.xml(td["part"])
        every = list(root.iter(W + "p"))
        for i in td["paragraphs"]:
            unnumber(every[i], stamp, next_id)
            next_id += 1
        pkg.write_xml(td["part"], root)
