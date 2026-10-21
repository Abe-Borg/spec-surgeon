"""move_paragraphs and V10's ``moved_paragraphs`` kind (built-in batch 6, F9 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §10): end
to end on the moving fixture (forward and backward, with and without tables, in the body, a table cell and a header,
tracked and untracked, already in place); a wrong OUTPUT per rule (the wrong place, a paragraph left behind, formatting
changed in transit, the tracked markup); wrong IMPLEMENTATIONS shared by plan and apply; the two sides' decisions on
every selection; refusals with their codes, and the verifier's own refusals; parameters; plan-time conflicts and
composition (rule 9); the catalog; the batch pipeline and a LibreOffice render."""
from __future__ import annotations

import ast
import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from fx_moving import build_moving
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import apply_spec, plan_chain, plan_spec, split_into_stages, stage_cuts
from spec_surgeon.ooxml import block_full_text
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import move_paragraphs as mp
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import moving as vm
from spec_surgeon.verify.props import PartView, Styles, para_matches
from spec_surgeon.verify.reader import VPackage
from spec_surgeon.verify.textcheck import accept_view, reject_view, view_texts

MAIN = "word/document.xml"
HEADER = "word/header1.xml"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W16DU = "{http://schemas.microsoft.com/office/word/2023/wordml/word16du}dateUtc"


def article(start: str, end: str) -> dict:
    """An article: its heading through the paragraph before the next heading (the 'up to the one before' idiom). The
    bounds read the paragraph's text without its tabs (a w:tab is not text): "1.3⇥SUBMITTALS" reads "1.3SUBMITTALS"."""
    return {"between_paragraphs": {"start_regex": f"^{start}", "end_regex": f"^{end}", "include_bounds": True},
            "text_regex": f"^(?!{end})"}


A12, A13, A14, A21 = article("1\\.2", "1\\.3"), article("1\\.3", "1\\.4"), article("1\\.4", "1\\.5"), \
    article("2\\.1", "2\\.2")
A15 = {"text_regex": "^1\\.5\\t|^A\\.\\tTwo years|^Controlled"}
A22 = {"text_regex": "^2\\.2\\t|^A\\.\\tThreaded"}
SUBMITTALS = ["1.3SUBMITTALS", "Product data for each sprinkler.", "Shop drawings, signed and sealed."]


def to(pattern: str, position: str, **more) -> dict:
    return {"to": {"text_regex": pattern}, "position": position, **more}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_moving()


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def move(data: bytes, select: dict, params: dict):
    return run_ops(data, [("move_paragraphs", select, params)])


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


def texts(data: bytes, part: str = MAIN) -> List[str]:
    return [block_full_text(p) for p in DocxPackage(data).xml(part).iter(W + "p")]


def para(root, text: str):
    hits = [p for p in root.iter(W + "p") if block_full_text(p) == text]
    assert len(hits) == 1, (text, len(hits))
    return hits[0]


def accepted(data: bytes, part: str = MAIN) -> List[str]:
    return view_texts(accept_view(VPackage(data).xml(part)), "accept")


def rejected(data: bytes, part: str = MAIN) -> List[str]:
    return view_texts(reject_view(VPackage(data).xml(part)), "reject")


def _caught(src, plan, out, part, fn, needle=None):
    bad = mutate(out, part, fn)
    rec = run_suite(src, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_an_article_moves_forward_past_a_table_untracked_exactly_as_it_is(src):
    """1.3 SUBMITTALS (its heading, two numbered paragraphs — one bold run, a direct indent — and the heading's
    bookmark) goes before 1.5: after 1.4 and its table. Every moved element is byte-identical; nothing else changes."""
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))
    assert rec.passed, rec.to_markdown()
    t = texts(out)
    at = t.index("B.Welding: AWS D10.9.")
    assert t[at + 1:at + 5] == SUBMITTALS + ["1.5WARRANTY"]
    assert t[t.index("A.NFPA 13, 2025 edition.") + 1] == "1.4QUALITY ASSURANCE"
    assert sorted(t) == sorted(texts(src))
    s_root, o_root = DocxPackage(src).xml(MAIN), DocxPackage(out).xml(MAIN)
    for text in SUBMITTALS:
        assert etree.tostring(para(s_root, text)) == etree.tostring(para(o_root, text))
    a = plan.property_assertions[0]
    assert a["kind"] == "moved_paragraphs" and a["moves"] == {
        MAIN: {"first": 6, "paragraphs": 3, "tables": 0, "anchor": 16, "noop": False}}
    assert plan.mode == "untracked" and not any(plan.rev_delta.values()) and plan.geometry_reference == "none"
    # deleted where they were (blocks 6–8 of the source), inserted where they go (blocks 13–15 of the output)
    assert [(d.block_index, d.before is None) for d in plan.diffs] == [(6, False), (13, True), (7, False), (14, True),
                                                                        (8, False), (15, True)]
    assert any("numbered automatically" in f for f in plan.flags) and any("UNTRACKED move" in f for f in plan.flags)
    assert not any("section" in f for f in plan.flags)
    assert rec.get("V10").data["residual_parts"] == []                     # the kind compared the part whole


