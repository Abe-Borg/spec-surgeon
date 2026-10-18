"""Snippets (docs/BUILTIN_OPS_BATCH1_DESIGN.md §9): a saved paragraph — typed or taken from a document — inserted by
insert_paragraph after (or before) an anchor in every file that has it, at most once per file; from the command line
without a key, from the window's Your library…, and offered by Claude in a conversation, where the user's pick approves
its exact words (the item's hash checked first) and Claude writes the spec. Scripted fake client only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_batch1
from spec_surgeon import compiler as compiler_module
from spec_surgeon import library
from spec_surgeon.batch import run_batch
from spec_surgeon.cli import main as cli_main
from spec_surgeon.conversation import Conversation, LibraryChanged
from spec_surgeon.editspec import plan_spec
from spec_surgeon.gui import Session
from spec_surgeon.ops import JobException
from spec_surgeon.package import DocxPackage, W
from test_gui import qt, window  # noqa: F401 — the fixture and the helper

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


def ask(tid="a1", **inp):
    return resp(tool("ask_user", inp, tid))


def submit(spec, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid))


def insert_spec(text=TEXT):
    return {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "insert_paragraph", "select": {"scope": ["document"], "paragraphs": {"text_regex": ANCHOR}},
                     "params": {"text": text, "allow_missing": True, "author": "x"}}]}


def last_user_texts(fake, i=-1):
    return [b["content"] if b.get("type") == "tool_result" else b["text"]
            for b in fake.requests[i]["messages"][-1]["content"]]


# ---- the library kind -------------------------------------------------------------------------------------------------

def test_a_snippet_is_saved_listed_shown_and_deleted(snippet):
    assert snippet.label() == "Snippet: Hydrant flow test"
    assert [i.label() for i in library.list_items()] == ["Snippet: Hydrant flow test"]
    item, sn = library.load_snippet("hydrant flow test")
    assert sn == {"text": TEXT, "style": None, "formatting": "inherit", "source": "typed"}
    assert len(library.item_hash(item)) == 64
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

def offer(name="Hydrant flow test", tid="a1", **extra):
    return ask(tid, understanding="You want your saved paragraph.", message="Insert it?",
               choices=[{"kind": "snippet", "name": name, **extra}])


def snippet_outcome_proposal():
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [
        {"id": "flow-test", "type": "insert_paragraph", "target": {"locators": [
            {"contains_text": "Schedule 40 per NFPA 13"}]}, "text": TEXT, "position": "after",
         "tracked": True, "author": "x"}]},
        "rationale": "Insert the approved saved flow-test paragraph after each original pipe-schedule paragraph."}))


def test_claude_offers_a_snippet_and_the_pick_approves_its_words(folder, snippet):
    fake = FakeClient(offer(wording="something else"), submit(insert_spec(), "s1"))
    c = Conversation(REQUEST, files(folder), client=fake)
    turn = c.start()
    first = fake.requests[0]["messages"][0]["content"]
    assert "Hydrant flow test" in first and TEXT not in first                # the index never shows the words
    (ch,) = turn.choices
    assert ch.kind == "snippet" and ch.words == [TEXT] and ch.label.startswith("Insert your snippet “Hydrant flow test”")
    assert any("was ignored" in e for e in turn.errors)                       # Claude's wording never reaches it
    # Claude's spec placing the snippet's words BEFORE the pick would be refused: the words are not the user's yet
    assert TEXT not in c.sources
    turn = c.reply(choice_id=ch.id)
    assert turn.status == "ok" and c.sources[-1] == TEXT
    said = last_user_texts(fake)[0]
    assert "approving these exact words: «" + TEXT + "»" in said and "insert_paragraph" in said


def test_a_spec_placing_a_snippets_words_before_the_pick_is_refused(folder, snippet):
    fake = FakeClient(submit(insert_spec(), "s1"), submit(insert_spec(), "s2"), submit(insert_spec(), "s3"))
    c = Conversation(REQUEST, files(folder), client=fake)
    turn = c.start()
    assert turn.status == "failed" and turn.spec is None
    assert any("the user never gave them verbatim" in e and TEXT in e for e in turn.errors), turn.errors


def test_a_snippet_edited_between_the_offer_and_the_pick_is_refused(folder, snippet):
    fake = FakeClient(offer())
    c = Conversation(REQUEST, files(folder), client=fake)
    (ch,) = c.start().choices
    library.save_snippet("Hydrant flow test", "Other words entirely.", replace=True)
    with pytest.raises(LibraryChanged):
        c.reply(choice_id=ch.id)
    assert TEXT not in c.sources and "Other words entirely." not in c.sources
    assert c.turn is not None and c.choice(ch.id) is ch                        # the turn stays open
    assert len(fake.requests) == 1                                             # nothing was sent


def test_a_snippet_choice_is_chat_only_must_exist_and_is_a_reply(folder, snippet):
    c = Conversation(REQUEST, files(folder), client=FakeClient(offer()), mode="compile")
    turn = c.start()
    assert not any(ch.kind == "snippet" for ch in turn.choices)
    assert any("offered in a conversation only" in e for e in turn.errors)
    c = Conversation(REQUEST, files(folder), client=FakeClient(offer("No such snippet")))
    turn = c.start()
    assert turn.choices == [] and any("No such snippet" in e for e in turn.errors)
    c = Conversation(REQUEST, files(folder), client=FakeClient(offer()))
    (ch,) = c.start().choices
    with pytest.raises(ValueError):
        c.take_library_choice(ch.id)
    with pytest.raises(ValueError):
        c.take_app_choice(ch.id)
    assert TEXT not in c.sources


def test_read_library_item_shows_a_snippets_text_under_the_rule(folder, snippet):
    detail = compiler_module.library_item_detail("Hydrant flow test")
    assert detail["kind"] == "snippet" and detail["text"] == TEXT and "ONLY once the user picks" in detail["text_rule"]
    entry = compiler_module.library_entry(snippet)
    assert entry["characters"] == len(TEXT) and TEXT not in json.dumps(entry)


def test_the_terminal_snippet_choice_asks_for_the_words_then_claude_writes_the_spec(folder, snippet, tmp_path,
                                                                                   monkeypatch, capsys):
    from spec_surgeon import cli
    fake = FakeClient(offer(), submit(insert_spec(), "s1"), snippet_outcome_proposal())
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    answers = iter(["#1", "y", "y", "y"])
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": (asked.append(prompt), next(answers))[1])
    out_dir = tmp_path / "o"
    assert cli_main(["do", REQUEST, str(folder), "--out", str(out_dir), "--no-render"]) == 0
    assert asked[1] == f"Approve these exact words for your documents: «{TEXT}»? [y/N] "
    assert asked[2] == "Approve these outcomes and every document's targets? [y/N] "
    saved = json.loads((out_dir / "conversation.json").read_text(encoding="utf-8"))
    assert saved["approved_words"] == [REQUEST, TEXT]
    outs = sorted((out_dir / "run" / "outputs").glob("*.docx"))
    assert len(outs) == 2 and all(TEXT in texts(p.read_bytes()) for p in outs)


# ---- the window ----------------------------------------------------------------------------------------------------

def test_the_window_adds_a_snippet_and_uses_it(qt, folder, tmp_path):
    from spec_surgeon.ui.app import NEW_SNIPPET, RecordingPrompts
    prompts = RecordingPrompts(texts=["Hydrant flow test", TEXT], items=[NEW_SNIPPET])
    s = Session(out_root=tmp_path / "out")
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    assert w.do_library()
    assert [i.label() for i in library.list_items()] == ["Snippet: Hydrant flow test"]
    prompts.items = ["Snippet: Hydrant flow test"]
    prompts.texts = [ANCHOR]
    assert w.do_library()
    assert s.spec is not None and s.spec["ops"][0]["params"]["text"] == TEXT
    assert s.spec_source == "your snippet 'Hydrant flow test'"
    assert "your snippet 'Hydrant flow test'" in w.request_page.spec_bar.text.text()
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.preview is not None and ui["buttons"]["run"].isEnabled(), w.run_page.run_hint.text()
    prompts.items, prompts.texts = [NEW_SNIPPET], ["Tabbed", "A.\tB"]
    assert not w.do_library()
    assert any(k == "warn" and "Nothing was saved" in t for k, _, t in prompts.shown)


def test_the_window_shows_a_snippet_choice_as_insert_it_and_claude_writes_the_spec(qt, folder, snippet, tmp_path):
    fake = FakeClient(offer(), submit(insert_spec(), "s1"), snippet_outcome_proposal())
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set(REQUEST)
    ui["buttons"]["preview"].click()
    ui["wait"]()
    ((cid, button),) = ui["choices"]()
    assert button.text() == "Insert it"
    labels = [lab.text() for lab in ui["chat"].findChildren(type(w.request_page.info.text))]
    assert any(f"Exact words: «{TEXT}»" in t for t in labels)
    button.click()
    ui["wait"]()
    assert s.spec is not None and s.spec["ops"][0]["params"]["text"] == TEXT and TEXT in s.conversation.sources
    assert s.preview is not None
    assert s.outcome_review["status"] == "PROPOSED" and not ui["buttons"]["run"].isEnabled()
    w.preview_page.outcome_approve_btn.click()
    assert s.outcome_approval == s.outcome_review["digest"] and ui["buttons"]["run"].isEnabled()
