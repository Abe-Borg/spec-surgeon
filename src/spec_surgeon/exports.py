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

**Export anyway** (Obliterate mode alone; the owner's decision of 2026-11-10, docs/BACKLOG.md O7): when a clean kind
left a MARKED copy without a file (UNVERIFIED, UNPROVEN or SALVAGED: a copy no check proves as it is), the menu
offers that kind made by Word instead (``make_anyway``). Word runs a fixed list of its own named commands
(``ANYWAY_ACTIONS``: accept every tracked change; for the PDF, delete every comment first) on a private copy of the
edited copy and saves it; for the PDF that file is then rendered and the PDF looked at as any clean PDF is. Nothing is
planned, predicted or passed through the gate, so the file is UNCHECKED and the summary names it so, with Word's own
count and what the independent reader found in the file Word saved (``reading``): whether its words are the edited
copy's read with every tracked change accepted, whether a tracked change is left, whether it opens as a package. The
choice is fixed here in code and named by the menu, never a model's; the edited copies are read and never written.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

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

# Export anyway (Obliterate mode alone, module docstring): the Word commands each clean kind is made with, in order —
# ids of ``word_actions.MENU``, fixed here; and the menu's words for the choice
ANYWAY_ACTIONS: Dict[str, Tuple[str, ...]] = {"accepted": ("accept_changes",),
                                              "pdf": ("delete_comments", "accept_changes")}
ANYWAY_LABELS: Dict[str, str] = {"accepted": "Word (Tracked Changes OFF), made by Word",
                                 "pdf": "PDF (Clean), made by Word"}
ANYWAY_DOES: Dict[str, str] = {
    "accepted": "Word accepts every tracked change on a private copy and saves it; comments stay.",
    "pdf": "Word deletes every comment and accepts every tracked change on a private copy, and the PDF is made from "
           "that.",
}
UNCHECKED = "UNCHECKED"                 # the word the summary names such a file by
WORD_MAKING = "Word is making the clean copies…"
# what ``PDF_REPORT.md`` says its PDFs were made from, for these
ANYWAY_PDF_BASIS = ("Each PDF was made from a file Microsoft Word saved after deleting every comment and accepting "
                    "every tracked change in a private copy of the edited copy (Export anyway, Obliterate mode): no "
                    "check of the app's proves that file. Each PDF opens, has pages and carries no note.")

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
                                         "details": getattr(item, "details", {})}, step=False)
        reason = re.sub(r"^(?:word|customXml)/[\w./-]+\.xml:\s*", "", reason)
    record = getattr(item, "record", None)
    if not reason and record is not None and not record.passed:
        failed = [ch.id for ch in record.checks if ch.status == "FAIL"]
        reason = "it did not pass the independent checks" + (f" ({', '.join(failed)})" if failed else "")
    return reason.strip().rstrip(".") or "it did not pass the independent checks"


# where a tracked change can stay after the accept step, in words, by the part's name without its number
# (``pending_changes``)
_PART_WORDS = {"styles": "the style definitions", "numbering": "the list definitions", "comments": "the comments",
               "document": "the text", "header": "a header", "footer": "a footer", "footnotes": "the footnotes",
               "endnotes": "the endnotes"}


def _pending_where(path: Path) -> List[str]:
    """Where a file still carries tracked changes, in words, each with its count; [] when it carries none."""
    from .ooxml import count_revisions
    from .package import DocxPackage
    pkg = DocxPackage.from_path(path)
    where: List[str] = []
    for part in pkg.wordprocessing_xml_parts():
        n = sum(count_revisions(pkg.xml(part)).values())
        if n:
            stem = Path(part).stem
            where.append(f"{_PART_WORDS.get(stem.rstrip('0123456789'), stem)} ({n})")
    return where


def pending_changes(path: Path) -> str:
    """'' when a clean copy carries no tracked change in any of its parts; otherwise where some stay, in words. The
    accept step settles the tracked changes in the text, never one in a style or a list definition
    (``resolve_tracked_changes`` flags those and leaves them for Word); a copy that kept any is not the file the menu
    promises, and an Export has no preview to show that flag (Codex review on PR #150)."""
    where = _pending_where(path)
    if not where:
        return ""
    return (f"tracked changes stay pending in {' and '.join(where)}, which the accept step leaves as they are — "
            f"accept them in Word, then export again")


