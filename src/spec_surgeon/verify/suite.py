"""The verification suite — V1…V10 as a filled-in record.

Inputs are bytes (source package, output package) and the plan as plain data. Nothing here imports
the transform side. A check that does not apply records N/A with a reason; it is never dropped.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .. import telemetry as diag
from .reader import VPackage, W
from .textcheck import blocks, diff_blocks, accept_view, reject_view, view_texts
from .props import check_properties
from .explain import explain_record
from . import xsd as _xsd

GEOM_TOL_PT = 0.5


@dataclass
class CheckResult:
    id: str
    name: str
    status: str                  # PASS | FAIL | NA
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationRecord:
    source: str
    output: str
    job: str
    mode: str
    predicted_diffs: List[Dict[str, Any]]
    predicted_parts: List[str]
    predicted_rev_delta: Dict[str, int]
    checks: List[CheckResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    predicted_members_removed: List[str] = field(default_factory=list)
    predicted_members_added: List[str] = field(default_factory=list)
    property_assertions: List[Dict[str, Any]] = field(default_factory=list)
    geometry_reference: str = "source"

    @property
    def passed(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    def get(self, cid: str) -> Optional[CheckResult]:
        for c in self.checks:
            if c.id == cid:
                return c
        return None

    def set(self, result: CheckResult) -> None:
        for i, c in enumerate(self.checks):
            if c.id == result.id:
                self.checks[i] = result
                return
        self.checks.append(result)

    def failures(self) -> List[tuple]:
        """``(id, name, summary, [specifics…])`` for every failed check — which part, block and text, what was
        predicted and what was found (``explain.failure_details``)."""
        return explain_record(self.checks, self)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passed"] = self.passed
        render = self.get("V6")
        d["verification_coverage"] = {
            "performed_checks": [c.id for c in self.checks if c.status in ("PASS", "FAIL")],
            "not_applicable_checks": [c.id for c in self.checks if c.status == "NA"],
            "renderer": render.data.get("renderer") if render is not None else None,
            "human_visual_acceptance": "not_recorded_by_this_suite",
            "pass_meaning": "no performed automated check failed; NA checks were not evaluated",
        }
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "VerificationRecord":
        rec = cls(source=d["source"], output=d["output"], job=d["job"], mode=d["mode"],
                  predicted_diffs=d.get("predicted_diffs", []), predicted_parts=d.get("predicted_parts", []),
                  predicted_rev_delta=d.get("predicted_rev_delta", {}), notes=d.get("notes", []),
                  flags=d.get("flags", []), predicted_members_removed=d.get("predicted_members_removed", []),
                  predicted_members_added=d.get("predicted_members_added", []),
                  property_assertions=d.get("property_assertions", []),
                  geometry_reference=d.get("geometry_reference", "source"))
        rec.checks = [CheckResult(**c) for c in d.get("checks", [])]
        return rec

    def to_markdown(self) -> str:
        lines = [f"VERIFICATION RECORD — {Path(self.output).name}",
                 f"Source:   {self.source}",
                 f"Output:   {self.output}",
                 f"Mode:     {self.mode}    Job: {self.job}",
                 f"Predicted part changes: {sorted(self.predicted_parts)}",
                 f"Predicted revision delta: ins {self.predicted_rev_delta.get('ins', 0):+d}, del {self.predicted_rev_delta.get('del', 0):+d}"
                 + "".join(f", {k} {v:+d}" for k, v in sorted(self.predicted_rev_delta.items()) if k not in ("ins", "del") and v),
                 f"Predicted member removals: {self.predicted_members_removed or 'none'}; additions: {self.predicted_members_added or 'none'}",
                 f"Property assertions (V10): " + (", ".join(f"{a.get('kind')}" + (f" [#{a['op_index'] + 1} {a.get('op')}]" if 'op_index' in a else '')
                                                            for a in self.property_assertions) or "none — no property change predicted"),
                 f"Geometry reference (V7): {self.geometry_reference}"]
        if not self.passed:
            lines += ["", "WHY IT FAILED (each failed check, in specifics; the checks below have the numbers)"]
            for cid, name, _summary, details in self.failures():
                lines.append(f"  {cid} {name}")
                lines += [f"    - {x}" for x in details]
        lines += ["", "Predicted diff list (written before the transform ran):"]
        if not self.predicted_diffs:
            lines.append("  (none — zero legitimate text diffs)")
        for i, d in enumerate(self.predicted_diffs, 1):
            b = "∅" if d.get("before") is None else repr(d["before"])
            a = "∅" if d.get("after") is None else repr(d["after"])
            lines.append(f"  D{i}. {d['part']} / block {d['block_index']}: {b} → {a}")
            if d.get("note"):
                lines.append(f"       ({d['note']})")
        lines.append("")
        for c in self.checks:
            lines.append(f"{c.id} {c.name:.<34} {c.status}")
            lines.append(f"    {c.summary}")
        lines.append("")
        lines.append(f"RESULT: {'PASS' if self.passed else 'FAIL'}")
        performed = [c.id for c in self.checks if c.status in ("PASS", "FAIL")]
        na = [c.id for c in self.checks if c.status == "NA"]
        lines.append(f"Automated checks performed ({len(performed)}): {', '.join(performed) or 'none'}. "
                     f"Not evaluated / not applicable ({len(na)}): {', '.join(na) or 'none'}.")
        lines.append("PASS means no performed automated check failed; NA is not evidence of success. "
                     "Human visual acceptance is not recorded by this suite. Render evidence applies to the "
                     "recorded renderer and does not establish another renderer's appearance.")
        if self.notes:
            lines.append("")
            lines.append("Judgment calls:")
            lines += [f"  - {n}" for n in self.notes]
        if self.flags:
            lines.append("")
            lines.append("Flagged for review:")
            lines += [f"  - {f}" for f in self.flags]
        return "\n".join(lines)


# ---- helpers ------------------------------------------------------------------------------------

CONTENT_REV = ("ins", "del", "moveFrom", "moveTo")
OTHER_REV = ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange", "tblGridChange",
             "numberingChange", "cellIns", "cellDel", "cellMerge")
PROPERTY_REV = ("rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange", "tblGridChange")


def structure_only(d: Dict[str, Any]) -> bool:
    """A predicted block diff that declares a change of structural children only (built-in batch 2: a comment's
    reference mark removed, a hidden tab removed): the block's text is the same before and after, ``struct_after``
    says the counts. V2 leaves it out of the expected text changes; V3 accepts it for that block only."""
    return (d.get("before") is not None and d.get("before") == d.get("after")
            and d.get("struct_after") is not None)


def _rev_kind(el) -> Optional[str]:
    if not isinstance(el.tag, str) or not el.tag.startswith(W):
        return None
    local = el.tag[len(W):]
    if local in CONTENT_REV:
        parent = el.getparent()
        return local + "_mark" if parent is not None and parent.tag == W + "rPr" else local
    if local in OTHER_REV:
        return local
    return None


def _rev_elements(root) -> List[tuple]:
    """(kind, w:id) for every revision element."""
    out = []
    for el in root.iter():
        k = _rev_kind(el)
        if k:
            out.append((k, el.get(W + "id")))
    return out


def _total_key(v: Any) -> tuple:
    """A sort key for every value the suite sorts, so a sort never raises: None (no block on that side of a V2 change,
    a revision element without a w:id) first, then text, then tuples element by element, then anything else by type
    name and repr. It decides only the ORDER: list equality still compares the items themselves, so two sorted lists
    are equal only when they hold the same items, and the same items of None, text and tuples of them always sort
    the same way — a comparison of sorted lists is exactly as strict as before (it used to raise TypeError on None
    beside text, e.g. an inserted and a changed block in one part)."""
    if v is None:
        return (0,)
    if isinstance(v, str):
        return (1, v)
    if isinstance(v, tuple):
        return (2, tuple(_total_key(x) for x in v))
    return (3, type(v).__name__, repr(v))


def _rev_ids(root) -> Dict[str, List[str]]:
    out = {"ins": [], "del": []}
    for tag in ("ins", "del"):
        for el in root.iter(W + tag):
            if el.getparent() is not None and el.getparent().tag == W + "rPr":
                continue  # paragraph-mark revision markers, not content wrappers
            out[tag].append(el.get(W + "id"))
    return out


def _grammar_errors(root, part: str) -> List[str]:
    errs = []
    for t in root.iter(W + "t"):
        q = t.getparent()
        while q is not None:
            if q.tag == W + "del":
                errs.append(f"{part}: w:t inside w:del (must be w:delText)")
                break
            q = q.getparent()
    for dt in root.iter(W + "delText"):
        q = dt.getparent()
        ok = False
        while q is not None:
            if q.tag == W + "del":
                ok = True
                break
            q = q.getparent()
        if not ok:
            errs.append(f"{part}: w:delText outside w:del")
    for tag in ("ins", "del"):
        for el in root.iter(W + tag):
            if el.getparent() is not None and el.getparent().tag == W + "rPr":
                continue
            if el.get(W + "id") is None or el.get(W + "author") is None:
                errs.append(f"{part}: w:{tag} missing w:id or w:author")
    for t in root.iter(W + "t", W + "delText"):
        txt = t.text or ""
        if txt != txt.strip() and t.get("{http://www.w3.org/XML/1998/namespace}space") != "preserve":
            errs.append(f"{part}: edge whitespace without xml:space=preserve: {txt!r}")
    return errs


def _property_change_grammar(root, part: str) -> List[str]:
    """Property-change revisions: id + author, and exactly one snapshot child of the right kind. Judged against
    the source's own baseline (Word itself omits w:id on some pPrChange elements)."""
    errs = []
    snapshot = {"rPrChange": "rPr", "pPrChange": "pPr", "sectPrChange": "sectPr", "tblPrChange": "tblPr",
                "trPrChange": "trPr", "tcPrChange": "tcPr"}
    for local, child in snapshot.items():
        for el in root.iter(W + local):
            where = f"{part}: w:{local} (id {el.get(W + 'id')})"
            if el.get(W + "id") is None or el.get(W + "author") is None:
                errs.append(f"{where} missing w:id or w:author")
            kids = [c for c in el if isinstance(c.tag, str)]
            if len(kids) != 1 or kids[0].tag != W + child:
                errs.append(f"{where} must hold exactly one w:{child} snapshot")
    return errs


