"""Describe cases for the table structure ops (built-in batch 6, F5): delete_tables, delete_table_rows,
insert_table_rows and insert_table — (select, params, [expected substrings]); the typical call first, then non-default
settings."""

CASES = {
    "delete_tables": [
        # the usual request: one table, chosen by its first row, as a tracked change
        ({"tables": {"first_row_regex": "^Tag"}}, {},
         ["Delete tables whose first row matches the pattern “^Tag”, shown as tracked changes."]),
        # every table in the headers, outright, by a named author
        ({"scope": ["headers"]}, {"tracked": False, "author": "J. Smith"},
         ["Delete every table in the headers, removing them outright without tracked changes."]),
        # by position, a file without one left alone
        ({"tables": {"index_range": [1, 2]}}, {"author": "J. Smith", "allow_missing": True},
         ["Delete tables numbered 2 through 3", "shown as tracked changes by “J. Smith”",
          "Files with no such table are left unchanged."]),
    ],
    "delete_table_rows": [
        # the usual request: the rows naming an old tag, tracked
        ({"tables": {"first_row_regex": "^Tag"}, "rows": {"text_regex": "^FP-2"}}, {},
         ["Delete the rows whose text matches the pattern “^FP-2” of tables whose first row matches the pattern “^Tag”, "
          "shown as tracked changes.", "A table losing every row is deleted whole."]),
        # by position, outright
        ({"rows": {"index_range": [2, 4]}}, {"tracked": False},
         ["Delete rows 3 through 5 of every table, removing them outright without tracked changes."]),
        # the header rows only, one row by its number
        ({"rows": {"header": True}}, {}, ["Delete the header rows of every table"]),
        ({"rows": {"index_range": [0, 0], "header": False}}, {"allow_missing": True},
         ["Delete the rows that are not header rows among row 1", "files with no such row are left unchanged"]),
        ({"scope": ["footers"], "rows": "all"}, {}, ["Delete every row of every table in the footers"]),
    ],
    "insert_table_rows": [
        # the usual request: one new row after the row naming FP-1, tracked
        ({"tables": {"first_row_regex": "^Tag"}, "rows": {"text_regex": "^FP-1"}},
         {"rows": [["FP-3", "Booster pump", "100 gpm"]]},
         ["Add a row with the cells “FP-3”, “Booster pump” and “100 gpm” after the row whose text matches the pattern "
          "“^FP-1” of tables whose first row matches the pattern “^Tag”, formatted like that row, shown as a tracked "
          "change."]),
        # two rows at the end, one with an empty cell, untracked
        ({}, {"rows": [["Z4", "West"], ["Z5", ""]], "position": "at_end", "tracked": False},
         ["Add 2 rows, with the cells “Z4” and “West”; and the cells “Z5” and an empty cell, at the end of every "
          "table, formatted like its last row, without tracked changes."]),
        # a header row before the first row, repeated on every page
        ({"rows": {"index_range": [0, 0]}}, {"rows": [["SCHEDULE"]], "position": "before", "repeat_header": True,
                                             "author": "J. Smith"},
         ["Add a row with the cell “SCHEDULE” before row 1 of every table", "repeated at the top of each page as "
          "header rows", "shown as a tracked change by “J. Smith”"]),
        # many rows: the first three are spelled out
        ({"rows": {"header": True}}, {"rows": [["a"], ["b"], ["c"], ["d"], ["e"]]},
         ["Add 5 rows", "“a”", "“c”", "and 2 more", "after the header row of every table"]),
    ],
    "insert_table": [
        # the usual request: typed rows after a heading, a style the document has, the header row repeated
        ({"paragraphs": {"text_regex": "^1\\.1 SCHEDULES"}},
         {"rows": [["Tag", "Description"], ["FP-1", "Fire pump"]], "style": "Table Grid", "header_rows": 1},
         ["Insert a table of 2 rows and 2 columns, the first row with the cells “Tag” and “Description”, after each "
          "of the paragraphs whose text matches the pattern “^1\\.1 SCHEDULES”, in the “Table Grid” table style, as "
          "wide as the text with equal columns, its first row repeated at the top of each page, shown as a tracked "
          "change."]),
        # from an attached sheet, before the anchor, set widths, untracked
        ({"paragraphs": {"text_regex": "^EQUIPMENT"}},
         {"table": {"file": "C:/Projects/pumps.xlsx", "sheet": "Pumps"}, "position": "before",
          "column_widths": [1, 2.5], "tracked": False},
         ["Insert the table in the file “C:/Projects/pumps.xlsx” (sheet “Pumps”) before each of the paragraphs whose "
          "text matches the pattern “^EQUIPMENT”", "with a single-line border around and between every cell",
          "its columns 1 inch and 2.5 inches wide", "without tracked changes"]),
        # a JSON list of rows, several header rows, files without the anchor left alone
        ({"paragraphs": {"style_in": ["Heading2"]}},
         {"table": {"file": "C:/p/project.json", "key": "schedules.zones"}, "header_rows": 2, "allow_missing": True},
         ["the table in the file “C:/p/project.json” (entry “schedules.zones”)", "its first 2 rows repeated",
          "Files with no such paragraph are left unchanged."]),
    ],
}
