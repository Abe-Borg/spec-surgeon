"""Ops: reply_to_comments, resolve_comments — the review loop on a document's comment threads
(docs/BUILTIN_OPS_BATCH6_DESIGN.md §9). The verifier derives all of it again in verify/comment_threads.py (kinds
``comment_replies`` and ``resolved_comments``): keep the two identical.

* **Threads** are remove_comments' (ops/comments.py ``CommentBook``: a comment's paraIds, its entry in
  commentsExtended, its parent by ``w15:paraIdParent``, its root by the parents). A thread is a top comment and every
  comment whose root it is; its state is its top comment's ``w15:done`` (1/true/on: resolved; no entry: open).
* **The choice** (``which``): ``all`` (default), ``authors`` (the comments whose author, casefolded with whitespace
  collapsed, is listed) or ``text_regex`` (the comments whose text — each paragraph's own ``w:t`` text, paragraphs
  joined by a line break — the pattern finds); the threads are the roots of the comments chosen, in the comments
  part's order, each once. A reply to a reply is a reply to its thread: Word writes threads one level deep.
* **New ids** (``NewIds``): a comment id is 1 + the largest ``w:id`` of a comment or comment mark in any text part,
  one more per reply; a paragraph or durable id is the smallest value from 1, below 0x80000000, that no paraId,
  paraIdParent or durableId of the text parts and side parts holds (eight upper-case hex digits) — per thread, in order:
  the top comment's last paragraph's paraId when it has none, the reply's paraId, its durableId when commentsIds is
  there.
* **reply_to_comments**: per thread, a ``w:comment`` (the user's words in one paragraph, its author and initials; no
  date — the engine invents none) appended to the comments part; in commentsExtended (created next to the main part
  when missing, with its relationship and content type) the top comment's entry (done 0) when it has none, then the
  reply's: its paraId, ``w15:paraIdParent`` the top comment's last paragraph's, done 0; in commentsIds (when there; the
  top comment must have an entry there) and commentsExtensible (when there, never without commentsIds) the reply's
  durable id. Its marks go beside the thread's, as Word writes a reply's: a ``w:commentRangeStart`` after the thread's
  last one (when the top comment has a range; at the start of the first paragraph after it when that one stands between
  paragraphs — the same text), and after the run of the thread's last ``w:commentReference`` a
  ``w:commentRangeEnd`` and a new run with that run's properties (without ``w:rPrChange``) holding the reply's
  reference. Untracked (Word does not track comments).
* **resolve_comments**: ``action`` resolve (default) | reopen; a thread not as asked gets ``w15:done`` 1 / 0 on its top
  comment's entry — resolving a thread with no entry appends one (its last paragraph's paraId, added when missing; the
  part created when missing); a thread already as asked is left alone; the replies' entries are left as they are (the
  thread's state is its top comment's, as remove_comments reads it).
* Refused: a top comment with no paragraph or no single reference mark, a thread whose marks are in two parts, a range
  starting after its reference or between paragraphs with no body paragraph after it (``comment-anchor``); a mark in a
  text box (``textbox``); side
  parts Word would not write together or with another root, a top comment the file's comment ids leave out
  (``comment-metadata``); unreadable ids (``structure``).
"""
from __future__ import annotations

import copy
import posixpath
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from ..ooxml import XML_SPACE, block_full_text, block_struct_counts, paragraphs
from ..package import W, DocxPackage, serialize_xml
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .comments import (EXTENDED_REL, MARK_TAGS, W14, W15, W16CEX, W16CID, Comment, CommentBook, in_text_box,
                       norm_author, reference_run)
from .textedit import nearest_p

WHICH = ("all", "authors", "text_regex")
EXTENDED_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"
HEX_ATTRS = (W14 + "paraId", W15 + "paraId", W15 + "paraIdParent", W16CID + "paraId", W16CID + "durableId",
             W16CEX + "durableId")
