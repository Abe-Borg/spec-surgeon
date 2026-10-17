"""Sustained scripted investigation, bounded resources, and lossless inspection pages."""
from __future__ import annotations

import copy
import json
from collections import defaultdict

import pytest
from lxml import etree

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build
from spec_surgeon import compiler, llm, resources
from spec_surgeon.codegen import generate_op
from spec_surgeon.conversation import Conversation, check_pairing
from spec_surgeon.package import DocxPackage, W
from test_codegen import GOOD_OP
from test_conversation import REMOVE


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "spec.docx"
    path.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    return path


def submission(spec, tid):
    return resp(tool("submit_editspec", {"spec": spec}, tid))


def bad_spec(name="not_an_operation"):
    spec = copy.deepcopy(REMOVE)
    spec["ops"][0]["op"] = name
    return spec


def test_distinct_failed_hypotheses_continue_past_old_retry_limits(source):
    fake = FakeClient(*(submission(bad_spec(f"hypothesis_{i}"), f"s{i}") for i in range(5)),
                      submission(REMOVE, "success"))
    conversation = Conversation("remove all headers and footers", [source], client=fake)
    result = conversation.start()
    assert result.status == "ok" and len(fake.requests) == 6
    assert len([e for e in result.errors if "unknown op" in e]) == 5
    for request in fake.requests:
        check_pairing(request["messages"])


def test_equivalent_rejections_cannot_reset_the_guard_by_rewording_summary(source):
    candidates = [dict(bad_spec(), summary=f"new description {i}", assumptions=[f"note {i}"])
                  for i in range(3)]
    fake = FakeClient(*(submission(spec, f"s{i}") for i, spec in enumerate(candidates)))
    conversation = Conversation("remove all headers and footers", [source], client=fake)
    result = conversation.start()
    assert result.status == "failed" and "stalled" in result.message
    assert len(fake.requests) == 3 and conversation.pending[0]["tool_use_id"] == "s2"


def test_new_evidence_allows_another_attempt_but_repeated_reads_do_not(source):
    inspect = lambda tid: resp(tool("read_paragraphs", {"file": source.name, "count": 1}, tid))
    fake = FakeClient(submission(bad_spec(), "s1"), submission(bad_spec(), "s2"), inspect("read1"),
                      submission(bad_spec(), "s3"), inspect("read2"), submission(bad_spec(), "s4"),
                      submission(bad_spec(), "s5"))
    conversation = Conversation("remove all headers and footers", [source], client=fake)
    result = conversation.start()
    assert result.status == "failed" and "stalled" in result.message
    assert len(fake.requests) == 7
    for request in fake.requests:
        check_pairing(request["messages"])


def test_default_investigation_can_use_more_than_twelve_calls(source):
    fake = FakeClient(*(resp(tool("read_paragraphs", {"file": source.name}, f"read{i}")) for i in range(13)),
                      submission(REMOVE, "success"))
    result = compiler.compile_request("remove all headers and footers", [source], client=fake)
    assert result.status == "ok" and len(fake.requests) == 14


def test_codegen_can_revise_more_than_three_distinct_candidates(source, tmp_path):
    bad = [resp(tool("submit_op", {"source": f"import {module}\n"}, f"s{i}"))
           for i, module in enumerate(("os", "subprocess", "socket", "pathlib"))]
    fake = FakeClient(*bad, resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "good")))
    before = source.read_bytes()
    generated = generate_op("header distance 900 twips", "a custom format", [source], tmp_path / "generated",
                            client=fake, max_output_tokens=20000)
    assert generated.passed and len(generated.attempts) == 5
    assert all(request["max_tokens"] == 20000 for request in fake.requests)
    assert source.read_bytes() == before


def test_local_output_policy_cannot_escape_outer_budget():
    budget = llm.CallBudget(max_calls=1, max_output_tokens=1234, effort="low")
    fake = FakeClient(resp(text("ok")))
    with llm.budget_scope(budget):
        llm.call(fake, model="claude-opus-5-5", system="S", tools=[], messages=[{"role": "user", "content": "x"}],
                 max_output_tokens=64000, effort="max")
    assert fake.requests[0]["max_tokens"] == 1234
    assert fake.requests[0]["output_config"] == {"effort": "low"}
    assert budget.calls == budget.usage.calls == 1


