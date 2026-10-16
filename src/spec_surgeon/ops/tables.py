"""Tables the user keeps — ``PROJECT.json``, a CSV or an Excel sheet — read on the transform side (design note §0.2;
CLAUDE.md "Table files"). verify/tables.py reads them again with its own code (rule 1). No new dependency: an .xlsx is
a zip of XML, read here with zipfile and lxml.

* JSON: the object (or list) at the dotted ``key`` (default: the whole file).
* CSV: UTF-8 (a BOM is fine); the first row holds the column headers.
* XLSX: the named ``sheet`` (default: the first); the first row holds the headers. A text cell (shared, inline or a
  formula's text result) is taken as stored. A number is taken only when Excel shows it exactly as stored: a whole number
  of at most 11 digits in the General or "0" format (its digits); any other number (a decimal, a date, a percentage, a
  currency …), a TRUE/FALSE or an error value is refused when it is used ("format the column as Text").
* A table file is an input like a template (rule 12): the op pins its md5 in the plan and apply refuses a changed file.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import posixpath
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .base import JobException

SSML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
OFFDOC_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
GENERAL_FORMATS = {0, 1}
MAX_WHOLE_DIGITS = 11
SUFFIXES = (".json", ".csv", ".xlsx")


class Bad:
    """A cell that cannot be used as text; the reason is said when (and only when) the cell is used."""

    def __init__(self, reason: str):
        self.reason = reason

    def __repr__(self) -> str:
        return f"Bad({self.reason!r})"


def _parse(data: bytes):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, huge_tree=True))


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return -1
    n = 0
    for ch in letters.group(0).upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _row_index(ref: str) -> int:
    m = re.search(r"(\d+)$", ref or "")
    return int(m.group(1)) - 1 if m else -1


def _si_text(si) -> str:
    """A shared or inline string: its own w:t, or its rich-text runs' t (phonetic runs left out)."""
    direct = si.find(SSML + "t")
    if direct is not None and not si.findall(SSML + "r"):
        return direct.text or ""
    return "".join((t.text or "") for r in si.findall(SSML + "r") for t in r.findall(SSML + "t"))


