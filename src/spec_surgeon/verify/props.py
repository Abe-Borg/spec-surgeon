"""V10 — property assertions and formatting preservation.

Two halves, both computed here from the before/after packages and the plan's declarative assertions, with
this module's OWN selector resolution, style-chain walking and theme lookup (nothing shared with ops/ or
ooxml.py — a selector bug on the transform side must not be able to hide behind the same bug here):

1. **Assertions.** Each ``plan["property_assertions"]`` entry names a kind (run_fonts, style_fonts,
   doc_defaults_fonts, paragraph_props, section_props, section_hf_refs, effective_font, deleted_paragraphs,
   inserted_paragraphs), a selector, the property values and the predicted target count. The selection is
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

from .reader import VPackage, W, M, STYLES_REL, THEME_REL
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
    """Where two trees first differ (same significance rules as sig()), or None."""
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
        return f"{here}: attributes {', '.join(diff)}"
    ta, tb = a.text or "", b.text or ""
    if a.tag not in TEXT_TAGS:
        ta, tb = ta.strip(), tb.strip()
    if ta != tb:
        return f"{here}: text {ta!r}→{tb!r}"
    ca, cb = list(a), list(b)
    for i in range(min(len(ca), len(cb))):
        d = first_difference(ca[i], cb[i], f"{here}[{i}]")
        if d:
            return d
    if len(ca) != len(cb):
        extra = cb[len(ca):] if len(cb) > len(ca) else ca[len(cb):]
        names = [_name(e) for e in extra]
        return f"{here}: {len(ca)}→{len(cb)} children ({'added' if len(cb) > len(ca) else 'removed'} {names[:5]})"
    return None


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


# ---- selector resolution (independent of ops/selectors.py) ------------------------------------------

PARA_KEYS = {"style_in", "text_regex", "in_table", "is_empty", "index_range",
             "has_bold", "is_all_caps", "after_heading_regex", "between_paragraphs"}
RUN_KEYS = {"has_direct_font", "font_in", "not_in_revisions"}


def _v_toggle_on(rpr, p, styles: Styles, tag: str) -> bool:
    if rpr is not None:
        d = rpr.find(W + tag)
        if d is not None:
            return d.get(W + "val") not in FALSY
        for st in styles.chain(_rstyle(rpr)):
            sr = st.find(W + "rPr")
            if sr is not None and sr.find(W + tag) is not None:
                return sr.find(W + tag).get(W + "val") not in FALSY
    for st in styles.chain(_pstyle(p) or styles.default.get("paragraph")):
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
    if "style_in" in pred and (_pstyle(p) or styles.default.get("paragraph")) not in pred["style_in"]:
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


def target_matches(pred, rpr, in_revision: bool, preserve: Set[str]) -> bool:
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
    return True


# ---- the checks ------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, src: VPackage, out: VPackage, plan: Dict[str, Any]):
        self.src, self.out, self.plan = src, out, plan
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
            if d.get("before") is not None and d.get("after") is not None:
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


def _check_font_rpr(S, O, a: Dict[str, Any], where: str, errs: List[str]) -> None:
    """The selected rPr ``O`` must be the source rPr ``S`` with exactly the asserted rFonts change."""
    want = {k: v for k, v in (a.get("set") or {}).items() if v is not None}
    clear = bool(a.get("clear_theme", True))
    tracked = bool(a.get("tracked", False))
    if O is None:
        errs.append(f"{where}: no rPr in the output")
        return
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
    others_s = [sig(c) for c in (S if S is not None else []) if _local(c) not in ("rFonts", "rPrChange")]
    others_o = [sig(c) for c in O if _local(c) not in ("rFonts", "rPrChange")]
    if others_s != others_o:
        errs.append(f"{where}: run properties other than rFonts changed")
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
    if o_chg.get(W + "id") is None or o_chg.get(W + "author") != a.get("author"):
        errs.append(f"{where}: w:rPrChange needs w:id and author {a.get('author')!r}")
    inner = o_chg.findall(W + "rPr")
    snap = [sig(c) for c in (S if S is not None else []) if _local(c) != "rPrChange"]
    if len(inner) != 1 or [sig(c) for c in inner[0]] != snap or len(o_chg) != 1:
        errs.append(f"{where}: w:rPrChange snapshot is not the source rPr (reject-all would not restore it)")


def _run_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", []))
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"run_fonts: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    sel = a.get("select") or {}
    runs_pred, para_pred = sel.get("runs", "all"), sel.get("paragraphs")
    exclude_rev = a.get("in_revisions", "include") == "exclude"
    n_runs = n_marks = 0
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
                if target_matches(runs_pred, rpr, in_rev, preserve):
                    cand_runs.append(ri)
            mark = False
            if a.get("include_paragraph_marks", True):
                mrpr = p.find(W + "pPr/" + W + "rPr")
                m_rev = mrpr is not None and any(mrpr.find(W + t) is not None for t in ("ins", "del", "moveFrom", "moveTo"))
                if not (exclude_rev and m_rev) and not symbolic(mrpr, p, ctx.src_styles, preserve) \
                        and target_matches(runs_pred, mrpr, m_rev, preserve):
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
                _check_font_rpr(sv.runs[bi][ri].find(W + "rPr"), ov.runs[oi][ri].find(W + "rPr"), a,
                                f"{part} block {bi} run {ri}", errs)
            if mark:
                n_marks += 1
                ctx.strip_marks.setdefault(part, set()).add(bi)
                _check_font_rpr(p.find(W + "pPr/" + W + "rPr"), ov.pars[oi].find(W + "pPr/" + W + "rPr"), a,
                                f"{part} block {bi} paragraph mark", errs)
    if n_runs != a.get("expected_runs", -1):
        errs.append(f"run_fonts: {n_runs} run(s) selected here, plan predicted {a.get('expected_runs')}")
    if n_marks != a.get("expected_marks", 0 if not a.get("include_paragraph_marks", True) else -1):
        errs.append(f"run_fonts: {n_marks} paragraph mark(s) selected here, plan predicted {a.get('expected_marks')}")
    return f"run_fonts: {n_runs} run(s) + {n_marks} mark(s) → {a.get('set')}"


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
        if a.get("only_where_defined", True) and _rfonts(st.find(W + "rPr")) is None:
            continue
        out.append(sid)
    return out


def _style_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("style_fonts: no common styles part")
        return "style_fonts: no styles part"
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    ids = _select_styles(ctx.src_styles, a, preserve)
    if sorted(ids) != sorted(a.get("expected_ids", [])):
        errs.append(f"style_fonts: selects {sorted(ids)} here, plan predicted {sorted(a.get('expected_ids', []))}")
    for sid in ids:
        o = ctx.out_styles.by_id.get(sid)
        if o is None:
            errs.append(f"style_fonts: style {sid} missing from the output")
            continue
        ctx.strip_styles.add(sid)
        _check_font_rpr(ctx.src_styles.by_id[sid].find(W + "rPr"), o.find(W + "rPr"), {**a, "tracked": False},
                        f"style {sid}", errs)
    return f"style_fonts: {len(ids)} style(s) → {a.get('set')}"


def _doc_defaults_fonts(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("doc_defaults_fonts: no common styles part")
        return "doc_defaults_fonts: no styles part"
    ctx.strip_doc_defaults = True
    _check_font_rpr(ctx.src_styles.doc_defaults_rpr(), ctx.out_styles.doc_defaults_rpr(), {**a, "tracked": False},
                    "docDefaults/rPrDefault", errs)
    return f"doc_defaults_fonts → {a.get('set')}"


PPR_SET_ELEMENTS = ("spacing", "ind", "jc")


def _paragraph_props(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
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
            if bi in ctx.text_changed.get(part, set()):
                errs.append(f"paragraph_props: {part} block {bi} is selected but also has predicted text changes")
                continue
            oi = ctx.maps[part].get(bi)
            if oi is None:
                errs.append(f"paragraph_props: {part} block {bi} has no counterpart in the output")
                continue
            ctx.strip_ppr.setdefault(part, {})[bi] = set(setv) | {"pPrChange"}
            S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
            where = f"{part} block {bi}"
            if O is None:
                errs.append(f"{where}: no pPr in the output")
                continue
            for name, val in setv.items():
                se, oe = (S.find(W + name) if S is not None else None), O.find(W + name)
                if name == "jc":
                    expected = {**(dict(se.attrib) if se is not None else {}), W + "val": val}
                else:
                    expected = dict(se.attrib) if se is not None else {}
                    for k, v in val.items():
                        expected[W + k] = str(v)
                    if name == "ind":
                        if "firstLine" in val:
                            expected.pop(W + "hanging", None); expected.pop(W + "hangingChars", None)
                        if "hanging" in val:
                            expected.pop(W + "firstLine", None); expected.pop(W + "firstLineChars", None)
                got = dict(oe.attrib) if oe is not None else None
                if got != expected:
                    errs.append(f"{where}: {name} expected { {etree.QName(k).localname: v for k, v in expected.items()} } "
                                f"got { {etree.QName(k).localname: v for k, v in (got or {}).items()} }")
                if oe is not None and len(oe):
                    errs.append(f"{where}: {name} gained children")
            s_chg = S.find(W + "pPrChange") if S is not None else None
            o_chg = O.find(W + "pPrChange")
            if not tracked:
                if sig(s_chg) != sig(o_chg):
                    errs.append(f"{where}: w:pPrChange changed by an untracked formatting op")
                continue
            if s_chg is not None:
                errs.append(f"{where}: source already carries a w:pPrChange (tracked op must refuse)")
                continue
            if o_chg is None or list(O)[-1] is not o_chg:
                errs.append(f"{where}: tracked change missing (w:pPrChange as last child of pPr)")
                continue
            if o_chg.get(W + "id") is None or o_chg.get(W + "author") != a.get("author"):
                errs.append(f"{where}: w:pPrChange needs w:id and author {a.get('author')!r}")
            inner = o_chg.findall(W + "pPr")
            snap = [sig(c) for c in (S if S is not None else []) if _local(c) not in ("rPr", "sectPr", "pPrChange")]
            if len(inner) != 1 or len(o_chg) != 1 or [sig(c) for c in inner[0]] != snap:
                errs.append(f"{where}: w:pPrChange snapshot is not the source pPr (reject-all would not restore it)")
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


def _inserted_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    parts = ctx.scope_parts(a.get("scope", ["document"]))
    pred = (a.get("select") or {}).get("paragraphs")
    pos = a.get("position", "after")
    text = a.get("text", "")
    tracked = bool(a.get("tracked", True))
    style = a.get("style")
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
    return f"inserted_paragraphs: {n} paragraph(s) {pos} anchor ({'tracked' if tracked else 'untracked'})"


def sect_prs(root) -> List[Any]:
    body = root.find(W + "body")
    if body is None:
        return []
    out = [sp for p in body.iter(W + "p") for sp in p.findall(W + "pPr/" + W + "sectPr")]
    final = body.find(W + "sectPr")
    if final is not None:
        out.append(final)
    return out


def _section_props(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    main = ctx.src.main_part()
    s_secs, o_secs = sect_prs(ctx.src.xml(main)), sect_prs(ctx.out.xml(main))
    if len(s_secs) != len(o_secs):
        errs.append(f"section_props: {len(s_secs)}→{len(o_secs)} sections")
        return "section_props: section count changed"
    sel = a.get("sections", [])
    if len(sel) != a.get("expected_sections", -1):
        errs.append(f"section_props: {len(sel)} section(s) listed, plan predicted {a.get('expected_sections')}")
    for i in sel:
        if not 0 <= i < len(s_secs):
            errs.append(f"section_props: no section {i}")
            continue
        S, O = s_secs[i], o_secs[i]
        for name, spec in (a.get("elements") or {}).items():
            ctx.strip_sect.setdefault(i, set()).add(name)
            se, oe = S.find(W + name), O.find(W + name)
            expected = dict(se.attrib) if se is not None else {}
            for k in spec.get("remove", []):
                expected.pop(W + k, None)
            for k, v in (spec.get("set") or {}).items():
                expected[W + k] = str(v)
            where = f"section {i} {name}"
            if not expected and (spec.get("drop_if_empty") or se is None):
                if oe is not None:
                    errs.append(f"{where}: should be absent")
                continue
            if oe is None or dict(oe.attrib) != expected:
                errs.append(f"{where}: expected { {etree.QName(k).localname: v for k, v in expected.items()} } got "
                            f"{ {etree.QName(k).localname: v for k, v in dict(oe.attrib).items()} if oe is not None else None }")
            elif [sig(c) for c in (se if se is not None else [])] != [sig(c) for c in oe]:
                errs.append(f"{where}: children changed")
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


def _effective_font(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    value = a.get("value")
    slots = a.get("slots", ["ascii", "hAnsi", "cs"])
    preserve = {f.casefold() for f in a.get("preserve_fonts", [])}
    checked = preserved = bad = 0
    bad_examples: List[str] = []
    for part in ctx.scope_parts(a.get("scope", ["document"])):
        if part not in ctx.both:
            continue
        sv, ov = ctx.view(part)
        tc = ctx.text_changed.get(part, set())
        for bi, oi in ctx.maps[part].items():
            sp, op = sv.pars[bi], ov.pars[oi]
            if bi in tc:
                had = {slot: set() for slot in slots}
                for r in sv.runs[bi]:
                    if any(c.tag in (W + "t", W + "delText") for c in r):
                        es = effective_fonts(r.find(W + "rPr"), sp, ctx.src_styles, ctx.src_theme)
                        for slot in slots:
                            if es.get(slot) and es[slot].casefold() in preserve:
                                had[slot].add(es[slot])
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
                pairs = []
                if a.get("include_paragraph_marks", True):
                    pairs.append((sp.find(W + "pPr/" + W + "rPr"), op.find(W + "pPr/" + W + "rPr"), "mark"))
            elif len(sv.runs[bi]) == len(ov.runs[oi]):
                pairs = [(r.find(W + "rPr"), q.find(W + "rPr"), f"run {k}") for k, (r, q) in enumerate(zip(sv.runs[bi], ov.runs[oi]))
                         if any(c.tag in (W + "t", W + "delText") for c in r)]
            else:
                pairs = []
                errs.append(f"effective_font: {part} block {bi}: run structure changed")
            if bi not in tc and a.get("include_paragraph_marks", True):
                pairs.append((sp.find(W + "pPr/" + W + "rPr"), op.find(W + "pPr/" + W + "rPr"), "mark"))
            for s_rpr, o_rpr, label in pairs:
                es = effective_fonts(s_rpr, sp, ctx.src_styles, ctx.src_theme)
                eo = effective_fonts(o_rpr, op, ctx.out_styles, ctx.out_theme)
                checked += 1
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
    if bad:
        errs.append(f"effective_font: {bad} slot(s) do not resolve as asserted, e.g. {bad_examples}")
    return f"effective_font: {checked} run(s)/mark(s) resolve to {value!r} on {slots} ({preserved} preserved symbol slot(s))"


KINDS = {
    "run_fonts": _run_fonts,
    "style_fonts": _style_fonts,
    "doc_defaults_fonts": _doc_defaults_fonts,
    "paragraph_props": _paragraph_props,
    "section_props": _section_props,
    "section_hf_refs": _section_hf_refs,
    "effective_font": _effective_font,
    "deleted_paragraphs": _deleted_paragraphs,
    "inserted_paragraphs": _inserted_paragraphs,
}


# ---- residual ---------------------------------------------------------------------------------------

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


def _residual_text_part(ctx: Ctx, part: str, errs: List[str]) -> None:
    sv, ov = ctx.view(part)
    s_root = etree.fromstring(etree.tostring(sv.root))   # private copies: stripping must not disturb the views
    o_root = etree.fromstring(etree.tostring(ov.root))
    s, o = PartView(s_root), PartView(o_root)
    amap = ctx.maps[part]
    tc = ctx.text_changed.get(part, set())
    del_src = ctx.deleted_src_blocks.get(part, set())
    ins_out = ctx.inserted_out_blocks.get(part, set())
    # predicted text changes: pPr identical, no new run-property signature, then out of the residual
    for bi in sorted(tc):
        if bi >= len(s.pars) or bi in del_src:
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
    remove_blocks(o, {amap[b] for b in tc if b in amap} | ins_out)
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
        for st in root.findall(W + "style"):
            if st.get(W + "styleId") in ctx.strip_styles and st.find(W + "rPr") is not None:
                st.remove(st.find(W + "rPr"))
    for st in o_root.findall(W + "style"):            # new styles: checked by styles_added, not residual
        if st.get(W + "styleId") in ctx.added_styles:
            o_root.remove(st)
    d = _residual_difference(s_root, o_root)
    if d:
        errs.append(f"{ctx.styles_part}: unpredicted change {d}")


def check_properties(src: VPackage, out: VPackage, plan: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    ctx = Ctx(src, out, plan)
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
        if src.read(part) != out.read(part):
            residual_parts.append(part)
            _residual_text_part(ctx, part, errs)
    if ctx.styles_part and src.read(ctx.styles_part) != out.read(ctx.styles_part):
        residual_parts.append(ctx.styles_part)
        _residual_styles(ctx, errs)
    ok = not errs
    head = (f"{len(assertions)} assertion(s): " + "; ".join(summaries)) if assertions else "no property change predicted"
    summary = (head + f" | residual identity over {len(residual_parts)} changed part(s): "
               + ("everything not asserted is identical" if ok else f"{len(errs)} problem(s): {errs[:3]}"))
    return ok, summary, {"errors": errs[:50], "assertions": summaries, "residual_parts": residual_parts}


# header/footer templating kinds live in verify/template.py (imported last: it uses the helpers above)
from .template import TEMPLATE_KINDS  # noqa: E402

KINDS.update(TEMPLATE_KINDS)
