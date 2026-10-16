"""The window's logic and the window after the second fix pass of Phase 8 (review-2, -3, -8, -12, -16, -21 residuals).

* review-2: a new request compiled while the previous accepted spec names a template file of the batch folder starts a
  conversation over the batch as a new conversation sees it (the template included), so the next reply is not refused.
* review-3: a custom transform that stops with an internal error tells Claude so (never as a verification result) —
  never the dangling "the outcome follows" promise.
* the files a custom transform is approved for are the files as they were BEFORE it was written: a sample edited while
  it is written leaves it unrunnable until Preview tries it again on the edited file.
* review-8: one of your operations picked while a request is typed clears the request box, so Preview tries the
  operation again instead of compiling the old words in a new conversation.
* review-16 / review-12: Save to library warns on a folder or a library that cannot be read or written, and on a name
  the other kind holds (no "Replace it?" — replacing cannot help); a same-kind name still asks.
* review-21: a custom transform's blocking visual-check finding, acknowledged for its own preview, then runs to the end —
  edited copies only.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import library, vision
from spec_surgeon.codegen import generate_op
from spec_surgeon.gui import Session, TransformFailed
from test_codegen import GOOD_OP

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "examples" / "specs"
REFERENCE = ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))

# Claude's answer when no op does it (with its offer of a one-off custom transform), and the writer's submission.
UNSUPPORTED = {"request": "r", "summary": "s", "unsupported": "no op sets the header distance alone",
               "proposal": "set_header_distance"}
OFFER_TURN = resp(tool("submit_editspec", {"spec": UNSUPPORTED, "custom_transform": "possible"}))
WRITER = resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}, "w1"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


@pytest.fixture
def qt(monkeypatch):
    """A Qt application (skipped when Qt cannot load, unless SPEC_SURGEON_REQUIRE_QT=1); every window a test appends
    to the list is deleted after it, on the main thread (``qt_support.destroy_windows``)."""
    from qt_support import destroy_windows, qt_app_or_skip
    app = qt_app_or_skip()
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    windows = []
    yield app, windows
    destroy_windows(app, windows)      # deleted now, on the main thread — never left to the garbage collector


def window(qt, session=None, **kw):
    from spec_surgeon.gui import build_window
    from spec_surgeon.ui.app import RecordingPrompts
    kw.setdefault("prompts", RecordingPrompts())
    w = build_window(session, **kw)
    qt[1].append(w)
    return w, w._spec_surgeon


def quiet(m):
    return None


def docx(d: Path):
    return sorted(d.glob("*.docx"))


def touch(f: Path) -> None:
    """The user opens a spec in Word and saves it: the file's mtime moves."""
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


def revise(f: Path) -> None:
    """The user edits a spec and saves it: new content (still a good .docx), new modification time."""
    st = f.stat()
    f.write_bytes(build("21 00 00", "SPEC 0 REVISED"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def offered(folder, tmp_path, *more) -> Session:
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(OFFER_TURN, *more))
    s.compile("put the header 0.625 in from the top", quiet)
    assert [c.kind for c in s.turn.choices] == ["custom_transform"]
    return s


def saved_entries(s) -> list:
    return json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))["entries"]


def sent_texts(fake) -> list:
    return [b.get("text") for b in fake.requests[-1]["messages"][-1]["content"] if b.get("type") == "text"]


def saved_operation(folder: Path, tmp_path: Path, name: str = "Header distance") -> None:
    g = generate_op("put the header 0.625 in from the top", "set_header_distance", docx(folder)[:1], tmp_path / "cg",
                    client=FakeClient(WRITER), brief="REQUEST: header")
    assert g.passed
    library.save_operation(name, g)


def label_of(kind: str) -> str:
    i = next(i for i in library.list_items() if i.kind == kind)
    return i.label() + (f" — {i.description}" if i.description else "")


# ---- review-2: a new request while the outgoing spec names a template of the batch folder ---------------------------

def _template_spec(folder: Path):
    tpl = folder / REFERENCE.name
    shutil.copy(REFERENCE, tpl)
    spec = json.loads((SPECS / "match-template-spec-header-footer.json").read_text(encoding="utf-8"))
    spec["ops"][0]["params"]["template"] = str(tpl)
    return tpl, spec


