"""V10 assertion kinds for header/footer templating — verifier side, independent of ops/ (CLAUDE.md rule 1).

* ``hf_template_refs`` — every section's header/footer references (of the templated kinds) resolve, through the
  OUTPUT's own relationships, to exactly the predicted new parts; titlePg as predicted.
* ``template_parts`` — for each part copied from the template, re-derived here from the template file, the
  SOURCE document and the output alone:
    - the template file is the one the plan used (md5);
    - the sections referencing the part, and the template section's and target sections' text widths;
    - the slots (yellow highlight / ``{{NAME}}``, or with ``slot_markers: ["reference"]`` every bounded place the
      template's located text shows its own value of a fill rule — the rule run on the template here, or its
      ``in_template`` text) found with this module's own detection, and every fill re-extracted from the source by
      its declared rule (literal / regex / paragraph_after / first_match); no slot of any marker in a field (between
      a complex field's begin and end over the whole part, or inside fldSimple); for reference slots also: none
      overlapping, and no template value left elsewhere in the part in another form (digit/letter tokens
      concatenated);
    - every block's text = the template block's text with the slots replaced by those fills;
    - the part skeleton (everything except top-level paragraph contents, drawing ids masked) identical;
    - per top-level paragraph: the paragraph properties, paragraph-mark and run properties outside the pinnable
      set identical to the template's; the effective pinnable properties (resolved here through the OUTPUT's
      styles/theme vs the TEMPLATE's) identical, tab stops rescaled by the declared rule; the run sequence equal
      to the template's with each run a slot touches cut at the slot's edges and each slot collapsed to one fill
      run (its highlight gone for highlight slots), every run's content (text and other children, in order) as
      modelled; a slot named in ``caps_fills`` shows its fill in
      capitals by formatting — its run's effective caps on, its letters and every other property as above;
    - ``wp:docPr`` ids of the part unique in the whole output.
* ``styles_added`` — independently derive equivalent styles and the missing dependency closure; exactly those
  definitions may be added, with checked reference/identity remapping and extension markup removed.

Definitions shared with the transform side (slot detection, display text, fill rules, effective properties,
tab rescaling) are restated in CLAUDE.md and implemented here separately.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from .reader import VPackage, W, R, STYLES_REL, THEME_REL
from .textcheck import blocks
from .patterns import v_compile

_V_SOURCE_TEXT = "a source paragraph's text"     # what a fill's pattern or anchor is searched on
from .props import Styles, Theme, sig, sect_prs, first_difference
from .template_styles import style_plan, mapped_tree, private_defaults

WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
DOCPR = "{%s}docPr" % WP_NS
BRACES = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
TEXT_EL = {W + "t", W + "delText", W + "instrText", W + "delInstrText",
           "{http://schemas.openxmlformats.org/officeDocument/2006/math}t"}
PIN_PPR = {"spacing", "ind", "jc", "tabs"}
PIN_RPR_ELEMENTS = {"sz", "color", "b", "i", "caps", "smallCaps"}
PIN_RFONTS_ATTRS = {"ascii", "hAnsi", "asciiTheme", "hAnsiTheme"}
FALSY = {"0", "false", "off"}
KEEP_NS = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
           "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
           "http://www.w3.org/XML/1998/namespace"}

_TEMPLATES: Dict[str, VPackage] = {}


def _template(path: str) -> Tuple[str, VPackage]:
    """(md5, package) of the template file as it is NOW — re-read on every call (a template edited mid-batch
    must show up as an md5 mismatch, not hide behind a cached copy); parsed packages are cached by content."""
    data = Path(path).read_bytes()
    h = hashlib.md5(data).hexdigest()
    if h not in _TEMPLATES:
        _TEMPLATES[h] = VPackage(data)
    return h, _TEMPLATES[h]


def _local(el) -> str:
    return el.tag[len(W):] if isinstance(el.tag, str) and el.tag.startswith(W) else ""


def _owner_p(el):
    q = el.getparent()
    while q is not None:
        if q.tag == W + "p":
            return q
        q = q.getparent()
    return None


def _runs_of(p) -> List[Any]:
    return [r for r in p.iter(W + "r") if _owner_p(r) is p]


def _run_display(r, p) -> str:
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag in (W + "del", W + "moveFrom"):
            return ""
        q = q.getparent()
    return "".join(t.text or "" for t in r.findall(W + "t"))


def display_of(p) -> str:
    return "".join(_run_display(r, p) for r in _runs_of(p))


def _v_norm_ws(s: str) -> str:
    return re.sub(r"[\s\u00a0]+", " ", s or "")


# ---- sections, widths, references (output/template, read here) -----------------------------------

def _width(sp) -> Optional[int]:
    sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
    if sz is None or mar is None or sz.get(W + "w") is None:
        return None
    return int(sz.get(W + "w")) - sum(int(mar.get(W + k) or 0) for k in ("left", "right", "gutter"))


def _refs_shown(pkg: VPackage) -> List[Dict[Tuple[str, str], Optional[str]]]:
    main = pkg.main_part()
    rels = pkg.rels_targets(main)
    shown, prev = [], {}
    for sp in sect_prs(pkg.xml(main)):
        cur = dict(prev)
        for el in sp:
            if el.tag in (W + "headerReference", W + "footerReference"):
                kind = "header" if el.tag == W + "headerReference" else "footer"
                rid = el.get(R + "id")
                cur[(kind, el.get(W + "type", "default"))] = rels[rid]["target"] if rid in rels else None
        shown.append(cur)
        prev = cur
    return shown


def _own_refs(pkg: VPackage, sp, kinds) -> Dict[str, Dict[str, Optional[str]]]:
    rels = pkg.rels_targets(pkg.main_part())
    out: Dict[str, Dict[str, Optional[str]]] = {}
    for el in sp:
        if el.tag in (W + "headerReference", W + "footerReference"):
            kind = "header" if el.tag == W + "headerReference" else "footer"
            if kind in kinds:
                rid = el.get(R + "id")
                out.setdefault(kind, {})[el.get(W + "type", "default")] = rels[rid]["target"] if rid in rels else None
    return out


def _title_pg(sp) -> bool:
    t = sp.find(W + "titlePg")
    return t is not None and t.get(W + "val") not in FALSY


# ---- slots ---------------------------------------------------------------------------------------

REF_MIN_ALNUM = 3
V_SEP = "\x00"      # located text: display-run content other than w:t


def _v_located(p) -> Tuple[str, List[int]]:
    """Display text with V_SEP for every non-text child of a display run; display offset per position (+ end)."""
    chars: List[str] = []
    offs: List[int] = []
    pos = 0
    for r in _runs_of(p):
        q, gone = r.getparent(), False
        while q is not None and q is not p:
            if q.tag in (W + "del", W + "moveFrom"):
                gone = True
                break
            q = q.getparent()
        if gone:
            continue
        for c in r:
            if c.tag == W + "rPr":
                continue
            if c.tag == W + "t":
                txt = c.text or ""
                chars.append(txt)
                offs += list(range(pos, pos + len(txt)))
                pos += len(txt)
            else:
                chars.append(V_SEP)
                offs.append(pos)
    offs.append(pos)
    return "".join(chars), offs


def _v_value_rx(value: str):
    parts = [re.escape(w) for w in re.split(r"\s+", (value or "").strip()) if w]
    return re.compile(r"\s+".join(parts), re.IGNORECASE)


def _v_bounded(text: str, a: int, b: int) -> bool:
    word = lambda ch: ch.isalnum() or ch == "_"
    for i, step in ((a - 1, -1), (b, 1)):
        if i < 0 or i >= len(text):
            continue
        if word(text[i]):
            return False
        if text[i].isspace() or text[i] == V_SEP:
            continue
        if 0 <= i + step < len(text) and word(text[i + step]):
            return False
    return True


def _v_tokens(s: str) -> List[str]:
    return [t.casefold() for t in re.findall(r"\d+|[^\W\d_]+", s or "")]


def _v_left_in(piece: str, value: str) -> bool:
    """The value's tokens spelled by consecutive tokens of ``piece``; the first and last may be cut inside a letter
    token, never inside a digit token (walked token by token here)."""
    key, toks = "".join(_v_tokens(value)), _v_tokens(piece)
    if not key:
        return False
    for i, first in enumerate(toks):
        for a in ([0] if first[:1].isdecimal() else range(len(first))):
            need, k, chunk = key, i, first[a:]
            while True:
                if len(chunk) >= len(need):
                    if chunk.startswith(need) and (len(chunk) == len(need) or not chunk[:1].isdecimal()):
                        return True
                    break
                if not need.startswith(chunk):
                    break
                need, k = need[len(chunk):], k + 1
                if k == len(toks):
                    break
                chunk = toks[k]
    return False


def _v_field_runs(root) -> Set[Any]:
    """Runs between a complex field's begin and end (over the whole part, document order) or inside fldSimple."""
    out: Set[Any] = set()
    depth = 0
    for r in root.iter(W + "r"):
        if depth > 0 or any(a.tag == W + "fldSimple" for a in r.iterancestors()):
            out.add(r)
        for fc in r.findall(W + "fldChar"):
            if fc.get(W + "fldCharType") == "begin":
                depth += 1
                out.add(r)
            elif fc.get(W + "fldCharType") == "end":
                depth = max(0, depth - 1)
    return out


