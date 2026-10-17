"""The shared list (numbering) model — ops/lists.py ↔ verify/lists.py (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md
§0, §0.3, §2.1): the two sides compared on every paragraph of the numbering fixtures and of the batch-4 fixtures, and on
thousands of paragraphs of random list definitions; exact expected values (the architect's outline, the appendix list's
roman/letter/legal/restart cases, a full override level, a shared abstractNum, the default paragraph style, formats); the
scheme and its refusals; typed numbers; the planned-output simulation the conversion ops will run (an imported list,
``override``) and its visible-line guarantee; and wrong implementations SHARED by both sides, which only the
expected-value checks can catch."""
from __future__ import annotations

import copy
import io
import random
import zipfile
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

import pytest
from lxml import etree

from make_fixtures import (SCHEME_LEVELS, build_numbering, build_scheme_example, build_style_template, build_styles,
                           build_typed, patch_docx)
from spec_surgeon.ops import JobException
from spec_surgeon.ops import lists as tl
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.ops.textedit import run_text
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import lists as vl
from spec_surgeon.verify.edits import v_paragraph_text
from spec_surgeon.verify.reader import VPackage

W_NS = W[1:-1]
MAIN = "word/document.xml"
NUMBERING = "word/numbering.xml"

_CACHE: Dict[str, bytes] = {}


def fixture(name: str) -> bytes:
    if name not in _CACHE:
        _CACHE[name] = {"numbering": build_numbering, "numbering-clean": lambda: build_numbering(False),
                        "typed": build_typed, "typed-typo": lambda: build_typed(typo=True),
                        "typed-lead": lambda: build_typed(leading_tab=True),
                        "typed-spaces": lambda: build_typed(spaces=True), "scheme": build_scheme_example,
                        "styles": build_styles, "style-template": build_style_template,
                        "default-style": _default_style_doc}[name]()
    return _CACHE[name]


# ---- the two sides over one package ------------------------------------------------------------------------------------

class TSide:
    def __init__(self, data: bytes):
        self.pkg = DocxPackage(data)
        self.sheet = StyleSheet(self.pkg)
        self.defs = tl.ListDefs.load(self.pkg)
        self.root = self.pkg.xml(self.pkg.main_part())
        self.pars = list(self.root.iter(W + "p"))

    def count(self, override=None, defs=None):
        return tl.count(self.root, self.sheet, defs or self.defs, override)


class VSide:
    def __init__(self, data: bytes):
        self.pkg = VPackage(data)
        self.styles = vl.v_styles(self.pkg)
        self.defs = vl.v_list_defs(self.pkg)
        self.root = self.pkg.xml(self.pkg.main_part())
        self.pars = list(self.root.iter(W + "p"))

    def count(self, override=None, defs=None):
        return vl.v_count(self.root, self.styles, defs or self.defs, override)


def row(x) -> Tuple:
    return (x.index, x.num_id, x.ilvl, x.counters, x.text, x.suff, x.fmt, x.issue, x.not_evaluated is not None,
            x.abstract_id, x.source, x.style)


def nb_row(nb) -> Optional[Tuple]:
    return None if nb is None else (nb.num_id, nb.ilvl, nb.source, nb.style, nb.dangling, nb.unreadable is not None)


def lvl_row(lv) -> Optional[Tuple]:
    return None if lv is None else (lv.ilvl, lv.start, lv.fmt, lv.text, lv.restart, lv.legal, lv.suff, lv.pstyle,
                                    lv.unsupported is not None)


def par_text(p) -> str:
    """w:t (and w:tab as \\t) whose nearest paragraph is ``p`` — the test's key for a paragraph."""
    out = []
    for el in p.iter(W + "t", W + "tab"):
        q = el.getparent()
        while q is not None and q.tag != W + "p":
            q = q.getparent()
        if q is p and (el.tag == W + "t" or el.getparent().tag == W + "r"):
            out.append(el.text or "" if el.tag == W + "t" else "\t")
    return "".join(out)


def assert_sides_agree(data: bytes, override_t=None, defs_pair=None) -> None:
    t, v = TSide(data), VSide(data)
    for p_t, p_v in zip(t.pars, v.pars):
        assert nb_row(tl.paragraph_numbering(p_t, t.sheet, t.defs)) == \
            nb_row(vl.v_paragraph_numbering(p_v, v.styles, v.defs)), par_text(p_t)
        assert tl.counted(p_t) == vl.v_counted(p_v)
    tc, vc = t.count(), v.count()
    assert [row(x) for x in tc] == [row(x) for x in vc]
    assert tl.main_abstract(tc) == vl.v_main_abstract(vc)
    for nid in sorted(set(t.defs.nums) | set(t.defs.unreadable_nums)):
        assert (tl.list_is_clean(t.defs, nid, tc) is None) == (vl.v_list_is_clean(v.defs, nid, vc) is None), nid
        assert [lvl_row(t.defs.level(nid, k)) for k in range(9)] == [lvl_row(v.defs.level(nid, k)) for k in range(9)]
        assert (t.defs.problem(nid) or (None,))[0] == (v.defs.problem(nid) or (None,))[0]
        assert tl.has_restart_rule(t.defs, nid) == vl.v_has_restart_rule(v.defs, nid)


FIXTURES = ["numbering", "numbering-clean", "typed", "typed-typo", "typed-lead", "typed-spaces", "scheme", "styles",
            "style-template", "default-style"]


@pytest.mark.parametrize("name", FIXTURES)
def test_both_sides_agree_on_every_paragraph_of_the_fixtures(name):
    assert_sides_agree(fixture(name))


# ---- exact expected values -------------------------------------------------------------------------------------------

OUTLINE = {   # build_numbering(shared_restart=False): paragraph text → rendered number (counters where it matters)
    "GENERAL": "PART 1 -", "SUMMARY": "1.01", "Section includes wet-pipe sprinkler systems.": "A.",
    "Related requirements:": "B.", 'Division 21 Section "Common Work Results for Fire Suppression".': "1.",
    "Hangers.": "a.", "Supports.": "b.", 'Division 28 Section "Fire Detection and Alarm".': "2.",
    "": "C.",                                                   # the empty numbered paragraph uses up a number
    "Deferred submittal.": "D.", "REFERENCES": "1.02", "NFPA 13.": "A.",
    "Hidden requirement.": "B.",                                # hidden paragraphs count
    "UL listings.": "C.",                                       # ArchParaAlt: level 2 through its basedOn chain
    "SUBMITTALS": "1.03", "Product data.": "1.",                # own numPr with ONLY ilvl 3: the style's list
    "Shop drawings.": "A.",
    "Calculations, as specified in Article 1.02.": "B.",        # the pending deletion before it is not counted
    "Comply with Article 1.02.": "C.",
    "PRODUCTS": "PART 2 -", "MANUFACTURERS": "2.01", "Tyco.": "A.", "Viking.": "B.", "MATERIALS": "2.02",
    "Steel pipe.": "A.", "PIPING": "2.03", "Black steel.": "A.", "VALVES": "2.04", "Gate valves.": "A.",
    "B.\tCheck valves.": "B.", "EXECUTION": "PART 3 -", "INSTALLATION": "3.01", "Install per NFPA 13.": "A.",
    "Cell requirement.": "B.", "After the table.": "C.",
}
OTHER_LISTS = {
    "Bullet one": "•", "Bullet two": "•",
    "Appendix one": "APPENDIX III",       # startOverride 3 on an unshared num (the level's own start is 1)
    "Zed": "Z.",                          # upperLetter from 26
    "Legal one": "3.26.1",                # isLgl: the roman and letter levels shown in decimal
    "Double A": "AA.",                    # 27
    "Legal two": "3.27.2",                # lvlRestart 1: a level-1 paragraph does not restart level 2
    "Double B": "BB.",                    # 28 — never AB
    "Roman one": "(i)",
    "Appendix two": "APPENDIX IV",
    "Legal three": ("skipped",),          # "%1.%2.%3": level 1 has no value after the restart — not offered
    "Roman two": "(ii)",                  # lvlRestart 0: never restarts
    "Zed again": "Z.",
    "Override first": "1.", "Override sub": ("override",), "Override second": "2.",
}
COUNTERS = {"Product data.": (1, 3, 1, 1), "Shop drawings.": (1, 3, 1), "Legal three": (4, 26, 1),
            "Roman two": (4, 26, 1, 2), "Hangers.": (1, 1, 2, 1, 1), "PIPING": (2, 3)}
