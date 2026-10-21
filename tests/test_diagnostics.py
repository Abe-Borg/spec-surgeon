"""Diagnostics: when something fails, the run says where it stopped, why, and how it got there.

Pins: failure classification (a refusal, an unreadable file and what its bytes are, an I/O problem, the engine's own
failure — never "a corrupt file" for a bug), the line of code a refusal or failure came from, the per-file trace and
the run log, a worker process that dies (its file named, the others unharmed), what the renderer said, a comparison
or a batch that fails, the verification record's specifics (which paragraph, which characters), DIAGNOSTICS.md, the
preview's log (and a page comparison that fails: a warning, never a lost preview) and the approval refusal that says
what changed (for a spec and for a custom transform), the compiler's transcript and API errors (an API error is a failed
turn, a defect propagates with what was said kept), the window session's folders and the records beside each preview,
the Tier B sandbox's traceback and the crash watch."""
from __future__ import annotations

import ast
import faulthandler
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from make_fixtures import build
from spec_surgeon import batch as batch_mod
from spec_surgeon import diagnostics as diag
from spec_surgeon.batch import run_batch
from spec_surgeon.editspec import load_spec
from spec_surgeon.report import FAILURE_EXPLANATIONS, explain_failure
from spec_surgeon.verify.explain import failure_details, text_change, visible

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
AUTHOR = "Abraham Borg"
REPLACE = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
           "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                    "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
