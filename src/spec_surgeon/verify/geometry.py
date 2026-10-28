"""V6/V7 — rendered-page evidence via PyMuPDF (no poppler dependency on Windows).

* word boxes per page → geometry assertion: every word must correspond and retain its position;
  pages the plan declares as expected-to-change are reported but not asserted.
* the words shown over the whole document (``shown_words``): the source render's words less the output render's,
  less what the plan removes — V6's warning for text a copy hides without moving a word the page-for-page
  comparison sees (a shape drawn over it, white on white, a size of nothing, spacing that pushes a line off the page).
* page rasters and a contact sheet for the human "actually look" step.
"""
from __future__ import annotations

import difflib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore


@dataclass
class PageGeom:
    page: int
    matched_words: int
    max_dy: float
    max_dx: float
    words_only_in_ref: int
    words_only_in_out: int
    expected_change: bool
    expected_removed_words: int = 0


WORDS_LISTED = 50       # the words a record lists of a whole-document count (the count itself is complete)


@dataclass
class WordCount:
    """A multiset of rendered words as a record carries it: how many, and the first ``WORDS_LISTED`` of them in the
    order the render shows them (a word missing twice is listed twice)."""
    count: int = 0
    words: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"count": self.count, "words": list(self.words)}


@dataclass
class GeometryResult:
    ref_pages: int
    out_pages: int
    pages: List[PageGeom] = field(default_factory=list)
    worst_dy_asserted: float = 0.0
    worst_dx_asserted: float = 0.0
    evidence_problems: List[str] = field(default_factory=list)
    # the whole-document word counts (``shown_words``; None when no plan was given to compare against): the words the
    # source render shows that the output render does not and the plan does not remove; the words the output render
    # shows beyond the source's and the plan's additions; the header/footer-band words left out of the count
    words_missing: Optional[WordCount] = None
    words_new: Optional[WordCount] = None
    hf_words_not_compared: Optional[WordCount] = None

    def to_dict(self):
        out = {
            "ref_pages": self.ref_pages, "out_pages": self.out_pages,
            "worst_dy_asserted_pt": round(self.worst_dy_asserted, 2),
            "worst_dx_asserted_pt": round(self.worst_dx_asserted, 2),
            "pages": [vars(p) for p in self.pages],
            "evidence_problems": self.evidence_problems,
        }
        out.update(self.shown_words_data())
        return out

    def shown_words_data(self) -> Dict[str, Any]:
        """The whole-document word counts for a check's data (``words_missing_from_render`` is V6's warning)."""
        out: Dict[str, Any] = {}
        if self.words_missing is not None:
            out["words_missing_from_render"] = self.words_missing.to_dict()
        if self.words_new is not None:
            out["words_new_in_render"] = self.words_new.to_dict()
        if self.hf_words_not_compared is not None and self.hf_words_not_compared.count:
            out["header_footer_words_not_compared"] = self.hf_words_not_compared.to_dict()
        return out


def _words(page) -> List[Tuple[str, float, float]]:
    return [(w[4], float(w[0]), float(w[1])) for w in page.get_text("words")]


# ---- V6: the words shown, over the whole document -------------------------------------------------------------
#
# A copy can hide text without moving a word the page-for-page comparison above sees: a shape drawn over it, white
# on white, a size of nothing, spacing that pushes a line off the page (the stress-test finding F4: PASS copies
# whose rendered pages were blank while the XML still held every character). The signal is the multiset of the words
# of every page of the source render minus the multiset of the output render's — a word that moved to another page
# cancels out — less what the plan says it removes (each diff's before words beyond its after words; a removed
# watermark's words, drawn on every page, in any count). Words are the renderer's tokens, split on whitespace as it
# splits them, never casefolded. It is a warning, never a FAIL: a layout operation reflows words across pages by
# design, and a renderer's own line breaking can split or join a token.
#
# Header and footer words repeat once per page, so they are counted per page and summed like every other word, and
# a word that moved to another page cancels out: a font change that shortens the document moves its body words,
# their counts stay. Only the per-page repeats themselves change with the page count (a document one page shorter
# shows its header one time fewer; a page number or NUMPAGES field reads another number). So, when the two renders
# have different page counts, a missing word the source render shows in its header/footer bands — wholly outside
# every section's body region, by the source's own margins as ``_removal_margins`` reads them; missing margins grant
# no band — is attributed to the band, recorded under ``header_footer_words_not_compared`` and left out of the
# warning. With equal page counts the band words count like any other: a header word a shape covers is reported. A
# plan whose layout reference is the template replaces the headers and footers wholesale (V7 compares the new bands
# with the template's render, page for page); its band words are recorded the same way, whatever the page counts.

