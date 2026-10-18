"""Claude and your library (docs/design/2026-10-16_claude_and_your_library.md; CLAUDE.md rules 13 and 17): in a
conversation Claude sees the library — an index in the first message, ``list_library``, ``read_library_item`` — and
may NAME one of your recipes or operations as a choice; the app builds that choice from the library, shows the words a
recipe places, and your pick is what approves them. Scripted fake client only — no test calls Claude.

Planted here, each failing without the code it pins: a spec that places a recipe's words before the pick is refused;
an invented name, a name of the other kind, a tampered operation, a recipe whose template is gone, one that plans on
none of these files or places words you cannot see — no choice, said to you and to Claude; Claude's wording on a
library choice is ignored; each placed word is its own source; an item edited between the offer and the pick is
refused; a saved operation that fails its trials is told to Claude and changes nothing; the index, the tools and the
kinds are chat-only; the window row and the terminal prompt."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon import library
from spec_surgeon.cli import main as cli_main
from spec_surgeon.conversation import (APP_CHOICE_KINDS, CHOICE_KINDS_FROM_CLAUDE, LIBRARY_CHOICE_KINDS, Conversation,
                                       LibraryChanged)
from spec_surgeon.gui import OperationFailed, Session
from test_codegen import GOOD_OP
from test_conversation import assert_well_formed
from test_compiler import remove_outcome_proposal
from test_gui import qt, window  # noqa: F401 — the fixture and the helper
from test_library import passing_op

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
STAMP = "ISSUED FOR PERMIT 2026-11-02"
REQUEST = "stamp the cover like last time"
STAMP_OP = {"op": "replace_text", "select": {"scope": ["document"]},
            "params": {"find": "REVISION HISTORY", "replace": STAMP, "tracked": True, "author": "x"}}
RECIPE = {"request": "stamp the cover for the permit set", "summary": "Replace the cover stamp.",
          "mode": "content", "ops": [STAMP_OP]}
PLACES_STAMP = {"request": "r", "summary": "s", "mode": "content", "ops": [STAMP_OP]}


def placing(*words):
    """A spec of Claude's that places ``words`` (one replace per word, each in its own paragraph)."""
    anchors = ["REVISION HISTORY", "SECTION 21"]
    return {"request": "r", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": a, "replace": w, "tracked": True, "author": "x"}}
                    for a, w in zip(anchors, words)]}


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.fixture
def recipe():
    return library.save_recipe("Permit stamp", RECIPE, "the cover stamp for permit sets")[0]


def files(folder):
    return sorted(folder.glob("*.docx"))


def ask(tid="a1", **inp):
    return resp(tool("ask_user", inp, tid))


def submit(spec, tid="s1", **extra):
    return resp(tool("submit_editspec", {"spec": spec, **extra}, tid))


def offer(kind, name, tid="a1", **extra):
    return ask(tid, understanding="You want the saved one.", message="Use it?", choices=[{"kind": kind, "name": name, **extra}])


def quiet(m):
    pass


def first_message(fake):
    return fake.requests[0]["messages"][0]["content"]


def last_user_texts(fake, i=-1):
    """What the user message that closed request ``i`` said, block by block: the owed tool results first (an ask_user
    result carries the user's words), then the app's notes, then the user's words when no ask carried them."""
    return [b["content"] if b.get("type") == "tool_result" else b["text"] for b in fake.requests[i]["messages"][-1]["content"]]


def scripted_input(monkeypatch, answers):
    """``input`` answered from ``answers``; returns the prompts it was asked, in order."""
    prompts = []
    it = iter(answers)

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(it)
    monkeypatch.setattr("builtins.input", fake_input)
    return prompts


# ---- what Claude sees, and where ------------------------------------------------------------------------------------

