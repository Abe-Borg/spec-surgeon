"""V10 kinds ``removed_hidden_text``, ``removed_notes`` and ``removed_empty_paragraphs`` — built-in batch 2 (items 14, 15,
17), verifier side, independent of ops/hidden.py and ops/empty_paragraphs.py (rule 1): this module's own reading of
what is hidden, what a note is, what a blank paragraph is, which paragraphs a deletion must keep and how fields limit a
run removal (docs/BUILTIN_OPS_BATCH2_DESIGN.md §5, §6, §8; CLAUDE.md lists the definitions).

Each kind derives every decision again from the SOURCE — which paragraphs go, which runs and simple fields go — and
compares it with what the plan predicted (``expected``), then checks the output: a paragraph that loses runs item by
item (verify/markup: ``drop`` untracked, ``tracked_delete`` tracked), a deleted paragraph as a tracked deletion
(``check_deleted_paragraph``) or gone (``deleted_src_blocks``, the paragraph count checked); everything else is the
residual's.

* **Content runs**: a paragraph's runs (nearest ``w:p``) that are live (no ``w:del``/``w:moveFrom`` below it) and hold
  something besides ``w:rPr``.
* **Hidden**: ``verify/markup.v_hidden`` / ``v_mark_hidden`` (ambiguous → the op must refuse). A hidden paragraph: mark
  hidden and every content run hidden (none counts).
* **Notes**: a paragraph matches a rule when every given test holds — style (its pStyle, else the default paragraph
  style), text (``re.search`` in the run text), colour (a content run with visible text exists and every such run's
  colour is listed), hidden (a content run exists and all are hidden). Inline notes: content runs of the listed colour
  and/or hidden, never inside a ``w:hyperlink``, never holding a picture.
* **Blank**: not in a text box, no section break, no revision, no numbering (own numId, else the style chain's, else
  docDefaults'; 0 = off), no border (a ``w:pBdr`` with a child) or frame anywhere on that chain, nothing but ``w:pPr``,
  proofing marks and runs of ``w:rPr``, ``w:lastRenderedPageBreak`` and ``w:t`` of spaces / no-break spaces.
* **Keeps** (per container, over its blocks ``w:p``/``w:tbl``/``w:sdt``/``w:customXml``/``w:altChunk``): the first
  paragraph between two tables the deletions would join; the last block when tracked, or — untracked — when the rest
  would leave the container empty or ending in a non-paragraph.
* **Fields**: a run to remove inside a complex field's span goes only with the whole outermost field (local to the
  paragraph, every content run of its span chosen and live, no simple field in it); inside a simple field only with the
  whole simple field (untracked; tracked refused).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .reader import W
from .props import Ctx, Styles, _pstyle
from .edits import (V_CONTAINERS, V_HIDING, V_REVISION_TAGS, check_deleted_paragraph, part_revision_ids, v_deletable,
                    v_items, v_paragraph_text)
from .fields import v_fields
from .markup import (check_paragraph, drop, tracked_delete, v_color, v_color_in, v_color_names, v_hidden,
                     v_mark_hidden)

V_BLOCKS = {W + "p", W + "tbl", W + "sdt", W + "customXml", W + "altChunk"}
V_PICTURES = {W + "drawing", W + "pict", W + "object"}
V_DEFAULT_HIDDEN_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]


class _VRefuse(Exception):
    """The op should have refused this file (the code it should have used)."""

    def __init__(self, code: str, why: str):
        super().__init__(f"{why} ({code})")
        self.code = code


def _vp(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _v_runs(p) -> List[Any]:
    return [r for r in p.iter(W + "r") if _vp(r) is p]


def _v_simples(p) -> List[Any]:
    return [s for s in p.iter(W + "fldSimple") if _vp(s) is p]


def _v_live(r, p) -> bool:
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag in V_HIDING:
            return False
        q = q.getparent()
    return True


def _v_has(r) -> bool:
    return any(isinstance(c.tag, str) and c.tag != W + "rPr" for c in r)


def _v_content(p) -> List[Any]:
    return [r for r in _v_runs(p) if _v_live(r, p) and _v_has(r)]


def _v_contained(r, p) -> bool:
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag not in V_CONTAINERS:
            return False
        q = q.getparent()
    return q is p


def _v_outer(el, p, tag: str) -> Optional[Any]:
    out, q = None, el.getparent()
    while q is not None and q is not p:
        if q.tag == tag:
            out = q
        q = q.getparent()
    return out


def _v_hidden_or_refuse(r, p, styles: Styles) -> bool:
    h = v_hidden(r.find(W + "rPr"), p, styles)
    if h is None:
        raise _VRefuse("toggle-ambiguous", "a run's 'hidden' is set at several style levels")
    return h


def _v_keep(blocks: Sequence[Any], chosen: Sequence[Any], tracked: bool) -> List[Any]:
    ch = {id(b) for b in chosen}
    keep: List[Any] = []
    n = len(blocks)
    for i in range(n):
        if blocks[i].tag != W + "tbl":
            continue
        j = i + 1
        while j < n and id(blocks[j]) in ch:
            j += 1
        if j < n and j > i + 1 and blocks[j].tag == W + "tbl" and all(x is not blocks[i + 1] for x in keep):
            keep.append(blocks[i + 1])
    if n and id(blocks[-1]) in ch and all(x is not blocks[-1] for x in keep):
        if tracked:
            keep.append(blocks[-1])
        else:
            kept = {id(x) for x in keep}
            rest = [b for b in blocks if id(b) not in ch or id(b) in kept]
            if not rest or rest[-1].tag != W + "p":
                keep.append(blocks[-1])
    return keep


def _v_blocks(container) -> List[Any]:
    return [c for c in container if isinstance(c.tag, str) and c.tag in V_BLOCKS]


class _VSpans:
    """The part's complex fields as begin-to-end spans over its runs in document order."""

    def __init__(self, root):
        self.runs = list(root.iter(W + "r"))
        self.at = {id(r): k for k, r in enumerate(self.runs)}
        self.spans: List[Tuple[int, int, Any]] = []
        for f in v_fields(root):
            if f.simple is not None or f.begin is None or f.end is None:
                continue
            b, e = self.at.get(id(f.begin.getparent())), self.at.get(id(f.end.getparent()))
            if b is not None and e is not None and b <= e:
                self.spans.append((b, e, f))

    def outer(self, r):
        k = self.at.get(id(r))
        if k is None:
            return None
        hits = [s for s in self.spans if s[0] <= k <= s[1]]
        return sorted(hits, key=lambda s: (s[0], -s[1]))[0] if hits else None


