"""A tracked deletion over someone's pending revisions (docs/PLAN-2026-10-22-batch-consistency.md C6).

From the owner's Division 21 run: a tracked delete_paragraphs set aside every paragraph that held a redline, though Word
deletes over them. Now the deletion core (ops/deletion.py ↔ verify/deletion.py) deletes as Word does: text in a pending
insertion gets a deletion nested inside it (rejecting the insertion still removes all of it), text already deleted
stays as it is, a paragraph mark someone inserted gets a deletion beside its insertion, property changes stay. Still
refused: moves, pending row and cell changes, and a kept paragraph whose mark is already deleted. Round trips as in
tests/test_batch6_round_trip.py: accepting everything reads like the untracked deletion of the accepted source,
rejecting everything like the rejected source, rejecting the new author's changes like the source."""
from __future__ import annotations

import copy
import io
import zipfile
from typing import Callable, List

import pytest
from lxml import etree

from fx_deletion import by_text
from make_fixtures import build
from spec_surgeon import compiler
from spec_surgeon.catalog import COMMON_ASSUMPTIONS
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import delete_paragraphs as dp
from spec_surgeon.package import W, DocxPackage, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite

MAIN = "word/document.xml"
AUTHOR = "Spec Team"
OP = "delete_paragraphs"
SPEC = build("21 05 00", "X")                    # C. holds Jeremy's insertion, D. Jack's deletion
C = {"text_regex": r"^C\.\tSprinklers"}
D = {"text_regex": r"^D\.\tPipe"}
TABLE = {"text_regex": r"^(Rev|Date|Description|0|2026-01-01|Issued for Construction)$"}
ARTICLE = {"between_paragraphs": {"start_regex": r"^1\.01", "end_regex": r"^2\.01", "include_bounds": "start"}}


def mutate(docx: bytes, fn: Callable) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(MAIN))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == MAIN else zin.read(info.filename))
    return buf.getvalue()


def rev(tag: str, rid: int, author: str):
    el = etree.Element(W + tag)
    el.set(W + "id", str(rid))
    el.set(W + "author", author)
    el.set(W + "date", "2026-09-24T09:00:00Z")
    return el


def mark_rpr(p):
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    rpr = ppr.find(W + "rPr")
    if rpr is None:
        rpr = etree.SubElement(ppr, W + "rPr")
    return rpr


def runs_into(wrapper, runs) -> None:
    runs[0].addprevious(wrapper)
    for r in runs:
        wrapper.append(r)


def as_deleted(r) -> None:
    for t in r.findall(W + "t"):
        t.tag = W + "delText"


# ---- the fixtures: someone's redlines of every kind the deletion goes over or refuses -----------------------------

def inserted_paragraph(root) -> None:
    """B. is Jeremy's whole new paragraph: its runs in his insertion, its mark inserted too."""
    p = by_text(root, "B.")
    runs_into(rev("ins", 300, "Jeremy"), p.findall(W + "r"))
    mark_rpr(p).insert(0, rev("ins", 301, "Jeremy"))


def deleted_mark(start: str):
    def fn(root):
        mark_rpr(by_text(root, start)).insert(0, rev("del", 310, "Jack"))
    return fn


def formatting_changes(root) -> None:
    """A.'s first run made bold, and its paragraph's properties changed, both with Track Changes on."""
    p = by_text(root, "A.")
    r = p.find(W + "r")
    rpr = r.find(W + "rPr")
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        r.insert(0, rpr)
    etree.SubElement(rpr, W + "b")
    change = rev("rPrChange", 320, "Jack")
    etree.SubElement(change, W + "rPr")
    rpr.append(change)
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    etree.SubElement(ppr, W + "jc").set(W + "val", "both")
    pchange = rev("pPrChange", 321, "Jack")
    etree.SubElement(pchange, W + "pPr")
    ppr.append(pchange)


def updated_field(root) -> None:
    """E.'s REF field updated with Track Changes on: its old result deleted by Jack, the new one inserted by Jeremy."""
    p = by_text(root, "E.")
    old = next(r for r in p.findall(W + "r") if (r.findtext(W + "t") or "") == "21 10 00")
    new = copy.deepcopy(old)
    new.find(W + "t").text = "21 10 01"
    as_deleted(old)
    runs_into(rev("del", 330, "Jack"), [old])
    ins = rev("ins", 331, "Jeremy")
    old.getparent().addnext(ins)
    ins.append(new)


