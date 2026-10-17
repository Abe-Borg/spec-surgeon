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
import re
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


def _resource_totals(report, ident):
    """Continuation allowances are separate; evaluation includes every recorded segment."""
    history = report.get("resource_history", [])
    if not isinstance(history, list):
        raise EvaluationError(f"{ident}.resource_history must be a list")
    segments = [*history, report.get("budget", {})]
    values = {"calls": [], "estimated_cost_usd": [], "reported_usage_cost_usd": []}
    served = set()
    for index, segment in enumerate(segments):
        prefix = f"{ident}.resource_segments[{index}]"
        segment = _object(segment, prefix)
        usage = _object(segment.get("usage", {}), prefix + ".usage")
        for field in ("model", "effort"):
            if segment.get(field) is not None and not isinstance(segment[field], str):
                raise EvaluationError(prefix + "." + field + " must be text or null")
        models = usage.get("served_models", [])
        if not isinstance(models, list) or any(not isinstance(model, str) for model in models):
            raise EvaluationError(prefix + ".usage.served_models must be a list of model names")
        served.update(models)
        values["calls"].append(_number(segment.get("calls"), prefix + ".calls", integer=True))
        values["estimated_cost_usd"].append(_number(segment.get("estimated_cost_usd"), prefix + ".estimated_cost_usd"))
        values["reported_usage_cost_usd"].append(_number(usage.get("cost_usd"), prefix + ".usage.cost_usd"))
    return {**{key: sum(items) if all(item is not None for item in items) else None
               for key, items in values.items()},
            "served_models": sorted(served), "resource_segment_count": len(segments)}


