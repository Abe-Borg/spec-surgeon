"""Independent template outcome checks, including the entire imported package graph.

Permissions returned here describe only changes derived from the original source
and the approved, content-pinned template. No transform plan is consulted.
"""
from __future__ import annotations

import copy
import hashlib
import posixpath
from pathlib import Path
from types import SimpleNamespace

from lxml import etree

from .props import Styles, sect_prs, sig
from .reader import CT, R, REL, STYLES_REL, VPackage, W
from .template import (_refs_shown, _title_pg, check_template_part, fill_value,
                       reference_values_of, slots_of, strip_extensions, _v_field_runs)

BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"


def _rels_name(part):
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def _target(owner, rel):
    if rel.get("TargetMode", "Internal") != "Internal":
        return None
    value = rel.get("Target", "")
    return value.lstrip("/") if value.startswith("/") else posixpath.normpath(
        posixpath.join(posixpath.dirname(owner), value))


def _relationships(pkg, owner):
    member = _rels_name(owner)
    root = pkg.xml(member) if member in pkg.names else etree.Element(REL + "Relationships")
    rows = list(root)
    if any(el.tag != REL + "Relationship" for el in rows):
        raise ValueError("unsupported relationship markup in " + member)
    ids = [el.get("Id") for el in rows]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("missing or duplicate relationship IDs in " + member)
    return root, dict(zip(ids, rows))


def _content_type(pkg, name):
    defaults, overrides = pkg.content_types()
    return overrides.get(name) or defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())


def _even(pkg):
    part = pkg.related(BASE + "settings")
    el = pkg.xml(part).find(W + "evenAndOddHeaders") if part else None
    return el is not None and el.get(W + "val") not in {"0", "false", "off"}


def _removed_graph(src, out, removed_ids):
    """Old H/F parts and images which become unreferenced; never unrelated orphans."""
    main = src.main_part()
    _, rels = _relationships(src, main)
    candidates = {_target(main, rels[rid]) for rid in removed_ids}
    retained = {_target(main, rel) for rid, rel in rels.items() if rid not in removed_ids}
    removed = {part for part in candidates - retained if part in src.names}
    old_parts = set(removed)
    for part in old_parts:
        if _rels_name(part) in src.names:
            removed.add(_rels_name(part))
    possible_images = {target for _, owner, typ, target in src.all_relationships()
                       if owner in old_parts and typ == BASE + "image" and target}
    still_used = {target for _, owner, _, target in out.all_relationships()
                  if owner not in old_parts and target}
    removed.update(part for part in possible_images - still_used if part in src.names)
    return removed


def _check_content_types(src, out, added, removed, expected_types, errors):
    before, after = src.xml("[Content_Types].xml"), out.xml("[Content_Types].xml")
    for root in (before, after):
        overrides = [e.get("PartName") for e in root.findall(CT + "Override")]
        defaults = [e.get("Extension") for e in root.findall(CT + "Default")]
        if len(set(overrides)) != len(overrides) or len(set(defaults)) != len(defaults):
            errors.append("duplicate content-type declarations")
    for name, wanted in expected_types.items():
        if _content_type(out, name) != wanted:
            errors.append("wrong imported content type: " + name)
    for root, names in ((before, removed), (after, added)):
        for el in list(root.findall(CT + "Override")):
            if el.get("PartName", "").lstrip("/") in names:
                if set(el.attrib) != {"PartName", "ContentType"} or len(el):
                    errors.append("unsupported imported content-type markup")
                root.remove(el)
    if sig(before) != sig(after):
        errors.append("unapproved content-type changes")


