"""Claude looks at rendered pages (decided 2026-10-15) — in two places, both advisory:

* ``page_pngs`` renders a batch file (or the template) and rasterises a few pages for the conversation's
  ``view_pages`` tool, so Claude can see what the user means by "the line under the header" or "like the template".
* ``review_samples`` is **Claude's visual check** of the preview: the before/after pages of the sample files (page 1
  and the first page whose words moved), with the request, in one call. Its findings are shown to the user; a
  ``block`` finding stops Run until the user has looked at it and acknowledged it.

Claude never grades. The engine's checks (V1–V10) decide PASS/FAIL from the XML; the visual check is not a check in
the verification record, can add a reason to stop and can never pass anything — not a file, not a preview, not a run.
It samples (never every page of every file) to keep the cost down. Nothing here raises into a preview, at any stage
(preparing the pages, making the client, the call, reading the answer): a renderer that cannot run, a missing key or an
API error is a ``skipped`` review with the reason (``llm.failure_text``: its kind, HTTP status and request id); any
other exception — of the client factory, of the call, or while reading Claude's answer ("Claude's answer could not be
read (…)": a ``findings`` that is not a list, an input that is not an object) — is skipped the same way, with its
traceback kept in ``VisualReview.transcript`` (visual_review.json) — the exchange as plain data, without the page
images. A skipped review has no findings: a review read part-way keeps nothing.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import llm

LONG_EDGE_PX = 2000            # ≤2000 px per side keeps every image valid in requests with more than 20 images
TARGET_DPI = 150
TOKENS_PER_IMAGE_CAP = 4784    # Opus 5.5 high-resolution tier
# view_pages' budget in estimated image tokens per conversation: about 20 letter pages (page_tokens(): 2,714 a letter
# page at 150 dpi, so 22 one at a time, 21 in calls of three); 18 pages of 11×17 (3,312 each at 117 dpi).
VIEW_BUDGET_TOKENS = 60000
SEVERITIES = ("info", "warn", "block")


def png_size(png: Path) -> tuple:
    with open(png, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{png.name} is not a PNG")
    return struct.unpack(">II", head[16:24])


def _visual_tokens(w: int, h: int) -> int:
    return min(-(-w // 28) * -(-h // 28), TOKENS_PER_IMAGE_CAP)


def est_tokens(png: Path) -> int:
    """⌈w/28⌉·⌈h/28⌉ visual tokens, capped at the model's per-image maximum."""
    return _visual_tokens(*png_size(png))


def page_dpi(width_pt: float, height_pt: float) -> int:
    """The resolution ``rasterize_pages`` renders a page of this size (points) at: TARGET_DPI, lower when the long edge
    would pass LONG_EDGE_PX."""
    return min(TARGET_DPI, int(LONG_EDGE_PX * 72 / (max(width_pt, height_pt) or 1)))


def page_tokens(width_pt: float = 612, height_pt: float = 792) -> int:
    """``est_tokens`` of a page of this size as ``rasterize_pages`` renders it (US letter by default: 1275 × 1650 px,
    2,714 tokens) — the figure the view_pages budget is explained with. The pixmap rounds outward, as PyMuPDF does."""
    dpi = page_dpi(width_pt, height_pt)
    return _visual_tokens(*(math.ceil(x * dpi / 72 - 1e-3) for x in (width_pt, height_pt)))


def image_block(png: Path) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(Path(png).read_bytes()).decode("ascii")}}


def rasterize_pages(pdf: Path, pages: Sequence[int], out_dir: Path) -> List[Path]:
    """Pages (1-based) of a PDF as PNGs no larger than LONG_EDGE_PX on either side."""
    from .verify.geometry import fitz
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out: List[Path] = []
    doc = fitz.open(str(pdf))
    try:
        for n in pages:
            if not 1 <= int(n) <= doc.page_count:
                continue
            page = doc[int(n) - 1]
            dpi = page_dpi(page.rect.width, page.rect.height)
            png = out_dir / f"p{int(n)}.png"
            page.get_pixmap(dpi=dpi).save(str(png))
            out.append(png)
    finally:
        doc.close()
    return out


