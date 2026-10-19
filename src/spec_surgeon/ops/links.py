"""Ops: remove_hyperlinks — the links come out, the words stay (docs/BUILTIN_OPS_BATCH2_DESIGN.md §9) — and
unlink_fields — fields become the text they show (§10, §1.5). The verifier re-derives both with its own code
(verify/links.py, kinds ``removed_hyperlinks`` and ``unlinked_fields``); the definitions below are both-sides
definitions (CLAUDE.md): keep the two identical.

* **Link element**: a ``w:hyperlink``; *external* when it has an ``r:id``, *internal* otherwise (``w:anchor``: a
  bookmark in the document — a table of contents' entries among them).
* **Link field**: a field (ops/fields.py) whose type is HYPERLINK; *internal* when its instruction holds the switch
  ``\\l`` — a token outside double quotes, any case (``instruction_switches``: tokens are ``"…"`` — an unterminated
  quote runs to the end — or runs of other non-space characters; a switch is an unquoted token starting with a
  backslash) — external otherwise.
* **Link style**: a character style whose ``w:name`` is Hyperlink or FollowedHyperlink (any case — Word's built-in
  names, whatever the style id). A selected link's runs lose a direct ``w:rStyle`` naming one (an emptied ``w:rPr``
  goes too) unless ``keep_style``. The runs of a link element: its ``w:r`` descendants whose paragraph is the link's;
  of a link field: its *result runs* — the paragraph's runs holding a child (other than ``w:rPr``) that lies after the
  field's ``separate`` and before its ``end`` in document order (a simple field: the ``w:r`` inside it).
* **Unlinking**: a link element is replaced by its children; a link field is unlinked (ops/fields.unlink).
* **Relationships**: a relationship of the part's rels that a removed link element named (``r:id``), of the hyperlink
  type, that no element of the part names any more once the selected link elements are gone (any attribute in the
  relationships namespace — a picture's click link keeps one alive) is removed: the address leaves the file.

unlink_fields: ``types`` (field type names, upper-cased, or ``"all"`` = every type but the page numbers — PAGE,
NUMPAGES, SECTIONPAGES, PAGEREF, which are refused when named). A selected field must be local, not inside a text box
(``textbox``), hold no field in its code and sit in no other field's code (``nested-field``), not be a form field
(``form-field``) and have no marker or instruction in a tracked change (``existing-revisions``) — ``Field.refusal``
plus those two. A field that shows nothing (no ``separate``, or nothing between it and ``end``) goes whole (flagged).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from ..ooxml import block_full_text, block_struct_counts, block_text_without, paragraphs
from ..package import R, REL_TYPE_BASE, W, DocxPackage
from . import prose, register
from .base import BlockDiff, JobException, Op, PlanFragment
from .fields import NEVER_UNLINK, REFUSAL_TEXT, Field, fields_of, paragraph_of, unlink
from .replace_table import resolve_scope
from .selectors import SCOPE_KINDS, StyleSheet, scope_parts

HYPERLINK_REL = REL_TYPE_BASE + "hyperlink"
DEFAULT_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]
LINK_STYLE_NAMES = ("hyperlink", "followedhyperlink")
WHICH = ("external", "internal", "all")
DATE_TYPES = ("DATE", "TIME", "CREATEDATE", "SAVEDATE", "PRINTDATE")
_TOKEN = re.compile(r'"[^"]*"?|[^\s"]+')            # an unterminated quote runs to the end
SCOPE_PARAM = {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
               "description": "part kinds (default: document, headers, footers, footnotes, endnotes); same as "
                              "select.scope"}
SELECT_SCHEMA = {"type": "object", "properties": {"scope": SCOPE_PARAM}}
ALLOW_MISSING = {"type": "boolean", "default": True,
                 "description": "true (default) = a file with nothing to remove is a no-op with a note; false = it is "
                                "set aside (not-found)"}


# ---- both-sides definitions (verify/links.py has its own) -----------------------------------------------------------

def instruction_switches(instruction: str) -> List[str]:
    """The switches of a field instruction: unquoted tokens that start with a backslash."""
    return [t for t in _TOKEN.findall(instruction or "") if not t.startswith('"') and t.startswith("\\")]


def field_is_internal(f: Field) -> bool:
    return any(s.lower() == "\\l" for s in instruction_switches(f.instruction))


def link_style_ids(sheet: StyleSheet) -> Set[str]:
    out = set()
    for sid, st in sheet.styles.items():
        name = st.find(W + "name")
        if st.get(W + "type") == "character" and name is not None \
                and (name.get(W + "val") or "").casefold() in LINK_STYLE_NAMES:
            out.add(sid)
    return out


def link_runs(h, p) -> List[Any]:
    return [r for r in h.iter(W + "r") if paragraph_of(r) is p]


def result_runs(f: Field, p) -> List[Any]:
    """The runs a field shows its result in: its paragraph's runs holding a child (not w:rPr) after the field's
    separate and before its end, in document order; for a simple field the runs inside it."""
    if f.simple is not None:
        return [r for r in f.simple.iter(W + "r") if paragraph_of(r) is p]
    if f.separate is None or f.end is None:
        return []
    order = list(p.iter())
    pos = {id(e): k for k, e in enumerate(order)}
    lo, hi = pos.get(id(f.separate)), pos.get(id(f.end))
    if lo is None or hi is None:
        return []
    out = []
    for r in p.iter(W + "r"):
        if paragraph_of(r) is not p:
            continue
        if any(isinstance(c.tag, str) and c.tag != W + "rPr" and lo < pos[id(c)] < hi for c in r):
            out.append(r)
    return out


def in_field_result(el, fields: Sequence[Field], root, skip: Set[int] = frozenset()) -> Optional[Field]:
    """The innermost field (not in ``skip``) whose result ``el`` lies in — between its separate and end over the whole
    part, or inside its w:fldSimple — else None. For the plan's flags only (Word rebuilds such a field's result)."""
    els = list(root.iter())                        # held: the proxies (and so their ids) stay alive while used
    order = {id(e): k for k, e in enumerate(els)}
    at = order.get(id(el))
    best = None
    for f in fields:
        if id(f) in skip:
            continue
        if f.simple is not None:
            inside = any(a is f.simple for a in el.iterancestors(W + "fldSimple"))
        else:
            inside = (f.separate is not None and f.end is not None and at is not None
                      and order[id(f.separate)] < at < order[id(f.end)])
        if inside:
            best = f
    return best


def shows_nothing(f: Field, p) -> bool:
    if f.simple is not None:
        return not any(isinstance(c.tag, str) and c.tag != W + "rPr" for r in result_runs(f, p) for c in r)
    return not result_runs(f, p)


def restyled(runs: Sequence[Any], styles: Set[str]) -> List[Any]:
    """The runs whose direct w:rStyle names a link style (what loses it)."""
    out = []
    for r in runs:
        rs = r.find(f"{W}rPr/{W}rStyle")
        if rs is not None and rs.get(W + "val") in styles and r not in out:
            out.append(r)
    return out


def unwrap(el) -> None:
    """Replace ``el`` by its children (in order; a tail it carried moves to the last of them)."""
    parent = el.getparent()
    idx = parent.index(el)
    kids = list(el)
    for c in kids:
        parent.insert(idx, c)
        idx += 1
    if el.tail:
        if kids:
            kids[-1].tail = (kids[-1].tail or "") + el.tail
        else:
            prev = el.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + el.tail
            else:
                parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def addresses_to_remove(pkg: DocxPackage, part: str, root, gone_links: Sequence[Any]) -> List[str]:
    """The relationships of ``part`` that leave with ``gone_links``: of the hyperlink type, named by one of them, named by
    nothing left in the part (any attribute in the relationships namespace of any other element)."""
    ids_gone = {id(h) for h in gone_links}
    used_after = {v for el in root.iter() if isinstance(el.tag, str) and id(el) not in ids_gone
                  for k, v in el.attrib.items() if k.startswith(R)}
    rels = pkg.rels(part)
    named = sorted({h.get(R + "id") for h in gone_links if h.get(R + "id")})
    return [rid for rid in named if rid not in used_after and rid in rels and rels[rid].type == HYPERLINK_REL]


def drop_style(r, styles: Set[str]) -> None:
    rpr = r.find(W + "rPr")
    rs = rpr.find(W + "rStyle") if rpr is not None else None
    if rs is not None and rs.get(W + "val") in styles:
        rpr.remove(rs)
        if not len(rpr) and not rpr.attrib:
            r.remove(rpr)


def _shown(runs: Sequence[Any], limit: int = 60) -> str:
    s = " ".join("".join(t.text or "" for r in runs for t in r.iter(W + "t", W + "delText")).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _scope(select: Dict[str, Any], params: Dict[str, Any], name: str) -> List[str]:
    select = dict(select or {})
    if set(select) - {"scope"}:
        raise JobException(f"{name} selects by scope only; unknown {sorted(set(select) - {'scope'})}", "bad-params")
    return resolve_scope(select, params, DEFAULT_SCOPE)


def _bool(params: Dict[str, Any], key: str, default: bool) -> bool:
    v = params.get(key, default)
    if not isinstance(v, bool):
        raise JobException(f"'{key}' must be true or false", "bad-params")
    return v


def _scope_words(select: Dict[str, Any], params: Dict[str, Any]) -> str:
    """The plan's prose: ``in the document body, the headers and the footers`` (the body named when it is alone)."""
    return prose.scope_words(select.get("scope") or params.get("scope") or DEFAULT_SCOPE) or "in the document body"