def _tokens(text: Optional[str]) -> List[str]:
    """The words of a plan's text as the renderer would show them: split on whitespace, nothing else."""
    return (text or "").split()


def _watermark_words(source_docx: Path, text: Optional[str]) -> Set[str]:
    """The words of the source's text watermarks (of those reading ``text``, when given), with the verifier's own
    reading of the header parts; a source whose watermarks the verifier cannot read gives none."""
    from .reader import VPackage
    from .watermark import _v_same, _v_watermarks
    out: Set[str] = set()
    try:
        pkg = VPackage(Path(source_docx).read_bytes())
        for part in pkg.part_of_kind("headers"):
            for found in _v_watermarks(pkg.xml(part)):
                shown = found.get("text")
                if isinstance(shown, str) and (text is None or _v_same(shown, text)):
                    out.update(_tokens(shown))
    except Exception:  # noqa: BLE001 — no words to subtract, the warning may then name the watermark's; never a crash
        return set()
    return out


def plan_removed_words(plan: Dict[str, Any], source_docx: Optional[Path] = None) -> Tuple[Counter, Set[str]]:
    """What the plan says disappears from the pages, in the renderer's terms: (bounded — per diff, the words of its
    before text beyond its after text: a replacement, a deletion, a removed header part's blocks; unbounded — the
    words of a removed watermark, drawn on every page)."""
    bounded: Counter = Counter()
    unbounded: Set[str] = set()
    for d in plan.get("diffs") or []:
        before, after = d.get("before"), d.get("after")
        if isinstance(before, str):
            bounded.update(Counter(_tokens(before)) - Counter(_tokens(after if isinstance(after, str) else "")))
    for a in plan.get("property_assertions") or []:
        if a.get("kind") == "removed_watermark":
            text = a.get("text") if isinstance(a.get("text"), str) else None
            words = _watermark_words(source_docx, text) if source_docx is not None else set()
            unbounded.update(words or _tokens(text))
    return bounded, unbounded


def plan_added_words(plan: Dict[str, Any]) -> Counter:
    """The words the plan's diffs add (each after text beyond its before text), subtracted from the output render's
    surplus so the record counts only what the plan does not account for."""
    added: Counter = Counter()
    for d in plan.get("diffs") or []:
        before, after = d.get("before"), d.get("after")
        if isinstance(after, str):
            added.update(Counter(_tokens(after)) - Counter(_tokens(before if isinstance(before, str) else "")))
    return added


def _band_words(doc, margins: Tuple[float, float]) -> Counter:
    """The words of ``doc``'s pages wholly inside the header band (above the smallest top margin) or the footer band
    (below the smallest bottom margin), in PDF points; a band of 0 holds no word."""
    top, bottom = margins
    out: Counter = Counter()
    for page in doc:
        height = page.rect.height
        for w in page.get_text("words"):
            if (top > 0 and float(w[3]) <= top) or (bottom > 0 and float(w[1]) >= height - bottom):
                out[w[4]] += 1
    return out


def _listed(counts: Counter, order: Sequence[str]) -> WordCount:
    """``counts`` as a record carries it: the total, and the first WORDS_LISTED words in the order of ``order``."""
    left = Counter({k: v for k, v in counts.items() if v > 0})
    total = sum(left.values())
    words: List[str] = []
    for w in order:
        if len(words) >= WORDS_LISTED:
            break
        if left.get(w, 0) > 0:
            left[w] -= 1
            words.append(w)
    return WordCount(total, words)