def _v_has_literal(rule) -> bool:
    if not isinstance(rule, dict):
        return False
    subs = rule["rules"] if isinstance(rule.get("rules"), list) else []
    return rule.get("rule") in ("literal", "typed") or any(_v_has_literal(r) for r in subs)


def reference_values_of(tpl: VPackage, fills: Dict[str, Any]) -> Dict[str, str]:
    """Each rule's template value, derived here: ``in_template`` text, else the rule run on the template."""
    out: Dict[str, str] = {}
    for name, rule in fills.items():
        if isinstance(rule, dict) and "in_template" in rule:
            v = rule["in_template"].strip() if isinstance(rule["in_template"], str) else ""
            if not v:
                raise ValueError(f"reference slot {name!r}: empty in_template")
        elif _v_has_literal(rule):
            raise ValueError(f"reference slot {name!r}: a literal fill without in_template")
        else:
            v = fill_value(tpl, name, rule)
        if V_SEP in v:
            raise ValueError(f"reference slot {name!r}: NUL in the template value")
        if len("".join(_v_tokens(v))) < REF_MIN_ALNUM:
            raise ValueError(f"reference slot {name!r}: template value {v!r} too short")
        out[name] = v
    return out


def slots_of(p, markers, colors, ref_values: Optional[Dict[str, str]] = None,
             in_field: Optional[Set[Any]] = None) -> List[Dict[str, Any]]:
    """[{name, start, end, highlight}] in display offsets — this module's own reading of the convention."""
    runs = _runs_of(p)
    spans: List[Dict[str, Any]] = []
    pos, offs = 0, []
    for r in runs:
        t = _run_display(r, p)
        offs.append((pos, pos + len(t)))
        pos += len(t)
    text = display_of(p)
    if "reference" in markers:
        loc, loffs = _v_located(p)
        hits = []
        for name, value in (ref_values or {}).items():
            for m in _v_value_rx(value).finditer(loc):
                if _v_bounded(loc, m.start(), m.end()):
                    hits.append((m.start(), m.end(), name))
        hits.sort()
        for (a1, b1, n1), (a2, b2, n2) in zip(hits, hits[1:]):
            if a2 < b1:
                raise ValueError(f"reference slots {n1!r} and {n2!r} overlap")
        for a1, b1, n1 in hits:
            spans.append({"name": n1, "start": loffs[a1], "end": loffs[b1], "highlight": False})
        for sl in spans:
            for r, (a, b) in zip(runs, offs):
                if b > a and a < sl["end"] and sl["start"] < b and in_field is not None and r in in_field:
                    raise ValueError(f"reference slot {sl['name']!r} is inside a field")
        rest, cur = [], 0
        for a1, b1, _ in hits:
            rest.append(loc[cur:a1])
            cur = b1
        rest.append(loc[cur:])
        left = [(n, " ".join(x.replace(V_SEP, " ").split())) for n, v in (ref_values or {}).items() for x in rest
                if _v_left_in(x, v)]
        if left:
            raise ValueError(f"the template's own value(s) left in another form: {left}")
        return spans
    if "highlight" in markers:
        group: List[int] = []
        for k in range(len(runs) + 1):
            hl = None
            if k < len(runs):
                e = runs[k].find(f"{W}rPr/{W}highlight")
                hl = e is not None and e.get(W + "val") in colors and runs[k].find(W + "t") is not None
            if hl:
                group.append(k)
                continue
            if group:
                a, b = offs[group[0]][0], offs[group[-1]][1]
                raw = text[a:b]
                m = BRACES.fullmatch(raw.strip())
                spans.append({"name": m.group(1) if m else raw.strip(), "start": a, "end": b, "highlight": True})
            group = []
    if "braces" in markers:
        for m in BRACES.finditer(text):
            inside = [s for s in spans if s["highlight"] and s["start"] <= m.start() and m.end() <= s["end"]]
            if inside and inside[0]["name"] == m.group(1) and text[inside[0]["start"]:inside[0]["end"]].strip() == m.group(0):
                continue
            if any(s["start"] < m.end() and m.start() < s["end"] for s in spans):
                raise ValueError(f"placeholder {m.group(0)} overlaps a highlighted slot")
            spans.append({"name": m.group(1), "start": m.start(), "end": m.end(), "highlight": False})
    if in_field is not None:                    # every marker: a slot a field shows is not a slot the op may fill
        for sl in spans:
            for r, (a, b) in zip(runs, offs):
                if b > a and a < sl["end"] and sl["start"] < b and r in in_field:
                    raise ValueError(f"slot {sl['name']!r} is inside a field")
    return sorted(spans, key=lambda s: s["start"])


