"""V10 kinds of built-in batch 1's word-changing ops — verifier side, independent of ops/ (rule 1). Each kind derives
the edits again from the SOURCE (and the user's input files) by the op's declared rule, with this module's own code, and
hands every edited paragraph to the item model (verify/edits.py). Definitions: CLAUDE.md and
docs/BUILTIN_OPS_BATCH1_DESIGN.md.

* ``replaced_text`` — replace_table / set_issue_stamp: the pairs (typed, plus the table file read again) found in run text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .reader import W
from .props import Ctx, para_matches
from .template import _v_field_runs, display_of, slots_of
from .edits import check_edited_paragraph, part_revision_ids, v_items, v_paragraph_text, v_run_text, v_shown
from .tables import table_md5, table_pairs, table_variables, usable_text


def _v_owner(el):
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


# ---- replacement pairs -------------------------------------------------------------------------------------------

_V_PAIR_DEFAULTS = {"regex": False, "match_case": True, "whole_word": True, "flexible_whitespace": False}


def _v_pair(raw: Dict[str, Any]) -> Dict[str, Any]:
    out = {"find": raw["find"], "replace": raw["replace"]}
    for k, d in _V_PAIR_DEFAULTS.items():
        out[k] = bool(raw.get(k, d))
    return out


def _v_flexible(find: str) -> str:
    """Spaces → any run of whitespace / no-break spaces, hyphens → any dash, quotes → straight or curly (this module's
    own reading of the flexible-whitespace literal)."""
    out, i = [], 0
    while i < len(find):
        ch = find[i]
        if ch.isspace() or ch == " ":
            while i < len(find) and (find[i].isspace() or find[i] == " "):
                i += 1
            out.append(r"[\s ]+")
            continue
        if ch in "-–—":
            out.append("[-–—]")
        elif ch in "'’‘":
            out.append("['’‘]")
        elif ch in '"“”':
            out.append('["“”]')
        else:
            out.append(re.escape(ch))
        i += 1
    return "".join(out)


def _v_rx(pair: Dict[str, Any]):
    src = pair["find"] if pair["regex"] else (_v_flexible(pair["find"]) if pair["flexible_whitespace"]
                                              else re.escape(pair["find"]))
    return re.compile(src, 0 if pair["match_case"] else re.IGNORECASE)


def _v_word(c: str) -> bool:
    return c.isalnum() or c == "_"


def _v_hits(text: str, rx, pair: Dict[str, Any]) -> List[Tuple[int, int]]:
    out = []
    for m in rx.finditer(text):
        a, b = m.span()
        if a == b:
            continue
        if pair["whole_word"]:
            if a > 0 and _v_word(text[a - 1]) and _v_word(text[a]):
                continue
            if b < len(text) and _v_word(text[b]) and _v_word(text[b - 1]):
                continue
        out.append((a, b))
    return out


def v_find_pairs(text: str, pairs: Sequence[Dict[str, Any]], rxs: Sequence[Any]) -> List[Tuple[int, int, int]]:
    """[(start, end, pair index)]; ValueError for overlapping matches or an occurrence split by a tab/line break."""
    reader = re.sub(r"[\t\n]", " ", text)
    hits = []
    for k, (pair, rx) in enumerate(zip(pairs, rxs)):
        mine = _v_hits(text, rx, pair)
        if len(_v_hits(reader, rx, pair)) > len(mine):
            raise ValueError(f"{pair['find']!r} also occurs split by a tab or a line break in {v_shown(text)!r}")
        hits += [(a, b, k) for a, b in mine]
    hits.sort()
    for x, y in zip(hits, hits[1:]):
        if y[0] < x[1]:
            raise ValueError(f"two pairs match overlapping text in {v_shown(text)!r}")
    return hits


def _replaced_text(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    pairs = [_v_pair(p) for p in a.get("pairs") or []]
    table = a.get("table")
    if table:
        try:
            _, md5 = table_md5(table["file"])
            if md5 != table.get("md5"):
                errs.append(f"replaced_text: the table {table['file']} changed since planning")
                return "replaced_text: table changed"
            pairs += [_v_pair({"find": f, "replace": r}) for f, r in table_pairs(table)]
        except (OSError, ValueError, KeyError) as e:
            errs.append(f"replaced_text: the table cannot be read here ({type(e).__name__}: {e})")
            return "replaced_text: table unreadable"
    rxs = [_v_rx(p) for p in pairs]
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    pred = (a.get("select") or {}).get("paragraphs")
    n, edited = 0, {}
    for part in ctx.scope_parts(a.get("scope") or ["document"]):
        if part not in ctx.both:
            errs.append(f"replaced_text: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"replaced_text: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        in_field, old_ids = _v_field_runs(sv.root), part_revision_ids(sv.root)
        for bi, p in enumerate(sv.pars):
            if pred not in (None, "all") and not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            text = v_run_text(v_items(p))
            try:
                hits = v_find_pairs(text, pairs, rxs)
            except ValueError as e:
                errs.append(f"replaced_text: {part} block {bi}: {e} — the op must refuse")
                continue
            edits = [(s, e, pairs[k]["replace"], False) for s, e, k in hits if text[s:e] != pairs[k]["replace"]]
            if not edits:
                continue
            if _v_owner(p) is not None:
                errs.append(f"replaced_text: {part} block {bi} is in a text box — the op must refuse")
                continue
            n += len(edits)
            edited.setdefault(part, []).append(bi)
            check_edited_paragraph(p, ov.pars[bi], edits, tracked, author, in_field, old_ids,
                                   f"replaced_text {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
    if n != a.get("expected_edits", -1):
        errs.append(f"replaced_text: {n} replacement(s) found here, the plan predicted {a.get('expected_edits')}")
    if edited != {k: list(v) for k, v in (a.get("edited_blocks") or {}).items()}:
        errs.append(f"replaced_text: edited paragraphs {edited} here, the plan predicted {a.get('edited_blocks')}")
    return f"replaced_text: {n} replacement(s) of {len(pairs)} pair(s) ({'tracked' if tracked else 'untracked'})"


# ---- project variables ---------------------------------------------------------------------------------------------

def _v_name(name: str) -> str:
    return " ".join(str(name).split())


def _table_of(a: Dict[str, Any], kind: str, errs: List[str], read) -> Optional[Any]:
    """The assertion's table read again here (None when it has none); an error when it changed or cannot be read."""
    table = a.get("table")
    if not table:
        return None
    try:
        _, md5 = table_md5(table["file"])
        if md5 != table.get("md5"):
            errs.append(f"{kind}: the table {table['file']} changed since planning")
            return False
        return read(table)
    except (OSError, ValueError, KeyError) as e:
        errs.append(f"{kind}: the table cannot be read here ({type(e).__name__}: {e})")
        return False


