"""Preview and approval — what the user sees before anything runs on the batch (docs/history/NL_SURGICAL_EDITOR_PLAN.md §7;
Phase 4).

``make_preview(spec, inputs, out_dir)``:
1. plans the spec on every file (planner.plan_batch — summaries and exceptions, nothing written);
2. picks representative plannable files — automatically eight or enough to cover every structure pattern,
   with an explicit positive ``review.sample_files`` count honored — and runs plan → apply → verify on them in memory,
   writing the outputs into ``out_dir/samples`` (never over a source) under names unique across the samples
   (``batch.output_names``: two folders' ``Spec.docx`` are ``Spec.docx`` and ``Spec (1).docx``); the preview text (and
   the note Claude gets) names each sample by ``sample_labels`` — ``a/Spec.docx`` and ``b/Spec.docx``, as Claude's
   visual check labels its pages;
3. renders source and output of each sample (Word when available, else LibreOffice — a visible warning says so:
   decision on §14.5, "fall back with a warning", never silently) and composes side-by-side PNGs of page 1 and of
   the first page whose words moved;
4. writes ``spec.json`` and ``PREVIEW.md`` with an **approval token** captured before planning — a hash of the spec
   and every planned file's path, size, mtime and content. ``run-spec --approval TOKEN`` refuses when any input
   changed since the preview — and says which (``approval_inputs`` are kept in ``preview.json``;
   ``explain_approval_change`` compares them with the files as they are now). ``do`` re-checks it after the y/N (or
   ``--yes``) and before the run (``changed_since_preview``) — for a custom transform with ``generated_key`` in place
   of the spec.

A plan per layout (``layouts.make_layout_preview``) runs this once per layout, on that layout's files only, under
``out_dir/layout-<id>``, and binds one token to every layout, its files and the routing.

Diagnostics: ``logs/preview.log`` has every file's plan or refusal (with the stage, the op and the line of code), each
sample's verification with the failing checks' specifics, and what the renderer said; a sample that fails in an
unexpected way is that sample's exception record (classified like a run's), never a lost preview; a page comparison
or picture that fails is a warning. A custom transform's preview (``preview_generated``) keeps the same log.

``render_samples`` keeps each sample's PDFs and the pages worth looking at (``look_pages``) for Claude's visual check
(``vision.review_samples``, advisory); ``write_preview_files`` rewrites PREVIEW.md and preview.json when it arrives
(``approval_inputs`` stay in preview.json).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import diagnostics as diag
from .editspec import apply_spec, plan_spec
from .fingerprint import fingerprint
from .package import DocxPackage
from .planner import BatchPlan, plan_batch


def key_templates(key: Any) -> List[Path]:
    """The files an approval key reads beside the documents: a spec's templates, tables and pictures
    (``editspec.spec_input_files``), or — a chain key (``chain.chain_key``: ``{"stages": [{"name", "spec"}, …]}``) —
    those of every stage, once each. A file edited after the preview voids the approval whichever stage reads it
    (CLAUDE.md rule 12)."""
    from .input_snapshots import input_paths
    return input_paths(key)


def _snapshot_preview_inputs(value, token_inputs, out_dir):
    """Run preview plans and checks against copies of exactly the approved auxiliary bytes.

    Public specs and approval keys keep their original paths. The execution copy alone
    names these persistent snapshots, whose manifest preserves the original provenance.
    """
    from .input_snapshots import snapshot_inputs
    return snapshot_inputs(value, out_dir, token_inputs["templates"])


def approval_inputs(spec: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """What the approval token is a hash of, as data: the spec's digest, every planned file's size, mtime and md5, and every
    file the spec reads beside the documents — templates, tables, pictures (``key_templates``) — its size, mtime and md5,
    kept beside the preview so a refused run can say exactly what changed. ``spec`` may be a chain key
    (``chain.chain_key``): then the files are every stage's."""
    spec_text = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    out: Dict[str, Any] = {"spec": hashlib.sha256(spec_text.encode("utf-8")).hexdigest()[:16], "files": {},
                           "templates": {}}
    for f in sorted(Path(x).resolve() for x in files):
        try:
            st = f.stat()
            out["files"][str(f)] = [st.st_size, st.st_mtime_ns, hashlib.md5(f.read_bytes()).hexdigest()]
        except OSError:
            out["files"][str(f)] = "missing"
    for t in sorted(set(key_templates(spec))):
        try:
            st = t.stat()
            out["templates"][str(t)] = [st.st_size, st.st_mtime_ns, hashlib.md5(t.read_bytes()).hexdigest()]
        except OSError:
            out["templates"][str(t)] = "missing"
    return out


