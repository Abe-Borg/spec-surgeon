"""Approved outcomes survive different methods, scoped decisions and independent critique."""
from __future__ import annotations

import copy
import json

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, tool
from spec_surgeon import recovery
from spec_surgeon.goals import compile_goal_candidate, resolve_goal
from test_goals import TITLE, write_document
from test_recovery import opts, submit


def contract():
    return {"kind": "document_effects", "effects": [
        {"id": "titles", "type": "format", "target": {"locators": [{"exact_text": TITLE}]},
         "run": {"b": True}, "tracked": False}]}


def critic(verdict="accept", **kwargs):
    return resp(tool("review_repair", {"verdict": verdict, "reason": "Inspect the target method and scope.", **kwargs}))


def test_critic_revision_replans_method_under_unchanged_goal_then_passes(tmp_path):
    source = write_document(tmp_path / "source.docx")
    goal = contract()
    first = compile_goal_candidate(source, goal)
    second = {"kind": "chain", "chain": {"stages": [{"spec": copy.deepcopy(first["spec"])}]}}
    client = FakeClient(submit(first), critic("revise"),
                        resp(tool("plan_repair", {"candidate": second, "reason": "Use explicit stage boundaries."})),
                        submit(second), critic())
    result = recovery.recover([source], "Make the section title bold", tmp_path / "session",
                              adaptive_contract=goal, options=opts(), client=client)
    assert result.ready, result.to_text()
    assert len(result.attempts) == 2 and result.budget["calls"] == 5
    assert result.attempts[0]["critic_review"]["verdict"] == "revise"
    assert result.attempts[0]["results"][0]["status"] == "UNRESOLVED"
    assert result.key["adaptive_contract"] == goal
    assert result.session_state["plans"]
    assert "independently critique" in client.requests[1]["system"]
    assert "original_goal" not in json.loads(client.requests[1]["messages"][0]["content"])
    loaded = recovery.load_recovery(result.out_dir)
    assert loaded.ready and loaded.session_state["critic_reviews"][1]["verdict"] == "accept"
    delivered = recovery.deliver_recovery(loaded)
    assert (delivered / source.name).read_bytes() == loaded.ready[0].output.read_bytes()


def test_old_wrong_selector_can_be_replaced_without_changing_approved_effect(tmp_path):
    source = write_document(tmp_path / "source.docx")
    goal = contract()
    candidate = compile_goal_candidate(source, goal)
    wrong = copy.deepcopy(candidate)
    wrong["spec"]["ops"][0]["select"] = {"paragraphs": {"style_in": ["Heading1"]}}
    result = recovery.recover([source], "Make the exact section title bold", tmp_path / "session",
        spec=wrong["spec"], adaptive_contract=goal, options=opts(),
        client=FakeClient(submit(wrong), submit(candidate), critic()))
    assert result.ready and len(result.attempts) == 2
    assert result.attempts[0]["results"][0]["status"] == "UNRESOLVED"
    assert result.key["goal"] == wrong["spec"]


def test_contract_rejects_extra_change_even_when_generated_candidate_spec_allows_it(tmp_path):
    source = write_document(tmp_path / "source.docx")
    goal = contract()
    changed = compile_goal_candidate(source, goal)
    changed["spec"]["ops"][0]["params"]["run"]["i"] = False
    result = recovery.recover([source], "Make the title bold", tmp_path / "session",
        adaptive_contract=goal, options=opts(max_attempts=1), client=FakeClient(submit(changed)))
    assert not result.ready and "Independent goal check" in result.files[0].reason
    assert not result.session_state.get("critic_reviews"), "A critic never reviews a deterministically failed candidate"


def test_critic_transport_pause_retains_candidate_but_never_ready_and_can_resume_review(tmp_path):
    source = write_document(tmp_path / "source.docx")
    goal = contract()
    result = recovery.recover([source], "Make the title bold", tmp_path / "session",
        adaptive_contract=goal, options=opts(),
        client=FakeClient(submit(compile_goal_candidate(source, goal)), raise_on_enter(api_error("offline"))))
    assert not result.ready and result.files[0].output
    assert result.session_state["critic_reviews"][0]["status"] == "paused"
    resumed = recovery.resume_recovery(result.out_dir, options=opts(),
        client=FakeClient(resp(tool("review_candidate", {})), critic()))
    assert resumed.ready and len(resumed.attempts) == 1


