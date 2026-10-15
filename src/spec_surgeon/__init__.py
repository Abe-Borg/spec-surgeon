"""Spec Surgeon — fast, provably lossless surgical edits to .docx files.

Architecture (see README):
  Tier 1  deterministic engine (this package): fingerprint → plan → apply → verify → render → guard
  Tier 2  exceptions routed to a human or an agent (spec_surgeon.agent)
  Tier 3  new job types developed with the docx-surgical-edit skill, then promoted into jobs/

Rule enforced by CLAUDE.md: ``spec_surgeon.verify`` shares no helper code with ``spec_surgeon.jobs``
or ``spec_surgeon.ooxml`` — the verifier reads only the before/after packages, so a bug in the
transform cannot hide behind the same bug in the check.
"""

__version__ = "0.4.1"