def approval_token(spec: Dict[str, Any], files: Sequence[Path]) -> str:
    """A hash of the spec, of every planned file's path, size, mtime and content, and every file read beside the
    documents — templates, tables, pictures (``key_templates`` → ``editspec.spec_input_files``: path, size, mtime and
    content hash): they are inputs like the source (CLAUDE.md rule 12), so one edited after the preview voids the
    approval exactly as an edited spec file does. A spec with templates only hashes exactly as before. ``spec`` may be
    a chain key (``chain.chain_key``): every stage's files count."""
    return _approval_token_from_inputs(spec, approval_inputs(spec, files))


def _approval_token_from_inputs(spec, inputs):
    """Bind a token to one captured identity snapshot, never to files re-read after the preview work."""
    h = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for path, pin in sorted(inputs["files"].items()):
        if pin == "missing":
            raise FileNotFoundError(path)
        h.update(f"\n{path}|{pin[0]}|{pin[1]}|{pin[2]}".encode("utf-8"))
    for path, pin in sorted(inputs["templates"].items()):
        identity = "missing" if pin == "missing" else f"{pin[0]}|{pin[1]}|{pin[2]}"
        h.update(f"\ntemplate {path}|{identity}".encode("utf-8"))
    return h.hexdigest()[:16]


def generated_key(g: Any) -> Dict[str, Any]:
    """What a custom transform's approval token hashes beside the files (in place of a spec): its code, selection and
    parameters and approved adaptive outcome — the same key as the window's ``Session._generated_key``."""
    return {"generated_source": g.source, "select": g.select, "params": g.params,
            "goal_contract": getattr(g, "goal_contract", None), "goal_bindings": getattr(g, "goal_bindings", {})}


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
    reason a run is refused, instead of "something changed". ``since`` names the moment ``before`` was taken (a
    custom transform compares the files with how they were before it was written)."""
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
        elif a[:2] == b[:2] and len(a) > 2 and len(b) > 2:
            out.append(f"{Path(f).name}: content changed since {since} (size and saved timestamp unchanged)")
        else:
            out.append(f"{Path(f).name}: modified since {since} (size {a[0]:,} → {b[0]:,} bytes; saved "
                       f"{_stamp(a[1])} → {_stamp(b[1])})")
    bt, nt = before.get("templates") or {}, now.get("templates") or {}
    for t in sorted(set(bt) | set(nt)):
        a, b = bt.get(t), nt.get(t)
        if a != b:
            what = "template" if Path(t).suffix.lower() == ".docx" else "input file"
            out.append(f"{what} {Path(t).name}: " + ("missing now" if b == "missing" or b is None else
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
    pages_before: Optional[int] = None
    pages_after: Optional[int] = None
    look_pages: List[int] = field(default_factory=list)   # page 1 and the first page whose words moved
    stages: List[Dict[str, Any]] = field(default_factory=list)   # a chain's sample: each stage's verdict, in order
    outcomes: List[Dict[str, Any]] = field(default_factory=list)  # a chain's independent final requirements

    def to_dict(self):
        return {"source": str(self.source), "output": str(self.output) if self.output else None, "passed": self.passed,
                "checks": self.checks, "images": [str(i) for i in self.images], "exception": self.exception,
                "look_pages": self.look_pages, "pages_before": self.pages_before, "pages_after": self.pages_after,
                **({"stages": self.stages} if self.stages else {}),
                **({"outcomes": self.outcomes} if self.outcomes else {})}

    def stages_text(self) -> str:
        """``stage 1 PASS · stage 2 FAIL [V10]`` — how far a chain's sample got."""
        parts = []
        for st in self.stages:
            if st.get("exception"):
                parts.append(f"stage {st['stage']} EXCEPTION [{st['exception'].get('code')}]")
            elif st.get("passed"):
                parts.append(f"stage {st['stage']} PASS")
            else:
                failed = [k for k, v in (st.get("checks") or {}).items() if v == "FAIL"]
                parts.append(f"stage {st['stage']} FAIL {failed}")
        return " · ".join(parts)


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
    input_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def to_text(self) -> str:
        spec = self.batch_plan.spec
        out = [f"PREVIEW — {spec.get('request')!r}", f"summary: {spec.get('summary')}"]
        out += [f"assumption: {a}" for a in spec.get("assumptions", [])]
        out += [f"WARNING: {w}" for w in self.warnings]
        for binding in spec.get("bindings", []):
            out.append(f"FILLS — {Path(binding['file']).name}:")
            for name, evidence in binding["slots"].items():
                out.append(f"   {name}: {evidence['value']!r} ({evidence['part']}, "
                           f"paragraph {evidence['paragraph'] + 1})")
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
                       + (f"; pages: {s.pages_before} → {s.pages_after}" if s.pages_before is not None else "")
                       + (f"; images: {[i.name for i in s.images]}" if s.images else ""))
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        from .readable import REPORT_BYTES, bounded
        return (bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")
                + f"\napproval token: {self.token}")

    def to_dict(self):
        return {"token": self.token, "renderer": self.renderer, "warnings": self.warnings,
                "plan": self.batch_plan.to_dict(), "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "approval_inputs": self.token_inputs, "input_snapshots": self.input_snapshots, "log": "logs/preview.log"}


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
    """Cover every planned pattern automatically, or honor an explicit sample count."""
    from .sampling import representative_samples
    planned = [f for f in bp.files if f.status == "planned"]
    samples = representative_samples(planned, pattern_key=lambda f: f.pattern_id or str(f.source),
                                     count=(bp.spec.get("review") or {}).get("sample_files"))
    return [f.source for f in samples]


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
    diag.event("preview.verification", sample=name, passed=rec.passed,
               checks={c.id: c.status for c in rec.checks},
               failed_checks=[c.id for c in rec.checks if c.status == "FAIL"])
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
    diag.event("preview.render_selected", enabled=do_render, requested=renderer, backend=backend,
               sample_count=len(samples))
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
    # the cache beside the session folders: what this preview renders, the run does not render again (and a source
    # rendered in an earlier session is not rendered again now) — keyed by the file's bytes, never its name
    cache = render_mod.RenderCache.for_out_dir(Path(out_dir))
    if cache is not None:
        detail(f"render cache: {cache.root}")
    srcs = render_mod.render_pdfs(src_in, rdir / "source", backend, 2, log=warn, detail=detail, cache=cache)
    outs = render_mod.render_pdfs(out_in, rdir / "output", backend, 2, log=warn, detail=detail, cache=cache)
    for s, a, b, name in zip(done, src_in, out_in, unique):
        sp, op = srcs.get(a), outs.get(b)
        if not sp or not op:
            warn(f"{labels[str(Path(s.source))]}: render failed")
            continue
        s.source_pdf, s.output_pdf = Path(sp), Path(op)
        s.look_pages = [1]                     # page 1 at least, even when the comparison below cannot run
        try:
            from .verify.geometry import fitz
            with fitz.open(str(sp)) as before, fitz.open(str(op)) as after:
                s.pages_before, s.pages_after = before.page_count, after.page_count
            if s.pages_after != s.pages_before:
                warn(f"{labels[str(Path(s.source))]}: pages {s.pages_before} → {s.pages_after} "
                     "in the preview render; inspect the reflow before approving")
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
                       + (f"; pages: {s.pages_before} → {s.pages_after}" if s.pages_before is not None else "")
                       + (f"; images: {[i.name for i in s.images]}" if s.images else ""))
        if self.review is not None:
            out += ["", self.review.to_text()]
        from .readable import REPORT_BYTES, bounded
        return bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")

    def to_dict(self):
        return {"name": self.name, "renderer": self.renderer, "warnings": self.warnings,
                "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "log": "logs/preview.log"}


def preview_generated(g: Any, out_dir: Path, renderer: str = "auto", do_render: bool = True) -> GeneratedPreview:
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl",
                    run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview.generated", out_dir=str(out_dir), renderer=renderer, do_render=do_render):
        return _preview_generated(g, out_dir, renderer, do_render)


def _preview_generated(g: Any, out_dir: Path, renderer: str, do_render: bool) -> GeneratedPreview:
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
    with diag.span("preview.records", out_dir=str(pv.out_dir)):
        from .readable import write_report
        write_report(pv.out_dir / "PREVIEW.md", "```\n" + pv.to_text() + "\n```\n", "preview.json")
        (pv.out_dir / "preview.json").write_text(json.dumps(pv.to_dict(), indent=2, ensure_ascii=False, default=str),
                                                 encoding="utf-8")


def make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                 do_render: bool = True) -> Preview:
    from .batch import check_inputs_outside, expand_inputs
    from .editspec import spec_input_files, validate_spec
    spec = copy.deepcopy(spec)
    validate_spec(spec)
    check_inputs_outside([*expand_inputs(inputs), *spec_input_files(spec)], out_dir)
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl",
                    run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview", out_dir=str(out_dir), renderer=renderer, do_render=do_render) as outcome:
        result = _make_preview(spec, inputs, out_dir, renderer, do_render)
        outcome.update(samples=len(result.samples), exceptions=len(result.batch_plan.exceptions),
                       warnings=result.warnings)
        return result


