"""V10 kind ``inserted_fields`` — built-in batch 6, verifier side (docs/BUILTIN_OPS_BATCH6_DESIGN.md §9), independent of
ops/insert_field.py (rule 1): this module's own reading of the request, of the field's instruction and markup, of where
each field goes and with what formatting, and its own expected paragraph, item by item (verify/edits.py).

* **The request** (the user's words, this module's own defaults): ``field`` one of PAGE, NUMPAGES, SECTIONPAGES,
  SECTION, DATE, SAVEDATE, FILENAME, STYLEREF, DOCPROPERTY; ``format`` a number format (``arabic``, ``roman``,
  ``ROMAN``, ``alphabetic``, ``ALPHABETIC``) for the four numbering fields or a date picture for DATE/SAVEDATE (only
  the letters d, M, y, H, h, m, s, AM/PM, am/pm, A/P, a/p and the separators space , . - / :); ``style``
  (STYLEREF) a paragraph or character style the file has, by id or by name ignoring case, with ``last_on_page`` and
  ``paragraph_number``; ``property`` (DOCPROPERTY) a built-in property by Word's name or a custom property the file
  has, ignoring case; ``path`` (FILENAME); exactly one of ``at_text`` and ``position`` (``start`` | ``end``, which
  needs a targeted paragraph selector); scope (default document, headers and footers); ``tracked`` (default true).
* **Instruction**: ``" " + " ".join(tokens) + " "`` — the field's name; STYLEREF the style's ``w:name`` in double
  quotes, then ``\\l``, then ``\\n`` as asked; DOCPROPERTY the property's name in double quotes (Word's spelling of a
  built-in one, the custom property's own); FILENAME ``\\p`` with the path; a number format ``\\*`` and its word
  (``Arabic``, ``roman``, ``ROMAN``, ``alphabetic``, ``ALPHABETIC``); a date picture ``\\@`` and the picture in double
  quotes.
* **Markup**: four consecutive runs of one parent, each holding one element after its properties: ``w:fldChar`` begin
  (with ``w:dirty="true"`` unless the field is PAGE, NUMPAGES or SECTIONPAGES, which Word computes as it lays out the
  pages), ``w:instrText`` (``xml:space="preserve"``), ``w:fldChar`` separate, ``w:fldChar`` end — an empty result;
  tracked, the four are the whole of one NEW ``w:ins``.
* **At the placeholder**: every occurrence, left to right and not overlapping, of ``at_text`` in the run text of a
  selected paragraph is an edit of the core (verify/edits.py: its rules, refused otherwise) whose new text is one
  character; the expected paragraph is the core's expected items with that character's item replaced by the four field
  items in its context — tracked: after the placeholder's deletion, in a NEW ``w:ins`` (after someone's pending
  insertion when the placeholder lies in one); untracked: where its first character was, with that run's properties
  and attributes. The run that character is in may not carry ``w:rPrChange``.
* **At the start or end** of a selected paragraph: the four items before (start) or after (end) every other item, with
  the properties of the run of the first (start) or last (end) character of the run text that is a ``w:t`` character —
  the paragraph mark's, without its revision marks, when there is none — without ``w:rPrChange``, no run attributes.
  Refused when that place lies inside a complex field (begin/end counted over the part in paragraph order, each
  paragraph's own field characters, deleted ones too).
* Refused anywhere: a paragraph in a text box. Every paragraph a field went into is registered in ``verified_blocks``.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from lxml import etree

from .edits import Item, compare_items, expected_items, part_revision_ids, v_check_edits, v_items, v_run_text
from .props import Ctx, Styles, para_matches, sig
from .reader import VPackage, W
from .template import _v_field_runs

_V_FIELDS = ("PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION", "DATE", "SAVEDATE", "FILENAME", "STYLEREF", "DOCPROPERTY")
_V_AT_LAYOUT = {"PAGE", "NUMPAGES", "SECTIONPAGES"}
_V_NUMBERED = {"PAGE", "NUMPAGES", "SECTIONPAGES", "SECTION"}
_V_DATED = {"DATE", "SAVEDATE"}
_V_NUMBER_WORD = {"arabic": "Arabic", "roman": "roman", "ROMAN": "ROMAN", "alphabetic": "alphabetic",
                  "ALPHABETIC": "ALPHABETIC"}
_V_PICTURE = re.compile(r"(?:AM/PM|am/pm|A/P|a/p|d{1,4}|M{1,4}|yyyy|yy|H{1,2}|h{1,2}|m{1,2}|s{1,2}|[ ,.\-/:])+")
_V_BUILT_IN = {n.casefold(): n for n in ("Title", "Subject", "Author", "Keywords", "Comments", "Category", "Company",
                                          "Manager", "LastSavedBy")}
_V_SCOPES = ("document", "headers", "footers", "footnotes", "endnotes")
_V_KEYS = {"field", "format", "style", "last_on_page", "paragraph_number", "property", "path", "at_text", "position",
           "tracked", "author", "allow_missing", "date_convention"}
_V_CUSTOM = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
_V_CU = "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
_V_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_V_ONE = "⁣"            # the one character the core places for a field (any character: it is replaced here)
_V_MARK_REVISIONS = {W + "ins", W + "del", W + "moveFrom", W + "moveTo", W + "rPrChange"}


def _v_flag(params: Dict[str, Any], key: str, default: bool) -> bool:
    v = params.get(key, default)
    if type(v) is not bool:
        raise ValueError(f"{key} must be true or false")
    return v


def _v_style_name(styles: Styles, wanted: Any) -> str:
    if not isinstance(wanted, str) or not wanted.strip():
        raise ValueError("STYLEREF needs a style")
    st = styles.by_id.get(wanted)
    if st is None:
        named = [s for s in styles.by_id.values()
                 if (s.find(W + "name") is not None and (s.find(W + "name").get(W + "val") or "").casefold()
                     == wanted.casefold())]
        if len(named) != 1:
            raise ValueError(f"the style {wanted!r} is {'not in the file' if not named else 'named twice'} — the op "
                             f"must refuse")
        st = named[0]
    if st.get(W + "type", "paragraph") not in ("paragraph", "character"):
        raise ValueError(f"the style {wanted!r} is not a paragraph or character style — the op must refuse")
    name = st.find(W + "name")
    value = name.get(W + "val") if name is not None else None
    if not value or '"' in value:
        raise ValueError(f"the style {wanted!r} has no name a field can quote — the op must refuse")
    return value


def _v_property_name(pkg: VPackage, wanted: Any) -> str:
    if not isinstance(wanted, str) or not wanted.strip():
        raise ValueError("DOCPROPERTY needs a property")
    if wanted.casefold() in _V_BUILT_IN:
        return _V_BUILT_IN[wanted.casefold()]
    member = pkg.package_related(_V_CUSTOM)
    names = [e.get("name") or "" for e in pkg.xml(member).iter(_V_CU + "property")] if member else []
    found = [n for n in names if n.casefold() == wanted.casefold()]
    if len(found) != 1 or '"' in found[0]:
        raise ValueError(f"the document property {wanted!r} is not one property of this file — the op must refuse")
    return found[0]


def _v_instruction(pkg: VPackage, styles: Styles, params: Dict[str, Any]) -> str:
    """The field's instruction text, from the user's words (ValueError: the op had to refuse)."""
    field = params.get("field")
    if field not in _V_FIELDS:
        raise ValueError(f"unknown field {field!r}")
    own = {"STYLEREF": {"style", "last_on_page", "paragraph_number"}, "DOCPROPERTY": {"property"},
           "FILENAME": {"path"}}.get(field, set())
    if field in _V_NUMBERED | _V_DATED:
        own = own | {"format"}
    stray = {"format", "style", "last_on_page", "paragraph_number", "property", "path"} & set(params) - own
    if stray:
        raise ValueError(f"{sorted(stray)} do not belong to a {field} field")
    tokens = [field]
    if field == "STYLEREF":
        tokens.append('"' + _v_style_name(styles, params.get("style")) + '"')
        if _v_flag(params, "last_on_page", False):
            tokens.append("\\l")
        if _v_flag(params, "paragraph_number", False):
            tokens.append("\\n")
    if field == "DOCPROPERTY":
        tokens.append('"' + _v_property_name(pkg, params.get("property")) + '"')
    if field == "FILENAME" and _v_flag(params, "path", False):
        tokens.append("\\p")
    if "format" in params:
        fmt = params["format"]
        if field in _V_NUMBERED:
            if fmt not in _V_NUMBER_WORD:
                raise ValueError(f"number format {fmt!r}")
            tokens += ["\\*", _V_NUMBER_WORD[fmt]]
        else:
            if not isinstance(fmt, str) or not _V_PICTURE.fullmatch(fmt) or not re.search("[dMyHhms]", fmt):
                raise ValueError(f"date picture {fmt!r}")
            tokens += ["\\@", '"' + fmt + '"']
    return " " + " ".join(tokens) + " "


def _v_markup(field: str, instruction: str) -> List[Any]:
    begin = etree.Element(W + "fldChar")
    begin.set(W + "fldCharType", "begin")
    if field not in _V_AT_LAYOUT:
        begin.set(W + "dirty", "true")
    code = etree.Element(W + "instrText")
    code.set(_V_SPACE, "preserve")
    code.text = instruction
    out = [begin, code]
    for kind in ("separate", "end"):
        e = etree.Element(W + "fldChar")
        e.set(W + "fldCharType", kind)
        out.append(e)
    return out


def _v_owner(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _v_depths(pars: Sequence[Any]) -> List[Tuple[int, int]]:
    """(complex-field depth at the start of each paragraph, at its end): its own field characters, in paragraph order."""
    out, depth = [], 0
    for p in pars:
        start = depth
        for fc in p.iter(W + "fldChar"):
            if _v_owner(fc) is not p:
                continue
            kind = fc.get(W + "fldCharType")
            if kind == "begin":
                depth += 1
            elif kind == "end":
                depth = max(0, depth - 1)
        out.append((start, depth))
    return out


def _v_rpr(rpr, drop: Set[str]):
    """The item model's signature of a copy of ``rpr`` without the children ``drop`` names (None: no properties)."""
    if rpr is None:
        return None
    c = copy.deepcopy(rpr)
    for k in list(c):
        if isinstance(k.tag, str) and k.tag in drop:
            c.remove(k)
    return sig(c) if (len(c) or c.attrib) else None


