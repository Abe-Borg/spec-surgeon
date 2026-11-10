"""Describe cases: a part replaced with a file of the user's (Obliterate's hands-on tools). The part's name and the
file's are quoted; the sentence says the bytes are placed as they are and that nothing else changes."""
from __future__ import annotations

CASES = {
    "replace_part": [
        ({}, {"part": "word/document.xml", "file": "C:/work/21 13 13 - word_document.xml", "sha256": "0" * 64},
         ["Replace the part “word/document.xml”", "with the file “21 13 13 - word_document.xml”", "byte for byte",
          "Not tracked", "no other part of the document changes"]),
        ({}, {"part": "word/media/image1.png", "file": "logo.png"},
         ["“word/media/image1.png”", "“logo.png”"]),
    ],
}
