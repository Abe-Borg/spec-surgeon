"""Open the window on synthetic documents with a scripted model, no API key and no network.

    python tools/demo_window.py                 # builds four fixtures, scripts one plan, serves the page, opens it
    python tools/demo_window.py --scenario question --no-open

Scenarios: ``plan`` (a font change compiled at once), ``question`` (one clarification with choices and chips, then
the plan), ``template`` (font + the architect's header/footer in one request: composed into two ordered steps). The
scripted replies are the same shape the real model returns, played back as a live stream, so the page shows the
typing, the activity lines and the plan the way it would with Claude. Everything else — triage, planning, previews
with LibreOffice or Word when installed, verification, copies — is the real engine.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import webbrowser
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))


def scripted(scenario: str, specs_dir: Path):
    from fake_claude import FakeClient, resp, text, tool
    from spec_surgeon.editspec import load_spec
    font = load_spec(specs_dir / "match-template-spec-font.json")
    hf = load_spec(specs_dir / "match-template-spec-header-footer.json")
    arial = {"request": "Change the font to Arial 10 pt.", "summary": "Arial 10 pt everywhere", "mode": "formatting",
             "assumptions": ["Titles, headers and footers change with the body text.",
                             "Symbol fonts (Wingdings, Symbol) keep their font so list bullets stay as they are."],
             "ops": font["ops"], "assertions": font.get("assertions", [])}
    plan = resp(text("I'll set the body text, the titles and the headers and footers of both documents to Arial 10 pt, "
                     "and leave every list bullet in its own symbol font."),
                tool("submit_editspec", {"spec": arial, "suggested_replies": [
                    "Also make the article titles bold", "Keep the headers as they are", "Match the template's header too"]}))
    if scenario == "question":
        ask = resp(text("Before I plan this: the titles, headers and footers use their own styles."),
                   tool("ask_user", {"message": "Should those change too, or only the body text? I'd change everything, "
                                                 "so the documents read as one.",
                                     "questions": ["Only the body text, or the headers, footers and titles too?"],
                                     "choices": [{"label": "Everything, including titles, headers and footers",
                                                  "kind": "clarify"},
                                                 {"label": "Only the body text; keep the titles, headers and footers",
                                                  "kind": "clarify"}],
                                     "suggested_replies": ["Everything", "Just the body text"]}))
        return FakeClient(ask, plan, plan, live=True)
    if scenario == "template":
        combined = {"request": "Match the template's header and footer, and its font.",
                    "summary": "The template's header, footer and font", "mode": "formatting",
                    "assumptions": ["The template's body text is Arial 10 pt."],
                    "ops": font["ops"] + hf["ops"], "assertions": font.get("assertions", [])}
        both = resp(text("I'll give both documents the template's header and footer, with each document's own section "
                         "number and title filled in, and set the text to the template's Arial 10 pt."),
                    tool("submit_editspec", {"spec": combined, "suggested_replies": ["Also make the article titles bold"]}))
        return FakeClient(both, both, live=True)
    return FakeClient(plan, plan, plan, live=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenario", choices=("plan", "question", "template"), default="plan")
    ap.add_argument("--no-open", action="store_true", help="print the URL instead of opening a browser")
    ap.add_argument("--no-render", action="store_true", help="skip page rendering (no LibreOffice or Word needed)")
    ap.add_argument("--folder", help="use this folder for the fixtures and the copies (default: a temp folder)")
    args = ap.parse_args(argv)
    from make_fixtures import build_sized
    from spec_surgeon.document_task import DocumentTask
    from spec_surgeon.gui import Session
    from spec_surgeon.settings import Settings
    from spec_surgeon.web.server import WindowState, new_token
    from spec_surgeon.web.shell import free_port, serve
    folder = Path(args.folder) if args.folder else Path(tempfile.mkdtemp(prefix="spec-surgeon-demo-"))
    docs = folder / "specs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build_sized())
    (docs / "21 13 16 – DRY-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build_sized("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    template = ROOT / "examples" / "templates" / "08 71 00 - DOOR HARDWARE (ACME).docx"
    session = Session(client=scripted(args.scenario, ROOT / "examples" / "specs"), out_root=folder / "runs",
                      visual_check=False)
    task = DocumentTask(session, render=not args.no_render, journal=folder / "conversation.json")
    task.attach([str(p) for p in sorted(docs.glob("*.docx"))])
    if args.scenario == "template" and template.is_file():
        task.attach([str(template)], template=True)
    state = WindowState(task, Settings(out_root=str(folder / "runs")), token=new_token())
    port = free_port()
    server = serve(state, port)
    url = f"http://127.0.0.1:{port}/?token={state.token}"
    print(json.dumps({"url": url, "folder": str(folder), "token": state.token, "port": port}), flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        import time
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True
    return 0


if __name__ == "__main__":
    sys.exit(main())