# ---- fills, re-extracted from the SOURCE ----------------------------------------------------------

def _source_pars(src: VPackage, source: str, section: Optional[int]) -> List[Any]:
    if source == "document":
        return list(src.xml(src.main_part()).iter(W + "p"))
    kind, _, typ = source.partition(".")
    shown = _refs_shown(src)
    idx = range(len(shown)) if section is None else [section]
    parts: List[str] = []
    for i in idx:
        part = shown[i].get((kind, typ)) if 0 <= i < len(shown) else None
        if part and part not in parts:
            parts.append(part)
    if not parts:
        raise ValueError(f"no {typ} {kind} in the source")
    return [p for part in parts for p in src.xml(part).iter(W + "p")]


def _source_match(rx, original, flexible):
    hit = rx.search(original)
    if hit is not None:
        return hit.group(1 if rx.groups else 0)
    if not flexible:
        return None
    units = [(" " if m.group().isspace() else m.group(), m.start(), m.end())
             for m in re.finditer(r"\s+|\S", original)]
    while units and units[0][0] == " ":
        units = units[1:]
    while units and units[-1][0] == " ":
        units = units[:-1]
    hit = rx.search("".join(unit[0] for unit in units))
    if hit is None:
        return None
    left, right = hit.span(1 if rx.groups else 0)
    return original[units[left][1]:units[right - 1][2]] if left >= 0 and right > left else ""


