"""remove_hyperlinks and unlink_fields (built-in batch 2, docs/BUILTIN_OPS_BATCH2_DESIGN.md §9, §10): links come out and
fields become the text they show — the words a reader sees never change — end to end through the whole suite; which
links and fields are selected; the addresses that leave the file; the refusals; the both-sides definitions compared;
and planted defects (wrong outputs, and wrong implementations shared by plan and apply) caught by the verifier."""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

import pytest
from docx import Document
from lxml import etree

from fx_links import awkward, build_fields, build_links, styles
from make_fixtures import docx_bytes
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import fields as fields_module
from spec_surgeon.ops import links as links_module
from spec_surgeon.ops.fields import fields_of
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, R, W
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import links as vlinks
from spec_surgeon.verify.fields import v_fields
from spec_surgeon.verify.props import Styles
from spec_surgeon.verify.reader import VPackage
from test_text_edits import failed, mutate, v10_errors

DOC = "word/document.xml"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"


@pytest.fixture(scope="module")
def links() -> bytes:
    return build_links()


@pytest.fixture(scope="module")
def fields_doc() -> bytes:
    return build_fields()


def run_op(name: str, src: bytes, params: Dict[str, Any], select: Optional[Dict[str, Any]] = None):
    pkg = DocxPackage(src)
    frag = get_op(name).plan(pkg, select or {}, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(name).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def parts(data: bytes) -> Dict[str, str]:
    kinds = DocxPackage(data).parts_by_kind()
    return {k: v[0] for k, v in kinds.items() if v}


def display(data: bytes) -> Dict[str, List[str]]:
    """What a reader sees, paragraph by paragraph, in every text part: the w:t text."""
    pkg = DocxPackage(data)
    return {part: ["".join(t.text or "" for t in p.iter(W + "t")) for p in pkg.xml(part).iter(W + "p")]
            for part in pkg.text_parts()}


def hyperlinks(data: bytes, part: str = DOC) -> List[tuple]:
    root = DocxPackage(data).xml(part)
    return [(h.get(R + "id"), h.get(W + "anchor"), "".join(t.text or "" for t in h.iter(W + "t")))
            for h in root.iter(W + "hyperlink")]


def instructions(data: bytes, part: str = DOC) -> List[str]:
    root = DocxPackage(data).xml(part)
    return [" ".join((t.text or "").split()) for t in root.iter(W + "instrText")] + \
        [" ".join((f.get(W + "instr") or "").split()) for f in root.iter(W + "fldSimple")]


def targets(data: bytes, part: str = DOC) -> Dict[str, str]:
    pkg = DocxPackage(data)
    return {rid: r.target for rid, r in pkg.rels(part).items()}


def run_styles(data: bytes, text: str, part: str = DOC) -> List[Optional[str]]:
    """The rStyle of every run holding ``text``."""
    root = DocxPackage(data).xml(part)
    out = []
    for r in root.iter(W + "r"):
        if "".join(t.text or "" for t in r.iter(W + "t")) == text:
            rs = r.find(f"{W}rPr/{W}rStyle")
            out.append(rs.get(W + "val") if rs is not None else None)
    return out


def member(data: bytes, name: str) -> bytes:
    return DocxPackage(data).read(name)


# ---- remove_hyperlinks -------------------------------------------------------------------------------------------------

def test_external_links_go_and_the_words_stay(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})
    assert rec.passed, rec.to_markdown()
    assert display(out) == display(links)                              # not one visible word moved
    assert hyperlinks(out) == [(None, "_Ref1", "Article 1.02")]         # the internal link stays
    pp = parts(links)
    assert hyperlinks(out, pp["headers"]) == [] and hyperlinks(out, pp["footnotes"]) == []
    left = instructions(out)
    assert 'HYPERLINK \\l "_Toc1"' in left and "DOCPROPERTY Title" in left and "REF _Ref123 \\h" in left
    assert not any("example.com/spec" in i or "example.com/a" in i for i in left)
    assert member(out, pp["footers"]) == member(links, pp["footers"])  # the page number is never touched
    # the addresses: gone with their links — except the one a picture's click link still uses
    gone = set(targets(links).values()) - set(targets(out).values())
    assert gone == {"https://example.com/owner", "https://example.com/shared", "https://example.com/plain",
                    "https://example.com/followed", "https://example.com/em", "https://example.com/ins"}
    assert "https://example.com/pic" in targets(out).values()
    assert "https://acme.example/" not in targets(out, pp["headers"]).values()
    assert targets(out, pp["footnotes"]) == {}
    # the look: link styles off, another character style kept, hand-made formatting flagged
    assert run_styles(out, "Owner portal") == [None] and run_styles(out, "followed link") == [None]
    assert run_styles(out, "example.com/spec") == [None]
    assert run_styles(out, "emphasised link") == ["EmphasisTwo"]
    assert run_styles(out, "Part 1 General") == ["Hyperlink"]           # the internal field's result: untouched
    assert run_styles(out, "inserted link") == [None] and run_styles(out, "simple link") == [None]
    ins = next(DocxPackage(out).xml(DOC).iter(W + "ins"))                  # the pending insertion keeps the words
    assert "".join(t.text for t in ins.iter(W + "t")) == "inserted link" and rec.get("V4").status == "PASS"
    assert not list(DocxPackage(out).xml(DOC).iter(W + "fldSimple"))
    assert any("plain blue link" in f for f in plan.flags)
    assert any("still uses them" in n for n in plan.notes)
    changed = set(rec.get("V1").data["changed"])
    assert changed == {DOC, pp["headers"], pp["footnotes"], "word/_rels/document.xml.rels",
                       DocxPackage(links).rels_path_for(pp["headers"]), "word/_rels/footnotes.xml.rels"}


