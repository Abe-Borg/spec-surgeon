"""The advisor — what to do about a file the engine refused (docs/NL_SURGICAL_EDITOR_PLAN.md §2 "Advisor", Phase 6).

``advise(exception_record)`` reads one ``exceptions/<name>.exception.json`` written by a batch (source path, spec,
fingerprint, the structured diagnosis) and asks Claude, with the same read-only tools as the compiler over that one
file, for ONE of:

* ``submit_recommendation(text)`` — a written recommendation for a human: what the structure is, why the engine
  refused, what would make it safe (e.g. "accept the pending revision in footer2.xml first", "this section inherits
  its header — remove it on sections 0–1 together");
* ``submit_editspec(spec)`` — a re-compiled spec **for this file's structure pattern only** (``applies_to`` is
  pinned to the pattern, and ``request`` to the original spec's request — never the text Claude wrote there). It must pass the compiler's checks — validation, only words already approved, a dry plan —
  and, unlike the compiler, it must also plan on this very file without any exception; otherwise it goes back once.
  The words it may place (``compiler.untraceable``, CLAUDE.md rule 13) come from three sources, each checked on its
  own: the original request, the words the original spec places (``compiler.placed_words``: its ``replace_text``
  replacements, ``insert_paragraph`` text and literal slot fills — the user approved that spec) and the user's
  answers — never the original spec's summary, assumptions or proposal (Claude's text, not the user's), and never
  the rest of its JSON.

``advise_batch(out_dir)`` does it for every exception of a batch output folder and writes ``advice/<name>.advice.md``
(+ ``advice/<name>.spec.json`` for a re-compiled spec, and ``advice/<name>.transcript.json``: ``Advice.transcript``, the
exchange with the model, for a diagnosis). Nothing is applied: a re-compiled spec is run like any other
(``run-spec``), after a human reads the advice. An API error (``llm.is_api_error``) ends the advice as ``failed`` with
its HTTP status and request id; anything else propagates from ``advise`` (``advise_batch`` records it per file).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import llm
from .catalog import op_catalog
from .compiler import DEFAULT_MODEL, TOOLS, _Batch, _client, check_submission, placed_words
from .diagnostics import write_json
from .editspec import plan_spec
from .fingerprint import fingerprint
from .ops import JobException
from .package import DocxPackage

# An explicit allowlist: a tool added to the compiler (or its chat) never reaches the advisor by accident.
ADVISOR_READ_TOOLS = ("inspect_profile", "list_styles", "sample_paragraphs", "show_part_text")
ADVISOR_TOOLS = [t for t in TOOLS if t["name"] in ADVISOR_READ_TOOLS] + [
    {"name": "submit_recommendation", "description": "A written recommendation for a human (final answer).",
     "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "submit_editspec", "description": "A re-compiled EditSpec for this file's structure pattern (final "
     "answer). It must plan on this file without an exception; errors come back once.",
     "input_schema": {"type": "object", "properties": {"spec": {"type": "object"}, "why": {"type": "string"}},
                      "required": ["spec"]}},
]

ADVISOR_SYSTEM = """A deterministic .docx editing engine refused to edit one file (it never guesses). You advise \
on that ONE exception. Look at the file with the read-only tools, then either:
- submit_editspec: a corrected EditSpec that does what the user asked on files of this structure pattern — only \
when the request can be met exactly, using catalog ops, and placing only words the user approved: the original \
request, the words the original spec places (its replace_text replacements, insert_paragraph text and literal slot \
fills) and the user's answers — never the original spec's summary, assumptions or proposal, which are not the \
user's words (a spec placing them is rejected); or
- submit_recommendation: a short written recommendation for a human — what the structure is, why the engine \
refused, the safe way forward (including what the user must decide or fix in Word first), and the one question to \
ask if a decision is needed.
Never invent document text. Do not loosen the request to make it pass (e.g. do not drop a section the user asked \
for); recommend instead.

