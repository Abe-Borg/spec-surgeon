"""Approved numbering outcomes bind source-derived targets, never execution plans."""
from __future__ import annotations

import hashlib

import pytest

from make_fixtures import SCHEME_LEVELS, build_numbering, build_scheme_example, build_typed, patch_docx
from spec_surgeon.ops import get_op
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify.goal_numbering import derive
from spec_surgeon.verify.props import Ctx, KINDS, Styles
from spec_surgeon.verify.reader import STYLES_REL, VPackage, W

AUTHOR = "Approved Reviewer"
MAIN = "word/document.xml"


@pytest.fixture
def scheme(tmp_path):
    path = tmp_path / "numbering.docx"
    path.write_bytes(build_scheme_example())
    return path


def _effect(kind, scheme=None, **params):
    effect = {"id": "numbering-approved", "type": kind, "select": {}, "expected_count": "all",
              "params": {"levels": list(SCHEME_LEVELS), "author": AUTHOR, **params}}
    if scheme is not None:
        effect.update(template=str(scheme), template_sha256=hashlib.sha256(scheme.read_bytes()).hexdigest())
    return effect


def _apply(data, effect):
    package = DocxPackage(data)
    params = dict(effect["params"])
    if "template" in effect:
        params["template"] = effect["template"]
    op = get_op("convert_typed_numbers" if effect["type"] == "typed_numbering" else "convert_numbering")
    fragment = op.plan(package, effect["select"], params)
    working = package.copy()
    op.apply(working, fragment)
    return working.output_bytes()


class _SourceOnly:
    """Any accidental access to the candidate, its plan or joint views fails."""
    def __init__(self, package):
        self.src = package
        part = package.related(STYLES_REL)
        self.src_styles = Styles(package.xml(part) if part else None)

    def __getattr__(self, name):
        raise AssertionError("derivation accessed non-source context: " + name)


def _check(source, output, effect):
    before, after = VPackage(source), VPackage(output)
    try:
        assertions, count = derive(_SourceOnly(before), effect)
        ctx = Ctx(before, after, {"property_assertions": assertions})
        errors = []
        for assertion in assertions:
            KINDS[assertion["kind"]](ctx, assertion, errors)
        return assertions, count, errors
    finally:
        before.zip.close()
        after.zip.close()


@pytest.mark.parametrize("kind,params", [
    ("numbering", {}),
    ("numbering", {"tracked": True}),
    ("numbering", {"direct_formatting": "clear"}),
    ("numbering", {"from": {"styles": [["ArchPart"], ["ArchArt"], ["ArchPara", "ArchParaAlt"],
                                         ["ArchSub"], ["ArchSub2"]]}}),
    ("numbering", {"levels": list(SCHEME_LEVELS[:3]), "level_map": {"3": 2, "4": 2}}),
    ("typed_numbering", {}),
    ("typed_numbering", {"tracked": False}),
    ("typed_numbering", {"tracked": False, "direct_formatting": "clear"}),
])
def test_independent_assertions_verify_numbering_outputs_without_reading_plans(scheme, kind, params):
    source = build_typed() if kind == "typed_numbering" else build_numbering(shared_restart=False)
    effect = _effect(kind, scheme, **params)
    output = _apply(source, effect)
    assertions, count, errors = _check(source, output, effect)
    assert not errors, errors
    assert count > 0
    assert count == len(assertions[0]["converted"])
    assert assertions[1]["kind"] == "styles_imported"
    assert all(assertion["op_index"] == effect["id"] for assertion in assertions)


def test_numbering_counts_paragraphs_already_in_the_approved_scheme(scheme):
    source = _apply(build_numbering(shared_restart=False), _effect("numbering", scheme))
    effect = _effect("numbering")
    assertions, count, errors = _check(source, source, effect)
    assert not errors, errors
    assert count > 0
    assert assertions[0]["changed"] == []
    assert assertions[0]["expected_paragraphs"] == 0


def test_shared_source_list_converts_as_one_complete_outline(scheme):
    source = build_numbering(shared_restart=True)
    effect = _effect("numbering", scheme)
    output = _apply(source, effect)
    _, count, errors = _check(source, output, effect)
    assert count > 0
    assert not errors, errors


def test_typed_table_selection_is_derived_from_source(scheme):
    source = build_typed()
    effect = _effect("typed_numbering", scheme, tracked=False)
    effect["select"] = {"include_tables": True}
    output = _apply(source, effect)
    _, count, errors = _check(source, output, effect)
    assert count > 0
    assert not errors, errors


@pytest.mark.parametrize("kind", ["numbering", "typed_numbering"])
def test_output_omitting_approved_conversion_is_rejected(scheme, kind):
    source = build_typed() if kind == "typed_numbering" else build_numbering(shared_restart=False)
    _, count, errors = _check(source, source, _effect(kind, scheme))
    assert count > 0
    assert errors


def test_corrupt_imported_number_is_rejected(scheme):
    source = build_numbering(shared_restart=False)
    effect = _effect("numbering", scheme)
    output = _apply(source, effect)

    def corrupt(root):
        abstract = root.findall(W + "abstractNum")[-1]
        abstract.find(W + "lvl/" + W + "lvlText").set(W + "val", "UNAPPROVED %1")

    output = patch_docx(output, {"word/numbering.xml": corrupt})
    _, _, errors = _check(source, output, effect)
    assert any("number" in error or "imported lists" in error for error in errors), errors


def test_corrupt_typed_paragraph_content_is_rejected(scheme):
    source = build_typed()
    effect = _effect("typed_numbering", scheme, tracked=False)
    output = _apply(source, effect)

    def corrupt(root):
        for element in root.iter(W + "t"):
            if element.text == "GENERAL":
                element.text = "UNAPPROVED CONTENT"
                return
        pytest.fail("fixture has no converted GENERAL paragraph")

    output = patch_docx(output, {MAIN: corrupt})
    _, _, errors = _check(source, output, effect)
    assert errors


def test_ambiguous_or_wrong_typed_sequence_refuses_from_source(scheme):
    source = VPackage(build_typed(typo=True))
    try:
        with pytest.raises(ValueError, match="visible line"):
            derive(_SourceOnly(source), _effect("typed_numbering", scheme))
    finally:
        source.zip.close()


def test_template_changed_since_approval_is_rejected(scheme):
    effect = _effect("numbering", scheme)
    scheme.write_bytes(scheme.read_bytes() + b"changed")
    source = VPackage(build_numbering(shared_restart=False))
    try:
        with pytest.raises(ValueError, match="template changed"):
            derive(_SourceOnly(source), effect)
    finally:
        source.zip.close()


def test_contract_author_is_used_when_params_omit_it(scheme):
    source = VPackage(build_typed())
    effect = _effect("typed_numbering", scheme)
    del effect["params"]["author"]
    effect["_assertion"] = {"author": "Contract Author"}
    try:
        assertions, _ = derive(_SourceOnly(source), effect)
        assert assertions[0]["author"] == "Contract Author"
    finally:
        source.zip.close()