def test_a_new_request_after_a_spec_that_names_a_folder_template_keeps_its_conversation(folder, tmp_path):
    tpl, spec = _template_spec(folder)
    fake = FakeClient(resp(tool("submit_editspec", {"spec": spec}, "s1")),
                      resp(tool("ask_user", {"questions": ["Which sections?"]}, "a2")),
                      resp(tool("ask_user", {"questions": ["And the cover?"]}, "a3")))
    s = Session(folder=folder, out_root=tmp_path / "out", client=fake)
    assert s.compile("match the template spec", quiet) == "Compiled."
    assert tpl not in s.files()                                     # the accepted spec reads it: not a batch file
    assert "needs your answer" in s.compile("now remove the footers", quiet)   # a new request, while that spec is out
    assert s.spec is None
    assert s.conversation.files == s.files() and tpl in s.conversation.files  # the batch a new conversation sees
    assert s.conversation_matches("now remove the footers")
    assert "needs your answer" in s.reply("every section", log=quiet)          # never "press Start over"
    assert len(fake.requests) == 3


def test_a_new_request_over_a_folder_that_cannot_be_read_changes_nothing(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    assert s.compile("remove all headers and footers", quiet) == "Compiled."
    s.make_preview(render=False, log=quiet)
    conversation, preview = s.conversation, s.preview
    shutil.move(str(folder), str(tmp_path / "moved"))
    with pytest.raises((ValueError, OSError)):
        s.compile("now remove the footers", quiet)
    assert s.conversation is conversation and s.spec == REMOVE and s.preview is preview   # refused before anything moved


# ---- review-3: an internal error in the custom-transform branch ------------------------------------------------------

def test_an_internal_error_while_making_the_transform_tells_claude_so_never_the_outcome_follows(folder, tmp_path,
                                                                                               monkeypatch):
    import spec_surgeon.preview as pv
    s = offered(folder, tmp_path, WRITER, resp(tool("ask_user", {"questions": ["Shall I try another way?"]}, "a9")))
    chosen = s.turn.choices[0]
    real = pv.preview_generated

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pv, "preview_generated", broken)
    with pytest.raises(OSError, match="disk full") as e:
        s.reply(choice_id=chosen.id, log=quiet)
    assert not isinstance(e.value, TransformFailed)                 # a defect propagates as itself
    notes = s.conversation.notes
    assert notes == [f"The user chose: «{chosen.label}». The app could not finish the custom transform: it stopped "
                     f"with an internal error (OSError) before any result — nothing was applied."]
    assert not any("outcome follows" in n or "passed verification" in n or "disk full" in n for n in notes)
    entries = saved_entries(s)                                      # the transcript is what the user saw: the choice
    assert entries[-1]["role"] == "user" and entries[-1]["text"].startswith("Chose: ")
    monkeypatch.setattr(pv, "preview_generated", real)
    s.reply("so did it work?")
    sent = sent_texts(s.client)                                     # the next message Claude gets
    assert "[note from the app] " + notes[0] in sent
    assert not any("outcome follows" in (t or "") for t in sent)


def test_a_transform_that_comes_out_still_says_the_outcome_follows_and_what_it_was(folder, tmp_path):
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in s.reply(choice_id=s.turn.choices[0].id, log=quiet)
    assert "outcome follows" in s.conversation.notes[0] and "passed verification" in s.conversation.notes[1]


# ---- a sample edited while the transform is being written -----------------------------------------------------------

