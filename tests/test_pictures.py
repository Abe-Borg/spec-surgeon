"""insert_image, remove_images and resize_images (built-in batch 6, F4 — docs/BUILTIN_OPS_BATCH6_DESIGN.md §5) and their V10
kinds: the two picture readers (BMP, the resolution) and the two size rules compared; each op end to end on the pictures
fixture (tracked and untracked, every form of match, inline and floating, the body, headers and footers); a wrong OUTPUT
per rule; wrong IMPLEMENTATIONS shared by plan and apply; refusals with their codes; parameters; touches and plan-time
conflicts (rule 9) and the steps the engine composes; the catalog and the registration points; the batch pipeline; a
LibreOffice render."""
from __future__ import annotations

import ast
import hashlib
import io
import random
import shutil
import struct
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from lxml import etree

from fx_pictures import (A, BLUE, GREEN, PIC, R, RED, SVG, W, WP, alternate, bmp, build_pictures, cropped, docpr,
                         drawing_named, format_change, gif, in_field, in_hyperlink, in_insertion, in_textbox, jpeg,
                         mutate, no_page_size, paragraph_with, png, rels_of, relative, svg_blip, text_of, turned,
                         turned_jpeg, vml, watermark)
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog, op_sheet
from spec_surgeon.editspec import apply_spec, plan_spec, spec_input_files, split_into_stages, stage_cuts
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import images as op_images
from spec_surgeon.ops import insert_image as ins_module
from spec_surgeon.ops import pictures as pic_module
from spec_surgeon.ops import remove_images as rm_module
from spec_surgeon.ops import resize_images as rs_module
from spec_surgeon.package import DocxPackage
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify import pictures as v_pictures
from spec_surgeon.verify.images import v_picture, v_picture_info

MAIN, HEADER, FOOTER = "word/document.xml", "word/header1.xml", "word/footer1.xml"
CT = "[Content_Types].xml"
EMU = 914400
TEXT_WIDTH_0 = int(6.5 * EMU)                    # section 1: 8.5 in − 1 in − 1 in
TEXT_WIDTH_1 = int(3.5 * EMU)                    # section 2: 8.5 in − 2.5 in − 2.5 in
SITE = {"paragraphs": {"text_regex": r"^A\.\tSee the site plan"}}
MATERIALS = {"paragraphs": {"text_regex": r"^2\.01"}}


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_pictures()


@pytest.fixture()
def pics(tmp_path) -> Dict[str, str]:
    files = {"wide.png": png(1200, 600), "small.png": png(8, 8), "dpi.png": png(600, 300, ppm=(3937, 3937)),
             "seal.gif": gif(40, 20), "plan.bmp": bmp(30, 20, (3780, 3780)), "photo.jpg": jpeg(600, 300, dpi=300),
             "blue.png": png(8, 8, BLUE), "red.png": png(8, 8, RED), "green.png": png(8, 4, GREEN),
             "logo.svg": SVG, "notes.png": b"not a picture"}
    out = {}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        out[name] = str(tmp_path / name)
    return out


def run_ops(data: bytes, specs: List[Tuple[str, dict, dict]]):
    pkg = DocxPackage(data)
    frags = [get_op(o).plan(pkg, sel, par) for o, sel, par in specs]
    plan = merge_fragments(frags)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    out = work.output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def one(data: bytes, op: str, select: dict, params: dict):
    return run_ops(data, [(op, select, params)])


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def explain(rec) -> str:
    return "; ".join(f"{c.id}: {c.summary[:300]} {c.data.get('errors', [])[:4]}" for c in rec.checks
                     if c.status == "FAIL")


