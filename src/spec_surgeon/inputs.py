"""Input files beside the documents (built-in batch 1): the tables (``.json``, ``.csv``, ``.xlsx``) and pictures
(``.png``, ``.jpg``, ``.jpeg``, ``.gif``, ``.bmp``) in the folders of the batch's files and of the attached template. The
conversation lists them in its first message (``listing``: path, kind, and an overview — a table's sheets and column
headers, a picture's format and size) and Claude reads one with ``read_input_file`` — only a listed file, by its exact
path, never any other path. A spec names them by that path (``fill_variables.table.file``,
``replace_table.pairs_table.file``, ``swap_image.image`` / ``match.image``, ``insert_image.image``,
``remove_images.match.image``, ``resize_images.match.image``); the ops pin them by md5 (rule 12).

What a table holds is document content like the profiles' text: it goes to Claude only to write the spec. Values a
table gives are the user's (they are not Claude's words — CLAUDE.md rule 13); typed values in a spec are traced as
always (``compiler.placed_words``).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .ops.base import JobException
from .ops.images import read_picture
from .ops.tables import Bad, csv_rows, xlsx_rows, xlsx_sheet_names

KINDS = {".json": "table", ".csv": "table", ".xlsx": "table", ".png": "picture", ".jpg": "picture",
         ".jpeg": "picture", ".gif": "picture", ".bmp": "picture"}
MAX_BYTES = 20 * 1024 * 1024          # a bigger file is listed as too big, never read
MAX_ROWS = 40                         # rows read_input_file shows of a sheet
CELL_CHARS = 120
JSON_CHARS = 12000


def _cell(v: Any) -> str:
    text = f"(unusable: {v.reason})" if isinstance(v, Bad) else str(v)
    return text if len(text) <= CELL_CHARS else text[:CELL_CHARS - 1] + "…"


def overview(path: Path) -> Dict[str, Any]:
    """What a listed input file is, in a few fields — never more than the headers of a table."""
    path = Path(path)
    kind = KINDS.get(path.suffix.lower(), "?")
    out: Dict[str, Any] = {"path": str(path), "kind": kind}
    try:
        size = path.stat().st_size
        if size > MAX_BYTES:
            out["problem"] = f"{size:,} bytes — too big to read here"
            return out
        data = path.read_bytes()
        suffix = path.suffix.lower()
        if kind == "picture":
            pic = read_picture(data)                # a BMP is a picture insert_image places (swap_image refuses it)
            out.update(format=pic.format, pixels=[pic.width, pic.height])
        elif suffix == ".json":
            obj = json.loads(data.decode("utf-8-sig"))
            out["json"] = (f"an object with keys {list(obj)[:40]}" if isinstance(obj, dict)
                           else f"a list of {len(obj)} item(s)" if isinstance(obj, list) else type(obj).__name__)
        elif suffix == ".csv":
            rows = csv_rows(data, path.name)
            out.update(headers=[_cell(c) for c in (rows[0] if rows else [])][:40], rows=max(len(rows) - 1, 0))
        else:
            sheets = xlsx_sheet_names(data, path.name)
            out["sheets"] = []
            for name in sheets[:10]:
                rows = xlsx_rows(data, name, path.name)
                out["sheets"].append({"name": name, "headers": [_cell(c) for c in (rows[0] if rows else [])][:40],
                                      "rows": max(len(rows) - 1, 0)})
    except (OSError, ValueError, JobException) as e:
        out["problem"] = f"cannot be read ({getattr(e, 'reason', e)})"
    return out


def read(path: str, allowed: Sequence[Path], sheet: Optional[str] = None, rows: int = MAX_ROWS) -> Dict[str, Any]:
    """read_input_file: one LISTED input file (``allowed``; the exact path the listing shows) in full enough to write
    a spec — a sheet's or a CSV's rows (at most ``rows``, cells cut), a JSON file's text (cut), a picture's format,
    size and md5. Any other path is refused."""
    want = Path(str(path))
    hit = next((a for a in allowed if str(a) == str(path) or a == want), None)
    if hit is None:
        raise KeyError(f"{path!r} is not one of the input files listed beside the documents")
    out = overview(hit)
    if out.get("problem"):
        return out
    data = hit.read_bytes()
    suffix = hit.suffix.lower()
    n = max(1, min(int(rows or MAX_ROWS), MAX_ROWS))
    if out["kind"] == "picture":
        out["md5"] = hashlib.md5(data).hexdigest()
        out["bytes"] = len(data)
    elif suffix == ".json":
        text = data.decode("utf-8-sig")
        out["text"] = text if len(text) <= JSON_CHARS else text[:JSON_CHARS] + f"… ({len(text):,} characters)"
    else:
        table = csv_rows(data, hit.name) if suffix == ".csv" else xlsx_rows(data, sheet, hit.name)
        out["sheet"] = sheet if suffix == ".xlsx" else None
        out["rows_shown"] = [[_cell(c) for c in r] for r in table[:n + 1]]
        out["rows_total"] = max(len(table) - 1, 0)
    return out
