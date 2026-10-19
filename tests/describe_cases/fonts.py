"""Describe cases for the fonts family: set_font, set_style_font, set_doc_defaults_font, set_properties,
set_paragraph_props — (select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "set_font": [
        ({"paragraphs": {"style_in": ["PR1", "PR2"]}}, {"font": "Arial"}, ["Arial", "“PR1”"]),
        ({}, {"font": "Times New Roman", "size": 10}, ["Times New Roman", "10 pt"]),
        # targeted (tracked by default) but asked plain; list labels asked on although the selection is narrow
        ({"paragraphs": {"text_regex": "^PART\\s+\\d"}}, {"font": "Arial", "tracked": False, "numbering_levels": True},
         ["“Arial”", "“^PART\\s+\\d”", "without tracked changes", "list labels included"]),
        # size only, limited to the sizes named, in the headers and footers; whole-document selections are plain
        ({"scope": ["headers", "footers"]}, {"size": 10, "size_from": [9, 11]},
         ["10 pt", "9 pt", "11 pt", "headers and footers"]),
        # every knob off its default, on runs with a direct font: a second sentence names the settings
        ({"runs": {"has_direct_font": True}},
         {"font": "Calibri", "east_asia": True, "size": 11, "add_missing_size": True, "clear_theme_fonts": False,
          "preserve_fonts": ["Symbol", "Wingdings"], "in_revisions": "exclude", "include_paragraph_marks": False},
         ["“Calibri”", "East Asian", "11 pt", "inherited sizes included", "font set directly", "theme font",
          "“Wingdings”", "tracked changes is left alone", "paragraph marks"]),
        # a tracked change with a named author, the font only where one is set directly
        ({"paragraphs": {"style_in": ["Heading1"]}}, {"font": "Arial", "only_where_defined": True, "author": "Jane"},
         ["“Arial”", "“Heading1”", "where a font is set directly", "tracked changes by “Jane”"]),
    ],
    "set_style_font": [
        ({"styles": "all"}, {"font": "Arial"}, ["“Arial”", "every style", "already sets its own font", "list labels"]),
        ({"styles": {"ids": ["PR1", "Heading1"]}}, {"font": "Arial", "size": 10, "only_where_defined": False},
         ["“Arial”", "10 pt", "“PR1”", "“Heading1”", "even where a style now inherits"]),
        ({"styles": {"types": ["paragraph"]}}, {"size": 10, "size_from": [9]},
         ["10 pt", "9 pt", "every paragraph style"]),
        ({"styles": "all-paragraph-and-character"}, {"font": "Calibri", "numbering_levels": False, "clear_theme_fonts": False},
         ["“Calibri”", "paragraph and character style", "List labels are left alone", "theme font"]),
    ],
    "set_doc_defaults_font": [
        ({}, {"font": "Arial"}, ["default font", "“Arial”", "set their own font keep it"]),
        ({}, {"font": "Arial", "size": 10, "size_from": [11]}, ["“Arial”", "10 pt", "11 pt", "current default"]),
        ({}, {"ascii": "Arial", "hAnsi": "Arial", "eastAsia": "SimSun"}, ["“Arial”", "“SimSun”", "East Asian"]),
        ({}, {"size": 12, "clear_theme_fonts": False}, ["default size to 12 pt"]),
    ],
    "set_properties": [
        ({"paragraphs": {"style_in": ["Heading1"]}}, {"run": {"b": True, "caps": True}},
         ["bold", "all capitals", "“Heading1”", "tracked change"]),
        ({"runs": {"color_in": ["0000FF"]}}, {"run": {"color": "auto", "i": False, "u": "none"}, "in_revisions": "exclude"},
         ["Remove italic and underlining", "colour to automatic", "“0000FF”", "tracked changes is left alone"]),
        ({"styles": {"ids": ["BodyText"]}}, {"paragraph": {"keepNext": True, "widowControl": False}, "run": {"u": "single"}},
         ["“BodyText”", "definition", "same page as the next", "widow", "single underline"]),
        ({"paragraphs": {"has_bold": True}}, {"run": {"b": False, "color": "1F3864"}, "paragraph": {"pageBreakBefore": True},
                                              "tracked": False},
         ["Remove bold", "“1F3864”", "new page", "without tracked changes", "bold paragraphs"]),
        ({"scope": ["headers", "footers"]}, {"run": {"i": True, "u": "double", "strike": True}, "include_paragraph_marks": False},
         ["italic", "double underline", "strikethrough", "headers and footers", "Paragraph marks are not changed"]),
    ],
    "set_paragraph_props": [
        ({}, {"spacing": {"before": 240, "after": 120}}, ["space before to 12 pt", "space after to 6 pt", "every paragraph"]),
        ({"paragraphs": {"style_in": ["PR1"]}}, {"ind": {"left": 720, "hanging": 360}, "jc": "both"},
         ["left indent to 0.5 inch", "hanging indent to 0.25 inch", "justified", "“PR1”", "tracked change"]),
        ({"styles": {"ids": ["BodyText"]}}, {"spacing": {"line": 360, "lineRule": "auto"}, "ind": {"firstLineChars": 200}},
         ["line spacing to 1.5 lines", "first-line indent to 2 characters", "“BodyText”", "definition"]),
        ({"paragraphs": {"in_table": True}}, {"spacing": {"line": 240, "lineRule": "exact"}, "jc": "center", "tracked": False},
         ["exactly 12 pt", "centred", "without tracked changes", "inside tables"]),
        ({"scope": ["footnotes"]}, {"spacing": {"afterLines": 50}, "ind": {"right": 1440}},
         ["space after to 0.5 line", "right indent to 1 inch", "footnotes"]),
    ],
}
