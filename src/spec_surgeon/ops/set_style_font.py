"""Op: set_style_font — set w:rFonts in style definitions (styles.xml only).

By default only styles that already define a font are changed (``only_where_defined``): the others inherit
through basedOn → docDefaults, so changing them would add formatting nobody had. Symbol styles (any explicit
symbol font in the style or its basedOn chain) are left alone. Style-definition changes are not tracked —
Word itself does not track them — so ``tracked: true`` is refused.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..package import W, DocxPackage
from ..ooxml import RPR_ORDER, STYLE_ORDER, get_or_add, set_rfonts
from . import register
from .base import JobException, Op, PlanFragment
from .selectors import DEFAULT_PRESERVE_FONTS, FONT_PARAM_DOC, StyleSheet, font_values, is_symbol_style
from .set_font import FONT_REFLOW

STYLE_TYPES = ("paragraph", "character", "table", "numbering")


def select_styles(sheet: StyleSheet, sel, preserve: List[str], only_where_defined: bool) -> List[str]:
    if sel in (None, "all"):
        types, ids = {"paragraph", "character", "table"}, None
    elif sel == "all-paragraph-and-character":
        types, ids = {"paragraph", "character"}, None
    elif isinstance(sel, dict) and sel and set(sel) <= {"ids", "types"}:
        types = set(sel.get("types", STYLE_TYPES))
        if types - set(STYLE_TYPES):
            raise JobException(f"unknown style type(s) {sorted(types - set(STYLE_TYPES))}", "bad-params")
        ids = list(sel["ids"]) if "ids" in sel else None
        if ids is not None:
            missing = [i for i in ids if i not in sheet.styles]
            if missing:
                raise JobException(f"style id(s) {missing} not in this document", "not-found", {"missing": missing})
            ids = set(ids)
    else:
        raise JobException(f"styles selector must be 'all', 'all-paragraph-and-character', {{'ids': [...]}} or "
                           f"{{'types': [...]}}, got {sel!r}", "bad-params")
    out = []
    for sid, st in sheet.styles.items():
        if st.get(W + "type", "paragraph") not in types or (ids is not None and sid not in ids):
            continue
        if is_symbol_style(st, sheet, preserve):
            continue
        rpr = st.find(W + "rPr")
        if only_where_defined and (rpr is None or rpr.find(W + "rFonts") is None):
            continue
        out.append(sid)
    return out


@register
class SetStyleFont(Op):
    name = "set_style_font"
    mode = "formatting"
    description = ("Set the font of style definitions (styles.xml): by default only styles that already define "
                   "a font; symbol styles are left alone.")
    params_schema = {"type": "object", "properties": {
        **FONT_PARAM_DOC,
        "only_where_defined": {"type": "boolean", "default": True,
                               "description": "change only styles that already carry rFonts (others inherit)"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {
        "styles": {"description": "'all' | 'all-paragraph-and-character' | {ids: [...]} | {types: [...]}"}}}
    verification_profile = {"V1": "styles.xml only", "V2": "zero text diffs",
                            "V10": "style_fonts: selected style ids re-derived and matched; only rFonts changed",
                            "V7": "none — a font change reflows the text"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"styles"}:
            raise JobException(f"set_style_font: unknown selector key(s) {sorted(set(select) - {'styles'})}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); "
                               "omit 'tracked' or set it false", "bad-params")
        values = font_values(params)
        preserve = list(params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS))
        clear = bool(params.get("clear_theme_fonts", True))
        only = bool(params.get("only_where_defined", True))
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        sel = select.get("styles", "all")
        ids = select_styles(sheet, sel, preserve, only)
        frag = self.fragment(select, params)
        frag.property_assertions.append({
            "kind": "style_fonts", "styles_part": sheet.part, "select": sel, "preserve_fonts": preserve,
            "only_where_defined": only, "set": values, "clear_theme": clear, "expected_ids": ids})
        frag.transform_data = {"styles_part": sheet.part, "ids": ids, "values": values, "clear_theme": clear}
        if ids:
            frag.intended_parts = [sheet.part]
            frag.touches = [(sheet.part, f"style:{sid}", "rPr") for sid in ids]
            frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        skipped_symbol = [sid for sid, st in sheet.styles.items() if is_symbol_style(st, sheet, preserve)]
        frag.notes.append(f"{len(ids)} style(s) → {values}: {ids}")
        if skipped_symbol:
            frag.notes.append(f"symbol-font styles left alone: {skipped_symbol}")
        if only:
            frag.notes.append("styles without their own rFonts are unchanged (they inherit through basedOn → docDefaults)")
        if pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["ids"]:
            return
        root = pkg.xml(td["styles_part"])
        by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
        for sid in td["ids"]:
            rpr = get_or_add(by_id[sid], "rPr", STYLE_ORDER)
            set_rfonts(rpr, td["values"], td["clear_theme"])
        pkg.write_xml(td["styles_part"], root)
