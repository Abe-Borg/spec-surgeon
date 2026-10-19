"""Describe cases for the hygiene family: normalize_whitespace, normalize_punctuation, protect_nonbreaking_spaces,
capitalize_defined_terms and resolve_options — (select, params, [expected substrings]); the typical call first, then
non-default settings. These ops default to the document body, headers and footers (resolve_options: the body) and
to tracked changes; allow_missing defaults on for the four character-hygiene ops and off for resolve_options."""

SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _approval(file, paragraph, start, before, after, ident):
    """A proposal record as propose_defined_terms writes it and the user approved it (the words describe uses)."""
    return {"file": file, "source_sha256": SHA, "part": "word/document.xml", "paragraph": paragraph, "start": start,
            "before": before, "after": after, "id": ident * 64, "context": f"... the {before} shall ..."}


CASES = {
    "normalize_whitespace": [
        ({}, {},
         ["Collapse each run of two or more ordinary spaces into one", "end of a line",
          "in every paragraph in the document body, the headers and the footers", "shown as tracked changes",
          "Words, tabs and non-breaking spaces are left as they are"]),
        # one style in the body only, plain, and a file with nothing to tidy is an exception
        ({"paragraphs": {"style_in": ["PR1"]}, "scope": ["document"]}, {"tracked": False, "allow_missing": False},
         ["in paragraphs in the “PR1” style", "without tracked changes", "set aside rather than passed through"]),
        ({"scope": ["headers", "footers"]}, {"author": "Spec Team"},
         ["in the headers and footers", "shown as tracked changes by “Spec Team”"]),
    ],
    "normalize_punctuation": [
        ({}, {"quotes": "curly", "dash": "em"},
         ["Use curly quotation marks throughout", "turn each “--” into an em dash (—)", "shown as tracked changes",
          "right after a digit are left alone as possible inch or foot marks"]),
        # section numbers hyphenated and en dashes, plain, only in the paragraphs that name a section
        ({"paragraphs": {"text_regex": "^SECTION"}}, {"section_separator": "hyphen", "dash": "en", "tracked": False},
         ["separate the parts of every section number with a hyphen (09-91-23)", "an en dash (–)",
          "whose text matches the pattern “^SECTION”", "without tracked changes"]),
        ({"scope": ["document"]}, {"quotes": "straight", "section_separator": "space", "author": "Jane Doe",
                                   "date_convention": "utc"},
         ["Use straight quotation marks throughout", "with a space (09 91 23)", "in every paragraph,",
          "shown as tracked changes by “Jane Doe” dated in UTC"]),
    ],
    "protect_nonbreaking_spaces": [
        ({}, {},
         ["Replace the ordinary spaces between a number and the unit “psi” and the parts of a spaced section number "
          "such as 09 91 23 with non-breaking spaces", "stay together on one line",
          "in every paragraph in the document body, the headers and the footers", "shown as tracked changes"]),
        # several units, section numbers left out, plain
        ({"scope": ["document"]}, {"units": ["psi", "kPa", "°F"], "section_numbers": False, "tracked": False},
         ["a number and the units “psi”, “kPa” or “°F” with non-breaking spaces", "without tracked changes"]),
        ({"paragraphs": {"in_table": True}}, {"units": [], "allow_missing": False},
         ["the parts of a spaced section number such as 09 91 23", "inside tables", "set aside"]),
    ],
    "capitalize_defined_terms": [
        ({}, {"approvals": [_approval("C:/Specs/09 91 23.docx", 12, 4, "owner", "Owner", "a"),
                            _approval("C:/Specs/09 91 23.docx", 31, 60, "owner", "Owner", "b"),
                            _approval("C:/Specs/09 91 23.docx", 40, 9, "work", "Work", "c")]},
         ["Capitalize the individually approved occurrences of defined terms", "“owner” to “Owner” in 2 places",
          "“work” to “Work” in 1 place", "shown as tracked changes",
          "Only the reviewed occurrences change; nothing is capitalized by guess"]),
        # approvals spanning two files, in the body and footnotes, plain, nothing-to-change is an exception
        ({"scope": ["document", "footnotes"]},
         {"approvals": [_approval("C:/Specs/09 91 23.docx", 12, 4, "contractor", "Contractor", "d"),
                        _approval("C:/Specs/23 05 00.docx", 7, 0, "engineer", "Engineer", "e")],
          "tracked": False, "allow_missing": False},
         ["“contractor” to “Contractor” in 1 place", "“engineer” to “Engineer” in 1 place", "across 2 files",
          "in the document body and the footnotes", "without tracked changes", "set aside rather than passed through"]),
    ],
    "resolve_options": [
        ({}, {"rules": [{"match": "preaction", "action": "pick"}]},
         ["Resolve the choices offered in square brackets in every paragraph", "shown as tracked changes",
          "keep only the option matching “preaction”, dropping the alternatives and the brackets",
          "The first rule that fits a choice decides it; choices no rule fits are left as they are"]),
        # a drop then a case-exact pick, in two styles of the body and headers, plain, missing allowed
        ({"paragraphs": {"style_in": ["PR1", "PR2"]}, "scope": ["document", "headers"]},
         {"rules": [{"match": "deluge", "action": "drop"}, {"match": "^Wet-pipe$", "action": "pick", "match_case": True}],
          "tracked": False, "allow_missing": True},
         ["in paragraphs in the “PR1” and “PR2” styles in the document body and the headers",
          "remove every option matching “deluge”, with its brackets and spacing",
          "keep only the option matching “^Wet-pipe$” (matching case exactly)", "without tracked changes",
          "files with no such choice are left unchanged"]),
        ({}, {"rules": [{"match": "galvanized", "action": "pick"}], "author": "Spec Team"},
         ["shown as tracked changes by “Spec Team”", "“galvanized”"]),
    ],
}
