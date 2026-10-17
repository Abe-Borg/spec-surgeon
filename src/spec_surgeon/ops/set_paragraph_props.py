"""Op: set_paragraph_props — spacing, indentation and alignment on selected paragraphs.

Values are merged into the existing w:spacing / w:ind elements (lesson: a style's ``before="240"`` must
survive a change to ``after``); elements are created in the complete pPr schema order, so a section-break
paragraph keeps its w:sectPr after them. Setting ``firstLine`` removes ``hanging`` and vice versa (they are
mutually exclusive in Word). Setting before/after on a paragraph whose spacing is "auto" is refused — the auto
flag would silently override the value.

Tracked or plain (decision §14.1): plain for whole-document selections, tracked (w:pPrChange holding the old
pPr) when the selector narrows to particular paragraphs; ``tracked`` overrides.

Style definitions (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §2): ``select: {"styles": {"ids": [...]}}`` —
alone, paragraph styles only — merges the same values into each style's OWN w:pPr (``merged_attrs``, the merge the
paragraphs get). Never tracked. A style whose own values already are the asked ones is left alone. Refused
(``conflict-property``, as for paragraphs): a side asked in points where the style's own spacing has ``…Autospacing``
on, or where the style, its basedOn ancestors or docDefaults set ``beforeLines``/``afterLines`` (unless the spec sets
the lines too); an ``ind`` alias whose other spelling the style's own w:ind uses. Paragraphs whose own direct
formatting keeps its value are flagged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, STYLE_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add,
                     max_revision_id, paragraph_ppr, paragraphs, property_change)
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


def merged_attrs(name: str, el, val) -> Dict[str, str]:
    """The attributes (Clark names) a w:spacing / w:ind / w:jc ends with: the asked ones set, the others kept —
    ``firstLine`` drops ``hanging``/``hangingChars`` and vice versa; w:jc gets w:val."""
    attrs = dict(el.attrib) if el is not None else {}
    if name == "jc":
        attrs[W + "val"] = val
        return attrs
    if name == "ind":
        if "firstLine" in val:
            attrs.pop(W + "hanging", None); attrs.pop(W + "hangingChars", None)
        if "hanging" in val:
            attrs.pop(W + "firstLine", None); attrs.pop(W + "firstLineChars", None)
    for k, v in val.items():
        attrs[W + k] = v
    return attrs


def write_values(ppr, values: Dict[str, Any]) -> None:
    """Merge the asked values into a pPr (a paragraph's or a style's), elements created in schema order."""
    for name in ("spacing", "ind", "jc"):
        if name in values:
            el = get_or_add(ppr, name, PPR_ORDER)
            new = merged_attrs(name, el, values[name])
            el.attrib.clear()
            for k, v in new.items():
                el.set(k, v)


def style_refusal(sheet: StyleSheet, sid: str, values: Dict[str, Any]) -> Any:
    """Why a style cannot take the values as asked — (message) — or None: a side asked in points where the style's own
    spacing is auto, or where the style, its basedOn ancestors or docDefaults give it in lines (unless the lines are
    asked too); an indentation alias whose other spelling the style's own w:ind uses."""
    ppr = sheet.styles[sid].find(W + "pPr")
    sp = ppr.find(W + "spacing") if ppr is not None else None
    ind = ppr.find(W + "ind") if ppr is not None else None
    sv, iv = values.get("spacing", {}), values.get("ind", {})
    dd = sheet.root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr") if sheet.root is not None else None
    for side in ("before", "after"):
        if side in sv and sp is not None and sp.get(W + side + "Autospacing") in TRUE:
            return f"spacing {side} of style {sid!r} is 'auto' ({side}Autospacing); the value would be ignored"
        if side in sv and f"{side}Lines" not in sv:
            levels = [(f"style {st.get(W + 'styleId')!r}", st.find(W + "pPr/" + W + "spacing"))
                      for st in sheet.lineage(sid)] + [("the document defaults", dd.find(W + "spacing") if dd is not None
                                                        else None)]
            for label, ssp in levels:
                if ssp is not None and ssp.get(W + side + "Lines") not in (None, ""):
                    return (f"spacing {side} of style {sid!r} is set in lines on {label} ({side}Lines="
                            f"{ssp.get(W + side + 'Lines')}); Word uses the line value and would ignore the point value "
                            f"— set {side}Lines in this spec as well, or clear it in Word first")
    if ind is not None:
        for a, b in (("left", "start"), ("right", "end"), ("start", "left"), ("end", "right")):
            if a in iv and ind.get(W + b) is not None:
                return f"the indentation of style {sid!r} uses w:{b}; set {b} instead of {a}"
    return None


