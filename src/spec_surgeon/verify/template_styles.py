"""Independently derive the style identities permitted by a template transplant.

Reads only the original destination and template. Neither output definitions nor a
transform's claimed mapping authorize a match or an imported definition.
"""
from __future__ import annotations

from lxml import etree

from .reader import STYLES_REL, W

REFERENCE_TAGS = {W + key for key in ("pStyle", "rStyle", "tblStyle", "basedOn", "next", "link",
                                      "numStyleLink", "styleLink")}


def _inherited_formatting(definitions, key, full):
    """Check inheritance without trusting the transform's compatibility decision.

    Only properties independently checked after pinning may differ. Keep the
    complete table/conditional formatting and all nested-paragraph properties.
    """
    containers = {W + n for n in ("pPr", "rPr", "tblPr", "trPr", "tcPr", "tblStylePr")}
    omit = {W + "pPr": {W + n for n in ("jc", "tabs")},
            W + "rPr": {W + n for n in ("sz", "color", "b", "i", "caps", "smallCaps")}}
    attributes = {(W + "pPr", W + "spacing"): {W + n for n in ("before", "after", "line", "lineRule")},
                  (W + "pPr", W + "ind"): {W + n for n in ("left", "right", "start", "end", "firstLine", "hanging")},
                  (W + "rPr", W + "rFonts"): {W + n for n in ("ascii", "hAnsi", "asciiTheme", "hAnsiTheme")}}

    def tree(node, remove=()):
        return (node.tag, tuple(sorted((k, v) for k, v in node.attrib.items() if k not in remove)),
                (node.text or "").strip(), tuple(tree(c) for c in node))

    result, seen = [], set()
    while key:
        if key in seen or key not in definitions:
            return None
        # Word's special IDs contribute neither properties nor inheritance
        # (MS-OI29500 §2.1.242(a)), including when used as a base style.
        if key in ("DefaultParagraphFont", "TableNormal", "NoList"):
            break
        seen.add(key)
        definition = definitions[key]
        layer = []
        for container in definition:
            if container.tag not in containers:
                continue
            children = []
            for child in container:
                if not full and child.tag in omit.get(container.tag, ()):
                    continue
                remove = attributes.get((container.tag, child.tag), ()) if not full else ()
                value = tree(child, remove)
                if not remove or any(value[1:]):
                    children.append(value)
            value = (container.tag, tuple(sorted(container.attrib.items())),
                     (container.text or "").strip(), tuple(children))
            if any(value[1:]):
                layer.append(value)
        if layer:
            result.insert(0, tuple(layer))
        parent = definition.find(W + "basedOn")
        key = parent.get(W + "val") if parent is not None else None
    return tuple(result)


def private_defaults(template, mapping, imported):
    part = template.related(STYLES_REL)
    if not part:
        return {}
    return {style.get(W + "type", "paragraph"): mapping[style.get(W + "styleId")]
            for style in template.xml(part).findall(W + "style")
            if style.get(W + "default") in ("1", "true", "on")
            and mapping.get(style.get(W + "styleId")) in imported}


def mapped_tree(root, mapping, implicit_defaults=None):
    from copy import deepcopy
    result = deepcopy(root)
    for node in result.iter():
        value = node.get(W + "val")
        if node.tag in REFERENCE_TAGS and value in mapping:
            node.set(W + "val", mapping[value])
    defaults = implicit_defaults or {}

    def bind(owner, path, ref, sid):
        parent = owner
        for name in path:
            container = parent.find(W + name)
            if container is None:
                container = etree.Element(W + name)
                if parent.tag == W + "pPr":
                    after = next((i for i, el in enumerate(parent)
                                  if el.tag in (W + "sectPr", W + "pPrChange")), len(parent))
                    parent.insert(after, container)
                else:
                    parent.insert(0, container)
            parent = container
        if parent.find(W + ref) is None:
            parent.insert(0, etree.Element(W + ref, {W + "val": sid}))

    for node in list(result.iter()):
        if node.tag == W + "p":
            if "paragraph" in defaults:
                bind(node, ["pPr"], "pStyle", defaults["paragraph"])
            if "character" in defaults:
                bind(node, ["pPr", "rPr"], "rStyle", defaults["character"])
        elif node.tag == W + "r" and "character" in defaults:
            bind(node, ["rPr"], "rStyle", defaults["character"])
        elif node.tag == W + "tbl" and "table" in defaults:
            bind(node, ["tblPr"], "tblStyle", defaults["table"])
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
    default_ids = {identity(s)[0]: sid for sid, s in definitions.items() if default(s)}
    required, full_styles = set(), set()
    for part in parts:
        for node in template.xml(part).iter():
            sid = node.get(W + "val")
            uses = []
            depth = sum(p.tag == W + "p" for p in node.iterancestors())
            if node.tag in {W + "pStyle", W + "rStyle", W + "tblStyle"} and sid:
                uses.append((sid, node.tag == W + "tblStyle" or depth > 1))
            if node.tag == W + "p":
                if node.find(f"{W}pPr/{W}pStyle") is None:
                    uses.append((default_ids.get("paragraph"), depth > 0))
                if node.find(f"{W}pPr/{W}rPr/{W}rStyle") is None:
                    uses.append((default_ids.get("character"), depth > 0))
            elif node.tag == W + "r" and node.find(f"{W}rPr/{W}rStyle") is None:
                uses.append((default_ids.get("character"), depth > 1))
            elif node.tag == W + "tbl" and node.find(f"{W}tblPr/{W}tblStyle") is None:
                uses.append((default_ids.get("table"), True))
            for sid, full in uses:
                if not sid:
                    continue
                required.add(sid)
                # A dependency of a style used without pins must also be compatible in full.
                while full and sid and sid not in full_styles:
                    full_styles.add(sid)
                    sid = (value(definitions[sid], "basedOn") if sid in definitions and
                           sid not in ("DefaultParagraphFont", "TableNormal", "NoList") else None)
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
        if (sid in matches and
                (expected_format := _inherited_formatting(definitions, sid, sid in full_styles)) is not None and
                expected_format ==
                _inherited_formatting(destination, matches[sid], sid in full_styles)):
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
        if sid in ("DefaultParagraphFont", "TableNormal", "NoList"):
            expected[:] = [child for child in expected if child.tag == W + "name"]
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
            link = value(expected, dependency)
            if link:
                resolve(link)

    for sid in sorted(required):
        resolve(sid)
    expected = {mapping[sid]: mapped_tree(copied[sid], mapping) for sid in definitions if sid in copied}
    return mapping, expected
