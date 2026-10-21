"""Op: remove_section_break — the section break at the end of each chosen section goes (docs/BUILTIN_OPS_BATCH6_DESIGN.md
§8; the shared core is ops/section_breaks.py).

``select.sections`` is set_page_setup's (``selectors.resolve_sections``, the content form included) and names the
sections whose ENDING break goes; ``"all"`` (the default) is every section that ends with one — all but the last. The
last section ends the document, not at a break: choosing it is refused (``last-section``).

A run of chosen sections a..b joins the section t = b + 1 after it (Word's behaviour): the w:sectPr of each paragraph that
held a break goes (the paragraph stays; a w:pPr left with nothing goes too), and section t's sectPr stays the joined
section's setup — page size, margins, columns, headers and footers — with two changes, so that nothing moves but the
joined pages:

* its START becomes section a's (``w:type`` and the page-number restart): where the joined section now begins;
* each header/footer reference (kind, type) section t has none of and a joined section has — the latest one — moves
  into it, after its own references: section t's pages, and every later section inheriting from it, show what they
  showed. A joined section's other references go; a header/footer part no section references any more leaves the
  package with its relationship, content-type override, .rels and the media nothing else uses (``hf_common``), and so
  does a joined section's printer settings part nothing else uses; all predicted in ``members_removed``, the removed
  parts' blocks as deletions.

Refused: a chosen section with a pending tracked change of its properties (``w:sectPrChange``) or whose break's paragraph
mark is a pending tracked insertion or deletion (``tracked-section``); a break that is not in a top-level body paragraph
(``structure``). No section chosen by content: ``not-found`` unless ``allow_missing``. Untracked only — Word cannot
track a removed section break as such — so ``tracked: true`` is refused.

Flagged for review: what the joined pages take from section t that differs from their own (page size, margins, columns
…); a header or footer they will show instead of their own; a page-number restart at the removed break that goes;
section t's first page that is no longer a first page; a pending tracked change of section t's properties.

V10 (``removed_section_breaks``): the user's select and params, the runs and the moved references; the verifier
re-derives them, builds the expected main part and compares it whole, and checks the relationships, the content types
and the parts that go.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import CT_PART, R, W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .hf_common import apply_removal, effective_refs, plan_removal, refs
from .section_breaks import holder, mark_revised, section_start, set_start, start_words
from .selectors import resolve_sections, section_reads
from .set_page_setup import title_pg_on

SETUP_WORDS = {"pgSz": "page size and orientation", "pgMar": "margins", "paperSrc": "paper source",
               "pgBorders": "page borders", "lnNumType": "line numbering", "pgNumType": "page number format",
               "cols": "columns", "formProt": "form protection", "vAlign": "vertical alignment",
               "noEndnote": "endnote placement", "titlePg": "first-page setting", "textDirection": "text direction",
               "bidi": "right-to-left layout", "rtlGutter": "gutter side", "docGrid": "line and character grid",
               "printerSettings": "printer settings", "footnotePr": "footnote settings",
               "endnotePr": "endnote settings"}
NOT_SETUP = {W + "headerReference", W + "footerReference", W + "type", W + "sectPrChange"}


def runs_of(chosen: Sequence[int]) -> List[Tuple[int, int]]:
    """Consecutive chosen sections as (first, last) runs."""
    out: List[Tuple[int, int]] = []
    for i in sorted(chosen):
        if out and out[-1][1] == i - 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def setup_of(sp: etree._Element) -> Dict[str, Any]:
    """The section's setup as comparable values per element (the START left out: w:type, the page-number restart)."""
    out: Dict[str, Any] = {}
    for c in sp:
        if not isinstance(c.tag, str) or c.tag in NOT_SETUP:
            continue
        name = etree.QName(c).localname
        if c.tag == W + "titlePg":
            out[name] = title_pg_on(sp)
            continue
        attrs = sorted((k, v) for k, v in c.attrib.items() if not (c.tag == W + "pgNumType" and k == W + "start"))
        out[name] = (attrs, etree.tostring(c, method="c14n") if len(c) else b"")
    if "titlePg" not in out:
        out["titlePg"] = False
    if out.get("pgNumType") == ([], b""):
        del out["pgNumType"]
    return out