START, REF = W + "commentRangeStart", W + "commentReference"
WHICH_SCHEMA = {
    "which": {"type": "string", "enum": list(WHICH), "default": "all",
              "description": "all | authors (the threads holding a comment by 'authors') | text_regex (the threads "
                             "holding a comment whose text 'text_regex' finds)"},
    "authors": {"type": "array", "items": {"type": "string"},
                "description": "with which=authors: the authors (case and spacing ignored)"},
    "text_regex": {"type": "string", "description": "with which=text_regex: a pattern searched in each comment's text"},
}


def read_which(params: Dict[str, Any]) -> Tuple[str, List[str], Optional[Any]]:
    which = params.get("which", "all")
    if which not in WHICH:
        raise JobException(f"'which' must be one of {list(WHICH)}", "bad-params")
    authors, pattern = params.get("authors"), params.get("text_regex")
    if (which == "authors") != (authors is not None) or (which == "text_regex") != (pattern is not None):
        raise JobException("name the authors with which='authors' and the pattern with which='text_regex' (and only "
                           "then)", "bad-params")
    if authors is not None and (not isinstance(authors, list) or not authors
                                or not all(isinstance(a, str) and a.strip() for a in authors)):
        raise JobException("'authors' must be a list of names", "bad-params")
    rx = None
    if pattern is not None:
        if not isinstance(pattern, str) or not pattern:
            raise JobException("'text_regex' must be a pattern", "bad-params")
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise JobException(f"'text_regex' is not a pattern: {e}", "bad-params")
    return which, list(authors or []), rx


def comment_text(c: Comment) -> str:
    return "\n".join("".join(t.text or "" for t in p.iter(W + "t") if nearest_p(t) is p) for p in c.paras)


def chosen_threads(book: CommentBook, which: str, authors: Sequence[str], rx) -> List[Comment]:
    """The top comments of the threads chosen (module docstring), in the comments part's order."""
    if which == "all":
        chosen = list(book.comments)
    elif which == "authors":
        wanted = {norm_author(a) for a in authors}
        chosen = [c for c in book.comments if norm_author(c.author) in wanted]
    else:
        chosen = [c for c in book.comments if rx.search(comment_text(c))]
    tops = {book.root_of(c).id for c in chosen}
    return [c for c in book.comments if c.id in tops]


def check_metadata(pkg: DocxPackage, book: CommentBook) -> None:
    for member, tag in ((book.extended, W15 + "commentsEx"), (book.ids_part, W16CID + "commentsIds"),
                        (book.extensible, W16CEX + "commentsExtensible")):
        if member and pkg.xml(member).tag != tag:
            raise JobException(f"{member} is not the {etree.QName(tag).localname} part Word writes; the threads "
                               f"cannot be kept consistent", "comment-metadata", {"member": member})
    if book.extensible and not book.ids_part:
        raise JobException("the file keeps comment dates (commentsExtensible) without comment ids (commentsIds), which "
                           "Word does not write; the threads cannot be kept consistent", "comment-metadata")


def side_parts(book: CommentBook) -> List[str]:
    return [m for m in (book.extended, book.ids_part, book.extensible) if m]


class NewIds:
    """The comment ids and paragraph/durable ids the file uses, and the next free ones (module docstring)."""

    def __init__(self, pkg: DocxPackage, book: CommentBook):
        self.top = 0
        for part in pkg.text_parts():
            for el in pkg.xml(part).iter(W + "comment", *MARK_TAGS):
                raw = el.get(W + "id", "")
                if not raw.isascii() or not raw.isdecimal():
                    raise JobException(f"{part}: a comment or comment mark has the id {raw!r}, not a number",
                                       "structure", {"part": part})
                self.top = max(self.top, int(raw))
        self.used: Set[int] = set()
        for member in pkg.text_parts() + side_parts(book):
            for el in pkg.xml(member).iter():
                for attr in HEX_ATTRS:
                    if el.get(attr) is None:
                        continue
                    try:
                        self.used.add(int(el.get(attr), 16))
                    except ValueError:
                        raise JobException(f"{member}: {el.get(attr)!r} is not a paragraph or durable id",
                                           "structure", {"member": member})

    def comment(self) -> str:
        self.top += 1
        return str(self.top)

    def hex(self) -> str:
        n = 1
        while n in self.used:
            n += 1
        if n >= 0x80000000:
            raise JobException("no paragraph id is left below 0x80000000", "structure")
        self.used.add(n)
        return f"{n:08X}"


