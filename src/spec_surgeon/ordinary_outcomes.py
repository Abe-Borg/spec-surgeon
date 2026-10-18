"""Reviewed request authority for the ordinary natural-language editing flow.

The outcome planner receives the request and original documents, never the editing
recipe. Its proposal is evidence for human review, not permission to execute.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _seal(review):
    review["digest"] = _digest({k: v for k, v in review.items() if k != "digest"})
    return review


def user_request(conversation):
    """Only the original request and actual user turns; no model recipe or rationale."""
    replies = [entry.text for entry in conversation.entries[1:] if entry.role == "user"]
    return conversation.request + ("\n\nUSER DECISIONS:\n" + "\n".join(replies) if replies else "")


def build_review(request, files, contract, *, rationale="", risks=(), evidence=(), template=None):
    from .goals import goal_templates, resolve_goal, validate_contract
    validate_contract(contract)
    attached = Path(template).resolve() if template else None
    for path in goal_templates(contract):
        if attached is None or path.resolve() != attached:
            raise ValueError("Outcome templates must be explicitly attached to this request.")
    review = {"version": 1, "request": request, "contract": copy.deepcopy(contract),
              "rationale": rationale, "risks": list(risks), "evidence": list(evidence),
              "template": str(attached) if attached else None,
              "template_sha256": hashlib.sha256(attached.read_bytes()).hexdigest() if attached else None,
              "documents": [], "status": "PROPOSED"}
    for source in files:
        path = Path(source).resolve()
        try:
            resolution = resolve_goal(path, contract)
        except Exception as exc:
            try:
                source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                source_hash = None
            resolution = {"status": "needs_input", "reason": f"{type(exc).__name__}: {exc}", "candidates": [],
                          "source_sha256": source_hash, "binding": None}
        review["documents"].append({"source": str(path), "source_sha256": resolution["source_sha256"],
                                   "resolution": resolution, "binding": resolution.get("binding")})
        if resolution.get("binding") is None:
            review["status"] = "NEEDS_INPUT"
    if not review["documents"]:
        raise ValueError("Outcome review needs at least one original document.")
    return _seal(review)


def propose(request, files, *, template=None, client=None, options=None, folder=None, log=None):
    from . import campaign_planner
    from .inventory import inspect_source
    from .recovery import RecoveryOptions
    options = options or RecoveryOptions(render=False, renderer="none")
    documents = [inspect_source(Path(source)) for source in files]
    project = {"id": "ordinary", "label": "Requested outcomes", "context": {},
               "goal": {"template": str(Path(template).resolve()) if template else None}}
    if folder:
        project["planning_artifact_dir"] = str(Path(folder) / "outcome-planning")
    draft, state = campaign_planner.propose(project, documents, request, options, client=client, log=log)
    if folder:
        root = Path(folder)
        root.mkdir(parents=True, exist_ok=True)
        (root / "outcome-planning.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    if draft.get("status") != "PROPOSED":
        return _seal({"version": 1, "request": request, "status": draft.get("status", "PAUSED"),
                      "contract": None, "documents": [], "rationale": draft.get("rationale", ""),
                      "questions": draft.get("questions", [])})
    return build_review(request, files, draft["contract"], rationale=draft["rationale"],
                        risks=draft.get("risks", []), evidence=draft.get("evidence", []), template=template)


def validate(review, files, *, request=None, approval=None):
    """Re-read source evidence before approval and again before execution."""
    from .goals import goal_templates, validate_binding
    if not isinstance(review, dict) or review.get("digest") != _digest({k: v for k, v in review.items() if k != "digest"}):
        raise ValueError("The outcome review changed; prepare and review it again.")
    if review.get("status") != "PROPOSED" or not review.get("contract"):
        raise ValueError("Request outcomes are not independently covered yet; clarify the request or use an explicit saved recipe.")
    if approval is not None and approval != review["digest"]:
        raise ValueError("Approve the exact outcomes and per-file targets shown in the current review.")
    if request is not None and request != review["request"]:
        raise ValueError("The request changed since outcome review; prepare it again.")
    paths = [str(Path(path).resolve()) for path in files]
    rows = review["documents"]
    if len(paths) != len(set(paths)) or len(rows) != len(paths) or {row["source"] for row in rows} != set(paths):
        raise ValueError("The document scope changed since outcome review.")
    template = review.get("template")
    if template and hashlib.sha256(Path(template).read_bytes()).hexdigest() != review.get("template_sha256"):
        raise ValueError(f"The attached template changed since outcome review: {template}")
    for path in goal_templates(review["contract"]):
        if template is None or path.resolve() != Path(template).resolve():
            raise ValueError("The outcome contract names an unattached template.")
    bindings = {}
    for row in rows:
        path = Path(row["source"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["source_sha256"]:
            raise ValueError(f"An original document changed since outcome review: {path}")
        bindings[str(path)] = validate_binding(path, review["contract"], row["binding"])
    return copy.deepcopy(review["contract"]), bindings


def choose_targets(review, source, candidate_ids, *, effect_id=None):
    from .goals import choose_goal, validate_binding
    result = copy.deepcopy(review)
    path = str(Path(source).resolve())
    row = next((row for row in result.get("documents", []) if row["source"] == path), None)
    if row is None:
        raise ValueError("Choose targets in a document from this review.")
    row["binding"] = choose_goal(Path(path), result["contract"], candidate_ids,
                                 effect_id=effect_id, binding=row.get("binding"))
    try:
        for item in result["documents"]:
            validate_binding(Path(item["source"]), result["contract"], item.get("binding"))
        result["status"] = "PROPOSED"
    except ValueError:
        result["status"] = "NEEDS_INPUT"
    return _seal(result)


def save(review, path):
    Path(path).write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")


def text(review):
    if not review:
        return "Recipe verification only; request satisfaction is not independently checked."
    lines = ["Review requested outcomes and every document's targets before approving.",
             "Paragraph numbers start at 1 within each original document part.",
             "Status: " + review["status"], "Request: " + review["request"],
             review.get("rationale", ""), "Outcome review: " + review["digest"]]
    if review.get("contract"):
        lines += ["Proposed wording and scope must match your request:",
                  json.dumps(review["contract"], ensure_ascii=False, indent=2)]
    for row in review.get("documents", []):
        lines.append("\nDocument: " + row["source"])
        resolution = row["resolution"]
        if "effects" not in resolution:
            lines.append(resolution["reason"])
            lines += [f"    {target['part']} paragraph {target['paragraph_index'] + 1}: {target['text']!r}"
                      for target in resolution.get("candidates", [])]
        for eid, effect in resolution.get("effects", {}).items():
            bound = (row.get("binding") or {}).get("effects", {}).get(eid, {})
            targets = bound.get("targets", effect.get("candidates", []))
            lines.append(f"  {eid}: {effect['reason']}; count " + str(bound.get("source_count", len(targets))))
            lines += [f"    {target['part']} paragraph {target['paragraph_index'] + 1}: {target['text']!r}" for target in targets]
    for question in review.get("questions", []):
        lines.append("Question: " + str(question))
    lines += ["Risk: " + risk for risk in review.get("risks", [])]
    return "\n".join(line for line in lines if line)
