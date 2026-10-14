"""A simple window for running Spec Surgeon (docs/NL_SURGICAL_EDITOR_PLAN.md Phase 7).

    spec-surgeon-gui            (no console window on Windows)
    spec-surgeon gui            (from a terminal)

One window, top to bottom: the specs folder, an optional template, where results go, the request in plain English
(or a spec file), answers to anything the compiler asks, three options, and the buttons
**Preview → Run → Load set-aside files → Open results**. Everything the window does is a plain call into the
engine (``Session`` below); nothing is applied before Preview, and Run refuses when a file changed since the
preview (approval token).

Tkinter only (it ships with Python on Windows) — no new dependency. Long work runs on a background thread; the
log pane shows its progress.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
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
        log(f"Compiling the request over {len(files)} file(s)…")
        res = compile_request(request, files, template=self.template, answers=answers, client=self.client, log=log,
                              prior_failures=self.prior_failures or None)
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
        out = self.new_out_dir()
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
        out = self.new_out_dir()
        log("Planning every file and trying the samples…")
        self.preview = make_preview(self.spec, [str(f) for f in files], out / "preview", renderer="auto",
                                    do_render=render)
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
            self.last_run_dir = out
            ok = sum(r.passed for r in res)
            return f"{ok} of {len(res)} file(s) PASS. Outputs: {out / 'outputs'} (your files were not changed)."
        if self.preview is None or self.spec is None:
            raise ValueError("press Preview first — nothing runs before you have seen the preview")
        files = self.files()
        if approval_token(self.spec, files) != self.preview.token:
            raise ValueError("a file or the spec changed since the preview — press Preview again")
        out = self.out_dir / "run"
        batch = run_batch([str(f) for f in files], "edit-spec", {}, out, renderer="auto", do_render=render,
                          in_place=in_place, progress=log, spec=self.spec)
        self.last_run_dir = out
        c = batch.counts()
        lines = [f"{len(batch.files)} file(s): PASS {c['PASS']}  FAIL {c['FAIL']}  EXCEPTION {c['EXCEPTION']}"]
        for f in batch.files:
            line = f"  {f.status:<9} {f.source.name}"
            if f.exception:
                line += f"  [{f.exception.get('code')}] {f.exception.get('reason')}"
            elif f.record and not f.record.passed:
                line += "  " + "; ".join(f"{ch.id} FAIL" for ch in f.record.checks if ch.status == "FAIL")
            elif f.delivered_to:
                line += f"  → written back to {f.delivered_to}"
            lines.append(line)
        lines.append(f"\nReport: {out / 'BATCH_REPORT.md'}")
        if not in_place:
            lines.append(f"Edited copies: {out / 'outputs'} (your files were not changed)")
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

def build_app(root=None, session: Optional[Session] = None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
    from . import __version__

    root = root or tk.Tk()
    root.title(f"Spec Surgeon {__version__}")
    root.geometry("980x760")
    s = session or Session()
    q: "queue.Queue[tuple]" = queue.Queue()
    pad = {"padx": 6, "pady": 3}

    frm = ttk.Frame(root, padding=8)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    folder_v = tk.StringVar(value=str(s.folder or ""))
    template_v = tk.StringVar(value=str(s.template or ""))
    out_v = tk.StringVar(value=str(s.out_root))
    key_v = tk.StringVar(value="")
    render_v, inplace_v, codegen_v = tk.BooleanVar(value=True), tk.BooleanVar(value=False), tk.BooleanVar(value=False)

    def browse_dir(var):
        d = filedialog.askdirectory(initialdir=var.get() or str(Path.home()))
        if d:
            var.set(d)
            if var is folder_v:
                s.reset_set_aside()

    def browse_file(var, types):
        f = filedialog.askopenfilename(filetypes=types)
        if f:
            var.set(f)

    rows = [("Specs folder", folder_v, lambda: browse_dir(folder_v)),
            ("Template (optional)", template_v, lambda: browse_file(template_v, [("Word", "*.docx")])),
            ("Results go to", out_v, lambda: browse_dir(out_v))]
    for r, (label, var, cmd) in enumerate(rows):
        ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=var).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=cmd).grid(row=r, column=2, **pad)

    ttk.Label(frm, text="What should change?").grid(row=3, column=0, sticky="nw", **pad)
    request_t = tk.Text(frm, height=3, wrap="word")
    request_t.grid(row=3, column=1, columnspan=2, sticky="ew", **pad)
    ttk.Label(frm, text="Answers (if asked)").grid(row=4, column=0, sticky="nw", **pad)
    answers_t = tk.Text(frm, height=2, wrap="word")
    answers_t.grid(row=4, column=1, columnspan=2, sticky="ew", **pad)

    ttk.Label(frm, text="Anthropic API key").grid(row=5, column=0, sticky="w", **pad)
    key_e = ttk.Entry(frm, textvariable=key_v, show="•")
    key_e.grid(row=5, column=1, sticky="ew", **pad)
    ttk.Label(frm, text="(only if not set in Windows; kept for this session only)").grid(row=5, column=2, sticky="w")

    opts = ttk.Frame(frm)
    opts.grid(row=6, column=0, columnspan=3, sticky="w", **pad)
    ttk.Checkbutton(opts, text="Render pages (Word or LibreOffice)", variable=render_v).pack(side="left", padx=6)
    ttk.Checkbutton(opts, text="Write results back over the originals (guarded)", variable=inplace_v).pack(side="left", padx=6)
    ttk.Checkbutton(opts, text="Allow AI-written transforms", variable=codegen_v).pack(side="left", padx=6)

    btns = ttk.Frame(frm)
    btns.grid(row=7, column=0, columnspan=3, sticky="w", **pad)
    log_t = ScrolledText(frm, height=24, wrap="word", font=("Consolas", 9))
    log_t.grid(row=8, column=0, columnspan=3, sticky="nsew", **pad)
    frm.rowconfigure(8, weight=1)
    status_v = tk.StringVar(value="Choose the specs folder, type a request (or load a spec file), then Preview.")
    ttk.Label(frm, textvariable=status_v, relief="sunken", anchor="w").grid(row=9, column=0, columnspan=3, sticky="ew")

    buttons: Dict[str, Any] = {}

    def write(text):
        log_t.insert("end", text + "\n")
        log_t.see("end")

    def sync():
        new_folder = Path(folder_v.get()) if folder_v.get().strip() else None
        if new_folder != s.folder:
            s.reset_set_aside()
        s.folder = new_folder
        s.template = Path(template_v.get()) if template_v.get().strip() else None
        s.out_root = Path(out_v.get()) if out_v.get().strip() else s.out_root
        if key_v.get().strip():
            os.environ["ANTHROPIC_API_KEY"] = key_v.get().strip()

    def busy(on: bool, label: str = ""):
        for b in buttons.values():
            b.state(["disabled"] if on else ["!disabled"])
        status_v.set(label if on else "Ready.")

    def work(label: str, fn):
        """Run fn(log) on a background thread; its log lines and result come back through the queue."""
        sync()
        busy(True, label + "…")
        write(f"\n=== {label} ===")

        def target():
            try:
                q.put(("done", fn(lambda m: q.put(("log", str(m))))))
            except Exception as e:  # shown to the user, not swallowed
                q.put(("error", f"{type(e).__name__}: {e}", traceback.format_exc()))
        threading.Thread(target=target, daemon=True).start()

    def poll():
        try:
            while True:
                item = q.get_nowait()
                if item[0] == "log":
                    write(item[1])
                elif item[0] == "done":
                    write(item[1] or "")
                    busy(False)
                elif item[0] == "error":
                    write("ERROR: " + item[1])
                    busy(False)
                    status_v.set("Error — see the log.")
        except queue.Empty:
            pass
        root.after(120, poll)

    # Tk widgets and variables may only be touched on the main thread: every value a background job needs is
    # read here, before the thread starts, and passed in.
    def do_preview():
        req = request_t.get("1.0", "end").strip()
        answers = [a for a in answers_t.get("1.0", "end").splitlines() if a.strip()]
        allow_codegen, render = codegen_v.get(), render_v.get()

        def fn(log):
            out = []
            if s.spec_source and s.spec_source != "compiled" and s.spec is not None and not req:
                out.append(f"Using the spec file {s.spec_source}.")
            else:
                msg = s.compile(req, answers, allow_codegen, log)
                out.append(msg)
                if s.questions:
                    out += [f"  ? {x}" for x in s.questions]
                    return "\n".join(out)
            if s.spec is not None or s.generated is not None:
                out.append(s.make_preview(render, log))
                if s.preview is not None:
                    out.append(f"\nPreview files: {s.out_dir / 'preview'}  — look, then press Run.")
            return "\n".join(out)
        work("Preview", fn)

    def do_load_spec():
        f = filedialog.askopenfilename(filetypes=[("EditSpec", "*.json")])
        if not f:
            return
        try:
            spec = s.load_spec(Path(f))
            request_t.delete("1.0", "end")
            write(f"\nLoaded spec {f}: {spec.get('request')!r} — {spec.get('summary')}\nPress Preview.")
        except Exception as e:
            messagebox.showerror("Spec Surgeon", f"Cannot use this spec:\n{e}")

    def do_run():
        sync()
        if inplace_v.get() and not messagebox.askyesno(
                "Spec Surgeon", "Write the verified results back over the original files?\n\nOnly files that pass "
                "every check are written, and a file open in Word or changed since the preview is skipped."):
            return
        in_place, render = inplace_v.get(), render_v.get()
        work("Run", lambda log: s.run(in_place, render, log))

    def do_set_aside():
        work("Load set-aside files", lambda log: s.load_set_aside())

    def do_open():
        target = s.last_run_dir or s.out_dir or s.out_root
        if target and Path(target).exists():
            open_folder(Path(target))
        else:
            messagebox.showinfo("Spec Surgeon", "Nothing there yet.")

    def do_advise():
        work("Advice on exceptions", lambda log: s.advise(log))

    for name, text, cmd in (("preview", "Preview", do_preview), ("load", "Load spec file…", do_load_spec),
                            ("run", "Run", do_run), ("set_aside", "Load set-aside files", do_set_aside),
                            ("open", "Open results folder", do_open),
                            ("advise", "Advise on exceptions", do_advise)):
        buttons[name] = ttk.Button(btns, text=text, command=cmd)
        buttons[name].pack(side="left", padx=4)

    root.after(120, poll)
    root._spec_surgeon = {"session": s, "buttons": buttons, "log": log_t, "request": request_t, "answers": answers_t,
                          "vars": {"folder": folder_v, "template": template_v, "out": out_v, "render": render_v,
                                   "in_place": inplace_v, "codegen": codegen_v}, "status": status_v}
    return root


def main(argv=None) -> int:
    try:
        root = build_app()
    except ImportError:
        print("Tkinter is not available in this Python. On Windows it ships with the python.org installer "
              "(tick 'tcl/tk and IDLE').", file=sys.stderr)
        return 2
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
