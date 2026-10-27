"""V10 kind ``numbering_labels`` — the label formatting of list levels a custom method names.
This module's own reading; nothing from ops/ (rule 1).

A custom method names list levels — a ``w:abstractNum`` by its ``w:abstractNumId`` and a ``w:ilvl``, or a ``w:num`` by
its ``w:numId`` and the ``w:ilvl`` of its one ``w:lvlOverride`` holding a ``w:lvl`` — and the label run properties it
writes there, from a closed list:

* ``fonts`` {slot: name} for the slots ``ascii``, ``hAnsi``, ``cs``, ``eastAsia``: ``w:rFonts/@w:<slot>`` is the name and
  the slot's theme attribute (``asciiTheme``, ``hAnsiTheme``, ``cstheme``, ``eastAsiaTheme``) is gone — a theme font
  overrides the name beside it, so only then does Word show the asserted font;
* ``size`` in points (a whole or half point, 1–1,638): ``w:sz`` and ``w:szCs`` its half-points;
* ``bold`` / ``italic`` true or false: ``w:b`` and ``w:bCs`` / ``w:i`` and ``w:iCs``, on (no ``w:val``) or ``w:val="0"``;
* ``color``: six hex digits or ``auto``: ``w:color`` with that ``w:val`` alone (its theme attributes gone).

Expected: the SOURCE numbering part — the one the main part's numbering relationship names, which must be the
assertion's and the output's — with each listed level's ``w:rPr`` holding its other children as they were and exactly
the written elements (once each, as written; ``EG_RPrBase`` is a choice in the schema, so their order inside the
``w:rPr`` is not compared; a level without one gains it where ``CT_Lvl`` puts it). Everything else of the part —
``w:start``, ``w:numFmt``, ``w:lvlText``, ``w:lvlRestart``, ``w:isLgl``, ``w:suff``, ``w:lvlJc``, the level's ``w:pPr``
and ``w:pStyle``, the ``w:num``/``w:abstractNum`` graph and every override — is compared whole, so every number Word
shows is the source's. The whole member is then proven (``Ctx.verified_members``).

The op must refuse: a level not uniquely defined or named twice, an unknown key or value, a picture label
(``w:lvlPicBulletId``), a font slot written over a symbol font (the glyph would change), no numbering part.
"""
from __future__ import annotations

import copy
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .font_numbering import V_PRESERVE, v_label_rows, v_level_element
from .lists import V_NUMBERING_REL
from .props import first_difference, sig
from .reader import W
from .xsd import sequence_order

_V_SLOTS = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "cs": "cstheme", "eastAsia": "eastAsiaTheme"}
_V_KEYS = {"fonts", "size", "bold", "italic", "color"}
_V_HEX = re.compile(r"[0-9A-Fa-f]{6}")
_V_COLOR_THEME = ("themeColor", "themeTint", "themeShade")


def _v_target(entry: Any) -> Tuple[str, int, int]:
    if not isinstance(entry, dict) or set(entry) - {"abstractNum", "num", "ilvl", "set"} or \
            ("abstractNum" in entry) == ("num" in entry):
        raise ValueError(f"numbering_labels: bad level {entry!r} (the op must refuse)")
    kind = "abstractNum" if "abstractNum" in entry else "num"
    owner, k = entry[kind], entry.get("ilvl")
    if type(owner) is not int or type(k) is not int or not 0 <= k <= 8:
        raise ValueError(f"numbering_labels: bad level {entry!r} (the op must refuse)")
    return kind, owner, k


def _v_elements(asked: Any) -> Tuple[Dict[str, str], List[Any]]:
    """(the fonts per slot, the other elements written) — ValueError for anything the op must refuse."""
    if not isinstance(asked, dict) or not asked or set(asked) - _V_KEYS:
        raise ValueError(f"numbering_labels: bad set {asked!r} (the op must refuse)")
    fonts = asked.get("fonts") or {}
    if "fonts" in asked and (not isinstance(fonts, dict) or not fonts or set(fonts) - set(_V_SLOTS)
                             or not all(isinstance(v, str) and v.strip() for v in fonts.values())):
        raise ValueError(f"numbering_labels: bad fonts {fonts!r} (the op must refuse)")
    els: List[Any] = []
    if "size" in asked:
        s = asked["size"]
        if type(s) not in (int, float) or not 1 <= s <= 1638 or (s * 2) != int(s * 2):
            raise ValueError(f"numbering_labels: bad size {s!r} (the op must refuse)")
        els += [etree.Element(W + n, {W + "val": str(int(s * 2))}) for n in ("sz", "szCs")]
    for key, names in (("bold", ("b", "bCs")), ("italic", ("i", "iCs"))):
        if key in asked:
            if not isinstance(asked[key], bool):
                raise ValueError(f"numbering_labels: bad {key} (the op must refuse)")
            els += [etree.Element(W + n, {} if asked[key] else {W + "val": "0"}) for n in names]
    if "color" in asked:
        c = asked["color"]
        if not (c == "auto" or (isinstance(c, str) and _V_HEX.fullmatch(c))):
            raise ValueError(f"numbering_labels: bad color {c!r} (the op must refuse)")
        els.append(etree.Element(W + "color", {W + "val": c if c == "auto" else c.upper()}))
    return dict(fonts), els


