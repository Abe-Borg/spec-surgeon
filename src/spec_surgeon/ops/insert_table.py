"""Op: insert_table — a new table before or after each anchor paragraph of the body (built-in batch 6, F5 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §6).

Parameters: the table's text from ``rows`` (``[[cell text, …], …]``, the user's words verbatim, every row the same
number of cells) or from ``table`` (a file the user attached: ``{file, sheet?, key?}`` — a CSV, an Excel sheet or a JSON
list of rows, read by ``tables.grid``, pinned by md5); ``position`` — ``after`` (default) or ``before`` each anchor;
``header_rows`` (default 0) — the first n rows repeat at the top of each page; ``style`` — a table style the document
has (id or name, ``style_import.resolve_style``); ``column_widths`` — inches, one per column; ``tracked`` (default
true), ``author``, ``allow_missing``, ``date_convention``. A cell's text is one plain paragraph (no tab, line break or
other control character).

Anchors: ``select.paragraphs`` (targeted, required), in the body only (``select.scope`` ``["document"]``).

**The new table** (``new_table``), carrying no attributes:

* ``w:tblPr``: ``w:tblStyle`` (the style, when given); ``w:tblW`` — ``w:w="5000" w:type="pct"`` (the full text width)
  without ``column_widths``, else ``w:type="dxa"`` and their sum; without a style, ``w:tblBorders`` with every side
  (top, left, bottom, right, insideH, insideV) ``w:val="single" w:sz="4" w:space="0" w:color="auto"``; ``w:tblLook`` with
  ``w:firstRow`` "1" when there are header rows (else "0"), ``w:lastRow``, ``w:firstColumn``, ``w:lastColumn`` and
  ``w:noHBand`` "0", ``w:noVBand`` "1", and ``w:val`` their legacy bits (``0420`` / ``0400``);
* ``w:tblGrid``: one ``w:gridCol`` per column — ``column_widths`` in twips (inches × 1440, rounded), or, from the text
  width W of the anchor's section, column k of n gets ⌊W·(k+1)/n⌋ − ⌊W·k/n⌋ twips. The text width is the section's
  ``w:pgSz/@w:w`` less its ``w:pgMar/@w:left`` and ``@w:right`` (the gutter is not counted); the anchor's section is the
  first ``w:p`` at or after the anchor (document order) whose ``w:pPr`` holds a ``w:sectPr``, else the body's
  ``w:sectPr``;
* per row a ``w:tr``: a ``w:trPr`` holding ``<w:tblHeader/>`` for the first ``header_rows`` rows and, tracked, a new
  ``w:ins`` (none when empty); per cell a ``w:tc`` with ``w:tcPr/w:tcW`` (its column's width, ``dxa``) and one paragraph
  (``table_edit.new_paragraph``: no properties of its own — the document's default paragraph style —, tracked its mark
  and run inserted). Revision ids: per row the row's, then its cells' (mark, run).

Refused, per anchor: in a text box (``textbox``); inside a table, or where the table would stand beside another table,
or after the body's last paragraph (``table-placement`` — Word joins two tables standing together, and a document ends
with a paragraph); ``after`` an anchor carrying a section break (``section-break``: the table would land in the next
section); without ``column_widths``, a section whose page width or side margins are missing or leave no text width
(``table-placement``). Per file: the style missing (``missing-style``) or not a table style (``style-mismatch``).

Verified by V10's ``inserted_tables`` kind (verify/tablestructure.py), which reads the table file again with its own
reader, builds the tables itself and compares the body whole.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id
from ..package import W, DocxPackage
from . import prose, register, tables
from .base import BlockDiff, JobException, Op, PlanFragment
from .delete_tables import TRACKING_PARAMS, tracking
from .insert_table_rows import cells_words, typed_rows
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .slots import nearest_p
from .style_import import resolve_style, style_type
from .table_edit import BLOCK_TAGS, cell_text, insertion_counts, new_paragraph, stamped

POSITIONS = ("after", "before")
SIDES = ("top", "left", "bottom", "right", "insideH", "insideV")
FULL_WIDTH = "5000"                     # w:tblW pct is in fiftieths of a percent
MAX_TWIPS = 31680
TABLE_FILE_SCHEMA = {"type": "object", "properties": {
    "file": {"type": "string", "description": "a .csv, .xlsx or .json file the user attached (its path)"},
    "sheet": {"type": "string", "description": "xlsx: the sheet's name (default: the first)"},
    "key": {"type": "string", "description": "json: the dotted key of the list of rows (default: the whole file)"}}}
PARAMS = {
    "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}},
             "description": "the table's rows typed in the request, each a list of its cells' texts — the user's words "
                            "verbatim, every row the same length ('' for an empty cell); or give 'table'"},
    "table": TABLE_FILE_SCHEMA,
    "position": {"type": "string", "enum": list(POSITIONS), "default": "after",
                 "description": "the table goes after / before each anchor paragraph"},
    "header_rows": {"type": "integer", "default": 0, "description": "the first n rows repeat at the top of each page"},
    "style": {"type": "string", "description": "a table style the document has (id or name); none: single-line "
                                               "borders around and between every cell"},
    "column_widths": {"type": "array", "items": {"type": "number"},
                      "description": "inches, one per column (default: the full text width in equal columns)"},
    **TRACKING_PARAMS,
}
REFLOW_REASON = "a new table reflows the content after it"


def look(header: bool):
    """The w:tblLook of a new table (module docstring)."""
    el = etree.Element(W + "tblLook")
    for name, value in (("val", "0420" if header else "0400"), ("firstRow", "1" if header else "0"), ("lastRow", "0"),
                        ("firstColumn", "0"), ("lastColumn", "0"), ("noHBand", "0"), ("noVBand", "1")):
        el.set(W + name, value)
    return el


def equal_columns(width: int, n: int) -> List[int]:
    return [width * (k + 1) // n - width * k // n for k in range(n)]


def anchor_section(root, p):
    """The w:sectPr of the anchor's section (module docstring)."""
    body = root.find(W + "body")
    seen = False
    for q in body.iter(W + "p"):
        seen = seen or q is p
        if seen and q.find(f"{W}pPr/{W}sectPr") is not None:
            return q.find(f"{W}pPr/{W}sectPr")
    return body.find(W + "sectPr")


