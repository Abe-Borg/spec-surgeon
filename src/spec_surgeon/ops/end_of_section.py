"""Op: ensure_end_of_section — every file ends with "END OF SECTION xx xx xx", the number taken from the file itself
(design note §6).

Definition (CLAUDE.md "End of section", both sides):
* the **number** is a fill rule (CLAUDE.md "Fill rules": regex / paragraph_after / first_match over the body or a
  header/footer) run on the file — extracted, never typed or converted; the **expected line** is ``text`` (default
  ``END OF SECTION {number}``, ``{number}`` exactly once) with the number in place;
* the **last block** is the body's last child (before its final ``w:sectPr``) that is not an empty paragraph — a
  paragraph is empty when its run text (ops/textedit.py) is only spaces, tabs and line breaks and its mark carries no
  section break; a table or content control is a block;
* the last block is a paragraph matching ``detect_regex`` (default ``(?i)^\\s*END\\s+OF\\s+SECTION\\b``, searched in its
  run text): when its text from the first to the last non-space character is the expected line, nothing changes;
  otherwise that stretch is replaced by the expected line (an edit of the core);
* otherwise a paragraph of the main part that matches ``detect_regex`` sits before the end: refused (``eos-not-last``);
* otherwise, unless the last block is a paragraph carrying a section break (refused: ``section-break``), a new
  paragraph holding the expected line is inserted right after the last block — ``formatting: "style"``
  (default): its paragraph style is ``style`` (a style the file has) or none (the default paragraph style), no direct
  formatting; ``"copy"``: the paragraph properties of the one body paragraph matching ``copy_from_regex`` without its
  section break, numbering and pending revisions (insert_paragraph's inherit rule minus ``w:numPr``) and its first text
  run's properties without ``w:rPrChange``.
The verifier derives all of it again (verify/textops.py, kind ``end_of_section``).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..ooxml import (PPR_ORDER, RPR_ORDER, RevisionStamp, file_uses_date_utc, insert_in_order, mark_rpr,
                     max_revision_id, paragraph_ppr, paragraphs, set_text)
from ..package import DocxPackage, W
from . import prose, register, tables
from .base import BlockDiff, JobException, Op, PlanFragment
from .patterns import compile_pattern
from .apply_header_footer_template import fill_rule_words
from .insert_paragraph import inherited_ppr, inherited_rpr
from .replace_table import default_author, tracked_of
from .selectors import StyleSheet
from .slots import extract_fill, field_runs, fill_reads
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, plan_paragraph, shown

DEFAULT_TEXT = "END OF SECTION {number}"
DEFAULT_DETECT = r"(?i)^\s*END\s+OF\s+SECTION\b"
BLANK = " \t\n "


def is_empty_paragraph(el) -> bool:
    return (el.tag == W + "p" and el.find(f"{W}pPr/{W}sectPr") is None
            and not RunText(el).text.strip(BLANK))


def last_block(body) -> Optional[Any]:
    kids = [c for c in body if isinstance(c.tag, str) and c.tag != W + "sectPr"]
    for c in reversed(kids):
        if not is_empty_paragraph(c):
            return c
    return None


def expected_line(text: str, number: str) -> str:
    return text.replace("{number}", number)


def copied_ppr(anchor) -> Optional[Any]:
    ppr = inherited_ppr(anchor)
    if ppr is not None:
        for n in ppr.findall(W + "numPr"):
            ppr.remove(n)
        if not len(ppr) and not ppr.attrib:
            return None
    return ppr


@register
class EnsureEndOfSection(Op):
    name = "ensure_end_of_section"
    mode = "content"
    description = ("Make every file end with its own 'END OF SECTION <number>' line: the number is extracted from the file "
                   "by a fill rule (never typed); a last line that reads otherwise is replaced, a missing one is "
                   "inserted after the last content (with a style the file has, or the formatting of a paragraph you "
                   "name), and an END OF SECTION line with content after it sets the file aside. Tracked (default) or "
                   "untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "number": {"type": "object", "description": "a fill rule finding the section number in the file itself "
                                                        "(regex / paragraph_after / first_match; see fill_rules)"},
            "text": {"type": "string", "default": DEFAULT_TEXT,
                     "description": "the line as the user wrote it, with {number} once"},
            "detect_regex": {"type": "string", "default": DEFAULT_DETECT,
                             "description": "what an existing end line looks like (any wording/number)"},
            "formatting": {"type": "string", "enum": ["style", "copy"], "default": "style"},
            "style": {"type": "string", "description": "formatting 'style': the new line's paragraph style id"},
            "copy_from_regex": {"type": "string",
                                "description": "formatting 'copy': the one body paragraph whose formatting the new line "
                                               "copies (e.g. the SECTION title line)"},
            "tracked": {"type": "boolean", "default": True},
            "author": {"type": "string"},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["number"],
    }
    select_schema = {"type": "object", "description": "none — the op looks at the end of the body"}
    verification_profile = {
        "V2": "insert: one inserted block; replace: one changed block; none: nothing",
        "V4": "tracked: insert +1 ins +1 ins_mark; replace: +1 del per run, +1 ins",
        "V10": "end_of_section re-extracts the number, re-finds the last block and the case, and checks the new or "
               "replaced line (its text, formatting and revision marks)",
    }

    def describe(self, select: Dict[str, Any], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        text = params.get("text") if isinstance(params.get("text"), str) and params.get("text") else DEFAULT_TEXT
        rule = params.get("number")
        if isinstance(rule, dict) and rule.get("rule") == "literal":
            number = "its number given as " + prose.quote(rule.get("value", ""))
        elif isinstance(rule, dict):
            number = "its number taken from the document itself — " + fill_rule_words(rule)
        else:
            number = "its number taken from the document itself"
        first = prose.sentence("Make sure the document ends with the line", prose.quote(text) + ",", number)
        detect = params.get("detect_regex")
        recognised = (" (recognised by the pattern " + prose.quote(detect) + ")") \
            if isinstance(detect, str) and detect and detect != DEFAULT_DETECT else ""
        if params.get("formatting") == "copy":
            look = "formatted like the paragraph matching " + prose.quote(params.get("copy_from_regex", ""))
        elif params.get("style"):
            look = "in " + prose.style(params["style"])
        else:
            look = "in the default paragraph style"
        if params.get("tracked", True) is False:
            tail = "without tracked changes"
        else:
            tail = "shown as a tracked change" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
        second = prose.sentence("An existing end line" + recognised + " that reads otherwise is corrected and a missing "
                                "one is added after the last content", look + ",",
                                tail + "; a document with content after its end line is set aside")
        return first + " " + second

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("ensure_end_of_section takes no selector", "bad-params")
        text = params.get("text", DEFAULT_TEXT)
        if not isinstance(text, str) or text.count("{number}") != 1:
            raise JobException("'text' must hold {number} exactly once", "bad-params")
        if any(ord(c) < 32 for c in text):
            raise JobException("'text' holds a control character", "bad-params")
        try:
            detect = compile_pattern(params.get("detect_regex", DEFAULT_DETECT), where="a body paragraph's text")
        except JobException as e:
            raise JobException(f"bad detect_regex ({e.reason})", e.code, e.details) from None
        formatting = params.get("formatting", "style")
        if formatting not in ("style", "copy"):
            raise JobException("formatting must be 'style' or 'copy'", "bad-params")
        if formatting == "copy" and not params.get("copy_from_regex"):
            raise JobException("formatting 'copy' needs copy_from_regex", "bad-params")
        tracked = tracked_of(params)
        author = params.get("author") or default_author()
        rule = params.get("number")
        if not isinstance(rule, dict):
            raise JobException("'number' must be a fill rule object", "bad-params")
        number = tables.placeable(extract_fill(pkg, "number", rule), "the section number")
        line = expected_line(text, number)
        main = pkg.main_part()
        root = pkg.xml(main)
        body = root.find(W + "body")
        frag = self.fragment({}, {**params, "tracked": tracked, "author": author, "text": text},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        # what the prediction READS (rule 9): the paragraphs the number comes from, and the end of the body — another
        # op changing that text, or adding a paragraph after the line, would reach this plan unverified
        frag.touches += [(part, str(bi), "verify:text") for part, bi in fill_reads(pkg, rule)]
        frag.touches.append((main, "end", "verify"))
        pars = paragraphs(root)
        last = last_block(body)
        if last is None:
            raise JobException("the body is empty", "not-found")
        case, at = "insert", None
        if last.tag == W + "p" and detect.search(RunText(last).text):
            rt = RunText(last)
            stripped = rt.text.strip(BLANK)
            lo = len(rt.text) - len(rt.text.lstrip(BLANK))
            at = pars.index(last)
            if stripped == line:
                case = "none"
            else:
                case = "replace"
                done = plan_paragraph(frag, main, at, last, [Edit(lo, lo + len(stripped), line)], tracked,
                                      field_runs(root), f"{main} paragraph {at}", note="end line corrected")
                frag.transform_data = {"case": case, "edits": {main: {str(at): edits_to_data(done)}}}
        else:
            early = [i for i, p in enumerate(pars) if detect.search(RunText(p).text)]
            if early:
                raise JobException(f"an END OF SECTION line ({shown(RunText(pars[early[0]]).text)!r}) sits before other "
                                   f"content; move or delete what follows it in Word", "eos-not-last",
                                   {"paragraph": early[0]})
        style = None
        if case == "insert":
            if last.tag == W + "p" and last.find(f"{W}pPr/{W}sectPr") is not None:
                raise JobException("the last content ends a section of its own (its paragraph carries a section break); "
                                   "where the end line belongs is not clear", "section-break")
            sheet = StyleSheet(pkg)
            if formatting == "style":
                style = params.get("style")
                if style is not None and style not in sheet.styles:
                    raise JobException(f"this file has no style {style!r} for the new line", "missing-style",
                                       {"style": style})
                if style is not None:                       # the new line names it: removing it beside is a conflict
                    frag.touches.append((sheet.part, f"style:{style}", "verify"))
            else:
                rx = compile_pattern(params["copy_from_regex"], where="a body paragraph's text")
                hits = [i for i, p in enumerate(pars) if p.getparent() is body and rx.search(RunText(p).text)]
                if len(hits) != 1:
                    raise JobException(f"copy_from_regex matches {len(hits)} body paragraph(s); exactly one needed",
                                       "ambiguous" if hits else "not-found", {"matches": hits})
                at = hits[0]
                frag.touches.append((main, str(at), "verify"))      # its paragraph and run properties are copied
            inner = [p for p in ([last] if last.tag == W + "p" else list(last.iter(W + "p")))]
            out_index = (pars.index(inner[-1]) + 1) if inner else sum(
                1 for p in pars if p.getparent() is not None and _before(p, last, body))
            frag.diffs.append(BlockDiff(part=main, block_index=out_index, before=None, after=line,
                                        accepted_view=line if tracked else None, note="END OF SECTION line inserted"))
            if tracked:
                frag.rev_delta["ins"] = frag.rev_delta.get("ins", 0) + 1
                frag.rev_delta["ins_mark"] = frag.rev_delta.get("ins_mark", 0) + 1
            frag.touches.append((main, "*", "text"))
            frag.intended_parts.append(main)
            frag.geometry_reference = "none"
            frag.geometry_reason = "a line is added at the end of the section"
            frag.transform_data = {"case": "insert", "after_child": list(body).index(last), "line": line,
                                   "formatting": formatting, "style": style, "copy_from": at}
        frag.property_assertions.append({
            "kind": "end_of_section", "number": rule, "text": text, "detect_regex": detect.pattern,
            "formatting": formatting, "style": params.get("style"), "copy_from_regex": params.get("copy_from_regex"),
            "tracked": tracked, "author": author, "expected_case": case, "expected_line": line})
        if case == "none":
            frag.notes.append(f"ensure_end_of_section: already ends with {line!r}; no change.")
            return frag
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(v for v in frag.rev_delta.values() if v > 0)
        frag.transform_data.update({"tracked": tracked, "author": author, "use_date_utc": file_uses_date_utc(pkg),
                                    "first_rev_id": frag.rev_id_base,
                                    "date_convention": params.get("date_convention", "word-local-z")})
        frag.notes.append(f"ensure_end_of_section: {'inserted' if case == 'insert' else 'corrected'} {line!r} "
                          f"(number {number!r} from the file; {'tracked' if tracked else 'untracked'})")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or td.get("case") not in ("insert", "replace"):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        main = pkg.main_part()
        root = pkg.xml(main)
        if td["case"] == "replace":
            apply_part_edits(root, td["edits"][main], td["tracked"], stamp, next_id)
            pkg.write_xml(main, root)
            return
        body = root.find(W + "body")
        anchor = list(body)[td["after_child"]]
        new_p = etree.Element(W + "p")
        rpr = None
        if td["formatting"] == "copy":
            src = paragraphs(root)[td["copy_from"]]
            ppr = copied_ppr(src)
            if ppr is not None:
                new_p.append(ppr)
            rpr = inherited_rpr(src)
        elif td.get("style"):
            ps = insert_in_order(paragraph_ppr(new_p), etree.Element(W + "pStyle"), PPR_ORDER)
            ps.set(W + "val", td["style"])
        if td["tracked"]:
            mark = etree.Element(W + "ins")
            stamp.apply(mark, next_id[0])
            next_id[0] += 1
            insert_in_order(mark_rpr(new_p, create=True), mark, RPR_ORDER)
            holder = etree.SubElement(new_p, W + "ins")
            stamp.apply(holder, next_id[0])
            next_id[0] += 1
            r = etree.SubElement(holder, W + "r")
        else:
            r = etree.SubElement(new_p, W + "r")
        if rpr is not None:
            r.append(rpr)
        set_text(etree.SubElement(r, W + "t"), td["line"])
        anchor.addnext(new_p)
        pkg.write_xml(main, root)


def _before(p, block, body) -> bool:
    """``p`` lies before the end of ``block`` in document order (for a block without paragraphs)."""
    kids = list(body)
    q = p
    while q is not None and q.getparent() is not body:
        q = q.getparent()
    return q is not None and kids.index(q) <= kids.index(block)
