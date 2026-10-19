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
    assert not task.results
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
    labels = "\n".join(c["label"] for c in view["choices"])
    assert "Paragraph 2" in labels and "Paragraph 3" in labels
    view = task.perform("send", text="2")
    assert view["stage"] == "preview"
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
    assert draft in "\n".join(c["label"] + " " + " ".join(c["words"]) for c in view["choices"])
    assert draft not in task.word_sources and not task.results
    view = task.perform("send", text="1")
    assert view["stage"] == "preview"
    assert draft in task.word_sources and not task.results
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
    assert view["stage"] == "preview", view   # the recovery plan is carried out internally; one decision remains
    assert view["effects"] and not task.results
    assert not any('"effects"' in message["text"] for message in view["messages"])
    assert view["action"] == "Create edited copies"
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
    assert task.session.conversation.batch.inputs == []   # nothing beside the attachments is ever read
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
                                                visual_review=False))
    task.attach([source, second])
    before = [p.read_bytes() for p in (source, second)]
    view = task.perform("send", text=REQUEST)
    view = task.perform("primary", token=view["token"])
    assert len(task.results) == 1
    assert task.session.recovery and view["stage"] == "preview"
    assert view["document_count"] == 1
    restored = task_for(tmp_path, journal=task.journal)
    restored.restore()
    view = restored.perform("primary", token=restored.action_token())
    assert view["stage"] == "preview" and len(restored.results) == 1
    restored.perform("primary", token=view["token"])
    assert len(restored.results) == 2
    assert [p.read_bytes() for p in (source, second)] == before


def test_only_attached_files_are_inspected_not_neighboring_project_data(tmp_path):
    source = document(tmp_path / "Spec.docx")
    (tmp_path / "PROJECT.json").write_text('{"secret": "unrelated project wording"}')
    (tmp_path / "values.csv").write_text("name,value\nsecret,unrelated table wording\n")
    task = task_for(tmp_path)
    task.attach([source])
    task.perform("send", text=REQUEST)
    assert task.session.conversation.batch.inputs == []
    assert "unrelated project wording" not in str(task.session.client.requests)
    assert "unrelated table wording" not in str(task.session.client.requests)
    tools = task.session.client.requests[0]["tools"]
    assert not {"list_library", "read_library_item"}.intersection(t["name"] for t in tools)
    choices = next(t for t in tools if t["name"] == "ask_user")["input_schema"]["properties"]["choices"]["items"]
    assert "recipe" not in choices["properties"]["kind"]["enum"]
    assert "YOUR LIBRARY" not in task.session.client.requests[0]["system"]
    assert "PROJECT MEMORY" not in str(task.session.client.requests[0]["messages"])


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
            options=opts(critic_review=False, visual_review=False), client=fake)
    assert paused.paused_reason and len(paused.attempts) == 1
    task.stage = "budget"
    task.save()
    old_cost = task._budget.estimated_cost_usd
    if reopen:
        task = task_for(tmp_path, fake, journal=task.journal)
        task.restore()
    monkeypatch.setattr(task, "_recovery_options", lambda:
                        opts(critic_review=False, visual_review=False))
    view = task.perform("primary", token=task.action_token())
    assert view["stage"] == "preview" and not task.results
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
                              options=opts(),
                              prior_failures=distinct_failures(paths), client=fake)
    assert len(result.ready) == 1 and result.questions
    task.session._adopt_recovery(result)
    task._settle(lambda _: None)
    task.perform("primary", token=task.action_token())
    first_output = Path(task.results[str(paths[0])])
    first_bytes = first_output.read_bytes()
    assert task.stage == "question" and len(task.results) == 1
    if reopen:
        task = task_for(tmp_path, fake, journal=task.journal)
        task.restore()
        task.perform("primary", token=task.action_token())
    monkeypatch.setattr(task, "_recovery_options", lambda:
                        opts(critic_review=False, visual_review=False))
    view = task.perform("send", text="Yes, use those margins on the second document.")
    assert view["stage"] == "preview" and len(task.results) == 1
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and len(task.results) == 2
    assert first_output.read_bytes() == first_bytes
    assert Path(task.results[str(paths[1])]).parent != first_output.parent
    assert all(Path(output).is_file() for output in task.results.values())
    assert [p.read_bytes() for p in paths] == original
    assert len(fake.requests) == 3


