"""Op: set_page_setup — margins, page size, orientation and the different first page, per section (w:sectPr/w:pgMar,
w:pgSz, w:titlePg).

Margins merge into the existing w:pgMar (every attribute of pgMar is required by the schema, so a section
without one is an exception rather than a guess); ``margins.header`` / ``margins.footer`` are the header and footer
distances from the page edge. Orientation sets w:pgSz/@w:orient and swaps w/h when they disagree with it; margins are
not rotated (say so in the spec if they should change too). Page setup is not tracked (w:sectPrChange is not
implemented); ``tracked: true`` is refused.

Built-in batch 5 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §7):

* ``different_first_page`` true → the section's w:titlePg present with no w:val (a ``w:val="0"`` removed); false →
  absent. A section where it is already in effect as asked (titlePg present and not off / absent or off) is left alone
  and not counted. Each changed section's note says what its first page will show, header and footer, through
  inheritance (``hf_common.effective_refs``: a section without a first-page reference shows the previous section's);
  a first page that will show NO header/footer (no first-page part in this section or any before it) or an EARLIER
  section's first-page part is flagged.
* ``select.sections`` keeps ``"all"`` and ``[indices]`` and gains a content form ``{containing, with_tables,
  index_range}`` (``selectors.resolve_sections``): every given key must hold. No section chosen → ``not-found``
  unless ``allow_missing`` (a no-op with a note).
* With an orientation, a header/footer part a changed section shows (any type it displays — default always, first
  with titlePg as it will be after the change, even with evenAndOddHeaders —, through inheritance) that is also shown
  by a section of the other orientation (after the change: w > h) is flagged, once per part: its right-aligned items
  stay where the portrait page puts them. The notes and flags are advice for review; V10 checks the sectPr changes.

Built-in batch 6 (docs/BUILTIN_OPS_BATCH6_DESIGN.md §8) — the section's layout, each written as Word writes it (a
value that is the schema's default is an absent attribute or element) and left alone where it is already in effect:

* ``columns`` ``{count, space, separator}`` → w:cols: ``w:num`` the count, ``w:space`` the twips between columns
  (given; else the section's own), ``w:sep`` on with a separator (off: absent; not given: as it was); the columns are
  always equal (``w:equalWidth`` and every per-column ``w:col`` go). ``count`` 1 is a single column: ``w:num``,
  ``w:sep``, ``w:equalWidth`` and the ``w:col`` go, ``w:space`` stays; a section without w:cols already has one.
* ``line_numbering`` ``{count_by, start, restart, distance}`` → w:lnNumType: ``w:countBy`` (given, else the section's,
  else 1), ``w:start`` = ``start`` − 1 (Word shows the attribute plus one; ``start`` 1 is the default, absent),
  ``w:restart`` (``new_page`` is the default, absent), ``w:distance`` (twips from the text); ``"none"`` removes it.
* ``vertical_alignment`` → w:vAlign (``top``, the default: absent); ``section_start`` → w:type (``next_page``, the
  default: absent) — how the section begins after the one before it.

Each is an ``exact`` element spec (the element's attributes exactly, no children) or ``absent`` in ``section_props``.
Columns narrower than half an inch are flagged; an even- or odd-page start is noted (Word may add a blank page).

Word's own bounds, refused at plan time (``bad-params``; a page Word would not lay out renders blank or loses its
text, and the verifier cannot see that): a page width or height outside 0.1 to 22 inches (``MIN_PAGE_TWIPS`` 144 to
``MAX_TWIPS`` 31680); and, in each chosen section once this op's own size and margins are set (``text_area``: the
section's own values where the op gives none), a text width (the page less the left and right margins and the
gutter) or a text height (the page less the top and bottom margins, a negative one — Word's "exactly" margin — by its
size) below half an inch (``MIN_TEXT_TWIPS`` 720), or a header or footer distance at or beyond the page's height. The
gutter counts against the height instead when the document's settings put it at the top (``gutter_at_top``:
``w:gutterAtTop`` on, unless ``w:mirrorMargins`` or a book fold holds it on the inside edge, as Word's Page Setup
dialog does). The refusal names the section and the values. The header, footer and gutter distances have no other
bound than being non-negative; top and bottom may be negative.

V10 (``section_props``): one assertion carries the user's own ``select`` and parameters, the sections the plan chose
and each changed section's element specs; the verifier re-derives the choice and every change from the SOURCE.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import SECTPR_ORDER, insert_in_order
from . import prose, register
from .base import JobException, Op, PlanFragment
from .hf_common import effective_refs, refs
from .selectors import resolve_sections, section_reads

MARGIN_KEYS = ("top", "right", "bottom", "left", "header", "footer", "gutter")
FALSY = ("0", "false", "off")
LAYOUT_PARAMS = ("columns", "line_numbering", "vertical_alignment", "section_start")
ASSERTED_PARAMS = ("margins", "size", "orientation", "different_first_page", "allow_missing") + LAYOUT_PARAMS
TITLE_PG_ON = {"present": True, "remove": ["val"]}      # section_props element specs (V10)
TITLE_PG_OFF = {"absent": True}
# the user's words → Word's (w:type, w:lnNumType/@w:restart); the first of each is the schema's default
START_TYPES = {"next_page": "nextPage", "continuous": "continuous", "even_page": "evenPage", "odd_page": "oddPage"}
LINE_RESTARTS = {"new_page": "newPage", "new_section": "newSection", "continuous": "continuous"}
VERTICAL = ("top", "center", "both", "bottom")
COLUMN_KEYS = ("count", "space", "separator")
LINE_KEYS = ("count_by", "start", "restart", "distance")
MAX_TWIPS = 31680                                       # 22 inches, Word's largest page side
MIN_PAGE_TWIPS = 144                                    # 0.1 inch, Word's smallest page side
MIN_TEXT_TWIPS = 720                                    # half an inch: the least text width and height Word lays out
START_WORDS = {"next_page": "a new page", "continuous": "the same page (continuous)",
               "even_page": "the next even-numbered page", "odd_page": "the next odd-numbered page"}
VERTICAL_WORDS = {"top": "the top of the page", "center": "the centre of the page",
                  "both": "the full height of the page (justified)", "bottom": "the bottom of the page"}
LINE_RESTART_WORDS = {"new_page": "restarting on each page", "new_section": "restarting at each section",
                      "continuous": "counting on through the document"}


def title_pg_on(sp: Any) -> bool:
    """Different first page in effect: a w:titlePg whose w:val is absent or not off."""
    tp = sp.find(W + "titlePg")
    return tp is not None and tp.get(W + "val") not in FALSY


def page_orientation(sp: Any) -> str:
    """The shape of the section's page: landscape when its width exceeds its height (w:orient when they are missing)."""
    pg = sp.find(W + "pgSz")
    if pg is None:
        return "portrait"
    w, h = pg.get(W + "w") or "", pg.get(W + "h") or ""
    if w.isdigit() and h.isdigit():
        return "landscape" if int(w) > int(h) else "portrait"
    return "landscape" if pg.get(W + "orient") == "landscape" else "portrait"


