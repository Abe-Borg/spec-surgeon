"""Op: set_page_setup — margins, page size and orientation per section (w:sectPr/w:pgMar, w:pgSz).

Margins merge into the existing w:pgMar (every attribute of pgMar is required by the schema, so a section
without one is an exception rather than a guess). Orientation sets w:pgSz/@w:orient and swaps w/h when they
disagree with it; margins are not rotated (say so in the spec if they should change too). Page setup is not
tracked (w:sectPrChange is not implemented); ``tracked: true`` is refused.
"""
from __future__ import annotations

from typing import Any, Dict, List

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import SECTPR_ORDER, insert_in_order
from . import register
from .base import JobException, Op, PlanFragment
from .selectors import resolve_sections

MARGIN_KEYS = ("top", "right", "bottom", "left", "header", "footer", "gutter")


@register
class SetPageSetup(Op):
    name = "set_page_setup"
    mode = "formatting"
    description = "Set margins, page size and/or orientation of selected sections."
    params_schema = {"type": "object", "properties": {
        "margins": {"type": "object", "description": "top/right/bottom/left/header/footer/gutter in twips (1440 = 1 in)"},
        "size": {"type": "object", "description": "{w, h} in twips (12240 x 15840 = US Letter)"},
        "orientation": {"type": "string", "enum": ["portrait", "landscape"]},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {"sections": {"description": "'all' or [section indices]"}}}
    verification_profile = {"V2": "zero text diffs",
                            "V10": "section_props per section: pgMar/pgSz attributes exactly as set; nothing else",
                            "V7": "none — page geometry changes by design"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"sections"}:
            raise JobException(f"set_page_setup: unknown selector key(s) {sorted(set(select) - {'sections'})}", "bad-params")
        if params.get("tracked"):
            raise JobException("page setup changes are not tracked (w:sectPrChange not implemented)", "bad-params")
        margins = params.get("margins") or {}
        size = params.get("size") or {}
        orient = params.get("orientation")
        for name, d, keys in (("margins", margins, MARGIN_KEYS), ("size", size, ("w", "h"))):
            if not isinstance(d, dict) or set(d) - set(keys):
                raise JobException(f"{name} keys must be among {keys}", "bad-params")
            for k, v in d.items():
                if isinstance(v, bool) or not isinstance(v, int) or (v < 0 and k not in ("top", "bottom")):
                    raise JobException(f"{name}.{k} must be an integer number of twips", "bad-params")
        if size and set(size) != {"w", "h"}:
            raise JobException("size needs both w and h", "bad-params")
        if orient not in (None, "portrait", "landscape"):
            raise JobException("orientation must be 'portrait' or 'landscape'", "bad-params")
        if not (margins or size or orient):
            raise JobException("nothing to set: give margins, size and/or orientation", "bad-params")
        ords = resolve_sections(pkg, select.get("sections", "all"))
        main = pkg.main_part()
        secs = DocxPackage.sect_prs(pkg.xml(main))
        frag = self.fragment(select, params)
        per_section: Dict[int, Dict[str, Dict[str, str]]] = {}
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
            per_section[i] = elements
            frag.property_assertions.append({"kind": "section_props", "part": main, "sections": [i],
                                             "elements": {k: {"set": v} for k, v in elements.items()},
                                             "expected_sections": 1})
            frag.touches += [(main, f"sectPr:{i}", name) for name in elements]
        frag.intended_parts = [main]
        frag.transform_data = {"main": main, "sections": {str(i): e for i, e in per_section.items()}}
        frag.geometry_reference, frag.geometry_reason = "none", "page setup changes move every page's content by design"
        frag.notes.append(f"sections {ords}: " + "; ".join(f"{i}: {e}" for i, e in per_section.items()))
        if orient:
            frag.notes.append("orientation swaps page width/height where they disagree with it; margins are not rotated")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
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
        pkg.write_xml(td["main"], root)
