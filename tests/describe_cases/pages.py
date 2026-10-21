"""Describe cases for the pages family: set_page_setup, set_tab_stops, set_borders_shading, add_watermark and
remove_watermark — (select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "set_page_setup": [
        # sections by an index range: both ends included, counted from 1 in the sentence
        ({"sections": {"index_range": [0, 2]}}, {"orientation": "landscape"}, ["sections 1 through 3"]),
        # the usual request: one-inch margins everywhere, every section, never tracked
        ({"sections": "all"}, {"margins": {"top": 1440, "bottom": 1440, "left": 1440, "right": 1440}},
         ["every margin to 1 inch", "in every section"]),
        # distinct margins, header and footer distances, a named page size, one section
        ({"sections": [0]}, {"margins": {"top": 1440, "left": 1800, "header": 720, "footer": 720, "gutter": 360},
                             "size": {"w": 12240, "h": 15840}},
         ["top margin to 1 inch", "left margin to 1.25 inches", "gutter to 0.25 inch",
          "header 0.5 inch from the page edge", "footer 0.5 inch from the page edge",
          "US Letter (8.5 by 11 inches)", "in section 1"]),
        # landscape with a different first page, sections chosen by content; a no-op where none matches
        ({"sections": {"with_tables": True}},
         {"orientation": "landscape", "different_first_page": True, "allow_missing": True},
         ["orientation to landscape", "sections that contain tables", "first page its own header and footer",
          "margins stay as they are", "no section matches are left unchanged"]),
        # the first page back to the ordinary header and footer, sections chosen by a pattern
        ({"sections": {"containing": {"text_regex": "^PART 1"}}}, {"different_first_page": False},
         ["ordinary header and footer on the first page", "sections whose text matches the pattern “^PART 1”"]),
        # built-in batch 6: two columns with a line between them, the text centred on the page
        ({"sections": [2]}, {"columns": {"count": 2, "space": 720, "separator": True},
                             "vertical_alignment": "center"},
         ["the text to 2 columns of equal width 0.5 inch apart with a line between them",
          "vertically to the centre of the page", "in section 3"]),
        # back to one column, line numbers every fifth line restarting on each page, the section on an odd page
        ({"sections": "all"}, {"columns": {"count": 1},
                               "line_numbering": {"count_by": 5, "start": 1, "restart": "new_page", "distance": 360},
                               "section_start": "odd_page"},
         ["a single column", "line numbers to every 5th line, starting at 1, restarting on each page, 0.25 inch from "
          "the text", "section start to the next odd-numbered page", "blank page"]),
        # line numbers off, a continuous start
        ({"sections": [1]}, {"line_numbering": "none", "section_start": "continuous"},
         ["section start to the same page (continuous)", "turn line numbering off", "in section 2"]),
        # line numbering alone
        ({}, {"line_numbering": "none"}, ["Turn line numbering off in every section"]),
    ],
    "set_tab_stops": [
        # a table-of-contents style: a dot-leader right tab at the right margin, nothing else left
        ({"paragraphs": {"style_in": ["TOC1"]}},
         {"set": [{"pos": 720}, {"pos": "right_margin", "align": "right", "leader": "dot"}], "clear_others": True},
         ["left tab stop at 0.5 inch", "right tab stop at the right margin with a dot leader", "“TOC1”",
          "clearing every other tab stop", "tracked change"]),
        # a centre tab, two positions cleared, asked plain although the selection is targeted
        ({"paragraphs": {"style_in": ["PR1"]}},
         {"set": [{"pos": "center", "align": "center"}], "clear": [720, 1440], "tracked": False},
         ["centre tab stop at the centre of the text", "clear the tab stops at 0.5 inch and 1 inch", "“PR1”",
          "without tracked changes"]),
        # every stop cleared in the headers and footers; whole-document selections are plain
        ({"scope": ["headers", "footers"]}, {"clear_others": True},
         ["Clear every tab stop", "headers and footers"]),
        # a decimal stop with an underscore leader, a named author
        ({"paragraphs": {"text_regex": "^\\d+\\."}},
         {"set": [{"pos": 4320, "align": "decimal", "leader": "underscore"}], "clear": ["center"], "author": "Jane"},
         ["decimal tab stop at 3 inches with an underscore leader", "clear the tab stop at the centre of the text",
          "“^\\d+\\.”", "tracked changes by “Jane”"]),
    ],
    "set_borders_shading": [
        # a rule under every heading: Word's defaults (single, half a point) spelled out, the other sides cancelled
        ({"paragraphs": {"style_in": ["Heading1"]}}, {"borders": {"bottom": {}}},
         ["0.5 pt single-line border below", "none on the other sides", "“Heading1”", "tracked change"]),
        # a boxed, shaded note in a style definition: thicker, spaced, coloured, with a pattern; never tracked
        ({"styles": {"ids": ["Caution"]}},
         {"borders": {side: {"size": 12, "color": "FF0000", "space": 4} for side in ("top", "left", "bottom", "right")},
          "shading": {"fill": "FFF2CC", "pattern": "pct10", "color": "FF0000"}},
         ["1.5 pt single-line border all around", "4 pt from the text", "coloured red", "fill of pale yellow",
          "10% pattern", "definition of the “Caution” style"]),
        # a double rule above and below plus a grey fill, on paragraphs of one style
        ({"paragraphs": {"style_in": ["Note"]}},
         {"borders": {"top": {"size": 8, "style": "double"}, "bottom": {"size": 8, "style": "double"}},
          "shading": {"fill": "D9D9D9"}},
         ["1 pt double-line border above and below", "fill of light grey", "“Note”"]),
        # everything off inside tables, asked plain
        ({"paragraphs": {"in_table": True}}, {"borders": "none", "shading": "none", "tracked": False},
         ["Remove every border and the shading", "inside tables", "without tracked changes"]),
    ],
    "add_watermark": [
        ({}, {"text": "DRAFT"}, ["reading “DRAFT”", "diagonally", "every page", "every header"]),
        ({}, {"text": "NOT FOR CONSTRUCTION", "layout": "horizontal", "font": "Arial", "color": "#ff0000",
              "opacity": 0.3, "size": 72, "replace": True},
         ["reading “NOT FOR CONSTRUCTION”", "horizontally", "“Arial” font", "coloured red", "30% opacity",
          "72 pt", "replacing any watermark the file already has"]),
        ({}, {"text": "CONFIDENTIAL", "replace": True}, ["“CONFIDENTIAL”", "already has is replaced"]),
    ],
    "remove_watermark": [
        ({}, {}, ["Remove every watermark", "every header", "nothing moves"]),
        ({}, {"text": "Draft", "allow_missing": False},
         ["watermark reading “Draft”", "ignoring case and spacing", "without such a watermark is set aside"]),
    ],
}
