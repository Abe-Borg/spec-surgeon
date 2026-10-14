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

When ``flexible_whitespace`` (default true) is active, regex/anchor matching falls back to a whitespace-normalized
view (converting ``\\u00a0`` and tabs to spaces and collapsing multiple spaces) and normalizes whitespace in the
extracted fill. Fills are stripped of surrounding whitespace (``strip``, default true) and must be non-empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import paragraphs, set_text, split_run_at_text
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


def _highlighted(run, colors) -> bool:
    hl = run.find(f"{W}rPr/{W}highlight")
    return hl is not None and hl.get(W + "val") in colors


def find_slots(p, markers: Sequence[str], colors: Sequence[str], where: str) -> List[Slot]:
    text, segs = display_segments(p)
    spans: List[Slot] = []
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
        for t, r, a, b in segs:
            if a < s.end and s.start < b and any(c.tag not in TEXT_ONLY for c in r):
                raise JobException(f"{where}: slot {s.name!r} runs through a run holding more than text "
                                   f"({[etree.QName(c).localname for c in r if c.tag not in TEXT_ONLY]})",
                                   "slot-structure", {"where": where, "slot": s.name})
        if not s.name:
            raise JobException(f"{where}: an empty highlighted slot", "slot-structure", {"where": where})
    return sorted(spans, key=lambda s: s.start)


def apply_slot(p, slot: Slot, fill: str, remove_highlight: bool) -> None:
    """Replace the slot's text with ``fill`` in one run: runs a boundary cuts through are split (rPr carried),
    the slot's runs collapse into the first one, whose highlight goes when the slot is a highlight slot."""
    def isolate(offset: int) -> None:
        """Split the run whose w:t straddles ``offset`` so that a run boundary falls exactly there."""
        _, segs = display_segments(p)
        for t, r, a, b in segs:
            if a < offset < b:
                pieces = [x for x in split_run_at_text(r, t, 0, offset - a) if x is not None]
                parent = r.getparent()
                idx = parent.index(r)
                parent.remove(r)
                for k, x in enumerate(pieces):
                    parent.insert(idx + k, x)
                return
    isolate(slot.start)
    isolate(slot.end)
    _, segs = display_segments(p)
    runs: List[Any] = []
    for t, r, a, b in segs:
        if slot.start <= a and b <= slot.end and b > a and r not in runs:
            runs.append(r)
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
    if source == "document":
        return paragraphs(pkg.xml(pkg.main_part()))
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
    return [p for part in parts for p in paragraphs(pkg.xml(part))]


def rule_uses_literal(rule: Dict[str, Any]) -> bool:
    if not isinstance(rule, dict):
        return False
    if rule.get("rule") == "literal":
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
    if kind == "literal":
        if not isinstance(rule.get("value"), str):
            raise JobException(f"slot {name!r}: literal fill needs a string 'value'", "bad-params")
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

    if kind == "literal":
        value = rule["value"]
    elif kind == "regex":
        rx = re.compile(rule["pattern"])
        pars = _source_paragraphs(pkg, rule.get("source", "document"), rule.get("section"))
        values = []
        for p in pars:
            raw = display_text(p)
            m = rx.search(raw)
            if m:
                values.append(m.group(1) if rx.groups else m.group(0))
        if not values and flex_ws:
            for p in pars:
                norm = _norm_ws(display_text(p)).strip()
                m = rx.search(norm)
                if m:
                    values.append(m.group(1) if rx.groups else m.group(0))
        if not values:
            raise JobException(f"slot {name!r}: pattern {rule['pattern']!r} matches nothing in {rule.get('source', 'document')}",
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
        rx = re.compile(rule["anchor_regex"])
        pars = _source_paragraphs(pkg, rule.get("source", "document"), rule.get("section"))
        texts = [display_text(p) for p in pars]
        hits = [i for i, t in enumerate(texts) if rx.search(t)]
        if not hits and flex_ws:
            hits = [i for i, t in enumerate(texts) if rx.search(_norm_ws(t).strip())]
        if not hits:
            raise JobException(f"slot {name!r}: anchor {rule['anchor_regex']!r} matches 0 paragraph(s); "
                               f"exactly one needed", "not-found", {"slot": name, "matches": hits})
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
    # normalization is used only to MATCH and to judge consensus; a match that needed the normalized view yields
    # that view's text, because the regex group has no other source.
    if strip:
        value = value.strip()
    if not value:
        raise JobException(f"slot {name!r}: the fill is empty", "not-found", {"slot": name})
    return value
