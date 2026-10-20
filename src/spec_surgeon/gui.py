"""The engine behind the Spec Surgeon window (docs/history/NL_SURGICAL_EDITOR_PLAN.md Phase 7), and its entry point.

    spec-surgeon-gui            (no console window on Windows; the installed app's SpecSurgeon.exe)
    spec-surgeon gui            (from a terminal)

``Session`` is a toolbox of named steps over the attached documents (``documents=[...]``, from any folders) and one
optional template: the conversation with Claude (``compile``, ``reply``), ``make_preview``, ``run`` (with the
approval-token check), custom transforms and the recovery of set-aside files. Each step is a plain call into the
engine with an explicit result, and ``Session.outcome`` is the one decision table that says what is needed next.
Nothing is applied before a preview, and ``run`` refuses when a file changed since the preview. The stages the user
sees belong to ``document_task.DocumentTask``, which also triages every attachment; the one window is
``ui/conversation_window.py``. This module has no widgets and no Qt, so it is tested on its own and the command line
never loads Qt.

The window itself (PySide6 / Qt 6) is the ``ui`` package: ``main()`` and ``build_window()`` below import it lazily
and explain how to install Qt when it is missing (``pip install "spec-surgeon[gui]"``).
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from functools import wraps
from inspect import signature
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import diagnostics as diag
from .settings import default_run_root

Log = Callable[[str], None]

# Running without an API key (``Session.claude_ready`` False): what each Claude feature says instead of calling. The
# window asks for the key in Settings before a conversation starts; the engine's own steps (plan, apply, verify) need
# no key.
NEEDS_KEY = "Connect your Anthropic API key in Settings to start the conversation."
NEEDS_KEY_REPLY = "Connect your Anthropic API key in Settings to continue this conversation."
NO_KEY_REVIEW = ("Claude's visual check needs an Anthropic API key (Settings) — look at the before/after pages "
                 "yourself.")


class TransformFailed(RuntimeError):
    """No custom transform passed verification: every attempt the writer submitted failed on the samples, or a call to
    Claude failed (an API error, recorded in ``codegen/history.json``). A recorded outcome — ``Session.reply`` tells
    Claude about it; any other exception raised while a transform is made is a defect and is never told to Claude as
    a verification result."""
    PREFIX = "No custom transform passed verification: "

    def __init__(self, details: str, *, recoverable: bool = False):
        self.details = details
        self.recoverable = recoverable
        super().__init__(self.PREFIX + details)


class TrialsFailed(RuntimeError):
    """A transform tried on the batch's sample files (``Session._prove``: sandbox + the full suite) did not pass on
    every one — a recorded refusal naming each failing sample, never a defect. Nothing changed in the session."""




# ---- the logic (no widgets; tested on its own) ----------------------------------------------------

def _user_request(conversation) -> str:
    """The request and the user's own replies, never the model's words: what a custom method's approval binds."""
    replies = [entry.text for entry in conversation.entries[1:] if entry.role == "user"]
    return conversation.request + ("\n\nUSER DECISIONS:\n" + "\n".join(replies) if replies else "")


def _session_action(fn):
    """Trace semantic actions, including refusals before a preview/results folder exists.

    Only option values and state metadata go into the application event stream. Request/reply text, model clients,
    document contents and the return value belong in their existing records, never in this metadata snapshot.
    """
    sig = signature(fn)

    @wraps(fn)
    def recorded(self, *args, **kwargs):
        options = {}
        try:
            arguments = sig.bind(self, *args, **kwargs).arguments
            for key in ("render", "in_place", "replace", "tracked", "position", "choice_id"):
                if key in arguments:
                    options[key] = arguments[key]
            for key in ("request", "text", "proposal"):
                if isinstance(arguments.get(key), str):
                    options[key + "_characters"] = len(arguments[key])
        except Exception:
            pass                              # a diagnostic never changes argument validation or the action
        with diag.scope(session_id=self.diagnostic_id, component="session"):
            with diag.span("session." + fn.__name__.lstrip("_"), options=options,
                           before=self._diagnostic_state()) as completion:
                try:
                    return fn(self, *args, **kwargs)
                finally:
                    completion["after"] = self._diagnostic_state()
    return recorded


@dataclass
class Outcome:
    """What the session needs next, decided in one place (``Session.outcome``)."""
    kind: str                                          # empty | auto | question | preview | resume
    message: str = ""                                  # a sentence for the user (question, resume)
    questions: List[str] = field(default_factory=list)
    choices: List[Any] = field(default_factory=list)   # conversation.Choice the user may pick
    auto_choice: str = ""                              # kind auto: the app choice to take now
    needs_preview: bool = False                        # kind auto: an outcome exists but has no preview yet
    preview: Any = None                                # kind preview: what the user approves
    token: str = ""
    blocking: bool = False                             # the preview carries blocking visual findings
    partial: bool = False                              # kind question: some copies are ready to save meanwhile


