"""resolve_tracked_changes (docs/BUILTIN_OPS_BATCH2_DESIGN.md §4): accept or reject tracked changes — all, by author, by
date — end to end through the whole suite; pandoc as the independent oracle; the cases left to Word (each refusal); the
whole-batch default (a file with nothing chosen is left as it is); and planted defects — a wrong output and wrong
decisions shared by plan and apply — that the verifier's own resolution catches."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest
from lxml import etree

from fx_revisions import JACK, JEREMY, awkward, redline
from spec_surgeon.editspec import apply_spec, load_spec, plan_spec
from spec_surgeon.ops import JobException
from spec_surgeon.ops import revisions as rev_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.planner import summarize_plan
from spec_surgeon.verify import run_suite
from test_text_edits import failed, mutate, v10_errors

DOC = "word/document.xml"
REV_TAGS = ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange", "sectPrChange")
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "specs" / "accept-changes-by-author.json"


def spec_of(params: Dict[str, Any], select: Dict[str, Any] = None, mode: str = "untracked") -> Dict[str, Any]:
    op: Dict[str, Any] = {"op": "resolve_tracked_changes", "params": params}
    if select:
        op["select"] = select
    return {"request": "accept or reject tracked changes", "summary": "s", "mode": mode, "ops": [op]}


def run(src: bytes, params: Dict[str, Any], select: Dict[str, Any] = None):
    pkg = DocxPackage(src)
    plan, frags = plan_spec(pkg, spec_of(params, select))
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def live(p) -> str:
    """The paragraph as a reader sees it with nothing pending: w:t outside deletions (deleted text as [-…-])."""
    out = []
    for t in p.iter(W + "t", W + "delText"):
        out.append(t.text or "" if t.tag == W + "t" else f"[-{t.text}-]")
    return "".join(out)


def texts(data: bytes, part: str = DOC) -> List[str]:
    return [live(p) for p in DocxPackage(data).xml(part).iter(W + "p") if live(p)]


def revisions_left(data: bytes, part: str = DOC) -> List[tuple]:
    root = DocxPackage(data).xml(part)
    return [(etree.QName(e).localname, e.get(W + "author")) for e in root.iter(*(W + t for t in REV_TAGS))]


@pytest.fixture(scope="module")
def doc() -> bytes:
    return redline()


# ---- end to end ----------------------------------------------------------------------------------------------------

def test_accept_everything(doc):
    plan, out, rec = run(doc, {"action": "accept"})
    assert rec.passed, rec.to_markdown()
    assert rec.get("V8").status == "NA" and rec.get("V10").status == "PASS"
    assert texts(out) == ["A. Pipe shall be hot-dip galvanized Schedule 40.", "B. Jeremy's insertion with Jack's deletion",
                          "C. Inserted paragraph.", "E. Formatting: bold now", "F. Text before. Text after.",
                          "G. Other text. Moved sentence. End of G.", "H. See  for details.", "Z. Final paragraph."]
    for part in (DOC, "word/header1.xml", "word/comments.xml"):
        assert revisions_left(out, part) == []
    assert texts(out, "word/header1.xml") == ["ACME SPEC REV B"]
    assert texts(out, "word/comments.xml") == ["Check "]
    root = DocxPackage(out).xml(DOC)
    assert not list(root.iter(W + "moveFromRangeStart", W + "moveToRangeEnd"))       # the move's ranges went with it
    assert [b.get(W + "name") for b in root.iter(W + "bookmarkStart")] == []          # _GoBack went with D
    e = next(p for p in root.iter(W + "p") if live(p).startswith("E."))
    assert e.find(f"{W}pPr/{W}jc").get(W + "val") == "center" and e.find(f".//{W}b") is not None
    assert plan.mode == "untracked" and plan.geometry_reference == "none"
    assert any(f.startswith("ACCEPTS 15 tracked change(s) — ") and "deletion 5 (Jack 5)" in f for f in plan.flags)
    assert any("_GoBack" in f for f in plan.flags)
    assert any(line.startswith("revisions: ") and "del -" in line for line in summarize_plan(plan))


def test_reject_everything(doc):
    plan, out, rec = run(doc, {"action": "reject"})
    assert rec.passed, rec.to_markdown()
    assert texts(out) == ["A. Pipe shall be black steel Schedule 40.", "B. Jeremy's insertion ", "D. Deleted paragraph.",
                          "E. Formatting: bold now", "F. Text before. Moved sentence. Text after.",
                          "G. Other text. End of G.", "H. See 21 10 00 for details.", "Z. Final paragraph."]
    for part in (DOC, "word/header1.xml", "word/comments.xml"):
        assert revisions_left(out, part) == []
    root = DocxPackage(out).xml(DOC)
    assert not list(root.iter(W + "delText", W + "delInstrText"))                     # all of it live text again
    assert [it.text for it in root.iter(W + "instrText")] == [" REF _Ref1 \\h "]       # the field's code is back
    e = next(p for p in root.iter(W + "p") if live(p).startswith("E."))
    assert e.find(f"{W}pPr/{W}jc").get(W + "val") == "left" and e.find(f".//{W}b") is None
    assert texts(out, "word/comments.xml") == ["Check the finish"]
    assert any("bring their words back" in f for f in plan.flags)


def test_accept_one_author_leaves_the_other_pending(doc):
    plan, out, rec = run(doc, {"action": "accept", "authors": ["  JEREMY "]})
    assert rec.passed, rec.to_markdown()
    left = revisions_left(out)
    assert left and {a for _, a in left} == {"Jack"}
    assert len(left) == len([r for r in revisions_left(doc) if r[1] == "Jack"])
    b = next(p for p in DocxPackage(out).xml(DOC).iter(W + "p") if live(p).startswith("B."))
    assert live(b) == "B. Jeremy's insertion with Jack's [-nested -]deletion"           # his insertion is plain text now
    assert b.find(W + "ins") is None and b.find(W + "del") is not None


def test_reject_one_author_inside_anothers_insertion(doc):
    plan, out, rec = run(doc, {"action": "reject", "authors": ["Jack"]})
    assert rec.passed, rec.to_markdown()
    b = next(p for p in DocxPackage(out).xml(DOC).iter(W + "p") if live(p).startswith("B."))
    ins = b.find(W + "ins")
    assert ins is not None and ins.get(W + "author") == "Jeremy"                         # Jeremy's still pending
    assert "".join(t.text for t in ins.iter(W + "t")) == "with Jack's nested deletion"     # Jack's words live inside it
    assert {a for _, a in revisions_left(out)} == {"Jeremy"}


@pytest.mark.parametrize("params,who", [({"on_or_after": "2026-09-23"}, "Jack"), ({"before": "2026-09-23"}, "Jeremy"),
                                        ({"on_or_after": "2026-09-22T10:15", "before": "2026-09-22T10:16"}, "Jeremy"),
                                        ({"on_or_after": "2026-09-23T08:00:01"}, None)])
def test_by_date(doc, params, who):
    plan, out, rec = run(doc, {"action": "accept", **params})
    assert rec.passed, rec.to_markdown()
    gone = {a for _, a in revisions_left(doc)} - {a for _, a in revisions_left(out)}
    assert gone == ({who} if who else set())


def test_a_change_without_a_date_is_left_and_flagged_under_a_date_filter():
    plan, out, rec = run(awkward("undated"), {"action": "accept", "on_or_after": "2026-01-01"})
    assert rec.passed
    assert any("no readable date" in f for f in plan.flags)
    left = DocxPackage(out).xml(DOC).findall(f".//{W}ins")
    assert len(left) == 1 and left[0].get(W + "date") is None
    plan, out, rec = run(awkward("undated"), {"action": "accept"})                       # no filter: both go
    assert rec.passed and DocxPackage(out).xml(DOC).find(f".//{W}ins") is None


def test_scope_headers_only(doc):
    plan, out, rec = run(doc, {"action": "accept"}, {"scope": ["headers"]})
    assert rec.passed, rec.to_markdown()
    assert revisions_left(out, "word/header1.xml") == []
    assert revisions_left(out) == revisions_left(doc)                                    # the body is untouched
    assert plan.intended_parts == ["word/header1.xml"]


@pytest.mark.parametrize("kind,action", [("chain", "accept"), ("mark-rprchange", "reject"), ("sectchange", "accept"),
                                         ("partial-move", "accept"), ("join", "reject"), ("last", "reject"),
                                         ("mark-rprchange", "accept")])
def test_boundary_cases_that_resolve(kind, action):
    plan, out, rec = run(awkward(kind), {"action": action})
    assert rec.passed, rec.to_markdown()
    assert revisions_left(out) == []


def test_a_change_of_structure_alone_is_declared_and_checked():
    """Accepting Jack's deleted tab changes no block text, only the block's tabs: a structure-only declaration (V3)."""
    plan, out, rec = run(awkward("tab-only"), {"action": "accept"})
    assert rec.passed, rec.to_markdown()
    only = [d for d in plan.diffs if d.before == d.after]
    assert len(only) == 1 and only[0].struct_after == {} and only[0].before == "Before the tabafter it."
    assert rec.get("V3").status == "PASS" and rec.get("V2").status == "PASS"
    plan, out, rec = run(awkward("tab-only"), {"action": "reject"})                       # the tab stays: no diff
    assert rec.passed and plan.diffs == []


def test_move_pieces_without_ranges_are_plain_revisions():
    plan, out, rec = run(awkward("loose-move"), {"action": "accept", "authors": ["Jeremy"]})
    assert rec.passed, rec.to_markdown()
    p = next(p for p in DocxPackage(out).xml(DOC).iter(W + "p") if live(p).startswith("Loose"))
    assert live(p) == "Loose: stays moved here" and p.find(W + "moveTo") is not None   # Jack's piece still pending


def test_a_mark_keeps_its_own_marker_when_only_the_formatting_change_is_rejected():
    plan, out, rec = run(awkward("mark-rprchange"), {"action": "reject", "authors": ["Jack"]})
    assert rec.passed, rec.to_markdown()
    p = next(p for p in DocxPackage(out).xml(DOC).iter(W + "p") if live(p).startswith("Inserted"))
    rpr = p.find(f"{W}pPr/{W}rPr")
    assert [etree.QName(c).localname for c in rpr] == ["ins"]                             # bold gone, Jeremy's marker kept


# ---- the independent oracle ------------------------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc not installed")
@pytest.mark.parametrize("action", ["accept", "reject"])
@pytest.mark.parametrize("moves", [False, True])
def test_pandoc_agrees(tmp_path, action, moves):
    """pandoc's own accept/reject of the source (the skill's V8 oracle) reads exactly like the op's output."""
    src = redline(moves=moves, extras=False)
    plan, out, rec = run(src, {"action": action})
    assert rec.passed
    (tmp_path / "src.docx").write_bytes(src)
    (tmp_path / "out.docx").write_bytes(out)

    def plain(path, mode=None):
        cmd = ["pandoc", str(path), "-t", "plain", "--wrap=none"] + ([f"--track-changes={mode}"] if mode else [])
        return " ".join(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.split())
    assert plain(tmp_path / "src.docx", action) == plain(tmp_path / "out.docx")


