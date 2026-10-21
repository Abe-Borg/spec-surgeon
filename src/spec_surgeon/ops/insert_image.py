"""Op: insert_image — an attached picture as its own new paragraph before or after each anchor paragraph (built-in batch
6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §5). The verifier derives all of it again (verify/pictures.py, kind
``inserted_images``).

Definition (both sides; the picture file, its resolution and the placed size are ops/images.py's):

* **anchors**: the paragraphs a targeted ``paragraphs`` selector chooses in the parts of the scope (document — the
  default —, headers, footers). An anchor in a text box, or holding one (a paragraph inside it), is refused (``textbox``);
  so is ``after`` an anchor carrying a section break (``section-break``: the picture would open the next section).
* **The new paragraph** is the anchor's sibling just ``before`` / ``after`` it: ``w:pPr`` holds ``w:pStyle`` when a
  ``style`` is given (a paragraph style of the document — else none: the document's default paragraph style) and
  ``w:jc`` for ``align`` (``left`` | ``center`` | ``right``); then one run, ``<w:rPr><w:noProof/></w:rPr>`` and the
  picture's ``w:drawing``. Tracked (the default): the run inside a ``w:ins`` and the paragraph mark inserted
  (``w:pPr/w:rPr/w:ins``), as Word records an inserted paragraph; ``tracked: false``: neither.
* **The drawing** is Word's inline picture (``build_drawing``): ``wp:extent`` and ``pic:spPr/a:xfrm/a:ext`` the placed
  size; ``wp:docPr`` id one above the highest ``wp:docPr`` id of every text part of the package, counting up through the
  anchors (parts in scope order, anchors in document order), named ``Picture <id>``, ``descr`` the user's
  ``description`` (the alt text, verbatim); ``a:blip r:embed`` the part's new relationship.
* **The size** (``placed_size``): ``width`` / ``height`` in inches; neither → the picture's own size, shrunk to the text
  width — ``pgSz`` w − ``pgMar`` left, right and gutter — of the anchor's section (a paragraph belongs to the section of
  the first ``w:sectPr`` at or after it: the paragraphs carrying one, in document order, then the body's), or in a header
  or footer the narrowest text width of every section. A section without a usable page size and margins refuses
  (``image-size``).
* **The package**: ONE new media member ``word/media/image<N>.<png|jpeg|gif|bmp>`` (``pictures.free_media_name``) with
  the file's bytes; ONE new ``image`` relationship per part that gets the picture, the next ``rId<n>`` of its rels (the
  rels member created when the part has none); a ``[Content_Types].xml`` Default for the extension when it has none
  (an Override when its Default names another type).

No anchor in any part in scope → ``not-found`` unless ``allow_missing``.
"""
from __future__ import annotations

import posixpath
import re
from typing import Any, Dict, List, Optional

from lxml import etree

from ..ooxml import (RPR_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, insert_in_order, mark_rpr,
                     max_revision_id, paragraphs)
from ..package import CT_PART, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .images import (EMU_PER_TWIP, INSERT_TYPES, check_unchanged, length_param, load_picture, placed_size)
from .insert_paragraph import default_author
from .pictures import (add_default, ct_change, free_media_name, length_words, max_docpr_id, nearest_paragraph,
                       next_rid, scope_of, scope_words, given_scope)
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .swap_image import A, A_NS, IMAGE_REL, PIC, PIC_NS, R, R_NS, W, W_NS, WP, WP_NS

PARAMS = ("image", "position", "width", "height", "align", "description", "style", "tracked", "author",
          "allow_missing", "date_convention")
ALIGNS = ("left", "center", "right")
PIC_URI = "http://schemas.openxmlformats.org/drawingml/2006/picture"


def text_width(sect_pr) -> Optional[int]:
    """The section's text width in EMU: page width − left − right − gutter (whole twips, a missing margin 0; × 635),
    None when the section states no page width or margins, a value is not a whole number, or nothing is left."""
    sz, mar = sect_pr.find(W + "pgSz"), sect_pr.find(W + "pgMar")
    if sz is None or mar is None:
        return None
    values = [sz.get(W + "w"), mar.get(W + "left", "0"), mar.get(W + "right", "0"), mar.get(W + "gutter", "0")]
    if not all(v is not None and re.fullmatch(r"-?[0-9]+", v) for v in values):
        return None
    page, left, right, gutter = (int(v) for v in values)
    twips = page - left - right - gutter
    return twips * EMU_PER_TWIP if twips > 0 else None


