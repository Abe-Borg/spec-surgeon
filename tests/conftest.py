import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402

# The model the tests expect (llm.DEFAULT_MODEL when SPEC_SURGEON_MODEL is not set).
PINNED_MODEL = "claude-opus-5-5"
# SPEC_SURGEON_MODEL is read once, when spec_surgeon.llm is first imported, and bound by value from there on (compiler
# and advisor re-export DEFAULT_MODEL; CompileResult's `model` field default holds it). Drop it before any test module
# imports the package, so every one of those bindings is the default whatever the developer's machine sets;
# `_hermetic` pins the module constants again for every test in case the package was imported before this file.
os.environ.pop("SPEC_SURGEON_MODEL", None)

# What `seal_environment` takes away from every test (and puts back exactly as the machine had it afterwards).
SEALED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "SPEC_SURGEON_MODEL")

# The one test file that reaches the real Windows Credential Manager: a round trip on a throw-away target.
REAL_CREDENTIALS_TEST = (Path(__file__).parent / "test_credentials.py").resolve()


def _no_live_claude():
    raise RuntimeError("no live Claude calls in tests — script a FakeClient and pass it in (or patch compiler._client)")


def _no_credential_manager():
    """Stands in for ``credentials._win32cred`` (the only route to win32cred): Credential Manager is not there, as on
    Linux — ``available()`` False, ``load()`` None, ``save()`` raises, ``delete()`` False."""
    return None


def seal_environment(monkeypatch) -> None:
    """No API key and no model choice from the machine. Each variable is set and then deleted, so the monkeypatch
    records the machine's value — or that there was none — and its teardown restores exactly that, even after a test
    (or the window's Settings, which writes ``os.environ`` itself) put a key there by itself."""
    for var in SEALED_ENV:
        monkeypatch.setenv(var, "sealed")
        monkeypatch.delenv(var)


def seal_credential_manager(monkeypatch) -> None:
    """No test reads or writes the key saved on this computer. On a Windows machine with pywin32 and a key saved through
    Settings, a window would load the real key (``MainWindow._init_key``) and the Settings test would overwrite it."""
    from spec_surgeon import credentials
    monkeypatch.setattr(credentials, "_win32cred", _no_credential_manager)


def pin_model(monkeypatch) -> None:
    """Every module that bound DEFAULT_MODEL at import (llm, compiler, advisor — any spec_surgeon module) says
    PINNED_MODEL. One imported later, during the test, binds it from the pinned llm (directly or through compiler)."""
    from spec_surgeon import llm  # noqa: F401 — the source every other binding copies
    for name, mod in list(sys.modules.items()):
        if name.split(".")[0] == "spec_surgeon" and hasattr(mod, "DEFAULT_MODEL"):
            monkeypatch.setattr(mod, "DEFAULT_MODEL", PINNED_MODEL)


@pytest.fixture(autouse=True)
def _hermetic(request, tmp_path_factory, monkeypatch):
    """Every test is sealed off from the machine it runs on: settings and the personal library live in a temporary
    folder (never the real %APPDATA%), no API key is visible (and none a test writes outlives it), Windows Credential
    Manager is out of reach (only tests/test_credentials.py's throw-away round trip reaches it), the model is the
    default whatever SPEC_SURGEON_MODEL says, a Claude client that was not scripted cannot be built, and the command
    line never thinks it is talking to a person (no prompt can block a test run in a console)."""
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
    seal_environment(monkeypatch)
    if Path(str(request.node.path)).resolve() != REAL_CREDENTIALS_TEST:
        seal_credential_manager(monkeypatch)
    pin_model(monkeypatch)
    try:
        from spec_surgeon import llm
        monkeypatch.setattr(llm, "client", _no_live_claude)
    except ImportError:
        pass
    try:
        from spec_surgeon import cli
        if hasattr(cli, "_interactive"):
            monkeypatch.setattr(cli, "_interactive", lambda: False)
    except ImportError:
        pass