def _v_edge_rpr(p, items: Sequence[Item], position: str):
    chars = [it for it in items if it.kind == "t" and it.pos is not None]
    if chars:
        run = (chars[0] if position == "start" else chars[-1]).run
        return _v_rpr(run.find(W + "rPr"), {W + "rPrChange"})
    return _v_rpr(p.find(W + "pPr/" + W + "rPr"), _V_MARK_REVISIONS)


def _v_field_items(markup: Sequence[Any], ctx: Tuple[Any, ...], chain_els: Tuple[Any, ...]) -> List[Item]:
    return [Item("x", sig(el), ctx, None, el.tag, None, None, True, chain_els) for el in markup]


def _v_check_runs(got: Sequence[Item], where_k: Sequence[int], tracked: bool, where: str, errs: List[str]) -> None:
    """The output items at ``where_k`` (one field, four items) stand in four consecutive runs of one parent, each holding
    nothing but its properties and that element; tracked, that parent is a w:ins holding exactly those runs."""
    runs = [got[k].run for k in where_k]
    if any(r is None for r in runs) or len({id(r) for r in runs}) != 4:
        errs.append(f"{where}: the field's four elements are not in four runs of their own")
        return
    parent = runs[0].getparent()
    if any(r.getparent() is not parent for r in runs) or any(runs[i].getnext() is not runs[i + 1] for i in range(3)):
        errs.append(f"{where}: the field's runs are not consecutive")
    for r, k in zip(runs, where_k):
        kids = [c for c in r if isinstance(c.tag, str) and c.tag != W + "rPr"]
        if kids != [got[k].el]:
            errs.append(f"{where}: a field run holds more than its field element")
    if tracked and (parent.tag != W + "ins" or [c for c in parent if isinstance(c.tag, str)] != runs):
        errs.append(f"{where}: the tracked field is not the whole of one w:ins")


