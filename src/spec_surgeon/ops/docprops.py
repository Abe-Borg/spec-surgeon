"""Ops: set_document_properties and set_document_state — built-in batch 2, items 20 and 21
(docs/BUILTIN_OPS_BATCH2_DESIGN.md §0, §11, §12). The verifier reads every property and setting again with its own code
(verify/docprops.py, kinds ``document_properties`` and ``document_state``) and V10's member residual compares everything
else in those members, element for element.

These are the operations the method used to forbid (``docProps`` are Word's, ``w:trackRevisions`` is the user's choice):
they run only when the user asks for them by name, and change exactly the named properties and settings.

**Document properties** (CLAUDE.md "Document properties", both sides):
* core (the package's ``core-properties`` relationship, usually ``docProps/core.xml``): ``title`` dc:title, ``subject``
  dc:subject, ``author`` dc:creator, ``keywords`` cp:keywords, ``comments`` dc:description, ``category`` cp:category,
  ``status`` cp:contentStatus, ``last_modified_by`` cp:lastModifiedBy; app (``extended-properties``): ``company``
  Company, ``manager`` Manager. Set: the element's children go and its text is the value (its attributes stay; a
  missing element is created at the end of the root — the parts are ``xsd:all``); clear: the element is removed — one
  that is absent or empty (no text, no child elements: Word shows it as no value) is already clear. Two elements of one
  property: ``structure``.
* custom (``custom-properties``, usually ``docProps/custom.xml``): a property is matched by name, casefolded. Set an
  existing one: its value element keeps its tag (``vt:lpwstr`` or ``vt:lpstr``) and takes the value — another type
  (a number, a date, yes/no) is refused (``property-type``: the file decides it, so never ``bad-params``, which the
  compiler reads as a mistake of the spec for every file); a new one is appended — fmtid
  ``{D5CDD505-2E9C-101B-9397-08002B2CF9AE}``, pid the largest pid of the source (at least 1) plus 1, plus 1 for each
  new one before it in the request's order — with a ``vt:lpwstr`` value. Clear: the property is removed. A file without
  a custom part gets ``docProps/custom.xml`` (only that member: its package relationship and content-type override with
  it; CLAUDE.md rule 10's third case — the op's purpose is the property).
* a value is the user's words: text, not blank, one line, no control or invisible character, at most 255 characters.
* "already as asked" (the same text, an absent element to clear) changes nothing and is noted.

**Settings and Mark as Final**:
* ``track_changes`` off: ``w:trackRevisions`` (on) is removed from the settings part; on: ``<w:trackRevisions/>`` is
  inserted in CT_Settings' schema order (``SETTINGS_ORDER``) or an off one turned on (its ``w:val`` removed). A
  tracking lock (``w:documentProtection w:edit="trackedChanges"``, enforced) refuses off (``protected``).
* ``final`` true: the custom property ``_MarkAsFinal`` = ``<vt:bool>true</vt:bool>`` and ``cp:contentStatus`` = ``Final``
  (what Word's *Mark as Final* writes; an existing ``_MarkAsFinal`` that is not a ``vt:bool``: ``property-type``); false:
  ``_MarkAsFinal`` removed and a ``Final`` status removed (another status is the user's and stays).
* layout reference: ``source`` — except a file where a field shows a property set_document_properties changes: a
  renderer draws it with the new value (Word keeps the saved result until fields update), so ``none``, with that reason.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import CT_PART, REL_TYPE_BASE, W, DocxPackage, serialize_xml
from . import register
from .base import JobException, Op, PlanFragment
from .fields import fields_of

CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
CU_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
CP, DC, EP, CU, VT = ("{%s}" % n for n in (CP_NS, DC_NS, EP_NS, CU_NS, VT_NS))

CORE_REL = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
APP_REL = REL_TYPE_BASE + "extended-properties"
CUSTOM_REL = REL_TYPE_BASE + "custom-properties"
SETTINGS_REL = REL_TYPE_BASE + "settings"
CUSTOM_MEMBER = "docProps/custom.xml"
CUSTOM_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
PACKAGE_RELS = "_rels/.rels"
FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
MARK_AS_FINAL = "_MarkAsFinal"
FINAL_STATUS = "Final"
MAX_VALUE = 255
FALSE = ("0", "false", "off")
TEXT_TYPES = (VT + "lpwstr", VT + "lpstr")
UNSEEN_CATEGORIES = ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp")

# property name → (which part, the element's qualified name)
PROPERTIES: Dict[str, Tuple[str, str]] = {
    "title": ("core", DC + "title"),
    "subject": ("core", DC + "subject"),
    "author": ("core", DC + "creator"),
    "keywords": ("core", CP + "keywords"),
    "comments": ("core", DC + "description"),
    "category": ("core", CP + "category"),
    "status": ("core", CP + "contentStatus"),
    "last_modified_by": ("core", CP + "lastModifiedBy"),
    "company": ("app", EP + "Company"),
    "manager": ("app", EP + "Manager"),
}
PART_RELS = {"core": CORE_REL, "app": APP_REL, "custom": CUSTOM_REL}

# which fields show which property (Word keeps a field's saved result until fields are updated)
FIELD_PROPERTY = {"TITLE": "title", "SUBJECT": "subject", "AUTHOR": "author", "KEYWORDS": "keywords",
                  "COMMENTS": "comments", "LASTSAVEDBY": "last_modified_by"}
DOCPROPERTY_NAMES = {"title": "title", "subject": "subject", "author": "author", "keywords": "keywords",
                     "comments": "comments", "category": "category", "company": "company", "manager": "manager",
                     "lastsavedby": "last_modified_by"}

# CT_Settings, in schema order (schemas/ISO-IEC29500-4_2016/wml.xsd); m:mathPr and sl:schemaLibrary by their namespaces
_M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_SL = "{http://schemas.openxmlformats.org/schemaLibrary/2006/main}"
SETTINGS_ORDER = [W + n for n in (
    "writeProtection", "view", "zoom", "removePersonalInformation", "removeDateAndTime", "doNotDisplayPageBoundaries",
    "displayBackgroundShape", "printPostScriptOverText", "printFractionalCharacterWidth", "printFormsData",
    "embedTrueTypeFonts", "embedSystemFonts", "saveSubsetFonts", "saveFormsData", "mirrorMargins",
    "alignBordersAndEdges", "bordersDoNotSurroundHeader", "bordersDoNotSurroundFooter", "gutterAtTop",
    "hideSpellingErrors", "hideGrammaticalErrors", "activeWritingStyle", "proofState", "formsDesign",
    "attachedTemplate", "linkStyles", "stylePaneFormatFilter", "stylePaneSortMethod", "documentType", "mailMerge",
    "revisionView", "trackRevisions", "doNotTrackMoves", "doNotTrackFormatting", "documentProtection",
    "autoFormatOverride", "styleLockTheme", "styleLockQFSet", "defaultTabStop", "autoHyphenation",
    "consecutiveHyphenLimit", "hyphenationZone", "doNotHyphenateCaps", "showEnvelope", "summaryLength",
    "clickAndTypeStyle", "defaultTableStyle", "evenAndOddHeaders", "bookFoldRevPrinting", "bookFoldPrinting",
    "bookFoldPrintingSheets", "drawingGridHorizontalSpacing", "drawingGridVerticalSpacing",
    "displayHorizontalDrawingGridEvery", "displayVerticalDrawingGridEvery", "doNotUseMarginsForDrawingGridOrigin",
    "drawingGridHorizontalOrigin", "drawingGridVerticalOrigin", "doNotShadeFormData", "noPunctuationKerning",
    "characterSpacingControl", "printTwoOnOne", "strictFirstAndLastChars", "noLineBreaksAfter", "noLineBreaksBefore",
    "savePreviewPicture", "doNotValidateAgainstSchema", "saveInvalidXml", "ignoreMixedContent",
    "alwaysShowPlaceholderText", "doNotDemarcateInvalidXml", "saveXmlDataOnly", "useXSLTWhenSaving",
    "saveThroughXslt", "showXMLTags", "alwaysMergeEmptyNamespace", "updateFields", "hdrShapeDefaults", "footnotePr",
    "endnotePr", "compat", "docVars", "rsids")] + [_M + "mathPr"] + [W + n for n in (
    "attachedSchema", "themeFontLang", "clrSchemeMapping", "doNotIncludeSubdocsInStats", "doNotAutoCompressPictures",
    "forceUpgrade", "captions", "readModeInkLockDown", "smartTagType")] + [_SL + "schemaLibrary"] + [W + n for n in (
    "shapeDefaults", "doNotEmbedSmartTags", "decimalSymbol", "listSeparator")]


# ---- finding the members ---------------------------------------------------------------------------------------------

def package_member(pkg: DocxPackage, rel_type: str) -> Optional[str]:
    """The member the package relationships give for ``rel_type`` (present in the package), or None."""
    for r in pkg.rels("").values():
        if r.type == rel_type and r.target_mode == "Internal" and r.resolved and pkg.has(r.resolved):
            return r.resolved
    return None


def settings_member(pkg: DocxPackage) -> Optional[str]:
    main = pkg.main_part()
    found = [p for p in pkg.parts_of_type(SETTINGS_REL, main) if pkg.has(p)]
    return found[0] if found else None


# ---- values ----------------------------------------------------------------------------------------------------------

def placeable_value(value: Any, where: str) -> str:
    """A property value as it may be written: text, not blank, one line, no control or invisible character, at most
    255 characters (a zero-width space pasted from a web page would otherwise travel into every file unseen)."""
    if not isinstance(value, str) or not value.strip():
        raise JobException(f"{where}: the value must be non-empty text, got {value!r} (use 'clear' to remove a "
                           f"property)", "bad-params", {"where": where})
    bad = sorted({f"U+{ord(c):04X}" for c in value if unicodedata.category(c) in UNSEEN_CATEGORIES})
    if bad:
        raise JobException(f"{where}: the value {value!r} holds a control or invisible character ({', '.join(bad)}) — "
                           f"one line of visible text only", "bad-params", {"where": where})
    if len(value) > MAX_VALUE:
        raise JobException(f"{where}: the value is {len(value)} characters long; Word keeps at most {MAX_VALUE}",
                           "bad-params", {"where": where})
    return value


def _name_ok(name: Any, where: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise JobException(f"{where}: a custom property needs a name", "bad-params", {"where": where})
    if any(unicodedata.category(c) in UNSEEN_CATEGORIES for c in name) or len(name) > MAX_VALUE:
        raise JobException(f"{where}: the custom property name {name!r} is not one line of visible text of at most "
                           f"{MAX_VALUE} characters", "bad-params", {"where": where})
    return name


def name_key(name: str) -> str:
    """How custom property names are compared (Word treats them case-insensitively)."""
    return name.casefold()


# ---- reading the source ----------------------------------------------------------------------------------------------

def _one(root, tag: str, where: str) -> Optional[Any]:
    found = [c for c in root if isinstance(c.tag, str) and c.tag == tag]
    if len(found) > 1:
        raise JobException(f"{where} holds {len(found)} {etree.QName(tag).localname} elements; only one is allowed",
                           "malformed-properties", {"where": where})
    return found[0] if found else None


def element_text(el) -> Optional[str]:
    """A property element's value as it reads: its text (None when it has element children: not a plain value)."""
    if el is None:
        return None
    if any(isinstance(c.tag, str) for c in el):
        return None
    return el.text or ""


