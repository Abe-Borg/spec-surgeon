"""Snippets (docs/BUILTIN_OPS_BATCH1_DESIGN.md §9): a saved paragraph — typed or taken from a document — inserted by
insert_paragraph after (or before) an anchor in every file that has it, at most once per file, from the command line
without a key. In a conversation the snippet's words are the user's only once the user gives them: a spec Claude
writes that places them on its own is refused. Scripted fake client only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_batch1
from spec_surgeon import library
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.conversation import Conversation
from spec_surgeon.editspec import plan_spec
from spec_surgeon.ops import JobException
from spec_surgeon.package import DocxPackage, W

TEXT = "Perform a hydrant flow test before design and record the static and residual pressures."
ANCHOR = r"Schedule 40 per NFPA 13"
REQUEST = "add our flow test paragraph after the pipe schedule"


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.fixture
def snippet():
    return library.save_snippet("Hydrant flow test", TEXT, description="the flow test paragraph")


def files(folder):
    return sorted(folder.glob("*.docx"))


def texts(docx: bytes):
    root = DocxPackage(docx).xml("word/document.xml")
    return ["".join(t.text or "" for t in p.iter(W + "t")) for p in root.iter(W + "p")]


def submit(spec, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid))


def insert_spec(text=TEXT):
    return {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_paragraph", "select": {"scope": ["document"], "paragraphs": {"text_regex": ANCHOR}},
                     "params": {"text": text, "allow_missing": True, "author": "x"}}]}


# ---- the library kind -------------------------------------------------------------------------------------------------

def test_a_snippet_is_saved_listed_shown_and_deleted(snippet):
    assert snippet.label() == "Snippet: Hydrant flow test"
    assert [i.label() for i in library.list_items()] == ["Snippet: Hydrant flow test"]
    item, sn = library.load_snippet("hydrant flow test")
    assert sn == {"text": TEXT, "style": None, "formatting": "inherit", "source": "typed"}
    assert item.path.is_file()
    library.delete("Hydrant flow test")
    assert library.list_items() == []


@pytest.mark.parametrize("text,words", [("", "needs some text"), ("A.\tFlow test", "tab"), ("one\ntwo", "line break"),
                                        ("zero​width", "cannot see"), ("a field \x00 here", "field")])
def test_a_snippet_must_be_one_plain_visible_paragraph(text, words):
    with pytest.raises(library.LibraryError) as e:
        library.save_snippet("Bad", text)
    assert words in str(e.value)
    assert library.list_items() == []


def test_a_snippet_name_belongs_to_one_item():
    library.save_recipe("Flow test", {"request": "r", "summary": "s", "mode": "formatting",
                                      "ops": [{"op": "remove_header_footer", "select": {"sections": "all"}}]})
    with pytest.raises(library.LibraryError) as e:
        library.save_snippet("Flow test", TEXT)
    assert "cannot take that name" in str(e.value)
    library.save_snippet("Flow test 2", TEXT)
    with pytest.raises(library.LibraryError):
        library.save_snippet("Flow test 2", "Other words.")
    library.save_snippet("Flow test 2", "Other words.", replace=True)
    assert library.load_snippet("Flow test 2")[1]["text"] == "Other words."
    with pytest.raises(library.LibraryError):
        library.save_snippet("Bad formatting", TEXT, formatting="bold")


def test_a_snippet_edited_by_hand_into_something_unusable_is_refused(snippet):
    snippet.path.write_text(json.dumps({"text": "A.\tB", "formatting": "inherit"}), encoding="utf-8")
    with pytest.raises(library.LibraryError) as e:
        library.load_snippet("Hydrant flow test")
    assert "tab" in str(e.value)


def test_a_snippet_taken_from_a_document_paragraph(folder):
    f = files(folder)[0]
    assert library.snippet_from_document(f, r"^PROJECT REVISION") == "PROJECT REVISION HISTORY"
    with pytest.raises(library.LibraryError) as e:                    # a typed "B.<tab>…" is not one plain paragraph
        library.snippet_from_document(f, r"^B\.\s*The auditor")
    assert "tab" in str(e.value)
    with pytest.raises(library.LibraryError) as e:                    # several paragraphs match
        library.snippet_from_document(f, r"FM Global")
    assert "make the pattern narrower" in str(e.value)
    with pytest.raises(library.LibraryError) as e:
        library.snippet_from_document(f, r"no such words")
    assert "no paragraph" in str(e.value)


# ---- inserting it -----------------------------------------------------------------------------------------------------

def test_a_snippet_goes_after_the_anchor_in_every_file_that_has_it(folder, snippet, tmp_path):
    (folder / "no anchor.docx").write_bytes(build_batch1())                    # no "Schedule 40 per NFPA 13" here
    item, sn = library.load_snippet("Hydrant flow test")
    spec = library.snippet_spec(item.name, sn, ANCHOR)
    assert spec["ops"][0]["expect"] == {"text_diffs": {"max": 1}} and spec["ops"][0]["params"]["allow_missing"]
    pkg = DocxPackage(files(folder)[0].read_bytes())
    plan, _ = plan_spec(pkg, spec)
    assert [d.after for d in plan.diffs] == [TEXT]
    # an anchor that matches twice in one file: that file is set aside, never given two copies
    with pytest.raises(JobException) as e:
        plan_spec(pkg, library.snippet_spec(item.name, sn, r"FM Global"))
    assert e.value.code == "expect"
    batch = run_batch([str(folder)], "", {}, tmp_path / "out", workers=1, do_render=False, spec=spec)
    assert {fr.status for fr in batch.files} == {"PASS"}
    for fr in batch.files:
        if fr.source.name == "no anchor.docx":                                # passes unchanged: nothing delivered
            assert not fr.delivered_to
            continue
        got = texts(Path(fr.delivered_to).read_bytes())
        i = next(k for k, t in enumerate(got) if "Schedule 40 per NFPA 13" in t)
        assert got[i + 1] == TEXT


def test_a_snippet_before_its_anchor_untracked_and_with_its_style(folder):
    library.save_snippet("Note", TEXT, style="Heading1", formatting="none")
    item, sn = library.load_snippet("Note")
    spec = library.snippet_spec(item.name, sn, ANCHOR, "before", tracked=False)
    assert spec["mode"] == "untracked" and spec["ops"][0]["params"]["style"] == "Heading1"
    with pytest.raises(library.LibraryError):
        library.snippet_spec(item.name, sn, "")
    with pytest.raises(library.LibraryError):
        library.snippet_spec(item.name, sn, "(unclosed")


def test_the_command_line_adds_shows_and_uses_a_snippet_without_a_key(folder, tmp_path, capsys):
    assert cli_main(["library", "add-snippet", "Hydrant flow test", "--text", TEXT, "--description", "flow"]) == 0
    assert cli_main(["library", "add-snippet", "From doc", "--from", str(files(folder)[0]),
                     "--paragraph", r"^PROJECT REVISION"]) == 0
    assert cli_main(["library", "add-snippet", "Neither"]) == 2
    assert cli_main(["library", "add-snippet", "Tabbed", "--text", "A.\tB"]) == 2
    out = capsys.readouterr()
    assert "Saved to your library: Snippet: Hydrant flow test" in out.out and "nothing was saved" in out.err
    assert cli_main(["library", "show", "Hydrant flow test"]) == 0
    shown = capsys.readouterr().out
    assert f"«{TEXT}»" in shown and "--after REGEX" in shown
    assert cli_main(["library", "use", "Hydrant flow test", str(folder), "--out", str(tmp_path / "o")]) == 2
    assert "--after REGEX or --before REGEX" in capsys.readouterr().err
    assert cli_main(["library", "use", "Hydrant flow test", str(folder), "--after", ANCHOR, "--out",
                     str(tmp_path / "o"), "--yes", "--no-render"]) == 0
    outs = sorted((tmp_path / "o" / "run" / "outputs").glob("*.docx"))
    assert len(outs) == 2 and all(TEXT in texts(p.read_bytes()) for p in outs)
    assert cli_main(["library", "list", "--kind", "snippet"]) == 0
    assert "Snippet: From doc" in capsys.readouterr().out


# ---- in the conversation -------------------------------------------------------------------------------------------

def test_a_spec_placing_a_snippets_words_the_user_never_gave_is_refused(folder, snippet):
    """A saved snippet is not the user's wording for THIS request until the user gives it: Claude's spec that places
    its words on its own is refused every time (placed_words traces every word to the user's own sources)."""
    fake = FakeClient(submit(insert_spec(), "s1"), submit(insert_spec(), "s2"), submit(insert_spec(), "s3"))
    c = Conversation(REQUEST, files(folder), client=fake)
    turn = c.start()
    assert turn.status == "failed" and turn.spec is None
    assert any("the user never gave them verbatim" in e and TEXT in e for e in turn.errors), turn.errors
    assert TEXT not in c.sources
