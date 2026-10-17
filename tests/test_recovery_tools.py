"""Recovery inspection stays within the request's inputs and configured renderer."""
import json
from pathlib import Path

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_template
from spec_surgeon.recovery import RecoveryOptions, RecoveryResult, recover, recover_from_run


def test_loaded_spec_templates_are_inspectable_without_an_attachment(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    template = tmp_path / "architect.docx"
    template.write_bytes(build_template())
    goal = json.loads((Path(__file__).resolve().parents[1] / "examples/specs/apply-header-footer-template.json").read_text())
    for op in goal["ops"]:
        if "template" in op.get("params", {}):
            op["params"]["template"] = str(template)
    client = FakeClient(resp(
        tool("read_template", {}, "profile"),
        tool("read_template", {"file": str(template)}, "named"),
        tool("inspect_xml", {"file": "template", "part": "word/document.xml"}, "primary"),
        tool("inspect_xml", {"file": str(template), "part": "word/document.xml"}, "xml"),
        tool("inspect_xml", {"file": str(tmp_path / "not-authorized.docx"), "part": "word/document.xml"}, "outside"),
        tool("view_pages", {"file": "template"}, "disabled")),
        resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}})))
    result = recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                     options=RecoveryOptions(render=False, use_memory=False), client=client)
    assert result.ready, result.to_text()
    answers = {a["tool_use_id"]: a for a in client.requests[1]["messages"][-1]["content"]}
    assert json.loads(answers["profile"]["content"])["path"] == str(template)
    assert json.loads(answers["named"]["content"])["path"] == str(template)
    assert "sectPr" in answers["primary"]["content"] and "sectPr" in answers["xml"]["content"]
    assert answers["outside"]["is_error"]
    assert answers["disabled"]["is_error"] and "disabled" in answers["disabled"]["content"]


def test_report_includes_reserved_cost_and_does_not_label_unknown_spending_free(tmp_path):
    result = RecoveryResult(tmp_path, "Set margins", budget={
        "calls": 1, "unreported_calls": 1, "usage": {"cost_usd": 0}, "estimated_cost_usd": 0.45})
    assert "$0.45" in result.to_text() and "reserved spending" in result.to_text()
    result.budget["estimated_cost_usd"] = None
    assert "unknown" in result.to_text() and "$0.00" not in result.to_text()


def test_answer_preserves_original_request_when_compile_has_no_spec(tmp_path):
    source = tmp_path / "source.docx"
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    options = RecoveryOptions(render=False, use_memory=False)
    first = recover([source], "Change the font to Verdana", tmp_path / "first", options=options,
                    client=FakeClient(resp(tool("need_user", {"question": "Which sections should change?"}))))
    client = FakeClient(resp(tool("unsupported", {"reason": "No supported proposal yet"})))
    second = recover_from_run(first.out_dir, tmp_path / "second", request="All sections", options=options, client=client)
    assert second.request == "Change the font to Verdana\n\nUser clarification: All sections"
    context = json.loads(client.requests[0]["messages"][0]["content"])
    assert context["request"] == "Change the font to Verdana", "Saved model history remains append-only"
    continuation = json.loads(client.requests[0]["messages"][-1]["content"])
    assert continuation["user_decisions"][0]["answer"] == "All sections"
    assert second.out_dir == first.out_dir
    assert second.question_records[0]["question"] == "Which sections should change?"
    assert second.question_records[0]["status"] == "answered"