class CustomProps:
    """The custom properties of a package (none when the part is missing)."""

    def __init__(self, pkg: DocxPackage):
        self.member = package_member(pkg, CUSTOM_REL)
        self.root = pkg.xml(self.member) if self.member else None
        self.by_key: Dict[str, Any] = {}
        self.max_pid = 1
        if self.root is not None:
            for prop in self.root.findall(CU + "property"):
                name = prop.get("name") or ""
                k = name_key(name)
                if k in self.by_key:
                    raise JobException(f"{self.member} holds two custom properties named {name!r}",
                                       "malformed-properties", {"name": name})
                self.by_key[k] = prop
                try:
                    self.max_pid = max(self.max_pid, int(prop.get("pid") or "0"))
                except ValueError:
                    raise JobException(f"{self.member}: the custom property {name!r} has an unreadable pid "
                                       f"{prop.get('pid')!r}", "malformed-properties", {"name": name})

    def get(self, name: str) -> Optional[Any]:
        return self.by_key.get(name_key(name))


def value_element(prop) -> Optional[Any]:
    kids = [c for c in prop if isinstance(c.tag, str)]
    return kids[0] if len(kids) == 1 else None


def text_value(prop, where: str) -> Tuple[str, str]:
    """(the value element's tag, its text) of a custom property that holds text; ``property-type`` otherwise (the
    file's own part decides it — never ``bad-params``, which the compiler reads as a spec mistake for every file)."""
    v = value_element(prop)
    if v is None or v.tag not in TEXT_TYPES:
        kind = etree.QName(v.tag).localname if v is not None else "no single value"
        raise JobException(f"{where}: the custom property {prop.get('name')!r} holds {kind}, not text — change it in "
                           f"Word (File → Info → Properties → Advanced → Custom)", "property-type", {"where": where})
    return v.tag, v.text or ""


