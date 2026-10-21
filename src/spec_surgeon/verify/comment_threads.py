"""V10 kinds ``comment_replies`` and ``resolved_comments`` — built-in batch 6, verifier side
(docs/BUILTIN_OPS_BATCH6_DESIGN.md §9), independent of ops/comment_threads.py (rule 1): this module's own choice of the
threads, its own allocation of the new ids and its own expected comments part, side parts and paragraphs.

* **Comments and threads**: verify/comments.py's reading (a comment's paraIds, its entry — the first ``w15:commentEx``
  whose paraId is one of them —, its parent by ``w15:paraIdParent``, its root by the parents, cycle-safe). A thread is a
  top comment and every comment whose root it is; its state is its top comment's entry's ``w15:done`` (1/true/on, any
  case; no entry or no attribute: open).
* **The choice** (``which``, the user's words): ``all`` (default) every comment; ``authors`` the comments whose author,
  casefolded with whitespace collapsed, is listed; ``text_regex`` the comments whose text — each paragraph's own
  ``w:t`` text, paragraphs joined by a line break — the pattern finds. The threads chosen are the roots of the chosen
  comments, in the order of the comments part, each once: a reply to a reply is a reply to its thread, as Word writes
  threads (one level).
* **New ids**: a comment id is 1 + the largest ``w:id`` of a comment or comment mark in any text part (0 for none), one
  more per reply; a paragraph or durable id is the smallest value from 1, below 0x80000000, that no ``w14:paraId``,
  ``w15:paraId``, ``w15:paraIdParent``, ``w16cid:paraId``, ``w16cid:durableId`` or ``w16cex:durableId`` of the text
  parts and side parts holds, as eight upper-case hex digits — per thread, in order: the top comment's last paragraph's
  paraId when it has none, the reply's paraId, its durableId when commentsIds is there.
* **A reply**: ``<w:comment w:id w:author [w:initials]><w:p w14:paraId><w:r><w:t xml:space="preserve">`` the user's
  words, appended to the comments part; in commentsExtended (created next to the main part when missing — the first
  free name of commentsExtended.xml, commentsExtended1.xml …, with a relationship from the main part and its content
  type) the top comment's ``w15:commentEx`` (paraId of its last paragraph, done 0) when it has none, then the reply's
  (its paraId, ``w15:paraIdParent`` = the top comment's last paraId, done 0); in commentsIds (when there — the top
  comment must have an entry there) ``w16cid:commentId`` (paraId, durableId); in commentsExtensible (when there, never
  without commentsIds) ``w16cex:commentExtensible`` (durableId). In the one text part holding the thread's marks: a
  ``w:commentRangeStart`` after the thread's last one (when the top comment has one; when that one stands between
  paragraphs, before every item of the first paragraph after it, which must not be in a text box), and
  after the run of the thread's last ``w:commentReference`` a ``w:commentRangeEnd`` (with the start) and a new run with
  that run's properties (without ``w:rPrChange``) holding the reply's reference.
* **Resolve / reopen**: a thread not as asked gets ``w15:done`` ``1`` / ``0`` on its top comment's entry — resolving a
  thread with no entry appends one (paraId of its last paragraph, added when missing; the part created when missing);
  a thread already as asked is left alone; the replies' entries never change.
"""
from __future__ import annotations

import copy
import posixpath
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from .comments import _VComments, _v_par, _v_who
from .edits import Item, part_revision_ids, v_items
from .markup import check_content_types, check_paragraph, check_relationships, tree_difference
from .props import Ctx, sig
from .reader import REL, VPackage, W

_V_EXTENDED = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
_V_IDS = "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds"
_V_EXTENSIBLE = "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible"
_V_EXTENDED_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"
_V_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
_V_W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
_V_CID = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
_V_CEX = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
_V_HEX = {_V_W14 + "paraId", _V_W15 + "paraId", _V_W15 + "paraIdParent", _V_CID + "paraId", _V_CID + "durableId",
          _V_CEX + "durableId"}
_V_ON = {"1", "true", "on"}
_V_START, _V_END, _V_REF = W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference"
_V_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


