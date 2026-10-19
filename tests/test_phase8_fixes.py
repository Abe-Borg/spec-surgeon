"""The Phase 8 review fixes (2026-10-15).

After Phase 8 was merged with master (the window rework, diagnostics and session bundles; PR #11, merge 54e197a), the
branch went through three reviews: an internal adversarially-verified review (``review-N``), a Codex review
(``codex-N``) and the merge's own verifiers (``merge-verify-N``). Every finding was reproduced on the merged code
first; each test below fails on the code before its fix (a few are guards that hold either way, marked so). Scripted
fake clients only — no test calls Claude.

Which finding each test pins:

The conversation (conversation.py, compiler.py, editspec.py)
* review-0 — a submitted spec the checks cannot read goes back to Claude as REJECTED (the conversation keeps pairing),
  and ``validate_spec`` refuses every wrongly shaped spec-level field with code ``spec``:
  test_validate_spec_refuses_a_wrongly_shaped_field_as_a_spec_error,
  test_validate_spec_refuses_wrong_shapes_of_questions_and_op_names,
  test_check_submission_answers_a_malformed_spec_with_errors,
  test_a_malformed_spec_goes_back_to_claude_as_rejected_and_the_conversation_pairs,
  test_a_check_that_raises_is_answered_as_rejected.
* review-4 — questions sent as one string are one question; non-list questions/choices are dropped and said; an
  answered turn's choices cannot be picked after the next turn failed:
  test_ask_user_questions_as_one_string_are_one_question, test_a_spec_s_questions_as_one_string_are_one_question,
  test_non_list_questions_and_choices_are_dropped_and_said_never_raised,
  test_the_choices_of_an_answered_turn_cannot_be_picked_after_the_next_turn_failed.
* review-6 — ``clean_wording`` refuses every character that renders as nothing or blank (``conversation.invisible``):
  test_clean_wording_refuses_a_character_the_user_cannot_see,
  test_clean_wording_refuses_every_default_ignorable_code_point_and_keeps_visible_text,
  test_a_choice_with_a_hidden_character_is_dropped_and_never_approved.
* review-1 — the spec the app gets carries the user's request, never Claude's:
  test_an_accepted_spec_carries_the_user_s_request_not_claude_s,
  test_compile_mode_and_unsupported_specs_carry_the_user_s_request.
* review-24 — conversation.json holds ``approved_words`` and ``answers``:
  test_conversation_json_holds_the_approved_words_and_the_answers.
* codex-3 — files that share a name in different folders get unique labels (``compiler.file_labels``):
  test_file_labels_are_names_unless_two_files_share_one, test_every_tool_reads_the_file_its_label_names,
  test_the_summary_and_profiles_claude_sees_use_the_labels, test_a_single_folder_batch_reads_as_before (a guard),
  test_unprofiled_and_unreadable_files_are_named_by_label, test_the_gate_s_errors_name_files_by_label,
  test_claude_addresses_the_second_same_named_file_in_a_conversation.

Claude's visual check and the advisor (vision.py, advisor.py)
* review-5 / merge-verify-2 — ``review_samples`` never raises, whatever the answer holds; a review read part-way keeps
  nothing: test_an_answer_that_cannot_be_read_is_a_skipped_review_never_an_exception,
  test_a_review_read_part_way_keeps_nothing, test_finding_items_of_any_type_are_read_or_dropped,
  test_findings_missing_is_a_review_without_findings, test_pages_that_cannot_be_sent_are_a_skip_with_their_traceback,
  test_a_log_that_raises_never_stops_the_review.
* merge-verify-3 — a ``client_factory`` error is classified like the call's:
  test_a_client_factory_error_is_classified_like_a_call_error.
* review-26 — the view_pages budget figure (about 20 letter pages) is derived from ``est_tokens``:
  test_the_view_pages_budget_figure_is_derived_from_est_tokens.
* review-28 — the advisor names the real sources of approved words:
  test_the_advisor_tells_claude_which_words_it_may_place.

The window's session (gui.Session)
* review-2 — a template spec from the batch folder keeps the conversation:
  test_a_template_spec_from_the_batch_folder_keeps_the_conversation,
  test_a_removed_file_still_ends_the_match_when_a_spec_names_a_template (a guard).
* review-16 — a vanished folder is no match, never an exception: test_a_vanished_folder_is_no_match_never_an_exception,
  test_the_windows_preview_with_a_vanished_folder_reports_on_the_page.
* review-3 / review-27 / merge-verify-4 — only ``TransformFailed`` is told to Claude, once:
  test_no_passing_attempt_is_told_to_claude_once_and_saved,
  test_an_api_error_while_writing_is_a_recorded_failure_told_to_claude,
  test_a_defect_while_writing_is_never_told_to_claude_as_a_verification_result.
* review-8 — Preview tries a stale custom transform again (``retrial/``):
  test_preview_tries_a_stale_custom_transform_again_and_run_then_runs,
  test_a_current_custom_transform_is_not_tried_again (a guard),
  test_a_saved_operation_whose_files_changed_is_tried_again_or_refused,
  test_trying_again_with_rendering_runs_the_visual_check_again,
  test_a_preview_that_stops_leaves_the_transform_unrunnable_until_previewed.
* review-18 — a saved operation is tried (``library-trial/``) before anything is replaced:
  test_a_saved_operation_that_fails_leaves_the_conversation_and_its_preview,
  test_a_saved_operation_that_passes_goes_into_a_new_folder.

The command line, the library and the preview (cli.py, library.py, preview.py)
* review-9 — ``do`` re-checks the approval token after y/N or --yes:
  test_do_refuses_a_file_edited_while_the_question_waits, test_do_yes_refuses_a_template_edited_during_the_visual_check,
  test_do_codegen_refuses_a_file_edited_after_the_transform_was_previewed.
* review-11 / review-20 / codex-1 — --allow-codegen needs Claude's "possible" verdict:
  test_do_allow_codegen_needs_claudes_possible_verdict.
* review-12 — --replace never takes the other kind's name:
  test_the_library_refuses_a_name_the_other_kind_holds_even_with_replace,
  test_save_as_with_replace_never_takes_the_other_kinds_name.
* review-13 — end of input / Ctrl-C at a prompt is a stop or a no: test_end_of_input_at_the_wording_approval_stops,
  test_ctrl_c_at_the_reply_prompt_stops, test_end_of_input_at_the_run_question_is_a_no,
  test_end_of_input_at_the_custom_transform_run_question_is_a_no,
  test_end_of_input_at_the_library_delete_question_keeps_the_item.
* review-25 — the command line's visual check carries the conversation:
  test_the_visual_check_gets_the_terminal_conversation.
* review-21 — the command line's gate on a blocking finding (guards: the code was right, these keep it so):
  test_the_command_line_gate_on_a_blocking_finding_for_a_spec,
  test_the_command_line_gate_on_a_blocking_finding_for_a_custom_transform.
* review-10 — preview samples with the same name keep their own outputs:
  test_preview_samples_with_the_same_name_keep_their_own_outputs.

The window (ui/app.py, ui/chat.py, diagnostics.py)
* codex-2 — the Render pages option is part of the preview key:
  test_turning_render_pages_on_or_off_after_a_preview_needs_a_new_preview.
* review-14 — an operation picked after a loaded spec or recipe can run and preview again; the Saved spec bar goes:
  test_an_operation_picked_after_a_loaded_spec_can_run_and_preview_again,
  test_a_typed_request_that_replaces_a_loaded_spec_takes_the_saved_spec_bar_away.
* review-15 — a chat that ended leaves the screen: test_load_saved_spec_ends_the_chat_on_screen (a guard: the merge had
  fixed it), test_use_the_whole_folder_again_ends_the_chat_on_screen,
  test_a_reply_after_the_folder_changed_ends_the_chat_instead_of_failing.
* review-17 — Save to library and the acknowledgement only while the preview is current:
  test_save_and_acknowledge_are_live_only_while_the_preview_is_current.
* review-19 — a choice's detail is plain text: test_a_choice_s_detail_is_shown_as_plain_text.
* merge-verify-0 / merge-verify-1 — the diagnostics bundle keeps the last run and adds newer session folders under
  ``results-later/``: test_make_bundle_mirrors_more_folders_beside_the_run_never_a_document_or_a_folder_twice,
  test_the_window_bundle_after_a_run_and_a_reply_holds_both,
  test_the_window_bundle_after_a_new_request_failed_holds_its_conversation_and_the_last_run,
  test_the_window_bundle_before_any_run_is_the_preview_with_the_conversation_s_newer_folder.

The custom-transform sandbox (codegen.py, sandbox_runner.py)
* review-7 — the whitelist is an allowlist for module attributes (``codegen.MODULE_ATTRS``), refuses any other use of a
  module object, lxml's file readers/writers and frame/generator internals; the sandbox runner adds an audit hook that
  keeps every file-system change inside its folder and refuses processes, sockets and native code:
  test_the_whitelist_refuses_every_route_from_an_allowed_module_to_the_file_system,
  test_the_whitelist_still_accepts_what_an_honest_op_uses,
  test_every_module_attribute_the_reference_ops_use_is_allowed (a guard),
  test_a_saved_operation_that_reaches_past_the_whitelist_is_refused,
  test_the_sandbox_guard_keeps_writes_inside_its_folder, test_the_sandbox_still_runs_an_honest_op (a guard).
* codex-3 (the transform writer) — the samples are listed by the labels its tools take:
  test_the_transform_writer_lists_same_named_samples_by_their_labels.

The test run sealed off from the machine (tests/conftest.py)
* review-23 — no test reaches Windows Credential Manager (only tests/test_credentials.py's throw-away round trip):
  test_no_test_reads_or_writes_the_key_saved_on_this_computer, test_a_window_never_loads_or_overwrites_the_saved_key,
  test_credentials_py_is_the_only_way_to_credential_manager (a guard),
  test_a_key_a_test_writes_into_the_environment_is_gone_after_it.
* review-22 — the model is the default whatever ``SPEC_SURGEON_MODEL`` says:
  test_the_model_is_the_default_whatever_the_machine_sets,
  test_a_model_bound_before_conftest_ran_is_pinned_for_the_test,
  test_a_model_set_on_the_developer_s_machine_does_not_reach_the_tests.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, api_error, raise_on_enter, resp, text, tool
from make_fixtures import build, build_rich, build_template
from spec_surgeon import advisor, cli, credentials, library, llm, vision
from spec_surgeon import compiler as compiler_module
from spec_surgeon import diagnostics as diag
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.codegen import generate_op
from spec_surgeon.compiler import (_Batch, build_messages, check_submission, compile_request, file_labels,
                                   placed_words, untraceable)
from spec_surgeon.conversation import INVISIBLE_RANGES, Conversation, check_pairing, clean_wording
from spec_surgeon.editspec import SpecError, load_spec, validate_spec
from spec_surgeon.gui import Session, TransformFailed
from spec_surgeon.library import LibraryError
from spec_surgeon.preview import SampleView
from test_compiler import (remove_outcome_proposal, header_outcome_proposal, outcome_cli_flags,
                           generate_reviewed_custom)
from spec_surgeon.vision import review_samples
from test_codegen import GOOD_OP

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
# Claude's answer when no op does it: unsupported, with its offer of a one-off custom transform (a set_header_distance
# op), and the writer's submission of test_codegen's GOOD_OP.
UNSUPPORTED = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
               "proposal": "set_header_distance"}
OFFER = {"spec": UNSUPPORTED, "custom_transform": "possible"}
OFFER_TURN = resp(tool("submit_editspec", OFFER))
WRITER = resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "w1"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.fixture
def qt(monkeypatch):
    """A Qt application (the test skips when Qt cannot load, unless SPEC_SURGEON_REQUIRE_QT=1); every window a test
    appends to the list is closed after it."""
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    close_windows(app, windows)


def docx(d: Path):
    return sorted(d.glob("*.docx"))


# =====================================================================================================================
# The conversation: review-0, review-4, review-6, review-1, review-24, codex-3
# =====================================================================================================================

PERMIT_STAMP = "ISSUED FOR PERMIT 2026-11-02"


def conv(folder, fake, request="remove all headers and footers", **kw) -> Conversation:
    return Conversation(request, sorted(folder.glob("*.docx")), client=fake, **kw)


def submit(spec, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid))


def ask(tid="a1", **inp):
    return resp(tool("ask_user", inp, tid))


def assert_well_formed(fake):
    for i, req in enumerate(fake.requests):
        check_pairing(req["messages"])
        if i:
            prev = fake.requests[i - 1]
            assert req["messages"][:len(prev["messages"])] == prev["messages"]


# ---- review-0: a malformed spec is a rejection, never a wedged conversation -------------------------------------

MALFORMED = [
    ({"review": [{"sample_files": 2}]}, "'review' must be an object"),
    ({"review": ["sample_files"]}, "'review' must be an object"),
    ({"review": "source"}, "'review' must be an object"),
    ({"applies_to": [{"pattern_ids": ["p1"]}]}, "'applies_to' must be an object"),
    ({"applies_to": "all"}, "'applies_to' must be an object"),
    ({"assertions": True}, "'assertions' must be a list"),
    ({"assertions": {"kind": "effective_font", "value": "Arial"}}, "'assertions' must be a list"),
    ({"assumptions": "the cover is section 0"}, "'assumptions' must be a list of strings"),
    ({"assumptions": 3}, "'assumptions' must be a list of strings"),
    ({"assumptions": ["ok", 3]}, "'assumptions' must be a list of strings"),
    ({"request": 7}, "'request' must be text"),
    ({"summary": None}, "'summary' must be text"),
    ({"proposal": ["x"]}, "'proposal' must be text"),
    ({"applies_to": {"pattern_ids": "b20e6c1da8"}}, "pattern_ids must be a list"),
    ({"applies_to": {"pattern_ids": [["b20e6c1da8"]]}}, "pattern_ids must be a list"),
    ({"applies_to": {"or": "any"}}, "applies_to.or can only be"),
    ({"review": {"sample_files": "two"}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": 0}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": -1}}, "sample_files must be a whole number"),
    ({"review": {"sample_files": True}}, "sample_files must be a whole number"),
    ({"mode": ["formatting"]}, "mode must be one of"),
]


@pytest.mark.parametrize("change, message", MALFORMED)
def test_validate_spec_refuses_a_wrongly_shaped_field_as_a_spec_error(change, message):
    spec = {**copy.deepcopy(REMOVE), **change}
    with pytest.raises(SpecError) as e:
        validate_spec(spec)
    assert e.value.code == "spec" and message in e.value.reason


def test_validate_spec_refuses_wrong_shapes_of_questions_and_op_names():
    for bad in ("Which sections?", ["ok", 2], 3):
        with pytest.raises(SpecError) as e:
            validate_spec({**copy.deepcopy(REMOVE), "questions": bad})
        assert e.value.code == "spec" and "'questions' must be a list of strings" in e.value.reason
    spec = copy.deepcopy(REMOVE)
    spec["ops"][0]["op"] = ["remove_header_footer"]                        # unhashable: was a TypeError
    with pytest.raises(SpecError) as e:
        validate_spec(spec)
    assert e.value.code == "spec" and "must be an object with an 'op' name" in e.value.reason
    validate_spec(copy.deepcopy(REMOVE))                                   # the worked example still validates
    for f in SPECS.glob("*.json"):
        validate_spec(json.loads(f.read_text(encoding="utf-8")))


@pytest.mark.parametrize("change, message", MALFORMED[:6])
def test_check_submission_answers_a_malformed_spec_with_errors(folder, change, message):
    spec = {**copy.deepcopy(REMOVE), **change}
    errs = check_submission(spec, _Batch(sorted(folder.glob("*.docx")), None), [spec["request"]])
    assert errs and errs[0].startswith("[spec]") and message in errs[0]


def test_a_malformed_spec_goes_back_to_claude_as_rejected_and_the_conversation_pairs(folder):
    bad = {**copy.deepcopy(REMOVE), "review": [{"sample_files": 2}]}
    fake = FakeClient(submit(bad, "s1"), submit(REMOVE, "s2"), ask("a3", questions=["Anything else?"]))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "ok" and any("'review' must be an object" in e for e in t.errors)
    rejected = fake.requests[1]["messages"][-1]["content"][0]
    assert rejected["tool_use_id"] == "s1" and rejected["is_error"]
    assert rejected["content"].startswith("REJECTED") and "'review' must be an object" in rejected["content"]
    assert c.reply("also keep the logo").status == "questions"             # the next reply still pairs
    assert_well_formed(fake)


def test_a_check_that_raises_is_answered_as_rejected(folder, monkeypatch):
    def boom(spec, batch, sources):
        raise TypeError("unhashable type: 'dict'")
    monkeypatch.setattr(compiler_module, "check_submission", boom)
    fake = FakeClient(submit(REMOVE, "s1"), ask("a2", questions=["Which sections?"]),
                      ask("a3", questions=["And the cover?"]))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "questions" and t.questions == ["Which sections?"]
    rejected = fake.requests[1]["messages"][-1]["content"][0]
    assert rejected["tool_use_id"] == "s1" and rejected["is_error"]
    assert "the spec could not be checked (TypeError: unhashable type: 'dict')" in rejected["content"]
    assert any("could not be checked" in e for e in t.errors)
    assert c.reply("all of them").status == "questions"                    # no ProtocolError: s1 was answered
    sent = fake.requests[2]["messages"][-1]["content"]
    assert sent[0]["tool_use_id"] == "a2" and "all of them" in sent[0]["content"]
    assert_well_formed(fake)


# ---- review-4: questions and choices in the wrong shape -----------------------------------------------------------

def test_ask_user_questions_as_one_string_are_one_question(folder):
    fake = FakeClient(ask(questions="Which sections?"))
    t = conv(folder, fake).start()
    assert t.questions == ["Which sections?"]
    assert any("questions came as one text" in e for e in t.errors)


def test_a_spec_s_questions_as_one_string_are_one_question(folder):
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [], "questions": "Which sections?"}
    fake = FakeClient(submit(spec))
    t = conv(folder, fake).start()
    assert t.status == "questions" and t.questions == ["Which sections?"]


def test_non_list_questions_and_choices_are_dropped_and_said_never_raised(folder):
    fake = FakeClient(ask(questions=3, choices="Use the permit stamp", message="Which stamp?"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "questions" and t.questions == [] and t.choices == [] and t.message == "Which stamp?"
    assert any("questions were dropped" in e for e in t.errors)
    assert any("choices were dropped" in e for e in t.errors)
    fake2 = FakeClient(ask(questions=["Which?", 5], choices=[{"label": "A", "kind": "clarify"}, "B"]))
    t2 = conv(folder, fake2).start()
    assert t2.questions == ["Which?"] and [ch.label for ch in t2.choices] == ["A"]
    assert any("were not text" in e for e in t2.errors) and any("not an object" in e for e in t2.errors)
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [], "questions": 3}
    t3 = conv(folder, FakeClient(submit(spec))).start()
    assert t3.status == "questions" and t3.questions == ["(the model asked an empty question)"]
    assert any("questions were dropped" in e for e in t3.errors)


def test_the_choices_of_an_answered_turn_cannot_be_picked_after_the_next_turn_failed(folder):
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "The permit stamp", "kind": "clarify",
                                                             "wording": PERMIT_STAMP}]),
                      raise_on_enter(RuntimeError("a defect in the next turn")))
    c = conv(folder, fake)
    old = c.start().choices[0]
    with pytest.raises(RuntimeError, match="a defect"):
        c.reply("the other one")
    assert c.turn is None
    with pytest.raises(ValueError, match="not offered any more"):
        c.choice(old.id)
    with pytest.raises(ValueError, match="not offered any more"):
        c.reply(choice_id=old.id)
    assert PERMIT_STAMP not in c.sources


# ---- review-6: invisible characters in a choice's wording ---------------------------------------------------------

INVISIBLE = [0x3164, 0x115F, 0x1160, 0xFFA0, 0x2800, 0xFE0F, 0xFE00, 0x034F, 0x17B4, 0x17B5, 0x180B, 0x180F, 0x061C,
             0x2028, 0x2029, 0xE0100, 0xE01EF, 0xE0041, 0x1BCA0, 0x1D173, 0x00AD, 0x200B, 0x200D, 0x202E, 0x2060,
             0x2066, 0xFEFF, 0xE000, 0x0378, 0x0007, 0x0085]


@pytest.mark.parametrize("cp", INVISIBLE, ids=[f"U+{cp:04X}" for cp in INVISIBLE])
def test_clean_wording_refuses_a_character_the_user_cannot_see(cp):
    with pytest.raises(ValueError, match="invisible or control"):
        clean_wording("ISSUED" + chr(cp) + " FOR PERMIT")


def test_clean_wording_refuses_every_default_ignorable_code_point_and_keeps_visible_text():
    for a, b in INVISIBLE_RANGES:
        for cp in range(a, b + 1):
            with pytest.raises(ValueError):
                clean_wording("A" + chr(cp) + "B")
    for ok in (PERMIT_STAMP, "ISSUED\tFOR PERMIT", "Révision 2", "été", "NFPA 13 (2025)", "ISSUED FOR",
               "08 71 00 – DOOR HARDWARE", "§ 3.2 ½\""):
        assert clean_wording(ok) == ok


def test_a_choice_with_a_hidden_character_is_dropped_and_never_approved(folder):
    hidden = "ISSUEDㅤ FOR PERMIT"
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "Use it", "kind": "alternative",
                                                            "wording": hidden},
                                                           {"label": "Other", "kind": "clarify"}]))
    c = conv(folder, fake)
    t = c.start()
    assert [ch.label for ch in t.choices] == ["Other"]
    assert any("Use it" in e and "0x3164" in e for e in t.errors)
    assert all(hidden not in s for s in c.sources)


# ---- review-1: the spec the app gets carries the user's request ---------------------------------------------------

CLAUDES = "Remove the headers and footers and replace NFPA 13 (2025) with NFPA 13 (2028) throughout"


def test_an_accepted_spec_carries_the_user_s_request_not_claude_s(folder):
    fake = FakeClient(submit(dict(REMOVE, request=CLAUDES), "s1"))
    c = conv(folder, fake)
    t = c.start()
    assert t.status == "ok" and t.spec["request"] == c.request == "remove all headers and footers"
    assert {k: v for k, v in t.spec.items() if k != "request"} == {k: v for k, v in REMOVE.items() if k != "request"}
    sent = [b for b in c.messages[1]["content"] if getattr(b, "type", None) == "tool_use"
            or (isinstance(b, dict) and b.get("type") == "tool_use")]
    claude_input = sent[0].input if not isinstance(sent[0], dict) else sent[0]["input"]
    assert claude_input["spec"]["request"] == CLAUDES                     # the history keeps the call as made
    # the advisor's sources (placed_words + the request + answers) no longer hold Claude's words
    respec = {"request": "r", "summary": "s", "mode": "content", "ops": [{
        "op": "replace_text", "select": {"scope": ["document"]},
        "params": {"find": "NFPA 13 (2025)", "replace": "NFPA 13 (2028)", "tracked": True, "author": "x"}}]}
    assert untraceable(respec, [*placed_words(t.spec), str(t.spec.get("request", ""))]) == ["NFPA 13 (2028)"]
    assert c.compile_result(t).spec["request"] == c.request
    assert json.loads(json.dumps(c.to_dict()))["turn"]["spec"]["request"] == c.request


def test_compile_mode_and_unsupported_specs_carry_the_user_s_request(folder):
    res = compile_request("strip every header and footer", sorted(folder.glob("*.docx")),
                          client=FakeClient(submit(dict(REMOVE, request=CLAUDES))))
    assert res.status == "ok" and res.spec["request"] == "strip every header and footer"
    unsupported = {"request": CLAUDES, "summary": "s", "unsupported": "no op", "proposal": "p"}
    c = conv(folder, FakeClient(submit(unsupported, custom_transform="possible")))
    t = c.start()
    assert t.status == "unsupported" and t.spec["request"] == c.request and c.unsupported["request"] == c.request
    assert CLAUDES not in c.brief()


# ---- review-24: conversation.json holds the approved words --------------------------------------------------------

def test_conversation_json_holds_the_approved_words_and_the_answers(folder, tmp_path):
    (folder / "PROJECT.json").write_text(json.dumps({"stamp": "ISSUED FOR BID"}), encoding="utf-8")
    fake = FakeClient(ask(message="Which stamp?", choices=[{"label": "Permit", "kind": "clarify",
                                                            "wording": PERMIT_STAMP}]),
                      ask("a2", questions=["Anything else?"]))
    c = conv(folder, fake, answers=["sections 1-3 only"])
    t = c.start()
    c.reply("and the cover too", choice_id=t.choices[0].id)
    rec = json.loads(c.save(tmp_path / "conversation.json").read_text(encoding="utf-8"))
    assert rec["approved_words"] == c.sources
    assert rec["approved_words"][:2] == ["remove all headers and footers", "sections 1-3 only"]
    assert "ISSUED FOR BID" in rec["approved_words"][2] and "PROJECT.json" not in rec["approved_words"][2]
    assert rec["approved_words"][3:] == [PERMIT_STAMP, "and the cover too"]
    assert rec["answers"] == ["sections 1-3 only"]


# ---- codex-3: files that share a name in different folders --------------------------------------------------------

def _two_folders(tmp_path) -> list:
    for sub, title in (("a", "ALPHA"), ("b", "BRAVO")):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "Spec.docx").write_bytes(build("21 00 00", title))
    (tmp_path / "a" / "Other.docx").write_bytes(build("21 00 01", "OTHER"))
    return [tmp_path / "a" / "Spec.docx", tmp_path / "b" / "Spec.docx", tmp_path / "a" / "Other.docx"]


def _rewrite(data: bytes, member: str, fn) -> bytes:
    from lxml import etree
    src, out = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for info in src.infolist():
            b = src.read(info.filename)
            if info.filename == member:
                root = etree.fromstring(b)
                fn(root)
                b = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            z.writestr(info, b)
    return out.getvalue()


def test_file_labels_are_names_unless_two_files_share_one():
    assert file_labels([Path("p/A.docx"), Path("p/B.docx")]) == {str(Path("p/A.docx")): "A.docx",
                                                                  str(Path("p/B.docx")): "B.docx"}
    got = file_labels([Path("a/x/Spec.docx"), Path("b/x/Spec.docx"), Path("c/Spec.docx"), Path("c/Other.docx")])
    assert got == {str(Path("a/x/Spec.docx")): "a/x/Spec.docx", str(Path("b/x/Spec.docx")): "b/x/Spec.docx",
                   str(Path("c/Spec.docx")): "c/Spec.docx", str(Path("c/Other.docx")): "Other.docx"}
    assert len(set(got.values())) == len(got)
    assert file_labels([Path("p/A.docx"), Path("p/A.docx")]) == {str(Path("p/A.docx")): "A.docx"}   # one file


def test_every_tool_reads_the_file_its_label_names(tmp_path, monkeypatch):
    files = _two_folders(tmp_path)
    b = _Batch(files, None)
    assert list(b.files) == ["a/Spec.docx", "b/Spec.docx", "Other.docx"]
    assert [b.files[k] for k in b.files] == files
    for label, title in (("a/Spec.docx", "ALPHA"), ("b/Spec.docx", "BRAVO")):
        assert title in json.dumps(b.sample_paragraphs(label, n=3))
        assert title in json.dumps(b.show_part_text(label, "word/document.xml"))
        assert title in json.dumps(b.find_paragraphs(label, title))
        assert title in b.show_xml(label, start=0, count=3)["xml"]
        assert isinstance(b.list_styles(label), list)
    assert "BRAVO" not in json.dumps(b.sample_paragraphs("a/Spec.docx", n=3))
    with pytest.raises(KeyError, match=r"a/Spec\.docx"):
        b.sample_paragraphs("Spec.docx")                                    # the bare name is ambiguous: refused
    seen = []

    def fake_pngs(path, pages, work_dir, renderer="auto"):
        seen.append(Path(path))
        return []
    monkeypatch.setattr(vision, "page_pngs", fake_pngs)
    b.work_dir = tmp_path / "chat"
    with pytest.raises(ValueError):
        b.view_pages("b/Spec.docx")
    assert seen == [tmp_path / "b" / "Spec.docx"]


def test_the_summary_and_profiles_claude_sees_use_the_labels(tmp_path):
    files = _two_folders(tmp_path)
    b = _Batch(files, None)
    [pat] = b.summary()["patterns"]
    assert pat["files"] == ["a/Spec.docx", "b/Spec.docx", "Other.docx"] and pat["profile"]["file"] == "a/Spec.docx"
    assert b.inspect_profile(pat["pattern_id"])["files"] == pat["files"]
    msg = build_messages("remove all headers and footers", b, [], {})[0]["content"]
    assert '"a/Spec.docx"' in msg and '"b/Spec.docx"' in msg
    assert [str(p) for p in b.pattern_paths(b.prof["patterns"][0])] == [str(f) for f in files]


def test_a_single_folder_batch_reads_as_before(folder):
    files = sorted(folder.glob("*.docx"))
    b = _Batch(files, None)
    assert list(b.files) == [f.name for f in files]
    assert b.summary()["patterns"][0]["files"] == [f.name for f in files]
    assert b.summary()["patterns"][0]["profile"]["file"] == files[0].name


def test_unprofiled_and_unreadable_files_are_named_by_label(tmp_path):
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def bad_margin(root):
        for m in root.iter(W + "pgMar"):
            m.set(W + "left", "abc")
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
    (tmp_path / "a" / "Spec.docx").write_bytes(_rewrite(build("21 00 15", "JULIET"), "word/document.xml", bad_margin))
    (tmp_path / "b" / "Spec.docx").write_bytes(build("21 00 16", "KILO"))
    (tmp_path / "c" / "Spec.docx").write_bytes(b"not a zip")
    b = _Batch([tmp_path / "a" / "Spec.docx", tmp_path / "b" / "Spec.docx", tmp_path / "c" / "Spec.docx"], None)
    [pat] = b.prof["patterns"]
    assert pat["files"] == ["b/Spec.docx", "a/Spec.docx"] and pat["profile"]["file"] == "b/Spec.docx"
    assert pat["unprofiled"][0].startswith("a/Spec.docx: ")
    assert b.prof["unreadable"][0]["file"] == "c/Spec.docx"
    alone = _Batch([tmp_path / "a" / "Spec.docx", tmp_path / "c" / "Spec.docx"], None)
    [pat] = alone.prof["patterns"]
    assert pat["profile"]["error"] and pat["profile"]["files"][0].startswith("a/Spec.docx: ")
    assert pat["unprofiled"][0].startswith("a/Spec.docx: ")


def test_the_gate_s_errors_name_files_by_label(tmp_path):
    files = _two_folders(tmp_path)
    spec = {"request": "r", "summary": "s", "mode": "content", "ops": [{
        "op": "replace_text", "select": {"scope": ["document"]},
        "params": {"find": "NFPA 99", "replace": "NFPA 13 (2025)", "tracked": True, "author": "x"}}]}
    errs = check_submission(spec, _Batch(files, None), ["NFPA 13 (2025)"])
    assert errs and errs[1].startswith("a/Spec.docx: [") and "e.g. b/Spec.docx: [" in errs[1]


def test_claude_addresses_the_second_same_named_file_in_a_conversation(tmp_path):
    files = _two_folders(tmp_path)
    fake = FakeClient(resp(tool("sample_paragraphs", {"file": "b/Spec.docx", "n": 3}, "t1")),
                      resp(text("BRAVO is the second file."), stop="end_turn"))
    c = Conversation("remove all headers and footers", files, client=fake)
    assert c.start().status == "questions"
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and "BRAVO" in result["content"] and "ALPHA" not in result["content"]
    assert '"b/Spec.docx"' in fake.requests[0]["messages"][0]["content"]


# =====================================================================================================================
# Claude's visual check and the advisor: review-5, merge-verify-2, merge-verify-3, review-26, review-28
# =====================================================================================================================

def _pdf(path: Path, pages=1, size=(612, 792), words="page") -> Path:
    from spec_surgeon.verify.geometry import fitz
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page(width=size[0], height=size[1])
        pg.insert_text((72, 72), f"{words} {i + 1}", fontsize=14)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    doc.close()
    return path


def _sample(tmp_path, name="21 00 00 – SPEC 0.docx") -> SampleView:
    return SampleView(source=tmp_path / name, output=tmp_path / "o.docx", passed=True,
                      source_pdf=_pdf(tmp_path / "pdf" / "before" / "x.pdf", words="before"),
                      output_pdf=_pdf(tmp_path / "pdf" / "after" / "x.pdf", words="after"), look_pages=[1])


def _odd(content=None, usage=None, stop="tool_use"):
    """A response with any content/usage (the scripted ``resp`` always builds well-formed ones)."""
    return SimpleNamespace(content=content, stop_reason=stop, stop_details=None, model="claude-opus-5-5", usage=usage)


def _review(**inp):
    return [tool("submit_review", inp)]


GOOD_USAGE = SimpleNamespace(input_tokens=10, output_tokens=2, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                             iterations=None)
BAD_USAGE = SimpleNamespace(input_tokens="lots", output_tokens=2, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0, iterations=None)

# Each answer is one Claude could give a non-strict tool (or a response the SDK could hand back oddly); none may raise.
UNREADABLE = {
    "findings is true": resp(tool("submit_review", {"summary": "looked", "findings": True})),        # review-5
    "findings is a number": resp(tool("submit_review", {"summary": "x", "findings": 5})),            # merge-verify-2
    "findings is a string": resp(tool("submit_review", {"summary": "x", "findings": "none"})),
    "findings is an object": resp(tool("submit_review", {"summary": "x", "findings": {"severity": "block",
                                                                                       "text": "squashed logo"}})),
    "input is a list of pairs": resp(tool("submit_review", [["summary", "x"], ["findings", []]])),
    "input is a string": resp(tool("submit_review", "summary: fine")),
    "input is None": resp(tool("submit_review", None)),
    "usage cannot be read": _odd(_review(summary="x", findings=[]), BAD_USAGE),
    "content is not a list": _odd(5, GOOD_USAGE),
    "a block without a name": _odd([{"type": "tool_use", "id": "t1", "input": {"summary": "x", "findings": []}}],
                                   GOOD_USAGE),
}


@pytest.mark.parametrize("name", list(UNREADABLE))
def test_an_answer_that_cannot_be_read_is_a_skipped_review_never_an_exception(tmp_path, name):
    fake = FakeClient(copy.deepcopy(UNREADABLE[name]))
    rv = review_samples("REQUEST: remove the headers", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert len(fake.requests) == 1                                    # the call was made; reading its answer failed
    assert rv.skipped.startswith("Claude's answer could not be read (") and rv.findings == [] and rv.summary == ""
    assert rv.blocking() == [] and "skipped: Claude's answer could not be read" in rv.to_text()
    last = rv.transcript[-1]
    assert last["turn"] == 1 and last["read_error"] and "Traceback" in last["traceback"]
    assert [e.get("role") for e in rv.transcript[:2]] == ["user", "assistant"]      # the answer itself is kept
    saved = json.loads(vision.save_review(rv, tmp_path).read_text(encoding="utf-8"))
    assert saved["skipped"] == rv.skipped and saved["transcript"][-1]["traceback"] == last["traceback"]


def test_a_review_read_part_way_keeps_nothing(tmp_path, monkeypatch):
    """A reading failure after some findings were read must not leave them behind a skip (a skipped review shows no
    findings, so a hidden `block` would stop Run with nothing on the screen)."""
    calls = []
    real = vision._finding

    def flaky(f, sent):
        calls.append(f)
        if len(calls) == 2:
            raise RuntimeError("a defect while reading the second finding")
        return real(f, sent)
    monkeypatch.setattr(vision, "_finding", flaky)
    s = _sample(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "Two problems.", "findings": [
        {"severity": "block", "file": s.source.name, "page": 1, "text": "Logo squashed."},
        {"severity": "warn", "text": "Footer moved."}]})))
    rv = review_samples("r", [s], tmp_path / "review", lambda: fake)
    assert rv.findings == [] and rv.summary == "" and rv.blocking() == []
    assert "RuntimeError: a defect while reading the second finding" in rv.skipped
    assert "flaky" in rv.transcript[-1]["traceback"]


def test_finding_items_of_any_type_are_read_or_dropped(tmp_path):
    s = _sample(tmp_path)
    fake = FakeClient(resp(tool("submit_review", {"summary": "Looked.", "findings": [
        None, 3, "a string", ["block", "x"], True,
        {"severity": "block", "file": s.source.name, "page": True, "text": "Logo squashed."},     # a bool is no page
        {"severity": ["block"], "file": {"a": 1}, "page": "1", "text": {"what": "odd"}},
        {"severity": "info", "file": s.source.name, "page": 1.0, "text": "  fine  "},
        {"severity": "warn", "text": None}]})))
    rv = review_samples("r", [s], tmp_path / "review", lambda: fake)
    assert not rv.skipped and rv.summary == "Looked."
    assert rv.findings == [
        {"severity": "block", "file": s.source.name, "page": None, "text": "Logo squashed."},
        {"severity": "warn", "file": "", "page": None, "text": "{'what': 'odd'}"},
        {"severity": "info", "file": s.source.name, "page": None, "text": "fine"}]
    assert "p.True" not in rv.to_text()


def test_findings_missing_is_a_review_without_findings(tmp_path):
    fake = FakeClient(resp(tool("submit_review", {"summary": "Nothing to see."})))
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert not rv.skipped and rv.summary == "Nothing to see." and rv.findings == []


def test_a_client_factory_error_is_classified_like_a_call_error(tmp_path):
    def raising(exc):
        def factory():
            raise exc
        return factory

    defect = review_samples("r", [_sample(tmp_path)], tmp_path / "r1", raising(KeyError("defect")))
    assert defect.skipped == "Claude could not be set up: KeyError: 'defect'"
    assert "not available" not in defect.skipped                      # a defect is never "Claude is not available"
    entry = defect.transcript[-1]
    assert entry["turn"] == 0 and entry["error"] == "KeyError: 'defect'" and "Traceback" in entry["traceback"]
    assert "raise exc" in entry["traceback"]

    plain = review_samples("r", [_sample(tmp_path)], tmp_path / "r2", raising(RuntimeError("no key")))
    assert plain.skipped.startswith("Claude could not be set up: RuntimeError") and "traceback" in plain.transcript[-1]

    missing = review_samples("r", [_sample(tmp_path)], tmp_path / "r3",
                             raising(llm.ClaudeUnavailable("install the Claude layer")))
    assert missing.skipped == "Claude is not available: install the Claude layer"
    assert missing.transcript == [{"turn": 0, "error": "ClaudeUnavailable: install the Claude layer", "seconds": 0.0}]

    api = review_samples("r", [_sample(tmp_path)], tmp_path / "r4", raising(api_error("no key", 401, "req_9")))
    assert api.skipped == "Claude could not be set up: APIStatusError: no key · HTTP 401 · request id req_9"
    assert "traceback" not in api.transcript[-1] and api.transcript[-1]["error"].endswith("request id req_9")
    for rv in (defect, plain, missing, api):
        assert rv.findings == [] and rv.usage.calls == 0 and rv.pages_sent == 0


def test_pages_that_cannot_be_sent_are_a_skip_with_their_traceback(tmp_path, monkeypatch):
    def unreadable(png):
        raise PermissionError(f"{Path(png).name} is locked")
    monkeypatch.setattr(vision, "image_block", unreadable)
    fake = FakeClient()
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake)
    assert rv.skipped == "the pages could not be prepared (PermissionError: p1.png is locked)" and fake.requests == []
    assert "Traceback" in rv.transcript[-1]["traceback"] and rv.transcript[-1]["error"].startswith("PermissionError")


def test_a_log_that_raises_never_stops_the_review(tmp_path):
    def broken(_m):
        raise RuntimeError("the window's log is gone")
    fake = FakeClient(resp(tool("submit_review", {"summary": "Fine.", "findings": []})))
    rv = review_samples("r", [_sample(tmp_path)], tmp_path / "review", lambda: fake, log=broken)
    assert not rv.skipped and rv.summary == "Fine."


def test_the_view_pages_budget_figure_is_derived_from_est_tokens(tmp_path):
    """The ample configurable allowance still reports actual image-token costs."""
    letter = vision.rasterize_pages(_pdf(tmp_path / "letter.pdf"), [1], tmp_path / "l")[0]
    tabloid = vision.rasterize_pages(_pdf(tmp_path / "tabloid.pdf", size=(792, 1224)), [1], tmp_path / "t")[0]
    a4 = vision.rasterize_pages(_pdf(tmp_path / "a4.pdf", size=(595.28, 841.89)), [1], tmp_path / "a")[0]
    assert vision.page_dpi(612, 792) == vision.TARGET_DPI and vision.png_size(letter) == (1275, 1650)
    assert vision.page_tokens() == vision.est_tokens(letter) == 2714
    assert vision.page_tokens(792, 1224) == vision.est_tokens(tabloid)
    assert vision.page_tokens(595.28, 841.89) == vision.est_tokens(a4)
    budget, per_letter = vision.VIEW_BUDGET_TOKENS, vision.page_tokens()
    one_at_a_time = budget // per_letter
    in_threes = 3 * (budget // (3 * per_letter))
    tabloids = budget // vision.page_tokens(792, 1224)
    assert (one_at_a_time, in_threes, tabloids) == (368, 366, 301)
    src = inspect.getsource(vision)
    start = src.index("TOKENS_PER_IMAGE_CAP = ")
    comment = " ".join(src[start:src.index("SEVERITIES = ", start)].replace("#", " ").split())
    assert "VIEW_BUDGET_TOKENS = DEFAULT_MAX_IMAGE_TOKENS" in comment
    assert "Configurable investigation allowance" in comment
    assert f"{per_letter:,}" in comment and f"{one_at_a_time} one at a time" in comment
    assert f"{in_threes} in calls of three" in comment and f"{tabloids} pages of 11×17" in comment


# ---- review-28: the advisor names the real sources of approved words -------------------------------------------------

@pytest.fixture
def batch_out(tmp_path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    (d / "21 05 00 – A.docx").write_bytes(build("21 05 00", "A"))
    (d / "21 13 13 – RICH.docx").write_bytes(build_rich())
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    spec["ops"][0]["expect"] = {"members_removed": 3}
    out = tmp_path / "out"
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=spec)
    assert [f.status for f in b.files] == ["PASS", "EXCEPTION"]
    return out


def test_the_advisor_tells_claude_which_words_it_may_place(batch_out):
    rec = json.loads(next((batch_out / "exceptions").glob("*.exception.json")).read_text(encoding="utf-8"))
    fake = FakeClient(resp(tool("submit_recommendation", {"text": "Expect 9 here."})))
    advisor.advise(rec, client=fake)
    system = fake.requests[0]["system"]
    assert system.startswith(advisor.ADVISOR_SYSTEM)
    for words in (advisor.ADVISOR_SYSTEM, advisor.__doc__):
        flat = " ".join(words.split())
        assert "original request/spec" not in flat and "the original request and spec" not in flat
        assert "the words the original spec places" in flat and "the user's answers" in flat
        assert "never the original spec's summary, assumptions or proposal" in flat


# =====================================================================================================================
# The window's session (gui.Session): review-2, review-16, review-3/27, merge-verify-4, review-8, review-18
# =====================================================================================================================

def quiet(m):
    return None


def touch(f: Path) -> None:
    """The user opens a spec in Word and saves it: the file's mtime moves."""
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def saved_operation(folder: Path, tmp_path: Path, name: str = "Header distance") -> None:
    g = generate_op("put the header 0.625 in from the top", "set_header_distance", sorted(folder.glob("*.docx"))[:1],
                    tmp_path / "cg", client=FakeClient(WRITER), brief="REQUEST: header")
    assert g.passed
    library.save_operation(name, g)


