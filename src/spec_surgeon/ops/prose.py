"""Prose for the plan the user reads: where an operation applies, in words, with units in inches and points.

Every op's ``describe(select, params)`` returns one or two complete sentences built with these helpers. A description
is for a person who has never seen an EditSpec: plain words, no JSON, no identifiers with underscores, no twips,
half-points or eighths of a point. Exact user text, style names and patterns are quoted with curly quotes; placed
words are never paraphrased. ``tests/test_describe.py`` checks every op against these rules.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

TWIPS_PER_INCH = 1440


def quote(text: Any) -> str:
    """The user's exact text, in curly quotes, never altered."""
    return f"“{text}”"


def join(items: Iterable[Any], conjunction: str = "and") -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    parts = [str(x) for x in items]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" {conjunction} " + parts[-1]


def count(n: int, singular: str, plural: Optional[str] = None) -> str:
    """``1 paragraph``, ``3 paragraphs``."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _number(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def inches(twips: Any) -> str:
    """Twips as inches: ``1 inch``, ``0.5 inch``, ``1.25 inches``."""
    try:
        value = float(twips) / TWIPS_PER_INCH
    except (TypeError, ValueError):
        return str(twips)
    text = _number(value)
    return f"{text} inch" if text in ("1", "0.5", "0.25", "0.75") or abs(value) <= 1 else f"{text} inches"


def points(value: Any) -> str:
    """A size already in points: ``10 pt``, ``10.5 pt``."""
    try:
        return f"{_number(float(value))} pt"
    except (TypeError, ValueError):
        return str(value)


def points_from_half(half_points: Any) -> str:
    """Word's half-point sizes (w:sz) as points."""
    try:
        return points(float(half_points) / 2)
    except (TypeError, ValueError):
        return str(half_points)


def points_from_eighths(eighths: Any) -> str:
    """Border widths in eighths of a point as points."""
    try:
        return points(float(eighths) / 8)
    except (TypeError, ValueError):
        return str(eighths)


def style(style_id: Any) -> str:
    return f"the {quote(style_id)} style"


def styles(ids: Sequence[Any]) -> str:
    ids = list(ids)
    if len(ids) == 1:
        return style(ids[0])
    return "the " + join(quote(x) for x in ids) + " styles"


SCOPE_WORDS = {"document": "the document body", "headers": "the headers", "footers": "the footers",
               "footnotes": "the footnotes", "endnotes": "the endnotes", "comments": "the comments"}


def scope_words(scope: Any) -> str:
    """``in the headers and footers``; empty for the body alone or no scope."""
    if not scope:
        return ""
    kinds = [scope] if isinstance(scope, str) else list(scope)
    if kinds == ["document"]:
        return ""
    names = [SCOPE_WORDS.get(str(k), str(k)) for k in kinds]
    if len(names) == 2 and set(kinds) == {"headers", "footers"}:
        return "in the headers and footers"
    return "in " + join(names)


def _paragraph_words(pred: Any) -> str:
    if pred in (None, "all", {}):
        return "every paragraph"
    if not isinstance(pred, dict):
        return "the selected paragraphs"
    adjectives: List[str] = []
    clauses: List[str] = []
    if pred.get("is_empty") is True:
        adjectives.append("empty")
    if pred.get("has_bold") is True:
        adjectives.append("bold")
    elif pred.get("has_bold") is False:
        adjectives.append("non-bold")
    if pred.get("is_all_caps") is True:
        adjectives.append("all-capitals")
    noun = " ".join(adjectives + ["paragraphs"])
    if pred.get("style_in"):
        clauses.append("in " + styles(pred["style_in"]))
    if pred.get("in_table") is True:
        clauses.append("inside tables")
    elif pred.get("in_table") is False:
        clauses.append("outside tables")
    if pred.get("text_regex"):
        qualifier = []
        if pred.get("case_insensitive"):
            qualifier.append("ignoring case")
        if pred.get("flexible_whitespace"):
            qualifier.append("ignoring spacing")
        clauses.append("whose text matches the pattern " + quote(pred["text_regex"])
                       + (f" ({' and '.join(qualifier)})" if qualifier else ""))
    if pred.get("index_range"):
        a, b = pred["index_range"]
        clauses.append(f"numbered {a + 1} through {b}" if b - a > 1 else f"number {a + 1}")
    if pred.get("after_heading_regex"):
        clauses.append("after the first paragraph matching " + quote(pred["after_heading_regex"]))
    if isinstance(pred.get("between_paragraphs"), dict):
        bp = pred["between_paragraphs"]
        clauses.append(f"between {quote(bp.get('start_regex', ''))} and {quote(bp.get('end_regex', ''))}"
                       + (" inclusive" if bp.get("include_bounds") else ""))
    if not adjectives and not clauses:
        return "every paragraph"
    return noun + (" " + " ".join(clauses) if clauses else "")


