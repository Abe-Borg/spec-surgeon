"""Batch orchestration — the same pipeline for 1 file or 100, for a named job or an EditSpec:

    acquire → fingerprint → plan (predict first) → apply → verify (V1–V5, V8)
            → render (source + output) → V6/V7 → deliver (guarded, V9) → records + batch report + set_aside.json

Transform+verify runs in a process pool; rendering runs through the chosen backend (Word is one
instance, LibreOffice is N parallel profiles). Exceptions never stop the batch; they are written to
<out>/exceptions/ and summarized in <out>/set_aside.json with a structured diagnosis for the next pass.

``<out>/outputs/`` holds only files that passed every check, V9 included. The worker verifies the output in memory
and writes it only after the suite has run (a verifier that raises leaves nothing behind but the exception record).
An output that fails verification, or whose V9 guard fails afterwards (the source drifted during the run, or the
in-place write-back was refused), is moved to ``<out>/failed/`` (kept for diagnosis, never delivered) so that copying
the outputs folder can never pick up a file the suite rejected.

Output identity. Every input gets an output name that is unique across the whole batch (``output_names``: inputs in
different folders may share a file name; a name an input really has is never handed to another input; Windows
compares names case-insensitively), and its plan, record and exception files carry the same stem. Delivery is bound
to the bytes the suite verified: the worker records their md5 and delivery re-hashes the output file before writing
anything, in place or not. A run never reads where it writes: an input inside the results folder is refused before
anything is created (``check_inputs_outside``) — copies mode would otherwise overwrite it.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from . import guard
from .fingerprint import fingerprint
from .editspec import apply_spec, is_template_file, plan_spec, spec_templates, validate_spec
from .jobs import get_job, JobException
from .package import DocxPackage
from .verify import run_suite, VerificationRecord, CheckResult, GEOM_TOL_PT
from .verify import geometry as geom
from . import render as render_mod


@dataclass
class FileResult:
    source: Path
    identity: Optional[guard.Identity] = None
    fingerprint: Dict[str, Any] = field(default_factory=dict)
    plan: Optional[Dict[str, Any]] = None
    exception: Optional[Dict[str, Any]] = None
    output: Optional[Path] = None
    record: Optional[VerificationRecord] = None
    delivered_to: Optional[str] = None
    timings: Dict[str, float] = field(default_factory=dict)
    noop: bool = False
    out_name: str = ""                       # the output's file name, unique across the batch (``output_names``)
    output_md5: Optional[str] = None         # md5 of the bytes the suite verified; delivery re-checks the file

    @property
    def record_stem(self) -> str:
        """The stem of this file's plan/record/exception files — the output name's, so two inputs that share a
        file name never share a record either."""
        return Path(self.out_name or self.source.name).stem

    @property
    def status(self) -> str:
        if self.exception:
            return "EXCEPTION"
        if self.record is None:
            return "PENDING"
        return "PASS" if self.record.passed else "FAIL"


@dataclass
class BatchResult:
    out_dir: Path
    job: str
    params: Dict[str, Any]
    files: List[FileResult] = field(default_factory=list)
    spec: Optional[Dict[str, Any]] = None
    skipped: List[str] = field(default_factory=list)      # inputs left out (the spec's own template files)
    timings: Dict[str, float] = field(default_factory=dict)
    renderer: Optional[str] = None
    contact_sheet: Optional[Path] = None

    def counts(self) -> Dict[str, int]:
        c = {"PASS": 0, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
        for f in self.files:
            c[f.status] += 1
        return c


def load_set_aside(path_or_dir: Union[str, Path]) -> Dict[str, Any]:
    """Load ``set_aside.json`` from a file path, a batch output folder, or its ``run/`` subfolder."""
    p = Path(path_or_dir)
    candidates = [p] if p.is_file() else [p / "set_aside.json", p / "run" / "set_aside.json"]
    for c in candidates:
        if c.is_file():
            data = json.loads(c.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "files" in data:
                return data
    raise FileNotFoundError(f"no set_aside.json found in {path_or_dir}")


# ---- input expansion -------------------------------------------------------------------------------

def expand_inputs(inputs: Sequence[str]) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            files += sorted(x for x in p.iterdir() if x.suffix.lower() == ".docx" and not x.name.startswith("~$"))
        elif any(ch in item for ch in "*?["):
            files += sorted(Path(x) for x in glob.glob(item) if x.lower().endswith(".docx") and not Path(x).name.startswith("~$"))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(item)
    seen = set()
    out = []
    for f in files:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            out.append(f)
    return out


def output_names(files: Sequence[Path]) -> Dict[str, str]:
    """One output name per input, unique across the batch however the inputs are named.

    An input keeps its own name when it is the first with it; a later input with the same name (another folder)
    gets ``stem (n)suffix`` for the first n that no input really has and no earlier output took — so ``a/Spec.docx``,
    ``b/Spec.docx`` and ``c/Spec (1).docx`` come out as ``Spec.docx``, ``Spec (2).docx`` and ``Spec (1).docx``, never
    two outputs at one path with one of them silently replacing the other. Names compare case-insensitively (Windows
    does). Keyed by ``str(path)``, as the worker results are."""
    reserved = {f.name.casefold() for f in files}      # every real name, before any generated one
    assigned: set = set()
    out: Dict[str, str] = {}
    for f in files:
        key = f.name.casefold()
        if key not in assigned:
            name = f.name
        else:
            n = 1
            while f"{f.stem} ({n}){f.suffix}".casefold() in reserved or f"{f.stem} ({n}){f.suffix}".casefold() in assigned:
                n += 1
            name = f"{f.stem} ({n}){f.suffix}"
        assigned.add(name.casefold())
        out[str(f)] = name
    return out


def check_inputs_outside(files: Sequence[Path], out_dir: Path) -> None:
    """Refuse a batch whose inputs lie inside the results folder — before anything is created or written.

    ``outputs/`` and ``failed/`` are written and emptied by the run; an input under the results folder (a previous
    run's output picked as this run's input with the same results folder, say) could be overwritten by its own
    edited copy and then moved to ``failed/`` — an input that is gone. The rule is simpler than tracking which
    paths collide: a run never reads where it writes."""
    root = Path(out_dir).resolve()
    inside = []
    for f in files:
        r = Path(f).resolve()
        if r == root or root in r.parents:
            inside.append(str(f))
    if inside:
        raise ValueError(f"{len(inside)} input file(s) lie inside the results folder {out_dir}: "
                         f"{[Path(x).name for x in inside[:6]]}{' …' if len(inside) > 6 else ''}. A run never writes "
                         f"where it reads — choose a results folder outside the input folders (nothing was written).")


# ---- worker (runs in a subprocess) ------------------------------------------------------------------

def _process_one(path: str, job_name: str, params: Dict[str, Any], out_dir: str, out_name: Optional[str] = None,
                 spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    t0 = time.perf_counter()
    res: Dict[str, Any] = {"source": path, "timings": {}}
    try:
        src = Path(path)
        identity = guard.acquire(src)
        res["identity"] = identity.to_dict()
        pkg = DocxPackage(src.read_bytes(), name=str(src))
        res["fingerprint"] = fingerprint(pkg)
        res["timings"]["acquire+fingerprint"] = time.perf_counter() - t0

        t1 = time.perf_counter()
        try:
            if spec is not None:
                plan, fragments = plan_spec(pkg, spec, res["fingerprint"].get("pattern_id"))
            else:
                job = get_job(job_name)
                plan = job.plan(pkg, params)
        except JobException as e:
            res["exception"] = e.to_dict()
            res["timings"]["plan"] = time.perf_counter() - t1
            return res
        res["plan"] = plan.to_dict()
        res["timings"]["plan"] = time.perf_counter() - t1

        t2 = time.perf_counter()
        if spec is not None:
            work = apply_spec(pkg, fragments)
            res["noop"] = not work.is_modified()
            out_bytes = work.output_bytes()
        else:
            subs = job.apply(pkg, plan)
            if not subs:
                res["noop"] = True
                out_bytes = pkg.data
            else:
                out_bytes = pkg.repack(subs)
        res["timings"]["apply"] = time.perf_counter() - t2

        # verify in memory first; the output is written only once the suite has run, so a verifier that raises
        # leaves nothing in outputs/ (the exception record says what happened), and delivery is bound to these bytes
        t3 = time.perf_counter()
        out_path = Path(out_dir) / "outputs" / (out_name or src.name)
        rec = run_suite(pkg.data, out_bytes, plan.to_dict(), str(src), str(out_path))
        res["record"] = rec.to_dict()
        res["timings"]["verify"] = time.perf_counter() - t3
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(out_bytes)
        res["output"] = str(out_path)
        res["output_md5"] = hashlib.md5(out_bytes).hexdigest()
    except JobException as e:  # apply-time refusal (an internal cross-check failed): never deliver
        res["exception"] = e.to_dict()
    except Exception as e:  # anything unexpected is an exception record, never a crashed batch
        res["exception"] = {"code": getattr(e, "code", None) or "crash", "reason": f"{type(e).__name__}: {e}",
                            "details": {"traceback": traceback.format_exc()}}
    res["timings"]["total"] = time.perf_counter() - t0
    return res


# ---- failed outputs ----------------------------------------------------------------------------------

def verified_bytes(fr: FileResult) -> bytes:
    """The output file's bytes, only if they are the ones the suite verified (``output_md5``); anything else —
    the file replaced, truncated, edited since — is a GuardError, never delivered."""
    data = fr.output.read_bytes()
    if fr.output_md5 and hashlib.md5(data).hexdigest() != fr.output_md5:
        raise guard.GuardError(f"{fr.output}: the output file is not the one the suite verified (its content "
                               f"changed after verification); not delivered")
    return data


def quarantine_failed_output(fr: FileResult, out_dir: Path) -> Path:
    """Move a failed output out of outputs/ into failed/ (diagnosis only); the record keeps pointing at the file."""
    dest_dir = Path(out_dir) / "failed"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / fr.output.name
    if dest.exists():
        dest.unlink()
    fr.output.replace(dest)
    fr.output = dest
    if fr.record is not None:
        fr.record.output = str(dest)
    return dest


# ---- V7 ----------------------------------------------------------------------------------------------

def _template_op_params(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The params of the plan's templating op (its section mapping and kinds, as the spec states them)."""
    for o in plan.get("ops", []) or []:
        if o.get("op") == "apply_header_footer_template" and isinstance(o.get("params"), dict) \
                and isinstance(o["params"].get("template_sections"), dict):
            return o["params"]
    return None


