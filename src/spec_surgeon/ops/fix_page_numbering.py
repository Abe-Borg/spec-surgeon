"""Op: fix_page_numbering — fix the cover/body page-number symptom (was the v0.1.0 job ``page-numbering``).

From the playbook: the cover is section 1 with the {PAGE} field in its own footer part, and the
first body section carries <w:pgNumType w:start="1"/>. Two options, exactly one applied:

  cover-unnumbered  delete just the paragraph containing the PAGE field from the cover footer part
  continuous        delete <w:pgNumType w:start="1"/> from the first body section's sectPr
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import block_full_text, count_revisions, paragraph_marked_text, paragraphs
from . import register
from .base import BlockDiff, JobException, Op, PlanFragment

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
        "V10": "continuous: section_props assertion (pgNumType/@start removed); nothing else changes",
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
        cover, body = sections[0], sections[1]

        if option == "continuous":
            if body.pg_num_start is None:
                raise JobException("first body section has no <w:pgNumType w:start>; numbering is already "
                                   "continuous or restarts elsewhere", "structure",
                                   {"sections": [s.__dict__ for s in sections]})
            main = pkg.main_part()
            root = pkg.xml(main)
            sp = DocxPackage.sect_prs(root)[body.sect_pr_ordinal]
            pg = sp.find(W + "pgNumType")
            other_attrs = {k: v for k, v in pg.attrib.items() if k != W + "start"}
            plan.intended_parts = [main]
            plan.transform_data = {"sect_pr_ordinal": body.sect_pr_ordinal,
                                   "remove_element": not other_attrs}
            plan.touches = [(main, f"sectPr:{body.sect_pr_ordinal}", "pgNumType")]
            plan.property_assertions.append({
                "kind": "section_props", "part": main, "sections": [body.sect_pr_ordinal],
                "elements": {"pgNumType": {"remove": ["start"], "drop_if_empty": True}},
                "expected_sections": 1})
            if other_attrs:
                plan.notes.append("pgNumType carries other attributes (%s); only w:start is removed, "
                                  "the element stays." % ", ".join(etree.QName(k).localname for k in other_attrs))
            else:
                plan.notes.append("pgNumType carried only w:start; the element is removed.")
            plan.notes.append("Zero legitimate text diffs in %s; body page numbers now continue from the cover." % main)
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
        shared_with = [s.index for s in sections[1:] if footer in s.footer_refs.values()]
        if shared_with:
            raise JobException(f"cover footer part {footer} is also referenced by section(s) {shared_with}; "
                               f"editing it would change body pages too", "shared-part",
                               {"footer": footer, "sections": shared_with})
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
            plan.touches = [(footer, str(idx), "text")]
            plan.notes.append("The PAGE paragraph is the footer's only paragraph; its runs are removed and the "
                              "empty paragraph kept so the part stays schema-valid.")
        else:
            gone = count_revisions(p)
            plan.transform_data = {"footer": footer, "paragraph_index": idx, "clear_only": False}
            plan.diffs.append(BlockDiff(part=footer, block_index=idx, before=before, after=None,
                                        note="paragraph containing the PAGE field deleted"))
            plan.touches = [(footer, "*", "structure")]
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
            sp = DocxPackage.sect_prs(root)[td["sect_pr_ordinal"]]
            pg = sp.find(W + "pgNumType")
            if td["remove_element"]:
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
