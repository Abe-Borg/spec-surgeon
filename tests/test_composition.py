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


# ---- a layout composes the same way ----------------------------------------------------------------------------------

def test_a_layout_whose_ops_overlap_is_accepted_as_the_chain_the_engine_composed(tmp_path):
    """A mixed batch: the accepted submission carries the composed chain for the layout whose ops overlap (the plan
    the conversation builds from the submission is what the preview and the run execute), and the layout's pattern
    pin rides the first stage only (Codex review on PR #46)."""
    from make_fixtures import build_styles
    from spec_surgeon import layouts
    from spec_surgeon.fingerprint import fingerprint
    typed = tmp_path / "typed" / "21 13 13 – WET-PIPE.docx"
    styled = tmp_path / "styled" / "21 13 16 – DRY-PIPE.docx"
    typed.parent.mkdir(); styled.parent.mkdir()
    typed.write_bytes(build_sized())
    styled.write_bytes(build_styles("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    pid = lambda p: fingerprint(DocxPackage.from_path(p))["pattern_id"]  # noqa: E731
    assert pid(typed) != pid(styled)
    bold = {"run": {"b": True}, "tracked": False}
    submission = {"summary": "The template's header, footer and font; bold styled articles", "layouts": [
        {"id": "A", "label": "typed numbers", "pattern_ids": [pid(typed)], "spec": combined_spec()},
        {"id": "B", "label": "styled", "pattern_ids": [pid(styled)],
         "spec": {"summary": "bold by style", "mode": "formatting",
                  "ops": [{"op": "set_properties", "select": {"paragraphs": {"style_in": ["ArchPara"]}},
                           "params": bold}]}}]}
    batch = compiler._Batch([typed, styled], None)
    outcome, errs = compiler.compose_submission(copy.deepcopy(submission), batch, [REQUEST])
    assert errs == []
    assert is_chain(outcome["layouts"][0]["spec"]) and len(outcome["layouts"][0]["spec"]["stages"]) == 2
    assert not is_chain(outcome["layouts"][1]["spec"])
    assert compiler.check_submission(copy.deepcopy(submission), batch, [REQUEST]) == []
    # the plan the conversation builds from the accepted submission runs the chain, pinned on its first stage only
    plan = layouts.plan_from_submission(outcome, REQUEST)
    layouts.validate_layout_plan(plan)
    spec_a = layouts.layout_spec(plan, plan["layouts"][0])
    assert is_chain(spec_a)
    assert spec_a["stages"][0]["spec"]["applies_to"] == {"pattern_ids": [pid(typed)]}
    assert "applies_to" not in spec_a["stages"][1]["spec"]
    pv = layouts.make_layout_preview(plan, [str(typed), str(styled)], tmp_path / "preview", do_render=False)
    assert [layout["id"] for layout, _ in pv.previews] == ["A", "B"]
    assert len(pv.samples) == 2 and all(s.passed for s in pv.samples)


# ---- the review's probes (PR #46): the trial merge, the stage modes, in place -----------------------------------------

def test_the_trial_merge_coalesces_the_numbering_fonts_the_way_the_real_merge_does():
    """On a document with numbered lists the three font ops share one numbering label-font edit, which
    merge_fragments gives one owner before judging conflicts; the trial that decides the cuts must do the same, or
    the owner's headline request becomes three steps instead of two on every real section."""
    from make_fixtures import build_numbering
    pkg = DocxPackage(build_numbering(), name="numbered.docx")
    assert stage_cuts(pkg, combined_spec()) == [3, 1]
    assert stage_cuts(pkg, font_spec()) == [3]


def test_a_composed_stage_declares_the_mode_its_ops_plan_not_the_mode_of_their_class(folder):
    """A template op with a literal fill plans untracked (it places the user's words); the stage that carries it
    must say so, or plan_spec refuses the step the app itself composed."""
    spec = combined_spec()
    hf = next(o for o in spec["ops"] if o["op"] == "apply_header_footer_template")
    hf["params"]["slot_fills"]["SECTION_TITLE"] = {"rule": "literal", "value": "WET-PIPE SPRINKLER SYSTEMS",
                                                   "in_template": "DOOR HARDWARE"}
    pkg = DocxPackage(build_sized(), name="x.docx")
    chain = split_into_stages(pkg, spec)
    assert chain is not None and [st["spec"]["mode"] for st in chain["stages"]] == ["formatting", "untracked"]
    plans = plan_chain(pkg, chain)                       # every step accepted, the second as untracked
    assert len(plans) == 2
    batch = compiler._Batch(sorted(folder.glob("*.docx")), None)
    outcome, errs = compiler.compose_submission(spec, batch, [REQUEST, "WET-PIPE SPRINKLER SYSTEMS"])
    assert errs == [] and is_chain(outcome)
    assert [st["spec"]["mode"] for st in outcome["stages"]] == ["formatting", "untracked"]


def test_a_composed_chain_is_never_run_in_place(folder, tmp_path, capsys):
    """Written back step by step, a file that fails the last step's final assertion would keep the earlier steps —
    where the one request the user approved would have withheld it whole. The command line refuses --in-place for
    the steps the app composed, before anything is previewed or written."""
    from types import SimpleNamespace
    from spec_surgeon import cli
    pkg = DocxPackage(build_sized(), name="x.docx")
    chain = split_into_stages(pkg, combined_spec())
    args = SimpleNamespace(in_place=True, no_render=True, renderer=None, yes=True, workers=1)
    code = cli._preview_and_run_chain(args, chain, sorted(folder.glob("*.docx")), tmp_path / "out")
    assert code == 2
    assert "--in-place" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_a_submission_that_is_both_steps_and_a_question_is_refused_not_half_run(folder, tmp_path):
    """Stages beside an unsupported outcome (or questions): the plan branch would win the turn and the preview would
    crash on the unknown chain keys, with the model's explanation never shown. Refused to the model, which answers
    with one outcome; the conversation then shows it."""
    batch = compiler._Batch(sorted(folder.glob("*.docx")), None)
    both = {"summary": "the font", "stages": [{"name": "font", "spec": font_spec()}],
            "unsupported": "The watermark cannot be matched.", "proposal": "none"}
    outcome, errs = compiler.compose_submission(copy.deepcopy(both), batch, [REQUEST])
    assert errs and "never both" in errs[0]
    layouts_and_question = {"summary": "x", "layouts": [], "questions": ["Which font?"]}
    assert "never both" in compiler.compose_submission(layouts_and_question, batch, [REQUEST])[1][0]
    fake = FakeClient(resp(tool("submit_editspec", {"spec": both})),
                      resp(tool("submit_editspec", {"spec": {"summary": "x", "unsupported": both["unsupported"],
                                                             "proposal": "none"}})))
    task = DocumentTask(Session(client=fake, out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([str(p) for p in sorted(folder.glob("*.docx"))])
    view = task.perform("send", text=REQUEST)
    assert view["stage"] == "question" and len(fake.requests) >= 2
    assert "never both" in repr(fake.requests[1]["messages"])          # the rejection reached the model
    # (what follows an unsupported outcome — the app's own investigation — is scripted elsewhere; here the turn
    # ended in a question to the user, never in a crash at the preview)


def test_a_source_bound_template_op_is_never_reordered_only_refused_when_not_first(tmp_path):
    """Bindings are read from the file as attached and run in the first step; the app does not move the template op
    ahead of the ops the model listed before it (an order the engine chose is an order nobody approved): it refuses
    with the way out, and accepts the model's own listing with the bindings on the first step alone."""
    import hashlib
    from make_fixtures import build_template
    from spec_surgeon.ops.base import JobException
    from test_live_template_bindings import TEMPLATE_FILLS, template_params
    source = tmp_path / "21 13 13 – WET-PIPE.docx"
    source.write_bytes(build_sized())
    template = tmp_path / "template.docx"
    template.write_bytes(build_template())
    fills = copy.deepcopy(TEMPLATE_FILLS)
    del fills["SECTION_TITLE"]
    hf = {"op": "apply_header_footer_template", "params": {**template_params(template), "slot_fills": fills}}
    title = Document(str(source)).paragraphs[1].text            # the bound literal must occur in its paragraph
    assert title
    bindings = [{"file": str(source.resolve()), "md5": hashlib.md5(source.read_bytes()).hexdigest(),
                 "slots": {"SECTION_TITLE": {"value": title, "part": "word/document.xml", "paragraph": 1}}}]
    listed_last = {"request": REQUEST, "summary": "font, then the template", "mode": "untracked", "bindings": bindings,
                   "ops": font_spec()["ops"] + [hf]}
    pkg = DocxPackage.from_path(source)
    with pytest.raises(JobException) as e:
        stage_cuts(pkg, listed_last)
    assert e.value.code == "bindings-order" and "listed first" in e.value.reason
    batch = compiler._Batch([source], template)
    words = [REQUEST, title] + [r["value"] for r in fills.values() if r.get("rule") == "literal"]
    _outcome, errs = compiler.compose_submission(copy.deepcopy(listed_last), batch, words)
    assert errs and any("listed first" in err for err in errs)
    listed_first = {**listed_last, "ops": [hf] + font_spec()["ops"]}
    chain = split_into_stages(pkg, listed_first)
    assert chain is not None and [[o["op"] for o in st["spec"]["ops"]] for st in chain["stages"]][0] == \
        ["apply_header_footer_template"]
    assert "bindings" in chain["stages"][0]["spec"] and all("bindings" not in st["spec"] for st in chain["stages"][1:])
    validate_chain(chain)
