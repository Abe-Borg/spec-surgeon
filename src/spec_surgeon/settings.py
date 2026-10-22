"""What the window remembers between sessions, and where the app keeps its own files.

    %APPDATA%\\Spec Surgeon\\settings.json        folders, options, the colour scheme, update preferences (this module)
    %LOCALAPPDATA%\\Spec Surgeon\\updates\\        downloaded installers and install logs (``updater``)
    %LOCALAPPDATA%\\Spec Surgeon\\logs\\           the window's error log

``SPEC_SURGEON_CONFIG_DIR`` moves all of it (settings at its root, the rest under ``data/``) — the tests use it.

Plain JSON: the user can see and delete the file, and the updater reads it without the window. Never stored:
the API key (Windows Credential Manager, ``credentials``), the safety-relevant switch (write back over the originals —
off at every start), the conversation with Claude and any acknowledgement of Claude's visual check (both belong to one
preview, never to the next start). ``theme`` is one of ``THEMES``, the window's colour schemes; any other value (a
v0.4.x file's ``"dark"``) reads as the first. Not settings at all (since the GUI rework after v0.4.1): the automatic
update check (once a day, always; ``SPEC_SURGEON_NO_UPDATE_CHECK`` is the only off switch). Keys an older
``settings.json`` still holds for it (``native_style``, ``auto_check_updates``) are ignored.
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


def default_run_root() -> Path:
    """Persist delivered copies locally; run results must never use a disposable cache."""
    if os.environ.get(CONFIG_ENV) or sys.platform.startswith("win"):
        return data_dir() / "runs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME / "runs"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "spec-surgeon" / "runs"


def output_root_warnings(root) -> list[str]:
    """One-line notices for chosen folders; explicit user choices remain usable."""
    text = str(root).replace("\\", "/").rstrip("/")
    parts = text.casefold().split("/")
    synced = any(part == "dropbox" or part.startswith("dropbox (")
                 or part == "onedrive" or part.startswith("onedrive - ")
                 or part in {"google drive", "googledrive", "my drive", "icloud drive", "box"}
                 for part in parts)
    # OneDrive can be relocated under a folder with any name. Environment
    # variables identify that case without opening or querying a sync client.
    for key in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        candidate = os.environ.get(key, "").replace("\\", "/").rstrip("/").casefold()
        if candidate and (text.casefold() == candidate or text.casefold().startswith(candidate + "/")):
            synced = True
    notices = []
    if synced:
        notices.append("Output is in a synced folder; sync may lock run files. A local folder is recommended.")
    # Reserve room for the deepest generated evidence path (a recovery page
    # render). Count UTF-16 code units as Windows does.
    suffix = ("/20261019-235959/recovery/inspection/" + "f" * 16 + "/pages/" + "f" * 16
              + "/renders/" + "f" * 16 + "/pages/p999999.png")
    projected = len((text + suffix).encode("utf-16-le")) // 2
    if projected >= 240:
        notices.append("Output folder is deep enough for generated paths to approach Windows' path limit; "
                       "choose a shorter local folder.")
    return notices


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILE


# The window's colour schemes, the first the default: the page's own list is frontend/src/lib/theme.ts, the colours
# frontend/src/index.css (tests/test_settings.py keeps the three in step).
THEMES = ("clay", "blueprint", "graphite", "carbon", "linen")


@dataclass
class Settings:
    schema: int = 1
    folder: str = ""
    template: str = ""
    out_root: str = ""
    render: bool = True
    request_draft: str = ""
    window_geometry: str = ""                     # hex of QWidget.saveGeometry()
    log_open: bool = False
    theme: str = THEMES[0]                        # the window's colour scheme, one of THEMES
    last_update_check: float = 0.0                # epoch seconds of the last automatic attempt (success or not)
    skipped_version: str = ""
    pending_update: Dict[str, Any] = field(default_factory=dict)   # {"version", "log", "started"} while installing
    last_run_version: str = ""


_FIELDS = {f.name: f for f in dataclasses.fields(Settings)}


def _coerce(name: str, value: Any, default: Any) -> Any:
    """The stored value when it has the field's type, else the default (a hand-edited file never crashes the app)."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if name == "theme":
        return value if value in THEMES else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if name == "pending_update":
        # only the keys the updater writes, with the types it writes; anything else means "no update pending"
        if not isinstance(value, dict) or not isinstance(value.get("version"), str):
            return default
        out = {"version": value["version"]}
        if isinstance(value.get("log"), str):
            out["log"] = value["log"]
        if isinstance(value.get("started"), (int, float)) and not isinstance(value.get("started"), bool):
            out["started"] = float(value["started"])
        return out
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
