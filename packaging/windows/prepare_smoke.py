"""Create synthetic input outside the installed app; never bundle test dependencies."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "tests"))
from make_fixtures import build
from test_codegen import GOOD_OP

if __name__ == "__main__":
    work = Path(sys.argv[1]).resolve()
    inputs = work / "Input specs with spaces"
    inputs.mkdir(parents=True, exist_ok=True)
    for i in range(2):
        (inputs / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    (work / "request.json").write_text(json.dumps({
        "input_dir": str(inputs), "output_dir": str(work / "Results"),
        "report": str(work / "smoke-result.json"), "generated_source": GOOD_OP,
    }), encoding="utf-8")
