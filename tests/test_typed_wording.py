"""Each document's own wording in a template slot (the 1.9.0 live run of 2026-10-22, test-set-2).

The user asked that the footer titles follow the template's capitalization: the template shows "Specification Name"
(title case), the files type their titles in capitals. Word shows capitals and small capitals through formatting,
never title case, so title case is wording — the user's, per document. A spec binding's ``typed`` slot is that
wording for one document: placed exactly as approved (``compiler.placed_words`` traces it to the user's words),
pinned to the document's bytes, and given to every document the spec covers. The assistant's reply in the live run
offered "ten one-file projects" instead, because a literal fill is one value for every file."""
from __future__ import annotations

import copy
import hashlib

import pytest

from make_fixtures import TEMPLATE_FILLS, build, build_template
from spec_surgeon import compiler, editspec, review_display
from spec_surgeon.editspec import SpecError, apply_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException
from spec_surgeon.ops import apply_header_footer_template as tmpl_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite as _run_suite

TITLES = {"wet.docx": ("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", "Wet-Pipe Sprinkler Systems"),
          "chillers.docx": ("23 64 00", "PACKAGED WATER CHILLERS", "Packaged Water Chillers")}
APPROVED = "wet — Wet-Pipe Sprinkler Systems · chillers — Packaged Water Chillers"   # the picked draft, one line
STAMP = TEMPLATE_FILLS["ISSUED FOR PERMIT"]["value"]       # the fixture template's other literal, typed by the user


def run_suite(source, output, plan):
    return _run_suite(source, output, plan, "source.docx", "output.docx")


@pytest.fixture
def batch(tmp_path):
    template = tmp_path / "template.docx"
    template.write_bytes(build_template())
    files = []
    for name, (number, title, _) in TITLES.items():
        path = tmp_path / name
        path.write_bytes(build(number, title))
        files.append(path)
    return template, files


def typed_spec(template, files, titles=None, mode="untracked"):
    fills = copy.deepcopy(TEMPLATE_FILLS)
    fills["SECTION_TITLE"] = {"rule": "paragraph_after", "source": "document",
                              "anchor_regex": r"^SECTION \d{2} \d{2} \d{2}$"}
    titles = titles or {p.name: TITLES[p.name][2] for p in files}
    return {"request": "Match the template's capitalization", "summary": "Template header and footer, titles as approved",
            "mode": mode,
            "bindings": [{"file": str(p.resolve()), "md5": hashlib.md5(p.read_bytes()).hexdigest(),
                          "slots": {"SECTION_TITLE": {"typed": titles[p.name]}}} for p in files if p.name in titles],
            "ops": [{"op": "apply_header_footer_template",
                     "params": {"template": str(template), "template_sections": {"cover": 0, "body": 1},
                                "slot_fills": fills}}]}


def hf_text(docx: bytes) -> str:
    pkg = DocxPackage(docx)
    return "\n".join("".join(t.text or "" for t in pkg.xml(m.filename).iter(W + "t")) for m in pkg.members
                     if m.filename.startswith(("word/header", "word/footer")) and m.filename.endswith(".xml"))


def body_text(docx: bytes) -> str:
    pkg = DocxPackage(docx)
    return "".join(t.text or "" for t in pkg.xml(pkg.main_part()).iter(W + "t"))


def template_assertions(plan):
    return [a for a in plan["property_assertions"] if a["kind"] == "template_parts"]


def test_each_document_gets_its_own_approved_wording_and_passes_the_checker(batch):
    template, files = batch
    spec = typed_spec(template, files)
    for path in files:
        pkg = DocxPackage.from_path(path)
        plan, fragments = plan_spec(pkg, spec)
        assert plan.mode == "untracked"
        output = apply_spec(pkg, fragments).output_bytes()
        record = run_suite(pkg.data, output, plan.to_dict())
        assert record.passed, record.to_markdown()
        number, typed_caps, wording = TITLES[path.name]
        assert wording in hf_text(output) and typed_caps not in hf_text(output)
        assert typed_caps in body_text(output)                     # the document's own title is left as typed
        # the assertion carries the spec's wording verbatim, pinned to this document's bytes
        rules = [a["fills"]["SECTION_TITLE"] for a in template_assertions(plan.to_dict()) if "SECTION_TITLE" in a["fills"]]
        assert rules and all(r == {"rule": "typed", "value": wording, "source_md5": hashlib.md5(pkg.data).hexdigest()}
                             for r in rules)


def test_the_checker_rechecks_the_wording_and_the_document_it_belongs_to(batch):
    template, files = batch
    pkg = DocxPackage.from_path(files[0])
    plan, fragments = plan_spec(pkg, typed_spec(template, files))
    output, predicted = apply_spec(pkg, fragments).output_bytes(), plan.to_dict()
    for change in ({"value": "Packaged Water Chillers"}, {"source_md5": hashlib.md5(files[1].read_bytes()).hexdigest()},
                   {"value": "   "}):
        tampered = copy.deepcopy(predicted)
        for assertion in template_assertions(tampered):
            if "SECTION_TITLE" in assertion["fills"]:
                assertion["fills"]["SECTION_TITLE"].update(change)
        assert run_suite(pkg.data, output, tampered).get("V10").status == "FAIL", change


