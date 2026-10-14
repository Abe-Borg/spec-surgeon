"""What the window remembers (``spec_surgeon.settings``): round trip, hand-edited and corrupt files, atomic writes,
and what is deliberately never stored."""
from __future__ import annotations

import dataclasses
import json

from spec_surgeon import settings as st
from spec_surgeon.settings import Settings, load_settings, save_settings


def test_round_trip(tmp_path):
    p = tmp_path / "settings.json"
    s = Settings(folder=r"C:\Projects\21 – Fire\Specs", out_root="D:/out", render=False, skipped_version="0.5.0",
                 auto_check_updates=False, last_update_check=123.5, pending_update={"version": "0.5.0"})
    save_settings(s, p)
    assert load_settings(p) == s
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_wrong_types_and_unknown_keys(tmp_path):
    p = tmp_path / "settings.json"
    assert load_settings(p) == Settings()
    p.write_text(json.dumps({"folder": 5, "render": "yes", "last_update_check": True, "auto_check_updates": "on",
                             "pending_update": [], "surprise": 1, "theme": "dark"}), encoding="utf-8")
    s = load_settings(p)
    assert s == Settings(theme="dark")


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
    assert not names & {"in_place", "codegen", "allow_codegen", "api_key", "key", "anthropic_api_key"}
