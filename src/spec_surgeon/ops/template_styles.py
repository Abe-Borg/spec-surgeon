"""Reconcile template style identities without changing the destination's styles.

Word localizes style IDs, but built-in names and default roles survive localization.
Use those identities, including linked character styles, before importing a dependency.
Unrelated ID/name collisions get private copies; dangling destination references are
reserved too, since filling one could change the original body without editing its XML.
The independent implementation is verify/template_styles.py.
"""
from __future__ import annotations

from ..package import W, REL_TYPE_BASE
from .base import JobException
from .selectors import StyleSheet

REFS = {W + tag for tag in ("pStyle", "rStyle", "tblStyle", "basedOn", "link", "next",
                            "numStyleLink", "styleLink")}
TRUE = {"1", "true", "on"}


def remap_references(root, mapping):
    for el in root.iter():
        if el.tag in REFS and el.get(W + "val") in mapping:
            el.set(W + "val", mapping[el.get(W + "val")])


def reference_parts(pkg, sheet):
    numbering = [r.resolved for r in pkg.rels(pkg.main_part()).values()
                 if r.type == REL_TYPE_BASE + "numbering" and r.resolved and pkg.has(r.resolved)]
    return list(dict.fromkeys(pkg.text_parts() + ([sheet.part] if sheet.part else []) + numbering))


def reconcile_styles(pkg, template, parts, import_missing, strip):
    source, target = StyleSheet(template), StyleSheet(pkg)
    kind = lambda s: s.get(W + "type", "paragraph")

    def name(s):
        el = s.find(W + "name")
        return " ".join((el.get(W + "val", "") if el is not None else "").split()).casefold()

    for sheet in (source, target):
        if sheet.root is not None:
            ids = [s.get(W + "styleId") for s in sheet.root.findall(W + "style")]
            if None in ids or len(ids) != len(set(ids)):
                raise JobException("missing or duplicate style IDs in template or destination", "structure")
    identities, defaults, names = {}, {}, {}
    for sid, style in target.styles.items():
        names.setdefault((kind(style), name(style)), []).append(sid)
        if style.get(W + "default") in TRUE:
            defaults.setdefault(kind(style), []).append(sid)
    for sid, style in source.styles.items():
        if style.get(W + "default") in TRUE:
            candidates = defaults.get(kind(style), [])
        else:
            candidates = names.get((kind(style), name(style)), []) if name(style) else []
            same = target.styles.get(sid)
            if same is not None and kind(same) == kind(style) and name(same) == name(style):
                candidates = [sid]
        if len(candidates) == 1:
            identities[sid] = candidates[0]
    # Localized character-style names are often custom names. Their reciprocal
    # link to an identified paragraph style supplies the identity instead.
    for sid, style in source.styles.items():
        link = style.find(W + "link")
        partner = identities.get(link.get(W + "val")) if link is not None else None
        if sid in identities or partner is None or style.get(W + "default") in TRUE:
            continue
        linked = target.styles[partner].find(W + "link")
        candidate = linked.get(W + "val") if linked is not None else None
        match = target.styles.get(candidate)
        back = match.find(W + "link") if match is not None else None
        if (match is not None and kind(match) == kind(style) and back is not None
                and back.get(W + "val") == partner):
            identities[sid] = candidate

    roots = {el.get(W + "val") for part in parts for el in template.xml(part).iter()
             if el.tag in {W + "pStyle", W + "rStyle", W + "tblStyle"} and el.get(W + "val")}
    reserved = set(target.styles)
    for part in reference_parts(pkg, target):
        reserved.update(el.get(W + "val") for el in pkg.xml(part).iter() if el.tag in REFS)
    allocated = reserved | set(source.styles)
    used_names = {name(s) for s in target.styles.values()}
    mapping, imports = {}, {}

    def visit(sid):
        if sid in mapping:
            return
        style = source.styles.get(sid)
        if style is None:
            raise JobException(f"template references undefined style {sid!r}", "missing-style")
        if style.get(W + "default") in TRUE and len(defaults.get(kind(style), [])) > 1:
            raise JobException(f"destination has multiple default {kind(style)} styles", "structure")
        if sid in identities:
            mapping[sid] = identities[sid]
            return
        if not import_missing:
            raise JobException(f"template style {sid!r} has no equivalent destination style; enable missing-style import",
                               "missing-style", {"styles": [sid]})
        if target.part is None:
            raise JobException("document has no styles part to import into", "structure")
        if kind(style) == "numbering" or style.find(f".//{W}numPr") is not None:
            raise JobException(f"template style {sid} carries numbering; not handled", "structure")
        dest = sid
        if dest in reserved:
            base, index = "Template" + sid, 1
            dest = base
            while dest in allocated:
                index += 1
                dest = base + str(index)
        allocated.add(dest)
        mapping[sid] = dest                    # assign before walking cycles
        clone = strip(style, f"template style {sid}")
        clone.set(W + "styleId", dest)
        if clone.get(W + "default") in TRUE:
            clone.attrib.pop(W + "default")   # never introduce a document-wide default
        label = clone.find(W + "name")
        if label is not None:
            original, index = label.get(W + "val", sid), 1
            while name(clone) in used_names:
                label.set(W + "val", original + " (Template" + (str(index) if index > 1 else "") + ")")
                index += 1
            used_names.add(name(clone))
        imports[sid] = clone
        for tag in ("basedOn", "link", "next"):
            link = style.find(W + tag)
            if link is not None and link.get(W + "val"):
                visit(link.get(W + "val"))

    for sid in sorted(roots):
        visit(sid)
    result = {}
    for sid in source.styles:                  # retain template definition order
        if sid in imports:
            clone = imports[sid]
            remap_references(clone, mapping)
            result[mapping[sid]] = clone
    return mapping, result
