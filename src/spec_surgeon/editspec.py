"""EditSpec — the JSON program the natural-language compiler will produce (docs/NL_SURGICAL_EDITOR_PLAN.md §3,
Appendix A). Phase 1 executes hand-written specs: validate → plan every op on the file (predict first) → merge
with conflict detection → apply in spec order on a working copy → the verifier reads the merged plan.

Rules enforced here (nothing runs when one is broken):
* ``questions`` non-empty or ``unsupported`` present → refuse (code ``questions`` / ``unsupported``);
* unknown keys anywhere at spec/op level, and unknown, ill-typed or out-of-enum ``select``/``params`` entries
  (checked against each op's declared ``select_schema``/``params_schema``) → refuse (a typo must not silently
  change what runs — e.g. ``"occurrences": "frist"`` would otherwise behave as ``"all"``);
* ``mode`` must equal the mode the ops imply (formatting unless an op writes characters);
* every op's machine-checkable ``expect`` entries must be reproduced by its plan on the file (code ``expect``) —
  supports exact integers as well as range predicates ``{"min": ..., "max": ...}`` for heterogeneous batches;
* ``review.geometry_reference`` must agree with the ops — a spec cannot drop the layout reference an op keeps,
  nor claim one an op gives up by design;
* spec-level ``assertions`` are post-conditions only (``effective_font``): they add V10 checks and can never
  exempt anything from V10's residual comparison;
* ``applies_to.pattern_ids`` (unless ``"or": "all"``): a file with another structure pattern is an exception.

The Phase 3 planner (selector resolution centralised, per-pattern compilation, the JSON schema validator and
the op catalog) grows out of this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from .ops import OPS, JobException, Plan, PlanFragment, get_op, merge_fragments
from .ops.selectors import DEFAULT_PRESERVE_FONTS
from .package import DocxPackage

SPEC_KEYS = {"request", "summary", "assumptions", "questions", "applies_to", "mode", "review", "ops",
             "unsupported", "proposal", "assertions"}
REQUIRED = ("request", "summary", "ops", "mode")
OP_KEYS = {"op", "select", "params", "expect", "tracked"}
MODES = ("formatting", "content", "untracked")
SPEC_ASSERTION_KINDS = ("effective_font",)
EXPECT_CHECKS = ("text_diffs", "text_diffs_outside_removed_parts", "text_diffs_in_kept_parts", "members_added",
                 "members_removed", "rev_delta")


class SpecError(JobException):
    """The spec itself is unusable (before any file is touched)."""


_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list, "object": dict,
          "null": type(None)}


def _schema_errors(value: Any, schema: Dict[str, Any], path: str) -> List[str]:
    """Errors of ``value`` against the small JSON-schema subset the ops declare: type (one or a list), enum,
    items (arrays), properties (objects: unknown keys refused, known ones checked recursively)."""
    errs: List[str] = []
    t = schema.get("type")
    if t is not None:
        allowed = t if isinstance(t, list) else [t]
        ok = False
        for name in allowed:
            py = _TYPES.get(name)
            if py is None:
                continue
            if name in ("integer", "number") and isinstance(value, bool):
                continue
            if isinstance(value, py):
                ok = True
        if not ok:
            return [f"{path} must be {' or '.join(allowed)}, got {value!r}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            errs += _schema_errors(v, schema["items"], f"{path}[{i}]")
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        for k, v in value.items():
            if k not in props:
                errs.append(f"{path}.{k} is not a known key (known: {sorted(props) or 'none'})")
            elif isinstance(props[k], dict):
                errs += _schema_errors(v, props[k], f"{path}.{k}")
    return errs


def _check_op_against_schemas(i: int, o: Dict[str, Any]) -> None:
    cls = OPS[o["op"]]
    label = f"op #{i} ({o['op']})"
    sel_schema = cls.select_schema or {}
    select = o.get("select") or {}
    if "properties" in sel_schema:
        errs = _schema_errors(select, {"type": "object", "properties": sel_schema["properties"]}, "select")
    else:
        errs = [f"select takes no keys ({sel_schema.get('description', 'none')}), got {sorted(select)}"] if select else []
    errs += _schema_errors(_op_params(o), {"type": "object", "properties": cls.params_schema.get("properties", {})},
                           "params")
    missing = [k for k in cls.params_schema.get("required", []) if k not in (o.get("params") or {})]
    errs += [f"params.{k} is required" for k in missing]
    if errs:
        raise SpecError(f"{label}: " + "; ".join(errs), "spec", {"op": o["op"], "errors": errs})


def _valid_range_pred(v: Any) -> bool:
    if not isinstance(v, dict) or not v or set(v) - {"min", "max"}:
        return False
    return all(isinstance(x, int) and not isinstance(x, bool) for x in v.values())


def _matches_expect_val(actual_val: int, exp_val: Any) -> bool:
    if isinstance(exp_val, int) and not isinstance(exp_val, bool):
        return actual_val == exp_val
    if isinstance(exp_val, dict):
        if "min" in exp_val and actual_val < exp_val["min"]:
            return False
        if "max" in exp_val and actual_val > exp_val["max"]:
            return False
        return True
    return False


def load_spec(source: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """A spec from a dict, inline JSON or a file. From a file, relative ``template`` paths in op params are
    resolved against the spec file's folder (so a spec and its template travel together)."""
    if isinstance(source, dict):
        return source
    text = str(source)
    if text.lstrip().startswith("{"):
        return json.loads(text)
    path = Path(text)
    spec = json.loads(path.read_text(encoding="utf-8"))
    for o in spec.get("ops", []) if isinstance(spec, dict) else []:
        params = o.get("params") if isinstance(o, dict) else None
        if isinstance(params, dict) and isinstance(params.get("template"), str) and not Path(params["template"]).is_absolute():
            params["template"] = str((path.parent / params["template"]).resolve())
    return spec