SHARED = {k: ("shared",) for k in OUTLINE}
DEFAULT_STYLE = {"One": "1.", "Two": "2.", "Three under Body": "3."}


def _default_style_doc() -> bytes:
    """A crafted file: the DEFAULT paragraph style ("Body") is numbered by list 5; "Other" is not; a paragraph whose
    w:pStyle is EMPTY names no style (unnumbered)."""
    styles = ('<w:style w:type="paragraph" w:default="1" w:styleId="Body"><w:name w:val="Body"/><w:pPr><w:numPr>'
              '<w:ilvl w:val="0"/><w:numId w:val="5"/></w:numPr></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Other"><w:name w:val="Other"/></w:style>')
    numbering = ('<w:abstractNum w:abstractNumId="5"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                 '<w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="5"><w:abstractNumId w:val="5"/></w:num>')
    body = (_p("One") + _p("Empty style", '<w:pStyle w:val=""/>') + _p("Other", '<w:pStyle w:val="Other"/>')
            + _p("Two") + _p("Three under Body", '<w:pStyle w:val="Body"/>'))
    return mini_docx(body, styles, numbering)


def observed(side: str, data: bytes) -> Dict[str, Any]:
    """paragraph text → rendered number, or (issue,) when not evaluated; counted numbered paragraphs only."""
    s = TSide(data) if side == "t" else VSide(data)
    out = {}
    for x in s.count():
        key = par_text(s.pars[x.index])
        while key in out:                                  # a paragraph counted twice under one text: a failure
            key += " (again)"
        out[key] = x.text if x.not_evaluated is None else (x.issue,)
        if key in COUNTERS and x.counters is not None and data is fixture("numbering-clean"):
            out[key + " counters"] = x.counters
    return out


FORMAT_CASES = [("upperLetter", 1, "A"), ("upperLetter", 26, "Z"), ("upperLetter", 27, "AA"), ("upperLetter", 28, "BB"),
                ("upperLetter", 52, "ZZ"), ("upperLetter", 53, "AAA"), ("lowerLetter", 27, "aa"),
                ("upperLetter", 0, None), ("upperRoman", 1, "I"), ("upperRoman", 4, "IV"), ("upperRoman", 9, "IX"),
                ("upperRoman", 14, "XIV"), ("upperRoman", 40, "XL"), ("upperRoman", 90, "XC"), ("upperRoman", 400, "CD"),
                ("upperRoman", 1994, "MCMXCIV"), ("upperRoman", 3999, "MMMCMXCIX"), ("lowerRoman", 2, "ii"),
                ("upperRoman", 0, None), ("upperRoman", 4000, None), ("decimalZero", 0, "00"), ("decimalZero", 1, "01"),
                ("decimalZero", 9, "09"), ("decimalZero", 10, "10"), ("decimalZero", 123, "123"),
                ("decimal", 0, "0"), ("decimal", 12, "12"), ("none", 5, ""), ("ordinal", 1, None),
                ("cardinalText", 1, None)]


def expectation_failures(side: str) -> List[str]:
    """Every way a side's output differs from the hand-written expectations (empty when it is right)."""
    fails = []
    fmt = tl.format_value if side == "t" else vl.v_format_value
    for f, n, want in FORMAT_CASES:
        if fmt(f, n) != want:
            fails.append(f"format {f} {n}: {fmt(f, n)!r} ≠ {want!r}")
    if fmt("bullet", 3, "•") != "•":
        fails.append("a bullet shows its text")
    clean = dict(OUTLINE, **OTHER_LISTS, **{k + " counters": v for k, v in COUNTERS.items()})
    for name, want in (("numbering-clean", clean), ("numbering", dict(SHARED, **OTHER_LISTS)),
                       ("default-style", DEFAULT_STYLE)):
        got = observed(side, fixture(name))
        if name == "numbering":
            got = {k: v for k, v in got.items() if not k.endswith(" counters")}
        for k in sorted(set(want) | set(got)):
            if want.get(k, "<absent>") != got.get(k, "<absent>"):
                fails.append(f"{name}: {k!r}: {got.get(k, '<absent>')!r} ≠ {want.get(k, '<absent>')!r}")
    return fails


def test_the_expected_numbers():
    assert expectation_failures("t") == []
    assert expectation_failures("v") == []


def test_what_is_not_counted_and_what_is_unnumbered():
    for side in ("t", "v"):
        got = observed(side, fixture("numbering-clean"))
        for text in ("SECTION 21 13 13", "WET-PIPE SPRINKLER SYSTEMS", "Unnumbered note under a numbered style.",
                     "Remark", "Text box holder: ", "Text box item", "Deleted requirement."):
            assert text not in got, (side, text)
    t = TSide(fixture("numbering-clean"))
    deleted = next(p for p in t.pars if "".join(e.text or "" for e in p.iter(W + "delText")) == "Deleted requirement.")
    assert tl.paragraph_numbering(deleted, t.sheet, t.defs) == tl.Numbering(10, 2, "style", "ArchPara")
    assert not tl.counted(deleted)
    boxed = [p for p in t.pars if par_text(p) == "Text box item"]
    assert len(boxed) == 2 and not any(tl.counted(p) for p in boxed)          # DrawingML and VML copies


def test_numbering_of_single_paragraphs():
    t = TSide(fixture("numbering-clean"))
    by_text = {par_text(p): p for p in t.pars}
    nb = lambda text: tl.paragraph_numbering(by_text[text], t.sheet, t.defs)
    assert nb("Section includes wet-pipe sprinkler systems.") == tl.Numbering(10, 2, "style", "ArchPara")  # linked
    assert nb("Hangers.") == tl.Numbering(10, 4, "style", "ArchSub2")                                      # linked
    assert nb("UL listings.") == tl.Numbering(10, 2, "style", "ArchPara")         # through the basedOn chain
    assert nb("Product data.") == tl.Numbering(10, 3, "style", "ArchPara")        # own ilvl, the style's list
    assert nb("SUMMARY") == tl.Numbering(10, 1, "style", "ArchArt")
    assert nb("Bullet one") == tl.Numbering(21, 0, "direct")
    assert nb("Unnumbered note under a numbered style.") is None                 # numId 0
    assert tl.style_numbering("ArchSub2", t.sheet, t.defs) == tl.Numbering(10, 4, "style", "ArchSub2")
    assert tl.style_numbering("Normal", t.sheet, t.defs) is None


def test_the_shared_abstractnum_and_the_destination_check():
    """Two nums of one abstractNum number paragraphs: every paragraph of both is not evaluated (Word continues one count
    across them, LibreOffice keeps them apart); the bullets, the appendix and the override list are unaffected."""
    for side, defs_of, count_of, clean in (("t", lambda s: s.defs, lambda s: s.count(), tl.list_is_clean),
                                           ("v", lambda s: s.defs, lambda s: s.count(), vl.v_list_is_clean)):
        s = TSide(fixture("numbering")) if side == "t" else VSide(fixture("numbering"))
        pars = count_of(s)
        shared = [x for x in pars if x.num_id in (10, 11)]
        assert len(shared) == 35 and all(x.issue == "shared" and x.text is None and x.counters is None for x in shared)
        assert all((tl.SHARED if side == "t" else vl.V_SHARED) in x.not_evaluated for x in shared)
        assert {x.num_id for x in shared} == {10, 11}
        assert (tl.main_abstract if side == "t" else vl.v_main_abstract)(pars) == 10
        assert "shared" in clean(defs_of(s), 10, pars)
        assert "lvlOverride" in clean(defs_of(s), 11, pars)                       # its startOverrides, before sharing
        assert clean(defs_of(s), 21, pars) is None                                 # the bullet list
        assert "lvlOverride" in clean(defs_of(s), 22, pars)                       # a startOverride is an override
        assert "lvlOverride" in clean(defs_of(s), 23, pars)
        s2 = TSide(fixture("numbering-clean")) if side == "t" else VSide(fixture("numbering-clean"))
        assert clean(defs_of(s2), 10, count_of(s2)) is None


def test_a_numbering_style_link_unreadable_definitions_and_dangling_ids():
    numbering = (
        '<w:abstractNum w:abstractNumId="1"><w:numStyleLink w:val="MyListStyle"/></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:start w:val="x"/></w:lvl></w:abstractNum>'
        '<w:abstractNum w:abstractNumId="3"><w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
        '</w:lvl></w:abstractNum><w:abstractNum w:abstractNumId="3"/>'
        '<w:abstractNum w:abstractNumId="4"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:lvlText w:val="%1."/></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="ordinal"/><w:lvlText w:val="%2"/></w:lvl>'
        '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:lvlText w:val="%1.%2.%3"/></w:lvl></w:abstractNum>'
        '<w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num>'
        '<w:num w:numId="3"><w:abstractNumId w:val="3"/></w:num><w:num w:numId="4"><w:abstractNumId w:val="4"/></w:num>'
        '<w:num w:numId="5"><w:abstractNumId w:val="x"/></w:num><w:num w:numId="6"><w:abstractNumId w:val="9"/></w:num>')
    body = "".join(_p(f"p{n}.{k}", f'<w:numPr><w:ilvl w:val="{k}"/><w:numId w:val="{n}"/></w:numPr>')
                   for n, k in ((1, 0), (2, 0), (3, 0), (4, 0), (4, 1), (4, 2), (5, 0), (6, 0), (7, 0)))
    body += _p("bad ilvl", '<w:numPr><w:ilvl w:val="12"/><w:numId w:val="4"/></w:numPr>') + \
        _p("after bad", '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="4"/></w:numPr>')
    data = mini_docx(body, "", numbering)
    assert_sides_agree(data)
    got = {par_text(TSide(data).pars[x.index]): (x.issue, x.text) for x in TSide(data).count()}
    assert got == {"p1.0": ("unsupported", None),          # w:numStyleLink
                   "p2.0": ("unreadable", None),           # a start that is not a whole number
                   "p3.0": ("unreadable", None),           # abstractNum 3 defined twice
                   "p4.0": (None, "1."),                   # level 0: decimal by default
                   "p4.1": ("unsupported", None),          # the format 'ordinal'
                   "p4.2": ("unsupported", None),          # names the ordinal level
                   "p5.0": ("unreadable", None),           # abstractNumId 'x'
                   "p6.0": ("unreadable", None),           # abstractNum 9 not defined
                   "bad ilvl": ("unreadable", None),       # ilvl 12
                   "after bad": ("earlier", None)}         # list 4 after a paragraph whose level is unknown
    t = TSide(data)                                        # p7.0: list 7 is not defined — dangling, not counted
    p7 = next(p for p in t.pars if par_text(p) == "p7.0")
    assert tl.paragraph_numbering(p7, t.sheet, t.defs).dangling
    v = VSide(data)
    assert vl.v_paragraph_numbering(next(p for p in v.pars if par_text(p) == "p7.0"), v.styles, v.defs).dangling
    # no numbering part: every paragraph is unnumbered, whatever its numPr says
    bare = mini_docx(body, "", None)
    assert TSide(bare).count() == [] and VSide(bare).count() == []
    assert tl.paragraph_numbering(TSide(bare).pars[0], TSide(bare).sheet, TSide(bare).defs) is None


def test_unsupported_levels_and_values():
    numbering = ('<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:lvlText w:val="%1."/>'
                 '</w:lvl><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="ordinal"/><w:lvlText w:val="%2"/>'
                 '</w:lvl><w:lvl w:ilvl="2"><w:start w:val="1"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>'
                 '<w:lvl w:ilvl="3"><w:start w:val="0"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="%4."/></w:lvl>'
                 '<w:lvl w:ilvl="4"><w:start w:val="1"/><w:lvlText w:val="%1.%6"/></w:lvl>'
                 '<w:lvl w:ilvl="5" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
                 '<w:start w:val="1"/><mc:AlternateContent><mc:Choice Requires="w14"><w:numFmt w:val="custom" '
                 'w:format="001, 002, 003, ..."/></mc:Choice><mc:Fallback><w:numFmt w:val="decimal"/></mc:Fallback>'
                 '</mc:AlternateContent><w:lvlText w:val="%6"/></w:lvl>'
                 '<w:lvl w:ilvl="6"><w:start w:val="1"/><w:lvlText w:val="%1.%8"/></w:lvl>'
                 '<w:lvl w:ilvl="7"><w:start w:val="1"/><w:suff w:val="wide"/><w:lvlText w:val="%8"/></w:lvl>'
                 '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>')
    body = "".join(_p(f"L{k}", f'<w:numPr><w:ilvl w:val="{k}"/><w:numId w:val="1"/></w:numPr>') for k in range(9))
    data = mini_docx(body, "", numbering)
    assert_sides_agree(data)
    got = {par_text(TSide(data).pars[x.index]): (x.issue, x.text, x.counters) for x in TSide(data).count()}
    assert got["L0"] == (None, "1.", (1,))
    assert got["L1"] == ("unsupported", None, (1, 1))             # its own format; the count is certain
    assert got["L2"] == ("unsupported", None, (1, 1, 1))          # names the ordinal level
    assert got["L3"] == ("unsupported", None, (1, 1, 1, 0))       # a letter of 0
    assert got["L4"] == ("unsupported", None, (1, 1, 1, 0, 1))    # names level 5 (mc:AlternateContent)
    assert got["L5"][0] == "unsupported"
    assert got["L6"][0] == "unsupported"                          # names level 7 (a suffix not modelled)
    assert got["L8"] == (None, "", (1, 1, 1, 0, 1, 1, 1, 1, 0))   # a missing level: no number, still counts


def test_the_default_paragraph_style_and_an_empty_pstyle():
    for side in ("t", "v"):
        assert observed(side, fixture("default-style")) == DEFAULT_STYLE


# ---- formats and the visible line -------------------------------------------------------------------------------------

def test_formats_agree_on_every_value():
    for f in tl.FORMATS + ("ordinal", "custom", ""):
        for n in range(-3, 4010):
            assert tl.format_value(f, n, "•") == vl.v_format_value(f, n, "•"), (f, n)
    assert tl.format_value("bullet", 1) == vl.v_format_value("bullet", 1) == ""


@pytest.mark.parametrize("number,text,suff,want", [
    (None, "\tA.\t Section  includes x ", None, "A. Section includes x"),
    ("A.", "Section includes", "tab", "A. Section includes"),
    ("PART 1 -", "GENERAL", "space", "PART 1 - GENERAL"),
    ("1.", "Text", "nothing", "1.Text"),
    ("", "Text", "tab", "Text"),
    ("1.", "Line\nbreak", "tab", "1. Line\nbreak"),
])
def test_the_visible_line(number, text, suff, want):
    assert tl.visible_line(number, text, suff) == vl.v_visible_line(number, text, suff) == want


# ---- the scheme -------------------------------------------------------------------------------------------------------

def scheme_pair(data: bytes, levels=SCHEME_LEVELS):
    t, v = TSide(data), VSide(data)
    return tl.resolve_scheme(levels, t.sheet, t.defs, "the template", "bad-params"), \
        vl.v_resolve_scheme(levels, v.styles, v.defs)


def test_the_example_file_is_a_scheme():
    ts, vs = scheme_pair(fixture("scheme"))
    assert [(l.style, l.num_id, l.ilvl, l.level.text, l.level.fmt) for l in ts.levels] == \
        [(l.style, l.num_id, l.ilvl, l.level.text, l.level.fmt) for l in vs.levels] == [
            ("PRT", 10, 0, "PART %1 -", "decimal"), ("ART", 10, 1, "%1.%2", "decimal"),
            ("PR1", 10, 2, "%3.", "upperLetter"), ("PR2", 10, 3, "%4.", "decimal"), ("PR3", 10, 4, "%5.", "lowerLetter")]
    assert ts.num_id == vs.num_id == 10 and ts.abstract_id == vs.abstract_id == 10
    assert ts.levels[0].level.suff == "space" and tl.level_pattern(ts, 1) == ts.levels[1].pattern
    got = {par_text(TSide(fixture("scheme")).pars[x.index]): x.text for x in TSide(fixture("scheme")).count()}
    assert got == {"GENERAL": "PART 1 -", "SUMMARY": "1.1", "Paragraph.": "A.", "Subparagraph.": "1.",
                   "Sub-subparagraph.": "a.", "Second sub-subparagraph.": "b.", "Second subparagraph.": "2.",
                   "Second paragraph.": "B.", "SECOND ARTICLE": "1.2", "PRODUCTS": "PART 2 -", "MATERIALS": "2.1",
                   "Paragraph under part 2.": "A."}
    # by name (whitespace collapsed, casefolded), and the office styles template's four levels
    ts2, vs2 = scheme_pair(fixture("scheme"), ["prt", " ART ", "pr1"])
    assert [l.style for l in ts2.levels] == [l.style for l in vs2.levels] == ["PRT", "ART", "PR1"]
    ts3, vs3 = scheme_pair(fixture("style-template"), ["PRT", "ART", "PR1", "PR2"])
    assert [(l.style, l.level.text) for l in ts3.levels] == [(l.style, l.level.text) for l in vs3.levels] == [
        ("PRT", "PART %1 -"), ("ART", "%1.%2"), ("PR1", "%3."), ("PR2", "%4.")]


def _refusals():
    def numpr_of(sid, num=None, ilvl=None):
        def edit(root):
            st = next(s for s in root.findall(W + "style") if s.get(W + "styleId") == sid)
            numpr = st.find(f"{W}pPr/{W}numPr")
            if num is not None:
                numpr.find(W + "numId").set(W + "val", str(num))
            if ilvl is not None:
                numpr.find(W + "ilvl").set(W + "val", str(ilvl))
        return {"word/styles.xml": edit}

    def level_of(k, fn):
        def edit(root):
            absn = next(a for a in root.findall(W + "abstractNum") if a.get(W + "abstractNumId") == "10")
            fn(next(l for l in absn.findall(W + "lvl") if l.get(W + "ilvl") == str(k)))
        return {NUMBERING: edit}

    def override(root):
        num = next(n for n in root.findall(W + "num") if n.get(W + "numId") == "10")
        o = etree.SubElement(num, W + "lvlOverride"); o.set(W + "ilvl", "2")
        lvl = etree.SubElement(o, W + "lvl"); lvl.set(W + "ilvl", "2")
    ex = SCHEME_LEVELS
    return [
        ("two lists", numpr_of("PR2", num=9), ex),
        ("wrong order", {}, ["ART", "PRT", "PR1"]),
        ("a style twice", {}, ["PRT", "ART", "ART"]),
        ("missing style", {}, ["PRT", "ART", "NoSuchStyle"]),
        ("not numbered", {}, ["PRT", "Normal"]),
        ("a character style", {}, ["PRT", "DefaultParagraphFont"]),
        ("wrong ilvl", numpr_of("PR1", ilvl=3), ex),
        ("a bullet level", level_of(4, lambda l: l.find(W + "numFmt").set(W + "val", "bullet")), ex),
        ("no text", level_of(3, lambda l: l.remove(l.find(W + "lvlText"))), ex),
        ("a format not modelled", level_of(2, lambda l: l.find(W + "numFmt").set(W + "val", "ordinal")), ex),
        ("names an undefined level", level_of(3, lambda l: l.find(W + "lvlText").set(W + "val", "%4.%9")), ex),
        ("names a bullet", {NUMBERING: lambda root: (level_of(4, lambda l: l.find(W + "numFmt").set(W + "val", "bullet"))
                                                     [NUMBERING](root),
                                                     level_of(3, lambda l: l.find(W + "lvlText").set(W + "val", "%4%5"))
                                                     [NUMBERING](root))}, ex[:4]),
        ("a full override level", {NUMBERING: override}, ex),
        ("dangling list", numpr_of("PRT", num=77), ex),
    ]


@pytest.mark.parametrize("case", [c[0] for c in _refusals()])
def test_scheme_refusals_on_both_sides(case):
    _, edits, levels = next(c for c in _refusals() if c[0] == case)
    data = patch_docx(fixture("scheme"), edits) if edits else fixture("scheme")
    t, v = TSide(data), VSide(data)
    with pytest.raises(JobException) as e:
        tl.resolve_scheme(levels, t.sheet, t.defs, "the template", "bad-params")
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        tl.resolve_scheme(levels, t.sheet, t.defs, "this file", "scheme")
    assert e.value.code == "scheme" and str(e.value).startswith("this file: ")
    with pytest.raises(ValueError):
        vl.v_resolve_scheme(levels, v.styles, v.defs)


@pytest.mark.parametrize("levels", [[], "PRT", ["PRT", 3], ["PRT", " "], ["PRT"] * 10])
def test_malformed_levels_are_bad_params(levels):
    t, v = TSide(fixture("scheme")), VSide(fixture("scheme"))
    with pytest.raises(JobException) as e:
        tl.resolve_scheme(levels, t.sheet, t.defs, "this file", "scheme")
    assert e.value.code == "bad-params"
    with pytest.raises(ValueError):
        vl.v_resolve_scheme(levels, v.styles, v.defs)


# ---- typed numbers ----------------------------------------------------------------------------------------------------

TYPED = {   # build_typed(): paragraph text → (level, token)
    "PART 1 - GENERAL": (0, "PART 1 -"), "1.1\tSUMMARY": (1, "1.1"),
    "A.\tSection includes wet-pipe sprinkler systems.": (2, "A."), "B.\tRelated requirements:": (2, "B."),
    '1.\tDivision 21 Section "Common Work Results for Fire Suppression".': (3, "1."), "a.\tHangers.": (4, "a."),
    "b.\tSupports.": (4, "b."), '2.\tDivision 28 Section "Fire Detection and Alarm".': (3, "2."),
    "C.  Submittals.": (2, "C."), "1.2\tREFERENCES": (1, "1.2"), "A.\tNFPA 13.": (2, "A."),
    "PART 2 - PRODUCTS": (0, "PART 2 -"), "2.1\tMANUFACTURERS": (1, "2.1"), "A.\tTyco.": (2, "A."),
    "B.\tViking.": (2, "B."), "C.\tReliable.": (2, "C."), "2.2\tMATERIALS": (1, "2.2"), "A.\tSteel pipe.": (2, "A."),
    "1.\tCell item.": (3, "1."), "2.\tCell item two.": (3, "2."), "PART 3 - EXECUTION": (0, "PART 3 -"),
    "3.1\tINSTALLATION": (1, "3.1"), "A.\tInstall per NFPA 13.": (2, "A."), "1.\tHangers per NFPA 13.": (3, "1."),
    "Unnumbered closing note.": None, "END OF SECTION 21 13 13": None, "SECTION 21 13 13": None,
    "WET-PIPE SPRINKLER SYSTEMS": None,
}


def typed_pair(text: str, schemes):
    ts, vs = schemes
    a, b = tl.typed_number(text, ts), vl.v_typed_number(text, vs)
    ra = None if a is None else (a.level, a.token, a.token_start, a.end, a.levels, a.ambiguous)
    rb = None if b is None else (b.level, b.token, b.token_start, b.end, b.levels, b.ambiguous)
    assert ra == rb, text
    return a


@pytest.mark.parametrize("variant", ["typed", "typed-lead", "typed-spaces"])
def test_typed_numbers_of_the_typed_fixture(variant):
    """Every paragraph of the three typed variants: the level and the number as typed; the prefix is the number with
    every space, tab and NBSP after it (and before it: the leading whitespace of ``leading_tab``)."""
    schemes = scheme_pair(fixture("scheme"))
    want = {(k.replace("\t", " ") if variant == "typed-spaces" else k): v for k, v in TYPED.items()}
    t, v = TSide(fixture(variant)), VSide(fixture(variant))
    seen = set()
    for p_t, p_v in zip(t.pars, v.pars):
        text = run_text(p_t)
        assert text == v_paragraph_text(p_v)
        key = text.lstrip(" \t")
        if not key:
            continue
        got = typed_pair(text, schemes)
        assert (None if got is None else (got.level, got.token)) == want[key], (variant, text)
        if got is not None:
            assert text[got.end:] == key[len(got.token):].lstrip(" \t\u00a0")
            assert text[got.token_start:got.end].startswith(got.token)
        seen.add(key)
    assert seen == set(want)


@pytest.mark.parametrize("text,want", [
    ("A.", (2, "A.", 0, 2)),                     # the end of the run text
    ("A. Text", (2, "A.", 0, 3)),           # an NBSP separator
    (" A. Text", None),                     # but no NBSP before the number
    (" \t A.\t \tText", (2, "A.", 3, 8)),
    ("1.1SUMMARY", None),                        # no separator
    ("A.Text", None),
    ("A Text", None), ("PART 1 GENERAL", None), ("part 1 - GENERAL", None),
    ("1.1.1\tText", None),                       # no level is 1.1.1
    ("AA.\tText", (2, "AA.", 0, 4)),             # a pattern, not a value check (rendered numbers are compared later)
    ("01.\tText", (3, "01.", 0, 4)),
    ("1.\n", None),                              # a break is not a separator, and not the end of the run text
    ("", None), ("\t", None),
])
def test_typed_number_rules(text, want):
    got = typed_pair(text, scheme_pair(fixture("scheme")))
    assert (None if got is None else (got.level, got.token, got.token_start, got.end)) == want


def test_typed_numbers_that_two_levels_match_are_ambiguous():
    """A scheme with a roman level and a letter level: "C.", "I.", "IV." are both (every roman numeral is letters too);
    "B." and "AB." only letters."""
    styles = "".join(f'<w:style w:type="paragraph" w:styleId="L{k}"><w:name w:val="L{k}"/><w:pPr><w:numPr>'
                     f'<w:ilvl w:val="{k}"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style>' for k in range(3))
    numbering = ('<w:abstractNum w:abstractNumId="1">'
                 '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="upperRoman"/><w:lvlText w:val="%1."/></w:lvl>'
                 '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="upperLetter"/><w:lvlText w:val="%2."/></w:lvl>'
                 '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="(%3)"/></w:lvl>'
                 '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>')
    data = mini_docx(_p("x"), styles, numbering)
    t, v = TSide(data), VSide(data)
    schemes = (tl.resolve_scheme(["L0", "L1", "L2"], t.sheet, t.defs), vl.v_resolve_scheme(["L0", "L1", "L2"], v.styles,
                                                                                            v.defs))
    for text, level, levels in (("C.\tx", None, (0, 1)), ("I. x", None, (0, 1)), ("B.\tx", 1, (1,)),
                                ("IV.\tx", None, (0, 1)), ("AB.\tx", 1, (1,)), ("(3)\tx", 2, (2,))):
        got = typed_pair(text, schemes)
        assert (got.level, got.levels, got.ambiguous) == (level, levels, len(levels) > 1), text
    # a legal level's %j are digits whatever the format; a level whose text names a bullet has no pattern
    legal = numbering.replace('<w:lvlText w:val="(%3)"/>', '<w:isLgl/><w:lvlText w:val="%1.%2.%3"/>')
    t, v = TSide(mini_docx(_p("x"), styles, legal)), VSide(mini_docx(_p("x"), styles, legal))
    schemes = (tl.resolve_scheme(["L0", "L1", "L2"], t.sheet, t.defs), vl.v_resolve_scheme(["L0", "L1", "L2"], v.styles,
                                                                                            v.defs))
    assert typed_pair("2.3.4\tx", schemes).level == 2 and typed_pair("II.C.4\tx", schemes) is None


# ---- the planned output: an imported list and override (what convert_typed_numbers will predict) ---------------------

def _imported(src_defs_root, new_id: int):
    """The example file's list 10 / abstractNum 10 as copies with ``new_id`` (what the style import would bring)."""
    a = copy.deepcopy(next(x for x in src_defs_root.findall(W + "abstractNum") if x.get(W + "abstractNumId") == "10"))
    n = copy.deepcopy(next(x for x in src_defs_root.findall(W + "num") if x.get(W + "numId") == "10"))
    a.set(W + "abstractNumId", str(new_id))
    n.set(W + "numId", str(new_id))
    n.find(W + "abstractNumId").set(W + "val", str(new_id))
    return a, n


def converted(side: str, variant: str) -> List[Tuple[str, Optional[str], str, str]]:
    """Every typed paragraph outside tables numbered by the imported scheme list at its typed level: (typed token,
    rendered number, the source's visible line, the output's visible line)."""
    tpl = DocxPackage(fixture("scheme")).xml(NUMBERING)
    a, n = _imported(tpl, 50)
    ts, vs = scheme_pair(fixture("scheme"))
    s = TSide(fixture(variant)) if side == "t" else VSide(fixture(variant))
    defs = s.defs.with_extra(abstracts=[a], nums=[n])
    text_of = run_text if side == "t" else v_paragraph_text
    typed = {}
    for i, p in enumerate(s.pars):
        q, in_table = p.getparent(), False
        while q is not None:
            in_table = in_table or q.tag == W + "tc"
            q = q.getparent()
        got = (tl.typed_number if side == "t" else vl.v_typed_number)(text_of(p), ts if side == "t" else vs)
        if got is not None and not in_table:
            typed[i] = got
    pars = {x.index: x for x in s.count({i: (50, g.level) for i, g in typed.items()}, defs)}
    vis = tl.visible_line if side == "t" else vl.v_visible_line
    out = []
    for i, g in typed.items():
        x = pars[i]
        assert x.num_id == 50 and x.not_evaluated is None
        out.append((g.token, x.text, vis(None, text_of(s.pars[i])), vis(x.text, text_of(s.pars[i])[g.end:], x.suff)))
    return out


@pytest.mark.parametrize("variant", ["typed", "typed-lead", "typed-spaces"])
def test_typed_numbers_become_the_same_numbers_and_the_document_reads_the_same(variant):
    for side in ("t", "v"):
        rows = converted(side, variant)
        assert len(rows) == 22
        assert all(token == text and before == after for token, text, before, after in rows), \
            [r for r in rows if r[0] != r[1] or r[2] != r[3]]
    assert converted("t", variant) == converted("v", variant)


def test_a_typo_in_the_typed_numbers_shows_as_a_mismatch():
    for side in ("t", "v"):
        bad = [r for r in converted(side, "typed-typo") if r[0] != r[1] or r[2] != r[3]]
        assert bad == [("B.", "C.", "B. Reliable.", "C. Reliable.")]


def test_extra_definitions_make_a_numbering_part_present():
    t = TSide(fixture("typed"))
    assert tl.ListDefs.load(t.pkg).with_extra().present and tl.ListDefs(None).present is False
    assert tl.ListDefs(None).with_extra(nums=[etree.Element(W + "num")]).present
    assert vl.VListDefs(None).present is False and vl.VListDefs(None).with_extra(nums=[etree.Element(W + "num")]).present


def test_override_none_and_numbering_off():
    t, v = TSide(fixture("scheme")), VSide(fixture("scheme"))
    first = next(x.index for x in t.count())
    for s in (t, v):
        got = s.count({first: None})
        assert first not in [x.index for x in got]
        assert (got[0].issue, got[0].text, got[0].counters) == ("skipped", None, (1, 1))   # "%1.%2", level 0 never seen
        assert [x.text for x in got[1:4]] == ["A.", "1.", "a."]                          # "%3." etc. name only their own
        got = s.count({first: (10, 1)})
        assert [(x.issue, x.counters) for x in got[:2]] == [("skipped", (1, 1)), ("skipped", (1, 2))]
        assert first not in [x.index for x in s.count({first: (0, 0)})]


SKIPS = ('<w:abstractNum w:abstractNumId="1">'
         '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:lvlText w:val="%1."/></w:lvl>'
         '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:lvlText w:val="%1.%2"/></w:lvl>'
         '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:lvlText w:val="%1.%2.%3"/></w:lvl>'
         '<w:lvl w:ilvl="3"><w:start w:val="1"/><w:lvlRestart w:val="0"/><w:lvlText w:val="(%4)"/></w:lvl>'
         '<w:lvl w:ilvl="4"><w:start w:val="1"/><w:lvlText w:val="%5 of %4"/></w:lvl>'
         '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>')


@pytest.mark.parametrize("side", ["t", "v"])
def test_a_skipped_level_is_not_evaluated(side):
    """A %j naming a level that has no current value — never reached, or reset by a restart — is not offered: Word shows
    0 there ("1.0.1"), LibreOffice the start and then continues from it. A level that never restarts (lvlRestart 0) keeps
    its value and may be named; the paragraph's own level always has one."""
    order = [("a", 0), ("b", 2), ("c", 1), ("d", 2), ("e", 0), ("f", 2), ("g", 3), ("h", 0), ("i", 4), ("j", 1)]
    body = "".join(_p(t, f'<w:numPr><w:ilvl w:val="{k}"/><w:numId w:val="1"/></w:numPr>') for t, k in order)
    data = mini_docx(body, "", SKIPS)
    assert_sides_agree(data)
    s = TSide(data) if side == "t" else VSide(data)
    got = {par_text(s.pars[x.index]): (x.issue, x.text, x.counters) for x in s.count()}
    assert got == {
        "a": (None, "1.", (1,)),
        "b": ("skipped", None, (1, 1, 1)),          # level 1 never reached
        "c": (None, "1.1", (1, 1)),
        "d": (None, "1.1.1", (1, 1, 1)),
        "e": (None, "2.", (2,)),                    # restarts levels 1 and 2 (not 3: lvlRestart 0)
        "f": ("skipped", None, (2, 1, 1)),          # level 1 reset by the restart
        "g": (None, "(1)", (2, 1, 1, 1)),
        "h": (None, "3.", (3,)),
        "i": (None, "1 of 1", (3, 1, 1, 1, 1)),     # names level 3, which never restarts and still holds 1
        "j": (None, "3.1", (3, 1)),
    }
    reason = next(x.not_evaluated for x in s.count() if par_text(s.pars[x.index]) == "b")
    assert reason.startswith(tl.SKIPPED if side == "t" else vl.V_SKIPPED) and "level 1" in reason


def test_levels_carrying_a_restart_rule():
    """LibreOffice ignores w:lvlRestart: the model reads it as Word does, and the levels carrying one are listed for a
    flag."""
    t, v = TSide(fixture("numbering-clean")), VSide(fixture("numbering-clean"))
    for nid, want in ((22, [2, 3]), (10, []), (21, []), (23, [])):
        assert tl.has_restart_rule(t.defs, nid) == vl.v_has_restart_rule(v.defs, nid) == want, nid
    assert tl.has_restart_rule(t.defs, 99) == vl.v_has_restart_rule(v.defs, 99) == []


# ---- shared defects: the comparison cannot see them, the expectations do -------------------------------------------

def _wrong_letters(real):
    def wrong(fmt, n, text=None):
        if fmt in ("upperLetter", "lowerLetter") and n > 26:          # spreadsheet columns: 27 → AA, 28 → AB
            s = ""
            while n:
                n, r = divmod(n - 1, 26)
                s = chr(65 + r) + s
            return s if fmt == "upperLetter" else s.lower()
        return real(fmt, n, text)
    return wrong


def _always_zero(real):
    return lambda fmt, n, text=None: "0" + str(n) if fmt == "decimalZero" else real(fmt, n, text)


def _never_zero(real):
    return lambda fmt, n, text=None: str(n) if fmt == "decimalZero" else real(fmt, n, text)


DEFECTS = {
    "lvlRestart ignored": [("restarts", "_v_restarts", lambda real: lambda restart, L, k: True)],
    "linked level forgotten": [("linked_level", "_v_linked_level", lambda real: lambda defs, n, s: None)],
    "startOverride ignored": [
        ("start_of", "_v_start_of", lambda real: lambda defs, n, k: (defs.level(n, k).start
                                                                   if defs.level(n, k) is not None else 0))],
    "letters past Z wrong": [("format_value", "v_format_value", _wrong_letters)],
    "decimalZero padded past 9": [("format_value", "v_format_value", _always_zero)],
    "decimalZero not padded": [("format_value", "v_format_value", _never_zero)],
    "isLgl ignored": [("render", "_v_render",
                       lambda real: lambda text, legal, value, level: real(text, False, value, level))],
    "default paragraph style forgotten": [
        ("paragraph_style", "v_paragraph_style", lambda real: lambda p, sheet: (
            (p.find(f"{W}pPr/{W}pStyle").get(W + "val") or None) if p.find(f"{W}pPr/{W}pStyle") is not None
            else None))],
    "pending deletions counted": [
        ("counted", "v_counted", lambda real: lambda p: not any(a.tag == W + "txbxContent" for a in p.iterancestors()))],
    "text boxes counted": [
        ("counted", "v_counted", lambda real: lambda p: real(p) or any(a.tag == W + "txbxContent"
                                                                       for a in p.iterancestors()))],
    "a skipped level shows its start": [("skipped_level", "_v_skipped", lambda real: lambda text, has: None)],
}


@pytest.mark.parametrize("defect", sorted(DEFECTS))
def test_a_wrong_rule_shared_by_both_sides_is_caught(defect, monkeypatch):
    for t_name, v_name, make in DEFECTS[defect]:
        monkeypatch.setattr(tl, t_name, make(getattr(tl, t_name)))
        monkeypatch.setattr(vl, v_name, make(getattr(vl, v_name)))
    assert expectation_failures("t"), defect
    assert expectation_failures("v"), defect
    for name in ("numbering", "numbering-clean", "default-style"):    # the two sides still agree with each other
        assert_sides_agree(fixture(name))


def test_override_levels_and_shared_lists_ignored_are_caught(monkeypatch):
    """The two decisions of the design review: a full override level counted as if supported, and a shared abstractNum
    counted per num as if separate — shared by both sides, caught by the expectations."""
    monkeypatch.setattr(tl.ListDefs, "is_override", lambda self, n, k: False)
    monkeypatch.setattr(vl.VListDefs, "is_override", lambda self, n, k: False)
    assert any("Override sub" in f for f in expectation_failures("t"))
    assert any("Override sub" in f for f in expectation_failures("v"))


# ---- random list definitions ------------------------------------------------------------------------------------------

def _p(text: str, ppr: str = "", in_table: bool = False, mark: str = "") -> str:
    rpr = f"<w:rPr>{mark}</w:rPr>" if mark else ""
    body = f"<w:p><w:pPr>{ppr}{rpr}</w:pPr><w:r><w:t xml:space=\"preserve\">{_esc(text)}</w:t></w:r></w:p>"
    return f"<w:tbl><w:tr><w:tc>{body}</w:tc></w:tr></w:tbl>" if in_table else body


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mini_docx(body: str, styles: str, numbering: Optional[str]) -> bytes:
    """A minimal package: a main part, a styles part, and (unless ``numbering`` is None) a numbering part."""
    decl = (f'xmlns:w="{W_NS}" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"')
    ct = ('<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" '
          'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
            'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>')
    base = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
    doc_rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships '
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                f'<Relationship Id="rId1" Type="{base}styles" Target="styles.xml"/>'
                + (f'<Relationship Id="rId2" Type="{base}numbering" Target="numbering.xml"/>' if numbering is not None
                   else "") + "</Relationships>")
    members = {"[Content_Types].xml": ct, "_rels/.rels": rels, "word/_rels/document.xml.rels": doc_rels,
               MAIN: f"<w:document {decl}><w:body>{body}</w:body></w:document>",
               "word/styles.xml": f"<w:styles {decl}>{styles}</w:styles>"}
    if numbering is not None:
        members[NUMBERING] = f"<w:numbering {decl}>{numbering}</w:numbering>"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text.encode("utf-8"))
    return buf.getvalue()


