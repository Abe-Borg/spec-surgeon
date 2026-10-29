"""delete_paragraphs grown over ops/deletion.py and V10's ``deleted_paragraphs`` kind (built-in batch 6, F2 —
docs/BUILTIN_OPS_BATCH6_DESIGN.md §1): "all", whole tables, partly chosen tables, fields, section breaks, comments,
notes, bookmarks, pictures and links, kept paragraphs — end to end untracked and tracked; a wrong OUTPUT per rule; wrong
IMPLEMENTATIONS shared by plan and apply; the two sides' decisions on every fixture; refusals with their codes;
parameters; touches and composition; the catalog; the batch pipeline; a LibreOffice render; and wiping a multi-section
document's body."""
from __future__ import annotations

import ast
import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

import fx_deletion as fx
from fx_deletion import by_text, text_of
from make_fixtures import add_parts, build, patch_docx
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import apply_spec, plan_chain, split_into_stages, stage_cuts
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import delete_paragraphs as dp
from spec_surgeon.ops import deletion
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import deletion as vdel
from spec_surgeon.verify.reader import VPackage

MAIN = "word/document.xml"
AUTHOR = "Spec Team"
OP = "delete_paragraphs"


@pytest.fixture(scope="module")
def tables() -> bytes:
    return fx.build_tables()


@pytest.fixture(scope="module")
def fields() -> bytes:
    return fx.build_fields()


@pytest.fixture(scope="module")
def sections() -> bytes:
    return fx.build_sections()


@pytest.fixture(scope="module")
def marks() -> bytes:
    return fx.build_marks()


@pytest.fixture(scope="module")
def wipe() -> bytes:
    return fx.build_wipe()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def delete(data: bytes, paragraphs, tracked: bool, **params):
    select = {"paragraphs": paragraphs, **({"scope": params.pop("scope")} if "scope" in params else {})}
    plan, out, rec = run_ops(data, [(OP, select, {"tracked": tracked, "author": AUTHOR, **params})])
    assert rec.passed, rec.to_markdown()
    return plan, out


def refused(data: bytes, paragraphs, tracked: bool = False, **params) -> JobException:
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(data), {"paragraphs": paragraphs}, {"tracked": tracked, **params})
    return e.value


def failed(rec):
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    root = etree.fromstring(zin.read(part))
    fn(root)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zin.infolist():
            z.writestr(info, serialize_xml(root) if info.filename == part else zin.read(info.filename))
    return buf.getvalue()


def caught(src, plan, out, part, fn, needle=None, check="V10"):
    rec = run_suite(src, mutate(out, part, fn), plan.to_dict(), "src.docx", "out.docx")
    assert check in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


def body_texts(data: bytes, part: str = MAIN) -> List[str]:
    return [text_of(p) for p in DocxPackage(data).xml(part).iter(W + "p")]


