"""Independent, source-bound final verification for adaptive formatting goals.

The binding is evidence to check, never a selector to trust. This module re-finds
the title in the original package and derives the permitted property changes
itself. It then checks the final formatting AND the original-to-final residual.
An editing plan, generated assertion, or successful intermediate stage cannot
authorize another change. The caller must still run the ordinary V1–V10 suite.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from .outcomes import _conditional
from .props import (Styles, _check_effective_para, _check_effective_run, _check_props_ppr,
                    _check_props_rpr, _v_gets_para, _v_gets_run, _v_run_elements,
                    first_difference, sig)
from .reader import STYLES_REL, VPackage, W


_RUN = {"b", "i", "caps", "smallCaps", "strike"}
_PARAGRAPH = {"keepNext", "keepLines", "widowControl", "pageBreakBefore"}
_DELETED = {W + "del", W + "moveFrom", W + "delText"}
_BINDING_KEYS = {"source_sha256", "contract_sha256", "candidate_id", "target", "choice"}
_REVISION_TAGS = {W + name for name in ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange",
                  "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange", "tblGridChange",
                  "tblPrExChange", "numberingChange", "cellIns", "cellDel", "cellMerge")}


def _hash_json(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_contract(contract: Dict[str, Any]) -> None:
    allowed = {"kind", "target", "run", "paragraph", "tracked", "author", "request"}
    if not isinstance(contract, dict) or set(contract) - allowed:
        raise ValueError("goal must be an object containing only the supported contract keys")
    if contract.get("kind") != "format_section_title":
        raise ValueError("unsupported goal kind; expected format_section_title")
    target = contract.get("target")
    if not isinstance(target, dict) or set(target) - {"title", "section_number", "style_id"}:
        raise ValueError("target must contain title and optional section_number/style_id")
    if "title" not in target:
        raise ValueError("target.title is required")
    for key, value in target.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"target.{key} must be non-empty exact text")
    if not any(contract.get(key) for key in ("run", "paragraph")):
        raise ValueError("goal requires run and/or paragraph properties")
    for key, keys in (("run", _RUN), ("paragraph", _PARAGRAPH)):
        if key not in contract:
            continue
        values = contract[key]
        if (not isinstance(values, dict) or not values or set(values) - keys
                or any(type(value) is not bool for value in values.values())):
            raise ValueError(f"{key} must contain supported boolean properties")
    if contract.get("run", {}).get("caps") is True and contract.get("run", {}).get("smallCaps") is True:
        raise ValueError("caps and smallCaps cannot both be true")
    if "tracked" in contract and type(contract["tracked"]) is not bool:
        raise ValueError("tracked must be boolean")
    for key in ("author", "request"):
        if key in contract and (not isinstance(contract[key], str) or not contract[key].strip()):
            raise ValueError(f"{key} must be non-empty text")


def _own_elements(paragraph):
    """Walk a paragraph without borrowing nested paragraphs or deleted content."""
    pending = list(reversed(list(paragraph)))
    while pending:
        element = pending.pop()
        if element.tag in {W + "p", W + "del", W + "moveFrom"}:
            continue
        yield element
        pending.extend(reversed(list(element)))


def _visible_text(paragraph) -> str:
    pieces = []
    structural = {W + "tab": "\t", W + "ptab": "\t", W + "br": "\n",
                  W + "cr": "\n", W + "noBreakHyphen": "-"}
    for element in _own_elements(paragraph):
        if element.tag == W + "t":
            pieces.append(element.text or "")
        elif element.tag in structural and element.getparent().tag == W + "r":
            pieces.append(structural[element.tag])
    return "".join(pieces)


def _unsupported(paragraph) -> bool:
    if any(parent.tag in {W + "p", W + "del", W + "moveFrom", W + "txbxContent"}
           for parent in paragraph.iterancestors()):
        return True
    return any(element.tag in _DELETED for element in paragraph.iter())


def _styles(pkg: VPackage) -> Styles:
    part = pkg.related(STYLES_REL)
    return Styles(pkg.xml(part) if part else None)


def _revision_ids(pkg: VPackage) -> set:
    """Revision identifiers are shared across the package's text-bearing parts."""
    found = set()
    for part in pkg.text_parts():
        for element in pkg.xml(part).iter():
            value = element.get(W + "id") or ""
            if element.tag in _REVISION_TAGS and value.isascii() and value.isdigit():
                found.add(int(value))
    return found


