"""Capitals like the template.

From the owner's Division 21 run: 21 05 53's footer title showed in mixed case — the fill copies the file's own title
as typed, and its first page types "Identification for Fire Suppression Systems" — while the template and the other
files showed capitals. ``caps_fills`` shows a named fill in capitals through formatting (w:caps on the filled run): no
letter is retyped, the fill stays the file's own words. Without it, a note names the file whose fill will show in
another case than the template's own text. V10 (verify/template.py) derives the expected look on its own."""
from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import REFERENCE_FILLS, build, build_reference_spec
from spec_surgeon import compiler
from spec_surgeon.editspec import plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_header_footer_template as tmpl_module
from spec_surgeon.ops.base import CASE_DIFFERS
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.planner import left_elsewhere_warnings, plan_batch
from spec_surgeon.verify import run_suite

OP = "apply_header_footer_template"
MIXED = "Identification for Fire Suppression Systems"


@pytest.fixture(scope="module")
def template(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("reference") / "08 71 00 - DOOR HARDWARE (ACME).docx"
    p.write_bytes(build_reference_spec())
    return p


def params(tpl: Path, **kw) -> dict:
    d = {"template": str(tpl), "template_sections": {"all": 0}, "slot_fills": copy.deepcopy(REFERENCE_FILLS),
         "slot_markers": ["reference"]}
    d.update(kw)
    return d


def run(src: bytes, prm: dict, apply_with=None):
    pkg = DocxPackage(src)
    frag = get_op(OP).plan(pkg, {"sections": "all"}, prm)
    plan = merge_fragments([frag])
    work = pkg.copy()
    (apply_with or get_op(OP)).apply(work, frag)
    out = work.output_bytes()
    return frag, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def hf_runs(docx: bytes):
    """(part, run text, caps on the run) for every run of the new header/footer parts."""
    pkg = DocxPackage(docx)
    out = []
    for part in sorted(m.filename for m in pkg.members if m.filename.startswith(("word/header", "word/footer"))
                       and m.filename.endswith(".xml")):
        for r in pkg.xml(part).iter(W + "r"):
            text = "".join(t.text or "" for t in r.findall(W + "t"))
            caps = r.find(f"{W}rPr/{W}caps")
            out.append((part, text, caps is not None and caps.get(W + "val") not in ("0", "false", "off")))
    return out


def mutate(docx: bytes, fn) -> bytes:
    """Apply ``fn(part name, root)`` to every header/footer part of a package (a template variant)."""
    zin = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename.startswith(("word/header", "word/footer")) and info.filename.endswith(".xml"):
                root = etree.fromstring(data)
                fn(info.filename, root)
                data = serialize_xml(root)
            z.writestr(info, data)
    return buf.getvalue()


def _mark_title_runs(element: str, **attrs):
    def fn(_, root):
        for r in root.iter(W + "r"):
            if "DOOR HARDWARE" in "".join(t.text or "" for t in r.findall(W + "t")):
                rpr = r.find(W + "rPr")
                if rpr is None:
                    rpr = etree.SubElement(r, W + "rPr")
                    r.remove(rpr)
                    r.insert(0, rpr)
                el = etree.SubElement(rpr, W + element)
                for k, v in attrs.items():
                    el.set(W + k, v)
    return fn


def test_caps_fills_shows_the_files_own_title_in_capitals(template):
    src = build("21 05 53", MIXED)
    frag, out, rec = run(src, params(template, caps_fills=["SECTION_TITLE"]))
    assert rec.passed, failed(rec)
    runs = hf_runs(out)
    titled = [(t, c) for _, t, c in runs if MIXED in t]
    assert titled and all(c for _, c in titled)                  # the fill, as the file types it, under w:caps
    assert not any(c for _, t, c in runs if MIXED not in t)      # nothing else gained capitals
    assert any("shown in capitals by formatting" in n for n in frag.notes)
    # the same request without caps_fills writes no w:caps at all
    _, plain, rec = run(src, params(template))
    assert rec.passed and not any(c for _, _, c in hf_runs(plain))


def test_a_fill_in_another_case_than_the_template_is_noted(template, tmp_path):
    def flags(src, **kw):
        spec = {"request": "Match the template.", "summary": "template", "mode": "formatting",
                "ops": [{"op": OP, "select": {"sections": "all"}, "params": params(template, **kw)}]}
        plan, _ = plan_spec(DocxPackage(src), spec)
        return [f.split(CASE_DIFFERS, 1)[1] for f in plan.flags if CASE_DIFFERS in f]
    mixed = build("21 05 53", MIXED)
    assert flags(mixed) == [f"the section title “{MIXED}” will show in mixed case in the headers/footers, where the "
                            f"template shows “DOOR HARDWARE” in capitals. It can show in capitals like the template's "
                            f"through formatting, without retyping a letter."]
    assert flags(mixed, caps_fills=["SECTION_TITLE"]) == []                # asked for: nothing to say
    assert flags(build("21 05 17", "SLEEVES AND SLEEVE SEALS")) == []      # typed in capitals already
    # a template that shows the title in capitals by formatting shows every fill so: nothing to say either
    shown = tmp_path / "shown.docx"
    shown.write_bytes(mutate(build_reference_spec(title="Door Hardware", header_title="Door Hardware"),
                             _mark_title_runs("caps")))
    spec = {"request": "Match the template.", "summary": "template", "mode": "formatting",
            "ops": [{"op": OP, "select": {"sections": "all"}, "params": params(shown)}]}
    plan, _ = plan_spec(DocxPackage(mixed), spec)
    assert not any(CASE_DIFFERS in f for f in plan.flags)


def test_every_file_gets_its_note_in_the_preview_text_and_beside_the_pages(template, tmp_path):
    files = []
    for n, title in (("21 05 17", "SLEEVES AND SLEEVE SEALS"), ("21 05 53", MIXED), ("21 05 29", "Hangers")):
        path = tmp_path / f"{n}.docx"
        path.write_bytes(build(n, title))
        files.append(path)
    spec = {"request": "Match the template.", "summary": "template", "mode": "formatting",
            "ops": [{"op": OP, "select": {"sections": "all"}, "params": params(template)}]}
    bp = plan_batch([str(f) for f in files], spec)
    assert [name for name, _ in bp.case_notes()] == ["21 05 53.docx", "21 05 29.docx"]
    assert "FILLS IN ANOTHER CASE THAN THE TEMPLATE" in bp.to_text(per_file=False)
    warnings = left_elsewhere_warnings(bp)
    assert any(w.startswith("21 05 53.docx: the section title") for w in warnings)


def test_refusals(template, tmp_path):
    src = build("21 05 53", MIXED)
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(template, caps_fills=["NOT_A_SLOT"]))
    assert e.value.code == "bad-params" and "no fill rule" in e.value.reason
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(template, caps_fills="SECTION_TITLE"))
    assert e.value.code == "bad-params"
    small = tmp_path / "small.docx"
    small.write_bytes(mutate(build_reference_spec(), _mark_title_runs("smallCaps")))
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(small, caps_fills=["SECTION_TITLE"]))
    assert e.value.code == "bad-params" and "small capitals" in e.value.reason
    # as set_properties: capitals never touch a symbol-font run (another glyph) (Codex on #81)
    symbol = tmp_path / "symbol.docx"
    symbol.write_bytes(mutate(build_reference_spec(), _mark_title_runs("rFonts", ascii="Wingdings", hAnsi="Wingdings")))
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(symbol, caps_fills=["SECTION_TITLE"]))
    assert e.value.code == "bad-params" and "symbol font" in e.value.reason
    get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(symbol))      # without caps_fills it plans


