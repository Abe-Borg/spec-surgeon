"""Op: insert_field — a Word field (the page number, the page count, the section's number or page count, today's date,
the date the file was saved, the file name, the nearest text in a style, a document property) at the user's placeholder
text, or at the start or end of chosen paragraphs (docs/BUILTIN_OPS_BATCH6_DESIGN.md §9). The verifier derives all of it
again in verify/inserted_fields.py (kind ``inserted_fields``): keep the two identical.

* **Instruction**: ``" " + " ".join(tokens) + " "`` — the field's name; STYLEREF the style's ``w:name`` in double
  quotes (the style the user names by id, or by name ignoring case — a paragraph or character style), then ``\\l``
  (the last such text on the page) and ``\\n`` (its paragraph number) when asked; DOCPROPERTY the property's name in
  double quotes (Word's spelling of a built-in one, the custom property's own); FILENAME ``\\p`` with the path; a
  number format ``\\*`` and its word (``Arabic``, ``roman``, ``ROMAN``, ``alphabetic``, ``ALPHABETIC``); a date
  picture ``\\@`` and the picture in double quotes (only the letters d, M, y, H, h, m, s, AM/PM, am/pm, A/P, a/p and
  the separators space , . - / :). Nothing else: no free instruction text.
* **Markup**: a complex field as Word writes one, four runs of one element each — ``w:fldChar`` begin, ``w:instrText``
  (``xml:space="preserve"``), ``w:fldChar`` separate, ``w:fldChar`` end — with an EMPTY result: the op adds no
  character a reader sees (CLAUDE.md invariant 6). Word computes PAGE, NUMPAGES and SECTIONPAGES as it lays out the
  pages; every other field's begin carries ``w:dirty="true"`` (ECMA-376 §17.16.18: the result is stale and is to be
  recalculated when an application that can reads the document), so Word fills it in when it opens the file.
* **At the placeholder** (``at_text``): every occurrence, left to right and not overlapping, of the exact text in the
  run text (ops/textedit.py) of a selected paragraph that is not part of a longer word (``placeholder_pattern``: where
  the placeholder's first character is a word character, ``\\w``, the one before it is not; where its last is, the one
  after it is not — "X" is never taken out of "EXTERIOR", "{{PAGE}}" has no word edge) is an edit of the core whose
  new text is one character (``ONE``), which is then replaced by the field's runs (``place``): the placeholder's
  deletion, the field's place and its formatting are exactly the core's — tracked, the placeholder deleted and the
  field in a new ``w:ins`` after it (after someone's pending insertion when the placeholder lies in one); untracked,
  the placeholder gone and the field where its first character was, with that run's properties and attributes. The
  core's refusals hold (a placeholder over a tab, a picture or deleted text, in what a field shows, tracked across a
  hyperlink or a content control …), and a placeholder run carrying a pending tracked formatting change is refused
  (its copies would carry that revision twice).
* **At the start or end** (``position``) of the selected — targeted — paragraphs: the field's runs right after the
  paragraph's properties, or after its last child, with the properties of the run of the first (start) or last (end)
  character of the run text that is a ``w:t`` character, else the paragraph mark's (without its revision marks), never
  a ``w:rPrChange``; tracked, the four runs in one new ``w:ins``. Refused when that place lies inside a complex field
  (a table of contents: ``field_depths`` counts each paragraph's own begin and end characters, deleted ones too, in
  paragraph order over the part; an end never takes the depth below 0).
* Refused anywhere: a paragraph in a text box (``textbox``). Scope: the body, headers, footers, footnotes, endnotes
  (default: the body, headers and footers); a page or section number (PAGE, NUMPAGES, SECTIONPAGES, SECTION) put at a
  placeholder in the body is flagged — it belongs in a header or footer. Tracked by default (``tracked: false`` on
  request).
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..ooxml import (RevisionStamp, XML_SPACE, block_full_text, block_struct_counts, clone_run_shell,
                     file_uses_date_utc, max_revision_id, paragraphs, set_text)
from ..package import W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment, Touch
from .docprops import CustomProps
from .fields import fields_of
from .replace_text import default_author
from .selectors import StyleSheet, check_paragraph_selector, is_targeted, paragraph_selected, scope_parts
from .slots import field_runs
from .textedit import (Edit, RunText, accept_text_map, apply_tracked, apply_untracked, check_edits, nearest_p,
                       predict)

FIELDS = ("PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION", "DATE", "SAVEDATE", "FILENAME", "STYLEREF", "DOCPROPERTY")
COMPUTED_AT_LAYOUT = ("PAGE", "NUMPAGES", "SECTIONPAGES")
NUMBERED = ("PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION")
DATED = ("DATE", "SAVEDATE")
NUMBER_FORMATS = {"arabic": "Arabic", "roman": "roman", "ROMAN": "ROMAN", "alphabetic": "alphabetic",
                  "ALPHABETIC": "ALPHABETIC"}
DATE_PICTURE = re.compile(r"(?:AM/PM|am/pm|A/P|a/p|d{1,4}|M{1,4}|yyyy|yy|H{1,2}|h{1,2}|m{1,2}|s{1,2}|[ ,.\-/:])+")
BUILT_IN_PROPERTIES = ("Title", "Subject", "Author", "Keywords", "Comments", "Category", "Company", "Manager",
                       "LastSavedBy")
SCOPES = ("document", "headers", "footers", "footnotes", "endnotes")
DEFAULT_SCOPE = ["document", "headers", "footers"]
OWN_PARAMS = {"STYLEREF": ("style", "last_on_page", "paragraph_number"), "DOCPROPERTY": ("property",),
              "FILENAME": ("path",)}
ARGUMENTS = ("format", "style", "last_on_page", "paragraph_number", "property", "path")
PARAMS = set(ARGUMENTS) | {"field", "at_text", "position", "tracked", "author", "allow_missing", "date_convention"}
MARK_REVISIONS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "rPrChange")
ONE = "\uFFFC"      # the one character the core places where a field goes; ``place`` replaces it at once


def placeholder_pattern(at_text: str) -> "re.Pattern[str]":
    """The placeholder where it is not part of a longer word: a word character (``\\w``) at its start may not follow
    one, at its end may not be followed by one; an edge that is no word character ("{{", "]", "#") takes any
    neighbour."""
    return re.compile(("(?<!\\w)" if re.match(r"\w", at_text[0]) else "") + re.escape(at_text)
                      + ("(?!\\w)" if re.match(r"\w", at_text[-1]) else ""))


def _flag(params: Dict[str, Any], key: str, default: bool) -> bool:
    v = params.get(key, default)
    if not isinstance(v, bool):
        raise JobException(f"{key} must be true or false", "bad-params")
    return v


def read_request(params: Dict[str, Any]) -> Dict[str, Any]:
    """The request, checked strictly (``bad-params``): the field and only its own arguments, one placement."""
    unknown = set(params) - PARAMS
    if unknown:
        raise JobException(f"insert_field takes no {sorted(unknown)}", "bad-params")
    field = params.get("field")
    if field not in FIELDS:
        raise JobException(f"field must be one of {list(FIELDS)}, got {field!r}", "bad-params")
    own = set(OWN_PARAMS.get(field, ())) | ({"format"} if field in NUMBERED + DATED else set())
    stray = sorted(k for k in ARGUMENTS if k in params and k not in own)
    if stray:
        raise JobException(f"a {field} field takes no {stray}", "bad-params")
    if "format" in params:
        fmt = params["format"]
        if field in NUMBERED and (not isinstance(fmt, str) or fmt not in NUMBER_FORMATS):
            raise JobException(f"format must be one of {list(NUMBER_FORMATS)} for a {field} field, got {fmt!r}",
                               "bad-params")
        if field in DATED and (not isinstance(fmt, str) or not DATE_PICTURE.fullmatch(fmt)
                               or not re.search("[dMyHhms]", fmt)):
            raise JobException(f"format must be a date picture of d, M, y, H, h, m, s, AM/PM and the separators "
                               f"space , . - / : (e.g. \"MMMM d, yyyy\"), got {fmt!r}", "bad-params")
    needed = {"STYLEREF": "style", "DOCPROPERTY": "property"}.get(field)
    if needed and (not isinstance(params.get(needed), str) or not params[needed].strip()):
        raise JobException(f"a {field} field needs '{needed}'", "bad-params")
    for key in ("last_on_page", "paragraph_number", "path"):
        _flag(params, key, False)
    at_text, position = params.get("at_text"), params.get("position")
    if (at_text is None) == (position is None):
        raise JobException("give the placeholder text to replace ('at_text') or the place in the paragraph "
                           "('position': start or end) — one of them", "bad-params")
    if at_text is not None and (not isinstance(at_text, str) or not at_text.strip()
                                or any(ord(c) < 32 or c == "\x7f" for c in at_text)):
        raise JobException(f"at_text must be the exact placeholder text on one line, got {at_text!r}", "bad-params")
    if position is not None and position not in ("start", "end"):
        raise JobException(f"position must be 'start' or 'end', got {position!r}", "bad-params")
    if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
        raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
    if "author" in params and (not isinstance(params["author"], str) or not params["author"].strip()):
        raise JobException("author must be a name", "bad-params")
    return {"field": field, "at_text": at_text, "position": position, "tracked": _flag(params, "tracked", True),
            "allow_missing": _flag(params, "allow_missing", False)}


def style_name(sheet: StyleSheet, wanted: str) -> Tuple[str, str]:
    """(style id, the name STYLEREF quotes) of the style the user names by id, or by name ignoring case."""
    st = sheet.styles.get(wanted)
    if st is None:
        named = [s for s in sheet.styles.values() if s.find(W + "name") is not None
                 and (s.find(W + "name").get(W + "val") or "").casefold() == wanted.casefold()]
        if len(named) != 1:
            raise JobException(f"the style {wanted!r} is {'not in this file' if not named else 'the name of two styles'}"
                               f"; a STYLEREF field to it would show an error", "field-argument", {"style": wanted})
        st = named[0]
    if st.get(W + "type", "paragraph") not in ("paragraph", "character"):
        raise JobException(f"the style {wanted!r} is a {st.get(W + 'type')} style; STYLEREF finds paragraph and "
                           f"character styles", "field-argument", {"style": wanted})
    name = st.find(W + "name")
    value = name.get(W + "val") if name is not None else None
    if not value or '"' in value:
        raise JobException(f"the style {wanted!r} has no name a field can quote", "field-argument", {"style": wanted})
    return st.get(W + "styleId"), value


def property_name(pkg: DocxPackage, wanted: str) -> Tuple[str, bool]:
    """(the name DOCPROPERTY quotes, whether it is a custom property of the file)."""
    for name in BUILT_IN_PROPERTIES:
        if name.casefold() == wanted.casefold():
            return name, False
    try:
        prop = CustomProps(pkg).get(wanted)
    except JobException as e:
        raise JobException(f"the document property {wanted!r} cannot be read: {e.reason}", "field-argument",
                           {"property": wanted}) from e
    stored = prop.get("name") if prop is not None else None
    if not stored or '"' in stored:
        raise JobException(f"the document property {wanted!r} is not a property of this file (the built-in ones are "
                           f"{', '.join(BUILT_IN_PROPERTIES)}); a DOCPROPERTY field would show an error",
                           "field-argument", {"property": wanted})
    return stored, True


def instruction(pkg: DocxPackage, sheet: StyleSheet, params: Dict[str, Any]) -> Tuple[str, List[Touch]]:
    """The field's instruction text (module docstring) and what choosing its argument read."""
    field = params["field"]
    tokens, reads = [field], []
    if field == "STYLEREF":
        sid, name = style_name(sheet, params["style"])
        tokens.append(f'"{name}"')
        tokens += [s for k, s in (("last_on_page", "\\l"), ("paragraph_number", "\\n")) if params.get(k)]
        if sheet.part:
            reads.append((sheet.part, f"style:{sid}", "verify"))
    if field == "DOCPROPERTY":
        name, custom = property_name(pkg, params["property"])
        tokens.append(f'"{name}"')
        if custom:
            reads.append((CustomProps(pkg).member, "*", "verify"))
    if field == "FILENAME" and params.get("path"):
        tokens.append("\\p")
    if "format" in params:
        tokens += (["\\*", NUMBER_FORMATS[params["format"]]] if field in NUMBERED
                   else ["\\@", '"' + params["format"] + '"'])
    return " " + " ".join(tokens) + " ", reads