def test_internal_links_only(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {"which": "internal"})
    assert rec.passed, rec.to_markdown()
    assert display(out) == display(links)
    assert all(rid is not None for rid, _, _ in hyperlinks(out))
    assert 'HYPERLINK \\l "_Toc1"' not in instructions(out)
    assert 'HYPERLINK "https://example.com/a\\l"' in instructions(out)      # a \l inside the address: external
    assert run_styles(out, "Part 1 General") == [None] and run_styles(out, "Article 1.02") == [None]
    assert run_styles(out, "Owner portal") == ["Hyperlink"]
    assert targets(out) == targets(links)
    assert "word/_rels/document.xml.rels" not in rec.get("V1").data["changed"]


def test_all_links_and_keep_style(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {"which": "all", "keep_style": True})
    assert rec.passed, rec.to_markdown()
    assert display(out) == display(links)
    assert hyperlinks(out) == [] and not any(i.startswith("HYPERLINK") for i in instructions(out))
    assert run_styles(out, "Owner portal") == ["Hyperlink"] and run_styles(out, "Part 1 General") == ["Hyperlink"]


def test_through_a_spec_with_scope(links):
    spec = {"request": "remove the web links from the body only", "summary": "s", "mode": "formatting",
            "ops": [{"op": "remove_hyperlinks", "select": {"scope": ["document"]}}]}
    pkg = DocxPackage(links)
    plan, frags = plan_spec(pkg, spec)
    out = apply_spec(pkg, frags).output_bytes()
    rec = run_suite(links, out, plan.to_dict(), "s", "o")
    assert rec.passed, rec.to_markdown()
    pp = parts(links)
    assert member(out, pp["headers"]) == member(links, pp["headers"])
    assert hyperlinks(out, pp["footnotes"]) == hyperlinks(links, pp["footnotes"])