def text_width(sect) -> Optional[int]:
    """The section's page width less its left and right margins, in twips; None when unreadable or not positive."""
    try:
        w = int(sect.find(W + "pgSz").get(W + "w"))
        mar = sect.find(W + "pgMar")
        width = w - int(mar.get(W + "left")) - int(mar.get(W + "right"))
    except (AttributeError, TypeError, ValueError):
        return None
    return width if width > 0 else None


def placement_problem(p, position: str) -> Optional[str]:
    """Why a new table cannot go ``position`` the anchor ``p`` (beside another table, or ending its container)."""
    blocks = [c for c in p.getparent() if isinstance(c.tag, str) and c.tag in BLOCK_TAGS]
    at = blocks.index(p)
    if position == "before":
        if at > 0 and blocks[at - 1].tag == W + "tbl":
            return "a table stands right before the paragraph, and Word would join the two tables"
        return None
    if at + 1 == len(blocks):
        return "it is the last paragraph of the document, which must end with a paragraph"
    if blocks[at + 1].tag == W + "tbl":
        return "a table follows the paragraph, and Word would join the two tables"
    return None


def new_table(texts: List[List[str]], widths: List[int], fixed: bool, style: Optional[str], header_rows: int,
              tracked: bool, stamp: Optional[RevisionStamp], next_id: List[int]):
    tbl = etree.Element(W + "tbl")
    pr = etree.SubElement(tbl, W + "tblPr")
    if style:
        etree.SubElement(pr, W + "tblStyle").set(W + "val", style)
    tw = etree.SubElement(pr, W + "tblW")
    tw.set(W + "w", str(sum(widths)) if fixed else FULL_WIDTH)
    tw.set(W + "type", "dxa" if fixed else "pct")
    if not style:
        borders = etree.SubElement(pr, W + "tblBorders")
        for side in SIDES:
            b = etree.SubElement(borders, W + side)
            for name, value in (("val", "single"), ("sz", "4"), ("space", "0"), ("color", "auto")):
                b.set(W + name, value)
    pr.append(look(header_rows > 0))
    grid = etree.SubElement(tbl, W + "tblGrid")
    for w in widths:
        etree.SubElement(grid, W + "gridCol").set(W + "w", str(w))
    for i, row in enumerate(texts):
        tr = etree.SubElement(tbl, W + "tr")
        if i < header_rows or tracked:
            trpr = etree.SubElement(tr, W + "trPr")
            if i < header_rows:
                etree.SubElement(trpr, W + "tblHeader")
            if tracked:
                trpr.append(stamped("ins", stamp, next_id))
        for text, w in zip(row, widths):
            tc = etree.SubElement(tr, W + "tc")
            tcw = etree.SubElement(etree.SubElement(tc, W + "tcPr"), W + "tcW")
            tcw.set(W + "w", str(w))
            tcw.set(W + "type", "dxa")
            tc.append(new_paragraph(None, None, text, tracked, stamp, next_id))
    return tbl