def test_the_index_the_tools_and_the_kinds_are_chat_only(folder, recipe, tmp_path):
    g = passing_op(folder, tmp_path)
    library.save_operation("Header distance", g, "0.625 in")
    fake = FakeClient(submit(REMOVE))
    c = Conversation(REQUEST, files(folder), client=fake)
    assert c.start().status == "ok"
    first = first_message(fake)
    assert "YOUR LIBRARY" in first and "Permit stamp" in first and "Header distance" in first
    assert STAMP not in first and "places_words\": 1" in first              # the index counts the words, never shows them
    assert "set_header_distance" in first and "the cover stamp for permit sets" in first
    names = [t["name"] for t in fake.requests[0]["tools"]]
    assert names[-2:] == ["list_library", "read_library_item"] and "view_pages" in names
    ask_user = next(t for t in fake.requests[0]["tools"] if t["name"] == "ask_user")
    choice = ask_user["input_schema"]["properties"]["choices"]["items"]["properties"]
    assert choice["kind"]["enum"][-3:] == ["recipe", "operation", "snippet"] and "name" in choice
    assert "YOUR LIBRARY" in fake.requests[0]["system"] and "read_library_item" in fake.requests[0]["system"]
    # the one-shot compile: no index, no library tool, no library kind, the same ask_user as ever
    fake2 = FakeClient(ask(choices=[{"kind": "recipe", "name": "Permit stamp"}]))
    c2 = Conversation(REQUEST, files(folder), client=fake2, mode="compile")
    t = c2.start()
    assert "YOUR LIBRARY" not in first_message(fake2) and "YOUR LIBRARY" not in fake2.requests[0]["system"]
    assert [x["name"] for x in fake2.requests[0]["tools"]] == [x["name"] for x in compiler_module.TOOLS]
    assert t.choices == [] and any("kind 'recipe' is offered in a conversation only" in e for e in t.errors)
    one_shot = next(x for x in compiler_module.TOOLS if x["name"] == "ask_user")
    assert one_shot["input_schema"]["properties"]["choices"]["items"]["properties"]["kind"]["enum"] == \
        list(CHOICE_KINDS_FROM_CLAUDE)
    assert set(LIBRARY_CHOICE_KINDS) <= set(APP_CHOICE_KINDS) and not set(LIBRARY_CHOICE_KINDS) & set(CHOICE_KINDS_FROM_CLAUDE)


def test_the_tools_read_the_library_and_a_recipe_is_dry_planned_on_the_batch(folder, recipe, tmp_path):
    g = passing_op(folder, tmp_path)
    library.save_operation("Header distance", g)
    fake = FakeClient(resp(tool("read_library_item", {"name": "permit STAMP"}, "r1")),
                      resp(tool("read_library_item", {"name": "Header distance"}, "r2")),
                      resp(tool("read_library_item", {"name": "Nope"}, "r3")),
                      resp(tool("list_library", {}, "l1")),
                      submit(REMOVE, "s1"))
    c = Conversation(REQUEST, files(folder), client=fake)
    assert c.start().status == "ok"
    got = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
    assert got["kind"] == "recipe" and got["placed_words"] == [STAMP] and "ONLY once the user picks" in got["placed_words_rule"]
    assert got["spec"]["ops"] == RECIPE["ops"] and got["dry_plan"]["accepted"] and got["templates"] == []
    assert [p["result"] for p in got["dry_plan"]["patterns"]] == ["plans"]
    op = json.loads(fake.requests[2]["messages"][-1]["content"][0]["content"])
    assert op["kind"] == "operation" and op["op_name"] == "set_header_distance" and "source" not in op and "op.py" not in json.dumps(op)
    missing = fake.requests[3]["messages"][-1]["content"][0]
    assert missing["is_error"] and "there is no recipe, operation, chain or snippet named 'Nope'" in missing["content"]
    listing = json.loads(fake.requests[4]["messages"][-1]["content"][0]["content"])
    assert sorted(i["name"] for i in listing["items"]) == ["Header distance", "Permit stamp"] and listing["more"] == 0
    assert_well_formed(fake)


# ---- the words: shown on the choice, approved by the pick, never before -------------------------------------------