def shown_words(ref_doc, out_doc, plan: Dict[str, Any], *, margins: Optional[Tuple[float, float]] = None,
                source_docx: Optional[Path] = None) -> Tuple[WordCount, WordCount, WordCount]:
    """(the source render's words missing from the output render that the plan does not remove; the output render's
    words beyond the source's that the plan does not add; the band words left out of the first count). ``ref_doc`` is
    the layout reference's render — the source's, or the independent header/footer-removal reference's — and
    ``out_doc`` the copy's; ``margins`` the source's body bounds (``_removal_margins``) for the band rule above."""
    ref_words = [w[0] for page in ref_doc for w in _words(page)]       # per page, summed over the document
    out_words = [w[0] for page in out_doc for w in _words(page)]
    removed, any_count = plan_removed_words(plan, source_docx)
    missing = Counter(ref_words) - Counter(out_words) - removed
    for w in any_count:
        missing.pop(w, None)
    new = Counter(out_words) - Counter(ref_words) - plan_added_words(plan)
    not_compared: Counter = Counter()
    if margins is not None and (ref_doc.page_count != out_doc.page_count
                                or plan.get("geometry_reference") == "template"):
        band = _band_words(ref_doc, margins)
        not_compared = missing & band
        missing = missing - band
    return _listed(missing, ref_words), _listed(new, out_words), _listed(not_compared, ref_words)


def _removal_margins(source: Path) -> Tuple[float, float]:
    """Conservative bands outside EVERY source section's body, in PDF points.

    Missing/negative margins grant no allowance. A header that intrudes into the body therefore
    needs review rather than silently exempting body text from the geometry assertion.
    """
    from .props import sect_prs
    from .reader import VPackage, W
    pkg = VPackage(Path(source).read_bytes())
    sections = sect_prs(pkg.xml(pkg.main_part()))
    result = []
    for name in ("top", "bottom"):
        values = []
        for section in sections:
            margin = section.find(W + "pgMar")
            values.append(max(0, int(margin.get(W + name, "0"))) / 20 if margin is not None else 0)
        result.append(min(values, default=0))
    return result[0], result[1]


def _removed_margin_words(original, baseline, margins: Tuple[float, float], tol: float) -> Tuple[int, List[str]]:
    """Validate the independently stripped reference against original word coordinates.

    Match by text AND position, so a repeated header word cannot consume its body occurrence.
    Every baseline word must survive at its original position. Only missing original words wholly
    outside the conservative body bounds may be attributed to the approved header/footer removal.
    """
    source = original.get_text("words")
    by_text = {}
    for i, word in enumerate(source):
        by_text.setdefault(word[4], []).append(i)
    used = set()
    for word in baseline.get_text("words"):
        match = next((i for i in by_text.get(word[4], []) if i not in used
                      and abs(source[i][0] - word[0]) <= tol and abs(source[i][1] - word[1]) <= tol), None)
        if match is None:
            return 0, ["independent header/footer-removal reference added or moved a surviving word"]
        used.add(match)
    removed = [word for i, word in enumerate(source) if i not in used]
    top, bottom = margins
    if any(not (word[3] <= top or word[1] >= original.rect.height - bottom) for word in removed):
        return 0, ["independent header/footer-removal reference lost a word in the source body region"]
    if original.rect != baseline.rect:
        return 0, ["independent header/footer-removal reference changed the page size"]
    return len(removed), []


