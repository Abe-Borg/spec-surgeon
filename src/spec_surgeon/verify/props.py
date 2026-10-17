"""V10 — property assertions and formatting preservation.

Two halves, both computed here from the before/after packages and the plan's declarative assertions, with
this module's OWN selector resolution, style-chain walking and theme lookup (nothing shared with ops/ or
ooxml.py — a selector bug on the transform side must not be able to hide behind the same bug here):

1. **Assertions.** Each ``plan["property_assertions"]`` entry names a kind (run_fonts, style_fonts,
   doc_defaults_fonts, paragraph_props, section_props, section_hf_refs, effective_font, deleted_paragraphs,
   inserted_paragraphs, properties), a selector, the property values and the predicted target count. The selection is
   re-resolved here; the count must match the plan, and every selected target must carry exactly the asserted
   change — no more (every other child and attribute identical to the source) and, for tracked changes, a
   w:rPrChange / w:pPrChange whose snapshot is the source's properties.

2. **Residual identity.** For every text part present on both sides and for the styles part, strip what the
   assertions cover (the selected runs' rPr, the selected sectPr children, …) and remove the blocks whose text
   the plan predicted to change; what remains must be identical, element for element, attribute for
   attribute. Blocks with predicted text changes must keep their pPr and introduce no run-property signature
   the source block did not have.

An assertion kind this module does not know is a FAIL, never a skip.
"""
from __future__ import annotations

import copy
import difflib
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import VPackage, W, M, R, STYLES_REL, THEME_REL, protected_members
from .textcheck import blocks

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
A = "{%s}" % A_NS
TEXT_TAGS = {W + "t", W + "delText", W + "instrText", W + "delInstrText", M + "t"}
REVISION_WRAPPERS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo"}
SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")
THEME_ATTR = {"ascii": "asciiTheme", "hAnsi": "hAnsiTheme", "eastAsia": "eastAsiaTheme", "cs": "cstheme"}
FONT_ATTRS = SLOTS + tuple(THEME_ATTR.values())
FALSY = ("0", "false", "off")


def _local(el) -> str:
    return el.tag[len(W):] if isinstance(el.tag, str) and el.tag.startswith(W) else ""


def sig(el) -> Any:
    """A comparable structural signature: tag, attributes (order-free), significant text, children in order.
    Whitespace outside text-bearing elements and tails are not significant in WordprocessingML."""
    if el is None:
        return None
    if not isinstance(el.tag, str):
        return ("#node", (el.text or "").strip())
    text = el.text or ""
    if el.tag not in TEXT_TAGS:
        text = text.strip()
    return (el.tag, tuple(sorted(el.attrib.items())), text, tuple(sig(c) for c in el))


def _rpr_sig_for_tc(rpr) -> Any:
    """Signature of a run's rPr inside a text-edited block: normalizes w:rPrChange/@w:id when a run carrying
    rPrChange was split (V4 and V5c check revision id uniqueness and snapshot grammar)."""
    if rpr is None:
        return None
    c = copy.deepcopy(rpr)
    for chg in c.findall(W + "rPrChange"):
        if W + "id" in chg.attrib:
            chg.set(W + "id", "#")
    return sig(c)


