"""DocxPackage — a .docx opened as an OPC zip, with rels-based part resolution and
repack-by-member-substitution.

Design points carried over from the docx-surgical-edit skill:
* Never assume part names (header2.xml etc.). Resolve via sectPr → r:id → document.xml.rels.
* Repack by copying the source zip's members in their original order and compression, substituting
  only the changed parts. Never ``zip -r`` (adds directory entries) and never pretty-print.
* Inert junk ([trash]/*.dat members, duplicate ids) is preserved byte-identically.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import zipfile
from copy import copy
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from lxml import etree

from .package_limits import ArchiveLimits, check_archive, check_container

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w16du": "http://schemas.microsoft.com/office/word/2023/wordml/word16du",
}
W = "{%s}" % NS["w"]
R = "{%s}" % NS["r"]
REL = "{%s}" % NS["rel"]

CT = "{%s}" % NS["ct"]
CT_PART = "[Content_Types].xml"

REL_TYPE_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
STRICT_NS_PREFIX = "http://purl.oclc.org/ooxml/"       # ISO/IEC 29500 Strict: every namespace moves here


class UnsupportedFormat(ValueError):
    """A .docx this engine does not read: ISO Strict Open XML (Word's "Strict Open XML Document (*.docx)" save
    format), whose parts use other namespaces for everything. ``code`` is what a batch records for the file."""
    code = "unsupported-format"

    def __init__(self, what: str):
        super().__init__(f"{what}: this is a Strict Open XML document (ISO/IEC 29500 Strict), which Spec Surgeon "
                         f"does not read. In Word, File → Save As → 'Word Document (*.docx)' (not 'Strict Open XML "
                         f"Document'), then run again.")


class XmlDoctype(ValueError):
    """A part carries an XML document type declaration (``<!DOCTYPE …>``), which Word never writes. The parser leaves
    an entity unresolved (``resolve_entities=False``, never loading anything external), so the part's text as read
    would silently lack every entity's text, on the transform side and the verifier's alike: refused, not guessed at.
    ``code`` is what a batch records for the file; the message is one sentence (triage gives it as its reason)."""
    code = "xml-dtd"

    def __init__(self, part: str):
        self.part = part
        self.details = {"part": part}
        super().__init__(f"{part} carries an XML document type declaration (<!DOCTYPE …>), which Word never writes, so "
                         f"its text cannot be read exactly; open the file in Word and save it again as .docx, or get "
                         f"it again from where it came from.")


HEADER_REL = REL_TYPE_BASE + "header"
FOOTER_REL = REL_TYPE_BASE + "footer"
FOOTNOTES_REL = REL_TYPE_BASE + "footnotes"
ENDNOTES_REL = REL_TYPE_BASE + "endnotes"
COMMENTS_REL = REL_TYPE_BASE + "comments"
SETTINGS_REL = REL_TYPE_BASE + "settings"
# relationship types whose targets are binary media (what unreferenced_media() may report)
MEDIA_REL_TYPES = {REL_TYPE_BASE + t for t in ("image", "audio", "video", "media")} | {
    "http://schemas.microsoft.com/office/2007/relationships/hdphoto",
    "http://schemas.microsoft.com/office/2007/relationships/media",
}


def parse_xml(data: bytes, part: str = "an XML part") -> etree._Element:
    # remove_blank_text=False: whitespace is content in OOXML; never let the parser touch it.
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
    root = etree.fromstring(data, parser)
    if has_doctype(root):
        raise XmlDoctype(part)
    return root


def has_doctype(root: etree._Element) -> bool:
    """Whether the parsed document carries a document type declaration — read off the parser's own record, so the
    answer does not depend on the part's encoding (a UTF-16 part spells ``<!DOCTYPE`` with interleaved zero bytes)."""
    info = root.getroottree().docinfo
    return info.internalDTD is not None or info.externalDTD is not None or bool(info.doctype)


def serialize_xml(root: etree._Element) -> bytes:
    """Serialize exactly the way the skill prescribes: declaration, UTF-8, standalone, no pretty print."""
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


@dataclass
class Relationship:
    rid: str
    type: str
    target: str          # as written in the rels file
    target_mode: str     # "Internal" | "External"
    resolved: Optional[str]  # package path (e.g. "word/header1.xml") for internal targets