# ---- a whole batch: nothing chosen is nothing to do -------------------------------------------------------------------

def test_a_file_with_nothing_chosen_is_left_as_it_is_by_default():
    for src, params in ((awkward("clean"), {"action": "accept"}),
                        (redline(), {"action": "reject", "authors": ["Nobody"]}),
                        (redline(), {"action": "accept", "before": "2020-01-01"})):
        plan, out, rec = run(src, params)
        assert rec.passed and out == src
        assert any("nothing to do" in n for n in plan.notes)
        assert plan.intended_parts == []
    with pytest.raises(JobException) as e:
        run(awkward("clean"), {"action": "accept", "allow_missing": False})
    assert e.value.code == "not-found"
    with pytest.raises(JobException) as e:
        run(redline(), {"action": "accept", "authors": ["Nobody"], "allow_missing": False})
    assert e.value.code == "not-found"


# ---- what is left to Word --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind,params,code", [
    ("join", {"action": "accept"}, "paragraph-merge"),
    ("last", {"action": "accept"}, "paragraph-merge"),
    ("before-table", {"action": "accept"}, "paragraph-merge"),
    ("bookmark", {"action": "accept"}, "paragraph-merge"),
    ("cell-last", {"action": "accept"}, "paragraph-merge"),
    ("section", {"action": "accept"}, "section-break"),
    ("row", {"action": "accept"}, "revision-kind"),
    ("row", {"action": "reject"}, "revision-kind"),
    ("math", {"action": "reject"}, "revision-kind"),
    ("sectchange", {"action": "reject"}, "revision-kind"),
    ("partial-move", {"action": "accept", "authors": ["Jeremy"]}, "partial-move"),
    ("comment-in-del", {"action": "accept"}, "marks-in-change"),
    ("bookmark-straddle", {"action": "reject"}, "marks-in-change"),
    ("field-part", {"action": "accept"}, "marks-in-change"),
])
def test_refusals(kind, params, code):
    with pytest.raises(JobException) as e:
        run(awkward(kind), params)
    assert e.value.code == code, e.value.reason