def render_pdf(docx: Path, work_dir: Path, renderer: str = "auto", log: Optional[Callable[[str], None]] = None) -> Path:
    """The file rendered to PDF (Word, else LibreOffice), cached by content. Raises when nothing can render it."""
    from . import render as render_mod
    docx = Path(docx)
    key = hashlib.md5(docx.read_bytes()).hexdigest()[:12]
    folder = Path(work_dir) / key
    pdf = folder / f"{docx.stem}.pdf"
    if pdf.is_file():
        return pdf
    backend = render_mod.pick(renderer)
    if backend is None:
        raise RuntimeError("no Word or LibreOffice on this machine to render pages")
    problems: List[str] = []
    got = render_mod.render_pdfs([docx], folder, backend, 1, log=problems.append)
    if docx not in got:
        raise RuntimeError(f"{docx.name} could not be rendered ({'; '.join(problems)[:300] or backend})")
    return Path(got[docx])


def page_pngs(docx: Path, pages: Sequence[int], work_dir: Path, renderer: str = "auto") -> List[Path]:
    """``view_pages``: a file's pages as PNGs (rendered once per file content, kept in ``work_dir``)."""
    pdf = render_pdf(docx, Path(work_dir) / "renders", renderer)
    return rasterize_pages(pdf, pages, pdf.parent / "pages")


# ---- Claude's visual check of the preview ------------------------------------------------------------------------

REVIEW_SYSTEM = """You review before/after page renders of sample Word files that a deterministic engine edited for \
a user. The engine has already verified from the XML that it changed exactly what its plan predicted and nothing \
else; you cannot pass or approve anything. Your job is to catch what those checks cannot see, from the pages:
- whether the AFTER pages do what the user asked (read the request and the spec summary);
- anything visibly wrong: text that disappeared or became invisible (white on white, hidden), overlapping text, a \
header or footer that moved, doubled or vanished unexpectedly, a squashed, stretched or missing logo or image, \
changes on a page where nothing should change, odd breaks.
Renders come from LibreOffice or Word: small font-metric, kerning or line-break differences between renderers are \
not defects. Severity: "block" = it would be wrong to send these files to a client like this; "warn" = worth a look; \
"info" = an observation. Name the file and page of each finding exactly as labelled. Report nothing you cannot see. \
Finish by calling submit_review once."""