def moved_away(root) -> None:
    """B. moved below 13.01 with Track Changes on: B.'s runs in a moveFrom, a copy in a moveTo."""
    p, there = by_text(root, "B."), by_text(root, "13.01")
    runs = p.findall(W + "r")
    copies = [copy.deepcopy(r) for r in runs]
    for r in runs:
        as_deleted(r)
    p.insert(len(p) - len(runs), rev("moveFromRangeStart", 340, "Jack"))
    p.find(W + "moveFromRangeStart").set(W + "name", "move1")
    runs_into(rev("moveFrom", 341, "Jack"), runs)
    end = etree.SubElement(p, W + "moveFromRangeEnd")
    end.set(W + "id", "340")
    start = rev("moveToRangeStart", 342, "Jack")
    start.set(W + "name", "move1")
    there.append(start)
    to = rev("moveTo", 343, "Jack")
    there.append(to)
    for r in copies:
        to.append(r)
    etree.SubElement(there, W + "moveToRangeEnd").set(W + "id", "342")


def row_inserted(root) -> None:
    tr = [r for r in next(root.iter(W + "tbl")) if r.tag == W + "tr"][1]
    trpr = etree.Element(W + "trPr")
    tr.insert(0, trpr)
    trpr.append(rev("ins", 350, "Jeremy"))


def cell_deleted(root) -> None:
    tcpr = next(root.iter(W + "tbl")).find(f"{W}tr/{W}tc/{W}tcPr")
    tcpr.append(rev("cellDel", 351, "Jack"))


def grid_changed(root) -> None:
    grid = next(root.iter(W + "tbl")).find(W + "tblGrid")
    change = rev("tblGridChange", 360, "Jack")
    old = etree.SubElement(change, W + "tblGrid")
    for w in ("3000", "2760", "2880"):
        etree.SubElement(old, W + "gridCol").set(W + "w", w)
    grid.append(change)


def cell_inserted_text(root) -> None:
    p = by_text(root, "Issued for Construction")
    runs_into(rev("ins", 370, "Jeremy"), p.findall(W + "r"))


# ---- running it ---------------------------------------------------------------------------------------------------

