"""Op: insert_paragraph — insert new paragraphs containing the user's approved wording verbatim, anchored
before or after paragraphs matching a targeted selector, as a tracked insertion (default) or untracked.

The wording (exactly one of, built-in batch 6 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §4.1–4.2; ``new_paragraphs``):

* ``text`` — one paragraph of one run;
* ``runs: [{text, b?, i?, u?, vertAlign?}]`` — one paragraph of segments, a run each, in order: a segment's own
  properties are direct formatting over the inherited run properties (set_properties' writer: ``b``/``i`` as the pairs
  Word writes, ``w:u`` keeping the inherited underline's other attributes, ``w:vertAlign`` exactly), created in schema
  order;
* ``paragraphs: [{text | runs, style?}]`` — several paragraphs, inserted in that order at each anchor; a paragraph's
  own ``style`` replaces the op's ``style`` for it.

Every text is the user's, verbatim: a ``\\t`` in it is written as a ``w:tab`` and a ``\\n`` as a ``w:br`` (a line break
in the same paragraph) where they stand, the characters between them in ``w:t`` elements of their own (the edit core's
``textedit.text_children``); no other control character is accepted, and every paragraph holds a visible character.
Tabs and line breaks are no block text: V2's after-text of a new paragraph leaves them out.

Formatting (``formatting: "inherit"``, the default): each new paragraph looks like its anchor. Definition shared
with the verifier (verify/props.py re-implements it; CLAUDE.md "Other definitions that must stay identical"):

* paragraph properties = a copy of the anchor's ``w:pPr`` without ``w:sectPr`` and ``w:pPrChange``, and without the
  revision marks (``w:ins``/``w:del``/``w:moveFrom``/``w:moveTo``) and ``w:rPrChange`` inside its paragraph-mark
  ``w:rPr`` (an emptied ``w:rPr`` is dropped); an explicit ``style`` replaces the copied ``w:pStyle``;
* run properties = a copy of the ``w:rPr`` of the anchor's first live text run (a ``w:r`` whose nearest ``w:p`` is
  the anchor, not inside ``w:del``/``w:moveFrom``, holding a ``w:t``) without ``w:rPrChange`` — every run of a new
  paragraph starts from it.

Nothing is synthesized: every property comes from the anchor or a segment's own words. ``formatting: "none"`` gives
the pre-0.3.1 behaviour (only the paragraph style, and the segments' own properties). Tracked: each new paragraph's mark
carries a ``w:ins`` and its runs sit in one ``w:ins`` (+1 ins, +1 ins_mark per paragraph).
"""
from __future__ import annotations

import copy
import getpass
import os
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import (PPR_ORDER, RPR_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, insert_in_order,
                     mark_rpr, max_revision_id, paragraph_ppr, paragraphs)
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .effective import UNDERLINES, VERT_ALIGNS, run_elements
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .set_properties import write_element
from .slots import nearest_p
from .textedit import control_characters, shown_text, text_children


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


WORDING = ("text", "runs", "paragraphs")
SEGMENT_PROPS = ("b", "i", "u", "vertAlign")


def _segment(seg: Any, where: str) -> List[Any]:
    """One ``runs`` entry, validated: [text, {its own run properties}]."""
    if not isinstance(seg, dict) or "text" not in seg or set(seg) - {"text", *SEGMENT_PROPS}:
        raise JobException(f"{where} must be {{text, b?, i?, u?, vertAlign?}}, got {seg!r}", "bad-params")
    props = {k: v for k, v in seg.items() if k != "text"}
    for k in ("b", "i"):
        if k in props and not isinstance(props[k], bool):
            raise JobException(f"{where}.{k} must be true or false", "bad-params")
    if "u" in props and props["u"] not in UNDERLINES:
        raise JobException(f"{where}.u must be one of {UNDERLINES}", "bad-params")
    if "vertAlign" in props and props["vertAlign"] not in VERT_ALIGNS:
        raise JobException(f"{where}.vertAlign must be one of {VERT_ALIGNS}", "bad-params")
    if not isinstance(seg["text"], str) or not seg["text"]:
        raise JobException(f"{where}.text must be a non-empty string", "bad-params")
    return [seg["text"], props]