def _name(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else "#node"


def first_difference(a, b, path="") -> Optional[str]:
    """Where two trees first differ (same significance rules as sig()), or None. The place is said twice: as a path
    of child positions, and as a reader finds it — the text of the paragraph it is in (the first tree's), or the id of
    the style."""
    if a is None or b is None:
        return None if a is b else f"{path}: element present on one side only"
    here = f"{path}/{_name(a)}"
    if a.tag != b.tag:
        return f"{path}: <{_name(a)}> vs <{_name(b)}>"
    if not isinstance(a.tag, str):
        return None if (a.text or "").strip() == (b.text or "").strip() else f"{here}: comment/PI changed"
    if dict(a.attrib) != dict(b.attrib):
        ka, kb = dict(a.attrib), dict(b.attrib)
        keys = sorted(set(ka) | set(kb))
        diff = [f"{etree.QName(k).localname}: {ka.get(k)!r}→{kb.get(k)!r}" for k in keys if ka.get(k) != kb.get(k)]
        return _where(a, f"{here}: attributes {', '.join(diff)}")
    ta, tb = a.text or "", b.text or ""
    if a.tag not in TEXT_TAGS:
        ta, tb = ta.strip(), tb.strip()
    if ta != tb:
        return _where(a, f"{here}: text {ta!r}→{tb!r}")
    ca, cb = list(a), list(b)
    for i in range(min(len(ca), len(cb))):
        d = first_difference(ca[i], cb[i], f"{here}[{i}]")
        if d:
            return _where(a, d)
    if len(ca) != len(cb):
        extra = cb[len(ca):] if len(cb) > len(ca) else ca[len(cb):]
        names = [_name(e) for e in extra]
        return _where(a, f"{here}: {len(ca)}→{len(cb)} children ({'added' if len(cb) > len(ca) else 'removed'} "
                         f"{names[:5]})")
    return None


def _where(el, msg: str) -> str:
    """``msg`` with the innermost paragraph's text or style's id it lies in (added once, by the innermost one)."""
    if " (in paragraph " in msg or " (in an empty paragraph)" in msg or " (in style " in msg:
        return msg
    if el.tag == W + "p":
        text = " ".join("".join(t.text or "" for t in el.iter(W + "t", W + "delText")).split())
        return f"{msg} (in paragraph “{text[:79] + '…' if len(text) > 80 else text}”)" if text else \
            f"{msg} (in an empty paragraph)"
    if el.tag == W + "style":
        return f"{msg} (in style {el.get(W + 'styleId')!r})"
    return msg


def _nearest_p(el):
    q = el.getparent()
    while q is not None:
        if q.tag == W + "p":
            return q
        q = q.getparent()
    return None


def _has_ancestor(el, tags: Set[str], stop) -> bool:
    q = el.getparent()
    while q is not None and q is not stop:
        if q.tag in tags:
            return True
        q = q.getparent()
    return False


# ---- per-part view ---------------------------------------------------------------------------------

class PartView:
    def __init__(self, root):
        self.root = root
        self.pars = list(root.iter(W + "p"))
        index = {id(p): i for i, p in enumerate(self.pars)}
        self.texts = [b.text for b in blocks(root)]
        self.runs: List[List[Any]] = [[] for _ in self.pars]
        for r in root.iter(W + "r"):
            p = _nearest_p(r)
            if p is not None:
                self.runs[index[id(p)]].append(r)


def align(a: Sequence[str], b: Sequence[str]) -> Dict[int, int]:
    """Source block index → output block index, aligned on block text (equal runs and equal-length replaces)."""
    out = {}
    sm = difflib.SequenceMatcher(a=list(a), b=list(b), autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                out[i1 + k] = j1 + k
    return out


# ---- styles and theme ------------------------------------------------------------------------------

class Styles:
    def __init__(self, root):
        self.root = root
        self.by_id: Dict[str, Any] = {}
        self.default: Dict[str, str] = {}
        if root is None:
            return
        for st in root.findall(W + "style"):
            sid = st.get(W + "styleId")
            if sid is None:
                continue
            self.by_id[sid] = st
            if st.get(W + "default") in ("1", "true", "on"):
                self.default.setdefault(st.get(W + "type", "paragraph"), sid)

    def chain(self, sid: Optional[str]) -> List[Any]:
        """The style and its basedOn ancestors, nearest first (cycle-safe)."""
        out, seen = [], set()
        while sid and sid in self.by_id and sid not in seen:
            seen.add(sid)
            st = self.by_id[sid]
            out.append(st)
            b = st.find(W + "basedOn")
            sid = b.get(W + "val") if b is not None else None
        return out

    def doc_defaults_rpr(self):
        if self.root is None:
            return None
        return self.root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")

    def doc_defaults_ppr(self):
        if self.root is None:
            return None
        return self.root.find(W + "docDefaults/" + W + "pPrDefault/" + W + "pPr")


class Theme:
    def __init__(self, root):
        self.fonts: Dict[str, str] = {}
        if root is None:
            return
        for which in ("major", "minor"):
            f = root.find(f".//{A}{which}Font")
            if f is None:
                continue
            for script in ("latin", "ea", "cs"):
                e = f.find(A + script)
                if e is not None:
                    self.fonts[f"{which}:{script}"] = e.get("typeface", "")

    def resolve(self, value: str) -> str:
        m = re.match(r"(major|minor)(HAnsi|Ascii|EastAsia|Bidi)$", value or "")
        if not m:
            return f"theme:{value}"
        script = {"HAnsi": "latin", "Ascii": "latin", "EastAsia": "ea", "Bidi": "cs"}[m.group(2)]
        return self.fonts.get(f"{m.group(1)}:{script}", f"theme:{value}")


def _rfonts(rpr):
    return rpr.find(W + "rFonts") if rpr is not None else None


def _explicit_names(rpr) -> List[str]:
    rf = _rfonts(rpr)
    if rf is None:
        return []
    return [rf.get(W + s) for s in SLOTS if rf.get(W + s)]


def _pstyle(p) -> Optional[str]:
    ps = p.find(W + "pPr/" + W + "pStyle")
    return ps.get(W + "val") if ps is not None else None


def _rstyle(rpr) -> Optional[str]:
    if rpr is None:
        return None
    rs = rpr.find(W + "rStyle")
    return rs.get(W + "val") if rs is not None else None


def symbolic(rpr, p, styles: Styles, preserve: Set[str]) -> bool:
    """True when any explicit font name (any slot) in the run's direct rFonts, its character-style chain, or
    its paragraph's style chain (default paragraph style when none) is a preserved (symbol) font."""
    names = list(_explicit_names(rpr))
    for st in styles.chain(_rstyle(rpr)):
        names += _explicit_names(st.find(W + "rPr"))
    if p is not None:
        for st in styles.chain(_pstyle(p) or styles.default.get("paragraph")):
            names += _explicit_names(st.find(W + "rPr"))
    return any(n.casefold() in preserve for n in names)


def symbolic_style(st, styles: Styles, preserve: Set[str]) -> bool:
    sid = st.get(W + "styleId")
    names = []
    for s in styles.chain(sid):
        names += _explicit_names(s.find(W + "rPr"))
    return any(n.casefold() in preserve for n in names)


def effective_fonts(rpr, p, styles: Styles, theme: Theme) -> Dict[str, Optional[str]]:
    """Per-slot font a renderer uses for this run (or paragraph mark): docDefaults → table style → paragraph
    style chain → character style chain → direct formatting; within one rFonts a theme attribute beats the
    explicit name. Conditional table-style formatting (tblStylePr) is not modelled."""
    levels = [_rfonts(styles.doc_defaults_rpr())]
    tbl = p
    while tbl is not None and tbl.tag != W + "tbl":
        tbl = tbl.getparent()
    if tbl is not None:
        ts = tbl.find(W + "tblPr/" + W + "tblStyle")
        tsid = ts.get(W + "val") if ts is not None else styles.default.get("table")
        levels += [_rfonts(st.find(W + "rPr")) for st in reversed(styles.chain(tsid))]
    if p is not None:
        levels += [_rfonts(st.find(W + "rPr")) for st in reversed(styles.chain(_pstyle(p) or styles.default.get("paragraph")))]
    levels += [_rfonts(st.find(W + "rPr")) for st in reversed(styles.chain(_rstyle(rpr) or styles.default.get("character")))]
    levels.append(_rfonts(rpr))
    out: Dict[str, Optional[str]] = {s: None for s in SLOTS}
    for rf in levels:
        if rf is None:
            continue
        for s in SLOTS:
            th = rf.get(W + THEME_ATTR[s])
            if th:
                out[s] = theme.resolve(th)
            elif rf.get(W + s):
                out[s] = rf.get(W + s)
    return out


def effective_size(rpr, p, styles: Styles) -> int:
    """Half-points a renderer uses for this run (or paragraph mark), same level order as ``effective_fonts``; 20
    (10 pt) when no level sets w:sz."""
    levels = [styles.doc_defaults_rpr()]
    tbl = p
    while tbl is not None and tbl.tag != W + "tbl":
        tbl = tbl.getparent()
    if tbl is not None:
        ts = tbl.find(W + "tblPr/" + W + "tblStyle")
        levels += [st.find(W + "rPr") for st in reversed(styles.chain(ts.get(W + "val") if ts is not None
                                                                       else styles.default.get("table")))]
    if p is not None:
        levels += [st.find(W + "rPr") for st in reversed(styles.chain(_pstyle(p) or styles.default.get("paragraph")))]
    levels += [st.find(W + "rPr") for st in reversed(styles.chain(_rstyle(rpr) or styles.default.get("character")))]
    levels.append(rpr)
    out = DEFAULT_SZ
    for L in levels:
        e = L.find(W + "sz") if L is not None else None
        v = e.get(W + "val") if e is not None else None
        if v is not None and v.isdigit():
            out = int(v)
    return out


# ---- selector resolution (independent of ops/selectors.py) ------------------------------------------

PARA_KEYS = {"style_in", "text_regex", "in_table", "is_empty", "index_range",
             "has_bold", "is_all_caps", "after_heading_regex", "between_paragraphs"}
RUN_KEYS = {"has_direct_font", "font_in", "not_in_revisions", "color_in"}


def _v_selector_style(p, styles: Styles) -> Optional[str]:
    """The paragraph style the selector grammar reads: ``w:pStyle``'s value when the paragraph has a ``w:pStyle`` — an
    empty or missing value names no style (batch 4's definition) —, else the default paragraph style. (Batch 5's review:
    ``_pstyle(p) or default`` read an empty pStyle as the default style; the transform reads it as none — a section
    chosen by ``style_in`` differed between the sides and a valid plan failed V10.)"""
    ps = p.find(W + "pPr/" + W + "pStyle")
    return ps.get(W + "val") if ps is not None else styles.default.get("paragraph")


def _v_toggle_on(rpr, p, styles: Styles, tag: str) -> bool:
    if rpr is not None:
        d = rpr.find(W + tag)
        if d is not None:
            return d.get(W + "val") not in FALSY
        for st in styles.chain(_rstyle(rpr)):
            sr = st.find(W + "rPr")
            if sr is not None and sr.find(W + tag) is not None:
                return sr.find(W + tag).get(W + "val") not in FALSY
    for st in styles.chain(_v_selector_style(p, styles)):
        sr = st.find(W + "rPr")
        if sr is not None and sr.find(W + tag) is not None:
            return sr.find(W + tag).get(W + "val") not in FALSY
    return False


def _v_live_runs(p, view_runs: List[Any]) -> List[Any]:
    return [r for r in view_runs if not _has_ancestor(r, {W + "del", W + "moveFrom"}, p)]


def _v_para_has_bold(p, view_runs: List[Any], styles: Styles) -> bool:
    for r in _v_live_runs(p, view_runs):
        txt = "".join(t.text or "" for t in r.findall(W + "t"))
        if txt.strip() and _v_toggle_on(r.find(W + "rPr"), p, styles, "b"):
            return True
    return False


def _v_para_is_all_caps(p, view_runs: List[Any], styles: Styles) -> bool:
    lruns = _v_live_runs(p, view_runs)
    live_txt = "".join(t.text or "" for r in lruns for t in r.findall(W + "t")).strip()
    if not live_txt or not any(c.isalpha() for c in live_txt):
        return False
    if live_txt == live_txt.upper():
        return True
    return any("".join(t.text or "" for t in r.findall(W + "t")).strip()
               and _v_toggle_on(r.find(W + "rPr"), p, styles, "caps") for r in lruns)


def _v_block_text_with_tabs(p) -> str:
    parts = []
    for el in p.iter(*TEXT_TAGS, W + "tab"):
        if el.tag == W + "tab" and (el.getparent() is None or el.getparent().tag != W + "r"):
            continue
        if _nearest_p(el) is p:
            parts.append("\t" if el.tag == W + "tab" else (el.text or ""))
    return "".join(parts).replace("\u00a0", " ")


def _v_rx_matches(pattern: str, raw_text: str, p) -> bool:
    rx = re.compile(pattern)
    if rx.search(raw_text) is not None:
        return True
    return rx.search(_v_block_text_with_tabs(p)) is not None

def para_matches(pred, p, bi: int, view: PartView, styles: Styles) -> bool:
    if pred in (None, "all") or pred == {}:
        return True
    if not isinstance(pred, dict) or set(pred) - PARA_KEYS:
        raise ValueError(f"unknown paragraph selector {pred!r}")
    if "style_in" in pred and _v_selector_style(p, styles) not in pred["style_in"]:
        return False
    if "text_regex" in pred and not _v_rx_matches(pred["text_regex"], view.texts[bi], p):
        return False
    if "in_table" in pred and _has_ancestor(p, {W + "tc"}, None) != bool(pred["in_table"]):
        return False
    if "is_empty" in pred and (view.texts[bi] == "") != bool(pred["is_empty"]):
        return False
    if "index_range" in pred:
        a, b = pred["index_range"]
        if not (a <= bi < b):
            return False
    if "has_bold" in pred and _v_para_has_bold(p, view.runs[bi], styles) != bool(pred["has_bold"]):
        return False
    if "is_all_caps" in pred and _v_para_is_all_caps(p, view.runs[bi], styles) != bool(pred["is_all_caps"]):
        return False
    if "after_heading_regex" in pred:
        rx = re.compile(pred["after_heading_regex"])
        hits = [i for i, t in enumerate(view.texts) if rx.search(t)]
        if not hits or bi <= hits[0]:
            return False
    if "between_paragraphs" in pred:
        bp = pred["between_paragraphs"]
        srx = re.compile(bp["start_regex"])
        starts = [i for i, t in enumerate(view.texts) if srx.search(t)]
        if not starts:
            return False
        s_idx = starts[0]
        e_idx = len(view.texts)
        if bp.get("end_regex") is not None:
            erx = re.compile(bp["end_regex"])
            ends = [i for i in range(s_idx + 1, len(view.texts)) if erx.search(view.texts[i])]
            if ends:
                e_idx = ends[0]
        if bp.get("include_bounds", False):
            if not (s_idx <= bi <= (e_idx if e_idx < len(view.texts) else len(view.texts) - 1)):
                return False
        else:
            if not (s_idx < bi < e_idx):
                return False
    return True


def _has_direct_font(rpr) -> bool:
    rf = _rfonts(rpr)
    return rf is not None and any(rf.get(W + a) for a in FONT_ATTRS)


def _v_sel_colors(colors) -> Set[str]:
    """A color_in list as this module reads it: each entry stripped, a leading # dropped, casefolded."""
    if not isinstance(colors, list) or not colors:
        raise ValueError(f"color_in must be a non-empty list, got {colors!r}")
    return {str(c).strip().lstrip("#").casefold() for c in colors if str(c).strip()}


def _v_sel_color_in(color, wanted: Set[str]) -> bool:
    """The effective colour (val, themeColor, themeShade, themeTint) matches when its theme name or its value is listed."""
    val, theme = (color[0] or ""), (color[1] or "")
    return (bool(theme) and theme.casefold() in wanted) or (bool(val) and val.casefold() in wanted)


def target_matches(pred, rpr, in_revision: bool, preserve: Set[str], color_of=None) -> bool:
    """``color_of`` gives the run's (or mark's) effective colour — ``_v_run_prop(rpr, p, styles, "color")[0]`` over the
    SOURCE; only ``color_in`` needs it."""
    if pred in (None, "all") or pred == {}:
        return True
    if not isinstance(pred, dict) or set(pred) - RUN_KEYS:
        raise ValueError(f"unknown run selector {pred!r}")
    if "has_direct_font" in pred and _has_direct_font(rpr) != bool(pred["has_direct_font"]):
        return False
    if "font_in" in pred:
        wanted = {f.casefold() for f in pred["font_in"]}
        if not any(n.casefold() in wanted for n in _explicit_names(rpr)):
            return False
    if pred.get("not_in_revisions") and in_revision:
        return False
    if "color_in" in pred:
        if color_of is None:
            raise ValueError("color_in needs the run's effective colour")
        if not _v_sel_color_in(color_of(), _v_sel_colors(pred["color_in"])):
            return False
    return True


# ---- the checks ------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, src: VPackage, out: VPackage, plan: Dict[str, Any], source_name: Optional[str] = None,
                 output_name: Optional[str] = None):
        self.src, self.out, self.plan = src, out, plan
        self.source_name, self.output_name = source_name, output_name      # file paths, as run_suite was given them
        self.src_tp, self.out_tp = src.text_parts(), out.text_parts()
        self.both = [p for p in self.src_tp if p in self.out_tp]
        self.views: Dict[str, Tuple[PartView, PartView]] = {}
        self.maps: Dict[str, Dict[int, int]] = {}
        sp, op = src.related(STYLES_REL), out.related(STYLES_REL)
        self.styles_part = sp if sp and sp == op else None
        self.src_styles = Styles(src.xml(sp) if sp else None)
        self.out_styles = Styles(out.xml(op) if op else None)
        self.src_styles_root = self.src_styles.root
        self.out_styles_root = self.out_styles.root
        tp = src.related(THEME_REL)
        self.src_theme = Theme(src.xml(tp) if tp else None)
        tq = out.related(THEME_REL)
        self.out_theme = Theme(out.xml(tq) if tq else None)
        self.text_changed: Dict[str, Set[int]] = {}
        for d in plan.get("diffs", []):
            # a structure-only declaration (batch 2: before == after, struct_after) never exempts its paragraph: the
            # kind that made the change checks it item by item (verified_blocks), or the residual compares it whole
            if d.get("before") is not None and d.get("after") is not None and not (
                    d.get("before") == d.get("after") and d.get("struct_after") is not None):
                self.text_changed.setdefault(d["part"], set()).add(int(d["block_index"]))
        for d in plan.get("accepted_views", []):
            self.text_changed.setdefault(d["part"], set()).add(int(d["block_index"]))
        # residual strip instructions (source-side coordinates)
        self.strip_runs: Dict[str, Set[Tuple[int, int]]] = {}
        self.strip_marks: Dict[str, Set[int]] = {}
        self.strip_ppr: Dict[str, Dict[int, Set[str]]] = {}
        self.strip_sect: Dict[int, Set[str]] = {}
        self.strip_styles: Set[str] = set()
        self.strip_doc_defaults = False
        self.added_styles: Set[str] = set()       # new styles a styles_added assertion accounted for
        self.deleted_src_blocks: Dict[str, Set[int]] = {}   # untracked deleted blocks verified by deleted_paragraphs
        self.inserted_out_blocks: Dict[str, Set[int]] = {}  # inserted output blocks verified by inserted_paragraphs
        self.strip_style_ppr: Dict[str, Set[str]] = {}      # style id → pPr children a properties assertion checked
        # source block → output block of every paragraph an edit kind checked item by item (verify/edits.py): the
        # residual leaves exactly those out, by this mapping, whatever the text alignment paired them with
        self.verified_blocks: Dict[str, Dict[int, int]] = {}
        self.masked_extents: Dict[str, Set[int]] = {}       # part → ordinals of the w:drawings whose sizes image_swaps checked
        # built-in batch 2 (docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.2):
        self.verified_parts: Set[str] = set()       # text parts a kind compared WHOLE with its own expected tree
        self.expected_removed: Dict[str, List[Tuple[int, ...]]] = {}   # part → child-index paths (SOURCE tree) of
        #                                             elements outside paragraphs a kind expects gone
        self.verified_members: Set[str] = set()     # non-text members a kind compared whole (rels, side parts, created)
        self.member_strip: Dict[str, Set[str]] = {}  # settings/docProps member → keys a kind verified (member residual)
        # built-in batch 4 (docs/BUILTIN_OPS_BATCH4_DESIGN.md §0):
        self.replaced_styles: Set[str] = set()      # style ids a kind compared WHOLE (a replaced definition): out of the
        #                                             styles residual on both sides
        self.removed_styles: Set[str] = set()       # style ids a kind expects GONE: out of the residual's source copy only
        self.strip_tbl_style: Dict[str, Set[int]] = {}   # part → ordinals (root.iter(w:tbl)) of tables whose
        #                                             tblPr/tblStyle a kind checked
        # built-in batch 5 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §5.2): table, row and cell properties a kind checked —
        # removed from both sides before the part's residual (an emptied w:trPr / w:tcPr dropped on both sides). Tables
        # by ordinal (root.iter(w:tbl)), rows by index among the table's own w:tr, cells among the row's own w:tc.
        self.strip_tbl: Dict[str, Dict[int, Set[str]]] = {}                # part → {ordinal: tblPr child names}
        self.strip_tr: Dict[str, Dict[Tuple[int, int], Set[str]]] = {}     # part → {(ordinal, row): trPr child names}
        self.strip_tc: Dict[str, Dict[Tuple[int, int, int], Set[str]]] = {}   # part → {(ordinal, row, cell): tcPr …}

    def view(self, part):
        if part not in self.views:
            a, b = PartView(self.src.xml(part)), PartView(self.out.xml(part))
            self.views[part] = (a, b)
            self.maps[part] = align(a.texts, b.texts)
        return self.views[part]

    def scope_parts(self, scope: Iterable[str]) -> List[str]:
        out = []
        for k in scope:
            for p in self.src.part_of_kind(k):
                if p not in out:
                    out.append(p)
        return out


DEFAULT_SZ = 20          # half-points when no level sets w:sz (10 pt)


def _v_own_sz(rpr) -> Optional[int]:
    """The size (half-points) this rPr itself sets: w:sz/@w:val as a whole number, else None."""
    e = rpr.find(W + "sz") if rpr is not None else None
    v = e.get(W + "val") if e is not None else None
    return int(v) if v is not None and v.isdigit() else None


def _v_size_to(own: Optional[int], size: Optional[Dict[str, Any]], add_where_missing: bool = False) -> Optional[int]:
    """The size (half-points) a level whose own size is ``own`` must end with, or None when it keeps its own."""
    if not size:
        return None
    frm = size.get("from")
    if own is None:
        return size["sz"] if add_where_missing and frm is None else None
    return size["sz"] if frm is None or own in frm else None


def _check_rpr_change(S, O, tracked: bool, author, where: str, errs: List[str]) -> None:
    """The w:rPrChange rules shared by the run-property kinds: plain → the marker unchanged; tracked → present as the
    last child, with w:id and the author, its snapshot the source rPr (reject-all restores it); a source already
    carrying one is the op's refusal."""
    s_chg = S.find(W + "rPrChange") if S is not None else None
    o_chg = O.find(W + "rPrChange")
    if not tracked:
        if sig(s_chg) != sig(o_chg):
            errs.append(f"{where}: w:rPrChange changed by an untracked formatting op")
        return
    if s_chg is not None:
        errs.append(f"{where}: source already carries a w:rPrChange (tracked op must refuse)")
        return
    if o_chg is None or list(O)[-1] is not o_chg:
        errs.append(f"{where}: tracked change missing (w:rPrChange as last child of rPr)")
        return
    if o_chg.get(W + "id") is None or o_chg.get(W + "author") != author:
        errs.append(f"{where}: w:rPrChange needs w:id and author {author!r}")
    inner = o_chg.findall(W + "rPr")
    snap = [sig(c) for c in (S if S is not None else []) if _local(c) != "rPrChange"]
    if len(inner) != 1 or [sig(c) for c in inner[0]] != snap or len(o_chg) != 1:
        errs.append(f"{where}: w:rPrChange snapshot is not the source rPr (reject-all would not restore it)")


def _check_ppr_change(S, O, tracked: bool, author, where: str, errs: List[str]) -> None:
    """The w:pPrChange rules shared by the paragraph-property kinds (as _check_rpr_change; the snapshot is the source
    pPr without its rPr, sectPr and pPrChange)."""
    s_chg = S.find(W + "pPrChange") if S is not None else None
    o_chg = O.find(W + "pPrChange")
    if not tracked:
        if sig(s_chg) != sig(o_chg):
            errs.append(f"{where}: w:pPrChange changed by an untracked formatting op")
        return
    if s_chg is not None:
        errs.append(f"{where}: source already carries a w:pPrChange (tracked op must refuse)")
        return
    if o_chg is None or list(O)[-1] is not o_chg:
        errs.append(f"{where}: tracked change missing (w:pPrChange as last child of pPr)")
        return
    if o_chg.get(W + "id") is None or o_chg.get(W + "author") != author:
        errs.append(f"{where}: w:pPrChange needs w:id and author {author!r}")
    inner = o_chg.findall(W + "pPr")
    snap = [sig(c) for c in (S if S is not None else []) if _local(c) not in ("rPr", "sectPr", "pPrChange")]
    if len(inner) != 1 or len(o_chg) != 1 or [sig(c) for c in inner[0]] != snap:
        errs.append(f"{where}: w:pPrChange snapshot is not the source pPr (reject-all would not restore it)")


def _check_font_rpr(S, O, a: Dict[str, Any], where: str, errs: List[str], size_to: Optional[int] = None) -> None:
    """The selected rPr ``O`` must be the source rPr ``S`` with exactly the asserted rFonts change (none when the
    assertion sets no font) and, when ``size_to`` is given, w:sz set to it — nothing else."""
    want = {k: v for k, v in (a.get("set") or {}).items() if v is not None}
    clear = bool(a.get("clear_theme", True))
    tracked = bool(a.get("tracked", False))
    if O is None:
        errs.append(f"{where}: no rPr in the output")
        return
    if want:
        s_attrs = dict(_rfonts(S).attrib) if _rfonts(S) is not None else {}
        expected = dict(s_attrs)
        for slot, val in want.items():
            expected[W + slot] = val
            if clear:
                expected.pop(W + THEME_ATTR[slot], None)
        o_rf = _rfonts(O)
        got = dict(o_rf.attrib) if o_rf is not None else {}
        if got != expected:
            keys = sorted(set(got) | set(expected))
            errs.append(f"{where}: rFonts " + ", ".join(f"{etree.QName(k).localname} {expected.get(k)!r}≠{got.get(k)!r}"
                                                        for k in keys if got.get(k) != expected.get(k)))
    elif sig(_rfonts(S)) != sig(_rfonts(O)):
        errs.append(f"{where}: rFonts changed by an op that sets no font")
    skip = {"rFonts", "rPrChange"} | ({"sz"} if size_to is not None else set())
    if size_to is not None:
        e = O.find(W + "sz")
        if e is None or dict(e.attrib) != {W + "val": str(size_to)}:
            errs.append(f"{where}: w:sz is {dict(e.attrib) if e is not None else None}, expected val {size_to}")
    others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in skip]
    others_o = [sig(c) for c in O if _local(c) not in skip]
    if others_s != others_o:
        errs.append(f"{where}: run properties other than " + ("rFonts / w:sz" if size_to is not None else "rFonts")
                    + " changed")
    _check_rpr_change(S, O, tracked, a.get("author"), where, errs)


