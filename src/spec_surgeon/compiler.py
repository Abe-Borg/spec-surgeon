"""The compiler — a plain-English request → an EditSpec, once per batch (docs/NL_SURGICAL_EDITOR_PLAN.md §6,
Appendix B; Phase 4).

Claude reads the request, the batch's per-pattern profiles (profile.py), the op catalog (catalog.py), the
template's profile when one is attached, project memory (``PROJECT.json`` beside the specs), and any prior pass's
set-aside failure diagnostics, and answers by calling ``submit_editspec`` — or ``ask_user`` when the request is
ambiguous. It can look closer with read-only tools (``inspect_profile``, ``list_styles``, ``sample_paragraphs``,
``show_part_text``, ``read_template``). It never edits a file and never sees XML; the engine plans, applies and
verifies the spec per file.

A submitted spec is checked before it is accepted:
* ``validate_spec`` (unknown keys/ops/params, mode, assertions) — open ``questions`` or ``unsupported`` are
  outcomes, not errors;
* **traceable words**: every character an op would place (``replace_text.replace``, ``insert_paragraph.text``, a
  ``literal`` template fill) must appear verbatim in the request, the user's answers or project memory — otherwise
  the model must ask;
* a **dry plan** on one representative file per structure pattern: fatal spec-level failures (``spec``,
  ``spec-mode``, ``geometry``, ``conflict``, ``bad-params``, ``unknown-slot``, ``slot-overlap``) are always compile
  errors; every other refusal (``unfilled-slot``, ``expect``, ``not-found``, ``spans-runs``, ``tracked-format``,
  ``section-break`` … — anything a particular file's structure can cause) is accepted as long as at least one
  pattern in the batch plans cleanly, allowing Pass 1 to process the clean majority and set aside the outliers
  for the next pass; a spec that plans on no file at all (or a batch with no files) is rejected.
A rejected spec goes back to the model once with the errors; a second rejection fails the compile.

The client is injectable (``client=``) so the loop is tested without the API; the default needs the
``anthropic`` package and credentials (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .catalog import op_catalog
from .editspec import SpecError, plan_spec, validate_spec
from .fingerprint import fingerprint
from .ops import JobException
from .ooxml import paragraphs
from .ops.selectors import StyleSheet
from .ops.slots import display_text, nearest_p
from .package import W, DocxPackage
from .profile import profile, profile_batch

DEFAULT_MODEL = os.environ.get("SPEC_SURGEON_MODEL", "claude-opus-5-5")
MAX_TURNS = 12
FATAL_SPEC_CODES = {"spec", "spec-mode", "geometry", "conflict", "bad-params", "unknown-slot", "slot-overlap"}
# The refusals that most often differ from one structure pattern to the next. Documentation only: _dry_errors treats
# EVERY non-fatal code as pattern-sensitive, so a refusal the ops add later can never slip through the gate.
PATTERN_SENSITIVE_CODES = {"unfilled-slot", "expect", "not-found", "ambiguous"}
SPEC_LEVEL_CODES = FATAL_SPEC_CODES | {"unfilled-slot", "expect"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

TOOLS = [
    {"name": "inspect_profile", "description": "The full profile of one structure pattern of the batch (sections, "
     "what each header/footer shows, slots, styles in use with fonts, counts, hygiene).",
     "input_schema": {"type": "object", "properties": {"pattern_id": {"type": "string"}}, "required": ["pattern_id"]}},
    {"name": "list_styles", "description": "Every style defined in one batch file: id, type, name, basedOn, and the "
     "fonts/size it sets directly.",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string", "description": "file name from the profiles"}},
                      "required": ["file"]}},
    {"name": "sample_paragraphs", "description": "Body paragraphs of one file (display text), optionally only those of "
     "a paragraph style and/or matching a Python regex.",
     "input_schema": {"type": "object", "properties": {
         "file": {"type": "string"}, "style": {"type": "string"}, "regex": {"type": "string"},
         "n": {"type": "integer", "minimum": 1, "maximum": 40}}, "required": ["file"]}},
    {"name": "show_part_text", "description": "The paragraph texts of one part of one file (e.g. word/footer2.xml).",
     "input_schema": {"type": "object", "properties": {"file": {"type": "string"}, "part": {"type": "string"}},
                      "required": ["file", "part"]}},
    {"name": "read_template", "description": "The profile of the template file the user attached (with its slots).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ask_user", "description": "Ask the user before anything runs (ends the compile; the user's answers "
     "come back with the request). Use it when the request is ambiguous or needs words the user has not given.",
     "input_schema": {"type": "object", "properties": {"questions": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
                      "required": ["questions"]}},
    {"name": "submit_editspec", "description": "Submit the EditSpec (the final answer). It is validated and dry-planned; "
     "errors come back once to fix.",
     "input_schema": {"type": "object", "properties": {"spec": {"type": "object"}}, "required": ["spec"]}},
]

SYSTEM = """You compile a user's request about editing Word (.docx) specification files into an EditSpec — a JSON \
program over a fixed library of deterministic operations. A separate engine plans, applies and verifies the spec on \
every file; you never edit files and you never see XML.

