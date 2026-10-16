"""swap_image (docs/BUILTIN_OPS_BATCH1_DESIGN.md §8): the swap end to end (match by file, md5, name, all; contain and
stretch; PNG, JPEG and GIF), a picture shared with the body, the refusals, the two picture readers and the two fitted
sizes compared, and planted defects — a wrong OUTPUT and wrong IMPLEMENTATIONS shared by plan and apply — each caught by
V10 (and V1 where the package itself is wrong)."""
from __future__ import annotations

import hashlib
import io
import random
import re
import struct
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List

import pytest
from lxml import etree

from make_fixtures import build_batch1, build_rich, png
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import images as op_images
from spec_surgeon.ops import swap_image as swap_module
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.images import v_fit, v_picture

HEADER = "word/header1.xml"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
BOX = 457200                                     # build_batch1's logo: 36 pt square


# ---- pictures -------------------------------------------------------------------------------------------------------

def png_wh(w: int, h: int, rgb=(10, 200, 30)) -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_wh(w: int, h: int) -> bytes:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h), False)
    pix.clear_with(180)
    return pix.tobytes("jpeg")


def gif_wh(w: int, h: int) -> bytes:
    """A GIF header with a w × h logical screen (two-colour table, one 1×1 frame: enough for every reader here)."""
    return (b"GIF89a" + struct.pack("<HH", w, h) + bytes([0x80, 0, 0]) + b"\x00\x00\x00\xff\xff\xff"
            + b"\x2c" + struct.pack("<HHHH", 0, 0, 1, 1) + b"\x00" + b"\x02\x02\x44\x01\x00" + b"\x3b")


