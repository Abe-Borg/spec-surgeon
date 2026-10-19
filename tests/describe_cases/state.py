"""Describe cases for the state family: resolve_tracked_changes, set_document_state, set_document_properties,
set_language and add_review_comments — (select, params, [expected substrings]); the typical call first, then
non-default settings. resolve_tracked_changes defaults to every text part and to leaving a file without a matching
change alone; the three file-level ops take no selector; add_review_comments needs a targeted paragraph selector and
by default sets aside a file in which nothing matches."""

CASES = {
    "resolve_tracked_changes": [
        ({}, {"action": "accept"},
         ["Accept every tracked change", "insertions, deletions, moved text and formatting changes",
          "in the document body, the headers, the footers, the footnotes, the endnotes and the comments"]),
        # two authors in a date window, the body only, a file without one of their changes is an exception
        ({"scope": ["document"]},
         {"action": "reject", "authors": ["Jane Doe", "R. Smith"], "on_or_after": "2026-01-01", "before": "2026-02-01",
          "allow_missing": False},
         ["Reject every tracked change by “Jane Doe” or “R. Smith”", "made on or after 1 January 2026 and before "
          "1 February 2026", "in the document body", "Rejected deletions put their words back into the text;",
          "a file with no such tracked change is set aside"]),
        ({"scope": ["headers", "footers"]}, {"action": "accept", "on_or_after": "2026-03-15T09:30"},
         ["Accept every tracked change made on or after 15 March 2026 at 09:30", "in the headers and footers"]),
    ],
    "set_document_state": [
        ({}, {"track_changes": "off"},
         ["Turn the file's Track Changes setting off, so it opens with tracking off",
          "Tracked changes already in the file stay as they are"]),
        ({}, {"track_changes": "on", "final": True},
         ["Track Changes setting on", "mark the file as final", "read-only", "“Marked as Final” bar"]),
        ({}, {"final": False}, ["Remove the file's Mark as Final, so it opens for editing again"]),
    ],
    "set_document_properties": [
        ({}, {"set": {"title": "Section 09 91 23 — Painting", "company": "Acme Engineering"}},
         ["In the file's properties (File → Info)", "set the title to “Section 09 91 23 — Painting”",
          "the company to “Acme Engineering”"]),
        # author names cleared, a custom property set and another removed
        ({}, {"clear": ["author", "last_modified_by"], "custom": {"Project": "Hospital Wing B"}, "clear_custom": ["Draft"]},
         ["clear the author and the last-modified-by name", "set the custom property “Project” to “Hospital Wing B”",
          "remove the custom property “Draft”", "Tracked changes and comments keep the names of their authors"]),
        ({}, {"set": {"status": "Issued for Construction", "manager": "A. Borg"}, "clear": ["keywords", "comments"]},
         ["the status to “Issued for Construction”", "the manager to “A. Borg”, and clear the keywords and the comments"]),
    ],
    "set_language": [
        ({}, {"language": "en-US"},
         ["Set the proofing language of all text to “en-US”", "every style, run and paragraph mark that names a language",
          "document default", "nothing is shown as a tracked change"]),
        # all three scripts, only replacing the languages listed
        ({}, {"language": "en-GB", "east_asian": "ja-JP", "complex_script": "ar-SA", "replace_only": ["en-US", "en-AU"],
              "tracked": False},
         ["“en-GB” for Latin text", "“ja-JP” for East Asian text", "“ar-SA” for complex-script text",
          "only where it is now “en-US” or “en-AU”, leaving text in any other language as it is"]),
        ({}, {"east_asian": "zh-CN"}, ["proofing language of East Asian text to “zh-CN”"]),
    ],
    "add_review_comments": [
        ({"paragraphs": {"text_regex": "^2\\.3\\s"}},
         {"text": "Confirm the fire rating with the architect.", "author": "Jane Doe"},
         ["Attach a Word comment by “Jane Doe” reading “Confirm the fire rating with the architect.”",
          "to paragraphs whose text matches the pattern “^2\\.3\\s”", "one comment covering each whole paragraph",
          "Existing comments and tracked changes stay as they are"]),
        # a source quotation in a second paragraph, initials, an exact count, and a file with no match passes through
        ({"paragraphs": {"style_in": ["PR1"], "has_bold": True}, "scope": ["document"]},
         {"text": "Verify against Division 01.", "source": "Per 01 33 00 §1.4.", "author": "Spec Team", "initials": "ST",
          "allow_missing": True, "expected_count": 3},
         ["by “Spec Team” (initials “ST”)", "reading “Verify against Division 01.”",
          "with the source wording “Per 01 33 00 §1.4.” as a second paragraph", "to bold paragraphs in the “PR1” style",
          "set aside unless exactly 3 paragraphs match", "a file with no matching paragraph is passed through unchanged"]),
        ({"paragraphs": {"after_heading_regex": "^PART 2"}}, {"text": "Check the model number.", "author": "QA",
                                                              "expected_count": 1},
         ["after the first paragraph matching “^PART 2”", "unless exactly 1 paragraph matches"]),
    ],
}
