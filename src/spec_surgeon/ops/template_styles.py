"""Reconcile template style identities without changing the destination's styles.

Word localizes style IDs, but built-in names and default roles survive localization.
Use those identities, including linked character styles, before importing a dependency.
Unrelated ID/name collisions get private copies; dangling destination references are
reserved too, since filling one could change the original body without editing its XML.
The independent implementation is verify/template_styles.py.
"""
from __future__ import annotations

from copy import deepcopy

from lxml import etree

from ..package import W, REL_TYPE_BASE
from ..ooxml import paragraph_ppr, run_rpr, mark_rpr
from .base import JobException
from .selectors import StyleSheet

REFS = {W + tag for tag in ("pStyle", "rStyle", "tblStyle", "basedOn", "link", "next",
                            "numStyleLink", "styleLink")}
TRUE = {"1", "true", "on"}
FORMAT_CONTAINERS = {W + key for key in ("pPr", "rPr", "tblPr", "trPr", "tcPr", "tblStylePr")}


def _formatting_signature(styles, sid, pinnable):
    """Conservative compatibility: preserve every inherited property not pinned by the op.

    Keep inheritance layers rather than flattening unfamiliar OOXML properties or
    toggle semantics. Tables and nested paragraphs cannot rely on the regular pins.
    """
    def signature(node):
        return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(),
                tuple(signature(child) for child in node))

    chain, seen = [], set()
    while sid:
        if sid in seen or sid not in styles:
            return None  # A broken destination chain is not a reusable equivalent.
        seen.add(sid)
        style = styles[sid]
        layer = []
        for prop in style:
            if prop.tag not in FORMAT_CONTAINERS:
                continue
            prop = deepcopy(prop)
            if pinnable and prop.tag in (W + "pPr", W + "rPr"):
                ignored = ({"jc", "tabs"} if prop.tag == W + "pPr" else
                           {"sz", "color", "b", "i", "caps", "smallCaps"})
                attributes = ({"spacing": ("before", "after", "line", "lineRule"),
                               "ind": ("left", "right", "start", "end", "firstLine", "hanging")}
                              if prop.tag == W + "pPr" else
                              {"rFonts": ("ascii", "hAnsi", "asciiTheme", "hAnsiTheme")})
                for child in list(prop):
                    if child.tag in {W + key for key in ignored}:
                        prop.remove(child)
                    elif child.tag in {W + key for key in attributes}:
                        for key in attributes[child.tag[len(W):]]:
                            child.attrib.pop(W + key, None)
                        if not child.attrib and len(child) == 0 and not child.text:
                            prop.remove(child)
            if prop.attrib or len(prop) or (prop.text or "").strip():
                layer.append(signature(prop))
        if layer:
            chain.append(tuple(layer))
        base = style.find(W + "basedOn")
        sid = base.get(W + "val") if base is not None else None
    return tuple(reversed(chain))


def _style_uses(root, defaults):
    for node in root.iter():
        nested = len(list(node.iterancestors(W + "p")))
        if node.tag in {W + "pStyle", W + "rStyle", W + "tblStyle"} and node.get(W + "val"):
            yield node.get(W + "val"), node.tag == W + "tblStyle" or nested > 1
        for tag, path, kind in (("p", "pPr/pStyle", "paragraph"), ("p", "pPr/rPr/rStyle", "character"),
                                ("r", "rPr/rStyle", "character"), ("tbl", "tblPr/tblStyle", "table")):
            if (node.tag == W + tag and kind in defaults and
                    node.find("/".join(W + part for part in path.split("/"))) is None):
                yield defaults[kind], tag == "tbl" or nested > (0 if tag == "p" else 1)


def _strict_dependencies(uses, styles):
    strict = {sid for sid, full in uses if full}
    pending = list(strict)
    while pending:
        sid = pending.pop()
        style = styles.get(sid)
        base = style.find(W + "basedOn") if style is not None else None
        parent = base.get(W + "val") if base is not None else None
        if parent and parent not in strict:
            strict.add(parent)
            pending.append(parent)
    return strict


def remap_references(root, mapping, implicit_defaults=None):
    for el in root.iter():
        if el.tag in REFS and el.get(W + "val") in mapping:
            el.set(W + "val", mapping[el.get(W + "val")])
    # Imported defaults are intentionally nondefault: explicitly bind copied
    # content that formerly used them implicitly, without changing the body.
    defaults = implicit_defaults or {}
    for p in root.iter(W + "p"):
        if "paragraph" in defaults and p.find(f"{W}pPr/{W}pStyle") is None:
            paragraph_ppr(p).insert(0, etree.Element(W + "pStyle", {W + "val": defaults["paragraph"]}))
        if "character" in defaults and p.find(f"{W}pPr/{W}rPr/{W}rStyle") is None:
            mark_rpr(p).insert(0, etree.Element(W + "rStyle", {W + "val": defaults["character"]}))
    for r in root.iter(W + "r"):
        if "character" in defaults and r.find(f"{W}rPr/{W}rStyle") is None:
            run_rpr(r).insert(0, etree.Element(W + "rStyle", {W + "val": defaults["character"]}))
    for table in root.iter(W + "tbl"):
        if "table" in defaults and table.find(f"{W}tblPr/{W}tblStyle") is None:
            properties = table.find(W + "tblPr")
            if properties is None:
                properties = etree.Element(W + "tblPr")
                table.insert(0, properties)
            properties.insert(0, etree.Element(W + "tblStyle", {W + "val": defaults["table"]}))


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

    uses = [use for part in parts for use in _style_uses(template.xml(part), source.defaults)]
    roots = {sid for sid, _ in uses}
    strict = _strict_dependencies(uses, source.styles)
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
        if (sid in identities and
                (expected_format := _formatting_signature(source.styles, sid, sid not in strict)) is not None and
                expected_format ==
                _formatting_signature(target.styles, identities[sid], sid not in strict)):
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
