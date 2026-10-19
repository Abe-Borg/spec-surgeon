"""Op: set_language — the proofing language of all text (built-in batch 4, item 29 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §4).

Formatting mode: zero text diffs. Never tracked (Word does not track a language change either; ``tracked: true`` is
``bad-params``). No selector: all text.

Parameters: ``language`` → ``w:lang/@w:val``, ``east_asian`` → ``@w:eastAsia``, ``complex_script`` → ``@w:bidi`` — at
least one; each a tag ``^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$``. ``replace_only``: a list of tags — only values in it
change (compared casefolded).

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's own
reading is verify/language.py):

* **Levels** (``language_levels``): docDefaults' ``rPrDefault/rPr``, every style's own ``w:rPr`` (any type; a style
  without a ``w:styleId`` cannot be referenced and is left alone), every run's and paragraph mark's own ``w:rPr`` in
  every text part (document, headers, footers, footnotes, endnotes, comments). Never: ``w:rPrChange`` snapshots,
  numbering levels, ``w:sdtPr``, ``w:tblStylePr``.
* **What a level gets** (``level_gets``), per asked attribute A = V: a level gets A when its own ``w:lang`` carries A
  with a value ≠ V (exactly; and, with ``replace_only``, a listed value). docDefaults without ``replace_only`` gets A
  also when its value is absent — ``w:docDefaults``, ``w:rPrDefault``, ``w:rPr``, ``w:lang`` created in schema order as
  needed (inside styles.xml — never a new part; a document without a styles part refuses, ``structure``). A level
  without an own ``w:lang`` is left alone (it inherits). The other attributes of a ``w:lang`` stay
  (``write_lang``).

The effective language (direct → character style chain → paragraph style chain → table style chain → docDefaults, the
first whose ``w:lang`` carries the attribute) is V10's post-condition and exists only on the verifier side
(verify/language.py), as the effective font does.

Touches: ``(part, block, "rPr" | "markRPr")`` per changed run's / mark's block, ``(styles, "style:<id>", "rPr")``,
``(styles, "docDefaults", "rPr")``, ``(part, "scope", "verify")`` for every text part and ``(styles, "*", "verify")``
(the post-condition reads every style chain: a style import or a style's font change beside it is a conflict), and per
text part the whole-member soft reads ``(part, "*", "verify-props:pStyle" | "verify-props:rStyle" |
"verify-props:tblPr")`` — the post-condition resolves every run through its paragraph's, its own and its table's style
in the OUTPUT, so a restyle anywhere in the part (apply_style, map_styles) is a plan-time conflict, while an edit of the
words (a soft read is not reached by ``text``) still plans.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import RPR_ORDER, get_or_add, paragraphs
from . import prose, register
from .base import VERIFY_PROPS, JobException, Op, PlanFragment
from .docprops import settings_member
from .selectors import StyleSheet, runs_by_paragraph

LANG_PARAMS = (("language", "val"), ("east_asian", "eastAsia"), ("complex_script", "bidi"))
TAG = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*")
FALSY = ("0", "false", "off")
REFLOW_REASON = ("the language decides hyphenation (automatic hyphenation is on) or which East Asian / complex-script "
                 "font Word uses; text may reflow, so the source render is no layout reference")


def _tag(v: Any, what: str) -> str:
    if not isinstance(v, str) or not TAG.fullmatch(v):
        raise JobException(f"{what} must be a language tag like en-US (2–3 letters, then '-' parts of 1–8 letters or "
                           f"digits), got {v!r}", "bad-params")
    return v


def clean_params(params: Dict[str, Any]) -> Tuple[Dict[str, str], Optional[Set[str]]]:
    """(asked w:lang attribute → value, the replace_only tags casefolded or None) — validated (``bad-params``)."""
    unknown = set(params) - {k for k, _ in LANG_PARAMS} - {"replace_only", "tracked"}
    if unknown:
        raise JobException(f"set_language: unknown parameter(s) {sorted(unknown)}", "bad-params")
    if params.get("tracked"):
        raise JobException("a language change is not tracked (Word does not track it either); omit 'tracked' or set it "
                           "false", "bad-params")
    if "tracked" in params and not isinstance(params["tracked"], bool):
        raise JobException("'tracked' must be false", "bad-params")
    asked = {attr: _tag(params[k], k) for k, attr in LANG_PARAMS if params.get(k) is not None}
    if not asked:
        raise JobException("nothing to set: give 'language' and/or 'east_asian' / 'complex_script'", "bad-params")
    listed = None
    if "replace_only" in params:
        ro = params["replace_only"]
        if not isinstance(ro, list) or not ro:
            raise JobException("replace_only must be a non-empty list of language tags", "bad-params")
        listed = {_tag(v, "replace_only").casefold() for v in ro}
    return asked, listed


def level_gets(rpr, asked: Dict[str, str], listed: Optional[Set[str]], doc_defaults: bool = False) -> Dict[str, str]:
    """The asked attributes this level gets: its own w:lang carries the attribute with another value (listed, with
    replace_only); docDefaults without replace_only also when the value is absent."""
    lang = rpr.find(W + "lang") if rpr is not None else None
    out: Dict[str, str] = {}
    for attr, value in asked.items():
        cur = lang.get(W + attr) if lang is not None else None
        if cur is None:
            if doc_defaults and listed is None:
                out[attr] = value
            continue
        if cur == value or (listed is not None and cur.casefold() not in listed):
            continue
        out[attr] = value
    return out


def write_lang(rpr, gets: Dict[str, str]) -> None:
    """Set the attributes on the rPr's w:lang (created in schema order when absent); its other attributes stay."""
    lang = get_or_add(rpr, "lang", RPR_ORDER)
    for attr, value in gets.items():
        lang.set(W + attr, value)