def xlsx_rows(data: bytes, sheet: Optional[str], where: str) -> List[List[Any]]:
    """The sheet's rows as lists of cells (str, or Bad), top row first; missing cells are ''."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        names = set(z.namelist())
        book = "xl/workbook.xml"
        if "_rels/.rels" in names:
            for rel in _parse(z.read("_rels/.rels")).findall(RELS + "Relationship"):
                if rel.get("Type", "").endswith("/officeDocument"):
                    book = rel.get("Target", book).lstrip("/")
        wb = _parse(z.read(book))
        base = posixpath.dirname(book)
        rels_name = posixpath.join(base, "_rels", posixpath.basename(book) + ".rels")
        targets = {}
        if rels_name in names:
            for rel in _parse(z.read(rels_name)).findall(RELS + "Relationship"):
                t = rel.get("Target", "")
                targets[rel.get("Id")] = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
        sheets = [(s.get("name"), s.get(OFFDOC_R + "id")) for s in wb.iter(SSML + "sheet")]
        if not sheets:
            raise JobException(f"{where}: the workbook has no sheet", "table-value")
        if sheet is None:
            name, rid = sheets[0]
        else:
            hit = [s for s in sheets if s[0] == sheet]
            if not hit:
                raise JobException(f"{where}: no sheet named {sheet!r} (sheets: {[s[0] for s in sheets]})", "not-found")
            name, rid = hit[0]
        part = targets.get(rid)
        if part is None or part not in names:
            raise JobException(f"{where}: sheet {name!r} cannot be found in the workbook", "table-value")
        shared: List[str] = []
        for cand in [t for t in targets.values() if t.endswith("sharedStrings.xml")] + ["xl/sharedStrings.xml"]:
            if cand in names:
                shared = [_si_text(si) for si in _parse(z.read(cand)).findall(SSML + "si")]
                break
        formats: List[int] = []
        for cand in [t for t in targets.values() if t.endswith("styles.xml")] + ["xl/styles.xml"]:
            if cand in names:
                xfs = _parse(z.read(cand)).find(SSML + "cellXfs")
                formats = [int(x.get("numFmtId", "0") or 0) for x in (xfs if xfs is not None else [])
                           if isinstance(x.tag, str) and x.tag == SSML + "xf"]
                break
        root = _parse(z.read(part))
    except JobException:
        raise
    except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, ValueError) as e:
        raise JobException(f"{where}: not a readable Excel workbook ({type(e).__name__}: {e})", "table-value")
    grid: Dict[Tuple[int, int], Any] = {}
    for c in root.iter(SSML + "c"):
        ref = c.get("r") or ""
        r, k = _row_index(ref), _col_index(ref)
        if r < 0 or k < 0:
            continue
        kind = c.get("t", "n")
        v = c.find(SSML + "v")
        raw = v.text if v is not None and v.text is not None else None
        at = f"cell {ref}"
        if kind == "s":
            try:
                grid[(r, k)] = shared[int(raw)]
            except (TypeError, ValueError, IndexError):
                grid[(r, k)] = Bad(f"{at} names a shared string the workbook does not have")
        elif kind == "inlineStr":
            is_ = c.find(SSML + "is")
            grid[(r, k)] = _si_text(is_) if is_ is not None else ""
        elif kind == "str":
            grid[(r, k)] = raw or ""
        elif kind == "b":
            grid[(r, k)] = Bad(f"{at} holds TRUE/FALSE, not text")
        elif kind == "e":
            grid[(r, k)] = Bad(f"{at} holds an error value ({raw})")
        elif raw is None:
            grid[(r, k)] = ""
        else:
            s = int(c.get("s", "0") or 0)
            fmt = formats[s] if 0 <= s < len(formats) else 0
            if fmt in GENERAL_FORMATS and re.fullmatch(r"-?\d{1,%d}" % MAX_WHOLE_DIGITS, raw):
                grid[(r, k)] = raw
            else:
                grid[(r, k)] = Bad(f"{at} holds a number ({raw}) that Excel shows in its own format (a date, a decimal, "
                                   f"a percentage …) — format the column as Text in Excel and type the value as it "
                                   f"should read")
    if not grid:
        return []
    rows = 1 + max(r for r, _ in grid)
    cols = 1 + max(k for _, k in grid)
    return [[grid.get((r, k), "") for k in range(cols)] for r in range(rows)]


def csv_rows(data: bytes, where: str) -> List[List[Any]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise JobException(f"{where}: the CSV is not UTF-8 — save it as 'CSV UTF-8' in Excel", "table-value")
    return [list(row) for row in csv.reader(io.StringIO(text))]


def load(spec: Dict[str, Any], what: str) -> Tuple[Path, bytes, str]:
    """(path, bytes, md5) of a table file spec ``{"file": …}`` — refused when missing or of another kind."""
    if not isinstance(spec, dict) or not isinstance(spec.get("file"), str) or not spec["file"].strip():
        raise JobException(f"{what}: give the table as {{'file': '<path to .json, .csv or .xlsx>'}}", "bad-params")
    path = Path(spec["file"])
    if path.suffix.lower() not in SUFFIXES:
        raise JobException(f"{what}: {path.name} is not a .json, .csv or .xlsx file", "bad-params")
    try:
        data = path.read_bytes()
    except OSError as e:
        raise JobException(f"{what}: the table {path} cannot be read ({e})", "not-found", {"file": str(path)})
    return path, data, hashlib.md5(data).hexdigest()


def check_unchanged(spec: Optional[Dict[str, Any]], what: str) -> None:
    """Apply refuses a table file that changed since the plan read it (rule 12)."""
    if not spec:
        return
    try:
        md5 = hashlib.md5(Path(spec["file"]).read_bytes()).hexdigest()
    except OSError as e:
        raise JobException(f"{what}: the table {spec['file']} cannot be read any more ({e})", "not-found")
    if md5 != spec.get("md5"):
        raise JobException(f"{what}: the table {Path(spec['file']).name} changed since the plan was made; plan again",
                           "table-changed", {"file": spec["file"]})


def _json_at(data: bytes, key: Optional[str], where: str):
    try:
        obj = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        raise JobException(f"{where}: not readable JSON ({e})", "table-value")
    for k in [x for x in (key or "").split(".") if x]:
        if not isinstance(obj, dict) or k not in obj:
            raise JobException(f"{where}: no {key!r} in the file", "not-found", {"key": key})
        obj = obj[k]
    return obj


def _rows(path: Path, data: bytes, spec: Dict[str, Any], where: str) -> List[List[Any]]:
    if path.suffix.lower() == ".csv":
        return csv_rows(data, where)
    return xlsx_rows(data, spec.get("sheet"), where)


def _column(headers: List[Any], want: Optional[str], default: int, label: str, where: str) -> int:
    texts = [h.strip() if isinstance(h, str) else "" for h in headers]
    if want is None:
        if default >= len(texts):
            raise JobException(f"{where}: the table has no column {default + 1} for the {label}", "table-value")
        return default
    if want in texts:
        return texts.index(want)
    folded = [k for k, t in enumerate(texts) if t.casefold() == want.strip().casefold()]
    if len(folded) == 1:
        return folded[0]
    raise JobException(f"{where}: no column headed {want!r} for the {label} (headers: {[t for t in texts if t]})",
                       "not-found", {"column": want})


def _cell(row: List[Any], k: int) -> Any:
    return row[k] if k < len(row) else ""


def used(value: Any, where: str) -> str:
    """A cell's text when it is used — a Bad cell is refused here."""
    if isinstance(value, Bad):
        raise JobException(f"{where}: {value.reason}", "table-value")
    return value


