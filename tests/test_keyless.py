"""Running without an API key (keyless mode): what needs Claude says so instead of failing, and everything else — saved
specs, Preview, Run and its verification — works without a key in the session.

``tests/conftest.py`` makes ``llm.credentials_found`` True for every test (the tests script their Claude clients); the
tests here patch it to False and test the real function on its own (``REAL_CREDENTIALS_FOUND``, taken when this module
is collected, before any fixture patches it)."""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import llm, vision
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import NEEDS_KEY, NEEDS_KEY_REPLY, NO_KEY_REVIEW, Session
from test_compiler import FakeClient, resp, tool

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
REAL_CREDENTIALS_FOUND = llm.credentials_found
REAL_SDK_CREDENTIALS = llm._sdk_credentials


@pytest.fixture
def keyless(monkeypatch):
    monkeypatch.setattr(llm, "credentials_found", lambda: False)


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def session(folder, tmp_path, **kw) -> Session:
    return Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out", **kw)


def load_saved_spec(s: Session, path: Path) -> None:
    s._set_outcome(spec=load_spec(path), source=str(path))


def quiet(_m):
    pass


# ---- the one decision: llm.credentials_found ------------------------------------------------------------------------

def test_credentials_are_a_key_in_the_environment_or_a_profile_the_sdk_finds(monkeypatch):
    assert REAL_CREDENTIALS_FOUND() is False                       # sealed: no key, no profile (conftest)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    assert REAL_CREDENTIALS_FOUND() is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    assert REAL_CREDENTIALS_FOUND() is True
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")                     # an empty variable is no key
    assert REAL_CREDENTIALS_FOUND() is False
    monkeypatch.setattr(llm, "_sdk_credentials", lambda: True)      # an `ant auth login` profile
    assert REAL_CREDENTIALS_FOUND() is True


def test_the_sdk_profile_lookup_never_raises_and_never_calls(monkeypatch):
    fake = types.ModuleType("anthropic.lib.credentials")
    monkeypatch.setitem(sys.modules, "anthropic.lib.credentials", fake)
    fake.default_credentials = lambda: object()
    assert REAL_SDK_CREDENTIALS() is True
    fake.default_credentials = lambda: None
    assert REAL_SDK_CREDENTIALS() is False

    def broken():
        raise ValueError("profile 'dev' cannot be read")
    fake.default_credentials = broken
    assert REAL_SDK_CREDENTIALS() is False                          # cannot authenticate either: keyless
    monkeypatch.setitem(sys.modules, "anthropic.lib.credentials", None)   # no SDK (or one without profiles)
    assert REAL_SDK_CREDENTIALS() is False


# ---- the session ------------------------------------------------------------------------------------------------

def test_a_keyless_session_previews_and_runs_a_saved_spec_and_refuses_what_needs_claude(keyless, folder, tmp_path):
    s = session(folder, tmp_path)
    assert s.claude_ready() is False
    load_saved_spec(s, SPECS / "remove-all-headers-footers.json")
    with pytest.raises(ValueError) as e:
        s.compile("change all fonts to Arial", quiet)
    assert str(e.value) == NEEDS_KEY
    assert s.spec == REMOVE and s.conversation is None              # refused before anything changed
    assert "2 plan cleanly" in s.make_preview(render=False, log=quiet)
    out = s.run(False, False, quiet)
    assert "PASS 2" in out and "Advise" not in out


def test_the_visual_check_is_skipped_without_a_key_and_nothing_is_sent(keyless, folder, tmp_path, monkeypatch):
    def never(*a, **k):
        raise AssertionError("keyless: nothing is prepared or sent")
    monkeypatch.setattr(vision, "review_samples", never)
    s = session(folder, tmp_path)
    load_saved_spec(s, SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=quiet)
    lines = []
    s._visual_check(s.preview, s._intent(), s.out_dir / "preview", lines.append)
    rv = s.preview.review
    assert rv.skipped == NO_KEY_REVIEW and rv.findings == [] and rv.blocking() == []
    assert s.run_blocked_reason() == ""                             # it can stop a run, never block one by its absence
    assert any("visual check skipped" in m for m in lines)
    saved = json.loads((s.out_dir / "preview" / "visual_review.json").read_text(encoding="utf-8"))
    assert saved["skipped"] == NO_KEY_REVIEW
    seen = []
    monkeypatch.setattr(vision, "review_samples", lambda *a, **k: seen.append(1) or vision.VisualReview())
    s.client = FakeClient()                                         # a client given: Claude can be called
    s._visual_check(s.preview, s._intent(), s.out_dir / "preview", quiet)
    assert seen == [1]


def test_a_conversation_waits_for_a_key_once_it_is_gone(folder, tmp_path, monkeypatch):
    s = session(folder, tmp_path, client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    s.compile("remove the headers", quiet)
    assert s.conversation is not None and s.turn.status == "questions"
    s.client = None
    monkeypatch.setattr(llm, "credentials_found", lambda: False)    # Settings → Forget saved key
    turns = s.conversation.user_turns()
    with pytest.raises(ValueError) as e:
        s.reply("all of them", log=quiet)
    assert str(e.value) == NEEDS_KEY_REPLY and s.conversation.user_turns() == turns


# ---- the command line -------------------------------------------------------------------------------------------
