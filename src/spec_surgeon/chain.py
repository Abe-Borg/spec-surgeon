"""Chains — several EditSpecs run one after another, each stage a full verified run on the previous stage's verified
outputs (decided with Abraham on 2026-10-16: "first change the font, then match the header/footer of the template
spec").

Why a chain and not one spec: a spec's ops are planned from the SOURCE and merged into one plan (CLAUDE.md rule 2),
so two ops that touch the same thing are refused rather than ordered (rule 9) — a font change over the headers plus a
header/footer template, say. A chain keeps every rule: each stage is an ordinary ``run_batch`` (plan → apply → verify
V1–V10 → render → deliver) whose source is the stage before it, so the end-to-end claim is the composition of N
verified steps.

    run_chain(inputs, chain, out_dir) →  <out>/stage-01/ … stage-NN/   one results folder per stage (a batch each)
                                         <out>/outputs/<name>.docx     files that passed EVERY stage (copies mode)
                                         <out>/CHAIN_REPORT.md, chain.json, set_aside.json, DIAGNOSTICS.md, logs/chain.log

Rules this module keeps:
* **The pin between stages.** Stage N+1 reads a file only when its bytes are the ones stage N verified
  (``FileResult.output_md5``, the hash ``batch.verified_bytes`` re-checks at delivery): a file changed between stages —
  edited, synced, replaced — is set aside there (``chain-drift``), never carried on. In copies mode stage N+1's input is
  stage N's copy in its ``outputs/``; in place, the source file itself, as stage N wrote it back.
* **A file goes as far as it passes.** One that is set aside or fails verification at stage N stops there, untouched by
  the later stages; the report says at which stage and why. In place, a file that stopped at stage N has stages 1…N-1
  written back (said in the report); in copies mode the sources are never changed.
* **A run never reads where it writes** (``batch.check_inputs_outside`` on the chain's folder before anything is
  created), a template of ANY stage is skipped from the inputs (``editspec.is_template_file``), and the chain's
  ``outputs/`` holds only files that passed every stage OF THIS RUN, re-hashed before the copy: a reused results
  folder has its ``outputs/`` — and each stage folder's ``outputs/`` and ``failed/`` — emptied first (Codex on #23: a
  file that passed last time and stops this time must not survive from the earlier run).
* **Order is the user's, flagged where it matters**: a later stage that changes or removes a part an earlier stage added
  (a font change over the template's new header) is flagged per file in the report, never refused — inside one spec the
  engine would refuse it (rule 9); in a chain the user asked for that order.
* Each stage keeps its own records, logs, plans and exception records in its folder; the chain adds its own log,
  report and set-aside manifest (``report.write_chain_report`` / ``write_chain_diagnostics``) with the ORIGINAL source
  paths, so the set-aside loop (``retry``, *Load set-aside files*) works on the user's files.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from . import diagnostics as diag
from .editspec import SpecError, is_template_file, spec_templates, validate_spec

CHAIN_KEYS = {"name", "description", "stages", "assertions"}
STAGE_KEYS = {"name", "recipe", "spec"}
STAGE_DIR = "stage-{:02d}"
CHAIN_LOG = "chain.log"
DRIFT_CODE = "chain-drift"
DELIVER_CODE = "chain-deliver"
OUTCOME_CODE = "chain-outcome"


class ChainError(SpecError):
    """The chain itself is unusable (before any file is touched): its shape, or one of its stages' specs."""

    def __init__(self, reason: str, details: Optional[dict] = None):
        super().__init__(reason, "chain", details)


def stage_name(stage: Dict[str, Any], index: int) -> str:
    """How a stage is called: its name, else the recipe it came from, else its spec's request (cut)."""
    name = stage.get("name") or stage.get("recipe") or ""
    if not name:
        req = str((stage.get("spec") or {}).get("request") or "")
        name = req if len(req) <= 60 else req[:57] + "…"
    return str(name) or f"stage {index}"


def chain_name(chain: Dict[str, Any]) -> str:
    return str(chain.get("name") or " → ".join(stage_name(s, i) for i, s in enumerate(chain.get("stages") or [], 1)))


