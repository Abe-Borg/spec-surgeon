"""Independent source-derived proof for the original literal replacement operation.

Matching uses the user's words and selectors, never the transform's offset list.
The item comparison retains each character's formatting, inline anchors, links,
structural characters and revision context. Optional paragraph-local coalescing
is derived here from source siblings, with the explicitly requested noise rule.
"""
from __future__ import annotations

import copy
import re

from .edits import check_edited_paragraph, part_revision_ids, v_check_edits, v_items
from .props import para_matches, sig
from .reader import W
from .template import _v_field_runs
from .textops import _v_flexible


_INERT = {W + "proofErr", W + "lastRenderedPageBreak"}
_MERGEABLE = {W + n for n in ("rPr", "t", "tab", "br", "cr", "noBreakHyphen", "softHyphen")}


def _font_key(run, noise):
    rpr = copy.deepcopy(run.find(W + "rPr"))
    if rpr is None:
        return None
    if noise:
        for child in list(rpr):
            if child.tag in {W + "lang", W + "noProof", W + "webHidden"}:
                rpr.remove(child)
            elif child.tag == W + "color" and child.get(W + "val") in {"auto", "000000"}:
                rpr.remove(child)
            elif child.tag == W + "szCs":
                size = rpr.find(W + "sz")
                if size is not None and size.get(W + "val") == child.get(W + "val"):
                    rpr.remove(child)
    for el in rpr.iter():
        for key in list(el.attrib):
            if "rsid" in key:
                del el.attrib[key]
    return sig(rpr) if len(rpr) or rpr.attrib else None


def _coalesced(paragraph, noise):
    """Source-only expected content/property inheritance of coalesced siblings."""
    root = copy.deepcopy(paragraph)
    holders = [root] + [el for el in root.iter() if el.tag in {W + "ins", W + "hyperlink", W + "sdtContent"}]
    allowed = _MERGEABLE | (_INERT if noise else set())
    for holder in holders:
        active, pending = None, []
        for child in list(holder):
            eligible = child.tag == W + "r" and all(c.tag in allowed for c in child)
            if active is not None and noise and child.tag in _INERT:
                pending.append(child)
                continue
            if not eligible:
                active, pending = None, []
                continue
            if active is None or _font_key(active, noise) != _font_key(child, noise):
                active, pending = child, []
                continue
            for marker in pending:
                holder.remove(marker)
            pending = []
            if noise:
                for run in (active, child):
                    for element in list(run):
                        if element.tag in _INERT:
                            run.remove(element)
            for element in list(child):
                if element.tag != W + "rPr":
                    active.append(element)
            holder.remove(child)
    return root


def _characters(paragraph):
    return [item for item in v_items(paragraph) if item.kind == "t"]


def _single_text_node(chars, match):
    return chars[match.start()].el is chars[match.end() - 1].el


def _coalescing_needed(chars, hits):
    # A coalesced run may retain several adjacent text nodes here; they form
    # one transform-side text node unless a structural child lies between them.
    for hit in hits:
        first, last = chars[hit.start()], chars[hit.end() - 1]
        if first.run is not last.run:
            return True
        children = list(first.run)
        a, b = children.index(first.el), children.index(last.el)
        if any(c.tag != W + "t" for c in children[a:b + 1]):
            return True
    return False


def _normalize_split_ids(before, after, original_ids, errors, where):
    """Only fresh copies of source formatting revisions may use another id."""
    source_changes = {el.get(W + "id"): sig(el) for el in before.iter(W + "rPrChange")}
    present = {el.get(W + "id") for el in after.iter(W + "rPrChange")}
    for value in source_changes:
        if value not in present:
            errors.append(f"{where}: original formatting-revision id {value!r} disappeared")
    for el in after.iter(W + "rPrChange"):
        value = el.get(W + "id")
        if value in original_ids and (value not in source_changes or sig(el) != source_changes[value]):
            errors.append(f"{where}: an existing formatting revision changed or was borrowed")
    for root in (before, after):
        for el in root.iter(W + "rPrChange"):
            el.set(W + "id", "#split-copy")