def v10(rec) -> str:
    c = rec.get("V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def suite(src: bytes, out: bytes, plan) -> Any:
    return run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def caught(src: bytes, plan, out: bytes, part: str, fn, needle: str = None) -> Any:
    """A wrong output (``fn`` changes ``part`` of the true one) fails V10 (and ``needle`` is in its errors)."""
    rec = suite(src, mutate(out, part, fn), plan)
    assert "V10" in failed(rec), rec.to_markdown()
    if needle:
        assert needle in v10(rec), v10(rec)
    return rec


def xml(data: bytes, part: str = MAIN):
    return DocxPackage(data).xml(part)


def members(data: bytes) -> List[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


def read(data: bytes, name: str) -> bytes:
    return zipfile.ZipFile(io.BytesIO(data)).read(name)


def extent(drawing) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    fr = next(c for c in drawing if c.tag in (WP + "inline", WP + "anchor"))
    e = fr.find(WP + "extent")
    x = fr.find(f"{A}graphic/{A}graphicData/{PIC}pic/{PIC}spPr/{A}xfrm/{A}ext")
    return (int(e.get("cx")), int(e.get("cy"))), (int(x.get("cx")), int(x.get("cy")))


def _ours(d) -> bool:
    """A drawing insert_image wrote: its pic:cNvPr is named "Picture <id>" (python-docx names its own "image.png")."""
    nv = d.find(f".//{PIC}cNvPr")
    return nv is not None and nv.get("id") == "0" and nv.get("name", "").startswith("Picture ")


def new_pictures(out: bytes, part: str = MAIN) -> List[Any]:
    """The drawings the op added, in document order."""
    return [d for d in xml(out, part).iter(W + "drawing") if _ours(d)]


def paragraph_of(drawing):
    q = drawing.getparent()
    while q.tag != W + "p":
        q = q.getparent()
    return q


# ---- the picture files: two readers, two size rules ---------------------------------------------------------------

def _outcome(fn, data):
    try:
        return tuple(fn(data))
    except (ValueError, struct.error):
        return "refused"


def _crafted() -> List[bytes]:
    def exif(unit, xr, yr, jfif_units=0):
        def seg(m, b):
            return b"\xff" + bytes([m]) + struct.pack(">H", len(b) + 2) + b
        e = "<"
        ifd = struct.pack(e + "H", 3)                     # at 8: 3 entries, the next IFD's offset, then the rationals
        for tag, typ, val in ((0x011A, 5, 50), (0x011B, 5, 58), (0x0128, 3, unit)):
            ifd += struct.pack(e + "HHI", tag, typ, 1) + (struct.pack(e + "I", val) if typ == 5
                                                         else struct.pack(e + "HH", val, 0))
        ifd += b"\x00" * 4 + struct.pack(e + "II", *xr) + struct.pack(e + "II", *yr)
        return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01" + bytes([jfif_units]) + b"\x00\x01\x00\x01\x00\x00")
                + seg(0xE1, b"Exif\x00\x00II" + struct.pack(e + "HI", 42, 8) + ifd)
                + seg(0xC0, b"\x08" + struct.pack(">HH", 12, 30) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
                + b"\xff\xd9")
    core = b"BM" + struct.pack("<IHHI", 40, 0, 0, 26) + struct.pack("<IHHHH", 12, 3, 2, 1, 24) + b"\x00" * 14
    return [png(3, 2), png(5, 5, ppm=(11811, 11811)), png(4, 4, ppm=(0, 5)), bmp(3, 2), bmp(3, 2, (0, 0)),
            bmp(7, 3, (2835, 5670)), core, gif(9, 4), jpeg(30, 12), jpeg(30, 12, dpi=300), exif(2, (300, 1), (300, 1)),
            exif(3, (72, 1), (144, 1)), exif(1, (300, 1), (300, 1)), exif(2, (0, 1), (300, 1)),
            exif(2, (300, 1), (300, 1), jfif_units=1), b"BM", b"BM" + b"\x00" * 30, SVG, b""]


def test_the_two_picture_readers_agree_on_crafted_and_damaged_files():
    cases = _crafted()
    rnd = random.Random(20261021)
    for base in list(cases[:15]):
        for _ in range(400):
            b = bytearray(base)
            for _ in range(rnd.randint(1, 4)):
                if b:
                    b[rnd.randrange(min(len(b), 120))] = rnd.randrange(256)
            cases.append(bytes(b[:rnd.randint(0, len(b))] if rnd.random() < 0.3 else b))
    seen = set()
    for data in cases:
        a, b = _outcome(op_images.read_picture, data), _outcome(v_picture_info, data)
        assert a == b, (data[:48], a, b)
        seen.add(a if a == "refused" else a[0])
    assert {"png", "gif", "jpeg", "bmp", "refused"} <= seen


def test_the_resolution_of_each_format():
    per = lambda data: op_images.read_picture(data)[3:]                 # noqa: E731
    assert per(png(4, 4)) == (Fraction(9525), Fraction(9525))           # none: 96 dpi
    assert per(png(4, 4, ppm=(3937, 7874))) == (Fraction(36000000, 3937), Fraction(36000000, 7874))
    assert per(jpeg(10, 10, dpi=300)) == (Fraction(3048), Fraction(3048))
    exif_inch, exif_cm, exif_no_unit, exif_zero, jfif_wins = _crafted()[10:15]
    assert per(exif_inch) == (Fraction(3048), Fraction(3048))
    assert per(exif_cm) == (Fraction(5000), Fraction(2500))
    assert per(exif_no_unit) == per(exif_zero) == (Fraction(9525), Fraction(9525))
    assert per(jfif_wins) == (Fraction(914400), Fraction(914400))         # JFIF's 1 × 1 dpi comes first
    assert per(bmp(3, 2, (3780, 3780))) == (Fraction(36000000, 3780),) * 2
    assert per(gif(5, 5)) == (Fraction(9525), Fraction(9525))


def test_swap_images_reader_still_takes_only_png_jpeg_and_gif():
    with pytest.raises(ValueError, match="not a PNG, JPEG or GIF picture"):
        op_images.sniff(bmp(3, 2))
    with pytest.raises(ValueError, match="not a PNG, JPEG or GIF picture"):
        v_picture(bmp(3, 2))
    assert op_images.read_picture(bmp(3, -2))[:3] == ("bmp", 3, 2)        # a top-down BMP


def test_the_two_size_rules_agree():
    rnd = random.Random(7)
    sizes = [None, 0.5, 1, 2.25, 6.5, 0.0000001, 13.37, 100]
    for i in range(2000):
        pw, ph = rnd.randint(1, 5000), rnd.randint(1, 5000)
        ex, ey = Fraction(rnd.choice([9525, 3048, 36000000]), rnd.choice([1, 7, 3937])), Fraction(9525)
        pic = op_images.Picture("png", pw, ph, ex, ey)
        width, height = rnd.choice(sizes), rnd.choice(sizes)
        limit = rnd.choice([TEXT_WIDTH_0, TEXT_WIDTH_1, 1])
        try:
            a = op_images.placed_size(pic, width, height, limit)
        except JobException:
            a = "refused"
        b = v_pictures._v_placed(pw, ph, ex, ey, width, height, limit)
        assert a == (b if v_pictures._v_fits(b) else "refused"), (pw, ph, ex, width, height, limit, a, b)
        box = (rnd.randint(1, 10 ** 7), rnd.randint(1, 10 ** 7))
        scale = rnd.choice([None, 50, 33.3, 0.001, 250])
        args = {"scale": scale} if scale else {"width": width, "height": height}
        if not scale and width is None and height is None:
            continue
        try:
            a = rs_module.new_size(box, args.get("width"), args.get("height"), args.get("scale"))
        except JobException:
            a = "refused"
        b = v_pictures._v_resized(box, args)
        assert a == (b if v_pictures._v_fits(b) else "refused"), (box, args, a, b)


# ---- insert_image end to end ----------------------------------------------------------------------------------------

def test_a_wide_picture_after_its_anchor_shrinks_to_the_text_width_tracked(src, pics):
    plan, out, rec = one(src, "insert_image", SITE, {"image": pics["wide.png"], "description": "Site plan, north up"})
    assert rec.passed, explain(rec)
    root = xml(out)
    anchor = paragraph_with(root, "A.\tSee the site plan")
    p = anchor.getnext()
    d = p.find(f"{W}ins/{W}r/{W}drawing")
    assert extent(d) == ((TEXT_WIDTH_0, TEXT_WIDTH_0 // 2),) * 2       # 1200 × 600 px at 96 dpi: 12.5 in → 6.5 in
    assert p.find(f"{W}pPr/{W}rPr/{W}ins") is not None and text_of(p) == ""
    assert d.find(f"{WP}inline/{WP}docPr").get("descr") == "Site plan, north up"
    assert plan.members_added == ["word/media/image6.png"] and CT not in plan.intended_parts
    assert read(out, "word/media/image6.png") == Path(pics["wide.png"]).read_bytes()
    rid = d.find(f".//{A}blip").get(R + "embed")
    assert rels_of(out, MAIN)[rid] == ("image", "media/image6.png")
    assert plan.rev_delta["ins"] == 1 and plan.rev_delta["ins_mark"] == 1 and plan.mode == "content"
    assert "inserted_images: 1 picture paragraph(s)" in rec.get("V10").summary


def test_the_second_section_is_narrower_and_a_picture_before_its_anchor_untracked(src, pics):
    plan, out, rec = one(src, "insert_image", MATERIALS, {"image": pics["wide.png"], "position": "before",
                                                          "tracked": False})
    assert rec.passed, explain(rec)
    p = paragraph_with(xml(out), "2.01").getprevious()
    d = p.find(f"{W}r/{W}drawing")
    assert extent(d)[0] == (TEXT_WIDTH_1, TEXT_WIDTH_1 // 2)
    assert p.find(W + "pPr") is None and p.find(W + "ins") is None and plan.mode == "untracked"
    assert any("UNTRACKED insertion of 1 picture" in f for f in plan.flags)


def test_a_small_picture_keeps_its_own_size_and_its_resolution_counts(src, pics):
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["small.png"]})
    assert rec.passed and extent(new_pictures(out)[0])[0] == (8 * 9525, 8 * 9525)
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["dpi.png"]})       # 3937 pixels per metre (100 dpi)
    want = Fraction(600 * 36000000, 3937)
    assert rec.passed and extent(new_pictures(out)[0])[0] == (int(want + Fraction(1, 2)), int(want / 2 + Fraction(1, 2)))
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["photo.jpg"]})    # 300 dpi: exactly 2 × 1 inches
    assert rec.passed and extent(new_pictures(out)[0])[0] == (2 * EMU, EMU)


@pytest.mark.parametrize("size,want", [
    ({"width": 2}, (2 * EMU, EMU)), ({"height": 2}, (4 * EMU, 2 * EMU)), ({"width": 1, "height": 3}, (EMU, 3 * EMU)),
    ({"width": 0.333}, (304495, 152248)),                                  # halves up: 152247.6 → 152248
])
def test_a_width_or_height_keeps_the_proportions_and_both_set_the_size(src, pics, size, want):
    _, out, rec = one(src, "insert_image", MATERIALS, {"image": pics["wide.png"], **size})
    assert rec.passed, explain(rec)
    assert extent(new_pictures(out)[0])[0] == want           # never shrunk to the text width when a size is given


def test_alignment_style_and_alt_text(src, pics):
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["small.png"], "align": "center", "style": "Figure",
                                                  "description": "A & <B> “quoted”"})
    assert rec.passed, explain(rec)
    p = new_pictures(out)[0]
    para = paragraph_of(p)
    assert para.find(f"{W}pPr/{W}pStyle").get(W + "val") == "Figure"
    assert para.find(f"{W}pPr/{W}jc").get(W + "val") == "center"
    assert p.find(f"{WP}inline/{WP}docPr").get("descr") == "A & <B> “quoted”"


def test_a_footer_without_relationships_gets_its_rels_and_a_gif_its_content_type(src, pics):
    assert "word/_rels/footer1.xml.rels" not in members(src)
    plan, out, rec = one(src, "insert_image", {"scope": ["footers"], "paragraphs": {"text_regex": "^21 13 13"}},
                         {"image": pics["seal.gif"], "width": 0.5})
    assert rec.passed, explain(rec)
    assert plan.members_added == ["word/media/image1.gif", "word/_rels/footer1.xml.rels"]
    assert rels_of(out, FOOTER) == {"rId1": ("image", "media/image1.gif")}
    ct = read(out, CT).decode()
    assert '<Default Extension="gif" ContentType="image/gif"/>' in ct and "image1.gif" not in ct


