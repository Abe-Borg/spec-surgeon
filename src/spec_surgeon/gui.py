"""The logic behind the Spec Surgeon window (docs/NL_SURGICAL_EDITOR_PLAN.md Phase 7), and its entry point.

    spec-surgeon-gui            (no console window on Windows; the installed app's SpecSurgeon.exe)
    spec-surgeon gui            (from a terminal)

``Session`` holds everything the window does — files, the conversation with Claude (compile, replies, choices), preview,
run (with the approval-token check), custom transforms, set-aside passes, advice — as plain calls into the engine;
nothing is applied before Preview, and Run refuses when a file changed since the preview. It has no widgets and no Qt,
so it is tested on its own and the command line never loads Qt.

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
    generated_preview: Any = None                 # preview.GeneratedPreview: its samples, renders, visual check
    last_run_dir: Optional[Path] = None
    questions: List[str] = field(default_factory=list)
    generated_token: str = ""                     # approval token of the files a generated transform was tested on
    selected_files: Optional[List[Path]] = None   # when set (e.g. from load_set_aside), narrows the active batch
    prior_failures: List[Dict[str, Any]] = field(default_factory=list)
    client: Any = None                            # injectable for tests
    last_batch: Any = None                        # BatchResult of the last Run (the window's results table)
    last_trials: Any = None                       # List[codegen.FileTrial] of the last AI-written-transform run
    visual_check: bool = True                     # Claude reviews the rendered sample pages of each preview
    conversation: Any = None                      # conversation.Conversation of the current request
    turn: Any = None                              # its last conversation.Turn
    chat_dir: Optional[Path] = None               # the session folder holding the conversation's records
                                                  # (conversation.json, compile.json); it follows each new folder
    last_compile: Any = None                      # CompileResult view of the conversation's last turn (compile.json)
    _compiled_dir: Optional[Path] = None          # the session folder the next preview or custom transform goes into
                                                  # (the compile record sits beside its preview); None once used
    generated_inputs: Dict[str, Any] = field(default_factory=dict)   # approval_inputs beside generated_token

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
        self.end_conversation()

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
        self._set_outcome()
        self.end_conversation()
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
    # ---- outcome: a spec XOR a custom transform, and what was previewed of it ----
    def _set_outcome(self, spec: Optional[Dict[str, Any]] = None, generated: Any = None, source: str = "") -> None:
        """The one place the session's outcome changes. A new outcome voids everything previewed or approved for the
        old one: a reply that turns a custom transform into a spec must never leave the transform runnable, and a reply
        that only asks a question must never leave the old spec's preview runnable."""
        if spec is not None and generated is not None:
            raise ValueError("a session runs either a spec or a custom transform, not both")
        self.spec, self.generated = spec, generated
        self.spec_source = source if spec is not None else ""
        self.preview, self.generated_token, self.generated_preview = None, "", None
        self.generated_inputs = {}

    def end_conversation(self) -> None:
        self.conversation, self.turn, self.questions = None, None, []
        self._compiled_dir = None                 # a loaded spec or saved operation never previews into its folder

    def load_spec(self, path: Path) -> Dict[str, Any]:
        from .editspec import load_spec, validate_spec
        spec = load_spec(path)
        validate_spec(spec)
        self._set_outcome(spec=spec, source=str(path))
        self.end_conversation()
        return spec

    def remove_spec(self) -> None:
        self._set_outcome()

    # ---- the conversation ----
    def conversation_matches(self, request: str) -> bool:
        """The current conversation is about this request, these files and this template (so Preview continues it
        rather than starting over)."""
        c = self.conversation
        if c is None or c.request.strip() != (request or "").strip():
            return False
        try:
            files = self.files()
        except ValueError:
            return False
        return ([Path(f).resolve() for f in files] == [f.resolve() for f in c.files]
                and (self.template.resolve() if self.template else None) == (c.template.resolve() if c.template else None))

    def compile(self, request: str, log: Log) -> str:
        """Start a conversation about ``request`` over the current files. Returns a status line; sets ``spec`` when
        Claude's first answer is a spec, otherwise ``turn`` says what Claude needs from the user. It opens the session
        folder (``out_dir``): conversation.json and compile.json there after every turn, and the first preview or
        custom transform beside them (``_session_dir``)."""
        from .conversation import Conversation
        if not request.strip():
            raise ValueError("type what you want done (or load a spec file)")
        files = self.files()
        self._set_outcome()
        self.end_conversation()
        out = self.out_dir = self._next_session_dir()   # the session folder: conversation.json and compile.json,
        self.chat_dir = self._compiled_dir = out        # then preview/, codegen/ and run/ beside them (bundle_root)
        log(f"Talking the request through with Claude over {len(files)} file(s)…")
        self.conversation = Conversation(request, files, template=self.template, client=self.client,
                                         prior_failures=self.prior_failures or None, work_dir=out / "chat", log=log)
        try:
            turn = self.conversation.start()
        except Exception:
            self._save_chat()                     # what was said before a defect stopped it, for the diagnosis
            raise
        msg = self._after_turn(turn)
        if (out / "compile.json").is_file():
            log(f"Compile record (the model's whole exchange): {out / 'compile.json'}")
        return msg

    def reply(self, text: str = "", choice_id: Optional[str] = None, log: Log = lambda m: None,
              render: bool = False) -> str:
        """The user's next message in the conversation — typed words, a picked choice, or both."""
        c = self.conversation
        if c is None:
            raise ValueError("there is no conversation yet — type a request and press Preview")
        if not self.conversation_matches(c.request):
            raise ValueError("the files or the template changed since this conversation started — press Start over")
        chosen = c.choice(choice_id) if choice_id else None
        if self._compiled_dir is None:            # a preview or custom transform used the folder: its records stay
            self.chat_dir = self._compiled_dir = self._next_session_dir()   # as they were; this reply's go here
            log(f"The conversation continues in {self.chat_dir} (conversation.json, compile.json)")
        self._set_outcome()                       # whatever was previewed before this reply is void now
        if chosen is not None and chosen.kind == "custom_transform":
            c.take_app_choice(chosen.id)
            proposal = (c.unsupported or {}).get("proposal") or chosen.detail
            try:
                msg = self._codegen(c.request, proposal, self.files(), log, brief=c.brief(), render=render)
                gp = self.generated_preview
                c.note(f"The one-off custom transform {self.generated.name!r} passed verification on the sample "
                       f"files; the user reviews its preview.\n" + (gp.to_text()[:4000] if gp is not None else ""),
                       display=f"Custom transform {self.generated.name!r} passed verification on the sample files.")
            except RuntimeError as e:
                c.note(f"No custom transform passed verification: {e}")
                self._save_chat()
                raise
            except Exception:
                self._save_chat()                 # a defect propagates; what was said is kept for the diagnosis
                raise
            self._save_chat()
            return msg
        try:
            turn = c.reply(text, choice_id, client=self.client)
        except Exception:
            self._save_chat()
            raise
        return self._after_turn(turn)

    def _after_turn(self, turn) -> str:
        self.turn = turn
        self.questions = list(turn.questions)
        self._save_chat()
        if turn.status == "ok":
            self._set_outcome(spec=turn.spec, source="compiled")
            return "Compiled."
        if turn.status == "questions":
            return "Claude needs your answer — reply in the chat below, or pick one of its choices."
        if turn.status == "unsupported":
            offer = any(ch.kind == "custom_transform" for ch in turn.choices)
            return (f"No built-in operation can do this: {turn.message}" +
                    ("\nClaude can write a one-off custom transform for it — pick that choice in the chat." if offer else ""))
        return f"That didn't work: {turn.message}\nReply in the chat to try again, or Start over."

    def _save_chat(self) -> None:
        """conversation.json and compile.json (the request, the answers and the outcome of the conversation's last turn
        with the model's whole exchange) in the current session folder; a record that cannot be written never stops
        the work."""
        c = self.conversation
        if c is None or self.chat_dir is None:
            return
        try:
            c.save(self.chat_dir / "conversation.json")
        except OSError:
            pass
        if c.turn is not None:
            from .diagnostics import write_json
            self.last_compile = c.compile_result(c.turn)
            write_json(self.chat_dir / "compile.json", {"request": c.request, "answers": list(c.answers),
                                                        **self.last_compile.to_dict()})

    def _session_dir(self) -> Path:
        """The folder the next preview or custom transform goes into: the conversation's folder while nothing has used
        it yet, else a new one (a loaded spec or saved operation: a new results folder). The conversation's records
        follow it, so every session folder holds the exchange that led to its preview and run (diagnostics.bundle_root
        bundles them together); once used, a folder's records are frozen (``reply`` opens the next folder before
        anything more is said)."""
        if self.conversation is None:
            out = self.new_out_dir()
        else:
            out = self._compiled_dir if self._compiled_dir is not None else self._next_session_dir()
        self.out_dir, self._compiled_dir = out, None
        if self.conversation is not None and self.chat_dir != out:
            self.chat_dir = out
            self._save_chat()
        return out

    def _next_session_dir(self) -> Path:
        """A new session folder for a conversation's records — never one that exists, even within the same second, so
        the records beside a preview or run are never overwritten by a later turn or another conversation. It does not
        move ``out_dir`` (a reply leaves it on the last preview until the next one)."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out, n = Path(self.out_root) / stamp, 2
        while out.exists():
            out, n = Path(self.out_root) / f"{stamp}-{n}", n + 1
        out.mkdir(parents=True, exist_ok=True)
        return out

    @staticmethod
    def _generated_key(g) -> Dict[str, Any]:
        return {"generated_source": g.source, "select": g.select, "params": g.params}

    def _codegen(self, request: str, proposal: str, files: List[Path], log: Log, brief: Optional[str] = None,
                 render: bool = False) -> str:
        from .codegen import generate_op, pick_samples
        from .preview import approval_inputs, approval_token, preview_generated
        samples = pick_samples(files)
        out = self._session_dir()                 # beside the conversation's records: codegen/history.json, preview/
        log(f"Writing a one-off transform, tested on {[s.name for s in samples]}…")
        g = generate_op(request, proposal or "", samples, out / "codegen", client=self.client, log=log, brief=brief)
        if not g.passed:
            raise RuntimeError("No AI-written transform passed verification: " + "; ".join(g.errors or
                               [f"attempt {a['attempt']}: {'; '.join(a['problems'][:2])}" for a in g.attempts[-1:]]))
        self._set_outcome(generated=g)
        self.generated_token = approval_token(self._generated_key(g), files)
        self.generated_inputs = approval_inputs(self._generated_key(g), files)
        self.generated_preview = preview_generated(g, out / "preview", "auto", render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        if render and self.visual_check:
            intent = "\n".join([f"A ONE-OFF CUSTOM TRANSFORM ({g.name}) for: {request}", f"WHAT IT SHOULD DO: {proposal}"]
                               + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
            self._visual_check(self.generated_preview, intent, out / "preview", log)
        return (f"An AI-written transform ({g.name}) passed verification on {len(samples)} sample file(s). "
                f"It is saved at {g.path} — Run writes edited copies only (never over your files).")

    # ---- your library ----
    def load_recipe(self, name: str) -> Dict[str, Any]:
        from .library import load_recipe
        return self.load_spec(load_recipe(name))

    def use_operation(self, name: str, log: Log, render: bool = False) -> str:
        """Run one of your saved operations on this batch: first on its sample files (sandbox + the full suite — it
        must pass here, not only where it was made), then its preview; Run writes edited copies only."""
        from .codegen import pick_samples, trial
        from .library import load_operation
        from .preview import approval_inputs, approval_token, preview_generated
        g = load_operation(name)
        files = self.files()
        self._set_outcome()
        self.end_conversation()
        samples = pick_samples(files)
        out = self.new_out_dir()
        log(f"Trying your operation {name!r} on {[f.name for f in samples]}…")
        trials = [trial(g.source, f, g.select, g.params, out / "library-trial" / f"{i:02d}") for i, f in enumerate(samples)]
        bad = [f"{t.source.name}: {'; '.join(t.problems[:2])}" for t in trials if not t.passed]
        if bad:
            raise RuntimeError(f"your operation {name!r} does not pass on these files — " + " | ".join(bad))
        g.passed, g.trials = True, trials
        self._set_outcome(generated=g)
        self.generated_token = approval_token(self._generated_key(g), files)
        self.generated_inputs = approval_inputs(self._generated_key(g), files)
        self.generated_preview = preview_generated(g, out / "preview", "auto", render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        if render and self.visual_check:
            self._visual_check(self.generated_preview, f"YOUR SAVED OPERATION {name!r} for: {g.request}\n"
                                                      f"WHAT IT DOES: {g.proposal}", out / "preview", log)
        return (f"Your operation {name!r} passed verification on {len(samples)} sample file(s) of this batch. "
                f"Run writes edited copies only (never over your files).")

    def save_recipe(self, name: str, description: str = "", replace: bool = False):
        """Save the previewed spec (only while its preview is current) as a recipe."""
        from .library import save_recipe
        from .preview import approval_token
        if self.spec is None or self.preview is None or approval_token(self.spec, self.files()) != self.preview.token:
            raise ValueError("preview the spec first — only a spec whose preview is current can be saved")
        return save_recipe(name, self.spec, description, replace)

    def save_operation(self, name: str, description: str = "", replace: bool = False):
        """Save the custom transform (only one that passed on this batch's samples and is still current)."""
        from .library import save_operation
        from .preview import approval_token
        g = self.generated
        if g is None or not g.passed or approval_token(self._generated_key(g), self.files()) != self.generated_token:
            raise ValueError("only a custom transform that passed on the current files can be saved — preview again")
        return save_operation(name, g, description, replace)

    def make_preview(self, render: bool, log: Log) -> str:
        from .preview import make_preview
        if self.generated is not None:
            gp = self.generated_preview
            return ((gp.to_text() + "\n\n") if gp is not None else "") + \
                "AI-written transform ready. Press Run to apply it to every file (edited copies only)."
        if self.spec is None:
            raise ValueError("nothing to preview — type a request or load a spec file")
        files = self.files()                      # the same filtered set Run checks and runs (no template files)
        out = self._session_dir()                 # beside the conversation's records (a loaded spec: a new folder)
        log("Planning every file and trying the samples…")
        self.preview = make_preview(self.spec, [str(f) for f in files], out / "preview", renderer="auto",
                                    do_render=render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        if render and self.visual_check:
            self._visual_check(self.preview, self._intent(), out / "preview", log)
        self._note_preview()
        return self.preview.to_text()

    # ---- Claude's visual check: advisory, can stop a run, never passes one ----
    def _intent(self) -> str:
        spec = self.spec or {}
        lines = [f"REQUEST: {spec.get('request') or ''}", f"WHAT THE SPEC DOES: {spec.get('summary') or ''}"]
        lines += [f"ASSUMPTION: {a}" for a in spec.get("assumptions") or []]
        if self.conversation is not None:
            lines.append("THE CONVERSATION:\n" + self.conversation.brief())
        return "\n".join(lines)

    def _visual_check(self, pv, intent: str, folder: Path, log: Log) -> None:
        from . import compiler, vision
        from .preview import write_preview_files
        client = self.client
        pv.review = vision.review_samples(intent, pv.samples, folder, lambda: client or compiler._client(), log=log)
        vision.save_review(pv.review, folder)
        write_preview_files(pv)

    def _current_review(self):
        """(the preview object that carries the visual check, the token it was made for)."""
        if self.generated is not None:
            return self.generated_preview, self.generated_token
        if self.preview is not None:
            return self.preview, self.preview.token
        return None, ""

    def run_blocked_reason(self) -> str:
        """Why Run must wait for the user: Claude's visual check found something it rates as blocking, and the user
        has not acknowledged it for this very preview."""
        pv, token = self._current_review()
        rv = getattr(pv, "review", None) if pv is not None else None
        if rv is None or not rv.blocking() or getattr(pv, "review_ack", "") == token:
            return ""
        return (f"Claude's visual check flagged {len(rv.blocking())} problem(s) on the sample pages — look at them on the "
                f"Preview step and press \u201cI've looked \u2014 allow Run\u201d, or change the request.")

    def acknowledge_review(self, token: str) -> None:
        """The user has looked at the blocking findings of the preview made with ``token`` (and only that one)."""
        pv, current = self._current_review()
        if pv is None or not token or token != current:
            raise ValueError("that preview is not the current one — press Preview again")
        pv.review_ack = token

    def _note_preview(self) -> None:
        """Tell Claude what the preview found, so a reply from the Preview page is a correction it can act on."""
        if self.conversation is None or self.preview is None:
            return
        pv = self.preview
        bp = pv.batch_plan
        total, planned = len(bp.files), len(bp.files) - len(bp.exceptions)
        flagged = len(pv.review.blocking()) if pv.review is not None else 0
        self.conversation.note("THE PREVIEW OF YOUR SPEC:\n" + pv.to_text()[:6000],
                               display=f"Preview made: {planned} of {total} file(s) plan cleanly." +
                                       (f" Claude's visual check flagged {flagged} problem(s)." if flagged else ""))
        self._save_chat()

    def run(self, in_place: bool, render: bool, log: Log) -> str:
        from .batch import run_batch
        from .preview import approval_token
        blocked = self.run_blocked_reason()
        if blocked:
            raise ValueError(blocked)
        if self.generated is not None:
            from .codegen import run_generated
            files = self.files()
            key = self._generated_key(self.generated)
            if approval_token(key, files) != self.generated_token:
                from .preview import approval_inputs, explain_approval_change
                what = explain_approval_change(self.generated_inputs or {}, approval_inputs(key, files))
                raise ValueError("a file changed (or the folder changed) since the AI-written transform was tested — "
                                 "press Preview again" + ("".join(f"\n  • {w}" for w in what) if what else ""))
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