FMT_POOL = ["decimal"] * 4 + ["decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman", "bullet", "none",
                              "ordinal", "custom", ""]
GOOD_FMTS = ["decimal", "decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman", "none"]
STYLE_IDS = ["S0", "S1", "S2", "S3", "Normal"]
TEXT_PIECES = ["%1", "%2", "%3", "%4", "%5", "%9", "%2", ".", ".", "-", " ", "PART ", "(", ")", "%", "%0", "A", "%%"]
TYPED_TEXTS = ["A.\tText", "1.1 Text", "PART 2 - X", "I. Y", "a. z", "  3. q", "", "AB.", "1.1Text", "(3)\tx",
               "01.\tq", "II.\tx", "1.2.3 t", "C)", "\tb.\tthing", "x 1. y"]


class Gen:
    """Random list definitions: ``rough`` (bad values, shared abstractNums, overrides — the refusal paths) or clean (a
    scheme-like list on S0–S3 — the counting paths)."""

    def __init__(self, rng, rough: bool):
        self.rng, self.rough = rng, rough
        self.p_bad = 0.06 if rough else 0.004

    def bad(self, good: Any) -> str:
        return self.rng.choice(["x", "", "1.5", "+2", "٣"]) if self.rng.random() < self.p_bad else str(good)

    def text(self) -> str:
        return "".join(self.rng.choice(TEXT_PIECES) for _ in range(self.rng.randint(0, 4)))

    def level(self, ilvl_attr: str) -> str:
        rng, out = self.rng, []
        if rng.random() < 0.9:
            out.append(f'<w:start w:val="{self.bad(rng.choice([0, 1, 1, 1, 1, 2, 5, 25, 26, 27, 3998, -1]))}"/>')
        if rng.random() < 0.9:
            out.append(f'<w:numFmt w:val="{rng.choice(FMT_POOL)}"/>')
        if rng.random() < 0.3:
            out.append(f'<w:lvlRestart w:val="{self.bad(rng.choice([0, 1, 2, 3, 9, -1]))}"/>')
        if rng.random() < 0.6:
            out.append(f'<w:pStyle w:val="{rng.choice(STYLE_IDS + [""])}"/>')
        if rng.random() < 0.15:
            out.append(rng.choice(["<w:isLgl/>", '<w:isLgl w:val="0"/>', '<w:isLgl w:val="false"/>',
                                   '<w:isLgl w:val="1"/>']))
        if rng.random() < 0.3:
            out.append(f'<w:suff w:val="{rng.choice(["tab", "space", "nothing", "wide", ""])}"/>')
        if rng.random() < 0.9:
            out.append(f"<w:lvlText w:val={quoteattr(self.text())}/>")
        if rng.random() < 0.03:
            out.append('<mc:AlternateContent><mc:Choice Requires="w14"><w:numFmt w:val="custom"/></mc:Choice>'
                       '<mc:Fallback><w:numFmt w:val="decimal"/></mc:Fallback></mc:AlternateContent>')
        rng.shuffle(out)
        return f'<w:lvl w:ilvl="{ilvl_attr}">{"".join(out)}</w:lvl>'

    def scheme_level(self, k: int) -> str:
        """A level a scheme can use: a supported format (rarely a bullet), a text naming itself and maybe earlier
        levels, its style S<k>."""
        rng = self.rng
        own = f"%{k + 1}"
        text = rng.choice([f"{own}.", f"({own})", f"{own})", "PART %1 -" if k == 0 else f"%{k}.{own}",
                           ".".join(f"%{j + 1}" for j in range(k + 1)), f"Article {own}"])
        out = [f'<w:start w:val="{self.bad(rng.choice([1, 1, 1, 0, 3, 26]))}"/>',
               f'<w:numFmt w:val="{rng.choice(GOOD_FMTS + (["bullet"] if rng.random() < 0.1 else []))}"/>']
        if rng.random() < 0.2:
            out.append(f'<w:lvlRestart w:val="{rng.choice([0, 1, 2, 3])}"/>')
        if k < 4:
            out.append(f'<w:pStyle w:val="S{k}"/>')
        if rng.random() < 0.15:
            out.append("<w:isLgl/>")
        if rng.random() < 0.4:
            out.append(f'<w:suff w:val="{rng.choice(["tab", "space", "nothing"])}"/>')
        out.append(f"<w:lvlText w:val={quoteattr(text)}/>")
        return f'<w:lvl w:ilvl="{k}">{"".join(out)}</w:lvl>'

    def numbering(self, abs_ids, num_ids) -> Tuple[List[str], List[str]]:
        rng = self.rng
        abstracts, nums = [], []
        for i, aid in enumerate(abs_ids):
            if self.rough and rng.random() < 0.05:
                abstracts.append(f'<w:abstractNum w:abstractNumId="{aid}"><w:numStyleLink w:val="S0"/></w:abstractNum>')
                continue
            if not self.rough and i == 0:
                levels = [self.scheme_level(k) for k in range(rng.randint(3, 6))]
            else:
                ks = rng.sample(range(9), rng.randint(0, 9))
                if self.rough and rng.random() < 0.04:
                    ks.append(rng.choice(["9", "x", "-1"] + ([str(ks[0])] if ks else [])))
                levels = [self.level(str(k)) for k in ks]
            abstracts.append(f'<w:abstractNum w:abstractNumId="{self.bad(aid)}">' + "".join(levels) + "</w:abstractNum>")
        if self.rough and abs_ids and rng.random() < 0.05:
            abstracts.append(f'<w:abstractNum w:abstractNumId="{abs_ids[0]}"/>')        # an id twice
        for i, nid in enumerate(num_ids):
            overrides = []
            for k in rng.sample(range(9), rng.choice([0, 0, 0, 1, 2] if self.rough else [0] * 9 + [1])):
                inner = ""
                if rng.random() < 0.8:
                    inner += f'<w:startOverride w:val="{self.bad(rng.choice([1, 2, 3, 0, 26]))}"/>'
                if rng.random() < (0.2 if self.rough else 0.05):
                    inner += self.level(str(k))
                overrides.append(f'<w:lvlOverride w:ilvl="{self.bad(k)}">{inner}</w:lvlOverride>')
            if self.rough:
                aid = rng.choice(abs_ids + [99]) if abs_ids else 99
            else:
                aid = abs_ids[i % len(abs_ids)] if abs_ids else 99    # one abstractNum each while there are enough
            nums.append(f'<w:num w:numId="{self.bad(nid)}"><w:abstractNumId w:val="{self.bad(aid)}"/>'
                        + "".join(overrides) + "</w:num>")
        if self.rough and num_ids and rng.random() < 0.04:
            nums.append(f'<w:num w:numId="{num_ids[0]}"><w:abstractNumId w:val="{abs_ids[0] if abs_ids else 0}"/></w:num>')
        return abstracts, nums

    def numpr(self, num_ids, style: Optional[int] = None) -> str:
        rng = self.rng
        if style is not None:                            # a scheme style: its list, its level or the linked one
            if rng.random() < 0.7:
                return f'<w:numPr><w:ilvl w:val="{style}"/><w:numId w:val="{num_ids[0]}"/></w:numPr>'
            return f'<w:numPr><w:numId w:val="{num_ids[0]}"/></w:numPr>'
        r = rng.random()
        pick = lambda: self.bad(rng.choice(num_ids + [0, 0, 42]) if num_ids else 0)
        lvl = lambda: self.bad(rng.choice([0, 0, 1, 1, 2, 3, 4, 8, 9] if self.rough else [0, 0, 1, 1, 2, 3, 4]))
        if r < (0.35 if self.rough else 0.6):
            return ""
        if r < 0.75:
            return f'<w:numPr><w:ilvl w:val="{lvl()}"/><w:numId w:val="{pick()}"/></w:numPr>'
        if r < 0.85:
            return f'<w:numPr><w:numId w:val="{pick()}"/></w:numPr>'
        if r < 0.97:
            return f'<w:numPr><w:ilvl w:val="{lvl()}"/></w:numPr>'
        return "<w:numPr/>"


