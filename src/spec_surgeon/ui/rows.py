"""What the window's tables show, computed from the engine's own result objects (no Qt here, so it is tested
without a display), and the progress the Run step reads from ``run_batch``'s log lines."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

PREVIEW_COLUMNS = ["File", "Pattern", "Status", "What changes"]
RESULT_COLUMNS = ["File", "Result", "Details", "Output"]


@dataclass
class Row:
    cells: List[str]
    badge: str                    # pass | fail | exception | planned — colours the status column
    tooltip: str = ""
    open_path: Optional[Path] = None   # what a double-click opens
    status_col: int = 2

    @property
    def is_problem(self) -> bool:
        return self.badge in ("fail", "exception")


def preview_rows(preview: Any) -> List[Row]:
    rows = []
    for f in preview.batch_plan.files:
        name = Path(f.source).name
        pattern = (f.pattern_id or "?")[:8]
        if f.status == "exception":
            exc = f.exception or {}
            from ..report import explain_failure
            category, explanation = explain_failure(exc.get("code", ""), exc.get("reason", ""))
            rows.append(Row([name, pattern, "Exception", f"{category}: {exc.get('reason', '')}"], "exception",
                            tooltip=f"[{exc.get('code')}] {explanation}", open_path=Path(f.source)))
        else:
            first = f.summary[0].strip() if f.summary else "planned"
            rows.append(Row([name, pattern, "Planned", first], "planned",
                            tooltip="\n".join(s.strip() for s in f.summary), open_path=Path(f.source)))
    return rows


def preview_counts(preview: Any) -> dict:
    bp = preview.batch_plan
    return {"files": len(bp.files), "planned": len(bp.files) - len(bp.exceptions), "exceptions": len(bp.exceptions),
            "patterns": len(bp.patterns)}


def result_rows(batch: Any) -> List[Row]:
    from ..report import explain_failure
    rows = []
    out_dir = Path(batch.out_dir)
    for f in batch.files:
        name = Path(f.source).name
        record_md = out_dir / "records" / f"{Path(f.source).stem}.record.md"
        output = Path(f.output) if f.output else None
        if f.status == "EXCEPTION":
            exc = f.exception or {}
            category, explanation = explain_failure(exc.get("code", ""), exc.get("reason", ""))
            rows.append(Row([name, "Exception", f"{category}: {exc.get('reason', '')}", "— (left untouched)"],
                            "exception", tooltip=f"[{exc.get('code')}] {explanation}", open_path=None, status_col=1))
        elif f.status == "FAIL":
            failed = [c for c in f.record.checks if c.status == "FAIL"]
            detail = "Failed " + ", ".join(c.id for c in failed) + " — the copy is in failed/ (do not use it)"
            rows.append(Row([name, "FAIL", detail, output.name if output else ""], "fail",
                            tooltip="\n".join(f"{c.id} {c.name}: {c.summary}" for c in failed),
                            open_path=record_md if record_md.exists() else output, status_col=1))
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
                            tooltip="\n".join(t.problems), status_col=1))
    return rows


def result_counts(rows: List[Row]) -> dict:
    return {"PASS": sum(r.badge == "pass" for r in rows), "FAIL": sum(r.badge == "fail" for r in rows),
            "EXCEPTION": sum(r.badge == "exception" for r in rows)}


_TOTAL = re.compile(r"^(\d+) file\(s\); transform\+verify")
_DONE = re.compile(r"^\s+done (.+)$")
_RENDER = re.compile(r"^rendering ")


@dataclass
class ProgressTracker:
    """Reads ``run_batch``'s progress lines: ``"N file(s); transform+verify with W worker(s)"``, one
    ``"  done NAME"`` per file, then ``"rendering …"``. ``tests/test_ui_rows.py`` pins these lines."""
    total: int = 0
    done: int = 0
    phase: str = ""
    seen: List[str] = field(default_factory=list)

    def feed(self, line: str) -> Optional[Tuple[int, int, str]]:
        """(done, total, phase) when the line moves the progress; total 0 means "busy, no count"."""
        m = _TOTAL.match(line)
        if m:
            self.total, self.done, self.phase = int(m.group(1)), 0, "Editing and verifying"
            return self.done, self.total, self.phase
        m = _DONE.match(line)
        if m and self.total:
            self.done = min(self.total, self.done + 1)
            self.seen.append(m.group(1))
            return self.done, self.total, self.phase
        if _RENDER.match(line):
            self.phase = "Rendering pages"
            return 0, 0, self.phase
        return None
