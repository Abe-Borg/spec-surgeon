"""Independent final requirements for a chain, separate from each stage's V1–V10.

These are absolute post-conditions over the FINAL package, including parts added by later stages. They never
grant permission to change anything, and never strip anything from V10. Source-relative font exceptions
(``size_from``/``preserve_fonts``) are deliberately unsupported: resolving a font in a new header cannot prove
it was preserved from the original header. Conditional table-style formatting is refused when it could affect
a requested property; the independent base-style resolvers do not model Word's conditional table formatting.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

from .props import (PARA_FLAGS, PROP_PAIRS, PartView, Styles, Theme, _v_para_prop, _v_run_elements,
                    _v_run_prop, _v_table_style_id, effective_fonts, effective_size, sect_prs)
from .reader import STYLES_REL, THEME_REL, VPackage, W

KINDS = ("effective_font", "page_setup", "effective_properties")
SCOPES = ("document", "headers", "footers", "footnotes", "endnotes", "comments")
SLOTS = ("ascii", "hAnsi", "eastAsia", "cs")
MARGINS = ("top", "bottom", "left", "right", "header", "footer", "gutter")
COMMON = {"kind", "scope", "include_paragraph_marks", "allow_absent_scopes"}


def _error(where: str, message: str) -> None:
    raise ValueError(f"{where}: {message}")


def _keys(obj: Any, allowed, where: str) -> None:
    if not isinstance(obj, dict):
        _error(where, "must be an object")
    unknown = set(obj) - set(allowed)
    if unknown:
        _error(where, f"unknown key(s) {sorted(map(str, unknown))}")


def _choices(value, allowed, where: str) -> None:
    if (not isinstance(value, list) or not value or any(not isinstance(x, str) or x not in allowed for x in value)
            or len(value) != len(set(value))):
        _error(where, f"must be a non-empty list of distinct values from {list(allowed)}")


def validate_assertions(assertions: Any) -> None:
    """Validate the entire closed grammar; unknown keys, empty selections and wrong scalar types refuse early.

    effective_font: value and/or size (points, half-point increments), scope (default document), slots (default
    ascii/hAnsi/cs), include_paragraph_marks (default true). Size means w:sz, as in the existing font operations.
    page_setup: sections (all or zero-based indices), margins (twips), size ({w,h}, twips), orientation.
    effective_properties: run ({b,i,caps,smallCaps,strike}: booleans), paragraph (keepNext/keepLines/
    widowControl/pageBreakBefore: booleans), scope, include_paragraph_marks. All selected text is checked.
    Both text kinds support allow_absent_scopes (default false): a missing optional part kind may be ignored,
    but present parts remain in scope and the assertion must still check at least one target overall.
    """
    if not isinstance(assertions, list):
        _error("final assertions", "must be a list")
    for i, a in enumerate(assertions, 1):
        where = f"final assertion {i}"
        if not isinstance(a, dict) or not isinstance(a.get("kind"), str) or a["kind"] not in KINDS:
            _error(where, f"kind must be one of {list(KINDS)}")
        kind = a["kind"]
        if kind != "page_setup":
            _keys(a, COMMON | ({"value", "size", "slots"} if kind == "effective_font" else {"run", "paragraph"}), where)
            _choices(a.get("scope", ["document"]), SCOPES, f"{where}.scope")
            for flag in ("include_paragraph_marks", "allow_absent_scopes"):
                if flag in a and not isinstance(a[flag], bool):
                    _error(where, f"{flag} must be boolean")
        if kind == "effective_font":
            if not ({"value", "size"} & set(a)):
                _error(where, "effective_font needs value and/or size")
            if "value" in a and (not isinstance(a["value"], str) or not a["value"].strip()):
                _error(where, "value must be a non-empty font name")
            if "size" in a:
                size = a["size"]
                if (isinstance(size, bool) or not isinstance(size, (int, float)) or not math.isfinite(size)
                        or not 0 < size <= 1638 or size * 2 != int(size * 2)):
                    _error(where, "size must be positive points, at most 1638, in half-point increments")
            if "slots" in a and "value" not in a:
                _error(where, "slots requires value")
            _choices(a.get("slots", ["ascii", "hAnsi", "cs"]), SLOTS, f"{where}.slots")
        elif kind == "page_setup":
            _keys(a, {"kind", "sections", "margins", "size", "orientation"}, where)
            sections = a.get("sections", "all")
            if sections != "all" and (not isinstance(sections, list) or not sections
                    or any(type(x) is not int or x < 0 for x in sections) or len(sections) != len(set(sections))):
                _error(where, "sections must be 'all' or distinct non-negative section indices")
            if not ({"margins", "size", "orientation"} & set(a)):
                _error(where, "page_setup needs margins, size and/or orientation")
            for key, allowed in (("margins", MARGINS), ("size", ("w", "h"))):
                if key not in a:
                    continue
                _keys(a[key], allowed, f"{where}.{key}")
                if not a[key] or (key == "size" and set(a[key]) != {"w", "h"}):
                    _error(where, f"{key} cannot be empty; size requires both w and h")
                for attr, value in a[key].items():
                    if (type(value) is not int or (key == "size" and value <= 0)
                            or (key == "margins" and attr not in ("top", "bottom") and value < 0)):
                        _error(where, f"{key}.{attr} must be integer twips with a valid sign")
            if "orientation" in a and a["orientation"] not in ("portrait", "landscape"):
                _error(where, "orientation must be portrait or landscape")
        else:
            if not ({"run", "paragraph"} & set(a)):
                _error(where, "effective_properties needs run and/or paragraph properties")
            for key, allowed in (("run", PROP_PAIRS), ("paragraph", PARA_FLAGS)):
                if key not in a:
                    continue
                _keys(a[key], allowed, f"{where}.{key}")
                if not a[key] or any(not isinstance(v, bool) for v in a[key].values()):
                    _error(where, f"{key} must contain one or more boolean properties")
            if a.get("run", {}).get("caps") is True and a.get("run", {}).get("smallCaps") is True:
                _error(where, "caps and smallCaps cannot both be true")


def _conditional(styles: Styles, p, run_elements, para_elements) -> bool:
    """Whether an applicable table-style chain contains unmodelled conditional values we would otherwise miss."""
    for st in styles.chain(_v_table_style_id(p, styles)):
        for conditional in st.findall(W + "tblStylePr"):
            if any(conditional.find(W + "rPr/" + W + name) is not None for name in run_elements):
                return True
            if any(conditional.find(W + "pPr/" + W + name) is not None for name in para_elements):
                return True
    return False


def _formatting(pkg: VPackage, a: Dict[str, Any], errors: List[str]) -> int:
    style_part, theme_part = pkg.related(STYLES_REL), pkg.related(THEME_REL)
    styles = Styles(pkg.xml(style_part) if style_part else None)
    theme = Theme(pkg.xml(theme_part) if theme_part else None)
    font = a["kind"] == "effective_font"
    run_asked = _v_run_elements(a.get("run")) if not font else {}
    para_asked = a.get("paragraph", {})
    run_elements = (["rFonts"] if "value" in a else []) + (["sz"] if "size" in a else []) if font else run_asked
    checked = 0
    for scope in a.get("scope", ["document"]):
        parts = pkg.part_of_kind(scope)
        if not parts:
            if a.get("allow_absent_scopes", False):
                continue
            errors.append(f"scope {scope!r} resolves to no final parts")
        scope_checked = 0
        for part in parts:
            view = PartView(pkg.xml(part))
            for bi, p in enumerate(view.pars):
                where = f"{part} paragraph {bi}"
                if _conditional(styles, p, run_elements, para_asked):
                    errors.append(f"{where}: conditional table-style formatting cannot be resolved independently")
                    continue
                if para_asked:
                    scope_checked += 1
                    for key, want in para_asked.items():
                        got, provenance = _v_para_prop(p, styles, key)
                        if got != want:
                            errors.append(f"{where}: {key} is {got!r} ({provenance}), expected {want!r}")
                if not font and not run_asked:
                    continue
                candidates = [(r.find(W + "rPr"), f"run {ri}") for ri, r in enumerate(view.runs[bi])
                              if any(c.tag in (W + "t", W + "delText") for c in r)]
                if a.get("include_paragraph_marks", True):
                    candidates.append((p.find(W + "pPr/" + W + "rPr"), "paragraph mark"))
                for rpr, label in candidates:
                    scope_checked += 1
                    loc = f"{where} {label}"
                    if font:
                        if "value" in a:
                            fonts = effective_fonts(rpr, p, styles, theme)
                            for slot in a.get("slots", ["ascii", "hAnsi", "cs"]):
                                if fonts[slot] != a["value"]:
                                    errors.append(f"{loc}: {slot} is {fonts[slot]!r}, expected {a['value']!r}")
                        if "size" in a:
                            size = effective_size(rpr, p, styles)
                            if size != int(a["size"] * 2):
                                errors.append(f"{loc}: size is {size / 2:g} pt, expected {a['size']:g} pt")
                    else:
                        for key, want in run_asked.items():
                            got, provenance = _v_run_prop(rpr, p, styles, key)
                            if got != want:
                                errors.append(f"{loc}: {key} is {got!r} ({provenance}), expected {want!r}")
            # A scope with only empty parts must not provide a vacuous success.
        if not scope_checked:
            errors.append(f"scope {scope!r} has no independently checked targets")
        checked += scope_checked
    if not checked:
        errors.append("the final requirement has no independently checked targets")
    return checked


def _page_setup(pkg: VPackage, a: Dict[str, Any], errors: List[str]) -> int:
    sections = sect_prs(pkg.xml(pkg.main_part()))
    selected = list(range(len(sections))) if a.get("sections", "all") == "all" else a["sections"]
    if not selected:
        errors.append("page_setup selects no final sections")
    checked = 0
    for index in selected:
        if index >= len(sections):
            errors.append(f"section {index} is missing from the final document")
            continue
        checked += 1
        section = sections[index]
        for key, element in (("margins", "pgMar"), ("size", "pgSz")):
            el = section.find(W + element)
            for attr, wanted in a.get(key, {}).items():
                got = el.get(W + attr) if el is not None else None
                try:
                    matches = got is not None and int(got) == wanted
                except ValueError:
                    matches = False
                if not matches:
                    errors.append(f"section {index}: {element}.{attr} is {got!r}, expected {wanted} twips")
        if "orientation" in a:
            size = section.find(W + "pgSz")
            got = size.get(W + "orient", "portrait") if size is not None else None
            if got != a["orientation"]:
                errors.append(f"section {index}: orientation is {got!r}, expected {a['orientation']!r}")
            try:
                width, height = int(size.get(W + "w")), int(size.get(W + "h"))
                dimensions_match = width > 0 and height > 0 and ((width > height) == (a["orientation"] == "landscape"))
            except (TypeError, ValueError, AttributeError):
                dimensions_match = False
            if not dimensions_match:
                errors.append(f"section {index}: page dimensions do not establish {a['orientation']} orientation")
    return checked


def assert_outcomes(data: bytes, assertions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Check immutable requirements against these exact final bytes; return one serializable record per assertion.

    Invalid requirements raise ValueError. An unreadable package or unresolved formatting fails the assertion;
    a successful record is not a substitute for stage verification or visual review.
    """
    validate_assertions(assertions)
    records = []
    if not assertions:
        return records
    for index, assertion in enumerate(assertions, 1):
        errors: List[str] = []
        checked = 0
        try:
            pkg = VPackage(data)
            try:
                checked = (_page_setup(pkg, assertion, errors) if assertion["kind"] == "page_setup"
                           else _formatting(pkg, assertion, errors))
            finally:
                pkg.zip.close()
        except Exception as e:  # noqa: BLE001 — no resolution error may become a passing requirement
            errors.append(f"cannot independently evaluate final requirement: {type(e).__name__}: {e}")
        status = "FAIL" if errors else "PASS"
        summary = (f"{assertion['kind']}: {checked} final target(s) checked"
                   + (f"; {len(errors)} problem(s): {errors[0]}" if errors else ""))
        records.append({"index": index, "kind": assertion["kind"], "status": status, "summary": summary,
                        "checked": checked, "error_count": len(errors), "errors": errors[:50]})
    return records
