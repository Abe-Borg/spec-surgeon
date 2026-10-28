"""Live QA F11: the custom-method writer can read a V10 assertion kind's contract. The verifier owns the text
(``verify.props.KIND_CONTRACTS``, filled by each kind's module at import the way ``KINDS`` is, every kind covered);
``codegen.describe_assertion_kind`` is the writer's read-only tool over it, answered like the other read tools, named
in every writer prompt; an unknown name gets a readable answer, not an exception."""
from __future__ import annotations

import copy
import json

import pytest

from spec_surgeon import codegen
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS
from spec_surgeon.codegen import (CODEGEN_READ_TOOLS, CODEGEN_TOOLS, CODEGEN_TOOLS_UNGUARDED, describe_assertion_kind,
                                  generate_op, system_prompt)
from spec_surgeon.verify.props import KIND_CONTRACTS, KIND_EXAMPLES, KINDS
from test_codegen import GOOD_OP, folder  # noqa: F401 — the fixture lives in test_codegen
from test_compiler import FakeClient, resp, tool

FAMILIES = sorted(set(CUSTOM_TRANSFORM_LIMITS["families"].values()))


def test_every_kind_the_verifier_evaluates_has_a_contract():
    assert set(KIND_CONTRACTS) == set(KINDS)
    for name, text in KIND_CONTRACTS.items():
        assert isinstance(text, str) and text.strip(), name
    assert set(KIND_EXAMPLES) <= set(KINDS)


@pytest.mark.parametrize("name", FAMILIES)
def test_every_family_a_custom_method_may_use_has_a_key_level_contract_and_a_worked_example(name):
    """The families (``catalog.CUSTOM_TRANSFORM_LIMITS``) are the kinds a Safe or Default custom method may assert: each
    has a contract written for the writer (not a module docstring: it names the keys and the values) and a worked
    assertion whose kind is its own."""
    text = KIND_CONTRACTS[name]
    assert text and ("Keys" in text or '"set"' in text or '"params"' in text), name
    assert "Compared" in text and ("Refused" in text or "refus" in text), name
    assert "V10 kind ``" not in text, f"{name}: the docstring fallback, not a written contract"
    example = KIND_EXAMPLES[name]
    assert isinstance(example, dict) and example["kind"] == name
    json.dumps(example)                                               # what the tool returns must serialise
    for key in example:
        if key != "kind":
            assert f'"{key}"' in text, f"{name}: the example's key {key!r} is not in the contract"


def test_the_contract_names_what_the_live_writer_missed():
    """F11: twenty-four methods were refused because nothing told the writer the level keys, the closed property set
    and the part the assertion names."""
    text = KIND_CONTRACTS["numbering_labels"]
    for needle in ('"abstractNum"', '"num"', '"ilvl"', '"set"', '"fonts"', '"size"', '"bold"', '"italic"', '"color"',
                   '"numbering_part"', '"expected_levels"', "ascii", "hAnsi", "cs", "eastAsia", "lvlPicBulletId"):
        assert needle in text, needle
    example = KIND_EXAMPLES["numbering_labels"]
    assert example["expected_levels"] == len(example["levels"]) == 2
    assert {"abstractNum", "ilvl", "set"} == set(example["levels"][0]) and {"num", "ilvl", "set"} == set(example["levels"][1])


def test_a_kind_without_a_written_contract_gets_its_modules_docstring():
    """Every other kind (a built-in op's, usable by a governed or an Obliterate method) is described by the verifier
    module that evaluates it: its docstring states its reading."""
    others = sorted(set(KINDS) - set(FAMILIES))
    assert others
    fallback = 0
    for name in others:
        text = KIND_CONTRACTS[name]
        if text.startswith("Kind "):                                   # the docstring fallback names its kind and module
            fallback += 1
            assert text.startswith(f"Kind {name}, as the verifier module that evaluates it states its reading (") and \
                len(text) > 120, name
        else:                                                          # a written contract: what it compares, at least
            assert len(text) > 200 and "Compared" in text, name
    assert fallback                                                    # the fallback is real: some kinds rely on it


