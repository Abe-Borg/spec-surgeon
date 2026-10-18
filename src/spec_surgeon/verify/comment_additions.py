"""Independent source-to-result proof for explicitly approved Word comments.

No transform helpers or proposed part/id/target lists are trusted. This module
resolves the package relationships, selects original paragraphs, allocates fresh
ids, and derives the complete allowed comment graph from literal request inputs.
"""
from __future__ import annotations

import copy
import posixpath

from lxml import etree

from .reader import CT, REL, W
from .props import PartView, para_matches
from .markup import tree_difference

BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
RELATION_TYPES = [BASE + "comments", "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
                  "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
                  "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible"]
P14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
P15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
P16 = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
P18 = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
ROOT_TAGS = [W + "comments", P15 + "commentsEx", P16 + "commentsIds", P18 + "commentsExtensible"]
CTYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
PARAM_KEYS = {"text", "source", "author", "initials", "allow_missing", "expected_count"}


def validate_request(select, params):
    if not isinstance(select, dict) or set(select) - {"paragraphs", "scope"}:
        raise ValueError("review comments accept only paragraph and document-scope selectors")
    if select.get("scope", ["document"]) not in (["document"], "document"):
        raise ValueError("review comments must target the document scope")
    if not isinstance(select.get("paragraphs"), dict) or not select["paragraphs"]:
        raise ValueError("review comments require a targeted paragraph predicate")
    if not isinstance(params, dict) or set(params) - PARAM_KEYS:
        raise ValueError("unsupported review comment parameters")
    for field in {"text", "author"} | ({"source", "initials"} & set(params)):
        text = params.get(field)
        if not isinstance(text, str) or not text.strip():
            raise ValueError(field + " requires nonempty literal approved wording")
        if any(ord(x) < 32 or 0xD800 <= ord(x) <= 0xDFFF or ord(x) in {65534, 65535} for x in text):
            raise ValueError(field + " contains unsupported control or XML characters")
    if "allow_missing" in params and type(params["allow_missing"]) is not bool:
        raise ValueError("allow_missing must be boolean")
    if "expected_count" in params and (type(params["expected_count"]) is not int or params["expected_count"] < 0):
        raise ValueError("expected_count requires a nonnegative integer")


def _rel_path(part):
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def _related_parts(pkg, main):
    rel_path = _rel_path(main)
    rows = list(pkg.xml(rel_path)) if rel_path in pkg.names else []
    result = []
    for typ, root_tag in zip(RELATION_TYPES, ROOT_TAGS):
        found = [r for r in rows if r.tag == REL + "Relationship" and r.get("Type") == typ]
        if len(found) > 1:
            raise ValueError("duplicate comment relationships are ambiguous")
        if not found:
            result.append(None)
            continue
        row = found[0]
        if row.get("TargetMode", "Internal") != "Internal":
            raise ValueError("external comment relationship is unsupported")
        value = row.get("Target", "")
        name = value.lstrip("/") if value.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(main), value))
        if name not in pkg.names or pkg.xml(name).tag != root_tag:
            raise ValueError("missing comment relationship target or wrong XML root")
        result.append(name)
    if (any(result[1:]) and result[0] is None) or (result[3] and not result[2]):
        raise ValueError("incomplete modern comment metadata")
    return result


