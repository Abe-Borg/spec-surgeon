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
import threading
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

# Relationship targets may have arbitrary names or extensions. Choose among
# the schemas already shipped by their root namespace before filename fallback.
ROOT_SCHEMAS = {
    "http://schemas.openxmlformats.org/wordprocessingml/2006/main": SCHEMA_FOR["word"],
    "http://schemas.openxmlformats.org/package/2006/content-types": SCHEMA_FOR["[Content_Types].xml"],
    "http://schemas.openxmlformats.org/package/2006/relationships": SCHEMA_FOR[".rels"],
    "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties": SCHEMA_FOR["app.xml"],
    "http://schemas.openxmlformats.org/package/2006/metadata/core-properties": SCHEMA_FOR["core.xml"],
    "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties": SCHEMA_FOR["custom.xml"],
    "http://schemas.microsoft.com/office/word/2012/wordml": SCHEMA_FOR["commentsExtended.xml"],
    "http://schemas.microsoft.com/office/word/2016/wordml/cid": SCHEMA_FOR["commentsIds.xml"],
    "http://schemas.microsoft.com/office/word/2018/wordml/cex": SCHEMA_FOR["commentsExtensible.xml"],
}


# Imports a vendored schema makes from the web, answered from the package instead (validation never uses the network):
# the OPC core-properties schema imports Dublin Core's element set and terms (schemas/dublincore/ — local definitions
# to the shape of DCMI's files).
LOCAL_IMPORTS = {
    "http://dublincore.org/schemas/xmls/qdc/2003/04/02/dc.xsd": "dublincore/dc.xsd",
    "http://dublincore.org/schemas/xmls/qdc/2003/04/02/dcterms.xsd": "dublincore/dcterms.xsd",
}
XS = "{http://www.w3.org/2001/XMLSchema}"


_LOCAL = threading.local()


def _schema(rel: str) -> etree.XMLSchema:
    # XMLSchema.error_log belongs to the validator object, not its latest caller. Batch threads must never
    # share that mutable log: a successful validation could otherwise erase another file's schema failures.
    if not hasattr(_LOCAL, "schemas"):
        _LOCAL.schemas = {}
    if rel in _LOCAL.schemas:
        return _LOCAL.schemas[rel]
    path = SCHEMA_DIR / rel
    with open(path, "rb") as fh:
        doc = etree.parse(fh, base_url=str(path))
    for imp in doc.getroot().iter(XS + "import"):
        local = LOCAL_IMPORTS.get(imp.get("schemaLocation") or "")
        if local:
            imp.set("schemaLocation", (SCHEMA_DIR / local).as_uri())
    schema = etree.XMLSchema(doc)
    if len(_LOCAL.schemas) >= 16:
        _LOCAL.schemas.clear()
    _LOCAL.schemas[rel] = schema
    return schema


def schema_for(part: str, data: Optional[bytes] = None) -> Optional[str]:
    if data is not None:
        try:
            root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, huge_tree=True))
            found = ROOT_SCHEMAS.get(etree.QName(root).namespace)
            if found:
                return found
        except etree.XMLSyntaxError:
            pass  # The chosen schema's validator reports malformed XML below.
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


def _results() -> Dict[Tuple[str, bool, str], Set[str]]:
    if not hasattr(_LOCAL, "results"):
        _LOCAL.results = {}
    return _LOCAL.results


def validate_part(part: str, data: bytes, schema: Optional[str] = None) -> Optional[Set[str]]:
    """Set of error messages (line numbers stripped so before/after sets compare), or None if no schema.
    Results are cached per worker by (schema, content hash): repeated template parts need one validation per worker."""
    rel = schema or schema_for(part, data)
    if rel is None:
        return None
    key = (rel, rel == SCHEMA_FOR["word"], hashlib.sha1(data).hexdigest())
    results = _results()
    if key not in results:
        if len(results) > 512:
            results.clear()
        results[key] = _validate(part, rel, data)
    return set(results[key])