def _rand_doc(rng) -> Tuple[bytes, List[List[str]]]:
    g = Gen(rng, rough=rng.random() < 0.5)
    abs_ids = rng.sample(range(8), rng.randint(0 if g.rough else 1, 4))
    num_ids = rng.sample(range(1, 9), rng.randint(0, 5) if g.rough else rng.randint(1, len(abs_ids)))
    abstracts, nums = g.numbering(abs_ids, num_ids)
    styles = []
    for k, sid in enumerate(STYLE_IDS):
        if g.rough:
            based = rng.choice([None, None, "Normal"] + STYLE_IDS)
            numpr = g.numpr(num_ids)
        else:
            based = rng.choice(["Normal"] * 4 + STYLE_IDS) if sid != "Normal" else None
            numpr = g.numpr(num_ids, k) if sid != "Normal" else (g.numpr(num_ids) if rng.random() < 0.1 else "")
        default = ' w:default="1"' if sid == "Normal" else ""
        styles.append(f'<w:style w:type="paragraph"{default} w:styleId="{sid}">'
                      f'<w:name w:val="{sid}"/>' + (f'<w:basedOn w:val="{based}"/>' if based else "")
                      + f"<w:pPr>{numpr}</w:pPr></w:style>")
    styles.append('<w:style w:type="character" w:styleId="C1"><w:name w:val="C1"/></w:style>')
    body = []
    for _ in range(rng.randint(10, 35)):
        junk = ["", "", "", '<w:pStyle w:val=""/>', '<w:pStyle w:val="Missing"/>', '<w:pStyle w:val="C1"/>']
        ps = rng.choice((junk if g.rough else ["", ""]) + [f'<w:pStyle w:val="{s}"/>' for s in STYLE_IDS] * 2)
        mark = rng.choice([""] * 12 + ['<w:del w:id="9" w:author="a" w:date="2026-01-01T00:00:00Z"/>',
                                       '<w:moveFrom w:id="9" w:author="a" w:date="2026-01-01T00:00:00Z"/>',
                                       '<w:ins w:id="9" w:author="a" w:date="2026-01-01T00:00:00Z"/>', "<w:vanish/>"])
        body.append(_p(rng.choice(TYPED_TEXTS), ps + g.numpr(num_ids), rng.random() < 0.08, mark))
    numbering = None if g.rough and rng.random() < 0.05 else "".join(abstracts + nums)
    level_lists = [rng.sample(STYLE_IDS + ["Missing", "C1", "s0", "Normal"], rng.randint(1, 4)) for _ in range(2)]
    level_lists += [["S0", "S1", "S2", "S3"][:rng.randint(1, 4)], ["s0", "S1", "S2"]]
    return mini_docx("".join(body), "".join(styles), numbering), level_lists