OP CATALOG:
"""


@dataclass
class Advice:
    source: str
    kind: str                                   # recommendation | respec | failed
    text: str = ""
    spec: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    usage: llm.Usage = field(default_factory=llm.Usage)
    transcript: List[Dict[str, Any]] = field(default_factory=list)   # the exchange with the model (diagnostics)

    def to_markdown(self) -> str:
        out = [f"# Advice — {Path(self.source).name}", "", f"**{self.kind}**", ""]
        if self.text:
            out += [self.text, ""]
        if self.spec:
            out += ["Re-compiled EditSpec (this structure pattern only; review, then `spec-surgeon run-spec`):", "",
                    "```json", json.dumps(self.spec, indent=2, ensure_ascii=False), "```"]
        if self.errors:
            out += ["", "Errors: " + "; ".join(self.errors)]
        if self.usage.calls:
            served = ", ".join(sorted(set(self.usage.served))) or self.model
            out += ["", f"Claude ({served}): {self.usage.line()}"]
        return "\n".join(out)


def _respec_errors(spec: Any, batch: _Batch, sources: Sequence[str], pkg: DocxPackage, pid: str,
                   request: str = "") -> List[str]:
    """[] when ``spec`` is an acceptable respec — which is then pinned: ``applies_to`` to this file's pattern and
    ``request`` to the ORIGINAL spec's request (``request``). Claude's own text in ``request`` never reaches
    ``advice/<name>.spec.json``: whoever runs that spec next (``run-spec``, Load saved spec, ``set_aside.json`` →
    ``retry``, the next advice) counts its request among the user's approved words."""
    errs = check_submission(spec, batch, sources)
    if errs or not isinstance(spec, dict):
        return errs
    if spec.get("questions") or spec.get("unsupported"):
        return ["a re-compiled spec must be runnable (no questions/unsupported) — submit a recommendation instead"]
    spec["request"] = request
    spec["applies_to"] = {"pattern_ids": [pid]}
    try:
        plan_spec(pkg, spec, pid)
    except JobException as e:
        return [f"on this file the spec still raises [{e.code}] {e.reason}"]
    return []


