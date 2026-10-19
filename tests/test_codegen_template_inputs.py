"""Generated repairs receive only pinned approved templates, in plan and apply."""
import hashlib
import json
import re
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_template
from test_template import params
from spec_surgeon.codegen import check_source, run_in_sandbox, system_prompt, trial, generate_op
from spec_surgeon.goals import resolve_goal


READ_TEMPLATE = '''from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import DocxPackage
from spec_surgeon.ops.template_styles import reconcile_styles
from spec_surgeon.ops.apply_header_footer_template import strip_foreign
from hashlib import sha256

class InspectPinnedTemplate(Op):
    name = "inspect_pinned_template"
    mode = "formatting"
    description = "Exercise supporting-input access without pretending to complete the goal"
    params_schema = {}
    select_schema = {}
    def plan(self, pkg, select, params):
        content = pkg.inputs[params["template"]]
        if sha256(content).hexdigest() != params["sha256"]:
            raise JobException("wrong template snapshot", "input")
        template = DocxPackage(content)
        if not template.has("word/header1.xml"):
            raise JobException("template header absent", "input")
        mapping, imports = reconcile_styles(pkg, template, ["word/header1.xml"], True, strip_foreign)
        if mapping["Header"] != "Header" or "HFStamp" not in imports:
            raise JobException("template helpers cannot resolve the expected styles", "input")
        frag = self.fragment(select, params)
        frag.geometry_reference = "none"
        frag.geometry_reason = "read-only input plumbing test"
        frag.transform_data = {"path": params["template"], "digest": params["sha256"]}
        return frag
    def apply(self, pkg, frag):
        if sha256(pkg.inputs[frag.transform_data["path"]]).hexdigest() != frag.transform_data["digest"]:
            raise JobException("input missing or changed in apply", "input")
'''


