"""N3 (docs/SPEC_FORMATTER_MODES_DESIGN.md): the header/footer template carries Word's even/odd page switch
(``w:evenAndOddHeaders``) when the user asks for it — ``even_odd: "template"`` — and never otherwise.

Pinned here: the default still refuses (Safe mode's buttons never pass the option, so they are unchanged); the switch
turned off, inserted at its CT_Settings place, and a switch written off turned on, each end to end (plan → merge →
apply → run_suite); already as asked changes nothing; one kind alone and a document with no settings part are
refused with their reasons; and planted defects — a wrong value written by apply, a wrong reading shared by plan and
apply, a misplaced insertion shared by both, another setting touched — each caught by the verifier's own reading."""
from __future__ import annotations

import copy
import io
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from make_fixtures import TEMPLATE_FILLS, build, build_rich, build_template
from spec_surgeon import ui_feedback
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import apply_header_footer_template as tmpl_module
from spec_surgeon.ops import docprops as docprops_module
from spec_surgeon.ops.apply_header_footer_template import ApplyHeaderFooterTemplate
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite

OP = "apply_header_footer_template"
SWITCH = W + "evenAndOddHeaders"
SETTINGS = "word/settings.xml"
DOC_RELS = "word/_rels/document.xml.rels"


def _rewrite(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == part:
                root = etree.fromstring(data)
                fn(root)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, data)
    return out.getvalue()


def _insert_switch(root, val=None):
    el = etree.Element(SWITCH)
    if val is not None:
        el.set(W + "val", val)
    root.find(W + "defaultTabStop").addnext(el)


def _switches(docx: bytes):
    pkg = DocxPackage(docx)
    return [c for c in pkg.xml(pkg.settings_part()) if isinstance(c.tag, str) and c.tag == SWITCH]


@pytest.fixture(scope="module")
def template_off(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("tpl-off") / "TEMPLATE.docx"
    p.write_bytes(build_template())
    return p


@pytest.fixture(scope="module")
def template_on(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("tpl-on") / "TEMPLATE.docx"
    p.write_bytes(_rewrite(build_template(), SETTINGS, _insert_switch))
    return p


@pytest.fixture(scope="module")
def plain() -> bytes:                                     # the switch off (absent)
    return build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION")


def params(tpl: Path, **kw) -> dict:
    d = {"template": str(tpl), "template_sections": {"cover": 0, "body": 1},
         "slot_fills": copy.deepcopy(TEMPLATE_FILLS)}
    d.update(kw)
    return d


def run(src: bytes, prm: dict, apply_with=None):
    pkg = DocxPackage(src)
    frag = get_op(OP).plan(pkg, {"sections": "all"}, prm)
    plan = merge_fragments([frag])
    work = pkg.copy()
    (apply_with or get_op(OP)).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def _record(exc: JobException) -> dict:
    """A refusal as the run records it (what ui_feedback.refusal_message reads)."""
    return {"code": exc.code, "reason": str(exc), "details": exc.details}


def v10(rec) -> str:
    check = rec.get("V10")
    return check.summary + " " + str(check.data)


# ---- the default: never changed silently ------------------------------------------------------------------------

def test_the_default_still_refuses_a_document_set_otherwise(template_off, template_on, plain):
    for src, tpl, on in ((build_rich(), template_off, False), (plain, template_on, True)):
        with pytest.raises(JobException) as e:
            get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(tpl))
        assert e.value.code == "even-odd" and e.value.details == {"template_even_odd": on}
        assert "not changed silently" in str(e.value)
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(plain), {"sections": "all"}, params(template_on, even_odd="refuse"))
    assert e.value.code == "even-odd"


def test_the_default_plan_is_todays_plan(template_off, plain):
    """Safe mode's buttons never pass even_odd: their plan carries no new assertion, member or touch."""
    frag = get_op(OP).plan(DocxPackage(plain), {"sections": "all"}, params(template_off))
    assert not any(a["kind"] == "hf_even_odd" for a in frag.property_assertions)
    assert SETTINGS not in frag.intended_parts
    assert frag.transform_data["even_odd"] is None


def test_an_unknown_value_is_refused(template_off, plain):
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(plain), {"sections": "all"}, params(template_off, even_odd="yes"))
    assert e.value.code == "bad-params"


# ---- even_odd "template", end to end ------------------------------------------------------------------------------

def test_the_switch_turned_on_is_inserted_at_its_place(template_on, plain):
    plan, out, rec = run(plain, params(template_on, even_odd="template"))
    assert rec.passed, rec.to_markdown()
    els = _switches(out)
    assert len(els) == 1 and not els[0].attrib
    names = [etree.QName(c).localname for c in DocxPackage(out).xml(SETTINGS) if isinstance(c.tag, str)]
    assert names.index("evenAndOddHeaders") == names.index("defaultTabStop") + 1
    assert SETTINGS in plan.intended_parts
    assert "hf_even_odd" in rec.get("V10").summary and "changed" in rec.get("V10").summary
    assert any("turned on" in n for n in plan.notes)
    # everything else in the settings part is the source's
    src_rest = [etree.tostring(c) for c in DocxPackage(plain).xml(SETTINGS)]
    out_rest = [etree.tostring(c) for c in DocxPackage(out).xml(SETTINGS) if c.tag != SWITCH]
    assert src_rest == out_rest