def test_a_sample_edited_while_the_transform_is_written_is_never_approved_with_its_new_stats(folder, tmp_path,
                                                                                              monkeypatch):
    import spec_surgeon.codegen as cg
    real = cg.generate_op
    edited = []

    def written_while_edited(request, proposal, samples, *a, **k):
        g = real(request, proposal, samples, *a, **k)               # its trials read the samples as they were
        revise(samples[0])                                          # saved in Word before the writer was done
        edited.append(samples[0])
        return g
    monkeypatch.setattr(cg, "generate_op", written_while_edited)
    s = offered(folder, tmp_path, WRITER)
    said = s.reply(choice_id=s.turn.choices[0].id, log=quiet)
    assert "passed verification" in said and "press Preview to try it again" in said
    assert edited and s.generated is not None
    assert s.generated_token == ""                                  # its token: none until it is tried again
    with pytest.raises(ValueError, match="press Preview again") as e:
        s.run(False, False, quiet)                                  # never approved with the edited file's stats
    assert f"{edited[0].name}: modified since the preview" in str(e.value)    # it names the file
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review(s.generated_token)                     # nothing of it can be approved
    with pytest.raises(ValueError, match="preview again"):
        s.save_operation("Header distance")                         # nor saved
    lines = []
    assert "AI-written transform ready" in s.make_preview(render=False, log=lines.append)
    assert any("trying it again" in m for m in lines)              # Preview tries it on the file as it is now
    assert (s.out_dir / "retrial").is_dir()
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


def test_a_sample_edited_while_your_operation_is_tried_is_never_approved_with_its_new_stats(folder, tmp_path,
                                                                                             monkeypatch):
    saved_operation(folder, tmp_path)
    import spec_surgeon.codegen as cg
    real = cg.trial

    def tried_while_edited(source, f, *a, **k):
        t = real(source, f, *a, **k)
        revise(Path(f))
        return t
    monkeypatch.setattr(cg, "trial", tried_while_edited)
    s = Session(folder=folder, out_root=tmp_path / "out")
    assert "passed verification" in s.use_operation("header distance", quiet)
    monkeypatch.setattr(cg, "trial", real)
    with pytest.raises(ValueError, match="press Preview again"):
        s.run(False, False, quiet)
    assert "ready" in s.make_preview(render=False, log=quiet)
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


# ---- review-8: an operation picked while a request is typed ---------------------------------------------------------

def test_an_operation_picked_while_a_request_is_typed_is_tried_again_by_preview_never_compiled(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    saved_operation(folder, tmp_path)
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}, "a1")))   # one turn: a 2nd call fails
    prompts = RecordingPrompts()
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert s.conversation is not None and len(fake.requests) == 1
    prompts.items = [label_of("operation")]
    assert w.do_library()
    ui["wait"]()
    assert s.generated is not None and s.conversation is None
    assert ui["request"].get() == ""                                # the old words are not the operation's request
    assert ui["buttons"]["run"].isEnabled(), w.run_page.run_hint.text()
    assert w.stack.currentWidget() is w.preview_page
    first = s.out_dir
    touch(docx(folder)[0])
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert "That didn't work" not in w.request_page.info.title.text(), w.request_page.info.text.text()
    assert s.generated is not None and s.conversation is None and len(fake.requests) == 1   # no new conversation
    assert s.out_dir != first and (s.out_dir / "retrial").is_dir()  # tried again on the files as they are now
    assert ui["buttons"]["run"].isEnabled()
    before = {f: f.read_bytes() for f in docx(folder)}
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert ui["results_model"].badges() == ["pass", "pass"]
    assert {f: f.read_bytes() for f in docx(folder)} == before


def test_an_operation_that_does_not_pass_keeps_the_typed_request_and_its_conversation(qt, folder, tmp_path,
                                                                                       monkeypatch):
    from spec_surgeon.ui.app import RecordingPrompts
    saved_operation(folder, tmp_path)
    import spec_surgeon.codegen as cg
    real = cg.trial

    def failing(*a, **k):
        t = real(*a, **k)
        t.passed, t.problems = False, ["V10 FAIL: not on these files"]
        return t
    fake = FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}, "a1")))
    prompts = RecordingPrompts()
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove the headers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    conversation = s.conversation
    monkeypatch.setattr(cg, "trial", failing)
    prompts.items = [label_of("operation")]
    assert w.do_library()
    ui["wait"]()
    assert "does not pass on these files" in w.request_page.info.text.text()
    assert s.conversation is conversation and s.generated is None
    assert ui["request"].get() == "remove the headers"              # nothing the user typed was thrown away


# ---- review-16 / review-12: Save to library ---------------------------------------------------------------------------