def _run_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", []))
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"run_fonts: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    sel = a.get("select") or {}
    runs_pred, para_pred = sel.get("runs", "all"), sel.get("paragraphs")
    exclude_rev = a.get("in_revisions", "include") == "exclude"
    fonts_set = bool({k: v for k, v in (a.get("set") or {}).items() if v is not None})
    size = a.get("size")
    only = bool(a.get("only_where_defined", False))
    n_runs = n_marks = n_sized = 0

    def gets(rpr) -> Tuple[bool, Optional[int]]:
        """(the font goes here, the size it must end with or None) — per property, from the SOURCE rPr."""
        return fonts_set and (not only or _has_direct_font(rpr)), _v_size_to(_v_own_sz(rpr), size)
    for part in parts:
        if part not in ctx.both:
            errs.append(f"run_fonts: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        for bi, p in enumerate(sv.pars):
            if not para_matches(para_pred, p, bi, sv, ctx.src_styles):
                continue
            cand_runs = []
            for ri, r in enumerate(sv.runs[bi]):
                rpr = r.find(W + "rPr")
                in_rev = _has_ancestor(r, REVISION_WRAPPERS, p)
                if exclude_rev and in_rev:
                    continue
                if a.get("skip_sym_runs", True) and r.find(W + "sym") is not None:
                    continue
                if symbolic(rpr, p, ctx.src_styles, preserve):
                    continue
                if gets(rpr) == (False, None):
                    continue
                if target_matches(runs_pred, rpr, in_rev, preserve,
                                  lambda rpr=rpr, p=p: _v_run_prop(rpr, p, ctx.src_styles, "color")[0]):
                    cand_runs.append(ri)
            mark = False
            if a.get("include_paragraph_marks", True):
                mrpr = p.find(W + "pPr/" + W + "rPr")
                m_rev = mrpr is not None and any(mrpr.find(W + t) is not None for t in ("ins", "del", "moveFrom", "moveTo"))
                if not (exclude_rev and m_rev) and not symbolic(mrpr, p, ctx.src_styles, preserve) \
                        and target_matches(runs_pred, mrpr, m_rev, preserve,
                                           lambda mrpr=mrpr, p=p: _v_run_prop(mrpr, p, ctx.src_styles, "color")[0]) \
                        and gets(mrpr) != (False, None):
                    mark = True
            if not cand_runs and not mark:
                continue
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"run_fonts: {part} block {bi} is selected but also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"run_fonts: {part} block {bi}: run structure changed")
                continue
            for ri in cand_runs:
                n_runs += 1
                ctx.strip_runs.setdefault(part, set()).add((bi, ri))
                s_rpr = sv.runs[bi][ri].find(W + "rPr")
                font_here, to = gets(s_rpr)
                n_sized += to is not None
                _check_font_rpr(s_rpr, ov.runs[oi][ri].find(W + "rPr"), {**a, "set": a.get("set") if font_here else {}},
                                f"{part} block {bi} run {ri}", errs, to)
            if mark:
                n_marks += 1
                ctx.strip_marks.setdefault(part, set()).add(bi)
                s_rpr = p.find(W + "pPr/" + W + "rPr")
                font_here, to = gets(s_rpr)
                n_sized += to is not None
                _check_font_rpr(s_rpr, ov.pars[oi].find(W + "pPr/" + W + "rPr"),
                                {**a, "set": a.get("set") if font_here else {}}, f"{part} block {bi} paragraph mark",
                                errs, to)
    if n_runs != a.get("expected_runs", -1):
        errs.append(f"run_fonts: {n_runs} run(s) selected here, plan predicted {a.get('expected_runs')}")
    if n_marks != a.get("expected_marks", 0 if not a.get("include_paragraph_marks", True) else -1):
        errs.append(f"run_fonts: {n_marks} paragraph mark(s) selected here, plan predicted {a.get('expected_marks')}")
    if n_sized != a.get("expected_sized", 0):
        errs.append(f"run_fonts: {n_sized} run(s)/mark(s) get the size here, plan predicted {a.get('expected_sized', 0)}")
    return f"run_fonts: {n_runs} run(s) + {n_marks} mark(s) → {a.get('set')}" + \
        (f", size {size['sz']} half-points on {n_sized}" if size else "")


def _select_styles(styles: Styles, a: Dict[str, Any], preserve: Set[str]) -> List[str]:
    sel = a.get("select", "all")
    if sel == "all":
        types = {"paragraph", "character", "table"}
        ids = None
    elif sel == "all-paragraph-and-character":
        types, ids = {"paragraph", "character"}, None
    elif isinstance(sel, dict) and set(sel) <= {"ids", "types"}:
        types = set(sel.get("types", ["paragraph", "character", "table", "numbering"]))
        ids = set(sel["ids"]) if "ids" in sel else None
    else:
        raise ValueError(f"unknown style selector {sel!r}")
    out = []
    for sid, st in styles.by_id.items():
        if st.get(W + "type", "paragraph") not in types or (ids is not None and sid not in ids):
            continue
        if symbolic_style(st, styles, preserve):
            continue
        font_here, size_to = _v_style_change(st, a)
        if not font_here and size_to is None:
            continue
        out.append(sid)
    return out


def _v_style_change(st, a: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
    """(font set on this style, size it must end with or None) — per property, where the style defines it unless
    only_where_defined is false."""
    only = a.get("only_where_defined", True)
    rpr = st.find(W + "rPr")
    fonts = bool({k: v for k, v in (a.get("set") or {}).items() if v is not None})
    return (fonts and (not only or _rfonts(rpr) is not None),
            _v_size_to(_v_own_sz(rpr), a.get("size"), add_where_missing=not only))


def _style_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("style_fonts: no common styles part")
        return "style_fonts: no styles part"
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    ids = _select_styles(ctx.src_styles, a, preserve)
    if sorted(ids) != sorted(a.get("expected_ids", [])):
        errs.append(f"style_fonts: selects {sorted(ids)} here, plan predicted {sorted(a.get('expected_ids', []))}")
    sized = []
    for sid in ids:
        o = ctx.out_styles.by_id.get(sid)
        if o is None:
            errs.append(f"style_fonts: style {sid} missing from the output")
            continue
        ctx.strip_styles.add(sid)
        font_here, size_to = _v_style_change(ctx.src_styles.by_id[sid], a)
        if size_to is not None:
            sized.append(sid)
        _check_font_rpr(ctx.src_styles.by_id[sid].find(W + "rPr"), o.find(W + "rPr"),
                        {**a, "tracked": False, "set": a.get("set") if font_here else {}}, f"style {sid}", errs, size_to)
    if a.get("size") and sorted(sized) != sorted(a.get("expected_sized", [])):
        errs.append(f"style_fonts: size set on {sorted(sized)} here, plan predicted {sorted(a.get('expected_sized', []))}")
    return f"style_fonts: {len(ids)} style(s) → {a.get('set')}" + (f", size on {len(sized)}" if a.get("size") else "")


def _doc_defaults_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("doc_defaults_fonts: no common styles part")
        return "doc_defaults_fonts: no styles part"
    S, O = ctx.src_styles.doc_defaults_rpr(), ctx.out_styles.doc_defaults_rpr()
    own = _v_own_sz(S)
    size_to = _v_size_to(own if own is not None else DEFAULT_SZ, a.get("size"))
    if bool(a.get("expected_sized")) != (size_to is not None):
        errs.append(f"doc_defaults_fonts: size {'applies' if size_to is not None else 'does not apply'} here, plan "
                    f"says {'it does' if a.get('expected_sized') else 'it does not'}")
    fonts = bool({k: v for k, v in (a.get("set") or {}).items() if v is not None})
    if not fonts and size_to is None:
        if sig(S) != sig(O):
            errs.append("doc_defaults_fonts: rPrDefault changed though nothing applies")
        return "doc_defaults_fonts: nothing applies"
    ctx.strip_doc_defaults = True
    _check_font_rpr(S, O, {**a, "tracked": False}, "docDefaults/rPrDefault", errs, size_to)
    return f"doc_defaults_fonts → {a.get('set')}" + (f", size {size_to} half-points" if size_to is not None else "")


PPR_SET_ELEMENTS = ("spacing", "ind", "jc")
_V_SPACING_KEYS = {"before", "after", "line", "lineRule", "beforeLines", "afterLines"}
_V_IND_KEYS = {"left", "right", "start", "end", "firstLine", "hanging", "leftChars", "rightChars", "startChars",
               "endChars", "firstLineChars", "hangingChars"}
_V_JC = {"left", "center", "right", "both", "distribute", "start", "end"}
_V_LINE_RULES = {"auto", "exact", "atLeast"}
_V_FIRST, _V_HANGING = ("firstLine", "firstLineChars"), ("hanging", "hangingChars")
_V_SIDES = (("left", "start"), ("right", "end"), ("firstLine", "hanging"))
_V_ON = ("1", "true", "on")


def _v_ppr_values(params: Dict[str, Any]) -> Dict[str, Any]:
    """set_paragraph_props' parameters (the user's own words) → the values to merge, as this module reads them;
    ValueError where the op must refuse."""
    out: Dict[str, Any] = {}
    for name, keys in (("spacing", _V_SPACING_KEYS), ("ind", _V_IND_KEYS)):
        v = params.get(name)
        if v is None:
            continue
        if not isinstance(v, dict) or not v or set(v) - keys:
            raise ValueError(f"bad {name} {v!r} (the op must refuse)")
        clean = {}
        for k, x in v.items():
            if k == "lineRule":
                if x not in _V_LINE_RULES:
                    raise ValueError(f"bad lineRule {x!r} (the op must refuse)")
                clean[k] = x
            elif isinstance(x, bool) or not isinstance(x, int):
                raise ValueError(f"{name}.{k} is not a whole number (the op must refuse)")
            else:
                clean[k] = str(x)
        out[name] = clean
    if set(_V_FIRST) & set(out.get("ind", {})) and set(_V_HANGING) & set(out.get("ind", {})):
        raise ValueError("a first-line and a hanging indent both asked (the op must refuse)")
    if params.get("jc") is not None:
        if params["jc"] not in _V_JC:
            raise ValueError(f"bad jc {params['jc']!r} (the op must refuse)")
        out["jc"] = params["jc"]
    if not out:
        raise ValueError("nothing asked (the op must refuse)")
    return out


def _v_merged(name: str, el, val) -> Dict[str, str]:
    """The attributes a w:spacing / w:ind / w:jc must end with: the asked ones set, the others kept (the first line —
    firstLine/firstLineChars — drops the hanging indent — hanging/hangingChars — and vice versa; jc gets w:val) — this
    module's reading of set_paragraph_props' merge."""
    out = dict(el.attrib) if el is not None else {}
    if name == "jc":
        out[W + "val"] = val
        return out
    for k, v in val.items():
        out[W + k] = str(v)
    if name == "ind":
        if any(k in val for k in _V_FIRST):
            for k in _V_HANGING:
                out.pop(W + k, None)
        if any(k in val for k in _V_HANGING):
            for k in _V_FIRST:
                out.pop(W + k, None)
    return out


def _v_ppr_of(st):
    return st.find(W + "pPr") if st is not None else None


def _v_paragraph_levels(p, styles: Styles) -> List[Tuple[str, Any]]:
    """(label, pPr) nearest first: the paragraph's own pPr, its paragraph style chain (its w:pStyle's value — an empty
    one names no style —, else the default), its table's style chain (tblStyle, else the default table style),
    docDefaults — template.Eff's level reading."""
    ps = p.find(W + "pPr/" + W + "pStyle")
    chain = styles.chain(ps.get(W + "val") if ps is not None else styles.default.get("paragraph"))
    tbl = next(p.iterancestors(W + "tbl"), None)
    if tbl is not None:
        ts = tbl.find(W + "tblPr/" + W + "tblStyle")
        chain = chain + styles.chain(ts.get(W + "val") if ts is not None else styles.default.get("table"))
    return ([("the paragraph", p.find(W + "pPr"))] + [(f"style {s.get(W + 'styleId')}", _v_ppr_of(s)) for s in chain]
            + [("docDefaults", styles.doc_defaults_ppr())])


def _v_style_levels(st, styles: Styles) -> List[Tuple[str, Any]]:
    """(label, pPr) nearest first: the style's own pPr, its basedOn ancestors, docDefaults."""
    return ([(f"style {st.get(W + 'styleId')}", _v_ppr_of(st))]
            + [(f"style {s.get(W + 'styleId')}", _v_ppr_of(s)) for s in styles.chain(st.get(W + "styleId"))[1:]]
            + [("docDefaults", styles.doc_defaults_ppr())])


def _v_inherited_refusal(levels: List[Tuple[str, Any]], setv: Dict[str, Any]) -> Optional[str]:
    """Why set_paragraph_props must refuse a target whose own pPr is ``levels[0]`` and which inherits from the rest
    (nearest first), or None: before/after asked where the nearest level naming …Autospacing turns it on, or where any
    level sets …Lines (the lines not asked); an indentation side asked in twips where any level gives it in characters
    (…Chars — Word's priority), those the merge removes from the target itself excepted, the side's Chars not asked; an
    indentation alias whose other spelling the target's own w:ind uses."""
    sv, iv = setv.get("spacing") or {}, setv.get("ind") or {}
    spacings = [(i, lab, ppr.find(W + "spacing")) for i, (lab, ppr) in enumerate(levels)
                if ppr is not None and ppr.find(W + "spacing") is not None]
    inds = [(i, lab, ppr.find(W + "ind")) for i, (lab, ppr) in enumerate(levels)
            if ppr is not None and ppr.find(W + "ind") is not None]
    for side in ("before", "after"):
        if side not in sv:
            continue
        named = [(lab, sp.get(W + side + "Autospacing")) for _, lab, sp in spacings
                 if sp.get(W + side + "Autospacing") is not None]
        if named and named[0][1] in _V_ON:
            return f"spacing {side} is auto ({side}Autospacing on {named[0][0]})"
        if f"{side}Lines" not in sv:
            for _, lab, sp in spacings:
                if sp.get(W + side + "Lines") not in (None, ""):
                    return f"spacing {side} is set in lines ({side}Lines on {lab})"
    for side in _V_SIDES:
        if not any(k in iv for k in side) or any(k + "Chars" in iv for k in side):
            continue
        dropped = (set(_V_HANGING) if any(k in iv for k in _V_FIRST) else set()) | \
                  (set(_V_FIRST) if any(k in iv for k in _V_HANGING) else set())
        for i, lab, ind in inds:
            for k in side:
                if ind.get(W + k + "Chars") not in (None, "") and not (i == 0 and k + "Chars" in dropped):
                    return f"indentation {k} is given in characters ({k}Chars on {lab}), which Word uses instead"
    own = levels[0][1].find(W + "ind") if levels and levels[0][1] is not None else None
    for asked, other in (("left", "start"), ("start", "left"), ("right", "end"), ("end", "right")):
        if asked in iv and own is not None and own.get(W + other) is not None:
            return f"indentation asked as {asked} where the target's own w:ind uses {other}"
    return None


def _v_check_merged(S, O, setv: Dict[str, Any], where: str, errs: List[str]) -> None:
    """Each asked element of the output pPr ``O`` occurs once and holds exactly the source's attributes with the asked
    values merged."""
    for name, val in setv.items():
        found = O.findall(W + name) if O is not None else []
        exp = _v_merged(name, S.find(W + name) if S is not None else None, val)
        got = dict(found[0].attrib) if found else None
        if len(found) > 1:
            errs.append(f"{where}: {len(found)} w:{name} elements in the output, expected one")
        if got != exp or (found and len(found[0])):
            errs.append(f"{where}: {name} is {_plain(got)}, expected {_plain(exp)}")


def _paragraph_props_styles(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    """Built-in batch 4 §2: the values merged into paragraph style definitions — from the user's own parameters, read
    here with this module's rules (never tracked: a ``tracked`` asked true is what the op must refuse) — each changed
    style's pPr is the source's with the asked values merged and every other child identical; a style already as asked
    is untouched; the refusals re-derived; every selected style shows the asked values in the output."""
    if ctx.styles_part is None:
        errs.append("paragraph_props: no common styles part")
        return "paragraph_props: no styles part"
    params = a.get("params") or {}
    if set(params) - {"spacing", "ind", "jc", "tracked"}:
        raise ValueError(f"unknown parameter(s) {sorted(set(params) - {'spacing', 'ind', 'jc', 'tracked'})}")
    setv = _v_ppr_values(params)
    if params.get("tracked", False):
        errs.append("paragraph_props: style definitions are never tracked (the op must refuse tracked: true)")
    sel = a.get("select") or {}
    if set(sel) != {"ids"} or not isinstance(sel["ids"], list):
        raise ValueError(f"bad styles selector {sel!r}")
    ids = list(dict.fromkeys(sel["ids"]))
    changed: List[str] = []
    for sid in ids:
        st, o = ctx.src_styles.by_id.get(sid), ctx.out_styles.by_id.get(sid)
        if st is None or o is None:
            errs.append(f"paragraph_props: style {sid} missing from the {'source' if st is None else 'output'}")
            continue
        where = f"style {sid}"
        if st.get(W + "type", "paragraph") != "paragraph":
            errs.append(f"{where}: a {st.get(W + 'type')} style (the op must refuse)")
            continue
        why = _v_inherited_refusal(_v_style_levels(st, ctx.src_styles), setv)
        if why:
            errs.append(f"{where}: {why} — the op must refuse")
            continue
        S, O = st.find(W + "pPr"), o.find(W + "pPr")
        expected = {name: _v_merged(name, S.find(W + name) if S is not None else None, val) for name, val in setv.items()}
        if not all(S is not None and S.find(W + name) is not None and dict(S.find(W + name).attrib) == exp
                   for name, exp in expected.items()):
            changed.append(sid)
            ctx.strip_style_ppr.setdefault(sid, set()).update(setv)
            _v_check_merged(S, O, setv, where, errs)
            others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in setv]
            others_o = [sig(c) for c in (O if O is not None else []) if _local(c) not in setv]
            if others_s != others_o:
                errs.append(f"{where}: paragraph properties other than {sorted(setv)} changed")
        # (an already-as-asked style is left to the residual, which compares it whole)
        for name, val in setv.items():                     # post-condition: the output style shows the asked values
            found = O.findall(W + name) if O is not None else []
            if len(found) != 1 or _v_merged(name, found[0], val) != dict(found[0].attrib):
                errs.append(f"{where}: {name} does not show {val} in the output"
                            + (f" ({len(found)} w:{name} elements)" if len(found) > 1 else ""))
    if sorted(changed) != sorted(a.get("expected_ids", [])):
        errs.append(f"paragraph_props: style(s) {sorted(changed)} change here, plan predicted "
                    f"{sorted(a.get('expected_ids', []))}")
    return f"paragraph_props: style(s) {changed} → {setv}"


def _paragraph_props(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    target = a.get("target", "direct")
    if target == "styles":
        return _paragraph_props_styles(ctx, a, errs)
    if target != "direct":
        raise ValueError(f"unknown target {target!r}")
    parts = ctx.scope_parts(a.get("scope", []))
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"paragraph_props: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    setv = a.get("set") or {}
    if set(setv) - set(PPR_SET_ELEMENTS):
        errs.append(f"paragraph_props: unknown properties {sorted(set(setv) - set(PPR_SET_ELEMENTS))}")
        return "paragraph_props: unknown properties"
    tracked = bool(a.get("tracked"))
    pred = (a.get("select") or {}).get("paragraphs", "all")
    n = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"paragraph_props: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        for bi, p in enumerate(sv.pars):
            if not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            n += 1
            where = f"{part} block {bi}"
            why = _v_inherited_refusal(_v_paragraph_levels(p, ctx.src_styles), setv)
            if why:
                errs.append(f"{where}: {why} — the op must refuse")
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"paragraph_props: {part} block {bi} is selected but also has predicted text changes")
                continue
            oi = ctx.maps[part].get(bi)
            if oi is None:
                errs.append(f"paragraph_props: {part} block {bi} has no counterpart in the output")
                continue
            ctx.strip_ppr.setdefault(part, {})[bi] = set(setv) | {"pPrChange"}
            S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
            if O is None:
                errs.append(f"{where}: no pPr in the output")
                continue
            _v_check_merged(S, O, setv, where, errs)
            _check_ppr_change(S, O, tracked, a.get("author"), where, errs)
    if n != a.get("expected_paragraphs", -1):
        errs.append(f"paragraph_props: {n} paragraph(s) selected here, plan predicted {a.get('expected_paragraphs')}")
    return f"paragraph_props: {n} paragraph(s) → {setv}" + (" (tracked)" if tracked else "")


def _deleted_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", ["document"]))
    pred = (a.get("select") or {}).get("paragraphs")
    tracked = bool(a.get("tracked", True))
    n = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"deleted_paragraphs: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        for bi, p in enumerate(sv.pars):
            if not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            n += 1
            if not tracked:
                ctx.deleted_src_blocks.setdefault(part, set()).add(bi)
            else:
                oi = ctx.maps[part].get(bi)
                if oi is None or oi >= len(ov.pars):
                    errs.append(f"deleted_paragraphs: {part} block {bi} missing in tracked output")
                    continue
                op = ov.pars[oi]
                if a.get("mark_deleted", True):
                    mrpr = op.find(W + "pPr/" + W + "rPr")
                    d_el = mrpr.find(W + "del") if mrpr is not None else None
                    if d_el is None or d_el.get(W + "id") is None or d_el.get(W + "author") != a.get("author"):
                        errs.append(f"deleted_paragraphs: {part} block {bi} paragraph mark missing valid w:del")
    if n != a.get("expected_paragraphs", -1):
        errs.append(f"deleted_paragraphs: {n} paragraph(s) selected here, plan predicted {a.get('expected_paragraphs')}")
    return f"deleted_paragraphs: {n} paragraph(s) ({'tracked' if tracked else 'untracked'})"


_V_MARK_REVS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "rPrChange"}


def _v_inherit_ppr(anchor):
    """This module's own reading of insert_paragraph's inherit rule for paragraph properties: the anchor's pPr
    without sectPr / pPrChange and without revision marks or rPrChange inside its paragraph-mark rPr."""
    ppr = anchor.find(W + "pPr")
    if ppr is None:
        return None
    c = copy.deepcopy(ppr)
    for ch in list(c):
        if _local(ch) in ("sectPr", "pPrChange"):
            c.remove(ch)
    rpr = c.find(W + "rPr")
    if rpr is not None:
        for ch in list(rpr):
            if ch.tag in _V_MARK_REVS:
                rpr.remove(ch)
        if not len(rpr) and not rpr.attrib:
            c.remove(rpr)
    return c if (len(c) or c.attrib) else None


def _v_inherit_rpr(anchor, view_runs):
    """… and for run properties: the rPr of the anchor's first live text run, without rPrChange."""
    for r in view_runs:
        if r.find(W + "t") is None or _has_ancestor(r, {W + "del", W + "moveFrom"}, anchor):
            continue
        rpr = r.find(W + "rPr")
        if rpr is None:
            return None
        c = copy.deepcopy(rpr)
        for ch in list(c):
            if _local(ch) == "rPrChange":
                c.remove(ch)
        return c if (len(c) or c.attrib) else None
    return None


def _v_ppr_sans_mark_ins(p):
    """The output paragraph's pPr with the tracked-insertion mark removed (what must equal the inherited pPr)."""
    ppr = p.find(W + "pPr")
    if ppr is None:
        return None
    c = copy.deepcopy(ppr)
    rpr = c.find(W + "rPr")
    if rpr is not None:
        for ch in list(rpr):
            if _local(ch) == "ins":
                rpr.remove(ch)
        if not len(rpr) and not rpr.attrib:
            c.remove(rpr)
    return c if (len(c) or c.attrib) else None


def _inserted_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", ["document"]))
    pred = (a.get("select") or {}).get("paragraphs")
    pos = a.get("position", "after")
    text = a.get("text", "")
    tracked = bool(a.get("tracked", True))
    style = a.get("style")
    formatting = a.get("formatting", "inherit")
    if formatting not in ("inherit", "none"):
        raise ValueError(f"unknown formatting {formatting!r}")
    n = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"inserted_paragraphs: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        matched = [bi for bi, p in enumerate(sv.pars) if para_matches(pred, p, bi, sv, ctx.src_styles)]
        n += len(matched)
        for k, bi in enumerate(matched):
            oi = (bi + 1 + k) if pos == "after" else (bi + k)
            if oi >= len(ov.pars):
                errs.append(f"inserted_paragraphs: {part} output block {oi} out of range")
                continue
            ctx.inserted_out_blocks.setdefault(part, set()).add(oi)
            op = ov.pars[oi]
            if ov.texts[oi] != text:
                errs.append(f"inserted_paragraphs: {part} output block {oi} text {ov.texts[oi]!r} ≠ {text!r}")
            want_style = (_pstyle(sv.pars[bi]) if style in (None, "inherit") else style)
            got_style = _pstyle(op)
            if got_style != want_style:
                errs.append(f"inserted_paragraphs: {part} output block {oi} style {got_style!r} ≠ {want_style!r}")
            # formatting, re-derived here from the SOURCE anchor
            anchor = sv.pars[bi]
            want_ppr = _v_inherit_ppr(anchor) if formatting == "inherit" else None
            want_rpr = _v_inherit_rpr(anchor, sv.runs[bi]) if formatting == "inherit" else None
            if want_style is not None:
                if want_ppr is None:
                    want_ppr = etree.Element(W + "pPr")
                ps = want_ppr.find(W + "pStyle")
                if ps is None:
                    ps = etree.Element(W + "pStyle")
                    want_ppr.insert(0, ps)
                ps.set(W + "val", want_style)
            got_ppr = _v_ppr_sans_mark_ins(op) if tracked else op.find(W + "pPr")
            if sig(want_ppr) != sig(got_ppr):
                errs.append(f"inserted_paragraphs: {part} output block {oi} paragraph properties are not the anchor's "
                            f"({formatting}): {first_difference(want_ppr, got_ppr) or 'differ'}")
            oruns = ov.runs[oi]
            if len(oruns) != 1 or [c.tag for c in oruns[0] if c.tag != W + "rPr"] != [W + "t"]:
                errs.append(f"inserted_paragraphs: {part} output block {oi} must hold exactly one run with one w:t, "
                            f"has {len(oruns)} run(s)")
            else:
                got_rpr = oruns[0].find(W + "rPr")
                if sig(want_rpr) != sig(got_rpr):
                    errs.append(f"inserted_paragraphs: {part} output block {oi} run properties are not the anchor's "
                                f"first text run's ({formatting}): {first_difference(want_rpr, got_rpr) or 'differ'}")
            if tracked:
                mrpr = op.find(W + "pPr/" + W + "rPr")
                ins_m = mrpr.find(W + "ins") if mrpr is not None else None
                ins_w = op.find(W + "ins")
                if ins_m is None or ins_m.get(W + "id") is None or ins_m.get(W + "author") != a.get("author"):
                    errs.append(f"inserted_paragraphs: {part} output block {oi} mark missing valid w:ins")
                if ins_w is None or ins_w.get(W + "id") is None or ins_w.get(W + "author") != a.get("author"):
                    errs.append(f"inserted_paragraphs: {part} output block {oi} body missing valid w:ins")
    if n != a.get("expected_anchors", -1):
        errs.append(f"inserted_paragraphs: {n} anchor(s) selected here, plan predicted {a.get('expected_anchors')}")
    return (f"inserted_paragraphs: {n} paragraph(s) {pos} anchor ({'tracked' if tracked else 'untracked'}, "
            f"formatting {formatting} re-derived from the anchor)")


def sect_prs(root) -> List[Any]:
    body = root.find(W + "body")
    if body is None:
        return []
    out = [sp for p in body.iter(W + "p") for sp in p.findall(W + "pPr/" + W + "sectPr")]
    final = body.find(W + "sectPr")
    if final is not None:
        out.append(final)
    return out


# ---- sections: set_page_setup's selection and post-conditions (built-in batch 5, items 39–40) ----------------------
#
# This module's own reading of set_page_setup (transform side: ops/set_page_setup.py, ops/selectors.resolve_sections).
# The assertion carries the user's own words — ``select`` and ``params`` as the spec gave them (absent = default) — and
# the plan's list of selected sections; the kind re-derives the selection over the SOURCE and, per selected section,
# exactly which sectPr elements must change and how, with its own defaults, and compares both with the plan.

V_SECTION_KEYS = ("containing", "with_tables", "index_range")
V_PAGE_PARAMS = ("margins", "size", "orientation", "different_first_page", "allow_missing")
V_MARGIN_KEYS = ("top", "right", "bottom", "left", "header", "footer", "gutter")


def _v_title_pg_on(sp) -> bool:
    """Different first page in effect: a w:titlePg whose w:val is absent or not off."""
    t = sp.find(W + "titlePg") if sp is not None else None
    return t is not None and t.get(W + "val") not in FALSY


def _v_resolve_sections(pkg: VPackage, sections, styles: Styles) -> List[int]:
    """The sections (indices among the main part's sectPr) a ``select.sections`` chooses, read over ``pkg``:
    ``"all"`` (or absent), a list of indices, or an object whose every given key must hold — ``index_range`` [a, b]
    inclusive, ``with_tables`` (the section holds a table: a table's section is its first paragraph's), ``containing``
    (some paragraph of the main part in the section matches the paragraph predicate, read by ``para_matches``). A
    paragraph's section is ``tabs._v_sections``' (its outermost paragraph's: the number of paragraphs holding a
    w:pPr/w:sectPr before it). ValueError where the op must refuse."""
    root = pkg.xml(pkg.main_part())
    n = len(sect_prs(root))
    if sections is None or sections == "all":
        return list(range(n))
    if isinstance(sections, (list, tuple)):
        if not all(type(i) is int and 0 <= i < n for i in sections):
            raise ValueError(f"sections {sections!r} are not section indices of this file (it has {n}) — the op must "
                             f"refuse")
        return sorted(set(sections))
    if not isinstance(sections, dict) or not sections or set(sections) - set(V_SECTION_KEYS):
        raise ValueError(f"sections {sections!r}: 'all', a list of indices or an object of {list(V_SECTION_KEYS)} — "
                         f"the op must refuse")
    chosen = set(range(n))
    if "index_range" in sections:
        r = sections["index_range"]
        if (not isinstance(r, (list, tuple)) or len(r) != 2 or not all(type(x) is int for x in r)
                or not 0 <= r[0] <= r[1]):
            raise ValueError(f"sections.index_range {r!r} — the op must refuse")
        chosen &= set(range(r[0], r[1] + 1))
    if "with_tables" in sections or "containing" in sections:
        from .tabs import _v_sections            # verify/tabs imports this module: imported when needed
        of = _v_sections(root)
        pars = list(root.iter(W + "p"))           # the list keeps every proxy alive: ids stay unique
        index = {id(p): k for k, p in enumerate(pars)}
    if "with_tables" in sections:
        flag = sections["with_tables"]
        if not isinstance(flag, bool):
            raise ValueError(f"sections.with_tables {flag!r} — the op must refuse")
        holding = set()
        for t in root.iter(W + "tbl"):
            first = next(t.iter(W + "p"), None)
            if first is not None:
                holding.add(of[index[id(first)]])
        chosen &= holding if flag else set(range(n)) - holding
    if "containing" in sections:
        pred = sections["containing"]
        if not isinstance(pred, dict) or not pred:
            raise ValueError(f"sections.containing {pred!r} — the op must refuse")
        view = PartView(root)
        try:
            hit = {of[bi] for bi, p in enumerate(view.pars) if para_matches(pred, p, bi, view, styles)}
        except re.error as e:
            raise ValueError(f"sections.containing: {e} — the op must refuse")
        chosen &= hit
    return sorted(chosen)


def _v_page_setup_elements(S, params: Dict[str, Any], where: str) -> Dict[str, Dict[str, Any]]:
    """What set_page_setup must change in one selected section's sectPr ``S`` (the SOURCE), as section_props element
    specs, read from the user's own parameters: pgMar gets the asked margins; pgSz the asked size, and with an
    orientation ``orient`` and width/height swapped where they disagree with it (a square page stays as it is); titlePg,
    when different_first_page is asked and not already in effect, present without w:val (on) or absent (off)."""
    out: Dict[str, Dict[str, Any]] = {}
    margins, size, orient = params.get("margins") or {}, params.get("size") or {}, params.get("orientation")
    for name, d, keys in (("margins", margins, V_MARGIN_KEYS), ("size", size, ("w", "h"))):
        if not isinstance(d, dict) or set(d) - set(keys) or any(
                type(v) is not int or (v < 0 and k not in ("top", "bottom")) for k, v in d.items()):
            raise ValueError(f"{name} {d!r} — the op must refuse")
    if size and set(size) != {"w", "h"}:
        raise ValueError("size needs both w and h — the op must refuse")
    if orient not in (None, "portrait", "landscape"):
        raise ValueError(f"orientation {orient!r} — the op must refuse")
    dfp = params.get("different_first_page")
    if dfp is not None and not isinstance(dfp, bool):
        raise ValueError(f"different_first_page {dfp!r} — the op must refuse")
    if margins:
        if S.find(W + "pgMar") is None:
            raise ValueError(f"{where} has no w:pgMar — the op must refuse")
        out["pgMar"] = {"set": {k: str(v) for k, v in margins.items()}}
    if size or orient:
        pg = S.find(W + "pgSz")
        own = lambda a: int(pg.get(W + a)) if pg is not None and (pg.get(W + a) or "").isdigit() else None
        w, h = size.get("w", own("w")), size.get("h", own("h"))
        if w is None or h is None:
            raise ValueError(f"{where} has no page size to orient — the op must refuse")
        pg_set: Dict[str, str] = {}
        if orient:
            if (orient == "landscape") != (w > h):
                w, h = h, w
            pg_set["orient"] = orient
        pg_set["w"], pg_set["h"] = str(w), str(h)
        out["pgSz"] = {"set": pg_set}
    if dfp is not None and _v_title_pg_on(S) != dfp:
        out["titlePg"] = {"present": True, "remove": ["val"]} if dfp else {"absent": True}
    return out


def _v_page_setup(ctx: Ctx, a: Dict[str, Any], s_secs, o_secs, errs: List[str]) -> List[int]:
    """The selection and the per-section changes of a set_page_setup assertion, re-derived (see the block comment)."""
    select, params = a.get("select"), a.get("params")
    if not isinstance(select, dict) or set(select) - {"sections"}:
        raise ValueError(f"select {select!r}: only 'sections'")
    if not isinstance(params, dict) or set(params) - set(V_PAGE_PARAMS):
        raise ValueError(f"params {params!r}: only {list(V_PAGE_PARAMS)}")
    want = _v_resolve_sections(ctx.src, select.get("sections", "all"), ctx.src_styles)
    if a.get("selected") != want:
        errs.append(f"section_props: the selection {select.get('sections', 'all')!r} chooses sections {want} here, "
                    f"the plan chose {a.get('selected')}")
    allow = params.get("allow_missing", False)
    if not isinstance(allow, bool):
        raise ValueError(f"allow_missing {allow!r} — the op must refuse")
    if not want and isinstance(select.get("sections"), dict) and not allow:
        errs.append(f"section_props: the selection {select['sections']!r} chooses no section here — the op must "
                    f"refuse (not-found) unless allow_missing")
    per = a.get("per_section") or {}
    changed = []
    for i in want:
        where = f"section {i}"
        expected = _v_page_setup_elements(s_secs[i], params, where)
        planned = per.get(str(i))
        if expected:
            changed.append(i)
            if planned != expected:
                errs.append(f"section_props: {where} must get {expected} (re-derived from the asked values and the "
                            f"source), the plan gives {planned}")
        elif planned is not None:
            errs.append(f"section_props: {where} needs no change (already as asked), the plan gives {planned}")
        # post-condition on the OUTPUT: the asked first-page setting is the one in effect, whether or not it changed
        dfp = params.get("different_first_page")
        if dfp is not None and _v_title_pg_on(o_secs[i]) != dfp:
            errs.append(f"section_props: {where} different first page is "
                        f"{'on' if _v_title_pg_on(o_secs[i]) else 'off'} in the output, asked {'on' if dfp else 'off'}")
    stray = sorted(set(a.get("sections", [])) - set(want))
    if stray:
        errs.append(f"section_props: sections {stray} changed but the selection does not choose them")
    if sorted(a.get("sections", [])) != changed:
        errs.append(f"section_props: sections {changed} must change here, the plan changes {a.get('sections', [])}")
    return want


def _v_check_sect_element(ctx: Ctx, i: int, S, O, name: str, spec: Dict[str, Any], errs: List[str]) -> None:
    """One element of one section, exactly as its spec says: ``{"absent": true}`` — gone; ``{"present": true,
    "remove": [...], "set": {...}}`` — there, with the source's attributes minus ``remove`` plus ``set`` and the source's
    children (none when the source has no such element); otherwise (``set``/``remove``/``drop_if_empty``) as before
    built-in batch 5."""
    ctx.strip_sect.setdefault(i, set()).add(name)
    se, oe = S.find(W + name), O.find(W + name)
    where = f"section {i} {name}"
    if spec.get("absent"):
        if set(spec) != {"absent"} or spec["absent"] is not True:
            raise ValueError(f"{where}: an absent element spec takes no other keys")
        if oe is not None:
            errs.append(f"{where}: should be absent")
        return
    expected = dict(se.attrib) if se is not None else {}
    for k in spec.get("remove", []):
        expected.pop(W + k, None)
    for k, v in (spec.get("set") or {}).items():
        expected[W + k] = str(v)
    if spec.get("present"):
        if spec["present"] is not True or set(spec) - {"present", "remove", "set"}:
            raise ValueError(f"{where}: a present element spec takes remove and set only")
        if oe is None:
            errs.append(f"{where}: should be present")
            return
    elif not expected and (spec.get("drop_if_empty") or se is None):
        if oe is not None:
            errs.append(f"{where}: should be absent")
        return
    if oe is None or dict(oe.attrib) != expected:
        errs.append(f"{where}: expected { {etree.QName(k).localname: v for k, v in expected.items()} } got "
                    f"{ {etree.QName(k).localname: v for k, v in dict(oe.attrib).items()} if oe is not None else None }")
    elif [sig(c) for c in (se if se is not None else [])] != [sig(c) for c in oe]:
        errs.append(f"{where}: children changed")


def _section_props(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    """Per listed section, each element exactly as its spec says (``elements``, shared by every listed section, or —
    set_page_setup since built-in batch 5 — ``per_section``: section index as text → its elements). With ``select``
    (set_page_setup), the selection and every section's change are re-derived from the user's own words first
    (``_v_page_setup``)."""
    main = ctx.src.main_part()
    s_secs, o_secs = sect_prs(ctx.src.xml(main)), sect_prs(ctx.out.xml(main))
    if len(s_secs) != len(o_secs):
        errs.append(f"section_props: {len(s_secs)}→{len(o_secs)} sections")
        return "section_props: section count changed"
    sel = a.get("sections", [])
    if len(sel) != a.get("expected_sections", -1):
        errs.append(f"section_props: {len(sel)} section(s) listed, plan predicted {a.get('expected_sections')}")
    per = a.get("per_section")
    if per is not None and (not isinstance(per, dict) or sorted(per) != sorted(str(i) for i in sel)):
        errs.append(f"section_props: per-section changes for {sorted(per) if isinstance(per, dict) else per!r}, "
                    f"sections listed {sel}")
        per = per if isinstance(per, dict) else {}
    if a.get("op") == "set_page_setup" and "select" not in a:
        # since batch 5 set_page_setup's assertion carries the user's own select and V10 re-derives the selection from
        # it; one without it would be trusted on its word (batch 5's review)
        errs.append("section_props: set_page_setup's assertion carries no select to re-derive the sections from")
    chosen = _v_page_setup(ctx, a, s_secs, o_secs, errs) if "select" in a else None
    for i in sel:
        if not 0 <= i < len(s_secs):
            errs.append(f"section_props: no section {i}")
            continue
        S, O = s_secs[i], o_secs[i]
        elements = (per.get(str(i)) or {}) if per is not None else (a.get("elements") or {})
        for name, spec in elements.items():
            _v_check_sect_element(ctx, i, S, O, name, spec, errs)
    if per is not None:
        names = sorted({n for e in per.values() if isinstance(e, dict) for n in e})
        return (f"section_props: sections {sel} → {names}"
                + (f" (selection re-derived: sections {chosen})" if chosen is not None else ""))
    return f"section_props: sections {sel} → {list((a.get('elements') or {}).keys())}"


def _section_hf_refs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    main = ctx.src.main_part()
    s_secs, o_secs = sect_prs(ctx.src.xml(main)), sect_prs(ctx.out.xml(main))
    if len(s_secs) != len(o_secs):
        errs.append(f"section_hf_refs: {len(s_secs)}→{len(o_secs)} sections")
        return "section_hf_refs: section count changed"
    kinds = {k + "Reference" for k in a.get("kinds", [])}
    types = set(a.get("types", []))
    removed = 0
    for i in a.get("sections", []):
        if not 0 <= i < len(s_secs):
            errs.append(f"section_hf_refs: no section {i}")
            continue
        ctx.strip_sect.setdefault(i, set()).update({"headerReference", "footerReference"})
        refs = lambda sp: [r for r in sp if _local(r) in ("headerReference", "footerReference")]
        drop = lambda r: _local(r) in kinds and r.get(W + "type", "default") in types
        keep_s = [sig(r) for r in refs(s_secs[i]) if not drop(r)]
        removed += sum(1 for r in refs(s_secs[i]) if drop(r))
        o_refs = refs(o_secs[i])
        if any(drop(r) for r in o_refs):
            errs.append(f"section {i}: selected header/footer references still present")
        if [sig(r) for r in o_refs if not drop(r)] != keep_s:
            errs.append(f"section {i}: references that were not selected changed")
    if removed != a.get("expected_removed", -1):
        errs.append(f"section_hf_refs: {removed} reference(s) selected here, plan predicted {a.get('expected_removed')}")
    return f"section_hf_refs: {removed} reference(s) removed from sections {a.get('sections')}"


def _v_page_field(root) -> bool:
    """A PAGE field anywhere in the part: a complex field's instruction or a simple field's, first word PAGE."""
    instr = [t.text or "" for t in root.iter(W + "instrText")] + [f.get(W + "instr") or "" for f in root.iter(W + "fldSimple")]
    return any((x.split() or [""])[0].upper() == "PAGE" for x in instr)


def _cover_unnumbered(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    """fix_page_numbering cover-unnumbered (post-condition, this module's own reading — never the transform's): the
    footer the cover section shows in the OUTPUT — its own first-page footer while its w:titlePg is on (w:val not
    0/false/off) and it has one, else its own default footer — is the footer the plan edited, and holds no PAGE field.
    Reading titlePg by presence alone once edited a first-page footer the cover never showed and PASSED with the page
    number still on the cover."""
    main = ctx.out.main_part()
    secs = sect_prs(ctx.out.xml(main))
    if not secs:
        errs.append("cover_unnumbered: the output has no section")
        return "cover_unnumbered: no section"
    rels = ctx.out.rels_targets(main)
    own: Dict[str, Optional[str]] = {}
    for el in secs[0].findall(W + "footerReference"):
        rid = el.get(R + "id")
        own[el.get(W + "type", "default")] = rels[rid]["target"] if rid in rels else None
    shown = own.get("first") if _v_title_pg_on(secs[0]) and own.get("first") else own.get("default")
    if shown != a.get("footer"):
        errs.append(f"cover_unnumbered: the cover shows footer {shown!r}, the plan edited {a.get('footer')!r}")
    elif shown and _v_page_field(ctx.out.xml(shown)):
        errs.append(f"cover_unnumbered: the footer the cover shows ({shown}) still holds a PAGE field")
    return f"cover_unnumbered: the cover shows {shown}, without a page number"


def _effective_font(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    """Post-condition: every text run and paragraph mark of the scope resolves (this module's resolvers) to
    ``value`` on ``slots`` (preserved symbol fonts stay as they were) and/or to ``size`` (points): with ``size_from``
    only what resolved to one of those sizes in the source, the rest keeping its size; symbol runs (a preserved
    font in their chain, or holding a w:sym) are exempt from the size."""
    value = a.get("value")
    slots = a.get("slots", ["ascii", "hAnsi", "cs"]) if value is not None else []
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    want_sz = int(a["size"] * 2) if a.get("size") is not None else None
    frm = {int(x * 2) for x in a["size_from"]} if a.get("size_from") else None
    checked = preserved = bad = 0
    bad_examples: List[str] = []

    def size_ok(es: Optional[int], eo: int) -> bool:
        if frm is None:
            return eo == want_sz
        return eo == (want_sz if es in frm else es)

    def exempt(rpr, p, run) -> bool:
        return symbolic(rpr, p, ctx.out_styles, preserve) or (run is not None and run.find(W + "sym") is not None)

    for part in ctx.scope_parts(a.get("scope", ["document"])):
        if part not in ctx.both:
            continue
        sv, ov = ctx.view(part)
        tc = ctx.text_changed.get(part, set())
        for bi, oi in ctx.maps[part].items():
            sp, op = sv.pars[bi], ov.pars[oi]
            if bi in tc:
                had = {slot: set() for slot in slots}
                src_sizes = set()
                for r in sv.runs[bi]:
                    if any(c.tag in (W + "t", W + "delText") for c in r):
                        es = effective_fonts(r.find(W + "rPr"), sp, ctx.src_styles, ctx.src_theme)
                        for slot in slots:
                            if es.get(slot) and es[slot].casefold() in preserve:
                                had[slot].add(es[slot])
                        src_sizes.add(effective_size(r.find(W + "rPr"), sp, ctx.src_styles))
                for k, q in enumerate(ov.runs[oi]):
                    if not any(c.tag in (W + "t", W + "delText") for c in q):
                        continue
                    eo = effective_fonts(q.find(W + "rPr"), op, ctx.out_styles, ctx.out_theme)
                    checked += 1
                    for slot in slots:
                        if eo.get(slot) == value:
                            continue
                        if eo.get(slot) in had[slot]:
                            preserved += 1
                            continue
                        bad += 1
                        if len(bad_examples) < 5:
                            bad_examples.append(f"{part} block {bi} (text-edited) output run {k} {slot}: {eo.get(slot)!r}")
                    if want_sz is not None and not exempt(q.find(W + "rPr"), op, q):
                        zo = effective_size(q.find(W + "rPr"), op, ctx.out_styles)
                        if not (zo == want_sz or (frm is not None and zo not in frm and zo in src_sizes)):
                            bad += 1
                            if len(bad_examples) < 5:
                                bad_examples.append(f"{part} block {bi} (text-edited) output run {k} size: {zo}")
                pairs = []
                if a.get("include_paragraph_marks", True):
                    pairs.append((sp.find(W + "pPr/" + W + "rPr"), op.find(W + "pPr/" + W + "rPr"), "mark", None))
            elif len(sv.runs[bi]) == len(ov.runs[oi]):
                pairs = [(r.find(W + "rPr"), q.find(W + "rPr"), f"run {k}", q)
                         for k, (r, q) in enumerate(zip(sv.runs[bi], ov.runs[oi]))
                         if any(c.tag in (W + "t", W + "delText") for c in r)]
            else:
                pairs = []
                errs.append(f"effective_font: {part} block {bi}: run structure changed")
            if bi not in tc and a.get("include_paragraph_marks", True):
                pairs.append((sp.find(W + "pPr/" + W + "rPr"), op.find(W + "pPr/" + W + "rPr"), "mark", None))
            for s_rpr, o_rpr, label, o_run in pairs:
                checked += 1
                if slots:
                    es = effective_fonts(s_rpr, sp, ctx.src_styles, ctx.src_theme)
                    eo = effective_fonts(o_rpr, op, ctx.out_styles, ctx.out_theme)
                    for slot in slots:
                        src_v = es.get(slot)
                        if src_v and src_v.casefold() in preserve:
                            preserved += 1
                            ok = eo.get(slot) == src_v
                        else:
                            ok = eo.get(slot) == value
                        if not ok:
                            bad += 1
                            if len(bad_examples) < 5:
                                bad_examples.append(f"{part} block {bi} {label} {slot}: {src_v!r}→{eo.get(slot)!r}")
                if want_sz is not None and not exempt(o_rpr, op, o_run) \
                        and not symbolic(s_rpr, sp, ctx.src_styles, preserve):
                    zs = effective_size(s_rpr, sp, ctx.src_styles)
                    zo = effective_size(o_rpr, op, ctx.out_styles)
                    if not size_ok(zs, zo):
                        bad += 1
                        if len(bad_examples) < 5:
                            bad_examples.append(f"{part} block {bi} {label} size: {zs}→{zo} half-points")
    if bad:
        errs.append(f"effective_font: {bad} slot(s) do not resolve as asserted, e.g. {bad_examples}")
    what = " and ".join(x for x in (f"{value!r} on {slots}" if slots else "",
                                    f"size {a['size']:g} pt" + (f" (from {sorted(a['size_from'])})" if frm else "")
                                    if want_sz is not None else "") if x)
    return f"effective_font: {checked} run(s)/mark(s) resolve to {what} ({preserved} preserved symbol slot(s))"


# ---- set_properties: the V10 kind "properties" ------------------------------------------------------------------
# This module's own reading of the op's definitions (docs/SET_PROPERTIES_DESIGN.md §3; transform side:
# ops/effective.PropResolver and ops/set_properties.py — CLAUDE.md lists them among the definitions that must stay
# identical on both sides). Nothing here is shared with the transform side.

PROP_PAIRS = {"b": ("b", "bCs"), "i": ("i", "iCs"), "caps": ("caps",), "smallCaps": ("smallCaps",),
              "strike": ("strike",)}
RUN_TOGGLE_ELEMENTS = ("b", "bCs", "i", "iCs", "caps", "smallCaps", "strike")
PARA_FLAGS = ("keepNext", "keepLines", "widowControl", "pageBreakBefore")
PARA_FLAG_DEFAULTS = {"keepNext": False, "keepLines": False, "widowControl": True, "pageBreakBefore": False}
CASE_ELEMENTS = {"caps", "smallCaps"}          # never on a symbol run: case-mapping a dingbat changes the glyph
COLOR_DEFAULT = ("auto", None, None, None)


def _v_on_off(el) -> Optional[bool]:
    """A CT_OnOff element's value: None when absent, else on unless w:val is 0/false/off."""
    return None if el is None else el.get(W + "val") not in FALSY


def _v_prop_read(element: str, el):
    """The value an element gives its property: on/off for toggles and paragraph flags, the (val, themeColor,
    themeShade, themeTint) tuple for w:color, w:val for w:u (None when the attribute is missing — never "already")."""
    if el is None:
        return None
    if element == "color":
        return (el.get(W + "val"), el.get(W + "themeColor"), el.get(W + "themeShade"), el.get(W + "themeTint"))
    if element == "u":
        return el.get(W + "val")
    return _v_on_off(el)


def _v_run_elements(run_set) -> Dict[str, Any]:
    """An assertion's user-facing run properties → the asked value per element: b/i expand to their complex-script
    pairs (a w:cs run draws its weight and slant from w:bCs/w:iCs), color to the effective tuple, u to its w:val."""
    out: Dict[str, Any] = {}
    for k, v in (run_set or {}).items():
        if k in PROP_PAIRS:
            for e in PROP_PAIRS[k]:
                out[e] = bool(v)
        elif k == "color":
            out["color"] = (str(v), None, None, None)
        elif k == "u":
            out["u"] = str(v)
        else:
            raise ValueError(f"unknown run property {k!r}")
    if out.get("caps") is True and out.get("smallCaps") is True:
        raise ValueError("caps and smallCaps both asked on — mutually exclusive in Word (the op must refuse)")
    return out


def _v_para_elements(para_set) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for k, v in (para_set or {}).items():
        if k not in PARA_FLAGS:
            raise ValueError(f"unknown paragraph property {k!r}")
        out[k] = bool(v)
    return out


def _v_expected_attrs(element: str, asked, source_el) -> Dict[str, str]:
    """The attributes the output element must carry: toggles and flags on → none, off → val 0; color → exactly val
    (the theme attributes gone); u → the source element's attributes with val replaced."""
    if element == "color":
        return {W + "val": asked[0]}
    if element == "u":
        attrs = dict(source_el.attrib) if source_el is not None else {}
        attrs[W + "val"] = asked
        return attrs
    return {} if asked else {W + "val": "0"}


def _v_table_style_id(p, styles: Styles) -> Optional[str]:
    tbl = p
    while tbl is not None and tbl.tag != W + "tbl":
        tbl = tbl.getparent()
    if tbl is None:
        return None
    ts = tbl.find(W + "tblPr/" + W + "tblStyle")
    return ts.get(W + "val") if ts is not None else styles.default.get("table")


def _v_run_levels(rpr, p, styles: Styles) -> List[Tuple[str, Any]]:
    """(provenance, rPr) of every level below direct formatting, lowest first: docDefaults, the table style chain,
    the paragraph style chain (pStyle, else the default), the character style chain (rStyle, else the default) —
    each chain root-most first; the order of effective_fonts."""
    levels: List[Tuple[str, Any]] = [("docDefaults", styles.doc_defaults_rpr())]
    for sid in (_v_table_style_id(p, styles), _pstyle(p) or styles.default.get("paragraph"),
                _rstyle(rpr) or styles.default.get("character")):
        levels += [(f"style:{st.get(W + 'styleId')}", st.find(W + "rPr")) for st in reversed(styles.chain(sid))]
    return levels


def _v_para_levels(p, styles: Styles) -> List[Tuple[str, Any]]:
    levels: List[Tuple[str, Any]] = [("docDefaults", styles.doc_defaults_ppr())]
    for sid in (_v_table_style_id(p, styles), _pstyle(p) or styles.default.get("paragraph")):
        levels += [(f"style:{st.get(W + 'styleId')}", st.find(W + "pPr")) for st in reversed(styles.chain(sid))]
    return levels


def _v_resolve(levels: List[Tuple[str, Any]], direct, element: str) -> Tuple[Any, str]:
    """(value, provenance) of one property. Toggles: direct formatting wins; specified at exactly one style level →
    that value; nowhere → off; more than one → (None, "ambiguous") — Word's XOR is not modelled. w:u, w:color and
    the paragraph flags: the nearest level that specifies them, else the default. Provenance: "direct",
    "style:<id>", "docDefaults" or "default"."""
    d = direct.find(W + element) if direct is not None else None
    if d is not None:
        return _v_prop_read(element, d), "direct"
    if element in RUN_TOGGLE_ELEMENTS:
        specs = [(_v_prop_read(element, L.find(W + element)), prov) for prov, L in levels
                 if L is not None and L.find(W + element) is not None]
        if len(specs) == 1:
            return specs[0]
        return (False, "default") if not specs else (None, "ambiguous")
    for prov, L in reversed(levels):
        e = L.find(W + element) if L is not None else None
        if e is not None:
            return _v_prop_read(element, e), prov
    if element == "color":
        return COLOR_DEFAULT, "default"
    if element in PARA_FLAGS:
        return PARA_FLAG_DEFAULTS[element], "default"
    return "none", "default"


def _v_run_prop(rpr, p, styles: Styles, element: str) -> Tuple[Any, str]:
    return _v_resolve(_v_run_levels(rpr, p, styles), rpr, element)


def _v_para_prop(p, styles: Styles, element: str) -> Tuple[Any, str]:
    return _v_resolve(_v_para_levels(p, styles), p.find(W + "pPr"), element)


def _v_gets_run(rpr, p, styles: Styles, asked: Dict[str, Any], symbol: bool) -> Dict[str, Any]:
    """The asked elements this run or mark gets: those whose effective value in the source is not the asked one (an
    ambiguous toggle gets it — direct formatting wins); caps/smallCaps never on a symbol run."""
    return {e: v for e, v in asked.items()
            if not (symbol and e in CASE_ELEMENTS) and _v_run_prop(rpr, p, styles, e)[0] != v}


def _v_gets_para(p, styles: Styles, asked: Dict[str, bool]) -> Dict[str, bool]:
    return {e: v for e, v in asked.items() if _v_para_prop(p, styles, e)[0] != v}


def _v_style_gets(st, container: str, asked: Dict[str, Any]) -> Dict[str, Any]:
    """A style definition's own values (its rPr or pPr): the asked elements whose own value is not the asked one."""
    c = st.find(W + container)
    return {e: v for e, v in asked.items() if _v_prop_read(e, c.find(W + e) if c is not None else None) != v}


def _plain(attrs: Optional[Dict[str, str]]):
    return None if attrs is None else {etree.QName(k).localname: v for k, v in attrs.items()}


def _check_props_rpr(S, O, gets: Dict[str, Any], tracked: bool, author, where: str, errs: List[str]) -> None:
    """The output rPr ``O`` must be the source rPr ``S`` with exactly the elements in ``gets`` set as asked — every
    other child identical — and the change marker as the tracked/plain rules demand."""
    if O is None:
        errs.append(f"{where}: no rPr in the output")
        return
    for e, v in gets.items():
        oe = O.find(W + e)
        exp = _v_expected_attrs(e, v, S.find(W + e) if S is not None else None)
        got = dict(oe.attrib) if oe is not None else None
        if got != exp or (oe is not None and len(oe)):
            errs.append(f"{where}: w:{e} is {_plain(got)}, expected {_plain(exp)}")
    skip = set(gets) | {"rPrChange"}
    others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in skip]
    others_o = [sig(c) for c in O if _local(c) not in skip]
    if others_s != others_o:
        errs.append(f"{where}: run properties other than {sorted(gets)} changed")
    _check_rpr_change(S, O, tracked, author, where, errs)


def _check_props_ppr(S, O, gets: Dict[str, bool], tracked: bool, author, where: str, errs: List[str]) -> None:
    """The output pPr ``O`` must be the source pPr ``S`` with exactly the flags in ``gets`` set as asked; the
    paragraph mark's rPr is another assertion's (or the residual's) business."""
    if O is None:
        errs.append(f"{where}: no pPr in the output")
        return
    for e, v in gets.items():
        oe = O.find(W + e)
        exp = _v_expected_attrs(e, v, None)
        got = dict(oe.attrib) if oe is not None else None
        if got != exp or (oe is not None and len(oe)):
            errs.append(f"{where}: w:{e} is {_plain(got)}, expected {_plain(exp)}")
    skip = set(gets) | {"pPrChange", "rPr"}
    others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in skip]
    others_o = [sig(c) for c in O if _local(c) not in skip]
    if others_s != others_o:
        errs.append(f"{where}: paragraph properties other than {sorted(gets)} changed")
    _check_ppr_change(S, O, tracked, author, where, errs)


def _check_effective_run(o_rpr, o_p, styles: Styles, asked: Dict[str, Any], symbol: bool, where: str,
                         errs: List[str]) -> None:
    """Post-condition on a selected run or mark of the OUTPUT: every asked element resolves to the asked value."""
    for e, v in asked.items():
        if symbol and e in CASE_ELEMENTS:
            continue
        val, prov = _v_run_prop(o_rpr, o_p, styles, e)
        if val != v:
            errs.append(f"{where}: w:{e} resolves to {val!r} ({prov}) in the output, asked {v!r}")
    _check_caps_exclusive(o_rpr, o_p, styles, asked, symbol, where, errs)


def _check_caps_exclusive(o_rpr, o_p, styles: Styles, asked: Dict[str, Any], symbol: bool, where: str,
                          errs: List[str]) -> None:
    """Word shows all caps and ignores small caps when both are in effect: an op that asks either on must leave the
    output with at most one of them in effect (an ambiguous toggle counts as possibly in effect)."""
    if symbol or not (asked.get("caps") is True or asked.get("smallCaps") is True):
        return
    c, s = _v_run_prop(o_rpr, o_p, styles, "caps")[0], _v_run_prop(o_rpr, o_p, styles, "smallCaps")[0]
    if c is not False and s is not False:
        errs.append(f"{where}: w:caps and w:smallCaps are both in effect in the output — Word shows all caps and ignores "
                    f"small caps (the op must refuse, or set the other one off)")


def _check_effective_para(o_p, styles: Styles, asked: Dict[str, bool], where: str, errs: List[str]) -> None:
    for e, v in asked.items():
        val, prov = _v_para_prop(o_p, styles, e)
        if val != v:
            errs.append(f"{where}: w:{e} resolves to {val!r} ({prov}) in the output, asked {v!r}")


def _v_mark_in_revision(mrpr) -> bool:
    return mrpr is not None and any(mrpr.find(W + t) is not None for t in ("ins", "del", "moveFrom", "moveTo"))


def _properties_direct(ctx: Ctx, a: Dict[str, Any], run_asked: Dict[str, Any], para_asked: Dict[str, bool],
                       errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", []))
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"properties: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    skip_sym = bool(a.get("skip_sym_runs", True))
    sel = a.get("select") or {}
    runs_pred, para_pred = sel.get("runs", "all"), sel.get("paragraphs")
    exclude_rev = a.get("in_revisions", "include") == "exclude"
    marks_too = bool(a.get("include_paragraph_marks", True))
    tracked, author = bool(a.get("tracked")), a.get("author")
    n_runs = n_marks = n_paras = 0
    for part in parts:
        if part not in ctx.both:
            errs.append(f"properties: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        amap = ctx.maps[part]
        for bi, p in enumerate(sv.pars):
            if not para_matches(para_pred, p, bi, sv, ctx.src_styles):
                continue
            cands = []                                   # (run index, the elements it gets, symbol run)
            if run_asked:
                for ri, r in enumerate(sv.runs[bi]):
                    rpr = r.find(W + "rPr")
                    in_rev = _has_ancestor(r, REVISION_WRAPPERS, p)
                    if (exclude_rev and in_rev) or not target_matches(
                            runs_pred, rpr, in_rev, preserve,
                            lambda rpr=rpr, p=p: _v_run_prop(rpr, p, ctx.src_styles, "color")[0]):
                        continue
                    sym = symbolic(rpr, p, ctx.src_styles, preserve) or (skip_sym and r.find(W + "sym") is not None)
                    cands.append((ri, _v_gets_run(rpr, p, ctx.src_styles, run_asked, sym), sym))
            mark = None                                  # (the elements the mark gets, symbol) when selected
            if run_asked and marks_too:
                mrpr = p.find(W + "pPr/" + W + "rPr")
                m_rev = _v_mark_in_revision(mrpr)
                if not (exclude_rev and m_rev) and target_matches(
                        runs_pred, mrpr, m_rev, preserve,
                        lambda mrpr=mrpr, p=p: _v_run_prop(mrpr, p, ctx.src_styles, "color")[0]):
                    msym = symbolic(mrpr, p, ctx.src_styles, preserve)
                    mark = (_v_gets_run(mrpr, p, ctx.src_styles, run_asked, msym), msym)
            pgets = _v_gets_para(p, ctx.src_styles, para_asked) if para_asked else {}
            if not cands and mark is None and not para_asked:
                continue
            targeted = any(g for _, g, _ in cands) or bool(mark and mark[0]) or bool(pgets)
            if bi in ctx.text_changed.get(part, set()):
                if targeted:
                    errs.append(f"properties: {part} block {bi} is selected but also has predicted text changes")
                continue
            oi = amap.get(bi)
            if oi is None or len(ov.runs[oi]) != len(sv.runs[bi]):
                errs.append(f"properties: {part} block {bi}: run structure changed")
                continue
            o_p = ov.pars[oi]
            for ri, gets, sym in cands:
                s_rpr, o_rpr = sv.runs[bi][ri].find(W + "rPr"), ov.runs[oi][ri].find(W + "rPr")
                where = f"{part} block {bi} run {ri}"
                if gets:
                    n_runs += 1
                    ctx.strip_runs.setdefault(part, set()).add((bi, ri))
                    _check_props_rpr(s_rpr, o_rpr, gets, tracked, author, where, errs)
                _check_effective_run(o_rpr, o_p, ctx.out_styles, run_asked, sym, where, errs)
            if mark is not None:
                gets, msym = mark
                s_rpr, o_rpr = p.find(W + "pPr/" + W + "rPr"), o_p.find(W + "pPr/" + W + "rPr")
                where = f"{part} block {bi} paragraph mark"
                if gets:
                    n_marks += 1
                    ctx.strip_marks.setdefault(part, set()).add(bi)
                    _check_props_rpr(s_rpr, o_rpr, gets, tracked, author, where, errs)
                _check_effective_run(o_rpr, o_p, ctx.out_styles, run_asked, msym, where, errs)
            if para_asked:
                where = f"{part} block {bi}"
                if pgets:
                    n_paras += 1
                    ctx.strip_ppr.setdefault(part, {})[bi] = set(pgets) | {"pPrChange"}
                    _check_props_ppr(p.find(W + "pPr"), o_p.find(W + "pPr"), pgets, tracked, author, where, errs)
                _check_effective_para(o_p, ctx.out_styles, para_asked, where, errs)
    for n, key, what in ((n_runs, "expected_runs", "run(s)"), (n_marks, "expected_marks", "paragraph mark(s)"),
                         (n_paras, "expected_paragraphs", "paragraph(s)")):
        if n != a.get(key, -1):
            errs.append(f"properties: {n} {what} get the change here, plan predicted {a.get(key)}")
    setv = a.get("set") or {}
    return (f"properties: {n_runs} run(s) + {n_marks} mark(s) → {setv.get('run') or {}}, {n_paras} paragraph(s) → "
            f"{setv.get('paragraph') or {}}" + (" (tracked)" if tracked else ""))


def _judge_style_prop(val, prov: str, asked, sid: str, element: str, symbol: bool, where: str,
                      errs: List[str]) -> None:
    """One run, mark or paragraph that uses a selected style, in the OUTPUT: the value it shows must come from that
    style and be the asked one, or from a nearer level's own definition (normal inheritance). A disagreeing direct
    value, an ambiguous toggle, or caps/smallCaps reaching a symbol run from the style are what the op must have
    refused."""
    if prov == f"style:{sid}":
        if symbol and element in CASE_ELEMENTS and asked:
            errs.append(f"{where}: w:{element} from style {sid} would case-map a symbol run (the op must refuse)")
        elif val != asked:
            errs.append(f"{where}: w:{element} resolves to {val!r} from style {sid}, asked {asked!r}")
    elif prov == "ambiguous":
        errs.append(f"{where}: w:{element} is specified at several style levels — Word XORs them (the op must refuse)")
    elif prov == "direct" and val != asked:
        errs.append(f"{where}: direct formatting w:{element} {val!r} overrides style {sid} (the op must refuse)")


def _properties_styles(ctx: Ctx, a: Dict[str, Any], run_asked: Dict[str, Any], para_asked: Dict[str, bool],
                       errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("properties: no common styles part")
        return "properties: no styles part"
    ids = list((a.get("select") or {}).get("ids") or [])
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    skip_sym = bool(a.get("skip_sym_runs", True))
    changed: List[str] = []
    types: Dict[str, str] = {}
    for sid in ids:
        st = ctx.src_styles.by_id.get(sid)
        if st is None:
            errs.append(f"properties: style {sid} is not in the source")
            continue
        types[sid] = st.get(W + "type", "paragraph")
        if para_asked and types[sid] != "paragraph":
            errs.append(f"properties: paragraph properties asked on {types[sid]} style {sid}")
        gets_r = _v_style_gets(st, "rPr", run_asked)
        gets_p = _v_style_gets(st, "pPr", para_asked) if types[sid] == "paragraph" else {}
        if not gets_r and not gets_p:
            continue
        changed.append(sid)
        o = ctx.out_styles.by_id.get(sid)
        if o is None:
            errs.append(f"properties: style {sid} missing from the output")
            continue
        if gets_r:
            ctx.strip_styles.add(sid)
            _check_props_rpr(st.find(W + "rPr"), o.find(W + "rPr"), gets_r, False, None, f"style {sid}", errs)
        if gets_p:
            ctx.strip_style_ppr.setdefault(sid, set()).update(gets_p)
            _check_props_ppr(st.find(W + "pPr"), o.find(W + "pPr"), gets_p, False, None, f"style {sid}", errs)
    if sorted(changed) != sorted(a.get("expected_ids", [])):
        errs.append(f"properties: style(s) {sorted(changed)} change here, plan predicted "
                    f"{sorted(a.get('expected_ids', []))}")
    # post-condition over every text part of the output: whatever uses a selected style shows the asked value
    out_styles = ctx.out_styles
    for part in ctx.both:
        _, ov = ctx.view(part)
        for oi, p in enumerate(ov.pars):
            pchain = {st.get(W + "styleId") for st in out_styles.chain(_pstyle(p) or out_styles.default.get("paragraph"))}
            holders = [(r.find(W + "rPr"), f"{part} block {oi} run {k}", r.find(W + "sym") is not None)
                       for k, r in enumerate(ov.runs[oi])]
            holders.append((p.find(W + "pPr/" + W + "rPr"), f"{part} block {oi} paragraph mark", False))
            for sid in ids:
                if sid not in types:
                    continue
                for rpr, where, has_sym in holders:
                    if types[sid] == "paragraph":
                        uses = sid in pchain
                    else:
                        uses = sid in {st.get(W + "styleId") for st in
                                       out_styles.chain(_rstyle(rpr) or out_styles.default.get("character"))}
                    if not uses:
                        continue
                    symbol = symbolic(rpr, p, out_styles, preserve) or (skip_sym and has_sym)
                    for e, v in run_asked.items():
                        val, prov = _v_run_prop(rpr, p, out_styles, e)
                        _judge_style_prop(val, prov, v, sid, e, symbol, where, errs)
                    _check_caps_exclusive(rpr, p, out_styles, run_asked, symbol, where, errs)
                if types[sid] == "paragraph" and sid in pchain:
                    for e, v in para_asked.items():
                        val, prov = _v_para_prop(p, out_styles, e)
                        _judge_style_prop(val, prov, v, sid, e, False, f"{part} block {oi}", errs)
    return f"properties: style(s) {changed} → {a.get('set')}"


def _properties(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    """set_properties' assertion: exactly the asked properties on exactly the re-derived targets, nothing else, and the
    asked value shown by every target (and everything that uses a changed style) in the output."""
    setv = a.get("set") or {}
    if set(setv) - {"run", "paragraph"}:
        raise ValueError(f"unknown property group(s) {sorted(set(setv) - {'run', 'paragraph'})}")
    run_asked = _v_run_elements(setv.get("run"))
    para_asked = _v_para_elements(setv.get("paragraph"))
    if not run_asked and not para_asked:
        raise ValueError("nothing asked")
    target = a.get("target")
    if target == "direct":
        return _properties_direct(ctx, a, run_asked, para_asked, errs)
    if target == "styles":
        return _properties_styles(ctx, a, run_asked, para_asked, errs)
    raise ValueError(f"unknown target {target!r}")


KINDS = {
    "run_fonts": _run_fonts,
    "style_fonts": _style_fonts,
    "doc_defaults_fonts": _doc_defaults_fonts,
    "paragraph_props": _paragraph_props,
    "section_props": _section_props,
    "section_hf_refs": _section_hf_refs,
    "cover_unnumbered": _cover_unnumbered,
    "effective_font": _effective_font,
    "deleted_paragraphs": _deleted_paragraphs,
    "inserted_paragraphs": _inserted_paragraphs,
    "properties": _properties,
}


# ---- residual ---------------------------------------------------------------------------------------

def element_path(el) -> Tuple[int, ...]:
    """The element's place in its tree: the child index at every level from the root (comments and processing
    instructions count, as they do in ``list(parent)``)."""
    out = []
    while el.getparent() is not None:
        out.append(el.getparent().index(el))
        el = el.getparent()
    return tuple(reversed(out))


def resolve_path(root, path: Sequence[int]):
    """The element at ``path`` (``element_path``) in ``root`` or a copy of it, or None."""
    el = root
    for i in path:
        kids = list(el)
        if not 0 <= i < len(kids):
            return None
        el = kids[i]
    return el


def _residual_difference(a, b) -> Optional[str]:
    """Byte-identical canonical forms are identical trees (fast path); otherwise walk the trees with the
    significance rules of sig() — whitespace-only differences outside text elements are not changes."""
    if etree.tostring(a, method="c14n") == etree.tostring(b, method="c14n"):
        return None
    return first_difference(a, b)


def _drop_empty(el):
    """Remove ``el`` when it has no attributes and no element children (after stripping)."""
    if el is not None and not len(el) and not el.attrib and el.getparent() is not None:
        el.getparent().remove(el)


def _ppr_without_mark_del(ppr):
    if ppr is None:
        return None
    c = copy.deepcopy(ppr)
    rpr = c.find(W + "rPr")
    if rpr is not None:
        for d in list(rpr.findall(W + "del")):
            rpr.remove(d)
        _drop_empty(rpr)
    _drop_empty(c)
    return c if (len(c) or c.attrib) else None


_WP_EXTENT = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"
_PIC_EXT = ("{http://schemas.openxmlformats.org/drawingml/2006/main}graphic/"
            "{http://schemas.openxmlformats.org/drawingml/2006/main}graphicData/"
            "{http://schemas.openxmlformats.org/drawingml/2006/picture}pic/"
            "{http://schemas.openxmlformats.org/drawingml/2006/picture}spPr/"
            "{http://schemas.openxmlformats.org/drawingml/2006/main}xfrm/"
            "{http://schemas.openxmlformats.org/drawingml/2006/main}ext")


def mask_picture_sizes(drawing) -> None:
    """Blank the two sizes of a picture (wp:extent and its pic:spPr a:xfrm/a:ext) — exactly what image_swaps checks."""
    for frame in drawing:
        if not isinstance(frame.tag, str):
            continue
        for e in [frame.find(_WP_EXTENT), frame.find(_PIC_EXT)]:
            if e is not None:
                e.set("cx", "masked")
                e.set("cy", "masked")


def _strip_table_props(ctx: Ctx, part: str, s_root, o_root) -> None:
    """Built-in batch 5: the table, row and cell property elements a kind checked (``strip_tbl``/``strip_tr``/
    ``strip_tc``) leave both sides; a row's ``w:trPr`` or a cell's ``w:tcPr`` left empty by that goes on both sides."""
    by_tbl = ctx.strip_tbl.get(part, {})
    by_tr = ctx.strip_tr.get(part, {})
    by_tc = ctx.strip_tc.get(part, {})
    if not (by_tbl or by_tr or by_tc):
        return

    def strip(holder, names: Set[str]) -> None:
        if holder is None:
            return
        for c in list(holder):
            if _local(c) in names:
                holder.remove(c)
        _drop_empty(holder)

    for root in (s_root, o_root):
        tables = list(root.iter(W + "tbl"))               # ordinals: no op adds, removes or moves a table
        rows = lambda k: [c for c in tables[k] if c.tag == W + "tr"] if k < len(tables) else []
        for k, names in by_tbl.items():
            if k < len(tables):
                pr = tables[k].find(W + "tblPr")
                if pr is not None:
                    for c in list(pr):
                        if _local(c) in names:
                            pr.remove(c)
        for (k, r), names in by_tr.items():
            trs = rows(k)
            if r < len(trs):
                strip(trs[r].find(W + "trPr"), names)
        for (k, r, c), names in by_tc.items():
            trs = rows(k)
            tcs = [x for x in trs[r] if x.tag == W + "tc"] if r < len(trs) else []
            if c < len(tcs):
                strip(tcs[c].find(W + "tcPr"), names)


def _residual_text_part(ctx: Ctx, part: str, errs: List[str]) -> None:
    sv, ov = ctx.view(part)
    s_root = etree.fromstring(etree.tostring(sv.root))   # private copies: stripping must not disturb the views
    o_root = etree.fromstring(etree.tostring(ov.root))
    # elements outside paragraphs a kind expects gone (batch 2: a comment's range marks between blocks): found in the
    # SOURCE copy by their path before anything else is stripped, removed from it after — the output must lack exactly
    # those, and nothing else may differ
    gone = []
    for path in ctx.expected_removed.get(part, []):
        el = resolve_path(s_root, path)
        if el is None or el.tag == W + "p" or _nearest_p(el) is not None or any(True for _ in el.iter(W + "p")):
            errs.append(f"{part}: an expected removal at {list(path)} is not an element outside the paragraphs")
            continue
        gone.append(el)
    masked = ctx.masked_extents.get(part, set())
    if masked:                                         # the sizes image_swaps checked, by drawing ordinal (never moved)
        for root in (s_root, o_root):
            drawings = list(root.iter(W + "drawing"))
            for k in masked:
                if k < len(drawings):
                    mask_picture_sizes(drawings[k])
    s, o = PartView(s_root), PartView(o_root)
    amap = ctx.maps[part]
    vb = ctx.verified_blocks.get(part, {})            # checked item by item by an edit kind (verify/edits.py)
    tc = ctx.text_changed.get(part, set()) | set(vb)
    del_src = ctx.deleted_src_blocks.get(part, set())
    ins_out = ctx.inserted_out_blocks.get(part, set())
    out_of = lambda b: vb[b] if b in vb else amap.get(b)
    # predicted text changes: pPr identical, no new run-property signature, then out of the residual (a block an edit
    # kind checked item by item has had every character's properties compared already — a filled highlight slot's
    # run legitimately has a signature the source block never had)
    for bi in sorted(tc):
        if bi >= len(s.pars) or bi in del_src or bi in vb:
            continue
        oi = amap.get(bi)
        if oi is not None:
            sppr = s.pars[bi].find(W + "pPr")
            oppr = o.pars[oi].find(W + "pPr")
            if sig(sppr) != sig(oppr) and sig(_ppr_without_mark_del(sppr)) != sig(_ppr_without_mark_del(oppr)):
                errs.append(f"{part} block {bi}: paragraph properties changed in a text-edited block")
            src_sigs = {_rpr_sig_for_tc(r.find(W + "rPr")) for r in s.runs[bi]}
            new = [r for r in o.runs[oi] if _rpr_sig_for_tc(r.find(W + "rPr")) not in src_sigs]
            if new:
                errs.append(f"{part} block {bi}: {len(new)} run(s) with run properties the source block never had")
    # strip what assertions cover (source coordinates → aligned output)
    for bi, ri in ctx.strip_runs.get(part, set()):
        oi = amap.get(bi)
        for view, b in ((s, bi), (o, oi)):
            if b is not None and ri < len(view.runs[b]):
                rpr = view.runs[b][ri].find(W + "rPr")
                if rpr is not None:
                    view.runs[b][ri].remove(rpr)
    for bi in ctx.strip_marks.get(part, set()):
        for view, b in ((s, bi), (o, amap.get(bi))):
            if b is None:
                continue
            ppr = view.pars[b].find(W + "pPr")
            if ppr is not None and ppr.find(W + "rPr") is not None:
                ppr.remove(ppr.find(W + "rPr"))
                _drop_empty(ppr)
    for bi, names in ctx.strip_ppr.get(part, {}).items():
        for view, b in ((s, bi), (o, amap.get(bi))):
            if b is None:
                continue
            ppr = view.pars[b].find(W + "pPr")
            if ppr is None:
                continue
            for c in list(ppr):
                if _local(c) in names:
                    ppr.remove(c)
            _drop_empty(ppr)
    tbls = ctx.strip_tbl_style.get(part, set())
    if tbls:                                           # ordinals among every w:tbl of the part (no op moves tables)
        for root in (s_root, o_root):
            tables = list(root.iter(W + "tbl"))
            for k in tbls:
                ts = tables[k].find(W + "tblPr/" + W + "tblStyle") if k < len(tables) else None
                if ts is not None:
                    ts.getparent().remove(ts)
    _strip_table_props(ctx, part, s_root, o_root)
    if part == ctx.src.main_part() and ctx.strip_sect:
        ss, os_ = sect_prs(s_root), sect_prs(o_root)
        for i, names in ctx.strip_sect.items():
            for secs in (ss, os_):
                if i < len(secs):
                    for c in list(secs[i]):
                        if _local(c) in names:
                            secs[i].remove(c)
    # remove text-changed blocks, deleted source blocks, and inserted output blocks
    def remove_blocks(view, idxs):
        els = [view.pars[i] for i in sorted(idxs) if i < len(view.pars)]
        chosen = set(map(id, els))
        for el in els:
            q, inside = el.getparent(), False
            while q is not None:
                if id(q) in chosen:
                    inside = True
                    break
                q = q.getparent()
            if not inside and el.getparent() is not None:
                el.getparent().remove(el)
    remove_blocks(s, tc | del_src)
    remove_blocks(o, {out_of(b) for b in tc if out_of(b) is not None} | ins_out)
    for el in gone:
        if el.getparent() is not None:
            el.getparent().remove(el)
    d = _residual_difference(s_root, o_root)
    if d:
        errs.append(f"{part}: unpredicted change {d}")


def _residual_styles(ctx: Ctx, errs: List[str]) -> None:
    s_root = etree.fromstring(etree.tostring(ctx.src_styles_root))
    o_root = etree.fromstring(etree.tostring(ctx.out_styles_root))
    for root in (s_root, o_root):
        if ctx.strip_doc_defaults:
            rpr = root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")
            if rpr is not None:
                parent = rpr.getparent()
                parent.remove(rpr)
                _drop_empty(parent)
                _drop_empty(root.find(W + "docDefaults"))
        # only the definition the kinds checked: the LAST w:style of an id (``Styles.by_id`` keeps it, as the
        # transform's sheet does) — another one sharing the id is compared whole, never stripped unchecked (rule 8)
        last = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
        for st in root.findall(W + "style"):
            if last.get(st.get(W + "styleId")) is not st:
                continue
            if st.get(W + "styleId") in ctx.strip_styles and st.find(W + "rPr") is not None:
                st.remove(st.find(W + "rPr"))
            names = ctx.strip_style_ppr.get(st.get(W + "styleId"))
            ppr = st.find(W + "pPr") if names else None
            if ppr is not None:
                for c in list(ppr):
                    if _local(c) in names:
                        ppr.remove(c)
                _drop_empty(ppr)
    for st in o_root.findall(W + "style"):            # new styles: checked by styles_added, not residual
        if st.get(W + "styleId") in ctx.added_styles:
            o_root.remove(st)
    for root in (s_root, o_root):                      # replaced definitions: compared whole by their kind
        for st in root.findall(W + "style"):
            if st.get(W + "styleId") in ctx.replaced_styles:
                root.remove(st)
    for st in s_root.findall(W + "style"):            # expected gone: an output that still holds one differs
        if st.get(W + "styleId") in ctx.removed_styles:
            s_root.remove(st)
    d = _residual_difference(s_root, o_root)
    if d:
        errs.append(f"{ctx.styles_part}: unpredicted change {d}")


def member_key(el) -> str:
    """The key a kind registers for a top-level child of a settings/docProps member it verified (``Ctx.member_strip``):
    a custom property by its name, anything else by its qualified tag."""
    if etree.QName(el).localname == "property" and el.get("name") is not None:
        return "property:" + el.get("name")
    return el.tag


def _residual_members(ctx: Ctx, errs: List[str]) -> List[str]:
    """Built-in batch 2 (design note §1.2): ``settings.xml`` and the document-properties parts were untouchable until
    batch 2, so no kind looked at them — now every change to one must be covered. The keys a kind verified
    (``member_strip``) are stripped on both sides and the rest compared element for element; a member a kind compared
    whole (``verified_members``) is skipped; one created without that is a failure."""
    checked = []
    for member in sorted(protected_members(ctx.src) | protected_members(ctx.out)):
        if member in ctx.verified_members:
            continue
        in_src, in_out = member in ctx.src.names, member in ctx.out.names
        if not in_out:
            continue                                    # a removal is V1's (predicted members_removed or a failure)
        if not in_src:
            errs.append(f"{member}: created, and no assertion checked what it holds")
            continue
        if ctx.src.read(member) == ctx.out.read(member):
            continue
        checked.append(member)
        keys = ctx.member_strip.get(member, set())
        s_root, o_root = ctx.src.xml(member), ctx.out.xml(member)
        for root in (s_root, o_root):
            for c in list(root):
                if isinstance(c.tag, str) and member_key(c) in keys:
                    root.remove(c)
        d = _residual_difference(s_root, o_root)
        if d:
            errs.append(f"{member}: unpredicted change {d}")
    return checked


def check_properties(src: VPackage, out: VPackage, plan: Dict[str, Any], source_name: Optional[str] = None,
                     output_name: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    ctx = Ctx(src, out, plan, source_name, output_name)
    errs: List[str] = []
    summaries: List[str] = []
    assertions = plan.get("property_assertions", []) or []
    for a in assertions:
        fn = KINDS.get(a.get("kind"))
        if fn is None:
            errs.append(f"unknown assertion kind {a.get('kind')!r} — cannot be evaluated")
            continue
        try:
            summaries.append(fn(ctx, a, errs))
        except ValueError as e:
            errs.append(f"{a.get('kind')}: {e}")
    residual_parts = []
    for part in ctx.both:
        # an expected removal is checked by the residual alone: a part a kind expects elements gone from is compared
        # even when its bytes did not change (they should have)
        if (src.read(part) != out.read(part) or ctx.expected_removed.get(part)) and part not in ctx.verified_parts:
            residual_parts.append(part)
            _residual_text_part(ctx, part, errs)
    if ctx.styles_part and src.read(ctx.styles_part) != out.read(ctx.styles_part):
        residual_parts.append(ctx.styles_part)
        _residual_styles(ctx, errs)
    residual_parts += _residual_members(ctx, errs)
    ok = not errs
    head = (f"{len(assertions)} assertion(s): " + "; ".join(summaries)) if assertions else "no property change predicted"
    summary = (head + f" | residual identity over {len(residual_parts)} changed part(s): "
               + ("everything not asserted is identical" if ok else f"{len(errs)} problem(s): {errs[:3]}"))
    return ok, summary, {"errors": errs[:50], "assertions": summaries, "residual_parts": residual_parts}


# header/footer templating kinds live in verify/template.py, built-in batch 1's word-changing kinds in verify/textops.py
# and its picture swap in verify/images.py, batch 2's in one module per op group (imported last: they use the helpers
# above)
from .template import TEMPLATE_KINDS  # noqa: E402
from .textops import TEXT_OP_KINDS  # noqa: E402
from .images import IMAGE_KINDS  # noqa: E402
# built-in batch 2 (docs/BUILTIN_OPS_BATCH2_DESIGN.md)
from .comments import COMMENT_KINDS  # noqa: E402
from .highlight import HIGHLIGHT_KINDS  # noqa: E402
from .revisions import REVISION_KINDS  # noqa: E402
from .removal import REMOVAL_KINDS  # noqa: E402
from .watermark import WATERMARK_KINDS  # noqa: E402
from .links import LINK_KINDS  # noqa: E402
from .docprops import DOCPROP_KINDS  # noqa: E402
# built-in batch 3 (docs/BUILTIN_OPS_BATCH3_DESIGN.md)
from .tabs import TAB_KINDS  # noqa: E402
# built-in batch 4 (docs/BUILTIN_OPS_BATCH4_DESIGN.md)
from .unused import UNUSED_KINDS  # noqa: E402
from .borders import BORDER_KINDS  # noqa: E402
from .clearing import CLEARING_KINDS  # noqa: E402
from .language import LANGUAGE_KINDS  # noqa: E402
# built-in batch 4 (docs/BUILTIN_OPS_BATCH4_DESIGN.md): the shared style import (§5) and map_styles (§7)
from .styleimport import IMPORT_KINDS  # noqa: E402
from .restyle import RESTYLE_KINDS  # noqa: E402
from .applystyle import APPLY_KINDS  # noqa: E402
# built-in batch 5 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §5): tables
from .tableprops import TABLE_PROP_KINDS  # noqa: E402
from .applytablestyle import APPLY_TABLE_KINDS  # noqa: E402
# built-in batch 5 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §3, §4): list levels and the numbering audit's fix
from .listlevel import LIST_LEVEL_KINDS  # noqa: E402
from .numberingaudit import AUDIT_KINDS  # noqa: E402

KINDS.update(TEMPLATE_KINDS)
KINDS.update(TEXT_OP_KINDS)
KINDS.update(IMAGE_KINDS)
for _batch2 in (COMMENT_KINDS, HIGHLIGHT_KINDS, REVISION_KINDS, REMOVAL_KINDS, WATERMARK_KINDS, LINK_KINDS, DOCPROP_KINDS):
    KINDS.update(_batch2)
KINDS.update(TAB_KINDS)
KINDS.update(UNUSED_KINDS)
KINDS.update(BORDER_KINDS)
KINDS.update(CLEARING_KINDS)
KINDS.update(LANGUAGE_KINDS)
KINDS.update(IMPORT_KINDS)
KINDS.update(RESTYLE_KINDS)
KINDS.update(APPLY_KINDS)
KINDS.update(TABLE_PROP_KINDS)
KINDS.update(APPLY_TABLE_KINDS)
KINDS.update(LIST_LEVEL_KINDS)
KINDS.update(AUDIT_KINDS)
