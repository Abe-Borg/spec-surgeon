"""Op: set_paragraph_props — spacing, indentation and alignment on selected paragraphs.

Values are merged into the existing w:spacing / w:ind elements (lesson: a style's ``before="240"`` must
survive a change to ``after``); elements are created in the complete pPr schema order, so a section-break
paragraph keeps its w:sectPr after them. Setting the first line (``firstLine`` / ``firstLineChars``) removes the
hanging indent (``hanging`` / ``hangingChars``) and vice versa (they are mutually exclusive in Word).

Refused (``conflict-property``, ``inherited_refusal``) where Word would ignore the value written — over the target's
own pPr and every level it inherits from, nearest first (a paragraph: its paragraph style chain, its table's style
chain, docDefaults; a style: its basedOn ancestors, docDefaults): ``before``/``after`` asked where the nearest level
naming ``…Autospacing`` turns it on; ``before``/``after`` asked where any level sets ``beforeLines``/``afterLines``
(unless the spec sets the lines too); an indentation side asked in twips (left/start, right/end, the first line:
firstLine/hanging) where any level gives that side in characters (``leftChars``/``startChars``, ``rightChars``/
``endChars``, ``firstLineChars``/``hangingChars`` — Word gives the character value priority), the ones the merge itself
removes from the target excepted, unless the spec gives that side in characters too; an ``ind`` alias whose other
spelling the target's own w:ind uses.

Tracked or plain (decision §14.1): plain for whole-document selections, tracked (w:pPrChange holding the old
pPr) when the selector narrows to particular paragraphs; ``tracked`` overrides.

Style definitions (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §2): ``select: {"styles": {"ids": [...]}}`` —
alone, paragraph styles only (another type in this file: ``style-mismatch``) — merges the same values into each
style's OWN w:pPr (``merged_attrs``, the merge the paragraphs get). Never tracked. A style whose own values already are
the asked ones is left alone. Paragraphs whose own direct formatting keeps its value are flagged. Its assertion
carries the user's own parameters; V10 applies its own reading of them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, STYLE_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add,
                     max_revision_id, paragraph_ppr, paragraphs, property_change)
from . import prose, register
from .base import JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import (StyleSheet, chain_reads, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts,
                        table_ordinals)
from .set_font import style_definitions, tracked_phrase

SPACING_KEYS = {"before": int, "after": int, "line": int, "lineRule": str, "beforeLines": int, "afterLines": int}
IND_KEYS = {"left": int, "right": int, "start": int, "end": int, "firstLine": int, "hanging": int,
            "leftChars": int, "rightChars": int, "startChars": int, "endChars": int, "firstLineChars": int,
            "hangingChars": int}
JC_VALUES = ("left", "center", "right", "both", "distribute", "start", "end")
LINE_RULES = ("auto", "exact", "atLeast")
TRUE = ("1", "true", "on")
FIRST = ("firstLine", "firstLineChars")            # the first-line family …
HANGING = ("hanging", "hangingChars")              # … and the hanging family: mutually exclusive
IND_SIDES = (("left", "start"), ("right", "end"), ("firstLine", "hanging"))   # twips attributes per side

# the plan's words (describe): Word shows spacing in points and indents in inches; a line value under the "auto" rule
# is a multiple of the line height (240 = single)
JC_WORDS = {"left": "left", "center": "centred", "right": "right", "both": "justified", "distribute": "distributed",
            "start": "to the leading edge", "end": "to the trailing edge"}
IND_WORDS = (("left", "the left indent"), ("start", "the left indent"), ("right", "the right indent"),
             ("end", "the right indent"), ("firstLine", "the first-line indent"), ("hanging", "the hanging indent"))
LINE_RULE_WORDS = {"exact": "exactly", "atLeast": "at least"}
LINE_MULTIPLES = {1.0: "single", 1.5: "1.5 lines", 2.0: "double"}


def _plain(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def _hundredths(value: Any, unit: str) -> str:
    """Hundredths of a line or of a character: ``1.5 lines``, ``2 characters``."""
    try:
        n = float(value) / 100
    except (TypeError, ValueError):
        return str(value)
    return f"{_plain(n)} {unit if n == 1 else unit + 's'}"


def _twips_points(twips: Any) -> str:
    try:
        return prose.points(float(twips) / 20)
    except (TypeError, ValueError):
        return str(twips)


def spacing_words(sp: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for side in ("before", "after"):
        if sp.get(side + "Lines") is not None:
            out.append(f"the space {side} to {_hundredths(sp[side + 'Lines'], 'line')}")
        if sp.get(side) is not None:
            out.append(f"the space {side} to {_twips_points(sp[side])}")
    rule = sp.get("lineRule")
    if sp.get("line") is not None:
        if rule in LINE_RULE_WORDS:
            out.append(f"the line spacing to {LINE_RULE_WORDS[rule]} {_twips_points(sp['line'])}")
        else:
            try:
                n: Optional[float] = float(sp["line"]) / 240
            except (TypeError, ValueError):
                n = None
            out.append("the line spacing to " + (str(sp["line"]) if n is None
                                                 else LINE_MULTIPLES.get(n) or _hundredths(n * 100, "line")))
    elif rule in LINE_RULE_WORDS or rule == "auto":
        out.append({"auto": "the line spacing to a multiple of the line height",
                    "exact": "the line spacing to an exact height",
                    "atLeast": "the line spacing to a minimum height"}[rule])
    return out


def indent_words(ind: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for key, label in IND_WORDS:
        if ind.get(key) is not None:
            out.append(f"{label} to {prose.inches(ind[key])}")
        if ind.get(key + "Chars") is not None:
            out.append(f"{label} to {_hundredths(ind[key + 'Chars'], 'character')}")
    return out


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
                    raise JobException(f"{name}.{k} must be an integer (twentieths of a point; hundredths of a line or "
                                       f"of a character for …Lines / …Chars)", "bad-params")
                clean[k] = str(val)
            else:
                if val not in LINE_RULES:
                    raise JobException(f"spacing.lineRule must be one of {LINE_RULES}", "bad-params")
                clean[k] = val
        out[name] = clean
    if "ind" in out and set(FIRST) & set(out["ind"]) and set(HANGING) & set(out["ind"]):
        raise JobException("a first-line indent (firstLine/firstLineChars) and a hanging indent (hanging/hangingChars) "
                           "are mutually exclusive", "bad-params")
    if params.get("jc") is not None:
        if params["jc"] not in JC_VALUES:
            raise JobException(f"jc must be one of {JC_VALUES}", "bad-params")
        out["jc"] = params["jc"]
    if not out:
        raise JobException("nothing to set: give spacing, ind and/or jc", "bad-params")
    return out


def merged_attrs(name: str, el, val) -> Dict[str, str]:
    """The attributes (Clark names) a w:spacing / w:ind / w:jc ends with: the asked ones set, the others kept — the
    first line (``firstLine``/``firstLineChars``) drops the hanging indent (``hanging``/``hangingChars``) and vice
    versa; w:jc gets w:val."""
    attrs = dict(el.attrib) if el is not None else {}
    if name == "jc":
        attrs[W + "val"] = val
        return attrs
    if name == "ind":
        for asked, gone in ((FIRST, HANGING), (HANGING, FIRST)):
            if set(asked) & set(val):
                for k in gone:
                    attrs.pop(W + k, None)
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


def inherited_refusal(levels: List[Tuple[str, Any, str]], values: Dict[str, Any]) -> Optional[str]:
    """Why Word would ignore the values written on a target, or None. ``levels`` are (label, pPr, read target), nearest
    first: the target's own pPr, then every level it inherits from (see the module docstring for the rules)."""
    sv, iv = values.get("spacing", {}), values.get("ind", {})

    def elements(name):
        return [(i, lv[0], lv[1].find(W + name)) for i, lv in enumerate(levels)
                if lv[1] is not None and lv[1].find(W + name) is not None]

    for side in ("before", "after"):
        if side not in sv:
            continue
        auto = next(((label, sp.get(W + side + "Autospacing")) for _, label, sp in elements("spacing")
                     if sp.get(W + side + "Autospacing") is not None), None)
        if auto is not None and auto[1] in TRUE:
            return f"spacing {side} is 'auto' ({side}Autospacing on {auto[0]}); the value would be ignored"
        if f"{side}Lines" not in sv:
            for _, label, sp in elements("spacing"):
                if sp.get(W + side + "Lines") not in (None, ""):
                    return (f"spacing {side} is set in lines on {label} ({side}Lines={sp.get(W + side + 'Lines')}); "
                            f"Word uses the line value and would ignore the point value — clear {side}Lines in Word "
                            f"first, or set {side}Lines in this spec as well")
    for side in IND_SIDES:
        asked = [k for k in side if k in iv]
        if not asked or any(k + "Chars" in iv for k in side):
            continue
        removed = set()                                # what the merge itself takes off the target's own w:ind
        if set(FIRST) & set(iv):
            removed |= set(HANGING)
        if set(HANGING) & set(iv):
            removed |= set(FIRST)
        for i, label, ind in elements("ind"):
            for k in side:
                v = ind.get(W + k + "Chars")
                if v not in (None, "") and not (i == 0 and k + "Chars" in removed):
                    return (f"indentation {asked[0]} is given in characters on {label} ({k}Chars={v}); Word uses the "
                            f"character value and would ignore the twips value — set {k}Chars in this spec as well, or "
                            f"clear it in Word first")
    own = levels[0][1].find(W + "ind") if levels and levels[0][1] is not None else None
    if own is not None:
        for a, b in (("left", "start"), ("right", "end"), ("start", "left"), ("end", "right")):
            if a in iv and own.get(W + b) is not None:
                return f"the indentation of {levels[0][0]} uses w:{b}; set {b} instead of {a}"
    return None


