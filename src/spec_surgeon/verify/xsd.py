"""V5a — XSD validation against the ECMA-376 / ISO 29500 transitional schemas (vendored), judged
against the SOURCE's own baseline: real files ship with inert schema noise that isn't ours to fix.

Approach (independent implementation of the technique the docx skill's validator uses): drop
mc:Ignorable, strip attributes/elements in namespaces outside the core OOXML set (w14, w15, w16…),
validate, and compare error *sets* before vs after.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import posixpath
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from lxml import etree

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

MC_IGNORABLE = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Ignorable"

OOXML_NAMESPACES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "http://schemas.openxmlformats.org/schemaLibrary/2006/main",
    "http://schemas.openxmlformats.org/drawingml/2006/main",
    "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "http://schemas.openxmlformats.org/drawingml/2006/chartDrawing",
    "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "http://schemas.openxmlformats.org/officeDocument/2006/sharedTypes",
    "http://www.w3.org/XML/1998/namespace",
}

SCHEMA_FOR = {
    "[Content_Types].xml": "ecma/fouth-edition/opc-contentTypes.xsd",
    ".rels": "ecma/fouth-edition/opc-relationships.xsd",
    "word": "ISO-IEC29500-4_2016/wml.xsd",
    "app.xml": "ISO-IEC29500-4_2016/shared-documentPropertiesExtended.xsd",
    "core.xml": "ecma/fouth-edition/opc-coreProperties.xsd",
    "custom.xml": "ISO-IEC29500-4_2016/shared-documentPropertiesCustom.xsd",
    "commentsIds.xml": "microsoft/wml-cid-2016.xsd",
    "commentsExtensible.xml": "microsoft/wml-cex-2018.xsd",
    "commentsExtended.xml": "microsoft/wml-2012.xsd",
    "people.xml": "microsoft/wml-2012.xsd",
}


@functools.lru_cache(maxsize=16)
def _schema(rel: str) -> etree.XMLSchema:
    path = SCHEMA_DIR / rel
    with open(path, "rb") as fh:
        doc = etree.parse(fh, base_url=str(path))
    return etree.XMLSchema(doc)


def schema_for(part: str) -> Optional[str]:
    name = posixpath.basename(part)
    if name in SCHEMA_FOR:
        return SCHEMA_FOR[name]
    if name.endswith(".rels"):
        return SCHEMA_FOR[".rels"]
    if part.startswith("word/") and name.endswith(".xml") and "/theme/" not in part and "/charts/" not in part \
            and not name.startswith("glossary"):
        return SCHEMA_FOR["word"]
    return None


def _clean(root: etree._Element) -> etree._Element:
    r = root          # validate_part hands over a fresh parse that nothing else uses: no copy needed
    if MC_IGNORABLE in r.attrib:
        del r.attrib[MC_IGNORABLE]
    for el in r.iter():
        if not isinstance(el.tag, str):
            continue
        for a in list(el.attrib):
            if a.startswith("{"):
                ns = a[1:].split("}")[0]
                if ns not in OOXML_NAMESPACES:
                    del el.attrib[a]

    def strip(parent):
        for child in list(parent):
            if not isinstance(child.tag, str):
                continue
            if child.tag.startswith("{"):
                ns = child.tag[1:].split("}")[0]
                if ns not in OOXML_NAMESPACES:
                    parent.remove(child)
                    continue
            strip(child)
    strip(r)
    # re-parse so namespace declarations are materialized locally (lesson: subtree c14n/validation
    # fails on attributes whose namespace is declared only on the root)
    return etree.fromstring(etree.tostring(r))


_RESULTS: Dict[Tuple[str, bool, str], Set[str]] = {}


def validate_part(part: str, data: bytes) -> Optional[Set[str]]:
    """Set of error messages (line numbers stripped so before/after sets compare), or None if no schema.
    Results are cached by (schema, content hash): a batch from one template validates its shared parts once."""
    rel = schema_for(part)
    if rel is None:
        return None
    key = (rel, part.startswith("word/"), hashlib.sha1(data).hexdigest())
    if key not in _RESULTS:
        if len(_RESULTS) > 512:
            _RESULTS.clear()
        _RESULTS[key] = _validate(part, rel, data)
    return set(_RESULTS[key])


def _validate(part: str, rel: str, data: bytes) -> Set[str]:
    schema = _schema(rel)
    try:
        root = etree.fromstring(data, etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True))
    except etree.XMLSyntaxError as e:
        return {f"not well-formed: {e}"}
    if part.startswith("word/"):
        root = _clean(root)
    schema.validate(root)
    errs = set()
    for e in schema.error_log:
        msg = e.message
        if "hyphenationZone" in msg:
            continue
        errs.add(msg)
    return errs


def compare(part: str, before: Optional[bytes], after: bytes) -> Tuple[Optional[Set[str]], Optional[Set[str]], Optional[Set[str]]]:
    """(baseline_errors, output_errors, new_errors) — any of them None when the part has no schema."""
    out = validate_part(part, after)
    if out is None:
        return None, None, None
    base = validate_part(part, before) if before is not None else set()
    return base, out, out - (base or set())