def test_an_article_moves_back_across_a_section_break_untracked(src):
    plan, out, rec = move(src, {"paragraphs": A21}, to("^A\\.\\tSection includes", "after", tracked=False))
    assert rec.passed, rec.to_markdown()
    t = texts(out)
    assert t[3:7] == ["A.Section includes wet-pipe sprinkler systems.", "2.1PIPE", "A.Schedule 40 black steel.",
                      "1.2REFERENCES"]
    assert t[t.index("PART 2 - PRODUCTS") + 1] == "2.2FITTINGS"
    assert any("from section 2 to section 1" in f for f in plan.flags)


def test_a_range_holding_a_table_moves_whole_untracked(src):
    plan, out, rec = move(src, {"paragraphs": A14}, to("^A\\.\\tTwo years", "after", tracked=False))
    assert rec.passed, rec.to_markdown()
    body = DocxPackage(out).xml(MAIN).find(W + "body")
    kinds = [(etree.QName(c).localname, block_full_text(c) if c.tag == W + "p" else "") for c in body]
    at = kinds.index(("p", "A.Two years from Substantial Completion."))
    assert kinds[at + 1:at + 6] == [("p", "1.4QUALITY ASSURANCE"), ("p", "A.Installer qualifications: licensed."),
                                    ("tbl", ""), ("p", "B.Welding: AWS D10.9."), ("p", "")]   # then the section break
    s_tbl = DocxPackage(src).xml(MAIN).find(f"{W}body/{W}tbl")
    assert etree.tostring(s_tbl) == etree.tostring(body.find(W + "tbl"))
    assert plan.property_assertions[0]["moves"][MAIN]["tables"] == 1
    assert len(plan.diffs) == 2 * 7                                       # three body paragraphs and four cell ones


def test_an_article_moves_forward_tracked_as_a_deletion_and_an_insertion(src):
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", author="Spec Team"))
    assert rec.passed, rec.to_markdown()
    assert plan.mode == "content"
    assert plan.rev_delta == {"ins": 3, "del": 3, "del_mark": 3, "ins_mark": 3}
    root = DocxPackage(out).xml(MAIN)
    both = [p for p in root.iter(W + "p") if block_full_text(p) in SUBMITTALS]
    assert len(both) == 6                                                 # the originals and their copies
    originals, copies = both[:3], both[3:]
    for p in originals:
        assert p.find(f"{W}pPr/{W}rPr/{W}del").get(W + "author") == "Spec Team"
        assert p.find(W + "r") is None and p.find(f"{W}del/{W}r/{W}delText") is not None
        assert p.find(W + "bookmarkStart") is None                        # bookmarks travel with the copy
    for p in copies:
        assert p.find(f"{W}pPr/{W}rPr/{W}ins").get(W + "author") == "Spec Team"
        assert p.find(W + "r") is None and p.find(f"{W}ins/{W}r/{W}t") is not None
        assert p.get(W14 + "paraId") is None
    shop = copies[2]
    assert [etree.QName(c).localname for c in shop] == ["pPr", "ins"]     # one unbroken run sequence, one w:ins
    assert shop.find(f"{W}ins/{W}r[2]/{W}rPr/{W}b") is not None            # the bold run keeps its formatting
    assert shop.find(f"{W}pPr/{W}ind") is not None
    assert copies[0].find(W + "bookmarkStart").get(W + "name") == "_Ref_Submittals"
    assert [etree.QName(c).localname for c in copies[0]] == ["pPr", "bookmarkStart", "ins", "bookmarkEnd"]
    # accepting every change is the untracked move; rejecting every change is the source
    _, untracked, _ = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))
    assert accepted(out) == texts(untracked)
    assert rejected(out) == rejected(src)
    assert any("bookmarks among the moved paragraphs" in f for f in plan.flags)


def test_an_article_moves_back_tracked(src):
    plan, out, rec = move(src, {"paragraphs": A21}, to("^1\\.2\\t", "before"))
    assert rec.passed, rec.to_markdown()
    _, untracked, _ = move(src, {"paragraphs": A21}, to("^1\\.2\\t", "before", tracked=False))
    assert accepted(out) == texts(untracked)
    assert [(d.block_index, d.after, d.accepted_view) for d in plan.diffs] == [
        (4, "2.1PIPE", "2.1PIPE"),
        (5, "A.Schedule 40 black steel.", "A.Schedule 40 black steel.")]
    assert plan.accepted_views == [{"part": MAIN, "block_index": 20, "accepted_view": ""},
                                   {"part": MAIN, "block_index": 21, "accepted_view": ""}]


@pytest.mark.parametrize("tracked", [False, True])
def test_paragraphs_move_inside_a_table_cell(src, tracked):
    plan, out, rec = move(src, {"paragraphs": {"text_regex": "^Cell third$"}},
                          to("^Cell first$", "before", tracked=tracked))
    assert rec.passed, rec.to_markdown()
    cell = para(DocxPackage(out).xml(MAIN), "Cell first").getparent()
    shown = accepted(out) if tracked else texts(out)
    assert shown[shown.index("Cell third"):shown.index("Cell third") + 3] == ["Cell third", "Cell first", "Cell second"]
    assert cell.tag == W + "tc" and cell[-1].tag == W + "p"                # the cell still ends with a paragraph