def test_planted_defects_are_caught(template, monkeypatch):
    src = build("21 05 53", MIXED)
    prm = params(template, caps_fills=["SECTION_TITLE"])

    class NoCaps(tmpl_module.ApplyHeaderFooterTemplate):          # wrong output: the capitals never written
        def apply(self, pkg, frag):
            frag = copy.deepcopy(frag)
            for c in frag.transform_data["copies"]:
                for sl in c["slots"].values():
                    for s in sl:
                        s.pop("caps", None)
            super().apply(pkg, frag)
    _, _, rec = run(src, prm, apply_with=NoCaps())
    assert "V10" in failed(rec)

    class AllCaps(tmpl_module.ApplyHeaderFooterTemplate):         # wrong output: capitals on every run
        def apply(self, pkg, frag):
            super().apply(pkg, frag)
            for part in frag.transform_data["copies"]:
                root = pkg.xml(part["part"])
                for r in root.iter(W + "r"):
                    rpr = r.find(W + "rPr")
                    if rpr is None:
                        rpr = etree.Element(W + "rPr")
                        r.insert(0, rpr)
                    if rpr.find(W + "caps") is None:
                        etree.SubElement(rpr, W + "caps")
                pkg.write_xml(part["part"], root)
    _, _, rec = run(src, prm, apply_with=AllCaps())
    assert "V10" in failed(rec)

    # a wrong implementation shared by plan and apply: the letters retyped in capitals instead of w:caps
    real = tmpl_module.extract_fill
    monkeypatch.setattr(tmpl_module, "extract_fill",
                        lambda pkg, name, rule: real(pkg, name, rule).upper() if name == "SECTION_TITLE"
                        else real(pkg, name, rule))
    _, out, rec = run(src, params(template))
    assert any(MIXED.upper() in t for _, t, _ in hf_runs(out))     # the planted defect did what it does
    assert "V10" in failed(rec)


def test_the_prompt_and_the_catalog_say_how_capitals_like_the_template_are_done():
    prompt = compiler.system_prompt(chat=True)
    assert 'caps_fills ["SECTION_TITLE"]' in prompt and "FILLS IN ANOTHER CASE THAN THE TEMPLATE" in prompt
    from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_sheet
    assert any("caps_fills" in a for a in COMMON_ASSUMPTIONS[OP])
    assert "caps_fills" in op_sheet()["ops"][OP]["params"]["properties"]