def test_ambiguous_target_requires_scoped_user_choice_then_replans(tmp_path):
    source = write_document(tmp_path / "source.docx", duplicate=True)
    goal = contract()
    initial = recovery.recover([source], "Make the intended section title bold", tmp_path / "session",
        adaptive_contract=goal, options=opts(), client=FakeClient(resp(tool("replan_goal", {"file": str(source)}))))
    assert initial.files[0].status == "NEEDS_INPUT"
    question = initial.question_records[0]
    assert question["kind"] == "goal_binding"
    resolution = resolve_goal(source, goal)
    chosen = resolution["effects"]["titles"]["candidates"][1]["candidate_id"]
    # The resumed model obtains the source-bound plan, then references the persisted ID.
    class Planner(FakeClient):
        def _stream(self, **kw):
            if len(self.requests) == 1:
                result = json.loads(kw["messages"][-1]["content"][0]["content"])
                self.responses.insert(0, resp(tool("submit_repair", {"plan_id": result["plan_id"]})))
            return super()._stream(**kw)
    client = Planner(resp(tool("replan_goal", {"file": str(source)})), critic())
    resumed = recovery.resume_recovery(initial.out_dir,
        request={"question_id": question["id"], "answer": "The second section title.",
                 "effect_id": "titles", "candidate_ids": [chosen]}, options=opts(), client=client)
    assert resumed.ready, resumed.to_text()
    assert resumed.key["adaptive_bindings"][str(source)]["effects"]["titles"]["choice"] == "user"
    assert resumed.decisions[0]["candidate_ids"] == [chosen]


def test_unreadable_document_is_investigated_without_normalizing_or_losing_bytes(tmp_path):
    source = tmp_path / "broken.docx"
    source.write_bytes(b"not a zip file")
    result = recovery.recover([source], "Make the title bold", tmp_path / "session",
        adaptive_contract=contract(), options=opts(),
        client=FakeClient(resp(tool("unsupported", {"reason": "The source is not a readable ZIP; refresh the source."}))))
    assert not result.ready and result.budget["calls"] == 1
    assert result.files[0].pattern_id == "unreadable"
    assert source.read_bytes() == b"not a zip file"


def test_dynamic_analysis_is_read_only_persisted_and_reachable_after_resume(tmp_path):
    source = write_document(tmp_path / "source.docx")
    before = source.read_bytes()
    analysis = "def analyze(documents, inputs):\n    return {'parts': sorted(documents[0]['parts']), 'label': documents[0]['label']}\n"
    result = recovery.recover([source], "Investigate the unusual title", tmp_path / "session", options=opts(),
        client=FakeClient(resp(tool("run_analysis", {"source": analysis, "files": [source.name]})),
                          resp(tool("need_user", {"question": "Confirm the intended title."}))))
    state = next(iter(result.session_state["groups"].values()))
    evidence = json.loads(state["messages"][2]["content"][0]["content"])
    assert evidence["ok"] and evidence["snapshots"][0]["sha256"]
    resumed = recovery.resume_recovery(result.out_dir, request="Inspect the archived result first.", options=opts(),
        client=FakeClient(resp(tool("read_analysis_result", {"artifact_id": evidence["artifact_id"]})),
                          resp(tool("unsupported", {"reason": "Awaiting an approved edit contract."}))))
    state = next(iter(resumed.session_state["groups"].values()))
    assert any(evidence["artifact_id"] in str(m) for m in state["messages"])
    assert source.read_bytes() == before and not resumed.ready


