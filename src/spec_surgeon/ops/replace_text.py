"""Op: replace_text — a surgical wording change as a tracked revision (or, on explicit request, an untracked
one), in the body and/or headers/footers/footnotes/comments (was the v0.1.0 job ``tracked-replace``).

Contract (content mode): the replacement text is the user's approved wording, verbatim. The op
never generates text; it moves the user's characters into the right place in the right grammar.

Grammar implemented (all from the paid-for lessons):
* Plain text:   <w:del><w:r><w:delText>old</w:delText></w:r></w:del><w:ins><w:r><w:t>new</w:t></w:r></w:ins>
                deletions before insertions at the replacement point; rPr/attributes copied onto
                every fragment; xml:space="preserve" wherever edge whitespace exists.
* Text inside a pending insertion (someone's earlier redline): nest the deletion INSIDE the
  insertion and split the original insertion around it — the structure Word itself writes — so
  accept-all yields the new wording and reject-all returns to the pre-insertion original.
* Field instructions and cached field results are never edited (exception → Tier 2). A complex field is
  counted over the WHOLE part (``field_start_depths``): a TOC begins in one paragraph, shows its entries in the
  paragraphs that follow and ends paragraphs later — every one of those paragraphs is inside the field.
* Text inside a pending deletion is not live text and is never matched.
* Whole words unless ``whole_word: false`` (the rule of ``replace_table``): a match whose first (last) character is a
  letter, digit or ``_`` must not have one right before (after) it in what a reader sees — a tab, a line break, a
  picture or a field mark between keeps two words apart. "NFPA 13" leaves "NFPA 13R" and "NFPA 13D" alone.
* A tracked edit in a run that carries a pending tracked FORMATTING change (w:rPrChange) is an exception by
  default (``tracked-format``); when ``allow_tracked_format_split: true``, every split fragment (and the inserted
  run) keeps a copy of the ``w:rPrChange`` — as Word itself writes a split run — and every copy after the first
  gets a fresh ``w:id``; the plan predicts the exact ``rPrChange`` delta and V4 fails on any duplicated id.
* Paragraph-scoped run coalescing can optionally ignore non-visual rPr noise (``ignore_formatting_noise: true``)
  and tolerate whitespace/NBSP/dash variations (``flexible_whitespace: true``).
* Tabs and line breaks in the replacement (built-in batch 6, docs/BUILTIN_OPS_BATCH6_DESIGN.md §4.3): a ``\\t`` is
  written as a ``w:tab`` and a ``\\n`` as a ``w:br`` (a line break in the same paragraph) where they stand among the new
  characters, inside the new run (tracked) or after the edited ``w:t`` in its run (untracked) — the edit core's
  definition (``textedit.text_children`` / ``place_text``; the verifier's ``verify/edits.py`` with ``breaks``). They are
  no block text: V2's after-text leaves them out, and the block's structural counts after (V3) are declared. Any other
  control character is refused (``bad-params``).
"""
from __future__ import annotations

import copy
import getpass
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import (FULL_TEXT_TAGS, RevisionStamp, XML_SPACE, ancestor, block_struct_counts, clone_run_shell,
                     file_uses_date_utc, flexible_regex_from_literal, max_revision_id, merge_adjacent_runs, paragraphs,
                     split_run_at_text, to_del_text)
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .selectors import StyleSheet, check_paragraph_selector, paragraph_selected
from .textedit import TRANSPARENT, control_characters, place_text, placed_counts, shown_text, text_children


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


@dataclass
class Segment:
    t: etree._Element
    run: etree._Element
    start: int
    end: int
    ins: Optional[etree._Element]     # nearest w:ins ancestor (None if not inserted)
    ins_is_parent: bool               # run's direct parent is that ins
    in_field: bool                    # inside a field instruction or cached result
    gap_before: bool = True           # the paragraph's start or a non-letter child (tab, break, picture, field or
                                      # note mark …) stands between this w:t and the live characters before it


