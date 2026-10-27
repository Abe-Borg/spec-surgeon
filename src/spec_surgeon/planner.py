"""Planner — an EditSpec over a whole batch, dry.

``plan_batch(inputs, spec)`` fingerprints every file, groups the batch by structure pattern, and plans the spec
on every file (predict first; nothing is applied, nothing written). Each file gets either a plan with a short
human summary (``summarize_plan``) or the exception it would raise — the same diagnosis the real run would
produce. Spec template files found among the inputs are skipped, as in a run.

This is what the preview (Phase 4) shows before anything runs:
"58 of 60 files plan cleanly; 2 raise `inheritance` — here is why".
"""
from __future__ import annotations

import hashlib
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .editspec import is_template_file, plan_chain, plan_spec, spec_templates, validate_spec
from .fingerprint import fingerprint
from .ops import Plan
from .ops.base import CASE_DIFFERS, LEFT_ELSEWHERE, text_diffs
from .package import DocxPackage


LEFT_LINES = 40          # lines of WORDING LEFT ELSEWHERE in the plan's text (the rest counted)
LEFT_WARNINGS = 6        # of them shown beside the previews (the rest counted)


def left_notes(flags: Sequence[str], superseded: Sequence[str] = ()) -> List[str]:
    """The words of a plan's ``LEFT_ELSEWHERE`` flags — without a replacement's whose find text a later step of a
    chain replaces again (``superseded``): what it says of the source may no longer hold after that step."""
    out = []
    for flag in flags:
        if LEFT_ELSEWHERE not in flag:
            continue
        what = flag.split(LEFT_ELSEWHERE, 1)[1]
        if not any(what.casefold().startswith(f"“{find}”".casefold()) for find in superseded):
            out.append(what)
    return out


def replaced_after(stages: Sequence[Dict[str, Any]], n: int) -> List[str]:
    """The find texts a replacement in a chain's steps after step ``n`` (1-based) replaces: a step's note that the
    same words remain elsewhere may not hold once those run (C3)."""
    return [op["params"]["find"] for st in stages[n:] for op in (st.get("spec") or {}).get("ops") or []
            if op.get("op") == "replace_text" and (op.get("params") or {}).get("find")]


def left_elsewhere_warnings(bp: "BatchPlan") -> List[str]:
    """What the window shows beside the previews: each file where a change kept to one place leaves the same words
    elsewhere (C3), and each file whose header/footer fill will show in another case than the template (C4), in the
    flags' own plain words; the assistant has every file in the plan's text."""
    out: List[str] = []
    for notes, more in ((bp.left_elsewhere(), "place(s) like these keep the old words"),
                        (bp.case_notes(), "file(s) like these show a fill in another case than the template")):
        out += [f"{name}: {what}" for name, what in notes[:LEFT_WARNINGS]]
        if len(notes) > LEFT_WARNINGS:
            out.append(f"{len(notes) - LEFT_WARNINGS} more {more}; ask in the chat to see them all or to change "
                       f"them too.")
    return out


def _names(parts: Sequence[str], n: int = 6) -> str:
    short = [p.rsplit("/", 1)[-1] for p in parts]
    return ", ".join(short[:n]) + (f" … (+{len(short) - n})" if len(short) > n else "")


def _targets(a: Dict[str, Any]) -> str:
    out = [f"{a[k]} {label}" for k, label in (("expected_runs", "run(s)"), ("expected_marks", "paragraph mark(s)"),
                                              ("expected_paragraphs", "paragraph(s)"), ("expected_sections", "section(s)"),
                                              ("expected_removed", "reference(s)")) if isinstance(a.get(k), int)]
    if isinstance(a.get("expected_ids"), list):
        out.append(f"{len(a['expected_ids'])} style(s)")
    if a.get("kind") == "template_parts":
        out.append(f"{a.get('part')} ← {a.get('template_part')}")
    return ", ".join(out)


