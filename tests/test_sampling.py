"""Representative evidence scales with structural variety without reducing verification."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon.catalog import editspec_json_schema
from spec_surgeon.codegen import generate_op, pick_samples, run_generated
from spec_surgeon.editspec import validate_spec
from spec_surgeon.planner import BatchPlan, FilePlan
from spec_surgeon.preview import _pick_samples
from spec_surgeon.sampling import representative_samples
from test_codegen import GOOD_OP
from test_conversation import REMOVE


def test_automatic_preview_covers_every_pattern_even_above_default_allowance():
    files = [FilePlan(Path(f"p{i}.docx"), f"pattern{i}", "planned") for i in range(11)]
    files += [FilePlan(Path(f"common{i}.docx"), "pattern0", "planned") for i in range(6)]
    files.append(FilePlan(Path("unplannable.docx"), "excluded", "exception"))
    spec = copy.deepcopy(REMOVE)
    spec.pop("review", None)
    selected = _pick_samples(BatchPlan(spec, files))
    assert len(selected) == 11
    assert {f.pattern_id for f in files if f.source in selected} == {f"pattern{i}" for i in range(11)}
    assert Path("unplannable.docx") not in selected


def test_explicit_preview_count_two_is_honored_with_many_patterns():
    files = [FilePlan(Path(f"p{i}.docx"), f"pattern{i}", "planned") for i in range(5)]
    spec = copy.deepcopy(REMOVE)
    spec["review"]["sample_files"] = 2
    assert _pick_samples(BatchPlan(spec, files)) == [Path("p0.docx"), Path("p1.docx")]


def test_automatic_selection_fills_allowance_without_hiding_small_groups():
    files = [("large", i) for i in range(12)] + [("small", 0), ("rare", 0)]
    selected = representative_samples(files, pattern_key=lambda f: f[0])
    assert len(selected) == 8 and len(set(selected)) == 8
    assert selected[:3] == [("large", 0), ("small", 0), ("rare", 0)]
    assert representative_samples(files, pattern_key=lambda f: f[0], count=100) == selected[:3] + files[1:12]


@pytest.mark.parametrize("count", [4, 8, 20])
def test_explicit_positive_sample_counts_have_no_three_file_ceiling(count):
    spec = copy.deepcopy(REMOVE)
    spec["review"]["sample_files"] = count
    validate_spec(spec)
    schema = editspec_json_schema()["properties"]["review"]["properties"]["sample_files"]
    assert "maximum" not in schema


@pytest.mark.parametrize("count", [True, 0, -1, 2.5])
def test_sample_allowance_rejects_invalid_counts_even_for_empty_input(count):
    with pytest.raises(ValueError, match="positive integer"):
        representative_samples([], pattern_key=lambda x: x, count=count)


def test_codegen_representatives_keep_full_paths_and_all_patterns(monkeypatch, tmp_path):
    from spec_surgeon import profile
    paths = [tmp_path / f"project{i}" / "Spec.docx" for i in range(10)]
    patterns = [{"pattern_id": f"p{i}", "paths": [str(path)], "files": [path.name]} for i, path in enumerate(paths)]
    monkeypatch.setattr(profile, "profile_batch", lambda files: {"patterns": patterns})
    assert pick_samples(paths) == paths
    assert pick_samples(paths, count=2) == paths[:2]


def test_eight_samples_still_require_verifying_every_delivered_file(tmp_path):
    folder = tmp_path / "specs"
    folder.mkdir()
    files = []
    for i in range(10):
        path = folder / f"spec{i}.docx"
        path.write_bytes(build("21 00 00", f"SPEC {i}"))
        files.append(path)
    samples = pick_samples(files)
    assert len(samples) == 8
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "good")))
    generated = generate_op("header 900 twips", "formatting", samples, tmp_path / "generated", client=fake)
    assert generated.passed and len(generated.trials) == 8
    results = run_generated(generated, [str(folder)], tmp_path / "outputs")
    assert len(results) == 10 and all(r.passed and r.record for r in results)
    assert {r.source for r in results} == set(files)