def _walk_runs(p: etree._Element):
    """Yield run for every w:r under the paragraph except those inside nested w:p
    (textboxes have their own paragraphs and are handled when they come up in iteration)."""
    for el in p.iter(W + "r"):
        q = el.getparent()
        nested = False
        while q is not None and q is not p:
            if q.tag == W + "p":
                nested = True
                break
            q = q.getparent()
        if nested:
            continue
        yield el


def field_start_depths(pars: List[etree._Element]) -> List[int]:
    """The complex-field nesting depth at the START of every paragraph of a part (document order). A field's begin
    and end can sit paragraphs apart (a TOC, an index); a paragraph that starts at depth > 0 lies inside such a
    field, and ``live_segments`` must know it — counting from zero in every paragraph would miss the whole middle
    of a multi-paragraph field. Every run of the paragraph counts, deleted ones included, as ``live_segments``
    counts them; nested paragraphs (textboxes) have their own runs and count for themselves."""
    out, depth = [], 0
    for p in pars:
        out.append(depth)
        for run in _walk_runs(p):
            for c in run:
                if c.tag == W + "fldChar":
                    t = c.get(W + "fldCharType")
                    if t == "begin":
                        depth += 1
                    elif t == "end":
                        depth = max(0, depth - 1)
    return out


def live_segments(p: etree._Element, start_depth: int = 0) -> Tuple[str, List[Segment]]:
    """Live text (what a reader sees with changes accepted: w:t not inside w:del) and its map. ``start_depth`` is
    the complex-field depth at the paragraph's start (``field_start_depths``); a paragraph inside a field that
    began in an earlier paragraph has every run in a field."""
    segs: List[Segment] = []
    pos = 0
    depth = start_depth  # field nesting depth (complex fields), continued from the paragraphs before
    gap = True           # no live character since the paragraph's start or the last non-letter run child
    for run in _walk_runs(p):
        if ancestor(run, W + "del", p) is not None:
            for c in run:
                if c.tag == W + "fldChar":
                    t = c.get(W + "fldCharType")
                    depth += 1 if t == "begin" else (-1 if t == "end" else 0)
            continue
        ins = ancestor(run, W + "ins", p)
        ins_is_parent = ins is not None and run.getparent() is ins
        in_fld_simple = ancestor(run, W + "fldSimple", p) is not None
        for c in run:
            if not isinstance(c.tag, str) or c.tag == W + "rPr" or c.tag in TRANSPARENT:
                continue
            if c.tag == W + "fldChar":
                t = c.get(W + "fldCharType")
                if t == "begin":
                    depth += 1
                elif t == "end":
                    depth = max(0, depth - 1)
                gap = True
            elif c.tag == W + "t":
                text = c.text or ""
                segs.append(Segment(t=c, run=run, start=pos, end=pos + len(text), ins=ins,
                                    ins_is_parent=ins_is_parent, in_field=(depth > 0 or in_fld_simple),
                                    gap_before=gap))
                pos += len(text)
                gap = gap and not text
            else:
                gap = True
    return "".join((s.t.text or "") for s in segs), segs


def _word(c: str) -> bool:
    return c.isalnum() or c == "_"


def _gap_at(segs: List[Segment], pos: int) -> bool:
    """A non-letter run child stands right before live position ``pos`` (0 < pos < len of the live text)."""
    for s in segs:
        if s.start <= pos < s.end:
            return pos == s.start and s.gap_before
    return True


def whole_word(live: str, segs: List[Segment], start: int, end: int) -> bool:
    """[start, end) of the live text is a whole word: a match whose first (last) character is a letter, digit or
    ``_`` has none right before (after) it in what a reader sees ("NFPA 13" is not whole inside "NFPA 13R"; "(2022)"
    is whole inside "x(2022)"). The live text holds only w:t characters, so a neighbour across a w:t edge counts
    only when nothing stands between (``Segment.gap_before``): "1.01<tab>SUMMARY" reads "1.01SUMMARY" here and
    "SUMMARY" is still whole."""
    if start > 0 and _word(live[start]) and _word(live[start - 1]) and not _gap_at(segs, start):
        return False
    if end < len(live) and _word(live[end - 1]) and _word(live[end]) and not _gap_at(segs, end):
        return False
    return True


