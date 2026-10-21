"""Describe cases for the moving family: move_paragraphs — (select, params, [expected substrings]); the typical call
first, then non-default settings."""

ARTICLE = {"between_paragraphs": {"start_regex": "^2\\.3\\s+SUBMITTALS", "end_regex": "^2\\.4", "include_bounds": True},
           "text_regex": "^(?!2\\.4)"}

CASES = {
    "move_paragraphs": [
        # the usual request: a whole article (its heading through the paragraph before the next one), tracked
        ({"paragraphs": ARTICLE}, {"to": {"text_regex": "^1\\.5"}, "position": "after"},
         ["Move the paragraphs from the one matching “^2\\.3\\s+SUBMITTALS” through the one before the one matching "
          "“^2\\.4” to after the paragraph matching “^1\\.5”, shown as a tracked deletion where they are and a tracked "
          "insertion where they go."]),
        # untracked, both bounds included, before the destination, a named author ignored when untracked
        ({"paragraphs": {"between_paragraphs": {"start_regex": "^PART 3", "end_regex": "^END OF SECTION",
                                                "include_bounds": True}}},
         {"to": {"text_regex": "^PART 2"}, "position": "before", "tracked": False, "allow_missing": True},
         ["from the one matching “^PART 3” through the one matching “^END OF SECTION”",
          "together with any tables among them", "to before the paragraph matching “^PART 2”",
          "moving them as they are without tracked changes", "Files with no such paragraphs are left unchanged."]),
        # the paragraphs between two others, in the headers, tracked by a named author, a destination by style
        ({"scope": ["headers"], "paragraphs": {"between_paragraphs": {"start_regex": "^PROJECT", "end_regex": "^ISSUE"}}},
         {"to": {"style_in": ["Header"], "text_regex": "^ISSUE"}, "position": "after", "author": "Spec Team"},
         ["the paragraphs between the one matching “^PROJECT” and the one matching “^ISSUE”",
          "to after the one paragraph in the “Header” style whose text matches the pattern “^ISSUE”", "in the headers",
          "by “Spec Team”"]),
        # a stretch chosen by its text alone
        ({"paragraphs": {"text_regex": "^Retain", "case_insensitive": True}},
         {"to": {"text_regex": "^END OF SECTION", "case_insensitive": True}, "position": "before"},
         ["Move the paragraphs whose text matches the pattern “^Retain” (ignoring case)",
          "to before the paragraph matching “^END OF SECTION” (ignoring case)"]),
    ],
}