def exif_jpeg(w: int, h: int, orientation=None, order: str = "II", sof: int = 0xC0, before: bytes = b"") -> bytes:
    """A JPEG's header up to its frame header: optional Exif orientation (in either byte order), any SOF marker."""
    def seg(marker: int, body: bytes) -> bytes:
        return b"\xff" + bytes([marker]) + struct.pack(">H", len(body) + 2) + body
    out = b"\xff\xd8" + before + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    if orientation is not None:
        e = "<" if order == "II" else ">"
        ifd = struct.pack(e + "H", 1) + struct.pack(e + "HHI", 0x0112, 3, 1) + struct.pack(e + "HH", orientation, 0)
        out += seg(0xE1, b"Exif\x00\x00" + order.encode() + struct.pack(e + "HI", 42, 8) + ifd + b"\x00\x00\x00\x00")
    out += seg(sof, b"\x08" + struct.pack(">HH", h, w) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01")
    return out + b"\xff\xd9"


# ---- running ---------------------------------------------------------------------------------------------------------

def swap(src: bytes, params: Dict[str, Any], select: Dict[str, Any] = None):
    pkg = DocxPackage(src)
    frag = get_op("swap_image").plan(pkg, select or {}, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("swap_image").apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def suite(src: bytes, out: bytes, plan) -> Any:
    return run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def v10_errors(rec) -> List[str]:
    return rec.get("V10").data.get("errors", [])


def sizes(docx: bytes, part: str = HEADER) -> List[tuple]:
    root = DocxPackage(docx).xml(part)
    out = []
    for d in root.iter(W + "drawing"):
        fr = d[0]
        e = fr.find(WP + "extent")
        x = fr.find(f"{A}graphic/{A}graphicData/{PIC}pic/{PIC}spPr/{A}xfrm/{A}ext")
        out.append(((int(e.get("cx")), int(e.get("cy"))), (int(x.get("cx")), int(x.get("cy")))))
    return out


def mutate(docx: bytes, part: str, fn) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == part:
                if part.endswith((".xml", ".rels")):
                    root = etree.fromstring(data)
                    fn(root)
                    data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                else:
                    data = fn(data)
            zout.writestr(info, data)
    return buf.getvalue()


def drop_member(docx: bytes, name: str) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            if info.filename != name:
                zout.writestr(info, zin.read(info.filename))
    return buf.getvalue()


def add_member_bytes(docx: bytes, name: str, data: bytes) -> bytes:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        zout.writestr(name, data)
    return buf.getvalue()


def media(docx: bytes) -> Dict[str, str]:
    z = zipfile.ZipFile(io.BytesIO(docx))
    return {n: hashlib.md5(z.read(n)).hexdigest() for n in z.namelist() if "/media/" in n}


@pytest.fixture()
def files(tmp_path):
    old = tmp_path / "old-logo.png"
    old.write_bytes(png((10, 80, 160), size=8))                  # build_batch1's header logo, byte for byte
    wide = tmp_path / "new-logo.png"
    wide.write_bytes(png_wh(40, 10))
    return {"old": old, "wide": wide, "dir": tmp_path}


# ---- the swap end to end -------------------------------------------------------------------------------------------

def test_swap_by_old_file_fits_contain_and_replaces_the_media(files):
    src = build_batch1()
    plan, out, rec = swap(src, {"image": str(files["wide"]), "match": {"image": str(files["old"])}})
    assert not failed(rec), [(c.id, c.summary) for c in rec.checks if c.status == "FAIL"]
    assert sizes(out) == [((BOX, BOX // 4), (BOX, BOX // 4))]          # 40 × 10 px inside 36 × 36 pt: full width
    assert plan.members_added == ["word/media/image2.png"] and plan.members_removed == ["word/media/image1.png"]
    assert media(out) == {"word/media/image2.png": hashlib.md5(files["wide"].read_bytes()).hexdigest()}
    rels = zipfile.ZipFile(io.BytesIO(out)).read("word/_rels/header1.xml.rels").decode()
    assert 'Id="rId1"' in rels and 'Target="media/image2.png"' in rels
    assert "[Content_Types].xml" not in plan.intended_parts           # png already has its Default
    assert plan.geometry_reference == "none"
    assert rec.get("V10").status == "PASS" and "1 picture(s) swapped" in rec.get("V10").summary


@pytest.mark.parametrize("match", ["md5", "name", "all"])
def test_every_kind_of_match_finds_the_logo(files, match):
    src = build_batch1()
    m = {"md5": {"md5": hashlib.md5(files["old"].read_bytes()).hexdigest().upper()},
         "name": {"name_regex": r"^Picture \d+$"}, "all": "all"}[match]
    _, out, rec = swap(src, {"image": str(files["wide"]), "match": m})
    assert not failed(rec)
    assert sizes(out) == [((BOX, BOX // 4), (BOX, BOX // 4))]


def test_stretch_fills_the_old_box_and_tall_pictures_keep_the_height(files):
    src = build_batch1()
    _, out, rec = swap(src, {"image": str(files["wide"]), "match": "all", "fit": "stretch"})
    assert not failed(rec) and sizes(out) == [((BOX, BOX), (BOX, BOX))]
    tall = files["dir"] / "tall.png"
    tall.write_bytes(png_wh(3, 7))
    _, out, rec = swap(src, {"image": str(tall), "match": "all"})
    assert not failed(rec)
    assert sizes(out)[0][0] == ((2 * BOX * 3 + 7) // 14, BOX) == (195943, BOX)      # 195942.857… rounded half up


def test_jpeg_keeps_the_content_types_and_gif_adds_an_override(files):
    src = build_batch1()
    jpg = files["dir"] / "logo.jpg"
    jpg.write_bytes(jpeg_wh(30, 12))
    plan, out, rec = swap(src, {"image": str(jpg), "match": "all"})
    assert not failed(rec) and plan.members_added == ["word/media/image1.jpeg"]
    assert "[Content_Types].xml" not in plan.intended_parts
    assert sizes(out)[0][0] == (BOX, 182880)
    gif = files["dir"] / "logo.gif"
    gif.write_bytes(gif_wh(20, 20))
    plan, out, rec = swap(src, {"image": str(gif), "match": "all"})
    assert not failed(rec), [(c.id, c.summary) for c in rec.checks if c.status == "FAIL"]
    assert plan.members_added == ["word/media/image1.gif"] and "[Content_Types].xml" in plan.intended_parts
    ct = zipfile.ZipFile(io.BytesIO(out)).read("[Content_Types].xml").decode()
    assert 'PartName="/word/media/image1.gif" ContentType="image/gif"' in ct


def test_a_picture_the_body_shares_keeps_its_media(tmp_path):
    """build_rich: the cover's default header shows the same picture as the body (one media member, two
    relationships). Swapping the headers' blue logo retargets only the header's relationship; the body keeps its
    picture and the old member stays. The first-page header's red logo is not matched."""
    src = build_rich()
    blue = tmp_path / "blue.png"
    blue.write_bytes(png((30, 30, 200)))
    new = tmp_path / "new.png"
    new.write_bytes(png_wh(16, 8))
    before = media(src)
    plan, out, rec = swap(src, {"image": str(new), "match": {"image": str(blue)}})
    assert not failed(rec), [(c.id, c.summary) for c in rec.checks if c.status == "FAIL"]
    assert plan.members_removed == [] and len(plan.members_added) == 1
    after = media(out)
    assert {k: v for k, v in after.items() if k in before} == before          # nothing old changed or went
    # with the body in scope too, the old picture is no longer used anywhere: it goes
    plan, out, rec = swap(src, {"image": str(new), "match": {"image": str(blue)}, "scope": ["headers", "document"]})
    assert not failed(rec)
    assert len(plan.members_removed) == 1 and plan.members_removed[0] not in media(out)


def test_a_picture_that_already_shows_the_new_one_is_left_alone(files):
    src = build_batch1()
    _, out, _ = swap(src, {"image": str(files["wide"]), "match": "all"})
    plan, out2, rec = swap(out, {"image": str(files["wide"]), "match": "all"})
    assert not failed(rec) and out2 == out
    assert plan.members_added == [] and any("already show the new picture" in n for n in plan.notes)


def test_no_matching_picture_is_set_aside_unless_allowed(files):
    src = build_batch1()
    other = files["dir"] / "other.png"
    other.write_bytes(png_wh(5, 5, (1, 1, 1)))
    with pytest.raises(JobException) as e:
        swap(src, {"image": str(files["wide"]), "match": {"image": str(other)}})
    assert e.value.code == "not-found"
    plan, out, rec = swap(src, {"image": str(files["wide"]), "match": {"image": str(other)}, "allow_missing": True})
    assert not failed(rec) and out == src
    with pytest.raises(JobException) as e:                                   # the body has no picture
        swap(src, {"image": str(files["wide"]), "match": "all", "scope": ["document"]})
    assert e.value.code == "not-found"


def test_alt_text_is_kept_and_flagged(files):
    src = mutate(build_batch1(), HEADER, lambda root: next(root.iter(WP + "docPr")).set("descr", "ACME logo"))
    plan, out, rec = swap(src, {"image": str(files["wide"]), "match": "all"})
    assert not failed(rec)
    assert any("ACME logo" in f for f in plan.flags)
    assert next(DocxPackage(out).xml(HEADER).iter(WP + "docPr")).get("descr") == "ACME logo"


# ---- refusals --------------------------------------------------------------------------------------------------------

def _blip(root):
    return next(root.iter(A + "blip"))


def _svg(root):
    ext_lst = etree.SubElement(_blip(root), A + "extLst")
    ext = etree.SubElement(ext_lst, A + "ext", uri="{96DAC541-7B7A-43D3-8B79-37D633B846F1}")
    svg = etree.SubElement(ext, "{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip")
    svg.set(R + "embed", "rId1")


def _crop(root):
    _blip(root).addnext(etree.Element(A + "srcRect", l="2500"))


def _turn(root):
    next(root.iter(A + "xfrm")).set("rot", "5400000")


def _unequal(root):
    next(root.iter(A + "ext")).set("cx", "400000")


def _linked(root):
    b = _blip(root)
    b.set(R + "link", b.get(R + "embed"))
    del b.attrib[R + "embed"]


def _alternate(root):
    d = next(root.iter(W + "drawing"))
    run = d.getparent()
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    ac = etree.Element(f"{{{mc}}}AlternateContent")
    choice = etree.SubElement(ac, f"{{{mc}}}Choice", Requires="wp14")
    run.replace(d, ac)
    choice.append(d)


def _vml(root):
    p = next(root.iter(W + "p"))
    v, o = "urn:schemas-microsoft-com:vml", "urn:schemas-microsoft-com:office:office"
    r = etree.SubElement(p, W + "r")
    pict = etree.SubElement(r, W + "pict")
    shape = etree.SubElement(pict, f"{{{v}}}shape", alt="old logo")
    data = etree.SubElement(shape, f"{{{v}}}imagedata", {f"{{{o}}}title": "logo"})
    data.set(R + "id", "rId1")


def _twin(root):
    """A second drawing sharing the logo's relationship, named differently."""
    d = next(root.iter(W + "drawing"))
    twin = etree.fromstring(etree.tostring(d.getparent()))
    next(twin.iter(WP + "docPr")).set("name", "Other 2")
    d.getparent().addnext(twin)


@pytest.mark.parametrize("damage,match,code,words", [
    (_svg, "all", "structure", "SVG"),
    (_crop, "all", "structure", "cropped"),
    (_turn, "all", "structure", "turned"),
    (_unequal, "all", "structure", "differ"),
    (_linked, "all", "structure", "linked"),
    (_alternate, "all", "structure", "AlternateContent"),
    (_vml, "all", "structure", "VML"),
    (_vml, "old", "structure", "VML"),
    (_vml, {"name_regex": "^Picture"}, "structure", "also used by imagedata"),
    (_twin, {"name_regex": "^Picture"}, "structure", "also used by blip"),
])
def test_pictures_that_cannot_be_swapped_are_refused(files, damage, match, code, words):
    src = mutate(build_batch1(), HEADER, damage)
    m = {"image": str(files["old"])} if match == "old" else match
    with pytest.raises(JobException) as e:
        swap(src, {"image": str(files["wide"]), "match": m})
    assert e.value.code == code and words in e.value.reason, e.value.reason


def test_bad_new_pictures_and_params_are_refused(files):
    src = build_batch1()
    d = files["dir"]
    (d / "logo.svg").write_text('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>')
    (d / "notes.png").write_text("not a picture")
    (d / "turned.jpg").write_bytes(exif_jpeg(30, 12, orientation=6))
    for name, words in [("logo.svg", "SVG"), ("notes.png", "not a PNG, JPEG or GIF"), ("turned.jpg", "quarter turn")]:
        with pytest.raises(JobException) as e:
            swap(src, {"image": str(d / name), "match": "all"})
        assert e.value.code == "bad-params" and words in e.value.reason, e.value.reason
    with pytest.raises(JobException) as e:
        swap(src, {"image": str(d / "missing.png"), "match": "all"})
    assert e.value.code == "not-found"
    for params in [{"match": "all", "fit": "cover"}, {"match": {"md5": "xyz"}}, {"match": {"image": str(files["old"]),
                   "md5": "0" * 32}}, {"match": "some"}, {"match": "all", "scope": ["footnotes"]},
                   {"match": {"image": str(files["wide"])}}, {"match": "all", "allow_missing": "yes"}]:
        with pytest.raises(JobException) as e:
            swap(src, {"image": str(files["wide"]), **params})
        assert e.value.code in ("bad-params", "not-found"), (params, e.value.reason)


def test_apply_refuses_a_picture_changed_after_the_plan(files):
    src = build_batch1()
    pkg = DocxPackage(src)
    frag = get_op("swap_image").plan(pkg, {}, {"image": str(files["wide"]), "match": "all"})
    files["wide"].write_bytes(png_wh(41, 10))
    with pytest.raises(JobException) as e:
        get_op("swap_image").apply(pkg.copy(), frag)
    assert e.value.code == "image-changed"


def test_v10_fails_when_the_picture_changes_after_the_run(files):
    src = build_batch1()
    plan, out, rec = swap(src, {"image": str(files["wide"]), "match": {"image": str(files["old"])}})
    assert not failed(rec)
    files["old"].write_bytes(png_wh(9, 9))
    assert any("old picture file" in e for e in v10_errors(suite(src, out, plan)))
    files["wide"].write_bytes(png_wh(41, 10))
    assert any("changed since planning" in e for e in v10_errors(suite(src, out, plan)))


# ---- the two readers and the two fitted sizes agree ----------------------------------------------------------------

def _outcome(fn, data):
    try:
        return fn(data)
    except (ValueError, struct.error):
        return "refused"


def _crafted() -> List[bytes]:
    cases = [png_wh(1, 1), png_wh(4000, 3), png((1, 2, 3)), gif_wh(1, 1), gif_wh(640, 480),
             b"GIF87a" + gif_wh(7, 9)[6:], jpeg_wh(30, 12), jpeg_wh(1, 1)]
    for o in (None, 1, 2, 3, 4, 5, 6, 7, 8, 9):
        for order in ("II", "MM"):
            cases.append(exif_jpeg(300, 120, o, order))
    for sof in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC9, 0xCF):
        cases.append(exif_jpeg(64, 48, sof=sof))
    cases += [exif_jpeg(64, 48, before=b"\xff\xff\xff\xd0\xff\x01"),            # fill bytes, a lone RST and TEM
              exif_jpeg(0, 48), png_wh(1, 1)[:20], b"\xff\xd8\xff\xda\x00\x02", b"\xff\xd8\xff\xc4\x00\x02\xff\xd9",
              b"", b"<svg/>", b"\x89PNG\r\n\x1a\n" + b"\x00" * 30, b"GIF89a\x00\x00\x05\x00", b"\xff\xd8",
              exif_jpeg(10, 10)[:-12]]
    return cases


def test_the_two_picture_readers_agree_on_crafted_and_damaged_files():
    cases = _crafted()
    rnd = random.Random(20261016)
    for base in list(cases[:12]):
        for _ in range(60):
            b = bytearray(base)
            for _ in range(rnd.randint(1, 4)):
                if b:
                    b[rnd.randrange(min(len(b), 80))] = rnd.randrange(256)
            cases.append(bytes(b[:rnd.randint(0, len(b))] if rnd.random() < 0.3 else b))
    seen = set()
    for data in cases:
        a, b = _outcome(op_images.sniff, data), _outcome(v_picture, data)
        assert a == b, (data[:40], a, b)
        seen.add(a if a == "refused" else a[0])
    assert {"png", "gif", "jpeg", "refused"} <= seen
    assert _outcome(op_images.sniff, exif_jpeg(300, 120, 6, "MM")) == "refused"
    assert _outcome(op_images.sniff, exif_jpeg(300, 120, 3, "MM")) == ("jpeg", 300, 120)


def test_the_two_fitted_sizes_agree():
    rnd = random.Random(7)
    boxes = [(BOX, BOX), (1, 1), (914400, 1), (3, 7), (10 ** 9, 10 ** 9 - 1)] + [
        (rnd.randint(1, 5_000_000), rnd.randint(1, 5_000_000)) for _ in range(400)]
    pixels = [(1, 1), (3, 7), (7, 3), (4000, 1), (1, 4000), (65535, 65535)] + [
        (rnd.randint(1, 5000), rnd.randint(1, 5000)) for _ in range(400)]
    for fit in ("contain", "stretch"):
        for i in range(800):
            box, px = boxes[i % len(boxes)], pixels[(i * 7) % len(pixels)]
            try:
                a = op_images.fitted(box, px, fit)
            except JobException:
                a = "refused"
            try:
                b = v_fit(box, px, fit)
            except ValueError:
                b = "refused"
            assert a == b, (box, px, fit, a, b)


# ---- planted defects: a wrong output ----------------------------------------------------------------------------------

@pytest.fixture()
def swapped(files):
    src = build_batch1()
    plan, out, rec = swap(src, {"image": str(files["wide"]), "match": {"image": str(files["old"])}})
    assert not failed(rec)
    return src, plan, out


def test_a_wrong_size_is_caught(swapped):
    src, plan, out = swapped
    bad = mutate(out, HEADER, lambda root: next(root.iter(WP + "extent")).set("cy", str(BOX // 4 + 1)))
    rec = suite(src, bad, plan)
    assert failed(rec) == ["V10"] and any("size" in e for e in v10_errors(rec))
    bad = mutate(out, HEADER, lambda root: next(root.iter(A + "ext")).set("cy", str(BOX)))     # inner size left old
    assert failed(suite(src, bad, plan)) == ["V10"]


def test_anything_else_changed_in_the_drawing_is_caught(swapped):
    src, plan, out = swapped
    bad = mutate(out, HEADER, lambda root: next(root.iter(WP + "docPr")).set("descr", "new"))
    rec = suite(src, bad, plan)
    assert "V10" in failed(rec) and any("changed beyond its size" in e for e in v10_errors(rec))
    bad = mutate(out, HEADER, lambda root: next(root.iter(A + "off")).set("x", "5"))
    assert "V10" in failed(suite(src, bad, plan))


def test_wrong_bytes_in_the_new_member_are_caught(swapped):
    src, plan, out = swapped
    bad = mutate(out, "word/media/image2.png", lambda data: png_wh(40, 10, (0, 0, 0)))
    rec = suite(src, bad, plan)
    assert failed(rec) == ["V10"] and any("bytes" in e for e in v10_errors(rec))


def test_an_old_picture_left_behind_is_caught(swapped, files):
    src, plan, out = swapped
    bad = add_member_bytes(out, "word/media/image1.png", files["old"].read_bytes())
    rec = suite(src, bad, plan)
    assert {"V1", "V10"} <= set(failed(rec))
    assert any("no longer used but is still in the file" in e for e in v10_errors(rec))


def test_retargeting_a_picture_out_of_scope_is_caught(tmp_path):
    src = build_rich()
    blue = tmp_path / "blue.png"
    blue.write_bytes(png((30, 30, 200)))
    new = tmp_path / "new.png"
    new.write_bytes(png_wh(16, 8))
    plan, out, rec = swap(src, {"image": str(new), "match": {"image": str(blue)}})
    assert not failed(rec)
    target = plan.members_added[0].rsplit("/", 1)[1]

    def retarget_body(root):
        for rel in root:
            if rel.get("Type", "").endswith("/image"):
                rel.set("Target", "media/" + target)
    bad = mutate(out, "word/_rels/document.xml.rels", retarget_body)
    rec = suite(src, bad, plan)
    assert "V10" in failed(rec) and any("document.xml relationship" in e for e in v10_errors(rec))


# ---- planted defects: wrong implementations shared by plan and apply ---------------------------------------------

def test_a_shared_rounding_defect_is_caught(files, monkeypatch):
    tall = files["dir"] / "tall.png"
    tall.write_bytes(png_wh(3, 7))

    def floor_fit(box, px, fit):
        (bw, bh), (pw, ph) = box, px
        return (bw, bw * ph // pw) if bw * ph <= bh * pw else (bh * pw // ph, bh)
    monkeypatch.setattr(swap_module, "fitted", floor_fit)
    _, out, rec = swap(build_batch1(), {"image": str(tall), "match": "all"})
    assert sizes(out)[0][0] == (195942, BOX)
    assert failed(rec) == ["V10"] and any("predicted sizes" in e for e in v10_errors(rec))


def test_a_shared_picture_reader_defect_is_caught(files, monkeypatch):
    real = op_images.sniff
    monkeypatch.setattr(op_images, "sniff", lambda data: (lambda f, w, h: (f, h, w))(*real(data)))     # w and h swapped
    _, out, rec = swap(build_batch1(), {"image": str(files["wide"]), "match": "all"})
    assert sizes(out)[0][0] == (BOX // 4, BOX)
    errs = v10_errors(rec)
    assert failed(rec) == ["V10"] and any("the plan read the picture as png 10 × 40" in e for e in errs)


def test_a_shared_matching_defect_is_caught(tmp_path, monkeypatch):
    """The matcher widened to every picture: the first-page header's red logo is swapped too, by plan and apply
    alike. The verifier reads the user's match itself and finds the red picture's relationship changed."""
    src = build_rich()
    blue = tmp_path / "blue.png"
    blue.write_bytes(png((30, 30, 200)))
    new = tmp_path / "new.png"
    new.write_bytes(png_wh(16, 8))
    real = swap_module.match_rule
    monkeypatch.setattr(swap_module, "match_rule", lambda raw: {**real(raw), "how": "all"})
    _, _, rec = swap(src, {"image": str(new), "match": {"image": str(blue)}})
    errs = v10_errors(rec)
    assert "V10" in failed(rec) and any("predicted sizes" in e for e in errs)
    assert any("relationship" in e and "changed" in e for e in errs)


def test_a_shared_defect_that_ignores_a_shared_relationship_is_caught(files, monkeypatch):
    monkeypatch.setattr(swap_module, "shared_use", lambda root, rids, own: None)
    src = mutate(build_batch1(), HEADER, _twin)
    _, out, rec = swap(src, {"image": str(files["wide"]), "match": {"name_regex": "^Picture"}})
    errs = v10_errors(rec)
    assert "V10" in failed(rec) and any("also used by blip" in e for e in errs), errs


def test_a_shared_defect_that_swaps_a_cropped_picture_is_caught(files, monkeypatch):
    monkeypatch.setattr(swap_module, "picture_problem", lambda drawing, blip: None)
    src = mutate(build_batch1(), HEADER, _crop)
    _, _, rec = swap(src, {"image": str(files["wide"]), "match": "all"})
    errs = v10_errors(rec)
    assert "V10" in failed(rec) and any("cannot be swapped (cropped)" in e for e in errs), errs


def test_a_shared_defect_that_removes_a_picture_still_in_use_is_caught(tmp_path, monkeypatch):
    src = build_rich()
    blue = tmp_path / "blue.png"
    blue.write_bytes(png((30, 30, 200)))
    new = tmp_path / "new.png"
    new.write_bytes(png_wh(16, 8))
    monkeypatch.setattr(swap_module, "unused_after", lambda pkg, retargeted, olds: sorted(olds))
    plan, out, rec = swap(src, {"image": str(new), "match": {"image": str(blue)}})
    assert plan.members_removed                                  # the defect: the body's picture goes too
    assert {"V1", "V10"} <= set(failed(rec))
    assert any("still used but changed or went" in e for e in v10_errors(rec))


# ---- with other ops ----------------------------------------------------------------------------------------------------

def test_a_text_edit_of_the_logos_paragraph_is_a_conflict_and_the_body_is_not(files):
    pkg = DocxPackage(build_batch1())
    swap_frag = get_op("swap_image").plan(pkg, {}, {"image": str(files["wide"]), "match": "all"})
    stamp = get_op("set_issue_stamp").plan(pkg, {}, {"pattern": r"ISSUED FOR PERMIT \S+", "text": "ISSUED FOR BID",
                                                     "scope": ["headers"], "author": "A"})
    with pytest.raises(JobException) as e:
        merge_fragments([swap_frag, stamp])
    assert e.value.code == "conflict"
    spec = {"request": "swap the logo and rename part 1", "summary": "s", "mode": "content",
            "ops": [{"op": "swap_image", "params": {"image": str(files["wide"]), "match": "all"}},
                    {"op": "replace_table", "params": {"pairs": [{"find": "GENERAL", "replace": "GENERAL TERMS"}],
                                                       "scope": ["document"], "author": "A",
                                                       "allow_missing": True}}]}
    src = build_batch1()
    plan, frags = plan_spec(DocxPackage(src), spec)
    out = apply_spec(DocxPackage(src), frags).output_bytes()
    rec = suite(src, out, plan)
    assert not failed(rec), [(c.id, c.summary[:400]) for c in rec.checks if c.status == "FAIL"]


def test_removing_the_headers_and_swapping_their_logo_is_a_conflict(files):
    spec = {"request": "r", "summary": "s", "mode": "formatting",
            "ops": [{"op": "swap_image", "params": {"image": str(files["wide"]), "match": "all"}},
                    {"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["header"]}}]}
    with pytest.raises(JobException) as e:
        plan_spec(DocxPackage(build_batch1()), spec)
    assert e.value.code == "conflict"


def test_a_batch_swaps_every_file_and_pins_the_picture(files, tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.docx").write_bytes(build_batch1("21 13 19"))
    (folder / "b.docx").write_bytes(build_batch1("21 13 13"))
    spec = {"request": "swap the logo", "summary": "s", "mode": "formatting",
            "ops": [{"op": "swap_image", "params": {"image": str(files["wide"]), "match": {"image": str(files["old"])}}}]}
    batch = run_batch([str(folder)], "", {}, tmp_path / "out", workers=1, do_render=False, spec=spec)
    assert [fr.status for fr in batch.files] == ["PASS", "PASS"]
    for fr in batch.files:
        assert sizes(Path(fr.delivered_to).read_bytes())[0][0] == (BOX, BOX // 4)
