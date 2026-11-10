"""The Obliterate record: what differs between an original and a copy, with nothing predicted.

A custom method written in Obliterate mode (the owner's decision of 2026-11-10; CLAUDE.md, Modes) predicts nothing: it
changes its copy, and this module — the independent verifier's own reading, with the suite's own readers and no code
of the transform — says what is different and checks what needs no prediction to check. The user approves the result
they are shown, never a plan.

``run_difference`` returns a ``VerificationRecord`` in the suite's shape, so every reader of a record reads this one:

* **V1 Package integrity** — FAIL on a part the copy can no longer be opened by (XML that is not well-formed), a
  relationship that points at nothing, a content-type override for a missing member, a member with no content type or
  the wrong one, or an ``r:`` reference that names no relationship: the things Word repairs or refuses. Its data
  lists the members changed, added, removed and left unreferenced.
* **V2 Words changed** — never a failure: the changed text blocks of every text part, and the accept-all and
  reject-all views of a part that carries tracked changes, in the data V2 keeps (``actual``, ``views``), so the words
  a copy adds and removes are listed by the same reader as any copy's (``preview.text_changes_of``).
* **V4 Revision ids** — FAIL on an id the method duplicated, one a new revision shares with an annotation of the
  original, or one Word cannot read. The counts before and after are data.
* **V5a XSD vs source baseline**, **V5c Revision grammar** — as the suite checks them: new schema errors in the
  changed and added parts, and tracked-change markup Word does not accept.
* **D1 What changed** — never a failure: the report in plain sentences (``lines``) and as data — counts of
  paragraphs, tables, pictures, fields, text boxes, sections and more, before and after; styles added, removed and
  changed; numbering, settings, relationships and parts; paragraphs whose formatting changed without their words,
  tables whose own layout changed, sections whose page setup changed; and every changed part this report does not
  read by content, named as such (``unread``).

V5b, V8 and V10 are N/A (they compare a copy with a prediction, and there is none); V6, V7 and V9 are the runner's,
as in the suite: the renderer opening the copy is V6. A record with no FAIL does not say the change is the one asked
for — nothing here can — which is why such a copy is marked UNPROVEN, and UNVERIFIED when a check here fails.

A copy that is not a package at all (no zip, no main part) is not described: ``run_difference`` raises, and the
caller holds that copy back as it holds back any output the verifier cannot read.
"""
from __future__ import annotations

import difflib
import json
import posixpath
from collections import Counter
from typing import Any, Dict, List, Set, Tuple

from lxml import etree

from .. import telemetry as diag
from . import xsd as _xsd
from .reader import REL, SETTINGS_REL, STYLES_REL, THEME_REL, VPackage, W
from .suite import (WORD_ID_LIMIT, CheckResult, VerificationRecord, _annotation_ids, _grammar_errors, _id_number,
                    _property_change_grammar, _rev_elements, _total_key, has_content_revisions)
from .textcheck import accept_view, blocks, diff_blocks, reject_view

JOB = "custom method (Obliterate)"
MODE = "shown"                      # the record's mode: the change was shown, not predicted
NOTE = ("Nothing was predicted for this copy: a custom method made it in Obliterate mode, and this record lists what "
        "differs from the original and checks what needs no prediction (the package, its XML, its tracked-change "
        "markup). No check here says the change is the one that was asked for.")
_REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
NUMBERING_REL = _REL_BASE + "numbering"
LISTED = 12                          # names a sentence lists in full before it says how many more
ADDRESS_CHARS = 120                  # of an outside address (a hyperlink's, a linked picture's) a sentence quotes
# what the report counts in the text parts, in the order it says them: (key, singular, plural, how it is found)
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
_VML = "{urn:schemas-microsoft-com:vml}"
COUNTED = (("paragraphs", "paragraph", "paragraphs"), ("tables", "table", "tables"),
           ("pictures", "picture", "pictures"), ("text_boxes", "text box", "text boxes"),
           ("fields", "field", "fields"), ("hyperlinks", "hyperlink", "hyperlinks"),
           ("sections", "section", "sections"), ("content_controls", "content control", "content controls"),
           ("bookmarks", "bookmark", "bookmarks"), ("footnotes", "footnote", "footnotes"),
           ("endnotes", "endnote", "endnotes"), ("comments", "comment", "comments"),
           ("page_breaks", "page break", "page breaks"))


