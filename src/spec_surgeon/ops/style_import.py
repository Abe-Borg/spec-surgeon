"""Shared style import — transform side (built-in batch 4, docs/BUILTIN_OPS_BATCH4_DESIGN.md §5).

Used by ``map_styles`` (item 31) and ``apply_style`` (item 30): styles named from a TEMPLATE file ("your styles") are
copied into the document with what they need — the styles they are based on, link to or hand over to, and the numbering
definition (``w:num`` + ``w:abstractNum``) a numbered style uses, with new numbering ids. The verifier re-derives all of it
with its own code (verify/styleimport.py, V10 kind ``styles_imported``); nothing there comes from here (rule 1).

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides):

* **Resolution of a style reference** (``resolve_style``): the style whose ``w:styleId`` equals the reference exactly;
  else the one whose ``w:name`` equals it casefolded (two such styles → ``style-mismatch``); else none.
* **Copied** styles = imported ∪ replaced. **Closure** from the requested template styles: a requested style is in it;
  for every COPIED closure style, its ``w:basedOn`` / ``w:link`` / ``w:next`` targets and the ``w:pStyle`` of every level
  of the numbering definition it uses (the abstractNum's ``w:lvl`` and the num's ``w:lvlOverride/w:lvl``) are in it — to a
  fixpoint. A closure style the document already holds (and that is not replaced) keeps the document's own definition and
  is not walked further (its template links do not matter: it is not copied). A reference to a style the template does not
  define is left as the template has it (nothing to copy).
* **Imported** = closure styles whose id the document lacks, in the template's style order; **replaced** (only with
  ``existing: "replace"``) = the requested styles the document holds, in the template's style order; **held** = the other
  closure styles (the document's own definitions are used).
* Refused (``style-import``): the document has no styles part; an imported style that is a default (``w:default``) in the
  template; a replaced style that is a default in the document, or whose template definition is a default (two defaults
  of one type), or whose template definition is of another type (every reference to it would point at the wrong kind); a
  copied style whose name (casefolded) another style holds after the import (a document style with another id, or another
  copy); an extension element carrying text (``strip_foreign``).
* **Copies** = the template's definitions with extension markup stripped (``apply_header_footer_template.strip_foreign``
  ↔ ``verify/template.strip_extensions``), the style's own ``w:pPr/w:numPr/w:numId/@w:val`` set to its new numId.
* **Numbering**: a copied style whose own ``w:pPr/w:numPr/w:numId`` is a whole number other than 0 brings the template's
  ``w:num`` with that id and its ``w:abstractNum``. Refused (``style-numbering``): the document has no numbering part; the
  numId is not a whole number; the template's num or abstractNum is missing; the abstractNum has ``w:numStyleLink`` or
  ``w:styleLink``; a level (``w:abstractNum/w:lvl`` or ``w:num/w:lvlOverride/w:lvl``) has ``w:lvlPicBulletId``; an
  extension element in a copy carries text. **New ids** (``allocate_ids``): through the copied styles in the template's
  style order, each template numId met for the first time gets the next numId above the document's largest (largest + 1,
  …; none → 1) and its abstractNum, met for the first time, the next abstractNumId above the document's largest (none →
  0). Copies stripped; ``w:abstractNum/@w:abstractNumId``, ``w:num/@w:numId`` and ``w:num/w:abstractNumId/@w:val`` set to
  the new ids. **Places** (``insert_numbering``): new abstractNums right after the document's last abstractNum (none:
  before its first num; none either: before ``w:numIdMacAtCleanup``, else at the end), new nums right after its last num
  (none: before ``w:numIdMacAtCleanup``, else at the end) — each group in allocation order.
* Imported styles are appended to the styles part in the template's style order; a replaced style takes its old place.

The template is an input (rule 12): read once at plan time and pinned by md5; ``apply_import`` reads it again, refuses a
changed file (``internal``) and rebuilds every copy from that fresh read with the plan's decisions; V10 re-reads it.

API (reused by apply_style)
---------------------------
``resolve_style(styles, ref, where) -> Optional[str]``
    ``styles`` is a ``{styleId: w:style}`` dict (``StyleSheet.styles``); raises ``JobException`` ``style-mismatch`` when
    two styles carry the name.
``TemplateStyles.load(path) -> TemplateStyles``
    The template read once: ``.path`` (resolved, a str), ``.data``, ``.md5``, ``.pkg``, ``.sheet`` (``StyleSheet``),
    ``.numbering`` (its numbering root or None), ``.order`` (style ids in document order). ``bad-params`` when the file is
    missing, is not a .docx, or has no styles part.
``style_type(st) -> str``
    ``w:type`` (default ``paragraph``).
``plan_import(pkg, tpl, requested, existing="keep") -> StyleImport``
    ``requested``: template style ids (already resolved — ``resolve_style(tpl.sheet.styles, ref, …)``); every one must be
    defined in the template (``bad-params`` otherwise). Raises the refusals above.
``StyleImport``
    ``.template``, ``.template_md5``, ``.requested``, ``.existing``, ``.styles_part``, ``.numbering_part``, ``.closure``,
    ``.imported``, ``.replaced``, ``.held``, ``.num_map`` / ``.abs_map`` (template id → new id, str → str), ``.flags``,
    ``.notes``; ``.changes`` (anything imported or replaced); ``.touches()`` — ``(styles, "style:<id>", "add")`` per
    imported style, ``(styles, "style:<id>", a)`` for ``a`` in ``replace, rPr, pPr, tblPr`` per replaced style,
    ``(numbering, "*", "add")`` when numbering is copied, ``(styles, "style:<id>", "verify")`` per held closure style —
    every style the document already holds that a copy names (basedOn / link / next) or an imported list level names
    (``w:pStyle``): a copy relies on it, so another op removing or changing it in the same spec (remove_unused_styles,
    a style-definition op) is a plan-time conflict, never a dangling reference or a V10 failure after apply;
    ``.intended_parts()`` — the styles part, and the
    numbering part when numbering is copied; ``.assertion()`` — the ``styles_imported`` V10 assertion (``requested``,
    ``existing``, the template and its md5, and the predictions ``imported``, ``replaced``, ``num_map``, ``abs_map``);
    ``.transform_data()`` — JSON data for ``apply_import``. An op adds all four to its fragment only when ``.changes``.
``apply_import(pkg, data) -> None``
    Writes the import into the working package (``pkg.write_xml`` on the styles and numbering parts; never a new part).
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import W, DocxPackage, REL_TYPE_BASE
from .apply_header_footer_template import strip_foreign
from .base import JobException
from .selectors import StyleSheet

NUMBERING_REL = REL_TYPE_BASE + "numbering"
STYLES_WITH_EFFECTS_REL = "http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects"
LINK_TAGS = ("basedOn", "link", "next")
TRUTHY = ("1", "true", "on")
REPLACED_ASPECTS = ("replace", "rPr", "pPr", "tblPr")


# ---- small readers ----------------------------------------------------------------------------------------------------

def style_type(st) -> str:
    return st.get(W + "type") or "paragraph"


def style_name(st) -> Optional[str]:
    n = st.find(W + "name")
    return n.get(W + "val") if n is not None else None


def is_default(st) -> bool:
    return st.get(W + "default") in TRUTHY


def resolve_style(styles: Dict[str, Any], ref: str, where: str = "") -> Optional[str]:
    """The id of the style ``ref`` names in ``styles`` (``{styleId: w:style}``): exact ``w:styleId``, else the one style
    whose ``w:name`` equals ``ref`` casefolded (two → ``style-mismatch``), else None."""
    if not isinstance(ref, str) or not ref:
        return None
    if ref in styles:
        return ref
    key = ref.casefold()
    hits = [sid for sid, st in styles.items() if (style_name(st) or "").casefold() == key]
    if len(hits) > 1:
        raise JobException(f"{where or 'the file'}: two styles are named {ref!r} ({hits}); name it by its id",
                           "style-mismatch", {"reference": ref, "styles": hits})
    return hits[0] if hits else None


def style_links(st) -> List[str]:
    """The style ids a style names through w:basedOn, w:link and w:next (in that order)."""
    out = []
    for tag in LINK_TAGS:
        e = st.find(W + tag)
        if e is not None and e.get(W + "val"):
            out.append(e.get(W + "val"))
    return out


def own_num_id(st, where: str) -> Optional[int]:
    """The numId a style's own w:pPr/w:numPr gives (None when absent or 0); ``style-numbering`` when not a whole number."""
    e = st.find(f"{W}pPr/{W}numPr/{W}numId")
    if e is None:
        return None
    v = e.get(W + "val")
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise JobException(f"{where}: its numbering id {v!r} is not a whole number", "style-numbering",
                           {"value": v})
    return n or None