@pytest.mark.parametrize("tracked", [False, True])
def test_paragraphs_move_to_the_very_start_and_the_very_end(src, tracked):
    plan, out, rec = move(src, {"paragraphs": A13}, to("^SECTION 21", "before", tracked=tracked))
    assert rec.passed, rec.to_markdown()
    shown = accepted(out) if tracked else texts(out)
    assert shown[:4] == SUBMITTALS + ["SECTION 21 13 13 - WET-PIPE SPRINKLER SYSTEMS"]
    plan, out, rec = move(src, {"paragraphs": A13}, to("^END OF SECTION$", "after", tracked=tracked))
    assert rec.passed, rec.to_markdown()
    shown = accepted(out) if tracked else texts(out)
    assert shown[-4:] == ["END OF SECTION"] + SUBMITTALS
    body = DocxPackage(out).xml(MAIN).find(W + "body")
    assert body[-1].tag == W + "sectPr" and body[-2].tag == W + "p"       # the body's last section stays last
    assert any("land at the end of their body" in f for f in plan.flags) == tracked


@pytest.mark.parametrize("tracked", [False, True])
def test_after_an_anchor_holding_a_text_box_the_paragraphs_land_after_its_box(tracked):
    """The text box's two paragraphs (both copies of its text) follow their paragraph in document order: the moved
    paragraphs' indices come after them."""
    data = build_moving(textbox=True)
    plan, out, rec = move(data, {"paragraphs": A13}, to("^A\\.\\tSchedule 40", "after", tracked=tracked))
    assert rec.passed, rec.to_markdown()
    shown = accepted(out) if tracked else texts(out)
    at = shown.index("A.Schedule 40 black steel.")
    assert shown[at + 1:at + 6] == ["Boxed note", "Boxed note"] + SUBMITTALS
    at = texts(out).index("A.Schedule 40 black steel.")                  # in the output, originals included
    assert [d.block_index for d in plan.diffs if d.before is None] == [at + 3, at + 4, at + 5]


def test_a_tracked_move_flags_what_word_keeps_at_a_containers_end(src):
    plan, _, rec = move(src, {"paragraphs": {"text_regex": "^Cell third$"}}, to("^Cell first$", "before"))
    assert rec.passed and any("end their tc" in f for f in plan.flags)
    plan, _, _ = move(src, {"paragraphs": {"text_regex": "^Cell third$"}}, to("^Cell first$", "before", tracked=False))
    assert not any("end their" in f for f in plan.flags)


@pytest.mark.parametrize("tracked", [False, True])
def test_paragraphs_move_in_a_header(src, tracked):
    plan, out, rec = move(src, {"scope": ["headers"], "paragraphs": {"text_regex": "^Header C$"}},
                          to("^Header A$", "before", tracked=tracked))
    assert rec.passed, rec.to_markdown()
    assert plan.intended_parts == [HEADER]
    assert (accepted(out, HEADER) if tracked else texts(out, HEADER)) == ["Header C", "Header A", "Header B"]
    assert DocxPackage(out).read(MAIN) == DocxPackage(src).read(MAIN)


def test_a_stretch_already_in_place_is_a_noted_no_op(src):
    plan, out, rec = move(src, {"paragraphs": A12}, to("^1\\.3\\t", "before"))
    assert rec.passed, rec.to_markdown()
    assert DocxPackage(out).read(MAIN) == DocxPackage(src).read(MAIN)
    assert plan.intended_parts == [] and any("nothing moves" in n for n in plan.notes)
    assert plan.property_assertions[0]["moves"][MAIN]["noop"] is True
    assert (MAIN, "*", "verify") in get_op("move_paragraphs").plan(DocxPackage(src), {"paragraphs": A12},
                                                                   to("^1\\.3\\t", "before")).touches


def test_no_paragraph_to_move_is_not_found_or_a_noted_no_op(src):
    with pytest.raises(JobException) as e:
        get_op("move_paragraphs").plan(DocxPackage(src), {"paragraphs": {"text_regex": "^NOPE"}}, to("^1\\.5", "after"))
    assert e.value.code == "not-found"
    plan, out, rec = move(src, {"paragraphs": {"text_regex": "^NOPE"}}, to("^1\\.5", "after", allow_missing=True))
    assert rec.passed and not plan.property_assertions and any("no-op" in n for n in plan.notes)


def test_untracked_moves_carry_comments_fields_links_revisions_and_content_controls(src):
    data = build_moving(comment=True, field=True, hyperlink=True, revision=True, sdt=True)
    for select, params in (({"paragraphs": A12}, to("^1\\.5\\t", "before", tracked=False)),
                           ({"paragraphs": A22}, to("^2\\.1\\t", "before", tracked=False)),
                           ({"paragraphs": A15}, to("^1\\.2\\t", "before", tracked=False)),
                           ({"paragraphs": A21}, to("^1\\.1\\t", "before", tracked=False))):
        plan, out, rec = move(data, select, params)
        assert rec.passed, (select, rec.to_markdown())
    plan, _, _ = move(data, {"paragraphs": A22}, to("^2\\.1\\t", "before", tracked=False))
    assert any("fields move" in f for f in plan.flags)
    plan, _, _ = move(data, {"paragraphs": A21}, to("^1\\.1\\t", "before", tracked=False))
    assert any("pending tracked changes" in f for f in plan.flags)