def test_a_bmp_gets_a_default_too_and_a_header_its_next_relationship(src, pics):
    plan, out, rec = one(src, "insert_image", {"scope": ["headers"], "paragraphs": {"text_regex": "^ACME"}},
                         {"image": pics["plan.bmp"], "height": 0.25})
    assert rec.passed, explain(rec)
    assert plan.members_added == ["word/media/image1.bmp"]
    assert rels_of(out, HEADER)["rId2"] == ("image", "media/image1.bmp")
    assert '<Default Extension="bmp" ContentType="image/bmp"/>' in read(out, CT).decode()


def test_an_extension_whose_default_names_another_type_gets_an_override(src, pics):
    odd = mutate(src, CT, lambda root: next(e for e in root if e.get("Extension") == "png").set("ContentType",
                                                                                                 "image/x-png"))
    plan, out, rec = one(odd, "insert_image", SITE, {"image": pics["small.png"]})
    assert rec.passed, explain(rec)
    assert 'PartName="/word/media/image6.png" ContentType="image/png"' in read(out, CT).decode()


def test_every_anchor_gets_a_picture_with_its_own_drawing_id(src, pics):
    sel = {"scope": ["document", "headers"], "paragraphs": {"text_regex": r"^(PART \d|ACME)"}}
    plan, out, rec = one(src, "insert_image", sel, {"image": pics["small.png"]})
    assert rec.passed, explain(rec)
    ids = [int(d.find(f"{WP}inline/{WP}docPr").get("id")) for part in (MAIN, HEADER) for d in new_pictures(out, part)]
    top = max(int(d.get("id")) for part in (MAIN, HEADER) for d in xml(src, part).iter(WP + "docPr"))
    assert ids == [top + 1, top + 2, top + 3]                   # the body's two PART lines, then the header
    assert len(plan.members_added) == 1                         # one media member for every picture
    body = {d.find(f".//{A}blip").get(R + "embed") for d in new_pictures(out)}
    assert len(body) == 1                                       # one relationship per part


def test_an_anchor_in_a_table_cell_gets_its_picture_inside_the_cell(src, pics):
    sel = {"paragraphs": {"text_regex": "^Valve room$"}}
    plan, out, rec = one(src, "insert_image", sel, {"image": pics["small.png"], "position": "after"})
    assert rec.passed, explain(rec)
    cell = paragraph_with(xml(out), "Valve room").getparent()
    assert cell.tag == W + "tc" and len(cell.findall(W + "p")) == 2 and new_pictures(out)

    def after_the_table(root):                         # the same place in document order, outside the cell
        p = paragraph_of(_new_drawing(root))
        table = p.getparent().getparent().getparent()   # w:p → w:tc → w:tr → w:tbl
        table.addnext(p)
    rec = suite(src, mutate(out, MAIN, after_the_table), plan)
    assert "V10" in failed(rec) and "not right after its anchor" in v10(rec)


def test_a_section_with_columns_is_flagged_for_a_picture_at_its_own_size(src, pics):
    two = mutate(src, MAIN, lambda r: next(r.iter(W + "cols")).set(W + "num", "2"))
    plan, _, rec = one(two, "insert_image", SITE, {"image": pics["wide.png"]})
    assert rec.passed and any("section 1 has 2 columns" in f for f in plan.flags)
    plan, _, rec = one(two, "insert_image", SITE, {"image": pics["wide.png"], "width": 3})
    assert rec.passed and not any("columns" in f for f in plan.flags)


def test_a_file_without_an_anchor_is_set_aside_unless_allowed(src, pics):
    with pytest.raises(JobException) as e:
        one(src, "insert_image", {"paragraphs": {"text_regex": "^NOWHERE"}}, {"image": pics["small.png"]})
    assert e.value.code == "not-found"
    plan, out, rec = one(src, "insert_image", {"paragraphs": {"text_regex": "^NOWHERE"}},
                         {"image": pics["small.png"], "allow_missing": True})
    assert rec.passed and out == src and plan.members_added == []


# ---- remove_images end to end ---------------------------------------------------------------------------------------

def _media(data: bytes) -> Dict[str, str]:
    z = zipfile.ZipFile(io.BytesIO(data))
    return {n: hashlib.md5(z.read(n)).hexdigest() for n in z.namelist() if "/media/" in n}


def _media_of(data: bytes, picture: bytes) -> str:
    md5 = hashlib.md5(picture).hexdigest()
    return next(n for n, m in _media(data).items() if m == md5)


@pytest.mark.parametrize("how", ["image", "md5", "name", "descr", "all"])
def test_every_kind_of_match_chooses_its_pictures(src, pics, how):
    blue = hashlib.md5(png(8, 8, BLUE)).hexdigest()
    match, want = {"image": ({"image": pics["blue.png"]}, ["Picture 2", "Picture 4"]),
                   "md5": ({"md5": blue.upper()}, ["Picture 2", "Picture 4"]),
                   "name": ({"name_regex": r"^Logo \d$"}, ["Logo 2"]),
                   "descr": ({"name_regex": "plan"}, ["Picture 2"]),
                   "all": ("all", ["Picture 2", "Riser diagram", "Picture 4", "Logo 2", "Detail 1",
                                   "Linked badge"])}[how]
    plan, out, rec = one(src, "remove_images", {}, {"match": match, "tracked": False})
    assert rec.passed, explain(rec)
    names = lambda data: [d.get("name") for d in xml(data).iter(WP + "docPr")]          # noqa: E731
    assert [n for n in names(src) if n not in names(out)] == want


def test_untracked_removal_by_file_takes_the_relationship_and_the_media_with_it(src, pics):
    blue = _media_of(src, png(8, 8, BLUE))
    plan, out, rec = one(src, "remove_images", {}, {"match": {"image": pics["blue.png"]}, "tracked": False})
    assert rec.passed, explain(rec)
    root = xml(out)
    assert not any(d.get("name") in ("Picture 2", "Picture 4") for d in root.iter(WP + "docPr"))
    assert plan.members_removed == [blue] and blue not in members(out)
    assert all(t != "media/" + blue.rsplit("/", 1)[1] for _, t in rels_of(out, MAIN).values())
    assert "Valve room" in text_of(next(root.iter(W + "tbl")).find(f"{W}tr"))           # the table cell stays
    cell = next(root.iter(W + "tbl")).findall(f"{W}tr/{W}tc")[1]
    assert len(cell.findall(W + "p")) == 1 and cell.find(f"{W}p/{W}r") is None          # its paragraph stays, empty
    assert plan.mode == "untracked" and any("UNTRACKED removal of 2 picture" in f for f in plan.flags)
    assert any(d.get("struct_after") is not None for d in plan.to_dict()["diffs"])      # structure-only declarations


def test_removing_one_of_two_pictures_that_share_a_relationship_keeps_it(src):
    plan, out, rec = one(src, "remove_images", {}, {"match": {"name_regex": "^Site plan$"}, "tracked": False})
    assert rec.passed, explain(rec)
    assert plan.members_removed == [] and rels_of(out, MAIN) == rels_of(src, MAIN)
    assert [d.get("name") for d in xml(out).iter(WP + "docPr")].count("Picture 4") == 1


def test_a_picture_the_header_also_shows_keeps_its_media_until_both_go(src, pics):
    red = _media_of(src, png(8, 8, RED))
    plan, out, rec = one(src, "remove_images", {}, {"match": {"image": pics["red.png"]}, "tracked": False})
    assert rec.passed, explain(rec)
    assert plan.members_removed == [] and red in members(out)
    assert len(rels_of(out, MAIN)) == len(rels_of(src, MAIN)) - 1
    plan, out, rec = one(src, "remove_images", {"scope": ["document", "headers"]},
                         {"match": {"image": pics["red.png"]}, "tracked": False})
    assert rec.passed, explain(rec)
    assert plan.members_removed == [red] and red not in members(out) and rels_of(out, HEADER) == {}