class _VBook(_VComments):
    """The comments of the SOURCE as this module reads them: verify/comments.py's threads, plus each comment's
    element, text and last paragraph, and the side parts."""

    def __init__(self, pkg: VPackage):
        super().__init__(pkg)
        self.els = {c.get(W + "id"): c for c in self.root.findall(W + "comment")} if self.root is not None else {}
        self.ids_part, self.cex_part = pkg.related(_V_IDS), pkg.related(_V_EXTENSIBLE)
        for member, tag in ((self.ext, _V_W15 + "commentsEx"), (self.ids_part, _V_CID + "commentsIds"),
                            (self.cex_part, _V_CEX + "commentsExtensible")):
            if member and pkg.xml(member).tag != tag:
                raise ValueError(f"{member} is not a {etree.QName(tag).localname} part — the op must refuse")
        if self.cex_part and not self.ids_part:
            raise ValueError("commentsExtensible without commentsIds — the op must refuse")

    def text(self, cid: str) -> str:
        return "\n".join("".join(t.text or "" for t in p.iter(W + "t") if _v_par(t) is p)
                         for p in self.els[cid].iter(W + "p"))

    def last_par(self, cid: str):
        pars = list(self.els[cid].iter(W + "p"))
        if not pars:
            raise ValueError(f"comment {cid} holds no paragraph — the op must refuse")
        return pars[-1]

    def done(self, cid: str) -> bool:
        ex = self.entry.get(cid)
        return ex is not None and (ex.get(_V_W15 + "done") or "").strip().lower() in _V_ON


def _v_threads(book: _VBook, params: Dict[str, Any]) -> List[str]:
    """The top comments of the threads the user's ``which`` chooses, in the comments part's order."""
    which = params.get("which", "all")
    authors, pattern = params.get("authors"), params.get("text_regex")
    if which not in ("all", "authors", "text_regex"):
        raise ValueError(f"which {which!r}")
    if (which == "authors") != (authors is not None) or (which == "text_regex") != (pattern is not None):
        raise ValueError("'authors' goes with which=authors, 'text_regex' with which=text_regex")
    if which == "all":
        chosen = list(book.order)
    elif which == "authors":
        if not isinstance(authors, list) or not authors or not all(isinstance(a, str) and a.strip() for a in authors):
            raise ValueError("authors must be names")
        names = {_v_who(a) for a in authors}
        chosen = [cid for cid in book.order if _v_who(book.author[cid]) in names]
    else:
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("text_regex must be a pattern")
        rx = re.compile(pattern)
        chosen = [cid for cid in book.order if rx.search(book.text(cid))]
    tops = {book.root_of(cid) for cid in chosen}
    return [cid for cid in book.order if cid in tops]


def _v_side_parts(book: _VBook) -> List[str]:
    return [m for m in (book.ext, book.ids_part, book.cex_part) if m]


class _VIds:
    """The comment ids and hex ids the source already uses, and the next free ones (module docstring)."""

    def __init__(self, ctx: Ctx, book: _VBook):
        self.top = 0
        for part in ctx.src_tp:
            for el in ctx.src.xml(part).iter(W + "comment", _V_START, _V_END, _V_REF):
                raw = el.get(W + "id", "")
                if not raw.isascii() or not raw.isdecimal():
                    raise ValueError(f"{part}: a comment id {raw!r} is not a number — the op must refuse")
                self.top = max(self.top, int(raw))
        self.used: Set[int] = set()
        for member in list(ctx.src_tp) + _v_side_parts(book):
            for el in ctx.src.xml(member).iter():
                for attr in _V_HEX & set(el.attrib):
                    try:
                        self.used.add(int(el.get(attr), 16))
                    except ValueError:
                        raise ValueError(f"{member}: {el.get(attr)!r} is not a paragraph or durable id — the op must "
                                         f"refuse")

    def comment(self) -> str:
        self.top += 1
        return str(self.top)

    def hex(self) -> str:
        n = 1
        while n in self.used:
            n += 1
        if n >= 0x80000000:
            raise ValueError("no paragraph id left below 0x80000000")
        self.used.add(n)
        return f"{n:08X}"


def _v_new_member(pkg: VPackage) -> Tuple[str, str]:
    """(member, relationship target) of a commentsExtended part to create: the first free name next to the main part."""
    main = pkg.main_part()
    taken = set(pkg.names) | set(pkg.content_types()[1]) | {t for _, _, _, t in pkg.all_relationships() if t}
    n = 0
    while True:
        name = posixpath.join(posixpath.dirname(main), f"commentsExtended{n or ''}.xml")
        if name not in taken:
            return name, posixpath.relpath(name, posixpath.dirname(main) or ".")
        n += 1


def _v_rels_member(part: str) -> str:
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")


