"""V5a chooses packaged schemas for relationship parts with arbitrary filenames."""
from __future__ import annotations

import pytest
from lxml import etree

from make_fixtures import build_rich
from spec_surgeon.editspec import apply_spec, plan_spec
from spec_surgeon.package import DocxPackage
from spec_surgeon.verify import run_suite, xsd


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
P15 = "http://schemas.microsoft.com/office/word/2012/wordml"
P16 = "http://schemas.microsoft.com/office/word/2016/wordml/cid"
P18 = "http://schemas.microsoft.com/office/word/2018/wordml/cex"


@pytest.mark.parametrize("part,source", [
    ("parts/main.data", f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Original</w:t></w:r></w:p></w:body></w:document>'),
    ("notes/reviewer.data", f'<w:comments xmlns:w="{W}"><w:comment w:id="0" w:author="Reviewer"><w:p><w:r><w:t>Reason</w:t></w:r></w:p></w:comment></w:comments>'),
    ("parts/core.xml", f'<w:document xmlns:w="{W}"><w:body><w:p/></w:body></w:document>'),
])
def test_core_word_parts_are_validated_without_filename_assumptions(part, source):
    original = source.encode()
    assert xsd.schema_for(part, original) == xsd.SCHEMA_FOR["word"]
    assert xsd.validate_part(part, original) == set()
    root = etree.fromstring(original)
    etree.SubElement(root, "{%s}notAllowed" % W)
    baseline, output, added = xsd.compare(part, original, etree.tostring(root))
    assert baseline == set()
    assert output and added


@pytest.mark.parametrize("namespace,root_name,child_name,attributes", [
    (P15, "commentsEx", "commentEx", {"paraId": "00000001", "done": "0"}),
    (P16, "commentsIds", "commentId", {"paraId": "00000001", "durableId": "00000002"}),
    (P18, "commentsExtensible", "commentExtensible", {"durableId": "00000002"}),
])
@pytest.mark.parametrize("directory,suffix", [("notes", "data"), ("word", "xml")])
def test_modern_comment_children_are_checked_instead_of_stripped(namespace, root_name, child_name, attributes, directory, suffix):
    root = etree.Element("{%s}%s" % (namespace, root_name), nsmap={"m": namespace})
    etree.SubElement(root, "{%s}%s" % (namespace, child_name),
                     {"{%s}%s" % (namespace, key): value for key, value in attributes.items()})
    part = f"{directory}/review-metadata.{suffix}"
    original = etree.tostring(root)
    assert xsd.validate_part(part, original) == set()
    etree.SubElement(root, "{%s}unapprovedChild" % namespace)
    baseline, output, added = xsd.compare(part, original, etree.tostring(root))
    assert baseline == set()
    assert output and added


def test_changing_a_renamed_parts_namespace_cannot_downgrade_to_no_schema():
    original = f'<w:comments xmlns:w="{W}"/>'.encode()
    baseline, output, added = xsd.compare("notes/reviewer.data", original, b'<unsupported xmlns="urn:unknown"/>')
    assert baseline == set()
    assert output and added


def test_unknown_extension_namespace_still_reports_no_packaged_schema():
    assert xsd.validate_part("notes/vendor.data", b'<unknown xmlns="urn:vendor-extension"/>') is None


def test_core_word_extension_cleanup_is_unchanged_for_renamed_parts():
    original = f'<w:comments xmlns:w="{W}" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">' \
        '<w:comment w:id="0" w:author="Reviewer"><w:p w14:paraId="00000001"/></w:comment></w:comments>'
    assert xsd.validate_part("notes/reviewer.data", original.encode()) == set()


def _verify(data: bytes, ops, mode="untracked", tamper=None):
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, {"request": "r", "summary": "s", "mode": mode, "ops": ops})
    out = apply_spec(pkg, frags).output_bytes()
    if tamper:
        out = tamper(out)
    return run_suite(pkg.data, out, plan.to_dict(), "src.docx", "out.docx")


SETTINGS_HEAD = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">')


def test_v5a_judges_a_settings_part_against_the_source_in_schema_order():
    """libxml2 stops checking a sequence at the first child it does not expect: a w:trackRevisions placed first hides a
    w:zoom without its required w:percent. Turning track changes off then let V5a reach the zoom error and call it new
    on a correct output. The baseline also validates the source with its children in schema order — every error that
    copy shows is the source's own — and a really new error is still new."""
    src = (SETTINGS_HEAD + '<w:trackRevisions/><w:zoom w:val="bestFit"/><w:defaultTabStop w:val="720"/>'
           '</w:settings>').encode()
    off = (SETTINGS_HEAD + '<w:zoom w:val="bestFit"/><w:defaultTabStop w:val="720"/></w:settings>').encode()
    assert xsd.validate_part("word/settings.xml", src) == set()          # the zoom error is out of reach here
    assert xsd.validate_part("word/settings.xml", off) != set()          # and in reach once the element goes
    base, out, new = xsd.compare("word/settings.xml", src, off)
    assert out and new == set()
    broken = (SETTINGS_HEAD + '<w:zoom w:val="bestFit"/><w:defaultTabStop/></w:settings>').encode()
    assert xsd.compare("word/settings.xml", src, broken)[2] != set()     # a missing w:val is the output's own
    fine = (SETTINGS_HEAD + '<w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/></w:settings>').encode()
    assert xsd.in_schema_order(fine) is None                              # in order: nothing more to validate
    assert xsd.compare("word/settings.xml", fine, broken.replace(b'w:val="bestFit"', b'w:percent="100"'))[2] != set()
    rec = _verify(build_rich(), [{"op": "set_document_state", "params": {"track_changes": "off"}}], mode="formatting")
    assert [c.id for c in rec.checks if c.status == "FAIL"] == []