def test_a_picture_with_a_hyperlink_takes_its_link_relationship_too(src):
    before = rels_of(src, MAIN)
    plan, out, rec = one(src, "remove_images", {}, {"match": {"name_regex": "^Linked badge$"}, "tracked": False})
    assert rec.passed, explain(rec)
    gone = set(before) - set(rels_of(out, MAIN))
    assert sorted(before[r][0] for r in gone) == ["hyperlink", "image"]


def test_an_svg_picture_takes_both_its_files_with_it(src):
    """Office 365's SVG picture: the blip shows a PNG copy and its svgBlip names the SVG — two relationships, two media
    members (the SVG's content type an Override); untracked, all of it goes; tracked, none of it."""
    from make_fixtures import add_parts
    image = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    data = add_parts(src, {"word/media/image9.svg": SVG}, rels=[("word/_rels/document.xml.rels", "rId90", image,
                                                                 "media/image9.svg")],
                     overrides={"word/media/image9.svg": "image/svg+xml"})
    data = mutate(data, MAIN, svg_blip("Detail 1", "rId90"))
    grey = _media_of(data, png(16, 8))
    plan, out, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Detail"}, "tracked": False})
    assert rec.passed, explain(rec)
    assert sorted(plan.members_removed) == sorted([grey, "word/media/image9.svg"])
    assert "image9.svg" not in read(out, CT).decode() and CT in plan.intended_parts
    plan, out, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Detail"}})
    assert rec.passed and plan.members_removed == [] and members(out) == members(data)
    _, out, rec = one(data, "resize_images", {}, {"match": {"name_regex": "^Detail"}, "scale": 50})
    assert rec.passed, explain(rec)


def test_tracked_removal_marks_each_picture_deleted_and_splits_a_shared_run(src):
    plan, out, rec = one(src, "remove_images", {}, {"match": "all"})
    assert rec.passed, explain(rec)
    root = xml(out)
    riser = paragraph_with(root, "B.\tThe riser diagram")
    kids = [c.tag for c in riser if c.tag != W + "pPr"]
    assert kids == [W + "r", W + "r", W + "del"]               # "B. The riser diagram: " | "riser " | the picture deleted
    assert riser[-1].find(f"{W}r/{W}drawing") is not None and riser[-1].find(f"{W}r/{W}t") is None
    assert all(d.getparent().getparent().tag == W + "del" for d in root.iter(W + "drawing"))
    assert plan.members_removed == [] and plan.rev_delta["del"] == 6 and members(out) == members(src)
    assert text_of(riser) == text_of(paragraph_with(xml(src), "B.\tThe riser diagram"))


def test_untracked_removal_of_a_run_holding_text_keeps_the_text(src, pics):
    plan, out, rec = one(src, "remove_images", {}, {"match": {"image": pics["green.png"]}, "tracked": False})
    assert rec.passed, explain(rec)
    riser = paragraph_with(xml(out), "B.\tThe riser diagram")
    assert text_of(riser) == "B.\tThe riser diagram: riser " and riser.find(f".//{W}drawing") is None


def test_a_clickable_picture_leaves_its_emptied_hyperlink_and_a_wrong_output_that_drops_it_is_caught(src):
    link = next(r for r, (t, _) in rels_of(src, MAIN).items() if t == "hyperlink")
    data = mutate(src, MAIN, in_hyperlink("Logo 2", link))
    plan, out, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Logo 2$"}, "tracked": False})
    assert rec.passed, explain(rec)
    after = paragraph_with(xml(out), "C.\tThe owner").getnext()          # the logo's paragraph
    assert [c.tag for c in after] == [W + "hyperlink"] and len(after[0]) == 0
    rec = suite(data, mutate(out, MAIN, lambda r: [h.getparent().remove(h) for h in list(r.iter(W + "hyperlink"))
                                                   if len(h) == 0]), plan)
    assert "V10" in failed(rec) and "not the paragraph the removal leaves" in v10(rec)
    plan, out, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Logo 2$"}})
    assert rec.passed, explain(rec)
    assert next(xml(out).iter(W + "hyperlink")).find(f"{W}del/{W}r/{W}drawing") is not None


def test_splitting_a_run_with_a_pending_formatting_change_is_refused(src, pics):
    data = mutate(src, MAIN, format_change("Riser diagram"))
    with pytest.raises(JobException) as e:
        one(data, "remove_images", {}, {"match": {"image": pics["green.png"]}})
    assert e.value.code == "tracked-format" and "tracked-format" in FAILURE_EXPLANATIONS
    _, _, rec = one(data, "remove_images", {}, {"match": {"image": pics["green.png"]}, "tracked": False})
    assert rec.passed, explain(rec)
    lone = mutate(src, MAIN, format_change("Logo 2"))                # alone in its run: wrapped whole, nothing copied
    _, _, rec = one(lone, "remove_images", {}, {"match": {"name_regex": "^Logo 2$"}})
    assert rec.passed, explain(rec)


def test_emptying_a_run_with_a_pending_formatting_change_is_refused(src, monkeypatch):
    # untracked, the run left with nothing but its properties goes: its w:rPrChange would go with it
    lone = mutate(src, MAIN, format_change("Picture 2"))
    with pytest.raises(JobException) as e:
        one(lone, "remove_images", {}, {"match": {"name_regex": "^Picture 2$"}, "tracked": False})
    assert e.value.code == "tracked-format" and "would go with it" in e.value.reason
    # a rule shared by plan and apply that lets it through: the verifier refuses on its own reading (and V4 counts it)
    monkeypatch.setattr(rm_module, "format_change_problem", lambda run, matched, tracked: False)
    plan, out, rec = one(lone, "remove_images", {}, {"match": {"name_regex": "^Picture 2$"}, "tracked": False})
    assert not list(xml(out).iter(W + "rPrChange"))
    assert {"V4", "V10"} <= set(failed(rec)) and "empties carries a pending formatting change" in v10(rec)
    monkeypatch.undo()
    # tracked, the same run is wrapped whole: the change stays with it, nothing copied
    plan, out, rec = one(lone, "remove_images", {}, {"match": {"name_regex": "^Picture 2$"}})
    assert rec.passed, explain(rec)
    assert len(list(xml(out).iter(W + "rPrChange"))) == 1


def test_a_picture_watermark_is_left_to_remove_watermark(src):
    rid = next(iter(rels_of(src, HEADER)))
    marked = mutate(src, HEADER, watermark(rid))
    plan, out, rec = one(marked, "remove_images", {"scope": ["headers"]}, {"match": "all", "tracked": False})
    assert rec.passed, explain(rec)
    assert any("1 watermark picture(s) left alone" in n for n in plan.notes)
    assert next(xml(out, HEADER).iter("{urn:schemas-microsoft-com:vml}imagedata")) is not None
    assert rels_of(out, HEADER) == rels_of(src, HEADER)          # the watermark still shows the logo's media


def test_no_matching_picture_is_set_aside_unless_allowed(src, pics):
    with pytest.raises(JobException) as e:
        one(src, "remove_images", {"scope": ["footers"]}, {"match": "all"})
    assert e.value.code == "not-found"
    plan, out, rec = one(src, "remove_images", {"scope": ["footers"]}, {"match": "all", "allow_missing": True})
    assert rec.passed and out == src


# ---- resize_images end to end ---------------------------------------------------------------------------------------

def _size_of(data: bytes, name: str, part: str = MAIN):
    return extent(drawing_named(xml(data, part), name))


