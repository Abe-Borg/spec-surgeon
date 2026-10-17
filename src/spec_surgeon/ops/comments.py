"""Op: remove_comments — take comments out of a document: all of them, only resolved threads, or the comments of named
authors; the document's text is unchanged (docs/BUILTIN_OPS_BATCH2_DESIGN.md §2). The verifier re-reads the comments,
their threads and their marks with its own code (verify/comments.py, kind ``removed_comments``).

**Comments** (CLAUDE.md "Comments", both sides): a comment is a ``w:comment`` of the comments part (the main part's
``comments`` relationship); its paraIds are the ``w14:paraId`` of its paragraphs. Threads come from the
``commentsExtended`` part: a ``w15:commentEx`` belongs to the comment one of whose paraIds is its ``w15:paraId`` (the
first such entry in the part, document order, is the comment's entry); its ``w15:paraIdParent`` names the paragraph of
the comment it replies to. A comment's root follows the parents (a parent no comment owns, or a cycle, ends the walk).
**Resolved**: the root's entry has ``w15:done`` on (``1``/``true``/``on``) — a whole thread is resolved or not.

**Selected**: ``all`` → every comment; ``resolved`` → every comment whose root is resolved; ``authors`` → every comment
whose author (casefolded, whitespace collapsed) is listed. Then every reply below a selected comment, recursively
(cycle-safe): a thread never keeps a reply whose comment went.

**Removed**: each selected ``w:comment``; every ``w15:commentEx`` and ``w16cid:commentId`` whose paraId is one of the
removed comments' paraIds; every ``w16cex:commentExtensible`` whose ``durableId`` one of those ``commentId`` carried;
in every other text part, every ``w:commentRangeStart``/``w:commentRangeEnd``/``w:commentReference`` with the id of a
removed comment — and the run of a reference when it then holds nothing but ``w:rPr``. Parts are never removed (an
emptied ``w:comments`` is valid); relationships stay.

Refused: a mark of a removed comment inside a text box (``textbox``); two comments with one id, a comment without one,
a paraId two comments share, a reference mark outside a run (``structure``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from ..ooxml import block_full_text, block_struct_counts, count_revisions, paragraphs
from ..package import R, REL_TYPE_BASE, W, DocxPackage
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment

COMMENTS_REL = REL_TYPE_BASE + "comments"
EXTENDED_REL = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"
IDS_REL = "http://schemas.microsoft.com/office/2016/09/relationships/commentsIds"
EXTENSIBLE_REL = "http://schemas.microsoft.com/office/2018/08/relationships/commentsExtensible"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15 = "{http://schemas.microsoft.com/office/word/2012/wordml}"
W16CID = "{http://schemas.microsoft.com/office/word/2016/wordml/cid}"
W16CEX = "{http://schemas.microsoft.com/office/word/2018/wordml/cex}"
ON = ("1", "true", "on")
MARK_TAGS = (W + "commentRangeStart", W + "commentRangeEnd", W + "commentReference")
WHICH = ("all", "resolved", "authors")


def norm_author(s: Any) -> str:
    return " ".join(str(s or "").split()).casefold()


class Comment:
    def __init__(self, el):
        self.el = el
        self.id = el.get(W + "id")
        self.author = el.get(W + "author") or ""
        self.paras = paragraphs(el)
        self.para_ids = [p.get(W14 + "paraId") for p in self.paras if p.get(W14 + "paraId")]


class CommentBook:
    """A document's comments, as the op reads them: the comments, their threads, their side-part entries."""

    def __init__(self, pkg: DocxPackage):
        main = pkg.main_part()
        rels = pkg.rels(main)
        target = lambda t: next((r.resolved for r in rels.values() if r.type == t and r.resolved and pkg.has(r.resolved)),
                                None)
        self.part = target(COMMENTS_REL)
        self.extended, self.ids_part, self.extensible = target(EXTENDED_REL), target(IDS_REL), target(EXTENSIBLE_REL)
        self.root = pkg.xml(self.part) if self.part else None
        self.comments: List[Comment] = [Comment(c) for c in self.root.findall(W + "comment")] if self.root is not None else []
        seen: Set[str] = set()
        owner: Dict[str, Comment] = {}
        for c in self.comments:
            if c.id is None or c.id in seen:
                raise JobException(f"{self.part}: two comments share the id {c.id!r} (or one has none); the marks in "
                                   f"the document cannot be told apart", "structure", {"part": self.part, "id": c.id})
            seen.add(c.id)
            for pid in c.para_ids:
                if pid in owner:
                    raise JobException(f"{self.part}: comments {owner[pid].id} and {c.id} share the paragraph id "
                                       f"{pid}; their replies and resolved state cannot be told apart", "structure",
                                       {"part": self.part, "paraId": pid})
                owner[pid] = c
        self.owner = owner
        self.ex_root = pkg.xml(self.extended) if self.extended else None
        self.entry: Dict[str, Any] = {}                 # comment id → its commentEx (the first matching one)
        if self.ex_root is not None:
            for ex in self.ex_root.findall(W15 + "commentEx"):
                c = owner.get(ex.get(W15 + "paraId"))
                if c is not None and c.id not in self.entry:
                    self.entry[c.id] = ex
        self.by_id = {c.id: c for c in self.comments}

    def parent(self, c: Comment) -> Optional[Comment]:
        ex = self.entry.get(c.id)
        pid = ex.get(W15 + "paraIdParent") if ex is not None else None
        p = self.owner.get(pid) if pid else None
        return p if p is not None and p is not c else None

    def root_of(self, c: Comment) -> Comment:
        seen = {c.id}
        while True:
            p = self.parent(c)
            if p is None or p.id in seen:
                return c
            seen.add(p.id)
            c = p

    def resolved(self, c: Comment) -> bool:
        ex = self.entry.get(self.root_of(c).id)
        return ex is not None and (ex.get(W15 + "done") or "").strip().lower() in ON

    def select(self, which: str, authors: List[str]) -> List[str]:
        """The ids of the comments to remove, in the comments part's order."""
        if which == "all":
            chosen = {c.id for c in self.comments}
        elif which == "resolved":
            chosen = {c.id for c in self.comments if self.resolved(c)}
        else:
            wanted = {norm_author(a) for a in authors}
            chosen = {c.id for c in self.comments if norm_author(c.author) in wanted}
        grew = True
        while grew:                                       # every reply below a selected comment goes with it
            grew = False
            for c in self.comments:
                p = self.parent(c)
                if c.id not in chosen and p is not None and p.id in chosen:
                    chosen.add(c.id)
                    grew = True
        return [c.id for c in self.comments if c.id in chosen]


