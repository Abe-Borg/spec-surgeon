"""V5a chooses packaged schemas for relationship parts with arbitrary filenames."""
from __future__ import annotations

import pytest
from lxml import etree

from spec_surgeon.verify import xsd


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
