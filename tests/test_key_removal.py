"""App-key removal uses fake credentials only; external authentication stays intact."""
import os

import pytest
from fastapi.testclient import TestClient

from spec_surgeon import credentials, llm
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.settings import Settings
from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app


def window(tmp_path, monkeypatch, *, stored=None, environment=None, profile=False):
    if environment:
        monkeypatch.setenv(credentials.ENV, environment)
    store = {"key": stored} if stored else {}
    monkeypatch.setattr(credentials, "available", lambda: True)
    monkeypatch.setattr(credentials, "load", lambda: store.get("key"))
    monkeypatch.setattr(credentials, "save", lambda value: store.__setitem__("key", value))
    monkeypatch.setattr(credentials, "delete", lambda: store.pop("key", None) is not None)
    def remove_saved():
        if not credentials.available():
            raise RuntimeError("fake store unavailable")
        return store.pop("key", None) is not None
    monkeypatch.setattr(credentials, "remove_saved_key", remove_saved)
    monkeypatch.setattr(llm, "credentials_found", lambda: profile or any(os.environ.get(v) for v in llm.CREDENTIAL_ENV))
    task = DocumentTask(Session(out_root=tmp_path / "out", visual_check=False), render=False)
    state = WindowState(task, Settings(), token="key-test")
    return state, TestClient(create_app(state), headers={TOKEN_HEADER: "key-test"}), store


def test_a_session_key_can_be_removed_without_any_provider_call(tmp_path, monkeypatch):
    state, client, store = window(tmp_path, monkeypatch)
    result = client.post("/api/settings", json={"api_key": "fake-unvalidated-key", "remember": False}).json()
    assert result["key_present"] and result["can_remove_key"] and result["origin"] == "session"
    assert "fake-unvalidated-key" not in str(result) and store == {}
    result = client.post("/api/settings/remove-key").json()
    assert not result["key_present"] and not result["can_remove_key"]
    assert credentials.ENV not in os.environ and state.key_origin == "none"


def test_removal_deletes_the_saved_app_key_and_the_session_override(tmp_path, monkeypatch):
    state, client, store = window(tmp_path, monkeypatch, stored="fake-saved-key")
    assert state.key_origin == "saved"
    client.post("/api/settings", json={"api_key": "fake-replacement", "remember": False})
    assert store["key"] == "fake-saved-key"
    result = client.post("/api/settings/remove-key").json()
    assert not result["key_present"] and store == {} and credentials.ENV not in os.environ


def test_removing_an_override_restores_the_launch_environment_key(tmp_path, monkeypatch):
    _, client, store = window(tmp_path, monkeypatch, environment="fake-launch-key")
    assert not client.get("/api/settings").json()["can_remove_key"]
    assert client.post("/api/settings/remove-key").status_code == 400
    client.post("/api/settings", json={"api_key": "fake-override", "remember": True})
    result = client.post("/api/settings/remove-key").json()
    assert os.environ[credentials.ENV] == "fake-launch-key" and store == {}
    assert result["key_present"] and result["origin"] == "environment" and not result["can_remove_key"]


def test_external_auth_still_works_after_removing_the_app_key(tmp_path, monkeypatch):
    _, client, _ = window(tmp_path, monkeypatch, profile=True)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fake-external-token")
    client.post("/api/settings", json={"api_key": "fake-session-key", "remember": False})
    result = client.post("/api/settings/remove-key").json()
    assert result["key_present"] and result["origin"] == "none" and not result["can_remove_key"]
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "fake-external-token"


@pytest.mark.parametrize("available", [True, False])
def test_secure_store_failure_keeps_the_current_key(tmp_path, monkeypatch, available):
    _, client, store = window(tmp_path, monkeypatch, stored="fake-saved-key")
    monkeypatch.setattr(credentials, "available", lambda: available)
    def cannot_remove():
        raise RuntimeError("fake deletion failed")
    monkeypatch.setattr(credentials, "remove_saved_key", cannot_remove)
    response = client.post("/api/settings/remove-key")
    assert response.status_code == 400 and "could not be removed" in response.json()["error"]
    assert store["key"] == os.environ[credentials.ENV] == "fake-saved-key"


def test_removal_is_refused_while_work_runs(tmp_path, monkeypatch):
    state, client, store = window(tmp_path, monkeypatch, stored="fake-saved-key")
    monkeypatch.setattr(type(state), "busy", property(lambda self: True))
    assert client.post("/api/settings/remove-key").status_code == 409
    assert client.post("/api/settings", json={"api_key": "fake-new-key", "remember": False}).status_code == 409
    assert store["key"] == os.environ[credentials.ENV] == "fake-saved-key"


@pytest.mark.parametrize("available", [True, False])
def test_unknown_saved_key_state_cannot_report_success(tmp_path, monkeypatch, available):
    _, client, store = window(tmp_path, monkeypatch, stored="fake-old-key", environment="fake-launch-key")
    client.post("/api/settings", json={"api_key": "fake-override", "remember": False})
    monkeypatch.setattr(credentials, "available", lambda: available)
    monkeypatch.setattr(credentials, "load", lambda: None)  # a swallowed read failure is not confirmed absence
    def denied():
        raise RuntimeError("fake store inaccessible")
    monkeypatch.setattr(credentials, "remove_saved_key", denied)
    assert client.post("/api/settings/remove-key").status_code == 400
    assert store["key"] == "fake-old-key" and os.environ[credentials.ENV] == "fake-override"


@pytest.mark.parametrize("code,absent", [(1168, True), (5, False)])
def test_strict_removal_recognizes_only_windows_not_found_as_absence(monkeypatch, code, absent):
    class FakeStore:
        CRED_TYPE_GENERIC = 1
        def CredDelete(self, *args):
            raise OSError(code, "fake credential error")
    monkeypatch.setattr(credentials, "_win32cred", lambda: FakeStore())
    if absent:
        assert credentials.remove_saved_key() is False
    else:
        with pytest.raises(RuntimeError, match="could not be removed"):
            credentials.remove_saved_key()


def test_key_changes_forget_a_conversations_cached_sdk_client(tmp_path, monkeypatch):
    from spec_surgeon import compiler
    from spec_surgeon.conversation import Conversation
    state, client, _ = window(tmp_path, monkeypatch)
    conversation = Conversation("continue this request", [])
    state.task.session.conversation = conversation
    old_client = object()
    conversation._client = old_client  # a client previously built from the app key
    client.post("/api/settings", json={"api_key": "fake-session-key", "remember": False})
    assert conversation._client is None
    conversation._client = old_client
    client.post("/api/settings/remove-key")
    assert conversation._client is None
    def unavailable():
        raise RuntimeError("fake credentials unavailable")
    monkeypatch.setattr(compiler, "_client", unavailable)
    with pytest.raises(RuntimeError, match="credentials unavailable"):
        conversation._client_now()


def test_removal_does_not_clear_an_explicitly_supplied_client(tmp_path, monkeypatch):
    from spec_surgeon.conversation import Conversation
    state, client, _ = window(tmp_path, monkeypatch)
    supplied = object()
    state.task.session.conversation = Conversation("external client", [], client=supplied)
    client.post("/api/settings", json={"api_key": "fake-session-key", "remember": False})
    client.post("/api/settings/remove-key")
    assert state.task.session.conversation._client_now() is supplied