def failing_trials(monkeypatch):
    import spec_surgeon.codegen as cg
    real = cg.trial

    def failing(*a, **k):
        t = real(*a, **k)
        t.passed, t.problems = False, ["V10 FAIL: not on these files"]
        return t
    monkeypatch.setattr(cg, "trial", failing)


# ---- review-2: compare like with like -----------------------------------------------------------------------------

def test_a_template_spec_from_the_batch_folder_keeps_the_conversation(folder, tmp_path):
    tpl = folder / "08 71 00 - DOOR HARDWARE (ACME).docx"
    shutil.copy(ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx", tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    request = "make the header and footer match the template spec"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec}, "s1")),
                      resp(tool("ask_user", {"questions": ["Keep the logo?"]}, "a2")))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    assert s.compile(request, quiet) == "Compiled."
    assert tpl in s.conversation.files and tpl not in s.files()      # the batch it started on held the template
    assert s.conversation_matches(request)                           # nothing changed: the same conversation
    assert "needs your answer" in s.reply("also keep the logo", log=quiet)
    assert s.spec is None and s.conversation_matches(request)        # without a spec the template is a file again
    (folder / "21 99 00 – NEW.docx").write_bytes(build("21 99 00", "NEW"))
    assert not s.conversation_matches(request)                       # a real change still ends the match
    with pytest.raises(ValueError, match="Start over"):
        s.reply("and the footer")


