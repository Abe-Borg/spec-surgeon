"""resolve_tracked_changes on tracked table rows (built-in batch 6, F5 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §6): accepting
a deleted row or rejecting an inserted one takes the row (a table losing every row, whole); the reverse takes only the
marker. Both sides (ops/revisions.py ↔ verify/revisions.py). The round trips run on what the structure ops of this batch
write: accepting their tracked output gives exactly their untracked output, rejecting it gives back the source."""
from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from fx_revisions import JACK, JEREMY, awkward, stamp
from fx_table_structure import build_structure
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import revisions as rev_module
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import first_difference

MAIN = "word/document.xml"
AUTHOR = "F5 Tester"
SCHEDULE = {"first_row_regex": "^Tag"}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_structure()


def run(data: bytes, op: str, select: dict, params: dict):
    pkg = DocxPackage(data)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def resolve(data: bytes, action: str, **params):
    return run(data, "resolve_tracked_changes", {}, {"action": action, **params})


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def body(data: bytes):
    return DocxPackage(data).xml(MAIN)


def mutate(docx: bytes, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(MAIN))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == MAIN else zin.read(info.filename))
    return buf.getvalue()


def frag_xml(xml: str):
    return etree.fromstring(f'<x xmlns:w="{W[1:-1]}">{xml}</x>')[0]


def rows(tbl):
    return [c for c in tbl if c.tag == W + "tr"]


