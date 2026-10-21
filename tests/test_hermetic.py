"""The test run is sealed off from the machine it runs on (``conftest._hermetic``): no test reaches Windows Credential
Manager (only test_credentials.py's throw-away round trip), no key a test writes outlives it, and the model is the
default whatever ``SPEC_SURGEON_MODEL`` says on the developer's machine."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, text, tool
from make_fixtures import build
from spec_surgeon import credentials
from spec_surgeon.gui import Session

ROOT = Path(__file__).resolve().parent.parent
SPECS = ROOT / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
SRC = ROOT / "src" / "spec_surgeon"
PINNED = "claude-opus-5-5"
REAL_KEY = "sk-ant-ABRAHAMS-REAL-SAVED-KEY"


class FakeCredentialManager:
    """Stands in for pywin32's ``win32cred`` on a Windows machine where the installed app saved a real key: it holds
    that key under the app's own target and records every call that reaches it."""
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self):
        self.store = {credentials.TARGET: REAL_KEY}
        self.calls = []

    def CredRead(self, target, type_, flags):  # noqa: N802 — pywin32's names
        self.calls.append(("CredRead", target))
        if target not in self.store:
            raise OSError(1168, "CredRead", "Element not found.")
        return {"TargetName": target, "CredentialBlob": self.store[target].encode("utf-16-le")}

    def CredWrite(self, cred, flags):  # noqa: N802
        self.calls.append(("CredWrite", cred["TargetName"]))
        self.store[cred["TargetName"]] = cred["CredentialBlob"]

    def CredDelete(self, target, type_, flags):  # noqa: N802
        self.calls.append(("CredDelete", target))
        del self.store[target]


@pytest.fixture
def machine_with_a_saved_key(monkeypatch):
    """Plants the recording Credential Manager where ``import win32cred`` finds it, as on Abraham's machine."""
    fake = FakeCredentialManager()
    monkeypatch.setitem(sys.modules, "win32cred", fake)
    return fake


def _unpatched_credentials_module():
    """A fresh copy of spec_surgeon/credentials.py (not the module object conftest patched): the control that shows the
    planted store is what the app's own code would read."""
    spec = importlib.util.spec_from_file_location("_credentials_control", SRC / "credentials.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_no_test_reads_or_writes_the_key_saved_on_this_computer(machine_with_a_saved_key):
    fake = machine_with_a_saved_key
    assert credentials.available() is False
    assert credentials.load() is None and credentials.key_source() == "none"
    with pytest.raises(RuntimeError):
        credentials.save("sk-ant-test-not-a-real-key")
    assert credentials.delete() is False
    assert fake.calls == [] and fake.store == {credentials.TARGET: REAL_KEY}
    control = _unpatched_credentials_module()                 # without conftest's cut the same machine gives up the key
    assert control.available() and control.load() == REAL_KEY and fake.calls == [("CredRead", credentials.TARGET)]


def test_a_window_never_loads_or_overwrites_the_saved_key(machine_with_a_saved_key, monkeypatch, tmp_path):
    """The window's server never reads the key saved on this computer in a test, and Settings → remember cannot
    reach the store either (conftest's cut makes ``credentials.available()`` False)."""
    from fastapi.testclient import TestClient
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import TOKEN_HEADER, WindowState, create_app
    monkeypatch.setenv("SPEC_SURGEON_NO_UPDATE_CHECK", "1")
    monkeypatch.delenv(credentials.ENV, raising=False)
    fake = machine_with_a_saved_key
    state = WindowState(DocumentTask(Session(out_root=tmp_path / "out"), render=False), Settings(), token="t")
    assert state.key_origin == "none" and credentials.ENV not in os.environ     # the window's init found nothing
    client = TestClient(create_app(state), headers={TOKEN_HEADER: "t"})
    assert client.get("/api/settings").json()["can_remember"] is False
    answer = client.post("/api/settings", json={"api_key": "sk-ant-test-not-a-real-key", "remember": True}).json()
    assert answer["origin"] == "session" and state.key_origin == "session"   # ticked, yet the store is out of reach
    assert os.environ[credentials.ENV] == "sk-ant-test-not-a-real-key"
    assert fake.calls == [] and fake.store == {credentials.TARGET: REAL_KEY}


def test_credentials_py_is_the_only_way_to_credential_manager():
    """conftest cuts the tests off at ``credentials._win32cred``; that is complete only while no other module imports
    win32cred or calls the Cred* API by another route."""
    offenders = []
    for py in SRC.rglob("*.py"):
        if py.name == "credentials.py" and py.parent == SRC:
            continue
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names = [node.value]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            if any(n.split(".")[0] == "win32cred" or n.startswith(("CredRead", "CredWrite", "CredDelete"))
                   for n in names):
                offenders.append(py.relative_to(SRC).as_posix())
    assert offenders == []


def test_the_model_is_the_default_whatever_the_machine_sets(tmp_path):
    """Run as is and, by the test below, in a pytest of its own with SPEC_SURGEON_MODEL set."""
    from spec_surgeon import compiler, llm
    assert "SPEC_SURGEON_MODEL" not in os.environ
    assert llm.DEFAULT_MODEL == compiler.DEFAULT_MODEL == PINNED
    bound = {name: mod.DEFAULT_MODEL for name, mod in list(sys.modules.items())
             if name.split(".")[0] == "spec_surgeon" and hasattr(mod, "DEFAULT_MODEL")}
    assert set(bound.values()) == {PINNED}, bound
    assert compiler.CompileResult(status="ok").model == PINNED          # a dataclass default bound at import
    doc = tmp_path / "21 00 00 – SPEC.docx"
    doc.write_bytes(build("21 00 00", "SPEC"))
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})), resp(text("done"), stop="end_turn"))
    res = compiler.compile_request("remove all headers and footers", [doc], client=fake)
    assert fake.requests[0]["model"] == PINNED and res.model == PINNED


