"""Op: apply_header_footer_template — make every section's headers/footers those of a project template, with the
template's placeholder slots filled from each document's own content (the header-footer-templating playbook).

Per target section (selector ``sections: "all"`` only), ``template_sections`` maps it to a template section
(``{"cover": i, "body": j}``: target section 0 → i, the others → j; or ``{"all": i}``). For each selected kind
(header/footer) and each type the template section *displays* (default always, first with its titlePg, even with
evenAndOddHeaders) the template part is copied into the target as a NEW part:

* slots (decision §14.2: yellow highlight and ``{{NAME}}``, see ops/slots.py) are filled by rule — extracted from
  the target, never typed; ``literal`` fills are the user's approved words (op mode becomes ``untracked``);
* tab stops of non-table paragraphs are rescaled from the template section's text width to the target section's
  (right stops keep their distance from the margin, center stops scale) when ``rescale_tabs``;
* properties are **pinned**: wherever a copied paragraph, paragraph mark or run would render differently in the
  target (its styles, docDefaults and theme differ) the template's effective value is written as direct
  formatting (ops/effective.py lists the properties) — the playbook's "pin spacing" generalised; nested (textbox)
  paragraphs are copied untouched;
* styles are reconciled by default role, name and linked-style identity across Word languages; genuinely
  missing dependencies are imported with isolated IDs for collisions. Existing target styles stay intact;
* images are copied under new names, ``wp:docPr`` ids renumbered above the target's, a content-type override
  and a document relationship added per part;
* the target's old header/footer parts of the selected kinds go (with their rels, overrides, orphaned media);
  pending revisions in them are flagged by name;
* optionally the target's titlePg and header/footer distances follow the template section.

Everything is predicted from the source and the template: members removed/added, every deleted and inserted
block's exact text, the fills, the pins, the new references; V10 re-derives the slots, fills, effective
properties and part skeleton independently (verify/template.py) and V7 compares header/footer anchor words with
the template's own render.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import CT_PART, MEDIA_REL_TYPES, NS, R, REL, W, DocxPackage, parse_xml, serialize_xml, REL_TYPE_BASE
from ..ooxml import (PPR_ORDER, RPR_ORDER, SECTPR_ORDER, block_full_text, count_revisions, get_or_add,
                     insert_in_order, mark_rpr, paragraph_ppr, paragraphs, run_rpr, set_rfonts)
from . import prose, register
from .base import LEFT_ELSEWHERE, BlockDiff, JobException, Op, PlanFragment
from .effective import AmbiguousToggle, Resolver, rescale_tabs, text_width, theme_fonts, theme_part
from .hf_common import (FOOTER_CT, HEADER_CT, KINDS, TYPES, apply_removal, check_part_relationships, effective_refs,
                        plan_removal, refs)
from .selectors import StyleSheet
from .template_styles import reconcile_styles, remap_references, reference_parts
from .slots import (REFERENCE_FILLS, apply_slot, display_segments, expected_block_text, extract_fill, field_runs, fill_reads,
                    find_slots, literal_values, mark_slots, nearest_p, paragraph_runs, reference_values,
                    rule_uses_literal, value_tokens)

WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
KEEP_NS = {NS["w"], NS["r"], "http://www.w3.org/XML/1998/namespace"}
FORBIDDEN = ("bookmarkStart", "commentRangeStart", "commentReference", "footnoteReference", "endnoteReference",
             "altChunk", "subDoc", "object", "numPr")
PIN_SLOTS = ("ascii", "hAnsi")


def _top_level(root) -> List[Tuple[int, Any]]:
    """(block index, w:p) for paragraphs not nested in another paragraph (textboxes are copied untouched)."""
    return [(i, p) for i, p in enumerate(paragraphs(root)) if nearest_p(p) is None]


def _in_table(p) -> bool:
    q = p.getparent()
    while q is not None and q.tag != W + "tbl":
        q = q.getparent()
    return q is not None


def strip_foreign(el, where: str):
    """Copy of a style element without extension markup: attributes outside w/r/xml namespaces and elements
    outside the w namespace go (lesson §6) — a hard stop if a dropped element carries document text."""
    c = etree.fromstring(etree.tostring(el))
    for e in list(c.iter()):
        if not isinstance(e.tag, str):
            continue
        for a in list(e.attrib):
            if a.startswith("{") and a[1:].split("}")[0] not in KEEP_NS:
                del e.attrib[a]
    for e in list(c.iter()):
        if isinstance(e.tag, str) and e.tag.startswith("{") and not e.tag.startswith(W) and e.getparent() is not None:
            if any(t.text for t in e.iter(W + "t")):
                raise JobException(f"{where}: an extension element {etree.QName(e).localname} carries text", "structure")
            e.getparent().remove(e)
    return c


def _diff(desired: Dict[str, Any], actual: Dict[str, Any], keys) -> Dict[str, Any]:
    return {k: desired[k] for k in keys if desired[k] != actual[k]}


PARA_KEYS = ("before", "after", "line", "lineRule", "left", "right", "first", "jc", "tabs")
RUN_KEYS = ("ascii", "hAnsi", "sz", "color", "b", "i", "caps", "smallCaps")
SLOT_DECORATIONS = ("color", "highlight", "shd")


def _slot_appearance(p, slots, slot, resolver):
    """Nearest run with non-slot wording supplies decoration; otherwise inherit the paragraph."""
    runs = paragraph_runs(p)
    _, segments = display_segments(p)
    start_run = next(r for _, r, a, b in segments if a <= slot.start < b)
    candidates = []
    for i, run in enumerate(runs):
        outside = "".join((t.text or "")[j - a] for t, r, a, b in segments if r is run
                          for j in range(a, b) if not any(s.start <= j < s.end for s in slots))
        if outside.strip():
            candidates.append((abs(i - runs.index(start_run)), i, run))
    donor = min(candidates, key=lambda item: item[:2])[2] if candidates else None
    rpr = etree.fromstring(etree.tostring(start_run.find(W + "rPr"))) if start_run.find(W + "rPr") is not None else etree.Element(W + "rPr")
    for name in SLOT_DECORATIONS:
        for child in rpr.findall(W + name):
            rpr.remove(child)
        value = donor.find(f"{W}rPr/{W}{name}") if donor is not None else None
        if value is not None:
            rpr.append(etree.fromstring(etree.tostring(value)))
    colour = resolver.run(donor.find(W + "rPr"), p)["color"] if donor is not None else resolver.run(rpr, p)["color"]
    return {"color": list(colour), "elements": [etree.tostring(e).decode() for e in rpr if e.tag in (W + "highlight", W + "shd")]}


def pin_paragraph(p, pins: Dict[str, Any]) -> None:
    ppr = paragraph_ppr(p)
    for k in ("before", "after", "line", "lineRule"):
        if k in pins:
            get_or_add(ppr, "spacing", PPR_ORDER).set(W + k, pins[k])
    if any(k in pins for k in ("left", "right", "first")):
        ind = get_or_add(ppr, "ind", PPR_ORDER)
        for k in ("left", "right"):
            if k in pins:
                ind.attrib.pop(W + {"left": "start", "right": "end"}[k], None)
                ind.set(W + k, pins[k])
        if "first" in pins:
            for a in ("firstLine", "hanging", "firstLineChars", "hangingChars"):
                ind.attrib.pop(W + a, None)
            v = int(pins["first"])
            ind.set(W + ("firstLine" if v >= 0 else "hanging"), str(abs(v)))
    if "jc" in pins:
        get_or_add(ppr, "jc", PPR_ORDER).set(W + "val", pins["jc"])
    if "tabs" in pins:
        old = ppr.find(W + "tabs")
        if old is not None:
            ppr.remove(old)
        entries = [(pos, val, leader) for pos, val, leader in pins["tabs"]]
        entries += [(pos, "clear", None) for pos in pins.get("tabs_clear", [])]
        if entries:
            tabs = insert_in_order(ppr, etree.Element(W + "tabs"), PPR_ORDER)
            for pos, val, leader in sorted(entries, key=lambda e: e[0]):
                t = etree.SubElement(tabs, W + "tab")
                t.set(W + "val", val)
                if leader:
                    t.set(W + "leader", leader)
                t.set(W + "pos", str(pos))


def pin_rpr(rpr, pins: Dict[str, Any]) -> None:
    fonts = {s: pins[s] for s in PIN_SLOTS if s in pins}
    if fonts:
        set_rfonts(rpr, fonts, clear_theme=True)
    if "sz" in pins:
        get_or_add(rpr, "sz", RPR_ORDER).set(W + "val", pins["sz"])
    if "color" in pins:
        c = get_or_add(rpr, "color", RPR_ORDER)
        for a in list(c.attrib):
            del c.attrib[a]
        for name, v in zip(("val", "themeColor", "themeShade", "themeTint"), pins["color"]):
            if v is not None:
                c.set(W + name, v)
    for t in ("b", "i", "caps", "smallCaps"):
        if t in pins:
            e = get_or_add(rpr, t, RPR_ORDER)
            e.attrib.pop(W + "val", None)
            if not pins[t]:
                e.set(W + "val", "0")


def _json_pins(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: (list(map(list, v)) if k == "tabs" else (list(v) if k == "color" else v)) for k, v in d.items()}


def _unjson_pins(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: ([tuple(x) for x in v] if k == "tabs" else (tuple(v) if k == "color" else v)) for k, v in d.items()}


_SOURCE_TYPE_WORDS = {"default": "", "first": "first-page", "even": "even-page"}


def _source_words(rule: Dict[str, Any]) -> str:
    """Where a fill rule reads: ``in the document body``, ``in the first-page footer``, ``in the header of section 2``."""
    src = rule.get("source")
    section = rule.get("section")
    try:
        section_words = f"section {int(section) + 1}" if section is not None else ""
    except (TypeError, ValueError):
        section_words = ""
    if src == "document":
        return f"in the body of {section_words}" if section_words else "in the document body"
    if isinstance(src, str) and src:
        kind, _, typ = src.partition(".")
        adjective = _SOURCE_TYPE_WORDS.get(typ or "default", typ)
        words = "in the " + (adjective + " " if adjective else "") + kind
        return words + (f" of {section_words}" if section_words else "")
    return f"in {section_words}" if section_words else ""


PATTERN_QUOTE_MAX = 32


def _pattern_words(pattern: Any, lead: str = "the pattern ") -> str:
    """A short pattern is quoted; a long one (a section-number regex runs to sixty characters) reads as "a
    pattern": the sentence is for the user, and every setting is shown on demand beside it."""
    pattern = str(pattern or "")
    if len(pattern) <= PATTERN_QUOTE_MAX:
        return lead + prose.quote(pattern)
    return "a pattern"