def _named(names: List[str]) -> str:
    """``a, b, c and 4 more``: at most ``LISTED`` names in full."""
    names = list(names)
    return ", ".join(names[:LISTED]) + (f" and {len(names) - LISTED:,} more" if len(names) > LISTED else "")


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _parse(pkg: VPackage, names: List[str]) -> Tuple[Dict[str, Any], List[str]]:
    """Each of ``names`` parsed, and those that are not well-formed XML (or carry a document type declaration)."""
    parsed: Dict[str, Any] = {}
    broken: List[str] = []
    for name in names:
        try:
            parsed[name] = pkg.xml(name)
        except (etree.XMLSyntaxError, ValueError) as error:
            broken.append(f"{name}: not well-formed XML ({str(error)[:120]})")
    return parsed, broken


def _counts(pkg: VPackage, parsed: Dict[str, Any]) -> Dict[str, int]:
    """What the text parts hold, over all of them: the things a reader would count on the page."""
    total = {key: 0 for key, _one, _many in COUNTED}
    main = pkg.main_part()
    notes = {kind: set(pkg.part_of_kind(kind)) for kind in ("footnotes", "endnotes", "comments")}
    for part, root in parsed.items():
        for el in root.iter():
            if not isinstance(el.tag, str):
                continue
            tag = el.tag
            if tag == W + "p":
                total["paragraphs"] += 1
            elif tag == W + "tbl":
                total["tables"] += 1
            elif tag in (W + "drawing", W + "pict"):
                # a picture or a drawn object; a text box's own drawing is counted with the text boxes instead
                holds_box = any(True for _ in el.iter(W + "txbxContent"))
                if not holds_box:
                    total["pictures"] += 1
            elif tag in (_WPS + "txbx", _VML + "textbox"):
                total["text_boxes"] += 1
            elif tag == W + "fldSimple" or (tag == W + "fldChar" and el.get(W + "fldCharType") == "begin"):
                total["fields"] += 1
            elif tag == W + "hyperlink":
                total["hyperlinks"] += 1
            elif tag == W + "sectPr" and part == main:
                total["sections"] += 1
            elif tag == W + "sdt":
                total["content_controls"] += 1
            elif tag == W + "bookmarkStart":
                total["bookmarks"] += 1
            elif tag == W + "br" and el.get(W + "type") == "page":
                total["page_breaks"] += 1
            elif tag in (W + "footnote", W + "endnote", W + "comment"):
                kind = tag[len(W):] + "s"
                # Word's own separator notes are not notes a reader sees
                if part in notes[kind] and el.get(W + "type") in (None, "normal"):
                    total[kind] += 1
    return total