def _previewed_spec(qt, folder, tmp_path, prompts):
    s = Session(out_root=tmp_path / "out", client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    w, ui = window(qt, s, prompts=prompts)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("remove all headers and footers")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    assert w._preview_current()
    return s, w, ui


def test_save_to_library_warns_when_the_folder_cannot_be_read(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    prompts = RecordingPrompts(texts=["My recipe"])
    s, w, ui = _previewed_spec(qt, folder, tmp_path, prompts)
    shutil.move(str(folder), str(tmp_path / "moved"))               # renamed, or the network share dropped
    assert w.do_save_to_library() is False                          # on the GUI thread: never raises
    warns = [x for x in prompts.shown if x[0] == "warn"]
    assert len(warns) == 1 and "Nothing was saved" in warns[0][2]
    assert library.list_items() == []


def test_save_to_library_warns_when_the_library_cannot_be_written(qt, folder, tmp_path, monkeypatch):
    from spec_surgeon.ui.app import RecordingPrompts
    prompts = RecordingPrompts(texts=["My recipe"])
    s, w, ui = _previewed_spec(qt, folder, tmp_path, prompts)

    def denied(*a, **k):
        raise PermissionError(13, "Access is denied", str(library.library_dir()))
    monkeypatch.setattr(library, "save_recipe", denied)
    assert w.do_save_to_library() is False
    warns = [x for x in prompts.shown if x[0] == "warn"]
    assert len(warns) == 1 and "Access is denied" in warns[0][2] and "Nothing was saved" in warns[0][2]
    assert "Saved to your library" not in w.preview_page.info.title.text()


def test_save_to_library_never_offers_replace_for_a_name_the_other_kind_holds(qt, folder, tmp_path):
    from spec_surgeon.ui.app import RecordingPrompts
    saved_operation(folder, tmp_path, "Strip headers")
    prompts = RecordingPrompts(texts=["strip headers"], answers=[True])
    s, w, ui = _previewed_spec(qt, folder, tmp_path, prompts)
    assert w.do_save_to_library() is False
    kinds = [x[0] for x in prompts.shown]
    assert kinds == ["ask_text", "warn"]                            # no "Replace it?" — replacing cannot help
    assert "My operation: Strip headers" in prompts.shown[-1][2] and "Replace it?" not in prompts.shown[-1][2]
    assert [i.label() for i in library.list_items()] == ["My operation: Strip headers"]


@pytest.mark.parametrize("replace", [True, False])
def test_save_to_library_still_asks_before_replacing_a_recipe_of_the_same_name(qt, folder, tmp_path, replace):
    from spec_surgeon.ui.app import RecordingPrompts
    library.save_recipe("Strip headers", {**REMOVE, "summary": "the old one"})
    prompts = RecordingPrompts(texts=["strip headers"], answers=[replace])
    s, w, ui = _previewed_spec(qt, folder, tmp_path, prompts)
    assert w.do_save_to_library() is replace
    assert [x[0] for x in prompts.shown] == ["ask_text", "ask"] and prompts.shown[-1][2].endswith("Replace it?")
    item = library.find("strip headers", "recipe")
    assert (json.loads(item.path.read_text(encoding="utf-8"))["summary"] == REMOVE["summary"]) is replace
    assert ("Saved to your library" in w.preview_page.info.title.text()) is replace


# ---- review-21: a custom transform's blocking finding, acknowledged, then Run ------------------------------------------

def test_a_custom_transform_s_blocking_finding_acknowledged_then_runs_edited_copies_only(folder, tmp_path, monkeypatch):
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)  # no renderer in this test
    intents = []

    def review(intent, samples, work_dir, client_factory, *a, **k):
        intents.append(intent)
        return vision.VisualReview(summary="looked at the pages", findings=[
            {"severity": "block", "file": "21 00 00 – SPEC 0.docx", "page": 1, "text": "the header moved off the page"}])
    monkeypatch.setattr(vision, "review_samples", review)
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in s.reply(choice_id=s.turn.choices[0].id, log=quiet, render=True)
    assert len(intents) == 1 and intents[0].startswith("A ONE-OFF CUSTOM TRANSFORM (set_header_distance)")
    assert s.generated_preview.review.blocking() and s.generated_token
    assert "visual check flagged 1 problem" in s.run_blocked_reason()
    with pytest.raises(ValueError, match="visual check flagged"):
        s.run(False, False, quiet)
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review("an old preview's token")
    assert s.run_blocked_reason()                                   # a wrong token acknowledges nothing
    s.acknowledge_review(s.generated_token)
    assert s.run_blocked_reason() == ""
    before = {f: f.read_bytes() for f in docx(folder)}
    out = s.run(in_place=True, render=False, log=quiet)             # in place asked for: copies all the same
    assert "2 of 2 file(s) PASS" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in docx(folder)} == before
    outputs = sorted((s.last_run_dir / "outputs").glob("*.docx"))
    assert len(outputs) == 2 and all(o.read_bytes() not in before.values() for o in outputs)
    assert [t.passed for t in s.last_trials] == [True, True]


