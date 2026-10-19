"""What the window's tables show, computed from the engine's own result objects (no Qt here, so it is tested
without a display), and the progress the Run step reads from ``run_batch``'s log lines."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

PREVIEW_COLUMNS = ["File", "Pattern", "Status", "What changes", "Pages before", "Pages after"]
RESULT_COLUMNS = ["File", "Result", "Details", "Output"]


@dataclass
class Row:
    cells: List[str]
    badge: str                    # pass | fail | exception | planned — colours the status column
    tooltip: str = ""
    open_path: Optional[Path] = None   # what a double-click opens
    status_col: int = 2
    diagnosis: str = ""           # Markdown: why it did not pass, where, how — a double-click shows it (before open_path)

    @property
    def is_problem(self) -> bool:
        return self.badge in ("fail", "exception")


def preview_rows(preview: Any) -> List[Row]:
    rows = []
    samples = {str(Path(s.source).resolve()): s for s in preview.samples}
    for f in preview.batch_plan.files:
        name = Path(f.source).name
        pattern = (f.pattern_id or "?")[:8]
        if f.status == "exception":
            exc = f.exception or {}
            from ..report import exception_diagnosis, explain_failure
            category, explanation = explain_failure(exc.get("code", ""), exc.get("reason", ""))
            rows.append(Row([name, pattern, "Exception", f"{category}: {exc.get('reason', '')}"], "exception",
                            tooltip=f"[{exc.get('code')}] {explanation}", open_path=Path(f.source),
                            diagnosis=exception_diagnosis(exc, name, heading="#")))
        else:
            first = f.summary[0].strip() if f.summary else "planned"
            rows.append(Row([name, pattern, "Planned", first], "planned",
                            tooltip="\n".join(s.strip() for s in f.summary), open_path=Path(f.source)))
        sample = samples.get(str(Path(f.source).resolve()))
        before = getattr(sample, "pages_before", None)
        after = getattr(sample, "pages_after", None)
        rows[-1].cells.extend([str(before) if before is not None else "—",
                               str(after) if after is not None else "—"])
        if before is not None and after != before:
            rows[-1].tooltip += f"\nPage count changed: {before} → {after}; inspect the rendered reflow."
        elif before is None:
            rows[-1].tooltip += "\nPage counts: Not rendered for this file."
    return rows


def preview_counts(preview: Any) -> dict:
    bp = preview.batch_plan
    return {"files": len(bp.files), "planned": len(bp.files) - len(bp.exceptions), "exceptions": len(bp.exceptions),
            "patterns": len(bp.patterns)}


def recovery_rows(result: Any, *, preview: bool = False) -> List[Row]:
    """A ready candidate is mechanically verified; it still needs the user's outcome review."""
    rows = []
    for f in result.files:
        ready = f.status == "READY"
        status = "Ready" if ready else ("Needs input" if f.status == "NEEDS_INPUT" else "Unresolved")
        detail = f.reason or ("Verification completed; inspect the intended result." if ready else "")
        if f.case_id:
            detail += f"\nRepair memory case: {f.case_id}"
        output = getattr(f, "delivered_to", None) or f.output
        cells = ([Path(f.source).name, "Recovery", status, detail, "—", "—"] if preview else
                 [Path(f.source).name, "PASS" if ready else status, detail, str(output or "—")])
        rows.append(Row(cells, "planned" if preview and ready else "pass" if ready else "exception",
                        tooltip=detail, open_path=Path(output) if output else None,
                        status_col=2 if preview else 1))
    return rows


def result_rows(batch: Any) -> List[Row]:
    from ..report import explain_failure, file_diagnosis
    rows = []
    out_dir = Path(batch.out_dir)
    for f in batch.files:
        name = Path(f.source).name
        record_md = out_dir / "records" / f"{getattr(f, 'record_stem', Path(f.source).stem)}.record.md"
        output = Path(f.output) if f.output else None
        if f.status == "EXCEPTION":
            exc = f.exception or {}
            category, explanation = explain_failure(exc.get("code", ""), exc.get("reason", ""))
            rows.append(Row([name, "Exception", f"{category}: {exc.get('reason', '')}", "— (left untouched)"],
                            "exception", tooltip=f"[{exc.get('code')}] {explanation}", open_path=None, status_col=1,
                            diagnosis=file_diagnosis(f, heading="#")))
        elif f.status == "FAIL":
            failed = [c for c in f.record.checks if c.status == "FAIL"]
            detail = "Failed " + ", ".join(c.id for c in failed) + " — the copy is in failed/ (do not use it)"
            rows.append(Row([name, "FAIL", detail, output.name if output else ""], "fail",
                            tooltip="\n".join(f"{c.id} {c.name}: {c.summary}" for c in failed),
                            open_path=record_md if record_md.exists() else output, status_col=1,
                            diagnosis=file_diagnosis(f, heading="#")))
        elif f.status == "PASS":
            if written_back(f):
                detail, where = "Verified and written back over the original", str(f.delivered_to)
            else:
                detail, where = "Verified — every check passed", output.name if output else ""
            rows.append(Row([name, "PASS", detail, where], "pass", tooltip=str(output or ""),
                            open_path=output if output and output.exists() else record_md, status_col=1))
        else:
            rows.append(Row([name, f.status, "", ""], "exception", status_col=1))
    return rows


