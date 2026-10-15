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
   changed since the preview.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .editspec import apply_spec, plan_spec
from .fingerprint import fingerprint
from .ops import JobException
from .package import DocxPackage
from .planner import BatchPlan, plan_batch


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
                "plan": self.batch_plan.to_dict(), "samples": [s.to_dict() for s in self.samples]}


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
    from . import render as render_mod
    from .verify import run_suite
    from .verify import geometry as geom
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bp = plan_batch(inputs, spec)
    pv = Preview(out_dir=out_dir, batch_plan=bp)
    for f in _pick_samples(bp):
        sv = SampleView(source=f)
        try:
            pkg = DocxPackage.from_path(f)
            plan, frags = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
            out = apply_spec(pkg, frags).output_bytes()
            sv.output = out_dir / "samples" / f.name
            sv.output.parent.mkdir(parents=True, exist_ok=True)
            sv.output.write_bytes(out)
            rec = run_suite(pkg.data, out, plan.to_dict(), str(f), str(sv.output))
            sv.passed = rec.passed
            sv.checks = {c.id: c.status for c in rec.checks}
        except JobException as e:
            sv.exception = e.to_dict()
        pv.samples.append(sv)
    backend = render_mod.pick(renderer) if do_render else None
    pv.renderer = backend
    if not do_render:
        pv.warnings.append("rendering skipped: plan summaries and verification only")
    elif backend is None:
        pv.warnings.append("no renderer found (Word or LibreOffice): plan summaries and verification only")
    elif backend != "word":
        pv.warnings.append(f"preview rendered with {backend}; Word's layout can differ (Word is the reference on "
                           f"your machine when installed)")
    done = [s for s in pv.samples if s.output]
    if backend and done:
        rdir = out_dir / "render"
        srcs = render_mod.render_pdfs([s.source for s in done], rdir / "source", backend, 2)
        outs = render_mod.render_pdfs([s.output for s in done], rdir / "output", backend, 2)
        for s in done:
            sp, op = srcs.get(s.source), outs.get(s.output)
            if not sp or not op:
                pv.warnings.append(f"{s.source.name}: render failed")
                continue
            g = geom.compare_pdfs(sp, op)
            moved = [p.page for p in g.pages if p.max_dy > 0.5 or p.words_only_in_out or p.words_only_in_ref]
            for page in sorted({1} | set(moved[:1])):
                img = side_by_side(sp, op, page, out_dir / "images" / f"{s.source.stem} p{page}.png")
                if img:
                    s.images.append(img)
    pv.token = approval_token(spec, [f.source for f in bp.files])
    (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "PREVIEW.md").write_text("```\n" + pv.to_text() + "\n```\n", encoding="utf-8")
    (out_dir / "preview.json").write_text(json.dumps(pv.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return pv