def new_extended(pkg: DocxPackage) -> Tuple[str, str]:
    """(member, relationship target) of the commentsExtended part to create: the first free name beside the main part."""
    main = pkg.main_part()
    taken = set(pkg.member_names) | set(pkg.content_types()[1]) | {r.resolved for _, r in pkg.relationships()
                                                                     if r.resolved}
    n = 0
    while True:
        name = posixpath.join(posixpath.dirname(main), f"commentsExtended{n or ''}.xml")
        if name not in taken:
            return name, posixpath.relpath(name, posixpath.dirname(main) or ".")
        n += 1


def last_paragraph(c: Comment):
    if not c.paras:
        raise JobException(f"comment {c.id} holds no paragraph; a thread cannot hang from it", "comment-anchor",
                           {"comment": c.id})
    return c.paras[-1]


def nth(root, tag: str, at: Sequence[Any]):
    """The ``at[1]``-th element ``tag`` with the id ``at[0]`` in document order (a mark found again at apply)."""
    found = [m for m in root.iter(tag) if m.get(W + "id") == at[0]]
    if at[1] >= len(found):
        raise JobException(f"comment mark {at} moved between plan and apply", "internal")
    return found[at[1]]


class TextParts:
    """The text parts that can hold comment marks (all but the comments part), each read once: its tree, the document
    order of its elements, its paragraphs' indices."""

    def __init__(self, pkg: DocxPackage, book: CommentBook):
        self.roots = {part: pkg.xml(part) for part in pkg.text_parts() if part != book.part}
        self._held: Dict[str, List[Any]] = {}          # the elements the maps below name: held, their ids stay theirs
        self._order: Dict[str, Dict[int, int]] = {}
        self._index: Dict[str, Dict[int, int]] = {}

    def order(self, part: str) -> Dict[int, int]:
        if part not in self._order:
            els = self._held.setdefault(part + "#all", list(self.roots[part].iter()))
            self._order[part] = {id(el): k for k, el in enumerate(els)}
        return self._order[part]

    def paragraphs(self, part: str) -> List[Any]:
        return self._held.setdefault(part + "#p", paragraphs(self.roots[part]))

    def index(self, part: str) -> Dict[int, int]:
        if part not in self._index:
            self._index[part] = {id(p): i for i, p in enumerate(self.paragraphs(part))}
        return self._index[part]