def _nearest_p(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def side_removals(pkg: DocxPackage, book: CommentBook, ids: List[str]) -> Dict[str, Tuple[Any, List[Any]]]:
    """{side part: (its tree, the entries of the removed comments in it)} — entries are direct children of the part's
    root: commentEx and commentId by a removed comment's paraId, commentExtensible by the durableId such a commentId
    carried. Parts with nothing to remove are left out."""
    pids = {pid for i in ids for pid in book.by_id[i].para_ids}
    out: Dict[str, Tuple[Any, List[Any]]] = {}
    if book.extended:
        root = pkg.xml(book.extended)
        out[book.extended] = (root, [ex for ex in root.findall(W15 + "commentEx") if ex.get(W15 + "paraId") in pids])
    durable: Set[str] = set()
    if book.ids_part:
        root = pkg.xml(book.ids_part)
        gone = [e for e in root.findall(W16CID + "commentId") if e.get(W16CID + "paraId") in pids]
        durable = {e.get(W16CID + "durableId") for e in gone if e.get(W16CID + "durableId")}
        out[book.ids_part] = (root, gone)
    if book.extensible:
        root = pkg.xml(book.extensible)
        out[book.extensible] = (root, [e for e in root.findall(W16CEX + "commentExtensible")
                                       if e.get(W16CEX + "durableId") in durable])
    return {k: v for k, v in out.items() if v[1]}


def marks_of(root, ids: Set[str]) -> List[Any]:
    return [el for el in root.iter(*MARK_TAGS) if el.get(W + "id") in ids]


def in_text_box(el) -> bool:
    """The element lies in a text box: inside a paragraph nested in another, or in text-box content at all."""
    p = _nearest_p(el)
    return (p is not None and _nearest_p(p) is not None) or any(a.tag == W + "txbxContent" for a in el.iterancestors())


def reference_run(ref) -> Any:
    r = ref.getparent()
    if r is None or r.tag != W + "r":
        raise JobException(f"a comment reference mark (id {ref.get(W + 'id')}) sits outside a run", "structure",
                           {"id": ref.get(W + "id")})
    return r


def emptied(run, ref) -> bool:
    """The run holds nothing but its w:rPr once ``ref`` is gone."""
    return all(not isinstance(c.tag, str) or c.tag == W + "rPr" or c is ref for c in run)


@register
class RemoveComments(Op):
    name = "remove_comments"
    mode = "formatting"
    description = ("Remove comments: all of them, only resolved threads, or the comments of named authors (a removed "
                   "comment takes its replies). The document's text is unchanged; the comment marks in the text go "
                   "with their comments.")
    params_schema = {
        "type": "object",
        "properties": {
            "which": {"type": "string", "enum": list(WHICH), "default": "all",
                      "description": "all | resolved (whole resolved threads) | authors (the comments of 'authors')"},
            "authors": {"type": "array", "items": {"type": "string"},
                        "description": "with which=authors: the authors whose comments go (case and spacing ignored)"},
            "allow_missing": {"type": "boolean", "default": True,
                              "description": "default true: a file with no comment to remove is left as it is (a "
                                             "clean-up runs over whole batches); false sets such a file aside"},
        },
    }
    select_schema = {"description": "none — comments live where they live"}
    verification_profile = {
        "V2": "every paragraph of a removed comment deleted (comments part); the text of every other part unchanged",
        "V3": "a paragraph that loses a reference mark: a structure-only declaration (commentReference count)",
        "V4": "revisions inside removed comments go (negative delta, flagged)",
        "V10": "removed_comments re-derives the selection (threads, resolved state, authors) and checks the comments "
               "part and every side part whole, every paragraph that held a mark item by item, marks between blocks "
               "through the residual",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("remove_comments takes no selector", "bad-params")
        which = params.get("which", "all")
        if which not in WHICH:
            raise JobException(f"'which' must be one of {list(WHICH)}", "bad-params")
        authors = params.get("authors") or []
        if not isinstance(authors, list) or not all(isinstance(a, str) and a.strip() for a in authors):
            raise JobException("'authors' must be a list of names", "bad-params")
        if (which == "authors") != bool(authors):
            raise JobException("name the authors with which='authors' (and only then)", "bad-params")
        book = CommentBook(pkg)
        frag = self.fragment({}, {**params, "which": which})
        ids = book.select(which, authors)
        if not ids:
            if not book.comments:
                why = "this file has no comments"
            elif which == "resolved":
                why = "no comment thread is marked resolved" + ("" if book.extended else
                                                                " (the file keeps no resolved state: no commentsExtended "
                                                                "part — Word 2010 or older)")
            else:
                why = f"no comment is by {', '.join(authors)}"
            if params.get("allow_missing", True):
                frag.notes.append(f"remove_comments ({which}): {why} — nothing to do.")
                # still asserted: the verifier reads the comments itself and must find nothing to remove either
                frag.property_assertions.append({"kind": "removed_comments", "asked_which": params.get("which"),
                                                 "asked_authors": params.get("authors"), "expected_ids": [],
                                                 "expected_anchor_blocks": {}, "expected_outside_marks": {}})
                return frag
            raise JobException(f"remove_comments ({which}): {why}", "not-found", {"which": which})
        gone_ids = set(ids)
        # the comments part: every paragraph of a removed comment is a deleted block
        cpars = paragraphs(book.root)
        index = {id(p): i for i, p in enumerate(cpars)}
        n_blocks = 0
        for i in ids:
            c = book.by_id[i]
            for p in c.paras:
                frag.diffs.append(BlockDiff(part=book.part, block_index=index[id(p)], before=block_full_text(p),
                                            after=None, note=f"comment {i} by {c.author or '?'} removed"))
                n_blocks += 1
            revs = count_revisions(c.el)
            for k, v in revs.items():
                frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
            if revs:
                frag.flags.append(f"comment {i} by {c.author or '?'} held pending tracked changes {revs}; they go with it")
        frag.untracked_diffs_expected = bool(frag.diffs)
        frag.intended_parts.append(book.part)
        frag.touches.append((book.part, "*", "remove"))
        # relationships of the comments part only removed comments used: they stay (flagged)
        used = lambda els: {v for el in els for x in el.iter() if isinstance(x.tag, str)
                            for k, v in x.attrib.items() if k.startswith(R)}
        kept_uses = used([c.el for c in book.comments if c.id not in gone_ids])
        orphan = sorted(used([book.by_id[i].el for i in ids]) - kept_uses)
        if orphan:
            frag.flags.append(f"a removed comment held a picture or link (relationship(s) {orphan} of {book.part}); "
                              f"the file stays in the package, unused")
        # side parts
        side = side_removals(pkg, book, ids)
        for member, (_, entries) in side.items():
            frag.intended_parts.append(member)
            frag.touches.append((member, "*", "remove"))
        # the marks in every other text part
        anchors: Dict[str, List[int]] = {}
        outside: Dict[str, List[str]] = {}
        for part in pkg.text_parts():
            if part == book.part:
                continue
            root = pkg.xml(part)
            marks = marks_of(root, gone_ids)
            if not marks:
                continue
            pars = paragraphs(root)
            where = {id(p): i for i, p in enumerate(pars)}
            by_block: Dict[int, List[Any]] = {}
            for m in marks:
                p = _nearest_p(m)
                if in_text_box(m):
                    raise JobException(f"{part}: the mark of comment {m.get(W + 'id')} sits inside a text box (Word "
                                       f"keeps two copies of it); remove that comment in Word", "textbox",
                                       {"part": part, "id": m.get(W + "id")})
                if p is None:
                    outside.setdefault(part, []).append(m.get(W + "id"))
                    frag.touches.append((part, f"comment-mark:{m.get(W + 'id')}", "remove"))
                    continue
                by_block.setdefault(where[id(p)], []).append(m)
            for bi, ms in sorted(by_block.items()):
                p = pars[bi]
                gone: List[Any] = []
                for m in ms:
                    gone.append(m)
                    if m.tag == W + "commentReference":
                        r = reference_run(m)
                        if emptied(r, m):
                            gone.append(r)
                            for k, v in count_revisions(r).items():
                                frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
                before, s_before, s_after = block_full_text(p), block_struct_counts(p), block_struct_counts(p, gone)
                if s_before != s_after:
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=before,
                                                struct_after=s_after, note="comment reference mark removed"))
                frag.touches.append((part, str(bi), "text"))
                anchors.setdefault(part, []).append(bi)
            if part not in frag.intended_parts:
                frag.intended_parts.append(part)
        n_replies = sum(1 for i in ids if book.parent(book.by_id[i]) is not None)
        # the user's own words (None = the default): the verifier applies its own default and reading
        frag.property_assertions.append({"kind": "removed_comments", "asked_which": params.get("which"),
                                         "asked_authors": params.get("authors"),
                                         "expected_ids": list(ids), "expected_anchor_blocks": anchors,
                                         "expected_outside_marks": outside})
        frag.transform_data = {"ids": list(ids), "part": book.part}
        frag.notes.append(f"remove_comments ({which}): {len(ids)} comment(s) removed (ids {', '.join(ids)}; "
                          f"{n_replies} of them replies), {n_blocks} comment paragraph(s); their marks taken out of "
                          f"{sorted(set(anchors) | set(outside)) or 'no part'}; the parts stay (people.xml too)")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("ids"):
            return
        ids = set(td["ids"])
        book = CommentBook(pkg)
        side = side_removals(pkg, book, list(td["ids"]))
        root = pkg.xml(book.part)
        for c in list(root.findall(W + "comment")):
            if c.get(W + "id") in ids:
                root.remove(c)
        pkg.write_xml(book.part, root)
        for member, (sroot, entries) in side.items():
            for e in entries:
                sroot.remove(e)
            pkg.write_xml(member, sroot)
        for part in pkg.text_parts():
            if part == book.part:
                continue
            proot = pkg.xml(part)
            marks = marks_of(proot, ids)
            if not marks:
                continue
            for m in marks:
                parent = m.getparent()
                parent.remove(m)
                if m.tag == W + "commentReference" and parent.tag == W + "r" and parent.getparent() is not None \
                        and all(not isinstance(c.tag, str) or c.tag == W + "rPr" for c in parent):
                    parent.getparent().remove(parent)
            pkg.write_xml(part, proot)