def fill_value(src: VPackage, name: str, rule: Dict[str, Any]) -> str:
    if rule.get("rule") == "first_match" or ("rules" in rule and rule.get("rule") in (None, "first_match")):
        sub = rule.get("rules") or []
        if not isinstance(sub, list) or not sub:
            raise ValueError(f"fill {name!r}: empty first_match rules")
        last_err: Optional[Exception] = None
        for r in sub:
            try:
                return fill_value(src, name, r)
            except (ValueError, KeyError) as e:
                last_err = e
        raise ValueError(f"fill {name!r}: no rule in first_match succeeded ({last_err})")

    how = rule.get("rule")
    for option in ("case_insensitive", "flexible_whitespace", "strip"):
        if option in rule and type(rule[option]) is not bool:
            raise ValueError(f"fill {name!r}: {option} must be a boolean")
    flex_ws = rule.get("flexible_whitespace", True)
    occ = rule.get("occurrence", "all")
    if how == "binding":
        if hashlib.md5(src.data).hexdigest() != rule.get("source_md5"):
            raise ValueError(f"fill {name!r}: bound source identity changed")
        part, ordinal, literal = rule.get("part"), rule.get("paragraph"), rule.get("value")
        if part not in src.text_parts() or type(ordinal) is not int or ordinal < 0:
            raise ValueError(f"fill {name!r}: invalid binding location")
        paragraphs = list(src.xml(part).iter(W + "p"))
        if not isinstance(literal, str) or not literal or ordinal >= len(paragraphs) or literal not in display_of(paragraphs[ordinal]):
            raise ValueError(f"fill {name!r}: literal does not occur verbatim at bound source location")
        return literal
    if how == "typed":
        # the user's own wording for this one file (a spec binding's ``typed``): the value as approved, bound to this
        # source's exact bytes, checked here against the bytes this module read — never another file's wording
        if hashlib.md5(src.data).hexdigest() != rule.get("source_md5"):
            raise ValueError(f"fill {name!r}: typed wording bound to another source")
        typed = rule.get("value")
        if not isinstance(typed, str) or not typed.strip():
            raise ValueError(f"fill {name!r}: typed wording is empty")
        return typed
    if how == "literal":
        v = rule["value"]
    elif how == "regex":
        rx = v_compile(rule["pattern"], re.I if rule.get("case_insensitive", False) else 0, where=_V_SOURCE_TEXT)
        pars = _source_pars(src, rule.get("source", "document"), rule.get("section"))
        found = []
        for p in pars:
            value = _source_match(rx, display_of(p), flex_ws)
            if value is not None:
                found.append(value)
        if not found:
            raise ValueError(f"fill {name!r}: regex yields nothing")
        if occ == "first":
            v = found[0]
        else:
            normed = [_v_norm_ws(x).strip() if flex_ws else x for x in found]
            if len(set(normed)) != 1:
                raise ValueError(f"fill {name!r}: regex yields {sorted(set(found))}")
            v = found[0]
    elif how == "paragraph_after":
        rx = v_compile(rule["anchor_regex"], re.I if rule.get("case_insensitive", False) else 0, where=_V_SOURCE_TEXT)
        texts = [display_of(p) for p in _source_pars(src, rule.get("source", "document"), rule.get("section"))]
        hits = [k for k, t in enumerate(texts) if _source_match(rx, t, flex_ws) is not None]
        if not hits:
            raise ValueError(f"fill {name!r}: anchor matches 0 paragraph(s)")
        cands = []
        for h in hits:
            rest = [t for t in texts[h + 1:] if t.strip()]
            if rest:
                cands.append(rest[0])
        if not cands:
            raise ValueError(f"fill {name!r}: nothing follows anchor")
        keys = [_v_norm_ws(c).strip() if flex_ws else c for c in cands]
        if len(hits) > 1 and occ != "first" and len(set(keys)) > 1:
            raise ValueError(f"fill {name!r}: anchor matches {len(hits)} paragraph(s) with different values")
        v = cands[0]
    else:
        raise ValueError(f"fill {name!r}: unknown rule {how!r}")
    # the fill is the source's own characters; normalization serves matching and consensus only
    if rule.get("strip", True):
        v = v.strip()
    if not v:
        raise ValueError(f"fill {name!r}: empty fill")
    return v


# ---- effective properties (this module's own resolver) --------------------------------------------

class Eff:
    """Effective pinnable properties of a paragraph / run in one package (definition: CLAUDE.md)."""

    def __init__(self, pkg: VPackage):
        sp = pkg.related(STYLES_REL)
        self.styles = Styles(pkg.xml(sp) if sp else None)
        tp = pkg.related(THEME_REL)
        self.theme = Theme(pkg.xml(tp) if tp else None)
        root = self.styles.root
        self.dd_p = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr") if root is not None else None
        self.dd_r = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr") if root is not None else None

    def _levels(self, p, rpr=None, run=False) -> List[Any]:
        """Style elements, lowest priority first: table chain, paragraph chain, [character chain]."""
        def chain(sid):
            active = []
            for style in self.styles.chain(sid):
                # Word discards these definitions' children, including basedOn.
                # MS-OI29500 §2.1.242(a); do not turn them into formatting pins.
                if style.get(W + "styleId") in ("NoList", "TableNormal", "DefaultParagraphFont"):
                    break
                active.insert(0, style)
            return active

        out = []
        q = p.getparent()
        while q is not None and q.tag != W + "tbl":
            q = q.getparent()
        if q is not None:
            ts = q.find(f"{W}tblPr/{W}tblStyle")
            out += chain(ts.get(W + "val") if ts is not None else self.styles.default.get("table"))
        ps = p.find(f"{W}pPr/{W}pStyle")
        out += chain(ps.get(W + "val") if ps is not None else self.styles.default.get("paragraph"))
        if run:
            rs = rpr.find(W + "rStyle") if rpr is not None else None
            out += chain(rs.get(W + "val") if rs is not None else self.styles.default.get("character"))
        return out

    def para(self, p, with_direct=True) -> Dict[str, Any]:
        layers = [self.dd_p] + [st.find(W + "pPr") for st in self._levels(p)]
        if with_direct:
            layers.append(p.find(W + "pPr"))
        v = {"before": "0", "after": "0", "line": "240", "lineRule": "auto", "left": "0", "right": "0", "first": "0",
             "jc": "left"}
        stops: Dict[int, Tuple[Any, Any]] = {}
        for L in layers:
            if L is None:
                continue
            e = L.find(W + "spacing")
            if e is not None:
                for k in ("before", "after", "line", "lineRule"):
                    if e.get(W + k) is not None:
                        v[k] = e.get(W + k)
            e = L.find(W + "ind")
            if e is not None:
                for k, alt in (("left", "start"), ("right", "end")):
                    x = e.get(W + k, e.get(W + alt))
                    if x is not None:
                        v[k] = x
                if e.get(W + "firstLine") is not None:
                    v["first"] = str(int(e.get(W + "firstLine")))
                if e.get(W + "hanging") is not None:
                    v["first"] = str(-int(e.get(W + "hanging")))
            e = L.find(W + "jc")
            if e is not None and e.get(W + "val"):
                j = e.get(W + "val")
                v["jc"] = "left" if j == "start" else ("right" if j == "end" else j)
            e = L.find(W + "tabs")
            if e is not None:
                for t in e.findall(W + "tab"):
                    pos = int(t.get(W + "pos"))
                    if t.get(W + "val") == "clear":
                        stops.pop(pos, None)
                    else:
                        stops[pos] = (t.get(W + "val"), t.get(W + "leader"))
        v["tabs"] = tuple(sorted((pos, a, b) for pos, (a, b) in stops.items()))
        return v

    def run(self, rpr, p) -> Dict[str, Any]:
        styled = [self.dd_r] + [st.find(W + "rPr") for st in self._levels(p, rpr, run=True)]
        v: Dict[str, Any] = {"ascii": "Times New Roman", "hAnsi": "Times New Roman", "sz": "20",
                             "color": ("auto", None, None, None)}
        for L in styled + [rpr]:
            if L is None:
                continue
            f = L.find(W + "rFonts")
            if f is not None:
                for slot, th in (("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme")):
                    if f.get(W + th):
                        v[slot] = self.theme.resolve(f.get(W + th))
                    elif f.get(W + slot):
                        v[slot] = f.get(W + slot)
            e = L.find(W + "sz")
            if e is not None and e.get(W + "val"):
                v["sz"] = e.get(W + "val")
            e = L.find(W + "color")
            if e is not None:
                v["color"] = tuple(e.get(W + k) for k in ("val", "themeColor", "themeShade", "themeTint"))
        for t in ("b", "i", "caps", "smallCaps"):
            seen = [L.find(W + t) for L in styled if L is not None and L.find(W + t) is not None]
            if len(seen) > 1:
                raise ValueError(f"toggle {t} set at {len(seen)} style levels (XOR not modelled)")
            val = bool(seen) and seen[0].get(W + "val") not in FALSY
            d = rpr.find(W + t) if rpr is not None else None
            v[t] = (d.get(W + "val") not in FALSY) if d is not None else val
        return v


