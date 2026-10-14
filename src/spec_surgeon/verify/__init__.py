"""Independent verifier. Imports nothing from spec_surgeon.jobs or spec_surgeon.ooxml (see CLAUDE.md)."""
from .suite import run_suite, VerificationRecord, CheckResult, GEOM_TOL_PT

__all__ = ["run_suite", "VerificationRecord", "CheckResult", "GEOM_TOL_PT"]
