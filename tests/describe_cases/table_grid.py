"""Describe cases for the table grid and cell ops (built-in batch 6, F6): delete_table_columns, set_table_cells and
merge_table_cells — (select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "delete_table_columns": [
        # the usual request: the Notes column of the pump schedule, chosen by its header
        ({"tables": {"first_row_regex": "^Tag"}, "columns": {"header_regex": "^Notes$"}}, {},
         ["Delete the columns headed by the pattern “^Notes$” of tables whose first row matches the pattern “^Tag”, "
          "with their cells and text, without tracked changes.", "a fixed table width shrinks with the columns"]),
        # by number, in every table, files without one left alone
        ({"columns": {"index": [1, 3]}}, {"allow_missing": True},
         ["Delete columns 2 and 4 of every table", "files with no such column are left unchanged"]),
        ({"scope": ["headers"], "columns": {"index": [0], "header_regex": "SECTION"}}, {},
         ["the columns headed by the pattern “SECTION” among column 1 of every table in the headers"]),
    ],
    "set_table_cells": [
        # the usual request: shade the header rows grey, their text centred vertically
        ({"rows": {"header": True}}, {"shading": "D9D9D9", "vertical_alignment": "center"},
         ["For the cells in the header rows of every table: shade them light grey and align their text to the middle "
          "of the cell, without tracked changes."]),
        # a column width, one table by its first row
        ({"tables": {"first_row_regex": "^Tag"}, "columns": {"index": [0]}}, {"width": 1.25},
         ["For the cells in column 1 of tables whose first row matches the pattern “^Tag”: make those columns 1.25 "
          "inches wide"]),
        # no shading and no wrapping in the cells of a column chosen by its header
        ({"columns": {"header_regex": "^Size$"}}, {"shading": "none", "no_wrap": True, "vertical_alignment": "bottom"},
         ["the columns headed by the pattern “^Size$”", "remove their shading", "keep their text on one line",
          "align their text to the bottom of the cell"]),
        ({"rows": {"index_range": [1, 3]}}, {"no_wrap": False, "vertical_alignment": "top"},
         ["For the cells in rows 2 through 4 of every table: align their text to the top of the cell and let their "
          "text wrap"]),
        ({}, {"width": 2}, ["For every cell of every table: make every column 2 inches wide"]),
    ],
    "merge_table_cells": [
        # the usual request: the first row's first two cells become one title cell
        ({"tables": {"first_row_regex": "^Tag"}, "rows": {"index_range": [0, 0]}, "columns": {"index": [0, 1]}}, {},
         ["Merge the cells in row 1 and columns 1 through 2 of tables whose first row matches the pattern “^Tag” into "
          "one cell, keeping their text in reading order, without tracked changes.",
          "Empty cells add nothing and the first cell's formatting stays."]),
        # down a column, by the rows' text, files without such cells left alone
        ({"rows": {"text_regex": "^P-[23]"}, "columns": {"header_regex": "^Notes$"}}, {"allow_missing": True},
         ["Merge the cells in the rows whose text matches the pattern “^P-[23]” and the columns headed by the pattern "
          "“^Notes$” of every table", "files with no such cells are left unchanged"]),
        ({"rows": {"index_range": [2, 4]}, "columns": {"index": [3]}}, {},
         ["Merge the cells in rows 3 through 5 and column 4 of every table into one cell"]),
    ],
}
