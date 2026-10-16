"""remove_comments (built-in batch 2, docs/BUILTIN_OPS_BATCH2_DESIGN.md §2): all, resolved threads or named authors' comments
go with their replies and their marks; the parts stay; the document's text is unchanged — and the verifier
(verify/comments.py) catches a wrong output and a wrong selection shared by plan and apply."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from fx_comments import CEX, CID, COMMENTS, DOC, W15, build_commented, build_plain
from make_fixtures import W, patch_docx
from spec_surgeon.editspec import SpecError, apply_spec, load_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException
from spec_surgeon.ops import comments as cop
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify.comments import _VComments
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.suite import run_suite

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "specs"


def spec(*ops, mode="formatting"):
    return {"request": "remove the comments", "summary": "s", "mode": mode, "ops": list(ops)}


def remove(**params):
    return {"op": "remove_comments", "params": params}


def run(data: bytes, sp):
    pkg = DocxPackage(data, "s.docx")
    plan, frags = plan_spec(pkg, sp)
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "s.docx", "o.docx")


def failed(rec):
    return {c.id: c.summary for c in rec.checks if c.status == "FAIL"}


def member(data: bytes, name: str):
    return etree.fromstring(zipfile.ZipFile(io.BytesIO(data)).read(name))


def comment_ids(data: bytes):
    return [c.get(W + "id") for c in member(data, COMMENTS).findall(W + "comment")]


def mark_ids(data: bytes, part=DOC):
    root = member(data, part)
    return sorted({m.get(W + "id") for m in root.iter(W + "commentRangeStart", W + "commentRangeEnd",
                                                       W + "commentReference")})


def text_of(data: bytes, part=DOC):
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in member(data, part).iter(W + "p")]


# ---- happy paths ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("params, gone", [
    ({"which": "all"}, ["0", "1", "2", "3", "4", "5", "6"]),
    ({"which": "resolved"}, ["0", "1"]),
    ({"which": "authors", "authors": ["jack"]}, ["1", "2", "3"]),            # Jack's reply 1 alone; his 2 with reply 3
    ({"which": "authors", "authors": ["  JEREMY "]}, ["0", "1", "3", "4", "6"]),   # Jeremy's 0 takes Jack's reply 1
])
def test_comments_go_with_their_replies_and_marks_and_every_check_passes(params, gone):
    data = build_commented()
    plan, out, rec = run(data, spec(remove(**params)))
    assert rec.passed, failed(rec)
    kept = [i for i in comment_ids(data) if i not in gone]
    assert comment_ids(out) == kept
    assert mark_ids(out) == sorted(kept)
    assert text_of(out) == text_of(data)                                # the document's text is unchanged
    assert plan.property_assertions[0]["expected_ids"] == gone
    for name, tag, attr in (("word/commentsExtended.xml", "{%s}commentEx" % W15, "{%s}paraId" % W15),
                            ("word/commentsIds.xml", "{%s}commentId" % CID, "{%s}paraId" % CID)):
        assert len(member(out, name).findall(tag)) == len(kept)
    assert len(member(out, "word/commentsExtensible.xml").findall("{%s}commentExtensible" % CEX)) == len(kept)
    # parts are never removed, relationships never change
    assert zipfile.ZipFile(io.BytesIO(out)).namelist() == zipfile.ZipFile(io.BytesIO(data)).namelist()


def test_removing_every_comment_leaves_an_empty_comments_part():
    data = build_commented()
    plan, out, rec = run(data, spec(remove()))                             # which defaults to all
    assert rec.passed, failed(rec)
    assert member(out, COMMENTS).findall(W + "comment") == []
    assert rec.get("V5a").status == "PASS"                                 # an empty w:comments is schema-valid
    assert plan.mode == "formatting" and plan.untracked_diffs_expected


def test_a_comment_spanning_two_paragraphs_loses_both_ends():
    data = build_commented()
    plan, out, rec = run(data, spec(remove(which="authors", authors=["Jeremy"])))
    assert rec.passed, failed(rec)
    anchors = plan.property_assertions[0]["expected_anchor_blocks"][DOC]
    texts = text_of(data)
    starts = {texts[i][:2] for i in anchors}
    assert {"A.", "B.", "C.", "D."} <= starts                              # comment 4 spans C and D
    assert "4" not in mark_ids(out)


def test_a_mark_between_paragraphs_is_an_expected_removal():
    data = build_commented()
    body = member(data, DOC).find(W + "body")
    assert [e.get(W + "id") for e in body if e.tag == W + "commentRangeStart"] == ["6"]
    plan, out, rec = run(data, spec(remove(which="authors", authors=["Jeremy"])))
    assert rec.passed, failed(rec)
    assert plan.property_assertions[0]["expected_outside_marks"] == {DOC: ["6"]}
    assert [e for e in member(out, DOC).find(W + "body") if e.tag == W + "commentRangeStart"] == []
    _, frags = plan_spec(DocxPackage(data, "s.docx"), spec(remove(which="authors", authors=["Jeremy"])))
    assert (DOC, "comment-mark:6", "remove") in frags[0].touches             # rule 9: the mark between blocks


def test_a_reference_mark_is_a_structure_only_declaration():
    data = build_commented()
    plan, out, rec = run(data, spec(remove(which="resolved")))
    shapes = [(d.part, d.before == d.after, d.struct_after) for d in plan.diffs if d.part == DOC]
    assert shapes and all(same and sa is not None and "commentReference" not in sa for _, same, sa in shapes)
    assert rec.get("V3").status == "PASS" and rec.get("V2").status == "PASS"


def test_revisions_inside_a_removed_comment_are_predicted_and_flagged():
    data = build_commented()
    plan, out, rec = run(data, spec(remove(which="authors", authors=["Abe"])))
    assert rec.passed, failed(rec)
    assert plan.rev_delta.get("ins") == -1
    assert any("held pending tracked changes" in f for f in plan.flags)


def test_a_file_from_old_word_without_side_parts():
    data = build_commented(side=False)
    plan, out, rec = run(data, spec(remove()))
    assert rec.passed, failed(rec)
    assert comment_ids(out) == []
    # no resolved state is kept in such a file: nothing to do by default, set aside when asked to insist
    plan, _ = plan_spec(DocxPackage(data, "s.docx"), spec(remove(which="resolved")))
    assert plan.intended_parts == [] and any("keeps no resolved state" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(data, "s.docx"), spec(remove(which="resolved", allow_missing=False)))
    assert e.value.code == "not-found"


def test_a_file_without_comments_is_a_no_op_unless_asked_to_insist():
    data = build_plain()
    plan, frags = plan_spec(DocxPackage(data, "s.docx"), spec(remove()))
    assert plan.intended_parts == [] and any("nothing to do" in n for n in plan.notes)
    out = apply_spec(DocxPackage(data, "s.docx"), frags).output_bytes()
    assert out == data
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(data, "s.docx"), spec(remove(allow_missing=False)))
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_commented(), "s.docx"), spec(remove(which="authors", authors=["Nobody"],
                                                                        allow_missing=False)))
    assert e.value.code == "not-found" and "Nobody" in e.value.reason


# ---- refusals -------------------------------------------------------------------------------------------------------

def test_a_mark_in_a_text_box_is_refused():
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_commented(textbox=True), "s.docx"), spec(remove()))
    assert e.value.code == "textbox"
    # a comment whose marks are all outside text boxes still goes
    plan, out, rec = run(build_commented(textbox=True), spec(remove(which="resolved")))
    assert rec.passed, failed(rec)


def test_two_comments_with_one_id_are_refused():
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_commented(duplicate=True), "s.docx"), spec(remove()))
    assert e.value.code == "structure"


@pytest.mark.parametrize("params, code", [
    ({"which": "authors"}, "bad-params"),
    ({"authors": ["Jack"]}, "bad-params"),
    ({"which": "authors", "authors": [""]}, "bad-params"),
])
def test_bad_parameters_are_refused(params, code):
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_commented(), "s.docx"), spec(remove(**params)))
    assert e.value.code == code


def test_an_unknown_which_is_a_spec_error():
    with pytest.raises(SpecError):
        validate_spec(spec(remove(which="some")))


# ---- wrong outputs ----------------------------------------------------------------------------------------------------

def _good(params=None):
    data = build_commented()
    plan, out, rec = run(data, spec(remove(**(params or {"which": "resolved"}))))
    assert rec.passed
    return data, plan, out


def test_a_reference_mark_left_behind_fails():
    data, plan, out = _good()

    def put_back(root):
        p = next(q for q in root.iter(W + "p") if "".join(t.text or "" for t in q.iter(W + "t")).startswith("A."))
        r = etree.SubElement(p, W + "r")
        etree.SubElement(r, W + "commentReference").set(W + "id", "0")
    rec = run_suite(data, patch_docx(out, {DOC: put_back}), plan.to_dict(), "s.docx", "o.docx")
    assert {"V3", "V10"} <= set(failed(rec))
    assert "declared structure change not found" in str(rec.get("V3").data)


def test_a_kept_comments_side_entry_removed_fails():
    data, plan, out = _good()

    def drop_one(root):
        root.remove(root.findall("{%s}commentEx" % W15)[-1])
    rec = run_suite(data, patch_docx(out, {"word/commentsExtended.xml": drop_one}), plan.to_dict(), "s.docx", "o.docx")
    assert "V10" in failed(rec) and "commentsExtended.xml" in failed(rec)["V10"]


def test_a_mark_between_paragraphs_left_behind_fails():
    data, plan, out = _good({"which": "all"})

    def put_back(root):
        body = root.find(W + "body")
        el = etree.Element(W + "commentRangeStart"); el.set(W + "id", "6")
        body.insert(3, el)
    rec = run_suite(data, patch_docx(out, {DOC: put_back}), plan.to_dict(), "s.docx", "o.docx")
    assert "V10" in failed(rec)


def test_a_comment_left_in_the_comments_part_fails():
    data, plan, out = _good({"which": "all"})
    src_comment = member(data, COMMENTS).findall(W + "comment")[0]

    def put_back(root):
        root.append(src_comment)
    rec = run_suite(data, patch_docx(out, {COMMENTS: put_back}), plan.to_dict(), "s.docx", "o.docx")
    assert {"V2", "V10"} <= set(failed(rec))


def test_a_kept_comments_mark_removed_fails():
    data, plan, out = _good()                                              # resolved: comments 2–6 stay

    def drop(root):
        for m in list(root.iter(W + "commentRangeEnd")):
            if m.get(W + "id") == "5":
                m.getparent().remove(m)
    rec = run_suite(data, patch_docx(out, {DOC: drop}), plan.to_dict(), "s.docx", "o.docx")
    assert "V10" in failed(rec)


# ---- wrong implementations shared by plan and apply ---------------------------------------------------------------------

def test_a_thread_reading_that_forgets_replies_is_caught(monkeypatch):
    monkeypatch.setattr(cop.CommentBook, "parent", lambda self, c: None)
    plan, out, rec = run(build_commented(), spec(remove(which="authors", authors=["Jack"])))
    assert plan.property_assertions[0]["expected_ids"] == ["1", "2"]     # reply 3 forgotten by plan and apply alike
    assert "V10" in failed(rec) and "selected here" in failed(rec)["V10"]


def test_a_default_that_reads_all_as_resolved_is_caught(monkeypatch):
    real = cop.CommentBook.select
    monkeypatch.setattr(cop.CommentBook, "select",
                        lambda self, which, authors: real(self, "resolved" if which == "all" else which, authors))
    plan, out, rec = run(build_commented(), spec(remove()))
    assert plan.property_assertions[0]["asked_which"] is None             # the user's words: the default
    assert "V10" in failed(rec)                                           # the verifier's own default is all


def test_a_selection_that_finds_nothing_by_mistake_is_caught_even_as_a_no_op(monkeypatch):
    monkeypatch.setattr(cop.CommentBook, "select", lambda self, which, authors: [])
    plan, out, rec = run(build_commented(), spec(remove(which="authors", authors=["Jack"])))
    assert plan.intended_parts == [] and comment_ids(out) == [str(i) for i in range(7)]   # plan and apply: nothing …
    assert "V10" in failed(rec) and "selected here" in failed(rec)["V10"]   # … the verifier finds Jack's comments


def test_a_resolved_reading_that_misreads_done_is_caught(monkeypatch):
    monkeypatch.setattr(cop.CommentBook, "resolved", lambda self, c: True)
    plan, out, rec = run(build_commented(), spec(remove(which="resolved")))
    assert "V10" in failed(rec)


def test_a_mark_finder_that_misses_range_ends_is_caught(monkeypatch):
    real = cop.marks_of
    monkeypatch.setattr(cop, "marks_of", lambda root, ids: [m for m in real(root, ids) if m.tag != W + "commentRangeEnd"])
    plan, out, rec = run(build_commented(), spec(remove(which="resolved")))
    assert "V10" in failed(rec)


def test_side_entries_matched_by_the_wrong_paragraph_are_caught(monkeypatch):
    real = cop.side_removals

    def first_paragraph_only(pkg, book, ids):
        out = real(pkg, book, ids)
        return {k: (root, ents[:1]) for k, (root, ents) in out.items()}
    monkeypatch.setattr(cop, "side_removals", first_paragraph_only)
    plan, out, rec = run(build_commented(), spec(remove(which="resolved")))
    assert "V10" in failed(rec)


# ---- the two readings agree ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("build", [build_commented, lambda: build_commented(side=False)])
@pytest.mark.parametrize("which, authors", [("all", []), ("resolved", []), ("authors", ["Jack"]), ("authors", ["jeremy"]),
                                            ("authors", ["Abe", "Nobody"])])
def test_the_transform_and_verifier_select_the_same_comments(build, which, authors):
    data = build()
    t = cop.CommentBook(DocxPackage(data, "s.docx")).select(which, authors)
    v = _VComments(VPackage(data)).chosen(which, authors)
    assert t == v


# ---- rule 9 and the rest of the app -----------------------------------------------------------------------------------

def test_a_text_edit_of_a_paragraph_with_a_comment_mark_is_a_plan_time_conflict():
    sp = spec(remove(),
              {"op": "replace_table", "tracked": False, "params": {"pairs": [{"find": "Sprinklers", "replace": "Heads"}],
                                                                   "author": "A"}}, mode="untracked")
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_commented(), "s.docx"), sp)
    assert e.value.code == "conflict"


def test_the_worked_example_validates_and_plans():
    sp = load_spec(EXAMPLES / "remove-resolved-comments.json")
    validate_spec(sp)
    plan, out, rec = run(build_commented(), sp)
    assert rec.passed and comment_ids(out) == ["2", "3", "4", "5", "6"]