def shown_parts(eff: List[Dict[Tuple[str, str], Optional[str]]], titles: List[bool], even_odd: bool,
                i: int) -> List[Tuple[str, str]]:
    """(kind, part) Word shows on section ``i``'s pages: default always, first with titlePg, even with
    evenAndOddHeaders."""
    out: List[Tuple[str, str]] = []
    for (kind, typ), part in sorted(eff[i].items()):
        if part and (typ == "default" or (typ == "first" and titles[i]) or (typ == "even" and even_odd)):
            if (kind, part) not in out:
                out.append((kind, part))
    return out


PAGE_SIZE_NAMES = {(12240, 15840): "US Letter", (12240, 20160): "US Legal", (15840, 24480): "Tabloid",
                   (11906, 16838): "A4", (16838, 23811): "A3", (8391, 11906): "A5"}


def page_size_words(w: Any, h: Any) -> str:
    """``US Letter (8.5 by 11 inches)``, ``8.27 by 11.69 inches`` — either way around for a landscape sheet."""
    try:
        wi, hi = int(w), int(h)
    except (TypeError, ValueError):
        return f"{w} by {h}"
    dims = f"{prose.inches(wi).split(' ')[0]} by {prose.inches(hi)}"
    name = PAGE_SIZE_NAMES.get((wi, hi)) or PAGE_SIZE_NAMES.get((hi, wi))
    return f"{name} ({dims})" if name else dims