def summarize_plan(plan: Plan) -> List[str]:
    """A few plain-English lines: what changes, what is removed/added, revisions, assertions, flags."""
    lines: List[str] = []
    removed = set(plan.members_removed)
    added = set(plan.members_added)
    diffs = text_diffs(plan.diffs)                       # a structure-only declaration changes no text
    marks_only = [d for d in plan.diffs if getattr(d, "structure_only", False)]
    kept_diffs = [d for d in diffs if d.part not in removed and d.part not in added]
    if plan.members_removed:
        lines.append(f"members removed: {_names(plan.members_removed)}")
    if plan.members_added:
        lines.append(f"members added: {_names(plan.members_added)}")
    # a pure tracked deletion leaves the block text as it is (the words stay as w:delText): it shows up only as an
    # accepted view, and the summary must say so rather than "no block changes text"
    deletions = {(a["part"], a["block_index"]) for a in plan.accepted_views} - {(d.part, d.block_index) for d in plan.diffs}
    if diffs:
        by_part = Counter(d.part.rsplit("/", 1)[-1] for d in kept_diffs)
        lines.append(f"text: {len(kept_diffs)} block(s) change in kept parts"
                     + (f" ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})" if by_part else "")
                     + (f"; {len(diffs) - len(kept_diffs)} block(s) go/come with removed/added parts"
                        if len(diffs) != len(kept_diffs) else ""))
    if deletions:
        by_part = Counter(part.rsplit("/", 1)[-1] for part, _ in deletions)
        lines.append(f"text: {len(deletions)} block(s) get a tracked deletion (the deleted words stay visible as "
                     f"a redline until accepted) ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})")
    if not diffs and not deletions:
        lines.append("text: no block changes text")
    if marks_only:
        by_part = Counter(d.part.rsplit("/", 1)[-1] for d in marks_only)
        lines.append(f"structure: {len(marks_only)} block(s) lose or gain a mark that is not text (a comment mark, a "
                     f"field character …) ({', '.join(f'{p} {n}' for p, n in by_part.most_common(4))})")
    rev = {k: v for k, v in plan.rev_delta.items() if v}
    if rev:
        lines.append("revisions: " + ", ".join(f"{k} {v:+d}" for k, v in sorted(rev.items())))
    for a in plan.property_assertions:
        n = _targets(a)
        lines.append(f"asserts {a.get('kind')}" + (f" on {n}" if n else "") + (f" [{a.get('op')}]" if a.get("op") else ""))
    lines.append(f"mode {plan.mode}; layout reference {plan.geometry_reference}"
                 + (f" ({plan.geometry_reason})" if plan.geometry_reason else ""))
    lines += [f"FLAG: {f}" for f in plan.flags]
    return lines