def file_rows(spec: Any) -> Tuple[List[List[str]], Dict[str, Any]]:
    """(the file's rows, the spec pinned by md5) — ``bad-params`` for a wrong spec, ``table-value`` for a bad cell."""
    if not isinstance(spec, dict) or set(spec) - {"file", "sheet", "key"}:
        raise JobException("table must be {file, sheet?, key?}", "bad-params")
    got, path, md5 = tables.grid(spec)
    rows = [[cell_text(t, f"{path} row {i + 1} column {k + 1}", "table-value") for k, t in enumerate(r)]
            for i, r in enumerate(got)]
    return rows, {**spec, "file": path, "md5": md5}


def clean_params(params: Dict[str, Any]):
    """The asked table: (rows, file spec or None, position, header rows, style reference, column widths in twips)."""
    has_rows, has_file = params.get("rows") is not None, params.get("table") is not None
    if has_rows == has_file:
        raise JobException("give the table's text as 'rows' (typed) or 'table' (an attached file), one of them",
                           "bad-params")
    if has_rows:
        rows, spec = typed_rows(params["rows"]), None
        if len({len(r) for r in rows}) != 1:
            raise JobException("every typed row must have the same number of cells", "bad-params")
    else:
        rows, spec = file_rows(params["table"])
    position = params.get("position", "after")
    if position not in POSITIONS:
        raise JobException(f"position must be 'after' or 'before', got {position!r}", "bad-params")
    n = params.get("header_rows", 0)
    if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= len(rows):
        raise JobException(f"header_rows must be a whole number from 0 to the table's {len(rows)} row(s), got {n!r}",
                           "bad-params")
    style = params.get("style")
    if style is not None and (not isinstance(style, str) or not style.strip()):
        raise JobException("style must be a table style's id or name", "bad-params")
    widths = params.get("column_widths")
    if widths is not None:
        if not isinstance(widths, list) or len(widths) != len(rows[0]) or not all(
                isinstance(w, (int, float)) and not isinstance(w, bool) and 0 < w * 1440 <= MAX_TWIPS for w in widths):
            raise JobException(f"column_widths must give {len(rows[0])} width(s) in inches (more than 0, at most 22), "
                               f"one per column", "bad-params")
        widths = [max(1, round(w * 1440)) for w in widths]
    return rows, spec, position, n, style, widths


def table_words(params: Dict[str, Any]) -> str:
    rows = params.get("rows")
    spec = params.get("table")
    if isinstance(spec, dict) and spec.get("file"):
        details = [f"sheet {prose.quote(spec['sheet'])}"] if spec.get("sheet") else []
        details += [f"entry {prose.quote(spec['key'])}"] if spec.get("key") else []
        return f"the table in the file {prose.quote(spec['file'])}" + (f" ({', '.join(details)})" if details else "")
    if isinstance(rows, list) and rows and all(isinstance(r, list) and r for r in rows):
        n, m = len(rows), len(rows[0])
        return (f"a table of {prose.count(n, 'row')} and {prose.count(m, 'column')}, the first row with "
                f"{cells_words(rows[0])[len('the '):]}")
    return "a table with the approved wording"


