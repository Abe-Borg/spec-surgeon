"""Independent package reader for the verifier.

Deliberately duplicates the small amount of zip/rels logic in spec_surgeon.package: the verifier
reads only the before/after bytes with its own code, so a transform-side bug (say, a footer part
missed by rels resolution) cannot hide behind the same bug in the check.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from typing import Dict, List, Optional, Set, Tuple

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "{%s}" % W_NS
R = "{%s}" % R_NS
REL = "{%s}" % REL_NS
M = "{%s}" % M_NS
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
CT = "{%s}" % CT_NS

_REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
TEXT_PART_REL_TYPES = [_REL_BASE + k for k in ("header", "footer", "footnotes", "endnotes", "comments")]
KIND_REL_TYPES = {"headers": _REL_BASE + "header", "footers": _REL_BASE + "footer",
                  "footnotes": _REL_BASE + "footnotes", "endnotes": _REL_BASE + "endnotes",
                  "comments": _REL_BASE + "comments"}
STYLES_REL = _REL_BASE + "styles"
_WML_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.{}+xml"
REL_CONTENT_TYPES = {k: _WML_CT.format(k) for k in ("header", "footer", "styles", "settings", "numbering", "footnotes",
                                                    "endnotes", "comments", "fontTable", "webSettings")}
REL_CONTENT_TYPES["theme"] = "application/vnd.openxmlformats-officedocument.theme+xml"
REL_CONTENT_TYPES["image"] = "image/*"
# built-in batch 2: the comment side parts and the document-properties parts (keyed, like the rest, by the last segment
# of the relationship type)
REL_CONTENT_TYPES.update({k: _WML_CT.format(k) for k in ("commentsExtended", "commentsIds", "commentsExtensible",
                                                         "people")})
REL_CONTENT_TYPES["core-properties"] = "application/vnd.openxmlformats-package.core-properties+xml"
REL_CONTENT_TYPES["extended-properties"] = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
REL_CONTENT_TYPES["custom-properties"] = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
THEME_REL = _REL_BASE + "theme"
SETTINGS_REL = _REL_BASE + "settings"
# the members the method kept away from every op until batch 2 (settings and document properties): V10's member
# residual checks any change to them (verify/props._residual_members)
PACKAGE_PROPERTY_RELS = ("http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
                         _REL_BASE + "extended-properties", _REL_BASE + "custom-properties")


class VDoctype(ValueError):
    """A part the verifier reads carries an XML document type declaration (``<!DOCTYPE …>``): an entity it declares is
    left unresolved by this reader, so the text read would lack the entity's text — not a reading to judge a file by.
    The verifier's own refusal, with the code a batch records (``xml-dtd``)."""
    code = "xml-dtd"

    def __init__(self, part: str):
        self.part = part
        self.details = {"part": part}
        super().__init__(f"{part} carries an XML document type declaration (<!DOCTYPE …>), which Word never writes; "
                         f"the verifier cannot read its text exactly")


def _has_doctype(root: etree._Element) -> bool:
    info = root.getroottree().docinfo
    return info.internalDTD is not None or info.externalDTD is not None or bool(info.doctype)


