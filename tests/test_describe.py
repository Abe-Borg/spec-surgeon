"""Every operation describes itself in prose a person can read (Workstream M: readable by construction).

Each op module owns ``describe(select, params)``. The cases below are the contract: one or more realistic
(select, params) pairs per op and the words the sentence must contain. Rules checked for every case: a complete
sentence, no JSON brackets, no identifiers with underscores outside quotes, no twips / half-points / eighths, no
Python literals, and nothing longer than a short paragraph.
"""
from __future__ import annotations

import re

import pytest

from spec_surgeon.catalog import op_catalog
from spec_surgeon.ops import get_op, prose

ALL_OPS = sorted(op_catalog(examples=False)["ops"])

# op name -> list of (select, params, [expected substrings]); one module per family in tests/describe_cases/
from describe_cases import all_cases

CASES = all_cases()


def _quoted_out(text: str) -> str:
    """The sentence with every curly-quoted span removed: identifiers inside quotes are the user's own."""
    return re.sub("“[^”]*”", "", text)


def check_rules(text: str) -> None:
    assert text and text[0].isupper(), f"must start a sentence: {text!r}"
    assert text.endswith("."), f"must end with a period: {text!r}"
    assert len(text) <= 600, f"too long for a plan line: {text!r}"
    bare = _quoted_out(text)
    for ch in "{}[]":
        assert ch not in bare, f"JSON bracket {ch!r} outside quotes: {text!r}"
    assert not re.search(r"\b\w+_\w+\b", bare), f"identifier with an underscore outside quotes: {text!r}"
    for word in ("twip", "half-point", "half point", "eighth", "None", "True", "False", "regex", "sectPr", "rPr",
                 "pPr", "w:", "params", "select"):
        assert word not in bare, f"{word!r} is engine vocabulary: {text!r}"


@pytest.mark.parametrize("name", ALL_OPS)
def test_every_op_has_a_case(name):
    assert name in CASES and CASES[name], f"{name}: add at least one describe case to tests/test_describe.py"


@pytest.mark.parametrize("name,select,params,expected", [(n, s, p, e) for n in CASES for s, p, e in CASES[n]],
                         ids=[f"{n}-{i}" for n in CASES for i, _ in enumerate(CASES[n])])
def test_description_reads_as_prose(name, select, params, expected):
    text = get_op(name).describe(select, params)
    check_rules(text)
    for word in expected:
        assert word in text, f"{name}: expected {word!r} in {text!r}"


@pytest.mark.parametrize("name", ALL_OPS)
def test_description_never_crashes_on_empty_inputs(name):
    check_rules(get_op(name).describe({}, {}))


def test_prose_units_and_selection():
    assert prose.inches(1440) == "1 inch"
    assert prose.inches(720) == "0.5 inch"
    assert prose.inches(1800) == "1.25 inches"
    assert prose.points_from_half(21) == "10.5 pt"
    assert prose.points_from_eighths(4) == "0.5 pt"
    assert prose.where({}) == "in every paragraph"
    assert prose.where({"scope": ["headers", "footers"]}) == "in every paragraph in the headers and footers"
    assert prose.where({"paragraphs": {"style_in": ["PR1"], "has_bold": True}}) == "in bold paragraphs in the “PR1” style"
    assert prose.where({"sections": [0, 2]}, default="every section") == "in sections 1 and 3"
    assert prose.where({"tables": "all"}, default="every table") == "in every table"
    check_rules(prose.sentence("set", prose.where({"paragraphs": {"text_regex": "^PART\\s+\\d"}})))
