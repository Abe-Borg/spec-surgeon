"""Describe cases for the styles family: apply_style, map_styles, remove_unused_styles, clear_direct_formatting and
filter_tagged_paragraphs — (select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "apply_style": [
        # bold all-capitals paragraphs become article headings; targeted, so tracked by default
        ({"paragraphs": {"has_bold": True, "is_all_caps": True}}, {"style": "ART"},
         ["Apply the “ART” style to bold all-capitals paragraphs", "shown as tracked changes"]),
        # the whole document restyled from a template, the template's definition winning, direct formatting cleared
        ({}, {"style": "Body Text", "template": "C:/Office/Spec.dotx", "existing": "replace", "direct_formatting": "clear"},
         ["Apply the “Body Text” style to every paragraph", "from the template “C:/Office/Spec.dotx”",
          "in place of the document's own", "clearing the paragraphs' own direct formatting"]),
        # only the formatting the style owns is replaced; asked plain although targeted
        ({"paragraphs": {"after_heading_regex": "^PART 2"}, "scope": ["document"]},
         {"style": "PR1", "direct_formatting": "replace_style_owned", "tracked": False},
         ["Apply the “PR1” style to paragraphs after the first paragraph matching “^PART 2”",
          "replacing only the direct formatting the style itself sets", "without tracked changes"]),
        # imported when missing, tracked with a named author, in the headers and footers
        ({"paragraphs": {"style_in": ["Arch Body"]}, "scope": ["headers", "footers"]},
         {"style": "PR1", "template": "Office.dotx", "author": "Jane"},
         ["to paragraphs in the “Arch Body” style in the headers and footers",
          "importing it with its numbering from the template “Office.dotx” when the document lacks it",
          "shown as tracked changes by “Jane”"]),
    ],
    "map_styles": [
        # the architect's styles become the office's, everywhere, plain by default
        ({}, {"map": [{"from": "Arch Body", "to": "PR1"}, {"from": "Arch Heading 1", "to": "ART"}]},
         ["Give every paragraph, run and table in the “Arch Body” style the “PR1” style",
          "those in the “Arch Heading 1” style the “ART” style instead", "everywhere in the document",
          "The old style definitions stay in the file"]),
        # the template's definitions replace the file's, tracked, missing styles refused, body only
        ({"scope": ["document"]},
         {"map": [{"from": "Normal Indent", "to": "PR2"}], "template": "C:/Office/Spec.dotx", "existing": "replace",
          "allow_missing": False, "tracked": True, "author": "Spec Team"},
         ["in the “Normal Indent” style the “PR2” style instead, in the document body",
          "shown as tracked changes by “Spec Team”",
          "taken from the template “C:/Office/Spec.dotx” in place of the document's own",
          "a file that lacks or does not use one of the old styles is set aside"]),
        # imported only when the file lacks the new style, in the headers and footers
        ({"scope": ["headers", "footers"]}, {"map": [{"from": "Arch Header", "to": "Header"}], "template": "Office.dotx"},
         ["in the “Arch Header” style the “Header” style instead, in the headers and footers",
          "imported, with its numbering, from the template “Office.dotx”"]),
    ],
    "remove_unused_styles": [
        ({}, {}, ["Remove every style definition that nothing in the document uses", "Nothing shown on any page changes"]),
        # only the document's own styles, paragraph and character ones, two kept whatever their use, no-op refused
        ({}, {"which": "custom", "types": ["paragraph", "character"], "keep": ["PR1", "Article Heading"],
              "allow_missing": False},
         ["Remove every custom paragraph or character style definition that nothing in the document uses",
          "leaving Word's built-in styles alone", "keeping the “PR1” and “Article Heading” styles whether used or not",
          "a file with nothing to remove is set aside"]),
        ({}, {"types": ["table"], "keep": ["Spec Table"]},
         ["every table style definition", "keeping the “Spec Table” style whether used or not"]),
    ],
    "clear_direct_formatting": [
        # paragraphs in one style go back to it; targeted, so tracked by default
        ({"paragraphs": {"style_in": ["PR1"]}}, {},
         ["Clear the direct character and paragraph formatting from paragraphs in the “PR1” style",
          "so the text follows its styles again", "shown as tracked changes"]),
        # character formatting only, bold and italic kept, highlighting cleared too, pending revisions left alone
        ({}, {"paragraph": False, "keep": ["b", "bCs", "i", "iCs"], "also": ["highlight"], "in_revisions": "exclude"},
         ["Clear the direct character formatting from every paragraph", "leaving bold and italic untouched",
          "clearing highlighting too", "Text inside existing tracked changes is left alone"]),
        # paragraph formatting only, the indents kept, outline levels cleared too, in the footnotes
        ({"scope": ["footnotes"]}, {"run": False, "keep": ["ind"], "also": ["outlineLvl"]},
         ["Clear the direct paragraph formatting from every paragraph in the footnotes", "leaving indents untouched",
          "clearing outline levels too"]),
        # runs narrowed, inside tables, asked plain, marks left alone, one symbol font named
        ({"runs": {"has_direct_font": True}, "paragraphs": {"in_table": True}},
         {"paragraph": False, "include_paragraph_marks": False, "tracked": False, "preserve_fonts": ["Symbol"]},
         ["from text with a font set directly on it in paragraphs inside tables", "without tracked changes",
          "Paragraph marks are not changed", "text in the “Symbol” font is left alone"]),
    ],
    "filter_tagged_paragraphs": [
        # keep two systems, the usual bracketed tag at the start of the paragraph, tracked by default
        ({}, {"tag_regex": "^\\[(?P<tag>[A-Z /]+)\\]\\s*", "keep": ["WET", "PREACTION"]},
         ["Keep the paragraphs tagged for “WET” or “PREACTION” and delete those tagged only for other systems",
          "the tag found by the pattern “^\\[(?P<tag>[A-Z /]+)\\]\\s*”", "in the document body",
          "taking the tag off the paragraphs that stay", "shown as tracked changes", "Untagged paragraphs stay"]),
        # delete two systems, tags left in place, an unusual separator, untracked, footnotes included, no-op allowed
        ({"scope": ["document", "footnotes"]},
         {"tag_regex": "\\[(?P<tag>[A-Z /|]+)\\]\\s*", "delete": ["DRY", "CLEAN AGENT"], "strip_tags": False,
          "tag_separator": "\\s*\\|\\s*", "tracked": False, "allow_missing": True},
         ["Delete the paragraphs tagged for “DRY” or “CLEAN AGENT” and keep those tagged for any other system as well",
          "in the document body and the footnotes", "leaving the tags on the paragraphs that stay",
          "splitting a tag that names several systems at the pattern “\\s*\\|\\s*”", "without tracked changes",
          "files with no tagged paragraph are left unchanged"]),
        # a named author
        ({}, {"tag_regex": "^\\[(?P<tag>[A-Z ]+)\\]\\s*", "keep": ["WET"], "author": "Jane Doe"},
         ["tagged for “WET”", "shown as tracked changes by “Jane Doe”"]),
    ],
}
