"""User-reviewed facts and standards in the existing project context, never a second store.

These records describe evidence. They do not authorize edits, upgrade model assumptions,
or establish which standard an authority has adopted.
"""
from __future__ import annotations

import copy
import json
import re


VERSION = 1


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text.")
    return value


def _scope(value):
    if not isinstance(value, dict) or value.get("kind") not in {"project", "discipline", "section", "document"}:
        raise ValueError("scope must name project, discipline, section or document.")
    allowed = {"kind"} if value["kind"] == "project" else {"kind", "value"}
    if set(value) != allowed:
        raise ValueError("Project scope has only kind; other scopes require kind and value.")
    if "value" in value:
        _text(value["value"], "scope.value")


def _source(value):
    if not isinstance(value, dict) or set(value) - {"kind", "reference", "sha256"}:
        raise ValueError("source must record kind, reference and optionally sha256.")
    if value.get("kind") not in {"user", "document", "model"}:
        raise ValueError("source.kind must be user, document or model.")
    _text(value.get("reference"), "source.reference")
    if "sha256" in value and not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"])):
        raise ValueError("source.sha256 must be a lowercase SHA-256 digest.")


def validate_context(context):
    """Return a defensive copy; arbitrary legacy context remains supported."""
    if not isinstance(context, dict):
        raise ValueError("Project context must be a JSON object.")
    try:
        json.dumps(context, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Project context must contain finite JSON values.") from exc
    evidence = context.get("project_evidence")
    if evidence is None:
        return copy.deepcopy(context)
    if not isinstance(evidence, dict) or set(evidence) != {"version", "facts", "standards"} or evidence["version"] != VERSION:
        raise ValueError("project_evidence requires version 1, facts and standards.")
    for key in ("facts", "standards"):
        if not isinstance(evidence[key], list):
            raise ValueError(f"project_evidence.{key} must be a list.")
    facts = {}
    for fact in evidence["facts"]:
        if not isinstance(fact, dict) or set(fact) - {
                "id", "statement", "scope", "source", "status", "confirmed_by", "supersedes", "replaced_by"}:
            raise ValueError("Fact has unknown fields or is not an object.")
        ident = _text(fact.get("id"), "fact.id")
        if ident in facts:
            raise ValueError("Fact IDs must be unique.")
        facts[ident] = fact
        _text(fact.get("statement"), "fact.statement")
        _scope(fact.get("scope"))
        _source(fact.get("source"))
        if fact.get("status") not in {"confirmed", "assumed", "superseded"}:
            raise ValueError("Fact status must be confirmed, assumed or superseded.")
        if fact["status"] == "confirmed":
            _text(fact.get("confirmed_by"), "confirmed_by (the human reviewer)")
        if "confirmed_by" in fact:
            _text(fact["confirmed_by"], "confirmed_by")
        if fact["status"] == "assumed" and "confirmed_by" in fact:
            raise ValueError("Assumed facts cannot carry confirmation.")
        if fact["status"] == "superseded":
            _text(fact.get("replaced_by"), "replaced_by")
        elif "replaced_by" in fact:
            raise ValueError("Only a superseded fact may name replaced_by.")
    for ident, fact in facts.items():
        for field, inverse in (("supersedes", "replaced_by"), ("replaced_by", "supersedes")):
            if field in fact:
                other = facts.get(fact[field])
                if other is None or other.get(inverse) != ident or other["scope"] != fact["scope"]:
                    raise ValueError("Supersession must be reciprocal, present and within the same scope.")
        seen, current = set(), ident
        while current is not None:
            if current in seen:
                raise ValueError("Fact supersession cannot contain a cycle.")
            seen.add(current)
            current = facts[current].get("replaced_by")
    seen = set()
    for entry in evidence["standards"]:
        if not isinstance(entry, dict) or set(entry) != {
                "id", "identifier", "edition", "basis", "scope", "source", "confirmed_by"}:
            raise ValueError("A standards entry requires id, identifier, edition, basis, scope, source and confirmed_by.")
        for key in ("id", "identifier", "edition", "basis", "confirmed_by"):
            _text(entry[key], f"standard.{key}")
        if entry["id"] in seen:
            raise ValueError("Standard entry IDs must be unique.")
        seen.add(entry["id"])
        _scope(entry["scope"])
        _source(entry["source"])
        if entry["source"]["kind"] != "user":
            raise ValueError("Standards register entries must be explicitly user supplied.")
    return copy.deepcopy(context)


def _editable(context):
    result = validate_context(context)
    result.setdefault("project_evidence", {"version": VERSION, "facts": [], "standards": []})
    return result


def set_fact(context, fact):
    """Add a new fact. Historical facts are immutable; use supersede_fact to correct one."""
    result = _editable(context)
    if not isinstance(fact, dict):
        raise ValueError("Fact must be an object.")
    if any(row["id"] == fact.get("id") for row in result["project_evidence"]["facts"]):
        raise ValueError("That fact ID exists; supersede it with a new fact ID instead.")
    result["project_evidence"]["facts"].append(copy.deepcopy(fact))
    return validate_context(result)


def supersede_fact(context, old_id, replacement):
    """Preserve the old evidence and link an explicit reviewed replacement atomically."""
    result = _editable(context)
    old = next((f for f in result["project_evidence"]["facts"] if f["id"] == old_id), None)
    if old is None or old["status"] == "superseded":
        raise ValueError("Choose an existing active fact to supersede.")
    if not isinstance(replacement, dict) or replacement.get("status") != "confirmed":
        raise ValueError("A replacement must be explicitly human-confirmed.")
    replacement = copy.deepcopy(replacement)
    replacement["supersedes"] = old_id
    old.update(status="superseded", replaced_by=replacement.get("id"))
    result["project_evidence"]["facts"].append(replacement)
    return validate_context(result)


def set_standards_register(context, entries):
    result = _editable(context)
    result["project_evidence"]["standards"] = copy.deepcopy(entries)
    return validate_context(result)


def active_facts(context, *, scope=None, include_assumptions=False):
    context = validate_context(context)
    if scope is not None:
        _scope(scope)
    return [f for f in context.get("project_evidence", {}).get("facts", [])
            if (f["status"] == "confirmed" or include_assumptions and f["status"] == "assumed")
            and (f["scope"]["kind"] == "project" or scope == f["scope"])]


def approved_context_words(context, *, scopes=None):
    """Allow confirmed statements only in explicit scopes; metadata is never wording.

    Without a narrowed source scope only project-wide statements are eligible.
    Standards register choices support QA, not implicit permission to insert text.
    """
    result = validate_context(context)
    if scopes is not None and not isinstance(scopes, list):
        raise ValueError("scopes must be a list of explicit scope objects.")
    scopes = scopes or []
    for scope in scopes:
        _scope(scope)
    evidence = result.pop("project_evidence", None)
    if evidence is not None:
        statements = [fact["statement"] for fact in evidence["facts"] if fact["status"] == "confirmed"
                      and (fact["scope"]["kind"] == "project" or fact["scope"] in scopes)]
        if statements:
            result["confirmed_project_statements"] = statements
    return json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)


