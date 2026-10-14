"""Op: delete_paragraphs — delete selected paragraphs, either as a tracked deletion (runs wrapped in w:del
with w:delText, paragraph mark marked with w:pPr/w:rPr/w:del) or untracked (w:p removed from the tree).
"""
from __future__ import annotations

import getpass
import os
from typing import Any, Dict, List

from lxml import etree

from ..ooxml import ( PPR_ORDER, RPR_ORDER, RevisionStamp, block_full_text,
                     count_revisions, file_uses_date_utc, insert_in_order, mark_rpr, max_revision_id,
                     paragraphs, to_del_text )
from ..package import W, DocxPackage
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .slots import nearest_p


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


# Paragraph children that carry no content and may stay in place (outside the w:del wrapper) when the paragraph's
# runs are tracked-deleted: bookmark anchors (Word's _GoBack is in most saved files) and proofing markers.
INERT_P_CHILDREN = (W + "bookmarkStart", W + "bookmarkEnd", W + "proofErr")


@register
class DeleteParagraphs(Op):
    name = "delete_paragraphs"
    mode = "content"
    description = ("Delete selected paragraphs as a tracked change (runs wrapped in w:del and paragraph mark "
                   "marked deleted) or untracked on request.")
    params_schema = {
        "type": "object",
        "properties": {
            "tracked": {"type": "boolean", "default": True,
                        "description": "true = tracked deletion (content mode); false = untracked removal."},
            "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)."},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "If true, matching 0 paragraphs is a no-op instead of an exception."},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": ["document"]},
            "paragraphs": {"type": "object", "description": "Targeted paragraph selector predicate (required)."},
        },
    }
    verification_profile = {
        "V2": "untracked: deleted blocks listed with after=None; tracked: block full text unchanged (w:delText)",
        "V4": "tracked: +1 del_mark per paragraph and +1 del per non-empty paragraph; untracked: -(dropped revisions)",
        "V5b": "tracked: reject-all view equals source; untracked: enumerated-list mode",
        "V8": "tracked: accept-all view of every deleted block is empty",
        "V10": "deleted_paragraphs assertion verifies selector count, mark revision, and residual identity",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"unknown selector key(s) {sorted(unknown)}", "bad-params")
        para_pred = select.get("paragraphs")
        check_paragraph_selector(para_pred)
        if not is_targeted(select):
            raise JobException("delete_paragraphs requires a targeted 'paragraphs' selector (refusing to delete all)",
                               "bad-params")
        tracked = bool(params.get("tracked", True))
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        scope = select.get("scope") or ["document"]
        if isinstance(scope, str):
            scope = [scope]
        parts = scope_parts(pkg, scope)
        sheet = StyleSheet(pkg)

        frag = self.fragment({"scope": scope, "paragraphs": para_pred},
                             {**params, "tracked": tracked, "author": author},
                             mode=("content" if tracked else "untracked"))
        frag.untracked_diffs_expected = not tracked

        targets_by_part: Dict[str, List[int]] = {}
        total = 0
        d_del = 0
        d_del_mark = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            matched: List[int] = []
            for bi, p in enumerate(pars):
                btxt = block_full_text(p)
                if not paragraph_selected(para_pred, p, bi, btxt, sheet, all_pars=pars):
                    continue
                if nearest_p(p) is not None:
                    raise JobException(f"{part} paragraph {bi} is inside a textbox; not supported", "textbox")
                if p.find(f"{W}pPr/{W}sectPr") is not None:
                    raise JobException(f"{part} paragraph {bi} carries a section break (w:sectPr); refusing to delete",
                                       "section-break", {"part": part, "paragraph": bi})
                if p.find(f".//{W}fldChar") is not None or p.find(f".//{W}fldSimple") is not None:
                    raise JobException(f"{part} paragraph {bi} contains a field; refusing to delete",
                                       "field-result", {"part": part, "paragraph": bi})
                if tracked:
                    if count_revisions(p):
                        raise JobException(f"{part} paragraph {bi} already carries pending revisions; accept/reject "
                                           f"them first or use untracked deletion", "existing-revisions",
                                           {"part": part, "paragraph": bi})
                    non_r = [c for c in p if c.tag not in (W + "pPr", W + "r") and c.tag not in INERT_P_CHILDREN]
                    if non_r:
                        raise JobException(f"{part} paragraph {bi} contains non-run elements "
                                           f"{[etree.QName(c).localname for c in non_r]}; not supported in tracked mode "
                                           f"(bookmarks and proofing markers are fine; hyperlinks, fields, content "
                                           f"controls and comment anchors are not)",
                                           "structure", {"part": part, "paragraph": bi})
                    runs = p.findall(W + "r")
                    if runs:
                        d_del += 1
                    d_del_mark += 1
                    frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": ""})
                    frag.touches.append((part, str(bi), "text"))
                else:
                    revs = count_revisions(p)
                    for rk, rv in revs.items():
                        frag.rev_delta[rk] = frag.rev_delta.get(rk, 0) - rv
                    if revs:
                        frag.flags.append(f"{part} block {bi}: dropped pending revisions {revs} in deleted paragraph")
                    # account for structural children removed with the deleted block
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=btxt, after=None,
                                                note="untracked paragraph deletion"))
                matched.append(bi)
                total += 1
            if matched:
                if not tracked:
                    # Verify no table cell loses all of its paragraphs (OOXML schema requires >=1 w:p per w:tc)
                    matched_els = {id(pars[i]) for i in matched}
                    for tc in root.iter(W + "tc"):
                        tc_pars = [ch for ch in tc if ch.tag == W + "p"]
                        if tc_pars and all(id(ch) in matched_els for ch in tc_pars):
                            raise JobException(f"{part}: deleting all paragraphs of a table cell would produce "
                                               f"invalid OOXML", "table-cell", {"part": part})
                    frag.touches.append((part, "*", "text"))
                targets_by_part[part] = matched
                if part not in frag.intended_parts:
                    frag.intended_parts.append(part)

        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append(f"0 paragraphs matched in {scope}; no-op.")
                return frag
            raise JobException(f"no paragraphs matched selector {para_pred} in {scope}", "not-found",
                               {"scope": scope, "paragraphs": para_pred})

        if tracked:
            frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + d_del
            if d_del_mark:
                frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + d_del_mark
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = d_del + d_del_mark
        else:
            frag.flags.append(f"UNTRACKED deletion of {total} paragraph(s) at the user's request.")

        frag.geometry_reference = "none"
        frag.geometry_reason = "deleting paragraph(s) reflows following content"
        frag.property_assertions.append({
            "kind": "deleted_paragraphs",
            "scope": scope,
            "select": {"paragraphs": para_pred},
            "tracked": tracked,
            "mark_deleted": True,
            "author": author,
            "expected_paragraphs": total,
        })
        frag.transform_data = {
            "targets": targets_by_part,
            "tracked": tracked,
            "author": author,
            "use_date_utc": file_uses_date_utc(pkg),
            "first_rev_id": frag.rev_id_base,
            "date_convention": params.get("date_convention", "word-local-z"),
        }
        frag.notes.append(f"delete_paragraphs: {total} paragraph(s) ({'tracked' if tracked else 'untracked'}) "
                          f"across {len(frag.intended_parts)} part(s).")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        tracked = td["tracked"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td.get("first_rev_id", 1)]

        for part, idxs in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for bi in sorted(idxs, reverse=True):
                p = pars[bi]
                if not tracked:
                    p.getparent().remove(p)
                    continue
                # Tracked: mark the paragraph mark deleted, and wrap the runs in one w:del with w:delText; inert
                # children (bookmark anchors, proofing markers) stay where they are, after the wrapper.
                mrpr = mark_rpr(p, create=True)
                del_mark = etree.Element(W + "del")
                stamp.apply(del_mark, next_id[0]); next_id[0] += 1
                insert_in_order(mrpr, del_mark, RPR_ORDER)
                runs = [c for c in list(p) if c.tag == W + "r"]
                if runs:
                    first_pos = list(p).index(runs[0])
                    del_wrap = etree.Element(W + "del")
                    stamp.apply(del_wrap, next_id[0]); next_id[0] += 1
                    for r in runs:
                        p.remove(r)
                        to_del_text(r)
                        del_wrap.append(r)
                    p.insert(first_pos, del_wrap)
            pkg.write_xml(part, root)