def make_clean(copies: Dict[str, Path], kind: str, run_dir: Path, *, render: bool = True,
               log: Optional[Callable[[str], None]] = None) -> Clean:
    """The clean version of each copy in ``copies`` ({key: copy}) for ``kind`` (``accepted`` or ``pdf``), made in
    ``run_dir`` (a new folder): the fixed spec through the gate, then a checked run over the copies, copies only; for
    ``pdf`` a PDF of each clean copy that passed. Only a clean copy that passed every check and carries no tracked
    change left anywhere (``pending_changes``) is used, never one delivered unverified; the copies are read, never
    written."""
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
                why = pending_changes(Path(made))
                if not why:
                    clean[key] = Path(made)
                    continue
            else:
                why = _why_not(item)
            result.failed[key] = why
            diag.event("export.file_failed", level="warning", kind=kind, source=str(item.source), reason=why)
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


# ---- Export anyway: the clean kinds made by Word, unchecked (Obliterate mode alone) -----------------------------------

@dataclass
class Anyway(Clean):
    """What ``make_anyway`` made: as ``Clean``, and for each document Word's own account of what it did (``said``,
    Word's words) and what the app's reading of the file Word saved found that a clean version should not show
    (``read``; an empty list: nothing)."""
    said: Dict[str, List[str]] = field(default_factory=dict)
    read: Dict[str, List[str]] = field(default_factory=dict)


def _blocks_differing(record: Any, skip: Set[str]) -> int:
    """How many text blocks of the file Word saved differ from the edited copy's read with every tracked change
    accepted, by the independent reader's own comparison (the record's V2: a part with tracked changes by its
    accept-all view, any other by its text), outside the parts in ``skip``."""
    check = record.get("V2")
    data = (check.data or {}) if check is not None else {}
    views = data.get("views") if isinstance(data.get("views"), dict) else {}
    revised = {str(p) for p in views.get("parts") or []}
    entries = [e for e in data.get("actual") or [] if str(e.get("part") or "") not in revised]
    entries += list(views.get("accepted") or [])
    return sum(1 for e in entries if str(e.get("part") or "") not in skip)


def _comments_left(path: Path) -> int:
    """How many comments a file still holds (its comments parts, found by relationship)."""
    from .package import DocxPackage, W
    pkg = DocxPackage.from_path(path)
    return sum(1 for part in pkg.parts_by_kind()["comments"] for _ in pkg.xml(part).iter(W + "comment"))


def reading(record: Any, made: Path, kind: str, comment_parts: Set[str]) -> List[str]:
    """What the app's own reading of the file Word saved found that the clean kind should not show, in words: a
    check of the file that failed, text that is not the edited copy's read with every tracked change accepted (for the
    PDF outside the comments, which Word deleted), a tracked change still pending, and for the PDF a comment still
    there. [] when it found none — which proves the words and nothing else: not the formatting, the numbering, the
    fields or the layout."""
    found = [f"{c.id} ({c.name}) failed" for c in record.checks if c.status == "FAIL"]
    n = _blocks_differing(record, comment_parts if kind == "pdf" else set())
    if n:
        found.append(f"{n} block{'' if n == 1 else 's'} of text {'differs' if n == 1 else 'differ'} from your edited "
                     f"copy read with every tracked change accepted")
    try:
        where = _pending_where(made)
    except Exception as error:  # noqa: BLE001 — a file this reader cannot open is a finding, never the job's failure
        where = []
        found.append(f"its tracked changes could not be counted ({type(error).__name__})")
    if where:
        found.append(f"tracked changes are still pending in {' and '.join(where)}")
    if kind == "pdf":
        try:
            left = _comments_left(made)
        except Exception as error:  # noqa: BLE001 — as above: said, never raised
            left = 0
            found.append(f"its comments could not be counted ({type(error).__name__})")
        if left:
            found.append(f"{left} comment{' is' if left == 1 else 's are'} still in it")
    return found


def _comment_parts(copy: Path) -> Set[str]:
    from .package import DocxPackage
    try:
        return set(DocxPackage.from_path(copy).parts_by_kind()["comments"])
    except Exception:  # noqa: BLE001 — a copy whose parts cannot be listed has no comments part to set aside
        return set()


