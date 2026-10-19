"""Op: remove_header_footer — delete header/footer references from selected sections, and every part that is
no longer referenced afterwards: the header/footer part, its .rels, its [Content_Types].xml override, its
relationship in document.xml.rels, and media nothing else uses.

Predicted from the source alone (the reference graph is walked here; apply() uses the package helpers, and
cross-checks ``unreferenced_media()`` against the prediction):

* members removed, exactly; parts shared with a section that keeps its reference stay;
* every block of a removed part is a predicted deletion (V2's text scope is the union of source and output);
* every revision inside a removed part is a predicted negative delta, and flagged by name — removing the part
  discards someone's pending tracked change;
* inheritance: a section without its own reference shows the previous section's part. The op refuses when the
  removal would leave a selected section still showing a header/footer through inheritance, or would change what
  an unselected section shows (``inheritance`` exception) — an empty replacement part would be needed, and this
  op never creates one. ``w:titlePg`` and ``w:evenAndOddHeaders`` are left as they are.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..package import CT_PART, W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .hf_common import KINDS, TYPES, apply_removal, plan_removal, refs
from .selectors import resolve_sections

_refs = refs
_TYPE_ADJECTIVES = {"default": "regular", "first": "first-page", "even": "even-page"}


@register
class RemoveHeaderFooter(Op):
    name = "remove_header_footer"
    mode = "formatting"
    description = ("Remove headers and/or footers (default, first-page, even-page) from selected sections, with "
                   "their parts, relationships, content-type overrides and media no longer used.")
    params_schema = {"type": "object", "properties": {}, "description": "none — everything is in the selector"}
    select_schema = {"type": "object", "properties": {
        "sections": {"description": "'all' or [section indices]", "default": "all"},
        "kinds": {"type": "array", "items": {"enum": list(KINDS)}, "default": list(KINDS)},
        "types": {"type": "array", "items": {"enum": list(TYPES)}, "default": list(TYPES)}}}
    verification_profile = {
        "V1": "members_removed exact (parts, .rels, orphaned media); rels + content types edited; no dangling rel/r:id",
        "V2": "every block of a removed part a predicted deletion (text scope = union)",
        "V4": "revisions inside removed parts predicted as negative deltas and flagged",
        "V10": "section_hf_refs: only the selected references gone; everything else in document.xml identical",
        "V7": "source reference: body text must not move",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select = dict(select or {})
        kinds = [k for k in (select.get("kinds") or KINDS) if k in KINDS] or list(KINDS)
        types = [t for t in (select.get("types") or TYPES) if t in TYPES] or list(TYPES)
        what = "the " + ("" if set(types) == set(TYPES) else prose.join(_TYPE_ADJECTIVES[t] for t in types) + " ") \
            + prose.join(k + "s" for k in kinds)
        place = prose.where({"sections": select.get("sections", "all")}, default="every section")[3:]
        kept = [_TYPE_ADJECTIVES[t] for t in TYPES if t not in types]
        first = prose.sentence("remove", what, "from", place + (f", leaving the {prose.join(kept)} ones" if kept else ""))
        second = prose.sentence("Body text does not move, and the page margins and first-page settings are left as they are")
        return first + " " + second

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"sections", "kinds", "types"}:
            raise JobException(f"remove_header_footer: unknown selector key(s) "
                               f"{sorted(set(select) - {'sections', 'kinds', 'types'})}", "bad-params")
        if params:
            raise JobException(f"remove_header_footer takes no params (got {sorted(params)})", "bad-params")
        kinds = list(select.get("kinds", KINDS))
        types = list(select.get("types", TYPES))
        if not kinds or set(kinds) - set(KINDS) or not types or set(types) - set(TYPES):
            raise JobException(f"kinds ⊆ {KINDS}, types ⊆ {TYPES}, both non-empty", "bad-params")
        ords = resolve_sections(pkg, select.get("sections", "all"))
        main = pkg.main_part()
        main_rels_part = pkg.rels_path_for(main)
        rels = pkg.rels(main)
        root = pkg.xml(main)
        secs = DocxPackage.sect_prs(root)
        part_of = {rid: r.resolved for rid, r in rels.items()}

        # which references go
        removed_refs: List[Tuple[int, str, str, str]] = []
        kept_rids: List[str] = []
        for i, sp in enumerate(secs):
            for kind, typ, rid, _ in _refs(sp):
                if i in ords and kind in kinds and typ in types:
                    removed_refs.append((i, kind, typ, rid))
                else:
                    kept_rids.append(rid)

        # inheritance: what each section shows, before and after
        def shown(drop: bool) -> List[Dict[Tuple[str, str], Optional[str]]]:
            out, prev = [], {}
            for i, sp in enumerate(secs):
                cur = dict(prev)
                for kind, typ, rid, _ in _refs(sp):
                    if drop and i in ords and kind in kinds and typ in types:
                        cur.pop((kind, typ), None)      # no own reference now → inherit whatever came before
                        cur[(kind, typ)] = prev.get((kind, typ))
                    else:
                        cur[(kind, typ)] = part_of.get(rid)
                out.append(cur)
                prev = cur
            return out
        before, after = shown(False), shown(True)
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        title_pg = [sp.find(W + "titlePg") is not None and sp.find(W + "titlePg").get(W + "val") not in ("0", "false", "off")
                    for sp in secs]

        def displayed(i: int, typ: str) -> bool:
            """Word shows a first-page part only with titlePg, an even-page part only with evenAndOddHeaders."""
            return typ == "default" or (typ == "first" and title_pg[i]) or (typ == "even" and even_odd)

        invisible: List[str] = []
        for i in range(len(secs)):
            for key in sorted({(k, t) for k in KINDS for t in TYPES}):
                b, a = before[i].get(key), after[i].get(key)
                if i in ords and key[0] in kinds and key[1] in types and a is not None:
                    if displayed(i, key[1]):
                        raise JobException(
                            f"section {i} would still show {a} as its {key[1]} {key[0]} through inheritance from an "
                            f"earlier section; removing it there needs an empty {key[0]} part, which this op never "
                            f"creates — select the earlier section(s) too", "inheritance",
                            {"section": i, "kind": key[0], "type": key[1], "inherited_part": a})
                    invisible.append(f"section {i} still inherits {a} as its {key[1]} {key[0]} (not displayed: "
                                     f"{'no titlePg' if key[1] == 'first' else 'evenAndOddHeaders off'})")
                if i not in ords and a != b:
                    if displayed(i, key[1]):
                        raise JobException(
                            f"section {i} (not selected) inherits its {key[1]} {key[0]} ({b}) from a selected section; "
                            f"the removal would change what it shows — select it too or narrow the selection",
                            "inheritance", {"section": i, "kind": key[0], "type": key[1], "part": b})
                    invisible.append(f"section {i} (not selected) loses its inherited {key[1]} {key[0]} {b} (not "
                                     f"displayed: {'no titlePg' if key[1] == 'first' else 'evenAndOddHeaders off'})")

        rp = plan_removal(pkg, main, root, removed_refs, kept_rids)
        gone_rids, parts_gone, media, ct_changes = rp.gone_rids, rp.parts_gone, rp.media, rp.ct_changes
        frag = self.fragment(select, params)
        frag.diffs = list(rp.diffs)
        frag.members_removed = list(rp.members)
        rev_gone, flags = rp.rev_gone, rp.flags

        frag.untracked_diffs_expected = bool(frag.diffs)
        frag.rev_delta = {"ins": 0, "del": 0}
        for k, v in rev_gone.items():
            frag.rev_delta[k] = -v
        frag.flags += flags
        frag.flags += [f"inheritance of a part Word does not display changes: {x} — it would matter only if "
                       f"titlePg / evenAndOddHeaders were turned on later" for x in invisible]
        if removed_refs:
            frag.intended_parts = [main] + ([main_rels_part] if gone_rids else []) + ([CT_PART] if ct_changes else [])
            frag.touches = [(main, f"sectPr:{i}", "hfref") for i in sorted({i for i, _, _, _ in removed_refs})]
            frag.touches += [(m, "*", "remove") for m in frag.members_removed]
            # the inheritance rule above READS every section's titlePg (whether a first-page part is displayed): a
            # different_first_page beside it would turn on a first page that then shows nothing — a plan-time conflict
            # (batch 5's review), never a pass with the first page blank
            frag.touches += [(main, f"sectPr:{i}", "verify:titlePg") for i in range(len(secs))]
            if gone_rids:
                frag.touches.append((main_rels_part, "*", "rels"))
            if ct_changes:
                frag.touches.append((CT_PART, "*", "ct"))
        frag.property_assertions.append({"kind": "section_hf_refs", "sections": ords, "kinds": kinds, "types": types,
                                         "expected_removed": len(removed_refs)})
        frag.transform_data = {"main": main, "sections": ords, "kinds": kinds, "types": types,
                               "rids": gone_rids, "members": frag.members_removed, "media": media}
        frag.notes.append(f"{len(removed_refs)} reference(s) removed from sections {ords} "
                          f"({', '.join(f'§{i} {t} {k}' for i, k, t, _ in removed_refs) or 'none present'}); "
                          f"parts removed: {parts_gone or 'none'}; media removed: {media or 'none'}")
        shared = sorted({part_of.get(rid) for _, _, _, rid in removed_refs} - set(parts_gone) - {None})
        if shared:
            frag.notes.append(f"kept (still referenced by a section that keeps it): {shared}")
        frag.notes.append("titlePg / evenAndOddHeaders and page margins left as they are; the header/footer space "
                          "collapses into the margin.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        root = pkg.xml(td["main"])
        secs = DocxPackage.sect_prs(root)
        changed = False
        for i in td["sections"]:
            for kind, typ, _, el in _refs(secs[i]):
                if kind in td["kinds"] and typ in td["types"]:
                    secs[i].remove(el)
                    changed = True
        if changed:
            pkg.write_xml(td["main"], root)
        apply_removal(pkg, td["main"], td["rids"], td["members"], td["media"])
