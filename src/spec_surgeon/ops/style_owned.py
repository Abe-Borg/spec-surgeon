"""Source-derived direct formatting owned by an applied paragraph style.

Only explicit properties in the resulting basedOn chain establish ownership;
docDefaults, linked character styles, and inactive built-in styles do not. The
ordinary clearing rules retain control over glyph, revision and content safety.
Compound font/spacing/indent attributes are replaced separately. Child-based
borders and tab stops are cleared only when every direct side/position is owned.
"""
from __future__ import annotations

import copy

from ..package import W
from .clear_formatting import ClearSettings, paragraph_clearing

INACTIVE = frozenset({"DefaultParagraphFont", "TableNormal", "NoList"})
ATTRIBUTE_GROUPS = {
    "rFonts": (("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme"),
               ("eastAsia", "eastAsiaTheme"), ("cs", "cstheme")),
    "spacing": (("before", "beforeLines", "beforeAutospacing"),
                ("after", "afterLines", "afterAutospacing"), ("line", "lineRule")),
    "ind": (("left", "leftChars", "start", "startChars"),
            ("right", "rightChars", "end", "endChars"),
            ("firstLine", "firstLineChars", "hanging", "hangingChars")),
}


def style_owned_properties(styles, sid):
    """Return explicit rPr/pPr property definitions and the visited style IDs."""
    owned, seen = {"rPr": {}, "pPr": {}}, set()
    while sid and sid not in seen and sid not in INACTIVE:
        seen.add(sid)
        if sid not in styles:
            break
        style = styles[sid]
        for kind in owned:
            container = style.find(W + kind)
            for prop in container if container is not None else []:
                if isinstance(prop.tag, str) and prop.tag.startswith(W):
                    owned[kind].setdefault(prop.tag[len(W):], []).append(prop)
        based = style.find(W + "basedOn")
        sid = based.get(W + "val") if based is not None else None
    return owned, seen


def _replacements(container, names, definitions):
    changed, replacements, retained = [], {}, set()
    for name in names:
        if len(container.findall(W + name)) != 1:
            retained.add(name)
            continue
        direct = container.find(W + name)
        supplied = definitions.get(name, [])
        if not supplied:
            continue
        if name in ATTRIBUTE_GROUPS and not len(direct):
            attrs = {attr for prop in supplied for attr in prop.attrib if attr.startswith(W)}
            for group in ATTRIBUTE_GROUPS[name]:
                group = {W + attr for attr in group}
                if attrs & group:
                    attrs |= group
            removed = set(direct.attrib) & attrs
            if not removed:
                continue
            kept = copy.deepcopy(direct)
            for attr in removed:
                del kept.attrib[attr]
            if kept.attrib or len(kept) or kept.text:
                replacements[name] = kept
        elif len(direct):
            # Never discard an unrelated border side, tab stop, or extension child.
            def key(child):
                return (child.tag, child.get(W + "pos")) if name == "tabs" else child.tag
            covered = {key(child) for prop in supplied for child in prop
                       if isinstance(child.tag, str) and child.tag.startswith(W)}
            if any(key(child) not in covered for child in direct) or direct.attrib:
                retained.add(name)
                continue
        elif any(not attr.startswith(W) for attr in direct.attrib):
            retained.add(name)
            continue
        changed.append(name)
    return changed, replacements, retained


def style_owned_clearing(p, runs, sheet, owned):
    """Narrow the existing safe clear decisions to explicit destination ownership."""
    d = paragraph_clearing(p, runs, sheet, ClearSettings())
    retained, changed_runs = set(), []
    for ri, names in d.runs:
        names, replacement, skipped = _replacements(runs[ri].find(W + "rPr"), names, owned["rPr"])
        retained |= skipped
        if names:
            changed_runs.append((ri, names))
            if replacement:
                d.run_replacements[ri] = replacement
    d.runs = changed_runs
    if d.mark:
        names, d.mark_replacements, skipped = _replacements(p.find(W + "pPr/" + W + "rPr"), d.mark, owned["rPr"])
        d.mark = names or None
        retained |= skipped
    d.ppr, d.ppr_replacements, skipped = _replacements(p.find(W + "pPr"), d.ppr, owned["pPr"])
    return d, retained | skipped
