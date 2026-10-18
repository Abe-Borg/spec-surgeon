"""Create synthetic input outside the installed app; never bundle test dependencies.

    python packaging/windows/prepare_smoke.py WORK                      → WORK/request.json (gui mode)
    python packaging/windows/prepare_smoke.py WORK --update INSTALLER   → WORK/update-request.json (update mode)
"""
import hashlib
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "tests"))
sys.path.insert(0, str(root / "src"))
from make_fixtures import build
from test_codegen import GOOD_OP

if __name__ == "__main__":
    work = Path(sys.argv[1]).resolve()
    if sys.argv[2:3] == ["--update"]:
        from spec_surgeon import __version__
        installer = Path(sys.argv[3]).resolve()
        (work / "update-request.json").write_text(json.dumps({
            "mode": "update", "installer": str(installer),
            "sha256": hashlib.sha256(installer.read_bytes()).hexdigest(), "version": __version__,
            "install_log": str(work / "update-install.log"), "output_dir": str(work / "Results"),
            "config_dir": str(work / "config"), "report": str(work / "update-result.json"),
        }), encoding="utf-8")
        sys.exit(0)
    inputs = work / "Input specs with spaces"
    inputs.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (inputs / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    (work / "request.json").write_text(json.dumps({
        "input_dir": str(inputs), "output_dir": str(work / "Results"),
        "report": str(work / "smoke-result.json"), "generated_source": GOOD_OP,
        "config_dir": str(work / "config"),
        "schemas": sorted(p.relative_to(root / "src/spec_surgeon/schemas").as_posix()
                          for p in (root / "src/spec_surgeon/schemas").rglob("*.xsd")),
        "worked_examples": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted((root / "examples/specs").glob("*.json"))},
    }), encoding="utf-8")
