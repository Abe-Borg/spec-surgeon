"""Planner — an EditSpec over a whole batch, dry (docs/history/NL_SURGICAL_EDITOR_PLAN.md §2, §7.1; Phase 3).

``plan_batch(inputs, spec)`` fingerprints every file, groups the batch by structure pattern, and plans the spec
on every file (predict first; nothing is applied, nothing written). Each file gets either a plan with a short
human summary (``summarize_plan``) or the exception it would raise — the same diagnosis the real run would
produce. Spec template files found among the inputs are skipped, as in a run.

This is what ``spec-surgeon plan-spec`` prints and what the preview (Phase 4) shows before anything runs:
"58 of 60 files plan cleanly; 2 raise `inheritance` — here is why".
"""
from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .editspec import is_template_file, plan_spec, spec_templates, validate_spec
from .fingerprint import fingerprint
from .ops import Plan
from .ops.base import text_diffs
from .package import DocxPackage


def _names(parts: Sequence[str], n: int = 6) -> str:
    short = [p.rsplit("/", 1)[-1] for p in parts]
    return ", ".join(short[:n]) + (f" … (+{len(short) - n})" if len(short) > n else "")


def _targets(a: Dict[str, Any]) -> str:
    out = [f"{a[k]} {label}" for k, label in (("expected_runs", "run(s)"), ("expected_marks", "paragraph mark(s)"),
                                              ("expected_paragraphs", "paragraph(s)"), ("expected_sections", "section(s)"),
                                              ("expected_removed", "reference(s)")) if isinstance(a.get(k), int)]
    if isinstance(a.get("expected_ids"), list):
        out.append(f"{len(a['expected_ids'])} style(s)")
    if a.get("kind") == "template_parts":
        out.append(f"{a.get('part')} ← {a.get('template_part')}")
    return ", ".join(out)


def summarize_plan(plan: Plan) -> List[str]:
    """A few plain-English lines: what changes, what is removed/added, revisions, assertions, flags."""
    lines: List[str] = []
    removed = set(plan.members_removed)
    added = set(plan.members_added)
    diffs = text_diffs(plan.diffs)                       # a structure-only declaration changes no text
    marks_only = [d for d in plan.diffs if getattr(d, "structure_only", False)]
    kept_diffs = [d for d in diffs if d.part not in removed and d.part not in added]
    if plan.members_removed:
        lines.append(f"members removed: {_names(plan.members_removed)}")
    if plan.members_added:
        lines.append(f"members added: {_names(plan.members_added)}")
    # a pure tracked deletion leaves the block text as it is (the words stay as w:delText): it shows up only as an
    # accepted view, and the summary must say so rather than "no block changes text"
    deletions = {(a["part"], a["block_index"]) for a in plan.accepted_views} - {(d.part, d.block_index) for d in plan.diffs}
    if diffs:
        by_part = Counter(d.part.rsplit("/", 1)[-1] for d in kept_diffs)
        lines.append(f"text: {len(kept_diffs)} block(s) change in kept parts"
                     + (f" ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})" if by_part else "")
                     + (f"; {len(diffs) - len(kept_diffs)} block(s) go/come with removed/added parts"
                        if len(diffs) != len(kept_diffs) else ""))
    if deletions:
        by_part = Counter(part.rsplit("/", 1)[-1] for part, _ in deletions)
        lines.append(f"text: {len(deletions)} block(s) get a tracked deletion (the deleted words stay visible as "
                     f"a redline until accepted) ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})")
    if not diffs and not deletions:
        lines.append("text: no block changes text")
    if marks_only:
        by_part = Counter(d.part.rsplit("/", 1)[-1] for d in marks_only)
        lines.append(f"structure: {len(marks_only)} block(s) lose or gain a mark that is not text (a comment mark, a "
                     f"field character …) ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})")
    rev = {k: v for k, v in plan.rev_delta.items() if v}
    if rev:
        lines.append("revisions: " + ", ".join(f"{k} {v:+d}" for k, v in sorted(rev.items())))
    for a in plan.property_assertions:
        n = _targets(a)
        lines.append(f"asserts {a.get('kind')}" + (f" on {n}" if n else "") + (f" [{a.get('op')}]" if a.get("op") else ""))
    lines.append(f"mode {plan.mode}; layout reference {plan.geometry_reference}"
                 + (f" ({plan.geometry_reason})" if plan.geometry_reason else ""))
    lines += [f"FLAG: {f}" for f in plan.flags]
    return lines


@dataclass
class FilePlan:
    source: Path
    pattern_id: Optional[str]
    status: str                                  # planned | exception
    summary: List[str] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None
    exception: Optional[Dict[str, Any]] = None
    ms: float = 0.0
    source_md5: Optional[str] = None             # exact bytes read for this plan, independently of later disk state

    def to_dict(self):
        result = {"source": str(self.source), "pattern_id": self.pattern_id, "status": self.status,
                "summary": self.summary, "exception": self.exception, "ms": round(self.ms, 1),
                "source_md5": self.source_md5}
        if self.plan and any(a.get("kind") == "character_hygiene" for a in self.plan.get("property_assertions", [])):
            result["character_changes"] = [c for a in self.plan["property_assertions"]
                                           if a.get("kind") == "character_hygiene" for c in a["changes"]]
        return result


