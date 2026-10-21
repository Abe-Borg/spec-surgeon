"""V10 kind ``deleted_paragraphs`` (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §1) and the deletion both batch-6
deletion kinds check with — verifier side, independent of ops/deletion.py (rule 1): this module's own reading of the
chosen blocks, the kept paragraphs, the tracked deletion and everything an untracked removal takes with it. The rules
are ops/deletion.py's docstring, read again here:

* **Chosen**: the selected paragraphs (never a separator note's); a table every paragraph of which is selected is wholly
  selected, the outermost such tables are deleted; a selected paragraph in or holding a text box: the op must refuse.
* **Kept** (per container, over its p/tbl/sdt/customXml/altChunk children): the first chosen paragraph between a table
  that stays and the next table that stays when only chosen blocks lie between; tracked, a chosen last block that is a
  paragraph; when the last block is chosen and what stays is nothing or ends in a non-paragraph, the last chosen
  paragraph not kept yet; in the body, when every block after the last section-break paragraph that stays is chosen
  and none of them kept, the last chosen paragraph there (the last section keeps one).
* **Comment threads**: a deleted paragraph that ends a comment whose paragraph ids a commentsExtended or commentsIds
  entry names, while the comment itself stays: the op must refuse (``comment-last-paragraph``).
* **Tracked**: the runs (directly in the paragraph or in hyperlinks, smart tags, run-level content controls, custom XML,
  dir, bdo) of each deleted or kept paragraph, each group of consecutive siblings in one NEW ``w:del``, text and field code
  as deleted text; a deleted paragraph's mark with a NEW ``w:del``, a kept one's without; each row of a deleted table
  with exactly one NEW ``w:trPr/w:del``. Each changed paragraph is compared whole with that expectation
  (``verified_blocks``), each row's ``w:trPr`` checked here and stripped of its ``w:del`` for the residual
  (``strip_tr``), which proves the rest.
* **Untracked** (``_VRemoval``): the deleted blocks leave, a kept paragraph keeps its ``w:pPr`` alone; then, in this order,
  the notes all of whose references are removed, the section breaks and the header/footer and printer-settings parts
  no relationship left targets, the comments all of whose marks are removed (with their replies; a crossing one: refused), the range marks
  (all removed: gone; some: each removed one takes the place of the outermost removed element it was in), the fields
  (a crossing one: refused) and the relationships no remaining use names (image, media, hyperlink: gone; header/footer as
  above; anything else: refused), media nothing targets any more. Every changed part's expected tree is built here and
  compared whole (``verified_parts``), the comment side parts likewise (``verified_members``), each changed relationships
  member and ``[Content_Types].xml`` as the source with exactly those entries gone.

A NEW revision is a ``w:ins``/``w:del`` whose ``w:id`` no source text part uses, by the plan's author, with a ``w:date``
(and ``w16du:dateUtc`` when the source's revisions carry one).
"""
from __future__ import annotations

import copy
import posixpath
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import REL, R, VPackage, W
from .props import Ctx, PartView, para_matches, resolve_path, element_path
from .edits import part_revision_ids, V_REVISION_TAGS
from .fields import v_fields
from .comments import _V_CID, _V_EXTENDED, _V_IDS, _V_W14, _V_W15, _VComments, _v_side_expected
from .markup import check_content_types, check_relationships, tree_difference

_V_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_V_HEADER, _V_FOOTER = _V_BASE + "header", _V_BASE + "footer"
_V_HYPERLINK = _V_BASE + "hyperlink"
_V_MEDIA = {_V_BASE + t for t in ("image", "audio", "video", "media")} | {
    "http://schemas.microsoft.com/office/2007/relationships/hdphoto",
    "http://schemas.microsoft.com/office/2007/relationships/media"}
_V_DATEUTC = "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"
_V_BLOCKS = {W + "p", W + "tbl", W + "sdt", W + "customXml", W + "altChunk"}
_V_SEPARATORS = {"separator", "continuationSeparator", "continuationNotice"}
_V_NOTES = (("footnotes", W + "footnote", W + "footnoteReference"), ("endnotes", W + "endnote", W + "endnoteReference"))
_V_ENTRIES = {W + "footnote", W + "endnote", W + "comment"}
_V_RUN_HOLDERS = {W + "hyperlink", W + "smartTag", W + "customXml", W + "dir", W + "bdo", W + "sdt", W + "sdtContent"}
_V_HOLDER_PROPS = {W + "smartTagPr", W + "customXmlPr", W + "sdtPr", W + "sdtEndPr"}
_V_INERT = {W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr", W + "commentRangeStart", W + "commentRangeEnd",
            W + "permStart", W + "permEnd"}