def test_new_revisions_carry_a_utc_date_when_the_file_does(src):
    data = build_moving(utc=True)
    plan, out, rec = move(data, {"paragraphs": A13}, to("^1\\.5\\t", "before"))
    assert rec.passed, rec.to_markdown()
    new = [el for el in DocxPackage(out).xml(MAIN).iter(W + "ins", W + "del") if el.get(W + "author") != "Jeremy"]
    assert len(new) == 12 and all(el.get(W16DU) for el in new)

    def strip_one(root):
        next(el for el in root.iter(W + "ins") if el.get(W + "author") != "Jeremy").attrib.pop(W16DU)
    _caught(data, plan, out, MAIN, strip_one, "dateUtc")

    def old_id(root):                                                     # Jeremy's revision's id, reused
        next(el for el in root.iter(W + "ins") if el.get(W + "author") != "Jeremy").set(W + "id", "501")
    _caught(data, plan, out, MAIN, old_id, "not the source with exactly this move")


# ---- a wrong output per rule -----------------------------------------------------------------------------------------

def test_a_stretch_put_in_the_wrong_place_is_caught(src):
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))

    def one_further(root):
        moved = [para(root, t) for t in SUBMITTALS]
        anchor = para(root, "1.5WARRANTY")
        for p in moved:
            p.getparent().remove(p)
        ref = anchor
        for p in moved:
            ref.addnext(p)
            ref = p
    _caught(src, plan, out, MAIN, one_further, "not the source with exactly this move")


def test_a_paragraph_left_behind_is_caught(src):
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))

    def leave_one(root):
        shop = para(root, "Shop drawings, signed and sealed.")
        shop.getparent().remove(shop)
        para(root, "1.4QUALITY ASSURANCE").addprevious(shop)
    _caught(src, plan, out, MAIN, leave_one)


def test_formatting_changed_in_transit_is_caught(src):
    for tracked in (False, True):
        plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=tracked))

        def unbold(root):
            shops = [p for p in root.iter(W + "p") if block_full_text(p) == "Shop drawings, signed and sealed."]
            shops[-1].find(f".//{W}b").getparent().remove(shops[-1].find(f".//{W}b"))
        rec = _caught(src, plan, out, MAIN, unbold)
        assert "V2" not in failed(rec)                                     # the words are the same: V10 alone sees it

        def unindent(root):
            shops = [p for p in root.iter(W + "p") if block_full_text(p) == "Shop drawings, signed and sealed."]
            shops[-1].find(f"{W}pPr/{W}ind").getparent().remove(shops[-1].find(f"{W}pPr/{W}ind"))
        _caught(src, plan, out, MAIN, unindent)


def test_wrong_tracked_markup_is_caught(src):
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", author="Spec Team"))

    def copies(root):
        return [p for p in root.iter(W + "p") if p.find(f"{W}pPr/{W}rPr/{W}ins") is not None]

    def originals(root):
        return [p for p in root.iter(W + "p") if p.find(f"{W}pPr/{W}rPr/{W}del") is not None]

    def unwrap_copy(root):
        ins = copies(root)[1].find(W + "ins")
        for r in list(ins):
            ins.addprevious(r)
        ins.getparent().remove(ins)

    def unmark_original(root):
        d = originals(root)[0].find(f"{W}pPr/{W}rPr/{W}del")
        d.getparent().remove(d)

    def wrong_author(root):
        copies(root)[0].find(W + "ins").set(W + "author", "Someone")

    def bookmark_left(root):
        orig = originals(root)[0]
        orig.insert(1, etree.fromstring(f'<w:bookmarkStart xmlns:w="{W[1:-1]}" w:id="91" w:name="_Left"/>'))
        orig.append(etree.fromstring(f'<w:bookmarkEnd xmlns:w="{W[1:-1]}" w:id="91"/>'))

    for fn in (unwrap_copy, unmark_original, bookmark_left):
        _caught(src, plan, out, MAIN, fn)
    _caught(src, plan, out, MAIN, wrong_author, "author 'Someone'")


# ---- wrong implementations shared by plan and apply ----------------------------------------------------------------

def test_a_shared_anchor_off_by_one_is_caught(src, monkeypatch):
    """The anchor resolved one paragraph further: the plan predicts the diffs of that move and apply makes it, so V2
    agrees — the verifier's own anchor does not."""
    real = mp.paragraph_selected

    def shifted(pred, p, index, text, sheet, all_pars=None):
        if pred == {"text_regex": "^1\\.5\\t"}:
            return index > 0 and real(pred, all_pars[index - 1], index - 1, block_full_text(all_pars[index - 1]),
                                      sheet, all_pars=all_pars)
        return real(pred, p, index, text, sheet, all_pars=all_pars)
    monkeypatch.setattr(mp, "paragraph_selected", shifted)
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "after", tracked=False))
    assert "V2" not in failed(rec) and "V10" in failed(rec), rec.to_markdown()