@pytest.mark.parametrize("params,want", [
    ({"width": 2}, (2 * EMU, EMU)),                            # the floating detail: 96 × 48 pt → 2 × 1 in
    ({"height": 0.5}, (EMU, EMU // 2)),
    ({"width": 3, "height": 3}, (3 * EMU, 3 * EMU)),
    ({"scale": 50}, (609600, 304800)),
    ({"scale": 33.3}, (405994, 202997)),                       # 1219200 × 0.333 = 405993.6 → 405994
])
def test_a_floating_picture_is_resized_in_both_sizes(src, params, want):
    plan, out, rec = one(src, "resize_images", {}, {"match": {"name_regex": "^Detail"}, **params})
    assert rec.passed, explain(rec)
    assert _size_of(out, "Detail 1") == (want, want) and plan.mode == "formatting"
    anchor = drawing_named(xml(out), "Detail 1").find(WP + "anchor")
    assert anchor.find(f"{WP}positionH/{WP}posOffset").text == "0" and anchor.find(WP + "wrapSquare") is not None


def test_every_body_picture_by_scale_and_the_header_logo_by_file(src, pics):
    _, out, rec = one(src, "resize_images", {}, {"match": "all", "scale": 200})
    assert rec.passed, explain(rec)
    for name in ("Picture 2", "Riser diagram", "Picture 4", "Logo 2", "Detail 1", "Linked badge"):
        (w0, h0), _ = _size_of(src, name)
        assert _size_of(out, name) == ((2 * w0, 2 * h0),) * 2
    assert _size_of(out, "Logo 1", HEADER) == _size_of(src, "Logo 1", HEADER)
    _, out, rec = one(src, "resize_images", {"scope": ["headers"]}, {"match": {"image": pics["red.png"]}, "width": 1})
    assert rec.passed, explain(rec)
    assert _size_of(out, "Logo 1", HEADER) == ((EMU, EMU),) * 2 and _size_of(out, "Logo 2") == _size_of(src, "Logo 2")


def test_a_cropped_picture_resizes_and_keeps_its_crop(src):
    data = mutate(src, MAIN, cropped("Picture 2"))
    _, out, rec = one(data, "resize_images", {}, {"match": {"name_regex": "^Site plan"}, "width": 2})
    assert rec.passed, explain(rec)
    assert next(drawing_named(xml(out), "Picture 2").iter(A + "srcRect")).get("l") == "25000"


def test_a_picture_already_that_size_is_left_alone(src):
    (w, h), _ = _size_of(src, "Picture 2")
    plan, out, rec = one(src, "resize_images", {}, {"match": {"name_regex": "^Site plan"}, "width": w / EMU})
    assert rec.passed and out == src and any("1 picture(s) already that size" in n for n in plan.notes)


# ---- wrong outputs are caught -------------------------------------------------------------------------------------

@pytest.fixture()
def inserted(src, pics):
    plan, out, rec = one(src, "insert_image", SITE, {"image": pics["wide.png"], "description": "Site plan"})
    assert rec.passed
    return src, plan, out


def _new_drawing(root):
    return next(d for d in root.iter(W + "drawing") if _ours(d))


def test_a_wrong_inserted_size_or_id_or_alt_text_is_caught(inserted):
    src, plan, out = inserted
    caught(src, plan, out, MAIN, lambda r: _new_drawing(r).find(f"{WP}inline/{WP}extent").set("cx", "5943601"),
           "not the expected picture paragraph")
    caught(src, plan, out, MAIN, lambda r: _new_drawing(r).find(f"{WP}inline/{WP}docPr").set("id", "1"))
    caught(src, plan, out, MAIN, lambda r: _new_drawing(r).find(f"{WP}inline/{WP}docPr").set("descr", "Site"))
    caught(src, plan, out, MAIN, lambda r: _new_drawing(r).find(f"{WP}inline/{WP}docPr").attrib.pop("descr"))


def test_a_picture_in_the_wrong_place_is_caught(inserted):
    src, plan, out = inserted

    def moved(root):                                           # one paragraph further down: the anchor's text follows it
        p = paragraph_of(_new_drawing(root))
        p.getnext().addnext(p)
    caught(src, plan, out, MAIN, moved)

    def unmarked(root):
        p = paragraph_of(_new_drawing(root))
        p.find(f"{W}pPr/{W}rPr").remove(p.find(f"{W}pPr/{W}rPr/{W}ins"))
    rec = suite(src, mutate(out, MAIN, unmarked), plan)
    assert "V10" in failed(rec) and "V4" in failed(rec)


def test_wrong_media_bytes_a_wrong_relationship_or_content_type_is_caught(inserted, pics):
    src, plan, out = inserted
    bad = zipfile.ZipFile(io.BytesIO(out))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in bad.infolist():
            data = bad.read(info.filename)
            z.writestr(info, png(1200, 600, (1, 2, 3)) if info.filename == "word/media/image6.png" else data)
    rec = suite(src, buf.getvalue(), plan)
    assert failed(rec) == ["V10"] and "does not hold the picture file's bytes" in v10(rec)
    rec = caught(src, plan, out, "word/_rels/document.xml.rels",
                 lambda r: r[-1].set("Target", "media/image2.png"))
    assert "relationship" in v10(rec)
    rec = suite(src, mutate(out, CT, lambda r: etree.SubElement(r, r.tag.replace("Types", "Override"),
                                                                PartName="/word/media/image6.png",
                                                                ContentType="image/png")), plan)
    assert "V10" in failed(rec) and "[Content_Types].xml" in v10(rec)


@pytest.fixture()
def removed_untracked(src, pics):
    plan, out, rec = one(src, "remove_images", {}, {"match": {"image": pics["blue.png"]}, "tracked": False})
    assert rec.passed
    return src, plan, out


def test_a_picture_left_behind_or_another_removed_is_caught(removed_untracked, src, pics):
    _, plan, out = removed_untracked
    keep = xml(src)

    def put_back(root):                                       # the site plan's run back where it was
        run = drawing_named(keep, "Picture 2").getparent()
        paragraph_with(root, "A.\tSee the site plan").getnext().append(run)
    rec = suite(src, mutate(out, MAIN, put_back), plan)
    assert "V10" in failed(rec) or "V3" in failed(rec), rec.to_markdown()
    caught(src, plan, out, MAIN, lambda r: drawing_named(r, "Logo 2").getparent().remove(drawing_named(r, "Logo 2")))


def test_a_media_or_relationship_left_behind_is_caught(removed_untracked, src):
    _, plan, out = removed_untracked
    gone = plan.members_removed[0]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for info in zipfile.ZipFile(io.BytesIO(out)).infolist():
            z.writestr(info, zipfile.ZipFile(io.BytesIO(out)).read(info.filename))
        z.writestr(gone, read(src, gone))
    rec = suite(src, buf.getvalue(), plan)
    assert {"V1", "V10"} <= set(failed(rec)) and "no longer used but is still in the file" in v10(rec)


def test_a_tracked_removal_that_deletes_the_whole_run_with_its_text_is_caught(src):
    plan, out, rec = one(src, "remove_images", {}, {"match": {"name_regex": "^Riser"}})
    assert rec.passed

    def whole(root):                                           # "riser " and the picture back in one run, all deleted
        riser = paragraph_with(root, "B.\tThe riser diagram")
        text_run, wrap = riser[-2], riser[-1]
        t = text_run.find(W + "t")
        t.tag = W + "delText"
        wrap.find(W + "r").insert(0, t)
        riser.remove(text_run)
    rec = suite(src, mutate(out, MAIN, whole), plan)
    assert "V10" in failed(rec) and "not exactly the predicted change" in v10(rec)


@pytest.fixture()
def resized(src):
    plan, out, rec = one(src, "resize_images", {}, {"match": "all", "width": 2})
    assert rec.passed
    return src, plan, out


def test_a_wrong_new_size_or_a_left_inner_size_is_caught(resized):
    src, plan, out = resized
    caught(src, plan, out, MAIN, lambda r: drawing_named(r, "Detail 1").find(f"{WP}anchor/{WP}extent").set("cy", "1"),
           "size")
    caught(src, plan, out, MAIN, lambda r: next(drawing_named(r, "Detail 1").iter(A + "ext")).set("cx", "1219200"))
    caught(src, plan, out, MAIN, lambda r: drawing_named(r, "Detail 1").find(f"{WP}anchor/{WP}positionH/{WP}posOffset")
           .__setattr__("text", "9144"), "changed beyond its size")


# ---- wrong implementations shared by plan and apply ---------------------------------------------------------------

def test_a_shared_rounding_defect_in_the_placed_size_is_caught(src, pics, monkeypatch):
    real = op_images.half_up
    monkeypatch.setattr(op_images, "half_up", lambda x: int(x) if x != int(x) else real(x))       # truncates
    _, out, rec = one(src, "insert_image", MATERIALS, {"image": pics["wide.png"], "width": 0.333})
    assert extent(new_pictures(out)[0])[0] == (304495, 152247)
    assert "V10" in failed(rec) and "the plan inserts" in v10(rec)


def test_a_shared_reader_that_ignores_the_resolution_is_caught(src, pics, monkeypatch):
    real = op_images.read_picture
    monkeypatch.setattr(op_images, "read_picture", lambda data: real(data)._replace(emu_x=Fraction(9525),
                                                                                       emu_y=Fraction(9525)))
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["photo.jpg"]})
    assert extent(new_pictures(out)[0])[0] == (600 * 9525, 300 * 9525)
    assert "V10" in failed(rec) and "the plan inserts" in v10(rec)


