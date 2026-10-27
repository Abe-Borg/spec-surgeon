"""A chain's every step is planned on every file in the preview (backlog Q2). The later steps were planned on the
samples alone, so a later step that could not plan a file that was not a sample was found at the run: the file
stopped at that step and went to recovery or the to-do list. Now ``planner.plan_batch_chain`` plans each later step
on the bytes the step before it would make, for every file, and such a file is an exception of the batch plan with
its step named — under Left untouched before anything runs, as a first-step refusal always was."""
from __future__ import annotations

import copy
from pathlib import Path

from make_fixtures import build, build_sized
from spec_surgeon.planner import plan_batch_chain, plan_file_chain
from spec_surgeon.preview import make_chain_preview, refusals

REMOVE = {"request": "remove the headers and footers", "summary": "Remove every header and footer.",
          "mode": "formatting", "ops": [{"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}}]}
RENAME = {"request": "FM Global to the insurer", "summary": "Call FM Global the insurer.", "mode": "content",
          "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                   "params": {"find": "FM Global", "replace": "the insurer", "tracked": True}}]}


def chain(*specs, sample_files=None):
    stages = [{"name": f"step {i}", "spec": copy.deepcopy(s)} for i, s in enumerate(specs, 1)]
    if sample_files is not None:
        stages[0]["spec"]["review"] = {"sample_files": sample_files}
    return {"name": "two steps", "stages": stages}


def folder(tmp_path) -> Path:
    """Two specifications that say FM Global and one that does not (the sized fixture): the rename refuses it."""
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – WET-PIPE.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (d / "21 13 16 – DRY-PIPE.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    (d / "21 13 19 – PREACTION.docx").write_bytes(build_sized("21 13 19", "PREACTION SPRINKLER SYSTEMS"))
    return d


def test_every_step_is_planned_on_every_file_and_a_later_step_refusal_names_its_step(tmp_path):
    bp = plan_batch_chain([str(folder(tmp_path))], chain(REMOVE, RENAME))
    assert len(bp.files) == 3 and [f.status for f in bp.files] == ["planned", "planned", "exception"]
    refused = bp.exceptions[0]
    assert refused.source.name.startswith("21 13 19") and refused.exception["code"] == "not-found"
    assert refused.exception["chain_stage"] == 2 and refused.exception["reason"].startswith("step 2: ")
    assert all(f.summary for f in bp.files if f.status == "planned")      # step 1's summary, as the text shows it
    assert "EXCEPTION 21 13 19 – PREACTION.docx: [not-found] step 2: " in bp.to_text()


def test_a_file_a_later_step_refuses_waits_under_left_untouched_even_when_it_is_not_a_sample(tmp_path):
    pv = make_chain_preview(chain(REMOVE, RENAME, sample_files=1), [str(folder(tmp_path))], tmp_path / "pv",
                            renderer="none", do_render=False)
    assert [s.source.name for s in pv.samples] == ["21 13 13 – WET-PIPE.docx"]       # one sample, the first file
    assert [Path(p).name for p, _ in refusals(pv)] == ["21 13 19 – PREACTION.docx"]  # refused before the run
    [(_, exc)] = refusals(pv)
    assert exc["chain_stage"] == 2 and exc["code"] == "not-found"
    text = pv.to_text()
    assert "EVERY STEP planned on every file" in text and "on the sample files only" not in text
    assert not any("sample documents only" in w for w in pv.warnings)


def test_a_first_step_refusal_reads_as_before(tmp_path):
    d = folder(tmp_path)
    fp = plan_file_chain(d / "21 13 19 – PREACTION.docx", chain(RENAME, REMOVE))
    assert fp.status == "exception" and fp.exception["code"] == "not-found"
    assert fp.exception["chain_stage"] == 1 and fp.exception["reason"].startswith("step 1: ")


def test_the_later_steps_notes_about_wording_left_elsewhere_cover_every_file(tmp_path):
    d = tmp_path / "specs"
    d.mkdir()
    (d / "a.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (d / "b.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    kept = copy.deepcopy(RENAME)
    kept["ops"][0]["select"] = {"scope": ["document"], "paragraphs": {"text_regex": "^A\\."}}   # one place only
    bp = plan_batch_chain([str(d)], chain(REMOVE, kept))
    assert all(f.status == "planned" for f in bp.files)
    assert all(any(note.startswith("after step 2: ") for note in f.later_notes) for f in bp.files)
    assert {name for name, _ in bp.left_elsewhere()} == {"a.docx", "b.docx"}
