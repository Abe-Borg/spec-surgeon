"""Exercise the real installed GUI, process pool, resources, and isolated subprocess without an API key."""
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback


def main(request_path):
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    report = Path(request["report"])
    root = None
    result = {"ok": False}
    try:
        import anthropic
        import pymupdf
        import pythoncom
        import win32com.client
        from spec_surgeon import __version__, catalog
        from spec_surgeon.agent import SKILL_DIR
        from spec_surgeon.codegen import _examples, trial
        from spec_surgeon.gui import Session, build_app
        from spec_surgeon.verify.xsd import SCHEMA_DIR

        assert getattr(sys, "frozen", False), "Test must run from the installed executable"
        assert len(list(SCHEMA_DIR.rglob("*.xsd"))) == 39
        assert (SKILL_DIR / "SKILL.md").is_file()
        assert "set_page_setup.py" in _examples()
        examples = catalog.worked_examples()
        assert any(e["request"] == "remove all headers and footers" for e in examples)

        inputs = Path(request["input_dir"])
        sources = sorted(inputs.glob("*.docx"))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        session = Session(out_root=Path(request["output_dir"]))
        session.load_spec(catalog.EXAMPLES_DIR / "remove-all-headers-footers.json")
        root = build_app(session=session)
        ui = root._spec_surgeon
        ui["vars"]["folder"].set(str(inputs))
        ui["vars"]["render"].set(False)

        def click(name):
            ui["buttons"][name].invoke()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                root.update()
                state = ui["status"].get()
                if state in ("Ready.", "Error — see the log."):
                    log = ui["log"].get("1.0", "end")
                    assert state == "Ready.", log
                    return log
                time.sleep(0.05)
            raise TimeoutError(f"GUI {name} timed out")

        assert "2 plan cleanly" in click("preview")
        assert "PASS 2" in click("run")
        assert len(list((session.last_run_dir / "outputs").glob("*.docx"))) == 2
        record_files = list((session.last_run_dir / "records").glob("*.json"))
        assert len(record_files) == 2
        for path in record_files:
            record = json.loads(path.read_text(encoding="utf-8"))
            assert not any(c["status"] == "FAIL" for c in record["checks"]), record
            assert any(c["id"] == "V5a" and c["status"] == "PASS" for c in record["checks"]), record

        generated = trial(request["generated_source"], sources[0], {}, {"twips": 900},
                          Path(request["output_dir"]) / "Isolated generated operation")
        assert generated.passed, generated.problems
        assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
        result = {"ok": True, "version": __version__, "gui_preview_run": "PASS 2",
                  "process_pool": "PASS", "isolated_generated_op": "PASS",
                  "schemas": 39, "sources_unchanged": True,
                  "anthropic": anthropic.__version__, "pymupdf": pymupdf.VersionBind,
                  "word_modules": True}
    except Exception:
        result["error"] = traceback.format_exc()
    finally:
        if root is not None:
            root.destroy()
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