REVIEW_TOOLS = [{
    "name": "submit_review", "description": "Your findings about the before/after pages (the final answer).",
    "input_schema": {"type": "object", "properties": {
        "summary": {"type": "string", "description": "one or two sentences: what you looked at and saw"},
        "findings": {"type": "array", "items": {"type": "object", "properties": {
            "severity": {"type": "string", "enum": list(SEVERITIES)},
            "file": {"type": "string"}, "page": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["severity", "text"]}}},
        "required": ["summary", "findings"]}}]


@dataclass
class VisualReview:
    findings: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    skipped: str = ""                             # why there is no review (never an error)
    usage: llm.Usage = field(default_factory=llm.Usage)
    model: str = ""
    pages_sent: int = 0
    seconds: float = 0.0
    transcript: List[Dict[str, Any]] = field(default_factory=list)   # the exchange, without the page images

    def blocking(self) -> List[Dict[str, Any]]:
        return [f for f in self.findings if f.get("severity") == "block"]

    def to_dict(self) -> Dict[str, Any]:
        return {"findings": self.findings, "summary": self.summary, "skipped": self.skipped, "model": self.model,
                "pages_sent": self.pages_sent, "usage": self.usage.to_dict(),
                "seconds": round(self.seconds, 2), "transcript": self.transcript}

    def to_text(self) -> str:
        head = "CLAUDE'S VISUAL CHECK (advisory — not a verification; it can stop a run, never pass one):"
        if self.skipped:
            return f"{head}\n   skipped: {self.skipped}"
        out = [head, f"   {self.summary}" if self.summary else "   (no summary)"]
        if not self.findings:
            out.append("   no findings")
        for f in self.findings:
            where = " ".join(x for x in (f.get("file") or "", f"p.{f['page']}" if f.get("page") else "") if x)
            out.append(f"   [{f['severity']}] {where + ': ' if where else ''}{f['text']}")
        if self.usage.calls:
            out.append(f"   Claude: {self.usage.line()}")
        return "\n".join(out)


def _describe(e: BaseException) -> str:
    """``Type: message`` of an exception, total: one whose message cannot be made is its type alone."""
    try:
        return f"{type(e).__name__}: {e}"[:500]
    except Exception:  # noqa: BLE001
        return type(e).__name__


def _traceback() -> str:
    """The traceback of the exception being handled (call it inside the ``except``), total."""
    try:
        return traceback.format_exc()[-4000:]
    except Exception:  # noqa: BLE001
        return ""


def _record(rv: VisualReview, make: Callable[[], Dict[str, Any]]) -> None:
    """One transcript entry; a record that cannot be made never stops the review (it never raises: rule 15)."""
    try:
        rv.transcript.append(make())
    except Exception as e:  # noqa: BLE001
        rv.transcript.append({"record_error": _describe(e)[:300]})


def _skip_for(rv: VisualReview, e: BaseException, turn: int, failed: str, seconds: float = 0.0) -> None:
    """Skip the review for an exception of the client factory (turn 0) or of the call (turn 1), classified the one way
    (Diagnostics rule 7): said with ``llm.failure_text`` (``ClaudeUnavailable``: "Claude is not available: …"), recorded
    with ``llm.error_entry``; anything but an API error is a defect and keeps its traceback. Call it inside the
    ``except``. Never raises."""
    tb = _traceback()
    try:
        rv.skipped = llm.failure_text(e, failed)
        if llm.is_api_error(e):
            tb = ""
    except Exception:  # noqa: BLE001 — an exception that cannot even be described is still only a skip
        rv.skipped = f"{failed} ({type(e).__name__})"
    _record(rv, lambda: {**llm.error_entry(turn, e, seconds), **({"traceback": tb} if tb else {})})


def _quiet(log: Optional[Callable[[str], None]]) -> Callable[[str], None]:
    """The caller's log, never raising into the review (a diagnostic never stops the work it describes)."""
    def say(message: str) -> None:
        try:
            if log is not None:
                log(message)
        except Exception:  # noqa: BLE001
            pass
    return say


def _finding(f: Any, sent: Dict[str, List[int]]) -> Optional[Dict[str, Any]]:
    """One finding as shown, or None for an item that is not one (not an object, no text). A file Claude was not shown
    loses its name (the finding stays); a page is an int (not a bool) of the pages sent for that file."""
    if not isinstance(f, dict) or not str(f.get("text") or "").strip():
        return None
    sev = f.get("severity") if f.get("severity") in SEVERITIES else "warn"
    file = str(f.get("file") or "").strip()
    page = f.get("page") if isinstance(f.get("page"), int) and not isinstance(f.get("page"), bool) else None
    if file and file not in sent:                       # a name Claude was not shown: keep the finding, drop the name
        file, page = "", None
    if page is not None and file and page not in sent[file]:
        page = None
    return {"severity": sev, "file": file, "page": page, "text": str(f["text"]).strip()}


def _read_answer(rv: VisualReview, resp: Any, model: str, sent: Dict[str, List[int]]) -> None:
    """The review out of Claude's answer. Raises on an answer that is not a review's shape (the caller makes that a
    skip); the summary and findings are set together, at the end, so a review read part-way leaves nothing behind."""
    rv.usage.add(resp, model)
    problem = llm.stop_problem(resp)
    if problem:
        rv.skipped = problem
        return
    body = llm.history_content(resp)
    uses = [b for b in llm.tool_uses(body) if b.name == "submit_review"]
    if not uses:
        said = " ".join(t.strip() for t in llm.texts(body) if t.strip())
        rv.skipped = "Claude answered without a review" + (f": {said[:300]}" if said else "")
        return
    inp = uses[0].input
    if not isinstance(inp, dict):
        raise ValueError(f"submit_review's input is {type(inp).__name__}, not an object")
    raw = inp.get("findings")
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ValueError(f"submit_review's findings is {type(raw).__name__}, not a list")
    findings = [x for x in (_finding(f, sent) for f in raw) if x is not None]
    rv.summary, rv.findings = str(inp.get("summary") or "").strip(), findings


def review_samples(intent: str, samples: Sequence[Any], work_dir: Path, client_factory: Callable[[], Any],
                   model: Optional[str] = None, log: Optional[Callable[[str], None]] = None) -> VisualReview:
    """One call: every sample's look pages, BEFORE and AFTER (labelled by ``compiler.file_labels``: the file's name, or
    with the folders that tell two same-named samples apart; findings are validated against those labels), plus the
    request. Never raises (rule 15): a problem at any stage — preparing the pages, making the client, the call, reading
    the answer — is a ``skipped`` review without findings, and anything but an API error keeps its traceback in the
    transcript."""
    say = _quiet(log)
    model = model or llm.DEFAULT_MODEL
    rv = VisualReview(model=model)
    labelled: List[tuple] = []
    sent: Dict[str, List[int]] = {}
    content: List[Dict[str, Any]] = []
    try:
        # each sample by its label (compiler.file_labels over every sample, as the preview lists them): its name, or
        # with the folders that tell two same-named samples apart — never two pages under one label, never one sample's
        # findings filed under another
        from .compiler import file_labels
        labels = file_labels([Path(s.source) for s in samples if getattr(s, "source", None) is not None])
        for i, s in enumerate(samples):
            if getattr(s, "exception", None) or not getattr(s, "source_pdf", None) or not getattr(s, "output_pdf", None):
                continue
            name = labels[str(Path(s.source))]
            for n in (getattr(s, "look_pages", None) or [1])[:2]:
                base = Path(work_dir) / "visual-check" / f"{i:02d}"
                before = rasterize_pages(s.source_pdf, [n], base / "before")
                after = rasterize_pages(s.output_pdf, [n], base / "after")
                if before and after:
                    labelled += [(f"{name} — page {n} — BEFORE", before[0]), (f"{name} — page {n} — AFTER", after[0])]
                    sent.setdefault(name, []).append(n)
        for lab, png in labelled:
            content += [{"type": "text", "text": lab}, image_block(png)]
    except Exception as e:  # noqa: BLE001 — a page that cannot be rasterised or read: no review, never a broken preview
        tb, why = _traceback(), _describe(e)
        rv.skipped = f"the pages could not be prepared ({why})"
        _record(rv, lambda: {"turn": 0, "error": why, "traceback": tb})
        return rv
    if not labelled:
        rv.skipped = "no page images (rendering was off, or no sample could be rendered)"
        return rv
    try:
        client = client_factory()
    except Exception as e:  # noqa: BLE001 — no anthropic package, no key, or a defect: the preview stands without a review
        _skip_for(rv, e, 0, "Claude could not be set up")
        return rv
    content.append({"type": "text", "text": "WHAT THE USER ASKED AND WHAT THE ENGINE DID:\n" + str(intent or "") +
                    "\n\nReview the pages above and call submit_review."})
    rv.pages_sent = len(labelled)
    say(f"Claude's visual check: {len(labelled)} page image(s) of {len(sent)} sample file(s)…")
    _record(rv, lambda: {"turn": 0, "role": "user", "content": llm.transcript_content(content)})
    t = time.perf_counter()
    try:
        resp = llm.call(client, model=model, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS,
                        messages=[{"role": "user", "content": content}])
    except Exception as e:  # noqa: BLE001 — advisory: never raises (rule 15); a defect keeps its traceback
        rv.seconds = time.perf_counter() - t
        _skip_for(rv, e, 1, "the request to Claude failed", rv.seconds)
        return rv
    rv.seconds = time.perf_counter() - t
    _record(rv, lambda: llm.response_entry(1, resp, rv.seconds))
    try:
        _read_answer(rv, resp, model, sent)
    except Exception as e:  # noqa: BLE001 — an answer that is not a review: skipped with its traceback (rule 15)
        tb, why = _traceback(), _describe(e)
        rv.summary, rv.findings = "", []
        rv.skipped = f"Claude's answer could not be read ({why})"
        _record(rv, lambda: {"turn": 1, "read_error": why, "traceback": tb})
    return rv


def save_review(rv: VisualReview, out_dir: Path) -> Path:
    path = Path(out_dir) / "visual_review.json"
    path.write_text(json.dumps(rv.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
