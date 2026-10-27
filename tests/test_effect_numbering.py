"""A whole-document font effect reaches the list labels (backlog P3). The font operations set every used level's
label font, so a copy's numbering part differs from the original — and the outcome check refused every such copy
("unapproved package-member change: word/numbering.xml"). The check now reads the used levels from the original
itself and approves exactly that change: the asked font on every used, non-symbol level's label, nothing else in
the part. Anything else there, a targeted font effect's change, or a change it cannot read, still fails closed; a
method that leaves the labels alone still passes."""
from __future__ import annotations

import pytest

from spec_surgeon.goals import resolve_goal
from spec_surgeon.verify.effects import verify_effects
from spec_surgeon.verify.reader import VPackage, W
from test_font_numbering import NUMBERING, font, level, mutate, numbered  # noqa: F401 — the fixture
from test_goal_contracts import contract, exact, execute

FONT_ALL = {"id": "font", "type": "format", "target": {"all": True}, "font": {"name": "Arial", "size": 11}}


@pytest.fixture
def source(tmp_path, numbered):
    path = tmp_path / "numbered.docx"
    path.write_bytes(numbered)
    return path


def bound(source, goal):
    binding = resolve_goal(source, goal)["binding"]
    return execute(source, goal, binding), binding


def errors_of(source, output, goal, binding):
    result = verify_effects(source, output, goal, binding)
    assert not result["passed"], result
    return "\n".join(result["errors"])


def test_a_whole_document_font_effect_passes_with_the_labels_in_the_font(source):
    goal = contract(FONT_ALL)
    output, binding = bound(source, goal)                     # execute asserts the outcome check passed
    root = VPackage(output).xml(NUMBERING)
    rf = font(level(root, 10, 0))
    assert all(rf.get(W + slot) == "Arial" for slot in ("ascii", "hAnsi", "cs", "eastAsia"))
    assert rf.get(W + "asciiTheme") is None
    assert all(font(level(root, 21)).get(W + slot) == "Wingdings" for slot in ("ascii", "hAnsi", "cs"))  # the bullet
    assert verify_effects(source, output, goal, binding)["passed"]


def with_part(data: bytes, part: str, content: bytes) -> bytes:
    """``data`` with ``part`` holding ``content`` (every other member as it was)."""
    import io
    import zipfile
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            dst.writestr(info, content if info.filename == part else src.read(info.filename))
    return out.getvalue()


def test_a_method_that_leaves_the_labels_alone_still_passes(source):
    goal = contract(FONT_ALL)
    output, binding = bound(source, goal)
    untouched = with_part(output, NUMBERING, VPackage(source.read_bytes()).read(NUMBERING))
    assert VPackage(untouched).read(NUMBERING) != VPackage(output).read(NUMBERING)
    result = verify_effects(source, untouched, goal, binding)
    assert result["passed"], result


@pytest.mark.parametrize("damage", ["other_font", "size", "number_text", "bullet_glyph", "start"])
def test_anything_else_in_the_numbering_part_still_fails_closed(source, damage):
    goal = contract(FONT_ALL)
    output, binding = bound(source, goal)

    def change(root):
        if damage == "other_font":
            font(level(root, 10, 0)).set(W + "ascii", "Calibri")
        elif damage == "size":
            level(root, 10, 1).find(W + "rPr/" + W + "sz").set(W + "val", "24")
        elif damage == "number_text":
            level(root, 10, 0).find(W + "lvlText").set(W + "val", "SECTION %1 -")
        elif damage == "bullet_glyph":
            for slot in ("ascii", "hAnsi", "cs"):
                font(level(root, 21)).set(W + slot, "Arial")
        elif damage == "start":
            level(root, 10, 1).find(W + "start").set(W + "val", "7")
    said = errors_of(source, mutate(output, NUMBERING, change), goal, binding)
    assert "word/numbering.xml" in said and ("numbering_labels" in said or "unapproved package-member change" in said)


def test_a_targeted_font_effect_never_approves_a_label_change(source):
    goal = contract({"id": "title", "type": "format", "target": exact("SUMMARY"), "font": {"name": "Arial"}})
    output, binding = bound(source, goal)
    assert VPackage(output).read(NUMBERING) == VPackage(source.read_bytes()).read(NUMBERING)
    said = errors_of(source, mutate(output, NUMBERING, lambda root: font(level(root, 10, 0)).set(W + "ascii", "Calibri")),
                     goal, binding)
    assert "unapproved package-member change: word/numbering.xml" in said


def test_labels_the_verifier_cannot_read_are_never_approved(source, monkeypatch):
    goal = contract(FONT_ALL)
    output, binding = bound(source, goal)
    import spec_surgeon.verify.numberinglabels as labels

    def unreadable(*a, **k):
        raise ValueError("planted: the list definitions cannot be read")
    monkeypatch.setattr(labels, "v_label_rows", unreadable)
    said = errors_of(source, output, goal, binding)
    assert "cannot be read" in said and "unapproved package-member change: word/numbering.xml" in said
