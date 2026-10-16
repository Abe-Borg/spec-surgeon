"""name_output and make_values_agree (docs/BUILTIN_OPS_BATCH1_DESIGN.md §7): output names from the file's own content,
places that agree with the truth the user picks, the batch's naming pre-pass (renames delivered as copies, collisions
refused, never in place), and planted defects the verifier catches."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
from lxml import etree

from make_fixtures import build_batch1
from spec_surgeon.batch import run_batch
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import agree as agree_module
from spec_surgeon.ops import naming as naming_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from test_text_edits import accepted_text, failed, mutate, v10_errors

AUTHOR = "Abraham Borg"
NUMBER = {"rule": "regex", "pattern": r"^SECTION\s+(\d{2} \d{2} \d{2})"}
TITLE = {"rule": "regex", "pattern": r"^SECTION\s+\d{2} \d{2} \d{2}\s*-\s*(.+)$"}
CONVENTION = {"pattern": "{number} - {title}", "values": {"number": NUMBER, "title": TITLE},
              "transforms": {"number": "digits", "title": "title"}}
TITLE_LINE = {"in": "document", "pattern": r"^SECTION\s+(?P<number>\d{2} \d{2} \d{2})\s*-\s*(?P<title>.+)$"}
FILE_NAME = {"in": "filename", "pattern": r"^(?P<number>\d{6}|\d{2} \d{2} \d{2}) - (?P<title>.+)$"}
HEADER = {"in": "headers", "pattern": r"^(?P<number>\d{2} \d{2} \d{2})\t(?P<title>[^\t]+)"}


def plan_and_check(src: bytes, op: str, params: Dict[str, Any], source: str, written_as: str = None):
    pkg = DocxPackage(src, name=source)
    frag = get_op(op).plan(pkg, {}, {"author": AUTHOR, **params} if op != "name_output" else params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    name = written_as or plan.output_name or Path(source).name
    return plan, out, run_suite(src, out, plan.to_dict(), source, f"outputs/{name}")


# ---- name_output -------------------------------------------------------------------------------------------------

def test_the_convention_from_the_users_example():
    plan, out, rec = plan_and_check(build_batch1(), "name_output", CONVENTION, "C:/specs/master 21.docx")
    assert plan.output_name == "211319 - Preaction Sprinkler Systems.docx"
    assert rec.passed and out == build_batch1()                       # the document itself is not changed
    assert plan.mode == "formatting"


@pytest.mark.parametrize("how,value,want", [("digits", "21 13 19", "211319"), ("upper", "Wet-pipe", "WET-PIPE"),
                                            ("lower", "WET", "wet"), ("title", "OWNER'S WET-PIPE SYSTEMS",
                                                                      "Owner's Wet-Pipe Systems"),
                                            ("as_is", "Mixed CASE", "Mixed CASE")])
def test_transforms(how, value, want):
    assert naming_module.transform(value, how) == want


@pytest.mark.parametrize("name", ["a/b.docx", "con.docx", "x .docx", "x..docx", " x.docx", "x\u200b.docx",
                                  ("y" * 200) + ".docx", ".docx"])
def test_names_windows_cannot_hold_are_refused(name):
    with pytest.raises(JobException) as e:
        naming_module.check_name(name)
    assert e.value.code == "bad-name"


def test_params():
    doc = build_batch1()
    for params in ({"pattern": "{number}", "values": {"title": TITLE}},
                   {"pattern": "{number}", "values": {"number": {"rule": "literal", "value": "211319"}}},
                   {"pattern": "{number}", "values": {"number": NUMBER}, "transforms": {"number": "camel"}}):
        with pytest.raises(JobException) as e:
            plan_and_check(doc, "name_output", params, "x.docx")
        assert e.value.code == "bad-params"


def test_defect_the_output_written_under_another_name():
    plan, out, rec = plan_and_check(build_batch1(), "name_output", CONVENTION, "x.docx",
                                    written_as="211319 - Preaction Sprinkler.docx")
    assert failed(rec) == ["V10"]


def test_shared_defect_title_case_by_str_title(monkeypatch):
    real = naming_module.transform
    monkeypatch.setattr(naming_module, "transform", lambda v, how: v.title() if how == "title" else real(v, how))
    src = mutate(build_batch1(), "word/document.xml", lambda root: [
        setattr(t, "text", t.text.replace("PREACTION SPRINKLER SYSTEMS", "OWNER'S SPRINKLER SYSTEMS"))
        for t in root.iter(W + "t") if t.text and "PREACTION SPRINKLER" in t.text])
    plan, out, rec = plan_and_check(src, "name_output", CONVENTION, "x.docx")
    assert plan.output_name == "211319 - Owner'S Sprinkler Systems.docx"           # str.title's apostrophe quirk …
    assert failed(rec) == ["V10"]                                                 # … the verifier writes Owner's


# ---- the batch -----------------------------------------------------------------------------------------------------

def test_batch_renames_copies_refuses_collisions_and_in_place(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.docx").write_bytes(build_batch1("21 13 19", "PREACTION SPRINKLER SYSTEMS"))
    (folder / "b.docx").write_bytes(build_batch1("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "c.docx").write_bytes(build_batch1("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    nameless = mutate(build_batch1(), "word/document.xml", lambda root: [
        setattr(t, "text", "TITLE") for t in root.iter(W + "t") if t.text and t.text.startswith("SECTION ")])
    (folder / "d.docx").write_bytes(nameless)
    spec = {"request": "name the files by number and title", "summary": "s", "mode": "formatting",
            "ops": [{"op": "name_output", "params": CONVENTION}]}
    batch = run_batch([str(folder)], "", {}, tmp_path / "out", workers=1, do_render=False, spec=spec)
    got = {fr.source.name: (fr.status, fr.out_name, (fr.exception or {}).get("code")) for fr in batch.files}
    assert got["a.docx"] == ("PASS", "211319 - Preaction Sprinkler Systems.docx", None)
    assert got["b.docx"][2] == got["c.docx"][2] == "name-collision"
    assert got["d.docx"][0] == "EXCEPTION" and got["d.docx"][2] == "not-found"
    assert sorted(p.name for p in (tmp_path / "out" / "outputs").iterdir()) == ["211319 - Preaction Sprinkler Systems.docx"]
    assert batch.files[0].delivered_to.endswith("211319 - Preaction Sprinkler Systems.docx")
    with pytest.raises(ValueError):
        run_batch([str(folder)], "", {}, tmp_path / "out2", workers=1, do_render=False, spec=spec, in_place=True)
    assert not (tmp_path / "out2").exists()


# ---- make_values_agree ---------------------------------------------------------------------------------------------

def _header(src: bytes, text: str) -> bytes:
    """The header's first paragraph reads ``text`` (a tab between number and title)."""
    def put(root):
        p = next(root.iter(W + "p"))
        for c in [c for c in p if c.tag != W + "pPr"]:
            p.remove(c)
        number, title = text.split("\t")
        for piece in (number, None, title):
            r = etree.SubElement(p, W + "r")
            if piece is None:
                etree.SubElement(r, W + "tab")
            else:
                etree.SubElement(r, W + "t").text = piece
    return mutate(src, "word/header1.xml", put)


