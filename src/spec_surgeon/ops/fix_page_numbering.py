"""Op: fix_page_numbering — fix the cover/body page-number symptom (was the v0.1.0 job ``page-numbering``).

From the playbook: the cover is section 1 with the {PAGE} field in its own footer part, and the
first body section carries <w:pgNumType w:start="1"/>. Two options, exactly one applied:

  cover-unnumbered  delete just the paragraph containing the PAGE field from the cover footer part
  continuous        delete <w:pgNumType w:start="…"/> from every section after the cover (a restart in a later
                    section would start the count over again; "continuous" means one count through the document)

The cover footer part must be the cover's alone: a later section that shows the same part — by an explicit
reference or by inheritance ("Link to Previous": no reference of its own, so Word shows the previous section's
part) — would lose its page number too, and no check could tell (rule 10: the op never adds an empty part to
break the link). ``hf_common.effective_refs`` resolves the inheritance; a part shown by any later section is
refused as ``shared-part``.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import block_full_text, count_revisions, paragraph_marked_text, paragraphs
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment
from .hf_common import effective_refs

_PAGE = re.compile(r"^\s*PAGE\b")


def _page_field_paragraphs(root: etree._Element) -> List[Tuple[int, etree._Element]]:
    hits = []
    for i, p in enumerate(paragraphs(root)):
        found = False
        for it in p.iter(W + "instrText"):
            if _PAGE.match(it.text or ""):
                found = True
        for fs in p.iter(W + "fldSimple"):
            if _PAGE.match(fs.get(W + "instr") or ""):
                found = True
        if found:
            hits.append((i, p))
    return hits


@register
class FixPageNumbering(Op):
    name = "fix_page_numbering"
    mode = "formatting"
    description = "Fix cover/body page numbering: make the cover unnumbered, or make numbering continuous."
    params_schema = {
        "type": "object",
        "properties": {
            "option": {"type": "string", "enum": ["cover-unnumbered", "continuous"],
                       "description": "cover-unnumbered: remove the PAGE paragraph from the cover footer; "
                                      "continuous: remove the body section's page-number restart."},
        },
        "required": ["option"],
    }
    select_schema = {"type": "object", "description": "none — the cover is section 0, the first body section is section 1"}
    verification_profile = {
        "V2": "cover-unnumbered: one deleted (or cleared) footer block; continuous: zero diffs",
        "V3": "cover-unnumbered: the PAGE field's fldChar count declared",
        "V4": "revisions inside a deleted footer paragraph predicted as negative deltas and flagged",
        "V10": "continuous: section_props assertion (pgNumType/@start removed from every restarting section after "
               "the cover); nothing else changes",
        "V7": "source reference; page 1 excluded for cover-unnumbered",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("fix_page_numbering takes no selector", "bad-params")
        option = params.get("option")
        if option not in ("cover-unnumbered", "continuous"):
            raise JobException("option must be 'cover-unnumbered' or 'continuous'", "bad-params")
        sections = pkg.sections()
        if len(sections) < 2:
            raise JobException("document has a single section; there is no cover/body split to fix",
                               "structure", {"sections": len(sections)})
        plan = self.fragment(select, params)
        cover = sections[0]

        if option == "continuous":
            # every section after the cover that restarts the count — not only the first body section: a restart
            # left in a third section would start the numbering over again there, and the output would still PASS
            restarting = [s for s in sections[1:] if s.pg_num_start is not None]
            if not restarting:
                raise JobException("no section after the cover has a <w:pgNumType w:start>; numbering is already "
                                   "continuous", "structure", {"sections": [s.__dict__ for s in sections]})
            main = pkg.main_part()
            root = pkg.xml(main)
            sps = DocxPackage.sect_prs(root)
            targets = []
            for s in restarting:
                pg = sps[s.sect_pr_ordinal].find(W + "pgNumType")
                other_attrs = [etree.QName(k).localname for k in pg.attrib if k != W + "start"]
                targets.append({"sect_pr_ordinal": s.sect_pr_ordinal, "remove_element": not other_attrs,
                                "start": s.pg_num_start, "other_attrs": other_attrs})
            plan.intended_parts = [main]
            plan.transform_data = {"targets": targets}
            plan.touches = [(main, f"sectPr:{t['sect_pr_ordinal']}", "pgNumType") for t in targets]
            plan.property_assertions.append({
                "kind": "section_props", "part": main, "sections": [t["sect_pr_ordinal"] for t in targets],
                "elements": {"pgNumType": {"remove": ["start"], "drop_if_empty": True}},
                "expected_sections": len(targets)})
            for t in targets:
                if t["other_attrs"]:
                    plan.notes.append("section %d: pgNumType restarts at %d and carries other attributes (%s); only "
                                      "w:start is removed, the element stays."
                                      % (t["sect_pr_ordinal"], t["start"], ", ".join(t["other_attrs"])))
                else:
                    plan.notes.append("section %d: pgNumType carried only w:start=%d; the element is removed."
                                      % (t["sect_pr_ordinal"], t["start"]))
            plan.notes.append("Zero legitimate text diffs in %s; page numbers now run continuously from the cover "
                              "through section(s) %s." % (main, [t["sect_pr_ordinal"] for t in targets]))
            if len(targets) > 1:
                plan.flags.append("Page numbering restarted in %d sections after the cover (%s), not only in the first "
                                  "body section; every restart is removed so the count runs through the whole "
                                  "document — check that a later restart was not meant (an appendix of its own)."
                                  % (len(targets), [t["sect_pr_ordinal"] for t in targets]))
            return plan

        # cover-unnumbered
        if cover.title_pg and "first" in cover.footer_refs:
            footer = cover.footer_refs["first"]
            plan.notes.append("Cover section has titlePg; its first-page footer part is the one shown, so that is the target.")
        elif "default" in cover.footer_refs:
            footer = cover.footer_refs["default"]
        else:
            raise JobException("cover section has no footer of its own (inherits from nowhere — nothing to edit)",
                               "structure", {"cover": cover.__dict__})
        # The part must be the cover's alone. Explicit references are in each section's footer_refs; an inherited
        # one ("Link to Previous") is not — effective_refs resolves what every section actually shows, and a part
        # shown on any later section's pages (default; first with titlePg; even with evenAndOddHeaders) is shared.
        eff = effective_refs(pkg)
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        shared: List[str] = []
        for s in sections[1:]:
            if footer in s.footer_refs.values():
                shared.append(f"section {s.index} (its own reference)")
                continue
            for (kind, typ), part in eff[s.index].items():
                if kind != "footer" or part != footer:
                    continue
                if (typ == "first" and not s.title_pg) or (typ == "even" and not even_odd):
                    continue
                shared.append(f"section {s.index} (inherited: no {typ} footer of its own, so Word shows the "
                              f"previous section's — 'Link to Previous')")
                break
        if shared:
            raise JobException(f"cover footer part {footer} is also shown by {'; '.join(shared)}; editing it would "
                               f"remove the page number from those pages too (unlink the body footer in Word first)",
                               "shared-part", {"footer": footer, "sections": shared})
        # The plan READS every section's titlePg (rule 9): the cover's decides which footer part the cover shows
        # (its first-page footer only while titlePg is on), a later section's whether it shows the cover's first-page
        # footer by inheritance. An op flipping a titlePg in the same spec (apply_header_footer_template following its
        # template's) would leave this edit on a part the cover no longer shows — the page number still on the cover
        # and nothing to catch it (V7 skips page 1 here) — so it is a plan-time conflict, not a guess at an order.
        reads = [(pkg.main_part(), f"sectPr:{s.sect_pr_ordinal}", "verify:titlePg") for s in sections]
        root = pkg.xml(footer)
        hits = _page_field_paragraphs(root)
        if not hits:
            raise JobException(f"no PAGE field found in cover footer {footer}", "not-found", {"footer": footer})
        if len(hits) > 1:
            raise JobException(f"{len(hits)} paragraphs with PAGE fields in {footer}; ambiguous", "ambiguous",
                               {"footer": footer, "paragraphs": [i for i, _ in hits]})
        idx, p = hits[0]
        n_pars = len(paragraphs(root))
        before = block_full_text(p)
        marked = paragraph_marked_text(p)
        plan.intended_parts = [footer]
        if n_pars == 1:
            # the runs go, the paragraph (and its pPr, so its mark revisions) stays
            gone = {}
            for child in p:
                if child.tag != W + "pPr":
                    for k, v in count_revisions(child).items():
                        gone[k] = gone.get(k, 0) + v
            plan.transform_data = {"footer": footer, "paragraph_index": idx, "clear_only": True}
            plan.diffs.append(BlockDiff(part=footer, block_index=idx, before=before, after="", struct_after={},
                                        note="only paragraph in the footer: content cleared, paragraph kept"))
            plan.touches = [(footer, str(idx), "text")] + reads
            plan.notes.append("The PAGE paragraph is the footer's only paragraph; its runs are removed and the "
                              "empty paragraph kept so the part stays schema-valid.")
        else:
            gone = count_revisions(p)
            plan.transform_data = {"footer": footer, "paragraph_index": idx, "clear_only": False}
            plan.diffs.append(BlockDiff(part=footer, block_index=idx, before=before, after=None,
                                        note="paragraph containing the PAGE field deleted"))
            plan.touches = [(footer, "*", "structure")] + reads
        plan.untracked_diffs_expected = True
        plan.rev_delta = {"ins": 0, "del": 0}
        for k, v in gone.items():
            plan.rev_delta[k] = -v
        n_ins, n_del = gone.get("ins", 0), gone.get("del", 0)
        others = {k: v for k, v in gone.items() if k not in ("ins", "del")}
        if gone:
            plan.flags.append(f"The deleted cover-footer content carried {n_ins} tracked insertion(s) and "
                              f"{n_del} deletion(s)" + (f" and {others}" if others else "")
                              + "; removing it discards those revisions.")
        plain = re.sub(r"[\t\n]", " ", marked).strip()
        if plain and plain not in ("1", ""):
            plan.notes.append(f"The deleted paragraph also carried text: {plain!r}. That text disappears from the cover footer with it.")
        plan.geometry_pages_expected_to_change = [1]
        plan.expected_structural_delta = {"fldChar": -sum(1 for _ in p.iter(W + "fldChar"))}
        return plan

    def apply(self, pkg: DocxPackage, plan: PlanFragment) -> None:
        td = plan.transform_data
        if plan.params["option"] == "continuous":
            main = pkg.main_part()
            root = pkg.xml(main)
            sps = DocxPackage.sect_prs(root)
            for t in td["targets"]:
                sp = sps[t["sect_pr_ordinal"]]
                pg = sp.find(W + "pgNumType")
                if t["remove_element"]:
                    sp.remove(pg)
                else:
                    del pg.attrib[W + "start"]
            pkg.write_xml(main, root)
            return
        footer = td["footer"]
        root = pkg.xml(footer)
        p = paragraphs(root)[td["paragraph_index"]]
        if td["clear_only"]:
            for child in list(p):
                if child.tag != W + "pPr":
                    p.remove(child)
        else:
            p.getparent().remove(p)
        pkg.write_xml(footer, root)