def _v_field_rules(p, chosen: Sequence[Any], spans: _VSpans, tracked: bool) -> Tuple[List[Any], List[Any]]:
    ch = {id(r) for r in chosen}
    simples: List[Any] = []
    for r in chosen:
        if not _v_contained(r, p):
            raise _VRefuse("structure", "a run to remove is not reachable through containers alone")
        if any(True for _ in r.iter(W + "p")):
            raise _VRefuse("textbox", "a run to remove holds a text box")
        span = spans.outer(r)
        if span is not None:
            b, e, f = span
            if f.paragraph() is not p:
                raise _VRefuse("field-result", "a run to remove lies in a field over several paragraphs")
            inside = spans.runs[b:e + 1]
            for x in inside:
                if _v_has(x) and (_vp(x) is not p or not _v_live(x, p) or id(x) not in ch):
                    raise _VRefuse("field-result", "a run to remove lies in a field not removed as a whole")
            inside_ids = {id(x) for x in inside}
            for s in _v_simples(p):
                if any(id(x) in inside_ids for x in s.iter(W + "r")):
                    raise _VRefuse("field-result", "a simple field lies inside a field to remove")
            continue
        s = _v_outer(r, p, W + "fldSimple")
        if s is None:
            continue
        if tracked:
            raise _VRefuse("field-result", "a simple field cannot be deleted as a tracked change")
        for x in s.iter(W + "r"):
            if _v_has(x) and (id(x) not in ch or not _v_live(x, p)):
                raise _VRefuse("field-result", "a run to remove is part of a simple field's result")
        if all(x is not s for x in simples):
            simples.append(s)
    runs = [r for r in chosen if not any(any(a is s for a in r.iterancestors(W + "fldSimple")) for s in simples)]
    return runs, simples


# ---- hidden text and notes: the decisions ---------------------------------------------------------------------------

def _v_choose_hidden(p, styles: Styles, args) -> Tuple[bool, List[Any]]:
    content = _v_content(p)
    hidden = [r for r in content if _v_hidden_or_refuse(r, p, styles)]
    if len(hidden) == len(content):
        m = v_mark_hidden(p, styles)
        if m is None:
            raise _VRefuse("toggle-ambiguous", "a paragraph mark's 'hidden' is set at several style levels")
        if m:
            return True, []
    return False, hidden


