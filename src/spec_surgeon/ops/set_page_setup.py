"""Op: set_page_setup — margins, page size, orientation and the different first page, per section (w:sectPr/w:pgMar,
w:pgSz, w:titlePg).

Margins merge into the existing w:pgMar (every attribute of pgMar is required by the schema, so a section
without one is an exception rather than a guess); ``margins.header`` / ``margins.footer`` are the header and footer
distances from the page edge. Orientation sets w:pgSz/@w:orient and swaps w/h when they disagree with it; margins are
not rotated (say so in the spec if they should change too). Page setup is not tracked (w:sectPrChange is not
implemented); ``tracked: true`` is refused.

Built-in batch 5 (docs/BUILTIN_OPS_BATCH5_DESIGN.md §7):

* ``different_first_page`` true → the section's w:titlePg present with no w:val (a ``w:val="0"`` removed); false →
  absent. A section where it is already in effect as asked (titlePg present and not off / absent or off) is left alone
  and not counted. Each changed section's note says what its first page will show, header and footer, through
  inheritance (``hf_common.effective_refs``: a section without a first-page reference shows the previous section's);
  a first page that will show NO header/footer (no first-page part in this section or any before it) or an EARLIER
  section's first-page part is flagged.
* ``select.sections`` keeps ``"all"`` and ``[indices]`` and gains a content form ``{containing, with_tables,
  index_range}`` (``selectors.resolve_sections``): every given key must hold. No section chosen → ``not-found``
  unless ``allow_missing`` (a no-op with a note).
* With an orientation, a header/footer part a changed section shows (any type it displays — default always, first
  with titlePg as it will be after the change, even with evenAndOddHeaders —, through inheritance) that is also shown
  by a section of the other orientation (after the change: w > h) is flagged, once per part: its right-aligned items
  stay where the portrait page puts them. The notes and flags are advice for review; V10 checks the sectPr changes.

V10 (``section_props``): one assertion carries the user's own ``select`` and parameters, the sections the plan chose
and each changed section's element specs; the verifier re-derives the choice and every change from the SOURCE.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import SECTPR_ORDER, insert_in_order
from . import prose, register
from .base import JobException, Op, PlanFragment
from .hf_common import effective_refs, refs
from .selectors import resolve_sections, section_reads

MARGIN_KEYS = ("top", "right", "bottom", "left", "header", "footer", "gutter")
FALSY = ("0", "false", "off")
ASSERTED_PARAMS = ("margins", "size", "orientation", "different_first_page", "allow_missing")
TITLE_PG_ON = {"present": True, "remove": ["val"]}      # section_props element specs (V10)
TITLE_PG_OFF = {"absent": True}


def title_pg_on(sp: Any) -> bool:
    """Different first page in effect: a w:titlePg whose w:val is absent or not off."""
    tp = sp.find(W + "titlePg")
    return tp is not None and tp.get(W + "val") not in FALSY


def page_orientation(sp: Any) -> str:
    """The shape of the section's page: landscape when its width exceeds its height (w:orient when they are missing)."""
    pg = sp.find(W + "pgSz")
    if pg is None:
        return "portrait"
    w, h = pg.get(W + "w") or "", pg.get(W + "h") or ""
    if w.isdigit() and h.isdigit():
        return "landscape" if int(w) > int(h) else "portrait"
    return "landscape" if pg.get(W + "orient") == "landscape" else "portrait"


def shown_parts(eff: List[Dict[Tuple[str, str], Optional[str]]], titles: List[bool], even_odd: bool,
                i: int) -> List[Tuple[str, str]]:
    """(kind, part) Word shows on section ``i``'s pages: default always, first with titlePg, even with
    evenAndOddHeaders."""
    out: List[Tuple[str, str]] = []
    for (kind, typ), part in sorted(eff[i].items()):
        if part and (typ == "default" or (typ == "first" and titles[i]) or (typ == "even" and even_odd)):
            if (kind, part) not in out:
                out.append((kind, part))
    return out


PAGE_SIZE_NAMES = {(12240, 15840): "US Letter", (12240, 20160): "US Legal", (15840, 24480): "Tabloid",
                   (11906, 16838): "A4", (16838, 23811): "A3", (8391, 11906): "A5"}


def page_size_words(w: Any, h: Any) -> str:
    """``US Letter (8.5 by 11 inches)``, ``8.27 by 11.69 inches`` — either way around for a landscape sheet."""
    try:
        wi, hi = int(w), int(h)
    except (TypeError, ValueError):
        return f"{w} by {h}"
    dims = f"{prose.inches(wi).split(' ')[0]} by {prose.inches(hi)}"
    name = PAGE_SIZE_NAMES.get((wi, hi)) or PAGE_SIZE_NAMES.get((hi, wi))
    return f"{name} ({dims})" if name else dims


