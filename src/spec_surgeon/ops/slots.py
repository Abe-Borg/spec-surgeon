"""Template slots and fills (transform side). The verifier re-implements detection and fill extraction in
verify/template.py; CLAUDE.md lists both definitions among those that must stay identical.

Slot markers (decision §14.2 — both conventions):

* ``highlight`` — a maximal sequence of consecutive runs (in the paragraph's run order) whose direct rPr has
  ``w:highlight`` in ``highlight_colors`` (default yellow). Slot name: ``NAME`` when the highlighted text, stripped,
  is ``{{NAME}}``; otherwise the stripped highlighted text itself (``"XX XX XX"``, ``"ISSUED FOR PERMIT"``).
* ``braces`` — every ``{{NAME}}`` (``NAME`` = letters, digits, underscore) in the paragraph's display text, even
  when rsid churn split it over several runs.

A braces placeholder inside a highlight span is the same slot only when the highlighted text is exactly that
placeholder; anything else overlapping is refused. Slot runs (and runs a slot boundary cuts through) may hold only
``w:rPr`` and ``w:t``; slots may not sit in nested (textbox) paragraphs.

* ``reference`` (used alone) — the template is an ordinary spec (e.g. the architect's), not a prepared template. Each
  fill rule NAME → rule also has a **template value**: the rule's ``in_template`` text when given, else the value
  the rule extracts from the TEMPLATE document itself (``extract_fill`` on the template; a rule containing a literal
  needs ``in_template``). Values are looked for in a paragraph's **located text**: its display text with a separator
  (``SEP``) wherever a display run holds anything other than ``w:rPr``/``w:t`` (a tab, break, drawing, field
  character…). An occurrence is a match of ``value_pattern`` (the value's words in order, whitespace between them,
  case-insensitive; never across a separator) that is ``bounded``: on each side the next character is absent,
  whitespace, a separator, or a mark that does not itself touch a letter/digit/underscore on its far side ("08 71
  00" in "08 71 00.01", "SECTION08 71 00" or "08 71 00/DOOR" is not an occurrence). Every occurrence is a slot NAME.
  Refused: a template value with fewer than 3 letters/digits (``template-slot``); occurrences of two names
  overlapping (``slot-overlap``); a slot in a field (``slot-in-field``: any run between a complex field's begin and
  end, counted over the whole part in document order, or inside ``w:fldSimple`` — Word would show the field's own
  result again on update; the same refusal holds for a highlight or braces slot a field shows, e.g. a
  ``{{SECTION_TITLE}}`` typed as a STYLEREF field's cached result); a template value left anywhere else in the copied parts in another form
  (``template-value-left``: in the located text between the slots of any paragraph, consecutive tokens — maximal
  runs of digits or of letters, casefolded, never across a separator — whose concatenation holds the value's
  tokens' concatenation, starting and ending at token edges or inside letter tokens, never inside a digit token:
  "087100", "08-71-00", "SECTION08 71 00" for "08 71 00", "DOORHARDWARECONSULTANT" for "DOOR HARDWARE"), because
  it would be copied unchanged into every spec. Unlike highlight/braces slots, the runs a reference slot touches may hold other
  content (a tab after the title, say): it stays, in order, in runs of its own beside the slot's run (``carve``).

Display text of a paragraph: its ``w:t`` (not ``w:delText``, not field instructions) whose nearest ``w:p`` is the
paragraph, outside ``w:del``/``w:moveFrom``, in document order. Fill extraction reads it; slot offsets use it.

Fill rules, evaluated on the TARGET (source package):

* ``literal`` — ``value`` verbatim (the user's approved words; makes the op ``untracked`` mode);
* ``regex`` — ``pattern`` searched in the display text of every paragraph of ``source`` (``document`` or
  ``header.<type>`` / ``footer.<type>``, optional ``section``); group 1 if the pattern has groups, else the match;
  at least one match and all matches equal (unless ``occurrence: "first"``);
* ``paragraph_after`` — paragraph(s) of the document match ``anchor_regex``; the fill is the display text of the
  next paragraph whose display text is not blank (when multiple anchors match, allowed if all following non-blank
  paragraphs have identical text or ``occurrence: "first"``);
* ``first_match`` (or ``{"rules": [...]}``) — tries each rule in ``rules`` in order and returns the first one that
  extracts a non-empty fill (syntax/bad-params errors in any sub-rule are raised immediately).

``case_insensitive`` (default false) controls regex and anchor matching. With ``flexible_whitespace`` (default true),
matching can use a whitespace-normalized view, but captures map back to exact source spans: internal spaces, NBSPs
and case are preserved. Fills are stripped only at their edges (``strip``, default true) and must be non-empty.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import clone_run_shell, paragraphs, set_text
from .base import JobException
from .hf_common import KINDS, TYPES, effective_refs

BRACES = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
TEXT_ONLY = (W + "rPr", W + "t")


@dataclass
class Slot:
    name: str
    marker: str                 # highlight | braces | both
    start: int                  # display-text offsets within the paragraph
    end: int
    placeholder: str


def nearest_p(el):
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def paragraph_runs(p) -> List[Any]:
    return [r for r in p.iter(W + "r") if nearest_p(r) is p]


def display_segments(p) -> Tuple[str, List[Tuple[Any, Any, int, int]]]:
    """(display text, [(w:t, run, start, end)])."""
    segs, pos, parts = [], 0, []
    for r in paragraph_runs(p):
        q, deleted = r.getparent(), False
        while q is not None and q is not p:
            if q.tag in (W + "del", W + "moveFrom"):
                deleted = True
                break
            q = q.getparent()
        if deleted:
            continue
        for t in r.findall(W + "t"):
            text = t.text or ""
            segs.append((t, r, pos, pos + len(text)))
            parts.append(text)
            pos += len(text)
    return "".join(parts), segs


def display_text(p) -> str:
    return display_segments(p)[0]


def _norm_ws(s: str) -> str:
    return re.sub(r"[\s\u00a0]+", " ", s or "")


def _fill_match(rx, text: str, flexible: bool):
    """Match a normalized view, but return only the exact original source span."""
    match = rx.search(text)
    if match is not None:
        return match.group(1) if rx.groups else match.group(0)
    if not flexible:
        return None
    chars, spans = [], []
    for token in re.finditer(r"\s+|\S+", text):
        if token.group().isspace():
            chars.append(" ")
            spans.append(token.span())
        else:
            for i in range(token.start(), token.end()):
                chars.append(text[i])
                spans.append((i, i + 1))
    while chars and chars[0] == " ":
        chars.pop(0)
        spans.pop(0)
    while chars and chars[-1] == " ":
        chars.pop()
        spans.pop()
    match = rx.search("".join(chars))
    if match is None:
        return None
    start, end = match.span(1 if rx.groups else 0)
    if start < 0 or end <= start:
        return ""
    return text[spans[start][0]:spans[end - 1][1]]


def _highlighted(run, colors) -> bool:
    hl = run.find(f"{W}rPr/{W}highlight")
    return hl is not None and hl.get(W + "val") in colors


REFERENCE_MIN_ALNUM = 3
SEP = "\x00"        # in located text: any display-run content other than w:t (never a character of real text)


def _deleted(r, p) -> bool:
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag in (W + "del", W + "moveFrom"):
            return True
        q = q.getparent()
    return False


def located_text(p) -> Tuple[str, List[int]]:
    """(located text, display offset of each of its positions and of its end) — see the module docstring."""
    out: List[str] = []
    offs: List[int] = []
    pos = 0
    for r in paragraph_runs(p):
        if _deleted(r, p):
            continue
        for c in r:
            if c.tag == W + "rPr":
                continue
            if c.tag == W + "t":
                s = c.text or ""
                out.append(s)
                offs.extend(range(pos, pos + len(s)))
                pos += len(s)
            else:
                out.append(SEP)
                offs.append(pos)
    offs.append(pos)
    return "".join(out), offs


def value_pattern(value: str):
    """How a template value is found in located text: its words in order, whitespace between them, any case."""
    words = [re.escape(w) for w in re.split(r"\s+", (value or "").strip()) if w]
    return re.compile(r"\s+".join(words), re.IGNORECASE)


def _wordch(c: str) -> bool:
    return c.isalnum() or c == "_"


def bounded(text: str, a: int, b: int) -> bool:
    """``text[a:b]`` stands on its own: on each side the next character is absent, whitespace, a separator, or a
    mark whose own far neighbour is not a letter, digit or underscore."""
    for i, step in ((a - 1, -1), (b, 1)):
        if not 0 <= i < len(text):
            continue
        c = text[i]
        if _wordch(c):
            return False
        if c.isspace() or c == SEP:
            continue
        j = i + step
        if 0 <= j < len(text) and _wordch(text[j]):
            return False
    return True


def value_tokens(s: str) -> List[str]:
    """Maximal runs of digits or of letters, casefolded (how a template value is recognised in another form)."""
    return [t.casefold() for t in re.findall(r"\d+|[^\W\d_]+", s or "")]


def value_left_in(piece: str, value: str) -> bool:
    """True when the value's tokens, concatenated, appear in the concatenation of ``piece``'s consecutive tokens
    starting at a token's start or inside a letter token, and ending at a token's end or inside a letter token: a
    letter token may be cut ("DOORHARDWARECONSULTANT" holds "DOOR HARDWARE"), a digit token never is ("(408)
    710-0123" and "1087100-01" do not hold "08 71 00")."""
    key = "".join(value_tokens(value))
    toks = value_tokens(piece)
    if not key or not toks:
        return False
    flat = "".join(toks)
    edges, digit, pos = set(), [], 0
    for t in toks:
        edges.add(pos)
        digit += [t[0].isdecimal()] * len(t)
        pos += len(t)
    edges.add(pos)
    i = flat.find(key)
    while i >= 0:
        j = i + len(key)
        if (i in edges or not digit[i]) and (j in edges or not digit[j - 1]):
            return True
        i = flat.find(key, i + 1)
    return False


def shown(text: str) -> str:
    """Located text for a message: separators as spaces, whitespace collapsed."""
    return " ".join(text.replace(SEP, " ").split())


def mark_slots(p, slots: Sequence["Slot"]) -> str:
    """The paragraph's text for a person, each slot in ⟦ ⟧ (``shown`` form)."""
    text, offs = located_text(p)
    starts, ends = {s.start for s in slots}, {s.end for s in slots}
    return shown("".join(("⟦" if ch != SEP and offs[i] in starts else "") + ch
                         + ("⟧" if ch != SEP and offs[i] + 1 in ends else "") for i, ch in enumerate(text)))


def field_runs(root) -> set:
    """Runs inside a field: between a complex field's begin and end (counted over the whole part in document
    order) or inside w:fldSimple."""
    inside, depth = set(), 0
    for r in root.iter(W + "r"):
        if depth > 0 or any(a.tag == W + "fldSimple" for a in r.iterancestors()):
            inside.add(r)
        for fc in r.findall(W + "fldChar"):
            kind = fc.get(W + "fldCharType")
            if kind == "begin":
                depth += 1
                inside.add(r)
            elif kind == "end":
                depth = max(0, depth - 1)
    return inside


def reference_values(tpkg: DocxPackage, fills_spec: Dict[str, Any]) -> Dict[str, str]:
    """Each fill rule's template value: its ``in_template`` text, else what the rule extracts from the template."""
    out: Dict[str, str] = {}
    for name, rule in fills_spec.items():
        if not isinstance(rule, dict):
            raise JobException(f"slot {name!r}: fill rule must be an object", "bad-params")
        if "in_template" in rule:
            v = rule["in_template"]
            if not isinstance(v, str) or not v.strip():
                raise JobException(f"slot {name!r}: 'in_template' must be the template's own non-empty text",
                                   "bad-params")
            v = v.strip()
        elif rule_uses_literal(rule):
            raise JobException(f"slot {name!r}: with reference slots a literal fill needs 'in_template' — the "
                               f"template spec's own words it replaces", "bad-params")
        else:
            try:
                v = extract_fill(tpkg, name, rule)
            except JobException as e:
                if e.code == "bad-params":
                    raise
                raise JobException(f"slot {name!r}: the rule finds no value in the template spec itself "
                                   f"({e.reason}); give 'in_template' or a rule that also fits the template",
                                   "template-slot", {"slot": name})
        if SEP in v:
            raise JobException(f"slot {name!r}: the template value holds a NUL character", "bad-params")
        if len("".join(value_tokens(v))) < REFERENCE_MIN_ALNUM:
            raise JobException(f"slot {name!r}: the template value {v!r} is too short to locate safely",
                               "template-slot", {"slot": name, "value": v})
        out[name] = v
    return out


def reference_slots(p, where: str, ref_values: Dict[str, str], in_field: Optional[set]) -> List[Slot]:
    """Every bounded occurrence of a template value in the paragraph's located text (module docstring), with the
    refusals that go with it: overlap, inside a field, the value left elsewhere in another form."""
    text, offs = located_text(p)
    found: List[Tuple[int, int, Slot]] = []
    for name, value in ref_values.items():
        for m in value_pattern(value).finditer(text):
            if bounded(text, m.start(), m.end()):
                found.append((m.start(), m.end(), Slot(name, "reference", offs[m.start()], offs[m.end()], m.group(0))))
    found.sort(key=lambda f: (f[0], f[1]))
    for (a1, b1, s1), (a2, b2, s2) in zip(found, found[1:]):
        if a2 < b1:
            raise JobException(f"{where}: the template values of {s1.name!r} and {s2.name!r} overlap in "
                               f"{shown(text[a1:max(b1, b2)])!r}", "slot-overlap", {"where": where, "text": shown(text)})
    if in_field is not None:
        _, segs = display_segments(p)
        for _, _, s in found:
            if any(b > a and a < s.end and s.start < b and r in in_field for t, r, a, b in segs):
                raise JobException(f"{where}: {s.name} ({s.placeholder!r}) is shown by a field; its result would come "
                                   f"back when Word updates fields — not filled", "slot-in-field",
                                   {"where": where, "slot": s.name})
    pieces, cur = [], 0
    for a, b, _ in found:
        pieces.append(text[cur:a])
        cur = b
    pieces.append(text[cur:])
    for name, value in ref_values.items():
        for piece in pieces:
            if value_left_in(piece, value):
                raise JobException(f"{where}: the template spec's own {name} {value!r} also appears in "
                                   f"{shown(piece)!r} in a form that can't be replaced safely (written differently, or "
                                   f"run into other text) — it would be copied unchanged into every spec",
                                   "template-value-left", {"where": where, "slot": name, "text": shown(piece)})
    return [s for _, _, s in found]


def find_slots(p, markers: Sequence[str], colors: Sequence[str], where: str,
               ref_values: Optional[Dict[str, str]] = None, in_field: Optional[set] = None,
               text_only: bool = True) -> List[Slot]:
    """``text_only`` False (fill_variables: the in-paragraph edit core places a value in any run) drops only the rule
    that a highlight/braces slot's runs hold nothing but text; every other refusal stays."""
    text, segs = display_segments(p)
    spans: List[Slot] = []
    if "reference" in markers:
        spans = reference_slots(p, where, ref_values or {}, in_field)
    if "highlight" in markers:
        cur: List[Any] = []
        def flush():
            tsegs = [s for s in segs if s[1] in cur]
            if tsegs:
                a, b = tsegs[0][2], tsegs[-1][3]
                raw = text[a:b]
                m = BRACES.fullmatch(raw.strip())
                spans.append(Slot(m.group(1) if m else raw.strip(), "highlight", a, b, raw))
        for r in paragraph_runs(p):
            if _highlighted(r, colors) and r.find(W + "t") is not None:
                cur.append(r)
            else:
                flush()
                cur = []
        flush()
    if "braces" in markers:
        for m in BRACES.finditer(text):
            same = [s for s in spans if s.marker == "highlight" and s.start <= m.start() and m.end() <= s.end]
            if same:
                h = same[0]
                if h.name == m.group(1) and text[h.start:h.end].strip() == m.group(0):
                    h.marker = "both"
                    continue
                raise JobException(f"{where}: highlighted text {text[h.start:h.end]!r} contains the placeholder "
                                   f"{m.group(0)} and other text; which is the slot?", "slot-overlap",
                                   {"where": where, "text": text})
            if any(s.start < m.end() and m.start() < s.end for s in spans):
                raise JobException(f"{where}: placeholder {m.group(0)} overlaps a highlighted slot", "slot-overlap",
                                   {"where": where, "text": text})
            spans.append(Slot(m.group(1), "braces", m.start(), m.end(), m.group(0)))
    for s in spans:
        if s.marker != "reference" and text_only:   # a reference slot never holds a separator; carve keeps what's beside it
            for t, r, a, b in segs:
                if a < s.end and s.start < b and any(c.tag not in TEXT_ONLY for c in r):
                    raise JobException(f"{where}: slot {s.name!r} runs through a run holding more than text "
                                       f"({[etree.QName(c).localname for c in r if c.tag not in TEXT_ONLY]})",
                                       "slot-structure", {"where": where, "slot": s.name})
        if len({id(r.getparent()) for t, r, a, b in segs if b > a and a < s.end and s.start < b}) > 1:
            raise JobException(f"{where}: slot {s.name!r} spans runs in different containers (a hyperlink, a content "
                               f"control…)", "slot-structure", {"where": where, "slot": s.name})
        if not s.name:
            raise JobException(f"{where}: an empty highlighted slot", "slot-structure", {"where": where})
        # A slot shown by a field — a {{SECTION_TITLE}} typed as a STYLEREF field's result, a highlighted field
        # result — would be filled correctly and then put back by Word's next field update (every marker; the
        # reference branch refused its own above).
        if s.marker != "reference" and in_field is not None and any(
                b > a and a < s.end and s.start < b and r in in_field for t, r, a, b in segs):
            raise JobException(f"{where}: slot {s.name!r} ({s.placeholder!r}) is shown by a field (a STYLEREF, a "
                               f"document property…); Word would put the field's own result back when it updates "
                               f"fields — replace the field with plain text in the template", "slot-in-field",
                               {"where": where, "slot": s.name})
    return sorted(spans, key=lambda s: s.start)


def carve(p, start: int, end: int) -> List[Any]:
    """Cut the runs that display text [start, end) touches (display runs whose text overlaps it) so that the range
    is exactly the w:t text of whole runs holding nothing but w:rPr and w:t. A touched run's other content — its
    text outside the range and its other children, in order — stays in runs of its own (one per stretch between the
    range's pieces), each with a copy of the run's attributes and properties; empty w:t elements of a touched run
    are dropped. Returns the runs holding the range, in order. The verifier's ``_run_model`` states the same."""
    spans, pos = [], 0
    for r in paragraph_runs(p):
        if _deleted(r, p):
            continue
        n = sum(len(t.text or "") for t in r.findall(W + "t"))
        spans.append((r, pos, pos + n))
        pos += n
    inside: List[Any] = []
    for r, ra, rb in spans:
        if not (rb > ra and ra < end and start < rb):
            continue
        groups: List[List[Any]] = []            # [is_inside, [elements]]

        def add(flag: bool, el) -> None:
            if groups and groups[-1][0] == flag:
                groups[-1][1].append(el)
            else:
                groups.append([flag, [el]])
        off = ra
        for c in [c for c in r if c.tag != W + "rPr"]:
            if c.tag != W + "t":
                add(False, c)
                continue
            text = c.text or ""
            a, b = off, off + len(text)
            off = b
            if not text:
                continue
            cuts = sorted({a, b} | {x for x in (start, end) if a < x < b})
            if len(cuts) == 2:
                add(start <= a and b <= end, c)
                continue
            for x, y in zip(cuts, cuts[1:]):
                piece = etree.Element(c.tag)
                set_text(piece, text[x - a:y - a])
                add(start <= x and y <= end, piece)
        if len(groups) == 1 and groups[0][0] and all(c.tag in TEXT_ONLY for c in r):
            inside.append(r)
            continue
        parent = r.getparent()
        idx = parent.index(r)
        parent.remove(r)
        for k, (flag, els) in enumerate(groups):
            nr = clone_run_shell(r)
            for el in els:
                nr.append(el)
            parent.insert(idx + k, nr)
            if flag:
                inside.append(nr)
    return inside


def apply_slot(p, slot: Slot, fill: str, remove_highlight: bool):
    """Replace the slot's text with ``fill`` in one run: the touched runs are carved (``carve``), the slot's runs
    collapse into the first one, whose highlight goes when the slot is a highlight slot."""
    runs = carve(p, slot.start, slot.end)
    if not runs:
        raise JobException(f"internal: slot {slot.name!r} lost its runs", "internal")
    parent = runs[0].getparent()
    if any(r.getparent() is not parent for r in runs):
        raise JobException(f"slot {slot.name!r} spans runs in different containers", "slot-structure")
    keep = runs[0]
    for c in [c for c in keep if c.tag == W + "t"]:
        keep.remove(c)
    t = etree.SubElement(keep, W + "t")
    set_text(t, fill)
    if remove_highlight:
        hl = keep.find(f"{W}rPr/{W}highlight")
        if hl is not None:
            hl.getparent().remove(hl)
    for r in runs[1:]:
        parent.remove(r)
    return keep


def expected_block_text(p, slots: Sequence[Slot], fills: Dict[str, str]) -> str:
    """Block text (CLAUDE.md definition) of the paragraph after its slots are filled — computed from the
    template paragraph, without running the transform."""
    from ..ooxml import FULL_TEXT_TAGS
    out, dpos = [], 0
    pending = sorted(slots, key=lambda s: s.start)
    for el in p.iter(*FULL_TEXT_TAGS):
        if nearest_p(el) is not p:
            continue
        text = el.text or ""
        if el.tag != W + "t":
            out.append(text)
            continue
        a, b = dpos, dpos + len(text)
        dpos = b
        cursor = a
        for s in pending:
            if s.end <= a or s.start >= b:
                continue
            if s.start > cursor:
                out.append(text[cursor - a:s.start - a])
            if s.start >= a:
                out.append(fills[s.name])
            cursor = max(cursor, min(s.end, b))
        out.append(text[cursor - a:])
    return "".join(out)


# ---- fills -------------------------------------------------------------------------------------------

def _source_paragraphs(pkg: DocxPackage, source: str, section: Optional[int]) -> List[Any]:
    return [p for part in _source_parts(pkg, source, section) for p in paragraphs(pkg.xml(part))]


def _source_parts(pkg: DocxPackage, source: str, section: Optional[int]) -> List[str]:
    """The parts a fill rule's ``source`` reads, in order (their paragraphs, in this order, are what it searches)."""
    if source == "document":
        return [pkg.main_part()]
    kind, _, typ = source.partition(".")
    if kind not in KINDS or typ not in TYPES:
        raise JobException(f"fill source must be 'document' or '<header|footer>.<default|first|even>', got {source!r}",
                           "bad-params")
    shown = effective_refs(pkg)
    secs = range(len(shown)) if section is None else [section]
    parts: List[str] = []
    for i in secs:
        if not 0 <= i < len(shown):
            raise JobException(f"fill source section {i} does not exist", "bad-params")
        part = shown[i].get((kind, typ))
        if part and part not in parts:
            parts.append(part)
    if not parts:
        raise JobException(f"the document has no {typ} {kind} to extract a fill from", "not-found",
                           {"source": source})
    return parts


def fill_reads(pkg: DocxPackage, rule: Dict[str, Any]) -> List[Tuple[str, int]]:
    """(part, block index) of every paragraph a fill rule takes its value from — what an op extracting with it READS
    from the source (CLAUDE.md rule 9: declared as ``(part, block, "verify:text")``, so another op changing that text
    in the same spec is a plan-time conflict, never a value the output no longer shows). ``regex``: the paragraphs it
    matches (as written or whitespace-normalised); ``paragraph_after``: each anchor and the paragraphs after it up to and
    including the first non-blank one; ``first_match``: every sub-rule's; ``literal``: none. A rule that cannot be
    resolved reads nothing here (its extraction raised already)."""
    if not isinstance(rule, dict):
        return []
    if rule.get("rule") == "first_match" or ("rules" in rule and rule.get("rule") in (None, "first_match")):
        out: List[Tuple[str, int]] = []
        for sub in rule.get("rules") or []:
            out += [r for r in fill_reads(pkg, sub) if r not in out]
        return out
    kind = rule.get("rule")
    if kind == "binding":
        return [(rule["part"], rule["paragraph"])]
    if kind not in ("regex", "paragraph_after"):
        return []
    try:
        rx = re.compile(rule["pattern"] if kind == "regex" else rule["anchor_regex"],
                        re.IGNORECASE if rule.get("case_insensitive", False) else 0)
        parts = _source_parts(pkg, rule.get("source", "document"), rule.get("section"))
    except (JobException, KeyError, re.error):
        return []
    out = []
    for part in parts:
        texts = [display_text(p) for p in paragraphs(pkg.xml(part))]
        hits = [i for i, t in enumerate(texts) if _fill_match(rx, t, rule.get("flexible_whitespace", True)) is not None]
        for h in hits:
            out.append((part, h))
            if kind == "paragraph_after":
                for j in range(h + 1, len(texts)):
                    out.append((part, j))
                    if texts[j].strip():
                        break
    return list(dict.fromkeys(out))


# The usual identity of a CSI spec, as fill rules — for slot_markers ['reference'] the same rules give the template
# spec's own values (read_template shows them and where its headers/footers show them) and each spec's own; the
# catalog shows them, and a flag compares a literal fill with the document's own value by them (C3).
REFERENCE_FILLS = {
    "SECTION_NUMBER": {"rule": "regex", "source": "document",
                       "case_insensitive": True,
                       "pattern": r"^SECTION\s+(\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?)\b"},
    "SECTION_TITLE": {"rule": "first_match", "rules": [
        {"rule": "paragraph_after", "source": "document", "case_insensitive": True,
         "anchor_regex": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*$"},
        {"rule": "regex", "source": "document", "case_insensitive": True,
         "pattern": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*[-–—:]\s*(.+)$"}]},
}


def rule_uses_literal(rule: Dict[str, Any]) -> bool:
    if not isinstance(rule, dict):
        return False
    if rule.get("rule") in ("literal", "typed"):       # typed: the user's own wording for one file (a spec binding)
        return True
    if isinstance(rule.get("rules"), list):
        return any(rule_uses_literal(r) for r in rule["rules"])
    return False


def _validate_rule_syntax(name: str, rule: Dict[str, Any]) -> None:
    if not isinstance(rule, dict):
        raise JobException(f"slot {name!r}: fill rule must be an object", "bad-params")
    if rule.get("rule") == "first_match" or ("rules" in rule and rule.get("rule") in (None, "first_match")):
        sub = rule.get("rules")
        if not isinstance(sub, list) or not sub:
            raise JobException(f"slot {name!r}: 'first_match' requires a non-empty 'rules' list", "bad-params")
        for r in sub:
            _validate_rule_syntax(name, r)
        return
    kind = rule.get("rule")
    for option in ("case_insensitive", "flexible_whitespace", "strip"):
        if option in rule and type(rule[option]) is not bool:
            raise JobException(f"slot {name!r}: {option} must be a boolean", "bad-params")
    if kind == "literal":
        if not isinstance(rule.get("value"), str):
            raise JobException(f"slot {name!r}: literal fill needs a string 'value'", "bad-params")
    elif kind == "typed":
        if (not isinstance(rule.get("value"), str) or not rule["value"].strip()
                or not isinstance(rule.get("source_md5"), str)):
            raise JobException(f"slot {name!r}: typed wording requires its value and source_md5", "bad-params")
    elif kind == "binding":
        if (not isinstance(rule.get("value"), str) or not rule["value"] or not isinstance(rule.get("part"), str)
                or type(rule.get("paragraph")) is not int or rule["paragraph"] < 0
                or not isinstance(rule.get("source_md5"), str)):
            raise JobException(f"slot {name!r}: binding requires exact value, part, paragraph and source_md5", "bad-params")
    elif kind == "regex":
        try:
            re.compile(rule["pattern"])
        except (KeyError, TypeError, re.error) as e:
            raise JobException(f"slot {name!r}: bad or missing 'pattern' ({e})", "bad-params")
    elif kind == "paragraph_after":
        try:
            re.compile(rule["anchor_regex"])
        except (KeyError, TypeError, re.error) as e:
            raise JobException(f"slot {name!r}: bad or missing 'anchor_regex' ({e})", "bad-params")
    else:
        raise JobException(f"slot {name!r}: rule must be literal | regex | paragraph_after | first_match, got {kind!r}",
                           "bad-params")


def extract_fill(pkg: DocxPackage, name: str, rule: Dict[str, Any]) -> str:
    _validate_rule_syntax(name, rule)
    if rule.get("rule") == "first_match" or ("rules" in rule and rule.get("rule") in (None, "first_match")):
        last_err: Optional[JobException] = None
        for sub in rule["rules"]:
            try:
                return extract_fill(pkg, name, sub)
            except JobException as e:
                if e.code == "bad-params":
                    raise
                last_err = e
        raise last_err or JobException(f"slot {name!r}: no rule in fallback chain matched", "not-found", {"slot": name})

    kind = rule.get("rule")
    strip = rule.get("strip", True)
    flex_ws = rule.get("flexible_whitespace", True)
    occ = rule.get("occurrence", "all")
    if occ not in ("all", "first"):
        raise JobException(f"slot {name!r}: occurrence must be 'all' or 'first', got {occ!r}", "bad-params")

    if kind == "typed":
        # the user's own wording for this file, placed as approved (compiler.placed_words traces it to their words)
        if hashlib.md5(pkg.data).hexdigest() != rule["source_md5"]:
            raise JobException(f"slot {name!r}: the wording given for this file is bound to other bytes (the file "
                               f"changed, or it is another file's)", "binding-source")
        return rule["value"]
    if kind == "binding":
        if hashlib.md5(pkg.data).hexdigest() != rule["source_md5"]:
            raise JobException(f"slot {name!r}: bound source identity changed", "binding-source")
        part = rule["part"]
        if part not in pkg.text_parts():
            raise JobException(f"slot {name!r}: bound part {part!r} is not a source text part", "binding-source")
        pars = paragraphs(pkg.xml(part))
        ordinal = rule["paragraph"]
        if ordinal >= len(pars) or rule["value"] not in display_text(pars[ordinal]):
            raise JobException(f"slot {name!r}: bound literal {rule['value']!r} does not occur verbatim in "
                               f"{part} paragraph {ordinal}", "binding-source")
        return rule["value"]
    if kind == "literal":
        value = rule["value"]
    elif kind == "regex":
        rx = re.compile(rule["pattern"], re.IGNORECASE if rule.get("case_insensitive", False) else 0)
        pars = _source_paragraphs(pkg, rule.get("source", "document"), rule.get("section"))
        values = []
        for p in pars:
            raw = display_text(p)
            value = _fill_match(rx, raw, flex_ws)
            if value is not None:
                values.append(value)
        if not values:
            first = display_text(pars[0]) if pars else "(empty)"
            raise JobException(f"slot {name!r}: pattern {rule['pattern']!r} matches nothing in {rule.get('source', 'document')}; "
                               f"first line: {first!r}",
                               "not-found", {"slot": name, "rule": rule})
        if occ == "first":
            value = values[0]
        else:
            # consensus is judged on whitespace-normalized text; the fill is the first match's own characters
            normed = [_norm_ws(v).strip() if flex_ws else v for v in values]
            if len(set(normed)) > 1:
                raise JobException(f"slot {name!r}: pattern matches different values {sorted(set(values))}", "ambiguous",
                                   {"slot": name, "values": sorted(set(values))})
            value = values[0]
    elif kind == "paragraph_after":
        rx = re.compile(rule["anchor_regex"], re.IGNORECASE if rule.get("case_insensitive", False) else 0)
        pars = _source_paragraphs(pkg, rule.get("source", "document"), rule.get("section"))
        texts = [display_text(p) for p in pars]
        hits = [i for i, t in enumerate(texts) if _fill_match(rx, t, flex_ws) is not None]
        if not hits:
            raise JobException(f"slot {name!r}: anchor {rule['anchor_regex']!r} matches 0 paragraph(s); "
                               f"exactly one needed; first line: {texts[0] if texts else '(empty)'!r}",
                               "not-found", {"slot": name, "matches": hits})
        candidates = []                   # the following paragraphs' own text (verbatim)
        for h in hits:
            nxt = [t for t in texts[h + 1:] if t.strip()]
            if nxt:
                candidates.append(nxt[0])
        if not candidates:
            raise JobException(f"slot {name!r}: nothing follows the anchor paragraph", "not-found", {"slot": name})
        keys = [_norm_ws(c).strip() if flex_ws else c for c in candidates]
        if len(hits) > 1 and occ != "first" and len(set(keys)) > 1:
            raise JobException(f"slot {name!r}: anchor {rule['anchor_regex']!r} matches {len(hits)} paragraph(s); "
                               f"exactly one needed", "ambiguous", {"slot": name, "matches": hits})
        value = candidates[0]
    else:
        raise JobException(f"slot {name!r}: rule must be literal | regex | paragraph_after | first_match, got {kind!r}",
                           "bad-params")
    # The fill keeps the document's own characters (CLAUDE.md rule 11: extracted, never typed). Whitespace
    # normalization is used only to MATCH and to judge consensus; captures are mapped back to the source span.
    if strip:
        value = value.strip()
    if not value:
        raise JobException(f"slot {name!r}: the fill is empty", "not-found", {"slot": name})
    return value