def _targets(ctx, select):
    root = ctx.src.xml(ctx.src.main_part())
    view = PartView(root)
    selected = [i for i, p in enumerate(view.pars) if para_matches(select["paragraphs"], p, i, view, ctx.src_styles)]
    # An interval model is independent of the transform's active-range walk.
    ordered = list(root.iter())
    offsets = {id(node): k for k, node in enumerate(ordered)}
    starts, ends = {}, {}
    marker_tags = {W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference"}
    for k, node in enumerate(ordered):
        if node.tag == W + "commentRangeStart":
            starts.setdefault(node.get(W + "id"), []).append(k)
        if node.tag == W + "commentRangeEnd":
            ends.setdefault(node.get(W + "id"), []).append(k)
    for i in selected:
        paragraph = view.pars[i]
        if paragraph.getparent() is None or paragraph.getparent().tag not in {W + "body", W + "tc"}:
            raise ValueError("selected comment anchor is not a body/table-cell paragraph")
        containers = {W + n for n in ("sdt", "txbxContent", "ins", "del", "moveFrom", "moveTo")}
        if any(a.tag in containers for a in paragraph.iterancestors()):
            raise ValueError("comment anchor lies inside a revision, control or textbox")
        if any(n.tag in {W + "sdt", W + "txbxContent"} for n in paragraph.iter()):
            raise ValueError("comment anchor contains a control or textbox")
        mark = paragraph.find(W + "pPr/" + W + "rPr")
        if mark is not None and any(n.tag in containers for n in mark):
            raise ValueError("revised paragraph mark makes the comment anchor unstable")
        for ancestor in paragraph.iterancestors():
            tag = {W + "tr": "trPr", W + "tc": "tcPr"}.get(ancestor.tag)
            properties = ancestor.find(W + tag) if tag else None
            if properties is not None and any(child.tag in {W + name for name in
                    ("ins", "del", "cellIns", "cellDel", "cellMerge")} for child in properties):
                raise ValueError("revised table row/cell makes the comment anchor unstable")
        if not view.texts[i].strip():
            raise ValueError("empty review-comment paragraph")
        if any(n.tag in marker_tags for n in paragraph.iter()):
            raise ValueError("existing comment mark overlaps the proposed anchor")
        start = offsets[id(paragraph)]
        for cid, locations in starts.items():
            for opening in locations:
                closings = [end for end in ends.get(cid, []) if end > opening]
                closing = min(closings) if closings else len(ordered)
                if opening < start < closing:
                    raise ValueError("existing cross-paragraph comment range overlaps the proposed anchor")
    return root, selected


def expected_addition(ctx, select, params):
    """Return independently expected trees and source counts; never inspect output."""
    validate_request(select, params)
    pkg, main = ctx.src, ctx.src.main_part()
    document, target_indices = _targets(ctx, select)
    count = len(target_indices)
    if not count and not params.get("allow_missing", False):
        raise ValueError("no review-comment anchor matched")
    if "expected_count" in params and params["expected_count"] != count:
        raise ValueError("approved review-comment cardinality does not match the source")
    parts = _related_parts(pkg, main)
    old_comments = pkg.xml(parts[0]) if parts[0] else None
    old_ids = [c.get(W + "id") for c in old_comments.findall(W + "comment")] if old_comments is not None else []
    if len(set(old_ids)) != len(old_ids):
        raise ValueError("existing comment ids are duplicated")
    rel_path = _rel_path(main)
    if not count:
        names = [main, "[Content_Types].xml"] + [p for p in [rel_path] + parts if p and p in pkg.names]
        return {"count": 0, "trees": {name: pkg.xml(name) for name in names}, "added": [], "targets": []}
    numeric, hexadecimal = set(), set()
    hex_keys = {P14 + "paraId", P15 + "paraId", P15 + "paraIdParent", P16 + "paraId", P16 + "durableId", P18 + "durableId"}
    defaults, overrides = pkg.content_types()
    relationship_xml = set(pkg.text_parts()) | {name for name in parts if name}
    for name in pkg.names:
        mime = overrides.get(name) or defaults.get(name.rsplit(".", 1)[-1].lower(), "")
        if name not in relationship_xml and not name.endswith(".xml") and not mime.endswith(("+xml", "/xml")):
            continue
        try:
            root = pkg.xml(name)
        except etree.XMLSyntaxError:
            continue
        for element in root.iter():
            if element.tag in {W + n for n in ("comment", "commentRangeStart", "commentRangeEnd", "commentReference")}:
                raw = element.get(W + "id", "")
                if not raw.isascii() or not raw.isdecimal():
                    raise ValueError("invalid existing comment id")
                numeric.add(int(raw))
            for key in hex_keys & set(element.attrib):
                hexadecimal.add(int(element.get(key), 16))
    comments_part = parts[0]
    is_new = comments_part is None
    content_types = pkg.xml("[Content_Types].xml")
    relationships = pkg.xml(rel_path) if rel_path in pkg.names else etree.Element(REL + "Relationships", nsmap={None: REL[1:-1]})
    if is_new:
        blocked = set(pkg.names) | set(pkg.content_types()[1]) | {r[3] for r in pkg.all_relationships() if r[3]}
        suffix = 0
        while comments_part is None:
            name = posixpath.join(posixpath.dirname(main), f"review-comments{suffix or ''}.xml")
            if name not in blocked:
                comments_part = name
            suffix += 1
        comments = etree.Element(W + "comments", nsmap={"w": W[1:-1]})
        if pkg.content_types()[0].get("xml") != CTYPE:
            etree.SubElement(content_types, CT + "Override", PartName="/" + comments_part, ContentType=CTYPE)
        rel_ids = [r.get("Id", "") for r in relationships.findall(REL + "Relationship")]
        rid = 1 + max([int(r[3:]) for r in rel_ids if r.startswith("rId") and r[3:].isdigit()] or [0])
        etree.SubElement(relationships, REL + "Relationship", Id=f"rId{rid}", Type=RELATION_TYPES[0],
                         Target=posixpath.relpath(comments_part, posixpath.dirname(main) or "."))
        if rel_path not in pkg.names and pkg.content_types()[0].get("rels") != "application/vnd.openxmlformats-package.relationships+xml":
            etree.SubElement(content_types, CT + "Override", PartName="/" + rel_path,
                             ContentType="application/vnd.openxmlformats-package.relationships+xml")
    else:
        comments = old_comments
    trees = {main: document, comments_part: comments, "[Content_Types].xml": content_types}
    if rel_path in pkg.names or is_new:
        trees[rel_path] = relationships
    for part in parts[1:]:
        if part:
            trees[part] = pkg.xml(part)
    paragraphs = list(document.iter(W + "p"))
    comment_id, hex_id = 0, 1
    for index in target_indices:
        while comment_id in numeric:
            comment_id += 1
        numeric.add(comment_id)
        cid = str(comment_id)
        comment = etree.SubElement(comments, W + "comment", {W + "id": cid, W + "author": params["author"]})
        if "initials" in params:
            comment.set(W + "initials", params["initials"])
        para_ids = []
        literal_paragraphs = [params["text"]] + ([params["source"]] if "source" in params else [])
        for literal in literal_paragraphs:
            p = etree.SubElement(comment, W + "p")
            if any(parts[1:]):
                while hex_id in hexadecimal:
                    hex_id += 1
                if hex_id > 0x7FFFFFFF:
                    raise ValueError("modern comment id range exhausted")
                hexadecimal.add(hex_id)
                para_ids.append(f"{hex_id:08X}")
                p.set(P14 + "paraId", para_ids[-1])
            run = etree.SubElement(p, W + "r")
            text = etree.SubElement(run, W + "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
            text.text = literal
        durable = None
        if parts[2]:
            while hex_id in hexadecimal:
                hex_id += 1
            if hex_id > 0x7FFFFFFF:
                raise ValueError("modern comment durable id range exhausted")
            hexadecimal.add(hex_id)
            durable = f"{hex_id:08X}"
        if parts[1]:
            etree.SubElement(trees[parts[1]], P15 + "commentEx", {P15 + "paraId": para_ids[-1], P15 + "done": "0"})
        if parts[2]:
            etree.SubElement(trees[parts[2]], P16 + "commentId", {P16 + "paraId": para_ids[-1], P16 + "durableId": durable})
        if parts[3]:
            etree.SubElement(trees[parts[3]], P18 + "commentExtensible", {P18 + "durableId": durable})
        anchor = paragraphs[index]
        start = etree.Element(W + "commentRangeStart", {W + "id": cid})
        anchor.insert(int(len(anchor) > 0 and anchor[0].tag == W + "pPr"), start)
        anchor.append(etree.Element(W + "commentRangeEnd", {W + "id": cid}))
        reference_run = etree.SubElement(anchor, W + "r")
        etree.SubElement(reference_run, W + "commentReference", {W + "id": cid})
    added = [comments_part] + ([rel_path] if rel_path not in pkg.names else []) if is_new else []
    return {"count": count, "trees": trees, "added": added, "targets": target_indices}


def added_review_comments(ctx, assertion, errors):
    expected = expected_addition(ctx, assertion.get("select", {}), assertion.get("params", {}))
    if assertion.get("expected_count") != expected["count"]:
        errors.append("added_review_comments: source selection differs from the predicted count")
    for part, tree in expected["trees"].items():
        if part not in ctx.out.names:
            errors.append(f"added_review_comments: expected part {part} is missing")
            continue
        difference = tree_difference(tree, ctx.out.xml(part))
        if difference:
            errors.append(f"added_review_comments: {part} differs from the independently derived addition: {difference}")
        if part in ctx.src_tp or part in ctx.out_tp:
            ctx.verified_parts.add(part)
        else:
            ctx.verified_members.add(part)
    # These names are source-derived, never an execution plan's exemption list.
    ctx.review_comment_additions = set(expected["added"])
    return f"added_review_comments: {expected['count']} literal comment(s), whole anchors/comments/metadata graph verified"


def verify_review_effect(source, output, effect, errors, binding):
    from .props import Ctx, _residual_styles
    source_context = Ctx(source, source, {"property_assertions": []})
    d = expected_addition(source_context, effect.get("select", {}), effect["params"])
    count = d["count"]
    if count != binding["effects"][effect["id"]]["source_count"]:
        raise ValueError("review-comment source binding count differs from independently selected anchors")
    if effect["expected_count"] != "all" and count != effect["expected_count"]:
        raise ValueError("review-comment expected_count differs from independently selected anchors")
    assertion = {"kind": "added_review_comments", "select": copy.deepcopy(effect.get("select", {})),
                 "params": copy.deepcopy(effect["params"]), "expected_count": count}
    ctx = Ctx(source, output, {"property_assertions": [assertion]})
    added_review_comments(ctx, assertion, errors)
    if ctx.styles_part:
        _residual_styles(ctx, errors)
    return ctx


COMMENT_ADDITION_KINDS = {"added_review_comments": added_review_comments}