def _run_words(pred: Any) -> str:
    if pred in (None, "all", {}):
        return ""
    if not isinstance(pred, dict):
        return "the selected text"
    clauses: List[str] = []
    if pred.get("has_direct_font") is True:
        clauses.append("with a font set directly on it")
    elif pred.get("has_direct_font") is False:
        clauses.append("without a font set directly on it")
    if pred.get("font_in"):
        clauses.append("in the " + join(quote(f) for f in pred["font_in"]) + (" fonts" if len(pred["font_in"]) > 1 else " font"))
    if pred.get("color_in"):
        clauses.append("coloured " + join(quote(c) for c in pred["color_in"]))
    if pred.get("not_in_revisions") is True:
        clauses.append("outside tracked changes")
    return ("text " + " ".join(clauses)) if clauses else ""


def _section_words(sections: Any) -> str:
    if sections in (None, "all"):
        return "every section"
    if isinstance(sections, list):
        return "section " + join(str(int(i) + 1) for i in sections) if len(sections) == 1 else \
            "sections " + join(str(int(i) + 1) for i in sections)
    if isinstance(sections, dict):
        if sections.get("containing"):
            inner = sections["containing"]
            pattern = inner.get("text_regex") if isinstance(inner, dict) else inner
            return "the sections containing " + quote(pattern)
        if sections.get("with_tables"):
            return "the sections that contain tables"
        if sections.get("index_range"):
            a, b = sections["index_range"]
            return f"sections {a + 1} through {b}"
    return "the selected sections"


def _table_words(tables: Any) -> str:
    if tables in (None, "all", {}):
        return "every table"
    if not isinstance(tables, dict):
        return "the selected tables"
    clauses: List[str] = []
    if tables.get("style_in"):
        clauses.append("in " + styles(tables["style_in"]))
    if tables.get("first_row_regex"):
        clauses.append("whose first row matches " + quote(tables["first_row_regex"]))
    if tables.get("text_regex"):
        clauses.append("whose text matches " + quote(tables["text_regex"]))
    if tables.get("index_range"):
        a, b = tables["index_range"]
        clauses.append(f"numbered {a + 1} through {b}" if b - a > 1 else f"number {a + 1}")
    if tables.get("nested") is True:
        clauses.append("including nested tables")
    elif tables.get("nested") is False:
        clauses.append("excluding nested tables")
    return "tables " + " ".join(clauses) if clauses else "every table"


def _style_words(sel: Any) -> str:
    if sel in (None, "all"):
        return "every style"
    if sel == "all-paragraph-and-character":
        return "every paragraph and character style"
    if isinstance(sel, dict):
        if sel.get("ids"):
            return styles(sel["ids"])
        if sel.get("types"):
            return "every " + join(str(t) for t in sel["types"]) + " style"
    return "the selected styles"


def where(select: Optional[Dict[str, Any]], *, default: str = "every paragraph") -> str:
    """The selection as a phrase: ``in every paragraph``, ``in bold all-capitals paragraphs in the headers``.

    Ops whose selection is not paragraphs pass ``default`` (``every section``, ``every table``) for the empty case.
    """
    select = select or {}
    if "sections" in select:
        subject = _section_words(select["sections"])
    elif "tables" in select:
        subject = _table_words(select["tables"])
    elif "styles" in select and "paragraphs" not in select:
        subject = _style_words(select["styles"])
    else:
        runs = _run_words(select.get("runs"))
        paragraphs = _paragraph_words(select.get("paragraphs"))
        if runs and paragraphs == "every paragraph":
            subject = runs
        elif runs:
            subject = f"{runs} in {paragraphs}"
        else:
            subject = paragraphs if (select.get("paragraphs") not in (None, "all", {}) or default == "every paragraph") else default
    scope = scope_words(select.get("scope"))
    return "in " + subject + (f" {scope}" if scope else "")


def sentence(*parts: str) -> str:
    """Join non-empty parts with spaces and end with one period."""
    text = " ".join(p.strip() for p in parts if p and p.strip())
    text = text.rstrip(".") + "."
    return text[0].upper() + text[1:] if text else text