MISSING = {"request": "replace NOT THERE with GRC", "summary": "s", "mode": "content",
           "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                    "params": {"find": "NOT THERE AT ALL", "replace": "GRC", "author": AUTHOR}}]}
_REAL_PROCESS_ONE = batch_mod._process_one


def _folder(tmp_path, names=("A", "B", "C")) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    for i, n in enumerate(names):
        (d / f"21 0{i} 00 – {n}.docx").write_bytes(build(f"21 0{i} 00", n))
    return d


def _stages(trace_lines):
    return [m.group(1) for m in (diag._TRACE_LINE.match(x) for x in trace_lines) if m]


# ---- what the bytes are; what kind of failure -----------------------------------------------------------------------

@pytest.mark.parametrize("data, says", [
    (b"", "empty (0 bytes)"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 32, "password-protected (encrypted)"),
    (b"%PDF-1.7\n", "PDF file"),
    (b"{\\rtf1\\ansi", "RTF file"),
    (b"PK\x03\x04 not really a zip", "damaged or cut short"),
    (b"<html><body>Sign in</body></html>", "HTML page"),
    (b"\0" * 64, "only zero bytes"),
    (b"plain words, not a document", "plain text file"),
    (bytes(range(200, 256)), "not a Word file"),
])
def test_unreadable_files_say_what_they_are(tmp_path, data, says):
    f = tmp_path / "X.docx"
    f.write_bytes(data)
    b = run_batch([str(f)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False, spec=REPLACE)
    exc = b.files[0].exception
    assert exc["code"] == "unreadable" and exc["kind"] == "unreadable" and exc["stage"] == "open"
    assert says in exc["reason"], exc["reason"]
    assert explain_failure("unreadable", "")[0] == "Not a Readable Word Document"


def test_a_malformed_part_is_named(tmp_path):
    import io
    src = build("21 00 01", "ALPHA")
    zin, out = zipfile.ZipFile(io.BytesIO(src)), io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "word/document.xml":
                data = data[:-40]                              # cut short: not well-formed
            zout.writestr(info, data)
    f = tmp_path / "CUT.docx"
    f.write_bytes(out.getvalue())
    b = run_batch([str(f)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False, spec=REPLACE)
    exc = b.files[0].exception
    assert exc["code"] == "unreadable" and "word/document.xml" in exc["reason"] and "well-formed" in exc["reason"]
    assert exc["details"]["malformed_parts"][0].startswith("word/document.xml")


def test_os_errors_are_unreadable_at_acquire_and_io_errors_later():
    gone = FileNotFoundError(2, "No such file or directory", "C:/x.docx")
    c = diag.classify(gone, "acquire")
    assert c["code"] == "unreadable" and "gone" in c["reason"] and c["hint"]
    c = diag.classify(PermissionError(13, "Permission denied", "out/outputs/x.docx"), "write")
    assert c["code"] == "io-error" and c["kind"] == "environment" and "permission denied" in c["reason"]
    cloud = OSError(22, "The cloud file provider is not running")
    assert "OneDrive" in diag.classify(cloud, "acquire")["reason"]
    long_path = OSError(22, "The filename or extension is too long")
    long_path.winerror = 206
    assert "too long for Windows" in diag.os_error_hint(long_path)
    assert diag.classify(MemoryError(), "verify")["code"] == "internal-error"
    assert diag.classify(KeyError("w:val"), "apply")["code"] == "internal-error"
    assert diag.classify(KeyError("no member 'word/header9.xml' in x"), "plan")["code"] == "unreadable"


def test_a_refusal_says_the_op_and_the_line_of_code(tmp_path):
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    b = run_batch([str(f)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False, spec=MISSING)
    exc = b.files[0].exception
    assert exc["kind"] == "refusal" and exc["code"] == "not-found" and exc["stage"] == "plan"
    assert exc["op_number"] == 1 and exc["op"] == "replace_text"
    assert exc["where"].startswith("ops/replace_text.py:") and "traceback" not in exc       # a refusal is not a bug
    assert exc["trace"] == "logs/21 00 00 – A.trace.log"
    trace = (tmp_path / "out" / exc["trace"]).read_text(encoding="utf-8")
    assert "REFUSED [not-found]" in trace and "at ops/replace_text.py:" in trace


def test_an_engine_failure_is_never_called_a_corrupt_file(tmp_path, monkeypatch):
    from spec_surgeon.ops.replace_text import ReplaceText

    def broken_apply(self, work, frag):
        raise KeyError("w:val")
    monkeypatch.setattr(ReplaceText, "apply", broken_apply)
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    out = tmp_path / "out"
    b = run_batch([str(f)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=REPLACE)
    exc = b.files[0].exception
    assert exc["code"] == "internal-error" and exc["kind"] == "internal-error" and exc["stage"] == "apply"
    assert exc["op_number"] == 1 and exc["op"] == "replace_text" and "KeyError: 'w:val'" in exc["traceback"]
    assert "defect in the program" in exc["reason"] and "Corrupt" not in explain_failure("internal-error", "")[0]
    diagnostics = (out / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert "Spec Surgeon itself failed on 1 file(s)" in diagnostics and "**Traceback**" in diagnostics
    assert "applying the edit to a copy (`apply`), op #1 `replace_text`" in diagnostics
    assert not list((out / "outputs").iterdir())


# ---- every refusal an operation can raise reads in plain words --------------------------------------------------------

OPS_DIR = Path(__file__).resolve().parent.parent / "src" / "spec_surgeon" / "ops"
# codes that fall back to the generic "Refused for Safety" wording today; no new code may join them
GENERIC_EXPLANATION = {"binding-source", "cardinality", "comment-overlap", "shared-part", "theme-font",
                       "unsupported-comment-anchor"}


def _refusal_codes():
    """Every code literal an operation module refuses with — ``JobException(reason, code)`` and
    ``resolve_scheme(..., code)`` — and the modules where a code is computed instead."""
    codes, computed = set(), []
    for py in sorted(OPS_DIR.glob("*.py")):
        for n in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not isinstance(n, ast.Call):
                continue
            called = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            at = {"JobException": 1, "resolve_scheme": 4}.get(called)
            if at is None:
                continue
            code = n.args[at] if len(n.args) > at else next((k.value for k in n.keywords if k.arg == "code"), None)
            if isinstance(code, ast.Constant):
                codes.add(code.value)
            else:
                computed.append(py.stem)
    return codes, sorted(set(computed))


def test_every_refusal_code_of_every_operation_is_explained_and_only_spec_level_ones_are_fatal():
    from spec_surgeon import compiler
    from spec_surgeon.ops import fields
    codes, computed = _refusal_codes()
    # computed codes: end_of_section picks one of two literals; links raises a field's refusal (fields.REFUSAL_TEXT);
    # insert_table_rows and table_edit check cell texts with the caller's code (bad-params for typed rows, table-value
    # for a table file's — both literals scanned); the others re-raise the code of a refusal read above
    assert computed == ["end_of_section", "insert_table_rows", "links", "lists", "set_properties", "set_tab_stops",
                        "style_import", "table_edit"]
    codes |= set(fields.REFUSAL_TEXT) | {"name-collision"}            # batch.planned_names
    codes.discard("internal")                                         # diagnostics.classify: an internal error
    assert {"table-value", "spans-structure", "nested-options", "bad-name", "image-changed", "eos-not-last",
            "revision-kind", "paragraph-merge", "partial-move", "marks-in-change", "watermark-exists", "no-header",
            "watermark-size", "field-span", "nested-field", "form-field", "no-part", "protected", "property-type",
            "malformed-properties", "tracked-format", "toggle-ambiguous", "field-result", "style-mismatch",
            "style-import", "style-numbering", "missing-style", "conflict-property", "scheme", "scheme-depth",
            "numbering-mismatch", "numbering-unsupported", "typed-format", "ambiguous", "tab-position",
            "table-structure", "textbox"} <= codes                    # the scan reads every operation
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == sorted(GENERIC_EXPLANATION)
    # a refusal one file earns never rejects the whole spec at compile time
    assert codes & compiler.FATAL_SPEC_CODES == {"bad-params", "conflict", "slot-overlap", "template-slot",
                                                  "unknown-slot"}


def test_no_report_sends_the_reader_to_a_command_or_the_removed_diagnostics_bundle():
    import inspect
    from spec_surgeon import report
    text = inspect.getsource(report).lower()
    assert "bundle" not in text and "spec-surgeon " not in text
    assert "about → open diagnostic logs" in FAILURE_EXPLANATIONS["internal-error"][1].lower()


# ---- the trace, the run log, DIAGNOSTICS.md -------------------------------------------------------------------------

def test_every_file_leaves_a_trace_and_the_run_a_log(tmp_path):
    d = _folder(tmp_path, ["A", "B"])
    out = tmp_path / "out"
    progress = []
    b = run_batch([str(d)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=REPLACE,
                  progress=progress.append)
    fr = b.files[0]
    assert fr.status == "PASS" and fr.trace_path == out / "logs" / "21 00 00 – A.trace.log"
    lines = fr.trace_path.read_text(encoding="utf-8").splitlines()
    assert lines == fr.trace
    assert _stages(lines) == ["acquire", "acquire", "open", "open", "fingerprint", "fingerprint", "plan", "plan",
                              "plan", "apply", "apply", "apply", "verify", "verify", "write", "render", "deliver",
                              "result"]
    text = "\n".join(lines)
    assert "op #1 replace_text: text: 4 block(s) change in kept parts (document.xml 4)" in text
    assert "op #1 replace_text: done [" in text and "verify      PASS — V1 PASS" in text
    assert text.endswith(f"PASS — delivered to {out / 'outputs' / '21 00 00 – A.docx'}")
    run_log = (out / "logs" / "run.log").read_text(encoding="utf-8")
    assert "Spec Surgeon" in run_log and "environment  Spec Surgeon" in run_log and "lxml" in run_log
    assert "  done 21 00 00 – A.docx" in run_log and "      | " + lines[7] in run_log      # the trace, in the log
    assert "21 00 00 – A.docx: PASS — delivered to" in run_log and "finished: PASS 2" in run_log
    assert progress[1:] == ["  done 21 00 00 – A.docx", "  done 21 01 00 – B.docx"]        # the progress lines kept
    report = json.loads((out / "batch.json").read_text(encoding="utf-8"))
    assert report["environment"]["spec_surgeon"] and report["log"] == "logs/run.log"
    assert report["files"][0]["trace"] == "logs/21 00 00 – A.trace.log"
    diagnostics = (out / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert "No problems: every file passed every check." in diagnostics and "## Timing" in diagnostics
    assert "DIAGNOSTICS.md" in (out / "BATCH_REPORT.md").read_text(encoding="utf-8")


def test_a_problem_shows_in_the_progress_lines_as_it_happens(tmp_path):
    d = _folder(tmp_path, ["A"])
    (d / "21 99 00 – BROKEN.docx").write_bytes(b"%PDF-1.4")
    progress = []
    run_batch([str(d)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False, spec=MISSING,
              progress=progress.append)
    assert any(x.startswith("    EXCEPTION [not-found]") and "(at" not in x for x in progress)   # a refusal: no stage
    assert any(x.startswith("    EXCEPTION [unreadable]") and x.endswith("(at open)") for x in progress)


# a module-level worker (picklable by reference) that kills its own process on one file
def _dies_on_b(path, *a, **k):
    if Path(path).name.startswith("21 01 00"):
        os._exit(7)
    return _REAL_PROCESS_ONE(path, *a, **k)


def test_a_worker_that_dies_costs_only_its_own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_mod, "_process_one", _dies_on_b)
    d = _folder(tmp_path)
    out = tmp_path / "out"
    progress = []
    b = run_batch([str(d)], "edit-spec", {}, out, workers=2, worker_mode="process", renderer="none", do_render=False, spec=REPLACE,
                  progress=progress.append)
    status = {f.source.name: f.status for f in b.files}
    assert status["21 01 00 – B.docx"] == "EXCEPTION"
    assert status["21 00 00 – A.docx"] == status["21 02 00 – C.docx"] == "PASS"
    exc = next(f for f in b.files if f.source.name.startswith("21 01 00")).exception
    assert exc["code"] == "worker-died" and exc["kind"] == "worker-died" and "ended abruptly" in exc["reason"]
    assert sorted(x for x in progress if x.startswith("  done ")) == sorted(f"  done {f.name}" for f in d.iterdir())
    assert any("running its" in x and "again, each alone" in x for x in progress)
    assert not (out / "outputs" / "21 01 00 – B.docx").exists()
    assert "WORKER DIED [worker-died]" in (out / "logs" / "21 01 00 – B.trace.log").read_text(encoding="utf-8")
    assert explain_failure("worker-died", "")[0] == "The Process Working on This File Stopped"


# ---- rendering: what the renderer said; a comparison that fails -------------------------------------------------------

def test_renderer_lines_are_attributed_by_name_not_by_substring():
    msgs = ["A.docx: LibreOffice could not render it: exit status 1", "BA.docx: no PDF came out of LibreOffice",
            "LibreOffice failed on a chunk of 2 file(s) (exit status 1); retrying them one by one: ['A.docx', 'C.docx']"]
    assert batch_mod._file_messages(msgs, ["A.docx"]) == [msgs[0], msgs[2]]
    assert batch_mod._file_messages(msgs, ["BA.docx"]) == [msgs[1]]


def test_libreoffice_failures_keep_what_it_printed():
    from spec_surgeon.render import libreoffice
    e = subprocess.CalledProcessError(81, ["soffice", "--convert-to", "pdf"] + ["x.docx"] * 40,
                                      output=b"convert x.docx -> x.pdf\n", stderr=b"Error: source file could not be loaded\n")
    text = libreoffice.failure_text(e)
    assert text.startswith("exit status 81") and "source file could not be loaded" in text and "--convert-to" not in text
    t = libreoffice.failure_text(subprocess.TimeoutExpired(["soffice"], 600, stderr=b"busy"))
    assert t.startswith("timed out after 600s") and "busy" in t


def test_a_failed_render_says_why_in_v6_and_the_trace(tmp_path, monkeypatch):
    from spec_surgeon.render import libreoffice

    def fails(soffice, files, out_dir, profile_dir, timeout):
        raise subprocess.CalledProcessError(1, [soffice], output=b"", stderr=b"javaldx: Could not find a Java Runtime")
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(libreoffice, "_convert_batch", fails)
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    out = tmp_path / "out"
    b = run_batch([str(f)], "edit-spec", {}, out, workers=1, renderer="libreoffice", do_render=True, spec=REPLACE)
    v6 = b.files[0].record.get("V6")
    assert v6.status == "FAIL" and "exit status 1" in v6.summary and "Java Runtime" in v6.summary
    assert any("Java Runtime" in m for m in v6.data["messages"])
    assert "V6 FAIL" in (out / "logs" / "21 00 00 – A.trace.log").read_text(encoding="utf-8")
    diagnostics = (out / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert "its pages could not be rendered or compared" in diagnostics and "Java Runtime" in diagnostics


def test_a_comparison_that_fails_is_this_files_v6_never_a_crashed_batch(tmp_path, monkeypatch):
    from spec_surgeon import render as render_mod

    def fake_render(files, out_dir, backend, workers=4, log=None, detail=None, cache=None):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        out = {}
        for f in files:
            pdf = Path(out_dir) / (Path(f).stem + ".pdf")
            pdf.write_bytes(b"%PDF-1.4 not really")
            out[Path(f)] = pdf
        return out
    monkeypatch.setattr(render_mod, "pick", lambda prefer="auto": "libreoffice")
    monkeypatch.setattr(render_mod, "render_pdfs", fake_render)
    monkeypatch.setattr(render_mod, "describe", lambda backend: "a fake renderer")
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    out = tmp_path / "out"
    b = run_batch([str(f)], "edit-spec", {}, out, workers=1, renderer="auto", do_render=True, spec=REPLACE)
    fr = b.files[0]
    assert fr.status == "FAIL" and fr.record.get("V6").status == "FAIL" and fr.record.get("V7").status == "NA"
    assert "could not be compared" in fr.record.get("V6").summary and "Traceback" in fr.record.get("V6").data["traceback"]
    assert "Traceback" in (out / "logs" / "21 00 00 – A.trace.log").read_text(encoding="utf-8")
    assert (out / "failed" / "21 00 00 – A.docx").is_file() and (out / "DIAGNOSTICS.md").is_file()


def test_a_batch_that_stops_says_where_in_its_log(tmp_path, monkeypatch):
    def boom(identity):
        raise RuntimeError("the guard itself broke")
    monkeypatch.setattr(batch_mod.guard, "drifted", boom)
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="the guard itself broke"):
        run_batch([str(f)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False, spec=REPLACE)
    log = (out / "logs" / "run.log").read_text(encoding="utf-8")
    assert "THE BATCH STOPPED during deliver: RuntimeError: the guard itself broke" in log and "Traceback" in log
    assert "The batch itself stopped" in (out / "DIAGNOSTICS.md").read_text(encoding="utf-8")


# ---- the record's specifics ---------------------------------------------------------------------------------------------

def test_text_change_shows_where_two_texts_part():
    assert text_change("the FM Global rules", "the GRC rules") == "the [-FM Global-]{+GRC+} rules"
    assert text_change("a\tb", "a b") == "a[-⇥-]{+ +}b" and visible("x\u00a0y") == "x⍽y"
    assert text_change(None, "new") == "(new block) {+new+}" and text_change("old", None) == "(block removed) [-old-]"
    long = "x" * 100 + "A" + "y" * 100
    assert text_change(long, long.replace("A", "B")).startswith("…") and "[-A-]{+B+}" in text_change(long, long.replace("A", "B"))


def test_v2_pairs_a_prediction_with_what_the_output_has():
    rec = SimpleNamespace(predicted_diffs=[{"part": "word/document.xml", "block_index": 7, "before": "use FM Global",
                                            "after": "use GRC"}])
    data = {"missing": [["word/document.xml", "use FM Global", "use GRC"]],
            "unpredicted": [["word/document.xml", "use FM Global", "use GRX"], ["word/footer1.xml", "a", "b"]],
            "actual": [{"part": "word/footer1.xml", "before": "a", "after": "b", "before_index": 2, "after_index": 2}]}
    lines = failure_details("V2", data, "summary", rec)
    assert lines[0].startswith("word/document.xml block 7: the plan predicted use [-FM Global-]{+GRC+}")
    assert lines[0].endswith("output against the prediction: use GR[-C-]{+X+}")
    assert lines[1] == "word/footer1.xml block 2: a change the plan did not predict: [-a-]{+b+}"


def test_a_failed_record_says_which_paragraph(tmp_path, monkeypatch):
    """A font op that also turns a run bold (planted): V1 and V10 fail, and the record names the paragraph."""
    from lxml import etree
    from spec_surgeon.ops.set_font import SetFont
    from spec_surgeon.package import W
    real = SetFont.apply

    def bold_too(self, work, frag):
        real(self, work, frag)
        root = work.xml("word/document.xml")
        r = next(root.iter(W + "r"))
        rpr = r.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr")
            r.insert(0, rpr)
        etree.SubElement(rpr, W + "b")
        work.write_xml("word/document.xml", root)
    monkeypatch.setattr(SetFont, "apply", bold_too)
    f = _folder(tmp_path, ["A"]) / "21 00 00 – A.docx"
    out = tmp_path / "out"
    b = run_batch([str(f)], "edit-spec", {}, out, workers=1, renderer="none", do_render=False,
                  spec=load_spec(SPECS / "change-all-font-arial.json"))
    fr = b.files[0]
    assert fr.status == "FAIL"
    md = (out / "records" / "21 00 00 – A.record.md").read_text(encoding="utf-8")
    assert "WHY IT FAILED" in md and "word/document.xml changed, but the plan predicted no change to it" in md
    assert "(in paragraph “" in md
    trace = (out / "logs" / "21 00 00 – A.trace.log").read_text(encoding="utf-8")
    assert "verify      FAIL (V1, V10)" in trace and "(in paragraph “" in trace
    diagnostics = (out / "DIAGNOSTICS.md").read_text(encoding="utf-8")
    assert "Failed verification (not delivered)" in diagnostics and "**V10 Property assertions**" in diagnostics
    assert "| 1 | 21 00 00 – A.docx | FAIL V1, V10 | verify |" in diagnostics


def test_first_difference_names_the_paragraph_and_the_style():
    from lxml import etree
    from spec_surgeon.verify.props import first_difference
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    a = etree.fromstring(f'<w:body {ns}><w:p><w:r><w:t>Fire pumps</w:t></w:r></w:p></w:body>')
    b = etree.fromstring(f'<w:body {ns}><w:p><w:r><w:rPr><w:b/></w:rPr><w:t>Fire pumps</w:t></w:r></w:p></w:body>')
    assert first_difference(a, b).endswith("(in paragraph “Fire pumps”)")
    s1 = etree.fromstring(f'<w:styles {ns}><w:style w:styleId="Heading1"><w:name w:val="x"/></w:style></w:styles>')
    s2 = etree.fromstring(f'<w:styles {ns}><w:style w:styleId="Heading1"><w:name w:val="y"/></w:style></w:styles>')
    assert first_difference(s1, s2).endswith("(in style 'Heading1')")


# ---- preview: its log, a sample that fails, an approval that says what changed -------------------------------------------

def test_a_preview_keeps_a_log_and_survives_a_sample_that_fails(tmp_path, monkeypatch):
    from spec_surgeon import verify as verify_pkg
    from spec_surgeon.preview import make_preview

    def boom(*a, **k):
        raise ZeroDivisionError("planted")
    monkeypatch.setattr(verify_pkg, "run_suite", boom)
    d = _folder(tmp_path, ["A"])
    (d / "21 99 00 – BROKEN.docx").write_bytes(b"")
    pv = make_preview(REPLACE, [str(d)], tmp_path / "pv", do_render=False)
    sample = pv.samples[0]
    assert sample.exception["code"] == "internal-error" and sample.exception["stage"] == "verify"
    log = (tmp_path / "pv" / "logs" / "preview.log").read_text(encoding="utf-8")
    assert "ZeroDivisionError: planted" in log and "EXCEPTION [unreadable] at open" in log and "empty (0 bytes)" in log
    assert json.loads((tmp_path / "pv" / "preview.json").read_text(encoding="utf-8"))["approval_inputs"]["files"]


def test_a_refused_run_says_what_changed_since_the_preview(tmp_path):
    from spec_surgeon.gui import Session
    d = _folder(tmp_path, ["A", "B"])
    s = Session(documents=sorted(d.glob("*.docx")), out_root=tmp_path / "out")
    s._set_outcome(spec=load_spec(SPECS / "remove-all-headers-footers.json"), source="remove-all-headers-footers.json")
    s.make_preview(render=False, log=lambda m: None)
    a = d / "21 00 00 – A.docx"
    a.write_bytes(a.read_bytes() + b"\0")
    new = d / "21 05 00 – NEW.docx"
    new.write_bytes(build("21 05 00", "NEW"))
    s.documents.append(new)                                    # attached after the preview
    with pytest.raises(ValueError, match="changed since the preview") as e:
        s.run(False, False, lambda m: None)
    assert "21 00 00 – A.docx: modified since the preview (size" in str(e.value)
    assert "21 05 00 – NEW.docx: a file that was not in the preview" in str(e.value)


def test_a_refused_custom_transform_run_says_what_changed(tmp_path):
    from spec_surgeon.gui import Session
    from test_codegen import GOOD_OP
    from test_compiler import FakeClient, resp, tool, generate_reviewed_custom
    d = _folder(tmp_path, ["A", "B"])
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(documents=sorted(d.glob("*.docx")), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}}))))
    s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    assert "passed verification" in generate_reviewed_custom(s)
    s.make_preview(render=False, log=lambda m: None)
    a = d / "21 00 00 – A.docx"
    a.write_bytes(a.read_bytes() + b"\0")
    with pytest.raises(ValueError, match="since the AI-written transform was tested — press Preview again") as e:
        s.run(False, False, lambda m: None)
    assert "21 00 00 – A.docx: modified since the preview (size" in str(e.value)


def test_a_failing_page_comparison_is_a_warning_not_a_lost_preview(tmp_path, monkeypatch):
    """The before/after pictures are a convenience: a comparison that raises costs the pictures (a warning, its
    traceback in the preview log), never the sample, its PDFs, or the pages the visual check would look at."""
    from spec_surgeon import render as render_mod
    from spec_surgeon.preview import make_preview
    from spec_surgeon.verify import geometry as geom
    from test_vision import make_pdf
    d = _folder(tmp_path, ["A"])
    spec = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))

    def fake_render(files, out_dir, backend, workers=4, log=None, detail=None, cache=None):
        return {Path(f): make_pdf(Path(out_dir) / f"{Path(f).stem}.pdf") for f in files}

    def boom(*a, **k):
        raise RuntimeError("planted")
    monkeypatch.setattr(render_mod, "pick", lambda prefer="auto": "libreoffice")
    monkeypatch.setattr(render_mod, "describe", lambda backend: "fake renderer")
    monkeypatch.setattr(render_mod, "render_pdfs", fake_render)
    monkeypatch.setattr(geom, "compare_pdfs", boom)
    pv = make_preview(spec, [str(d)], tmp_path / "pv", do_render=True)
    s = pv.samples[0]
    assert s.passed and s.source_pdf and s.output_pdf and s.look_pages == [1] and not s.images
    assert any("the before/after pictures could not be made (RuntimeError: planted)" in w for w in pv.warnings)
    log = (tmp_path / "pv" / "logs" / "preview.log").read_text(encoding="utf-8")
    assert "planted" in log and "Traceback" in log and "renderer: fake renderer" in log


# ---- the model's exchange: transcripts and API errors --------------------------------------------------------------------

class _ApiError(Exception):
    """Stands in for the SDK's error (its module is the SDK's)."""
    __module__ = "anthropic._exceptions"

    def __init__(self):
        super().__init__("Overloaded")
        self.status_code, self.request_id = 529, "req_01ABC"


def test_the_compile_keeps_its_transcript_and_says_why_the_api_failed(tmp_path):
    from fake_claude import raise_on_enter, raise_on_final
    from spec_surgeon.compiler import compile_request
    from spec_surgeon.conversation import ACCEPTED_RESULT
    from test_compiler import FakeClient, resp, text, tool
    d = _folder(tmp_path, ["A"])
    files = sorted(d.glob("*.docx"))
    spec = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    res = compile_request("remove all headers and footers", files,
                          client=FakeClient(resp(text("Looking."), tool("submit_editspec", {"spec": spec}))))
    assert res.status == "ok" and res.transcript[0]["role"] == "user" and "REQUEST" in res.transcript[0]["content"]
    turn = res.transcript[1]
    assert turn["role"] == "assistant" and turn["stop_reason"] == "tool_use" and turn["usage"]["input_tokens"] == 100
    assert [b["type"] for b in turn["content"]] == ["text", "tool_use"] and turn["content"][1]["input"]["spec"] == spec
    assert res.transcript[2]["tool_results"][0]["content"] == ACCEPTED_RESULT and "transcript" in res.to_dict()
    for scripted in (raise_on_enter(_ApiError()), raise_on_final(_ApiError())):  # refused at once, or dropped mid-way
        failed = compile_request("remove all headers and footers", files, client=FakeClient(scripted))
        assert failed.status == "failed" and "HTTP 529" in failed.errors[0]
        assert "request id req_01ABC" in failed.errors[0]
        assert failed.transcript[-1]["error"].startswith("_ApiError: Overloaded")
    with pytest.raises(AssertionError):                     # a defect (a test script running out) is not an API error
        compile_request("x", files, client=FakeClient(raise_on_enter(AssertionError("scripted turns ran out"))))


def test_the_window_session_writes_the_compile_record_beside_its_preview(tmp_path):
    from spec_surgeon.gui import Session
    from test_compiler import FakeClient, resp, tool
    d = _folder(tmp_path, ["A"])
    spec = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    s = Session(documents=sorted(d.glob("*.docx")), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": spec}))))
    lines = []
    assert s.compile("remove all headers and footers", lines.append) == "Compiled."
    record = s.out_dir / "compile.json"
    assert record.is_file() and json.loads(record.read_text(encoding="utf-8"))["transcript"]
    assert s.chat_dir == s.out_dir and (s.out_dir / "conversation.json").is_file()     # the conversation beside it
    s.make_preview(render=False, log=lines.append)
    assert (record.parent / "preview" / "preview.json").is_file()            # the same folder: compile, then preview
    assert any("Compile record" in x for x in lines) and any("Preview log" in x for x in lines)


def test_the_window_session_records_a_failed_call_and_keeps_what_was_said_before_a_defect(tmp_path, monkeypatch):
    """An API error (or Claude not installed) is a failed turn the user can answer: its kind, HTTP status and request
    id in compile.json and the exchange in conversation.json. Anything else is a defect: it propagates, and the session
    folder still holds what was said up to it, for the diagnosis."""
    from fake_claude import raise_on_enter, raise_on_final
    from spec_surgeon import llm
    from spec_surgeon.gui import Session
    from test_compiler import FakeClient, resp, tool
    d = _folder(tmp_path, ["A"])
    s = Session(documents=sorted(d.glob("*.docx")), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("ask_user", {"questions": ["Which sections?"]})),
                                  raise_on_final(_ApiError()),
                                  raise_on_enter(AssertionError("a planted defect"))))

    def records():
        return (json.loads((s.out_dir / "compile.json").read_text(encoding="utf-8")),
                json.loads((s.out_dir / "conversation.json").read_text(encoding="utf-8")))
    assert "reply in the chat" in s.compile("remove some headers", lambda m: None)
    msg = s.reply("all of them")
    assert msg.startswith("That didn't work: the request to Claude failed: _ApiError: Overloaded") and "HTTP 529" in msg
    compiled, said = records()
    assert compiled["status"] == "failed" and "request id req_01ABC" in compiled["errors"][0]
    assert said["exchange"][-1]["error"].startswith("_ApiError: Overloaded") and s.conversation is not None
    with pytest.raises(AssertionError, match="a planted defect"):
        s.reply("every section, please")
    said = records()[1]
    assert [e["text"] for e in said["entries"] if e["role"] == "user"][-1] == "every section, please"

    def missing():
        raise llm.ClaudeUnavailable('install the Claude layer (pip install "spec-surgeon[agent]")')
    monkeypatch.setattr(llm, "client", missing)                 # Claude not installed: the user's setup, not a defect
    s.client = None
    msg = s.compile("remove some headers", lambda m: None)
    assert msg.startswith("That didn't work: Claude is not available: install the Claude layer")
    assert records()[0]["status"] == "failed"


def test_every_preview_of_a_conversation_has_the_records_that_led_to_it(tmp_path, monkeypatch):
    """The window's session folders: the compile folder takes the first preview or custom transform; once a preview
    used a folder, its records stay as they were (the spec in its compile.json is the one its preview and run used),
    and the conversation goes on in the next folder, which the next preview takes. A loaded spec never previews into a
    conversation's folder."""
    from datetime import datetime, timedelta
    from spec_surgeon import gui
    from spec_surgeon.gui import Session
    from test_codegen import GOOD_OP
    from test_compiler import FakeClient, resp, tool, generate_reviewed_custom

    class Clock:                                  # every folder a second apart (no same-second reuse in a fast test)
        t = 0

        @classmethod
        def now(cls):
            cls.t += 1
            return datetime(2026, 10, 15, 12, 0, 0) + timedelta(seconds=cls.t)
    monkeypatch.setattr(gui, "datetime", Clock)
    d = _folder(tmp_path, ["A", "B"])
    spec = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))
    again = dict(spec, summary="remove them again")
    unsupported = {"request": "r", "summary": "s", "unsupported": "no op", "proposal": "set_header_distance"}
    s = Session(documents=sorted(d.glob("*.docx")), out_root=tmp_path / "out",
                client=FakeClient(resp(tool("submit_editspec", {"spec": unsupported, "custom_transform": "possible"})),
                                  resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})),
                                  resp(tool("submit_editspec", {"spec": spec}, "s2")),
                                  resp(tool("submit_editspec", {"spec": again}, "s3"))))

    def records(folder):
        return (json.loads((folder / "compile.json").read_text(encoding="utf-8")),
                json.loads((folder / "conversation.json").read_text(encoding="utf-8")))

    def users(conversation):
        return sum(e["role"] == "user" for e in conversation["entries"])

    s.compile("Set every section's header distance to 900 twips.", lambda m: None)
    f1 = s.out_dir
    assert s.chat_dir == f1 and records(f1)[0]["status"] == "unsupported"
    assert "passed verification" in generate_reviewed_custom(s)
    assert s.out_dir == f1 and (f1 / "codegen" / "history.json").is_file() and (f1 / "preview").is_dir()
    assert s.reply("actually, just remove all headers and footers") == "Compiled."
    f2 = s.chat_dir
    assert f2 != f1 and s.out_dir == f1                    # the transform's folder keeps its records; out_dir its preview
    assert records(f1)[0]["status"] == "unsupported" and records(f2)[0]["status"] == "ok"
    s.make_preview(render=False, log=lambda m: None)
    assert s.out_dir == f2 and (f2 / "preview" / "spec.json").is_file()
    assert s.reply("again") == "Compiled."
    f3 = s.chat_dir
    s.make_preview(render=False, log=lambda m: None)
    assert s.out_dir == f3 and len({f1, f2, f3}) == 3
    for folder, which in ((f2, spec), (f3, again)):        # each folder: the spec its preview used, and only that
        compiled, _ = records(folder)
        assert compiled["spec"] == dict(which, request="Set every section's header distance to 900 twips.")
        assert compiled["spec"] == json.loads((folder / "preview" / "spec.json").read_text(encoding="utf-8"))
    assert users(records(f2)[1]) < users(records(f3)[1])
    s._set_outcome(spec=load_spec(SPECS / "remove-all-headers-footers.json"), source="remove-all-headers-footers.json")
    s.end_conversation()                                   # a loaded spec replaces the conversation
    s.make_preview(render=False, log=lambda m: None)
    assert s.out_dir not in (f1, f2, f3) and not (s.out_dir / "compile.json").exists()
    assert not (s.out_dir / "conversation.json").exists()