@dataclass
class FilePlan:
    source: Path
    pattern_id: Optional[str]
    status: str                                  # planned | exception
    summary: List[str] = field(default_factory=list)
    plan: Optional[Dict[str, Any]] = None
    exception: Optional[Dict[str, Any]] = None
    ms: float = 0.0
    source_md5: Optional[str] = None             # exact bytes read for this plan, independently of later disk state
    later_notes: List[str] = field(default_factory=list)   # a chain: the later steps' wording left elsewhere, this file

    def to_dict(self):
        result = {"source": str(self.source), "pattern_id": self.pattern_id, "status": self.status,
                "summary": self.summary, "exception": self.exception, "ms": round(self.ms, 1),
                "source_md5": self.source_md5, **({"later_notes": self.later_notes} if self.later_notes else {})}
        if self.plan and any(a.get("kind") == "character_hygiene" for a in self.plan.get("property_assertions", [])):
            result["character_changes"] = [c for a in self.plan["property_assertions"]
                                           if a.get("kind") == "character_hygiene" for c in a["changes"]]
        occurrences = self.occurrences()
        if occurrences:
            result["occurrences"] = occurrences
        return result

    def occurrences(self) -> List[Dict[str, Any]]:
        """Every occurrence a ``replace_table`` op of the plan lists (backlog F1), each with the index of the op it
        belongs to: from the plan's ``replaced_text`` assertions, or — for a file the plan refused — from the
        refusal's details, which carry every occurrence the op listed (a refusal waits until every paragraph is
        listed), the refused one included.
        ``set_issue_stamp`` shares the assertion kind but takes no skip list, so its occurrences are nobody's to
        untick and are left out."""
        out: List[Dict[str, Any]] = []
        if self.plan:
            for a in self.plan.get("property_assertions", []):
                if a.get("kind") == "replaced_text" and a.get("op") == "replace_table" and isinstance(a.get("op_index"), int):
                    out += [{**o, "op_index": a["op_index"]} for o in a.get("occurrences") or [] if isinstance(o, dict)]
        elif self.exception:
            details = self.exception.get("details") or {}
            number = details.get("op_number")
            if details.get("op") == "replace_table" and isinstance(number, int) \
                    and isinstance(details.get("occurrences"), list):
                out += [{**o, "op_index": number - 1} for o in details["occurrences"] if isinstance(o, dict)]
        return out


@dataclass
class BatchPlan:
    spec: Dict[str, Any]
    files: List[FilePlan] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    seconds: float = 0.0
    superseded: List[str] = field(default_factory=list)   # find texts a later step of a chain replaces again (C3)

    @property
    def patterns(self) -> Dict[str, List[FilePlan]]:
        out: Dict[str, List[FilePlan]] = {}
        for f in self.files:
            out.setdefault(f.pattern_id or "?", []).append(f)
        return out

    @property
    def exceptions(self) -> List[FilePlan]:
        return [f for f in self.files if f.status == "exception"]

    def left_elsewhere(self) -> List[Tuple[str, str]]:
        """(file name, what) for every planned file's flag that a change kept to one place leaves the same words
        elsewhere (``LEFT_ELSEWHERE``, C3) — every file's, where the summaries show one file per pattern."""
        return [(f.source.name, what) for f in self.files if f.status == "planned"
                for what in left_notes(f.summary, self.superseded) + f.later_notes]

    def case_notes(self) -> List[Tuple[str, str]]:
        """(file name, what) for every planned file's flag that a header/footer fill will show in another case than
        the template's own text (``CASE_DIFFERS``, C4)."""
        return [(f.source.name, line.split(CASE_DIFFERS, 1)[1]) for f in self.files if f.status == "planned"
                for line in f.summary if CASE_DIFFERS in line]

    def to_dict(self):
        return {"request": self.spec.get("request"), "files": [f.to_dict() for f in self.files],
                "skipped": self.skipped, "seconds": round(self.seconds, 2),
                "patterns": {k: [Path(f.source).name for f in v] for k, v in self.patterns.items()},
                "planned": len(self.files) - len(self.exceptions), "exceptions": len(self.exceptions)}

    def to_text(self, per_file: bool = True) -> str:
        out = [f"EditSpec {self.spec.get('request')!r}: {len(self.files)} file(s), "
               f"{len(self.files) - len(self.exceptions)} plan cleanly, {len(self.exceptions)} exception(s); "
               f"{len(self.patterns)} structure pattern(s) [{self.seconds:.2f}s, nothing written]"]
        if self.skipped:
            out.append(f"skipped (the spec's template): {[Path(s).name for s in self.skipped]}")
        for pid, fs in sorted(self.patterns.items(), key=lambda kv: -len(kv[1])):
            out.append(f"\n-- pattern {pid}: {len(fs)} file(s)")
            shown = fs if per_file else fs[:1]
            for f in shown:
                if f.status == "exception":
                    out.append(f"   EXCEPTION {f.source.name}: [{f.exception['code']}] {f.exception['reason']}")
                else:
                    out.append(f"   {f.source.name}:")
                    out += [f"      {line}" for line in f.summary]
            if not per_file and len(fs) > 1:
                out.append(f"   … and {len(fs) - 1} more file(s) of this pattern")
        left = self.left_elsewhere()
        if left:
            out.append("\nWORDING LEFT ELSEWHERE — every file where the same words remain in a place this plan does "
                       "not change (tell the user, and offer to change them too):")
            out += [f"   {name}: {what}" for name, what in left[:LEFT_LINES]]
            if len(left) > LEFT_LINES:                  # every file still named; read them with the tools
                rest = list(dict.fromkeys(name for name, _ in left[LEFT_LINES:]))
                out.append(f"   … and {len(left) - LEFT_LINES} more note(s) of the same kind, in: {', '.join(rest)}")
        cases = self.case_notes()
        if cases:
            out.append("\nFILLS IN ANOTHER CASE THAN THE TEMPLATE — every file (tell the user; caps_fills shows a fill "
                       "in capitals through formatting, without retyping a letter):")
            out += [f"   {name}: {what}" for name, what in cases[:LEFT_LINES]]
            if len(cases) > LEFT_LINES:
                rest = list(dict.fromkeys(name for name, _ in cases[LEFT_LINES:]))
                out.append(f"   … and {len(cases) - LEFT_LINES} more, in: {', '.join(rest)}")
        # Hygiene changes must be reviewable for EVERY file, even when other summaries
        # are grouped by structure pattern. JSON quoting exposes ordinary/NBSP differences.
        import json
        for f in self.files:
            changes = f.to_dict().get("character_changes", [])
            if changes:
                out.append(f"\nEXACT CHARACTER CHANGES: {f.source}")
                for c in changes:
                    before = json.dumps(c["before"], ensure_ascii=True)
                    after = json.dumps(c["after"], ensure_ascii=True)
                    out.append(f"   {c['part']} paragraph {c['paragraph']+1}, character {c['start']+1}: {before} → {after}")
        return "\n".join(out)


