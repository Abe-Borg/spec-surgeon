"""Capture the default conversation UI using synthetic documents and a scripted model.

    python packaging/gui_screenshot.py            # start and preview; no API calls
    python packaging/gui_screenshot.py --render   # include real LibreOffice/Word page previews
    python packaging/gui_screenshot.py --chat     # a follow-up question
    SPEC_SURGEON_THEME=dark SPEC_SURGEON_SCREENSHOT_SUFFIX=-dark python packaging/gui_screenshot.py

The screenshots show the app's own theme as Linux renders it; fonts differ slightly from Windows.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = [("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"),
         ("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")]
CHAT_ASK = {
    "understanding": "Change the specification text to Arial 10 pt.",
    "message": "The titles, headers and footers use their own styles. Should those change too, or only the body text?",
    "questions": ["Only the body text, or the headers, footers and titles too?"],
    "choices": [
        {"label": "Only the body text; keep the titles, headers and footers as they are", "kind": "clarify"},
        {"label": "Everything, including titles, headers and footers", "kind": "clarify"},
    ]}


def capture(window, name, suffix):
    app = window._spec_surgeon["app"]
    end = time.monotonic() + 0.5
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    path = ROOT / "docs" / f"conversation-{name}{suffix}.png"
    assert window.grab().save(str(path))
    print(f"wrote {path.relative_to(ROOT)}")


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("SPEC_SURGEON_THEME", "light")
    os.environ["SPEC_SURGEON_NO_UPDATE_CHECK"] = "1"
    sys.path.insert(0, str(ROOT / "tests"))
    from fake_claude import FakeClient, resp, tool
    from make_fixtures import build
    from qt_support import close_windows
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.settings import Settings
    from spec_surgeon.ui.app import RecordingPrompts

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        os.environ["SPEC_SURGEON_CONFIG_DIR"] = str(work / "config")
        sources = []
        for number, title in SPECS:
            path = work / f"{number} – {title}.docx"
            path.write_bytes(build(number, title))
            sources.append(path)
        spec = json.loads((ROOT / "examples/specs/remove-all-headers-footers.json").read_text())
        chat = "--chat" in sys.argv[1:]
        response = tool("ask_user", CHAT_ASK) if chat else tool("submit_editspec", {"spec": spec})
        session = Session(out_root=work / "Results", client=FakeClient(resp(response)), visual_check=False)
        window = build_window(session, settings=Settings(), prompts=RecordingPrompts())
        window.task.render = "--render" in sys.argv[1:]
        window.resize(1280, 900)
        window.show()
        suffix = os.environ.get("SPEC_SURGEON_SCREENSHOT_SUFFIX", "")
        try:
            capture(window, "start", suffix)
            window.add_documents([str(p) for p in sources])
            window.composer.setPlainText("Change the font to Arial 10 pt." if chat else spec["request"])
            window.do_send()
            window._spec_surgeon["wait"](120)
            assert window.task.stage == ("question" if chat else "preview"), window.notice.text()
            capture(window, "chat" if chat else "preview", suffix)
        finally:
            close_windows(window._spec_surgeon["app"], [window])
    return 0


if __name__ == "__main__":
    sys.exit(main())