def _set_aside(params: Dict[str, Any], what: str) -> str:
    if params.get("allow_missing", True) is False:
        return f"a file with no such {what} is set aside rather than passed through"
    return ""


def _textbox(p) -> bool:
    return p is not None and paragraph_of(p) is not None


def _field_refusal(f: Field, part: str, extra_nested: bool = False) -> None:
    """Raise the field's refusal (textbox first, then Field.refusal, then sitting in another field's code)."""
    p = f.paragraph()
    where = f"{part}: the {f.type or 'unnamed'} field"
    if _textbox(p):
        raise JobException(f"{where} is inside a text box (Word keeps two copies of it); unlink it in Word", "textbox",
                           {"part": part, "field": f.instruction.strip()[:80]})
    code = f.refusal()
    if code:
        raise JobException(f"{where} {f.instruction.strip()[:60]!r} cannot be turned into text: {REFUSAL_TEXT[code]}",
                           code, {"part": part, "field": f.instruction.strip()[:80]})
    if extra_nested and f.in_parent_code:
        raise JobException(f"{where} sits inside the code of a {f.parent.type or 'unnamed'} field; turning it into text "
                           f"would change what that field does", "nested-field",
                           {"part": part, "field": f.instruction.strip()[:80]})


def paragraph_links(p) -> List[Any]:
    """The link elements of the paragraph, in document order (a text box's are its own paragraph's)."""
    return [h for h in p.iter(W + "hyperlink") if paragraph_of(h) is p]