def rescaled(tabs, w_from: Optional[int], w_to: Optional[int]):
    if not w_from or not w_to or w_from == w_to:
        return tuple(tabs)
    out = []
    for pos, kind, leader in tabs:
        if kind in ("right", "end"):
            pos += w_to - w_from
        elif kind == "center":
            pos = int(pos * w_to / w_from + 0.5)
        out.append((pos, kind, leader))
    return tuple(sorted(out))


# ---- residual helpers -----------------------------------------------------------------------------

def _mask(el):
    c = etree.fromstring(etree.tostring(el))
    for d in c.iter(DOCPR):
        d.set("id", "#")
    return c


def _ppr_rest(p):
    ppr = p.find(W + "pPr")
    if ppr is None:
        return ()
    return tuple(sig(c) for c in ppr if _local(c) not in PIN_PPR | {"rPr"})


def _rpr_rest(rpr, drop_highlight=False):
    if rpr is None:
        return ()
    out = []
    for c in rpr:
        name = _local(c)
        if name in PIN_RPR_ELEMENTS or (drop_highlight and name == "highlight"):
            continue
        if name == "rFonts":
            attrs = tuple(sorted((k, v) for k, v in c.attrib.items() if etree.QName(k).localname not in PIN_RFONTS_ATTRS))
            if attrs:
                out.append(("rFonts", attrs))
            continue
        out.append(sig(c))
    return tuple(out)


def _items(r) -> Tuple[Tuple[str, Any], ...]:
    """A run's content in order: consecutive w:t merged into ("t", text), any other child (rPr aside) by signature."""
    out: List[Tuple[str, Any]] = []
    for c in r:
        if c.tag == W + "rPr":
            continue
        if c.tag == W + "t":
            if out and out[-1][0] == "t":
                out[-1] = ("t", out[-1][1] + (c.text or ""))
            else:
                out.append(("t", c.text or ""))
        else:
            out.append(("x", sig(_mask(c))))
    return tuple(out)


def _skeleton(root):
    c = etree.fromstring(etree.tostring(root))
    for p in [p for p in c.iter(W + "p") if _owner_p(p) is None]:
        for ch in list(p):
            p.remove(ch)
        for a in list(p.attrib):
            del p.attrib[a]
    for d in c.iter(DOCPR):
        d.set("id", "#")
    return c


def _expected_block(p, slots, fills) -> str:
    out, dpos = [], 0
    for el in p.iter(*TEXT_EL):
        if _owner_p(el) is not p:
            continue
        s = el.text or ""
        if el.tag != W + "t":
            out.append(s)
            continue
        a, b = dpos, dpos + len(s)
        dpos = b
        cur = a
        for sl in slots:
            if sl["end"] <= a or sl["start"] >= b:
                continue
            if sl["start"] > cur:
                out.append(s[cur - a:sl["start"] - a])
            if sl["start"] >= a:
                out.append(fills[sl["name"]])
            cur = max(cur, min(sl["end"], b))
        out.append(s[cur - a:])
    return "".join(out)


