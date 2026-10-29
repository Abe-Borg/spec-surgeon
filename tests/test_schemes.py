"""The bundled numbering schemes (schemes.py, docs/SPEC_FORMATTER_MODES_DESIGN.md N5): the committed files are a fresh
build's bytes; each is one list with a paragraph style per level; the chat offers one only as an app choice whose label
the app writes; picking it in the window attaches a copy as the template and plans the request again, the pick a
decision and never wording. Scripted client only."""
from __future__ import annotations

from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build_styled_outline
from make_scheme_examples import schemes as fresh_schemes
from spec_surgeon import schemes
from spec_surgeon.conversation import APP_CHOICE_KINDS, Conversation
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.ops import lists
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage


def test_the_committed_schemes_are_a_fresh_build():
    built = fresh_schemes()
    assert set(built) == {name for name, *_ in schemes.SCHEMES.values()}
    for scheme in schemes.SCHEMES:
        assert schemes.scheme_file(scheme).read_bytes() == built[schemes.SCHEMES[scheme][0]], scheme


@pytest.mark.parametrize("scheme", sorted(schemes.SCHEMES))
def test_each_scheme_is_one_list_with_a_style_per_level(scheme):
    pkg = DocxPackage.from_path(schemes.scheme_file(scheme))
    found = lists.resolve_scheme(schemes.levels(scheme), StyleSheet(pkg), lists.ListDefs.load(pkg))
    assert [level.ilvl for level in found.levels] == list(range(9))
    shown = [row.text for row in lists.count(pkg.xml(pkg.main_part()), StyleSheet(pkg), lists.ListDefs.load(pkg))]
    assert shown == list(schemes.SCHEMES[scheme][3])


def test_the_scheme_text_is_the_formatters():
    assert schemes.SCHEMES["csc"][3][:3] == ("PART 1", "1.1", ".1")
    assert schemes.SCHEMES["csi"][3] == ("PART 1", "1.1", "A.", "1.", "a.", "1)", "a)", "(1)", "(a)")


# ---- the chat's choice ---------------------------------------------------------------------------------------------

@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – SPEC.docx").write_bytes(build_styled_outline())
    return d


def ask_scheme(*choices):
    return resp(tool("ask_user", {"message": "Which numbering?", "questions": ["Which numbering?"],
                                  "choices": list(choices)}, "a1"))


def test_a_scheme_choice_is_the_apps_label_and_carries_no_words(folder):
    fake = FakeClient(ask_scheme({"label": "Go Canadian!", "kind": "scheme", "scheme": "csc"},
                                 {"label": "Elsewhere", "kind": "scheme", "scheme": "masterspec"}))
    c = Conversation("convert to PageFormat", sorted(folder.glob("*.docx")), client=fake)
    turn = c.start()
    assert "scheme" in APP_CHOICE_KINDS
    [choice] = turn.choices
    assert choice.kind == "scheme" and choice.label == schemes.label("csc") and choice.words == []
    assert "PART 1, 1.1, .1" in choice.detail and "in place of" not in choice.detail
    assert any("scheme choice was dropped" in e for e in turn.errors)
    with pytest.raises(ValueError):
        c.reply(choice_id=choice.id)                                # carried out by the app, never sent to Claude


def test_picking_a_scheme_attaches_a_copy_as_the_template_and_plans_again(tmp_path, folder):
    fake = FakeClient(ask_scheme({"label": "x", "kind": "scheme", "scheme": "csc"}),
                      resp(tool("ask_user", {"message": "Keep each paragraph's look?",
                                             "questions": ["Keep each paragraph's look?"]}, "a2")))
    task = DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False)
    source = next(folder.glob("*.docx"))
    task.attach([source])
    view = task.perform("send", text="Convert the numbering to Canadian PageFormat.")
    [choice] = view["choices"]
    assert choice["label"] == schemes.label("csc")
    task.perform("choose", choice_id=choice["id"])
    template = task.template()
    assert template is not None and template.name == "CSC PageFormat scheme.docx"
    assert (tmp_path / "out" / "attachments") in template.parents
    assert template.read_bytes() == schemes.scheme_file("csc").read_bytes()
    assert schemes.label("csc") in task.decisions and schemes.label("csc") not in task.word_sources
    assert len(fake.requests) == 2
    replan = str(fake.requests[1]["messages"])
    assert schemes.label("csc") in replan and "CSC PageFormat scheme.docx" in replan