def margin_words(margins: Dict[str, Any]) -> List[str]:
    """``every margin to 1 inch``, ``the top margin to 1 inch``, ``the header 0.5 inch from the page edge``."""
    out: List[str] = []
    edges = ("top", "bottom", "left", "right")
    if all(k in margins for k in edges) and len({str(margins[k]) for k in edges}) == 1:
        out.append(f"every margin to {prose.inches(margins['top'])}")
    else:
        out += [f"the {k} margin to {prose.inches(margins[k])}" for k in edges if k in margins]
    if "gutter" in margins:
        out.append(f"the gutter to {prose.inches(margins['gutter'])}")
    out += [f"the {k} {prose.inches(margins[k])} from the page edge" for k in ("header", "footer") if k in margins]
    return out


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_layout(params: Dict[str, Any]) -> None:
    """The layout parameters as the op reads them (module docstring); bad-params otherwise."""
    cols = params.get("columns")
    if cols is not None:
        if not isinstance(cols, dict) or set(cols) - set(COLUMN_KEYS) or "count" not in cols:
            raise JobException(f"columns must be {{count, space, separator}} with a count, got {cols!r}", "bad-params")
        if not _whole(cols["count"]) or not 1 <= cols["count"] <= 45:
            raise JobException("columns.count must be a whole number from 1 to 45", "bad-params")
        if "space" in cols and (not _whole(cols["space"]) or not 0 <= cols["space"] <= MAX_TWIPS):
            raise JobException("columns.space must be the twips between the columns (0 to 31680)", "bad-params")
        if "separator" in cols and not isinstance(cols["separator"], bool):
            raise JobException("columns.separator must be true or false", "bad-params")
        if cols["count"] == 1 and ("space" in cols or cols.get("separator") is True):
            raise JobException("a single column has no space between columns and no separator line", "bad-params")
    lines = params.get("line_numbering")
    if lines is not None and lines != "none":
        if not isinstance(lines, dict) or set(lines) - set(LINE_KEYS):
            raise JobException(f"line_numbering must be 'none' or {{count_by, start, restart, distance}}, got "
                               f"{lines!r}", "bad-params")
        for key, lo, hi in (("count_by", 1, 100), ("start", 1, 32767), ("distance", 0, MAX_TWIPS)):
            if key in lines and (not _whole(lines[key]) or not lo <= lines[key] <= hi):
                raise JobException(f"line_numbering.{key} must be a whole number from {lo} to {hi}", "bad-params")
        if "restart" in lines and lines["restart"] not in LINE_RESTARTS:
            raise JobException(f"line_numbering.restart must be one of {list(LINE_RESTARTS)}", "bad-params")
    if params.get("vertical_alignment") not in (None,) + VERTICAL:
        raise JobException(f"vertical_alignment must be one of {list(VERTICAL)}", "bad-params")
    if params.get("section_start") not in (None, *START_TYPES):
        raise JobException(f"section_start must be one of {list(START_TYPES)}", "bad-params")


def w_attrs(el: Any) -> Dict[str, str]:
    """An element's w: attributes by local name ({} for no element)."""
    return {} if el is None else {k[len(W):]: v for k, v in el.attrib.items() if k.startswith(W)}


def _put(want: Dict[str, str], key: str, value: str, default: Optional[str]) -> None:
    """``want[key]`` made to read ``value``: left as it is when it already does (absent reads ``default``), removed
    when ``value`` is the default, written otherwise."""
    if want.get(key, default) == value:
        return
    if value == default:
        want.pop(key, None)
    else:
        want[key] = value


def layout_specs(sp: Any, params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The section_props element specs the layout parameters give one section's sectPr ``sp``: ``{"exact":
    {attribute: value}}`` (the element with exactly these w: attributes and no children; created where missing) or
    ``{"absent": True}``, per element — none where the section is already as asked (module docstring)."""
    out: Dict[str, Dict[str, Any]] = {}

    def exact(name: str, want: Dict[str, str]) -> None:
        el = sp.find(W + name)
        if el is None or want != w_attrs(el) or len(el):
            out[name] = {"exact": want}

    cols = params.get("columns")
    if cols is not None:
        el = sp.find(W + "cols")
        want = w_attrs(el)
        on, asked = want.get("sep") not in (None,) + FALSY, cols.get("separator")
        if on and (asked is False or (asked is None and cols["count"] == 1)):
            want.pop("sep")                         # a single column has no line between columns
        elif not on and asked is True:
            want["sep"] = "1"
        if want.get("equalWidth") in FALSY:
            want.pop("equalWidth")                  # the columns are equal: the per-column widths go
        _put(want, "num", str(cols["count"]), "1")
        if "space" in cols:
            _put(want, "space", str(cols["space"]), None)
        if el is not None or cols["count"] > 1:     # no w:cols: a single column already
            exact("cols", want)
    lines = params.get("line_numbering")
    if lines == "none":
        if sp.find(W + "lnNumType") is not None:
            out["lnNumType"] = {"absent": True}
    elif lines is not None:
        want = w_attrs(sp.find(W + "lnNumType"))
        if "count_by" in lines:
            _put(want, "countBy", str(lines["count_by"]), None)
        want.setdefault("countBy", "1")
        if "start" in lines:
            _put(want, "start", str(lines["start"] - 1), "0")     # Word shows w:start plus one
        if "restart" in lines:
            _put(want, "restart", LINE_RESTARTS[lines["restart"]], "newPage")
        if "distance" in lines:
            _put(want, "distance", str(lines["distance"]), None)
        exact("lnNumType", want)
    for name, key, words, default in (("vAlign", "vertical_alignment", {v: v for v in VERTICAL}, "top"),
                                      ("type", "section_start", START_TYPES, "nextPage")):
        asked = params.get(key)
        if asked is None:
            continue
        el = sp.find(W + name)
        value = words[asked]
        if w_attrs(el).get("val", default) == value:
            continue                                # already in effect (absent reads the default)
        if value == default:
            out[name] = {"absent": True}
        else:
            out[name] = {"exact": {**w_attrs(el), "val": value}}
    return out


def effective_twips(sp: Any, elements: Dict[str, Dict[str, str]], name: str, key: str) -> Optional[int]:
    """Section ``sp``'s ``w:<name>/@w:<key>`` (twips) once this op's own elements are set: the value the op writes,
    else the section's own; None when neither reads as a whole number."""
    el = sp.find(W + name)
    v = elements.get(name, {}).get(key, el.get(W + key) if el is not None else None)
    return int(v) if v is not None and str(v).lstrip("-").isdigit() else None


