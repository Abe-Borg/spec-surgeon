"""Describe cases for the numbering family: convert_numbering, convert_typed_numbers, set_list_level and
audit_numbering — (select, params, [expected substrings]); the typical call first, then non-default settings.
List levels read as Word counts them (level 1 is the outermost), not as the engine's zero-based indices."""

CASES = {
    "convert_numbering": [
        # the typical call: the main list, the scheme imported from an example file, plain (its default)
        ({}, {"levels": ["PRT", "ART", "PR1", "PR2", "PR3"], "template": "spec-template.docx"},
         ["Convert the document's numbering", "“PRT”", "“ART”", "“PR3”", "example file “spec-template.docx”",
          "main list", "loses its own list numbering", "your list numbers it"]),
        # by source styles, the example file's definitions replacing this file's, cleared, tracked by a named author,
        # a file with nothing to convert set aside
        ({}, {"levels": ["PRT", "ART", "PR1"], "template": "T.docx", "existing": "replace",
              "from": {"styles": [["Part Heading"], ["Article Heading", "Heading 2"], ["Body Numbered"]]},
              "direct_formatting": "clear", "tracked": True, "author": "Jane", "allow_missing": False},
         ["numbered paragraphs in the “Part Heading” style take the “PRT” style",
          "those in the “Article Heading” and “Heading 2” styles the “ART” style", "“Body Numbered”", "“PR1”",
          "replacing this file's own", "tracked changes by “Jane”", "direct formatting is cleared", "set aside"]),
        # the main list with its levels remapped (the engine's level 1 is the reader's level 2), no example file
        ({}, {"levels": ["ART", "PR1"], "level_map": {"1": 0, "2": 1}},
         ["main list", "level 2 of the list going to the “ART” style", "level 3 of the list going to the “PR1” style"]),
        # table cells left as they are, every number keeping its values (docs/PLAN-2026-10-23-modes.md E1, E3)
        ({"include_tables": False}, {"levels": ["PRT", "ART", "PR1"], "template": "T.docx", "counters": "keep"},
         ["main list", "table cells are left as they are", "would count differently is set aside"]),
    ],
    "convert_typed_numbers": [
        # the typical call: every paragraph, tracked (its default), the scheme from an example file
        ({}, {"levels": ["PRT", "ART", "PR1", "PR2"], "template": "spec-template.docx"},
         ["typed numbers at the start of every paragraph", "real list numbers", "“PRT”", "“PR2”",
          "example file “spec-template.docx”", "space or tab after it", "shown as tracked changes",
          "reads exactly as before"]),
        # a style selection, table cells included, plain, cleared, a file with no typed number set aside
        ({"paragraphs": {"style_in": ["Normal"]}, "include_tables": True},
         {"levels": ["ART", "PR1"], "tracked": False, "direct_formatting": "clear", "allow_missing": False},
         ["paragraphs in the “Normal” style", "without tracked changes", "table cells are converted too",
          "direct formatting is cleared", "no typed number to convert is set aside"]),
        # a pattern outside tables, the example file's definitions replacing this file's, a named author
        ({"paragraphs": {"text_regex": "^[A-Z]\\.", "in_table": False}},
         {"levels": ["PR1"], "template": "T.docx", "existing": "replace", "author": "Spec Team"},
         ["whose text matches the pattern “^[A-Z]\\.”", "outside tables", "“PR1”", "replacing this file's own",
          "tracked changes by “Spec Team”"]),
        # typed values instead of the typed look (docs/PLAN-2026-10-23-modes.md G1), plain
        ({}, {"levels": ["PRT", "ART", "PR1"], "template": "T.docx", "match": "counters", "tracked": False},
         ["without tracked changes", "values are the ones your list counts at that place",
          "whatever its zero padding or dash", "“1.01” shows as the list's “1.1”",
          "every other paragraph must read exactly as before"]),
    ],
    "set_list_level": [
        # "restart per PART": the articles' level restarts after any higher level
        ({}, {"style": "ART", "restart": "after_higher"},
         ["In the list's definition, change the level that the “ART” style numbers with",
          "every paragraph at that level follows", "restarts after any higher level"]),
        # zero padding on
        ({}, {"style": "PR1", "zero_pad": True}, ["“PR1”", "zero-padded (1 becomes 01)"]),
        # another level of the style's list, a letter format, a start value, a restart after a given level,
        # a file without the style set aside
        ({}, {"style": "ArchPart", "level": 1, "format": "upperLetter", "start": 2, "restart": {"after_level": 0},
              "allow_missing": False},
         ["level 2 of the list that the “ArchPart” style numbers with", "shown as A, B, C", "starts at 2",
          "restarts only after level 1", "missing or not numbered is set aside"]),
        # a roman format that never restarts; zero padding off
        ({}, {"style": "PR2", "format": "lowerRoman", "restart": "never"}, ["i, ii, iii", "never restarts"]),
        ({}, {"style": "PR1", "zero_pad": False}, ["lose their zero padding (01 becomes 1)"]),
    ],
    "audit_numbering": [
        # report only (the default): nothing changes
        ({}, {}, ["Audit the document's numbering", "gap, duplicate", "empty numbered paragraph", "hidden numbered",
                  "Nothing changes."]),
        # the fix, plain
        ({}, {"unnumber_empty": True},
         ["Each empty numbered paragraph then loses its number", "later numbers of its list move up",
          "nothing else changes"]),
        # the fix as tracked changes by a named author
        ({}, {"unnumber_empty": True, "tracked": True, "author": "Jane"},
         ["loses its number, shown as tracked changes by “Jane”", "move up"]),
    ],
}