def thread_marks(parts: TextParts, book: CommentBook, top: Comment) -> Dict[str, Any]:
    """Where the reply's marks go: the part, the thread's last range start (or None) and last reference, each as
    (comment id, occurrence) and its paragraph."""
    members = {c.id for c in book.comments if book.root_of(c).id == top.id}
    where = f"comment {top.id} by {top.author or '?'}"
    found = [part for part, root in parts.roots.items()
             if any(m.get(W + "id") in members for m in root.iter(START, REF))]
    if len(found) != 1:
        raise JobException(f"{where}: the thread's marks are in {found or 'no part of the document'}; reply to it in "
                           f"Word", "comment-anchor", {"comment": top.id})
    part = found[0]
    root = parts.roots[part]
    refs = [m for m in root.iter(REF) if m.get(W + "id") in members]
    starts = [m for m in root.iter(START) if m.get(W + "id") in members]
    if sum(1 for m in refs if m.get(W + "id") == top.id) != 1:
        raise JobException(f"{where}: the comment has no single reference mark in the text; reply to it in Word",
                           "comment-anchor", {"comment": top.id})
    ref = refs[-1]
    start = starts[-1] if any(m.get(W + "id") == top.id for m in starts) else None
    for m in (start, ref):
        if m is not None and in_text_box(m):
            raise JobException(f"{where}: its mark sits in a text box (Word keeps two copies of it); reply to it in "
                               f"Word", "textbox", {"comment": top.id})
    reference_run(ref)
    order = parts.order(part)
    if start is not None and order[id(start)] > order[id(ref)]:
        raise JobException(f"{where}: its range starts after its reference mark; reply to it in Word",
                           "comment-anchor", {"comment": top.id})
    index = parts.index(part)
    occurrence = lambda m: [x for x in root.iter(m.tag) if x.get(W + "id") == m.get(W + "id")].index(m)
    out = {"part": part, "ref": [ref.get(W + "id"), occurrence(ref)], "ref_block": index[id(nearest_p(ref))],
           "start": None, "start_block": None, "start_first": False}
    if start is not None:
        holder = nearest_p(start)
        if holder is None:                      # between paragraphs: the first paragraph after it starts the range
            holder = next((p for p in parts.paragraphs(part) if order[id(p)] > order[id(start)]), None)
            if holder is None or nearest_p(holder) is not None:
                raise JobException(f"{where}: its range starts between paragraphs and no body paragraph follows the "
                                   f"start; reply to it in Word", "comment-anchor", {"comment": top.id})
            out["start_first"] = True
        out.update(start=[start.get(W + "id"), occurrence(start)], start_block=index[id(holder)])
    return out


def reply_element(parent, cid: str, pid: str, params: Dict[str, Any]) -> None:
    c = etree.SubElement(parent, W + "comment")
    c.set(W + "id", cid)
    c.set(W + "author", params["author"])
    if "initials" in params:
        c.set(W + "initials", params["initials"])
    p = etree.SubElement(c, W + "p")
    p.set(W14 + "paraId", pid)
    t = etree.SubElement(etree.SubElement(p, W + "r"), W + "t")
    t.set(XML_SPACE, "preserve")
    t.text = params["text"]


def entry(parent, pid: str, done: str, up: Optional[str] = None) -> None:
    e = etree.SubElement(parent, W15 + "commentEx")
    e.set(W15 + "paraId", pid)
    if up is not None:
        e.set(W15 + "paraIdParent", up)
    e.set(W15 + "done", done)


def extended_root():
    return etree.Element(W15 + "commentsEx", nsmap={"w15": W15[1:-1]})


def place_marks(root, r: Dict[str, Any]) -> None:
    """One reply's marks beside its thread's (module docstring)."""
    if r["marks"]["start"] is not None:
        start = etree.Element(START)
        start.set(W + "id", r["id"])
        if r["marks"]["start_first"]:
            p = paragraphs(root)[r["marks"]["start_block"]]
            p.insert(1 if len(p) and p[0].tag == W + "pPr" else 0, start)
        else:
            nth(root, START, r["marks"]["start"]).addnext(start)
    run = nth(root, REF, r["marks"]["ref"]).getparent()
    new = etree.Element(W + "r")
    rpr = run.find(W + "rPr")
    if rpr is not None:
        rpr = copy.deepcopy(rpr)
        for c in rpr.findall(W + "rPrChange"):
            rpr.remove(c)
        if len(rpr) or rpr.attrib:
            new.append(rpr)
    etree.SubElement(new, REF).set(W + "id", r["id"])
    run.addnext(new)
    if r["marks"]["start"] is not None:
        end = etree.Element(W + "commentRangeEnd")
        end.set(W + "id", r["id"])
        run.addnext(end)


