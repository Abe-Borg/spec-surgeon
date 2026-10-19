"""Font changes on used list-label definitions, predicted from the original package.

Definitions are shared by every paragraph using a level. Explicit ``numbering_levels: true`` therefore covers
all used levels, even beside a narrower run/style selection. Symbol and picture bullets retain their glyphs.
This changes fonts only; list label sizes and all numbering/counter rules remain untouched.
"""
from __future__ import annotations

from ..ooxml import THEME_FONT_ATTR, get_or_add, set_rfonts
from ..package import W
from .base import JobException
from .lists import ListDefs, paragraph_numbering, whole
from .selectors import is_symbol_rpr, is_targeted

NUMBERING_PARAM = {"numbering_levels": {
    "type": "boolean", "description": "Set fonts on every used list-label level (shared across paragraphs); "
    "default true for all runs of the document or all styles. Symbol/picture bullets and label sizes are preserved."}}
LEVEL_ORDER = ["start", "numFmt", "lvlRestart", "pStyle", "isLgl", "suff", "lvlText", "lvlPicBulletId", "legacy",
               "lvlJc", "pPr", "rPr"]


def numbering_enabled(select, params, style=False):
    if "numbering_levels" in params and not isinstance(params["numbering_levels"], bool):
        raise JobException("'numbering_levels' must be a boolean", "bad-params")
    if style:
        whole = select.get("styles", "all") in (None, "all", "all-paragraph-and-character")
    else:
        scope = select.get("scope", ["document"])
        scope = [scope] if isinstance(scope, str) else scope
        whole = "document" in scope and select.get("runs", "all") in (None, "all", {}) \
            and not is_targeted(select)
    return params.get("numbering_levels", whole)


def level_element(root, target):
    kind, owner, k = target
    parent = next(e for e in root.findall(W + kind)
                  if whole(e.get(W + ("abstractNumId" if kind == "abstractNum" else "numId"))) == owner)
    if kind == "num":
        parent = next(e for e in parent.findall(W + "lvlOverride") if whole(e.get(W + "ilvl")) == k)
    return next(e for e in parent.findall(W + "lvl") if whole(e.get(W + "ilvl")) == k)


def used_levels(pkg, sheet, preserve):
    defs = ListDefs.load(pkg)
    targets = set()
    protected = set()
    if defs.root is None:
        return defs.part, []
    for part in [pkg.main_part()]:
        for bi, p in enumerate(pkg.xml(part).iter(W + "p")):
            nb = paragraph_numbering(p, sheet, defs)
            if nb is None:
                continue
            why = nb.unreadable or ("dangling list reference" if nb.dangling else None)
            problem = defs.problem(nb.num_id)
            if why or problem:
                raise JobException(f"{part} paragraph {bi}: cannot resolve list-label font: "
                                   f"{why or problem[1]}; set numbering_levels: false to leave labels alone",
                                   "numbering-unsupported")
            target = ("num", nb.num_id, nb.ilvl) if defs.is_override(nb.num_id, nb.ilvl) else \
                ("abstractNum", defs.abstract_of(nb.num_id), nb.ilvl)
            # A missing level displays no label. Its definition must remain absent.
            if defs.level(nb.num_id, nb.ilvl) is None:
                continue
            lvl = level_element(defs.root, target)
            if lvl.find(W + "lvlPicBulletId") is not None or \
                    is_symbol_rpr(lvl.find(W + "rPr"), p, sheet, preserve) or \
                    is_symbol_rpr(p.find(W + "pPr/" + W + "rPr"), p, sheet, preserve):
                protected.add(target)
                continue
            targets.add(target)
    return defs.part, [list(t) for t in sorted(targets - protected)]


def plan_numbering(pkg, frag, sheet, values, preserve, clear, *, enabled, tracked=False):
    if not enabled or not values:
        return
    part, targets = used_levels(pkg, sheet, preserve)
    if tracked and targets:
        raise JobException("list-definition fonts cannot be tracked; use tracked: false or numbering_levels: false",
                           "bad-params")
    if not clear and part:
        root = pkg.xml(part)
        for t in targets:
            rf = level_element(root, t).find(W + "rPr/" + W + "rFonts")
            if rf is not None and any(rf.get(W + THEME_FONT_ATTR[s]) for s in values):
                raise JobException("used list labels carry theme font attributes that override the requested font; "
                                   "use clear_theme_fonts: true", "theme-font")
    frag.property_assertions.append({"kind": "numbering_fonts", "numbering_part": part,
                                     "font_params": dict(frag.params), "font_op": frag.op,
                                     "font_select": dict(frag.select),
                                     "expected_levels": targets, "set": values,
                                     "preserve_fonts": preserve, "clear_theme": clear})
    frag.transform_data["numbering_fonts"] = {"part": part, "targets": targets,
                                               "values": values, "clear_theme": clear}
    if part:
        frag.touches.append((part, "*", "verify"))  # V10 compares the WHOLE member, even for an empty selection.
        if targets:
            frag.intended_parts.append(part)
            frag.touches.append((part, "*", "rPr"))
    # Numbering resolution reads paragraph styles and numPr, including inherited numPr. A second font op may
    # change rPr safely, but a restyle or numbering edit must be split into a separate approved stage.
    for text_part in [pkg.main_part()]:
        frag.touches += [(text_part, "*", "verify-props:numPr"), (text_part, "*", "verify-props:pStyle")]
    if sheet.part:
        frag.touches += [(sheet.part, "*", f"verify:{family}")
                         for family in ("numPr", "basedOn", "add", "replace")]
    frag.notes.append(f"{len(targets)} used list-label level(s) → {values}; symbol/picture bullets and label sizes preserved.")


def apply_numbering(pkg, frag):
    td = frag.transform_data.get("numbering_fonts")
    if not td or not td["targets"]:
        return
    root = pkg.xml(td["part"])
    for target in td["targets"]:
        rpr = get_or_add(level_element(root, target), "rPr", LEVEL_ORDER)
        set_rfonts(rpr, td["values"], td["clear_theme"])
    pkg.write_xml(td["part"], root)


def coalesce_numbering_fonts(fragments):
    """An identical shared label-font edit has one apply/verifier owner in a composed font recipe.

    Different values, target sets or theme policy still hit ordinary whole-member conflict detection.
    """
    owners = {}
    for frag in fragments:
        td = frag.transform_data.get("numbering_fonts")
        if td is None:
            continue
        part = td["part"]
        previous = owners.get(part)
        if previous is None:
            owners[part] = td
            continue
        if td != previous:
            continue
        del frag.transform_data["numbering_fonts"]
        frag.property_assertions = [a for a in frag.property_assertions if a.get("kind") != "numbering_fonts"]
        frag.touches = [t for t in frag.touches if t[0] != part]
        frag.intended_parts = [p for p in frag.intended_parts if p != part]