def test_nothing_to_remove_is_a_no_op_unless_asked():
    doc = Document()
    styles(doc)
    doc.add_paragraph("No links here.")
    data = docx_bytes(doc)
    plan, out, rec = run_op("remove_hyperlinks", data, {})
    assert rec.passed and out == data and any("nothing to do" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        run_op("remove_hyperlinks", data, {"allow_missing": False})
    assert e.value.code == "not-found"


@pytest.mark.parametrize("kind,code", [("textbox-link", "textbox"), ("span", "field-span"),
                                       ("revision", "existing-revisions"), ("nested", "nested-field")])
def test_remove_hyperlinks_refusals(kind, code):
    with pytest.raises(JobException) as e:
        run_op("remove_hyperlinks", awkward(kind), {})
    assert e.value.code == code


@pytest.mark.parametrize("params", [{"which": "web"}, {"keep_style": "yes"}, {"allow_missing": 1}])
def test_remove_hyperlinks_bad_params(links, params):
    with pytest.raises(JobException) as e:
        run_op("remove_hyperlinks", links, params)
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        run_op("remove_hyperlinks", links, {}, select={"paragraphs": "all"})
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("name,build", [("remove-hyperlinks", build_links), ("unlink-docproperty-fields", build_fields)])
def test_the_worked_examples_plan_and_pass(name, build, tmp_path):
    from pathlib import Path
    from spec_surgeon.batch import run_batch
    from spec_surgeon.editspec import load_spec
    spec = load_spec(Path(__file__).resolve().parent.parent / "examples" / "specs" / f"{name}.json")
    (tmp_path / "21 13 13 – SPEC.docx").write_bytes(build())
    batch = run_batch([str(tmp_path / "21 13 13 – SPEC.docx")], "edit-spec", {}, tmp_path / "out", workers=1,
                      renderer="none", do_render=False, spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


def test_conflicts_with_an_op_that_removes_a_part_in_scope(links):
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "remove_header_footer", "select": {"kinds": ["header"], "types": ["default"]}},
                    {"op": "remove_hyperlinks"}]}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(links), spec)
    assert e.value.code == "conflict"


def test_the_two_ops_conflict_on_one_link_field(links):
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "remove_hyperlinks"}, {"op": "unlink_fields", "params": {"types": ["HYPERLINK"]}}]}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(links), spec)
    assert e.value.code == "conflict"
    # on different paragraphs they plan together, in either order (each finds its links and fields again by their
    # paragraph, so the other's removals cannot shift them)
    spec["ops"][1]["params"]["types"] = ["DOCPROPERTY"]
    for ops in (spec["ops"], list(reversed(spec["ops"]))):
        plan, frags = plan_spec(DocxPackage(links), {**spec, "ops": ops})
        out = apply_spec(DocxPackage(links), frags).output_bytes()
        rec = run_suite(links, out, plan.to_dict(), "s", "o")
        assert rec.passed, rec.to_markdown()
        assert "DOCPROPERTY Title" not in instructions(out) and hyperlinks(out) == [(None, "_Ref1", "Article 1.02")]


def test_links_a_table_of_contents_shows_are_flagged():
    data = awkward("toc")
    plan, out, rec = run_op("remove_hyperlinks", data, {"which": "internal"})
    assert rec.passed, rec.to_markdown()
    assert hyperlinks(out) == [] and display(out) == display(data)
    assert sum("sits in what a TOC field shows" in f for f in plan.flags) == 2
    assert any(i.startswith("TOC") for i in instructions(out))


# ---- both sides agree ----------------------------------------------------------------------------------------------

SWITCH_CASES = [' HYPERLINK "https://x.com/a\\l" ', ' HYPERLINK \\l "_Toc1" ', ' HYPERLINK \\L "_Toc1" ',
                ' HYPERLINK "https://x.com" \\o "tip \\l here" ', ' HYPERLINK \\l"_Toc2"', ' HYPERLINK "unterminated \\l',
                ' HYPERLINK C:\\docs\\l.docx ', '', ' \\l ', ' HYPERLINK "a" "b" \\t "_blank" \\l "x" ']


def test_the_two_switch_readers_agree():
    rnd = random.Random(7)
    alphabet = ['"', "\\", "l", "L", " ", "\t", "x", "o", "HYPERLINK", "_Toc", ":", "/"]
    cases = SWITCH_CASES + ["".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 24))) for _ in range(600)]
    for c in cases:
        assert links_module.instruction_switches(c) == vlinks.v_switches(c), c
    assert [any(s.lower() == "\\l" for s in links_module.instruction_switches(c)) for c in SWITCH_CASES] == \
        [False, True, True, False, True, False, False, False, True, True]


