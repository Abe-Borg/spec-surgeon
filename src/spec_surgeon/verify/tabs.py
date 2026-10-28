"""V10 kind ``tab_stops`` — set_tab_stops, verified (built-in batch 3, docs/BUILTIN_OPS_BATCH3_DESIGN.md §2).

This module's own reading of the op's definitions (transform side: ops/set_tab_stops.py; CLAUDE.md lists them among the
definitions that must stay identical on both sides). Nothing here comes from ops/ (rule 1):

* the asked stops and positions are read from the user's own parameters, with this module's defaults (align ``left``,
  leader ``none``, clear_others false; tracked: as asked, else when ``paragraphs`` narrows the selection);
* own entries (``_v_own``), the inherited fold (``template.Eff.para(with_direct=False)``), numbered
  (``removal._v_numbered``), the section of a body paragraph (``_v_sections``), the sections showing a header/footer
  part (``template._refs_shown``), text widths (``template._width``) and the rule (``_v_decide``).

Fails when the changed paragraphs or their count differ from the plan's; when a changed paragraph's output ``w:tabs`` is
not exactly the expected entries (untouched entries by full signature, new ones by exact attributes, in order; absent when
none); when its ``w:pPrChange`` breaks the paragraph-property rules; and — on the OUTPUT, for every selected paragraph —
when an asked stop is not in effect, a cleared position still shows a stop (numbered: has no own clear entry), or with
clear_others any other stop is in effect. Only ``tabs`` and ``pPrChange`` of the changed paragraphs leave the residual.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .reader import W
from .props import Ctx, _check_ppr_change, _plain, para_matches, sect_prs, sig
from .removal import _v_numbered
from .template import Eff, _refs_shown, _width

V_ALIGNS = {"left", "center", "right", "decimal", "bar"}
V_LEADERS = {"none", "dot", "hyphen", "underscore", "heavy", "middleDot"}
V_MARGINS = {"right_margin", "center"}
V_LIMIT = 31680


def _v_pos(v: Any) -> Any:
    if isinstance(v, str) and v in V_MARGINS:
        return v
    if isinstance(v, int) and not isinstance(v, bool) and -V_LIMIT <= v <= V_LIMIT:
        return v
    raise ValueError(f"bad position {v!r}")


def _v_asked(params: Dict[str, Any]) -> Tuple[List[Tuple[Any, str, str]], List[Any], bool]:
    stops = []
    for s in params.get("set") or []:
        align, leader = s.get("align", "left"), s.get("leader", "none")
        if align not in V_ALIGNS or leader not in V_LEADERS:
            raise ValueError(f"bad stop {s!r}")
        stops.append((_v_pos(s["pos"]), align, leader))
    clear = [_v_pos(v) for v in params.get("clear") or []]
    others = params.get("clear_others", False)
    if not isinstance(others, bool) or not (stops or clear or others):
        raise ValueError("nothing asked")
    return stops, clear, others


def _v_own(p) -> List[Tuple[int, str, str, Any]]:
    """(pos, val, leader, element) of the paragraph's own entries; ValueError where the op must refuse."""
    tabs = p.find(W + "pPr/" + W + "tabs")
    out: List[Tuple[int, str, str, Any]] = []
    for el in (tabs if tabs is not None else []):
        if not isinstance(el.tag, str):
            continue
        if el.tag != W + "tab":
            raise ValueError("a tab list holds something other than w:tab (the op must refuse)")
        out.append((int(el.get(W + "pos")), el.get(W + "val") or "", el.get(W + "leader") or "none", el))
    if len({e[0] for e in out}) != len(out):
        raise ValueError("two tab entries at one position (the op must refuse)")
    return out


def _v_inherited(eff: Eff, p) -> Dict[int, Tuple[str, str]]:
    return {pos: (a or "", b or "none") for pos, a, b in eff.para(p, with_direct=False)["tabs"]}


def _v_fold(inherited: Dict[int, Tuple[str, str]], own) -> Dict[int, Tuple[str, str]]:
    out = dict(inherited)
    for pos, val, leader, _ in own:
        if val == "clear":
            out.pop(pos, None)
        else:
            out[pos] = (val, leader)
    return out


