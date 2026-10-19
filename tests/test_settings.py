"""What the window remembers (``spec_surgeon.settings``): round trip, hand-edited and corrupt files, atomic writes,
and what is deliberately never stored."""
from __future__ import annotations

import dataclasses
import json

import pytest

from spec_surgeon import settings as st
from spec_surgeon.settings import Settings, load_settings, save_settings


def test_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings(folder=r"C:\Projects\21 – Fire\Specs", out_root="D:/out", render=False, skipped_version="0.5.0",
                 last_update_check=123.5, pending_update={"version": "0.5.0"})
    save_settings(s, p)
    assert load_settings(p) == s
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_wrong_types_and_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    assert load_settings(p) == Settings()
    p.write_text(json.dumps({"folder": 5, "render": "yes", "last_update_check": True, "auto_check_updates": "on",
                             "pending_update": [], "surprise": 1, "theme": "dark", "log_open": True}), encoding="utf-8")
    s = load_settings(p)
    assert s == Settings(log_open=True)


def test_the_look_and_the_update_check_are_not_settings():
    """The GUI rework after v0.4.1 removed the Appearance and Updates panels: the window follows Windows' light/dark
    setting and checks for updates once a day without being told to. A settings.json from v0.4.x still loads."""
    names = {f.name for f in dataclasses.fields(Settings)}
    assert not names & {"theme", "native_style", "auto_check_updates"}
    assert {"last_update_check", "skipped_version", "pending_update", "last_run_version"} <= names


def test_a_hand_edited_pending_update_cannot_break_start_up(tmp_path):
    p = tmp_path / "settings.json"
    for bad in ({"version": 1}, {"log": "x"}, "0.5.0", [], {"version": None}):
        p.write_text(json.dumps({"pending_update": bad}), encoding="utf-8")
        assert load_settings(p).pending_update == {}
    p.write_text(json.dumps({"pending_update": {"version": "0.5.0", "log": 7, "started": "yesterday", "x": 1}}),
                 encoding="utf-8")
    assert load_settings(p).pending_update == {"version": "0.5.0"}
    p.write_text(json.dumps({"pending_update": {"version": "0.5.0", "log": "l.log", "started": 12}}), encoding="utf-8")
    assert load_settings(p).pending_update == {"version": "0.5.0", "log": "l.log", "started": 12.0}


def test_corrupt_file_is_kept_aside(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_settings(p) == Settings()
    assert (tmp_path / "settings.json.bad").read_text(encoding="utf-8") == "{not json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert load_settings(p) == Settings()


def test_config_dir_override(tmp_path, monkeypatch):
    monkeypatch.setenv(st.CONFIG_ENV, str(tmp_path))
    assert st.settings_path() == tmp_path / "settings.json"
    assert st.updates_dir() == tmp_path / "data" / "updates" and st.logs_dir() == tmp_path / "data" / "logs"
    save_settings(Settings(folder="x"))
    assert load_settings().folder == "x"


def test_safety_switches_and_the_key_are_never_settings():
    names = {f.name for f in dataclasses.fields(Settings)}
    assert not names & {"in_place", "codegen", "allow_codegen", "api_key", "key", "anthropic_api_key",
                        "accept_custom_transform", "review_ack", "acknowledge_review", "conversation"}


def test_default_runs_use_local_app_data_on_windows(monkeypatch):
    from pathlib import Path
    monkeypatch.delenv(st.CONFIG_ENV, raising=False)
    monkeypatch.setattr(st.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/Owner/AppData/Local")
    assert st.default_run_root() == Path("C:/Users/Owner/AppData/Local/Spec Surgeon/runs")


@pytest.mark.parametrize("xdg_data", [None, "persistent-data"])
def test_unix_deliverables_use_persistent_data_independently_of_cache(tmp_path, monkeypatch, xdg_data):
    monkeypatch.delenv(st.CONFIG_ENV, raising=False)
    monkeypatch.setattr(st.sys, "platform", "linux")
    monkeypatch.setattr(st.Path, "home", lambda: tmp_path / "home")
    cache = tmp_path / "disposable-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    if xdg_data:
        persistent = tmp_path / xdg_data
        monkeypatch.setenv("XDG_DATA_HOME", str(persistent))
    else:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        persistent = tmp_path / "home" / ".local" / "share"
    assert st.default_run_root() == persistent / "spec-surgeon" / "runs"
    assert st.logs_dir().is_relative_to(cache)


def test_mac_deliverables_use_application_support(tmp_path, monkeypatch):
    monkeypatch.delenv(st.CONFIG_ENV, raising=False)
    monkeypatch.setattr(st.sys, "platform", "darwin")
    monkeypatch.setattr(st.Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "disposable-cache"))
    assert st.default_run_root() == tmp_path / "home" / "Library" / "Application Support" / "Spec Surgeon" / "runs"


def test_synced_and_deep_output_roots_have_actionable_single_line_warnings(monkeypatch):
    monkeypatch.setenv("OneDriveCommercial", "D:/Company Documents")
    for root in ("C:/Users/Owner/OneDrive - Ehvert/Specs", "D:/Company Documents/Jobs", "/home/owner/Dropbox/Specs"):
        warnings = st.output_root_warnings(root)
        assert any("synced" in line and "local" in line for line in warnings)
        assert all("\n" not in line for line in warnings)
    assert not st.output_root_warnings("C:/runs")
    assert any("path limit" in line for line in st.output_root_warnings("C:/" + "a" * 150))
    assert any("path limit" in line for line in st.output_root_warnings("C:/" + "😀" * 80))
    assert not st.output_root_warnings("C:/Users/Owner/OneDrive-backup")
