"""Op: insert_paragraph — insert a new paragraph containing the user's approved wording verbatim, anchored
before or after paragraphs matching a targeted selector, as a tracked insertion (default) or untracked.

Formatting (``formatting: "inherit"``, the default): the new paragraph looks like its anchor. Definition shared
with the verifier (verify/props.py re-implements it; CLAUDE.md "Other definitions that must stay identical"):

* paragraph properties = a copy of the anchor's ``w:pPr`` without ``w:sectPr`` and ``w:pPrChange``, and without the
  revision marks (``w:ins``/``w:del``/``w:moveFrom``/``w:moveTo``) and ``w:rPrChange`` inside its paragraph-mark
  ``w:rPr`` (an emptied ``w:rPr`` is dropped); an explicit ``style`` replaces the copied ``w:pStyle``;
* run properties = a copy of the ``w:rPr`` of the anchor's first live text run (a ``w:r`` whose nearest ``w:p`` is
  the anchor, not inside ``w:del``/``w:moveFrom``, holding a ``w:t``) without ``w:rPrChange``.

Nothing is synthesized: every property comes from the anchor. ``formatting: "none"`` gives the pre-0.3.1
behaviour (only the paragraph style).
"""
from __future__ import annotations

import copy
import getpass
import os
from typing import Any, Dict, List, Optional

from lxml import etree

from ..ooxml import (PPR_ORDER, RPR_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, insert_in_order,
                     mark_rpr, max_revision_id, paragraph_ppr, paragraphs, set_text)
from ..package import W, DocxPackage
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .slots import nearest_p


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


MARK_REVISIONS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "rPrChange")


def inherited_ppr(anchor: etree._Element) -> Optional[etree._Element]:
    """The anchor's paragraph properties as the new paragraph inherits them (module docstring)."""
    ppr = anchor.find(W + "pPr")
    if ppr is None:
        return None
    c = copy.deepcopy(ppr)
    for child in list(c):
        if child.tag in (W + "sectPr", W + "pPrChange"):
            c.remove(child)
    rpr = c.find(W + "rPr")
    if rpr is not None:
        for child in list(rpr):
            if child.tag in MARK_REVISIONS:
                rpr.remove(child)
        if len(rpr) == 0 and not rpr.attrib:
            c.remove(rpr)
    return c if (len(c) or c.attrib) else None


def first_live_text_run(anchor: etree._Element) -> Optional[etree._Element]:
    for r in anchor.iter(W + "r"):
        if nearest_p(r) is not anchor or r.find(W + "t") is None:
            continue
        q, hidden = r.getparent(), False
        while q is not None and q is not anchor:
            if q.tag in (W + "del", W + "moveFrom"):
                hidden = True
                break
            q = q.getparent()
        if not hidden:
            return r
    return None


def inherited_rpr(anchor: etree._Element) -> Optional[etree._Element]:
    """The run properties the new paragraph's run inherits: the anchor's first live text run's rPr minus rPrChange."""
    run = first_live_text_run(anchor)
    rpr = run.find(W + "rPr") if run is not None else None
    if rpr is None:
        return None
    c = copy.deepcopy(rpr)
    for child in list(c):
        if child.tag == W + "rPrChange":
            c.remove(child)
    return c if (len(c) or c.attrib) else None