def _extra(rng):
    g = Gen(rng, rough=rng.random() < 0.5)
    a = etree.fromstring(f'<w:abstractNum xmlns:w="{W_NS}" xmlns:mc="http://schemas.openxmlformats.org/markup-'
                         f'compatibility/2006" w:abstractNumId="90">'
                         + "".join(g.level(str(k)) for k in range(rng.randint(1, 6))) + "</w:abstractNum>")
    n = etree.fromstring(f'<w:num xmlns:w="{W_NS}" w:numId="90"><w:abstractNumId w:val="90"/></w:num>')
    return a, n


def test_transform_and_verifier_agree_on_random_list_definitions():
    rng = random.Random(33)
    paragraphs = evaluated = schemes = typed = 0
    for case in range(450):
        data, level_lists = _rand_doc(rng)
        assert_sides_agree(data)
        t, v = TSide(data), VSide(data)
        paragraphs += len(t.pars)
        evaluated += sum(1 for x in t.count() if x.not_evaluated is None)
        # the planned output: an extra list and overrides
        a, n = _extra(rng)
        tdefs, vdefs = t.defs.with_extra([a], [n]), v.defs.with_extra([copy.deepcopy(a)], [copy.deepcopy(n)])
        override = {i: rng.choice([None, (90, rng.randint(0, 8)), (rng.randint(0, 9), rng.randint(0, 8))])
                    for i in rng.sample(range(len(t.pars)), min(len(t.pars), rng.randint(0, 8)))}
        assert [row(x) for x in t.count(override, tdefs)] == [row(x) for x in v.count(override, vdefs)], case
        for levels in level_lists:
            try:
                ts = tl.resolve_scheme(levels, t.sheet, t.defs)
            except JobException:
                ts = None
            try:
                vs = vl.v_resolve_scheme(levels, v.styles, v.defs)
            except ValueError:
                vs = None
            assert (ts is None) == (vs is None), (case, levels)
            if ts is None:
                continue
            schemes += 1
            assert [(l.style, l.num_id, l.ilvl, lvl_row(l.level)) for l in ts.levels] == \
                [(l.style, l.num_id, l.ilvl, lvl_row(l.level)) for l in vs.levels]
            samples = list(TYPED_TEXTS)
            for l in ts.levels:
                for value in (1, 2, 9, 10, 26, 27, 4):
                    shown, _ = tl.render(l.level.text, l.level.legal, lambda k: value,
                                         lambda k: ts.level_defs.get(k))
                    if shown is not None:
                        samples += [shown + sep + "x" for sep in ("\t", " ", " ", "", "\n")] + [shown, " " + shown]
            for s in samples:
                if typed_pair(s, (ts, vs)) is not None:
                    typed += 1
    assert paragraphs > 8000 and evaluated > 1500 and schemes > 20 and typed > 100, (paragraphs, evaluated, schemes,
                                                                                     typed)
