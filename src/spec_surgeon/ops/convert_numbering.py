"""Op: convert_numbering — convert a document's numbering to the user's scheme (PART / 1.1 / A. / 1. / a.) from an
example file (built-in batch 5, item 33 ★ — docs/BUILTIN_OPS_BATCH5_DESIGN.md §1).

Formatting mode: zero text diffs. The numbers are not text: each converted paragraph gets the scheme level's paragraph
style and loses its own ``w:numPr``, so the scheme's list numbers it; the scheme's numbering text (``w:lvlText``, e.g.
``PART %1 -``) is the user's own, from the example file or the document (rule 4, decision 1).

Parameters: ``levels`` (1–9 paragraph styles of the scheme, level 0 first), ``template`` (the example file), ``existing``
(``keep`` | ``replace``, with a template) — ``numbering_convert`` (the destination, design §0.3); ``from``: ``"main"``
(default) — every counted paragraph numbered by any num of the **main abstractNum** (``lists.main_abstract``), its
level = its ilvl — or ``{"styles": [[level 0 styles], [level 1 styles], …]}`` — every counted, numbered paragraph whose
paragraph style (pStyle, else the default) is in level k's list (references resolved in the file; missing ones skipped,
noted; a numbered style's unnumbered paragraphs — ``numId`` 0 — are left alone, noted); ``level_map``
(``{"<source level>": <scheme level>}``, default identity); ``direct_formatting``: ``keep`` (default) | ``clear``
(apply_style's: clear_direct_formatting's DEFAULT rules on the paragraph, its runs and its mark); ``tracked`` (§14.1:
plain — a whole-document conversion — unless asked), ``author``; ``allow_missing`` (default true: a file with no numbered
paragraph to convert is a no-op with a note; false → ``not-found``); ``counters``: ``flag`` (default: a converted
paragraph whose counters change is flagged) | ``keep`` (refused instead, below); ``styles``: ``scheme`` (default: the
level's style, below) | ``keep`` (docs/SPEC_FORMATTER_MODES_DESIGN.md N5: the paragraph keeps its own ``w:pStyle`` and
its own ``w:numPr`` becomes ``w:ilvl`` = its level, ``w:numId`` = the destination list, at its CT_PPr place —
``renumber_edit``; already exactly so and nothing to clear: untouched; no restyle, so no F9 caps flag, a flag that the
paragraphs keep their own styles' indents instead, and a note that the imported level styles stay unused; touches
``pPr`` and ``numPr``, never ``pStyle`` or a style's ``uses``). ``select``: ``include_tables`` (default
true: a numbered paragraph in a table cell is converted like any; false → left out, and F7 then holds for it).

Definitions (the verifier's own reading is verify/numberingconvert.py):

* **Not evaluated in the SOURCE** (F8 of the batch-5 review): a converted paragraph whose number the source cannot
  evaluate converts when its LEVEL is known (``LEVEL_KNOWN``: issue ``shared``, ``start-override``, ``skipped``,
  ``override``; its ilvl and num read) and EVERY counted paragraph of its num is converted (``whole_list_chosen``) — the
  list leaves whole, its new numbers are predicted; flagged ("could not predict before the conversion … check them").
  Else refused (``numbering-unsupported``: an unreadable or unsupported list, an ``earlier``/``ambiguous-level``
  paragraph, a list converted in part — the reason says to convert every paragraph of it). An own numPr with only a
  ``w:ilvl`` is read by the list model (list from the style, that level) and converted like any (F5).
* Refused: one whose own numPr carries a tracked change (``existing-revisions``); a source level beyond the scheme after
  ``level_map`` (``scheme-depth``, file-specific); a predicted number that is not evaluated where the paragraph is
  converted — except a paragraph not evaluated before (``LEVEL_KNOWN``) that follows a skipped level at the level it
  had (``skipped_both``: the outline's own shape carried over; flagged) — or was evaluated in the source
  (``numbering-unsupported``); tracked when a converted paragraph's style chain holds a style the import REPLACES
  (``numbering_convert.replaced_in_chain``; ``tracked-format``, F14 — the replacement is never tracked, reject-all would
  give the old style back under the new numbered definition).
* ``counters: "keep"``: a converted paragraph whose predicted counters — the values
  of its level and the levels above it — are not its source counters (``counter_shift``) is refused
  (``numbering-mismatch``, naming the first ones: a renumbered article breaks "as specified in 1.3"); so is one whose
  number the source or the prediction cannot evaluate (``numbering-unsupported``: that it keeps its number cannot be
  shown) — the F8 and skipped-level conversions the default flags.
* **Paragraphs left out keep their numbers** (F7): every counted paragraph NOT converted must show, predicted, what it
  showed (``shows``: unnumbered, else rendered text, suffix and issue) — else ``numbering-mismatch`` naming the first ones
  ("their list no longer counts the converted paragraphs; convert every level") — except a paragraph the destination
  list numbers in the prediction (a held destination list, a style the import brings or replaces): flagged by name.
* The change of a converted paragraph: ``w:pStyle`` = the level's style (first in the pPr; a pPr created first in the
  paragraph), its own ``w:numPr`` removed, the cleared children gone with ``clear``; tracked: one ``w:pPrChange`` (the
  snapshot = the source pPr without rPr, sectPr, pPrChange, taken before all of it) and one ``w:rPrChange`` per cleared
  run or mark; existing change markers as apply_style (tracked → ``tracked-format``, plain → flagged). Already as asked
  (the style already the level's, no own numPr, nothing to clear): untouched, uncounted.
* **Predicted numbers**: ``lists.count`` over the main part with the planned output's styles and lists
  (``numbering_convert.Destination``) and ``override`` = every converted paragraph → (destination list, its level) —
  every counted paragraph's counters and rendered number (``numbering_convert.row``), carried by the assertion.
* Flagged: converted paragraphs whose counters change ("was shown as 2.04 and becomes 2.3": a source glitch corrected);
  converted paragraphs whose text starts with a typed number of the scheme (double numbering); REF/PAGEREF/NOTEREF
  fields whose bookmark lies in a converted paragraph (their cached number goes stale); every occurrence, in another
  paragraph's run text, of a converted paragraph's OLD rendered number that changes (a written cross-reference: never
  edited); every paragraph of the destination list not converted whose number changes (F7's exception); the converted
  paragraphs not evaluated before (F8) and those following a skipped level before and after; the destination's restart
  rules LibreOffice ignores (``lists.has_restart_rule``); converted paragraphs whose mark is hidden (``markup.Look``:
  Word counts them, LibreOffice does not — its render proof numbers what follows differently); restyled paragraphs whose
  lower-case text the new style shows in capitals (``_caps_text``, F9: their own rPr less what ``clear`` removes, over
  the planned chain — ``numbering_convert.planned_levels``/``look_of``); scheme level styles whose level is the linked
  level (``Destination.linked``, F3: LibreOffice's proofs show them at level 0).
* Refused at scheme resolution (``numbering_convert``): a scheme level whose ``w:lvl/w:rPr`` turns on vanish, caps or
  smallCaps (``bad-params`` in the template, ``scheme`` for a held list); with a template and existing "keep", a held
  level style numbering unlike the template's level (``scheme``, F6: use "replace"); an import into a file without a
  numbering part (``style-numbering``, F12).

Touches (rule 9): per changed paragraph ``(main, bi, "pPr")``, ``"pStyle"`` (restyled), ``"numPr"`` (own numPr removed),
each cleared pPr child by name, ``"rPr"``, ``"markRPr"``; ``uses+`` of the level style and ``uses-`` of the old style per
restyled paragraph; the import's touches; ``lists.count_reads`` (the numbering part whole, the main part's ``pStyle`` and
``numPr`` families, ``verify:numPr`` of every counted numbered paragraph, ``verify:pPr`` of every style on their chains and
the scheme's); ``(main, "scope", "verify")``; ``(styles, "style:<id>", "verify")`` of a held level style — each once
(F10: duplicates made ``merge_fragments`` quadratic).

Verified by V10's ``converted_numbering`` kind (verify/numberingconvert.py) — and, when styles are imported, by
``styles_imported`` (verify/styleimport.py).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import PPR_ORDER, RevisionStamp, file_uses_date_utc, insert_in_order, max_revision_id, paragraphs
from ..package import W, DocxPackage
from . import lists, prose, register
from .base import JobException, Op, PlanFragment
from .clear_formatting import ClearSettings, ParagraphClearing, changes_needed, check_markers, clear_paragraph, \
    paragraph_clearing
from .fields import fields_of
from .effective import PropResolver
from .markup import Look
from .numbering_convert import destination, look_of, planned_levels, replaced_in_chain, row, scheme_params, shown
# shared with set_list_level and audit_numbering's fix (ops/numbering_flags.py): what a renumbering leaves stale
from .numbering_flags import REF_FIELDS, number_rx
from .numbering_flags import bookmark_paragraph as _bookmark_paragraph
from .replace_text import default_author
from .selectors import StyleSheet, runs_by_paragraph
from .style_import import apply_import, check_template_unchanged, resolve_style
from .textedit import RunText, run_text

PARAM_KEYS = {"from", "level_map", "template", "levels", "existing", "direct_formatting", "tracked", "author",
              "allow_missing", "counters", "styles"}
USER_PARAMS = ("from", "level_map", "template", "levels", "existing", "direct_formatting", "tracked", "allow_missing",
               "counters", "styles")
STYLE_MODES = ("scheme", "keep")
REFLOW_REASON = "the paragraphs take the scheme's styles and list: numbers, indents and spacing change, text reflows"
KEEP_REFLOW_REASON = "the paragraphs take the scheme's list: numbers and list indents change, text reflows"
REVISION_IN_NUMPR = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "numberingChange"}


def restyle_edit(style_id: str):
    """A ``ppr_edit`` for ``clear_paragraph``: w:pStyle = ``style_id`` first in the pPr, the paragraph's own w:numPr
    removed (the style's numbering applies)."""
    def edit(ppr) -> None:
        ps = ppr.find(W + "pStyle")
        if ps is None:
            ps = etree.Element(W + "pStyle")
            ppr.insert(0, ps)
        ps.attrib.clear()
        ps.set(W + "val", style_id)
        for numpr in ppr.findall(W + "numPr"):
            ppr.remove(numpr)
    return edit


def renumber_edit(num_id: int, ilvl: int):
    """A ``ppr_edit`` for ``clear_paragraph`` (``styles: "keep"``, docs/SPEC_FORMATTER_MODES_DESIGN.md N5): the
    paragraph's own ``w:numPr`` becomes ``<w:ilvl w:val=ilvl/><w:numId w:val=num_id/>`` at its CT_PPr place (any own
    numPr it had removed first); its ``w:pStyle`` is left as it is."""
    def edit(ppr) -> None:
        for numpr in ppr.findall(W + "numPr"):
            ppr.remove(numpr)
        numpr = etree.Element(W + "numPr")
        etree.SubElement(numpr, W + "ilvl").set(W + "val", str(ilvl))
        etree.SubElement(numpr, W + "numId").set(W + "val", str(num_id))
        insert_in_order(ppr, numpr, PPR_ORDER)
    return edit


def numbered_as(p, num_id: int, ilvl: int) -> bool:
    """``styles: "keep"``: the paragraph's own ``w:numPr`` is already exactly ``ilvl`` then ``numId`` with those values
    (as written), nothing else in it."""
    numpr = p.find(f"{W}pPr/{W}numPr")
    if numpr is None or [c.tag for c in numpr] != [W + "ilvl", W + "numId"]:
        return False
    return numpr[0].get(W + "val") == str(ilvl) and numpr[1].get(W + "val") == str(num_id)


def clean_from(frm: Any, n_levels: int) -> Any:
    if frm == "main":
        return frm
    if not isinstance(frm, dict) or set(frm) != {"styles"} or not isinstance(frm["styles"], list) or \
            not 1 <= len(frm["styles"]) <= 9 or not all(
                isinstance(g, list) and g and all(isinstance(s, str) and s.strip() for s in g) for g in frm["styles"]):
        raise JobException("from must be 'main' or {\"styles\": [[level 0 styles], [level 1 styles], …]} (1 to 9 "
                           "non-empty lists of style names or ids)", "bad-params")
    flat = [s for g in frm["styles"] for s in g]
    if len(set(flat)) != len(flat):
        raise JobException(f"from.styles names a style twice: {flat}", "bad-params")
    return frm


def clean_level_map(lm: Any, n_levels: int) -> Dict[int, int]:
    if lm is None:
        return {}
    if not isinstance(lm, dict) or not all(isinstance(k, str) and re.fullmatch("[0-8]", k) for k in lm) or not all(
            isinstance(v, int) and not isinstance(v, bool) and 0 <= v < n_levels for v in lm.values()):
        raise JobException(f"level_map maps source levels ('0'–'8') to scheme levels (0–{n_levels - 1})", "bad-params")
    return {int(k): v for k, v in lm.items()}


LEVEL_KNOWN = ("shared", "start-override", "skipped", "override")    # F8: not evaluated, its level known


def shows(x: Optional[lists.NumberedPar]) -> Optional[Tuple[Any, ...]]:
    """What a paragraph's number shows, as F7 compares it: None (unnumbered), else (text, suffix, issue) — a number
    that is not evaluated equals only a number with the same issue."""
    return None if x is None else (x.text, x.suff, x.issue)


def skipped_both(x: lists.NumberedPar, y: Optional[lists.NumberedPar], lvl: int) -> bool:
    """A converted paragraph not predictable before (a ``LEVEL_KNOWN`` issue) whose predicted number follows a skipped
    level (``skipped``: a deeper level was numbered before this one had a value) at the level it had — the outline's
    own shape carried over as it is: Word and LibreOffice number it differently, before as after; converted, flagged."""
    return (y is not None and not x.evaluated and not y.evaluated and x.issue in LEVEL_KNOWN and y.issue == "skipped"
            and x.ilvl == lvl)


def counter_shift(x: lists.NumberedPar, y: Optional[lists.NumberedPar]) -> bool:
    """``counters: "keep"``: the converted paragraph's predicted counters are not its source counters (or either number
    is not evaluated, or it is no longer numbered)."""
    return y is None or not x.evaluated or not y.evaluated or tuple(y.counters) != tuple(x.counters)


def in_table(p) -> bool:
    """The paragraph stands in a table cell (any depth)."""
    q = p.getparent()
    while q is not None:
        if q.tag == W + "tc":
            return True
        q = q.getparent()
    return False


def whole_list_chosen(x: lists.NumberedPar, src, chosen_at) -> bool:
    """F8: every counted paragraph of ``x``'s list (its num) is among the chosen ones."""
    return all(y.index in chosen_at for y in src if y.num_id == x.num_id)


def number_words(x: Optional[lists.NumberedPar]) -> str:
    if x is None:
        return "unnumbered"
    return repr(x.text) if x.evaluated else f"(not predictable: {x.issue})"


# ---- the plan's prose (shared with convert_typed_numbers) ----------------------------------------------------------

def scheme_phrase(params: Dict[str, Any]) -> str:
    """``the scheme of the “PRT”, “ART” and “PR1” styles, imported with their list from the example file “T.docx”``;
    ``your scheme`` when no levels are given yet."""
    levels = params.get("levels")
    if not isinstance(levels, list) or not levels:
        return "your scheme"
    text = "the scheme of " + prose.styles(levels)
    template = params.get("template")
    if isinstance(template, str) and template.strip():
        text += f", imported with their list from the example file {prose.quote(template)}"
        if params.get("existing") == "replace":
            text += " (its definitions replacing this file's own)"
    return text


def level_style(levels: Any, k: Any) -> str:
    """The scheme level's style in words (``the “ART” style``), else the level by its number as Word counts it."""
    if isinstance(levels, list) and isinstance(k, int) and not isinstance(k, bool) and 0 <= k < len(levels):
        return prose.style(levels[k])
    return f"scheme level {k + 1}" if isinstance(k, int) and not isinstance(k, bool) else "its scheme level"


def level_map_pairs(lm: Any) -> Dict[int, int]:
    """``level_map`` as (source level, scheme level) pairs; anything unreadable is left out (plan refuses it)."""
    pairs: Dict[int, int] = {}
    if isinstance(lm, dict):
        for k, v in lm.items():
            try:
                pairs[int(k)] = int(v)
            except (TypeError, ValueError):
                continue
    return pairs


def tracked_words(params: Dict[str, Any]) -> str:
    """``shown as tracked changes by “Jane”``."""
    author = params.get("author")
    return "shown as tracked changes" + (f" by {prose.quote(author)}" if isinstance(author, str) and author else "")


def conversion_extras(params: Dict[str, Any], nothing: str) -> List[str]:
    """The non-default settings both conversions share, as clauses for a second sentence."""
    extras: List[str] = []
    if params.get("direct_formatting") == "clear":
        extras.append("the paragraphs' own direct formatting is cleared")
    if params.get("allow_missing") is False:
        extras.append(f"a file with {nothing} to convert is set aside")
    return extras


def with_second_sentence(first: str, extras: List[str]) -> str:
    """The first sentence, then the extras as one more sentence when there are any."""
    return first if not extras else first + " " + prose.sentence("; ".join(extras))


@register
class ConvertNumbering(Op):
    name = "convert_numbering"
    mode = "formatting"
    description = ("Convert the document's numbering to your scheme (PART / 1.1 / A. / 1. / a.) from an example file: "
                   "each numbered paragraph gets the scheme level's paragraph style (imported from the example file with "
                   "its list) and loses its own list numbering, so your list numbers it. The numbers are predicted for "
                   "every paragraph; shifts, stale cross-references and typed numbers are flagged. Adds no characters.")
    params_schema = {"type": "object", "required": ["levels"], "properties": {
        "levels": {"type": "array", "items": {"type": "string"},
                   "description": "the scheme's paragraph styles, level 0 first (ids or names as read_template shows "
                                  "them), e.g. [\"PRT\", \"ART\", \"PR1\", \"PR2\", \"PR3\"]"},
        "template": {"type": "string", "description": "the example file holding the scheme's styles and their list"},
        "existing": {"type": "string", "enum": ["keep", "replace"], "default": "keep",
                     "description": "with template: a level style this file already has keeps this file's definition "
                                    "(keep) or takes the example file's (replace)"},
        "from": {"type": ["string", "object"], "default": "main",
                 "description": "'main' (the list numbering the most paragraphs, each at its own level) or "
                                "{\"styles\": [[level 0 styles], [level 1 styles], …]}"},
        "level_map": {"type": "object", "description": "{\"<source level>\": <scheme level>} when the levels differ"},
        "direct_formatting": {"type": "string", "enum": ["keep", "clear"], "default": "keep",
                              "description": "keep the paragraphs' own formatting, or clear it by "
                                             "clear_direct_formatting's default rules"},
        "tracked": {"type": "boolean", "default": False,
                    "description": "w:pPrChange per paragraph (w:rPrChange per cleared run); default plain"},
        "author": {"type": "string"},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "a file with nothing numbered to convert is a no-op (false: set aside)"},
        "counters": {"type": "string", "enum": ["flag", "keep"], "default": "flag",
                     "description": "flag: a converted paragraph whose number counts differently is flagged; keep: "
                                    "the file is set aside instead (every number keeps its values)"},
        "styles": {"type": "string", "enum": list(STYLE_MODES), "default": "scheme",
                   "description": "scheme: each converted paragraph takes the scheme level's paragraph style; keep: it "
                                  "keeps its own style and only its numbering changes (its own list numbering points "
                                  "at your list's level) — the numbers change, the look stays"}}}
    select_schema = {"type": "object", "properties": {
        "include_tables": {"type": "boolean", "default": True,
                           "description": "numbered paragraphs in table cells too (false: left as they are; a cell "
                                          "numbered by the converted list then sets the file aside)"}},
        "description": "the main document's numbered paragraphs ('from' says which)"}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per changed paragraph, +1 w:rPrChange per cleared run/mark",
        "V10": "converted_numbering: the scheme, the converted paragraphs, their levels and changes re-derived from the "
               "source; every counted paragraph's number predicted by the verifier's own list model equals the plan's "
               "and its count of the output; each changed pPr exactly; styles_imported for the import; residual identical",
        "V7": "none — numbers, indents and spacing change"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        levels = params.get("levels")
        level_map = level_map_pairs(params.get("level_map"))
        frm = params.get("from", "main")
        keep_styles = params.get("styles") == "keep"
        if isinstance(frm, dict) and isinstance(frm.get("styles"), list) and frm["styles"]:
            moves: List[str] = []
            for k, refs in enumerate(frm["styles"]):
                refs = refs if isinstance(refs, list) else [refs]
                group = prose.styles(refs) if refs else "no style"
                target = level_style(levels, level_map.get(k, k))
                if keep_styles:
                    moves.append(f"the numbered paragraphs in {group} are numbered at the level of {target}" if k == 0
                                 else f"those in {group} at the level of {target}")
                else:
                    moves.append(f"the numbered paragraphs in {group} take {target}" if k == 0
                                 else f"those in {group} {target}")
            source = prose.join(moves) + (", keeping each paragraph's own style" if keep_styles
                                          else ", each losing its own list numbering, so your list numbers it")
        else:
            mapped = ""
            if level_map:
                mapped = " (" + prose.join(f"level {k + 1} of the list going to {level_style(levels, v)}"
                                           for k, v in sorted(level_map.items())) + ")"
            if keep_styles:
                source = ("every paragraph numbered by the document's main list is numbered by your list at the level "
                          "of its scheme style" + mapped + ", keeping each paragraph's own style")
            else:
                source = ("every paragraph numbered by the document's main list takes the paragraph style of its level"
                          + mapped + " and loses its own list numbering, so your list numbers it")
        how = f", {tracked_words(params)}" if params.get("tracked") is True else ""
        first = prose.sentence(f"Convert the document's numbering to {scheme_phrase(params)}{how}: {source}")
        extras = []
        if (select or {}).get("include_tables") is False:
            extras.append("numbered paragraphs in table cells are left as they are")
        if params.get("counters") == "keep":
            extras.append("a file where a converted paragraph's number would count differently is set aside")
        return with_second_sentence(first, extras + conversion_extras(params, "nothing numbered"))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"include_tables"}:
            raise JobException(f"convert_numbering takes only include_tables as a selector (got {sorted(select)}); "
                               f"'from' says which paragraphs", "bad-params")
        if "include_tables" in select and not isinstance(select["include_tables"], bool):
            raise JobException("include_tables must be true or false", "bad-params")
        tables = select.get("include_tables", True)
        unknown = set(params) - PARAM_KEYS
        if unknown:
            raise JobException(f"convert_numbering: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                               "bad-params")
        levels, template, existing = scheme_params(params)
        direct = params.get("direct_formatting", "keep")
        if direct not in ("keep", "clear"):
            raise JobException("direct_formatting must be 'keep' or 'clear'", "bad-params")
        for k in ("tracked", "allow_missing"):
            if k in params and not isinstance(params[k], bool):
                raise JobException(f"{k} must be true or false", "bad-params")
        keep = params.get("counters", "flag")
        if keep not in ("flag", "keep"):
            raise JobException("counters must be 'flag' or 'keep'", "bad-params")
        keep = keep == "keep"
        style_mode = params.get("styles", "scheme")
        if style_mode not in STYLE_MODES:
            raise JobException("styles must be 'scheme' or 'keep'", "bad-params")
        keep_styles = style_mode == "keep"
        frm = clean_from(params.get("from", "main"), len(levels))
        level_map = clean_level_map(params.get("level_map"), len(levels))
        tracked = bool(params.get("tracked", False))
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("this file has no styles part; no paragraph style can be applied", "structure")
        main = pkg.main_part()
        root = pkg.xml(main)
        pars = paragraphs(root)
        groups = runs_by_paragraph(root, pars)
        defs = lists.ListDefs.load(pkg)
        src = lists.count(root, sheet, defs)
        by_index = {x.index: x for x in src}
        dest = destination(pkg, sheet, defs, src, levels, template, existing)
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})

        # ---- which paragraphs, at which level ----
        chosen: List[Tuple[lists.NumberedPar, int]] = []
        notes: List[str] = []
        if frm == "main":
            a = lists.main_abstract(src)
            if a is not None:
                chosen = [(x, x.ilvl) for x in src if x.abstract_id == a]
                notes.append(f"the main list (abstractNum {a}) numbers {len(chosen)} counted paragraph(s)")
        else:
            level_of: Dict[str, int] = {}
            missing: List[str] = []
            for k, refs in enumerate(frm["styles"]):
                for ref in refs:
                    sid = resolve_style(sheet.styles, ref, "this file")
                    if sid is None:
                        missing.append(ref)
                    elif sid in level_of:
                        raise JobException(f"from.styles: {ref!r} is style {sid}, already named for level "
                                           f"{level_of[sid]}", "ambiguous", {"style": sid})
                    else:
                        level_of[sid] = k
            if missing:
                notes.append(f"from.styles: not in this file, skipped: {missing}")
            off = 0
            for i, p in enumerate(pars):
                if not lists.counted(p):
                    continue
                st = lists.paragraph_style(p, sheet)
                if st not in level_of:
                    continue
                if i in by_index:
                    chosen.append((by_index[i], level_of[st]))
                else:
                    off += 1
            if off:
                notes.append(f"{off} paragraph(s) of those styles carry no number (numbering off): left alone")
        if not tables:
            celled = [x for x, _ in chosen if in_table(pars[x.index])]
            if celled:
                chosen = [(x, k) for x, k in chosen if not in_table(pars[x.index])]
                notes.append(f"{len(celled)} numbered paragraph(s) in table cells left as they are (include_tables: "
                             f"false)")

        # ---- refusals and levels ----
        converted: List[Tuple[lists.NumberedPar, int]] = []
        unpredicted: List[int] = []                      # F8: converted, their source number not predictable
        chosen_at = {x.index for x, _ in chosen}
        replaced = set(dest.imp.replaced) if dest.imp is not None else set()
        for x, src_level in chosen:
            p = pars[x.index]
            where = f"{main} paragraph {x.index} ({shown(run_text(p))!r})"
            if not x.evaluated:
                known = x.issue in LEVEL_KNOWN and x.ilvl is not None and x.num_id is not None
                if not (known and whole_list_chosen(x, src, chosen_at)):
                    raise JobException(f"{where}: its number cannot be read with certainty — {x.not_evaluated}"
                                       + (f"; it can be converted only with every paragraph of list {x.num_id} "
                                          f"(from: \"main\", or every level's styles in from.styles)"
                                          if known else ""),
                                       "numbering-unsupported", {"where": where, "issue": x.issue})
                unpredicted.append(x.index)
            numpr = p.find(f"{W}pPr/{W}numPr")
            if numpr is not None and any(c.tag in REVISION_IN_NUMPR for c in numpr.iter()):
                raise JobException(f"{where}: its own numbering carries a pending tracked change; accept or reject it "
                                   f"first", "existing-revisions", {"where": where})
            hit = replaced_in_chain(sheet, p, replaced)
            if tracked and hit:
                raise JobException(f"{where}: its style {hit[0]} is one whose definition the example file replaces "
                                   f"(existing 'replace', never tracked) — reject-all would give the paragraph back its "
                                   f"old style under the new numbered definition; convert with tracked: false, or keep "
                                   f"this file's definitions (existing 'keep')", "tracked-format",
                                   {"where": where, "style": hit[0]})
            lvl = level_map.get(src_level, src_level)
            if lvl >= len(dest.scheme.levels):
                raise JobException(f"{where}: source level {src_level} maps to scheme level {lvl}, and the scheme has "
                                   f"{len(dest.scheme.levels)} level(s) ({dest.sids})", "scheme-depth",
                                   {"where": where, "level": lvl})
            converted.append((x, lvl))
        if not converted:
            if params.get("allow_missing", True) is False:
                raise JobException("no numbered paragraph to convert in this file", "not-found")

        # ---- the predicted numbers ----
        override = {x.index: (dest.scheme.num_id, lvl) for x, lvl in converted}
        predicted = lists.count(root, dest.sheet, dest.defs, override)
        pred_at = {x.index: x for x in predicted}
        carried: List[int] = []                          # skipped before and after: the source's own uncertainty
        for x, lvl in converted:
            y = pred_at.get(x.index)
            if skipped_both(x, y, lvl):
                carried.append(x.index)
                continue
            if y is None or not y.evaluated:
                raise JobException(f"{main} paragraph {x.index} ({shown(run_text(pars[x.index]))!r}): its number in "
                                   f"the converted list cannot be predicted — {y.not_evaluated if y else 'not numbered'}",
                                   "numbering-unsupported", {"index": x.index})
        for x in src:
            y = pred_at.get(x.index)
            if x.evaluated and y is not None and not y.evaluated:
                raise JobException(f"{main} paragraph {x.index} ({shown(run_text(pars[x.index]))!r}): the conversion "
                                   f"would leave its number unpredictable — {y.not_evaluated}",
                                   "numbering-unsupported", {"index": x.index})
        # F7: a paragraph left out shows the number it showed (the destination list's own: flagged by name below)
        conv_at = {x.index for x, _ in converted}
        moved = [i for i in sorted(set(by_index) | set(pred_at))
                 if i not in conv_at and shows(by_index.get(i)) != shows(pred_at.get(i))
                 and not (i in pred_at and pred_at[i].num_id == dest.scheme.num_id)]
        if moved:
            raise JobException(f"{main}: the conversion would change the number of {len(moved)} paragraph(s) it does "
                               f"not convert — "
                               + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’ {number_words(by_index.get(i))} → "
                                           f"{number_words(pred_at.get(i))}" for i in moved[:6])
                               + (" …" if len(moved) > 6 else "")
                               + " — their list no longer counts the converted paragraphs; convert every level of that "
                                 "list (from: \"main\", or every level's styles in from.styles)",
                               "numbering-mismatch", {"paragraphs": moved[:20]})
        if keep:
            # E3: every converted paragraph keeps its counters — the source's, predicted again in the scheme's list
            unproven = [x.index for x, _ in converted if not x.evaluated or not pred_at[x.index].evaluated]
            if unproven:
                raise JobException(f"{main}: counters 'keep' — the number of {len(unproven)} converted paragraph(s) "
                                   f"cannot be read with certainty before or after the conversion, so that it stays "
                                   f"cannot be shown: "
                                   + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’" for i in unproven[:6])
                                   + (" …" if len(unproven) > 6 else ""), "numbering-unsupported",
                                   {"paragraphs": unproven[:20]})
            shifted = [x for x, _ in converted if counter_shift(x, pred_at.get(x.index))]
            if shifted:
                raise JobException(f"{main}: counters 'keep' — {len(shifted)} converted paragraph(s) would count "
                                   f"differently: "
                                   + "; ".join(f"‘{shown(run_text(pars[x.index]), 40)}’ {x.text!r} → "
                                               f"{pred_at[x.index].text!r}" for x in shifted[:6])
                                   + (" …" if len(shifted) > 6 else "")
                                   + " — a written reference such as 'as specified in 1.3' would point elsewhere",
                                   "numbering-mismatch", {"paragraphs": [x.index for x in shifted[:20]]})

        # ---- the changes ----
        settings = ClearSettings() if direct == "clear" else None
        changed: List[list] = []
        uses: set = set()
        already = n_paras = n_restyled = n_runs = n_marks = rev_ids = older_r = older_p = 0
        for x, lvl in converted:
            bi, p = x.index, pars[x.index]
            sid = dest.sids[lvl]
            ps = p.find(f"{W}pPr/{W}pStyle")
            old = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph")
            if keep_styles:
                # its own style stays; its own numPr is written to the destination list's level (``own``: it changes)
                restyle = False
                own = not numbered_as(p, dest.scheme.num_id, lvl)
            else:
                restyle = old != sid
                own = p.find(f"{W}pPr/{W}numPr") is not None
            d = paragraph_clearing(p, groups[bi], sheet, settings) if settings else ParagraphClearing()
            if not restyle and not own and not d.changed:
                already += 1
                continue
            ppr_change = restyle or own or bool(d.ppr)
            o_r, o_p = check_markers(p, groups[bi], d, tracked, f"{main} paragraph {bi}", ppr_changes=ppr_change)
            older_r, older_p = older_r + o_r, older_p + o_p
            changed.append([bi, lvl, restyle, own, [[ri, list(n)] for ri, n in d.runs], d.mark, list(d.ppr)])
            rev_ids += changes_needed(d, ppr_changes=restyle or own)
            n_paras += int(ppr_change)
            n_restyled += int(restyle)
            n_runs += len(d.runs)
            n_marks += int(bool(d.mark))
            if ppr_change:
                frag.touches.append((main, str(bi), "pPr"))
            if restyle:
                frag.touches.append((main, str(bi), "pStyle"))
                uses.add((sheet.part, f"style:{sid}", "uses+"))
                if old:
                    uses.add((sheet.part, f"style:{old}", "uses-"))
            if own:
                frag.touches.append((main, str(bi), "numPr"))
            frag.touches += [(main, str(bi), name) for name in d.ppr]
            if d.runs:
                frag.touches.append((main, str(bi), "rPr"))
            if d.mark:
                frag.touches.append((main, str(bi), "markRPr"))
        frag.touches += sorted(uses)
        frag.touches += lists.count_reads(pkg, sheet, src, extra_styles=[s for s in dest.sids if s in sheet.styles])
        frag.touches += [(sheet.part, f"style:{s}", "verify") for s in dict.fromkeys(dest.sids)
                         if s in sheet.styles and s not in dest.requested]
        frag.touches.append((main, "scope", "verify"))
        frag.touches = list(dict.fromkeys(frag.touches))
        if changed:
            frag.intended_parts.append(main)

        # ---- the record ----
        imp = dest.imp
        frag.property_assertions.append({
            "kind": "converted_numbering", "part": main, "select": select,
            "params": {k: params[k] for k in USER_PARAMS if k in params},
            "template": dest.tpl.path if dest.tpl is not None else None,
            "template_md5": dest.tpl.md5 if dest.tpl is not None else None,
            "author": author, "dest_num": dest.scheme.num_id, "styles": list(dest.sids), "requested": dest.requested,
            "converted": [[x.index, lvl] for x, lvl in converted],
            "changed": [[c[0], c[1], c[2], c[3]] for c in changed],
            "expected_paragraphs": n_paras, "expected_restyled": n_restyled, "expected_runs": n_runs,
            "expected_marks": n_marks, "predicted": [row(y) for y in predicted]})
        frag.transform_data = {"part": main, "changed": changed, "styles": list(dest.sids), "tracked": tracked,
                               "keep_styles": keep_styles, "num_id": dest.scheme.num_id,
                               "author": author, "import": imp.transform_data() if imp is not None and imp.changes else None,
                               "template": dest.tpl.path if dest.tpl is not None else None,
                               "template_md5": dest.tpl.md5 if dest.tpl is not None else None}
        if imp is not None and imp.changes:
            frag.intended_parts += [p for p in imp.intended_parts() if p not in frag.intended_parts]
            frag.touches += imp.touches()
            frag.property_assertions.append(imp.assertion())
            frag.notes += imp.notes
            frag.flags += imp.flags
        if tracked and rev_ids:
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            if n_runs + n_marks:
                frag.rev_delta["rPrChange"] = n_runs + n_marks
            frag.rev_ids_needed = rev_ids
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if changed or (imp is not None and imp.changes):
            frag.geometry_reference = "none"
            frag.geometry_reason = KEEP_REFLOW_REASON if keep_styles else REFLOW_REASON

        # ---- notes and flags ----
        frag.notes += notes
        frag.notes.append(f"scheme: {dest.sids} numbered by list {dest.scheme.num_id} "
                          f"({'this file’s own' if dest.held else 'imported from the example file'}): "
                          + ", ".join(f"level {k} {sl.level.text!r}" for k, sl in enumerate(dest.scheme.levels)))
        frag.notes.append(f"{len(converted)} numbered paragraph(s) converted: "
                          + (f"each keeps its own style, {sum(1 for c in changed if c[3])} take their own numbering "
                             f"from list {dest.scheme.num_id}" if keep_styles else
                             f"{n_restyled} restyled, {sum(1 for c in changed if c[3])} lose their own numbering")
                          + (f"; direct formatting cleared on {sum(1 for c in changed if c[6])} paragraph(s), "
                             f"{n_runs} run(s), {n_marks} mark(s)" if settings else "")
                          + f"; {'tracked' if tracked else 'plain'}")
        if already:
            frag.notes.append(f"already as asked, left alone: {already} paragraph(s)")
        if keep_styles and imp is not None and imp.changes:
            frag.notes.append(f"the scheme's level styles {dest.sids} are imported with their list and stay unused: "
                              f"each converted paragraph keeps its own style (styles 'keep')")
        if keep_styles and any(c[3] for c in changed):
            frag.flags.append(f"{sum(1 for c in changed if c[3])} converted paragraph(s) keep their own style: where "
                              f"that style sets its own indents or spacing, those stay, and the list level's indent "
                              f"may not show — check the outline's indents in the preview")
        if not converted:
            frag.notes.append("nothing numbered to convert in this file; no change")
        self._flags(frag, root, pars, src, predicted, converted, dest, main,
                    [i for i in unpredicted if i not in carried])
        if carried:
            frag.flags.append(f"{len(carried)} converted paragraph(s) follow a level the outline skipped (a deeper level "
                              f"was numbered before this one had a value), before the conversion and after it: Word "
                              f"starts the level when it next comes, LibreOffice gave it its start then and continues — "
                              f"the two number them differently, and the engine predicts neither; check them: "
                              + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’" for i in carried[:6])
                              + (" …" if len(carried) > 6 else ""))
        look = Look(sheet)
        hidden = [x.index for x, _ in converted if look.mark_hidden(pars[x.index]) is not False]
        if hidden:
            frag.flags.append(f"{len(hidden)} converted paragraph(s) are hidden (their paragraph mark): Word counts them "
                              f"and the prediction follows Word, LibreOffice does not — its render proof numbers the "
                              f"paragraphs after them differently: "
                              + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’" for i in hidden[:6]))
        caps_text = self._caps_text(look, PropResolver(dest.sheet), pars, groups, changed, dest)
        if caps_text:
            frag.flags.append(f"{len(caps_text)} paragraph(s) take a scheme style that shows text in capitals: their "
                              f"words will read in capitals (‘General’ → ‘GENERAL’), the letters stay as typed: "
                              + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’" for i in caps_text[:6]))
        if dest.linked:
            frag.flags.append(f"your scheme's style(s) {dest.linked} do not name their level (w:ilvl) in their numbering; "
                              f"Word shows them right, LibreOffice's proof PDFs show them at level 0 — add the level "
                              f"to those styles in the example file to make the proofs agree")
        if older_r or older_p:
            frag.flags.append(f"{older_r} changed run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change from an earlier reviewer; reject-all would restore that older "
                              f"snapshot.")
        return frag

    @staticmethod
    def _caps_text(look: Look, planned: PropResolver, pars, groups, changed, dest) -> List[int]:
        """F9: restyled paragraphs holding a run of lower-case text that is not in capitals now (caps and smallCaps
        off) and will be under the level's style — its own rPr less what ``clear`` removes, over the planned chain."""
        out: List[int] = []
        for bi, lvl, restyle, own, runs_, mark, ppr in changed:
            if not restyle:
                continue
            p, sid = pars[bi], dest.sids[lvl]
            cleared = {ri: set(names) for ri, names in runs_}
            rt = RunText(p)
            for ri, r in enumerate(groups[bi]):
                text = "".join(rt.text[k] for k, ps in enumerate(rt.positions) if ps.run is r)
                rpr = r.find(W + "rPr")
                if not any(ch.islower() for ch in text) or any(look.pr.run_prop(rpr, p, e)[0] is not False
                                                               for e in ("caps", "smallCaps")):
                    continue
                after = rpr
                if ri in cleared and rpr is not None:
                    after = copy.deepcopy(rpr)
                    for c in list(after):
                        if etree.QName(c).localname in cleared[ri] and c.tag.startswith(W):
                            after.remove(c)
                now = look_of(planned_levels(planned, p, sid, after), after)
                if now["caps"] is not False or now["smallCaps"] is not False:
                    out.append(bi)
                    break
        return out

    @staticmethod
    def _flags(frag: PlanFragment, root, pars, src, predicted, converted, dest, main: str, unpredicted=()) -> None:
        by_src = {x.index: x for x in src}
        by_pred = {x.index: x for x in predicted}
        conv = {x.index for x, _ in converted}
        shifts = []
        for x, _ in converted:
            y = by_pred[x.index]
            if not x.evaluated:
                continue                                  # F8: flagged on its own
            if tuple(y.counters) != tuple(x.counters):
                shifts.append(f"‘{shown(run_text(pars[x.index]), 40)}’ was {x.text!r} and becomes {y.text!r}")
        if shifts:
            frag.flags.append(f"{len(shifts)} converted paragraph(s) count differently than before (a numbering glitch "
                              f"in the source corrected — check it is what you want): " + "; ".join(shifts[:6])
                              + (" …" if len(shifts) > 6 else ""))
        typed = [x.index for x, _ in converted if lists.typed_number(run_text(pars[x.index]), dest.scheme) is not None]
        if typed:
            frag.flags.append(f"{len(typed)} converted paragraph(s) also start with a typed number (it will show twice): "
                              + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’" for i in typed[:6]))
        stale = []
        for f in fields_of(root):
            if f.type not in REF_FIELDS:
                continue
            words = f.instruction.split()
            if len(words) < 2:
                continue
            target = _bookmark_paragraph(root, words[1])
            if target is None:
                continue
            i = next((k for k, p in enumerate(pars) if p is target), None)
            if i in conv:
                holder = f.paragraph()
                stale.append(f"{f.type} {words[1]} → ‘{shown(run_text(pars[i]), 30)}’"
                             + (f" in ‘{shown(run_text(holder), 30)}’" if holder is not None else ""))
        if stale:
            frag.flags.append(f"{len(stale)} cross-reference field(s) point at converted paragraphs; their shown number is "
                              f"a cached result that goes stale until fields are updated in Word: " + "; ".join(stale[:6]))
        texts = [run_text(p) for p in pars]
        refs = []
        for x, _ in converted:
            y = by_pred[x.index]
            old = (x.text or "").strip()
            if not old or old == (y.text or "").strip():
                continue
            rx = number_rx(old)
            hits = [i for i, t in enumerate(texts) if i != x.index and rx.search(t)]
            if hits:
                refs.append(f"{old!r} (now {y.text!r}) in " + ", ".join(f"‘{shown(texts[i], 40)}’" for i in hits[:3])
                            + (" …" if len(hits) > 3 else ""))
        if refs:
            frag.flags.append(f"written references to old numbers that change (content: flagged, never edited): "
                              + "; ".join(refs[:8]) + (" …" if len(refs) > 8 else ""))
        moved = []
        for i in sorted(set(by_src) | set(by_pred)):
            if i in conv:
                continue
            a, b = by_src.get(i), by_pred.get(i)
            if shows(a) != shows(b):                     # F7: only the destination list's own get here (else refused)
                moved.append(f"‘{shown(run_text(pars[i]), 40)}’ {number_words(a)} → {number_words(b)}")
        if moved:
            why = "this file’s own list" if dest.held else "a style the import brings or replaces"
            frag.flags.append(f"{len(moved)} paragraph(s) not converted change their number: the destination list "
                              f"{dest.scheme.num_id} numbers them too ({why}), and the converted paragraphs now count "
                              f"in it: " + "; ".join(moved[:6]) + (" …" if len(moved) > 6 else ""))
        if unpredicted:
            frag.flags.append(f"{len(unpredicted)} paragraph(s) had numbers the engine could not predict before the "
                              f"conversion (a restarted or shared list): their new numbers are predicted, whether they "
                              f"changed is not known — check them: "
                              + "; ".join(f"‘{shown(run_text(pars[i]), 40)}’ → {by_pred[i].text!r}"
                                          for i in unpredicted[:6])
                              + (" …" if len(unpredicted) > 6 else ""))
        if dest.restart_levels:
            frag.flags.append(f"levels {dest.restart_levels} of the destination list carry a restart rule "
                              f"(w:lvlRestart) that LibreOffice ignores: the render proof may number them differently "
                              f"from Word (the prediction follows Word)")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td.get("template") and not td.get("import"):
            check_template_unchanged(td["template"], td["template_md5"])     # rule 12: the plan read it
        apply_import(pkg, td.get("import"))
        if not td["changed"]:
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        root = pkg.xml(td["part"])
        pars = paragraphs(root)
        groups = runs_by_paragraph(root, pars)
        for bi, lvl, restyle, own, runs_, mark, ppr in td["changed"]:
            d = ParagraphClearing(runs=[(ri, list(n)) for ri, n in runs_], mark=mark, ppr=list(ppr))
            if td.get("keep_styles"):
                ppr_edit = renumber_edit(td["num_id"], lvl) if own else None
            else:
                ppr_edit = restyle_edit(td["styles"][lvl]) if (restyle or own) else None
            next_id = clear_paragraph(pars[bi], groups[bi], d, stamp, next_id, ppr_edit=ppr_edit)
        pkg.write_xml(td["part"], root)