def spec_templates(spec: Dict[str, Any]) -> List[Path]:
    """Template files an EditSpec reads (never edited, never in the batch)."""
    return [Path(o["params"]["template"]).resolve() for o in spec.get("ops", [])
            if isinstance(o.get("params"), dict) and isinstance(o["params"].get("template"), str)]


def is_template_file(path: Path, templates: List[Path]) -> bool:
    """The file is one of the spec's templates: the same path, or byte-identical (a copy of it)."""
    import hashlib
    p = Path(path).resolve()
    if p in templates:
        return True
    try:
        size = p.stat().st_size
        digest = None
        for t in templates:
            if t.is_file() and t.stat().st_size == size:
                digest = digest or hashlib.md5(p.read_bytes()).hexdigest()
                if hashlib.md5(t.read_bytes()).hexdigest() == digest:
                    return True
    except OSError:
        return False
    return False


def validate_spec(spec: Dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise SpecError("an EditSpec is a JSON object", "spec")
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise SpecError(f"unknown EditSpec key(s) {sorted(unknown)}; known: {sorted(SPEC_KEYS)}", "spec")
    if spec.get("unsupported"):
        raise SpecError(f"the spec says the request is unsupported: {spec['unsupported']}"
                        + (f" — proposal: {spec['proposal']}" if spec.get("proposal") else ""), "unsupported")
    if spec.get("questions"):
        raise SpecError("the spec has open questions; nothing runs until they are answered: "
                        + " | ".join(spec["questions"]), "questions", {"questions": spec["questions"]})
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise SpecError(f"EditSpec missing required key(s) {missing}", "spec")
    if spec["mode"] not in MODES:
        raise SpecError(f"mode must be one of {MODES}", "spec")
    ops = spec["ops"]
    if not isinstance(ops, list) or not ops:
        raise SpecError("ops must be a non-empty list", "spec")
    for i, o in enumerate(ops, 1):
        if not isinstance(o, dict) or "op" not in o:
            raise SpecError(f"op #{i} must be an object with an 'op' name", "spec")
        bad = set(o) - OP_KEYS
        if bad:
            raise SpecError(f"op #{i} ({o['op']}): unknown key(s) {sorted(bad)}; known: {sorted(OP_KEYS)}", "spec")
        if o["op"] not in OPS:
            raise SpecError(f"op #{i}: unknown op {o['op']!r}; known: {sorted(OPS)}", "spec")
        for k in ("select", "params", "expect"):
            if k in o and not isinstance(o[k], dict):
                raise SpecError(f"op #{i} ({o['op']}): '{k}' must be an object", "spec")
        if "tracked" in o and not isinstance(o["tracked"], bool):
            raise SpecError(f"op #{i} ({o['op']}): 'tracked' must be true or false", "spec")
        if "tracked" in o and "tracked" in o.get("params", {}) and o["params"]["tracked"] != o["tracked"]:
            raise SpecError(f"op #{i} ({o['op']}): 'tracked' and params.tracked disagree", "spec")
        for k, v in (o.get("expect") or {}).items():
            if k not in EXPECT_CHECKS and not isinstance(v, str):
                raise SpecError(f"op #{i} ({o['op']}): expect.{k} is not a known check ({EXPECT_CHECKS}); only "
                                f"prose (strings) may use other keys", "spec")
            if k in EXPECT_CHECKS and k != "rev_delta" and not isinstance(v, str):
                if not (isinstance(v, int) and not isinstance(v, bool)) and not _valid_range_pred(v):
                    raise SpecError(f"op #{i} ({o['op']}): expect.{k} must be an integer, a range "
                                    f"{{'min': ..., 'max': ...}}, or a prose string, got {v!r}", "spec")
            if k == "rev_delta" and not isinstance(v, str):
                if not isinstance(v, dict) or not all(
                    (isinstance(rv, int) and not isinstance(rv, bool)) or _valid_range_pred(rv) for rv in v.values()
                ):
                    raise SpecError(f"op #{i} ({o['op']}): expect.rev_delta must map revision kinds to integers or "
                                    f"range objects, got {v!r}", "spec")
        _check_op_against_schemas(i, o)
    review = spec.get("review") or {}
    if set(review) - {"sample_files", "geometry_reference"}:
        raise SpecError(f"unknown review key(s) {sorted(set(review) - {'sample_files', 'geometry_reference'})}", "spec")
    if review.get("geometry_reference") not in (None, "source", "template", "none"):
        raise SpecError("review.geometry_reference must be source | template | none", "spec")
    applies = spec.get("applies_to") or {}
    if set(applies) - {"pattern_ids", "or"}:
        raise SpecError("applies_to takes pattern_ids and/or \"or\": \"all\"", "spec")
    for a in spec.get("assertions") or []:
        if not isinstance(a, dict) or a.get("kind") not in SPEC_ASSERTION_KINDS:
            raise SpecError(f"spec-level assertions are post-conditions only ({SPEC_ASSERTION_KINDS}); got {a!r}", "spec")


def _op_params(o: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(o.get("params") or {})
    if "tracked" in o:
        params["tracked"] = o["tracked"]
    return params


def _check_expect(i: int, o: Dict[str, Any], frag: PlanFragment, plan_notes: List[str]) -> None:
    exp = o.get("expect") or {}
    label = f"op #{i} ({o['op']})"
    removed, added = set(frag.members_removed), set(frag.members_added)
    actual = {
        "text_diffs": len(frag.diffs),
        "text_diffs_outside_removed_parts": sum(1 for d in frag.diffs if d.part not in removed),
        "text_diffs_in_kept_parts": sum(1 for d in frag.diffs if d.part not in removed and d.part not in added),
        "members_added": len(frag.members_added),
        "members_removed": len(frag.members_removed),
    }
    for k, v in exp.items():
        if isinstance(v, str):
            plan_notes.append(f"{label} expects ({k}): {v}")
            continue
        if k == "rev_delta":
            got = {kk: frag.rev_delta.get(kk, 0) for kk in v}
            if not all(_matches_expect_val(got[kk], v[kk]) for kk in v):
                raise JobException(f"{label}: expected revision delta {v}, the plan on this file gives {got}", "expect",
                                   {"op": o["op"], "expected": v, "planned": got})
            continue
        if not _matches_expect_val(actual[k], v):
            raise JobException(f"{label}: expected {k} = {v}, the plan on this file gives {actual[k]}", "expect",
                               {"op": o["op"], "check": k, "expected": v, "planned": actual[k]})


def plan_spec(pkg: DocxPackage, spec: Dict[str, Any], pattern_id: Optional[str] = None) -> Tuple[Plan, List[PlanFragment]]:
    """Predict-first for a whole spec on one file. Raises JobException (the file's exception record) when the
    spec cannot be planned deterministically on it."""
    validate_spec(spec)
    applies = spec.get("applies_to") or {}
    if applies.get("pattern_ids") and applies.get("or") != "all":
        if pattern_id is None:
            from .fingerprint import fingerprint
            pattern_id = fingerprint(pkg)["pattern_id"]
        if pattern_id not in applies["pattern_ids"]:
            raise JobException(f"this file's structure pattern {pattern_id} is not one the spec was compiled for "
                               f"{applies['pattern_ids']}", "pattern", {"pattern_id": pattern_id})
    frags: List[PlanFragment] = []
    expect_notes: List[str] = []
    for i, o in enumerate(spec["ops"], 1):
        try:
            frag = get_op(o["op"]).plan(pkg, o.get("select") or {}, _op_params(o))
        except JobException as e:
            raise JobException(f"op #{i} ({o['op']}): {e.reason}", e.code, {**e.details, "op": o["op"], "op_number": i})
        _check_expect(i, o, frag, expect_notes)
        frags.append(frag)
    plan = merge_fragments(frags, job="edit-spec", params={"request": spec["request"], "summary": spec["summary"]})
    if plan.mode != spec["mode"]:
        raise JobException(f"the spec says mode {spec['mode']!r} but its ops make it {plan.mode!r} "
                           f"({'they write characters' if plan.mode != 'formatting' else 'no op writes characters'})",
                           "spec-mode", {"declared": spec["mode"], "implied": plan.mode})
    changes = bool(plan.intended_parts or plan.members_removed or plan.members_added)
    want = (spec.get("review") or {}).get("geometry_reference")
    if want and changes and want != plan.geometry_reference:
        why = plan.geometry_reason or "the ops keep the source render as the layout reference"
        raise JobException(f"review.geometry_reference is {want!r} but the ops imply {plan.geometry_reference!r} "
                           f"({why}); a spec can neither drop a layout reference an op keeps nor claim one an op "
                           f"gives up", "geometry", {"declared": want, "implied": plan.geometry_reference})
    for a in spec.get("assertions") or []:
        a = dict(a)
        if a["kind"] == "effective_font":
            a.setdefault("preserve_fonts", DEFAULT_PRESERVE_FONTS)
            a.setdefault("slots", ["ascii", "hAnsi", "cs"])
            a.setdefault("include_paragraph_marks", True)
            a.setdefault("scope", ["document"])
        plan.property_assertions.append({**a, "op": "spec"})
    plan.notes = [f"assumption: {x}" for x in spec.get("assumptions", [])] + expect_notes + plan.notes
    return plan, frags


def apply_spec(pkg: DocxPackage, fragments: List[PlanFragment]) -> DocxPackage:
    """Apply the planned fragments in spec order on a working copy (each op reads the state the previous left)."""
    work = pkg.copy()
    for f in fragments:
        get_op(f.op).apply(work, f)
    return work
