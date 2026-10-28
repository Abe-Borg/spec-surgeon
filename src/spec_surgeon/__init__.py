"""Spec Surgeon — surgical edits to .docx files with independent verification records.

Architecture (see README):
  Tier 1  deterministic engine (this package): fingerprint → plan → apply → verify → render → guard
  Tier 2  natural-language requests compiled by Claude into EditSpecs (compiler, conversation), exceptions
          routed to a human or to recovery
  Tier 3  new operations developed with the docx-surgical-edit skill, then promoted into ops/

Rule enforced by CLAUDE.md: ``spec_surgeon.verify`` shares no helper code with ``spec_surgeon.ops``
or ``spec_surgeon.ooxml`` — the verifier reads only the before/after packages, so a bug in the
transform cannot hide behind the same bug in the check.
"""

from pathlib import Path as _Path

__version__ = "1.14.0"

# The docx-surgical-edit method the engine implements, shipped inside the package (read it before changing any op or check).
SKILL_DIR = _Path(__file__).resolve().parent / "skill"