def gutter_at_top(pkg: DocxPackage) -> bool:
    """Whether the document's gutter sits at the top of the page (``w:gutterAtTop`` in the settings) rather than at
    the side: mirrored margins and a book fold keep it on the inside edge whatever that flag says (Word's Page Setup
    dialog fixes the gutter position for those layouts)."""
    return pkg.settings_flag("gutterAtTop") and not any(
        pkg.settings_flag(n) for n in ("mirrorMargins", "bookFoldPrinting", "bookFoldRevPrinting"))


def text_area(sp: Any, elements: Dict[str, Dict[str, str]],
              gutter_top: bool = False) -> Tuple[Optional[int], Optional[int]]:
    """(text width, text height) in twips of section ``sp`` once this op's own size and margins (``elements``) are
    set: the page width less the left and right margins; the page height less the top and bottom margins, a
    negative one (Word's "exactly" margin) by its size; the gutter off the width, or off the height when the document
    puts it at the top (``gutter_top``). None where a value cannot be read."""
    w, h = effective_twips(sp, elements, "pgSz", "w"), effective_twips(sp, elements, "pgSz", "h")
    left, right = effective_twips(sp, elements, "pgMar", "left"), effective_twips(sp, elements, "pgMar", "right")
    top, bottom = effective_twips(sp, elements, "pgMar", "top"), effective_twips(sp, elements, "pgMar", "bottom")
    gutter = effective_twips(sp, elements, "pgMar", "gutter") or 0
    width = None if None in (w, left, right) else w - left - right - (0 if gutter_top else gutter)
    height = None if None in (h, top, bottom) else h - abs(top) - abs(bottom) - (gutter if gutter_top else 0)
    return width, height


def column_width(sp: Any, elements: Dict[str, Dict[str, str]], cols: Dict[str, Any],
                 gutter_top: bool = False) -> Optional[int]:
    """Each column's width (twips) in section ``sp`` once this op's own size and margins (``elements``) are set; None
    when the page size or margins cannot be read."""
    text, _ = text_area(sp, elements, gutter_top)
    if text is None:
        return None
    own = w_attrs(sp.find(W + "cols")).get("space", "720")
    space = cols.get("space", int(own) if own.isdigit() else 720)
    return (text - (cols["count"] - 1) * space) // cols["count"]


def check_page(i: int, sp: Any, elements: Dict[str, Dict[str, str]], gutter_top: bool = False) -> None:
    """Word's bounds on section ``i``'s page once this op's own size and margins (``elements``) are set (module
    docstring): at least half an inch of text width and of text height (the gutter off whichever the document puts it
    on, ``gutter_top``), and the header and footer within the page — ``bad-params`` otherwise, naming the section and
    the values. A value that cannot be read is not judged."""
    def twips(name: str, key: str) -> Optional[int]:
        return effective_twips(sp, elements, name, key)

    def words(v: Optional[int]) -> str:
        return f"{v} twips ({prose.inches(v)})"

    w, h = twips("pgSz", "w"), twips("pgSz", "h")
    width, height = text_area(sp, elements, gutter_top)
    gutter = f"a gutter of {twips('pgMar', 'gutter') or 0}"
    left, right = f"a left margin of {twips('pgMar', 'left')}", f"a right margin of {twips('pgMar', 'right')}"
    top, bottom = f"a top margin of {twips('pgMar', 'top')}", f"a bottom margin of {twips('pgMar', 'bottom')}"
    if width is not None and width < MIN_TEXT_TWIPS:
        sides = f"{left} and {right}" if gutter_top else f"{left}, {right} and {gutter}"
        raise JobException(
            f"section {i}: a page {words(w)} wide less {sides} leaves {words(width)} for the text; Word keeps at "
            f"least {MIN_TEXT_TWIPS} (half an inch) — widen the page or narrow the margins",
            "bad-params", {"section": i, "text_width": width})
    if height is not None and height < MIN_TEXT_TWIPS:
        ends = f"{top}, {bottom} and {gutter} at the top" if gutter_top else f"{top} and {bottom}"
        raise JobException(
            f"section {i}: a page {words(h)} tall less {ends} leaves {words(height)} for the text; Word keeps at "
            f"least {MIN_TEXT_TWIPS} (half an inch) — make the page taller or the margins smaller",
            "bad-params", {"section": i, "text_height": height})
    for kind in ("header", "footer"):
        v = twips("pgMar", kind)
        if v is not None and h is not None and v >= h:
            raise JobException(
                f"section {i}: the {kind} {words(v)} from the page edge lies off a page {words(h)} tall",
                "bad-params", {"section": i, kind: v})