def fill_rule_words(rule: Any) -> str:
    """A fill rule as a phrase a person reads: what is taken and from where. The shared vocabulary in ops/prose.py has
    no helper for fill rules, so it lives here; ensure_end_of_section borrows it for the section number."""
    if not isinstance(rule, dict):
        return "a value taken from the document itself"
    kind = rule.get("rule")
    if kind == "first_match" or ("rules" in rule and kind is None):
        subs = [fill_rule_words(r) for r in (rule.get("rules") or []) if isinstance(r, dict)]
        words = ("the first of these that is found: " + prose.join(subs, "or")) if subs else \
            "the first of the listed rules that is found"
    elif kind == "literal":
        words = "the approved wording " + prose.quote(rule.get("value", ""))
    elif kind in ("regex", "paragraph_after"):
        qualifiers: List[str] = []
        if rule.get("occurrence") == "first":
            qualifiers.append("the first match")
        if rule.get("case_insensitive"):
            qualifiers.append("ignoring case")
        if rule.get("flexible_whitespace") is False:
            qualifiers.append("with exact spacing")
        source = _source_words(rule)
        tail = (" " + source if source else "") + (f" ({', '.join(qualifiers)})" if qualifiers else "")
        if kind == "regex":
            words = "the text matching " + _pattern_words(rule.get("pattern", "")) + tail
        else:
            words = "the paragraph after the one matching " + _pattern_words(rule.get("anchor_regex", ""), "") + tail
    else:
        words = "a value taken from the document itself"
    if rule.get("in_template"):
        words += ", where the template shows " + prose.quote(rule["in_template"])
    return words


