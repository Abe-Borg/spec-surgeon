"""LibreOffice headless renderer. Batch-converts many files per process (amortizes startup) and runs
several processes side by side, each with its own user profile (LibreOffice serializes on a shared
profile).

The profiles are kept: ``<config dir>/render-profiles/<n>`` (``settings.config_dir``), one per parallel worker,
reused from call to call and from launch to launch instead of created and deleted every time — creating one is
most of what a soffice start costs. A profile is leased for the life of one soffice run (a lock file beside it,
released by the OS when the process ends), so a second process rendering at the same time takes the next free
index rather than sharing — two soffice on one profile hand their work to each other and lose it. A profile that
fails to start (soffice exits with an error and nothing came out of it) is wiped and recreated once; the chunk is
tried again on the fresh one before its files are retried one by one.

What LibreOffice says is kept: a chunk that fails is reported with its exit status and the end of its stderr/stdout
(not just "Command … returned non-zero exit status"), and a file that comes out without a PDF is reported with what
the process printed — LibreOffice exits 0 even when it could not load a file."""
from __future__ import annotations

import configparser
import contextvars
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple

from .. import diagnostics as diag

_CANDIDATES = [
    "soffice", "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]
PROFILES_DIR_NAME = "render-profiles"
MAX_PROFILES = 32           # indexes a lease may try past the one it was asked for


def find_soffice() -> Optional[str]:
    env = os.environ.get("SPEC_SURGEON_SOFFICE")
    if env and Path(env).exists():
        return env
    for c in _CANDIDATES:
        p = shutil.which(c) if not os.path.isabs(c) else (c if Path(c).exists() else None)
        if p:
            return p
    return None


def version(soffice: Optional[str] = None) -> Optional[str]:
    """LibreOffice's version from the files beside soffice (``version.ini`` on Windows, ``versionrc`` elsewhere) —
    read, not run, so asking costs no process start."""
    exe = soffice or find_soffice()
    if not exe:
        return None
    try:
        here = Path(exe).resolve().parent
    except OSError:
        return None
    for name in ("version.ini", "versionrc", "../Resources/versionrc"):
        f = here / name
        if not f.is_file():
            continue
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read_string(f.read_text(encoding="utf-8", errors="replace"))
        except (configparser.Error, OSError):
            continue
        sec = cp["Version"] if cp.has_section("Version") else None
        if sec is None:
            continue
        for key in ("MsiProductVersion", "ProductVersion"):
            if sec.get(key):
                return sec[key]
        if sec.get("buildid"):
            return f"build {sec['buildid']}"
    return None


def executable_identity(soffice: Optional[str] = None) -> Optional[str]:
    """When no version file can be read: the executable's size and modification time, which an upgrade changes —
    the render cache's fallback renderer identity. None when there is no executable to look at."""
    exe = soffice or find_soffice()
    if not exe:
        return None
    try:
        st = Path(exe).resolve().stat()
    except OSError:
        return None
    return f"exe-{st.st_size}-{int(st.st_mtime)}"


# ---- user profiles: kept, leased, recreated when broken ---------------------------------------------------------------

def profiles_root() -> Path:
    from ..settings import config_dir
    return config_dir() / PROFILES_DIR_NAME


def _try_lock(fh) -> None:
    """An exclusive, non-blocking lock the OS releases when the holder dies; raises OSError when another holds it."""
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh) -> None:
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        fh.close()


class ProfileLease:
    """``with ProfileLease(root, index) as lease:`` — ``lease.path`` is a user profile no other soffice is using:
    the one asked for, or the next free index when another process holds it. Held through a lock file
    (``<root>/<index>.lock``) for the life of the run."""

    def __init__(self, root: Path, index: int):
        self.root = Path(root)
        self.wanted = int(index)
        self.index: Optional[int] = None
        self.path: Optional[Path] = None
        self._lock = None

    def __enter__(self) -> "ProfileLease":
        self.root.mkdir(parents=True, exist_ok=True)
        for i in range(self.wanted, self.wanted + MAX_PROFILES):
            fh = open(self.root / f"{i}.lock", "a+b")
            try:
                _try_lock(fh)
            except OSError:
                fh.close()
                continue
            self._lock, self.index, self.path = fh, i, self.root / str(i)
            self.path.mkdir(parents=True, exist_ok=True)
            if i != self.wanted:
                diag.event("render.profile_busy", backend="libreoffice", wanted=self.wanted, leased=i)
            return self
        raise RuntimeError(f"no free LibreOffice profile under {self.root} ({MAX_PROFILES} are in use)")

    def __exit__(self, *exc) -> None:
        if self._lock is not None:
            _unlock(self._lock)
            self._lock = None


