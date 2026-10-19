"""Flexible matching preserves regex syntax on the independent selector implementations."""
import pytest

from make_fixtures import build
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops.selectors import _selector_regex, check_paragraph_selector
from spec_surgeon.package import DocxPackage, W, serialize_xml
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.props import _v_selector_regex


@pytest.mark.parametrize("pattern,text,expected", [
    (r"^a {2}b$", "a\u00a0 b", True),
    (r"^[] ] foo$", "]\tfoo", True),
    (r"^[] ] foo$", "\t foo", False),
    (r"^[^] ] foo$", "A\tfoo", True),
    (r"^[^] ] foo$", " \tfoo", False),
    (r"^[^] ] foo$", "] foo", False),
    (r"^a\ b$", "a  b", False),
    (r"^a[ ]b$", "a\tb", False),
    (r"^a\[ b$", "a[\tb", True),
    (r"^a\] b$", "a]\tb", True),
    (r"(?x)^foo  bar$", "foobar", True),
    (r"(?x)^foo  bar$", "foo bar", False),
    (r"^(?x:foo  bar)$", "foobar", True),
    ("(?x)^foo # keep this comment\n bar$", "foobar", True),
])
def test_flexible_selector_regex_respects_quantifiers_classes_escapes_and_verbose(pattern, text, expected):
    options = {"flexible_whitespace": True}
    check_paragraph_selector({"text_regex": pattern, **options})
    for compile_regex in (_selector_regex, _v_selector_regex):
        assert (compile_regex(pattern, options).search(text) is not None) is expected


def test_quantified_flexible_selector_plans_and_is_independently_verified():
    package = DocxPackage(build("21 05 00", "SPRINKLERS"))
    root = package.xml("word/document.xml")
    root.find(".//" + W + "t").text = "a\u00a0 b"
    package.write("word/document.xml", serialize_xml(root))
    original = package.output_bytes()
    package = DocxPackage(original)
    operation = get_op("set_properties")
    selection = {"paragraphs": {"text_regex": r"^a {2}b$", "flexible_whitespace": True}}
    fragment = operation.plan(package, selection, {"paragraph": {"keepNext": True}})
    plan = merge_fragments([fragment])
    output = package.copy()
    operation.apply(output, fragment)
    record = run_suite(original, output.output_bytes(), plan.to_dict(), "before.docx", "after.docx")
    assert record.passed, record.to_markdown()
    assert output.xml("word/document.xml").find(".//" + W + "keepNext") is not None


@pytest.mark.parametrize("selector", [
    {"text_regex": "("},
    {"text_regex": "(?<=a )b"},
    {"after_heading_regex": "(?<=a )b"},
    {"between_paragraphs": {"start_regex": "start", "end_regex": "(?<=a )b"}},
])
def test_matching_options_compile_errors_are_plan_time_refusals(selector):
    with pytest.raises(JobException) as exception:
        check_paragraph_selector({**selector, "flexible_whitespace": True})
    assert exception.value.code == "bad-params"