def _v_check_created(ctx: Ctx, member: str, target: str, tree, kind: str, errs: List[str]) -> None:
    """The created commentsExtended part: its tree, the main part's new relationship, its content type."""
    if member not in ctx.out.names:
        errs.append(f"{kind}: the commentsExtended part {member} was not created")
        return
    d = tree_difference(tree, ctx.out.xml(member))
    if d:
        errs.append(f"{kind}: {member} is not the expected new part: {d}")
    rels = _v_rels_member(ctx.src.main_part())
    check_relationships(ctx.src, ctx.out, rels, [], [{"Type": _V_EXTENDED, "Target": target}], kind, errs)
    added = {} if ctx.src.content_types()[0].get("xml") == _V_EXTENDED_CT else {member: _V_EXTENDED_CT}
    check_content_types(ctx.src, ctx.out, [], added, kind, errs)
    ctx.verified_members.update({member, rels, "[Content_Types].xml"})


def _v_last_copy(comments, cid: str):
    """The last paragraph of comment ``cid`` in a copy of the comments part."""
    c = next(c for c in comments.findall(W + "comment") if c.get(W + "id") == cid)
    return list(c.iter(W + "p"))[-1]


def _v_ex_root():
    return etree.Element(_V_W15 + "commentsEx", nsmap={"w15": _V_W15[1:-1]})


def _v_entry(parent, pid: str, done: str, up: Optional[str] = None) -> None:
    e = etree.SubElement(parent, _V_W15 + "commentEx")
    e.set(_V_W15 + "paraId", pid)
    if up is not None:
        e.set(_V_W15 + "paraIdParent", up)
    e.set(_V_W15 + "done", done)


def _v_mark_items(ctx: Ctx, book: _VBook, tops: List[str], new_ids: Dict[str, str],
                  errs: List[str]) -> Dict[Tuple[str, int], List[Item]]:
    """{(part, block): its expected items} — the SOURCE paragraph's items with each reply's marks where they go (module
    docstring); refusals as errors."""
    items_of: Dict[Tuple[str, int], List[Item]] = {}          # each paragraph's items, read once (their ids are keys)
    after: Dict[Tuple[str, int], Dict[int, List[Item]]] = {}
    first: Dict[Tuple[str, int], List[Item]] = {}              # new items before every other item of the paragraph
    for top in tops:
        members = {cid for cid in book.order if book.root_of(cid) == top}
        where = f"comment_replies thread {top}"
        found = []
        for part in ctx.src_tp:
            if part == book.part:
                continue
            if any(m.get(W + "id") in members for m in ctx.src.xml(part).iter(_V_START, _V_REF)):
                found.append(part)
        if len(found) != 1:
            errs.append(f"{where}: its marks are in {found or 'no part'} — the op must refuse")
            continue
        part = found[0]
        sv, _ = ctx.view(part)
        held = list(sv.root.iter())                        # held while the map below names them
        order = {id(el): k for k, el in enumerate(held)}
        refs = [m for m in sv.root.iter(_V_REF) if m.get(W + "id") in members]
        starts = [m for m in sv.root.iter(_V_START) if m.get(W + "id") in members]
        if sum(1 for m in refs if m.get(W + "id") == top) != 1:
            errs.append(f"{where}: the top comment has no single reference mark — the op must refuse")
            continue
        ref = max(refs, key=lambda m: order[id(m)])
        start = max(starts, key=lambda m: order[id(m)]) if any(m.get(W + "id") == top for m in starts) else None
        marks = [m for m in (start, ref) if m is not None]
        if any(_v_par(m) is not None and _v_par(_v_par(m)) is not None for m in marks) or any(
                x.tag == W + "txbxContent" for m in marks for x in m.iterancestors()):
            errs.append(f"{where}: a mark is in a text box — the op must refuse")
            continue
        if ref.getparent() is None or ref.getparent().tag != W + "r":
            errs.append(f"{where}: the reference mark is not in a run — the op must refuse")
            continue
        if start is not None and order[id(start)] > order[id(ref)]:
            errs.append(f"{where}: the range starts after its reference — the op must refuse")
            continue
        index = {id(p): i for i, p in enumerate(sv.pars)}
        cid = new_ids[top]
        if start is not None:
            el = etree.Element(_V_START)
            el.set(W + "id", cid)
            holder = _v_par(start)
            if holder is None:                              # between paragraphs: before the next paragraph's items
                holder = next((p for p in sv.pars if order[id(p)] > order[id(start)]), None)
                if holder is None or _v_par(holder) is not None:
                    errs.append(f"{where}: no body paragraph follows the range's start — the op must refuse")
                    continue
                bi = index[id(holder)]
                items_of.setdefault((part, bi), v_items(holder))
                first.setdefault((part, bi), []).append(
                    Item("p", sig(el), (None, (), ()), None, el.tag, None, el, False, ()))
            else:
                bi = index[id(holder)]
                items = items_of.setdefault((part, bi), v_items(holder))
                anchor = next((it for it in items if it.el is start), None)
                if anchor is None:
                    errs.append(f"{where}: the range start is not where the item model reads it")
                    continue
                after.setdefault((part, bi), {})[id(anchor)] = [
                    Item("p", sig(el), (None, (), anchor.ctx[2]), None, el.tag, None, el, False, anchor.chain_els)]
        run = ref.getparent()
        bi = index[id(_v_par(ref))]
        items = items_of.setdefault((part, bi), v_items(sv.pars[bi]))
        last = [it for it in items if it.run is run][-1]
        new: List[Item] = []
        if start is not None:
            el = etree.Element(_V_END)
            el.set(W + "id", cid)
            new.append(Item("p", sig(el), (None, (), last.ctx[2]), None, el.tag, None, el, False, last.chain_els))
        rpr = copy.deepcopy(run.find(W + "rPr"))
        if rpr is not None:
            for c in rpr.findall(W + "rPrChange"):
                rpr.remove(c)
        el = etree.Element(_V_REF)
        el.set(W + "id", cid)
        new.append(Item("x", sig(el), (sig(rpr) if rpr is not None and (len(rpr) or rpr.attrib) else None, (),
                                       last.ctx[2]), None, el.tag, None, el, last.live, last.chain_els))
        after.setdefault((part, bi), {}).setdefault(id(last), []).extend(new)
    out: Dict[Tuple[str, int], List[Item]] = {}
    for key, items in items_of.items():
        out[key] = list(first.get(key, []))
        for it in items:
            out[key].append(it)
            out[key] += after.get(key, {}).get(id(it), [])
    return out