# ---- the plan's shared pieces ----------------------------------------------------------------------------------------

class Changes:
    """What one op writes into the properties members: per member, the edits apply() performs (planned from the
    SOURCE), and the bookkeeping the fragment needs."""

    def __init__(self, pkg: DocxPackage):
        self.pkg = pkg
        self.members = {k: package_member(pkg, rel) for k, rel in PART_RELS.items() if k != "custom"}
        self.custom = CustomProps(pkg)
        self.edits: Dict[str, List[Dict[str, Any]]] = {}    # member → [{op, tag/name, value …}]
        self.new_custom: List[Tuple[str, str, str]] = []      # (name, value tag, value) of properties to create
        self.notes: List[str] = []
        self.changed_keys: List[str] = []                    # "<member>|<key>" of every property that changes

    def member(self, where: str, which: str, purpose: str) -> str:
        m = self.members.get(which)
        if m is None:
            rel = PART_RELS[which].rsplit("/", 1)[-1]
            raise JobException(f"{purpose}: this file has no {which} properties part (no '{rel}' relationship in the "
                               f"package) — save it from Word once and run again", "no-part", {"where": where})
        return m

    def set_element(self, which: str, tag: str, value: str, label: str) -> None:
        member = self.member(label, which, f"setting {label}")
        root = self.pkg.xml(member)
        el = _one(root, tag, member)
        if el is not None and element_text(el) == value:
            self.notes.append(f"{label} already reads {value!r}; unchanged")
            return
        self.edits.setdefault(member, []).append({"op": "set", "tag": tag, "value": value})
        self.changed_keys.append(f"{member}|{tag}")

    def clear_element(self, which: str, tag: str, label: str) -> None:
        member = self.member(label, which, f"clearing {label}")
        root = self.pkg.xml(member)
        el = _one(root, tag, member)
        if el is None or element_text(el) == "":
            # absent, or an empty value (Word shows it as none)
            self.notes.append(f"{label} is not set; nothing to clear")
            return
        self.edits.setdefault(member, []).append({"op": "remove", "tag": tag})
        self.changed_keys.append(f"{member}|{tag}")

    def set_custom(self, name: str, value: str, value_tag: Optional[str] = None) -> None:
        """Set a custom property; ``value_tag`` None = text (``vt:lpwstr`` when new, the existing text tag kept), else
        the value element's tag (``vt:bool`` for _MarkAsFinal)."""
        where = f"custom property {name!r}"
        prop = self.custom.get(name)
        if prop is not None:
            if value_tag is None:
                tag, current = text_value(prop, where)
            else:
                v = value_element(prop)
                if v is None or v.tag != value_tag:
                    raise JobException(f"{where} holds {etree.QName(v.tag).localname if v is not None else 'no value'}"
                                       f", not {etree.QName(value_tag).localname} — change it in Word", "property-type",
                                       {"where": where})
                tag, current = v.tag, v.text or ""
            if current == value:
                self.notes.append(f"{where} already reads {value!r}; unchanged")
                return
            self.edits.setdefault(self.custom.member, []).append({"op": "custom_set", "name": prop.get("name"),
                                                                  "value": value})
            self.changed_keys.append(f"{self.custom.member}|property:{prop.get('name')}")
            return
        self.new_custom.append((name, value_tag or VT + "lpwstr", value))

    def clear_custom(self, name: str) -> None:
        prop = self.custom.get(name)
        if prop is None:
            self.notes.append(f"custom property {name!r} is not set; nothing to clear")
            return
        self.edits.setdefault(self.custom.member, []).append({"op": "custom_remove", "name": prop.get("name")})
        self.changed_keys.append(f"{self.custom.member}|property:{prop.get('name')}")

    def finish(self, frag: PlanFragment) -> Dict[str, Any]:
        """Put the members, touches and member additions into the fragment; the transform data for apply()."""
        td: Dict[str, Any] = {"edits": self.edits, "create": None, "append": []}
        for member, edits in self.edits.items():
            if member not in frag.intended_parts:
                frag.intended_parts.append(member)
            for e in edits:
                key = etree.QName(e["tag"]).localname if "tag" in e else name_key(e["name"])
                frag.touches.append((member, f"property:{key}", "value"))
        if self.new_custom:
            first = self.custom.max_pid + 1
            props = [{"name": n, "value_tag": t, "value": v, "pid": first + i}
                     for i, (n, t, v) in enumerate(self.new_custom)]
            if self.custom.member is None:
                if self.pkg.has(CUSTOM_MEMBER):
                    raise JobException(f"{CUSTOM_MEMBER} is in the package but no relationship names it as the custom "
                                       f"properties part — save the file from Word once and run again",
                                       "malformed-properties", {"member": CUSTOM_MEMBER})
                td["create"] = {"member": CUSTOM_MEMBER, "properties": props}
                frag.members_added.append(CUSTOM_MEMBER)
                for m in (PACKAGE_RELS, CT_PART):
                    if m not in frag.intended_parts:
                        frag.intended_parts.append(m)
                frag.touches += [(CUSTOM_MEMBER, "*", "add"), (PACKAGE_RELS, "*", "rels"), (CT_PART, "*", "ct")]
            else:
                td["append"] = props
                if self.custom.member not in frag.intended_parts:
                    frag.intended_parts.append(self.custom.member)
                # new properties take the next pids: two ops adding properties to one part would both take them
                frag.touches.append((self.custom.member, "pid", "add"))
                frag.touches += [(self.custom.member, f"property:{name_key(p['name'])}", "value") for p in props]
            where = td["create"]["member"] if td["create"] else self.custom.member
            self.changed_keys += [f"{where}|property:{p['name']}" for p in props]
        frag.notes.extend(self.notes)
        return td