def _run_model(p, slots, fills) -> List[Tuple[Any, Tuple[Tuple[str, Any], ...], Optional[Dict[str, Any]]]]:
    """Expected output runs: (template run, content items, slot or None). A run no slot touches is unchanged. A run
    a slot touches (its display text overlaps the slot) is cut at the slots' edges: each stretch of its other
    content — text outside the slots and its other children, in order, empty w:t dropped — becomes one run, and each
    slot becomes one run holding only the fill, in the run where the slot starts (its pieces in later runs go)."""
    model, pos = [], 0
    for r in _runs_of(p):
        t = _run_display(r, p)
        a, b = pos, pos + len(t)
        pos = b
        hit = [s for s in slots if s["start"] < b and a < s["end"]] if t else []
        if not hit:
            model.append((r, _items(r), None))
            continue
        groups: List[List[Any]] = []                 # [slot or None, [items]]
        off = a
        for c in r:
            if c.tag == W + "rPr":
                continue
            if c.tag == W + "t":
                txt = c.text or ""
                ca, cb = off, off + len(txt)
                off = cb
                cuts = sorted({ca, cb} | {x for s in hit for x in (s["start"], s["end"]) if ca < x < cb})
                pieces = [(next((s for s in hit if s["start"] <= x and y <= s["end"]), None), ("t", txt[x - ca:y - ca]))
                          for x, y in zip(cuts, cuts[1:])]
            else:
                pieces = [(None, ("x", sig(_mask(c))))]
            for lab, item in pieces:
                if groups and groups[-1][0] is lab:
                    last = groups[-1][1]
                    if item[0] == "t" and last[-1][0] == "t":
                        last[-1] = ("t", last[-1][1] + item[1])
                    else:
                        last.append(item)
                else:
                    groups.append([lab, [item]])
        for lab, items in groups:
            if lab is None:
                model.append((r, tuple(items), None))
            elif lab["start"] >= a:
                model.append((r, (("t", fills[lab["name"]]),), lab))
    return model


def _filled_run_properties(p, slot, slots, resolver):
    """Derive clear-mode colour and decoration from original template runs, independently of planned pins."""
    runs = _runs_of(p)
    positions, cursor = [], 0
    for run in runs:
        text = _run_display(run, p)
        positions.append((cursor, cursor + len(text), text))
        cursor += len(text)
    index = next(i for i, (left, right, _) in enumerate(positions) if left <= slot["start"] < right)
    choices = []
    for i, (left, right, text) in enumerate(positions):
        ordinary = "".join(text[pos - left] for pos in range(left, right)
                           if all(not (item["start"] <= pos < item["end"]) for item in slots))
        if ordinary.strip():
            choices.append(i)
    donor = runs[min(choices, key=lambda i: (abs(i - index), i))] if choices else None
    original = runs[index].find(W + "rPr")
    clean = etree.fromstring(etree.tostring(original)) if original is not None else etree.Element(W + "rPr")
    for kind in ("color", "highlight", "shd"):
        for node in clean.findall(W + kind):
            clean.remove(node)
        replacement = donor.find(f"{W}rPr/{W}{kind}") if donor is not None else None
        if replacement is not None:
            clean.append(etree.fromstring(etree.tostring(replacement)))
    effective = resolver.run(clean, p)
    if donor is not None:
        effective["color"] = resolver.run(donor.find(W + "rPr"), p)["color"]
    return clean, effective


# ---- the kinds ------------------------------------------------------------------------------------

def check_hf_template_refs(ctx, a: Dict[str, Any], errs: List[str]) -> str:
    main = ctx.src.main_part()
    s_secs, o_secs = sect_prs(ctx.src.xml(main)), sect_prs(ctx.out.xml(main))
    if len(s_secs) != len(o_secs):
        errs.append(f"hf_template_refs: {len(s_secs)}→{len(o_secs)} sections")
        return "hf_template_refs: section count changed"
    kinds = a.get("kinds", [])
    for i, sp in enumerate(o_secs):
        want = {k: v for k, v in (a.get("sections", {}).get(str(i)) or {}).items() if v}
        got = _own_refs(ctx.out, sp, kinds)
        if got != want:
            errs.append(f"hf_template_refs: section {i} references {got}, predicted {want}")
        tp = (a.get("title_pg") or {}).get(str(i))
        if tp is not None and _title_pg(sp) != tp:
            errs.append(f"hf_template_refs: section {i} titlePg is {_title_pg(sp)}, predicted {tp}")
        names = {"titlePg"} | {k + "Reference" for k in kinds}
        ctx.strip_sect.setdefault(i, set()).update(names)
    return f"hf_template_refs: {len(o_secs)} section(s) reference the predicted template copies"


def _style_parts(ctx, a):
    # In V10, tie the dependency roots to the parts actually being checked,
    # rather than accepting arbitrary extra roots from a generated plan.
    # The final goal checker supplies roots derived directly from its graph.
    if hasattr(ctx, "plan"):
        parts = sorted({entry["template_part"] for entry in ctx.plan.get("property_assertions", [])
                        if entry.get("kind") == "template_parts" and entry.get("template") == a["template"]
                        and entry.get("template_md5") == a.get("template_md5")})
        if "style_parts" in a and sorted(set(a["style_parts"])) != parts:
            raise ValueError("style dependency roots differ from the copied template parts")
        return parts
    return a.get("style_parts", [a["template_part"]] if "template_part" in a else [])


