"""The logic behind the Spec Surgeon window (docs/NL_SURGICAL_EDITOR_PLAN.md Phase 7), and its entry point.

    spec-surgeon-gui            (no console window on Windows; the installed app's SpecSurgeon.exe)
    spec-surgeon gui            (from a terminal)

``Session`` holds everything the window does — files, compile, preview, run (with the approval-token check),
Tier B, set-aside passes, advice — as plain calls into the engine; nothing is applied before Preview, and Run
refuses when a file changed since the preview. It has no widgets and no Qt, so it is tested on its own and the
command line never loads Qt.

The window itself (PySide6 / Qt 6, v0.4.0) is the ``ui`` package: ``main()`` and ``build_window()`` below import it
lazily and explain how to install Qt when it is missing (``pip install "spec-surgeon[gui]"``).
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

Log = Callable[[str], None]


# ---- the logic (no widgets; tested on its own) ----------------------------------------------------

@dataclass
class Session:
    """What the window is working on. Every step is a call into the engine; the window only shows the text."""
    folder: Optional[Path] = None
    template: Optional[Path] = None
    out_root: Path = field(default_factory=lambda: Path.home() / "SpecSurgeon_out")
    spec: Optional[Dict[str, Any]] = None
    spec_source: str = ""                         # "compiled" | path of a loaded spec file
    out_dir: Optional[Path] = None
    preview: Any = None
    generated: Any = None                         # Tier B op, when the request needed one
    last_run_dir: Optional[Path] = None
    questions: List[str] = field(default_factory=list)
    generated_token: str = ""                     # approval token of the files a generated transform was tested on
    selected_files: Optional[List[Path]] = None   # when set (e.g. from load_set_aside), narrows the active batch
    prior_failures: List[Dict[str, Any]] = field(default_factory=list)
    client: Any = None                            # injectable for tests
    last_batch: Any = None                        # BatchResult of the last Run (the window's results table)
    last_trials: Any = None                       # List[codegen.FileTrial] of the last AI-written-transform run
    last_compile: Any = None                      # CompileResult of the last compile (its transcript: compile.json)
    _compiled_dir: Optional[Path] = None          # the folder the last compile wrote into; its preview goes there too

    def files(self) -> List[Path]:
        from .batch import expand_inputs
        from .editspec import is_template_file, spec_templates
        templates = ([self.template.resolve()] if self.template else []) + (spec_templates(self.spec) if self.spec else [])
        if self.selected_files is not None:
            files = [f for f in self.selected_files if f.is_file() and not is_template_file(f, templates)]
            if not files:
                raise ValueError("no set-aside .docx files remain to process")
            return files
        if not self.folder:
            raise ValueError("choose the folder with the specs first")
        files = [f for f in expand_inputs([str(self.folder)]) if not is_template_file(f, templates)]
        if not files:
            raise ValueError(f"no .docx files in {self.folder}")
        return files

    def reset_set_aside(self) -> None:
        self.selected_files = None
        self.prior_failures = []

    def load_set_aside(self, source_dir: Optional[Path] = None) -> str:
        """Narrow the session to only the files that were set aside in the last run (or ``source_dir``),
        preserving their failure diagnostics for the next compile pass."""
        from .batch import load_set_aside
        target = source_dir or self.last_run_dir or (self.out_dir / "run" if self.out_dir else None)
        if not target:
            raise ValueError("run a batch first (or provide a batch output folder with set_aside.json)")
        manifest = load_set_aside(target)
        items = manifest.get("files") or []
        if not items:
            self.reset_set_aside()
            return "All files passed in the previous run — 0 set-aside files remaining."
        self.selected_files = [Path(x["source"]) for x in items]
        self.prior_failures = list(items)
        self.spec, self.preview, self.generated, self.questions = None, None, None, []
        lines = [f"Loaded {len(items)} set-aside file(s) for the next pass:"]
        for x in items:
            lines.append(f"  - {x['name']} [{x['code']}] ({x['category']}): {x['reason']}")
        lines.append("Press Preview to compile an EditSpec tailored to these files.")
        return "\n".join(lines)

    def new_out_dir(self) -> Path:
        self.out_dir = Path(self.out_root) / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir

    # ---- steps ----
    def load_spec(self, path: Path) -> Dict[str, Any]:
        from .editspec import load_spec, validate_spec
        spec = load_spec(path)
        validate_spec(spec)
        self.spec, self.spec_source, self.preview, self.generated, self.questions = spec, str(path), None, None, []
        return spec

    def compile(self, request: str, answers: List[str], allow_codegen: bool, log: Log) -> str:
        """Returns a short status line; sets ``spec`` (or ``questions`` / ``generated``)."""
        from .compiler import compile_request
        if not request.strip():
            raise ValueError("type what you want done (or load a spec file)")
        files = self.files()
        self.spec, self.preview, self.generated, self.questions = None, None, None, []
        self._compiled_dir = None
        log(f"Compiling the request over {len(files)} file(s)…")
        res = compile_request(request, files, template=self.template, answers=answers, client=self.client, log=log,
                              prior_failures=self.prior_failures or None)
        self.last_compile = res
        # the whole exchange with the model, kept with the preview it leads to (or on its own when it leads nowhere)
        self._compiled_dir = self.new_out_dir()
        from .diagnostics import write_json
        if write_json(self._compiled_dir / "compile.json", {"request": request, "answers": list(answers),
                                                            **res.to_dict()}):
            log(f"Compile record (the model's whole exchange): {self._compiled_dir / 'compile.json'}")
        if res.status == "questions":
            self.questions = res.questions
            return "The request needs answers — type them in the Answers box (one per line) and press Preview again."
        if res.status == "unsupported":
            reason = f"No operation can do this: {res.spec.get('unsupported')}. Proposal: {res.spec.get('proposal')}"
            if not allow_codegen:
                return reason + "\nTick 'Allow AI-written transforms' to let Claude write a one-off, tested transform."
            return self._codegen(request, res.spec, files, log)
        if res.status != "ok":
            raise RuntimeError("Compile failed: " + "; ".join(res.errors))
        self.spec, self.spec_source = res.spec, "compiled"
        return "Compiled."

    @staticmethod
    def _generated_key(g) -> Dict[str, Any]:
        return {"generated_source": g.source, "select": g.select, "params": g.params}

    def _codegen(self, request: str, spec: Dict[str, Any], files: List[Path], log: Log) -> str:
        from .codegen import generate_op
        from .preview import approval_token
        from .profile import profile_batch
        names = {f.name: f for f in files}
        samples = [names[p["files"][0]] for p in profile_batch(files)["patterns"][:3]]
        out = self._compiled_dir or self.new_out_dir()
        log(f"Writing a one-off transform, tested on {[s.name for s in samples]}…")
        g = generate_op(request, spec.get("proposal") or spec.get("unsupported") or "", samples, out / "codegen",
                        client=self.client, log=log)
        if not g.passed:
            raise RuntimeError("No AI-written transform passed verification: " + "; ".join(g.errors))
        self.generated = g
        self.generated_token = approval_token(self._generated_key(g), files)
        return (f"An AI-written transform ({g.name}) passed verification on {len(samples)} sample file(s). "
                f"It is saved at {g.path} — Run writes outputs only (never over your files).")

    def make_preview(self, render: bool, log: Log) -> str:
        from .preview import make_preview
        if self.generated is not None:
            return "AI-written transform ready (see above). Press Run to apply it to every file (outputs only)."
        if self.spec is None:
            raise ValueError("nothing to preview — type a request or load a spec file")
        files = self.files()                      # the same filtered set Run checks and runs (no template files)
        out = self._compiled_dir if self._compiled_dir is not None and self.spec_source == "compiled" \
            else self.new_out_dir()
        self.out_dir, self._compiled_dir = out, None
        log("Planning every file and trying the samples…")
        self.preview = make_preview(self.spec, [str(f) for f in files], out / "preview", renderer="auto",
                                    do_render=render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        return self.preview.to_text()

    def run(self, in_place: bool, render: bool, log: Log) -> str:
        from .batch import run_batch
        from .preview import approval_token
        if self.generated is not None:
            from .codegen import run_generated
            files = self.files()
            if approval_token(self._generated_key(self.generated), files) != self.generated_token:
                raise ValueError("a file changed (or the folder changed) since the AI-written transform was tested — "
                                 "press Preview again")
            out = (self.out_dir or self.new_out_dir()) / "run"
            res = run_generated(self.generated, [str(f) for f in files], out, log=log)
            self.last_run_dir, self.last_trials, self.last_batch = out, res, None
            ok = sum(r.passed for r in res)
            return f"{ok} of {len(res)} file(s) PASS. Outputs: {out / 'outputs'} (your files were not changed)."
        if self.preview is None or self.spec is None:
            raise ValueError("press Preview first — nothing runs before you have seen the preview")
        files = self.files()
        if approval_token(self.spec, files) != self.preview.token:
            from .preview import approval_inputs, explain_approval_change
            what = explain_approval_change(getattr(self.preview, "token_inputs", None) or {},
                                           approval_inputs(self.spec, files))
            raise ValueError("a file or the spec changed since the preview — press Preview again"
                             + ("".join(f"\n  • {w}" for w in what) if what else ""))
        out = self.out_dir / "run"
        self.last_run_dir = out                   # set before the run: a run that stops still has its logs there
        batch = run_batch([str(f) for f in files], "edit-spec", {}, out, renderer="auto", do_render=render,
                          in_place=in_place, progress=log, spec=self.spec)
        self.last_run_dir, self.last_batch, self.last_trials = out, batch, None
        c = batch.counts()
        lines = [f"{len(batch.files)} file(s): PASS {c['PASS']}  FAIL {c['FAIL']}  EXCEPTION {c['EXCEPTION']}"]
        for f in batch.files:
            line = f"  {f.status:<9} {f.source.name}"
            if f.exception:
                line += f"  [{f.exception.get('code')}] {f.exception.get('reason')}"
                if f.exception.get("kind") not in (None, "refusal"):
                    line += f" (at {f.exception.get('stage')})"
            elif f.record and not f.record.passed:
                line += "  " + "; ".join(f"{ch.id} FAIL" for ch in f.record.checks if ch.status == "FAIL")
            elif f.delivered_to:
                line += f"  → written back to {f.delivered_to}" if in_place else f"  → {f.delivered_to}"
            lines.append(line)
        lines.append(f"\nReport: {out / 'BATCH_REPORT.md'}")
        lines.append(f"Diagnostics (why, where and how, per file): {out / 'DIAGNOSTICS.md'} — log: "
                     f"{out / 'logs' / 'run.log'}")
        if not in_place:
            lines.append(f"Edited copies that passed every check: {out / 'outputs'} (your files were not changed)")
        if c["FAIL"]:
            lines.append(f"Copies that FAILED verification: {out / 'failed'} (diagnosis only — do not use them)")
        if c["EXCEPTION"] or c["FAIL"]:
            lines.append("Some files were set aside untouched — click 'Load set-aside files' to run the next pass "
                         "on only those files, or 'Advise on exceptions' for per-file advice.")
        return "\n".join(lines)

    def advise(self, log: Log) -> str:
        from .advisor import advise_batch
        if not self.last_run_dir:
            raise ValueError("run something first")
        res = advise_batch(self.last_run_dir, client=self.client, log=log)
        if not res:
            return "No exceptions to advise on."
        return "\n".join(f"  {a.kind:<14} {Path(a.source).name}" for a in res) + \
            f"\n\nAdvice: {self.last_run_dir / 'advice'}"


def open_folder(path: Path) -> None:
    path = Path(path)
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa: S606 — opening a folder the user asked to see
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ---- the window ----------------------------------------------------------------------------------

def build_window(session: Optional[Session] = None, **kw):
    """The Qt window (``ui.app.build_window``); imported lazily so this module never needs PySide6."""
    from .ui.app import build_window as _build
    return _build(session, **kw)


GUI_MISSING = ("The Spec Surgeon window needs Qt for Python (PySide6).\n"
               "Install it with:\n"
               '    py -3 -m pip install "spec-surgeon[gui]"\n'
               "(the Windows installer includes it).")


def main(argv=None) -> int:
    try:
        import PySide6.QtWidgets  # noqa: F401 — PySide6, or a system library Qt needs (libEGL on Linux), missing
    except ImportError as e:
        msg = f"{GUI_MISSING}\n\n({e})"
        if sys.stderr is not None:
            print(msg, file=sys.stderr)
        else:                                      # pythonw (the windowed launcher): no console to print to
            from .winapp import message_box
            message_box("Spec Surgeon", msg)       # modal — never shown when a stream can take the message
        return 2
    from .ui.app import main as qt_main
    return qt_main(argv)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