def _nth(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def layout_words(params: Dict[str, Any]) -> List[str]:
    """``the text to 2 columns 0.5 inch apart with a line between them``, ``line numbers to every 5th line, starting
    at 1, restarting on each page``, ``the text vertically to the centre of the page``, ``the section start to a new
    page`` — what the layout parameters set, for describe()."""
    out: List[str] = []
    cols = params.get("columns")
    if isinstance(cols, dict) and _whole(cols.get("count")):
        if cols["count"] == 1:
            out.append("the text to a single column")
        else:
            words = f"the text to {cols['count']} columns of equal width"
            if _whole(cols.get("space")):
                words += f" {prose.inches(cols['space'])} apart"
            if isinstance(cols.get("separator"), bool):
                words += " with a line between them" if cols["separator"] else " without a line between them"
            out.append(words)
    lines = params.get("line_numbering")
    if isinstance(lines, dict):
        n = lines.get("count_by", 1)
        words = "line numbers to every line" if n == 1 else f"line numbers to every {_nth(n) if _whole(n) else n} line"
        if _whole(lines.get("start")):
            words += f", starting at {lines['start']}"
        if lines.get("restart") in LINE_RESTART_WORDS:
            words += ", " + LINE_RESTART_WORDS[lines["restart"]]
        if _whole(lines.get("distance")):
            words += f", {prose.inches(lines['distance'])} from the text"
        out.append(words)
    if params.get("vertical_alignment") in VERTICAL_WORDS:
        out.append("the text vertically to " + VERTICAL_WORDS[params["vertical_alignment"]])
    if params.get("section_start") in START_WORDS:
        out.append("the section start to " + START_WORDS[params["section_start"]])
    return out


@register
class SetPageSetup(Op):
    name = "set_page_setup"
    mode = "formatting"
    description = ("Set margins (incl. header/footer distance from the page edge), page size, orientation, different "
                   "first page, columns, line numbering, vertical alignment and/or the section start of selected "
                   "sections — by index or by what they contain.")
    params_schema = {"type": "object", "properties": {
        "margins": {"type": "object",
                    "description": "top/right/bottom/left/header/footer/gutter in twips (1440 = 1 in); "
                                   "header/footer = the header's/footer's distance from the page edge; the page "
                                   "must keep at least 720 twips of text width and height inside them"},
        "size": {"type": "object", "description": "{w, h} in twips (12240 x 15840 = US Letter; each 144 to 31680)"},
        "orientation": {"type": "string", "enum": ["portrait", "landscape"],
                        "description": "swaps width and height where they disagree; margins are not rotated"},
        "different_first_page": {"type": "boolean",
                                 "description": "true: the section's first page shows its first-page header/footer "
                                                "(w:titlePg on); false: it shows the ordinary ones"},
        "columns": {"type": "object", "properties": {
                        "count": {"type": "integer", "minimum": 1, "maximum": 45},
                        "space": {"type": "integer", "description": "twips between the columns"},
                        "separator": {"type": "boolean", "description": "a line between the columns"}},
                    "required": ["count"],
                    "description": "equal columns; count 1 = a single column (no space or separator then)"},
        "line_numbering": {"description": "'none' turns line numbers off; or {count_by (every Nth line, default 1), "
                                          "start (the first number shown), restart: new_page | new_section | "
                                          "continuous, distance (twips from the text)}"},
        "vertical_alignment": {"type": "string", "enum": list(VERTICAL),
                               "description": "where the text sits between the top and bottom margins"},
        "section_start": {"type": "string", "enum": list(START_TYPES),
                          "description": "how the section begins after the one before it (its section break)"},
        "allow_missing": {"type": "boolean", "default": False,
                          "description": "content-form sections: a file where no section matches is a no-op instead "
                                         "of not-found"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {"sections": {
        "description": "'all', [section indices] (0-based), or by content: {containing: <paragraph predicate>, "
                       "with_tables: bool, index_range: [first, last]} — every given key must hold"}}}
    verification_profile = {"V2": "zero text diffs",
                            "V10": "section_props: the selection re-derived from the user's select; per section "
                                   "pgMar/pgSz attributes exactly as set, titlePg present without w:val / absent, "
                                   "cols/lnNumType/vAlign/type exactly as asked or absent; nothing else",
                            "V7": "none — page geometry changes by design"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        what: List[str] = []
        margins, size = params.get("margins"), params.get("size")
        if isinstance(margins, dict) and margins:
            what += margin_words(margins)
        if isinstance(size, dict) and size.get("w") is not None and size.get("h") is not None:
            what.append("the page size to " + page_size_words(size["w"], size["h"]))
        orient = params.get("orientation")
        if orient in ("portrait", "landscape"):
            what.append(f"the orientation to {orient}")
        what += layout_words(params)
        first = params.get("different_first_page")
        clauses = [c for c in ("give the first page its own header and footer" if first is True else
                               "show the ordinary header and footer on the first page" if first is False else "",
                               "turn line numbering off" if params.get("line_numbering") == "none" else "") if c]
        where = prose.where(select, default="every section")
        if what:
            head = prose.sentence("set", prose.join(what), where, f"and {prose.join(clauses)}" if clauses else "")
        elif clauses:
            head = prose.sentence(prose.join(clauses), where)
        else:
            head = prose.sentence("set the margins, page size, orientation, columns, line numbering and first-page "
                                  "header and footer", where)
        extras: List[str] = []
        if orient in ("portrait", "landscape"):
            extras.append("the page width and height swap where they disagree with it")
            if not (isinstance(margins, dict) and margins):
                extras.append("the margins stay as they are")
        if params.get("section_start") in ("even_page", "odd_page"):
            extras.append("Word adds a blank page where the page before would end on the wrong side")
        if params.get("allow_missing") is True:
            extras.append("files where no section matches are left unchanged")
        return head if not extras else head + " " + prose.sentence(prose.join(extras))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"sections"}:
            raise JobException(f"set_page_setup: unknown selector key(s) {sorted(set(select) - {'sections'})}", "bad-params")
        if params.get("tracked"):
            raise JobException("page setup changes are not tracked (w:sectPrChange not implemented)", "bad-params")
        margins = params.get("margins") or {}
        size = params.get("size") or {}
        orient = params.get("orientation")
        first = params.get("different_first_page")
        allow_missing = params.get("allow_missing", False)
        for name, d, keys in (("margins", margins, MARGIN_KEYS), ("size", size, ("w", "h"))):
            if not isinstance(d, dict) or set(d) - set(keys):
                raise JobException(f"{name} keys must be among {keys}", "bad-params")
            for k, v in d.items():
                if isinstance(v, bool) or not isinstance(v, int) or (v < 0 and k not in ("top", "bottom")):
                    raise JobException(f"{name}.{k} must be an integer number of twips", "bad-params")
        if size and set(size) != {"w", "h"}:
            raise JobException("size needs both w and h", "bad-params")
        for k, v in size.items():
            if not MIN_PAGE_TWIPS <= v <= MAX_TWIPS:
                raise JobException(f"size.{k} must be from {MIN_PAGE_TWIPS} to {MAX_TWIPS} twips (0.1 to 22 inches, "
                                   f"Word's range), got {v}", "bad-params")
        if orient not in (None, "portrait", "landscape"):
            raise JobException("orientation must be 'portrait' or 'landscape'", "bad-params")
        if first is not None and not isinstance(first, bool):
            raise JobException("different_first_page must be true or false", "bad-params")
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        check_layout(params)
        layout = any(params.get(k) is not None for k in LAYOUT_PARAMS)
        if not (margins or size or orient or first is not None or layout):
            raise JobException("nothing to set: give margins, size, orientation, different_first_page, columns, "
                               "line_numbering, vertical_alignment and/or section_start", "bad-params")
        chooser = select.get("sections", "all")
        ords = resolve_sections(pkg, chooser, content=True)
        main = pkg.main_part()
        secs = DocxPackage.sect_prs(pkg.xml(main))
        frag = self.fragment(select, params)
        frag.intended_parts = [main] if ords else []
        frag.geometry_reference, frag.geometry_reason = "none", "page setup changes move every page's content by design"
        # what the content form reads (its paragraphs' words and styles): another op changing them is a plan-time
        # conflict (rule 9)
        frag.touches += section_reads(pkg, chooser)
        if isinstance(chooser, dict) and ords and len(ords) == len(secs):
            # chosen by content, and every section matched — in a one-section file that is the whole document ("Schedule
            # 40 pipe" matches a 'schedule' pattern in the body of a piping spec): say so, the user decides (review)
            frag.flags.append(f"the content selection chose every section of this file ({len(secs)}): the change applies "
                              f"to the whole document, not to a part of it — check that the pattern does not match "
                              f"ordinary text")
        gutter_top = gutter_at_top(pkg)
        per_section: Dict[int, Dict[str, Dict[str, str]]] = {}
        asserted: Dict[str, Dict[str, Dict[str, Any]]] = {}     # section_props element specs per changed section
        titles: Dict[int, bool] = {}
        layouts: Dict[int, Dict[str, Dict[str, Any]]] = {}      # the layout parameters' element specs (batch 6)
        already: List[int] = []
        laid_out: List[int] = []                                # the layout already as asked
        for i in ords:
            sp = secs[i]
            elements: Dict[str, Dict[str, str]] = {}
            if margins:
                if sp.find(W + "pgMar") is None:
                    raise JobException(f"section {i} has no w:pgMar (all of its attributes are required; not guessing "
                                       f"the missing ones)", "structure", {"section": i})
                elements["pgMar"] = {k: str(v) for k, v in margins.items()}
            if size or orient:
                pg = sp.find(W + "pgSz")
                w = size.get("w", int(pg.get(W + "w")) if pg is not None and pg.get(W + "w") else None)
                h = size.get("h", int(pg.get(W + "h")) if pg is not None and pg.get(W + "h") else None)
                if w is None or h is None:
                    raise JobException(f"section {i} has no page size to orient; give size", "structure", {"section": i})
                pg_set: Dict[str, str] = {}
                if orient:
                    if (orient == "landscape") != (w > h):
                        w, h = h, w
                    pg_set["orient"] = orient
                pg_set["w"], pg_set["h"] = str(w), str(h)
                elements["pgSz"] = pg_set
            if elements:
                check_page(i, sp, elements, gutter_top)
            if first is not None:
                # V10 reads every selected section's titlePg in the OUTPUT, an already-as-asked one included: another
                # op flipping it (the templating op following its template) is a plan-time conflict
                frag.touches.append((main, f"sectPr:{i}", "verify:titlePg"))
                if title_pg_on(sp) != first:
                    titles[i] = first
                else:
                    already.append(i)
            lay = layout_specs(sp, params) if layout else {}
            if layout and not lay:
                laid_out.append(i)
            if elements or i in titles or lay:
                per_section[i] = elements
                asserted[str(i)] = {name: {"set": attrs} for name, attrs in elements.items()}
                asserted[str(i)].update(copy.deepcopy(lay))
            if lay:
                layouts[i] = lay
                width = column_width(sp, elements, params["columns"], gutter_top) if "cols" in lay else None
                if width is not None and params["columns"]["count"] > 1 and width < 720:
                    frag.flags.append(f"section {i}: {params['columns']['count']} columns leave each column "
                                      f"{prose.inches(max(width, 0))} wide — less than half an inch")
            frag.touches += [(main, f"sectPr:{i}", name) for name in list(elements) + list(lay)]
            if i in titles:
                asserted[str(i)]["titlePg"] = copy.deepcopy(TITLE_PG_ON if titles[i] else TITLE_PG_OFF)
                frag.touches.append((main, f"sectPr:{i}", "titlePg"))
        changed = sorted(per_section)
        frag.property_assertions.append({
            # the user's own words (absent = the verifier's default) and what the plan chose from them: V10 re-derives
            # the choice and each section's change and compares
            "kind": "section_props", "part": main, "select": copy.deepcopy(select),
            "params": {k: copy.deepcopy(params[k]) for k in ASSERTED_PARAMS if k in params},
            "selected": list(ords), "sections": changed, "per_section": asserted,
            "expected_sections": len(changed)})
        frag.transform_data = {"main": main, "sections": {str(i): e for i, e in per_section.items()},
                               "title_pg": {str(i): v for i, v in titles.items()},
                               "layout": {str(i): s for i, s in layouts.items()}}
        if not ords:
            if not isinstance(chooser, dict):
                frag.notes.append("no section selected; nothing to do")
                return frag
            if not allow_missing:
                raise JobException(f"no section matches {chooser!r}", "not-found", {"sections": chooser})
            frag.notes.append(f"no section matches {chooser!r}; nothing to do here (allow_missing)")
            return frag
        if isinstance(chooser, dict):
            frag.notes.append(f"sections chosen by {chooser!r}: {list(ords)} of {len(secs)}")
        frag.notes.append(f"sections {ords}: " + "; ".join(
            f"{i}: {per_section.get(i, {})}" + (f" + titlePg {'on' if titles[i] else 'off'}" if i in titles else "")
            + (f" + {layouts[i]}" if i in layouts else "") for i in ords))
        if already:
            frag.notes.append(f"different first page already {'on' if first else 'off'} in section(s) {already}: "
                              f"left alone")
        if laid_out:
            frag.notes.append(f"the asked {', '.join(k for k in LAYOUT_PARAMS if params.get(k) is not None)} already "
                              f"in effect in section(s) {laid_out}: left alone")
        starting = [i for i in layouts if "type" in layouts[i]]
        if params.get("section_start") in ("even_page", "odd_page") and starting:
            frag.notes.append(f"section(s) {starting} start on {START_WORDS[params['section_start']]}: Word adds a "
                              f"blank page where the page before would end on the wrong side")
        if 0 in starting:
            frag.notes.append("section 0 is the first section: no page comes before it, so its start has no effect "
                              "until a section is inserted before it")
        older = [i for i in changed if secs[i].find(W + "sectPrChange") is not None]
        if older:                          # the change is plain; a pending tracked one stays (batch 5's review)
            frag.flags.append(f"section(s) {older} already carry a pending tracked section change (w:sectPrChange) "
                              f"from an earlier reviewer; reject-all would restore that older page setup")
        if titles:
            self._first_pages(pkg, secs, titles, frag)
        if orient:
            frag.notes.append("orientation swaps page width/height where they disagree with it; margins are not rotated")
            self._mixed_orientation(pkg, secs, ords, orient, titles, frag)
        return frag

    @staticmethod
    def _first_pages(pkg: DocxPackage, secs, titles: Dict[int, bool], frag: PlanFragment) -> None:
        """What each changed section's first page will show (references do not change: what a section shows is
        resolved through inheritance from the source's; the first page shows the first-page parts with titlePg, the
        default ones without), and the flags (§7)."""
        eff = effective_refs(pkg, secs)
        own = [{(kind, typ) for kind, typ, _, _ in refs(sp)} for sp in secs]
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        for i, on in sorted(titles.items()):
            shows = []
            for kind in ("header", "footer"):
                typ = "first" if on else "default"
                part = eff[i].get((kind, typ))
                if part is None:
                    shows.append(f"no {kind}")
                    if on:
                        frag.flags.append(f"section {i}: different first page is turned on, but no section up to it "
                                          f"has a first-page {kind}: its first page will show no {kind} (blank)")
                    continue
                origin = next(j for j in range(i, -1, -1) if (kind, typ) in own[j])
                shows.append(f"{kind} {part}" + ("" if origin == i else f" (section {origin}'s, inherited)"))
                if on and origin != i:
                    frag.flags.append(f"section {i}: different first page is turned on and it has no first-page "
                                      f"{kind} of its own: its first page will show section {origin}'s first-page "
                                      f"{kind} {part} (Word's 'Link to Previous')")
            note = (f"section {i}: different first page {'on' if on else 'off'} — its first page shows "
                    + ", ".join(shows))
            if not on and even_odd:
                note += " (or the even-page ones when that page number is even)"
            frag.notes.append(note)

    @staticmethod
    def _mixed_orientation(pkg: DocxPackage, secs, ords: List[int], orient: str, titles: Dict[int, bool],
                           frag: PlanFragment) -> None:
        """A header/footer part a changed section shows, also shown by a section of the other orientation (after the
        change) — one flag per part."""
        eff = effective_refs(pkg, secs)
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        after_titles = [titles.get(i, title_pg_on(sp)) for i, sp in enumerate(secs)]
        after_orient = [orient if i in ords else page_orientation(sp) for i, sp in enumerate(secs)]
        shown = [shown_parts(eff, after_titles, even_odd, i) for i in range(len(secs))]
        seen = set()
        for i in ords:
            for kind, part in shown[i]:
                if part in seen:
                    continue
                seen.add(part)
                by = [j for j in range(len(secs)) if (kind, part) in shown[j]]
                portrait = [j for j in by if after_orient[j] == "portrait"]
                landscape = [j for j in by if after_orient[j] == "landscape"]
                if portrait and landscape:
                    frag.flags.append(f"the {kind} {part} is shown on portrait and landscape pages (portrait: "
                                      f"sections {portrait}; landscape: sections {landscape}): its right-aligned "
                                      f"items stay where the portrait page puts them")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["sections"] and not td.get("title_pg") and not td.get("layout"):
            return                                  # every section already as asked, or none chosen
        root = pkg.xml(td["main"])
        secs = DocxPackage.sect_prs(root)
        for i, elements in td["sections"].items():
            sp = secs[int(i)]
            for name, attrs in elements.items():
                el = sp.find(W + name)
                if el is None:
                    el = insert_in_order(sp, etree.Element(W + name), SECTPR_ORDER)
                for k, v in attrs.items():
                    el.set(W + k, v)
        for i, on in td.get("title_pg", {}).items():
            sp = secs[int(i)]
            tp = sp.find(W + "titlePg")
            if on:
                if tp is None:
                    insert_in_order(sp, etree.Element(W + "titlePg"), SECTPR_ORDER)
                else:
                    tp.attrib.pop(W + "val", None)
            elif tp is not None:
                sp.remove(tp)
        for i, specs in td.get("layout", {}).items():
            sp = secs[int(i)]
            for name, spec in specs.items():
                el = sp.find(W + name)
                if spec.get("absent"):
                    if el is not None:
                        sp.remove(el)
                    continue
                if el is None:
                    el = insert_in_order(sp, etree.Element(W + name), SECTPR_ORDER)
                for k in [k for k in el.attrib if k.startswith(W)]:
                    del el.attrib[k]
                for child in list(el):
                    el.remove(child)
                for k, v in spec["exact"].items():
                    el.set(W + k, v)
        pkg.write_xml(td["main"], root)