def geometry_check(plan: Dict[str, Any], g: "geom.GeometryResult",
                   tg: Optional["geom.TemplateGeometry"] = None) -> CheckResult:
    """V7 from the plan's geometry reference:

    * ``source`` (default) — word positions of the output vs the source render, asserted on every page not
      declared expected-to-change; for a content edit with diffs the numbers are informational (text reflows);
    * ``none`` — the op changes layout by design (a font or page-setup change reflows everything); N/A with the
      plan's reason, and a FAIL when the plan gives no reason;
    * ``template`` — the output's headers/footers rendered alone vs the template's, page for page (output section k
      ↔ template section map[k], first/even/odd variant ↔ the same variant; verify/geometry.hf_only_copy): a region
      the template leaves blank must be blank, any other must be found at the template's position, its outer edge
      and every shared word within tolerance; FAIL when either render (or the op's section map) is missing.
    """
    ref = plan.get("geometry_reference", "source")
    expected_pages = plan.get("geometry_pages_expected_to_change", [])
    ok = g.worst_dy_asserted <= GEOM_TOL_PT and g.worst_dx_asserted <= GEOM_TOL_PT and g.ref_pages == g.out_pages
    moved = [p.page for p in g.pages if not p.expected_change and (p.max_dy > GEOM_TOL_PT or p.max_dx > GEOM_TOL_PT)]
    info = (f"max |Δy| {g.worst_dy_asserted:.2f}pt, max |Δx| {g.worst_dx_asserted:.2f}pt; pages with movement "
            f"{moved or 'none'}; pages {g.ref_pages}→{g.out_pages}")
    if ref == "none":
        reason = (plan.get("geometry_reason") or "").strip()
        if not reason:
            return CheckResult("V7", "Geometry assertion", "FAIL",
                               "plan declares geometry_reference 'none' without a reason; a layout reference may only "
                               "be dropped for a stated reason", g.to_dict())
        return CheckResult("V7", "Geometry assertion", "NA",
                           f"N/A — no layout reference by design: {reason}. Informational: {info}", g.to_dict())
    if ref == "template":
        if tg is None:
            return CheckResult("V7", "Geometry assertion", "FAIL",
                               f"geometry_reference 'template' ({plan.get('geometry_template')}) but no comparison could "
                               f"be made (template render, headers/footers-only output render or the templating op's "
                               f"section map missing); refusing to pass without the reference", g.to_dict())
        bad = [p for p in tg.pages if p.problems]
        return CheckResult(
            "V7", "Geometry assertion", "FAIL" if bad else "PASS",
            f"reference = the template's headers/footers rendered alone ({Path(plan.get('geometry_template', '')).name}), "
            f"page for page against the output's ({'/'.join(tg.kinds)}): max |Δy| {tg.worst_dy:.2f}pt over "
            f"{sum(p.anchors for p in tg.pages)} anchor word(s) and every region edge on {len(tg.pages)} page(s) "
            f"(tol {GEOM_TOL_PT}pt)"
            + ("; " + "; ".join(f"page {p.page} (vs template page {p.template_page}): " + ", ".join(p.problems)
                                for p in bad[:4]) + (f"; … {len(bad) - 4} more page(s)" if len(bad) > 4 else "")
               if bad else ""),
            {**tg.to_dict(), "source_comparison": g.to_dict()})
    if ref != "source":
        return CheckResult("V7", "Geometry assertion", "FAIL", f"unknown geometry_reference {ref!r}", g.to_dict())
    if plan.get("mode") in ("content", "untracked") and plan.get("diffs"):
        # A wording change reflows the text that follows it; the source render is not a layout
        # reference for those pages. The numbers are recorded, not asserted.
        return CheckResult("V7", "Geometry assertion", "NA",
                           f"N/A for a content edit (text reflows after the change). Informational: {info}", g.to_dict())
    return CheckResult(
        "V7", "Geometry assertion", "PASS" if ok else "FAIL",
        f"reference = source render; max |Δy| {g.worst_dy_asserted:.2f}pt, max |Δx| {g.worst_dx_asserted:.2f}pt on asserted pages "
        f"(tol {GEOM_TOL_PT}pt); pages excluded as expected-to-change: {expected_pages or 'none'}"
        + (f"; MOVED pages {moved}" if moved else "") + ("" if g.ref_pages == g.out_pages else "; PAGE COUNT CHANGED"),
        g.to_dict())