def chain_rows(result: Any) -> List[Row]:
    """The Run step's table for a chain (``chain.ChainResult``): one row per file — PASS when it got through every
    stage, else the stage it stopped at and why (that stage's own diagnosis opens on a double-click)."""
    from ..report import file_diagnosis
    rows = []
    n = len(result.stages)
    for f in result.files:
        name = Path(f.source).name
        output = Path(f.output) if f.output else None
        if f.status == "PASS":
            if f.delivered_to and _same(f.delivered_to, f.source):
                detail, where = f"Verified at every stage ({n}) and written back over the original", str(f.delivered_to)
            else:
                detail, where = f"Verified at every stage ({n})", output.name if output else ""
            rows.append(Row([name, "PASS", detail, where], "pass", tooltip=" · ".join(
                f"stage {i} {st}" for i, st in enumerate(f.stage_status, 1)),
                            open_path=output if output and output.exists() else None, status_col=1))
            continue
        stage = f.stopped_at or n
        stage_name = result.stages[stage - 1].name if 0 < stage <= n else "?"
        fr = f.results[stage - 1] if 0 < stage <= len(f.results) else None
        diagnosis = (f"# {name} — stopped at stage {stage} of {n} ({stage_name})\n\n"
                     + (file_diagnosis(fr, heading="##") if fr is not None else f"- **Why:** {f.reason}"))
        badge = "fail" if f.status == "FAIL" else "exception"
        label = "FAIL" if f.status == "FAIL" else "Exception"
        detail = f"Stopped at stage {stage} of {n} ({stage_name}) — {f.category}: {f.reason}"
        tooltip = f"[{f.code}] {f.explanation}"
        where = "" if f.status == "EXCEPTION" and stage == 1 else (
            f"stages 1–{stage - 1} written back" if f.delivered_to is None and result.in_place and stage > 1 else
            (output.name if output and f.status == "FAIL" else ""))
        rows.append(Row([name, label, detail, where or ("— (left untouched)" if not result.in_place or stage == 1 else "")],
                        badge, tooltip=tooltip, open_path=None, status_col=1, diagnosis=diagnosis))
    return rows


def _same(a: Any, b: Any) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False


def written_back(f: Any) -> bool:
    """``FileResult.delivered_to`` is the output path for a copy run and the source path for an in-place run."""
    if not f.delivered_to:
        return False
    try:
        return Path(f.delivered_to).resolve() == Path(f.source).resolve()
    except OSError:
        return False


def generated_rows(trials: Any) -> List[Row]:
    rows = []
    for t in trials or []:
        name = Path(t.source).name
        if t.passed:
            rows.append(Row([name, "PASS", "Verified — AI-written transform", Path(t.output).name if t.output else ""],
                            "pass", open_path=Path(t.output) if t.output else None, status_col=1))
        else:
            rows.append(Row([name, "FAIL", "; ".join(t.problems)[:300] or "failed verification", ""], "fail",
                            tooltip="\n".join(t.problems), status_col=1,
                            diagnosis="\n".join([f"# {name} — the AI-written transform failed here", ""]
                                                + [f"- {x}" for x in t.problems or ["failed verification"]])))
    return rows


def result_counts(rows: List[Row]) -> dict:
    return {"PASS": sum(r.badge == "pass" for r in rows), "FAIL": sum(r.badge == "fail" for r in rows),
            "EXCEPTION": sum(r.badge == "exception" for r in rows)}


_TOTAL = re.compile(r"^(\d+) file\(s\); transform\+verify")
_DONE = re.compile(r"^\s+done (.+)$")
_RENDER = re.compile(r"^rendering ")
_STAGE = re.compile(r"^stage (\d+) of (\d+) — (.*?): \d+ file\(s\)")


@dataclass
class ProgressTracker:
    """Reads ``run_batch``'s progress lines: ``"N file(s); transform+verify with W worker(s)"``, one
    ``"  done NAME"`` per file, then ``"rendering …"`` — and, in a chain (``chain.run_chain``), the
    ``"stage i of n — NAME: k file(s)"`` line before each stage, which names the stage in the phase ("Stage 2 of 3 ·
    Editing and verifying"); each stage's own lines then move the bar from 0 again. ``tests/test_ui_rows.py`` pins
    these lines."""
    total: int = 0
    done: int = 0
    phase: str = ""
    stage: str = ""
    seen: List[str] = field(default_factory=list)

    def _phase(self, text: str) -> str:
        return f"{self.stage} · {text}" if self.stage else text

    def feed(self, line: str) -> Optional[Tuple[int, int, str]]:
        """(done, total, phase) when the line moves the progress; total 0 means "busy, no count"."""
        m = _STAGE.match(line)
        if m:
            self.stage = f"Stage {m.group(1)} of {m.group(2)} ({m.group(3)})"
            self.total, self.done, self.phase = 0, 0, self._phase("Starting")
            return 0, 0, self.phase
        m = _TOTAL.match(line)
        if m:
            self.total, self.done, self.phase = int(m.group(1)), 0, self._phase("Editing and verifying")
            return self.done, self.total, self.phase
        m = _DONE.match(line)
        if m and self.total:
            self.done = min(self.total, self.done + 1)
            self.seen.append(m.group(1))
            return self.done, self.total, self.phase
        if _RENDER.match(line):
            self.phase = self._phase("Rendering pages")
            return 0, 0, self.phase
        return None
