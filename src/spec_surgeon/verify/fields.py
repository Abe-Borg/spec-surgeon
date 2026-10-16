"""Fields — verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.5), independent of ops/fields.py (rule 1): this module's
own reading of where a field begins and ends, what its instruction says, and what unlinking it leaves.

**Complex field**: scanning the part in document order, ``w:fldChar w:fldCharType="begin"`` opens a field (an open field
nests the new one), ``separate`` moves the innermost open field from its code to its result, ``end`` closes it. Its
instruction is the text of the ``w:instrText``/``w:delInstrText`` met while it is the innermost open field and still in
its code; a field that begins while another is in its code is nested in that field's code. Its type is the instruction's
first word, upper-cased (``=`` counts). **Simple field**: ``w:fldSimple``, instruction ``w:instr``. **Local**: begin,
separate (if any) and end in one paragraph that is not inside a text box (for a simple field: the element's paragraph).
**Unlinking** removes the begin, separate and end ``w:fldChar`` and the instruction elements of its own code (then the
runs left holding nothing but ``w:rPr``); a simple field is replaced by its children.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, List, Optional

from .reader import W

V_NEVER_UNLINK = ("PAGE", "NUMPAGES", "SECTIONPAGES", "PAGEREF")
V_FORM_TYPES = ("FORMTEXT", "FORMCHECKBOX", "FORMDROPDOWN")
_V_REV = {W + "ins", W + "del", W + "moveFrom", W + "moveTo"}


def _v_para(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def _v_in_textbox(p) -> bool:
    return p is not None and _v_para(p) is not None


@dataclass
class VField:
    simple: Any = None                        # the w:fldSimple (simple field)
    begin: Any = None                         # the begin w:fldChar (complex field)
    separate: Any = None
    end: Any = None
    code: List[Any] = _field(default_factory=list)          # its own w:instrText / w:delInstrText
    nested_in_code: List["VField"] = _field(default_factory=list)
    parent: Optional["VField"] = None
    in_parent_code: bool = False

    @property
    def instruction(self) -> str:
        if self.simple is not None:
            return self.simple.get(W + "instr") or ""
        return "".join(t.text or "" for t in self.code)

    @property
    def type(self) -> str:
        words = self.instruction.split()
        if not words:
            return ""
        w = words[0]
        return "=" if w.startswith("=") else w.upper()

    def markers(self) -> List[Any]:
        """The elements unlinking removes (complex field): begin, separate, end and its own instruction text."""
        return [e for e in (self.begin, self.separate, self.end) if e is not None] + list(self.code)

    def paragraph(self) -> Optional[Any]:
        if self.simple is not None:
            return _v_para(self.simple)
        if self.end is None:
            return None
        ps = [_v_para(e) for e in (self.begin, self.separate, self.end) if e is not None]   # held: proxies stay one
        return ps[0] if all(x is ps[0] for x in ps) else None

    def local(self) -> bool:
        p = self.paragraph()
        return p is not None and not _v_in_textbox(p)

    def form(self) -> bool:
        return self.type in V_FORM_TYPES or (self.begin is not None and self.begin.find(W + "ffData") is not None)

    def in_revision(self) -> bool:
        """A marker or instruction element of the field lies in a tracked change (or is deleted field code)."""
        els = [self.simple] if self.simple is not None else self.markers()
        for e in els:
            if e is None:
                continue
            if e.tag == W + "delInstrText":
                return True
            q, p = e.getparent(), _v_para(e)
            while q is not None and q is not p:
                if q.tag in _V_REV:
                    return True
                q = q.getparent()
        return False

    def refusal(self) -> Optional[str]:
        """Why this field cannot be unlinked (None when it can)."""
        if not self.local():
            return "field-span"
        if self.nested_in_code:
            return "nested-field"
        if self.form():
            return "form-field"
        if self.in_revision():
            return "existing-revisions"
        return None


def v_fields(root) -> List[VField]:
    """Every field of the part, in the document order of their start (simple and complex alike)."""
    out: List[VField] = []
    stack: List[VField] = []
    simple_of = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == W + "fldSimple":
            f = VField(simple=el)
            q = el.getparent()
            while q is not None and id(q) not in simple_of:
                q = q.getparent()
            if q is not None:
                f.parent = simple_of[id(q)]
            elif stack:
                f.parent, f.in_parent_code = stack[-1], stack[-1].separate is None
                if f.in_parent_code:
                    stack[-1].nested_in_code.append(f)
            simple_of[id(el)] = f
            out.append(f)
        elif el.tag == W + "fldChar":
            kind = el.get(W + "fldCharType")
            if kind == "begin":
                f = VField(begin=el)
                if stack:
                    f.parent, f.in_parent_code = stack[-1], stack[-1].separate is None
                    if f.in_parent_code:
                        stack[-1].nested_in_code.append(f)
                stack.append(f)
                out.append(f)
            elif kind == "separate" and stack and stack[-1].separate is None:
                stack[-1].separate = el
            elif kind == "end" and stack:
                stack.pop().end = el
        elif el.tag in (W + "instrText", W + "delInstrText") and stack and stack[-1].separate is None:
            stack[-1].code.append(el)
    return out