def test_your_operation_after_a_spec_that_names_a_folder_template_is_approved_for_the_batch_it_runs(folder, tmp_path):
    from spec_surgeon.preview import approval_token
    saved_operation(folder, tmp_path)
    tpl, spec = _template_spec(folder)
    spec_file = tmp_path / "match.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(spec_file)
    assert tpl not in s.files()                                     # the loaded spec reads it as its template
    assert "passed verification" in s.use_operation("header distance", quiet)
    assert s.spec is None and tpl in s.files()                      # the operation replaced it: a file of the batch
    assert approval_token(Session._generated_key(s.generated), s.files()) == s.generated_token
    assert "3 of 3 file(s) PASS" in s.run(False, False, quiet)      # approved for the batch it runs


def test_the_window_tries_again_at_once_a_transform_whose_sample_was_edited_while_it_was_written(qt, folder, tmp_path,
                                                                                                  monkeypatch):
    import spec_surgeon.codegen as cg
    real = cg.generate_op

    def written_while_edited(request, proposal, samples, *a, **k):
        g = real(request, proposal, samples, *a, **k)
        revise(samples[0])
        return g
    monkeypatch.setattr(cg, "generate_op", written_while_edited)
    fake = FakeClient(OFFER_TURN, WRITER)                            # no third call: trying again needs no Claude
    s = Session(out_root=tmp_path / "out", client=fake)
    w, ui = window(qt, s)
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("put the header 0.625 in from the top")
    ui["buttons"]["preview"].click()
    ui["wait"]()
    (cid, button), = ui["choices"]()
    button.click()
    log = ui["wait"]()
    assert "changed while the custom transform was being written" in log and "trying it again" in log
    assert (s.out_dir / "retrial").is_dir() and len(fake.requests) == 2
    assert ui["buttons"]["run"].isEnabled(), w.run_page.run_hint.text()
    ui["buttons"]["run"].click()
    ui["wait"]()
    assert ui["results_model"].badges() == ["pass", "pass"]


def test_turning_render_pages_on_makes_the_custom_transforms_preview_again_with_the_visual_check(folder, tmp_path,
                                                                                                 monkeypatch):
    """Codex on #12: a custom transform previewed with Render pages off, then previewed with it on, reused the old
    preview — no page images, no visual check — while the window took the render-on key as current."""
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)   # no renderer in this test
    reviews = []
    monkeypatch.setattr(vision, "review_samples",
                        lambda intent, samples, *a, **k: reviews.append(intent) or vision.VisualReview(summary="ok"))
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in s.reply(choice_id=s.turn.choices[0].id, log=quiet, render=False)
    first, token = s.generated_preview, s.generated_token
    assert first.rendered is False and first.review is None and reviews == [] and token
    s.make_preview(render=True, log=quiet)
    again = s.generated_preview
    assert again is not first and again.rendered is True and again.out_dir != first.out_dir
    assert len(reviews) == 1 and again.review is not None            # the visual check ran this time
    assert s.generated_token == token                               # same files, same transform: same approval
    s.make_preview(render=True, log=quiet)                          # nothing changed: the preview is reused
    assert s.generated_preview is again and len(reviews) == 1
    s.make_preview(render=False, log=quiet)                         # and back: made again without pages
    assert s.generated_preview.rendered is False and len(reviews) == 1
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)