def _wording(src: Dict[str, Any], where: str) -> List[List[Any]]:
    """The segments of one paragraph — [[text, props], …] — from its ``text`` or ``runs``, validated."""
    if "text" in src:
        if not isinstance(src["text"], str):
            raise JobException(f"'{where}text' must be a string", "bad-params")
        segs = [[src["text"], {}]]
    else:
        runs = src["runs"]
        if not isinstance(runs, list) or not runs:
            raise JobException(f"'{where}runs' must be a non-empty list of {{text, b?, i?, u?, vertAlign?}}",
                               "bad-params")
        segs = [_segment(s, f"{where}runs[{k}]") for k, s in enumerate(runs)]
    text = "".join(t for t, _ in segs)
    label = f"'{where}{'text' if 'text' in src else 'runs'}'"
    bad = control_characters(text, breaks=True)
    if bad:
        raise JobException(f"{label} holds a control character other than a tab (\\t) or a line break (\\n): "
                           f"{bad[0]!r}", "bad-params")
    if not text.strip():
        raise JobException(f"{label} must hold some text (not only spaces, tabs or line breaks)", "bad-params")
    return segs


def new_paragraphs(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The paragraphs the op places, in order, from the user's words (module docstring), validated:
    [{"segments": [[text, props], …], "style"?: …}] — "style" only where a ``paragraphs`` entry names its own."""
    given = [k for k in WORDING if k in params]
    if len(given) != 1:
        raise JobException("give the wording as exactly one of 'text', 'runs' or 'paragraphs'", "bad-params")
    if given[0] != "paragraphs":
        return [{"segments": _wording(params, "")}]
    items = params["paragraphs"]
    if not isinstance(items, list) or not items:
        raise JobException("'paragraphs' must be a non-empty list of {text | runs, style?}", "bad-params")
    out: List[Dict[str, Any]] = []
    for k, item in enumerate(items):
        if not isinstance(item, dict) or set(item) - {"text", "runs", "style"} or ("text" in item) == ("runs" in item):
            raise JobException(f"paragraphs[{k}] must be {{text | runs, style?}} (one of text and runs), got {item!r}",
                               "bad-params")
        para: Dict[str, Any] = {"segments": _wording(item, f"paragraphs[{k}].")}
        if "style" in item:
            if item["style"] is not None and not isinstance(item["style"], str):
                raise JobException(f"paragraphs[{k}].style must be a style ID or 'inherit'", "bad-params")
            para["style"] = item["style"]
        out.append(para)
    return out


def segment_rpr(base: Optional[etree._Element], props: Dict[str, Any]) -> Optional[etree._Element]:
    """A segment's run properties: a copy of the inherited ones (``base``, or none) with the segment's own ``b``, ``i``,
    ``u`` and ``vertAlign`` written over them as direct formatting (set_properties' writer, in schema order)."""
    asked = run_elements(props)
    rpr = copy.deepcopy(base) if base is not None else None
    if asked and rpr is None:
        rpr = etree.Element(W + "rPr")
    for e, v in asked.items():
        write_element(rpr, e, v, RPR_ORDER)
    return rpr


@register
class InsertParagraph(Op):
    name = "insert_paragraph"
    mode = "content"
    description = ("Insert one or more paragraphs containing the user's approved text verbatim before or after anchor "
                   "paragraphs selected by a targeted predicate, tracked (default) or untracked. A tab (\\t) and a line "
                   "break (\\n) in the text are written as Word's own; segments may be bold, italic, underlined or "
                   "superscript/subscript.")
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Approved paragraph text to insert, verbatim (non-empty); \\t = a "
                                                      "tab, \\n = a line break in the same paragraph. Give exactly one "
                                                      "of text, runs and paragraphs."},
            "runs": {"type": "array", "description": "One paragraph of segments, in order, each the user's words "
                                                     "verbatim with its own formatting over the inherited one: "
                                                     "b/i (true/false), u (an underline or 'none'), vertAlign "
                                                     "(superscript|subscript|baseline).",
                     "items": {"type": "object", "properties": {
                         "text": {"type": "string"}, "b": {"type": "boolean"}, "i": {"type": "boolean"},
                         "u": {"type": "string", "enum": list(UNDERLINES)},
                         "vertAlign": {"type": "string", "enum": list(VERT_ALIGNS)}}}},
            "paragraphs": {"type": "array", "description": "Several paragraphs, inserted in this order at each anchor: "
                                                           "each {text} or {runs} (as above) and optionally its own "
                                                           "style (a style ID, or 'inherit').",
                           "items": {"type": "object", "properties": {
                               "text": {"type": "string"},
                               "runs": {"type": "array", "items": {"type": "object", "properties": {
                                   "text": {"type": "string"}, "b": {"type": "boolean"}, "i": {"type": "boolean"},
                                   "u": {"type": "string", "enum": list(UNDERLINES)},
                                   "vertAlign": {"type": "string", "enum": list(VERT_ALIGNS)}}}},
                               "style": {"type": ["string", "null"]}}}},
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
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": ["document"]},
            "paragraphs": {"type": "object", "description": "Targeted selector identifying the anchor paragraph(s)."},
        },
    }
    verification_profile = {
        "V2": "one BlockDiff per inserted paragraph (before=None, after=its text; tabs and line breaks are no text)",
        "V4": "tracked: +1 ins and +1 ins_mark per inserted paragraph; untracked: 0",
        "V5b": "tracked: reject-all view equals source (inserted mark merges empty rejected paragraph); untracked: enumerated list",
        "V8": "tracked: accept-all view includes the inserted paragraph at its exact output index",
        "V10": "inserted_paragraphs assertion re-derives the paragraphs from the user's words and checks anchor count, "
               "output indices, text, style, inherited paragraph and run properties (re-derived from the anchor), each "
               "segment's run (properties, w:t / w:tab / w:br in order), revision grammar, and residual identity",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        news = new_paragraphs(params)
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
        styles = [para.get("style", style) for para in news]          # each new paragraph's own or the op's
        for s in [style] + styles:
            if s not in (None, "inherit") and s not in sheet.styles:
                raise JobException(f"style {s!r} does not exist in styles.xml", "bad-params", {"style": s})
        texts = [shown_text("".join(t for t, _ in para["segments"])) for para in news]
        n = len(news)

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
                if position == "after" and any(q is not p for q in p.iter(W + "p")):
                    # its text box's own paragraphs come between it and anything inserted after it
                    raise JobException(f"{part} anchor paragraph {bi} holds a text box; insert 'before' it",
                                       "textbox", {"part": part, "paragraph": bi})
                ps = p.find(f"{W}pPr/{W}pStyle")
                anchor_style = ps.get(W + "val") if ps is not None else None
                k = len(matched)
                # the k earlier anchors of this part each put n paragraphs before this one's
                first = (bi + 1 + k * n) if position == "after" else (bi + k * n)
                for j, text in enumerate(texts):
                    frag.diffs.append(BlockDiff(
                        part=part,
                        block_index=first + j,
                        before=None,
                        after=text,
                        accepted_view=text if tracked else None,
                        note=f"inserted {position} block {bi}" + (f", paragraph {j + 1} of {n}" if n > 1 else "")
                             + f" ({'tracked' if tracked else 'untracked'})",
                    ))
                matched.append({"anchor_index": bi, "out_index": first,
                                "styles": [anchor_style if s in (None, "inherit") else s for s in styles]})
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

        for s in dict.fromkeys(s for s in styles if s not in (None, "inherit")):
            if sheet.part:
                # the new paragraphs name this style: another op removing it (remove_unused_styles) is a plan-time conflict
                frag.touches.append((sheet.part, f"style:{s}", "verify"))
        if tracked:
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + total * n
            frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + total * n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = 2 * total * n
        else:
            frag.flags.append(f"UNTRACKED insertion of {total * n} paragraph(s) at the user's request.")

        frag.geometry_reference = "none"
        frag.geometry_reason = "inserting paragraph(s) reflows following content"
        frag.property_assertions.append({
            "kind": "inserted_paragraphs",
            "scope": scope,
            "select": {"paragraphs": para_pred},
            "position": position,
            **{k: copy.deepcopy(params[k]) for k in WORDING if k in params},     # the user's own words
            "style": style,
            "formatting": formatting,
            "tracked": tracked,
            "author": author,
            "expected_anchors": total,
        })
        frag.transform_data = {
            "anchors": anchors_by_part,
            "paragraphs": [para["segments"] for para in news],
            "position": position,
            "formatting": formatting,
            "tracked": tracked,
            "author": author,
            "use_date_utc": file_uses_date_utc(pkg),
            "first_rev_id": frag.rev_id_base,
            "date_convention": params.get("date_convention", "word-local-z"),
        }
        frag.notes.append(f"insert_paragraph: {total * n} paragraph(s) ({n} per anchor) {position} {total} anchor(s) "
                          f"({'tracked' if tracked else 'untracked'}, formatting {formatting}) across "
                          f"{len(frag.intended_parts)} part(s).")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("anchors"):
            return
        tracked = td["tracked"]
        position = td["position"]
        inherit = td.get("formatting", "inherit") == "inherit"
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]

        for part, items in td["anchors"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for item in reversed(items):
                anchor = pars[item["anchor_index"]]
                news = [self._new_paragraph(anchor, segments, item["styles"][j], inherit, tracked, stamp, next_id)
                        for j, segments in enumerate(td["paragraphs"])]
                if position == "after":
                    for new_p in reversed(news):            # each right after the anchor: the first ends first
                        anchor.addnext(new_p)
                else:
                    for new_p in news:
                        anchor.addprevious(new_p)
            pkg.write_xml(part, root)

    @staticmethod
    def _new_paragraph(anchor, segments: List[List[Any]], style: Optional[str], inherit: bool, tracked: bool,
                       stamp: RevisionStamp, next_id: List[int]) -> etree._Element:
        """One new paragraph: the anchor's inherited properties (``style`` its pStyle when given), a run per segment —
        the inherited run properties with the segment's own over them, its text as ``text_children`` — tracked: the mark
        and the runs (in one ``w:ins``) inserted."""
        new_p = etree.Element(W + "p")
        ppr = inherited_ppr(anchor) if inherit else None
        if ppr is not None:
            new_p.append(ppr)
        if style:                                       # the anchor's own style id, or the explicit one
            ppr = paragraph_ppr(new_p)
            ps = ppr.find(W + "pStyle")
            if ps is None:
                ps = insert_in_order(ppr, etree.Element(W + "pStyle"), PPR_ORDER)
            ps.set(W + "val", style)
        base = inherited_rpr(anchor) if inherit else None
        holder = new_p
        if tracked:
            mrpr = mark_rpr(new_p, create=True)
            ins_mark = etree.Element(W + "ins")
            stamp.apply(ins_mark, next_id[0]); next_id[0] += 1
            insert_in_order(mrpr, ins_mark, RPR_ORDER)
            holder = etree.SubElement(new_p, W + "ins")
            stamp.apply(holder, next_id[0]); next_id[0] += 1
        for text, props in segments:
            r = etree.SubElement(holder, W + "r")
            rpr = segment_rpr(base, props)
            if rpr is not None:
                r.append(rpr)
            for el in text_children(text):
                r.append(el)
        return new_p

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        what, listed = _wording_words(params)
        position = "before" if params.get("position") == "before" else "after"
        subject = prose.where(select)[3:]                      # "every paragraph …" or "paragraphs in the “PR1” style …"
        if subject.startswith("every paragraph"):
            anchor = "each anchor paragraph" + subject[len("every paragraph"):]
        else:
            anchor = "each of the " + subject
        clauses: List[str] = []
        style = params.get("style", "inherit")
        if style not in (None, "inherit"):
            clauses.append(f"in {prose.style(style)}")
        if params.get("formatting") == "none":
            clauses.append("without copying that paragraph's formatting")
        elif clauses:
            clauses.append("otherwise formatted like that paragraph")
        else:
            clauses.append("formatted like that paragraph")
        if bool(params.get("tracked", True)):
            tail = "shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        else:
            tail = "without tracked changes"
        first = (f"Insert {what} {position} {anchor}" + (f", reading in this order: {listed}" if listed else "")
                 + ", " + ", ".join(clauses) + f", {tail}.")
        second = "Files with no such paragraph are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)


# how a segment looks, in the plan's words after its quoted text (``“Note:” in bold and underlined``)
SEGMENT_WORDS = {("b", True): "in bold", ("b", False): "not bold", ("i", True): "in italic", ("i", False): "not italic",
                 ("u", "single"): "underlined", ("u", "double"): "double-underlined",
                 ("u", "words"): "underlined word by word", ("u", "dotted"): "with a dotted underline",
                 ("u", "dash"): "with a dashed underline", ("u", "wave"): "with a wavy underline",
                 ("u", "none"): "not underlined", ("vertAlign", "superscript"): "in superscript",
                 ("vertAlign", "subscript"): "in subscript", ("vertAlign", "baseline"): "on the baseline"}


def _segments_words(runs: Any) -> str:
    """A paragraph of segments as the plan reads it: its whole text quoted, then each formatted segment —
    ``“Note: Provide …” (“Note:” in bold)``."""
    if not isinstance(runs, list) or not runs or not all(isinstance(s, dict) for s in runs):
        return "the approved wording"
    whole = "".join(str(s.get("text", "")) for s in runs)
    looks: List[str] = []
    for s in runs:
        words = [SEGMENT_WORDS[(k, s[k])] for k in SEGMENT_PROPS
                 if isinstance(s.get(k), (str, bool)) and (k, s[k]) in SEGMENT_WORDS]
        if words:
            looks.append(f"{prose.typed(str(s.get('text', '')))} {prose.join(words)}")
    return prose.typed(whole) + (f" ({'; '.join(looks)})" if looks else "")


def _paragraph_words(item: Any) -> str:
    if isinstance(item, dict) and "runs" in item:
        words = _segments_words(item["runs"])
    elif isinstance(item, dict) and isinstance(item.get("text"), str) and item["text"]:
        words = prose.typed(item["text"])
    else:
        words = "the approved wording"
    style = item.get("style") if isinstance(item, dict) else None
    return words + (f" in {prose.style(style)}" if style not in (None, "inherit") else "")


def _wording_words(params: Dict[str, Any]) -> Tuple[str, str]:
    """What the op inserts, in the plan's words: (``a new paragraph reading “…”``, "") — or, for several paragraphs,
    (``3 new paragraphs``, ``“…”; then “…”; then “…”``), the list said after the anchor."""
    items = params.get("paragraphs")
    if isinstance(items, list) and items:
        if len(items) == 1:
            return f"a new paragraph reading {_paragraph_words(items[0])}", ""
        return f"{len(items)} new paragraphs", "; then ".join(_paragraph_words(x) for x in items)
    if "runs" in params:
        return f"a new paragraph reading {_segments_words(params['runs'])}", ""
    text = params.get("text")
    return (f"a new paragraph reading {prose.typed(text)}" if isinstance(text, str) and text else
            "a new paragraph with the approved wording"), ""