@dataclass
class Session:
    """What the window is working on. Every step is a call into the engine; the window only shows the text."""
    documents: List[Path] = field(default_factory=list)   # the attached documents, in the order the user gave
    template: Optional[Path] = None
    inputs: List[Path] = field(default_factory=list)      # pictures and tables the user attached for the edit to use
    out_root: Path = field(default_factory=default_run_root)
    spec: Optional[Dict[str, Any]] = None
    out_dir: Optional[Path] = None
    preview: Any = None
    generated: Any = None                         # Tier B op, when the request needed one
    generated_preview: Any = None                 # preview.GeneratedPreview: its samples, renders, visual check
    last_run_dir: Optional[Path] = None
    questions: List[str] = field(default_factory=list)
    generated_token: str = ""                     # approval token of the files a generated transform was tested on
    client: Any = None                            # injectable for tests
    last_batch: Any = None                        # BatchResult of the last Run (the window's results table)
    last_trials: Any = None                       # List[codegen.FileTrial] of the last AI-written-transform run
    visual_check: bool = True                     # Claude reviews the rendered sample pages of each preview
    conversation: Any = None                      # conversation.Conversation of the current request
    turn: Any = None                              # its last conversation.Turn
    chat_dir: Optional[Path] = None               # the session folder holding the conversation's records
                                                  # (conversation.json, compile.json); it follows each new folder
    _compiled_dir: Optional[Path] = None          # the session folder the next preview or custom transform goes into
                                                  # (the compile record sits beside its preview); None once used
    generated_inputs: Dict[str, Any] = field(default_factory=dict)   # approval_inputs beside generated_token
    generated_intent: str = ""                    # what the custom transform's visual check is told it should do
    recovery: Any = None                          # investigated copies, awaiting review and export
    last_recovery: Any = None                     # recovery whose ready copies were exported
    pending_custom_transform: Optional[Dict[str, Any]] = None
    replay_word_sources: Optional[List[str]] = None
    recovery_checkpoint: Optional[Path] = None  # current durable investigation, including in-flight work
    pending_layout_recovery: List[Path] = field(default_factory=list)   # layout batch folders still to recover,
                                                  # in plan order (a plan per layout recovers one layout at a time)
    diagnostic_id: str = field(default_factory=diag.new_id, init=False, repr=False, compare=False)

    def _diagnostic_state(self) -> Dict[str, Any]:
        """Small metadata snapshot; no file reads, credential checks, document text or expensive approval hashing."""
        try:
            pv, approval = self._current_review()
            return {"outcome": ("recovery" if self.recovery is not None else
                                "generated" if self.generated is not None else "spec" if self.spec is not None else
                                "none"),
                    "document_count": len(self.documents),
                    "template": str(self.template) if self.template else None,
                    "out_dir": str(self.out_dir) if self.out_dir else None,
                    "chat_dir": str(self.chat_dir) if self.chat_dir else None,
                    "last_run_dir": str(self.last_run_dir) if self.last_run_dir else None,
                    "conversation_open": self.conversation is not None,
                    "turn_status": getattr(self.turn, "status", None),
                    "preview_available": pv is not None, "approval_digest": approval,
                    "review_acknowledged": bool(approval and getattr(pv, "review_ack", "") == approval)}
        except Exception:
            return {"snapshot_unavailable": True}

    def claude_ready(self) -> bool:
        """Claude can be called: a client was given (tests, scripted) or credentials are there
        (``llm.credentials_found``: the key from Settings, the environment or an ``ant auth login`` profile). False is keyless mode — the window
        says "Connect your Anthropic API key in Settings" instead of sending, and every Claude feature says it needs a
        key instead of failing."""
        from . import llm
        return self.client is not None or llm.credentials_found()

    def _templates(self, outcome: bool = True) -> List[Path]:
        """The files that are never part of the batch: the attached template and (``outcome``) every template the
        current spec reads — a spec may name one of the attached documents as its template."""
        from .editspec import spec_templates
        from .layouts import is_layout_plan, layout_spec
        own: List[Path] = []
        if outcome and self.spec:
            own = ([t for layout in self.spec["layouts"] for t in spec_templates(layout_spec(self.spec, layout))]
                   if is_layout_plan(self.spec) else spec_templates(self.spec))
        return ([self.template.resolve()] if self.template else []) + own

    def files(self, outcome: bool = True) -> List[Path]:
        """The attached documents without the templates, in the order the user attached them. ``outcome=False``: the
        batch as it reads once the current outcome is void (what a new conversation starts over), so its files never
        depend on the spec it replaces.

        A copy of the user's own attached template among the documents is refused: they attached the same file twice,
        once to edit and once to copy from, and only they can say which they meant. A document the current spec reads
        as its template is filtered, not refused — it is the spec's choice, nothing the user did, and ``compile`` and
        ``conversation_matches`` compare like with like through this same filter."""
        from .editspec import is_template_file
        own = [self.template.resolve()] if self.template else []
        spec_reads = [t for t in self._templates(outcome) if t not in own]
        files: List[Path] = []
        for value in self.documents:
            path = Path(value).resolve()
            if path.suffix.lower() != ".docx" or path.name.startswith("~$"):
                raise ValueError(f"Attach a Word .docx document: {path.name}")
            if not path.is_file():
                raise ValueError(f"An attached document is missing: {path}")
            if path in files:
                continue
            if own and is_template_file(path, own):
                raise ValueError(f"{path.name} is also a copy of the reference template. "
                                 "Remove it from the documents to edit, or choose a different template.")
            if spec_reads and is_template_file(path, spec_reads):
                continue
            files.append(path)
        if not files:
            raise ValueError("Add the Word documents you want to edit.")
        return files

    def new_out_dir(self) -> Path:
        self.out_dir = Path(self.out_root) / datetime.now().strftime("%Y%m%d-%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir

    # ---- steps ----
    # ---- outcome: a spec XOR a custom transform, and what was previewed of it ----
    @_session_action
    def _set_outcome(self, spec: Optional[Dict[str, Any]] = None, generated: Any = None, source: str = "") -> None:
        """The one place the session's outcome changes. A new outcome voids everything previewed or approved for the
        old one: a reply that turns a custom transform into a spec must never leave the transform runnable, and a reply
        that only asks a question must never leave the old spec's preview runnable. The outcome is a spec (or a plan
        per layout) XOR a custom transform."""
        if spec is not None and generated is not None:
            raise ValueError("a session runs either a spec or a custom transform, not both")
        self.spec, self.generated = spec, generated
        self.recovery = None
        self.recovery_checkpoint = None
        self.pending_layout_recovery = []
        self.preview, self.generated_token, self.generated_preview = None, "", None
        self.generated_inputs, self.generated_intent = {}, ""
        self.pending_custom_transform = None

    def outcome_key(self) -> Any:
        """What the approval token is taken over beside the files: the spec (or the plan per layout)."""
        return self.spec

    @_session_action
    def end_conversation(self) -> None:
        self.conversation, self.turn, self.questions = None, None, []
        self._compiled_dir = None                 # the next preview or custom transform opens a new folder

    # ---- the conversation ----
    def conversation_matches(self, request: str) -> bool:
        """The current conversation is about this request, these files and this template (so Preview continues it
        rather than starting over). Like is compared with like: the conversation's files go through the same template
        filter as ``files()`` — a spec Claude accepted may name one of the attached documents as its template, and that
        file then leaves ``files()`` although nothing changed. An attached document that cannot be read (moved, a
        network share that dropped) is no match, never an exception."""
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
                    (c.template.resolve() if c.template else None)
                    and [Path(p).resolve() for p in self.inputs] == [Path(p).resolve() for p in c.batch.inputs])
        except (ValueError, OSError):
            return False

    @_session_action
    def compile(self, request: str, log: Log) -> str:
        """Start a conversation about ``request`` over the current files. Returns a status line; sets ``spec`` when
        Claude's first answer is a spec, otherwise ``turn`` says what Claude needs from the user. It opens the session
        folder (``out_dir``): conversation.json and compile.json there after every turn, and the first preview or
        custom transform beside them (``_session_dir``)."""
        from .conversation import Conversation
        if not request.strip():
            raise ValueError("Type what you want done.")
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY)           # before anything changes: the outcome and its preview stay
        # The batch as it reads once the old outcome is void (just below), never through the outgoing spec: an attached
        # document that spec reads as its template is a file of this conversation, as conversation_matches sees it
        # from here on. Taken first, so a document that cannot be read refuses before anything changes.
        files = self.files(outcome=False)
        self._set_outcome()
        self.end_conversation()
        out = self.out_dir = self._next_session_dir()   # the session folder: conversation.json and compile.json,
        self.chat_dir = self._compiled_dir = out        # then preview/, codegen/ and run/ beside them (bundle_root)
        log(f"Talking the request through with Claude over {len(files)} file(s)…")
        self.conversation = Conversation(request, files, template=self.template, client=self.client,
                                         work_dir=out / "chat", log=log, continuous_recovery=True,
                                         word_sources=self.replay_word_sources, input_files=self.inputs)
        try:
            turn = self.conversation.start()
        except Exception:
            self._save_chat()                     # what was said before a defect stopped it, for the diagnosis
            raise
        msg = self._after_turn(turn)
        if (out / "compile.json").is_file():
            log(f"Compile record (the model's whole exchange): {out / 'compile.json'}")
        return msg

    @_session_action
    def reply(self, text: str = "", choice_id: Optional[str] = None, log: Log = lambda m: None,
              render: bool = False) -> str:
        """The user's next message in the conversation — typed words, a picked choice, or both."""
        c = self.conversation
        if c is None:
            raise ValueError("there is no conversation yet — describe the change first")
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY_REPLY)     # nothing said, nothing voided: the conversation waits for a key
        if not self.conversation_matches(c.request):
            raise ValueError("the files or the template changed since this conversation started — describe the change again")
        chosen = c.choice(choice_id) if choice_id else None
        if chosen is not None and chosen.kind == "recovery_target":
            from .continuous_recovery import resume_choice, result_turn
            result = resume_choice(c, chosen.id)
            self._adopt_recovery(result, keep_conversation=True)
            self.turn = result_turn(c, result)
            self._save_chat()
            return result.to_text()
        if (self.recovery is not None and c.recovery_active and text and chosen is None
                and any(f.status != "READY" for f in self.recovery.files)
                and not any(q.get("kind") == "goal_binding" and q.get("status") == "open"
                            for q in self.recovery.question_records)):
            from .recovery import resume_recovery
            from .continuous_recovery import result_turn
            result = resume_recovery(self.recovery.out_dir, request=text, client=self.client, log=log)
            c.sources.append(text)
            self._adopt_recovery(result, keep_conversation=True)
            self.turn = result_turn(c, result)
            self._save_chat()
            return result.to_text()
        if self._compiled_dir is None:            # a preview or custom transform used the folder: its records stay
            self.chat_dir = self._compiled_dir = self._next_session_dir()   # as they were; this reply's go here
            log(f"The conversation continues in {self.chat_dir} (conversation.json, compile.json)")
        self._set_outcome()                       # whatever was previewed before this reply is void now
        if chosen is not None and chosen.kind == "recovery_plan":
            from .continuous_recovery import approve, execute, result_turn, options_for
            snapshot = approve(c, chosen.id)
            options = options_for(c, render=render)
            options.visual_review = self.visual_check
            self.recovery_checkpoint = self.chat_dir / f"recovery-{chosen.id}"
            result = execute(c, snapshot, self.recovery_checkpoint, options=options)
            self._adopt_recovery(result, keep_conversation=True)
            self.turn = result_turn(c, result)
            self._save_chat()
            return result.to_text()
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
                if c.continuous_recovery and e.recoverable:
                    self._set_outcome()
                    return self._after_turn(c.investigate_failure({"reason": str(e), "proposal": proposal}))
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

    @_session_action
    def resume_conversation(self, log: Log = lambda m: None) -> str:
        """Continue the conversation's turn that stopped before Claude answered (a paused allowance, a stop, a failed
        call), with everything it had investigated, instead of starting the request over."""
        c = self.conversation
        if c is None or not c.resumable():
            raise ValueError("there is no stopped conversation turn to continue")
        if not self.claude_ready():
            raise ValueError(NEEDS_KEY_REPLY)
        if not self.conversation_matches(c.request):
            raise ValueError("the files or the template changed since this conversation started — describe the change again")
        if self._compiled_dir is None:
            self.chat_dir = self._compiled_dir = self._next_session_dir()
        c.log = log
        log("Continuing where the conversation stopped…")
        try:
            turn = c.resume()
        except Exception:
            self._save_chat()
            raise
        return self._after_turn(turn)

    def _after_turn(self, turn) -> str:
        self.turn = turn
        self.questions = list(turn.questions)
        diag.event("session.conversation.turn", status=turn.status, question_count=len(self.questions),
                   choice_count=len(getattr(turn, "choices", [])), has_spec=getattr(turn, "spec", None) is not None)
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

    @_session_action
    def _save_chat(self) -> None:
        """conversation.json and compile.json (the request, the answers and the outcome of the conversation's last turn
        with the model's whole exchange) in the current session folder; a record that cannot be written never stops
        the work."""
        c = self.conversation
        if c is None or self.chat_dir is None:
            return
        try:
            c.save(self.chat_dir / "conversation.json")
        except OSError as exc:
            diag.event("session.conversation.save_failed", level="warning",
                       path=str(self.chat_dir / "conversation.json"),
                       error=diag.exception_record(exc, "save-conversation"))
        if c.turn is not None:
            from .diagnostics import write_json
            write_json(self.chat_dir / "compile.json", {"request": c.request, "answers": list(c.answers),
                                                        **c.compile_result(c.turn).to_dict()})

    def _session_dir(self) -> Path:
        """The folder the next preview or custom transform goes into: the conversation's folder while nothing has used
        it yet, else a new one. The conversation's records
        follow it, so every session folder holds the exchange that led to its preview and run (diagnostics.bundle_root
        bundles them together); once used, a folder's records are frozen (``reply`` opens the next folder before
        anything more is said)."""
        if self.conversation is None:
            out = self.new_out_dir()
        else:
            out = self._compiled_dir if self._compiled_dir is not None else self._next_session_dir()
        return self._use_folder(out)

    @_session_action
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
        from .preview import generated_key
        return generated_key(g)

    @_session_action
    def _codegen(self, request: str, proposal: str, files: List[Path], log: Log, brief: Optional[str] = None,
                 render: bool = False) -> str:
        from .preview import approval_token
        out = self._session_dir()
        key = {"request": _user_request(self.conversation) if self.conversation else request, "custom_transform": True}
        self.pending_custom_transform = {"request": request, "proposal": proposal, "files": files,
            "brief": brief, "out": out, "key": key, "token": approval_token(key, files)}
        return self.continue_custom_transform(log, render)

    @_session_action
    def continue_custom_transform(self, log: Log, render: bool = False) -> str:
        pending = self.pending_custom_transform
        if pending is None:
            raise ValueError("There is no custom transform awaiting preparation.")
        from .preview import approval_token
        if approval_token(pending["key"], self.files()) != pending["token"]:
            raise ValueError("The original files changed; press Preview to prepare the transform again.")
        message = self._generate_custom(pending, log, render)
        if self.conversation is not None:
            self.conversation.note("The custom transform passed its independent sample verification.",
                                   display="Custom transform prepared; review the resulting preview before Run.")
            self._save_chat()
        return message

    def _generate_custom(self, pending, log: Log, render: bool) -> str:
        from .codegen import generate_op, pick_samples
        from .preview import approval_inputs, _approval_token_from_inputs, explain_approval_change
        request, proposal, files, brief, out = (pending[k] for k in ("request", "proposal", "files", "brief", "out"))
        pending["attempts"] = pending.get("attempts", 0) + 1
        attempt = pending["attempts"]
        samples = pick_samples(files)
        # The files as they are BEFORE the transform is written and tried on them (several calls to Claude). Its token
        # needs its code, known only after; a file whose stats moved in between was tried with its old content, so the
        # token taken then must never approve it: it is left empty — Run refuses, and Preview tries the transform again
        # on the files as they are (``make_preview``; the window's reply does it at once) — as a file edited during
        # ``_prove``'s trials leaves its token stale.
        before = approval_inputs({}, files)
        log(f"Writing a one-off transform, tested on {[s.name for s in samples]}…")
        writer_dir = out / ("codegen" if attempt == 1 else f"codegen-{attempt:02d}")
        g = generate_op(request, proposal or "", samples, writer_dir, client=self.client, log=log, brief=brief,
                        approved_words=list(self.conversation.sources if self.conversation else [request]),
                        )
        if not g.passed:
            raise TransformFailed("; ".join(g.errors or [f"attempt {a['attempt']}: {'; '.join(a['problems'][:2])}"
                for a in g.attempts[-1:]]) or "no transform was submitted",
                recoverable=bool(g.attempts) and not any("error" in e for e in g.transcript))
        intent = "\n".join([f"A ONE-OFF CUSTOM TRANSFORM ({g.name}) for: {request}", f"WHAT IT SHOULD DO: {proposal}"]
                           + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
        key = self._generated_key(g)
        inputs = approval_inputs(key, files)
        try:
            token = _approval_token_from_inputs(key, inputs)
        except OSError:
            token = ""                            # a file vanished while the transform was written
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

    @_session_action
    def _prove(self, g, files: List[Path], samples: List[Path], folder: Path, refusal: str, exc=TrialsFailed):
        """Try ``g`` on ``samples`` (sandbox + the full suite) in ``folder``. The approval token and inputs of ``files``
        are taken BEFORE the trials, so a file that changes while they run voids them. Raises ``exc`` (``TrialsFailed``,
        a RuntimeError) naming every sample that fails; changes nothing in the session. Returns (token, inputs,
        trials)."""
        from .codegen import trial
        from .preview import approval_inputs, _approval_token_from_inputs
        key = self._generated_key(g)
        inputs = approval_inputs(key, files)
        token = _approval_token_from_inputs(key, inputs)
        trials = [trial(g.source, f, g.select, g.params, folder / f"{i:02d}",
                        **({"goal_contract": g.goal_contract, "goal_binding": g.goal_bindings.get(str(f))}
                           if getattr(g, "goal_contract", None) is not None else {})) for i, f in enumerate(samples)]
        bad = [f"{t.source.name}: {'; '.join(t.problems[:2])}" for t in trials if not t.passed]
        if bad:
            raise exc(f"{refusal} — " + " | ".join(bad))
        return token, inputs, trials

    @_session_action
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

    @_session_action
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

    @_session_action
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

    # ---- the preview: a spec, a plan per layout (layouts.py) or a custom transform, tried on samples ----
    @_session_action
    def make_preview(self, render: bool, log: Log) -> str:
        from .preview import approval_token, make_preview
        if self.pending_custom_transform is not None:
            pending = self.pending_custom_transform
            pending["files"] = self.files()
            pending["token"] = approval_token(pending["key"], pending["files"])
            return self.continue_custom_transform(log, render)
        if self.generated is not None:
            files = self.files()
            if approval_token(self._generated_key(self.generated), files) != self.generated_token:
                self._prove_again(files, render, log)   # stale: Run would refuse forever — re-prove, then preview
            elif (self.generated_preview is None
                  or bool(getattr(self.generated_preview, "rendered", False)) != bool(render)):
                self._preview_again(render, log)        # Render pages changed: pages and the visual check follow it
            gp = self.generated_preview
            return ((gp.to_text() + "\n\n") if gp is not None else "") + \
                "AI-written transform verified. Review its preview before Run."
        if self.spec is None:
            raise ValueError("nothing to preview — describe the change first")
        files = self.files()                      # the same filtered set Run checks and runs (no template files)
        out = self._session_dir()                 # beside the conversation's records
        from .layouts import is_layout_plan, make_layout_preview
        if is_layout_plan(self.spec):
            n = len(self.spec["layouts"])
            log(f"Planning {n} layout(s) on their own files and trying one sample per layout…")
            self.preview = make_layout_preview(self.spec, [str(f) for f in files], out / "preview", renderer="auto",
                                               do_render=render)
        else:
            log("Planning every file and trying the samples…")
            self.preview = make_preview(self.spec, [str(f) for f in files], out / "preview", renderer="auto",
                                        do_render=render)
        log(f"Preview log: {out / 'preview' / 'logs' / 'preview.log'}")
        if render and self.visual_check:
            self._visual_check(self.preview, self._intent(), out / "preview", log)
        self._note_preview()
        c = self.conversation
        if c is not None and c.continuous_recovery and (
                any(f.status == "exception" for f in self.preview.batch_plan.files)
                or any(not s.passed for s in self.preview.samples)):
            evidence = {"reason": self.preview.to_text(), "candidate": copy.deepcopy(self.spec)}
            self._set_outcome()
            return self._after_turn(c.investigate_failure(evidence))
        return self.preview.to_text()

    # ---- Claude's visual check: findings accompany preview consent ----
    def _intent(self) -> str:
        spec = self.spec or {}
        lines = [f"REQUEST: {spec.get('request') or ''}", f"WHAT THE SPEC DOES: {spec.get('summary') or ''}"]
        for layout in spec.get("layouts") or []:
            lines.append(f"LAYOUT {layout.get('id')} ({layout.get('label') or ''}): {layout['spec'].get('summary') or ''}")
            lines += [f"  ASSUMPTION: {a}" for a in layout["spec"].get("assumptions") or []]
        lines += [f"ASSUMPTION: {a}" for a in spec.get("assumptions") or []]
        if self.conversation is not None:
            lines.append("THE CONVERSATION:\n" + self.conversation.brief())
        return "\n".join(lines)

    @_session_action
    def _visual_check(self, pv, intent: str, folder: Path, log: Log) -> None:
        from . import compiler, vision
        from .preview import write_preview_files
        client = self.client
        if not self.claude_ready():               # keyless: said on the Preview step, nothing prepared or sent
            log(f"Claude's visual check skipped: {NO_KEY_REVIEW}")
            pv.review = vision.VisualReview(skipped=NO_KEY_REVIEW)
        else:
            pv.review = vision.review_samples(intent, pv.samples, folder, lambda: client or compiler._client(), log=log,
                                             model=getattr(self.conversation, "model", None))
        vision.save_review(pv.review, folder)
        write_preview_files(pv)

    def _current_review(self):
        """(the preview object that carries the visual check, the token it was made for)."""
        if self.pending_custom_transform is not None:
            return None, self.pending_custom_transform["token"]
        if self.recovery is not None:
            return self.recovery, self.recovery.token
        if self.generated is not None:
            return self.generated_preview, self.generated_token
        if self.preview is not None:
            return self.preview, self.preview.token
        return None, ""

    def outcome(self) -> "Outcome":
        """The one decision table: what the session needs next, from the session's own state. The coordinator and
        the window act on this and never probe the fields themselves."""
        from .conversation import APP_CHOICE_KINDS
        if self.pending_custom_transform is not None:
            return Outcome("resume", message="A custom method was being prepared; continue to finish it.")
        if self.recovery is not None:
            r = self.recovery
            review = getattr(r, "review", None)
            blocking = bool(review is not None and review.blocking())
            if any(f.status != "READY" for f in r.files):
                questions = list(r.questions) or [f"{Path(f.source).name}: {f.reason}" for f in r.files
                                                  if f.status != "READY"]
                return Outcome("question", message="Some documents still need attention.", questions=questions,
                               preview=r, token=r.token, blocking=blocking, partial=bool(r.ready))
            return Outcome("preview", preview=r, token=r.token, blocking=blocking)
        turn = self.turn
        if turn is not None and self.generated is None:
            app = [c for c in turn.choices if c.kind in ("custom_transform", "recovery_plan")]
            if app:
                return Outcome("auto", auto_choice=app[0].id)
        # An outcome (a spec, a chain, a prepared custom method) outranks the turn that led to it: a reply voids the
        # old outcome before a new turn can ask anything, so an outcome and an open question never coexist.
        if self.spec is not None or self.generated is not None:
            pv, token = self._current_review()
            if pv is None:
                return Outcome("auto", needs_preview=True)
            review = getattr(pv, "review", None)
            return Outcome("preview", preview=pv, token=token,
                           blocking=bool(review is not None and review.blocking()))
        if turn is not None and turn.needs_user():
            user = [c for c in turn.choices if c.kind not in APP_CHOICE_KINDS]
            return Outcome("question", message=turn.message, questions=list(turn.questions), choices=user)
        if self.conversation is not None:
            return Outcome("question", message=(getattr(turn, "message", "") or
                                               "Tell me what you\u2019d like to change or clarify."))
        return Outcome("empty")

    def run_blocked_reason(self) -> str:
        """Explain unavailable actions; visual findings are part of the single preview decision."""
        if self.pending_custom_transform is not None:
            return "Press Preview to finish preparing the custom transform."
        if self.generated is not None and not self.generated_token:
            from .preview import approval_inputs, explain_approval_change
            what = explain_approval_change(self.generated_inputs or {},
                                          approval_inputs(self._generated_key(self.generated), self.files()))
            return ("a file changed since the AI-written transform was tested — press Preview again"
                    + "".join(f"\n  • {detail}" for detail in what))
        if self._current_review()[0] is None:
            return "Press Preview first — review its changes before Run."
        return ""

    @_session_action
    def acknowledge_review(self, token: str) -> None:
        """The user has looked at the blocking findings of the preview made with ``token`` (and only that one)."""
        pv, current = self._current_review()
        if self.pending_custom_transform is not None:
            raise ValueError("No preview exists yet; the custom method is still being prepared.")
        if pv is None or not token or token != current:
            diag.event("session.approval.refused", level="warning", gate="review_acknowledgement",
                       reason="preview_changed", supplied_digest=token, current_digest=current)
            raise ValueError("that preview is not the current one — press Preview again")
        pv.review_ack = token
        diag.event("session.approval.acknowledged", approval_digest=token)

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

    @_session_action
    def run(self, in_place: bool, render: bool, log: Log) -> str:
        from .batch import run_batch
        from .preview import approval_inputs, _approval_token_from_inputs
        blocked = self.run_blocked_reason()
        if blocked:
            diag.event("session.approval.refused", level="warning", gate="visual_review", reason=blocked)
            raise ValueError(blocked)
        if self.recovery is not None:
            if in_place:
                diag.event("session.approval.refused", level="warning", gate="delivery_mode",
                           reason="recovery_copies_only")
                raise ValueError("Recovery exports edited copies only; original files cannot be overwritten.")
            from .recovery import deliver_recovery
            result = self.recovery
            # Each partial export is a new set of copies. Earlier deliveries stay intact, including when the
            # investigation was reopened from its saved checkpoint.
            destination, sequence = result.out_dir / "outputs", 2
            while destination.exists():
                destination = result.out_dir / f"outputs-{sequence}"
                sequence += 1
            delivery = {"out_dir": destination}
            destination = deliver_recovery(result, acknowledge_review=True, **delivery)
            self.last_recovery = result
            self.last_run_dir = result.out_dir
            self.last_batch, self.last_trials = None, None
            return result.to_text() + f"\nRecovery copies exported: {destination}. Original files were not changed."
        self.last_recovery = None
        if self.generated is not None:
            from .codegen import run_generated
            files = self.files()
            key = self._generated_key(self.generated)
            current_inputs = approval_inputs(key, files)
            try:
                current_token = _approval_token_from_inputs(key, current_inputs)
            except OSError:
                current_token = ""
            if current_token != self.generated_token:
                from .preview import explain_approval_change
                what = explain_approval_change(self.generated_inputs or {}, current_inputs)
                diag.event("session.approval.refused", level="warning", gate="approval_digest",
                           reason="generated_inputs_changed", changes=what)
                raise ValueError("a file changed (or the folder changed) since the AI-written transform was tested — "
                                 "press Preview again" + ("".join(f"\n  • {w}" for w in what) if what else ""))
            out = (self.out_dir or self.new_out_dir()) / "run"
            diag.event("session.approval.accepted", approval_digest=self.generated_token,
                       file_count=len(files), in_place=False, run_dir=str(out))
            res = run_generated(self.generated, [str(f) for f in files], out, log=log,
                                approved_inputs=self.generated_inputs or current_inputs)
            self.last_run_dir, self.last_trials, self.last_batch = out, res, None
            ok = sum(r.passed for r in res)
            return f"{ok} of {len(res)} file(s) PASS. Outputs: {out / 'outputs'} (your files were not changed)."
        if self.preview is None or self.spec is None:
            diag.event("session.approval.refused", level="warning", gate="preview", reason="preview_required")
            raise ValueError("nothing runs before you have seen the preview")
        if in_place:
            diag.event("session.approval.refused", level="warning", gate="delivery_mode", reason="copies_only")
            raise ValueError("Edited copies only; original files are never overwritten.")
        files = self.files()
        from .layouts import is_layout_plan
        if is_layout_plan(self.spec):
            return self._run_layouts(files, in_place, render, log)
        key = self.outcome_key()
        current_inputs = approval_inputs(key, files)
        try:
            current_token = _approval_token_from_inputs(key, current_inputs)
        except OSError:
            current_token = ""
        if current_token != self.preview.token:
            from .preview import explain_approval_change
            what = explain_approval_change(getattr(self.preview, "token_inputs", None) or {},
                                           current_inputs)
            diag.event("session.approval.refused", level="warning", gate="approval_digest",
                       reason="preview_inputs_changed", changes=what)
            raise ValueError("a file or the plan changed since the preview — check the documents again"
                             + ("".join(f"\n  • {w}" for w in what) if what else ""))
        out = self.out_dir / "run"
        diag.event("session.approval.accepted", approval_digest=self.preview.token,
                   file_count=len(files), in_place=in_place, run_dir=str(out))
        self.last_run_dir = out                   # set before the run: a run that stops still has its logs there
        approved_inputs = getattr(self.preview, "token_inputs", None) or current_inputs
        batch = run_batch([str(f) for f in files], "edit-spec", {}, out, renderer="auto", do_render=render,
                          in_place=False, progress=log, spec=self.spec, approved_inputs=approved_inputs)
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
        lines.append(f"Edited copies that passed every check: {out / 'outputs'} (your files were not changed)")
        if c["FAIL"]:
            lines.append(f"Copies that FAILED verification: {out / 'failed'} (diagnosis only — do not use them)")
        return "\n".join(lines)

    @_session_action
    def _run_layouts(self, files: List[Path], in_place: bool, render: bool, log: Log) -> str:
        """A layout plan: every layout's spec on its own files (``layouts.run_layouts``), copies only; the token the
        user approved binds every layout, its files and the routing, and is checked again here."""
        from .layouts import layout_token, run_layouts
        if in_place:
            diag.event("session.approval.refused", level="warning", gate="delivery_mode", reason="layouts_copies_only")
            raise ValueError("A plan per layout writes edited copies only; original files are not overwritten.")
        try:
            current = layout_token(self.spec, files)
        except OSError:
            current = ""
        if current != self.preview.token:
            diag.event("session.approval.refused", level="warning", gate="approval_digest",
                       reason="layout_inputs_changed")
            raise ValueError("a file, the plan or the way the files are grouped changed since the preview — "
                             "check the documents again before creating copies")
        out = self.out_dir / "run"
        diag.event("session.approval.accepted", approval_digest=self.preview.token, file_count=len(files),
                   in_place=False, run_dir=str(out))
        self.last_run_dir = out
        result = run_layouts([str(f) for f in files], self.spec, out, renderer="auto", do_render=render,
                             progress=log, approved_inputs=self.preview.token_inputs)
        self.last_run_dir, self.last_batch, self.last_trials = out, result, None
        c = result.counts()
        lines = [f"{len(result.files)} file(s) in {len(result.batches)} layout(s): PASS {c['PASS']}  FAIL {c['FAIL']}  "
                 f"EXCEPTION {c['EXCEPTION']}"]
        for f in result.files:
            line = f"  {f.status:<9} {f.source.name}"
            if f.exception:
                line += f"  [{f.exception.get('code')}] {f.exception.get('reason')}"
            elif f.record and not f.record.passed:
                line += "  " + "; ".join(f"{ch.id} FAIL" for ch in f.record.checks if ch.status == "FAIL")
            elif f.delivered_to:
                line += f"  → {f.delivered_to}"
            lines.append(line)
        lines.append(f"\nReport: {out / 'LAYOUT_REPORT.md'}")
        return "\n".join(lines)

    @_session_action
    def load_recovery(self, run_dir: Path) -> str:
        """Reopen saved investigation evidence without making a model call or restoring review approval."""
        from .recovery import load_recovery
        result = load_recovery(run_dir)
        self._adopt_recovery(result)
        return result.to_text()

    def _adopt_recovery(self, result, *, keep_conversation=False) -> None:
        self._set_outcome()
        if not keep_conversation:
            self.end_conversation()
        else:
            self.turn = None
        self.recovery = result
        self.out_dir = result.out_dir
        self.questions = list(result.questions)

    @_session_action
    def resolve_remaining(self, log: Log, options=None, request: Any = None) -> str:
        """Investigate the last run's exceptions, or the current preview's failed files, on copies.

        No export is performed here. The returned candidates become the reviewable outcome for Run.
        """
        from .recovery import recover, recover_from_run
        out = Path(self.out_root) / datetime.now().strftime("%Y%m%d-%H%M%S-%f-recovery")
        previous = self.recovery
        self.recovery_checkpoint = previous.out_dir if previous is not None else out
        if previous is not None:
            from .recovery import resume_recovery
            result = resume_recovery(previous.out_dir, request=request, options=options,
                                     client=self.client, log=log)
            self._adopt_recovery(result)
            return result.to_text()
        source_run = self.last_run_dir
        if ((self.preview is not None or self.conversation is not None)
                and self.out_dir is not None and self.last_run_dir != self.out_dir / "run"):
            source_run = None  # the current conversation/preview has not run; an older batch is unrelated
        pending: List[Path] = []
        if source_run is not None:
            from .batch import load_set_aside
            from .layouts import is_layout_run, layouts_needing_work
            if is_layout_run(Path(source_run)):
                # A plan per layout ran one batch per layout; recovery takes them one at a time, each against its
                # own spec (the rest wait in pending_layout_recovery). A document no layout covered has no plan to
                # recover against: it stays as it was, named in the report.
                queue = layouts_needing_work(Path(source_run))
                if not queue:
                    return ("Nothing to resolve: every document that had a plan passed. A document no layout "
                            "covered stays as it was (see LAYOUT_REPORT.md).")
                source_run, pending = queue[0], queue[1:]
            if (Path(source_run) / "set_aside.json").is_file() and not load_set_aside(source_run).get("files"):
                return "Nothing to resolve: 0 failed or set-aside files in the previous run."
            result = recover_from_run(source_run, out, request=request, options=options,
                                      client=self.client, log=log)
        else:
            if request is not None and not isinstance(request, str):
                raise ValueError("Scoped answers require an existing recovery investigation.")
            files = self.files()
            pv = self.preview
            if pv is not None:
                bad = {Path(f.source) for f in pv.batch_plan.files if f.status == "exception"}
                bad.update(Path(s.source) for s in pv.samples if not s.passed)
                files = [f for f in files if f in bad]
                if not files:
                    return "Nothing to resolve: 0 failed or set-aside files in the current preview."
            approved = list(self.conversation.sources) if self.conversation is not None else []
            original_request = (self.conversation.request if self.conversation is not None else "") or \
                (self.spec or {}).get("request") or ""
            clarified = original_request
            if request and request.strip() != original_request.strip():
                clarified = original_request + "\n\nUser clarification: " + request if original_request else request
            result = recover(files, clarified, out, spec=self.spec, template=self.template,
                             approved_words=approved, options=options, client=self.client, log=log,
                             input_files=list(self.inputs))
        self._adopt_recovery(result)
        self.pending_layout_recovery = pending
        return result.to_text()

    @_session_action
    def resolve_next_layout(self, log: Log, options=None) -> str:
        """After one layout's recovery was exported, investigate the next layout whose files still need work
        (``pending_layout_recovery``, in plan order). Each layout is recovered against its own EditSpec; the exported
        copies of the previous layout stay where they are."""
        from .recovery import recover_from_run
        queue = list(self.pending_layout_recovery)
        if not queue:
            raise ValueError("No other layout has documents that still need work.")
        out = Path(self.out_root) / datetime.now().strftime("%Y%m%d-%H%M%S-%f-recovery")
        self.recovery_checkpoint = out
        result = recover_from_run(queue[0], out, options=options, client=self.client, log=log)
        self._adopt_recovery(result)
        self.pending_layout_recovery = queue[1:]
        return result.to_text()


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
    from .settings import logs_dir
    diag.setup_app_logging(logs_dir())           # also records Qt/import failures before the window can start
    with diag.span("gui.application") as completion:
        code = _main(argv)
        completion["exit_code"] = code
        return code


def _main(argv=None) -> int:
    try:
        import PySide6.QtWidgets  # noqa: F401 — PySide6, or a system library Qt needs (libEGL on Linux), missing
    except ImportError as e:
        diag.event("gui.startup.failed", level="error", **diag.error_fields(e))
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
