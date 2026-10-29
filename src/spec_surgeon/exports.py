"""Export's three kinds (the top bar's Export menu, docs/CONVERSATIONAL_DESKTOP.md "Export"): what each one writes into
the folder the user chose, made from the edited copies the window lists.

* ``word`` — **Word (Tracked Changes ON)**: each copy as it is, byte for byte the copy the checker passed. Its tracked
  changes (the request's own and any the file already had) and its comments stay, to accept or reject in Word.
* ``accepted`` — **Word (Tracked Changes OFF)**: each copy with every tracked change accepted. Its comments stay: the
  menu names tracked changes only, and nothing is removed that the user did not ask to remove.
* ``pdf`` — **PDF (Clean)**: each copy with every comment removed and then every tracked change accepted, as a PDF
  made by Word or LibreOffice (``pdf_export``) and checked before it is handed over.

The two clean kinds are fixed specs built here in code (no AI call, no word placed), passed through the gate
(``compiler.compose_submission``), planned from each copy, applied and checked by the independent verifier exactly as
a run's copy is (``batch.run_batch``, ``chain.run_chain``), in a folder of the window's scratch folder. The menu item
names the change and the click is its approval (the owner's decision, 2026-10-29: Export replaced Safe mode's *Save
clean PDFs* button). The edited copies themselves are never changed and nothing joins the history the model hears; a
copy whose clean version cannot be made exactly, or does not pass every check, gets no file and the export says why.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import diagnostics as diag

KINDS: Tuple[str, ...] = ("word", "accepted", "pdf")
# The menu's own words: the owner named the three kinds (2026-10-29)
LABELS: Dict[str, str] = {"word": "Word (Tracked Changes ON)", "accepted": "Word (Tracked Changes OFF)",
                          "pdf": "PDF (Clean)"}
REQUESTS: Dict[str, str] = {
    "accepted": "Export Word (Tracked Changes OFF): every tracked change accepted, comments kept",
    "pdf": "Export PDF (Clean): every comment removed, every tracked change accepted",
}
# What the outcome planner and recovery are told (their prompts end with it; the conversation's own rule is in
# ``compiler.SIMPLE_EXPERIENCE``): a model that does not know Export told the owner PDFs could not be made (2026-10-28)
PROMPT_NOTE = """Saving files is the window's Export menu, never an edit, a plan or a repair:
Word (Tracked Changes ON) saves the edited copies as they are;
Word (Tracked Changes OFF) saves them with every tracked change accepted and the comments kept;
PDF (Clean) saves PDFs of them with every comment removed and every tracked change accepted.
When the user asks for PDFs, or for clean or accepted files, tell them in plain words that Export in the top bar
does it once the copies exist; never plan it for them, and never say PDFs cannot be made.
"""
EXPORT_FOLDER = "exports"               # under the window's scratch folder: one run folder per clean export
# the progress lines the window turns into its status (``document_task.STATUS_FOR_LOG``)
CLEANING = "Creating and checking the clean copies…"
SAVING_PDFS = "Saving the clean PDFs…"

_ACCEPT = {"op": "resolve_tracked_changes", "params": {"action": "accept"}}
_ACCEPT_ASSUMPTIONS = ["the body, headers, footers, footnotes and endnotes",
                       "a change Word must settle itself (a table cell inserted or merged, a change inside an "
                       "equation) leaves the document without an exported file",
                       "the edited copies the next request works on keep their comments and tracked changes"]


def _spec(request: str, summary: str, mode: str, ops: List[dict], assumptions: List[str]) -> dict:
    return {"request": request, "summary": summary, "mode": mode, "ops": ops, "assumptions": assumptions}


def clean_spec(kind: str) -> Tuple[dict, str]:
    """``(spec, request)``: the fixed plan of a clean kind. ``pdf`` is two ordered steps, comments first, so a comment
    anchored in text a tracked deletion takes away no longer stops the tracked changes from being accepted."""
    request = REQUESTS.get(kind)
    if request is None:
        raise ValueError(f"{kind!r} is not a clean export")
    accept = _spec(request, "Accept every tracked change: insertions, deletions, moves and formatting changes.",
                   "untracked", [dict(_ACCEPT, params=dict(_ACCEPT["params"]))], list(_ACCEPT_ASSUMPTIONS))
    if kind == "accepted":
        return accept, request
    stages = [
        {"name": "Remove every comment",
         "spec": _spec(request, "Remove every comment with its replies.", "formatting",
                       [{"op": "remove_comments", "params": {"which": "all"}}],
                       ["a document without comments is left as it is"])},
        {"name": "Accept every tracked change", "spec": accept},
    ]
    return {"summary": "Remove every comment, then accept every tracked change, then save a PDF of each copy.",
            "stages": stages}, request


@dataclass
class Clean:
    """What ``make_clean`` made: for each document (keyed as the caller keyed its copies) the clean file — a .docx for
    ``accepted``, a PDF for ``pdf`` — or why it has none; and the PDFs' renderer."""
    kind: str
    run_dir: Path
    made: Dict[str, Path] = field(default_factory=dict)
    failed: Dict[str, str] = field(default_factory=dict)
    renderer_name: str = ""


