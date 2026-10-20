"""Op: fill_variables — ``{{NAME}}`` placeholders and highlighted text in the body, headers and footers filled from a
per-project table (design note §1): one pass turns a master set into a site set.

Slots are the templating op's (CLAUDE.md "Slots": a maximal sequence of highlighted runs, named by its text — or by NAME
when it reads ``{{NAME}}`` — and every ``{{NAME}}`` in the display text, even split over runs), found by the same code
(``slots.find_slots``; the rule that a slot's runs hold only text is dropped — the in-paragraph edit core places a value
in any run — but a highlighted slot must still be whole runs of text: its highlight goes). The value of a slot comes
from ``values`` (typed: the user's words, checked by the compiler) or, failing that, the ``table`` file the user keeps
(``ops/tables.py``: names down one column, one column per site). Names match with whitespace collapsed. A slot with no
value is refused (``unfilled-slot``) or, with ``unknown: "leave"``, left as it is and flagged. Each slot is one edit of
the core (ops/textedit.py): the value takes the slot's first run's formatting. The verifier re-detects the slots,
re-reads the table and checks every filled paragraph item by item (verify/textops.py, kind ``filled_variables``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage
from . import prose, register, tables
from .base import JobException, Op, PlanFragment
from .replace_table import COMMON_PARAMS, default_author, resolve_scope, tracked_of
from .selectors import SCOPE_KINDS, scope_parts
from .slots import display_text, field_runs, find_slots
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph, shown

DEFAULT_SCOPE = ["document", "headers", "footers"]
MARKERS = ("highlight", "braces")


def norm_name(name: str) -> str:
    return " ".join(str(name).split())


def text_positions(rt: RunText) -> List[int]:
    """Run-text index of each display character (the k-th w:t character of the live runs)."""
    return [i for i, ps in enumerate(rt.positions) if ps.offset >= 0]


def slot_range(idx: List[int], slot) -> Tuple[int, int]:
    """The slot's display offsets as a run-text range."""
    return idx[slot.start], idx[slot.end - 1] + 1


