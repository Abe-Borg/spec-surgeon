"""Operation interface — the composable unit of the natural-language editor (docs/NL_SURGICAL_EDITOR_PLAN.md §4).

An op is two functions over a package:

    plan(pkg, select, params) -> PlanFragment   predict-first: everything the verifier will assert, computed
                                                analytically from the SOURCE before any transform runs
    apply(work, fragment)     -> None           the deterministic transform; mutates a working copy of the
                                                package (``DocxPackage.copy()``) through its overlay API
                                                (write / remove_member / add_member / edit_rels)

Fragments from several ops merge into one ``Plan`` (``merge_fragments``) that the independent verifier reads.
Two ops that would touch the same thing are a conflict and raise ``JobException`` (code ``conflict``) — the
engine never guesses an order for overlapping edits.

A plan that cannot be made deterministically raises ``JobException`` (→ Tier 2), never guesses.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence, Tuple


class JobException(Exception):
    """The op/job cannot proceed deterministically on this file. Carries a structured diagnosis so the
    exception can be routed to a human or an agent with everything they need."""

    def __init__(self, reason: str, code: str = "exception", details: Optional[dict] = None):
        super().__init__(reason)
        self.reason = reason
        self.code = code
        self.details = details or {}

    def to_dict(self):
        return {"code": self.code, "reason": self.reason, "details": self.details}


OpException = JobException


@dataclass
class BlockDiff:
    """One predicted text difference in one block (paragraph) of one part."""
    part: str
    block_index: int            # index of the w:p among all w:p of the part, in document order
    before: Optional[str]       # None = block inserted
    after: Optional[str]        # None = block deleted
    accepted_view: Optional[str] = None   # text after accept-all (content jobs)
    rejected_view: Optional[str] = None   # text after reject-all (should equal source reject view)
    struct_after: Optional[Dict[str, int]] = None  # V3: structural-children counts after, if they change
    note: str = ""

    def to_dict(self):
        return asdict(self)


GEOMETRY_REFERENCES = ("source", "template", "none")


@dataclass
class Plan:
    """What the verifier asserts. v0.1.0 fields first; the NL-editor fields default to "nothing", so a v0.1.0
    plan dict is still a complete, valid plan for the extended verifier."""
    job: str
    mode: str                                   # formatting | content | untracked
    params: Dict[str, Any]
    intended_parts: List[str]                   # V1 whitelist (members whose bytes may change)
    diffs: List[BlockDiff] = field(default_factory=list)       # V2 enumerated list
    accepted_views: List[Dict[str, Any]] = field(default_factory=list)  # V8: {part, block_index, accepted_view} per edited block
    untracked_diffs_expected: bool = False      # V5b: True only for untracked/formatting block edits
    rev_delta: Dict[str, int] = field(default_factory=lambda: {"ins": 0, "del": 0})  # V4 (other kinds optional, default 0)
    expected_structural_delta: Dict[str, int] = field(default_factory=dict)  # V3: e.g. {"fldChar": -2}
    geometry_pages_expected_to_change: List[int] = field(default_factory=list)  # V7 exclusions (1-based)
    notes: List[str] = field(default_factory=list)             # judgment calls for the report
    flags: List[str] = field(default_factory=list)             # things flagged for human review
    transform_data: Dict[str, Any] = field(default_factory=dict)  # opaque, for apply()
    # ---- NL-editor plan fields (§5 of the plan) ----
    members_removed: List[str] = field(default_factory=list)   # V1: exact set of zip members that must disappear
    members_added: List[str] = field(default_factory=list)     # V1: exact members that must appear, in this order, appended
    property_assertions: List[Dict[str, Any]] = field(default_factory=list)  # V10: declarative property claims
    geometry_reference: str = "source"          # V7: source | template | none
    geometry_reason: str = ""                   # V7: required when geometry_reference == "none"
    geometry_template: Optional[str] = None     # V7: template file when geometry_reference == "template"
    ops: List[Dict[str, Any]] = field(default_factory=list)    # per-op summary when the plan merges several ops
    template_baselines: Dict[str, List[str]] = field(default_factory=dict)  # V5a: added member → [template file, its member]
    output_name: Optional[str] = None          # the output file's name when an op names it (name_output, make_values_agree)

    def to_dict(self):
        d = asdict(self)
        d["diffs"] = [x.to_dict() if isinstance(x, BlockDiff) else x for x in self.diffs]
        return d


Touch = Tuple[str, str, str]   # (member, target, aspect) — see PlanFragment and check_conflicts for the grammar
VERIFY = "verify"              # the aspect of a read: the op's prediction depends on the target as it is in the SOURCE


def verify_family(aspect: str) -> Tuple[bool, Optional[str]]:
    """(is a read, the property family it depends on — None for every family) of a touch's aspect:
    ``verify`` → (True, None); ``verify:pgMar`` → (True, "pgMar"); ``rPr`` → (False, None)."""
    if aspect == VERIFY:
        return True, None
    if aspect.startswith(VERIFY + ":"):
        return True, aspect[len(VERIFY) + 1:] or None      # a bare "verify:" reads every family (never less)
    return False, None


@dataclass
class PlanFragment(Plan):
    """One op's contribution to a plan: every Plan field, plus the bookkeeping the merge needs.

    ``touches`` lists what the op will modify — and what its prediction READS — as (member, target, aspect):
      * target "*" means the whole member (removed, added, or blocks added/removed in it);
      * otherwise a block index (``"12"``), ``"sectPr:N"``, ``"style:<id>"``, ``"docDefaults"`` …;
      * aspect says which property family: ``text`` (run structure / characters), ``rPr`` (run properties),
        ``markRPr`` (paragraph-mark run properties), ``pPr``, ``hfref``, ``pgMar``, ``pgSz``, ``pgNumType``,
        ``titlePg``, ``remove``, ``add``, ``rels``, ``ct`` …;
      * a **read** has the aspect ``verify`` (or ``verify:<family>``): the op's prediction is computed from the
        target as it is in the SOURCE (rule 2 of CLAUDE.md: predict first, analytically), so another op changing it
        in the same spec would reach that prediction unverified — a plan-time ``conflict`` (rule 9), never a V10
        failure after apply or, worse, a pass over an edit the prediction never saw.
          - ``(member, "scope", "verify")``: the op's V10 assertion re-resolves its selection over that whole
            member (every part in a font or paragraph op's scope, whether or not it changes anything there): the
            member must still exist, unchanged by anyone else as a whole, when the verifier runs; other ops may
            change things inside it (their real touches decide);
          - ``(member, "*", "verify")``: the prediction depends on the whole member — ``apply_header_footer_template``
            computes its pins from the target's ``styles.xml`` and theme part; any change inside the member by
            another op (a style's font, docDefaults, a style's properties) is a conflict;
          - ``(member, target, "verify:<family>")``: the prediction depends on that property family of that target —
            the templating op's tab rescale and distance alignment read ``pgSz``/``pgMar`` of every section's
            sectPr, and ``fix_page_numbering``'s cover-unnumbered reads every section's ``titlePg`` to know which
            footer the cover shows; a change of that family (or ``text``) of that target by another op is a
            conflict, a change of another family is not; ``(member, target, "verify")`` depends on every family.
        Reads never conflict with reads, and an op's own change of what it reads is not a conflict.
    ``rev_ids_needed`` is how many new revision ids (w:ins/w:del/w:rPrChange/w:pPrChange …) apply() consumes;
    the merge hands each fragment a disjoint range starting at ``transform_data["first_rev_id"]``.
    """
    op: str = ""
    op_index: int = 0
    select: Dict[str, Any] = field(default_factory=dict)
    touches: List[Touch] = field(default_factory=list)
    rev_ids_needed: int = 0
    rev_id_base: int = 1

    def to_dict(self):
        d = super().to_dict()
        d.pop("touches", None)          # can be one entry per block; bookkeeping, not a prediction
        return d


class Op:
    """Base class. Subclasses set the class attributes and implement plan() and apply()."""
    name: str = "base"
    mode: str = "formatting"            # formatting | content | untracked (may be refined per plan)
    description: str = ""
    params_schema: Dict[str, Any] = {}
    select_schema: Dict[str, Any] = {}
    # which checks this op's plan feeds, and how (documentation + the future op catalog)
    verification_profile: Dict[str, str] = {}

    def plan(self, pkg, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:  # pragma: no cover
        raise NotImplementedError

    def apply(self, pkg, fragment: PlanFragment) -> None:  # pragma: no cover
        raise NotImplementedError

    def fragment(self, select: Optional[Dict[str, Any]], params: Dict[str, Any], mode: Optional[str] = None) -> PlanFragment:
        return PlanFragment(job=self.name, mode=mode or self.mode, params=dict(params), intended_parts=[],
                            op=self.name, select=dict(select or {}))


# ---- merge ------------------------------------------------------------------------------------------

def _label(f: PlanFragment) -> str:
    return f"#{f.op_index + 1} {f.op}"


def check_conflicts(fragments: Sequence[PlanFragment]) -> None:
    """Raise JobException(code="conflict") when two ops would touch the same thing.

    Rules (conservative on purpose — an order the engine had to guess is an order nobody verified):
      1. a whole-member CHANGE (target "*", any aspect but a read) by one op conflicts with ANY touch of that
         member by another op — a ``verify`` touch included: an op whose assertion covers a member another op
         removes (``set_font`` over the headers plus ``remove_header_footer``, say) is refused here, not left for
         V10 to fail on; a whole-member READ (``(member, "*", "verify")``) conflicts with any CHANGE of that member
         by another op, whole or inside it (``apply_header_footer_template``'s pins read ``styles.xml`` whole, so a
         ``set_style_font`` in the same spec is refused here, not left for V10 to fail on after apply);
      2. the same (member, target) touched by two ops conflicts when the aspects are equal, or either is "text", or
         one is a read of the other's family — ``verify`` (every family) or ``verify:<family>`` (that one; a change
         of ``text`` restructures the target and conflicts with any read of it). Two reads never conflict
         (verifying is not changing), and an op's own change of what it reads is not a conflict;
      3. two ops predicting text diffs for the same (part, block) conflict (defence in depth for rule 2).
    """
    whole: Dict[str, List[Tuple[int, str]]] = {}
    by_target: Dict[Tuple[str, str], List[Tuple[int, str]]] = {}
    any_touch: Dict[str, List[int]] = {}
    changes: Dict[str, List[int]] = {}                  # member → ops with a non-read touch of it (any target)
    for f in fragments:
        for member, target, aspect in f.touches:
            any_touch.setdefault(member, []).append(f.op_index)
            if not verify_family(aspect)[0]:
                changes.setdefault(member, []).append(f.op_index)
            if target == "*":
                whole.setdefault(member, []).append((f.op_index, aspect))
            else:
                by_target.setdefault((member, str(target)), []).append((f.op_index, aspect))
    names = {f.op_index: _label(f) for f in fragments}
    for member, owners in whole.items():
        changers = sorted({o for o, a in owners if not verify_family(a)[0]})
        readers = sorted({o for o, a in owners if verify_family(a)[0]} - set(changers))
        if changers:
            others = sorted({i for i in any_touch.get(member, []) if i not in changers})
            if others or len(changers) > 1:
                who = sorted(set(changers) | set(others))
                verifiers = sorted({f.op_index for f in fragments if f.op_index in others
                                    and all(verify_family(a)[0] for m, t, a in f.touches if m == member)})
                hint = (f"; {[names[i] for i in verifiers]} would verify {member} without changing it — narrow its "
                        f"scope to the parts that stay" if verifiers else
                        "; split them into separate specs or narrow their selectors")
                raise JobException(
                    f"ops {[names[i] for i in who]} both touch {member} and at least one of them replaces, removes or "
                    f"restructures it as a whole" + hint,
                    "conflict", {"member": member, "ops": [names[i] for i in who]})
        for r in readers:                       # each reader on its own: another reader's change inside counts too
            changing = sorted({i for i in changes.get(member, []) if i != r})
            if changing:
                who = sorted({r} | set(changing))
                raise JobException(
                    f"ops {[names[i] for i in who]} both touch {member}: {[names[r]]} plan from it "
                    f"as it is in the source (the whole member) and {[names[i] for i in changing]} would change it "
                    f"underneath, so the change would reach that plan unverified — split out "
                    f"{[names[i] for i in changing]} into a spec of its own and run it first, then "
                    f"{[names[r]]} on its outputs",
                    "conflict", {"member": member, "ops": [names[i] for i in who],
                                 "reads": [names[r]], "changes": [names[i] for i in changing]})
    for (member, target), owners in by_target.items():
        for a in range(len(owners)):
            for b in range(a + 1, len(owners)):
                (ia, aa), (ib, ab) = owners[a], owners[b]
                (ra, fa), (rb, fb) = verify_family(aa), verify_family(ab)
                if ia == ib or (ra and rb):
                    continue
                if ra or rb:
                    (ir, fam), (ic, change) = ((ia, fa), (ib, ab)) if ra else ((ib, fb), (ia, aa))
                    if fam is None or fam == change or change == "text":
                        raise JobException(
                            f"op {names[ir]} plans from {fam or 'every property'} of {member} [{target}] as it is in "
                            f"the source and {names[ic]} would change {change} there underneath, so the change would "
                            f"reach that plan unverified — split out {names[ic]} into a spec of its own and run it "
                            f"first, then {names[ir]} on its outputs",
                            "conflict", {"member": member, "target": target, "ops": [names[ia], names[ib]],
                                         "aspects": [aa, ab], "reads": [names[ir]], "changes": [names[ic]]})
                    continue
                if aa == ab or "text" in (aa, ab):
                    raise JobException(
                        f"ops {names[ia]} and {names[ib]} both change {aa if aa == ab else aa + '/' + ab} of "
                        f"{member} [{target}]; the order would decide the result — split into separate specs",
                        "conflict", {"member": member, "target": target, "ops": [names[ia], names[ib]],
                                     "aspects": [aa, ab]})
    seen: Dict[Tuple[str, int], int] = {}
    for f in fragments:
        blocks = {(d.part, d.block_index) for d in f.diffs} | {(a["part"], a["block_index"]) for a in f.accepted_views}
        for key in blocks:
            if key in seen and seen[key] != f.op_index:
                raise JobException(f"ops {names[seen[key]]} and {names[f.op_index]} both change the text of "
                                   f"{key[0]} block {key[1]}", "conflict",
                                   {"part": key[0], "block": key[1], "ops": [names[seen[key]], names[f.op_index]]})
            seen[key] = f.op_index


def merged_mode(fragments: Sequence[PlanFragment]) -> str:
    modes = {f.mode for f in fragments}
    if "content" in modes and "untracked" in modes:
        raise JobException("a spec cannot mix tracked content ops with untracked text edits: V5b's reject-view "
                           "check would see the untracked edit as a violation; run them as two specs",
                           "conflict", {"ops": [_label(f) for f in fragments], "modes": sorted(modes)})
    if "content" in modes:
        removed = {m for f in fragments for m in f.members_removed}
        for f in fragments:
            if f.mode != "content" and any(d.part not in removed for d in f.diffs):
                raise JobException(f"{_label(f)} makes untracked text changes; combined with tracked content ops "
                                   f"they would fail V5b's reject-view check — run them as two specs", "conflict",
                                   {"op": _label(f)})
        return "content"
    if "untracked" in modes:
        return "untracked"
    return "formatting"


def assign_revision_ids(fragments: Sequence[PlanFragment]) -> List[str]:
    """Give every fragment that writes revisions a disjoint id range (ids must be unique across all parts)."""
    notes = []
    needing = [f for f in fragments if f.rev_ids_needed]
    if not needing:
        return notes
    base = max(f.rev_id_base for f in needing)
    for f in needing:
        f.transform_data["first_rev_id"] = base
        notes.append(f"{_label(f)}: revision ids {base}..{base + f.rev_ids_needed - 1}")
        base += f.rev_ids_needed
    return notes


def merge_geometry(fragments: Sequence[PlanFragment]) -> Tuple[str, str, Optional[str]]:
    refs = [f.geometry_reference for f in fragments]
    for r in refs:
        if r not in GEOMETRY_REFERENCES:
            raise JobException(f"unknown geometry_reference {r!r}", "bad-params")
    if "none" in refs:
        reasons = "; ".join(f"{_label(f)}: {f.geometry_reason}" for f in fragments if f.geometry_reference == "none")
        return "none", reasons, None
    templates = {f.geometry_template for f in fragments if f.geometry_reference == "template"}
    if templates:
        if len(templates) > 1 or None in templates:
            raise JobException(f"ops reference different (or no) geometry templates: {sorted(map(str, templates))}",
                               "conflict")
        return "template", "", templates.pop()
    return "source", "", None


def merge_fragments(fragments: Sequence[PlanFragment], job: str = "edit-spec",
                    params: Optional[Dict[str, Any]] = None) -> Plan:
    """One Plan from the ordered fragments of a spec, after conflict detection. Fragments are numbered in
    spec order (op_index) and keep their own transform_data for apply()."""
    if not fragments:
        raise JobException("nothing to merge: the spec has no ops", "bad-params")
    for i, f in enumerate(fragments):
        f.op_index = i
    check_conflicts(fragments)
    mode = merged_mode(fragments)
    id_notes = assign_revision_ids(fragments)
    geo, reason, template = merge_geometry(fragments)
    multi = len(fragments) > 1
    plan = Plan(job=job, mode=mode, params=dict(params or {}), intended_parts=[])
    for f in fragments:
        tag = (_label(f) + ": ") if multi else ""
        for p in f.intended_parts:
            if p not in plan.intended_parts:
                plan.intended_parts.append(p)
        plan.diffs.extend(f.diffs)
        plan.accepted_views.extend(f.accepted_views)
        plan.untracked_diffs_expected = plan.untracked_diffs_expected or f.untracked_diffs_expected
        for k, v in f.rev_delta.items():
            plan.rev_delta[k] = plan.rev_delta.get(k, 0) + v
        for k, v in f.expected_structural_delta.items():
            plan.expected_structural_delta[k] = plan.expected_structural_delta.get(k, 0) + v
        for pg in f.geometry_pages_expected_to_change:
            if pg not in plan.geometry_pages_expected_to_change:
                plan.geometry_pages_expected_to_change.append(pg)
        plan.notes.extend(tag + n for n in f.notes)
        plan.flags.extend(tag + n for n in f.flags)
        for m in f.members_removed:
            if m not in plan.members_removed:
                plan.members_removed.append(m)
        for m in f.members_added:
            if m in plan.members_added:
                raise JobException(f"two ops add {m}", "conflict")
            plan.members_added.append(m)
        for a in f.property_assertions:
            plan.property_assertions.append({**a, "op": f.op, "op_index": f.op_index})
        plan.template_baselines.update(f.template_baselines)
        if f.output_name is not None:
            if plan.output_name is not None:
                raise JobException(f"two ops name the output file ({plan.output_name!r} and {f.output_name!r})",
                                   "conflict")
            plan.output_name = f.output_name
        plan.ops.append({"index": f.op_index, "op": f.op, "mode": f.mode, "select": f.select, "params": f.params,
                         "intended_parts": list(f.intended_parts), "diffs": len(f.diffs),
                         "rev_delta": dict(f.rev_delta), "members_removed": list(f.members_removed),
                         "members_added": list(f.members_added),
                         "property_assertions": len(f.property_assertions),
                         "geometry_reference": f.geometry_reference})
    plan.geometry_reference, plan.geometry_reason, plan.geometry_template = geo, reason, template
    plan.notes.extend(id_notes)
    plan.transform_data = {"fragments": [{"op": f.op, "op_index": f.op_index} for f in fragments]}
    return plan
