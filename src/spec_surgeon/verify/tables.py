"""Table files (PROJECT.json, CSV, XLSX) read again by the verifier — its own code, independent of ops/tables.py (rule 1;
CLAUDE.md "Table files"). Same reading: JSON at a dotted key; CSV and sheets with a header row; an .xlsx text cell as
stored, a number only when Excel shows it as stored (a whole number of at most 11 digits in the General or "0" format),
anything else unusable. A ValueError says why a value cannot be used."""
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

_X = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PR = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


class Unusable(str):
    """A cell's value that may not be placed (a date, a decimal, TRUE/FALSE, an error …)."""


def usable_text(value: Any) -> str:
    """The value as it may be placed: text, not blank, no character in Cc/Cf/Co/Cs/Cn/Zl/Zp (ValueError otherwise)."""
    import unicodedata
    if isinstance(value, Unusable) or not isinstance(value, str):
        raise ValueError(f"unusable value ({value})")
    if not value.strip():
        raise ValueError("empty value")
    if any(unicodedata.category(c) in {"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"} for c in value):
        raise ValueError(f"the value {value!r} holds a control or invisible character")
    return value


def table_md5(path: str) -> Tuple[bytes, str]:
    data = Path(path).read_bytes()
    return data, hashlib.md5(data).hexdigest()


def _xml(data: bytes):
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, huge_tree=True))
    info = root.getroottree().docinfo
    if info.internalDTD is not None or info.externalDTD is not None or info.doctype:
        raise ValueError("a workbook part carries an XML document type declaration (<!DOCTYPE …>): an entity it "
                         "declares would be left out of the text read")
    return root


def _string_item(el) -> str:
    runs = el.findall(_X + "r")
    if runs:
        return "".join(t.text or "" for r in runs for t in r.findall(_X + "t"))
    t = el.find(_X + "t")
    return (t.text or "") if t is not None else ""


def _ref(ref: str) -> Tuple[int, int]:
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", ref or "")
    if not m:
        return -1, -1
    col = 0
    for ch in m.group(1).upper():
        col = col * 26 + ord(ch) - ord("A") + 1
    return int(m.group(2)) - 1, col - 1


def _sheet_cells(data: bytes, sheet: Optional[str]) -> Dict[Tuple[int, int], Any]:
    z = zipfile.ZipFile(io.BytesIO(data))
    have = z.namelist()
    main = "xl/workbook.xml"
    if "_rels/.rels" in have:
        for r in _xml(z.read("_rels/.rels")):
            if str(r.get("Type", "")).endswith("/officeDocument"):
                main = r.get("Target").lstrip("/")
    folder = posixpath.dirname(main)
    rel_file = f"{folder}/_rels/{posixpath.basename(main)}.rels"
    by_id: Dict[str, str] = {}
    if rel_file in have:
        for r in _xml(z.read(rel_file)):
            tgt = r.get("Target", "")
            by_id[r.get("Id")] = tgt.lstrip("/") if tgt.startswith("/") else posixpath.normpath(f"{folder}/{tgt}")
    entries = [(s.get("name"), s.get(_R + "id")) for s in _xml(z.read(main)).iter(_X + "sheet")]
    chosen = entries[0] if (sheet is None and entries) else next((e for e in entries if e[0] == sheet), None)
    if chosen is None or by_id.get(chosen[1]) not in have:
        raise ValueError(f"no sheet {sheet!r} in the workbook")
    strings: List[str] = []
    fmt_of: List[int] = []
    for target in list(by_id.values()) + ["xl/sharedStrings.xml", "xl/styles.xml"]:
        if target not in have:
            continue
        if target.endswith("sharedStrings.xml") and not strings:
            strings = [_string_item(si) for si in _xml(z.read(target)) if si.tag == _X + "si"]
        if target.endswith("styles.xml") and not fmt_of:
            node = _xml(z.read(target)).find(_X + "cellXfs")
            fmt_of = [int(xf.get("numFmtId") or 0) for xf in (node if node is not None else []) if xf.tag == _X + "xf"]
    cells: Dict[Tuple[int, int], Any] = {}
    for c in _xml(z.read(by_id[chosen[1]])).iter(_X + "c"):
        row, col = _ref(c.get("r", ""))
        if row < 0:
            continue
        t = c.get("t") or "n"
        v = c.findtext(_X + "v")
        if t == "s":
            try:
                cells[(row, col)] = strings[int(v)]
            except (TypeError, ValueError, IndexError):
                cells[(row, col)] = Unusable("a shared string that does not exist")
        elif t == "inlineStr":
            node = c.find(_X + "is")
            cells[(row, col)] = "" if node is None else _string_item(node)
        elif t == "str":
            cells[(row, col)] = v or ""
        elif t in ("b", "e"):
            cells[(row, col)] = Unusable("TRUE/FALSE" if t == "b" else f"an error value ({v})")
        elif v is None:
            cells[(row, col)] = ""
        else:
            style = int(c.get("s") or 0)
            fmt = fmt_of[style] if style < len(fmt_of) else 0
            ok = fmt in (0, 1) and v.lstrip("-").isdigit() and len(v.lstrip("-")) <= 11 and v.count("-") <= int(v.startswith("-"))
            cells[(row, col)] = v if ok else Unusable(f"a number shown in Excel's own format ({v})")
    return cells


