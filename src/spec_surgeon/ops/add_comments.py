"""Add approved review reasons as classic Word comments, with analytical predictions.

Comment wording and optional source text are literal inputs. Existing comments,
revisions and their modern metadata remain intact. The independent proof lives in
verify/comment_additions.py and does not import this module.
"""
from __future__ import annotations

import posixpath
from lxml import etree

from ..ooxml import block_full_text, block_struct_counts, paragraphs
from ..package import REL, REL_TYPE_BASE, W, DocxPackage, serialize_xml
from . import register
from .base import BlockDiff, JobException, Op
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected

COMMENTS = REL_TYPE_BASE + "comments"
SIDE_TYPES = (
    "http://schemas.microsoft.com/office/2011/relationships/commentsExtended",
    "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds",
    "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible",
)
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
CID = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
CEX = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
COMMENT_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
MARKS = {W + x for x in ("commentRangeStart", "commentRangeEnd", "commentReference")}
HEX_ATTRS = {W14 + "paraId", W15 + "paraId", W15 + "paraIdParent", CID + "paraId", CID + "durableId", CEX + "durableId"}


def clean_request(select, params):
    """Validate literal inputs without generating or normalizing their wording."""
    if not isinstance(select, dict) or set(select) - {"scope", "paragraphs"}:
        raise JobException("review comments select document paragraphs only", "bad-params")
    scope = select.get("scope", ["document"])
    if scope not in (["document"], "document"):
        raise JobException("review comments support only the document scope", "bad-params")
    check_paragraph_selector(select.get("paragraphs"))
    if not is_targeted(select):
        raise JobException("review comments require a targeted paragraph selector", "bad-params")
    if not isinstance(params, dict) or set(params) - {"text", "source", "author", "initials", "allow_missing", "expected_count"}:
        raise JobException("unsupported review comment parameters", "bad-params")
    for key in ("text", "author", "source", "initials"):
        if key not in params and key not in ("text", "author"):
            continue
        value = params.get(key)
        if not isinstance(value, str) or not value.strip():
            raise JobException(f"{key} must be non-empty approved literal text", "bad-params")
        # The first release accepts single paragraphs. Do not silently translate
        # literal control characters into different visible Word wording.
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value):
            raise JobException(f"{key} must contain XML-safe text without control characters", "bad-params")
    if "allow_missing" in params and type(params["allow_missing"]) is not bool:
        raise JobException("allow_missing must be boolean", "bad-params")
    if "expected_count" in params and (type(params["expected_count"]) is not int or params["expected_count"] < 0):
        raise JobException("expected_count must be a non-negative integer", "bad-params")


def _part_for(pkg, main, kind):
    rp = pkg.rels_path_for(main)
    rows = [r for r in pkg.xml(rp).findall(REL + "Relationship") if r.get("Type") == kind] if pkg.has(rp) else []
    if not rows:
        return None
    if len(rows) != 1 or rows[0].get("TargetMode", "Internal") != "Internal":
        raise JobException("ambiguous or external comment relationship", "structure")
    target = rows[0].get("Target", "")
    part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(main), target))
    if not pkg.has(part):
        raise JobException("comment relationship names a missing part", "structure")
    return part


def _supported_target(p, index, occupied):
    if p.getparent() is None or p.getparent().tag not in (W + "body", W + "tc"):
        raise JobException(f"paragraph {index}: comment anchors require a body or table-cell paragraph", "unsupported-comment-anchor")
    forbidden = {W + x for x in ("sdt", "txbxContent", "ins", "del", "moveFrom", "moveTo")}
    if any(a.tag in forbidden for a in p.iterancestors()) or any(e.tag in (W + "sdt", W + "txbxContent") for e in p.iter()):
        raise JobException(f"paragraph {index}: comment anchor crosses a control, textbox or revision container", "unsupported-comment-anchor")
    rpr = p.find(W + "pPr/" + W + "rPr")
    if rpr is not None and any(e.tag in forbidden for e in rpr):
        raise JobException(f"paragraph {index}: a revised paragraph mark makes the comment anchor unstable", "unsupported-comment-anchor")
    for ancestor in p.iterancestors():
        if ancestor.tag in (W + "tr", W + "tc"):
            props = ancestor.find(W + ("trPr" if ancestor.tag == W + "tr" else "tcPr"))
            if props is not None and any(e.tag in {W + n for n in ("ins", "del", "cellIns", "cellDel", "cellMerge")} for e in props):
                raise JobException(f"paragraph {index}: a revised table row or cell makes the comment anchor unstable", "unsupported-comment-anchor")
    if index in occupied:
        raise JobException(f"paragraph {index}: an existing comment range overlaps the proposed anchor", "comment-overlap")
    if not block_full_text(p).strip():
        raise JobException(f"paragraph {index}: an empty paragraph is not a review-comment anchor", "unsupported-comment-anchor")