def _make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str,
                  do_render: bool) -> Preview:
    from . import diagnostics as diag
    from .batch import check_inputs_outside, expand_inputs, output_names
    from .editspec import is_template_file, spec_input_files, spec_templates, validate_spec
    from .verify import run_suite
    from .input_snapshots import changed_inputs
    spec = copy.deepcopy(spec)
    validate_spec(spec)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    check_inputs_outside([*files, *spec_input_files(spec)], out_dir)
    templates = spec_templates(spec)
    planned = [f for f in files if not is_template_file(f, templates)]
    token_inputs = approval_inputs(spec, planned)
    token = _approval_token_from_inputs(spec, token_inputs)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = _preview_log(out_dir, repr(spec.get("request")))
    try:
        execution_spec, snapshots = _snapshot_preview_inputs(spec, token_inputs, out_dir)
        with diag.span("preview.plan_batch"):
            bp = plan_batch([str(f) for f in planned], execution_spec)
        bp.spec, bp.skipped = spec, [str(f) for f in files if f not in planned]
        pv = Preview(out_dir=out_dir, batch_plan=bp, log_path=log.path, token=token, token_inputs=token_inputs,
                     input_snapshots=snapshots)
        input_changed = (changed_since_preview(spec, planned, token, token_inputs) is not None or
                         bool(changed_inputs(snapshots)) or
                         {str(f.source.resolve()) for f in bp.files} != set(token_inputs["files"]) or
                         any(f.source_md5 != token_inputs["files"][str(f.source.resolve())][2] for f in bp.files))
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
        diag.event("preview.samples_selected", sources=[str(f) for f in picks], planned_files=len(bp.files))
        # names unique across the samples (two folders can each hold a "Spec.docx"): one sample's output never
        # overwrites another's, so each before/after picture and the visual check pair a source with its own output
        names = output_names(picks)
        for f in picks:
            with diag.scope(source=str(f)), diag.span("preview.sample"):
                sv = SampleView(source=f)
                stage, data = "open", None
                try:
                    with diag.span("preview.sample.read"):
                        data = Path(f).read_bytes()
                    if hashlib.md5(data).hexdigest() != token_inputs["files"][str(Path(f).resolve())][2]:
                        input_changed = True
                    with diag.span("preview.sample.open"):
                        pkg = DocxPackage(data, name=str(f))
                    stage = "plan"
                    with diag.span("preview.sample.plan"):
                        plan, frags = plan_spec(pkg, execution_spec, fingerprint(pkg)["pattern_id"])
                    stage = "apply"
                    with diag.span("preview.sample.apply"):
                        out = apply_spec(pkg, frags).output_bytes()
                    stage = "write"
                    sv.output = out_dir / "samples" / names[str(f)]
                    with diag.span("preview.sample.write", output=str(sv.output), size=len(out)):
                        sv.output.parent.mkdir(parents=True, exist_ok=True)
                        sv.output.write_bytes(out)
                    stage = "verify"
                    with diag.span("preview.sample.verify"):
                        rec = run_suite(pkg.data, out, plan.to_dict(), str(f), str(sv.output))
                    sv.passed = rec.passed
                    sv.checks = {c.id: c.status for c in rec.checks}
                    _log_verification(log, f.name, rec)
                except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
                    sv.exception = diag.exception_record(e, stage, data)
                    diag.event("preview.sample_failed", level="error", source=str(f), stage=stage,
                               exception=sv.exception)
                    log.detail(f"sample {f.name}: EXCEPTION [{sv.exception['code']}] at {stage}: {sv.exception['reason']}")
                    if sv.exception.get("traceback") and sv.exception.get("kind") != "refusal":
                        log.block(sv.exception["traceback"])
                pv.samples.append(sv)
                input_changed = (input_changed or changed_since_preview(spec, planned, token, token_inputs) is not None
                                 or bool(changed_inputs(snapshots)))
                diag.event("preview.sample_finished", source=str(f), passed=sv.passed,
                           exception=sv.exception, checks=sv.checks)
        pv.renderer = render_samples(pv.samples, out_dir, renderer, do_render, pv.warnings, log=log)
        if input_changed or changed_since_preview(spec, planned, token, token_inputs) is not None or changed_inputs(snapshots):
            pv.token = ""  # an observed mismatch cannot become approved by restoring an earlier file later
            pv.warnings.append("An input changed while the preview was being prepared; press Preview again before Run.")
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