def find_all(pattern, live: str, segs: List[Segment], whole: bool) -> list:
    """Every match of ``pattern`` in the live text, whole words only when ``whole``."""
    return [m for m in pattern.finditer(live) if not whole or whole_word(live, segs, m.start(), m.end())]


def inside_words(pattern, live: str, segs: List[Segment]) -> List[str]:
    """The longer word around every match that is not a whole word ("NFPA 13R" for "NFPA 13"): what whole words left
    alone, for the plan to say."""
    out = []
    for m in pattern.finditer(live):
        a, b = m.start(), m.end()
        if whole_word(live, segs, a, b):
            continue
        while a > 0 and _word(live[a - 1]) and not _gap_at(segs, a):
            a -= 1
        while b < len(live) and _word(live[b]) and not _gap_at(segs, b):
            b += 1
        out.append(live[a:b])
    return out


def full_text_map(p: etree._Element, skip_deleted: bool = False) -> Tuple[str, Dict[int, int]]:
    """Verifier-view text of a block (all text-bearing elements in document order) and a map from
    id(element) to its start offset. With skip_deleted=True this is the accept-all view (everything
    under w:del dropped). Implemented here independently of verify/."""
    parts = []
    offsets = {}
    pos = 0
    for el in p.iter(*FULL_TEXT_TAGS):
        q = el.getparent()
        nested = False
        deleted = False
        while q is not None and q is not p:
            if q.tag == W + "p":
                nested = True
                break
            if q.tag == W + "del":
                deleted = True
            q = q.getparent()
        if nested or (skip_deleted and deleted):
            continue
        text = el.text or ""
        offsets[id(el)] = pos
        parts.append(text)
        pos += len(text)
    return "".join(parts), offsets


def _inside_text(find: str, inside: List[str]) -> str:
    words = list(dict.fromkeys(inside))
    shown = ", ".join(repr(w) for w in words[:3]) + (", …" if len(words) > 3 else "")
    return (f"{find!r} also occurs {len(inside)}× inside longer words ({shown}), not changed — whole_word false "
            f"changes those too")


