"""Describe cases for the tables family: set_table_props, apply_table_style, swap_image and name_output —
(select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "set_table_props": [
        # the usual request: a thin grid all around and between the cells, the first row repeated, every table
        ({"tables": "all"},
         {"borders": {"top": {"style": "single", "size": 4}, "left": {"style": "single", "size": 4},
                      "bottom": {"style": "single", "size": 4}, "right": {"style": "single", "size": 4},
                      "insideH": {"style": "single", "size": 4}, "insideV": {"style": "single", "size": 4}},
          "repeat_header_rows": 1},
         ["Set the borders to a 0.5 pt single-line border all around and between the rows and columns, and "
          "repeat the first row at the top of every page in every table."]),
        # cell margins and a full width, rows kept whole, on the tables chosen by their first row
        ({"tables": {"first_row_regex": "^Item"}},
         {"cell_margins": {"left": 108, "right": 108}, "width": {"pct": 100}, "cant_split": True},
         ["Set the cell margins to 0.075 inch on the left and right, set the width to 100% of the text width, "
          "and keep every row on one page in tables whose first row matches the pattern “^Item”."]),
        # every border removed, no header rows, rows free to break, in the headers
        ({"scope": ["headers"]}, {"borders": "none", "repeat_header_rows": 0, "cant_split": False},
         ["Remove every border", "let no row repeat at the top of each page", "let rows break across pages",
          "in every table in the headers"]),
        # a coloured double rule top and bottom only, a fixed width, the cells' own borders cleared
        ({"tables": {"style_in": ["TableGrid"]}},
         {"borders": {"top": {"style": "double", "size": 12, "color": "1F3864"},
                      "bottom": {"style": "double", "size": 12, "color": "1F3864"}},
          "width": {"twips": 7200}, "clear_cell_overrides": True},
         ["a 1.5 pt double-line border at the top and bottom, coloured dark blue (none on the other sides)",
          "the width to 5 inches", "in tables in the “TableGrid” style",
          "The cells' own borders are removed so the table's show"]),
        # width left to Word, margins all round, three header rows, a border kept away from the text
        ({}, {"width": "auto", "cell_margins": {"top": 0, "bottom": 0, "left": 115, "right": 115},
              "repeat_header_rows": 3},
         ["Set the cell margins to 0 inch at the top and bottom, and 0.08 inch on the left and right",
          "let the width be set automatically", "repeat the first 3 rows at the top of every page", "in every table"]),
        # edges only, held away from the text, on the tables chosen by their text
        ({"tables": {"text_regex": "Sprinkler"}},
         {"borders": {"top": {"size": 8, "space": 4}, "left": {"size": 8, "space": 4},
                      "bottom": {"size": 8, "space": 4}, "right": {"size": 8, "space": 4}}},
         ["a 1 pt single-line border all around, 4 pt from the text (none between the cells)",
          "in tables whose text matches the pattern “Sprinkler”"]),
    ],
    "apply_table_style": [
        ({"tables": "all"}, {"style": "Grid Table 4"}, ["Give every table the “Grid Table 4” table style."]),
        # the style comes from a template when the document lacks it; the tables chosen by their first row
        ({"tables": {"first_row_regex": "^Item"}}, {"style": "SpecTable", "template": "C:/Projects/styles.dotx"},
         ["Give tables whose first row matches the pattern “^Item” the “SpecTable” table style",
          "imported from the template “C:/Projects/styles.dotx” when the document lacks it"]),
        # the template's definition wins, own formatting cleared, nested tables left out
        ({"scope": ["document"], "tables": {"nested": False}},
         {"style": "Plain Table 1", "template": "T:/office.dotx", "existing": "replace", "direct_formatting": "clear"},
         ["the “Plain Table 1” table style", "as the template “T:/office.dotx” defines it",
          "in place of the document's own definition", "excluding nested tables",
          "The tables' own borders, shading and cell margins", "are removed so the style shows"]),
    ],
    "swap_image": [
        # the usual request: the old logo in every header for the new one, proportions kept
        ({}, {"image": "C:/Logos/new-logo.png", "match": {"image": "C:/Logos/old-logo.png"}},
         ["Swap every picture that shows the file “C:/Logos/old-logo.png”",
          "for the picture “C:/Logos/new-logo.png” in the headers",
          "fitted inside the old picture's space with its proportions kept"]),
        # found by name, stretched, headers and footers, files without one pass unchanged
        ({"scope": ["headers", "footers"]},
         {"image": "logo.jpg", "match": {"name_regex": "(?i)logo"}, "fit": "stretch", "allow_missing": True},
         ["every picture whose name or description matches the pattern “(?i)logo”", "for the picture “logo.jpg”",
          "in the headers and footers", "stretched to fill the old picture's space",
          "Files with no such picture are left unchanged"]),
        # every picture of the body, the scope given as a parameter
        ({}, {"image": "seal.gif", "match": "all", "scope": ["document"]},
         ["Swap every picture for the picture “seal.gif” in the document body"]),
        ({}, {"image": "new.png", "match": {"md5": "0123456789abcdef0123456789abcdef"}},
         ["whose image has the checksum “0123456789abcdef0123456789abcdef”", "in the headers"]),
    ],
    "name_output": [
        # the worked example: "211319 - Preaction Sprinkler Systems.docx" from the title line
        ({}, {"pattern": "{number} - {title}",
              "values": {"number": {"rule": "regex", "pattern": "^SECTION (\\d{2} \\d{2} \\d{2})", "source": "document"},
                         "title": {"rule": "paragraph_after", "anchor_regex": "^SECTION \\d{2} \\d{2} \\d{2}$"}},
              "transforms": {"number": "digits", "title": "title"}},
         ["Name each output file “{number} - {title}.docx”",
          "“number” from the text matching the pattern “^SECTION (\\d{2} \\d{2} \\d{2})” in the document body, "
          "keeping only its digits, and “title” from the paragraph after the one matching "
          "“^SECTION \\d{2} \\d{2} \\d{2}$”, with each word capitalized.",
          "The document itself is not changed; only the edited copy gets the new name"]),
        # one value from the footer, the first match, in capitals; the suffix already in the pattern
        ({}, {"pattern": "{section}.docx",
              "values": {"section": {"rule": "regex", "pattern": "(\\d{6})", "source": "footer.default",
                                     "occurrence": "first"}},
              "transforms": {"section": "upper"}},
         ["Name each output file “{section}.docx”", "“section” from the text matching the pattern “(\\d{6})” in the "
          "footer (the first match), written in capitals"]),
        # a value found by the first rule that matches, written as it is
        ({}, {"pattern": "Spec {code}",
              "values": {"code": {"rule": "first_match", "rules": [
                  {"rule": "regex", "pattern": "^SECTION (\\d{6})", "source": "document"},
                  {"rule": "regex", "pattern": "(\\d{6})", "source": "header.default"}]}}},
         ["Name each output file “Spec {code}.docx”",
          "“code” from the first of these that is found: the text matching the pattern “^SECTION (\\d{6})” in the "
          "document body or the text matching the pattern “(\\d{6})” in the header"]),
    ],
}
