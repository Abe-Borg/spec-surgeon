"""Regenerate the README screenshots (docs/gui-files.png — the first step, docs/gui.png — Preview,
docs/gui-results.png — Run & results) from the real window, offscreen, on synthetic fire-protection specs. No API key
needed (it uses the example spec).

    python packaging/gui_screenshot.py            (needs: pip install -e ".[gui,dev]")
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
        from spec_surgeon.gui import Session, build_window
        from spec_surgeon.settings import Settings
        from spec_surgeon.ui.app import RecordingPrompts
        s = Session(out_root=work / "Results")
        s.load_spec(ROOT / "examples" / "specs" / "remove-all-headers-footers.json")
        suffix = os.environ.get("SPEC_SURGEON_SCREENSHOT_SUFFIX", "")
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
