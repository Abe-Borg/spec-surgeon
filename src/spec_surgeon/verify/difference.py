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

**A copy Word saved** (``rewritten=True``: Obliterate's named Word actions, docs/BACKLOG.md O5). Word writes every
part of the file again, so a byte or a part name says nothing, and the comparison is by content:

* the text parts are paired by their place in the document (``_paired``: the body, the notes, the comments, and each
  header and footer by the sections that use it and as what), never by their names, which Word gives again;
* paragraphs, tables, sections, styles, numbering and settings are compared without Word's own save marks
  (``_plain``: its revision-save ids, paragraph ids, spelling and page-break marks, the ``_GoBack`` bookmark, and how
  a line's text is split into runs), so a count says markup that changed, not a file that was saved. The styles
  and numbering parts are read whole — the document's default formatting beside the style definitions, the picture
  bullets beside the lists (``_outside``) — but for what Word writes again with each save and which formats nothing:
  its list of latent styles, its own counters, the save's bookkeeping in the settings;
* V4, V5a and V5c are N/A: the revision ids and the markup are Word's own writing, and a rule of this app's about
  them would say nothing about a file Word itself just wrote. V1 still fails a copy that does not open as a package
  (a part that does not parse, a reference to nothing), and V6 is the renderer opening it.
"""
from __future__ import annotations

import copy
import difflib
import json
import posixpath
from collections import Counter
from typing import Any, Dict, List, Set, Tuple

from lxml import etree

from .. import telemetry as diag
from . import xsd as _xsd
from .reader import R, REL, SETTINGS_REL, STYLES_REL, THEME_REL, VPackage, W, root_problems
from .suite import (WORD_ID_LIMIT, CheckResult, VerificationRecord, _annotation_ids, _grammar_errors, _id_number,
                    _property_change_grammar, _rev_elements, _total_key, has_content_revisions)
from .textcheck import accept_view, blocks, diff_blocks, reject_view

JOB = "custom method (Obliterate)"
MODE = "shown"                      # the record's mode: the change was shown, not predicted
NOTE = ("Nothing was predicted for this copy: a custom method made it in Obliterate mode, and this record lists what "
        "differs from the original and checks what needs no prediction (the package, its XML, its tracked-change "
        "markup). No check here says the change is the one that was asked for.")
JOB_WORD = "Word actions (Obliterate)"
NOTE_WORD = ("Nothing was predicted for this copy: Word made it in Obliterate mode, running named actions and saving "
             "the whole file again. This record lists what differs from the original by content — the words, and what "
             "the parts hold once Word's own save marks are set aside — and checks that the copy opens as a package. "
             "No check here says the change is the one that was asked for.")
SAVED_AGAIN = ("Word saved every part of this file again, so the parts are paired by their place in the document and "
               "compared by what they hold. Word's own save marks are not counted: its revision-save ids, paragraph "
               "ids, spelling and page-break marks, and how a line's text is split into runs. A count below can "
               "still include markup Word tidied as it saved.")
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
                if el.get(W + "name") != "_GoBack":      # Word's own mark of where the cursor was, never a reader's
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


_SAVE_ELEMENTS = (W + "proofErr", W + "lastRenderedPageBreak", W + "rsid")
_SAVE_ATTRIBUTES = ("paraId", "textId")
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _same_run_format(a, b) -> bool:
    """Two runs that hold text alone, with the same formatting: Word may write them as one run or as several."""
    for run in (a, b):
        if any(isinstance(c.tag, str) and c.tag not in (W + "rPr", W + "t") for c in run):
            return False
    pa, pb = a.find(W + "rPr"), b.find(W + "rPr")
    if (pa is None) != (pb is None):
        return False
    return pa is None or etree.tostring(pa, method="c14n", exclusive=True) == etree.tostring(pb, method="c14n",
                                                                                              exclusive=True)


def _plain(el) -> bytes:
    """``el`` in canonical form without what Word's own save writes into it: the revision-save ids (``w:rsid…``)
    and paragraph ids, the spelling and page-break marks, the ``_GoBack`` bookmark, the relationship ids (Word gives
    them again), and the boundaries between neighbouring runs of the same formatting. Only the namespaces the element itself uses are written, so a root
    that declares more of them changes nothing. What a copy Word saved is compared by (module docstring)."""
    el = copy.deepcopy(el)
    goback = {b.get(W + "id") for b in el.iter(W + "bookmarkStart") if b.get(W + "name") == "_GoBack"}
    for node in list(el.iter()):
        if not isinstance(node.tag, str):
            continue
        marks = node.tag in _SAVE_ELEMENTS or (node.tag in (W + "bookmarkStart", W + "bookmarkEnd")
                                               and node.get(W + "id") in goback)
        if marks and node is not el and node.getparent() is not None:
            node.getparent().remove(node)
            continue
        for name in list(node.attrib):
            local = etree.QName(name).localname
            # (a relationship id is Word's to give again: where a reference leads is the relationships' to say, D1)
            if local.startswith("rsid") or local in _SAVE_ATTRIBUTES or name.startswith(R):
                del node.attrib[name]
        if node.tag == W + "t":
            node.set(_XML_SPACE, "preserve")
    for parent in list(el.iter()):
        if not isinstance(parent.tag, str):
            continue
        previous = None
        for child in list(parent):
            if child.tag != W + "r":
                previous = None
                continue
            if previous is not None and _same_run_format(previous, child):
                texts = previous.findall(W + "t") + child.findall(W + "t")
                kept = texts[0] if texts else None
                if kept is not None:
                    kept.text = "".join(t.text or "" for t in texts)
                    for t in texts[1:]:
                        t.getparent().remove(t)
                parent.remove(child)
                continue
            previous = child
    return etree.tostring(el, method="c14n", exclusive=True)


def _roles(pkg: VPackage, parsed: Dict[str, Any]) -> Dict[str, Tuple[str, ...]]:
    """What each text part is by its place in the document, never by its name: the body, the footnotes, endnotes
    and comments, and each header and footer by the sections that use it and as what (``header:first:2``: the
    first-page header of the third section). A part no section uses has no role."""
    main = pkg.main_part()
    roles: Dict[str, List[str]] = {main: ["document"]}
    for kind in ("footnotes", "endnotes", "comments"):
        for part in pkg.part_of_kind(kind):
            roles.setdefault(part, []).append(kind)
    root = parsed.get(main)
    if root is not None:
        rels = pkg.rels_targets(main)
        for i, section in enumerate(root.iter(W + "sectPr")):
            for ref in section:
                if ref.tag in (W + "headerReference", W + "footerReference"):
                    target = (rels.get(ref.get(R + "id") or "") or {}).get("target")
                    if target:
                        what = "header" if ref.tag == W + "headerReference" else "footer"
                        roles.setdefault(target, []).append(f"{what}:{ref.get(W + 'type') or 'default'}:{i}")
    return {part: tuple(sorted(found)) for part, found in roles.items()}


def _paired(src: VPackage, out: VPackage, src_xml: Dict[str, Any],
            out_xml: Dict[str, Any]) -> List[Tuple[Any, Any]]:
    """The text parts of a copy Word saved beside the original's, ``(source part or None, copy part or None)``: by
    their place in the document first (``_roles``), then by name for what is left, then alone (a part that came or
    went). The source's parts keep their order."""
    a, b = _roles(src, src_xml), _roles(out, out_xml)
    placed = {roles: part for part, roles in b.items() if roles and part in out_xml}
    pairs: List[Tuple[Any, Any]] = []
    used: Set[str] = set()
    waiting: List[str] = []
    for part in src_xml:
        other = placed.get(a.get(part) or ())
        if other is not None and other not in used:
            pairs.append((part, other))
            used.add(other)
        else:
            waiting.append(part)
    for part in waiting:
        if part in out_xml and part not in used:
            pairs.append((part, part))
            used.add(part)
        else:
            pairs.append((part, None))
    pairs += [(None, part) for part in out_xml if part not in used]
    return pairs


def _children_by(root, tag: str, key: str, form=None) -> Dict[str, bytes]:
    """The canonical form of each ``tag`` child of ``root`` by its ``key`` attribute (a style by its id, a numbering
    definition by its number); one without the attribute is keyed by its position. ``form``: how an element is
    written to be compared (``_plain`` for a copy Word saved)."""
    out: Dict[str, bytes] = {}
    for i, el in enumerate(root.iter(W + tag)):
        out[el.get(W + key) or f"#{i}"] = (form or _canonical)(el)
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


def _renamed(src: VPackage, out: VPackage, pairs) -> Dict[str, str]:
    """The members of a copy Word saved that are the original's under another name, copy name → original name: a
    text part by its place in the document (``pairs``), and any other member whose bytes are exactly one member's of
    the original (a picture Word numbered again)."""
    names = {b: a for a, b in pairs if a and b and a != b}
    hb, ha = src.hashes(), out.hashes()
    by_bytes: Dict[str, List[str]] = {}
    for name, digest in hb.items():
        by_bytes.setdefault(digest, []).append(name)
    taken = set(names.values())
    for name, digest in ha.items():
        same = by_bytes.get(digest) or []
        if name not in names and name not in hb and len(same) == 1 and same[0] not in ha and same[0] not in taken:
            names[name] = same[0]
            taken.add(same[0])
    return names


def _relationships(pkg: VPackage, known: Any = None) -> Counter:
    """Every relationship of the package, counted: (owner part, kind, target, outside). An internal target is the
    member it resolves to; an outside one (a hyperlink's address, a linked picture or template) is the address
    itself, so a link that leads somewhere else is a relationship that went and one that came. A relationships part
    that does not parse gives none (V1 names it). ``known`` (a copy Word saved, ``_renamed``): an owner and an
    internal target are said by the names the original gave those parts."""
    names = set(pkg.names)
    known = known or {}
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
            if not outside:
                target = known.get(target, target)
            found[(known.get(owner, owner) or "the package", kind, target, outside)] += 1
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


def _formatting_only(before, after, form=None) -> int:
    """The paragraphs of one text part whose markup differs while their words do not: the paragraphs the two sides
    share, paired in order by their text (a paragraph that came or went, or whose words changed, is V2's to list),
    counted when their canonical XML differs (``form``: ``_plain`` for a copy Word saved)."""
    form = form or _canonical
    a, b = list(before.iter(W + "p")), list(after.iter(W + "p"))
    texts_a, texts_b = [x.text for x in blocks(before)], [x.text for x in blocks(after)]
    pairs = difflib.SequenceMatcher(None, texts_a, texts_b, autojunk=False).get_matching_blocks()
    return sum(1 for i, j, n in pairs for k in range(n) if form(a[i + k]) != form(b[j + k]))


_TABLE_SHAPE = tuple(W + tag for tag in ("tblPr", "tblGrid", "tblPrEx", "trPr", "tcPr"))
_SAVE_SETTINGS = ("rsids", "proofState")        # what Word's settings part records of a save itself
# What Word writes again with each save in the styles and numbering parts, and which formats nothing: its own list of
# the built-in styles a document does not define (their order and visibility in Word's panes), and a counter of its
# own. Everything else outside the style and list definitions is read (``_outside``): the document's default
# formatting changes every page.
_SAVE_STYLES = ("latentStyles",)
_SAVE_NUMBERING = ("numIdMacAtCleanup",)
_OUTSIDE_WORDS = {"docDefaults": "the document's default formatting (the font, size and spacing every style starts "
                                 "from)",
                  "numPicBullet": "the picture bullets"}


def _outside(root, own: Tuple[str, ...], skip: Tuple[str, ...]) -> Dict[str, bytes]:
    """What a styles or numbering part holds beside its ``own`` definitions, by element name, in plain form
    (``_plain``), without what Word's save writes of its own accord (``skip``)."""
    found: Dict[str, bytes] = {}
    for el in root:
        if not isinstance(el.tag, str):
            continue
        name = etree.QName(el).localname
        if name not in own and name not in skip:
            found[name] = found.get(name, b"") + _plain(el)
    return found


def _outside_changed(before, after, own: Tuple[str, ...], skip: Tuple[str, ...]) -> List[str]:
    """The names of what changed, came or went in a part outside its own definitions (``_outside``)."""
    found = _keyed_difference(_outside(before, own, skip), _outside(after, own, skip))
    return sorted(set(found["added"]) | set(found["removed"]) | set(found["changed"]))



def _table_shape(table, form=None) -> bytes:
    """What a table is apart from its cells' paragraphs: its own properties, its grid, and each row's and cell's."""
    return b"".join((form or _canonical)(el) for el in table.iter(*_TABLE_SHAPE))


def _paired_changes(before: List[Any], after: List[Any], form) -> int:
    """How many of two equally long lists' members differ, pair by pair in order; 0 when the lists are not the same
    length (the count that moved is said instead, and no pairing is honest)."""
    if len(before) != len(after):
        return 0
    return sum(1 for a, b in zip(before, after) if form(a) != form(b))


def run_difference(source: bytes, output: bytes, source_name: str, output_name: str, *,
                   rewritten: bool = False) -> VerificationRecord:
    """The Obliterate record of ``output`` against ``source`` (module docstring). ``rewritten``: Word saved the copy
    (the named Word actions), so it is compared by content and the checks of this app's own writing are N/A. Raises
    when ``output`` is not a package the verifier can open at all."""
    with diag.span("verification.difference", source_bytes=len(source), output_bytes=len(output),
                   rewritten=rewritten) as outcome:
        rec = _run_difference(source, output, source_name, output_name, rewritten)
        outcome.update(outcome="PASS" if rec.passed else "FAIL",
                       failed_checks=[c.id for c in rec.checks if c.status == "FAIL"])
        return rec


def _run_difference(source: bytes, output: bytes, source_name: str, output_name: str,
                    rewritten: bool = False) -> VerificationRecord:
    src, out = VPackage(source), VPackage(output)
    if out.main_part() not in out.names:               # no main part: not a document, and nothing to describe
        raise ValueError("the copy has no main document part")
    rec = VerificationRecord(source=source_name, output=output_name, job=JOB_WORD if rewritten else JOB, mode=MODE,
                             predicted_diffs=[], predicted_parts=[], predicted_rev_delta={},
                             notes=[NOTE_WORD if rewritten else NOTE], geometry_reference="none")
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
        problems |= root_problems(src, out, changed)     # a part that is no longer the kind its place calls for
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
    # (a copy Word saved: each text part beside the one in its place, whatever Word named it; an entry carries the
    # original's name for the part, the copy's when the part is new)
    pairs = (_paired(src, out, src_xml, out_xml) if rewritten else
             [(p if p in src_xml else None, p if p in out_xml else None) for p in sorted(set(src_xml) | set(out_xml))])
    scope = [a or b for a, b in pairs]
    actual: List[Dict[str, Any]] = []
    for a, b in pairs:
        b_before = blocks(src_xml[a]) if a else []
        b_after = blocks(out_xml[b]) if b else []
        for ch in diff_blocks(b_before, b_after):
            if ch.before != ch.after:
                actual.append({"part": a or b, "before": ch.before, "after": ch.after,
                               "before_index": ch.before_index, "after_index": ch.after_index})
    revised = [(a, b) for a, b in pairs if (a and has_content_revisions(src_xml[a]))
               or (b and has_content_revisions(out_xml[b]))]
    # ``after``: the parts whose COPY still carries tracked changes — where it carries none, a difference between
    # the two views is a tracked change of the original that the copy settled (accepted or rejected), not one the
    # copy holds
    views: Dict[str, Any] = {"parts": [a or b for a, b in revised], "accepted": [], "rejected": [],
                             "after": [a or b for a, b in revised if b and has_content_revisions(out_xml[b])]}
    for a, b in revised:
        for which, view in (("accepted", accept_view), ("rejected", reject_view)):
            v_before = blocks(view(src_xml[a])) if a else []
            v_after = blocks(view(out_xml[b])) if b else []
            for ch in diff_blocks(v_before, v_after):
                if ch.before != ch.after:
                    views[which].append({"part": a or b, "before": ch.before, "after": ch.after,
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
    if rewritten:
        rec.set(CheckResult("V4", "Revision ids", "NA",
                            f"N/A — Word wrote this copy's revision ids itself; tracked changes "
                            f"{sum(cnt_b.values())} before, {sum(cnt_a.values())} after",
                            {"counts_before": cnt_b, "counts_after": cnt_a}))
    else:
        rec.set(_v4(cnt_b, cnt_a, duplicated, reused, unreadable, v4_bad))
    _schema_checks(rec, src, out, src_xml, out_xml, changed, added, broken, rewritten)
    rec.set(CheckResult("V8", "Accept / reject simulation", "NA",
                        "N/A — nothing was predicted: V2 lists what the accept-all and reject-all views add and remove"))
    rec.set(CheckResult("V10", "Property assertions", "NA",
                        "N/A — Word's actions assert nothing: what they changed is under D1" if rewritten else
                        "N/A — a custom method in Obliterate mode asserts nothing: what it changed is under D1"))

    # D1 — everything else that differs, in plain sentences
    lines, data = _what_changed(src, out, src_xml, out_xml, changed, added, removed, orphaned, actual, cnt_b, cnt_a,
                                pairs if rewritten else None)
    rec.set(CheckResult("D1", "What changed", "NA", " ".join(lines)[:1200], data))

    rec.set(CheckResult("V6", "Automated render comparison", "NA", "no renderer run yet"))
    rec.set(CheckResult("V7", "Geometry assertion", "NA", "no renderer run yet"))
    rec.set(CheckResult("V9", "Overwrite guard", "NA", "not delivered yet"))
    return rec


def _v4(cnt_b, cnt_a, duplicated, reused, unreadable, v4_bad) -> CheckResult:
    return (CheckResult(
        "V4", "Revision ids", "FAIL" if v4_bad else "PASS",
        f"tracked changes {sum(cnt_b.values())} before, {sum(cnt_a.values())} after"
        + (f"; ids the method DUPLICATED {duplicated[:5]}" if duplicated else "; ids unique")
        + (f"; NEW ids that reuse an annotation id of the original {reused[:5]}" if reused else "")
        + (f"; NEW ids Word cannot read (not a whole number up to {WORD_ID_LIMIT}) {unreadable[:5]}"
           if unreadable else ""),
        {"counts_before": cnt_b, "counts_after": cnt_a, "duplicate_ids_all_kinds": duplicated,
         "new_ids_reusing_source_annotation_ids": reused, "new_ids_out_of_range": unreadable}))


def _schema_checks(rec: VerificationRecord, src: VPackage, out: VPackage, src_xml, out_xml, changed, added, broken,
                   rewritten: bool) -> None:
    """V5a, V5b and V5c. For a copy Word saved, V5a and V5c are N/A: the markup is Word's own writing."""
    if rewritten:
        rec.set(CheckResult("V5a", "XSD vs source baseline", "NA",
                            "N/A — Word wrote every part of this copy itself; the schema check is of this app's "
                            "own writing"))
        rec.set(CheckResult("V5b", "Untracked-edit check", "NA",
                            "N/A — nothing was predicted: the words that changed are listed under V2"))
        rec.set(CheckResult("V5c", "Revision grammar", "NA",
                            "N/A — the tracked-change markup of this copy is Word's own writing"))
        return
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


def _what_changed(src: VPackage, out: VPackage, src_xml, out_xml, changed, added, removed, orphaned, actual,
                  cnt_b: Dict[str, int], cnt_a: Dict[str, int],
                  pairs: Any = None) -> Tuple[List[str], Dict[str, Any]]:
    """D1's sentences and data: what differs beside the words (V2 lists those). ``pairs``: the text parts of a copy
    Word saved, each beside the one in its place (``_paired``); the report then compares by content, without Word's
    own save marks (``_plain``), and says so first."""
    rewritten = pairs is not None
    form = _plain if rewritten else _canonical
    lines: List[str] = [SAVED_AGAIN] if rewritten else []
    # the text parts to compare pair by pair: a copy Word saved has every one, whatever its bytes or its name
    both = ([(a, b) for a, b in pairs if a and b] if rewritten else
            [(part, part) for part in sorted(set(src_xml) & set(out_xml)) if part in changed])
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
    for a, b in both:
        n = _formatting_only(src_xml[a], out_xml[b], form)
        if n:
            formatting[a] = n
            lines.append(f"In {_part_words(src, a)}, the formatting or markup of {_count(n, 'paragraph', 'paragraphs')} "
                         f"changed without changing {'its' if n == 1 else 'their'} words.")

    # tables and page setup: what a paragraph-by-paragraph reading does not see
    tables: Dict[str, int] = {}
    page_setup = 0
    for a, b in both:
        n = _paired_changes(list(src_xml[a].iter(W + "tbl")), list(out_xml[b].iter(W + "tbl")),
                            lambda table: _table_shape(table, form))
        if n:
            tables[a] = n
            lines.append(f"In {_part_words(src, a)}, the layout or formatting of {_count(n, 'table', 'tables')} "
                         f"changed (widths, borders, shading, or row and cell settings).")
        if b == out.main_part():
            # (a copy Word saved: which header a section uses is said by the pairing, not by a relationship id
            # Word gave again)
            page_setup = _paired_changes(list(src_xml[a].iter(W + "sectPr")), list(out_xml[b].iter(W + "sectPr")),
                                         form)
            if page_setup:
                lines.append(f"Page setup: the settings of {_count(page_setup, 'section', 'sections')} changed (page "
                             f"size, margins, columns, or which headers and footers it uses).")

    # styles, numbering, settings: read by content
    styles: Dict[str, List[str]] = {}
    s_name, s_before = _related_xml(src, STYLES_REL)
    o_name, s_after = _related_xml(out, STYLES_REL)
    described |= {n for n in (s_name, o_name) if n}
    if s_before is not None and s_after is not None and (s_name in changed or s_name != o_name):
        styles = _keyed_difference(_children_by(s_before, "style", "styleId", form),
                                   _children_by(s_after, "style", "styleId", form))
        said = [f"{len(styles[k])} {k} ({_named(styles[k])})" for k in ("added", "removed", "changed") if styles[k]]
        if rewritten:
            # Word writes the part again, so its bytes alone say nothing: what it holds beside the style definitions
            # is read too — the document's default formatting — and only Word's own latent-styles list is set aside
            styles["outside"] = _outside_changed(s_before, s_after, ("style",), _SAVE_STYLES)
            if styles["outside"]:
                said.append("changed outside the style definitions: "
                            + _named([_OUTSIDE_WORDS.get(n, n) for n in styles["outside"]]))
        if said or not rewritten:
            lines.append("Styles: " + ("; ".join(said) + "." if said else
                                       "the styles part changed outside its style definitions (defaults or latent "
                                       "styles)."))
    numbering: Dict[str, List[str]] = {}
    n_name, n_before = _related_xml(src, NUMBERING_REL)
    on_name, n_after = _related_xml(out, NUMBERING_REL)
    described |= {n for n in (n_name, on_name) if n}
    if (n_name or on_name) and (n_name in changed or n_name != on_name or on_name in added or n_name in removed):
        b_defs = {**{"list " + k: v for k, v in _children_by(n_before, "num", "numId", form).items()},
                  **{"definition " + k: v
                     for k, v in _children_by(n_before, "abstractNum", "abstractNumId", form).items()}} \
            if n_before is not None else {}
        a_defs = {**{"list " + k: v for k, v in _children_by(n_after, "num", "numId", form).items()},
                  **{"definition " + k: v
                     for k, v in _children_by(n_after, "abstractNum", "abstractNumId", form).items()}} \
            if n_after is not None else {}
        numbering = _keyed_difference(b_defs, a_defs)
        said = [f"{len(numbering[k])} {k}" for k in ("added", "removed", "changed") if numbering[k]]
        if rewritten and n_before is not None and n_after is not None:
            numbering["outside"] = _outside_changed(n_before, n_after, ("num", "abstractNum"), _SAVE_NUMBERING)
            if numbering["outside"]:
                said.append("changed outside the lists and their definitions: "
                            + _named([_OUTSIDE_WORDS.get(n, n) for n in numbering["outside"]]))
        if said or not rewritten:
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
                    if rewritten and name in _SAVE_SETTINGS:
                        continue                 # Word's own bookkeeping of a save, never a setting of the document
                    found[name] = found.get(name, b"") + form(el)
            return found
        settings = _keyed_difference(top(t_before), top(t_after))
        said = [f"{k} {_named(settings[k])}" for k in ("added", "removed", "changed") if settings[k]]
        if said or not rewritten:
            lines.append("Document settings: " + ("; ".join(said) + "." if said else "the settings part changed."))

    # relationships and the parts themselves; an outside address is read as it is written, so a hyperlink that now
    # leads somewhere else is said with both addresses
    known = _renamed(src, out, pairs) if rewritten else {}
    r_before, r_after = _relationships(src), _relationships(out, known)
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

    counted = len(lines)                 # what was said so far is what this report reads by content
    def sized(pkg: VPackage, names: List[str]) -> List[str]:
        return [f"{n} ({len(pkg.read(n)):,} bytes)" for n in names]
    # (a copy Word saved: a part that is the original's under another name neither came nor went)
    plain_added = [n for n in added if not n.endswith(".rels") and n not in known]
    plain_removed = [n for n in removed if not n.endswith(".rels") and n not in set(known.values())]
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
    elif rewritten and counted == 1 and not actual and not plain_added and not plain_removed:
        lines.insert(1, "Nothing this report counts differs: the same words, paragraphs, tables, pictures, styles, "
                        "numbering, settings, relationships and tracked changes.")
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
