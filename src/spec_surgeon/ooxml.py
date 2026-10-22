"""OOXML helpers used by the TRANSFORM side (ops/). The verifier (verify/) must not import this
module — it keeps its own independent extraction so the two cannot share a failure mode.
"""
from __future__ import annotations

import copy
import datetime as _dt
import posixpath
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from lxml import etree

from .package import NS, W, R, DocxPackage

XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W16DU_DATEUTC = "{%s}dateUtc" % NS["w16du"]

# Children of w:r that render as (or anchor) something but carry no w:t text.
STRUCTURAL_RUN_CHILDREN = (
    "tab", "br", "cr", "noBreakHyphen", "softHyphen", "sym", "ptab",
    "footnoteReference", "endnoteReference", "commentReference", "drawing", "fldChar",
)

# Schema order of w:pPr children (complete, from the lessons: abbreviated tables have bitten).
PPR_ORDER = [
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
    "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
    "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
    "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle", "rPr",
    "sectPr", "pPrChange",
]

# Non-visual rPr children that frequently fragment runs in messy/OCR'd specs without changing appearance:
# proofing language, "no proofing", hidden-in-web-view. NOT in this set, although they also scatter: w:cs (switches
# the run to the complex-script font/size/weight), w:eastAsianLayout (combined/vertical East Asian text) and
# w:snapToGrid (character pitch under a document grid) — each can change what the reader sees.
NOISE_RPR_LOCALS = {"lang", "noProof", "webHidden"}
INERT_RUN_CHILDREN = {W + "lastRenderedPageBreak", W + "proofErr"}


def flexible_regex_from_literal(find: str) -> str:
    """Turn a literal search phrase into a regex that tolerates whitespace/NBSP runs ([\\s\\u00a0]+),
    hyphen/en-dash/em-dash variations ([-–—]), and straight/curly quotes."""
    tokens = []
    i = 0
    n = len(find)
    while i < n:
        ch = find[i]
        if ch.isspace() or ch == "\u00a0":
            while i < n and (find[i].isspace() or find[i] == "\u00a0"):
                i += 1
            tokens.append(r"[\s\u00a0]+")
        elif ch in "-–—":
            tokens.append(r"[-–—]")
            i += 1
        elif ch in "'’‘":
            tokens.append(r"['’‘]")
            i += 1
        elif ch in '"“”':
            tokens.append(r'["“”]')
            i += 1
        else:
            tokens.append(re.escape(ch))
            i += 1
    return "".join(tokens)


# ---- text views -----------------------------------------------------------------------------------

def run_text(r: etree._Element) -> str:
    """Text of a run as Word draws it (w:t and w:delText; instrText excluded — it is not drawn)."""
    out = []
    for child in r:
        if child.tag in (W + "t", W + "delText"):
            out.append(child.text or "")
        elif child.tag == W + "tab":
            out.append("\t")
        elif child.tag in (W + "br", W + "cr"):
            out.append("\n")
    return "".join(out)


def paragraph_marked_text(p: etree._Element) -> str:
    """Paragraph text with {+inserted+} and [-deleted-] markers — the discovery dump."""
    parts: List[str] = []

    def walk(el, ins, dele):
        for child in el:
            tag = child.tag
            if tag == W + "r":
                t = run_text(child)
                if not t:
                    continue
                if dele:
                    parts.append("[-" + t + "-]")
                elif ins:
                    parts.append("{+" + t + "+}")
                else:
                    parts.append(t)
            elif tag == W + "ins":
                walk(child, True, dele)
            elif tag == W + "del":
                walk(child, ins, True)
            elif tag in (W + "hyperlink", W + "smartTag", W + "sdt", W + "sdtContent", W + "fldSimple",
                         W + "customXml", W + "dir", W + "bdo"):
                walk(child, ins, dele)
            # mc:AlternateContent etc. are not descended here: textboxes hold their own w:p elements
    walk(p, False, False)
    return "".join(parts)


M_T = "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"
FULL_TEXT_TAGS = (W + "t", W + "delText", W + "instrText", W + "delInstrText", M_T)