def _filled_variables(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    markers = a.get("slot_markers") or ["highlight", "braces"]
    colors = a.get("highlight_colors") or ["yellow"]
    values = {_v_name(k): v for k, v in (a.get("values") or {}).items()}
    got = _table_of(a, "filled_variables", errs, table_variables)
    if got is False:
        return "filled_variables: table changed or unreadable"
    from_table = {_v_name(k): v for k, v in (got or {}).items()}
    tracked, author, leave = bool(a.get("tracked", True)), a.get("author"), a.get("unknown") == "leave"
    n, edited = 0, {}
    for part in ctx.scope_parts(a.get("scope") or ["document"]):
        if part not in ctx.both:
            errs.append(f"filled_variables: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"filled_variables: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        in_field, old_ids = _v_field_runs(sv.root), part_revision_ids(sv.root)
        for bi, p in enumerate(sv.pars):
            where = f"filled_variables {part} block {bi}"
            try:
                slots = slots_of(p, markers, colors, None, in_field)
            except ValueError as e:
                errs.append(f"{where}: {e} — the op must refuse")
                continue
            if not slots:
                continue
            if _v_owner(p) is not None:
                errs.append(f"{where}: a slot in a text box — the op must refuse")
                continue
            items = v_items(p)
            chars = [it for it in items if it.kind == "t" and it.pos is not None]
            if "".join(it.value for it in chars) != display_of(p):
                errs.append(f"{where}: text outside the plain runs — the op must refuse")
                continue
            edits = []
            for s in slots:
                key = _v_name(s["name"])
                value = values.get(key, from_table.get(key))
                if key not in values and key not in from_table:
                    if not leave:
                        errs.append(f"{where}: no value for {key!r} — the op must refuse")
                    continue
                try:
                    value = usable_text(value)
                except ValueError as e:
                    errs.append(f"{where}: {key}: {e} — the op must refuse")
                    continue
                edits.append((chars[s["start"]].pos, chars[s["end"] - 1].pos + 1, value, bool(s["highlight"])))
            text = v_run_text(items)
            edits = [e for e in edits if e[3] or text[e[0]:e[1]] != e[2]]
            if not edits:
                continue
            n += len(edits)
            edited.setdefault(part, []).append(bi)
            check_edited_paragraph(p, ov.pars[bi], edits, tracked, author, in_field, old_ids, where, errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
    if n != a.get("expected_edits", -1):
        errs.append(f"filled_variables: {n} slot(s) to fill here, the plan predicted {a.get('expected_edits')}")
    if edited != {k: list(v) for k, v in (a.get("edited_blocks") or {}).items()}:
        errs.append(f"filled_variables: filled paragraphs {edited} here, the plan predicted {a.get('edited_blocks')}")
    return f"filled_variables: {n} slot(s) filled ({'tracked' if tracked else 'untracked'})"


# ---- MasterSpec option groups ----------------------------------------------------------------------------------------

_V_INSIDE = "[^\\[\\]\\x00\\t\\n]"
_V_OPTION = re.compile(r"\[" + _V_INSIDE + r"*[^\[\]\x00\t\n\s ]" + _V_INSIDE + r"*\]")
_V_SEP = re.compile(r"[  ]+")


def _v_groups(text: str) -> List[Tuple[int, int, List[Tuple[int, int]], int]]:
    """[(start, end, options, depth at start)] — options chained by spaces / no-break spaces, scanned left to right."""
    out, k = [], 0
    depth_at, depth = [], 0
    for ch in text:
        depth_at.append(depth)
        depth = depth + 1 if ch == "[" else (max(0, depth - 1) if ch == "]" else depth)
    while k < len(text):
        m = _V_OPTION.match(text, k)
        if not m:
            k += 1
            continue
        opts = [m.span()]
        while True:
            sep = _V_SEP.match(text, opts[-1][1])
            nxt = _V_OPTION.match(text, sep.end()) if sep else None
            if not nxt:
                break
            opts.append(nxt.span())
        out.append((opts[0][0], opts[-1][1], opts, depth_at[opts[0][0]]))
        k = opts[-1][1]
    return out


def _v_resolve(text: str, rules: Sequence[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """The deletions every group's deciding rule asks for; ValueError where the op must refuse."""
    rxs = [(re.compile(r["match"], 0 if r.get("match_case") else re.IGNORECASE), r["action"]) for r in rules]
    dels: List[Tuple[int, int]] = []
    for start, end, opts, depth in _v_groups(text):
        for rx, action in rxs:
            hit = [i for i, (a, b) in enumerate(opts) if rx.search(text[a + 1:b - 1])]
            if not hit:
                continue
            if depth:
                raise ValueError(f"a nested group {v_shown(text[start:end])!r} is touched by a rule")
            if action == "pick":
                if len(hit) > 1:
                    raise ValueError(f"'pick' matches {len(hit)} options of {v_shown(text[start:end])!r}")
                a, b = opts[hit[0]]
                dels += [(start, a + 1), (b - 1, end)]
            elif len(hit) == len(opts):
                after = len(text[end:]) - len(text[end:].lstrip("  "))
                before = len(text[:start]) - len(text[:start].rstrip("  "))
                dels.append((start, end + after) if after else (start - before, end))
            else:
                last_kept = max(i for i in range(len(opts)) if i not in hit)
                for i in hit:
                    dels.append((opts[i][0], opts[i + 1][0]) if i < last_kept else (opts[i - 1][1], opts[i][1]))
            break
    return sorted(dels)


def _resolved_options(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    rules = a.get("rules") or []
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    pred = (a.get("select") or {}).get("paragraphs")
    n, edited = 0, {}
    for part in ctx.scope_parts(a.get("scope") or ["document"]):
        if part not in ctx.both:
            errs.append(f"resolved_options: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"resolved_options: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        in_field, old_ids = _v_field_runs(sv.root), part_revision_ids(sv.root)
        for bi, p in enumerate(sv.pars):
            if pred not in (None, "all") and not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            where = f"resolved_options {part} block {bi}"
            try:
                dels = _v_resolve(v_paragraph_text(p), rules)
            except (ValueError, re.error) as e:
                errs.append(f"{where}: {e} — the op must refuse")
                continue
            if not dels:
                continue
            if _v_owner(p) is not None:
                errs.append(f"{where}: in a text box — the op must refuse")
                continue
            n += len(dels)
            edited.setdefault(part, []).append(bi)
            check_edited_paragraph(p, ov.pars[bi], [(x, y, "", False) for x, y in dels], tracked, author, in_field,
                                   old_ids, where, errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
    if n != a.get("expected_edits", -1):
        errs.append(f"resolved_options: {n} deletion(s) here, the plan predicted {a.get('expected_edits')}")
    if edited != {k: list(v) for k, v in (a.get("edited_blocks") or {}).items()}:
        errs.append(f"resolved_options: resolved paragraphs {edited} here, the plan predicted {a.get('edited_blocks')}")
    return f"resolved_options: {n} deletion(s) ({'tracked' if tracked else 'untracked'})"


TEXT_OP_KINDS = {
    "replaced_text": _replaced_text,
    "filled_variables": _filled_variables,
    "resolved_options": _resolved_options,
}
