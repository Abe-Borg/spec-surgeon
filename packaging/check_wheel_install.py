"""Install a built wheel into a fresh core-only venv and compare its packaged prompts.

Used on Linux and Windows by pull-request CI. Nothing is installed into the
environment running the tests, and no model or rendering service is contacted.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv


def main(dist: Path):
    wheels = list(dist.resolve().glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected one built wheel in {dist}, found {len(wheels)}")
    root = Path(__file__).resolve().parents[1]
    smoke = root / "packaging/installed_wheel_smoke.py"
    clean_env = dict(os.environ, PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    clean_env.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="spec-surgeon-wheel-") as work:
        work = Path(work)
        prompts = work / "source-prompts.json"
        source_env = {**clean_env, "PYTHONPATH": str(root / "src")}
        fingerprints = subprocess.run([sys.executable, str(smoke), "--print-prompt-fingerprints"],
            cwd=work, env=source_env, text=True, capture_output=True, check=True)
        prompts.write_text(fingerprints.stdout, encoding="utf-8")
        installed = work / "installed"
        venv.EnvBuilder(with_pip=True).create(installed)
        python = installed / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheels[0])],
                       cwd=work, env=clean_env, check=True)
        subprocess.run([str(python), str(smoke), str(root / "examples/specs"), "--expected-prompts", str(prompts)],
                       cwd=work, env=clean_env, check=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
