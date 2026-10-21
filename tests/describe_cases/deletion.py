"""Describe cases for deletion (built-in batch 6, F2): delete_paragraphs and remove_footnotes. Each case is (select,
params, [expected substrings])."""

CASES = {
    "delete_paragraphs": [
        ({"paragraphs": {"is_empty": True, "in_table": False}}, {"author": "Spec Team"},
         ["Delete all empty paragraphs outside tables", "shown as tracked changes by “Spec Team”",
          "A table all of whose paragraphs are chosen goes whole", "keeps its last paragraph, emptied",
          "stay until the deletion is accepted"]),
        ({"paragraphs": {"text_regex": "^\\s*DRAFT", "case_insensitive": True}, "scope": ["headers"]},
         {"tracked": False, "allow_missing": True},
         ["Delete all paragraphs whose text matches the pattern “^\\s*DRAFT” (ignoring case) in the headers",
          "without tracked changes", "go with them", "joins its pages to the next section",
          "Files with no such paragraph are left unchanged"]),
        ({"paragraphs": "all"}, {"tracked": False},
         ["Delete every paragraph in the document body", "removing them outright without tracked changes",
          "fields, comments, footnotes and bookmarks lying wholly inside go with them"]),
        ({"paragraphs": "all", "scope": ["document", "footnotes"]}, {},
         ["Delete every paragraph in the document body and the footnotes", "shown as tracked changes"]),
    ],
    "remove_footnotes": [
        ({}, {}, ["Remove every footnote and endnote, with its reference mark in the text", "shown as tracked changes",
                  "The separators stay and Word renumbers the notes that stay"]),
        ({}, {"which": "footnotes", "text_regex": "^ ?See NFPA", "tracked": False, "allow_missing": False},
         ["Remove every footnote whose text matches the pattern “^ ?See NFPA”",
          "removing them outright without tracked changes", "set aside rather than passed through"]),
        ({"scope": ["document", "headers"]}, {"which": "endnotes", "author": "Spec Team"},
         ["Remove every endnote referenced in the document body and the headers", "tracked changes by “Spec Team”"]),
    ],
}