def reset_profile(path: Path) -> None:
    """Wipe a profile LibreOffice cannot start from and recreate it empty (LibreOffice fills it on the next start)."""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


# ---- conversion -----------------------------------------------------------------------------------------------------

def _convert_batch(soffice: str, files: Sequence[Path], out_dir: Path, profile_dir: Path, timeout: int):
    profile_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("SAL_USE_VCLPLUGIN", "svp")
    cmd = [soffice, "--headless", "--norestore", "--nologo",
           f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
           "--convert-to", "pdf", "--outdir", str(out_dir)] + [str(f) for f in files]
    with diag.span("render.process", backend="libreoffice", executable=soffice,
                   files=[str(f) for f in files], out_dir=str(out_dir), profile=str(profile_dir),
                   timeout_seconds=timeout) as outcome:
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, timeout=timeout, env=env)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            outcome.update(returncode=getattr(e, "returncode", None),
                           stdout=_tail(getattr(e, "stdout", None)), stderr=_tail(getattr(e, "stderr", None)))
            raise
        outcome.update(returncode=result.returncode, stdout=_tail(result.stdout), stderr=_tail(result.stderr))
        return result


def _tail(raw, n: int = 6) -> str:
    """The last lines a process printed, decoded leniently."""
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    lines = [x.rstrip() for x in text.strip().splitlines() if x.strip()]
    return " | ".join(lines[-n:])


def failure_text(e: BaseException) -> str:
    """Why a soffice run failed: the exit status or timeout, and the end of what it printed."""
    if isinstance(e, subprocess.TimeoutExpired):
        out = _tail(getattr(e, "stderr", None)) or _tail(getattr(e, "output", None))
        return f"timed out after {e.timeout:.0f}s" + (f"; it printed: {out}" if out else "")
    if isinstance(e, subprocess.CalledProcessError):
        err, out = _tail(e.stderr), _tail(e.output)
        return (f"exit status {e.returncode}" + (f"; stderr: {err}" if err else "") + (f"; stdout: {out}" if out else "")
                + ("" if err or out else "; it printed nothing"))
    return f"{type(e).__name__}: {str(e)[:300]}"


