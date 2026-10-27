"""Read-only, source-bound specification structure evidence.

Roles are proposals, never edit authority. Only ordinary main-story paragraphs are
classified. Every paragraph keeps the exact ordinal used by ``ooxml.paragraphs``;
unsupported or ambiguous content receives an explicit disposition. ``role_summary``
condenses that analysis into how one file encodes each role (styles, typed or
automatic markers, examples) and the concrete selector a planner can write for it
when the file makes one unambiguous. A paragraph with a wording redline takes part
when it reads alike with its changes accepted and with them rejected; a revision that
can change a list number or the paragraph's existence (a paragraph mark inserted,
deleted or moved, a tracked paragraph-property change, a numbering change) excludes it
and unsettles its list. Nothing here is imported by the independent verifier.
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
ANALYZER_VERSION = "2"
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
# The two readings that make a paragraph something other than a role-shaped line with an uncertain context: unmarked
# wording, and an automatic list of a pattern no role covers (a bullet, a Roman list). role_summary treats a style or
# marker shape these paragraphs share with a role as ambiguous; a missing parent or a pending revision does not.
NO_MARKER = "no unambiguous supported specification marker"
UNSUPPORTED_LIST = "automatic numbering pattern has no supported unambiguous specification role"
# The exclusions a pending revision brings: one that can change the paragraph's existence or its number, and a
# wording redline whose two views read as different things. A wording redline that reads alike in both takes part.
STRUCTURAL_REVISION = "pending revision: accept/reject views may have different structure"
VIEWS_DIFFER = ("pending revision: the wording reads as one thing with its changes accepted and as another with "
                "them rejected")
# The revision kinds (ooxml.revision_kind) that can change no list number and no paragraph's existence: wording
# inserted, deleted or moved inside the paragraph, a run's or the mark's formatting change, a section property
# change. Every other kind is structural — a paragraph mark inserted, deleted or moved, a tracked paragraph-property
# change (its old properties may have numbered the paragraph otherwise), a numbering change, a table revision — and
# so is a revision wrapper around the paragraph: the accept and reject views may not hold the same paragraphs.
WORDING_REVISIONS = frozenset({"ins", "del", "moveFrom", "moveTo", "rPrChange", "sectPrChange"})
# The revision wrappers a view leaves out: with every change accepted, deleted and moved-away wording is gone; with
# every change rejected, inserted and moved-in wording is.
_LEFT_OUT = {None: (), "accept": (W + "del", W + "moveFrom"), "reject": (W + "ins", W + "moveTo")}
_MARKER_LIKE = re.compile(r"^(?:[A-Z]|[0-9]+)[.)]\s|^\([A-Za-z0-9]+\)")


def _paragraph_text(p, view=None):
    """Full text — the kept and the deleted wording together, as a selector's regex meets it
    (``ooxml.block_full_text``) and as the evidence on an excluded revision — or, with ``view`` ``"accept"`` or
    ``"reject"``, the text as Word shows it with every pending change accepted or with every one rejected."""
    pieces = []
    left_out = _LEFT_OUT[view]
    for el in p.iter(W + "t", W + "delText", W + "tab", W + "br", W + "cr", W + "noBreakHyphen"):
        if next((a for a in el.iterancestors() if a.tag == W + "p"), None) is not p:
            continue
        if el.tag not in (W + "t", W + "delText") and el.getparent().tag != W + "r":
            continue
        if left_out and any(a.tag in left_out for a in _within(el, p)):
            continue
        pieces.append(el.text or "" if el.tag in (W + "t", W + "delText") else
                      "\t" if el.tag == W + "tab" else "‑" if el.tag == W + "noBreakHyphen" else "\n")
    return "".join(pieces)


def _within(el, p):
    """``el``'s ancestors below the paragraph ``p``."""
    for a in el.iterancestors():
        if a is p:
            return
        yield a


def _revision(p):
    """None, ``"wording"`` or ``"structural"``: the pending revisions ``p`` carries, by what they can change
    (``WORDING_REVISIONS``)."""
    if any(revision_kind(a) for a in p.iterancestors()):
        return "structural"
    kinds = {revision_kind(el) for el in p.iter()} - {None}
    if not kinds:
        return None
    return "wording" if kinds <= WORDING_REVISIONS else "structural"