@dataclass
class SectionInfo:
    index: int
    sect_pr_ordinal: int                  # index among all sectPr elements in document order
    header_refs: Dict[str, str] = field(default_factory=dict)   # type -> part name
    footer_refs: Dict[str, str] = field(default_factory=dict)
    title_pg: bool = False
    pg_num_start: Optional[int] = None
    pg_num_fmt: Optional[str] = None


class DocxPackage:
    """A .docx as an OPC zip: read-only view of the SOURCE bytes plus a working overlay.

    Reads go through the overlay (substituted, added, removed members), so a working copy (``copy()``) can be
    edited by several ops in sequence and every helper (rels, sections, text_parts) sees the current state.
    ``data`` and ``members`` always describe the source; ``output_bytes()`` repacks source + overlay by member
    substitution."""

    def __init__(self, data: bytes, name: str = "document.docx", *, limits: Optional[ArchiveLimits] = None):
        self.name = name
        self.data = data
        self.limits = limits or ArchiveLimits.from_environment()
        check_container(data, self.limits)
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        try:
            check_archive(self._zip, self.limits)
        except BaseException:
            self._zip.close()
            raise
        self.members: List[zipfile.ZipInfo] = self._zip.infolist()     # SOURCE members, in source order
        self.source_names: List[str] = [m.filename for m in self.members]
        self._source_set = set(self.source_names)
        self._cache: Dict[str, bytes] = {}          # source bytes (immutable; shared with copies)
        self._subs: Dict[str, bytes] = {}           # substituted source members
        self._added: Dict[str, bytes] = {}          # new members, in the order they were added
        self._removed: List[str] = []               # removed source members, in removal order
        self._names: Optional[List[str]] = None

    @classmethod
    def from_path(cls, path, *, limits: Optional[ArchiveLimits] = None) -> "DocxPackage":
        limits = limits or ArchiveLimits.from_environment()
        with open(path, "rb") as fh:
            return cls(fh.read(limits.max_archive_bytes + 1), name=str(path), limits=limits)

    def copy(self) -> "DocxPackage":
        """A working copy: same source, a snapshot of this package's overlay."""
        new = DocxPackage.__new__(DocxPackage)
        new.name, new.data, new._zip, new.members = self.name, self.data, self._zip, self.members
        new.limits = self.limits
        new.source_names, new._source_set, new._cache = self.source_names, self._source_set, self._cache
        new._subs, new._added, new._removed = dict(self._subs), dict(self._added), list(self._removed)
        new._names = None
        return new

    # ---- raw access -------------------------------------------------------------------------

    @property
    def member_names(self) -> List[str]:
        """Current members: source order minus removed members, then added members in add order."""
        if self._names is None:
            removed = set(self._removed)
            self._names = [n for n in self.source_names if n not in removed] + list(self._added)
        return self._names

    def read(self, part: str) -> bytes:
        if part in self._subs:
            return self._subs[part]
        if part in self._added:
            return self._added[part]
        if part in self._removed or part not in self._source_set:
            raise KeyError(f"no member {part!r} in {self.name}")
        if part not in self._cache:
            self._cache[part] = self._zip.read(part)
        return self._cache[part]

    def read_source(self, part: str) -> bytes:
        if part not in self._cache:
            self._cache[part] = self._zip.read(part)
        return self._cache[part]

    def has(self, part: str) -> bool:
        return part in self._added or (part in self._source_set and part not in self._removed)

    def xml(self, part: str) -> etree._Element:
        return parse_xml(self.read(part), part)

    def hashes(self) -> Dict[str, str]:
        return {n: md5(self.read(n)) for n in self.member_names}

    # ---- working overlay ---------------------------------------------------------------------

    def write(self, part: str, data: bytes) -> None:
        """Substitute an existing member (use add_member for a new one)."""
        if part in self._added:
            self._added[part] = data
        elif self.has(part):
            self._subs[part] = data
        else:
            raise KeyError(f"cannot write {part!r}: not a member (use add_member)")

    def write_xml(self, part: str, root: etree._Element) -> None:
        self.write(part, serialize_xml(root))

    def substitutions(self) -> Dict[str, bytes]:
        """Changed SOURCE members only (the v0.1.0 job interface)."""
        return dict(self._subs)

    @property
    def removed(self) -> List[str]:
        return list(self._removed)

    @property
    def added(self) -> List[str]:
        return list(self._added)

    def is_modified(self) -> bool:
        return bool(self._subs or self._added or self._removed)

    def output_bytes(self) -> bytes:
        """The working state as a package (source bytes when nothing changed)."""
        if not self.is_modified():
            return self.data
        return self.repack(self._subs, removed=self._removed, added=self._added)


    # ---- members, content types, relationships (working overlay) ----------------------------------

    def content_types(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """(defaults: extension → type, overrides: member name → type) from the current [Content_Types].xml."""
        root = self.xml(CT_PART)
        defaults = {d.get("Extension", "").lower(): d.get("ContentType") for d in root.findall(CT + "Default")}
        overrides = {o.get("PartName", "").lstrip("/"): o.get("ContentType") for o in root.findall(CT + "Override")}
        return defaults, overrides

    def content_type_of(self, name: str) -> Optional[str]:
        defaults, overrides = self.content_types()
        if name in overrides:
            return overrides[name]
        return defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())

    def remove_member(self, name: str) -> None:
        """Remove a member and its [Content_Types].xml Override (if any). Its .rels member, relationships that
        point at it, and media only it referenced are separate decisions: see edit_rels / unreferenced_media."""
        if not self.has(name):
            raise KeyError(f"cannot remove {name!r}: not a member")
        if name in (CT_PART, "_rels/.rels"):
            raise ValueError(f"refusing to remove {name}: the package cannot exist without it")
        if name in self._added:
            del self._added[name]
        else:
            self._subs.pop(name, None)
            self._removed.append(name)
        self._names = None
        root = self.xml(CT_PART)
        found = [o for o in root.findall(CT + "Override") if o.get("PartName", "").lstrip("/") == name]
        for o in found:
            root.remove(o)
        if found:
            self.write_xml(CT_PART, root)

    def add_member(self, name: str, data: bytes, content_type: Optional[str]) -> None:
        """Add a new member. ``content_type`` gets an Override unless the extension's Default already says the
        same; ``None`` means "covered by the Default for its extension" (which must exist)."""
        if self.has(name):
            raise ValueError(f"cannot add {name!r}: already a member")
        if name in self._removed:
            raise ValueError(f"cannot add {name!r}: removed earlier in this edit (substitute instead)")
        defaults, overrides = self.content_types()
        ext = posixpath.splitext(name)[1].lstrip(".").lower()
        if content_type is None:
            if ext not in defaults:
                raise ValueError(f"no content type for {name!r}: no Default for .{ext} and none given")
        elif defaults.get(ext) != content_type:
            root = self.xml(CT_PART)
            ov = etree.SubElement(root, CT + "Override")
            ov.set("PartName", "/" + name)
            ov.set("ContentType", content_type)
            self.write_xml(CT_PART, root)
        self._added[name] = data
        self._names = None

    def edit_rels(self, part: str, add: Optional[Sequence[Dict[str, str]]] = None,
                  remove: Optional[Sequence[str]] = None) -> List[str]:
        """Edit ``part``'s relationships: remove the given rIds (each must exist), then add relationships
        (dicts with ``type``, ``target`` and optionally ``target_mode``). Returns the new rIds in order (``rIdN``
        continuing from the highest numeric id ever present, so no id is reused)."""
        rp = self.rels_path_for(part)
        add, remove = list(add or []), list(remove or [])
        if not self.has(rp):
            if remove:
                raise KeyError(f"{part} has no relationships to remove")
            if not add:
                return []
            root = etree.Element(REL + "Relationships", nsmap={None: NS["rel"]})
            created = True
        else:
            root = self.xml(rp)
            created = False
        rels = {r.get("Id"): r for r in root.findall(REL + "Relationship")}
        missing = [rid for rid in remove if rid not in rels]
        if missing:
            raise KeyError(f"{rp}: no relationship(s) {missing}")
        nums = [int(rid[3:]) for rid in rels if rid.startswith("rId") and rid[3:].isdigit()]
        nxt = max(nums, default=0) + 1
        for rid in remove:
            root.remove(rels[rid])
        new_ids = []
        for spec in add:
            el = etree.SubElement(root, REL + "Relationship")
            rid = f"rId{nxt}"
            nxt += 1
            el.set("Id", rid)
            el.set("Type", spec["type"])
            el.set("Target", spec["target"])
            if spec.get("target_mode", "Internal") != "Internal":
                el.set("TargetMode", spec["target_mode"])
            new_ids.append(rid)
        if created:
            self.add_member(rp, serialize_xml(root), None)
        else:
            self.write_xml(rp, root)
        return new_ids

    def relationships(self, source: bool = False) -> List[Tuple[str, Relationship]]:
        """Every relationship as (owner part, Relationship), over rels files whose owner still exists (the
        package rels ``_rels/.rels`` always counts, owner ``""``). ``source=True`` reads the original package."""
        names = self.source_names if source else self.member_names
        present = set(names)
        out = []
        for rp in names:
            if not rp.endswith(".rels"):
                continue
            d, b = posixpath.split(rp)
            if posixpath.basename(d) != "_rels":
                continue
            owner = posixpath.join(posixpath.dirname(d), b[:-len(".rels")]) if b != ".rels" else ""
            if owner and owner not in present:
                continue
            data = self.read_source(rp) if source else self.read(rp)
            base = posixpath.dirname(owner)
            for rel in parse_xml(data, rp).findall(REL + "Relationship"):
                mode = rel.get("TargetMode", "Internal")
                target = rel.get("Target")
                resolved = None
                if mode == "Internal":
                    resolved = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(base, target))
                out.append((owner, Relationship(rel.get("Id"), rel.get("Type"), target, mode, resolved)))
        return out

    def unreferenced_media(self) -> List[str]:
        """Media members (targets of image/audio/video/media relationships in the SOURCE package) that are still
        members but that no relationship of the CURRENT package targets any more — e.g. a logo used only by a
        header part that has been removed. Media that was already orphaned in the source is not reported."""
        was_media = {r.resolved for _, r in self.relationships(source=True)
                     if r.type in MEDIA_REL_TYPES and r.resolved}
        still = {r.resolved for _, r in self.relationships() if r.resolved}
        return [n for n in self.member_names if n in was_media and n not in still]

    # ---- relationships ------------------------------------------------------------------------

    @staticmethod
    def rels_path_for(part: str) -> str:
        d, b = posixpath.split(part)
        return posixpath.join(d, "_rels", b + ".rels")

    def rels(self, part: str = "word/document.xml") -> Dict[str, Relationship]:
        rp = self.rels_path_for(part)
        if not self.has(rp):
            return {}
        root = self.xml(rp)
        base = posixpath.dirname(part)
        out: Dict[str, Relationship] = {}
        for rel in root.findall(REL + "Relationship"):
            rid = rel.get("Id")
            target = rel.get("Target")
            mode = rel.get("TargetMode", "Internal")
            resolved = None
            if mode == "Internal":
                if target.startswith("/"):
                    resolved = target.lstrip("/")
                else:
                    resolved = posixpath.normpath(posixpath.join(base, target))
            out[rid] = Relationship(rid, rel.get("Type"), target, mode, resolved)
        return out

    def main_part(self) -> str:
        """Resolve the main document part from the package rels (it is almost always word/document.xml,
        but the rule is: resolve, never assume). A Strict Open XML package (its officeDocument relationship type
        under the ISO namespace) is refused here, with a message that says what to do, rather than failing on
        some missing element deep inside."""
        root = self.xml("_rels/.rels")
        for rel in root.findall(REL + "Relationship"):
            rtype = rel.get("Type", "")
            if rtype.endswith("/officeDocument"):
                if rtype.startswith(STRICT_NS_PREFIX):
                    raise UnsupportedFormat(self.name)
                return rel.get("Target").lstrip("/")
        return "word/document.xml"

    def parts_of_type(self, rel_type: str, source: str = "word/document.xml") -> List[str]:
        return [r.resolved for r in self.rels(source).values() if r.type == rel_type and r.resolved]

    def text_parts(self) -> List[str]:
        """Every text-bearing part the verifier must scope: document, headers, footers, footnotes,
        endnotes, comments. Resolved via rels, in a stable order."""
        main = self.main_part()
        rels = self.rels(main)
        parts = [main]
        for t in (HEADER_REL, FOOTER_REL, FOOTNOTES_REL, ENDNOTES_REL, COMMENTS_REL):
            for r in rels.values():
                if r.type == t and r.resolved and r.resolved not in parts and self.has(r.resolved):
                    parts.append(r.resolved)
        return parts

    def wordprocessing_xml_parts(self) -> List[str]:
        """Every current member that holds the document's own XML, in member order: the text parts wherever their
        relationships put them, and every XML member the main part relates (wherever its relationship puts it) or
        that lies in the main part's folder (usually ``word/``, subfolders included) — styles, numbering, settings,
        theme, the glossary, the comment side parts. A member is XML by its declared content type (its Override, part
        names compared case-insensitively as OPC compares them, else its extension's Default) ending in ``xml``, or,
        with none declared, by a ``.xml`` extension in any case. Relationship parts are not included, nor pictures:
        an SVG (``image/svg+xml``) is XML and often carries a DOCTYPE."""
        defaults, overrides = self.content_types() if self.has(CT_PART) else ({}, {})
        folded = {name.casefold(): ctype for name, ctype in overrides.items()}
        main = self.main_part()
        folder = posixpath.dirname(main)
        prefix = folder + "/" if folder else ""
        text = set(self.text_parts())
        related = {r.resolved for r in self.rels(main).values() if r.target_mode == "Internal" and r.resolved}
        out = []
        for name in self.member_names:
            if name in text:
                out.append(name)
                continue
            if posixpath.basename(posixpath.dirname(name)) == "_rels" or not (name in related or name.startswith(prefix)):
                continue
            ctype = folded.get(name.casefold()) or defaults.get(posixpath.splitext(name)[1].lstrip(".").lower())
            if ctype is None:
                xml = name.lower().endswith(".xml")
            else:
                xml = ctype.lower().endswith("xml") and not ctype.lower().startswith("image/")
            if xml:
                out.append(name)
        return out

    def check_no_doctype(self) -> None:
        """Raise ``XmlDoctype`` for the first WordprocessingML part (``wordprocessing_xml_parts``) that carries a
        document type declaration — at attach time, before any request reads the part (``parse_xml`` refuses it then
        anyway). Only a part whose bytes spell ``<!DOCTYPE`` in an ASCII-compatible or UTF-16 encoding is parsed, and
        the parser decides; the others cannot hold one in an encoding Word reads."""
        marks = tuple("<!DOCTYPE".encode(e) for e in ("ascii", "utf-16-le", "utf-16-be"))
        for part in self.wordprocessing_xml_parts():
            if any(m in self.read(part) for m in marks):
                self.xml(part)

    def parts_by_kind(self) -> Dict[str, List[str]]:
        """{'document': [...], 'headers': [...], 'footers': [...], 'footnotes': [...], 'endnotes': [...],
        'comments': [...]} — resolved via rels, never by file name."""
        main = self.main_part()
        rels = self.rels(main)
        kinds = {"document": [main], "headers": [], "footers": [], "footnotes": [], "endnotes": [], "comments": []}
        mapping = {HEADER_REL: "headers", FOOTER_REL: "footers", FOOTNOTES_REL: "footnotes",
                   ENDNOTES_REL: "endnotes", COMMENTS_REL: "comments"}
        for r in rels.values():
            k = mapping.get(r.type)
            if k and r.resolved and self.has(r.resolved) and r.resolved not in kinds[k]:
                kinds[k].append(r.resolved)
        return kinds

    # ---- sections -----------------------------------------------------------------------------

    @staticmethod
    def sect_prs(root: etree._Element) -> List[etree._Element]:
        """Every sectPr in document order: those inside paragraph pPr, then the body-level final one."""
        body = root.find(W + "body")
        if body is None:
            if etree.QName(root).namespace and etree.QName(root).namespace.startswith(STRICT_NS_PREFIX):
                raise UnsupportedFormat("the main document part")
            raise ValueError("the main document part has no w:body")
        out = []
        for p in body.iter(W + "p"):
            ppr = p.find(W + "pPr")
            if ppr is not None:
                sp = ppr.find(W + "sectPr")
                if sp is not None:
                    out.append(sp)
        final = body.find(W + "sectPr")
        if final is not None:
            out.append(final)
        return out

    def sections(self) -> List[SectionInfo]:
        main = self.main_part()
        root = self.xml(main)
        rels = self.rels(main)
        sect_prs = self.sect_prs(root)
        infos = []
        for i, sp in enumerate(sect_prs):
            info = SectionInfo(index=i, sect_pr_ordinal=i)
            for ref in sp.findall(W + "headerReference"):
                rid = ref.get(R + "id")
                if rid in rels and rels[rid].resolved:
                    info.header_refs[ref.get(W + "type", "default")] = rels[rid].resolved
            for ref in sp.findall(W + "footerReference"):
                rid = ref.get(R + "id")
                if rid in rels and rels[rid].resolved:
                    info.footer_refs[ref.get(W + "type", "default")] = rels[rid].resolved
            tp = sp.find(W + "titlePg")             # w:val 0/false/off turns it off (CT_OnOff)
            info.title_pg = tp is not None and tp.get(W + "val") not in ("0", "false", "off")
            pg = sp.find(W + "pgNumType")
            if pg is not None:
                start = pg.get(W + "start")
                info.pg_num_start = int(start) if start is not None else None
                info.pg_num_fmt = pg.get(W + "fmt")
            infos.append(info)
        return infos

    def settings_part(self) -> Optional[str]:
        """The document settings part, found as Word finds it: through the main part's relationships, never by its
        usual name. None when the main part relates none (a stray ``word/settings.xml`` it does not relate is not
        its settings, and Word reads nothing from it)."""
        for part in self.parts_of_type(SETTINGS_REL, self.main_part()):
            if self.has(part):
                return part
        return None

    def settings_flag(self, name: str) -> bool:
        part = self.settings_part()
        if part is None:
            return False
        root = self.xml(part)
        el = root.find(W + name)
        if el is None:
            return False
        val = el.get(W + "val")
        return val not in ("0", "false", "off")

    # ---- repack -------------------------------------------------------------------------------

    def repack(self, substitutions: Optional[Dict[str, bytes]] = None, removed: Iterable[str] = (),
               added: Optional[Dict[str, bytes]] = None) -> bytes:
        """Write from SOURCE, preserving order, compression method, metadata and uncompressed member bytes.
        Compressed records are regenerated; no-op repacks return the original archive bytes.
        Substituted members keep safe ZipInfo metadata; sizes/CRC/ZIP64 offsets are regenerated.
        ``removed`` members are left out;
        ``added`` members are appended in order (deflated, dated like the first source member)."""
        substitutions = substitutions or {}
        removed = set(removed)
        added = added or {}
        unknown = (set(substitutions) | removed) - self._source_set
        if unknown:
            raise ValueError(f"substitution/removal of non-existent members: {sorted(unknown)}")
        clash = set(added) & (self._source_set - removed)
        if clash:
            raise ValueError(f"added members already exist: {sorted(clash)}")
        if not removed and not added and all(
                info.filename not in substitutions or substitutions[info.filename] == self._zip.read(info)
                for info in self.members):
            return self.data
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            out.comment = self._zip.comment
            for info in self.members:
                if info.filename in removed:
                    continue
                data = substitutions.get(info.filename, None)
                if data is None:
                    data = self._zip.read(info)  # retain distinct occurrences of duplicate inert names
                zi = copy(info)
                zi.extra = _safe_zip_extra(info.extra)
                out.writestr(zi, data)
                # zipfile supplies default permissions when this is zero; zero was intentional too.
                zi.external_attr = info.external_attr
            stamp = self.members[0].date_time if self.members else (1980, 1, 1, 0, 0, 0)
            for name, data in added.items():
                zi = zipfile.ZipInfo(name, date_time=stamp)
                zi.compress_type = zipfile.ZIP_DEFLATED
                out.writestr(zi, data)
        return buf.getvalue()

    def close(self):
        self._zip.close()


def _safe_zip_extra(extra: bytes) -> bytes:
    """Keep opaque metadata except ZIP64's stale sizes/offsets (zipfile regenerates it)."""
    kept = bytearray()
    pos = 0
    while pos + 4 <= len(extra):
        kind = int.from_bytes(extra[pos:pos + 2], "little")
        size = int.from_bytes(extra[pos + 2:pos + 4], "little")
        end = pos + 4 + size
        if end > len(extra):
            # zipfile already rejects malformed complete records at intake.
            raise ValueError("truncated ZIP extra metadata")
        if kind != 0x0001:
            kept.extend(extra[pos:end])
        pos = end
    kept.extend(extra[pos:])  # preserve an inert short trailing field, as zipfile does
    return bytes(kept)