# ---- the batch -------------------------------------------------------------------------------------

def run_batch(inputs: Sequence[str], job_name: str, params: Dict[str, Any], out_dir: Path,
              workers: int = 0, renderer: str = "auto", do_render: bool = True, in_place: bool = False,
              progress=None, spec: Optional[Dict[str, Any]] = None) -> BatchResult:
    """Run a named job (``job_name`` + ``params``) or an EditSpec (``spec``; then ``job_name`` is only a label)."""
    out_dir = Path(out_dir)
    if spec is not None:
        validate_spec(spec)          # fail early: questions, unsupported, unknown ops — before any file is read
        job_name = job_name or "edit-spec"
        params = {"request": spec.get("request"), "summary": spec.get("summary")}
    else:
        get_job(job_name)  # fail early on an unknown job
    files = expand_inputs(inputs)
    check_inputs_outside(files, out_dir)      # before anything is created: a run never writes where it reads
    for sub in ("outputs", "records", "exceptions", "plans", "render"):
        (out_dir / sub).mkdir(parents=True, exist_ok=True)
    skipped_templates: List[Path] = []
    if spec is not None:
        tlist = spec_templates(spec)
        skipped_templates = [f for f in files if is_template_file(f, tlist)]
        files = [f for f in files if f not in skipped_templates]
    batch = BatchResult(out_dir=out_dir, job=job_name, params=params, spec=spec)
    batch.skipped = [str(f) for f in skipped_templates]
    t_start = time.perf_counter()
    log = progress or (lambda msg: None)

    # 1-2. acquire, fingerprint, plan, apply, verify — in parallel
    workers = workers or max(1, min(os.cpu_count() or 2, len(files), 8))
    results: Dict[str, Dict[str, Any]] = {}
    log(f"{len(files)} file(s); transform+verify with {workers} worker(s)"
        + (f"; skipped the spec's template file(s) {[Path(x).name for x in batch.skipped]}" if batch.skipped else ""))
    out_names = output_names(files)       # unique across the batch, whatever the inputs are called
    if workers == 1 or len(files) == 1:
        for f in files:
            results[str(f)] = _process_one(str(f), job_name, params, str(out_dir), out_names[str(f)], spec)
            log(f"  done {f.name}")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_process_one, str(f), job_name, params, str(out_dir), out_names[str(f)], spec): f
                    for f in files}
            for fut in as_completed(futs):
                r = fut.result()
                results[r["source"]] = r
                log(f"  done {Path(r['source']).name}")
    batch.timings["transform+verify"] = time.perf_counter() - t_start

    for f in files:
        r = results[str(f)]
        fr = FileResult(source=f, timings=r.get("timings", {}), noop=r.get("noop", False),
                        out_name=out_names[str(f)], output_md5=r.get("output_md5"))
        if r.get("identity"):
            fr.identity = guard.Identity(**r["identity"])
        fr.fingerprint = r.get("fingerprint", {})
        fr.plan = r.get("plan")
        fr.exception = r.get("exception")
        if r.get("output"):
            fr.output = Path(r["output"])
        if r.get("record"):
            fr.record = VerificationRecord.from_dict(r["record"])
        batch.files.append(fr)

    # 3-4. render + geometry
    t_r = time.perf_counter()
    backend = render_mod.pick(renderer) if do_render else None
    batch.renderer = backend
    renderable = [fr for fr in batch.files if fr.output is not None and fr.record is not None]
    if backend and renderable:
        log(f"rendering {2 * len(renderable)} PDF(s) with {backend}")
        # a source whose name another source has too is rendered from a copy carrying its unique output name, so
        # two sources called Spec.docx never share one Spec.pdf (V7 would compare one of them with the other's)
        src_in: Dict[Path, Path] = {}
        for fr in renderable:
            if fr.out_name and fr.out_name != fr.source.name:
                cp = out_dir / "render" / "source-in" / fr.out_name
                cp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(fr.source, cp)
                src_in[cp] = fr.source
            else:
                src_in[fr.source] = fr.source
        rendered = render_mod.render_pdfs(list(src_in), out_dir / "render" / "source", backend, workers, log=log)
        src_pdfs = {src_in[k]: v for k, v in rendered.items() if k in src_in}
        out_pdfs = render_mod.render_pdfs([fr.output for fr in renderable], out_dir / "render" / "output", backend,
                                          workers, log=log)
        templates = sorted({(fr.plan or {}).get("geometry_template") for fr in renderable
                            if (fr.plan or {}).get("geometry_reference") == "template" and (fr.plan or {}).get("geometry_template")})
        tpl_pdfs: Dict[str, Path] = {}
        if templates:
            log(f"rendering {len(templates)} template(s) (headers/footers alone) with {backend}")
            hf_docs = {geom.hf_only_copy(Path(t), out_dir / "render" / "template" /
                                         f"{k:02d} {Path(t).stem} (headers-footers only).docx")[0]: str(Path(t))
                       for k, t in enumerate(templates)}
            rendered = render_mod.render_pdfs(list(hf_docs), out_dir / "render" / "template", backend, 1, log=log)
            tpl_pdfs = {hf_docs[k]: v for k, v in rendered.items() if k in hf_docs}
        # V7 template mode compares headers/footers-only renders of the output and the template, page for page
        out_hf: Dict[Path, Tuple[Path, int]] = {}
        templated = [fr for fr in renderable if (fr.plan or {}).get("geometry_reference") == "template"]
        if templated:
            hf_dir = out_dir / "render" / "output-hf"
            copies = {}
            for fr in templated:
                path, n = geom.hf_only_copy(fr.output, hf_dir / f"{Path(fr.output).stem} (headers-footers only).docx")
                copies[path] = (fr.output, n)
            log(f"rendering {len(copies)} headers/footers-only output(s) with {backend}")
            for k, v in render_mod.render_pdfs(list(copies), hf_dir, backend, workers, log=log).items():
                if k in copies:
                    out_hf[copies[k][0]] = (v, copies[k][1])
        pages_dir = out_dir / "render" / "pages"
        for fr in renderable:
            sp, op = src_pdfs.get(fr.source), out_pdfs.get(fr.output)
            if not sp or not op:
                fr.record.set(CheckResult("V6", "Render and look", "FAIL", f"render failed ({backend})"))
                fr.record.set(CheckResult("V7", "Geometry assertion", "NA", "no render"))
                continue
            expected_pages = (fr.plan or {}).get("geometry_pages_expected_to_change", [])
            g = geom.compare_pdfs(sp, op, expected_pages)
            look_pages = sorted({1} | {p.page for p in g.pages if p.max_dy > GEOM_TOL_PT or p.words_only_in_out or p.words_only_in_ref})
            pngs = geom.rasterize(op, pages_dir, look_pages[:6])
            fr.record.set(CheckResult(
                "V6", "Render and look", "PASS",
                f"renderer {backend}; pages {g.ref_pages}→{g.out_pages}; rasterized pages {look_pages[:6]} → "
                f"{pages_dir.name}/; page-count delta is not evidence of body change (XML checks are character truth)",
                {"renderer": backend, "source_pdf": str(sp), "output_pdf": str(op), "pages": [str(p) for p in pngs]}))
            tg = None
            tref = (fr.plan or {}).get("geometry_template")
            top = _template_op_params(fr.plan or {})
            if ((fr.plan or {}).get("geometry_reference") == "template" and tref and str(Path(tref)) in tpl_pdfs
                    and fr.output in out_hf and top is not None):
                hf_pdf, n_sections = out_hf[fr.output]
                tg = geom.compare_template_bands(tpl_pdfs[str(Path(tref))], hf_pdf,
                                                 geom.template_section_map(top["template_sections"], n_sections),
                                                 top.get("kinds", ["header", "footer"]), GEOM_TOL_PT)
            fr.record.set(geometry_check(fr.plan or {}, g, tg))
        try:
            batch.contact_sheet = geom.contact_sheet([out_pdfs[fr.output] for fr in renderable if fr.output in out_pdfs],
                                                    out_dir / "render" / "contact_sheet.png")
        except Exception as e:  # pragma: no cover
            log(f"contact sheet failed: {e}")
    else:
        for fr in renderable:
            why = "rendering disabled" if not do_render else "no renderer available (install Word+pywin32 or LibreOffice)"
            fr.record.set(CheckResult("V6", "Render and look", "NA", why))
            fr.record.set(CheckResult("V7", "Geometry assertion", "NA", why))
    batch.timings["render+geometry"] = time.perf_counter() - t_r

    # 5. deliver (guarded)
    t_d = time.perf_counter()
    for fr in batch.files:
        if fr.record is None or fr.output is None:
            continue
        if not fr.record.passed:
            moved = quarantine_failed_output(fr, out_dir)
            fr.record.set(CheckResult("V9", "Overwrite guard", "NA",
                                      f"not delivered: verification failed; the output was moved out of outputs/ to "
                                      f"{moved.parent.name}/{moved.name} (diagnosis only)"))
            continue
        if fr.noop:
            fr.record.set(CheckResult("V9", "Overwrite guard", "NA", "no-op: output identical to source; nothing written back"))
            continue
        if in_place:
            try:
                new_id = guard.write_back(fr.identity, verified_bytes(fr))
                fr.delivered_to = new_id.path
                fr.record.set(CheckResult("V9", "Overwrite guard", "PASS",
                                          f"source re-checked (size {fr.identity.size}, mtime unchanged); no Word lock file; "
                                          f"atomic replace; new md5 {new_id.md5}"))
            except (guard.GuardError, OSError) as e:
                # V9 failed after V1–V8/V10 passed: the output is still a copy that was never delivered — out of
                # outputs/ it goes, like any other failed check, so the folder keeps its guarantee. (An OSError
                # here is one the guard did not translate — reading the output copy, say; it is this file's
                # failure, never the batch's.)
                moved = quarantine_failed_output(fr, out_dir)
                fr.record.set(CheckResult("V9", "Overwrite guard", "FAIL",
                                          f"{e} — not delivered; the output was moved out of outputs/ to "
                                          f"{moved.parent.name}/{moved.name} (diagnosis only; run again once the source is "
                                          f"closed and unchanged)"))
        else:
            d = guard.drifted(fr.identity)
            try:
                verified_bytes(fr)                 # the copy in outputs/ is the one the suite verified
            except (guard.GuardError, OSError) as e:
                d = d or str(e)
            if not d:
                fr.delivered_to = str(fr.output)
                fr.record.set(CheckResult("V9", "Overwrite guard", "PASS",
                                          "source unchanged since acquisition; output written to " + str(fr.output)))
            else:
                moved = quarantine_failed_output(fr, out_dir)
                fr.record.set(CheckResult("V9", "Overwrite guard", "FAIL",
                                          f"SOURCE DRIFTED during the run: {d} — the output was built from a stale source and "
                                          f"moved out of outputs/ to {moved.parent.name}/{moved.name} (diagnosis only); run "
                                          f"again on the current file"))
    batch.timings["deliver"] = time.perf_counter() - t_d

    # 6. records, exceptions, plans — named after the output (unique across the batch), never after the source alone
    for fr in batch.files:
        stem = fr.record_stem
        if fr.plan is not None:
            (out_dir / "plans" / f"{stem}.plan.json").write_text(json.dumps(fr.plan, indent=2), encoding="utf-8")
        if fr.exception is not None:
            (out_dir / "exceptions" / f"{stem}.exception.json").write_text(json.dumps({
                "source": str(fr.source), "job": job_name, "params": params,
                **({"spec": spec} if spec is not None else {}),
                "fingerprint": fr.fingerprint, "exception": fr.exception}, indent=2), encoding="utf-8")
        if fr.record is not None:
            (out_dir / "records" / f"{stem}.record.md").write_text(fr.record.to_markdown(), encoding="utf-8")
            (out_dir / "records" / f"{stem}.record.json").write_text(json.dumps(fr.record.to_dict(), indent=2), encoding="utf-8")
    batch.timings["total"] = time.perf_counter() - t_start
    from .report import write_batch_report
    write_batch_report(batch)
    return batch