def _why_not(item) -> str:
    """Why a copy's clean version was not made: the engine's reason in plain words (``ui_feedback.refusal_message``:
    no operation or step names), without the part it was found in."""
    from .ui_feedback import refusal_message
    exc = getattr(item, "exception", None) or {}
    reason = ""
    if exc or getattr(item, "reason", ""):
        reason = refusal_message(exc or {"code": getattr(item, "code", ""), "reason": item.reason,
                                         "details": getattr(item, "details", {})})
        reason = re.sub(r"^(?:word|customXml)/[\w./-]+\.xml:\s*", "", reason)
    record = getattr(item, "record", None)
    if not reason and record is not None and not record.passed:
        failed = [ch.id for ch in record.checks if ch.status == "FAIL"]
        reason = "it did not pass the independent checks" + (f" ({', '.join(failed)})" if failed else "")
    return reason.strip().rstrip(".") or "it did not pass the independent checks"


def make_clean(copies: Dict[str, Path], kind: str, run_dir: Path, *, render: bool = True,
               log: Optional[Callable[[str], None]] = None) -> Clean:
    """The clean version of each copy in ``copies`` ({key: copy}) for ``kind`` (``accepted`` or ``pdf``), made in
    ``run_dir`` (a new folder): the fixed spec through the gate, then a checked run over the copies, copies only; for
    ``pdf`` a PDF of each clean copy that passed. Only a clean copy that passed every check is used (never one
    delivered unverified); the copies are read, never written."""
    from . import compiler
    from .batch import run_batch
    from .chain import run_chain
    from .editspec import is_chain
    log = log or (lambda message: None)
    run_dir = Path(run_dir)
    result = Clean(kind, run_dir)
    sources = {key: Path(copy) for key, copy in copies.items()}
    if not sources:
        return result
    spec, request = clean_spec(kind)
    files = list(sources.values())
    with diag.span("export.clean", kind=kind, file_count=len(files), run_dir=str(run_dir)) as outcome:
        planned, errors = compiler.compose_submission(spec, compiler._Batch(files, None), [request])
        if errors and not errors[0].startswith(compiler.PLANS_ON_NONE):
            # a fixed spec the gate refuses outright: every document says why, and nothing runs
            why = "; ".join(errors[:2]).rstrip(".")
            result.failed = {key: why for key in sources}
            outcome.update(refused=why)
            return result
        names = [str(f) for f in files]
        log(CLEANING)
        if is_chain(planned):
            run = run_chain(names, planned, run_dir, renderer="auto", do_render=render, in_place=False, progress=log)
        else:
            run = run_batch(names, "edit-spec", {}, run_dir, renderer="auto", do_render=render, in_place=False,
                            progress=log, spec=planned)
        by_copy = {str(path.resolve()): key for key, path in sources.items()}
        clean: Dict[str, Path] = {}
        for item in run.files:
            key = by_copy.get(str(Path(item.source).resolve()))
            if key is None:
                continue
            made = item.delivered_to or (item.output if getattr(item, "noop", False) else None)
            if item.status == "PASS" and not getattr(item, "unverified", None) and made and Path(made).is_file():
                clean[key] = Path(made)
            else:
                result.failed[key] = _why_not(item)
                diag.event("export.file_failed", level="warning", kind=kind, source=str(item.source),
                           reason=result.failed[key])
        for key in sources:
            if key not in clean and key not in result.failed:
                result.failed[key] = "the run stopped before its copy was made"
        if kind == "accepted":
            result.made = clean
        else:
            from .pdf_export import PDF_FOLDER, export_pdfs
            from .render import RenderCache
            log(SAVING_PDFS)
            pdfs = export_pdfs({Path(key): path for key, path in clean.items()}, run_dir / PDF_FOLDER,
                               cache=RenderCache.for_out_dir(run_dir), log=log)
            result.renderer_name = pdfs.renderer_name
            result.made = {str(source): Path(pdf) for source, pdf in pdfs.pdfs.items()}
            result.failed.update({str(source): f"its PDF could not be made: {str(why).rstrip('.')}"
                                  for source, why in pdfs.failed.items()})
        outcome.update(made=len(result.made), failed=len(result.failed))
    return result
