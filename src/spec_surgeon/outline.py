"""The outline tool (docs/SPEC_FORMATTER_MODES_DESIGN.md N1): the structure reader's roles for every main-body
paragraph of one file, and Claude's reading of the paragraphs the reader leaves unresolved.

**The reader first, and final.** ``structure.analyze_structure(pkg, canadian=True)`` on the file's unmodified bytes.
A paragraph it places keeps that role: nothing here overrides it — with one exception the Specification Formatter
learned (``demote_unshaped``): a TYPED number read as a PART, an article or the section number line is taken only when
the rest of the line is shaped like a heading. "1.5 times the pipe diameter shall be maintained." and "PART 1 of the
Contract Documents shall govern." read as requirement wording, and a section line that names a second SECTION is a
cross-reference; those go to Claude to decide instead. Automatic numbering is stronger evidence and is never demoted;
Safe mode's reading is not this module's and is unchanged. A paragraph it excludes (a table cell, hidden text,
a redline that can change the structure, an empty line) is never sent and never gets a role.

**Claude's reading of the rest** (``classify``): the unresolved paragraphs go to one structured call per window of at
most ``OUTLINE_CHUNK`` of them, the window shown with up to ``OUTLINE_CONTEXT`` paragraphs of context on either side
(their reader roles shown, never decided again) and the outline so far above it. The system prompt and the one tool
(``submit_roles``) are constants: byte-stable, so the prompt is cached. The call is ``one_shot``, at
``resources.OUTLINE_EFFORT``. The answer must name every paragraph it was asked about exactly once, each with a role
of ``ROLE_VOCABULARY`` (``none``: no part of the outline, with a reason); a window whose answer misses, repeats or
invents a paragraph, or names another role, is asked again ``OUTLINE_RETRIES`` time(s), and what is still not answered
cleanly stays unplaced with why. A file with more than ``OUTLINE_MAX`` paragraphs to decide is not sent.

**What comes back** (``result``): per role, the paragraphs the reader placed and the ones Claude proposes, and one
``indices`` selector (N2) for both; the paragraphs Claude leaves out of the outline, with its reasons; the ones left
unplaced, with why. A role chooses paragraphs and never words: the plan built on it places nothing (invariant 6), and
the user approves it with the plan, whose summary names the proposals and whose preview shows them.

The conversation runs it (``Conversation._read_outline``): the model, the client, the usage, the spending check and
Stop are the conversation's. Nothing here edits a file or is imported by the verifier.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .package import DocxPackage, W
from .structure import ROLES, analyze_structure

ROLE_VOCABULARY = tuple(ROLES) + ("none",)
OUTLINE_CHUNK = 120              # paragraphs to decide in one call
OUTLINE_CONTEXT = 3              # paragraphs of context shown before and after a window
OUTLINE_MAX = 2000               # paragraphs to decide in one file, past which nothing is sent
OUTLINE_RETRIES = 1              # times a window whose answer is not clean is asked again
OUTLINE_MAX_OUTPUT_TOKENS = 32000
TEXT_CLIP = 400                  # characters of a paragraph's text shown
REASON_CLIP = 200                # characters of a reason kept
ROLE_WORDS = {
    "section_id": 'the section number line ("SECTION 21 13 13")',
    "section_title": "the section's title, under the number line",
    "part": '"PART 1 - GENERAL", "PART 2 PRODUCTS"',
    "article": '"1.1 SUMMARY", "1.01 SUBMITTALS": a PART\'s headings',
    "paragraph": 'an article\'s numbered requirement: "A." in CSI, ".1" in PageFormat',
    "subparagraph": 'under a paragraph: "1." in CSI, ".1" one level deeper in PageFormat',
    "subparagraph_2": 'under a subparagraph: "a." in CSI',
    "subparagraph_3": 'the sixth numbered level: "1)" or "(1)"',
    "subparagraph_4": 'the seventh numbered level: "a)" or "(a)"',
    "end_of_section": '"END OF SECTION 21 13 13"',
    "none": "no part of the outline: a specifier's or editor's note, a title page line, a revision log, prose between "
            "headings that belongs to no numbered level",
}

CLASSIFY_SYSTEM = """You read the outline of a construction specification (CSI SectionFormat, or Canadian CSC \
PageFormat) for a deterministic engine that formats Word files. A structure reader has already placed every \
paragraph it could; you decide only the paragraphs marked DECIDE, which it could not. The other paragraphs are \
context: their roles are fixed, never repeat or change them.