def test_a_shared_text_width_that_forgets_the_margins_is_caught(src, pics, monkeypatch):
    monkeypatch.setattr(ins_module, "text_width", lambda sp: int(sp.find(W + "pgSz").get(W + "w")) * 635)
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["wide.png"]})
    assert extent(new_pictures(out)[0])[0][0] == int(8.5 * EMU)
    assert "V10" in failed(rec)


def test_a_shared_section_rule_off_by_one_is_caught(src, pics, monkeypatch):
    real = ins_module.section_of
    monkeypatch.setattr(ins_module, "section_of", lambda pars: [k + 1 for k in real(pars)])
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["wide.png"]})
    assert extent(new_pictures(out)[0])[0][0] == TEXT_WIDTH_1
    assert "V10" in failed(rec)


def test_a_shared_drawing_id_that_repeats_one_is_caught(src, pics, monkeypatch):
    monkeypatch.setattr(ins_module, "max_docpr_id", lambda pkg: 0)
    _, _, rec = one(src, "insert_image", SITE, {"image": pics["small.png"]})
    assert "V10" in failed(rec) and "not unique" in v10(rec)


def test_a_shared_content_type_rule_that_writes_an_override_is_caught(src, pics, monkeypatch):
    monkeypatch.setattr(ins_module, "ct_change", lambda pkg, ext, ct: "override")
    _, out, rec = one(src, "insert_image", SITE, {"image": pics["seal.gif"]})       # .gif has no Default: it needs one
    assert 'PartName="/word/media/image1.gif"' in read(out, CT).decode()
    assert "V10" in failed(rec) and "[Content_Types].xml" in v10(rec)


def test_a_shared_paragraph_without_its_mark_is_caught(src, pics, monkeypatch):
    real = ins_module.build_paragraph

    def no_mark(drawing, style, align, tracked, stamp, ids):
        p = real(drawing, style, align, tracked, stamp, ids)
        if p.find(W + "pPr") is not None:
            p.remove(p.find(W + "pPr"))
        return p
    monkeypatch.setattr(ins_module, "build_paragraph", no_mark)
    plan, _, rec = one(src, "insert_image", SITE, {"image": pics["small.png"]})
    assert "V10" in failed(rec) and "not the expected picture paragraph" in v10(rec)


def test_a_shared_match_that_widens_to_every_picture_is_caught(src, pics, monkeypatch):
    real = pic_module.match_rule
    monkeypatch.setattr(rm_module, "match_rule", lambda raw, what: {**real(raw, what), "how": "all"})
    _, _, rec = one(src, "remove_images", {}, {"match": {"image": pics["blue.png"]}, "tracked": False})
    assert "V10" in failed(rec) and "the plan removes pictures" in v10(rec)


def test_a_shared_field_rule_that_misses_a_field_is_caught(src, monkeypatch):
    data = mutate(src, MAIN, in_field("Picture 2"))
    monkeypatch.setattr(rm_module, "field_drawings", lambda root: set())
    _, _, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Site"}, "tracked": False})
    assert "V10" in failed(rec) and "shown by a field" in v10(rec)


def test_a_shared_rule_that_ignores_a_pending_change_is_caught(src, monkeypatch):
    data = mutate(src, MAIN, in_insertion("Logo 2"))
    monkeypatch.setattr(rm_module, "in_revision", lambda el, p: False)
    _, _, rec = one(data, "remove_images", {}, {"match": {"name_regex": "^Logo 2$"}, "tracked": False})
    assert "V10" in failed(rec) and "pending tracked change — the op must refuse" in v10(rec)


def test_a_shared_rule_that_takes_a_watermark_for_a_picture_is_caught(src, monkeypatch):
    data = mutate(src, HEADER, lambda r: docpr(r, "Logo 1").set("name", "WordPictureWatermark1"))
    plan, out, rec = one(data, "remove_images", {"scope": ["headers"]}, {"match": "all", "allow_missing": True})
    assert rec.passed and out == data and "1 watermark picture(s) left alone" in " ".join(plan.notes)
    monkeypatch.setattr(pic_module, "in_watermark", lambda ref: False)
    _, _, rec = one(data, "remove_images", {"scope": ["headers"]}, {"match": "all"})
    assert "V10" in failed(rec) and "watermark picture(s) here" in v10(rec)


def test_a_shared_rule_that_wraps_a_shared_run_whole_is_caught(src, monkeypatch):
    monkeypatch.setattr(rm_module, "stretches", lambda run, matched: [(True, rm_module.content_of(run))])
    _, out, rec = one(src, "remove_images", {}, {"match": {"name_regex": "^Riser"}})
    riser = paragraph_with(xml(out), "B.\tThe riser diagram")
    assert riser[-1].tag == W + "del" and riser[-1].find(f"{W}r/{W}t") is not None    # the text went in the deletion
    assert "V10" in failed(rec) or "V5c" in failed(rec)


def test_a_shared_rule_that_removes_a_relationship_still_in_use_is_caught(src, monkeypatch):
    """The site plan's relationship (the table's picture still uses it) and its media taken out with it, by plan and
    apply alike: the table's picture is left naming nothing."""
    real = rm_module.RemoveImages.plan

    def greedy(self, pkg, select, params):
        frag = real(self, pkg, select, params)
        rid = drawing_named(pkg.xml(MAIN), "Picture 2").find(f".//{A}blip").get(R + "embed")
        frag.transform_data["parts"][MAIN]["rids"] = [rid]
        frag.transform_data["members"] = [pkg.rels(MAIN)[rid].resolved]
        frag.members_removed = list(frag.transform_data["members"])
        frag.property_assertions[0]["rels_removed"] = {MAIN: [rid]}
        frag.property_assertions[0]["members_removed"] = list(frag.members_removed)
        return frag
    monkeypatch.setattr(rm_module.RemoveImages, "plan", greedy)
    _, _, rec = one(src, "remove_images", {}, {"match": {"name_regex": "^Site plan$"}, "tracked": False})
    assert {"V1", "V10"} <= set(failed(rec)), explain(rec)
    assert "relationships no longer used here {}" in v10(rec)


def test_a_shared_rounding_defect_in_the_new_size_is_caught(src, monkeypatch):
    monkeypatch.setattr(rs_module, "half_up", lambda x: int(x))
    _, out, rec = one(src, "resize_images", {}, {"match": {"name_regex": "^Detail"}, "scale": 33.3})
    assert _size_of(out, "Detail 1")[0] == (405993, 202996)
    assert "V10" in failed(rec) and "predicted sizes" in v10(rec)


def test_a_shared_rule_that_resizes_a_turned_picture_is_caught(src, monkeypatch):
    data = mutate(src, MAIN, turned("Detail 1"))
    monkeypatch.setattr(rs_module, "size_problem", lambda drawing: None)
    _, _, rec = one(data, "resize_images", {}, {"match": {"name_regex": "^Detail"}, "width": 1})
    assert "V10" in failed(rec) and "cannot be resized (turned)" in v10(rec)


# ---- refusals and parameters --------------------------------------------------------------------------------------