def test_context_epochs_preserve_complete_signed_history_and_scope(tmp_path, monkeypatch):
    from fake_claude import thinking
    from spec_surgeon.catalog import op_catalog
    from spec_surgeon.contexts import estimate_context_tokens
    source = write_document(tmp_path / "source.docx")
    # Leave room for the current catalog and trusted task context, then force rollover
    # with the large tool result. A fixed 60k cap cannot fit the batch-4 catalog alone.
    system = recovery.SYSTEM + "\nOP CATALOG:\n" + recovery._json(op_catalog(examples=False))
    context_limit = estimate_context_tokens(system=system, tools=recovery.TOOLS, messages=[]) + 20000
    monkeypatch.setattr(recovery.compiler._Batch, "list_styles", lambda self, **kw: {"evidence": "x" * 240000})
    client = FakeClient(resp(thinking("original-signature", "reasoning"), tool("list_styles", {"file": source.name})),
                        resp(tool("need_user", {"question": "Which property should change?"})))
    result = recovery.recover([source], "Investigate formatting", tmp_path / "session", options=opts(max_context_tokens=context_limit),
                             client=client)
    assert len(client.requests) == 2
    state = next(iter(result.session_state["groups"].values()))
    assert state["context"]["epoch"] == 1
    record = state["context"]["archives"][0]
    from pathlib import Path
    from spec_surgeon.contexts import load_context_manifest
    from spec_surgeon.storage import artifact_path
    archived = load_context_manifest(artifact_path(Path(state["context_folder"]) / "context", record["artifact_id"]))
    assert archived["messages"][1]["content"][0]["signature"] == "original-signature"
    assert "x" * 240000 in archived["messages"][2]["content"][0]["content"]
    checkpoint = json.loads(state["messages"][0]["content"])
    assert checkpoint["TRUSTED_AUTHORITY"]["files"] == [str(source)]
    assert "x" * 1000 not in str(checkpoint["TRUSTED_AUTHORITY"])


def test_prepare_approved_goal_attempts_all_easy_files_without_calling_model(tmp_path):
    sources = [write_document(tmp_path / "ambiguous.docx", duplicate=True), write_document(tmp_path / "easy.docx")]
    result = recovery.prepare_recovery(sources, "Make the section titles bold", tmp_path / "session",
                                       adaptive_contract=contract(), options=opts())
    assert [f.status for f in result.files] == ["NEEDS_INPUT", "READY"]
    assert result.budget["calls"] == 0 and len(result.attempts) == 1
    assert result.question_records[0]["files"] == [str(sources[0])]


def test_invalid_old_method_is_diagnostic_and_does_not_prevent_goal_planning(tmp_path):
    source = write_document(tmp_path / "source.docx")
    invalid = {"ops": [{"op": "nonexistent_legacy_method"}]}
    result = recovery.prepare_recovery([source], "Make the title bold", tmp_path / "session", spec=invalid,
                                       adaptive_contract=contract(), options=opts())
    assert result.ready and result.session_state["rejected_initial_implementation"]["candidate"] == invalid


TITLE_METHOD = '''
from spec_surgeon.ops.base import Op
from spec_surgeon.package import W
from spec_surgeon.ooxml import paragraphs, run_rpr, mark_rpr, get_or_add, RPR_ORDER

class SetExactTitle(Op):
    name = "set_exact_title"
    mode = "formatting"
    params_schema = {"type": "object", "properties": {"index": {"type": "integer"}}}
    select_schema = {}
    def plan(self, pkg, select, params):
        main = pkg.main_part()
        index = params["index"]
        paragraph = paragraphs(pkg.xml(main))[index]
        frag = self.fragment(select, params)
        runs = list(paragraph.iter(W + "r"))
        frag.property_assertions.append({"kind": "properties", "target": "direct", "scope": ["document"],
            "parts": [main], "select": {"runs": "all", "paragraphs": {"index_range": [index,index+1]}},
            "set": {"run": {"b": True}, "paragraph": {}}, "preserve_fonts": [], "skip_sym_runs": False,
            "in_revisions": "include", "include_paragraph_marks": True, "tracked": False,
            "author": "Abraham Borg", "expected_runs": len(runs), "expected_marks": 1, "expected_paragraphs": 0})
        frag.intended_parts = [main]
        frag.touches = [(main, str(index), "rPr"), (main, str(index), "markRPr")]
        frag.transform_data = {"main": main, "index": index}
        frag.geometry_reference = "none"
        frag.geometry_reason = "The approved title formatting may change wrapping."
        return frag
    def apply(self, pkg, frag):
        main = frag.transform_data["main"]
        root = pkg.xml(main)
        paragraph = paragraphs(root)[frag.transform_data["index"]]
        for run in paragraph.iter(W + "r"):
            for name in ("b", "bCs"):
                get_or_add(run_rpr(run), name, RPR_ORDER).attrib.pop(W + "val", None)
        for name in ("b", "bCs"):
            get_or_add(mark_rpr(paragraph), name, RPR_ORDER).attrib.pop(W + "val", None)
        pkg.write_xml(main, root)
'''


