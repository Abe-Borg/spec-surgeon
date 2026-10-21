"""Describe cases for the pictures family (built-in batch 6, F4): insert_image, remove_images and resize_images —
(select, params, [expected substrings]); the typical call first, then non-default settings."""

CASES = {
    "insert_image": [
        # the usual request: the attached site plan after the paragraph that introduces it, at its own size
        ({"paragraphs": {"text_regex": "^A\\.\\s+See the site plan"}}, {"image": "C:/Plans/site.png"},
         ["Insert the picture “C:/Plans/site.png” as its own paragraph after each of the paragraphs whose text "
          "matches the pattern “^A\\.\\s+See the site plan”",
          "at its own size, made smaller to fit the text width when it is wider", "shown as a tracked change"]),
        # a width (proportions kept), centred, with alt text, before the anchor, untracked
        ({"paragraphs": {"text_regex": "^PART 2"}},
         {"image": "C:/Logos/seal.png", "position": "before", "width": 2.5, "align": "center",
          "description": "Engineer's seal", "tracked": False},
         ["before each of the paragraphs whose text matches the pattern “^PART 2”", "2.5 inches wide with its "
          "proportions kept", "centred", "with the alt text “Engineer's seal”", "without tracked changes"]),
        # both sides, a style, in the footers, by an author, files without the anchor left alone
        ({"paragraphs": {"style_in": ["Footer"]}, "scope": ["footers"]},
         {"image": "logo.bmp", "width": 1, "height": 0.5, "style": "Figure", "author": "Abraham", "allow_missing": True},
         ["each of the paragraphs in the “Footer” style in the footers", "1 inch wide and 0.5 inch high",
          "in the “Figure” style", "shown as a tracked change by “Abraham”",
          "Files with no such paragraph are left unchanged."]),
        # a height alone
        ({"paragraphs": {"text_regex": "^2\\.01"}}, {"image": "riser.gif", "height": 3},
         ["3 inches high with its proportions kept"]),
    ],
    "remove_images": [
        # the usual request: an old picture, by its file, tracked
        ({}, {"match": {"image": "C:/Logos/old-logo.png"}},
         ["Remove every picture that shows the file “C:/Logos/old-logo.png” in the document body, leaving the "
          "paragraphs that held them, shown as a tracked change."]),
        # every picture of the headers and footers, untracked
        ({"scope": ["headers", "footers"]}, {"match": "all", "tracked": False},
         ["Remove every picture in the headers and footers", "without tracked changes",
          "a picture's file leaves the document when nothing else shows it"]),
        # by name or alt text, files without one left alone, by an author
        ({}, {"match": {"name_regex": "^Site plan"}, "allow_missing": True, "author": "Abraham"},
         ["every picture whose name or description matches the pattern “^Site plan”", "by “Abraham”",
          "Files with no such picture are left unchanged."]),
    ],
    "resize_images": [
        # the usual request: every picture of the body 3 inches wide
        ({}, {"match": "all", "width": 3},
         ["Resize every picture in the document body to 3 inches wide, keeping its proportions",
          "(a picture's size is never a tracked change)"]),
        # a scale, on the header logo by its file
        ({"scope": ["headers"]}, {"match": {"image": "C:/Logos/logo.png"}, "scale": 50},
         ["Resize every picture that shows the file “C:/Logos/logo.png” in the headers to 50% of its present size"]),
        # both sides, by name, files without one left alone
        ({}, {"match": {"name_regex": "Detail"}, "width": 2, "height": 1.5, "allow_missing": True},
         ["to 2 inches wide and 1.5 inches high", "Files with no such picture are left unchanged."]),
        # a height alone
        ({}, {"match": "all", "height": 0.75}, ["to 0.75 inch high, keeping its proportions"]),
    ],
}