def test_pictures_that_cannot_be_inserted_are_refused(src, pics, tmp_path):
    (tmp_path / "turned.jpg").write_bytes(turned_jpeg(30, 12))
    for name, words in [(pics["logo.svg"], "SVG"), (pics["notes.png"], "not a PNG, JPEG, GIF or BMP picture"),
                        (str(tmp_path / "turned.jpg"), "quarter turn")]:
        with pytest.raises(JobException) as e:
            one(src, "insert_image", SITE, {"image": name})
        assert e.value.code == "image-format" and words in e.value.reason, e.value.reason
    with pytest.raises(JobException) as e:
        one(src, "insert_image", SITE, {"image": str(tmp_path / "missing.png")})
    assert e.value.code == "not-found"


def _break_paragraph_says(text: str):
    """The paragraph carrying the first section break gets the words ``text`` (python-docx leaves it empty)."""
    def fn(root):
        p = next(p for p in root.iter(W + "p") if p.find(f"{W}pPr/{W}sectPr") is not None)
        p.append(etree.fromstring(f'<w:r xmlns:w="{W[1:-1]}"><w:t>{text}</w:t></w:r>'))
    return fn


@pytest.mark.parametrize("damage,select,params,code", [
    (in_textbox("A.\tSee the site plan"), SITE, {}, "textbox"),
    (_break_paragraph_says("BREAK HERE"), {"paragraphs": {"text_regex": "^BREAK HERE"}}, {}, "section-break"),
    (no_page_size, SITE, {}, "image-size"),
    (None, SITE, {"width": 0.0000001}, "image-size"),
    (None, SITE, {"style": "NoSuchStyle"}, "missing-style"),
    (None, SITE, {"style": "EmphasisStrong"}, "style-mismatch"),
])
def test_anchors_and_sizes_that_cannot_be_planned_are_refused(src, pics, damage, select, params, code):
    data = mutate(src, MAIN, damage) if damage else src
    with pytest.raises(JobException) as e:
        one(data, "insert_image", select, {"image": pics["wide.png"], **params})
    assert e.value.code == code, e.value.reason
    assert code in FAILURE_EXPLANATIONS


def test_before_a_section_break_is_fine_and_a_size_given_needs_no_page(src, pics):
    data = mutate(src, MAIN, _break_paragraph_says("BREAK HERE"))
    _, _, rec = one(data, "insert_image", {"paragraphs": {"text_regex": "^BREAK HERE"}},
                    {"image": pics["wide.png"], "position": "before"})
    assert rec.passed, explain(rec)
    _, _, rec = one(mutate(src, MAIN, no_page_size), "insert_image", SITE, {"image": pics["wide.png"], "width": 2})
    assert rec.passed, explain(rec)


@pytest.mark.parametrize("params", [
    {"image": None}, {"image": 3}, {"width": 0}, {"width": -1}, {"width": True}, {"height": "2in"}, {"width": 101},
    {"align": "justify"}, {"description": ""}, {"description": "bell\x07"}, {"position": "inside"},
    {"tracked": "yes"}, {"allow_missing": 1}, {"date_convention": "local"}, {"colour": "red"}, {"style": 4},
    {"scope": ["headers"]},                                   # the anchors' scope is the selector's
])
def test_insert_parameters_are_checked(src, pics, params):
    with pytest.raises(JobException) as e:
        get_op("insert_image").plan(DocxPackage(src), SITE, {"image": pics["small.png"], **params})
    assert e.value.code in ("bad-params", "not-found"), params


@pytest.mark.parametrize("select", [{}, {"paragraphs": "all"}, {"tables": "all"}, {"scope": ["footnotes"]},
                                    {"scope": []}, {**SITE, "scope": "comments"}])
def test_insert_selectors_are_checked(src, pics, select):
    with pytest.raises(JobException) as e:
        get_op("insert_image").plan(DocxPackage(src), select, {"image": pics["small.png"]})
    assert e.value.code == "bad-params"


@pytest.mark.parametrize("damage,match,code,words", [
    (lambda r: vml("C.\tThe owner", next(d.find(f".//{A}blip").get(R + "embed")
                                         for d in r.iter(W + "drawing")))(r), "all", "structure", "VML"),
    (alternate("Picture 2"), "all", "structure", "AlternateContent"),
    (in_textbox("C.\tThe owner"), {"name_regex": "^Boxed"}, "textbox", "text box"),
    (in_insertion("Logo 2"), "all", "existing-revisions", "pending tracked change"),
    (in_field("Picture 2"), "all", "field-result", "field"),
])
def test_pictures_that_cannot_be_removed_are_refused(src, damage, match, code, words):
    data = mutate(src, MAIN, damage)
    for tracked in (True, False):
        with pytest.raises(JobException) as e:
            one(data, "remove_images", {}, {"match": match, "tracked": tracked})
        assert e.value.code == code and words in e.value.reason, e.value.reason


@pytest.mark.parametrize("damage,code,words", [
    (turned("Detail 1"), "structure", "turned"),
    (relative("Detail 1"), "structure", "follows the page"),
    (lambda r: drawing_named(r, "Detail 1").find(f"{WP}anchor/{WP}extent").set("cx", "1"), "structure", "differ"),
    (alternate("Detail 1"), "structure", "AlternateContent"),
])
def test_pictures_that_cannot_be_resized_are_refused(src, damage, code, words):
    with pytest.raises(JobException) as e:
        one(mutate(src, MAIN, damage), "resize_images", {}, {"match": {"name_regex": "^Detail"}, "width": 1})
    assert e.value.code == code and words in e.value.reason, e.value.reason


@pytest.mark.parametrize("op,params", [
    ("remove_images", {}), ("remove_images", {"match": "some"}), ("remove_images", {"match": {"md5": "xyz"}}),
    ("remove_images", {"match": "all", "tracked": "no"}), ("remove_images", {"match": "all", "scope": ["comments"]}),
    ("remove_images", {"match": "all", "width": 1}), ("remove_images", {"match": {"name_regex": "("}}),
    ("resize_images", {"match": "all"}), ("resize_images", {"match": "all", "width": 1, "tracked": True}),
    ("resize_images", {"match": "all", "scale": 50, "width": 1}), ("resize_images", {"match": "all", "scale": 0}),
    ("resize_images", {"match": "all", "scale": 5000}), ("resize_images", {"match": "all", "width": -2}),
    ("resize_images", {"match": "all", "width": 1, "allow_missing": "yes"}),
])
def test_remove_and_resize_parameters_are_checked(src, op, params):
    with pytest.raises(JobException) as e:
        get_op(op).plan(DocxPackage(src), {}, params)
    assert e.value.code == "bad-params", (op, params, e.value.reason)


def test_a_picture_file_changed_after_the_plan_is_refused_and_caught(src, pics):
    pkg = DocxPackage(src)
    frag = get_op("insert_image").plan(pkg, SITE, {"image": pics["small.png"]})
    Path(pics["small.png"]).write_bytes(png(9, 9))
    with pytest.raises(JobException) as e:
        get_op("insert_image").apply(pkg.copy(), frag)
    assert e.value.code == "image-changed"
    Path(pics["small.png"]).write_bytes(png(8, 8))
    plan, out, rec = one(src, "remove_images", {}, {"match": {"image": pics["blue.png"]}})
    assert rec.passed
    Path(pics["blue.png"]).write_bytes(png(8, 8, (1, 1, 1)))
    assert "changed since planning" in v10(suite(src, out, plan))


