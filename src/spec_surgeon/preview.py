"""Preview and approval — what the user sees before anything runs on the batch.

``make_preview(spec, inputs, out_dir)``:
1. plans the spec on every file (planner.plan_batch — summaries and exceptions, nothing written);
2. picks representative plannable files — automatically eight or enough to cover every structure pattern,
   with an explicit positive ``review.sample_files`` count honored — and runs plan → apply → verify on them in memory,
   writing the outputs into ``out_dir/samples`` (never over a source) under names unique across the samples
   (``batch.output_names``: two folders' ``Spec.docx`` are ``Spec.docx`` and ``Spec (1).docx``); the preview text (and
   the note Claude gets) names each sample by ``sample_labels`` — ``a/Spec.docx`` and ``b/Spec.docx``, as Claude's
   visual check labels its pages;
3. renders source and output of each sample (Word when available, else LibreOffice — a visible warning says so:
   decision on §14.5, "fall back with a warning", never silently) and composes side-by-side PNGs of page 1 and of
   the first page whose words moved;
4. writes ``spec.json`` and ``PREVIEW.md`` with an **approval token** captured before planning — a hash of the spec
   and every planned file's path, size, mtime and content. ``run-spec --approval TOKEN`` refuses when any input
   changed since the preview — and says which (``approval_inputs`` are kept in ``preview.json``;
   ``explain_approval_change`` compares them with the files as they are now). ``do`` re-checks it after the y/N (or
   ``--yes``) and before the run (``changed_since_preview``) — for a custom transform with ``generated_key`` in place
   of the spec.

A plan per layout (``layouts.make_layout_preview``) runs this once per layout, on that layout's files only, under
``out_dir/layout-<id>``, and binds one token to every layout, its files and the routing.

Diagnostics: ``logs/preview.log`` has every file's plan or refusal (with the stage, the op and the line of code), each
sample's verification with the failing checks' specifics, and what the renderer said; a sample that fails in an
unexpected way is that sample's exception record (classified like a run's), never a lost preview; a page comparison
or picture that fails is a warning. A custom transform's preview (``preview_generated``) keeps the same log.

``render_samples`` keeps each sample's PDFs and the pages worth looking at (``look_pages``) for Claude's visual check
(``vision.review_samples``, advisory); ``write_preview_files`` rewrites PREVIEW.md and preview.json when it arrives
(``approval_inputs`` stay in preview.json).
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import os
import posixpath
import re
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diagnostics as diag
from .editspec import apply_spec, plan_spec
from .fingerprint import fingerprint
from .package import DocxPackage
from .planner import BatchPlan, left_elsewhere_notes, left_elsewhere_warnings, left_notes, plan_batch, plan_batch_chain


def key_templates(key: Any) -> List[Path]:
    """The files an approval key reads beside the documents: a spec's templates, tables and pictures
    (``editspec.spec_input_files``), or — a chain key (``chain.chain_key``: ``{"stages": [{"name", "spec"}, …]}``) —
    those of every stage, once each. A file edited after the preview voids the approval whichever stage reads it
    (CLAUDE.md rule 12)."""
    from .input_snapshots import input_paths
    return input_paths(key)


def _snapshot_preview_inputs(value, token_inputs, out_dir):
    """Run preview plans and checks against copies of exactly the approved auxiliary bytes.

    Public specs and approval keys keep their original paths. The execution copy alone
    names these persistent snapshots, whose manifest preserves the original provenance.
    """
    from .input_snapshots import snapshot_inputs
    return snapshot_inputs(value, out_dir, token_inputs["templates"])


def approval_inputs(spec: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """What the approval token is a hash of, as data: the spec's digest, every planned file's size, mtime and md5, and every
    file the spec reads beside the documents — templates, tables, pictures (``key_templates``) — its size, mtime and md5,
    kept beside the preview so a refused run can say exactly what changed. ``spec`` may be a chain key
    (``chain.chain_key``): then the files are every stage's."""
    spec_text = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    out: Dict[str, Any] = {"spec": hashlib.sha256(spec_text.encode("utf-8")).hexdigest()[:16], "files": {},
                           "templates": {}}
    for f in sorted(Path(x).resolve() for x in files):
        try:
            st = f.stat()
            out["files"][str(f)] = [st.st_size, st.st_mtime_ns, hashlib.md5(f.read_bytes()).hexdigest()]
        except OSError:
            out["files"][str(f)] = "missing"
    for t in sorted(set(key_templates(spec))):
        try:
            st = t.stat()
            out["templates"][str(t)] = [st.st_size, st.st_mtime_ns, hashlib.md5(t.read_bytes()).hexdigest()]
        except OSError:
            out["templates"][str(t)] = "missing"
    return out


def approval_token(spec: Dict[str, Any], files: Sequence[Path]) -> str:
    """A hash of the spec, of every planned file's path, size, mtime and content, and every file read beside the
    documents — templates, tables, pictures (``key_templates`` → ``editspec.spec_input_files``: path, size, mtime and
    content hash): they are inputs like the source (CLAUDE.md rule 12), so one edited after the preview voids the
    approval exactly as an edited spec file does. A spec with templates only hashes exactly as before. ``spec`` may be
    a chain key (``chain.chain_key``): every stage's files count."""
    return _approval_token_from_inputs(spec, approval_inputs(spec, files))


def narrow_approval_inputs(inputs: Dict[str, Any], files: Sequence[Path]) -> Dict[str, Any]:
    """The approval inputs restricted to ``files``, part of the approved batch: the same spec digest and template
    pins, and only those files' pins. A run on part of an approved batch is held to this, so every file that runs
    keeps the identity the user approved and nothing outside the approval runs."""
    keep = {str(Path(f).resolve()) for f in files}
    return {**inputs, "files": {p: pin for p, pin in (inputs.get("files") or {}).items() if p in keep}}


def _approval_token_from_inputs(spec, inputs):
    """Bind a token to one captured identity snapshot, never to files re-read after the preview work."""
    h = hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode("utf-8"))
    for path, pin in sorted(inputs["files"].items()):
        if pin == "missing":
            raise FileNotFoundError(path)
        h.update(f"\n{path}|{pin[0]}|{pin[1]}|{pin[2]}".encode("utf-8"))
    for path, pin in sorted(inputs["templates"].items()):
        identity = "missing" if pin == "missing" else f"{pin[0]}|{pin[1]}|{pin[2]}"
        h.update(f"\ntemplate {path}|{identity}".encode("utf-8"))
    return h.hexdigest()[:16]


def generated_key(g: Any) -> Dict[str, Any]:
    """What a custom transform's approval token hashes beside the files (in place of a spec): its code, selection and
    parameters and approved adaptive outcome — the same key as the window's ``Session._generated_key``."""
    key = {"generated_source": g.source, "select": g.select, "params": g.params,
           "goal_contract": getattr(g, "goal_contract", None), "goal_bindings": getattr(g, "goal_bindings", {})}
    if getattr(g, "unguarded", False):
        key["unguarded"] = True                   # an Obliterate method's approval is never a guarded one's
        if getattr(g, "attachments", None):
            key["attachments"] = list(g.attachments)          # the attached files' pinned bytes it was written with
        if getattr(g, "accepted_failures", None):
            key["accepted_failures"] = dict(g.accepted_failures)   # the reasons the user read beside the copies
    return key


def changed_since_preview(key: Dict[str, Any], files: Sequence[Path], token: str,
                          before: Optional[Dict[str, Any]] = None) -> Optional[List[str]]:
    """None when ``key`` (a spec, or ``generated_key``) and ``files`` are exactly as they were when the preview's
    ``token`` was taken; otherwise what changed, one line each (``explain_approval_change`` against ``before``, the
    preview's ``approval_inputs`` — possibly empty when they were not kept). A previewed file that is gone is a
    change, never an exception."""
    try:
        if token and approval_token(key, files) == token:
            return None
    except OSError:
        pass                                      # a previewed file is missing now: changed
    return explain_approval_change(before or {}, approval_inputs(key, files))


def _stamp(ns: Any) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(int(ns) / 1e9).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "?"