def check_template_part(ctx, a: Dict[str, Any], errs: List[str]) -> str:
    part = a["part"]
    tag = f"template_parts {part}"
    md5, tpl = _template(a["template"])
    if md5 != a.get("template_md5"):
        errs.append(f"{tag}: template file changed since planning (md5 {md5} ≠ {a.get('template_md5')})")
        return tag
    if part in ctx.src.names or part not in ctx.out.names or part not in ctx.out_tp:
        errs.append(f"{tag}: not a new text part of the output")
        return tag
    # widths and referencing sections, from the output and the template themselves
    o_secs = sect_prs(ctx.out.xml(ctx.out.main_part()))
    users = [i for i, sp in enumerate(o_secs)
             if any(part in by_type.values() for by_type in _own_refs(ctx.out, sp, ["header", "footer"]).values())]
    if users != sorted(a.get("target_sections", [])):
        errs.append(f"{tag}: referenced by sections {users}, predicted {sorted(a.get('target_sections', []))}")
    widths = {_width(o_secs[i]) for i in users}
    if len(widths) > 1:
        errs.append(f"{tag}: shared by sections of different text widths {sorted(widths)}")
    w_to = widths.pop() if widths else None
    t_secs = sect_prs(tpl.xml(tpl.main_part()))
    w_from = _width(t_secs[a["template_section"]])
    if not a.get("rescale_tabs", True):
        w_to = w_from
    # slots and fills
    troot, oroot = tpl.xml(a["template_part"]), ctx.out.xml(part)
    fills: Dict[str, str] = {}
    for name, rule in (a.get("fills") or {}).items():
        try:
            fills[name] = fill_value(ctx.src, name, rule)
        except (ValueError, KeyError) as e:
            errs.append(f"{tag}: {e}")
            return tag
    if fills != a.get("expected_fills"):
        errs.append(f"{tag}: fills re-extracted here {fills} differ from the plan's {a.get('expected_fills')}")
    caps_fills = set(a.get("caps_fills") or [])          # C4: these slots' filled runs show in capitals (w:caps)
    if caps_fills - set(fills):
        errs.append(f"{tag}: caps_fills {sorted(caps_fills - set(fills))} name no fill")
    t_pars, o_pars = list(troot.iter(W + "p")), list(oroot.iter(W + "p"))
    slots: Dict[int, List[Dict[str, Any]]] = {}
    markers = a.get("slot_markers", ["highlight", "braces"])
    ref_values, in_field = None, _v_field_runs(troot)
    try:
        if "reference" in markers:
            if markers != ["reference"]:
                raise ValueError("'reference' slot markers must be used alone")
            ref_values = reference_values_of(tpl, a.get("template_fill_rules", a.get("fills")) or {})
            if ref_values != a.get("template_values"):
                errs.append(f"{tag}: template values derived here {ref_values} differ from the plan's "
                            f"{a.get('template_values')}")
        for k, p in enumerate(t_pars):
            try:
                sl = slots_of(p, markers, a.get("highlight_colors", ["yellow"]), ref_values, in_field)
            except ValueError as e:
                raise ValueError(f"block {k}: {e}")
            if sl:
                if _owner_p(p) is not None:
                    errs.append(f"{tag}: slot inside a textbox paragraph")
                missing = [s["name"] for s in sl if s["name"] not in fills]
                if missing:
                    errs.append(f"{tag}: slot(s) {missing} have no fill")
                    return tag
                slots[k] = sl
    except ValueError as e:
        errs.append(f"{tag}: {e}")
        return tag
    # text
    want_text = [(_expected_block(p, slots[k], fills) if k in slots else blocks_text(p)) for k, p in enumerate(t_pars)]
    got_text = [b.text for b in blocks(oroot)]
    if want_text != got_text:
        bad = [k for k, (x, y) in enumerate(zip(want_text, got_text)) if x != y][:3]
        errs.append(f"{tag}: block text differs from template+fills at {bad or 'count'}: "
                    + "; ".join(f"{want_text[k]!r}≠{got_text[k]!r}" for k in bad))
    # Derive style identities from the two inputs, never from the claimed mapping.
    try:
        mapping, imported = style_plan(ctx.src, tpl, _style_parts(ctx, a),
                                a.get("import_missing_styles", True), strip_extensions)
    except ValueError as exc:
        errs.append(f"{tag}: {exc}")
        return tag
    if "style_map" in a and a["style_map"] != mapping:
        errs.append(f"{tag}: claimed style mapping differs from the input-derived mapping")
    mapped = mapped_tree(troot, mapping, private_defaults(tpl, mapping, imported))
    m_pars = list(mapped.iter(W + "p"))
    # skeleton
    d = first_difference(_skeleton(mapped), _skeleton(oroot))
    if d:
        errs.append(f"{tag}: structure differs from the template: {d}")
        return tag
    # per paragraph
    teff, oeff = Eff(tpl), Eff(ctx.out)
    in_table = lambda p: any(q.tag == W + "tbl" for q in p.iterancestors())
    try:
        for k, (tp_, op_) in enumerate(zip(t_pars, o_pars)):
            if _owner_p(tp_) is not None:
                continue                       # textbox content: covered by the run content comparison
            where = f"{tag} block {k}"
            mp_ = m_pars[k]
            if _ppr_rest(mp_) != _ppr_rest(op_):
                errs.append(f"{where}: paragraph properties outside the pinnable set changed")
            T, O = teff.para(tp_), oeff.para(op_)
            if a.get("rescale_tabs", True) and not in_table(tp_):
                T = dict(T, tabs=rescaled(T["tabs"], w_from, w_to))
            if T != O:
                errs.append(f"{where}: effective paragraph properties {({k2: O[k2] for k2 in O if O[k2] != T[k2]})} "
                            f"≠ template's {({k2: T[k2] for k2 in T if O[k2] != T[k2]})}")
            tm, om = tp_.find(f"{W}pPr/{W}rPr"), op_.find(f"{W}pPr/{W}rPr")
            if _rpr_rest(mp_.find(f"{W}pPr/{W}rPr")) != _rpr_rest(om):
                errs.append(f"{where}: paragraph-mark properties outside the pinnable set changed")
            if teff.run(tm, tp_) != oeff.run(om, op_):
                errs.append(f"{where}: effective paragraph-mark properties differ from the template's")
            model = _run_model(mp_, slots.get(k, []), fills)
            original_model = _run_model(tp_, slots.get(k, []), fills)
            oruns = _runs_of(op_)
            if len(model) != len(oruns):
                errs.append(f"{where}: {len(oruns)} run(s), expected {len(model)} (template runs, slots collapsed)")
                continue
            for n, ((tr, items, slot), orr) in enumerate(zip(model, oruns)):
                rw = f"{where} run {n}"
                text = "".join(v for kind, v in items if kind == "t")
                if _run_display(orr, op_) != text:
                    errs.append(f"{rw}: text {_run_display(orr, op_)!r}, expected {text!r}")
                trpr, orpr = tr.find(W + "rPr"), orr.find(W + "rPr")
                source_rpr = original_model[n][0].find(W + "rPr")
                expected_rpr, expected_effective = trpr, teff.run(source_rpr, tp_)
                if slot and a.get("slot_formatting", "clear") == "clear":
                    expected_rpr, expected_effective = _filled_run_properties(tp_, slot, slots.get(k, []), teff)
                    # Preserve reconciled style identity in the non-pinnable comparison.
                    expected_rpr = etree.fromstring(etree.tostring(expected_rpr))
                    mapped_style = trpr.find(W + "rStyle") if trpr is not None else None
                    old_style = expected_rpr.find(W + "rStyle")
                    if old_style is not None:
                        expected_rpr.remove(old_style)
                    if mapped_style is not None:
                        expected_rpr.append(etree.fromstring(etree.tostring(mapped_style)))
                if slot and slot["name"] in caps_fills:
                    # capitals by formatting: the filled run's caps is on, every other property as the template's
                    expected_effective = dict(expected_effective, caps=True)
                if _rpr_rest(expected_rpr) != _rpr_rest(orpr):
                    errs.append(f"{rw}: run properties outside the pinnable set differ from the template's"
                                + (" (slot highlight must go)" if slot and slot["highlight"] else ""))
                if _items(orr) != items:
                    errs.append(f"{rw}: run content (text, tabs, fields, drawings… in order) differs from the "
                                f"template's")
                if expected_effective != oeff.run(orpr, op_):
                    errs.append(f"{rw}: effective run properties {oeff.run(orpr, op_)} ≠ template's {expected_effective}")
    except ValueError as e:
        errs.append(f"{tag}: {e}")
    # drawing ids unique in the whole output
    ids: Dict[str, int] = {}
    for p_ in ctx.out_tp:
        for d_ in ctx.out.xml(p_).iter(DOCPR):
            ids[d_.get("id")] = ids.get(d_.get("id"), 0) + 1
    clash = sorted({d_.get("id") for d_ in oroot.iter(DOCPR) if ids.get(d_.get("id"), 0) > 1})
    if clash:
        errs.append(f"{tag}: wp:docPr id(s) {clash} are not unique in the output (Word repair trigger)")
    n_slots = sum(len(v) for v in slots.values())
    return (f"template_parts {part} ← {a['template_part']}: {len(t_pars)} block(s), {n_slots} slot(s) filled "
            f"{sorted({s['name'] for v in slots.values() for s in v})}, tabs {w_from}→{w_to}")