def compare_pdfs(ref_pdf: Path, out_pdf: Path, expected_change_pages: Sequence[int] = (), *,
                 removed_hf_pdf: Optional[Path] = None, removed_hf_source: Optional[Path] = None,
                 plan: Optional[Dict[str, Any]] = None, source_docx: Optional[Path] = None) -> GeometryResult:
    """The page-for-page comparison (V7's evidence) and, with ``plan``, the whole-document word counts
    (``shown_words``, V6's warning); ``source_docx`` is the source file, read for its body bounds (the band rule)
    and for what a removed watermark showed."""
    ref = fitz.open(str(ref_pdf))
    out = fitz.open(str(out_pdf))
    res = GeometryResult(ref_pages=ref.page_count, out_pages=out.page_count)
    baseline = fitz.open(str(removed_hf_pdf)) if removed_hf_pdf is not None else None
    margins = _removal_margins(removed_hf_source) if baseline is not None and removed_hf_source is not None else None
    if baseline is not None and (margins is None or baseline.page_count != ref.page_count):
        res.evidence_problems.append("independent header/footer-removal reference lacks source bounds or has a different page count")
    if plan is not None:
        # the words of the reference the plan removes headers/footers from are the reference's (the words it keeps),
        # when that reference is sound; the band rule needs the source's own bounds
        whole_ref = baseline if (baseline is not None and margins is not None
                                 and baseline.page_count == ref.page_count) else ref
        bounds = margins if margins is not None else (_removal_margins(source_docx) if source_docx is not None else None)
        res.words_missing, res.words_new, res.hf_words_not_compared = shown_words(
            whole_ref, out, plan, margins=bounds, source_docx=source_docx)
    n = min(ref.page_count, out.page_count)
    for i in range(n):
        removed = 0
        reference_page = ref[i]
        if baseline is not None and i < baseline.page_count and margins is not None:
            removed, problems = _removed_margin_words(ref[i], baseline[i], margins, 0.5)
            res.evidence_problems.extend(f"page {i + 1}: {problem}" for problem in problems)
            reference_page = baseline[i]
        rw, ow = _words(reference_page), _words(out[i])
        sm = difflib.SequenceMatcher(a=[w[0] for w in rw], b=[w[0] for w in ow], autojunk=False)
        max_dy = max_dx = 0.0
        matched = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != "equal":
                continue
            for k in range(i2 - i1):
                a, b = rw[i1 + k], ow[j1 + k]
                max_dy = max(max_dy, abs(a[2] - b[2]))
                max_dx = max(max_dx, abs(a[1] - b[1]))
                matched += 1
        only_ref = sum(i2 - i1 for op, i1, i2, _, _ in sm.get_opcodes() if op in ("delete", "replace"))
        only_out = sum(j2 - j1 for op, _, _, j1, j2 in sm.get_opcodes() if op in ("insert", "replace"))
        exp = (i + 1) in expected_change_pages
        res.pages.append(PageGeom(i + 1, matched, round(max_dy, 2), round(max_dx, 2), only_ref, only_out, exp, removed))
        if not exp:
            res.worst_dy_asserted = max(res.worst_dy_asserted, max_dy)
            res.worst_dx_asserted = max(res.worst_dx_asserted, max_dx)
    ref.close(); out.close()
    if baseline is not None:
        baseline.close()
    return res


def hf_removal_copy(docx: Path, out_docx: Path, plan: Dict) -> Optional[Path]:
    """Independently remove ONLY the raw-approved header/footer references from a source copy.

    The original body, section setup and all other members remain unchanged. Rendering this copy
    identifies the exact intentionally removed word anchors, including rendered PAGE fields. Neither
    transform data, planned target lists nor the actual output are used to construct the reference.
    """
    import zipfile
    from lxml import etree
    from .props import Styles, _v_resolve_sections, sect_prs
    from .reader import VPackage, W, STYLES_REL
    operations = [op for op in plan.get("ops", []) if op.get("op") == "remove_header_footer"]
    if not operations:
        return None
    pkg = VPackage(Path(docx).read_bytes())
    main = pkg.main_part()
    root = pkg.xml(main)
    sections = sect_prs(root)
    style_part = pkg.related(STYLES_REL)
    styles = Styles(pkg.xml(style_part) if style_part else None)
    changed = False
    for op in operations:
        selection = op.get("select") or {}
        if op.get("params") or set(selection) - {"sections", "kinds", "types"}:
            raise ValueError("unsupported raw header/footer-removal parameters")
        chosen = _v_resolve_sections(pkg, selection.get("sections", "all"), styles)
        kinds = selection.get("kinds", ["header", "footer"])
        types = selection.get("types", ["default", "first", "even"])
        if not kinds or set(kinds) - {"header", "footer"} or not types or set(types) - {"default", "first", "even"}:
            raise ValueError("invalid raw header/footer-removal kinds or types")
        for index in chosen:
            section = sections[index]
            for kind in kinds:
                for reference in list(section.findall(W + kind + "Reference")):
                    if reference.get(W + "type", "default") in types:
                        section.remove(reference)
                        changed = True
    if not changed:
        return None
    out_docx = Path(out_docx)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as output:
        for info in pkg.infos:
            output.writestr(info, xml if info.filename == main else pkg.read(info.filename))
    return out_docx