def apply_changes(pkg: DocxPackage, td: Dict[str, Any]) -> None:
    for member, edits in td.get("edits", {}).items():
        root = pkg.xml(member)
        for e in edits:
            if e["op"] in ("set", "remove"):
                el = next((c for c in root if isinstance(c.tag, str) and c.tag == e["tag"]), None)
                if e["op"] == "remove":
                    root.remove(el)
                    continue
                if el is None:
                    el = etree.SubElement(root, e["tag"])
                for c in list(el):
                    el.remove(c)
                el.text = e["value"]
            else:
                prop = next(p for p in root.findall(CU + "property") if p.get("name") == e["name"])
                if e["op"] == "custom_remove":
                    root.remove(prop)
                else:
                    value_element(prop).text = e["value"]
        if td.get("append") and member == td.get("custom_member"):
            _append_props(root, td["append"])
        pkg.write_xml(member, root)
    if td.get("append") and td.get("custom_member") not in td.get("edits", {}):
        root = pkg.xml(td["custom_member"])
        _append_props(root, td["append"])
        pkg.write_xml(td["custom_member"], root)
    if td.get("create"):
        root = etree.Element(CU + "Properties", nsmap={None: CU_NS, "vt": VT_NS})
        _append_props(root, td["create"]["properties"])
        pkg.add_member(td["create"]["member"], serialize_xml(root), CUSTOM_CT)
        pkg.edit_rels("", add=[{"type": CUSTOM_REL, "target": td["create"]["member"]}])