def test_a_mixed_batch_gets_one_plan_per_layout_and_one_decision(tmp_path):
    from make_fixtures import build, build_styles, build_rich
    from spec_surgeon.fingerprint import fingerprint
    from spec_surgeon.package import DocxPackage
    typed = tmp_path / "a" / "21 13 13.docx"
    styled = tmp_path / "b" / "21 13 16.docx"
    other = tmp_path / "c" / "21 13 19.docx"
    for path, data in ((typed, build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")),
                       (styled, build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")),
                       (other, build_rich("21 13 19", "PREACTION SPRINKLER SYSTEMS"))):
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
    pid = lambda p: fingerprint(DocxPackage.from_path(p))["pattern_id"]  # noqa: E731
    bold = {"run": {"b": True}, "tracked": False}
    submission = {"summary": "Bold body paragraphs in two layouts", "layouts": [
        {"id": "A", "label": "typed numbers, no styles", "pattern_ids": [pid(typed)],
         "spec": {"summary": "by text", "mode": "formatting", "assumptions": ["Body paragraphs start with A. to E."],
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": r"^[A-E]\.\t"}}, "params": bold}]}},
        {"id": "B", "label": "styled body", "pattern_ids": [pid(styled), pid(other)],
         "spec": {"summary": "by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara", "Normal"]}}, "params": bold}]}}]}
    fake = FakeClient(resp(tool("submit_editspec", {"spec": submission, "message": "I’ll bold the body paragraphs; your documents use two layouts."})))
    task = task_for(tmp_path, fake)
    task.attach([typed, styled, other])
    before = [p.read_bytes() for p in (typed, styled, other)]
    view = task.perform("send", text="Make the body paragraphs bold.")
    assert view["stage"] == "preview" and view["action"] == "Create edited copies"
    assert [m["text"] for m in view["messages"] if m["role"] == "assistant"] == [
        "I’ll bold the body paragraphs; your documents use two layouts."]
    groups = view["groups"]
    assert [(g["id"], g["label"], g["count"]) for g in groups] == [("A", "typed numbers, no styles", 1), ("B", "styled body", 2)]
    assert groups[0]["assumptions"] == ["Body paragraphs start with A. to E."]
    assert all(change["summary"].endswith(".") for g in groups for change in g["changes"])
    assert len(view["samples"]) == 3 and not view["set_aside"]   # one sample per structure pattern
    task.perform("primary", token=view["token"])
    assert task.stage == "done" and len(task.results) == 3
    assert [p.read_bytes() for p in (typed, styled, other)] == before
    assert len(fake.requests) == 1


def _layout_submission(tmp_path):
    from make_fixtures import build, build_rich, build_styles
    from spec_surgeon.fingerprint import fingerprint
    from spec_surgeon.package import DocxPackage
    typed, styled, other = tmp_path / "a" / "21 13 13.docx", tmp_path / "b" / "21 13 16.docx", tmp_path / "c" / "21 13 19.docx"
    for path, data in ((typed, build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")),
                       (styled, build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")),
                       (other, build_rich("21 13 19", "PREACTION SPRINKLER SYSTEMS"))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    pid = lambda p: fingerprint(DocxPackage.from_path(p))["pattern_id"]  # noqa: E731
    bold = {"run": {"b": True}, "tracked": False}
    submission = {"summary": "Bold body paragraphs in two layouts", "layouts": [
        {"id": "A", "label": "typed numbers", "pattern_ids": [pid(typed)],
         "spec": {"summary": "by text", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": r"^[A-E]\.\t"}}, "params": bold}]}},
        {"id": "B", "label": "styled body", "pattern_ids": [pid(styled)],
         "spec": {"summary": "by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": bold}]}}]}
    return submission, [typed, styled, other]


def test_after_one_layouts_recovery_is_saved_the_next_layouts_documents_are_checked(tmp_path, monkeypatch):
    """Both layouts set their document aside at run time: recovery takes layout A first; once its copies are saved
    the coordinator moves on to layout B by itself, and the queue survives a reopened conversation. (The compiler
    refuses a plan that fails on its own documents, so the plan is set as the outcome directly.)"""
    from spec_surgeon import recovery
    submission, docs = _layout_submission(tmp_path)
    plan = {"kind": "layouts", "request": "Make the body paragraphs bold.", "summary": submission["summary"],
            "layouts": submission["layouts"]}
    for layout in plan["layouts"]:
        layout["spec"]["request"] = plan["request"]
        layout["spec"]["ops"][0]["expect"] = {"text_diffs": 5}
    calls = []

    def fake_recover(run_dir, out_dir, *, request=None, options=None, client=None, log=None):
        calls.append(Path(run_dir).name)
        out = Path(out_dir)
        (out / "outputs").mkdir(parents=True, exist_ok=True)   # a delivery is not part of this test
        return recovery.RecoveryResult(out_dir=out, request=str(request or ""))
    monkeypatch.setattr(recovery, "recover_from_run", fake_recover)
    journal = tmp_path / "journal.json"
    task = task_for(tmp_path, journal=journal)
    task.attach(docs[:2])
    task.session._set_outcome(spec=plan, source="test")
    task._settle(lambda m: None)
    assert task.stage == "preview"
    view = task.view()
    assert [item["name"] for item in view["set_aside"]] == ["21 13 13.docx", "21 13 16.docx"]
    view = task.perform("primary", token=view["token"])
    run = task.session.last_run_dir
    assert calls == ["layout-A"] and task.session.pending_layout_recovery == [run / "layout-B"]
    assert view["stage"] == "preview"                                # layout A's recovery awaits its review
    assert json.loads(journal.read_text(encoding="utf-8"))["pending_layouts"] == [str(run / "layout-B")]
    reopened = task_for(tmp_path, journal=journal)                   # a reopened conversation keeps the queue
    assert reopened.restore() and reopened._resume_layouts == [str(run / "layout-B")]
    # the empty stub recovery has nothing to export; the coordinator's own step to the next layout is the subject
    task.session.last_recovery, task.session.recovery = task.session.recovery, None
    task.session.resolve_next_layout(lambda m: None)
    assert calls == ["layout-A", "layout-B"] and task.session.pending_layout_recovery == []
