"""EditSpec execution (Phase 1 deliverable): validation rules, plan-time refusals, and the example specs end to end
through run_batch and the run-spec CLI — 24 files, no renderer."""
from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path

import pytest

from make_fixtures import build, build_rich
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.editspec import SpecError, load_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException
from spec_surgeon.package import DocxPackage

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
FONT = load_spec(SPECS / "change-all-font-arial.json")
REMOVE = load_spec(SPECS / "remove-all-headers-footers.json")
TEMPLATE = load_spec(SPECS / "apply-header-footer-template.json")


def test_example_specs_are_valid():
    validate_spec(FONT)
    validate_spec(REMOVE)
    validate_spec(TEMPLATE)
    assert Path(TEMPLATE["ops"][0]["params"]["template"]).is_file()      # resolved against the spec's folder


@pytest.mark.parametrize("mutation, code", [
    (lambda s: s.update(questions=["Which stamp?"]), "questions"),
    (lambda s: s.update(unsupported="needs a new op"), "unsupported"),
    (lambda s: s.update(colour="red"), "spec"),                                        # typo'd key
    (lambda s: s["ops"][0].update(op="set_fonts"), "spec"),                            # unknown op
    (lambda s: s["ops"][0].update(paramz={}), "spec"),
    (lambda s: s.update(assertions=[{"kind": "run_fonts"}]), "spec"),                  # could exempt things from V10
    (lambda s: s["ops"][0].update(expect={"text_diffs": "zero"}), None),               # prose is allowed
    (lambda s: s["ops"][0].update(expect={"text_difs": 0}), "spec"),
    # op select/params are checked against the op's declared schema (Codex review on PR #1)
    (lambda s: s["ops"][0]["params"].update(fnt="Arial"), "spec"),                     # unknown param key
    (lambda s: s["ops"][0]["params"].update(font=12), "spec"),                         # wrong type
    (lambda s: s["ops"][0]["params"].update(in_revisions="sometimes"), "spec"),        # not in enum
    (lambda s: s["ops"][0]["select"].update(scope=["document", "header"]), "spec"),    # scope typo
    (lambda s: s["ops"][0]["select"].update(paragraph={"style_in": ["Heading1"]}), "spec"),   # selector key typo
    (lambda s: s["ops"][2].update(select={"styles": "all"}), "spec"),                  # op takes no selector
    (lambda s: s["ops"][1].update(tracked=False), None),                               # explicit "not tracked" is fine
    (lambda s: s["ops"][1].update(tracked=True), "spec"),                              # styles are never tracked
])
def test_spec_validation(mutation, code):
    spec = copy.deepcopy(FONT)
    mutation(spec)
    if code is None:
        validate_spec(spec)
        return
    with pytest.raises(SpecError) as e:
        validate_spec(spec)
    assert e.value.code == code


def test_replace_text_param_typos_are_refused_not_widened():
    """'occurrences': 'frist' used to behave as 'all' — every match edited, verified and delivered."""
    spec = {"request": "r", "summary": "s", "mode": "content", "ops": [
        {"op": "replace_text", "params": {"find": "FM Global", "replace": "GRC", "occurrences": "frist"}}]}
    with pytest.raises(SpecError) as e:
        validate_spec(spec)
    assert "occurrences" in e.value.reason
    spec["ops"][0]["params"] = {"find": "FM Global", "replace": "GRC", "occurrence": "first"}
    with pytest.raises(SpecError):
        validate_spec(spec)
    from spec_surgeon.jobs import get_job        # the v0.1.0 job path validates inside the op
    with pytest.raises(JobException) as e:
        get_job("tracked-replace").plan(DocxPackage(build("21 05 00", "A")),
                                        {"find": "FM Global", "replace": "GRC", "occurrences": "frist"})
    assert e.value.code == "bad-params"


