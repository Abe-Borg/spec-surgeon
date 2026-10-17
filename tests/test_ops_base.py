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


# ---- reads: an op's prediction depends on what another op would change (2026-10-16) ------------------------------

def test_a_whole_member_read_conflicts_with_any_change_inside_that_member_and_never_with_a_read():
    templating = _frag("apply_header_footer_template", [("word/styles.xml", "*", "verify")])
    for change in ([("word/styles.xml", "style:Normal", "rPr")], [("word/styles.xml", "docDefaults", "rPr")],
                   [("word/styles.xml", "style:Normal", "pPr")], [("word/styles.xml", "*", "remove")]):
        with pytest.raises(JobException) as e:
            merge_fragments([templating, _frag("set_style_font", change)])
        assert e.value.code == "conflict" and e.value.details["member"] == "word/styles.xml"
        assert "split out ['#2 set_style_font']" in e.value.reason or "replaces, removes or restructures" in e.value.reason
    # the other way round: the change first in the spec is the same conflict
    with pytest.raises(JobException) as e:
        merge_fragments([_frag("set_style_font", [("word/styles.xml", "style:Normal", "rPr")]), templating])
    assert e.value.code == "conflict" and e.value.details["changes"] == ["#1 set_style_font"]
    # two reads of one member never conflict: another whole-member read, a scope read, a family read
    for other in ([("word/styles.xml", "*", "verify")], [("word/styles.xml", "scope", "verify")],
                  [("word/styles.xml", "docDefaults", "verify:rPr")]):
        plan = merge_fragments([templating, _frag("set_font", other)])
        assert [o["op"] for o in plan.ops] == ["apply_header_footer_template", "set_font"]
    # the reader's own change inside the member it reads (an imported style) is no conflict with its own read
    own = _frag("apply_header_footer_template", [("word/styles.xml", "*", "verify"), ("word/styles.xml", "style:HFStamp", "add")])
    assert merge_fragments([own, _frag("set_font", [("word/document.xml", "3", "rPr")])]).mode == "formatting"
    # a change of another member is no conflict either
    assert merge_fragments([templating, _frag("set_font", [("word/document.xml", "3", "rPr")])]).mode == "formatting"
    # two readers of one member, one of which also changes something inside it: the other's read meets that change
    with pytest.raises(JobException) as e:
        merge_fragments([templating, own])
    assert e.value.code == "conflict" and e.value.details["reads"] == ["#1 apply_header_footer_template"]
    assert e.value.details["changes"] == ["#2 apply_header_footer_template"]
    with pytest.raises(JobException) as e:
        merge_fragments([own, templating])
    assert e.value.details["reads"] == ["#2 apply_header_footer_template"] and e.value.details["changes"] == ["#1 apply_header_footer_template"]


def test_a_family_read_of_a_target_conflicts_with_a_change_of_that_family_or_of_its_text_only():
    sect = "word/document.xml", "sectPr:0"
    templating = _frag("apply_header_footer_template", [(*sect, "verify:pgMar"), (*sect, "verify:pgSz"), (*sect, "hfref")])
    numbering = _frag("fix_page_numbering", [(*sect, "pgNumType")])
    assert [o["op"] for o in merge_fragments([templating, numbering]).ops] == ["apply_header_footer_template", "fix_page_numbering"]
    for aspect in ("pgMar", "pgSz", "text"):
        with pytest.raises(JobException) as e:
            merge_fragments([templating, _frag("set_page_setup", [(*sect, aspect)])])
        assert e.value.code == "conflict"
        assert e.value.details == {"member": sect[0], "target": sect[1], "ops": ["#1 apply_header_footer_template", "#2 set_page_setup"],
                                   "aspects": [f"verify:{aspect}" if aspect != "text" else "verify:pgMar", aspect],
                                   "reads": ["#1 apply_header_footer_template"], "changes": ["#2 set_page_setup"]}
        assert "split out #2 set_page_setup into a spec of its own and run it first, then #1 apply_header_footer_template on its outputs" in e.value.reason
    # a read of every family of the target conflicts with any change of it
    with pytest.raises(JobException) as e:
        merge_fragments([_frag("x", [(*sect, "verify")]), numbering])
    assert e.value.code == "conflict" and "plans from every property of word/document.xml [sectPr:0]" in e.value.reason
    # a bare "verify:" reads every family too (never less than it says)
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [(*sect, "verify:")]), numbering])
    # the same target read by two ops, and an op's own change of what it reads: no conflict
    both = _frag("fix_page_numbering", [(*sect, "verify:titlePg"), ("word/footer1.xml", "0", "text")])
    own = _frag("apply_header_footer_template", [(*sect, "verify:titlePg"), (*sect, "verify:pgMar"), (*sect, "titlePg"), (*sect, "pgMar")])
    assert merge_fragments([templating, both]).mode == "formatting"
    with pytest.raises(JobException) as e:
        merge_fragments([both, own])                 # the other op's change of titlePg meets the read
    assert e.value.code == "conflict" and e.value.details["aspects"] == ["verify:titlePg", "titlePg"]
    assert merge_fragments([own, _frag("set_page_setup", [("word/document.xml", "sectPr:1", "pgMar")])]).mode == "formatting"
    # a whole-member change of the member still conflicts with a family read inside it (rule 1, as before)
    with pytest.raises(JobException) as e:
        merge_fragments([templating, _frag("delete_paragraphs", [("word/document.xml", "*", "text")])])
    assert e.value.code == "conflict" and "would verify word/document.xml without changing it" not in e.value.reason


