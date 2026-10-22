"""Op: filter_tagged_paragraphs — keep or delete the paragraphs tagged for a system type (wet, dry, preaction, clean
agent …) and take the tag off the ones that stay (design note §4).

Tags (CLAUDE.md "Tagged paragraphs", both sides): ``tag_regex`` is searched in each paragraph's run text (ops/textedit.py);
no match → untagged, always kept. The tag is the named group ``tag``, else group 1, else the whole match; split by
``tag_separator`` (default ``\\s*[/,;]\\s*``) it gives the paragraph's systems, compared casefolded with whitespace
collapsed. With ``keep`` a tagged paragraph stays when one of its systems is listed; with ``delete`` it stays when one of
its systems is NOT listed ("[WET/DRY]" survives deleting DRY). A paragraph that stays loses the regex's whole match when
``strip_tags`` (default true) — an edit of the core; one that goes is deleted:

* tracked — every run that is a direct child of the paragraph goes into its own ``w:del`` (``w:t`` → ``w:delText``) and
  the paragraph mark gets a ``w:del``; bookmarks and proofing marks stay where they are (nothing moves); a paragraph
  holding pending revisions or other content (hyperlinks, content controls, comment anchors) is refused;
* untracked — the paragraph is removed (its pending revisions with it, flagged); a table cell's last paragraph is refused.
  A bookmark, permission, move or custom-XML range mark in it, some mark of whose pair lies in a paragraph that stays,
  stays where the paragraph was (``removal.kept_marks``; flagged) — a pair wholly in deleted paragraphs goes.

Both refuse a paragraph that carries a section break, holds a field, or holds a text box (``section-break``,
``field-result``, ``textbox``). The verifier derives the tags and decisions again (verify/textops.py, kind
``tagged_paragraphs``).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, block_full_text, count_revisions, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage, W
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .removal import deletable, delete_paragraph, kept_marks, range_marks  # noqa: F401 — deletable is re-exported
from .replace_table import COMMON_PARAMS, default_author, resolve_scope, tracked_of
from .selectors import SCOPE_KINDS, scope_parts
from .slots import field_runs
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph, shown

DEFAULT_SEPARATOR = r"\s*[/,;]\s*"


def norm_system(s: str) -> str:
    return " ".join(s.split()).casefold()


def tag_of(m) -> str:
    if "tag" in m.re.groupindex:
        return m.group("tag") or ""
    return (m.group(1) if m.re.groups else m.group(0)) or ""


def systems_of(tag: str, separator) -> List[str]:
    return [norm_system(s) for s in separator.split(tag) if s.strip()]


def stays(systems: List[str], keep: List[str], delete: List[str]) -> bool:
    if keep:
        return any(s in keep for s in systems)
    return any(s not in delete for s in systems)


@register
class FilterTaggedParagraphs(Op):
    name = "filter_tagged_paragraphs"
    mode = "content"
    description = ("Keep or delete the paragraphs tagged for a system type (e.g. [WET] [DRY] [PREACTION] [CLEAN AGENT] "
                   "at the start of a paragraph): 'keep' the listed systems or 'delete' them; a paragraph tagged for "
                   "several systems stays while one of them stays; untagged paragraphs always stay; the tag is taken off "
                   "the paragraphs that stay. Tracked (default) or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "tag_regex": {"type": "string", "description": "a Python regex finding the tag in a paragraph (named group "
                                                           "'tag' or group 1 = the systems); include the space after it "
                                                           "so stripping leaves no gap, e.g. ^\\[(?P<tag>[A-Z /]+)\\]\\s*"},
            "keep": {"type": "array", "items": {"type": "string"}, "description": "systems to keep (all others go)"},
            "delete": {"type": "array", "items": {"type": "string"}, "description": "systems to delete (others stay)"},
            "tag_separator": {"type": "string", "default": DEFAULT_SEPARATOR,
                              "description": "regex splitting one tag into several systems ('WET/DRY')"},
            "strip_tags": {"type": "boolean", "default": True},
            **COMMON_PARAMS,
        },
        "required": ["tag_regex"],
    }
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document)"}}}
    verification_profile = {
        "V2": "untracked: each deleted paragraph listed (after=None), each stripped one changed; tracked: block texts "
              "unchanged (w:delText), accepted views predicted",
        "V4": "tracked: +1 del per run of a deleted paragraph, +1 del_mark per deleted paragraph, +1 del per run a tag "
              "touches; untracked: −(revisions in removed paragraphs)",
        "V10": "tagged_paragraphs re-derives every tag and decision, checks each deleted paragraph item by item "
               "(tracked) or its absence (untracked), and each stripped paragraph item by item",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})

        def systems(key: str) -> List[str]:
            raw = params.get(key)
            return [s for s in raw if isinstance(s, str) and s.strip()] if isinstance(raw, list) else []

        keep, delete = systems("keep"), systems("delete")
        if keep:
            head = (f"Keep the paragraphs tagged for {prose.join((prose.quote(s) for s in keep), 'or')} and delete "
                    f"those tagged only for other systems")
        elif delete:
            head = (f"Delete the paragraphs tagged for {prose.join((prose.quote(s) for s in delete), 'or')} and keep "
                    f"those tagged for any other system as well")
        else:
            head = "Keep or delete the paragraphs tagged for the systems to be named"
        rx = params.get("tag_regex")
        tag = f" (the tag found by the pattern {prose.quote(rx)})" if isinstance(rx, str) and rx else ""
        scope = select.get("scope") or params.get("scope") or ["document"]
        where = prose.scope_words(scope) or "in the document body"
        tails = ["leaving the tags on the paragraphs that stay" if params.get("strip_tags") is False
                 else "taking the tag off the paragraphs that stay"]
        sep = params.get("tag_separator")
        if isinstance(sep, str) and sep and sep != DEFAULT_SEPARATOR:
            tails.append(f"splitting a tag that names several systems at the pattern {prose.quote(sep)}")
        if params.get("tracked") is False:
            tracked = "without tracked changes"
        else:
            tracked = "shown as tracked changes" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        first = prose.sentence(f"{head}{tag} {where}, {prose.join(tails)}, {tracked}")
        extras = ["untagged paragraphs stay"]
        if params.get("allow_missing") is True:
            extras.append("files with no tagged paragraph are left unchanged")
        return first + " " + prose.sentence(prose.join(extras))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException(f"filter_tagged_paragraphs selects by scope only", "bad-params")
        try:
            rx = re.compile(params.get("tag_regex") or "")
            sep = re.compile(params.get("tag_separator", DEFAULT_SEPARATOR))
        except re.error as e:
            raise JobException(f"bad regex ({e})", "bad-params")
        if not params.get("tag_regex"):
            raise JobException("'tag_regex' is required", "bad-params")
        keep = [norm_system(s) for s in params.get("keep") or []]
        delete = [norm_system(s) for s in params.get("delete") or []]
        if bool(keep) == bool(delete):
            raise JobException("say which systems to 'keep' or which to 'delete' (one of the two)", "bad-params")
        strip = params.get("strip_tags", True)
        if not isinstance(strip, bool):
            raise JobException("strip_tags must be true or false", "bad-params")
        scope = resolve_scope(select, params, ["document"])
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        author = params.get("author") or default_author()
        frag = self.fragment({"scope": scope}, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        edits: Dict[str, Dict[str, List[List[Any]]]] = {}
        gone: Dict[str, List[int]] = {}
        marks: Dict[str, Dict[str, List[int]]] = {}      # untracked: part → {deleted paragraph: its kept range marks}
        seen: Dict[str, List[int]] = {}                  # system → [kept, deleted]
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            in_field = field_runs(root)
            removed: List[int] = []
            for bi, p in enumerate(pars):
                rt = RunText(p)
                m = rx.search(rt.text)
                if m is None:
                    continue
                where = f"{part} paragraph {bi}"
                systems = systems_of(tag_of(m), sep)
                if not systems:
                    raise JobException(f"{where}: the tag in {shown(rt.text)[:80]!r} names no system", "structure",
                                       {"where": where})
                if nearest_p(p) is not None:
                    raise JobException(f"{where} is inside a text box (Word keeps two copies of it)", "textbox",
                                       {"where": where})
                stay = stays(systems, keep, delete)
                for s in systems:
                    seen.setdefault(s, [0, 0])[0 if stay else 1] += 1
                if stay:
                    if strip and m.end() > m.start():
                        done = plan_paragraph(frag, part, bi, p, [Edit(m.start(), m.end(), "")], tracked, in_field,
                                              where, note="tag taken off")
                        if done:
                            edits.setdefault(part, {})[str(bi)] = edits_to_data(done)
                    continue
                deletable(p, part, bi, tracked)
                removed.append(bi)
                if tracked:
                    runs = [c for c in p if isinstance(c.tag, str) and c.tag == W + "r"]
                    frag.rev_delta["del"] = frag.rev_delta.get("del", 0) + len(runs)
                    frag.rev_delta["del_mark"] = frag.rev_delta.get("del_mark", 0) + 1
                    frag.accepted_views.append({"part": part, "block_index": bi, "accepted_view": ""})
                    frag.touches.append((part, str(bi), "text"))
                else:
                    for k, v in count_revisions(p).items():
                        frag.rev_delta[k] = frag.rev_delta.get(k, 0) - v
                        frag.flags.append(f"{where}: its pending revisions ({k} ×{v}) go with the deleted paragraph")
                    frag.diffs.append(BlockDiff(part=part, block_index=bi, before=block_full_text(p), after=None,
                                                note="tagged paragraph deleted (untracked)"))
            if removed:
                gone[part] = removed
                if not tracked:
                    chosen = {id(pars[i]) for i in removed}
                    for tc in root.iter(W + "tc"):
                        cell = [c for c in tc if c.tag == W + "p"]
                        if cell and all(id(c) in chosen for c in cell):
                            raise JobException(f"{part}: deleting every paragraph of a table cell would break the "
                                               f"table", "table-cell", {"part": part})
                    frag.touches.append((part, "*", "text"))
                    # the bookmarks and range marks running out of a deleted paragraph into what stays stay in its place
                    at = {id(pars[i]): i for i in removed}
                    for p, held in kept_marks(root, [pars[i] for i in removed]).items():
                        ranges = range_marks(p)
                        marks.setdefault(part, {})[str(at[id(p)])] = sorted(ranges.index(m) for m in held)
                        frag.flags.append(f"{part} paragraph {at[id(p)]}: {len(held)} bookmark or range mark(s) in the "
                                          f"deleted paragraph run on into text that stays; each stays where the "
                                          f"paragraph was, so its bookmark keeps both ends")
                if part not in frag.intended_parts:
                    frag.intended_parts.append(part)
        n_del = sum(len(v) for v in gone.values())
        n_strip = sum(len(v) for blocks in edits.values() for v in blocks.values())
        frag.property_assertions.append({
            "kind": "tagged_paragraphs", "scope": scope, "tag_regex": params["tag_regex"],
            "tag_separator": params.get("tag_separator", DEFAULT_SEPARATOR), "keep": params.get("keep") or [],
            "delete": params.get("delete") or [], "strip_tags": strip, "tracked": tracked, "author": author,
            "expected_deleted": {p: v for p, v in gone.items()},
            "expected_stripped": {p: sorted(int(b) for b in v) for p, v in edits.items()},
            "expected_marks": marks})
        if not seen:
            if params.get("allow_missing"):
                frag.notes.append("filter_tagged_paragraphs: no tagged paragraph; no change.")
                return frag
            raise JobException(f"no paragraph in {scope} carries a tag matching {params['tag_regex']!r}", "not-found",
                               {"scope": scope})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(v for k, v in frag.rev_delta.items() if v > 0)
        frag.geometry_reference = "none"
        frag.geometry_reason = "deleting paragraphs reflows the content after them"
        frag.transform_data = {"edits": edits, "deleted": gone, "marks": marks, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        frag.notes.append(f"filter_tagged_paragraphs ({'tracked' if tracked else 'untracked'}): {n_del} paragraph(s) "
                          f"deleted, {n_strip} tag(s) taken off; systems found: "
                          + ", ".join(f"{s} (kept {k}, deleted {d})" for s, (k, d) in sorted(seen.items())))
        if not tracked:
            frag.flags.append(f"UNTRACKED deletion of {n_del} paragraph(s) at the user's request.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not (td.get("edits") or td.get("deleted")):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part in dict.fromkeys(list(td.get("edits", {})) + list(td.get("deleted", {}))):
            root = pkg.xml(part)
            if part in td.get("edits", {}):
                apply_part_edits(root, td["edits"][part], td["tracked"], stamp, next_id)
            pars = paragraphs(root)
            held = td.get("marks", {}).get(part, {})
            for bi in sorted(td.get("deleted", {}).get(part, []), reverse=True):
                ranges = range_marks(pars[bi])
                delete_paragraph(pars[bi], td["tracked"], stamp, next_id, [ranges[k] for k in held.get(str(bi), [])])
            pkg.write_xml(part, root)
