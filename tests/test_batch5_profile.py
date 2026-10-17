"""Built-in batch 5: what the profiles show Claude about tables and lists (descriptive — the ops read the files
themselves). The example file's numbered styles are what a scheme's ``levels`` are named from."""
import json

from make_fixtures import build_numbering, build_rich, build_scheme_example, build_tables, build_typed

from spec_surgeon.package import DocxPackage
from spec_surgeon.profile import bound, profile


def _profile(data: bytes):
    return profile(DocxPackage(data, name="x.docx"))


def test_the_example_file_shows_its_scheme_as_numbered_styles():
    lists = _profile(build_scheme_example())["lists"]
    shown = [(e["style"], e["level"], e.get("shows")) for e in lists["numbered_styles"][:5]]
    assert shown == [("PRT", 0, "PART 1 -"), ("ART", 1, "1.1"), ("PR1", 2, "A."), ("PR2", 3, "1."), ("PR3", 4, "a.")]
    assert len({e["list"] for e in lists["numbered_styles"][:5]}) == 1            # one list: a scheme
    outline = lists["lists"][0]
    assert [lv.get("first") for lv in outline["levels"]] == ["PART 1 -", "1.1", "A.", "1.", "a."]
    assert "not_evaluated" not in outline


def test_unused_single_level_list_styles_are_left_out():
    """python-docx's List Number styles number nothing in these files: noise for a scheme."""
    lists = _profile(build_rich()).get("lists") or {}
    assert not lists.get("numbered_styles")


def test_lists_say_which_paragraphs_the_engine_does_not_evaluate():
    outline = _profile(build_numbering(shared_restart=True))["lists"]["lists"][0]
    assert outline["lists"] == [10, 11] and outline["not_evaluated"] == {"shared": outline["paragraphs"]}
    clean = _profile(build_numbering(shared_restart=False))["lists"]["lists"][0]
    assert "not_evaluated" not in clean and clean["levels"][1]["first"] == "1.01"
    assert clean["levels"][2]["styles"][0] == "ArchPara"


def test_typed_numbers_are_counted_with_tab_separators():
    lists = _profile(build_typed())["lists"]
    assert lists["typed_numbers"] >= 20 and "lists" not in lists


def test_tables_are_summarised_by_the_selector_s_ordinal():
    tables = _profile(build_tables())["tables"]
    assert tables["count"] == len(tables["first"]) + tables.get("more", 0)
    assert [t["index"] for t in tables["first"]] == list(range(len(tables["first"])))
    assert any(t.get("nested") for t in tables["first"]) and any(t["header_rows"] for t in tables["first"])
    assert "tables" not in _profile(build_scheme_example())              # a body without a table says nothing


def test_the_summaries_go_before_any_section_when_the_profile_is_too_big():
    prof = _profile(build_numbering(shared_restart=True))
    assert "lists" in prof and "tables" in prof
    for limit in (6000, 4500, 3500):
        cut = bound(json.loads(json.dumps(prof)), limit=limit)
        if len(cut.get("sections", [])) < len(prof["sections"]):
            assert "lists" not in cut and "tables" not in cut, limit