# ---- chains: several specs, one after another (chain.py) ----------------------------------------------------------

@dataclass
class ChainPreview(Preview):
    """A chain's preview: stage 1's plan on every file (the later stages are planned on each file once the stage before
    it ran, so only the samples go through every stage here), the samples applied and verified stage by stage in
    memory, their before/after renders (the source against the file after the LAST stage) and Claude's visual check.
    The approval token is over the chain (``chain.chain_key``: every stage's spec and templates) and the files."""
    chain: Dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        from .chain import chain_name, stage_name
        stages = self.chain.get("stages") or []
        out = [f"PREVIEW — chain «{chain_name(self.chain)}»: {len(stages)} stage(s), run one after another on each file"]
        for i, st in enumerate(stages, 1):
            spec = st.get("spec") or {}
            out.append(f"  stage {i} — {stage_name(st, i)}: {spec.get('request')!r} — {spec.get('summary') or ''}")
            out += [f"     assumption: {a}" for a in spec.get("assumptions") or []]
        out += [f"WARNING: {w}" for w in self.warnings]
        out.append("")
        out.append("STAGE 1 planned on every file (each later stage is planned on a file once the stage before it ran; "
                   "the samples below went through every stage):")
        out.append(self.batch_plan.to_text(per_file=False))
        out.append("\nSAMPLES (every stage applied and verified in memory, one after another; sources untouched):")
        labels = sample_labels(self.samples)
        for sv in self.samples:
            lab = labels[str(Path(sv.source))]
            if sv.exception:
                out.append(f"   {lab}: {sv.stages_text()} — EXCEPTION [{sv.exception['code']}] {sv.exception['reason']}")
                continue
            failed = [k for k, v in sv.checks.items() if v == "FAIL"]
            out.append(f"   {lab}: {sv.stages_text()} → verification {'PASS' if sv.passed else 'FAIL ' + str(failed)}"
                       + (f"; pages: {sv.pages_before} → {sv.pages_after}" if sv.pages_before is not None else "")
                       + (f"; images: {[i.name for i in sv.images]}" if sv.images else ""))
            out += [f"     final requirement {entry['index']} {entry['status']}: {entry['summary']}"
                    for entry in sv.outcomes]
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        from .readable import REPORT_BYTES, bounded
        return (bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")
                + f"\napproval token: {self.token}")

    def to_dict(self):
        d = super().to_dict()
        d["chain"] = self.chain
        return d