Roles (the only ones you may give):
""" + "\n".join(f"- {role}: {words}" for role, words in ROLE_WORDS.items()) + """

How to decide:
- Automatic list numbering (a "list" marker on the line) is stronger evidence than the words; a paragraph's style, \
its indent and its neighbours come next. The marker shown is what Word displays for that paragraph.
- A paragraph is placed one level below the nearest heading above it that it belongs to; never skip a level the \
document does not skip.
- A cross-reference is not a heading: "as specified in Section 23 05 00", "PART 1 of the Contract Documents shall \
govern", "comply with Article 1.3" are requirement wording, and a line naming a second SECTION is not the section \
number line.
- A sentence shaped like a heading is not one when it reads as a requirement: "1.5 times the pipe diameter shall be \
maintained." is not an article.
- A single letter that could be a Roman numeral (i., v., x.) is a letter when the paragraph before it at the same \
level carries the preceding letter.
- A typed Canadian ".1" has the same shape at every level below the article: place it by its indent, its style and \
the paragraphs around it.
- When you cannot tell, say none with the reason: a wrong role restyles a paragraph the user did not mean, and a \
missing one leaves it as it is.
Answer by calling submit_roles once, with every DECIDE paragraph exactly once."""

CLASSIFY_TOOLS = [{
    "name": "submit_roles", "description": "Your role for every DECIDE paragraph (the final answer).",
    "input_schema": {"type": "object", "properties": {
        "paragraphs": {"type": "array", "items": {"type": "object", "properties": {
            "index": {"type": "integer", "description": "the paragraph's number as shown in brackets"},
            "role": {"type": "string", "enum": list(ROLE_VOCABULARY)},
            "reason": {"type": "string", "description": "a few words: why (required for none)"}},
            "required": ["index", "role"]}}},
        "required": ["paragraphs"]}}]


@dataclass
class Proposal:
    index: int
    role: str
    reason: str = ""


@dataclass
class Window:
    decide: List[int]
    context: List[int]


@dataclass
class OutlineReading:
    """One file's reading: the reader's rows, Claude's proposals, what stays unplaced and why."""
    label: str
    digest: str                      # SHA-256 of the file's bytes: the key its chosen paragraphs are pinned by
    rows: List[Dict[str, Any]]
    proposals: Dict[int, Proposal] = field(default_factory=dict)
    unplaced: Dict[int, str] = field(default_factory=dict)
    calls: int = 0
    note: str = ""
    demoted: List[int] = field(default_factory=list)   # typed headings the reader read that fail a heading's shape


# ---- the reader, with typed headings held to a heading's shape -----------------------------------------------------

HEADING_WORDS = 12               # words a typed heading's title may have
UNSHAPED = ("a typed number begins wording that reads as a requirement, not a heading (the Specification Formatter's "
            "rule: the rest of the line must be shaped like a heading)")
SECOND_SECTION = "the line names a second SECTION: a cross-reference, not the section number line"
_SECTION_WORD = re.compile(r"\bSECTION\s+[0-9]{2}[ \u00a0–—-]*[0-9]{2}", re.I)


def heading_shaped(rest: str) -> bool:
    """The words after a typed PART or article number read as a heading's title: none, or starting with a capital
    letter or a digit, not ending like a sentence, at most ``HEADING_WORDS`` words."""
    rest = " ".join(str(rest or "").split())
    if not rest:
        return True
    return ((rest[0].isupper() or rest[0].isdigit()) and not rest.endswith((".", "?", "!", ";", ","))
            and len(rest.split()) <= HEADING_WORDS)