@register
class ReplaceText(Op):
    name = "replace_text"
    mode = "content"
    description = ("Replace approved wording with approved wording as a tracked change "
                   "(or untracked on request) in the body and/or headers, footers, footnotes, comments.")
    params_schema = {
        "type": "object",
        "properties": {
            "find": {"type": "string", "description": "Exact text to replace (live text; case-sensitive by default)."},
            "replace": {"type": "string", "description": "Approved replacement text, verbatim. Empty string = pure "
                                                         "deletion. \\t = a tab, \\n = a line break in the same "
                                                         "paragraph (written as Word's own tab and line break)."},
            "scope": {"type": "array", "items": {"type": "string",
                      "enum": ["document", "headers", "footers", "footnotes", "endnotes", "comments"]},
                      "default": ["document"]},
            "occurrences": {"type": "string", "enum": ["all", "first"], "default": "all"},
            "match_case": {"type": "boolean", "default": True},
            "whole_word": {"type": "boolean", "default": True,
                           "description": "If true (default), whole words only: a match whose first (last) character is "
                                          "a letter, digit or _ must not have one right before (after) it, so 'NFPA 13' "
                                          "leaves 'NFPA 13R' alone. false also matches inside longer words."},
            "flexible_whitespace": {"type": "boolean", "default": False,
                                    "description": "If true, spaces in 'find' match any run of spaces/NBSPs/tabs and '-' matches en/em-dashes."},
            "ignore_formatting_noise": {"type": "boolean", "default": False,
                                        "description": "If true, paragraph-local run merging ignores non-visual rPr noise (w:lang, w:noProof, w:webHidden, a redundant w:szCs or auto/black color) and drops the inert w:proofErr/w:lastRenderedPageBreak markers between runs it merges. Visual differences (fonts, size, bold, w:cs …) still refuse."},
            "allow_tracked_format_split": {"type": "boolean", "default": False,
                                           "description": "If true, allow splitting a run that carries w:rPrChange by assigning fresh revision IDs to split fragments."},
            "mode": {"type": "string", "enum": ["tracked", "untracked"], "default": "tracked"},
            "tracked": {"type": "boolean", "default": True,
                        "description": "EditSpec spelling of mode: true = tracked, false = untracked."},
            "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR env or OS user)."},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "If true, a file with no occurrence is a no-op instead of an exception."},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z",
                                "description": "w:date as Word writes it (local time + Z) or true UTC"},
        },
        "required": ["find", "replace"],
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": [
                      "document", "headers", "footers", "footnotes", "endnotes", "comments"]},
                      "description": "part kinds; same as the 'scope' param (either spelling)"},
            "paragraphs": {"type": ["string", "object"],
                           "description": "optional paragraph selector predicate to restrict replacement to matching paragraphs"},
        },
    }
    verification_profile = {
        "V2": "one BlockDiff per edited block (tracked: old text stays as delText, new text follows)",
        "V4": "+1 del per occurrence, +1 ins when the replacement is non-empty, +1 ins per re-wrapped suffix",
        "V5b": "tracked: reject-all view unchanged; untracked: enumerated-list mode",
        "V8": "accept-all view of every edited block predicted",
        "V10": "independently re-derived literal edits preserve every other paragraph item, property and container",
    }

    # ---- plan -----------------------------------------------------------------------------

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        find = params.get("find")
        replace = params.get("replace")
        if not find:
            raise JobException("'find' must be a non-empty string", "bad-params")
        if replace is None:
            raise JobException("'replace' is required (use \"\" for a pure deletion)", "bad-params")
        if not isinstance(replace, str) or control_characters(replace, breaks=True):
            raise JobException(f"the replacement {replace!r} holds a control character other than a tab (\\t) or a "
                               f"line break (\\n)", "bad-params")
        select = select or {}
        if select.get("scope") and params.get("scope") and list(select["scope"]) != list(params["scope"]):
            raise JobException("select.scope and params.scope disagree", "bad-params")
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"replace_text selects by scope/paragraphs only; unknown selector keys {sorted(unknown)}", "bad-params")
        para_pred = select.get("paragraphs")
        check_paragraph_selector(para_pred)
        scope = select.get("scope") or params.get("scope") or ["document"]
        occurrences = params.get("occurrences", "all")
        match_case = params.get("match_case", True)
        whole = params.get("whole_word", True)
        flex_ws = bool(params.get("flexible_whitespace", False))
        ignore_noise = bool(params.get("ignore_formatting_noise", False))
        allow_tf_split = bool(params.get("allow_tracked_format_split", False))
        mode = params.get("mode")
        if "tracked" in params:
            t_mode = "tracked" if params["tracked"] else "untracked"
            if mode is not None and mode != t_mode:
                raise JobException(f"params 'tracked' ({params['tracked']}) and 'mode' ({mode!r}) disagree", "bad-params")
            mode = t_mode
        mode = mode or "tracked"
        if mode not in ("tracked", "untracked"):
            raise JobException("mode must be 'tracked' or 'untracked'", "bad-params")
        if occurrences not in ("all", "first"):
            raise JobException(f"occurrences must be 'all' or 'first', got {occurrences!r}", "bad-params")
        if not isinstance(match_case, bool):
            raise JobException("match_case must be true or false", "bad-params")
        if not isinstance(whole, bool):
            raise JobException("whole_word must be true or false", "bad-params")
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()

        kinds = pkg.parts_by_kind()
        parts: List[str] = []
        for k in scope:
            if k not in kinds:
                raise JobException(f"unknown scope '{k}'", "bad-params")
            parts.extend(kinds[k])

        sel_dict: Dict[str, Any] = {"scope": scope}
        if para_pred is not None:
            sel_dict["paragraphs"] = para_pred
        plan = self.fragment(sel_dict, {**params, "author": author, "scope": scope, "mode": mode},
                             mode=("untracked" if mode == "untracked" else "content"))
        plan.untracked_diffs_expected = (mode == "untracked")
        flags = 0 if match_case else re.IGNORECASE
        rx_str = flexible_regex_from_literal(find) if flex_ws else re.escape(find)
        pattern = re.compile(rx_str, flags)
        sheet = StyleSheet(pkg) if para_pred is not None else None

        total = 0
        inside: List[str] = []             # the longer words whole words left alone (occurrences "all")
        edits: List[Dict[str, Any]] = []   # transform instructions, per paragraph
        stop = False
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            depths = field_start_depths(pars)
            for pi, p in enumerate(pars):
                if stop:
                    break
                if para_pred is not None:
                    btxt, _ = full_text_map(p)
                    if not paragraph_selected(para_pred, p, pi, btxt, sheet, all_pars=pars):
                        continue
                live, segs = live_segments(p, depths[pi])
                matches = find_all(pattern, live, segs, whole)
                if whole and occurrences == "all":
                    inside += inside_words(pattern, live, segs)
                if not matches:
                    continue
                merge_needed = False
                merge_noise = False
                work_p = p
                # Every target must lie inside a single w:t; else merge THIS paragraph's runs only.
                if not all(self._single_segment(segs, m.start(), m.end()) for m in matches):
                    work_p = copy.deepcopy(p)
                    merge_adjacent_runs(work_p, ignore_noise=False)
                    live2, segs2 = live_segments(work_p, depths[pi])
                    matches2 = find_all(pattern, live2, segs2, whole)
                    if live2 == live and len(matches2) == len(matches) and \
                            all(self._single_segment(segs2, m.start(), m.end()) for m in matches2):
                        merge_needed = True
                        segs, matches = segs2, matches2
                    elif ignore_noise:
                        work_p = copy.deepcopy(p)
                        merge_adjacent_runs(work_p, ignore_noise=True)
                        live3, segs3 = live_segments(work_p, depths[pi])
                        matches3 = find_all(pattern, live3, segs3, whole)
                        if live3 == live and len(matches3) == len(matches) and \
                                all(self._single_segment(segs3, m.start(), m.end()) for m in matches3):
                            merge_needed = True
                            merge_noise = True
                            segs, matches = segs3, matches3
                    if not merge_needed:
                        bad = [m for m in matches if not self._single_segment(segs, m.start(), m.end())]
                        raise JobException(
                            f"target {find!r} spans runs with different formatting in {part} paragraph {pi}; "
                            f"a paragraph-local merge cannot make it one run", "spans-runs",
                            {"part": part, "paragraph": pi, "live_text": live,
                             "offsets": [(m.start(), m.end()) for m in bad]})
                occ_list = []
                for m in matches:
                    seg = self._segment_at(segs, m.start())
                    if seg.in_field:
                        raise JobException(
                            f"target {find!r} in {part} paragraph {pi} is inside a field instruction or cached "
                            f"field result{' (a field that began in an earlier paragraph — a TOC or an index)' if depths[pi] else ''}; "
                            f"editing a cache is not a content edit: Word would put the old text back when it "
                            f"updates the field", "field-result",
                            {"part": part, "paragraph": pi, "live_text": live})
                    if mode == "tracked" and seg.run.find(W + "rPr/" + W + "rPrChange") is not None and not allow_tf_split:
                        raise JobException(
                            f"target {find!r} in {part} paragraph {pi} sits in a run carrying a pending tracked "
                            f"formatting change (w:rPrChange); splitting the run would duplicate that revision",
                            "tracked-format", {"part": part, "paragraph": pi, "live_text": live})
                    if seg.ins is not None and not seg.ins_is_parent:
                        raise JobException(
                            f"target {find!r} in {part} paragraph {pi} is inside a tracked insertion that wraps "
                            f"another container (hyperlink/sdt); the nested-split grammar needs a direct w:ins parent",
                            "nested-container", {"part": part, "paragraph": pi, "live_text": live})
                    occ_list.append((m.start(), m.end(), seg))
                    total += 1
                    if occurrences == "first":
                        stop = True
                        break
                self._predict_paragraph(plan, part, pi, p if not merge_needed else work_p, live, occ_list,
                                        find, replace, mode, allow_tf_split=allow_tf_split, source_p=p)
                edits.append({"part": part, "paragraph": pi, "merge": merge_needed,
                              "merge_noise": merge_noise, "allow_tf_split": allow_tf_split,
                              "live_offsets": [(s, e) for s, e, _ in occ_list]})
                plan.touches.append((part, str(pi), "text"))
                if part not in plan.intended_parts:
                    plan.intended_parts.append(part)
            if stop:
                break

        plan.property_assertions.append({
            "kind": "literal_replacement", "select": sel_dict, "params": dict(params),
            "author": author, "expected_edits": total,
            "edited_blocks": {part: [e["paragraph"] for e in edits if e["part"] == part]
                              for part in parts if any(e["part"] == part for e in edits)},
        })
        if inside:
            plan.flags.append(f"Whole words only: {_inside_text(find, inside)}.")
        if total == 0:
            if params.get("allow_missing"):
                plan.notes.append(f"{find!r} does not occur in scope {scope}; no-op.")
                return plan
            raise JobException(f"{find!r} not found in scope {scope}"
                               + (f" as a whole word; {_inside_text(find, inside)}" if inside else ""), "not-found",
                               {"scope": scope, "parts_searched": parts, "inside_longer_words": len(inside)})

        plan.transform_data = {
            "edits": edits,
            "author": author,
            "use_date_utc": file_uses_date_utc(pkg),
            "first_rev_id": max_revision_id(pkg) + 1,     # re-based by assign_revision_ids when ops are merged
            "date_convention": params.get("date_convention", "word-local-z"),
        }
        plan.rev_id_base = plan.transform_data["first_rev_id"]
        plan.rev_ids_needed = (plan.rev_delta.get("ins", 0) + plan.rev_delta.get("del", 0)
                               + plan.rev_delta.get("rPrChange", 0))
        plan.notes.append(f"{total} occurrence(s) of {find!r} → {replace!r} ({mode}) across {len(plan.intended_parts)} part(s).")
        if mode == "tracked":
            plan.notes.append(f"Author {author!r}; "
                              f"w16du:dateUtc {'written' if plan.transform_data['use_date_utc'] else 'not used by this file'}.")
        else:
            plan.flags.append("UNTRACKED edit at the user's request: no revision marks; V2's enumerated list is the proof.")
        return plan

    @staticmethod
    def _single_segment(segs: List[Segment], start: int, end: int) -> bool:
        for s in segs:
            if s.start <= start < s.end:
                return end <= s.end
        return False

    @staticmethod
    def _segment_at(segs: List[Segment], offset: int) -> Segment:
        for s in segs:
            if s.start <= offset < s.end:
                return s
        raise KeyError(offset)

    def _predict_paragraph(self, plan: PlanFragment, part: str, pi: int, p: etree._Element, live: str,
                           occ: List[Tuple[int, int, Segment]], find: str, replace: str, mode: str,
                           allow_tf_split: bool = False, source_p: Optional[etree._Element] = None) -> None:
        full, offsets = full_text_map(p)
        accepted, acc_offsets = full_text_map(p, skip_deleted=True)
        after = full
        shown = shown_text(replace)            # the replacement's block text: its tabs and line breaks are structure
        d_ins = d_del = d_rpr = 0
        # descending so earlier offsets stay valid
        for s, e, seg in sorted(occ, key=lambda x: -x[0]):
            local = s - seg.start
            fs = offsets[id(seg.t)] + local
            fe = fs + (e - s)
            as_ = acc_offsets[id(seg.t)] + local
            ae = as_ + (e - s)
            accepted = accepted[:as_] + shown + accepted[ae:]
            if mode == "untracked":
                after = after[:fs] + shown + after[fe:]
            else:
                after = after[:fe] + shown + after[fe:]         # old stays (as delText), new follows
                d_del += 1
                if replace:
                    d_ins += 1
                if allow_tf_split and seg.run.find(W + "rPr/" + W + "rPrChange") is not None:
                    t_text = seg.t.text or ""
                    local_end = local + (e - s)
                    run_children = [c for c in seg.run if c.tag != W + "rPr"]
                    idx_t = run_children.index(seg.t)
                    has_before = local > 0 or idx_t > 0
                    has_after = local_end < len(t_text) or idx_t < len(run_children) - 1
                    fragments_count = (1 if has_before else 0) + 1 + (1 if replace else 0) + (1 if has_after else 0)
                    d_rpr += max(0, fragments_count - 1)
                if seg.ins is not None:
                    # suffix insertion exists if anything of the original insertion follows the target
                    t_text = seg.t.text or ""
                    local_end = local + (e - s)
                    run_children = [c for c in seg.run if c.tag != W + "rPr"]
                    has_after_in_run = local_end < len(t_text) or run_children.index(seg.t) < len(run_children) - 1
                    ins_children = list(seg.ins)
                    has_after_in_ins = ins_children.index(seg.run) < len(ins_children) - 1
                    if has_after_in_run or has_after_in_ins:
                        d_ins += 1
        plan.accepted_views.append({"part": part, "block_index": pi, "accepted_view": accepted})
        # the tabs and line breaks every occurrence places join the block's structural children (V3); the found text
        # lies inside one w:t, so nothing structural leaves
        placed = placed_counts(replace)
        struct_after = None
        if placed:
            struct_after = block_struct_counts(source_p if source_p is not None else p)
            for k, n in placed.items():
                struct_after[k] = struct_after.get(k, 0) + n * len(occ)
        if after != full or struct_after is not None:
            # a pure tracked deletion leaves the block's full text unchanged (old text stays as delText); a replacement
            # of only tabs or line breaks declares its structure alone (V2 leaves it out, V3 checks it)
            plan.diffs.append(BlockDiff(part=part, block_index=pi, before=full, after=after,
                                        accepted_view=accepted, struct_after=struct_after,
                                        note=f"{len(occ)} occurrence(s); {'untracked' if mode == 'untracked' else 'tracked'}"))
        else:
            plan.notes.append(f"{part} block {pi}: pure deletion — block text unchanged (deleted text stays as w:delText); "
                              f"accepted view: {accepted!r}")
        plan.rev_delta["ins"] += d_ins
        plan.rev_delta["del"] += d_del
        if d_rpr:
            plan.rev_delta["rPrChange"] = plan.rev_delta.get("rPrChange", 0) + d_rpr

    # ---- apply ----------------------------------------------------------------------------

    def apply(self, pkg: DocxPackage, plan: PlanFragment) -> None:
        td = plan.transform_data
        if not td:
            return
        mode = plan.params["mode"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        by_part: Dict[str, List[dict]] = {}
        for e in td["edits"]:
            by_part.setdefault(e["part"], []).append(e)
        for part, edits in by_part.items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for e in edits:
                p = pars[e["paragraph"]]
                if e["merge"]:
                    merge_adjacent_runs(p, ignore_noise=bool(e.get("merge_noise", False)))
                for s, en in sorted(e["live_offsets"], key=lambda x: -x[0]):
                    live, segs = live_segments(p)
                    seg = self._segment_at(segs, s)
                    assert seg.end >= en, "target no longer within one w:t — internal error"
                    local = s - seg.start
                    old = live[s:en]
                    if mode == "untracked":
                        self._apply_untracked(seg, local, local + len(old), plan.params["replace"])
                    else:
                        self._apply_tracked(seg, local, local + len(old), plan.params["replace"], stamp, next_id,
                                            allow_tf_split=bool(e.get("allow_tf_split", False)))
            pkg.write_xml(part, root)

    @staticmethod
    def _apply_untracked(seg: Segment, ls: int, le: int, replace: str) -> None:
        text = seg.t.text or ""
        place_text(seg.t, text[:ls], replace, text[le:])     # its tabs and line breaks after that w:t, in its run

    @staticmethod
    def _apply_tracked(seg: Segment, ls: int, le: int, replace: str, stamp: RevisionStamp, next_id: List[int],
                       allow_tf_split: bool = False) -> None:
        run = seg.run
        parent = run.getparent()
        before, target, after = split_run_at_text(run, seg.t, ls, le)
        idx = parent.index(run)

        del_el = etree.Element(W + "del")
        stamp.apply(del_el, next_id[0]); next_id[0] += 1
        to_del_text(target)
        del_el.append(target)

        ins_el = None
        new_run = None
        if replace:
            ins_el = etree.Element(W + "ins")
            stamp.apply(ins_el, next_id[0]); next_id[0] += 1
            new_run = clone_run_shell(run)
            for el in text_children(replace):            # one w:t, or the stretches with their tabs and line breaks
                new_run.append(el)
            ins_el.append(new_run)

        if allow_tf_split:
            created_runs = [r for r in (before, target, new_run, after) if r is not None]
            for extra_r in created_runs[1:]:
                chg = extra_r.find(W + "rPr/" + W + "rPrChange")
                if chg is not None:
                    chg.set(W + "id", str(next_id[0]))
                    next_id[0] += 1

        if seg.ins is None or parent is not seg.ins:
            # plain context: [before] [del old] [ins new] [after], deletions before insertions
            parent.remove(run)
            pos = idx
            for el in (before, del_el, ins_el, after):
                if el is not None:
                    parent.insert(pos, el)
                    pos += 1
            return

        # nested context: parent IS the original w:ins
        orig_ins = parent
        post = list(orig_ins)[idx + 1:]
        orig_ins.remove(run)
        pos = idx
        if before is not None:
            orig_ins.insert(pos, before); pos += 1
        orig_ins.insert(pos, del_el)
        grand = orig_ins.getparent()
        gpos = grand.index(orig_ins) + 1
        if ins_el is not None:
            grand.insert(gpos, ins_el); gpos += 1
        if after is not None or post:
            suffix_ins = etree.Element(W + "ins")
            for k, v in orig_ins.attrib.items():
                suffix_ins.set(k, v)
            suffix_ins.set(W + "id", str(next_id[0])); next_id[0] += 1
            if after is not None:
                suffix_ins.append(after)
            for el in post:
                orig_ins.remove(el)
                suffix_ins.append(el)
            grand.insert(gpos, suffix_ins)

    # ---- describe -------------------------------------------------------------------------

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        scope = select.get("scope") or params.get("scope")
        if scope:
            select["scope"] = scope
        find, replace = params.get("find"), params.get("replace")
        if find:
            target = ("the first occurrence of " if params.get("occurrences") == "first" else "") + prose.quote(find)
            if replace == "":
                head = f"Delete {target}"
            elif replace is None:
                head = f"Replace {target} with the approved wording"
            else:
                head = f"Replace {target} with {prose.typed(replace)}"    # its tabs and line breaks named
        else:
            head = "Replace the wording to be found with the approved wording"
        hows: List[str] = []
        if params.get("match_case") is False:
            hows.append("ignoring case")
        if params.get("whole_word") is False:
            hows.append("even inside longer words")
        if params.get("flexible_whitespace"):
            hows.append("allowing any spacing or dash")
        if params.get("ignore_formatting_noise"):
            hows.append("even where invisible formatting differences split the wording")
        if params.get("allow_tracked_format_split"):
            hows.append("even inside text carrying a pending tracked formatting change")
        tracked = bool(params["tracked"]) if "tracked" in params else params.get("mode") != "untracked"
        if tracked:
            tail = "shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        else:
            tail = "without tracked changes"
        first = f"{head} {prose.where(select)}" + (", " + prose.join(hows) if hows else "") + f", {tail}."
        second = "Files in which it does not occur are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, second)