def _kind_words(kinds: Any) -> str:
    chosen = [k for k in (kinds if isinstance(kinds, (list, tuple)) else []) if k in KINDS] or list(KINDS)
    return prose.join(k + "s" for k in chosen)


@register
class ApplyHeaderFooterTemplate(Op):
    name = "apply_header_footer_template"
    mode = "formatting"
    description = ("Give every section the headers/footers of a project template: template parts copied, slots "
                   "(yellow highlight / {{NAME}}) filled from each document's own content by rule, tab stops "
                   "rescaled, rendering properties pinned, localized styles reconciled automatically, missing "
                   "styles imported, old parts removed.")
    params_schema = {"type": "object", "properties": {
        "template": {"type": "string", "description": "path of the template .docx (relative paths: the spec's folder)"},
        "template_sections": {"type": "object", "description": "{\"cover\": i, \"body\": j} or {\"all\": i}"},
        "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}, "default": list(KINDS)},
        "slot_fills": {"type": "object",
                       "description": "{slot name: fill rule}; a rule is {rule: 'literal', value} | {rule: 'regex', pattern, "
                                      "source?, section?, occurrence?, flexible_whitespace?} | {rule: 'paragraph_after', "
                                      "anchor_regex, source?, section?, occurrence?, flexible_whitespace?} | {rule: "
                                      "'first_match', rules: [...]} (tried in order; use it when the batch mixes layouts). "
                                      "Grammar and semantics: the catalog's fill_rules. Every slot of the template parts in "
                                      "use needs a rule; a rule naming no slot is refused. With slot_markers ['reference'] "
                                      "a rule may carry in_template: the template spec's own text it replaces (required "
                                      "for a literal)."},
        "slot_markers": {"type": "array", "items": {"type": "string", "enum": ["highlight", "braces", "reference"]},
                         "default": ["highlight", "braces"],
                         "description": "highlight/braces: a prepared template's placeholders. ['reference'] (alone): "
                                        "the template is an ordinary spec (e.g. the architect's); each fill rule is "
                                        "also run on the template itself (or names 'in_template' text) and every "
                                        "place its header/footer shows that value is a slot — the template's own "
                                        "section number/title become each spec's own"},
        "highlight_colors": {"type": "array", "items": {"type": "string"}, "default": ["yellow"]},
        "slot_formatting": {"type": "string", "enum": ["clear", "keep"], "default": "clear",
                            "description": "clear copies surrounding run colour/highlight/shading onto filled slots; keep preserves placeholder formatting"},
        "rescale_tabs": {"type": "boolean", "default": True},
        "align_header_footer_distance": {"type": "boolean", "default": True,
                                         "description": "set each section's pgMar header/footer to the template's"},
        "align_title_page": {"type": "boolean", "default": True,
                             "description": "set each section's titlePg to the template section's"},
        "import_missing_styles": {"type": "boolean", "default": True,
                                  "description": "Automatically reuse equivalent styles across Word languages; "
                                                 "import only genuinely missing dependencies. False still allows "
                                                 "reusing equivalent existing styles, but disallows new definitions."},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}},
        "required": ["template", "template_sections", "slot_fills"]}
    select_schema = {"type": "object", "properties": {"sections": {"enum": ["all"], "description": "'all' only"}}}
    verification_profile = {
        "V1": "members removed (old parts, rels, orphaned media) and added (new parts, rels, media) exact",
        "V2": "old parts' blocks deleted, new parts' blocks inserted with the filled text",
        "V5a": "added parts validated against the template's own baseline",
        "V10": "hf_template_refs, template_parts (slots, fills re-derived, effective properties, skeleton), styles_added",
        "V7": "template reference: header/footer anchor words vs the template render",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        kind_words = _kind_words(params.get("kinds"))
        template = params.get("template")
        head = (f"Give every section the {kind_words} of "
                + (f"the template {prose.quote(Path(template).name)}" if template else "the project template"))
        ts = params.get("template_sections") if isinstance(params.get("template_sections"), dict) else {}
        uses: List[str] = []
        if isinstance(ts.get("all"), int):
            uses.append(f"its section {ts['all'] + 1} throughout")
        else:
            if isinstance(ts.get("cover"), int):
                uses.append(f"its section {ts['cover'] + 1} for the cover")
            if isinstance(ts.get("body"), int):
                uses.append(("its " if not uses else "") + f"section {ts['body'] + 1} for the body")
        if uses:
            head += f" ({', '.join(uses)})"
        markers = list(params.get("slot_markers") or ["highlight", "braces"])
        colors = [str(c) for c in (params.get("highlight_colors") or ["yellow"])]
        if markers == ["reference"]:
            what = "with every place that shows the template's own values filled from the document itself"
        else:
            forms: List[str] = []
            if "highlight" in markers:
                forms.append("text highlighted in " + prose.join(colors, "or"))
            if "braces" in markers:
                forms.append(prose.quote("{{NAME}}"))
            what = "with each placeholder" + (f" ({prose.join(forms, 'or')})" if forms else "") + \
                " filled from the document itself"
        fills: Dict[str, Any] = {}
        for key in ("slot_fills", "slot_bindings"):
            if isinstance(params.get(key), dict):
                fills.update(params[key])
        items: List[str] = []
        for name, rule in fills.items():
            if isinstance(rule, dict) and rule.get("rule") == "literal":
                items.append(f"{prose.quote(name)} set to {fill_rule_words(rule)}")
            else:
                items.append(f"{prose.quote(name)} from {fill_rule_words(rule)}")
        if len(items) > 4:
            items = items[:3] + [f"{len(items) - 3} more"]
        if items:
            what += ": " + prose.join(items)
        extras: List[str] = []
        if params.get("slot_formatting") == "keep":
            extras.append("placeholder formatting is kept")
        if params.get("rescale_tabs") is False:
            extras.append("tab stops are not rescaled")
        if params.get("align_header_footer_distance") is False:
            extras.append("header and footer distances are left as they are")
        if params.get("align_title_page") is False:
            extras.append("first-page settings are left as they are")
        if params.get("import_missing_styles") is False:
            extras.append("no new styles are added")
        second = f"The old {kind_words} are removed; " + prose.join(["the new ones keep the template's look"] + extras)
        return prose.sentence(head + ", " + what) + " " + prose.sentence(second)

    # ------------------------------------------------------------------------------------------------
    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if select.get("sections", "all") != "all" or set(select) - {"sections"}:
            raise JobException("apply_header_footer_template applies to all sections (select {\"sections\": \"all\"})",
                               "bad-params")
        tpath = params.get("template")
        if not tpath or not Path(tpath).is_file():
            raise JobException(f"template file not found: {tpath!r}", "bad-params")
        tdata = Path(tpath).read_bytes()
        tpkg = DocxPackage(tdata, name=str(tpath))
        tmd5 = hashlib.md5(tdata).hexdigest()
        kinds = list(params.get("kinds", KINDS))
        markers = list(params.get("slot_markers", ["highlight", "braces"]))
        colors = list(params.get("highlight_colors", ["yellow"]))
        rescale = bool(params.get("rescale_tabs", True))
        align_dist = bool(params.get("align_header_footer_distance", True))
        align_title = bool(params.get("align_title_page", True))
        import_missing = bool(params.get("import_missing_styles", True))
        fills_spec = params.get("slot_fills") or {}
        binding_rules = params.get("slot_bindings") or {}
        if params.get("slot_formatting", "clear") not in ("clear", "keep"):
            raise JobException("slot_formatting must be 'clear' or 'keep'", "bad-params")
        if not isinstance(binding_rules, dict):
            raise JobException("slot_bindings must map slot names to source-bound literals", "bad-params")
        effective_fills = {**fills_spec, **binding_rules}
        reference = markers == ["reference"]
        if not kinds or set(kinds) - set(KINDS) or not markers or \
                (not reference and set(markers) - {"highlight", "braces"}):
            raise JobException("kinds ⊆ header/footer, and slot_markers ⊆ highlight/braces or exactly ['reference']",
                               "bad-params")
        if not reference and any(isinstance(r, dict) and "in_template" in r for r in fills_spec.values()):
            raise JobException("'in_template' belongs to slot_markers ['reference'] (a template spec); a prepared "
                               "template's slots are its highlighted / {{NAME}} placeholders", "bad-params")
        ref_values = reference_values(tpkg, fills_spec) if reference else None

        tsect = DocxPackage.sect_prs(tpkg.xml(tpkg.main_part()))
        main = pkg.main_part()
        root = pkg.xml(main)
        secs = DocxPackage.sect_prs(root)
        ts = params.get("template_sections") or {}
        if set(ts) not in ({"all"}, {"cover", "body"}, {"cover"}) or not all(isinstance(v, int) for v in ts.values()):
            raise JobException("template_sections must be {\"cover\": i, \"body\": j} or {\"all\": i}", "bad-params")
        if "body" not in ts and "all" not in ts and len(secs) > 1:
            raise JobException("the document has body sections but template_sections names no 'body'", "bad-params")
        bad = [v for v in ts.values() if not 0 <= v < len(tsect)]
        if bad:
            raise JobException(f"template has {len(tsect)} section(s); no {bad}", "bad-params")
        tmap = [ts["all"] if "all" in ts else (ts["cover"] if i == 0 else ts["body"]) for i in range(len(secs))]
        t_even = tpkg.settings_flag("evenAndOddHeaders")
        if t_even != pkg.settings_flag("evenAndOddHeaders"):
            raise JobException(f"evenAndOddHeaders is {'on' if t_even else 'off'} in the template and "
                               f"{'off' if t_even else 'on'} in this document; it is a document-wide setting — "
                               f"not changed silently", "even-odd")
        t_title = [sp.find(W + "titlePg") is not None and sp.find(W + "titlePg").get(W + "val") not in ("0", "false", "off")
                   for sp in tsect]
        title = [sp.find(W + "titlePg") is not None and sp.find(W + "titlePg").get(W + "val") not in ("0", "false", "off")
                 for sp in secs]
        t_eff = effective_refs(tpkg, tsect)

        # ---- which template part each target section gets, and the copies needed ----
        copies: List[Dict[str, Any]] = []
        copy_of: Dict[Tuple[str, Optional[int], Optional[int]], int] = {}
        new_refs: Dict[int, Dict[str, Dict[str, int]]] = {}
        for i in range(len(secs)):
            j = tmap[i]
            w_tpl, w_tgt = text_width(tsect[j]), text_width(secs[i])
            for kind in kinds:
                for typ in TYPES:
                    if (typ == "first" and not t_title[j]) or (typ == "even" and not t_even):
                        continue
                    tp = t_eff[j].get((kind, typ))
                    if tp is None:
                        continue
                    key = (tp, w_tpl, w_tgt if rescale else w_tpl)
                    if key not in copy_of:
                        copy_of[key] = len(copies)
                        copies.append({"template_part": tp, "kind": kind, "template_section": j,
                                       "template_width": w_tpl, "target_width": key[2], "target_sections": []})
                    c = copies[copy_of[key]]
                    if i not in c["target_sections"]:
                        c["target_sections"].append(i)
                    new_refs.setdefault(i, {}).setdefault(kind, {})[typ] = copy_of[key]

        # ---- template parts: structure, slots ----
        slots_by_copy: List[Dict[int, list]] = []
        all_slot_names: set = set()
        shown_in: List[str] = []                 # reference slots: each paragraph holding one, slots in ⟦ ⟧
        case_noted: set = set()
        frag_flags: List[str] = []
        for c in copies:
            tp = c["template_part"]
            troot = tpkg.xml(tp)
            if count_revisions(troot):
                raise JobException(f"template part {tp} carries tracked revisions {count_revisions(troot)}; accept or "
                                   f"reject them in the template first", "template-revisions", {"part": tp})
            for name in FORBIDDEN:
                if troot.find(f".//{W}{name}") is not None:
                    raise JobException(f"template part {tp} contains w:{name}; not handled by the templating op",
                                       "structure", {"part": tp, "element": name})
            for sdt in troot.iter(W + "sdt"):
                if sdt.find(f"{W}sdtPr/{W}dataBinding") is not None:
                    raise JobException(f"template part {tp} has a data-bound content control", "structure")
            check_part_relationships(tpkg, tp)
            in_field = field_runs(troot)          # every marker: a slot a field shows is refused (slot-in-field)
            per_p: Dict[int, list] = {}
            for bi, p in enumerate(paragraphs(troot)):
                where = f"template {tp} paragraph {bi}"
                sl = find_slots(p, markers, colors, where, ref_values=ref_values, in_field=in_field)
                if sl and nearest_p(p) is not None:
                    raise JobException(f"template {tp}: slot {sl[0].name!r} is inside a textbox; not handled",
                                       "slot-in-textbox", {"part": tp})
                if sl:
                    per_p[bi] = sl
                    all_slot_names |= {s.name for s in sl}
                    if reference:
                        shown_in.append(f"{tp}: «{mark_slots(p, sl)}»")
                        for s_ in sl:
                            if s_.placeholder != ref_values[s_.name] and s_.name not in case_noted:
                                case_noted.add(s_.name)
                                frag_flags.append(f"the template spec shows its own {s_.name} as {s_.placeholder!r} "
                                                  f"(its value: {ref_values[s_.name]!r}); each spec's own value is "
                                                  f"placed there as that spec writes it")
            slots_by_copy.append(per_p)
        unfilled = sorted(all_slot_names - set(effective_fills))
        if unfilled:
            raise JobException(f"template slot(s) {unfilled} have no fill rule", "unfilled-slot", {"slots": unfilled})
        # A rule is "unknown" only when it names a slot in NONE of the template sections the spec maps — not merely
        # none this document happens to use: a single-section spec in a batch mapped to a cover+body template uses
        # the cover's parts only, and the body's SECTION_NUMBER / SECTION_TITLE rules are still the batch's rules.
        known = set(all_slot_names)
        copied = {c["template_part"] for c in copies}
        for j in sorted(set(ts.values())):
            for kind in kinds:
                for typ in TYPES:
                    if (typ == "first" and not t_title[j]) or (typ == "even" and not t_even):
                        continue
                    tp = t_eff[j].get((kind, typ))
                    if tp is None or tp in copied:
                        continue
                    other_root = tpkg.xml(tp)
                    other_fields = field_runs(other_root)
                    for bi, p in enumerate(paragraphs(other_root)):
                        try:
                            known |= {s.name for s in find_slots(p, markers, colors, f"template {tp} paragraph {bi}",
                                                                 ref_values=ref_values, in_field=other_fields)}
                        except JobException:
                            continue            # a part this document does not use; its own refusals are not ours
        unknown = sorted(set(effective_fills) - known)
        if unknown and reference:
            raise JobException("the template spec's own " + ", ".join(f"{n} {ref_values[n]!r}" for n in unknown)
                               + " appear(s) in none of the headers/footers of the template sections the spec uses; "
                               "drop the rule(s) or fix them",
                               "unknown-slot", {"fills": unknown, "values": {n: ref_values.get(n) for n in unknown}})
        if unknown:
            raise JobException(f"fill rule(s) for {unknown} name no slot in the headers/footers of the template "
                               f"sections the spec uses", "unknown-slot", {"fills": unknown, "slots": sorted(known)})
        fills = {name: extract_fill(pkg, name, rule) for name, rule in effective_fills.items()}
        literal = sorted(n for n, r in effective_fills.items() if rule_uses_literal(r))

        # ---- styles ----
        tsheet, sheet = StyleSheet(tpkg), StyleSheet(pkg)
        style_parts = sorted({c["template_part"] for c in copies})
        style_map, imported = reconcile_styles(pkg, tpkg, style_parts, import_missing, strip_foreign)
        implicit_defaults = {kind: style_map[sid] for kind, sid in tsheet.defaults.items()
                             if style_map.get(sid) in imported}

        # ---- pins (template-effective vs target-effective) ----
        tres = Resolver(tsheet, theme_fonts(tpkg))
        ores = Resolver(sheet, theme_fonts(pkg), extra_styles=imported)
        n_pins = 0
        for c in copies:
            troot = tpkg.xml(c["template_part"])
            mapped = etree.fromstring(etree.tostring(troot))
            remap_references(mapped, style_map, implicit_defaults)
            mapped_pars = dict(_top_level(mapped))
            pins: Dict[str, Any] = {}
            try:
                for bi, p in _top_level(troot):
                    entry: Dict[str, Any] = {}
                    destination_p = mapped_pars[bi]
                    T, O = tres.paragraph(p), ores.paragraph(destination_p)
                    if rescale and not _in_table(p):
                        T = dict(T, tabs=rescale_tabs(T["tabs"], c["template_width"], c["target_width"]))
                    d = _diff(T, O, PARA_KEYS)
                    if "tabs" in d:
                        style_tabs = ores.paragraph(destination_p, direct=False)["tabs"]
                        wanted = {pos for pos, _, _ in T["tabs"]}
                        d["tabs_clear"] = sorted({pos for pos, _, _ in style_tabs} - wanted)
                    if d:
                        entry["ppr"] = _json_pins(d)
                    mr = p.find(f"{W}pPr/{W}rPr")
                    d = _diff(tres.run(mr, p), ores.run(destination_p.find(f"{W}pPr/{W}rPr"), destination_p), RUN_KEYS)
                    if d:
                        entry["mark"] = _json_pins(d)
                    runs = {}
                    destination_runs = paragraph_runs(destination_p)
                    for ri, r in enumerate(paragraph_runs(p)):
                        d = _diff(tres.run(r.find(W + "rPr"), p), ores.run(destination_runs[ri].find(W + "rPr"), destination_p), RUN_KEYS)
                        if d:
                            runs[str(ri)] = _json_pins(d)
                    if runs:
                        entry["runs"] = runs
                    if entry:
                        pins[str(bi)] = entry
                        n_pins += len(entry.get("ppr", {})) + len(entry.get("mark", {})) + sum(len(v) for v in runs.values())
            except AmbiguousToggle as e:
                raise JobException(f"template part {c['template_part']}: {e}", "toggle-ambiguous")
            c["pins"] = pins

        # ---- names: parts, media, docPr ids ----
        taken = set(pkg.source_names)
        def fresh(fmt: str) -> str:
            k = 1
            while fmt.format(k) in taken:
                k += 1
            taken.add(fmt.format(k))
            return fmt.format(k)
        docpr_max = 0
        for part in pkg.text_parts():
            for e in pkg.xml(part).iter(WP + "docPr"):
                try:
                    docpr_max = max(docpr_max, int(e.get("id")))
                except (TypeError, ValueError):
                    pass
        media_map: Dict[str, str] = {}
        t_defaults, t_overrides = tpkg.content_types()
        members_added: List[str] = []
        frag = self.fragment(select, params, mode="untracked" if literal else "formatting")
        next_docpr = docpr_max + 1
        for k, c in enumerate(copies):
            tp = c["template_part"]
            c["part"] = fresh("word/" + c["kind"] + "{}.xml")
            members_added.append(c["part"])
            trels = tpkg.rels(tp)
            c["rels"] = {}
            c["media"] = []
            if trels:
                members_added.append(pkg.rels_path_for(c["part"]))
                for rid, r in trels.items():
                    if r.type in MEDIA_REL_TYPES and r.resolved:
                        if r.resolved not in media_map:
                            ext = posixpath.splitext(r.resolved)[1]
                            media_map[r.resolved] = fresh("word/media/image{}" + ext)
                            c["media"].append(r.resolved)
                            members_added.append(media_map[r.resolved])
                        c["rels"][rid] = posixpath.relpath(media_map[r.resolved], posixpath.dirname(c["part"]))
            troot = tpkg.xml(tp)
            n_docpr = sum(1 for _ in troot.iter(WP + "docPr"))
            c["docpr_start"] = next_docpr
            next_docpr += n_docpr
            # predicted blocks of the new part
            for bi, p in enumerate(paragraphs(troot)):
                sl = slots_by_copy[k].get(bi)
                after = expected_block_text(p, sl, fills) if sl else block_full_text(p)
                frag.diffs.append(BlockDiff(part=c["part"], block_index=bi, before=None, after=after,
                                            note=f"from template {tp}" + (f"; slots {[s.name for s in sl]}" if sl else "")))
            c["slots"] = {str(bi): [{"name": s.name, "marker": s.marker, "start": s.start, "end": s.end}
                                    for s in sl] for bi, sl in slots_by_copy[k].items()}
            if params.get("slot_formatting", "clear") == "clear":
                for bi, sl in slots_by_copy[k].items():
                    for entry, slot in zip(c["slots"][str(bi)], sl):
                        entry["appearance"] = _slot_appearance(paragraphs(troot)[bi], sl, slot, tres)
            for tbl in troot.iter(W + "tbl"):
                g = tbl.find(W + "tblGrid")
                width = sum(int(gc.get(W + "w") or 0) for gc in (g.findall(W + "gridCol") if g is not None else []))
                if c["target_width"] and width > c["target_width"] and nearest_p(tbl) is None:
                    frag.flags.append(f"{c['part']} (from {tp}): a table {width} twips wide is wider than the target "
                                      f"text width {c['target_width']}; table widths are not rescaled")

        # ---- the old parts go ----
        removed_refs, kept_rids = [], []
        for i, sp in enumerate(secs):
            for kind, typ, rid, _ in refs(sp):
                if kind in kinds:
                    removed_refs.append((i, kind, typ, rid))
                else:
                    kept_rids.append(rid)
        rp = plan_removal(pkg, main, root, removed_refs, kept_rids)

        # ---- section property alignment ----
        title_changes = {i: t_title[tmap[i]] for i in range(len(secs)) if align_title and title[i] != t_title[tmap[i]]}
        if not align_title and any(title[i] != t_title[tmap[i]] for i in range(len(secs))):
            raise JobException("titlePg differs from the template section's and align_title_page is off", "title-page")
        dist_changes: Dict[int, Dict[str, str]] = {}
        if align_dist:
            for i, sp in enumerate(secs):
                tm, m = tsect[tmap[i]].find(W + "pgMar"), sp.find(W + "pgMar")
                if tm is None or m is None:
                    continue
                d = {k: tm.get(W + k) for k in ("header", "footer") if tm.get(W + k) and tm.get(W + k) != m.get(W + k)}
                if d:
                    dist_changes[i] = d

        # ---- the fragment ----
        styles_part = sheet.part
        frag.members_removed = list(rp.members)
        frag.members_added = members_added
        frag.diffs = list(rp.diffs) + frag.diffs
        frag.untracked_diffs_expected = True
        frag.rev_delta = {"ins": 0, "del": 0}
        for k_, v in rp.rev_gone.items():
            frag.rev_delta[k_] = -v
        frag.flags += rp.flags
        frag.intended_parts = [main, pkg.rels_path_for(main), CT_PART] + ([styles_part] if imported else [])
        frag.touches = [(main, f"sectPr:{i}", "hfref") for i in range(len(secs))]
        frag.touches += [(main, f"sectPr:{i}", "titlePg") for i in title_changes]
        frag.touches += [(main, f"sectPr:{i}", "pgMar") for i in dist_changes]
        frag.touches += [(m, "*", "remove") for m in frag.members_removed] + [(m, "*", "add") for m in members_added]
        frag.touches += [(part, ordinal, "verify:text") for rule in effective_fills.values()
                         for part, ordinal in fill_reads(pkg, rule)]
        frag.touches += [(pkg.rels_path_for(main), "*", "rels"), (CT_PART, "*", "ct")]
        frag.touches += [(styles_part, f"style:{sid}", "add") for sid in imported]
        # What the plan READS from the target (rule 9: a change of it by another op in this spec is a plan-time
        # conflict, never a V10 failure after apply): the pins are the diff of template-effective vs TARGET-effective
        # properties, resolved over the target's whole styles part (every chain, docDefaults) and theme; every copy's
        # tab rescale and the table-width flag read the section's text width (pgSz, pgMar), the distance alignment
        # its pgMar, and hf_template_refs predicts every section's titlePg. The op's own changes of these targets
        # (titlePg, pgMar where they follow the template; imported styles) are not conflicts with its own reads.
        frag.touches += [(main, f"sectPr:{i}", f"verify:{fam}") for i in range(len(secs))
                         for fam in ("pgSz", "pgMar", "titlePg")]
        for reference_part in reference_parts(pkg, sheet):
            if reference_part in pkg.text_parts():
                frag.touches.extend((reference_part, "*", "verify-props:" + family)
                                    for family in ("pStyle", "rStyle", "tblStyle"))
            else:
                frag.touches.append((reference_part, "*", "verify"))
        theme = theme_part(pkg)
        if theme is not None:
            frag.touches.append((theme, "*", "verify"))
        part_name = lambda idx: copies[idx]["part"]
        frag.property_assertions.append({
            "kind": "hf_template_refs", "kinds": kinds,
            "sections": {str(i): {kind: {typ: part_name(idx) for typ, idx in m.items()} for kind, m in kd.items()}
                         for i, kd in new_refs.items()},
            "title_pg": {str(i): (title_changes.get(i, title[i])) for i in range(len(secs))}})
        for c in copies:
            frag.property_assertions.append({
                "kind": "template_parts", "part": c["part"], "template": str(Path(tpath).resolve()),
                "template_md5": tmd5, "template_part": c["template_part"], "template_section": c["template_section"],
                "target_sections": c["target_sections"], "rescale_tabs": rescale, "slot_markers": markers,
                "highlight_colors": colors, "fills": effective_fills, "expected_fills": fills,
                "template_fill_rules": fills_spec, "slot_formatting": params.get("slot_formatting", "clear"),
                "style_map": style_map, "style_parts": style_parts, "import_missing_styles": import_missing,
                **({"template_values": ref_values} if reference else {})})
        if imported:
            frag.property_assertions.append({"kind": "styles_added", "styles_part": styles_part, "ids": list(imported),
                                             "template": str(Path(tpath).resolve()), "template_md5": tmd5,
                                             "style_map": style_map, "style_parts": style_parts,
                                             "import_missing_styles": import_missing})
        for i, d in dist_changes.items():
            frag.property_assertions.append({"kind": "section_props", "part": main, "sections": [i],
                                             "elements": {"pgMar": {"set": d}}, "expected_sections": 1})
        frag.template_baselines = {c["part"]: [str(Path(tpath).resolve()), c["template_part"]] for c in copies}
        frag.geometry_reference = "template"
        frag.geometry_template = str(Path(tpath).resolve())
        frag.transform_data = {
            "template": str(Path(tpath).resolve()), "template_md5": tmd5, "main": main, "kinds": kinds,
            "copies": [{k_: c[k_] for k_ in ("part", "kind", "template_part", "pins", "slots", "rels", "media",
                                              "docpr_start", "target_sections")} for c in copies],
            "media_map": media_map, "members_added": members_added,
            "new_refs": {str(i): {kind: {typ: part_name(idx) for typ, idx in m.items()} for kind, m in kd.items()}
                         for i, kd in new_refs.items()},
            "fills": fills, "title_changes": {str(i): v for i, v in title_changes.items()},
            "dist_changes": {str(i): d for i, d in dist_changes.items()},
            "style_map": style_map, "implicit_defaults": implicit_defaults, "styles_part": styles_part,
            "imported": {sid: etree.tostring(el).decode() for sid, el in imported.items()},
            "removal": {"rids": rp.gone_rids, "members": rp.members, "media": rp.media},
            "media_ct": {m: (t_overrides.get(m) or t_defaults.get(posixpath.splitext(m)[1].lstrip(".").lower()))
                         for m in media_map},
        }
        # ---- the record ----
        frag.notes.append(f"template {Path(tpath).name} (md5 {tmd5[:12]}…), sections mapped {tmap}; "
                          f"{len(copies)} part(s) copied: " + "; ".join(
                              f"{c['part']} ← {c['template_part']} for sections {c['target_sections']}"
                              + (f", tabs {c['template_width']}→{c['target_width']}" if c['template_width'] != c['target_width'] else "")
                              for c in copies))
        frag.notes.append("fills: " + "; ".join(f"{n} = {fills[n]!r} ({effective_fills[n].get('rule')})" for n in sorted(fills)))
        for name, binding in binding_rules.items():
            frag.notes.append(f"binding: {name} = {binding['value']!r} from {binding['part']} paragraph {binding['paragraph']} "
                              f"(source md5 {binding['source_md5']})")
        if reference:
            frag.notes.append("template spec (reference slots): its own " + ", ".join(
                f"{n} {ref_values[n]!r} → {fills[n]!r}" for n in sorted(ref_values)) + "; replaced at ⟦ ⟧ in "
                + "; ".join(dict.fromkeys(shown_in)) + "; everything else in its headers/footers is copied as it is")
            frag.flags.extend(frag_flags)
        frag.notes.append(f"{n_pins} property value(s) pinned so the copies render as in the template"
                          + (f"; styles imported: {list(imported)}" if imported else "; no styles imported"))
        if any(k != v for k, v in style_map.items()):
            frag.notes.append("template styles reconciled: " + "; ".join(
                f"{k} → {v}" for k, v in sorted(style_map.items()) if k != v))
        frag.notes.append(f"old parts removed: {rp.parts_gone or 'none'}; media removed: {rp.media or 'none'}")
        if title_changes:
            frag.notes.append(f"titlePg set to the template's on sections {sorted(title_changes)}")
        if dist_changes:
            frag.notes.append(f"header/footer distance set to the template's on sections {sorted(dist_changes)}")
        if literal:
            frag.flags.append(f"literal fill(s) {literal} are user-supplied words placed untracked: "
                              + "; ".join(f"{n} = {fills[n]!r}" for n in literal))
        frag.flags += self._left_elsewhere(pkg, fills, effective_fills, literal)
        frag.notes.append("the document's own body (revision-history table included) is untouched")
        return frag

    @staticmethod
    def _left_elsewhere(pkg: DocxPackage, fills: Dict[str, str], rules: Dict[str, Any], literal: List[str]) -> List[str]:
        """A literal fill of a reference slot (SECTION_NUMBER, SECTION_TITLE) whose words differ from the document's
        own, read by the reference rule: the headers/footers will say one thing and the document another
        (docs/PLAN-2026-10-22-batch-consistency.md C3). A flag only: nothing is predicted from the reading, and the
        document stays as it is. Letters compared casefolded — a difference of case alone is not this flag's."""
        out = []
        for name in literal:
            if name not in REFERENCE_FILLS or fills[name] not in literal_values(rules[name]):
                continue                                 # no reference rule, or a chain whose literal did not fill it
            try:
                own = extract_fill(pkg, name, REFERENCE_FILLS[name])
            except JobException:
                continue                                 # the document has no value of its own to compare
            if value_tokens(own) != value_tokens(fills[name]):
                what = name.lower().replace("_", " ")
                out.append(f"{LEFT_ELSEWHERE}the headers/footers get the words given for the {what}, “{fills[name]}”, "
                           f"while the document's own {what} reads “{own}”; this change leaves the document as it is.")
        return out

    # ------------------------------------------------------------------------------------------------
    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        tdata = Path(td["template"]).read_bytes()
        if hashlib.md5(tdata).hexdigest() != td["template_md5"]:
            raise JobException("the template file changed between plan and apply", "internal")
        tpkg = DocxPackage(tdata, name=td["template"])
        main = td["main"]
        payload: Dict[str, Tuple[bytes, Optional[str]]] = {}
        for c in td["copies"]:
            troot = tpkg.xml(c["template_part"])
            remap_references(troot, td.get("style_map", {}), td.get("implicit_defaults", {}))
            pars = paragraphs(troot)
            for bi, entry in c["pins"].items():
                p = pars[int(bi)]
                runs = paragraph_runs(p)
                if "ppr" in entry:
                    pin_paragraph(p, _unjson_pins(entry["ppr"]))
                if "mark" in entry:
                    pin_rpr(mark_rpr(p), _unjson_pins(entry["mark"]))
                for ri, pins in entry.get("runs", {}).items():
                    pin_rpr(run_rpr(runs[int(ri)]), _unjson_pins(pins))
            from .slots import Slot
            for bi, sl in c["slots"].items():
                p = pars[int(bi)]
                for s in sorted(sl, key=lambda s: -s["start"]):
                    filled = apply_slot(p, Slot(s["name"], s["marker"], s["start"], s["end"], ""), td["fills"][s["name"]], False)
                    if "appearance" in s:
                        rpr = run_rpr(filled)
                        for name in SLOT_DECORATIONS:
                            for child in rpr.findall(W + name):
                                rpr.remove(child)
                        for xml in s["appearance"]["elements"]:
                            insert_in_order(rpr, etree.fromstring(xml.encode()), RPR_ORDER)
                        pin_rpr(rpr, {"color": s["appearance"]["color"]})
            for k, e in enumerate(troot.iter(WP + "docPr")):
                e.set("id", str(c["docpr_start"] + k))
            payload[c["part"]] = (serialize_xml(troot), HEADER_CT if c["kind"] == "header" else FOOTER_CT)
            if c["rels"] or tpkg.has(tpkg.rels_path_for(c["template_part"])):
                rroot = tpkg.xml(tpkg.rels_path_for(c["template_part"]))
                for rel in rroot.findall(REL + "Relationship"):
                    if rel.get("Id") in c["rels"]:
                        rel.set("Target", c["rels"][rel.get("Id")])
                payload[pkg.rels_path_for(c["part"])] = (serialize_xml(rroot), None)
        for old, new in td["media_map"].items():
            payload[new] = (tpkg.read(old), td["media_ct"][old])
        for name in td["members_added"]:
            data, ct = payload[name]
            pkg.add_member(name, data, ct)
        # styles
        if td["imported"]:
            sroot = pkg.xml(td["styles_part"])
            for sid, xml in td["imported"].items():
                sroot.append(parse_xml(xml.encode()))
            pkg.write_xml(td["styles_part"], sroot)
        # relationships and section references
        rel_type = {"header": REL_TYPE_BASE + "header", "footer": REL_TYPE_BASE + "footer"}
        new_ids = pkg.edit_rels(main, add=[{"type": rel_type[c["kind"]], "target": posixpath.relpath(c["part"], posixpath.dirname(main))}
                                           for c in td["copies"]])
        rid_of = {c["part"]: rid for c, rid in zip(td["copies"], new_ids)}
        root = pkg.xml(main)
        secs = DocxPackage.sect_prs(root)
        for i, sp in enumerate(secs):
            for kind, typ, rid, el in refs(sp):
                if kind in td["kinds"]:
                    sp.remove(el)
            kd = td["new_refs"].get(str(i), {})
            for kind in ("footer", "header"):          # inserted first-in-order: headers end up before footers
                for typ in reversed(TYPES):
                    part = kd.get(kind, {}).get(typ)
                    if part is None:
                        continue
                    ref = etree.Element(W + ("headerReference" if kind == "header" else "footerReference"))
                    ref.set(W + "type", typ)
                    ref.set(R + "id", rid_of[part])
                    sp.insert(0, ref)
            if str(i) in td["title_changes"]:
                tp = sp.find(W + "titlePg")
                if td["title_changes"][str(i)]:
                    if tp is None:
                        insert_in_order(sp, etree.Element(W + "titlePg"), SECTPR_ORDER)
                    else:
                        tp.attrib.pop(W + "val", None)
                elif tp is not None:
                    sp.remove(tp)
            for k, v in td["dist_changes"].get(str(i), {}).items():
                sp.find(W + "pgMar").set(W + k, v)
        pkg.write_xml(main, root)
        rm = td["removal"]
        apply_removal(pkg, main, rm["rids"], rm["members"], rm["media"])