def _comment_replies(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    params = dict(a.get("params") or {})
    text, author, initials = params.get("text"), params.get("author"), params.get("initials")
    for key, value in (("text", text), ("author", author)) + ((("initials", initials),) if "initials" in params else ()):
        if not isinstance(value, str) or not value.strip() or any(
                ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value):
            raise ValueError(f"{key} must be one line of text")
    book = _VBook(ctx.src)
    for prob in book.problems():
        errs.append(f"comment_replies: {prob} — the op must refuse")
    if book.problems():
        return "comment_replies: comments unreadable"
    tops = _v_threads(book, params)
    if tops != list(a.get("expected_threads") or []):
        errs.append(f"comment_replies: threads {tops} are chosen here, the plan replies to {a.get('expected_threads')}")
    if not tops:
        return "comment_replies: no thread chosen"
    ids = _VIds(ctx, book)
    comments = copy.deepcopy(book.root)
    ex = copy.deepcopy(ctx.src.xml(book.ext)) if book.ext else _v_ex_root()
    cid_root = copy.deepcopy(ctx.src.xml(book.ids_part)) if book.ids_part else None
    cex_root = copy.deepcopy(ctx.src.xml(book.cex_part)) if book.cex_part else None
    new_ids: Dict[str, str] = {}
    for top in tops:
        last = book.last_par(top)
        up = last.get(_V_W14 + "paraId")
        if up is None:
            up = ids.hex()
            _v_last_copy(comments, top).set(_V_W14 + "paraId", up)
        if cid_root is not None and not any(e.get(_V_CID + "paraId") in book.pids[top]
                                            for e in ctx.src.xml(book.ids_part).iter(_V_CID + "commentId")):
            errs.append(f"comment_replies: comment {top} has no entry in {book.ids_part} — the op must refuse")
        cid, pid = ids.comment(), ids.hex()
        new_ids[top] = cid
        c = etree.SubElement(comments, W + "comment")
        c.set(W + "id", cid)
        c.set(W + "author", author)
        if "initials" in params:
            c.set(W + "initials", initials)
        p = etree.SubElement(c, W + "p")
        p.set(_V_W14 + "paraId", pid)
        t = etree.SubElement(etree.SubElement(p, W + "r"), W + "t")
        t.set(_V_SPACE, "preserve")
        t.text = text
        if top not in book.entry:
            _v_entry(ex, up, "0")
        _v_entry(ex, pid, "0", up)
        if cid_root is not None:
            durable = ids.hex()
            e = etree.SubElement(cid_root, _V_CID + "commentId")
            e.set(_V_CID + "paraId", pid)
            e.set(_V_CID + "durableId", durable)
            if cex_root is not None:
                etree.SubElement(cex_root, _V_CEX + "commentExtensible").set(_V_CEX + "durableId", durable)
    if list(new_ids.values()) != list(a.get("expected_ids") or []):
        errs.append(f"comment_replies: the replies take ids {list(new_ids.values())} here, the plan "
                    f"{a.get('expected_ids')}")
    d = tree_difference(comments, ctx.out.xml(book.part)) if book.part in ctx.out.names else "missing"
    if d:
        errs.append(f"comment_replies: {book.part} is not the source with exactly the replies: {d}")
    ctx.verified_parts.add(book.part)
    for member, tree in ((book.ext, ex), (book.ids_part, cid_root), (book.cex_part, cex_root)):
        if member is None:
            continue
        d = tree_difference(tree, ctx.out.xml(member)) if member in ctx.out.names else "missing"
        if d:
            errs.append(f"comment_replies: {member} is not the source with exactly the replies' entries: {d}")
        ctx.verified_members.add(member)
    if not book.ext:
        member, target = _v_new_member(ctx.src)
        _v_check_created(ctx, member, target, ex, "comment_replies", errs)
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(name)) for name in ctx.src_tp))
    for (part, bi), expected in sorted(_v_mark_items(ctx, book, tops, new_ids, errs).items()):
        if part not in ctx.both:
            errs.append(f"comment_replies: {part} is missing from the output")
            continue
        sv, ov = ctx.view(part)
        oi = ctx.maps[part].get(bi)
        if oi is None:
            errs.append(f"comment_replies: {part} block {bi} has no counterpart in the output")
            continue
        check_paragraph(sv.pars[bi], ov.pars[oi], expected, old_ids, f"comment_replies {part} block {bi}", errs)
        ctx.verified_blocks.setdefault(part, {})[bi] = oi
    return f"comment_replies: {len(tops)} repl{'y' if len(tops) == 1 else 'ies'} (comments {', '.join(new_ids.values())})"


