"""Op: set_tab_stops — tab stops on selected paragraphs (built-in batch 3, docs/BUILTIN_OPS_BATCH3_DESIGN.md §2).

Formatting mode: zero text diffs. Direct formatting only (the paragraph's own ``w:pPr/w:tabs``); style definitions are
not changed.

Parameters: ``set`` — stops to have, ``{pos, align, leader}``: ``pos`` in twips from the left margin or
``"right_margin"`` / ``"center"``; ``clear`` — positions where no stop may be in effect afterwards; ``clear_others`` —
every other stop in effect is cleared, so the paragraph ends with exactly the ``set`` stops.

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's reading
is verify/tabs.py):

* **Own stops** — the paragraph's ``w:pPr/w:tabs/w:tab`` entries ``(pos, val, leader)`` (leader absent = ``none``); a
  ``val`` of ``clear`` is a clear entry. Two own entries at one position, an entry without a whole-number position or a
  ``w:tabs`` child other than ``w:tab`` refuse (``structure``).
* **Inherited stops** — the tab fold without the paragraph's own pPr (``effective.Resolver.paragraph(direct=False)``:
  docDefaults → table style chain → paragraph style chain, a ``clear`` removing an earlier stop at its position).
  **In effect** — the inherited stops folded with the own entries.
* **Numbered** — numbering in effect (``empty_paragraphs.numbered``). The list definition can add stops the op does not
  read, so on a numbered paragraph only the own entries count as certain.
* **Margin positions** — ``right_margin`` = the text width W of the paragraph's section (``effective.text_width``),
  ``center`` = W // 2. A body paragraph's section: of its outermost paragraph, the number of paragraphs holding a
  ``w:pPr/w:sectPr`` before it in document order (``paragraph_sections``); a header/footer paragraph: every section
  showing the part (``hf_common.effective_refs``), all with one W. Anything else is ``tab-position``.

The rule, per selected paragraph, with S the asked stops and C the positions to clear (plus, with ``clear_others``,
every position in effect not in S): X in S is already when (numbered: the own entry at X is exactly S[X]; else: the stop
in effect at X is exactly S[X]), otherwise the own entries at X go and S[X] is written; X in C is already when
(numbered: the own entry at X is a clear entry; else: nothing is in effect at X), otherwise the own entries at X go and a
clear entry is written when a stop is inherited at X or the paragraph is numbered. A changed paragraph's own entries =
the untouched source entries and the new ones, sorted by position (stable); none left → ``w:tabs`` removed.

Tracked or plain (§14.1) and an existing ``w:pPrChange`` exactly as set_paragraph_props. Verified by V10's
``tab_stops`` kind (verify/tabs.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add, max_revision_id,
                     paragraph_ppr, paragraphs, property_change)
from . import register
from .base import JobException, Op, PlanFragment
from .effective import Resolver, text_width
from .empty_paragraphs import numbered
from .hf_common import effective_refs
from .replace_text import default_author
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts

ALIGNS = ("left", "center", "right", "decimal", "bar")
LEADERS = ("none", "dot", "hyphen", "underscore", "heavy", "middleDot")
MARGIN_POSITIONS = ("right_margin", "center")
POS_LIMIT = 31680                                  # ±22 inches, Word's range for a tab stop
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}
POS_SCHEMA = {"description": "twips from the left margin (1440 = 1 inch; -31680…31680), or 'right_margin' / 'center' "
                             "(the text width of the paragraph's section / half of it)",
              "anyOf": [{"type": "integer", "minimum": -POS_LIMIT, "maximum": POS_LIMIT},
                        {"type": "string", "enum": list(MARGIN_POSITIONS)}]}

Stop = Tuple[str, str]                            # (val, leader) — leader "none" when absent


def _pos(v: Any, where: str) -> Any:
    if isinstance(v, str):
        if v not in MARGIN_POSITIONS:
            raise JobException(f"{where}: a position is twips (an integer) or one of {list(MARGIN_POSITIONS)}",
                               "bad-params")
        return v
    if isinstance(v, bool) or not isinstance(v, int) or not -POS_LIMIT <= v <= POS_LIMIT:
        raise JobException(f"{where}: a position is a whole number of twips from -{POS_LIMIT} to {POS_LIMIT} "
                           f"(1440 = 1 inch), or one of {list(MARGIN_POSITIONS)}", "bad-params")
    return v


def clean_params(params: Dict[str, Any]) -> Tuple[List[Tuple[Any, str, str]], List[Any], bool]:
    """(the asked stops as (position, align, leader), the positions to clear, clear_others) — validated."""
    raw_set, raw_clear = params.get("set"), params.get("clear")
    others = params.get("clear_others", False)
    if not isinstance(others, bool):
        raise JobException("clear_others must be true or false", "bad-params")
    stops: List[Tuple[Any, str, str]] = []
    if raw_set is not None:
        if not isinstance(raw_set, list):
            raise JobException("'set' must be a list of stops {pos, align, leader}", "bad-params")
        for i, s in enumerate(raw_set):
            if not isinstance(s, dict) or "pos" not in s or set(s) - {"pos", "align", "leader"}:
                raise JobException(f"set[{i}] must be an object {{pos, align, leader?}}", "bad-params")
            align, leader = s.get("align", "left"), s.get("leader", "none")
            if align not in ALIGNS:
                raise JobException(f"set[{i}].align must be one of {list(ALIGNS)}", "bad-params")
            if leader not in LEADERS:
                raise JobException(f"set[{i}].leader must be one of {list(LEADERS)}", "bad-params")
            stops.append((_pos(s["pos"], f"set[{i}].pos"), align, leader))
    clear: List[Any] = []
    if raw_clear is not None:
        if not isinstance(raw_clear, list):
            raise JobException("'clear' must be a list of positions", "bad-params")
        clear = [_pos(v, f"clear[{i}]") for i, v in enumerate(raw_clear)]
    if not stops and not clear and not others:
        raise JobException("nothing to do: give 'set' stops, 'clear' positions and/or clear_others", "bad-params")
    asked = [s[0] for s in stops]
    if len(set(asked)) != len(asked):
        raise JobException(f"two 'set' stops at one position: {asked}", "bad-params")
    if set(asked) & set(clear):
        raise JobException(f"a position is both set and cleared: {sorted(set(map(str, asked)) & set(map(str, clear)))}",
                           "bad-params")
    if len(set(map(str, clear))) != len(clear):
        raise JobException(f"a position is cleared twice: {clear}", "bad-params")
    return stops, clear, others


# ---- reading a paragraph ------------------------------------------------------------------------------------------

def own_entries(p) -> List[Tuple[int, str, str, Any]]:
    """The paragraph's own tab entries (pos, val, leader, element) in document order; ``structure`` for a w:tabs that
    holds something else, an entry without a whole-number position, or two entries at one position."""
    tabs = p.find(f"{W}pPr/{W}tabs")
    if tabs is None:
        return []
    out = []
    for el in tabs:
        if not isinstance(el.tag, str):
            continue
        if el.tag != W + "tab":
            raise JobException(f"the paragraph's tab list holds a {etree.QName(el).localname} element", "structure")
        try:
            pos = int(el.get(W + "pos"))
        except (TypeError, ValueError):
            raise JobException(f"a tab stop of the paragraph has no whole-number position ({el.get(W + 'pos')!r})",
                               "structure")
        out.append((pos, el.get(W + "val") or "", el.get(W + "leader") or "none", el))
    positions = [e[0] for e in out]
    if len(set(positions)) != len(positions):
        raise JobException(f"the paragraph has two tab entries at one position ({positions})", "structure")
    return out


def inherited_stops(res: Resolver, p) -> Dict[int, Stop]:
    try:
        tabs = res.paragraph(p, direct=False)["tabs"]
    except (TypeError, ValueError):
        raise JobException("a style's tab stop has no whole-number position", "structure")
    return {pos: (val or "", leader or "none") for pos, val, leader in tabs}


def in_effect(inherited: Dict[int, Stop], own: List[Tuple[int, str, str, Any]]) -> Dict[int, Stop]:
    eff = dict(inherited)
    for pos, val, leader, _ in own:
        if val == "clear":
            eff.pop(pos, None)
        else:
            eff[pos] = (val, leader)
    return eff


def decide(stops: Dict[int, Stop], clear: List[int], clear_others: bool, own: List[Tuple[int, str, str, Any]],
           inherited: Dict[int, Stop], is_numbered: bool) -> Optional[List[Any]]:
    """The paragraph's own entries after the change — each ``("keep", k)`` (the k-th source entry, untouched) or
    ``(pos, val, leader)`` (new) — sorted by position; None when nothing changes."""
    eff = in_effect(inherited, own)
    own_at = {pos: (val, leader) for pos, val, leader, _ in own}
    targets = list(clear)
    if clear_others:
        targets += [x for x in sorted(eff) if x not in stops and x not in targets]
    new: Dict[int, Tuple[str, str]] = {}
    for x, want in stops.items():
        already = own_at.get(x) == want if is_numbered else eff.get(x) == want
        if not already:
            new[x] = want
    gone: set = set(new)
    for x in targets:
        already = (own_at.get(x, ("", ""))[0] == "clear") if is_numbered else x not in eff
        if already:
            continue
        gone.add(x)
        if x in inherited or is_numbered:
            new[x] = ("clear", "none")
    if not gone:
        return None
    entries: List[Tuple[int, Any]] = [(pos, ("keep", k)) for k, (pos, _, _, _) in enumerate(own) if pos not in gone]
    entries += [(x, (x, val, leader)) for x, (val, leader) in new.items()]
    entries.sort(key=lambda e: e[0])                  # stable: untouched entries keep their order among equals
    return [e for _, e in entries]


def paragraph_sections(root) -> List[int]:
    """Per paragraph of the main part (``paragraphs(root)`` order), the index of its section in ``sect_prs``: of its
    outermost paragraph (itself unless in a text box), the number of paragraphs holding a w:pPr/w:sectPr before it."""
    pars = paragraphs(root)
    index = {id(p): i for i, p in enumerate(pars)}      # the list keeps every proxy alive: ids stay unique
    out: List[int] = []
    k = 0
    for p in pars:
        outer, q = p, p.getparent()
        while q is not None:
            if q.tag == W + "p":
                outer = q
            q = q.getparent()
        out.append(k if outer is p else out[index[id(outer)]])
        if p.find(f"{W}pPr/{W}sectPr") is not None:
            k += 1
    return out


class Margins:
    """The text width a margin position measures, per part (and per paragraph of the main part), with the sections it
    read — or the ``tab-position`` refusal."""

    def __init__(self, pkg: DocxPackage):
        self.pkg = pkg
        self.main = pkg.main_part()
        self.secs = DocxPackage.sect_prs(pkg.xml(self.main))
        self.widths = [text_width(sp) for sp in self.secs]
        self._body: Optional[List[int]] = None
        self._refs = None

    def width(self, part: str, bi: int) -> Tuple[int, List[int], bool]:
        """(W, the sections measured, whether the header/footer references were read)."""
        if part == self.main:
            if self._body is None:
                self._body = paragraph_sections(self.pkg.xml(self.main))
            k = self._body[bi]
            w = self.widths[k] if k < len(self.widths) else None
            if not w:
                raise JobException(f"paragraph {bi} lies in section {k + 1}, which states no usable page width and "
                                   f"margins; a margin position cannot be measured", "tab-position",
                                   {"part": part, "paragraph": bi})
            return w, [k], False
        kinds = self.pkg.parts_by_kind()
        if part not in kinds.get("headers", []) + kinds.get("footers", []):
            raise JobException(f"{part}: a margin position ('right_margin', 'center') is measured only in the body, "
                               f"headers and footers", "tab-position", {"part": part})
        if self._refs is None:
            self._refs = effective_refs(self.pkg, self.secs)
        showing = [i for i, refs in enumerate(self._refs) if part in refs.values()]
        if not showing:
            raise JobException(f"{part} is shown by no section; a margin position cannot be measured", "tab-position",
                               {"part": part})
        ws = {self.widths[i] for i in showing}
        if len(ws) != 1 or not next(iter(ws)):
            raise JobException(f"{part} is shown by sections {[i + 1 for i in showing]} of different (or unstated) text "
                               f"widths {sorted(map(str, ws))}; a margin position would be wrong in some of them",
                               "tab-position", {"part": part})
        return next(iter(ws)), showing, True


def resolve_position(v: Any, width: Optional[int]) -> int:
    if v == "right_margin":
        return width
    if v == "center":
        return width // 2
    return v


@register
class SetTabStops(Op):
    name = "set_tab_stops"
    mode = "formatting"
    description = ("Set or clear tab stops on selected paragraphs (left, center, right, decimal or bar; with dot, "
                   "hyphen or underscore leaders), at a position in twips or at the right margin / the centre of the "
                   "text; clear_others leaves exactly the asked stops. Direct formatting; adds no characters; a "
                   "paragraph that already has the asked stops is left alone.")
    params_schema = {"type": "object", "properties": {
        "set": {"type": "array", "description": "stops the paragraphs must have",
                "items": {"type": "object", "required": ["pos"], "properties": {
                    "pos": POS_SCHEMA,
                    "align": {"type": "string", "enum": list(ALIGNS), "default": "left"},
                    "leader": {"type": "string", "enum": list(LEADERS), "default": "none"}}}},
        "clear": {"type": "array", "items": POS_SCHEMA,
                  "description": "positions where no stop may be in effect afterwards (the paragraph's own or its "
                                 "style's)"},
        "clear_others": {"type": "boolean", "default": False,
                         "description": "clear every other stop in effect (the paragraph's own and its styles'), so "
                                        "only the 'set' stops remain; a numbered paragraph's list-definition stops "
                                        "are not read (flagged)"},
        "tracked": {"type": "boolean", "description": "w:pPrChange; default: plain for whole-document selections, "
                                                      "tracked when paragraphs are targeted"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per changed paragraph",
        "V10": "tab_stops: selection, margin positions and every paragraph's own tab entries re-derived from the source; "
               "each changed paragraph's w:tabs exactly as expected, the asked stops in effect (and no other with "
               "clear_others) in the output; residual identical",
        "V7": "none when a stop changes (text moves)"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        if set(raw_select) - {"scope", "paragraphs"}:
            raise JobException(f"set_tab_stops: unknown selector key(s) {sorted(set(raw_select) - {'scope', 'paragraphs'})}",
                               "bad-params")
        stops, clear, others = clean_params(params)
        pred = raw_select.get("paragraphs", "all")
        check_paragraph_selector(pred)
        scope = raw_select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        tracked = bool(params.get("tracked", is_targeted(raw_select)))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        res = Resolver(sheet, {})
        margins = Margins(pkg) if any(isinstance(v, str) for v in [s[0] for s in stops] + clear) else None
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, List[list]] = {}
        already = older = numbered_others = 0
        measured: Dict[str, set] = {}
        read_secs: set = set()
        read_refs = False
        for part in parts:
            pars = paragraphs(pkg.xml(part))
            chosen: List[list] = []
            for bi, p in enumerate(pars):
                if not paragraph_selected(pred, p, bi, block_full_text(p), sheet):
                    continue
                where = f"{part} paragraph {bi}"
                width = None
                if margins is not None:
                    width, secs, refs = margins.width(part, bi)
                    read_secs.update(secs)
                    read_refs = read_refs or refs
                    measured.setdefault(part, set()).add(width)
                want = {resolve_position(v, width): (align, leader) for v, align, leader in stops}
                if len(want) != len(stops):
                    raise JobException(f"{where}: two 'set' stops resolve to one position here ({sorted(want)}; "
                                       f"right_margin = {width})", "tab-position", {"part": part, "paragraph": bi})
                gone = [resolve_position(v, width) for v in clear]
                if set(gone) & set(want) or len(set(gone)) != len(gone):
                    raise JobException(f"{where}: a 'clear' position resolves onto another asked position here "
                                       f"(right_margin = {width})", "tab-position", {"part": part, "paragraph": bi})
                try:
                    own = own_entries(p)
                except JobException as e:
                    raise JobException(f"{where}: {e}", e.code, {"part": part, "paragraph": bi})
                is_num = numbered(p, sheet)
                numbered_others += bool(is_num and others)
                # V10's post-condition reads this paragraph's stops in the output whether or not it changes: another op
                # changing them (a second set_tab_stops) is a plan-time conflict, never a failure after apply
                frag.touches.append((part, str(bi), "verify:tabs"))
                after = decide(want, gone, others, own, inherited_stops(res, p), is_num)
                if after is None:
                    already += 1
                    continue
                ppr = p.find(W + "pPr")
                if ppr is not None and ppr.find(W + "pPrChange") is not None:
                    if tracked:
                        raise JobException(f"{where} already carries a pending tracked paragraph change "
                                           f"(w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                    older += 1
                chosen.append([bi, [list(e) for e in after]])
            if chosen:
                targets[part] = chosen
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), aspect) for bi, _ in chosen for aspect in ("pPr", "tabs")]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
        main = pkg.main_part()
        for k in sorted(read_secs):
            frag.touches += [(main, f"sectPr:{k}", "verify:pgSz"), (main, f"sectPr:{k}", "verify:pgMar")]
        if read_refs:
            frag.touches += [(main, f"sectPr:{k}", "verify:hfref") for k in range(len(margins.secs))]
        n = sum(len(v) for v in targets.values())
        frag.property_assertions.append({
            "kind": "tab_stops", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in ("set", "clear", "clear_others", "tracked") if k in params},
            "author": author, "expected_paragraphs": n,
            "changed": {part: [bi for bi, _ in v] for part, v in targets.items()}})
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author}
        if tracked and n:
            frag.rev_delta["pPrChange"] = n
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if n:
            frag.geometry_reference = "none"
            frag.geometry_reason = "a tab stop moves the text after a tab by design"
        frag.notes.append(f"{n} paragraph(s) get tab stops: {_describe(stops, clear, others)}; "
                          f"{'tracked (w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append(f"already as asked, left alone: {already} paragraph(s)")
        for part, ws in measured.items():
            frag.notes.append(f"{part}: right_margin = {', '.join(str(w) for w in sorted(ws))} twips (the text width"
                              f"{' of each paragraph’s section' if len(ws) > 1 else ''}), center = half of it")
        if numbered_others:
            frag.flags.append(f"{numbered_others} numbered paragraph(s): their list definition may set tab stops of its "
                              f"own (the tab after the number); clear_others does not read list definitions and leaves "
                              f"those as they are.")
        if older:
            frag.flags.append(f"{older} changed paragraph(s) already carried a pending tracked paragraph change "
                              f"(w:pPrChange); reject-all would restore that older snapshot.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, chosen in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for bi, after in chosen:
                ppr = paragraph_ppr(pars[bi])
                chg = None
                if stamp:
                    snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
                    chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
                    next_id += 1
                write_tabs(ppr, after)
                if chg is not None:
                    ppr.append(chg)
            pkg.write_xml(part, root)


def write_tabs(ppr, after: List[Any]) -> None:
    """Rebuild the paragraph's w:tabs from ``decide``'s entries: kept source entries as they are, new ones as Word
    writes them (w:val, w:leader unless none, w:pos); no entry left → no w:tabs."""
    tabs = ppr.find(W + "tabs")
    old = [el for el in (tabs if tabs is not None else []) if isinstance(el.tag, str)]
    if not after:
        if tabs is not None:
            ppr.remove(tabs)
        return
    tabs = get_or_add(ppr, "tabs", PPR_ORDER)
    for c in list(tabs):
        tabs.remove(c)
    for e in after:
        if e[0] == "keep":
            tabs.append(old[e[1]])
            continue
        pos, val, leader = e
        el = etree.SubElement(tabs, W + "tab")
        el.set(W + "val", val)
        if leader != "none":
            el.set(W + "leader", leader)
        el.set(W + "pos", str(pos))


def _describe(stops, clear, others) -> str:
    parts = [f"{align} at {pos}" + (f" ({leader} leader)" if leader != "none" else "") for pos, align, leader in stops]
    if clear:
        parts.append("clear " + ", ".join(map(str, clear)))
    if others:
        parts.append("every other stop cleared")
    return "; ".join(parts)