def validate_chain(chain: Any) -> None:
    """A chain is ``{"name"?, "description"?, "stages": [{"name"?, "recipe"?, "spec": <EditSpec>}, …]}`` with at least
    one stage; every stage's spec passes ``validate_spec`` (a stage with open questions or an unsupported spec refuses
    the whole chain, before any file is read)."""
    if not isinstance(chain, dict):
        raise ChainError("a chain is a JSON object with a list of stages")
    unknown = set(chain) - CHAIN_KEYS
    if unknown:
        raise ChainError(f"unknown chain key(s) {sorted(unknown)}; known: {sorted(CHAIN_KEYS)}")
    for k in ("name", "description"):
        if chain.get(k) is not None and not isinstance(chain[k], str):
            raise ChainError(f"chain '{k}' must be text, got {chain[k]!r}")
    if "assertions" in chain:
        from .verify.outcomes import validate_assertions
        try:
            validate_assertions(chain["assertions"])
        except ValueError as e:
            raise ChainError(str(e)) from e
    stages = chain.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ChainError("a chain needs 'stages': a non-empty list, one EditSpec per stage, in the order they run")
    for i, st in enumerate(stages, 1):
        if not isinstance(st, dict):
            raise ChainError(f"stage {i} must be an object with a 'spec'")
        bad = set(st) - STAGE_KEYS
        if bad:
            raise ChainError(f"stage {i}: unknown key(s) {sorted(bad)}; known: {sorted(STAGE_KEYS)}")
        for k in ("name", "recipe"):
            if st.get(k) is not None and not isinstance(st[k], str):
                raise ChainError(f"stage {i}: '{k}' must be text, got {st[k]!r}")
        if not isinstance(st.get("spec"), dict):
            raise ChainError(f"stage {i} ({stage_name(st, i)}): 'spec' must be an EditSpec object")
        try:
            validate_spec(st["spec"])
        except SpecError as e:
            raise ChainError(f"stage {i} ({stage_name(st, i)}): [{e.code}] {e.reason}",
                             {"stage": i, "code": e.code, **e.details}) from e