def _v_demote_hidden(p, styles: Styles, args) -> List[Any]:
    return [r for r in _v_content(p) if v_hidden(r.find(W + "rPr"), p, styles)]


def _v_note_rule(rule: Dict[str, Any], p, content: List[Any], styles: Styles) -> bool:
    if "style_in" in rule and (_pstyle(p) or styles.default.get("paragraph")) not in rule["style_in"]:
        return False
    if "text_regex" in rule and re.search(rule["text_regex"], v_paragraph_text(p)) is None:
        return False
    if "color_in" in rule:
        wanted = v_color_names(rule["color_in"])
        shown = [r for r in content if any((t.text or "").strip() for t in r.findall(W + "t"))]
        if not shown or not all(v_color_in(v_color(r.find(W + "rPr"), p, styles), wanted) for r in shown):
            return False
    if rule.get("hidden"):
        if not content:
            return False
        for r in content:
            if not _v_hidden_or_refuse(r, p, styles):
                return False
    return True


def _v_choose_note(p, styles: Styles, args) -> Tuple[bool, List[Any]]:
    rules, run_rule = args
    content = _v_content(p)
    if any(_v_note_rule(rule, p, content, styles) for rule in rules):
        return True, []
    if not run_rule:
        return False, []
    wanted = v_color_names(run_rule.get("color_in") or [])
    out = []
    for r in content:
        if _v_outer(r, p, W + "hyperlink") is not None or any(c.tag in V_PICTURES for c in r):
            continue
        if "color_in" in run_rule and not v_color_in(v_color(r.find(W + "rPr"), p, styles), wanted):
            continue
        if run_rule.get("hidden") and not _v_hidden_or_refuse(r, p, styles):
            continue
        out.append(r)
    return False, out


def _v_demote_note(p, styles: Styles, args) -> List[Any]:
    return _v_content(p)


def _v_decide(sv, styles: Styles, tracked: bool, choose, demote, args, refuse_undeletable: bool) -> Dict[str, Any]:
    """{"delete": [bi], "runs": {"bi": [own-run index]}, "simple": {"bi": [own simple-field index]}} — or _VRefuse."""
    pars = sv.pars
    whole: List[int] = []
    runs_of: Dict[int, List[Any]] = {}
    for bi, p in enumerate(pars):
        w, runs = choose(p, styles, args)
        if (w or runs) and _vp(p) is not None:
            raise _VRefuse("textbox", f"paragraph {bi} in a text box has something to remove")
        if w:
            whole.append(bi)
        elif runs:
            runs_of[bi] = runs
    groups: List[Tuple[Any, List[Any]]] = []
    for bi in whole:
        c = pars[bi].getparent()
        for g in groups:
            if g[0] is c:
                g[1].append(pars[bi])
                break
        else:
            groups.append((c, [pars[bi]]))
    at = {id(p): bi for bi, p in enumerate(pars)}
    kept: List[int] = []
    for c, chosen in groups:
        kept += [at[id(p)] for p in _v_keep(_v_blocks(c), chosen, tracked)]
    for bi in whole:
        if bi in kept:
            continue
        p = pars[bi]
        if any(q is not p for q in p.iter(W + "p")):
            raise _VRefuse("textbox", f"paragraph {bi} to delete holds a text box")
        why = v_deletable(p, tracked)
        if why:
            if refuse_undeletable:
                raise _VRefuse("not-deletable", f"paragraph {bi}: {why}")
            kept.append(bi)
    delete = sorted(bi for bi in whole if bi not in kept)
    for bi in kept:
        runs = demote(pars[bi], styles, args)
        if runs:
            runs_of[bi] = runs
    spans = _VSpans(sv.root)
    out: Dict[str, Any] = {"delete": delete, "runs": {}, "simple": {}}
    for bi in sorted(set(delete) | set(runs_of)):
        if bi in delete:
            continue
        p = pars[bi]
        runs, simples = _v_field_rules(p, runs_of[bi], spans, tracked)
        own, sims = _v_runs(p), _v_simples(p)
        if runs:
            out["runs"][str(bi)] = [next(k for k, x in enumerate(own) if x is r) for r in runs]
        if simples:
            out["simple"][str(bi)] = [next(k for k, x in enumerate(sims) if x is s) for s in simples]
    return out


