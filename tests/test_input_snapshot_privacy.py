"""New auxiliary copies must not enter a diagnostics export under JSON/CSV suffixes."""
import json
import zipfile

from spec_surgeon import diagnostics
from spec_surgeon.input_snapshots import snapshot_inputs


def test_diagnostics_excludes_input_snapshots_regardless_of_extension(tmp_path):
    table = tmp_path / "private-input.json"
    table.write_text(json.dumps({"PROJECT": "Private input wording"}), encoding="utf-8")
    results = tmp_path / "results"
    results.mkdir()
    spec = {"ops": [{"op": "fill_variables", "params": {"table": {"file": str(table)}}}]}
    _, manifest = snapshot_inputs(spec, results)
    assert manifest
    (results / "DIAGNOSTICS.md").write_text("Safe diagnostic artifact", encoding="utf-8")
    bundle = diagnostics.make_bundle(tmp_path / "diagnostics.zip", results)
    with zipfile.ZipFile(bundle["path"]) as archive:
        assert "results/DIAGNOSTICS.md" in archive.namelist()
        assert not any("input-snapshot-" in name for name in archive.namelist())
        assert not any(name.endswith("private-input.json") for name in archive.namelist())