def margin_words(margins: Dict[str, Any]) -> List[str]:
    """``every margin to 1 inch``, ``the top margin to 1 inch``, ``the header 0.5 inch from the page edge``."""
    out: List[str] = []
    edges = ("top", "bottom", "left", "right")
    if all(k in margins for k in edges) and len({str(margins[k]) for k in edges}) == 1:
        out.append(f"every margin to {prose.inches(margins['top'])}")
    else:
        out += [f"the {k} margin to {prose.inches(margins[k])}" for k in edges if k in margins]
    if "gutter" in margins:
        out.append(f"the gutter to {prose.inches(margins['gutter'])}")
    out += [f"the {k} {prose.inches(margins[k])} from the page edge" for k in ("header", "footer") if k in margins]
    return out


@register
class SetPageSetup(Op):
    name = "set_page_setup"
    mode = "formatting"
    description = ("Set margins (incl. header/footer distance from the page edge), page size, orientation and/or "
                   "different first page of selected sections — by index or by what they contain.")
    params_schema = {"type": "object", "properties": {
        "margins": {"type": "object",
                    "description": "top/right/bottom/left/header/footer/gutter in twips (1440 = 1 in); "
                                   "header/footer = the header's/footer's distance from the page edge"},
        "size": {"type": "object", "description": "{w, h} in twips (12240 x 15840 = US Letter)"},
        "orientation": {"type": "string", "enum": ["portrait", "landscape"],
                        "description": "swaps width and height where they disagree; margins are not rotated"},
        "different_first_page": {"type": "boolean",
                                 "description": "true: the section's first page shows its first-page header/footer "
                                                "(w:titlePg on); false: it shows the ordinary ones"},
        "allow_missing": {"type": "boolean", "default": False,
                          "description": "content-form sections: a file where no section matches is a no-op instead "
                                         "of not-found"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "properties": {"sections": {
        "description": "'all', [section indices] (0-based), or by content: {containing: <paragraph predicate>, "
                       "with_tables: bool, index_range: [first, last]} — every given key must hold"}}}
    verification_profile = {"V2": "zero text diffs",
                            "V10": "section_props: the selection re-derived from the user's select; per section "
                                   "pgMar/pgSz attributes exactly as set, titlePg present without w:val / absent; "
                                   "nothing else",
                            "V7": "none — page geometry changes by design"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        what: List[str] = []
        margins, size = params.get("margins"), params.get("size")
        if isinstance(margins, dict) and margins:
            what += margin_words(margins)
        if isinstance(size, dict) and size.get("w") is not None and size.get("h") is not None:
            what.append("the page size to " + page_size_words(size["w"], size["h"]))
        orient = params.get("orientation")
        if orient in ("portrait", "landscape"):
            what.append(f"the orientation to {orient}")
        first = params.get("different_first_page")
        first_clause = ("give the first page its own header and footer" if first is True else
                        "show the ordinary header and footer on the first page" if first is False else "")
        where = prose.where(select, default="every section")
        if what:
            head = prose.sentence("set", prose.join(what), where, f"and {first_clause}" if first_clause else "")
        elif first_clause:
            head = prose.sentence(first_clause, where)
        else:
            head = prose.sentence("set the margins, page size, orientation and first-page header and footer", where)
        extras: List[str] = []
        if orient in ("portrait", "landscape"):
            extras.append("the page width and height swap where they disagree with it")
            if not (isinstance(margins, dict) and margins):
                extras.append("the margins stay as they are")
        if params.get("allow_missing") is True:
            extras.append("files where no section matches are left unchanged")
        return head if not extras else head + " " + prose.sentence(prose.join(extras))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"sections"}:
            raise JobException(f"set_page_setup: unknown selector key(s) {sorted(set(select) - {'sections'})}", "bad-params")
        if params.get("tracked"):
            raise JobException("page setup changes are not tracked (w:sectPrChange not implemented)", "bad-params")
        margins = params.get("margins") or {}
        size = params.get("size") or {}
        orient = params.get("orientation")
        first = params.get("different_first_page")
        allow_missing = params.get("allow_missing", False)
        for name, d, keys in (("margins", margins, MARGIN_KEYS), ("size", size, ("w", "h"))):
            if not isinstance(d, dict) or set(d) - set(keys):
                raise JobException(f"{name} keys must be among {keys}", "bad-params")
            for k, v in d.items():
                if isinstance(v, bool) or not isinstance(v, int) or (v < 0 and k not in ("top", "bottom")):
                    raise JobException(f"{name}.{k} must be an integer number of twips", "bad-params")
        if size and set(size) != {"w", "h"}:
            raise JobException("size needs both w and h", "bad-params")
        if orient not in (None, "portrait", "landscape"):
            raise JobException("orientation must be 'portrait' or 'landscape'", "bad-params")
        if first is not None and not isinstance(first, bool):
            raise JobException("different_first_page must be true or false", "bad-params")
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        if not (margins or size or orient or first is not None):
            raise JobException("nothing to set: give margins, size, orientation and/or different_first_page",
                               "bad-params")
        chooser = select.get("sections", "all")
        ords = resolve_sections(pkg, chooser, content=True)
        main = pkg.main_part()
        secs = DocxPackage.sect_prs(pkg.xml(main))
        frag = self.fragment(select, params)
        frag.intended_parts = [main] if ords else []
        frag.geometry_reference, frag.geometry_reason = "none", "page setup changes move every page's content by design"
        # what the content form reads (its paragraphs' words and styles): another op changing them is a plan-time
        # conflict (rule 9)
        frag.touches += section_reads(pkg, chooser)
        if isinstance(chooser, dict) and ords and len(ords) == len(secs):
            # chosen by content, and every section matched — in a one-section file that is the whole document ("Schedule
            # 40 pipe" matches a 'schedule' pattern in the body of a piping spec): say so, the user decides (review)
            frag.flags.append(f"the content selection chose every section of this file ({len(secs)}): the change applies "
                              f"to the whole document, not to a part of it — check that the pattern does not match "
                              f"ordinary text")
        per_section: Dict[int, Dict[str, Dict[str, str]]] = {}
        asserted: Dict[str, Dict[str, Dict[str, Any]]] = {}     # section_props element specs per changed section
        titles: Dict[int, bool] = {}
        already: List[int] = []
        for i in ords:
            sp = secs[i]
            elements: Dict[str, Dict[str, str]] = {}
            if margins:
                if sp.find(W + "pgMar") is None:
                    raise JobException(f"section {i} has no w:pgMar (all of its attributes are required; not guessing "
                                       f"the missing ones)", "structure", {"section": i})
                elements["pgMar"] = {k: str(v) for k, v in margins.items()}
            if size or orient:
                pg = sp.find(W + "pgSz")
                w = size.get("w", int(pg.get(W + "w")) if pg is not None and pg.get(W + "w") else None)
                h = size.get("h", int(pg.get(W + "h")) if pg is not None and pg.get(W + "h") else None)
                if w is None or h is None:
                    raise JobException(f"section {i} has no page size to orient; give size", "structure", {"section": i})
                pg_set: Dict[str, str] = {}
                if orient:
                    if (orient == "landscape") != (w > h):
                        w, h = h, w
                    pg_set["orient"] = orient
                pg_set["w"], pg_set["h"] = str(w), str(h)
                elements["pgSz"] = pg_set
            if first is not None:
                # V10 reads every selected section's titlePg in the OUTPUT, an already-as-asked one included: another
                # op flipping it (the templating op following its template) is a plan-time conflict
                frag.touches.append((main, f"sectPr:{i}", "verify:titlePg"))
                if title_pg_on(sp) != first:
                    titles[i] = first
                else:
                    already.append(i)
            if elements or i in titles:
                per_section[i] = elements
                asserted[str(i)] = {name: {"set": attrs} for name, attrs in elements.items()}
            frag.touches += [(main, f"sectPr:{i}", name) for name in elements]
            if i in titles:
                asserted[str(i)]["titlePg"] = copy.deepcopy(TITLE_PG_ON if titles[i] else TITLE_PG_OFF)
                frag.touches.append((main, f"sectPr:{i}", "titlePg"))
        changed = sorted(per_section)
        frag.property_assertions.append({
            # the user's own words (absent = the verifier's default) and what the plan chose from them: V10 re-derives
            # the choice and each section's change and compares
            "kind": "section_props", "part": main, "select": copy.deepcopy(select),
            "params": {k: copy.deepcopy(params[k]) for k in ASSERTED_PARAMS if k in params},
            "selected": list(ords), "sections": changed, "per_section": asserted,
            "expected_sections": len(changed)})
        frag.transform_data = {"main": main, "sections": {str(i): e for i, e in per_section.items()},
                               "title_pg": {str(i): v for i, v in titles.items()}}
        if not ords:
            if not isinstance(chooser, dict):
                frag.notes.append("no section selected; nothing to do")
                return frag
            if not allow_missing:
                raise JobException(f"no section matches {chooser!r}", "not-found", {"sections": chooser})
            frag.notes.append(f"no section matches {chooser!r}; nothing to do here (allow_missing)")
            return frag
        if isinstance(chooser, dict):
            frag.notes.append(f"sections chosen by {chooser!r}: {list(ords)} of {len(secs)}")
        frag.notes.append(f"sections {ords}: " + "; ".join(
            f"{i}: {per_section.get(i, {})}" + (f" + titlePg {'on' if titles[i] else 'off'}" if i in titles else "")
            for i in ords))
        if already:
            frag.notes.append(f"different first page already {'on' if first else 'off'} in section(s) {already}: "
                              f"left alone")
        older = [i for i in changed if secs[i].find(W + "sectPrChange") is not None]
        if older:                          # the change is plain; a pending tracked one stays (batch 5's review)
            frag.flags.append(f"section(s) {older} already carry a pending tracked section change (w:sectPrChange) "
                              f"from an earlier reviewer; reject-all would restore that older page setup")
        if titles:
            self._first_pages(pkg, secs, titles, frag)
        if orient:
            frag.notes.append("orientation swaps page width/height where they disagree with it; margins are not rotated")
            self._mixed_orientation(pkg, secs, ords, orient, titles, frag)
        return frag

    @staticmethod
    def _first_pages(pkg: DocxPackage, secs, titles: Dict[int, bool], frag: PlanFragment) -> None:
        """What each changed section's first page will show (references do not change: what a section shows is
        resolved through inheritance from the source's; the first page shows the first-page parts with titlePg, the
        default ones without), and the flags (§7)."""
        eff = effective_refs(pkg, secs)
        own = [{(kind, typ) for kind, typ, _, _ in refs(sp)} for sp in secs]
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        for i, on in sorted(titles.items()):
            shows = []
            for kind in ("header", "footer"):
                typ = "first" if on else "default"
                part = eff[i].get((kind, typ))
                if part is None:
                    shows.append(f"no {kind}")
                    if on:
                        frag.flags.append(f"section {i}: different first page is turned on, but no section up to it "
                                          f"has a first-page {kind}: its first page will show no {kind} (blank)")
                    continue
                origin = next(j for j in range(i, -1, -1) if (kind, typ) in own[j])
                shows.append(f"{kind} {part}" + ("" if origin == i else f" (section {origin}'s, inherited)"))
                if on and origin != i:
                    frag.flags.append(f"section {i}: different first page is turned on and it has no first-page "
                                      f"{kind} of its own: its first page will show section {origin}'s first-page "
                                      f"{kind} {part} (Word's 'Link to Previous')")
            note = (f"section {i}: different first page {'on' if on else 'off'} — its first page shows "
                    + ", ".join(shows))
            if not on and even_odd:
                note += " (or the even-page ones when that page number is even)"
            frag.notes.append(note)

    @staticmethod
    def _mixed_orientation(pkg: DocxPackage, secs, ords: List[int], orient: str, titles: Dict[int, bool],
                           frag: PlanFragment) -> None:
        """A header/footer part a changed section shows, also shown by a section of the other orientation (after the
        change) — one flag per part."""
        eff = effective_refs(pkg, secs)
        even_odd = pkg.settings_flag("evenAndOddHeaders")
        after_titles = [titles.get(i, title_pg_on(sp)) for i, sp in enumerate(secs)]
        after_orient = [orient if i in ords else page_orientation(sp) for i, sp in enumerate(secs)]
        shown = [shown_parts(eff, after_titles, even_odd, i) for i in range(len(secs))]
        seen = set()
        for i in ords:
            for kind, part in shown[i]:
                if part in seen:
                    continue
                seen.add(part)
                by = [j for j in range(len(secs)) if (kind, part) in shown[j]]
                portrait = [j for j in by if after_orient[j] == "portrait"]
                landscape = [j for j in by if after_orient[j] == "landscape"]
                if portrait and landscape:
                    frag.flags.append(f"the {kind} {part} is shown on portrait and landscape pages (portrait: "
                                      f"sections {portrait}; landscape: sections {landscape}): its right-aligned "
                                      f"items stay where the portrait page puts them")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td["sections"] and not td.get("title_pg"):
            return                                  # every section already as asked, or none chosen
        root = pkg.xml(td["main"])
        secs = DocxPackage.sect_prs(root)
        for i, elements in td["sections"].items():
            sp = secs[int(i)]
            for name, attrs in elements.items():
                el = sp.find(W + name)
                if el is None:
                    el = insert_in_order(sp, etree.Element(W + name), SECTPR_ORDER)
                for k, v in attrs.items():
                    el.set(W + k, v)
        for i, on in td.get("title_pg", {}).items():
            sp = secs[int(i)]
            tp = sp.find(W + "titlePg")
            if on:
                if tp is None:
                    insert_in_order(sp, etree.Element(W + "titlePg"), SECTPR_ORDER)
                else:
                    tp.attrib.pop(W + "val", None)
            elif tp is not None:
                sp.remove(tp)
        pkg.write_xml(td["main"], root)