def _resolved_comments(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    params = dict(a.get("params") or {})
    action = params.get("action", "resolve")
    if action not in ("resolve", "reopen"):
        raise ValueError(f"action {action!r}")
    book = _VBook(ctx.src)
    for prob in book.problems():
        errs.append(f"resolved_comments: {prob} — the op must refuse")
    if book.problems():
        return "resolved_comments: comments unreadable"
    tops = _v_threads(book, params)
    want_done = action == "resolve"
    changed = [top for top in tops if book.done(top) != want_done]
    if changed != list(a.get("expected_changed") or []):
        errs.append(f"resolved_comments: threads {changed} change here, the plan changes {a.get('expected_changed')}")
    if not changed:
        return f"resolved_comments: every chosen thread already {'resolved' if want_done else 'open'}"
    ids = _VIds(ctx, book)
    comments = copy.deepcopy(book.root)
    ex = copy.deepcopy(ctx.src.xml(book.ext)) if book.ext else _v_ex_root()
    ids_added = False
    for top in changed:
        entry = book.entry.get(top)
        if entry is not None:                 # the same child of a copy of the same bytes
            list(ex)[list(entry.getparent()).index(entry)].set(_V_W15 + "done", "1" if want_done else "0")
            continue
        last = book.last_par(top)
        pid = last.get(_V_W14 + "paraId")
        if pid is None:
            pid = ids.hex()
            _v_last_copy(comments, top).set(_V_W14 + "paraId", pid)
            ids_added = True
        _v_entry(ex, pid, "1")
    if ids_added:
        d = tree_difference(comments, ctx.out.xml(book.part)) if book.part in ctx.out.names else "missing"
        if d:
            errs.append(f"resolved_comments: {book.part} is not the source with exactly the new paragraph ids: {d}")
        ctx.verified_parts.add(book.part)
    if book.ext:
        d = tree_difference(ex, ctx.out.xml(book.ext)) if book.ext in ctx.out.names else "missing"
        if d:
            errs.append(f"resolved_comments: {book.ext} is not the source with exactly those threads "
                        f"{'resolved' if want_done else 'reopened'}: {d}")
        ctx.verified_members.add(book.ext)
    else:
        member, target = _v_new_member(ctx.src)
        _v_check_created(ctx, member, target, ex, "resolved_comments", errs)
    return f"resolved_comments: {len(changed)} thread(s) {'resolved' if want_done else 'reopened'}"


COMMENT_THREAD_KINDS: Dict[str, Any] = {"comment_replies": _comment_replies, "resolved_comments": _resolved_comments}
