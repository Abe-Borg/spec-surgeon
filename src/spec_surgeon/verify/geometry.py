"""V6/V7 — rendered-page evidence via PyMuPDF (no poppler dependency on Windows).

* word boxes per page → geometry assertion: for every word that appears in both renders of a page,
  |Δy| and |Δx| vs the reference; pages the plan declares as expected-to-change are reported but not
  asserted.
* page rasters and a contact sheet for the human "actually look" step.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

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


@dataclass
class GeometryResult:
    ref_pages: int
    out_pages: int
    pages: List[PageGeom] = field(default_factory=list)
    worst_dy_asserted: float = 0.0
    worst_dx_asserted: float = 0.0

    def to_dict(self):
        return {
            "ref_pages": self.ref_pages, "out_pages": self.out_pages,
            "worst_dy_asserted_pt": round(self.worst_dy_asserted, 2),
            "worst_dx_asserted_pt": round(self.worst_dx_asserted, 2),
            "pages": [vars(p) for p in self.pages],
        }


def _words(page) -> List[Tuple[str, float, float]]:
    return [(w[4], float(w[0]), float(w[1])) for w in page.get_text("words")]


def compare_pdfs(ref_pdf: Path, out_pdf: Path, expected_change_pages: Sequence[int] = ()) -> GeometryResult:
    ref = fitz.open(str(ref_pdf))
    out = fitz.open(str(out_pdf))
    res = GeometryResult(ref_pages=ref.page_count, out_pages=out.page_count)
    n = min(ref.page_count, out.page_count)
    for i in range(n):
        rw, ow = _words(ref[i]), _words(out[i])
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
        res.pages.append(PageGeom(i + 1, matched, round(max_dy, 2), round(max_dx, 2), only_ref, only_out, exp))
        if not exp:
            res.worst_dy_asserted = max(res.worst_dy_asserted, max_dy)
            res.worst_dx_asserted = max(res.worst_dx_asserted, max_dx)
    ref.close(); out.close()
    return res


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
# Output section k is compared with template section map[k] (the spec's own template_sections), page for page,
# for each templated kind (header / footer):
#   * the template shows nothing there → the output must show nothing there;
#   * otherwise the output's words within BAND_SLACK_PT of the template region's extent must exist ("not found"
#     — a header pushed down by a different distance lands outside it), the region's outer edge — the header's
#     top line, the footer's bottom line measured from the page bottom (page heights may differ) — must sit where
#     the template's does, and every word both regions share (difflib; fills differ from placeholders and are
#     simply not matched) must sit at the template's height, all within the tolerance.
# Lines a long fill wraps onto beyond the template region's extent are outside the window (the first header line
# and the last footer line still are checked). x positions are not asserted (fills and rescaled tabs move them).

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


def hf_only_copy(docx: Path, out_docx: Path) -> Tuple[Path, int]:
    """Write a copy of ``docx`` whose body is only its section breaks, three pages each (``pageBreakBefore`` —
    LibreOffice folds a paragraph holding nothing but a run-level break), every section of type ``nextPage`` with
    ``pgNumType/@start`` = 1 (blank pages for oddPage/evenPage sections and continuous sections sharing a page
    would break the page-for-page correspondence). Verifier-side: own zip reader/writer; every member but the
    main part copied byte for byte. Returns (path, number of sections)."""
    import zipfile
    from lxml import etree
    from .reader import VPackage, W
    data = Path(docx).read_bytes()
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