class VPackage:
    def __init__(self, data: bytes):
        self.data = data
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        self.infos = self.zip.infolist()
        self.names = [i.filename for i in self.infos]
        self._memo: Dict[tuple, object] = {}     # derived, immutable lookups only (never parsed trees)

    def hashes(self) -> Dict[str, str]:
        return {n: hashlib.md5(self.zip.read(n)).hexdigest() for n in self.names}

    def read(self, name: str) -> bytes:
        return self.zip.read(name)

    def metadata_changes(self, output: "VPackage", removed=()) -> List[str]:
        """Independently check safe ZIP metadata on every retained member occurrence.

        Compression records, CRC/sizes, descriptor flags and offsets are regenerated.
        ZIP64 extra records describe those offsets/sizes and are excluded explicitly.
        No document operation is allowed to discard comments or the other metadata.
        """
        def extra_metadata(extra):
            result = bytearray()
            offset = 0
            while offset + 4 <= len(extra):
                tag = int.from_bytes(extra[offset:offset + 2], "little")
                length = int.from_bytes(extra[offset + 2:offset + 4], "little")
                stop = offset + 4 + length
                if stop > len(extra):
                    raise ValueError("invalid ZIP extra record")
                if tag != 1:
                    result.extend(extra[offset:stop])
                offset = stop
            result.extend(extra[offset:])
            return bytes(result)

        changes = []
        if self.zip.comment != output.zip.comment:
            changes.append("archive comment")
        remaining = [item for item in self.infos if item.filename not in set(removed)]
        fields = ("date_time", "compress_type", "comment", "create_system", "create_version",
                  "extract_version", "internal_attr", "external_attr")
        # V1 separately checks exact order and membership; match retained occurrences in order.
        for before, after in zip(remaining, output.infos):
            if before.filename != after.filename:
                continue
            differences = [field for field in fields if getattr(before, field) != getattr(after, field)]
            if extra_metadata(before.extra) != extra_metadata(after.extra):
                differences.append("extra")
            if differences:
                changes.append(f"{before.filename}: {', '.join(differences)}")
        return changes

    def xml(self, name: str) -> etree._Element:
        parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
        root = etree.fromstring(self.read(name), parser)
        if _has_doctype(root):
            raise VDoctype(name)
        return root

    def _cached(self, key: tuple, fn):
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def main_part(self) -> str:
        return self._cached(("main",), self._main_part)

    def _main_part(self) -> str:
        root = self.xml("_rels/.rels")
        for rel in root.findall(REL + "Relationship"):
            if rel.get("Type", "").endswith("/officeDocument"):
                return rel.get("Target").lstrip("/")
        return "word/document.xml"

    def rels_targets(self, part: str) -> Dict[str, Dict[str, str]]:
        return self._cached(("rels", part), lambda: self._rels_targets(part))

    def _rels_targets(self, part: str) -> Dict[str, Dict[str, str]]:
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        if rp not in self.names:
            return {}
        root = self.xml(rp)
        out = {}
        for rel in root.findall(REL + "Relationship"):
            if rel.get("TargetMode", "Internal") != "Internal":
                continue
            t = rel.get("Target")
            resolved = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(d, t))
            out[rel.get("Id")] = {"type": rel.get("Type"), "target": resolved}
        return out

    def text_parts(self) -> List[str]:
        main = self.main_part()
        parts = [main]
        rels = self.rels_targets(main)
        for t in TEXT_PART_REL_TYPES:
            for r in rels.values():
                if r["type"] == t and r["target"] in self.names and r["target"] not in parts:
                    parts.append(r["target"])
        return parts

    def document_xml_parts(self) -> List[str]:
        """The members holding the document's own XML, read independently of the transform: every text part (by its
        relationship, whatever its name), and in the main part's folder each member whose declared content type — the
        Override for its name (case-insensitive, as OPC compares part names), else the Default for its extension — is
        an XML type and not a picture's (``image/svg+xml``), or which, with no type declared, is named ``*.xml`` in
        any case. Relationship parts are left out."""
        defaults, overrides = self.content_types()
        override_of = {name.lower(): ctype for name, ctype in overrides.items()}
        home = posixpath.dirname(self.main_part())
        text = self.text_parts()
        found = []
        for name in self.names:
            if name in text:
                found.append(name)
                continue
            folder = posixpath.dirname(name)
            if (home and folder != home and not folder.startswith(home + "/")) or posixpath.basename(folder) == "_rels":
                continue
            ctype = override_of.get(name.lower())
            if ctype is None:
                ctype = defaults.get(posixpath.splitext(name)[1][1:].lower())
            if ctype is None and name.lower().endswith(".xml"):
                found.append(name)
            elif ctype is not None and ctype.lower().endswith("xml") and not ctype.lower().startswith("image/"):
                found.append(name)
        return found

    # ---- added for the NL editor's checks (V1 integrity, V10) — still independent of package.py ----

    def part_of_kind(self, kind: str) -> List[str]:
        """Parts for a scope kind (document | headers | footers | footnotes | endnotes | comments)."""
        main = self.main_part()
        if kind == "document":
            return [main]
        t = KIND_REL_TYPES[kind]
        out = []
        for r in self.rels_targets(main).values():
            if r["type"] == t and r["target"] in self.names and r["target"] not in out:
                out.append(r["target"])
        return out

    def related(self, rel_type: str) -> Optional[str]:
        for r in self.rels_targets(self.main_part()).values():
            if r["type"] == rel_type and r["target"] in self.names:
                return r["target"]
        return None

    def package_related(self, rel_type: str) -> Optional[str]:
        """The member the package relationships (``_rels/.rels``) give for ``rel_type``, or None."""
        if "_rels/.rels" not in self.names:
            return None
        for rel in self.xml("_rels/.rels").findall(REL + "Relationship"):
            if rel.get("Type") == rel_type and rel.get("TargetMode", "Internal") == "Internal":
                t = (rel.get("Target") or "").lstrip("/")
                if t in self.names:
                    return t
        return None

    def all_relationships(self) -> List[Tuple[str, str, str, Optional[str]]]:
        """(rels member, owner part, type, resolved internal target or None) for every relationship in a rels
        member whose owner part exists (the package rels always counts)."""
        return self._cached(("allrels",), self._all_relationships)

    def _all_relationships(self) -> List[Tuple[str, str, str, Optional[str]]]:
        names = set(self.names)
        out = []
        for rp in self.names:
            d, b = posixpath.split(rp)
            if not b.endswith(".rels") or posixpath.basename(d) != "_rels":
                continue
            owner = "" if b == ".rels" else posixpath.join(posixpath.dirname(d), b[:-5])
            if owner and owner not in names:
                continue
            base = posixpath.dirname(owner)
            for rel in self.xml(rp).findall(REL + "Relationship"):
                target = None
                if rel.get("TargetMode", "Internal") == "Internal":
                    t = rel.get("Target") or ""
                    target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
                out.append((rp, owner, rel.get("Type"), target))
        return out

    def content_types(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        if "[Content_Types].xml" not in self.names:
            return {}, {}
        root = self.xml("[Content_Types].xml")
        defaults = {d.get("Extension", "").lower(): d.get("ContentType") for d in root.findall(CT + "Default")}
        overrides = {o.get("PartName", "").lstrip("/"): o.get("ContentType") for o in root.findall(CT + "Override")}
        return defaults, overrides

    def integrity_problems(self) -> Set[str]:
        """Package-level repair triggers: dangling internal relationships, overrides naming missing members,
        members with no content type."""
        problems = set()
        names = set(self.names)
        for rp, owner, typ, target in self.all_relationships():
            if target is not None and target not in names:
                problems.add(f"dangling relationship in {rp} → {target} ({typ.rsplit('/', 1)[-1]})")
        defaults, overrides = self.content_types()
        for name in overrides:
            if name not in names:
                problems.add(f"content-type override for missing member /{name}")
        for name in self.names:
            if name == "[Content_Types].xml" or name.endswith("/"):
                continue
            ext = posixpath.splitext(name)[1].lstrip(".").lower()
            if name not in overrides and ext not in defaults:
                problems.add(f"no content type for member {name}")
        # the content type must be the one the relationship pointing at the part requires
        for rp, owner, typ, target in self.all_relationships():
            want = REL_CONTENT_TYPES.get((typ or "").rsplit("/", 1)[-1])
            if target is None or target not in names or want is None:
                continue
            got = overrides.get(target) or defaults.get(posixpath.splitext(target)[1].lstrip(".").lower())
            ok = (got or "").startswith("image/") if want == "image/*" else got == want
            if not ok:
                problems.add(f"content type of {target} is {got!r} but its relationship ({typ.rsplit('/', 1)[-1]}) "
                             f"needs {want!r}")
        return problems

    def referenced_members(self) -> Set[str]:
        return {t for _, _, _, t in self.all_relationships() if t is not None}

    def rid_problems(self, part: str) -> Set[str]:
        """r:* attributes in ``part`` (r:id, r:embed, r:link, …) naming no relationship of that part — the XML
        side of a dangling reference (a headerReference whose relationship is gone is a Word repair prompt)."""
        if part not in self.names or not part.endswith(".xml"):
            return set()
        try:
            root = self.xml(part)
        except etree.XMLSyntaxError:
            return {f"{part}: not well-formed"}
        d, b = posixpath.split(part)
        rp = posixpath.join(d, "_rels", b + ".rels")
        ids = set()
        if rp in self.names:
            ids = {rel.get("Id") for rel in self.xml(rp).findall(REL + "Relationship")}
        out = set()
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            for k, v in el.attrib.items():
                if k.startswith(R) and v not in ids:
                    out.add(f"{part}: {etree.QName(el).localname}/@r:{k[len(R):]}={v} names no relationship")
        return out


def protected_members(pkg: "VPackage") -> Set[str]:
    """``settings.xml`` (through the main part's relationships) and the document-properties parts (through the package
    relationships) of this package — whatever their names."""
    out = set()
    s = pkg.related(SETTINGS_REL)
    if s:
        out.add(s)
    for t in PACKAGE_PROPERTY_RELS:
        m = pkg.package_related(t)
        if m:
            out.add(m)
    return out
