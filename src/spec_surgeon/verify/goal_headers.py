"""Independent source-derived member graph for approved header/footer removal."""
from __future__ import annotations

import copy
import posixpath

from .props import first_difference, sect_prs, sig
from .reader import CT, R, REL, VPackage, W


REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
MEDIA = {REL_BASE + kind for kind in ("image", "audio", "video", "media")} | {
    "http://schemas.microsoft.com/office/2007/relationships/hdphoto",
    "http://schemas.microsoft.com/office/2007/relationships/media"}


def _rels_path(part):
    directory, basename = posixpath.split(part)
    return posixpath.join(directory, "_rels", basename + ".rels")


def _refs(section):
    return [(element.tag[len(W):-len("Reference")], element.get(W + "type", "default"),
             element.get(R + "id"), element) for element in section
            if element.tag in (W + "headerReference", W + "footerReference")]


def verify_header_removal(src: VPackage, out: VPackage, effect: dict) -> dict:
    errors, changed, masks = [], set(), {}
    main = src.main_part()
    source_root, output_root = src.xml(main), out.xml(main)
    source_sections, output_sections = sect_prs(source_root), sect_prs(output_root)
    selected = list(range(len(source_sections))) if effect.get("sections", "all") == "all" else effect["sections"]
    kinds, types = set(effect.get("kinds", ["header", "footer"])), set(effect.get("types", ["default", "first", "even"]))
    if len(source_sections) != len(output_sections) or not selected or any(i >= len(source_sections) for i in selected):
        raise ValueError("header/footer removal has missing or changed sections")
    dropped, kept_ids = [], set()
    for index, (before, after) in enumerate(zip(source_sections, output_sections)):
        expected = []
        for kind, typ, rid, reference in _refs(before):
            if index in selected and kind in kinds and typ in types:
                dropped.append((kind, rid))
            else:
                expected.append(sig(reference))
                kept_ids.add(rid)
        if [sig(reference) for _, _, _, reference in _refs(after)] != expected:
            errors.append(f"section {index}: header/footer references differ from the approved removal")
        if index in selected:
            masks[index] = {kind + "Reference": None for kind in kinds}
    relationships = src.rels_targets(main)

    def shown(drop):
        previous, sections = {}, []
        for index, section in enumerate(source_sections):
            current = dict(previous)
            for kind, typ, rid, _ in _refs(section):
                if drop and index in selected and kind in kinds and typ in types:
                    continue
                current[kind, typ] = relationships.get(rid, {}).get("target")
            sections.append(current)
            previous = current
        return sections

    before_shown, after_shown = shown(False), shown(True)
    settings = src.related(REL_BASE + "settings")
    even = src.xml(settings).find(W + "evenAndOddHeaders") if settings else None
    even_on = even is not None and even.get(W + "val") not in ("0", "false", "off")
    for index, section in enumerate(source_sections):
        title = section.find(W + "titlePg")
        first_on = title is not None and title.get(W + "val") not in ("0", "false", "off")
        for kind in ("header", "footer"):
            for typ in ("default", "first", "even"):
                visible = typ == "default" or (typ == "first" and first_on) or (typ == "even" and even_on)
                if not visible:
                    continue
                before, after = before_shown[index].get((kind, typ)), after_shown[index].get((kind, typ))
                if index in selected and kind in kinds and typ in types and after is not None:
                    errors.append(f"section {index}: selected {typ} {kind} survives through inheritance")
                if index not in selected and before != after:
                    errors.append(f"section {index}: removal changes an unselected inherited {typ} {kind}")
    for element in source_root.iter():
        if element.tag not in (W + "headerReference", W + "footerReference"):
            kept_ids.update(value for key, value in element.attrib.items() if key.startswith(R))
    gone_ids = {rid for _, rid in dropped} - kept_ids
    still_targeted = {rel["target"] for rid, rel in relationships.items() if rid not in gone_ids}
    parts_gone = {relationships[rid]["target"] for rid in gone_ids
                  if rid in relationships and relationships[rid]["target"] not in still_targeted
                  and relationships[rid]["target"] in src.names}
    for rid in gone_ids:
        if rid not in relationships or relationships[rid]["type"] not in (REL_BASE + "header", REL_BASE + "footer"):
            errors.append("a selected header/footer reference has no matching internal header/footer relationship")
    removed = set(parts_gone)
    for part in parts_gone:
        rel_path = _rels_path(part)
        if rel_path in src.names:
            removed.add(rel_path)
            for relationship in src.xml(rel_path).findall(REL + "Relationship"):
                typ = relationship.get("Type")
                if relationship.get("TargetMode", "Internal") == "External" and typ == REL_BASE + "hyperlink":
                    continue
                if typ not in MEDIA:
                    errors.append(f"removed header/footer {part}: unsupported dependent relationship {typ}")
    remaining_targets = {target for _, owner, _, target in src.all_relationships()
                         if owner not in parts_gone and owner != main and target is not None}
    remaining_targets.update(still_targeted)
    for part in parts_gone:
        for relationship in src.rels_targets(part).values():
            target = relationship["target"]
            if relationship["type"] in MEDIA and target not in remaining_targets and target in src.names:
                removed.add(target)
    # A source relationship outside the selected graph is never permission to
    # remove its target, even when the source had unusual relationship sharing.
    if parts_gone & remaining_targets:
        errors.append("selected header/footer parts remain referenced outside the removed graph")
    main_rels = _rels_path(main)
    if main_rels in src.names:
        expected = src.xml(main_rels)
        for relationship in list(expected.findall(REL + "Relationship")):
            if relationship.get("Id") in gone_ids:
                expected.remove(relationship)
        if main_rels not in out.names:
            errors.append("main relationships member disappeared")
        else:
            difference = first_difference(expected, out.xml(main_rels))
            if difference:
                errors.append("main relationship graph changed beyond selected removal: " + difference)
        if gone_ids:
            changed.add(main_rels)
    content_types = "[Content_Types].xml"
    expected_types = src.xml(content_types)
    original_types = copy.deepcopy(expected_types)
    for override in list(expected_types.findall(CT + "Override")):
        if override.get("PartName", "").lstrip("/") in removed:
            expected_types.remove(override)
    difference = first_difference(expected_types, out.xml(content_types))
    if difference:
        errors.append("content types changed beyond removed members: " + difference)
    if sig(expected_types) != sig(original_types):
        changed.add(content_types)
    return {"errors": errors, "allowed_added": [], "allowed_removed": sorted(removed),
            "allowed_changed": sorted(changed), "section_masks": masks, "style_ids": []}