@pytest.mark.parametrize("tracked", [True, False])
def test_header_and_file_name_follow_the_title_line(tracked):
    src = _header(build_batch1(), "21 13 13\tWET-PIPE SPRINKLER SYSTEMS")
    plan, out, rec = plan_and_check(src, "make_values_agree",
                                    {"places": {"title": TITLE_LINE, "header": HEADER, "file": FILE_NAME},
                                     "truth": "title", "tracked": tracked},
                                    "C:/specs/211313 - Wet-Pipe Sprinkler Systems.docx")
    assert rec.passed, rec.to_markdown()
    header = next(DocxPackage(out).xml("word/header1.xml").iter(W + "p"))
    assert accepted_text(header) == "21 13 19\tPREACTION SPRINKLER SYSTEMS"
    assert plan.output_name == "21 13 19 - PREACTION SPRINKLER SYSTEMS.docx"


def test_places_that_already_agree_are_never_converted():
    src = _header(build_batch1(), "211319\tPreaction Sprinkler Systems")
    plan, out, rec = plan_and_check(src, "make_values_agree",
                                    {"places": {"title": TITLE_LINE, "header": {**HEADER, "pattern":
                                                r"^(?P<number>\d{6})\t(?P<title>[^\t]+)"}, "file": FILE_NAME},
                                     "truth": "title"}, "C:/specs/211319 - Preaction Sprinkler Systems.docx")
    assert rec.passed and out == src and plan.output_name is None
    assert "every place already agrees" in plan.notes[-1]