def plan_file(path: Path, spec: Dict[str, Any]) -> FilePlan:
    """One file's dry plan. Any failure — a refusal, a file that cannot even be read (truncated zip, not a .docx), or
    the engine's own error — is that file's exception record, classified as a run would (``unreadable``,
    ``internal-error`` …, with the stage, the op and the line of code); it never aborts the batch."""
    from .diagnostics import exception_record
    t0 = time.perf_counter()
    pid = None
    stage, data, op = "open", None, None
    try:
        data = Path(path).read_bytes()
        pkg = DocxPackage(data, name=str(path))
        pkg.main_part()
        stage = "fingerprint"
        pid = fingerprint(pkg)["pattern_id"]
        stage = "plan"

        def hook(phase, number, name, state, fragment=None):
            nonlocal op
            op = (number, name) if state == "start" else None
        plan, _ = plan_spec(pkg, spec, pid, trace=hook)
        fp = FilePlan(Path(path), pid, "planned", summarize_plan(plan), plan.to_dict())
    except Exception as e:  # noqa: BLE001 — a refusal, unreadable or unexpected: recorded, not raised
        fp = FilePlan(Path(path), pid, "exception", exception=exception_record(e, stage, data, op))
    fp.source_md5 = hashlib.md5(data).hexdigest() if data is not None else None
    fp.ms = (time.perf_counter() - t0) * 1000
    return fp


