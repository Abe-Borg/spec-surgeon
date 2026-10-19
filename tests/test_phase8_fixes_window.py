"""The window's session logic after the second fix pass of Phase 8 (review-2, -3, -21 residuals and the render-pages
fix) — every test drives ``spec_surgeon.gui.Session``, the engine the conversation window is built over.

* review-2: a new request compiled while the previous accepted spec names one of the attached documents as its
  template starts a conversation over the batch as a new conversation sees it (that document included), so the next
  reply is not refused.
* review-3: a custom transform that stops with an internal error tells Claude so (never as a verification result) —
  never the dangling "the outcome follows" promise.
* the files a custom transform is approved for are the files as they were BEFORE it was written: a sample edited while
  it is written leaves it unrunnable until Preview tries it again on the edited file.
* review-21: a custom transform's blocking visual-check finding is part of its preview consent; the run makes edited
  copies only.
* a custom transform previewed with Render pages off, then on, is previewed again with its pages and visual check.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon import vision
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


def quiet(m):
    return None


def docx(d: Path):
    return sorted(d.glob("*.docx"))


def revise(f: Path) -> None:
    """The user edits a spec and saves it: new content (still a good .docx), new modification time."""
    st = f.stat()
    f.write_bytes(build("21 00 00", "SPEC 0 REVISED"))
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def offered(folder, tmp_path, *more) -> Session:
    s = Session(documents=docx(folder), out_root=tmp_path / "out", client=FakeClient(OFFER_TURN, *more))
    s.compile("put the header 0.625 in from the top", quiet)
    assert [c.kind for c in s.turn.choices] == ["custom_transform"]
    return s


def create_approved_custom(s, *, choice_id=None, render=False):
    message = s.reply(choice_id=choice_id or s.turn.choices[0].id, log=quiet, render=render)
    assert s.pending_custom_transform is None and s.generated is not None
    return message


def saved_entries(s) -> list:
    return json.loads((s.chat_dir / "conversation.json").read_text(encoding="utf-8"))["entries"]


def sent_texts(fake) -> list:
    return [b.get("text") for b in fake.requests[-1]["messages"][-1]["content"] if b.get("type") == "text"]


# ---- review-2: a new request while the outgoing spec names an attached document as its template ---------------------

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
    s = Session(documents=docx(folder), out_root=tmp_path / "out", client=fake)   # the template is attached too
    assert s.compile("match the template spec", quiet) == "Compiled."
    assert tpl not in s.files() and tpl in s.conversation.files     # the accepted spec reads it: filtered, not refused
    assert "needs your answer" in s.compile("now remove the footers", quiet)   # a new request, while that spec is out
    assert s.spec is None
    assert s.conversation.files == s.files() and tpl in s.conversation.files  # the batch a new conversation sees
    assert s.conversation_matches("now remove the footers")
    assert "needs your answer" in s.reply("every section", log=quiet)          # never "describe the change again"
    assert len(fake.requests) == 3


def test_a_new_request_over_a_folder_that_cannot_be_read_changes_nothing(folder, tmp_path):
    s = Session(documents=docx(folder), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
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
        create_approved_custom(s, choice_id=chosen.id)
    assert not isinstance(e.value, TransformFailed)                 # a defect propagates as itself
    notes = s.conversation.notes
    assert any("internal error (OSError) before any result — nothing was applied" in note for note in notes)
    assert not any("outcome follows" in n or "passed verification" in n or "disk full" in n for n in notes)
    entries = saved_entries(s)                                      # the transcript is what the user saw: the choice
    assert [entry for entry in entries if entry["role"] == "user"][-1]["text"].startswith("Chose: ")
    monkeypatch.setattr(pv, "preview_generated", real)
    s.reply("so did it work?")
    sent = sent_texts(s.client)                                     # the next message Claude gets
    assert all("[note from the app] " + note in sent for note in notes)
    assert not any("outcome follows" in (t or "") for t in sent)


def test_a_transform_that_comes_out_records_its_independently_verified_result(folder, tmp_path):
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in create_approved_custom(s)
    assert any("passed its independent sample verification" in n for n in s.conversation.notes)


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
    said = create_approved_custom(s)
    assert "passed verification" in said and "press Preview to try it again" in said
    assert edited and s.generated is not None
    assert s.generated_token == ""                                  # its token: none until it is tried again
    with pytest.raises(ValueError, match="press Preview again") as e:
        s.run(False, False, quiet)                                  # never approved with the edited file's stats
    assert f"{edited[0].name}: modified since the preview" in str(e.value)    # it names the file
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review(s.generated_token)                     # nothing of it can be approved
    lines = []
    assert "transform verified" in s.make_preview(render=False, log=lines.append)
    assert s.pending_custom_transform is None
    assert all((s.out_dir / "retrial" / f"{i:02d}" / "result.json").is_file() for i in range(2))
    assert "2 of 2 file(s) PASS" in s.run(False, False, quiet)


# ---- review-21: a custom transform's blocking finding is part of its preview consent, then Run -------------------------

def test_a_custom_transform_s_visual_findings_are_included_in_preview_consent(folder, tmp_path, monkeypatch):
    import spec_surgeon.preview as pv
    monkeypatch.setattr(pv, "render_samples", lambda *a, **k: None)  # no renderer in this test
    intents = []

    def review(intent, samples, work_dir, client_factory, *a, **k):
        intents.append(intent)
        return vision.VisualReview(summary="looked at the pages", findings=[
            {"severity": "block", "file": "21 00 00 – SPEC 0.docx", "page": 1, "text": "the header moved off the page"}])
    monkeypatch.setattr(vision, "review_samples", review)
    s = offered(folder, tmp_path, WRITER)
    assert "passed verification" in create_approved_custom(s, render=True)
    assert len(intents) == 1 and intents[0].startswith("A ONE-OFF CUSTOM TRANSFORM (set_header_distance)")
    assert s.generated_preview.review.blocking() and s.generated_token
    assert s.run_blocked_reason() == ""
    with pytest.raises(ValueError, match="not the current one"):
        s.acknowledge_review("an old preview's token")
    before = {f: f.read_bytes() for f in docx(folder)}
    out = s.run(in_place=False, render=False, log=quiet)            # edited copies: the documents are never touched
    assert "2 of 2 file(s) PASS" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in docx(folder)} == before
    outputs = sorted((s.last_run_dir / "outputs").glob("*.docx"))
    assert len(outputs) == 2 and all(o.read_bytes() not in before.values() for o in outputs)
    assert [t.passed for t in s.last_trials] == [True, True]


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
    assert "passed verification" in create_approved_custom(s, render=False)
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