def last_reference(sp: etree._Element) -> Optional[etree._Element]:
    found = [c for c in sp if c.tag in (W + "headerReference", W + "footerReference")]
    return found[-1] if found else None


@register
class RemoveSectionBreak(Op):
    name = "remove_section_break"
    mode = "formatting"
    description = ("Remove the section break at the end of selected sections: their pages join the following section "
                   "and take its page setup, headers and footers; the paragraph that held each break stays.")
    params_schema = {"type": "object", "properties": {
        "allow_missing": {"type": "boolean", "default": False,
                          "description": "content-form sections: a file where no section matches is a no-op instead "
                                         "of not-found"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {"sections": {
        "description": "the sections whose ENDING break goes: 'all' (every break: every section but the last), "
                       "[section indices] (0-based; never the last one), or by content (set_page_setup's form: "
                       "{containing, with_tables, index_range})"}}}
    verification_profile = {
        "V1": "members_removed exact (header/footer parts no section uses any more, their .rels and media, an unused "
              "printer settings part); relationships and content types edited; no dangling rel or r:id",
        "V2": "every block of a removed part a predicted deletion; no other text changes",
        "V10": "removed_section_breaks: the chosen sections, the joined sections' start and moved references "
               "re-derived; the main part compared whole; the relationships, content types and removed parts checked",
        "V7": "none — the joined pages take the following section's layout by design"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        sections = select.get("sections", "all")
        if sections in (None, "all"):
            head = prose.sentence("remove every section break, so the whole document becomes one section with the "
                                  "page setup, headers and footers of the last section")
        else:
            place = prose.where({"sections": sections}, default="every section")[3:]
            head = prose.sentence("remove the section break at the end of", place + ", so those pages join the next "
                                  "section and take its page setup, headers and footers")
        tail = ["the joined section begins where the first joined one did, with its page-number restart",
                "the paragraph that held each break stays", "the change is not tracked"]
        if params.get("allow_missing") is True:
            tail.append("files where no section matches are left unchanged")
        return head + " " + prose.sentence(prose.join(tail))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select, params = dict(select or {}), dict(params or {})
        if set(select) - {"sections"}:
            raise JobException(f"remove_section_break: unknown selector key(s) {sorted(set(select) - {'sections'})}",
                               "bad-params")
        if set(params) - {"allow_missing", "tracked"}:
            raise JobException(f"remove_section_break: unknown param(s) "
                               f"{sorted(set(params) - {'allow_missing', 'tracked'})}", "bad-params")
        if params.get("tracked") not in (None, False):
            raise JobException("removing a section break is not tracked: only tracked false is accepted", "bad-params")
        allow_missing = params.get("allow_missing", False)
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        chooser = select.get("sections", "all")
        main = pkg.main_part()
        root = pkg.xml(main)
        body = root.find(W + "body")
        secs = DocxPackage.sect_prs(root)
        n = len(secs)
        if chooser in (None, "all"):
            chosen = list(range(n - 1))                  # every section that ends with a break
        else:
            chosen = resolve_sections(pkg, chooser, content=True)
            if n - 1 in chosen:
                raise JobException(f"section {n - 1} is the last section: it ends the document, not at a section "
                                   f"break — to join it with the section before it, choose that one",
                                   "last-section", {"section": n - 1, "sections": n})
        frag = self.fragment(select, params)
        frag.geometry_reference, frag.geometry_reason = "none", "the joined pages take the following section's layout"
        frag.touches += section_reads(pkg, chooser)
        for i in chosen:
            sp, p = secs[i], holder(secs[i])
            if p is None or p.getparent() is not body:
                raise JobException(f"section {i}'s break is not in a top-level paragraph of the body", "structure",
                                   {"section": i})
            if sp.find(W + "sectPrChange") is not None:
                raise JobException(f"section {i}'s break carries a pending tracked change of its page setup "
                                   f"(w:sectPrChange): accept or reject it in Word first", "tracked-section",
                                   {"section": i})
            if mark_revised(p):
                raise JobException(f"the paragraph that holds section {i}'s break has a pending tracked insertion or "
                                   f"deletion of its mark: accept or reject it in Word first", "tracked-section",
                                   {"section": i})
        own = [refs(sp) for sp in secs]
        runs: List[Dict[str, Any]] = []
        dropped: List[Tuple[int, str, str, str]] = []
        moved_rids: List[str] = []
        for a, b in runs_of(chosen):
            covered = {(kind, typ) for kind, typ, _, _ in own[b + 1]}
            moved: List[List[int]] = []
            for i in range(b, a - 1, -1):                # the latest joined section's reference wins
                for k, (kind, typ, rid, _) in enumerate(own[i]):
                    if (kind, typ) in covered:
                        dropped.append((i, kind, typ, rid))
                    else:
                        covered.add((kind, typ))
                        moved.append([i, k])
                        moved_rids.append(rid)
            runs.append({"first": a, "last": b, "target": b + 1, "moved": moved})
        kept = [rid for i, r in enumerate(own) if i not in set(chosen) for _, _, rid, _ in r] + moved_rids
        rp = plan_removal(pkg, main, root, dropped, kept)
        printer_rids, printer_parts = self._printer_settings(pkg, main, root, secs, chosen)
        rids = sorted(set(rp.gone_rids) | set(printer_rids))
        order = {m: k for k, m in enumerate(pkg.member_names)}
        members = sorted(set(rp.members) | set(printer_parts), key=lambda m: order[m])
        frag.diffs = list(rp.diffs)
        frag.untracked_diffs_expected = bool(frag.diffs)
        frag.members_removed = members
        for k, v in rp.rev_gone.items():
            frag.rev_delta[k] = -v
        frag.flags += rp.flags
        frag.property_assertions.append({
            # the user's own words and what the plan chose from them: V10 re-derives every decision
            "kind": "removed_section_breaks", "part": main, "select": copy.deepcopy(select),
            "params": {k: copy.deepcopy(params[k]) for k in ("allow_missing",) if k in params},
            "chosen": list(chosen), "runs": copy.deepcopy(runs), "gone_rids": rids,
            "members_removed": list(members), "sections": n - len(chosen)})
        frag.transform_data = {"main": main, "chosen": list(chosen), "runs": runs, "rids": rids, "members": members,
                               "media": rp.media}
        if not chosen:
            if isinstance(chooser, dict) and not allow_missing:
                raise JobException(f"no section matches {chooser!r}", "not-found", {"sections": chooser})
            frag.notes.append("no section break to remove here; nothing to do"
                              + (" (allow_missing)" if isinstance(chooser, dict) else ""))
            return frag
        _, overrides = pkg.content_types()
        ct = any(m in overrides for m in members)
        frag.intended_parts = [main] + ([pkg.rels_path_for(main)] if rids else []) + ([CT_PART] if ct else [])
        # every section from the first joined one on changes its index: the main part changes as a whole for the ops
        # beside it; the parts that go, and the relationships and content types, are changed whole too
        frag.touches.append((main, "*", "sections"))
        frag.touches += [(m, "*", "remove") for m in members]
        if rids:
            frag.touches.append((pkg.rels_path_for(main), "*", "rels"))
        if ct:
            frag.touches.append((CT_PART, "*", "ct"))
        if isinstance(chooser, dict):
            frag.notes.append(f"sections chosen by {chooser!r}: {chosen} of {n}")
        self._notes(pkg, secs, runs, frag)
        frag.notes.append(f"relationships removed: {rids or 'none'}; parts removed: {members or 'none'}")
        return frag

    @staticmethod
    def _printer_settings(pkg: DocxPackage, main: str, root, secs, chosen: Sequence[int]) -> Tuple[List[str], List[str]]:
        """The printer settings relationships only a joined section's break used, and their parts no other
        relationship targets."""
        gone_secs = {id(secs[i]) for i in chosen}
        mine = [secs[i].find(W + "printerSettings") for i in chosen]
        rids = {el.get(R + "id") for el in mine if el is not None and el.get(R + "id")}
        if not rids:
            return [], []
        used = set()
        for el in root.iter():
            if not isinstance(el.tag, str) or any(id(a) in gone_secs for a in el.iterancestors(W + "sectPr")):
                continue
            used |= {v for k, v in el.attrib.items() if k.startswith(R)}
        rels = pkg.rels(main)
        gone = sorted(rid for rid in rids - used if rid in rels)
        targets = {rels[rid].resolved for rid in gone}
        others = {r.resolved for owner, r in pkg.relationships(source=True)
                  if not (owner == main and r.rid in gone) and r.resolved}
        return gone, sorted(t for t in targets if t and pkg.has(t) and t not in others)

    @staticmethod
    def _notes(pkg: DocxPackage, secs, runs: List[Dict[str, Any]], frag: PlanFragment) -> None:
        """What the joined pages take and what review should look at (module docstring)."""
        eff = effective_refs(pkg, secs)
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        for run in runs:
            a, b, t = run["first"], run["last"], run["target"]
            joined = list(range(a, b + 1))
            target = secs[t]
            frag.notes.append(f"section(s) {joined} join section {t}; the joined section begins on "
                              f"{start_words(secs[a])}, as section {a} did")
            restart = section_start(secs[a])[1]
            if restart is not None:
                frag.notes.append(f"the joined section restarts its page numbers at {restart}, as section {a} did")
            if section_start(target)[1] is not None:
                frag.flags.append(f"section {t} restarted its page numbers at {section_start(target)[1]}; that restart "
                                  f"was at the removed break and goes: the page numbers run on from section {a}'s pages")
            wanted = setup_of(target)
            for i in joined:
                own = setup_of(secs[i])
                differs = sorted({k for k in set(own) | set(wanted) if own.get(k) != wanted.get(k)},
                                 key=lambda k: list(SETUP_WORDS).index(k) if k in SETUP_WORDS else len(SETUP_WORDS))
                if differs:
                    frag.flags.append(f"the pages of section {i} join section {t} and take its "
                                      f"{prose.join(SETUP_WORDS.get(k, k) for k in differs)}")
                else:
                    frag.notes.append(f"the pages of section {i} join section {t}, whose page setup is the same")
                types = ["default"] + (["even"] if even_odd else [])
                for kind in ("header", "footer"):
                    for typ in types:
                        before, after = eff[i].get((kind, typ)), eff[t].get((kind, typ))
                        if before != after:
                            what = kind if typ == "default" else f"even-page {kind}"
                            frag.flags.append(f"the pages of section {i} will show the {what} {after or 'none'} "
                                              f"(section {t}'s) instead of {before or 'none'}")
            for kind in ("header", "footer"):
                first_before = eff[a].get((kind, "first" if title_pg_on(secs[a]) else "default"))
                first_after = eff[t].get((kind, "first" if title_pg_on(target) else "default"))
                if first_before != first_after:
                    frag.flags.append(f"the first page of section {a} (now the joined section's first page) will show "
                                      f"the {kind} {first_after or 'none'} instead of {first_before or 'none'}")
                if title_pg_on(target) and eff[t].get((kind, "first")) != eff[t].get((kind, "default")):
                    frag.flags.append(f"section {t}'s first page is no longer a first page: it will show the ordinary "
                                      f"{kind} {eff[t].get((kind, 'default')) or 'none'} instead of its first-page "
                                      f"{kind} {eff[t].get((kind, 'first')) or 'none'}")
            if target.find(W + "sectPrChange") is not None and (run["moved"] or section_start(target) !=
                                                                 section_start(secs[a])):
                frag.flags.append(f"section {t} carries a pending tracked section change (w:sectPrChange) from an "
                                  f"earlier reviewer; reject-all would restore that older page setup")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["chosen"]:
            return
        root = pkg.xml(td["main"])
        secs = DocxPackage.sect_prs(root)                # read before anything is removed
        for run in td["runs"]:
            target, first = secs[int(run["target"])], secs[int(run["first"])]
            moved = [copy.deepcopy(refs(secs[int(i)])[int(k)][3]) for i, k in run["moved"]]
            set_start(target, *section_start(first))
            anchor = last_reference(target)
            for el in moved:
                el.tail = None
                if anchor is None:
                    target.insert(0, el)
                else:
                    anchor.addnext(el)
                anchor = el
        for i in td["chosen"]:
            sp = secs[int(i)]
            ppr = sp.getparent()
            ppr.remove(sp)
            if not len(ppr) and not ppr.attrib:
                ppr.getparent().remove(ppr)
        pkg.write_xml(td["main"], root)
        apply_removal(pkg, td["main"], td["rids"], td["members"], td["media"])
