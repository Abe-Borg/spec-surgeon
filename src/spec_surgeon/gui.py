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

# Running without an API key (``Session.claude_ready`` False): what each Claude feature says instead of calling. Saved
# specs, your library, Preview, Run and set-aside passes need no key and are unchanged.
NEEDS_KEY = ("Describing a change in plain English needs an Anthropic API key — set one in Settings. Without one, use "
             "a saved spec or Your library: they preview and run without a key.")
NEEDS_KEY_REPLY = ("Claude needs an Anthropic API key to go on with this conversation — set one in Settings, or Start "
                   "over and use a saved spec or Your library.")
NEEDS_KEY_ADVICE = ("Advice on set-aside files comes from Claude and needs an Anthropic API key — set one in Settings. "
                    "Without one, View diagnostics says why each file was set aside.")
NO_KEY_REVIEW = ("Claude's visual check needs an Anthropic API key (Settings) — look at the before/after pages "
                 "yourself.")


class TransformFailed(RuntimeError):
    """No custom transform passed verification: every attempt the writer submitted failed on the samples, or a call to
    Claude failed (an API error, recorded in ``codegen/history.json``). A recorded outcome — ``Session.reply`` tells
    Claude about it; any other exception raised while a transform is made is a defect and is never told to Claude as
    a verification result."""
    PREFIX = "No custom transform passed verification: "

    def __init__(self, details: str):
        self.details = details
        super().__init__(self.PREFIX + details)


class TrialsFailed(RuntimeError):
    """A transform tried on the batch's sample files (``Session._prove``: sandbox + the full suite) did not pass on
    every one — a recorded refusal naming each failing sample, never a defect. Nothing changed in the session."""