def plan_file_chain(path: Path, chain: Dict[str, Any]) -> FilePlan:
    """One file's dry plan through every step of a chain (``editspec.plan_chain``: step 1 on the file, each later
    step on the bytes the step before it would make, applied in memory and never written). The plan and summary are
    step 1's, as the batch's text shows them; ``later_notes`` carries the later steps' wording-left-elsewhere notes
    for this file. A step that refuses is this file's exception, its reason prefixed "step N:" and ``chain_stage``
    set, so the file waits under Left untouched before anything runs instead of stopping at that step in the run
    (backlog Q2: the later steps were planned on the samples alone)."""
    from .diagnostics import exception_record
    t0 = time.perf_counter()
    pid = None
    stage, data, op = "open", None, None
    stages = chain.get("stages") or []
    try:
        data = Path(path).read_bytes()
        pkg = DocxPackage(data, name=str(path))
        pkg.main_part()
        stage = "fingerprint"
        pid = fingerprint(pkg)["pattern_id"]
        stage = "plan"

        def hook(phase, number, name, state, fragment=None):
            nonlocal op
            op = (number, name) if state == "start" else None
        planned = plan_chain(pkg, chain, pid, trace=hook)
        first = planned[0][0]
        later = [f"after step {i}: {what}" for i, (plan, _frags) in enumerate(planned, 1) if i > 1
                 for what in left_notes(plan.flags, replaced_after(stages, i))]
        fp = FilePlan(Path(path), pid, "planned", summarize_plan(first), first.to_dict(), later_notes=later)
    except Exception as e:  # noqa: BLE001 — a refusal at any step, unreadable or unexpected: recorded, not raised
        record = exception_record(e, stage, data, op)
        step = (record.get("details") or {}).get("chain_stage")
        if isinstance(step, int):
            record["chain_stage"] = step
        fp = FilePlan(Path(path), pid, "exception", exception=record)
    fp.source_md5 = hashlib.md5(data).hexdigest() if data is not None else None
    fp.ms = (time.perf_counter() - t0) * 1000
    return fp


# Planning is a fraction of a file's run (no apply, no verify): a batch is shared over worker processes for its plans
# only from this many files, where the workers' start costs less than it saves (batch.map_files).
PLAN_PROCESS_MIN_FILES = 12


def plan_batch(inputs: Sequence[str], spec: Dict[str, Any]) -> BatchPlan:
    """Plan ``spec`` on every input file (dry), in the inputs' order; a large batch's files are planned side by side
    in worker processes (``batch.map_files``), each exactly as alone. Raises SpecError for an unusable spec before
    reading any file."""
    from .batch import expand_inputs, map_files
    validate_spec(spec)
    t0 = time.perf_counter()
    files = expand_inputs(inputs)
    templates = spec_templates(spec)
    bp = BatchPlan(spec=spec)
    planned = []
    for f in files:
        if is_template_file(f, templates):
            bp.skipped.append(str(f))
        else:
            planned.append(f)
    bp.files = map_files(plan_file, [(f, spec) for f in planned], min_files=PLAN_PROCESS_MIN_FILES)
    bp.seconds = time.perf_counter() - t0
    return bp


def plan_batch_chain(inputs: Sequence[str], chain: Dict[str, Any]) -> BatchPlan:
    """``plan_batch`` for a chain: every step planned on every input file (``plan_file_chain``), side by side in
    worker processes from ``PLAN_PROCESS_MIN_FILES`` files. The batch plan's spec is step 1's (its text shows step
    1's summaries); every stage's template files are skipped from the inputs, as a run skips them."""
    from .batch import expand_inputs, map_files
    stages = chain.get("stages") or []
    if not stages:
        raise ValueError("a chain needs at least one stage")
    for st in stages:
        validate_spec(st["spec"])
    t0 = time.perf_counter()
    files = expand_inputs(inputs)
    templates = [t for st in stages for t in spec_templates(st["spec"])]
    bp = BatchPlan(spec=stages[0]["spec"], superseded=replaced_after(stages, 1))
    planned = []
    for f in files:
        if is_template_file(f, templates):
            bp.skipped.append(str(f))
        else:
            planned.append(f)
    bp.files = map_files(plan_file_chain, [(f, chain) for f in planned], min_files=PLAN_PROCESS_MIN_FILES)
    bp.seconds = time.perf_counter() - t0
    return bp