def create_extended(pkg: DocxPackage, created: Dict[str, str], root) -> None:
    pkg.add_member(created["member"], serialize_xml(root), EXTENDED_CT)
    pkg.edit_rels(pkg.main_part(), add=[{"type": EXTENDED_REL, "target": created["target"]}])


def plan_created(pkg: DocxPackage, frag: PlanFragment) -> Dict[str, str]:
    """The commentsExtended part to create, its relationship and content type, in the fragment."""
    member, target = new_extended(pkg)
    rels = pkg.rels_path_for(pkg.main_part())
    frag.members_added.append(member)
    frag.intended_parts.append(rels)
    if pkg.content_types()[0].get("xml") != EXTENDED_CT:
        frag.intended_parts.append("[Content_Types].xml")
    frag.touches += [(member, "*", "add"), (rels, "*", "rels"), ("[Content_Types].xml", "*", "ct")]
    frag.notes.append(f"the file kept no comment threads (no commentsExtended part): {member} is created")
    return {"member": member, "target": target}


def _thread_words(params: Dict[str, Any]) -> str:
    which = params.get("which")
    authors = [a.strip() for a in params["authors"] if isinstance(a, str) and a.strip()] \
        if isinstance(params.get("authors"), list) else []
    if which == "authors" or (which is None and authors):
        return ("every comment thread holding a comment by " + prose.join((prose.quote(a) for a in authors), "or")
                if authors else "the comment threads of the named authors")
    if which == "text_regex" or (which is None and isinstance(params.get("text_regex"), str)):
        pattern = params.get("text_regex")
        return ("every comment thread holding a comment that matches " + prose.quote(pattern)
                if isinstance(pattern, str) and pattern else "the comment threads whose comments match a pattern")
    return "every comment thread"


