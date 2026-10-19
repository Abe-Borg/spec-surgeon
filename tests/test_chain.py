"""Chains (chain.py, 2026-10-16): several specs run one after another, each stage a full verified run on the stage
before it — the pin between stages, a file going as far as it passes, templates of any stage skipped, the order
flagged where a later stage changes a part an earlier one added; the chain preview and its approval token over every
stage; the library's chain kind under the one-name rule; the window's Session, the window itself and the command
line."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from make_fixtures import build, build_sized
from spec_surgeon import batch as batch_module
from spec_surgeon import library
from spec_surgeon.batch import load_set_aside
from spec_surgeon.chain import (ChainError, chain_from_specs, chain_intent, chain_key, chain_templates, load_chain_file,
                                run_chain, validate_chain)
from spec_surgeon.cli import main as cli_main
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session
from spec_surgeon.library import LibraryError
from spec_surgeon.package import DocxPackage
from spec_surgeon.preview import approval_token, changed_since_preview, key_templates, make_chain_preview
from spec_surgeon.ui.rows import ProgressTracker, chain_rows, result_counts

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
FONT_SPEC = SPECS / "match-template-spec-font.json"
HF_SPEC = SPECS / "match-template-spec-header-footer.json"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _font():
    return load_spec(FONT_SPEC)


def _hf():
    return load_spec(HF_SPEC)


def _remove():
    return load_spec(SPECS / "remove-all-headers-footers.json")


def _refuse():
    """A stage that refuses every file (the phrase is nowhere): a file stops there."""
    return {"request": "replace a phrase that is nowhere", "summary": "x", "mode": "content", "applies_to": {"or": "all"},
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "NO SUCH PHRASE ANYWHERE", "replace": "X", "tracked": True}}]}


def _courier():
    """A font change over the headers and footers too — on the template's new parts when it runs after the templating
    stage."""
    return {"request": "all fonts Courier New", "summary": "x", "mode": "formatting", "applies_to": {"or": "all"}, "ops": [
        {"op": "set_font", "select": {"scope": ["document", "headers", "footers"], "runs": "all"},
         "params": {"font": "Courier New"}},
        {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": "Courier New"}},
        {"op": "set_doc_defaults_font", "params": {"font": "Courier New"}}],
        "assertions": [{"kind": "effective_font", "value": "Courier New", "scope": ["document", "headers", "footers"]}]}


def _template_chain(name="Match the template spec"):
    return chain_from_specs([("Match the font", _font(), "Match the font"), ("Match the header/footer", _hf())], name=name)


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "21 13 13 – WET-PIPE.docx").write_bytes(build_sized())
    (d / "21 13 16 – DRY-PIPE.docx").write_bytes(build_sized("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    tpl = Path(_hf()["ops"][0]["params"]["template"])
    shutil.copy(tpl, d / "08 71 00 – DOOR HARDWARE (architect).docx")      # stage 2's template, in the folder
    return d


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


# ---- the run ----------------------------------------------------------------------------------------------------------

def test_a_chain_runs_each_stage_on_the_stage_before_it_and_delivers_only_files_that_passed_every_stage(folder, tmp_path):
    lines = []
    r = run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False,
                  progress=lines.append)
    assert r.counts() == {"PASS": 2, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0} and r.name == "Match the template spec"
    assert [Path(x).name for x in r.skipped] == ["08 71 00 – DOOR HARDWARE (architect).docx"]   # any stage's template
    assert [s.out_dir.name for s in r.stages] == ["stage-01", "stage-02"]
    for s in r.stages:
        assert (s.out_dir / "BATCH_REPORT.md").is_file() and (s.out_dir / "logs" / "run.log").is_file()
        assert s.batch.counts()["PASS"] == 2
    # stage 2 read exactly stage 1's verified copies, and the chain's outputs/ holds the last stage's verified bytes
    assert [p.parent.name for p in r.stages[1].inputs] == ["outputs", "outputs"]
    assert all(p.parent.parent.name == "stage-01" for p in r.stages[1].inputs)
    for f in r.files:
        assert f.status == "PASS" and f.stage_status == ["PASS", "PASS"] and f.stopped_at is None and not f.flags
        final = Path(f.delivered_to)
        assert final.parent == tmp_path / "run" / "outputs" and final.read_bytes() == Path(f.results[-1].output).read_bytes()
        assert any("SECTION 21 13 1" in t and "SPRINKLER SYSTEMS" in t for t in _header_texts(final))   # stage 2
        assert _normal_font(final) == "Arial"                                                            # stage 1
    assert sorted(p.name for p in (tmp_path / "run" / "outputs").iterdir()) == sorted(f.name for f in folder.glob("21*.docx"))
    assert {f.read_bytes() for f in folder.glob("*.docx")} and all(f.is_file() for f in folder.glob("*.docx"))
    # the records: the chain's own report, log and json beside the stages
    report = (tmp_path / "run" / "CHAIN_REPORT.md").read_text(encoding="utf-8")
    assert "1. **Match the font**" in report and "2. **Match the header/footer**" in report and "| PASS | PASS |" in report
    data = json.loads((tmp_path / "run" / "chain.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in data["stages"]] == ["Match the font", "Match the header/footer"]
    assert all(f["stages"] == ["PASS", "PASS"] and f["stopped_at"] is None for f in data["files"])
    assert json.loads((tmp_path / "run" / "set_aside.json").read_text(encoding="utf-8"))["files"] == []
    assert "No problems" in (tmp_path / "run" / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert lines[0].startswith("stage 1 of 2 — Match the font: 2 file(s)") and "stage 2 of 2 — Match the header/footer: 2 file(s)" in lines
    assert lines.count("  done 21 13 13 – WET-PIPE.docx") == 2                 # once per stage


def test_a_file_that_stops_at_a_stage_goes_no_further_and_is_set_aside_with_the_stage(folder, tmp_path):
    chain = chain_from_specs([("strip", _remove()), ("refuse", _refuse()), ("hf", _hf())])
    r = run_chain([str(folder)], chain, tmp_path / "run", workers=1, renderer="none", do_render=False)
    assert r.counts()["EXCEPTION"] == 2 and r.counts()["PASS"] == 0
    for f in r.files:
        assert f.status == "EXCEPTION" and f.stopped_at == 2 and f.code == "not-found"
        assert f.stage_status == ["PASS", "EXCEPTION", "NOT_RUN"] and f.results[2] is None
        assert f.reason.startswith("stage 2 (refuse): ") and f.delivered_to is None
    assert not (tmp_path / "run" / "outputs").exists()                           # nothing passed every stage
    assert r.stages[2].batch is None and r.stages[2].inputs == []               # stage 3 had nothing to run
    # the set-aside manifest names the ORIGINAL files and the stage; the loaders read it like a batch's
    manifest = load_set_aside(tmp_path / "run")
    assert [Path(x["source"]).parent for x in manifest["files"]] == [folder, folder]
    assert all(x["stage"] == "chain stage 2" and x["details"]["chain_stage"] == 2 and x["code"] == "not-found"
               and x["reason"].startswith("stopped at stage 2 of 3") and x["details"]["stage_name"] == "refuse"
               for x in manifest["files"])
    diag = (tmp_path / "run" / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert "stopped at stage 2 of 3 (refuse)" in diag and "stage-02/" in diag and "NO SUCH PHRASE ANYWHERE" in diag
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.last_run_dir = tmp_path / "run"
    text = s.load_set_aside()
    assert "Loaded 2 set-aside file(s)" in text and "[not-found]" in text and len(s.selected_files) == 2


def test_the_pin_between_stages_refuses_a_file_changed_between_them(folder, tmp_path, monkeypatch):
    real = batch_module.run_batch
    victim = "21 13 13 – WET-PIPE.docx"

    def tampering(inputs, *a, **k):
        res = real(inputs, *a, **k)
        for fr in res.files:                       # stage 1's verified copy is edited before stage 2 reads it
            if fr.source.name == victim and fr.output and fr.output.parent.parent.name == "stage-01":
                fr.output.write_bytes(fr.output.read_bytes() + b"\0")
        return res
    monkeypatch.setattr(batch_module, "run_batch", tampering)
    r = run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False)
    by = {f.source.name: f for f in r.files}
    bad, good = by[victim], by["21 13 16 – DRY-PIPE.docx"]
    assert bad.status == "EXCEPTION" and bad.code == "chain-drift" and bad.stopped_at == 2
    assert bad.stage_status == ["PASS", "DRIFTED"] and "changed between stage 1 and stage 2" in bad.reason
    assert good.status == "PASS" and good.stage_status == ["PASS", "PASS"]
    assert [p.name for p in (tmp_path / "run" / "outputs").iterdir()] == [good.source.name]
    assert [p.name for p in r.stages[1].inputs] == [good.source.name]          # stage 2 never read the changed file
    assert load_set_aside(tmp_path / "run")["files"][0]["category"] == "Changed Between Two Stages of the Chain"


def test_a_later_stage_that_changes_a_part_an_earlier_stage_added_is_flagged(folder, tmp_path):
    chain = chain_from_specs([("hf", _hf()), ("courier", _courier())])
    r = run_chain([str(folder)], chain, tmp_path / "run", workers=1, renderer="none", do_render=False)
    assert r.counts()["PASS"] == 2
    for f in r.files:
        assert len(f.flags) == 1 and "stage 2 (courier) changes word/footer2.xml, word/header2.xml" in f.flags[0]
        assert "(hf) added from its template" in f.flags[0]
    report = (tmp_path / "run" / "CHAIN_REPORT.md").read_text(encoding="utf-8")
    assert "## Flagged for review (the order of the stages)" in report and "in a chain the order is yours" in report
    # the other order has nothing to flag: the template's parts come last
    r2 = run_chain([str(folder)], chain_from_specs([("courier", _courier()), ("hf", _hf())]), tmp_path / "run2",
                   workers=1, renderer="none", do_render=False)
    assert r2.counts()["PASS"] == 2 and not any(f.flags for f in r2.files)


def test_in_place_writes_back_stage_by_stage_and_a_stopped_file_keeps_the_stages_before(folder, tmp_path):
    before = {f.name: f.read_bytes() for f in folder.glob("21*.docx")}
    r = run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False,
                  in_place=True)
    assert r.counts()["PASS"] == 2 and not (tmp_path / "run" / "outputs").exists()
    for f in r.files:
        assert f.delivered_to == str(f.source) and f.source.read_bytes() == Path(f.results[-1].output).read_bytes()
        assert f.source.read_bytes() != before[f.source.name] and _normal_font(f.source) == "Arial"
        assert [p for p in r.stages[1].inputs] == [cf.source for cf in r.files]   # in place: the sources themselves
    # a file that stops at stage 2 in place keeps stage 1 (the headers are gone) and the reason says so
    d2 = tmp_path / "specs2"
    shutil.copytree(folder, d2)
    chain = chain_from_specs([("strip", _remove()), ("refuse", _refuse())])
    r2 = run_chain([str(d2)], chain, tmp_path / "run2", workers=1, renderer="none", do_render=False, in_place=True)
    for f in r2.files:
        assert f.status == "EXCEPTION" and f.stopped_at == 2 and f.stage_status == ["PASS", "EXCEPTION"]
        assert "stages 1…1 were written back over the file; stage 2 and later were not applied" in f.reason
        assert _header_texts(f.source) == []                                     # stage 1 was written back
    assert "written back" in (tmp_path / "run2" / "CHAIN_REPORT.md").read_text(encoding="utf-8")


def test_a_chain_never_reads_where_it_writes_and_refuses_a_bad_chain_before_anything_is_made(folder, tmp_path):
    run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False)
    with pytest.raises(ValueError, match="lie inside the results folder"):
        run_chain([str(tmp_path / "run" / "outputs")], _template_chain(), tmp_path / "run", workers=1,
                  renderer="none", do_render=False)
    with pytest.raises(ChainError) as e:
        run_chain([str(folder)], {"stages": []}, tmp_path / "bad", workers=1, renderer="none", do_render=False)
    assert e.value.code == "chain" and not (tmp_path / "bad").exists()
    for bad, text in [({}, "needs 'stages'"), ({"stages": [{"spec": {}}]}, "stage 1"), ({"stages": "x"}, "non-empty list"),
                      ({"stages": [{"spec": _font(), "extra": 1}]}, "unknown key"),
                      ({"stages": [{"spec": dict(_font(), questions=["which?"])}]}, "[questions]"),
                      ({"stages": [_font()]}, "unknown key"), ({"name": 3, "stages": [{"spec": _font()}]}, "must be text"),
                      ([], "JSON object")]:
        with pytest.raises(ChainError, match=text):
            validate_chain(bad)
    # the helpers: templates of every stage, the key the token hashes, what the visual check is told
    chain = _template_chain()
    assert chain_templates(chain) == [Path(_hf()["ops"][0]["params"]["template"]).resolve()]
    assert [st["name"] for st in chain_key(chain)["stages"]] == ["Match the font", "Match the header/footer"]
    assert key_templates(chain_key(chain)) == chain_templates(chain)
    assert chain_intent(chain).startswith("A CHAIN «Match the template spec» of 2 stage(s)") and "STAGE 2 — Match the header/footer" in chain_intent(chain)
    # a chain file round-trips (its stages' relative templates resolve against the file)
    (tmp_path / "c.json").write_text(json.dumps(chain), encoding="utf-8")
    assert load_chain_file(tmp_path / "c.json")["stages"][1]["spec"] == _hf()


def test_a_reused_results_folder_keeps_nothing_of_an_earlier_run(folder, tmp_path):
    """Codex on #23: a file that passed last time and is absent or stops this time must not survive in outputs/."""
    r1 = run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False)
    assert r1.counts()["PASS"] == 2 and len(list((tmp_path / "run" / "outputs").iterdir())) == 2
    (tmp_path / "run" / "stage-01" / "failed").mkdir()
    (tmp_path / "run" / "stage-01" / "failed" / "old.docx").write_bytes(b"stale")
    gone = sorted(folder.glob("21*.docx"))[1]
    gone.unlink()                                             # one file is no longer in the batch
    r2 = run_chain([str(folder)], _template_chain(), tmp_path / "run", workers=1, renderer="none", do_render=False)
    assert [f.source.name for f in r2.files] == ["21 13 13 – WET-PIPE.docx"] and r2.counts()["PASS"] == 1
    for sub in ("outputs", "stage-01/outputs", "stage-02/outputs"):
        assert [p.name for p in (tmp_path / "run" / sub).iterdir()] == ["21 13 13 – WET-PIPE.docx"], sub
    assert not (tmp_path / "run" / "stage-01" / "failed").exists()
    # the same folder again, now with a chain every file stops in: outputs/ is empty, not last run's copies
    r3 = run_chain([str(folder)], chain_from_specs([("strip", _remove()), ("refuse", _refuse())]), tmp_path / "run",
                   workers=1, renderer="none", do_render=False)
    assert r3.counts()["PASS"] == 0 and not (tmp_path / "run" / "outputs").exists()


