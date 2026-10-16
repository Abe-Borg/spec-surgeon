"""Summarize explicitly selected local recovery reports; never run a model or open a document.

Run with ``python -m spec_surgeon.recovery_evaluation manifest.json --out evaluation``.
The manifest supplies provenance and optional human judgments. Engine readiness, model visual
advice, and human acceptance remain separate measurements, including when a report has no
independent final assertions. This is a comparison aid, not another document verifier.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

MAX_JSON_BYTES = 32 * 1024 * 1024
EXECUTIONS = ("scripted", "live", "unknown")


class EvaluationError(ValueError):
    """A manifest or report cannot support an honest evaluation measurement."""


def _read(path: Path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise EvaluationError(f"JSON file exceeds {MAX_JSON_BYTES} bytes: {path}")
    data = path.read_bytes()
    try:
        value = json.loads(data, parse_constant=lambda s: (_ for _ in ()).throw(ValueError(s)))
    except (ValueError, UnicodeError) as exc:
        raise EvaluationError(f"Invalid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationError(f"JSON must contain an object: {path}")
    return value, hashlib.sha256(data).hexdigest()


def _number(value, where: str, *, integer: bool = False):
    if value is None:
        return None
    valid = type(value) is int if integer else type(value) in (int, float)
    if not valid or value < 0 or not math.isfinite(value):
        raise EvaluationError(f"{where} must be a nonnegative {'integer' if integer else 'number'} or null")
    return value


def _bool(value, where: str):
    if value is not None and type(value) is not bool:
        raise EvaluationError(f"{where} must be true, false or null")
    return value


def _object(value, where: str):
    if not isinstance(value, dict):
        raise EvaluationError(f"{where} must be an object")
    return value


def _case(case: Dict[str, Any], base: Path) -> Dict[str, Any]:
    ident = case.get("id")
    if not isinstance(ident, str) or not ident.strip():
        raise EvaluationError("Every case needs a nonempty id")
    location = case.get("report")
    if not isinstance(location, str) or not location:
        raise EvaluationError(f"{ident}: report must be a local JSON path")
    path = (base / location).resolve()
    report, digest = _read(path)
    files = report.get("files")
    if not isinstance(files, list) or not files or any(not isinstance(f, dict) for f in files):
        raise EvaluationError(f"{ident}: recovery report needs a nonempty files list")
    statuses = [f.get("status") for f in files]
    if any(s not in ("READY", "UNRESOLVED", "NEEDS_INPUT") for s in statuses):
        raise EvaluationError(f"{ident}: unrecognized recovery file status")
    attempts = report.get("attempts", [])
    if not isinstance(attempts, list):
        raise EvaluationError(f"{ident}: attempts must be a list")
    budget = _object(report.get("budget", {}), f"{ident}.budget")
    usage = _object(budget.get("usage", {}), f"{ident}.budget.usage")
    for field in ("model", "effort"):
        if budget.get(field) is not None and not isinstance(budget[field], str):
            raise EvaluationError(f"{ident}.budget.{field} must be text or null")
    served = usage.get("served_models", [])
    if not isinstance(served, list) or any(not isinstance(model, str) for model in served):
        raise EvaluationError(f"{ident}.budget.usage.served_models must be a list of model names")
    execution = case.get("execution", "unknown")
    if execution not in EXECUTIONS:
        raise EvaluationError(f"{ident}: execution must be one of {EXECUTIONS}")
    human = _object(case.get("human_review", {}), f"{ident}.human_review")
    judgment = {k: _bool(human.get(k), f"{ident}.human_review.{k}")
                for k in ("request_satisfied", "content_preserved", "visual_checked")}
    for k in ("reviewer", "notes"):
        if k in human and not isinstance(human[k], str):
            raise EvaluationError(f"{ident}.human_review.{k} must be text")
    judgment.update({k: human.get(k, "") for k in ("reviewer", "notes")})
    review = report.get("visual_review")
    if review is not None:
        _object(review, f"{ident}.visual_review")
    findings = (review or {}).get("findings", [])
    if not isinstance(findings, list) or any(not isinstance(f, dict) for f in findings):
        raise EvaluationError(f"{ident}: visual findings must be a list of objects")
    visual = ("not_reviewed" if not review or review.get("skipped") else
              "blocking_findings" if any(f.get("severity") == "block" for f in findings) else
              "advisory_findings" if findings else "advisory_no_findings")
    key = _object(report.get("key", {}), f"{ident}.key")
    assertions = key.get("assertions", [])
    if not isinstance(assertions, list):
        raise EvaluationError(f"{ident}: final assertions must be a list")
    final_passed = 0
    for file in files:
        outcomes = file.get("outcomes", [])
        if not isinstance(outcomes, list) or any(not isinstance(o, dict) for o in outcomes):
            raise EvaluationError(f"{ident}: file outcomes must be a list of objects")
        if assertions and len(outcomes) == len(assertions) and all(o.get("status") == "PASS" for o in outcomes):
            final_passed += 1
    # Prefer the ledger including retained reservations over known usage alone. A missing
    # estimate stays unknown; neither a free scripted run nor an incomplete bill is inferred.
    cost = _number(budget.get("estimated_cost_usd"), f"{ident}.estimated_cost_usd")
    row = {
        "id": ident, "execution": execution, "cohort": str(case.get("cohort", "unspecified")),
        "report": str(path), "report_sha256": digest, "files": len(files),
        "ready": statuses.count("READY"), "unresolved": statuses.count("UNRESOLVED"),
        "needs_input": statuses.count("NEEDS_INPUT"),
        "delivered": sum(bool(f.get("delivered_to")) for f in files),
        "attempts": len(attempts), "calls": _number(budget.get("calls"), f"{ident}.calls", integer=True),
        "model": budget.get("model"), "effort": budget.get("effort"), "served_models": served,
        "seconds": _number(report.get("seconds"), f"{ident}.seconds"),
        "estimated_cost_usd": cost,
        "reported_usage_cost_usd": _number(usage.get("cost_usd"), f"{ident}.usage.cost_usd"),
        "budget_stop": budget.get("exhausted_reason"),
        "final_assertion_count": len(assertions),
        "final_assertions_passed_files": final_passed,
        "model_visual_review": visual, "human_review": judgment,
        "human_interventions": _number(case.get("human_interventions"),
                                       f"{ident}.human_interventions", integer=True),
        "human_accepted": (all(judgment[k] is True for k in
                               ("request_satisfied", "content_preserved", "visual_checked"))
                           and statuses.count("READY") == len(files)
                           and (not assertions or final_passed == len(files))),
    }
    expected = _object(case.get("expect", {}), f"{ident}.expect")
    allowed = {"ready", "unresolved", "needs_input", "delivered", "calls_max", "budget_stopped"}
    if set(expected) - allowed:
        raise EvaluationError(f"{ident}: unknown expectation(s): {sorted(set(expected) - allowed)}")
    mismatches = []
    for name, want in expected.items():
        if name == "budget_stopped":
            if type(want) is not bool:
                raise EvaluationError(f"{ident}.expect.budget_stopped must be boolean")
            actual, matches = bool(row["budget_stop"]), bool(row["budget_stop"]) == want
        else:
            _number(want, f"{ident}.expect.{name}", integer=True)
            if want is None:
                raise EvaluationError(f"{ident}.expect.{name} cannot be null")
            actual = row["calls"] if name == "calls_max" else row[name]
            matches = actual is not None and (actual <= want if name == "calls_max" else actual == want)
        if not matches:
            mismatches.append(f"{name}: expected {want!r}, observed {actual!r}")
    row.update(expectation_status="FAIL" if mismatches else "PASS" if expected else "NOT_SCORED",
               expectation_mismatches=mismatches)
    return row


def _summary(rows):
    # Never mix scripted token prices with live model economics. Cost per accepted batch
    # includes failed live investigations too, and stays unknown if any live cost is unknown.
    live = [r for r in rows if r["execution"] == "live"]
    total = sum(r["estimated_cost_usd"] for r in live) if live and all(
        r["estimated_cost_usd"] is not None for r in live) else None
    accepted = sum(r["human_accepted"] for r in live)
    return {
                "cases": len(rows), "expectation_failures": sum(r["expectation_status"] == "FAIL" for r in rows),
                "live_batches": len(live), "live_human_accepted_batches": accepted,
                "live_estimated_cost_usd": total,
                "live_cost_per_human_accepted_batch_usd": total / accepted if total is not None and accepted else None}


def evaluate(manifest_path: Path) -> Dict[str, Any]:
    """Read only the manifest and its named JSON reports; no document paths are dereferenced."""
    manifest_path = Path(manifest_path).resolve()
    manifest, digest = _read(manifest_path)
    if type(manifest.get("version")) is not int or manifest["version"] != 1:
        raise EvaluationError("Evaluation manifest version must be 1")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases or any(not isinstance(c, dict) for c in cases):
        raise EvaluationError("Evaluation cases must be a nonempty list of objects")
    rows = [_case(c, manifest_path.parent) for c in cases]
    if len({r["id"] for r in rows}) != len(rows):
        raise EvaluationError("Evaluation case ids must be unique")
    groups = {}
    for row in rows:
        groups.setdefault((row["cohort"], row["model"], row["effort"]), []).append(row)
    return {"version": 1, "manifest": str(manifest_path), "manifest_sha256": digest,
            "cases": rows, "summary": _summary(rows), "groups": [
                {"cohort": cohort, "model": model, "effort": effort, **_summary(group)}
                for (cohort, model, effort), group in groups.items()],
            "limitations": [
                "Reports are recorded evidence, not a fresh verification of documents or outputs.",
                "READY and delivery do not establish that the original user request was satisfied.",
                "Final assertions cover only declared supported properties; model visual review is advisory.",
                "Execution provenance and human judgments are supplied by the evaluator, not inferred.",
                "Costs are listed-price estimates including reservations, not provider bills; unknown stays unknown."]}


def markdown(result: Dict[str, Any]) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = ["# Recovery evaluation", "", "Local report comparison; no models were called and no documents were uploaded.", "",
             "| Case | Cohort | Execution | Model / effort | Ready/files | Delivered | Calls | Estimated USD | Model visual review | Human accepted | Expectations |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |"]
    for row in result["cases"]:
        cost = "unknown" if row["estimated_cost_usd"] is None else f"{row['estimated_cost_usd']:.4f}"
        lines.append("| " + " | ".join(map(cell, [row["id"], row["cohort"], row["execution"],
            f"{row['model'] or 'unknown'} / {row['effort'] or 'unknown'}", f"{row['ready']}/{row['files']}",
            row["delivered"], row["calls"] if row["calls"] is not None else "unknown", cost,
            row["model_visual_review"], "yes" if row["human_accepted"] else "not established",
            row["expectation_status"]])) + " |")
    metric = result["summary"]["live_cost_per_human_accepted_batch_usd"]
    lines += ["", "Live estimated cost per human-accepted batch (including unsuccessful live runs): " +
              (f"${metric:.4f}." if metric is not None else "unknown / no accepted live batches."), ""]
    for row in result["cases"]:
        lines += [f"- {cell(row['id'])}: {cell(message)}" for message in row["expectation_mismatches"]]
    lines += ["", *[f"- {text}" for text in result["limitations"]], ""]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="new, empty local results folder")
    args = parser.parse_args(argv)
    try:
        result = evaluate(args.manifest)
        out = args.out.resolve()
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            raise EvaluationError("Choose a new empty evaluation folder; existing files are never overwritten")
        out.mkdir(parents=True, exist_ok=True)
        (out / "evaluation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        (out / "EVALUATION.md").write_text(markdown(result), encoding="utf-8")
    except (OSError, EvaluationError) as exc:
        parser.exit(2, f"Evaluation could not be completed: {exc}\n")
    print(out / "EVALUATION.md")
    return 1 if result["summary"]["expectation_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