def test_describe_assertion_kind_returns_the_contract_and_the_example():
    out = describe_assertion_kind("numbering_labels")
    assert out["kind"] == "numbering_labels" and out["contract"] == KIND_CONTRACTS["numbering_labels"]
    assert out["example"] == KIND_EXAMPLES["numbering_labels"] and out["example"] is not KIND_EXAMPLES["numbering_labels"]
    assert "worked" in out["example_note"]
    out["example"]["levels"].clear()                                   # the tool hands out copies
    assert KIND_EXAMPLES["numbering_labels"]["levels"]
    without = next(n for n in sorted(KINDS) if n not in KIND_EXAMPLES)
    out = describe_assertion_kind(without)
    assert out["kind"] == without and out["contract"] and "example" not in out and "no worked example" in out["example_note"]


@pytest.mark.parametrize("name", ["no_such_kind", "", None, 7, " numbering_labels "])
def test_an_unknown_name_is_a_readable_answer_not_an_exception(name):
    out = describe_assertion_kind(name)
    if name == " numbering_labels ":
        assert out["kind"] == "numbering_labels"                       # whitespace forgiven
        return
    assert "error" in out and "unknown assertion kind" in out["error"] and out["kinds"] == sorted(KINDS)
    json.dumps(out)


def test_the_tool_is_a_read_tool_in_both_tool_lists_and_every_prompt_names_it():
    assert "describe_assertion_kind" in CODEGEN_READ_TOOLS
    for tools in (CODEGEN_TOOLS, CODEGEN_TOOLS_UNGUARDED):
        names = [t["name"] for t in tools]
        assert names.index("describe_assertion_kind") < names.index("submit_op")
        schema = next(t for t in tools if t["name"] == "describe_assertion_kind")["input_schema"]
        assert schema["required"] == ["name"] and set(schema["properties"]) == {"name"}
    plain = system_prompt()
    assert "Read a kind's contract with describe_assertion_kind before you write its assertion" in plain
    assert "read each kind's contract with describe_assertion_kind" in plain
    unguarded = system_prompt(unguarded=True)
    assert "Read a kind's contract with describe_assertion_kind before you write its assertion" in unguarded
    governed = system_prompt(goal_contract={"kind": "format_section_title", "target": {"title": "FIRE SUPPRESSION"},
                                            "run": {"b": True}})
    assert "Read a kind's contract with describe_assertion_kind before you write its assertion" in governed
    # the governed and Obliterate prompts list every kind the verifier evaluates: each has a contract to read
    for prompt, lead in ((unguarded, "V10 assertion kinds: "), (governed, "Available kind names: ")):
        start = prompt.index(lead) + len(lead)
        listed = json.loads(prompt[start:prompt.index("]", start) + 1])
        assert listed == sorted(KINDS) and all(KIND_CONTRACTS[k] for k in listed)
        end = prompt.index("]", start)
        assert prompt.index("describe_assertion_kind", end) - end < 300           # named right beside the list


def test_the_writer_reads_a_contract_through_the_tool_and_an_unknown_kind_is_answered(folder, tmp_path):
    f = sorted(folder.glob("*.docx"))[0]
    fake = FakeClient(resp(tool("describe_assertion_kind", {"name": "numbering_labels"}, "k1"),
                           tool("describe_assertion_kind", {"name": "nope"}, "k2"),
                           tool("describe_assertion_kind", {}, "k3")),
                      resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "a1")))
    g = generate_op("header 0.625 in", "set the header distance", [f], tmp_path / "cg", client=fake)
    assert g.passed and len(g.attempts) == 1
    answered = fake.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in answered] == ["k1", "k2", "k3"]
    first = json.loads(answered[0]["content"])
    assert not answered[0].get("is_error") and first["contract"] == KIND_CONTRACTS["numbering_labels"]
    assert first["example"] == KIND_EXAMPLES["numbering_labels"]
    second = json.loads(answered[1]["content"])
    assert not answered[1].get("is_error") and "unknown assertion kind 'nope'" in second["error"]
    assert answered[2]["is_error"] and "TypeError" in answered[2]["content"]      # a call without its argument
    tools = fake.requests[0]["tools"]
    assert "describe_assertion_kind" in [t["name"] for t in tools]


def test_the_contract_table_stays_the_verifiers_own():
    """The text is read from ``verify/``; ``codegen`` only hands it over (``tests/test_independence.py`` keeps
    ``verify/`` free of the transform side, so the contract can never be a copy of an op's own words)."""
    import inspect
    src = inspect.getsource(codegen.describe_assertion_kind)
    assert "from .verify.props import" in src
    assert copy.deepcopy(KIND_EXAMPLES["run_fonts"]) == KIND_EXAMPLES["run_fonts"]
