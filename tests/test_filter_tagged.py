"""filter_tagged_paragraphs (docs/BUILTIN_OPS_BATCH1_DESIGN.md §4): keep or delete the paragraphs tagged for a system,
the tag taken off the ones that stay — end to end tracked and untracked (V2 pairs a stripped paragraph beside a deleted
one), keep vs delete lists, the refusals, and planted defects caught by the verifier."""
from __future__ import annotations

import io
from typing import Any, Dict, List

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from make_fixtures import _ref_field, build_batch1
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import filter_tagged as tagged_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from test_text_edits import accepted_text, failed, mutate, v10_errors

AUTHOR = "Abraham Borg"
TAG = r"^\[(?P<tag>[A-Z /]+)\]\s*"


@pytest.fixture(scope="module")
def doc() -> bytes:
    return build_batch1()


def run_op(src: bytes, params: Dict[str, Any]):
    pkg = DocxPackage(src)
    frag = get_op("filter_tagged_paragraphs").plan(pkg, {}, {"author": AUTHOR, "tag_regex": TAG, **params})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("filter_tagged_paragraphs").apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def visible(data: bytes) -> List[str]:
    """Paragraph texts with every change accepted (tracked-deleted paragraphs gone)."""
    root = DocxPackage(data).xml("word/document.xml")
    out = []
    for p in root.iter(W + "p"):
        mark = p.find(f"{W}pPr/{W}rPr/{W}del")
        text = accepted_text(p)
        if mark is None or text:
            out.append(text)
    return out


SYSTEMS_AFTER = ["Wet system paragraph.", "Preaction system paragraph.", "Wet/Dry system paragraph."]


@pytest.mark.parametrize("tracked", [True, False])
def test_keep_preaction_and_wet(doc, tracked):
    plan, out, rec = run_op(doc, {"keep": ["PREACTION", "wet"], "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    texts = visible(out)
    assert [t for t in texts if "system paragraph" in t] == SYSTEMS_AFTER
    assert any("dry (kept 1, deleted 1)" in n for n in plan.notes)
    if not tracked:
        assert rec.get("V2").status == "PASS"                  # a stripped paragraph right after a deleted one
        assert len(list(DocxPackage(out).xml("word/document.xml").iter(W + "p"))) == \
            len(list(DocxPackage(doc).xml("word/document.xml").iter(W + "p"))) - 2
    else:
        assert plan.rev_delta.get("del_mark") == 2


def test_delete_list_is_the_mirror_of_keep(doc):
    plan, out, rec = run_op(doc, {"delete": ["DRY", "Clean  Agent"], "tracked": False})
    assert rec.passed
    assert [t for t in visible(out) if "system paragraph" in t] == SYSTEMS_AFTER


def test_tags_stay_when_asked(doc):
    plan, out, rec = run_op(doc, {"keep": ["preaction"], "strip_tags": False, "tracked": False})
    assert rec.passed
    assert [t for t in visible(out) if "system paragraph" in t] == ["[PREACTION] Preaction system paragraph."]


def test_params(doc):
    for params in ({"keep": ["wet"], "delete": ["dry"]}, {}, {"keep": ["wet"], "tag_regex": "("}):
        with pytest.raises(JobException) as e:
            run_op(doc, params)
        assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        run_op(doc, {"keep": ["wet"], "tag_regex": r"^<<(\w+)>>"})
    assert e.value.code == "not-found"
    plan, out, rec = run_op(doc, {"keep": ["wet"], "tag_regex": r"^<<(\w+)>>", "allow_missing": True})
    assert rec.passed and out == doc


def _awkward(kind: str) -> bytes:
    d = Document()
    d.add_paragraph("Intro.")
    if kind == "field":
        p = d.add_paragraph("[DRY] See ")
        _ref_field(p)
    elif kind == "revision":
        p = d.add_paragraph("[DRY] Dry text ")
        r = p.add_run("added")
        ins = OxmlElement("w:ins"); ins.set(qn("w:id"), "5"); ins.set(qn("w:author"), "J"); ins.set(qn("w:date"), "2026-01-01T00:00:00Z")
        r._r.addprevious(ins); ins.append(r._r)
    elif kind == "cell":
        t = d.add_table(rows=1, cols=1)
        t.cell(0, 0).paragraphs[0].add_run("[DRY] the only paragraph of a cell")
    elif kind == "section":
        import copy as _copy
        p = d.add_paragraph("[DRY] last paragraph of the first section")       # its mark carries the section break
        p._p.get_or_add_pPr().append(_copy.deepcopy(d.element.body.find(qn("w:sectPr"))))
        d.add_paragraph("Body.")
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("kind,tracked,code", [("field", True, "field-result"), ("revision", True, "existing-revisions"),
                                               ("cell", False, "table-cell"), ("section", True, "section-break")])
def test_refusals(kind, tracked, code):
    with pytest.raises(JobException) as e:
        run_op(_awkward(kind), {"keep": ["wet"], "tracked": tracked})
    assert e.value.code == code


def test_untracked_deletion_drops_pending_revisions_and_flags_them():
    plan, out, rec = run_op(_awkward("revision"), {"keep": ["wet"], "tracked": False})
    assert rec.passed, rec.to_markdown()
    assert plan.rev_delta.get("ins") == -1 and any("pending revisions" in f for f in plan.flags)


# ---- planted defects ---------------------------------------------------------------------------------------------

def test_defect_a_run_left_out_of_the_deletion(doc):
    plan, out, rec = run_op(doc, {"keep": ["wet"], "tracked": True})

    def unwrap(root):
        for d in root.iter(W + "del"):
            if d.getparent().tag == W + "p" and d.get(W + "author") == AUTHOR:
                r = d.find(W + "r")
                for t in r.findall(W + "delText"):
                    t.tag = W + "t"
                d.addprevious(r)
                d.getparent().remove(d)
                break
    rec = run_suite(doc, mutate(out, "word/document.xml", unwrap), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)


def test_shared_defect_multi_system_tags_need_every_system(doc, monkeypatch):
    monkeypatch.setattr(tagged_module, "stays", lambda systems, keep, delete: all(s in keep for s in systems))
    plan, out, rec = run_op(doc, {"keep": ["wet", "preaction"], "tracked": False})
    assert "Wet/Dry system paragraph." not in visible(out)          # deleted by plan and apply alike …
    assert failed(rec) == ["V10"]                                   # … the verifier keeps it


def test_shared_defect_separator_ignored(doc, monkeypatch):
    monkeypatch.setattr(tagged_module, "systems_of", lambda tag, sep: [tagged_module.norm_system(tag)])
    plan, out, rec = run_op(doc, {"keep": ["wet"], "tracked": True})
    assert failed(rec) == ["V10"]
    assert any("tagged_paragraphs" in e for e in v10_errors(rec))


def test_defect_an_extra_paragraph_removed(doc):
    plan, out, rec = run_op(doc, {"keep": ["wet"], "tracked": False})

    def drop(root):
        for p in root.iter(W + "p"):
            if "".join(t.text or "" for t in p.iter(W + "t")).startswith("N."):
                p.getparent().remove(p)
                break
    rec = run_suite(doc, mutate(out, "word/document.xml", drop), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)