def _v_same(derived: Dict[str, Any], planned: Optional[Dict[str, Any]]) -> bool:
    planned = planned or {"delete": [], "runs": {}, "simple": {}}
    norm = lambda d: (sorted(d.get("delete") or []),
                      {str(k): sorted(v) for k, v in (d.get("runs") or {}).items()},
                      {str(k): sorted(v) for k, v in (d.get("simple") or {}).items()})
    return norm(derived) == norm(planned)


def _v_check_outputs(ctx: Ctx, part: str, sv, ov, dec: Dict[str, Any], tracked: bool, author: Optional[str],
                     kind: str, errs: List[str]) -> Tuple[int, int]:
    """Check the output of one part against the derived decisions; (paragraphs deleted, runs/fields removed)."""
    old_ids = part_revision_ids(sv.root)
    delete = set(dec["delete"])
    if tracked:
        if len(ov.pars) != len(sv.pars):
            errs.append(f"{kind}: {part} has {len(ov.pars)} paragraphs, the source {len(sv.pars)} (a tracked deletion "
                        f"keeps them)")
            return 0, 0
        to_out = {bi: bi for bi in range(len(sv.pars))}
    else:
        kept = [bi for bi in range(len(sv.pars)) if bi not in delete]
        if len(ov.pars) != len(kept):
            errs.append(f"{kind}: {part} has {len(ov.pars)} paragraphs, expected {len(kept)} once {len(delete)} are "
                        f"deleted")
            return 0, 0
        to_out = {bi: k for k, bi in enumerate(kept)}
    for bi in sorted(delete):
        if tracked:
            check_deleted_paragraph(sv.pars[bi], ov.pars[bi], author, old_ids, f"{kind} {part} block {bi}", errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = bi
        else:
            ctx.deleted_src_blocks.setdefault(part, set()).add(bi)
    n_runs = 0
    for b in sorted(set(dec["runs"]) | set(dec["simple"]), key=int):
        bi = int(b)
        src_p = sv.pars[bi]
        own, sims = _v_runs(src_p), _v_simples(src_p)
        runs = [own[k] for k in dec["runs"].get(b, [])]
        simples = [sims[k] for k in dec["simple"].get(b, [])]
        n_runs += len(runs) + len(simples)
        items = v_items(src_p)
        if tracked:
            expected = tracked_delete(items, runs, author)
        else:
            expected = [it for it in drop(items, runs=runs)
                        if not any(e is s for s in simples for e in it.chain_els)]
        check_paragraph(src_p, ov.pars[to_out[bi]], expected, old_ids, f"{kind} {part} block {bi}", errs)
        ctx.verified_blocks.setdefault(part, {})[bi] = to_out[bi]
    return len(delete), n_runs


def _v_removed(ctx: Ctx, a: Dict[str, Any], errs: List[str], kind: str, choose, demote, args,
               refuse_undeletable: bool, default_scope: List[str]) -> str:
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    expected = a.get("expected") or {}
    n_del = n_runs = 0
    for part in ctx.scope_parts(a.get("scope") or default_scope):
        if part not in ctx.both:
            errs.append(f"{kind}: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        try:
            dec = _v_decide(sv, ctx.src_styles, tracked, choose, demote, args, refuse_undeletable)
        except _VRefuse as e:
            errs.append(f"{kind}: {part}: {e} — the op must refuse")
            continue
        except re.error as e:
            errs.append(f"{kind}: {part}: bad rule ({e})")
            continue
        if not _v_same(dec, expected.get(part)):
            errs.append(f"{kind}: {part}: removes {dec} here, the plan predicted {expected.get(part) or 'nothing'}")
            continue
        d, r = _v_check_outputs(ctx, part, sv, ov, dec, tracked, author, kind, errs)
        n_del += d
        n_runs += r
    for part in expected:
        if part not in ctx.scope_parts(a.get("scope") or default_scope):
            errs.append(f"{kind}: the plan removes something from {part}, outside the scope")
    return f"{kind}: {n_del} paragraph(s) deleted, {n_runs} run(s)/field(s) removed ({'tracked' if tracked else 'untracked'})"


def _removed_hidden_text(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    return _v_removed(ctx, a, errs, "removed_hidden_text", _v_choose_hidden, _v_demote_hidden, None, False,
                      V_DEFAULT_HIDDEN_SCOPE)


def _removed_notes(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    rules = list(a.get("rules") or [])
    run_rule = a.get("runs") or None
    if not rules and not run_rule:
        errs.append("removed_notes: no rule to check")
        return "removed_notes: no rule"
    return _v_removed(ctx, a, errs, "removed_notes", _v_choose_note, _v_demote_note, (rules, run_rule), True,
                      ["document"])


# ---- blank paragraphs ----------------------------------------------------------------------------------------------

def _v_levels(p, styles: Styles) -> List[Any]:
    out = [p.find(W + "pPr")]
    out += [st.find(W + "pPr") for st in styles.chain(_pstyle(p) or styles.default.get("paragraph"))]
    out.append(styles.doc_defaults_ppr())
    return out


def _v_numbered(p, styles: Styles) -> bool:
    for L in _v_levels(p, styles):
        nid = L.find(f"{W}numPr/{W}numId") if L is not None else None
        if nid is not None:
            return nid.get(W + "val") != "0"
    return False


def _v_boxed(p, styles: Styles) -> bool:
    for L in _v_levels(p, styles):
        if L is None:
            continue
        bdr = L.find(W + "pBdr")
        if bdr is not None and len([c for c in bdr if isinstance(c.tag, str)]):
            return True
        if L.find(W + "framePr") is not None:
            return True
    return False


def _v_blank(p, styles: Styles) -> bool:
    if _vp(p) is not None or p.find(f"{W}pPr/{W}sectPr") is not None:
        return False
    if any(isinstance(el.tag, str) and el.tag in V_REVISION_TAGS for el in p.iter()):
        return False
    for c in p:
        if not isinstance(c.tag, str) or c.tag in (W + "pPr", W + "proofErr"):
            continue
        if c.tag != W + "r":
            return False
        for k in c:
            if not isinstance(k.tag, str) or k.tag in (W + "rPr", W + "lastRenderedPageBreak"):
                continue
            if k.tag == W + "t" and (k.text or "").replace(" ", "").replace(" ", "") == "":
                continue
            return False
    return not _v_numbered(p, styles) and not _v_boxed(p, styles)


def _v_blank_decisions(sv, styles: Styles, mode: str, tracked: bool) -> List[int]:
    pars = sv.pars
    at = {id(p): bi for bi, p in enumerate(pars)}
    seen: List[Any] = []
    for p in pars:
        c = p.getparent()
        if _vp(p) is None and c is not None and all(x is not c for x in seen):
            seen.append(c)
    out: List[int] = []
    for c in seen:
        blocks = _v_blocks(c)
        blank = [b.tag == W + "p" and _v_blank(b, styles) for b in blocks]
        if mode == "all":
            chosen = [b for b, e in zip(blocks, blank) if e]
        else:
            chosen = [b for i, (b, e) in enumerate(zip(blocks, blank)) if e and i > 0 and blank[i - 1]]
        keep = _v_keep(blocks, chosen, tracked)
        out += [at[id(b)] for b in chosen if all(b is not k for k in keep)]
    return sorted(out)


def _removed_empty_paragraphs(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    mode = a.get("mode", "collapse")
    if mode not in ("collapse", "all"):
        errs.append(f"removed_empty_paragraphs: unknown mode {mode!r}")
        return "removed_empty_paragraphs: bad mode"
    tracked, author = bool(a.get("tracked", True)), a.get("author")
    expected = a.get("expected") or {}
    n = 0
    scope = ctx.scope_parts(a.get("scope") or ["document"])
    for part in scope:
        if part not in ctx.both:
            errs.append(f"removed_empty_paragraphs: {part} missing from the output")
            continue
        sv, ov = ctx.view(part)
        derived = _v_blank_decisions(sv, ctx.src_styles, mode, tracked)
        if derived != sorted(expected.get(part) or []):
            errs.append(f"removed_empty_paragraphs: {part}: deletes {derived} here, the plan predicted "
                        f"{expected.get(part) or []}")
            continue
        for bi in derived:
            why = v_deletable(sv.pars[bi], tracked)
            if why:
                errs.append(f"removed_empty_paragraphs: {part} block {bi}: {why} — the op must refuse")
        d, _ = _v_check_outputs(ctx, part, sv, ov, {"delete": derived, "runs": {}, "simple": {}}, tracked, author,
                                "removed_empty_paragraphs", errs)
        n += d
    for part in expected:
        if part not in scope:
            errs.append(f"removed_empty_paragraphs: the plan deletes paragraphs in {part}, outside the scope")
    return f"removed_empty_paragraphs: {n} blank paragraph(s) deleted ({mode}, {'tracked' if tracked else 'untracked'})"


REMOVAL_KINDS = {
    "removed_hidden_text": _removed_hidden_text,
    "removed_notes": _removed_notes,
    "removed_empty_paragraphs": _removed_empty_paragraphs,
}