def block_full_text(p: etree._Element) -> str:
    """The block text as the verifier defines it (w:t, w:delText, w:instrText, w:delInstrText, m:t in
    document order, excluding nested paragraphs). Plans must express before/after in these terms."""
    parts = []
    for el in p.iter(*FULL_TEXT_TAGS):
        q = el.getparent()
        nested = False
        while q is not None and q is not p:
            if q.tag == W + "p":
                nested = True
                break
            q = q.getparent()
        if not nested:
            parts.append(el.text or "")
    return "".join(parts)


def block_struct_counts(p: etree._Element, without: Iterable[etree._Element] = ()) -> Dict[str, int]:
    """The block's structural children by local name (CLAUDE.md "Block-text definition"): STRUCTURAL_RUN_CHILDREN whose
    nearest w:p is ``p`` — a w:tab only when its parent is a w:r (tab-stop definitions share the name) — leaving out
    any that lies in (or is) one of ``without`` (the elements a plan predicts gone). Zero counts are omitted."""
    gone = {id(e) for e in without}
    out: Dict[str, int] = {}
    for el in p.iter():
        if not isinstance(el.tag, str) or not el.tag.startswith(W):
            continue
        local = el.tag[len(W):]
        if local not in STRUCTURAL_RUN_CHILDREN:
            continue
        if local == "tab" and (el.getparent() is None or el.getparent().tag != W + "r"):
            continue
        mine, in_gone = _belongs(el, p, gone)
        if mine and not in_gone:
            out[local] = out.get(local, 0) + 1
    return out


def _belongs(el: etree._Element, p: etree._Element, gone: set) -> Tuple[bool, bool]:
    """(the element's nearest w:p is ``p``, the element or an ancestor below ``p`` is in ``gone``)."""
    q, in_gone = el, False
    while q is not None and q is not p:
        if id(q) in gone:
            in_gone = True
        q = q.getparent()
        if q is not None and q.tag == W + "p" and q is not p:
            return False, in_gone                      # a nested paragraph's own (a text box): its own block
    return q is p, in_gone


def block_text_without(p: etree._Element, without: Iterable[etree._Element] = ()) -> str:
    """The block text (``block_full_text``) leaving out the text elements that lie in (or are) one of ``without``."""
    gone = {id(e) for e in without}
    parts = []
    for el in p.iter(*FULL_TEXT_TAGS):
        mine, in_gone = _belongs(el, p, gone)
        if mine and not in_gone:
            parts.append(el.text or "")
    return "".join(parts)


def paragraphs(root: etree._Element) -> List[etree._Element]:
    """All w:p in document order, including inside tables, sdt, and textboxes (both mc branches)."""
    return list(root.iter(W + "p"))


# ---- revision bookkeeping ----------------------------------------------------------------------

def max_revision_id(pkg: DocxPackage) -> int:
    """The highest numeric w:id on any element of any XML part: new revision ids count up from it.

    w:id values must be unique across the document, and no list of revision tags stays complete (one that left out
    w:tblGridChange and w:tblPrExChange handed out an id the file already carried, and V4 set the file aside). So
    every w:id counts, in every XML part (styles and numbering can carry property changes too): a bookmark's, a
    comment's or a note's id only raises the seed, which is harmless. A part that is not well-formed XML is skipped
    (it is kept byte for byte); a text part that is not still raises, as everywhere else."""
    text = set(pkg.text_parts())
    defaults, overrides = pkg.content_types()
    mx = 0
    for name in pkg.member_names:
        media_type = overrides.get(name, defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())) or ""
        if name not in text and not name.endswith(".xml") and not media_type.endswith(("+xml", "/xml")):
            continue
        try:
            root = pkg.xml(name)
        except etree.XMLSyntaxError:
            if name in text:
                raise
            continue
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            v = el.get(W + "id")
            if v is not None:
                try:
                    mx = max(mx, int(v))
                except ValueError:
                    pass
    return mx