def test_a_soft_read_is_reached_by_a_change_of_its_family_and_never_by_a_text_change():
    """Built-in batch 4: set_properties' reads of the runs it finds already as asked are soft (``verify-props``) — a
    replace_text edit in the same paragraph plans, as before batch 4 (V2 judges the words, the kind re-resolves its runs
    over the output), while clear_direct_formatting's change of those run properties is a conflict."""
    sp = _frag("set_properties", [("word/document.xml", "3", "verify-props:rPr"),
                                  ("word/document.xml", "3", "verify-props:pStyle")])
    assert merge_fragments([sp, _frag("replace_text", [("word/document.xml", "3", "text")])]).mode == "formatting"
    for aspect in ("rPr", "pStyle"):
        with pytest.raises(JobException) as e:
            merge_fragments([_frag("clear_direct_formatting", [("word/document.xml", "3", aspect)]), sp])
        assert e.value.code == "conflict" and e.value.details["reads"] == ["#2 set_properties"]
    # a hard family read is still reached by a text change, and a bare soft read by every family but text
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [("word/document.xml", "3", "verify:rPr")]),
                         _frag("replace_text", [("word/document.xml", "3", "text")])])
    bare = _frag("x", [("word/document.xml", "3", "verify-props:")])
    assert merge_fragments([bare, _frag("replace_text", [("word/document.xml", "3", "text")])]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([bare, _frag("set_font", [("word/document.xml", "3", "rPr")])])


def test_a_whole_member_family_read_is_reached_by_that_family_anywhere_in_the_member_only():
    """A selection by style_in/has_bold/is_all_caps reads every paragraph's pStyle: a restyle of any paragraph of the
    part conflicts, a run-property or text change does not; a whole-member change still does (rule 1)."""
    sel = _frag("set_font", [("word/document.xml", "*", "verify-props:pStyle"), ("word/document.xml", "scope", "verify")])
    with pytest.raises(JobException) as e:
        merge_fragments([sel, _frag("apply_style", [("word/document.xml", "17", "pStyle")])])
    assert e.value.code == "conflict" and "the pStyle of every target in it" in e.value.reason
    assert e.value.details["reads"] == ["#1 set_font"] and e.value.details["changes"] == ["#2 apply_style"]
    for other in ([("word/document.xml", "17", "rPr")], [("word/document.xml", "17", "text")],
                  [("word/footer1.xml", "2", "pStyle")], [("word/document.xml", "17", "verify:pStyle")]):
        assert merge_fragments([sel, _frag("other", other)]).mode == "formatting", other
    with pytest.raises(JobException):
        merge_fragments([sel, _frag("delete_paragraphs", [("word/document.xml", "*", "text")])])
    # a hard whole-member family read is reached by text changes inside the member too
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [("word/document.xml", "*", "verify:pStyle")]),
                         _frag("replace_text", [("word/document.xml", "4", "text")])])


def test_incremental_uses_commute_in_one_direction_and_collide_in_opposite_ones():
    """``uses+``/``uses-`` of a style: two ops adding uses of one style commute; mapping a style away (uses-) beside an
    op that applies it (uses+) is order-dependent; a selection by the style (verify:uses) meets both; remove_unused's
    judgement (verify:uses-) only a removal; a bare read of the style's definition neither."""
    st = "word/styles.xml", "style:Heading2"
    add1, add2 = _frag("apply_style", [(*st, "uses+")]), _frag("apply_style", [(*st, "uses+")])
    away = _frag("map_styles", [(*st, "uses-")])
    away2 = _frag("apply_style", [(*st, "uses-")])
    assert merge_fragments([add1, add2]).mode == "formatting"
    assert merge_fragments([away, away2]).mode == "formatting"
    for a, b in ((add1, away), (away, add1)):
        with pytest.raises(JobException) as e:
            merge_fragments([a, b])
        assert e.value.code == "conflict" and sorted(e.value.details["aspects"]) == ["uses+", "uses-"]
    selection = _frag("set_font", [(*st, "verify:uses")])
    for change in (add1, away):
        with pytest.raises(JobException):
            merge_fragments([selection, change])
    judgement = _frag("remove_unused_styles", [("word/styles.xml", "*", "verify:uses-")])
    assert merge_fragments([judgement, add1]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([judgement, away])
    definition = _frag("insert_paragraph", [(*st, "verify")])
    for change in (add1, away2):
        assert merge_fragments([definition, change]).mode == "formatting"
    with pytest.raises(JobException):
        merge_fragments([definition, _frag("set_style_font", [(*st, "rPr")])])
    # a plain change of the family collides with an incremental one, and an incremental aspect is never a read
    with pytest.raises(JobException):
        merge_fragments([_frag("x", [(*st, "uses")]), add1])
    from spec_surgeon.ops.base import incremental
    assert incremental("uses+") == ("uses", "+") and incremental("verify:uses-") is None and incremental("rPr") is None