@pytest.mark.parametrize("build", [build_links, build_fields, lambda: awkward("nested")])
def test_link_styles_result_runs_and_field_sides_agree(build):
    data = build()
    pkg, vp = DocxPackage(data), VPackage(data)
    styles_part = vp.related("http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles")
    assert links_module.link_style_ids(StyleSheet(pkg)) == vlinks.v_link_styles(Styles(vp.xml(styles_part)))
    for part in pkg.text_parts():
        root = pkg.xml(part)
        for f, g in zip(fields_of(root), v_fields(root)):
            p = f.paragraph()
            assert links_module.field_is_internal(f) == vlinks.v_field_internal(g)
            if p is not None:
                a, b = links_module.result_runs(f, p), vlinks.v_result_runs(g, p)        # both held: one proxy each
                assert [id(r) for r in a] == [id(r) for r in b]


# ---- unlink_fields ---------------------------------------------------------------------------------------------------

def test_docproperty_fields_become_their_text(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["DOCPROPERTY"]})
    assert rec.passed, rec.to_markdown()
    assert display(out) == display(fields_doc)
    pp = parts(fields_doc)
    for part in (DOC, pp["headers"]):
        assert not any(i.startswith("DOCPROPERTY") for i in instructions(out, part))
    left = instructions(out)
    assert "REF _Ref123 \\h" in left and 'XE "sprinklers"' in left and 'DATE \\@ "yyyy"' in left
    assert member(out, pp["footers"]) == member(fields_doc, pp["footers"])
    assert any("Company" in f and "HYPERLINK field shows" in f for f in plan.flags)
    assert any("DOCPROPERTY Manager" in f and "shows nothing" in f for f in plan.flags)
    assert len(plan.diffs) == 5 and all(d.struct_after is not None for d in plan.diffs)
    two = [d for d in plan.diffs if d.before.startswith("H.")][0]
    assert two.after == "H.Two: Spec Title / Sprinklers" and two.struct_after == {"tab": 1}   # a tab is structure


def test_a_simple_field_is_unwrapped_and_its_type_any_case(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["date"]})
    assert rec.passed, rec.to_markdown()
    assert not list(DocxPackage(out).xml(DOC).iter(W + "fldSimple"))
    assert plan.diffs == []                                   # the instruction is an attribute: no block text changes
    assert rec.get("V10").data["residual_parts"] == [DOC]
    assert any("keeps the date" in n for n in plan.notes)


