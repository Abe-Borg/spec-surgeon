"""Describe cases for the sections family (built-in batch 6): insert_section_break and remove_section_break — (select,
params, [expected substrings]); the typical call first, then non-default settings. set_page_setup's layout cases live
with its other cases in pages.py."""

CASES = {
    "insert_section_break": [
        # the usual request: a new page (and section) before each appendix heading
        ({"paragraphs": {"text_regex": "^APPENDIX"}}, {},
         ["section break that starts a new page", "before paragraphs whose text matches the pattern “^APPENDIX”",
          "keeps the page setup, headers and footers", "page numbers run on", "not tracked"]),
        # a continuous break before a heading style, files without one left alone
        ({"paragraphs": {"style_in": ["Heading1"]}}, {"type": "continuous", "allow_missing": True},
         ["continuous section break", "same page", "“Heading1”", "files without such a paragraph are left unchanged"]),
        # an odd-page break: Word may add a blank page
        ({"paragraphs": {"text_regex": "^PART 2"}}, {"type": "odd_page"},
         ["next odd-numbered page", "blank page", "“^PART 2”"]),
    ],
    "remove_section_break": [
        # the usual request: one section for the whole document
        ({"sections": "all"}, {},
         ["Remove every section break", "one section", "page setup, headers and footers of the last section",
          "paragraph that held each break stays", "not tracked"]),
        # the cover joins the body
        ({"sections": [0]}, {},
         ["at the end of section 1, so those pages join the next section", "page-number restart"]),
        # chosen by content, files without such a section left alone
        ({"sections": {"containing": {"text_regex": "^APPENDIX"}}}, {"allow_missing": True},
         ["sections whose text matches the pattern “^APPENDIX”", "files where no section matches are left unchanged"]),
    ],
}
