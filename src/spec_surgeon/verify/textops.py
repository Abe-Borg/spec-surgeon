"""V10 kinds of built-in batch 1's word-changing ops — verifier side, independent of ops/ (rule 1). Each kind derives
the edits again from the SOURCE (and the user's input files) by the op's declared rule, with this module's own code, and
hands every edited paragraph to the item model (verify/edits.py). Definitions: CLAUDE.md and
docs/BUILTIN_OPS_BATCH1_DESIGN.md.

* ``replaced_text`` — replace_table / set_issue_stamp: the pairs (typed, plus the table file read again) found in run text.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import W
from .props import Ctx, _v_inherit_ppr, _v_inherit_rpr, para_matches, sig
from .template import _v_field_runs, display_of, fill_value, slots_of
from .edits import (check_deleted_paragraph, check_edited_paragraph, part_revision_ids, ppr_norm, v_deletable,
                    v_items, v_paragraph_text, v_run_text, v_shown)
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


# ---- tagged paragraphs -----------------------------------------------------------------------------------------------

def _v_system(s: str) -> str:
    return " ".join(s.split()).casefold()


def _tagged_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    try:
        rx = re.compile(a["tag_regex"])
        sep = re.compile(a.get("tag_separator") or r"\s*[/,;]\s*")
    except (KeyError, re.error) as e:
        errs.append(f"tagged_paragraphs: {e}")
        return "tagged_paragraphs: bad rule"
    keep = {_v_system(s) for s in a.get("keep") or []}
    delete = {_v_system(s) for s in a.get("delete") or []}
    tracked, author, strip = bool(a.get("tracked", True)), a.get("author"), bool(a.get("strip_tags", True))
    n_del = n_strip = 0
    for part in ctx.scope_parts(a.get("scope") or ["document"]):
        if part not in ctx.both:
            errs.append(f"tagged_paragraphs: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        in_field, old_ids = _v_field_runs(sv.root), part_revision_ids(sv.root)
        deleted: List[int] = []
        stripped: Dict[int, Tuple[int, int, str, bool]] = {}
        for bi, p in enumerate(sv.pars):
            text = v_paragraph_text(p)
            m = rx.search(text)
            if m is None:
                continue
            where = f"tagged_paragraphs {part} block {bi}"
            tag = m.group("tag") if "tag" in rx.groupindex else (m.group(1) if rx.groups else m.group(0))
            systems = [_v_system(s) for s in sep.split(tag or "") if s.strip()]
            if not systems or _v_owner(p) is not None:
                errs.append(f"{where}: {'an empty tag' if not systems else 'a tag in a text box'} — the op must refuse")
                continue
            if any(s in keep for s in systems) if keep else any(s not in delete for s in systems):
                if strip and m.end() > m.start():
                    stripped[bi] = (m.start(), m.end(), "", False)
                continue
            why = v_deletable(p, tracked)
            if why:
                errs.append(f"{where}: {why} — the op must refuse")
                continue
            deleted.append(bi)
        if deleted != list((a.get("expected_deleted") or {}).get(part, [])):
            errs.append(f"tagged_paragraphs: {part}: deletes {deleted} here, the plan predicted "
                        f"{(a.get('expected_deleted') or {}).get(part, [])}")
        if sorted(stripped) != list((a.get("expected_stripped") or {}).get(part, [])):
            errs.append(f"tagged_paragraphs: {part}: strips tags from {sorted(stripped)} here, the plan predicted "
                        f"{(a.get('expected_stripped') or {}).get(part, [])}")
        if tracked:
            if len(ov.pars) != len(sv.pars):
                errs.append(f"tagged_paragraphs: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
                continue
            to_out = {bi: bi for bi in range(len(sv.pars))}
        else:
            kept = [bi for bi in range(len(sv.pars)) if bi not in set(deleted)]
            if len(ov.pars) != len(kept):
                errs.append(f"tagged_paragraphs: {part} has {len(ov.pars)} paragraphs, expected {len(kept)} once "
                            f"{len(deleted)} are deleted")
                continue
            to_out = {bi: k for k, bi in enumerate(kept)}
        for bi in deleted:
            n_del += 1
            if tracked:
                check_deleted_paragraph(sv.pars[bi], ov.pars[bi], author, old_ids,
                                        f"tagged_paragraphs {part} block {bi}", errs)
                ctx.verified_blocks.setdefault(part, {})[bi] = bi
            else:
                ctx.deleted_src_blocks.setdefault(part, set()).add(bi)
        for bi, edit in sorted(stripped.items()):
            n_strip += 1
            check_edited_paragraph(sv.pars[bi], ov.pars[to_out[bi]], [edit], tracked, author, in_field, old_ids,
                                   f"tagged_paragraphs {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = to_out[bi]
        # untracked: the residual comparison (deleted source blocks left out) proves every other paragraph unchanged
    return (f"tagged_paragraphs: {n_del} paragraph(s) deleted, {n_strip} tag(s) taken off "
            f"({'tracked' if tracked else 'untracked'})")


# ---- END OF SECTION --------------------------------------------------------------------------------------------------

_V_BLANK = " \t\n "


def _v_empty_p(el) -> bool:
    return (el.tag == W + "p" and el.find(f"{W}pPr/{W}sectPr") is None
            and not v_paragraph_text(el).strip(_V_BLANK))


def _v_copy_ppr(anchor):
    """insert_paragraph's inherit rule (this module's reading, via props) without the numbering."""
    c = _v_inherit_ppr(anchor)
    if c is not None:
        for n in c.findall(W + "numPr"):
            c.remove(n)
        if not len(c) and not c.attrib:
            return None
    return c


def _end_of_section(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    main = ctx.src.main_part()
    try:
        number = usable_text(fill_value(ctx.src, "number", a.get("number") or {}))
    except (ValueError, KeyError, re.error) as e:
        errs.append(f"end_of_section: the number cannot be extracted here ({e})")
        return "end_of_section: no number"
    line = (a.get("text") or "").replace("{number}", number)
    if line != a.get("expected_line"):
        errs.append(f"end_of_section: the line is {line!r} here, the plan says {a.get('expected_line')!r}")
    sv, ov = ctx.view(main)
    body = sv.root.find(W + "body")
    kids = [c for c in body if isinstance(c.tag, str) and c.tag != W + "sectPr"]
    last = next((c for c in reversed(kids) if not _v_empty_p(c)), None)
    detect = re.compile(a.get("detect_regex") or r"(?i)^\s*END\s+OF\s+SECTION\b")
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    old_ids = part_revision_ids(sv.root)
    if last is None:
        errs.append("end_of_section: the body is empty — the op must refuse")
        return "end_of_section"
    if last.tag == W + "p" and detect.search(v_paragraph_text(last)):
        text = v_paragraph_text(last)
        bare = text.strip(_V_BLANK)
        case = "none" if bare == line else "replace"
    elif any(detect.search(v_paragraph_text(p)) for p in sv.pars):
        errs.append("end_of_section: an END OF SECTION line sits before other content — the op must refuse")
        return "end_of_section"
    elif last.tag == W + "p" and last.find(f"{W}pPr/{W}sectPr") is not None:
        errs.append("end_of_section: the last content carries a section break — the op must refuse")
        return "end_of_section"
    else:
        case = "insert"
    if case != a.get("expected_case"):
        errs.append(f"end_of_section: {case} here, the plan says {a.get('expected_case')}")
        return f"end_of_section: {case}"
    if case == "replace":
        bi = sv.pars.index(last)
        lo = len(text) - len(text.lstrip(_V_BLANK))
        check_edited_paragraph(last, ov.pars[bi], [(lo, lo + len(bare), line, False)], tracked, author,
                               _v_field_runs(sv.root), old_ids, f"end_of_section {main} block {bi}", errs)
        ctx.verified_blocks.setdefault(main, {})[bi] = bi
    elif case == "insert":
        okids = [c for c in ov.root.find(W + "body") if isinstance(c.tag, str) and c.tag != W + "sectPr"]
        k = kids.index(last)
        if len(okids) != len(kids) + 1 or okids[k + 1].tag != W + "p":
            errs.append("end_of_section: the output body is not the source with one paragraph after the last content")
            return "end_of_section: insert"
        new = okids[k + 1]
        oi = ov.pars.index(new)
        ctx.inserted_out_blocks.setdefault(main, set()).add(oi)
        where = f"end_of_section {main} new block {oi}"
        if v_paragraph_text(new) != line:
            errs.append(f"{where}: reads {v_paragraph_text(new)!r}, expected {line!r}")
        if a.get("formatting") == "copy":
            hits = [p for p in sv.pars if p.getparent() is body and re.search(a.get("copy_from_regex") or "(?!)",
                                                                             v_paragraph_text(p))]
            if len(hits) != 1:
                errs.append(f"{where}: copy_from_regex matches {len(hits)} paragraph(s) here — the op must refuse")
                return "end_of_section: insert"
            want_ppr = _v_copy_ppr(hits[0])
            want_rpr = _v_inherit_rpr(hits[0], sv.runs[sv.pars.index(hits[0])])
        else:
            want_ppr = None
            if a.get("style"):
                if a["style"] not in ctx.src_styles.by_id:
                    errs.append(f"{where}: the file has no style {a['style']!r} — the op must refuse")
                want_ppr = etree.Element(W + "pPr")
                etree.SubElement(want_ppr, W + "pStyle").set(W + "val", a["style"])
            want_rpr = None
        got_ppr, marked = ppr_norm(new.find(W + "pPr"), author if tracked else None, old_ids, mark="ins")
        if got_ppr != ppr_norm(want_ppr)[0]:
            errs.append(f"{where}: paragraph properties are not the ones the rule gives")
        if tracked and not marked:
            errs.append(f"{where}: the paragraph mark carries no new w:ins by {author!r}")
        holder = [c for c in new if isinstance(c.tag, str) and c.tag != W + "pPr"]
        if tracked:
            ins = holder[0] if len(holder) == 1 and holder[0].tag == W + "ins" else None
            if ins is None or ins.get(W + "author") != author or ins.get(W + "id") in old_ids or not ins.get(W + "date"):
                errs.append(f"{where}: the text is not in one new w:ins by {author!r}")
                return "end_of_section: insert"
            holder = [c for c in ins if isinstance(c.tag, str)]
        if len(holder) != 1 or holder[0].tag != W + "r" or \
                [c.tag for c in holder[0] if isinstance(c.tag, str) and c.tag != W + "rPr"] != [W + "t"]:
            errs.append(f"{where}: the line must be one run holding one w:t")
        elif sig(holder[0].find(W + "rPr")) != sig(want_rpr):
            errs.append(f"{where}: run properties are not the ones the rule gives")
    return f"end_of_section: {case} ({line!r})"


# ---- output file names and agreeing values -----------------------------------------------------------------------------

_V_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_V_DEVICES = {"con", "prn", "aux", "nul"} | {f"{k}{i}" for k in ("com", "lpt") for i in range(1, 10)}


def _v_base(path: Optional[str]) -> str:
    return re.split(r"[\\/]", path or "")[-1]


def _v_stem(path: Optional[str]) -> str:
    base = _v_base(path)
    return base[:-5] if base.lower().endswith(".docx") else base


def _v_transformed(value: str, how: str) -> str:
    if how == "digits":
        return "".join(ch for ch in value if ch.isdigit())
    if how in ("upper", "lower"):
        return value.upper() if how == "upper" else value.lower()
    if how == "title":
        out, k = [], 0
        for m in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)*", value):
            out.append(value[k:m.start()] + m.group(0)[0].upper() + m.group(0)[1:].lower())
            k = m.end()
        return "".join(out) + value[k:]
    return value


def _v_name_problem(name: str) -> Optional[str]:
    import unicodedata
    stem = name[:-5] if name.lower().endswith(".docx") else name
    if not stem.strip():
        return "empty"
    if set(name) & set('\\/:*?"<>|'):
        return "a character Windows forbids"
    if any(ord(c) < 32 or unicodedata.category(c) in {"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"} for c in name):
        return "a control or invisible character"
    if stem[:1] == " " or stem[-1:] in (" ", "."):
        return "a leading space or a trailing space/dot"
    if stem.split(".")[0].strip().lower() in _V_DEVICES:
        return "a device name"
    if len(name) > 200:
        return "too long"
    return None


def _output_name(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    got: Dict[str, str] = {}
    for k, rule in (a.get("values") or {}).items():
        try:
            got[k] = _v_transformed(fill_value(ctx.src, k, rule), (a.get("transforms") or {}).get(k, "as_is"))
        except (ValueError, KeyError, re.error) as e:
            errs.append(f"output_name: {k} cannot be extracted here ({e})")
            return "output_name"
    pattern = a.get("pattern") or ""
    if set(_V_PLACEHOLDER.findall(pattern)) != set(got):
        errs.append("output_name: the pattern's placeholders and the values differ")
        return "output_name"
    name = _V_PLACEHOLDER.sub(lambda m: got[m.group(1)], pattern)
    name = name if name.lower().endswith(".docx") else name + ".docx"
    problem = _v_name_problem(name)
    if problem:
        errs.append(f"output_name: {name!r} has {problem} — the op must refuse")
    if name != a.get("expected"):
        errs.append(f"output_name: {name!r} here, the plan predicted {a.get('expected')!r}")
    if _v_base(ctx.output_name) != name:
        errs.append(f"output_name: the output was written as {_v_base(ctx.output_name)!r}, not {name!r}")
    return f"output_name: {name!r}"


def _v_span(m, g: str) -> Optional[Tuple[int, int]]:
    a, b = m.span(g)
    if a < 0:
        return None
    piece = m.string[a:b]
    a2 = a + (len(piece) - len(piece.lstrip()))
    b2 = b - (len(piece) - len(piece.rstrip()))
    return (a2, b2) if b2 > a2 else None


def _v_agree(x: str, y: str) -> bool:
    from .template import _v_tokens
    return "".join(_v_tokens(x)) == "".join(_v_tokens(y))


def _agreed_values(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    try:
        places = {k: (v["in"], re.compile(v["pattern"]), bool(v.get("required"))) for k, v in a["places"].items()}
    except (KeyError, TypeError, re.error) as e:
        errs.append(f"agreed_values: {e}")
        return "agreed_values"
    truth, names = a.get("truth"), list(a.get("values") or [])
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    stem = _v_stem(ctx.source_name)
    found: Dict[str, List[Tuple[str, int, Any, List[Any]]]] = {}
    for pname, (where, rx, _req) in places.items():
        if where == "filename":
            m = rx.search(stem)
            found[pname] = [("filename", -1, None, [m])] if m else []
            continue
        found[pname] = []
        for part in ctx.scope_parts([where]):
            sv, _ = ctx.view(part)
            for bi, p in enumerate(sv.pars):
                ms = list(rx.finditer(v_paragraph_text(p)))
                if ms:
                    found[pname].append((part, bi, p, ms))
    truths: Dict[str, str] = {}
    for g in names:
        vals = [m.string[s[0]:s[1]] for _, _, _, ms in found.get(truth, []) for m in ms
                for s in [_v_span(m, g)] if s is not None]
        if not vals or len({" ".join(v.split()) for v in vals}) > 1:
            errs.append(f"agreed_values: the truth gives {g} as {sorted(set(vals))} — the op must refuse")
            return "agreed_values"
        truths[g] = vals[0]
    if truths != a.get("expected_truth"):
        errs.append(f"agreed_values: the truth says {truths} here, the plan {a.get('expected_truth')}")
    edits: Dict[Tuple[str, int], List[Tuple[int, int, str, bool]]] = {}
    paras: Dict[Tuple[str, int], Any] = {}
    stem_edits: List[Tuple[int, int, str]] = []
    for pname, (where, rx, required) in places.items():
        if pname == truth:
            continue
        if not found[pname] and required:
            errs.append(f"agreed_values: {pname} shows no match — the op must refuse")
        for part, bi, p, ms in found[pname]:
            for m in ms:
                for g in names:
                    if g not in rx.groupindex:
                        continue
                    s = _v_span(m, g)
                    if s is None or _v_agree(m.string[s[0]:s[1]], truths[g]):
                        continue
                    if where == "filename":
                        stem_edits.append((s[0], s[1], truths[g]))
                    else:
                        edits.setdefault((part, bi), []).append((s[0], s[1], truths[g], False))
                        paras[(part, bi)] = p
    n, edited = 0, {}
    for (part, bi), es in sorted(edits.items()):
        sv, ov = ctx.view(part)
        if len(sv.pars) != len(ov.pars):
            errs.append(f"agreed_values: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        p = paras[(part, bi)]
        text = v_paragraph_text(p)
        es = [e for e in es if text[e[0]:e[1]] != e[2]]
        if not es:
            continue
        if _v_owner(p) is not None:
            errs.append(f"agreed_values: {part} block {bi} is in a text box — the op must refuse")
            continue
        n += len(es)
        edited.setdefault(part, []).append(bi)
        check_edited_paragraph(p, ov.pars[bi], es, tracked, author, _v_field_runs(sv.root),
                               part_revision_ids(sv.root), f"agreed_values {part} block {bi}", errs)
        ctx.verified_blocks.setdefault(part, {})[bi] = bi
    if n != a.get("expected_edits", -1):
        errs.append(f"agreed_values: {n} correction(s) here, the plan predicted {a.get('expected_edits')}")
    if edited != {k: list(v) for k, v in (a.get("edited_blocks") or {}).items()}:
        errs.append(f"agreed_values: corrected paragraphs {edited} here, the plan predicted {a.get('edited_blocks')}")
    name = None
    if stem_edits:
        new = stem
        for x, y, v in sorted(stem_edits, reverse=True):
            new = new[:x] + v + new[y:]
        name = new + ".docx"
        problem = _v_name_problem(name)
        if problem:
            errs.append(f"agreed_values: the corrected name {name!r} has {problem} — the op must refuse")
        if _v_base(ctx.output_name) != name:
            errs.append(f"agreed_values: the output was written as {_v_base(ctx.output_name)!r}, not {name!r}")
    if name != a.get("expected_name"):
        errs.append(f"agreed_values: the output name is {name!r} here, the plan predicted {a.get('expected_name')!r}")
    return f"agreed_values: {n} correction(s)" + (f", file name {name!r}" if name else "")


TEXT_OP_KINDS = {
    "replaced_text": _replaced_text,
    "filled_variables": _filled_variables,
    "resolved_options": _resolved_options,
    "tagged_paragraphs": _tagged_paragraphs,
    "end_of_section": _end_of_section,
    "output_name": _output_name,
    "agreed_values": _agreed_values,
}
