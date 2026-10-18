"""Independently derive the style identities permitted by a template transplant.

Reads only the original destination and template. Neither output definitions nor a
transform's claimed mapping authorize a match or an imported definition.
"""
from __future__ import annotations

from .reader import STYLES_REL, W

REFERENCE_TAGS = {W + key for key in ("pStyle", "rStyle", "tblStyle", "basedOn", "next", "link",
                                      "numStyleLink", "styleLink")}


def mapped_tree(root, mapping):
    from copy import deepcopy
    result = deepcopy(root)
    for node in result.iter():
        value = node.get(W + "val")
        if node.tag in REFERENCE_TAGS and value in mapping:
            node.set(W + "val", mapping[value])
    return result


def style_plan(source, template, parts, allow_import, strip):
    def read(pkg):
        part = pkg.related(STYLES_REL)
        root = pkg.xml(part) if part else None
        definitions = list(root.findall(W + "style")) if root is not None else []
        ids = [el.get(W + "styleId") for el in definitions]
        if None in ids or len(ids) != len(set(ids)):
            raise ValueError("missing or duplicate style IDs")
        return part, dict(zip(ids, definitions))

    destination_part, destination = read(source)
    _, definitions = read(template)

    def value(style, tag):
        node = style.find(W + tag)
        return node.get(W + "val", "") if node is not None else ""

    def identity(style):
        return style.get(W + "type", "paragraph"), " ".join(value(style, "name").split()).casefold()

    def default(style):
        return style.get(W + "default") in ("1", "on", "true")

    matches = {}
    for sid, definition in definitions.items():
        typ, label = identity(definition)
        if default(definition):
            options = [key for key, el in destination.items() if default(el) and identity(el)[0] == typ]
        elif sid in destination and identity(destination[sid]) == (typ, label):
            options = [sid]
        else:
            options = [key for key, el in destination.items() if label and identity(el) == (typ, label)]
        if len(options) == 1:
            matches[sid] = options[0]
    for sid, definition in definitions.items():
        if sid in matches or default(definition):
            continue
        parent = matches.get(value(definition, "link"))
        if parent is not None:
            other = value(destination[parent], "link")
            if (other in destination and value(destination[other], "link") == parent
                    and identity(destination[other])[0] == identity(definition)[0]):
                matches[sid] = other

    refs = set(destination)
    original_parts = source.text_parts()
    for relation in (STYLES_REL, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering"):
        part = source.related(relation)
        if part:
            original_parts.append(part)
    for part in set(original_parts):
        refs.update(n.get(W + "val") for n in source.xml(part).iter() if n.tag in REFERENCE_TAGS)
    taken = refs | set(definitions)
    labels = {identity(el)[1] for el in destination.values()}
    required = set()
    for part in parts:
        required.update(n.get(W + "val") for n in template.xml(part).iter()
                        if n.tag in {W + "pStyle", W + "rStyle", W + "tblStyle"} and n.get(W + "val"))
    mapping, copied = {}, {}

    def resolve(sid):
        if sid in mapping:
            return
        if sid not in definitions:
            raise ValueError("template references undefined style " + str(sid))
        definition = definitions[sid]
        if default(definition):
            count = sum(default(el) and identity(el)[0] == identity(definition)[0] for el in destination.values())
            if count > 1:
                raise ValueError("multiple destination defaults for " + identity(definition)[0])
        if sid in matches:
            mapping[sid] = matches[sid]
            return
        if not allow_import or destination_part is None:
            raise ValueError("required template style cannot be imported: " + sid)
        if identity(definition)[0] == "numbering" or any(True for _ in definition.iter(W + "numPr")):
            raise ValueError("imported template style depends on numbering: " + sid)
        new_id = sid
        if sid in refs:
            new_id = "Template" + sid
            suffix = 1
            while new_id in taken:
                suffix += 1
                new_id = "Template" + sid + str(suffix)
        mapping[sid] = new_id
        taken.add(new_id)
        expected = strip(definition)
        expected.set(W + "styleId", new_id)
        if default(expected):
            del expected.attrib[W + "default"]
        label_node = expected.find(W + "name")
        if label_node is not None:
            old_label = label_node.get(W + "val", sid)
            suffix = 1
            while identity(expected)[1] in labels:
                label_node.set(W + "val", old_label + " (Template" + (str(suffix) if suffix > 1 else "") + ")")
                suffix += 1
            labels.add(identity(expected)[1])
        copied[sid] = expected
        for dependency in ("basedOn", "link", "next"):
            link = value(definition, dependency)
            if link:
                resolve(link)

    for sid in sorted(required):
        resolve(sid)
    expected = {mapping[sid]: mapped_tree(copied[sid], mapping) for sid in definitions if sid in copied}
    return mapping, expected
