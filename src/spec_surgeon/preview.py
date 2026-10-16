"""Preview and approval — what the user sees before anything runs on the batch (docs/NL_SURGICAL_EDITOR_PLAN.md §7;
Phase 4).

``make_preview(spec, inputs, out_dir)``:
1. plans the spec on every file (planner.plan_batch — summaries and exceptions, nothing written);
2. picks sample files — the first plannable file of each structure pattern, up to ``review.sample_files``
   (default 2, at most 3, but always one per pattern up to 3) — and runs plan → apply → verify on them in memory,
   writing the outputs into ``out_dir/samples`` (never over a source) under names unique across the samples
   (``batch.output_names``: two folders' ``Spec.docx`` are ``Spec.docx`` and ``Spec (1).docx``); the preview text (and
   the note Claude gets) names each sample by ``sample_labels`` — ``a/Spec.docx`` and ``b/Spec.docx``, as Claude's
   visual check labels its pages;
3. renders source and output of each sample (Word when available, else LibreOffice — a visible warning says so:
   decision on §14.5, "fall back with a warning", never silently) and composes side-by-side PNGs of page 1 and of
   the first page whose words moved;
4. writes ``spec.json`` and ``PREVIEW.md`` and computes an **approval token** — a hash of the spec and of every
   planned file's path, size and mtime. ``run-spec --approval TOKEN`` refuses to run when the spec or any file
   changed since the preview — and says which (``approval_inputs`` are kept in ``preview.json``;
   ``explain_approval_change`` compares them with the files as they are now). ``do`` re-checks it after the y/N (or
   ``--yes``) and before the run (``changed_since_preview``) — for a custom transform with ``generated_key`` in place
   of the spec.

Diagnostics: ``logs/preview.log`` has every file's plan or refusal (with the stage, the op and the line of code), each
sample's verification with the failing checks' specifics, and what the renderer said; a sample that fails in an
unexpected way is that sample's exception record (classified like a run's), never a lost preview; a page comparison
or picture that fails is a warning. A custom transform's preview (``preview_generated``) keeps the same log.

``render_samples`` keeps each sample's PDFs and the pages worth looking at (``look_pages``) for Claude's visual check
(``vision.review_samples``, advisory); ``write_preview_files`` rewrites PREVIEW.md and preview.json when it arrives
(``approval_inputs`` stay in preview.json).
"""
from __future__ import annotations

import hashlib
import json
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .editspec import apply_spec, plan_spec
from .fingerprint import fingerprint
from .package import DocxPackage
from .planner import BatchPlan, plan_batch


def approval_inputs(spec: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """What the approval token is a hash of, as data: the spec's digest, every planned file's size and mtime, and every
    template's size, mtime and md5 — kept beside the preview so a refused run can say exactly what changed."""
    from .editspec import spec_templates
    spec_text = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    out: Dict[str, Any] = {"spec": hashlib.sha256(spec_text.encode("utf-8")).hexdigest()[:16], "files": {},
                           "templates": {}}
    for f in sorted(Path(x).resolve() for x in files):
        try:
            st = f.stat()
            out["files"][str(f)] = [st.st_size, st.st_mtime_ns]
        except OSError:
            out["files"][str(f)] = "missing"
    for t in sorted(set(spec_templates(spec) if isinstance(spec, dict) else [])):
        try:
            st = t.stat()
            out["templates"][str(t)] = [st.st_size, st.st_mtime_ns, hashlib.md5(t.read_bytes()).hexdigest()]
        except OSError:
            out["templates"][str(t)] = "missing"
    return out


def approval_token(spec: Dict[str, Any], files: Sequence[Path]) -> str:
    """A hash of the spec, of every planned file's path, size and mtime, and of every template file the spec reads
    (path, size, mtime and content hash): the template is an input like the source (CLAUDE.md rule 12), so a
    template edited after the preview voids the approval exactly as an edited spec file does."""
    from .editspec import spec_templates
    h = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for f in sorted(Path(x).resolve() for x in files):
        st = f.stat()
        h.update(f"\n{f}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8"))
    for t in sorted(set(spec_templates(spec) if isinstance(spec, dict) else [])):
        try:
            st = t.stat()
            digest = hashlib.md5(t.read_bytes()).hexdigest()
            h.update(f"\ntemplate {t}|{st.st_size}|{st.st_mtime_ns}|{digest}".encode("utf-8"))
        except OSError:
            h.update(f"\ntemplate {t}|missing".encode("utf-8"))
    return h.hexdigest()[:16]