def test_plan_time_refusals():
    pkg = DocxPackage(build_rich())
    spec = copy.deepcopy(FONT); spec["mode"] = "content"
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "spec-mode"
    spec = copy.deepcopy(FONT); spec["review"]["geometry_reference"] = "source"     # a font change reflows
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "geometry"
    spec = copy.deepcopy(REMOVE); spec["review"]["geometry_reference"] = "none"     # would drop a kept reference
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "geometry"
    spec = copy.deepcopy(REMOVE); spec["ops"][0]["expect"] = {"members_removed": 2}
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "expect"
    spec = copy.deepcopy(REMOVE); spec["applies_to"] = {"pattern_ids": ["0000000000"]}
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "pattern"
    spec = copy.deepcopy(REMOVE)
    spec["ops"].append({"op": "set_font", "select": {"scope": ["headers"]}, "params": {"font": "Arial"}})
    with pytest.raises(JobException) as e:
        plan_spec(pkg, spec)
    assert e.value.code == "conflict"


def test_example_specs_on_the_rich_fixture(tmp_path):
    (tmp_path / "21 13 13 – RICH.docx").write_bytes(build_rich())
    for spec in (FONT, REMOVE):
        batch = run_batch([str(tmp_path)], "edit-spec", {}, tmp_path / spec["request"].replace(" ", "_"),
                          workers=1, renderer="none", do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
        assert f.record.get("V10").status == "PASS"
        if spec is FONT:
            assert "effective_font" in f.record.get("V10").summary


@pytest.mark.parametrize("name", ["change-all-font-arial", "remove-all-headers-footers", "apply-header-footer-template"])
def test_run_spec_batch_24_files(tmp_path, name, capsys):
    n = 24
    src = tmp_path / "specs"
    src.mkdir()
    for i in range(n):
        (src / f"21 {i:02d} 00 – SPEC {i}.docx").write_bytes(build(f"21 {i:02d} 00", f"SPEC {i}"))
    out = tmp_path / "out"
    t0 = time.perf_counter()
    rc = cli_main(["run-spec", str(SPECS / f"{name}.json"), str(src), "--out", str(out), "--no-render", "--workers", "4"])
    dt = time.perf_counter() - t0
    assert rc == 0, (out / "BATCH_REPORT.md").read_text(encoding="utf-8")
    report = json.loads((out / "batch.json").read_text(encoding="utf-8"))
    assert report["counts"]["PASS"] == n and report["spec"]["request"]
    assert len(list((out / "records").glob("*.record.md"))) == n
    with capsys.disabled():
        print(f"\nrun-spec {name}: {n} files, --no-render: {dt:.2f}s wall = {dt / n * 1000:.0f} ms/file")


def test_run_spec_refuses_open_questions(tmp_path, capsys):
    spec = copy.deepcopy(REMOVE); spec["questions"] = ["Keep the cover footer?"]
    p = tmp_path / "q.json"; p.write_text(json.dumps(spec), encoding="utf-8")
    (tmp_path / "a.docx").write_bytes(build("21 05 00", "A"))
    assert cli_main(["run-spec", str(p), str(tmp_path / "a.docx"), "--out", str(tmp_path / "o"), "--no-render"]) == 2
    assert not (tmp_path / "o").exists()                       # nothing ran, nothing written
    assert "Keep the cover footer?" in capsys.readouterr().err


def test_run_spec_in_place_guarded(tmp_path):
    f = tmp_path / "21 05 00 – A.docx"; f.write_bytes(build("21 05 00", "A"))
    before = f.read_bytes()
    batch = run_batch([str(f)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      in_place=True, spec=REMOVE)
    assert batch.files[0].status == "PASS" and f.read_bytes() != before
    assert batch.files[0].record.get("V9").status == "PASS"
    assert all(not s.header_refs and not s.footer_refs for s in DocxPackage.from_path(f).sections())


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_remove_all_rendered_geometry_unchanged(tmp_path):
    """V7 keeps the source render as reference for header/footer removal: body text must not move."""
    (tmp_path / "21 13 13 – RICH.docx").write_bytes(build_rich())
    batch = run_batch([str(tmp_path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="libreoffice",
                      spec=REMOVE)
    rec = batch.files[0].record
    assert rec.passed and rec.get("V7").status == "PASS", rec.to_markdown()