def derive(pkg, select, params):
    """Source-only decisions: targets, unused ids, destination members and side rows."""
    clean_request(select, params)
    main = pkg.main_part()
    root = pkg.xml(main)
    pars = paragraphs(root)
    sheet = StyleSheet(pkg)
    targets = [i for i, p in enumerate(pars) if paragraph_selected(select["paragraphs"], p, i, block_full_text(p), sheet, pars)]
    if not targets and not params.get("allow_missing", False):
        raise JobException("no review-comment paragraphs matched", "not-found")
    if "expected_count" in params and len(targets) != params["expected_count"]:
        raise JobException(f"review comments matched {len(targets)} paragraphs, expected {params['expected_count']}", "cardinality")
    # Detect ranges that begin before a selected paragraph as well as marks in it.
    positions = {id(p): i for i, p in enumerate(pars)}
    active, occupied = set(), set()
    for el in root.iter():
        if el.tag == W + "p" and active:
            occupied.add(positions[id(el)])
        if el.tag in MARKS:
            nearest = next((a for a in el.iterancestors() if a.tag == W + "p"), None)
            if nearest is not None:
                occupied.add(positions[id(nearest)])
            if el.tag == W + "commentRangeStart":
                active.add(el.get(W + "id"))
            elif el.tag == W + "commentRangeEnd":
                active.discard(el.get(W + "id"))
    for i in targets:
        _supported_target(pars[i], i, occupied)
    part = _part_for(pkg, main, COMMENTS)
    sides = [_part_for(pkg, main, kind) for kind in SIDE_TYPES]
    if (any(sides) and not part) or (sides[2] and not sides[1]):
        raise JobException("incomplete modern comment metadata; cannot safely append a thread", "structure")
    if part and pkg.xml(part).tag != W + "comments":
        raise JobException("comments relationship does not name a comments root", "structure")
    roots = (W15 + "commentsEx", CID + "commentsIds", CEX + "commentsExtensible")
    for side, tag in zip(sides, roots):
        if side and pkg.xml(side).tag != tag:
            raise JobException("unsupported comment metadata root", "structure")
    ids, hex_ids, reads = set(), set(), []
    comment_ids = []
    if part:
        comment_ids = [c.get(W + "id") for c in pkg.xml(part).findall(W + "comment")]
        if len(set(comment_ids)) != len(comment_ids):
            raise JobException("duplicate existing comment ids", "structure")
    defaults, overrides = pkg.content_types()
    known_xml = set(pkg.text_parts()) | {p for p in sides if p}
    for name in pkg.member_names:
        media_type = overrides.get(name, defaults.get(posixpath.splitext(name)[1].lstrip(".").lower(), ""))
        if name not in known_xml and not name.endswith(".xml") and not media_type.endswith(("+xml", "/xml")):
            continue
        try:
            tree = pkg.xml(name)
        except etree.XMLSyntaxError:
            continue  # unrelated opaque XML is preserved byte-for-byte
        reads.append(name)
        for el in tree.iter():
            if el.tag == W + "comment" or el.tag in MARKS:
                value = el.get(W + "id", "")
                if not value.isascii() or not value.isdecimal():
                    raise JobException("non-numeric or missing comment id", "structure")
                ids.add(int(value))
            for attr, value in el.attrib.items():
                if attr in HEX_ATTRS:
                    try:
                        hex_ids.add(int(value, 16))
                    except ValueError:
                        raise JobException("invalid comment paragraph or durable id", "structure")
    reserved = set(pkg.member_names) | set(pkg.content_types()[1]) | {r.resolved for _, r in pkg.relationships() if r.resolved}
    if part is None:
        n = 0
        while True:
            candidate = posixpath.join(posixpath.dirname(main), "review-comments" + (str(n) if n else "") + ".xml")
            if candidate not in reserved:
                part = candidate
                break
            n += 1
    entries, next_id, next_hex = [], 0, 1
    for i in targets:
        while next_id in ids:
            next_id += 1
        entry = {"paragraph": i, "id": str(next_id), "para_ids": [], "durable_id": None}
        ids.add(next_id)
        if any(sides):
            for _ in range(1 + int("source" in params) + int(bool(sides[1]))):
                while next_hex in hex_ids:
                    next_hex += 1
                if next_hex > 0x7FFFFFFF:
                    raise JobException("no supported modern comment ids available", "structure")
                value = f"{next_hex:08X}"
                hex_ids.add(next_hex)
                entry["para_ids"].append(value)
            if sides[1]:
                entry["durable_id"] = entry["para_ids"].pop()
        entries.append(entry)
    return {"main": main, "part": part, "sides": sides, "entries": entries, "reads": reads,
            "existing": pkg.has(part), "comment_blocks": len(paragraphs(pkg.xml(part))) if pkg.has(part) else 0}