def paragraph_fields(fields: Sequence[Field], p) -> List[Field]:
    return [f for f in fields if f.paragraph() is p]


def _locate(p, fields: Sequence[Field], hs: Sequence[Any], fs: Sequence[Field]) -> Dict[str, Any]:
    """Where the chosen links and fields stand within their paragraph — what apply finds them by (stable: another op
    touching this paragraph is a plan-time conflict, one adding or removing paragraphs touches the whole part)."""
    links_in_p, fields_in_p = paragraph_links(p), paragraph_fields(fields, p)
    return {"links": [links_in_p.index(h) for h in hs], "link_ids": [[h.get(R + "id"), h.get(W + "anchor")] for h in hs],
            "fields": [fields_in_p.index(f) for f in fs], "instructions": [f.instruction for f in fs]}


def _resolve(root, blocks: Dict[str, Dict[str, Any]], name: str) -> Tuple[List[Any], List[Field]]:
    """The chosen links and fields found again in the working copy (refused as an internal error if they moved)."""
    pars = paragraphs(root)
    fields = fields_of(root)
    hs: List[Any] = []
    fs: List[Field] = []
    for bi, e in sorted(blocks.items(), key=lambda kv: int(kv[0])):
        p = pars[int(bi)] if int(bi) < len(pars) else None
        links_in_p = paragraph_links(p) if p is not None else []
        fields_in_p = paragraph_fields(fields, p) if p is not None else []
        for k, ident in zip(e["links"], e["link_ids"]):
            if k >= len(links_in_p) or [links_in_p[k].get(R + "id"), links_in_p[k].get(W + "anchor")] != list(ident):
                raise JobException(f"{name}: paragraph {bi} changed between plan and apply (link {k})", "internal")
            hs.append(links_in_p[k])
        for k, instr in zip(e["fields"], e["instructions"]):
            if k >= len(fields_in_p) or fields_in_p[k].instruction != instr:
                raise JobException(f"{name}: paragraph {bi} changed between plan and apply (field {k})", "internal")
            fs.append(fields_in_p[k])
    return hs, fs