def section_of(pars: List[Any]) -> List[int]:
    """Per paragraph of the main part (document order), the index of its section among ``DocxPackage.sect_prs``."""
    out, k = [], 0
    for p in pars:
        out.append(k)
        ppr = p.find(W + "pPr")
        if ppr is not None and ppr.find(W + "sectPr") is not None:
            k += 1
    return out


def description_param(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise JobException("'description' must be the alt text: the user's words for what the picture shows",
                           "bad-params")
    bad = sorted({f"U+{ord(c):04X}" for c in value if (ord(c) < 32 and c not in "\t\n") or 0xD800 <= ord(c) <= 0xDFFF
                  or c in "￾￿"})
    if bad:
        raise JobException(f"the alt text holds a control character ({', '.join(bad)})", "bad-params")
    return value


def build_drawing(cx: int, cy: int, docpr: int, rid: str, description: Optional[str]):
    """Word's inline picture (module docstring)."""
    drawing = etree.Element(W + "drawing", nsmap={"w": W_NS, "wp": WP_NS, "r": R_NS})
    inline = etree.SubElement(drawing, WP + "inline", {"distT": "0", "distB": "0", "distL": "0", "distR": "0"})
    etree.SubElement(inline, WP + "extent", {"cx": str(cx), "cy": str(cy)})
    etree.SubElement(inline, WP + "effectExtent", {"l": "0", "t": "0", "r": "0", "b": "0"})
    doc_pr = etree.SubElement(inline, WP + "docPr", {"id": str(docpr), "name": f"Picture {docpr}"})
    if description is not None:
        doc_pr.set("descr", description)
    frame_pr = etree.SubElement(inline, WP + "cNvGraphicFramePr")
    etree.SubElement(frame_pr, A + "graphicFrameLocks", {"noChangeAspect": "1"}, nsmap={"a": A_NS})
    graphic = etree.SubElement(inline, A + "graphic", nsmap={"a": A_NS})
    data = etree.SubElement(graphic, A + "graphicData", {"uri": PIC_URI})
    pic = etree.SubElement(data, PIC + "pic", nsmap={"pic": PIC_NS})
    nv = etree.SubElement(pic, PIC + "nvPicPr")
    etree.SubElement(nv, PIC + "cNvPr", {"id": "0", "name": f"Picture {docpr}"})
    etree.SubElement(nv, PIC + "cNvPicPr")
    fill = etree.SubElement(pic, PIC + "blipFill")
    etree.SubElement(fill, A + "blip", {R + "embed": rid})
    etree.SubElement(etree.SubElement(fill, A + "stretch"), A + "fillRect")
    sp = etree.SubElement(pic, PIC + "spPr")
    xfrm = etree.SubElement(sp, A + "xfrm")
    etree.SubElement(xfrm, A + "off", {"x": "0", "y": "0"})
    etree.SubElement(xfrm, A + "ext", {"cx": str(cx), "cy": str(cy)})
    etree.SubElement(etree.SubElement(sp, A + "prstGeom", {"prst": "rect"}), A + "avLst")
    return drawing


def build_paragraph(drawing, style: Optional[str], align: Optional[str], tracked: bool, stamp, ids: List[int]):
    """The new paragraph around the drawing (module docstring); ``ids`` hands out the revision ids."""
    p = etree.Element(W + "p")
    if style or align:
        ppr = etree.SubElement(p, W + "pPr")
        if style:
            etree.SubElement(ppr, W + "pStyle").set(W + "val", style)
        if align:
            etree.SubElement(ppr, W + "jc").set(W + "val", align)
    holder = p
    if tracked:
        mark = etree.Element(W + "ins")
        stamp.apply(mark, ids.pop(0))
        insert_in_order(mark_rpr(p, create=True), mark, RPR_ORDER)
        holder = etree.SubElement(p, W + "ins")
        stamp.apply(holder, ids.pop(0))
    run = etree.SubElement(holder, W + "r")
    etree.SubElement(etree.SubElement(run, W + "rPr"), W + "noProof")
    run.append(drawing)
    return p


@register
class InsertImage(Op):
    name = "insert_image"
    mode = "content"
    description = ("Insert an attached picture (PNG, JPEG, GIF or BMP) as its own new paragraph before or after each "
                   "anchor paragraph a targeted selector chooses, tracked (default) or untracked: 'width' and/or "
                   "'height' in inches (one keeps the proportions; neither: the picture's own size, shrunk to the text "
                   "width), 'align' left/center/right, 'description' the user's alt text, 'style' the new paragraph's "
                   "style (default: the document's default paragraph style). SVG and other files are refused.")
    params_schema = {
        "type": "object",
        "properties": {
            "image": {"type": "string", "description": "the picture to insert: a .png, .jpg, .gif or .bmp file"},
            "position": {"type": "string", "enum": ["after", "before"], "default": "after"},
            "width": {"type": "number", "description": "inches; alone, the height keeps the picture's proportions"},
            "height": {"type": "number", "description": "inches; alone, the width keeps the picture's proportions"},
            "align": {"type": "string", "enum": list(ALIGNS),
                      "description": "the new paragraph's alignment (default: its style's)"},
            "description": {"type": "string", "description": "alt text: the user's words, verbatim"},
            "style": {"type": "string", "description": "a paragraph style id of the document for the new paragraph"},
            "tracked": {"type": "boolean", "default": True},
            "author": {"type": "string"},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file without an anchor passes unchanged instead of being set aside"},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["image"],
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers"]},
                      "default": ["document"]},
            "paragraphs": {"type": "object", "description": "targeted selector naming the anchor paragraph(s)"},
        },
    }
    verification_profile = {
        "V1": "one media member added (the picture file's bytes), each anchor part's rels (created when missing) and "
              "[Content_Types].xml when the extension has no Default",
        "V2": "one BlockDiff per new paragraph (before=None, after='' — a picture has no text)",
        "V4": "tracked: +1 ins and +1 ins_mark per picture; untracked: 0",
        "V10": "inserted_images re-derives the anchors, the picture's size (its own reader and resolution, the "
               "section's text width), the drawing ids, and builds each new paragraph again with its own template; "
               "the relationships, the media member and the content types are compared whole",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        image = params.get("image")
        what = f"the picture {prose.quote(image)}" if image else "the attached picture"
        position = "before" if params.get("position") == "before" else "after"
        subject = prose.where({"paragraphs": select.get("paragraphs")})[3:]
        anchor = ("each anchor paragraph" + subject[len("every paragraph"):] if subject.startswith("every paragraph")
                  else "each of the " + subject)
        scope = select.get("scope") or params.get("scope")
        where = f" {scope_words(scope)}" if scope and list(scope) != ["document"] else ""
        width, height = params.get("width"), params.get("height")
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)        # noqa: E731
        if number(width) and number(height):
            size = f"{length_words(width)} wide and {length_words(height)} high"
        elif number(width):
            size = f"{length_words(width)} wide with its proportions kept"
        elif number(height):
            size = f"{length_words(height)} high with its proportions kept"
        else:
            size = "at its own size, made smaller to fit the text width when it is wider"
        clauses = [size]
        if params.get("align") in ALIGNS:
            clauses.append({"left": "aligned left", "center": "centred", "right": "aligned right"}[params["align"]])
        if isinstance(params.get("style"), str) and params["style"]:
            clauses.append(f"in {prose.style(params['style'])}")
        if isinstance(params.get("description"), str) and params["description"]:
            clauses.append(f"with the alt text {prose.quote(params['description'])}")
        if params.get("tracked", True) is False:
            tail = "without tracked changes"
        else:
            tail = "shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        first = f"Insert {what} as its own paragraph {position} {anchor}{where}, {', '.join(clauses)}, {tail}."
        second = "Files with no such paragraph are left unchanged." if params.get("allow_missing") is True else ""
        return prose.sentence(first, second)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select, params = dict(select or {}), dict(params or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"insert_image's selector takes 'scope' and 'paragraphs' (got {sorted(unknown)})",
                               "bad-params")
        unknown = set(params) - set(PARAMS)
        if unknown:
            raise JobException(f"insert_image has no parameter(s) {sorted(unknown)}", "bad-params")
        pred = select.get("paragraphs")
        check_paragraph_selector(pred)
        if not is_targeted(select):
            raise JobException("insert_image needs a targeted 'paragraphs' selector naming the anchor paragraph(s)",
                               "bad-params")
        path, data, md5, pic = load_picture(params.get("image"), "insert_image.image")
        position = params.get("position", "after")
        if position not in ("after", "before"):
            raise JobException(f"position must be 'after' or 'before', got {position!r}", "bad-params")
        width, height = length_param(params.get("width"), "width"), length_param(params.get("height"), "height")
        align = params.get("align")
        if align is not None and align not in ALIGNS:
            raise JobException(f"'align' must be one of {list(ALIGNS)}", "bad-params")
        description = description_param(params.get("description"))
        tracked = params.get("tracked", True)
        allow_missing = params.get("allow_missing", False)
        if not isinstance(tracked, bool) or not isinstance(allow_missing, bool):
            raise JobException("'tracked' and 'allow_missing' must be true or false", "bad-params")
        convention = params.get("date_convention", "word-local-z")
        if convention not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        scope = scope_of(select, params, "insert_image")
        parts = scope_parts(pkg, scope)
        sheet = StyleSheet(pkg)
        style = params.get("style")
        if style is not None:
            if not isinstance(style, str) or not style:
                raise JobException("'style' must be a paragraph style id", "bad-params")
            if style not in sheet.styles:
                raise JobException(f"this file has no style {style!r}", "missing-style", {"style": style})
            if sheet.styles[style].get(W + "type", "paragraph") != "paragraph":
                raise JobException(f"{style!r} is not a paragraph style in this file", "style-mismatch",
                                   {"style": style})
        main = pkg.main_part()
        secs = DocxPackage.sect_prs(pkg.xml(main))
        widths = [text_width(sp) for sp in secs]
        main_sections = section_of(paragraphs(pkg.xml(main)))
        frag = self.fragment({"scope": scope, "paragraphs": pred},
                             {**params, "position": position, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        frag.geometry_reference = "none"
        frag.geometry_reason = "a picture placed in its own paragraph reflows what follows"
        docpr = max_docpr_id(pkg)
        planned: Dict[str, List[Dict[str, Any]]] = {}
        read_sections = set()
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-resolves the anchors over every part in scope
            root = pkg.xml(part)
            pars = paragraphs(root)
            items: List[Dict[str, Any]] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                holds_box = any(q is not p for q in p.iter(W + "p"))
                if nearest_paragraph(p) is not None or holds_box:
                    raise JobException(f"{part} paragraph {bi} is in a text box or holds one — change it in Word",
                                       "textbox", {"part": part, "paragraph": bi})
                if position == "after" and p.find(f"{W}pPr/{W}sectPr") is not None:
                    raise JobException(f"{part} anchor paragraph {bi} carries a section break; insert 'before' it or "
                                       f"anchor on a paragraph of the next section", "section-break",
                                       {"part": part, "paragraph": bi})
                limit = None
                if width is None and height is None:
                    shown = [main_sections[bi]] if part == main else list(range(len(secs)))
                    if not shown or any(i >= len(secs) or widths[i] is None for i in shown):
                        raise JobException(f"{part} paragraph {bi}: its section states no usable page size and margins "
                                           f"to fit the picture in — give its width or height in inches",
                                           "image-size", {"part": part, "paragraph": bi})
                    limit = min(widths[i] for i in shown)
                    read_sections.update(shown)
                cx, cy = placed_size(pic, width, height, limit)
                k = len(items)
                out = bi + k if position == "before" else bi + 1 + k
                docpr += 1
                items.append({"anchor": bi, "out": out, "docpr": docpr, "size": [cx, cy]})
            if items:
                planned[part] = items
        total = sum(len(v) for v in planned.values())
        assertion: Dict[str, Any] = {
            "kind": "inserted_images", "scope": given_scope(select, params), "select": {"paragraphs": pred},
            "position": position, "image": {"file": str(path), "md5": md5, "format": pic.format,
                                            "width": pic.width, "height": pic.height, "size": len(data)},
            "width": width, "height": height, "align": align, "description": description, "style": style,
            "tracked": tracked, "author": author, "expected": planned, "expected_anchors": total}
        if not total:
            if not allow_missing:
                raise JobException(f"no anchor paragraph matched {pred} in {scope}", "not-found",
                                   {"scope": scope, "paragraphs": pred})
            frag.notes.append("insert_image: no anchor paragraph in this file (allowed); nothing inserted")
            assertion.update(media=None, rids={})
            frag.property_assertions.append(assertion)
            return frag
        ext = pic.format
        content_type = INSERT_TYPES[ext]
        media = free_media_name(pkg, ext)
        ct = ct_change(pkg, ext, content_type)
        defaults, _ = pkg.content_types()
        rids: Dict[str, str] = {}
        created: List[str] = []
        for part, items in planned.items():
            rp = pkg.rels_path_for(part)
            rids[part] = next_rid(pkg, part)
            if not pkg.has(rp):
                if "rels" not in defaults:
                    raise JobException(f"{part} has no relationships and [Content_Types].xml no Default for .rels",
                                       "structure", {"part": part})
                created.append(rp)
            frag.intended_parts += [part, rp]
            frag.touches += [(part, "*", "text"), (rp, "*", "rels")]
            for it in items:
                frag.diffs.append(BlockDiff(part=part, block_index=it["out"], before=None, after="",
                                            accepted_view="" if tracked else None,
                                            note=f"picture inserted {position} block {it['anchor']} "
                                                 f"({'tracked' if tracked else 'untracked'})"))
        if ct:
            frag.intended_parts.append(CT_PART)
            frag.touches.append((CT_PART, "*", "ct"))
        frag.members_added = [media] + created
        frag.touches += [(m, "*", "add") for m in frag.members_added]
        frag.touches.append((main, "docPr", "ids"))      # the new drawing ids count up from the package's highest
        frag.touches += [(main, f"sectPr:{i}", fam) for i in sorted(read_sections)
                         for fam in ("verify:pgSz", "verify:pgMar")]
        if style and sheet.part:
            frag.touches.append((sheet.part, f"style:{style}", "verify"))
        if tracked:
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + total
            frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + total
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = 2 * total
        else:
            frag.flags.append(f"UNTRACKED insertion of {total} picture(s) at the user's request.")
        assertion.update(media=media, rids=rids)
        frag.property_assertions.append(assertion)
        frag.notes.append(f"insert_image: {path.name} ({pic.format}, {pic.width} × {pic.height} px) as {total} new "
                          f"paragraph(s) {position} their anchors in {len(planned)} part(s), "
                          + "; ".join(f"{p} block {it['out']}: {it['size'][0]} × {it['size'][1]} EMU"
                                      for p, v in planned.items() for it in v)
                          + f"; media {media}" + (f"; [Content_Types].xml gets a {ct} for .{ext}" if ct else ""))
        frag.transform_data = {
            "image": {"file": str(path), "md5": md5}, "media": media, "content_type": content_type, "ct": ct,
            "ext": ext, "position": position, "style": style, "align": align, "description": description,
            "tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
            "date_convention": convention, "first_rev_id": frag.rev_id_base,
            "parts": {part: {"rid": rids[part], "target": posixpath.relpath(media, posixpath.dirname(part)),
                             "items": items} for part, items in planned.items()}}
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        data = check_unchanged(td["image"]["file"], td["image"]["md5"], "insert_image.image")
        if td["ct"] == "default":
            add_default(pkg, td["ext"], td["content_type"])
        pkg.add_member(td["media"], data, td["content_type"] if td["ct"] == "override" else None)
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td["date_convention"])
        ids = list(range(td["first_rev_id"], td["first_rev_id"] + frag.rev_ids_needed))
        for part, d in td["parts"].items():
            got = pkg.edit_rels(part, add=[{"type": IMAGE_REL, "target": d["target"]}])
            if got != [d["rid"]]:
                raise JobException(f"internal: {part}'s new relationship is {got}, the plan said {d['rid']}", "internal")
            root = pkg.xml(part)
            pars = paragraphs(root)
            news = []
            for it in d["items"]:               # revision ids in document order; placed after, so indices hold
                drawing = build_drawing(it["size"][0], it["size"][1], it["docpr"], d["rid"], td["description"])
                news.append((pars[it["anchor"]], build_paragraph(drawing, td["style"], td["align"], td["tracked"],
                                                                 stamp, ids)))
            for anchor, p in news:
                if td["position"] == "after":
                    anchor.addnext(p)
                else:
                    anchor.addprevious(p)
            pkg.write_xml(part, root)