def literal_replacement(ctx, assertion, errors):
    params, select = assertion.get("params", {}), assertion.get("select", {})
    find, replacement = params.get("find"), params.get("replace")
    if not isinstance(find, str) or not find or not isinstance(replacement, str):
        raise ValueError("literal replacement needs exact find/replace strings")
    tracked = params.get("tracked", params.get("mode", "tracked") != "untracked")
    regex = re.compile(_v_flexible(find) if params.get("flexible_whitespace", False) else re.escape(find),
                       0 if params.get("match_case", True) else re.IGNORECASE)
    scope = select.get("scope") or params.get("scope") or ["document"]
    parts = ctx.scope_parts([scope] if isinstance(scope, str) else scope)
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(name)) for name in ctx.src_tp))
    count, edited, stop = 0, {}, False
    for part in parts:
        if stop:
            break
        if part not in ctx.both:
            errors.append(f"literal_replacement: {part} is missing")
            continue
        source_view, output_view = ctx.view(part)
        field_runs = _v_field_runs(source_view.root)
        for index, paragraph in enumerate(source_view.pars):
            if stop:
                break
            if not para_matches(select.get("paragraphs"), paragraph, index, source_view, ctx.src_styles):
                continue
            chars = _characters(paragraph)
            hits = list(regex.finditer("".join(item.value for item in chars)))
            if not hits:
                continue
            base = paragraph
            if any(not _single_text_node(chars, match) for match in hits):
                base = _coalesced(paragraph, False)
                if _coalescing_needed(_characters(base), hits) and params.get("ignore_formatting_noise", False):
                    base = _coalesced(paragraph, True)
                if _coalescing_needed(_characters(base), hits):
                    errors.append(f"literal_replacement {part} block {index}: source matches span incompatible runs")
                    continue
            if params.get("occurrences", "all") == "first":
                hits, stop = hits[:1], True
            edits = [(chars[m.start()].pos, chars[m.end() - 1].pos + 1, replacement, False) for m in hits]
            allow_split = bool(params.get("allow_tracked_format_split", False))
            where = f"literal_replacement {part} block {index}"
            # the replacement may hold tabs and line breaks (built-in batch 6 §4.3): written as w:tab / w:br
            v_check_edits(paragraph, v_items(paragraph), edits, tracked, field_runs,
                          allow_tracked_format_split=allow_split, breaks=True)
            output_index = ctx.maps[part].get(index)
            if output_index is None:
                errors.append(f"{where}: original paragraph has no final counterpart")
                continue
            before, after = copy.deepcopy(base), copy.deepcopy(output_view.pars[output_index])
            if allow_split and tracked:
                _normalize_split_ids(before, after, old_ids, errors, where)
            check_edited_paragraph(before, after, edits, tracked, assertion.get("author"), set(), old_ids,
                                   where, errors, allow_tracked_format_split=allow_split, breaks=True)
            # The mature envelope check retains empty runs/containers and text
            # metadata in addition to the character-item proof above.
            from .effects import _check_edit_envelope
            _check_edit_envelope(base, output_view.pars[output_index], old_ids, where, errors)
            ctx.verified_blocks.setdefault(part, {})[index] = output_index
            count += len(hits)
            edited.setdefault(part, []).append(index)
    if count != assertion.get("expected_edits"):
        errors.append(f"literal_replacement: independently found {count} edits; predicted {assertion.get('expected_edits')}")
    if edited != assertion.get("edited_blocks", {}):
        errors.append(f"literal_replacement: independently edited blocks {edited}; predicted {assertion.get('edited_blocks')}")
    return f"literal_replacement: {count} independently derived edit(s), all other paragraph items preserved"


def paragraph_edits(ctx, assertion, errors):
    """Prove declared custom edits without granting a whole-paragraph exemption.

    Built-ins' source-derived rule assertions run first; this primitive neither
    replaces their matching rules nor changes their target decisions. Custom
    methods still require the independent approved-goal check for request intent.
    """
    part, index = assertion.get("part"), assertion.get("block_index")
    if part not in ctx.both or type(index) is not int or index < 0:
        raise ValueError("paragraph_edits needs an existing part and a source paragraph index")
    if part in ctx.verified_parts or index in ctx.verified_blocks.get(part, {}):
        return "paragraph_edits: already covered by its independently derived operation rule"
    source_view, output_view = ctx.view(part)
    output_index = ctx.maps[part].get(index)
    if index >= len(source_view.pars) or output_index is None:
        raise ValueError("paragraph_edits source paragraph has no final counterpart")
    edits = assertion.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("paragraph_edits needs a nonempty list of source-derived edits")
    for edit in edits:
        if (not isinstance(edit, (list, tuple)) or len(edit) != 4 or type(edit[0]) is not int
                or type(edit[1]) is not int or not isinstance(edit[2], str) or type(edit[3]) is not bool):
            raise ValueError("paragraph_edits requires [start, end, literal text, drop_highlight] entries")
    tracked = assertion.get("tracked", True)
    author = assertion.get("author")
    if type(tracked) is not bool or type(assertion.get("tabs", False)) is not bool \
            or type(assertion.get("breaks", False)) is not bool:
        raise ValueError("paragraph_edits tracked/tabs/breaks must be boolean")
    if tracked and (not isinstance(author, str) or not author.strip()):
        raise ValueError("tracked paragraph_edits needs its explicit approved author")
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(name)) for name in ctx.src_tp))
    before, after = source_view.pars[index], output_view.pars[output_index]
    where = f"paragraph_edits {part} block {index}"
    check_edited_paragraph(before, after, edits, tracked, author, _v_field_runs(source_view.root), old_ids,
                           where, errors, tabs=assertion.get("tabs", False), breaks=assertion.get("breaks", False))
    from .effects import _check_edit_envelope
    _check_edit_envelope(before, after, old_ids, where, errors)
    ctx.verified_blocks.setdefault(part, {})[index] = output_index
    return f"paragraph_edits: {len(edits)} declared edit(s), every other paragraph item preserved"