def _candidates(root, styles: Styles, part: str, source_hash: str, target: Dict[str, Any]):
    """Reconstruct exact target evidence without the transform's selector/resolver."""
    candidates, excluded = [], 0
    anchor_index, anchor_text = None, None
    for index, paragraph in enumerate(root.iter(W + "p")):
        text = _visible_text(paragraph)
        unsupported = _unsupported(paragraph)
        nested = any(parent.tag in {W + "p", W + "txbxContent"} for parent in paragraph.iterancestors())
        if text.startswith("SECTION ") and not nested:
            anchor_index, anchor_text = (None, None) if unsupported else (index, text)
        if text != target["title"]:
            continue
        if unsupported:
            excluded += 1
            continue
        style = paragraph.find(W + "pPr/" + W + "pStyle")
        style_id = style.get(W + "val") if style is not None else styles.default.get("paragraph")
        if "style_id" in target and target["style_id"] != style_id:
            continue
        if "section_number" in target and (anchor_text != "SECTION " + target["section_number"]
                or anchor_index is None or anchor_index >= index):
            continue
        identity = {"source_sha256": source_hash, "part": part, "paragraph_index": index, "text": text}
        candidates.append({"candidate_id": _hash_json(identity), "part": part, "paragraph_index": index,
                           "text": text, "style_id": style_id,
                           "in_table": any(parent.tag == W + "tc" for parent in paragraph.iterancestors()),
                           "section_number": anchor_text[len("SECTION "):] if anchor_text else None,
                           "section_anchor_index": anchor_index, "section_anchor_text": anchor_text})
    return candidates, excluded


def _check_binding(binding, candidates: List[dict], excluded: int, source_hash: str, contract_hash: str) -> dict:
    if not isinstance(binding, dict) or set(binding) != _BINDING_KEYS:
        raise ValueError("invalid source binding shape")
    if binding["source_sha256"] != source_hash:
        raise ValueError("source binding is stale: original bytes do not match source_sha256")
    if binding["contract_sha256"] != contract_hash:
        raise ValueError("approved goal changed: contract_sha256 does not match")
    if binding["choice"] not in ("unique", "user"):
        raise ValueError("binding choice must be unique or user")
    if binding["choice"] == "unique" and (len(candidates) != 1 or excluded):
        raise ValueError("unique binding does not select a unique source candidate")
    matches = [candidate for candidate in candidates if candidate["candidate_id"] == binding["candidate_id"]]
    if len(matches) != 1 or _hash_json(matches[0]) != _hash_json(binding["target"]):
        raise ValueError("bound target evidence does not match an independently resolved source candidate")
    return matches[0]


def _single(parent, name: str, where: str, errors: List[str]):
    children = parent.findall(W + name) if parent is not None else []
    if len(children) > 1:
        errors.append(f"{where}: duplicate w:{name} containers are unsupported")
    return children[0] if children else None


def _container_identity(before, after, where: str, errors: List[str]) -> None:
    """Property helpers check children; container attributes/text must survive too."""
    if after is None:
        return
    attrs = dict(before.attrib) if before is not None else {}
    text = (before.text or "").strip() if before is not None else ""
    if dict(after.attrib) != attrs or (after.text or "").strip() != text:
        errors.append(f"{where}: property-container attributes or text changed")