def doc_defaults_rpr(styles_root, create: bool = False):
    """docDefaults' rPrDefault/rPr; with ``create``, w:docDefaults (first child of w:styles), w:rPrDefault (before
    w:pPrDefault) and w:rPr are created as needed."""
    dd = styles_root.find(W + "docDefaults")
    if dd is None:
        if not create:
            return None
        dd = etree.Element(W + "docDefaults")
        first = next((i for i, c in enumerate(styles_root) if isinstance(c.tag, str)), len(styles_root))
        styles_root.insert(first, dd)
    rpd = dd.find(W + "rPrDefault")
    if rpd is None:
        if not create:
            return None
        rpd = etree.Element(W + "rPrDefault")
        dd.insert(0, rpd)
    rpr = rpd.find(W + "rPr")
    if rpr is None and create:
        rpr = etree.SubElement(rpd, W + "rPr")
    return rpr


def language_levels(pkg: DocxPackage, sheet: StyleSheet):
    """Every level, in a stable order: ("docDefaults", rpr), ("style", id, rpr), ("run", part, block, run, rpr),
    ("mark", part, block, rpr) — rpr None where the level has none."""
    out: List[tuple] = [("docDefaults", doc_defaults_rpr(sheet.root))]
    for sid, st in sheet.styles.items():
        out.append(("style", sid, st.find(W + "rPr")))
    for part in pkg.text_parts():
        root = pkg.xml(part)
        pars = paragraphs(root)
        groups = runs_by_paragraph(root, pars)
        for bi, p in enumerate(pars):
            for ri, r in enumerate(groups[bi]):
                out.append(("run", part, bi, ri, r.find(W + "rPr")))
            out.append(("mark", part, bi, p.find(W + "pPr/" + W + "rPr")))
    return out


def auto_hyphenation(pkg: DocxPackage) -> bool:
    member = settings_member(pkg)
    if member is None:
        return False
    el = pkg.xml(member).find(W + "autoHyphenation")
    return el is not None and el.get(W + "val") not in FALSY


LANG_WORDS = {"language": "Latin text", "east_asian": "East Asian text", "complex_script": "complex-script text"}