def test_a_removed_file_still_ends_the_match_when_a_spec_names_a_template(folder, tmp_path):
    tpl = folder / "08 71 00 - DOOR HARDWARE (ACME).docx"
    shutil.copy(ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx", tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": spec}))))
    s.compile("match the template spec", quiet)
    sorted(folder.glob("21 00*.docx"))[0].unlink()
    assert not s.conversation_matches("match the template spec")


# ---- review-16: a vanished folder ---------------------------------------------------------------------------------

def test_a_vanished_folder_is_no_match_never_an_exception(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove some headers", quiet)
    assert s.conversation_matches("remove some headers")
    shutil.move(str(folder), str(tmp_path / "moved"))                # renamed, or the network share dropped
    assert s.conversation_matches("remove some headers") is False
    with pytest.raises(ValueError, match="Start over"):
        s.reply("all of them")
    assert s.conversation is not None                                # nothing was thrown away


def test_the_windows_preview_with_a_vanished_folder_reports_on_the_page(qt, folder, tmp_path):
    from spec_surgeon.gui import build_window
    from spec_surgeon.ui.app import RecordingPrompts
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("ask_user", {"questions": ["Which?"]}))))
    w = build_window(s, prompts=RecordingPrompts())
    qt[1].append(w)
    ui = w._spec_surgeon
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.conversation is not None
    shutil.move(str(folder), str(tmp_path / "moved"))
    w.do_preview()                                                   # on the GUI thread: must not raise
    ui["wait"]()
    assert not w.busy