def _new_marker(before, after, name: str, tracked: bool, where: str, errors: List[str], used_ids: set) -> None:
    """Close the residual exemption around exactly one validated new snapshot."""
    source_changes = before.findall(W + name) if before is not None else []
    output_changes = after.findall(W + name) if after is not None else []
    if not tracked:
        if [sig(c) for c in source_changes] != [sig(c) for c in output_changes]:
            errors.append(f"{where}: an existing formatting revision changed")
        return
    if source_changes or len(output_changes) != 1:
        errors.append(f"{where}: tracked edit requires exactly one new {name} and no source snapshot")
        return
    change = output_changes[0]
    allowed = {W + "id", W + "author", W + "date",
               "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"}
    if set(change.attrib) - allowed:
        errors.append(f"{where}: unexpected tracked-property revision attributes")
    if len(change) != 1 or change[0].attrib or (change[0].text or "").strip() or (change.text or "").strip():
        errors.append(f"{where}: malformed tracked property snapshot")
    revision_id = change.get(W + "id") or ""
    if not revision_id.isascii() or not revision_id.isdigit():
        errors.append(f"{where}: tracked property revision needs a non-negative numeric id")
    elif int(revision_id) in used_ids:
        errors.append(f"{where}: new formatting revision reuses id {revision_id}")
    else:
        used_ids.add(int(revision_id))


def _restore_child(parent, name: str, source) -> None:
    """Undo only a property container which has independently passed its checks."""
    output = parent.find(W + name)
    if output is not None:
        if source is None:
            parent.remove(output)
        else:
            parent.replace(output, copy.deepcopy(source))


def _formatting(source_root, output_root, source_styles: Styles, output_styles: Styles,
                index: int, contract: dict, errors: List[str], used_ids: set) -> None:
    """Validate all requested values, then restore permitted properties for residual comparison."""
    source_p = list(source_root.iter(W + "p"))[index]
    output_p = list(output_root.iter(W + "p"))[index]
    asked_run = _v_run_elements(contract.get("run"))
    asked_para = contract.get("paragraph", {})
    conditional_run = set(asked_run)
    if asked_run.get("caps") is True or asked_run.get("smallCaps") is True:
        conditional_run.update(("caps", "smallCaps"))
    for label, styles, paragraph in (("source", source_styles, source_p), ("output", output_styles, output_p)):
        if _conditional(styles, paragraph, conditional_run, asked_para):
            errors.append(f"{label} target: conditional table-style formatting cannot be resolved independently")
    tracked, author = contract.get("tracked", False), contract.get("author", "Abraham Borg")
    source_ppr = _single(source_p, "pPr", "source target", errors)
    output_ppr = _single(output_p, "pPr", "output target", errors)
    source_runs = [element for element in _own_elements(source_p) if element.tag == W + "r"]
    output_runs = [element for element in _own_elements(output_p) if element.tag == W + "r"]
    if len(source_runs) != len(output_runs):
        errors.append("target run structure changed")
        return
    if asked_run:
        if not source_runs:
            errors.append("the requested title has no independently checked runs")
        pairs = [(s, o, f"target run {i}") for i, (s, o) in enumerate(zip(source_runs, output_runs))]
        pairs.append((source_ppr, output_ppr, "target paragraph mark"))
        for source_parent, output_parent, where in pairs:
            before = _single(source_parent, "rPr", "source " + where, errors)
            after = _single(output_parent, "rPr", "output " + where, errors)
            gets = _v_gets_run(before, source_p, source_styles, asked_run, False)
            _check_effective_run(after, output_p, output_styles, asked_run, False, where, errors)
            if gets:
                _container_identity(before, after, where, errors)
                for key in gets:
                    if after is not None and len(after.findall(W + key)) != 1:
                        errors.append(f"{where}: expected exactly one w:{key}")
                _check_props_rpr(before, after, gets, tracked, author, where, errors)
                _new_marker(before, after, "rPrChange", tracked, where, errors, used_ids)
                if output_parent is not None:
                    _restore_child(output_parent, "rPr", before)
            elif sig(before) != sig(after):
                errors.append(f"{where}: properties already satisfied the goal but changed anyway")
    _check_effective_para(output_p, output_styles, asked_para, "target paragraph", errors)
    gets_para = _v_gets_para(source_p, source_styles, asked_para)
    _container_identity(source_ppr, output_ppr, "target paragraph", errors)
    if not asked_run:
        source_mark = _single(source_ppr, "rPr", "source paragraph mark", errors)
        output_mark = _single(output_ppr, "rPr", "output paragraph mark", errors)
        if sig(source_mark) != sig(output_mark):
            errors.append("target paragraph: unrequested paragraph-mark formatting changed")
    if gets_para:
        for key in gets_para:
            if output_ppr is not None and len(output_ppr.findall(W + key)) != 1:
                errors.append(f"target paragraph: expected exactly one w:{key}")
        _check_props_ppr(source_ppr, output_ppr, gets_para, tracked, author, "target paragraph", errors)
        _new_marker(source_ppr, output_ppr, "pPrChange", tracked, "target paragraph", errors, used_ids)
        _restore_child(output_p, "pPr", source_ppr)
    elif source_ppr is None and output_ppr is not None and not len(output_ppr) and not output_ppr.attrib:
        # A requested paragraph-mark rPr can require a new pPr wrapper. The checked
        # mark was restored above; only that now-empty wrapper may disappear here.
        output_p.remove(output_ppr)