def test_a_runs_chain_json_loads_back_as_the_chain(folder, tmp_path):
    """Codex on #23: a run's chain.json wraps the chain beside the stages' results; the loader must unwrap it."""
    chain = _template_chain()
    run_chain([str(folder)], chain, tmp_path / "run", workers=1, renderer="none", do_render=False)
    data = json.loads((tmp_path / "run" / "chain.json").read_text(encoding="utf-8"))
    assert "chain" in data and "stages" in data and "files" in data          # the wrapper, with the results
    assert load_chain_file(tmp_path / "run" / "chain.json") == chain
    pv = make_chain_preview(chain, [str(folder)], tmp_path / "preview", renderer="none", do_render=False)
    assert load_chain_file(tmp_path / "preview" / "chain.json") == chain      # the preview's: the chain itself
    s = Session(folder=folder, out_root=tmp_path / "out")
    assert s.load_chain_file(tmp_path / "run" / "chain.json") == chain and s.chain_source == str(tmp_path / "run" / "chain.json")
    (tmp_path / "x.json").write_text(json.dumps({"counts": {}, "files": []}), encoding="utf-8")
    with pytest.raises(ChainError, match="unknown chain key"):                  # neither shape: refused, not misread
        load_chain_file(tmp_path / "x.json")


# ---- the preview and its token ---------------------------------------------------------------------------------------

