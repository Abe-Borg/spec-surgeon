"""Op: remove_empty_paragraphs (built-in batch 2, item 17) — remove blank paragraphs, or collapse each run of blank
lines to one (docs/BUILTIN_OPS_BATCH2_DESIGN.md §8). The verifier derives every decision again with its own code
(verify/removal.py, kind ``removed_empty_paragraphs``): keep the two identical (CLAUDE.md lists the definition).

**Empty** (``is_empty``): a paragraph that is not inside a text box, carries no section break, has no numbering in effect
(its own ``w:numPr`` with a ``w:numId`` decides — 0 switches numbering off —, else the first ``w:numId`` up its style
chain, else docDefaults'), no paragraph border (a ``w:pBdr`` with a border in it) and no frame (``w:framePr``) — its own
or its style chain's or docDefaults' —, no revision of any kind, and holds nothing but ``w:pPr``, proofing marks and runs
of ``w:rPr``, ``w:lastRenderedPageBreak`` and ``w:t`` of spaces and no-break spaces. A bookmark, a tab, a break, a field, a
picture or a comment mark makes it not empty.

**Which go**, per container (the paragraph's parent: body, cell, header, footer, note, comment, block content control)
over its blocks (``w:p``, ``w:tbl``, ``w:sdt``, ``w:customXml``, ``w:altChunk``): ``all`` — every empty paragraph;
``collapse`` (the default) — every empty paragraph whose previous block is an empty paragraph too (each run of blank
lines keeps its first). Then ``ops/hidden.keep_rules`` keeps some: the first paragraph between two tables the deletions
would join; the container's last block when tracked (Word cannot delete the final paragraph mark), or — untracked — when
the rest would leave the container empty or ending in something other than a paragraph.

Deleted as a tracked change by default (every run in a ``w:del``, the mark deleted — ``ops/removal.py``), or untracked.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..ooxml import RevisionStamp, count_revisions, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage, W
from . import register
from .base import JobException, Op, PlanFragment
from .hidden import CLEANUP_PARAMS, blocks_of, keep_rules, nearest_p
from .removal import deletable, delete_paragraph, plan_paragraph_deletion
from .replace_table import default_author, resolve_scope, tracked_of
from .selectors import SCOPE_KINDS, StyleSheet, scope_parts

MODES = ("collapse", "all")
BLANK = "  "


def _style_levels(p, sheet: StyleSheet) -> List[Any]:
    """The paragraph's own pPr, then its style chain's (nearest first), then docDefaults' pPr."""
    out = [p.find(W + "pPr")]
    out += [st.find(W + "pPr") for st in sheet.lineage(sheet.paragraph_style_id(p))]
    if sheet.root is not None:
        out.append(sheet.root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr"))
    return out


def numbered(p, sheet: StyleSheet) -> bool:
    for ppr in _style_levels(p, sheet):
        nid = ppr.find(f"{W}numPr/{W}numId") if ppr is not None else None
        if nid is not None:
            return nid.get(W + "val") != "0"
    return False


def bordered_or_framed(p, sheet: StyleSheet) -> bool:
    for ppr in _style_levels(p, sheet):
        if ppr is None:
            continue
        b = ppr.find(W + "pBdr")
        if (b is not None and any(isinstance(c.tag, str) for c in b)) or ppr.find(W + "framePr") is not None:
            return True
    return False


def is_empty(p, sheet: StyleSheet) -> bool:
    if nearest_p(p) is not None:
        return False
    ppr = p.find(W + "pPr")
    if ppr is not None and ppr.find(W + "sectPr") is not None:
        return False
    if count_revisions(p):
        return False
    for c in p:
        if not isinstance(c.tag, str) or c.tag in (W + "pPr", W + "proofErr"):
            continue
        if c.tag != W + "r":
            return False
        for k in c:
            if not isinstance(k.tag, str) or k.tag in (W + "rPr", W + "lastRenderedPageBreak"):
                continue
            if k.tag == W + "t" and not (k.text or "").strip(BLANK):
                continue
            return False
    return not numbered(p, sheet) and not bordered_or_framed(p, sheet)


def blank_paragraphs(root, sheet: StyleSheet, mode: str, tracked: bool) -> Dict[str, Any]:
    """{"delete": [paragraph elements], "kept": n} — the module docstring's rules over every container of the part."""
    containers: List[Any] = []
    seen = set()
    for p in paragraphs(root):
        c = p.getparent()
        if nearest_p(p) is None and c is not None and id(c) not in seen:
            seen.add(id(c))
            containers.append(c)
    delete: List[Any] = []
    kept = 0
    for c in containers:
        blocks = blocks_of(c)
        empty = [b for b in blocks if b.tag == W + "p" and is_empty(b, sheet)]
        es = set(empty)
        if mode == "all":
            chosen = empty
        else:
            chosen = [b for i, b in enumerate(blocks) if b in es and i > 0 and blocks[i - 1] in es]
        keep = keep_rules(blocks, chosen, tracked)
        kept += len(keep)
        delete += [b for b in chosen if b not in keep]
    return {"delete": delete, "kept": kept}


@register
class RemoveEmptyParagraphs(Op):
    name = "remove_empty_paragraphs"
    mode = "content"
    description = ("Remove blank paragraphs: 'collapse' (default) leaves one blank line where several stand together, "
                   "'all' removes every blank paragraph. A paragraph with a number or bullet, a border, a bookmark, a "
                   "picture, a field, a tab or a break is not blank; a cell, the document and a header always keep a "
                   "last paragraph, and two tables are never joined. Tracked (default) or untracked.")
    params_schema = {"type": "object", "properties": {
        "mode": {"type": "string", "enum": list(MODES), "default": "collapse",
                 "description": "collapse = each run of blank lines keeps its first; all = every blank paragraph goes"},
        **CLEANUP_PARAMS,
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document)"}}}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document)"}}}
    verification_profile = {
        "V2": "untracked: each deleted blank paragraph listed (after=None); tracked: block texts unchanged",
        "V4": "tracked: +1 del per run and +1 del_mark per deleted paragraph",
        "V8": "tracked: every deleted paragraph's accepted view empty",
        "V10": "removed_empty_paragraphs re-derives the blank paragraphs, the collapse and the container rules with its "
               "own code, and checks each deletion (tracked: item by item; untracked: gone, nothing else changed)",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException(f"remove_empty_paragraphs selects by scope only; unknown {sorted(set(select) - {'scope'})}",
                               "bad-params")
        mode = params.get("mode", "collapse")
        if mode not in MODES:
            raise JobException(f"mode must be one of {list(MODES)}, got {mode!r}", "bad-params")
        scope = resolve_scope(select, params, ["document"])
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        allow_missing = params.get("allow_missing", True)
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        author = params.get("author") or default_author()
        frag = self.fragment({"scope": scope}, {**params, "mode": mode, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        sheet = StyleSheet(pkg)
        data: Dict[str, List[int]] = {}
        kept = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            index = {id(p): bi for bi, p in enumerate(pars)}
            got = blank_paragraphs(root, sheet, mode, tracked)
            kept += got["kept"]
            bis = sorted(index[id(p)] for p in got["delete"])
            for bi in bis:
                deletable(pars[bi], part, bi, tracked)
                plan_paragraph_deletion(frag, part, bi, pars[bi], tracked, "blank paragraph deleted")
            if bis:
                data[part] = bis
                if not tracked:
                    frag.touches.append((part, "*", "text"))
        frag.property_assertions.append({"kind": "removed_empty_paragraphs", "scope": scope, "mode": mode,
                                         "tracked": tracked, "author": author, "expected": data})
        n = sum(len(v) for v in data.values())
        if not n:
            if allow_missing:
                frag.notes.append(f"remove_empty_paragraphs: no blank paragraph to remove in {scope} ({mode}): nothing "
                                  f"to do.")
                return frag
            raise JobException(f"remove_empty_paragraphs: no blank paragraph to remove in {scope} ({mode})",
                               "not-found", {"scope": scope})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(v for v in frag.rev_delta.values() if v > 0)
        frag.geometry_reference = "none"
        frag.geometry_reason = "deleting blank paragraphs moves the content after them up"
        frag.transform_data = {"delete": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        frag.notes.append(f"remove_empty_paragraphs ({mode}, {'tracked' if tracked else 'untracked'}): {n} blank "
                          f"paragraph(s) deleted in {len(data)} part(s)"
                          + (f"; {kept} kept (a cell, the document or a header ends with it, or it stands between two "
                             f"tables)" if kept else "") + ".")
        if not tracked:
            frag.flags.append(f"UNTRACKED deletion of {n} blank paragraph(s) at the user's request.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("delete"):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, bis in td["delete"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for p in [pars[bi] for bi in bis]:
                delete_paragraph(p, td["tracked"], stamp, next_id)
            pkg.write_xml(part, root)