# ---- the suite ----------------------------------------------------------------------------------

@contextmanager
def _check(rec: VerificationRecord, check_id: str, name: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    """Trace real check work without copying document text or changing the verification record.

    A completed evaluation can return FAIL or NA; an exception is an ERROR and is
    re-raised by the span with its traceback. V2/V3 share their traversal, so both
    spans cover that work and identify it explicitly as shared.
    """
    with diag.scope(check_id=check_id, check_name=name), diag.span("verification.check", **fields) as outcome:
        outcome["outcome"] = "ERROR"
        yield outcome
        result = rec.get(check_id)
        outcome["outcome"] = result.status if result is not None else "UNRECORDED"


def run_suite(source: bytes, output: bytes, plan: Dict[str, Any], source_name: str, output_name: str) -> VerificationRecord:
    with diag.span("verification.suite", source_bytes=len(source), output_bytes=len(output)) as outcome:
        rec = _run_suite(source, output, plan, source_name, output_name)
        outcome.update(outcome="PASS" if rec.passed else "FAIL", check_count=len(rec.checks),
                       failed_checks=[c.id for c in rec.checks if c.status == "FAIL"],
                       not_applicable_checks=[c.id for c in rec.checks if c.status == "NA"])
        return rec


def _run_suite(source: bytes, output: bytes, plan: Dict[str, Any], source_name: str, output_name: str) -> VerificationRecord:
    with diag.span("verification.open_packages") as opening:
        src, out = VPackage(source), VPackage(output)
        opening.update(source_members=len(src.names), output_members=len(out.names))
    rec = VerificationRecord(source=source_name, output=output_name, job=plan.get("job", "?"),
                             mode=plan.get("mode", "?"), predicted_diffs=plan.get("diffs", []),
                             predicted_parts=list(plan.get("intended_parts", [])),
                             predicted_rev_delta=dict(plan.get("rev_delta", {"ins": 0, "del": 0})),
                             notes=list(plan.get("notes", [])), flags=list(plan.get("flags", [])),
                             predicted_members_removed=list(plan.get("members_removed", [])),
                             predicted_members_added=list(plan.get("members_added", [])),
                             property_assertions=[{k: v for k, v in a.items() if k in ("kind", "op", "op_index")}
                                                  for a in plan.get("property_assertions", [])],
                             geometry_reference=plan.get("geometry_reference", "source"))
    intended = set(plan.get("intended_parts", []))
    tracked_content = plan.get("mode") == "content"
    untracked_expected = bool(plan.get("untracked_diffs_expected"))
    pred_removed = list(plan.get("members_removed", []))
    pred_added = list(plan.get("members_added", []))

    # V1 — package hash whitelist, predicted member removals/additions, member order, package integrity
    with _check(rec, "V1", "Package hash whitelist") as check:
        hb, ha = src.hashes(), out.hashes()
        added = sorted(set(ha) - set(hb))
        removed = sorted(set(hb) - set(ha))
        changed = sorted(n for n in hb if n in ha and hb[n] != ha[n])
        expected_order = [n for n in src.names if n not in set(pred_removed)] + pred_added
        order_ok = out.names == expected_order
        order_detail = None
        if not order_ok:
            k = next((i for i, (x, y) in enumerate(zip(out.names, expected_order)) if x != y),
                     min(len(out.names), len(expected_order)))
            order_detail = {"position": k, "expected": expected_order[k] if k < len(expected_order) else None,
                            "found": out.names[k] if k < len(out.names) else None}
        unexpected = sorted(set(changed) - intended)
        members_ok = added == sorted(pred_added) and removed == sorted(set(pred_removed))
        new_problems = out.integrity_problems() - src.integrity_problems()
        def _rels_of(n):
            d, b = n.rsplit("/", 1) if "/" in n else ("", n)
            return (d + "/" if d else "") + "_rels/" + b + ".rels"
        touched = [n for n in out.names if n.endswith(".xml") and (n not in hb or hb[n] != ha.get(n)
                   or hb.get(_rels_of(n)) != ha.get(_rels_of(n)))]
        for n in touched:
            new_problems |= out.rid_problems(n) - src.rid_problems(n)
        new_problems = sorted(new_problems)
        src_refd, out_refd = src.referenced_members(), out.referenced_members()
        orphaned = sorted(n for n in out.names if n in src_refd and n not in out_refd)
        v1_ok = members_ok and not unexpected and order_ok and not new_problems and not orphaned
        rec.set(CheckResult("V1", "Package hash whitelist", "PASS" if v1_ok else "FAIL",
                            f"changed {changed} ⊆ intended {sorted(intended)}: {'yes' if not unexpected else 'NO ' + str(unexpected)}; "
                            f"added {added or 'none'} (predicted {pred_added or 'none'}); "
                            f"removed {removed or 'none'} (predicted {sorted(pred_removed) or 'none'})"
                            + ("" if members_ok else " — MEMBER SET MISMATCH")
                            + f"; member order {'kept' if order_ok else 'CHANGED'}"
                            + (f"; NEW integrity problems {new_problems[:3]}" if new_problems else "; no new dangling rels/overrides/untyped members")
                            + (f"; ORPHANED (still present, no longer referenced) {orphaned[:5]}" if orphaned else ""),
                            {"changed": changed, "added": added, "removed": removed, "unexpected": unexpected,
                             "integrity": new_problems, "orphaned": orphaned, "order": order_detail}))
        check.update(changed_parts=len(changed), added_parts=len(added), removed_parts=len(removed),
                     unexpected_parts=len(unexpected), integrity_problems=len(new_problems), orphaned_members=len(orphaned))

    # V2/V3 — text preservation and structural children over the UNION of source and output text parts
    with _check(rec, "V2", "Text preservation", shared_computation="V2/V3") as text_check, \
            _check(rec, "V3", "Structural children", shared_computation="V2/V3") as structure_check:
        src_tp, out_tp = src.text_parts(), out.text_parts()
        scope = sorted(p for p in set(src_tp) | set(out_tp) if p.endswith(".xml"))
        parts = [p for p in scope if p in src_tp and p in out_tp]
        actual_text_changes = []
        struct_changes = []
        struct_only_seen = set()           # (part, block index) of every structure-only declaration a real change matched
        n_blocks = 0
        src_xml = {p: src.xml(p) for p in src_tp}
        out_xml = {p: out.xml(p) for p in out_tp}
        for part in scope:
            b_before = blocks(src_xml[part]) if part in src_xml else []
            b_after = blocks(out_xml[part]) if part in out_xml else []
            n_blocks += len(b_before)
            mine = [d for d in plan.get("diffs", []) if d.get("part") == part and isinstance(d.get("block_index"), int)
                    and not isinstance(d.get("block_index"), bool)]
            deleted_hint = {d["block_index"] for d in mine if d.get("before") is not None and d.get("after") is None}
            inserted_hint = {d["block_index"] for d in mine if d.get("before") is None and d.get("after") is not None}
            for ch in diff_blocks(b_before, b_after, deleted_hint, inserted_hint):
                if ch.before != ch.after:
                    actual_text_changes.append({"part": part, "before": ch.before, "after": ch.after,
                                                "before_index": ch.before_index, "after_index": ch.after_index})
                if ch.before is not None and ch.after is not None and ch.struct_before != ch.struct_after:
                    # a structure-only declaration (before == after) counts for its own block only (source index); one
                    # beside a text change keeps the older text match
                    declared = [d for d in plan.get("diffs", []) if d["part"] == part and d.get("before") == ch.before
                                and d.get("after") == ch.after and d.get("struct_after") is not None
                                and {k: v for k, v in d["struct_after"].items() if v} == ch.struct_after
                                and (not structure_only(d) or d.get("block_index") == ch.before_index)]
                    if not declared:
                        struct_changes.append({"part": part, "block": ch.before_index,
                                               "before": ch.struct_before, "after": ch.struct_after,
                                               "text": (ch.before or "")[:160]})
                    struct_only_seen |= {(part, d.get("block_index")) for d in declared if structure_only(d)}
        # a structure-only declaration is a prediction like any other: the change it declares must have happened
        for d in plan.get("diffs", []):
            if structure_only(d) and (d.get("part"), d.get("block_index")) not in struct_only_seen:
                struct_changes.append({"part": d.get("part"), "block": d.get("block_index"),
                                       "declared": {k: v for k, v in (d.get("struct_after") or {}).items() if v},
                                       "text": (d.get("before") or "")[:160],
                                       "problem": "declared structure change not found in the output"})
        # the same multisets as ever, sorted with a total key: a part whose changes mix an inserted or removed block (None
        # on one side) with a changed one sorts instead of raising
        # a structure-only declaration (V3's business) is not a text change
        expected = sorted(((d["part"], d.get("before"), d.get("after")) for d in plan.get("diffs", [])
                           if not structure_only(d)), key=_total_key)
        actual = sorted(((c["part"], c["before"], c["after"]) for c in actual_text_changes), key=_total_key)
        v2_ok = expected == actual
        missing = [e for e in expected if e not in actual]
        extra = [a for a in actual if a not in expected]
        gone_parts = [p for p in scope if p not in out_tp]
        new_parts = [p for p in scope if p not in src_tp]
        rec.set(CheckResult("V2", "Text preservation", "PASS" if v2_ok else "FAIL",
                            f"blocks compared: {n_blocks} across {len(scope)} part(s)"
                            + (f" (no longer text parts: {gone_parts})" if gone_parts else "")
                            + (f" (new text parts: {new_parts})" if new_parts else "")
                            + f"; diffs found: {len(actual)}; "
                            f"matches predicted list exactly: {'yes' if v2_ok else 'NO'}"
                            + ("" if v2_ok else f" — missing {len(missing)}, unpredicted {len(extra)}"),
                            {"parts": scope, "actual": actual_text_changes, "missing": missing, "unpredicted": extra}))
        rec.set(CheckResult("V3", "Structural children", "PASS" if not struct_changes else "FAIL",
                            "w:tab(in w:r)/w:br/w:cr/noBreakHyphen/sym/fldChar/… per block: "
                            + ("identical on every aligned block, or changed exactly as the plan declared" if not struct_changes
                               else f"{len(struct_changes)} block(s) differ from the plan's declarations (changed without "
                                    f"one, or declared and not changed)"),
                            {"changes": struct_changes}))
        text_check.update(parts=len(scope), blocks_compared=n_blocks, differences=len(actual),
                          missing_differences=len(missing), unpredicted_differences=len(extra))
        structure_check.update(parts=len(scope), blocks_compared=n_blocks, unpredicted_changes=len(struct_changes))

    # V4 — revision accounting, every kind: source text parts before, output text parts after
    with _check(rec, "V4", "Revision accounting") as check:
        cnt_b: Dict[str, int] = {}
        cnt_a: Dict[str, int] = {}
        ids_b, ids_a = set(), set()
        for part, root in src_xml.items():
            for k, i in _rev_elements(root):
                cnt_b[k] = cnt_b.get(k, 0) + 1
            r = _rev_ids(root)
            ids_b |= {(part, i) for i in r["ins"] + r["del"]}
        out_elems = []
        for part, root in out_xml.items():
            for k, i in _rev_elements(root):
                cnt_a[k] = cnt_a.get(k, 0) + 1
                out_elems.append((part, k, i))
            r = _rev_ids(root)
            ids_a |= {(part, i) for i in r["ins"] + r["del"]}
        src_elems = {(part, k, i) for part, root in src_xml.items() for k, i in _rev_elements(root)}
        pred = plan.get("rev_delta", {"ins": 0, "del": 0})
        kinds = sorted(set(cnt_b) | set(cnt_a) | set(pred), key=lambda k: (k not in ("ins", "del"), k))
        delta = {k: cnt_a.get(k, 0) - cnt_b.get(k, 0) for k in kinds}
        wrong = {k: (delta[k], pred.get(k, 0)) for k in kinds if delta[k] != pred.get(k, 0)}
        d_ins, d_del = delta.get("ins", 0), delta.get("del", 0)
        ins_b, del_b, ins_a, del_a = cnt_b.get("ins", 0), cnt_b.get("del", 0), cnt_a.get("ins", 0), cnt_a.get("del", 0)
        all_ids = []
        for part in out_xml:
            r = _rev_ids(out_xml[part])
            all_ids += r["ins"] + r["del"]
        dup = sorted({i for i in all_ids if all_ids.count(i) > 1}, key=_total_key)   # an id may be None (no w:id)
        id_count: Dict[str, int] = {}
        for _, _, i in out_elems:
            if i is not None:
                id_count[i] = id_count.get(i, 0) + 1
        new_elems = [e for e in out_elems if e not in src_elems]
        collide = sorted({i for _, _, i in new_elems if i is not None and id_count[i] > 1})
        # Every revision element of every kind in every text part: an id used more often in the output than in the
        # source is a duplicate the transform introduced. (A split run whose two halves both keep the original
        # w:rPrChange id hides from `collide`, because that (part, kind, id) already existed in the source.)
        src_id_count: Dict[str, int] = {}
        for part, root in src_xml.items():
            for _, i in _rev_elements(root):
                if i is not None:
                    src_id_count[i] = src_id_count.get(i, 0) + 1
        dup_all = sorted(i for i, n in id_count.items() if n > 1 and n > src_id_count.get(i, 0))
        others = [f"{k} {cnt_b.get(k, 0)}→{cnt_a.get(k, 0)} (Δ{delta[k]:+d}, predicted {pred.get(k, 0):+d})"
                  for k in kinds if k not in ("ins", "del") and (delta[k] or pred.get(k, 0))]
        rec.set(CheckResult("V4", "Revision accounting",
                            "PASS" if (not wrong and not dup and not collide and not dup_all) else "FAIL",
                            f"w:ins {ins_b}→{ins_a} (Δ{d_ins:+d}, predicted {pred.get('ins', 0):+d}); "
                            f"w:del {del_b}→{del_a} (Δ{d_del:+d}, predicted {pred.get('del', 0):+d})"
                            + (f"; {'; '.join(others)}" if others else "")
                            + (f"; UNPREDICTED deltas {wrong}" if wrong else "")
                            + (f"; DUPLICATE ids {sorted(set(dup) | set(dup_all), key=_total_key)[:5]}"
                               if (dup or dup_all) else "; ids unique")
                            + (f"; NEW revision ids collide {collide[:5]}" if collide else ""),
                            {"ins_before": ins_b, "ins_after": ins_a, "del_before": del_b, "del_after": del_a,
                             "counts_before": cnt_b, "counts_after": cnt_a, "unpredicted": wrong,
                             "duplicate_ids": dup, "duplicate_ids_all_kinds": dup_all, "colliding_new_ids": collide}))
        check.update(revisions_before=sum(cnt_b.values()), revisions_after=sum(cnt_a.values()),
                     unpredicted_deltas=len(wrong), duplicate_ids=len(set(dup) | set(dup_all)),
                     colliding_new_ids=len(collide))

    # V5a — XSD against the source baseline (changed and added members)
    with _check(rec, "V5a", "XSD vs source baseline") as check:
        xsd_parts = sorted(set(changed) | set(added) | {"[Content_Types].xml"} | {n for n in changed if n.endswith(".rels")})
        new_errors: Dict[str, List[str]] = {}
        baseline: Dict[str, int] = {}
        skipped = []
        tbase = plan.get("template_baselines", {}) or {}
        for part in xsd_parts:
            if part not in out.names:
                continue
            before = src.read(part) if part in src.names else None
            if before is None and part in tbase:
                tpath, tpart = tbase[part]
                try:
                    before = VPackage(Path(tpath).read_bytes()).read(tpart)
                except (OSError, KeyError) as e:
                    new_errors[part] = [f"template baseline unreadable: {e}"]
                    continue
            base, outp, new = _xsd.compare(part, before, out.read(part))
            if outp is None:
                skipped.append(part)
                continue
            baseline[part] = len(base or [])
            if new:
                new_errors[part] = sorted(new)[:5]
        rec.set(CheckResult("V5a", "XSD vs source baseline", "PASS" if not new_errors else "FAIL",
                            f"validated {len(baseline)} changed part(s); baseline errors {baseline or '{}'}; new errors: "
                            + ("0" if not new_errors else json.dumps(new_errors)[:600])
                            + (f"; no schema for {skipped}" if skipped else ""),
                            {"baseline": baseline, "new_errors": new_errors, "skipped": skipped}))
        check.update(validated_parts=len(baseline), baseline_errors=sum(baseline.values()),
                     parts_with_new_errors=len(new_errors), skipped_parts=len(skipped))

    # V5b — redlining / untracked-edit check, V5c grammar
    with _check(rec, "V5b", "Untracked-edit check", tracked_content=tracked_content) as check:
        if tracked_content:
            rej_diffs = []
            for part in parts:
                a = view_texts(reject_view(src_xml[part]), "reject")
                b = view_texts(reject_view(out_xml[part]), "reject")
                if a != b:
                    for i, (x, y) in enumerate(zip(a, b)):
                        if x != y:
                            rej_diffs.append({"part": part, "block": i, "source": x, "output": y})
                    if len(a) != len(b):
                        rej_diffs.append({"part": part, "block_count": [len(a), len(b)]})
            one_sided = [p for p in scope if p not in parts]
            rec.set(CheckResult("V5b", "Untracked-edit check (reject view)", "PASS" if not rej_diffs else "FAIL",
                                "reject-all view of output == reject-all view of source, char-for-char: "
                                + ("yes" if not rej_diffs else f"NO — {len(rej_diffs)} difference(s)")
                                + (f"; parts present on one side only {one_sided} are accounted for by V1/V2" if one_sided else ""),
                                {"differences": rej_diffs[:20]}))
            check.update(parts_compared=len(parts), reject_mismatches=len(rej_diffs))
        else:
            new_ids = sorted(ids_a - ids_b, key=_total_key)               # (part, id); an id may be None
            prop_new = sum(1 for _, k, _ in new_elems if k in PROPERTY_REV)
            prop_pred = sum(pred.get(k, 0) for k in PROPERTY_REV if pred.get(k, 0) > 0)
            ok = not new_ids and v2_ok and prop_new == prop_pred
            rec.set(CheckResult("V5b", "Untracked-edit check (enumerated-list mode)", "PASS" if ok else "FAIL",
                                ("no revision added" if not new_ids else f"UNEXPECTED new revisions {new_ids[:5]}")
                                + "; V2's enumerated list is the complete account of text changes"
                                + ("" if untracked_expected else " (formatting job: zero text diffs expected)")
                                + (f"; {prop_new} tracked property change(s) added (predicted {prop_pred})" if (prop_new or prop_pred) else ""),
                                {"new_revision_ids": new_ids, "new_property_changes": prop_new,
                                 "predicted_property_changes": prop_pred, "v2_ok": v2_ok}))
            check.update(new_revision_ids=len(new_ids), new_property_changes=prop_new,
                         predicted_property_changes=prop_pred, text_preserved=v2_ok)

    with _check(rec, "V5c", "Revision grammar") as check:
        grammar_set = set()
        for part in out_xml:
            grammar_set |= set(_grammar_errors(out_xml[part], part))
        for part in src_xml:
            grammar_set -= set(_grammar_errors(src_xml[part], part))
        grammar = sorted(grammar_set)
        prop_grammar = set()
        for part in out_xml:
            prop_grammar |= set(_property_change_grammar(out_xml[part], part))
        for part in src_xml:
            prop_grammar -= set(_property_change_grammar(src_xml[part], part))
        grammar += sorted(prop_grammar)
        rec.set(CheckResult("V5c", "Revision grammar", "PASS" if not grammar else "FAIL",
                            "w:delText only inside w:del, no w:t inside w:del, id+author on every wrapper, "
                            "xml:space on edge whitespace, property-change snapshots well-formed: "
                            + ("ok" if not grammar else f"{len(grammar)} error(s): {grammar[:3]}"),
                            {"errors": grammar[:50]}))
        check["new_errors"] = len(grammar)

    # V8 — accept / reject simulation
    with _check(rec, "V8", "Accept / reject simulation", tracked_content=tracked_content) as check:
        if tracked_content:
            acc_bad = []
            changed_blocks = {(d["part"], d["block_index"]): d.get("accepted_view") for d in plan.get("diffs", [])
                              if d.get("before") is not None and d.get("accepted_view") is not None}
            changed_blocks.update({(d["part"], d["block_index"]): d["accepted_view"] for d in plan.get("accepted_views", [])})
            inserted_blocks: Dict[str, List[tuple]] = {}
            for d in plan.get("diffs", []):
                if d.get("before") is None and d.get("after") is not None:
                    inserted_blocks.setdefault(d["part"], []).append(
                        (int(d["block_index"]), d.get("accepted_view") if d.get("accepted_view") is not None else d["after"])
                    )
            for part in parts:
                a_src = [b.text for b in blocks(accept_view(src_xml[part]))]
                a_out = [b.text for b in blocks(accept_view(out_xml[part]))]
                expected_acc = [changed_blocks.get((part, i), t) if (part, i) in changed_blocks else t
                                for i, t in enumerate(a_src)]
                for out_idx, acc_txt in sorted(inserted_blocks.get(part, []), key=lambda x: x[0]):
                    expected_acc.insert(out_idx, acc_txt)
                if expected_acc != a_out:
                    for i, (x, y) in enumerate(zip(expected_acc, a_out)):
                        if x != y:
                            acc_bad.append({"part": part, "block": i, "expected": x, "actual": y})
                    if len(expected_acc) != len(a_out):
                        acc_bad.append({"part": part, "block_count": [len(expected_acc), len(a_out)]})
            rej_ok = rec.get("V5b").status == "PASS"
            rec.set(CheckResult("V8", "Accept / reject simulation", "PASS" if (not acc_bad and rej_ok) else "FAIL",
                                f"accept-all == predicted accepted view: {'yes' if not acc_bad else 'NO (' + str(len(acc_bad)) + ')'}; "
                                f"reject-all == original: {'yes' if rej_ok else 'NO'}; oracle: XML simulation"
                                + ("" if not acc_bad else f"; first: {json.dumps(acc_bad[0])[:300]}"),
                                {"accept_mismatches": acc_bad[:20]}))
            check.update(parts_compared=len(parts), accept_mismatches=len(acc_bad), reject_matches=rej_ok)
        else:
            prop_note = ("; tracked property changes are checked by V10 (snapshot == source properties)"
                         if any(pred.get(k, 0) for k in PROPERTY_REV) else "")
            rec.set(CheckResult("V8", "Accept / reject simulation", "NA",
                                "N/A — no tracked revisions added by this job (V4 delta is the account)" + prop_note))
            check["reason"] = "no_tracked_content"

    # V10 — property assertions + residual identity (verify/props.py)
    with _check(rec, "V10", "Property assertions", assertion_count=len(plan.get("property_assertions", []))):
        try:
            with diag.span("verification.properties"):
                ok10, summary10, data10 = check_properties(src, out, plan, source_name, output_name)
        except Exception as e:  # a check that cannot run is a failure, never a skip
            ok10, summary10, data10 = False, f"could not evaluate: {type(e).__name__}: {e}", {}
        rec.set(CheckResult("V10", "Property assertions", "PASS" if ok10 else "FAIL", summary10, data10))

    # V6, V7, V9 are filled in by the batch runner (they need renders / the write-back step)
    rec.set(CheckResult("V6", "Automated render comparison", "NA", "no renderer run yet"))
    rec.set(CheckResult("V7", "Geometry assertion", "NA", "no renderer run yet"))
    rec.set(CheckResult("V9", "Overwrite guard", "NA", "not delivered yet"))
    for check_id, reason in (("V6", "render_pending"), ("V7", "render_pending"), ("V9", "delivery_pending")):
        diag.event("verification.check.deferred", check_id=check_id, outcome="NA", reason=reason)
    return rec
