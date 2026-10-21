"""Op: insert_section_break — a section break before each selected body paragraph (docs/BUILTIN_OPS_BATCH6_DESIGN.md
§8; the shared core is ops/section_breaks.py).

The paragraph before each match (its previous block, range marks skipped) gets a copy of the GOVERNING section's
sectPr — the section the match lies in, whose sectPr is the first at or after it — so the new section has the same page
setup and the same header/footer references (two sections may show one header part). A governing section with k matches
splits into k + 1 parts in document order: the first part's copy is the source sectPr unchanged (the section's START —
``w:type`` and the page-number restart — stays with it); every later part, the governing sectPr itself last, starts
with the asked break (``type``: ``next_page``, the default, writes no w:type) and no restart. Nothing else changes: no
paragraph, no text, no other section.

Refused (``break-position``): a match that is the first block of the body, whose previous block is not a paragraph (a
table, a content control …), that already starts a section (its previous paragraph holds a w:sectPr), or that is not a
top-level body paragraph (a table cell, a content control); a match in a text box (``textbox``); a pending tracked
change of the governing section's properties (``w:sectPrChange``) or of the previous paragraph's mark
(``tracked-section``). No match: ``not-found`` unless ``allow_missing``. Untracked only — Word cannot track a section
break — so ``tracked: true`` is refused.

Flagged for review: the governing section shows a different first page (each new part's first page shows the first-page
header and footer); its line numbers or its footnote/endnote numbers restart at each section; it has several columns
(the columns of each part end at its break); its text is aligned vertically other than at the top (each part's last page
is aligned so too). An even- or odd-page break is noted (Word may add a blank page).

V10 (``inserted_section_breaks``): the user's select and params and the breaks the plan chose (match, carrier, section);
the verifier re-derives the matches, the refusals and the expected main part, and compares it whole.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from lxml import etree

from ..ooxml import PPR_ORDER, block_full_text, insert_in_order, paragraph_ppr, paragraphs
from ..package import W, DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .hf_common import effective_refs
from .section_breaks import (block_name, break_type, mark_revised, previous_block, section_start, set_start,
                             start_words)
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected
from .set_page_setup import START_TYPES, START_WORDS, title_pg_on
from .set_tab_stops import paragraph_sections
from .slots import nearest_p

BREAK_WORDS = {"next_page": "a section break that starts a new page",
               "continuous": "a continuous section break (the new section starts on the same page)",
               "even_page": "a section break that starts on the next even-numbered page",
               "odd_page": "a section break that starts on the next odd-numbered page"}


def snippet(p: etree._Element) -> str:
    """The paragraph's own words, short and quoted, for the notes (a tab read as a space)."""
    text = " ".join("".join(" " if el.tag == W + "tab" else (el.text or "") for el in p.iter(W + "t", W + "tab")
                            if nearest_p(el) is p and el.getparent().tag == W + "r").split())
    return prose.quote(text[:60] + ("…" if len(text) > 60 else "")) if text else "an empty paragraph"


