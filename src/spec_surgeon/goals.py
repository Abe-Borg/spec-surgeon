"""Source-bound adaptive goals, separate from the EditSpec that implements them.

The first capability formats one *explicitly named* section title. The approved title is
literal document text, not a label the model is allowed to interpret or rewrite. A style
is useful evidence, but restricts selection only when the contract explicitly names it.
Normal paragraphs, manually formatted titles and table-cell paragraphs use the same
goal; their concrete paragraph bindings and formatting plans are resolved per document.

Contract example::

    {"kind": "format_section_title",
     "target": {"title": "FIRE SUPPRESSION", "section_number": "21 13 13"},
     "run": {"b": True}, "paragraph": {"keepNext": True}, "tracked": False}

``section_number`` and ``paragraph`` are optional. A section number means an exact
preceding ``SECTION <number>`` paragraph, with no later ``SECTION ...`` anchor between
it and the title. ``target.style_id`` is an optional, explicit style restriction.
No whitespace, case or section-number normalization is implicit. Textboxes and titles
with deleted content need another capability; they are reported instead of guessed.

Resolution and compilation never write files. The returned ordinary EditSpec still
goes through the existing per-file V1–V10 pipeline. ``verify_goal`` is an additional,
independent final requirement; it never replaces that pipeline or authorizes delivery.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .editspec import plan_spec, validate_spec
from .ooxml import block_full_text, paragraphs
from .ops.selectors import StyleSheet
from .package import DocxPackage, W


KIND = "format_section_title"
RUN_PROPERTIES = frozenset(("b", "i", "caps", "smallCaps", "strike"))
PARAGRAPH_PROPERTIES = frozenset(("keepNext", "keepLines", "widowControl", "pageBreakBefore"))


class GoalError(ValueError):
    def __init__(self, reason: str, code: str = "goal-contract"):
        super().__init__(reason)
        self.reason, self.code = reason, code


class NeedsInput(GoalError):
    """A goal has no uniquely supported target; alternatives are source-bound evidence."""

    def __init__(self, reason: str, alternatives=None, code: str = "goal-target", resolution=None):
        super().__init__(reason, code)
        self.alternatives = copy.deepcopy(alternatives or [])
        self.resolution = copy.deepcopy(resolution or {})


def _digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def contract_author(contract: Any) -> str:
    """The author a contract's tracked changes carry: the one it names, else the engine's own default
    (``ops.replace_text.default_author``: ``SPEC_SURGEON_AUTHOR`` or the user's account name), never a literal name."""
    from .ops.replace_text import default_author
    return (contract.get("author") if isinstance(contract, dict) else None) or default_author()


def validate_contract(contract: Dict[str, Any]) -> None:
    """Validate a closed, explicit goal grammar without inventing defaults for intent."""
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        from .goal_contracts import validate_contract as validate_effects
        return validate_effects(contract)
    keys = {"kind", "target", "run", "paragraph", "tracked", "author", "request"}
    if not isinstance(contract, dict) or set(contract) - keys:
        raise GoalError(f"goal must be an object with only {sorted(keys)}")
    if contract.get("kind") != KIND:
        raise GoalError(f"unsupported goal kind {contract.get('kind')!r}; supported: {KIND}")
    target = contract.get("target")
    if not isinstance(target, dict) or set(target) - {"title", "section_number", "style_id"}:
        raise GoalError("target must contain title and optionally section_number or style_id")
    if not isinstance(target.get("title"), str) or not target["title"].strip():
        raise GoalError("target.title must be the non-empty, exact approved document text")
    for key in ("section_number", "style_id"):
        if key in target and (not isinstance(target[key], str) or not target[key].strip()):
            raise GoalError(f"target.{key} must be non-empty exact text")
    if not any(contract.get(key) for key in ("run", "paragraph")):
        raise GoalError("goal needs at least one explicit run or paragraph property")
    for key, allowed in (("run", RUN_PROPERTIES), ("paragraph", PARAGRAPH_PROPERTIES)):
        if key not in contract:
            continue
        values = contract[key]
        if (not isinstance(values, dict) or not values or set(values) - allowed
                or any(type(value) is not bool for value in values.values())):
            raise GoalError(f"{key} must contain boolean values from {sorted(allowed)}")
    if contract.get("run", {}).get("caps") is True and contract.get("run", {}).get("smallCaps") is True:
        raise GoalError("caps and smallCaps cannot both be true")
    if "tracked" in contract and type(contract["tracked"]) is not bool:
        raise GoalError("tracked must be a boolean")
    for key in ("author", "request"):
        if key in contract and (not isinstance(contract[key], str) or not contract[key].strip()):
            raise GoalError(f"{key} must be non-empty text")


def _ancestors(element):
    parent = element.getparent()
    while parent is not None:
        yield parent
        parent = parent.getparent()


def _visible_text(paragraph) -> str:
    """Literal own visible characters, excluding nested paragraphs and deleted content."""
    pieces = []
    for element in paragraph.iter():
        if element is paragraph:
            continue
        ancestors = []
        for parent in _ancestors(element):
            if parent is paragraph:
                break
            ancestors.append(parent)
        if any(parent.tag in (W + "p", W + "del", W + "moveFrom") for parent in ancestors):
            continue
        if element.tag == W + "t":
            pieces.append(element.text or "")
        elif element.getparent() is not None and element.getparent().tag == W + "r":
            if element.tag in (W + "tab", W + "ptab"):
                pieces.append("\t")
            elif element.tag in (W + "br", W + "cr"):
                pieces.append("\n")
            elif element.tag == W + "noBreakHyphen":
                pieces.append("-")
    return "".join(pieces)


def _unsupported(paragraph) -> Optional[str]:
    if any(parent.tag in (W + "p", W + "txbxContent") for parent in _ancestors(paragraph)):
        return "the matching paragraph is inside a textbox"
    if any(parent.tag in (W + "del", W + "moveFrom") for parent in _ancestors(paragraph)):
        return "the matching paragraph is inside deleted or moved-away content"
    if any(element.tag in (W + "del", W + "moveFrom", W + "delText") for element in paragraph.iter()):
        return "the matching paragraph contains pending deleted content"
    return None


def _binding(resolution: Dict[str, Any], candidate: Dict[str, Any], choice: str) -> Dict[str, Any]:
    return {"source_sha256": resolution["source_sha256"], "contract_sha256": resolution["contract_sha256"],
            "candidate_id": candidate["candidate_id"], "target": copy.deepcopy(candidate), "choice": choice}


def resolve_goal(source: Path, contract: Dict[str, Any]) -> Dict[str, Any]:
    """List exact supported candidates; automatically bind only a unique candidate.

    Ambiguity is a normal ``needs_input`` result, not a best-match score. A caller may
    present candidates and call ``choose_goal`` with the user's deliberate selection.
    """
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        from .goal_contracts import resolve_goal as resolve_effects
        return resolve_effects(source, contract)
    validate_contract(contract)
    source = Path(source)
    data = source.read_bytes()
    source_hash = hashlib.sha256(data).hexdigest()
    pkg = DocxPackage(data, name=str(source))
    try:
        part = pkg.main_part()
        sheet = StyleSheet(pkg)
        target = contract["target"]
        candidates, excluded = [], []
        anchor_index, anchor_text = None, None
        for index, paragraph in enumerate(paragraphs(pkg.xml(part))):
            text = _visible_text(paragraph)
            unsupported = _unsupported(paragraph)
            # A later SECTION marker must reset context even if its own text cannot
            # safely establish a binding (for example, a marker with pending deletion).
            if text.startswith("SECTION ") and not any(p.tag in (W + "p", W + "txbxContent") for p in _ancestors(paragraph)):
                anchor_index, anchor_text = (index, text) if unsupported is None else (None, None)
            if text != target["title"]:
                continue
            if unsupported is not None:
                excluded.append({"part": part, "paragraph_index": index, "text": text, "reason": unsupported})
                continue
            style_id = sheet.paragraph_style_id(paragraph)
            if "style_id" in target and style_id != target["style_id"]:
                continue
            if "section_number" in target and anchor_text != "SECTION " + target["section_number"]:
                continue
            # An anchor is preceding evidence, never the target itself.
            if "section_number" in target and (anchor_index is None or anchor_index >= index):
                continue
            identity = {"source_sha256": source_hash, "part": part, "paragraph_index": index, "text": text}
            candidates.append({"candidate_id": _digest(identity), "part": part, "paragraph_index": index,
                               "text": text, "style_id": style_id,
                               "in_table": any(p.tag == W + "tc" for p in _ancestors(paragraph)),
                               "section_number": anchor_text[len("SECTION "):] if anchor_text else None,
                               "section_anchor_index": anchor_index, "section_anchor_text": anchor_text})
    finally:
        pkg.close()
    if len(candidates) == 1 and not excluded:
        status, reason = "resolved", "one exact source target matches the approved goal"
    elif candidates and excluded:
        status, reason = "needs_input", "matching titles include unsupported structures; choose a supported target explicitly"
    elif candidates:
        status, reason = "needs_input", "multiple exact title targets match; choose the intended paragraph"
    else:
        status, reason = "needs_input", "no supported paragraph matches the exact approved title and restrictions"
    result = {"status": status, "reason": reason, "source": str(source.resolve()), "source_sha256": source_hash,
              "contract_sha256": _digest(contract), "candidates": candidates, "excluded": excluded, "binding": None}
    if status == "resolved":
        result["binding"] = _binding(result, candidates[0], "unique")
    return result


def choose_goal(source: Path, contract: Dict[str, Any], candidate_id, *, effect_id=None, binding=None) -> Dict[str, Any]:
    """Bind a deliberate user choice from the current source's candidate list."""
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        from .goal_contracts import choose_goal as choose_effects
        return choose_effects(source, contract, candidate_id, effect_id=effect_id, binding=binding)
    if isinstance(candidate_id, list):
        if len(candidate_id) != 1:
            raise GoalError("the title goal needs exactly one candidate", "goal-target")
        candidate_id = candidate_id[0]
    resolution = resolve_goal(source, contract)
    candidate = next((c for c in resolution["candidates"] if c["candidate_id"] == candidate_id), None)
    if candidate is None:
        raise GoalError("the selected candidate is not in the current source; resolve and review again", "stale-binding")
    return _binding(resolution, candidate, "user")


def _checked_binding(resolution: Dict[str, Any], binding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if binding is None:
        if resolution["binding"] is None:
            raise NeedsInput(resolution["reason"], resolution["candidates"], resolution=resolution)
        return resolution["binding"]
    if not isinstance(binding, dict) or set(binding) != {
            "source_sha256", "contract_sha256", "candidate_id", "target", "choice"}:
        raise GoalError("invalid goal binding; resolve and review again", "stale-binding")
    if any(binding.get(key) != resolution[key] for key in ("source_sha256", "contract_sha256")):
        raise GoalError("source or approved goal changed since the binding; resolve and review again", "stale-binding")
    candidate = next((c for c in resolution["candidates"] if c["candidate_id"] == binding.get("candidate_id")), None)
    if candidate is None or _digest(candidate) != _digest(binding["target"]):
        raise GoalError("the binding's evidence does not match the current source", "stale-binding")
    if binding["choice"] not in ("unique", "user") or (binding["choice"] == "unique" and resolution["status"] != "resolved"):
        raise GoalError("an ambiguous target needs an explicit candidate choice", "goal-target")
    return copy.deepcopy(binding)


def compile_goal(source: Path, contract: Dict[str, Any], binding: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compile evidence to a normal EditSpec, dry-plan it, and leave execution guarded."""
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        candidate = compile_goal_candidate(source, contract, binding)
        return candidate[candidate["kind"]]
    resolution = resolve_goal(source, contract)
    bound = _checked_binding(resolution, binding)
    data = Path(source).read_bytes()
    if hashlib.sha256(data).hexdigest() != bound["source_sha256"]:
        raise GoalError("source changed during goal compilation", "stale-binding")
    pkg = DocxPackage(data, name=str(source))
    try:
        target = bound["target"]
        index = target["paragraph_index"]
        paragraph = paragraphs(pkg.xml(target["part"]))[index]
        predicate = {"index_range": [index, index + 1],
                     "text_regex": r"\A" + re.escape(block_full_text(paragraph)) + r"\Z"}
        if "style_id" in contract["target"]:
            predicate["style_in"] = [contract["target"]["style_id"]]
        params = {key: copy.deepcopy(contract[key]) for key in ("run", "paragraph", "author") if key in contract}
        params.update({"tracked": contract.get("tracked", False), "include_paragraph_marks": True,
                       "author": contract_author(contract),
                       "preserve_fonts": [], "skip_sym_runs": False})
        spec = {"request": contract.get("request", "Format the explicitly approved section title " + repr(target["text"])),
                "summary": "Format one source-bound section title; preserve its exact text and document structure.",
                "mode": "formatting", "ops": [{"op": "set_properties",
                                                "select": {"scope": ["document"], "paragraphs": predicate},
                                                "params": params}]}
        validate_spec(spec)
        plan_spec(pkg, spec)
        return spec
    finally:
        pkg.close()


def verify_goal(original, candidate, contract: Dict[str, Any], binding: Dict[str, Any]) -> Dict[str, Any]:
    """Convenient entry point; the verifier implementation has no transform-side imports."""
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        from .verify.effects import verify_effects as independent_effects
        return independent_effects(original, candidate, contract, binding)
    from .verify.goals import verify_goal as independent_check
    return independent_check(original, candidate, contract, binding)


def compile_goal_candidate(source, contract, binding=None):
    """Return a spec-or-chain candidate without confusing the goal with its method."""
    if isinstance(contract, dict) and contract.get("kind") == "document_effects":
        from .goal_contracts import compile_goal_candidate as compile_effects
        return compile_effects(source, contract, binding)
    return {"kind": "spec", "spec": compile_goal(source, contract, binding)}


def approved_goal_words(contract):
    """Only explicitly approved replacement/insertion literals, never the entire contract JSON."""
    validate_contract(contract)
    if contract["kind"] == "document_effects":
        from .goal_contracts import approved_goal_words as effect_words
        return effect_words(contract)
    return []


def goal_catalog():
    """Machine-readable closed goal grammar for the planner and review UI."""
    from .goal_contracts import goal_catalog as effects_catalog
    return effects_catalog()


def goal_templates(contract):
    """Approved external templates; callers pin these inputs before planning or delivery."""
    validate_contract(contract)
    if contract["kind"] == "document_effects":
        from .goal_contracts import goal_templates as effect_templates
        return effect_templates(contract)
    return []


def validate_binding(source, contract, binding=None):
    """Check approved intent and original source evidence without selecting an editing method."""
    resolution = resolve_goal(source, contract)
    if contract["kind"] == "document_effects":
        from .goal_contracts import _checked_binding as checked_effects
        return checked_effects(resolution, contract, binding)
    return _checked_binding(resolution, binding)
