"""ops/base.py — fragment merge and conflict detection (no documents needed)."""
from __future__ import annotations

import pytest

from spec_surgeon.ops import JobException, PlanFragment, BlockDiff, merge_fragments


def _frag(op, touches=(), mode="formatting", **kw):
    f = PlanFragment(job=op, mode=mode, params={}, intended_parts=list(kw.pop("parts", [])), op=op)
    f.touches = list(touches)
    for k, v in kw.items():
        setattr(f, k, v)
    return f


def test_disjoint_targets_merge_in_order():
    a = _frag("set_style_font", [("word/styles.xml", "style:Heading1", "rPr")], parts=["word/styles.xml"],
              property_assertions=[{"kind": "style_fonts"}])
    b = _frag("set_doc_defaults_font", [("word/styles.xml", "docDefaults", "rPr")], parts=["word/styles.xml"],
              property_assertions=[{"kind": "doc_defaults_fonts"}])
    c = _frag("set_paragraph_props", [("word/document.xml", "3", "pPr")], parts=["word/document.xml"])
    d = _frag("set_font", [("word/document.xml", "3", "rPr"), ("word/document.xml", "3", "markRPr")],
              parts=["word/document.xml"])
    plan = merge_fragments([a, b, c, d])
    assert plan.intended_parts == ["word/styles.xml", "word/document.xml"]
    assert [x["op_index"] for x in plan.property_assertions] == [0, 1]
    assert [o["op"] for o in plan.ops] == ["set_style_font", "set_doc_defaults_font", "set_paragraph_props", "set_font"]
    assert plan.mode == "formatting"


def test_same_aspect_on_same_target_conflicts():
    a = _frag("set_font", [("word/document.xml", "3", "rPr")])
    b = _frag("set_font", [("word/document.xml", "3", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


def test_text_edit_conflicts_with_any_aspect_on_that_block():
    a = _frag("replace_text", [("word/document.xml", "7", "text")], mode="content",
              diffs=[BlockDiff("word/document.xml", 7, "a", "ab")])
    b = _frag("set_font", [("word/document.xml", "7", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"
    # a different block is fine
    b2 = _frag("set_font", [("word/document.xml", "8", "rPr")])
    assert merge_fragments([a, b2]).mode == "content"


def test_whole_member_touch_conflicts_with_anything_in_that_member():
    a = _frag("remove_header_footer", [("word/header1.xml", "*", "remove")], members_removed=["word/header1.xml"])
    b = _frag("set_font", [("word/header1.xml", "0", "rPr")])
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"


def test_mode_rules():
    tracked = _frag("replace_text", [("word/document.xml", "1", "text")], mode="content",
                    diffs=[BlockDiff("word/document.xml", 1, "x", "xy")])
    untracked = _frag("replace_text", [("word/document.xml", "2", "text")], mode="untracked",
                      diffs=[BlockDiff("word/document.xml", 2, "x", "y")], untracked_diffs_expected=True)
    with pytest.raises(JobException):
        merge_fragments([tracked, untracked])
    # untracked text diffs in a SURVIVING part cannot ride along with tracked content ops (V5b would fail) …
    cover = _frag("fix_page_numbering", [("word/footer1.xml", "*", "structure")],
                  diffs=[BlockDiff("word/footer1.xml", 0, "x", None)], untracked_diffs_expected=True)
    with pytest.raises(JobException):
        merge_fragments([tracked, cover])
    # … but deletions inside REMOVED parts can (they are accounted for by V1/V2, not by the reject view)
    removal = _frag("remove_header_footer", [("word/header1.xml", "*", "remove")],
                    diffs=[BlockDiff("word/header1.xml", 0, "x", None)], members_removed=["word/header1.xml"],
                    untracked_diffs_expected=True)
    assert merge_fragments([tracked, removal]).mode == "content"


def test_revision_ids_are_disjoint_and_geometry_none_dominates():
    a = _frag("replace_text", [("word/document.xml", "1", "text")], mode="content", rev_ids_needed=3, rev_id_base=200)
    b = _frag("set_paragraph_props", [("word/document.xml", "2", "pPr")], rev_ids_needed=2, rev_id_base=200,
              geometry_reference="none", geometry_reason="spacing changes reflow the page")
    plan = merge_fragments([a, b])
    assert a.transform_data["first_rev_id"] == 200 and b.transform_data["first_rev_id"] == 203
    assert plan.geometry_reference == "none" and "reflow" in plan.geometry_reason


# ---- the v0.1.0 jobs are thin aliases over ops ---------------------------------------------------------

def test_jobs_are_aliases_of_ops(tmp_path):
    from make_fixtures import build
    from spec_surgeon.jobs import get_job
    from spec_surgeon.ops import get_op
    from spec_surgeon.package import DocxPackage
    pkg = DocxPackage(build("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"))
    params = {"find": "FM Global", "replace": "GRC", "scope": ["document", "headers"], "author": "Abraham Borg"}
    via_job = get_job("tracked-replace").plan(pkg, params)
    via_op = get_op("replace_text").plan(pkg, {"scope": ["document", "headers"]},
                                         {"find": "FM Global", "replace": "GRC", "tracked": True, "author": "Abraham Borg"})
    assert [d.to_dict() for d in via_job.diffs] == [d.to_dict() for d in via_op.diffs]
    assert via_job.rev_delta == via_op.rev_delta and via_job.rev_ids_needed == 9
    assert via_job.job == "tracked-replace" and via_op.job == "replace_text"
    with pytest.raises(JobException):
        get_op("replace_text").plan(pkg, {}, {"find": "FM Global", "replace": "GRC", "tracked": False, "mode": "tracked"})