class OperationFailed(TrialsFailed):
    """One of your saved operations does not pass on this batch's samples. When the conversation offered it (a choice
    Claude named), this is the ONE outcome Claude hears — as ``TransformFailed`` is for a fresh custom transform; a
    defect while trying it is never told to Claude as a result."""


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
    generated_intent: str = ""                    # what the custom transform's visual check is told it should do
    recipe_name: str = ""                         # the outcome is one of your recipes, picked in the conversation and
                                                  # used as saved (``spec_source`` "compiled": the conversation's
                                                  # outcome)

    def claude_ready(self) -> bool:
        """Claude can be called: a client was given (tests, scripted) or credentials are there
        (``llm.credentials_found``: the key from Settings, the environment or an ``ant auth login`` profile). False is keyless mode — the window
        offers saved specs and Your library, and every Claude feature says it needs a key instead of failing."""
        from . import llm
        return self.client is not None or llm.credentials_found()

    def _templates(self, outcome: bool = True) -> List[Path]:
        """The files that are never part of the batch: the attached template and (``outcome``) every template the
        current spec reads — a spec may name a file of the batch folder as its template."""
        from .editspec import spec_templates
        return (([self.template.resolve()] if self.template else []) +
                (spec_templates(self.spec) if outcome and self.spec else []))

    def files(self, outcome: bool = True) -> List[Path]:
        """The batch: the folder's (or the set-aside pass's) .docx files without the templates. ``outcome=False``: the
        batch as it reads once the current outcome is void — what a new conversation starts over (``compile``), so its
        files never depend on the spec it replaces."""
        from .batch import expand_inputs
        from .editspec import is_template_file
        templates = self._templates(outcome)
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
        lines.append("Press Preview to compile an EditSpec tailored to these files." if self.claude_ready() else
                     "Load a saved spec or use Your library for them, then press Preview.")
        return "\n".join(lines)

    def new_out_dir(self) -> Path:
        self.out_dir = Path(self.out_root) / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir

    # ---- steps ----
    # ---- outcome: a spec XOR a custom transform, and what was previewed of it ----
    def _set_outcome(self, spec: Optional[Dict[str, Any]] = None, generated: Any = None, source: str = "",
                     recipe: str = "") -> None:
        """The one place the session's outcome changes. A new outcome voids everything previewed or approved for the
        old one: a reply that turns a custom transform into a spec must never leave the transform runnable, and a reply
        that only asks a question must never leave the old spec's preview runnable. ``recipe``: the spec is one of your
        recipes picked in the conversation (``recipe_name``; the Preview step and the visual check say so)."""
        if spec is not None and generated is not None:
            raise ValueError("a session runs either a spec or a custom transform, not both")
        self.spec, self.generated = spec, generated
        self.spec_source = source if spec is not None else ""
        self.recipe_name = recipe if spec is not None else ""
        self.preview, self.generated_token, self.generated_preview = None, "", None
        self.generated_inputs, self.generated_intent = {}, ""

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
        rather than starting over). Like is compared with like: the conversation's files go through the same template
        filter as ``files()`` — a spec Claude accepted may name a file of the batch folder as its template, and that
        file then leaves ``files()`` although nothing changed. A folder that cannot be read (renamed, a network share
        that dropped) is no match, never an exception."""
        from .editspec import is_template_file
        c = self.conversation
        if c is None or c.request.strip() != (request or "").strip():
            return False
        try:
            files = self.files()
            templates = self._templates()
            had = [Path(f).resolve() for f in c.files if not is_template_file(f, templates)]
            return ([Path(f).resolve() for f in files] == had
                    and (self.template.resolve() if self.template else None) ==
                    (c.template.resolve() if c.template else None))
        except (ValueError, OSError):
            return False

    def compile(self, request: str, log: Log) -> str:
        """Start a conversation about ``request`` over the current files. Returns a status line; sets ``spec`` when
        Claude's first answer is a spec, otherwise ``turn`` says what Claude needs from the user. It opens the session
        folder (``out_dir``): conversation.json and compile.json there after every turn, and the first preview or
        custom transform beside them (``_session_dir``)."""
        from .conversation import Conversation
        if not request.strip():
            raise ValueError("type what you want done (or load a spec file)")
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY)           # before anything changes: the outcome and its preview stay
        # The batch as it reads once the old outcome is void (just below), never through the outgoing spec: a file of
        # the folder that spec reads as its template is a file of this conversation, as conversation_matches sees it
        # from here on. Taken first, so a folder that cannot be read refuses before anything changes.
        files = self.files(outcome=False)
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
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY_REPLY)     # nothing said, nothing voided: the conversation waits for a key
        if not self.conversation_matches(c.request):
            raise ValueError("the files or the template changed since this conversation started — press Start over")
        chosen = c.choice(choice_id) if choice_id else None
        if self._compiled_dir is None:            # a preview or custom transform used the folder: its records stay
            self.chat_dir = self._compiled_dir = self._next_session_dir()   # as they were; this reply's go here
            log(f"The conversation continues in {self.chat_dir} (conversation.json, compile.json)")
        self._set_outcome()                       # whatever was previewed before this reply is void now
        from .conversation import LIBRARY_CHOICE_KINDS
        if chosen is not None and chosen.kind in LIBRARY_CHOICE_KINDS:
            return self._take_library_choice(c, chosen, log, render)
        if chosen is not None and chosen.kind == "custom_transform":
            queued = len(c.notes)
            c.take_app_choice(chosen.id)          # queues "…carried it out; the outcome follows." for Claude
            proposal = (c.unsupported or {}).get("proposal") or chosen.detail
            try:
                msg = self._codegen(c.request, proposal, self.files(), log, brief=c.brief(), render=render)
                gp = self.generated_preview
                c.note(f"The one-off custom transform {self.generated.name!r} passed verification on the sample "
                       f"files; the user reviews its preview.\n" + (gp.to_text()[:4000] if gp is not None else ""),
                       display=f"Custom transform {self.generated.name!r} passed verification on the sample files.")
            except TransformFailed as e:          # the recorded outcome (no attempt passed, or the call failed)
                c.note(str(e))
                self._save_chat()
                raise
            except Exception as e:                # a defect propagates, never told to Claude as a verification result;
                # the queued promise of an outcome is withdrawn — there is none — and Claude hears only that the app
                # stopped (the error's type, never its text: not a finding about the request). The transcript keeps
                # the user's choice; what was said is saved for the diagnosis.
                del c.notes[queued:]
                c.notes.append(f"The user chose: «{chosen.label}». The app could not finish the custom transform: it "
                               f"stopped with an internal error ({type(e).__name__}) before any result — nothing was "
                               f"applied.")
                self._save_chat()
                raise
            self._save_chat()
            return msg
        try:
            turn = c.reply(text, choice_id, client=self.client)
        except Exception:
            self._save_chat()
            raise
        return self._after_turn(turn)

    def _take_library_choice(self, c, chosen, log: Log, render: bool) -> str:
        """One of your recipes or operations that Claude offered and you picked. ``Conversation.take_library_choice``
        checks the item is exactly what was offered (a changed one is ``LibraryChanged``: nothing used, the conversation
        goes on) and approves the words the choice showed. A recipe becomes the outcome as the validated snapshot the
        choice holds (the window previews it next, as any spec; ``recipe_name`` says which). An operation follows
        ``use_operation``'s trial-first rule with the conversation kept: its trials in a new session folder, the outcome
        only when every sample passes, and Claude told either way — ``OperationFailed`` is the one failure it hears; a
        defect withdraws the promised outcome and is never told as a result (as for a custom transform)."""
        queued = len(c.notes)
        try:
            chosen, spec = c.take_library_choice(chosen.id)
        except Exception:                         # LibraryChanged (a recorded refusal) or a defect: both saved first
            self._save_chat()
            raise
        if chosen.kind == "recipe":
            self._set_outcome(spec=spec, source="compiled", recipe=chosen.name)
            self._save_chat()
            log(f"Using your recipe {chosen.name!r} as saved.")
            return f"Using your recipe {chosen.name!r} as saved — its preview follows."
        try:
            msg = self._use_saved_operation(chosen.name, log, render, end_conversation=False)
            gp = self.generated_preview
            c.note(f"Your operation {chosen.name!r} passed verification on the sample files; the user reviews its "
                   f"preview.\n" + (gp.to_text()[:4000] if gp is not None else ""),
                   display=f"Your operation {chosen.name!r} passed verification on the sample files.")
        except OperationFailed as e:              # the recorded outcome: told to Claude, nothing changed
            c.note(str(e))
            self._save_chat()
            raise
        except Exception as e:                    # a defect: the promised outcome is withdrawn, the type alone is said
            del c.notes[queued:]
            c.notes.append(f"The user chose: «{chosen.label}». The app could not finish trying your operation: it "
                           f"stopped with an internal error ({type(e).__name__}) before any result — nothing was "
                           f"applied.")
            self._save_chat()
            raise
        self._save_chat()
        return msg

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
        return self._use_folder(out)

    def _use_folder(self, out: Path) -> Path:
        """``out`` becomes the results folder (the next preview and run go there); the conversation's records follow."""
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
        from .preview import approval_inputs, approval_token, explain_approval_change
        samples = pick_samples(files)
        out = self._session_dir()                 # beside the conversation's records: codegen/history.json, preview/
        # The files as they are BEFORE the transform is written and tried on them (several calls to Claude). Its token
        # needs its code, known only after; a file whose stats moved in between was tried with its old content, so the
        # token taken then must never approve it: it is left empty — Run refuses, and Preview tries the transform again
        # on the files as they are (``make_preview``; the window's reply does it at once) — as a file edited during
        # ``_prove``'s trials leaves its token stale.
        before = approval_inputs({}, files)
        log(f"Writing a one-off transform, tested on {[s.name for s in samples]}…")
        g = generate_op(request, proposal or "", samples, out / "codegen", client=self.client, log=log, brief=brief)
        if not g.passed:
            raise TransformFailed("; ".join(g.errors or [f"attempt {a['attempt']}: {'; '.join(a['problems'][:2])}"
                                                         for a in g.attempts[-1:]]) or "no transform was submitted")
        intent = "\n".join([f"A ONE-OFF CUSTOM TRANSFORM ({g.name}) for: {request}", f"WHAT IT SHOULD DO: {proposal}"]
                           + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
        key = self._generated_key(g)
        try:
            token = approval_token(key, files)
        except OSError:
            token = ""                            # a file vanished while the transform was written
        inputs = approval_inputs(key, files)
        moved = explain_approval_change({"files": before["files"]}, {"files": inputs["files"]},
                                        since="the custom transform was started")
        if moved:
            token, inputs = "", {**inputs, "files": before["files"]}   # Run names what moved since it was tried
            log("A file changed while the custom transform was being written and tried — it runs only once Preview has "
                "tried it again on the files as they are now:" + "".join(f"\n  • {w}" for w in moved))
        self._show_generated(g, out, token, inputs, intent, render, log)
        return (f"An AI-written transform ({g.name}) passed verification on {len(samples)} sample file(s). "
                f"It is saved at {g.path} — Run writes edited copies only (never over your files)." +
                ("\nA file changed while it was written: press Preview to try it again on the files as they are now."
                 if moved else ""))

    def _prove(self, g, files: List[Path], samples: List[Path], folder: Path, refusal: str, exc=TrialsFailed):
        """Try ``g`` on ``samples`` (sandbox + the full suite) in ``folder``. The approval token and inputs of ``files``
        are taken BEFORE the trials, so a file that changes while they run voids them. Raises ``exc`` (``TrialsFailed``,
        a RuntimeError) naming every sample that fails; changes nothing in the session. Returns (token, inputs,
        trials)."""
        from .codegen import trial
        from .preview import approval_inputs, approval_token
        key = self._generated_key(g)
        token, inputs = approval_token(key, files), approval_inputs(key, files)
        trials = [trial(g.source, f, g.select, g.params, folder / f"{i:02d}") for i, f in enumerate(samples)]
        bad = [f"{t.source.name}: {'; '.join(t.problems[:2])}" for t in trials if not t.passed]
        if bad:
            raise exc(f"{refusal} — " + " | ".join(bad))
        return token, inputs, trials

    def _show_generated(self, g, out: Path, token: str, inputs: Dict[str, Any], intent: str, render: bool,
                        log: Log) -> None:
        """``g``, passed on the samples of the files ``token`` is of, becomes the session's outcome: its preview in
        ``out/preview`` (with Claude's visual check when rendering), then the approval token — set last, so a preview
        that stops leaves nothing runnable (Preview tries the transform again)."""
        from .preview import preview_generated
        self._set_outcome(generated=g)
        self.generated_intent = intent
        self.generated_preview = preview_generated(g, out / "preview", "auto", render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        if render and self.visual_check:
            self._visual_check(self.generated_preview, intent, out / "preview", log)
        self.generated_token, self.generated_inputs = token, inputs

    def _preview_again(self, render: bool, log: Log) -> None:
        """The custom transform's files are as it was tried, but its preview was made with Render pages set the other way
        (or is missing): make the preview again from the same passing trials, in a new folder, with page images and
        Claude's visual check when rendering — never reuse a preview made without them. The token stays the same (the
        files have not changed); ``_show_generated`` voids the old preview and its acknowledgement and sets it last."""
        g = self.generated
        token, inputs = self.generated_token, self.generated_inputs
        out = self._next_session_dir()
        log(f"Making the preview of the custom transform {g.name!r} again "
            f"({'with' if render else 'without'} page images)…")
        intent = self.generated_intent or f"A CUSTOM TRANSFORM ({g.name}) for: {g.request}\nWHAT IT DOES: {g.proposal}"
        self._use_folder(out)
        self._show_generated(g, out, token, inputs, intent, render, log)

    def _prove_again(self, files: List[Path], render: bool, log: Log) -> None:
        """A file changed (or was added or removed) since the custom transform was tried: try it again on the samples of
        the files as they are now, in a new folder, and preview that — the token is then current. One that no longer
        passes is refused, and the session stays as it was (Run keeps refusing)."""
        from .codegen import pick_samples
        g = self.generated
        samples = pick_samples(files)
        out = self._next_session_dir()            # a new folder: the old preview stays as it was until this passes
        log(f"The files changed since the custom transform {g.name!r} was tried — trying it again on "
            f"{[f.name for f in samples]}…")
        intent = self.generated_intent or f"A CUSTOM TRANSFORM ({g.name}) for: {g.request}\nWHAT IT DOES: {g.proposal}"
        token, inputs, trials = self._prove(g, files, samples, out / "retrial",
                                            f"the custom transform {g.name!r} does not pass on the files as they are now")
        g.trials = trials
        self._use_folder(out)
        self._show_generated(g, out, token, inputs, intent, render, log)

    # ---- your library ----
    def load_recipe(self, name: str) -> Dict[str, Any]:
        from .library import load_recipe
        return self.load_spec(load_recipe(name))

    def use_operation(self, name: str, log: Log, render: bool = False) -> str:
        """Run one of your saved operations on this batch (Your library…): first on its sample files (sandbox + the
        full suite — it must pass here, not only where it was made), then its preview; Run writes edited copies only.
        The trials run in a new folder; only an operation that passes there replaces the session's outcome and ends its
        conversation — one that does not pass (``OperationFailed``) leaves the conversation, the outcome, its preview
        and ``out_dir`` as they were. Picked in the conversation instead, the same rule keeps the conversation
        (``_take_library_choice``)."""
        return self._use_saved_operation(name, log, render, end_conversation=True)

    def _use_saved_operation(self, name: str, log: Log, render: bool, end_conversation: bool) -> str:
        from .codegen import pick_samples
        from .library import load_operation
        g = load_operation(name)                  # the manifest's sha256 and the whitelist, again, now
        files = self.files(outcome=False)         # the batch it runs once it replaces the outcome: a file the outgoing
        samples = pick_samples(files)             # spec read as its template is one of them (compile's rule)
        out = self._next_session_dir()            # never the current folder: nothing is replaced before it passes
        log(f"Trying your operation {name!r} on {[f.name for f in samples]}…")
        token, inputs, trials = self._prove(g, files, samples, out / "library-trial",
                                            f"your operation {name!r} does not pass on these files", OperationFailed)
        g.passed, g.trials = True, trials
        if end_conversation:
            self.end_conversation()               # Your library…: a saved operation is not part of the conversation
        self._use_folder(out)
        self._show_generated(g, out, token, inputs, f"YOUR SAVED OPERATION {name!r} for: {g.request}\n"
                                                    f"WHAT IT DOES: {g.proposal}", render, log)
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
        from .preview import approval_token, make_preview
        if self.generated is not None:
            files = self.files()
            if approval_token(self._generated_key(self.generated), files) != self.generated_token:
                self._prove_again(files, render, log)   # stale: Run would refuse forever — re-prove, then preview
            elif (self.generated_preview is None
                  or bool(getattr(self.generated_preview, "rendered", False)) != bool(render)):
                self._preview_again(render, log)        # Render pages changed: pages and the visual check follow it
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
        if self.recipe_name:
            lines.insert(1, f"YOUR RECIPE «{self.recipe_name}» (used as saved)")
        lines += [f"ASSUMPTION: {a}" for a in spec.get("assumptions") or []]
        if self.conversation is not None:
            lines.append("THE CONVERSATION:\n" + self.conversation.brief())
        return "\n".join(lines)

    def _visual_check(self, pv, intent: str, folder: Path, log: Log) -> None:
        from . import compiler, vision
        from .preview import write_preview_files
        client = self.client
        if not self.claude_ready():               # keyless: said on the Preview step, nothing prepared or sent
            log(f"Claude's visual check skipped: {NO_KEY_REVIEW}")
            pv.review = vision.VisualReview(skipped=NO_KEY_REVIEW)
        else:
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
        head = (f"THE PREVIEW OF YOUR RECIPE «{self.recipe_name}» (used as saved):\n" if self.recipe_name
                else "THE PREVIEW OF YOUR SPEC:\n")
        self.conversation.note(head + pv.to_text()[:6000],
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
                         "on only those files" + (", or 'Advise on exceptions' for per-file advice." if
                                                  self.claude_ready() else "."))
        return "\n".join(lines)

    def advise(self, log: Log) -> str:
        from .advisor import advise_batch
        if not self.last_run_dir:
            raise ValueError("run something first")
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY_ADVICE)
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