def generated_key(g: Any) -> Dict[str, Any]:
    """What a custom transform's approval token hashes beside the files (in place of a spec): its code, selection and
    parameters — the same key as the window's ``Session._generated_key``."""
    return {"generated_source": g.source, "select": g.select, "params": g.params}


def changed_since_preview(key: Dict[str, Any], files: Sequence[Path], token: str,
                          before: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
    """None when ``key`` (a spec, or ``generated_key``) and ``files`` are exactly as they were when the preview's
    ``token`` was taken; otherwise what changed, one line each (``explain_approval_change`` against ``before``, the
    preview's ``approval_inputs`` — possibly empty when they were not kept). A previewed file that is gone is a
    change, never an exception."""
    try:
        if token and approval_token(key, files) == token:
            return None
    except OSError:
        pass                                      # a previewed file is missing now: changed
    return explain_approval_change(before or {}, approval_inputs(key, files))


def _stamp(ns: Any) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(ns) / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def explain_approval_change(before: Dict[str, Any], now: Dict[str, Any], limit: int = 12,
                            since: str = "the preview") -> List[str]:
    """What changed between the preview's ``approval_inputs`` and the files as they are now, one line each — the
    reason a run is refused, instead of "something changed". ``since`` names the moment ``before`` was taken (the
    command line's custom transform compares the files with how they were before it was written)."""
    out: List[str] = []
    if before.get("spec") != now.get("spec"):
        out.append("the EditSpec itself changed")
    bf, nf = before.get("files") or {}, now.get("files") or {}
    for f in sorted(set(nf) - set(bf)):
        out.append(f"{Path(f).name}: a file that was not in the preview (added to the folder since)")
    for f in sorted(set(bf) - set(nf)):
        out.append(f"{Path(f).name}: in the preview, but no longer among the files (moved, renamed or deleted)")
    for f in sorted(set(bf) & set(nf)):
        a, b = bf[f], nf[f]
        if a == b:
            continue
        if b == "missing" or a == "missing":
            out.append(f"{Path(f).name}: {'missing now' if b == 'missing' else 'back again'}")
        else:
            out.append(f"{Path(f).name}: modified since {since} (size {a[0]:,} → {b[0]:,} bytes; saved "
                       f"{_stamp(a[1])} → {_stamp(b[1])})")
    bt, nt = before.get("templates") or {}, now.get("templates") or {}
    for t in sorted(set(bt) | set(nt)):
        a, b = bt.get(t), nt.get(t)
        if a != b:
            out.append(f"template {Path(t).name}: " + ("missing now" if b == "missing" or b is None else
                                                       f"changed since {since}" if a not in (None, "missing")
                                                       else "new"))
    if len(out) > limit:
        out = out[:limit] + [f"… and {len(out) - limit} more change(s)"]
    return out


def sample_labels(samples: Sequence[Any]) -> Dict[str, str]:
    """``str(source)`` → how the preview text (and the note Claude gets) names each sample: its name, or with the
    folders that tell two same-named samples apart (``compiler.file_labels``, as Claude's visual check labels its
    pages) — two folders' ``Spec.docx`` are ``a/Spec.docx`` and ``b/Spec.docx``, never two lines that read alike."""
    from .compiler import file_labels
    return file_labels([Path(s.source) for s in samples])


@dataclass
class SampleView:
    source: Path
    output: Optional[Path] = None
    passed: Optional[bool] = None
    checks: Dict[str, str] = field(default_factory=dict)
    images: List[Path] = field(default_factory=list)
    exception: Optional[Dict[str, Any]] = None
    source_pdf: Optional[Path] = None             # the renders (for Claude's visual check)
    output_pdf: Optional[Path] = None
    look_pages: List[int] = field(default_factory=list)   # page 1 and the first page whose words moved

    def to_dict(self):
        return {"source": str(self.source), "output": str(self.output) if self.output else None, "passed": self.passed,
                "checks": self.checks, "images": [str(i) for i in self.images], "exception": self.exception,
                "look_pages": self.look_pages}


@dataclass
class Preview:
    out_dir: Path
    batch_plan: BatchPlan
    samples: List[SampleView] = field(default_factory=list)
    token: str = ""
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    review: Any = None                            # vision.VisualReview — Claude's visual check (advisory)
    review_ack: str = ""                          # the token whose blocking findings the user acknowledged
    token_inputs: Dict[str, Any] = field(default_factory=dict)   # approval_inputs at preview time
    log_path: Optional[Path] = None                               # logs/preview.log

    def to_text(self) -> str:
        spec = self.batch_plan.spec
        out = [f"PREVIEW — {spec.get('request')!r}", f"summary: {spec.get('summary')}"]
        out += [f"assumption: {a}" for a in spec.get("assumptions", [])]
        out += [f"WARNING: {w}" for w in self.warnings]
        out.append("")
        out.append(self.batch_plan.to_text(per_file=False))
        out.append("\nSAMPLES (applied and verified in memory; sources untouched):")
        labels = sample_labels(self.samples)
        for s in self.samples:
            lab = labels[str(Path(s.source))]
            if s.exception:
                out.append(f"   {lab}: EXCEPTION [{s.exception['code']}] {s.exception['reason']}")
                continue
            failed = [k for k, v in s.checks.items() if v == "FAIL"]
            out.append(f"   {lab}: verification {'PASS' if s.passed else 'FAIL ' + str(failed)}"
                       + (f"; images: {[i.name for i in s.images]}" if s.images else ""))
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        out.append(f"\napproval token: {self.token}")
        return "\n".join(out)

    def to_dict(self):
        return {"token": self.token, "renderer": self.renderer, "warnings": self.warnings,
                "plan": self.batch_plan.to_dict(), "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "approval_inputs": self.token_inputs, "log": "logs/preview.log"}


def side_by_side(src_pdf: Path, out_pdf: Path, page: int, png: Path, dpi: int = 70) -> Optional[Path]:
    """One PNG: the source's page on the left, the output's on the right (1-based page)."""
    from .verify.geometry import fitz
    a, b = fitz.open(str(src_pdf)), fitz.open(str(out_pdf))
    try:
        if page > a.page_count or page > b.page_count:
            return None
        ra, rb = a[page - 1].rect, b[page - 1].rect
        gap = 18
        sheet = fitz.open()
        pg = sheet.new_page(width=ra.width + rb.width + gap, height=max(ra.height, rb.height) + 16)
        pg.show_pdf_page(fitz.Rect(0, 16, ra.width, 16 + ra.height), a, page - 1)
        pg.show_pdf_page(fitz.Rect(ra.width + gap, 16, ra.width + gap + rb.width, 16 + rb.height), b, page - 1)
        pg.insert_text((4, 11), f"BEFORE  p.{page}", fontsize=9)
        pg.insert_text((ra.width + gap + 4, 11), f"AFTER  p.{page}", fontsize=9)
        png.parent.mkdir(parents=True, exist_ok=True)
        pg.get_pixmap(dpi=dpi).save(str(png))
        sheet.close()
        return png
    finally:
        a.close(); b.close()


def _pick_samples(bp: BatchPlan) -> List[Path]:
    """One plannable file per structure pattern (largest pattern first), topped up to review.sample_files;
    never more than 3."""
    want = min(max(int((bp.spec.get("review") or {}).get("sample_files") or 2), 1), 3)
    groups = sorted(([f for f in fs if f.status == "planned"] for fs in bp.patterns.values()), key=lambda fs: -len(fs))
    groups = [g for g in groups if g]
    picks = [g[0].source for g in groups][:3]
    rest = [f.source for g in groups for f in g[1:]]
    while len(picks) < want and rest:
        picks.append(rest.pop(0))
    return picks


def _preview_log(out_dir: Path, title: str):
    """``logs/preview.log`` beside the preview (a diagnostic never stops the work it describes: an unwritable log is
    silently no log)."""
    from . import diagnostics as diag
    return diag.RunLog(Path(out_dir) / diag.LOGS / "preview.log", None,
                       [f"Spec Surgeon preview log — {title}",
                        f"environment  {diag.environment_line(diag.environment())}", ""])


def _log_verification(log: Any, name: str, rec: Any) -> None:
    """One sample's verification in the preview log: every check's status (V6/V7/V9 belong to a run) and each failed
    check's specifics."""
    if log is None or rec is None:
        return
    log.detail(f"sample {name}: verification {'PASS' if rec.passed else 'FAIL'} — "
               + " · ".join(f"{c.id} {c.status}" for c in rec.checks if c.id not in ("V6", "V7", "V9")))
    for cid, cname, _summary, details in rec.failures():
        log.block(f"{cid} {cname}:\n" + "\n".join(f"  - {x}" for x in details))


def render_samples(samples: List[SampleView], out_dir: Path, renderer: str, do_render: bool,
                   warnings: List[str], log: Any = None) -> Optional[str]:
    """Render each sample's source and output (Word, else LibreOffice), compose before/after images of page 1 and of
    the first page whose words moved, and keep the PDFs and those page numbers on the sample (Claude's visual check
    reads them). Never raises for a renderer or a picture: a renderer that cannot run, a file it cannot convert or a
    page comparison that fails is a warning beside the plan summaries and verification. ``log`` (a
    ``diagnostics.RunLog``, optional) gets what the renderer said and every traceback. Returns the renderer used, or
    None."""
    import shutil
    from . import render as render_mod
    from .verify import geometry as geom
    detail = log.detail if log is not None else (lambda msg: None)
    block = log.block if log is not None else (lambda text: None)

    def warn(msg: str) -> None:
        warnings.append(msg)
        detail(msg)
    backend = render_mod.pick(renderer) if do_render else None
    if not do_render:
        warnings.append("rendering skipped: plan summaries and verification only")
    elif backend is None:
        warnings.append("no renderer found (Word or LibreOffice): plan summaries and verification only")
        detail(f"no renderer: {render_mod.unavailable_reason(renderer)}")
    elif backend != "word":
        warnings.append(f"preview rendered with {backend}; Word's layout can differ (Word is the reference on "
                        f"your machine when installed)")
    if backend:
        detail(f"renderer: {render_mod.describe(backend)}")
    done = [s for s in samples if s.output]
    if not (backend and done):
        return backend
    rdir = Path(out_dir) / "render"
    # renderers name a PDF after its file's stem: samples whose names collide are rendered from numbered copies
    stems = [s.source.stem.casefold() for s in done]
    unique = [s.source.stem if stems.count(s.source.stem.casefold()) == 1 else f"{i:02d} {s.source.stem}"
              for i, s in enumerate(done)]

    def staged(path: Path, side: str, name: str) -> Path:
        if path.stem == name:
            return path
        copy = rdir / "staged" / side / f"{name}{path.suffix}"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        return copy
    src_in = [staged(s.source, "source", n) for s, n in zip(done, unique)]
    out_in = [staged(s.output, "output", n) for s, n in zip(done, unique)]
    # a renderer failure (Word that will not start, a file LibreOffice chokes on) is a warning beside the plan
    # summaries and verification, never a preview that dies without them
    labels = sample_labels(samples)              # two folders' Spec.docx are told apart in the warnings too
    srcs = render_mod.render_pdfs(src_in, rdir / "source", backend, 2, log=warn, detail=detail)
    outs = render_mod.render_pdfs(out_in, rdir / "output", backend, 2, log=warn, detail=detail)
    for s, a, b, name in zip(done, src_in, out_in, unique):
        sp, op = srcs.get(a), outs.get(b)
        if not sp or not op:
            warn(f"{labels[str(Path(s.source))]}: render failed")
            continue
        s.source_pdf, s.output_pdf = Path(sp), Path(op)
        s.look_pages = [1]                     # page 1 at least, even when the comparison below cannot run
        try:
            g = geom.compare_pdfs(sp, op)
            moved = [p.page for p in g.pages if p.max_dy > 0.5 or p.words_only_in_out or p.words_only_in_ref]
            s.look_pages = sorted({1} | set(moved[:1]))
            for page in s.look_pages:
                img = side_by_side(sp, op, page, Path(out_dir) / "images" / f"{name} p{page}.png")
                if img:
                    s.images.append(img)
        except Exception as e:  # noqa: BLE001 — pictures are a nicety; the plan and verification stand
            warn(f"{labels[str(Path(s.source))]}: the before/after pictures could not be made "
                 f"({type(e).__name__}: {e})")
            block(traceback.format_exc())
    return backend


@dataclass
class GeneratedPreview:
    """What the user sees of a one-off custom transform before Run: its sample files (tested in the sandbox and
    verified), their before/after renders and Claude's visual check — the same as a spec's preview minus the per-file
    plan table (a generated op is proven per file only when it runs)."""
    out_dir: Path
    name: str
    samples: List[SampleView] = field(default_factory=list)
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    review: Any = None
    review_ack: str = ""
    log_path: Optional[Path] = None                               # logs/preview.log
    rendered: bool = False                                        # made with Render pages on (pages + visual check)

    def to_text(self) -> str:
        out = [f"PREVIEW — one-off custom transform {self.name!r} (AI-written, tested on sample files)"]
        out += [f"WARNING: {w}" for w in self.warnings]
        out.append("SAMPLES (run in the sandbox and verified; sources untouched):")
        labels = sample_labels(self.samples)
        for s in self.samples:
            failed = [k for k, v in s.checks.items() if v == "FAIL"]
            out.append(f"   {labels[str(Path(s.source))]}: verification {'PASS' if s.passed else 'FAIL ' + str(failed)}"
                       + (f"; images: {[i.name for i in s.images]}" if s.images else ""))
        if self.review is not None:
            out += ["", self.review.to_text()]
        return "\n".join(out)

    def to_dict(self):
        return {"name": self.name, "renderer": self.renderer, "warnings": self.warnings,
                "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "log": "logs/preview.log"}


def preview_generated(g: Any, out_dir: Path, renderer: str = "auto", do_render: bool = True) -> GeneratedPreview:
    """The custom transform's preview, from the trials that made it pass: outputs copied out of the sandbox under names
    unique across the samples, rendered beside their sources. ``logs/preview.log`` has each sample's verification and
    what the renderer said."""
    from .batch import output_names
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = getattr(g, "name", "") or "generated"
    log = _preview_log(out_dir, f"one-off custom transform {name!r}")
    try:
        gp = GeneratedPreview(out_dir=out_dir, name=name, log_path=log.path, rendered=bool(do_render))
        trials = list(getattr(g, "trials", None) or [])
        names = output_names([Path(t.source) for t in trials])
        for t in trials:
            sv = SampleView(source=Path(t.source), passed=bool(t.passed),
                            checks={c.id: c.status for c in t.record.checks} if t.record is not None else {})
            if t.record is not None:
                _log_verification(log, Path(t.source).name, t.record)
            else:
                log.detail(f"sample {Path(t.source).name}: no verification record — "
                           + "; ".join(str(x) for x in (getattr(t, "problems", None) or ["(no problems recorded)"])))
            if t.output and Path(t.output).is_file():
                sv.output = out_dir / "samples" / names[str(t.source)]
                sv.output.parent.mkdir(parents=True, exist_ok=True)
                sv.output.write_bytes(Path(t.output).read_bytes())
            gp.samples.append(sv)
        gp.renderer = render_samples(gp.samples, out_dir, renderer, do_render, gp.warnings, log=log)
        write_preview_files(gp)
        return gp
    except BaseException as e:
        log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        raise
    finally:
        log.close()


def write_preview_files(pv: Any) -> None:
    """PREVIEW.md and preview.json beside the samples (rewritten when Claude's visual check arrives — preview.json keeps
    the approval inputs, so a refused run can still say what changed)."""
    (pv.out_dir / "PREVIEW.md").write_text("```\n" + pv.to_text() + "\n```\n", encoding="utf-8")
    (pv.out_dir / "preview.json").write_text(json.dumps(pv.to_dict(), indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")


def make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                 do_render: bool = True) -> Preview:
    from . import diagnostics as diag
    from .batch import output_names
    from .verify import run_suite
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = _preview_log(out_dir, repr(spec.get("request")))
    try:
        bp = plan_batch(inputs, spec)
        pv = Preview(out_dir=out_dir, batch_plan=bp, log_path=log.path)
        log.detail(f"planned {len(bp.files)} file(s) in {bp.seconds:.2f}s: {len(bp.files) - len(bp.exceptions)} "
                   f"plan cleanly, {len(bp.exceptions)} exception(s)")
        for fpl in bp.files:
            if fpl.status == "planned":
                log.detail(f"  {fpl.source.name}: planned [{fpl.ms:.0f} ms] — " + " · ".join(fpl.summary))
            else:
                e = fpl.exception or {}
                log.detail(f"  {fpl.source.name}: EXCEPTION [{e.get('code')}] at {e.get('stage')}"
                           + (f", op #{e['op_number']} {e.get('op')}" if e.get("op_number") else "")
                           + f": {e.get('reason')}" + (f" (at {e['where']})" if e.get("where") else ""))
                if e.get("traceback") and e.get("kind") in ("internal-error", "environment"):
                    log.block(e["traceback"])
        picks = _pick_samples(bp)
        # names unique across the samples (two folders can each hold a "Spec.docx"): one sample's output never
        # overwrites another's, so each before/after picture and the visual check pair a source with its own output
        names = output_names(picks)
        for f in picks:
            sv = SampleView(source=f)
            stage, data = "open", None
            try:
                data = Path(f).read_bytes()
                pkg = DocxPackage(data, name=str(f))
                stage = "plan"
                plan, frags = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                stage = "apply"
                out = apply_spec(pkg, frags).output_bytes()
                stage = "write"
                sv.output = out_dir / "samples" / names[str(f)]
                sv.output.parent.mkdir(parents=True, exist_ok=True)
                sv.output.write_bytes(out)
                stage = "verify"
                rec = run_suite(pkg.data, out, plan.to_dict(), str(f), str(sv.output))
                sv.passed = rec.passed
                sv.checks = {c.id: c.status for c in rec.checks}
                _log_verification(log, f.name, rec)
            except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
                sv.exception = diag.exception_record(e, stage, data)
                log.detail(f"sample {f.name}: EXCEPTION [{sv.exception['code']}] at {stage}: {sv.exception['reason']}")
                if sv.exception.get("traceback") and sv.exception.get("kind") != "refusal":
                    log.block(sv.exception["traceback"])
            pv.samples.append(sv)
        pv.renderer = render_samples(pv.samples, out_dir, renderer, do_render, pv.warnings, log=log)
        pv.token = approval_token(spec, [f.source for f in bp.files])
        pv.token_inputs = approval_inputs(spec, [f.source for f in bp.files])
        (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        write_preview_files(pv)
        log.detail(f"approval token {pv.token}")
        return pv
    except BaseException as e:
        log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        raise
    finally:
        log.close()
