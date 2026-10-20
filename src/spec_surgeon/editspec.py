"""EditSpec — the JSON program the natural-language compiler will produce (docs/history/NL_SURGICAL_EDITOR_PLAN.md §3,
Appendix A). Phase 1 executes hand-written specs: validate → plan every op on the file (predict first) → merge
with conflict detection → apply in spec order on a working copy → the verifier reads the merged plan.

Rules enforced here (nothing runs when one is broken):
* every spec-level field has the shape the published schema gives it (``request``/``summary`` text, ``assumptions``/
  ``questions`` lists of text, ``review``/``applies_to`` objects with a positive ``sample_files`` count, ``pattern_ids`` a list of
  text and ``or`` only ``"all"``, ``assertions`` a list, an op's ``op`` a name) → else refuse (code ``spec``), never a
  TypeError or a misreading;
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

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .ooxml import CELL_REV_TAGS, CONTENT_REV_TAGS, PROPERTY_REV_TAGS
from .ops import OPS, JobException, Plan, PlanFragment, get_op, merge_fragments
from .ops.base import text_diffs
from .ops.selectors import DEFAULT_PRESERVE_FONTS, selection_reads, size_values
from .package import DocxPackage

SPEC_KEYS = {"request", "summary", "assumptions", "questions", "applies_to", "mode", "review", "ops",
             "unsupported", "proposal", "assertions", "bindings"}
REQUIRED = ("request", "summary", "ops", "mode")
OP_KEYS = {"op", "select", "params", "expect", "tracked"}
MODES = ("formatting", "content", "untracked")
SPEC_ASSERTION_KINDS = ("effective_font",)
EXPECT_CHECKS = ("text_diffs", "text_diffs_outside_removed_parts", "text_diffs_in_kept_parts", "members_added",
                 "members_removed", "rev_delta")
# The revision kinds V4 counts (CLAUDE.md "Revision kinds"), derived from the transform side's tables so a kind added
# there is accepted here: content wrappers, their paragraph-mark forms, property changes, table-cell revisions.
REV_DELTA_KINDS = tuple(CONTENT_REV_TAGS) + tuple(t + "_mark" for t in CONTENT_REV_TAGS) + tuple(PROPERTY_REV_TAGS) \
    + tuple(CELL_REV_TAGS)


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
    """``{"min": n}``, ``{"max": n}`` or both: non-negative integers (never booleans), min ≤ max."""
    if not isinstance(v, dict) or not v or set(v) - {"min", "max"}:
        return False
    if not all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in v.values()):
        return False
    return not ("min" in v and "max" in v and v["min"] > v["max"])


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


# Files an op reads beside the documents, besides any op's ``template`` (built-in batch 1): (op, keys into params).
# They are inputs like the source (CLAUDE.md rule 12): pinned by md5 in the plan, hashed by the approval token,
# resolved against a spec file's folder, stored absolute in a recipe.
INPUT_FILE_PARAMS: Dict[str, List[Tuple[str, ...]]] = {
    "fill_variables": [("table", "file")],
    "replace_table": [("pairs_table", "file")],
    "swap_image": [("image",), ("match", "image")],
}


def input_slots(o: Any) -> List[Tuple[Dict[str, Any], str]]:
    """(the dict, its key) of every input-file path one op's params hold: its ``template``, and the
    ``INPUT_FILE_PARAMS`` of its kind."""
    params = o.get("params") if isinstance(o, dict) else None
    if not isinstance(params, dict):
        return []
    out: List[Tuple[Dict[str, Any], str]] = []
    if isinstance(params.get("template"), str):
        out.append((params, "template"))
    for keys in INPUT_FILE_PARAMS.get(o.get("op"), []):
        d: Any = params
        for k in keys[:-1]:
            d = d.get(k) if isinstance(d, dict) else None
        if isinstance(d, dict) and isinstance(d.get(keys[-1]), str) and d[keys[-1]].strip():
            out.append((d, keys[-1]))
    return out


def load_spec(source: Union[str, Path, Dict[str, Any]]) -> Dict[str, Any]:
    """A spec from a dict, inline JSON or a file. From a file, relative input-file paths in op params (a ``template``,
    a table, a picture: ``input_slots``) are resolved against the spec file's folder (so a spec and its files travel
    together)."""
    if isinstance(source, dict):
        return source
    text = str(source)
    if text.lstrip().startswith("{"):
        return json.loads(text)
    path = Path(text)
    spec = json.loads(path.read_text(encoding="utf-8"))
    for o in spec.get("ops", []) if isinstance(spec, dict) else []:
        for d, k in input_slots(o):
            if not Path(d[k]).is_absolute():
                d[k] = str((path.parent / d[k]).resolve())
    for binding in spec.get("bindings", []) if isinstance(spec, dict) else []:
        if isinstance(binding, dict) and isinstance(binding.get("file"), str):
            if not Path(binding["file"]).is_absolute():
                binding["file"] = str((path.parent / binding["file"]).resolve())
    return spec


def spec_templates(spec: Dict[str, Any]) -> List[Path]:
    """Template files an EditSpec reads (never edited, never in the batch)."""
    return [Path(o["params"]["template"]).resolve() for o in spec.get("ops", [])
            if isinstance(o.get("params"), dict) and isinstance(o["params"].get("template"), str)]


def spec_input_files(spec: Dict[str, Any]) -> List[Path]:
    """Every file an EditSpec reads beside the documents — templates, tables, pictures (``input_slots``) — in spec
    order, each once. What the approval token hashes (rule 12)."""
    out: List[Path] = []
    for o in (spec or {}).get("ops", []) if isinstance(spec, dict) else []:
        for d, k in input_slots(o):
            p = Path(d[k]).resolve()
            if p not in out:
                out.append(p)
    return out


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


def _is_str_list(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def _check_shapes(spec: Dict[str, Any]) -> None:
    """The spec-level fields have the shapes the published schema gives them (``catalog.editspec_json_schema``),
    before anything reads them: a wrong shape is a ``spec`` refusal, never a TypeError (``"review": [...]`` would make
    ``set(review)`` raise, ``"assertions": true`` would not iterate) and never a silent misreading (a string
    ``pattern_ids`` would match by substring, a string ``questions`` or ``assumptions`` would split into characters)."""
    for k in ("request", "summary"):
        if k in spec and not isinstance(spec[k], str):
            raise SpecError(f"'{k}' must be text, got {spec[k]!r}", "spec")
    for k in ("unsupported", "proposal"):
        if spec.get(k) is not None and not isinstance(spec[k], str):
            raise SpecError(f"'{k}' must be text, got {spec[k]!r}", "spec")
    for k in ("assumptions", "questions"):
        if spec.get(k) is not None and not _is_str_list(spec[k]):
            raise SpecError(f"'{k}' must be a list of strings, got {spec[k]!r}", "spec")
    for k in ("review", "applies_to"):
        if spec.get(k) is not None and not isinstance(spec[k], dict):
            raise SpecError(f"'{k}' must be an object, got {spec[k]!r}", "spec")
    if spec.get("assertions") is not None and not isinstance(spec["assertions"], list):
        raise SpecError(f"'assertions' must be a list, got {spec['assertions']!r}", "spec")


def _validate_bindings(bindings: Any) -> None:
    if not isinstance(bindings, list):
        raise SpecError("bindings must be a list of source-file bindings", "spec")
    files = set()
    for entry in bindings:
        if not isinstance(entry, dict) or set(entry) != {"file", "md5", "slots"}:
            raise SpecError("each binding needs exactly file, md5 and slots", "spec")
        file, digest, slots = entry["file"], entry["md5"], entry["slots"]
        if not isinstance(file, str) or not file.strip() or not Path(file).is_absolute():
            raise SpecError("binding.file must be an absolute source path", "spec")
        identity = str(Path(file).resolve())
        if identity in files:
            raise SpecError(f"duplicate binding for {file!r}", "spec")
        files.add(identity)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{32}", digest):
            raise SpecError("binding.md5 must be the source file's lowercase MD5", "spec")
        if not isinstance(slots, dict) or not slots:
            raise SpecError("binding.slots must be a non-empty slot-name mapping", "spec")
        for name, slot in slots.items():
            if not isinstance(name, str) or not name or not isinstance(slot, dict) or set(slot) != {"value", "part", "paragraph"}:
                raise SpecError("each bound slot needs exactly value, part and paragraph", "spec")
            if not isinstance(slot["value"], str) or not slot["value"] or not isinstance(slot["part"], str) or not slot["part"]:
                raise SpecError("bound slot value and part must be non-empty text", "spec")
            if type(slot["paragraph"]) is not int or slot["paragraph"] < 0:
                raise SpecError("bound slot paragraph must be a non-negative integer", "spec")


def _file_bindings(pkg: DocxPackage, spec: Dict[str, Any]) -> Dict[str, Any]:
    path = str(Path(pkg.name).resolve())
    entry = next((b for b in spec.get("bindings", []) if str(Path(b["file"]).resolve()) == path), None)
    if entry is None:
        return {}
    if hashlib.md5(pkg.data).hexdigest() != entry["md5"]:
        raise JobException(f"bound source changed since inspection: {entry['file']}", "binding-source")
    return {name: {"rule": "binding", **slot, "source_md5": entry["md5"]} for name, slot in entry["slots"].items()}


def validate_spec(spec: Dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise SpecError("an EditSpec is a JSON object", "spec")
    unknown = set(spec) - SPEC_KEYS
    if unknown:
        raise SpecError(f"unknown EditSpec key(s) {sorted(unknown)}; known: {sorted(SPEC_KEYS)}", "spec")
    _check_shapes(spec)
    _validate_bindings(spec.get("bindings", []))
    if spec.get("unsupported"):
        raise SpecError(f"the spec says the request is unsupported: {spec['unsupported']}"
                        + (f" — proposal: {spec['proposal']}" if spec.get("proposal") else ""), "unsupported")
    if spec.get("questions"):
        raise SpecError("the spec has open questions; nothing runs until they are answered: "
                        + " | ".join(spec["questions"]), "questions", {"questions": spec["questions"]})
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise SpecError(f"EditSpec missing required key(s) {missing}", "spec")
    if not isinstance(spec["mode"], str) or spec["mode"] not in MODES:
        raise SpecError(f"mode must be one of {MODES}", "spec")
    ops = spec["ops"]
    if not isinstance(ops, list) or not ops:
        raise SpecError("ops must be a non-empty list", "spec")
    if spec.get("bindings") and not any(isinstance(o, dict) and o.get("op") == "apply_header_footer_template" for o in ops):
        raise SpecError("template slot bindings require apply_header_footer_template", "spec")
    for i, o in enumerate(ops, 1):
        if not isinstance(o, dict) or not isinstance(o.get("op"), str):
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
                                    f"{{'min': ..., 'max': ...}} (non-negative, min ≤ max), or a prose string, got {v!r}", "spec")
            if k == "rev_delta" and not isinstance(v, str):
                if not isinstance(v, dict) or not all(
                    (isinstance(rv, int) and not isinstance(rv, bool)) or _valid_range_pred(rv) for rv in v.values()
                ):
                    raise SpecError(f"op #{i} ({o['op']}): expect.rev_delta must map revision kinds to integers or "
                                    f"range objects {{'min': ..., 'max': ...}} (non-negative, min ≤ max), got {v!r}", "spec")
                unknown_kinds = sorted(set(v) - set(REV_DELTA_KINDS))
                if unknown_kinds:
                    raise SpecError(f"op #{i} ({o['op']}): expect.rev_delta names unknown revision kind(s) "
                                    f"{unknown_kinds}; known: {list(REV_DELTA_KINDS)}", "spec")
        _check_op_against_schemas(i, o)
    review = spec.get("review") or {}
    if set(review) - {"sample_files", "geometry_reference"}:
        raise SpecError(f"unknown review key(s) {sorted(set(review) - {'sample_files', 'geometry_reference'})}", "spec")
    if review.get("geometry_reference") not in (None, "source", "template", "none"):
        raise SpecError("review.geometry_reference must be source | template | none", "spec")
    n = review.get("sample_files")
    if n is not None and (type(n) is not int or n < 1):
        raise SpecError(f"review.sample_files must be a whole number of at least 1, got {n!r}", "spec")
    applies = spec.get("applies_to") or {}
    if set(applies) - {"pattern_ids", "or"}:
        raise SpecError("applies_to takes pattern_ids and/or \"or\": \"all\"", "spec")
    if applies.get("pattern_ids") is not None and not _is_str_list(applies["pattern_ids"]):
        raise SpecError(f"applies_to.pattern_ids must be a list of pattern ids, got {applies['pattern_ids']!r}", "spec")
    if applies.get("or") is not None and applies["or"] != "all":
        raise SpecError(f"applies_to.or can only be \"all\", got {applies['or']!r}", "spec")
    for a in spec.get("assertions") or []:
        if not isinstance(a, dict) or a.get("kind") not in SPEC_ASSERTION_KINDS:
            raise SpecError(f"spec-level assertions are post-conditions only ({SPEC_ASSERTION_KINDS}); got {a!r}", "spec")
        if a["kind"] == "effective_font":
            for flag in ("east_asia", "numbering_levels"):
                if flag in a and not isinstance(a[flag], bool):
                    raise SpecError(f"effective_font '{flag}' must be a boolean", "spec")
            if "slots" in a and (not isinstance(a["slots"], list) or not a["slots"] or
                                 any(s not in ("ascii", "hAnsi", "cs", "eastAsia") for s in a["slots"])):
                raise SpecError("effective_font 'slots' must name ascii, hAnsi, cs or eastAsia", "spec")
            if a.get("value") is None and a.get("size") is None:
                raise SpecError("an effective_font assertion needs 'value' (a font name) and/or 'size' (points)", "spec")
            if a.get("value") is not None and (not isinstance(a["value"], str) or not a["value"].strip()):
                raise SpecError("effective_font 'value' must be a font name", "spec")
            try:
                size_values({"size": a.get("size"), "size_from": a.get("size_from")})
            except JobException as e:
                raise SpecError(f"effective_font: {e.reason}", "spec")


def _op_params(o: Dict[str, Any]) -> Dict[str, Any]:
    params = dict(o.get("params") or {})
    if "tracked" in o:
        params["tracked"] = o["tracked"]
    return params


op_params = _op_params                        # what an op's plan() receives (``tracked`` folded in) — batch.planned_names

NAMING_OPS = ("name_output", "make_values_agree")


def naming_op(spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The spec's op that can name the output files: name_output, or make_values_agree with the file name among its
    places and not its truth (only then can it rename)."""
    for o in (spec or {}).get("ops") or []:
        if not isinstance(o, dict):
            continue
        if o.get("op") == "name_output":
            return o
        if o.get("op") == "make_values_agree":
            params = o.get("params") or {}
            places = params.get("places") if isinstance(params.get("places"), dict) else {}
            if any(isinstance(v, dict) and v.get("in") == "filename" and k != params.get("truth")
                   for k, v in places.items()):
                return o
    return None


