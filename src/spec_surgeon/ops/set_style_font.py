"""Op: set_style_font — set w:rFonts and/or w:sz in styles, plus used label fonts for all-style selections.

By default only styles that already define a font are changed (``only_where_defined``): the others inherit
through basedOn → docDefaults, so changing them would add formatting nobody had. The size works the same way, per
property: a style gets the font when it defines rFonts and the size when it defines w:sz (listed in ``size_from``
when given; with ``size_from`` a style without its own size never changes). With ``only_where_defined: false``
every selected style gets the font, and the size when no ``size_from`` is given. Symbol styles (any explicit
symbol font in the style or its basedOn chain) are left alone. Style-definition changes are not tracked —
Word itself does not track them — so ``tracked: true`` is refused.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..package import W, DocxPackage
from ..ooxml import RPR_ORDER, STYLE_ORDER, get_or_add, own_size, set_rfonts, set_size
from . import register
from .base import JobException, Op, PlanFragment
from .font_numbering import NUMBERING_PARAM, apply_numbering, numbering_enabled, plan_numbering
from .selectors import (DEFAULT_PRESERVE_FONTS, FONT_PARAM_DOC, StyleSheet, font_values, is_symbol_style,
                        size_applies, size_values)
from .set_font import FONT_REFLOW

STYLE_TYPES = ("paragraph", "character", "table", "numbering")


def style_changes(st, values: Dict[str, str], size, only_where_defined: bool):
    """(font changes here, size changes here) for one style definition."""
    rpr = st.find(W + "rPr")
    font = bool(values) and (not only_where_defined or (rpr is not None and rpr.find(W + "rFonts") is not None))
    return font, size_applies(own_size(rpr), size, add_where_missing=not only_where_defined)


def select_styles(sheet: StyleSheet, sel, preserve: List[str], only_where_defined: bool, values: Dict[str, str],
                  size: Optional[Dict[str, Any]]) -> List[str]:
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
        if not any(style_changes(st, values, size, only_where_defined)):
            continue
        out.append(sid)
    return out


@register
class SetStyleFont(Op):
    name = "set_style_font"
    mode = "formatting"
    description = ("Set the font and/or size of style definitions: by default only where a style "
                   "already defines that property; all-style selections also update used list-label fonts. "
                   "Symbol styles and label glyph fonts are left alone.")
    params_schema = {"type": "object", "properties": {
        **FONT_PARAM_DOC,
        **NUMBERING_PARAM,
        "only_where_defined": {"type": "boolean", "default": True,
                               "description": "change a style's font only where it carries rFonts and its size only "
                                              "where it carries w:sz (the others inherit)"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {
        "styles": {"description": "'all' | 'all-paragraph-and-character' | {ids: [...]} | {types: [...]}"}}}
    verification_profile = {"V1": "styles.xml and used list-label fonts when requested", "V2": "zero text diffs",
                            "V10": "style_fonts: selected style ids re-derived and matched; only rFonts / w:sz "
                                   "changed, each where the style gets it; numbering_fonts proves label changes whole",
                            "V7": "none — a font change reflows the text"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"styles"}:
            raise JobException(f"set_style_font: unknown selector key(s) {sorted(set(select) - {'styles'})}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); "
                               "omit 'tracked' or set it false", "bad-params")
        values = font_values(params)
        size = size_values(params)
        preserve = list(params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS))
        clear = bool(params.get("clear_theme_fonts", True))
        only = bool(params.get("only_where_defined", True))
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        sel = select.get("styles", "all")
        ids = select_styles(sheet, sel, preserve, only, values, size)
        changes = {sid: style_changes(sheet.styles[sid], values, size, only) for sid in ids}
        frag = self.fragment(select, params)
        frag.property_assertions.append({
            "kind": "style_fonts", "font_params": dict(params),
            "styles_part": sheet.part, "select": sel, "preserve_fonts": preserve,
            "only_where_defined": only, "set": values, "clear_theme": clear, "expected_ids": ids, "size": size,
            "expected_sized": sorted(sid for sid, (_, z) in changes.items() if z)})
        frag.transform_data = {"styles_part": sheet.part, "ids": ids, "values": values, "clear_theme": clear,
                               "size": size, "changes": {sid: list(c) for sid, c in changes.items()}}
        if ids:
            frag.intended_parts = [sheet.part]
            frag.touches = [(sheet.part, f"style:{sid}", "rPr") for sid in ids]
            frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        if not (isinstance(sel, dict) and "ids" in sel):
            # "all" or by type: the set of styles is read from the source (rule 9, batch 4) — a style an import adds, or
            # a definition it replaces, would keep its own font beside the ones this op changed
            frag.touches += [(sheet.part, "*", "verify:add"), (sheet.part, "*", "verify:replace")]
        skipped_symbol = [sid for sid, st in sheet.styles.items() if is_symbol_style(st, sheet, preserve)]
        if values:
            frag.notes.append(f"{sum(f for f, _ in changes.values())} style(s) → {values}: "
                              f"{[sid for sid, (f, _) in changes.items() if f]}")
        if size:
            frag.notes.append(f"{sum(z for _, z in changes.values())} style(s) → {size['sz'] / 2:g} pt"
                              + (f" (those set to {', '.join(f'{h / 2:g}' for h in size['from'])} pt)"
                                 if size["from"] else "") + f": {[sid for sid, (_, z) in changes.items() if z]}")
        if skipped_symbol:
            frag.notes.append(f"symbol-font styles left alone: {skipped_symbol}")
        if only:
            frag.notes.append("styles without their own " + " / ".join(x for x, on in (("rFonts", values),
                                                                                        ("w:sz", size)) if on)
                              + " keep inheriting it through basedOn → docDefaults")
        if pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        plan_numbering(pkg, frag, sheet, values, preserve, clear,
                       enabled=numbering_enabled(select, params, style=True))
        if frag.transform_data.get("numbering_fonts", {}).get("targets"):
            frag.geometry_reference, frag.geometry_reason = "none", FONT_REFLOW
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td["ids"]:
            root = pkg.xml(td["styles_part"])
            by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
            for sid in td["ids"]:
                font, size = td["changes"][sid]
                rpr = get_or_add(by_id[sid], "rPr", STYLE_ORDER)
                if font:
                    set_rfonts(rpr, td["values"], td["clear_theme"])
                if size:
                    set_size(rpr, td["size"]["sz"])
            pkg.write_xml(td["styles_part"], root)
        apply_numbering(pkg, frag)