def test_the_file_name_as_the_truth():
    plan, out, rec = plan_and_check(build_batch1("21 13 19", "WET-PIPE SPRINKLER SYSTEMS"), "make_values_agree",
                                    {"places": {"title": TITLE_LINE, "file": FILE_NAME}, "truth": "file",
                                     "tracked": False},
                                    "C:/specs/211319 - Preaction Sprinkler Systems.docx")
    assert rec.passed, rec.to_markdown()
    title = [p for p in DocxPackage(out).xml("word/document.xml").iter(W + "p")
             if accepted_text(p).startswith("SECTION")][0]
    assert accepted_text(title) == "SECTION 21 13 19 - Preaction Sprinkler Systems"   # the truth's text, as written
    assert plan.output_name is None


def test_refusals():
    two = mutate(build_batch1(), "word/document.xml", lambda root: [
        setattr(t, "text", t.text + " SECTION 21 13 18 - OTHER") for t in root.iter(W + "t")
        if t.text and t.text.startswith("SECTION ")])
    twice = mutate(build_batch1(), "word/document.xml", lambda root: root.find(W + "body").insert(
        0, etree.fromstring(f'<w:p xmlns:w="{W[1:-1]}"><w:r><w:t>SECTION 21 13 18 - OTHER</w:t></w:r></w:p>')))
    with pytest.raises(JobException) as e:
        plan_and_check(twice, "make_values_agree", {"places": {"title": TITLE_LINE, "file": FILE_NAME},
                                                    "truth": "title"}, "211319 - x.docx")
    assert e.value.code == "ambiguous"
    with pytest.raises(JobException) as e:
        plan_and_check(build_batch1(), "make_values_agree",
                       {"places": {"title": TITLE_LINE, "header": {**HEADER, "required": True}}, "truth": "title"},
                       "x.docx")
    assert e.value.code == "not-found"
    for params in ({"places": {"title": TITLE_LINE}, "truth": "title"},
                   {"places": {"title": TITLE_LINE, "file": FILE_NAME}, "truth": "nope"},
                   {"places": {"title": {"in": "document", "pattern": "SECTION"}, "file": FILE_NAME}, "truth": "file"}):
        with pytest.raises(JobException) as e:
            plan_and_check(build_batch1(), "make_values_agree", params, "x.docx")
        assert e.value.code == "bad-params"
    assert two                                                        # (a second title inside one line is one match)


def test_shared_defect_agreement_by_exact_text(monkeypatch):
    monkeypatch.setattr(agree_module, "agree", lambda a, b: a == b)
    src = _header(build_batch1(), "21 13 19\tPreaction Sprinkler Systems")
    plan, out, rec = plan_and_check(src, "make_values_agree", {"places": {"title": TITLE_LINE, "header": HEADER},
                                                               "truth": "title", "tracked": False}, "x.docx")
    header = next(DocxPackage(out).xml("word/header1.xml").iter(W + "p"))
    assert accepted_text(header) == "21 13 19\tPREACTION SPRINKLER SYSTEMS"        # converted by plan and apply …
    assert failed(rec) == ["V10"]                                                 # … the verifier says it agreed


def test_defect_renamed_output_under_the_old_name():
    plan, out, rec = plan_and_check(build_batch1(), "make_values_agree",
                                    {"places": {"title": TITLE_LINE, "file": FILE_NAME}, "truth": "title"},
                                    "C:/specs/211313 - Wet-Pipe Sprinkler Systems.docx",
                                    written_as="211313 - Wet-Pipe Sprinkler Systems.docx")
    assert failed(rec) == ["V10"]
    assert any("written as" in e for e in v10_errors(rec))


# ---- what the naming ops READ (rule 9: read touches, 2026-10-16) and chains ---------------------------------------