def _case(case: Dict[str, Any], base: Path, *, paired: bool = False,
          measurement_scope: str = "recovery") -> Dict[str, Any]:
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
    totals = _resource_totals(report, ident)
    cost = totals["estimated_cost_usd"]
    row = {
        "id": ident, "execution": execution, "cohort": str(case.get("cohort", "unspecified")),
        "report": str(path), "report_sha256": digest, "files": len(files),
        "ready": statuses.count("READY"), "unresolved": statuses.count("UNRESOLVED"),
        "needs_input": statuses.count("NEEDS_INPUT"),
        "delivered": sum(bool(f.get("delivered_to")) for f in files),
        "attempts": len(attempts), "calls": totals["calls"],
        "model": budget.get("model"), "effort": budget.get("effort"), "served_models": totals["served_models"],
        "resource_segment_count": totals["resource_segment_count"],
        "seconds": _number(report.get("seconds"), f"{ident}.seconds"),
        "estimated_cost_usd": cost,
        "reported_usage_cost_usd": totals["reported_usage_cost_usd"],
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
    if paired:
        row.update(_comparison_metadata(case, report, row, measurement_scope))
        row["human_accepted"] = row["human_accepted"] and row["human_review_complete"]
    return row


def _sha256(value, where):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EvaluationError(f"{where} must be a lowercase SHA-256 digest")
    return value


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _comparison_metadata(case, report, row, scope):
    """Bind a comparison to recorded inputs, not a caller's matching batch label."""
    ident = row["id"]
    for field in ("pair_id", "cohort"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            raise EvaluationError(f"{ident}.{field} must be nonempty text for paired comparisons")
    variant = case.get("variant")
    if variant not in ("baseline", "current"):
        raise EvaluationError(f"{ident}.variant must be baseline or current")
    revision = case.get("revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise EvaluationError(f"{ident}.revision must be a full lowercase Git commit SHA")
    expected_digest = _sha256(case.get("report_sha256"), f"{ident}.report_sha256")
    if expected_digest != row["report_sha256"]:
        raise EvaluationError(f"{ident}: report SHA-256 changed since the manifest was recorded")
    session = report.get("recovery_id")
    if not isinstance(session, str) or not session.strip():
        raise EvaluationError(f"{ident}: paired reports require a recovery_id to prevent duplicate session accounting")
    request = report.get("request")
    if not isinstance(request, str) or not request.strip():
        raise EvaluationError(f"{ident}: paired reports require the exact recorded request")
    key = report.get("key", {})
    if key.get("request", request) != request:
        raise EvaluationError(f"{ident}: report request and approval-key request disagree")
    pins = _object(report.get("input_pins"), f"{ident}.input_pins")
    for path, digest in pins.items():
        if not isinstance(path, str) or not path:
            raise EvaluationError(f"{ident}: input pins need nonempty path names")
        _sha256(digest, f"{ident}.input_pins[{path!r}]")
    sources = [file.get("source") for file in report["files"]]
    if any(not isinstance(path, str) or path not in pins for path in sources):
        raise EvaluationError(f"{ident}: every reported source needs a recorded SHA-256 input pin")
    if len(set(sources)) != len(sources):
        raise EvaluationError(f"{ident}: duplicate sources cannot represent distinct evaluated files")
    for name in ("recovery_templates", "recovery_inputs"):
        paths = key.get(name)
        if not isinstance(paths, list) or any(not isinstance(p, str) or p not in pins for p in paths):
            raise EvaluationError(f"{ident}: key.{name} must list the SHA-256-pinned auxiliary inputs (possibly empty)")
    # Paths may move between checkouts. Content identity keeps duplicate documents as a
    # multiset and keeps source documents separate from templates/other auxiliary inputs.
    def portable(value, trail=()):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                # Canonicalize only schema fields that name attached files. Literal
                # words may themselves equal a pathname and must remain verbatim.
                attachment = ((key == "template" and (trail[-1:] == ("params",)
                                or len(trail) >= 2 and trail[-2] == "effects"))
                              or key == "file" and trail[-2:] == ("params", "table"))
                result[key] = ({"input_sha256": pins[item]} if attachment and isinstance(item, str)
                               and item in pins else portable(item, (*trail, key)))
            return result
        if isinstance(value, list):
            return [portable(v, (*trail, index)) for index, v in enumerate(value)]
        return value

    declaration = case.get("reviewed_outcome")
    if declaration is not None:
        declaration = _object(declaration, f"{ident}.reviewed_outcome")
        if set(declaration) != {"statement", "reviewer"} or any(
                not isinstance(v, str) or not v.strip() for v in declaration.values()):
            raise EvaluationError(f"{ident}.reviewed_outcome needs a nonempty statement and reviewer")
        outcome, outcome_basis = declaration["statement"], "reviewed_declaration"
    elif isinstance(key.get("adaptive_contract"), dict):
        outcome, outcome_basis = key["adaptive_contract"], "independent_contract"
    else:
        # Legacy recipes do not prove equivalent outcomes when their instructions change.
        # Different methods need an explicit human-reviewed common outcome declaration.
        outcome, outcome_basis = key.get("goal"), "legacy_recipe"
    identity = {
        "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
        "source_sha256": sorted(pins[path] for path in sources),
        "auxiliary_sha256": sorted(digest for path, digest in pins.items() if path not in sources),
        "outcome_basis": outcome_basis,
        "approved_outcome_sha256": _canonical_hash(portable({
            "outcome": outcome,
            "approved_words": key.get("approved_words", []), "assertions": key.get("assertions", [])})),
    }
    workflow = case.get("workflow")
    if scope == "workflow":
        workflow = _object(workflow, f"{ident}.workflow")
        fields = {"elapsed_seconds", "human_interventions", "additional_model_cost_usd"}
        if set(workflow) != fields:
            raise EvaluationError(f"{ident}.workflow must specify exactly {sorted(fields)}; use null for unknowns")
        elapsed = _number(workflow["elapsed_seconds"], f"{ident}.workflow.elapsed_seconds")
        interventions = _number(workflow["human_interventions"],
                                f"{ident}.workflow.human_interventions", integer=True)
        additional = _number(workflow["additional_model_cost_usd"],
                             f"{ident}.workflow.additional_model_cost_usd")
        cost = row["estimated_cost_usd"] + additional if (
            row["estimated_cost_usd"] is not None and additional is not None) else None
    else:
        if workflow is not None:
            raise EvaluationError(f"{ident}: workflow measurements require measurement_scope: workflow")
        elapsed, interventions, additional, cost = (row["seconds"], row["human_interventions"],
                                                   None, row["estimated_cost_usd"])
    human = row["human_review"]
    reviewed = (bool(human["reviewer"].strip()) and human["visual_checked"] is True and
                all(type(human[k]) is bool for k in ("request_satisfied", "content_preserved")))
    policies = {}
    for segment in [*report.get("resource_history", []), report.get("budget", {})]:
        policy = {"model": segment.get("model"), "effort": segment.get("effort")}
        for field in ("max_calls", "max_output_tokens", "max_total_tokens", "max_cost_usd"):
            policy[field] = _number(segment.get(field), f"{ident}.resource_policy.{field}",
                                    integer=field != "max_cost_usd")
        if policy["max_cost_usd"] is not None:
            policy["max_cost_usd"] = float(policy["max_cost_usd"])
        policies[_canonical_hash(policy)] = policy
    conditions = {"model": row["model"], "effort": row["effort"],
                  "renderer": report.get("renderer"), "options": report.get("options_snapshot", {}),
                  "resource_policies": [policies[key] for key in sorted(policies)],
                  "resource_segment_count": row["resource_segment_count"], "served_models": row["served_models"]}
    bindings = _object(key.get("adaptive_bindings", {}), f"{ident}.adaptive_bindings")
    if set(bindings) - set(sources):
        raise EvaluationError(f"{ident}: target bindings refer to sources outside this report")
    target_bindings = {}
    for source in sources:
        binding = bindings.get(source) or (key.get("adaptive_binding") if len(sources) == 1 else None)
        if not binding:
            continue
        binding = _object(binding, f"{ident}.binding")
        if binding.get("source_sha256") != pins[source]:
            raise EvaluationError(f"{ident}: target binding source hash differs from its input pin")
        effects = binding.get("effects")
        if effects is None:
            effects = {"legacy_title": {"targets": [binding.get("target")], "choice": binding.get("choice")}}
        for effect_id, effect in _object(effects, f"{ident}.binding.effects").items():
            effect = _object(effect, f"{ident}.binding.effects.{effect_id}")
            target_key = _canonical_hash([pins[source], effect_id])
            target_bindings.setdefault(target_key, []).append({
                "targets_sha256": _canonical_hash(portable(effect.get("targets", []))),
                "choice": effect.get("choice")})
    for targets in target_bindings.values():
        targets.sort(key=lambda target: (target["targets_sha256"], str(target["choice"])))
    return {"pair_id": case["pair_id"], "variant": variant, "revision": revision,
            "recovery_id": session, "target_bindings": target_bindings,
            "identity": identity, "identity_sha256": _canonical_hash(identity),
            "reviewed_outcome": declaration, "recorded_goal_sha256": _canonical_hash(portable(key.get("goal"))),
            "recorded_contract_sha256": _canonical_hash(portable(key.get("adaptive_contract"))),
            "measurement_scope": scope, "conditions": conditions, "human_review_complete": reviewed,
            "elapsed_seconds": elapsed, "human_interventions": interventions,
            "additional_model_cost_usd": additional, "measurement_estimated_cost_usd": cost}


def _complete_total(rows, field):
    values = [row[field] for row in rows]
    return sum(values) if values and all(v is not None for v in values) else None


def _comparison_summary(rows):
    reviewed = sum(row["human_review_complete"] for row in rows)
    accepted = sum(row["human_accepted"] for row in rows)
    total_cost = _complete_total(rows, "measurement_estimated_cost_usd") if all(
        row["execution"] == "live" for row in rows) else None
    elapsed = _complete_total(rows, "elapsed_seconds")
    interventions = _complete_total(rows, "human_interventions")
    return {"batches": len(rows), "files": sum(row["files"] for row in rows),
            "ready_files": sum(row["ready"] for row in rows),
            "human_reviewed_batches": reviewed, "human_accepted_batches": accepted,
            "human_review_coverage": reviewed / len(rows),
            "human_acceptance_rate": accepted / len(rows) if reviewed == len(rows) else None,
            "elapsed_seconds": elapsed, "human_interventions": interventions,
            "estimated_cost_usd": total_cost,
            "cost_per_human_accepted_batch_usd": total_cost / accepted if (
                total_cost is not None and accepted) else None}


def _delta(current, baseline):
    return current - baseline if current is not None and baseline is not None else None


def _paired_comparisons(rows):
    pairs, revisions, seen, sessions = {}, {}, set(), set()
    for row in rows:
        if row["report_sha256"] in seen:
            raise EvaluationError("A report cannot be reused for multiple comparison cases")
        seen.add(row["report_sha256"])
        if row["recovery_id"] in sessions:
            raise EvaluationError("Different snapshots of one recovery session cannot count as independent runs")
        sessions.add(row["recovery_id"])
        previous = revisions.setdefault(row["variant"], row["revision"])
        if previous != row["revision"]:
            raise EvaluationError("Each comparison variant must use one application revision")
        pair = pairs.setdefault(row["pair_id"], {})
        if row["variant"] in pair:
            raise EvaluationError(f"{row['pair_id']}: duplicate {row['variant']} case")
        pair[row["variant"]] = row
    if revisions.get("baseline") == revisions.get("current"):
        raise EvaluationError("Baseline and current must identify different application revisions")
    paired, groups = [], {}
    for pair_id, pair in pairs.items():
        if set(pair) != {"baseline", "current"}:
            raise EvaluationError(f"{pair_id}: each pair requires one baseline and one current case")
        baseline, current = pair["baseline"], pair["current"]
        for field in ("identity_sha256", "cohort", "execution"):
            if baseline[field] != current[field]:
                raise EvaluationError(f"{pair_id}: baseline/current {field} differs; these runs cannot be paired")
        for target in baseline["target_bindings"].keys() & current["target_bindings"].keys():
            before, after = baseline["target_bindings"][target], current["target_bindings"][target]
            if (sorted(t["targets_sha256"] for t in before) != sorted(t["targets_sha256"] for t in after)
                    and any(t["choice"] == "user" for t in [*before, *after])
                    and baseline["identity"]["outcome_basis"] != "reviewed_declaration"):
                raise EvaluationError(f"{pair_id}: conflicting approved target selections need a reviewed common outcome")
        changes = [key for key in baseline["conditions"]
                   if baseline["conditions"][key] != current["conditions"][key]]
        group = groups.setdefault((baseline["execution"], baseline["cohort"]), {"baseline": [], "current": []})
        for variant, row in pair.items():
            group[variant].append(row)
        reviewed = baseline["human_review_complete"] and current["human_review_complete"]
        paired.append({"pair_id": pair_id, "baseline": baseline["id"], "current": current["id"],
                       "identity_sha256": baseline["identity_sha256"], "execution": baseline["execution"],
                       "cohort": baseline["cohort"], "changed_conditions": changes,
                       "outcome_basis": baseline["identity"]["outcome_basis"],
                       "changed_implementation": baseline["recorded_goal_sha256"] != current["recorded_goal_sha256"],
                       "changed_target_bindings": baseline["target_bindings"] != current["target_bindings"],
                       "human_accepted_delta": (int(current["human_accepted"]) - int(baseline["human_accepted"]))
                       if reviewed else None,
                       "elapsed_seconds_delta": _delta(current["elapsed_seconds"], baseline["elapsed_seconds"]),
                       "human_interventions_delta": _delta(current["human_interventions"], baseline["human_interventions"]),
                       "estimated_cost_usd_delta": _delta(current["measurement_estimated_cost_usd"],
                                                           baseline["measurement_estimated_cost_usd"])
                       if baseline["execution"] == "live" else None})
    comparisons = []
    for (execution, cohort), group in groups.items():
        summaries = {variant: _comparison_summary(items) for variant, items in group.items()}
        comparisons.append({"execution": execution, "cohort": cohort, **summaries,
                            "current_minus_baseline": {
                                key: _delta(summaries["current"][key], summaries["baseline"][key])
                                for key in summaries["baseline"]}})
    return {"pairs": paired, "comparisons": comparisons, "revisions": revisions}


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
    if type(manifest.get("version")) is not int or manifest["version"] not in (1, 2):
        raise EvaluationError("Evaluation manifest version must be 1 or 2")
    paired = manifest["version"] == 2
    scope = manifest.get("measurement_scope", "recovery")
    if scope not in ("recovery", "workflow") or (not paired and scope != "recovery"):
        raise EvaluationError("measurement_scope must be recovery, or workflow with manifest version 2")
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases or any(not isinstance(c, dict) for c in cases):
        raise EvaluationError("Evaluation cases must be a nonempty list of objects")
    rows = [_case(c, manifest_path.parent, paired=paired, measurement_scope=scope) for c in cases]
    if len({r["id"] for r in rows}) != len(rows):
        raise EvaluationError("Evaluation case ids must be unique")
    groups = {}
    for row in rows:
        groups.setdefault((row["cohort"], row["model"], row["effort"]), []).append(row)
    result = {"version": manifest["version"], "manifest": str(manifest_path), "manifest_sha256": digest,
            "cases": rows, "summary": _summary(rows), "groups": [
                {"cohort": cohort, "model": model, "effort": effort, **_summary(group)}
                for (cohort, model, effort), group in groups.items()],
            "limitations": [
                "Reports are recorded evidence, not a fresh verification of documents or outputs.",
                "READY and delivery do not establish that the original user request was satisfied.",
                "Final assertions cover only declared supported properties; model visual review is advisory.",
                "Execution provenance and human judgments are supplied by the evaluator, not inferred.",
                "Costs are listed-price estimates including reservations, not provider bills; unknown stays unknown."]}
    if paired:
        result.update(measurement_scope=scope, **_paired_comparisons(rows))
        # A pooled baseline/current recovery price is neither variant's workflow cost.
        # Version 2 publishes economics only in the explicitly scoped comparisons.
        result["summary"] = {"cases": len(rows), "pairs": len(result["pairs"]),
                             "expectation_failures": sum(r["expectation_status"] == "FAIL" for r in rows),
                             **{execution + "_batches": sum(r["execution"] == execution for r in rows)
                                for execution in EXECUTIONS}}
        result.pop("groups")
        result["limitations"] += [
            "Pair identity checks recorded hashes and exact requests/outcomes; it does not authenticate reports or Git revisions.",
            "Different legacy recipes need an explicit reviewed common-outcome declaration; declarations do not prove equivalent results.",
            "Comparison deltas are current minus baseline; changed run conditions are disclosed, not statistically controlled.",
            "Incomplete human review leaves acceptance rates unknown. Failed and unreviewed runs remain in cost/time totals.",
            "Workflow measurements are evaluator-supplied; additional model costs must include all spending outside the recovery report."
            if scope == "workflow" else
            "Recovery scope excludes ordinary planning, work before recovery, human review time and costs outside the recovery report."]
    return result


def markdown(result: Dict[str, Any]) -> str:
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = ["# Recovery evaluation", "", "Local report comparison; no models were called and no documents were uploaded.", "",
             "| Case | Cohort | Execution | Model / effort | Ready/files | Delivered | Calls | Recovery estimated USD | Model visual review | Human accepted | Expectations |",
             "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- |"]
    for row in result["cases"]:
        cost = "unknown" if row["estimated_cost_usd"] is None else f"{row['estimated_cost_usd']:.4f}"
        lines.append("| " + " | ".join(map(cell, [row["id"], row["cohort"], row["execution"],
            f"{row['model'] or 'unknown'} / {row['effort'] or 'unknown'}", f"{row['ready']}/{row['files']}",
            row["delivered"], row["calls"] if row["calls"] is not None else "unknown", cost,
            row["model_visual_review"], "yes" if row["human_accepted"] else "not established",
            row["expectation_status"]])) + " |")
    if result["version"] == 2:
        def measured(value):
            return "unknown" if value is None else f"{value:.4f}" if type(value) is float else str(value)
        lines += ["", "## Paired comparison", "",
                  f"Measurement scope: **{result['measurement_scope']}**. Deltas are current minus baseline.", "",
                  "Baseline revision: `" + result["revisions"]["baseline"] + "`.",
                  "Current revision: `" + result["revisions"]["current"] + "`.", "",
                  "| Execution / cohort | Variant | Batches | Human reviewed | Human accepted | Acceptance rate | Elapsed seconds | Interventions | Estimated USD | USD / accepted batch |",
                  "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for comparison in result["comparisons"]:
            for variant in ("baseline", "current", "current_minus_baseline"):
                group = comparison[variant]
                lines.append("| " + " | ".join(map(cell, [
                    comparison["execution"] + " / " + comparison["cohort"], variant,
                    *[measured(group[k]) for k in ("batches", "human_reviewed_batches", "human_accepted_batches",
                       "human_acceptance_rate", "elapsed_seconds", "human_interventions", "estimated_cost_usd",
                       "cost_per_human_accepted_batch_usd")]])) + " |")
        lines += ["", "Scripted measurements demonstrate the harness only. Live economics exclude scripted/unknown execution.",
                  "Acceptance rates remain unknown until every batch in that cohort has a complete human review.", "",
                  "| Pair | Execution / cohort | Acceptance delta | Seconds delta | Intervention delta | USD delta | Outcome basis | Method changed | Target bindings changed | Changed conditions |",
                  "| --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- |"]
        for pair in result["pairs"]:
            lines.append("| " + " | ".join(map(cell, [pair["pair_id"], pair["execution"] + " / " + pair["cohort"],
                *[measured(pair[k]) for k in ("human_accepted_delta", "elapsed_seconds_delta",
                                             "human_interventions_delta", "estimated_cost_usd_delta")],
                pair["outcome_basis"], "yes" if pair["changed_implementation"] else "no",
                "yes" if pair["changed_target_bindings"] else "no",
                ", ".join(pair["changed_conditions"]) or "none recorded"])) + " |")
    else:
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