def _v_decide(want: Dict[int, Tuple[str, str]], gone: List[int], others: bool, own, inherited,
              numbered: bool) -> Optional[List[Any]]:
    """The expected own entries (``("keep", k)`` or ``(pos, val, leader)``) sorted by position, or None (no change)."""
    eff = _v_fold(inherited, own)
    mine = {pos: (val, leader) for pos, val, leader, _ in own}
    clear_at = list(gone) + ([x for x in sorted(eff) if x not in want and x not in gone] if others else [])
    write: Dict[int, Tuple[str, str]] = {}
    for x, stop in want.items():
        if (mine.get(x) if numbered else eff.get(x)) != stop:
            write[x] = stop
    drop: Set[int] = set(write)
    for x in clear_at:
        done = mine.get(x, (None,))[0] == "clear" if numbered else x not in eff
        if not done:
            drop.add(x)
            if numbered or x in inherited:
                write[x] = ("clear", "none")
    if not drop:
        return None
    keyed = [(pos, ("keep", k)) for k, (pos, _, _, _) in enumerate(own) if pos not in drop]
    keyed += [(x, (x, v, l)) for x, (v, l) in write.items()]
    keyed.sort(key=lambda t: t[0])
    return [e for _, e in keyed]


def _v_sections(root) -> List[int]:
    """Per paragraph (root.iter order), its section: of its outermost paragraph, how many paragraphs holding a
    w:pPr/w:sectPr come before it."""
    pars = list(root.iter(W + "p"))
    seen: Dict[int, int] = {}
    out: List[int] = []
    n = 0
    for i, p in enumerate(pars):
        top = p
        for anc in p.iterancestors(W + "p"):
            top = anc
        seen[id(p)] = i
        out.append(n if top is p else out[seen[id(top)]])
        if p.find(W + "pPr/" + W + "sectPr") is not None:
            n += 1
    return out


class _VMargins:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.main = ctx.src.main_part()
        self.secs = sect_prs(ctx.src.xml(self.main))
        self.body: Optional[List[int]] = None
        self.shown = None

    def width(self, part: str, bi: int) -> int:
        """The text width a margin position measures for this paragraph; ValueError where the op must refuse."""
        if part == self.main:
            if self.body is None:
                self.body = _v_sections(self.ctx.src.xml(self.main))
            k = self.body[bi]
            w = _width(self.secs[k]) if k < len(self.secs) else None
            if not w:
                raise ValueError(f"paragraph {bi}: section {k + 1} has no usable width (the op must refuse)")
            return w
        if part not in set(self.ctx.src.part_of_kind("headers")) | set(self.ctx.src.part_of_kind("footers")):
            raise ValueError(f"{part}: margin positions only in the body, headers and footers (the op must refuse)")
        if self.shown is None:
            self.shown = _refs_shown(self.ctx.src)
        ws = {_width(self.secs[i]) for i, refs in enumerate(self.shown) if part in refs.values() and i < len(self.secs)}
        if len(ws) != 1 or not next(iter(ws)):
            raise ValueError(f"{part}: no single text width among the sections showing it (the op must refuse)")
        return next(iter(ws))


