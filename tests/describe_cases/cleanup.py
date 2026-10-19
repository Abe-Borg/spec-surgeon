"""Describe cases for the cleanup family: remove_comments, remove_empty_paragraphs, remove_hidden_text, remove_highlight,
remove_hyperlinks, remove_notes and unlink_fields — (select, params, [expected substrings]); the typical call first,
then non-default settings. These ops select by scope only; allow_missing defaults on for all of them (a clean-up runs
over whole batches), the deletions (blank paragraphs, hidden text, notes) track by default, remove_highlight is plain
by default and the rest never track."""

CASES = {
    "remove_comments": [
        ({}, {}, ["Remove every comment from the document", "whether or not it is resolved", "text is unchanged",
                  "comment marks in the text go with their comments"]),
        # only the resolved threads, and a file without one is an exception
        ({}, {"which": "resolved", "allow_missing": False},
         ["comment thread that is marked resolved", "replies included", "set aside rather than passed through"]),
        ({}, {"which": "authors", "authors": ["Jane Doe", "Spec Team"]},
         ["the comments by “Jane Doe” or “Spec Team”", "regardless of case and spacing", "every reply beneath them"]),
    ],
    "remove_empty_paragraphs": [
        ({}, {}, ["Remove the extra blank paragraphs in the document body", "leaving one blank line where several stand "
                  "together", "shown as tracked changes", "number or bullet", "two tables are never joined"]),
        # every blank paragraph, in the body, headers and footers, plain, nothing-to-remove is an exception
        ({"scope": ["document", "headers", "footers"]}, {"mode": "all", "tracked": False, "allow_missing": False},
         ["Remove every blank paragraph in the document body, the headers and the footers", "without tracked changes",
          "set aside rather than passed through"]),
        ({}, {"author": "Spec Team", "date_convention": "utc"},
         ["shown as tracked changes by “Spec Team” dated in UTC"]),
    ],
    "remove_hidden_text": [
        ({}, {}, ["Remove the hidden text", "“Hidden” font setting",
                  "in the document body, the headers, the footers, the footnotes and the endnotes",
                  "shown as tracked changes", "paragraph mark are all hidden is deleted whole", "listed in the preview"]),
        # the body only, plain, a file with no hidden text is an exception
        ({"scope": ["document"]}, {"tracked": False, "allow_missing": False},
         ["in the document body, without tracked changes", "a file with no hidden text is set aside"]),
        # the scope given as a parameter, with a named author
        ({}, {"scope": ["footnotes", "endnotes"], "author": "Jane"},
         ["in the footnotes and the endnotes", "shown as tracked changes by “Jane”"]),
    ],
    "remove_notes": [
        ({}, {"rules": [{"style_in": ["SpecNote"]},
                        {"text_regex": "^(Retain|Delete|Revise|Coordinate)\\b", "color_in": ["0000FF"]}],
              "runs": {"color_in": ["0000FF"]}},
         ["Delete the note paragraphs", "those in the “SpecNote” style",
          "whose text matches the pattern “^(Retain|Delete|Revise|Coordinate)\\b” and is all coloured “0000FF”",
          "remove the inline notes (text coloured “0000FF”) from the other paragraphs in the document body",
          "shown as tracked changes", "Hyperlink text and pictures are never taken as notes"]),
        # one rule with two tests, in the body and headers, plain, nothing-to-remove is an exception
        ({"scope": ["document", "headers"]},
         {"rules": [{"hidden": True, "style_in": ["Note"]}], "tracked": False, "allow_missing": False},
         ["those in the “Note” style whose text is all hidden", "in the document body and the headers",
          "without tracked changes", "a file with no such note is set aside"]),
        # inline notes only: hidden runs in two colours (a hex value and a theme colour), by a named author
        ({}, {"runs": {"color_in": ["FF0000", "accent2"], "hidden": True}, "author": "Spec Team"},
         ["Remove the inline notes (hidden text coloured “FF0000” or “accent2”)",
          "shown as tracked changes by “Spec Team”"]),
    ],
    "remove_highlight": [
        ({}, {}, ["Remove the highlighting of every colour from the text",
                  "in the document body, the headers, the footers, the footnotes and the endnotes",
                  "other formatting stay", "a style that highlights its text is left as it is"]),
        # two colours (Word's spelling turned into words), tracked with an author, the body only, missing is an exception
        ({"scope": ["document"]}, {"colors": ["yellow", "darkBlue"], "tracked": True, "author": "Jane",
                                   "allow_missing": False},
         ["Remove the yellow and dark blue highlighting from the text in the document body",
          "shown as tracked formatting changes by “Jane”", "a file with no such highlighting is set aside"]),
    ],
    "remove_hyperlinks": [
        ({}, {}, ["Remove the external hyperlinks (web addresses and e-mail links)",
                  "in the document body, the headers, the footers, the footnotes and the endnotes",
                  "keeping the linked words but taking the “Hyperlink” and “FollowedHyperlink” styles off them",
                  "A web address that no remaining link uses leaves the file"]),
        # internal links (a table of contents), the words keep their link style, the body only, missing is an exception
        ({"scope": ["document"]}, {"which": "internal", "keep_style": True, "allow_missing": False},
         ["Remove the internal hyperlinks", "table of contents", "in the document body",
          "keeping the linked words in the “Hyperlink” style (blue and underlined)",
          "file with no such link is set aside rather than passed through"]),
        ({}, {"which": "all"}, ["Remove every hyperlink, external (web addresses and e-mail links) or internal"]),
    ],
    "unlink_fields": [
        ({}, {"types": ["DOCPROPERTY", "REF"]},
         ["Turn the “DOCPROPERTY” and “REF” fields",
          "in the document body, the headers, the footers, the footnotes and the endnotes",
          "into the plain text they show", "What each field shows stays, with its formatting; the field code goes"]),
        # every type but the page numbers, in the headers and footers, nothing-to-convert is an exception
        ({"scope": ["headers", "footers"]}, {"types": "all", "allow_missing": False},
         ["Turn every field but the page numbers in the headers and footers into the plain text it shows",
          "page numbers are never turned into text", "freeze at the page they showed",
          "a file with no such field is set aside"]),
        # date fields: the reader is told the date freezes; a single type name given as a string, lower case
        ({}, {"types": "savedate"},
         ["Turn the “SAVEDATE” field", "into the plain text it shows",
          "a date or time field keeps the date it showed when the file was last saved"]),
    ],
}