def run(data: bytes, op: str, select: dict, params: dict):
    pkg = DocxPackage(data)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return frag, plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def delete(data: bytes, paragraphs_, tracked: bool = True):
    frag, plan, out, rec = run(data, OP, {"paragraphs": paragraphs_}, {"tracked": tracked, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    return frag, plan, out


def resolve(data: bytes, action: str, authors=None) -> bytes:
    params = {"action": action, **({"authors": authors} if authors else {})}
    _, _, out, rec = run(data, "resolve_tracked_changes", {}, params)
    assert rec.passed, rec.to_markdown()
    return out


def reading(data: bytes) -> List[str]:
    return [block_full_text(p) for p in paragraphs(DocxPackage(data).xml(MAIN))]


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def refused(data: bytes, paragraphs_) -> JobException:
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(data), {"paragraphs": paragraphs_}, {"tracked": True, "author": AUTHOR})
    return e.value


def written(out: bytes, start: str):
    return by_text(DocxPackage(out).xml(MAIN), start)


def new(el) -> bool:
    return el.get(W + "author") == AUTHOR


# ---- end to end ---------------------------------------------------------------------------------------------------

def test_text_someone_inserted_is_deleted_inside_their_insertion():
    frag, _, out = delete(SPEC, C)
    p = written(out, "C.")
    ins = p.find(W + "ins")
    assert ins.get(W + "author") == "Jeremy" and ins.get(W + "id") == "100"          # their insertion, as it was
    assert [c.tag for c in ins] == [W + "del"] and new(ins[0])                         # our deletion nested inside
    assert ins[0].find(f"{W}r/{W}delText").text.startswith("UL listed")
    assert not [r for r in p.iter(W + "r") if r.find(W + "t") is not None]            # nothing of it left live
    assert frag.rev_delta["del"] == 3 and frag.rev_delta["del_mark"] == 1
    assert any("what Jeremy inserted is deleted inside the insertion" in f for f in frag.flags)


def test_text_someone_deleted_stays_as_it_was():
    frag, _, out = delete(SPEC, D)
    p = written(out, "D.")
    dels = p.findall(W + "del")
    assert [(d.get(W + "author"), d.get(W + "id")) for d in dels if not new(d)] == [("Jack", "101")]
    jack = next(d for d in dels if not new(d))
    assert not jack.findall(f".//{W}del") and jack.find(f"{W}r/{W}delText").text == "black steel "
    assert len([d for d in dels if new(d)]) == 2                                       # the live runs on each side
    assert any("what Jack deleted stays as it was" in f for f in frag.flags)


def test_a_paragraph_mark_someone_inserted_gets_its_deletion_beside_the_insertion():
    src = mutate(SPEC, inserted_paragraph)
    frag, _, out = delete(src, {"text_regex": r"^B\.\t"})
    rpr = written(out, "B.").find(f"{W}pPr/{W}rPr")
    assert [(etree.QName(c).localname, c.get(W + "author")) for c in rpr] == [("ins", "Jeremy"), ("del", AUTHOR)]
    assert frag.rev_delta["del_mark"] == 1


def test_a_mark_already_deleted_gets_no_second_deletion():
    src = mutate(SPEC, deleted_mark("D."))
    frag, _, out = delete(src, D)
    rpr = written(out, "D.").find(f"{W}pPr/{W}rPr")
    assert [(etree.QName(c).localname, c.get(W + "author")) for c in rpr] == [("del", "Jack")]
    assert frag.rev_delta.get("del_mark", 0) == 0


def test_formatting_changes_stay_where_they_are():
    src = mutate(SPEC, formatting_changes)
    frag, _, out = delete(src, {"text_regex": r"^A\.\t"})
    assert any("the formatting changes by Jack stay with it" in f for f in frag.flags), frag.flags   # Codex on #83
    p = written(out, "A.")
    assert p.find(f"{W}pPr/{W}pPrChange").get(W + "id") == "321"
    assert p.find(f"{W}del/{W}r/{W}rPr/{W}rPrChange").get(W + "id") == "320"


def test_a_field_updated_with_track_changes_on_is_not_cut():
    src = mutate(SPEC, updated_field)
    _, _, out = delete(src, {"text_regex": r"^E\.\t"})
    p = written(out, "E.")
    assert p.find(f"{W}ins/{W}del/{W}r/{W}delText").text == "21 10 01"                  # their new result, deleted
    assert p.find(f"{W}del[@{W}author='Jack']/{W}r/{W}delText").text == "21 10 00"     # their old result, as it was


def test_a_table_with_redlines_in_its_cells_and_a_resized_grid_is_deleted():
    src = mutate(mutate(SPEC, cell_inserted_text), grid_changed)
    frag, _, out = delete(src, TABLE)
    assert any("Jeremy inserted" in f and "formatting changes by Jack" in f for f in frag.flags), frag.flags
    tbl = next(DocxPackage(out).xml(MAIN).iter(W + "tbl"))
    assert tbl.find(f"{W}tblGrid/{W}tblGridChange").get(W + "id") == "360"
    assert all(new(tr.find(f"{W}trPr/{W}del")) for tr in tbl.findall(W + "tr"))
    assert by_text(tbl, "Issued").find(f"{W}ins/{W}del") is not None


def test_the_whole_redlined_body_is_deleted_tracked():
    frag, _, out = delete(SPEC, "all")
    assert any("Jeremy inserted" in f and "Jack deleted" in f for f in frag.flags)


# ---- round trips --------------------------------------------------------------------------------------------------

ROUND_TRIPS = {
    "someone's insertion": (SPEC, C),
    "someone's deletion": (SPEC, D),
    "a redlined article": (SPEC, ARTICLE),
    "someone's new paragraph": (mutate(SPEC, inserted_paragraph), {"text_regex": r"^B\.\t"}),
    "formatting changes": (mutate(SPEC, formatting_changes), {"text_regex": r"^A\.\t"}),
    "a redlined table": (mutate(SPEC, cell_inserted_text), TABLE),
}


@pytest.mark.parametrize("case", sorted(ROUND_TRIPS))
def test_accepting_reads_like_the_untracked_deletion_and_rejecting_like_the_source(case):
    src, select = ROUND_TRIPS[case]
    _, _, tracked = delete(src, select)
    _, _, clean = delete(resolve(src, "accept"), select, tracked=False)
    assert reading(resolve(tracked, "accept")) == reading(clean), case
    assert reading(resolve(tracked, "reject")) == reading(resolve(src, "reject")), case
    assert reading(resolve(tracked, "reject", [AUTHOR])) == reading(src), case


def test_accepting_a_deletion_over_a_mark_already_deleted_takes_the_paragraph_and_rejecting_restores_it():
    src = mutate(SPEC, deleted_mark("D."))
    _, _, tracked = delete(src, D)
    accepted = reading(resolve(tracked, "accept"))
    assert not any(t.startswith("D.") for t in accepted) and any(t.startswith("E.") for t in accepted)
    assert reading(resolve(tracked, "reject", [AUTHOR])) == reading(src)


# ---- still refused ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("fixture, select, words", [
    (moved_away, {"text_regex": r"^B\.\t"}, "moved with tracked changes"),
    (moved_away, {"text_regex": r"^13\.01"}, "moved with tracked changes"),
    (row_inserted, TABLE, "row or cell"),
    (cell_deleted, TABLE, "row or cell"),
    (deleted_mark("30.01"), {"text_regex": r"^30\.01"}, "already carries a pending deletion"),   # the body's last
])
def test_what_cannot_be_predicted_is_still_refused(fixture, select, words):
    e = refused(mutate(SPEC, fixture), select)
    assert e.code == "existing-revisions" and words in e.reason, e.reason
    _, _, out, rec = run(mutate(SPEC, fixture), OP, {"paragraphs": select}, {"tracked": False})
    assert rec.passed, rec.to_markdown()                                               # untracked goes through


