"""Op: convert_typed_numbers — typed "A." and "1." become real list numbers (built-in batch 5, item 34 ★ —
docs/BUILTIN_OPS_BATCH5_DESIGN.md §2).

The op REMOVES characters — each converted paragraph's typed number and the whitespace after it — and writes none: the
scheme's list (the user's own ``w:lvlText``, rule 4) shows the number instead. It converts a file only where the list
shows exactly the typed numbers: the document reads the same afterwards (§2.4), else the file is refused.

Parameters: ``levels``, ``template``, ``existing`` — ``numbering_convert`` (the destination, design §0.3);
``direct_formatting`` (``keep`` | ``clear``: apply_style's — clear_direct_formatting's DEFAULT rules on the paragraph,
its runs and its mark; with a TRACKED conversion refused ``bad-params``, see below); ``tracked`` (default **true** —
characters are removed, batch 1 rule 2; false → untracked, clean copies); ``author``; ``allow_missing`` (default true: a
file with no typed number to convert is a no-op with a note; false → ``not-found``). ``select``: ``paragraphs`` (the
paragraph selector grammar; default all), ``include_tables`` (default false: a typed "1." in a table cell is left alone).
Main document only.

Definitions (the verifier's own reading is verify/typednumbers.py):

* A **candidate** is a counted paragraph of the main part (``lists.counted``) that is unnumbered (``lists.
  paragraph_numbering`` None or dangling), selected, and not in a table unless ``include_tables``. Its **typed number**:
  ``lists.typed_number`` on its run text (batch 1's definition, ``textedit.run_text``) — one scheme level's pattern
  after optional spaces/tabs, then spaces/tabs/NBSPs or the end; the **prefix** = position 0 to the end of that
  whitespace. Two levels matching → the file is refused (``ambiguous``). A NUMBERED paragraph whose text starts with a
  typed number is flagged (double numbering), never touched.
* **It reads as typed, and the number looks like it** (``display_problem``; run text ignores display properties): the
  file is refused (``typed-format``) when a run of the prefix is effectively hidden (``w:vanish`` or ``w:specVanish`` on
  or ambiguous: ``hidden_toggle``, batch 2's toggle rule) — a hidden "A.⇥" would become a visible number; when a run
  holding a character of the typed number is in capitals or small capitals (on or ambiguous) and the typed number holds
  a lower-case letter — "a." reads "A." (F4 of the batch-5 review: "PART 1 -", "1.1", "A." read the same in capitals and
  convert); when the paragraph mark is effectively hidden (Word formats the list number with the mark: it would be
  hidden, and LibreOffice joins the paragraph to the next); when, in the planned output, the list number
  (``numbering_convert.number_look``: the mark's own rPr over the NEW style's chain, the level's ``w:lvl/w:rPr`` on
  top) is hidden, or in capitals while the level's number can hold a lower-case letter, or in a symbol font
  (``number_symbolic``), or a typed run is in one (``selectors.is_symbol_rpr``); and when a typed run and the number,
  both over the planned chain, differ in strike, double strike, vertical alignment, position, colour (auto ≡ 000000) or
  size (F1: a white, 1 pt, superscript or struck mark made an invisible or odd number that passed). A difference in
  bold, italic, underline or a non-symbol font is flagged. A scheme level whose ``w:lvl/w:rPr`` turns on
  vanish/caps/smallCaps is refused at scheme resolution (``numbering_convert``).
* Tracked, refused (``tracked-format``, F14) when a candidate's style chain holds a style the import REPLACES
  (``numbering_convert.replaced_in_chain``): the replacement is never tracked — reject-all would bring the typed number
  back under the new numbered definition ("A. A. First").
* The change of a converted paragraph: the prefix removed by the in-paragraph edit core (one edit ``(0, end, "")`` with
  ``tabs=True``: the tab after a typed number goes with it — design §2.3), ``w:pStyle`` = the level's style (first in the
  pPr), its own ``w:numPr`` (a ``numId`` 0 or dangling one) removed, the cleared children gone with ``clear`` (cleared
  BEFORE the edit, on the source's runs). Tracked: the prefix in ``w:del`` (one per run it touches), one ``w:pPrChange``
  (snapshot before all of it). Refused: a level whose suffix (``w:suff``) is ``nothing`` (``numbering-mismatch``: the
  typed number was followed by whitespace); own numPr carrying a tracked change (``existing-revisions``); the edit core's
  refusals (``structure``, ``field-result``, ``nested-container``, ``tracked-format``); existing change markers as
  apply_style. **Tracked + clear** is refused (``bad-params``): a run would carry a tracked deletion and a tracked
  formatting change at once, which the item model cannot verify — convert plain, or keep the direct formatting.
* **The guarantee** (§2.4): the plan predicts every counted paragraph's number over the planned output (``lists.count``
  with the planned styles/lists and ``override`` = every converted paragraph → (destination list, its typed level)) and
  REFUSES the file (``numbering-mismatch``, naming the first paragraphs) unless the visible line
  (``numbering_convert.visible``: the rendered number, its suffix, the run text — whitespace runs collapsed, the ends
  stripped; a not-evaluated number equal only to itself) of EVERY counted paragraph of the main part is unchanged: a
  converted paragraph shows its typed number exactly, a paragraph the destination list already numbered keeps its number.
  A typo in the typed numbers (two "B."s, a skipped letter) therefore refuses the file; a skipped level (an article
  "1.1" before any PART) is not evaluated, so it refuses too — the reason then names the nearest earlier unconverted
  line that looks like a typed number (``audit_numbering.typed_shape``) but matched no level, and what the level needs
  (F11: "PART 1 – GENERAL" with an en dash).
* Flagged: the look differences above; paragraphs whose lower-case text the new style shows in capitals (F9: "General"
  reads "GENERAL" under PRT); scheme level styles whose level is the linked level (``Destination.linked``, F3:
  LibreOffice's proofs show them at level 0); the destination's restart rules; double numbering.

Touches (rule 9): per converted paragraph ``(main, bi, "text")`` (the edit core), ``"pPr"``, ``"pStyle"`` (restyled),
``"numPr"`` (own numPr removed), each cleared pPr child, ``"rPr"``, ``"markRPr"``; ``uses+``/``uses-`` as apply_style; the
import's touches; ``lists.count_reads``; ``(main, "*", "verify:text")`` (the post-condition reads every paragraph's text:
any other text change in the main part in the same spec is a conflict); ``(main, "scope", "verify")``; what the display
rule read — ``selectors.chain_reads`` of each converted paragraph with its prefix runs' and its mark's rPr (families
``replace`` and ``rPr``: a style import swapping a chain style, a style font/property/language change), ``verify:rPr`` of
the new style's chain and of docDefaults — each once (F10).

Verified by V10's ``typed_numbers`` kind (verify/typednumbers.py) — and, when styles are imported, by
``styles_imported``.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id, paragraphs
from ..package import W, DocxPackage
from . import lists, prose, register
from .audit_numbering import typed_shape
from .base import JobException, Op, PlanFragment
from .clear_formatting import ClearSettings, ParagraphClearing, changes_needed, check_markers, clear_paragraph, \
    paragraph_clearing
from .convert_numbering import (REVISION_IN_NUMPR, conversion_extras, restyle_edit, scheme_phrase, tracked_words,
                                with_second_sentence)
from .effective import PropResolver
from .markup import Look
from .numbering_convert import (LOOK_FLAGGED, LOOK_REFUSED, destination, level_sample, look_of, number_look,
                                number_symbolic, planned_levels, replaced_in_chain, row, scheme_params, shown,
                                shows_lowercase, visible)
from .replace_text import default_author
from .selectors import (DEFAULT_PRESERVE_FONTS, StyleSheet, chain_reads, check_paragraph_selector, is_symbol_rpr,
                        paragraph_selected, runs_by_paragraph, table_ordinals)
from .slots import field_runs
from .style_import import apply_import, check_template_unchanged
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, plan_paragraph, run_text

PARAM_KEYS = {"template", "levels", "existing", "direct_formatting", "tracked", "author", "allow_missing"}
USER_PARAMS = ("template", "levels", "existing", "direct_formatting", "tracked", "allow_missing")
REFLOW_REASON = "the typed numbers become list numbers with the scheme's indents; text reflows"
MISMATCH_SHOWN = 5


LOOK_WORDS = {"strike": "struck through", "dstrike": "double struck through", "vertAlign": "raised or lowered "
              "(superscript / subscript)", "position": "raised or lowered (position)", "color": "in another colour",
              "sz": "in another size"}
FLAG_WORDS = {"b": "bold", "i": "italic", "u": "underline", "font": "font"}


def hidden_toggle(look: Look, rpr, p) -> Optional[bool]:
    """Hidden: the effective ``w:vanish`` or ``w:specVanish`` (batch 2's toggle rule) on; None when either is
    ambiguous and neither is on."""
    a, b = look.toggle(rpr, p, "vanish"), look.toggle(rpr, p, "specVanish")
    if a is True or b is True:
        return True
    return None if (a is None or b is None) else False


def display_problem(look: Look, planned: PropResolver, planned_sheet, sheet, p, prefix_runs, token_runs, token: str,
                    sid: str, scheme, level: int, level_rpr) -> Tuple[Optional[str], List[str]]:
    """(why the typed number does not read as its run text says, or its list number would not look like it — None when
    it does; the properties that differ only in a way flagged). Refused, in this order:

    * a run of the prefix effectively hidden (``hidden_toggle``: vanish or specVanish, on or ambiguous);
    * a run holding a character of the typed number in capitals or small capitals (on or ambiguous) when the number
      holds a lower-case letter ("a." reads "A."; "PART 1 -", "1.1", "A." read the same in capitals — F4);
    * the paragraph mark effectively hidden (Word hides the number with it, LibreOffice joins the paragraph to the next);
    * the list number in the planned output (``numbering_convert.number_look``: the mark's properties over the NEW
      style's chain, the level's ``w:lvl/w:rPr`` on top) hidden, or in capitals when the level's number can hold a
      lower-case letter (``shows_lowercase``); in a symbol font (``number_symbolic``), or a typed number in one
      (``selectors.is_symbol_rpr``);
    * a run of the typed number and the list number differing in ``numbering_convert.LOOK_REFUSED`` — strike, double
      strike, vertical alignment, position, colour (auto ≡ 000000), size; an ambiguous toggle counts as a difference
      (F1: a white, 1 pt, superscript or struck number passed before). Both are read in the planned output, over the
      same chain (``planned_levels`` with the NEW style): the typed run's own rPr vs the mark's with the level's on top —
      a style change alters the text and the number alike, so only what the mark (or the level) does differently from
      the typed runs counts.

    Flagged, not refused: a difference in ``LOOK_FLAGGED`` — bold, italic, underline, a (non-symbol) font."""
    for r in prefix_runs:
        h = hidden_toggle(look, r.find(W + "rPr"), p)
        if h is not False:
            return "the typed number is hidden text" + ("" if h else " (its hidden setting is ambiguous)"), []
    if any(ch.islower() for ch in token):
        for r in token_runs:
            rpr = r.find(W + "rPr")
            for e, what in (("caps", "capitals"), ("smallCaps", "small capitals")):
                v = look.pr.run_prop(rpr, p, e)[0]
                if v is not False:
                    return (f"the typed {token!r} is shown in {what}" + ("" if v else " (ambiguous)")
                            + " — the reader does not see the letters it is typed with"), []
    ppr = p.find(W + "pPr")
    h = hidden_toggle(look, ppr.find(W + "rPr") if ppr is not None else None, p)
    if h is not False:
        return ("the paragraph mark is hidden" + ("" if h else " (ambiguous)")
                + " — Word would hide the list number with it, LibreOffice joins the paragraph to the next"), []
    num = number_look(planned, p, sid, level_rpr)
    if num["vanish"] is not False or num["specVanish"] is not False:
        return (f"with style {sid} the list number would be hidden — Word formats it with the paragraph mark's "
                f"run properties and the level's own"), []
    if shows_lowercase(scheme, level) and (num["caps"] is not False or num["smallCaps"] is not False):
        return (f"with style {sid} the list number would be in capitals — Word formats it with the paragraph mark's "
                f"run properties and the level's own: a lower-case number would not show as typed"), []
    if number_symbolic(planned_sheet, p, sid, level_rpr, DEFAULT_PRESERVE_FONTS):
        return f"with style {sid} the list number would be in a symbol font (its glyphs are not letters)", []
    if any(is_symbol_rpr(r.find(W + "rPr"), p, sheet, DEFAULT_PRESERVE_FONTS) for r in token_runs):
        return "the typed number is in a symbol font", []
    flagged: List[str] = []
    for r in token_runs:
        rpr = r.find(W + "rPr")
        typed = look_of(planned_levels(planned, p, sid, rpr), rpr)      # as typed, under the same new style
        for e in LOOK_REFUSED:
            if typed[e] != num[e] or typed[e] is None:
                return (f"the list number would be {LOOK_WORDS[e]} — it takes the paragraph mark's formatting "
                        f"(with style {sid}), the typed {token!r} its own run's ({e}: typed {typed[e]!r}, number "
                        f"{num[e]!r})"), []
        flagged += [e for e in LOOK_FLAGGED if typed[e] != num[e] and e not in flagged]
    return None, flagged


def in_table(p) -> bool:
    q = p.getparent()
    while q is not None:
        if q.tag == W + "tc":
            return True
        q = q.getparent()
    return False


@register
class ConvertTypedNumbers(Op):
    name = "convert_typed_numbers"
    mode = "content"
    description = ("Turn typed numbers (\"A.\", \"1.\", \"1.1\", \"PART 1 -\") into real list numbers of your scheme "
                   "(levels from an example file): the typed number and the space or tab after it are removed and the "
                   "scheme's list shows the same number — only where the whole document then reads exactly as before "
                   "(a typo such as two \"B.\"s sets the file aside). Removes characters, adds none; tracked by default.")
    params_schema = {"type": "object", "required": ["levels"], "properties": {
        "levels": {"type": "array", "items": {"type": "string"},
                   "description": "the scheme's paragraph styles, level 0 first, e.g. [\"PRT\", \"ART\", \"PR1\", "
                                  "\"PR2\", \"PR3\"]"},
        "template": {"type": "string", "description": "the example file holding the scheme's styles and their list"},
        "existing": {"type": "string", "enum": ["keep", "replace"], "default": "keep"},
        "direct_formatting": {"type": "string", "enum": ["keep", "clear"], "default": "keep",
                              "description": "keep the paragraphs' own formatting, or clear it (only with tracked: false)"},
        "tracked": {"type": "boolean", "default": True,
                    "description": "true = the typed numbers deleted as tracked changes; false = removed outright"},
        "author": {"type": "string"},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "a file with no typed number to convert is a no-op (false: set aside)"}}}
    select_schema = {"type": "object", "properties": {
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"},
        "include_tables": {"type": "boolean", "default": False,
                           "description": "typed numbers in table cells too (default: left alone)"}}}
    verification_profile = {
        "V2": "untracked: each converted paragraph's text loses exactly its typed number", "V3": "untracked: the tab "
        "after a typed number goes", "V4": "tracked: +1 w:del per run the typed number touches, +1 w:pPrChange per "
        "paragraph", "V8": "tracked: the accept-all view without the typed numbers",
        "V10": "typed_numbers: the candidates, typed numbers, prefixes and levels re-derived from the source; each "
               "converted paragraph item by item (the edit core, tabs on) and its pPr; every counted paragraph reads the "
               "same in the output (number + text) as in the source; the plan's predicted numbers equal the verifier's "
               "and its count of the output; styles_imported for the import",
        "V7": "none — list indents replace typed spacing"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        where = prose.where(select)
        subject = where[3:] if where.startswith("in ") else where
        how = tracked_words(params) if params.get("tracked", True) is not False else "without tracked changes"
        first = prose.sentence(f"Turn the typed numbers at the start of {subject} into real list numbers of "
                               f"{scheme_phrase(params)}: each typed number and the space or tab after it are removed, "
                               f"{how}, and your list shows the same number in its place")
        extras = ["nothing is added, and a file is converted only where the whole document then reads exactly as before"]
        if select.get("include_tables") is True:
            extras.append("typed numbers in table cells are converted too")
        extras += conversion_extras(params, "no typed number")
        return with_second_sentence(first, extras)

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"paragraphs", "include_tables"}:
            raise JobException(f"convert_typed_numbers: unknown selector key(s) "
                               f"{sorted(set(select) - {'paragraphs', 'include_tables'})}", "bad-params")
        unknown = set(params) - PARAM_KEYS
        if unknown:
            raise JobException(f"convert_typed_numbers: unknown parameter(s) {sorted(unknown)}; known: "
                               f"{sorted(PARAM_KEYS)}", "bad-params")
        levels, template, existing = scheme_params(params)
        direct = params.get("direct_formatting", "keep")
        if direct not in ("keep", "clear"):
            raise JobException("direct_formatting must be 'keep' or 'clear'", "bad-params")
        for k in ("tracked", "allow_missing"):
            if k in params and not isinstance(params[k], bool):
                raise JobException(f"{k} must be true or false", "bad-params")
        if "include_tables" in select and not isinstance(select["include_tables"], bool):
            raise JobException("include_tables must be true or false", "bad-params")
        tracked = bool(params.get("tracked", True))
        if tracked and direct == "clear":
            raise JobException("direct_formatting 'clear' with a tracked conversion: a run would carry a tracked "
                               "deletion and a tracked formatting change at once — convert with tracked: false, or keep "
                               "the direct formatting", "bad-params")
        pred = select.get("paragraphs", "all")
        check_paragraph_selector(pred)
        tables = bool(select.get("include_tables", False))
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
        by_src = {x.index: x for x in src}
        dest = destination(pkg, sheet, defs, src, levels, template, existing)
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        texts = [run_text(p) for p in pars]

        # ---- candidates and their typed numbers ----
        look = Look(sheet)
        planned = PropResolver(dest.sheet)
        found: List[Tuple[int, lists.TypedNumber]] = []
        double: List[int] = []
        look_flags: Dict[str, List[int]] = {}
        caps_text: List[int] = []
        replaced = set(dest.imp.replaced) if dest.imp is not None else set()
        for i, p in enumerate(pars):
            if not lists.counted(p):
                continue
            nb = lists.paragraph_numbering(p, sheet, defs)
            if nb is not None and not nb.dangling:
                if lists.typed_number(texts[i], dest.scheme) is not None:
                    double.append(i)
                continue
            if in_table(p) and not tables:
                continue
            if not paragraph_selected(pred, p, i, block_full_text(p), sheet, all_pars=pars):
                continue
            tn = lists.typed_number(texts[i], dest.scheme)
            if tn is None:
                continue
            where = f"{main} paragraph {i} ({shown(texts[i])!r})"
            if tn.ambiguous:
                raise JobException(f"{where}: the typed {tn.token!r} fits "
                                   + " and ".join(f"level {k} (numbered like {level_sample(dest.scheme.level_defs, k)!r})"
                                                  for k in tn.levels)
                                   + " of the scheme — a roman numeral is also a run of letters, so the typed text "
                                     "cannot tell its level; type those numbers so they fit one level, or use a scheme "
                                     "whose levels cannot be confused", "ambiguous",
                                   {"where": where, "levels": list(tn.levels)})
            if dest.scheme.levels[tn.level].level.suff == "nothing":
                raise JobException(f"{where}: level {tn.level} of the scheme puts nothing between the number and the "
                                   f"text (w:suff nothing), the typed number is followed by a space or tab — the "
                                   f"paragraph would read differently", "numbering-mismatch", {"where": where})
            sid = dest.sids[tn.level]
            rt = RunText(p)
            why, flagged = display_problem(look, planned, dest.sheet, sheet, p, rt.runs_in(0, tn.end),
                                           rt.runs_in(tn.token_start, tn.token_start + len(tn.token)), tn.token, sid,
                                           dest.scheme, tn.level, dest.level_rprs.get(tn.level))
            if why:
                raise JobException(f"{where}: {why}; the typed number cannot become a list number that reads the same",
                                   "typed-format", {"where": where})
            numpr = p.find(f"{W}pPr/{W}numPr")
            if numpr is not None and any(c.tag in REVISION_IN_NUMPR for c in numpr.iter()):
                raise JobException(f"{where}: its own numbering carries a pending tracked change; accept or reject it "
                                   f"first", "existing-revisions", {"where": where})
            hit = replaced_in_chain(sheet, p, replaced)
            if tracked and hit:
                raise JobException(f"{where}: its style {hit[0]} is one whose definition the example file replaces "
                                   f"(existing 'replace', never tracked) — reject-all would bring the typed number back "
                                   f"under the new numbered definition and show it twice; convert with tracked: false, "
                                   f"or keep this file's definitions (existing 'keep')", "tracked-format",
                                   {"where": where, "style": hit[0]})
            for e in flagged:
                look_flags.setdefault(e, []).append(i)
            for r in rt.runs_in(tn.end, len(rt.text)):           # F9: capitals the new style brings to the text
                rest = "".join(rt.text[k] for k, ps in enumerate(rt.positions) if ps.run is r and k >= tn.end)
                rpr = r.find(W + "rPr")
                if any(ch.islower() for ch in rest) and all(look.pr.run_prop(rpr, p, e)[0] is False
                                                            for e in ("caps", "smallCaps")):
                    after = look_of(planned_levels(planned, p, sid, rpr), rpr)
                    if after["caps"] is not False or after["smallCaps"] is not False:
                        caps_text.append(i)
                        break
            found.append((i, tn))
        if not found and params.get("allow_missing", True) is False:
            raise JobException("no typed number to convert in this file", "not-found")

        # ---- the guarantee: every counted paragraph reads the same ----
        override = {i: (dest.scheme.num_id, tn.level) for i, tn in found}
        predicted = lists.count(root, dest.sheet, dest.defs, override)
        pred_at = {x.index: x for x in predicted}
        ends = {i: tn.end for i, tn in found}
        bad: List[str] = []
        hint = None
        for i, p in enumerate(pars):
            if not lists.counted(p):
                continue
            before = visible(by_src.get(i), texts[i])
            y = pred_at.get(i)
            after = visible(y, texts[i][ends[i]:] if i in ends else texts[i])
            if before != after:
                if y is not None and y.issue == "skipped" and hint is None:
                    hint = self._skipped_hint(pars, texts, i, y, by_src, ends, dest)
                if y is not None and y.issue == "skipped":
                    after = f"(no number: its level {y.ilvl} names a level no paragraph reached) {texts[i][ends.get(i, 0):]}"
                bad.append(f"‘{shown(before)}’ would read ‘{shown(after)}’")
        if bad:
            raise JobException(f"the list would not show the typed numbers exactly — {len(bad)} paragraph(s) would read "
                               f"differently: " + "; ".join(bad[:MISMATCH_SHOWN]) + (" …" if len(bad) > MISMATCH_SHOWN
                                                                                       else "")
                               + (f". {hint}" if hint else "")
                               + " (a typo in the typed numbers, a skipped level, or a list that already numbers "
                                 "paragraphs here; the audit shows where)", "numbering-mismatch",
                               {"paragraphs": bad[:20]})

        # ---- the changes ----
        in_field = field_runs(root)
        tables_k = table_ordinals(root)
        settings = ClearSettings() if direct == "clear" else None
        edits: Dict[str, List[List[Any]]] = {}
        changed: List[list] = []
        uses: set = set()
        n_paras = n_restyled = n_runs = n_marks = ppr_ids = older_r = older_p = 0
        per_level = [0] * len(dest.scheme.levels)
        for i, tn in found:
            p = pars[i]
            where = f"{main} paragraph {i}"
            done = plan_paragraph(frag, main, i, p, [Edit(0, tn.end, "")], tracked, in_field, where,
                                  note=f"typed number {tn.token!r} removed (level {tn.level})", tabs=True)
            edits[str(i)] = edits_to_data(done)
            sid = dest.sids[tn.level]
            ps = p.find(f"{W}pPr/{W}pStyle")
            old = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph")
            restyle = old != sid
            own = p.find(f"{W}pPr/{W}numPr") is not None
            d = paragraph_clearing(p, groups[i], sheet, settings) if settings else ParagraphClearing()
            ppr_change = restyle or own or bool(d.ppr)
            o_r, o_p = check_markers(p, groups[i], d, tracked, where, ppr_changes=ppr_change)
            older_r, older_p = older_r + o_r, older_p + o_p
            changed.append([i, tn.level, restyle, own, [[ri, list(n)] for ri, n in d.runs], d.mark, list(d.ppr)])
            ppr_ids += changes_needed(d, ppr_changes=restyle or own)
            per_level[tn.level] += 1
            n_paras += int(ppr_change)
            n_restyled += int(restyle)
            n_runs += len(d.runs)
            n_marks += int(bool(d.mark))
            if ppr_change:
                frag.touches.append((main, str(i), "pPr"))
            if restyle:
                frag.touches.append((main, str(i), "pStyle"))
                uses.add((sheet.part, f"style:{sid}", "uses+"))
                if old:
                    uses.add((sheet.part, f"style:{old}", "uses-"))
            if own:
                frag.touches.append((main, str(i), "numPr"))
            frag.touches += [(main, str(i), name) for name in d.ppr]
            if d.runs:
                frag.touches.append((main, str(i), "rPr"))
            if d.mark:
                frag.touches.append((main, str(i), "markRPr"))
            # what the display rule read (rule 9): the prefix runs' and the mark's effective formatting through their
            # style chains, and the new style's chain for the planned mark
            mrpr = p.find(f"{W}pPr/{W}rPr")
            rprs = [r.find(W + "rPr") for r in RunText(p).runs_in(0, tn.end)] + [mrpr]
            frag.touches += sorted(chain_reads(sheet, main, p, tables_k, rprs, ("replace", "rPr")))
            frag.touches += [(sheet.part, f"style:{s}", "verify:rPr") for s, _ in lists.style_chain(dest.sheet, sid)
                             if s in sheet.styles]           # an imported one: the import's own touches
        if found:
            frag.touches.append((sheet.part, "docDefaults", "verify:rPr"))
        frag.touches += sorted(uses)
        frag.touches += lists.count_reads(pkg, sheet, src, extra_styles=[s for s in dest.sids if s in sheet.styles])
        frag.touches += [(sheet.part, f"style:{s}", "verify") for s in dict.fromkeys(dest.sids)
                         if s in sheet.styles and s not in dest.requested]
        frag.touches += [(main, "*", "verify:text"), (main, "scope", "verify")]
        if found and main not in frag.intended_parts:
            frag.intended_parts.append(main)

        # ---- the record ----
        imp = dest.imp
        frag.property_assertions.append({
            "kind": "typed_numbers", "part": main, "select": select,
            "params": {k: params[k] for k in USER_PARAMS if k in params},
            "template": dest.tpl.path if dest.tpl is not None else None,
            "template_md5": dest.tpl.md5 if dest.tpl is not None else None,
            "author": author, "dest_num": dest.scheme.num_id, "styles": list(dest.sids), "requested": dest.requested,
            "converted": [[i, tn.level, tn.end] for i, tn in found],
            "changed": [[c[0], c[1], c[2], c[3]] for c in changed],
            "expected_paragraphs": n_paras, "expected_restyled": n_restyled, "expected_runs": n_runs,
            "expected_marks": n_marks, "predicted": [row(y) for y in predicted]})
        frag.transform_data = {"part": main, "edits": edits, "changed": changed, "styles": list(dest.sids),
                               "tracked": tracked, "author": author,
                               "import": imp.transform_data() if imp is not None and imp.changes else None,
                               "template": dest.tpl.path if dest.tpl is not None else None,
                               "template_md5": dest.tpl.md5 if dest.tpl is not None else None,
                               "use_date_utc": file_uses_date_utc(pkg)}
        if imp is not None and imp.changes:
            frag.intended_parts += [p for p in imp.intended_parts() if p not in frag.intended_parts]
            frag.touches += imp.touches()
            frag.property_assertions.append(imp.assertion())
            frag.notes += imp.notes
            frag.flags += imp.flags
        if tracked and found:
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            frag.rev_ids_needed = frag.rev_delta.get("del", 0) + frag.rev_delta.get("ins", 0) + ppr_ids
            frag.rev_id_base = max_revision_id(pkg) + 1
        if found or (imp is not None and imp.changes):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON

        # ---- notes and flags ----
        frag.notes.append(f"scheme: {dest.sids} numbered by list {dest.scheme.num_id} "
                          f"({'this file’s own' if dest.held else 'imported from the example file'}): "
                          + ", ".join(f"level {k} {sl.level.text!r}" for k, sl in enumerate(dest.scheme.levels)))
        if found:
            frag.notes.append(f"{len(found)} typed number(s) become list numbers ("
                              + ", ".join(f"level {k}: {n}" for k, n in enumerate(per_level) if n)
                              + f"); every counted paragraph reads the same — "
                              + ("tracked deletions of the typed numbers" if tracked else "removed untracked")
                              + (f"; direct formatting cleared on {sum(1 for c in changed if c[6])} paragraph(s), "
                                 f"{n_runs} run(s), {n_marks} mark(s)" if settings else ""))
        else:
            frag.notes.append("no typed number of the scheme in this file; no change")
        if not tables:
            skipped = sum(1 for i, p in enumerate(pars) if in_table(p) and lists.counted(p) and i not in by_src
                          and lists.typed_number(texts[i], dest.scheme) is not None)
            if skipped:
                frag.notes.append(f"{skipped} typed number(s) in table cells left as typed (include_tables: false)")
        if double:
            frag.flags.append(f"{len(double)} numbered paragraph(s) also start with a typed number (it shows twice), "
                              f"left as they are: " + "; ".join(f"‘{shown(texts[i], 40)}’" for i in double[:6]))
        if dest.restart_levels:
            frag.flags.append(f"levels {dest.restart_levels} of the destination list carry a restart rule "
                              f"(w:lvlRestart) that LibreOffice ignores: the render proof may number them differently "
                              f"from Word (the prediction follows Word)")
        if not tracked and found:
            frag.flags.append("UNTRACKED removal of typed numbers at the user's request: no revision marks; V2's list "
                              "and the visible-line check are the proof.")
        if older_r or older_p:
            frag.flags.append(f"{older_r} changed run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change from an earlier reviewer; reject-all would restore that older "
                              f"snapshot.")
        for e, idx in sorted(look_flags.items()):
            frag.flags.append(f"{len(idx)} list number(s) will differ in {FLAG_WORDS[e]} from the typed number they "
                              f"replace — a list number takes the paragraph mark's formatting (and the scheme style's): "
                              + "; ".join(f"‘{shown(texts[i], 40)}’" for i in idx[:6]))
        if caps_text:
            frag.flags.append(f"{len(caps_text)} paragraph(s) take a scheme style that shows text in capitals: their "
                              f"words will read in capitals (‘General’ → ‘GENERAL’), the letters stay as typed: "
                              + "; ".join(f"‘{shown(texts[i][ends[i]:], 40)}’" for i in caps_text[:6]))
        if dest.linked:
            frag.flags.append(f"your scheme's style(s) {dest.linked} do not name their level (w:ilvl) in their numbering; "
                              f"Word shows them right, LibreOffice's proof PDFs show them at level 0 — add the level "
                              f"to those styles in the example file to make the proofs agree")
        frag.touches = list(dict.fromkeys(frag.touches))           # one per target and aspect (merge cost)
        return frag

    @staticmethod
    def _skipped_hint(pars, texts, i: int, y, by_src, ends, dest) -> Optional[str]:
        """A converted paragraph whose level names a level no paragraph reached (an article "1.1" with no PART before
        it): the nearest earlier unconverted, unnumbered paragraph that looks like a typed number by the audit's generic
        grammar (``audit_numbering.typed_shape``) but matched no level of the scheme — and what the level needs."""
        lv = dest.scheme.level_defs.get(y.ilvl)
        named = [k for k in lists.named_levels(lv.text if lv is not None else None) if k < (y.ilvl or 0)]
        gap = min(named) if named else 0
        for j in range(i - 1, -1, -1):
            if j in by_src or j in ends or not lists.counted(pars[j]):
                continue
            shape = typed_shape(texts[j])
            if shape is not None:
                return (f"The nearest line before it that looks like a typed number, ‘{shown(texts[j], 50)}’ "
                        f"({shape.token!r}), matches no level of the scheme: level {gap} numbers like "
                        f"{level_sample(dest.scheme.level_defs, gap)!r}")
        return (f"No line before it is numbered at level {gap} (numbered like "
                f"{level_sample(dest.scheme.level_defs, gap)!r})")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td:
            return
        if td.get("template") and not td.get("import"):
            check_template_unchanged(td["template"], td["template_md5"])     # rule 12: the plan read it
        apply_import(pkg, td.get("import"))
        if not td["changed"]:
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = [td.get("first_rev_id", 0)]
        root = pkg.xml(td["part"])
        pars = paragraphs(root)
        groups = runs_by_paragraph(root, pars)
        # the paragraph properties (and, plain, the cleared run properties) first, on the source's runs; then the edits
        for bi, lvl, restyle, own, runs_, mark, ppr in td["changed"]:
            d = ParagraphClearing(runs=[(ri, list(n)) for ri, n in runs_], mark=mark, ppr=list(ppr))
            next_id[0] = clear_paragraph(pars[bi], groups[bi], d, stamp, next_id[0],
                                         ppr_edit=restyle_edit(td["styles"][lvl]) if (restyle or own) else None)
        apply_part_edits(root, td["edits"], td["tracked"], stamp, next_id, tabs=True)
        pkg.write_xml(td["part"], root)
