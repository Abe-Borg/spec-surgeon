"""Tier 2/3 helpers — optional, need the ``anthropic`` package and ANTHROPIC_API_KEY.

* ``ask``   — natural-language entry: turn "apply X to these" into a job + params JSON the user confirms.
* ``advise`` — exception handling: give the agent the diagnosis, the fingerprint, the relevant XML
              excerpt and the skill's contract, and get back a written recommendation. The agent does
              not edit files in this prototype; it tells a human what it would do and why.

The skill folder shipped inside the package (spec_surgeon/skill) is the agent's method.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from .jobs import REGISTRY

SKILL_DIR = Path(__file__).resolve().parent / "skill"
DEFAULT_MODEL = os.environ.get("SPEC_SURGEON_MODEL", "claude-opus-5-5")


def _client():
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install anthropic (and set ANTHROPIC_API_KEY) to use the agent features") from e
    return anthropic.Anthropic()


def _skill_text(*names: str) -> str:
    out = []
    for n in names:
        p = SKILL_DIR / n
        if p.exists():
            out.append(f"\n\n===== {n} =====\n" + p.read_text(encoding="utf-8"))
    return "".join(out)


def job_catalog() -> str:
    return json.dumps({name: {"description": cls.description, "mode": cls.mode, "params": cls.params_schema}
                       for name, cls in REGISTRY.items()}, indent=2)


def ask(request: str, model: str = DEFAULT_MODEL) -> Dict[str, Any]:
    """Return {"job": ..., "params": {...}, "confidence": ..., "questions": [...]} for the user to confirm."""
    client = _client()
    prompt = (
        "You translate a user's request about editing Word (.docx) specification files into ONE job call for "
        "the Spec Surgeon engine. Output ONLY a JSON object with keys: job (one of the catalog names, or null if "
        "no job fits), params (object matching that job's schema), confidence (0-1), questions (array of "
        "strings — anything you must ask before running; empty if none). Never invent replacement wording: if the "
        "user did not give the exact replacement text, put a question instead.\n\nJOB CATALOG:\n"
        + job_catalog() + "\n\nUSER REQUEST:\n" + request
    )
    msg = client.messages.create(model=model, max_tokens=800, messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def advise(exception_json: Path, model: str = DEFAULT_MODEL, max_tokens: int = 1500) -> str:
    """Written recommendation for one exception, grounded in the skill's method."""
    client = _client()
    exc = json.loads(Path(exception_json).read_text(encoding="utf-8"))
    excerpt = ""
    src = Path(exc.get("source", ""))
    details = exc.get("exception", {}).get("details", {})
    if src.exists() and details.get("part") is not None:
        try:
            from .package import DocxPackage
            from .ooxml import paragraphs
            from lxml import etree
            pkg = DocxPackage.from_path(src)
            root = pkg.xml(details["part"])
            p = paragraphs(root)[details.get("paragraph", 0)]
            excerpt = etree.tostring(p, pretty_print=True, encoding="unicode")[:6000]
        except Exception as e:  # pragma: no cover
            excerpt = f"(could not extract paragraph XML: {e})"
    method = _skill_text("SKILL.md", "references/verification-checklist.md", "references/ooxml-lessons.md")
    prompt = (
        "You are advising on ONE exception raised by a deterministic .docx editing engine that follows the method "
        "below. The engine refused to guess. Explain (1) what the structure is, (2) whether the job can be done "
        "safely and how — exact XML-level steps, (3) what the predicted diff list and revision delta would be, "
        "(4) what to verify, (5) whether a human must decide something first and the ONE question to ask. "
        "Be concrete and brief. Do not invent document wording.\n\n"
        f"EXCEPTION:\n{json.dumps(exc, indent=2)[:8000]}\n\nTARGET PARAGRAPH XML (if located):\n{excerpt}\n\nMETHOD:{method}"
    )
    msg = client.messages.create(model=model, max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