_V_COMMENT_MARKS = (W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference")
# paired range marks, by family: a mark's pair is the marks of its family with its w:id
_V_RANGES = {W + family + end: family for family in ("bookmark", "perm") for end in ("Start", "End")}
_V_RANGES.update({W + family + "Range" + end: family for family in
                  ("moveFrom", "moveTo", "customXmlIns", "customXmlDel", "customXmlMoveFrom", "customXmlMoveTo")
                  for end in ("Start", "End")})
NEW = "NEW"


class VRefuse(Exception):
    """The op should have refused this file (the code it should have used)."""

    def __init__(self, code: str, why: str):
        super().__init__(f"{why} ({code})")
        self.code = code


def v_par(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def v_separator(p) -> bool:
    for a in p.iterancestors(W + "footnote", W + "endnote"):
        return (a.get(W + "type") or "normal") in _V_SEPARATORS
    return False


def _v_blocks(c) -> List[Any]:
    return [x for x in c if isinstance(x.tag, str) and x.tag in _V_BLOCKS]


def _v_keep(blocks: Sequence[Any], chosen: Set[Any], tracked: bool) -> List[Any]:
    keep: List[Any] = []
    n = len(blocks)
    for i in range(n):
        if blocks[i].tag != W + "tbl" or blocks[i] in chosen:
            continue
        j = i + 1
        while j < n and blocks[j] in chosen:
            j += 1
        if j < n and j > i + 1 and blocks[j].tag == W + "tbl":
            between = [x for x in blocks[i + 1:j] if x.tag == W + "p"]
            if not between:
                raise VRefuse("tables-join", "two tables would touch and no paragraph between them can stay")
            if all(x is not between[0] for x in keep):
                keep.append(between[0])
    if n and blocks[-1] in chosen:
        if tracked and blocks[-1].tag == W + "p" and all(x is not blocks[-1] for x in keep):
            keep.append(blocks[-1])
        stays = lambda: [b for b in blocks if b not in chosen or any(b is k for k in keep)]
        if not stays() or stays()[-1].tag != W + "p":
            cand = [x for x in blocks if x in chosen and x.tag == W + "p" and all(x is not k for k in keep)]
            if not cand:
                raise VRefuse("structure", "a container would end without a paragraph")
            keep.append(cand[-1])
            if stays()[-1].tag != W + "p":
                raise VRefuse("structure", "a container would end without a paragraph")
    return keep


def _v_last_section(blocks: Sequence[Any], chosen: Set[Any], keep: List[Any]) -> None:
    """The body's last section — what follows the last section-break paragraph that stays — keeps a paragraph: when
    all of it is chosen and nothing of it is kept, its last chosen paragraph is kept too (appended to ``keep``)."""
    end = None
    for i, b in enumerate(blocks):
        carries = b.tag == W + "p" and b.find(W + "pPr/" + W + "sectPr") is not None
        if carries and (b not in chosen or any(b is k for k in keep)):
            end = i
    if end is None or end == len(blocks) - 1:
        return
    section = blocks[end + 1:]
    if not all(b in chosen for b in section) or any(b is k for b in section for k in keep):
        return
    pars = [b for b in section if b.tag == W + "p"]
    if not pars:
        raise VRefuse("structure", "the last section would be emptied and holds no paragraph that can stay")
    keep.append(pars[-1])


def v_choose(root, selected: Sequence[Any], tracked: bool) -> Dict[str, List[Any]]:
    """{"tables", "deleted", "kept", "in_tables"} for the selected paragraphs of ``root`` (document order)."""
    for p in selected:
        if v_par(p) is not None or any(q is not p for q in p.iter(W + "p")):
            raise VRefuse("textbox", "a selected paragraph is in, or holds, a text box")
    sel = set(selected)
    full = [t for t in root.iter(W + "tbl") if list(t.iter(W + "p")) and all(q in sel for q in t.iter(W + "p"))]
    fs = set(full)
    tables = [t for t in full if not any(a in fs for a in t.iterancestors(W + "tbl"))]
    inside = set()
    for t in tables:
        inside.update(t.iter(W + "p"))
    chosen = set(tables) | {p for p in selected if p not in inside}
    groups: List[Any] = []
    for el in [p for p in selected if p not in inside] + tables:
        c = el.getparent()
        if c is not None and all(c is not g for g in groups):
            groups.append(c)
    kept: Set[Any] = set()
    for c in groups:
        blocks = _v_blocks(c)
        keep = _v_keep(blocks, chosen, tracked)
        if c.tag == W + "body":
            _v_last_section(blocks, chosen, keep)
        kept.update(keep)
    pars = list(root.iter(W + "p"))
    return {"tables": tables, "kept": [p for p in pars if p in kept],
            "deleted": [p for p in pars if p in chosen and p not in kept],
            "in_tables": [p for p in pars if p in inside]}


def v_thread_ends(src: VPackage, root) -> Dict[Any, str]:
    """{the last paragraph of each w:comment of ``root`` (a comments part) one of whose paragraph ids a thread entry
    names — a commentsExtended entry's paraId or paraIdParent, a commentsIds entry's paraId: the comment's id}. Word
    names a comment's thread by its last paragraph: it must not go while the comment stays."""
    if root.tag != W + "comments":
        return {}
    named: Set[str] = set()
    for rel, tag, attrs in ((_V_EXTENDED, _V_W15 + "commentEx", (_V_W15 + "paraId", _V_W15 + "paraIdParent")),
                            (_V_IDS, _V_CID + "commentId", (_V_CID + "paraId",))):
        member = src.related(rel)
        if member:
            for e in src.xml(member).iter(tag):
                named.update(v for v in (e.get(a) for a in attrs) if v)
    ends: Dict[Any, str] = {}
    for c in root:
        if c.tag != W + "comment":
            continue
        ps = list(c.iter(W + "p"))
        if ps and named & {q.get(_V_W14 + "paraId") for q in ps}:
            ends[ps[-1]] = c.get(W + "id")
    return ends


def v_refuse_thread_ends(src: VPackage, root, gone_paragraphs: Iterable[Any], comments_gone: Iterable[str]) -> None:
    ends, gone = v_thread_ends(src, root), set(comments_gone)
    for p in gone_paragraphs:
        if p in ends and ends[p] not in gone:
            raise VRefuse("comment-last-paragraph", f"the last paragraph of comment {ends[p]}, which its thread entry "
                                                    f"names, goes while the comment stays")


def v_tracked_runs(p) -> List[Any]:
    """The runs a tracked deletion of ``p`` deletes (document order), or VRefuse."""
    out: List[Any] = []

    def walk(el):
        for c in el:
            if not isinstance(c.tag, str):
                continue
            if el is p and c.tag == W + "pPr":
                continue
            if c.tag == W + "r":
                out.append(c)
            elif c.tag in _V_INERT or (el is not p and c.tag in _V_HOLDER_PROPS):
                continue
            elif c.tag in _V_RUN_HOLDERS:
                walk(c)
            elif c.tag == W + "fldSimple":
                raise VRefuse("field-result", "a simple field cannot be deleted as a tracked change")
            else:
                raise VRefuse("structure", f"w:{etree.QName(c).localname} cannot be deleted as a tracked change")
    walk(p)
    return out


def v_groups(runs: Sequence[Any]) -> List[List[Any]]:
    """Consecutive sibling runs (no node between them) among ``runs``, in order."""
    rs = set(runs)
    out: List[List[Any]] = []
    for r in runs:
        prev = r.getprevious()
        if out and prev is not None and prev in rs and prev is out[-1][-1]:
            out[-1].append(r)
        else:
            out.append([r])
    return out


def v_field_cut(root, gone) -> None:
    """VRefuse("field-crossing") for a complex field of ``root`` some of whose runs are ``gone`` and some not."""
    runs = list(root.iter(W + "r"))
    at = {r: k for k, r in enumerate(runs)}
    for f in v_fields(root):
        if f.simple is not None or f.begin is None:
            continue
        b = at.get(f.begin.getparent())
        if b is None:
            continue
        e = at.get(f.end.getparent()) if f.end is not None else None
        if e is None or e < b:
            if gone(runs[b]):
                raise VRefuse("field-crossing", "a field that never ends begins in what goes")
            continue
        if len({bool(gone(r)) for r in runs[b:e + 1]}) > 1:
            raise VRefuse("field-crossing", f"a field ({f.type or 'field'}) is only partly deleted")


def v_uses_date_utc(src: VPackage) -> bool:
    return any(el.get(_V_DATEUTC) is not None for part in src.text_parts()
               for el in src.xml(part).iter(W + "ins", W + "del"))


def v_old_ids(src: VPackage) -> Set[str]:
    out: Set[str] = set()
    for part in src.text_parts():
        out |= part_revision_ids(src.xml(part))
    return out


def v_normalize(el, author: Optional[str], old_ids: Set[str]) -> Any:
    """A copy of ``el`` with each NEW w:ins/w:del's id and dates written NEW (the rest as it is)."""
    c = copy.deepcopy(el)
    for x in c.iter(W + "ins", W + "del"):
        if x.get(W + "id") not in old_ids and x.get(W + "id") is not None and x.get(W + "author") == author \
                and x.get(W + "date"):
            x.set(W + "id", NEW)
            x.set(W + "date", NEW)
            if x.get(_V_DATEUTC) is not None:
                x.set(_V_DATEUTC, NEW)
    return c


def v_new_del(author: Optional[str], utc: bool):
    d = etree.Element(W + "del")
    d.set(W + "id", NEW)
    d.set(W + "author", author or "")
    d.set(W + "date", NEW)
    if utc:
        d.set(_V_DATEUTC, NEW)
    return d


def _v_as_deleted(r) -> None:
    for t in r.findall(W + "t"):
        t.tag = W + "delText"
        if t.text and t.text != t.text.strip():
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for it in r.findall(W + "instrText"):
        it.tag = W + "delInstrText"


def check_tracked_paragraph(src_p, out_p, runs: Sequence[Any], mark: bool, author: Optional[str], utc: bool,
                            old_ids: Set[str], where: str, errs: List[str], own_wrappers: bool = False) -> None:
    """``out_p`` must be ``src_p`` with each group of ``runs`` (``v_groups``; each run alone with ``own_wrappers``) in a
    NEW w:del, their text deleted, and the mark carrying a NEW w:del exactly when ``mark`` — nothing else."""
    expected = copy.deepcopy(src_p)
    paths = [element_path_from(src_p, r) for r in runs]
    copies = [resolve_path(expected, pth) for pth in paths]
    at = {id(r): c for r, c in zip(runs, copies)}
    groups = [[r] for r in runs] if own_wrappers else v_groups(runs)
    for g in groups:
        first = at[id(g[0])]
        d = v_new_del(author, utc)
        first.addprevious(d)
        for r in g:
            c = at[id(r)]
            _v_as_deleted(c)
            d.append(c)
    got = v_normalize(out_p, author, old_ids)
    ppr = got.find(W + "pPr")
    marks = [x for x in (ppr.find(W + "rPr") if ppr is not None and ppr.find(W + "rPr") is not None else [])
             if x.tag == W + "del" and x.get(W + "id") == NEW]
    if mark and (len(marks) != 1 or len(marks[0])):
        errs.append(f"{where}: the paragraph mark carries {len(marks)} new w:del by {author!r}, expected one (empty)")
    if mark and marks:
        rpr = marks[0].getparent()
        rpr.remove(marks[0])
        src_ppr = src_p.find(W + "pPr")
        if not len(rpr) and not rpr.attrib and (src_ppr is None or src_ppr.find(W + "rPr") is None):
            ppr.remove(rpr)
        if not len(ppr) and not ppr.attrib and src_ppr is None:
            got.remove(ppr)
    d = tree_difference(expected, got)
    if d:
        errs.append(f"{where}: not exactly the tracked deletion — {d}")


def element_path_from(top, el) -> Tuple[int, ...]:
    """``el``'s child-index path below ``top``."""
    out = []
    while el is not top:
        q = el.getparent()
        out.append(q.index(el))
        el = q
    return tuple(reversed(out))


def v_rows(root, tables: Sequence[Any]) -> List[Tuple[int, int]]:
    """(table ordinal, row index among its own rows) of every row of ``tables``; a wrapped row: VRefuse."""
    ordinal = {t: k for k, t in enumerate(root.iter(W + "tbl"))}
    out = []
    for t in tables:
        for tr in t.iter(W + "tr"):
            if tr.getparent() is None or tr.getparent().tag != W + "tbl":
                raise VRefuse("table-structure", "a row to delete is wrapped in a content control or custom XML")
            own = [c for c in tr.getparent() if c.tag == W + "tr"]
            out.append((ordinal[tr.getparent()], next(i for i, x in enumerate(own) if x is tr)))
    return out


def v_part_rels(src: VPackage, part: str) -> Dict[str, Tuple[str, Optional[str]]]:
    """{Id: (Type, resolved internal target or None)} of every relationship of ``part`` (external ones too)."""
    d, b = posixpath.split(part)
    rp = posixpath.join(d, "_rels", b + ".rels")
    if rp not in src.names:
        return {}
    out = {}
    for rel in src.xml(rp).findall(REL + "Relationship"):
        target = None
        if rel.get("TargetMode", "Internal") == "Internal":
            t = rel.get("Target") or ""
            target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(d, t))
        out[rel.get("Id")] = (rel.get("Type"), target)
    return out


def v_rels_member(part: str) -> str:
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")


def _v_all_rels(src: VPackage) -> List[Tuple[str, str, str, Optional[str]]]:
    """(owner part, Id, Type, resolved internal target or None) of every relationship of every rels member whose owner
    exists (the package's own relationships: owner "")."""
    names = set(src.names)
    out = []
    for rp in src.names:
        d, b = posixpath.split(rp)
        if not b.endswith(".rels") or posixpath.basename(d) != "_rels":
            continue
        owner = "" if b == ".rels" else posixpath.join(posixpath.dirname(d), b[:-5])
        if owner and owner not in names:
            continue
        base = posixpath.dirname(owner)
        for rel in src.xml(rp).findall(REL + "Relationship"):
            target = None
            if rel.get("TargetMode", "Internal") == "Internal":
                t = rel.get("Target") or ""
                target = t.lstrip("/") if t.startswith("/") else posixpath.normpath(posixpath.join(base, t))
            out.append((owner, rel.get("Id"), rel.get("Type"), target))
    return out


def _v_uses(els: Iterable[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for el in els:
        for x in el.iter():
            if isinstance(x.tag, str):
                for k, v in x.attrib.items():
                    if k.startswith(R):
                        out[v] = out.get(v, 0) + 1
    return out


def _v_sections(root) -> List[Any]:
    body = root.find(W + "body")
    out = [p.find(W + "pPr/" + W + "sectPr") for p in body.iter(W + "p") if p.find(W + "pPr/" + W + "sectPr") is not None]
    if body.find(W + "sectPr") is not None:
        out.append(body.find(W + "sectPr"))
    return out


class VRemoval:
    """An untracked removal over the SOURCE package, as this module reads it: the elements that leave (``drop``), then
    what they take with them (``settle``), then each changed part's expected tree (``expected``)."""

    def __init__(self, src: VPackage):
        self.src = src
        self.main = src.main_part()
        self.roots: Dict[str, Any] = {}
        self.tops: Dict[str, Set[Any]] = {}
        self.parts_gone: List[str] = []
        self.printers: List[str] = []
        self.notes: Dict[str, List[str]] = {}
        self.sections: List[int] = []
        self.comments: List[str] = []
        self.book: Optional[_VComments] = None
        self.moved: Dict[str, Dict[Any, List[Any]]] = {}
        self.rels: Dict[str, List[str]] = {}
        self.media: List[str] = []
        self.members: List[str] = []

    def root(self, part: str):
        if part not in self.roots:
            self.roots[part] = self.src.xml(part)
        return self.roots[part]

    def drop(self, part: str, el) -> None:
        if self.gone(part, el):
            return
        tops = self.tops.setdefault(part, set())
        for d in list(el.iter()):
            tops.discard(d)
        tops.add(el)

    def gone(self, part: str, el) -> bool:
        if part in self.parts_gone:
            return True
        tops = self.tops.get(part) or set()
        return el in tops or any(a in tops for a in el.iterancestors())

    def notes_part(self, kind: str) -> Optional[str]:
        found = self.src.part_of_kind(kind)
        return found[0] if found else None

    def settle(self) -> None:
        notes_parts = [x for x in (self.notes_part(k) for k, _, _ in _V_NOTES) if x]
        texts = self.src.text_parts()
        # 1. notes all of whose references go
        for kind, entry_tag, ref_tag in _V_NOTES:
            part = self.notes_part(kind)
            if part is None:
                continue
            refs: Dict[str, List[bool]] = {}
            for tp in texts:
                if tp not in notes_parts:
                    for ref in self.root(tp).iter(ref_tag):
                        refs.setdefault(ref.get(W + "id"), []).append(self.gone(tp, ref))
            seen: Set[str] = set()
            for e in [x for x in self.root(part) if x.tag == entry_tag]:
                g = refs.get(e.get(W + "id"))
                if not g or not all(g):
                    continue
                if e.get(W + "id") in seen:
                    raise VRefuse("structure", "two notes share an id")
                seen.add(e.get(W + "id"))
                self.notes.setdefault(part, []).append(e.get(W + "id"))
                self.drop(part, e)
        # 2. section breaks and the header/footer parts nothing targets any more
        if self.tops.get(self.main):
            root = self.root(self.main)
            secs = _v_sections(root)
            self.sections = [i for i, sp in enumerate(secs) if self.gone(self.main, sp)]
            rels = v_part_rels(self.src, self.main)
            before, removed = _v_uses([root]), _v_uses(self.tops[self.main])
            # the parts a removed sectPr names: headers, footers and its printer settings (a binary part: it only leaves)
            printer: Set[str] = set()
            hf: Set[str] = set()
            for i in self.sections:
                for ref in secs[i]:
                    rid = ref.get(R + "id")
                    if ref.tag not in (W + "headerReference", W + "footerReference", W + "printerSettings") \
                            or rid not in rels or removed.get(rid) != before.get(rid):
                        continue
                    hf.add(rid)
                    if ref.tag == W + "printerSettings":
                        printer.add(rid)
            hf = sorted(hf)
            if hf:
                self.rels.setdefault(self.main, []).extend(hf)
            still = {t for owner, rid, _, t in _v_all_rels(self.src) if t and not (owner == self.main and rid in hf)}
            for rid in hf:
                target = rels[rid][1]
                if target and target in self.src.names and target not in still and target not in self.parts_gone \
                        and target not in self.printers:
                    for typ, tg in v_part_rels(self.src, target).values():
                        if typ not in _V_MEDIA and not (typ == _V_HYPERLINK and tg is None):
                            raise VRefuse("structure", f"{target} has a relationship other than images and links")
                    (self.printers if rid in printer else self.parts_gone).append(target)
        # 3. comments all of whose marks go, with their replies
        book = _VComments(self.src)
        if book.part and book.order and book.problems():
            if any(self.gone(tp, m) for tp in texts if tp != book.part for m in self.root(tp).iter(*_V_COMMENT_MARKS)):
                raise VRefuse("structure", "; ".join(book.problems()))
        elif book.part and book.order:
            marks: Dict[str, List[bool]] = {}
            for tp in texts:
                if tp == book.part:
                    continue
                for m in self.root(tp).iter(*_V_COMMENT_MARKS):
                    if m.get(W + "id") in book.author:
                        marks.setdefault(m.get(W + "id"), []).append(self.gone(tp, m))
            bad = [cid for cid, g in marks.items() if any(g) and not all(g)]
            take = {cid for cid, g in marks.items() if g and all(g)}
            grew = True
            while grew:
                grew = False
                for cid in book.order:
                    up = book.parent(cid)
                    if cid not in take and up is not None and up in take:
                        take.add(cid)
                        grew = True
                        if marks.get(cid) and not all(marks[cid]):
                            bad.append(cid)
            if bad:
                raise VRefuse("comment-crossing", f"comment {sorted(bad)[0]} is only partly in what goes")
            if take:
                self.book = book
                self.comments = [cid for cid in book.order if cid in take]
                croot = self.root(book.part)
                for c in [x for x in croot if x.tag == W + "comment"]:
                    if c.get(W + "id") in take:
                        self.drop(book.part, c)
        # 4–5. range marks and fields of every part something leaves
        for part in [p for p, t in self.tops.items() if t and p not in self.parts_gone]:
            root = self.root(part)
            pairs: Dict[Tuple[str, str], List[Any]] = {}
            for el in root.iter():
                if isinstance(el.tag, str) and el.tag in _V_RANGES:
                    pairs.setdefault((_V_RANGES[el.tag], el.get(W + "id") or ""), []).append(el)
            tops = self.tops[part]
            for (fam, _), els in pairs.items():
                flags = [self.gone(part, e) for e in els]
                if all(flags) or not any(flags):
                    continue
                for e, f in zip(els, flags):
                    if not f:
                        continue
                    top = e if e in tops else next(a for a in e.iterancestors() if a in tops)
                    if top.tag in _V_ENTRIES:
                        raise VRefuse("structure", f"a {fam} runs out of a note or comment that goes")
                    self.moved.setdefault(part, {}).setdefault(top, []).append(e)
            v_field_cut(root, lambda r, part=part: self.gone(part, r))
        # 6. relationships no remaining use names, and the media nothing targets any more
        for part in [p for p, t in self.tops.items() if t and p not in self.parts_gone]:
            before, removed = _v_uses([self.root(part)]), _v_uses(self.tops[part])
            rels = v_part_rels(self.src, part)
            for rid in sorted(removed):
                if removed[rid] != before.get(rid) or rid not in rels or rid in self.rels.get(part, []):
                    continue
                typ = rels[rid][0]
                if part == self.main and typ in (_V_HEADER, _V_FOOTER):
                    continue
                if typ not in _V_MEDIA and typ != _V_HYPERLINK:
                    raise VRefuse("embedded-object", f"{part}: a {typ.rsplit('/', 1)[-1]} goes with the content")
                self.rels.setdefault(part, []).append(rid)
        gone_rels = {(owner, rid) for owner, rids in self.rels.items() for rid in rids}
        everything = _v_all_rels(self.src)
        candidates = {t for owner, rid, typ, t in everything
                      if t and typ in _V_MEDIA and (owner in self.parts_gone or (owner, rid) in gone_rels)}
        remaining = {t for owner, rid, typ, t in everything
                     if t and owner not in self.parts_gone and (owner, rid) not in gone_rels}
        self.media = sorted(m for m in candidates if m in self.src.names and m not in remaining)
        whole = set(self.parts_gone) | set(self.printers)
        members = whole | {v_rels_member(p) for p in whole if v_rels_member(p) in self.src.names} | set(self.media)
        self.members = [n for n in self.src.names if n in members]

    def summary(self) -> Dict[str, Any]:
        return {"notes": {p: list(v) for p, v in self.notes.items()}, "sections": list(self.sections),
                "parts": list(self.parts_gone), "comments": list(self.comments),
                "rels": {p: sorted(v) for p, v in self.rels.items()}, "media": list(self.media),
                "members": list(self.members)}

    def expected(self, part: str):
        """The part's expected tree: the source without what leaves, each moved range mark in its outermost removed
        element's place."""
        root = self.root(part)
        order = {el: k for k, el in enumerate(root.iter())}
        tops = sorted(self.tops.get(part, set()), key=lambda el: order[el])
        moved = self.moved.get(part, {})
        exp = copy.deepcopy(root)
        plan = [(resolve_path(exp, element_path(t)),
                 [resolve_path(exp, element_path(m)) for m in sorted(moved.get(t, []), key=lambda el: order[el])])
                for t in tops]
        for top, marks in plan:
            parent = top.getparent()
            at = parent.index(top)
            for k, m in enumerate(marks):
                m.tail = None
                parent.insert(at + k, m)
            parent.remove(top)
        return exp

    def check(self, ctx: Ctx, kind: str, planned: Dict[str, Any], errs: List[str]) -> None:
        """The derivation against the plan's, then every changed member of the output against this one's."""
        mine = self.summary()
        if mine != (planned or {}):
            errs.append(f"{kind}: the removal takes {mine} here, the plan predicted {planned}")
        for m in self.members:
            if m not in (ctx.plan.get("members_removed") or []):
                errs.append(f"{kind}: {m} leaves the package here, the plan does not remove it")
        for part, tops in self.tops.items():
            if not tops or part in self.parts_gone:
                continue
            if part not in ctx.out.names:
                errs.append(f"{kind}: {part} is missing from the output")
                continue
            d = tree_difference(self.expected(part), ctx.out.xml(part))
            if d:
                errs.append(f"{kind}: {part} is not the source without exactly what the deletion removes: {d}")
            ctx.verified_parts.add(part)
        if self.book is not None:
            for member, expected in _v_side_expected(ctx.src, self.book, self.comments).items():
                if member not in ctx.out.names:
                    errs.append(f"{kind}: {member} is missing from the output")
                    continue
                d = tree_difference(expected, ctx.out.xml(member))
                if d:
                    errs.append(f"{kind}: {member} is not the source without exactly the removed comments' entries: {d}")
                ctx.verified_members.add(member)
        for owner, rids in self.rels.items():
            check_relationships(ctx.src, ctx.out, v_rels_member(owner), rids, [], kind, errs)
        _, overrides = ctx.src.content_types()
        gone_overrides = [m for m in self.members if m in overrides]
        if gone_overrides:
            check_content_types(ctx.src, ctx.out, gone_overrides, {}, kind, errs)


def _v_tracked(a: Dict[str, Any], kind: str) -> bool:
    t = a.get("asked_tracked")
    if t is None:
        return True
    if not isinstance(t, bool):
        raise ValueError(f"{kind}: 'tracked' {t!r} is not true or false — the op must refuse")
    return t


def _v_scope(a: Dict[str, Any]) -> List[str]:
    s = a.get("scope") or ["document"]
    return [s] if isinstance(s, str) else list(s)


def _deleted_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    tracked = _v_tracked(a, "deleted_paragraphs")
    pred = (a.get("select") or {}).get("paragraphs")
    if pred in (None, {}):
        errs.append("deleted_paragraphs: no paragraph selector (the op must refuse)")
        return "deleted_paragraphs: no selector"
    author = a.get("author")
    rm = None if tracked else VRemoval(ctx.src)
    derived: Dict[str, Dict[str, Any]] = {}
    found: Dict[str, Tuple[Any, Dict[str, List[Any]]]] = {}
    n = 0
    try:
        for part in ctx.scope_parts(_v_scope(a)):
            if rm is not None:
                root = rm.root(part)
                view = PartView(root)
            elif part in ctx.both:
                view = ctx.view(part)[0]
                root = view.root
            else:
                errs.append(f"deleted_paragraphs: {part} missing from the output")
                continue
            picked = [p for bi, p in enumerate(view.pars) if not v_separator(p)
                      and para_matches(pred, p, bi, view, ctx.src_styles)]
            if not picked:
                continue
            n += len(picked)
            dec = v_choose(root, picked, tracked)
            at = {p: i for i, p in enumerate(view.pars)}
            tbl_at = {t: k for k, t in enumerate(root.iter(W + "tbl"))}
            derived[part] = {"delete": [at[p] for p in dec["deleted"]], "keep": [at[p] for p in dec["kept"]],
                             "tables": [tbl_at[t] for t in dec["tables"]]}
            found[part] = (root, dec)
            if tracked:
                derived[part]["rows"] = [list(x) for x in v_rows(root, dec["tables"])]
                v_refuse_thread_ends(ctx.src, root, dec["deleted"] + dec["in_tables"], ())
    except VRefuse as e:
        errs.append(f"deleted_paragraphs: {e} — the op must refuse")
        return "deleted_paragraphs: the op must refuse"
    if n != a.get("expected_paragraphs", -1):
        errs.append(f"deleted_paragraphs: {n} paragraph(s) selected here, plan predicted {a.get('expected_paragraphs')}")
    if derived != (a.get("expected") or {}):
        errs.append(f"deleted_paragraphs: deletes {derived} here, the plan predicted {a.get('expected')}")
        return "deleted_paragraphs: decisions differ"
    if tracked:
        _check_tracked(ctx, found, author, errs)
    else:
        try:
            for part, (root, dec) in found.items():
                for el in dec["tables"] + dec["deleted"]:
                    rm.drop(part, el)
                for p in dec["kept"]:
                    for c in p:
                        if c.tag != W + "pPr":
                            rm.drop(part, c)
            rm.settle()
            for part, (root, dec) in found.items():
                v_refuse_thread_ends(ctx.src, root, dec["deleted"] + dec["in_tables"], rm.comments)
        except VRefuse as e:
            errs.append(f"deleted_paragraphs: {e} — the op must refuse")
            return "deleted_paragraphs: the op must refuse"
        rm.check(ctx, "deleted_paragraphs", a.get("expected_removal"), errs)
    tables = sum(len(d["tables"]) for d in derived.values())
    kept = sum(len(d["keep"]) for d in derived.values())
    return (f"deleted_paragraphs: {n} paragraph(s) selected, {tables} whole table(s), {kept} kept emptied "
            f"({'tracked' if tracked else 'untracked'})")


def _check_tracked(ctx: Ctx, found, author: Optional[str], errs: List[str]) -> None:
    old_ids = v_old_ids(ctx.src)
    utc = v_uses_date_utc(ctx.src)
    for part, (root, dec) in found.items():
        if part not in ctx.both:
            errs.append(f"deleted_paragraphs: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        marked = set(dec["deleted"]) | set(dec["in_tables"])
        changed = [p for p in sv.pars if p in marked or p in set(dec["kept"])]
        at = {p: i for i, p in enumerate(sv.pars)}
        runs_of = {}
        try:
            for t in dec["tables"]:
                if any(isinstance(x.tag, str) and x.tag in V_REVISION_TAGS for x in t.iter()):
                    raise VRefuse("existing-revisions", "a table to delete carries pending revisions")
            for p in changed:
                if any(isinstance(x.tag, str) and x.tag in V_REVISION_TAGS for x in p.iter()):
                    raise VRefuse("existing-revisions", f"paragraph {at[p]} carries pending revisions")
                runs_of[p] = v_tracked_runs(p)
            gone = {r for rs in runs_of.values() for r in rs}
            v_field_cut(root, lambda r: r in gone)
        except VRefuse as e:
            errs.append(f"deleted_paragraphs: {part}: {e} — the op must refuse")
            continue
        for p in changed:
            bi = at[p]
            oi = ctx.maps[part].get(bi)
            if oi is None or oi >= len(ov.pars):
                errs.append(f"deleted_paragraphs: {part} block {bi} has no counterpart in the output")
                continue
            check_tracked_paragraph(p, ov.pars[oi], runs_of[p], p in marked, author, utc, old_ids,
                                    f"deleted_paragraphs {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = oi
        check_tracked_rows(ctx, part, v_rows(root, dec["tables"]), author, old_ids, "deleted_paragraphs", errs)


def check_tracked_rows(ctx: Ctx, part: str, rows: Sequence[Tuple[int, int]], author: Optional[str],
                       old_ids: Set[str], kind: str, errs: List[str]) -> None:
    """Each row's output w:trPr holds exactly one w:del, a NEW one by ``author``; the residual compares the rest of the
    row (``strip_tr``: the w:del out of both sides)."""
    if not rows:
        return
    out_tables = list(ctx.out.xml(part).iter(W + "tbl"))
    for k, r in rows:
        own = [c for c in out_tables[k] if c.tag == W + "tr"] if k < len(out_tables) else []
        tr = own[r] if r < len(own) else None
        trpr = tr.find(W + "trPr") if tr is not None else None
        dels = trpr.findall(W + "del") if trpr is not None else []
        if len(dels) != 1 or dels[0].get(W + "id") in old_ids or dels[0].get(W + "id") is None \
                or dels[0].get(W + "author") != author or not dels[0].get(W + "date") or len(dels[0]):
            errs.append(f"{kind}: {part} table {k} row {r}: w:trPr holds {len(dels)} w:del, expected one new "
                        f"deletion by {author!r}")
        ctx.strip_tr.setdefault(part, {}).setdefault((k, r), set()).add("del")


DELETION_KINDS: Dict[str, Any] = {"deleted_paragraphs": _deleted_paragraphs}
