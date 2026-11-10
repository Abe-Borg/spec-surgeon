"""Obliterate's hands-on tools, the reading half (the owner's decision of 2026-11-10; docs/BACKLOG.md O4).

A document is a package of parts. With the hands-on tools on, the window shows them read-only — the list of parts,
one part as text, a search across them — and saves one out as it is (``DocumentTask.save_part``), for the user to
change in an editor of their own and put back with **Replace this part** (``ops/replace_part.py``). Nothing in the
window edits XML.

Everything here reads bytes it is given and returns plain data: no file is written, no model is called, and nothing
of what is read leaves the machine. A part is shown indented for reading — a copy made for the screen: the file's own
bytes are what Save writes and what a replacement is compared with.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from lxml import etree

from .package import DocxPackage, XmlDoctype, parse_xml

PAGE_LINES = 400                 # lines of a part one page of the browser shows
LINE_CHARS = 2000                # characters of one line shown; a longer line is cut, and says so
SEARCH_HITS = 200                # matches one search lists
QUERY_CHARS = 200                # characters of a search
MAX_SHOWN_BYTES = 32 * 1024 * 1024       # a larger part is listed and can be saved, and is not shown as text

_XML_NAMES = (".xml", ".rels", ".vml")


def _is_xml(name: str, content_type: Optional[str]) -> bool:
    return name.lower().endswith(_XML_NAMES) or "xml" in (content_type or "").lower()


def list_parts(data: bytes) -> List[Dict[str, Any]]:
    """Every member of the package, in its own order: ``name``, ``size`` (bytes), ``type`` (its content type, ''
    when the package declares none) and ``xml`` (whether the browser shows it as text)."""
    pkg = DocxPackage(data)
    try:
        out = []
        for name in pkg.member_names:
            kind = pkg.content_type_of(name) or ""
            out.append({"name": name, "size": len(pkg.read(name)), "type": kind, "xml": _is_xml(name, kind)})
        return out
    finally:
        pkg.close()


def part_bytes(data: bytes, part: str) -> bytes:
    """The member's own bytes; ``KeyError`` when the document has no such part."""
    pkg = DocxPackage(data)
    try:
        if not pkg.has(part):
            raise KeyError(part)
        return pkg.read(part)
    finally:
        pkg.close()


def _lines(raw: bytes, part: str) -> Dict[str, Any]:
    """``{"lines", "note"}`` of one XML member: indented for reading when it parses, as its own text otherwise."""
    if len(raw) > MAX_SHOWN_BYTES:
        return {"lines": [], "note": f"{len(raw):,} bytes: too large to show here. Save it out to read it."}
    note = "Shown indented for reading; Save writes the part's own bytes."
    try:
        root = parse_xml(raw, part)
        text = etree.tostring(root.getroottree(), pretty_print=True, encoding="unicode")
    except (etree.XMLSyntaxError, XmlDoctype, ValueError) as error:
        text = raw.decode("utf-8", errors="replace")
        note = f"Not well-formed XML ({' '.join(str(error).split())[:160]}): shown as its own text."
    lines = []
    for line in text.splitlines():
        lines.append(line if len(line) <= LINE_CHARS else
                     line[:LINE_CHARS] + f" … ({len(line) - LINE_CHARS:,} more characters on this line)")
    return {"lines": lines, "note": note}


class Reader:
    """One document's parts, read once and kept while the browser is on that file (``DocumentTask`` holds one per
    file it is asked about, keyed by the file's identity)."""

    def __init__(self, data: bytes):
        self.data = data
        self.parts = list_parts(data)
        self._shown: Dict[str, Dict[str, Any]] = {}

    def _part(self, part: str) -> Dict[str, Any]:
        entry = next((p for p in self.parts if p["name"] == part), None)
        if entry is None:
            raise KeyError(part)
        return entry

    def shown(self, part: str) -> Dict[str, Any]:
        entry = self._part(part)
        if part not in self._shown:
            if not entry["xml"]:
                self._shown[part] = {"lines": [], "note": f"Not text: {entry['type'] or 'no declared type'}, "
                                                          f"{entry['size']:,} bytes. Save it out to open it."}
            else:
                self._shown[part] = _lines(part_bytes(self.data, part), part)
        return self._shown[part]

    def page(self, part: str, page: int = 1) -> Dict[str, Any]:
        """One page of a part as the browser shows it: ``lines`` (``PAGE_LINES`` at most), ``first`` (the number of
        its first line, from 1), ``page``, ``pages``, ``total`` (lines), ``size``, ``type``, ``xml`` and ``note``."""
        entry = self._part(part)
        shown = self.shown(part)
        total = len(shown["lines"])
        pages = max(1, -(-total // PAGE_LINES))
        page = min(max(1, int(page)), pages)
        start = (page - 1) * PAGE_LINES
        return {"part": part, "size": entry["size"], "type": entry["type"], "xml": entry["xml"],
                "lines": shown["lines"][start:start + PAGE_LINES], "first": start + 1, "page": page, "pages": pages,
                "total": total, "note": shown["note"]}

    def search(self, query: str) -> Dict[str, Any]:
        """Every line of every part shown as text that holds ``query`` (letters compared without case): ``matches``
        — ``{"part", "line" (from 1), "page", "text"}``, ``SEARCH_HITS`` at most — and ``more`` when there are more."""
        needle = " ".join(str(query or "").split()).lower()
        if not needle:
            return {"query": "", "matches": [], "more": False}
        needle = needle[:QUERY_CHARS]
        matches: List[Dict[str, Any]] = []
        for entry in self.parts:
            if not entry["xml"]:
                continue
            for number, line in enumerate(self.shown(entry["name"])["lines"], 1):
                if needle in line.lower():
                    if len(matches) >= SEARCH_HITS:
                        return {"query": needle, "matches": matches, "more": True}
                    at = line.lower().index(needle)
                    start = max(0, at - 80)
                    text = ("…" if start else "") + line[start:at + len(needle) + 120].strip()
                    matches.append({"part": entry["name"], "line": number, "page": (number - 1) // PAGE_LINES + 1,
                                    "text": text})
        return {"query": needle, "matches": matches, "more": False}


def saved_name(document: str, part: str) -> str:
    """The file name a part is saved under: the document's name, then the part's with its folders flattened, so two
    documents' parts and two parts of one document never share a name."""
    stem = document[:-5] if document.lower().endswith(".docx") else document
    flat = part.strip("/").replace("/", "_").replace("\\", "_") or "part"
    return f"{stem} - {flat}"