def test_a_shared_selection_that_leaves_the_last_paragraph_behind_is_caught(src, monkeypatch):
    real = mp.paragraph_selected

    def short(pred, p, index, text, sheet, all_pars=None):
        return real(pred, p, index, text, sheet, all_pars=all_pars) and text != "Shop drawings, signed and sealed."
    monkeypatch.setattr(mp, "paragraph_selected", short)
    for tracked in (False, True):
        plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=tracked))
        assert failed(rec) == ["V10"], rec.to_markdown()
        assert "moves found here" in v10(rec)


def test_a_shared_run_grouping_that_splits_every_run_is_caught(src, monkeypatch):
    monkeypatch.setattr(mp, "run_groups", lambda p, transparent=frozenset(): [[r] for r in p if r.tag == W + "r"])
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before"))
    assert plan.rev_delta["ins"] == 5 and "V4" not in failed(rec) and "V10" in failed(rec), rec.to_markdown()


def test_a_placement_that_reverses_the_stretch_is_caught(src, monkeypatch):
    """V2 compares the multiset of changed blocks: three paragraphs inserted in the wrong order read the same to it."""
    real = mp.place
    monkeypatch.setattr(mp, "place", lambda elements, anchor, position: real(list(reversed(elements)), anchor, position))
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))
    assert "V2" not in failed(rec) and "V10" in failed(rec), rec.to_markdown()


def test_a_copy_that_keeps_the_paragraph_ids_is_caught(src, monkeypatch):
    monkeypatch.setattr(mp, "COPY_DROPS", ())
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before"))
    assert failed(rec) == ["V10"] and "paraId" in v10(rec), rec.to_markdown()


def test_an_origin_that_keeps_its_bookmarks_is_caught(src, monkeypatch):
    monkeypatch.setattr(mp, "BOOKMARKS", set())
    plan, out, rec = move(src, {"paragraphs": A13}, to("^1\\.5\\t", "before"))
    assert "V10" in failed(rec), rec.to_markdown()


# ---- the two readings agree ------------------------------------------------------------------------------------------