@register
class FillVariables(Op):
    name = "fill_variables"
    mode = "content"
    description = ("Fill {{NAME}} placeholders and yellow-highlighted text in the body, headers and footers with each "
                   "project's own values: typed 'values' (the user's words) and/or a 'table' file the user keeps "
                   "(PROJECT.json, .csv or .xlsx: names down one column, one column per site) — one pass turns a "
                   "master set into a site set. Highlights go; tracked (default) or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "values": {"type": "object", "description": "NAME → the user's exact value (wins over the table)"},
            "table": {"type": "object", "properties": {
                "file": {"type": "string", "description": ".json, .csv or .xlsx the user keeps (absolute path)"},
                "sheet": {"type": "string", "description": "xlsx: the sheet (default: the first)"},
                "key": {"type": "string", "description": "json: dotted key of the object of NAME → value"},
                "name_column": {"type": "string", "description": "csv/xlsx: header of the names column (default: 1st)"},
                "value_column": {"type": "string",
                                 "description": "csv/xlsx: header of this project's/site's column (default: 2nd)"}}},
            "slot_markers": {"type": "array", "items": {"type": "string", "enum": list(MARKERS)},
                             "default": list(MARKERS)},
            "highlight_colors": {"type": "array", "items": {"type": "string"}, "default": ["yellow"]},
            "unknown": {"type": "string", "enum": ["refuse", "leave"], "default": "refuse",
                        "description": "a placeholder with no value: refuse the file, or leave it as it is (flagged)"},
            **COMMON_PARAMS,
        },
    }
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document, headers, footers)"}}}
    verification_profile = {
        "V2": "one BlockDiff per filled paragraph",
        "V4": "tracked: +1 del per run a slot touches, +1 ins per slot",
        "V10": "filled_variables re-detects every slot (verify/template.slots_of), re-reads the table, re-derives each "
               "value and checks every filled paragraph item by item; nothing else changes",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException(f"fill_variables selects by scope only; unknown {sorted(set(select) - {'scope'})}",
                               "bad-params")
        scope = resolve_scope(select, params, DEFAULT_SCOPE)
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        author = params.get("author") or default_author()
        markers = list(params.get("slot_markers") or MARKERS)
        if not markers or set(markers) - set(MARKERS):
            raise JobException(f"slot_markers must be some of {list(MARKERS)}", "bad-params")
        colors = list(params.get("highlight_colors") or ["yellow"])
        unknown = params.get("unknown", "refuse")
        if unknown not in ("refuse", "leave"):
            raise JobException("unknown must be 'refuse' or 'leave'", "bad-params")
        typed = params.get("values") or {}
        if not isinstance(typed, dict) or not all(isinstance(v, str) for v in typed.values()):
            raise JobException("'values' must map names to text", "bad-params")
        values: Dict[str, Any] = {norm_name(k): v for k, v in typed.items()}
        table = None
        from_table: Dict[str, Any] = {}
        if params.get("table") is not None:
            got, path, md5 = tables.variables(params["table"])
            table = {**params["table"], "file": path, "md5": md5}
            from_table = {norm_name(k): v for k, v in got.items()}
        if not values and table is None:
            raise JobException("give the values ('values') or the table they are in ('table')", "bad-params")
        frag = self.fragment({"scope": scope}, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        data: Dict[str, Dict[str, List[List[Any]]]] = {}
        filled: Dict[str, int] = {}
        left: Dict[str, int] = {}
        used_values: Dict[str, str] = {}
        for part in parts:
            root = pkg.xml(part)
            in_field = field_runs(root)
            for bi, p in enumerate(paragraphs(root)):
                where = f"{part} paragraph {bi}"
                slots = find_slots(p, markers, colors, where, in_field=in_field, text_only=False)
                if not slots:
                    continue
                if nearest_p(p) is not None:
                    raise JobException(f"{where} is inside a text box (Word keeps two copies of it); a placeholder "
                                       f"there is not filled", "textbox", {"where": where})
                rt = RunText(p)
                idx = text_positions(rt)
                if "".join(rt.text[i] for i in idx) != display_text(p):
                    raise JobException(f"{where}: the paragraph holds text outside its plain runs (an embedded object "
                                       f"…); not filled", "structure", {"where": where})
                edits = []
                for s in slots:
                    key = norm_name(s.name)
                    if key in values:
                        value = values[key]
                    elif key in from_table:
                        value = from_table[key]
                    else:
                        if unknown == "leave":
                            left[key] = left.get(key, 0) + 1
                            continue
                        raise JobException(f"{where}: no value for {s.placeholder!r} ({key}) — add it to the "
                                           f"{'table' if table else 'values'}", "unfilled-slot",
                                           {"where": where, "slot": key})
                    value = tables.placeable(value, f"{key}")
                    used_values[key] = value
                    a, b = slot_range(idx, s)
                    edits.append(Edit(a, b, value, drop_highlight=s.marker in ("highlight", "both")))
                done = plan_paragraph(frag, part, bi, p, edits, tracked, in_field, where)
                if done:
                    data.setdefault(part, {})[str(bi)] = edits_to_data(done)
                for s in slots:
                    if norm_name(s.name) in used_values:
                        filled[norm_name(s.name)] = filled.get(norm_name(s.name), 0) + 1
        total = sum(len(v) for blocks in data.values() for v in blocks.values())
        frag.property_assertions.append({
            "kind": "filled_variables", "scope": scope, "values": typed, "table": table, "slot_markers": markers,
            "highlight_colors": colors, "unknown": unknown, "tracked": tracked, "author": author,
            "expected_edits": total,
            "edited_blocks": {part: sorted(int(b) for b in blocks) for part, blocks in data.items()}})
        if left:
            frag.flags.append("placeholders left as they are (no value): "
                              + ", ".join(f"{k} ×{n}" for k, n in sorted(left.items())))
        if total == 0:
            if params.get("allow_missing") or left:
                frag.notes.append("fill_variables: nothing to fill; no change.")
                return frag
            raise JobException(f"fill_variables: no placeholder or highlighted text in {scope}", "not-found",
                               {"scope": scope})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = frag.rev_delta.get("ins", 0) + frag.rev_delta.get("del", 0)
        frag.transform_data = {"edits": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z"), "table": table}
        src = f" (table {table['file']})" if table else ""
        frag.notes.append(f"fill_variables: {total} placeholder(s) filled ({'tracked' if tracked else 'untracked'})"
                          f"{src}: " + "; ".join(f"{k} → {used_values[k]!r} ×{n}" for k, n in sorted(filled.items())))
        if not tracked:
            frag.flags.append("UNTRACKED fill at the user's request: no revision marks; V2's list is the proof.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("edits"):
            return
        tables.check_unchanged(td.get("table"), self.name)
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, by_block in td["edits"].items():
            root = pkg.xml(part)
            apply_part_edits(root, by_block, td["tracked"], stamp, next_id)
            pkg.write_xml(part, root)

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        scope = select.get("scope") or params.get("scope") or DEFAULT_SCOPE
        markers = [m for m in (params.get("slot_markers") or MARKERS) if m in MARKERS] or list(MARKERS)
        colors = [str(c) for c in (params.get("highlight_colors") or ["yellow"])]
        kinds: List[str] = []
        if "braces" in markers:
            kinds.append("the placeholders written as " + prose.quote("{{NAME}}"))
        if "highlight" in markers:
            kinds.append("the yellow-highlighted text" if colors == ["yellow"]
                         else "the text highlighted in " + prose.join(colors, "or"))
        where = prose.scope_words(scope) or "in the document body"
        typed = params.get("values") if isinstance(params.get("values"), dict) else {}
        shown = [f"{prose.quote(k)} becomes {prose.quote(v)}" for k, v in typed.items()]
        if len(shown) > 4:
            shown = shown[:3] + [f"{len(shown) - 3} more"]
        spec = params.get("table")
        table = ""
        if isinstance(spec, dict) and spec.get("file"):
            details: List[str] = []
            if spec.get("sheet"):
                details.append(f"sheet {prose.quote(spec['sheet'])}")
            if spec.get("key"):
                details.append(f"entry {prose.quote(spec['key'])}")
            if spec.get("name_column"):
                details.append(f"names in the column {prose.quote(spec['name_column'])}")
            if spec.get("value_column"):
                details.append(f"values in the column {prose.quote(spec['value_column'])}")
            table = f"the file {prose.quote(spec['file'])}" + (f" ({', '.join(details)})" if details else "")
        if shown and table:
            source = "with the project's values: " + prose.join(shown) + ", taking any other value from " + table
        elif shown:
            source = "with the project's values: " + prose.join(shown)
        elif table:
            source = "with the values in " + table
        else:
            source = "with the project's values"
        first = f"Fill {prose.join(kinds)} {where} {source}."
        effects: List[str] = []
        if params.get("unknown") == "leave":
            effects.append("a placeholder with no value is left as it is")
        if "highlight" in markers:
            effects.append("the highlight is removed")
        if bool(params.get("tracked", True)):
            effects.append("each fill is shown as a tracked change"
                           + (f" by {prose.quote(params['author'])}" if params.get("author") else ""))
        else:
            effects.append("the fills are made without tracked changes")
        second = prose.join(effects)
        second = second[0].upper() + second[1:] + "."
        third = "Files with nothing to fill are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second, third)