def verify_goal(original: bytes | Path, candidate: bytes | Path, contract: dict, binding: dict) -> dict:
    """Check the approved source-bound goal, returning failures rather than authorizing edits.

    Only the main document's exact selected run/mark/paragraph properties may
    differ. Every other member's bytes and the main document's residual XML must
    survive. A passing result is an additional requirement, not delivery approval.
    """
    checks: List[dict] = []
    errors: List[str] = []
    packages = []

    def record(identifier: str, start: int, summary: str) -> None:
        checks.append({"id": identifier, "status": "FAIL" if len(errors) > start else "PASS",
                       "summary": "; ".join(errors[start:]) if len(errors) > start else summary})

    phase = "G1"
    try:
        _validate_contract(contract)
        record(phase, 0, "approved goal uses the supported exact-title formatting contract")
        phase = "G2"
        source_bytes = original if isinstance(original, bytes) else Path(original).read_bytes()
        output_bytes = candidate if isinstance(candidate, bytes) else Path(candidate).read_bytes()
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        source = VPackage(source_bytes)
        packages.append(source)
        output = VPackage(output_bytes)
        packages.append(output)
        part = source.main_part()
        if source.names.count(part) != 1 or output.names.count(part) != 1 or output.main_part() != part:
            raise ValueError("main document part is missing, duplicated, or changed")
        source_root, output_root = source.xml(part), output.xml(part)
        if source_root.tag != W + "document" or output_root.tag != W + "document":
            raise ValueError("goal requires a Transitional WordprocessingML main document")
        source_styles, output_styles = _styles(source), _styles(output)
        candidates, excluded = _candidates(source_root, source_styles, part, source_hash, contract["target"])
        target = _check_binding(binding, candidates, excluded, source_hash, _hash_json(contract))
        record(phase, len(errors), f"source binding independently confirmed among {len(candidates)} candidate(s)")
        phase = "G3"
        final_candidates, _ = _candidates(output_root, output_styles, part, source_hash, contract["target"])
        if target not in final_candidates:
            raise ValueError("the final target's index, literal text, style or section evidence changed")
        if len(list(source_root.iter(W + "p"))) != len(list(output_root.iter(W + "p"))):
            raise ValueError("document paragraph structure changed")
        record(phase, len(errors), "the final target retains its original identity and section context")
        phase = "G4"
        start = len(errors)
        _formatting(source_root, output_root, source_styles, output_styles,
                    target["paragraph_index"], contract, errors,
                    _revision_ids(source) if contract.get("tracked", False) else set())
        record(phase, start, "requested run, paragraph-mark and paragraph properties independently verified")
        phase = "G5"
        start = len(errors)
        if source.names != output.names:
            errors.append("package members or their order changed")
        else:
            for before, after in zip(source.infos, output.infos):
                if before.filename != part and source.zip.read(before) != output.zip.read(after):
                    errors.append(f"unapproved package-member change: {before.filename}")
        difference = first_difference(source_root, output_root)
        if difference:
            errors.append("document content/structure outside the approved property changes differs: " + difference)
        record(phase, start, "all document text, structures and unrelated package members preserved")
    except Exception as exc:
        errors.append(f"{phase}: {type(exc).__name__}: {exc}")
        record(phase, len(errors) - 1, "")
    finally:
        for package in packages:
            package.zip.close()
    return {"passed": not errors, "checks": checks, "errors": errors}