def test_a_model_bound_before_conftest_ran_is_pinned_for_the_test(monkeypatch, tmp_path):
    """conftest drops SPEC_SURGEON_MODEL before the package is imported; if something imported it earlier (a plugin),
    pin_model still makes every bound copy the default for the test."""
    import conftest
    from spec_surgeon import compiler, llm
    for mod in (llm, compiler):
        monkeypatch.setattr(mod, "DEFAULT_MODEL", "claude-sonnet-5-5")      # what an earlier import would have bound
    inner = pytest.MonkeyPatch()
    try:
        conftest.pin_model(inner)
        assert llm.DEFAULT_MODEL == compiler.DEFAULT_MODEL == PINNED
        doc = tmp_path / "21 00 00 – SPEC.docx"
        doc.write_bytes(build("21 00 00", "SPEC"))
        fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE})), resp(text("done"), stop="end_turn"))
        res = compiler.compile_request("remove all headers and footers", [doc], client=fake)
        assert res.model == PINNED and fake.requests[0]["model"] == PINNED
    finally:
        inner.undo()


def test_a_model_set_on_the_developer_s_machine_does_not_reach_the_tests():
    env = {**os.environ, "SPEC_SURGEON_MODEL": "claude-sonnet-5-5", "PYTHONDONTWRITEBYTECODE": "1"}
    node = f"{Path(__file__).resolve()}::test_the_model_is_the_default_whatever_the_machine_sets"
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
                       cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
    assert "1 passed" in r.stdout


@pytest.mark.parametrize("machine", [None, "sk-ant-the-machine-s-own-key"])
def test_a_key_a_test_writes_into_the_environment_is_gone_after_it(monkeypatch, machine):
    """ConnectionDialog.apply writes ``os.environ[ANTHROPIC_API_KEY]`` itself; after the test the environment is the
    machine's again (its own key or none), not the typed one."""
    import conftest
    if machine is not None:
        monkeypatch.setenv(credentials.ENV, machine)
    inner = pytest.MonkeyPatch()
    try:
        conftest.seal_environment(inner)
        assert credentials.ENV not in os.environ and "SPEC_SURGEON_MODEL" not in os.environ
        os.environ[credentials.ENV] = "sk-ant-typed-in-settings"
    finally:
        inner.undo()
    assert os.environ.get(credentials.ENV) == machine
