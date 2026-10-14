"""The window (Phase 7, first cut): its logic (``gui.Session``) end to end with a scripted client, and — where Tk
and a display exist — the real window driven through its buttons."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon.gui import Session
from test_codegen import GOOD_OP
from test_compiler import FakeClient, resp, tool

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "Specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def log_to(lines):
    return lambda m: lines.append(m)


def test_request_preview_run(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out", client=FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    lines = []
    with pytest.raises(ValueError, match="Preview first"):
        s.run(False, False, log_to(lines))
    assert s.compile("remove all headers and footers", [], False, log_to(lines)) == "Compiled."
    text = s.make_preview(render=False, log=log_to(lines))
    assert "2 plan cleanly" in text and s.preview.token in text
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    out = s.run(in_place=False, render=False, log=log_to(lines))
    assert "PASS 2" in out and "your files were not changed" in out
    assert {f: f.read_bytes() for f in folder.glob("*.docx")} == before
    assert (s.last_run_dir / "outputs").is_dir()


def test_run_refuses_after_a_file_changed(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    f = sorted(folder.glob("*.docx"))[0]
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    with pytest.raises(ValueError, match="changed since the preview"):
        s.run(False, False, lambda m: None)


def test_in_place_run_is_guarded(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    before = {f: f.read_bytes() for f in folder.glob("*.docx")}
    out = s.run(in_place=True, render=False, log=lambda m: None)
    assert "written back" in out
    assert all(f.read_bytes() != before[f] for f in folder.glob("*.docx"))


def test_questions_and_unsupported(folder, tmp_path):
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]}))))
    msg = s.compile("remove some headers", [], False, lambda m: None)
    assert "Answers box" in msg and s.questions == ["Which sections?"] and s.spec is None
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s.client = FakeClient(resp(tool("submit_editspec", {"spec": unsupported})))
    assert "Allow AI-written transforms" in s.compile("x", [], False, lambda m: None)
    s.client = FakeClient(resp(tool("submit_editspec", {"spec": unsupported})),
                          resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    assert "passed verification" in s.compile("x", [], True, lambda m: None)
    out = s.run(False, False, lambda m: None)
    assert "2 of 2 file(s) PASS" in out


def test_errors_are_plain(tmp_path):
    with pytest.raises(ValueError, match="choose the folder"):
        Session(out_root=tmp_path).files()
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no .docx files"):
        Session(folder=empty, out_root=tmp_path).files()
    with pytest.raises(ValueError, match="type what you want"):
        Session(folder=empty, out_root=tmp_path).compile("  ", [], False, lambda m: None)


def _tk_or_skip():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError as e:
        pytest.skip(f"no display: {e}")
    return root


def test_the_window_previews_and_runs_a_spec_file(folder, tmp_path):
    root = _tk_or_skip()
    from spec_surgeon.gui import build_app
    s = Session(out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    app = build_app(root, s)
    ui = app._spec_surgeon
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)

    def click_and_wait(name, timeout=60):
        ui["buttons"][name].invoke()
        t0 = time.time()
        while time.time() - t0 < timeout:
            app.update()
            if ui["status"].get() in ("Ready.", "Error — see the log."):
                return ui["log"].get("1.0", "end")
            time.sleep(0.05)
        raise AssertionError("timed out")

    text = click_and_wait("preview")
    assert "2 plan cleanly" in text, text
    text = click_and_wait("run")
    assert "PASS 2" in text, text
    app.destroy()


def test_template_in_the_folder_is_neither_previewed_nor_run(folder, tmp_path):
    """Codex review on PR #4: preview and Run must use the same filtered file set — a template file selected in the
    window and sitting in the specs folder is excluded from both (else Run always refused, or edited the template)."""
    from make_fixtures import build_template
    tpl = folder / "00 00 00 TEMPLATE.docx"
    tpl.write_bytes(build_template())
    s = Session(folder=folder, template=tpl, out_root=tmp_path / "out")
    s.load_spec(SPECS / "remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    assert len(s.preview.batch_plan.files) == 2
    before = tpl.read_bytes()
    out = s.run(in_place=True, render=False, log=lambda m: None)
    assert "2 file(s): PASS 2" in out and tpl.read_bytes() == before


def test_generated_run_refuses_unpreviewed_files(folder, tmp_path):
    """Codex review on PR #4: an AI-written transform runs only on the files it was tested on."""
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(folder=folder, out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported})),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))))
    assert "passed verification" in s.compile("x", [], True, lambda m: None)
    (folder / "21 99 00 – NEW.docx").write_bytes(build("21 99 00", "NEW"))
    with pytest.raises(ValueError, match="since the AI-written transform was tested"):
        s.run(False, False, lambda m: None)