def explain_approval_change(before: Dict[str, Any], now: Dict[str, Any], limit: int = 12,
                            since: str = "the preview") -> List[str]:
    """What changed between the preview's ``approval_inputs`` and the files as they are now, one line each — the
    reason a run is refused, instead of "something changed". ``since`` names the moment ``before`` was taken (a
    custom transform compares the files with how they were before it was written)."""
    out: List[str] = []
    if before.get("spec") != now.get("spec"):
        out.append("the EditSpec itself changed")
    bf, nf = before.get("files") or {}, now.get("files") or {}
    for f in sorted(set(nf) - set(bf)):
        out.append(f"{Path(f).name}: a file that was not in the preview (added to the folder since)")
    for f in sorted(set(bf) - set(nf)):
        out.append(f"{Path(f).name}: in the preview, but no longer among the files (moved, renamed or deleted)")
    for f in sorted(set(bf) & set(nf)):
        a, b = bf[f], nf[f]
        if a == b:
            continue
        if b == "missing" or a == "missing":
            out.append(f"{Path(f).name}: {'missing now' if b == 'missing' else 'back again'}")
        elif a[:2] == b[:2] and len(a) > 2 and len(b) > 2:
            out.append(f"{Path(f).name}: content changed since {since} (size and saved timestamp unchanged)")
        else:
            out.append(f"{Path(f).name}: modified since {since} (size {a[0]:,} → {b[0]:,} bytes; saved "
                       f"{_stamp(a[1])} → {_stamp(b[1])})")
    bt, nt = before.get("templates") or {}, now.get("templates") or {}
    for t in sorted(set(bt) | set(nt)):
        a, b = bt.get(t), nt.get(t)
        if a != b:
            what = "template" if Path(t).suffix.lower() == ".docx" else "input file"
            out.append(f"{what} {Path(t).name}: " + ("missing now" if b == "missing" or b is None else
                                                       f"changed since {since}" if a not in (None, "missing")
                                                       else "new"))
    if len(out) > limit:
        out = out[:limit] + [f"… and {len(out) - limit} more change(s)"]
    return out


def sample_labels(samples: Sequence[Any]) -> Dict[str, str]:
    """``str(source)`` → how the preview text (and the note Claude gets) names each sample: its name, or with the
    folders that tell two same-named samples apart (``compiler.file_labels``, as Claude's visual check labels its
    pages) — two folders' ``Spec.docx`` are ``a/Spec.docx`` and ``b/Spec.docx``, never two lines that read alike."""
    from .compiler import file_labels
    return file_labels([Path(s.source) for s in samples])


@dataclass
class SampleView:
    source: Path
    output: Optional[Path] = None
    passed: Optional[bool] = None
    checks: Dict[str, str] = field(default_factory=dict)
    images: List[Path] = field(default_factory=list)
    exception: Optional[Dict[str, Any]] = None
    source_pdf: Optional[Path] = None             # the renders (for Claude's visual check)
    output_pdf: Optional[Path] = None
    pages_before: Optional[int] = None
    pages_after: Optional[int] = None
    look_pages: List[int] = field(default_factory=list)   # page 1 and the first page whose words moved
    stages: List[Dict[str, Any]] = field(default_factory=list)   # a chain's sample: each stage's verdict, in order
    outcomes: List[Dict[str, Any]] = field(default_factory=list)  # a chain's independent final requirements
    left_later: List[str] = field(default_factory=list)   # a chain's sample: its later steps' wording left elsewhere
    plan: Optional[Dict[str, Any]] = None        # the plan the verdict is of (V6/V7 read its geometry reference); not saved
    geometry: Optional[Dict[str, str]] = None    # the page-layout verdict: status PASS | FAIL | NA, and a note in plain words
    # a custom method's sample: the words its copy adds and removes, derived from the verifier's V2 record
    # (``text_changes_of``); [] when the method changes no words; None for a plan's sample (its sentences say what
    # changes) and for a sample the checker left no record of
    text_changes: Optional[List[Dict[str, Any]]] = None
    render_warnings: List[str] = field(default_factory=list)   # V6's warning: words the original's pages show, the copy's not

    def to_dict(self):
        return {"source": str(self.source), "output": str(self.output) if self.output else None, "passed": self.passed,
                "checks": self.checks, "images": [str(i) for i in self.images], "exception": self.exception,
                "look_pages": self.look_pages, "pages_before": self.pages_before, "pages_after": self.pages_after,
                "geometry": self.geometry, "text_changes": self.text_changes,
                **({"render_warnings": self.render_warnings} if self.render_warnings else {}),
                **({"stages": self.stages} if self.stages else {}),
                **({"outcomes": self.outcomes} if self.outcomes else {}),
                **({"left_later": self.left_later} if self.left_later else {})}

    def stages_text(self) -> str:
        """``stage 1 PASS · stage 2 FAIL [V10]`` — how far a chain's sample got."""
        parts = []
        for st in self.stages:
            if st.get("exception"):
                parts.append(f"stage {st['stage']} EXCEPTION [{st['exception'].get('code')}]")
            elif st.get("passed"):
                parts.append(f"stage {st['stage']} PASS")
            else:
                failed = [k for k, v in (st.get("checks") or {}).items() if v == "FAIL"]
                parts.append(f"stage {st['stage']} FAIL {failed}")
        return " · ".join(parts)


GEOMETRY_AT_RUN = {"status": "NA", "at": "run",
                   "note": "Page layout is checked when the copies are created; no pages were rendered here."}
# the sample's pages WERE rendered (the before/after pictures and page counts are real) but the preview could not
# make the layout check on them; the status and the ``at`` marker are the same, the note says what did happen
GEOMETRY_RENDERED_AT_RUN = {"status": "NA", "at": "run",
                            "note": "Pages were rendered here; the page layout is checked when the copies are "
                                    "created, because the preview does not hold this sample's plan."}
GEOMETRY_RENDERED_NOT_COMPARED = {"status": "NA", "at": "run",
                                  "note": "Pages were rendered here; the page layout is checked when the copies are "
                                          "created, because the layout reference could not be rendered or compared."}
GEOMETRY_AT_RUN_STEPS = {"status": "NA", "at": "run",
                         "note": "Page layout of a plan done in steps is checked step by step when the copies are "
                                 "created; the preview renders the last step's pages only."}


def geometry_text(sv: Any) -> str:
    """The page-layout verdict in a sample's line of the preview text: V7's status and note when the preview checked
    it, else that the run will."""
    g = sv.geometry
    if not g:
        return ""
    if g.get("at") == "run":
        return f"; page geometry (V7): checked at the run — {g['note']}"
    return f"; page geometry (V7) {g['status']}: {g['note']}"


def geometry_note(plan: Dict[str, Any], v7: Any) -> str:
    """V7's verdict in plain words, for the window and the preview text."""
    ref = plan.get("geometry_reference", "source")
    if v7.status == "PASS":
        return ("Page layout checked on the rendered pages: the headers and footers sit where the template's do."
                if ref == "template" else
                "Page layout checked on the rendered pages: the words sit where they did.")
    if v7.status == "FAIL":
        return "Page layout differs from what the plan predicts: " + diag.snip(v7.summary, 400)
    if ref == "none":
        return f"No page-layout reference by design: {plan.get('geometry_reason') or 'the plan reflows the text'}."
    if plan.get("mode") in ("content", "untracked") and plan.get("diffs"):
        return "Text reflows after a wording change; the page layout is recorded, not asserted."
    return "Page layout not asserted here: " + diag.snip(v7.summary, 400)


def layout_failures(samples: Sequence[Any]) -> List[str]:
    """One line per sample whose rendered pages failed the page-layout check (V7), labelled as the preview text labels
    the sample, with V7's verdict in plain words: what a custom method's verdict says beside the checks its trials
    passed (``codegen.samples_verdict``), since a trial is verified without its pages."""
    labels = sample_labels(samples)
    return [f"{labels[str(Path(s.source))]}: {s.geometry.get('note') or 'the page layout check failed'}"
            for s in samples if (s.geometry or {}).get("status") == "FAIL"]


def render_word_warnings(pv: Any) -> List[str]:
    """Each sample's V6 warning (``batch.compare_pair`` over its rendered pages: words the original's pages show that
    the copy's do not and the plan does not remove), labelled as the preview text labels the sample. What the chat
    says in the app's words before approval, and what Claude's visual check is told to look for."""
    samples = getattr(pv, "samples", None) or []
    labels = sample_labels(samples)
    return [f"{labels[str(Path(s.source))]}: {w}" for s in samples
            for w in (getattr(s, "render_warnings", None) or [])]


def sample_refused(sv: Any) -> bool:
    """A sample the plan refused (a ``JobException`` with its code: a case the op cannot predict), not one the engine
    failed on or whose output failed its checks. A chain's every step is planned on every file in the batch plan
    (``planner.plan_batch_chain``), so a sample refused here is one whose bytes the sample run read otherwise."""
    return (getattr(sv, "exception", None) or {}).get("kind") == "refusal"