@register
class SetLanguage(Op):
    name = "set_language"
    mode = "formatting"
    description = ("Set the proofing language of all text (w:lang): every style, run and paragraph mark that names a "
                   "language, plus the document default; text that names none inherits it. replace_only changes only "
                   "the listed languages. Never tracked; adds no characters.")
    params_schema = {"type": "object", "properties": {
        "language": {"type": "string", "description": "language tag for Latin text (w:lang w:val), e.g. en-US"},
        "east_asian": {"type": "string", "description": "language tag for East Asian text (w:lang w:eastAsia)"},
        "complex_script": {"type": "string", "description": "language tag for complex-script text (w:lang w:bidi)"},
        "replace_only": {"type": "array", "items": {"type": "string"},
                         "description": "only these languages change (e.g. ['en-GB']); text in other languages stays"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema = {"type": "object", "description": "none: all text"}
    verification_profile = {
        "V1": "the text parts and styles.xml whose levels change", "V2": "zero text diffs",
        "V10": "language: every level's change re-derived from the source and matched; each changed w:lang = the "
               "source's with exactly the asked attributes, nothing else in its rPr; every run and mark of every text "
               "part resolves to the asked language in the output (replace_only: none to a listed one); residual "
               "identical",
        "V7": "source; none when east_asian/complex_script is asked or automatic hyphenation is on"}


    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        asked = [(k, params[k].strip()) for k, _ in LANG_PARAMS if isinstance(params.get(k), str) and params[k].strip()]
        if not asked:
            what = "of all text"
        elif len(asked) == 1 and asked[0][0] == "language":
            what = f"of all text to {prose.quote(asked[0][1])}"
        elif len(asked) == 1:
            what = f"of {LANG_WORDS[asked[0][0]]} to {prose.quote(asked[0][1])}"
        else:
            what = "of all text to " + prose.join(f"{prose.quote(v)} for {LANG_WORDS[k]}" for k, v in asked)
        listed = params.get("replace_only")
        if isinstance(listed, list) and listed:
            first = prose.sentence("set the proofing language", what + ", but only where it is now "
                                   + prose.join((prose.quote(t) for t in listed), "or")
                                   + ", leaving text in any other language as it is")
        else:
            first = prose.sentence("set the proofing language", what + ": every style, run and paragraph mark that "
                                   "names a language changes, and so does the document default, so text that names "
                                   "none inherits it too")
        return first + " " + prose.sentence("spelling and grammar checks then follow that language; nothing is shown "
                                            "as a tracked change and no words change")

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("set_language takes no selector: it sets the language of all text", "bad-params")
        asked, listed = clean_params(params)
        sheet = StyleSheet(pkg)
        if sheet.part is None:
            raise JobException("this file has no styles part (word/styles.xml): the document default language has "
                               "nowhere to go, and no part is ever created", "structure")
        frag = self.fragment(select, params)
        doc_defaults: Dict[str, str] = {}
        styles: Dict[str, Dict[str, str]] = {}
        parts: Dict[str, Dict[str, list]] = {}
        for level in language_levels(pkg, sheet):
            kind, rpr = level[0], level[-1]
            gets = level_gets(rpr, asked, listed, doc_defaults=(kind == "docDefaults"))
            if not gets:
                continue
            if kind == "docDefaults":
                doc_defaults = gets
            elif kind == "style":
                styles[level[1]] = gets
            elif kind == "run":
                parts.setdefault(level[1], {"runs": [], "marks": []})["runs"].append([level[2], level[3], gets])
            else:
                parts.setdefault(level[1], {"runs": [], "marks": []})["marks"].append([level[2], gets])
        main_parts = [p for p in pkg.text_parts() if p in parts]
        for part in main_parts:
            frag.intended_parts.append(part)
            frag.touches += [(part, str(bi), "rPr") for bi in sorted({b for b, _, _ in parts[part]["runs"]})]
            frag.touches += [(part, str(bi), "markRPr") for bi, _ in parts[part]["marks"]]
        if doc_defaults or styles:
            frag.intended_parts.append(sheet.part)
        if doc_defaults:
            frag.touches.append((sheet.part, "docDefaults", "rPr"))
        frag.touches += [(sheet.part, f"style:{sid}", "rPr") for sid in styles]
        for part in pkg.text_parts():
            frag.touches.append((part, "scope", "verify"))     # V10's post-condition reads every run of every text part
            # … through each paragraph's pStyle, each run's and mark's rStyle and each table's tblStyle in the OUTPUT: a
            # restyle anywhere in the part (apply_style, map_styles) is a plan-time conflict; an edit of the words is not
            frag.touches += [(part, "*", f"{VERIFY_PROPS}:{family}") for family in ("pStyle", "rStyle", "tblPr")]
        frag.touches.append((sheet.part, "*", "verify"))       # … and every style definition and docDefaults
        n_runs = sum(len(v["runs"]) for v in parts.values())
        n_marks = sum(len(v["marks"]) for v in parts.values())
        frag.property_assertions.append({
            "kind": "language", "styles_part": sheet.part,
            "params": {k: params[k] for k in ("language", "east_asian", "complex_script", "replace_only", "tracked")
                       if k in params},
            "expected": {"doc_defaults": doc_defaults, "styles": styles, "parts": parts},
            "expected_runs": n_runs, "expected_marks": n_marks})
        frag.transform_data = {"styles_part": sheet.part, "doc_defaults": doc_defaults, "styles": styles, "parts": parts}
        changes = bool(doc_defaults or styles or parts)
        if changes and (set(asked) & {"eastAsia", "bidi"} or auto_hyphenation(pkg)):
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON
        what = ", ".join(f"{k} {asked[a]}" for k, a in LANG_PARAMS if a in asked)
        frag.notes.append(f"language → {what}" + (f" where it is now one of {sorted(params['replace_only'])}"
                                                  if listed is not None else "")
                          + f": {n_runs} run(s), {n_marks} paragraph mark(s), {len(styles)} style(s)"
                          + (" and the document default" if doc_defaults else "") + " (not tracked)")
        if not changes:
            frag.notes.append("nothing to change: every level that names a language already has the asked one"
                              + ("" if listed is not None else " and so does the document default"))
        if doc_defaults and doc_defaults_rpr(sheet.root) is None:
            frag.notes.append("docDefaults/rPrDefault/rPr did not exist; created in schema order inside styles.xml")
        if pkg.has("word/stylesWithEffects.xml") and styles:
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td["doc_defaults"] or td["styles"]:
            root = pkg.xml(td["styles_part"])
            if td["doc_defaults"]:
                write_lang(doc_defaults_rpr(root, create=True), td["doc_defaults"])
            by_id = {}
            for st in root.findall(W + "style"):
                if st.get(W + "styleId") is not None:
                    by_id[st.get(W + "styleId")] = st              # the last of a repeated id, as StyleSheet keeps it
            for sid, gets in td["styles"].items():
                write_lang(by_id[sid].find(W + "rPr"), gets)
            pkg.write_xml(td["styles_part"], root)
        for part, t in td["parts"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, ri, gets in t["runs"]:
                write_lang(groups[bi][ri].find(W + "rPr"), gets)
            for bi, gets in t["marks"]:
                write_lang(pars[bi].find(W + "pPr/" + W + "rPr"), gets)
            pkg.write_xml(part, root)