@dataclass
class BatchPlan:
    spec: Dict[str, Any]
    files: List[FilePlan] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def patterns(self) -> Dict[str, List[FilePlan]]:
        out: Dict[str, List[FilePlan]] = {}
        for f in self.files:
            out.setdefault(f.pattern_id or "?", []).append(f)
        return out

    @property
    def exceptions(self) -> List[FilePlan]:
        return [f for f in self.files if f.status == "exception"]

    def to_dict(self):
        return {"request": self.spec.get("request"), "files": [f.to_dict() for f in self.files],
                "skipped": self.skipped, "seconds": round(self.seconds, 2),
                "patterns": {k: [Path(f.source).name for f in v] for k, v in self.patterns.items()},
                "planned": len(self.files) - len(self.exceptions), "exceptions": len(self.exceptions)}

    def to_text(self, per_file: bool = True) -> str:
        out = [f"EditSpec {self.spec.get('request')!r}: {len(self.files)} file(s), "
               f"{len(self.files) - len(self.exceptions)} plan cleanly, {len(self.exceptions)} exception(s); "
               f"{len(self.patterns)} structure pattern(s) [{self.seconds:.2f}s, nothing written]"]
        if self.skipped:
            out.append(f"skipped (the spec's template): {[Path(s).name for s in self.skipped]}")
        for pid, fs in sorted(self.patterns.items(), key=lambda kv: -len(kv[1])):
            out.append(f"\n-- pattern {pid}: {len(fs)} file(s)")
            shown = fs if per_file else fs[:1]
            for f in shown:
                if f.status == "exception":
                    out.append(f"   EXCEPTION {f.source.name}: [{f.exception['code']}] {f.exception['reason']}")
                else:
                    out.append(f"   {f.source.name}:")
                    out += [f"      {line}" for line in f.summary]
            if not per_file and len(fs) > 1:
                out.append(f"   … and {len(fs) - 1} more file(s) of this pattern")
        # Hygiene changes must be reviewable for EVERY file, even when other summaries
        # are grouped by structure pattern. JSON quoting exposes ordinary/NBSP differences.
        import json
        for f in self.files:
            changes = f.to_dict().get("character_changes", [])
            if changes:
                out.append(f"\nEXACT CHARACTER CHANGES: {f.source}")
                for c in changes:
                    before = json.dumps(c["before"], ensure_ascii=True)
                    after = json.dumps(c["after"], ensure_ascii=True)
                    out.append(f"   {c['part']} paragraph {c['paragraph']+1}, character {c['start']+1}: {before} → {after}")
        return "\n".join(out)


def plan_file(path: Path, spec: Dict[str, Any]) -> FilePlan:
    """One file's dry plan. Any failure — a refusal, a file that cannot even be read (truncated zip, not a .docx), or
    the engine's own error — is that file's exception record, classified as a run would (``unreadable``,
    ``internal-error`` …, with the stage, the op and the line of code); it never aborts the batch."""
    from .diagnostics import exception_record
    t0 = time.perf_counter()
    pid = None
    stage, data, op = "open", None, None
    try:
        data = Path(path).read_bytes()
        pkg = DocxPackage(data, name=str(path))
        pkg.main_part()
        stage = "fingerprint"
        pid = fingerprint(pkg)["pattern_id"]
        stage = "plan"

        def hook(phase, number, name, state, fragment=None):
            nonlocal op
            op = (number, name) if state == "start" else None
        plan, _ = plan_spec(pkg, spec, pid, trace=hook)
        fp = FilePlan(Path(path), pid, "planned", summarize_plan(plan), plan.to_dict())
    except Exception as e:  # noqa: BLE001 — a refusal, unreadable or unexpected: recorded, not raised
        fp = FilePlan(Path(path), pid, "exception", exception=exception_record(e, stage, data, op))
    fp.source_md5 = hashlib.md5(data).hexdigest() if data is not None else None
    fp.ms = (time.perf_counter() - t0) * 1000
    return fp


def plan_batch(inputs: Sequence[str], spec: Dict[str, Any]) -> BatchPlan:
    """Plan ``spec`` on every input file (dry). Raises SpecError for an unusable spec before reading any file."""
    from .batch import expand_inputs
    validate_spec(spec)
    t0 = time.perf_counter()
    files = expand_inputs(inputs)
    templates = spec_templates(spec)
    bp = BatchPlan(spec=spec)
    for f in files:
        if is_template_file(f, templates):
            bp.skipped.append(str(f))
            continue
        bp.files.append(plan_file(f, spec))
    bp.seconds = time.perf_counter() - t0
    return bp