def markup(field: str, instr: str) -> List[Any]:
    """The field's four elements: begin (dirty unless Word computes it at layout), its code, separate, end."""
    begin = etree.Element(W + "fldChar")
    begin.set(W + "fldCharType", "begin")
    if field not in COMPUTED_AT_LAYOUT:
        begin.set(W + "dirty", "true")
    code = etree.Element(W + "instrText")
    code.set(XML_SPACE, "preserve")
    code.text = instr
    separate = etree.Element(W + "fldChar")
    separate.set(W + "fldCharType", "separate")
    end = etree.Element(W + "fldChar")
    end.set(W + "fldCharType", "end")
    return [begin, code, separate, end]


def field_depths(pars: Sequence[Any]) -> List[Tuple[int, int]]:
    """(complex-field depth at the start of each paragraph, at its end) — module docstring."""
    out, depth = [], 0
    for p in pars:
        start = depth
        for fc in p.iter(W + "fldChar"):
            if nearest_p(fc) is not p:
                continue
            kind = fc.get(W + "fldCharType")
            if kind == "begin":
                depth += 1
            elif kind == "end":
                depth = max(0, depth - 1)
        out.append((start, depth))
    return out


def edge_rpr(p, position: str) -> Optional[Any]:
    """The run properties a field at the paragraph's start or end takes (module docstring), or None."""
    rt = RunText(p)
    chars = [ps for ps in rt.positions if ps.offset >= 0]
    if chars:
        src, drop = (chars[0] if position == "start" else chars[-1]).run.find(W + "rPr"), (W + "rPrChange",)
    else:
        ppr = p.find(W + "pPr")
        src, drop = (ppr.find(W + "rPr") if ppr is not None else None), MARK_REVISIONS
    if src is None:
        return None
    rpr = copy.deepcopy(src)
    for c in list(rpr):
        if isinstance(c.tag, str) and c.tag in drop:
            rpr.remove(c)
    return rpr if (len(rpr) or rpr.attrib) else None