def _int_attr(el, attr: str) -> Optional[int]:
    try:
        return int(el.get(W + attr))
    except (TypeError, ValueError):
        return None


def numbering_part(pkg: DocxPackage) -> Optional[str]:
    for r in pkg.rels(pkg.main_part()).values():
        if r.type == NUMBERING_REL and r.resolved and pkg.has(r.resolved):
            return r.resolved
    return None


def find_num(root, num_id: int):
    for n in (root.findall(W + "num") if root is not None else []):
        if _int_attr(n, "numId") == num_id:
            return n
    return None


def find_abstract(root, abs_id: int):
    for a in (root.findall(W + "abstractNum") if root is not None else []):
        if _int_attr(a, "abstractNumId") == abs_id:
            return a
    return None


def num_abstract_id(num) -> Optional[int]:
    e = num.find(W + "abstractNumId")
    return _int_attr(e, "val") if e is not None else None


def numbering_styles(num, abstract) -> List[str]:
    """The w:pStyle of every level of a numbering definition: the abstractNum's w:lvl, then the num's lvlOverride levels."""
    lvls = list(abstract.findall(W + "lvl")) + list(num.findall(f"{W}lvlOverride/{W}lvl"))
    out = []
    for lvl in lvls:
        e = lvl.find(W + "pStyle")
        if e is not None and e.get(W + "val"):
            out.append(e.get(W + "val"))
    return out