def make_chain_preview(chain: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                       do_render: bool = True) -> ChainPreview:
    with diag.scope(run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview.chain", out_dir=str(out_dir), renderer=renderer, do_render=do_render):
        return _make_chain_preview(chain, inputs, out_dir, renderer, do_render)


def _make_chain_preview(chain: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str,
                        do_render: bool) -> ChainPreview:
    """``make_preview`` for a chain: stage 1 planned on every file, the samples (``_pick_samples`` of that plan) run
    through every stage in memory — plan → apply → verify, each stage on the bytes the stage before it verified; a
    stage that refuses or fails verification stops that sample there —, the final output written under
    ``samples/`` (unique names), rendered beside the source. Writes ``chain.json``, PREVIEW.md and preview.json;
    ``logs/preview.log`` has every stage's verdict per sample."""
    from . import diagnostics as diag
    from .batch import check_inputs_outside, expand_inputs, output_names
    from .chain import chain_key, chain_name, chain_templates, stage_name, validate_chain
    from .editspec import is_template_file, spec_input_files
    from .verify import run_suite
    from .input_snapshots import changed_inputs
    from .verify.outcomes import assert_outcomes
    chain = copy.deepcopy(chain)
    validate_chain(chain)
    out_dir = Path(out_dir)
    candidates = expand_inputs(inputs)
    auxiliary = [path for stage in chain["stages"] for path in spec_input_files(stage["spec"])]
    check_inputs_outside([*candidates, *auxiliary], out_dir)
    templates = chain_templates(chain)
    files = [f for f in candidates if not is_template_file(f, templates)]
    key = chain_key(chain)
    token_inputs = approval_inputs(key, files)
    token = _approval_token_from_inputs(key, token_inputs)
    with diag.scope(out_dir / diag.LOGS / "events.jsonl"), diag.span("preview.chain.execute"):
        out_dir.mkdir(parents=True, exist_ok=True)
        log = _preview_log(out_dir, f"chain {chain_name(chain)!r}")
        try:
            execution_chain, snapshots = _snapshot_preview_inputs(chain, token_inputs, out_dir)
            stages = execution_chain["stages"]
            bp = plan_batch([str(f) for f in files], stages[0]["spec"])
            bp.spec = chain["stages"][0]["spec"]
            pv = ChainPreview(out_dir=out_dir, batch_plan=bp, chain=chain, log_path=log.path,
                              token=token, token_inputs=token_inputs, input_snapshots=snapshots)
            input_changed = (changed_since_preview(key, files, token, token_inputs) is not None or
                             bool(changed_inputs(snapshots)) or
                             {str(f.source.resolve()) for f in bp.files} != set(token_inputs["files"]) or
                             any(f.source_md5 != token_inputs["files"][str(f.source.resolve())][2] for f in bp.files))
            log.detail(f"stage 1 ({stage_name(stages[0], 1)}) planned on {len(bp.files)} file(s) in {bp.seconds:.2f}s: "
                       f"{len(bp.files) - len(bp.exceptions)} plan cleanly, {len(bp.exceptions)} exception(s)")
            for fpl in bp.files:
                if fpl.status == "planned":
                    log.detail(f"  {fpl.source.name}: planned [{fpl.ms:.0f} ms] — " + " · ".join(fpl.summary))
                else:
                    e = fpl.exception or {}
                    log.detail(f"  {fpl.source.name}: EXCEPTION [{e.get('code')}] at {e.get('stage')}: {e.get('reason')}")
            picks = _pick_samples(bp)
            names = output_names(picks)
            for f in picks:
                sv = SampleView(source=f)
                phase, data, produced = "open", None, False
                try:
                    data = Path(f).read_bytes()
                    if hashlib.md5(data).hexdigest() != token_inputs["files"][str(Path(f).resolve())][2]:
                        input_changed = True
                except Exception as e:  # noqa: BLE001
                    sv.exception = diag.exception_record(e, phase, None)
                    diag.event("preview.sample_failed", level="error", source=str(f), stage=phase,
                               exception=sv.exception)
                for i, st in enumerate(stages, 1):
                    if sv.exception is not None:
                        break
                    spec, label = st["spec"], stage_name(st, i)
                    with diag.scope(source=str(f), chain_stage=i, chain_stage_name=label), \
                            diag.span("preview.chain_stage"):
                        try:
                            phase = "plan"
                            pkg = DocxPackage(data, name=str(f))
                            with diag.span("preview.sample.plan"):
                                plan, frags = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                            phase = "apply"
                            with diag.span("preview.sample.apply"):
                                out = apply_spec(pkg, frags).output_bytes()
                            produced = True
                            phase = "verify"
                            with diag.span("preview.sample.verify"):
                                rec = run_suite(pkg.data, out, plan.to_dict(), str(f), str(out_dir / "samples" / names[str(f)]))
                            checks = {c.id: c.status for c in rec.checks}
                            sv.stages.append({"stage": i, "name": label, "passed": rec.passed, "checks": checks})
                            _log_verification(log, f"{f.name} · stage {i} ({label})", rec)
                            data = out
                            sv.checks = checks
                            if not rec.passed:
                                sv.passed = False
                                break
                        except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
                            exc = diag.exception_record(e, phase, data)
                            exc["chain_stage"] = i
                            exc["reason"] = f"stage {i} ({label}): {exc.get('reason', '')}"
                            sv.exception = exc
                            diag.event("preview.sample_failed", level="error", source=str(f), chain_stage=i,
                                       stage=phase, exception=exc)
                            sv.stages.append({"stage": i, "name": label, "exception": {"code": exc.get("code"),
                                                                                        "reason": exc.get("reason")}})
                            log.detail(f"sample {f.name}: stage {i} EXCEPTION [{exc['code']}] at {phase}: {exc['reason']}")
                            if exc.get("traceback") and exc.get("kind") != "refusal":
                                log.block(exc["traceback"])
                else:
                    if sv.exception is None and sv.passed is None:
                        sv.passed = True
                if sv.passed and chain.get("assertions"):
                    sv.outcomes = assert_outcomes(data, chain["assertions"])
                    sv.checks["FINAL"] = "PASS" if all(entry["status"] == "PASS" for entry in sv.outcomes) else "FAIL"
                    sv.passed = sv.checks["FINAL"] == "PASS"
                    for entry in sv.outcomes:
                        log.detail(f"sample {f.name}: final requirement {entry['index']} {entry['status']}: {entry['summary']}")
                if produced:
                    sv.output = out_dir / "samples" / names[str(f)]
                    sv.output.parent.mkdir(parents=True, exist_ok=True)
                    sv.output.write_bytes(data)
                pv.samples.append(sv)
                input_changed = (input_changed or changed_since_preview(key, files, token, token_inputs) is not None
                                 or bool(changed_inputs(snapshots)))
                diag.event("preview.sample_finished", source=str(f), passed=sv.passed,
                           exception=sv.exception, checks=sv.checks, stages=sv.stages)
            pv.renderer = render_samples(pv.samples, out_dir, renderer, do_render, pv.warnings, log=log)
            if input_changed or changed_since_preview(key, files, token, token_inputs) is not None or changed_inputs(snapshots):
                pv.token = ""
                pv.warnings.append("An input changed while the preview was being prepared; press Preview again before Run.")
            (out_dir / "chain.json").write_text(json.dumps(chain, indent=2, ensure_ascii=False), encoding="utf-8")
            write_preview_files(pv)
            log.detail(f"approval token {pv.token}")
            return pv
        except BaseException as e:
            log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
            log.block(traceback.format_exc())
            raise
        finally:
            log.close()