def verify_template_effect(src: VPackage, out: VPackage, effect: dict) -> dict:
    result = {"errors": [], "allowed_added": [], "allowed_removed": [],
              "allowed_changed": [], "section_masks": {}, "style_ids": []}
    errors = result["errors"]
    try:
        data = Path(effect["template"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != effect["template_sha256"]:
            raise ValueError("approved template changed")
        tpl = VPackage(data)
        try:
            _check_template(src, out, tpl, data, effect, result)
        finally:
            tpl.zip.close()
    except (ValueError, KeyError, TypeError, IndexError, OSError, etree.Error) as exc:
        errors.append("template outcome: " + str(exc))
    return result


def _check_template(src, out, tpl, data, effect, result):
    errors = result["errors"]
    params = effect["params"]
    kinds = params.get("kinds", ["header", "footer"])
    main = src.main_part()
    if out.main_part() != main:
        raise ValueError("main document relationship changed")
    sroot, oroot = src.xml(main), out.xml(main)
    source_sections, output_sections = sect_prs(sroot), sect_prs(oroot)
    template_sections = sect_prs(tpl.xml(tpl.main_part()))
    if len(source_sections) != len(output_sections):
        raise ValueError("section count changed")
    if _even(src) != _even(tpl) or _even(src) != _even(out):
        raise ValueError("even/odd header setting differs")
    mapping = params["template_sections"]
    indexes = [mapping["all"] if "all" in mapping else mapping["cover" if i == 0 else "body"]
               for i in range(len(source_sections))]
    shown = _refs_shown(tpl)
    smain_rels, sr = _relationships(src, main)
    omain_rels, orels = _relationships(out, main)
    selected_ids, retained_ids, new_ids = set(), set(), set()
    copies = {}
    for i, (ss, os) in enumerate(zip(source_sections, output_sections)):
        ti = indexes[i]
        ts = template_sections[ti]
        wanted = {(kind, typ): shown[ti].get((kind, typ))
                  for kind in kinds for typ in ("default", "first", "even")
                  if (typ != "first" or _title_pg(ts)) and (typ != "even" or _even(tpl))}
        wanted = {key: part for key, part in wanted.items() if part}
        actual = {}
        for el in ss:
            if el.tag in {W + kind + "Reference" for kind in kinds}:
                selected_ids.add(el.get(R + "id"))
            elif el.tag in {W + "headerReference", W + "footerReference"}:
                retained_ids.add(el.get(R + "id"))
        for el in os:
            if el.tag not in {W + kind + "Reference" for kind in kinds}:
                continue
            kind = el.tag[len(W):-len("Reference")]
            key = (kind, el.get(W + "type", "default"))
            rid = el.get(R + "id")
            if (key in actual or set(el.attrib) - {W + "type", R + "id"} or len(el)
                    or rid not in orels):
                raise ValueError("invalid or duplicate output header/footer reference")
            rel = orels[rid]
            part = _target(main, rel)
            if rel.get("Type") != BASE + kind or part in src.names or part not in out.names:
                raise ValueError("output reference is not a new part of the approved kind")
            if set(rel.attrib) != {"Id", "Type", "Target"} or len(rel) or rid in sr:
                raise ValueError("unapproved output header/footer relationship")
            actual[key] = part
            new_ids.add(rid)
        if set(actual) != set(wanted):
            raise ValueError("output header/footer types differ from approved template")
        for key, part in actual.items():
            value = copies.setdefault(part, {"template_part": wanted[key], "template_section": ti,
                                             "target_sections": []})
            if value["template_part"] != wanted[key]:
                raise ValueError("one output part substitutes for different template parts")
            if i not in value["target_sections"]:
                value["target_sections"].append(i)
        masks = {kind + "Reference": None for kind in kinds}
        expected_title = copy.deepcopy(ss.find(W + "titlePg"))
        if params.get("align_title_page", True):
            if _title_pg(ss) != _title_pg(ts):
                if _title_pg(ts):
                    if expected_title is None:
                        expected_title = etree.Element(W + "titlePg")
                    expected_title.attrib.pop(W + "val", None)
                else:
                    expected_title = None
            if len(os.findall(W + "titlePg")) > 1 or sig(expected_title) != sig(os.find(W + "titlePg")):
                errors.append(f"section {i}: title-page behavior differs from template contract")
            masks["titlePg"] = None
        elif _title_pg(ss) != _title_pg(ts):
            errors.append(f"section {i}: template needs explicitly approved title-page alignment")
        if params.get("align_header_footer_distance", True):
            sm, om, tm = ss.find(W + "pgMar"), os.find(W + "pgMar"), ts.find(W + "pgMar")
            attrs = []
            for attr in ("header", "footer"):
                original = sm.get(W + attr) if sm is not None else None
                expected = tm.get(W + attr) if tm is not None else None
                expected = expected or original
                actual_value = om.get(W + attr) if om is not None else None
                if actual_value != expected:
                    errors.append(f"section {i}: {attr} distance differs from approved template")
                attrs.append(attr)
            masks["pgMar"] = attrs
        result["section_masks"][i] = masks
    # A relationship still used outside selected section references cannot be removed.
    selected_tags = {W + kind + "Reference" for kind in kinds}
    for el in sroot.iter():
        if el.tag not in selected_tags:
            retained_ids.update(value for key, value in el.attrib.items() if key.startswith(R))
    removed_ids = selected_ids - retained_ids
    if not removed_ids <= set(sr):
        raise ValueError("source header/footer relationship is unresolved")
    removed = _removed_graph(src, out, removed_ids)
    for root, ids in ((smain_rels, removed_ids), (omain_rels, new_ids)):
        for rel in list(root):
            if rel.get("Id") in ids:
                root.remove(rel)
    if sig(smain_rels) != sig(omain_rels):
        errors.append("unapproved document relationship changes")
    added, expected_types, used_styles = set(copies), {}, set()
    fills = params["slot_fills"]
    expected_fills = {name: fill_value(src, name, rule) for name, rule in fills.items()}
    markers = params.get("slot_markers", ["highlight", "braces"])
    reference_values = reference_values_of(tpl, fills) if markers == ["reference"] else None
    used_slots = set()
    ctx = SimpleNamespace(src=src, out=out, out_tp=out.text_parts())
    for part, descriptor in copies.items():
        tpart = descriptor["template_part"]
        assertion = dict(descriptor, part=part, template=effect["template"],
                         template_md5=hashlib.md5(data).hexdigest(), fills=fills,
                         expected_fills=expected_fills,
                         rescale_tabs=params.get("rescale_tabs", True),
                         slot_markers=params.get("slot_markers", ["highlight", "braces"]),
                         highlight_colors=params.get("highlight_colors", ["yellow"]))
        if assertion["slot_markers"] == ["reference"]:
            assertion["template_values"] = reference_values_of(tpl, fills)
        check_template_part(ctx, assertion, errors)
        expected_types[part] = _content_type(tpl, tpart)
        troot = tpl.xml(tpart)
        field_runs = _v_field_runs(troot)
        for paragraph in troot.iter(W + "p"):
            used_slots.update(slot["name"] for slot in slots_of(paragraph, markers,
                params.get("highlight_colors", ["yellow"]), reference_values, field_runs))
        for el in troot.iter():
            if el.tag in {W + "pStyle", W + "rStyle", W + "tblStyle"}:
                used_styles.add(el.get(W + "val"))
        trroot, tr = _relationships(tpl, tpart)
        orrroot, orr = _relationships(out, part)
        if set(tr) != set(orr):
            errors.append("imported relationship IDs differ: " + part)
        for rid in set(tr) & set(orr):
            trel, orel = tr[rid], orr[rid]
            tc, oc = copy.deepcopy(trel), copy.deepcopy(orel)
            ttarget, otarget = _target(tpart, trel), _target(part, orel)
            if trel.get("Type") == BASE + "image" and ttarget is not None:
                if otarget is None or otarget not in out.names or out.read(otarget) != tpl.read(ttarget):
                    errors.append("imported image bytes differ: " + str(otarget))
                else:
                    if otarget not in src.names:
                        added.add(otarget)
                    expected_types[otarget] = _content_type(tpl, ttarget)
                tc.attrib.pop("Target", None)
                oc.attrib.pop("Target", None)
            elif not (trel.get("Type") == BASE + "hyperlink" and ttarget is None):
                errors.append("unsupported template relationship: " + str(trel.get("Type")))
            if sig(tc) != sig(oc):
                errors.append("imported relationship changed: " + part + " " + rid)
        for root in (trroot, orrroot):
            for child in list(root):
                root.remove(child)
        if sig(trroot) != sig(orrroot):
            errors.append("imported relationship root differs: " + part)
        trname, orname = _rels_name(tpart), _rels_name(part)
        if (trname in tpl.names) != (orname in out.names):
            errors.append("imported relationship member presence differs: " + part)
        if orname in out.names:
            added.add(orname)
            expected_types[orname] = _content_type(tpl, trname)
    if set(fills) != used_slots:
        errors.append("template fill names differ from selected slots: unused=" + str(sorted(set(fills) - used_slots))
                      + ", missing=" + str(sorted(used_slots - set(fills))))
    # Import only the missing style closure used by these template parts.
    sstyle, ostyle, tstyle = (pkg.related(STYLES_REL) for pkg in (src, out, tpl))
    if sstyle != ostyle:
        raise ValueError("styles relationship changed")
    ss = Styles(src.xml(sstyle) if sstyle else None)
    ts = Styles(tpl.xml(tstyle) if tstyle else None)
    pending = list(used_styles)
    while pending:
        sid = pending.pop()
        el = ts.by_id.get(sid)
        if el is None:
            continue
        for tag in ("basedOn", "link", "next"):
            link = el.find(W + tag)
            value = link.get(W + "val") if link is not None else None
            if value and value not in used_styles:
                used_styles.add(value)
                pending.append(value)
    missing = used_styles - set(ss.by_id) if params.get("import_missing_styles", True) else set()
    if missing and not sstyle:
        raise ValueError("template requires an absent source styles part")
    # Filling a formerly dangling style reference can change the original body
    # even when its XML is untouched. Such an effect needs a separate authority.
    style_refs = {W + key for key in ("pStyle", "rStyle", "tblStyle", "basedOn", "link", "next",
                                     "numStyleLink", "styleLink")}
    numbering = src.related(BASE + "numbering")
    original_parts = src.text_parts() + [part for part in (sstyle, numbering) if part]
    for part in set(original_parts) - removed:
        for el in src.xml(part).iter():
            if el.tag in style_refs and el.get(W + "val") in missing:
                errors.append("imported style changes an original style reference: " + part)
    for sid in missing:
        definition = ts.by_id.get(sid)
        if definition is not None and (definition.get(W + "default") not in (None, "0", "false", "off")
                                       or definition.get(W + "type") == "numbering"
                                       or next(definition.iter(W + "numPr"), None) is not None):
            errors.append("imported style changes defaults or depends on unapproved numbering: " + sid)
    if sstyle:
        before, after = src.xml(sstyle), out.xml(ostyle)
        os = Styles(after)
        for sid in missing:
            if sum(el.get(W + "styleId") == sid for el in after.findall(W + "style")) != 1:
                errors.append("imported style ID must occur exactly once: " + str(sid))
        if set(os.by_id) != set(ss.by_id) | missing:
            errors.append("imported style set differs from approved template closure")
        for sid in missing:
            if sid not in ts.by_id or sig(os.by_id.get(sid)) != sig(strip_extensions(ts.by_id[sid])):
                errors.append("imported style differs: " + str(sid))
        for el in list(after.findall(W + "style")):
            if el.get(W + "styleId") in missing:
                after.remove(el)
        if sig(before) != sig(after):
            errors.append("preexisting styles changed during template import")
    _check_content_types(src, out, added, removed, expected_types, errors)
    result["allowed_added"] = sorted(added)
    result["allowed_removed"] = sorted(removed)
    result["allowed_changed"] = ["[Content_Types].xml", _rels_name(main)] + ([sstyle] if missing else [])
    result["style_ids"] = sorted(missing)
