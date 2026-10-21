"""Built-in batch 6 across ops (docs/BUILTIN_OPS_BATCH6_DESIGN.md §0 rule 4, "tracked like Word tracks it"): every op that
adds or removes content writes revisions this engine's own resolve_tracked_changes can settle — accepting the tracked
output reads exactly like the untracked output, rejecting it reads exactly like the source — on one ordinary spec that
already carries someone else's pending changes (only the new author's are resolved)."""
from __future__ import annotations

import struct
import zlib

import pytest

from make_fixtures import build
from spec_surgeon.ooxml import block_full_text, paragraphs
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.package import W, DocxPackage
from spec_surgeon.verify import run_suite

AUTHOR = "Batch Six Reviewer"
SPEC = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")
PICTURED = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS", body_picture=True)
ARTICLE = {"between_paragraphs": {"start_regex": r"^3\.01", "end_regex": r"^4\.01", "include_bounds": "start"}}


def _png(width: int, height: int) -> bytes:
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


CASES = {
    "delete an article": (SPEC, "delete_paragraphs", {"paragraphs": ARTICLE}, {}),
    "insert paragraphs with segments, a tab and a break": (
        SPEC, "insert_paragraph", {"paragraphs": {"text_regex": r"^3\.01"}},
        {"position": "after", "paragraphs": [{"text": "First\tline\nsecond"},
                                             {"runs": [{"text": "Bold ", "b": True}, {"text": "plain"}]}]}),
    "replace with a tab": (SPEC, "replace_text", {"scope": ["document"]},
                           {"find": "FM Global", "replace": "FM\tGlobal", "occurrences": "all"}),
    "move an article": (SPEC, "move_paragraphs", {"paragraphs": ARTICLE},
                        {"to": {"text_regex": r"^10\.01"}, "position": "before"}),
    "delete a table": (SPEC, "delete_tables", {"tables": "all"}, {}),
    "delete a row": (SPEC, "delete_table_rows", {"tables": "all", "rows": {"text_regex": "^0"}}, {}),
    "insert a row": (SPEC, "insert_table_rows", {"tables": "all", "rows": {"text_regex": "^0"}},
                     {"rows": [["1", "2026-02-01", "Issued for Bid"]], "position": "after"}),
    "insert a table": (SPEC, "insert_table", {"paragraphs": {"text_regex": r"^1\.01"}},
                       {"rows": [["Item", "Size"], ["Pipe", "2 in"]], "position": "after", "header_rows": 1}),
    "insert a field": (SPEC, "insert_field", {"scope": ["document"], "paragraphs": {"text_regex": "^GENERAL$"}},
                       {"field": "DATE", "position": "end", "format": "MMMM d, yyyy"}),
    "insert a picture": (SPEC, "insert_image", {"paragraphs": {"text_regex": "^GENERAL$"}},
                         {"image": "LOGO", "position": "after", "width": 1.5}),
    "remove the pictures": (PICTURED, "remove_images", {"scope": ["document"]}, {"match": "all"}),
}


def _run(src: bytes, op: str, select: dict, params: dict):
    pkg = DocxPackage(src)
    frag = get_op(op).plan(pkg, select, params)
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op(op).apply(work, frag)
    out = work.output_bytes()
    return out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


def _reading(data: bytes):
    """What a reader sees: every paragraph's text of the body, headers and footers, and each table's row count."""
    pkg = DocxPackage(data)
    kinds = pkg.parts_by_kind()
    parts = [pkg.main_part()] + kinds["headers"] + kinds["footers"]
    text = {part: [block_full_text(p) for p in paragraphs(pkg.xml(part))] for part in parts}
    rows = [len([r for r in t if r.tag == W + "tr"]) for t in pkg.xml(pkg.main_part()).iter(W + "tbl")]
    return text, rows


@pytest.mark.parametrize("case", sorted(CASES))
def test_accepting_reads_like_untracked_and_rejecting_like_the_source(case, tmp_path):
    src, op, select, params = CASES[case]
    if params.get("image") == "LOGO":
        logo = tmp_path / "logo.png"
        logo.write_bytes(_png(120, 40))
        params = {**params, "image": str(logo)}
    clean, rec = _run(src, op, select, {**params, "tracked": False, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    tracked, rec = _run(src, op, select, {**params, "tracked": True, "author": AUTHOR})
    assert rec.passed, rec.to_markdown()
    for action, expected in (("accept", clean), ("reject", src)):
        resolved, rec = _run(tracked, "resolve_tracked_changes", {}, {"action": action, "authors": [AUTHOR]})
        assert rec.passed, rec.to_markdown()
        assert _reading(resolved) == _reading(expected), (case, action)


def test_a_tracked_deletion_over_someone_elses_pending_change_refuses_and_untracked_goes_through():
    with pytest.raises(JobException) as e:
        _run(SPEC, "delete_paragraphs", {"paragraphs": "all"}, {"tracked": True, "author": AUTHOR})
    assert e.value.code == "existing-revisions"
    out, rec = _run(SPEC, "delete_paragraphs", {"paragraphs": "all"}, {"tracked": False})
    assert rec.passed, rec.to_markdown()
    assert all(not t for t in _reading(out)[0][DocxPackage(out).main_part()])