def rasterize(pdf: Path, out_dir: Path, pages: Optional[Sequence[int]] = None, dpi: int = 80) -> List[Path]:
    doc = fitz.open(str(pdf))
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    idxs = [p - 1 for p in pages] if pages else range(doc.page_count)
    for i in idxs:
        if 0 <= i < doc.page_count:
            pix = doc[i].get_pixmap(dpi=dpi)
            p = out_dir / f"{pdf.stem}-p{i + 1}.png"
            pix.save(str(p))
            paths.append(p)
    doc.close()
    return paths


def contact_sheet(pdfs: Sequence[Path], out_png: Path, page: int = 1, cols: int = 5, thumb_w: float = 150) -> Path:
    """One image with the chosen page of every PDF, labelled — the batch-level 'actually look'."""
    sheet = fitz.open()
    n = len(pdfs)
    if n == 0:
        raise ValueError("no PDFs")
    rows = (n + cols - 1) // cols
    thumb_h = thumb_w * 1.3
    pad = 8
    label_h = 12
    width = cols * (thumb_w + pad) + pad
    height = rows * (thumb_h + label_h + pad) + pad
    pg = sheet.new_page(width=width, height=height)
    for k, pdf in enumerate(pdfs):
        r, c = divmod(k, cols)
        x0 = pad + c * (thumb_w + pad)
        y0 = pad + r * (thumb_h + label_h + pad)
        rect = fitz.Rect(x0, y0, x0 + thumb_w, y0 + thumb_h)
        try:
            src = fitz.open(str(pdf))
            if src.page_count >= page:
                pg.show_pdf_page(rect, src, page - 1)
            src.close()
        except Exception:
            pg.draw_rect(rect, color=(1, 0, 0))
        pg.draw_rect(rect, color=(0.6, 0.6, 0.6), width=0.5)
        pg.insert_text((x0, y0 + thumb_h + 10), Path(pdf).stem[:28], fontsize=6)
    pix = pg.get_pixmap(dpi=110)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    sheet.close()
    return out_png


def page_text(pdf: Path) -> List[str]:
    doc = fitz.open(str(pdf))
    out = [p.get_text("text") for p in doc]
    doc.close()
    return out


# ---- V7 against a template render (header/footer regions, page for page) -------------------------------
#
# Both sides are rendered as HEADERS/FOOTERS ONLY: hf_only_copy() writes a copy of a .docx whose body is nothing
# but its section breaks, three pages per section (first, even, odd variants all render), each section starting
# on a new page with page numbering restarted at 1 — so page 3k+v of a copy is section k's v-th page, in the
# template and in the output alike, whatever either document's body, section types or numbering. Every word on
# such a render is header or footer text (upper / lower half of the page).
#
# The template side is the template as the plan makes this document show it (verify/template_layout.py): every slot
# showing its fill, each template section the output uses laid at the output section's text width with its tab
# stops rescaled as the plan declares — so a fill shorter or longer than its placeholder, or a narrower page, wraps
# the reference's lines as it wraps the copy's (live QA finding F08: against the unfilled template, a hyphen a short
# section number let up onto the header's first line read as moved 11.50 pt). Its section map pairs output section
# k with the reference section made for template section map[k] (the spec's own template_sections) at k's width.
#
# Output section k is compared with its reference section, page for page, for each templated kind (header /
# footer):
#   * the template shows nothing there → the output must show nothing there;
#   * otherwise the output's words within BAND_SLACK_PT of the template region's extent must exist ("not found"
#     — a header pushed down by a different distance lands outside it), the region's outer edge — the header's
#     top line, the footer's bottom line measured from the page bottom (page heights may differ) — must sit where
#     the template's does, and every word both regions share (difflib: the fills among them) must sit at the
#     template's height, all within the tolerance.
# Lines beyond the template region's extent are outside the window (the first header line and the last footer line
# still are checked). x positions are not asserted (side margins and rescaled tabs move them).

BAND_SLACK_PT = 2.0
_SECTPR_ORDER = ("headerReference", "footerReference", "footnotePr", "endnotePr", "type", "pgSz", "pgMar",
                 "paperSrc", "pgBorders", "lnNumType", "pgNumType", "cols", "formProt", "vAlign", "noEndnote",
                 "titlePg", "textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings", "sectPrChange")