def _quoted(p, limit: int = 40) -> str:
    """The start of a paragraph's text for a flag: whitespace collapsed, a field or picture as ¤, cut."""
    text = " ".join(RunText(p).text.replace("\x00", "¤").split())
    return f"‘{text if len(text) <= limit else text[:limit - 1] + '…'}’"


def place(run, t, k: int, elements: Sequence[Any]) -> None:
    """The character at offset ``k`` of ``t`` (a ``w:t`` of ``run``) replaced by the field: the run split there, each of
    the field's elements in a copy of the run's attributes and properties; a run left empty goes."""
    text = t.text or ""
    head, tail = text[:k], text[k + 1:]
    kids = [c for c in run if not (isinstance(c.tag, str) and c.tag == W + "rPr")]
    following = kids[kids.index(t) + 1:]
    shells = [clone_run_shell(run) for _ in range(len(elements) + 1)]
    if head:
        set_text(t, head)
    else:
        run.remove(t)
    rest = shells[-1]
    if tail:
        set_text(etree.SubElement(rest, W + "t"), tail)
    for c in following:
        rest.append(c)
    anchor = run
    for shell, el in zip(shells, elements):
        shell.append(el)
        anchor.addnext(shell)
        anchor = shell
    if any(isinstance(c.tag, str) and c.tag != W + "rPr" for c in rest):
        anchor.addnext(rest)
    if all(not isinstance(c.tag, str) or c.tag == W + "rPr" for c in run):        # the core's "left empty"
        run.getparent().remove(run)


