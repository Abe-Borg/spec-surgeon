"""In-paragraph edits — verifier side (docs/BUILTIN_OPS_BATCH1_DESIGN.md §0.1; CLAUDE.md "Run text and in-paragraph
edits"), independent of ops/textedit.py (rule 1): this module's own run text, its own reading of the rules every edit
obeys, and the ITEM MODEL that checks an edited paragraph is exactly the source paragraph with exactly those edits.

A paragraph is a sequence of items in document order:
* one ``t`` item per character of a ``w:t`` of a live run (a run in no ``w:del``/``w:moveFrom``) — each a run-text
  position;
* one ``dt`` item per character of a ``w:delText`` of a hidden run;
* one ``x`` item per other run child (its signature; a run-text position when the run is live, unless the child is
  transparent — ``w:softHyphen``, ``w:lastRenderedPageBreak``);
* one ``p`` item per paragraph-level element that is not a run or a container (bookmarks, proofing marks, comment
  ranges, mc:AlternateContent, math …).
Every item carries its context: the run's property signature (an empty ``w:rPr`` is none), the run's attributes, and the
chain of containers between it and the paragraph (tag + attributes).

The expected output of an edit (start, end, text, drop_highlight), from the SOURCE items:
* untracked — the range's ``t`` items vanish; ``text``'s characters appear where the first one was, in its context
  (``w:highlight`` gone from the property signature when ``drop_highlight``);
* tracked — the range's ``t`` items become ``dt`` items whose chain gains a NEW ``w:del``; ``text`` follows the last of
  them in the first one's context plus a NEW ``w:ins``. A NEW wrapper matches an output wrapper of that tag with the
  plan's author, a ``w:date`` and a ``w:id`` no revision of the source part has. In someone's pending insertion (the
  runs' common parent is a ``w:ins`` whose parent is the paragraph) the deleted items keep that insertion around their
  NEW ``w:del``, the new text is NOT in it (it follows it), and — when text is placed — every later item of that
  insertion is in a SPLIT of it: a ``w:ins`` with the same attributes but a ``w:id`` the source part does not have.
The output paragraph's items must equal the expected items one for one; its ``w:pPr`` and attributes are the source's.

**Tabs** (built-in batch 5, docs/BUILTIN_OPS_BATCH5_DESIGN.md §2.3; this module's own reading): with ``tabs=True``
(``v_check_edits``, ``expected_items``, ``check_edited_paragraph`` — off by default) a PURE deletion (no text placed) may
also cover ``x`` items of a ``w:tab`` (never ``w:ptab``, a break or anything else). Untracked: those items vanish with the
characters; tracked: each stays an ``x`` item (no longer a run-text position) whose chain gains the NEW ``w:del``.

**Tabs and line breaks placed** (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §4.3; this module's own reading):
with ``breaks=True`` (``v_check_edits``, ``expected_items``, ``check_edited_paragraph`` — off by default) ``text`` may
hold ``\\t`` and ``\\n`` (no other control character): each is an ``x`` item of a bare ``w:tab`` / ``w:br`` where it stands
among the new characters, in the same context as they are (``v_placed_items``).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .reader import W, M
from .props import sig

V_OPAQUE = "\x00"
V_SHOWN = {W + "tab": "\t", W + "ptab": "\t", W + "br": "\n", W + "cr": "\n", W + "noBreakHyphen": "-"}
V_TRANSPARENT = {W + "softHyphen", W + "lastRenderedPageBreak"}
V_CONTAINERS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "hyperlink", W + "smartTag", W + "customXml",
                W + "sdt", W + "sdtContent", W + "fldSimple", W + "dir", W + "bdo"}
V_HIDING = {W + "del", W + "moveFrom"}
V_TEXT = (W + "t", W + "delText", W + "instrText", W + "delInstrText", M + "t")
V_REVISION_TAGS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "rPrChange", W + "pPrChange",
                   W + "sectPrChange", W + "tblPrChange", W + "trPrChange", W + "tcPrChange", W + "tblGridChange",
                   W + "numberingChange", W + "cellIns", W + "cellDel", W + "cellMerge"}


@dataclass
class Item:
    kind: str            # t | dt | x | p
    value: Any           # the character, or the element's signature
    ctx: Tuple[Any, ...]  # (rPr signature or None, run attributes, container chain)
    pos: Optional[int] = None    # run-text position (live t, live non-transparent x)
    tag: str = ""        # the element's tag (x, p)
    run: Any = None      # the run (t, dt, x)
    el: Any = None       # the w:t / w:delText / child / element
    live: bool = False   # in a run that is not hidden (no w:del / w:moveFrom around it)
    chain_els: Tuple[Any, ...] = ()   # the containers themselves (None for a NEW one), parallel to ctx[2]


def _owner(el):
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _rpr_sig(rpr, drop_highlight: bool = False):
    if rpr is None:
        return None
    if drop_highlight:
        rpr = copy.deepcopy(rpr)
        for h in rpr.findall(W + "highlight"):
            rpr.remove(h)
    if not len(rpr) and not rpr.attrib:
        return None
    return sig(rpr)


def _desc(el) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
    return (el.tag, tuple(sorted(el.attrib.items())))


def v_items(p) -> List[Item]:
    out: List[Item] = []
    pos = [0]

    def run_items(r, chain):
        hidden = any(q.tag in V_HIDING for q in chain)
        rpr = r.find(W + "rPr")
        ctx = (_rpr_sig(rpr), tuple(sorted(r.attrib.items())), tuple(_desc(q) for q in chain))
        els = tuple(chain)
        for c in r:
            if not isinstance(c.tag, str) or c.tag == W + "rPr":
                continue
            if c.tag == W + "t" and not hidden:
                for ch in c.text or "":
                    out.append(Item("t", ch, ctx, pos[0], c.tag, r, c, True, els))
                    pos[0] += 1
            elif c.tag == W + "delText" and hidden:
                for ch in c.text or "":
                    out.append(Item("dt", ch, ctx, None, c.tag, r, c, False, els))
            else:
                here = None
                if not hidden and c.tag not in V_TRANSPARENT:
                    here = pos[0]
                    pos[0] += 1
                out.append(Item("x", sig(c), ctx, here, c.tag, r, c, not hidden, els))

    def walk(el, chain):
        for c in el:
            if not isinstance(c.tag, str) or (el is p and c.tag == W + "pPr"):
                continue
            if c.tag == W + "r":
                run_items(c, chain)
            elif c.tag in V_CONTAINERS:
                walk(c, chain + [c])
            else:
                out.append(Item("p", sig(c), (None, (), tuple(_desc(q) for q in chain)), None, c.tag, None, c, False,
                                tuple(chain)))
    walk(p, [])
    return out


def v_run_text(items: Sequence[Item]) -> str:
    return "".join(it.value if it.kind == "t" else V_SHOWN.get(it.tag, V_OPAQUE) for it in items if it.pos is not None)


def v_paragraph_text(p) -> str:
    return v_run_text(v_items(p))


def v_shown(text: str) -> str:
    return " ".join(text.replace(V_OPAQUE, "¤").split())


def part_revision_ids(root) -> Set[str]:
    return {el.get(W + "id") for el in root.iter() if isinstance(el.tag, str) and el.tag in V_REVISION_TAGS
            and el.get(W + "id") is not None}


# ---- the rules every edit obeys (this module's own reading) ------------------------------------------------

def _v_tab_ok(it: Item, text: str, tabs: bool) -> bool:
    """A w:tab item a pure deletion may cover (``tabs=True`` only)."""
    return tabs and not text and it.kind == "x" and it.tag == W + "tab"


V_PLACED = {"\t": W + "tab", "\n": W + "br"}      # breaks=True: the characters a placed text shows as run children


def v_placed_items(text: str, ctx: Tuple[Any, ...], chain_els: Tuple[Any, ...] = ()) -> List[Item]:
    """The items a placed ``text`` brings, in ``ctx``: a ``t`` item per character, an ``x`` item of a bare ``w:tab`` /
    ``w:br`` per tab / line break."""
    return [Item("x", sig(etree.Element(V_PLACED[ch])), ctx, None, V_PLACED[ch], chain_els=chain_els)
            if ch in V_PLACED else Item("t", ch, ctx, chain_els=chain_els) for ch in text]


def v_check_edits(p, items: Sequence[Item], edits: Sequence[Tuple[int, int, str, bool]], tracked: bool,
                  in_field: Set[Any], tabs: bool = False,
                  allow_tracked_format_split: bool = False, breaks: bool = False) -> List[Tuple[int, int, str, bool]]:
    """The edits sorted, after the rules; a ValueError says the plan should have refused. ``tabs``: a pure deletion may
    cover w:tab items; ``breaks``: the new text may hold tabs and line breaks."""
    by_pos = {it.pos: k for k, it in enumerate(items) if it.pos is not None}
    out = sorted(edits, key=lambda e: (e[0], e[1]))
    for a, b in zip(out, out[1:]):
        if b[0] < a[1]:
            raise ValueError(f"edits overlap at run-text positions {a[:2]} and {b[:2]}")
    for start, end, text, drop in out:
        if not 0 <= start < end or any(i not in by_pos for i in range(start, end)):
            raise ValueError(f"edit ({start}, {end}) lies outside the paragraph's run text")
        if any((ord(c) < 32 or c == "\x7f") and not (breaks and c in V_PLACED) for c in text):
            raise ValueError(f"edit text {text!r} holds a control character"
                             + (" other than a tab or a line break" if breaks else ""))
        ks = [by_pos[i] for i in range(start, end)]
        if any(items[k].kind != "t" and not _v_tab_ok(items[k], text, tabs) for k in ks):
            raise ValueError(f"edit ({start}, {end}) covers something other than plain text"
                             + (" or a tab" if tabs and not text else ""))
        runs: List[Any] = []
        for k in ks:
            if items[k].run not in runs:
                runs.append(items[k].run)
        if any(r in in_field for r in runs):
            raise ValueError(f"edit ({start}, {end}) changes text a field shows")
        for it in items[ks[0] + 1:ks[-1]]:
            if it.kind == "t" and it.pos is not None and start <= it.pos < end:
                continue
            if it.kind == "dt" or (it.el is not None and any(_owner(t) is p for t in it.el.iter(*V_TEXT))):
                raise ValueError(f"edit ({start}, {end}) has deleted text or field code inside it")
        if tracked:
            parent = runs[0].getparent()
            for r in runs:
                if r.getparent() is not parent or not (parent is p or (parent.tag == W + "ins"
                                                                       and parent.getparent() is p)):
                    raise ValueError(f"edit ({start}, {end}) is tracked across or inside a "
                                     f"{r.getparent().tag.split('}')[-1]} other than one pending insertion")
                if r.find(W + "rPr/" + W + "rPrChange") is not None and not allow_tracked_format_split:
                    raise ValueError(f"edit ({start}, {end}) is tracked in a run carrying w:rPrChange")
        if drop:
            for r in runs:
                if any(it.run is r and it.pos is not None and not start <= it.pos < end for it in items) or any(
                        isinstance(c.tag, str) and c.tag not in (W + "rPr", W + "t") for c in r):
                    raise ValueError(f"edit ({start}, {end}) drops a highlight from a run holding more than its text")
    return out


def expected_items(p, items: Sequence[Item], edits: Sequence[Tuple[int, int, str, bool]], tracked: bool,
                   author: Optional[str], tabs: bool = False, breaks: bool = False) -> List[Item]:
    out = list(items)
    for start, end, text, drop in sorted(edits, key=lambda e: -e[0]):
        by_pos = {it.pos: k for k, it in enumerate(out) if it.pos is not None}
        ks = [by_pos[i] for i in range(start, end)]
        if any(out[k].kind != "t" and not _v_tab_ok(out[k], text, tabs) for k in ks):
            raise ValueError(f"edit ({start}, {end}) covers something other than plain text")
        if not breaks and any(c in V_PLACED for c in text):
            raise ValueError(f"edit text {text!r} holds a tab or a line break")
        first = next((out[k] for k in ks if out[k].kind == "t"), out[ks[0]])
        rs = _rpr_sig(first.run.find(W + "rPr"), drop_highlight=drop)
        base = (rs, first.ctx[1], first.ctx[2])
        if tracked:
            parent = first.run.getparent()
            pending = parent if parent.tag == W + "ins" and parent.getparent() is p else None
            for k in ks:
                it = out[k]
                if it.kind == "x":                        # a w:tab (tabs=True): deleted with the characters around it
                    out[k] = Item("x", it.value, (it.ctx[0], it.ctx[1], it.ctx[2] + (("NEW", W + "del", author),)),
                                  None, it.tag, it.run, it.el, False, it.chain_els + (None,))
                    continue
                out[k] = Item("dt", it.value, (it.ctx[0], it.ctx[1], it.ctx[2] + (("NEW", W + "del", author),)),
                              chain_els=it.chain_els + (None,))
            for k in range(ks[0] + 1, ks[-1]):            # a page-break mark or soft hyphen inside goes with the text
                it = out[k]
                if it.kind == "x" and it.live and it.tag in V_TRANSPARENT:
                    out[k] = Item("x", it.value, (it.ctx[0], it.ctx[1], it.ctx[2] + (("NEW", W + "del", author),)),
                                  it.pos, it.tag, it.run, it.el, False, it.chain_els + (None,))
            chain, els = base[2], first.chain_els
            if pending is not None:                       # the new text follows the pending insertion, not in it
                chain, els = chain[:-1], els[:-1]
            new = v_placed_items(text, (base[0], base[1], chain + (("NEW", W + "ins", author),)), els + (None,))
            out[ks[-1] + 1:ks[-1] + 1] = new
            if pending is not None and text:
                split = ("SPLIT", W + "ins", tuple(sorted((k, v) for k, v in pending.attrib.items() if k != W + "id")))
                for k in range(ks[-1] + 1 + len(new), len(out)):
                    it = out[k]
                    for j, el in enumerate(it.chain_els):
                        if el is pending:
                            c2 = list(it.ctx[2])
                            c2[j] = split
                            out[k] = Item(it.kind, it.value, (it.ctx[0], it.ctx[1], tuple(c2)), it.pos, it.tag,
                                          it.run, it.el, it.live, it.chain_els)
                            break
        else:
            for k in sorted(ks, reverse=True):
                del out[k]
            out[ks[0]:ks[0]] = v_placed_items(text, base)
    return out


def _ctx_matches(exp, got, old_ids: Set[str]) -> bool:
    if exp[0] != got[0] or exp[1] != got[1] or len(exp[2]) != len(got[2]):
        return False
    for a, b in zip(exp[2], got[2]):
        if a and a[0] == "NEW":
            attrs = dict(b[1])
            if b[0] != a[1] or attrs.get(W + "author") != a[2] or W + "date" not in attrs \
                    or attrs.get(W + "id") is None or attrs.get(W + "id") in old_ids:
                return False
        elif a and a[0] == "SPLIT":
            attrs = dict(b[1])
            if b[0] != a[1] or tuple(sorted((k, v) for k, v in attrs.items() if k != W + "id")) != a[2] \
                    or attrs.get(W + "id") is None or attrs.get(W + "id") in old_ids:
                return False
        elif a != b:
            return False
    return True


def _say(it: Item) -> str:
    what = {"t": "text", "dt": "deleted text", "x": "run content", "p": "paragraph content"}[it.kind]
    shown = repr(it.value) if it.kind in ("t", "dt") else (it.tag.split("}")[-1] if it.tag else "element")
    chain = [c[1].split("}")[-1] + " (new)" if c and c[0] == "NEW" else c[0].split("}")[-1] for c in it.ctx[2]]
    return f"{what} {shown}" + (f" in {'/'.join(chain)}" if chain else "")


def compare_items(expected: Sequence[Item], got: Sequence[Item], old_ids: Set[str]) -> Optional[str]:
    """None when equal, else where they first differ (with the text around it)."""
    for k in range(min(len(expected), len(got))):
        e, g = expected[k], got[k]
        if e.kind != g.kind or e.value != g.value or not _ctx_matches(e.ctx, g.ctx, old_ids):
            near = "".join(x.value for x in expected[max(0, k - 20):k] if x.kind in ("t", "dt"))
            how = ("its formatting or place (run properties, run attributes, hyperlink, content control or revision "
                   "around it) differs" if e.kind == g.kind and e.value == g.value else
                   f"expected {_say(e)}, found {_say(g)}")
            return f"after …{near!r}: {how}"
    if len(expected) != len(got):
        extra = got[len(expected):] if len(got) > len(expected) else expected[len(got):]
        return (f"{'more' if len(got) > len(expected) else 'less'} content than expected: "
                + ", ".join(_say(x) for x in extra[:4]))
    return None


V_INERT = {W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr"}


def v_deletable(p, tracked: bool) -> Optional[str]:
    """Why the op had to refuse deleting this paragraph (None when it may): a section break, a field, a text box; tracked
    also pending revisions and anything but pPr, runs, bookmarks and proofing marks."""
    if p.find(f"{W}pPr/{W}sectPr") is not None:
        return "it carries a section break"
    if any(True for _ in p.iter(W + "fldChar", W + "fldSimple")):
        return "it holds a field"
    if any(q is not p for q in p.iter(W + "p")):
        return "it holds a text box"
    if tracked:
        if any(isinstance(el.tag, str) and el.tag in V_REVISION_TAGS for el in p.iter()):
            return "it carries pending revisions"
        if any(isinstance(c.tag, str) and c.tag not in V_INERT | {W + "pPr", W + "r"} for c in p):
            return "it holds content other than plain runs"
    return None


def ppr_norm(ppr, drop_new_del_author: Optional[str] = None, old_ids: Set[str] = frozenset(), mark: str = "del"):
    """(signature of the pPr with a NEW paragraph-mark w:del — or ``mark`` w:ins — by ``author`` removed, empty rPr / pPr
    dropped; whether that mark was there)."""
    if ppr is None:
        return None, False
    c = copy.deepcopy(ppr)
    found = False
    rpr = c.find(W + "rPr")
    if rpr is not None and drop_new_del_author is not None:
        for d in rpr.findall(W + mark):
            if d.get(W + "author") == drop_new_del_author and d.get(W + "date") and d.get(W + "id") is not None \
                    and d.get(W + "id") not in old_ids:
                rpr.remove(d)
                found = True
    if rpr is not None and not len(rpr) and not rpr.attrib:
        c.remove(rpr)
    if not len(c) and not c.attrib:
        return None, found
    return sig(c), found


def check_deleted_paragraph(src_p, out_p, author: Optional[str], old_ids: Set[str], where: str,
                            errs: List[str]) -> None:
    """A tracked paragraph deletion: every run directly in the paragraph inside a NEW ``w:del`` of its own (its text as
    ``w:delText``), the paragraph mark carrying a NEW ``w:del``, everything else — bookmarks, proofing marks, their
    order — as it was."""
    why = v_deletable(src_p, True)
    if why:
        errs.append(f"{where}: {why} — the op must refuse")
        return
    expected = []
    for it in v_items(src_p):
        if it.run is not None and it.run.getparent() is src_p:
            expected.append(Item("dt" if it.kind == "t" else it.kind, it.value,
                                 (it.ctx[0], it.ctx[1], it.ctx[2] + (("NEW", W + "del", author),)),
                                 None, it.tag, it.run, it.el, False, it.chain_els + (None,)))
        else:
            expected.append(it)
    d = compare_items(expected, v_items(out_p), old_ids)
    if d:
        errs.append(f"{where}: not exactly a tracked deletion of the paragraph — {d}")
    src_sig, _ = ppr_norm(src_p.find(W + "pPr"))
    out_sig, marked = ppr_norm(out_p.find(W + "pPr"), author, old_ids)
    if not marked:
        errs.append(f"{where}: the paragraph mark carries no new w:del by {author!r}")
    if src_sig != out_sig:
        errs.append(f"{where}: paragraph properties changed beyond the deleted mark")
    if dict(src_p.attrib) != dict(out_p.attrib):
        errs.append(f"{where}: paragraph attributes changed")


def check_edited_paragraph(src_p, out_p, edits: Sequence[Tuple[int, int, str, bool]], tracked: bool,
                           author: Optional[str], in_field: Set[Any], old_ids: Set[str], where: str,
                           errs: List[str], tabs: bool = False, ppr: bool = True, base=None,
                           allow_tracked_format_split: bool = False, breaks: bool = False) -> None:
    """``out_p`` must be ``src_p`` with exactly ``edits`` (the verifier's own derivation), item for item.
    ``tabs``: pure deletions may cover w:tab items (built-in batch 5). ``breaks``: a new text may hold tabs and line
    breaks (built-in batch 6). ``ppr=False``: the caller checks the paragraph's ``w:pPr`` itself (an op that also
    restyles it). ``base``: a copy of ``src_p`` the caller changed in the run properties its op also changes (cleared
    formatting) — the expected items are built from it; the rules are always judged on ``src_p``."""
    items = v_items(src_p)
    try:
        edits = v_check_edits(src_p, items, edits, tracked, in_field, tabs=tabs,
                              allow_tracked_format_split=allow_tracked_format_split, breaks=breaks)
    except ValueError as e:
        errs.append(f"{where}: {e} — the op must refuse this paragraph")
        return
    if ppr and sig(src_p.find(W + "pPr")) != sig(out_p.find(W + "pPr")):
        errs.append(f"{where}: paragraph properties changed")
    if dict(src_p.attrib) != dict(out_p.attrib):
        errs.append(f"{where}: paragraph attributes changed")
    if base is not None:
        b_items = v_items(base)
        if [(it.kind, it.value, it.pos) for it in b_items] != [(it.kind, it.value, it.pos) for it in items]:
            errs.append(f"{where}: the expected base paragraph is not the source's content")
            return
        expected = expected_items(base, b_items, edits, tracked, author, tabs=tabs, breaks=breaks)
    else:
        expected = expected_items(src_p, items, edits, tracked, author, tabs=tabs, breaks=breaks)
    d = compare_items(expected, v_items(out_p), old_ids)
    if d:
        errs.append(f"{where}: not exactly the predicted change — {d}")