def _exclusion(p, revision):
    """Why ``p`` is outside structural coverage, or None; ``revision`` is ``_revision(p)``."""
    ancestors = list(p.iterancestors())
    if any(a.tag == W + "tbl" for a in ancestors):
        return "table paragraph: table structure is outside this analyzer's coverage"
    if any(a.tag in (W + "txbxContent", W + "p", _MC + "AlternateContent") for a in ancestors):
        return "textbox or alternate-content paragraph is outside coverage"
    if any(el.tag in (W + "drawing", W + "pict", W + "object", _MC + "AlternateContent") for el in p.iter()):
        return "drawing, object or alternate-content host paragraph is outside coverage"
    if revision == "structural":
        return STRUCTURAL_REVISION
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


def _line(text, num):
    """The line the role decision reads: the rendered list marker, if any, before the text."""
    return ((num.text or "") + " " + text).strip() if num is not None else text.strip()


def _reading(line, num, first_nonblank, previous):
    """What the role decision in ``analyze_structure`` sees in ``line``, in the order it looks: the marker's kind and
    label, whether the line could be a title, nothing of the wording after the marker. Two views of a redlined
    paragraph with the same reading take the same role in the same context."""
    if not line and num is None:
        return ("empty",)
    section = _SECTION.fullmatch(line)
    if section is None and first_nonblank and num is None:
        section = _BARE_SECTION.fullmatch(line)
    if section:
        return ("section_id", _section_id(section[1]), bool(section[2].strip()))   # a bare number awaits its title
    if _END.fullmatch(line):
        return ("end_of_section",)
    part = _PART.fullmatch(line)
    if part:
        return ("part", str(int(part[1])))
    article = _ARTICLE.fullmatch(line)
    if article:
        return ("article", canonical_reference(article[1]))
    title = num is None and line.isupper() and any(c.isalpha() for c in line) and not _MARKER_LIKE.match(line)
    depth, label, _, ambiguity = _provision(line, num.fmt if num else None, previous)
    return ("provision", depth, label, ambiguity, title)


def analyze_structure(pkg: DocxPackage) -> dict:
    """Analyze immutable source bytes; analysis is evidence for a reviewed proposal.

    ``paragraph`` and ``parent`` are zero-based main-part paragraph ordinals. A
    parent is an occurrence, not a potentially repeated label. ``reference_id``
    is section-local; repeated labels explicitly lose hierarchy reliability.
    ``text`` is never truncated, while ``content`` removes only a recognized
    typed marker (automatic markers were never in the source text). A paragraph
    with a wording redline carries ``views`` (its text with every change accepted
    and with every one rejected) when both read alike; ``text`` keeps the kept and
    the deleted wording together, as a selector's regex meets it, and ``content``
    is the accepted view.
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
        revision = _revision(p)
        row = {"part": main, "paragraph": index, "text": text, "content": text,
               "role": None, "disposition": "unresolved", "evidence": [],
               "reason": NO_MARKER,
               "style_id": sheet.paragraph_style_id(p), "parent": None, "depth": None,
               "section_id": section, "section_paragraph": section_paragraph,
               "article_label": article_label, "label": None, "reference_id": None,
               "hierarchy_reliable": False}
        excluded = _exclusion(p, revision)
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
            # The formatting a tracked change replaced counts too: hidden with the change rejected is hidden.
            properties += [old for old in (pr.find(W + "rPrChange/" + W + "rPr") for pr in properties
                                           if pr is not None) if old is not None]
            if any(look.hidden(pr, p) is not False for pr in properties):
                excluded = "hidden text or ambiguous effective visibility is outside structural coverage"
        if numbering is not None and revision == "structural":
            revised_lists.add(numbering.num_id)
        for old in p.iterfind(W + "pPr/" + W + "pPrChange/" + W + "pPr/" + W + "numPr/" + W + "numId"):
            if (old.get(W + "val") or "").isdecimal():      # the list a tracked property change took it out of
                revised_lists.add(int(old.get(W + "val")))
        if excluded is None and revision == "wording":
            # Read with every change accepted and with every change rejected: the same reading is the same role.
            accept, reject = _paragraph_text(p, "accept"), _paragraph_text(p, "reject")
            previous = {k: rows[v]["label"] for k, v in stack.items() if k >= 3}
            if _reading(_line(accept, num), num, first_nonblank, previous) != \
                    _reading(_line(reject, num), num, first_nonblank, previous):
                excluded = VIEWS_DIFFER
            else:
                row["views"] = {"accept": accept, "reject": reject}
                row["evidence"].append("wording redline: reads alike with its changes accepted and rejected")
                row["content"] = text = accept
                line = accept.strip()
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
                  and not _MARKER_LIKE.match(line)):
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
                    row["reason"] = UNSUPPORTED_LIST
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
                            "Tables, drawings, textboxes, fields, hidden text and pending revisions that can change "
                            "the structure (a paragraph mark, a paragraph property, a number) are excluded; a wording "
                            "redline takes part when it reads alike with its changes accepted and rejected.",
                            "Unsupported numbering, ambiguous Roman markers and unmarked prose remain unresolved.",
                            "Headers, footers, notes and comments are outside structural coverage."]}


# ---- role summary: how this file encodes each role, and the selector a planner can write for it -------------------

EXAMPLE_CLIP = 80            # characters per example
EXAMPLE_SAMPLES = 2          # examples per role
MARKER_SAMPLES = 2           # markers shown per role (the regex is built from every distinct marker)
REASON_LIMIT = 6             # unresolved reasons listed, the most frequent first
STYLE_SELECTOR_LIMIT = 2     # style_in is offered for a role that shares at most this many styles
_TABLE_REASON = "table paragraph"
_SHAPE_TOKEN = re.compile(r"[0-9]+|[A-Z]+|[a-z]+|\s+|.", re.S)


def _clip_example(text: str, n: int = EXAMPLE_CLIP) -> str:
    """The paragraph's words with line breaks folded; tabs and non-breaking spaces stay (a planner's regex meets
    them); at most ``n`` characters."""
    s = re.sub(r"[\r\n]+", " ", (text or "").strip())
    return s if len(s) <= n else s[:n - 1] + "…"


def _marker_kind(row) -> str:
    """How the role's marker reaches the reader: typed in the text, rendered by a list, or none (a title)."""
    evidence = row.get("evidence") or []
    if "rendered list marker" in evidence:
        return "automatic"
    if "typed specification marker" in evidence:
        return "typed"
    return "none"