def blocks_text(p) -> str:
    return "".join(el.text or "" for el in p.iter(*TEXT_EL) if _owner_p(el) is p)


def strip_extensions(el):
    c = etree.fromstring(etree.tostring(el))
    for e in c.iter():
        if not isinstance(e.tag, str):
            continue
        for k in list(e.attrib):
            if k.startswith("{") and k[1:].split("}")[0] not in KEEP_NS:
                del e.attrib[k]
    for e in [e for e in c.iter() if isinstance(e.tag, str) and e.tag.startswith("{") and not e.tag.startswith(W)]:
        if e.getparent() is not None:
            e.getparent().remove(e)
    return c


def check_styles_added(ctx, a: Dict[str, Any], errs: List[str]) -> str:
    if ctx.styles_part is None:
        errs.append("styles_added: no common styles part")
        return "styles_added"
    md5, tpl = _template(a["template"])
    if md5 != a.get("template_md5"):
        errs.append(f"styles_added: template file changed since planning (md5 {md5} ≠ {a.get('template_md5')})")
        return "styles_added"
    try:
        mapping, expected = style_plan(ctx.src, tpl, _style_parts(ctx, a),
                                       a.get("import_missing_styles", True), strip_extensions)
    except ValueError as exc:
        errs.append(f"styles_added: {exc}")
        return "styles_added"
    if a.get("style_map", mapping) != mapping or sorted(a.get("ids", [])) != sorted(expected):
        errs.append("styles_added: claimed imports or mapping differ from the input-derived closure")
    new = sorted(set(ctx.out_styles.by_id) - set(ctx.src_styles.by_id))
    gone = sorted(set(ctx.src_styles.by_id) - set(ctx.out_styles.by_id))
    if new != sorted(expected) or gone:
        errs.append(f"styles_added: new styles {new} (expected {sorted(expected)}), removed {gone}")
    for sid, definition in expected.items():
        out = ctx.out_styles.by_id.get(sid)
        count = sum(el.get(W + "styleId") == sid for el in ctx.out_styles.root.findall(W + "style"))
        if count != 1 or sig(out) != sig(definition):
            errs.append(f"styles_added: {sid} differs from the reconciled template definition or occurs more than once")
        ctx.added_styles.add(sid)
    return f"styles_added: {a.get('ids')}"


TEMPLATE_KINDS = {
    "hf_template_refs": check_hf_template_refs,
    "template_parts": check_template_part,
    "styles_added": check_styles_added,
}