@pytest.mark.parametrize("fixture, select, refusal", [
    (row_inserted, TABLE, "refuse_table_pending"),
    (cell_deleted, TABLE, "refuse_table_pending"),
    (deleted_mark("30.01"), {"text_regex": r"^30\.01"}, "refuse_pending"),
])
def test_the_verifier_refuses_on_its_own(fixture, select, refusal, monkeypatch):
    monkeypatch.setattr(dp, refusal, lambda *a, **k: None)                       # the op's refusal planted away
    src = mutate(SPEC, fixture)
    _, _, _, rec = run(src, OP, {"paragraphs": select}, {"tracked": True, "author": AUTHOR})
    assert "V10" in failed(rec) and "the op must refuse" in v10(rec), v10(rec)


# ---- planted defects ----------------------------------------------------------------------------------------------

def rewrite(out: bytes, fn: Callable) -> bytes:
    return mutate(out, fn)


def test_a_wrong_output_is_caught():
    _, plan, out = delete(SPEC, C)

    def outside(root):                            # our deletion moved out of Jeremy's insertion, after it
        ins = by_text(root, "C.").find(W + "ins")
        ins.addnext(ins[0])
    rec = run_suite(SPEC, rewrite(out, outside), plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "V5b" in failed(rec)

    _, plan, out = delete(SPEC, D)

    def again(root):                              # Jack's deleted run deleted a second time, inside his deletion
        jack = by_text(root, "D.").find(f"{W}del[@{W}author='Jack']")
        runs_into(rev("del", 999, AUTHOR), list(jack))
    rec = run_suite(SPEC, rewrite(out, again), plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec)

    src = mutate(SPEC, inserted_paragraph)
    _, plan, out = delete(src, {"text_regex": r"^B\.\t"})

    def dropped(root):                            # Jeremy's inserted mark lost beside the new deletion
        rpr = by_text(root, "B.").find(f"{W}pPr/{W}rPr")
        rpr.remove(rpr.find(W + "ins"))
    rec = run_suite(src, rewrite(out, dropped), plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec)


def test_a_wrong_implementation_shared_by_plan_and_apply_is_caught(monkeypatch):
    real = dp.tracked_runs

    def skip_insertions(p, where):               # someone's inserted text left live
        return [r for r in real(p, where) if r.getparent().tag != W + "ins"]
    monkeypatch.setattr(dp, "tracked_runs", skip_insertions)
    _, _, out, rec = run(SPEC, OP, {"paragraphs": C}, {"tracked": True, "author": AUTHOR})
    assert "UL listed" in written(out, "C.").find(f"{W}ins/{W}r/{W}t").text          # the defect did what it does
    assert "V10" in failed(rec)
    monkeypatch.setattr(dp, "tracked_runs", real)

    def into_deletions(p, where):                # someone's deleted text deleted again
        return real(p, where) + [r for r in p.iter(W + "r") if r.getparent().tag == W + "del"]
    monkeypatch.setattr(dp, "tracked_runs", into_deletions)
    _, _, out, rec = run(SPEC, OP, {"paragraphs": D}, {"tracked": True, "author": AUTHOR})
    assert written(out, "D.").find(f"{W}del[@{W}author='Jack']/{W}del") is not None
    assert "V10" in failed(rec)
    monkeypatch.setattr(dp, "tracked_runs", real)

    monkeypatch.setattr(dp, "mark_deleted", lambda p: False)       # a mark already deleted deleted again
    src = mutate(SPEC, deleted_mark("D."))
    _, _, out, rec = run(src, OP, {"paragraphs": D}, {"tracked": True, "author": AUTHOR})
    assert len(written(out, "D.").findall(f"{W}pPr/{W}rPr/{W}del")) == 2
    assert "V10" in failed(rec)


# ---- the words around it ------------------------------------------------------------------------------------------

def test_the_prompt_the_catalog_and_the_explanation_say_what_it_does():
    prompt = compiler.system_prompt(chat=True)
    assert "A tracked deletion goes over someone's pending tracked changes as Word does" in prompt
    assert any("deleted inside it" in a for a in COMMON_ASSUMPTIONS[OP])
    title, words = FAILURE_EXPLANATIONS["existing-revisions"]
    assert "moved" in words and "row" in words
