import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pytest  # noqa: E402


def _no_live_claude():
    raise RuntimeError("no live Claude calls in tests — script a FakeClient and pass it in (or patch compiler._client)")


@pytest.fixture(autouse=True)
def _hermetic(tmp_path_factory, monkeypatch):
    """Every test is sealed off from the machine it runs on: settings and the personal library live in a temporary
    folder (never the real %APPDATA%), no API key is visible, a Claude client that was not scripted cannot be built,
    and the command line never thinks it is talking to a person (no prompt can block a test run in a console)."""
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path_factory.mktemp("config")))
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
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