@pytest.mark.parametrize("kind,action", [("goback-straddle", "reject"), ("bookmark-straddle", "accept"),
                                         ("comment-in-del", "reject"), ("field-part", "reject")])
def test_marks_that_stay_whole_are_fine(kind, action):
    plan, out, rec = run(awkward(kind), {"action": action})
    assert rec.passed, rec.to_markdown()
    names = [b.get(W + "name") for b in DocxPackage(out).xml(DOC).iter(W + "bookmarkStart")]
    if kind == "goback-straddle":                        # Word's cursor bookmark went whole: no end left behind
        assert names == [] and not list(DocxPackage(out).xml(DOC).iter(W + "bookmarkEnd"))
        assert any("_GoBack" in f for f in plan.flags)


def test_a_refused_kind_nobody_chose_is_left_alone():
    plan, out, rec = run(awkward("row"), {"action": "accept", "authors": ["Jack"]})          # Jeremy's row stays
    assert rec.passed and out == awkward("row")


@pytest.mark.parametrize("params", [{}, {"action": "keep"}, {"action": "accept", "authors": []},
                                    {"action": "accept", "authors": [" "]}, {"action": "accept", "before": "Sept 23"},
                                    {"action": "accept", "on_or_after": "2026-02-30"},
                                    {"action": "accept", "on_or_after": "2026-10-01", "before": "2026-09-01"},
                                    {"action": "accept", "allow_missing": "yes"}])