_KEEPS = {"before": ("before", "beforeLines", "beforeAutospacing"), "after": ("after", "afterLines", "afterAutospacing"),
          "beforeLines": ("before", "beforeLines"), "afterLines": ("after", "afterLines"),
          "left": ("left", "start"), "start": ("left", "start"), "right": ("right", "end"), "end": ("right", "end"),
          "firstLine": ("firstLine", "hanging", "firstLineChars", "hangingChars"),
          "hanging": ("firstLine", "hanging", "firstLineChars", "hangingChars")}


def keeps_own(ppr, values: Dict[str, Any]) -> bool:
    """A paragraph's (or style's) own pPr sets one of the asked properties itself, so it keeps its own value over the
    style (flagged, never refused)."""
    if ppr is None:
        return False
    for name in ("spacing", "ind"):
        el = ppr.find(W + name)
        if el is not None and any(el.get(W + a) is not None for k in values.get(name, {}) for a in _KEEPS.get(k, (k,))):
            return True
    return "jc" in values and ppr.find(W + "jc") is not None


@register
class SetParagraphProps(Op):
    name = "set_paragraph_props"
    mode = "formatting"
    description = ("Set spacing (before/after/line), indentation and alignment on selected paragraphs, or in paragraph "
                   "style definitions (select.styles).")
    params_schema = {"type": "object", "properties": {
        "spacing": {"type": "object", "description": "before/after/line (twips), lineRule auto|exact|atLeast, "
                                                     "beforeLines/afterLines"},
        "ind": {"type": "object", "description": "left/right/start/end/firstLine/hanging (twips)"},
        "jc": {"type": "string", "enum": list(JC_VALUES)},
        "tracked": {"type": "boolean", "description": "w:pPrChange; default: plain for whole-document selections, "
                                                      "tracked when paragraphs are targeted; never for styles"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes", "endnotes", "comments"]}, "default": ["document"]},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range}"},
        "styles": {"description": "{ids: [styleId, …]} — merge the values into these paragraph style definitions "
                                  "instead; alone (no scope/paragraphs); never tracked"}}}
    verification_profile = {"V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per paragraph",
                            "V10": "paragraph_props: selection re-resolved, values merged, snapshot == source pPr; "
                                   "styles: each changed style's pPr = source + the values merged, nothing else",
                            "V7": "none — spacing/indent/alignment move text"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope", "paragraphs", "styles"}:
            raise JobException(f"set_paragraph_props: unknown selector key(s) {sorted(set(select) - {'scope', 'paragraphs', 'styles'})}",
                               "bad-params")
        if "styles" in select:
            if set(select) != {"styles"}:
                raise JobException("set_paragraph_props: 'styles' is a target of its own — give it alone, without "
                                   "scope or paragraphs (direct formatting is a second op)", "bad-params")
            return self._plan_styles(pkg, select, params, _clean(params))
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
        reads: set = set()
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
                        if sheet.part:
                            # the lines rule read the styles' spacing: another op changing a style's pPr (the styles
                            # target of this op sets beforeLines/afterLines) is a plan-time conflict
                            reads.update(st.get(W + "styleId") for st in sheet.lineage(sheet.paragraph_style_id(p)))
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
        frag.touches += [(sheet.part, f"style:{sid}", "verify:pPr") for sid in sorted(reads)]
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

    def _plan_styles(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any],
                     values: Dict[str, Any]) -> PlanFragment:
        sel = select["styles"]
        if not (isinstance(sel, dict) and set(sel) == {"ids"} and isinstance(sel["ids"], list) and sel["ids"]
                and all(isinstance(x, str) for x in sel["ids"])):
            raise JobException(f"styles selector must be {{'ids': [styleId, …]}}, got {sel!r}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); omit "
                               "'tracked' or set it false", "bad-params")
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        ids = list(dict.fromkeys(sel["ids"]))
        missing = [i for i in ids if i not in sheet.styles]
        if missing:
            raise JobException(f"style id(s) {missing} not in this document", "not-found", {"missing": missing})
        bad = [sid for sid in ids if sheet.styles[sid].get(W + "type", "paragraph") != "paragraph"]
        if bad:
            raise JobException(f"spacing, indentation and alignment go on paragraph styles only; {bad} are not",
                               "bad-params")
        changes: List[str] = []
        reads: set = set(ids)              # V10 reads every selected style's own pPr in the output
        lines_read = any(side in values.get("spacing", {}) and f"{side}Lines" not in values["spacing"]
                         for side in ("before", "after"))
        for sid in ids:
            why = style_refusal(sheet, sid, values)
            if why:
                raise JobException(why, "conflict-property", {"style": sid})
            if lines_read:                 # the lines rule read the ancestors' and the document defaults' spacing
                reads.update(st.get(W + "styleId") for st in sheet.lineage(sid))
            ppr = sheet.styles[sid].find(W + "pPr")
            own = {name: (ppr.find(W + name) if ppr is not None else None) for name in values}
            if any(own[name] is None or merged_attrs(name, own[name], val) != dict(own[name].attrib)
                   for name, val in values.items()):
                changes.append(sid)
        frag = self.fragment(select, params)
        frag.touches += [(sheet.part, f"style:{sid}", "verify:pPr") for sid in sorted(reads)]
        if lines_read:
            frag.touches.append((sheet.part, "docDefaults", "verify:pPr"))
        if changes:
            frag.intended_parts = [sheet.part]
            frag.touches += [(sheet.part, f"style:{sid}", "pPr") for sid in changes]
            frag.geometry_reference = "none"
            frag.geometry_reason = "spacing/indentation/alignment changes move text by design"
        frag.property_assertions.append({
            "kind": "paragraph_props", "target": "styles", "styles_part": sheet.part, "select": {"ids": ids},
            "set": values, "tracked": False, "expected_ids": changes})
        frag.transform_data = {"target": "styles", "styles_part": sheet.part, "changes": changes, "values": values}
        frag.notes.append(f"style(s) {changes or 'none'} → {values} (definitions only; not tracked — Word does not "
                          f"track style changes)" + (f"; already as asked: {[s for s in ids if s not in changes]}"
                                                     if len(changes) < len(ids) else ""))
        for sid in changes:
            n = sum(1 for part in pkg.text_parts() for p in paragraphs(pkg.xml(part))
                    if sid in [st.get(W + "styleId") for st in sheet.lineage(sheet.paragraph_style_id(p))]
                    and keeps_own(p.find(W + "pPr"), values))
            if n:
                frag.flags.append(f"{n} paragraph(s) using style {sid} (or a style based on it) keep their own direct "
                                  f"spacing/indentation/alignment for what was asked — direct formatting wins over the "
                                  f"style.")
            based = sorted(other for other, st in sheet.styles.items() if other not in changes
                           and sid in [x.get(W + "styleId") for x in sheet.lineage(other)[1:]]
                           and keeps_own(st.find(W + "pPr"), values))
            if based:
                frag.notes.append(f"style(s) {based} are based on {sid} and set their own value for what was asked; "
                                  f"they keep it")
        if changes and pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        values = td["values"]
        if td.get("target") == "styles":
            if not td["changes"]:
                return
            root = pkg.xml(td["styles_part"])
            by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
            for sid in td["changes"]:
                write_values(get_or_add(by_id[sid], "pPr", STYLE_ORDER), values)
            pkg.write_xml(td["styles_part"], root)
            return
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
                write_values(ppr, values)
                if chg is not None:
                    ppr.append(chg)
            pkg.write_xml(part, root)
