"""``tests/section_lines.py`` (backlog S4): the reader the owner runs over real specifications to show what the
reference rules see at the top of each. It quotes clipped first lines and never writes a file."""
from __future__ import annotations

from pathlib import Path

import section_lines
from fx_revisions import redline
from make_fixtures import build


def test_it_prints_what_the_rules_read_and_the_top_of_each_document_and_writes_nothing(tmp_path, capsys):
    (tmp_path / "21 13 13 - Wet.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (tmp_path / "redline.docx").write_bytes(redline())
    (tmp_path / "broken.docx").write_bytes(b"not a package")
    (tmp_path / "~$lock.docx").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("not a document")
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()}
    assert section_lines.main(["section_lines.py", str(tmp_path)]) == 0
    said = capsys.readouterr().out
    assert said.startswith("3 document(s) in the folder\n")          # Word's lock file and other files are not read
    wet = said.split("=== 21 13 13 - Wet.docx", 1)[1].split("===", 1)[0]
    assert "SECTION_NUMBER: '21 13 13'" in wet and "SECTION_TITLE: 'WET-PIPE SPRINKLER SYSTEMS'" in wet
    assert "body     SECTION 21 13 13" in wet and "default footer: 21 13 13 - 1" in wet
    assert 'paragraphs that start with "Section" or hold END OF SECTION: 1' in wet
    # a document the rules read nothing from says so with the refusal's code, and shows what it starts with instead
    other = said.split("=== redline.docx", 1)[1]
    assert "SECTION_NUMBER: NOT READ [not-found]" in other and "A. Pipe shall be hot-dip galvanized" in other
    assert "=== broken.docx\n    cannot be read: BadZipFile" in said
    # every line is clipped, and nothing in the folder was written, added or removed
    assert max(len(line) for line in said.splitlines()) < 2 * section_lines.CLIP + 60
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in tmp_path.iterdir()} == before
    assert section_lines.shown("a\tb\nc" + "x" * 500) == ("a→b⏎c" + "x" * 500)[:section_lines.CLIP - 1] + "…"
    assert section_lines.main(["section_lines.py"]) == 2 and section_lines.main(["x", str(tmp_path / "none")]) == 2