def comment_element(entry, params):
    c = etree.Element(W + "comment")
    c.set(W + "id", entry["id"])
    c.set(W + "author", params["author"])
    if "initials" in params:
        c.set(W + "initials", params["initials"])
    for i, value in enumerate([params["text"]] + ([params["source"]] if "source" in params else [])):
        p = etree.SubElement(c, W + "p")
        if entry["para_ids"]:
            p.set(W14 + "paraId", entry["para_ids"][i])
        t = etree.SubElement(etree.SubElement(p, W + "r"), W + "t")
        t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        t.text = value
    return c


@register
class AddReviewComments(Op):
    name = "add_review_comments"
    mode = "untracked"
    description = "Attach approved literal review reasons and optional source text to selected paragraphs as Word comments."
    params_schema = {"type": "object", "properties": {
        "text": {"type": "string", "description": "Approved comment wording, verbatim, one paragraph."},
        "source": {"type": "string", "description": "Optional approved source wording, verbatim in a second paragraph."},
        "author": {"type": "string", "description": "Literal reviewer name shown by Word."},
        "initials": {"type": "string"},
        "allow_missing": {"type": "boolean", "default": False},
        "expected_count": {"type": "integer", "minimum": 0},
    }, "required": ["text", "author"]}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": ["document"]}},
        "paragraphs": {"type": "object", "description": "Targeted paragraph selector; anchors encompass the complete paragraph."},
    }, "required": ["paragraphs"]}
    verification_profile = {
        "V2": "Exact approved comment paragraphs added; every document paragraph text preserved.",
        "V3": "Exactly one commentReference added to each selected paragraph.",
        "V4": "No tracked revisions added, removed or changed.",
        "V10": "Independently derive original targets, ids, comment metadata and whole expected package parts.",
    }

    def plan(self, pkg, select, params):
        d = derive(pkg, select, params)
        frag = self.fragment(select, params, mode="untracked")
        frag.untracked_diffs_expected = True
        frag.property_assertions = [{"kind": "added_review_comments", "select": dict(select), "params": dict(params),
                                     "expected_count": len(d["entries"])}]
        # Whole-part proof deliberately requires a separate chain stage after
        # wording/style edits: all selection evidence must still be the source.
        frag.touches = [(p, "*", "verify") for p in d["reads"]]
        frag.touches += [(p, "*", "verify") for p in pkg.member_names if p.endswith(".rels")]
        frag.transform_data = d
        if not d["entries"]:
            frag.notes.append("No review-comment paragraphs matched; explicit allow_missing no-op.")
            return frag
        main, part = d["main"], d["part"]
        frag.intended_parts = [main] + ([part] if d["existing"] else []) + [p for p in d["sides"] if p]
        frag.touches += [(p, "*", "text") for p in frag.intended_parts]
        if not d["existing"]:
            frag.members_added.append(part)
            rp = pkg.rels_path_for(main)
            if pkg.has(rp):
                frag.intended_parts.append(rp)
            else:
                frag.members_added.append(rp)
            defaults = pkg.content_types()[0]
            if defaults.get("xml") != COMMENT_CT or (not pkg.has(rp) and defaults.get("rels") != RELS_CT):
                frag.intended_parts.append("[Content_Types].xml")
            frag.touches += [(part, "*", "add"), (rp, "*", "rels"), ("[Content_Types].xml", "*", "ct")]
        pars = paragraphs(pkg.xml(main))
        bi = d["comment_blocks"]
        for entry in d["entries"]:
            p = pars[entry["paragraph"]]
            struct = block_struct_counts(p)
            struct["commentReference"] = struct.get("commentReference", 0) + 1
            frag.diffs.append(BlockDiff(main, entry["paragraph"], block_full_text(p), block_full_text(p),
                                        struct_after=struct, note="approved review comment anchor"))
            for value in [params["text"]] + ([params["source"]] if "source" in params else []):
                frag.diffs.append(BlockDiff(part, bi, None, value, note="approved literal review-comment text"))
                bi += 1
        frag.geometry_reference = "none"
        frag.geometry_reason = "review comments can change Word's markup margin; body layout is not a geometric promise"
        frag.notes.append(f"Added {len(d['entries'])} review comment(s), with literal approved wording. Existing comments and revisions stay.")
        return frag

    def apply(self, pkg, frag):
        d, params = frag.transform_data, frag.params
        if not d["entries"]:
            return
        main, part = d["main"], d["part"]
        root = pkg.xml(main)
        pars = paragraphs(root)
        comments = pkg.xml(part) if d["existing"] else etree.Element(W + "comments", nsmap={"w": W[1:-1]})
        side_roots = [pkg.xml(p) if p else None for p in d["sides"]]
        for entry in d["entries"]:
            p = pars[entry["paragraph"]]
            start = etree.Element(W + "commentRangeStart", {W + "id": entry["id"]})
            p.insert(1 if len(p) and p[0].tag == W + "pPr" else 0, start)
            etree.SubElement(p, W + "commentRangeEnd", {W + "id": entry["id"]})
            etree.SubElement(etree.SubElement(p, W + "r"), W + "commentReference", {W + "id": entry["id"]})
            comments.append(comment_element(entry, params))
            if side_roots[0] is not None:
                etree.SubElement(side_roots[0], W15 + "commentEx", {W15 + "paraId": entry["para_ids"][-1], W15 + "done": "0"})
            if side_roots[1] is not None:
                etree.SubElement(side_roots[1], CID + "commentId", {CID + "paraId": entry["para_ids"][-1], CID + "durableId": entry["durable_id"]})
            if side_roots[2] is not None:
                etree.SubElement(side_roots[2], CEX + "commentExtensible", {CEX + "durableId": entry["durable_id"]})
        pkg.write_xml(main, root)
        if d["existing"]:
            pkg.write_xml(part, comments)
        else:
            pkg.add_member(part, serialize_xml(comments), COMMENT_CT)
            target = posixpath.relpath(part, posixpath.dirname(main) or ".")
            rp = pkg.rels_path_for(main)
            if pkg.has(rp):
                pkg.edit_rels(main, add=[{"type": COMMENTS, "target": target}])
            else:
                relationships = etree.Element(REL + "Relationships", nsmap={None: REL[1:-1]})
                etree.SubElement(relationships, REL + "Relationship", Id="rId1", Type=COMMENTS, Target=target)
                pkg.add_member(rp, serialize_xml(relationships), RELS_CT)
        for path, tree in zip(d["sides"], side_roots):
            if path:
                pkg.write_xml(path, tree)