# ---- review-3 / review-27 / merge-verify-4: what Claude is told when a custom transform does not come out -----------

def _offered(folder, tmp_path, *writer):
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(OFFER_TURN, *writer))
    s.compile("put the header 0.625 in from the top", quiet)
    assert [c.kind for c in s.turn.choices] == ["custom_transform"]
    return s


def _saved(s):
    return json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))


def test_no_passing_attempt_is_told_to_claude_once_and_saved(folder, tmp_path):
    bad = [resp(tool("submit_op", {"source": "x = 1"}, f"o{i}")) for i in range(3)]
    s = _offered(folder, tmp_path, *bad,
        resp(tool("need_user", {"question": "Which outcome should the replacement method preserve?"})))
    s.reply(choice_id=s.turn.choices[0].id, log=quiet)
    expected = ("No custom transform passed verification: no passing op after 3 attempt(s): "
                "investigation stalled; equivalent rejected transforms repeated without new evidence")
    assert s.conversation.notes[-1] == expected
    assert sum("passed verification" in n for n in s.conversation.notes) == 1
    assert any(e["role"] == "app" and e["text"] == expected for e in _saved(s)["entries"])
    assert s.generated is None
    assert s.conversation.recovery_active and s.turn.status == "questions"
    assert expected in str(s.client.requests[-1]["messages"])
    s.client.responses.append(resp(tool("need_user", {"question": "Which outcome?"}, "a9")))
    s.reply("so did it work?")
    assert "so did it work?" in str(s.client.requests[-1]["messages"])


def test_an_api_error_while_writing_is_a_recorded_failure_told_to_claude(folder, tmp_path):
    s = _offered(folder, tmp_path, raise_on_enter(api_error("Overloaded", 529, "req_7")))
    with pytest.raises(TransformFailed, match="the call to the model failed"):
        generate_reviewed_custom(s, log=quiet)
    note = s.conversation.notes[-1]
    assert note.startswith(TransformFailed.PREFIX) and "No AI-written" not in note and "529" in note
    assert _saved(s)["entries"][-1]["text"] == note


def test_a_defect_while_writing_is_never_told_to_claude_as_a_verification_result(folder, tmp_path):
    s = _offered(folder, tmp_path, raise_on_enter(RuntimeError("a defect in the writer")))
    with pytest.raises(RuntimeError, match="a defect in the writer") as e:
        generate_reviewed_custom(s, log=quiet)
    assert not isinstance(e.value, TransformFailed)
    assert not any("passed verification" in n or "defect" in n for n in s.conversation.notes)
    entries = _saved(s)["entries"]                                   # what was said is kept for the diagnosis
    assert [e for e in entries if e["role"] == "user"][-1]["text"].startswith("Chose: ")
    assert entries[-1]["role"] == "user"  # the failed method's choice is preserved; no second review ran
    assert "internal error (RuntimeError) before any result" in s.conversation.notes[-1]


# ---- review-8: Preview tries a stale custom transform again ---------------------------------------------------------

