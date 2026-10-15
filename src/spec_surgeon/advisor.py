"""The advisor — what to do about a file the engine refused (docs/NL_SURGICAL_EDITOR_PLAN.md §2 "Advisor", Phase 6).

``advise(exception_record)`` reads one ``exceptions/<name>.exception.json`` written by a batch (source path, spec,
fingerprint, the structured diagnosis) and asks Claude, with the same read-only tools as the compiler over that one
file, for ONE of:

* ``submit_recommendation(text)`` — a written recommendation for a human: what the structure is, why the engine
  refused, what would make it safe (e.g. "accept the pending revision in footer2.xml first", "this section inherits
  its header — remove it on sections 0–1 together");
* ``submit_editspec(spec)`` — a re-compiled spec **for this file's structure pattern only** (``applies_to`` is
  pinned to the pattern). It must pass the compiler's checks — validation, only words already approved (the
  original request and spec, the user's answers), a dry plan — and, unlike the compiler, it must also plan on this
  very file without any exception; otherwise it goes back once.

``advise_batch(out_dir)`` does it for every exception of a batch output folder and writes ``advice/<name>.advice.md``
(+ ``advice/<name>.spec.json`` for a re-compiled spec). Nothing is applied: a re-compiled spec is run like any
other (``run-spec``), after a human reads the advice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .catalog import op_catalog
from .compiler import DEFAULT_MODEL, FALLBACK_BETA, TOOLS, _Batch, _client, check_submission
from .editspec import plan_spec
from .fingerprint import fingerprint
from .ops import JobException
from .package import DocxPackage

ADVISOR_TOOLS = [t for t in TOOLS if t["name"] not in ("ask_user", "read_template", "submit_editspec")] + [
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
when the request can be met exactly, with the user's own words (from the original request/spec), using catalog \
ops; or
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

    def to_markdown(self) -> str:
        out = [f"# Advice — {Path(self.source).name}", "", f"**{self.kind}**", ""]
        if self.text:
            out += [self.text, ""]
        if self.spec:
            out += ["Re-compiled EditSpec (this structure pattern only; review, then `spec-surgeon run-spec`):", "",
                    "```json", json.dumps(self.spec, indent=2, ensure_ascii=False), "```"]
        if self.errors:
            out += ["", "Errors: " + "; ".join(self.errors)]
        return "\n".join(out)


def _respec_errors(spec: Any, batch: _Batch, sources: Sequence[str], pkg: DocxPackage, pid: str) -> List[str]:
    errs = check_submission(spec, batch, sources)
    if errs or not isinstance(spec, dict):
        return errs
    if spec.get("questions") or spec.get("unsupported"):
        return ["a re-compiled spec must be runnable (no questions/unsupported) — submit a recommendation instead"]
    spec["applies_to"] = {"pattern_ids": [pid]}
    try:
        plan_spec(pkg, spec, pid)
    except JobException as e:
        return [f"on this file the spec still raises [{e.code}] {e.reason}"]
    return []


def advise(record: Dict[str, Any], client=None, model: Optional[str] = None, answers: Sequence[str] = ()) -> Advice:
    model = model or DEFAULT_MODEL
    src = Path(record["source"])
    adv = Advice(source=str(src), kind="failed")
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
    client = client or _client()          # only now: an unreadable file never needs the model (nor an API key)
    spec0 = record.get("spec") or {}
    sources = [json.dumps(spec0, ensure_ascii=False), str(spec0.get("request", "")), *answers]
    messages: List[Dict[str, Any]] = [{"role": "user", "content": json.dumps({
        "file": src.name, "structure_pattern": pid, "exception": record.get("exception"),
        "original_spec": spec0 or {"job": record.get("job"), "params": record.get("params")},
        "user_answers": list(answers), "profile": batch.prof["patterns"][0]["profile"]}, ensure_ascii=False)}]
    system = ADVISOR_SYSTEM + json.dumps(op_catalog(examples=False), ensure_ascii=False)
    rejected = False
    for _ in range(10):
        resp = client.beta.messages.create(model=model, max_tokens=16000, system=system, tools=ADVISOR_TOOLS,
                                           messages=messages, output_config={"effort": "high"},
                                           cache_control={"type": "ephemeral"}, betas=[FALLBACK_BETA],
                                           fallbacks="default")
        if getattr(resp, "stop_reason", None) in ("refusal", "max_tokens"):
            adv.errors.append(f"model stopped: {resp.stop_reason}")
            return adv
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
        if not uses:
            texts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
            if texts:                                    # a plain answer counts as a recommendation
                adv.kind, adv.text = "recommendation", "\n".join(texts)
                return adv
            messages.append({"role": "user", "content": "Finish with submit_recommendation or submit_editspec."})
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
                errs = _respec_errors(spec, batch, sources, pkg, pid)
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
                out = getattr(batch, b.name)(**inp)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)[:20000]})
            except (AttributeError, KeyError, TypeError, ValueError) as e:
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": str(e)})
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
        if adv.spec:
            (adir / f"{stem}.spec.json").write_text(json.dumps(adv.spec, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(adv)
    return results