def demote_unshaped(rows: List[Dict[str, Any]]) -> List[int]:
    """Hold the reader's TYPED section, PART and article readings to a heading's shape (module docstring): a row that
    fails becomes unresolved with the reason, so Claude decides it. Returns the rows demoted."""
    demoted = []
    for row in rows:
        if row.get("role") not in ("section_id", "part", "article") or row.get("numbering", {}).get("marker"):
            continue
        rest = str(row.get("content") or "")
        if row["role"] == "section_id":
            why = SECOND_SECTION if _SECTION_WORD.search(rest) else None
        else:
            why = None if heading_shaped(rest) else UNSHAPED
        if why:
            row.update(role=None, disposition="unresolved", reason=why, depth=None)
            demoted.append(row["paragraph"])
    return demoted


# ---- what is sent ---------------------------------------------------------------------------------------------------

def _left_indent(p, sheet) -> Optional[float]:
    """The paragraph's left indent in inches: its own ``w:ind``, else its style chain's; None when neither sets one
    (a numbered paragraph's list level may still indent it — the marker says that)."""
    def ind_of(ppr) -> Optional[int]:
        ind = ppr.find(W + "ind") if ppr is not None else None
        if ind is None:
            return None
        for attr in ("left", "start"):
            v = ind.get(W + attr)
            if v is not None and v.lstrip("-").isdigit():
                return int(v)
        return None
    own = ind_of(p.find(W + "pPr"))
    if own is not None:
        return round(own / 1440, 2)
    sid, seen = sheet.paragraph_style_id(p), set()
    while sid and sid in sheet.styles and sid not in seen:
        seen.add(sid)
        st = sheet.styles[sid]
        v = ind_of(st.find(W + "pPr"))
        if v is not None:
            return round(v / 1440, 2)
        based = st.find(W + "basedOn")
        sid = based.get(W + "val") if based is not None else None
    return None


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1] + "…"


def windows(rows: Sequence[Dict[str, Any]], decide: Sequence[int], chunk: int = OUTLINE_CHUNK,
            context: int = OUTLINE_CONTEXT) -> List[Window]:
    """The paragraphs to decide in order, ``chunk`` at a time; each window's context is every paragraph from
    ``context`` before its first to ``context`` after its last (those to decide included)."""
    out: List[Window] = []
    n = len(rows)
    for at in range(0, len(decide), chunk):
        group = list(decide[at:at + chunk])
        lo, hi = max(0, group[0] - context), min(n, group[-1] + context + 1)
        out.append(Window(group, list(range(lo, hi))))
    return out


def _line(row: Dict[str, Any], p, sheet, decide: bool) -> Optional[str]:
    if row["disposition"] == "excluded":
        if not str(row.get("text") or "").strip():
            return None
        head = str(row.get("reason") or "").split(":")[0]
        return f"[{row['paragraph']}] not in the outline ({head}) | {_clip(row['text'], TEXT_CLIP)}"
    bits = [f"[{row['paragraph']}]", "DECIDE" if decide else f"role={row['role'] or 'unresolved'}"]
    style = row.get("style_id")
    if style:
        bits.append(f"style={json.dumps(style)}")
    num = row.get("numbering") or {}
    if num.get("marker"):
        bits.append(f"list={json.dumps(num['marker'])} level={num.get('level')}")
    indent = _left_indent(p, sheet) if p is not None else None
    if indent:
        bits.append(f"indent={indent:g}in")
    return " ".join(bits) + " | " + _clip(row.get("text"), TEXT_CLIP)


def _outline_so_far(rows: Sequence[Dict[str, Any]], before: int) -> str:
    """The nearest placed paragraph of each depth above ``before``: where the window starts in the outline."""
    path: Dict[int, Dict[str, Any]] = {}
    for row in rows[:before]:
        if row.get("role") and row.get("depth") is not None:
            path = {d: r for d, r in path.items() if d < row["depth"]} | {row["depth"]: row}
    if not path:
        return "(nothing placed above this window)"
    return " > ".join(f"{r['role']} [{r['paragraph']}] {_clip(r.get('text'), 60)!r}"
                      for _, r in sorted(path.items()))