def test_all_but_the_page_numbers(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": "all"})
    assert rec.passed, rec.to_markdown()
    assert display(out) == display(fields_doc)
    pp = parts(fields_doc)
    assert instructions(out) == [] and instructions(out, pp["headers"]) == []
    assert not list(DocxPackage(out).xml(DOC).iter(W + "fldChar"))
    assert member(out, pp["footers"]) == member(fields_doc, pp["footers"])
    assert any('XE "sprinklers"' in f and "shows nothing" in f for f in plan.flags)
    assert not any("HYPERLINK field shows" in f for f in plan.flags)        # the outer link is unlinked too


@pytest.mark.parametrize("types", [["PAGE"], ["numpages", "REF"], ["PageRef"], ["SECTIONPAGES"]])
def test_page_numbers_are_never_converted(fields_doc, types):
    with pytest.raises(JobException) as e:
        run_op("unlink_fields", fields_doc, {"types": types})
    assert e.value.code == "bad-params" and "page numbers" in e.value.reason


@pytest.mark.parametrize("params", [{}, {"types": []}, {"types": [""]}, {"types": 5}, {"types": ["REF"], "allow_missing": "no"}])
def test_unlink_fields_bad_params(fields_doc, params):
    with pytest.raises(JobException) as e:
        run_op("unlink_fields", fields_doc, params)
    assert e.value.code == "bad-params"


def test_a_spec_without_types_is_refused_before_any_file():
    from spec_surgeon.editspec import SpecError, validate_spec
    with pytest.raises(SpecError):
        validate_spec({"request": "r", "summary": "s", "mode": "formatting", "ops": [{"op": "unlink_fields"}]})
    validate_spec({"request": "r", "summary": "s", "mode": "formatting",
                   "ops": [{"op": "unlink_fields", "params": {"types": "all"}}]})


@pytest.mark.parametrize("kind,types,code", [("nested", ["IF"], "nested-field"), ("nested", ["MERGEFIELD"], "nested-field"),
                                             ("form", ["FORMTEXT"], "form-field"),
                                             ("revision", ["DOCPROPERTY"], "existing-revisions"),
                                             ("span", ["HYPERLINK"], "field-span"),
                                             ("textbox-field", ["DOCPROPERTY"], "textbox")])
def test_unlink_fields_refusals(kind, types, code):
    with pytest.raises(JobException) as e:
        run_op("unlink_fields", awkward(kind), {"types": types})
    assert e.value.code == code


def test_no_field_of_the_type_is_a_no_op_unless_asked(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["SEQ"]})
    assert rec.passed and out == fields_doc and any("nothing to do" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        run_op("unlink_fields", fields_doc, {"types": ["SEQ"], "allow_missing": False})
    assert e.value.code == "not-found"


# ---- planted defects: wrong outputs ------------------------------------------------------------------------------------

def _runs_with(root, text: str):
    return [r for r in root.iter(W + "r") if "".join(t.text or "" for t in r.iter(W + "t")) == text]


def test_defect_a_links_words_lost(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def lose(root):
        r = _runs_with(root, "Owner portal")[0]
        r.getparent().remove(r)
    rec = run_suite(links, mutate(out, DOC, lose), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


def test_defect_the_link_style_left_on(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def restyle(root):
        r = _runs_with(root, "Owner portal")[0]
        rpr = etree.SubElement(r, W + "rPr")
        r.insert(0, rpr)
        etree.SubElement(rpr, W + "rStyle").set(W + "val", "Hyperlink")
    rec = run_suite(links, mutate(out, DOC, restyle), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]


def test_defect_an_address_left_in_the_file(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def keep(root):
        el = etree.SubElement(root, REL + "Relationship")
        el.set("Id", "rId9999"); el.set("Type", links_module.HYPERLINK_REL)
        el.set("Target", "https://example.com/owner"); el.set("TargetMode", "External")
    rec = run_suite(links, mutate(out, "word/_rels/document.xml.rels", keep), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]
    assert any("relationship" in e for e in v10_errors(rec))


def test_defect_an_address_still_in_use_removed(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def drop(root):
        for el in root.findall(REL + "Relationship"):
            if el.get("Target") == "https://example.com/pic":
                root.remove(el)
    rec = run_suite(links, mutate(out, "word/_rels/document.xml.rels", drop), plan.to_dict(), "s", "o")
    assert "V1" in failed(rec) and "V10" in failed(rec)          # a picture's link now names nothing


def test_defect_a_field_marker_left(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def put_back(root):
        r = _runs_with(root, "example.com/spec")[0]
        m = etree.Element(W + "r")
        etree.SubElement(m, W + "fldChar").set(W + "fldCharType", "end")
        r.addnext(m)
    rec = run_suite(links, mutate(out, DOC, put_back), plan.to_dict(), "s", "o")
    assert "V3" in failed(rec) and "V10" in failed(rec)


def test_defect_the_internal_link_unwrapped_too(links):
    plan, out, rec = run_op("remove_hyperlinks", links, {})

    def unwrap_internal(root):
        h = next(h for h in root.iter(W + "hyperlink") if h.get(W + "anchor") == "_Ref1")
        links_module.unwrap(h)
    rec = run_suite(links, mutate(out, DOC, unwrap_internal), plan.to_dict(), "s", "o")
    assert failed(rec) == ["V10"]


def test_defect_a_field_result_changed(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["DOCPROPERTY"]})

    def change(root):
        r = _runs_with(root, "Sprinklers")[0]
        r.find(W + "t").text = "Sprinklers!"
    rec = run_suite(fields_doc, mutate(out, DOC, change), plan.to_dict(), "s", "o")
    assert "V2" in failed(rec) and "V10" in failed(rec)


def test_defect_a_field_left_or_another_unlinked(fields_doc):
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["DOCPROPERTY"]})
    ref = run_op("unlink_fields", fields_doc, {"types": ["REF", "DOCPROPERTY"]})[1]
    rec = run_suite(fields_doc, ref, plan.to_dict(), "s", "o")             # the REF field unlinked as well
    assert "V2" in failed(rec) and "V10" in failed(rec)
    left = run_op("unlink_fields", fields_doc, {"types": ["REF"]})[1]
    rec = run_suite(fields_doc, left, plan.to_dict(), "s", "o")            # the DOCPROPERTY fields left as they were
    assert "V2" in failed(rec) and "V10" in failed(rec)


# ---- planted defects shared by plan and apply ------------------------------------------------------------------------

def test_shared_defect_every_link_field_read_as_external(links, monkeypatch):
    monkeypatch.setattr(links_module, "field_is_internal", lambda f: False)
    plan, out, rec = run_op("remove_hyperlinks", links, {})
    assert not any("\\l" in i for i in instructions(out))                # the TOC-style field unlinked by both …
    assert failed(rec) == ["V10"]                                          # … the verifier reads it as internal


def test_shared_defect_another_style_taken_for_the_link_style(links, monkeypatch):
    monkeypatch.setattr(links_module, "link_style_ids", lambda sheet: {"Hyperlink", "FollowedHyperlink", "EmphasisTwo"})
    plan, out, rec = run_op("remove_hyperlinks", links, {})
    assert run_styles(out, "emphasised link") == [None]
    assert failed(rec) == ["V10"]


def test_shared_defect_an_address_still_in_use_planned_away(links, monkeypatch):
    def every_named(pkg, part, root, gone):
        return sorted({h.get(R + "id") for h in gone if h.get(R + "id")})
    monkeypatch.setattr(links_module, "addresses_to_remove", every_named)
    plan, out, rec = run_op("remove_hyperlinks", links, {})
    assert "https://example.com/pic" not in targets(out).values()
    assert "V1" in failed(rec) and "V10" in failed(rec)


def test_shared_defect_a_field_result_left_in_the_link_style(links, monkeypatch):
    monkeypatch.setattr(links_module, "result_runs", lambda f, p: [])
    plan, out, rec = run_op("remove_hyperlinks", links, {})
    assert run_styles(out, "example.com/spec") == ["Hyperlink"]
    assert failed(rec) == ["V10"]


def test_shared_defect_a_type_matched_too_widely(fields_doc, monkeypatch):
    monkeypatch.setattr(links_module, "type_selected", lambda t, types: t in ("DOCPROPERTY", "REF"))
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["DOCPROPERTY"]})
    assert "REF _Ref123 \\h" not in instructions(out)
    assert failed(rec) == ["V10"]


def test_shared_defect_the_result_counted_among_the_markers(fields_doc, monkeypatch):
    real = fields_module.Field.markers

    def with_result(self):
        out = real(self)
        if self.simple is None and self.type == "DOCPROPERTY" and self.separate is not None:
            p = self.paragraph()
            out += [t for r in links_module.result_runs(self, p) for t in r.findall(W + "t")]
        return out
    monkeypatch.setattr(fields_module.Field, "markers", with_result)
    plan, out, rec = run_op("unlink_fields", fields_doc, {"types": ["DOCPROPERTY"]})
    assert "Spec Title" not in "".join(display(out)[DOC])                 # plan predicted it, apply removed it …
    assert rec.get("V2").status == "PASS" and "V10" in failed(rec)         # … the verifier keeps the result