def _table_style_id(p, sheet: StyleSheet) -> Optional[str]:
    q = p.getparent()
    while q is not None and q.tag != W + "tbl":
        q = q.getparent()
    if q is None:
        return None
    ts = q.find(W + "tblPr/" + W + "tblStyle")
    return ts.get(W + "val") if ts is not None else sheet.defaults.get("table")


def _style_level(st) -> Tuple[str, Any, str]:
    return f"style {st.get(W + 'styleId')!r}", st.find(W + "pPr"), f"style:{st.get(W + 'styleId')}"


def paragraph_levels(p, sheet: StyleSheet) -> List[Tuple[str, Any, str]]:
    """(label, pPr, read target) nearest first for a paragraph: its own pPr, its paragraph style chain (pStyle, else the
    default), its table's style chain (tblStyle, else the default table style), docDefaults."""
    out: List[Tuple[str, Any, str]] = [("the paragraph itself", p.find(W + "pPr"), "")]
    out += [_style_level(st)
            for st in sheet.lineage(sheet.paragraph_style_id(p)) + sheet.lineage(_table_style_id(p, sheet))]
    out.append(("the document defaults", _doc_defaults_ppr(sheet), "docDefaults"))
    return out


def style_levels(sid: str, sheet: StyleSheet) -> List[Tuple[str, Any, str]]:
    """(label, pPr, read target) nearest first for a style: its own pPr, its basedOn ancestors, docDefaults."""
    out = [_style_level(st) for st in sheet.lineage(sid)]
    out.append(("the document defaults", _doc_defaults_ppr(sheet), "docDefaults"))
    return out