def _marker(row) -> str | None:
    """The marker as this file carries it: the rendered list text for automatic numbering, else the typed prefix
    (the stripped text up to the recognized content — ``1.01``, ``A.``, ``PART 1 -``, ``SECTION 21 13 13``)."""
    kind = _marker_kind(row)
    if kind == "automatic":
        return (row.get("numbering") or {}).get("marker") or None
    if kind != "typed":
        return None
    line = ((row.get("views") or {}).get("accept") or row.get("text") or "").strip()   # the view the content is from
    content = row.get("content") or ""
    if content and content != line and line.endswith(content):
        return line[:len(line) - len(content)].strip() or None
    return line or None


def _shape(marker: str):
    tokens = []
    for m in _SHAPE_TOKEN.finditer(marker):
        t = m.group()
        if t[0].isdigit():
            kind = "digits"
        elif t.isspace():
            kind = "space"
        elif t.isalpha():
            kind = "upper" if t.isupper() else "lower"
        else:
            kind = "punct"
        tokens.append((kind, t))
    return tokens


def marker_regex(markers) -> str | None:
    """A ``text_regex`` for the typed marker shape every one of ``markers`` shares, or None when they share none.

    Digit runs become ``\\d{n}`` when every marker has ``n`` digits there (``\\d`` for one), ``\\d+`` otherwise; a
    single letter becomes its class (``[A-Z]``, ``[a-z]``); a word every marker spells the same (``PART``, ``SECTION``)
    stays literal; punctuation must agree and is escaped; whitespace becomes ``\\s+``. The regex tolerates leading
    whitespace (a typed tab before the marker) and requires whitespace or the end of the text after it, so ``1.``
    never selects ``1.01``. Markers whose token shapes differ (``PART 1 -`` beside ``PART 2``) give None: a guess
    would select the wrong paragraphs."""
    shapes = [_shape(m) for m in markers if m]
    if not shapes or any(len(sh) != len(shapes[0]) for sh in shapes):
        return None
    out = [r"^\s*"]
    for position, (kind, _) in enumerate(shapes[0]):
        column = [sh[position] for sh in shapes]
        if any(k != kind for k, _ in column):
            return None
        values = {v for _, v in column}
        if kind == "space":
            out.append(r"\s+")
        elif kind == "digits":
            lengths = {len(v) for v in values}
            if lengths == {1}:
                out.append(r"\d")
            elif len(lengths) == 1:
                out.append(r"\d{%d}" % lengths.pop())
            else:
                out.append(r"\d+")
        elif kind == "punct":
            if len(values) != 1:
                return None
            out.append(re.escape(values.pop()))
        elif all(len(v) == 1 for v in values):
            out.append("[A-Z]" if kind == "upper" else "[a-z]")
        elif len(values) == 1:
            out.append(re.escape(values.pop()))
        else:
            return None
    out.append(r"(?:\s|$)")
    return "".join(out)


