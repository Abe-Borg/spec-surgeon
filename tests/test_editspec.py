"""EditSpec execution (Phase 1 deliverable): validation rules, plan-time refusals, and the example specs end to end
through run_batch — 24 files, no renderer."""
from __future__ import annotations

import copy
import json
import shutil
import time
from pathlib import Path

import pytest

from make_fixtures import build, build_batch1, build_rich, build_styles, png
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import SpecError, load_spec, plan_spec, spec_input_files, spec_templates, validate_spec
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite
from test_text_edits import _xlsx

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
    with pytest.raises(JobException) as e:                # the op validates its own params too
        get_op("replace_text").plan(DocxPackage(build("21 05 00", "A")), {},
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
def test_a_spec_runs_on_a_batch_of_24_files(tmp_path, name, capsys):
    n = 24
    src = tmp_path / "specs"
    src.mkdir()
    for i in range(n):
        (src / f"21 {i:02d} 00 – SPEC {i}.docx").write_bytes(build(f"21 {i:02d} 00", f"SPEC {i}"))
    out = tmp_path / "out"
    t0 = time.perf_counter()
    batch = run_batch([str(src)], "edit-spec", {}, out, workers=4, do_render=False,
                      spec=load_spec(SPECS / f"{name}.json"))
    dt = time.perf_counter() - t0
    assert batch.counts()["PASS"] == n, (out / "BATCH_REPORT.md").read_text(encoding="utf-8")
    report = json.loads((out / "batch.json").read_text(encoding="utf-8"))
    assert report["counts"]["PASS"] == n and report["spec"]["request"]
    assert len(list((out / "records").glob("*.record.md"))) == n
    with capsys.disabled():
        print(f"\n{name}: {n} files, no render: {dt:.2f}s wall = {dt / n * 1000:.0f} ms/file")


def test_a_spec_with_open_questions_never_runs(tmp_path):
    spec = copy.deepcopy(REMOVE); spec["questions"] = ["Keep the cover footer?"]
    (tmp_path / "a.docx").write_bytes(build("21 05 00", "A"))
    with pytest.raises(SpecError):
        run_batch([str(tmp_path / "a.docx")], "edit-spec", {}, tmp_path / "o", do_render=False, spec=spec)
    assert not (tmp_path / "o").exists()                       # nothing ran, nothing written


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


@pytest.fixture
def inputs_folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "211319 - Preaction.docx").write_bytes(build_batch1())
    (d / "PROJECT.xlsx").write_bytes(_xlsx([["Variable", "VA1", "VA3"], ["SITE_NAME", "Ashburn", "Leesburg"],
                                            ["OWNER_NAME", "Contoso", "Contoso"], ["PROJECT NO", "1001", "1003"]]))
    (d / "pairs.csv").write_text("find,replace\nOwner,Client\n", encoding="utf-8")
    (d / "logo.png").write_bytes(png((1, 2, 3), size=6))
    return d


def test_tables_and_pictures_are_spec_inputs_resolved_and_pinned(inputs_folder, tmp_path):
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [
        {"op": "swap_image", "params": {"image": "logo.png", "match": {"image": "old.png"}}},
        {"op": "fill_variables", "params": {"table": {"file": "PROJECT.xlsx"}}},
        {"op": "replace_table", "params": {"pairs_table": {"file": "pairs.csv"}}},
        {"op": "apply_header_footer_template", "params": {"template": "T.docx", "template_sections": {"0": 0},
                                                          "slot_fills": {}}}]}
    f = inputs_folder / "spec.json"
    f.write_text(json.dumps(spec), encoding="utf-8")
    loaded = load_spec(f)
    want = [inputs_folder / name for name in ("logo.png", "old.png", "PROJECT.xlsx", "pairs.csv", "T.docx")]
    assert spec_input_files(loaded) == [p.resolve() for p in want]
    assert spec_templates(loaded) == [(inputs_folder / "T.docx").resolve()]          # the batch filter: templates only


ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "specs"
WORKED = ("notes-boxed-and-shaded", "style-spacing", "clear-direct-formatting", "language-us-english",
          "bold-caps-to-heading", "architect-styles-to-office", "remove-unused-styles")


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.mark.parametrize("name", WORKED)
def test_the_worked_examples_plan_and_verify_on_the_architects_spec(src, name):
    spec = load_spec(EXAMPLES / f"{name}.json")
    validate_spec(spec)
    assert json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["request"]
    pkg = DocxPackage(src)
    plan, frags = plan_spec(pkg, spec)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions, name                     # every one of them asserts what it changed


AUTHOR = "Abraham Borg"


def test_expect_validation_refuses_impossible_ranges_and_unknown_revision_kinds():
    base = {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}, "expect": {}}]}
    for exp in ({"text_diffs": {"min": 5, "max": 1}}, {"text_diffs": {"min": -3}}, {"rev_delta": {"delx": 0}},
                {"rev_delta": {"ins": {"max": -1}}}):
        s = copy.deepcopy(base); s["ops"][0]["expect"] = exp
        with pytest.raises(SpecError) as e:
            validate_spec(s)
        assert e.value.code == "spec"
    ok = copy.deepcopy(base)
    ok["ops"][0]["expect"] = {"text_diffs": {"min": 1, "max": 10},
                              "rev_delta": {"ins": {"min": 1}, "del_mark": 0, "rPrChange": {"max": 2}}}
    validate_spec(ok)
    plan, _ = plan_spec(DocxPackage(build("21 05 00", "COMMON WORK RESULTS")), ok)
    assert len(plan.diffs) == 4


# ---- a wrongly shaped spec is a spec error ---------------------------------------------------------------------------

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