def _predict_paragraph(frag: PlanFragment, part: str, bi: int, p, gone: Sequence[Any], note: str) -> None:
    before, after = block_full_text(p), block_text_without(p, gone)
    s_before, s_after = block_struct_counts(p), block_struct_counts(p, gone)
    if before != after or s_before != s_after:
        frag.diffs.append(BlockDiff(part=part, block_index=bi, before=before, after=after,
                                    struct_after=s_after if s_after != s_before else None, note=note))
        frag.untracked_diffs_expected = True
    frag.touches.append((part, str(bi), "text"))
    if part not in frag.intended_parts:
        frag.intended_parts.append(part)


@register
class RemoveHyperlinks(Op):
    name = "remove_hyperlinks"
    mode = "formatting"
    description = ("Remove hyperlinks and keep their words: external links (web, e-mail — the default), internal "
                   "links (to a bookmark: a table of contents' entries), or all; both the w:hyperlink form and the "
                   "HYPERLINK field. The linked words lose the Hyperlink character style unless keep_style; an "
                   "address no link uses any more leaves the file. A file with no such link is left as it is.")
    params_schema = {
        "type": "object",
        "properties": {
            "which": {"type": "string", "enum": list(WHICH), "default": "external",
                      "description": "external (web addresses, e-mail) | internal (links to a place in the document, "
                                     "e.g. a table of contents) | all"},
            "keep_style": {"type": "boolean", "default": False,
                           "description": "true = the linked words keep the Hyperlink character style (blue, "
                                          "underlined); false (default) = they look like the text around them"},
            "allow_missing": ALLOW_MISSING,
            "scope": SCOPE_PARAM,
        },
    }
    select_schema = SELECT_SCHEMA
    verification_profile = {
        "V1": "the parts with a removed link and, when an address goes, the part's rels",
        "V2": "a HYPERLINK field's paragraph loses the field code (text); unwrapping a w:hyperlink changes no text",
        "V3": "a HYPERLINK field's paragraph loses its field characters (declared)",
        "V10": "removed_hyperlinks re-derives every link with the verifier's own code and checks each paragraph item by "
               "item (the link gone from every chain, the link style gone, the field's markers gone) and the rels "
               "member (exactly the unused addresses removed); nothing else changes",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        which = params.get("which", "external")
        what = {"internal": "the internal hyperlinks (links to a place in the document, such as a table of contents' "
                            "entries)",
                "all": "every hyperlink, external (web addresses and e-mail links) or internal (links to a place in the "
                       "document),"}.get(which, "the external hyperlinks (web addresses and e-mail links)")
        if params.get("keep_style") is True:
            words = f"keeping the linked words in {prose.styles(['Hyperlink'])} (blue and underlined)"
        else:
            words = (f"keeping the linked words but taking {prose.styles(['Hyperlink', 'FollowedHyperlink'])} off them "
                     f"so they look like the text around them")
        first = prose.sentence(f"Remove {what} {_scope_words(select, params)}, {words}")
        remarks = ["a web address that no remaining link uses leaves the file" if which != "internal" else "",
                   _set_aside(params, "link")]
        remarks = [r for r in remarks if r]
        return first if not remarks else first + " " + prose.sentence("; ".join(remarks))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        scope = _scope(select, params, self.name)
        which = params.get("which", "external")
        if which not in WHICH:
            raise JobException(f"'which' must be one of {list(WHICH)}, got {which!r}", "bad-params")
        keep = _bool(params, "keep_style", False)
        allow = _bool(params, "allow_missing", True)
        parts = scope_parts(pkg, scope)
        styles = link_style_ids(StyleSheet(pkg))
        frag = self.fragment({"scope": scope}, {**params, "which": which, "keep_style": keep, "allow_missing": allow})
        data: Dict[str, Dict[str, Any]] = {}
        blocks: Dict[str, List[int]] = {}
        removed_rels: Dict[str, List[str]] = {}
        n_links = n_fields = n_rels = 0
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives the links over every part in scope
            root = pkg.xml(part)
            pars = paragraphs(root)
            index = {id(p): i for i, p in enumerate(pars)}
            links = list(root.iter(W + "hyperlink"))
            fields = fields_of(root)
            chosen_links: List[Tuple[int, Any]] = []
            for k, h in enumerate(links):
                internal = h.get(R + "id") is None
                if which == "all" or (which == "internal") == internal:
                    p = paragraph_of(h)
                    if p is None:
                        raise JobException(f"{part}: a link stands outside any paragraph", "structure", {"part": part})
                    if _textbox(p):
                        raise JobException(f"{part}: the link «{_shown(link_runs(h, p))}» is inside a text box (Word "
                                           f"keeps two copies of it); remove it in Word", "textbox", {"part": part})
                    chosen_links.append((k, h))
            chosen_fields: List[Tuple[int, Field]] = []
            for k, f in enumerate(fields):
                if f.type != "HYPERLINK":
                    continue
                if which == "all" or (which == "internal") == field_is_internal(f):
                    _field_refusal(f, part, extra_nested=True)
                    chosen_fields.append((k, f))
            if not chosen_links and not chosen_fields:
                continue
            chosen_field_ids = {id(f) for _, f in chosen_fields}
            for el, what in [(h, f"the link «{_shown(link_runs(h, paragraph_of(h)))}»") for _, h in chosen_links] + \
                    [(f.begin if f.simple is None else f.simple, f"the HYPERLINK field {f.instruction.strip()[:50]!r}")
                     for _, f in chosen_fields]:
                host = in_field_result(el, fields, root, skip=chosen_field_ids)
                if host is not None:
                    frag.flags.append(f"{part}: {what} sits in what a {host.type or 'unnamed'} field shows; Word puts "
                                      f"it back when that field is updated")
            by_par: Dict[int, Dict[str, List[Any]]] = {}
            for _, h in chosen_links:
                p = paragraph_of(h)
                by_par.setdefault(index[id(p)], {"links": [], "fields": []})["links"].append(h)
            for _, f in chosen_fields:
                by_par.setdefault(index[id(f.paragraph())], {"links": [], "fields": []})["fields"].append(f)
            for bi in sorted(by_par):
                p = pars[bi]
                hs, fs = by_par[bi]["links"], by_par[bi]["fields"]
                gone = [m for f in fs if f.simple is None for m in f.markers()]
                runs = [r for h in hs for r in link_runs(h, p)] + [r for f in fs for r in result_runs(f, p)]
                for r in runs:
                    rpr = r.find(W + "rPr")
                    if rpr is not None and (rpr.find(W + "color") is not None or rpr.find(W + "u") is not None):
                        frag.flags.append(f"{part} paragraph {bi}: the link text «{_shown([r])}» keeps its own colour "
                                          f"or underline (direct formatting, not the link style)")
                        break
                _predict_paragraph(frag, part, bi, p, gone,
                                   f"{len(hs) + len(fs)} link(s) removed, the words kept")
            named = sorted({h.get(R + "id") for _, h in chosen_links if h.get(R + "id")})
            remove = addresses_to_remove(pkg, part, root, [h for _, h in chosen_links])
            kept = [rid for rid in named if rid not in remove]
            if kept:
                frag.notes.append(f"{part}: address(es) {kept} stay — something else in the part still uses them (a "
                                  f"picture's link, another link that stays) or they are not web links")
            if remove:
                rels_member = pkg.rels_path_for(part)
                removed_rels[rels_member] = remove
                if rels_member not in frag.intended_parts:
                    frag.intended_parts.append(rels_member)
                frag.touches.append((rels_member, "*", "rels"))
            n_links += len(chosen_links)
            n_fields += len(chosen_fields)
            n_rels += len(remove)
            blocks[part] = sorted(by_par)
            data[part] = {"blocks": {str(bi): _locate(pars[bi], fields, e["links"], e["fields"])
                                     for bi, e in sorted(by_par.items())},
                          "remove_rels": remove}
        # the user's own words (scope, which, keep_style as asked; absent = the default): the verifier reads them itself
        frag.property_assertions.append({
            "kind": "removed_hyperlinks", "scope": select.get("scope") or params.get("scope"),
            **{k: params[k] for k in ("which", "keep_style") if k in params},
            "expected_blocks": blocks, "expected_links": n_links, "expected_fields": n_fields,
            "removed_relationships": removed_rels})
        if not n_links and not n_fields:
            if allow:
                frag.notes.append(f"remove_hyperlinks: no {which if which != 'all' else ''} hyperlink in {scope}: "
                                  f"nothing to do.".replace("  ", " "))
                return frag
            raise JobException(f"no {which if which != 'all' else ''} hyperlink in {scope}".replace("  ", " "),
                               "not-found", {"scope": scope, "which": which})
        frag.geometry_reference = "source"
        frag.transform_data = {"parts": data, "styles": sorted(styles), "keep_style": keep}
        frag.notes.append(f"remove_hyperlinks ({which}): {n_links} link(s) and {n_fields} HYPERLINK field(s) removed, "
                          f"the words kept" + ("" if keep else ", the link style taken off them")
                          + (f"; {n_rels} address(es) removed from the file" if n_rels else ""))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("parts"):
            return
        styles = set(td["styles"])
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            hs, fs = _resolve(root, d["blocks"], self.name)
            if not td["keep_style"]:
                runs = []
                for h in hs:
                    runs += link_runs(h, paragraph_of(h))
                for f in fs:
                    runs += result_runs(f, f.paragraph())
                for r in restyled(runs, styles):
                    drop_style(r, styles)
            for f in fs:
                unlink(f)
            for h in hs:
                unwrap(h)
            pkg.write_xml(part, root)
            if d["remove_rels"]:
                pkg.edit_rels(part, remove=d["remove_rels"])


def normalize_types(raw: Any) -> Any:
    """``"all"`` or the list of field types, upper-cased (bad-params for anything else or a page-number type)."""
    if raw == "all":
        return "all"
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw or not all(isinstance(t, str) and t.strip() for t in raw):
        raise JobException("'types' must be \"all\" or a list of field type names (DOCPROPERTY, REF, DATE …)",
                           "bad-params")
    types = []
    for t in raw:
        u = t.strip().upper()
        if u not in types:
            types.append(u)
    pages = [t for t in types if t in NEVER_UNLINK]
    if pages:
        raise JobException(f"page numbers are never converted to text ({pages}): they would freeze at the page they "
                           f"showed when the file was saved", "bad-params", {"types": pages})
    return types


def type_selected(ftype: str, types: Any) -> bool:
    if types == "all":
        return ftype not in NEVER_UNLINK
    return ftype in types


@register
class UnlinkFields(Op):
    name = "unlink_fields"
    mode = "formatting"
    description = ("Turn fields into the plain text they show — DOCPROPERTY, REF, DATE, a mail-merge field … — by type, "
                   "or all of them; page numbers (PAGE, NUMPAGES, SECTIONPAGES, PAGEREF) never. What each field shows "
                   "stays, with its formatting; the field code goes. A file with no such field is left as it is.")
    params_schema = {
        "type": "object",
        "properties": {
            "types": {"type": ["array", "string"], "items": {"type": "string"},
                      "description": "field types to turn into text, e.g. [\"DOCPROPERTY\"], or \"all\" (every type "
                                     "but the page numbers)"},
            "allow_missing": ALLOW_MISSING,
            "scope": SCOPE_PARAM,
        },
        "required": ["types"],
    }
    select_schema = SELECT_SCHEMA
    verification_profile = {
        "V2": "every paragraph with an unlinked complex field loses the field code (its text); a simple field's "
              "instruction is an attribute — no text change",
        "V3": "the field characters go (declared per paragraph)",
        "V10": "unlinked_fields re-derives the fields with the verifier's own parser and checks each paragraph item by "
               "item (the markers gone, a simple field unwrapped, the result kept); nothing else changes",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        raw = params.get("types")
        given = [raw] if isinstance(raw, str) and raw != "all" else raw if isinstance(raw, list) else []
        types = [t.strip().upper() for t in given if isinstance(t, str) and t.strip()]
        if raw == "all":
            what, shown = "every field but the page numbers", "it shows"
        elif types:
            what = "the " + prose.join(prose.quote(t) for t in types) + (" fields" if len(types) > 1 else " field")
            shown = "they show" if len(types) > 1 else "it shows"
        else:
            what, shown = "the named fields", "they show"
        first = prose.sentence(f"Turn {what} {_scope_words(select, params)} into the plain text {shown}")
        remarks = ["what each field shows stays, with its formatting; the field code goes"]
        if raw == "all":
            remarks.append("page numbers are never turned into text, since they would freeze at the page they showed "
                           "when the file was saved")
        if raw == "all" or any(t in DATE_TYPES for t in types):
            remarks.append("a date or time field keeps the date it showed when the file was last saved")
        remarks.append(_set_aside(params, "field"))
        return first + " " + prose.sentence("; ".join(r for r in remarks if r))

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        scope = _scope(select, params, self.name)
        types = normalize_types(params.get("types"))
        allow = _bool(params, "allow_missing", True)
        parts = scope_parts(pkg, scope)
        frag = self.fragment({"scope": scope}, {**params, "allow_missing": allow})
        data: Dict[str, Dict[str, Any]] = {}
        expected: Dict[str, Dict[str, List[str]]] = {}
        per_type: Dict[str, int] = {}
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives the fields over every part in scope
            root = pkg.xml(part)
            pars = paragraphs(root)
            index = {id(p): i for i, p in enumerate(pars)}
            fields = fields_of(root)
            chosen = [(k, f) for k, f in enumerate(fields) if type_selected(f.type, types)]
            if not chosen:
                continue
            for _, f in chosen:
                _field_refusal(f, part, extra_nested=True)
            chosen_ids = {id(f) for _, f in chosen}
            by_par: Dict[int, List[Field]] = {}
            for _, f in chosen:
                by_par.setdefault(index[id(f.paragraph())], []).append(f)
            for bi in sorted(by_par):
                p = pars[bi]
                fs = by_par[bi]
                for f in fs:
                    per_type[f.type] = per_type.get(f.type, 0) + 1
                    what = f.instruction.strip()[:60]
                    if shows_nothing(f, p):
                        frag.flags.append(f"{part} paragraph {bi}: the field {what!r} shows nothing; it is removed")
                    if f.type in DATE_TYPES:
                        frag.notes.append(f"{part} paragraph {bi}: the {f.type} field keeps the date it showed when the "
                                          f"file was last saved")
                    if f.parent is not None and id(f.parent) not in chosen_ids:
                        frag.flags.append(f"{part} paragraph {bi}: the field {what!r} sits in what a {f.parent.type} "
                                          f"field shows; Word puts it back when that field is updated")
                gone = [m for f in fs if f.simple is None for m in f.markers()]
                _predict_paragraph(frag, part, bi, p, gone, f"{len(fs)} field(s) turned into text")
            expected[part] = {str(bi): [f.instruction for f in fs] for bi, fs in sorted(by_par.items())}
            data[part] = {"blocks": {str(bi): _locate(pars[bi], fields, [], fs) for bi, fs in sorted(by_par.items())}}
        frag.property_assertions.append({"kind": "unlinked_fields", "scope": select.get("scope") or params.get("scope"),
                                         "types": params.get("types"),
                                         "expected": expected})
        total = sum(per_type.values())
        if not total:
            if allow:
                frag.notes.append(f"unlink_fields: no {types if types != 'all' else ''} field in {scope}: nothing to "
                                  f"do.".replace("  ", " "))
                return frag
            raise JobException(f"no field of the types {types} in {scope}", "not-found",
                               {"scope": scope, "types": types})
        frag.geometry_reference = "none"
        frag.geometry_reason = "a converted field shows its saved result, which a renderer may recompute in the source"
        frag.transform_data = {"parts": data}
        frag.notes.append(f"unlink_fields: {total} field(s) turned into the text they show — "
                          + ", ".join(f"{t or '(no type)'} ×{n}" for t, n in sorted(per_type.items())))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("parts"):
            return
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            _, fs = _resolve(root, d["blocks"], self.name)
            for f in fs:
                unlink(f)
            pkg.write_xml(part, root)