def rows_of(path: str, sheet: Optional[str]) -> List[List[Any]]:
    data = Path(path).read_bytes()
    if path.lower().endswith(".csv"):
        return [list(r) for r in csv.reader(io.StringIO(data.decode("utf-8-sig")))]
    cells = _sheet_cells(data, sheet)
    if not cells:
        return []
    height, width = max(r for r, _ in cells) + 1, max(c for _, c in cells) + 1
    return [[cells.get((r, c), "") for c in range(width)] for r in range(height)]


def json_at(path: str, key: Optional[str]):
    obj = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    for part in (key or "").split("."):
        if part:
            if not isinstance(obj, dict) or part not in obj:
                raise ValueError(f"no {key!r} in {Path(path).name}")
            obj = obj[part]
    return obj


def _col(header: List[Any], name: Optional[str], default: int) -> int:
    texts = [(h if isinstance(h, str) and not isinstance(h, Unusable) else "").strip() for h in header]
    if name is None:
        if default >= len(texts):
            raise ValueError(f"no column {default + 1}")
        return default
    if name in texts:
        return texts.index(name)
    loose = [i for i, t in enumerate(texts) if t.casefold() == name.strip().casefold()]
    if len(loose) != 1:
        raise ValueError(f"no column headed {name!r}")
    return loose[0]


def table_variables(spec: Dict[str, Any]) -> Dict[str, Any]:
    """{name: value} — a value is a str, or an Unusable naming why it cannot be placed."""
    path = spec["file"]
    if path.lower().endswith(".json"):
        obj = json_at(path, spec.get("key"))
        if not isinstance(obj, dict):
            raise ValueError("the JSON at the key is not an object")
        return {str(k): (v if isinstance(v, str) else Unusable(f"not text ({type(v).__name__})")) for k, v in obj.items()}
    rows = rows_of(path, spec.get("sheet"))
    if not rows:
        raise ValueError("the table is empty")
    kn, kv = _col(rows[0], spec.get("name_column"), 0), _col(rows[0], spec.get("value_column"), 1)
    out: Dict[str, Any] = {}
    for row in rows[1:]:
        name = row[kn] if kn < len(row) else ""
        if isinstance(name, Unusable) or not name.strip():
            continue
        key = " ".join(name.split())
        if key in out:
            raise ValueError(f"{key!r} listed twice")
        out[key] = row[kv] if kv < len(row) else ""
    return out


def table_pairs(spec: Dict[str, Any]) -> List[Tuple[str, str]]:
    path = spec["file"]
    found: List[Tuple[Any, Any]] = []
    if path.lower().endswith(".json"):
        obj = json_at(path, spec.get("key"))
        for item in (obj.items() if isinstance(obj, dict) else obj):
            if isinstance(item, dict):
                if set(item) != {"find", "replace"}:
                    raise ValueError(f"a pair is not {{find, replace}}: {item!r}")
                found.append((item["find"], item["replace"]))
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                found.append((item[0], item[1]))
            else:
                raise ValueError(f"not a pair: {item!r}")
    else:
        rows = rows_of(path, spec.get("sheet"))
        if not rows:
            raise ValueError("the table is empty")
        kf, kr = _col(rows[0], spec.get("find_column"), 0), _col(rows[0], spec.get("replace_column"), 1)
        for row in rows[1:]:
            f = row[kf] if kf < len(row) else ""
            if not isinstance(f, Unusable) and not f.strip():
                continue
            found.append((f, row[kr] if kr < len(row) else ""))
    out = []
    for f, r in found:
        if isinstance(f, Unusable) or isinstance(r, Unusable) or not isinstance(f, str) or not isinstance(r, str):
            raise ValueError(f"an unusable pair ({f!r} → {r!r})")
        if not f or not r.strip():
            raise ValueError(f"an empty find or replacement ({f!r} → {r!r})")
        out.append((f, r))
    return out


def table_grid(spec: Dict[str, Any]) -> List[List[str]]:
    """A table file placed whole (built-in batch 6, insert_table): the rows of a CSV or a sheet, or the JSON list of rows
    at the key; rows at the end and columns on the right empty in every cell left out, short rows padded with empty
    cells. ValueError for an unusable cell, a JSON value that is not text, or nothing left."""
    path = spec["file"]
    if path.lower().endswith(".json"):
        obj = json_at(path, spec.get("key"))
        if not isinstance(obj, list) or any(not isinstance(row, list) for row in obj):
            raise ValueError("the JSON at the key is not a list of rows")
        rows = [[v if isinstance(v, str) else Unusable(f"not text ({type(v).__name__})") for v in row] for row in obj]
    else:
        rows = rows_of(path, spec.get("sheet"))
    while rows and not any(isinstance(c, Unusable) or c != "" for c in rows[-1]):
        rows = rows[:-1]
    width = max([len(row) for row in rows] or [0])
    while width > 0 and all(len(row) < width or (row[width - 1] == "" and not isinstance(row[width - 1], Unusable))
                            for row in rows):
        width -= 1
    if not rows or width == 0:
        raise ValueError("the table is empty")
    out = []
    for row in rows:
        cells = list(row[:width]) + [""] * (width - len(row))
        if any(isinstance(c, Unusable) or not isinstance(c, str) for c in cells):
            raise ValueError(f"an unusable cell in {cells!r}")
        out.append(cells)
    return out