def file_uses_date_utc(pkg: DocxPackage) -> bool:
    for part in pkg.text_parts():
        root = pkg.xml(part)
        for el in root.iter(W + "ins", W + "del"):
            if el.get(W16DU_DATEUTC) is not None:
                return True
    return False


@dataclass
class RevisionStamp:
    author: str
    date_local_z: str   # Word's convention: local wall-clock time with a 'Z' suffix
    date_utc: str       # true UTC
    use_date_utc: bool  # whether the file carries w16du:dateUtc on its revisions

    @classmethod
    def now(cls, author: str, use_date_utc: bool, convention: str = "word-local-z") -> "RevisionStamp":
        utc = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
        local = utc.astimezone().replace(tzinfo=None)
        utc_s = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        if convention == "utc":
            local_s = utc_s
        else:
            local_s = local.strftime("%Y-%m-%dT%H:%M:%SZ")
        return cls(author=author, date_local_z=local_s, date_utc=utc_s, use_date_utc=use_date_utc)

    def apply(self, el: etree._Element, rev_id: int) -> None:
        el.set(W + "id", str(rev_id))
        el.set(W + "author", self.author)
        el.set(W + "date", self.date_local_z)
        if self.use_date_utc:
            el.set(W16DU_DATEUTC, self.date_utc)


# ---- run surgery ------------------------------------------------------------------------------

def set_text(t: etree._Element, text: str) -> None:
    """Write text into a w:t / w:delText, preserving edge whitespace (silent single-character loss
    otherwise — the failure mode this whole tool exists to prevent)."""
    t.text = text
    if text != text.strip() or " " in text or "  " in text:
        t.set(XML_SPACE, "preserve")
    elif t.get(XML_SPACE) == "preserve":
        pass  # leaving an existing preserve is harmless


def clone_run_shell(r: etree._Element) -> etree._Element:
    """A copy of the run with its attributes and rPr but no content children."""
    new = etree.Element(W + "r", nsmap=r.nsmap)
    for k, v in r.attrib.items():
        new.set(k, v)
    rpr = r.find(W + "rPr")
    if rpr is not None:
        new.append(copy.deepcopy(rpr))
    return new


def split_run_at_text(r: etree._Element, t: etree._Element, start: int, end: int
                      ) -> Tuple[Optional[etree._Element], etree._Element, Optional[etree._Element]]:
    """Split run ``r`` so that the substring [start:end) of its child ``t`` becomes its own run.

    Returns (before, target, after); ``before``/``after`` are None when empty. Non-text children
    (w:tab, w:br, …) are carried into the fragment they belong to, in order; rPr and attributes are
    copied onto every fragment (lesson: splitting drops them unless you carry them)."""
    text = t.text or ""
    assert 0 <= start < end <= len(text), (start, end, len(text))
    children = [c for c in r if c.tag != W + "rPr"]
    idx = children.index(t)
    before = clone_run_shell(r)
    target = clone_run_shell(r)
    after = clone_run_shell(r)
    for c in children[:idx]:
        before.append(copy.deepcopy(c))
    if start > 0:
        bt = etree.SubElement(before, t.tag)
        set_text(bt, text[:start])
    tt = etree.SubElement(target, t.tag)
    set_text(tt, text[start:end])
    if end < len(text):
        at = etree.SubElement(after, t.tag)
        set_text(at, text[end:])
    for c in children[idx + 1:]:
        after.append(copy.deepcopy(c))

    def nonempty(run):
        return any(c.tag != W + "rPr" for c in run)

    return (before if nonempty(before) else None, target, after if nonempty(after) else None)


def to_del_text(run: etree._Element) -> None:
    for t in run.findall(W + "t"):
        t.tag = W + "delText"
        if t.text and t.text != t.text.strip():
            t.set(XML_SPACE, "preserve")


def ancestor(el: etree._Element, tag: str, stop: etree._Element) -> Optional[etree._Element]:
    cur = el.getparent()
    while cur is not None and cur is not stop:
        if cur.tag == tag:
            return cur
        cur = cur.getparent()
    return None