def test_planted_defects_are_caught(batch, monkeypatch):
    template, files = batch
    wet, chillers = files
    spec = typed_spec(template, files)
    # a wrong output: the apply places other words than the plan predicted
    pkg = DocxPackage.from_path(wet)
    plan, fragments = plan_spec(pkg, spec)
    for f in fragments:
        if f.op == "apply_header_footer_template":
            f.transform_data["fills"]["SECTION_TITLE"] = "Wet Pipe Sprinkler Systems"
    record = run_suite(pkg.data, apply_spec(pkg, fragments).output_bytes(), plan.to_dict())
    assert not record.passed and record.get("V10").status == "FAIL"
    # a wrong implementation shared by plan and apply: another document's wording, its own byte check gone
    real_bindings, real_extract = editspec._file_bindings, tmpl_module.extract_fill
    other = {"rule": "typed", "value": "Packaged Water Chillers",
             "source_md5": hashlib.md5(chillers.read_bytes()).hexdigest()}
    monkeypatch.setattr(editspec, "_file_bindings", lambda pkg, spec: {"SECTION_TITLE": dict(other)})
    monkeypatch.setattr(tmpl_module, "extract_fill", lambda pkg, name, rule: rule["value"]
                        if isinstance(rule, dict) and rule.get("rule") == "typed" else real_extract(pkg, name, rule))
    plan, fragments = plan_spec(pkg, spec)
    output = apply_spec(pkg, fragments).output_bytes()
    assert "Packaged Water Chillers" in hf_text(output)
    record = run_suite(pkg.data, output, plan.to_dict())
    assert record.get("V10").status == "FAIL"
    assert "typed wording bound to another source" in record.to_markdown()
    monkeypatch.setattr(editspec, "_file_bindings", real_bindings)


def test_the_engine_refuses_wording_bound_to_other_bytes(batch):
    template, files = batch
    spec = typed_spec(template, files)
    spec["bindings"][0]["md5"] = hashlib.md5(files[1].read_bytes()).hexdigest()
    with pytest.raises(JobException, match="bound source changed"):
        plan_spec(DocxPackage.from_path(files[0]), spec)


@pytest.mark.parametrize("slot", [{"typed": ""}, {"typed": "Two\nlines"}, {"typed": 7},
                                  {"typed": "Wet", "part": "word/document.xml"}])
def test_the_spec_refuses_typed_wording_that_is_not_one_line_of_text(batch, slot):
    template, files = batch
    spec = typed_spec(template, files)
    spec["bindings"][0]["slots"]["SECTION_TITLE"] = slot
    with pytest.raises(SpecError):
        validate_spec(spec)


def test_the_wording_is_the_users_and_every_planned_document_has_its_own(batch):
    template, files = batch
    wet, chillers = files
    b = compiler._Batch(files, template)
    spec = typed_spec(template, files)
    assert set(TITLES[p.name][2] for p in files) <= set(compiler.placed_words(spec))
    # drafted by the assistant, not yet approved: refused; picked (or typed) by the user: accepted
    assert compiler.check_submission(spec, b, ["Match the template's capitalization", STAMP])
    assert compiler.check_submission(spec, b, ["Match the template's capitalization", STAMP, APPROVED]) == []
    # one document without its wording: refused by name, unless the user left it out of the request
    one = typed_spec(template, files, titles={"wet.docx": "Wet-Pipe Sprinkler Systems"})
    errs = compiler.check_submission(one, b, [STAMP, APPROVED])
    assert errs and "[bindings]" in errs[0] and "chillers.docx" in errs[0]
    assert compiler.check_submission(one, b, [STAMP, APPROVED], leave_out=[chillers]) == []
    # a binding for a document the user left out is refused too
    errs = compiler.check_submission(spec, b, [STAMP, APPROVED], leave_out=[chillers])
    assert errs and "[leave-out] chillers.docx is left out" in errs[0]
    # the spec's own mode must say what the wording makes it
    errs = compiler.check_submission(typed_spec(template, files, mode="formatting"), b, [STAMP, APPROVED])
    assert errs and "spec-mode" in " ".join(errs)


def test_the_window_and_the_preview_say_whose_wording_it_is(batch):
    template, files = batch
    spec = typed_spec(template, files)
    rows = review_display.spec_bindings(spec)
    assert rows[0]["details"] == ["SECTION TITLE: Wet-Pipe Sprinkler Systems (your wording for this document)"]
    from spec_surgeon.preview import make_preview
    pv = make_preview(spec, [str(p) for p in files], files[0].parent / "preview", renderer="none", do_render=False)
    text = pv.to_text()
    assert "SECTION_TITLE: 'Wet-Pipe Sprinkler Systems' (the user's wording for this file)" in text
    assert all(s.passed for s in pv.samples) and not pv.batch_plan.exceptions
    # a case-only difference from the document's own title says nothing; other words say where the old ones remain
    assert pv.batch_plan.left_elsewhere() == [] and pv.batch_plan.case_notes() == []
    other = typed_spec(template, files, titles={"wet.docx": "Automatic Wet Sprinklers",
                                                "chillers.docx": "Packaged Water Chillers"})
    pv = make_preview(other, [str(p) for p in files], files[0].parent / "preview2", renderer="none", do_render=False)
    notes = pv.batch_plan.left_elsewhere()
    assert [name for name, _ in notes] == ["wet.docx"] and "Automatic Wet Sprinklers" in notes[0][1]


def test_the_prompt_teaches_title_case_as_the_users_wording():
    prompt = compiler.system_prompt(chat=True)
    assert "{typed: the user's own wording for this document}" in prompt
    assert "never title case, so title case is wording" in prompt
    assert "one plan for the whole batch, never one per document" in prompt
    from spec_surgeon.catalog import COMMON_ASSUMPTIONS, editspec_json_schema
    slot = editspec_json_schema()["properties"]["bindings"]["items"]["properties"]["slots"]["additionalProperties"]
    assert [sorted(o["required"]) for o in slot["oneOf"]] == [["paragraph", "part", "value"], ["typed"]]
    assert any("typed wording" in a for a in COMMON_ASSUMPTIONS["apply_header_footer_template"])
