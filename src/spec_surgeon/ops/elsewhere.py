"""Wording left elsewhere (docs/PLAN-2026-10-22-batch-consistency.md C3): a change kept to one place says where the
same words remain. ``flag_left_elsewhere`` runs once every op of a spec is planned on a file (``editspec.plan_spec``,
before the merge), so a place another op of the spec changes is never named:

* ``replace_text``: each place its ``find`` (the same matching) also occurs and no ``replace_text`` of the spec edits —
  the plan records the occurrences it reads (``transform_data["left_elsewhere"]``);
* ``apply_header_footer_template``: a literal fill of a reference slot (SECTION_NUMBER, SECTION_TITLE) whose words
  differ from the document's own, read by ``REFERENCE_FILLS`` (a difference of case alone is not this flag's).

Flags only (``LEFT_ELSEWHERE``): nothing is predicted from them, so the reads behind them are no ``touches``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..package import DocxPackage
from .base import LEFT_ELSEWHERE, JobException, PlanFragment
from .slots import REFERENCE_FILLS, extract_fill, literal_values, value_tokens


def places_text(left: List[list]) -> str:
    """ "the body (1×) and the headers (2×, 1 in a field's result)" for occurrences [part, paragraph, start, end,
    in a field, where]."""
    counts: Dict[str, List[int]] = {}
    for *_, in_field, where in left:
        c = counts.setdefault(where, [0, 0])
        c[0] += 1
        c[1] += 1 if in_field else 0
    bits = [f"{where} ({n}×" + (f", {k} in a field's result" if k else "") + ")" for where, (n, k) in counts.items()]
    return ", ".join(bits[:-1]) + " and " + bits[-1] if len(bits) > 1 else (bits[0] if bits else "")


def _replacements(ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    edited = [(e["part"], e["paragraph"], a, b) for o, f in zip(ops, frags) if o.get("op") == "replace_text"
              for e in f.transform_data.get("edits", []) for a, b in e["live_offsets"]]
    for o, f in zip(ops, frags):
        if o.get("op") != "replace_text":
            continue
        left = [x for x in f.transform_data.get("left_elsewhere") or []
                if not any(x[0] == part and x[1] == pi and x[2] < b and a < x[3] for part, pi, a, b in edited)]
        if left:
            f.flags.append(f"{LEFT_ELSEWHERE}“{f.params.get('find')}” also occurs in {places_text(left)}; this change "
                           f"leaves it as it is.")


def _template_fills(pkg: DocxPackage, ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    for o, f in zip(ops, frags):
        if o.get("op") != "apply_header_footer_template":
            continue
        rules = f.params.get("slot_fills") or {}
        bound = f.params.get("slot_bindings") or {}
        for name, value in sorted((f.transform_data.get("fills") or {}).items()):
            if name not in REFERENCE_FILLS or name in bound or value not in literal_values(rules.get(name)):
                continue                       # no reference rule, the file's own words, or a rule that read them
            try:
                own = extract_fill(pkg, name, REFERENCE_FILLS[name])
            except JobException:
                continue                       # the document has no value of its own to compare
            if value_tokens(own) != value_tokens(value):
                what = name.lower().replace("_", " ")
                f.flags.append(f"{LEFT_ELSEWHERE}the headers/footers get the words given for the {what}, “{value}”, "
                               f"while the document's own {what} reads “{own}”; this change leaves the document as it "
                               f"is.")


def flag_left_elsewhere(pkg: DocxPackage, ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    """Add the C3 flags to a spec's fragments (``ops`` and ``frags`` in spec order): two replacements that rename the
    footer and the body leave nothing to say; one that renames only the footer says the body keeps the old words."""
    _replacements(ops, frags)
    _template_fills(pkg, ops, frags)