def test_the_sandbox_hands_back_the_generated_codes_traceback(tmp_path):
    from spec_surgeon.codegen import trial
    from test_codegen import GOOD_OP
    bad = GOOD_OP.replace("main = pkg.main_part()", "main = pkg.main_part()\n        undefined_name_here")
    f = tmp_path / "A.docx"
    f.write_bytes(build("21 00 01", "ALPHA"))
    t = trial(bad, f, {}, {"twips": 500}, tmp_path / "sb")
    assert not t.passed and "NameError" in t.problems[0] and "(traceback, last lines:)" in t.problems[0]
    assert "line" in t.problems[0]


# ---- the window's crash watch -------------------------------------------------------------------------------------------

def test_the_crash_watch_reports_a_session_that_never_closed(tmp_path):
    was_enabled = faulthandler.is_enabled()
    dead = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True)
    dead_pid = int(dead.stdout.strip())
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "crash-old.log").write_text("Fatal Python error: Segmentation fault\n\nCurrent thread 0x1:\n", "utf-8")
    (logs / f"session-{dead_pid}.json").write_text(json.dumps({"pid": dead_pid, "version": "0.4.0",
                                                               "started": "yesterday", "crash_log": "crash-old.log"}),
                                                   encoding="utf-8")
    (logs / f"session-{os.getpid()}.json").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
    try:
        w = diag.CrashWatch(logs)
        previous = w.start()
        # the dead one, and a stale marker that has our pid (left by an earlier session that died with it)
        assert sorted(str(p["version"]) for p in previous) == ["0.4.0", "None"]
        old = next(p for p in previous if p["version"] == "0.4.0")
        assert old["crash_log"].endswith("crash-old.log") and "Segmentation fault" in old["excerpt"][0]
        assert w.marker.is_file() and w.crash_log.is_file() and not (logs / f"session-{dead_pid}.json").exists()
        assert w.previous() == []                                        # ours is alive: never reported
        w.stop()
        assert not w.marker.exists() and not w.crash_log.exists()        # a clean exit leaves nothing behind
        assert (logs / "crash-old.log").exists()                         # the old report stays in the logs
    finally:
        if was_enabled:
            faulthandler.enable()


def test_process_alive_never_kills_and_knows_the_living():
    from spec_surgeon.winapp import process_alive
    assert diag.pid_alive(os.getpid()) is True
    if not sys.platform.startswith("win"):
        assert process_alive(os.getpid()) is None                        # asked of Windows only


def test_app_logging_keeps_every_line(tmp_path):
    import logging
    path = diag.setup_app_logging(tmp_path / "logs")
    logger = logging.getLogger("spec_surgeon")
    try:
        assert diag.setup_app_logging(tmp_path / "logs") == path and sum(
            isinstance(h, diag.RotatingFileHandler) for h in logger.handlers) == 1      # idempotent
        diag.app_log().info("activity line")
        for h in logger.handlers:
            h.flush()
        assert "activity line" in path.read_text(encoding="utf-8")
    finally:
        for h in [h for h in logger.handlers if isinstance(h, diag.RotatingFileHandler)]:
            logger.removeHandler(h)
            h.close()