def window_message(label: str, rows: Sequence[Dict[str, Any]], pars: Sequence[Any], sheet, window: Window,
                   retry: str = "") -> str:
    decide = set(window.decide)
    lines = [ln for ln in (_line(rows[i], pars[i] if i < len(pars) else None, sheet, i in decide)
                           for i in window.context) if ln]
    return (f"FILE: {label}\nTHE OUTLINE ABOVE THIS WINDOW: {_outline_so_far(rows, window.context[0])}\n\n"
            "PARAGRAPHS (in document order; DECIDE marks yours, the others are context with fixed roles):\n"
            + "\n".join(lines)
            + (f"\n\nYOUR PREVIOUS ANSWER WAS NOT USED: {retry} Answer every DECIDE paragraph exactly once."
               if retry else "")
            + "\n\nCall submit_roles with a role for each DECIDE paragraph: "
            + ", ".join(str(i) for i in window.decide) + ".")


# ---- what comes back ------------------------------------------------------------------------------------------------

def read_answer(resp: Any, asked: Sequence[int]) -> Tuple[Dict[int, Proposal], List[str]]:
    """The proposals of one answer and what is wrong with it (empty: clean). Only asked paragraphs, a role of the
    vocabulary, ``none`` with a reason and each paragraph once are kept; a paragraph answered twice is kept by
    neither answer."""
    from . import llm
    problem = llm.stop_problem(resp)
    if problem:
        return {}, [problem]
    uses = [b for b in llm.tool_uses(llm.history_content(resp)) if getattr(b, "name", "") == "submit_roles"]
    if not uses:
        return {}, ["the answer did not call submit_roles"]
    raw = (uses[0].input or {}).get("paragraphs") if isinstance(uses[0].input, dict) else None
    if not isinstance(raw, list):
        return {}, ["submit_roles had no list of paragraphs"]
    wanted = set(asked)
    seen: Dict[int, Proposal] = {}
    twice, errors = set(), []
    for item in raw:
        if not isinstance(item, dict):
            errors.append("an entry was not an object")
            continue
        index, role = item.get("index"), item.get("role")
        reason = _clip(item.get("reason") or "", REASON_CLIP)
        if type(index) is not int or index not in wanted:
            errors.append(f"paragraph {index!r} was not one to decide")
            continue
        if role not in ROLE_VOCABULARY:
            errors.append(f"paragraph {index}: {role!r} is not a role")
            continue
        if role == "none" and not reason:
            errors.append(f"paragraph {index}: none without a reason")
            continue
        if index in seen or index in twice:
            twice.add(index)
            seen.pop(index, None)
            continue
        seen[index] = Proposal(index, role, reason)
    if twice:
        errors.append("answered more than once: " + ", ".join(str(i) for i in sorted(twice)))
    missing = sorted(wanted - set(seen) - twice)
    if missing:
        errors.append("not answered: " + ", ".join(str(i) for i in missing[:30]) + (" …" if len(missing) > 30 else ""))
    return seen, errors