def sample_failed(sv: Any, unguarded: bool = False) -> bool:
    """A sample that did not get through and was not refused: its copy failed a check, or the engine failed on it.
    In Obliterate mode (``unguarded``) a copy that was made but failed its checks is delivered marked UNVERIFIED, so
    only a sample with no copy (an engine failure) counts there."""
    if sample_refused(sv) or getattr(sv, "passed", None) is True:
        return False
    return not (unguarded and getattr(sv, "exception", None) is None)


def failed_samples(pv: Any, unguarded: bool = False) -> List[Any]:
    """The samples a preview sets aside because they failed (``sample_failed``) while another sample got through: the
    preview of the others stands, and they wait under Left untouched with their failed checks, as a refused file does
    (the owner's Safe-mode run of 2026-10-28: one twelve-file batch's sample failed V7 and Create was unavailable for
    all twelve). When no sample got through there is nothing to set them aside from: [] — the preview cannot stand,
    and Create stays unavailable, or Default mode's outcome planning takes the request."""
    samples = list(getattr(pv, "samples", None) or [])
    failed = [sv for sv in samples if sample_failed(sv, unguarded)]
    through = [sv for sv in samples if not sample_refused(sv) and not sample_failed(sv, unguarded)]
    return failed if through else []


def refusals(pv: Any) -> List[Tuple[Path, Dict[str, Any]]]:
    """The documents a preview refused, each with its refusal: every one the plan refused — at any step of a chain,
    every step being planned on every file — then any sample a step refused when it ran. They wait under Left
    untouched while the others are previewed (the owner's 1.10.0 run of 2026-10-23: two samples whose list numbering
    the font step refused handed a nineteen-file preview to outcome planning)."""
    out = [(Path(f.source), f.exception or {}) for f in getattr(getattr(pv, "batch_plan", None), "exceptions", None)
           or []]
    seen = {p.resolve() for p, _ in out}
    for sv in getattr(pv, "samples", None) or []:
        if sample_refused(sv) and Path(sv.source).resolve() not in seen:
            seen.add(Path(sv.source).resolve())
            out.append((Path(sv.source), sv.exception))
    return out