def _reason_head(reason: str) -> str:
    return reason.split(":", 1)[0].strip()


def _something_else(row) -> bool:
    """A paragraph the analyzer read as something other than this role: another role, unmarked wording, or an
    automatic list of an unsupported pattern. A recognized marker with an uncertain parent, a Roman-looking letter
    or a list disturbed by revisions keeps its shape; it is not evidence against a selector."""
    if row["disposition"] == "classified":
        return True
    return row["disposition"] == "unresolved" and row["reason"] in (NO_MARKER, UNSUPPORTED_LIST)


def role_summary(pkg: DocxPackage) -> dict:
    """How THIS file encodes each role, for a planner that must write concrete selectors per layout.

    ``roles``: per role present (in hierarchy order) its paragraph count, the paragraph styles it uses with counts,
    its numbering (``typed`` markers in the text, ``automatic`` list markers, ``mixed``, or ``none`` for a title), the
    first two distinct markers as the file carries them, and two examples of at most 80 characters (the source
    text: an automatic marker is not in it). ``unresolved``: how many main-body paragraphs the analyzer could not
    place and why (reasons condensed to their head). ``selectors``: per role, the selector that is unambiguous in
    this file — ``style_in`` when the role's paragraphs share one or two styles no paragraph read as something else
    uses; otherwise, when every marker is typed, a ``text_regex`` built from the markers' shared shape that selects
    no such paragraph; a role with neither is left out. Either gets ``in_table: false`` when a table cell would
    otherwise be selected too (tables are outside the analyzer's coverage). Descriptive only: nothing here plans an
    edit, and the analysis is read-only source evidence (``analyze_structure``)."""
    rows = analyze_structure(pkg)["paragraphs"]
    by_role: dict = {}
    for row in rows:
        if row["role"]:
            by_role.setdefault(row["role"], []).append(row)
    unresolved = [r for r in rows if r["disposition"] == "unresolved"]
    tables = [r for r in rows if r["disposition"] == "excluded" and r["reason"].startswith(_TABLE_REASON)]
    roles: dict = {}
    selectors: dict = {}
    for role in ROLES:
        members = by_role.get(role)
        if not members:
            continue
        kinds = Counter(_marker_kind(r) for r in members)
        numbering = ("mixed" if kinds["typed"] and kinds["automatic"] else "typed" if kinds["typed"]
                     else "automatic" if kinds["automatic"] else "none")
        markers: list = []
        for r in members:
            m = _marker(r)
            if m and m not in markers:
                markers.append(m)
        styles = Counter(r["style_id"] or "(none)" for r in members)
        roles[role] = {"count": len(members), "styles": dict(styles.most_common()), "numbering": numbering,
                       "markers": markers[:MARKER_SAMPLES],
                       "examples": [_clip_example(r["text"]) for r in members[:EXAMPLE_SAMPLES]]}
        others = [r for r in rows if r["role"] != role and _something_else(r)]
        selector = None
        own = [sid for sid, _ in styles.most_common()]
        if len(own) <= STYLE_SELECTOR_LIMIT and all(r["style_id"] for r in members) \
                and not any(r["style_id"] in own for r in others):
            selector = {"style_in": own}
            if any(r["style_id"] in own for r in tables):
                selector["in_table"] = False
        elif numbering == "typed":
            rx = marker_regex(markers)
            if rx is not None:
                try:
                    compiled = re.compile(rx)
                except re.error:
                    compiled = None
                if compiled is not None and all(compiled.search(r["text"]) for r in members) \
                        and not any(compiled.search(r["text"]) for r in others):
                    selector = {"text_regex": rx}
                    if any(compiled.search(r["text"]) for r in tables):
                        selector["in_table"] = False
        if selector:
            selectors[role] = selector
    reasons = Counter(_reason_head(r["reason"]) for r in unresolved)
    return {"roles": roles,
            "unresolved": {"count": len(unresolved), "reasons": dict(reasons.most_common(REASON_LIMIT))},
            "selectors": selectors}