def _doc_defaults_ppr(sheet: StyleSheet):
    return sheet.root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr") if sheet.root is not None else None


def reads_inherited(values: Dict[str, Any]) -> bool:
    """Whether ``inherited_refusal`` reads the levels a target inherits from (they are then declared as reads)."""
    sv, iv = values.get("spacing", {}), values.get("ind", {})
    return bool({"before", "after"} & set(sv)) or any(
        any(k in iv for k in side) and not any(k + "Chars" in iv for k in side) for side in IND_SIDES)


_KEEPS = {"before": ("before", "beforeLines", "beforeAutospacing"), "after": ("after", "afterLines", "afterAutospacing"),
          "beforeLines": ("before", "beforeLines"), "afterLines": ("after", "afterLines"),
          "left": ("left", "start", "leftChars", "startChars"), "start": ("left", "start", "leftChars", "startChars"),
          "right": ("right", "end", "rightChars", "endChars"), "end": ("right", "end", "rightChars", "endChars"),
          "firstLine": FIRST + HANGING, "hanging": FIRST + HANGING}
_KEEPS.update({k + "Chars": _KEEPS[k] for k in ("left", "start", "right", "end", "firstLine", "hanging")})


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
        "ind": {"type": "object", "description": "left/right/start/end/firstLine/hanging (twips); leftChars/rightChars/"
                                                 "startChars/endChars/firstLineChars/hangingChars (hundredths of a "
                                                 "character — Word prefers them over the twips value)"},
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

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        what: List[str] = []
        if isinstance(params.get("spacing"), dict):
            what += spacing_words(params["spacing"])
        if isinstance(params.get("ind"), dict):
            what += indent_words(params["ind"])
        if params.get("jc") is not None:
            what.append("the alignment to " + JC_WORDS.get(params["jc"], prose.quote(params["jc"])))
        styles = select.get("styles")
        if isinstance(styles, dict) and styles.get("ids"):   # style definitions: never tracked
            where, tracked = style_definitions(styles["ids"]), ""
        else:
            where, tracked = prose.where(select), tracked_phrase(select, params)
        return prose.sentence("set", prose.join(what) if what else "the paragraph spacing, indentation and alignment",
                              where + (f", {tracked}" if tracked else ""))

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
        walks = reads_inherited(values)
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            tables = table_ordinals(root)
            chosen = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet):
                    continue
                ppr = p.find(W + "pPr")
                levels = paragraph_levels(p, sheet)
                why = inherited_refusal(levels, values)
                if why:
                    raise JobException(f"{part} paragraph {bi}: {why}", "conflict-property",
                                       {"part": part, "paragraph": bi})
                if walks and sheet.part:
                    # the rule read the styles this paragraph inherits from: another op changing one of their pPr (the
                    # styles target of this op can set …Lines or …Chars there) is a plan-time conflict
                    reads.update(lv[2] for lv in levels[1:])
                    # … and so does a remap of its table's style or an import adding a style its chain names (batch 4)
                    frag.touches += sorted(t for t in chain_reads(sheet, part, p, tables) if t[0] == part
                                           or t[2] == "verify:add")
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
        frag.touches += [(sheet.part, target, "verify:pPr") for target in sorted(reads)]
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
            raise JobException(f"spacing, indentation and alignment go on paragraph styles only; {bad} are not "
                               f"paragraph styles in this file", "style-mismatch", {"styles": bad})
        changes: List[str] = []
        reads: set = {f"style:{sid}" for sid in ids}      # V10 reads every selected style's own pPr in the output
        walks = reads_inherited(values)
        for sid in ids:
            levels = style_levels(sid, sheet)
            why = inherited_refusal(levels, values)
            if why:
                raise JobException(f"style {sid!r}: {why}", "conflict-property", {"style": sid})
            if walks:                      # the rule read the ancestors and the document defaults
                reads.update(lv[2] for lv in levels[1:])
            ppr = sheet.styles[sid].find(W + "pPr")
            own = {name: (ppr.find(W + name) if ppr is not None else None) for name in values}
            if any(own[name] is None or merged_attrs(name, own[name], val) != dict(own[name].attrib)
                   for name, val in values.items()):
                changes.append(sid)
        frag = self.fragment(select, params)
        frag.touches += [(sheet.part, target, "verify:pPr") for target in sorted(reads)]
        if changes:
            frag.intended_parts = [sheet.part]
            frag.touches += [(sheet.part, f"style:{sid}", "pPr") for sid in changes]
            frag.geometry_reference = "none"
            frag.geometry_reason = "spacing/indentation/alignment changes move text by design"
        # the user's own words (batch 2 rule 3): V10 applies its own reading and defaults to them
        frag.property_assertions.append({
            "kind": "paragraph_props", "target": "styles", "styles_part": sheet.part, "select": dict(sel),
            "params": {k: params[k] for k in ("spacing", "ind", "jc", "tracked") if k in params},
            "expected_ids": changes})
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