ROUND_TRIPS = [
    ("delete_tables", {"tables": SCHEDULE}, {}),
    ("delete_tables", {"tables": {"first_row_regex": "^Valve"}}, {}),                     # a nested table inside
    ("delete_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [1, 2]}}, {}),
    ("delete_table_rows", {"tables": {"first_row_regex": "^Zone"}, "rows": {"index_range": [2, 3]}}, {}),
    ("delete_table_rows", {"tables": {"first_row_regex": "^Item"}, "rows": "all"}, {}),       # the table goes whole
    ("insert_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}},
     {"rows": [["FP-3", "Booster pump", "100 gpm"], ["FP-4", "", " spare "]]}),
    ("insert_table_rows", {"tables": SCHEDULE, "rows": {"index_range": [0, 0]}},
     {"rows": [["SCHEDULE", "", ""]], "position": "before", "repeat_header": True}),
    ("insert_table_rows", {"tables": {"first_row_regex": "^Ref"}}, {"rows": [["R-2", "Second"]], "position": "at_end"}),
    ("insert_table", {"paragraphs": {"text_regex": "^1\\.2 NOTES"}},
     {"rows": [["Tag", "Description"], ["FP-1", "Fire pump"]], "header_rows": 1, "style": "Table Grid"}),
]


@pytest.mark.parametrize("op,select,params", ROUND_TRIPS)
def test_accepting_gives_the_untracked_edit_and_rejecting_the_source(src, op, select, params):
    _, tracked, rec = run(src, op, select, {**params, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    _, plain, rec = run(src, op, select, {**params, "tracked": False})
    assert rec.passed, rec.to_markdown()
    plan, accepted, rec = resolve(tracked, "accept", authors=[AUTHOR])
    assert rec.passed, rec.to_markdown()
    assert first_difference(body(accepted), body(plain)) is None
    plan, rejected, rec = resolve(tracked, "reject", authors=[AUTHOR])
    assert rec.passed, rec.to_markdown()
    assert first_difference(body(rejected), body(src)) is None


def test_a_deleted_table_holding_a_crossing_bookmark_is_left_for_word_on_accept(src):
    """The untracked deletion keeps the bookmark's inside end at the table's place; accepting a tracked one would take it
    away with the rows (resolve_tracked_changes' rule for content that goes: one end of a bookmark is refused)."""
    _, tracked, rec = run(src, "delete_tables", {"tables": {"first_row_regex": "^Ref"}}, {"author": AUTHOR})
    assert rec.passed
    with pytest.raises(JobException) as e:
        resolve(tracked, "accept", authors=[AUTHOR])
    assert e.value.code == "marks-in-change"
    plan, rejected, rec = resolve(tracked, "reject", authors=[AUTHOR])
    assert rec.passed and first_difference(body(rejected), body(src)) is None


def test_a_row_word_inserted(src):
    source = awkward("row")                                # Jeremy's inserted row, its text not marked
    plan, out, rec = resolve(source, "accept")
    assert rec.passed, rec.to_markdown()
    tbl = body(out).find(f".//{W}tbl")
    assert len(rows(tbl)) == 2 and rows(tbl)[1].find(W + "trPr") is None
    assert plan.rev_delta == {"ins": -1, "del": 0}
    plan, out, rec = resolve(source, "reject")
    assert rec.passed, rec.to_markdown()
    assert len(rows(body(out).find(f".//{W}tbl"))) == 1
    assert [d["before"] for d in plan.to_dict()["diffs"]] == ["inserted row"]


def _word_deleted_row(root):
    """Jack deleted row 2 of the schedule; Jeremy had deleted a word in it before."""
    tr = rows(next(root.iter(W + "tbl")))[2]
    trpr = tr.find(W + "trPr")
    if trpr is None:
        trpr = etree.Element(W + "trPr")
        tr.insert(0, trpr)
    trpr.append(frag_xml(f'<w:del w:id="910" w:author="{JACK[0]}" w:date="{JACK[1]}"/>'))
    run_ = tr.find(f".//{W}r")
    d = frag_xml(f'<w:del w:id="911" w:author="{JEREMY[0]}" w:date="{JEREMY[1]}"/>')
    run_.addprevious(d)
    d.append(run_)
    for t in run_.iter(W + "t"):
        t.tag = W + "delText"


def test_a_row_another_author_deleted_takes_what_it_holds(src):
    data = mutate(src, _word_deleted_row)
    plan, out, rec = resolve(data, "accept", authors=["Jack"])
    assert rec.passed, rec.to_markdown()
    assert len(rows(next(body(out).iter(W + "tbl")))) == 3
    assert plan.rev_delta["del"] == -2                       # the row's marker and Jeremy's deletion inside it
    plan, out, rec = resolve(data, "reject", authors=["Jack"])
    assert rec.passed, rec.to_markdown()
    tr = rows(next(body(out).iter(W + "tbl")))[2]
    assert tr.find(f"{W}trPr/{W}del") is None and tr.find(f".//{W}del") is not None     # Jeremy's stays pending
    plan, out, rec = resolve(data, "reject", authors=["Jeremy"])
    assert rec.passed and rows(next(body(out).iter(W + "tbl")))[2].find(f"{W}trPr/{W}del") is not None


def _cell_insert(root):
    tc = rows(next(root.iter(W + "tbl")))[1].find(W + "tc")
    tc.find(W + "tcPr").append(frag_xml(f'<w:cellIns w:id="920" w:author="{JACK[0]}" w:date="{JACK[1]}"/>'))


def _row_in_control(root):
    _word_deleted_row(root)
    tr = rows(next(root.iter(W + "tbl")))[2]
    sdt = frag_xml("<w:sdt><w:sdtPr/><w:sdtContent/></w:sdt>")
    tr.addprevious(sdt)
    sdt.find(W + "sdtContent").append(tr)


def _whole_table_between_tables(root):
    for text in ("Zone schedule", "Valve schedule"):
        p = next(p for p in root.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")) == text)
        p.getparent().remove(p)
    tbl = list(root.iter(W + "tbl"))[1]
    for k, tr in enumerate(rows(tbl)):
        trpr = tr.find(W + "trPr")
        if trpr is None:
            trpr = etree.Element(W + "trPr")
            tr.insert(0, trpr)
        trpr.append(frag_xml(f'<w:del w:id="{930 + k}" w:author="{JACK[0]}" w:date="{JACK[1]}"/>'))


def _comment_in_deleted_row(root):
    _word_deleted_row(root)
    p = rows(next(root.iter(W + "tbl")))[2].find(f".//{W}p")
    p.append(frag_xml('<w:commentRangeStart w:id="3"/>'))


def _moved_row(root):
    tr = rows(next(root.iter(W + "tbl")))[3]
    tr.insert(0, frag_xml(f'<w:trPr><w:moveFrom w:id="940" w:author="{JACK[0]}" w:date="{JACK[1]}"/></w:trPr>'))


@pytest.mark.parametrize("plant,action,code", [
    (_cell_insert, "accept", "revision-kind"), (_cell_insert, "reject", "revision-kind"),
    (_row_in_control, "accept", "table-structure"), (_whole_table_between_tables, "accept", "table-structure"),
    (_comment_in_deleted_row, "accept", "marks-in-change"), (_moved_row, "accept", "revision-kind"),
])
def test_what_is_left_for_word(src, plant, action, code):
    with pytest.raises(JobException) as e:
        resolve(mutate(src, plant), action)
    assert e.value.code == code, e.value.reason


def test_the_marker_side_needs_no_room(src):
    """Rejecting a deleted row (or a deleted whole table between two tables) only takes the markers."""
    plan, out, rec = resolve(mutate(src, _whole_table_between_tables), "reject")
    assert rec.passed, rec.to_markdown()
    plan, out, rec = resolve(mutate(src, _row_in_control), "reject", authors=["Jack"])
    assert rec.passed, rec.to_markdown()


def test_the_verifier_refuses_what_the_op_must(src, monkeypatch):
    monkeypatch.setattr(rev_module, "table_removal_problem", lambda container, going: None)
    plan, out, rec = resolve(mutate(src, _whole_table_between_tables), "accept")
    assert "V10" in failed(rec) and "the op must refuse" in v10(rec)


def test_a_shared_wrong_row_rule_is_caught(src, monkeypatch):
    """A resolution that takes an accepted inserted row away (the reverse of the rule), shared by plan and apply."""
    _, tracked, rec = run(src, "insert_table_rows", {"tables": SCHEDULE, "rows": {"text_regex": "^FP-1"}},
                          {"rows": [["FP-3", "Booster pump", "100 gpm"]], "author": AUTHOR})

    real = rev_module._rows
    monkeypatch.setattr(rev_module, "_rows", lambda res, part, action: real(
        res, part, "reject" if action == "accept" else "accept"))
    plan, out, rec = resolve(tracked, "accept", authors=[AUTHOR])
    assert "V10" in failed(rec) and "resolved_changes" in v10(rec)


# ---- a vertical merge (delete_table_rows' rule, on both sides) -------------------------------------------------------
# The Zone schedule (table 1): "Notes" merges down from Z1 ("Shared", w:vMerge restart) through Z2 and Z3 (continue).

ZONE_ROWS = {"Z1": 1, "Z2": 2, "Z3": 3, "Totals": 4}


def _zone_rows(root):
    return rows(list(root.iter(W + "tbl"))[1])


def _tracked_zone_rows(kind: str, *names: str):
    """Jack's tracked insertion (``ins``) or deletion (``del``) of the named rows of the Zone schedule."""
    def plant(root):
        for k, name in enumerate(names):
            tr = _zone_rows(root)[ZONE_ROWS[name]]
            trpr = tr.find(W + "trPr")
            if trpr is None:
                trpr = etree.Element(W + "trPr")
                tr.insert(0, trpr)
            trpr.append(frag_xml(f'<w:{kind} w:id="{950 + k}" w:author="{JACK[0]}" w:date="{JACK[1]}"/>'))
    return plant


def _zone_texts(data: bytes):
    return [" | ".join("".join(t.text or "" for t in tc.iter(W + "t")) for tc in tr.findall(W + "tc"))
            for tr in _zone_rows(body(data))]


@pytest.mark.parametrize("kind,action,names", [
    ("del", "accept", ("Z1",)), ("ins", "reject", ("Z1",)), ("del", "accept", ("Z1", "Z2")),
])
def test_a_tracked_row_that_starts_a_merge_is_left_for_word(src, kind, action, names):
    """Taking the merge's first row (its text, "Shared") while a row it goes on in stays would leave the merged cell
    empty: refused as delete_table_rows refuses it."""
    with pytest.raises(JobException) as e:
        resolve(mutate(src, _tracked_zone_rows(kind, *names)), action)
    assert e.value.code == "vertical-merge", e.value.reason
    assert "row 1" in e.value.reason
    assert "vertical-merge" in FAILURE_EXPLANATIONS and "tracked" in FAILURE_EXPLANATIONS["vertical-merge"][1]


@pytest.mark.parametrize("kind,action,names,left", [
    ("del", "accept", ("Z2",), ["Zone | Area | Notes", "Z1 | North | Shared", "Z3 | East | ", "Totals | 3"]),
    ("del", "accept", ("Z3",), ["Zone | Area | Notes", "Z1 | North | Shared", "Z2 | South | ", "Totals | 3"]),
    ("ins", "reject", ("Z2", "Z3"), ["Zone | Area | Notes", "Z1 | North | Shared", "Totals | 3"]),
    ("del", "accept", ("Z1", "Z2", "Z3"), ["Zone | Area | Notes", "Totals | 3"]),      # the merged block goes whole
    ("del", "accept", ("Totals",), ["Zone | Area | Notes", "Z1 | North | Shared", "Z2 | South | ", "Z3 | East | "]),
    ("del", "reject", ("Z1",), None), ("ins", "accept", ("Z1",), None),                 # only the marker goes
])
def test_rows_that_keep_a_merge_whole_resolve(src, kind, action, names, left):
    """Taking later rows of a merge only shortens it; a merge going whole, a row outside every merge and the marker
    side resolve as before."""
    data = mutate(src, _tracked_zone_rows(kind, *names))
    plan, out, rec = resolve(data, action)
    assert rec.passed, rec.to_markdown()
    assert _zone_texts(out) == (left if left is not None else _zone_texts(src))
    assert all(tr.find(f"{W}trPr/{W}{kind}") is None for tr in _zone_rows(body(out)))


def test_an_output_that_cuts_a_merge_is_caught(src):
    """A wrong OUTPUT: accepting Jack's deletion of Z2 is planned and verified; an output that also lost Z1 — the merged
    cell's text gone, Z3's w:vMerge continuing nothing — fails V10."""
    data = mutate(src, _tracked_zone_rows("del", "Z2"))
    plan, out, rec = resolve(data, "accept")
    assert rec.passed, rec.to_markdown()

    def cut(root):
        z1 = _zone_rows(root)[1]
        z1.getparent().remove(z1)
    bad = mutate(out, cut)
    assert _zone_texts(bad) == ["Zone | Area | Notes", "Z3 | East | ", "Totals | 3"]
    assert _zone_rows(body(bad))[1].find(f".//{W}vMerge") is not None
    rec = run_suite(data, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "resolved_changes" in v10(rec)


def test_a_shared_wrong_merge_rule_is_caught(src, monkeypatch):
    """A wrong IMPLEMENTATION shared by plan and apply: without the merge rule, accepting Jack's deletion of Z1 takes
    the merged cell's text away (what the op did before); the verifier's own rule says the op must refuse."""
    monkeypatch.setattr(rev_module, "merge_crossings", lambda tbl, gone: [])
    plan, out, rec = resolve(mutate(src, _tracked_zone_rows("del", "Z1")), "accept")
    assert _zone_texts(out) == ["Zone | Area | Notes", "Z2 | South | ", "Z3 | East | ", "Totals | 3"]
    assert "V10" in failed(rec) and "the op must refuse" in v10(rec) and "vertically merged" in v10(rec)
