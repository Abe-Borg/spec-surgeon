"""The simple experience must preserve the engine's authority and explicit document scope."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from docx import Document

from fake_claude import FakeClient, resp, tool
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon import review_display


REQUEST = "Make the FIRE SUPPRESSION title bold."
CONTRACT = {"kind": "document_effects", "effects": [{"id": "title", "type": "format",
    "target": {"locators": [{"exact_text": "FIRE SUPPRESSION"}], "cardinality": 1}, "run": {"b": True}}]}
SPEC = {"request": REQUEST, "summary": "Make the title bold", "mode": "formatting",
        "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": "^FIRE SUPPRESSION$"}},
                 "params": {"run": {"b": True}, "tracked": False}}]}


def document(path, duplicate=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_paragraph("SECTION 21 13 13")
    doc.add_paragraph("FIRE SUPPRESSION")
    if duplicate:
        doc.add_paragraph("FIRE SUPPRESSION")
    doc.add_paragraph("Keep this technical wording intact.")
    doc.save(path)
    return path


def client(spec=None):
    return FakeClient(resp(tool("submit_editspec", {"spec": spec or SPEC})))


def task_for(tmp_path, fake=None, **kwargs):
    return DocumentTask(Session(client=fake or client(), out_root=tmp_path / "out", visual_check=False),
                        render=False, **kwargs)


def test_explicit_attachments_never_expand_to_a_folder_and_support_same_names(tmp_path):
    first = document(tmp_path / "a" / "Spec.docx")
    second = document(tmp_path / "b" / "Spec.docx")
    document(tmp_path / "a" / "Unselected.docx")
    task = task_for(tmp_path)
    task.attach([first, second, first])
    assert task.session.files() == [first, second]
    before = [p.read_bytes() for p in (first, second)]
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview"
    assert len(task.session.client.requests) == 1  # one plan, no second intent model
    assert task.session.outcome_review is None
    assert not task.results
    assert task.session.outcome_approval == ""
    task.perform("primary", token=view["token"])
    assert task.stage == "done"
    assert set(task.results) == {str(first), str(second)}
    assert len(set(task.results.values())) == 2
    assert [p.read_bytes() for p in (first, second)] == before
    for output in task.results.values():
        doc = Document(output)
        assert doc.paragraphs[1].runs[0].bold
        assert doc.paragraphs[2].text == "Keep this technical wording intact."


def test_missing_attachment_is_not_silently_removed_from_scope(tmp_path):
    source = document(tmp_path / "Spec.docx")
    task = task_for(tmp_path)
    task.attach([source])
    source.unlink()
    with pytest.raises(ValueError, match="missing"):
        task.session.files()


def test_template_is_separate_and_invalid_drop_is_atomic(tmp_path):
    source = document(tmp_path / "Spec.docx")
    template = document(tmp_path / "Template.docx")
    task = task_for(tmp_path)
    with pytest.raises(ValueError, match="Word"):
        task.attach([source, tmp_path])
    assert task.session.documents == []
    task.attach([source, template])
    task.attach([template], template=True)
    assert task.session.documents == [source]
    assert task.session.template == template


def test_changed_source_cannot_use_the_displayed_approval(tmp_path):
    source = document(tmp_path / "Spec.docx")
    task = task_for(tmp_path)
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        task.perform("primary", token=view["token"])
    assert not task.results


def test_attachment_change_keeps_request_but_invalidates_plan(tmp_path):
    source = document(tmp_path / "a" / "Spec.docx")
    extra = document(tmp_path / "b" / "Other.docx")
    task = task_for(tmp_path)
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.attach([extra])
    assert task.decisions == [REQUEST]
    assert task.session.preview is None
    assert task.session.conversation is None
    with pytest.raises(ValueError, match="changed"):
        task.perform("primary", token=view["token"])


def test_target_question_uses_chat_numbers_without_approving_plan(tmp_path):
    source = document(tmp_path / "Spec.docx", duplicate=True)
    selected = copy.deepcopy(SPEC)
    selected["ops"][0]["select"]["paragraphs"]["index_range"] = [2, 3]
    fake = FakeClient(resp(tool("ask_user", {"message": "Which title should change?", "choices": [
        {"kind": "clarify", "label": "Paragraph 2: FIRE SUPPRESSION"},
        {"kind": "clarify", "label": "Paragraph 3: FIRE SUPPRESSION"}]})),
        resp(tool("submit_editspec", {"spec": selected})))
    task = task_for(tmp_path, fake)
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "question" and not view["action"]
    assert "Paragraph 2" in "\n".join(view["questions"])
    assert "Paragraph 3" in "\n".join(view["questions"])
    view = task.perform("send", text="2")
    assert view["stage"] == "preview"
    assert task.session.outcome_approval == ""
    assert len(task.session.client.requests) == 2
    assert not task.results
    task.perform("primary", token=view["token"])
    result = Document(task.results[str(source)])
    assert not result.paragraphs[1].runs[0].bold
    assert result.paragraphs[2].runs[0].bold


def test_clarification_continues_same_conversation(tmp_path):
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which title should change?"]})),
        resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    task.perform("send", text="Make the title bold")
    original = task.session.conversation
    view = task.perform("send", text="FIRE SUPPRESSION")
    assert task.session.conversation is original
    assert view["stage"] == "preview"
    assert len(task.messages) >= 2


def test_readable_review_preserves_exact_wording_and_every_effect(tmp_path):
    contract = copy.deepcopy(CONTRACT)
    contract["effects"].append({"id": "words", "type": "replace_text", "target": {"all": True},
        "find": "Old <name>", "replace": "New & Co.", "expected_matches": 1})
    sections = review_display.effects(contract)
    assert len(sections) == 2
    text = "\n".join(line for section in sections for line in section["details"])
    assert "Old <name>" in text and "New & Co." in text
    assert "Bold: yes" in text
    assert "Required text replacements: 1" in text
    assert '"effects"' not in text
    assert "Definition · Kind: bullet" in review_display.fields({"definition": {"kind": "bullet"}})


def test_journal_restores_work_without_restoring_approval_or_resetting_spend(tmp_path):
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, journal=journal)
    task.attach([document(tmp_path / "Spec.docx")])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    task.save()
    restored = task_for(tmp_path, journal=journal)
    assert restored.restore()
    assert restored.stage == "resume"
    assert restored.decisions == [REQUEST]
    assert restored.session.outcome_approval == ""
    assert restored.session.preview is None
    assert restored._budget.calls == task._budget.calls
    assert restored._budget.estimated_cost_usd == task._budget.estimated_cost_usd
    assert "outcome_approval" not in json.loads(journal.read_text())


def test_interrupted_call_pauses_before_new_spending(tmp_path):
    journal = tmp_path / "conversation.json"
    task = task_for(tmp_path, journal=journal)
    task.attach([document(tmp_path / "Spec.docx")])
    task.decisions = [REQUEST]
    task.save(in_progress=True)
    restored = task_for(tmp_path, journal=journal)
    restored.restore()
    assert restored.stage == "budget"
    view = restored.view()
    assert "$10" in view["action"]
    restored.perform("primary", token=view["token"])
    assert len(restored.session.client.requests) == 1
    assert restored._budget.max_cost_usd == 20
    assert restored._budget.estimated_cost_usd >= 10
    assert restored.stage == "preview"


def test_budget_covers_all_conversation_turns(tmp_path):
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which title?"]})),
                      resp(tool("submit_editspec", {"spec": SPEC})))
    task = task_for(tmp_path, fake)
    task.attach([document(tmp_path / "Spec.docx")])
    task._budget.max_calls = 1
    task.perform("send", text=REQUEST)
    view = task.perform("send", text="FIRE SUPPRESSION")
    assert view["stage"] == "budget"
    assert len(task.session.client.requests) == 1
    assert not task.results


def test_simple_prompt_keeps_drafting_behind_exact_wording_approval(tmp_path):
    task = task_for(tmp_path)
    task.attach([document(tmp_path / "Spec.docx")])
    task.perform("send", text=REQUEST)
    prompt = task.session.client.requests[0]["system"]
    assert "Do not apply that draft until the" in prompt
    assert "user chooses it" in prompt
    assert "Never invent project facts" in prompt


def test_proposed_rewrite_requires_wording_choice_then_preview_approval(tmp_path):
    original = "Keep this technical wording intact."
    draft = "Preserve the technical wording."
    request = "Rewrite the final sentence more concisely."
    spec = {"request": request, "summary": "Shorten the final sentence", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": original, "replace": draft, "tracked": True, "author": "Abraham Borg"}}]}
    fake = FakeClient(resp(tool("ask_user", {"message": "Here is a proposed shorter sentence.",
        "choices": [{"kind": "rephrase", "label": "Use this sentence", "wording": draft}]})),
        resp(tool("submit_editspec", {"spec": spec})))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    before = source.read_bytes()
    task.attach([source])
    view = task.perform("send", text=request)
    assert view["stage"] == "question" and not view["action"]
    assert draft in "\n".join(view["questions"])
    assert draft not in task.word_sources and not task.results
    view = task.perform("send", text="1")
    assert view["stage"] == "preview"
    assert draft in task.word_sources and not task.results
    assert task.session.outcome_approval == ""
    task.perform("primary", token=view["token"])
    assert task.stage == "done"
    from zipfile import ZipFile
    from lxml import etree
    with ZipFile(task.results[str(source)]) as package:
        root = etree.fromstring(package.read("word/document.xml"))
    inserted = root.xpath("//w:ins//w:t/text()", namespaces={"w":
        "http://schemas.openxmlformats.org/wordprocessingml/2006/main"})
    assert "".join(inserted) == draft
    assert source.read_bytes() == before


def test_recovery_plan_and_preview_stay_in_same_task_without_json(tmp_path, monkeypatch):
    from spec_surgeon import continuous_recovery
    from spec_surgeon.recovery import RecoveryOptions
    refusal = resp(tool("submit_editspec", {"spec": {"request": REQUEST, "summary": "Title",
        "unsupported": "Try another method", "proposal": "Investigate"},
        "custom_transform": "outside_limits", "custom_transform_reason": "Use outcome checks."}))
    fake = FakeClient(refusal, resp(tool("propose_plan", {"contract": CONTRACT,
                      "rationale": "Format the title."})))
    monkeypatch.setattr(continuous_recovery, "options_for", lambda *a, **kw:
                        RecoveryOptions(render=False, critic_review=False, visual_review=False))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    before = source.read_bytes()
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "plan"
    assert view["effects"] and not task.results
    assert not any('"effects"' in message["text"] for message in view["messages"])
    view = task.perform("primary", token=view["token"])
    assert view["stage"] == "ready", view
    assert not task.results
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and Path(task.results[str(source)]).is_file()
    assert source.read_bytes() == before


def test_visual_findings_are_visible_and_bound_to_current_action(tmp_path):
    from spec_surgeon.vision import VisualReview
    task = task_for(tmp_path)
    task.attach([document(tmp_path / "Spec.docx")])
    old_view = task.perform("send", text=REQUEST)
    task.session.preview.review = VisualReview(findings=[{"severity": "block", "file": "Spec.docx",
                                                        "page": 2, "text": "The title overlaps the header."}])
    view = task.view()
    assert "reviewed the flagged pages" in view["action"]
    assert "Spec.docx · page 2: The title overlaps the header." in view["warnings"]
    assert task.session.preview.review_ack == ""
    with pytest.raises(ValueError, match="changed"):
        task.perform("primary", token=old_view["token"])
    assert task.session.preview.review_ack == ""


def test_replayed_choice_labels_never_become_approved_document_wording(tmp_path):
    task = task_for(tmp_path)
    task.attach([document(tmp_path / "Spec.docx")])
    task.decisions = [REQUEST, "MODEL LABEL CONTAINING UNAPPROVED WORDS"]
    task.word_sources = [REQUEST]
    task.stage = "resume"
    task.perform("primary", token=task.action_token())
    assert "MODEL LABEL CONTAINING UNAPPROVED WORDS" in task.session.conversation.request
    assert "MODEL LABEL CONTAINING UNAPPROVED WORDS" not in task.session.conversation.sources


def test_later_revision_retains_earlier_copies_and_originals(tmp_path):
    second = copy.deepcopy(SPEC)
    second["ops"][0]["params"]["run"] = {"b": True, "i": True}
    fake = FakeClient(
        resp(tool("submit_editspec", {"spec": SPEC})),
        resp(tool("submit_editspec", {"spec": second})))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    before = source.read_bytes()
    task.attach([source])
    view = task.perform("send", text=REQUEST)
    task.perform("primary", token=view["token"])
    old_output = Path(task.results[str(source)])
    old_bytes = old_output.read_bytes()
    view = task.perform("send", text="Also make that title italic.")
    task.perform("primary", token=view["token"])
    assert Path(task.results[str(source)]) != old_output
    assert old_output.read_bytes() == old_bytes
    assert source.read_bytes() == before
    run = Document(task.results[str(source)]).paragraphs[1].runs[0]
    assert run.bold and run.italic


def test_custom_method_is_prepared_internally_but_only_delivered_after_review(tmp_path, monkeypatch):
    from test_codegen import GOOD_OP
    from spec_surgeon import compiler
    def forbid(*args):
        raise AssertionError("Custom preparation must not read neighboring files")
    monkeypatch.setattr(compiler, "input_files_near", forbid)
    request = "Set the header distance to 900 twips."
    fake = FakeClient(resp(tool("submit_editspec", {"spec": {"request": request, "summary": "Move header",
        "unsupported": "Use a custom method", "proposal": "Set the header distance to 900 twips"},
        "custom_transform": "possible", "custom_transform_reason": "Formatting only"})),
        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    view = task.perform("send", text=request)
    assert len(fake.requests) == 2
    assert view["stage"] == "preview"
    assert task.session.generated.passed and not task.results
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and Path(task.results[str(source)]).is_file()
    assert Document(task.results[str(source)]).sections[0].header_distance.twips == 900


def test_partial_failure_recovers_automatically_and_can_reopen(tmp_path, monkeypatch):
    from spec_surgeon.recovery import RecoveryOptions
    from spec_surgeon import batch
    from spec_surgeon.verify.suite import CheckResult
    source = document(tmp_path / "First.docx")
    second = document(tmp_path / "Second.docx")
    original = batch._render_and_compare
    injected = False
    def fail_one_render(result, *args, **kwargs):
        nonlocal injected
        original(result, *args, **kwargs)
        if not injected:
            failed = next(f for f in result.files if f.source == second)
            failed.record.set(CheckResult("V6", "Page rendering", "FAIL", "Renderer stopped unexpectedly"))
            injected = True
    monkeypatch.setattr(batch, "_render_and_compare", fail_one_render)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": SPEC})),
        resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": SPEC}})))
    task = task_for(tmp_path, fake, journal=tmp_path / "journal.json")
    monkeypatch.setattr(task, "_recovery_options", lambda: RecoveryOptions(render=False, critic_review=False,
                                                visual_review=False, include_nearby_inputs=False))
    task.attach([source, second])
    before = [p.read_bytes() for p in (source, second)]
    view = task.perform("send", text=REQUEST)
    view = task.perform("primary", token=view["token"])
    assert len(task.results) == 1
    assert task.session.recovery and view["stage"] == "ready"
    assert view["document_count"] == 1
    restored = task_for(tmp_path, journal=task.journal)
    restored.restore()
    assert restored.session.outcome_approval == ""
    view = restored.perform("primary", token=restored.action_token())
    assert view["stage"] == "ready" and len(restored.results) == 1
    restored.perform("primary", token=view["token"])
    assert len(restored.results) == 2
    assert [p.read_bytes() for p in (source, second)] == before


def test_only_attached_files_are_inspected_not_neighboring_project_data(tmp_path, monkeypatch):
    from spec_surgeon import compiler
    source = document(tmp_path / "Spec.docx")
    (tmp_path / "PROJECT.json").write_text('{"secret": "unrelated project wording"}')
    def forbid(*args):
        raise AssertionError("Unattached neighboring files must not be discovered")
    monkeypatch.setattr(compiler, "input_files_near", forbid)
    monkeypatch.setattr(compiler, "load_project_memory", forbid)
    monkeypatch.setattr(compiler, "library_index", forbid)
    task = task_for(tmp_path)
    task.attach([source])
    task.perform("send", text=REQUEST)
    assert task.session.conversation.batch.inputs == []
    assert task.session.conversation.memory == {}
    assert "unrelated project wording" not in str(task.session.client.requests)
    tools = task.session.client.requests[0]["tools"]
    assert not {"list_library", "read_library_item"}.intersection(t["name"] for t in tools)
    choices = next(t for t in tools if t["name"] == "ask_user")["input_schema"]["properties"]["choices"]["items"]
    assert "recipe" not in choices["properties"]["kind"]["enum"]
    assert "YOUR LIBRARY" not in task.session.client.requests[0]["system"]


@pytest.mark.parametrize("change_source", [False, True])
def test_more_budget_continues_pending_custom_preparation(tmp_path, change_source):
    from test_codegen import GOOD_OP
    from spec_surgeon.gui import TransformFailed
    request = "Set the header distance to 900 twips."
    fake = FakeClient(resp(tool("submit_editspec", {"spec": {"request": request, "summary": "Move header",
        "unsupported": "Use a custom method", "proposal": request},
        "custom_transform": "possible", "custom_transform_reason": "Formatting only"})),
        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    task = task_for(tmp_path, fake)
    source = document(tmp_path / "Spec.docx")
    task.attach([source])
    task._budget.max_calls = 1
    with pytest.raises(TransformFailed):
        task.perform("send", text=request)
    assert task.stage == "budget" and task.session.pending_custom_transform
    conversation = task.session.conversation
    if change_source:
        source.write_bytes(source.read_bytes() + b"changed")
        with pytest.raises(ValueError, match="original files changed"):
            task.perform("primary", token=task.action_token())
        assert len(fake.requests) == 1 and not task.results
        return
    view = task.perform("primary", token=task.action_token())
    assert view["stage"] == "preview" and not task.results
    assert task.session.conversation is conversation
    assert task.session.generated.passed and task.session.pending_custom_transform is None
    assert len(fake.requests) == task._budget.calls == 2
    task.perform("primary", token=view["token"])
    assert Document(task.results[str(source)]).sections[0].header_distance.twips == 900


@pytest.mark.parametrize("reopen", [False, True])
def test_more_budget_resumes_unadopted_recovery_checkpoint(tmp_path, reopen, monkeypatch):
    from spec_surgeon import llm, recovery
    from test_recovery import candidate, margin, opts, spec, submit
    source = document(tmp_path / "Spec.docx")
    goal = spec([margin(), margin("right", 2880)])
    chain = {"kind": "chain", "chain": {"stages": [{"spec": spec([op])} for op in goal["ops"]]}}
    fake = FakeClient(submit(candidate(goal)), submit(chain))
    task = task_for(tmp_path, fake, journal=tmp_path / "journal.json")
    task.attach([source])
    task.decisions = task.word_sources = [goal["request"]]
    task._budget.max_calls = 1
    # Model execution pauses after a failed candidate has been recorded. The coordinator
    # has the checkpoint path, as it does when interrupted before adopting the result.
    checkpoint = tmp_path / "recovery"
    task.session.recovery_checkpoint = checkpoint
    with llm.budget_scope(task._budget):
        paused = recovery.recover([source], goal["request"], checkpoint, spec=goal,
            options=opts(critic_review=False, visual_review=False, include_nearby_inputs=False), client=fake)
    assert paused.paused_reason and len(paused.attempts) == 1
    task.stage = "budget"
    task.save()
    old_cost = task._budget.estimated_cost_usd
    if reopen:
        task = task_for(tmp_path, fake, journal=task.journal)
        task.restore()
    monkeypatch.setattr(task, "_recovery_options", lambda:
                        opts(critic_review=False, visual_review=False, include_nearby_inputs=False))
    view = task.perform("primary", token=task.action_token())
    assert view["stage"] == "ready" and not task.results
    assert task.session.recovery.out_dir == checkpoint
    assert len(task.session.recovery.attempts) == 2
    assert len(fake.requests) == task._budget.calls == 2
    assert task._budget.estimated_cost_usd >= old_cost
    assert task.session.recovery.resource_history[0]["calls"] == 1
    task.perform("primary", token=view["token"])
    assert Path(task.results[str(source)]).is_file()


def test_recovery_resume_refuses_changed_checkpoint_sources(tmp_path):
    from spec_surgeon import recovery
    from test_recovery import margin, opts, spec
    source = document(tmp_path / "Spec.docx")
    task = task_for(tmp_path, FakeClient())
    task.attach([source])
    goal = spec([margin()])
    ready = recovery.prepare_recovery([source], goal["request"], tmp_path / "recovery", spec=goal, options=opts())
    task.session.recovery_checkpoint = ready.out_dir
    task.stage = "budget"
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed"):
        task.perform("primary", token=task.action_token())
    assert not task.session.client.requests and not task.results


@pytest.mark.parametrize("reopen", [False, True])
def test_partial_recovery_can_export_again_after_an_answer(tmp_path, reopen, monkeypatch):
    from spec_surgeon import recovery
    from test_recovery import candidate, margin, opts, spec, submit
    from test_recovery_resume import distinct_failures
    paths = [document(tmp_path / name) for name in ("First.docx", "Second.docx")]
    original = [p.read_bytes() for p in paths]
    goal = spec([margin()])
    fake = FakeClient(submit(candidate(goal)),
                      resp(tool("need_user", {"question": "Use these margins on the second document?"})),
                      submit(candidate(goal)))
    task = task_for(tmp_path, fake, journal=tmp_path / "journal.json")
    task.attach(paths)
    task.decisions = task.word_sources = [goal["request"]]
    result = recovery.recover(paths, goal["request"], tmp_path / "recovery", spec=goal,
                              options=opts(include_nearby_inputs=False),
                              prior_failures=distinct_failures(paths), client=fake)
    assert len(result.ready) == 1 and result.questions
    task.session._adopt_recovery(result)
    task._settle(lambda _: None, preview=False)
    task.perform("primary", token=task.action_token())
    first_output = Path(task.results[str(paths[0])])
    first_bytes = first_output.read_bytes()
    assert task.stage == "question" and len(task.results) == 1
    if reopen:
        task = task_for(tmp_path, fake, journal=task.journal)
        task.restore()
        task.perform("primary", token=task.action_token())
    monkeypatch.setattr(task, "_recovery_options", lambda:
                        opts(critic_review=False, visual_review=False, include_nearby_inputs=False))
    view = task.perform("send", text="Yes, use those margins on the second document.")
    assert view["stage"] == "ready" and len(task.results) == 1
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and len(task.results) == 2
    assert first_output.read_bytes() == first_bytes
    assert Path(task.results[str(paths[1])]).parent != first_output.parent
    assert all(Path(output).is_file() for output in task.results.values())
    assert [p.read_bytes() for p in paths] == original
    assert len(fake.requests) == 3