def test_the_switch_turned_off_is_removed(template_off):
    src = build_rich()                                    # the switch on
    assert _switches(src)
    plan, out, rec = run(src, params(template_off, even_odd="template"))
    assert rec.passed, rec.to_markdown()
    assert _switches(out) == []
    assert any("turned off" in n for n in plan.notes)


def test_a_switch_written_off_is_turned_on(template_on):
    src = _rewrite(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"), SETTINGS,
                   lambda r: _insert_switch(r, "0"))
    assert not DocxPackage(src).settings_flag("evenAndOddHeaders")
    plan, out, rec = run(src, params(template_on, even_odd="template"))
    assert rec.passed, rec.to_markdown()
    els = _switches(out)
    assert len(els) == 1 and not els[0].attrib


def test_already_as_asked_changes_nothing(template_off, plain):
    plan, out, rec = run(plain, params(template_off, even_odd="template"))
    assert rec.passed, rec.to_markdown()
    assert SETTINGS not in plan.intended_parts
    assert DocxPackage(out).read(SETTINGS) == DocxPackage(plain).read(SETTINGS)
    assert "already as asked" in rec.get("V10").summary
    assert any("already the template's" in n for n in plan.notes)


def test_one_kind_alone_is_refused(template_on, plain):
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(plain), {"sections": "all"},
                        params(template_on, even_odd="template", kinds=["header"]))
    assert e.value.code == "even-odd" and e.value.details["why"] == "one kind"
    assert "headers and footers alike" in ui_feedback.refusal_message(_record(e.value), step=False)


def test_a_document_without_a_settings_part_is_refused(template_on, plain):
    def drop_settings(root):
        for rel in list(root):
            if rel.get("Type", "").endswith("/settings"):
                root.remove(rel)
    src = _rewrite(plain, DOC_RELS, drop_settings)
    assert DocxPackage(src).settings_part() is None
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(template_on, even_odd="template"))
    assert e.value.code == "even-odd" and e.value.details["why"] == "no settings part"
    assert "no settings part" in ui_feedback.refusal_message(_record(e.value), step=False)


def test_two_switches_are_refused(template_off):
    src = _rewrite(build_rich(), SETTINGS, lambda r: _insert_switch(r))
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(src), {"sections": "all"}, params(template_off, even_odd="template"))
    assert e.value.code == "malformed-properties"


def test_the_sentence_says_it():
    text = ApplyHeaderFooterTemplate().describe({"sections": "all"}, params(Path("t.docx"), even_odd="template"))
    assert "Different Odd & Even Pages" in text
    assert "Different Odd" not in ApplyHeaderFooterTemplate().describe({"sections": "all"}, params(Path("t.docx")))


# ---- planted defects: the verifier's own reading catches each -----------------------------------------------------

class _WrongValue(ApplyHeaderFooterTemplate):
    """apply writes the switch off where the plan turns it on."""
    def apply(self, pkg, frag):
        super().apply(pkg, frag)
        member = frag.transform_data["even_odd"]["member"]
        root = pkg.xml(member)
        root.find(SWITCH).set(W + "val", "0")
        pkg.write_xml(member, root)


def test_a_wrong_value_written_by_apply_is_caught(template_on, plain):
    plan, out, rec = run(plain, params(template_on, even_odd="template"), apply_with=_WrongValue())
    assert not rec.passed and "V10" in failed(rec)
    assert "hf_even_odd" in v10(rec)


def test_a_wrong_reading_shared_by_plan_and_apply_is_caught(template_on, plain, monkeypatch):
    """Both sides read every switch as off: the plan predicts no change, apply makes none; the verifier reads the
    template's own switch and finds the output's is not it."""
    monkeypatch.setattr(tmpl_module, "even_odd_switch", lambda pkg: False)
    plan, out, rec = run(plain, params(template_on, even_odd="template"))
    assert _switches(out) == []
    assert not rec.passed and "V10" in failed(rec)
    assert "not the template's" in v10(rec)


def test_a_misplaced_insertion_shared_by_plan_and_apply_is_caught(template_on, plain, monkeypatch):
    monkeypatch.setattr(docprops_module, "settings_rank", lambda el: 10 ** 6)     # appended after everything
    plan, out, rec = run(plain, params(template_on, even_odd="template"))
    assert _switches(out)
    assert not rec.passed
    assert "schema order" in v10(rec) or "V5a" in failed(rec)


class _AnotherSetting(ApplyHeaderFooterTemplate):
    """apply also drops the zoom setting: nothing predicted it."""
    def apply(self, pkg, frag):
        super().apply(pkg, frag)
        member = frag.transform_data["even_odd"]["member"]
        root = pkg.xml(member)
        root.remove(root.find(W + "zoom"))
        pkg.write_xml(member, root)


def test_another_setting_touched_is_caught_by_the_residual(template_on, plain):
    plan, out, rec = run(plain, params(template_on, even_odd="template"), apply_with=_AnotherSetting())
    assert not rec.passed and "V10" in failed(rec)
    assert "unpredicted change" in v10(rec)