def chain_from_specs(specs: Sequence[Any], name: str = "", description: str = "",
                     assertions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """A chain from ``(stage name, spec)`` pairs or ``(stage name, spec, recipe name)`` triples, validated."""
    stages = []
    for item in specs:
        nm, spec = item[0], item[1]
        st: Dict[str, Any] = {"name": str(nm or ""), "spec": spec}
        if len(item) > 2 and item[2]:
            st["recipe"] = str(item[2])
        stages.append(st)
    chain: Dict[str, Any] = {"stages": stages}
    if name:
        chain["name"] = name
    if description:
        chain["description"] = description
    if assertions is not None:
        chain["assertions"] = copy.deepcopy(assertions)
    validate_chain(chain)
    return chain


def chain_templates(chain: Dict[str, Any]) -> List[Path]:
    """Every template file any stage reads (never edited, never in the batch), in stage order, once each."""
    out: List[Path] = []
    for st in chain.get("stages") or []:
        for t in spec_templates(st.get("spec") or {}):
            if t not in out:
                out.append(t)
    return out


def chain_key(chain: Dict[str, Any]) -> Dict[str, Any]:
    """What the approval token hashes beside the files (in place of a spec): the stages' names and specs, in order.
    ``preview.approval_token`` reads the templates of every stage from it (``preview.key_templates``)."""
    key = {"stages": [{"name": stage_name(st, i), "spec": st["spec"]} for i, st in enumerate(chain["stages"], 1)]}
    if "assertions" in chain:
        key["assertions"] = chain["assertions"]
    return copy.deepcopy(key)


def chain_intent(chain: Dict[str, Any]) -> str:
    """What the chain is meant to do, for Claude's visual check of the final pages (the stages, in order)."""
    lines = [f"A CHAIN «{chain_name(chain)}» of {len(chain['stages'])} stage(s), run one after another on each file:"]
    for i, st in enumerate(chain["stages"], 1):
        spec = st["spec"]
        lines.append(f"STAGE {i} — {stage_name(st, i)}")
        lines.append(f"  REQUEST: {spec.get('request') or ''}")
        lines.append(f"  WHAT THE SPEC DOES: {spec.get('summary') or ''}")
        lines += [f"  ASSUMPTION: {a}" for a in spec.get("assumptions") or []]
    lines.append("The AFTER pages show the file once every stage has run.")
    if chain.get("assertions"):
        lines.append("FINAL REQUIREMENTS (must still hold after every stage): " + json.dumps(chain["assertions"]))
    return "\n".join(lines)


# ---- results -----------------------------------------------------------------------------------------

@dataclass
class StageRun:
    index: int                                    # 1-based
    name: str
    spec: Dict[str, Any]
    out_dir: Path
    batch: Any = None                             # batch.BatchResult
    inputs: List[Path] = field(default_factory=list)

    def to_dict(self):
        c = self.batch.counts() if self.batch is not None else {}
        return {"index": self.index, "name": self.name, "folder": str(self.out_dir), "request": self.spec.get("request"),
                "files": len(self.inputs), "counts": c,
                "renderer": getattr(self.batch, "renderer", None), "timings": getattr(self.batch, "timings", {})}


@dataclass
class ChainFile:
    source: Path                                  # the user's file
    out_name: str                                 # unique across the chain (batch.output_names)
    pattern_id: Optional[str] = None
    stage_status: List[str] = field(default_factory=list)   # per stage: PASS | FAIL | EXCEPTION | DRIFTED | NOT_RUN
    results: List[Any] = field(default_factory=list)         # per stage: the stage's FileResult for it, or None
    stopped_at: Optional[int] = None              # the stage (1-based) the file did not get past; None = every stage
    status: str = "PENDING"                       # PASS | FAIL | EXCEPTION
    code: str = ""
    category: str = ""
    reason: str = ""
    explanation: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    delivered_to: Optional[str] = None            # the chain's copy in outputs/, or the source written back in place
    output: Optional[Path] = None                 # the last verified output (a stage's copy, or the chain's)
    noop: bool = False                            # every stage left it as it was
    flags: List[str] = field(default_factory=list)
    outcomes: List[Dict[str, Any]] = field(default_factory=list)  # independent final requirements, after all stages

    @property
    def record_stem(self) -> str:
        return Path(self.out_name or self.source.name).stem

    def to_dict(self):
        return {"source": str(self.source), "out_name": self.out_name, "pattern_id": self.pattern_id,
                "stages": self.stage_status, "stopped_at": self.stopped_at, "status": self.status, "code": self.code,
                "category": self.category, "reason": self.reason, "delivered_to": self.delivered_to,
                "output": str(self.output) if self.output else None, "noop": self.noop, "flags": self.flags,
                "outcomes": self.outcomes}


@dataclass
class ChainResult:
    out_dir: Path
    chain: Dict[str, Any]
    in_place: bool = False
    stages: List[StageRun] = field(default_factory=list)
    files: List[ChainFile] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)      # inputs left out: a template of one of the stages
    renderer: Optional[str] = None
    timings: Dict[str, float] = field(default_factory=dict)
    environment: Dict[str, Any] = field(default_factory=dict)
    started: str = ""
    finished: str = ""
    log_path: Optional[Path] = None               # logs/chain.log
    stopped: Optional[str] = None                 # the chain itself failed: the stage and the error

    @property
    def name(self) -> str:
        return chain_name(self.chain)

    def counts(self) -> Dict[str, int]:
        c = {"PASS": 0, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
        for f in self.files:
            c[f.status] += 1
        return c

    def stage_dirs(self) -> List[Path]:
        return [s.out_dir for s in self.stages]


# ---- the run -----------------------------------------------------------------------------------------

def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _header(result: ChainResult, files: List[Path], workers: int, renderer: str, do_render: bool) -> List[str]:
    stages = ", ".join(f"{s.index} {s.name}" for s in result.stages)
    return [f"Spec Surgeon {__version__} — chain log", f"chain        {result.name} ({len(result.stages)} stage(s): {stages})",
            f"files        {len(files)}" + (f"; skipped the stages' template file(s) {[Path(x).name for x in result.skipped]}"
                                            if result.skipped else ""),
            f"mode         {'write back in place (guarded, stage by stage)' if result.in_place else 'copies: each stage reads the stage before it; outputs/ holds the files that passed every stage'}",
            f"renderer     {renderer}{'' if do_render else ' (rendering off)'}; workers {workers or 'auto'}",
            f"environment  {diag.environment_line(result.environment)}", ""]


def _stop(cf: ChainFile, stage: int, status: str, code: str, reason: str, details: Optional[Dict[str, Any]] = None) -> None:
    from .report import explain_failure
    cf.stopped_at, cf.status, cf.code, cf.reason = stage, status, code, reason
    cf.category, cf.explanation = explain_failure(code, reason)
    cf.details = dict(details or {}, stage=stage)


def run_chain(inputs: Sequence[str], chain: Dict[str, Any], out_dir: Path, workers: int = 0, renderer: str = "auto",
              do_render: bool = True, in_place: bool = False, progress=None) -> ChainResult:
    """Run every stage of ``chain`` in order, each a ``run_batch`` in ``<out>/stage-NN/`` on the files the stage
    before it verified (the pin above); then the chain's own report. Raises ``ChainError`` for an unusable chain and
    ``ValueError`` for inputs inside the results folder — both before anything is created. A stage's own failure (the
    batch stopped) is recorded in ``stopped`` and the chain log, DIAGNOSTICS.md written, and the exception propagates
    (as ``run_batch`` does)."""
    from .batch import check_inputs_outside, expand_inputs, output_names, run_batch, verified_bytes
    from . import guard
    chain = copy.deepcopy(chain)                 # callers cannot weaken requirements during a stage callback
    validate_chain(chain)
    if in_place and chain.get("assertions"):
        raise ChainError("chains with final assertions require copies mode: stage-by-stage write-back cannot "
                         "withhold a file that fails a final requirement")
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    check_inputs_outside(files, out_dir)          # before anything is created: a run never writes where it reads
    templates = chain_templates(chain)
    skipped = [f for f in files if is_template_file(f, templates)]
    files = [f for f in files if f not in skipped]
    result = ChainResult(out_dir=out_dir, chain=chain, in_place=in_place, skipped=[str(f) for f in skipped])
    result.stages = [StageRun(index=i, name=stage_name(st, i), spec=st["spec"], out_dir=out_dir / STAGE_DIR.format(i))
                     for i, st in enumerate(chain["stages"], 1)]
    result.environment = diag.environment()
    result.started = datetime.now().astimezone().isoformat(timespec="seconds")
    (out_dir / diag.LOGS).mkdir(parents=True, exist_ok=True)
    result.log_path = out_dir / diag.LOGS / CHAIN_LOG
    log = diag.RunLog(result.log_path, progress, _header(result, files, workers, renderer, do_render))
    names = output_names(files)
    result.files = [ChainFile(source=f, out_name=names[str(f)]) for f in files]
    current: Dict[str, Path] = {str(cf.source): cf.source for cf in result.files}   # what the next stage reads
    t_start = time.perf_counter()
    stage_label = "start"
    try:
        _empty(out_dir / "outputs")                # a reused folder: nothing of an earlier run survives in outputs/
        for sr in result.stages:
            stage_label = f"stage {sr.index} ({sr.name})"
            _empty(sr.out_dir / "outputs")         # the stage's own copies are this run's only (its inputs lie in
            _empty(sr.out_dir / "failed")          # the stage before it, never here)
            alive = [cf for cf in result.files if cf.stopped_at is None]
            # the pin: stage N+1 reads only the bytes stage N verified
            if sr.index > 1:
                for cf in alive:
                    prev = cf.results[-1]
                    path = current[str(cf.source)]
                    try:
                        actual = _md5(path)
                    except OSError as e:
                        actual = f"unreadable ({e})"
                    if prev is None or actual != prev.output_md5:
                        _stop(cf, sr.index, "EXCEPTION", DRIFT_CODE,
                              f"{path.name} changed between stage {sr.index - 1} and stage {sr.index} (md5 "
                              f"{prev.output_md5 if prev else '?'} verified by stage {sr.index - 1}, {actual} now) — stage "
                              f"{sr.index} did not read it", {"expected_md5": getattr(prev, 'output_md5', None),
                                                               "actual_md5": actual, "path": str(path)})
                        cf.stage_status.append("DRIFTED")
                        cf.results.append(None)
                        log(f"  {cf.source.name}: {cf.reason}")
                alive = [cf for cf in result.files if cf.stopped_at is None]
            sr.inputs = [current[str(cf.source)] for cf in alive]
            log(f"stage {sr.index} of {len(result.stages)} — {sr.name}: {len(sr.inputs)} file(s)"
                + ("" if sr.inputs else " (nothing left to run)"))
            if not sr.inputs:
                for cf in result.files:
                    if cf.stopped_at is not None and len(cf.stage_status) < sr.index:
                        cf.stage_status.append("NOT_RUN")
                        cf.results.append(None)
                continue
            sr.batch = run_batch([str(p) for p in sr.inputs], "edit-spec", {}, sr.out_dir, workers=workers,
                                 renderer=renderer, do_render=do_render, in_place=in_place, progress=log,
                                 spec=sr.spec)
            result.renderer = result.renderer or sr.batch.renderer
            by_input = {str(Path(fr.source).resolve()): fr for fr in sr.batch.files}
            added_before = {cf.source: set() for cf in alive}
            for cf in alive:
                for fr in cf.results:
                    if fr is not None and fr.plan:
                        added_before[cf.source] |= set(fr.plan.get("members_added") or [])
            for cf in alive:
                fr = by_input.get(str(current[str(cf.source)].resolve()))
                cf.results.append(fr)
                if fr is None:                     # the batch has no result for it (never happens; said, not hidden)
                    cf.stage_status.append("EXCEPTION")
                    _stop(cf, sr.index, "EXCEPTION", "internal-error",
                          f"stage {sr.index} returned no result for {cf.source.name}")
                    continue
                cf.pattern_id = cf.pattern_id or fr.fingerprint.get("pattern_id")
                cf.stage_status.append(fr.status)
                touched = set((fr.plan or {}).get("intended_parts") or []) | set((fr.plan or {}).get("members_removed") or [])
                hit = sorted(touched & added_before[cf.source])
                if hit:
                    earlier = [s.name for s in result.stages[:sr.index - 1]]
                    cf.flags.append(f"stage {sr.index} ({sr.name}) changes {', '.join(hit)}, which an earlier stage "
                                    f"({' / '.join(earlier)}) added from its template — the template's part is not "
                                    f"what the file shows now")
                if fr.status == "PASS":
                    if not in_place:
                        current[str(cf.source)] = Path(fr.output)
                    cf.output = Path(fr.output) if fr.output else cf.output
                    continue
                if fr.status == "FAIL":
                    bad = [c for c in fr.record.checks if c.status == "FAIL"]
                    _stop(cf, sr.index, "FAIL", "verify-fail",
                          f"stage {sr.index} ({sr.name}): " + "; ".join(f"{c.id}: {c.summary}" for c in bad),
                          {"failed_checks": [c.id for c in bad]})
                else:
                    e = fr.exception or {}
                    _stop(cf, sr.index, "EXCEPTION", e.get("code", "exception"),
                          f"stage {sr.index} ({sr.name}): {e.get('reason', '')}", dict(e.get("details") or {}))
                if in_place and sr.index > 1:
                    cf.reason += (f" — stages 1…{sr.index - 1} were written back over the file; stage {sr.index} "
                                  f"and later were not applied")
                elif in_place:
                    cf.reason += " — the file was left untouched"
        stage_label = "deliver"
        _deliver(result, current, in_place, verified_bytes, guard, log)
        result.timings["total"] = time.perf_counter() - t_start
        stage_label = "reports"
        result.finished = datetime.now().astimezone().isoformat(timespec="seconds")
        from .report import write_chain_diagnostics, write_chain_report
        write_chain_report(result)
        write_chain_diagnostics(result)
        c = result.counts()
        log(f"chain finished: PASS {c['PASS']} · FAIL {c['FAIL']} · EXCEPTION {c['EXCEPTION']} "
            f"({result.timings['total']:.1f}s)")
        return result
    except BaseException as e:
        result.stopped = f"{stage_label}: {type(e).__name__}: {e}"
        log.detail(f"THE CHAIN STOPPED during {stage_label}: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        try:
            from .report import write_chain_diagnostics
            result.finished = datetime.now().astimezone().isoformat(timespec="seconds")
            write_chain_diagnostics(result)
        except Exception as e2:  # noqa: BLE001 — the original error is the one that matters
            log.detail(f"(DIAGNOSTICS.md could not be written either: {type(e2).__name__}: {e2})")
        raise
    finally:
        log.close()


def _empty(folder: Path) -> None:
    """Remove a results subfolder an earlier run of the same folder left (``outputs/``, ``failed/``): the chain's
    guarantee is about THIS run. Only ever called on folders under the chain's own results folder, after
    ``check_inputs_outside`` proved no input lies there."""
    if folder.is_dir():
        shutil.rmtree(folder)


def _deliver(result: ChainResult, current: Dict[str, Path], in_place: bool, verified_bytes, guard, log) -> None:
    """The chain's own delivery: in copies mode, every file that passed every stage is copied — re-hashed against the
    last stage's verified bytes — into ``<out>/outputs/``; in place the last stage already wrote it back."""
    t0 = time.perf_counter()
    n = len(result.stages)
    for cf in result.files:
        if cf.stopped_at is not None:
            while len(cf.stage_status) < n:
                cf.stage_status.append("NOT_RUN")
                cf.results.append(None)
            continue
        if not cf.results or any(fr is None for fr in cf.results):
            _stop(cf, n, "EXCEPTION", "internal-error", "no stage result")
            continue
        cf.noop = all(bool(fr.noop) for fr in cf.results)
        last = cf.results[-1]
        if in_place:
            written = [fr.delivered_to for fr in cf.results if fr.delivered_to]
            cf.status = "PASS"
            cf.delivered_to = written[-1] if written else None
            cf.output = Path(last.output) if last.output else None
            continue
        try:
            data = verified_bytes(last)
        except (guard.GuardError, OSError) as e:
            _stop(cf, n, "EXCEPTION", DELIVER_CODE, f"{e}")
            log(f"  {cf.source.name}: not delivered — {e}")
            continue
        if result.chain.get("assertions"):
            from .verify.outcomes import assert_outcomes
            cf.outcomes = assert_outcomes(data, result.chain["assertions"])
            failures = [entry for entry in cf.outcomes if entry["status"] != "PASS"]
            if failures:
                _stop(cf, n, "FAIL", OUTCOME_CODE,
                      "final requirements after all stages: " + "; ".join(entry["summary"] for entry in failures),
                      {"outcomes": cf.outcomes, "phase": "final requirements"})
                cf.category = "Final Requirements Not Satisfied"
                cf.explanation = "Each stage passed, but the final document does not satisfy the chain's explicit " \
                                 "requirements. Revise the operations or their order and rerun on the original files."
                log(f"  {cf.source.name}: not delivered — {cf.reason}")
                continue
        dest = result.out_dir / "outputs" / cf.out_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        cf.status, cf.delivered_to, cf.output = "PASS", str(dest), dest
    result.timings["deliver"] = time.perf_counter() - t0


def load_chain_file(path: Path) -> Dict[str, Any]:
    """A chain from a JSON file (a saved chain, or ``chain.json`` of a run or preview); relative template paths in its
    stages resolve against the file's folder, as ``editspec.load_spec`` does for a spec."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # a run's chain.json wraps the chain beside the stages' RESULTS (``write_chain_report``: ``chain``, ``stages``,
    # ``files``, …); a saved chain and a preview's chain.json are the chain itself
    if isinstance(data, dict) and isinstance(data.get("chain"), dict) and isinstance(data["chain"].get("stages"), list):
        chain = data["chain"]
    else:
        chain = data
    if not isinstance(chain, dict):
        raise ChainError(f"{path} does not hold a chain")
    for st in chain.get("stages") or []:
        for o in (st.get("spec") or {}).get("ops") or [] if isinstance(st, dict) else []:
            params = o.get("params") if isinstance(o, dict) else None
            if isinstance(params, dict) and isinstance(params.get("template"), str) \
                    and not Path(params["template"]).is_absolute():
                params["template"] = str((path.parent / params["template"]).resolve())
    validate_chain(chain)
    return chain