SELECTIONS = [
    ({"paragraphs": A13}, to("^1\\.5\\t", "before")), ({"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False)),
    ({"paragraphs": A14}, to("^A\\.\\tTwo years", "after", tracked=False)), ({"paragraphs": A14}, to("^1\\.1", "after")),
    ({"paragraphs": A12}, to("^1\\.3\\t", "before")), ({"paragraphs": A21}, to("^1\\.2\\t", "before")),
    ({"paragraphs": {"text_regex": "^Cell third$"}}, to("^Cell first$", "before")),
    ({"paragraphs": {"text_regex": "^1\\.3\\t|^1\\.4\\t"}}, to("^1\\.5", "before")),
    ({"paragraphs": {"text_regex": "^Installer$|^1\\.5\\t"}}, to("^1\\.1", "after")),
    ({"paragraphs": {"text_regex": "^Cell first$|^Other cell$"}}, to("^1\\.1", "after")),
    ({"paragraphs": A13}, to("^Product data", "after")), ({"paragraphs": A13}, to("^Cell first$", "after")),
    ({"paragraphs": A13}, to("^A\\.\\t", "after")), ({"paragraphs": A13}, to("^NOPE", "after")),
    ({"paragraphs": {"text_regex": "^Between the tables"}}, to("^1\\.1", "after", tracked=False)),
    ({"paragraphs": {"text_regex": "^END OF SECTION$"}}, to("^PART 2", "before", tracked=False)),
    ({"paragraphs": {"text_regex": "^1\\.5\\t|^A\\.\\tTwo years|^$"}}, to("^1\\.1", "after", tracked=False)),
    ({"paragraphs": A13}, to("^SECTION 21", "before")), ({"paragraphs": A13}, to("^END OF SECTION$", "after")),
    ({"paragraphs": A21}, to("^$", "after", tracked=False)), ({"paragraphs": A21}, to("^$", "before", tracked=False)),
    ({"paragraphs": A13}, to("^1\\.2\\t", "after")), ({"paragraphs": A13}, to("^A\\.\\tNFPA", "after")),
]


# the table of contents: a field begun in "Summary ..... 1" and ended in "Submittals ..... 2"
SUMMARY, REFERENCES, ENTRIES = "Summary \\.+ 1$", "^References \\.+", "\\.{5} \\d$"
TOC_SELECTIONS = [
    ({"paragraphs": {"text_regex": SUMMARY}}, to("^1\\.1\\t", "after", tracked=False)),
    ({"paragraphs": A13}, to(REFERENCES, "before", tracked=False)), ({"paragraphs": A13}, to(SUMMARY, "after")),
    ({"paragraphs": A13}, to(SUMMARY, "before")), ({"paragraphs": A13}, to("^Submittals \\.+", "after")),
    ({"paragraphs": {"text_regex": ENTRIES}}, to("^END OF SECTION$", "after", tracked=False)),
    ({"paragraphs": {"text_regex": REFERENCES}}, to("^1\\.2\\t", "before", tracked=False)),
]


@pytest.mark.parametrize("select,params", SELECTIONS + [(s, {**p, "toc": True}) for s, p in TOC_SELECTIONS])
def test_transform_and_verifier_decide_alike(src, select, params):
    if params.get("toc"):
        src = build_moving(toc=True)
    pkg, vpkg = DocxPackage(src), VPackage(src)
    root, vroot = pkg.xml(MAIN), vpkg.xml(MAIN)
    pars, view = list(root.iter(W + "p")), PartView(vroot)
    sheet, styles = StyleSheet(pkg), Styles(vpkg.xml("word/styles.xml"))
    tracked = params.get("tracked", True)
    chosen = [i for i, p in enumerate(pars) if mp.paragraph_selected(select["paragraphs"], p, i, block_full_text(p),
                                                                     sheet, all_pars=pars)]
    assert chosen == [i for i, p in enumerate(view.pars) if para_matches(select["paragraphs"], p, i, view, styles)]
    try:
        m = mp._locate(MAIN, root, pars, chosen, params["to"], params["position"], tracked, sheet)
        theirs = (pars.index(m.pars[0]), len(m.pars), m.anchor_index, m.noop)
    except JobException:
        theirs = "refused"
    try:
        d = vm._decide(vroot, view, chosen, params["to"], params["position"], tracked, styles)
        ours = (d["first"], len(d["moved"]), d["anchor"], d["still"])
    except vm._Refuse:
        ours = "refused"
    assert theirs == ours


# ---- refusals ----------------------------------------------------------------------------------------------------

def _refused(data: bytes, select: dict, params: dict) -> JobException:
    with pytest.raises(JobException) as e:
        get_op("move_paragraphs").plan(DocxPackage(data), select, params)
    return e.value


def test_a_stretch_with_a_gap_is_refused_naming_the_gap(src):
    e = _refused(src, {"paragraphs": {"text_regex": "^1\\.3\\t|^1\\.4\\t"}}, to("^1\\.5", "before"))
    assert e.code == "move-gap" and "Product data for each sprinkler." in e.reason
    e = _refused(src, {"paragraphs": {"text_regex": "^Installer$|^1\\.5\\t"}}, to("^1\\.1", "after"))
    assert e.code == "move-gap" and "“Installer”" in e.reason


def test_paragraphs_in_two_places_or_a_content_control_are_refused(src):
    assert _refused(src, {"paragraphs": {"text_regex": "^Cell first$|^Other cell$"}},
                    to("^1\\.1", "after")).code == "move-container"
    data = build_moving(sdt=True)
    assert _refused(data, {"paragraphs": {"text_regex": "^Controlled"}}, to("^1\\.1", "after")).code == "move-container"


def test_the_destination_must_be_one_paragraph_beside_the_stretch(src):
    assert _refused(src, {"paragraphs": A13}, to("^NOPE", "after")).code == "not-found"
    e = _refused(src, {"paragraphs": A13}, to("^A\\.\\t", "after"))
    assert e.code == "ambiguous" and e.details["count"] == 6
    assert _refused(src, {"paragraphs": A13}, to("^Product data", "after")).code == "move-anchor"
    e = _refused(src, {"paragraphs": A13}, to("^Cell first$", "after"))
    assert e.code == "move-anchor" and "tc" in e.reason
    e = _refused(src, {"paragraphs": {"text_regex": "^Cell third$"}}, to("^1\\.1", "after"))
    assert e.code == "move-anchor"


def test_a_section_break_in_the_stretch_is_refused(src):
    e = _refused(src, {"paragraphs": {"text_regex": "^1\\.5\\t|^A\\.\\tTwo years|^$"}}, to("^1\\.1", "after"))
    assert e.code == "section-break"
    # the paragraph right before the break moves; the break stays
    plan, out, rec = move(src, {"paragraphs": {"text_regex": "^1\\.5\\t|^A\\.\\tTwo years"}},
                          to("^1\\.2\\t", "before", tracked=False))
    assert rec.passed, rec.to_markdown()


def test_tables_that_would_touch_or_end_the_container_are_refused(src):
    e = _refused(src, {"paragraphs": {"text_regex": "^Between the tables"}}, to("^1\\.1", "after", tracked=False))
    assert e.code == "move-table-edge" and "two tables would touch" in e.reason
    e = _refused(src, {"paragraphs": {"text_regex": "^END OF SECTION$"}}, to("^PART 2", "before", tracked=False))
    assert e.code == "move-table-edge" and "body would end in a table" in e.reason


def test_a_bookmark_cut_by_the_stretch_is_refused():
    data = build_moving(split_bookmark=True)
    e = _refused(data, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False))
    assert e.code == "move-split-range" and "bookmark" in e.reason
    # taking both of its ends moves it whole
    both = {"paragraphs": {"text_regex": "^1\\.2\\t|^A\\.\\tNFPA|^1\\.3\\t|^Product|^Shop"}}
    plan, out, rec = move(data, both, to("^1\\.5\\t", "before", tracked=False))
    assert rec.passed, rec.to_markdown()


def test_a_table_of_contents_is_neither_cut_nor_a_destination():
    """A field whose result runs over several paragraphs: moving its first paragraph alone would cut it; a destination
    inside its result would be replaced when Word updates fields. Right before and right after it are outside; the
    whole contents moves (untracked: a tracked move cannot copy a field); an entry may leave its result."""
    data = build_moving(toc=True)
    e = _refused(data, {"paragraphs": {"text_regex": SUMMARY}}, to("^1\\.1\\t", "after", tracked=False))
    assert e.code == "move-split-range" and "TOC field" in e.reason
    for destination in (to(REFERENCES, "before", tracked=False), to(SUMMARY, "after")):
        e = _refused(data, {"paragraphs": A13}, destination)
        assert e.code == "move-anchor" and "inside a TOC field" in e.reason
    for select, params in (({"paragraphs": A13}, to(SUMMARY, "before")),
                           ({"paragraphs": A13}, to("^Submittals \\.+", "after")),
                           ({"paragraphs": {"text_regex": ENTRIES}}, to("^END OF SECTION$", "after", tracked=False)),
                           ({"paragraphs": {"text_regex": REFERENCES}}, to("^1\\.2\\t", "before", tracked=False))):
        plan, out, rec = move(data, select, params)
        assert rec.passed, (select, rec.to_markdown())
    assert _refused(data, {"paragraphs": {"text_regex": ENTRIES}},
                    to("^END OF SECTION$", "after")).code == "move-tracked-content"


@pytest.mark.parametrize("kw,select,expected", [
    ({}, {"paragraphs": A14}, "move-tracked-content"),                             # a table
    ({"hyperlink": True}, {"paragraphs": A15}, "move-tracked-content"),            # a hyperlink
    ({"field": True}, {"paragraphs": A22}, "move-tracked-content"),                # a field
    ({"comment": True}, {"paragraphs": A12}, "move-tracked-content"),              # a comment's range and reference
    ({"sdt": True}, {"paragraphs": A15}, "move-tracked-content"),                  # a content control
    ({"revision": True}, {"paragraphs": A21}, "existing-revisions"),               # Jack's pending deletion
])
def test_what_a_tracked_move_cannot_copy_is_refused(kw, select, expected):
    data = build_moving(**kw)
    assert _refused(data, select, to("^1\\.1\\t", "before")).code == expected


def test_text_boxes_and_bound_content_controls_are_refused():
    data = build_moving(textbox=True)
    for tracked in (False, True):
        assert _refused(data, {"paragraphs": A21}, to("^1\\.1\\t", "before", tracked=tracked)).code == "textbox"
    assert _refused(data, {"paragraphs": {"text_regex": "^Boxed note"}}, to("^1\\.1", "after")).code == "textbox"
    data = build_moving(sdt=True, data_bound=True)
    assert _refused(data, {"paragraphs": A15}, to("^1\\.1\\t", "before", tracked=False)).code == "structure"


@pytest.mark.parametrize("patch,data_kw,select,params", [
    ("_check_tracked", {}, {"paragraphs": A14}, to("^1\\.1\\t", "before")),
    ("_check_split", {"split_bookmark": True}, {"paragraphs": A13}, to("^1\\.5\\t", "before", tracked=False)),
    ("_check_field_cut", {"toc": True}, {"paragraphs": {"text_regex": SUMMARY}},
     to("^1\\.1\\t", "after", tracked=False)),
    ("_check_field_destination", {"toc": True}, {"paragraphs": A13}, to(REFERENCES, "before", tracked=False)),
])
def test_the_verifier_refuses_what_the_op_must_refuse(patch, data_kw, select, params, monkeypatch):
    data = build_moving(**data_kw)
    monkeypatch.setattr(mp, patch, lambda *a, **k: None)
    plan, out, rec = move(data, select, params)
    assert "V10" in failed(rec) and "the op must refuse" in v10(rec), rec.to_markdown()


def test_every_refusal_code_of_the_move_is_explained():
    codes = set()
    for n in ast.walk(ast.parse(Path(mp.__file__).read_text(encoding="utf-8"))):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
            assert isinstance(n.args[1], ast.Constant), n.lineno
            codes.add(n.args[1].value)
    assert codes == {"bad-params", "not-found", "ambiguous", "textbox", "structure", "section-break",
                     "existing-revisions", "move-gap", "move-container", "move-anchor", "move-table-edge",
                     "move-split-range", "move-tracked-content"}
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


# ---- parameters --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("params", [
    {}, {"position": "after"}, {"to": {"text_regex": "^1\\.5"}}, {"to": "^1\\.5", "position": "after"},
    {"to": {"text_regex": "^1\\.5"}, "position": "below"}, {"to": "all", "position": "after"},
    {"to": {}, "position": "after"}, {"to": {"text": "1.5"}, "position": "after"},
    {"to": {"text_regex": "("}, "position": "after"},
    {"to": {"text_regex": "^1\\.5"}, "position": "after", "tracked": "yes"},
    {"to": {"text_regex": "^1\\.5"}, "position": "after", "allow_missing": 1},
    {"to": {"text_regex": "^1\\.5"}, "position": "after", "author": ""},
    {"to": {"text_regex": "^1\\.5"}, "position": "after", "date_convention": "local"},
])
def test_parameters_are_checked(src, params):
    with pytest.raises(JobException) as e:
        get_op("move_paragraphs").plan(DocxPackage(src), {"paragraphs": A13}, params)
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("select", [{}, {"paragraphs": "all"}, {"paragraphs": {"case_insensitive": True}},
                                    {"paragraphs": {"style": "x"}}, {"tables": "all"}, {"scope": []},
                                    {"scope": ["body"], "paragraphs": A13}])