# ---- paragraph-local run merge ---------------------------------------------------------------

def rpr_key(r: etree._Element, ignore_noise: bool = False) -> bytes:
    rpr = r.find(W + "rPr")
    if rpr is None:
        return b""
    c = copy.deepcopy(rpr)
    if ignore_noise:
        for child in list(c):
            if isinstance(child.tag, str) and child.tag.startswith(W):
                local = child.tag[len(W):]
                if local in NOISE_RPR_LOCALS:
                    c.remove(child)
                elif local == "color" and child.get(W + "val", "").lower() in ("000000", "auto") and len(child.attrib) == 1:
                    c.remove(child)
                elif local == "szCs":
                    sz = c.find(W + "sz")
                    if sz is not None and sz.get(W + "val") == child.get(W + "val"):
                        c.remove(child)
    # rsid attributes never affect rendering; strip for comparison only
    for el in c.iter():
        for a in list(el.attrib):
            if "rsid" in a:
                del el.attrib[a]
    if len(c) == 0 and not c.attrib:
        return b""
    return etree.tostring(c)


_MERGEABLE = (W + "rPr", W + "t", W + "tab", W + "br", W + "cr", W + "noBreakHyphen", W + "softHyphen")
_MERGEABLE_NOISE = _MERGEABLE + tuple(INERT_RUN_CHILDREN)


def merge_adjacent_runs(p: etree._Element, ignore_noise: bool = False) -> int:
    """Merge adjacent sibling runs with identical rPr under the same parent (so runs in different
    w:ins/w:del wrappers are never merged). When ``ignore_noise`` is True, non-visual rPr noise (w:lang,
    w:noProof, w:webHidden, redundant w:szCs/auto color) is ignored during comparison, and the inert
    w:proofErr / w:lastRenderedPageBreak markers that sit between (or inside) two runs that DO merge are
    removed with the merge; markers elsewhere in the paragraph are left exactly as they were."""
    merges = 0
    allowed_tags = _MERGEABLE_NOISE if ignore_noise else _MERGEABLE

    def mergeable(r) -> bool:
        return r.tag == W + "r" and all(c.tag in allowed_tags for c in r)

    for parent in [p] + [el for el in p.iter() if el.tag in (W + "ins", W + "hyperlink", W + "sdtContent")]:
        kids = list(parent)
        i = 0
        while i < len(kids) - 1:
            a = kids[i]
            if not mergeable(a):
                i += 1
                continue
            j = i + 1
            between = []                       # inert markers separating a from the next run (noise mode only)
            while ignore_noise and j < len(kids) and kids[j].tag in INERT_RUN_CHILDREN:
                between.append(kids[j])
                j += 1
            if j >= len(kids):
                break
            b = kids[j]
            if mergeable(b) and rpr_key(a, ignore_noise=ignore_noise) == rpr_key(b, ignore_noise=ignore_noise):
                for el in between:
                    parent.remove(el)
                if ignore_noise:
                    for r_el in (a, b):
                        for ch in list(r_el):
                            if ch.tag in INERT_RUN_CHILDREN:
                                r_el.remove(ch)
                a_content = [c for c in a if c.tag != W + "rPr"]
                b_content = [c for c in b if c.tag != W + "rPr"]
                if a_content and b_content and a_content[-1].tag == W + "t" and b_content[0].tag == W + "t":
                    set_text(a_content[-1], (a_content[-1].text or "") + (b_content[0].text or ""))
                    b_content = b_content[1:]
                for c in b_content:
                    a.append(c)
                parent.remove(b)
                kids = list(parent)
                merges += 1
                continue                       # a may merge with the run after b as well
            i += 1
    return merges


def insert_ppr_child_in_order(ppr: etree._Element, child: etree._Element) -> None:
    name = etree.QName(child).localname
    rank = PPR_ORDER.index(name) if name in PPR_ORDER else len(PPR_ORDER)
    for i, existing in enumerate(ppr):
        ename = etree.QName(existing).localname
        erank = PPR_ORDER.index(ename) if ename in PPR_ORDER else len(PPR_ORDER)
        if erank > rank:
            ppr.insert(i, child)
            return
    ppr.append(child)


