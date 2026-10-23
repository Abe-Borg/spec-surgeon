"""A plan per structural layout: one user intent, one EditSpec per layout, one verification per file.

A batch may mix projects whose documents are built differently (styled with PR1 articles, Normal-only with typed
numbers, Heading-styled). One selector cannot say "the article titles" for all of them. A LAYOUT PLAN carries one
EditSpec per layout::

    {"kind": "layouts", "request": "...", "summary": "...",
     "layouts": [{"id": "A", "label": "styled, PR1 articles", "pattern_ids": ["ab12cd34ef"], "spec": {EditSpec}},
                 {"id": "B", "label": "typed numbers",        "pattern_ids": ["0f9e8d7c6b"], "spec": {EditSpec}}]}

Every file is routed to the one layout whose ``pattern_ids`` include its structure pattern
(``fingerprint.pattern_id``). Each layout's spec is planned, previewed, applied and verified by the existing
single-spec machinery on that layout's files alone, so every delivered file still has exactly one specification
(CLAUDE.md invariant 7) and the independent verifier is unchanged. A file no layout covers is set aside at plan time
with a sentence (exception code ``layout``), never after a paid investigation. A plan with one layout is the ordinary
single-spec case; ``single_spec`` returns it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diagnostics as diag
from .editspec import SpecError, is_chain, validate_spec
from .fingerprint import fingerprint
from .package import DocxPackage

LAYOUT_CODE = "layout"


# ---- the plan --------------------------------------------------------------------------------------

def is_layout_plan(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "layouts"


def validate_layout_plan(plan: Any) -> None:
    """Raise SpecError unless ``plan`` is a well-formed layout plan whose every spec validates on its own."""
    if not isinstance(plan, dict) or plan.get("kind") != "layouts":
        raise SpecError("a layout plan is an object with kind \"layouts\"", "layouts")
    if not isinstance(plan.get("request"), str) or not plan["request"].strip():
        raise SpecError("a layout plan carries the user's request", "layouts")
    layouts = plan.get("layouts")
    if not isinstance(layouts, list) or not layouts:
        raise SpecError("layouts must be a non-empty list", "layouts")
    seen_ids: set = set()
    seen_patterns: Dict[str, str] = {}
    for n, layout in enumerate(layouts, 1):
        if not isinstance(layout, dict):
            raise SpecError(f"layout #{n} must be an object", "layouts")
        ident = layout.get("id")
        if not isinstance(ident, str) or not ident.strip():
            raise SpecError(f"layout #{n} needs an id", "layouts")
        if ident in seen_ids:
            raise SpecError(f"layout id {ident!r} is used twice", "layouts")
        seen_ids.add(ident)
        patterns = layout.get("pattern_ids")
        if not isinstance(patterns, list) or not patterns or not all(isinstance(p, str) and p for p in patterns):
            raise SpecError(f"layout {ident!r} needs a non-empty list of pattern ids", "layouts")
        for p in patterns:
            if p in seen_patterns and seen_patterns[p] != ident:
                raise SpecError(f"pattern {p} belongs to layouts {seen_patterns[p]!r} and {ident!r}; a file can have "
                                f"only one plan", "layouts")
            seen_patterns[p] = ident
        if "label" in layout and not isinstance(layout["label"], str):
            raise SpecError(f"layout {ident!r}: label must be text", "layouts")
        spec = layout.get("spec")
        if not isinstance(spec, dict):
            raise SpecError(f"layout {ident!r} needs a spec", "layouts")
        if is_chain(spec):
            from .chain import validate_chain
            validate_chain(layout_spec(plan, layout))
        else:
            validate_spec(layout_spec(plan, layout))


def layout_spec(plan: Dict[str, Any], layout: Dict[str, Any]) -> Dict[str, Any]:
    """The layout's spec as the engine executes it: the plan's request, and ``applies_to`` pinned to the layout's
    patterns so ``plan_spec`` refuses any file routed to it by mistake (defence in depth). A layout whose ops the app
    composed into ordered steps is a chain; its first stage carries the pin (a later stage reads the step before it,
    whose structure the first stage already checked)."""
    spec = copy.deepcopy(layout["spec"])
    if is_chain(spec):
        for i, st in enumerate(spec.get("stages") or []):
            own = st.get("spec") if isinstance(st, dict) else None
            if not isinstance(own, dict):
                continue
            if not own.get("request"):
                own["request"] = plan.get("request", "")
            if i == 0:
                own["applies_to"] = {"pattern_ids": list(layout["pattern_ids"])}
        return spec
    spec.setdefault("request", plan.get("request", ""))
    if not spec.get("request"):
        spec["request"] = plan.get("request", "")
    spec["applies_to"] = {"pattern_ids": list(layout["pattern_ids"])}
    return spec


def plan_from_submission(spec: Dict[str, Any], request: str) -> Dict[str, Any]:
    """The model's submission ``{"summary": ..., "layouts": [...]}`` as a layout plan carrying the USER's request
    (never text the model wrote in a request field), each layout's spec carrying it too."""
    layouts = []
    for layout in spec.get("layouts") or []:
        if not isinstance(layout, dict):
            layouts.append(layout)
            continue
        own = copy.deepcopy(layout)
        if isinstance(own.get("spec"), dict):
            if is_chain(own["spec"]):
                for st in own["spec"]["stages"]:
                    if isinstance(st, dict) and isinstance(st.get("spec"), dict):
                        st["spec"]["request"] = request
            else:
                own["spec"]["request"] = request
        layouts.append(own)
    return {"kind": "layouts", "request": request, "summary": str(spec.get("summary") or ""),
            "assumptions": list(spec.get("assumptions") or []), "layouts": layouts}