@register
class InsertParagraph(Op):
    name = "insert_paragraph"
    mode = "content"
    description = ("Insert a paragraph containing the user's approved text verbatim before or after anchor "
                   "paragraphs selected by a targeted predicate, tracked (default) or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Approved paragraph text to insert, verbatim (non-empty)."},
            "position": {"type": "string", "enum": ["after", "before"], "default": "after",
                         "description": "Insert 'after' or 'before' each matched anchor paragraph."},
            "style": {"type": ["string", "null"], "default": "inherit",
                      "description": "Paragraph style ID, or 'inherit' to copy the anchor paragraph's pStyle."},
            "formatting": {"type": "string", "enum": ["inherit", "none"], "default": "inherit",
                           "description": "inherit = the new paragraph copies the anchor's paragraph properties (style, "
                                          "indents, spacing, tabs, list numbering; never its section break or pending "
                                          "revisions) and the run properties of the anchor's first text run (font, size, "
                                          "bold…), so it looks like its neighbour even in directly formatted documents; "
                                          "none = only the paragraph style (see 'style')."},
            "tracked": {"type": "boolean", "default": True,
                        "description": "true = tracked insertion (content mode); false = untracked insertion."},
            "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)."},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "If true, matching 0 anchors is a no-op instead of an exception."},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["text"],
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": ["document"]},
            "paragraphs": {"type": "object", "description": "Targeted selector identifying the anchor paragraph(s)."},
        },
    }
    verification_profile = {
        "V2": "one BlockDiff per inserted paragraph (before=None, after=text)",
        "V4": "tracked: +1 ins and +1 ins_mark per inserted paragraph; untracked: 0",
        "V5b": "tracked: reject-all view equals source (inserted mark merges empty rejected paragraph); untracked: enumerated list",
        "V8": "tracked: accept-all view includes the inserted paragraph at its exact output index",
        "V10": "inserted_paragraphs assertion checks anchor count, output index, text, style, inherited paragraph and run "
               "properties (re-derived from the anchor), revision grammar, and residual identity",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise JobException("'text' must be a non-empty string", "bad-params")
        position = params.get("position", "after")
        if position not in ("after", "before"):
            raise JobException(f"position must be 'after' or 'before', got {position!r}", "bad-params")
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"unknown selector key(s) {sorted(unknown)}", "bad-params")
        para_pred = select.get("paragraphs")
        check_paragraph_selector(para_pred)
        if not is_targeted(select):
            raise JobException("insert_paragraph requires a targeted 'paragraphs' selector naming the anchor(s)",
                               "bad-params")
        tracked = bool(params.get("tracked", True))
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        style = params.get("style", "inherit")
        formatting = params.get("formatting", "inherit")
        if formatting not in ("inherit", "none"):
            raise JobException(f"formatting must be 'inherit' or 'none', got {formatting!r}", "bad-params")
        scope = select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        parts = scope_parts(pkg, scope)
        sheet = StyleSheet(pkg)
        if style not in (None, "inherit") and style not in sheet.styles:
            raise JobException(f"style {style!r} does not exist in styles.xml", "bad-params", {"style": style})

        frag = self.fragment({"scope": scope, "paragraphs": para_pred},
                             {**params, "position": position, "style": style, "formatting": formatting, "tracked": tracked,
                              "author": author},
                             mode=("content" if tracked else "untracked"))
        frag.untracked_diffs_expected = not tracked

        anchors_by_part: Dict[str, List[Dict[str, Any]]] = {}
        total = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            matched: List[Dict[str, Any]] = []
            for bi, p in enumerate(pars):
                btxt = block_full_text(p)
                if not paragraph_selected(para_pred, p, bi, btxt, sheet, all_pars=pars):
                    continue
                if nearest_p(p) is not None:
                    raise JobException(f"{part} paragraph {bi} is inside a textbox; not supported", "textbox")
                if position == "after" and p.find(f"{W}pPr/{W}sectPr") is not None:
                    raise JobException(f"{part} anchor paragraph {bi} carries a section break (w:sectPr); "
                                       f"insert 'before' it or select a paragraph in the next section",
                                       "section-break", {"part": part, "paragraph": bi})
                ps = p.find(f"{W}pPr/{W}pStyle")
                anchor_style = ps.get(W + "val") if ps is not None else None
                eff_style = anchor_style if style in (None, "inherit") else style
                k = len(matched)
                out_bi = (bi + 1 + k) if position == "after" else (bi + k)
                frag.diffs.append(BlockDiff(
                    part=part,
                    block_index=out_bi,
                    before=None,
                    after=text,
                    accepted_view=text if tracked else None,
                    note=f"inserted {position} block {bi} ({'tracked' if tracked else 'untracked'})",
                ))
                matched.append({"anchor_index": bi, "out_index": out_bi, "style": eff_style})
                total += 1
            if matched:
                anchors_by_part[part] = matched
                frag.touches.append((part, "*", "text"))
                if part not in frag.intended_parts:
                    frag.intended_parts.append(part)

        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append(f"0 anchor paragraphs matched in {scope}; no-op.")
                return frag
            raise JobException(f"no anchor paragraphs matched selector {para_pred} in {scope}", "not-found",
                               {"scope": scope, "paragraphs": para_pred})

        if tracked:
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + total
            frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + total
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = 2 * total
        else:
            frag.flags.append(f"UNTRACKED insertion of {total} paragraph(s) at the user's request.")

        frag.geometry_reference = "none"
        frag.geometry_reason = "inserting paragraph(s) reflows following content"
        frag.property_assertions.append({
            "kind": "inserted_paragraphs",
            "scope": scope,
            "select": {"paragraphs": para_pred},
            "position": position,
            "text": text,
            "style": style,
            "formatting": formatting,
            "tracked": tracked,
            "author": author,
            "expected_anchors": total,
        })
        frag.transform_data = {
            "anchors": anchors_by_part,
            "text": text,
            "position": position,
            "formatting": formatting,
            "tracked": tracked,
            "author": author,
            "use_date_utc": file_uses_date_utc(pkg),
            "first_rev_id": frag.rev_id_base,
            "date_convention": params.get("date_convention", "word-local-z"),
        }
        frag.notes.append(f"insert_paragraph: {total} paragraph(s) {position} anchor(s) "
                          f"({'tracked' if tracked else 'untracked'}, formatting {formatting}) across "
                          f"{len(frag.intended_parts)} part(s).")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("anchors"):
            return
        tracked = td["tracked"]
        text = td["text"]
        position = td["position"]
        inherit = td.get("formatting", "inherit") == "inherit"
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]

        for part, items in td["anchors"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for item in reversed(items):
                anchor = pars[item["anchor_index"]]
                new_p = etree.Element(W + "p")
                ppr = inherited_ppr(anchor) if inherit else None
                if ppr is not None:
                    new_p.append(ppr)
                if item.get("style"):                      # the anchor's own style id, or the explicit one
                    ppr = paragraph_ppr(new_p)
                    ps = ppr.find(W + "pStyle")
                    if ps is None:
                        ps = insert_in_order(ppr, etree.Element(W + "pStyle"), PPR_ORDER)
                    ps.set(W + "val", item["style"])
                run_rpr = inherited_rpr(anchor) if inherit else None
                if tracked:
                    mrpr = mark_rpr(new_p, create=True)
                    ins_mark = etree.Element(W + "ins")
                    stamp.apply(ins_mark, next_id[0]); next_id[0] += 1
                    insert_in_order(mrpr, ins_mark, RPR_ORDER)
                    ins_wrap = etree.SubElement(new_p, W + "ins")
                    stamp.apply(ins_wrap, next_id[0]); next_id[0] += 1
                    r = etree.SubElement(ins_wrap, W + "r")
                else:
                    r = etree.SubElement(new_p, W + "r")
                if run_rpr is not None:
                    r.append(run_rpr)
                t = etree.SubElement(r, W + "t")
                set_text(t, text)
                if position == "after":
                    anchor.addnext(new_p)
                else:
                    anchor.addprevious(new_p)
            pkg.write_xml(part, root)