def make_anyway(copies: Dict[str, Path], kind: str, run_dir: Path, *,
                log: Optional[Callable[[str], None]] = None) -> Anyway:
    """The clean version of each copy in ``copies`` ({key: copy}) for ``kind`` (``accepted`` or ``pdf``), MADE BY
    WORD in ``run_dir`` (a new folder) — Export anyway, module docstring. Word opens a private copy of the copy's
    bytes (``word_actions.run_word_actions``: its private instance, macros disabled, links not updated), runs the
    kind's fixed commands (``ANYWAY_ACTIONS``) and saves; the independent reader then reads what Word saved against
    the copy (``verify.run_difference(rewritten=True)``, its record kept under ``records/``) and ``reading`` puts what
    it found into words. For ``pdf`` the file Word saved is rendered and the PDF looked at as any clean PDF is
    (``pdf_export``). Nothing is planned or predicted: no file made here is checked the way ``make_clean``'s are, and
    the caller names each as unchecked. A document Word cannot finish, or whose PDF cannot be made, has no file and
    the reason. The copies are read, never written."""
    import json

    from . import stopping, word_actions
    from .verify import run_difference
    actions = ANYWAY_ACTIONS.get(kind)
    if actions is None:
        raise ValueError(f"{kind!r} is not a clean export")
    log = log or (lambda message: None)
    run_dir = Path(run_dir)
    result = Anyway(kind, run_dir)
    sources = {key: Path(copy) for key, copy in copies.items()}
    if not sources:
        return result
    clean: Dict[str, Path] = {}
    with diag.span("export.anyway", kind=kind, file_count=len(sources), run_dir=str(run_dir),
                   actions=list(actions)) as outcome:
        log(WORD_MAKING)
        for number, (key, copy) in enumerate(sources.items(), 1):
            stopping.check("export")              # Stop lands between the documents; Word's own wait looks at it too
            work = run_dir / "word" / f"{number:03d}"
            private, made = work / "in" / copy.name, work / copy.name
            try:
                data = copy.read_bytes()
                private.parent.mkdir(parents=True, exist_ok=True)
                private.write_bytes(data)
                report = word_actions.run_word_actions(private, list(actions), made)
            except stopping.Stopped:
                raise
            except Exception as error:  # noqa: BLE001 — Word's failure is this document's reason, never the job's
                why = word_actions.failure(error, actions)
                result.failed[key] = why
                diag.event("export.file_failed", level="warning", kind=kind, anyway=True, source=str(copy), reason=why)
                continue
            if not made.is_file():
                result.failed[key] = "Word reported no error and saved no copy"
                continue
            try:
                record = run_difference(data, made.read_bytes(), str(copy), str(made), rewritten=True)
            except Exception as error:  # noqa: BLE001 — a file the reader cannot open at all is no file to hand over
                result.failed[key] = (f"the file Word saved could not be read at all ({type(error).__name__}: "
                                      f"{diag.snip(error, 200)})")
                continue
            result.said[key] = word_actions.account(report)
            result.read[key] = reading(record, made, kind, _comment_parts(copy))
            try:
                kept = run_dir / "records" / f"{copy.stem}.record.json"
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
            except OSError as error:
                diag.event("export.record_failed", level="warning", source=str(copy), reason=str(error))
            clean[key] = made
            diag.event("export.anyway_made", kind=kind, source=str(copy), found=len(result.read[key]))
        if kind == "accepted":
            result.made = clean
        elif clean:
            from .pdf_export import PDF_FOLDER, export_pdfs
            log(SAVING_PDFS)
            pdfs = export_pdfs({Path(key): path for key, path in clean.items()}, run_dir / PDF_FOLDER, prefer="word",
                               log=log, basis=ANYWAY_PDF_BASIS)
            result.renderer_name = pdfs.renderer_name
            result.made = {str(source): Path(pdf) for source, pdf in pdfs.pdfs.items()}
            result.failed.update({str(source): f"its PDF could not be made: {str(why).rstrip('.')}"
                                  for source, why in pdfs.failed.items()})
        outcome.update(made=len(result.made), failed=len(result.failed))
    return result
