"""Job interface (v0.1.0). Since the op library (ops/) a job is a thin alias over one op, so the v0.1.0 CLI,
``run_batch(job_name, params)`` and the library API keep working. A job is two functions over a package:

    plan(pkg, params)  -> Plan      predict-first: everything the verifier will assert, computed
                                    from the SOURCE before any transform runs
    apply(pkg, plan)   -> {part: bytes}   the deterministic transform, producing substituted parts

A plan that cannot be made deterministically raises JobException (→ Tier 2), never guesses.
"""
from __future__ import annotations

from typing import Any, Dict

from ..ops import get_op
from ..ops.base import BlockDiff, JobException, Plan, assign_revision_ids  # moved to ops/base.py; re-exported here
from ..package import DocxPackage


class Job:
    name: str = "base"
    mode: str = "formatting"
    description: str = ""
    params_schema: Dict[str, Any] = {}

    def plan(self, pkg: DocxPackage, params: Dict[str, Any]) -> Plan:  # pragma: no cover
        raise NotImplementedError

    def apply(self, pkg: DocxPackage, plan: Plan) -> Dict[str, bytes]:  # pragma: no cover
        raise NotImplementedError


class OpJob(Job):
    """A v0.1.0 job backed by one op: ``plan`` returns the op's PlanFragment (a Plan subclass, so every v0.1.0
    field is there) and ``apply`` runs the op on a working copy and returns the substituted parts."""
    op_name: str = ""

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        if cls.op_name:
            from ..ops import OPS
            op = OPS[cls.op_name]
            cls.mode = op.mode
            cls.description = op.description
            cls.params_schema = op.params_schema

    def plan(self, pkg: DocxPackage, params: Dict[str, Any]) -> Plan:
        select = {"scope": params["scope"]} if "scope" in params else {}
        frag = get_op(self.op_name).plan(pkg, select, params)
        frag.job = self.name
        assign_revision_ids([frag])
        return frag

    def apply(self, pkg: DocxPackage, plan: Plan) -> Dict[str, bytes]:
        work = pkg.copy()
        get_op(self.op_name).apply(work, plan)
        if work.removed or work.added:
            raise JobException("this op adds or removes package members, which the v0.1.0 job interface cannot "
                               "express; run it through an EditSpec (spec-surgeon run-spec)", "interface")
        return work.substitutions()
