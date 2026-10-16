"""V10 kind ``removed_comments`` — built-in batch 2, verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §2), independent
of ops/comments.py (rule 1): this module's own reading of the comments, their threads, their resolved state and their
marks, and its own derivation of what removing them leaves.

* The selection, re-derived from the SOURCE: comments (the main part's ``comments`` relationship), their paraIds
  (``w14:paraId`` of their paragraphs), each comment's entry (the first ``w15:commentEx`` of the commentsExtended part
  whose paraId is one of its paraIds), replies (``w15:paraIdParent``), the root (parents followed, cycle-safe),
  resolved (the root's ``w15:done`` on); ``all`` / ``resolved`` / ``authors``; then every reply below a selected
  comment. It must equal the plan's ``expected_ids``.
* The comments part: the source with exactly those ``w:comment`` gone, compared whole (``verified_parts``).
* Each side part (commentsExtended, commentsIds, commentsExtensible): the source with exactly the entries of the removed
  comments gone, compared whole (``verified_members``).
* Every other text part: each paragraph holding a mark of a removed comment must be the source paragraph without those
  marks, item for item (``verified_blocks``); a mark outside the paragraphs is an expected removal (``expected_removed``)
  — the residual then proves nothing else changed. A mark in a text box is the op's refusal.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Set, Tuple

from .reader import VPackage, W
from .props import Ctx, element_path
from .edits import part_revision_ids, v_items
from .markup import check_paragraph, drop, tree_difference

_V_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_V_COMMENTS = _V_BASE + "comments"
_V_EXTENDED = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
_V_IDS = "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds"
_V_EXTENSIBLE = "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible"
_V_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
_V_W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
_V_CID = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
_V_CEX = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
_V_ON = {"1", "true", "on"}
_V_MARKS = (W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference")


def _v_who(s: Optional[str]) -> str:
    return " ".join((s or "").split()).casefold()


def _v_par(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


class _VComments:
    """The comments of one package, read by this module."""

    def __init__(self, pkg: VPackage):
        self.pkg = pkg
        self.part = pkg.related(_V_COMMENTS)
        self.root = pkg.xml(self.part) if self.part else None
        self.order: List[str] = []
        self.author: Dict[str, str] = {}
        self.pids: Dict[str, List[str]] = {}
        self.owner: Dict[str, str] = {}
        for c in (self.root.findall(W + "comment") if self.root is not None else []):
            cid = c.get(W + "id")
            self.order.append(cid)
            self.author[cid] = c.get(W + "author") or ""
            self.pids[cid] = [p.get(_V_W14 + "paraId") for p in c.iter(W + "p") if p.get(_V_W14 + "paraId")]
            for pid in self.pids[cid]:
                self.owner.setdefault(pid, cid)
        self.ext = pkg.related(_V_EXTENDED)
        self.entry: Dict[str, Any] = {}
        if self.ext:
            for ex in pkg.xml(self.ext).findall(_V_W15 + "commentEx"):
                cid = self.owner.get(ex.get(_V_W15 + "paraId"))
                if cid is not None and cid not in self.entry:
                    self.entry[cid] = ex

    def problems(self) -> List[str]:
        out = []
        if None in self.order or len(set(self.order)) != len(self.order):
            out.append("two comments share an id (or one has none)")
        flat = [pid for cid in self.order for pid in self.pids.get(cid, [])]
        if len(set(flat)) != len(flat):
            out.append("two comments share a paragraph id")
        return out

    def parent(self, cid: str) -> Optional[str]:
        ex = self.entry.get(cid)
        pid = ex.get(_V_W15 + "paraIdParent") if ex is not None else None
        up = self.owner.get(pid) if pid else None
        return up if up is not None and up != cid else None

    def root_of(self, cid: str) -> str:
        seen = {cid}
        while True:
            up = self.parent(cid)
            if up is None or up in seen:
                return cid
            seen.add(up)
            cid = up

    def chosen(self, which: str, authors: List[str]) -> List[str]:
        if which == "all":
            keep = set(self.order)
        elif which == "resolved":
            keep = set()
            for cid in self.order:
                ex = self.entry.get(self.root_of(cid))
                if ex is not None and (ex.get(_V_W15 + "done") or "").strip().lower() in _V_ON:
                    keep.add(cid)
        elif which == "authors":
            names = {_v_who(a) for a in authors}
            keep = {cid for cid in self.order if _v_who(self.author[cid]) in names}
        else:
            raise ValueError(f"unknown 'which' {which!r}")
        changed = True
        while changed:
            changed = False
            for cid in self.order:
                up = self.parent(cid)
                if cid not in keep and up is not None and up in keep:
                    keep.add(cid)
                    changed = True
        return [cid for cid in self.order if cid in keep]


def _v_side_expected(pkg: VPackage, book: _VComments, ids: List[str]) -> Dict[str, Any]:
    """{side part: the source tree without the removed comments' entries} for every side part the package has."""
    pids: Set[str] = {pid for cid in ids for pid in book.pids.get(cid, [])}
    out: Dict[str, Any] = {}
    durable: Set[str] = set()
    for rel, tag, attr in ((_V_EXTENDED, _V_W15 + "commentEx", _V_W15 + "paraId"),
                           (_V_IDS, _V_CID + "commentId", _V_CID + "paraId")):
        member = pkg.related(rel)
        if not member:
            continue
        root = copy.deepcopy(pkg.xml(member))
        for e in list(root):
            if e.tag == tag and e.get(attr) in pids:
                if tag == _V_CID + "commentId" and e.get(_V_CID + "durableId"):
                    durable.add(e.get(_V_CID + "durableId"))
                root.remove(e)
        out[member] = root
    member = pkg.related(_V_EXTENSIBLE)
    if member:
        root = copy.deepcopy(pkg.xml(member))
        for e in list(root):
            if e.tag == _V_CEX + "commentExtensible" and e.get(_V_CEX + "durableId") in durable:
                root.remove(e)
        out[member] = root
    return out


def _removed_comments(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    which, authors = a.get("asked_which") or "all", list(a.get("asked_authors") or [])
    book = _VComments(ctx.src)
    for prob in book.problems():
        errs.append(f"removed_comments: {prob} — the op must refuse")
    if book.problems():
        return "removed_comments: comments unreadable"
    ids = book.chosen(which, authors)
    if ids != list(a.get("expected_ids") or []):
        errs.append(f"removed_comments: comments {ids} are selected here, the plan removes {a.get('expected_ids')}")
    gone = set(ids)
    # the comments part, whole
    if book.part:
        if book.part not in ctx.out.names:
            errs.append(f"removed_comments: {book.part} is missing from the output (parts are never removed)")
        else:
            expected = copy.deepcopy(book.root)
            for c in list(expected.findall(W + "comment")):
                if c.get(W + "id") in gone:
                    expected.remove(c)
            d = tree_difference(expected, ctx.out.xml(book.part))
            if d:
                errs.append(f"removed_comments: {book.part} is not the source without exactly those comments: {d}")
            ctx.verified_parts.add(book.part)
    # the side parts, whole
    for member, expected in _v_side_expected(ctx.src, book, ids).items():
        if member not in ctx.out.names:
            errs.append(f"removed_comments: {member} is missing from the output")
            continue
        d = tree_difference(expected, ctx.out.xml(member))
        if d:
            errs.append(f"removed_comments: {member} is not the source without exactly the removed comments' "
                        f"entries: {d}")
        ctx.verified_members.add(member)
    # the marks in every other text part
    anchors: Dict[str, List[int]] = {}
    outside: Dict[str, List[str]] = {}
    for part in ctx.src_tp:
        if part == book.part or not gone:
            continue
        sv, ov = ctx.view(part)
        marks = [m for m in sv.root.iter(*_V_MARKS) if m.get(W + "id") in gone]
        if not marks:
            continue
        index = {id(p): i for i, p in enumerate(sv.pars)}
        per_block: Dict[int, List[Any]] = {}
        for m in marks:
            p = _v_par(m)
            if (p is not None and _v_par(p) is not None) or any(x.tag == W + "txbxContent" for x in m.iterancestors()):
                errs.append(f"removed_comments: {part}: the mark of comment {m.get(W + 'id')} is in a text box — the "
                            f"op must refuse")
                continue
            if p is None:
                outside.setdefault(part, []).append(m.get(W + "id"))
                ctx.expected_removed.setdefault(part, []).append(element_path(m))
                continue
            per_block.setdefault(index[id(p)], []).append(m)
        if part not in ctx.both:
            errs.append(f"removed_comments: {part} is missing from the output")
            continue
        if len(ov.pars) != len(sv.pars):
            errs.append(f"removed_comments: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)}")
            continue
        old_ids = part_revision_ids(sv.root)
        for bi, ms in sorted(per_block.items()):
            anchors.setdefault(part, []).append(bi)
            expected = drop(v_items(sv.pars[bi]), elements=ms)
            check_paragraph(sv.pars[bi], ov.pars[bi], expected, old_ids, f"removed_comments {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
    want_anchors = {k: list(v) for k, v in (a.get("expected_anchor_blocks") or {}).items()}
    if anchors != want_anchors:
        errs.append(f"removed_comments: marks in paragraphs {anchors} here, the plan predicted {want_anchors}")
    want_outside = {k: list(v) for k, v in (a.get("expected_outside_marks") or {}).items()}
    if outside != want_outside:
        errs.append(f"removed_comments: marks outside paragraphs {outside} here, the plan predicted {want_outside}")
    return (f"removed_comments ({which}): {len(ids)} comment(s) and their marks "
            f"({sum(len(v) for v in anchors.values())} paragraph(s), {sum(len(v) for v in outside.values())} mark(s) "
            f"between blocks)")


COMMENT_KINDS: Dict[str, Any] = {"removed_comments": _removed_comments}