def _sect_set(sp, name: str, attrs: Dict[str, str]) -> None:
    from lxml import etree
    from .reader import W
    el = sp.find(W + name)
    if el is None:
        el = etree.Element(W + name)
        rank = _SECTPR_ORDER.index(name)
        later = [c for c in sp if isinstance(c.tag, str) and c.tag.startswith(W)
                 and c.tag[len(W):] in _SECTPR_ORDER and _SECTPR_ORDER.index(c.tag[len(W):]) > rank]
        if later:
            later[0].addprevious(el)
        else:
            sp.append(el)
    for k, v in attrs.items():
        el.set(W + k, v)


def hf_only_copy(docx: Union[Path, bytes], out_docx: Path) -> Tuple[Path, int]:
    """Write a copy of ``docx`` (a file, or its bytes) whose body is only its section breaks, three pages each
    (``pageBreakBefore`` — LibreOffice folds a paragraph holding nothing but a run-level break), every section of
    type ``nextPage`` with ``pgNumType/@start`` = 1 (blank pages for oddPage/evenPage sections and continuous
    sections sharing a page would break the page-for-page correspondence). Verifier-side: own zip reader/writer;
    every member but the main part copied byte for byte. Returns (path, number of sections)."""
    import zipfile
    from lxml import etree
    from .reader import VPackage, W
    data = docx if isinstance(docx, bytes) else Path(docx).read_bytes()
    pkg = VPackage(data)
    main = pkg.main_part()
    root = pkg.xml(main)
    body = root.find(W + "body")

    def three_pages():
        out = []
        for k in range(3):
            q = etree.Element(W + "p")
            if k:
                etree.SubElement(etree.SubElement(q, W + "pPr"), W + "pageBreakBefore")
            out.append(q)
        return out

    kept, n = [], 0
    for ch in list(body):
        sp = ch.find(f"{W}pPr/{W}sectPr") if ch.tag == W + "p" else None
        if sp is None and ch.tag == W + "sectPr":
            sp = ch
        if sp is not None:
            _sect_set(sp, "type", {"val": "nextPage"})
            _sect_set(sp, "pgNumType", {"start": "1"})
            n += 1
            if sp is ch:
                kept += three_pages() + [ch]
            else:
                q = etree.Element(W + "p")
                etree.SubElement(q, W + "pPr").append(sp)
                kept += three_pages() + [q]
        body.remove(ch)
    for ch in kept:
        body.append(ch)
    out_docx = Path(out_docx)
    out_docx.parent.mkdir(parents=True, exist_ok=True)
    xml = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(out_docx, "w", zipfile.ZIP_DEFLATED) as z:
        for info in pkg.infos:
            z.writestr(info, xml if info.filename == main else pkg.read(info.filename))
    return out_docx, n


def template_section_map(template_sections: Dict[str, int], n_sections: int) -> List[int]:
    """The spec's own ``template_sections`` read here: {"all": i} → every section i; {"cover": i, "body": j} →
    section 0 → i, the others → j."""
    if "all" in template_sections:
        return [int(template_sections["all"])] * n_sections
    return [int(template_sections["cover"] if k == 0 else template_sections["body"]) for k in range(n_sections)]


@dataclass
class BandPage:
    page: int                  # page of the output's headers/footers-only render (1-based)
    template_page: Optional[int]
    anchors: int               # matched anchor words in the compared regions
    max_dy: float              # worst |Δy| over anchors and region edges
    problems: List[str] = field(default_factory=list)


@dataclass
class TemplateGeometry:
    template_pages: int
    out_pages: int
    pages: List[BandPage] = field(default_factory=list)
    worst_dy: float = 0.0
    kinds: Tuple[str, ...] = ("header", "footer")

    @property
    def failed_pages(self) -> List[int]:
        return [p.page for p in self.pages if p.problems]

    def to_dict(self):
        return {"template_pages": self.template_pages, "out_pages": self.out_pages, "slack_pt": BAND_SLACK_PT,
                "kinds": list(self.kinds), "worst_dy_pt": round(self.worst_dy, 2),
                "pages": [vars(p) for p in self.pages]}