def test_preview_tries_a_stale_custom_transform_again_and_run_then_runs(folder, tmp_path):
    s = _offered(folder, tmp_path, WRITER)
    assert "passed verification" in generate_reviewed_custom(s, log=quiet)
    s.make_preview(render=False, log=quiet)
    first = s.out_dir
    touch(sorted(folder.glob("*.docx"))[0])
    with pytest.raises(ValueError, match="press Preview again"):
        s.run(False, False, quiet)
    lines = []
    text = s.make_preview(render=False, log=lines.append)
    assert "transform verified" in text and s.pending_custom_transform is None
    assert s.outcome_review is None
    retrials = [s.out_dir / "retrial"]
    assert s.out_dir != first and retrials[0].is_dir() and (s.out_dir / "preview").is_dir()
    assert all(t.output.is_relative_to(retrials[0]) for t in s.generated.trials)
    assert s.chat_dir == s.out_dir and (s.out_dir / "conversation.json").is_file()   # the records follow the preview
    assert (first / "preview").is_dir() and (first / "codegen" / "history.json").is_file()   # the old one is kept
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_a_current_custom_transform_is_not_tried_again(folder, tmp_path, monkeypatch):
    s = _offered(folder, tmp_path, WRITER)
    generate_reviewed_custom(s, log=quiet)
    token, gp, out = s.generated_token, s.generated_preview, s.out_dir
    import spec_surgeon.codegen as cg

    def no_trial(*a, **k):
        raise AssertionError("a current transform must not be tried again")
    monkeypatch.setattr(cg, "trial", no_trial)
    assert "AI-written transform verified" in s.make_preview(render=False, log=quiet)
    assert (s.generated_token, s.generated_preview, s.out_dir) == (token, gp, out)


def test_a_saved_operation_whose_files_changed_is_tried_again_or_refused(folder, tmp_path, monkeypatch):
    saved_operation(folder, tmp_path)
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.use_operation("header distance", quiet)
    touch(sorted(folder.glob("*.docx"))[1])
    with pytest.raises(ValueError, match="press Preview again"):
        s.run(False, False, quiet)
    with monkeypatch.context() as m:                                 # it no longer passes on the files as they are
        failing_trials(m)
        token, gp, out = s.generated_token, s.generated_preview, s.out_dir
        with pytest.raises(RuntimeError, match="does not pass on the files as they are now"):
            s.make_preview(render=False, log=quiet)
        assert s.generated is not None and (s.generated_token, s.generated_preview, s.out_dir) == (token, gp, out)
        with pytest.raises(ValueError, match="press Preview again"):
            s.run(False, False, quiet)                               # still refused: nothing was approved
    assert "AI-written transform verified" in s.make_preview(render=False, log=quiet)  # a current token
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_trying_again_with_rendering_runs_the_visual_check_again(folder, tmp_path, monkeypatch):
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)  # no renderer in this test
    s = _offered(folder, tmp_path, WRITER)
    generate_reviewed_custom(s, log=quiet)
    checks = []
    monkeypatch.setattr(Session, "_visual_check", lambda self, p, intent, where, log: checks.append((p, intent)))
    touch(sorted(folder.glob("*.docx"))[0])
    s.make_preview(render=True, log=quiet)
    assert s.pending_custom_transform is None
    assert len(checks) == 1 and checks[0][0] is s.generated_preview
    assert checks[0][1].startswith("A ONE-OFF CUSTOM TRANSFORM (set_header_distance) for: put the header 0.625 in from the top")


def test_a_preview_that_stops_leaves_the_transform_unrunnable_until_previewed(folder, tmp_path, monkeypatch):
    saved_operation(folder, tmp_path)
    s = Session(folder=folder, out_root=tmp_path / "out")
    import spec_surgeon.preview as pv
    real = pv.preview_generated

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pv, "preview_generated", broken)
    with pytest.raises(OSError):
        s.use_operation("header distance", quiet)
    assert s.generated_token == "" and s.generated_preview is None
    with pytest.raises(ValueError, match="press Preview again"):
        s.run(False, False, quiet)                                   # never runs without its preview
    monkeypatch.setattr(pv, "preview_generated", real)
    assert "AI-written transform verified" in s.make_preview(render=False, log=quiet)
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


# ---- review-18: a saved operation replaces nothing before it passes --------------------------------------------------

def test_a_saved_operation_that_fails_leaves_the_conversation_and_its_preview(folder, tmp_path, monkeypatch):
    saved_operation(folder, tmp_path)
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    assert s.compile("remove all headers and footers", quiet) == "Compiled."
    s.make_preview(render=False, log=quiet)
    conversation, preview, out = s.conversation, s.preview, s.out_dir
    failing_trials(monkeypatch)
    with pytest.raises(RuntimeError, match="does not pass on these files"):
        s.use_operation("header distance", quiet)
    assert s.conversation is conversation and s.spec == REMOVE and s.preview is preview and s.out_dir == out
    assert s.generated is None
    assert "PASS 2" in s.run(False, False, quiet)                    # the previewed spec still runs


def test_a_saved_operation_that_passes_goes_into_a_new_folder(folder, tmp_path):
    saved_operation(folder, tmp_path)
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    s.compile("remove all headers and footers", quiet)
    s.make_preview(render=False, log=quiet)
    out = s.out_dir
    assert "passed verification" in s.use_operation("header distance", quiet)
    assert s.conversation is None and s.spec is None and s.generated is not None
    assert s.out_dir != out and (s.out_dir / "library-trial").is_dir() and (s.out_dir / "preview").is_dir()
    assert (out / "preview" / "spec.json").is_file()                 # the spec's preview folder is untouched
    assert not (s.out_dir / "conversation.json").exists()            # the ended conversation does not follow
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


# =====================================================================================================================
# The command line, the library and the preview: review-9, -11/-20/codex-1, -12, -13, -25, -21, -10
# =====================================================================================================================

STAMP = "ISSUED FOR CONSTRUCTION  2026-02-15"


def touch_edit(f: Path) -> None:
    """Edit a file the way saving it would: new content, new modification time."""
    st = f.stat()
    f.write_bytes(f.read_bytes() + b"\0")
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def script_input(monkeypatch, *items):
    """builtins.input answers ``items`` in order; an exception class or instance is raised; a callable is called with
    the prompt and its result returned. Every prompt is recorded."""
    seq, prompts = list(items), []

    def fake(prompt=""):
        prompts.append(prompt)
        if not seq:
            raise AssertionError(f"an unscripted prompt: {prompt!r}")
        item = seq.pop(0)
        if isinstance(item, BaseException) or (isinstance(item, type) and issubclass(item, BaseException)):
            raise item
        return item(prompt) if callable(item) else item
    monkeypatch.setattr("builtins.input", fake)
    return prompts


def fake_review(monkeypatch, block=False, on_call=None):
    """vision.review_samples replaced: records each intent, optionally does something while it 'looks' (an edit made
    while the visual check runs), and answers one blocking finding when ``block``."""
    intents = []

    def review(intent, samples, work_dir, client_factory, *a, **k):
        intents.append(intent)
        if on_call is not None:
            on_call()
        rv = vision.VisualReview(summary="looked at the pages")
        if block:
            rv.findings = [{"severity": "block", "file": "x.docx", "page": 1, "text": "the cover lost its title"}]
        return rv
    monkeypatch.setattr(vision, "review_samples", review)
    return intents


def passing_op(folder, tmp_path):
    return generate_op("put the header 0.625 in from the top", "set_header_distance", docx(folder)[:1],
                       tmp_path / "cg", client=FakeClient(WRITER))


# ---- review-9: what was approved is what was previewed ----

def test_do_refuses_a_file_edited_while_the_question_waits(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    f = docx(folder)[0]
    before = f.read_bytes()
    script_input(monkeypatch, lambda prompt: (touch_edit(f), "y")[1])          # saved in Word, then "y"
    out = tmp_path / "o"
    approval = outcome_cli_flags(tmp_path / "review.json", "remove all headers and footers", docx(folder))
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out), "--no-render", *approval]) == 2
    err = capsys.readouterr().err
    assert "changed since the preview" in err and f"{f.name}: modified since the preview" in err
    assert not (out / "run").exists()
    assert f.read_bytes() == before + b"\0"                                # the edit is the user's; nothing ran
    # the same with nothing edited runs: the refusal above is the edit's
    script_input(monkeypatch, "y")
    approval = outcome_cli_flags(tmp_path / "review-current.json", "remove all headers and footers", docx(folder))
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o2"),
                     "--no-render", *approval]) == 0
    assert "PASS 2" in capsys.readouterr().out and (tmp_path / "o2" / "run").is_dir()


def test_do_yes_refuses_a_template_edited_during_the_visual_check(folder, tmp_path, monkeypatch, capsys):
    """Rule 12 on the `do` path: a template saved after the preview was made (here while Claude's visual check looks at
    the pages) refuses the run, --yes or not, and names the template."""
    tpl = tmp_path / "tpl" / "00 00 00 TEMPLATE.docx"
    tpl.parent.mkdir()
    tpl.write_bytes(build_template())
    spec = json.loads((SPECS / "apply-header-footer-template.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", {"spec": spec}))))
    intents = fake_review(monkeypatch, on_call=lambda: touch_edit(tpl))
    out = tmp_path / "o"
    assert cli_main(["do", "make the header/footer look like this template", str(folder), "--template", str(tpl),
                     "--answer", STAMP, "--out", str(out), "--renderer", "none", "--yes"]) == 2
    assert len(intents) == 1
    err = capsys.readouterr().err
    assert "template changed since the preview" in err and tpl.name in err
    assert not (out / "run").exists()


def test_do_codegen_refuses_a_file_edited_after_the_transform_was_previewed(folder, tmp_path, monkeypatch, capsys):
    f = docx(folder)[1]
    fake = FakeClient(resp(tool("submit_editspec", OFFER)), WRITER)
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    fake_review(monkeypatch, on_call=lambda: touch_edit(f))
    out = tmp_path / "o"
    approval = outcome_cli_flags(tmp_path / "review.json", "header 0.625in from the top", docx(folder), header=True)
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out), "--renderer", "none",
                     "--allow-codegen", "--yes", *approval]) == 2
    printed = capsys.readouterr()
    assert "passed on the samples" in printed.out
    assert "changed since the custom transform was tested" in printed.err and f.name in printed.err
    assert not (out / "run").exists()


# ---- review-11 / review-20 / codex-1: --allow-codegen accepts an offer, never makes one ----

def test_do_without_a_possible_verdict_continues_read_only_outcome_planning(folder, tmp_path, monkeypatch, capsys):
    for i, extra in enumerate([{}, {"custom_transform": "maybe"}]):
        fake = FakeClient(resp(tool("submit_editspec", {"spec": UNSUPPORTED, **extra})),
                          resp(tool("need_user", {"question": "Which header outcome should be checked?"})))
        monkeypatch.setattr(compiler_module, "_client", lambda: fake)
        out_dir = tmp_path / f"o{i}"
        assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out_dir), "--no-render",
                         "--allow-codegen", "--yes"]) == 2
        printed = capsys.readouterr().out
        assert "Which header outcome should be checked?" in printed
        assert any("propose_plan" in json.dumps(r.get("tools", [])) for r in fake.requests)
        assert not any("submit_op" in json.dumps(r.get("tools", []), default=str) for r in fake.requests)
        assert not (out_dir / "codegen").exists() and not (out_dir / "run").exists()


# ---- review-12: --replace replaces an item of the same kind only ----

def test_the_library_refuses_a_name_the_other_kind_holds_even_with_replace(folder, tmp_path):
    g = passing_op(folder, tmp_path)
    library.save_recipe("Header distance", REMOVE)
    with pytest.raises(LibraryError, match="cannot take that name, not even with replace"):
        library.save_operation("header DISTANCE", g, replace=True)
    library.save_operation("Strip headers", g)
    with pytest.raises(LibraryError, match="My operation: Strip headers"):
        library.save_recipe("strip headers", REMOVE, replace=True)
    assert sorted(i.label() for i in library.list_items()) == ["My operation: Strip headers", "Recipe: Header distance"]
    assert library.find("header distance").kind == "recipe"              # one name, one item: never ambiguous
    library.save_recipe("Header distance", REMOVE, "again", replace=True)      # the same kind still replaces
    assert library.find("Header distance").description == "again"


