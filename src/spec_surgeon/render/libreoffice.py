"""LibreOffice headless renderer. Batch-converts many files per process (amortizes startup) and runs
several processes side by side, each with its own user profile (LibreOffice serializes on a shared
profile)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

_CANDIDATES = [
    "soffice", "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]


def find_soffice() -> Optional[str]:
    env = os.environ.get("SPEC_SURGEON_SOFFICE")
    if env and Path(env).exists():
        return env
    for c in _CANDIDATES:
        p = shutil.which(c) if not os.path.isabs(c) else (c if Path(c).exists() else None)
        if p:
            return p
    return None


def _convert_batch(soffice: str, files: Sequence[Path], out_dir: Path, profile_dir: Path, timeout: int) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("SAL_USE_VCLPLUGIN", "svp")
    cmd = [soffice, "--headless", "--norestore", "--nologo",
           f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
           "--convert-to", "pdf", "--outdir", str(out_dir)] + [str(f) for f in files]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout, env=env)


def render_pdfs(files: Sequence[Path], out_dir: Path, workers: int = 4, timeout: int = 600,
                log: Optional[Callable[[str], None]] = None) -> Dict[Path, Path]:
    """Render each .docx to <out_dir>/<stem>.pdf. Returns {docx: pdf} for the ones that rendered. A chunk whose
    soffice process fails (a crash on one file, a timeout) is retried one file at a time, so one bad file costs
    its own PDF and no other's; every failure is said on ``log``."""
    log = log or (lambda msg: None)
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not found; set SPEC_SURGEON_SOFFICE or install LibreOffice")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files]
    if not files:
        return {}
    workers = max(1, min(workers, len(files)))
    chunks = [files[i::workers] for i in range(workers)]
    base = Path(tempfile.mkdtemp(prefix="specsurgeon-lo-"))
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [(chunk, ex.submit(_convert_batch, soffice, chunk, out_dir, base / f"profile{i}", timeout))
                    for i, chunk in enumerate(chunks) if chunk]
            retry: List[Path] = []
            for chunk, fut in futs:
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001 — the chunk's process failed; find the file(s) at fault
                    log(f"LibreOffice failed on a chunk of {len(chunk)} file(s) ({type(e).__name__}: "
                        f"{str(e)[:200]}); retrying them one by one")
                    retry += [f for f in chunk if not (out_dir / (f.stem + ".pdf")).exists()]
        for k, f in enumerate(retry):
            try:
                _convert_batch(soffice, [f], out_dir, base / f"retry{k}", timeout)
            except Exception as e:  # noqa: BLE001
                log(f"{f.name}: LibreOffice could not render it: {type(e).__name__}: {str(e)[:200]}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    result = {}
    for f in files:
        pdf = out_dir / (f.stem + ".pdf")
        if pdf.exists():
            result[f] = pdf
        else:
            log(f"{f.name}: no PDF came out of LibreOffice")
    return result