# ---- revision counting (must stay identical to verify/suite.py's _rev_counts) ----------------------

CONTENT_REV_TAGS = ("ins", "del", "moveFrom", "moveTo")
PROPERTY_REV_TAGS = ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
                     "tblGridChange", "numberingChange")
CELL_REV_TAGS = ("cellIns", "cellDel", "cellMerge")


def revision_kind(el: etree._Element) -> Optional[str]:
    """V4's revision kinds: w:ins/w:del/w:moveFrom/w:moveTo as content wrappers (``ins`` …) or, when their
    parent is a w:rPr, as paragraph-mark revisions (``ins_mark`` …); property changes and table-cell
    revisions by their local name. None for anything else."""
    if not isinstance(el.tag, str) or not el.tag.startswith(W):
        return None
    local = el.tag[len(W):]
    if local in CONTENT_REV_TAGS:
        parent = el.getparent()
        return local + "_mark" if parent is not None and parent.tag == W + "rPr" else local
    if local in PROPERTY_REV_TAGS or local in CELL_REV_TAGS:
        return local
    return None


def count_revisions(el: etree._Element) -> Dict[str, int]:
    """Revision elements in the subtree rooted at ``el`` (inclusive), by kind."""
    out: Dict[str, int] = {}
    for e in el.iter():
        k = revision_kind(e)
        if k:
            out[k] = out.get(k, 0) + 1
    return out


# ---- schema order tables + property setters (formatting ops) --------------------------------------

# w:rPr children (CT_ParaRPr ∪ CT_RPr, EG_RPrBase in schema order). Extension-namespace children rank last
# before rPrChange, which is always the final child.
RPR_ORDER = [
    "ins", "del", "moveFrom", "moveTo", "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden", "color",
    "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText",
    "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange",
]
# w:sectPr children (CT_SectPr); header/footer references share the first slot.
SECTPR_ORDER = [
    "headerReference", "footnotePr", "endnotePr", "type", "pgSz", "pgMar", "paperSrc", "pgBorders", "lnNumType",
    "pgNumType", "cols", "formProt", "vAlign", "noEndnote", "titlePg", "textDirection", "bidi", "rtlGutter",
    "docGrid", "printerSettings", "sectPrChange",
]
# Tables (built-in batch 5, items 37–38). w:tblPr children (CT_TblPr = CT_TblPrBase's sequence + tblPrChange).
TBLPR_ORDER = [
    "tblStyle", "tblpPr", "tblOverlap", "bidiVisual", "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
    "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout", "tblCellMar", "tblLook", "tblCaption",
    "tblDescription", "tblPrChange",
]
# w:trPr children: CT_TrPrBase is a choice (any order is valid); written in the order Word writes them (the schema's
# listing), then CT_TrPr's own sequence (ins, del, trPrChange).
TRPR_ORDER = [
    "cnfStyle", "divId", "gridBefore", "gridAfter", "wBefore", "wAfter", "cantSplit", "trHeight", "tblHeader",
    "tblCellSpacing", "jc", "hidden", "ins", "del", "trPrChange",
]
# w:tcPr children (CT_TcPr = CT_TcPrBase + EG_CellMarkupElements + tcPrChange)
TCPR_ORDER = [
    "cnfStyle", "tcW", "gridSpan", "hMerge", "vMerge", "tcBorders", "shd", "noWrap", "tcMar", "textDirection",
    "tcFitText", "vAlign", "hideMark", "headers", "cellIns", "cellDel", "cellMerge", "tcPrChange",
]
# w:tblBorders sides (CT_TblBorders) and w:tblCellMar sides (CT_TblCellMar), schema order
TBL_BORDERS_ORDER = ["top", "start", "left", "bottom", "end", "right", "insideH", "insideV"]
TBL_CELLMAR_ORDER = ["top", "start", "left", "bottom", "end", "right"]
# w:style children (CT_Style)
STYLE_ORDER = [
    "name", "aliases", "basedOn", "next", "link", "autoRedefine", "hidden", "uiPriority", "semiHidden",
    "unhideWhenUsed", "qFormat", "locked", "personal", "personalCompose", "personalReply", "rsid", "pPr", "rPr",
    "tblPr", "trPr", "tcPr", "tblStylePr",
]