def test_bad_params(doc, params):
    with pytest.raises(JobException) as e:
        rev_module.ResolveTrackedChanges().plan(DocxPackage(doc), {}, params)
    assert e.value.code == "bad-params"
    with pytest.raises(JobException) as e:
        rev_module.ResolveTrackedChanges().plan(DocxPackage(doc), {"paragraphs": "all"}, {"action": "accept"})
    assert e.value.code == "bad-params"


def test_it_is_a_spec_of_its_own():
    """Accepting or rejecting touches every part it changes as a whole (rule 9): another op on the body is refused."""
    spec = spec_of({"action": "accept"})
    spec["ops"].append({"op": "replace_table", "params": {"pairs": [{"find": "Schedule", "replace": "Sched."}],
                                                          "tracked": False}})
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(redline()), spec)
    assert e.value.code == "conflict"


def test_the_worked_example(doc):
    spec = load_spec(EXAMPLE)
    plan, frags = plan_spec(DocxPackage(doc), spec)
    out = apply_spec(DocxPackage(doc), frags).output_bytes()
    assert run_suite(doc, out, plan.to_dict(), "s.docx", "o.docx").passed
    assert spec["ops"][0]["params"]["authors"] and json.dumps(spec)


# ---- planted defects ---------------------------------------------------------------------------------------------------