@dataclass
class Preview:
    out_dir: Path
    batch_plan: BatchPlan
    samples: List[SampleView] = field(default_factory=list)
    token: str = ""
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    # the warnings worth reading before the copies are made, as the window lists them under Worth knowing: the same
    # words left elsewhere (C3), a fill in another case than the template (C4), an input that changed under the preview
    worth_knowing: List[Dict[str, str]] = field(default_factory=list)
    review: Any = None                            # vision.VisualReview — Claude's visual check (advisory)
    review_ack: str = ""                          # the token whose blocking findings the user acknowledged
    token_inputs: Dict[str, Any] = field(default_factory=dict)   # approval_inputs at preview time
    log_path: Optional[Path] = None                               # logs/preview.log
    input_snapshots: List[Dict[str, Any]] = field(default_factory=list)

    def to_text(self) -> str:
        spec = self.batch_plan.spec
        out = [f"PREVIEW — {spec.get('request')!r}", f"summary: {spec.get('summary')}"]
        out += [f"assumption: {a}" for a in spec.get("assumptions", [])]
        out += [f"WARNING: {w}" for w in self.warnings]
        for binding in spec.get("bindings", []):
            out.append(f"FILLS — {Path(binding['file']).name}:")
            for name, evidence in binding["slots"].items():
                out.append(f"   {name}: {evidence['typed']!r} (the user's wording for this file)" if "typed" in evidence
                           else f"   {name}: {evidence['value']!r} ({evidence['part']}, "
                                f"paragraph {evidence['paragraph'] + 1})")
        out.append("")
        out.append(self.batch_plan.to_text(per_file=False))
        out.append("\nSAMPLES (applied and verified in memory; sources untouched):")
        labels = sample_labels(self.samples)
        for s in self.samples:
            lab = labels[str(Path(s.source))]
            if s.exception:
                out.append(f"   {lab}: EXCEPTION [{s.exception['code']}] {s.exception['reason']}")
                continue
            failed = [k for k, v in s.checks.items() if v == "FAIL"]
            out.append(f"   {lab}: verification {'PASS' if s.passed else 'FAIL ' + str(failed)}"
                       + (f"; pages: {s.pages_before} → {s.pages_after}" if s.pages_before is not None else "")
                       + (f"; images: {[i.name for i in s.images]}" if s.images else "")
                       + geometry_text(s))
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        from .readable import REPORT_BYTES, bounded
        return (bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")
                + f"\napproval token: {self.token}")

    def to_dict(self):
        return {"token": self.token, "renderer": self.renderer, "warnings": self.warnings,
                "plan": self.batch_plan.to_dict(), "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "approval_inputs": self.token_inputs, "input_snapshots": self.input_snapshots, "log": "logs/preview.log"}


def side_by_side(src_pdf: Path, out_pdf: Path, page: int, png: Path, dpi: int = 70) -> Optional[Path]:
    """One PNG: the source's page on the left, the output's on the right (1-based page)."""
    from .verify.geometry import fitz
    a, b = fitz.open(str(src_pdf)), fitz.open(str(out_pdf))
    try:
        if page > a.page_count or page > b.page_count:
            return None
        ra, rb = a[page - 1].rect, b[page - 1].rect
        gap = 18
        sheet = fitz.open()
        pg = sheet.new_page(width=ra.width + rb.width + gap, height=max(ra.height, rb.height) + 16)
        pg.show_pdf_page(fitz.Rect(0, 16, ra.width, 16 + ra.height), a, page - 1)
        pg.show_pdf_page(fitz.Rect(ra.width + gap, 16, ra.width + gap + rb.width, 16 + rb.height), b, page - 1)
        pg.insert_text((4, 11), f"BEFORE  p.{page}", fontsize=9)
        pg.insert_text((ra.width + gap + 4, 11), f"AFTER  p.{page}", fontsize=9)
        png.parent.mkdir(parents=True, exist_ok=True)
        pg.get_pixmap(dpi=dpi).save(str(png))
        sheet.close()
        return png
    finally:
        a.close(); b.close()


def _pick_samples(bp: BatchPlan) -> List[Path]:
    """Cover every planned pattern automatically, or honor an explicit sample count."""
    from .sampling import representative_samples
    planned = [f for f in bp.files if f.status == "planned"]
    samples = representative_samples(planned, pattern_key=lambda f: f.pattern_id or str(f.source),
                                     count=(bp.spec.get("review") or {}).get("sample_files"))
    return [f.source for f in samples]


# ---- the words a custom method changes, read from the verifier's own record -------------------------------------------
#
# A built-in operation says what it changes in its own sentence (``Op.describe``) and the gate traces every placed word
# to the user's sources or to the reply the user read. A custom method has neither: its words are checked only against
# its own plan. So before approval the window lists the words its sample copies add and remove, taken from the
# independent verifier's V2 record (``verify/suite.py``: ``data["actual"]``, one entry per changed text block —
# ``{part, before, after, before_index, after_index}``), never from the method or the model. The derivation reads the
# record as data (a dict); it imports nothing of the verifier, and the verifier knows nothing of it.

_PART_WORDS = {"document": "body", "header": "header", "footer": "footer", "footnotes": "footnote",
               "endnotes": "endnote", "comments": "comment"}
_PART_NAME = re.compile(r"(document|header|footer|footnotes|endnotes|comments)(\d*)")


def part_words(part: str, several: bool = False) -> str:
    """A text part's name in plain words — ``body``, ``header``, ``footer``, ``footnote``, ``endnote``, ``comment`` —
    from its usual name (``word/footer1.xml``); a name the rule does not know is shown as it is, never guessed.
    ``several``: more than one part of the kind is in play, so Word's number tells them apart (``footer 2``)."""
    stem = posixpath.basename(part or "").rsplit(".", 1)[0].lower()
    m = _PART_NAME.fullmatch(stem)
    if m is None:
        return part
    kind, number = m.groups()
    word = _PART_WORDS[kind]
    return f"{word} {number}" if several and number else word


def word_changes(before: Optional[str], after: Optional[str]) -> Tuple[List[str], List[str]]:
    """The words removed and the words added between one block's text before and after, at word granularity: each
    entry is one unbroken stretch of words, in order. A block that is new (``before`` None) or gone (``after`` None) is
    its whole text. Words are what whitespace separates; a change of spacing alone is no word change (both lists empty)."""
    if before is None:
        return [], ([after] if after else [])
    if after is None:
        return ([before] if before else []), []
    a, b = before.split(), after.split()
    removed: List[str] = []
    added: List[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        if i2 > i1:
            removed.append(" ".join(a[i1:i2]))
        if j2 > j1:
            added.append(" ".join(b[j1:j2]))
    return removed, added


def _v2_data(record: Any) -> Optional[Dict[str, Any]]:
    """V2's data from a verification record (``VerificationRecord.to_dict()``, or the object itself, read through
    ``to_dict``); None when the record has no V2 check at all (nothing was compared)."""
    if record is None:
        return None
    if not isinstance(record, dict):
        record = record.to_dict()
    for check in record.get("checks") or []:
        if isinstance(check, dict) and check.get("id") == "V2":
            data = check.get("data")
            return data if isinstance(data, dict) else {}
    return None


def _entries(value: Any) -> List[Dict[str, Any]]:
    return [a for a in value if isinstance(a, dict)] if isinstance(value, list) else []


def _v2_changes(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The changed blocks a copy's words are listed from: V2's ``actual`` entries for every part without a content
    revision, and for a part that carries one (``views["parts"]``) the union of the verifier's accept-all and
    reject-all view comparisons instead — the block text V2 enumerates keeps tracked-deleted text, so a tracked
    deletion reads as no change there and a tracked replacement as its deleted and inserted words run together.
    A block the two views do not agree on is marked ``tracked``: the words sit in the copy's tracked changes. A record
    without ``views`` (older) gives the ``actual`` entries alone."""
    actual = _entries(data.get("actual"))
    views = data.get("views")
    if not isinstance(views, dict):
        return actual
    revised = {str(p) for p in (views.get("parts") or [])}
    out = [a for a in actual if str(a.get("part") or "") not in revised]
    merged: Dict[Tuple[str, Any, Any], Dict[str, Any]] = {}
    for which in ("accepted", "rejected"):
        for entry in _entries(views.get(which)):
            if str(entry.get("part") or "") not in revised:
                continue
            key = (str(entry.get("part") or ""), entry.get("before_index"), entry.get("after_index"))
            found = merged.setdefault(key, {"part": key[0], "before_index": key[1], "after_index": key[2],
                                            "views": {}})
            found["views"][which] = (entry.get("before"), entry.get("after"))
    for found in merged.values():
        found["tracked"] = found["views"].get("accepted") != found["views"].get("rejected")
        out.append(found)
    return out


def text_changes_of(record: Any) -> Optional[List[Dict[str, Any]]]:
    """The words a copy adds and removes, derived from its verification record's V2 entries: one entry per changed
    text block, in the record's order — ``part`` (as the record names it), ``where`` (the part in plain words and,
    in the body, or past a part's first paragraph, the paragraph's ordinal from 1: ``footer``, ``body paragraph 12``),
    ``paragraph`` (the block's ordinal in its part from 0: the source's, or the copy's for a new block), ``removed``
    and ``added`` (``word_changes``; for a part with tracked changes, the union of what the verifier's accept-all and
    reject-all views remove and add, ``_v2_changes``), ``block`` ``new`` or ``gone`` when the whole paragraph is, and
    ``tracked`` when the words sit in the copy's tracked changes (the two views disagree). [] when the record
    compared the texts and found the same words; None when it holds no V2 check."""
    data = _v2_data(record)
    if data is None:
        return None
    changes = _v2_changes(data)
    kinds: Dict[str, set] = {}
    for a in changes:
        part = str(a.get("part") or "")
        kinds.setdefault(part_words(part), set()).add(part)
    out: List[Dict[str, Any]] = []
    for a in changes:
        part = str(a.get("part") or "")
        before_index, after_index = a.get("before_index"), a.get("after_index")
        index = before_index if isinstance(before_index, int) else after_index
        label = part_words(part, several=len(kinds.get(part_words(part), ())) > 1)
        where = label
        if isinstance(index, int) and (label.startswith("body") or index > 0):
            where = f"{label} paragraph {index + 1}"
        # the words: the one comparison, or the union of what the accept-all and reject-all views each remove and add
        pairs = list((a.get("views") or {}).values()) or [(a.get("before"), a.get("after"))]
        removed: List[str] = []
        added: List[str] = []
        for before, after in pairs:
            r, d = word_changes(before, after)
            removed += [w for w in r if w not in removed]
            added += [w for w in d if w not in added]
        entry: Dict[str, Any] = {"part": part, "where": where, "paragraph": index if isinstance(index, int) else None,
                                 "removed": removed, "added": added}
        befores = [b for b, _ in pairs]
        afters = [x for _, x in pairs]
        if all(b is None for b in befores) and any(x is not None for x in afters):
            entry["block"] = "new"
        elif all(x is None for x in afters) and any(b is not None for b in befores):
            entry["block"] = "gone"
        if a.get("tracked"):
            entry["tracked"] = True
        out.append(entry)
    return out


def _quoted(words: Sequence[str]) -> str:
    return ", ".join(f"\u201c{w}\u201d" for w in words)


def text_change_phrase(entry: Dict[str, Any]) -> str:
    """One entry of ``text_changes_of`` as the window says it: ``words removed “FIRE”; words added “WATER”``, a whole
    paragraph named as such, and a change of spacing alone said to be one."""
    removed, added, block = entry.get("removed") or [], entry.get("added") or [], entry.get("block")
    tracked = " (in the copy's tracked changes)" if entry.get("tracked") else ""
    if block == "new":
        return (f"a new paragraph, words added {_quoted(added)}" if added else "a new paragraph without words") + tracked
    if block == "gone":
        return (f"the whole paragraph removed, words removed {_quoted(removed)}" if removed
                else "an empty paragraph removed") + tracked
    parts = []
    if removed:
        parts.append(f"words removed {_quoted(removed)}")
    if added:
        parts.append(f"words added {_quoted(added)}")
    return ("; ".join(parts) if parts else "only the spacing changes, no words") + tracked


NO_WORDS_CHANGE = ("No words change: the independent checker read the same words in the sample copies as in the "
                   "originals, in the body, headers, footers, notes and comments.")
WORDS_CHANGE_HEAD = ("Before you approve — the words the custom method changes, as the independent checker read them "
                     "in the sample copies:")


def text_changes_lines(samples: Sequence[Any]) -> List[str]:
    """The app's own sentences about the words a custom method's samples change, said in the chat before the approval
    button and kept in the transcript: nothing when no sample carries a derivation; ``NO_WORDS_CHANGE`` when every
    sample's copy keeps its words; otherwise the heading and one line per distinct change (the same change in several
    documents is one line naming them all), the exact words quoted."""
    known = [s for s in samples if getattr(s, "text_changes", None) is not None]
    if not known:
        return []
    lines: Dict[Tuple[str, str], List[str]] = {}
    for s in known:
        name = Path(s.source).name
        for entry in s.text_changes:
            key = (entry.get("where") or entry.get("part") or "", text_change_phrase(entry))
            names = lines.setdefault(key, [])
            if name not in names:
                names.append(name)
    if not lines:
        return [NO_WORDS_CHANGE]
    out = [WORDS_CHANGE_HEAD]
    for (where, phrase), names in lines.items():
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        out.append(f"\u2022 {who}, {where}: {phrase}")
    return out


def _preview_log(out_dir: Path, title: str):
    """``logs/preview.log`` beside the preview (a diagnostic never stops the work it describes: an unwritable log is
    silently no log)."""
    from . import diagnostics as diag
    return diag.RunLog(Path(out_dir) / diag.LOGS / "preview.log", None,
                       [f"Spec Surgeon preview log — {title}",
                        f"environment  {diag.environment_line(diag.environment())}", ""])


def _log_verification(log: Any, name: str, rec: Any) -> None:
    """One sample's verification in the preview log: every check's status as the suite left them (V6 and V7 follow
    once the sample's pages are rendered, ``_sample_geometry``; V9 belongs to a run) and each failed check's
    specifics."""
    if log is None or rec is None:
        return
    diag.event("preview.verification", sample=name, passed=rec.passed,
               checks={c.id: c.status for c in rec.checks},
               failed_checks=[c.id for c in rec.checks if c.status == "FAIL"])
    log.detail(f"sample {name}: verification {'PASS' if rec.passed else 'FAIL'} — "
               + " · ".join(f"{c.id} {c.status}" for c in rec.checks if c.id not in ("V6", "V7", "V9")))
    for cid, cname, _summary, details in rec.failures():
        log.block(f"{cid} {cname}:\n" + "\n".join(f"  - {x}" for x in details))


def render_samples(samples: List[SampleView], out_dir: Path, renderer: str, do_render: bool,
                   warnings: List[str], log: Any = None) -> Optional[str]:
    """Render each sample's source and output (Word, else LibreOffice), compose before/after images of page 1 and of
    the first page whose words moved, and keep the PDFs and those page numbers on the sample (Claude's visual check
    reads them). Never raises for a renderer or a picture: a renderer that cannot run, a file it cannot convert or a
    page comparison that fails is a warning beside the plan summaries and verification. ``log`` (a
    ``diagnostics.RunLog``, optional) gets what the renderer said and every traceback. Returns the renderer used, or
    None."""
    import shutil
    from . import render as render_mod
    from .verify import geometry as geom
    detail = log.detail if log is not None else (lambda msg: None)
    block = log.block if log is not None else (lambda text: None)

    def warn(msg: str) -> None:
        warnings.append(msg)
        detail(msg)
    backend = render_mod.pick(renderer) if do_render else None
    diag.event("preview.render_selected", enabled=do_render, requested=renderer, backend=backend,
               sample_count=len(samples))
    if not do_render:
        warnings.append("rendering skipped: plan summaries and verification only")
    elif backend is None:
        warnings.append("no renderer found (Word or LibreOffice): plan summaries and verification only")
        detail(f"no renderer: {render_mod.unavailable_reason(renderer)}")
    elif backend != "word":
        warnings.append(f"preview rendered with {backend}; Word's layout can differ (Word is the reference on "
                        f"your machine when installed)")
    if backend:
        detail(f"renderer: {render_mod.describe(backend)}")
    done = [s for s in samples if s.output]
    if not (backend and done):
        _sample_geometry(samples, Path(out_dir), None, None, sample_labels(samples), warn, detail, block)
        return backend
    rdir = Path(out_dir) / "render"
    # renderers name a PDF after its file's stem: samples whose names collide are rendered from numbered copies
    stems = [s.source.stem.casefold() for s in done]
    unique = [s.source.stem if stems.count(s.source.stem.casefold()) == 1 else f"{i:02d} {s.source.stem}"
              for i, s in enumerate(done)]

    def staged(path: Path, side: str, name: str) -> Path:
        if path.stem == name:
            return path
        copy = rdir / "staged" / side / f"{name}{path.suffix}"
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        return copy
    src_in = [staged(s.source, "source", n) for s, n in zip(done, unique)]
    out_in = [staged(s.output, "output", n) for s, n in zip(done, unique)]
    # a renderer failure (Word that will not start, a file LibreOffice chokes on) is a warning beside the plan
    # summaries and verification, never a preview that dies without them
    labels = sample_labels(samples)              # two folders' Spec.docx are told apart in the warnings too
    # the cache beside the session folders: what this preview renders, the run does not render again (and a source
    # rendered in an earlier session is not rendered again now) — keyed by the file's bytes, never its name
    cache = render_mod.RenderCache.for_out_dir(Path(out_dir))
    if cache is not None:
        detail(f"render cache: {cache.root}")
    srcs = render_mod.render_pdfs(src_in, rdir / "source", backend, 2, log=warn, detail=detail, cache=cache)
    outs = render_mod.render_pdfs(out_in, rdir / "output", backend, 2, log=warn, detail=detail, cache=cache)
    for s, a, b, name in zip(done, src_in, out_in, unique):
        sp, op = srcs.get(a), outs.get(b)
        if not sp or not op:
            warn(f"{labels[str(Path(s.source))]}: render failed")
            continue
        s.source_pdf, s.output_pdf = Path(sp), Path(op)
        s.look_pages = [1]                     # page 1 at least, even when the comparison below cannot run
        try:
            from .verify.geometry import fitz
            with fitz.open(str(sp)) as before, fitz.open(str(op)) as after:
                s.pages_before, s.pages_after = before.page_count, after.page_count
            if s.pages_after != s.pages_before:
                warn(f"{labels[str(Path(s.source))]}: pages {s.pages_before} → {s.pages_after} "
                     "in the preview render; inspect the reflow before approving")
            g = geom.compare_pdfs(sp, op)
            moved = [p.page for p in g.pages if p.max_dy > 0.5 or p.words_only_in_out or p.words_only_in_ref]
            s.look_pages = sorted({1} | set(moved[:1]))
            for page in s.look_pages:
                img = side_by_side(sp, op, page, Path(out_dir) / "images" / f"{name} p{page}.png")
                if img:
                    s.images.append(img)
        except Exception as e:  # noqa: BLE001 — pictures are a nicety; the plan and verification stand
            warn(f"{labels[str(Path(s.source))]}: the before/after pictures could not be made "
                 f"({type(e).__name__}: {e})")
            block(traceback.format_exc())
    _sample_geometry(samples, Path(out_dir), backend, cache, labels, warn, detail, block)
    return backend


class _GeometryLog:
    """What ``batch.geometry_references`` logs with, over the preview log's lines."""

    def __init__(self, detail, block):
        self.detail, self.block = detail, block

    def __call__(self, msg: str) -> None:
        self.detail(msg)


def _sample_geometry(samples: List[SampleView], out_dir: Path, backend: Optional[str], cache, labels: Dict[str, str],
                     warn, detail, block) -> None:
    """V6 and V7 on the samples that rendered, with the run's own code (``batch.compare_pair`` over the references
    ``batch.geometry_references`` renders), so a sample's verdict here is the one the run will give: a layout that
    moved fails the sample — a custom method's sample too, by the plan its trial predicted
    (``codegen.FileTrial.plan``). A sample without a render says the layout is checked at the run and that no pages
    were rendered here; one whose pages were rendered but whose plan the preview does not hold (a custom method's
    trial kept without it; a chain's step whose reference is its own input is told apart earlier,
    ``GEOMETRY_AT_RUN_STEPS``) says its pages were rendered and why the layout check waits. A comparison the preview cannot make is a warning beside the
    plan, never a failed sample: the run makes it again, and the sample's note says the pages were rendered."""
    from .batch import RenderPair, compare_pair, geometry_references
    from .report import words_missing_sentence
    pairs: List[Tuple[SampleView, RenderPair]] = []
    for s in samples:
        if s.geometry is not None or s.exception is not None or not s.output:
            continue
        if not s.source_pdf or not s.output_pdf or not backend:
            s.geometry = dict(GEOMETRY_AT_RUN)
            continue
        if s.plan is None:
            s.geometry = dict(GEOMETRY_RENDERED_AT_RUN)
            continue
        pairs.append((s, RenderPair(Path(s.source), Path(s.output), s.plan, Path(s.source_pdf), Path(s.output_pdf),
                                    Path(s.output).name)))
    if not pairs:
        return
    log = _GeometryLog(detail, block)
    try:
        refs = geometry_references([pair for _, pair in pairs], out_dir, backend, 2, cache, detail, log)
    except Exception as e:  # noqa: BLE001 — the references are the run's to make again
        warn(f"the page-layout references could not be rendered ({type(e).__name__}: {e}); the layout is checked "
             f"when the copies are created")
        block(traceback.format_exc())
        for s, _pair in pairs:
            s.geometry = dict(GEOMETRY_RENDERED_NOT_COMPARED)
        return
    for s, pair in pairs:
        name = labels[str(Path(s.source))]
        try:
            v6, v7, _geometry = compare_pair(pair, refs, backend)
        except Exception as e:  # noqa: BLE001
            warn(f"{name}: the rendered pages could not be compared ({type(e).__name__}: {e}); the layout is "
                 f"checked when the copies are created")
            block(traceback.format_exc())
            s.geometry = dict(GEOMETRY_RENDERED_NOT_COMPARED)
            continue
        s.checks["V6"], s.checks["V7"] = v6.status, v7.status
        s.geometry = {"status": v7.status, "note": geometry_note(pair.plan, v7)}
        if v7.status == "FAIL":
            s.passed = False
        # V6's warning, from the record: words the original's pages show that the copy's do not — beside the sample
        # (the page's warnings), on the sample (the chat, the visual check), never a failed sample
        missing = words_missing_sentence(v6)
        if missing:
            s.render_warnings.append(missing)
            warn(f"{name}: {missing}")
        diag.event("preview.geometry", sample=str(s.source), v6=v6.status, v7=v7.status, summary=v7.summary)
        detail(f"sample {name}: V6 {v6.status} — {v6.summary}; V7 {v7.status} — {v7.summary}")


@dataclass
class GeneratedPreview:
    """What the user sees of a one-off custom transform before Run: its sample files (tested in the sandbox and
    verified), their before/after renders and Claude's visual check — the same as a spec's preview minus the per-file
    plan table (a generated op is proven per file only when it runs)."""
    out_dir: Path
    name: str
    samples: List[SampleView] = field(default_factory=list)
    renderer: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    review: Any = None
    review_ack: str = ""
    log_path: Optional[Path] = None                               # logs/preview.log
    rendered: bool = False                                        # made with Render pages on (pages + visual check)

    def to_text(self) -> str:
        out = [f"PREVIEW — one-off custom transform {self.name!r} (AI-written, tested on sample files)"]
        out += [f"WARNING: {w}" for w in self.warnings]
        out.append("SAMPLES (run in the sandbox and verified; sources untouched):")
        labels = sample_labels(self.samples)
        for s in self.samples:
            failed = [k for k, v in s.checks.items() if v == "FAIL"]
            out.append(f"   {labels[str(Path(s.source))]}: verification {'PASS' if s.passed else 'FAIL ' + str(failed)}"
                       + (f"; pages: {s.pages_before} → {s.pages_after}" if s.pages_before is not None else "")
                       + (f"; images: {[i.name for i in s.images]}" if s.images else "")
                       + geometry_text(s))
        words = text_changes_lines(self.samples)
        if words:
            out += ["WORDS (the independent checker's reading of the sample copies, V2):"] + [f"   {w}" for w in words]
        if self.review is not None:
            out += ["", self.review.to_text()]
        from .readable import REPORT_BYTES, bounded
        return bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")

    def to_dict(self):
        return {"name": self.name, "renderer": self.renderer, "warnings": self.warnings,
                "samples": [s.to_dict() for s in self.samples],
                "visual_review": self.review.to_dict() if self.review is not None else None,
                "log": "logs/preview.log"}


def preview_generated(g: Any, out_dir: Path, renderer: str = "auto", do_render: bool = True) -> GeneratedPreview:
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl",
                    run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview.generated", out_dir=str(out_dir), renderer=renderer, do_render=do_render):
        return _preview_generated(g, out_dir, renderer, do_render)


def _preview_generated(g: Any, out_dir: Path, renderer: str, do_render: bool) -> GeneratedPreview:
    """The custom transform's preview, from the trials that made it pass: outputs copied out of the sandbox under names
    unique across the samples, rendered beside their sources. ``logs/preview.log`` has each sample's verification and
    what the renderer said."""
    from .batch import output_names
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = getattr(g, "name", "") or "generated"
    log = _preview_log(out_dir, f"one-off custom transform {name!r}")
    try:
        gp = GeneratedPreview(out_dir=out_dir, name=name, log_path=log.path, rendered=bool(do_render))
        trials = list(getattr(g, "trials", None) or [])
        names = output_names([Path(t.source) for t in trials])
        for t in trials:
            # a trial that passed with failed checks (Obliterate mode: FileTrial.unverified) may be delivered, but its
            # preview shows the checks it failed, never a pass
            # the plan the method predicted rides with its sample, so its rendered pages get V6 and V7 here as a
            # built-in sample's do (``_sample_geometry``) and the run's verdict is the one shown (the deep-dive stress
            # test's D3: a custom sample's pages were rendered and never compared)
            sv = SampleView(source=Path(t.source), passed=bool(t.passed) and not getattr(t, "unverified", None),
                            checks={c.id: c.status for c in t.record.checks} if t.record is not None else {},
                            plan=getattr(t, "plan", None) if t.record is not None else None)
            if t.record is not None:
                # the words the copy adds and removes, from the verifier's own V2 record — the method's plan has no say
                sv.text_changes = text_changes_of(t.record.to_dict())
                _log_verification(log, Path(t.source).name, t.record)
                for line in text_changes_lines([sv]):
                    log.detail(f"sample {Path(t.source).name}: {line}")
            else:
                log.detail(f"sample {Path(t.source).name}: no verification record — "
                           + "; ".join(str(x) for x in (getattr(t, "problems", None) or ["(no problems recorded)"])))
            if t.output and Path(t.output).is_file():
                sv.output = out_dir / "samples" / names[str(t.source)]
                sv.output.parent.mkdir(parents=True, exist_ok=True)
                sv.output.write_bytes(Path(t.output).read_bytes())
            gp.samples.append(sv)
        gp.renderer = render_samples(gp.samples, out_dir, renderer, do_render, gp.warnings, log=log)
        write_preview_files(gp)
        return gp
    except BaseException as e:
        log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        raise
    finally:
        log.close()


def write_preview_files(pv: Any) -> None:
    """PREVIEW.md and preview.json beside the samples (rewritten when Claude's visual check arrives — preview.json keeps
    the approval inputs, so a refused run can still say what changed)."""
    with diag.span("preview.records", out_dir=str(pv.out_dir)):
        from .readable import write_report
        write_report(pv.out_dir / "PREVIEW.md", "```\n" + pv.to_text() + "\n```\n", "preview.json")
        (pv.out_dir / "preview.json").write_text(json.dumps(pv.to_dict(), indent=2, ensure_ascii=False, default=str),
                                                 encoding="utf-8")


INPUT_CHANGED = "An input changed while the preview was being prepared; press Preview again before Run."
INPUT_CHANGED_NOTE = ("A file changed while this preview was being made: make the preview again before you create "
                      "the copies.")


def _input_changed(pv: Preview) -> None:
    """An input that changed under the preview: its warning, and the note the window lists under Worth knowing."""
    pv.warnings.append(INPUT_CHANGED)
    pv.worth_knowing.append({"name": "", "text": INPUT_CHANGED_NOTE})


def make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                 do_render: bool = True) -> Preview:
    from .batch import check_inputs_outside, expand_inputs
    from .editspec import spec_input_files, validate_spec
    spec = copy.deepcopy(spec)
    validate_spec(spec)
    check_inputs_outside([*expand_inputs(inputs), *spec_input_files(spec)], out_dir)
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl",
                    run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview", out_dir=str(out_dir), renderer=renderer, do_render=do_render) as outcome:
        result = _make_preview(spec, inputs, out_dir, renderer, do_render)
        outcome.update(samples=len(result.samples), exceptions=len(result.batch_plan.exceptions),
                       warnings=result.warnings)
        return result


def _preview_sample(source: str, spec: Dict[str, Any], output: str, approved_md5: str,
                    context: Optional[Dict[str, Any]] = None, events: Optional[str] = None) -> Dict[str, Any]:
    """One sample of ``make_preview``: read, open, plan, apply, write ``output``, verify — here, or in a spawned worker
    (``batch.map_files``), where ``events`` is its own journal and ``context`` the preview's correlation. Plain data
    back: ``passed``, ``checks``, ``record`` (the verification record as a dict), ``exception``, ``output`` (set once
    the copy was being written) and ``changed`` (the bytes read are not the ones the approval token was made for)."""
    from . import diagnostics as diag
    if events is not None:
        with diag.scope(Path(events), **(context or {})):
            return _preview_sample(source, spec, output, approved_md5)
    from .verify import run_suite
    f = Path(source)
    r: Dict[str, Any] = {"passed": None, "checks": {}, "record": None, "exception": None, "output": None,
                         "changed": False, "plan": None}
    with diag.scope(source=str(f)), diag.span("preview.sample"):
        stage, data = "open", None
        try:
            with diag.span("preview.sample.read"):
                data = f.read_bytes()
            if hashlib.md5(data).hexdigest() != approved_md5:
                r["changed"] = True
            with diag.span("preview.sample.open"):
                pkg = DocxPackage(data, name=str(f))
            stage = "plan"
            with diag.span("preview.sample.plan"):
                plan, frags = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
            if plan.output_name:
                # V10 checks the actual output filename in preview just as in a run. Keep a directory per sample
                # so two documents requesting one name cannot overwrite each other's preview evidence.
                output = str(Path(output).parent / Path(output).stem / plan.output_name)
            stage = "apply"
            with diag.span("preview.sample.apply"):
                out = apply_spec(pkg, frags).output_bytes()
            stage = "write"
            r["output"] = output
            with diag.span("preview.sample.write", output=output, size=len(out)):
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_bytes(out)
            stage = "verify"
            with diag.span("preview.sample.verify"):
                rec = run_suite(pkg.data, out, plan.to_dict(), str(f), output)
            r.update(passed=rec.passed, checks={c.id: c.status for c in rec.checks}, record=rec.to_dict(),
                     plan=plan.to_dict())
        except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
            r["exception"] = diag.exception_record(e, stage, data)
            diag.event("preview.sample_failed", level="error", source=str(f), stage=stage, exception=r["exception"])
        diag.event("preview.sample_finished", source=str(f), passed=r["passed"], exception=r["exception"],
                   checks=r["checks"])
    return r


def _make_preview(spec: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str,
                  do_render: bool) -> Preview:
    from . import diagnostics as diag
    from .batch import PROCESS_MIN_FILES, check_inputs_outside, expand_inputs, map_files, output_names, worker_plan
    from .editspec import is_template_file, spec_input_files, spec_templates, validate_spec
    from .verify.suite import VerificationRecord
    from .input_snapshots import changed_inputs
    spec = copy.deepcopy(spec)
    validate_spec(spec)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    check_inputs_outside([*files, *spec_input_files(spec)], out_dir)
    templates = spec_templates(spec)
    planned = [f for f in files if not is_template_file(f, templates)]
    token_inputs = approval_inputs(spec, planned)
    token = _approval_token_from_inputs(spec, token_inputs)
    out_dir.mkdir(parents=True, exist_ok=True)
    log = _preview_log(out_dir, repr(spec.get("request")))
    try:
        execution_spec, snapshots = _snapshot_preview_inputs(spec, token_inputs, out_dir)
        with diag.span("preview.plan_batch"):
            bp = plan_batch([str(f) for f in planned], execution_spec)
        bp.spec, bp.skipped = spec, [str(f) for f in files if f not in planned]
        pv = Preview(out_dir=out_dir, batch_plan=bp, log_path=log.path, token=token, token_inputs=token_inputs,
                     input_snapshots=snapshots)
        pv.warnings += left_elsewhere_warnings(bp)     # the same words where this plan does not change them (C3)
        pv.worth_knowing += left_elsewhere_notes(bp)
        input_changed = (changed_since_preview(spec, planned, token, token_inputs) is not None or
                         bool(changed_inputs(snapshots)) or
                         {str(f.source.resolve()) for f in bp.files} != set(token_inputs["files"]) or
                         any(f.source_md5 != token_inputs["files"][str(f.source.resolve())][2] for f in bp.files))
        log.detail(f"planned {len(bp.files)} file(s) in {bp.seconds:.2f}s: {len(bp.files) - len(bp.exceptions)} "
                   f"plan cleanly, {len(bp.exceptions)} exception(s)")
        for fpl in bp.files:
            if fpl.status == "planned":
                log.detail(f"  {fpl.source.name}: planned [{fpl.ms:.0f} ms] — " + " · ".join(fpl.summary))
            else:
                e = fpl.exception or {}
                log.detail(f"  {fpl.source.name}: EXCEPTION [{e.get('code')}] at {e.get('stage')}"
                           + (f", op #{e['op_number']} {e.get('op')}" if e.get("op_number") else "")
                           + f": {e.get('reason')}" + (f" (at {e['where']})" if e.get("where") else ""))
                if e.get("traceback") and e.get("kind") in ("internal-error", "environment"):
                    log.block(e["traceback"])
        picks = _pick_samples(bp)
        diag.event("preview.samples_selected", sources=[str(f) for f in picks], planned_files=len(bp.files))
        # names unique across the samples (two folders can each hold a "Spec.docx"): one sample's output never
        # overwrites another's, so each before/after picture and the visual check pair a source with its own output
        names = output_names(picks)
        # The samples side by side in worker processes when there are enough of them (batch.map_files), each with its
        # own events file; one or two run here, in the preview's own journal.
        shared = len(picks) >= PROCESS_MIN_FILES and worker_plan(None, 0, len(picks))[0] == "process"
        done = map_files(_preview_sample, [
            (str(f), execution_spec, str(out_dir / "samples" / names[str(f)]),
             token_inputs["files"][str(Path(f).resolve())][2],
             {**diag.context(), "source": str(f)} if shared else None,
             str(diag.trace_path(out_dir, names[str(f)]).with_suffix(".events.jsonl")) if shared else None)
            for f in picks])
        for f, r in zip(picks, done):
            sv = SampleView(source=f, passed=r["passed"], checks=r["checks"], exception=r["exception"],
                            plan=r.get("plan"))
            if r["output"]:
                sv.output = Path(r["output"])
            input_changed = input_changed or r["changed"]
            if r["record"] is not None:
                _log_verification(log, f.name, VerificationRecord.from_dict(r["record"]))
            if sv.exception is not None:
                log.detail(f"sample {f.name}: EXCEPTION [{sv.exception['code']}] at {sv.exception.get('stage')}: "
                           f"{sv.exception['reason']}")
                if sv.exception.get("traceback") and sv.exception.get("kind") != "refusal":
                    log.block(sv.exception["traceback"])
            pv.samples.append(sv)
        input_changed = (input_changed or changed_since_preview(spec, planned, token, token_inputs) is not None
                         or bool(changed_inputs(snapshots)))
        pv.renderer = render_samples(pv.samples, out_dir, renderer, do_render, pv.warnings, log=log)
        if input_changed or changed_since_preview(spec, planned, token, token_inputs) is not None or changed_inputs(snapshots):
            pv.token = ""  # an observed mismatch cannot become approved by restoring an earlier file later
            _input_changed(pv)
        (out_dir / "spec.json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
        write_preview_files(pv)
        log.detail(f"approval token {pv.token}")
        return pv
    except BaseException as e:
        log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
        log.block(traceback.format_exc())
        raise
    finally:
        log.close()


# ---- chains: several specs, one after another (chain.py) ----------------------------------------------------------

def _replaced_after(stages: Sequence[Dict[str, Any]], n: int) -> List[str]:
    """``planner.replaced_after``: the find texts the steps after step ``n`` replace again (C3)."""
    from .planner import replaced_after
    return replaced_after(stages, n)


@dataclass
class ChainPreview(Preview):
    """A chain's preview: stage 1's plan on every file (the later stages are planned on each file once the stage before
    it ran, so only the samples go through every stage here), the samples applied and verified stage by stage in
    memory, their before/after renders (the source against the file after the LAST stage) and Claude's visual check.
    The approval token is over the chain (``chain.chain_key``: every stage's spec and templates) and the files."""
    chain: Dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        from .chain import chain_name, stage_name
        stages = self.chain.get("stages") or []
        out = [f"PREVIEW — chain «{chain_name(self.chain)}»: {len(stages)} stage(s), run one after another on each file"]
        for i, st in enumerate(stages, 1):
            spec = st.get("spec") or {}
            out.append(f"  stage {i} — {stage_name(st, i)}: {spec.get('request')!r} — {spec.get('summary') or ''}")
            out += [f"     assumption: {a}" for a in spec.get("assumptions") or []]
        out += [f"WARNING: {w}" for w in self.warnings]
        out.append("")
        out.append("EVERY STEP planned on every file (each later step on the bytes the step before it would make; a "
                   "file a step refuses is an EXCEPTION naming the step; the samples below were applied and verified "
                   "through every step):")
        out.append(self.batch_plan.to_text(per_file=False))
        out.append("\nSAMPLES (every stage applied and verified in memory, one after another; sources untouched):")
        labels = sample_labels(self.samples)
        for sv in self.samples:
            lab = labels[str(Path(sv.source))]
            if sv.exception:
                out.append(f"   {lab}: {sv.stages_text()} — EXCEPTION [{sv.exception['code']}] {sv.exception['reason']}")
                continue
            failed = [k for k, v in sv.checks.items() if v == "FAIL"]
            out.append(f"   {lab}: {sv.stages_text()} → verification {'PASS' if sv.passed else 'FAIL ' + str(failed)}"
                       + (f"; pages: {sv.pages_before} → {sv.pages_after}" if sv.pages_before is not None else "")
                       + (f"; images: {[i.name for i in sv.images]}" if sv.images else "")
                       + geometry_text(sv))
            out += [f"     final requirement {entry['index']} {entry['status']}: {entry['summary']}"
                    for entry in sv.outcomes]
        if self.review is not None:
            out.append("")
            out.append(self.review.to_text())
        from .readable import REPORT_BYTES, bounded
        return (bounded("\n".join(out), REPORT_BYTES - 200, "preview.json")
                + f"\napproval token: {self.token}")

    def to_dict(self):
        d = super().to_dict()
        d["chain"] = self.chain
        return d


def make_chain_preview(chain: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str = "auto",
                       do_render: bool = True, *, continue_failed: bool = False) -> ChainPreview:
    with diag.scope(run_id=diag.context().get("run_id") or diag.new_id(), preview_id=diag.new_id()), \
            diag.span("preview.chain", out_dir=str(out_dir), renderer=renderer, do_render=do_render):
        return _make_chain_preview(chain, inputs, out_dir, renderer, do_render, continue_failed)


def _make_chain_preview(chain: Dict[str, Any], inputs: Sequence[str], out_dir: Path, renderer: str,
                        do_render: bool, continue_failed: bool = False) -> ChainPreview:
    """``make_preview`` for a chain: every stage planned on every file (``planner.plan_batch_chain``: a file a later
    stage refuses is an exception of the batch plan, under Left untouched), the samples (``_pick_samples`` of that
    plan) run through every stage in memory — plan → apply → verify, each stage on the bytes the stage before it verified; a
    stage that refuses or fails verification stops that sample there (``continue_failed``, Obliterate mode: a failed
    check marks the sample failed and the next stage reads its output, as ``run_chain(deliver_unverified=True)`` will
    do) —, the final output written under
    ``samples/`` (unique names), rendered beside the source. Writes ``chain.json``, PREVIEW.md and preview.json;
    ``logs/preview.log`` has every stage's verdict per sample."""
    from . import diagnostics as diag
    from .batch import check_inputs_outside, expand_inputs, output_names
    from .chain import chain_key, chain_name, chain_templates, stage_name, validate_chain
    from .editspec import is_template_file, spec_input_files
    from .verify import run_suite
    from .input_snapshots import changed_inputs
    from .verify.outcomes import assert_outcomes
    chain = copy.deepcopy(chain)
    validate_chain(chain)
    out_dir = Path(out_dir)
    candidates = expand_inputs(inputs)
    auxiliary = [path for stage in chain["stages"] for path in spec_input_files(stage["spec"])]
    check_inputs_outside([*candidates, *auxiliary], out_dir)
    templates = chain_templates(chain)
    files = [f for f in candidates if not is_template_file(f, templates)]
    key = chain_key(chain)
    token_inputs = approval_inputs(key, files)
    token = _approval_token_from_inputs(key, token_inputs)
    with diag.scope(out_dir / diag.LOGS / "events.jsonl"), diag.span("preview.chain.execute"):
        out_dir.mkdir(parents=True, exist_ok=True)
        log = _preview_log(out_dir, f"chain {chain_name(chain)!r}")
        try:
            execution_chain, snapshots = _snapshot_preview_inputs(chain, token_inputs, out_dir)
            stages = execution_chain["stages"]
            bp = plan_batch_chain([str(f) for f in files], execution_chain)
            bp.spec = chain["stages"][0]["spec"]
            pv = ChainPreview(out_dir=out_dir, batch_plan=bp, chain=chain, log_path=log.path,
                              token=token, token_inputs=token_inputs, input_snapshots=snapshots)
            pv.warnings += left_elsewhere_warnings(bp)     # stage 1's, on every file, as far as they hold (C3)
            pv.worth_knowing += left_elsewhere_notes(bp)
            input_changed = (changed_since_preview(key, files, token, token_inputs) is not None or
                             bool(changed_inputs(snapshots)) or
                             {str(f.source.resolve()) for f in bp.files} != set(token_inputs["files"]) or
                             any(f.source_md5 != token_inputs["files"][str(f.source.resolve())][2] for f in bp.files))
            log.detail(f"every step ({len(stages)}) planned on {len(bp.files)} file(s) in {bp.seconds:.2f}s: "
                       f"{len(bp.files) - len(bp.exceptions)} plan cleanly, {len(bp.exceptions)} exception(s)")
            for fpl in bp.files:
                if fpl.status == "planned":
                    log.detail(f"  {fpl.source.name}: planned [{fpl.ms:.0f} ms] — " + " · ".join(fpl.summary)
                               + (" · " + " · ".join(fpl.later_notes) if fpl.later_notes else ""))
                else:
                    e = fpl.exception or {}
                    log.detail(f"  {fpl.source.name}: EXCEPTION [{e.get('code')}] at {e.get('stage')}"
                               + (f" (step {e['chain_stage']})" if e.get("chain_stage") else "") + f": {e.get('reason')}")
            picks = _pick_samples(bp)
            names = output_names(picks)
            for f in picks:
                sv = SampleView(source=f)
                phase, data, produced = "open", None, False
                source_name = str(f)
                last_plan, done_stages = None, 0
                sample_dir = out_dir / "samples" / Path(names[str(f)]).stem
                try:
                    data = Path(f).read_bytes()
                    if hashlib.md5(data).hexdigest() != token_inputs["files"][str(Path(f).resolve())][2]:
                        input_changed = True
                except Exception as e:  # noqa: BLE001
                    sv.exception = diag.exception_record(e, phase, None)
                    diag.event("preview.sample_failed", level="error", source=str(f), stage=phase,
                               exception=sv.exception)
                for i, st in enumerate(stages, 1):
                    if sv.exception is not None:
                        break
                    spec, label = st["spec"], stage_name(st, i)
                    with diag.scope(source=str(f), chain_stage=i, chain_stage_name=label), \
                            diag.span("preview.chain_stage"):
                        try:
                            phase = "plan"
                            pkg = DocxPackage(data, name=source_name)
                            with diag.span("preview.sample.plan"):
                                plan, frags = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
                            last_plan, done_stages = plan.to_dict(), i
                            if i > 1:                    # planned on the step before it: true for this sample
                                sv.left_later += left_notes(plan.flags, _replaced_after(stages, i))
                            phase = "apply"
                            with diag.span("preview.sample.apply"):
                                out = apply_spec(pkg, frags).output_bytes()
                            output = sample_dir / (plan.output_name or Path(source_name).name)
                            produced = True
                            phase = "verify"
                            with diag.span("preview.sample.verify"):
                                rec = run_suite(pkg.data, out, plan.to_dict(), source_name, str(output))
                            checks = {c.id: c.status for c in rec.checks}
                            sv.stages.append({"stage": i, "name": label, "passed": rec.passed, "checks": checks})
                            _log_verification(log, f"{f.name} · stage {i} ({label})", rec)
                            data = out
                            source_name = str(output)
                            sv.checks = checks
                            if not rec.passed:
                                sv.passed = False
                                if not continue_failed:
                                    break
                        except Exception as e:  # noqa: BLE001 — a refusal, or the engine's own failure: the sample's record
                            exc = diag.exception_record(e, phase, data)
                            exc["chain_stage"] = i
                            exc["reason"] = f"stage {i} ({label}): {exc.get('reason', '')}"
                            sv.exception = exc
                            diag.event("preview.sample_failed", level="error", source=str(f), chain_stage=i,
                                       stage=phase, exception=exc)
                            sv.stages.append({"stage": i, "name": label, "exception": {"code": exc.get("code"),
                                                                                        "reason": exc.get("reason")}})
                            log.detail(f"sample {f.name}: stage {i} EXCEPTION [{exc['code']}] at {phase}: {exc['reason']}")
                            if exc.get("traceback") and exc.get("kind") != "refusal":
                                log.block(exc["traceback"])
                else:
                    if sv.exception is None and sv.passed is None:
                        sv.passed = True
                if sv.passed and chain.get("assertions"):
                    sv.outcomes = assert_outcomes(data, chain["assertions"])
                    sv.checks["FINAL"] = "PASS" if all(entry["status"] == "PASS" for entry in sv.outcomes) else "FAIL"
                    sv.passed = sv.checks["FINAL"] == "PASS"
                    for entry in sv.outcomes:
                        log.detail(f"sample {f.name}: final requirement {entry['index']} {entry['status']}: {entry['summary']}")
                if produced:
                    sv.output = output
                    sv.output.parent.mkdir(parents=True, exist_ok=True)
                    sv.output.write_bytes(data)
                    # the page layout of the last step the sample went through: checked here when its reference
                    # is the template or none (no earlier render needed), or when that step read the source itself
                    reference = (last_plan or {}).get("geometry_reference", "source")
                    if done_stages == 1 or reference in ("template", "none"):
                        sv.plan = last_plan
                    else:
                        sv.geometry = dict(GEOMETRY_AT_RUN_STEPS)
                pv.samples.append(sv)
                input_changed = (input_changed or changed_since_preview(key, files, token, token_inputs) is not None
                                 or bool(changed_inputs(snapshots)))
                diag.event("preview.sample_finished", source=str(f), passed=sv.passed,
                           exception=sv.exception, checks=sv.checks, stages=sv.stages)
            pv.renderer = render_samples(pv.samples, out_dir, renderer, do_render, pv.warnings, log=log)
            if input_changed or changed_since_preview(key, files, token, token_inputs) is not None or changed_inputs(snapshots):
                pv.token = ""
                _input_changed(pv)
            (out_dir / "chain.json").write_text(json.dumps(chain, indent=2, ensure_ascii=False), encoding="utf-8")
            write_preview_files(pv)
            log.detail(f"approval token {pv.token}")
            return pv
        except BaseException as e:
            log.detail(f"THE PREVIEW STOPPED: {type(e).__name__}: {e}")
            log.block(traceback.format_exc())
            raise
        finally:
            log.close()
