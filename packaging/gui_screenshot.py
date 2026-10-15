"""Regenerate the README screenshots (docs/gui.png — Preview, docs/gui-results.png — Run & results) from the real
window, offscreen, on synthetic fire-protection specs. No API key needed (it uses the example spec).

    python packaging/gui_screenshot.py            (needs: pip install -e ".[gui,dev]")
    python packaging/gui_screenshot.py --chat     docs/gui-chat.png — the conversation with Claude on the Describe
                                                  step, from a scripted client (tests/fake_claude.py; no network)

The screenshots show the app's own theme as Linux renders it; fonts differ slightly from Windows (Segoe UI).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPECS = [("21 05 00", "COMMON WORK RESULTS FOR FIRE SUPPRESSION"), ("21 10 00", "WATER-BASED FIRE-SUPPRESSION SYSTEMS"),
         ("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"), ("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS")]


CHAT_ASK = {
    "understanding": "Change the font of the specification text to Arial 10 pt in all four 21-series specs.",
    "message": "The cover's title lines and the headers/footers use their own styles (Title, Header, Footer) at "
               "other sizes. Should those change too, or only the body text?",
    "questions": ["Only the body text, or the headers, footers and title lines too?"],
    "choices": [
        {"label": "Only the body text (paragraph styles Normal, PR1–PR3); headers, footers and titles keep their font "
                  "and size", "kind": "clarify"},
        {"label": "Everything: body, headers, footers and titles — all Arial, sizes unchanged except 9 pt text → 10 pt",
         "kind": "clarify"},
        {"label": "Change only the font, keep every size as it is", "kind": "alternative",
         "detail": "Differs from your request: 9 pt text stays 9 pt."},
    ]}


def settle(app, seconds: float = 0.5) -> None:
    """Let Qt lay out the widgets a job's result just added (posted layout requests) before grabbing the window."""
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)


def chat_screenshot(work: Path, folder: Path) -> None:
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.settings import Settings
    from spec_surgeon.ui.app import RecordingPrompts
    s = Session(out_root=work / "Results",
                client=FakeClient(resp(tool("ask_user", CHAT_ASK, "a1"), inp=18400, out=950)))
    w = build_window(s, settings=Settings(theme="light"), prompts=RecordingPrompts())
    ui = w._spec_surgeon
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    ui["request"].set("change the font in the specs to Arial 10")
    w.resize(1280, 900)
    w.show()
    ui["buttons"]["preview"].click()
    ui["wait"](120)
    ui["reply"].set("headers and footers too, but leave the cover title alone")
    settle(ui["app"])
    w.grab().save(str(ROOT / "docs" / "gui-chat.png"))
    w.force_close = True
    w.close()


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "tests"))
    from make_fixtures import build
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        os.environ["SPEC_SURGEON_CONFIG_DIR"] = str(work / "config")
        os.environ["SPEC_SURGEON_NO_UPDATE_CHECK"] = "1"
        folder = work / "Project" / "Specifications"
        folder.mkdir(parents=True)
        for number, title in SPECS:
            (folder / f"{number} – {title}.docx").write_bytes(build(number, title))
        if "--chat" in sys.argv[1:]:
            chat_screenshot(work, folder)
            print("wrote docs/gui-chat.png")
            return 0
        from spec_surgeon.gui import Session, build_window
        from spec_surgeon.settings import Settings
        from spec_surgeon.ui.app import RecordingPrompts
        s = Session(out_root=work / "Results")
        s.load_spec(ROOT / "examples" / "specs" / "remove-all-headers-footers.json")
        w = build_window(s, settings=Settings(theme="light"), prompts=RecordingPrompts())
        ui = w._spec_surgeon
        ui["vars"]["folder"].set(str(folder))
        ui["vars"]["render"].set(False)
        w.resize(1280, 840)
        w.show()
        ui["buttons"]["preview"].click()
        ui["wait"](120)
        w.grab().save(str(ROOT / "docs" / "gui.png"))
        ui["buttons"]["run"].click()
        ui["wait"](300)
        w.grab().save(str(ROOT / "docs" / "gui-results.png"))
        w.force_close = True
        w.close()
    print("wrote docs/gui.png and docs/gui-results.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
