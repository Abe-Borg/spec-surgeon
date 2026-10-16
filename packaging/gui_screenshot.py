"""Regenerate the README screenshots (docs/gui-files.png — the first step, docs/gui.png — Preview,
docs/gui-results.png — Run & results) from the real window, offscreen, on synthetic fire-protection specs. No API key
needed (it uses the example spec).

    python packaging/gui_screenshot.py            (needs: pip install -e ".[gui,dev]")
    python packaging/gui_screenshot.py --chat     docs/gui-chat.png — the conversation with Claude on the Describe
                                                  step, from a scripted client (tests/fake_claude.py; no network)
    python packaging/gui_screenshot.py --keyless  docs/gui-keyless.png — the step "Choose the change" as it reads
                                                  without an API key (keyless mode)
    SPEC_SURGEON_THEME=dark SPEC_SURGEON_SCREENSHOT_SUFFIX=-dark python packaging/gui_screenshot.py   (the dark look)

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


def chat_screenshot(work: Path, folder: Path, suffix: str = "") -> None:
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.settings import Settings
    from spec_surgeon.ui.app import RecordingPrompts
    s = Session(out_root=work / "Results",
                client=FakeClient(resp(tool("ask_user", CHAT_ASK, "a1"), inp=18400, out=950)))
    w = build_window(s, settings=Settings(), prompts=RecordingPrompts())   # the look: SPEC_SURGEON_THEME (main)
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
    w.grab().save(str(ROOT / "docs" / f"gui-chat{suffix}.png"))
    w.force_close = True
    w.close()


def keyless_screenshot(work: Path, folder: Path, suffix: str = "") -> None:
    """Step 2 without an API key: the saved specs and Your library first, what a key adds, the rail's note."""
    from spec_surgeon.gui import Session, build_window
    from spec_surgeon.settings import Settings
    from spec_surgeon.ui.app import RecordingPrompts
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        os.environ.pop(var, None)
    s = Session(out_root=work / "Results")
    w = build_window(s, settings=Settings(), prompts=RecordingPrompts())
    if w.claude_ok:
        raise SystemExit("an Anthropic sign-in was found on this machine (ant auth login): the keyless screenshot "
                         "needs a machine without one")
    ui = w._spec_surgeon
    ui["vars"]["folder"].set(str(folder))
    ui["vars"]["render"].set(False)
    w.resize(1280, 840)
    w.show()
    w.go(1)
    settle(ui["app"])
    w.grab().save(str(ROOT / "docs" / f"gui-keyless{suffix}.png"))
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
        os.environ.setdefault("SPEC_SURGEON_THEME", "light")          # SPEC_SURGEON_THEME=dark for the dark look
        folder = work / "Project" / "Specifications"
        folder.mkdir(parents=True)
        for number, title in SPECS:
            (folder / f"{number} – {title}.docx").write_bytes(build(number, title))
        suffix = os.environ.get("SPEC_SURGEON_SCREENSHOT_SUFFIX", "")
        if "--keyless" in sys.argv[1:]:
            keyless_screenshot(work, folder, suffix)
            print(f"wrote docs/gui-keyless{suffix}.png")
            return 0
        if "--chat" in sys.argv[1:]:
            chat_screenshot(work, folder, suffix)
            print(f"wrote docs/gui-chat{suffix}.png")
            return 0
        from spec_surgeon.gui import Session, build_window
        from spec_surgeon.settings import Settings
        from spec_surgeon.ui.app import RecordingPrompts
        # the window as it reads with a key (keyless mode has its own screenshot, --keyless); never used — a saved spec
        # with Render pages off calls nothing
        os.environ["ANTHROPIC_API_KEY"] = "screenshot-not-a-real-key"
        s = Session(out_root=work / "Results")
        s.load_spec(ROOT / "examples" / "specs" / "remove-all-headers-footers.json")
        w = build_window(s, settings=Settings(), prompts=RecordingPrompts())
        ui = w._spec_surgeon
        w.resize(1280, 840)
        w.show()
        ui["wait"](30)
        w.grab().save(str(ROOT / "docs" / f"gui-files{suffix}.png"))
        ui["vars"]["folder"].set(str(folder))
        ui["vars"]["render"].set(False)
        ui["buttons"]["preview"].click()
        ui["wait"](120)
        w.grab().save(str(ROOT / "docs" / f"gui{suffix}.png"))
        ui["buttons"]["run"].click()
        ui["wait"](300)
        w.grab().save(str(ROOT / "docs" / f"gui-results{suffix}.png"))
        w.force_close = True
        w.close()
    print(f"wrote docs/gui-files{suffix}.png, docs/gui{suffix}.png and docs/gui-results{suffix}.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