def test_every_refusal_code_of_the_picture_ops_is_explained():
    codes = set()
    for module in (ins_module, rm_module, rs_module, pic_module, op_images):
        for n in ast.walk(ast.parse(Path(module.__file__).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "JobException" and len(n.args) > 1:
                assert isinstance(n.args[1], ast.Constant), (module.__name__, n.lineno)
                codes.add(n.args[1].value)
    codes.discard("internal")
    assert {"image-format", "image-size", "textbox", "structure", "field-result", "existing-revisions",
            "section-break", "missing-style", "style-mismatch", "not-found", "bad-params", "image-changed"} <= codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []


# ---- touches, conflicts and composed steps --------------------------------------------------------------------------

def _frag(data, op, select, params):
    return get_op(op).plan(DocxPackage(data), select, params)


def test_touches_declare_the_changes_and_the_reads(src, pics):
    ins = set(_frag(src, "insert_image", SITE, {"image": pics["wide.png"]}).touches)
    assert {(MAIN, "*", "text"), ("word/_rels/document.xml.rels", "*", "rels"), ("word/media/image6.png", "*", "add"),
            (MAIN, "docPr", "ids"), (MAIN, "sectPr:0", "verify:pgSz"), (MAIN, "sectPr:0", "verify:pgMar"),
            (MAIN, "scope", "verify")} <= ins
    rm = set(_frag(src, "remove_images", {}, {"match": {"image": pics["red.png"]}, "tracked": False}).touches)
    assert ("word/_rels/document.xml.rels", "*", "rels") in rm and ("word/_rels/header1.xml.rels", "*", "verify") in rm
    rs = set(_frag(src, "resize_images", {}, {"match": {"name_regex": "^Detail"}, "width": 1}).touches)
    bi = next(i for i, p in enumerate(xml(src).iter(W + "p")) if text_of(p).startswith("D.\tA floating"))
    assert (MAIN, str(bi), "drawing") in rs


def test_swap_resize_and_remove_of_one_picture_conflict(src, pics):
    swap = _frag(src, "swap_image", {}, {"image": pics["wide.png"], "match": {"image": pics["red.png"]},
                                         "scope": ["headers"]})
    for op, params in (("resize_images", {"match": "all", "width": 1}), ("remove_images", {"match": "all"})):
        with pytest.raises(JobException) as e:
            merge_fragments([swap, _frag(src, op, {"scope": ["headers"]}, params)])
        assert e.value.code == "conflict", op


def test_two_insertions_conflict_and_the_engine_composes_them_into_steps(src, pics):
    a = _frag(src, "insert_image", SITE, {"image": pics["small.png"]})
    b = _frag(src, "insert_image", MATERIALS, {"image": pics["seal.gif"]})
    with pytest.raises(JobException) as e:
        merge_fragments([a, b])
    assert e.value.code == "conflict"
    spec = {"request": "two pictures", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_image", "select": SITE, "params": {"image": pics["small.png"]}},
                    {"op": "insert_image", "select": MATERIALS, "params": {"image": pics["seal.gif"]}}]}
    pkg = DocxPackage(src)
    assert stage_cuts(pkg, spec) == [1, 1]
    current = src
    for stage in split_into_stages(pkg, spec)["stages"]:         # each step planned on the step before it
        step = DocxPackage(current)
        plan, frags = plan_spec(step, stage["spec"])
        out = apply_spec(step, frags).output_bytes()
        rec = suite(current, out, plan)
        assert rec.passed, explain(rec)
        current = out
    ids = [d.get("id") for part in (MAIN, HEADER, FOOTER) for d in xml(current, part).iter(WP + "docPr")]
    new = [d.find(f"{WP}inline/{WP}docPr").get("id") for d in new_pictures(current)]
    assert len(new) == 2 and all(ids.count(i) == 1 for i in new)  # python-docx repeats ids across parts; ours never


def test_a_picture_beside_formatting_of_other_paragraphs_merges(src, pics):
    plan, out, rec = run_ops(src, [
        ("remove_images", {}, {"match": {"name_regex": "^Riser"}}),
        ("set_properties", {"paragraphs": {"text_regex": r"^C\.\tThe owner"}}, {"run": {"b": True}, "tracked": True}),
    ])
    assert rec.passed, explain(rec)


# ---- catalog, registration, the window, the batch pipeline, a render ----------------------------------------------

def test_catalog_schema_and_registration_points(src, pics, tmp_path):
    from spec_surgeon import compiler, inputs
    from spec_surgeon.editspec import SpecError, validate_spec
    cat, sheet = op_catalog(), op_sheet()
    for op in ("insert_image", "remove_images", "resize_images"):
        assert op in cat["ops"] and op in sheet["ops"] and COMMON_ASSUMPTIONS[op]
    spec = {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_image", "select": SITE, "params": {"image": pics["small.png"],
                                                                      "description": "Site plan, north up"}},
                    {"op": "remove_images", "params": {"match": {"image": pics["blue.png"]}}},
                    {"op": "resize_images", "params": {"match": {"image": pics["red.png"]}, "width": 1}}]}
    validate_spec(spec)
    with pytest.raises(SpecError):
        validate_spec({**spec, "ops": [{"op": "insert_image", "select": SITE, "params": {"width": 2}}]})
    assert "Site plan, north up" in compiler.placed_words(spec)
    assert spec_input_files(spec) == [Path(pics[n]).resolve() for n in ("small.png", "blue.png", "red.png")]
    assert inputs.KINDS[".bmp"] == "picture"
    found = inputs.overview(Path(pics["plan.bmp"]))
    assert found["format"] == "bmp" and found["pixels"] == [30, 20] and "problem" not in found


def test_the_window_takes_a_bmp_as_a_picture_for_the_edit(tmp_path, pics):
    from fake_claude import FakeClient
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    task = DocumentTask(Session(client=FakeClient(), out_root=tmp_path / "out", visual_check=False), render=False)
    task.attach([pics["plan.bmp"]])
    att = next(a for a in task.attachments if a.path.name == "plan.bmp")
    assert att.reference and att.state == "note" and "BMP picture, 30 × 20 pixels" in att.notes[0]


def test_a_spec_runs_through_the_batch_pipeline(src, pics, tmp_path):
    from spec_surgeon.batch import run_batch
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "21 13 13 – SPRINKLERS.docx").write_bytes(src)
    (folder / "21 13 16 – DRY-PIPE.docx").write_bytes(src)
    spec = {"request": "add the seal, halve the logo", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_image", "select": MATERIALS,
                     "params": {"image": pics["seal.gif"], "width": 1, "description": "Seal"}},
                    {"op": "resize_images", "select": {"scope": ["headers"]},
                     "params": {"match": {"image": pics["red.png"]}, "scale": 50}}]}
    batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=spec)
    assert [f.status for f in batch.files] == ["PASS", "PASS"], [f.exception for f in batch.files]
    out = Path(batch.files[0].delivered_to).read_bytes()
    assert any(n.endswith(".gif") for n in members(out))
    assert _size_of(out, "Logo 1", HEADER)[0] == (228600, 228600)
    spec = {"request": "drop the riser", "summary": "s", "mode": "untracked",
            "ops": [{"op": "remove_images", "params": {"match": {"image": pics["green.png"]}, "tracked": False}}]}
    batch = run_batch([str(folder)], "edit-spec", {}, tmp_path / "out2", workers=1, renderer="none", do_render=False,
                      spec=spec)
    assert [f.status for f in batch.files] == ["PASS", "PASS"], [f.exception for f in batch.files]


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_libreoffice_opens_and_renders_the_outputs(src, pics, tmp_path):
    from spec_surgeon.render import libreoffice
    outs = {}
    for name, (op, sel, params) in {
            "inserted": ("insert_image", SITE, {"image": pics["photo.jpg"], "align": "center"}),
            "inserted-bmp": ("insert_image", MATERIALS, {"image": pics["plan.bmp"], "width": 1, "tracked": False}),
            "removed": ("remove_images", {}, {"match": "all"}),
            "resized": ("resize_images", {}, {"match": "all", "scale": 150})}.items():
        _, out, rec = one(src, op, sel, params)
        assert rec.passed, explain(rec)
        outs[name] = tmp_path / f"{name}.docx"
        outs[name].write_bytes(out)
    pdfs = libreoffice.render_pdfs(list(outs.values()), tmp_path / "pdf", workers=1)
    if len(pdfs) != len(outs):
        pytest.skip("LibreOffice did not render here")
    for path in outs.values():
        pdf = pdfs[path]
        assert pdf.exists() and pdf.stat().st_size > 1000 and pdf.read_bytes().startswith(b"%PDF")
