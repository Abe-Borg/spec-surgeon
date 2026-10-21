"""Describe cases for built-in batch 6's fields and comment threads: insert_field, reply_to_comments and
resolve_comments (docs/BUILTIN_OPS_BATCH6_DESIGN.md §9). Each case is (select, params, [expected substrings])."""

CASES = {
    "insert_field": [
        ({"scope": ["footers"]}, {"field": "PAGE", "at_text": "{{PAGE}}"},
         ["Replace every “{{PAGE}}” in the footers with a page number field, shown as a tracked change",
          "The field is inserted empty; Word fills in the number as it lays out the pages"]),
        ({}, {"field": "DATE", "at_text": "[DATE]", "format": "MMMM d, yyyy", "tracked": False},
         ["Replace every “[DATE]” in the document body, the headers and the footers with a field showing today's date "
          "written as “MMMM d, yyyy”, without tracked changes",
          "marked for updating; Word fills it in when it opens the file"]),
        ({"scope": ["footers"], "paragraphs": {"text_regex": "^Page"}},
         {"field": "NUMPAGES", "position": "end", "format": "ROMAN", "author": "Abe"},
         ["Add a field showing the number of pages in capital roman numerals (I, II, III) at the end of the paragraphs "
          "whose text matches the pattern “^Page” in the footers, shown as a tracked change by “Abe”"]),
        ({"scope": ["headers"], "paragraphs": {"text_regex": "^SECTION"}},
         {"field": "STYLEREF", "style": "Heading 1", "position": "start", "last_on_page": True},
         ["Add a field showing the nearest text in the “Heading 1” style (the last one on the page) at the start of"]),
        ({"scope": ["document"], "paragraphs": {"style_in": ["Title"]}},
         {"field": "DOCPROPERTY", "property": "Project Number", "at_text": "XXXX", "allow_missing": True},
         ["Replace every “XXXX” in the document body, in paragraphs in the “Title” style, with a field showing the "
          "document property “Project Number”", "Files with no such place are left unchanged"]),
        ({}, {"field": "FILENAME", "at_text": "<file>", "path": True},
         ["a field showing the file name with its folder"]),
        ({}, {"field": "SECTIONPAGES", "at_text": "{{PAGES}}", "format": "alphabetic"},
         ["the number of pages in the section in small letters (a, b, c)"]),
    ],
    "reply_to_comments": [
        ({}, {"text": "Done, see revised 2.3.", "author": "Abe Borg", "initials": "AB"},
         ["Reply “Done, see revised 2.3.” as “Abe Borg” (initials “AB”) to every comment thread, one reply per thread "
          "beneath its first comment", "the document's text stay as they are"]),
        ({}, {"text": "Agreed.", "author": "Abe", "which": "authors", "authors": ["Jack", "Jeremy"],
              "allow_missing": True},
         ["to every comment thread holding a comment by “Jack” or “Jeremy”",
          "a file with no such thread is left unchanged"]),
        ({}, {"text": "Will check.", "author": "Abe", "which": "text_regex", "text_regex": "listing"},
         ["to every comment thread holding a comment that matches “listing”"]),
    ],
    "resolve_comments": [
        ({}, {}, ["Mark every comment thread resolved, as Word's Resolve does",
                  "Threads already resolved are left alone"]),
        ({}, {"action": "reopen", "which": "authors", "authors": ["Jack"]},
         ["Reopen every resolved comment thread holding a comment by “Jack”", "Threads already open are left alone"]),
        ({}, {"which": "text_regex", "text_regex": "^Done", "allow_missing": False},
         ["holding a comment that matches “^Done” resolved", "a file with no such thread is set aside"]),
    ],
}
