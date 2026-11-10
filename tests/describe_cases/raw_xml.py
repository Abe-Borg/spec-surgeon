"""Describe cases: the raw XML edit (Obliterate mode only). Its XPath, part and names are engine vocabulary, so the
sentence quotes each one; a long fragment is cut, with its length."""
from __future__ import annotations

CASES = {
    "edit_xml": [
        ({}, {"part": "word/document.xml", "xpath": "//w:sdt[w:sdtPr/w:dataBinding]", "count": 3, "action": "unwrap",
              "keep": "w:sdtContent"},
         ["Raw XML edit", "“word/document.xml”", "unwrap the 3 elements", "“//w:sdt[w:sdtPr/w:dataBinding]”",
          "“w:sdtContent”", "Not tracked"]),
        ({}, {"part": "word/header2.xml", "xpath": "//w:r[w:pict]", "count": 1, "action": "remove"},
         ["remove the 1 element", "with everything inside them", "nothing else in the part"]),
        ({}, {"part": "word/document.xml", "xpath": "//w:pgMar", "count": 2, "action": "set_attribute",
              "name": "w:header", "value": "576"},
         ["set the attribute “w:header” to “576” on the 2 elements"]),
        ({}, {"part": "word/document.xml", "xpath": "//w:pgMar", "count": 2, "action": "remove_attribute",
              "name": "w:gutter"}, ["remove the attribute “w:gutter” from the 2 elements"]),
        ({}, {"part": "word/document.xml", "xpath": "(//w:t)[1]", "count": 1, "action": "set_text",
              "text": "SECTION 21 13 13"}, ["set the text of the 1 element", "to “SECTION 21 13 13”"]),
        ({}, {"part": "word/document.xml", "xpath": "//w:body/w:p[2]", "count": 1, "action": "insert",
              "position": "after", "xml": "<w:p><w:r><w:t>Added line</w:t></w:r></w:p>"},
         ["insert the XML “<w:p><w:r><w:t>Added line</w:t></w:r></w:p>” after the 1 element"]),
        ({}, {"part": "word/document.xml", "xpath": "//w:body/w:p[2]", "count": 1, "action": "replace",
              "xml": "<w:p>" + "<w:r><w:t>word</w:t></w:r>" * 40 + "</w:p>"},
         ["replace the 1 element", "…” (1,", "characters)"]),
    ],
}