def _plan(spec):
    from spec_surgeon.editspec import plan_spec
    return plan_spec(DocxPackage(build_batch1(), "211319 - Preaction Sprinkler Systems.docx"), spec)


def _spec(*ops, mode="content"):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


TITLE_EDIT = {"op": "replace_table", "params": {"pairs": [{"find": "PREACTION", "replace": "DELUGE"}],
                                                "scope": ["document"], "author": "A"}}


def test_a_name_read_from_a_line_another_op_rewrites_is_a_conflict():
    """name_output reads the title line; replace_table rewriting it in the same spec would leave the copy named after
    words it no longer shows — refused at plan time, naming the op to run first."""
    with pytest.raises(JobException) as e:
        _plan(_spec({"op": "name_output", "params": CONVENTION}, TITLE_EDIT))
    assert e.value.code == "conflict" and "split out" in e.value.reason
    # a font change of the same line reads nothing of its text: it plans
    plan, _ = _plan(_spec({"op": "name_output", "params": CONVENTION},
                          {"op": "set_font", "select": {"scope": ["document"],
                                                        "paragraphs": {"text_regex": "^SECTION"}},
                           "params": {"font": "Arial", "tracked": False}}, mode="formatting"))
    assert plan.output_name == "211319 - Preaction Sprinkler Systems.docx"


def test_end_of_section_reads_its_number_and_the_end_of_the_body():
    eos = {"op": "ensure_end_of_section", "params": {"number": NUMBER, "author": "A"}}
    with pytest.raises(JobException) as e:                 # the number's line rewritten underneath
        _plan(_spec(eos, {"op": "replace_table", "params": {"pairs": [{"find": "21 13 19", "replace": "21 13 13"}],
                                                             "scope": ["document"], "author": "A"}}))
    assert e.value.code == "conflict"
    after_end = {"op": "insert_paragraph", "select": {"scope": ["document"],
                                                      "paragraphs": {"text_regex": "^END OF SECTION"}},
                 "params": {"text": "NOTE", "author": "A"}}
    from spec_surgeon.editspec import plan_spec
    right = DocxPackage(build_batch1(eos="right"))
    with pytest.raises(JobException) as e:                 # a paragraph added after a line that was already right
        plan_spec(right, _spec(eos, after_end))
    assert e.value.code == "conflict"


def test_agreement_reads_every_place_it_matched_and_the_output_name():
    agree_params = {"places": {"title": TITLE_LINE, "header": HEADER}, "truth": "title", "author": "A"}
    with pytest.raises(JobException) as e:
        _plan(_spec({"op": "make_values_agree", "params": agree_params}, TITLE_EDIT))
    assert e.value.code == "conflict"
    with_file = {"places": {"title": TITLE_LINE, "file": FILE_NAME}, "truth": "title", "author": "A"}
    with pytest.raises(JobException) as e:                 # it reads the name another op gives the copy
        _plan(_spec({"op": "make_values_agree", "params": with_file}, {"op": "name_output", "params": CONVENTION}))
    assert e.value.code == "conflict"


def test_a_chain_carries_a_renaming_stage_to_its_outputs_and_refuses_in_place(tmp_path):
    from spec_surgeon.chain import run_chain
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.docx").write_bytes(build_batch1())
    chain = {"stages": [
        {"name": "name", "spec": _spec({"op": "name_output", "params": CONVENTION}, mode="formatting")},
        {"name": "stamp", "spec": _spec({"op": "set_issue_stamp", "params": {
            "pattern": r"ISSUED FOR PERMIT \S+", "text": "ISSUED FOR BID", "author": "A"}})}]}
    result = run_chain([str(folder)], chain, tmp_path / "out", workers=1, do_render=False)
    assert [cf.status for cf in result.files] == ["PASS"]
    assert sorted(p.name for p in (tmp_path / "out" / "outputs").iterdir()) == \
        ["211319 - Preaction Sprinkler Systems.docx"]
    with pytest.raises(ValueError) as e:
        run_chain([str(folder)], chain, tmp_path / "out2", workers=1, do_render=False, in_place=True)
    assert "copies only" in str(e.value) and not (tmp_path / "out2").exists()