def test_save_as_with_replace_never_takes_the_other_kinds_name(folder, tmp_path, monkeypatch, capsys):
    library.save_recipe("Header distance", REMOVE)
    fake = FakeClient(resp(tool("submit_editspec", OFFER)), WRITER)
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    out = tmp_path / "o"
    approval = outcome_cli_flags(tmp_path / "review.json", "header 0.625in from the top", docx(folder), header=True)
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out), "--no-render",
                     "--allow-codegen", "--yes", "--save-as", "Header distance", "--replace", *approval]) == 2
    err = capsys.readouterr().err
    assert "'Recipe: Header distance'" in err and "--replace replaces only an item of the same kind" in err
    assert len(fake.requests) == 1 and not (out / "codegen").exists()     # refused before the writer ran
    assert [i.label() for i in library.list_items()] == ["Recipe: Header distance"]
    # without a terminal and without --allow-codegen only a recipe can come of it: refused before any compile
    library.save_operation("Strip headers", passing_op(folder, tmp_path))
    calls = []
    monkeypatch.setattr(compiler_module, "_client", lambda: calls.append(1) or FakeClient())
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o2"),
                     "--no-render", "--save-as", "strip headers", "--replace"]) == 2
    err = capsys.readouterr().err
    assert "'My operation: Strip headers'" in err and "a recipe cannot take that name" in err and calls == []
    assert library.find("strip headers").kind == "operation"


# ---- review-13: end of input / Ctrl-C at a prompt ----

def test_end_of_input_at_the_wording_approval_stops(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    fake = FakeClient(resp(tool("ask_user", {"understanding": "Stamp the headers.",
                                             "choices": [{"label": "Stamp it", "kind": "alternative",
                                                          "wording": "ISSUED FOR PERMIT"}]})))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    prompts = script_input(monkeypatch, "#1", EOFError)
    out = tmp_path / "o"
    assert cli_main(["do", "stamp the headers", str(folder), "--out", str(out), "--no-render"]) == 2
    assert "Stopped — nothing was run." in capsys.readouterr().out
    assert len(prompts) == 2 and "Approve these exact words" in prompts[1]
    assert len(fake.requests) == 1 and not (out / "preview").exists()     # nothing was sent, nothing previewed


def test_ctrl_c_at_the_reply_prompt_stops(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    asks = FakeClient(resp(tool("ask_user", {"questions": ["Which?"]})))
    monkeypatch.setattr(compiler_module, "_client", lambda: asks)
    script_input(monkeypatch, KeyboardInterrupt)
    assert cli_main(["do", "remove some headers", str(folder), "--out", str(tmp_path / "o"), "--no-render"]) == 2
    assert "Stopped — nothing was run." in capsys.readouterr().out


def test_end_of_input_at_the_run_question_is_a_no(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    prompts = script_input(monkeypatch, EOFError)
    out = tmp_path / "o"
    approval = outcome_cli_flags(tmp_path / "review.json", "remove all headers and footers", docx(folder))
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out), "--no-render", *approval]) == 0
    assert "Not run. To run exactly this" in capsys.readouterr().out and "Run this on 2 file(s)?" in prompts[0]
    assert not (out / "run").exists()