UNSEEN_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")


def placeable(value: Any, where: str) -> str:
    """A value that may be placed in a line of text: text, not blank, no control or invisible character (a zero-width
    space pasted from a web page would otherwise travel into every document unseen)."""
    import unicodedata
    value = used(value, where)
    if not isinstance(value, str) or not value.strip():
        raise JobException(f"{where}: the value is empty", "table-value")
    bad = sorted({f"U+{ord(c):04X}" for c in value if unicodedata.category(c) in UNSEEN_CATEGORIES})
    if bad:
        raise JobException(f"{where}: the value {value!r} holds a control or invisible character ({', '.join(bad)})",
                           "table-value")
    return value


def variables(spec: Dict[str, Any], where: str = "table") -> Tuple[Dict[str, Any], str, str]:
    """({name: value (str or Bad)}, path, md5) — names down one column, values in another (one column per site)."""
    path, data, md5 = load(spec, where)
    where = f"{where} {path.name}"
    if path.suffix.lower() == ".json":
        obj = _json_at(data, spec.get("key"), where)
        if not isinstance(obj, dict):
            raise JobException(f"{where}: expected an object of names and values at {spec.get('key') or 'the top'}",
                               "table-value")
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            out[str(k)] = v if isinstance(v, str) else Bad(f"the value of {k!r} is not text ({type(v).__name__})")
        return out, str(path), md5
    rows = _rows(path, data, spec, where)
    if not rows:
        raise JobException(f"{where}: the table is empty", "table-value")
    kn = _column(rows[0], spec.get("name_column"), 0, "names", where)
    kv = _column(rows[0], spec.get("value_column"), 1, "values", where)
    out = {}
    for n, row in enumerate(rows[1:], 2):
        name = _cell(row, kn)
        if isinstance(name, Bad) or not name.strip():
            continue
        key = " ".join(name.split())
        if key in out:
            raise JobException(f"{where}: {key!r} is listed twice (row {n})", "table-value", {"name": key})
        out[key] = _cell(row, kv)
    return out, str(path), md5


def pairs(spec: Dict[str, Any], where: str = "pairs table") -> Tuple[List[Tuple[str, str]], str, str]:
    """([(find, replace)], path, md5) — a list of {find, replace} / [find, replace] or an object in JSON; two columns
    (``find_column``, ``replace_column``; default the first two) in a CSV or a sheet. An empty replacement in a table is
    refused (a deletion is typed, never left blank by mistake)."""
    path, data, md5 = load(spec, where)
    where = f"{where} {path.name}"
    out: List[Tuple[str, str]] = []
    if path.suffix.lower() == ".json":
        obj = _json_at(data, spec.get("key"), where)
        items = list(obj.items()) if isinstance(obj, dict) else obj
        if not isinstance(items, list):
            raise JobException(f"{where}: expected a list of pairs or an object of find → replace", "table-value")
        for n, it in enumerate(items, 1):
            if isinstance(it, dict) and set(it) == {"find", "replace"}:
                f, r = it["find"], it["replace"]
            elif isinstance(it, (list, tuple)) and len(it) == 2:
                f, r = it
            else:
                raise JobException(f"{where}: pair {n} is not {{find, replace}} or [find, replace]", "table-value")
            if not isinstance(f, str) or not isinstance(r, str):
                raise JobException(f"{where}: pair {n} must be text", "table-value")
            out.append((f, r))
    else:
        rows = _rows(path, data, spec, where)
        if not rows:
            raise JobException(f"{where}: the table is empty", "table-value")
        kf = _column(rows[0], spec.get("find_column"), 0, "text to find", where)
        kr = _column(rows[0], spec.get("replace_column"), 1, "replacements", where)
        for n, row in enumerate(rows[1:], 2):
            f = _cell(row, kf)
            if not isinstance(f, Bad) and not f.strip():
                continue
            f = used(f, f"{where} row {n}")
            out.append((f, used(_cell(row, kr), f"{where} row {n}")))
    for n, (f, r) in enumerate(out, 1):
        if not f:
            raise JobException(f"{where}: pair {n} has nothing to find", "table-value")
        if not r.strip():
            raise JobException(f"{where}: the replacement for {f!r} is empty — write it, or type the deletion in the "
                               f"request", "table-value", {"find": f})
    return out, str(path), md5