def _revisions(parsed: Dict[str, Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for root in parsed.values():
        for kind, _id in _rev_elements(root):
            out[kind] = out.get(kind, 0) + 1
    return out


def _canonical(el) -> bytes:
    return etree.tostring(el, method="c14n")


def _children_by(root, tag: str, key: str) -> Dict[str, bytes]:
    """The canonical form of each ``tag`` child of ``root`` by its ``key`` attribute (a style by its id, a numbering
    definition by its number); one without the attribute is keyed by its position."""
    out: Dict[str, bytes] = {}
    for i, el in enumerate(root.iter(W + tag)):
        out[el.get(W + key) or f"#{i}"] = _canonical(el)
    return out


def _keyed_difference(before: Dict[str, bytes], after: Dict[str, bytes]) -> Dict[str, List[str]]:
    return {"added": sorted(set(after) - set(before)), "removed": sorted(set(before) - set(after)),
            "changed": sorted(k for k in before if k in after and before[k] != after[k])}


def _related_xml(pkg: VPackage, rel_type: str):
    """The part the main part relates by ``rel_type`` (its styles, numbering or settings), parsed; None when there is
    none or it does not parse (V1 names a part that does not)."""
    name = pkg.related(rel_type)
    if not name or name not in pkg.names:
        return None, None
    try:
        return name, pkg.xml(name)
    except (etree.XMLSyntaxError, ValueError):
        return name, None


def _rels_owner(rels_member: str) -> str:
    """The part a relationships member belongs to: ``word/_rels/document.xml.rels`` → ``word/document.xml``; the
    package's own (``_rels/.rels``) has none, and gives ``""``."""
    folder, name = posixpath.split(rels_member)
    if posixpath.basename(folder) != "_rels" or not name.endswith(".rels") or name == ".rels":
        return ""
    return posixpath.join(posixpath.dirname(folder), name[:-len(".rels")])


def _relationships(pkg: VPackage) -> Counter:
    """Every relationship of the package, counted: (owner part, kind, target, outside). An internal target is the
    member it resolves to; an outside one (a hyperlink's address, a linked picture or template) is the address
    itself, so a link that leads somewhere else is a relationship that went and one that came. A relationships part
    that does not parse gives none (V1 names it)."""
    names = set(pkg.names)
    found: Counter = Counter()
    for member in pkg.names:
        folder, name = posixpath.split(member)
        if not name.endswith(".rels") or posixpath.basename(folder) != "_rels":
            continue
        owner = _rels_owner(member)
        if owner and owner not in names:
            continue
        try:
            root = pkg.xml(member)
        except (etree.XMLSyntaxError, ValueError):
            continue
        base = posixpath.dirname(owner)
        for rel in root.findall(REL + "Relationship"):
            kind = (rel.get("Type") or "").rsplit("/", 1)[-1]
            target = rel.get("Target") or ""
            outside = rel.get("TargetMode", "Internal") != "Internal"
            if not outside:
                target = target.lstrip("/") if target.startswith("/") else posixpath.normpath(
                    posixpath.join(base, target))
            found[(owner or "the package", kind, target, outside)] += 1
    return found


def _part_words(pkg: VPackage, part: str) -> str:
    """A text part in plain words: ``the body``, ``a header (word/header2.xml)``."""
    if part == pkg.main_part():
        return "the body"
    for kind, word in (("headers", "a header"), ("footers", "a footer"), ("footnotes", "the footnotes"),
                       ("endnotes", "the endnotes"), ("comments", "the comments")):
        if part in pkg.part_of_kind(kind):
            return f"{word} ({part})"
    return part


def _formatting_only(before, after) -> int:
    """The paragraphs of one text part whose markup differs while their words do not: the paragraphs the two sides
    share, paired in order by their text (a paragraph that came or went, or whose words changed, is V2's to list),
    counted when their canonical XML differs."""
    a, b = list(before.iter(W + "p")), list(after.iter(W + "p"))
    texts_a, texts_b = [x.text for x in blocks(before)], [x.text for x in blocks(after)]
    pairs = difflib.SequenceMatcher(None, texts_a, texts_b, autojunk=False).get_matching_blocks()
    return sum(1 for i, j, n in pairs for k in range(n) if _canonical(a[i + k]) != _canonical(b[j + k]))


_TABLE_SHAPE = tuple(W + tag for tag in ("tblPr", "tblGrid", "tblPrEx", "trPr", "tcPr"))


def _table_shape(table) -> bytes:
    """What a table is apart from its cells' paragraphs: its own properties, its grid, and each row's and cell's."""
    return b"".join(_canonical(el) for el in table.iter(*_TABLE_SHAPE))


def _paired_changes(before: List[Any], after: List[Any], form) -> int:
    """How many of two equally long lists' members differ, pair by pair in order; 0 when the lists are not the same
    length (the count that moved is said instead, and no pairing is honest)."""
    if len(before) != len(after):
        return 0
    return sum(1 for a, b in zip(before, after) if form(a) != form(b))


def run_difference(source: bytes, output: bytes, source_name: str, output_name: str) -> VerificationRecord:
    """The Obliterate record of ``output`` against ``source`` (module docstring). Raises when ``output`` is not a
    package the verifier can open at all."""
    with diag.span("verification.difference", source_bytes=len(source), output_bytes=len(output)) as outcome:
        rec = _run_difference(source, output, source_name, output_name)
        outcome.update(outcome="PASS" if rec.passed else "FAIL",
                       failed_checks=[c.id for c in rec.checks if c.status == "FAIL"])
        return rec


def _run_difference(source: bytes, output: bytes, source_name: str, output_name: str) -> VerificationRecord:
    src, out = VPackage(source), VPackage(output)
    if out.main_part() not in out.names:               # no main part: not a document, and nothing to describe
        raise ValueError("the copy has no main document part")
    rec = VerificationRecord(source=source_name, output=output_name, job=JOB, mode=MODE, predicted_diffs=[],
                             predicted_parts=[], predicted_rev_delta={}, notes=[NOTE], geometry_reference="none")
    hb, ha = src.hashes(), out.hashes()
    added = sorted(set(ha) - set(hb))
    removed = sorted(set(hb) - set(ha))
    changed = sorted(n for n in hb if n in ha and hb[n] != ha[n])
    src_tp, out_tp = src.text_parts(), out.text_parts()
    src_xml, _src_broken = _parse(src, src_tp)
    out_xml, broken = _parse(out, out_tp)

    # V1 — the package: what Word repairs or refuses, none of it a matter of prediction
    touched = [n for n in changed + added if n.lower().endswith((".xml", ".rels")) and n not in out_tp]
    broken += _parse(out, touched)[1]
    try:
        problems = set(out.integrity_problems()) - set(src.integrity_problems())
        # a part's r: references are read again when the part changed, and when its relationships did (a
        # relationship removed leaves the reference that named it pointing at nothing, in a part no one touched)
        referring = set(changed + added) | {_rels_owner(n) for n in changed + added + removed if n.endswith(".rels")}
        for name in sorted(referring):
            if name in out.names and name.endswith(".xml") and not any(b.startswith(name + ":") for b in broken):
                problems |= out.rid_problems(name) - (src.rid_problems(name) if name in src.names else set())
        src_refd, out_refd = src.referenced_members(), out.referenced_members()
        orphaned = sorted(n for n in out.names if n in src_refd and n not in out_refd)
    except (etree.XMLSyntaxError, ValueError, KeyError) as error:
        # a relationships part that does not parse: the broken list names it; nothing more can be read off it
        problems, orphaned = {f"the package's relationships cannot be read ({str(error)[:120]})"}, []
    problems = sorted(problems | set(broken))
    rec.set(CheckResult(
        "V1", "Package integrity", "FAIL" if problems else "PASS",
        f"parts changed {len(changed)}, added {len(added)}, removed {len(removed)}"
        + (f"; NEW problems Word repairs or refuses: {problems[:3]}" if problems else
           "; every part parses, every relationship has its part and every part its content type"),
        {"changed": changed, "added": added, "removed": removed, "orphaned": orphaned, "integrity": problems}))

    # V2 — the words, listed: the suite's own block comparison with no prediction beside it
    scope = sorted(set(src_xml) | set(out_xml))
    actual: List[Dict[str, Any]] = []
    for part in scope:
        b_before = blocks(src_xml[part]) if part in src_xml else []
        b_after = blocks(out_xml[part]) if part in out_xml else []
        for ch in diff_blocks(b_before, b_after):
            if ch.before != ch.after:
                actual.append({"part": part, "before": ch.before, "after": ch.after,
                               "before_index": ch.before_index, "after_index": ch.after_index})
    revised = [p for p in scope if (p in src_xml and has_content_revisions(src_xml[p]))
               or (p in out_xml and has_content_revisions(out_xml[p]))]
    views: Dict[str, Any] = {"parts": revised, "accepted": [], "rejected": []}
    for part in revised:
        for which, view in (("accepted", accept_view), ("rejected", reject_view)):
            v_before = blocks(view(src_xml[part])) if part in src_xml else []
            v_after = blocks(view(out_xml[part])) if part in out_xml else []
            for ch in diff_blocks(v_before, v_after):
                if ch.before != ch.after:
                    views[which].append({"part": part, "before": ch.before, "after": ch.after,
                                         "before_index": ch.before_index, "after_index": ch.after_index})
    rec.set(CheckResult("V2", "Words changed (listed, not predicted)", "NA",
                        f"{len(actual)} text block(s) differ across {len(scope)} text part(s); they are listed for "
                        f"the user, and nothing was predicted to compare them with",
                        {"parts": scope, "actual": actual, "missing": [], "unpredicted": [], "views": views}))

    # V4 — revision ids: the ones Word cannot tell apart or cannot read
    cnt_b, cnt_a = _revisions(src_xml), _revisions(out_xml)
    src_list = [(part, k, i) for part, root in src_xml.items() for k, i in _rev_elements(root)]
    out_elems = [(part, k, i) for part, root in out_xml.items() for k, i in _rev_elements(root)]
    src_elems = set(src_list)
    id_count: Dict[str, int] = {}
    for _part, _kind, i in out_elems:
        if i is not None:
            id_count[i] = id_count.get(i, 0) + 1
    src_id_count: Dict[str, int] = {}
    for _part, _kind, i in src_list:               # every occurrence: an id the original repeats is the original's
        if i is not None:
            src_id_count[i] = src_id_count.get(i, 0) + 1
    duplicated = sorted(i for i, n in id_count.items() if n > 1 and n > src_id_count.get(i, 0))
    new_elems = [e for e in out_elems if e not in src_elems]
    new_ids = sorted({i for _part, _kind, i in new_elems}, key=_total_key)
    annotations = _annotation_ids(src, src_xml) if new_ids else set()
    reused = [i for i in new_ids if _id_number(i) is not None and _id_number(i) in annotations]
    unreadable = [i for i in new_ids if _id_number(i) is None or not 0 <= _id_number(i) <= WORD_ID_LIMIT]
    v4_bad = bool(duplicated or reused or unreadable)
    rec.set(CheckResult(
        "V4", "Revision ids", "FAIL" if v4_bad else "PASS",
        f"tracked changes {sum(cnt_b.values())} before, {sum(cnt_a.values())} after"
        + (f"; ids the method DUPLICATED {duplicated[:5]}" if duplicated else "; ids unique")
        + (f"; NEW ids that reuse an annotation id of the original {reused[:5]}" if reused else "")
        + (f"; NEW ids Word cannot read (not a whole number up to {WORD_ID_LIMIT}) {unreadable[:5]}"
           if unreadable else ""),
        {"counts_before": cnt_b, "counts_after": cnt_a, "duplicate_ids_all_kinds": duplicated,
         "new_ids_reusing_source_annotation_ids": reused, "new_ids_out_of_range": unreadable}))

    # V5a — the schema, against what the original already got wrong
    new_errors: Dict[str, List[str]] = {}
    baseline: Dict[str, int] = {}
    skipped: List[str] = []
    for part in sorted(set(changed) | set(added) | {"[Content_Types].xml"}):
        if part not in out.names or any(b.startswith(part + ":") for b in broken):
            continue
        try:
            base, outp, new = _xsd.compare(part, src.read(part) if part in src.names else None, out.read(part))
        except Exception as error:  # noqa: BLE001 — a part the schema reader cannot take is a finding, not a crash
            new_errors[part] = [f"could not be validated: {type(error).__name__}: {str(error)[:160]}"]
            continue
        if outp is None:
            skipped.append(part)
            continue
        baseline[part] = len(base or [])
        if new:
            new_errors[part] = sorted(new)[:5]
    rec.set(CheckResult("V5a", "XSD vs source baseline", "PASS" if not new_errors else "FAIL",
                        f"validated {len(baseline)} changed part(s); new errors: "
                        + ("0" if not new_errors else json.dumps(new_errors)[:600])
                        + (f"; no schema for {skipped[:8]}" if skipped else ""),
                        {"baseline": baseline, "new_errors": new_errors, "skipped": skipped}))
    rec.set(CheckResult("V5b", "Untracked-edit check", "NA",
                        "N/A — nothing was predicted: the words that changed are listed under V2"))

    # V5c — tracked-change markup Word accepts
    grammar: Set[str] = set()
    for part, root in out_xml.items():
        grammar |= set(_grammar_errors(root, part)) | set(_property_change_grammar(root, part))
    for part, root in src_xml.items():
        grammar -= set(_grammar_errors(root, part)) | set(_property_change_grammar(root, part))
    rec.set(CheckResult("V5c", "Revision grammar", "PASS" if not grammar else "FAIL",
                        "tracked-change markup the method added is well-formed: "
                        + ("yes" if not grammar else f"NO — {len(grammar)} error(s): {sorted(grammar)[:3]}"),
                        {"errors": sorted(grammar)[:50]}))
    rec.set(CheckResult("V8", "Accept / reject simulation", "NA",
                        "N/A — nothing was predicted: V2 lists what the accept-all and reject-all views add and remove"))
    rec.set(CheckResult("V10", "Property assertions", "NA",
                        "N/A — a custom method in Obliterate mode asserts nothing: what it changed is under D1"))

    # D1 — everything else that differs, in plain sentences
    lines, data = _what_changed(src, out, src_xml, out_xml, changed, added, removed, orphaned, actual, cnt_b, cnt_a)
    rec.set(CheckResult("D1", "What changed", "NA", " ".join(lines)[:1200], data))

    rec.set(CheckResult("V6", "Automated render comparison", "NA", "no renderer run yet"))
    rec.set(CheckResult("V7", "Geometry assertion", "NA", "no renderer run yet"))
    rec.set(CheckResult("V9", "Overwrite guard", "NA", "not delivered yet"))
    return rec


def _what_changed(src: VPackage, out: VPackage, src_xml, out_xml, changed, added, removed, orphaned, actual,
                  cnt_b: Dict[str, int], cnt_a: Dict[str, int]) -> Tuple[List[str], Dict[str, Any]]:
    """D1's sentences and data: what differs beside the words (V2 lists those)."""
    lines: List[str] = []
    described: Set[str] = set(src_xml) | set(out_xml) | {"[Content_Types].xml"}
    described |= {n for n in changed + added + removed if n.endswith(".rels")}

    # the counts a reader would make on the page
    before, after = _counts(src, src_xml), _counts(out, out_xml)
    moved = {key: [before[key], after[key]] for key, _one, _many in COUNTED if before[key] != after[key]}
    for key, one, many in COUNTED:
        if key in moved:
            lines.append(f"{many.capitalize()}: {before[key]:,} before, {after[key]:,} after.")
    if cnt_b != cnt_a:
        ins = cnt_a.get("ins", 0) - cnt_b.get("ins", 0)
        dele = cnt_a.get("del", 0) - cnt_b.get("del", 0)
        other = (sum(cnt_a.values()) - sum(cnt_b.values())) - ins - dele
        parts = [f"{n:+,} {what}" for n, what in ((ins, "tracked insertions"), (dele, "tracked deletions"),
                                                  (other, "other tracked changes")) if n]
        lines.append(f"Tracked changes: {sum(cnt_b.values()):,} before, {sum(cnt_a.values()):,} after"
                     + (f" ({', '.join(parts)})." if parts else "."))

    # paragraphs whose formatting or markup changed while their words did not
    formatting: Dict[str, int] = {}
    for part in sorted(set(src_xml) & set(out_xml)):
        if part in changed:
            n = _formatting_only(src_xml[part], out_xml[part])
            if n:
                formatting[part] = n
                lines.append(f"In {_part_words(out, part)}, the formatting or markup of {_count(n, 'paragraph', 'paragraphs')} "
                             f"changed without changing {'its' if n == 1 else 'their'} words.")

    # tables and page setup: what a paragraph-by-paragraph reading does not see
    tables: Dict[str, int] = {}
    page_setup = 0
    for part in sorted(set(src_xml) & set(out_xml)):
        if part not in changed:
            continue
        n = _paired_changes(list(src_xml[part].iter(W + "tbl")), list(out_xml[part].iter(W + "tbl")), _table_shape)
        if n:
            tables[part] = n
            lines.append(f"In {_part_words(out, part)}, the layout or formatting of {_count(n, 'table', 'tables')} "
                         f"changed (widths, borders, shading, or row and cell settings).")
        if part == out.main_part():
            page_setup = _paired_changes(list(src_xml[part].iter(W + "sectPr")),
                                         list(out_xml[part].iter(W + "sectPr")), _canonical)
            if page_setup:
                lines.append(f"Page setup: the settings of {_count(page_setup, 'section', 'sections')} changed (page "
                             f"size, margins, columns, or which headers and footers it uses).")

    # styles, numbering, settings: read by content
    styles: Dict[str, List[str]] = {}
    s_name, s_before = _related_xml(src, STYLES_REL)
    o_name, s_after = _related_xml(out, STYLES_REL)
    described |= {n for n in (s_name, o_name) if n}
    if s_before is not None and s_after is not None and (s_name in changed or s_name != o_name):
        styles = _keyed_difference(_children_by(s_before, "style", "styleId"), _children_by(s_after, "style", "styleId"))
        said = [f"{len(styles[k])} {k} ({_named(styles[k])})" for k in ("added", "removed", "changed") if styles[k]]
        lines.append("Styles: " + ("; ".join(said) + "." if said else
                                   "the styles part changed outside its style definitions (defaults or latent styles)."))
    numbering: Dict[str, List[str]] = {}
    n_name, n_before = _related_xml(src, NUMBERING_REL)
    on_name, n_after = _related_xml(out, NUMBERING_REL)
    described |= {n for n in (n_name, on_name) if n}
    if (n_name or on_name) and (n_name in changed or n_name != on_name or on_name in added or n_name in removed):
        b_defs = {**{"list " + k: v for k, v in _children_by(n_before, "num", "numId").items()},
                  **{"definition " + k: v for k, v in _children_by(n_before, "abstractNum", "abstractNumId").items()}} \
            if n_before is not None else {}
        a_defs = {**{"list " + k: v for k, v in _children_by(n_after, "num", "numId").items()},
                  **{"definition " + k: v for k, v in _children_by(n_after, "abstractNum", "abstractNumId").items()}} \
            if n_after is not None else {}
        numbering = _keyed_difference(b_defs, a_defs)
        said = [f"{len(numbering[k])} {k}" for k in ("added", "removed", "changed") if numbering[k]]
        lines.append("List numbering: " + (", ".join(said) + "." if said else "the numbering part changed."))
    settings: Dict[str, List[str]] = {}
    t_name, t_before = _related_xml(src, SETTINGS_REL)
    ot_name, t_after = _related_xml(out, SETTINGS_REL)
    described |= {n for n in (t_name, ot_name) if n}
    if t_before is not None and t_after is not None and (t_name in changed or t_name != ot_name):
        def top(root) -> Dict[str, bytes]:
            found: Dict[str, bytes] = {}
            for el in root:
                if isinstance(el.tag, str):
                    name = etree.QName(el).localname
                    found[name] = found.get(name, b"") + _canonical(el)
            return found
        settings = _keyed_difference(top(t_before), top(t_after))
        said = [f"{k} {_named(settings[k])}" for k in ("added", "removed", "changed") if settings[k]]
        lines.append("Document settings: " + ("; ".join(said) + "." if said else "the settings part changed."))

    # relationships and the parts themselves; an outside address is read as it is written, so a hyperlink that now
    # leads somewhere else is said with both addresses
    r_before, r_after = _relationships(src), _relationships(out)
    r_added, r_removed = sorted((r_after - r_before).elements()), sorted((r_before - r_after).elements())
    if r_added or r_removed:
        def one(r):
            if r[3]:
                address = r[2] if len(r[2]) <= ADDRESS_CHARS else r[2][:ADDRESS_CHARS] + "…"
                return f"{r[1]} → {address or '(no address)'}, outside the file"
            return f"{r[1]} → {posixpath.basename(r[2]) if '/' in r[2] else r[2]}"
        lines.append("Relationships: " + "; ".join(
            f"{len(group)} {word} ({_named([one(r) for r in group])})"
            for group, word in ((r_added, "added"), (r_removed, "removed")) if group) + ".")

    def sized(pkg: VPackage, names: List[str]) -> List[str]:
        return [f"{n} ({len(pkg.read(n)):,} bytes)" for n in names]
    plain_added = [n for n in added if not n.endswith(".rels")]
    plain_removed = [n for n in removed if not n.endswith(".rels")]
    if plain_added:
        lines.append(f"Parts added: {_named(sized(out, plain_added))}.")
    if plain_removed:
        lines.append(f"Parts removed: {_named(plain_removed)}.")
    if orphaned:
        lines.append(f"Left in the package but no longer referenced by anything: {_named(orphaned)}.")
    theme = {n for n in (src.related(THEME_REL), out.related(THEME_REL)) if n}
    unread = sorted(n for n in changed if n not in described)
    if unread:
        lines.append("Changed, and not read by this report (it compares their bytes, not what they hold): "
                     + _named([n + (" — the theme" if n in theme else "") for n in unread]) + ".")
    if not (changed or added or removed):
        lines.append("Nothing differs: every part of the copy is the original's, byte for byte.")
    elif not lines and not actual:
        lines.append(f"{_count(len(changed), 'part', 'parts')} changed ({_named(changed)}), with no difference this "
                     f"report counts: the same words, paragraphs, tables, pictures and tracked changes.")
    return lines, {"lines": lines, "counts": moved, "counts_before": before, "counts_after": after,
                   "formatting_only": formatting, "tables": tables, "page_setup": page_setup, "styles": styles,
                   "numbering": numbering, "settings": settings,
                   "relationships": {"added": [list(r[:3]) for r in r_added],
                                     "removed": [list(r[:3]) for r in r_removed]},
                   "members": {"changed": changed, "added": added, "removed": removed, "orphaned": orphaned},
                   "unread": unread}


def difference_lines(record: Any) -> List[str]:
    """D1's sentences from a record (the object, or its ``to_dict()``); [] for a record without one."""
    checks = record.get("checks") if isinstance(record, dict) else [
        {"id": c.id, "data": c.data} for c in getattr(record, "checks", None) or []]
    for check in checks or []:
        if isinstance(check, dict) and check.get("id") == "D1":
            lines = (check.get("data") or {}).get("lines")
            return [str(x) for x in lines] if isinstance(lines, list) else []
    return []
