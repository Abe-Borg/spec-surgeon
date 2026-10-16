"""Output file names (design note §7) — the rules ``name_output`` and ``make_values_agree`` share, and ``name_output``:
name each output file by a convention from its own content ("211319 - Preaction Sprinkler Systems.docx").

A file name is not document text, so a named, visible transform is allowed in it (never in a document): ``digits``
keeps the digits, ``upper`` / ``lower`` change case, ``title`` capitalizes each word (a run of letters, apostrophes
inside it kept: OWNER'S → Owner's) and lowers the rest; the default leaves the value as written. A name is refused
(``bad-name``) when it holds ``\\ / : * ? " < > |``, a control or invisible character, starts with a space, ends (before
.docx) with a space or a dot, is a Windows device name (CON, PRN, AUX, NUL, COM1–9, LPT1–9), is empty or is longer than
200 characters. The verifier re-derives the name with its own code (verify/textops.py, kind ``output_name``) and compares
it with the name the output was written under.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List

from ..package import DocxPackage
from . import register
from .base import JobException, Op, PlanFragment
from .slots import extract_fill, rule_uses_literal

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
TRANSFORMS = ("as_is", "digits", "upper", "lower", "title")
FORBIDDEN = set('\\/:*?"<>|')
DEVICES = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
MAX_NAME = 200
OUTPUT_NAME_TOUCH = ("[output-name]", "*", "name")
WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def transform(value: str, how: str) -> str:
    if how == "digits":
        return "".join(c for c in value if c.isdigit())
    if how == "upper":
        return value.upper()
    if how == "lower":
        return value.lower()
    if how == "title":
        return WORD.sub(lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), value)
    return value


def check_name(name: str) -> str:
    """The name (with .docx) when Windows can hold it as written, else JobException bad-name."""
    stem = name[:-5] if name.lower().endswith(".docx") else name
    why = None
    if not stem.strip():
        why = "it is empty"
    elif FORBIDDEN & set(name):
        why = f"it holds {sorted(FORBIDDEN & set(name))}"
    elif any(ord(c) < 32 or unicodedata.category(c) in ("Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp") for c in name):
        why = "it holds a control or invisible character"
    elif stem != stem.lstrip(" ") or stem.endswith((" ", ".")):
        why = "it starts with a space or ends with a space or a dot (Windows would change it)"
    elif stem.split(".")[0].strip().casefold() in DEVICES:
        why = "it is a Windows device name"
    elif len(name) > MAX_NAME:
        why = f"it is longer than {MAX_NAME} characters"
    if why:
        raise JobException(f"the output name {name!r} cannot be used: {why}", "bad-name", {"name": name})
    return name


def with_suffix(stem: str) -> str:
    return stem if stem.lower().endswith(".docx") else stem + ".docx"


@register
class NameOutput(Op):
    name = "name_output"
    mode = "formatting"
    description = ("Name each output file by a convention from its own content, e.g. '{number} - {title}' → '211319 - "
                   "Preaction Sprinkler Systems.docx': each {placeholder} is a fill rule run on the file (the title line, "
                   "a header or footer) with an optional transform (digits, upper, lower, title). The document itself is "
                   "not changed; the edited copy gets the new name (copies only, never written back in place).")
    params_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "the name with {placeholders}; .docx is added"},
            "values": {"type": "object", "description": "placeholder → a fill rule run on the file (see fill_rules; no "
                                                        "literal — fixed words go in the pattern)"},
            "transforms": {"type": "object", "description": f"placeholder → one of {list(TRANSFORMS)} (default as_is)"},
        },
        "required": ["pattern", "values"],
    }
    select_schema = {"type": "object", "description": "none — names the whole file"}
    verification_profile = {"V10": "output_name re-extracts every value, applies the transforms and the validity rules "
                                   "with the verifier's own code, and compares the result with the output's file name"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("name_output takes no selector", "bad-params")
        pattern = params.get("pattern")
        values = params.get("values")
        transforms = params.get("transforms") or {}
        if not isinstance(pattern, str) or not pattern.strip():
            raise JobException("'pattern' must be text with {placeholders}", "bad-params")
        if not isinstance(values, dict) or not isinstance(transforms, dict):
            raise JobException("'values' (and 'transforms') must be objects", "bad-params")
        names = PLACEHOLDER.findall(pattern)
        if set(names) != set(values):
            raise JobException(f"the pattern's placeholders {sorted(set(names))} and the values {sorted(values)} must "
                               f"be the same", "bad-params")
        bad = {k: v for k, v in transforms.items() if k not in values or v not in TRANSFORMS}
        if bad:
            raise JobException(f"transforms must name placeholders and be one of {list(TRANSFORMS)}: {bad}",
                               "bad-params")
        got: Dict[str, str] = {}
        for k, rule in values.items():
            if rule_uses_literal(rule):
                raise JobException(f"{k}: a literal value — fixed words belong in the pattern itself", "bad-params")
            got[k] = transform(extract_fill(pkg, k, rule), transforms.get(k, "as_is"))
        name = check_name(with_suffix(PLACEHOLDER.sub(lambda m: got[m.group(1)], pattern)))
        frag = self.fragment({}, params)
        frag.output_name = name
        frag.touches.append(OUTPUT_NAME_TOUCH)
        frag.property_assertions.append({"kind": "output_name", "pattern": pattern, "values": values,
                                         "transforms": transforms, "expected": name})
        frag.notes.append(f"output file name: {name!r} — " + ", ".join(f"{k} = {v!r}" for k, v in got.items()))
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        return None                          # the document is not changed; the batch writes the copy under the name