def test_the_chain_preview_runs_every_stage_on_the_samples_and_its_token_covers_every_stage(folder, tmp_path):
    tpl = tmp_path / "tpl" / "08 71 00 - DOOR HARDWARE (ACME).docx"
    tpl.parent.mkdir()
    shutil.copy(_hf()["ops"][0]["params"]["template"], tpl)
    hf = _hf()
    hf["ops"][0]["params"]["template"] = str(tpl)
    chain = chain_from_specs([("font", _font()), ("hf", hf)], name="Match")
    pv = make_chain_preview(chain, [str(folder)], tmp_path / "preview", renderer="none", do_render=False)
    text = pv.to_text()
    assert text.startswith("PREVIEW — chain «Match»: 2 stage(s)") and "stage 1 — font:" in text and "stage 2 — hf:" in text
    assert len(pv.batch_plan.files) == 2 and all(f.status == "planned" for f in pv.batch_plan.files)   # stage 1, every file
    assert [s.source.name for s in pv.samples] == ["21 13 13 – WET-PIPE.docx", "21 13 16 – DRY-PIPE.docx"]
    for s in pv.samples:
        assert s.passed and [st["stage"] for st in s.stages] == [1, 2] and all(st["passed"] for st in s.stages)
        assert s.stages_text() == "stage 1 PASS · stage 2 PASS" and s.output.is_file()
        assert _normal_font(s.output) == "Arial" and any("SECTION 21 13 1" in t for t in _header_texts(s.output))
    assert "stage 1 PASS · stage 2 PASS → verification PASS" in text and f"approval token: {pv.token}" in text
    assert json.loads((tmp_path / "preview" / "chain.json").read_text(encoding="utf-8")) == chain
    assert json.loads((tmp_path / "preview" / "preview.json").read_text(encoding="utf-8"))["samples"][0]["stages"][1]["name"] == "hf"
    files = [f.source for f in pv.batch_plan.files]
    assert changed_since_preview(chain_key(chain), files, pv.token, pv.token_inputs) is None
    # stage 2's template edited after the preview voids the approval (rule 12 — every stage's template counts)
    os.utime(tpl, ns=(tpl.stat().st_atime_ns, tpl.stat().st_mtime_ns + 10_000_000))
    assert changed_since_preview(chain_key(chain), files, pv.token, pv.token_inputs) == [
        f"template {tpl.name}: changed since the preview"]
    # a stage's spec edited changes the key, so the token
    other = json.loads(json.dumps(chain))
    other["stages"][0]["spec"]["params" if False else "summary"] = "something else"
    assert approval_token(chain_key(other), files) != approval_token(chain_key(chain), files)
    # a sample that stops at a stage says where, and the preview goes on
    chain2 = chain_from_specs([("strip", _remove()), ("refuse", _refuse())])
    pv2 = make_chain_preview(chain2, [str(folder)], tmp_path / "preview2", renderer="none", do_render=False)
    for s in pv2.samples:
        assert s.exception["code"] == "not-found" and s.exception["chain_stage"] == 2
        assert s.exception["reason"].startswith("stage 2 (refuse): ") and s.stages_text() == "stage 1 PASS · stage 2 EXCEPTION [not-found]"
        assert s.output.is_file() and _header_texts(s.output) == []           # the file as stage 1 left it
    assert "stage 1 PASS · stage 2 EXCEPTION [not-found] — EXCEPTION [not-found] stage 2 (refuse)" in pv2.to_text()