def place_at_edge(p, position: str, elements: Sequence[Any], rpr, ins=None) -> None:
    """The field's runs at the paragraph's start (after its properties) or end, in ``ins`` when tracked."""
    runs = []
    for el in elements:
        r = etree.Element(W + "r")
        if rpr is not None:
            r.append(copy.deepcopy(rpr))
        r.append(el)
        runs.append(r)
    nodes = runs
    if ins is not None:
        for r in runs:
            ins.append(r)
        nodes = [ins]
    if position == "end":
        for n in nodes:
            p.append(n)
        return
    at = 1 if len(p) and p[0].tag == W + "pPr" else 0
    for k, n in enumerate(nodes):
        p.insert(at + k, n)


FIELD_WORDS = {
    "PAGE": "a page number field", "NUMPAGES": "a field showing the number of pages",
    "SECTIONPAGES": "a field showing the number of pages in the section", "SECTION": "a section number field",
    "DATE": "a field showing today's date", "SAVEDATE": "a field showing the date the file was last saved",
    "FILENAME": "a file name field",
}
FORMAT_WORDS = {"arabic": "in ordinary numbers (1, 2, 3)", "roman": "in small roman numerals (i, ii, iii)",
                "ROMAN": "in capital roman numerals (I, II, III)", "alphabetic": "in small letters (a, b, c)",
                "ALPHABETIC": "in capital letters (A, B, C)"}


