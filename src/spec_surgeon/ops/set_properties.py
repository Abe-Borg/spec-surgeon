"""Op: set_properties — bold, italic, caps, small caps, strikethrough, double strikethrough, underline, text colour,
superscript/subscript, hidden text, highlight and character spacing on selected runs and their paragraph marks, or in
style definitions; keep-with-next, keep lines together, widow control and page break before on selected paragraphs, or
in paragraph styles. Design: docs/SET_PROPERTIES_DESIGN.md; the run properties of built-in batch 6 (vertAlign, vanish,
highlight, spacing, dstrike): docs/BUILTIN_OPS_BATCH6_DESIGN.md §3.

Formatting mode: zero text diffs — "all caps" is w:caps, a display property; the letters stay exactly as typed. Hidden
text (w:vanish) is a display property too: the characters stay in the file, Word hides them.

Single and double strikethrough exclude each other, as in Word's Font dialog: ``strike`` on asks ``dstrike`` off and
the reverse (``effective.run_elements``), unless the other is asked as well — both on is a bad parameter. The other
one is an asked element like any: on text it is written off only where it would otherwise be in effect (the "already"
rule below), in a style definition unless the style itself already says off.

Two target families, one per op call:

* **direct formatting** — ``select.scope`` / ``paragraphs`` / ``runs`` (set_font's grammar): ``run`` properties go on
  every selected run of the selected paragraphs and on their paragraph marks (``include_paragraph_marks``),
  ``paragraph`` properties on each selected paragraph's pPr. Hidden (``vanish`` on) goes on a mark only when, after the
  change, every content run of its paragraph (a run holding anything but its properties) is hidden — selected, or
  hidden already: Word joins a paragraph whose mark is hidden to the next one, so a paragraph that keeps visible text
  keeps its mark visible (flagged). Bold and italic are the pairs Word itself writes (w:b + w:bCs, w:i + w:iCs): a run
  marked complex-script (w:cs) draws its weight and slant from the Cs twin. Caps and small caps never touch a symbol
  run (set_font's definition: ``preserve_fonts``, w:sym) — case-mapping a dingbat changes the glyph; the other run
  properties do apply to it. A target that already shows the asked value — the effective value, resolved through its
  styles by ``effective.PropResolver`` — is left alone and not counted, so tracked mode records a change only where
  Word itself would have. An ambiguous toggle (specified at two style levels) counts as needing the change: direct
  formatting wins, so the result is certain either way.
* **style definitions** — ``select.styles: {"ids": [...]}`` (paragraph and character styles; ``paragraph`` properties on
  paragraph styles only; ``highlight`` on text only — Word does not show highlighting set in a style, ``bad-params``):
  the style's rPr/pPr in styles.xml, never tracked. The plan resolves every run, mark and paragraph of every text part
  whose style chain includes the style, after the change (``PropResolver`` with an override) and with its provenance:
  a value from the style itself is affected (styles based on it inherit the change — noted); a nearer style's own
  value is normal inheritance (noted, never refused); a disagreeing direct value refuses ``conflict-property``; a
  toggle another style level specifies too refuses ``toggle-ambiguous`` (Word XORs them — not modelled, as the
  pinnable resolvers decide today); caps/smallCaps reaching a symbol run from the style refuse ``conflict-property``.

Tracked or plain (decision §14.1): plain for whole-document selections, tracked — one w:rPrChange per changed run or
mark, one w:pPrChange per changed paragraph, the snapshot the properties before — when the selector names particular
paragraphs; ``tracked`` overrides. A target already carrying someone's rPrChange/pPrChange is refused when tracked (two
snapshots cannot be expressed) and changed-and-flagged when plain, as set_font and set_paragraph_props do.

Verified by V10's ``properties`` kind (verify/props.py), which re-derives all of the above on its own.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from ..package import W, DocxPackage
from ..ooxml import (PPR_ORDER, RPR_ORDER, STYLE_ORDER, RevisionStamp, block_full_text, file_uses_date_utc, get_or_add,
                     mark_rpr, max_revision_id, paragraph_ppr, paragraphs, property_change, run_rpr)
from . import prose, register
from .base import JobException, Op, PlanFragment
from .effective import (CASE_ELEMENTS, PARA_FLAGS, REFLOW_ELEMENTS, UNDERLINES, VERT_ALIGNS, PropResolver,
                        para_elements, run_elements)
from .highlight import COLORS as HIGHLIGHT_COLORS
from .replace_text import default_author
from .selectors import (DEFAULT_PRESERVE_FONTS, StyleSheet, chain_reads, check_paragraph_selector, check_run_selector,
                        in_revision, is_symbol_rpr, is_targeted, mark_in_revision, paragraph_selected, rpr_selected,
                        runs_by_paragraph, scope_parts, table_ordinals)
from .set_font import formatting_extras, preserved_fonts_extra, style_definitions, tracked_phrase, two_sentences
from .textedit import run_text

BOOL_RUN = ("b", "i", "caps", "smallCaps", "strike", "dstrike", "vanish")
VALUE_RUN = ("u", "color", "vertAlign", "highlight", "spacing")
HIGHLIGHTS = HIGHLIGHT_COLORS + ("none",)
MAX_SPACING_PT = 1584                  # Word's own limit for expanded and condensed character spacing
# the plan's words for each property (describe): on / off
RUN_WORDS = {"b": "bold", "i": "italic", "caps": "all capitals", "smallCaps": "small capitals", "strike": "strikethrough",
             "dstrike": "double strikethrough"}
UNDERLINE_WORDS = {"single": "a single underline", "double": "a double underline", "words": "a words-only underline",
                   "dotted": "a dotted underline", "dash": "a dashed underline", "wave": "a wavy underline"}
VERT_ALIGN_WORDS = {"superscript": "make the text superscript", "subscript": "make the text subscript",
                    "baseline": "put the text back on the baseline (no superscript or subscript)"}
# Word's highlighter colours as its menu names them
HIGHLIGHT_WORDS = {"darkBlue": "dark blue", "darkCyan": "dark cyan", "darkGreen": "dark green",
                   "darkMagenta": "dark magenta", "darkRed": "dark red", "darkYellow": "dark yellow",
                   "darkGray": "dark grey", "lightGray": "light grey"}
PARA_WORDS = {"keepNext": ("keep each paragraph on the same page as the next",
                           "stop keeping each paragraph on the same page as the next"),
              "keepLines": ("keep the lines of each paragraph on one page",
                            "allow the lines of each paragraph to split across pages"),
              "widowControl": ("turn on widow and orphan control", "turn off widow and orphan control"),
              "pageBreakBefore": ("start each paragraph on a new page", "stop starting each paragraph on a new page")}
HEX_COLOR = re.compile(r"[0-9A-Fa-f]{6}")
REFLOW_REASON = ("bold, italic and caps change glyph widths and the paragraph flags move paragraphs between pages; "
                 "text reflows, so the source render is no layout reference")
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": ["document", "headers", "footers", "footnotes",
                                                                       "endnotes", "comments"]}, "default": ["document"]}


def clean_params(params: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, bool]]:
    """(run properties, paragraph properties) as asked — user-facing names, validated, the colour upper-cased."""
    run, para = params.get("run"), params.get("paragraph")
    if run is None and para is None:
        raise JobException("nothing to set: give 'run' and/or 'paragraph' properties", "bad-params")
    run_out: Dict[str, Any] = {}
    if run is not None:
        if not isinstance(run, dict) or not run:
            raise JobException("'run' must be a non-empty object of run properties", "bad-params")
        for k, v in run.items():
            if k in BOOL_RUN:
                if not isinstance(v, bool):
                    raise JobException(f"run.{k} must be true or false", "bad-params")
                run_out[k] = v
            elif k == "u":
                if v not in UNDERLINES:
                    raise JobException(f"run.u must be one of {UNDERLINES}", "bad-params")
                run_out[k] = v
            elif k == "color":
                if not isinstance(v, str) or not (v == "auto" or HEX_COLOR.fullmatch(v)):
                    raise JobException("run.color must be 6 hex digits (RRGGBB) or 'auto'", "bad-params")
                run_out[k] = v if v == "auto" else v.upper()
            elif k == "vertAlign":
                if v not in VERT_ALIGNS:
                    raise JobException(f"run.vertAlign must be one of {VERT_ALIGNS}", "bad-params")
                run_out[k] = v
            elif k == "highlight":
                if v not in HIGHLIGHTS:
                    raise JobException(f"run.highlight must be one of Word's highlight colours {list(HIGHLIGHTS)} "
                                       f"('none' removes the highlight)", "bad-params")
                run_out[k] = v
            elif k == "spacing":
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not abs(v) <= MAX_SPACING_PT \
                        or abs(v * 20 - round(v * 20)) > 1e-6:
                    raise JobException(f"run.spacing is the character spacing in points (negative condenses, 0 is "
                                       f"normal), a multiple of 0.05 pt within ±{MAX_SPACING_PT} pt; got {v!r}",
                                       "bad-params")
                run_out[k] = v
            else:
                raise JobException(f"unknown run property {k!r}; known: {BOOL_RUN + VALUE_RUN}", "bad-params")
    if run_out.get("caps") and run_out.get("smallCaps"):
        raise JobException("run.caps and run.smallCaps are mutually exclusive — Word shows all caps and ignores small "
                           "caps when both are in effect; ask for one of them true (and the other false where it is in "
                           "effect)", "bad-params")
    if run_out.get("strike") and run_out.get("dstrike"):
        raise JobException("run.strike and run.dstrike are mutually exclusive — Word draws one strikethrough or the "
                           "other; asking one on turns the other off", "bad-params")
    para_out: Dict[str, bool] = {}
    if para is not None:
        if not isinstance(para, dict) or not para:
            raise JobException("'paragraph' must be a non-empty object of paragraph properties", "bad-params")
        for k, v in para.items():
            if k not in PARA_FLAGS:
                raise JobException(f"unknown paragraph property {k!r}; known: {PARA_FLAGS}", "bad-params")
            if not isinstance(v, bool):
                raise JobException(f"paragraph.{k} must be true or false", "bad-params")
            para_out[k] = v
    return run_out, para_out


def caps_conflict(asked: Dict[str, Any], effective) -> bool:
    """Word shows all caps and ignores small caps when both are in effect: True when the op asks caps or smallCaps on
    and, after the change, both would be in effect on this run or mark. ``effective(element)`` gives the value of an
    element the op does not ask (the source's for a direct target, the simulated one for a style target); an
    ambiguous toggle (None) counts as possibly in effect."""
    if not (asked.get("caps") is True or asked.get("smallCaps") is True):
        return False
    after = {e: (asked[e] if e in asked else effective(e)) for e in ("caps", "smallCaps")}
    return after["caps"] is not False and after["smallCaps"] is not False


CAPS_CONFLICT = ("w:caps and w:smallCaps would both be in effect — Word shows all caps and ignores small caps; set the "
                 "other one false in this op")


def shows_content(run) -> bool:
    """A content run: one holding anything but its properties (text, a tab, a break, a field character, a picture …)."""
    return any(isinstance(c.tag, str) and c.tag != W + "rPr" for c in run)


def stays_visible(resolver: PropResolver, p, left_out: List[Any]) -> bool:
    """Whether paragraph ``p`` still shows text once its selected runs are hidden: a content run the selection leaves
    out (``left_out``, their rPr) is not hidden already — an ambiguous toggle counts as shown. Word joins a paragraph
    whose mark is hidden to the next one, so such a paragraph keeps its mark visible."""
    return any(resolver.run_prop(rpr, p, "vanish")[0] is not True for rpr in left_out)


def _quoted(p, limit: int = 40) -> str:
    """The start of a paragraph's text for a flag: whitespace collapsed, a field or picture as ¤, cut."""
    text = " ".join(run_text(p).replace("\x00", "¤").split())
    return f"‘{text if len(text) <= limit else text[:limit - 1] + '…'}’"


def write_element(container, element: str, value, order: List[str]) -> None:
    """Set one property element (created in schema order when absent): toggles and flags on → no attributes, off →
    w:val 0; w:color → exactly w:val (theme attributes gone); w:u → w:val set, its other attributes kept; w:vertAlign,
    w:highlight and w:spacing (twentieths of a point) → exactly w:val."""
    el = get_or_add(container, element, order)
    if element == "u":
        el.set(W + "val", value)
        return
    el.attrib.clear()
    if element == "color":
        el.set(W + "val", value[0])
    elif element in ("vertAlign", "highlight", "spacing"):
        el.set(W + "val", str(value))
    elif not value:
        el.set(W + "val", "0")


def _write_rpr(rpr, elements: List[str], asked: Dict[str, Any], stamp: Optional[RevisionStamp], next_id: int) -> int:
    chg = None
    if stamp is not None:
        snapshot = [c for c in rpr if c.tag != W + "rPrChange"]
        chg = property_change("rPrChange", "rPr", snapshot, stamp, next_id)
        next_id += 1
    for e in elements:
        write_element(rpr, e, asked[e], RPR_ORDER)
    if chg is not None:
        rpr.append(chg)
    return next_id


def _describe(run_set: Dict[str, Any], para_set: Dict[str, bool]) -> str:
    parts = []
    if run_set:
        parts.append(", ".join(f"{k} {v}" for k, v in run_set.items()))
    if para_set:
        parts.append(", ".join(f"{k} {v}" for k, v in para_set.items()))
    return "; ".join(parts)


@register
class SetProperties(Op):
    name = "set_properties"
    mode = "formatting"
    description = ("Set bold, italic, caps, small caps, strikethrough, double strikethrough, underline, text colour, "
                   "superscript/subscript, hidden text, highlight and character spacing on selected text (runs and "
                   "their paragraph marks) or in style definitions, and keep-with-next, keep lines together, widow "
                   "control and page break before on selected paragraphs or paragraph styles. Adds no characters (caps "
                   "and hidden are display properties: the letters stay as typed); a target that already shows the "
                   "asked value is left alone.")
    params_schema = {"type": "object", "properties": {
        "run": {"type": "object",
                "description": "text properties: b, i, caps, smallCaps, strike, dstrike (double strikethrough), "
                               "vanish (hidden text; a paragraph mark only when all its paragraph's text is hidden) "
                               "(true = on, false = explicitly off), u "
                               "(single|double|words|dotted|dash|wave|none), color (RRGGBB hex or 'auto'), vertAlign "
                               "(superscript|subscript|baseline), highlight (Word's highlighter colour or 'none'; on "
                               "text only: Word shows no highlighting set in a style), "
                               "spacing (character spacing in points: positive expands, negative condenses, 0 is "
                               "normal). b and i are written as the pairs Word writes (w:b + w:bCs, w:i + w:iCs); "
                               "strike on turns dstrike off and the reverse, as Word does",
                "properties": {"b": {"type": "boolean"}, "i": {"type": "boolean"}, "caps": {"type": "boolean"},
                               "smallCaps": {"type": "boolean"}, "strike": {"type": "boolean"},
                               "dstrike": {"type": "boolean"}, "vanish": {"type": "boolean"},
                               "u": {"type": "string", "enum": list(UNDERLINES)},
                               "color": {"type": "string", "description": "6 hex digits (RRGGBB) or 'auto'; theme "
                                                                          "colour attributes are removed"},
                               "vertAlign": {"type": "string", "enum": list(VERT_ALIGNS)},
                               "highlight": {"type": "string", "enum": list(HIGHLIGHTS)},
                               "spacing": {"type": "number", "description": "points, a multiple of 0.05 (e.g. 0.5 "
                                                                            "expands, -0.5 condenses, 0 is normal)"}}},
        "paragraph": {"type": "object",
                      "description": "paragraph properties: keepNext, keepLines, widowControl, pageBreakBefore (true/false)",
                      "properties": {k: {"type": "boolean"} for k in PARA_FLAGS}},
        "skip_sym_runs": {"type": "boolean", "default": True,
                          "description": "a run holding a w:sym is a symbol run (caps/smallCaps never touch it)"},
        "preserve_fonts": {"type": "array", "items": {"type": "string"},
                           "description": "symbol/dingbat fonts whose runs never get caps/smallCaps (default: Symbol, "
                                          "Wingdings…); the other run properties do apply to them"},
        "in_revisions": {"type": "string", "enum": ["include", "exclude"], "default": "include",
                         "description": "runs inside pending w:ins/w:del: change them too, or leave them"},
        "include_paragraph_marks": {"type": "boolean", "default": True},
        "tracked": {"type": "boolean", "description": "w:rPrChange / w:pPrChange per change; default: plain for "
                                                      "whole-document selections, tracked when paragraphs are targeted; "
                                                      "never for style definitions"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {
        "scope": SCOPE_SCHEMA,
        "runs": {"description": "'all' or {has_direct_font, font_in, not_in_revisions, color_in}; color_in [colours] "
                                "chooses runs by their effective colour (direct or from a style): 6 hex digits, 'auto' "
                                "or a theme colour name — e.g. the editor's blue text"},
        "paragraphs": {"description": "'all' or {style_in, text_regex, in_table, is_empty, index_range, has_bold, "
                                      "is_all_caps, after_heading_regex, between_paragraphs}"},
        "styles": {"description": "{ids: [styleId, …]} — change these style definitions instead of formatting text "
                                  "directly; alone (no scope/runs/paragraphs); paragraph and character styles"}}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:rPrChange per run/mark changed, +1 w:pPrChange per paragraph",
        "V10": "properties: selection and the elements each target gets re-derived (the effective value through the "
               "styles), counts matched, each target's whole rPr/pPr = source + exactly the asked elements, the "
               "asked value shown by every selected target — and by everything that uses a changed style — in the "
               "output; residual identical",
        "V7": "none when bold/italic/caps, hidden, superscript/subscript, character spacing or a paragraph flag is set "
              "(the text reflows); source for colour, underline, highlight and strikethrough alone",
    }

    # ---- describe ------------------------------------------------------------------------------------------------

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        run = params.get("run") if isinstance(params.get("run"), dict) else {}
        para = params.get("paragraph") if isinstance(params.get("paragraph"), dict) else {}
        on: List[str] = []
        off: List[str] = []
        for k, v in run.items():
            if k in RUN_WORDS and isinstance(v, bool):
                replaces = k == "dstrike" and v and "strike" not in run      # Word turns single strikethrough off
                (on if v else off).append(RUN_WORDS[k] + (" (in place of any single strikethrough)" if replaces else ""))
            elif k == "u" and v is not None:
                if v == "none":
                    off.append("underlining")
                else:
                    on.append(UNDERLINE_WORDS.get(v, f"a {prose.quote(v)} underline"))
            elif k == "highlight" and v == "none":
                off.append("highlighting")
        clauses: List[str] = []
        if on:
            clauses.append("apply " + prose.join(on))
        if off:
            clauses.append("remove " + prose.join(off))
        color = run.get("color")
        if color == "auto":
            clauses.append("reset the text colour to automatic")
        elif isinstance(color, str) and color:
            clauses.append(f"colour the text {prose.colour(color)}")
        if run.get("vertAlign") in VERT_ALIGN_WORDS:
            clauses.append(VERT_ALIGN_WORDS[run["vertAlign"]])
        if isinstance(run.get("vanish"), bool):
            clauses.append("hide the text (hidden text: the characters stay in the file)" if run["vanish"]
                           else "show hidden text again")
        highlight = run.get("highlight")
        if isinstance(highlight, str) and highlight and highlight != "none":
            clauses.append(f"highlight the text {HIGHLIGHT_WORDS.get(highlight, highlight)}")
        spacing = run.get("spacing")
        if isinstance(spacing, (int, float)) and not isinstance(spacing, bool):
            clauses.append("set the character spacing to normal" if spacing == 0 else
                           f"{'expand' if spacing > 0 else 'condense'} the character spacing by "
                           f"{prose.points(abs(spacing))}")
        clauses += [PARA_WORDS[k][0 if v else 1] for k, v in para.items() if k in PARA_WORDS and isinstance(v, bool)]
        if not clauses:
            clauses.append("change the text and paragraph properties")
        styles = select.get("styles")
        if isinstance(styles, dict) and styles.get("ids"):   # style definitions: never tracked, no run filters
            where, tracked, extras = style_definitions(styles["ids"]), "", preserved_fonts_extra(params)
        else:
            where, tracked, extras = prose.where(select), tracked_phrase(select, params), formatting_extras(params)
        first = prose.sentence(prose.join(clauses), where + (f", {tracked}" if tracked else ""))
        return two_sentences(first, extras)

    # ---- plan ----------------------------------------------------------------------------------------------------

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        unknown = set(select) - {"scope", "runs", "paragraphs", "styles"}
        if unknown:
            raise JobException(f"set_properties: unknown selector key(s) {sorted(unknown)}", "bad-params")
        run_set, para_set = clean_params(params)
        preserve = list(params.get("preserve_fonts", DEFAULT_PRESERVE_FONTS))
        skip_sym = bool(params.get("skip_sym_runs", True))
        sheet = StyleSheet(pkg)
        if "styles" in select:
            if set(select) != {"styles"}:
                raise JobException("set_properties: 'styles' is a target of its own — give it alone, without scope, "
                                   "runs or paragraphs (direct formatting is a second op)", "bad-params")
            return self._plan_styles(pkg, sheet, select, params, run_set, para_set, preserve, skip_sym)
        return self._plan_direct(pkg, sheet, select, params, run_set, para_set, preserve, skip_sym)

    def _plan_direct(self, pkg, sheet, select, params, run_set, para_set, preserve, skip_sym) -> PlanFragment:
        runs_pred, para_pred = select.get("runs", "all"), select.get("paragraphs")
        check_run_selector(runs_pred)
        check_paragraph_selector(para_pred)
        scope = select.get("scope", ["document"])
        parts = scope_parts(pkg, scope)
        in_revs = params.get("in_revisions", "include")
        if in_revs not in ("include", "exclude"):
            raise JobException("in_revisions must be 'include' or 'exclude'", "bad-params")
        marks_too = bool(params.get("include_paragraph_marks", True))
        tracked = bool(params.get("tracked", is_targeted(select)))
        author = params.get("author") or default_author()
        run_asked, para_asked = run_elements(run_set), para_elements(para_set)
        resolver = PropResolver(sheet)
        frag = self.fragment(select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, Dict[str, list]] = {}
        # V10's post-condition reads every selected run, mark and paragraph in the OUTPUT, an "already as asked" one
        # included: another op changing its properties (clear_direct_formatting, set_font), its style or its style chain
        # (an import replacing a style, set_properties on a style, a table style remapped) is a plan-time conflict, never
        # a failure after apply (batch 4). The reads are soft: an in-paragraph text edit leaves them as they were.
        reads: set = set()
        n_runs = n_marks = n_paras = 0
        already = Counter()
        kept_shown: List[Any] = []        # paragraphs whose mark stays visible: their text is not all hidden
        older_r = older_p = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            tables = table_ordinals(root)
            t_runs: List[list] = []          # [block, run, [elements]]
            t_marks: List[list] = []         # [block, [elements]]
            t_paras: List[list] = []         # [block, [flags]]
            for bi, p in enumerate(pars):
                if not paragraph_selected(para_pred, p, bi, block_full_text(p), sheet):
                    continue
                chain_rprs: list = []
                if para_asked or (run_asked and (marks_too or groups[bi])):
                    # the effective values (before) and V10's post-condition (after) read this paragraph's style chain:
                    # another op restyling it (apply_style, map_styles) is a plan-time conflict, never a failure after apply
                    reads.add((part, str(bi), "verify-props:pStyle"))
                if run_asked:
                    left_out: list = []          # the rPr of each content run the selection leaves out
                    for ri, r in enumerate(groups[bi]):
                        rpr = r.find(W + "rPr")
                        revised = in_revision(r, p)
                        if (in_revs == "exclude" and revised) or not rpr_selected(runs_pred, rpr, revised, p, sheet):
                            if shows_content(r):
                                left_out.append(rpr)
                            continue
                        reads.update({(part, str(bi), "verify-props:rPr"), (part, str(bi), "verify-props:rStyle")})
                        chain_rprs.append(rpr)
                        sym = is_symbol_rpr(rpr, p, sheet, preserve) or (skip_sym and r.find(W + "sym") is not None)
                        gets = resolver.gets_run(rpr, p, run_asked, sym)
                        if not sym and caps_conflict(run_asked, lambda e, rpr=rpr, p=p: resolver.run_prop(rpr, p, e)[0]):
                            raise JobException(f"{part} paragraph {bi} run {ri}: {CAPS_CONFLICT}", "conflict-property",
                                               {"part": part, "paragraph": bi})
                        if gets:
                            t_runs.append([bi, ri, sorted(gets)])
                        else:
                            already["runs"] += 1
                    if marks_too:
                        mrpr = p.find(W + "pPr/" + W + "rPr")
                        revised = mark_in_revision(p)
                        if not (in_revs == "exclude" and revised) and rpr_selected(runs_pred, mrpr, revised, p, sheet):
                            reads.update({(part, str(bi), "verify-props:markRPr"),
                                          (part, str(bi), "verify-props:rStyle")})
                            chain_rprs.append(mrpr)
                            msym = is_symbol_rpr(mrpr, p, sheet, preserve)
                            gets = resolver.gets_run(mrpr, p, run_asked, msym)
                            if not msym and caps_conflict(run_asked, lambda e, mrpr=mrpr, p=p: resolver.run_prop(mrpr, p, e)[0]):
                                raise JobException(f"{part} paragraph {bi} paragraph mark: {CAPS_CONFLICT}",
                                                   "conflict-property", {"part": part, "paragraph": bi})
                            keep_shown = False
                            if gets.get("vanish") is True:
                                # a hidden mark joins the paragraph to the next one in Word: only a paragraph whose
                                # every content run is hidden after the change hides its mark (what the runs left out
                                # of the selection show is read)
                                if left_out:
                                    reads.update({(part, str(bi), "verify-props:rPr"),
                                                  (part, str(bi), "verify-props:rStyle")})
                                    chain_rprs += left_out
                                keep_shown = stays_visible(resolver, p, left_out)
                                if keep_shown:
                                    del gets["vanish"]
                                    kept_shown.append(p)
                            if gets:
                                if tracked and revised:
                                    raise JobException(
                                        f"{part}: a selected paragraph mark is itself a pending revision; a tracked "
                                        f"formatting change on it would copy that revision into the snapshot",
                                        "tracked-format", {"part": part, "paragraph": bi})
                                t_marks.append([bi, sorted(gets)])
                            elif not keep_shown:
                                already["marks"] += 1
                if (part, str(bi), "verify-props:pStyle") in reads:
                    reads.update(chain_reads(sheet, part, p, tables, chain_rprs, ("replace", "props")))
                if para_asked:
                    reads.add((part, str(bi), "verify-props:pPr"))
                    gets = resolver.gets_para(p, para_asked)
                    if gets:
                        t_paras.append([bi, sorted(gets)])
                    else:
                        already["paragraphs"] += 1
            for bi, ri, _ in t_runs:
                rpr = groups[bi][ri].find(W + "rPr")
                if rpr is not None and rpr.find(W + "rPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part}: a selected run already carries a pending tracked formatting "
                                           f"change (w:rPrChange); a second tracked change cannot be expressed",
                                           "tracked-format", {"part": part, "paragraph": bi})
                    older_r += 1
            for bi, _ in t_marks:
                mrpr = pars[bi].find(W + "pPr/" + W + "rPr")
                if mrpr is not None and mrpr.find(W + "rPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part}: a selected paragraph mark already carries a pending tracked "
                                           f"formatting change (w:rPrChange)", "tracked-format",
                                           {"part": part, "paragraph": bi})
                    older_r += 1
            for bi, _ in t_paras:
                ppr = pars[bi].find(W + "pPr")
                if ppr is not None and ppr.find(W + "pPrChange") is not None:
                    if tracked:
                        raise JobException(f"{part} paragraph {bi} already carries a pending tracked paragraph "
                                           f"change (w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                    older_p += 1
            if t_runs or t_marks or t_paras:
                targets[part] = {"runs": t_runs, "marks": t_marks, "paras": t_paras}
                frag.intended_parts.append(part)
                frag.touches += [(part, str(bi), "rPr") for bi in sorted({b for b, _, _ in t_runs})]
                frag.touches += [(part, str(bi), "markRPr") for bi, _ in t_marks]
                frag.touches += [(part, str(bi), "pPr") for bi, _ in t_paras]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the selection over this part
            n_runs += len(t_runs)
            n_marks += len(t_marks)
            n_paras += len(t_paras)
        frag.touches += sorted(reads)
        frag.property_assertions.append({
            "kind": "properties", "target": "direct", "scope": [scope] if isinstance(scope, str) else list(scope),
            "parts": parts, "select": {"runs": runs_pred, "paragraphs": para_pred},
            "set": {"run": run_set, "paragraph": para_set}, "preserve_fonts": preserve, "skip_sym_runs": skip_sym,
            "in_revisions": in_revs, "include_paragraph_marks": marks_too, "tracked": tracked, "author": author,
            "expected_runs": n_runs, "expected_marks": n_marks, "expected_paragraphs": n_paras,
            "expected_shown_marks": len(kept_shown)})
        frag.transform_data = {"target": "direct", "targets": targets, "set": {"run": run_set, "paragraph": para_set},
                               "tracked": tracked, "author": author}
        n = n_runs + n_marks + n_paras
        if tracked and n:
            if n_runs + n_marks:
                frag.rev_delta["rPrChange"] = n_runs + n_marks
            if n_paras:
                frag.rev_delta["pPrChange"] = n_paras
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        self._geometry(frag, run_asked, para_asked, bool(targets))
        frag.notes.append(f"{n_runs} run(s), {n_marks} paragraph mark(s) and {n_paras} paragraph(s) → "
                          f"{_describe(run_set, para_set)} across {len(targets)} part(s); "
                          f"{'tracked (w:rPrChange / w:pPrChange)' if tracked else 'plain'} — "
                          + ("explicit 'tracked'" if "tracked" in params else
                             "default for " + ("targeted" if tracked else "whole-document") + " formatting (§14.1)"))
        if already:
            frag.notes.append("already as asked, left alone: " + ", ".join(f"{v} {k}" for k, v in sorted(already.items())))
        if run_asked and set(run_asked) & CASE_ELEMENTS:
            frag.notes.append(f"caps/smallCaps never touch symbol runs (fonts {preserve}, w:sym runs): case-mapping a "
                              f"dingbat changes the glyph; the other run properties apply to them")
        if kept_shown:
            frag.flags.append(f"{len(kept_shown)} paragraph mark(s) left visible: the rest of the paragraph's text stays "
                              f"visible, and Word joins a paragraph whose mark is hidden to the next one (select the "
                              f"whole paragraph to hide it, mark and all): "
                              + "; ".join(_quoted(p) for p in kept_shown[:6]) + (" …" if len(kept_shown) > 6 else ""))
        if older_r or older_p:
            frag.flags.append(f"{older_r} changed run(s)/mark(s) and {older_p} paragraph(s) already carried a pending "
                              f"tracked formatting change (w:rPrChange / w:pPrChange) from an earlier reviewer; "
                              f"reject-all would restore that older snapshot.")
        return frag

    def _plan_styles(self, pkg, sheet, select, params, run_set, para_set, preserve, skip_sym) -> PlanFragment:
        sel = select["styles"]
        if not (isinstance(sel, dict) and set(sel) == {"ids"} and isinstance(sel["ids"], list) and sel["ids"]
                and all(isinstance(x, str) for x in sel["ids"])):
            raise JobException(f"styles selector must be {{'ids': [styleId, …]}}, got {sel!r}", "bad-params")
        if params.get("tracked"):
            raise JobException("style-definition changes are not tracked (Word does not track them either); omit "
                               "'tracked' or set it false", "bad-params")
        if "highlight" in run_set:
            raise JobException("Word does not show highlighting set in a style; highlight the text directly "
                               "(select.runs / paragraphs)", "bad-params")
        for k in ("in_revisions", "include_paragraph_marks"):
            if k in params:
                raise JobException(f"'{k}' applies to direct formatting, not to style definitions", "bad-params")
        if sheet.part is None:
            raise JobException("document has no styles part", "structure")
        ids = list(dict.fromkeys(sel["ids"]))
        missing = [i for i in ids if i not in sheet.styles]
        if missing:
            raise JobException(f"style id(s) {missing} not in this document", "not-found", {"missing": missing})
        types = {sid: sheet.styles[sid].get(W + "type", "paragraph") for sid in ids}
        bad = [sid for sid, t in types.items() if t not in ("paragraph", "character")]
        if bad:
            raise JobException(f"style(s) {bad} are not paragraph or character styles", "bad-params")
        if para_set:
            bad = [sid for sid, t in types.items() if t != "paragraph"]
            if bad:
                raise JobException(f"paragraph properties cannot go on character style(s) {bad}", "bad-params")
        run_asked, para_asked = run_elements(run_set), para_elements(para_set)
        changes: Dict[str, Dict[str, List[str]]] = {}
        for sid in ids:
            st = sheet.styles[sid]
            gets_r = PropResolver.style_gets(st, "rPr", run_asked)
            gets_p = PropResolver.style_gets(st, "pPr", para_asked) if types[sid] == "paragraph" else {}
            if gets_r or gets_p:
                changes[sid] = {"rPr": sorted(gets_r), "pPr": sorted(gets_p)}
        # certainty: resolve everything that uses a selected style, after the change, with its provenance
        override: Dict[Tuple[str, str], Any] = {}
        for sid in ids:
            override.update({(sid, e): v for e, v in run_asked.items()})
            if types[sid] == "paragraph":
                override.update({(sid, e): v for e, v in para_asked.items()})
        sim = PropResolver(sheet, override)
        affected, inherits, nearer, direct_ok = Counter(), Counter(), Counter(), Counter()
        refusals: List[Tuple[str, str]] = []

        def judge(val, prov, v, sid, e, sym, own_style, where):
            if prov == f"style:{sid}":
                if sym and e in CASE_ELEMENTS and v:
                    refusals.append(("conflict-property", f"{where}: w:{e} from style {sid} would case-map a symbol "
                                                          f"run (the glyph would change) — set it as direct formatting "
                                                          f"instead, which leaves symbol runs alone"))
                elif own_style is not None and own_style != sid:
                    inherits[(sid, own_style, e)] += 1
                else:
                    affected[(sid, e)] += 1
            elif prov == "ambiguous":
                refusals.append(("toggle-ambiguous", f"{where}: w:{e} would be specified at several style levels "
                                                     f"(Word XORs them — not modelled); set it as direct formatting "
                                                     f"instead"))
            elif prov == "direct":
                if val != v:
                    refusals.append(("conflict-property", f"{where}: direct formatting (w:{e} {val!r}) would keep "
                                                          f"its own value over style {sid}; set it as direct "
                                                          f"formatting on these paragraphs instead, or clear it in Word"))
                else:
                    direct_ok[(sid, e)] += 1
            else:
                nearer[(sid, e, prov)] += 1

        for part in pkg.text_parts():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, p in enumerate(pars):
                pstyle = sheet.paragraph_style_id(p)
                pchain = [st.get(W + "styleId") for st in sheet.lineage(pstyle)]
                holders = [(r.find(W + "rPr"), f"run {ri}", r.find(W + "sym") is not None)
                           for ri, r in enumerate(groups[bi])]
                holders.append((p.find(W + "pPr/" + W + "rPr"), "paragraph mark", False))
                for sid in ids:
                    for rpr, label, has_sym in holders:
                        if types[sid] == "paragraph":
                            uses, own = sid in pchain, pstyle
                        else:
                            rs = rpr.find(W + "rStyle") if rpr is not None else None
                            rsid = rs.get(W + "val") if rs is not None else sheet.defaults.get("character")
                            uses, own = sid in [st.get(W + "styleId") for st in sheet.lineage(rsid)], rsid
                        if not uses:
                            continue
                        sym = is_symbol_rpr(rpr, p, sheet, preserve) or (skip_sym and has_sym)
                        for e, v in run_asked.items():
                            val, prov = sim.run_prop(rpr, p, e)
                            judge(val, prov, v, sid, e, sym, own, f"{part} paragraph {bi} {label}")
                        if not sym and caps_conflict(run_asked, lambda e, rpr=rpr, p=p: sim.run_prop(rpr, p, e)[0]):
                            refusals.append(("conflict-property", f"{part} paragraph {bi} {label}: {CAPS_CONFLICT}"))
                    if types[sid] == "paragraph" and sid in pchain:
                        for e, v in para_asked.items():
                            val, prov = sim.para_prop(p, e)
                            judge(val, prov, v, sid, e, False, pstyle, f"{part} paragraph {bi}")
        if refusals:
            code, reason = refusals[0]
            raise JobException(f"style change not certain: {reason}" + (f" (+{len(refusals) - 1} more)"
                                                                        if len(refusals) > 1 else ""),
                               code, {"problems": [r for _, r in refusals[:20]], "styles": ids})
        frag = self.fragment(select, params)
        frag.property_assertions.append({
            "kind": "properties", "target": "styles", "styles_part": sheet.part, "select": {"ids": ids},
            "set": {"run": run_set, "paragraph": para_set}, "preserve_fonts": preserve, "skip_sym_runs": skip_sym,
            "expected_ids": list(changes), "tracked": False})
        frag.transform_data = {"target": "styles", "styles_part": sheet.part, "changes": changes,
                               "set": {"run": run_set, "paragraph": para_set}}
        if changes:
            frag.intended_parts = [sheet.part]
            for sid, ch in changes.items():
                if ch["rPr"]:
                    frag.touches.append((sheet.part, f"style:{sid}", "rPr"))
                if ch["pPr"]:
                    frag.touches.append((sheet.part, f"style:{sid}", "pPr"))
                # set_properties' own families: a direct set_properties reads them through its chains (verify:props) —
                # its "already as asked" would go stale; set_style_font's fonts beside it stay a plan (batch 4)
                frag.touches.append((sheet.part, f"style:{sid}", "props"))
        self._geometry(frag, run_asked, para_asked, bool(changes))
        frag.notes.append(f"style(s) {list(changes) or 'none'} → {_describe(run_set, para_set)} (definitions only; "
                          f"not tracked — Word does not track style changes)"
                          + (f"; already as asked: {[s for s in ids if s not in changes]}"
                             if any(s not in changes for s in ids) else ""))
        for sid in ids:
            n = sum(c for (s, _), c in affected.items() if s == sid)
            frag.notes.append(f"{sid}: {n} run(s)/mark(s)/paragraph(s) take the value from the style")
        for (sid, own, e), c in sorted(inherits.items()):
            frag.notes.append(f"{own} (based on {sid}) inherits the change to {e}: {c} run(s)/mark(s)/paragraph(s)")
        for (sid, e, prov), c in sorted(nearer.items()):
            frag.notes.append(f"{prov[6:] if prov.startswith('style:') else prov} keeps its own {e} over {sid}: "
                              f"{c} run(s)/mark(s)/paragraph(s) do not change")
        for (sid, e), c in sorted(direct_ok.items()):
            frag.notes.append(f"{c} run(s)/mark(s)/paragraph(s) already show {e} as asked by direct formatting")
        if pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    @staticmethod
    def _geometry(frag: PlanFragment, run_asked: Dict[str, Any], para_asked: Dict[str, bool], changes: bool) -> None:
        if changes and (set(run_asked) & REFLOW_ELEMENTS or para_asked):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON

    # ---- apply ---------------------------------------------------------------------------------------------------

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        run_asked, para_asked = run_elements(td["set"]["run"]), para_elements(td["set"]["paragraph"])
        if td["target"] == "styles":
            if not td["changes"]:
                return
            root = pkg.xml(td["styles_part"])
            by_id = {st.get(W + "styleId"): st for st in root.findall(W + "style")}
            for sid, ch in td["changes"].items():
                if ch["rPr"]:
                    rpr = get_or_add(by_id[sid], "rPr", STYLE_ORDER)
                    for e in ch["rPr"]:
                        write_element(rpr, e, run_asked[e], RPR_ORDER)
                if ch["pPr"]:
                    ppr = get_or_add(by_id[sid], "pPr", STYLE_ORDER)
                    for e in ch["pPr"]:
                        write_element(ppr, e, para_asked[e], PPR_ORDER)
            pkg.write_xml(td["styles_part"], root)
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, t in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, ri, elements in t["runs"]:
                next_id = _write_rpr(run_rpr(groups[bi][ri]), elements, run_asked, stamp, next_id)
            for bi, elements in t["marks"]:
                next_id = _write_rpr(mark_rpr(pars[bi]), elements, run_asked, stamp, next_id)
            for bi, flags in t["paras"]:
                ppr = paragraph_ppr(pars[bi])
                chg = None
                if stamp is not None:
                    snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
                    chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
                    next_id += 1
                for e in flags:
                    write_element(ppr, e, para_asked[e], PPR_ORDER)
                if chg is not None:
                    ppr.append(chg)
            pkg.write_xml(part, root)