@register
class InsertSectionBreak(Op):
    name = "insert_section_break"
    mode = "formatting"
    description = ("Insert a section break (next page, continuous, even or odd page) before each selected body "
                   "paragraph; the new section keeps the page setup, headers and footers of the section it splits.")
    params_schema = {"type": "object", "properties": {
        "type": {"type": "string", "enum": list(START_TYPES), "default": "next_page",
                 "description": "how the new section starts: on a new page, on the same page (continuous), or on the "
                                "next even / odd page"},
        "allow_missing": {"type": "boolean", "default": False,
                          "description": "a file where no paragraph matches is a no-op instead of not-found"},
        "tracked": {"type": "boolean", "enum": [False],
                    "description": "not tracked — Word cannot track a section break (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {
        "paragraphs": {"type": "object", "description": "targeted selector: the top-level body paragraphs each new "
                                                        "section starts at"}}}
    verification_profile = {"V2": "zero text diffs",
                            "V10": "inserted_section_breaks: the matches, refusals and governing sections re-derived; "
                                   "the main part compared whole with the source plus exactly the breaks (each carrier's "
                                   "copy of its section's sectPr, the split section's start)",
                            "V7": "none — the content after each break moves by design"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        what = BREAK_WORDS.get(params.get("type", "next_page"), "a section break")
        head = prose.sentence("insert", what, "before", prose.where(select)[3:])
        tail = ["the new section keeps the page setup, headers and footers of the section it splits",
                "the page numbers run on across the break", "the break is not tracked"]
        if params.get("type") in ("even_page", "odd_page"):
            tail.insert(2, "Word adds a blank page where the page before would end on the wrong side")
        if params.get("allow_missing") is True:
            tail.append("files without such a paragraph are left unchanged")
        return head + " " + prose.sentence(prose.join(tail))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select, params = dict(select or {}), dict(params or {})
        if set(select) - {"paragraphs"}:
            raise JobException(f"insert_section_break: unknown selector key(s) {sorted(set(select) - {'paragraphs'})} "
                               f"(it chooses body paragraphs)", "bad-params")
        pred = select.get("paragraphs")
        check_paragraph_selector(pred)
        if not is_targeted(select):
            raise JobException("insert_section_break needs a targeted 'paragraphs' selector naming the paragraph(s) "
                               "each new section starts at", "bad-params")
        if set(params) - {"type", "allow_missing", "tracked"}:
            raise JobException(f"insert_section_break: unknown param(s) "
                               f"{sorted(set(params) - {'type', 'allow_missing', 'tracked'})}", "bad-params")
        kind = params.get("type", "next_page")
        if kind not in START_TYPES:
            raise JobException(f"type must be one of {list(START_TYPES)}", "bad-params")
        if params.get("tracked") not in (None, False):
            raise JobException("a section break is not tracked (Word cannot track one): only tracked false is "
                               "accepted", "bad-params")
        allow_missing = params.get("allow_missing", False)
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        main = pkg.main_part()
        root = pkg.xml(main)
        body = root.find(W + "body")
        pars = paragraphs(root)
        where_of = {p: i for i, p in enumerate(pars)}          # keyed by element: the list keeps the proxies alive
        secs = DocxPackage.sect_prs(root)
        section_of = paragraph_sections(root)
        sheet = StyleSheet(pkg)
        frag = self.fragment(select, params)
        frag.geometry_reference, frag.geometry_reason = "none", "a section break moves the content after it"
        breaks: List[Dict[str, int]] = []
        for bi, p in enumerate(pars):
            if not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                continue
            where = f"paragraph {bi} ({snippet(p)})"
            details = {"paragraph": bi}
            if nearest_p(p) is not None:
                raise JobException(f"{where} is inside a text box; a section break goes only between paragraphs of "
                                   f"the body", "textbox", details)
            if p.getparent() is not body:
                inside = "a table" if any(a.tag == W + "tc" for a in p.iterancestors()) else \
                    "a content control or custom XML block"
                raise JobException(f"{where} is inside {inside}: a section break goes only between top-level "
                                   f"paragraphs of the body", "break-position", details)
            prev = previous_block(p)
            if prev is None:
                raise JobException(f"{where} is the first paragraph of the document: no paragraph before it can end "
                                   f"a section", "break-position", details)
            if prev.tag != W + "p":
                raise JobException(f"{where} follows {block_name(prev)}: no paragraph before it can carry the section "
                                   f"break — choose a paragraph that follows a paragraph, or add one after the "
                                   f"{etree.QName(prev).localname} in Word", "break-position", details)
            if prev.find(f"{W}pPr/{W}sectPr") is not None:
                raise JobException(f"{where} already starts a section: the paragraph before it holds a section break",
                                   "break-position", details)
            if mark_revised(prev):
                raise JobException(f"the paragraph before {where} has a pending tracked insertion or deletion of its "
                                   f"paragraph mark, which would carry the break: accept or reject it in Word first",
                                   "tracked-section", details)
            g = section_of[bi]
            if secs[g].find(W + "sectPrChange") is not None:
                raise JobException(f"section {g}, which {where} lies in, carries a pending tracked change of its page "
                                   f"setup (w:sectPrChange): which part of the split would keep it cannot be "
                                   f"predicted — accept or reject it in Word first", "tracked-section",
                                   {**details, "section": g})
            breaks.append({"paragraph": bi, "carrier": where_of[prev], "section": g})
        frag.property_assertions.append({
            # the user's own words and what the plan chose from them: V10 re-derives both and the whole main part
            "kind": "inserted_section_breaks", "part": main, "select": copy.deepcopy(select),
            "params": {k: copy.deepcopy(params[k]) for k in ("type", "allow_missing") if k in params},
            "breaks": [dict(b) for b in breaks], "expected_breaks": len(breaks),
            "sections": len(secs) + len(breaks)})
        frag.transform_data = {"main": main, "type": kind, "breaks": breaks}
        if not breaks:
            if not allow_missing:
                raise JobException(f"no paragraph matches {pred!r}", "not-found", {"paragraphs": pred})
            frag.notes.append(f"no paragraph matches {pred!r}; nothing to do here (allow_missing)")
            return frag
        frag.intended_parts = [main]
        # every section from the first split one on changes its index and the split section its start: the main part
        # changes as a whole for the ops beside it (a set_page_setup of a section by index would reach another one)
        frag.touches.append((main, "*", "sections"))
        self._notes(pkg, pars, secs, breaks, kind, frag)
        return frag

    @staticmethod
    def _notes(pkg: DocxPackage, pars, secs, breaks: List[Dict[str, int]], kind: str, frag: PlanFragment) -> None:
        """What each split section keeps and what review should look at (module docstring)."""
        eff = effective_refs(pkg, secs)
        by: Dict[int, List[Dict[str, int]]] = {}
        for b in breaks:
            by.setdefault(b["section"], []).append(b)
        for g, bs in sorted(by.items()):
            sp = secs[g]
            at = prose.join(snippet(pars[b["paragraph"]]) for b in bs)
            frag.notes.append(f"section {g} (it starts on {start_words(sp)}) splits into {len(bs) + 1} parts, at {at}: "
                              f"every part keeps its page setup, headers and footers; each new part starts on "
                              f"{START_WORDS[kind]}")
            restart = section_start(sp)[1]
            if restart is not None:
                frag.notes.append(f"section {g}'s page-number restart (at {restart}) stays with its first part; the "
                                  f"page numbers run on across the new break(s)")
            if title_pg_on(sp):
                shows = [f"{k} {eff[g].get((k, 'first'))}" if eff[g].get((k, "first")) else f"no {k}"
                         for k in ("header", "footer")]
                frag.flags.append(f"section {g} shows a different first page: the first page after each new break (at "
                                  f"{at}) will show its first-page header and footer ({', '.join(shows)}), not the "
                                  f"ordinary ones")
            ln = sp.find(W + "lnNumType")
            if ln is not None and ln.get(W + "restart") == "newSection":
                frag.flags.append(f"section {g}'s line numbers restart at each section: they start over at each new "
                                  f"break (at {at})")
            for name, words in (("footnotePr", "footnote"), ("endnotePr", "endnote")):
                rs = sp.find(f"{W}{name}/{W}numRestart")
                if rs is not None and rs.get(W + "val") == "eachSect":
                    frag.flags.append(f"section {g}'s {words} numbers restart at each section: they start over at each "
                                      f"new break (at {at})")
            cols = sp.find(W + "cols")
            if cols is not None and (cols.get(W + "num") or "1").isdigit() and int(cols.get(W + "num") or "1") > 1:
                frag.flags.append(f"section {g} is set in {cols.get(W + 'num')} columns: the columns of each part end "
                                  f"at its break (at {at})" + (" and are balanced there" if kind == "continuous" else ""))
            va = sp.find(W + "vAlign")
            if va is not None and va.get(W + "val") not in (None, "top"):
                frag.flags.append(f"section {g}'s text is aligned vertically ({va.get(W + 'val')}): the last page of "
                                  f"each part before a new break (at {at}) is aligned so too")
        if kind in ("even_page", "odd_page"):
            frag.notes.append(f"each new section starts on {START_WORDS[kind]}: Word adds a blank page where the page "
                              f"before would end on the wrong side")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["breaks"]:
            return
        root = pkg.xml(td["main"])
        pars = paragraphs(root)
        secs = DocxPackage.sect_prs(root)                # read before any copy goes in
        new_start = break_type(td["type"])
        by: Dict[int, List[Dict[str, int]]] = {}
        for b in td["breaks"]:
            by.setdefault(int(b["section"]), []).append(b)
        for g, bs in sorted(by.items()):
            governing = secs[g]
            source = copy.deepcopy(governing)
            source.tail = None
            for k, b in enumerate(sorted(bs, key=lambda x: x["paragraph"])):
                piece = copy.deepcopy(source)
                if k:                                    # a middle part starts with the asked break, no restart
                    set_start(piece, new_start, None)
                insert_in_order(paragraph_ppr(pars[int(b["carrier"])]), piece, PPR_ORDER)
            set_start(governing, new_start, None)
        pkg.write_xml(td["main"], root)

