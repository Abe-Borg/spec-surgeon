"""The conversation accepts one spec per layout and checks each on its own documents (Workstream L)."""
from __future__ import annotations

import copy
from pathlib import Path

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_styles
from spec_surgeon import compiler
from spec_surgeon.conversation import Conversation
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.package import DocxPackage

REQUEST = "Make the body paragraphs bold."


def _files(tmp_path):
    typed = tmp_path / "a" / "21 13 13.docx"
    styled = tmp_path / "b" / "21 13 16.docx"
    typed.parent.mkdir(parents=True); styled.parent.mkdir(parents=True)
    typed.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    styled.write_bytes(build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    return typed, styled


def _pattern(path: Path) -> str:
    return fingerprint(DocxPackage.from_path(path))["pattern_id"]


def _submission(typed, styled):
    bold = {"run": {"b": True}, "tracked": False}
    return {"summary": "Bold body paragraphs in two layouts", "layouts": [
        {"id": "A", "label": "typed numbers", "pattern_ids": [_pattern(typed)],
         "spec": {"summary": "by text", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"text_regex": r"^[A-E]\.\t"}}, "params": bold}]}},
        {"id": "B", "label": "styled", "pattern_ids": [_pattern(styled)],
         "spec": {"summary": "by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara"]}}, "params": bold}]}}]}


def test_check_submission_accepts_a_full_cover_and_names_every_gap(tmp_path):
    typed, styled = _files(tmp_path)
    batch = compiler._Batch([typed, styled], None, input_files=[])
    good = _submission(typed, styled)
    assert compiler.check_submission(good, batch, [REQUEST]) == []
    partial = copy.deepcopy(good)
    del partial["layouts"][1]
    errs = compiler.check_submission(partial, batch, [REQUEST])
    assert errs and "no layout covers pattern" in errs[0] and styled.name in errs[0]
    unknown = copy.deepcopy(good)
    unknown["layouts"][1]["pattern_ids"] = ["0000000000"]
    errs = compiler.check_submission(unknown, batch, [REQUEST])
    assert errs and "THE BATCH does not list" in errs[0]
    overlap = copy.deepcopy(good)
    overlap["layouts"][1]["pattern_ids"] = good["layouts"][0]["pattern_ids"] + good["layouts"][1]["pattern_ids"]
    errs = compiler.check_submission(overlap, batch, [REQUEST])
    assert errs and "only one plan" in errs[0]


def test_words_inside_a_layout_spec_must_be_the_users(tmp_path):
    typed, styled = _files(tmp_path)
    batch = compiler._Batch([typed, styled], None, input_files=[])
    sub = _submission(typed, styled)
    sub["layouts"][0]["spec"] = {"summary": "reword", "mode": "content", "ops": [
        {"op": "replace_text", "select": {"scope": ["document"]},
         "params": {"find": "FM Global", "replace": "Factory Mutual", "tracked": True}}]}
    assert "Factory Mutual" in compiler.placed_words(sub)
    errs = compiler.check_submission(sub, batch, [REQUEST])
    assert errs and errs[0].startswith("layout 'A'") and "never gave them verbatim" in errs[0]
    assert compiler.check_submission(sub, batch, [REQUEST, "replace FM Global with Factory Mutual"]) == []


def test_each_layout_is_dry_planned_on_its_own_documents_only(tmp_path):
    typed, styled = _files(tmp_path)
    batch = compiler._Batch([typed, styled], None, input_files=[])
    sub = _submission(typed, styled)
    # a header/footer template fill is refused on the styled fixture's different anchor; it stays out of layout B
    sub["layouts"][0]["spec"]["ops"][0]["select"] = {"paragraphs": {"index_range": [0, 2]}}
    assert compiler.check_submission(sub, batch, [REQUEST]) == []


def test_conversation_turns_a_layout_submission_into_a_plan(tmp_path):
    typed, styled = _files(tmp_path)
    sub = _submission(typed, styled)
    sub["layouts"][0]["spec"]["request"] = "text the model typed, not the user's"
    conversation = Conversation(REQUEST, [typed, styled], client=FakeClient(resp(tool("submit_editspec", {"spec": sub}))),
                                mode="chat")
    turn = conversation.start()
    assert turn.status == "ok"
    plan = turn.spec
    assert plan["kind"] == "layouts" and plan["request"] == REQUEST
    assert [layout["id"] for layout in plan["layouts"]] == ["A", "B"]
    assert all(layout["spec"]["request"] == REQUEST for layout in plan["layouts"])
    assert conversation.entries[-1].role == "claude" and "two layouts" in conversation.entries[-1].text
