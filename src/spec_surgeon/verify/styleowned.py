"""Independent source/template interpretation of selective paragraph styling.

The transform's property sets, projected XML and style-closure predictions are
never inputs here. Ownership is read from the original destination definitions.
"""
from __future__ import annotations

from copy import deepcopy

from .reader import W
from .clearing import v_cleared_sets, v_decide


def v_style_ownership(source_styles, template_styles, sid, imported):
    """The effective post-import basedOn path, respecting held source ancestors."""
    result = {"rPr": {}, "pPr": {}}
    visited = set()
    first = True
    while sid:
        if sid in visited or sid in {"DefaultParagraphFont", "TableNormal", "NoList"}:
            break
        visited.add(sid)
        if (first and imported) or sid not in source_styles.by_id:
            element = template_styles.by_id.get(sid) if template_styles is not None else None
        else:
            element = source_styles.by_id[sid]
        if element is None:
            break
        for family in ("rPr", "pPr"):
            properties = element.find(W + family)
            if properties is not None:
                for child in properties:
                    if isinstance(child.tag, str) and child.tag.startswith(W):
                        result[family].setdefault(child.tag.removeprefix(W), []).append(child)
        parent = element.find(W + "basedOn")
        sid = parent.get(W + "val") if parent is not None else None
        first = False
    return result


def _owned_attributes(name, definitions):
    attrs = {key for definition in definitions for key in definition.attrib if key.startswith(W)}
    equivalences = {
        "rFonts": ["ascii asciiTheme", "hAnsi hAnsiTheme", "eastAsia eastAsiaTheme", "cs cstheme"],
        "spacing": ["before beforeLines beforeAutospacing", "after afterLines afterAutospacing", "line lineRule"],
        "ind": ["left leftChars start startChars", "right rightChars end endChars",
                "firstLine firstLineChars hanging hangingChars"],
    }
    for words in equivalences[name]:
        alternatives = {W + word for word in words.split()}
        if not attrs.isdisjoint(alternatives):
            attrs.update(alternatives)
    return attrs


def _v_project(properties, candidates, ownership):
    allowed, remaining = set(), {}
    for name in candidates:
        supplied = ownership.get(name)
        if not supplied:
            continue
        if len(properties.findall(W + name)) != 1:
            continue
        source = properties.find(W + name)
        if name in {"rFonts", "spacing", "ind"} and len(source) == 0:
            attrs = _owned_attributes(name, supplied)
            if not any(attr in attrs for attr in source.attrib):
                continue
            projected = deepcopy(source)
            for attr in tuple(projected.attrib):
                if attr in attrs:
                    projected.attrib.pop(attr)
            if len(projected) or projected.attrib or projected.text:
                remaining[name] = projected
        elif len(source):
            if source.attrib:
                continue
            matches = True
            for child in source:
                if not any(child.tag == value.tag and
                           (name != "tabs" or child.get(W + "pos") == value.get(W + "pos"))
                           for definition in supplied for value in definition
                           if isinstance(value.tag, str) and value.tag.startswith(W)):
                    matches = False
                    break
            if not matches:
                continue
        elif any(not attr.startswith(W) for attr in source.attrib):
            continue
        allowed.add(name)
    return allowed, remaining


def v_style_owned_decision(paragraph, runs, source_styles, ownership):
    run_names, para_names = v_cleared_sets()
    decision = v_decide(paragraph, runs, source_styles, run_names, para_names)
    for index in tuple(decision.runs):
        changed, kept = _v_project(runs[index].find(W + "rPr"), decision.runs[index], ownership["rPr"])
        if changed:
            decision.runs[index] = changed
            decision.run_replacements[index] = kept
        else:
            del decision.runs[index]
    if decision.mark:
        changed, kept = _v_project(paragraph.find(W + "pPr/" + W + "rPr"), decision.mark, ownership["rPr"])
        decision.mark = changed or None
        decision.mark_replacements = kept
    decision.ppr, decision.ppr_replacements = _v_project(paragraph.find(W + "pPr"), decision.ppr, ownership["pPr"])
    return decision
