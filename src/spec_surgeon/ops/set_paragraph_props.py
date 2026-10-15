"""Op: set_paragraph_props — spacing, indentation and alignment on selected paragraphs.

Values are merged into the existing w:spacing / w:ind elements (lesson: a style's ``before="240"`` must
survive a change to ``after``); elements are created in the complete pPr schema order, so a section-break
paragraph keeps its w:sectPr after them. Setting ``firstLine`` removes ``hanging`` and vice versa (they are
mutually exclusive in Word). Setting before/after on a paragraph whose spacing is "auto" is refused — the auto
flag would silently override the value.

Tracked or plain (decision §14.1): plain for whole-document selections, tracked (w:pPrChange holding the old
pPr) when the selector narrows to particular paragraphs; ``tracked`` overrides.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add, max_revision_id,
                     paragraph_ppr, paragraphs, property_change)
from . import register
from .base import JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts

SPACING_KEYS = {"before": int, "after": int, "line": int, "lineRule": str, "beforeLines": int, "afterLines": int}
IND_KEYS = {"left": int, "right": int, "start": int, "end": int, "firstLine": int, "hanging": int}
JC_VALUES = ("left", "center", "right", "both", "distribute", "start", "end")
LINE_RULES = ("auto", "exact", "atLeast")
TRUE = ("1", "true", "on")


def _clean(params: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name, keys in (("spacing", SPACING_KEYS), ("ind", IND_KEYS)):
        if params.get(name) is None:
            continue
        v = params[name]
        if not isinstance(v, dict) or not v:
            raise JobException(f"'{name}' must be a non-empty object", "bad-params")
        unknown = set(v) - set(keys)
        if unknown:
            raise JobException(f"unknown {name} attribute(s) {sorted(unknown)}; known: {sorted(keys)}", "bad-params")
        clean = {}
        for k, val in v.items():
            if keys[k] is int:
                if isinstance(val, bool) or not isinstance(val, int):
                    raise JobException(f"{name}.{k} must be an integer (twentieths of a point)", "bad-params")
                clean[k] = str(val)
            else:
                if val not in LINE_RULES:
                    raise JobException(f"spacing.lineRule must be one of {LINE_RULES}", "bad-params")
                clean[k] = val
        out[name] = clean
    if "ind" in out and "firstLine" in out["ind"] and "hanging" in out["ind"]:
        raise JobException("ind.firstLine and ind.hanging are mutually exclusive", "bad-params")
    if params.get("jc") is not None:
        if params["jc"] not in JC_VALUES:
            raise JobException(f"jc must be one of {JC_VALUES}", "bad-params")
        out["jc"] = params["jc"]
    if not out:
        raise JobException("nothing to set: give spacing, ind and/or jc", "bad-params")
    return out


@register
class SetParagraphProps(Op):
    name = "set_paragraph_props"
    mode = "formatting"
    description = "Set spacing (before/after/line), indentation and alignment on selected paragraphs."
    params_schema = {"type": "object", "properties": {
        "spacing": {"type": "object", "description": "before/after/line (twips), lineRule auto|exact|atLeast, "
                                                     "beforeLines/afterLines"},
        "ind": {"type": "object", "description": "left/right/start/end/firstLine/hanging (twips)"},
        "jc": {"type": "string", "enum": list(JC_VALUES)},
        "tracked": {"type": "boolean", "description": "w:pPrChange; default: plain for whole-document selections, "
                                                      "tracked when paragraphs are targeted"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes", "endnotes", "comments"]}, "default": ["document"]},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range}"}}}
    verification_profile = {"V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per paragraph",
                            "V10": "paragraph_props: selection re-resolved, values merged, snapshot == source pPr",
                            "V7": "none — spacing/indent/alignment move text"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope", "paragraphs"}:
            raise JobException(f"set_paragraph_props: unknown selector key(s) {sorted(set(select) - {'scope', 'paragraphs'})}",
                               "bad-params")
        values = _clean(params)
        pred = select.get("paragraphs", "all")
        check_paragraph_selector(pred)
        scope = select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        tracked = bool(params.get("tracked", is_targeted(select)))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, List[int]] = {}
        older = 0
        for part in parts:
            pars = paragraphs(pkg.xml(part))
            chosen = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet):
                    continue
                ppr = p.find(W + "pPr")
                sp = ppr.find(W + "spacing") if ppr is not None else None
                ind = ppr.find(W + "ind") if ppr is not None else None
                sv = values.get("spacing", {})
                for side in ("before", "after"):
                    if side in sv and sp is not None and sp.get(W + side + "Autospacing") in TRUE:
                        raise JobException(f"{part} paragraph {bi}: spacing {side} is 'auto' "
                                           f"({side}Autospacing); the value would be ignored", "conflict-property",
                                           {"part": part, "paragraph": bi})
                    # a spacing given in lines (beforeLines/afterLines, hundredths of a line) wins over the point
                    # value: "6 pt after" written beside afterLines="200" is two lines after, with a PASS — refused
                    # unless the spec sets the lines value too (or the user clears it in Word first)
                    if side in sv and f"{side}Lines" not in sv:
                        lines_at = None
                        if sp is not None and sp.get(W + side + "Lines") not in (None, ""):
                            lines_at = f"the paragraph itself ({side}Lines={sp.get(W + side + 'Lines')})"
                        else:
                            for st in sheet.lineage(sheet.paragraph_style_id(p)):
                                ssp = st.find(W + "pPr/" + W + "spacing")
                                if ssp is not None and ssp.get(W + side + "Lines") not in (None, ""):
                                    lines_at = f"style {st.get(W + 'styleId')!r} ({side}Lines={ssp.get(W + side + 'Lines')})"
                                    break
                        if lines_at:
                            raise JobException(f"{part} paragraph {bi}: spacing {side} is set in lines on {lines_at}; "
                                               f"Word uses the line value and would ignore the point value — clear "
                                               f"{side}Lines in Word first, or set {side}Lines in this spec as well",
                                               "conflict-property", {"part": part, "paragraph": bi})
                iv = values.get("ind", {})
                if ind is not None:
                    for a, b in (("left", "start"), ("right", "end"), ("start", "left"), ("end", "right")):
                        if a in iv and ind.get(W + b) is not None:
                            raise JobException(f"{part} paragraph {bi}: indentation uses w:{b}; set {b} instead of {a}",
                                               "conflict-property", {"part": part, "paragraph": bi})
                if ppr is not None and ppr.find(W + "pPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part} paragraph {bi} already carries a pending tracked paragraph "
                                           f"change (w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                    older += 1
                chosen.append(bi)
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), "pPr") for bi in chosen]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
        n = sum(len(v) for v in targets.values())
        frag.property_assertions.append({
            "kind": "paragraph_props", "scope": [scope] if isinstance(scope, str) else list(scope), "parts": parts,
            "select": {"paragraphs": pred}, "set": values, "tracked": tracked, "author": author,
            "expected_paragraphs": n})
        frag.transform_data = {"targets": targets, "values": values, "tracked": tracked, "author": author}
        if tracked and n:
            frag.rev_delta["pPrChange"] = n
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if n:
            frag.geometry_reference = "none"
            frag.geometry_reason = "spacing/indentation/alignment changes move text by design"
        frag.notes.append(f"{n} paragraph(s) → {values}; {'tracked (w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if older:
            frag.flags.append(f"{older} changed paragraph(s) already carried a pending tracked paragraph change "
                              f"(w:pPrChange); reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        values = td["values"]
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, idxs in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for bi in idxs:
                ppr = paragraph_ppr(pars[bi])
                chg = None
                if stamp:
                    snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
                    chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
                    next_id += 1
                if "spacing" in values:
                    sp = get_or_add(ppr, "spacing", PPR_ORDER)
                    for k, v in values["spacing"].items():
                        sp.set(W + k, v)
                if "ind" in values:
                    ind = get_or_add(ppr, "ind", PPR_ORDER)
                    iv = values["ind"]
                    if "firstLine" in iv:
                        ind.attrib.pop(W + "hanging", None); ind.attrib.pop(W + "hangingChars", None)
                    if "hanging" in iv:
                        ind.attrib.pop(W + "firstLine", None); ind.attrib.pop(W + "firstLineChars", None)
                    for k, v in iv.items():
                        ind.set(W + k, v)
                if "jc" in values:
                    get_or_add(ppr, "jc", PPR_ORDER).set(W + "val", values["jc"])
                if chg is not None:
                    ppr.append(chg)
            pkg.write_xml(part, root)