def single_spec(plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A one-layout plan is an ordinary spec (without the pattern pin, so a file of a new pattern is still planned)."""
    if is_layout_plan(plan) and len(plan.get("layouts") or []) == 1:
        spec = copy.deepcopy(plan["layouts"][0]["spec"])
        spec.setdefault("request", plan.get("request", ""))
        return spec
    return None


def layout_patterns(plan: Dict[str, Any]) -> Dict[str, str]:
    """pattern id → layout id."""
    return {p: layout["id"] for layout in plan["layouts"] for p in layout["pattern_ids"]}


# ---- routing ---------------------------------------------------------------------------------------

@dataclass
class Routing:
    files: Dict[str, List[Path]] = field(default_factory=dict)          # layout id → files, in input order
    patterns: Dict[str, str] = field(default_factory=dict)              # str(file) → pattern id
    unassigned: List[Tuple[Path, str]] = field(default_factory=list)    # (file, pattern id) no layout covers
    unreadable: List[Tuple[Path, str]] = field(default_factory=list)    # (file, error)

    def layout_of(self, path: Path) -> Optional[str]:
        for ident, files in self.files.items():
            if path in files:
                return ident
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {"files": {k: [str(p) for p in v] for k, v in self.files.items()},
                "unassigned": [{"source": str(p), "pattern_id": pid} for p, pid in self.unassigned],
                "unreadable": [{"source": str(p), "error": e} for p, e in self.unreadable]}


def route(files: Sequence[Path], plan: Dict[str, Any]) -> Routing:
    """Every file to the one layout whose patterns include its own; files are fingerprinted here, never guessed."""
    owners = layout_patterns(plan)
    routing = Routing(files={layout["id"]: [] for layout in plan["layouts"]})
    for f in files:
        f = Path(f)
        try:
            pid = fingerprint(DocxPackage.from_path(f))["pattern_id"]
        except Exception as e:  # noqa: BLE001 — an unreadable file is listed, never fatal
            routing.unreadable.append((f, f"{type(e).__name__}: {e}"))
            continue
        routing.patterns[str(f)] = pid
        if pid in owners:
            routing.files[owners[pid]].append(f)
        else:
            routing.unassigned.append((f, pid))
    return routing


def unassigned_exception(path: Path, pattern_id: str, plan: Dict[str, Any]) -> Dict[str, Any]:
    labels = [layout.get("label") or layout["id"] for layout in plan["layouts"]]
    return {"code": LAYOUT_CODE, "kind": "refusal", "stage": "plan",
            "reason": f"{Path(path).name} is built differently from the documents the plan covers "
                      f"({', '.join(labels)}); it was left untouched. Attach it on its own, or ask for a plan that "
                      f"covers it.", "details": {"pattern_id": pattern_id}}


def unreadable_exception(path: Path, error: str) -> Dict[str, Any]:
    return {"code": "unreadable", "kind": "environment", "stage": "open",
            "reason": f"{Path(path).name} could not be opened as a Word package ({error}); it was left untouched."}


# ---- approval token --------------------------------------------------------------------------------

def layout_inputs(plan: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """What the user's approval binds: every layout's own approval inputs on its own files, the routing, and the
    files left out. Recomputed by ``run_layouts`` and compared with the preview's token before anything runs."""
    from .editspec import approval_key
    from .preview import approval_inputs
    routing = route(files, plan)
    inputs = {"kind": "layouts", "layouts": {}, "unassigned": sorted(str(p) for p, _ in routing.unassigned),
              "unreadable": sorted(str(p) for p, _ in routing.unreadable), "files": {}}
    for layout in plan["layouts"]:
        spec = layout_spec(plan, layout)
        own = approval_inputs(approval_key(spec), routing.files[layout["id"]])
        inputs["layouts"][layout["id"]] = own
        inputs["files"].update(own.get("files", {}))
    return inputs


def layout_token(plan: Dict[str, Any], files: Sequence[Path]) -> str:
    return _token_from_inputs(layout_inputs(plan, files))


def narrow_layout_inputs(inputs: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """The approved inputs restricted to ``files``, part of the approved batch, each file with the pin the user
    approved: exactly what ``layout_inputs`` gives for those files alone while none of them changed. A run on part
    of an approved batch (the user set the other documents aside) is compared against this, so every file that
    runs is still held to its approved identity and a file outside the approval never gets in."""
    keep = {str(Path(f).resolve()) for f in files}
    out = {"kind": inputs.get("kind", "layouts"), "layouts": {},
           "unassigned": [p for p in inputs.get("unassigned", []) if p in keep],
           "unreadable": [p for p in inputs.get("unreadable", []) if p in keep],
           "files": {p: pin for p, pin in (inputs.get("files") or {}).items() if p in keep}}
    for layout_id, own in (inputs.get("layouts") or {}).items():
        out["layouts"][layout_id] = {**own, "files": {p: pin for p, pin in (own.get("files") or {}).items() if p in keep}}
    return out


def _token_from_inputs(inputs: Dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


# ---- preview ---------------------------------------------------------------------------------------

@dataclass
class LayoutBatchPlan:
    """The combined dry plan every layout's preview produced, in the shape the window and the session read
    (``files`` with ``status`` planned/exception, ``exceptions``, ``to_text``)."""
    spec: Dict[str, Any]
    files: List[Any] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def exceptions(self) -> List[Any]:
        return [f for f in self.files if f.status == "exception"]

    def to_dict(self) -> Dict[str, Any]:
        return {"request": self.spec.get("request"), "files": [f.to_dict() for f in self.files],
                "skipped": self.skipped, "seconds": round(self.seconds, 2),
                "planned": len(self.files) - len(self.exceptions), "exceptions": len(self.exceptions)}

    def to_text(self, per_file: bool = True) -> str:
        out = [f"LAYOUT PLAN {self.spec.get('request')!r}: {len(self.files)} file(s), "
               f"{len(self.files) - len(self.exceptions)} plan cleanly, {len(self.exceptions)} exception(s) "
               f"[{self.seconds:.2f}s, nothing written]"]
        for f in self.files:
            if f.status == "exception":
                out.append(f"   EXCEPTION {Path(f.source).name}: [{f.exception['code']}] {f.exception['reason']}")
            elif per_file:
                out.append(f"   {Path(f.source).name}: " + " · ".join(getattr(f, "summary", [])))
        return "\n".join(out)


@dataclass
class LayoutPreview:
    out_dir: Path
    plan: Dict[str, Any]
    routing: Routing
    previews: List[Tuple[Dict[str, Any], Any]] = field(default_factory=list)   # (layout, preview.Preview)
    batch_plan: LayoutBatchPlan = None  # type: ignore[assignment]
    samples: List[Any] = field(default_factory=list)
    token: str = ""
    token_inputs: Dict[str, Any] = field(default_factory=dict)
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    review: Any = None
    review_ack: str = ""
    log_path: Optional[Path] = None
    input_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def layout_for_sample(self, sample: Any) -> Optional[Dict[str, Any]]:
        for layout, pv in self.previews:
            if any(s is sample for s in pv.samples):
                return layout
        return None

    def to_text(self) -> str:
        out = [f"LAYOUT PREVIEW — {self.plan.get('request')!r}: {len(self.previews)} layout(s)"]
        for layout, pv in self.previews:
            n = len(self.routing.files.get(layout["id"], []))
            out.append(f"\n== Layout {layout['id']} · {layout.get('label') or ''} · {n} file(s)")
            out.append(pv.to_text())
        for path, pid in self.routing.unassigned:
            out.append(f"\nSET ASIDE {Path(path).name}: {unassigned_exception(path, pid, self.plan)['reason']}")
        for path, err in self.routing.unreadable:
            out.append(f"\nSET ASIDE {Path(path).name}: {unreadable_exception(path, err)['reason']}")
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        return "\n".join(out) + f"\napproval token: {self.token}"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "layouts", "token": self.token, "renderer": self.renderer, "warnings": self.warnings,
                "routing": self.routing.to_dict(),
                "layouts": [{"id": layout["id"], "label": layout.get("label", ""), "pattern_ids": layout["pattern_ids"],
                             "preview": pv.to_dict()} for layout, pv in self.previews],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "approval_inputs": self.token_inputs}


def make_layout_preview(plan: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                        do_render: bool = True) -> LayoutPreview:
    """Each layout previewed by ``preview.make_preview`` on its own files, under ``out_dir/layout-<id>``; the token
    binds every layout's spec, its files and the routing."""
    from .batch import expand_inputs
    from .planner import FilePlan
    from .preview import make_chain_preview, make_preview
    validate_layout_plan(plan)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    t0 = time.perf_counter()
    with diag.span("preview.layouts", out_dir=str(out_dir), layouts=len(plan["layouts"])):
        token_inputs = layout_inputs(plan, files)
        routing = route(files, plan)
        result = LayoutPreview(out_dir=out_dir, plan=plan, routing=routing, token_inputs=token_inputs,
                               token=_token_from_inputs(token_inputs))
        combined = LayoutBatchPlan(spec=plan)
        for layout in plan["layouts"]:
            own = routing.files[layout["id"]]
            if not own:
                result.warnings.append(f"No attached document has the layout “{layout.get('label') or layout['id']}”; "
                                       f"that part of the plan changes nothing.")
                continue
            own_spec = layout_spec(plan, layout)
            maker = make_chain_preview if is_chain(own_spec) else make_preview
            pv = maker(own_spec, [str(f) for f in own], out_dir / f"layout-{layout['id']}",
                       renderer=renderer, do_render=do_render)
            result.previews.append((layout, pv))
            result.samples.extend(pv.samples)
            result.warnings.extend(pv.warnings)
            result.input_snapshots.extend(pv.input_snapshots)
            result.renderer = result.renderer or pv.renderer
            result.log_path = result.log_path or pv.log_path
            combined.files.extend(pv.batch_plan.files)
            combined.skipped.extend(pv.batch_plan.skipped)
            combined.seconds += pv.batch_plan.seconds
        for path, pid in routing.unassigned:
            combined.files.append(FilePlan(Path(path), pid, "exception", exception=unassigned_exception(path, pid, plan)))
        for path, err in routing.unreadable:
            combined.files.append(FilePlan(Path(path), None, "exception", exception=unreadable_exception(path, err)))
        combined.seconds = time.perf_counter() - t0
        result.batch_plan = combined
        result.warnings = list(dict.fromkeys(result.warnings))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "layouts.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
        (out_dir / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return result


# ---- run -------------------------------------------------------------------------------------------

@dataclass
class LayoutResult:
    out_dir: Path
    plan: Dict[str, Any]
    routing: Routing
    batches: List[Tuple[Dict[str, Any], Any]] = field(default_factory=list)   # (layout, batch.BatchResult)
    files: List[Any] = field(default_factory=list)                            # every file's FileResult, input order
    timings: Dict[str, float] = field(default_factory=dict)
    stopped: Optional[str] = None

    def counts(self) -> Dict[str, int]:
        c = {"PASS": 0, "FAIL": 0, "EXCEPTION": 0, "PENDING": 0}
        for f in self.files:
            c[f.status] += 1
        return c

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": "layouts", "request": self.plan.get("request"), "routing": self.routing.to_dict(),
                "layouts": [{"id": layout["id"], "label": layout.get("label", ""), "out_dir": str(b.out_dir),
                             "counts": b.counts()} for layout, b in self.batches],
                "counts": self.counts(), "timings": self.timings, "stopped": self.stopped}


def run_layouts(inputs: Sequence[str], plan: Dict[str, Any], out_dir: Path, *, renderer: str = "auto",
                do_render: bool = True, progress=None, approved_inputs: Optional[Dict[str, Any]] = None,
                worker_mode: Optional[str] = None) -> LayoutResult:
    """Run every layout's spec on its own files (``batch.run_batch`` under ``out_dir/layout-<id>``), copies only.
    Each layout batch delivers, records and reports exactly as a single-spec run does; a file no layout covers is an
    EXCEPTION here, untouched. ``approved_inputs`` are the preview's (``layout_inputs``): the run refuses when the
    files, the plan or the routing moved since the user approved."""
    from .batch import FileResult, expand_inputs, run_batch
    validate_layout_plan(plan)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    log = progress or (lambda m: None)
    t0 = time.perf_counter()
    with diag.span("batch.layouts", out_dir=str(out_dir), layouts=len(plan["layouts"])) as outcome:
        current = layout_inputs(plan, files)
        if approved_inputs is not None and _token_from_inputs(approved_inputs) != _token_from_inputs(current):
            raise ValueError("a file, the plan or the way the files are grouped changed since the preview — "
                             "check the documents again before creating copies")
        routing = route(files, plan)
        result = LayoutResult(out_dir=out_dir, plan=plan, routing=routing)
        by_source: Dict[str, Any] = {}
        for layout in plan["layouts"]:
            own = routing.files[layout["id"]]
            if not own:
                continue
            label = layout.get("label") or layout["id"]
            log(f"Layout {layout['id']} ({label}): {len(own)} file(s)…")
            own_spec = layout_spec(plan, layout)
            if is_chain(own_spec):
                from .chain import run_chain
                batch = run_chain([str(f) for f in own], own_spec, out_dir / f"layout-{layout['id']}",
                                  renderer=renderer, do_render=do_render, in_place=False, progress=log,
                                  approved_inputs=current["layouts"].get(layout["id"]), worker_mode=worker_mode)
            else:
                batch = run_batch([str(f) for f in own], "edit-spec", {}, out_dir / f"layout-{layout['id']}",
                                  renderer=renderer, do_render=do_render, in_place=False, progress=log,
                                  spec=own_spec, approved_inputs=current["layouts"].get(layout["id"]),
                                  worker_mode=worker_mode)
            result.batches.append((layout, batch))
            for fr in batch.files:
                by_source[str(fr.source)] = fr
        for path, pid in routing.unassigned:
            by_source[str(path)] = FileResult(source=Path(path), exception=unassigned_exception(path, pid, plan))
        for path, err in routing.unreadable:
            by_source[str(path)] = FileResult(source=Path(path), exception=unreadable_exception(path, err))
        result.files = [by_source[str(f)] for f in files if str(f) in by_source]
        result.timings["total"] = time.perf_counter() - t0
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "layouts.json").write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
        (out_dir / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        _write_report(result)
        outcome.update(counts=result.counts(), timings=result.timings)
        return result


def is_layout_run(run_dir: Path) -> bool:
    """The folder ``run_layouts`` wrote: ``layouts.json`` at its root and no batch manifest of its own — every
    set-aside manifest is inside a ``layout-<id>`` batch folder."""
    run_dir = Path(run_dir)
    return (run_dir / "layouts.json").is_file() and not (run_dir / "set_aside.json").is_file()


def layouts_needing_work(run_dir: Path) -> List[Path]:
    """The layout batch folders whose set-aside manifest still lists files, in plan order: what recovery can take,
    one layout at a time, each against its own EditSpec. A document no layout covered is not among them — there is
    no plan to recover it against; it stays as it was and the report names it."""
    from .batch import load_set_aside
    run_dir = Path(run_dir)
    data = json.loads((run_dir / "layouts.json").read_text(encoding="utf-8"))
    out: List[Path] = []
    for entry in data.get("layouts", []):
        folder = run_dir / f"layout-{entry['id']}"
        try:
            manifest = load_set_aside(folder)
        except (OSError, ValueError):
            continue
        if manifest.get("files"):
            out.append(folder)
    return out


def _write_report(result: LayoutResult) -> None:
    lines = [f"# Layout run — {result.plan.get('request')!r}", ""]
    for layout, batch in result.batches:
        c = batch.counts()
        lines.append(f"## Layout {layout['id']} · {layout.get('label') or ''}")
        lines.append(f"{len(batch.files)} file(s): PASS {c['PASS']}  FAIL {c['FAIL']}  EXCEPTION {c['EXCEPTION']}; "
                     f"report: {batch.out_dir / 'BATCH_REPORT.md'}")
        lines.append("")
    for path, pid in result.routing.unassigned:
        lines.append(f"- SET ASIDE {Path(path).name}: {unassigned_exception(path, pid, result.plan)['reason']}")
    for path, err in result.routing.unreadable:
        lines.append(f"- SET ASIDE {Path(path).name}: {unreadable_exception(path, err)['reason']}")
    (result.out_dir / "LAYOUT_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