The contract you must keep (these files go to clients; silent loss or invented text is unacceptable):
- Formatting edits place zero characters. Content edits place only words the user gave verbatim (or a template's \
own words). Never invent, reword, or "improve" document text; if the user did not give the exact words, ask.
- Every interpretation you make goes into "assumptions" (the user sees them before anything runs). When the request \
is ambiguous in a way an assumption cannot honestly cover, call ask_user instead of guessing.
- Use only ops, selector keys and params from the catalog; use only part names, style ids and section indices that \
appear in the profiles or tool results.
- If no op can express the request, submit {"request", "summary", "unsupported": "<why no op fits>", \
"proposal": "<what operation would do it>"} with no ops. Never approximate the request with an op that does \
something else.
- Two ops must not touch the same thing. "Change all fonts" is three ops (set_font on runs with a direct font, \
set_style_font, set_doc_defaults_font) plus an effective_font assertion.
- mode: "formatting" unless an op places characters; "content" for tracked wording changes; "untracked" when \
the user asked for untracked wording or a template has literal fills.
- Finish by calling submit_editspec exactly once with the whole spec (or ask_user). Keep tool calls few.

OP CATALOG (ops, selector grammar, common assumptions, the EditSpec JSON schema, worked examples):
"""


@dataclass
class CompileResult:
    status: str                                   # ok | questions | unsupported | failed
    spec: Optional[Dict[str, Any]] = None
    questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self):
        return {"status": self.status, "spec": self.spec, "questions": self.questions, "errors": self.errors,
                "tool_calls": self.tool_calls, "model": self.model, "usage": self.usage}


def _client():
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("pip install anthropic (and set ANTHROPIC_API_KEY or run `ant auth login`) to compile "
                           "natural-language requests") from e
    return anthropic.Anthropic()


def load_project_memory(inputs: Sequence[Path]) -> Dict[str, Any]:
    """PROJECT.json beside the specs (§14.4: yes) — e.g. {"stamp": "...", "author": "...", "template": "...",
    "notes": [...]}. The first one found in an input folder or a file's folder wins."""
    seen = set()
    for p in inputs:
        p = Path(p)
        d = p if p.is_dir() else p.parent
        if d in seen:
            continue
        seen.add(d)
        f = d / "PROJECT.json"
        if f.is_file():
            try:
                mem = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(mem, dict):
                    return {**mem, "_path": str(f)}
            except ValueError:
                pass
    return {}


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _extract_literal_rules(rule: Any, out: List[str]) -> None:
    if isinstance(rule, dict):
        if rule.get("rule") == "literal" and isinstance(rule.get("value"), str) and rule["value"]:
            out.append(rule["value"])
        if isinstance(rule.get("rules"), list):
            for r in rule["rules"]:
                _extract_literal_rules(r, out)
    elif isinstance(rule, list):
        for r in rule:
            _extract_literal_rules(r, out)


def placed_words(spec: Dict[str, Any]) -> List[str]:
    """Every string an op of the spec would place in a document."""
    out: List[str] = []
    for o in spec.get("ops") or []:
        params = o.get("params") or {}
        if o.get("op") == "replace_text" and isinstance(params.get("replace"), str) and params["replace"]:
            out.append(params["replace"])
        if o.get("op") == "insert_paragraph" and isinstance(params.get("text"), str) and params["text"]:
            out.append(params["text"])
        if o.get("op") == "apply_header_footer_template":
            for rule in (params.get("slot_fills") or {}).values():
                _extract_literal_rules(rule, out)
    return out


def untraceable(spec: Dict[str, Any], sources: Sequence[str]) -> List[str]:
    hay = _norm("\n".join(sources))
    return [w for w in placed_words(spec) if _norm(w) not in hay]


class _Batch:
    """The batch as the tools see it: files by name, profiles by pattern."""

    def __init__(self, files: Sequence[Path], template: Optional[Path]):
        self.files = {Path(f).name: Path(f) for f in files}
        self.prof = profile_batch([Path(f) for f in files])
        self.template = Path(template) if template else None
        self._pkgs: Dict[str, DocxPackage] = {}

    def pkg(self, name: str) -> DocxPackage:
        if name not in self.files:
            raise KeyError(f"no file {name!r} in this batch; files: {sorted(self.files)[:12]}")
        if name not in self._pkgs:
            self._pkgs[name] = DocxPackage.from_path(self.files[name])
        return self._pkgs[name]

    def representatives(self) -> List[Path]:
        return [self.files[p["files"][0]] for p in self.prof["patterns"]]

    def summary(self) -> Dict[str, Any]:
        return {"files": self.prof["files"],
                "patterns": [{"pattern_id": p["pattern_id"], "count": p["count"], "files": p["files"][:10],
                              "profile": p["profile"]} for p in self.prof["patterns"][:4]]
                + [{"pattern_id": p["pattern_id"], "count": p["count"], "files": p["files"][:5],
                    "profile": "(call inspect_profile)"} for p in self.prof["patterns"][4:]]}

    # ---- tools ----
    def inspect_profile(self, pattern_id: str):
        for p in self.prof["patterns"]:
            if p["pattern_id"] == pattern_id:
                return p
        raise KeyError(f"no pattern {pattern_id!r}")

    def list_styles(self, file: str):
        sheet = StyleSheet(self.pkg(file))
        out = []
        for sid, st in sheet.styles.items():
            e = {"id": sid, "type": st.get(W + "type", "paragraph")}
            for tag in ("name", "basedOn"):
                el = st.find(W + tag)
                if el is not None:
                    e[tag] = el.get(W + "val")
            rf, sz = st.find(f"{W}rPr/{W}rFonts"), st.find(f"{W}rPr/{W}sz")
            if rf is not None:
                e["rFonts"] = {k.split("}")[1]: v for k, v in rf.attrib.items()}
            if sz is not None:
                e["size_pt"] = int(sz.get(W + "val")) / 2
            out.append(e)
        return out[:200]

    def sample_paragraphs(self, file: str, style: Optional[str] = None, regex: Optional[str] = None, n: int = 12):
        pkg = self.pkg(file)
        sheet = StyleSheet(pkg)
        rx = re.compile(regex) if regex else None
        out = []
        for i, p in enumerate(paragraphs(pkg.xml(pkg.main_part()))):
            if nearest_p(p) is not None:
                continue
            ps = p.find(f"{W}pPr/{W}pStyle")
            sid = ps.get(W + "val") if ps is not None else sheet.defaults.get("paragraph")
            t = display_text(p)
            if style and sid != style:
                continue
            if rx and not rx.search(t):
                continue
            out.append({"index": i, "style": sid, "text": t[:200]})
            if len(out) >= min(int(n or 12), 40):
                break
        return out

    def show_part_text(self, file: str, part: str):
        pkg = self.pkg(file)
        if not pkg.has(part):
            raise KeyError(f"{file} has no part {part!r}; text parts: {pkg.text_parts()}")
        return [display_text(p)[:200] for p in paragraphs(pkg.xml(part))][:80]

    def read_template(self):
        if not self.template:
            raise KeyError("no template was attached to this request")
        return {"path": str(self.template), "profile": profile(DocxPackage.from_path(self.template))}


def _dry_errors(spec: Dict[str, Any], batch: _Batch) -> List[str]:
    """Spec-level failures of a dry plan on one file per pattern.

    True schema/contract mistakes (FATAL_SPEC_CODES) always reject the spec. Every other refusal is treated as
    pattern-sensitive (a particular file's structure caused it) and rejects the spec ONLY when zero patterns in
    the batch plan cleanly — in a mixed batch where at least one pattern plans cleanly, minority pattern refusals
    are left for the preview and set-aside sieve rather than blocking Pass 1. A spec that plans on no file at all
    is rejected with every file's refusal, so the model can fix the spec instead of the batch setting every file
    aside; a batch with no files is rejected outright.
    """
    reps = batch.representatives()
    if not reps:
        return ["the batch has no .docx files to plan on"]
    fatal_errs: List[str] = []
    pattern_errs: List[str] = []
    planned_ok = 0
    for f in reps:
        pkg = DocxPackage.from_path(f)
        try:
            plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
            planned_ok += 1
        except SpecError as e:
            fatal_errs.append(f"[{e.code}] {e.reason}")
            break
        except JobException as e:
            if e.code in FATAL_SPEC_CODES:
                fatal_errs.append(f"{f.name}: [{e.code}] {e.reason}")
            else:
                pattern_errs.append(f"{f.name}: [{e.code}] {e.reason}")
    if fatal_errs:
        return fatal_errs
    if planned_ok == 0:
        return ["the spec plans cleanly on none of the batch's structure patterns:"] + pattern_errs
    return []


def check_submission(spec: Any, batch: _Batch, sources: Sequence[str]) -> List[str]:
    """[] when the spec is acceptable (or is a clean question/unsupported outcome)."""
    if not isinstance(spec, dict):
        return ["the spec must be a JSON object"]
    if spec.get("questions") or spec.get("unsupported"):
        return []
    try:
        validate_spec(spec)
    except SpecError as e:
        return [f"[{e.code}] {e.reason}"]
    bad = untraceable(spec, sources)
    if bad:
        return [f"these words would be placed in the documents but the user never gave them verbatim: {bad}. Use "
                f"the user's exact words, or call ask_user for them."]
    return _dry_errors(spec, batch)


def build_messages(request: str, batch: _Batch, answers: Sequence[str], memory: Dict[str, Any],
                   prior_failures: Optional[Sequence[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    parts = [f"REQUEST:\n{request}"]
    if answers:
        parts.append("THE USER'S ANSWERS TO EARLIER QUESTIONS:\n" + "\n".join(f"- {a}" for a in answers))
    if memory:
        parts.append("PROJECT MEMORY (PROJECT.json):\n" + json.dumps({k: v for k, v in memory.items() if k != "_path"},
                                                                     ensure_ascii=False))
    if prior_failures:
        parts.append("PRIOR PASS SET-ASIDE DIAGNOSTICS (these files failed the previous pass for the reasons below; "
                     "inspect their structure and compile an EditSpec tailored to them):\n"
                     + json.dumps(list(prior_failures)[:20], ensure_ascii=False))
    if batch.template:
        parts.append(f"A TEMPLATE FILE IS ATTACHED: {batch.template} (use read_template; put this exact path in "
                     f"the op's 'template' param)")
    parts.append("THE BATCH (profiles per structure pattern):\n" + json.dumps(batch.summary(), ensure_ascii=False))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def compile_request(request: str, files: Sequence[Path], template: Optional[Path] = None,
                    answers: Sequence[str] = (), model: Optional[str] = None, client=None,
                    memory: Optional[Dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None,
                    prior_failures: Optional[Sequence[Dict[str, Any]]] = None) -> CompileResult:
    """Request + batch → CompileResult. ``status`` is ``ok`` (a validated, dry-planned spec), ``questions`` (ask the
    user, then call again with ``answers``), ``unsupported`` (no op fits; see spec["proposal"]) or ``failed``."""
    model = model or DEFAULT_MODEL
    log = log or (lambda m: None)
    client = client or _client()
    batch = _Batch(files, template)
    memory = memory if memory is not None else load_project_memory([Path(f) for f in files])
    sources = [request, *answers, json.dumps(memory, ensure_ascii=False)]
    system = SYSTEM + json.dumps(op_catalog(), ensure_ascii=False)
    messages = build_messages(request, batch, answers, memory, prior_failures=prior_failures)
    res = CompileResult(status="failed", model=model)
    rejections = 0
    nudged = False
    for _turn in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=model, max_tokens=16000, system=system, tools=TOOLS, messages=messages,
            output_config={"effort": "high"}, cache_control={"type": "ephemeral"},
            betas=[FALLBACK_BETA], fallbacks="default")
        u = getattr(resp, "usage", None)
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens"):
            res.usage[k] = res.usage.get(k, 0) + int(getattr(u, k, 0) or 0)
        stop = getattr(resp, "stop_reason", None)
        if stop == "refusal":
            det = getattr(resp, "stop_details", None)
            res.errors.append(f"the model declined the request ({getattr(det, 'category', None)}: "
                              f"{getattr(det, 'explanation', '')})")
            return res
        if stop == "max_tokens":
            res.errors.append("the model's answer was cut off (max_tokens)")
            return res
        messages.append({"role": "assistant", "content": resp.content})
        if stop == "pause_turn":
            continue
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            if nudged:
                res.errors.append("the model ended without submitting a spec or asking a question")
                return res
            nudged = True
            messages.append({"role": "user", "content": "Finish by calling submit_editspec (or ask_user)."})
            continue
        results = []
        final: Optional[CompileResult] = None
        for b in uses:
            name, inp = b.name, dict(b.input or {})
            res.tool_calls.append({"tool": name, "input": inp if name != "submit_editspec" else "(spec)"})
            log(f"compiler: {name}")
            if name == "ask_user":
                qs = [q for q in inp.get("questions") or [] if isinstance(q, str) and q.strip()]
                final = CompileResult("questions", questions=qs or ["(the model asked an empty question)"])
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": "asked"})
                continue
            if name == "submit_editspec":
                spec = inp.get("spec")
                errs = check_submission(spec, batch, sources)
                if not errs:
                    if spec.get("unsupported"):
                        final = CompileResult("unsupported", spec=spec)
                    elif spec.get("questions"):
                        final = CompileResult("questions", spec=spec, questions=list(spec["questions"]))
                    else:
                        final = CompileResult("ok", spec=spec)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": "accepted"})
                    continue
                rejections += 1
                res.errors += errs
                if rejections > 1:
                    res.errors.insert(0, "the spec was rejected twice")
                    return res
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": "REJECTED — fix and call submit_editspec again:\n- " + "\n- ".join(errs)})
                continue
            try:
                fn = getattr(batch, name) if name in {t["name"] for t in TOOLS} else None
                if fn is None:
                    raise KeyError(f"unknown tool {name}")
                out = fn(**inp)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)[:20000]})
            except (KeyError, TypeError, ValueError, re.error) as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": str(e)})
        if final is not None:
            final.model, final.usage, final.tool_calls = model, res.usage, res.tool_calls
            final.errors = res.errors
            return final
        messages.append({"role": "user", "content": results})
    res.errors.append(f"no spec after {MAX_TURNS} turns")
    return res