def test_dynamic_non_page_method_after_failed_legacy_plan_keeps_independent_goal_gate(tmp_path):
    source = write_document(tmp_path / "source.docx")
    result = recovery.recover([source], "Make the section title bold", tmp_path / "session",
        spec={"ops": [{"op": "unknown_old_method"}]}, adaptive_contract=contract(), options=opts(),
        client=FakeClient(resp(tool("custom_transform", {"proposal": "Use an exact source-bound title method."})),
                          resp(tool("submit_op", {"source": TITLE_METHOD, "params": {"index": 1}})), critic()))
    assert result.ready, result.to_text()
    assert result.options_snapshot["allow_codegen"] is True
    assert result.attempts[0]["generated_attempts"][0]["source"] == TITLE_METHOD
    assert result.attempts[0]["critic_review"]["verdict"] == "accept"
    assert recovery.load_recovery(result.out_dir).ready


def test_reopened_session_gains_new_tools_in_new_epoch_without_rewriting_old_schema(tmp_path, monkeypatch):
    source = write_document(tmp_path / "source.docx")
    modern = recovery.TOOLS
    monkeypatch.setattr(recovery, "TOOLS", [t for t in modern if t["name"] != "run_analysis"])
    initial = recovery.recover([source], "Investigate formatting", tmp_path / "session", options=opts(),
        client=FakeClient(resp(tool("need_user", {"question": "Confirm the document."}))))
    old_state = next(iter(initial.session_state["groups"].values()))
    old_history = recovery.json_value(old_state["messages"])
    monkeypatch.setattr(recovery, "TOOLS", modern)
    client = FakeClient(resp(tool("unsupported", {"reason": "No approved effect yet."})))
    result = recovery.resume_recovery(initial.out_dir, request="This is the document.", options=opts(), client=client)
    state = next(iter(result.session_state["groups"].values()))
    assert state["context"]["epoch"] == 1
    assert "run_analysis" in {t["name"] for t in client.requests[0]["tools"]}
    from pathlib import Path
    from spec_surgeon.contexts import load_context_manifest
    from spec_surgeon.storage import artifact_path
    archived = load_context_manifest(artifact_path(Path(state["context_folder"]) / "context",
                                                  state["context"]["archives"][0]["artifact_id"]))
    assert "run_analysis" not in {t["name"] for t in archived["tools"]}
    assert archived["messages"][:len(old_history)] == old_history


def test_builtin_planning_failure_resumes_with_new_method_instead_of_requiring_its_plan(tmp_path, monkeypatch):
    from spec_surgeon import goals
    from spec_surgeon.ops.base import JobException
    source = write_document(tmp_path / "source.docx")
    def failed_builtin(*args, **kwargs):
        raise JobException("The old extraction cannot express this source layout", "legacy-extraction")
    monkeypatch.setattr(goals, "compile_goal_candidate", failed_builtin)
    first = recovery.prepare_recovery([source], "Make the section title bold", tmp_path / "session",
                                       adaptive_contract=contract(), options=opts())
    assert not first.ready and first.session_state["planning_failures"][0]["code"] == "legacy-extraction"
    resumed = recovery.resume_recovery(first.out_dir, options=opts(),
        client=FakeClient(resp(tool("custom_transform", {"proposal": "Use the source-bound title method."})),
                          resp(tool("submit_op", {"source": TITLE_METHOD, "params": {"index": 1}})), critic()))
    assert resumed.ready and resumed.out_dir == first.out_dir


def test_scoped_adaptive_answer_leaves_easy_ready_output_untouched(tmp_path):
    from spec_surgeon.goals import choose_goal
    sources = [write_document(tmp_path / "ambiguous.docx", duplicate=True), write_document(tmp_path / "easy.docx")]
    initial = recovery.prepare_recovery(sources, "Make intended section titles bold", tmp_path / "session",
                                       adaptive_contract=contract(), options=opts())
    ready = initial.ready[0]
    before = (ready.output.read_bytes(), ready.output.stat().st_mtime_ns)
    selected = resolve_goal(sources[0], contract())["effects"]["titles"]["candidates"][1]["candidate_id"]
    bound = choose_goal(sources[0], contract(), [selected], effect_id="titles")
    method = compile_goal_candidate(sources[0], contract(), bound)
    resumed = recovery.resume_recovery(initial.out_dir, options=opts(),
        request={"question_id": initial.question_records[0]["id"], "answer": "The second section title.",
                 "effect_id": "titles", "candidate_ids": [selected]}, client=FakeClient(submit(method), critic()))
    assert len(resumed.ready) == 2 and resumed.files[1].attempts == 1
    assert (ready.output.read_bytes(), ready.output.stat().st_mtime_ns) == before