def test_selectors_are_checked(src, select):
    with pytest.raises(JobException) as e:
        get_op("move_paragraphs").plan(DocxPackage(src), select, to("^1\\.5", "after"))
    assert e.value.code == "bad-params"


# ---- conflicts and composition (rule 9) -------------------------------------------------------------------------

def _spec(*ops, mode="content"):
    return {"request": "r", "summary": "s", "mode": mode, "ops": list(ops)}


MOVE_13 = {"op": "move_paragraphs", "select": {"paragraphs": A13}, "params": to("^1\\.5\\t", "before")}


def _conflict(data, *ops):
    for order in (ops, tuple(reversed(ops))):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec(*order))
        assert e.value.code == "conflict", order
    return str(e.value)


def _passes(data, *ops):
    for order in (ops, tuple(reversed(ops))):
        plan, frags = plan_spec(DocxPackage(data), _spec(*order))
        out = apply_spec(DocxPackage(data), frags).output_bytes()
        rec = run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed, rec.to_markdown()


def test_touches_declare_the_part_as_a_whole(src):
    frag = get_op("move_paragraphs").plan(DocxPackage(src), {"paragraphs": A13}, to("^1\\.5\\t", "before"))
    assert set(frag.touches) == {(MAIN, "*", "text")}


def test_an_edit_in_the_moved_stretch_or_a_deletion_beside_it_conflicts_and_composes_into_steps(src):
    in_range = {"op": "replace_text", "select": {"paragraphs": {"text_regex": "^Shop"}},
                "params": {"find": "Shop drawings", "replace": "Working drawings"}}
    _conflict(src, MOVE_13, in_range)
    delete = {"op": "delete_paragraphs", "select": {"paragraphs": {"text_regex": "^B\\.\\tWelding"}}, "params": {}}
    _conflict(src, MOVE_13, delete)
    pkg = DocxPackage(src, name="x.docx")
    assert stage_cuts(pkg, _spec(MOVE_13, in_range)) == [1, 1]
    chain = split_into_stages(pkg, _spec(MOVE_13, in_range))
    assert [[o["op"] for o in st["spec"]["ops"]] for st in chain["stages"]] == [["move_paragraphs"], ["replace_text"]]
    assert [len(frags) for _plan, frags in plan_chain(pkg, chain)] == [1, 1]