def _validate(part: str, rel: str, data: bytes) -> Set[str]:
    schema = _schema(rel)
    try:
        root = etree.fromstring(data, etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True))
    except etree.XMLSyntaxError as e:
        return {f"not well-formed: {e}"}
    if rel == SCHEMA_FOR["word"]:
        root = _clean(root)
    schema.validate(root)
    errs = set()
    for e in schema.error_log:
        msg = e.message
        if "hyphenationZone" in msg:
            continue
        errs.add(msg)
    return errs


# libxml2 stops checking a sequence at the first child it does not expect, so an error in a LATER child of the SOURCE is
# never reported — python-docx's <w:zoom w:val="bestFit"/> without its required w:percent, behind a w:trackRevisions
# placed first — until an op removes the misplaced child and the output reports it as new (a correct "track changes
# off" failed V5a so). For these parts the baseline also validates a copy of the source whose root children stand in
# the schema's sequence order: it only reorders, so every error it shows is the source's own and none is excused that
# the source does not have. (root tag → (schema, complexType))
REORDER_BASELINE = {"{http://schemas.openxmlformats.org/wordprocessingml/2006/main}settings":
                    ("ISO-IEC29500-4_2016/wml.xsd", "CT_Settings")}


@functools.lru_cache(maxsize=4)
def sequence_order(rel: str, type_name: str) -> Tuple[str, ...]:
    """The elements of a complexType's sequence in the bundled schema, in order (Clark names)."""
    root = etree.parse(str(SCHEMA_DIR / rel)).getroot()
    tns = root.get("targetNamespace")
    ct = next((c for c in root.iter(XS + "complexType") if c.get("name") == type_name), None)
    seq = ct.find(XS + "sequence") if ct is not None else None
    out: List[str] = []
    for el in (seq.iter(XS + "element") if seq is not None else ()):
        if el.get("name"):
            out.append("{%s}%s" % (tns, el.get("name")))
        elif el.get("ref"):
            prefix, local = el.get("ref").split(":", 1)
            out.append("{%s}%s" % (el.nsmap[prefix], local))
    return tuple(out)


def in_schema_order(data: bytes) -> Optional[bytes]:
    """The part with its root's element children stable-sorted into the schema's sequence order (elements the sequence
    does not name last), for a root REORDER_BASELINE names; None for any other part, one that is not well-formed, or
    one already in order."""
    try:
        root = etree.fromstring(data, etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True))
    except etree.XMLSyntaxError:
        return None
    spec = REORDER_BASELINE.get(root.tag)
    if spec is None:
        return None
    order = sequence_order(*spec)
    rank = {tag: k for k, tag in enumerate(order)}
    kids = [c for c in root if isinstance(c.tag, str)]
    ranked = sorted(kids, key=lambda c: rank.get(c.tag, len(order)))
    if ranked == kids:
        return None
    for c in list(root):
        root.remove(c)
    root.extend(ranked)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def compare(part: str, before: Optional[bytes], after: bytes) -> Tuple[Optional[Set[str]], Optional[Set[str]], Optional[Set[str]]]:
    """(baseline_errors, output_errors, new_errors) — any of them None when the part has no schema. The baseline is the
    source's errors, plus — for a part REORDER_BASELINE names — those of the source with its children in schema order."""
    # Keep the source's schema when a renamed part's output changes namespace;
    # otherwise that corruption could silently become a no-schema skip.
    rel = schema_for(part, before) if before is not None else schema_for(part, after)
    out = validate_part(part, after, schema=rel)
    if out is None:
        return None, None, None
    base = validate_part(part, before, schema=rel) if before is not None else set()
    ordered = in_schema_order(before) if before is not None else None
    if ordered is not None:
        base = (base or set()) | (validate_part(part, ordered, schema=rel) or set())
    return base, out, out - (base or set())
