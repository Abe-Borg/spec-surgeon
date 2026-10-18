"""Read-only, source-bound specification structure evidence.

Roles are proposals, never edit authority. Only ordinary main-story paragraphs are
classified. Every paragraph keeps the exact ordinal used by ``ooxml.paragraphs``;
unsupported or ambiguous content receives an explicit disposition. Nothing here
is imported by the independent verifier.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict

from .ooxml import paragraphs, revision_kind
from .ops.lists import ListDefs, count, paragraph_numbering
from .ops.markup import Look
from .ops.selectors import StyleSheet
from .package import DocxPackage, W

SCHEMA_VERSION = 1
ANALYZER_VERSION = "1"
ROLES = ("section_id", "section_title", "part", "article", "paragraph", "subparagraph",
         "subparagraph_2", "subparagraph_3", "subparagraph_4", "end_of_section")
_NUMBER = r"[0-9]{2}[ \u00a0–—-]*[0-9]{2}[ \u00a0–—-]*[0-9]{2}(?:\.[0-9]{2})?"
_SECTION = re.compile(r"^SECTION\s+(" + _NUMBER + r")(?:\s*[:–—-]\s*|\s+)?(.*?)$")
_BARE_SECTION = re.compile(r"^(" + _NUMBER + r")(?:\s*[:–—-]\s*|\s+)?(.*?)$")
_PART = re.compile(r"^PART\s+([1-9][0-9]*)(?:\s*[-–—:]\s*|\s+|$)(.*)$", re.I)
_ARTICLE = re.compile(r"^(?:ARTICLE\s+)?([1-9][0-9]*\.[0-9]+)\.?\s+(\S.*)$", re.I)
_END = re.compile(r"^END\s+OF\s+(?:SECTION|SPECIFICATION)\b(?:\s+" + _NUMBER + r")?\s*[.]?$", re.I)
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_ROMAN = re.compile(r"[IVXLCDMivxlcdm]+\Z")
_DEPTH_ROLES = {3: "paragraph", 4: "subparagraph", 5: "subparagraph_2",
                6: "subparagraph_3", 7: "subparagraph_4"}


def _paragraph_text(p):
    """Full text, retaining deleted wording as evidence on excluded revisions."""
    pieces = []
    for el in p.iter(W + "t", W + "delText", W + "tab", W + "br", W + "cr", W + "noBreakHyphen"):
        if next((a for a in el.iterancestors() if a.tag == W + "p"), None) is not p:
            continue
        if el.tag not in (W + "t", W + "delText") and el.getparent().tag != W + "r":
            continue
        pieces.append(el.text or "" if el.tag in (W + "t", W + "delText") else
                      "\t" if el.tag == W + "tab" else "‑" if el.tag == W + "noBreakHyphen" else "\n")
    return "".join(pieces)


def _exclusion(p):
    ancestors = list(p.iterancestors())
    if any(a.tag == W + "tbl" for a in ancestors):
        return "table paragraph: table structure is outside this analyzer's coverage"
    if any(a.tag in (W + "txbxContent", W + "p", _MC + "AlternateContent") for a in ancestors):
        return "textbox or alternate-content paragraph is outside coverage"
    if any(el.tag in (W + "drawing", W + "pict", W + "object", _MC + "AlternateContent") for el in p.iter()):
        return "drawing, object or alternate-content host paragraph is outside coverage"
    if any(revision_kind(el) for el in p.iter()) or any(revision_kind(a) for a in ancestors):
        return "pending revision: accept/reject views may have different structure"
    if any(el.tag in (W + "fldChar", W + "fldSimple", W + "instrText") for el in p.iter()):
        return "field-bearing paragraph: displayed field results are not structural evidence"
    if not any(a.tag == W + "body" for a in ancestors):
        return "paragraph outside the main document body"
    return None


def _section_id(value):
    compact = re.sub(r"[ \u00a0–—-]", "", value)
    return f"{compact[:2]} {compact[2:4]} {compact[4:]}"


def canonical_reference(value):
    """Normalize decimal components without conflating upper/lower case clauses."""
    return ".".join(str(int(x)) if x.isdecimal() else x for x in value.rstrip(".").split("."))


def word_location(row):
    """A findable location plus the exact technical paragraph identity."""
    pieces = []
    if row.get("section_id"):
        pieces.append("Section " + row["section_id"])
    if row.get("article_label"):
        pieces.append("Article " + row["article_label"])
    text = " ".join(row.get("text", "").split())
    if text:
        pieces.append('beginning “' + text[:100] + ('…' if len(text) > 100 else '') + '”')
    pieces.append(f"{row['part']} paragraph {row['paragraph'] + 1}")
    return "; ".join(pieces)


def _provision(text, fmt=None, previous=None):
    """Return observed depth, marker, and wording, without inventing a parent."""
    for pattern, depth in ((r"^([A-Z])\.\s+(\S.*)$", 3),
                           (r"^([0-9]+)\.\s+(\S.*)$", 4),
                           (r"^([a-z])\.\s+(\S.*)$", 5),
                           (r"^\(([0-9]+)\)\s+(\S.*)$", 6),
                           (r"^\(([a-z])\)\s+(\S.*)$", 7)):
        match = re.match(pattern, text)
        if match:
            prior = (previous or {}).get(depth)
            sequential_letter = (isinstance(prior, str) and len(prior) == len(match[1]) == 1
                                 and prior.isalpha() and ord(match[1]) == ord(prior) + 1)
            if _ROMAN.fullmatch(match[1]) and fmt not in ("upperLetter", "lowerLetter") and not sequential_letter:
                return None, None, None, "letter marker is also a Roman numeral; its role needs review"
            return depth, match[1], match[2], None
    return None, None, None, None


def analyze_structure(pkg: DocxPackage) -> dict:
    """Analyze immutable source bytes; analysis is evidence for a reviewed proposal.

    ``paragraph`` and ``parent`` are zero-based main-part paragraph ordinals. A
    parent is an occurrence, not a potentially repeated label. ``reference_id``
    is section-local; repeated labels explicitly lose hierarchy reliability.
    ``text`` is never truncated, while ``content`` removes only a recognized
    typed marker (automatic markers were never in the source text).
    """
    if pkg.is_modified():
        raise ValueError("structure analysis requires unmodified source bytes")
    main = pkg.main_part()
    root = pkg.xml(main)
    sheet = StyleSheet(pkg)
    look = Look(sheet)
    definitions = ListDefs.load(pkg)
    numbered = {p.index: p for p in count(root, sheet, definitions)}
    rows, stack = [], {}
    revised_lists = set()
    section, section_paragraph, part_label, article_label = None, None, None, None
    pending_title = None
    first_nonblank = True
    for index, p in enumerate(paragraphs(root)):
        text = _paragraph_text(p)
        row = {"part": main, "paragraph": index, "text": text, "content": text,
               "role": None, "disposition": "unresolved", "evidence": [],
               "reason": "no unambiguous supported specification marker",
               "style_id": sheet.paragraph_style_id(p), "parent": None, "depth": None,
               "section_id": section, "section_paragraph": section_paragraph,
               "article_label": article_label, "label": None, "reference_id": None,
               "hierarchy_reliable": False}
        excluded = _exclusion(p)
        line = text.strip()
        num = numbered.get(index)
        # Resolve effective numbering before considering any typed prefix. A
        # dangling/unsupported list must not accidentally become a typed heading.
        numbering = paragraph_numbering(p, sheet, definitions)
        if numbering is not None:
            row["numbering"] = {"num_id": numbering.num_id, "level": numbering.ilvl,
                                "source": numbering.source, "marker": num.text if num else None,
                                "format": num.fmt if num else None}
        if excluded is None:
            runs = [r for r in p.iter(W + "r") if next((a for a in r.iterancestors() if a.tag == W + "p"), None) is p]
            properties = [r.find(W + "rPr") for r in runs] + [p.find(W + "pPr/" + W + "rPr")]
            if any(look.hidden(pr, p) is not False for pr in properties):
                excluded = "hidden text or ambiguous effective visibility is outside structural coverage"
        if numbering is not None and (any(revision_kind(el) for el in p.iter()) or
                                      any(revision_kind(el) for el in p.iterancestors())):
            revised_lists.add(numbering.num_id)
        if excluded:
            row.update(disposition="excluded", reason=excluded)
            pending_title = None
            if "table paragraph" not in excluded and line:
                stack = {k: v for k, v in stack.items() if k < 2}
                article_label = None
        elif not line and num is None:
            row.update(disposition="excluded", reason="empty paragraph")
        elif numbering is not None and (num is None or not num.evaluated):
            row["reason"] = "automatic numbering is unresolved: " + (
                num.not_evaluated if num is not None else numbering.unreadable or "missing or dangling list definition")
            pending_title = None
            stack = {k: v for k, v in stack.items() if k < 2}
            article_label = None
        elif num is not None and num.num_id in revised_lists:
            row["reason"] = "earlier pending revisions can change this list's displayed numbers"
            pending_title = None
            stack = {k: v for k, v in stack.items() if k < 2}
            article_label = None
        else:
            if num is not None:
                row["numbering"] = {"num_id": num.num_id, "level": num.ilvl, "marker": num.text,
                                    "format": num.fmt, "source": num.source}
                row["evidence"].append("effective automatic numbering")
                # Only supported punctuation/patterns assign semantic roles. A
                # bullet or Roman list is deliberately not guessed from ilvl.
                line = ((num.text or "") + " " + text).strip()
            section_match = _SECTION.fullmatch(line)
            if section_match is None and first_nonblank and num is None:
                section_match = _BARE_SECTION.fullmatch(line)
            part_match, article_match = _PART.fullmatch(line), _ARTICLE.fullmatch(line)
            if section_match:
                section = _section_id(section_match[1])
                section_paragraph = index
                part_label = article_label = None
                stack = {0: index}
                pending_title = index if not section_match[2].strip() else None
                row.update(role="section_id", depth=0, label=section, section_id=section,
                           section_paragraph=index, article_label=None, content=section_match[2].strip(),
                           hierarchy_reliable=True)
            elif _END.fullmatch(line):
                row.update(role="end_of_section", depth=0, parent=section_paragraph,
                           hierarchy_reliable=section_paragraph is not None)
                stack, pending_title = {}, None
                section = section_paragraph = part_label = article_label = None
            elif part_match:
                part_label, article_label = str(int(part_match[1])), None
                row.update(role="part", depth=1, label=part_label, parent=section_paragraph,
                           article_label=None, content=text if num else part_match[2], hierarchy_reliable=True)
                stack = {k: v for k, v in stack.items() if k == 0} | {1: index}
                pending_title = None
            elif article_match:
                label = canonical_reference(article_match[1])
                if part_label is not None and label.split(".")[0] != part_label:
                    row["reason"] = "article number disagrees with the current PART"
                    stack = {k: v for k, v in stack.items() if k < 2}
                    article_label = None
                else:
                    article_label = label
                    row.update(role="article", depth=2, label=label, article_label=label,
                               reference_id=label, parent=stack.get(1, section_paragraph),
                               content=text if num else article_match[2], hierarchy_reliable=True)
                    stack = {k: v for k, v in stack.items() if k < 2} | {2: index}
                pending_title = None
            elif (pending_title is not None and num is None and line.isupper() and any(c.isalpha() for c in line)
                  and not re.match(r"^(?:[A-Z]|[0-9]+)[.)]\s|^\([A-Za-z0-9]+\)", line)):
                # A title is the next nonblank, plain uppercase line, not an
                # arbitrary prose paragraph following a section number.
                row.update(role="section_title", depth=0, parent=pending_title, hierarchy_reliable=True)
                pending_title = None
            else:
                depth, label, content, ambiguity = _provision(
                    line, num.fmt if num else None, {k: rows[v]["label"] for k, v in stack.items() if k >= 3})
                pending_title = None
                if ambiguity:
                    row["reason"] = ambiguity
                    stack = {k: v for k, v in stack.items() if k < 3}
                elif depth is not None:
                    parent = stack.get(depth - 1)
                    if parent is None:
                        row["reason"] = "recognized marker lacks an unambiguous immediate parent"
                        stack = {k: v for k, v in stack.items() if k < depth}
                    else:
                        ref = rows[parent]["reference_id"]
                        row.update(role=_DEPTH_ROLES[depth], depth=depth, label=label, parent=parent,
                                   content=text if num else content, reference_id=f"{ref}.{label}" if ref else None,
                                   hierarchy_reliable=rows[parent]["hierarchy_reliable"])
                        stack = {k: v for k, v in stack.items() if k < depth} | {depth: index}
                elif num is not None:
                    row["reason"] = "automatic numbering pattern has no supported unambiguous specification role"
                    stack = {k: v for k, v in stack.items() if k < 3}
                else:
                    # An unmarked line might be a heading. Do not let subsequent
                    # clauses silently inherit a parent across that uncertainty.
                    stack = {k: v for k, v in stack.items() if k < 2}
                    article_label = None
            if row["role"]:
                row.update(disposition="classified", reason="recognized specification marker and context")
                row["evidence"].append("typed specification marker" if num is None else "rendered list marker")
                if row["role"] == "section_title":
                    row["evidence"][-1] = "uppercase title immediately following section identity"
            first_nonblank = False
        rows.append(row)

    # A duplicate logical address is never an unambiguous target. Descendants
    # inherit their parent's ambiguity, even when their own labels are unique.
    addresses = defaultdict(list)
    for row in rows:
        if row["reference_id"]:
            addresses[(row["section_paragraph"], row["reference_id"])].append(row)
    for matches in addresses.values():
        if len(matches) > 1:
            for row in matches:
                row["hierarchy_reliable"] = False
                row["evidence"].append("repeated logical label within this section")
    for row in rows:
        if row["parent"] is not None and not rows[row["parent"]]["hierarchy_reliable"]:
            row["hierarchy_reliable"] = False
        row["location"] = word_location(row)
    coverage = dict(Counter(row["disposition"] for row in rows))
    coverage = {key: coverage.get(key, 0) for key in ("classified", "unresolved", "excluded")} | {"total": len(rows)}
    return {"schema_version": SCHEMA_VERSION, "analyzer_version": ANALYZER_VERSION,
            "source_sha256": hashlib.sha256(pkg.data).hexdigest(), "main_part": main,
            "paragraphs": rows, "coverage": coverage, "report_only": True,
            "limitations": ["Roles are reviewable evidence, not authorization to edit.",
                            "Only ordinary main-body paragraphs with supported markers are classified.",
                            "Tables, drawings, textboxes, fields, hidden text and pending revisions are excluded.",
                            "Unsupported numbering, ambiguous Roman markers and unmarked prose remain unresolved.",
                            "Headers, footers, notes and comments are outside structural coverage."]}
