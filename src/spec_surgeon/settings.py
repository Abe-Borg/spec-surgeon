"""What the window remembers between sessions, and where the app keeps its own files.

    %APPDATA%\\Spec Surgeon\\settings.json        folders, options, update preferences (this module)
    %LOCALAPPDATA%\\Spec Surgeon\\updates\\        downloaded installers and install logs (``updater``)
    %LOCALAPPDATA%\\Spec Surgeon\\logs\\           the window's error log

``SPEC_SURGEON_CONFIG_DIR`` moves all of it (settings at its root, the rest under ``data/``) — the tests use it.

Plain JSON, not QSettings: the user can see and delete the file, and the updater reads it without Qt. Never stored:
the API key (Windows Credential Manager, ``credentials``), and the safety-relevant switches (write back over the
originals, AI-written transforms) — they are off at every start.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_ENV = "SPEC_SURGEON_CONFIG_DIR"
APP_DIR_NAME = "Spec Surgeon"
SETTINGS_FILE = "settings.json"


def config_dir() -> Path:
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV])
    if sys.platform.startswith("win"):
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR_NAME
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "spec-surgeon"


def data_dir() -> Path:
    if os.environ.get(CONFIG_ENV):
        return Path(os.environ[CONFIG_ENV]) / "data"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_DIR_NAME
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "spec-surgeon"


def updates_dir() -> Path:
    return data_dir() / "updates"


def logs_dir() -> Path:
    return data_dir() / "logs"


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


@dataclass
class Settings:
    schema: int = 1
    folder: str = ""
    template: str = ""
    out_root: str = ""
    render: bool = True
    request_draft: str = ""
    theme: str = "system"                         # system | light | dark
    native_style: bool = False                    # Qt's windows11 style instead of the app's own theme
    window_geometry: str = ""                     # hex of QWidget.saveGeometry()
    log_open: bool = False
    auto_check_updates: Optional[bool] = None     # None → this build's default (on when installed, off for pip/source)
    last_update_check: float = 0.0                # epoch seconds of the last automatic attempt (success or not)
    skipped_version: str = ""
    pending_update: Dict[str, Any] = field(default_factory=dict)   # {"version", "log", "started"} while installing
    last_run_version: str = ""


_FIELDS = {f.name: f for f in dataclasses.fields(Settings)}


def _coerce(name: str, value: Any, default: Any) -> Any:
    """The stored value when it has the field's type, else the default (a hand-edited file never crashes the app)."""
    if name == "auto_check_updates":
        return value if value is None or isinstance(value, bool) else default
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else default
    return default


def load_settings(path: Optional[Path] = None) -> Settings:
    """Read the settings; unknown keys are ignored, wrong types fall back to defaults, and an unreadable file is
    renamed to ``settings.json.bad`` (kept for inspection) and replaced by defaults."""
    path = Path(path) if path else settings_path()
    s = Settings()
    if not path.is_file():
        return s
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError):
        try:
            path.replace(path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return s
    for name, f in _FIELDS.items():
        if name in data:
            setattr(s, name, _coerce(name, data[name], getattr(s, name)))
    return s


def save_settings(s: Settings, path: Optional[Path] = None) -> Path:
    """Write atomically (temp file + replace): a crash mid-write never leaves a half-written settings file."""
    path = Path(path) if path else settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(dataclasses.asdict(s), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    return path