def _standard_key(value):
    return re.sub(r"\s+", " ", value.strip()).upper()


def audit_standards_register(observations, context, *, scope=None, scopes=None):
    """Compare observed editions to explicit choices; uncertainty stays unassessed.

    Observations are objects with identifier, optional edition, and optional location.
    Project entries apply everywhere. Scoped entries apply only with an exact scope;
    conflicting applicable entries remain ambiguous rather than guessing precedence.
    """
    context = validate_context(context)
    if scope is not None and scopes is not None:
        raise ValueError("Choose scope or scopes, not both.")
    if scope is not None:
        _scope(scope)
    if scopes is not None and not isinstance(scopes, list):
        raise ValueError("scopes must be a list of explicit scope objects.")
    applicable_scopes = scopes if scopes is not None else [scope] if scope is not None else []
    for item in applicable_scopes:
        _scope(item)
    entries = context.get("project_evidence", {}).get("standards", [])
    result = []
    for observed in observations:
        if not isinstance(observed, dict):
            raise ValueError("A standards observation must be an object.")
        identifier = _text(observed.get("identifier"), "observation.identifier")
        edition = observed.get("edition")
        if edition is not None:
            _text(edition, "observation.edition")
        matching = [entry for entry in entries if _standard_key(entry["identifier"]) == _standard_key(identifier)
                    and (entry["scope"]["kind"] == "project" or entry["scope"] in applicable_scopes)]
        editions = {row["edition"] for row in matching}
        reason = ("No applicable approved register entry." if not matching else
                  "Applicable register entries disagree." if len(editions) > 1 else
                  "The cited edition is not explicit." if edition is None else
                  "The cited edition matches the approved register." if edition in editions else
                  "The cited edition differs from the approved register.")
        status = ("unassessed" if not matching or len(editions) != 1 or edition is None else
                  "match" if edition in editions else "mismatch")
        result.append({"identifier": identifier, "edition": edition, "location": copy.deepcopy(observed.get("location")),
                       "status": status, "reason": reason, "register_entries": copy.deepcopy(matching)})
    return result