def test_a_recipes_words_are_refused_until_the_user_picks_the_choice_that_shows_them(folder, recipe, tmp_path):
    fake = FakeClient(resp(tool("read_library_item", {"name": "Permit stamp"}, "r1")),
                      submit(PLACES_STAMP, "s1"),                 # Claude copies the recipe's words: refused
                      offer("recipe", "Permit stamp", "a2", detail="It is the stamp you asked for."),
                      submit(PLACES_STAMP, "s3"))                 # after the pick: the same words are approved
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    assert "needs your answer" in s.compile(REQUEST, quiet)
    c = s.conversation
    rejected = fake.requests[2]["messages"][-1]["content"][0]
    assert rejected["is_error"] and "never gave them verbatim" in rejected["content"] and STAMP in rejected["content"]
    assert STAMP not in " ".join(c.sources)
    (choice,) = s.turn.choices
    assert choice.kind == "recipe" and choice.words == [STAMP] and choice.wording is None and choice.name == "Permit stamp"
    assert choice.label.startswith("Use your recipe “Permit stamp” — the cover stamp for permit sets (saved ")
    assert "from the request «stamp the cover for the permit set»" in choice.label
    assert choice.detail == "It is the stamp you asked for." and choice.item_hash and choice.snapshot["ops"] == RECIPE["ops"]
    assert choice.describe().endswith(f"— exact words: «{STAMP}»")
    msg = s.reply(choice_id=choice.id, log=quiet)
    assert "Using your recipe 'Permit stamp' as saved" in msg
    assert s.spec["ops"] == RECIPE["ops"] and s.spec["summary"] == RECIPE["summary"] and s.spec["request"] == REQUEST
    assert s.recipe_name == "Permit stamp" and s.spec_source == "compiled" and s.conversation is c
    assert c.sources[-1] == STAMP and c.entries[-1].role == "user" and c.entries[-1].text.startswith("Chose: Use your recipe")
    assert "2 plan cleanly" in s.make_preview(False, quiet)
    assert c.notes[-1].startswith("THE PREVIEW OF YOUR RECIPE «Permit stamp» (used as saved):")
    saved = json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))
    assert saved["approved_words"] == [REQUEST, STAMP]
    assert "PASS 2" in s.run(False, False, quiet)                  # the words reach the copies only through the pick
    assert "Compiled." == s.reply("keep it, same words", log=quiet)
    assert s.spec == dict(PLACES_STAMP, request=REQUEST) and s.recipe_name == ""
    sent = last_user_texts(fake, 3)                               # the ask's result carries the reply, then the notes
    assert sent[0] == "The user replied: keep it, same words"
    assert sent[1].startswith("[note from the app] The user chose: «Use your recipe “Permit stamp”") and \
        f"approving these exact words: «{STAMP}»" in sent[1] and "the outcome follows" in sent[1]
    assert sent[2].startswith("[note from the app] THE PREVIEW OF YOUR RECIPE") and len(sent) == 3
    assert_well_formed(fake)


def test_claude_s_wording_and_label_on_a_library_choice_are_ignored(folder, recipe, tmp_path):
    fake = FakeClient(offer("recipe", "Permit stamp", wording="EVIL WORDS", label="Claude's label"))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile(REQUEST, quiet)
    (choice,) = s.turn.choices
    assert choice.words == [STAMP] and choice.wording is None and choice.label.startswith("Use your recipe")
    assert choice.detail == "Claude's label" and any("was ignored" in e for e in s.turn.errors)
    s.reply(choice_id=choice.id, log=quiet)
    assert "EVIL WORDS" not in " ".join(s.conversation.sources) and STAMP in s.conversation.sources


def test_each_placed_word_is_its_own_source(folder):
    two = {"request": "two", "summary": "two words", "mode": "content", "ops": [
        {"op": "replace_text", "select": {"scope": ["document"]},
         "params": {"find": "REVISION HISTORY", "replace": "ALPHA ONE", "tracked": True, "author": "x"}},
        {"op": "replace_text", "select": {"scope": ["document"]},
         "params": {"find": "SECTION 21", "replace": "BETA TWO", "tracked": True, "author": "x"}}]}
    library.save_recipe("Two words", two)
    fake = FakeClient(offer("recipe", "Two words"),
                      submit(placing("ALPHA ONE BETA TWO"), "s1"),   # stitched across two sources: refused
                      submit(placing("ALPHA ONE", "BETA TWO"), "s2"))
    c = Conversation(REQUEST, files(folder), client=fake)
    (choice,) = c.start().choices
    assert choice.words == ["ALPHA ONE", "BETA TWO"]
    chosen, spec = c.take_library_choice(choice.id)
    assert c.sources == [REQUEST, "ALPHA ONE", "BETA TWO"] and spec["ops"] == two["ops"] and spec["request"] == REQUEST
    t = c.reply("now with both")
    rejected = fake.requests[2]["messages"][-1]["content"][0]
    assert rejected["is_error"] and "never gave them verbatim" in rejected["content"] and "ALPHA ONE BETA TWO" in rejected["content"]
    assert t.status == "ok" and t.spec["ops"] == placing("ALPHA ONE", "BETA TWO")["ops"]
    assert_well_formed(fake)