def test_end_of_input_at_the_custom_transform_run_question_is_a_no(folder, tmp_path, monkeypatch, capsys):
    """The terminal's custom-transform path: '#1' picks Claude's offer, the transform passes, and Ctrl-D at 'Run it on
    N file(s)?' is a no."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    fake = FakeClient(resp(tool("submit_editspec", OFFER)), WRITER)
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    prompts = script_input(monkeypatch, "#1", EOFError)
    out = tmp_path / "o"
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out), "--no-render"]) == 0
    assert "Not run. Review the generated module" in capsys.readouterr().out
    assert "Run it on 2 file(s)" in prompts[1] and not (out / "run").exists()


def test_end_of_input_at_the_library_delete_question_keeps_the_item(monkeypatch):
    library.save_recipe("Strip headers", REMOVE)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    script_input(monkeypatch, EOFError)
    assert cli_main(["library", "delete", "strip headers"]) == 2
    script_input(monkeypatch, KeyboardInterrupt)
    assert cli_main(["library", "delete", "strip headers"]) == 2
    assert [i.name for i in library.list_items()] == ["Strip headers"]
    script_input(monkeypatch, "y")
    assert cli_main(["library", "delete", "strip headers"]) == 0 and library.list_items() == []


# ---- review-25: the terminal conversation reaches the visual check ----

def test_the_visual_check_gets_the_terminal_conversation(folder, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Every section, or only some?"]})),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "s2")))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    intents = fake_review(monkeypatch)
    script_input(monkeypatch, "all sections, including the cover", "n")
    assert cli_main(["do", "remove the headers and footers", str(folder), "--out", str(tmp_path / "o"),
                     "--renderer", "none"]) == 0
    assert len(intents) == 1
    assert intents[0].startswith("REQUEST: ")
    assert "\nTHE CONVERSATION:\nREQUEST: remove the headers and footers" in intents[0]
    assert "USER: all sections, including the cover" in intents[0]
    # a spec that did not come from a conversation (no terminal): no conversation section
    monkeypatch.setattr(cli, "_interactive", lambda: False)
    clients = iter([FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))), FakeClient(remove_outcome_proposal())])
    monkeypatch.setattr(compiler_module, "_client", lambda: next(clients))
    assert cli_main(["do", "remove the headers and footers", str(folder), "--out", str(tmp_path / "o2"),
                     "--renderer", "none"]) == 0  # Preview waits for the single execution decision.
    assert len(intents) == 2 and "THE CONVERSATION" not in intents[1]


# ---- review-21: the command line's gate on Claude's visual check ----

def test_the_command_line_gate_on_a_blocking_finding_for_a_spec(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    intents = fake_review(monkeypatch, block=True)
    approval = outcome_cli_flags(tmp_path / "review.json", "remove all headers and footers", docx(folder))
    out = tmp_path / "o"
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(out), "--renderer", "none",
                     "--yes", *approval]) == 0
    printed = capsys.readouterr().out
    assert "Visual review: 1 finding(s)" in printed
    assert "the cover lost its title" in printed and (out / "run").exists() and len(intents) == 1
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o2"),
                     "--renderer", "none", "--yes", "--ack-review", *approval]) == 0
    assert "PASS 2" in capsys.readouterr().out and (tmp_path / "o2" / "run").is_dir() and len(intents) == 2
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o3"),
                     "--renderer", "none", "--yes", "--no-review", *approval]) == 0
    assert len(intents) == 2 and (tmp_path / "o3" / "run").is_dir()      # --no-review: never asked
    # without --yes and without a terminal: not run, and the printed command says what running it means
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o4"),
                     "--renderer", "none", *approval]) == 0
    assert "you have looked at Claude's visual-check findings" in capsys.readouterr().out
    assert not (tmp_path / "o4" / "run").exists()


def test_the_command_line_gate_on_a_blocking_finding_for_a_custom_transform(folder, tmp_path, monkeypatch, capsys):
    def script():
        fake = FakeClient(resp(tool("submit_editspec", OFFER)), WRITER)
        monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    intents = fake_review(monkeypatch, block=True)
    approval = outcome_cli_flags(tmp_path / "review.json", "header 0.625in from the top", docx(folder), header=True)
    script()
    out = tmp_path / "o"
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(out), "--renderer", "none",
                     "--allow-codegen", "--yes", *approval]) == 0
    printed = capsys.readouterr().out
    assert "Visual review: 1 finding(s)" in printed
    assert (out / "run").exists() and len(intents) == 1 and intents[0].startswith("A ONE-OFF CUSTOM TRANSFORM")
    script()
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o2"), "--renderer",
                     "none", "--allow-codegen", "--yes", "--ack-review", *approval]) == 0
    assert "2 of 2 file(s) PASS" in capsys.readouterr().out and len(intents) == 2
    script()
    assert cli_main(["do", "header 0.625in from the top", str(folder), "--out", str(tmp_path / "o3"), "--renderer",
                     "none", "--allow-codegen", "--yes", "--no-review", *approval]) == 0
    assert "2 of 2 file(s) PASS" in capsys.readouterr().out and len(intents) == 2


# ---- review-10: one output per sample, never two samples at one path ----

def test_preview_samples_with_the_same_name_keep_their_own_outputs(tmp_path):
    from spec_surgeon.editspec import load_spec, plan_spec
    from spec_surgeon.fingerprint import fingerprint
    from spec_surgeon.package import DocxPackage
    from spec_surgeon.preview import make_preview
    from spec_surgeon.verify import run_suite
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    a, b = tmp_path / "A" / "spec.docx", tmp_path / "B" / "spec.docx"
    a.write_bytes(build("21 13 13", "WET-PIPE"))
    b.write_bytes(build_rich())                                           # another structure pattern: both sampled
    spec = load_spec(SPECS / "change-all-font-arial.json")
    pv = make_preview(spec, [str(a), str(b)], tmp_path / "pv", do_render=False)
    assert [s.source for s in pv.samples] == [a, b] and all(s.passed for s in pv.samples)
    assert [s.output.name for s in pv.samples] == ["spec.docx", "spec (1).docx"]
    for s in pv.samples:                                                  # each output is its own source's
        pkg = DocxPackage(s.source.read_bytes(), name=str(s.source))
        plan, _ = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
        assert run_suite(pkg.data, s.output.read_bytes(), plan.to_dict(), str(s.source), str(s.output)).passed


# =====================================================================================================================
# The window: codex-2, review-14, review-15, review-17, review-19, merge-verify-0/1
# =====================================================================================================================

DOCUMENTS = (".docx", ".pdf", ".png")


def window(qt, session=None, **kw):
    from spec_surgeon.gui import build_window
    from spec_surgeon.ui.app import RecordingPrompts
    kw.setdefault("prompts", RecordingPrompts())
    w = build_window(session, **kw)
    qt[1].append(w)
    return w, w._spec_surgeon


def asks(n=1):
    """Claude asks, with one choice, ``n`` times (a further call is a defect: the script ran out)."""
    return FakeClient(*[resp(tool("ask_user", {"understanding": "Remove the headers.",
                                               "choices": [{"label": "Every section", "kind": "clarify"}]}, f"a{i}"))
                        for i in range(n)])


def zip_names(path) -> list:
    with zipfile.ZipFile(path) as z:
        return z.namelist()


# ---- codex-2: the rendering option is part of what a preview is of -------------------------------------------------

def test_turning_render_pages_on_or_off_after_a_preview_needs_a_new_preview(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert ui["buttons"]["run"].isEnabled()
    ui["vars"]["render"].set(True)               # pages asked for after a preview without them: no page images, no
    assert not ui["buttons"]["run"].isEnabled()  # visual check — Run must wait for a preview that has them
    assert "press Preview again" in w.run_page.run_hint.text()
    assert w.do_run() is False and "Preview first" in w.run_page.info.title.text()
    ui["vars"]["render"].set(False)
    assert ui["buttons"]["run"].isEnabled()
    k = w._widget_key()
    ui["vars"]["render"].set(True)
    assert w._widget_key() != k                  # both ways: the key itself carries the option


# ---- review-14: one of your operations after a loaded spec or recipe ----------------------------------------------


def _label(kind):
    i = next(i for i in library.list_items() if i.kind == kind)
    return i.label() + (f" — {i.description}" if i.description else "")


@pytest.mark.parametrize("first", ["recipe", "saved spec"])
def test_an_operation_picked_after_a_loaded_spec_can_run_and_preview_again(qt, folder, tmp_path, first):
    from spec_surgeon.ui.app import RecordingPrompts
    saved_operation(folder, tmp_path)
    library.save_recipe("Strip headers", REMOVE)
    prompts = RecordingPrompts()
    s = Session(out_root=tmp_path / "out")
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("")
    if first == "recipe":
        prompts.items = [_label("recipe")]
        assert w.do_library()
    else:
        prompts.files = [str(SPECS / "remove-all-headers-footers.json")]
        assert w.do_load_spec()
    assert s.spec is not None and not w.request_page.spec_bar.isHidden()
    prompts.items = [_label("operation")]
    assert w.do_library()
    ui["wait"]()
    assert s.generated is not None and s.spec is None
    assert w.stack.currentWidget() is w.preview_page
    assert ui["buttons"]["run"].isEnabled(), w.run_page.run_hint.text()
    assert w.request_page.spec_bar.isHidden()      # its Remove would have thrown the operation away
    ui["buttons"]["preview"].click()               # Preview with the request box empty: the operation again,
    ui["wait"]()                                   # never "type what you want done"
    assert "That didn't work" not in w.request_page.info.title.text(), w.request_page.info.text.text()
    assert s.generated is not None and ui["buttons"]["run"].isEnabled()
    assert "Type a request" not in w.request_page.preview_hint.text()


def test_a_typed_request_that_replaces_a_loaded_spec_takes_the_saved_spec_bar_away(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})),
                                                           remove_outcome_proposal()))
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    assert not w.request_page.spec_bar.isHidden()
    ui["request"].set("remove all headers and footers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.spec_source == "compiled" and ui["buttons"]["run"].isEnabled()
    assert w.preview_page.outcome_card.isHidden()
    assert w.request_page.spec_bar.isHidden()


# ---- review-15: a chat that ended leaves the screen -----------------------------------------------------------------

def _chatting(qt, folder, tmp_path, **kw):
    s = Session(out_root=tmp_path / "out", client=asks())
    w, ui = window(qt, s, **kw)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert len(ui["choices"]()) == 1 and not ui["chat"].isHidden()
    return s, w, ui


def test_load_saved_spec_ends_the_chat_on_screen(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    s, w, ui = _chatting(qt, folder, tmp_path,
                         prompts=RecordingPrompts(files=[str(SPECS / "remove-all-headers-footers.json")]))
    assert w.do_load_spec()
    assert s.conversation is None and ui["chat"].isHidden() and ui["preview_chat"].isHidden()


def test_use_the_whole_folder_again_ends_the_chat_on_screen(qt, folder, tmp_path):
    s = Session(out_root=tmp_path / "out", client=asks())
    s.selected_files = sorted(folder.glob("*.docx"))[:1]          # a set-aside pass
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    w._show_set_aside()
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert not ui["chat"].isHidden() and s.conversation is not None
    w._clear_set_aside()                                           # the bar's "Use the whole folder again"
    assert s.selected_files is None and s.conversation is None
    assert ui["chat"].isHidden() and ui["preview_chat"].isHidden()


@pytest.mark.parametrize("how", ["send", "choose"])
def test_a_reply_after_the_folder_changed_ends_the_chat_instead_of_failing(qt, folder, tmp_path, how):
    s, w, ui = _chatting(qt, folder, tmp_path)           # one scripted turn: a second call to Claude would fail loudly
    other = tmp_path / "Other"
    other.mkdir()
    (other / "21 05 00 – OTHER.docx").write_bytes(build("21 05 00", "OTHER"))
    ui["vars"]["folder"].set(str(other))
    log_before = ui["log"].get()
    cid = ui["choices"]()[0][0]
    assert (w.do_send("every section") if how == "send" else w.do_choose(cid)) is False
    assert not w.busy and s.conversation is None
    assert ui["chat"].isHidden() and ui["preview_chat"].isHidden()
    assert "ERROR" not in ui["log"].get()[len(log_before):]
    assert "conversation ended" in w.request_page.info.text.text()


# ---- review-17: Save to library and the acknowledgement only while the preview is current ---------------------------

def test_save_and_acknowledge_are_live_only_while_the_preview_is_current(qt, folder, tmp_path):
    from spec_surgeon.vision import VisualReview
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})),
                      resp(tool("ask_user", {"questions": ["Which sections?"]}, "a2")))
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove all headers and footers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    p, save, ack = w.preview_page, ui["buttons"]["save_to_library"], ui["buttons"]["review_ack"]
    assert p.outcome_card.isHidden()
    assert ui["buttons"]["run"].isEnabled() and save.isEnabled()
    s.preview.review = VisualReview(summary="Looked.", findings=[{"severity": "block", "text": "Logo squashed."}])
    w._fill_preview()
    w._refresh_run_state()
    assert ack.isHidden() and ui["buttons"]["run"].isEnabled()
    ui["request"].set("remove the footers only")                 # out of date: nothing to save or acknowledge
    assert not save.isEnabled() and not ack.isEnabled()
    ui["request"].set("remove all headers and footers")
    assert save.isEnabled() and ack.isEnabled()
    ui["preview_chat"].reply.setPlainText("hmm, what about the cover?")
    ui["preview_chat"].send_btn.click()                           # Claude only asks: the preview is void
    ui["wait"]()
    assert s.preview is None and s.spec is None
    assert p.body.isHidden() and not p.empty.isHidden()          # the Preview step's empty state, not the old preview
    assert not save.isEnabled() and not ack.isEnabled()
    w.set_busy(True)
    w.set_busy(False)                                             # a job's end never re-enables them
    assert not save.isEnabled() and not ack.isEnabled()
    assert w.do_save_to_library() is False and w.do_ack_review() is False


# ---- review-19: a choice's detail is plain text ---------------------------------------------------------------------

def test_a_choice_s_detail_is_shown_as_plain_text(qt):
    from PySide6.QtGui import QTextDocument
    from spec_surgeon.ui.chat import ChatView
    view = ChatView()
    detail = 'Keep <b>the cover</b> <span style="display:none">hidden</span><img src="C:/x.png"> & "this" too'
    view.set_transcript([], [{"id": "c1", "label": "Every section", "kind": "clarify", "detail": detail},
                             {"id": "c2", "label": "Only the cover", "kind": "clarify", "detail": ""}], "", True)
    b, b2 = view.choice_buttons()
    doc = QTextDocument()
    doc.setHtml(b.toolTip())                    # how Qt renders a tooltip that might be rich text
    assert doc.toPlainText() == detail          # every character shown as typed: nothing hidden, restyled or loaded
    assert "<b>" not in b.toolTip() and "<img" not in b.toolTip()
    assert b2.toolTip() == ""
    view.deleteLater()


# ---- merge-verify-0/1: the bundle keeps the last run and adds the conversation's newer folder ------------------------

def test_make_bundle_mirrors_more_folders_beside_the_run_never_a_document_or_a_folder_twice(tmp_path):
    first = tmp_path / "out" / "20261015-100000"
    (first / "run" / "logs").mkdir(parents=True)
    (first / "compile.json").write_text("{}", encoding="utf-8")
    (first / "run" / "DIAGNOSTICS.md").write_text("why", encoding="utf-8")
    (first / "run" / "logs" / "run.log").write_text("log", encoding="utf-8")
    later = tmp_path / "out" / "20261015-100500"
    (later / "chat").mkdir(parents=True)
    (later / "preview" / "samples").mkdir(parents=True)
    (later / "conversation.json").write_text('{"entries": []}', encoding="utf-8")
    (later / "chat" / "page-1.png").write_bytes(b"\x89PNG")
    (later / "preview" / "samples" / "a.docx").write_bytes(b"PK")
    (later / "preview" / "preview.json").write_text("{}", encoding="utf-8")
    res = diag.make_bundle(tmp_path / "b.zip", first / "run", None,
                           also=[later, first, first / "run", tmp_path / "missing"])
    names = zip_names(res["path"])
    assert {"results/compile.json", "results/run/DIAGNOSTICS.md", "results/run/logs/run.log"} <= set(names)
    assert {"results-later/20261015-100500/conversation.json",
            "results-later/20261015-100500/preview/preview.json"} <= set(names)
    assert not [n for n in names if n.lower().endswith(DOCUMENTS)] and res["left_out"] == 2
    assert not [n for n in names if n.startswith("results-later/") and "100500" not in n]    # never bundled twice
    with zipfile.ZipFile(res["path"]) as z:
        manifest = json.loads(z.read("environment.json"))
        assert "results-later/" in z.read("README.txt").decode("utf-8")
    assert manifest["also"] == [str(later.resolve())]
    plain = diag.make_bundle(tmp_path / "plain.zip", first / "run")             # without ``also``: as before
    assert not [n for n in zip_names(plain["path"]) if n.startswith("results-later/")]


def test_the_window_bundle_after_a_run_and_a_reply_holds_both(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})),
                      resp(tool("ask_user", {"questions": ["What about the cover?"]}, "a2")))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile("remove all headers and footers", lambda m: None)
    s.make_preview(render=False, log=lambda m: None)
    s.run(False, False, lambda m: None)
    first = s.out_dir
    s.reply("hmm, what about the cover?")
    later = s.chat_dir
    assert later != first and s.last_run_dir == first / "run"
    prompts = RecordingPrompts(files=[str(tmp_path / "bundle.zip")])
    w, _ui = window(qt, s, prompts=prompts)
    names = zip_names(w.save_bundle())
    assert {"results/run/DIAGNOSTICS.md", "results/run/logs/run.log", "results/conversation.json",
            "results/compile.json", "results/preview/preview.json"} <= set(names)      # the last run, with its session
    assert {f"results-later/{later.name}/conversation.json", f"results-later/{later.name}/compile.json"} <= set(names)
    with zipfile.ZipFile(tmp_path / "bundle.zip") as z:
        newer = json.loads(z.read(f"results-later/{later.name}/conversation.json"))
        older = json.loads(z.read("results/conversation.json"))
    assert any("what about the cover" in (e.get("text") or "") for e in newer["entries"])
    assert not any("what about the cover" in (e.get("text") or "") for e in older["entries"])
    assert not [n for n in names if n.lower().endswith(DOCUMENTS)]
    assert any(kind == "notify" and later.name in text for kind, _, text in prompts.shown)


def test_the_window_bundle_after_a_new_request_failed_holds_its_conversation_and_the_last_run(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    prompts = RecordingPrompts(files=[str(tmp_path / "bundle.zip")])
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    ui["buttons"]["run"].click()
    ui["wait"](120)
    first = s.last_run_dir.parent
    s.client = FakeClient()                     # nothing scripted: the first call of the new request is a defect
    ui["request"].set("make every heading bold")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert "That didn't work" in w.request_page.info.title.text()
    later = s.chat_dir
    assert later != first and (later / "conversation.json").is_file()
    names = zip_names(w.save_bundle())          # what the error bar's "Save diagnostics…" does
    assert "results/run/DIAGNOSTICS.md" in names and "results/preview/preview.json" in names
    assert f"results-later/{later.name}/conversation.json" in names
    assert not [n for n in names if n.lower().endswith(DOCUMENTS)]


def test_the_window_bundle_before_any_run_is_the_preview_with_the_conversation_s_newer_folder(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})),
                      resp(tool("ask_user", {"questions": ["Which?"]}, "a2")))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile("remove all headers and footers", lambda m: None)
    s.make_preview(render=False, log=lambda m: None)
    first = s.out_dir
    s.reply("hmm")
    later = s.chat_dir
    assert later != first and s.last_run_dir is None
    w, _ui = window(qt, s, prompts=RecordingPrompts(files=[str(tmp_path / "bundle.zip")]))
    names = zip_names(w.save_bundle())
    assert "results/preview/preview.json" in names and "results/conversation.json" in names
    assert f"results-later/{later.name}/conversation.json" in names
    s.end_conversation()                        # Start over: the conversation's folder is no longer the session's
    os.remove(tmp_path / "bundle.zip")
    w.prompts.files = [str(tmp_path / "bundle.zip")]
    names = zip_names(w.save_bundle())
    assert "results/preview/preview.json" in names and not [n for n in names if n.startswith("results-later/")]


# =====================================================================================================================
# The test run sealed off from the machine (tests/conftest.py): review-23, review-22
# =====================================================================================================================

SRC = ROOT / "src" / "spec_surgeon"
PINNED = "claude-opus-5-5"
REAL_KEY = "sk-ant-ABRAHAMS-REAL-SAVED-KEY"


class FakeCredentialManager:
    """Stands in for pywin32's ``win32cred`` on a Windows machine where the installed app saved a real key: it holds
    that key under the app's own target and records every call that reaches it."""
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self):
        self.store = {credentials.TARGET: REAL_KEY}
        self.calls = []

    def CredRead(self, target, type_, flags):  # noqa: N802 — pywin32's names
        self.calls.append(("CredRead", target))
        if target not in self.store:
            raise OSError(1168, "CredRead", "Element not found.")
        return {"TargetName": target, "CredentialBlob": self.store[target].encode("utf-16-le")}

    def CredWrite(self, cred, flags):  # noqa: N802
        self.calls.append(("CredWrite", cred["TargetName"]))
        self.store[cred["TargetName"]] = cred["CredentialBlob"]

    def CredDelete(self, target, type_, flags):  # noqa: N802
        self.calls.append(("CredDelete", target))
        del self.store[target]


@pytest.fixture
def machine_with_a_saved_key(monkeypatch):
    """Plants the recording Credential Manager where ``import win32cred`` finds it, as on Abraham's machine."""
    fake = FakeCredentialManager()
    monkeypatch.setitem(sys.modules, "win32cred", fake)
    return fake