def classify(label: str, pkg: DocxPackage, call: Callable[[str], Any], *, chunk: int = OUTLINE_CHUNK,
             context: int = OUTLINE_CONTEXT, limit: int = OUTLINE_MAX, retries: int = OUTLINE_RETRIES,
             failed: Optional[Callable[[BaseException], Optional[str]]] = None) -> OutlineReading:
    """The reader's rows for ``pkg`` and Claude's proposals for what it leaves. ``call(message)`` makes one model call
    with ``CLASSIFY_SYSTEM`` and ``CLASSIFY_TOOLS`` and returns the response (the conversation's ``llm.call``).
    ``failed(exception)``: the reason to record when a call fails (an API error), or None to let the exception
    propagate (Stop, the spending check, a defect); after a recorded failure nothing more is sent, what was read is
    kept, and the paragraphs not yet read stay unplaced with that reason."""
    from .ooxml import paragraphs
    from .ops.selectors import StyleSheet
    rows = analyze_structure(pkg, canadian=True)["paragraphs"]
    reading = OutlineReading(label, hashlib.sha256(pkg.data).hexdigest(), rows)
    reading.demoted = demote_unshaped(rows)
    decide = [r["paragraph"] for r in rows if r["disposition"] == "unresolved"]
    if not decide:
        reading.note = "the structure reader placed every paragraph in the outline: nothing was sent to Claude"
        return reading
    if len(decide) > limit:
        reading.unplaced = {i: f"the file has {len(decide)} paragraphs the reader could not place, more than the "
                               f"{limit} the outline tool sends" for i in decide}
        reading.note = "too many paragraphs to decide: nothing was sent to Claude"
        return reading
    pars = paragraphs(pkg.xml(pkg.main_part()))
    sheet = StyleSheet(pkg)
    for window in windows(rows, decide, chunk, context):
        kept: Dict[int, Proposal] = {}
        errors: List[str] = []
        for attempt in range(retries + 1):
            reading.calls += 1
            try:
                resp = call(window_message(label, rows, pars, sheet, window,
                                           "; ".join(errors)[:600] if attempt else ""))
            except Exception as e:  # noqa: BLE001 — classified by the caller
                why = failed(e) if failed is not None else None
                if why is None:
                    raise
                reading.proposals.update(kept)
                for i in decide:
                    if i not in reading.proposals:
                        reading.unplaced[i] = f"Claude's reading could not be made ({why})"
                reading.note = "Claude's reading stopped at a failed call; what it read before is kept"
                return reading
            got, errors = read_answer(resp, window.decide)
            kept.update(got)
            if not errors:
                break
        reading.proposals.update(kept)
        for i in window.decide:
            if i not in kept:
                reading.unplaced[i] = "Claude's answer did not give it one role" + (f" ({errors[0]})" if errors else "")
    return reading


def result(reading: OutlineReading) -> Dict[str, Any]:
    """The tool's answer to the chat model (module docstring)."""
    roles: Dict[str, Dict[str, Any]] = {}
    for row in reading.rows:
        if row.get("role"):
            roles.setdefault(row["role"], {"placed_by_reader": [], "proposed_by_claude": []})[
                "placed_by_reader"].append(row["paragraph"])
    left_out = []
    for i, prop in sorted(reading.proposals.items()):
        if prop.role == "none":
            left_out.append({"paragraph": i, "reason": prop.reason})
        else:
            roles.setdefault(prop.role, {"placed_by_reader": [], "proposed_by_claude": []})[
                "proposed_by_claude"].append(i)
    ordered = {}
    for role in ROLES:
        if role in roles:
            entry = roles[role]
            chosen = sorted(entry["placed_by_reader"] + entry["proposed_by_claude"])
            ordered[role] = {**entry, "selector": {"scope": ["document"],
                                                   "paragraphs": {"indices_by_document": {reading.digest: chosen}}}}
    excluded = sum(1 for r in reading.rows if r["disposition"] == "excluded")
    proposed = sum(len(v["proposed_by_claude"]) for v in ordered.values())
    return {
        "file": reading.label,
        "document_sha256": reading.digest,
        "paragraphs_in_body": len(reading.rows),
        "roles": ordered,
        "left_out_of_the_outline_by_claude": left_out,
        "unplaced": [{"paragraph": i, "why": why} for i, why in sorted(reading.unplaced.items())],
        "excluded_by_the_reader": excluded,
        "typed_headings_sent_to_claude": reading.demoted,
        "claude_calls": reading.calls,
        "note": (reading.note + ". " if reading.note else "")
        + ("Paragraph numbers count every paragraph of the document body from 0. A selector names them under this "
           "document's SHA-256 (indices_by_document): for several documents in one plan, put each document's entry "
           "in the same indices_by_document; a document it does not name is refused, never given another's "
           "paragraphs. placed_by_reader is the structure reader's deterministic reading; proposed_by_claude is Claude's "
           "reading of paragraphs the reader could not place"
           + (f" ({proposed} here): say in your message which roles rest on Claude's reading — the user approves "
              "them only with the plan, and the preview shows them" if proposed else "")
           + ". A role's selector covers both."),
    }