@pytest.fixture
def case(tmp_path):
    source, template = tmp_path / "source.docx", tmp_path / "template.docx"
    source.write_bytes(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    template.write_bytes(build_template())
    values = params(template)
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    contract = {"kind": "document_effects", "effects": [{"id": "branding", "type": "header_footer_template",
        "template": str(template), "template_sha256": digest,
        "params": {k: v for k, v in values.items() if k != "template"}}]}
    return source, template, contract, {"template": str(template), "sha256": digest}


def test_pinned_template_is_available_in_both_sandbox_phases(case, tmp_path):
    source, template, contract, arguments = case
    original = template.read_bytes()
    result = run_in_sandbox(READ_TEMPLATE, source, {}, arguments, tmp_path / "sandbox", goal_contract=contract)
    assert result.ok, (result.error, result.stderr)
    assert template.read_bytes() == original
    assert result.output.read_bytes() == source.read_bytes()


def test_template_inputs_preserve_source_snapshot_and_custom_process_limits(case, tmp_path):
    source, template, contract, arguments = case
    approved = source.read_bytes()
    original_template = template.read_bytes()
    live = build("23 05 00", "SOURCE SAVED AFTER APPROVAL")
    source.write_bytes(live)
    result = run_in_sandbox(READ_TEMPLATE, source, {}, arguments, tmp_path / "snapshot",
                            goal_contract=contract, source_bytes=approved, output_bytes=len(approved) + 8192)
    assert result.ok, (result.error, result.stderr)
    assert result.output.read_bytes() == approved
    limited = run_in_sandbox(READ_TEMPLATE, source, {}, arguments, tmp_path / "limited",
                             goal_contract=contract, source_bytes=approved, output_bytes=len(approved) - 1)
    assert not limited.ok and limited.code == "resource-limit"
    assert not (tmp_path / "limited" / "out.docx").exists()
    assert source.read_bytes() == live and template.read_bytes() == original_template


def test_template_access_does_not_let_a_noop_claim_goal_completion(case, tmp_path):
    source, _, contract, arguments = case
    result = trial(READ_TEMPLATE, source, {}, arguments, tmp_path / "trial", goal_contract=contract,
                   goal_binding=resolve_goal(source, contract)["binding"])
    assert not result.passed
    assert result.record.get("GOAL").status == "FAIL"
    assert all(c.status != "FAIL" for c in result.record.checks if c.id != "GOAL")


def test_changed_template_is_refused_before_generated_code_runs(case, tmp_path):
    source, template, contract, arguments = case
    template.write_bytes(template.read_bytes() + b"changed")
    result = run_in_sandbox(READ_TEMPLATE, source, {}, arguments, tmp_path / "sandbox", goal_contract=contract)
    assert not result.ok and result.code == "stale-template"
    assert not (tmp_path / "sandbox" / "out.docx").exists()


def test_approved_path_spelling_is_available_alongside_resolved_path(case, tmp_path):
    source, template, contract, arguments = case
    (tmp_path / "alias").mkdir()
    alias = str(tmp_path / "alias" / ".." / template.name)
    contract["effects"][0]["template"] = alias
    result = run_in_sandbox(READ_TEMPLATE, source, {}, {**arguments, "template": alias},
                            tmp_path / "sandbox", goal_contract=contract)
    assert result.ok, result.error


def test_generated_code_cannot_change_input_mapping_or_request_unapproved_input(case, tmp_path):
    source, _, contract, arguments = case
    bad = READ_TEMPLATE.replace('content = pkg.inputs[params["template"]]',
                                'pkg.inputs[params["template"]] = b"replacement"\n        content = b"replacement"')
    result = run_in_sandbox(bad, source, {}, arguments, tmp_path / "mutation", goal_contract=contract)
    assert not result.ok and "does not support item assignment" in result.error
    result = run_in_sandbox(READ_TEMPLATE, source, {}, {**arguments, "template": "/unapproved.docx"},
                            tmp_path / "unapproved", goal_contract=contract)
    assert not result.ok and "KeyError" in result.error


def test_governed_writer_can_inspect_the_attached_template(case, tmp_path):
    source, template, contract, arguments = case
    fake = FakeClient(
        resp(tool("read_xml_page", {"file": str(template), "part": "word/header1.xml"}, "inspect")),
        resp(tool("submit_op", {"source": READ_TEMPLATE, "params": arguments}, "trial")))
    generated = generate_op("Apply the template", "inspect template inputs", [source], tmp_path / "writer",
        client=fake, max_iter=1, goal_contract=contract,
        approved_words=[contract["effects"][0]["params"]["slot_fills"]["ISSUED FOR PERMIT"]["value"]])
    assert not generated.passed   # inspecting alone cannot satisfy the template outcome
    response = fake.requests[1]["messages"][-1]["content"][0]
    assert not response.get("is_error"), response
    assert "MICROSOFT" in response["content"]


def test_governed_prompt_describes_graph_capabilities_and_inputs(case):
    _, _, contract, _ = case
    prompt = system_prompt(contract)
    assert "- PLAIN formatting only:" not in prompt
    assert "- Only these property families" not in prompt
    assert "pkg.inputs" in prompt and "pkg.add_member" in prompt
    assert "template_parts" in prompt and "hf_template_refs" in prompt
    assert "- PLAIN formatting only:" in system_prompt()


def test_template_display_segments_helper_remains_governed_only(case):
    _, _, contract, _ = case
    source = "from spec_surgeon.ops.slots import display_segments\n" + READ_TEMPLATE
    assert check_source(source, goal_contract=contract) == []
    assert any("spec_surgeon.ops.slots" in problem and "not allowed" in problem
               for problem in check_source(source))


def test_governed_method_can_complete_template_graph_with_independent_verification(case, tmp_path):
    # Adapt the implementation reference just as a writer would: package inputs
    # replace external file reads, and imports obey the governed whitelist.
    # This is an integration check of the sandbox/contract boundary, not a model eval.
    source, template, contract, _ = case
    reference = Path(__file__).parents[1] / "src/spec_surgeon/ops/apply_header_footer_template.py"
    method = reference.read_text(encoding="utf-8")
    method = method.replace("import hashlib", "from hashlib import md5")
    method = method.replace("import posixpath", "from posixpath import basename, dirname, relpath, splitext")
    method = method.replace("from pathlib import Path\n", "")
    method = method.replace("from . import register\n", "").replace("@register\n", "")
    method = method.replace("from ..", "from spec_surgeon.")
    method = re.sub(r"from \.(\w+)", r"from spec_surgeon.ops.\1", method)
    method = method.replace("hashlib.md5", "md5").replace("posixpath.", "")
    method = method.replace("Path(tpath).is_file()", "(tpath in pkg.inputs)")
    method = method.replace("Path(tpath).read_bytes()", "pkg.inputs[tpath]")
    method = method.replace('Path(td["template"]).read_bytes()', 'pkg.inputs[td["template"]]')
    method = method.replace("str(Path(tpath).resolve())", "tpath")
    method = method.replace("Path(tpath).name", "basename(tpath)")
    result = trial(method, source, {"sections": "all"}, params(template), tmp_path / "graph",
                   goal_contract=contract, goal_binding=resolve_goal(source, contract)["binding"])
    assert result.passed, result.record.to_markdown() if result.record else result.problems
    assert result.record.get("GOAL").status == "PASS"
    assert result.record.get("V10").status == "PASS"
    plan = json.loads((tmp_path / "graph" / "plan.json").read_text(encoding="utf-8"))
    assert plan["members_added"] and plan["members_removed"]