def _unpatched_credentials_module():
    """A fresh copy of spec_surgeon/credentials.py (not the module object conftest patched): the control that shows the
    planted store is what the app's own code would read."""
    spec = importlib.util.spec_from_file_location("_credentials_control", SRC / "credentials.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---- review-23: Windows Credential Manager ------------------------------------------------------------------------

def test_no_test_reads_or_writes_the_key_saved_on_this_computer(machine_with_a_saved_key):
    fake = machine_with_a_saved_key
    assert credentials.available() is False
    assert credentials.load() is None and credentials.key_source() == "none"
    with pytest.raises(RuntimeError):
        credentials.save("sk-ant-test-not-a-real-key")
    assert credentials.delete() is False
    assert fake.calls == [] and fake.store == {credentials.TARGET: REAL_KEY}
    control = _unpatched_credentials_module()                 # without conftest's cut the same machine gives up the key
    assert control.available() and control.load() == REAL_KEY and fake.calls == [("CredRead", credentials.TARGET)]


def test_a_window_never_loads_or_overwrites_the_saved_key(machine_with_a_saved_key, monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    from spec_surgeon.gui import build_window
    from spec_surgeon.ui.app import RecordingPrompts
    from spec_surgeon.ui.settings_dialog import SettingsDialog
    fake = machine_with_a_saved_key
    w = build_window(None, prompts=RecordingPrompts())
    try:
        assert w.key_origin == "none" and credentials.ENV not in os.environ     # MainWindow._init_key found nothing
        dlg = SettingsDialog(w)
        assert not dlg.remember.isEnabled() and not dlg.remember.isChecked() and not dlg.forget.isEnabled()
        dlg.key.setText("sk-ant-test-not-a-real-key")
        dlg.remember.setChecked(True)                    # even ticked by code, apply() cannot reach the store
        dlg.apply()
        assert w.key_origin == "session" and os.environ[credentials.ENV] == "sk-ant-test-not-a-real-key"
        dlg._forget()
        assert fake.calls == [] and fake.store == {credentials.TARGET: REAL_KEY}
    finally:
        close_windows(app, [w])


def test_credentials_py_is_the_only_way_to_credential_manager():
    """conftest cuts the tests off at ``credentials._win32cred``; that is complete only while no other module imports
    win32cred or calls the Cred* API by another route."""
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "credentials.py" and py.parent == SRC:
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names = [node.value]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(n.split(".")[0] == "win32cred" or n.startswith(("CredRead", "CredWrite", "CredDelete"))
                   for n in names):
                offenders.append(py.relative_to(SRC).as_posix())
    assert offenders == []


# ---- review-22: the model ---------------------------------------------------------------------------------------

def test_the_model_is_the_default_whatever_the_machine_sets(tmp_path):
    """Run as is and, by the test below, in a pytest of its own with SPEC_SURGEON_MODEL set."""
    from spec_surgeon import advisor, compiler, llm
    assert "SPEC_SURGEON_MODEL" not in os.environ
    assert llm.DEFAULT_MODEL == compiler.DEFAULT_MODEL == advisor.DEFAULT_MODEL == PINNED
    bound = {name: mod.DEFAULT_MODEL for name, mod in list(sys.modules.items())
             if name.split(".")[0] == "spec_surgeon" and hasattr(mod, "DEFAULT_MODEL")}
    assert set(bound.values()) == {PINNED}, bound
    assert compiler.CompileResult(status="ok").model == PINNED          # a dataclass default bound at import
    doc = tmp_path / "21 00 00 – SPEC.docx"
    doc.write_bytes(build("21 00 00", "SPEC"))
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})), resp(text("done"), stop="end_turn"))
    res = compiler.compile_request("remove all headers and footers", [doc], client=fake)
    assert fake.requests[0]["model"] == PINNED and res.model == PINNED


def test_a_model_bound_before_conftest_ran_is_pinned_for_the_test(monkeypatch, tmp_path):
    """conftest drops SPEC_SURGEON_MODEL before the package is imported; if something imported it earlier (a plugin),
    pin_model still makes every bound copy the default for the test."""
    import conftest
    from spec_surgeon import advisor, compiler, llm
    for mod in (llm, compiler, advisor):
        monkeypatch.setattr(mod, "DEFAULT_MODEL", "claude-sonnet-5-5")      # what an earlier import would have bound
    inner = pytest.MonkeyPatch()
    try:
        conftest.pin_model(inner)
        assert llm.DEFAULT_MODEL == compiler.DEFAULT_MODEL == advisor.DEFAULT_MODEL == PINNED
        doc = tmp_path / "21 00 00 – SPEC.docx"
        doc.write_bytes(build("21 00 00", "SPEC"))
        fake = FakeClient(resp(text("Accept or fix the file first."), stop="end_turn"))
        adv = advisor.advise({"source": str(doc), "exception": {"code": "structure"}}, client=fake)
        assert adv.kind == "recommendation" and adv.model == PINNED and fake.requests[0]["model"] == PINNED
    finally:
        inner.undo()


def test_a_model_set_on_the_developer_s_machine_does_not_reach_the_tests():
    env = {**os.environ, "SPEC_SURGEON_MODEL": "claude-sonnet-5-5", "PYTHONDONTWRITEBYTECODE": "1"}
    node = f"{Path(__file__).resolve()}::test_the_model_is_the_default_whatever_the_machine_sets"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "1 passed" in r.stdout


# ---- the environment after a test -------------------------------------------------------------------------------

@pytest.mark.parametrize("machine", [None, "sk-ant-the-machine-s-own-key"])
def test_a_key_a_test_writes_into_the_environment_is_gone_after_it(monkeypatch, machine):
    """SettingsDialog.apply writes ``os.environ[ANTHROPIC_API_KEY]`` itself; after the test the environment is the
    machine's again (its own key or none), not the typed one."""
    import conftest
    if machine is not None:
        monkeypatch.setenv(credentials.ENV, machine)
    inner = pytest.MonkeyPatch()
    try:
        conftest.seal_environment(inner)
        assert credentials.ENV not in os.environ and "SPEC_SURGEON_MODEL" not in os.environ
        os.environ[credentials.ENV] = "sk-ant-typed-in-settings"
    finally:
        inner.undo()
    assert os.environ.get(credentials.ENV) == machine


# ---- review-7: the custom-transform sandbox ----------------------------------------------------------------------

OP_HEAD = """from spec_surgeon.ops.base import Op, PlanFragment
{imports}

class Probe(Op):
    name = "probe"
    mode = "formatting"

    def plan(self, pkg, select, params):
        {body}
        return self.fragment(select, params)

    def apply(self, work, fragment):
        pass
"""


def probe_source(body, imports=""):
    return OP_HEAD.format(imports=imports, body=body)


@pytest.mark.parametrize("imports, body", [
    ("import re", "re.enum"),                                         # an allowed module's other attributes
    ("import json", "json.codecs"),
    ("import json", "json.decoder"),
    ("import re as r", "r.enum"),                                     # under another name
    ("import copy", "copy.sys"),
    ("from lxml import etree", "etree.ElementTree"),                  # lxml beyond elements
    ("from lxml import etree as E", "E.xmlfile"),
    ("from lxml import etree", "etree.Element('x').getroottree().write_c14n('x')"),   # a tree's writers
    ("from lxml import etree", "etree.Element('x').getroottree().xinclude()"),
    ("import re", "x = re"),                                          # the module object itself, anywhere
    ("import json", "print(json)"),
    ("import json", "[json][0]"),
    ("import re", "re = None"),
    ("", "(x for x in ()).gi_frame"),                                 # frames and generator internals
    ("", "(x for x in ()).gi_frame.f_back.f_globals"),
    ("", "self.plan.func_globals"),
    ("", "__builtins__"),                                             # dunder names
    ("", "__loader__"),
])
def test_the_whitelist_refuses_every_route_from_an_allowed_module_to_the_file_system(imports, body):
    from spec_surgeon.codegen import check_source
    assert check_source(probe_source(body, imports)), f"{imports!r} / {body!r} must be refused"


def test_the_whitelist_still_accepts_what_an_honest_op_uses():
    from spec_surgeon.codegen import check_source
    assert check_source(GOOD_OP) == []
    honest = probe_source(
        "rx = re.compile(r'^PART \\d', re.IGNORECASE)\n"
        "        data = json.loads(json.dumps({'a': [1, 2]}))\n"
        "        twips = math.ceil(0.625 * 1440)\n"
        "        pairs = list(itertools.chain([1], [2]))\n"
        "        total = functools.reduce(lambda a, b: a + b, pairs)\n"
        "        el = etree.SubElement(etree.Element('p'), 'r')\n"
        "        clone = copy.deepcopy(el)\n"
        "        ok = bool(rx.search('PART 1')) and twips == 900 and total == 3 and etree.iselement(clone)",
        "import re\nimport json\nimport math\nimport itertools\nimport functools\nimport copy\nfrom lxml import etree")
    assert check_source(honest) == []


def test_every_module_attribute_the_reference_ops_use_is_allowed():
    from spec_surgeon import codegen
    ops_dir = Path(codegen.__file__).parent / "ops"
    for name in codegen.EXAMPLE_OPS:
        tree = ast.parse((ops_dir / name).read_text(encoding="utf-8"))
        bound = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bound.update({a.asname or a.name: a.name for a in node.names if a.name in codegen.MODULE_ATTRS})
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    key = codegen.MODULE_FROM_IMPORTS.get((node.module, a.name))
                    if key:
                        bound[a.asname or a.name] = key
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in bound:
                assert node.attr in codegen.MODULE_ATTRS[bound[node.value.id]], (name, node.value.id, node.attr)


def test_a_saved_operation_that_reaches_past_the_whitelist_is_refused():
    from spec_surgeon.codegen import GeneratedOp
    bad = probe_source("re.enum", "import re")
    library.save_operation("Probe", GeneratedOp(source=bad, name="probe", passed=True))
    with pytest.raises(LibraryError, match="no longer passes the safety whitelist"):
        library.load_operation("Probe")


GUARD_SCRIPT = """
import os, subprocess, sys
from spec_surgeon.sandbox_runner import install_guard
work, outside = sys.argv[1], sys.argv[2]
install_guard(work)
results = {}
def attempt(name, fn):
    try:
        fn()
        results[name] = "allowed"
    except PermissionError:
        results[name] = "refused"
attempt("write inside", lambda: open(os.path.join(work, "out.txt"), "w").write("ok"))
attempt("write outside", lambda: open(outside, "w").write("changed"))
attempt("append outside", lambda: open(outside, "a").write("changed"))
attempt("os.open outside", lambda: os.close(os.open(outside, os.O_WRONLY)))
attempt("remove outside", lambda: os.remove(outside))
attempt("rename outside", lambda: os.rename(outside, outside + ".moved"))
attempt("remove inside", lambda: os.remove(os.path.join(work, "out.txt")))
attempt("read outside", lambda: open(outside).read())
attempt("start a process", lambda: subprocess.run([sys.executable, "-c", "pass"]))
print(results)
"""


def test_the_sandbox_guard_keeps_writes_inside_its_folder(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "kept.txt"
    outside.write_text("original", encoding="utf-8")
    script = tmp_path / "guard_probe.py"
    script.write_text(GUARD_SCRIPT, encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    out = subprocess.run([sys.executable, str(script), str(work), str(outside)], capture_output=True, text=True,
                         env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    results = json.loads(out.stdout.strip().splitlines()[-1].replace("'", '"'))
    assert results == {"write inside": "allowed", "write outside": "refused", "append outside": "refused",
                       "os.open outside": "refused", "remove outside": "refused", "rename outside": "refused",
                       "remove inside": "allowed", "read outside": "allowed", "start a process": "refused"}
    assert outside.read_text(encoding="utf-8") == "original"


def test_the_sandbox_still_runs_an_honest_op(tmp_path):
    from spec_surgeon.codegen import run_in_sandbox
    src = tmp_path / "21 00 00 – SPEC.docx"
    src.write_bytes(build("21 00 00", "SPEC"))
    before = src.read_bytes()
    res = run_in_sandbox(GOOD_OP, src, {}, {"twips": 900}, tmp_path / "box")
    assert res.ok, (res.error, res.traceback, res.stderr)
    assert res.output is not None and res.output.is_file() and src.read_bytes() == before


def test_the_transform_writer_lists_same_named_samples_by_their_labels(tmp_path):
    a, b = tmp_path / "A" / "spec.docx", tmp_path / "B" / "spec.docx"
    for f, num in ((a, "21 00 00"), (b, "21 13 13")):
        f.parent.mkdir()
        f.write_bytes(build(num, "SPEC"))
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    g = generate_op("header distance", "set_header_distance", [a, b], tmp_path / "cg", client=fake)
    first = fake.requests[0]["messages"][0]["content"]
    assert "'A/spec.docx'" in first and "'B/spec.docx'" in first and g.passed
