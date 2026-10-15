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
  ``spec-mode``, ``geometry``, ``conflict``, ``bad-params``, ``unknown-slot``, ``slot-overlap``, ``template-slot``)
  are always compile errors; every other refusal (``unfilled-slot``, ``expect``, ``not-found``, ``spans-runs``, ``tracked-format``,
  ``section-break`` … — anything a particular file's structure can cause) is accepted as long as at least one
  pattern in the batch plans cleanly, allowing Pass 1 to process the clean majority and set aside the outliers
  for the next pass; a spec that plans on no file at all (or a batch with no files) is rejected.
A rejected spec goes back to the model once with the errors; a second rejection fails the compile.

The client is injectable (``client=``) so the loop is tested without the API; the default needs the
``anthropic`` package and credentials (``ANTHROPIC_API_KEY`` or an ``ant auth login`` profile).
"""
from __future__ import annotations

import json
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
from .profile import profile, profile_batch, template_spec_summary

from . import llm
from .llm import DEFAULT_MODEL, FALLBACK_BETA  # noqa: F401 — re-exported (cli, codegen, advisor, tests)

MAX_TURNS = 12
# template-slot: a reference fill rule finds no usable value in the template spec itself — the same for every file
FATAL_SPEC_CODES = {"spec", "spec-mode", "geometry", "conflict", "bad-params", "unknown-slot", "slot-overlap",
                    "template-slot"}
# The refusals that most often differ from one structure pattern to the next. Documentation only: _dry_errors treats
# EVERY non-fatal code as pattern-sensitive, so a refusal the ops add later can never slip through the gate.
PATTERN_SENSITIVE_CODES = {"unfilled-slot", "expect", "not-found", "ambiguous"}
SPEC_LEVEL_CODES = FATAL_SPEC_CODES | {"unfilled-slot", "expect"}

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
    {"name": "read_template", "description": "The template file the user attached: its profile, and what it offers "
     "as a template (template_spec: prepared slots, or — an ordinary spec from another firm — its own section "
     "number/title and where its headers/footers show them; its body text font/size).",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "ask_user", "description": "Ask the user before anything runs; their reply comes back as this tool's "
     "result. Use it when the request is ambiguous, needs words the user has not given, or no operation does exactly "
     "what was asked (then say what you understood, what is missing, and offer choices).",
     "input_schema": {"type": "object", "properties": {
         "understanding": {"type": "string", "description": "one sentence: what you understood the user wants"},
         "message": {"type": "string", "description": "what you need to tell the user (e.g. what no operation can "
                                                      "do, and why) — plain words, no XML"},
         "questions": {"type": "array", "items": {"type": "string"}},
         "choices": {"type": "array", "items": {"type": "object", "properties": {
             "label": {"type": "string", "description": "what picking this means, in plain words; for an "
                                                       "alternative, say exactly how it differs from what was asked"},
             "kind": {"type": "string", "enum": ["clarify", "alternative", "rephrase"]},
             "wording": {"type": "string", "description": "ONLY when picking this choice would place words in the "
                                                         "documents: those exact words (the user's pick approves "
                                                         "them; nothing else does)"},
             "detail": {"type": "string"}}, "required": ["label", "kind"]}}}}},
    {"name": "submit_editspec", "description": "Submit the EditSpec. It is validated and dry-planned; errors come "
     "back to fix. A spec that says 'unsupported' must also judge custom_transform.",
     "input_schema": {"type": "object", "properties": {
         "spec": {"type": "object"},
         "custom_transform": {"type": "string", "enum": ["possible", "outside_limits"],
                              "description": "for an unsupported spec only: could a one-off custom transform do it "
                                             "within the catalog's custom_transform_limits?"},
         "custom_transform_reason": {"type": "string"}}, "required": ["spec"]}},
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
- When no op can express the request exactly, do not give up and never silently approximate. If a clarification \
could make it expressible, or a built-in op could do something close, call ask_user with "understanding" (one \
sentence: what you understood), "message" (what no op can do, and why, in plain words) and "choices": kind \
"clarify" for a likely meaning an op can do; kind "alternative" for the closest built-in action, its label saying \
exactly how it differs from what was asked. A choice that would place words in the documents carries those exact \
words in "wording" — the user's pick approves them; your own text never does. When nothing fits even after the \
user's answers, submit {"request", "summary", "unsupported": "<why no op fits>", "proposal": "<what a one-off \
transform would do>"} with no ops, and beside the spec set custom_transform to "possible" only when a one-off \
transform could do it within the catalog's custom_transform_limits, else "outside_limits" with custom_transform_reason.
- The user may reply after your spec was previewed: that reply is a correction, and the app's notes before it say \
what the preview found. Submit a revised spec (or ask). The user's replies are approved words like the request.
- Two ops must not touch the same thing. "Change all fonts" is three ops (set_font on runs with a direct font, \
set_style_font, set_doc_defaults_font) plus an effective_font assertion.
- The attached template may be a TEMPLATE SPEC: an ordinary spec from the architect or another firm (read_template \
says which). "Make the header/footer match the template spec" is apply_header_footer_template with slot_markers \
["reference"] and the catalog's reference_fills as slot_fills (adapt them when the profiles show another layout): \
its own section number and title are replaced by each spec's own and everything else is copied. "Match the \
template's font" follows the catalog rule: font and size from the template spec's body_text, size_from from the \
batch's body_text sizes. State the template's values and the sizes in assumptions.
- mode: "formatting" unless an op places characters; "content" for tracked wording changes; "untracked" when \
the user asked for untracked wording or a template has literal fills.
- Finish each turn by calling submit_editspec once with the whole spec, or ask_user. Keep tool calls few.
"""

CHAT_NOTE = """
- This is a conversation: plain text without a tool call is shown to the user as your message and waits for their \
reply.
"""


CHAT_TOOLS = list(TOOLS)            # the conversation's tools (Phase C adds view_pages); the advisor never gets these


def system_prompt(chat: bool = False) -> str:
    """The system prompt — built once per conversation and never changed during it."""
    return (SYSTEM + (CHAT_NOTE if chat else "")
            + "\nOP CATALOG (ops, selector grammar, common assumptions, custom_transform_limits, the EditSpec JSON schema, "
            "worked examples):\n"
            + json.dumps(op_catalog(), ensure_ascii=False))


@dataclass
class CompileResult:
    status: str                                   # ok | questions | unsupported | failed
    spec: Optional[Dict[str, Any]] = None
    questions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    model: str = DEFAULT_MODEL
    usage: Dict[str, int] = field(default_factory=dict)
    served_models: List[str] = field(default_factory=list)   # what actually answered (a refusal fallback may differ)
    custom_transform: str = ""                    # unsupported only: "possible" | "outside_limits" | "" (not judged)
    custom_transform_reason: str = ""

    def to_dict(self):
        return {"status": self.status, "spec": self.spec, "questions": self.questions, "errors": self.errors,
                "tool_calls": self.tool_calls, "model": self.model, "served_models": self.served_models,
                "usage": self.usage, "custom_transform": self.custom_transform,
                "custom_transform_reason": self.custom_transform_reason}


def _client():
    """The Claude client every caller resolves at call time (tests replace this name with a scripted fake)."""
    return llm.client()


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


def memory_source(memory: Dict[str, Any]) -> str:
    """PROJECT.json as a source of approved words — without the path it was read from (a folder name is not wording
    the user approved for the documents)."""
    return json.dumps({k: v for k, v in (memory or {}).items() if k != "_path"}, ensure_ascii=False)


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
    """The words the spec would place that appear verbatim (whitespace-normalised) in none of the sources. Each source
    is searched on its own: a phrase may not be stitched together from the end of one and the start of the next."""
    hay = [_norm(s) for s in sources if s]
    return [w for w in placed_words(spec) if not any(_norm(w) in h for h in hay)]


class _Batch:
    """The batch as the tools see it: files by name, profiles by pattern."""

    def __init__(self, files: Sequence[Path], template: Optional[Path]):
        self.files = {Path(f).name: Path(f) for f in files}
        self.prof = profile_batch([Path(f) for f in files])
        self.template = Path(template) if template else None
        self._pkgs: Dict[str, DocxPackage] = {}
        self.work_dir: Optional[Path] = None      # where a conversation keeps its page renders (chat only)

    def pkg(self, name: str) -> DocxPackage:
        if name not in self.files:
            raise KeyError(f"no file {name!r} in this batch; files: {sorted(self.files)[:12]}")
        if name not in self._pkgs:
            self._pkgs[name] = DocxPackage.from_path(self.files[name])
        return self._pkgs[name]

    def representatives(self) -> List[Path]:
        return [Path(p["paths"][0]) for p in self.prof["patterns"]]

    def pattern_paths(self, pat: Dict[str, Any]) -> List[Path]:
        """The files of one profiled pattern, as paths: the name map (``files``) cannot tell two files of one name in
        different folders apart, so anything that visits every file of a pattern goes by path."""
        return [Path(x) for x in pat.get("paths") or [self.files[n] for n in pat["files"] if n in self.files]]

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
        pkg = DocxPackage.from_path(self.template)
        return {"path": str(self.template), "template_spec": template_spec_summary(pkg), "profile": profile(pkg)}


def _dry_errors(spec: Dict[str, Any], batch: _Batch) -> List[str]:
    """Spec-level failures of a dry plan on one file per pattern.

    True schema/contract mistakes (FATAL_SPEC_CODES) always reject the spec. Every other refusal is treated as
    pattern-sensitive (a particular file's structure caused it) and rejects the spec ONLY when zero patterns in
    the batch plan cleanly — in a mixed batch where at least one pattern plans cleanly, minority pattern refusals
    are left for the preview and set-aside sieve rather than blocking Pass 1. A spec that plans on no file at all
    is rejected with every file's refusal, so the model can fix the spec instead of the batch setting every file
    aside; a batch with no files is rejected outright.
    """
    if not batch.representatives():
        return ["the batch has no .docx files to plan on"]
    fatal_errs: List[str] = []
    pattern_errs: List[str] = []
    planned_ok = 0
    for pat in batch.prof["patterns"]:
        # The representative is one file of the pattern; a refusal that depends on the file's content (the phrase
        # is in the second file, not the first) must not reject a spec that plans on another file of the same
        # structure — try the pattern's other files before saying no file of it plans.
        tried: List[str] = []
        planned = False
        for f in batch.pattern_paths(pat):
            try:
                pkg = DocxPackage.from_path(f)
                plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                planned = True
                break
            except SpecError as e:
                return [f"[{e.code}] {e.reason}"]
            except JobException as e:
                if e.code in FATAL_SPEC_CODES:
                    fatal_errs.append(f"{f.name}: [{e.code}] {e.reason}")
                    break
                tried.append(f"{f.name}: [{e.code}] {e.reason}")
            except Exception as e:  # noqa: BLE001 — an unreadable file is not the spec's fault; try the next one
                tried.append(f"{f.name}: cannot be read ({type(e).__name__}: {e})")
        if planned:
            planned_ok += 1
        elif tried:
            pattern_errs.append(tried[0] + (f" (none of this pattern's {len(tried)} files plans; e.g. {tried[1]})"
                                            if len(tried) > 1 else ""))
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
        parts.append(f"A TEMPLATE FILE IS ATTACHED: {batch.template} — a prepared header/footer template or a "
                     f"template spec (e.g. the architect's); call read_template to see which, and put this exact path "
                     f"in the op's 'template' param")
    parts.append("THE BATCH (profiles per structure pattern):\n" + json.dumps(batch.summary(), ensure_ascii=False))
    return [{"role": "user", "content": "\n\n".join(parts)}]


def compile_request(request: str, files: Sequence[Path], template: Optional[Path] = None,
                    answers: Sequence[str] = (), model: Optional[str] = None, client=None,
                    memory: Optional[Dict[str, Any]] = None, log: Optional[Callable[[str], None]] = None,
                    prior_failures: Optional[Sequence[Dict[str, Any]]] = None) -> CompileResult:
    """Request + batch → CompileResult, in one go (the command line without a terminal, the tests). ``status`` is
    ``ok`` (a validated, dry-planned spec), ``questions`` (ask the user, then call again with ``answers``),
    ``unsupported`` (no op fits; see spec["proposal"]) or ``failed``. The loop itself is conversation.Conversation."""
    from .conversation import Conversation
    conv = Conversation(request, files, template=template, client=client, model=model, memory=memory,
                        prior_failures=prior_failures, answers=answers, mode="compile", log=log)
    return conv.compile_result(conv.start())
