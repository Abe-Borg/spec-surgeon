"""Composition: ops that overlap are ordered into steps by the engine, never into a question for the user
(the owner's "match the template header/footer and the font" case, 2026-10-20). A spec whose ops conflict at merge
time becomes a chain — each step planned on the step before it — and the window previews, approves and runs it as
one plan; a spec whose ops merge stays a spec; the model may also submit its own stages; a layout's spec composes
the same way."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from docx import Document

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build_sized
from spec_surgeon import compiler
from spec_surgeon.chain import validate_chain
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.editspec import (chain_specs, compose_chain, is_chain, load_spec, merge_cuts, plan_chain,
                                   split_into_stages, stage_cuts)
from spec_surgeon.gui import Session
from spec_surgeon.package import DocxPackage

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REQUEST = "Match the template's header and footer, and its font."


def font_spec():
    return load_spec(SPECS / "match-template-spec-font.json")


def hf_spec():
    return load_spec(SPECS / "match-template-spec-header-footer.json")


def combined_spec():
    """The one spec a model writes for the request: the font ops and the template op together."""
    font, hf = font_spec(), hf_spec()
    spec = {"request": REQUEST, "summary": "Match the template's header, footer and font", "mode": "formatting",
            "assumptions": ["The template's body text font is Arial 10 pt."],
            "ops": font["ops"] + hf["ops"], "assertions": font.get("assertions", [])}
    return spec


def _header_texts(path: Path):
    out = DocxPackage.from_path(path)
    return ["".join(t.text or "" for t in out.xml(p.filename).iter(W + "t"))
            for p in out.members if p.filename.startswith("word/header") and p.filename.endswith(".xml")]


def _normal_font(path: Path):
    out = DocxPackage.from_path(path)
    for st in out.xml("word/styles.xml").iter(W + "style"):
        if st.get(W + "styleId") == "Normal":
            rf = st.find(f".//{W}rFonts")
            return rf.get(W + "ascii") if rf is not None else None
    return None


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – WET-PIPE.docx").write_bytes(build_sized())
    (d / "21 13 16 – DRY-PIPE.docx").write_bytes(build_sized("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    return d


# ---- the engine ------------------------------------------------------------------------------------------------------

def test_overlapping_ops_are_cut_into_steps_in_the_users_order_and_each_step_plans_on_the_one_before():
    pkg = DocxPackage(build_sized(), name="x.docx")
    spec = combined_spec()
    assert stage_cuts(pkg, spec) == [3, 1]          # the three font ops merge; the template op touches what they read
    chain = split_into_stages(pkg, spec)
    validate_chain(chain)
    assert [[o["op"] for o in st["spec"]["ops"]] for st in chain["stages"]] == [
        ["set_font", "set_style_font", "set_doc_defaults_font"], ["apply_header_footer_template"]]
    assert chain["stages"][-1]["spec"]["assertions"] == spec["assertions"]      # final requirements ride the last step
    assert all("assertions" not in st["spec"] for st in chain["stages"][:-1])
    assert all(st["spec"]["assumptions"] == spec["assumptions"] for st in chain["stages"])
    assert all("geometry_reference" not in (st["spec"].get("review") or {}) for st in chain["stages"])
    plans = plan_chain(pkg, chain)
    assert [len(frags) for _plan, frags in plans] == [3, 1]
    reverse = {**spec, "ops": hf_spec()["ops"] + font_spec()["ops"], "assertions": []}
    assert stage_cuts(pkg, reverse) == [1, 3]       # the user's order is the user's: the template first, then the font


def test_a_spec_whose_ops_merge_is_not_split():
    pkg = DocxPackage(build_sized(), name="x.docx")
    assert stage_cuts(pkg, font_spec()) == [3]
    assert split_into_stages(pkg, font_spec()) is None
    assert chain_specs(font_spec()) == [font_spec()] and not is_chain(font_spec())


def test_merging_cuts_across_files_takes_every_cut_any_file_needs():
    assert merge_cuts([[3, 1], [1, 2, 1]]) == [1, 2, 1]
    assert merge_cuts([[4]]) == [4]
    assert merge_cuts([[2, 2], [4]]) == [2, 2]
    assert merge_cuts([]) == []
    chain = compose_chain(combined_spec(), [1, 2, 1])
    validate_chain(chain)
    assert [len(st["spec"]["ops"]) for st in chain["stages"]] == [1, 2, 1]
    with pytest.raises(ValueError):
        compose_chain(combined_spec(), [2, 1])


# ---- the compiler ----------------------------------------------------------------------------------------------------

def test_the_compiler_composes_a_conflicting_submission_into_a_chain_instead_of_rejecting_it(folder):
    batch = compiler._Batch(sorted(folder.glob("*.docx")), None)
    outcome, errs = compiler.compose_submission(combined_spec(), batch, [REQUEST])
    assert errs == [] and is_chain(outcome)
    assert len(outcome["stages"]) == 2
    assert compiler.check_submission(combined_spec(), batch, [REQUEST]) == []
    plain, errs = compiler.compose_submission(font_spec(), batch, [REQUEST])
    assert errs == [] and plain is not None and not is_chain(plain)


def test_the_model_may_submit_its_own_stages(folder):
    batch = compiler._Batch(sorted(folder.glob("*.docx")), None)
    font = font_spec()
    hf = hf_spec()
    submission = {"summary": "Match the template", "assumptions": ["Arial 10 pt"],
                  "stages": [{"name": "the font", "spec": font}, {"name": "the header and footer", "spec": hf}]}
    outcome, errs = compiler.compose_submission(submission, batch, [REQUEST])
    assert errs == [] and is_chain(outcome)
    assert outcome["description"] == "Match the template"
    assert [st["name"] for st in outcome["stages"]] == ["the font", "the header and footer"]
    bad = copy.deepcopy(submission)
    bad["stages"][1]["spec"]["ops"][0]["params"]["template"] = str(folder / "no such template.docx")
    _outcome, errs = compiler.compose_submission(bad, batch, [REQUEST])
    assert errs and "no such template" in " ".join(errs)


def test_the_prompt_no_longer_teaches_two_steps():
    prompt = compiler.system_prompt(chat=True)
    for phrase in ("offer a chain", "two specs", "separate specs", "second spec"):
        assert phrase not in prompt, phrase
    assert "composed by the app into ordered steps" in prompt
    assert "never tell the user to do anything in two steps" in prompt
    assert "suggested_replies" in json.dumps(compiler.CHAT_TOOLS)


# ---- the window: one request, one plan, one approval, verified copies -------------------------------------------------

def test_font_and_template_in_one_request_is_one_plan_one_approval_and_verified_copies(folder, tmp_path):
    fake = FakeClient(resp(text("I'll give both documents the template's header and footer and its Arial 10 pt font."),
                           tool("submit_editspec", {"spec": combined_spec(),
                                                    "suggested_replies": ["Also make the article titles bold"]})))
    session = Session(client=fake, out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False)
    task.attach(sorted(folder.glob("*.docx")))
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "preview", view["messages"]
    assert view["stages"] == 2
    assert len(fake.requests) == 1                                   # one model call: the engine composed the steps
    assert [c["title"] for g in view["groups"] for c in g["changes"]] == [
        "Change fonts", "Change style fonts", "Change the document's default font",
        "Match headers and footers to the template"]
    assert view["messages"][-1]["role"] == "assistant"
    assert view["messages"][-1]["text"].startswith("I'll give both documents")
    assert view["chips"] == ["Also make the article titles bold"]
    assert view["action"] == "Create edited copies"
    assert not any("two steps" in m["text"] or "step 2" in m["text"].lower() for m in view["messages"])
    task.perform("primary", token=view["token"])
    assert task.stage == "done", task.messages
    assert len(task.results) == 2
    for source, output in task.results.items():
        assert Path(output).is_file() and Path(output) != Path(source)
        assert any("SECTION 21 13" in h for h in _header_texts(Path(output)))   # the template's header, filled in
        assert _normal_font(Path(output)) == "Arial"                             # and the font
    assert (session.out_dir / "run" / "CHAIN_REPORT.md").is_file()


def test_a_request_the_user_answers_with_a_chip_sends_it_as_their_words(folder, tmp_path):
    fake = FakeClient(resp(tool("ask_user", {"message": "Should the headers change too?",
                                              "questions": ["Only the body text, or the headers too?"],
                                              "suggested_replies": ["Everything, including the headers", "Just the body"]})),
                      resp(tool("submit_editspec", {"spec": font_spec()})))
    session = Session(client=fake, out_root=tmp_path / "out", visual_check=False)
    task = DocumentTask(session, render=False)
    task.attach(sorted(folder.glob("*.docx")))
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "question"
    assert view["chips"] == ["Everything, including the headers", "Just the body"]
    assert view["choices"] == []
    view = task.perform("send", text=view["chips"][0])
    assert view["stage"] == "preview" and view["chips"] == []
    sent = fake.requests[1]["messages"][-1]["content"]
    assert any("Everything, including the headers" in json.dumps(block) for block in sent)
