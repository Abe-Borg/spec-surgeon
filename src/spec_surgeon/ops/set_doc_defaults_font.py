"""Op: set_doc_defaults_font — set w:docDefaults/w:rPrDefault/w:rPr/w:rFonts and/or w:sz (the bottom of every
run's font and size inheritance chain), creating the elements in schema order when absent. Theme attributes on the
slots set are cleared by default (Word lets them override the explicit name). The size is set unless ``size_from``
is given and docDefaults' own size (10 pt when it sets none) is not listed."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import RPR_ORDER, own_size, set_rfonts, set_size
from . import prose, register
from .base import JobException, Op, PlanFragment
from .selectors import DEFAULT_PRESERVE_FONTS, FONT_PARAM_DOC, StyleSheet, font_values, size_applies, size_values
from .set_font import FONT_REFLOW, font_phrase, two_sentences

DEFAULT_SIZE = 20                                    # half-points a run gets when no level sets w:sz (10 pt)


@register
class SetDocDefaultsFont(Op):
    name = "set_doc_defaults_font"
    mode = "formatting"
    description = ("Set the document-default font and/or size (docDefaults/rPrDefault) that every style "
                   "ultimately inherits.")
    params_schema = {"type": "object", "properties": {
        **{k: v for k, v in FONT_PARAM_DOC.items() if k != "preserve_fonts"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "description": "none"}
    verification_profile = {"V1": "styles.xml only", "V2": "zero text diffs",
                            "V10": "doc_defaults_fonts: only rFonts / w:sz of rPrDefault changed",
                            "V7": "none — a font change reflows the text"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        font = font_phrase(params)                       # "the font to “Arial”" / "the font of … to …"
        what: List[str] = []
        if font:
            what.append("the document's default " + font[len("the "):])
        if params.get("size") is not None:
            text = ("its" if font else "the document's") + f" default size to {prose.points(params['size'])}"
            frm = params.get("size_from")
            if isinstance(frm, (list, tuple)) and frm:
                text += " (if the current default is " + prose.join((prose.points(x) for x in frm), "or") + ")"
            what.append(text)
        first = prose.sentence("set", prose.join(what) if what else "the document's default font and size")
        extras: List[str] = []
        if what:
            props = [w for w, on in (("font", bool(font)), ("size", params.get("size") is not None)) if on]
            extras.append("styles and text that set their own " + prose.join(props, "or") + " keep it")
        if font and params.get("clear_theme_fonts") is False:
            extras.append("theme font settings are kept")
        return two_sentences(first, extras)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("set_doc_defaults_font takes no selector", "bad-params")
        if params.get("tracked"):
            raise JobException("docDefaults changes are not tracked; omit 'tracked' or set it false", "bad-params")
        values = font_values({**params, "preserve_fonts": DEFAULT_PRESERVE_FONTS})
        size = size_values(params)
        clear = bool(params.get("clear_theme_fonts", True))
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        rpr = sheet.root.find(W + "docDefaults/" + W + "rPrDefault/" + W + "rPr")
        own = own_size(rpr)
        sized = size_applies(own if own is not None else DEFAULT_SIZE, size)
        frag = self.fragment(select, params)
        frag.property_assertions.append({"kind": "doc_defaults_fonts", "font_params": dict(params),
                                         "styles_part": sheet.part, "set": values,
                                         "clear_theme": clear, "size": size, "expected_sized": sized})
        frag.transform_data = {"styles_part": sheet.part, "values": values, "clear_theme": clear,
                               "size": size["sz"] if sized else None}
        if values or sized:
            frag.intended_parts = [sheet.part]
            frag.touches = [(sheet.part, "docDefaults", "rPr")]
            frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        if values:
            rf = rpr.find(W + "rFonts") if rpr is not None else None
            before = {etree.QName(k).localname: v for k, v in rf.attrib.items()} if rf is not None else {}
            frag.notes.append(f"docDefaults rFonts {before or '(none)'} → {values}"
                              + (" (theme attributes on these slots cleared)" if clear else ""))
        if size:
            now = f"{(own if own is not None else DEFAULT_SIZE) / 2:g} pt" + ("" if own is not None else " (none set)")
            frag.notes.append(f"docDefaults size {now} → {size['sz'] / 2:g} pt" if sized else
                              f"docDefaults size {now} is not one of {[h / 2 for h in size['from']]} pt: unchanged")
        if rpr is None and (values or sized):
            frag.notes.append("docDefaults/rPrDefault/rPr did not exist; created in schema order")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["values"] and td.get("size") is None:
            return
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
        if td["values"]:
            set_rfonts(rpr, td["values"], td["clear_theme"])
        if td.get("size") is not None:
            set_size(rpr, td["size"])
        pkg.write_xml(td["styles_part"], root)