def _v_expected_rpr(lvl, fonts: Dict[str, str], els: List[Any]):
    """The level's expected w:rPr (the level gains one where CT_Lvl puts it)."""
    rpr = lvl.find(W + "rPr")
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        order = sequence_order("ISO-IEC29500-4_2016/wml.xsd", "CT_Lvl")
        at = next((i for i, c in enumerate(lvl) if c.tag in order and order.index(c.tag) > order.index(rpr.tag)),
                  len(lvl))
        lvl.insert(at, rpr)
    if fonts:
        rf = rpr.find(W + "rFonts")
        if rf is None:
            rf = etree.SubElement(rpr, W + "rFonts")
        for slot, name in fonts.items():
            if (rf.get(W + slot) or "").casefold() in V_PRESERVE:
                raise ValueError(f"numbering_labels: the label's {slot} font {rf.get(W + slot)!r} is a symbol font "
                                 f"(the glyph would change; the op must refuse)")
            rf.set(W + slot, name)
            rf.attrib.pop(W + _V_SLOTS[slot], None)
    for el in els:
        for old in rpr.findall(el.tag):
            rpr.remove(old)
        rpr.append(copy.deepcopy(el))
    return rpr


def _v_bag(rpr) -> List[Any]:
    return sorted(sig(c) for c in (rpr if rpr is not None else []) if isinstance(c.tag, str))


def _numbering_labels(ctx, a: Dict[str, Any], errs: List[str]) -> str:
    part, out_part = ctx.src.related(V_NUMBERING_REL), ctx.out.related(V_NUMBERING_REL)
    if part is None:
        raise ValueError("numbering_labels: the document has no numbering part (the op must refuse)")
    if part != out_part or part != a.get("numbering_part"):
        errs.append(f"numbering_labels: numbering part {part!r} → {out_part!r}; plan: {a.get('numbering_part')!r}")
        return "numbering_labels: numbering part mismatch"
    levels = a.get("levels")
    if not isinstance(levels, list) or not levels:
        raise ValueError("numbering_labels: no level named (the op must refuse)")
    expected = copy.deepcopy(ctx.src.xml(part))
    got = copy.deepcopy(ctx.out.xml(out_part))
    seen = set()
    for entry in levels:
        target = _v_target(entry)
        if target in seen:
            raise ValueError(f"numbering_labels: {target} named twice (the op must refuse)")
        seen.add(target)
        fonts, els = _v_elements(entry.get("set"))
        try:
            e_lvl = v_level_element(expected, target)
        except ValueError as e:
            raise ValueError(f"numbering_labels: {e} (the op must refuse)") from e
        if e_lvl.find(W + "lvlPicBulletId") is not None:
            raise ValueError(f"numbering_labels: {target} is a picture label (the op must refuse)")
        e_rpr = _v_expected_rpr(e_lvl, fonts, els)
        try:
            o_lvl = v_level_element(got, target)
        except ValueError as e:
            errs.append(f"numbering_labels: {target} in the output: {e}")
            continue
        o_rpr = o_lvl.find(W + "rPr")
        if _v_bag(o_rpr) != _v_bag(e_rpr):
            errs.append(f"numbering_labels: {target} label properties are {_v_bag(o_rpr)}, expected {_v_bag(e_rpr)}")
            continue
        if o_rpr.attrib != e_rpr.attrib:
            errs.append(f"numbering_labels: {target} w:rPr attributes {dict(o_rpr.attrib)} are not the source's")
            continue
        o_lvl.replace(o_rpr, copy.deepcopy(e_rpr))       # proven above whatever the order of its children
    if len(seen) != a.get("expected_levels", -1):
        errs.append(f"numbering_labels: {len(seen)} level(s) named, the plan predicted {a.get('expected_levels')}")
    if sig(expected) != sig(got):
        errs.append(f"numbering_labels: {part} differs beyond the named labels' properties: "
                    f"{first_difference(expected, got)}")
    ctx.verified_members.add(part)
    return f"numbering_labels: {len(seen)} list label level(s) formatted"


def v_label_fonts_effect(source, output, styles, fonts: Dict[str, str],
                         skip: Any = ()) -> Tuple[Optional[str], List[str]]:
    """What a whole-document font effect may leave in the numbering part, read from the SOURCE by this module alone:
    every used, readable list level whose label is not a symbol or picture bullet carries ``fonts`` (the slots named,
    their theme attributes gone), and nothing else in the part changed — the label change the font operations make
    (backlog P3: copies refused for an "unapproved package-member change" of the numbering part). Returns the part
    and the errors: none when the part is absent or untouched (a method may leave the labels alone), or when the
    change is exactly that; otherwise what differs. The part is never approved on a reading this module cannot make.
    A part in ``skip`` — one another approved effect's proof already owns whole — is neither read nor judged."""
    part = source.related(V_NUMBERING_REL)
    if part is None or output.related(V_NUMBERING_REL) != part or part in skip:
        return None, []
    if source.read(part) == output.read(part):
        return part, []
    try:
        rows = v_label_rows(source, styles, V_PRESERVE)
    except ValueError as e:
        return part, [f"the list labels cannot be read, so their change cannot be approved: {e}"]
    targets = sorted({tuple(row[3]) for row in rows if not row[5]})
    if not targets:
        return part, ["the numbering part changed, but no used list level takes the font"]
    levels = [{kind: owner, "ilvl": k, "set": {"fonts": dict(fonts)}} for kind, owner, k in targets]
    errs: List[str] = []
    try:
        _numbering_labels(SimpleNamespace(src=source, out=output, verified_members=set()),
                          {"numbering_part": part, "levels": levels, "expected_levels": len(levels)}, errs)
    except ValueError as e:
        errs.append(str(e))
    return part, errs


NUMBERING_LABEL_KINDS: Dict[str, Any] = {"numbering_labels": _numbering_labels}