def render_pdfs(files: Sequence[Path], out_dir: Path, workers: int = 4, timeout: int = 600,
                log: Optional[Callable[[str], None]] = None,
                detail: Optional[Callable[[str], None]] = None) -> Dict[Path, Path]:
    """Render each .docx to <out_dir>/<stem>.pdf. Returns {docx: pdf} for the ones that rendered. A chunk whose
    soffice process fails (a crash on one file, a timeout) is retried one file at a time, so one bad file costs
    its own PDF and no other's; every failure is said on ``log``, with what soffice printed; ``detail`` gets each
    run's command summary, time and output. Chunk ``i`` runs on profile ``i`` (see ``ProfileLease``), and so do
    its one-by-one retries; a profile soffice cannot start from is wiped and recreated once per call."""
    log = log or (lambda msg: None)
    detail = detail or (lambda msg: None)
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) not found; set SPEC_SURGEON_SOFFICE or install LibreOffice")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = [Path(f) for f in files]
    if not files:
        return {}
    workers = max(1, min(workers, len(files)))
    chunks = [files[i::workers] for i in range(workers)]
    root = profiles_root()
    said: Dict[Path, str] = {}                      # file → what its (last) soffice run printed
    reported: set = set()                           # files whose failure has been said already
    wiped: Set[Path] = set()                        # profiles recreated during this call (once each)
    worked: Set[Path] = set()                       # profiles a PDF came out of during this call: proven, never wiped

    def came_out(chunk: List[Path]) -> bool:
        return any((out_dir / (f.stem + ".pdf")).exists() for f in chunk)

    def run(chunk: List[Path], lease: ProfileLease, attempt: int = 1):
        t = time.perf_counter()
        for f in chunk:                             # a PDF from an earlier run is never taken for this one
            (out_dir / (f.stem + ".pdf")).unlink(missing_ok=True)
        with diag.scope(render_attempt=attempt, render_profile=lease.index):
            try:
                done = _convert_batch(soffice, chunk, out_dir, lease.path, timeout)
            except subprocess.CalledProcessError as e:
                if came_out(chunk):
                    worked.add(lease.path)
                if lease.path in wiped or lease.path in worked:
                    raise
                # soffice exited with an error and nothing has ever come out of this profile: a damaged profile makes
                # every start fail this way, so recreate it once and try the chunk again on the fresh one
                wiped.add(lease.path)
                diag.event("render.profile_reset", level="warning", backend="libreoffice", profile=str(lease.path),
                           reason=failure_text(e))
                detail(f"LibreOffice: profile {lease.index} failed to start ({failure_text(e)}); recreating it and "
                       f"trying the {len(chunk)} file(s) again")
                reset_profile(lease.path)
                done = _convert_batch(soffice, chunk, out_dir, lease.path, timeout)
            except subprocess.TimeoutExpired:
                if came_out(chunk):
                    worked.add(lease.path)
                raise
        if came_out(chunk):
            worked.add(lease.path)
        printed = " ".join(x for x in (_tail(getattr(done, "stdout", None)), _tail(getattr(done, "stderr", None))) if x)
        for f in chunk:
            said[f] = printed
        detail(f"LibreOffice (profile {lease.index}): {len(chunk)} file(s) → {out_dir.name}/ in "
               f"{time.perf_counter() - t:.1f}s" + (f"; it printed: {printed}" if printed else ""))
        return done

    def run_chunk(index: int, chunk: List[Path]):
        with ProfileLease(root, index) as lease:
            return run(chunk, lease)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [(i, chunk, ex.submit(contextvars.copy_context().run, run_chunk, i, chunk))
                for i, chunk in enumerate(chunks) if chunk]
        retry: List[Tuple[int, Path]] = []
        for i, chunk, fut in futs:
            try:
                fut.result()
            except Exception as e:  # noqa: BLE001 — the chunk's process failed; find the file(s) at fault
                diag.event("render.retry", level="warning", backend="libreoffice", attempt=2,
                           files=[str(f) for f in chunk], reason=failure_text(e))
                log(f"LibreOffice failed on a chunk of {len(chunk)} file(s) ({failure_text(e)}); retrying them "
                    f"one by one: {[f.name for f in chunk][:8]}")
                retry += [(i, f) for f in chunk if not (out_dir / (f.stem + ".pdf")).exists()]
    for i, f in retry:
        try:
            with ProfileLease(root, i) as lease:
                run([f], lease, attempt=2)
        except Exception as e:  # noqa: BLE001
            reported.add(f)
            diag.event("render.file_failed", level="error", backend="libreoffice", source=str(f),
                       attempt=2, reason=failure_text(e))
            log(f"{f.name}: LibreOffice could not render it: {failure_text(e)}")
    result = {}
    for f in files:
        pdf = out_dir / (f.stem + ".pdf")
        if pdf.exists():
            result[f] = pdf
            diag.event("render.file_ready", backend="libreoffice", source=str(f), output=str(pdf))
        elif f not in reported:
            diag.event("render.file_missing", level="warning", backend="libreoffice", source=str(f),
                       output=str(pdf), process_output=said.get(f))
            log(f"{f.name}: no PDF came out of LibreOffice" + (f"; it printed: {said[f]}" if said.get(f) else
                                                                " and it printed nothing"))
    return result