def test_a_wrong_output_is_caught(doc):
    plan, out, rec = run(doc, {"action": "accept"})
    assert rec.passed

    def keep_deleted_words(root):                       # accept "forgot" one deletion: its words stay, live
        for p in root.iter(W + "p"):
            if live(p).startswith("A."):
                r = etree.SubElement(p, W + "r")
                etree.SubElement(r, W + "t").text = "black steel "
    bad = mutate(out, DOC, keep_deleted_words)
    rec = run_suite(doc, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "V2" in failed(rec)
    assert any("not the source with the chosen tracked changes accepted" in e for e in v10_errors(rec))

    def drop_a_comment_mark(root):                      # a change no text or structure count sees: only V10 does
        for el in list(root.iter(W + "commentRangeStart")):
            el.getparent().remove(el)
    bad = mutate(out, DOC, drop_a_comment_mark)
    rec = run_suite(doc, bad, plan.to_dict(), "src.docx", "out.docx")
    assert failed(rec) == ["V10"]


def _shared(monkeypatch, change):
    """Make plan and apply agree on a wrong decision: every Resolution passes through ``change``."""
    real = rev_module.analyze

    def wrong(root, part, action, sel):
        res = real(root, part, action, sel)
        change(root, res)
        return res
    monkeypatch.setattr(rev_module, "analyze", wrong)


def test_a_selection_bug_shared_by_plan_and_apply_is_caught(doc, monkeypatch):
    monkeypatch.setattr(rev_module.Selection, "dated", property(lambda self: False))   # dates ignored on both sides
    plan, out, rec = run(doc, {"action": "accept", "on_or_after": "2026-09-23"})
    assert failed(rec) == ["V10"], rec.to_markdown()                                     # V2/V4 follow the wrong plan
    assert any("revisions that go here" in e or "not the source" in e for e in v10_errors(rec))


def test_accepting_deletions_by_unwrapping_them_is_caught(doc, monkeypatch):
    def swap(root, res):
        dels = [e for e in res.removed if e.tag == W + "del"]
        res.removed = [e for e in res.removed if e.tag != W + "del"]
        res.unwrapped += dels
    _shared(monkeypatch, swap)
    plan, out, rec = run(doc, {"action": "accept"})
    assert "V10" in failed(rec), rec.to_markdown()


def test_removing_the_wrong_paragraph_is_caught(doc, monkeypatch):
    def next_one(root, res):
        res.gone_paragraphs = [p.getnext() for p in res.gone_paragraphs]
    _shared(monkeypatch, next_one)
    plan, out, rec = run(doc, {"action": "accept"})
    assert "V10" in failed(rec), rec.to_markdown()


def test_joining_paragraphs_the_check_says_must_be_refused_is_caught(monkeypatch):
    monkeypatch.setattr(rev_module, "_leftover", lambda *a, **k: [])
    plan, out, rec = run(awkward("join"), {"action": "accept"})
    assert "V10" in failed(rec)
    assert any("the op must refuse" in e for e in v10_errors(rec))


def test_taking_a_comment_mark_away_with_the_text_is_caught(monkeypatch):
    monkeypatch.setattr(rev_module, "_marks_in_removed", lambda *a, **k: None)
    plan, out, rec = run(awkward("comment-in-del"), {"action": "accept"})
    assert failed(rec) == ["V10"], rec.to_markdown()
    assert any("holds a commentReference — the op must refuse" in e for e in v10_errors(rec))


def test_reject_that_forgets_to_bring_deleted_text_back_to_life_is_caught(doc, monkeypatch):
    real = rev_module.resolve

    def lazy(root, res):
        res.del_texts = []
        real(root, res)
    monkeypatch.setattr(rev_module, "resolve", lazy)
    plan, out, rec = run(doc, {"action": "reject"})
    assert {"V5c", "V10"} <= set(failed(rec)), rec.to_markdown()


def test_a_wrong_revision_count_is_caught(doc, monkeypatch):
    real = rev_module.gone_revisions
    monkeypatch.setattr(rev_module, "gone_revisions", lambda res: dict(list(real(res).items())[1:]))
    plan, out, rec = run(doc, {"action": "accept"})
    assert {"V4", "V10"} <= set(failed(rec))
