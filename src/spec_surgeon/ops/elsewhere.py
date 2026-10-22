"""Wording left elsewhere (docs/PLAN-2026-10-22-batch-consistency.md C3): a change kept to one place says where the
same words remain. ``flag_left_elsewhere`` runs once every op of a spec is planned on a file (``editspec.plan_spec``,
before the merge), so a place another op of the spec changes is never named:

* ``replace_text``: each place its ``find`` (the same matching) also occurs and no ``replace_text`` of the spec edits —
  the plan records the occurrences it reads (``transform_data["left_elsewhere"]``);
* ``apply_header_footer_template``: a literal fill of a reference slot (SECTION_NUMBER, SECTION_TITLE) whose words
  differ from the document's own, read by ``REFERENCE_FILLS`` (a difference of case alone is not this flag's);
* ``apply_header_footer_template`` (C4, ``CASE_DIFFERS``): a fill whose letters will show in another case than the
  template's own text in that slot (a template spec's title, a highlighted placeholder's words; a {{NAME}} is a name,
  not text), where the template shows neither capitals nor small capitals by formatting and ``caps_fills`` does not
  name the slot: "Identification for …" where the template shows "DOOR HARDWARE".

Flags only (``LEFT_ELSEWHERE``, ``CASE_DIFFERS``): nothing is predicted from them, so the reads behind them are no
``touches``.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..package import DocxPackage
from .base import CASE_DIFFERS, LEFT_ELSEWHERE, JobException, PlanFragment
from .slots import REFERENCE_FILLS, extract_fill, value_tokens


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


def _supplier(pkg: DocxPackage, name: str, rule: Any) -> Any:
    """The rule that supplies a fill: a first_match chain's first rule that extracts one, as ``extract_fill`` tries
    them (a regex that reads the same words as a later literal fallback supplied them, not the literal)."""
    if isinstance(rule, dict) and isinstance(rule.get("rules"), list) and rule.get("rule") in (None, "first_match"):
        for sub in rule["rules"]:
            try:
                extract_fill(pkg, name, sub)
            except JobException:
                continue
            return _supplier(pkg, name, sub)
        return None
    return rule


def _template_fills(pkg: DocxPackage, ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    for o, f in zip(ops, frags):
        if o.get("op") != "apply_header_footer_template":
            continue
        rules = f.params.get("slot_fills") or {}
        bound = f.params.get("slot_bindings") or {}
        for name, value in sorted((f.transform_data.get("fills") or {}).items()):
            typed = (bound.get(name) or {}).get("rule") == "typed"     # the user's wording for this one file
            if name not in REFERENCE_FILLS or (name in bound and not typed) or \
                    (not typed and (_supplier(pkg, name, rules.get(name)) or {}).get("rule") != "literal"):
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


def letters_case(text: str) -> str:
    """ "capitals", "lower case", "mixed case", or "" without a cased letter — the letters as typed."""
    cased = [c for c in text or "" if c.isupper() or c.islower()]
    if not cased:
        return ""
    if all(c.isupper() for c in cased):
        return "capitals"
    return "lower case" if all(c.islower() for c in cased) else "mixed case"


def _fill_case(ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    for o, f in zip(ops, frags):
        if o.get("op") != "apply_header_footer_template":
            continue
        caps = set(f.params.get("caps_fills") or [])
        fills = f.transform_data.get("fills") or {}
        noted = set()
        for c in f.transform_data.get("copies") or []:
            for entry in (e for sl in (c.get("slots") or {}).values() for e in sl):
                name = entry["name"]
                if name in noted or name in caps or entry.get("shown") or entry.get("marker") == "braces" \
                        or name not in fills:
                    continue
                theirs, ours = letters_case(entry.get("template_text", "")), letters_case(fills[name])
                if not theirs or not ours or theirs == ours:
                    continue
                noted.add(name)
                what = name.lower().replace("_", " ")
                offer = (" It can show in capitals like the template's through formatting, without retyping a "
                         "letter." if theirs == "capitals" else "")
                f.flags.append(f"{CASE_DIFFERS}the {what} “{fills[name]}” will show in {ours} in the headers/footers, "
                               f"where the template shows “{entry.get('template_text')}” in {theirs}.{offer}")


def flag_left_elsewhere(pkg: DocxPackage, ops: List[Dict[str, Any]], frags: List[PlanFragment]) -> None:
    """Add the C3 flags to a spec's fragments (``ops`` and ``frags`` in spec order): two replacements that rename the
    footer and the body leave nothing to say; one that renames only the footer says the body keeps the old words."""
    _replacements(ops, frags)
    _template_fills(pkg, ops, frags)
    _fill_case(ops, frags)