def _v_at(v: Any, width: Optional[int]) -> int:
    return width if v == "right_margin" else (width // 2 if v == "center" else v)


def _check_tabs_out(S, O, expected: List[Any], own, where: str, errs: List[str]) -> None:
    """The output pPr's w:tabs must hold exactly the expected entries, in order."""
    o_tabs = O.find(W + "tabs") if O is not None else None
    got = [c for c in (o_tabs if o_tabs is not None else []) if isinstance(c.tag, str)]
    if not expected:
        if o_tabs is not None:
            errs.append(f"{where}: w:tabs left in the output with {len(got)} entr(ies); no entry should remain")
        return
    if o_tabs is None:
        errs.append(f"{where}: w:tabs missing from the output")
        return
    if o_tabs.attrib:
        errs.append(f"{where}: w:tabs gained attributes {_plain(dict(o_tabs.attrib))}")
    if len(got) != len(expected):
        errs.append(f"{where}: w:tabs holds {len(got)} entr(ies), expected {len(expected)}")
        return
    for k, (e, g) in enumerate(zip(expected, got)):
        if e[0] == "keep":
            if sig(g) != sig(own[e[1]][3]):
                errs.append(f"{where}: tab entry {k} should be the paragraph's own {_plain(dict(own[e[1]][3].attrib))}, "
                            f"got {_plain(dict(g.attrib))}")
            continue
        pos, val, leader = e
        want = {W + "val": val, W + "pos": str(pos)}
        if leader != "none":
            want[W + "leader"] = leader
        if g.tag != W + "tab" or dict(g.attrib) != want or len(g):
            errs.append(f"{where}: tab entry {k} is {_plain(dict(g.attrib))}, expected {_plain(want)}")


def _tab_stops(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    sel = a.get("select") or {}
    if set(sel) - {"scope", "paragraphs"}:
        raise ValueError(f"unknown selector key(s) {sorted(set(sel) - {'scope', 'paragraphs'})}")
    scope = sel.get("scope", ["document"])
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    if sorted(parts) != sorted(a.get("parts", [])):
        errs.append(f"tab_stops: scope resolves to {sorted(parts)} here, plan says {sorted(a.get('parts', []))}")
    params = a.get("params") or {}
    stops, clear, others = _v_asked(params)
    pred = sel.get("paragraphs", "all")
    tracked = bool(params["tracked"]) if "tracked" in params else pred not in (None, "all", {})
    author = a.get("author")
    margins = _VMargins(ctx) if any(isinstance(v, str) for v in [s[0] for s in stops] + clear) else None
    s_eff, o_eff = Eff(ctx.src), Eff(ctx.out)
    changed: Dict[str, List[int]] = {}
    for part in parts:
        if part not in ctx.both:
            errs.append(f"tab_stops: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        for bi, p in enumerate(sv.pars):
            if not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            where = f"{part} block {bi}"
            width = margins.width(part, bi) if margins is not None else None
            want = {_v_at(v, width): (al, le) for v, al, le in stops}
            gone = [_v_at(v, width) for v in clear]
            if len(want) != len(stops) or set(gone) & set(want) or len(set(gone)) != len(gone):
                raise ValueError(f"{where}: two asked positions coincide here (the op must refuse)")
            own = _v_own(p)
            numbered = _v_numbered(p, ctx.src_styles)
            expected = _v_decide(want, gone, others, own, _v_inherited(s_eff, p), numbered)
            oi = ctx.maps[part].get(bi)
            if expected is not None:
                changed.setdefault(part, []).append(bi)
                if bi in ctx.text_changed.get(part, set()):
                    errs.append(f"tab_stops: {where} is selected but also has predicted text changes")
                    continue
                if oi is None:
                    errs.append(f"tab_stops: {where} has no counterpart in the output")
                    continue
                ctx.strip_ppr.setdefault(part, {}).setdefault(bi, set()).update({"tabs", "pPrChange"})
                S, O = p.find(W + "pPr"), ov.pars[oi].find(W + "pPr")
                if O is None:
                    errs.append(f"{where}: no pPr in the output")
                    continue
                _check_tabs_out(S, O, expected, own, where, errs)
                _check_ppr_change(S, O, tracked, author, where, errs)
            if oi is None:
                continue
            # post-condition, on every selected paragraph of the OUTPUT (an "already" judgement included)
            o_p = ov.pars[oi]
            try:
                o_own = _v_own(o_p)
            except ValueError as e:
                errs.append(f"{where}: the output's tab list is malformed — {e}")
                continue
            after = _v_fold(_v_inherited(o_eff, o_p), o_own)
            o_mine = {pos: (val, leader) for pos, val, leader, _ in o_own}
            o_num = _v_numbered(o_p, ctx.out_styles)
            for x, stop in want.items():
                if after.get(x) != stop or (o_num and o_mine.get(x) != stop):
                    errs.append(f"{where}: the stop at {x} is {after.get(x)} in the output, asked {stop}")
            for x in gone:
                if x in after or (o_num and o_mine.get(x, (None,))[0] != "clear"):
                    errs.append(f"{where}: a stop is still in effect at {x} in the output (asked to clear it)")
            if others and set(after) != set(want):
                errs.append(f"{where}: stops {sorted(set(after) - set(want))} are still in effect in the output "
                            f"(clear_others asked for only {sorted(want)})")
    n = sum(len(v) for v in changed.values())
    if n != a.get("expected_paragraphs", -1):
        errs.append(f"tab_stops: {n} paragraph(s) change here, plan predicted {a.get('expected_paragraphs')}")
    planned = {k: sorted(v) for k, v in (a.get("changed") or {}).items() if v}
    if {k: sorted(v) for k, v in changed.items()} != planned:
        errs.append(f"tab_stops: the paragraphs that change here {changed} are not the plan's {planned}")
    return f"tab_stops: {n} paragraph(s) → {params.get('set') or []} clear {params.get('clear') or []}" + \
        (" + every other stop" if others else "") + (" (tracked)" if tracked else "")


TAB_KINDS = {"tab_stops": _tab_stops}


# the contract a custom method's writer reads (``props.KIND_CONTRACTS``; the verifier owns the text)
from .props import KIND_CONTRACTS, KIND_EXAMPLES, PARA_KEYS  # noqa: E402

KIND_CONTRACTS["tab_stops"] = (
    'Tab stops of paragraphs: set, clear, clear the others (set_tab_stops). Keys: "select": {"scope": a part kind '
    'or a list of them (document | headers | footers | footnotes | endnotes | comments; default ["document"]), '
    '"paragraphs": a paragraph predicate (absent or "all" for every paragraph, else an object with any of ' +
    ", ".join(sorted(PARA_KEYS)) + ')}; "parts": the part names the scope resolves to (re-resolved and compared); '
    '"params": the user\'s own words, read with the verifier\'s defaults: "set": [{"pos": a position in twips '
    'within ±31680, or "right_margin" or "center" (resolved from the section\'s page size and margins), "align": '
    'left (default) | center | right | decimal | bar, "leader": none (default) | dot | hyphen | underscore | heavy | '
    'middleDot}, ...], "clear": [positions], "clear_others": boolean (default false; at least one of the three '
    'asked), "tracked": boolean (default: tracked when "paragraphs" narrows the selection, plain otherwise); '
    '"author"; "expected_paragraphs": how many selected paragraphs change; "changed": {part name: [paragraph '
    'indices that change, in document order over every w:p of the part]} — the verifier derives which selected '
    'paragraphs need an entry from its own fold of the inherited stops (docDefaults, the style chain, the '
    'paragraph\'s own w:tabs: a stop already in effect as asked, a position already clear, needs nothing) and the '
    'lists must agree. Compared: each changed paragraph\'s output w:tabs is exactly the expected entries — the '
    'untouched entries by full signature, the new ones (w:val, w:pos, w:leader) exact, in position order, the '
    'element absent when no entry remains; the pPrChange rules (untracked: unchanged; tracked: last child of pPr '
    'with w:id and the author, snapshot the source pPr); and — on the OUTPUT, for every selected paragraph, changed '
    'or not — every asked stop is in effect, a cleared position shows no stop (a numbered paragraph needs its own '
    'clear entry), and with clear_others no other stop is in effect. Refused: a w:tabs holding something other '
    'than w:tab, two entries at one position. Only w:tabs and w:pPrChange of the changed paragraphs leave the '
    'residual.')
KIND_EXAMPLES["tab_stops"] = {
    "kind": "tab_stops", "parts": ["word/document.xml"],
    "select": {"scope": "document", "paragraphs": {"style_in": ["PRT", "ART"]}},
    "params": {"set": [{"pos": 720, "align": "left"}, {"pos": "right_margin", "align": "right", "leader": "dot"}],
               "clear": [360], "clear_others": False, "tracked": False},
    "author": None, "expected_paragraphs": 6, "changed": {"word/document.xml": [3, 9, 15, 21, 27, 33]}}