def test_codegen_equivalent_ast_does_not_progress_by_changing_comments(source, tmp_path):
    attempts = [resp(tool("submit_op", {"source": "# explanation\n" * i + "import os\n"}, f"s{i}"))
                for i in range(3)]
    fake = FakeClient(*attempts)
    generated = generate_op("a format", "custom method", [source], tmp_path / "generated", client=fake)
    assert not generated.passed and "stalled" in generated.errors[0]
    assert len(generated.attempts) == len(fake.requests) == 3


def test_larger_allowances_remain_configurable_and_invalid_values_are_refused(source):
    budget = llm.CallBudget(max_calls=900, max_total_tokens=30000000, max_cost_usd=500)
    assert budget.max_calls == 900
    conversation = Conversation("r", [source], max_calls=900, max_attempts=200)
    assert conversation.max_calls == 900 and conversation.max_attempts == 200
    defaults = llm.CallBudget()
    assert defaults.max_calls == resources.DEFAULT_MAX_CALLS == 512
    assert defaults.max_output_tokens == resources.DEFAULT_MAX_OUTPUT_TOKENS == 64000
    assert defaults.max_total_tokens == resources.DEFAULT_MAX_TOTAL_TOKENS == 20000000
    assert defaults.max_cost_usd == resources.DEFAULT_MAX_COST_USD == 250
    assert defaults.effort == resources.DEFAULT_EFFORT == "max"
    for bad in (True, 0, -1, 1.5):
        with pytest.raises(ValueError):
            Conversation("r", [source], max_attempts=bad)


@pytest.fixture
def long_document(source):
    pkg = DocxPackage.from_path(source)
    root = pkg.xml(pkg.main_part())
    body = root.find(W + "body")
    section = body.find(W + "sectPr")
    for child in list(body):
        body.remove(child)
    texts = [f"MATCH paragraph {i}" for i in range(105)]
    texts[80] = 'MATCH "quoted" \\ text Ω ' * 1500 + " UNIQUE TAIL"
    for content in texts:
        p = etree.SubElement(body, W + "p")
        etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = content
    if section is not None:
        body.append(section)
    pkg.write_xml(pkg.main_part(), root)
    source.write_bytes(pkg.output_bytes())
    return source, texts


def test_paginated_text_reaches_late_matches_and_long_paragraph_tails(long_document):
    source, expected = long_document
    batch = compiler._Batch([source], None)
    cursor, recovered, hashes = {"start": 0, "text_offset": 0}, defaultdict(str), set()
    while cursor is not None:
        page = batch.read_paragraphs(source.name, regex="MATCH", count=17, chars=1000, **cursor)
        # Wire JSON remains parseable even when paragraph chunks contain quotes/backslashes.
        page = json.loads(json.dumps(page, ensure_ascii=False))
        hashes.add(page["source_sha256"])
        for row in page["paragraphs"]:
            assert row["text_offset"] == len(recovered[row["index"]])
            recovered[row["index"]] += row["text"]
        assert page["next"] != cursor
        cursor = page["next"]
    assert len(hashes) == 1
    assert [recovered[i] for i in range(len(expected))] == expected
    assert recovered[80].endswith("UNIQUE TAIL") and recovered[104].endswith("104")


def test_xml_and_package_pages_are_complete_and_confined_to_listed_inputs(long_document):
    source, _ = long_document
    batch = compiler._Batch([source], None)
    names, start = [], 0
    while start is not None:
        page = batch.list_parts(source.name, start=start, count=3)
        names += [p["name"] for p in page["parts"]]
        start = page["next_start"]
    assert names == batch.pkg(source.name).source_names
    chunks, offset = [], 0
    while offset is not None:
        page = batch.read_xml_page(source.name, part="word/document.xml", offset=offset, chars=2000)
        chunks.append(page["xml"])
        offset = page["next_offset"]
    reconstructed = "".join(chunks)
    etree.fromstring(reconstructed.encode())
    assert "UNIQUE TAIL" in reconstructed and "paragraph 104" in reconstructed
    with pytest.raises(KeyError):
        batch.read_xml_page("../../outside.docx")
    with pytest.raises(KeyError):
        batch.read_xml_page(source.name, part="../../outside.xml")
    with pytest.raises(ValueError):
        batch.read_paragraphs(source.name, start=True)