# ---- the library -----------------------------------------------------------------------------------------------------

def test_the_library_keeps_chains_under_the_one_name_rule(folder, tmp_path):
    chain = _template_chain()
    chain["stages"][0]["spec"]["applies_to"] = {"pattern_ids": ["abc"]}
    item, notes = library.save_chain("Template chain", chain, "font, then header/footer")
    assert item.kind == "chain" and item.label() == "Chain: Template chain" and item.path.name == "template-chain.json"
    assert item.request == "Match the font → Match the header/footer" and item.description == "font, then header/footer"
    assert notes == ["stage 1 (Match the font): the spec was pinned to this batch's structure patterns; the pin was "
                     "dropped so stage 1 (Match the font) can run on other projects' files (it is still planned file by file)"]
    assert (item.path.with_name("template-chain.meta.json")).is_file()
    assert [i.label() for i in library.list_items()] == ["Chain: Template chain"]
    loaded = library.load_chain("template CHAIN")
    assert [st["name"] for st in loaded["stages"]] == ["Match the font", "Match the header/footer"]
    assert loaded["stages"][0]["recipe"] == "Match the font" and "recipe" not in loaded["stages"][1]
    assert "applies_to" not in loaded["stages"][0]["spec"] and loaded["stages"][1]["spec"] == _hf()
    assert loaded["description"] == "font, then header/footer" and loaded["name"] == "Template chain"
    # one name, one item: a recipe or an operation cannot take it, not even with replace; nor the reverse
    with pytest.raises(LibraryError, match="a recipe cannot take that name, not even with replace"):
        library.save_recipe("template chain", _font(), replace=True)
    library.save_recipe("Match the font", _font())
    with pytest.raises(LibraryError, match="a chain cannot take that name"):
        library.save_chain("match the FONT", chain, replace=True)
    with pytest.raises(LibraryError, match="already has a chain named 'Template chain' — replace it"):
        library.save_chain("Template chain", chain)
    library.save_chain("Template chain", chain, replace=True)
    assert sorted(i.label() for i in library.list_items()) == ["Chain: Template chain", "Recipe: Match the font"]
    h = library.item_hash(item)
    item.path.write_text(item.path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert library.item_hash(item) != h
    with pytest.raises(LibraryError, match="there is no chain named 'nope'"):
        library.load_chain("nope")
    assert library.delete("template chain").kind == "chain" and [i.kind for i in library.list_items()] == ["recipe"]
    # the compiler never offers a chain: it is not in Claude's index, and read_library_item says so
    from spec_surgeon.compiler import library_index, library_item_detail
    library.save_chain("Template chain", chain)
    assert [e["kind"] for e in library_index()["items"]] == ["recipe"]
    detail = library_item_detail("Template chain")
    assert detail["kind"] == "chain" and [s["name"] for s in detail["stages"]] == ["Match the font", "Match the header/footer"]
    assert "cannot be offered as a choice" in detail["note"]


# ---- the window's logic ----------------------------------------------------------------------------------------------

def test_the_session_builds_a_chain_from_recipes_previews_runs_and_saves_it(folder, tmp_path):
    library.save_recipe("Font", _font())
    library.save_recipe("HF", _hf(), "the architect's header and footer")
    s = Session(folder=folder, out_root=tmp_path / "out")
    with pytest.raises(LibraryError, match="there is no recipe named 'Nope'"):
        s.build_chain(["Font", "Nope"])
    assert s.chain is None
    chain = s.build_chain(["Font", "HF"])
    assert s.chain is chain and s.chain_source == "recipes" and s.spec is None and s.chain_label() == "Font → HF"
    assert [st["recipe"] for st in chain["stages"]] == ["Font", "HF"]
    assert len(s.files()) == 2                                                 # the template copy is left out
    with pytest.raises(ValueError, match="preview the chain first"):
        s.save_chain("x")
    with pytest.raises(ValueError, match="Preview first"):
        s.run(False, False, lambda m: None)
    text = s.make_preview(render=False, log=lambda m: None)
    assert "PREVIEW — chain «Font → HF»: 2 stage(s)" in text and "stage 1 PASS · stage 2 PASS" in text
    assert "A CHAIN «Font → HF» of 2 stage(s)" in s._intent()
    item, notes = s.save_chain("My chain", "font then header/footer")
    assert item.kind == "chain" and library.load_chain("my chain")["stages"][1]["recipe"] == "HF"
    out = s.run(in_place=False, render=False, log=lambda m: None)
    assert "PASS 2 (every stage)" in out and "your files were not changed" in out and "CHAIN_REPORT.md" in out
    assert s.last_chain is not None and s.last_batch is None and (s.last_run_dir / "outputs").is_dir()
    assert sorted(p.name for p in (s.last_run_dir / "outputs").iterdir()) == sorted(f.name for f in folder.glob("21*.docx"))
    # a file changed since the preview: refused, as for a spec
    f = sorted(folder.glob("21*.docx"))[0]
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    with pytest.raises(ValueError, match="a file or the chain changed since the preview"):
        s.run(False, False, lambda m: None)
    # a saved chain from Your library, and Remove
    s.remove_spec()
    assert s.chain is None and s.preview is None
    s.load_chain("My chain")
    assert s.chain_source == "library:My chain" and s.chain_label() == "My chain"
    assert "stage 1 PASS · stage 2 PASS" in s.make_preview(render=False, log=lambda m: None)
    assert "PASS 2 (every stage)" in s.run(in_place=False, render=False, log=lambda m: None)
    # a spec loaded afterwards replaces the chain (one outcome at a time)
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    assert s.chain is None and s.spec is not None
    with pytest.raises(ValueError, match="not two of them"):
        s._set_outcome(spec=_font(), chain=chain)


def test_the_rows_and_the_progress_bar_follow_a_chain(folder, tmp_path):
    lines = []
    r = run_chain([str(folder)], chain_from_specs([("strip", _remove()), ("refuse", _refuse())]), tmp_path / "run",
                  workers=1, renderer="none", do_render=False, progress=lines.append)
    rows = chain_rows(r)                           # three files: the architect's copy is no template of THIS chain
    assert [x.badge for x in rows] == ["exception"] * 3 and result_counts(rows)["EXCEPTION"] == 3
    wet = next(x for x in rows if x.cells[0] == "21 13 13 – WET-PIPE.docx")
    assert wet.cells[1] == "Exception" and wet.cells[2].startswith("Stopped at stage 2 of 2 (refuse) — ")
    assert wet.diagnosis.startswith("# 21 13 13 – WET-PIPE.docx — stopped at stage 2 of 2 (refuse)")
    assert "NO SUCH PHRASE ANYWHERE" in wet.diagnosis
    r2 = run_chain([str(folder)], _template_chain(), tmp_path / "run2", workers=1, renderer="none", do_render=False)
    rows = chain_rows(r2)
    assert [x.badge for x in rows] == ["pass", "pass"] and rows[0].cells[2] == "Verified at every stage (2)"
    assert rows[0].open_path and rows[0].open_path.parent == tmp_path / "run2" / "outputs"
    tracker = ProgressTracker()
    moves = [m for m in (tracker.feed(line) for line in lines) if m]
    assert moves[0] == (0, 0, "Stage 1 of 2 (strip) · Starting")
    assert (0, 3, "Stage 1 of 2 (strip) · Editing and verifying") in moves and (3, 3, "Stage 1 of 2 (strip) · Editing and verifying") in moves
    assert (0, 3, "Stage 2 of 2 (refuse) · Editing and verifying") in moves
    assert tracker.seen.count("21 13 13 – WET-PIPE.docx") == 2


# ---- the command line ------------------------------------------------------------------------------------------------

def test_the_command_line_chain_command_and_library_use(folder, tmp_path, capsys):
    library.save_recipe("HF recipe", _hf())
    assert cli_main(["chain", str(folder), "--out", str(tmp_path / "o0"), "--no-render"]) == 2
    assert "give at least one --stage" in capsys.readouterr().err
    assert cli_main(["chain", "--stage", "recipe:nope", str(folder), "--out", str(tmp_path / "o0"), "--no-render"]) == 2
    assert "there is no recipe named 'nope'" in capsys.readouterr().err
    assert cli_main(["chain", "--stage", str(FONT_SPEC), "--stage", "recipe:HF recipe", str(folder), "--name", "Template chain",
                     "--out", str(tmp_path / "o1"), "--no-render", "--yes", "--save-as", "Template chain"]) == 0
    out = capsys.readouterr().out
    assert "PREVIEW — chain «Template chain»: 2 stage(s)" in out and "stage 1 PASS · stage 2 PASS" in out
    assert "Saved to your library: Chain: Template chain" in out
    assert "2 file(s): PASS 2 (every stage)  FAIL 0  EXCEPTION 0" in out and "[PASS · PASS]" in out
    assert (tmp_path / "o1" / "run" / "CHAIN_REPORT.md").is_file() and (tmp_path / "o1" / "preview" / "chain.json").is_file()
    assert sorted(p.name for p in (tmp_path / "o1" / "run" / "outputs").iterdir()) == sorted(f.name for f in folder.glob("21*.docx"))
    # the saved chain: shown, listed, used, spliced into a longer chain, deleted
    assert cli_main(["library", "show", "template chain"]) == 0
    out = capsys.readouterr().out
    assert "Chain: Template chain" in out and "1. match-template-spec-font" in out and "2. HF recipe (recipe 'HF recipe')" in out
    assert cli_main(["library", "list", "--kind", "chain"]) == 0 and "Chain: Template chain" in capsys.readouterr().out
    assert cli_main(["library", "use", "Template chain", str(folder), "--out", str(tmp_path / "o2"), "--no-render", "--yes"]) == 0
    assert "Using your chain 'Template chain' on 2 file(s)." in capsys.readouterr().err
    assert (tmp_path / "o2" / "run" / "outputs").is_dir()
    assert cli_main(["chain", "--stage", "chain:Template chain", "--stage", json.dumps(_remove()), str(folder),
                     "--out", str(tmp_path / "o3"), "--no-render", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "3 stage(s)" in out and "stage 1 PASS · stage 2 PASS · stage 3 PASS" in out
    # a stage that stops every file is exit 1 with the stage named; --save-as of a taken name is refused before anything
    assert cli_main(["chain", "--stage", json.dumps(_refuse()), str(folder), "--out", str(tmp_path / "o4"), "--no-render",
                     "--yes"]) == 1
    assert "stopped at stage 1: [not-found]" in capsys.readouterr().out
    assert cli_main(["chain", "--stage", str(FONT_SPEC), str(folder), "--out", str(tmp_path / "o5"), "--no-render", "--yes",
                     "--save-as", "HF recipe"]) == 2
    assert "--replace replaces only an item of the same kind, so a chain cannot take that name" in capsys.readouterr().err
    assert not (tmp_path / "o5" / "run").exists()
    assert cli_main(["library", "delete", "template chain", "--kind", "chain", "--yes"]) == 0
    assert [i.kind for i in library.list_items()] == ["recipe"]
    # without --yes and without a terminal: previewed, not run
    assert cli_main(["chain", "--stage", str(FONT_SPEC), str(folder), "--out", str(tmp_path / "o6"), "--no-render"]) == 0
    assert "Not run." in capsys.readouterr().out and not (tmp_path / "o6" / "run").exists()


# ---- the window --------------------------------------------------------------------------------------------------------

@pytest.fixture
def qt_env(tmp_path, monkeypatch):
    from qt_support import close_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    close_windows(app, windows)


def test_the_window_chains_recipes_previews_runs_and_saves_the_chain(qt_env, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts, build_legacy_window as build_window
    _app, windows = qt_env
    library.save_recipe("Font", _font())
    library.save_recipe("HF", _hf())
    prompts = RecordingPrompts(sequences=[["Font", "HF"]], texts=["My chain"], items=["Chain: My chain"])
    s = Session(out_root=tmp_path / "out")
    w = build_window(s, prompts=prompts)
    windows.append(w)
    ui = w._spec_surgeon
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["buttons"]["chain"].click()
    assert s.chain is not None and s.chain_label() == "Font → HF" and prompts.shown[0][0] == "pick_sequence"
    assert "Chain" in w.request_page.spec_bar.title.text() and "1. Font → 2. HF" in w.request_page.spec_bar.text.text()
    assert not ui["buttons"]["run"].isEnabled()
    ui["buttons"]["preview"].click()
    log = ui["wait"]()
    assert "Using the chain 'Font → HF' (2 stage(s))." in log and "stage 1 PASS · stage 2 PASS" in log
    assert ui["buttons"]["run"].isEnabled() and w.preview_page.request_lbl.text().startswith("Your chain “Font → HF” — 2 stage(s)")
    assert "1. Font:" in w.preview_page.summary_lbl.text() and "2. HF:" in w.preview_page.summary_lbl.text()
    assert w.nav.item(1).data(__import__("spec_surgeon.ui.widgets", fromlist=["STEP_STATUS_ROLE"]).STEP_STATUS_ROLE) == "Chain: Font → HF"
    ui["buttons"]["save_to_library"].click()
    assert [i.label() for i in library.list_items("chain")] == ["Chain: My chain"]
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert s.last_chain is not None and s.last_chain.counts()["PASS"] == 2
    assert ui["results_model"].rowCount() == 2 and "Done" in w.run_page.summary.title.text()
    assert w.run_page.report_btn.isEnabled() and (s.last_run_dir / "CHAIN_REPORT.md").is_file()
    # the library dialog loads a saved chain like a recipe
    s.remove_spec()
    ui["buttons"]["library"].click()
    assert s.chain is not None and s.chain_source == "library:My chain" and "My chain" in w.request_page.spec_bar.text.text()


def test_the_engine_and_command_line_never_load_qt_for_a_chain():
    import subprocess
    import sys
    code = ("import sys, spec_surgeon.chain, spec_surgeon.cli, spec_surgeon.gui, spec_surgeon.ui.rows; "
            "bad = [m for m in sys.modules if m.split('.')[0] in ('PySide6', 'shiboken6')]; assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


def test_the_sequence_dialog_builds_an_ordered_chain(qt_env):
    """The real picker behind Chain recipes… (RecordingPrompts stands in for it above): add, remove, move, the OK
    button only with a stage, and what it hands back, in order."""
    from PySide6.QtWidgets import QDialogButtonBox
    from qt_support import flush_deferred_deletes
    from spec_surgeon.ui.app import SequenceDialog
    app, _windows = qt_env
    dlg = SequenceDialog("Chain recipes", "Pick them in order", ["Font", "HF", "Strip"])
    try:
        ok = dlg.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert dlg.picked() == [] and not ok.isEnabled()
        dlg.available.setCurrentRow(1)
        dlg._add()
        dlg.available.setCurrentRow(0)
        dlg._add()
        assert dlg.picked() == ["HF", "Font"] and ok.isEnabled()
        dlg.chain.setCurrentRow(1)
        dlg._move(-1)
        assert dlg.picked() == ["Font", "HF"]
        dlg._move(-1)                                   # already first: nothing moves
        assert dlg.picked() == ["Font", "HF"]
        dlg.chain.setCurrentRow(0)
        dlg._move(1)
        assert dlg.picked() == ["HF", "Font"]
        dlg.chain.setCurrentRow(0)
        dlg._remove()
        assert dlg.picked() == ["Font"]
        dlg._remove()                                   # the selection moved with the removal: nothing selected now
        assert dlg.picked() in (["Font"], []) and ok.isEnabled() == bool(dlg.picked())
    finally:
        dlg.deleteLater()
        flush_deferred_deletes(app)