def spec_renames_outputs(spec: Dict[str, Any]) -> bool:
    """True when the spec may give an output another name than its source — then it delivers copies only."""
    return naming_op(spec) is not None


def _check_expect(i: int, o: Dict[str, Any], frag: PlanFragment, plan_notes: List[str]) -> None:
    exp = o.get("expect") or {}
    label = f"op #{i} ({o['op']})"
    removed, added = set(frag.members_removed), set(frag.members_added)
    diffs = text_diffs(frag.diffs)                  # structure-only declarations are not text diffs
    actual = {
        "text_diffs": len(diffs),
        "text_diffs_outside_removed_parts": sum(1 for d in diffs if d.part not in removed),
        "text_diffs_in_kept_parts": sum(1 for d in diffs if d.part not in removed and d.part not in added),
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


def plan_spec(pkg: DocxPackage, spec: Dict[str, Any], pattern_id: Optional[str] = None,
              trace: Optional[Callable[..., None]] = None) -> Tuple[Plan, List[PlanFragment]]:
    """Predict-first for a whole spec on one file. Raises JobException (the file's exception record) when the
    spec cannot be planned deterministically on it. ``trace(phase, number, op, state, fragment=None)`` is told when
    each op starts and finishes (the file's diagnostic trace, ``diagnostics.Tracer.op_hook``)."""
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
    bound_slots = _file_bindings(pkg, spec)
    for i, o in enumerate(spec["ops"], 1):
        if trace:
            trace("plan", i, o["op"], "start")
        try:
            params = _op_params(o)
            if o["op"] == "apply_header_footer_template" and bound_slots:
                params = {**params, "slot_bindings": bound_slots}
            frag = get_op(o["op"]).plan(pkg, o.get("select") or {}, params)
        except JobException as e:
            raise JobException(f"op #{i} ({o['op']}): {e.reason}", e.code,
                               {**e.details, "op": o["op"], "op_number": i}) from e
        # what its selection read through every paragraph's style chain (rule 9, built-in batch 4): a restyle in scope
        # by another op of the spec is a plan-time conflict
        frag.touches += selection_reads(pkg, o["op"], o.get("select") or {}, _op_params(o), frag.touches)
        _check_expect(i, o, frag, expect_notes)
        frags.append(frag)
        if trace:
            trace("plan", i, o["op"], "done", frag)
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
            a.setdefault("slots", ["ascii", "hAnsi", "cs"] + (["eastAsia"] if a.get("east_asia") else []))
            a.setdefault("include_paragraph_marks", True)
            a.setdefault("scope", ["document"])
        plan.property_assertions.append({**a, "op": "spec"})
    plan.notes = [f"assumption: {x}" for x in spec.get("assumptions", [])] + expect_notes + plan.notes
    return plan, frags


def apply_spec(pkg: DocxPackage, fragments: List[PlanFragment],
               trace: Optional[Callable[..., None]] = None) -> DocxPackage:
    """Apply the planned fragments in spec order on a working copy (each op reads the state the previous left).
    ``trace`` as for ``plan_spec``."""
    work = pkg.copy()
    for i, f in enumerate(fragments, 1):
        if trace:
            trace("apply", i, f.op, "start")
        get_op(f.op).apply(work, f)
        if trace:
            trace("apply", i, f.op, "done")
    return work


# ---- composition: a spec whose ops conflict becomes a chain of stages (chain.py), never a question to the user ------

def is_chain(value: Any) -> bool:
    """A chain (``chain.py``): ``{"stages": [{"spec": EditSpec}, …]}`` — several specs one after another. A spec has
    ``ops``; a layout plan has ``kind: "layouts"``; a chain has ``stages`` and neither."""
    return (isinstance(value, dict) and isinstance(value.get("stages"), list) and "ops" not in value
            and value.get("kind") != "layouts")


def chain_specs(value: Any) -> List[Dict[str, Any]]:
    """The specs the engine runs for ``value``: a chain's stages in order, else the spec itself."""
    if is_chain(value):
        return [st["spec"] for st in value["stages"] if isinstance(st, dict) and isinstance(st.get("spec"), dict)]
    return [value] if isinstance(value, dict) else []


def templates_of(value: Any) -> List[Path]:
    """Every template a spec or chain reads (``spec_templates`` over every stage)."""
    out: List[Path] = []
    for spec in chain_specs(value):
        for t in spec_templates(spec):
            if t not in out:
                out.append(t)
    return out


def input_files_of(value: Any) -> List[Path]:
    """Every picture or table a spec or chain reads (``spec_input_files`` over every stage)."""
    out: List[Path] = []
    for spec in chain_specs(value):
        for p in spec_input_files(spec):
            if p not in out:
                out.append(p)
    return out


def approval_key(value: Any) -> Any:
    """What an approval token hashes beside the files: the spec itself, or a chain's key (every stage's name and
    spec, ``chain.chain_key``)."""
    if is_chain(value):
        from .chain import chain_key
        return chain_key(value)
    return value


def _plan_fragments(pkg: DocxPackage, spec: Dict[str, Any]) -> List[PlanFragment]:
    """Each op's fragment on ``pkg``, in spec order, with its selection reads (the per-op half of ``plan_spec``:
    nothing merged, no conflict judged)."""
    bound_slots = _file_bindings(pkg, spec)
    frags: List[PlanFragment] = []
    for i, o in enumerate(spec["ops"], 1):
        try:
            params = _op_params(o)
            if o["op"] == "apply_header_footer_template" and bound_slots:
                params = {**params, "slot_bindings": bound_slots}
            frag = get_op(o["op"]).plan(pkg, o.get("select") or {}, params)
        except JobException as e:
            raise JobException(f"op #{i} ({o['op']}): {e.reason}", e.code,
                               {**e.details, "op": o["op"], "op_number": i}) from e
        frag.touches += selection_reads(pkg, o["op"], o.get("select") or {}, _op_params(o), frag.touches)
        frag.op_index = i - 1
        frags.append(frag)
    return frags


def _trial(frags: List[PlanFragment]) -> List[PlanFragment]:
    """Copies of the fragments as ``merge_fragments`` would merge them (the shared numbering label-font edit given one
    owner), so a judgement or an in-memory apply never touches the originals. ``coalesce_numbering_fonts`` drops a
    key of ``transform_data`` and reassigns the lists it trims, so a shallow copy with its own dict is enough."""
    from .ops.font_numbering import coalesce_numbering_fonts
    trial = []
    for i, f in enumerate(frags):
        t = copy.copy(f)
        t.transform_data = dict(f.transform_data)
        t.op_index = i
        trial.append(t)
    coalesce_numbering_fonts(trial)
    return trial


def _merges_cleanly(frags: List[PlanFragment]) -> bool:
    """Whether these fragments would merge into one plan (rule 9 and the mode rule), judged the way
    ``merge_fragments`` judges them and without side effects."""
    from .ops.base import check_conflicts, merged_mode
    trial = _trial(frags)
    try:
        check_conflicts(trial)
        merged_mode(trial)
    except JobException as e:
        if e.code != "conflict":
            raise
        return False
    return True


def _stage_modes(pkg: DocxPackage, spec: Dict[str, Any], cuts: List[int]) -> List[str]:
    """Each stage's mode as its ops make it on ``pkg`` — what ``plan_spec`` will compare the stage's declaration
    against — each stage planned on the output of the stages before it. An op refines its mode in ``plan()`` (a
    template op with a literal fill plans untracked; a properties op that only clears plans formatting), so the op
    class alone can declare a mode the engine then refuses."""
    from .ops.base import merged_mode
    ops = _bound_ops_first(spec)
    current = pkg
    modes: List[str] = []
    at = 0
    for take in cuts:
        group = ops[at:at + take]
        at += take
        trial = _trial(_plan_fragments(current, {**spec, "ops": group}))
        modes.append(merged_mode(trial))
        current = apply_spec(current, trial)
    return modes


def _stage_spec(spec: Dict[str, Any], ops: List[Dict[str, Any]], frags: List[PlanFragment], *, first: bool,
                last: bool, index: int, count: int) -> Dict[str, Any]:
    """One stage of a composed chain: the spec's own fields over these ops; its mode what the ops make it; source-bound
    bindings on the first stage only (``validate_chain``); the spec's final assertions on the last stage."""
    from .ops.base import merged_mode
    stage = {k: copy.deepcopy(v) for k, v in spec.items() if k not in ("ops", "assertions", "bindings", "questions")}
    # a layout's pattern pin belongs to the first stage alone: a later stage reads the step before it, whose
    # structure the first stage already checked and whose pattern the step may have changed
    if not first:
        stage.pop("applies_to", None)
    # each stage's ops decide its layout reference (a font change gives it up, a template claims it); the spec's own
    # declaration described the whole, not one step
    if isinstance(stage.get("review"), dict):
        stage["review"] = {k: v for k, v in stage["review"].items() if k != "geometry_reference"}
        if not stage["review"]:
            del stage["review"]
    stage["ops"] = copy.deepcopy(ops)
    stage["mode"] = merged_mode([copy.copy(f) for f in frags])
    stage["summary"] = f"{spec.get('summary') or ''} (step {index} of {count})".strip()
    if first and spec.get("bindings"):
        stage["bindings"] = copy.deepcopy(spec["bindings"])
    if last and spec.get("assertions"):
        stage["assertions"] = copy.deepcopy(spec["assertions"])
    return stage


def _bound_ops_first(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Source-bound template bindings must run in a chain's first stage: the op that reads them leads the order."""
    ops = list(spec["ops"])
    if not spec.get("bindings"):
        return ops
    bound = [o for o in ops if o.get("op") == "apply_header_footer_template"]
    return bound + [o for o in ops if o not in bound]


def stage_cuts(pkg: DocxPackage, spec: Dict[str, Any]) -> List[int]:
    """Where the spec's ops split into stages on ``pkg``: the lengths of the longest prefixes of the remaining ops
    that merge into one plan, in order, each later prefix planned on the output of the stages before it (predict
    first: a later stage's prediction is computed on the bytes it will actually read). ``[len(ops)]`` when the
    whole spec merges. Raises what planning raises when an op cannot be planned at all."""
    ops = _bound_ops_first(spec)
    current = pkg
    cuts: List[int] = []
    while ops:
        frags = _plan_fragments(current, {**spec, "ops": ops})
        take = 1
        while take < len(ops) and _merges_cleanly(frags[:take + 1]):
            take += 1
        cuts.append(take)
        current = apply_spec(current, _trial(frags[:take]))
        ops = ops[take:]
    return cuts


def compose_chain(spec: Dict[str, Any], cuts: List[int], pkg: Optional[DocxPackage] = None) -> Dict[str, Any]:
    """The chain whose stages are the spec's ops cut at ``cuts`` (lengths, in order); the spec's own fields on
    every stage. With ``pkg`` each stage declares the mode its ops make it on that file (``_stage_modes``); without
    one, the mode the op classes give it. ``cuts`` summing to anything but the op count is a defect."""
    ops = _bound_ops_first(spec)
    if sum(cuts) != len(ops) or any(c < 1 for c in cuts):
        raise ValueError(f"stage cuts {cuts} do not cover {len(ops)} op(s)")
    modes = _stage_modes(pkg, spec, cuts) if pkg is not None else None
    stages = []
    at = 0
    for n, take in enumerate(cuts, 1):
        group = ops[at:at + take]
        at += take
        # the mode of a stage is what its ops make it; judged from the ops alone (every text-placing op is
        # content or untracked by its own class), the same rule merged_mode applies to planned fragments
        frags = [get_op(o["op"]).fragment(o.get("select"), _op_params(o), mode=_op_mode(o)) for o in group]
        stage = _stage_spec(spec, group, frags, first=n == 1, last=n == len(cuts), index=n, count=len(cuts))
        if modes is not None:
            stage["mode"] = modes[n - 1]
        stages.append({"name": f"step {n}", "spec": stage})
    return {"name": str(spec.get("summary") or spec.get("request") or "edit"), "stages": stages}


def _op_mode(o: Dict[str, Any]) -> str:
    """The mode an op's class and params give it (``tracked: false`` on a text op makes it untracked)."""
    cls = get_op(o["op"])
    mode = cls.mode
    if mode == "content" and (o.get("tracked") is False or _op_params(o).get("tracked") is False):
        return "untracked"
    return mode


def split_into_stages(pkg: DocxPackage, spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The chain the engine composes from a spec whose ops conflict on ``pkg`` (rule 5: overlapping ops are never
    ordered by guess inside one plan — but the user's order across STAGES is the user's, as a chain keeps it), or
    None when the whole spec merges into one plan."""
    cuts = stage_cuts(pkg, spec)
    if len(cuts) <= 1:
        return None
    return compose_chain(spec, cuts, pkg)


def merge_cuts(all_cuts: List[List[int]]) -> List[int]:
    """One cut list every file in a batch can take: the union of every file's cut points (a stage a file needs
    split further is split for every file; a stage that merges on every file stays one)."""
    points: set = set()
    total = None
    for cuts in all_cuts:
        at = 0
        for c in cuts:
            at += c
            points.add(at)
        total = at if total is None else total
    if total is None:
        return []
    out, prev = [], 0
    for p in sorted(points):
        if p > prev:
            out.append(p - prev)
            prev = p
    return out


def plan_chain(pkg: DocxPackage, chain: Dict[str, Any], pattern_id: Optional[str] = None,
               trace: Optional[Callable[..., None]] = None) -> List[Tuple[Plan, List[PlanFragment]]]:
    """Predict-first for every stage of a chain on one file: stage 1 on the file, each later stage on the bytes the
    stage before it would produce (applied in memory, never written). Raises what ``plan_spec`` raises, with the
    stage named."""
    out: List[Tuple[Plan, List[PlanFragment]]] = []
    current = pkg
    for i, st in enumerate(chain.get("stages") or [], 1):
        try:
            plan, frags = plan_spec(current, st["spec"], pattern_id if i == 1 else None, trace)
        except JobException as e:
            raise JobException(f"step {i}: {e.reason}", e.code, {**e.details, "chain_stage": i}) from e
        out.append((plan, frags))
        current = apply_spec(current, frags)
    return out