FONT_SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")
THEME_FONT_ATTR = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "eastAsia": "eastAsiaTheme", "cs": "cstheme"}


def _rank(el: etree._Element, order: List[str]) -> int:
    if not isinstance(el.tag, str) or not el.tag.startswith(W):
        return len(order) - 1.5      # extension elements: after every w: child except the last (…Change)
    name = el.tag[len(W):]
    if name == "footerReference" and "headerReference" in order:
        name = "headerReference"
    return order.index(name) if name in order else len(order) - 1.5


def insert_in_order(parent: etree._Element, child: etree._Element, order: List[str]) -> etree._Element:
    """Insert ``child`` before the first existing child that ranks after it in the schema ``order``."""
    rank = _rank(child, order)
    for i, existing in enumerate(parent):
        if _rank(existing, order) > rank:
            parent.insert(i, child)
            return child
    parent.append(child)
    return child


def get_or_add(parent: etree._Element, local: str, order: List[str]) -> etree._Element:
    el = parent.find(W + local)
    if el is None:
        el = insert_in_order(parent, etree.Element(W + local), order)
    return el


def run_rpr(run: etree._Element, create: bool = True) -> Optional[etree._Element]:
    """The run's w:rPr (always its first child), created when asked."""
    rpr = run.find(W + "rPr")
    if rpr is None and create:
        rpr = etree.Element(W + "rPr")
        run.insert(0, rpr)
    return rpr


def mark_rpr(p: etree._Element, create: bool = True) -> Optional[etree._Element]:
    """The paragraph mark's run properties (w:pPr/w:rPr), created in schema order when asked."""
    ppr = p.find(W + "pPr")
    if ppr is None:
        if not create:
            return None
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    rpr = ppr.find(W + "rPr")
    if rpr is None and create:
        rpr = insert_in_order(ppr, etree.Element(W + "rPr"), PPR_ORDER)
    return rpr


def paragraph_ppr(p: etree._Element) -> etree._Element:
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    return ppr


def set_rfonts(rpr: etree._Element, values: Dict[str, str], clear_theme: bool) -> None:
    """Set the named font slots on rPr/rFonts (created in schema order). Theme attributes override explicit
    names in Word, so the theme attribute of every slot being set is removed when ``clear_theme``."""
    rf = get_or_add(rpr, "rFonts", RPR_ORDER)
    for slot, font in values.items():
        rf.set(W + slot, font)
        if clear_theme:
            rf.attrib.pop(W + THEME_FONT_ATTR[slot], None)


def own_size(rpr: Optional[etree._Element]) -> Optional[int]:
    """The font size (half-points) this rPr itself sets — its w:sz/@w:val as a whole number — or None."""
    sz = rpr.find(W + "sz") if rpr is not None else None
    val = sz.get(W + "val") if sz is not None else None
    return int(val) if val is not None and val.isdigit() else None


def set_size(rpr: etree._Element, half_points: int) -> None:
    """Set w:sz (created in schema order when absent). w:szCs (complex-script size) is left alone."""
    get_or_add(rpr, "sz", RPR_ORDER).set(W + "val", str(half_points))


def property_change(tag: str, snapshot_tag: str, children: Iterable[etree._Element], stamp: "RevisionStamp",
                    rev_id: int) -> etree._Element:
    """A w:rPrChange / w:pPrChange carrying a snapshot of the properties before the change."""
    chg = etree.Element(W + tag)
    stamp.apply(chg, rev_id)
    snap = etree.SubElement(chg, W + snapshot_tag)
    for c in children:
        snap.append(copy.deepcopy(c))
    return chg
