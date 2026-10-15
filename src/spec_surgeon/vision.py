"""Claude looks at rendered pages (decided 2026-10-15) — in two places, both advisory:

* ``page_pngs`` renders a batch file (or the template) and rasterises a few pages for the conversation's
  ``view_pages`` tool, so Claude can see what the user means by "the line under the header" or "like the template".
* ``review_samples`` is **Claude's visual check** of the preview: the before/after pages of the sample files (page 1
  and the first page whose words moved), with the request, in one call. Its findings are shown to the user; a
  ``block`` finding stops Run until the user has looked at it and acknowledged it.

Claude never grades. The engine's checks (V1–V10) decide PASS/FAIL from the XML; the visual check is not a check in
the verification record, can add a reason to stop and can never pass anything — not a file, not a preview, not a run.
It samples (never every page of every file) to keep the cost down. Nothing here raises into a preview: a renderer that
cannot run, a missing key or an API error is a ``skipped`` review with the reason.
"""
from __future__ import annotations

import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import llm

LONG_EDGE_PX = 2000            # ≤2000 px per side keeps every image valid in requests with more than 20 images
TARGET_DPI = 150
TOKENS_PER_IMAGE_CAP = 4784    # Opus 5.5 high-resolution tier
VIEW_BUDGET_TOKENS = 60000     # estimated image tokens per conversation for view_pages (about a dozen letter pages)
SEVERITIES = ("info", "warn", "block")


def png_size(png: Path) -> tuple:
    with open(png, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{png.name} is not a PNG")
    return struct.unpack(">II", head[16:24])


def est_tokens(png: Path) -> int:
    """⌈w/28⌉·⌈h/28⌉ visual tokens, capped at the model's per-image maximum."""
    w, h = png_size(png)
    return min(-(-w // 28) * -(-h // 28), TOKENS_PER_IMAGE_CAP)


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
            longest_pt = max(page.rect.width, page.rect.height) or 1
            dpi = min(TARGET_DPI, int(LONG_EDGE_PX * 72 / longest_pt))
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

    def blocking(self) -> List[Dict[str, Any]]:
        return [f for f in self.findings if f.get("severity") == "block"]

    def to_dict(self) -> Dict[str, Any]:
        return {"findings": self.findings, "summary": self.summary, "skipped": self.skipped, "model": self.model,
                "pages_sent": self.pages_sent, "usage": self.usage.to_dict()}

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


def review_samples(intent: str, samples: Sequence[Any], work_dir: Path, client_factory: Callable[[], Any],
                   model: Optional[str] = None, log: Optional[Callable[[str], None]] = None) -> VisualReview:
    """One call: every sample's look pages, BEFORE and AFTER (labelled), plus the request. Never raises."""
    log = log or (lambda m: None)
    model = model or llm.DEFAULT_MODEL
    rv = VisualReview(model=model)
    labelled: List[tuple] = []
    sent: Dict[str, List[int]] = {}
    try:
        for i, s in enumerate(samples):
            if getattr(s, "exception", None) or not getattr(s, "source_pdf", None) or not getattr(s, "output_pdf", None):
                continue
            name = Path(s.source).name
            for n in (getattr(s, "look_pages", None) or [1])[:2]:
                base = Path(work_dir) / "visual-check" / f"{i:02d}"
                before = rasterize_pages(s.source_pdf, [n], base / "before")
                after = rasterize_pages(s.output_pdf, [n], base / "after")
                if before and after:
                    labelled += [(f"{name} — page {n} — BEFORE", before[0]), (f"{name} — page {n} — AFTER", after[0])]
                    sent.setdefault(name, []).append(n)
    except Exception as e:  # noqa: BLE001 — a page that cannot be rasterised: no review, never a broken preview
        rv.skipped = f"the pages could not be prepared ({type(e).__name__}: {e})"
        return rv
    if not labelled:
        rv.skipped = "no page images (rendering was off, or no sample could be rendered)"
        return rv
    try:
        client = client_factory()
    except Exception as e:  # noqa: BLE001 — no anthropic package, no key: the preview stands without a review
        rv.skipped = f"Claude is not available ({e})"
        return rv
    content: List[Dict[str, Any]] = []
    for lab, png in labelled:
        content += [{"type": "text", "text": lab}, image_block(png)]
    content.append({"type": "text", "text": "WHAT THE USER ASKED AND WHAT THE ENGINE DID:\n" + intent +
                    "\n\nReview the pages above and call submit_review."})
    rv.pages_sent = len(labelled)
    log(f"Claude's visual check: {len(labelled)} page image(s) of {len(sent)} sample file(s)…")
    try:
        resp = llm.call(client, model=model, system=REVIEW_SYSTEM, tools=REVIEW_TOOLS,
                        messages=[{"role": "user", "content": content}])
    except Exception as e:  # noqa: BLE001
        rv.skipped = f"the request to Claude failed ({type(e).__name__}: {e})"
        return rv
    rv.usage.add(resp, model)
    problem = llm.stop_problem(resp)
    if problem:
        rv.skipped = problem
        return rv
    body = llm.history_content(resp)
    uses = [b for b in llm.tool_uses(body) if b.name == "submit_review"]
    if not uses:
        said = " ".join(t.strip() for t in llm.texts(body) if t.strip())
        rv.skipped = "Claude answered without a review" + (f": {said[:300]}" if said else "")
        return rv
    inp = dict(uses[0].input or {})
    rv.summary = str(inp.get("summary") or "").strip()
    for f in inp.get("findings") or []:
        if not isinstance(f, dict) or not str(f.get("text") or "").strip():
            continue
        sev = f.get("severity") if f.get("severity") in SEVERITIES else "warn"
        file = str(f.get("file") or "").strip()
        page = f.get("page") if isinstance(f.get("page"), int) else None
        if file and file not in sent:                       # a name Claude was not shown: keep the finding, drop the name
            file, page = "", None
        if page is not None and file and page not in sent[file]:
            page = None
        rv.findings.append({"severity": sev, "file": file, "page": page, "text": str(f["text"]).strip()})
    return rv


def save_review(rv: VisualReview, out_dir: Path) -> Path:
    path = Path(out_dir) / "visual_review.json"
    path.write_text(json.dumps(rv.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path