def check_definition(num, abstract, num_id: int) -> None:
    if abstract.find(W + "numStyleLink") is not None or abstract.find(W + "styleLink") is not None:
        raise JobException(f"the template's list {num_id} is defined through a numbering style (w:numStyleLink / "
                           f"w:styleLink); importing it is not handled", "style-numbering", {"numId": num_id})
    lvls = list(abstract.findall(W + "lvl")) + list(num.findall(f"{W}lvlOverride/{W}lvl"))
    if any(lvl.find(W + "lvlPicBulletId") is not None for lvl in lvls):
        raise JobException(f"the template's list {num_id} uses a picture bullet (w:lvlPicBulletId); importing it is not "
                           f"handled", "style-numbering", {"numId": num_id})


def _shape(el) -> Any:
    """What a style definition says, for the "defined differently" flag: w: elements and w:/r: attributes, text, in
    order — extension markup and w:rsid (revision-save ids) left out, namespace declarations ignored."""
    keep = {W[1:-1], "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            "http://www.w3.org/XML/1998/namespace"}
    attrs = tuple(sorted((k, v) for k, v in el.attrib.items() if not k.startswith("{") or k[1:].split("}")[0] in keep))
    kids = tuple(_shape(c) for c in el if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag != W + "rsid")
    return el.tag, attrs, (el.text or "").strip(), kids


def stripped(el, where: str, code: str):
    try:
        return strip_foreign(el, where)
    except JobException as e:
        raise JobException(f"{e}; not imported", code, {"where": where})


# ---- the template --------------------------------------------------------------------------------------------------

@dataclass
class TemplateStyles:
    path: str
    data: bytes
    md5: str
    pkg: DocxPackage
    sheet: StyleSheet
    numbering: Any
    order: List[str]

    @classmethod
    def load(cls, path) -> "TemplateStyles":
        if not isinstance(path, str) or not path or not Path(path).is_file():
            raise JobException(f"template file not found: {path!r}", "bad-params")
        return cls.from_bytes(Path(path).read_bytes(), str(Path(path).resolve()))

    @classmethod
    def from_bytes(cls, data: bytes, path: str) -> "TemplateStyles":
        try:
            pkg = DocxPackage(data, name=path)
            sheet = StyleSheet(pkg)
        except (zipfile.BadZipFile, KeyError, etree.XMLSyntaxError, ValueError) as e:
            raise JobException(f"the template {Path(path).name!r} cannot be read as a .docx ({type(e).__name__}: {e})",
                               "bad-params")
        if sheet.root is None:
            raise JobException(f"the template {Path(path).name!r} has no styles part", "bad-params")
        npart = numbering_part(pkg)
        order = list(dict.fromkeys(st.get(W + "styleId") for st in sheet.root.findall(W + "style")
                                   if st.get(W + "styleId")))
        return cls(path=path, data=data, md5=hashlib.md5(data).hexdigest(), pkg=pkg, sheet=sheet,
                   numbering=pkg.xml(npart) if npart else None, order=order)


# ---- the plan ------------------------------------------------------------------------------------------------------

@dataclass
class StyleImport:
    template: str
    template_md5: str
    requested: List[str]
    existing: str
    styles_part: Optional[str]
    numbering_part: Optional[str]
    closure: List[str] = field(default_factory=list)
    imported: List[str] = field(default_factory=list)
    replaced: List[str] = field(default_factory=list)
    held: List[str] = field(default_factory=list)
    num_map: Dict[str, str] = field(default_factory=dict)
    abs_map: Dict[str, str] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def changes(self) -> bool:
        return bool(self.imported or self.replaced)

    def touches(self) -> List[Tuple[str, str, str]]:
        if not self.changes:
            return []
        out = [(self.styles_part, f"style:{sid}", "add") for sid in self.imported]
        out += [(self.styles_part, f"style:{sid}", a) for sid in self.replaced for a in REPLACED_ASPECTS]
        if self.num_map:
            out.append((self.numbering_part, "*", "add"))
        out += [(self.styles_part, f"style:{sid}", "verify") for sid in self.held]
        return out

    def intended_parts(self) -> List[str]:
        if not self.changes:
            return []
        return [self.styles_part] + ([self.numbering_part] if self.num_map else [])

    def assertion(self) -> Dict[str, Any]:
        return {"kind": "styles_imported", "styles_part": self.styles_part, "numbering_part": self.numbering_part,
                "template": self.template, "template_md5": self.template_md5, "requested": list(self.requested),
                "existing": self.existing, "imported": list(self.imported), "replaced": list(self.replaced),
                "num_map": dict(self.num_map), "abs_map": dict(self.abs_map)}

    def transform_data(self) -> Dict[str, Any]:
        return {"template": self.template, "template_md5": self.template_md5, "styles_part": self.styles_part,
                "numbering_part": self.numbering_part, "imported": list(self.imported),
                "replaced": list(self.replaced), "num_map": dict(self.num_map), "abs_map": dict(self.abs_map)}


def closure_of(tsheet: StyleSheet, requested: Sequence[str], copied, tnum) -> List[str]:
    """The closure (template ids, in the order first reached): requested styles, then what every COPIED closure style
    names (basedOn/link/next) and the level styles of the numbering definition it uses."""
    out: List[str] = []
    seen = set()
    todo = list(requested)
    while todo:
        sid = todo.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        if sid not in tsheet.styles:
            continue                                    # a dangling reference in the template: nothing to copy
        out.append(sid)
        if not copied(sid):
            continue                                    # held: the document's own definition stays; not walked
        st = tsheet.styles[sid]
        todo += style_links(st)
        n = own_num_id(st, f"template style {sid}")
        if n is not None:
            num = find_num(tnum, n)
            a = find_abstract(tnum, num_abstract_id(num)) if num is not None else None
            if num is None or a is None:
                raise JobException(f"template style {sid} is numbered by list {n}, which the template's numbering does "
                                   f"not define" + (" (its abstractNum is missing)" if num is not None else ""),
                                   "style-numbering", {"style": sid, "numId": n})
            todo += numbering_styles(num, a)
    return out


def next_ids(numbering_root) -> Tuple[int, int]:
    """(the first new numId, the first new abstractNumId) — above the document's largest of each."""
    nums = [_int_attr(n, "numId") for n in numbering_root.findall(W + "num")]
    absn = [_int_attr(a, "abstractNumId") for a in numbering_root.findall(W + "abstractNum")]
    nums = [n for n in nums if n is not None]
    absn = [a for a in absn if a is not None]
    return (max(nums) if nums else 0) + 1, (max(absn) if absn else -1) + 1


def allocate_ids(tsheet: StyleSheet, tnum, copied_in_order: Sequence[str], numbering_root
                 ) -> Tuple[Dict[str, str], Dict[str, str]]:
    """(template numId → new numId, template abstractNumId → new abstractNumId), first met first."""
    next_num, next_abs = next_ids(numbering_root)
    num_map: Dict[str, str] = {}
    abs_map: Dict[str, str] = {}
    for sid in copied_in_order:
        n = own_num_id(tsheet.styles[sid], f"template style {sid}")
        if n is None or str(n) in num_map:
            continue
        num_map[str(n)] = str(next_num)
        next_num += 1
        a = num_abstract_id(find_num(tnum, n))
        if str(a) not in abs_map:
            abs_map[str(a)] = str(next_abs)
            next_abs += 1
    return num_map, abs_map


def build_copies(tpl: "TemplateStyles", imported: Sequence[str], replaced: Sequence[str], num_map: Dict[str, str],
                 abs_map: Dict[str, str]) -> Tuple[Dict[str, Any], List[Any], List[Any]]:
    """(style id → its copy, the abstractNum copies, the num copies — each list in allocation order)."""
    styles: Dict[str, Any] = {}
    for sid in list(imported) + list(replaced):
        c = stripped(tpl.sheet.styles[sid], f"template style {sid}", "style-import")
        e = c.find(f"{W}pPr/{W}numPr/{W}numId")
        if e is not None and own_num_id(c, sid) is not None:
            e.set(W + "val", num_map[str(own_num_id(c, sid))])
        styles[sid] = c
    by_new_abs = sorted(abs_map.items(), key=lambda kv: int(kv[1]))
    abstracts = []
    for old, new in by_new_abs:
        a = stripped(find_abstract(tpl.numbering, int(old)), f"the template's abstractNum {old}", "style-numbering")
        a.set(W + "abstractNumId", new)
        abstracts.append(a)
    nums = []
    for old, new in sorted(num_map.items(), key=lambda kv: int(kv[1])):
        n = stripped(find_num(tpl.numbering, int(old)), f"the template's num {old}", "style-numbering")
        n.set(W + "numId", new)
        n.find(W + "abstractNumId").set(W + "val", abs_map[str(num_abstract_id(find_num(tpl.numbering, int(old))))])
        nums.append(n)
    return styles, abstracts, nums


def insert_numbering(root, abstracts: Sequence[Any], nums: Sequence[Any]) -> None:
    """New abstractNums after the last abstractNum (else before the first num, else before numIdMacAtCleanup, else at
    the end); new nums after the last num (else before numIdMacAtCleanup, else at the end)."""
    def place(after_tag: str, before_tags: Sequence[str], els: Sequence[Any]) -> None:
        if not els:
            return
        kids = list(root)
        last = [i for i, c in enumerate(kids) if c.tag == W + after_tag]
        if last:
            at = last[-1] + 1
        else:
            at = next((i for i, c in enumerate(kids) if c.tag in {W + t for t in before_tags}), len(kids))
        for k, el in enumerate(els):
            root.insert(at + k, el)
    place("abstractNum", ("num", "numIdMacAtCleanup"), abstracts)
    place("num", ("numIdMacAtCleanup",), nums)


def plan_import(pkg: DocxPackage, tpl: TemplateStyles, requested: Sequence[str], existing: str = "keep") -> StyleImport:
    """The import of ``requested`` (template style ids) into ``pkg``: closure, imported/replaced/held, numbering ids,
    flags — every refusal raised here, predicted from the SOURCE and the template."""
    if existing not in ("keep", "replace"):
        raise JobException("existing must be 'keep' or 'replace'", "bad-params")
    requested = list(dict.fromkeys(requested))
    missing = [sid for sid in requested if sid not in tpl.sheet.styles]
    if missing:
        raise JobException(f"the template has no style {missing}", "bad-params", {"styles": missing})
    sheet = StyleSheet(pkg)
    npart = numbering_part(pkg)
    imp = StyleImport(template=tpl.path, template_md5=tpl.md5, requested=requested, existing=existing,
                      styles_part=sheet.part, numbering_part=npart)
    if not requested:
        return imp
    if sheet.part is None:
        raise JobException("this file has no styles part to import into (a part is never created)", "style-import")
    req = set(requested)

    def copied(sid: str) -> bool:
        return sid not in sheet.styles or (existing == "replace" and sid in req)
    closure = closure_of(tpl.sheet, requested, copied, tpl.numbering)
    inside = set(closure)
    imp.closure = closure
    imp.imported = [sid for sid in tpl.order if sid in inside and sid not in sheet.styles]
    imp.replaced = [sid for sid in tpl.order if sid in inside and sid in sheet.styles and existing == "replace"
                    and sid in req]
    imp.held = [sid for sid in closure if not copied(sid)]
    copied_order = [sid for sid in tpl.order if sid in set(imp.imported) | set(imp.replaced)]

    # ---- refusals (style-import) ----
    for sid in imp.imported:
        if is_default(tpl.sheet.styles[sid]):
            raise JobException(f"template style {sid} is a default style ({style_type(tpl.sheet.styles[sid])}) that "
                               f"this file lacks; a second default is never imported", "style-import", {"style": sid})
    for sid in imp.replaced:
        mine, theirs = sheet.styles[sid], tpl.sheet.styles[sid]
        if is_default(mine) or is_default(theirs):
            raise JobException(f"style {sid} is a default style {'in this file' if is_default(mine) else 'in the template'}; "
                               f"replacing it is refused (it would change every paragraph without a style, or make two "
                               f"defaults)", "style-import", {"style": sid})
        if style_type(mine) != style_type(theirs):
            raise JobException(f"style {sid} is a {style_type(mine)} style here and a {style_type(theirs)} style in the "
                               f"template; replacing it would leave every reference to it pointing at the wrong kind",
                               "style-import", {"style": sid})
    copies = {sid: stripped(tpl.sheet.styles[sid], f"template style {sid}", "style-import") for sid in copied_order}
    names: Dict[str, List[str]] = {}
    for sid, st in sheet.styles.items():
        if sid not in copies and style_name(st):
            names.setdefault(style_name(st).casefold(), []).append(sid)
    for sid, c in copies.items():
        if style_name(c):
            names.setdefault(style_name(c).casefold(), []).append(sid)
    for sid, c in copies.items():
        n = style_name(c)
        others = [o for o in names.get((n or "").casefold(), []) if o != sid] if n else []
        if others:
            raise JobException(f"template style {sid} is named {n!r}, a name style {others[0]!r} holds after the "
                               f"import; Word finds styles by name — rename one of them first", "style-import",
                               {"style": sid, "name": n, "other": others[0]})

    # ---- numbering ----
    numbered = [sid for sid in copied_order if own_num_id(tpl.sheet.styles[sid], f"template style {sid}") is not None]
    if numbered:
        if npart is None:
            raise JobException(f"style(s) {numbered} are numbered and this file has no numbering part to import the "
                               f"list into (a part is never created)", "style-numbering", {"styles": numbered})
        for sid in numbered:                            # closure_of found num and abstractNum for every copy
            n = own_num_id(tpl.sheet.styles[sid], sid)
            num = find_num(tpl.numbering, n)
            check_definition(num, find_abstract(tpl.numbering, num_abstract_id(num)), n)
        nroot = pkg.xml(npart)
        imp.num_map, imp.abs_map = allocate_ids(tpl.sheet, tpl.numbering, copied_order, nroot)
        _, abstracts, _ = build_copies(tpl, imp.imported, imp.replaced, imp.num_map, imp.abs_map)
        doc_nsids = {e.get(W + "val") for e in nroot.iter(W + "nsid") if e.get(W + "val")}
        clash = sorted({a.find(W + "nsid").get(W + "val") for a in abstracts
                        if a.find(W + "nsid") is not None and a.find(W + "nsid").get(W + "val") in doc_nsids})
        if clash:
            imp.flags.append(f"an imported list definition carries the identifier (w:nsid) {clash} of one of this "
                             f"file's own lists; Word may continue that list's numbering into it — check the numbers")

    # ---- flags: what the copies lean on ----
    copied_set = set(copies)
    flagged = set()
    for sid in copied_order:
        chain, seen = [], {sid}
        b = tpl.sheet.styles[sid].find(W + "basedOn")
        while b is not None and b.get(W + "val") and b.get(W + "val") not in seen:
            aid = b.get(W + "val")
            seen.add(aid)
            if aid in copied_set:
                b = tpl.sheet.styles[aid].find(W + "basedOn") if aid in tpl.sheet.styles else None
                continue
            if aid in sheet.styles and aid in tpl.sheet.styles and aid not in flagged:
                if _shape(sheet.styles[aid]) != _shape(tpl.sheet.styles[aid]):
                    flagged.add(aid)
                    imp.flags.append(f"{'imported' if sid in imp.imported else 'replaced'} style {sid} is based on {aid}, "
                                     f"which this file defines differently from the template: {sid} renders on this "
                                     f"file's own {aid}")
            break
    sweff = any(r.type == STYLES_WITH_EFFECTS_REL for r in pkg.rels(pkg.main_part()).values()) or \
        pkg.has("word/stylesWithEffects.xml")
    if imp.changes and sweff:
        imp.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not updated; "
                         "Word 2013+ ignores it.")
    imp.notes.append(f"styles from template {Path(tpl.path).name} (md5 {tpl.md5[:12]}…): imported "
                     f"{imp.imported or 'none'}; replaced {imp.replaced or 'none'}"
                     + (f"; kept this file's own {imp.held}" if imp.held else "")
                     + (f"; lists: num {', '.join(f'{k}→{v}' for k, v in imp.num_map.items())}, abstractNum "
                        f"{', '.join(f'{k}→{v}' for k, v in imp.abs_map.items())}" if imp.num_map else ""))
    return imp


def apply_import(pkg: DocxPackage, data: Optional[Dict[str, Any]]) -> None:
    """Write a planned import into the working package; the template is read again and must be the planned one."""
    if not data or not (data.get("imported") or data.get("replaced")):
        return
    raw = Path(data["template"]).read_bytes()
    if hashlib.md5(raw).hexdigest() != data["template_md5"]:
        raise JobException("the template file changed between plan and apply", "internal")
    tpl = TemplateStyles.from_bytes(raw, data["template"])
    styles, abstracts, nums = build_copies(tpl, data["imported"], data["replaced"], data["num_map"], data["abs_map"])
    sroot = pkg.xml(data["styles_part"])
    by_id = {st.get(W + "styleId"): st for st in sroot.findall(W + "style")}
    for sid in data["replaced"]:
        old = by_id[sid]
        old.getparent().replace(old, styles[sid])
    for sid in data["imported"]:
        sroot.append(styles[sid])
    pkg.write_xml(data["styles_part"], sroot)
    if abstracts or nums:
        nroot = pkg.xml(data["numbering_part"])
        insert_numbering(nroot, abstracts, nums)
        pkg.write_xml(data["numbering_part"], nroot)
