"""Fields — transform side (docs/BUILTIN_OPS_BATCH2_DESIGN.md §1.5; CLAUDE.md "Fields", both sides). The verifier has
its own reading in verify/fields.py: keep the two identical.

**Complex field**: scanning the part in document order, ``w:fldChar w:fldCharType="begin"`` opens a field (an open
field nests it), ``separate`` moves the innermost open field from its code to its result, ``end`` closes it. Its
instruction is the text of the ``w:instrText``/``w:delInstrText`` met while it is the innermost open field and in its
code; a field beginning while another is in its code is nested in that field's code. Type: the instruction's first word,
upper-cased (``=`` counts). **Simple field**: ``w:fldSimple`` (instruction ``w:instr``). **Local**: begin, separate (if
any) and end in one paragraph that is not inside a text box (a simple field: its paragraph).

**Unlinking** removes the begin/separate/end ``w:fldChar`` and the instruction elements of the field's own code, then
every run left holding nothing but ``w:rPr``; a simple field is replaced by its children. The result stays as it was.
Refused for a selected field (``Field.refusal``): not local (``field-span``), a field nested in its code
(``nested-field``), a form field (``form-field``), a marker or instruction in a tracked change (``existing-revisions``).
Page numbers (PAGE, NUMPAGES, SECTIONPAGES, PAGEREF) are never unlinked.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..ooxml import block_struct_counts, block_text_without
from ..package import W

NEVER_UNLINK = ("PAGE", "NUMPAGES", "SECTIONPAGES", "PAGEREF")
FORM_TYPES = ("FORMTEXT", "FORMCHECKBOX", "FORMDROPDOWN")
REVISION_TAGS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
REFUSAL_TEXT = {
    "field-span": "it runs over more than one paragraph (a table of contents, an index …) or never ends",
    "nested-field": "another field sits inside its code",
    "form-field": "it is a form field (a text box, check box or drop-down someone fills in)",
    "existing-revisions": "its code or markers lie in a pending tracked change — accept or reject it first",
}


def paragraph_of(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


class Field:
    def __init__(self, simple=None, begin=None):
        self.simple, self.begin = simple, begin
        self.separate = self.end = None
        self.code: List[Any] = []
        self.nested_in_code: List["Field"] = []
        self.parent: Optional["Field"] = None
        self.in_parent_code = False

    @property
    def instruction(self) -> str:
        if self.simple is not None:
            return self.simple.get(W + "instr") or ""
        return "".join(el.text or "" for el in self.code)

    @property
    def type(self) -> str:
        first = (self.instruction.split() or [""])[0]
        if first.startswith("="):
            return "="
        return first.upper()

    def markers(self) -> List[Any]:
        return [el for el in (self.begin, self.separate, self.end) if el is not None] + self.code

    def paragraph(self) -> Optional[Any]:
        if self.simple is not None:
            return paragraph_of(self.simple)
        if self.begin is None or self.end is None:
            return None
        ps = [paragraph_of(el) for el in (self.begin, self.separate, self.end) if el is not None]
        return ps[0] if all(x is ps[0] for x in ps) else None

    def refusal(self) -> Optional[str]:
        p = self.paragraph()
        if p is None or paragraph_of(p) is not None:
            return "field-span"
        if self.nested_in_code:
            return "nested-field"
        if self.type in FORM_TYPES or (self.begin is not None and self.begin.find(W + "ffData") is not None):
            return "form-field"
        for el in ([self.simple] if self.simple is not None else self.markers()):
            if el.tag == W + "delInstrText":
                return "existing-revisions"
            q = el.getparent()
            while q is not None and q is not p:
                if q.tag in REVISION_TAGS:
                    return "existing-revisions"
                q = q.getparent()
        return None


def fields_of(root) -> List[Field]:
    """Every field of the part, in the document order of their start."""
    out: List[Field] = []
    open_fields: List[Field] = []
    simples: Dict[int, Field] = {}
    for el in root.iter(W + "fldSimple", W + "fldChar", W + "instrText", W + "delInstrText"):
        if el.tag == W + "fldSimple":
            f = Field(simple=el)
            anc = next((a for a in el.iterancestors(W + "fldSimple")), None)
            if anc is not None and id(anc) in simples:
                f.parent = simples[id(anc)]
            elif open_fields:
                f.parent = open_fields[-1]
                f.in_parent_code = f.parent.separate is None
                if f.in_parent_code:
                    f.parent.nested_in_code.append(f)
            simples[id(el)] = f
            out.append(f)
            continue
        if el.tag == W + "fldChar":
            kind = el.get(W + "fldCharType")
            if kind == "begin":
                f = Field(begin=el)
                if open_fields:
                    f.parent = open_fields[-1]
                    f.in_parent_code = f.parent.separate is None
                    if f.in_parent_code:
                        f.parent.nested_in_code.append(f)
                open_fields.append(f)
                out.append(f)
            elif kind == "separate":
                if open_fields and open_fields[-1].separate is None:
                    open_fields[-1].separate = el
            elif kind == "end":
                if open_fields:
                    open_fields.pop().end = el
            continue
        if open_fields and open_fields[-1].separate is None:          # instrText / delInstrText in a field's code
            open_fields[-1].code.append(el)
    return out


def predict_unlink(p, fields: List[Field]) -> Dict[str, Any]:
    """What unlinking ``fields`` (all local to ``p``) does to the block: text before/after, structure after."""
    gone: List[Any] = []
    for f in fields:
        gone += f.markers() if f.simple is None else []
    return {"before": block_text_without(p), "after": block_text_without(p, gone),
            "struct_before": block_struct_counts(p), "struct_after": block_struct_counts(p, gone)}


def unlink(f: Field) -> None:
    """Unlink one field in place (it must have passed ``refusal``)."""
    if f.simple is not None:
        el = f.simple
        parent = el.getparent()
        idx = parent.index(el)
        for c in list(el):
            parent.insert(idx, c)
            idx += 1
        parent.remove(el)
        return
    runs = []
    for el in f.markers():
        r = el.getparent()
        r.remove(el)
        if r.tag == W + "r" and r not in runs:
            runs.append(r)
    for r in runs:
        if r.getparent() is not None and all(not isinstance(c.tag, str) or c.tag == W + "rPr" for c in r):
            r.getparent().remove(r)