def _append_props(root, props: Sequence[Dict[str, Any]]) -> None:
    for p in props:
        el = etree.SubElement(root, CU + "property")
        el.set("fmtid", FMTID)
        el.set("pid", str(p["pid"]))
        el.set("name", p["name"])
        v = etree.SubElement(el, p["value_tag"])
        v.text = p["value"]


# ---- flags -----------------------------------------------------------------------------------------------------------

def _docproperty_name(instruction: str) -> str:
    rest = instruction.strip()[len("DOCPROPERTY"):].strip()
    m = re.match(r'"([^"]*)"|(\S+)', rest)
    return (m.group(1) if m and m.group(1) is not None else (m.group(2) if m else "")) or ""


def field_flags(pkg: DocxPackage, changed: Sequence[str], custom_names: Sequence[str]) -> List[str]:
    """One flag per field (in any text part) that shows a property this op changes: it keeps its saved result."""
    keys = set(changed)
    custom = {name_key(n) for n in custom_names}
    out = []
    for part in pkg.text_parts():
        found: Dict[str, int] = {}
        for f in fields_of(pkg.xml(part)):
            prop = None
            if f.type in FIELD_PROPERTY and FIELD_PROPERTY[f.type] in keys:
                prop = FIELD_PROPERTY[f.type]
            elif f.type == "DOCPROPERTY":
                name = _docproperty_name(f.instruction)
                if DOCPROPERTY_NAMES.get(name.casefold()) in keys:
                    prop = DOCPROPERTY_NAMES[name.casefold()]
                elif name_key(name) in custom:
                    prop = f"custom property {name!r}"
            if prop:
                label = f"{f.type} {_docproperty_name(f.instruction)!r}" if f.type == "DOCPROPERTY" else f.type
                found[f"{label} field (shows {prop})"] = found.get(f"{label} field (shows {prop})", 0) + 1
        for what, n in found.items():
            out.append(f"{part}: {n} {what} keep{'s' if n == 1 else ''} the value it showed when the file was last "
                       f"saved, until Word updates fields (F9, or printing with 'Update fields')")
    return out