@register
class InsertTable(Op):
    name = "insert_table"
    mode = "content"
    description = ("Insert a new table — its cells the user's words typed in the request, or the rows of an attached "
                   "CSV, Excel or JSON table file, verbatim, one paragraph per cell — after or before each anchor "
                   "paragraph of the body; full text width in equal columns unless widths are given, a table style the "
                   "document has or single-line borders, optional repeated header rows. Tracked by default (rows and "
                   "text inserted), or untracked with tracked false.")
    params_schema = {"type": "object", "properties": PARAMS}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": ["document"]}, "default": ["document"]},
        "paragraphs": {"type": "object", "description": "Targeted selector identifying the anchor paragraph(s)."}}}
    verification_profile = {
        "V2": "one inserted block per new cell paragraph (before=None, after=its text) at its output position",
        "V4": "tracked: +1 ins per row, +1 ins_mark per cell paragraph, +1 ins per cell with text; untracked: 0",
        "V5b": "tracked: reject-all view equals the source; untracked: enumerated-list mode",
        "V8": "tracked: the accept-all view holds every new cell's text at its exact output position",
        "V10": "inserted_tables: the anchors, the table file (read again by the verifier's own reader), the widths, the "
               "style and every element of each new table re-derived from the source; the body compared whole",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        position = "before" if params.get("position") == "before" else "after"
        subject = prose.where(select)[3:]
        anchor = ("each anchor paragraph" + subject[len("every paragraph"):]) if subject.startswith("every paragraph") \
            else "each of the " + subject
        clauses: List[str] = []
        if isinstance(params.get("style"), str) and params["style"]:
            clauses.append(f"in the {prose.quote(params['style'])} table style")
        else:
            clauses.append("with a single-line border around and between every cell")
        widths = params.get("column_widths")
        if isinstance(widths, list) and widths and all(isinstance(w, (int, float)) for w in widths):
            clauses.append("its columns " + prose.join(prose.inches(round(w * 1440)) for w in widths) + " wide")
        else:
            clauses.append("as wide as the text with equal columns")
        n = params.get("header_rows")
        if isinstance(n, int) and not isinstance(n, bool) and n > 0:
            clauses.append(("its first row" if n == 1 else f"its first {n} rows") + " repeated at the top of each page")
        if params.get("tracked", True) is False:
            clauses.append("without tracked changes")
        else:
            clauses.append("shown as a tracked change" + (f" by {prose.quote(params['author'])}"
                                                          if params.get("author") else ""))
        first = f"Insert {table_words(params)} {position} {anchor}, " + ", ".join(clauses) + "."
        second = "Files with no such paragraph are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select, params = dict(select or {}), dict(params or {})
        unknown = set(raw_select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"insert_table: unknown selector key(s) {sorted(unknown)}", "bad-params")
        scope = raw_select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        if list(scope) != ["document"]:
            raise JobException("insert_table places tables in the body only (scope ['document'])", "bad-params")
        pred = raw_select.get("paragraphs")
        check_paragraph_selector(pred)
        if not is_targeted(raw_select):
            raise JobException("insert_table requires a targeted 'paragraphs' selector naming the anchor(s)",
                               "bad-params")
        own = {"rows", "table", "position", "header_rows", "style", "column_widths"}
        tracked, author, allow, convention = tracking({k: v for k, v in params.items() if k not in own},
                                                      set(TRACKING_PARAMS), self.name)
        texts, spec, position, header_rows, style_ref, fixed = clean_params(params)
        sheet = StyleSheet(pkg)
        style = None
        if style_ref is not None:
            style = resolve_style(sheet.styles, style_ref, "the document")
            if style is None:
                raise JobException(f"the document has no table style {style_ref!r}", "missing-style",
                                   {"style": style_ref})
            if style_type(sheet.styles[style]) != "table":
                raise JobException(f"{style_ref!r} is a {style_type(sheet.styles[style])} style, not a table style",
                                   "style-mismatch", {"style": style_ref})
        part = scope_parts(pkg, ["document"])[0]
        root = pkg.xml(part)
        frag = self.fragment({"scope": ["document"], "paragraphs": pred},
                             {**params, "position": position, "header_rows": header_rows, "tracked": tracked,
                              "author": author}, mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        frag.touches.append((part, "scope", "verify"))
        pars = list(root.iter(W + "p"))
        index = {p: i for i, p in enumerate(pars)}
        anchors: List[List[Any]] = []
        wide: List[str] = []
        offset = 0
        for bi, p in enumerate(pars):
            if not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                continue
            where = f"{part} paragraph {bi}"
            if nearest_p(p) is not None:
                raise JobException(f"{where} is inside a text box; a table cannot go there", "textbox", {"where": where})
            if next(p.iterancestors(W + "tc"), None) is not None:
                raise JobException(f"{where} is inside a table; a new table goes beside body paragraphs only",
                                   "table-placement", {"where": where})
            if position == "after" and p.find(f"{W}pPr/{W}sectPr") is not None:
                raise JobException(f"{where} carries a section break (w:sectPr); insert 'before' it or anchor on a "
                                   f"paragraph of the next section", "section-break", {"where": where})
            why = placement_problem(p, position)
            if why:
                raise JobException(f"{where}: a new table cannot go {position} it — {why}; anchor it on another "
                                   f"paragraph", "table-placement", {"where": where})
            width = text_width(anchor_section(root, p))
            if fixed is None and width is None:
                raise JobException(f"{where}: its section states no page width and side margins, so the table's full "
                                   f"width is not known — give column_widths", "table-placement", {"where": where})
            widths = fixed or equal_columns(width, len(texts[0]))
            if fixed is not None and width is not None and sum(fixed) > width:
                wide.append(f"{where} ({prose.inches(sum(fixed))} in a text width of {prose.inches(width)})")
            at = (index[p] if position == "before" else index[list(p.iter(W + "p"))[-1]] + 1) + offset
            for row in texts:
                for text in row:
                    frag.diffs.append(BlockDiff(part=part, block_index=at, before=None, after=text,
                                                accepted_view=text if tracked else None,
                                                note=f"new table {position} block {bi}"))
                    at += 1
            offset += sum(len(r) for r in texts)
            anchors.append([bi, widths])
        frag.property_assertions.append({
            "kind": "inserted_tables", "parts": [part], "select": {"paragraphs": pred},
            "params": {"rows": texts if spec is None else None, "table": spec, "position": position,
                       "header_rows": header_rows, "style": style_ref,
                       "column_widths": params.get("column_widths")},
            "tracked": tracked, "author": author, "expected_anchors": len(anchors),
            "anchors": {part: [bi for bi, _ in anchors]} if anchors else {}})
        if not anchors:
            if allow:
                frag.notes.append("insert_table: no anchor paragraph in this file; no change.")
                return frag
            raise JobException(f"no anchor paragraph matches {pred!r}", "not-found", {"paragraphs": pred})
        if style is not None and sheet.part:
            frag.touches.append((sheet.part, f"style:{style}", "verify"))    # a removal of the style beside conflicts
        rows_n, pars_n, filled = insertion_counts(texts)
        if tracked:
            frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + len(anchors) * (rows_n + filled)
            frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + len(anchors) * pars_n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = len(anchors) * (rows_n + pars_n + filled)
        else:
            frag.flags.append(f"UNTRACKED insertion of {len(anchors)} table(s) at the user's request.")
        if wide:
            frag.flags.append(f"the column widths asked are wider than the text: {wide}")
        frag.intended_parts.append(part)
        frag.touches.append((part, "*", "text"))
        frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        frag.transform_data = {"anchors": anchors, "rows": texts, "table": spec, "fixed": fixed is not None,
                               "style": style, "header_rows": header_rows, "position": position, "part": part,
                               "tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                               "first_rev_id": frag.rev_id_base, "date_convention": convention}
        frag.notes.append(f"insert_table: {len(anchors)} table(s) of {rows_n} row(s) × {len(texts[0])} column(s) "
                          f"{position} their anchors ({'tracked' if tracked else 'untracked'})")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("anchors"):
            return
        tables.check_unchanged(td.get("table"), self.name)
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        root = pkg.xml(td["part"])
        pars = list(root.iter(W + "p"))
        work = [(pars[bi], new_table(td["rows"], widths, td["fixed"], td["style"], td["header_rows"], td["tracked"],
                                     stamp, next_id)) for bi, widths in td["anchors"]]
        for p, tbl in work:
            (p.addprevious if td["position"] == "before" else p.addnext)(tbl)
        pkg.write_xml(td["part"], root)