@register
class ReplyToComments(Op):
    name = "reply_to_comments"
    mode = "untracked"
    description = ("Reply to comment threads with the user's approved words, verbatim, as a named author: one reply per "
                   "thread chosen (all, by the authors of its comments, or by a pattern in their text), shown in Word "
                   "beneath the thread's first comment. Comments are not tracked changes; nothing else changes.")
    params_schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "the user's approved reply, verbatim, one paragraph"},
            "author": {"type": "string", "description": "the reviewer name Word shows"},
            "initials": {"type": "string"},
            **WHICH_SCHEMA,
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file with no such thread is left as it is"},
            "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"},
        },
        "required": ["text", "author"],
    }
    select_schema = {"description": "none — comments live where they live"}
    verification_profile = {
        "V2": "the reply's paragraph added to the comments part; every other text unchanged",
        "V3": "one reference mark more in the paragraph holding the thread's last one",
        "V10": "comment_replies re-derives the threads, the new ids and the marks' places and checks the comments part "
               "and the side parts whole and each marked paragraph item by item",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        text, author, initials = params.get("text"), params.get("author"), params.get("initials")
        words = prose.quote(text) if isinstance(text, str) and text.strip() else "the approved wording"
        who = ""
        if isinstance(author, str) and author.strip():
            who = " as " + prose.quote(author)
            if isinstance(initials, str) and initials.strip():
                who += f" (initials {prose.quote(initials)})"
        first = prose.sentence(f"Reply {words}{who} to {_thread_words(params)}, one reply per thread beneath its first "
                               f"comment")
        remarks = ["the comments already there and the document's text stay as they are"]
        if params.get("allow_missing") is True:
            remarks.append("a file with no such thread is left unchanged")
        return first + " " + prose.sentence("; ".join(remarks))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("reply_to_comments takes no selector", "bad-params")
        unknown = set(params) - {"text", "author", "initials", "which", "authors", "text_regex", "allow_missing",
                                 "tracked"}
        if unknown:
            raise JobException(f"reply_to_comments takes no {sorted(unknown)}", "bad-params")
        if params.get("tracked", False) is not False:
            raise JobException("a reply is a comment, which Word does not track: only tracked=false is accepted",
                               "bad-params")
        for key in ("text", "author") + (("initials",) if "initials" in params else ()):
            value = params.get(key)
            if not isinstance(value, str) or not value.strip() or any(
                    ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in (0xFFFE, 0xFFFF) for c in value):
                raise JobException(f"'{key}' must be the user's words on one line", "bad-params")
        allow = params.get("allow_missing", False)
        if not isinstance(allow, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        which, authors, rx = read_which(params)
        book = CommentBook(pkg)
        check_metadata(pkg, book)
        frag = self.fragment({}, params, mode="untracked")
        tops = chosen_threads(book, which, authors, rx)
        assertion = {"kind": "comment_replies", "params": dict(params), "expected_threads": [c.id for c in tops],
                     "expected_ids": []}
        frag.property_assertions.append(assertion)
        if not tops:
            why = "this file has no comments" if not book.comments else "no comment thread is chosen"
            if allow:
                frag.notes.append(f"reply_to_comments: {why} — nothing to do.")
                return frag
            raise JobException(f"reply_to_comments: {why}", "not-found", {"which": which})
        ids = NewIds(pkg, book)
        ids_root = pkg.xml(book.ids_part) if book.ids_part else None
        text_parts = TextParts(pkg, book)
        replies: List[Dict[str, Any]] = []
        for top in tops:
            last = last_paragraph(top)
            up = last.get(W14 + "paraId")
            add_up = up is None
            if add_up:
                up = ids.hex()
            if ids_root is not None and not any(e.get(W16CID + "paraId") in top.para_ids
                                                for e in ids_root.iter(W16CID + "commentId")):
                raise JobException(f"comment {top.id} by {top.author or '?'} has no entry in the file's comment ids "
                                   f"({book.ids_part}); Word would not link a reply to it — reply in Word",
                                   "comment-metadata", {"comment": top.id})
            r = {"top": top.id, "up": up, "add_up": add_up, "top_entry": top.id not in book.entry,
                 "id": ids.comment(), "para_id": ids.hex()}
            r["durable"] = ids.hex() if book.ids_part else None
            r["marks"] = thread_marks(text_parts, book, top)
            if book.resolved(top):
                frag.notes.append(f"comment {top.id}'s thread is marked resolved; the reply joins it and it stays "
                                  f"resolved")
            replies.append(r)
        assertion["expected_ids"] = [r["id"] for r in replies]
        # the comments part: the replies' paragraphs added at its end (V2), the part changed whole
        cpars = len(paragraphs(book.root))
        for k, r in enumerate(replies):
            frag.diffs.append(BlockDiff(part=book.part, block_index=cpars + k, before=None, after=params["text"],
                                        note=f"reply to comment {r['top']}"))
        frag.untracked_diffs_expected = True
        frag.intended_parts.append(book.part)
        frag.touches.append((book.part, "*", "text"))
        for member in side_parts(book):
            frag.intended_parts.append(member)
            frag.touches.append((member, "*", "text"))
        created = None if book.extended else plan_created(pkg, frag)
        # the marks: one reference more in the paragraph of each thread's last one (V3), the range start beside its own
        by_block: Dict[Tuple[str, int], int] = {}
        for r in replies:
            m = r["marks"]
            by_block[(m["part"], m["ref_block"])] = by_block.get((m["part"], m["ref_block"]), 0) + 1
            if m["start_block"] is not None:
                by_block.setdefault((m["part"], m["start_block"]), 0)
        for (part, bi), n in sorted(by_block.items()):
            p = paragraphs(text_parts.roots[part])[bi]
            if n:
                struct = block_struct_counts(p)
                struct["commentReference"] = struct.get("commentReference", 0) + n
                text = block_full_text(p)
                frag.diffs.append(BlockDiff(part=part, block_index=bi, before=text, after=text, struct_after=struct,
                                            note=f"{n} reply reference mark(s)"))
            frag.touches.append((part, str(bi), "text"))
            if part not in frag.intended_parts:
                frag.intended_parts.append(part)
        frag.geometry_reference = "none"
        frag.geometry_reason = "comments can change Word's markup margin; the body's layout is not a geometric promise"
        frag.transform_data = {"part": book.part, "extended": book.extended, "ids_part": book.ids_part,
                               "extensible": book.extensible, "created": created, "replies": replies}
        frag.notes.append(f"reply_to_comments: {len(replies)} repl{'y' if len(replies) == 1 else 'ies'} by "
                          f"{params['author']!r} (comments {', '.join(r['id'] for r in replies)}) to the thread(s) of "
                          f"comment(s) {', '.join(r['top'] for r in replies)}; untracked, as Word writes comments")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("replies"):
            return
        params = frag.params
        croot = pkg.xml(td["part"])
        by_id = {c.get(W + "id"): c for c in croot.findall(W + "comment")}
        ex = pkg.xml(td["extended"]) if td["extended"] else extended_root()
        ids_root = pkg.xml(td["ids_part"]) if td["ids_part"] else None
        cex_root = pkg.xml(td["extensible"]) if td["extensible"] else None
        by_part: Dict[str, List[Dict[str, Any]]] = {}
        for r in td["replies"]:
            if r["add_up"]:
                list(by_id[r["top"]].iter(W + "p"))[-1].set(W14 + "paraId", r["up"])
            reply_element(croot, r["id"], r["para_id"], params)
            if r["top_entry"]:
                entry(ex, r["up"], "0")
            entry(ex, r["para_id"], "0", r["up"])
            if ids_root is not None:
                e = etree.SubElement(ids_root, W16CID + "commentId")
                e.set(W16CID + "paraId", r["para_id"])
                e.set(W16CID + "durableId", r["durable"])
            if cex_root is not None:
                etree.SubElement(cex_root, W16CEX + "commentExtensible").set(W16CEX + "durableId", r["durable"])
            by_part.setdefault(r["marks"]["part"], []).append(r)
        pkg.write_xml(td["part"], croot)
        for member, root in ((td["ids_part"], ids_root), (td["extensible"], cex_root)):
            if member:
                pkg.write_xml(member, root)
        for part, rs in by_part.items():
            root = pkg.xml(part)
            for r in rs:
                place_marks(root, r)
            pkg.write_xml(part, root)
        if td["created"]:
            create_extended(pkg, td["created"], ex)
        else:
            pkg.write_xml(td["extended"], ex)


@register
class ResolveComments(Op):
    name = "resolve_comments"
    mode = "formatting"
    description = ("Mark comment threads resolved (as Word's Resolve does), or reopen them: all, by the authors of their "
                   "comments, or by a pattern in their text. A thread already as asked is left alone; the comments' "
                   "words and the document's text are unchanged.")
    params_schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["resolve", "reopen"], "default": "resolve"},
            **WHICH_SCHEMA,
            "allow_missing": {"type": "boolean", "default": True,
                              "description": "default true: a file with no such thread is left as it is; false sets "
                                             "it aside"},
            "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"},
        },
    }
    select_schema = {"description": "none — comments live where they live"}
    verification_profile = {
        "V2": "no text changes",
        "V10": "resolved_comments re-derives the threads and their state and checks the commentsExtended part whole "
               "(and the comments part when a paragraph id is added)",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        threads = _thread_words(params)
        if params.get("action") == "reopen":
            head = "Reopen " + threads.replace("comment thread", "resolved comment thread", 1)
            already = "threads already open are left alone"
        else:
            head = f"Mark {threads} resolved, as Word's Resolve does"
            already = "threads already resolved are left alone"
        remarks = [already + ", and the comments' words and the document's text are unchanged"]
        if params.get("allow_missing", True) is False:
            remarks.append("a file with no such thread is set aside rather than passed through")
        return prose.sentence(head) + " " + prose.sentence("; ".join(remarks))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("resolve_comments takes no selector", "bad-params")
        unknown = set(params) - {"action", "which", "authors", "text_regex", "allow_missing", "tracked"}
        if unknown:
            raise JobException(f"resolve_comments takes no {sorted(unknown)}", "bad-params")
        if params.get("tracked", False) is not False:
            raise JobException("Word does not track a comment's resolved state: only tracked=false is accepted",
                               "bad-params")
        action = params.get("action", "resolve")
        if action not in ("resolve", "reopen"):
            raise JobException("'action' must be 'resolve' or 'reopen'", "bad-params")
        allow = params.get("allow_missing", True)
        if not isinstance(allow, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        which, authors, rx = read_which(params)
        book = CommentBook(pkg)
        check_metadata(pkg, book)
        frag = self.fragment({}, params)
        tops = chosen_threads(book, which, authors, rx)
        want = action == "resolve"
        changed = [c for c in tops if book.resolved(c) != want]
        frag.property_assertions.append({"kind": "resolved_comments", "params": dict(params),
                                         "expected_changed": [c.id for c in changed]})
        if book.part:
            frag.touches.append((book.part, "*", "verify"))             # the choice reads the comments
        if book.extended:
            frag.touches.append((book.extended, "*", "verify"))         # … and the threads' state
        if not tops:
            why = "this file has no comments" if not book.comments else "no comment thread is chosen"
            if allow:
                frag.notes.append(f"resolve_comments: {why} — nothing to do.")
                return frag
            raise JobException(f"resolve_comments: {why}", "not-found", {"which": which})
        done_word = "resolved" if want else "open"
        if len(changed) < len(tops):
            frag.notes.append(f"resolve_comments: {len(tops) - len(changed)} thread(s) already {done_word}: left alone")
        if not changed:
            return frag
        ids = NewIds(pkg, book)
        changes: List[Dict[str, Any]] = []
        for top in changed:
            ex = book.entry.get(top.id)
            if ex is not None:
                changes.append({"top": top.id, "entry": list(book.ex_root).index(ex), "para": ex.get(W15 + "paraId")})
                continue
            pid = last_paragraph(top).get(W14 + "paraId")
            changes.append({"top": top.id, "new": pid or ids.hex(), "add_up": pid is None})
        if book.extended:
            frag.intended_parts.append(book.extended)
            frag.touches.append((book.extended, "*", "done"))
        created = None if book.extended else plan_created(pkg, frag)
        if any(c.get("add_up") for c in changes):
            frag.intended_parts.append(book.part)
            frag.touches.append((book.part, "*", "paraId"))
        frag.transform_data = {"part": book.part, "extended": book.extended, "created": created, "done": want,
                               "changes": changes}
        frag.notes.append(f"resolve_comments: {len(changed)} thread(s) marked {done_word} (comment(s) "
                          f"{', '.join(c.id for c in changed)}); their replies keep their own marks")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("changes"):
            return
        done = "1" if td["done"] else "0"
        ex = pkg.xml(td["extended"]) if td["extended"] else extended_root()
        croot = pkg.xml(td["part"])
        by_id = {c.get(W + "id"): c for c in croot.findall(W + "comment")}
        for ch in td["changes"]:
            if "entry" in ch:
                e = list(ex)[ch["entry"]]
                if e.get(W15 + "paraId") != ch["para"]:
                    raise JobException(f"comment {ch['top']}'s thread entry moved between plan and apply", "internal")
                e.set(W15 + "done", done)
                continue
            if ch["add_up"]:
                list(by_id[ch["top"]].iter(W + "p"))[-1].set(W14 + "paraId", ch["new"])
            entry(ex, ch["new"], done)
        if any(ch.get("add_up") for ch in td["changes"]):
            pkg.write_xml(td["part"], croot)
        if td["created"]:
            create_extended(pkg, td["created"], ex)
        else:
            pkg.write_xml(td["extended"], ex)