def test_an_edit_in_another_part_plans_beside_the_move(src):
    _passes(src, MOVE_13, {"op": "replace_text", "select": {"scope": ["headers"]},
                           "params": {"find": "Header B", "replace": "Header Two"}})
    _passes(src, {"op": "move_paragraphs", "select": {"scope": ["headers"], "paragraphs": {"text_regex": "^Header C$"}},
                  "params": to("^Header A$", "before")}, MOVE_13)


# ---- the catalog, the pipeline, a render -----------------------------------------------------------------------------

def test_catalog_and_schema(src):
    from spec_surgeon.editspec import SpecError, validate_spec
    cat = op_catalog()
    assert "move_paragraphs" in cat["ops"] and COMMON_ASSUMPTIONS["move_paragraphs"]
    assert cat["ops"]["move_paragraphs"]["params"]["required"] == ["to", "position"]
    validate_spec(_spec(MOVE_13))
    for bad in ({"op": "move_paragraphs", "select": {"paragraphs": A13}, "params": {"to": {"text_regex": "x"}}},
                {"op": "move_paragraphs", "select": {"paragraphs": A13},
                 "params": {**to("x", "after"), "position": "under"}},
                {"op": "move_paragraphs", "select": {"paragraphs": A13}, "params": {**to("x", "after"), "where": 1}}):
        with pytest.raises(SpecError):
            validate_spec(_spec(bad))


def test_a_spec_runs_through_the_batch_pipeline(src, tmp_path):
    """Plan, apply, verify, write and deliver in a worker, from a spec (the plan and its transform data go through JSON)."""
    from spec_surgeon.batch import run_batch
    spec = _spec(MOVE_13, {"op": "move_paragraphs", "select": {"scope": ["headers"],
                                                               "paragraphs": {"text_regex": "^Header C$"}},
                           "params": to("^Header A$", "before")})
    path = tmp_path / "21 13 13 – SPRINKLERS.docx"
    path.write_bytes(src)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_output(src, tmp_path):
    from spec_surgeon.render import libreoffice
    outs = {}
    for name, params in (("untracked", to("^1\\.5\\t", "before", tracked=False)), ("tracked", to("^1\\.5\\t", "before"))):
        _, out, rec = move(src, {"paragraphs": A13}, params)
        assert rec.passed, rec.to_markdown()
        outs[name] = tmp_path / f"{name}.docx"
        outs[name].write_bytes(out)
    pdfs = libreoffice.render_pdfs(list(outs.values()), tmp_path / "pdf", workers=1)
    if len(pdfs) != 2:
        pytest.skip("LibreOffice did not render here")
    for path in outs.values():
        pdf = pdfs[path]
        assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
