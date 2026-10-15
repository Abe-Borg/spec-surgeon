"""Preview and approval — what the user sees before anything runs on the batch (docs/NL_SURGICAL_EDITOR_PLAN.md §7;
Phase 4).

``make_preview(spec, inputs, out_dir)``:
1. plans the spec on every file (planner.plan_batch — summaries and exceptions, nothing written);
2. picks sample files — the first plannable file of each structure pattern, up to ``review.sample_files``
   (default 2, at most 3, but always one per pattern up to 3) — and runs plan → apply → verify on them in memory,
   writing the outputs into ``out_dir/samples`` (never over a source);
3. renders source and output of each sample (Word when available, else LibreOffice — a visible warning says so:
   decision on §14.5, "fall back with a warning", never silently) and composes side-by-side PNGs of page 1 and of
   the first page whose words moved;
4. writes ``spec.json`` and ``PREVIEW.md`` and computes an **approval token** — a hash of the spec and of every
   planned file's path, size and mtime. ``run-spec --approval TOKEN`` refuses to run when the spec or any file
   changed since the preview — and says which (``approval_inputs`` are kept in ``preview.json``;
   ``explain_approval_change`` compares them with the files as they are now).

Diagnostics: ``logs/preview.log`` has every file's plan or refusal (with the stage, the op and the line of code), each
sample's verification with the failing checks' specifics, and what the renderer said; a sample that fails in an
unexpected way is that sample's exception record (classified like a run's), never a lost preview.
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


def _stamp(ns: Any) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(ns) / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def explain_approval_change(before: Dict[str, Any], now: Dict[str, Any], limit: int = 12) -> List[str]:
    """What changed between the preview's ``approval_inputs`` and the files as they are now, one line each — the
    reason a run is refused, instead of "something changed"."""
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
            out.append(f"{Path(f).name}: modified since the preview (size {a[0]:,} → {b[0]:,} bytes; saved "
                       f"{_stamp(a[1])} → {_stamp(b[1])})")
    bt, nt = before.get("templates") or {}, now.get("templates") or {}
    for t in sorted(set(bt) | set(nt)):
        a, b = bt.get(t), nt.get(t)
        if a != b:
            out.append(f"template {Path(t).name}: " + ("missing now" if b == "missing" or b is None else
                                                       "changed since the preview" if a not in (None, "missing")
                                                       else "new"))
    if len(out) > limit:
        out = out[:limit] + [f"… and {len(out) - limit} more change(s)"]
    return out


@dataclass
class SampleView:
    source: Path
    output: Optional[Path] = None
    passed: Optional[bool] = None
    checks: Dict[str, str] = field(default_factory=dict)
    images: List[Path] = field(default_factory=list)
    exception: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return {"source": str(self.source), "output": str(self.output) if self.output else None, "passed": self.passed,
                "checks": self.checks, "images": [str(i) for i in self.images], "exception": self.exception}


@dataclass
class Preview:
    out_dir: Path
    batch_plan: BatchPlan
    samples: List[SampleView] = field(default_factory=list)
    token: str = ""
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
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
        for s in self.samples:
            if s.exception:
                out.append(f"   {s.source.name}: EXCEPTION [{s.exception['code']}] {s.exception['reason']}")
                continue
            failed = [k for k, v in s.checks.items() if v == "FAIL"]
            out.append(f"   {s.source.name}: verification {'PASS' if s.passed else 'FAIL ' + str(failed)}"
                       + (f"; images: {[i.name for i in s.images]}" if s.images else ""))
        out.append(f"\napproval token: {self.token}")
        return "\n".join(out)

    def to_dict(self):
        return {"token": self.token, "renderer": self.renderer, "warnings": self.warnings,
                "plan": self.batch_plan.to_dict(), "samples": [s.to_dict() for s in self.samples],
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


def make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                 do_render: bool = True) -> Preview:
    from . import diagnostics as diag
    from . import render as render_mod
    from .verify import run_suite
    from .verify import geometry as geom
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = diag.RunLog(out_dir / diag.LOGS / "preview.log", None,
                      [f"Spec Surgeon preview log — {spec.get('request')!r}",
                       f"environment  {diag.environment_line(diag.environment())}", ""])
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
        for f in _pick_samples(bp):
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
                sv.output = out_dir / "samples" / f.name
                sv.output.parent.mkdir(parents=True, exist_ok=True)
                sv.output.write_bytes(out)
                stage = "verify"
                rec = run_suite(pkg.data, out, plan.to_dict(), str(f), str(sv.output))
                sv.passed = rec.passed
                sv.checks = {c.id: c.status for c in rec.checks}
                log.detail(f"sample {f.name}: verification {'PASS' if rec.passed else 'FAIL'} — "
                           + " · ".join(f"{c.id} {c.status}" for c in rec.checks if c.id not in ("V6", "V7", "V9")))
                for cid, cname, _summary, details in rec.failures():
                    log.block(f"{cid} {cname}:\n" + "\n".join(f"  - {x}" for x in details))
            except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
                sv.exception = diag.exception_record(e, stage, data)
                log.detail(f"sample {f.name}: EXCEPTION [{sv.exception['code']}] at {stage}: {sv.exception['reason']}")
                if sv.exception.get("traceback") and sv.exception.get("kind") != "refusal":
                    log.block(sv.exception["traceback"])
            pv.samples.append(sv)
        backend = render_mod.pick(renderer) if do_render else None
        pv.renderer = backend
        if not do_render:
            pv.warnings.append("rendering skipped: plan summaries and verification only")
        elif backend is None:
            pv.warnings.append("no renderer found (Word or LibreOffice): plan summaries and verification only")
            log.detail(f"no renderer: {render_mod.unavailable_reason(renderer)}")
        elif backend != "word":
            pv.warnings.append(f"preview rendered with {backend}; Word's layout can differ (Word is the reference on "
                               f"your machine when installed)")
        if backend:
            log.detail(f"renderer: {render_mod.describe(backend)}")
        done = [s for s in pv.samples if s.output]

        def warn(msg: str) -> None:
            pv.warnings.append(msg)
            log.detail(msg)
        if backend and done:
            rdir = out_dir / "render"
            # a renderer failure (Word that will not start, a file LibreOffice chokes on) is a warning beside the plan
            # summaries and verification, never a preview that dies without them
            srcs = render_mod.render_pdfs([s.source for s in done], rdir / "source", backend, 2, log=warn,
                                          detail=log.detail)
            outs = render_mod.render_pdfs([s.output for s in done], rdir / "output", backend, 2, log=warn,
                                          detail=log.detail)
            for s in done:
                sp, op = srcs.get(s.source), outs.get(s.output)
                if not sp or not op:
                    warn(f"{s.source.name}: render failed")
                    continue
                try:
                    g = geom.compare_pdfs(sp, op)
                    moved = [p.page for p in g.pages if p.max_dy > 0.5 or p.words_only_in_out or p.words_only_in_ref]
                    for page in sorted({1} | set(moved[:1])):
                        img = side_by_side(sp, op, page, out_dir / "images" / f"{s.source.stem} p{page}.png")
                        if img:
                            s.images.append(img)
                except Exception as e:  # noqa: BLE001 — pictures are a nicety; the plan and verification stand
                    warn(f"{s.source.name}: the before/after pictures could not be made ({type(e).__name__}: {e})")
                    log.block(traceback.format_exc())
        pv.token = approval_token(spec, [f.source for f in bp.files])
        pv.token_inputs = approval_inputs(spec, [f.source for f in bp.files])
        (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "PREVIEW.md").write_text("```\n" + pv.to_text() + "\n```\n", encoding="utf-8")
        (out_dir / "preview.json").write_text(json.dumps(pv.to_dict(), indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
        log.detail(f"approval token {pv.token}")
        return pv
    except BaseException as e:
        log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        raise
    finally:
        log.close()
