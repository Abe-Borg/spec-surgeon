"""Version 3 local evaluation of batch, chain, campaign and recovery evidence.

Adapters record evidence, never rerun verification or infer human acceptance. Save an
adapted snapshot before pairing; it pins the report and every sidecar actually read.
Recovery evaluation versions 1 and 2 remain in recovery_evaluation unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
from pathlib import Path

from .recovery_evaluation import EvaluationError, MAX_JSON_BYTES, _resource_totals


VERSION = 3
READY = {"PASS", "READY", "EXPORTED"}
KINDS = {"batch", "chain", "campaign", "recovery"}
FILE_STATUSES = READY | {"FAIL", "EXCEPTION", "PENDING", "UNRESOLVED", "NEEDS_INPUT", "UNREADABLE",
                        "REFERENCE", "DRIFTED", "ERROR", "RUNNING"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise EvaluationError(f"{field} must be nonempty text")
    return value


def _number(value, field, *, integer=False):
    if value is not None and (type(value) not in ((int,) if integer else (int, float))
                              or value < 0 or not math.isfinite(value)):
        raise EvaluationError(f"{field} must be a nonnegative {'integer' if integer else 'number'} or null")
    return value


def _digest(value, field):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise EvaluationError(f"{field} must be a lowercase SHA-256")
    return value


def _read(path):
    path = Path(path).resolve()
    if path.stat().st_size > MAX_JSON_BYTES:
        raise EvaluationError(f"JSON exceeds {MAX_JSON_BYTES} bytes: {path}")
    data = path.read_bytes()
    if len(data) > MAX_JSON_BYTES:
        raise EvaluationError(f"JSON exceeds {MAX_JSON_BYTES} bytes: {path}")
    def invalid(value):
        raise EvaluationError(f"Nonfinite JSON number: {value}")
    value = json.loads(data, parse_constant=invalid)
    if not isinstance(value, dict):
        raise EvaluationError(f"Expected a JSON object: {path}")
    return value, hashlib.sha256(data).hexdigest()


def _kind(report):
    if "projects" in report and "documents" in report:
        return "campaign"
    if "recovery_id" in report or "input_pins" in report and "attempts" in report:
        return "recovery"
    if "chain" in report and "stages" in report:
        return "chain"
    if "job" in report and "files" in report:
        return "batch"
    raise EvaluationError("Unrecognized report; choose batch.json, chain.json, campaign.json or recovery.json")


def _check_rows(checks, *, source, evidence, stage=None):
    if not isinstance(checks, list) or any(not isinstance(c, dict) for c in checks):
        raise EvaluationError("Verification checks must be a list of objects")
    rows = []
    for check in checks:
        status = check.get("status")
        if status not in {"PASS", "FAIL", "NA", "N/A"}:
            raise EvaluationError("Unknown verification check status")
        rows.append({"source": source, "id": check.get("id", check.get("kind", check.get("index"))),
                     "status": "NA" if status == "N/A" else status,
                     "summary": check.get("summary"), "stage": stage, "evidence": evidence})
    return rows


def _sum_known(values):
    return sum(values) if values and all(v is not None for v in values) else None


def _ledger(history, label):
    if not isinstance(history, list):
        raise EvaluationError(f"{label} must be a list")
    if not history:
        return 0
    return _sum_known([_number(row.get("estimated_cost_usd"), label) for row in history])


def _resolve(base, value):
    path = Path(_text(value, "artifact path"))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _validate_record(record, *, root=True, depth=0):
    """Normalized snapshots are data too; JSON truthiness never grants readiness."""
    if depth > 12 or not isinstance(record, dict) or record.get("kind") not in KINDS:
        raise EvaluationError("Invalid normalized workflow record")
    if root:
        if record.get("schema") != "spec-surgeon-workflow-evaluation" or record.get("version") != VERSION:
            raise EvaluationError("Unsupported normalized workflow record")
        lineage = record.get("lineage")
        if not isinstance(lineage, list) or not lineage:
            raise EvaluationError("Normalized record requires retained artifact lineage")
        paths, sessions = set(), set()
        for artifact in lineage:
            if not isinstance(artifact, dict) or artifact.get("kind") not in KINDS | {"verification"}:
                raise EvaluationError("Invalid lineage artifact")
            path = _text(artifact.get("path"), "lineage path")
            _digest(artifact.get("sha256"), "lineage SHA-256")
            session = artifact.get("session_id")
            if session is not None:
                _text(session, "lineage session_id")
            if path in paths or session is not None and session in sessions:
                raise EvaluationError("Duplicated report path or session in lineage")
            paths.add(path)
            if session:
                sessions.add(session)
    files = record.get("files")
    if not isinstance(files, list) or not files:
        raise EvaluationError("Normalized record requires its complete files list")
    sources = []
    for file in files:
        if not isinstance(file, dict) or file.get("status") not in FILE_STATUSES:
            raise EvaluationError("Invalid normalized file status")
        sources.append(_text(file.get("source"), "file source"))
        if type(file.get("ready")) is not bool or type(file.get("delivered")) is not bool:
            raise EvaluationError("Normalized ready and delivered values must be booleans")
        if file["ready"] != (file["status"] in READY):
            raise EvaluationError("Normalized readiness contradicts file status")
    if len(sources) != len(set(sources)):
        raise EvaluationError("Duplicate normalized source files")
    all_pins = set()
    for key in ("source_pins", "auxiliary_pins"):
        entries = record.get(key)
        if not isinstance(entries, list):
            raise EvaluationError(f"Normalized {key} must be a list")
        for row in entries:
            if not isinstance(row, dict):
                raise EvaluationError("Invalid normalized pin")
            name = _text(row.get("path"), "pin path")
            if name in all_pins:
                raise EvaluationError("Source and auxiliary pins must be unique and disjoint")
            all_pins.add(name)
            if row.get("sha256") is not None:
                _digest(row["sha256"], "pin SHA-256")
    if {p["path"] for p in record["source_pins"]} != set(sources):
        raise EvaluationError("Source pins must match the complete file population")
    checks = record.get("check_coverage")
    if not isinstance(checks, list) or any(not isinstance(c, dict) or c.get("status") not in {"PASS", "FAIL", "NA"} for c in checks):
        raise EvaluationError("Invalid normalized check coverage")
    _number(record.get("estimated_model_cost_usd"), "normalized estimated cost")
    _number(record.get("reported_elapsed_seconds"), "normalized reported elapsed seconds")
    declaration = record.get("reviewed_outcome")
    if declaration is not None:
        if not isinstance(declaration, dict) or set(declaration) != {"statement", "reviewer"}:
            raise EvaluationError("reviewed_outcome requires statement and reviewer")
        for key, value in declaration.items():
            _text(value, "reviewed_outcome." + key)
    children = record.get("children")
    if not isinstance(children, list):
        raise EvaluationError("Normalized children must be a list")
    for child in children:
        _validate_record(child, root=False, depth=depth + 1)
    return record


def adapt_report(path, *, kind=None, evidence=None):
    """Snapshot actual reports, including sidecar checks and nested run lineage.

    ``evidence`` may supply historically unrecorded source_pins / auxiliary_pins
    ({path: sha256}), an exact request and an explicit reviewed_outcome declaration.
    Supplemental evidence is labelled evaluator-supplied and cannot replace recorded pins.
    The adapter reads JSON only; it never hashes current DOCX bytes as historical evidence.
    """
    path = Path(path).resolve()
    report, digest = _read(path)
    if report.get("schema") == "spec-surgeon-workflow-evaluation" and report.get("version") == VERSION:
        if kind is not None or evidence:
            raise EvaluationError("An adapted snapshot cannot be overlaid with new evidence")
        return copy.deepcopy(_validate_record(report))
    evidence = copy.deepcopy(evidence or {})
    if not isinstance(evidence, dict) or set(evidence) - {"source_pins", "auxiliary_pins", "request", "reviewed_outcome"}:
        raise EvaluationError("Unknown supplemental evaluation evidence")
    selected_kind = _kind(report)
    if kind is not None and kind != selected_kind:
        raise EvaluationError(f"Requested {kind} adapter but report is {selected_kind}")
    artifacts, seen = [], set()

    def visit(location, depth=0):
        location = Path(location).resolve()
        if depth > 12 or location in seen:
            raise EvaluationError("Cyclic, duplicated or too deeply nested report lineage")
        seen.add(location)
        raw, sha = _read(location)
        report_kind = _kind(raw)
        ident = raw.get("recovery_id") if report_kind == "recovery" else raw.get("id") if report_kind == "campaign" else None
        artifact = {"path": str(location), "sha256": sha, "kind": report_kind, "session_id": ident}
        artifacts.append(artifact)
        files = list(raw["documents"].values()) if report_kind == "campaign" else raw.get("files", [])
        references = [f for f in files if isinstance(f, dict) and f.get("status") == "REFERENCE"] if report_kind == "campaign" else []
        files = [f for f in files if f not in references]
        if not isinstance(files, list) or not files or any(not isinstance(f, dict) for f in files):
            raise EvaluationError("A workflow report requires a nonempty file population")
        sources = [f.get("source") for f in files]
        if any(not isinstance(s, str) or not s for s in sources) or len(set(sources)) != len(sources):
            raise EvaluationError("Report sources must be unique nonempty paths")
        pins = {f["source"]: f.get("source_sha256") for f in files}
        auxiliary = {f["source"]: f.get("source_sha256") for f in references}
        coverage, children, limitations = [], [], []
        outcome = raw.get("goal_contract")
        recipe = raw.get("spec") or raw.get("chain")
        request = (recipe or {}).get("request") or raw.get("request") or (raw.get("params") or {}).get("request")
        if report_kind == "batch":
            recipe = recipe or {"job": raw.get("job"), "params": raw.get("params")}
            # No model call occurs inside the deterministic engine. Conversation
            # and planning spending must be supplied as additional workflow cost.
            cost, cost_basis = 0, "deterministic_engine_only"
            for sidecar in sorted((location.parent / "records").glob("*.record.json")):
                record, record_sha = _read(sidecar)
                if record.get("source") not in sources:
                    continue
                artifacts.append({"path": str(sidecar.resolve()), "sha256": record_sha, "kind": "verification", "session_id": None})
                coverage.extend(_check_rows(record.get("checks", []), source=record["source"], evidence=str(sidecar.resolve())))
        elif report_kind == "chain":
            cost, cost_basis = 0, "deterministic_engine_only"
            for stage in raw.get("stages", []):
                child_path = _resolve(location.parent, stage["folder"]) / "batch.json"
                if child_path.is_file():
                    child = visit(child_path, depth + 1)
                    children.append(child)
                    coverage.extend([{**c, "stage": stage.get("index")} for c in child["check_coverage"]])
                else:
                    limitations.append(f"Missing stage report: {child_path}")
        elif report_kind == "recovery":
            key = raw.get("key", {})
            saved_pins = raw.get("input_pins", {})
            pins = {s: saved_pins.get(s) for s in sources}
            auxiliary = {p: h for p, h in saved_pins.items() if p not in sources}
            outcome = key.get("adaptive_contract") or key.get("goal_contract")
            recipe = key.get("goal")
            cost, cost_basis = _resource_totals(raw, "recovery")["estimated_cost_usd"], "recovery_segments_including_reservations"
            # Saved attempts contain summaries; preserve only explicitly recorded
            # final checks rather than inferring full V1-V10 coverage from READY.
            if not any(f.get("outcomes") for f in files):
                limitations.append("Recovery report has no recorded final outcome checks; detailed attempt records were not ingested.")
        else:
            goals = []
            cost_parts = []
            for project_id, project in raw["projects"].items():
                goals.append({"project_id": project_id, "goal": project.get("goal"),
                              "document_goals": project.get("document_goals")})
                project_children = []
                missing_child = False
                unique_paths = set()
                active_runs = project.get("runs", [])
                if any(run.get("document_id") for run in active_runs):
                    latest = {}
                    for run in active_runs:
                        latest[run.get("document_id")] = run["path"]
                    current_paths = set(latest.values())
                else:
                    current_paths = {active_runs[-1]["path"]} if active_runs else set()
                for run in [*project.get("archived_runs", []), *active_runs]:
                    child_path = _resolve(location.parent, run["path"]) / "recovery.json"
                    if child_path in unique_paths:
                        continue
                    unique_paths.add(child_path)
                    if child_path.is_file():
                        child = visit(child_path, depth + 1)
                        children.append(child)
                        project_children.append(child)
                        role = "current" if run["path"] in current_paths else "historical"
                        child["lineage_role"] = role
                        coverage.extend([{**check, "lineage_role": role} for check in child["check_coverage"]])
                        auxiliary.update({r["path"]: r["sha256"] for r in child["auxiliary_pins"]})
                    else:
                        missing_child = True
                        limitations.append(f"Missing recovery report: {child_path}")
                # Adaptive workspaces share the parent's execution ledger with
                # child recoveries. Never add both representations of that spend.
                if "resource_history" in project:
                    execution_cost = _ledger(project["resource_history"], "project.resource_history")
                else:
                    execution_cost = None if missing_child else _sum_known([c["estimated_model_cost_usd"] for c in project_children])
                planning_cost = _ledger(project.get("planning_resource_history", []), "project.planning_resource_history")
                cost_parts.append(_sum_known([execution_cost, planning_cost]))
            outcome, recipe = None, goals
            cost, cost_basis = _sum_known(cost_parts), "project_execution_ledger_or_unique_children_plus_planning"
        for entry in raw.get("input_snapshots", []):
            auxiliary[entry["source"]] = entry.get("sha256")
        for file in files:
            coverage.extend(_check_rows(file.get("outcomes", []), source=file["source"], evidence=str(location)))
        for target in (pins, auxiliary):
            for name, value in target.items():
                if value is not None:
                    _digest(value, f"pin[{name}]")
        normalized_files = [{"source": file["source"], "status": file.get("status"),
                             "ready": file.get("status") in READY,
                             "delivered": bool(file.get("delivered_to") or file.get("status") == "EXPORTED"),
                             "outcomes": copy.deepcopy(file.get("outcomes", []))} for file in files]
        if any(h is None for h in pins.values()):
            limitations.append("The report did not record a SHA-256 pin for every original source.")
        if not coverage:
            limitations.append("Detailed verification coverage is unknown.")
        return {"kind": report_kind, "session_id": ident, "request": request,
                "source_pins": [{"path": p, "sha256": h, "provenance": "report"} for p, h in sorted(pins.items())],
                "auxiliary_pins": [{"path": p, "sha256": h, "provenance": "report"} for p, h in sorted(auxiliary.items())],
                "approved_outcome": outcome, "recipe": recipe, "approved_words": raw.get("key", {}).get("approved_words", []),
                "assertions": raw.get("key", {}).get("assertions", []),
                "target_bindings": raw.get("goal_bindings") or raw.get("key", {}).get("adaptive_bindings") or raw.get("key", {}).get("adaptive_binding"),
                "files": normalized_files, "check_coverage": coverage, "renderer": raw.get("renderer"),
                "reported_elapsed_seconds": _number(raw.get("seconds") if report_kind == "recovery"
                    else raw.get("timings", {}).get("total"), "reported elapsed seconds"),
                "estimated_model_cost_usd": cost, "cost_basis": cost_basis,
                "conditions": {"model": raw.get("budget", {}).get("model"), "effort": raw.get("budget", {}).get("effort"),
                               "options": raw.get("options_snapshot"), "environment": raw.get("environment"),
                               "child_conditions": [c.get("conditions") for c in children]},
                "children": children, "limitations": limitations}

    result = visit(path)
    for key in ("source_pins", "auxiliary_pins"):
        supplied = evidence.get(key, {})
        if not isinstance(supplied, dict):
            raise EvaluationError(f"evidence.{key} must map paths to SHA-256 pins")
        index = {row["path"]: row for row in result[key]}
        if key == "source_pins" and set(supplied) - set(index):
            raise EvaluationError("Supplemental source pins cannot change the complete source population")
        for name, sha in supplied.items():
            _text(name, "pin path")
            _digest(sha, "supplemental pin")
            if name in index and index[name]["sha256"] not in (None, sha):
                raise EvaluationError("Supplemental evidence contradicts a recorded input pin")
            if name not in index:
                row = {"path": name, "sha256": sha, "provenance": "evaluator_supplied"}
                result[key].append(row)
                index[name] = row
            elif index[name]["sha256"] is None:
                index[name].update(sha256=sha, provenance="evaluator_supplied")
    if "request" in evidence:
        _text(evidence["request"], "supplemental request")
        if result["request"] not in (None, "", evidence["request"]):
            raise EvaluationError("Supplemental request contradicts the report")
        result["request"] = evidence["request"]
    if "reviewed_outcome" in evidence:
        declaration = evidence["reviewed_outcome"]
        if not isinstance(declaration, dict) or set(declaration) != {"statement", "reviewer"}:
            raise EvaluationError("reviewed_outcome requires statement and reviewer")
        for key, value in declaration.items():
            _text(value, "reviewed_outcome." + key)
        result["reviewed_outcome"] = declaration
    result.update(schema="spec-surgeon-workflow-evaluation", version=VERSION,
                  report={"path": str(path), "sha256": digest}, lineage=artifacts,
                  supplemental_evidence=evidence,
                  evidence_limits="Recorded automated checks and supplied provenance; no fresh document verification or human acceptance inferred.")
    return _validate_record(result)


def _identity(record):
    request = _text(record.get("request"), "paired request")
    source = record.get("source_pins", [])
    if not source or len(source) != len(record.get("files", [])):
        raise EvaluationError("Pairing requires complete source pins")
    pins = {row["path"]: _digest(row["sha256"], "paired source pin") for row in source}
    if len(pins) != len(source) or set(pins) != {f["source"] for f in record["files"]}:
        raise EvaluationError("Pairing requires one pin for every source")
    auxiliary = record.get("auxiliary_pins", [])
    for row in auxiliary:
        if row["path"] in pins:
            raise EvaluationError("Auxiliary pins cannot replace source pins")
        pins[row["path"]] = _digest(row["sha256"], "paired auxiliary pin")
    def portable(value, trail=()):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                attached = ((key == "template" and (trail[-1:] == ("params",)
                                or len(trail) >= 2 and trail[-2] == "effects"))
                            or key == "file" and trail[-2:] == ("params", "table"))
                result[key] = {"input_sha256": pins[item]} if attached and isinstance(item, str) and item in pins else portable(item, (*trail, key))
            return result
        if isinstance(value, list):
            return [portable(item, (*trail, i)) for i, item in enumerate(value)]
        return value
    declaration = record.get("reviewed_outcome")
    outcome = declaration["statement"] if declaration else record.get("approved_outcome") or record.get("recipe")
    if outcome is None:
        raise EvaluationError("Pairing requires a recorded recipe, approved contract, or reviewed common outcome")
    bindings = record.get("target_bindings")
    def bound_identity(value):
        if isinstance(value, dict):
            # The approved contract is already compared after attachment path
            # relocation. Its raw serialization hash is redundant and path-bound.
            return {k: bound_identity(v) for k, v in value.items() if k != "contract_sha256"}
        if isinstance(value, list):
            return [bound_identity(v) for v in value]
        return value
    bindings = bound_identity(bindings)
    if isinstance(bindings, dict) and bindings and all(path in pins for path in bindings):
        bindings = sorted([{"source_sha256": pins[path], "binding": portable(binding)}
                           for path, binding in bindings.items()], key=_hash)
    return {"request": _hash(request), "source_sha256": sorted(row["sha256"] for row in source),
            "auxiliary_sha256": sorted(row["sha256"] for row in auxiliary),
            "outcome_basis": "reviewed_declaration" if declaration else "contract" if record.get("approved_outcome") else "recipe",
            "outcome": _hash(portable({"outcome": outcome, "approved_words": record.get("approved_words", []),
                                       "assertions": record.get("assertions", [])})),
            "approved_targets": None if declaration else _hash(bindings)}


def _human(value):
    if not isinstance(value, dict):
        raise EvaluationError("human_review must be an object")
    result = {}
    for key in ("request_satisfied", "content_preserved", "visual_checked"):
        item = value.get(key)
        if item is not None and type(item) is not bool:
            raise EvaluationError(f"human_review.{key} must be true, false or null")
        result[key] = item
    for key in ("reviewer", "notes"):
        item = value.get(key, "")
        if not isinstance(item, str):
            raise EvaluationError(f"human_review.{key} must be text")
        result[key] = item
    return result


def _supported_final_outcomes(record):
    """Recorded readiness alone cannot fill missing required outcome checks."""
    contract = record.get("approved_outcome")
    if record["kind"] == "campaign":
        current = [c for c in record["children"] if c.get("lineage_role") == "current"]
        covered = {f["source"] for c in current for f in c["files"]}
        return (bool(current) and covered >= {f["source"] for f in record["files"]}
                and all(_supported_final_outcomes(c) for c in current))
    if contract and record["kind"] == "batch":
        return all(any(c["source"] == f["source"] and c["id"] == "GOAL" and c["status"] == "PASS"
                       for c in record["check_coverage"]) for f in record["files"])
    if contract and record["kind"] == "chain":
        return all(any(c.get("kind") == "approved_goal" and c.get("status") == "PASS"
                       for c in f.get("outcomes", [])) for f in record["files"])
    if record["kind"] == "recovery":
        needed = len(record.get("assertions", [])) + bool(contract)
        return all(len(f.get("outcomes", [])) >= needed and all(c.get("status") == "PASS" for c in f.get("outcomes", []))
                   for f in record["files"])
    return True


def evaluate(manifest_path):
    """Compare complete matched batches; reject overlapping lineage and missing pins."""
    manifest_path = Path(manifest_path).resolve()
    manifest, _ = _read(manifest_path)
    if manifest.get("version") != VERSION or not isinstance(manifest.get("cases"), list) or not manifest["cases"]:
        raise EvaluationError("Use a version 3 manifest with a nonempty cases list")
    rows, seen_ids, seen_artifacts, seen_sessions = [], set(), set(), set()
    for case in manifest["cases"]:
        ident = _text(case.get("id"), "case.id")
        if ident in seen_ids:
            raise EvaluationError("Case IDs must be unique")
        seen_ids.add(ident)
        revision = case.get("revision")
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise EvaluationError("Each case needs a full lowercase Git revision")
        variant, execution = case.get("variant"), case.get("execution")
        if variant not in {"baseline", "current"} or execution not in {"scripted", "live", "unknown"}:
            raise EvaluationError("Specify baseline/current and scripted/live/unknown execution")
        pair = _text(case.get("pair_id"), "pair_id")
        cohort = _text(case.get("cohort"), "cohort")
        path = _resolve(manifest_path.parent, case.get("report"))
        raw, digest = _read(path)
        if _digest(case.get("report_sha256"), "report_sha256") != digest:
            raise EvaluationError("Report changed since the manifest was recorded")
        record = adapt_report(path, evidence=case.get("evidence"))
        if raw.get("schema") != "spec-surgeon-workflow-evaluation":
            expected = case.get("artifact_sha256", {})
            actual = {a["path"]: a["sha256"] for a in record["lineage"] if a["path"] != str(path)}
            if expected != actual:
                raise EvaluationError("Pin every sidecar in artifact_sha256, or save an adapted snapshot before pairing")
        for artifact in record["lineage"]:
            marker = artifact["path"]
            session = artifact.get("session_id")
            if marker in seen_artifacts or session and session in seen_sessions:
                raise EvaluationError("Overlapping report lineage or repeated session would double count work")
            seen_artifacts.add(marker)
            if session:
                seen_sessions.add(session)
        identity = _identity(record)
        workflow = case.get("workflow")
        if not isinstance(workflow, dict) or set(workflow) != {"elapsed_seconds", "human_interventions", "additional_model_cost_usd"}:
            raise EvaluationError("workflow requires elapsed_seconds, human_interventions and additional_model_cost_usd; use null for unknowns")
        elapsed = _number(workflow["elapsed_seconds"], "elapsed_seconds")
        interventions = _number(workflow["human_interventions"], "human_interventions", integer=True)
        additional = _number(workflow["additional_model_cost_usd"], "additional_model_cost_usd")
        cost = _sum_known([record["estimated_model_cost_usd"], additional])
        human = _human(case.get("human_review", {}))
        reviewed = bool(human["reviewer"].strip()) and all(type(human[k]) is bool for k in ("request_satisfied", "content_preserved", "visual_checked"))
        ready = all(f["ready"] for f in record["files"])
        failed = any(c["status"] == "FAIL" and c.get("lineage_role") != "historical" for c in record["check_coverage"])
        supported = _supported_final_outcomes(record)
        accepted = (ready and not failed and supported and all(human[k] is True for k in ("request_satisfied", "content_preserved", "visual_checked"))) if reviewed else None
        rows.append({"id": ident, "pair_id": pair, "cohort": cohort, "variant": variant,
                     "execution": execution, "revision": revision, "identity": identity,
                     "record": record, "report_sha256": digest, "files": len(record["files"]),
                     "ready": sum(f["ready"] for f in record["files"]), "human_review": human,
                     "review_complete": reviewed, "human_accepted": accepted,
                     "required_outcome_evidence_present": supported,
                     "elapsed_seconds": elapsed, "human_interventions": interventions, "estimated_model_cost_usd": cost})
    grouped = {}
    for row in rows:
        group = grouped.setdefault(row["pair_id"], {})
        if row["variant"] in group:
            raise EvaluationError("Each pair requires exactly one baseline and current run")
        group[row["variant"]] = row
    pairs = []
    for ident, variants in grouped.items():
        if set(variants) != {"baseline", "current"}:
            raise EvaluationError("Every case must belong to a complete baseline/current pair")
        before, after = variants["baseline"], variants["current"]
        for key in ("identity", "cohort", "execution"):
            if before[key] != after[key]:
                raise EvaluationError(f"{ident}: paired {key} differs")
        delta = {key: after[key] - before[key] if before[key] is not None and after[key] is not None else None
                 for key in ("elapsed_seconds", "human_interventions", "estimated_model_cost_usd")}
        delta["human_accepted"] = int(after["human_accepted"]) - int(before["human_accepted"]) if before["human_accepted"] is not None and after["human_accepted"] is not None else None
        pairs.append({"pair_id": ident, "baseline": before["id"], "current": after["id"], "delta": delta,
                      "condition_changes": [key for key in ("kind", "renderer", "recipe", "target_bindings", "conditions")
                                            if before["record"].get(key) != after["record"].get(key)]})
    groups = []
    for key in sorted({(r["cohort"], r["execution"], r["variant"]) for r in rows}):
        selected = [r for r in rows if (r["cohort"], r["execution"], r["variant"]) == key]
        accepted = sum(r["human_accepted"] is True for r in selected)
        cost = _sum_known([r["estimated_model_cost_usd"] for r in selected])
        groups.append({"cohort": key[0], "execution": key[1], "variant": key[2], "batches": len(selected),
                       "files": sum(r["files"] for r in selected), "confirmed_accepted": accepted,
                       "reviewed": sum(r["review_complete"] for r in selected),
                       "acceptance_rate": accepted / len(selected) if all(r["review_complete"] for r in selected) else None,
                       "elapsed_seconds": _sum_known([r["elapsed_seconds"] for r in selected]),
                       "human_interventions": _sum_known([r["human_interventions"] for r in selected]),
                       "estimated_model_cost_usd": cost,
                       "live_cost_per_accepted_batch": cost / accepted if key[1] == "live" and accepted and cost is not None else None})
    return {"version": VERSION, "cases": rows, "pairs": pairs, "groups": groups,
            "limitations": ["No documents were opened or reverified during evaluation.",
                            "Revisions, execution provenance and human judgments are evaluator-supplied.",
                            "Automated readiness, human acceptance and live performance evidence are separate."]}


def _write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    adapt = sub.add_parser("adapt", help="Snapshot an existing report and its verification evidence")
    adapt.add_argument("report", type=Path)
    adapt.add_argument("--evidence", type=Path, help="Optional JSON with missing historical pins or reviewed common outcome")
    adapt.add_argument("--out", required=True, type=Path)
    evaluate_parser = sub.add_parser("compare", help="Evaluate pinned version 3 baseline/current pairs")
    evaluate_parser.add_argument("manifest", type=Path)
    evaluate_parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "adapt":
            evidence = _read(args.evidence)[0] if args.evidence else None
            result = adapt_report(args.report, evidence=evidence)
        else:
            result = evaluate(args.manifest)
        _write(args.out, result)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"Evaluation error: {exc}\n")
    print(str(args.out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