def xml(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def names(data: bytes) -> List[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


TRACKED = pytest.mark.parametrize("tracked", [False, True], ids=["untracked", "tracked"])
TABLE_A = {"text_regex": "^(Tag|Size|P-1|2 in)$"}


# ---- tables -------------------------------------------------------------------------------------------------------

@TRACKED
def test_a_table_all_of_whose_paragraphs_are_chosen_goes_whole(tables, tracked):
    plan, out = delete(tables, TABLE_A, tracked)
    root = xml(out)
    if not tracked:
        assert len(list(root.iter(W + "tbl"))) == len(list(xml(tables).iter(W + "tbl"))) - 1
        assert "Tag" not in body_texts(out) and "Between the tables." in body_texts(out)
        assert plan.rev_delta.get("del", 0) == 0
    else:
        first = next(root.iter(W + "tbl"))
        rows = [c for c in first if c.tag == W + "tr"]
        assert all(len(tr.findall(f"{W}trPr/{W}del")) == 1 for tr in rows)
        assert all(p.find(f"{W}pPr/{W}rPr/{W}del") is not None for p in first.iter(W + "p"))   # every mark, the last too
        # V4 counts the row marks as w:del (their parent is w:trPr, not w:rPr): 4 cells' runs + 2 rows
        assert plan.rev_delta == {"ins": 0, "del": 6, "del_mark": 4}
        assert plan.property_assertions[0]["expected"][MAIN]["rows"] == [[0, 0], [0, 1]]


@TRACKED
def test_a_paragraph_between_two_tables_stays_emptied(tables, tracked):
    plan, out = delete(tables, {"text_regex": "^Between"}, tracked)
    p = next(p for p in xml(out).iter(W + "p") if p.getprevious() is not None and p.getprevious().tag == W + "tbl"
             and p.getnext() is not None and p.getnext().tag == W + "tbl")
    if tracked:
        assert p.find(f"{W}pPr/{W}rPr/{W}del") is None and p.find(f"{W}del/{W}r/{W}delText").text == "Between the tables."
    else:
        assert text_of(p) == "" and [c.tag for c in p if c.tag != W + "pPr"] == []
    assert any("stays, emptied" in f or "keeps its paragraph mark" in f for f in plan.flags)


@TRACKED
def test_a_partly_chosen_table_keeps_each_cells_last_paragraph(tables, tracked):
    plan, out = delete(tables, {"text_regex": "^(V-1|175 psi|Rated)"}, tracked)
    second = list(xml(out).iter(W + "tbl"))[1]
    last_row = [c for c in second if c.tag == W + "tr"][1]
    cells = [c for c in last_row if c.tag == W + "tc"]
    if tracked:
        # "175 psi" (not the cell's last) is deleted with its mark; "Rated per UL." (the last) keeps its mark
        assert [p.find(f"{W}pPr/{W}rPr/{W}del") is not None for p in cells[1].findall(W + "p")] == [True, False]
        assert last_row.find(f"{W}trPr/{W}del") is None
    else:
        assert [len(c.findall(W + "p")) for c in cells] == [1, 1]
        assert [text_of(c.find(W + "p")) for c in cells] == ["", ""]
    assert plan.property_assertions[0]["expected"][MAIN]["tables"] == []


@TRACKED
def test_a_nested_table_goes_and_its_cell_keeps_a_paragraph(tables, tracked):
    plan, out = delete(tables, {"text_regex": "^Inner cell"}, tracked)
    outer = list(xml(out).iter(W + "tbl"))[2]
    if not tracked:
        assert len(list(outer.iter(W + "tbl"))) == 1             # the nested table is gone, the cell keeps its paragraph
        assert "Above the inner table." in body_texts(out)
    else:
        assert plan.property_assertions[0]["expected"][MAIN]["rows"] == [[3, 0]]


@TRACKED
def test_all_of_a_body_with_tables_leaves_one_paragraph(tables, tracked):
    plan, out = delete(tables, "all", tracked)
    body = xml(out).find(W + "body")
    if not tracked:
        assert [c.tag for c in body] == [W + "p", W + "sectPr"] and body_texts(out) == [""]
    else:
        assert body_texts(out) == body_texts(tables)               # deleted text stays as deleted text
        last = [c for c in body if c.tag == W + "p"][-1]
        assert last.find(f"{W}pPr/{W}rPr/{W}del") is None          # the final paragraph mark stays


# ---- fields -------------------------------------------------------------------------------------------------------

@TRACKED
def test_fields_wholly_inside_the_deletion_go(fields, tracked):
    plan, out = delete(fields, {"text_regex": "^A\\. Page|SUMMARY|SUBMITTALS|QUALITY"}, tracked)
    root = xml(out)
    if tracked:
        assert len(list(root.iter(W + "delInstrText"))) == 2 and not any(
            "PAGE" in (t.text or "") or "TOC" in (t.text or "") for t in root.iter(W + "instrText"))
    else:
        assert [t.text for t in root.iter(W + "instrText")] == [" REF Clause1 \\h "]


@TRACKED
def test_a_field_crossing_the_edge_is_refused(fields, tracked):
    e = refused(fields, {"text_regex": "SUMMARY|SUBMITTALS"}, tracked)
    assert e.code == "field-crossing" and "TOC" in e.reason


def test_a_simple_field_goes_untracked_and_is_refused_tracked(fields):
    plan, out = delete(fields, {"text_regex": "^B\\. See"}, False)
    assert not list(xml(out).iter(W + "fldSimple"))
    assert refused(fields, {"text_regex": "^B\\. See"}, True).code == "field-result"


def test_a_bookmark_a_field_points_to_is_flagged_when_it_goes(fields):
    plan, _ = delete(fields, {"text_regex": "^C\\. The clause"}, False)
    assert any("REF field still points to the bookmark 'Clause1'" in f for f in plan.flags)


# ---- section breaks -----------------------------------------------------------------------------------------------

def test_a_section_break_untracked_joins_the_next_section_and_its_unused_parts_leave(sections):
    plan, out = delete(sections, {"text_regex": "^End of the cover"}, False)
    assert len(DocxPackage(out).sections()) == 2
    gone = ["word/header1.xml", "word/_rels/header1.xml.rels", "word/media/image1.png", "word/footer1.xml"]
    assert sorted(plan.members_removed) == sorted(gone) and not set(gone) & set(names(out))
    assert any("join section 2" in f for f in plan.flags)
    assert any("inherited its default footer" in f and "now shows none" in f for f in plan.flags)


def test_a_section_break_tracked_is_marked_deleted_and_stays(sections):
    plan, out = delete(sections, {"text_regex": "^End of the cover"}, True)
    p = by_text(xml(out), "End of the cover")
    assert p.find(f"{W}pPr/{W}sectPr") is not None and p.find(f"{W}pPr/{W}rPr/{W}del") is not None
    assert plan.members_removed == [] and any("once the deletion is accepted" in f for f in plan.flags)


def test_a_part_another_section_still_uses_stays(sections):
    plan, out = delete(sections, {"text_regex": "^End of the body"}, False)
    assert plan.members_removed == ["word/header2.xml"]         # the appendix inherited it; nothing else used it
    assert any("section 3 inherited its default header" in f for f in plan.flags)


# ---- comments, bookmarks, notes, pictures, links ----------------------------------------------------------------

def test_a_comment_wholly_inside_goes_with_its_reply_and_side_entries(marks):
    plan, out = delete(marks, {"text_regex": "^A\\. Comment"}, False)
    assert [c.get(W + "id") for c in xml(out, fx.COMMENTS).findall(W + "comment")] == ["2"]
    ex = xml(out, "word/commentsExtended.xml")
    assert [e.get("{%s}paraId" % fx.W15) for e in ex] == ["1A000003"]
    assert any("comment(s) 0, 1" in f for f in plan.flags)


def test_a_comment_crossing_the_edge_is_refused_untracked_and_stays_tracked(marks):
    assert refused(marks, {"text_regex": "^B\\. A comment"}, False).code == "comment-crossing"
    plan, out = delete(marks, {"text_regex": "^B\\. A comment"}, True)
    assert len(xml(out, fx.COMMENTS).findall(W + "comment")) == 3
    plan, out = delete(marks, {"text_regex": "^[BC]\\. "}, False)          # both its paragraphs: it goes
    assert [c.get(W + "id") for c in xml(out, fx.COMMENTS).findall(W + "comment")] == ["0", "1"]


def test_tracked_deletion_keeps_comment_marks_and_flags_them(marks):
    plan, out = delete(marks, {"text_regex": "^A\\. Comment"}, True)
    p = by_text(xml(out), "A. Comment")
    assert p.find(W + "commentRangeStart") is not None and p.find(f"{W}del/{W}r/{W}commentReference") is not None
    assert any("comment(s) 0, 1 stay, anchored to deleted text" in f for f in plan.flags)


def test_a_bookmark_wholly_inside_goes_and_a_crossing_one_keeps_its_outside_half(marks):
    plan, out = delete(marks, {"text_regex": "^D\\. Bookmark"}, False)
    assert "Inside" not in [b.get(W + "name") for b in xml(out).iter(W + "bookmarkStart")]
    assert any("REF field still points to the bookmark 'Inside'" in f for f in plan.flags)
    for start, half in (("^E\\. A bookmark", W + "bookmarkStart"), ("^F\\. and ends", W + "bookmarkEnd")):
        plan, out = delete(marks, {"text_regex": start}, False)
        moved = [b for b in xml(out).find(W + "body") if b.tag == half]
        assert len(moved) == 1 and moved[0].get(W + "id") == "22"   # it took the deleted paragraph's place
        assert any("ran into the deleted text from outside" in f for f in plan.flags)


def test_a_note_referenced_only_from_deleted_text_goes_with_what_only_it_used(marks):
    plan, out = delete(marks, {"text_regex": "^H\\. A footnote"}, False)
    assert [f.get(W + "id") for f in xml(out, fx.FOOTNOTES)] == ["-1", "0", "1", "3"]
    assert plan.members_removed == [fx.FOOTNOTE_IMAGE] and fx.FOOTNOTE_IMAGE not in names(out)
    rels = xml(out, "word/_rels/footnotes.xml.rels")
    assert [r.get("Id") for r in rels] == []                     # its link and its picture's relationships went
    plan, out = delete(marks, {"text_regex": "^I\\. An endnote"}, False)
    assert [f.get(W + "id") for f in xml(out, fx.ENDNOTES)] == ["-1", "0"]
    plan, out = delete(marks, {"text_regex": "^G\\. A footnote"}, True)
    assert len(list(xml(out, fx.FOOTNOTES))) == 5 and any("Word removes their notes" in f for f in plan.flags)


def test_a_picture_and_a_link_in_deleted_text_leave_the_package(marks):
    plan, out = delete(marks, {"text_regex": "^K\\. Picture"}, False)
    assert plan.members_removed == ["word/media/image1.png"]
    before = {r.get("Id") for r in xml(marks, "word/_rels/document.xml.rels")}
    after = {r.get("Id") for r in xml(out, "word/_rels/document.xml.rels")}
    assert len(before - after) == 1
    plan, out = delete(marks, {"text_regex": "^L\\. Link"}, False)
    assert not [r for r in xml(out, "word/_rels/document.xml.rels") if r.get("Target") == "https://nfpa.org/"]
    assert plan.members_removed == []


def test_deleting_in_the_headers_keeps_a_paragraph_and_drops_what_only_they_used(sections):
    plan, out = delete(sections, "all", False, scope=["headers"])
    pkg = DocxPackage(out)
    for part in pkg.parts_by_kind()["headers"]:
        assert [text_of(p) for p in pkg.xml(part).iter(W + "p")] == [""]       # each header keeps one, emptied
    assert plan.members_removed == ["word/media/image1.png"]
    assert list(pkg.xml("word/_rels/header1.xml.rels")) == []               # the picture's relationship went
    assert body_texts(out) == body_texts(sections)


def test_deleting_in_the_notes_never_touches_a_separator(marks):
    plan, out = delete(marks, "all", False, scope=["footnotes"])
    notes = list(xml(out, fx.FOOTNOTES))
    assert [text_of(p) for e in notes[:2] for p in e.iter(W + "p")] == ["", ""]
    assert [e.find(f"{W}p/{W}r/{W}separator") is not None for e in notes[:1]] == [True]
    assert [len(e.findall(W + "p")) for e in notes[2:]] == [1, 1, 1]
    assert plan.members_removed == [fx.FOOTNOTE_IMAGE]


# ---- the whole body -----------------------------------------------------------------------------------------------

@TRACKED
def test_a_spec_that_wipes_a_multi_section_body_passes(wipe, tracked):
    if tracked:
        plan, out = delete(wipe, "all", True)
        assert len(DocxPackage(out).sections()) == 3 and plan.rev_delta["del_mark"] > 0
        return
    plan, out = delete(wipe, "all", False)
    assert body_texts(out) == [""] and len(DocxPackage(out).sections()) == 1
    assert xml(out, fx.COMMENTS).findall(W + "comment") == []
    assert [f.get(W + "id") for f in xml(out, fx.FOOTNOTES)] == ["-1", "0"]
    assert {"word/header1.xml", "word/header2.xml", "word/footer1.xml"} <= set(plan.members_removed)


def _spec(*ops, mode="untracked"):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


def test_wiping_a_document_composes_the_removals_into_ordered_steps(wipe):
    """No "nuke" op: the body, the headers and footers, the comments and the footnotes in one request, each step planned
    on the one before it and verified on its own (rule 5)."""
    spec = _spec({"op": OP, "select": {"paragraphs": "all"}, "params": {"tracked": False}},
                 {"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}},
                 {"op": "remove_comments", "params": {}},
                 {"op": "remove_footnotes", "params": {"tracked": False}})
    pkg = DocxPackage(wipe)
    assert len(stage_cuts(pkg, spec)) >= 2
    chain = split_into_stages(pkg, spec)
    current = wipe
    for plan, frags in plan_chain(pkg, chain):
        out = apply_spec(DocxPackage(current), frags).output_bytes() if frags else current
        rec = run_suite(current, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()
        current = out
    assert body_texts(current) == [""]
    assert not [n for n in names(current) if "header" in n or "footer" in n]


# ---- wrong outputs ------------------------------------------------------------------------------------------------

def test_wrong_untracked_outputs_are_caught(tables, sections, marks):
    plan, out = delete(tables, TABLE_A, False)

    def keep_a_row(root):                          # an empty row of the deleted table left behind
        body = root.find(W + "body")
        tbl = etree.SubElement(body, W + "tbl")
        body.remove(tbl)
        by_text(root, "PART 1").addnext(tbl)
    caught(tables, plan, out, MAIN, keep_a_row, "is not the source without exactly what the deletion removes")
    plan, out = delete(tables, {"text_regex": "^Between"}, False)
    caught(tables, plan, out, MAIN, lambda root: root.find(W + "body").remove(
        next(p for p in root.iter(W + "p") if p.getprevious() is not None and p.getprevious().tag == W + "tbl"
             and p.getnext() is not None and p.getnext().tag == W + "tbl")))
    plan, out = delete(marks, {"text_regex": "^E\\. A bookmark"}, False)

    def misplace(root):                            # the moved start put at the end of the body instead
        start = next(b for b in root.find(W + "body") if b.tag == W + "bookmarkStart")
        root.find(W + "body").remove(start)
        by_text(root, "END OF SECTION").append(start)
    caught(marks, plan, out, MAIN, misplace)
    plan, out = delete(marks, {"text_regex": "^A\\. Comment"}, False)
    caught(marks, plan, out, fx.COMMENTS, lambda root: root.remove(root.findall(W + "comment")[-1]))
    caught(marks, plan, out, "word/commentsExtended.xml",
           lambda root: root.append(etree.Element("{%s}commentEx" % fx.W15)), "commentsExtended")
    plan, out = delete(marks, {"text_regex": "^G\\. A footnote"}, False)
    caught(marks, plan, out, fx.FOOTNOTES, lambda root: root.remove(root[-1]))


def test_wrong_tracked_outputs_are_caught(tables, fields, marks):
    plan, out = delete(tables, TABLE_A, True)
    caught(tables, plan, out, MAIN, lambda root: next(root.iter(W + "trPr")).remove(
        next(root.iter(W + "trPr")).find(W + "del")), "w:trPr holds 0 w:del")

    def second_row_del(root):
        trpr = next(root.iter(W + "trPr"))
        extra = etree.SubElement(trpr, W + "del")
        for k, v in trpr.find(W + "del").attrib.items():
            extra.set(k, v)
        extra.set(W + "id", "9999")
    caught(tables, plan, out, MAIN, second_row_del, "w:trPr holds 2 w:del")
    plan, out = delete(fields, {"text_regex": "^A\\. Page"}, True)
    caught(fields, plan, out, MAIN, lambda root: setattr(next(root.iter(W + "delInstrText")), "tag", W + "instrText"),
           "not exactly the tracked deletion")

    def one_wrapper_per_run(root):                 # the runs regrouped: the item text is the same, the wrappers are not
        dl = by_text(root, "A. Page").find(W + "del")
        at = dl
        for k, r in enumerate(list(dl)[1:]):
            d2 = etree.Element(W + "del", dict(dl.attrib))
            d2.set(W + "id", str(9000 + k))
            at.addnext(d2)
            d2.append(r)
            at = d2
    caught(fields, plan, out, MAIN, one_wrapper_per_run)
    plan, out = delete(marks, {"text_regex": "^L\\. Link"}, True)
    caught(marks, plan, out, MAIN, lambda root: by_text(root, "L. Link").find(f"{W}pPr/{W}rPr").remove(
        by_text(root, "L. Link").find(f"{W}pPr/{W}rPr/{W}del")), "carries 0 new w:del")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def _shared(src, monkeypatch, paragraphs, tracked, patches, needle=None):
    for target, name, value in patches:
        monkeypatch.setattr(target, name, value)
    plan, out, rec = run_ops(src, [(OP, {"paragraphs": paragraphs}, {"tracked": tracked, "author": AUTHOR})])
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)


def test_a_shared_keep_rule_that_keeps_nothing_is_caught(tables, monkeypatch):
    _shared(tables, monkeypatch, {"text_regex": "^Between"}, False, [(deletion, "keep_rules", lambda *a: [])],
            "deletes")


def test_a_shared_table_rule_that_never_deletes_tables_is_caught(tables, monkeypatch):
    real = deletion.choose

    def no_tables(part, root, selected, tracked):
        ch = real(part, root, selected, tracked)
        ch.deleted = [p for p in deletion.paragraphs(root) if p in set(ch.deleted) | set(ch.in_tables)]
        ch.tables, ch.in_tables = [], []
        return ch
    _shared(tables, monkeypatch, TABLE_A, False, [(dp, "choose", no_tables)], "deletes")


def test_a_shared_grouping_of_one_wrapper_per_run_is_caught(fields, monkeypatch):
    per_run = lambda runs: [[r] for r in runs]
    _shared(fields, monkeypatch, {"text_regex": "^A\\. Page"}, True,
            [(deletion, "run_groups", per_run), (dp, "run_groups", per_run)], "not exactly the tracked deletion")


def test_a_shared_walk_that_skips_runs_in_links_is_caught(marks, monkeypatch):
    real = deletion.tracked_runs
    skip = lambda p, where: [r for r in real(p, where) if r.getparent().tag != W + "hyperlink"]
    _shared(marks, monkeypatch, {"text_regex": "^L\\. Link"}, True, [(dp, "tracked_runs", skip)],
            "not exactly the tracked deletion")


@pytest.mark.parametrize("step", ["_comments", "_notes", "_range_marks", "_sections"])
def test_a_shared_cascade_that_skips_a_step_is_caught(marks, sections, monkeypatch, step):
    src, sel = {"_comments": (marks, {"text_regex": "^A\\. Comment"}), "_notes": (marks, {"text_regex": "^G\\. A foot"}),
                "_range_marks": (marks, {"text_regex": "^E\\. A bookmark"}),
                "_sections": (sections, {"text_regex": "^End of the cover"})}[step]
    _shared(src, monkeypatch, sel, False, [(deletion.Removal, step, lambda self, *a: None)])


def test_a_shared_relationship_step_that_keeps_media_is_caught(marks, monkeypatch):
    real = deletion.Removal._relationships

    def keep_media(self):
        real(self)
        self.media, self.members = [], [m for m in self.members if not m.startswith("word/media/")]
        self.rels = {}
    _shared(marks, monkeypatch, {"text_regex": "^K\\. Picture"}, False,
            [(deletion.Removal, "_relationships", keep_media)], "the removal takes")


# ---- both sides decide alike ---------------------------------------------------------------------------------------

SELECTIONS = [(fx.build_tables, TABLE_A), (fx.build_tables, {"text_regex": "^Between|^Rated"}),
              (fx.build_tables, "all"), (fx.build_fields, {"text_regex": "SUMMARY|SUBMITTALS|QUALITY|^A\\. "}),
              (fx.build_sections, "all"), (fx.build_marks, {"text_regex": "^[A-H]\\. "}), (fx.build_wipe, "all")]


@TRACKED
@pytest.mark.parametrize("builder,paragraphs", SELECTIONS)
def test_transform_and_verifier_choose_alike(builder, paragraphs, tracked):
    from spec_surgeon.ops.selectors import StyleSheet, paragraph_selected
    from spec_surgeon.ooxml import block_full_text
    from spec_surgeon.verify.props import PartView, Styles, para_matches
    data = builder()
    pkg, vpkg = DocxPackage(data), VPackage(data)
    root, vroot = pkg.xml(MAIN), vpkg.xml(MAIN)
    pars, vview = deletion.paragraphs(root), PartView(vroot)
    picked = [p for i, p in enumerate(pars) if paragraph_selected(paragraphs, p, i, block_full_text(p), StyleSheet(pkg),
                                                                  all_pars=pars)]
    vstyles = Styles(vpkg.xml("word/styles.xml"))
    vpicked = [p for i, p in enumerate(vview.pars) if para_matches(paragraphs, p, i, vview, vstyles)]
    try:
        ch = deletion.choose(MAIN, root, picked, tracked)
    except JobException as e:
        with pytest.raises(vdel.VRefuse) as v:
            vdel.v_choose(vroot, vpicked, tracked)
        assert v.value.code == e.code
        return
    dec = vdel.v_choose(vroot, vpicked, tracked)
    at, vat = {p: i for i, p in enumerate(pars)}, {p: i for i, p in enumerate(vview.pars)}
    assert [at[p] for p in ch.deleted] == [vat[p] for p in dec["deleted"]]
    assert [at[p] for p in ch.kept] == [vat[p] for p in dec["kept"]]
    assert [at[p] for p in ch.in_tables] == [vat[p] for p in dec["in_tables"]]


# ---- refusals -----------------------------------------------------------------------------------------------------

def test_text_boxes_are_refused():
    data = fx.build_marks(textbox=True)
    assert refused(data, {"text_regex": "^N\\. Text box"}).code == "textbox"
    assert refused(data, "all").code == "textbox"


def test_pending_revisions_go_untracked_and_a_tracked_deletion_goes_over_them():
    src = build("21 05 00", "X")                                  # C. holds Jeremy's insertion, D. Jack's deletion
    delete(src, {"text_regex": "^C\\.\\tSprinklers"}, True)     # as Word does (tests/test_deletion_over_revisions.py)
    plan, out = delete(src, {"text_regex": "^C\\.\\tSprinklers"}, False)
    assert plan.rev_delta["ins"] == -1 and any("pending tracked changes" in f for f in plan.flags)


def test_two_tables_left_touching_with_no_paragraph_between_are_refused(tables):
    def adjacent(root):                            # two more tables right after the second, nothing between the three
        second = list(root.iter(W + "tbl"))[1]
        at = second
        for word in ("Dup ", "Keep "):
            copy_ = etree.fromstring(etree.tostring(second))
            for t in copy_.iter(W + "t"):
                t.text = word + (t.text or "")
            at.addnext(copy_)
            at = copy_
    data = mutate(tables, MAIN, adjacent)
    assert refused(data, {"text_regex": "^Dup "}).code == "tables-join"
    assert refused(data, {"text_regex": "^Dup "}, True).code == "tables-join"


def test_an_embedded_object_in_untracked_deletion_is_refused(marks):
    def as_chart(root):                            # the body picture's relationship read as a chart's
        for r in root:
            if r.get("Target", "").startswith("media/image"):
                r.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart")
    data = mutate(marks, "word/_rels/document.xml.rels", as_chart)
    assert refused(data, {"text_regex": "^K\\. Picture"}).code == "embedded-object"
    delete(data, {"text_regex": "^K\\. Picture"}, True)          # tracked: the object stays until the deletion is accepted


def test_wrapped_rows_refuse_a_tracked_table_deletion(tables):
    def wrap(root):
        first = next(root.iter(W + "tbl"))
        row = [c for c in first if c.tag == W + "tr"][1]
        sdt = etree.SubElement(first, W + "sdt")
        content = etree.SubElement(sdt, W + "sdtContent")
        first.remove(sdt)
        row.addprevious(sdt)
        content.append(row)
    data = mutate(tables, MAIN, wrap)
    assert refused(data, TABLE_A, True).code == "table-structure"


def test_an_equation_refuses_a_tracked_deletion_only():
    def math(root):
        etree.SubElement(by_text(root, "A. Body"), "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")
    data = patch_docx(fx.build_sections(), {MAIN: math})
    assert refused(data, {"text_regex": "^A\\. Body"}, True).code == "structure"
    delete(data, {"text_regex": "^A\\. Body"}, False)


@pytest.mark.parametrize("select,params", [
    ({}, {}), ({"paragraphs": {}}, {}), ({"paragraphs": "all", "runs": "all"}, {}),
    ({"paragraphs": {"colour": "red"}}, {}), ({"paragraphs": "all"}, {"tracked": "yes"}),
    ({"paragraphs": "all"}, {"date_convention": "local"}), ({"paragraphs": "all", "scope": []}, {}),
])
def test_parameters_are_checked(tables, select, params):
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(tables), select, params)
    assert e.value.code == "bad-params"


def test_nothing_chosen_is_not_found_unless_allowed(tables):
    with pytest.raises(JobException) as e:
        get_op(OP).plan(DocxPackage(tables), {"paragraphs": {"text_regex": "^No such"}}, {})
    assert e.value.code == "not-found"
    plan, out, rec = run_ops(tables, [(OP, {"paragraphs": {"text_regex": "^No such"}}, {"allow_missing": True})])
    assert rec.passed and plan.property_assertions[0]["expected"] == {}


def test_every_refusal_code_of_the_deletion_ops_is_explained():
    codes = set()
    for name in ("deletion", "delete_paragraphs", "remove_footnotes"):
        path = Path(deletion.__file__).with_name(f"{name}.py")
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant), (name, n.lineno)
                codes.add(n.args[1].value)
    codes.discard("internal")
    assert {"textbox", "tables-join", "structure", "field-result", "table-structure", "field-crossing",
            "comment-crossing", "embedded-object", "existing-revisions", "custom-mark", "bad-params",
            "not-found", "comment-last-paragraph"} == codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


# ---- the batch-6 review: pictures, the last section, printer settings, comment threads ------------------------------

OBJECTS = (W + "drawing", W + "pict", W + "object", W + "fldSimple", W + "fldChar", W + "txbxContent",
           "{http://schemas.openxmlformats.org/markup-compatibility/2006}AlternateContent")
BEFORE_SIGNED = {"between_paragraphs": {"start_regex": "^SECTION", "end_regex": "^Signed"}}


@pytest.fixture(scope="module")
def pictures() -> bytes:
    return fx.build_pictures()


def test_is_empty_never_chooses_a_paragraph_holding_a_picture_object_or_field_on_either_side(pictures):
    from spec_surgeon.ooxml import block_full_text
    from spec_surgeon.ops.selectors import StyleSheet, paragraph_selected
    from spec_surgeon.verify.props import PartView, Styles, para_matches
    pkg, vpkg = DocxPackage(pictures), VPackage(pictures)
    pars, view = deletion.paragraphs(pkg.xml(MAIN)), PartView(vpkg.xml(MAIN))
    sheet, styles = StyleSheet(pkg), Styles(vpkg.xml("word/styles.xml"))
    for pred in ({"is_empty": True}, {"is_empty": False}, {"is_empty": True, "in_table": True}):
        ours = [i for i, p in enumerate(pars) if paragraph_selected(pred, p, i, block_full_text(p), sheet,
                                                                    all_pars=pars)]
        assert ours == [i for i, p in enumerate(view.pars) if para_matches(pred, p, i, view, styles)], pred
    empty = [p for i, p in enumerate(pars) if paragraph_selected({"is_empty": True}, p, i, block_full_text(p), sheet,
                                                                 all_pars=pars)]
    # the two blank lines and the blank cell; the logo, the cell's picture, the text box, the field and the shape are
    # paragraphs without text that are not empty
    assert len(empty) == 3 and not any(next(p.iter(*OBJECTS), None) is not None for p in empty)
    assert sum(1 for p in pars if block_full_text(p) == "") == 8


@TRACKED
def test_deleting_empty_paragraphs_keeps_every_picture_text_box_field_and_table(pictures, tracked):
    plan, out = delete(pictures, {"is_empty": True}, tracked)
    src, root = xml(pictures), xml(out)
    for tag in OBJECTS + (W + "tbl",):
        assert len(list(root.iter(tag))) == len(list(src.iter(tag))), tag
    assert plan.members_removed == [] and not any("with the deleted paragraphs" in f or "deleted with the paragraphs"
                                                  in f for f in plan.flags)
    expected = plan.property_assertions[0]["expected"][MAIN]
    assert (len(expected["delete"]), len(expected["keep"]), expected["tables"]) == (2, 1, [])   # the blank cell stays


@TRACKED
def test_the_pictures_and_tables_a_deletion_takes_are_flagged_in_plain_words(pictures, tracked):
    plan, out = delete(pictures, BEFORE_SIGNED, tracked)
    if tracked:
        assert ("2 pictures and 1 table are deleted with the paragraphs (tracked: they go when the deletion is "
                "accepted)") in plan.flags
        return
    assert "2 pictures and 1 table go with the deleted paragraphs" in plan.flags
    assert [m for m in plan.members_removed if m.startswith("word/media/")] == ["word/media/image1.png",
                                                                                 "word/media/image2.png"]
    plan, out = delete(pictures, {"between_paragraphs": {"start_regex": "^SECTION", "end_regex": "^Figure"}}, False)
    assert "1 picture goes with the deleted paragraphs" in plan.flags


def test_an_emptiness_shared_by_plan_and_apply_that_ignores_pictures_is_caught(pictures, monkeypatch):
    """A selector reading a paragraph with a picture as empty (text boxes still seen): the plan deletes the logo and the
    signature table and apply does — the verifier's own selection does not."""
    from spec_surgeon.ops import selectors
    monkeypatch.setattr(selectors, "holds_object", lambda p: next(p.iter(W + "txbxContent"), None) is not None)
    _shared(pictures, monkeypatch, {"is_empty": True}, False, [], "paragraph(s) selected here")


@TRACKED
def test_the_last_section_keeps_a_paragraph_and_its_blank_page_is_flagged(sections, tracked):
    """Deleting all of the last section (the appendix: after the body's break) keeps its last paragraph, emptied —
    not the break's paragraph alone at the end of the body, which Word and LibreOffice show as a blank page."""
    plan, out = delete(sections, {"text_regex": "^APPENDIX A|^Attached"}, tracked)
    expected = plan.property_assertions[0]["expected"][MAIN]
    assert len(expected["delete"]) == 1 and len(expected["keep"]) == 1
    assert any(f.startswith("the last section is now empty and shows a blank page") and
               f.endswith("remove the section break before it to drop the page") for f in plan.flags)
    body = xml(out).find(W + "body")
    if not tracked:
        assert [c.tag for c in body][-3:] == [W + "p", W + "p", W + "sectPr"]
        assert body[-3].find(f"{W}pPr/{W}sectPr") is not None and text_of(body[-2]) == ""
    plan, _ = delete(sections, {"text_regex": "^Attached"}, False)               # the heading stays: no blank page
    assert not any("the last section" in f for f in plan.flags)
    plan, out = delete(sections, "all", False)                                  # every break goes: one section left
    assert body_texts(out) == [""] and not any("the last section" in f for f in plan.flags)


def test_a_last_section_left_without_a_paragraph_is_caught(sections, monkeypatch):
    plan, out = delete(sections, {"text_regex": "^APPENDIX A|^Attached"}, False)
    caught(sections, plan, out, MAIN, lambda root: root.find(W + "body").remove(root.find(W + "body")[-2]),
           "is not the source without exactly what the deletion removes")
    _shared(sections, monkeypatch, {"text_regex": "^APPENDIX A|^Attached"}, False,
            [(deletion, "last_section_keep", lambda *a: [])], "deletes")


def test_printer_settings_only_a_removed_break_used_leave_the_package(sections):
    """Word on Windows names a printer settings part from section breaks: an untracked deletion of a break takes it as
    it takes the break's headers (it was refused as an embedded object)."""
    data = fx.with_printer_settings(sections, (0,))
    plan, out = delete(data, {"text_regex": "^End of the cover"}, False)
    assert fx.PRINTER in plan.members_removed and fx.PRINTER not in names(out)
    assert "rIdPS1" in plan.property_assertions[0]["expected_removal"]["rels"][MAIN]
    assert not [r for r in xml(out, "word/_rels/document.xml.rels") if r.get("Id") == "rIdPS1"]
    assert b"printerSettings1.bin" not in DocxPackage(out).read("[Content_Types].xml")
    assert any(f.startswith("printer settings part(s)") for f in plan.flags)
    plan, out = delete(fx.with_printer_settings(sections, (0, 1)), {"text_regex": "^End of the cover"}, False)
    assert fx.PRINTER not in plan.members_removed and fx.PRINTER in names(out)   # the body's break still names it
    plan, out = delete(data, {"text_regex": "^End of the cover"}, True)           # tracked: the break stays
    assert plan.members_removed == [] and fx.PRINTER in names(out)


def test_a_printer_settings_part_left_behind_is_caught(sections, monkeypatch):
    data = fx.with_printer_settings(sections, (0,))
    plan, out = delete(data, {"text_regex": "^End of the cover"}, False)
    rec = run_suite(data, add_parts(out, {fx.PRINTER: b"\x00"}), plan.to_dict(), "src.docx", "out.docx")
    assert "V1" in failed(rec), rec.to_markdown()                                # orphaned, and predicted gone
    real = deletion.Removal._sections

    def keep_printer(self):                       # the relationship goes, the part it targeted stays
        real(self)
        self.printers = []
    _shared(data, monkeypatch, {"text_regex": "^End of the cover"}, False,
            [(deletion.Removal, "_sections", keep_printer)], "the removal takes")


def _plan_comments(data: bytes, paragraphs, tracked: bool, scope=("comments",)):
    return get_op(OP).plan(DocxPackage(data), {"scope": list(scope), "paragraphs": paragraphs},
                           {"tracked": tracked, "author": AUTHOR})


def test_a_threaded_comments_last_paragraph_is_refused_unless_the_comment_goes(marks):
    """commentsExtended names a comment's thread (its replies, its resolved state) by the comment's last paragraph:
    deleting that paragraph alone would leave the thread and its reply pointing at nothing."""
    data = fx.with_last_comment_paragraph(marks)
    with pytest.raises(JobException) as e:
        _plan_comments(data, {"text_regex": "TBD"}, False)
    assert e.value.code == "comment-last-paragraph" and "comment 0" in e.value.reason
    plan, out = delete(data, {"text_regex": "TBD"}, True, scope=["comments"])    # tracked: the mark stays (rule 2)
    pids = {p.get("{%s}paraId" % fx.W14) for p in xml(out, fx.COMMENTS).iter(W + "p")}
    assert "1A000001" in pids
    plan, out = delete(data, {"text_regex": "^Check this"}, False, scope=["comments"])   # its first paragraph: fine
    pids = {p.get("{%s}paraId" % fx.W14) for p in xml(out, fx.COMMENTS).iter(W + "p")}
    assert "3C000009" not in pids and all(e.get("{%s}paraId" % fx.W15) in pids
                                          for e in xml(out, "word/commentsExtended.xml"))
    # the comment's anchored text and its paragraph together: the whole comment goes, its reply and entries with it
    plan, out = delete(data, {"text_regex": "^A\\. Comment|TBD"}, False, scope=["document", "comments"])
    assert [c.get(W + "id") for c in xml(out, fx.COMMENTS).findall(W + "comment")] == ["2"]


def test_the_verifier_refuses_a_threaded_comments_last_paragraph_deleted(marks, monkeypatch):
    data = fx.with_last_comment_paragraph(marks)
    monkeypatch.setattr(dp, "refuse_thread_ends", lambda *a, **k: None)
    plan, out, rec = run_ops(data, [(OP, {"scope": ["comments"], "paragraphs": {"text_regex": "TBD"}},
                                     {"tracked": False, "author": AUTHOR})])
    assert "V10" in failed(rec) and "comment-last-paragraph" in v10(rec) and "the op must refuse" in v10(rec)


# ---- range marks in a paragraph the deletion keeps (2026-10-21 review fix) ------------------------------------------

HELD = "sat in a paragraph that stays, emptied; each stays in that paragraph"
CROSSING = {"cell": ("32", "KeptCell"), "between": ("34", "Span"), "body": ("35", "ToEnd"),
            "section": ("36", "IntoAppendix")}


def marks_of(root, tag: str) -> List[str]:
    return sorted(e.get(W + "id") for e in root.iter(W + tag))


@pytest.mark.parametrize("shape", list(fx.KEPT_SHAPES))
def test_a_kept_paragraph_keeps_the_range_marks_that_run_out_of_it(shape):
    """A paragraph the deletion keeps, emptied (a cell's last, one between two tables, the body's last, the last
    section's), holding one end of a bookmark whose other end stays: that end stays in the paragraph, where it stood —
    it used to be removed with the paragraph's runs, leaving the other end an orphan."""
    src = fx.build_kept_marks(shape)
    plan, out = delete(src, {"text_regex": fx.KEPT_SHAPES[shape]}, False)
    bid, name = CROSSING[shape]
    root = xml(out)
    assert len(plan.property_assertions[0]["expected"][MAIN]["keep"]) == 1
    assert marks_of(root, "bookmarkStart") == marks_of(root, "bookmarkEnd") == [bid]
    held = next(e for e in root.iter(W + "bookmarkStart", W + "bookmarkEnd")
                if e.get(W + "id") == bid and not text_of(e.getparent()))
    assert held.getparent().tag == W + "p" and [c.tag for c in held.getparent() if c.tag != W + "pPr"] == \
        [c.tag for c in held.getparent() if c.tag in (W + "bookmarkStart", W + "bookmarkEnd", W + "permStart")]
    assert any(f.startswith("1 bookmark") and HELD in f for f in plan.flags) or shape == "cell"
    assert not any("ran into the deleted text from outside" in f for f in plan.flags)
    assert not any(repr(name) in f for f in plan.flags)               # it does not go: nothing points at a lost one


def test_a_kept_cell_paragraph_keeps_its_crossing_marks_and_loses_the_ones_wholly_deleted():
    """The cell's last paragraph keeps the start of "KeptCell" (its end in the next cell; the REF field below still
    finds it) and of the permission range; "Inner" (both ends in it) and "Wholly" (from the deleted paragraph above it)
    lie wholly in the deleted text and go."""
    src = fx.build_kept_marks("cell")
    plan, out = delete(src, {"text_regex": fx.KEPT_SHAPES["cell"]}, False)
    root = xml(out)
    kept = next(p for p in root.iter(W + "p") if p.find(W + "bookmarkStart") is not None)
    assert text_of(kept) == "" and [c.tag for c in kept if c.tag != W + "pPr"] == [W + "bookmarkStart", W + "permStart"]
    assert [b.get(W + "name") for b in root.iter(W + "bookmarkStart")] == ["KeptCell"]
    assert marks_of(root, "permStart") == marks_of(root, "permEnd") == ["33"]
    assert any(f.startswith("2 bookmark or range mark(s)") and HELD in f for f in plan.flags)
    assert not any("KeptCell" in f for f in plan.flags)
    assert body_texts(out)[-2:] == ["A. See Q.", "END OF SECTION"]
    assert [t.text for t in root.iter(W + "instrText")] == [" REF KeptCell \\h "]


@TRACKED
def test_a_mark_wholly_inside_the_deletion_still_goes(tracked):
    src = fx.build_kept_marks("cell")
    plan, out = delete(src, {"text_regex": "^Q\\. |^Next cell"}, tracked)   # now KeptCell's end is deleted too
    root = xml(out)
    if tracked:                                                    # tracked: every mark stays until it is accepted
        assert marks_of(root, "bookmarkStart") == marks_of(xml(src), "bookmarkStart")
    else:
        assert marks_of(root, "bookmarkStart") == marks_of(root, "bookmarkEnd") == []
        assert marks_of(root, "permStart") == marks_of(root, "permEnd") == []
        assert any("REF field still points to the bookmark 'KeptCell'" in f for f in plan.flags)
        assert not any(HELD in f for f in plan.flags)


def test_a_kept_paragraphs_crossing_mark_removed_from_the_output_is_caught():
    """Today's output before the fix: the start gone with the cell paragraph's runs, its end left an orphan."""
    src = fx.build_kept_marks("cell")
    plan, out = delete(src, {"text_regex": fx.KEPT_SHAPES["cell"]}, False)

    def orphan(root):
        start = next(b for b in root.iter(W + "bookmarkStart") if b.get(W + "name") == "KeptCell")
        start.getparent().remove(start)
    caught(src, plan, out, MAIN, orphan, "is not the source without exactly what the deletion removes")


@pytest.mark.parametrize("shape", list(fx.KEPT_SHAPES))
def test_a_shared_cascade_that_drops_a_kept_paragraphs_crossing_mark_is_caught(shape, monkeypatch):
    """The old rule shared by plan and apply: the mark, removed as the kept paragraph's own child, stays removed."""
    real = deletion.Removal._range_marks

    def drop_held(self, part):
        real(self, part)
        for e in self.held.pop(part, []):
            self._tops[part].add(e)
    _shared(fx.build_kept_marks(shape), monkeypatch, {"text_regex": fx.KEPT_SHAPES[shape]}, False,
            [(deletion.Removal, "_range_marks", drop_held)],
            "is not the source without exactly what the deletion removes")


# ---- touches and composition ---------------------------------------------------------------------------------------

def test_untracked_claims_the_part_and_tracked_composes_beside_other_paragraphs(tables):
    pkg = DocxPackage(tables)
    font = get_op("set_font").plan(pkg, {"paragraphs": {"text_regex": "^END OF"}}, {"font": "Arial"})
    tracked = get_op(OP).plan(pkg, {"paragraphs": TABLE_A}, {"tracked": True})
    merge_fragments([tracked, font])                         # different paragraphs: one plan
    untracked = get_op(OP).plan(pkg, {"paragraphs": TABLE_A}, {"tracked": False})
    with pytest.raises(JobException) as e:
        merge_fragments([untracked, get_op("set_font").plan(pkg, {"paragraphs": {"text_regex": "^END OF"}},
                                                             {"font": "Arial"})])
    assert e.value.code == "conflict"
    assert (MAIN, "*", "text") in untracked.touches and (MAIN, "scope", "verify") in tracked.touches
    assert (MAIN, "tr:0:1", "trPr") in tracked.touches


def test_a_tracked_row_deletion_beside_a_row_property_change_conflicts(tables):
    pkg = DocxPackage(tables)
    rows = get_op("set_table_props").plan(pkg, {"tables": {"index_range": [0, 1]}}, {"cant_split": True})
    with pytest.raises(JobException) as e:
        merge_fragments([get_op(OP).plan(pkg, {"paragraphs": TABLE_A}, {"tracked": True}), rows])
    assert e.value.code == "conflict"


def test_the_composed_steps_keep_the_users_order(wipe):
    spec = _spec({"op": "remove_comments", "params": {}},
                 {"op": OP, "select": {"paragraphs": "all"}, "params": {"tracked": False}})
    cuts = stage_cuts(DocxPackage(wipe), spec)
    assert cuts == [1, 1]


# ---- catalog, pipeline, render -----------------------------------------------------------------------------------

def test_catalog_and_schema(tables):
    from spec_surgeon.editspec import SpecError, validate_spec
    cat = op_catalog()
    for op in (OP, "remove_footnotes"):
        assert op in cat["ops"] and COMMON_ASSUMPTIONS[op]
    assert any("'all'" in line for line in COMMON_ASSUMPTIONS[OP])
    validate_spec(_spec({"op": OP, "select": {"paragraphs": "all", "scope": ["document"]}, "params": {"tracked": False}}))
    with pytest.raises(SpecError):
        validate_spec(_spec({"op": OP, "select": {"paragraphs": "all", "scope": ["everything"]}, "params": {}}))


def test_a_spec_runs_through_the_batch_pipeline(wipe, tmp_path):
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 – WIPE.docx"
    path.write_bytes(wipe)
    for k, tracked in enumerate((False, True)):
        spec = _spec({"op": OP, "select": {"paragraphs": "all"}, "params": {"tracked": tracked}},
                     mode="content" if tracked else "untracked")
        batch = run_batch([str(path)], "edit-spec", {}, tmp_path / f"out{k}", workers=1, renderer="none",
                          do_render=False, spec=spec)
        f = batch.files[0]
        assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.render
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(wipe, tables, tmp_path):
    from spec_surgeon.render import libreoffice
    outs = []
    for k, (data, paragraphs, tracked) in enumerate(((wipe, "all", False), (tables, TABLE_A, True),
                                                     (tables, {"text_regex": "^Between|^Rated"}, False))):
        _, out = delete(data, paragraphs, tracked)
        path = tmp_path / f"out{k}.docx"
        path.write_bytes(out)
        outs.append(path)
    pdfs = libreoffice.render_pdfs(outs, tmp_path / "pdf", workers=1)
    if len(pdfs) != len(outs):
        pytest.skip("LibreOffice did not render here")
    for path in outs:
        pdf = pdfs[path]
        assert pdf.exists() and pdf.stat().st_size > 500 and pdf.read_bytes().startswith(b"%PDF")