# ---- no choice, and said -------------------------------------------------------------------------------------------

def dropped(fake, folder, kind, name, **extra):
    c = Conversation(REQUEST, files(folder), client=fake)
    t = c.start()
    assert t.choices == []
    (reason,) = [e for e in t.errors if e.startswith(f"the {kind} choice «{name}» was dropped: ")]
    assert c.entries[-1].role == "app" and f"Claude offered your {kind} «{name}», but" in c.entries[-1].text \
        and "the choice was not shown" in c.entries[-1].text
    assert c.notes[-1].startswith(f"The {kind} choice «{name}» was not shown to the user: ")
    return reason, c


def test_an_invented_name_or_the_other_kind_is_no_choice(folder, recipe):
    fake = FakeClient(ask(choices=[{"kind": "recipe", "name": "Nope"}, {"kind": "operation", "name": "Permit stamp"},
                                   {"kind": "recipe"}]),
                      submit(REMOVE, "s2"))
    c = Conversation(REQUEST, files(folder), client=fake)
    t = c.start()
    assert t.choices == []
    assert any("«Nope» was dropped: there is no recipe named 'Nope'" in e for e in t.errors)
    assert any("«Permit stamp» was dropped: there is no operation named 'Permit stamp'" in e for e in t.errors)
    assert any("a recipe choice without a name was dropped" in e for e in t.errors)
    assert c.entries[-1].role == "app" and c.entries[-1].text.count("the choice was not shown") == 2
    assert c.reply("just do it").status == "ok"
    sent = last_user_texts(fake)
    assert sent[0] == "The user replied: just do it" and "Nope" not in " ".join(c.sources)
    assert sent[1].startswith("[note from the app] The recipe choice «Nope» was not shown to the user: there is no recipe")
    assert sent[2].startswith("[note from the app] The operation choice «Permit stamp» was not shown")
    assert_well_formed(fake)