def _inserted_fields(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    params = dict(a.get("params") or {})
    select = dict(a.get("select") or {})
    if set(params) - _V_KEYS or set(select) - {"scope", "paragraphs"}:
        raise ValueError(f"unknown request keys {sorted(set(params) - _V_KEYS) + sorted(set(select) - {'scope', 'paragraphs'})}")
    field = params.get("field")
    instruction = _v_instruction(ctx.src, ctx.src_styles, params)
    markup = _v_markup(field, instruction)
    tracked = _v_flag(params, "tracked", True)
    author = a.get("author")
    if tracked and (not isinstance(author, str) or not author.strip()):
        raise ValueError("a tracked field needs its author")
    at_text, position = params.get("at_text"), params.get("position")
    if (at_text is None) == (position is None):
        raise ValueError("give the placeholder text or the position, not both")
    if at_text is not None and (not isinstance(at_text, str) or not at_text.strip()
                                or any(ord(c) < 32 or c == "\x7f" for c in at_text)):
        raise ValueError(f"placeholder {at_text!r}")
    pred = select.get("paragraphs")
    if position is not None:
        if position not in ("start", "end"):
            raise ValueError(f"position {position!r}")
        targeted = (bool(set(pred) - {"case_insensitive", "flexible_whitespace"}) if isinstance(pred, dict)
                    else pred not in (None, "all", {}))
        if not targeted:
            raise ValueError("a field at the start or end of paragraphs needs a targeted paragraph selector")
    scope = select["scope"] if select.get("scope") is not None else ["document", "headers", "footers"]
    if isinstance(scope, str):
        scope = [scope]
    if not scope or set(scope) - set(_V_SCOPES):
        raise ValueError(f"scope {scope!r}")
    old_ids = set().union(*(part_revision_ids(ctx.src.xml(name)) for name in ctx.src_tp))
    found: Dict[str, Dict[str, int]] = {}
    for part in ctx.scope_parts(scope):
        if part not in ctx.both:
            errs.append(f"inserted_fields: {part} is missing from the output")
            continue
        sv, ov = ctx.view(part)
        in_field = _v_field_runs(sv.root)
        depths = _v_depths(sv.pars)
        for bi, p in enumerate(sv.pars):
            if pred is not None and not para_matches(pred, p, bi, sv, ctx.src_styles):
                continue
            where = f"inserted_fields {part} block {bi}"
            items = v_items(p)
            if at_text is not None:
                text = v_run_text(items)
                edits, k = [], text.find(at_text)
                while k >= 0:
                    edits.append((k, k + len(at_text), _V_ONE, False))
                    k = text.find(at_text, k + len(at_text))
                if not edits:
                    continue
            if _v_owner(p) is not None:
                errs.append(f"{where}: the paragraph is in a text box — the op must refuse")
                continue
            if at_text is not None:
                try:
                    v_check_edits(p, items, edits, tracked, in_field)
                except ValueError as e:
                    errs.append(f"{where}: {e} — the op must refuse")
                    continue
                by_pos = {it.pos: it for it in items if it.pos is not None}
                if not tracked and any(by_pos[s].run.find(W + "rPr/" + W + "rPrChange") is not None
                                       for s, _, _, _ in edits):
                    errs.append(f"{where}: the placeholder's run carries a tracked formatting change — the op must "
                                f"refuse")
                    continue
                placed = expected_items(p, items, edits, tracked, author)
                expected: List[Item] = []
                fields: List[List[int]] = []
                for it in placed:
                    if it.kind == "t" and it.el is None and it.value == _V_ONE:
                        fields.append(list(range(len(expected), len(expected) + 4)))
                        expected += _v_field_items(markup, it.ctx, it.chain_els)
                    else:
                        expected.append(it)
            else:
                start, end = depths[bi]
                if (start if position == "start" else end) > 0:
                    errs.append(f"{where}: the paragraph's {position} lies inside a field — the op must refuse")
                    continue
                chain = (("NEW", W + "ins", author),) if tracked else ()
                new = _v_field_items(markup, (_v_edge_rpr(p, items, position), (), chain), (None,) if tracked else ())
                expected = new + list(items) if position == "start" else list(items) + new
                first = 0 if position == "start" else len(items)
                fields = [list(range(first, first + 4))]
            oi = ctx.maps[part].get(bi)
            if oi is None:
                errs.append(f"{where}: the paragraph has no counterpart in the output")
                continue
            out_p = ov.pars[oi]
            if sig(p.find(W + "pPr")) != sig(out_p.find(W + "pPr")) or dict(p.attrib) != dict(out_p.attrib):
                errs.append(f"{where}: the paragraph's properties or attributes changed")
            got = v_items(out_p)
            d = compare_items(expected, got, old_ids)
            if d:
                errs.append(f"{where}: not exactly the field(s) predicted — {d}")
            else:
                for ks in fields:
                    _v_check_runs(got, ks, tracked, where, errs)
            ctx.verified_blocks.setdefault(part, {})[bi] = oi
            found.setdefault(part, {})[str(bi)] = len(fields)
    want = {part: {str(b): int(n) for b, n in blocks.items()} for part, blocks in (a.get("expected") or {}).items()}
    if found != want:
        errs.append(f"inserted_fields: fields go into {found} here, the plan predicted {want}")
    total = sum(n for blocks in found.values() for n in blocks.values())
    return f"inserted_fields: {total} {field} field(s){' (tracked)' if tracked else ''}, instruction {instruction!r}"


FIELD_INSERT_KINDS: Dict[str, Any] = {"inserted_fields": _inserted_fields}