@register
class InsertField(Op):
    name = "insert_field"
    mode = "content"
    description = ("Insert a Word field — the page number (PAGE), the number of pages (NUMPAGES), the section's page "
                   "count (SECTIONPAGES) or number (SECTION), today's date (DATE), the date the file was saved "
                   "(SAVEDATE), the file name (FILENAME), the nearest text in a style (STYLEREF) or a document property "
                   "(DOCPROPERTY) — in place of the user's exact placeholder text ('at_text'), or at the start or end of "
                   "targeted paragraphs ('position'). The field is inserted empty and Word fills it in; tracked by "
                   "default.")
    params_schema = {
        "type": "object",
        "properties": {
            "field": {"type": "string", "enum": list(FIELDS)},
            "format": {"type": "string", "description": "PAGE/NUMPAGES/SECTIONPAGES/SECTION: arabic | roman | ROMAN | "
                                                        "alphabetic | ALPHABETIC; DATE/SAVEDATE: the user's date "
                                                        "picture, e.g. \"MMMM d, yyyy\" or \"M/d/yyyy\""},
            "style": {"type": "string", "description": "STYLEREF: the style (id or name) whose nearest text it shows"},
            "last_on_page": {"type": "boolean", "description": "STYLEREF: the last such text on the page (\\l)"},
            "paragraph_number": {"type": "boolean", "description": "STYLEREF: that paragraph's number (\\n)"},
            "property": {"type": "string", "description": "DOCPROPERTY: Title, Subject, Author, Keywords, Comments, "
                                                          "Category, Company, Manager, LastSavedBy, or a custom "
                                                          "property the file has"},
            "path": {"type": "boolean", "description": "FILENAME: with the folder (\\p)"},
            "at_text": {"type": "string", "description": "the user's exact placeholder text; every occurrence that is "
                                                         "not part of a longer word ('X' never inside 'EXTERIOR') is "
                                                         "removed and the field takes its place and formatting"},
            "position": {"type": "string", "enum": ["start", "end"],
                         "description": "instead of at_text: the start or end of each targeted paragraph"},
            "tracked": {"type": "boolean", "default": True},
            "author": {"type": "string", "description": "tracked-change author (default: SPEC_SURGEON_AUTHOR or OS user)"},
            "allow_missing": {"type": "boolean", "default": False,
                              "description": "a file with no placeholder (or no such paragraph) is left as it is"},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["field"],
    }
    select_schema = {
        "type": "object",
        "properties": {
            "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPES)},
                      "description": "default: document, headers, footers"},
            "paragraphs": {"type": ["string", "object"],
                           "description": "with at_text: optional restriction; with position: required, targeted"},
        },
    }
    verification_profile = {
        "V2": "every paragraph a field goes into: the placeholder out (untracked) or kept as deleted text (tracked), "
              "the field's instruction text in",
        "V3": "three field characters (begin, separate, end) per field, declared per paragraph",
        "V4": "tracked: +1 del per run the placeholder touches, +1 ins per field (+1 for a split pending insertion)",
        "V8": "tracked: the accepted view holds the instruction text where the placeholder was",
        "V10": "inserted_fields re-derives the instruction, the markup, every place and its formatting from the "
               "user's words and checks each paragraph item by item: the field's four runs, nothing else changed",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        req = read_request(params)
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"insert_field selects by scope and paragraphs; unknown {sorted(unknown)}", "bad-params")
        pred = select.get("paragraphs")
        check_paragraph_selector(pred)
        if req["position"] and not is_targeted(select):
            raise JobException("a field at the start or end of paragraphs needs a targeted 'paragraphs' selector "
                               "naming them", "bad-params")
        scope = select["scope"] if select.get("scope") is not None else list(DEFAULT_SCOPE)
        if isinstance(scope, str):
            scope = [scope]
        bad = [k for k in scope if k not in SCOPES] if isinstance(scope, list) else [scope]
        if bad or not scope:
            raise JobException(f"insert_field's scope is some of {list(SCOPES)}, got {scope!r}", "bad-params")
        parts = scope_parts(pkg, scope)
        sheet = StyleSheet(pkg)
        instr, reads = instruction(pkg, sheet, params)
        field, tracked, at_text, position = req["field"], req["tracked"], req["at_text"], req["position"]
        author = params.get("author") or default_author()
        sel = {"scope": scope, **({"paragraphs": pred} if pred is not None else {})}
        frag = self.fragment(sel, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        blocks: Dict[str, Dict[str, Any]] = {}
        expected: Dict[str, Dict[str, int]] = {}
        pattern = placeholder_pattern(at_text) if at_text is not None else None
        in_body: List[Any] = []             # body paragraphs a page or section number goes into at a placeholder
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives every place over this part
            root = pkg.xml(part)
            pars = paragraphs(root)
            in_field = field_runs(root)
            depths = field_depths(pars) if position else []
            fields = fields_of(root) if position else []
            for bi, p in enumerate(pars):
                if pred is not None and not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                where = f"{part} paragraph {bi}"
                if at_text is not None:
                    rt = RunText(p)
                    edits = [Edit(m.start(), m.end(), instr) for m in pattern.finditer(rt.text)]
                    if not edits:
                        continue
                if nearest_p(p) is not None:
                    raise JobException(f"{where} is in a text box (Word keeps two copies of it); insert the field "
                                       f"there in Word", "textbox", {"where": where})
                if at_text is not None:
                    edits = check_edits(rt, edits, tracked, in_field, where)
                    for e in edits:
                        if rt.positions[e.start].run.find(W + "rPr/" + W + "rPrChange") is not None:
                            raise JobException(f"{where}: {at_text!r} carries a pending tracked formatting change; "
                                               f"the field's runs would copy that revision — accept or reject it "
                                               f"first", "tracked-format", {"where": where})
                    before, after, accepted, delta = predict(rt, edits, tracked)
                    count = len(edits)
                    blocks.setdefault(part, {})[str(bi)] = [[e.start, e.end] for e in edits]
                    if field in NUMBERED and part == pkg.main_part():
                        in_body.append(p)
                else:
                    if depths[bi][0 if position == "start" else 1]:
                        raise JobException(f"{where}: its {position} lies inside a field that runs over several "
                                           f"paragraphs (a table of contents …); Word would replace the new field when "
                                           f"it updates that one", "field-result", {"where": where})
                    before = block_full_text(p)
                    after = instr + before if position == "start" else before + instr
                    acc = accept_text_map(p)[0]
                    accepted = (instr + acc if position == "start" else acc + instr) if tracked else None
                    delta = {"ins": 1} if tracked else {}
                    count = 1
                    blocks.setdefault(part, {})[str(bi)] = position
                    if any(f.paragraph() is p and f.instruction.split() == instr.split() for f in fields):
                        frag.flags.append(f"{where} already holds a {field} field like this one; a second one is added")
                struct = block_struct_counts(p)
                struct["fldChar"] = struct.get("fldChar", 0) + 3 * count
                frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after,
                                            accepted_view=accepted if tracked else None, struct_after=struct,
                                            note=f"{count} {field} field(s) {'tracked' if tracked else 'untracked'}"))
                for k, v in delta.items():
                    if v:
                        frag.rev_delta[k] = frag.rev_delta.get(k, 0) + v
                frag.touches.append((part, str(bi), "text"))
                expected.setdefault(part, {})[str(bi)] = count
            if part in blocks:
                frag.intended_parts.append(part)
                frag.touches.append((part, "fields", "add"))       # an op choosing among the part's fields reads them
        frag.touches += reads
        total = sum(n for per in expected.values() for n in per.values())
        # the user's own words (absent = the default): the verifier applies its own defaults and reading
        frag.property_assertions.append({"kind": "inserted_fields", "params": dict(params), "select": dict(select),
                                         "author": author, "expected": expected})
        what = f"{at_text!r}" if at_text is not None else f"the {position} of the selected paragraphs"
        if not total:
            if req["allow_missing"]:
                frag.notes.append(f"insert_field: no {what} in {scope}; no change.")
                return frag
            raise JobException(f"insert_field: no {what} in {scope}", "not-found", {"scope": scope})
        frag.geometry_reference = "none"
        frag.geometry_reason = "a new field shows a value the renderer computes (a page number, a date)"
        if tracked:
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.rev_ids_needed = frag.rev_delta.get("ins", 0) + frag.rev_delta.get("del", 0)
        else:
            frag.flags.append(f"UNTRACKED field insertion at the user's request: {total} {field} field(s).")
        if in_body:
            n = sum(expected[pkg.main_part()].values())
            frag.flags.append(f"{n} {field} field(s) go into the document body, in place of {prose.quote(at_text)} in "
                              f"{len(in_body)} paragraph(s): a page or section number belongs in a header or footer — "
                              f"check these are the placeholders meant, or limit the scope to the headers and footers: "
                              + "; ".join(_quoted(p) for p in in_body[:6]) + (" …" if len(in_body) > 6 else ""))
        frag.transform_data = {"blocks": blocks, "field": field, "instruction": instr, "tracked": tracked,
                               "author": author, "use_date_utc": file_uses_date_utc(pkg),
                               "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        frag.notes.append(f"insert_field: {total} {field} field(s) {'in place of ' if at_text is not None else 'at '}"
                          f"{what} ({'tracked' if tracked else 'untracked'}), instruction {instr!r}; inserted empty — "
                          + ("Word computes it as it lays out the pages"
                             if field in COMPUTED_AT_LAYOUT else "marked for Word to update when it opens the file"))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("blocks"):
            return
        tracked = td["tracked"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, blocks in td["blocks"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            for bi in sorted(blocks, key=int, reverse=True):
                p, how = pars[int(bi)], blocks[bi]
                if isinstance(how, str):                            # the paragraph's start or end
                    ins = None
                    if tracked:
                        ins = etree.Element(W + "ins")
                        stamp.apply(ins, next_id[0])
                        next_id[0] += 1
                    place_at_edge(p, how, markup(td["field"], td["instruction"]), edge_rpr(p, how), ins)
                    continue
                for a, b in sorted(how, reverse=True):              # a later place first: earlier positions stay
                    one = [Edit(a, b, ONE)]
                    if tracked:
                        apply_tracked(p, one, stamp, next_id)
                    else:
                        apply_untracked(p, one)
                    at = RunText(p).positions[a]
                    if at.offset < 0 or (at.child.text or "")[at.offset] != ONE:
                        raise JobException(f"{part} paragraph {bi}: the field's place moved between plan and apply",
                                           "internal")
                    place(at.run, at.child, at.offset, markup(td["field"], td["instruction"]))
            pkg.write_xml(part, root)

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        field = params.get("field")
        if field == "STYLEREF":
            style = params.get("style")
            what = ("a field showing the nearest text in " + (prose.style(style) if isinstance(style, str) and style
                                                              else "the named style"))
            if params.get("paragraph_number"):
                what = what.replace("the nearest text", "the paragraph number of the nearest text")
            if params.get("last_on_page"):
                what += " (the last one on the page)"
        elif field == "DOCPROPERTY":
            prop = params.get("property")
            what = "a field showing the document property " + (prose.quote(prop) if isinstance(prop, str) and prop
                                                               else "named")
        elif field == "FILENAME" and params.get("path"):
            what = "a field showing the file name with its folder"
        else:
            what = FIELD_WORDS.get(field, "a field")
        fmt = params.get("format")
        if field in NUMBERED and fmt in FORMAT_WORDS:
            what += " " + FORMAT_WORDS[fmt]
        elif field in DATED and isinstance(fmt, str) and fmt:
            what += " written as " + prose.quote(fmt)
        scope = select.get("scope") or DEFAULT_SCOPE
        where = prose.scope_words(scope) or "in the document body"
        pred = select.get("paragraphs")
        tracked = bool(params.get("tracked", True))
        tail = ("shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
                if tracked else "without tracked changes")
        at_text = params.get("at_text")
        whole = ""
        if isinstance(at_text, str) and at_text:
            limit = "" if pred in (None, "all", {}) else ", " + prose.where({"paragraphs": pred})
            first = f"Replace every {prose.quote(at_text)} {where}{limit}{',' if limit else ''} with {what}, {tail}."
            if re.match(r"\w", at_text[0]) or re.match(r"\w", at_text[-1]):
                whole = f"Only whole words match: {prose.quote(at_text)} inside a longer word is left alone."
        else:
            spot = "start" if params.get("position") == "start" else "end"
            subject = prose.where({"paragraphs": pred, "scope": scope})[3:]
            first = f"Add {what} at the {spot} of {subject if subject.startswith('every') else 'the ' + subject}, {tail}."
        if field in COMPUTED_AT_LAYOUT:
            second = "The field is inserted empty; Word fills in the number as it lays out the pages."
        else:
            second = "The field is inserted empty and marked for updating; Word fills it in when it opens the file."
        third = "Files with no such place are left unchanged." if params.get("allow_missing") else ""
        return prose.sentence(first, whole, second, third)