def test_a_recipe_whose_template_is_gone_is_not_offered(folder, tmp_path):
    spec = json.loads((SPECS / "apply-header-footer-template.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tmp_path / "gone" / "TEMPLATE.docx")
    library.save_recipe("Architect header", spec)
    reason, _ = dropped(FakeClient(offer("recipe", "Architect header")), folder, "recipe", "Architect header")
    assert "its template file is no longer at" in reason and "TEMPLATE.docx" in reason


def test_a_recipe_that_plans_on_none_of_these_files_is_not_offered(folder):
    nowhere = dict(RECIPE, ops=[dict(STAMP_OP, params=dict(STAMP_OP["params"], find="ZZZ-NOWHERE"))])
    library.save_recipe("Nowhere", nowhere)
    reason, _ = dropped(FakeClient(offer("recipe", "Nowhere")), folder, "recipe", "Nowhere")
    assert "it plans on none of these files" in reason and "not-found" in reason


def test_a_recipe_placing_words_you_cannot_see_is_not_offered(folder):
    hidden = dict(RECIPE, ops=[dict(STAMP_OP, params=dict(STAMP_OP["params"], replace="ISSUED​FOR PERMIT"))])
    library.save_recipe("Hidden", hidden)
    reason, _ = dropped(FakeClient(offer("recipe", "Hidden")), folder, "recipe", "Hidden")
    assert "characters you cannot see" in reason and "0x200b" in reason


def test_a_tampered_operation_is_not_offered(folder, tmp_path):
    item = library.save_operation("Header distance", passing_op(folder, tmp_path))
    (item.path / "op.py").write_text(GOOD_OP + "\n# edited by hand\n", encoding="utf-8")
    reason, c = dropped(FakeClient(offer("operation", "Header distance")), folder, "operation", "Header distance")
    assert "changed since it was saved" in reason
    assert not list((tmp_path / "out").rglob("*")) if (tmp_path / "out").exists() else True


# ---- an operation: tried first, told either way --------------------------------------------------------------------

def test_a_saved_operation_is_tried_first_and_the_conversation_is_kept(folder, tmp_path):
    library.save_operation("Header distance", passing_op(folder, tmp_path), "0.625 in")
    fake = FakeClient(offer("operation", "Header distance"), submit(REMOVE, "s2"))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile(REQUEST, quiet)
    (choice,) = s.turn.choices
    assert choice.kind == "operation" and choice.words == [] and choice.name == "Header distance"
    assert choice.label.startswith("Try your operation “Header distance” — 0.625 in (saved ") and \
        "tried on this batch's sample files first — edited copies only" in choice.label
    msg = s.reply(choice_id=choice.id, log=quiet)
    assert "passed verification on 2 sample file(s)" in msg and s.generated.passed and s.spec is None
    c = s.conversation
    assert c is not None and c.notes[-1].startswith("Your operation 'Header distance' passed verification on the sample files")
    assert c.entries[-1].role == "app" and "passed verification" in c.entries[-1].text
    assert (s.out_dir / "library-trial").is_dir() and s.generated_token
    assert "AI-written transform previewed" in s.make_preview(False, quiet)
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)
    assert s.reply("no, remove the headers instead", log=quiet) == "Compiled." and s.generated is None
    sent = last_user_texts(fake, 1)
    assert sent[0] == "The user replied: no, remove the headers instead"
    assert sent[1].startswith("[note from the app] The user chose: «Try your operation “Header distance”") and \
        "the outcome follows" in sent[1] and sent[2].startswith("[note from the app] Your operation 'Header distance' passed")
    assert_well_formed(fake)


def test_a_saved_operation_that_fails_its_trials_is_told_to_claude_and_changes_nothing(folder, tmp_path, monkeypatch):
    library.save_operation("Header distance", passing_op(folder, tmp_path))
    import spec_surgeon.codegen as cg
    real = cg.trial

    def failing(*a, **k):
        t = real(*a, **k)
        t.passed, t.problems = False, ["V10 FAIL: not here"]
        return t
    monkeypatch.setattr(cg, "trial", failing)
    fake = FakeClient(offer("operation", "Header distance"), submit(REMOVE, "s2"))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile(REQUEST, quiet)
    (choice,) = s.turn.choices
    with pytest.raises(OperationFailed, match="does not pass on these files"):
        s.reply(choice_id=choice.id, log=quiet)
    c = s.conversation
    assert c is not None and s.generated is None and s.spec is None and s.generated_token == ""
    assert c.notes[-1].startswith("your operation 'Header distance' does not pass on these files — ") and "V10 FAIL" in c.notes[-1]
    assert not list((tmp_path / "out").rglob("outputs"))
    assert s.reply("remove the headers then", log=quiet) == "Compiled."
    sent = last_user_texts(fake, 1)
    assert sent[0] == "The user replied: remove the headers then" and "the outcome follows" in sent[1]
    assert sent[2].startswith("[note from the app] your operation 'Header distance' does not pass") and len(sent) == 3
    assert_well_formed(fake)


# ---- the item as offered is the item used --------------------------------------------------------------------------

def test_a_recipe_edited_between_the_offer_and_the_pick_is_refused(folder, recipe, tmp_path):
    fake = FakeClient(offer("recipe", "Permit stamp"), submit(REMOVE, "s2"))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile(REQUEST, quiet)
    (choice,) = s.turn.choices
    edited = copy.deepcopy(RECIPE)
    edited["ops"][0]["params"]["replace"] = "OTHER WORDS NOBODY SAW"
    recipe.path.write_text(json.dumps(edited), encoding="utf-8")
    with pytest.raises(LibraryChanged, match="changed since Claude offered it"):
        s.reply(choice_id=choice.id, log=quiet)
    c = s.conversation
    assert s.spec is None and c is not None and STAMP not in c.sources and "OTHER WORDS" not in " ".join(c.sources)
    assert c.entries[-1].role == "app" and c.entries[-1].text.startswith("Not used: your recipe «Permit stamp» changed")
    assert s.reply("fine, remove the headers", log=quiet) == "Compiled."
    sent = last_user_texts(fake, 1)
    assert sent[0] == "The user replied: fine, remove the headers"
    assert sent[1].startswith("[note from the app] The user chose «Use your recipe") and "changed since Claude offered it" in sent[1]
    assert_well_formed(fake)


def test_a_snapshot_that_does_not_place_the_words_shown_never_becomes_the_outcome(folder, recipe):
    c = Conversation(REQUEST, files(folder), client=FakeClient(offer("recipe", "Permit stamp")))
    (choice,) = c.start().choices
    choice.snapshot["ops"][0]["params"]["replace"] = "SWAPPED UNDER THE CHOICE"
    with pytest.raises(RuntimeError, match="does not place the words its choice showed"):
        c.take_library_choice(choice.id)
    assert c.sources == [REQUEST] and c.entries[-1].role == "claude"


def test_an_operation_edited_between_the_offer_and_the_pick_is_refused(folder, tmp_path):
    item = library.save_operation("Header distance", passing_op(folder, tmp_path))
    fake = FakeClient(offer("operation", "Header distance"))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    s.compile(REQUEST, quiet)
    (choice,) = s.turn.choices
    manifest = json.loads((item.path / "manifest.json").read_text(encoding="utf-8"))
    manifest["description"] = "edited"
    (item.path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(LibraryChanged, match="your operation «Header distance» changed since Claude offered it"):
        s.reply(choice_id=choice.id, log=quiet)
    assert s.generated is None and s.conversation is not None and not list((tmp_path / "out").rglob("library-trial"))


def test_your_library_outside_the_conversation_is_unchanged(folder, tmp_path):
    """Your library… still ends the conversation when an operation passes, and leaves everything when it fails."""
    library.save_operation("Header distance", passing_op(folder, tmp_path))
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(submit(REMOVE)))
    s.compile("remove all headers and footers", quiet)
    assert s.spec is not None
    assert "passed verification" in s.use_operation("header distance", quiet)
    assert s.conversation is None and s.generated.passed and s.spec is None


# ---- the window ----------------------------------------------------------------------------------------------------

def _window_with(qt, folder, tmp_path, fake):
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set(REQUEST)
    w.resize(1100, 700)
    w.show()
    ui["buttons"]["preview"].click()
    ui["wait"]()
    qt[0].processEvents()
    return s, w, ui


def test_the_window_shows_and_uses_a_recipe_choice(qt, folder, tmp_path):
    library.save_recipe("Permit stamp", RECIPE, "the cover stamp for permit sets")
    s, w, ui = _window_with(qt, folder, tmp_path, FakeClient(offer("recipe", "Permit stamp")))
    ((cid, button),) = ui["choices"]()
    assert button.text() == "Use it"
    labels = [lab.text() for lab in ui["chat"].findChildren(type(w.request_page.info.text))]
    assert any(t.startswith("Use your recipe “Permit stamp” — the cover stamp for permit sets") and
               f"Exact words: «{STAMP}» — choosing approves these exact words for your documents." in t for t in labels)
    button.click()
    ui["wait"]()
    assert s.recipe_name == "Permit stamp" and s.preview is not None
    assert w.stack.currentWidget() is w.preview_page and ui["buttons"]["run"].isEnabled()
    assert w.preview_page.request_lbl.text().startswith("Your recipe “Permit stamp” (as saved)")
    assert ui["choices"]() == [] and not ui["preview_chat"].isHidden()
    assert w.request_page.spec_bar.isHidden()


def test_the_window_tries_an_operation_choice_first(qt, folder, tmp_path):
    library.save_operation("Header distance", passing_op(folder, tmp_path))
    s, w, ui = _window_with(qt, folder, tmp_path, FakeClient(offer("operation", "Header distance")))
    ((cid, button),) = ui["choices"]()
    assert button.text() == "Try it"
    button.click()
    ui["wait"]()
    assert s.generated is not None and s.generated.passed and s.conversation is not None
    assert w.stack.currentWidget() is w.preview_page and ui["buttons"]["run"].isEnabled()
    assert "passed verification" in w.log.toPlainText()


# ---- the terminal --------------------------------------------------------------------------------------------------

def test_the_terminal_recipe_choice_asks_for_the_words(folder, recipe, tmp_path, monkeypatch, capsys):
    from spec_surgeon import cli
    fake = FakeClient(offer("recipe", "Permit stamp"))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    prompts = scripted_input(monkeypatch, ["#1", "n", "#1", "y", "y"])
    out_dir = tmp_path / "o"
    assert cli_main(["do", REQUEST, str(folder), "--out", str(out_dir), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "[#1] Use your recipe “Permit stamp” — the cover stamp for permit sets" in printed
    assert f"        exact words: «{STAMP}»" in printed
    assert printed.count("[#1] Use your recipe") == 2                      # a typed n: the choices come back
    assert prompts[1] == prompts[3] == f"Approve these exact words for your documents: «{STAMP}»? [y/N] "
    assert prompts[4].startswith("\nRun this on 2 file(s)? [y/N] ") and len(prompts) == 5
    assert "Using your recipe 'Permit stamp' as saved." in printed
    assert len(list((out_dir / "run" / "outputs").glob("*.docx"))) == 2
    saved = json.loads((out_dir / "conversation.json").read_text(encoding="utf-8"))
    assert saved["approved_words"] == [REQUEST, STAMP]
    assert [e["role"] for e in saved["entries"]] == ["user", "claude", "user"] and saved["entries"][-1]["text"].startswith("Chose: Use your recipe")
    assert json.loads((out_dir / "preview" / "spec.json").read_text(encoding="utf-8"))["request"] == REQUEST


def test_the_terminal_declining_the_words_keeps_the_conversation(folder, recipe, tmp_path, monkeypatch, capsys):
    from spec_surgeon import cli
    headers_only = copy.deepcopy(REMOVE)
    headers_only["ops"][0]["select"]["kinds"] = ["header"]
    fake = FakeClient(offer("recipe", "Permit stamp"), submit(headers_only, "s2"),
                      remove_outcome_proposal(kinds=["header"]))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    prompts = scripted_input(monkeypatch, ["#1", "n", "remove the headers instead", "y", "n"])
    assert cli_main(["do", REQUEST, str(folder), "--out", str(tmp_path / "o"), "--no-render"]) == 0
    assert "Not run." in capsys.readouterr().out and prompts[1].startswith("Approve these exact words")
    assert prompts[3] == "Approve these outcomes and every document's targets? [y/N] "
    assert last_user_texts(fake, 1) == ["The user replied: remove the headers instead"] and STAMP not in " ".join(
        json.loads((tmp_path / "o" / "conversation.json").read_text(encoding="utf-8"))["approved_words"])


def test_the_terminal_operation_choice_tries_first_and_a_failure_returns_to_the_conversation(folder, tmp_path, monkeypatch,
                                                                                            capsys):
    from spec_surgeon import cli
    import spec_surgeon.codegen as cg
    library.save_operation("Header distance", passing_op(folder, tmp_path))
    real = cg.trial

    def failing(*a, **k):
        t = real(*a, **k)
        t.passed, t.problems = False, ["V10 FAIL: not here"]
        return t
    monkeypatch.setattr(cg, "trial", failing)
    fake = FakeClient(offer("operation", "Header distance"), submit(REMOVE, "s2"), remove_outcome_proposal())
    monkeypatch.setattr(compiler_module, "_client", lambda: fake)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    prompts = scripted_input(monkeypatch, ["#1", "strip them instead", "y", "n"])
    out_dir = tmp_path / "o"
    assert cli_main(["do", REQUEST, str(folder), "--out", str(out_dir), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "Not used: your operation 'Header distance' does not pass on these files — " in printed and "Not run." in printed
    assert prompts[0] == prompts[1] == "Your reply ('#n' picks a choice; an empty line stops): "   # back to the loop
    assert prompts[2] == "Approve these outcomes and every document's targets? [y/N] "
    sent = last_user_texts(fake, 1)
    assert sent[0] == "The user replied: strip them instead" and \
        sent[1].startswith("[note from the app] The user chose: «Try your operation") and \
        sent[2].startswith("[note from the app] your operation 'Header distance' does not pass")
    assert (out_dir / "library-trial" / "try-1").is_dir() and not (out_dir / "run").exists()
    # the same operation, passing: tried, previewed, run after a yes — edited copies only
    monkeypatch.setattr(cg, "trial", real)
    fake2 = FakeClient(offer("operation", "Header distance"))
    monkeypatch.setattr(compiler_module, "_client", lambda: fake2)
    prompts2 = scripted_input(monkeypatch, ["#1", "y"])
    out2 = tmp_path / "o2"
    assert cli_main(["do", REQUEST, str(folder), "--out", str(out2), "--no-render"]) == 0
    printed = capsys.readouterr().out
    assert "your operation 'Header distance' passed on the samples" in printed
    assert prompts2[1].startswith("\nRun it on 2 file(s) (edited copies only)? [y/N] ")
    assert "2 of 2 file(s) PASS" in printed and len(list((out2 / "run" / "outputs").glob("*.docx"))) == 2
    assert (out2 / "library-trial" / "preview").is_dir()


# ---- Codex's review of the implementation (PR #17): containment, and results that stay valid JSON ------------------

def _symlink_or_skip(target, link):
    import os
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks are not available here: {e}")


def test_a_library_item_that_leads_outside_the_library_is_never_read(folder, tmp_path):
    """A recipe file or an operation folder that is a symlink to somewhere outside the library lists (its target
    exists) but every read — the index, read_library_item, the choice — goes through the containment check and refuses."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.json").write_text(json.dumps(RECIPE), encoding="utf-8")
    recipes = library._kind_dir("recipe")
    recipes.mkdir(parents=True, exist_ok=True)
    _symlink_or_skip(outside / "evil.json", recipes / "evil.json")
    (recipes / "evil.meta.json").write_text(json.dumps({"name": "evil", "description": "", "request": "x",
                                                        "saved_at": "2026-10-16T00:00:00"}), encoding="utf-8")
    op_src = library.save_operation("Header distance", passing_op(folder, tmp_path))
    moved = outside / "moved-op"
    import shutil
    shutil.move(str(op_src.path), str(moved))
    _symlink_or_skip(moved, library._kind_dir("operation") / "header-distance")
    assert sorted(i.name for i in library.list_items()) == ["Header distance", "evil"]
    index = compiler_module.library_index()
    by_name = {e["name"]: e for e in index["items"]}
    assert "is not in your library" in by_name["evil"]["error"] and "spec" not in by_name["evil"]
    assert "is not in your library" in by_name["Header distance"]["error"] and "op" not in by_name["Header distance"]
    fake = FakeClient(resp(tool("read_library_item", {"name": "evil"}, "r1")),
                      resp(tool("read_library_item", {"name": "Header distance"}, "r2")),
                      ask("a1", choices=[{"kind": "recipe", "name": "evil"}, {"kind": "operation", "name": "Header distance"}]))
    c = Conversation(REQUEST, files(folder), client=fake)
    t = c.start()
    for i in (1, 2):
        result = fake.requests[i]["messages"][-1]["content"][0]
        assert result["is_error"] and "is not in your library" in result["content"] and STAMP not in result["content"]
    assert t.choices == [] and sum("is not in your library" in e for e in t.errors) == 2


def test_an_oversized_recipe_detail_stays_valid_json_with_the_words_and_the_dry_plan(folder):
    big = dict(RECIPE, assumptions=[f"assumption {i}: " + "x" * 400 for i in range(60)])    # ~25,000 characters
    library.save_recipe("Big recipe", big)
    fake = FakeClient(resp(tool("read_library_item", {"name": "Big recipe"}, "r1")), submit(REMOVE, "s1"))
    c = Conversation(REQUEST, files(folder), client=fake)
    assert c.start().status == "ok"
    raw = fake.requests[1]["messages"][-1]["content"][0]["content"]
    got = json.loads(raw)                                                     # not cut mid-JSON by the 20,000 cap
    assert len(raw) <= compiler_module.DETAIL_BUDGET
    assert got["placed_words"] == [STAMP] and got["dry_plan"]["accepted"] and got["templates"] == []
    assert "too long for this tool" in got["spec"]["left_out"] and got["spec"]["ops"] == [
        {"op": "replace_text", "select": {"scope": ["document"]}, "params": ["author", "find", "replace", "tracked"]}]
    library.save_recipe("Small recipe", RECIPE)                             # a small recipe is returned whole
    assert compiler_module.library_item_detail("Small recipe")["spec"]["ops"] == RECIPE["ops"]


def test_an_oversized_index_entry_is_skipped_and_later_items_still_show():
    library.save_recipe("A big one", dict(RECIPE, ops=[STAMP_OP] + [
        dict(STAMP_OP, params=dict(STAMP_OP["params"], find=f"SECTION {i}")) for i in range(60)]),
        "a recipe with more ops than an index entry lists")
    library.save_recipe("Z small", RECIPE)
    entries = [compiler_module.library_entry(i) for i in library.list_items()]
    big = next(e for e in entries if e["name"] == "A big one")
    assert len(big["ops"]) == compiler_module.ENTRY_OPS + 1 and big["ops"][-1] == "… and 31 more"
    budget = len(json.dumps(next(e for e in entries if e["name"] == "Z small"), ensure_ascii=False)) + 2 + 2 + 10
    index = compiler_module.library_index(budget=budget)
    assert [e["name"] for e in index["items"]] == ["Z small"] and index["more"] == 1
    assert compiler_module.library_index()["more"] == 0 and len(compiler_module.library_index()["items"]) == 2