def _region_check(name: str, tw, ow, t_h: float, o_h: float, tol: float):
    """(anchors, worst |Δ|, problems) for one region; ``tw``/``ow`` are the words of that half of the two pages.
    Footer heights are measured from the page bottom."""
    if not tw:
        if ow:
            return 0, 0.0, [f"{name}: the template shows no {name} on this page, the output does "
                            f"({[w[0] for w in ow[:5]]})"]
        return 0, 0.0, []
    if name == "header":
        rel_t, rel_o = [(w[0], w[2]) for w in tw], [(w[0], w[2]) for w in ow]
    else:
        rel_t, rel_o = [(w[0], t_h - w[2]) for w in tw], [(w[0], o_h - w[2]) for w in ow]
    near, far = min(y for _, y in rel_t), max(y for _, y in rel_t)       # outer edge, inner extent
    win = [w for w in rel_o if w[1] <= far + BAND_SLACK_PT]
    if not win:
        closest = min((y for _, y in rel_o), default=None)
        where = "from the top" if name == "header" else "from the bottom"
        return 0, 0.0, [f"{name}: no words where the template's {name} is ({near:.1f}–{far:.1f}pt {where}); "
                        + (f"the output's nearest is {closest:.1f}pt {where}" if closest is not None else
                           f"the output shows no {name}")]
    edge = abs(min(y for _, y in win) - near)
    sm = difflib.SequenceMatcher(a=[w[0] for w in rel_t], b=[w[0] for w in win], autojunk=False)
    pairs = [(rel_t[i1 + k], win[j1 + k]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op == "equal"
             for k in range(i2 - i1)]
    worst = max([edge] + [abs(a[1] - b[1]) for a, b in pairs])
    probs = []
    if edge > tol:
        probs.append(f"{name}: outer edge {edge:.2f}pt from the template's")
    moved = [a[0] for a, b in pairs if abs(a[1] - b[1]) > tol]
    if moved:
        probs.append(f"{name}: words {moved[:5]} moved up to {worst:.2f}pt")
    return len(pairs), worst, probs


def compare_template_bands(template_hf_pdf: Path, out_hf_pdf: Path, section_map: Sequence[int],
                           kinds: Sequence[str] = ("header", "footer"), tol: float = 0.5) -> TemplateGeometry:
    """V7 template mode (see the block comment above): ``template_hf_pdf`` / ``out_hf_pdf`` are the renders of
    hf_only_copy() of the template and of the output; ``section_map[k]`` is the template section of output
    section k; only the ``kinds`` the op templated are compared."""
    tpl, out = fitz.open(str(template_hf_pdf)), fitz.open(str(out_hf_pdf))
    res = TemplateGeometry(template_pages=tpl.page_count, out_pages=out.page_count, kinds=tuple(kinds))
    want_out, want_tpl = 3 * len(section_map), 3 * (max(section_map) + 1) if section_map else 0
    if out.page_count != want_out or tpl.page_count < want_tpl or tpl.page_count % 3:
        res.pages.append(BandPage(0, None, 0, 0.0, [
            f"headers/footers-only renders have {out.page_count} (output) and {tpl.page_count} (template) pages; "
            f"expected {want_out} and a multiple of 3 ≥ {want_tpl} — pages cannot be paired"]))
    halves = lambda pg: (pg.rect.height, [w for w in _words(pg) if w[2] < pg.rect.height / 2],
                         [w for w in _words(pg) if w[2] >= pg.rect.height / 2])
    for k, j in enumerate(section_map):
        for v in range(3):
            i, q = 3 * k + v, 3 * j + v
            if i >= out.page_count or q >= tpl.page_count:
                continue
            o_h, oh, of = halves(out[i])
            t_h, th, tf = halves(tpl[q])
            a = d = 0
            probs: List[str] = []
            if "header" in kinds:
                a1, d1, p1 = _region_check("header", th, oh, t_h, o_h, tol)
                a, d, probs = a + a1, max(d, d1), probs + p1
            if "footer" in kinds:
                a2, d2, p2 = _region_check("footer", tf, of, t_h, o_h, tol)
                a, d, probs = a + a2, max(d, d2), probs + p2
            res.pages.append(BandPage(i + 1, q + 1, a, round(d, 2), probs))
            res.worst_dy = max(res.worst_dy, d)
    tpl.close(); out.close()
    return res
