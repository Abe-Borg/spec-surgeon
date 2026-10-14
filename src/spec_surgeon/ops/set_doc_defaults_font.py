"""Op: set_doc_defaults_font — set w:docDefaults/w:rPrDefault/w:rPr/w:rFonts (the bottom of every run's
font inheritance chain), creating the elements in schema order when absent. Theme attributes on the slots set
are cleared by default (Word lets them override the explicit name)."""
from __future__ import annotations

from typing import Any, Dict

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import RPR_ORDER, set_rfonts
from . import register
from .base import JobException, Op, PlanFragment
from .selectors import DEFAULT_PRESERVE_FONTS, FONT_PARAM_DOC, StyleSheet, font_values
from .set_font import FONT_REFLOW


@register
class SetDocDefaultsFont(Op):
    name = "set_doc_defaults_font"
    mode = "formatting"
    description = "Set the document-default font (docDefaults/rPrDefault) that every style ultimately inherits."
    params_schema = {"type": "object", "properties": {
        **{k: v for k, v in FONT_PARAM_DOC.items() if k != "preserve_fonts"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "description": "none"}
    verification_profile = {"V1": "styles.xml only", "V2": "zero text diffs",
                            "V10": "doc_defaults_fonts: only rFonts of rPrDefault changed",
                            "V7": "none — a font change reflows the text"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("set_doc_defaults_font takes no selector", "bad-params")
        if params.get("tracked"):
            raise JobException("docDefaults changes are not tracked; omit 'tracked' or set it false", "bad-params")
        values = font_values({**params, "preserve_fonts": DEFAULT_PRESERVE_FONTS})
        clear = bool(params.get("clear_theme_fonts", True))
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        frag = self.fragment(select, params)
        frag.intended_parts = [sheet.part]
        frag.touches = [(sheet.part, "docDefaults", "rPr")]
        frag.property_assertions.append({"kind": "doc_defaults_fonts", "styles_part": sheet.part, "set": values,
                                         "clear_theme": clear})
        frag.transform_data = {"styles_part": sheet.part, "values": values, "clear_theme": clear}
        frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        rpr = sheet.root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")
        rf = rpr.find(W + "rFonts") if rpr is not None else None
        before = {etree.QName(k).localname: v for k, v in rf.attrib.items()} if rf is not None else {}
        frag.notes.append(f"docDefaults rFonts {before or '(none)'} → {values}"
                          + (" (theme attributes on these slots cleared)" if clear else ""))
        if rpr is None:
            frag.notes.append("docDefaults/rPrDefault/rPr did not exist; created in schema order")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        root = pkg.xml(td["styles_part"])
        dd = root.find(W + "docDefaults")
        if dd is None:
            dd = etree.Element(W + "docDefaults")
            root.insert(0, dd)                       # first child of w:styles
        rpd = dd.find(W + "rPrDefault")
        if rpd is None:
            rpd = etree.Element(W + "rPrDefault")
            dd.insert(0, rpd)                        # before pPrDefault
        rpr = rpd.find(W + "rPr")
        if rpr is None:
            rpr = etree.SubElement(rpd, W + "rPr")
        set_rfonts(rpr, td["values"], td["clear_theme"])
        pkg.write_xml(td["styles_part"], root)