def author_flags(pkg: DocxPackage) -> List[str]:
    revs = 0
    for part in pkg.text_parts():
        root = pkg.xml(part)
        revs += sum(1 for el in root.iter() if isinstance(el.tag, str) and el.get(W + "author") is not None
                    and el.tag != W + "comment")
    kinds = pkg.parts_by_kind()
    comments = sum(1 for part in kinds.get("comments", []) for _ in pkg.xml(part).iter(W + "comment"))
    out = []
    if revs:
        out.append(f"{revs} tracked change(s) keep their authors' names (resolve them with resolve_tracked_changes)")
    if comments:
        out.append(f"{comments} comment(s) keep their authors' names (remove them with remove_comments)")
    return out


# ---- set_document_properties -----------------------------------------------------------------------------------------

@register
class SetDocumentProperties(Op):
    name = "set_document_properties"
    mode = "untracked"
    description = ("Set or clear the file's document properties (File → Info → Properties): title, subject, author, "
                   "keywords, comments, category, status, last modified by, company, manager, and custom text "
                   "properties — e.g. set the title and company, or clear the author names. Only on request: the "
                   "values are the user's words. Fields that show a property keep their result until Word updates "
                   "fields (flagged).")
    params_schema = {
        "type": "object",
        "properties": {
            "set": {"type": "object", "properties": {k: {"type": "string"} for k in PROPERTIES},
                    "description": "property → the user's exact value (one line, at most 255 characters)"},
            "clear": {"type": "array", "items": {"type": "string", "enum": list(PROPERTIES)},
                      "description": "properties to remove (e.g. author, last_modified_by)"},
            "custom": {"type": "object", "description": "custom text property name → the user's exact value "
                                                       "(created when missing)"},
            "clear_custom": {"type": "array", "items": {"type": "string"},
                             "description": "custom property names to remove"},
        },
    }
    select_schema = {"type": "object", "description": "no selector: the file's properties"}
    verification_profile = {
        "V1": "only docProps/core.xml / app.xml / custom.xml change (custom.xml created with its relationship and "
              "content-type override when missing)",
        "V5a": "the changed properties parts validate against the OPC schemas",
        "V10": "document_properties: every asked property re-read in the output (text exact, cleared = absent); the "
               "member residual compares everything else in those parts element for element",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("set_document_properties takes no selector", "bad-params")
        sets = params.get("set") or {}
        clears = list(params.get("clear") or [])
        custom = params.get("custom") or {}
        clear_custom = list(params.get("clear_custom") or [])
        if not isinstance(sets, dict) or not isinstance(custom, dict):
            raise JobException("'set' and 'custom' are objects of name → value", "bad-params")
        if not (sets or clears or custom or clear_custom):
            raise JobException("say which properties to set or clear", "bad-params")
        unknown = sorted((set(sets) | set(clears)) - set(PROPERTIES))
        if unknown:
            raise JobException(f"unknown properties {unknown} (known: {list(PROPERTIES)})", "bad-params")
        both = sorted(set(sets) & set(clears))
        if both or len(set(clears)) != len(clears):
            raise JobException(f"{both or 'a property'} is both set and cleared, or cleared twice", "bad-params")
        for k, v in sets.items():
            placeable_value(v, f"set.{k}")
        seen: Dict[str, str] = {}
        for n in list(custom) + clear_custom:
            _name_ok(n, "custom property")
            if name_key(n) in seen:
                raise JobException(f"custom property {n!r} is named twice (names are compared without case)",
                                   "bad-params")
            seen[name_key(n)] = n
        for n, v in custom.items():
            placeable_value(v, f"custom.{n}")
        ch = Changes(pkg)
        for k, v in sets.items():
            which, tag = PROPERTIES[k]
            ch.set_element(which, tag, v, k)
        for k in clears:
            which, tag = PROPERTIES[k]
            ch.clear_element(which, tag, k)
        for n, v in custom.items():
            ch.set_custom(n, v)
        for n in clear_custom:
            ch.clear_custom(n)
        words = bool(sets or custom)
        frag = self.fragment({}, params, mode="untracked" if words else "formatting")
        td = ch.finish(frag)
        td["custom_member"] = ch.custom.member
        frag.transform_data = td
        frag.geometry_reference = "source"
        frag.property_assertions.append({
            "kind": "document_properties", "set": dict(sets), "clear": clears,
            "custom": [[n, v] for n, v in custom.items()],
            "clear_custom": clear_custom, "changed": sorted(ch.changed_keys)})
        changed_props = [k for k in list(sets) + clears if any(e.endswith("|" + PROPERTIES[k][1])
                                                               for e in ch.changed_keys)]
        changed_custom = {name_key(k.split("|property:", 1)[1]) for k in ch.changed_keys if "|property:" in k}
        shown = field_flags(pkg, changed_props,
                            [n for n in list(custom) + clear_custom if name_key(n) in changed_custom])
        frag.flags += shown
        if shown:
            # a renderer draws such a field with the property's new value (Word keeps the saved result until fields are
            # updated): the source render is no layout reference for the lines they stand on
            frag.geometry_reference = "none"
            frag.geometry_reason = ("fields show a changed property; a renderer draws them with the new value, so the "
                                    "words beside them move")
        if {"author", "last_modified_by"} & set(clears):
            frag.flags += author_flags(pkg)
        frag.notes.append("set_document_properties: " + (", ".join(
            [f"{k} = {v!r}" for k, v in sets.items()] + [f"{k} cleared" for k in clears]
            + [f"custom {n!r} = {v!r}" for n, v in custom.items()] + [f"custom {n!r} cleared" for n in clear_custom]))
            + ("; docProps/custom.xml created" if td.get("create") else ""))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        apply_changes(pkg, frag.transform_data)


# ---- set_document_state ----------------------------------------------------------------------------------------------

def _on(el) -> bool:
    return el is not None and el.get(W + "val") not in FALSE


def tracking_locked(settings_root) -> bool:
    for dp in settings_root.findall(W + "documentProtection"):
        if dp.get(W + "edit") == "trackedChanges" and (dp.get(W + "enforcement") or "").lower() in ("1", "true", "on"):
            return True
    return False


def settings_rank(el) -> float:
    try:
        return SETTINGS_ORDER.index(el.tag)
    except ValueError:
        return len(SETTINGS_ORDER)                 # extension elements (w14:…, w15:…) after every schema element


@register
class SetDocumentState(Op):
    name = "set_document_state"
    mode = "formatting"
    description = ("Turn the file's Track Changes setting off (or on), and/or mark the file as final (Word's Mark as "
                   "Final: it opens read-only with a 'Marked as Final' bar) or unmark it. Only on request. Track "
                   "changes locked with a password is refused.")
    params_schema = {
        "type": "object",
        "properties": {
            "track_changes": {"type": "string", "enum": ["off", "on"],
                              "description": "the Track Changes setting the file opens with"},
            "final": {"type": "boolean", "description": "true = mark as final, false = unmark"},
        },
    }
    select_schema = {"type": "object", "description": "no selector: the file's settings"}
    verification_profile = {
        "V1": "only word/settings.xml, docProps/core.xml and docProps/custom.xml change (custom.xml created with its "
              "relationship and content-type override when missing)",
        "V5a": "settings.xml validates (w:trackRevisions in schema order)",
        "V10": "document_state: the setting and the two Mark-as-Final markers re-read in the output; the member "
               "residual compares everything else",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("set_document_state takes no selector", "bad-params")
        track = params.get("track_changes")
        final = params.get("final")
        if track is None and final is None:
            raise JobException("say 'track_changes' (off/on) and/or 'final' (true/false)", "bad-params")
        if track not in (None, "off", "on"):
            raise JobException("track_changes is 'off' or 'on'", "bad-params")
        if final is not None and not isinstance(final, bool):
            raise JobException("final is true or false", "bad-params")
        frag = self.fragment({}, params, mode="formatting")
        ch = Changes(pkg)
        settings_edit = None
        if track is not None:
            member = settings_member(pkg)
            if member is None:
                raise JobException("this file has no settings part (word/settings.xml) — save it from Word once and run "
                                   "again", "no-part", {"what": "settings"})
            root = pkg.xml(member)
            els = [c for c in root if isinstance(c.tag, str) and c.tag == W + "trackRevisions"]
            if len(els) > 1:
                raise JobException(f"{member} holds {len(els)} trackRevisions elements", "malformed-properties",
                                   {"part": member})
            el = els[0] if els else None
            if track == "off":
                if tracking_locked(root):
                    raise JobException("Track Changes is locked in this file (Review → Track Changes → Lock Tracking, "
                                       "with a password): unlock it in Word, then run again", "protected",
                                       {"part": member})
                if _on(el):
                    settings_edit = {"member": member, "op": "off"}
                else:
                    frag.notes.append("track changes is already off; unchanged")
            else:
                if el is None:
                    settings_edit = {"member": member, "op": "insert"}
                elif not _on(el):
                    settings_edit = {"member": member, "op": "turn_on"}
                else:
                    frag.notes.append("track changes is already on; unchanged")
            if settings_edit:
                frag.intended_parts.append(member)
                frag.touches.append((member, "trackRevisions", "value"))
        if final is True:
            ch.set_element("core", PROPERTIES["status"][1], FINAL_STATUS, "status")
            ch.set_custom(MARK_AS_FINAL, "true", VT + "bool")
        elif final is False:
            core = ch.members.get("core")
            if core is not None:
                el = _one(pkg.xml(core), PROPERTIES["status"][1], core)
                if el is not None and element_text(el) == FINAL_STATUS:
                    ch.clear_element("core", PROPERTIES["status"][1], "status")
            ch.clear_custom(MARK_AS_FINAL)
        td = ch.finish(frag)
        td["custom_member"] = ch.custom.member
        td["settings"] = settings_edit
        frag.transform_data = td
        frag.geometry_reference = "source"
        frag.property_assertions.append({"kind": "document_state", "track_changes": track, "final": final,
                                         "changed": sorted(ch.changed_keys
                                                           + ([f"{settings_edit['member']}|{W}trackRevisions"]
                                                              if settings_edit else []))})
        said = []
        if track is not None:
            said.append(f"track changes {track}" + ("" if settings_edit else " (already)"))
        if final is not None:
            said.append("marked as final" if final else "no longer marked as final")
        frag.notes.append("set_document_state: " + "; ".join(said)
                          + ("; docProps/custom.xml created" if td.get("create") else ""))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        s = td.get("settings")
        if s:
            root = pkg.xml(s["member"])
            if s["op"] == "off":
                for el in [c for c in root if isinstance(c.tag, str) and c.tag == W + "trackRevisions"]:
                    root.remove(el)
            elif s["op"] == "turn_on":
                el = next(c for c in root if isinstance(c.tag, str) and c.tag == W + "trackRevisions")
                el.attrib.pop(W + "val", None)
            else:
                new = etree.Element(W + "trackRevisions")
                rank = settings_rank(new)
                for i, c in enumerate(root):
                    if isinstance(c.tag, str) and settings_rank(c) > rank:
                        root.insert(i, new)
                        break
                else:
                    root.append(new)
            pkg.write_xml(s["member"], root)
        apply_changes(pkg, td)