def advise(record: Dict[str, Any], client=None, model: Optional[str] = None, answers: Sequence[str] = ()) -> Advice:
    model = model or DEFAULT_MODEL
    src = Path(record["source"])
    adv = Advice(source=str(src), kind="failed", model=model)
    if not src.is_file():
        adv.errors.append(f"source file not found: {src}")
        return adv
    try:
        pkg = DocxPackage.from_path(src)
        pid = fingerprint(pkg)["pattern_id"]
        batch = _Batch([src], None)
    except Exception as e:  # noqa: BLE001 — the run set this file aside because it cannot be read; say so, no model
        exc = record.get("exception") or {}
        adv.kind = "recommendation"
        adv.text = (f"{src.name} cannot be opened as a Word document ({type(e).__name__}: {e}); the run set it aside "
                    f"for the same reason ([{exc.get('code')}] {exc.get('reason')}). Open it in Word and save it again "
                    f"as a .docx (Word Document), check that it is a Word file at all (a renamed PDF or text file "
                    f"is not), then run the next pass on it.")
        return adv
    if client is None:                    # only now: an unreadable file never needs the model (nor an API key)
        try:
            client = _client()
        except Exception as e:  # noqa: BLE001 — classified: Claude not installed is a recorded failure, a defect raises
            if not llm.is_api_error(e):
                raise
            adv.errors.append(llm.failure_text(e, "the call to the model failed"))
            return adv
    spec0 = record.get("spec") or {}
    # Only words the user approved: the original request, the words the original spec places (the user approved that
    # spec) and the user's answers — never Claude's own summary, assumptions or proposal.
    request0 = spec0.get("request", "") if isinstance(spec0.get("request", ""), str) else ""
    sources = [*placed_words(spec0), request0, *answers]
    messages: List[Dict[str, Any]] = [{"role": "user", "content": json.dumps({
        "file": src.name, "structure_pattern": pid, "exception": record.get("exception"),
        "original_spec": spec0 or {"job": record.get("job"), "params": record.get("params")},
        "user_answers": list(answers), "profile": batch.prof["patterns"][0]["profile"]}, ensure_ascii=False)}]
    system = ADVISOR_SYSTEM + json.dumps(op_catalog(examples=False), ensure_ascii=False)
    rejected = False
    adv.transcript.append({"turn": 0, "role": "user", "content": messages[0]["content"]})
    for turn in range(10):
        t = time.perf_counter()
        try:
            resp = llm.call(client, model=model, system=system, tools=ADVISOR_TOOLS, messages=messages)
        except Exception as e:  # noqa: BLE001 — classified below
            if not llm.is_api_error(e):
                raise                          # a defect propagates (advise_batch records it per file)
            adv.errors.append(llm.failure_text(e, "the call to the model failed"))
            adv.transcript.append(llm.error_entry(turn + 1, e, time.perf_counter() - t))
            return adv
        adv.usage.add(resp, model)
        adv.transcript.append(llm.response_entry(turn + 1, resp, time.perf_counter() - t))
        problem = llm.stop_problem(resp)
        if problem:
            adv.errors.append(f"model stopped: {problem}")
            return adv
        content = llm.history_content(resp)
        messages.append({"role": "assistant", "content": content})
        uses = llm.tool_uses(content)
        if not uses:
            texts = [t for t in llm.texts(content) if t.strip()]
            if texts:                                    # a plain answer counts as a recommendation
                adv.kind, adv.text = "recommendation", "\n".join(texts)
                return adv
            nudge = "Finish with submit_recommendation or submit_editspec."
            messages.append({"role": "user", "content": nudge})
            adv.transcript.append({"turn": turn + 1, "role": "user", "content": nudge})
            continue
        results = []
        for b in uses:
            inp = dict(b.input or {})
            adv.tool_calls.append({"tool": b.name})
            if b.name == "submit_recommendation":
                adv.kind, adv.text = "recommendation", str(inp.get("text", ""))
                return adv
            if b.name == "submit_editspec":
                spec = inp.get("spec")
                errs = _respec_errors(spec, batch, sources, pkg, pid, request0)
                if not errs:
                    adv.kind, adv.spec, adv.text = "respec", spec, str(inp.get("why", ""))
                    return adv
                if rejected:
                    adv.errors += errs
                    return adv
                rejected = True
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": "REJECTED:\n- " + "\n- ".join(errs)})
                continue
            try:
                if b.name not in ADVISOR_READ_TOOLS:
                    raise KeyError(f"unknown tool {b.name}")
                out = getattr(batch, b.name)(**inp)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)[:20000]})
            except Exception as e:  # noqa: BLE001 — every tool call gets an answer, or the next request is refused
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": f"{type(e).__name__}: {e}"[:2000]})
        adv.transcript.append(llm.results_entry(turn + 1, results))
        messages.append({"role": "user", "content": results})
    adv.errors.append("no advice after 10 turns")
    return adv


def advise_batch(out_dir: Path, only: Optional[str] = None, client=None, model: Optional[str] = None,
                 log=None) -> List[Advice]:
    log = log or (lambda m: None)
    out_dir = Path(out_dir)
    adir = out_dir / "advice"
    adir.mkdir(parents=True, exist_ok=True)
    results = []
    for f in sorted((out_dir / "exceptions").glob("*.exception.json")):
        if only and only not in f.name:
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        log(f"advising on {Path(rec['source']).name} [{rec.get('exception', {}).get('code')}]")
        try:
            adv = advise(rec, client=client, model=model)
        except Exception as e:  # noqa: BLE001 — one file's advice failing never stops the advice for the others
            adv = Advice(source=str(rec.get("source")), kind="failed", errors=[f"{type(e).__name__}: {e}"])
            log(f"  advice failed: {type(e).__name__}: {e}")
        stem = f.name[:-len(".exception.json")]
        (adir / f"{stem}.advice.md").write_text(adv.to_markdown(), encoding="utf-8")
        if adv.transcript:
            write_json(adir / f"{stem}.transcript.json", {"source": adv.source, "kind": adv.kind,
                                                          "errors": adv.errors, "model": adv.model,
                                                          "usage": adv.usage.to_dict(), "transcript": adv.transcript})
        if adv.spec:
            (adir / f"{stem}.spec.json").write_text(json.dumps(adv.spec, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(adv)
    return results
