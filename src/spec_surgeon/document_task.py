"""One conversational document task, independent of the window.

The Session is a toolbox: compile, reply, preview, run, recover, each a verified engine step. This coordinator owns
the stages the user sees — empty, question, preview, done, budget, resume — and moves between them on the Session's
single decision table (``Session.outcome``), never by probing engine fields. It keeps the user's decisions together,
triages every attachment the moment it arrives, and persists a recoverable journal. Journals never restore delivery
approval or execute saved code.

Attachments are Word documents to edit, at most one template, and pictures or tables the user attached for the edit
to use (``reference``: a new logo, a sheet of project values); nothing beside them is discovered. There is no spending
ceiling: every model call writes up to the model's maximum output at high effort, the work pauses after
``STEPS_PER_PAUSE`` model calls until the user says to go on, the window shows the estimated spending, and its Stop
cuts off the call that is running (``llm.cancellation_scope``). Continue after a pause, a stop or a failed call picks
the conversation up where it stopped.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import llm, review_display
from .gui import Session
from .inputs import KINDS as REFERENCE_KINDS
from .recovery_state import atomic_json
from .resources import DEFAULT_MAX_OUTPUT_TOKENS

STEPS_PER_PAUSE = 48    # model calls between the pauses where the user says to go on
AUTO_STEPS = 6          # internal steps the coordinator takes on its own after one user action (bounded)
STAGES = ("empty", "question", "preview", "done", "budget", "resume")
START_OVER = "Start over from the originals"   # the reply (a chip, a button) that drops the working copies
CONVERSATION_EFFORT = "medium"   # the model's own default: fewer tool rounds per turn than high, measured by the owner
RECOVERY_EFFORT = "high"         # the hard path keeps the depth

# What the window says while the model uses a tool: one plain sentence per tool, never the tool's name.
TOOL_ACTIVITY = {
    "inspect_profile": "Looking at how the documents are built", "list_styles": "Reading the styles",
    "sample_paragraphs": "Reading sample paragraphs", "show_part_text": "Reading a header or footer",
    "read_template": "Reading the template", "read_input_file": "Reading the attached table",
    "view_pages": "Looking at the rendered pages", "describe_op": "Checking what an edit can do",
    "inspect_template_styles": "Checking the template's styles", "list_parts": "Listing the document's parts",
    "read_paragraphs": "Reading the documents", "read_xml_page": "Reading the document's structure",
    "run_analysis": "Analysing the documents", "read_analysis_result": "Reading the analysis",
    "propose_defined_terms": "Finding defined terms", "read_context_archive": "Looking back at the conversation",
    "submit_editspec": "Writing the plan", "ask_user": "",
}
STATUS_FOR_LOG = {"Preparing and checking the preview…": ("previewing", "Preparing and checking the preview…"),
                  "Looking at the rendered pages…": ("looking", "Looking at the rendered pages…"),
                  "Creating and checking your edited copies…": ("creating", "Creating and checking your edited copies…"),
                  "Checking the files that still need work…": ("recovering", "Checking the files that still need work…"),
                  "Continuing where the conversation stopped…": ("resuming", "Continuing where the conversation stopped…")}


@dataclass
class Attachment:
    """A file the user attached, with what triage found the moment it arrived: a document to edit, the template, or a
    picture or table for the edit to use (``reference``)."""
    path: Path
    template: bool = False
    reference: bool = False
    state: str = "ok"                       # ok | note | refused
    notes: List[str] = field(default_factory=list)
    reason: str = ""                        # the refusal, one sentence
    pattern_id: str = ""

    @property
    def refused(self) -> bool:
        return self.state == "refused"

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "template": self.template, "reference": self.reference, "state": self.state,
                "notes": list(self.notes), "reason": self.reason, "pattern_id": self.pattern_id}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def triage_reference(path: Path) -> Attachment:
    """A picture or table attached for the edit to use: model-free, milliseconds — it opens, it is within the size the
    conversation reads, and what it is."""
    from .inputs import overview
    path = Path(path)
    found = overview(path)
    if found.get("problem"):
        return Attachment(path, reference=True, state="refused", reason=f"{path.name} {found['problem']}.")
    if found["kind"] == "picture":
        width, height = found.get("pixels") or (0, 0)
        note = f"A {str(found.get('format') or 'picture').upper()} picture, {width} × {height} pixels, for the edit to use."
    elif found.get("sheets") is not None:
        note = f"A spreadsheet with {len(found['sheets'])} sheet(s), for the edit to use."
    elif found.get("headers") is not None:
        note = f"A table with {found.get('rows', 0)} row(s), for the edit to use."
    else:
        note = "A data file, for the edit to use."
    return Attachment(path, reference=True, state="note", notes=[note])


def triage(path: Path, *, template: bool = False) -> Attachment:
    """Deterministic, model-free, milliseconds: can the engine work with this file, and what should the user know."""
    try:
        from .triage import triage_file
    except ImportError:                     # the triage module is optional until it ships with the package
        return Attachment(Path(path), template=template)
    found = triage_file(path)
    return Attachment(Path(path), template=template, state=found.state, notes=list(found.notes),
                      reason=found.reason, pattern_id=getattr(found, "pattern_id", "") or "")


def _same_file_key(path) -> str:
    """One spelling of a path for comparing files: resolved when the file system can, the text otherwise."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


class DocumentTask:
    def __init__(self, session=None, *, journal=None, render=True):
        self.session = session or Session()
        self.render = render
        self.journal = Path(journal) if journal else None
        self.messages: List[Dict[str, str]] = []
        self.decisions: List[str] = []
        self.word_sources: List[str] = []
        self.results: Dict[str, str] = {}
        self.attachments: List[Attachment] = []
        self.stage = "empty"
        self.problem = ""
        # continue on the result: the delivered copies become what the next request works on (original → current),
        # a generation counter, and what was already done, told to the model with the next request
        self.working: Dict[str, str] = {}
        self.generation = 0
        self.history: List[str] = []
        self._events = None                 # the window's live channel for the job that is running (perform)
        self._talking = False               # inside a call of the conversation itself: its words are the reply
        self._seen = 0
        self._conversation_id = None
        self._revision = 0
        self._resume_recovery = None
        self._resume_layouts = []           # layout batch folders still to recover, from the journal
        self._budget = self._new_budget()
        # Files a caller handed the session (tests, the installed smoke test) become attachments.
        given: List[Path] = []
        given = list(self.session.documents or [])
        template = self.session.template
        references = list(self.session.inputs or [])
        self.session.documents, self.session.template, self.session.inputs = [], None, []
        for path in given:
            self.attachments.append(triage(Path(path)))
        if template:
            self.attachments.append(triage(Path(template), template=True))
        for path in references:
            self.attachments.append(triage_reference(Path(path)))
        self._sync()

    @staticmethod
    def _new_budget(max_calls=STEPS_PER_PAUSE):
        """No spending or token ceiling; the call count pauses the work for the user's go-ahead."""
        return llm.CallBudget(max_cost_usd=None, max_calls=max_calls, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                              max_total_tokens=None, effort=CONVERSATION_EFFORT)

    # ---- attachments ----
    def documents(self) -> List[Path]:
        """The documents the next request works on: each attached document, or the copy the last run made of it."""
        return [self.current_of(a.path) for a in self.attachments if not a.template and not a.reference and not a.refused]

    def current_of(self, original) -> Path:
        return Path(self.working.get(str(Path(original)), str(Path(original))))

    def origin_of(self, current) -> Path:
        """The attached original a working copy stands for (itself when it is an original). Compared as the files
        on disk: the engine resolves the paths it reads (a results folder behind a junction, a mapped drive or
        another spelling of the case would otherwise never match the copy it was given)."""
        wanted = _same_file_key(current)
        for original, path in self.working.items():
            if path == str(Path(current)) or _same_file_key(path) == wanted:
                return Path(original)
        return Path(current)

    # ---- the window's live channel ----
    @contextlib.contextmanager
    def _conversation(self):
        """Around a call of the conversation itself (compile, a reply, a resumed turn): its words are the reply."""
        self._talking = True
        try:
            yield
        finally:
            self._talking = False

    def _emit(self, event: Dict[str, Any]) -> None:
        if self._events is None:
            return
        try:
            self._events(event)
        except Exception:  # noqa: BLE001 — the window's problem, never the job's
            pass

    def _on_stream(self, event: Dict[str, Any]) -> None:
        """A model call's stream, as the window shows it: the model's words as they are written, its thinking
        summary, and one plain sentence per tool it uses. Words and thinking are relayed from the conversation's
        own calls alone (``_talking``): the visual reviewer, recovery and a custom method also call the model, and
        their text is not the assistant's reply to the user."""
        kind = event.get("type")
        if kind == "started":
            self._emit({"type": "status", "kind": "thinking", "text": "Thinking…"})
        elif kind == "retry":
            # the attempt is abandoned and sent again: the page drops the words it had from it
            self._emit({"type": "status", "kind": "retrying", "text": "Sending again…"})
        elif kind in ("text_delta", "thinking_delta"):
            if self._talking:
                self._emit({"type": kind, "text": event.get("text", "")})
        elif kind == "tool":
            name = str(event.get("name") or "")
            if name == "submit_editspec":
                self._emit({"type": "status", "kind": "planning", "text": "Writing the plan…"})
            else:
                text = TOOL_ACTIVITY.get(name, "")
                if text:
                    self._emit({"type": "activity", "text": text})

    def template(self) -> Optional[Path]:
        return next((a.path for a in self.attachments if a.template and not a.refused), None)

    def references(self) -> List[Path]:
        return [a.path for a in self.attachments if a.reference and not a.refused]

    def _sync(self) -> None:
        self.session.documents = self.documents()
        self.session.template = self.template()
        self.session.inputs = self.references()

    def say(self, text, role="app"):
        if text:
            message = {"role": role, "text": str(text), "at": time.time()}
            self.messages.append(message)
            if role != "user":
                # the id the view numbers this message by (docs/WEB_API.md: ``m<index>``), so the page can finalize
                # the streaming bubble under it and keep its aside
                self._emit({"type": "message", "message": {"id": f"m{len(self.messages) - 1}", **message}})

    def _invalidate(self):
        self.session._set_outcome()
        self.session.end_conversation()
        self._resume_recovery = None
        self._revision += 1
        self.stage = "resume" if self.decisions else "empty"
        self.problem = ""

    def attach(self, paths, *, template=False):
        """Word documents to edit, or the template; pictures and tables (``REFERENCE_KINDS``) are attached for the edit
        to use. Anything else is refused before anything changes."""
        checked: List[Path] = []
        for value in paths:
            path = Path(value).resolve()
            suffix = path.suffix.lower()
            usable = suffix == ".docx" or (not template and suffix in REFERENCE_KINDS)
            if not path.is_file() or not usable or path.name.startswith("~$"):
                raise ValueError(f"Choose a Word .docx file: {path.name}" if template else
                                 f"Choose Word .docx documents, pictures (.png, .jpg, .gif, .bmp) or tables (.xlsx, .csv, "
                                 f".json): {path.name}")
            if path not in checked:
                checked.append(path)
        if not checked:
            return
        if template and len(checked) != 1:
            raise ValueError("Choose one template document.")
        before = (self.documents(), self.template(), self.references())
        fresh: List[str] = []
        if template:
            found = triage(checked[0], template=True)
            if found.refused:
                raise ValueError(found.reason)
            self.attachments = [a for a in self.attachments if a.path != checked[0] and not a.template]
            self.attachments.append(found)
        else:
            known = {a.path for a in self.attachments}
            for path in checked:
                if path in known:
                    continue
                self.attachments.append(triage(path) if path.suffix.lower() == ".docx" else triage_reference(path))
                if self.working and path.suffix.lower() == ".docx":
                    fresh.append(path.name)
        self._sync()
        if before != (self.documents(), self.template(), self.references()):
            self._invalidate()
            if self.decisions:
                self.say("The attachments changed. I’ll check the updated documents against your request.")
            if fresh:
                self.say(f"{', '.join(fresh)}: attached as an original. The changes already made to the other copies "
                         f"are not in it; the next change is made to it as it is. To give it every change, say "
                         f"“{START_OVER}” and ask again.")
        self.save()

    def remove(self, path, *, template=False):
        path = Path(path)
        self.attachments = [a for a in self.attachments if not (a.path == path and a.template == template)]
        had_copies = bool(self.working)
        self.working.pop(str(path), None)
        self._sync()
        self._invalidate()
        if had_copies and not self.working:
            # the last working copy left with its original: the attached documents are originals again, and the
            # changes the history lists are in none of them
            self.history, self.generation, self.results = [], 0, {}
            self.stage = "resume" if self.decisions else "empty"
            self.say("No edited copy is attached any more, so the next change starts from your originals.")
        self.save()

    # ---- the request ----
    def _request(self):
        if not self.decisions:
            return ""
        return self.decisions[0] + ("\n\nUser decisions and revisions:\n" + "\n".join(self.decisions[1:])
                                    if len(self.decisions) > 1 else "")

    def _compile(self, log):
        self.session.replay_word_sources = list(self.word_sources)
        self._emit({"type": "status", "kind": "reading", "text": "Reading your documents…"})
        with self._conversation():
            self.session.compile(self._request(), log, history=list(self.history), fresh=self._fresh_documents())

    # ---- continue on the result ----
    def _advance_generation(self) -> None:
        """The delivered copies become the working set: the next request builds on them, the originals stay as they
        are, and the model is told what was already done. The conversation that led here is over (its files are
        not the next request's)."""
        moved = False
        for original, output in self.results.items():
            if Path(output).is_file():
                self.working[str(Path(original))] = str(Path(output).resolve())
                moved = True
        if not moved:
            return
        done = [c["summary"] for g in self._groups() for c in g["changes"]]
        self.history.extend(done or self.decisions[:1])
        self.generation += 1
        self.decisions = []
        self._sync()
        self.session._set_outcome()
        self.session.end_conversation()

    def _fresh_documents(self) -> List[str]:
        """Documents attached after the last run: originals among working copies, carrying none of the changes the
        history lists (the model is told; the user was told when they attached them)."""
        if not self.working:
            return []
        return [a.path.name for a in self.attachments
                if not a.template and not a.reference and not a.refused and str(a.path) not in self.working]

    def start_over(self) -> None:
        """Back to the attached originals: the working copies are set aside (the files stay where they are), the
        plan is void, and the next request starts from the originals."""
        if not self.working and not self.history:
            return
        self.working, self.generation, self.history, self.decisions = {}, 0, [], []
        self.results = {}
        self._sync()
        self._invalidate()
        self.stage = "empty"
        self.say("Starting over from the originals. Tell me what you'd like to change.")
        self.save()

    def _target_question(self):
        if self.session.recovery is None:
            return None
        for record in self.session.recovery.question_records:
            if record.get("status") != "open" or record.get("kind") != "goal_binding":
                continue
            resolution = record.get("resolution", {})
            for ident, effect in resolution.get("effects", {None: resolution}).items():
                if effect.get("status") != "needs_input" or not effect.get("candidates"):
                    continue
                candidates = effect["candidates"]
                return {"question_id": record["id"], "effect_id": ident,
                        "candidates": candidates,
                        "options": [f"Paragraph {c['paragraph_index'] + 1} in "
                                    f"{review_display.readable(c.get('scope', 'document'))}: {c.get('text', '')}"
                                    for c in candidates],
                        "text": f"Which passage(s) in {Path(record['files'][0]).name} should change? "
                                "Reply with the number, or numbers separated by commas."}
        return None

    def perform(self, action, *, text="", token="", choice_id="", chip=False, log=lambda _: None, events=None):
        """Worker entry point. One ledger covers every nested model call and every turn. ``events`` is the window's
        live channel (docs/WEB_API.md: status, activity, text_delta, thinking_delta, message, plan, samples, results,
        question, cost); without one the job runs silently and the view says everything at the end."""
        self.problem = ""
        self._events = events
        self.save(in_progress=True)

        def checkpointed_log(message):
            self.save(in_progress=True)
            status = STATUS_FOR_LOG.get(str(message))
            if status:
                self._emit({"type": "status", "kind": status[0], "text": status[1]})
            log(message)
        try:
            with llm.budget_scope(self._budget), llm.stream_observer_scope(self._on_stream):
                if action == "send":
                    self._send(text, checkpointed_log, chip=chip)
                elif action == "choose":
                    self._choose(choice_id, checkpointed_log)
                elif action == "primary":
                    self._primary(token, checkpointed_log)
                else:
                    raise ValueError("Unknown task action")
        except llm.BudgetExceeded:
            self.stage = "budget"
        except Exception:
            # Invalidate the exposed action immediately, but retain evidence for diagnostics/replanning.
            self.stage = "question" if self.stage == "question" else "resume" if self.decisions else "empty"
            self._revision += 1
            raise
        finally:
            if self._budget.exhausted_reason and self.stage not in {"done", "preview"}:
                self.stage = "budget"
            self._emit({"type": "cost", "cost": self._cost()})
            self._events = None
            self._talking = False
            self.save()
        return self.view()

    def _cost(self) -> Dict[str, Any]:
        cost = self._budget.spent_usd
        text = ("an amount the price table cannot estimate" if cost is None else
                "less than $0.01" if cost < 0.005 else f"about ${cost:.2f}")
        return {"usd": cost, "text": text, "calls": self._budget.calls}

    def _ready_to_talk(self):
        self.session.files(outcome=False)  # reject missing attachments before consuming an answer
        if not self.session.claude_ready():
            raise ValueError("Connect your Anthropic API key in Settings to start the conversation.")

    def _send(self, text, log, chip=False):
        """The user's message. ``chip``: the text is one of the model's suggested replies, tapped — the user's
        decision, carried to the model as their reply, but never approved wording (the words are Claude's)."""
        text = text.strip()
        if not text:
            return
        if text.casefold() == START_OVER.casefold():
            self.say(text, "user")
            self.start_over()
            return
        self._ready_to_talk()
        question = self._target_question()
        if question:
            picks = self._numbers(text, len(question["candidates"]))
            if picks:
                ids = [question["candidates"][i]["candidate_id"] for i in picks]
                answer = {"question_id": question["question_id"], "candidate_ids": ids, "answer": text}
                if question["effect_id"] is not None:
                    answer["effect_id"] = question["effect_id"]
                self.session.resolve_remaining(log, request=answer, options=self._recovery_options())
                self.say(text, "user")
                self._revision += 1
                self._settle(log)
                return
        choices = self._choices()
        picks = self._numbers(text, len(choices))
        if len(picks) == 1 and choices:
            self.say(text, "user")
            self._take(choices[picks[0]], log)
        else:
            self.say(text, "user")
            self.decisions.append(text)
            if not chip:
                self.word_sources.append(text)
            self.save(in_progress=True)
            s = self.session
            if s.conversation is not None and s.conversation_matches(s.conversation.request):
                with self._conversation():
                    s.reply(text, render=self.render, log=log, approve=not chip)
            elif s.recovery is not None and any(f.status != "READY" for f in s.recovery.files):
                s.resolve_remaining(log, request=text, options=self._recovery_options())
            else:
                self._compile(log)
        self._settle(log)

    def _choose(self, choice_id, log):
        self._ready_to_talk()
        chosen = next((c for c in self._choices() if c.id == choice_id), None)
        if chosen is None:
            raise ValueError("That choice is no longer available. Review the updated plan first.")
        self.say(f"Chose: {chosen.label}", "user")
        self._take(chosen, log)
        self._settle(log)

    def _take(self, chosen, log):
        """A choice the user picked: its label is a decision, its exact words are approved wording."""
        self.decisions.append(chosen.label)
        self.decisions.extend(chosen.words)
        self.word_sources.extend(chosen.words)
        self.save(in_progress=True)
        with self._conversation():
            self.session.reply(choice_id=chosen.id, render=self.render, log=log)

    @staticmethod
    def _numbers(text, count):
        if not re.fullmatch(r"\s*\d+(?:\s*[, ]\s*\d+)*\s*", text):
            return []
        values = list(dict.fromkeys(int(v) - 1 for v in re.findall(r"\d+", text)))
        return values if all(0 <= v < count for v in values) else []

    def _choices(self):
        return list(self.session.outcome().choices)

    def _collect_conversation(self):
        c = self.session.conversation
        if c is None:
            return
        if c.id != self._conversation_id:
            self._conversation_id, self._seen = c.id, 0
        for entry in c.entries[self._seen:]:
            # App notes carry implementation telemetry; the coordinator renders its own state instead.
            if entry.role != "claude":
                continue
            if c.recovery_active or any(choice.get("kind") in {"custom_transform", "recovery_plan", "recovery_target"}
                                        for choice in entry.choices):
                continue
            # Questions and choices are shown once, in the clarification card, never twice.
            lines = [line for line in entry.text.split("\n") if not line.startswith("• ")]
            self.say("\n".join(lines).strip(), "assistant")
        self._seen = len(c.entries)

    def _settle(self, log):
        """Take every internal step the engine needs (prepare a custom method, carry out a recovery plan, make the
        preview) until the user is needed, then set the stage from the one decision table."""
        s = self.session
        self._collect_conversation()
        outcome = s.outcome()
        for _ in range(AUTO_STEPS):
            if outcome.kind != "auto":
                break
            if outcome.needs_preview:
                self._emit({"type": "plan", "plan": self._plan_view()})    # the plan shows before any page renders
                log("Preparing and checking the preview…")
                s.make_preview(self.render, log,
                               on_preview=lambda: self._emit({"type": "samples", **self._samples_view()}))
                self._emit({"type": "samples", **self._samples_view()})     # again with Claude's visual findings
            else:
                with self._conversation():
                    s.reply(choice_id=outcome.auto_choice, render=self.render, log=log)
            self._collect_conversation()
            outcome = s.outcome()
        self._revision += 1
        self.stage = {"question": "question", "preview": "preview", "resume": "resume", "auto": "question",
                      "empty": "resume" if self.decisions else "empty"}[outcome.kind]
        if self._budget.exhausted_reason and self.stage == "question":
            self.stage = "budget"
        if self.stage == "question":
            self._emit({"type": "question", "questions": list(outcome.questions),
                        "choices": [{"id": c.id, "label": c.label, "words": list(c.words), "detail": c.detail}
                                    for c in outcome.choices], "chips": list(outcome.chips)})

    def _recovery_options(self):
        from .recovery import RecoveryOptions
        return RecoveryOptions(max_cost_usd=None, max_calls=self._budget.max_calls, max_attempts=8,
                               max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=None,
                               effort=RECOVERY_EFFORT, render=self.render, visual_review=self.session.visual_check)

    def action_token(self):
        s = self.session
        outcome = s.outcome()
        preview = outcome.preview
        visual = getattr(preview, "review", None)
        return _fingerprint({"revision": self._revision, "stage": self.stage,
                             "documents": s.documents, "template": s.template, "inputs": s.inputs, "spec": s.spec,
                             "preview": outcome.token,
                             "visual_findings": visual.to_dict() if visual else None,
                             "warnings": getattr(preview, "warnings", [])})

    def _primary(self, token, log):
        if token != self.action_token():
            raise ValueError("The documents or plan changed. Review the updated plan first.")
        s = self.session
        if self.stage == "budget":
            # Extend the same ledger; do not reset accumulated usage or nested limits.
            self._budget.max_calls = max(self._budget.max_calls, self._budget.calls) + STEPS_PER_PAUSE
            self._budget.exhausted_reason = None
            self.stage = "resume"
            self.say(f"Continuing for up to {STEPS_PER_PAUSE} more AI steps.")
            self._primary(self.action_token(), log)
            return
        if self.stage == "resume":
            self._resume(log)
            return
        outcome = s.outcome()
        partial = self.stage == "question" and outcome.kind == "question" and outcome.partial
        if self.stage != "preview" and not partial:
            raise ValueError("Answer the open question before continuing.")
        if outcome.kind != "preview" and not partial:
            raise ValueError("The documents or plan changed. Review the updated plan first.")
        if outcome.blocking:
            # A blocking visual finding is a separate, explicitly labelled decision on the displayed preview.
            s.acknowledge_review(outcome.token)
        log("Creating and checking your edited copies…")
        s.run(False, self.render, log)
        self._collect_results()
        self._emit({"type": "results", "results": self._results_view()})
        if s.last_batch is not None and any(f.status != "PASS" for f in s.last_batch.files):
            log("Checking the files that still need work…")
            conversation = s.conversation
            s.resolve_remaining(log, options=self._recovery_options())
            if s.recovery is not None:
                if conversation is not None:
                    from .continuous_recovery import result_turn
                    conversation.recovery_active = True
                    s.conversation = conversation
                    s.turn = result_turn(conversation, s.recovery)
                self._settle(log)
                return
            # nothing a plan can recover: a document no layout covered stays as it was, named under "Left untouched"
        if s.last_trials is not None and any(not f.passed for f in s.last_trials):
            log("Checking the files that still need work…")
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        if s.recovery is not None and len(s.recovery.ready) < len(s.recovery.files):
            self.stage = "question"
            self._revision += 1
            self.say("The ready copies are saved. The remaining documents still need attention.")
            return
        if s.pending_layout_recovery:
            # a plan per layout: the previous layout's copies are saved; the next layout's documents are checked now
            log("Checking the next layout's documents that still need work…")
            s.resolve_next_layout(log, options=self._recovery_options())
            self._settle(log)
            return
        self.stage = "done"
        self._revision += 1
        n = len(self.results)
        self.say(f"{n} document {'copy is' if n == 1 else 'copies are'} ready. You can open them, describe another "
                 f"change to make on top of them, or start over from the originals.")
        self._advance_generation()

    def _resume(self, log):
        s = self.session
        if s.pending_custom_transform is not None:
            s.continue_custom_transform(log, self.render)
            self._settle(log)
            return
        checkpoint = self._resume_recovery or s.recovery_checkpoint
        if checkpoint:
            # Loading validates saved evidence and source pins. Never discard a pending checkpoint (including a
            # missing/changed one) by recompiling the request.
            s.load_recovery(checkpoint)
            self._resume_recovery = None
            s.pending_layout_recovery = [Path(x) for x in self._resume_layouts]
            self._resume_layouts = []
            groups = s.recovery.session_state.get("groups", {}).values()
            if any(group.get("status") in {"active", "paused"} for group in groups):
                s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        self._resume_recovery = None
        if s.recovery is not None:
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        c = s.conversation
        if c is not None and c.resumable() and s.conversation_matches(c.request):
            # A pause, a stop or a failed call: the conversation goes on with what it had investigated.
            with self._conversation():
                s.resume_conversation(log)
        else:
            self._compile(log)
        self._settle(log)

    def _collect_results(self):
        """The copies a run delivered, keyed by the ATTACHED original (a run on working copies reads the copies, but
        the user thinks in their own files)."""
        s = self.session
        result = s.last_recovery or s.last_batch
        if result is not None:
            for item in result.files:
                origin = str(self.origin_of(item.source))
                if item.delivered_to:
                    self.results[origin] = str(item.delivered_to)
                elif (getattr(item, "noop", False) and item.status == "PASS" and item.output
                      and Path(item.output).is_file()):
                    # The engine verifies and saves identical copies too, but intentionally leaves delivered_to
                    # empty to distinguish them from changed documents in its reports.
                    self.results[origin] = str(item.output)
        for item in s.last_trials or []:
            if item.passed and item.output:
                self.results[str(self.origin_of(item.source))] = str(item.output)

    # ---- what the window shows ----
    def _groups(self):
        """What will change, grouped by layout: one group for an ordinary spec, one per layout for a plan."""
        from .layouts import is_layout_plan, route
        s = self.session
        preview = s.outcome().preview
        contract = s.recovery.key.get("adaptive_contract") if s.recovery else None
        count = len(s.recovery.files) if s.recovery else len(s.documents)
        if contract:
            return [{"id": "", "label": "", "count": count, "files": [], "changes": review_display.effects(contract),
                     "assumptions": []}]
        if is_layout_plan(s.spec):
            routing = getattr(preview, "routing", None)
            if routing is None:
                try:
                    routing = route(s.documents, s.spec)
                except Exception:  # noqa: BLE001 — a routing that cannot be read shows counts of zero
                    routing = None
            return review_display.layout_changes(s.spec, routing)
        changes = review_display.spec_changes(s.spec)
        assumptions = review_display.spec_assumptions(s.spec)
        if not changes and s.generated is not None:
            changes = [{"title": "Requested changes", "summary": s.generated.proposal,
                        "details": [s.generated.request, "The sample copies passed independent verification."]}]
        if not changes:
            return []
        return [{"id": "", "label": "", "count": count, "files": [], "changes": changes, "assumptions": assumptions}]

    def _plan_view(self) -> Dict[str, Any]:
        """The plan as the window shows it: groups of change sentences with their assumptions, the passages each
        document contributes, the documents left untouched, and how many ordered steps the engine composed."""
        from .editspec import is_chain
        from .layouts import is_layout_plan
        s = self.session
        groups = self._groups()
        if is_layout_plan(s.spec):
            stages = max([len(layout["spec"].get("stages") or []) if is_chain(layout["spec"]) else 1
                          for layout in s.spec.get("layouts") or []] or [1])
        else:
            stages = len(s.spec.get("stages") or []) if is_chain(s.spec) else 1
        preview = s.outcome().preview
        set_aside = []
        for item in getattr(getattr(preview, "batch_plan", None), "exceptions", []) or []:
            exc = item.exception or {}
            set_aside.append({"name": Path(item.source).name, "reason": exc.get("reason", "")})
        return {"groups": groups,
                "targets": review_display.recovery_targets(s.recovery) if s.recovery else review_display.spec_bindings(s.spec),
                "set_aside": set_aside, "stages": stages if groups else 0}

    def _samples_view(self) -> Dict[str, Any]:
        preview = self.session.outcome().preview
        samples = list(getattr(preview, "samples", []) or [])
        return {"samples": [{"source": str(sv.source), "name": Path(sv.source).name,
                             "images": [str(p) for p in sv.images if Path(p).is_file()],
                             "pages_before": sv.pages_before, "pages_after": sv.pages_after, "passed": sv.passed}
                            for sv in samples],
                "warnings": list(dict.fromkeys(getattr(preview, "warnings", []) or [])),
                "blocking": bool(self.session.outcome().blocking)}

    def _results_view(self) -> List[Dict[str, Any]]:
        return [{"source": source, "output": output, "name": Path(output).name}
                for source, output in self.results.items()]

    def view(self):
        s = self.session
        outcome = s.outcome()
        question = self._target_question()
        choices = [] if question else outcome.choices
        questions: List[str] = []
        if question:
            questions.append(question["text"] + "\n" + "\n".join(
                f"{i}. {value}" for i, value in enumerate(question["options"], 1)))
        elif self.stage == "question":
            questions.extend(outcome.questions)
            if not questions and not choices:
                questions.append(outcome.message or "Tell me what you’d like to change or clarify.")
        preview = outcome.preview
        samples = list(getattr(preview, "samples", []) or [])
        warnings = list(getattr(preview, "warnings", []) or [])
        from .settings import output_root_warnings
        warnings.extend(output_root_warnings(s.out_root))
        visual = getattr(preview, "review", None)
        if visual is not None:
            if visual.skipped:
                warnings.append("Automated page review was unavailable: " + visual.skipped)
            for finding in getattr(visual, "findings", []):
                location = " · ".join(str(v) for v in (finding.get("file"),
                                           f"page {finding['page']}" if finding.get("page") else None) if v)
                warnings.append((location + ": " if location else "") + finding.get("text", ""))
        if samples and not any(sample.images for sample in samples):
            warnings.append("Page appearance was not checked: a rendered preview is unavailable. "
                            "Check the edited copies in Word before using them.")
        set_aside = []
        batch_plan = getattr(preview, "batch_plan", None)
        for item in getattr(batch_plan, "exceptions", []) or []:
            exc = item.exception or {}
            set_aside.append({"name": Path(item.source).name, "reason": exc.get("reason", "")})
        labels = {"preview": "Create edited copies", "resume": "Continue", "budget": "Continue"}
        if outcome.blocking and self.stage == "preview":
            labels["preview"] = "I reviewed the flagged pages — create copies"
        if self.stage == "question" and outcome.partial:
            ready = len(s.recovery.ready)
            labels["question"] = f"Save {ready} ready {'copy' if ready == 1 else 'copies'}"
        groups = self._groups() if self.stage in {"preview", "question"} else []
        from .editspec import is_chain
        from .layouts import is_layout_plan
        if is_layout_plan(s.spec):
            stages = max([len(layout["spec"].get("stages") or []) if is_chain(layout["spec"]) else 1
                          for layout in s.spec.get("layouts") or []] or [1])
        else:
            stages = len(s.spec.get("stages") or []) if is_chain(s.spec) else 1
        chips = list(outcome.chips) if not question else []
        if (self.working or self.history) and self.stage in {"done", "empty", "resume"} and START_OVER not in chips:
            chips.append(START_OVER)
        return {"stage": self.stage, "messages": list(self.messages),
                "stages": stages if groups else 0,
                "chips": chips,
                "working": {"generation": self.generation, "from_originals": not self.working,
                            "can_start_over": bool(self.working or self.history)},
                "attachments": [{**a.to_dict(), "current": str(self.current_of(a.path))} for a in self.attachments],
                "groups": groups,
                "effects": [c for g in groups for c in g["changes"]],
                "assumptions": list(dict.fromkeys(a for g in groups for a in g["assumptions"])),
                "targets": review_display.recovery_targets(s.recovery) if s.recovery else review_display.spec_bindings(s.spec),
                "document_count": len(s.recovery.files) if s.recovery else len(s.documents),
                "questions": questions,
                "choices": [{"id": c.id, "label": c.label, "words": list(c.words), "detail": c.detail} for c in choices],
                "samples": samples, "set_aside": set_aside,
                "warnings": list(dict.fromkeys(warnings)), "results": dict(self.results),
                "action": labels.get(self.stage, ""), "token": self.action_token(),
                "budget_paused": self.stage == "budget", "steps_per_pause": STEPS_PER_PAUSE,
                "cost": self._budget.spent_usd,
                "blocking": outcome.blocking}

    # ---- the journal ----
    def save(self, *, in_progress=False):
        if not self.journal:
            return
        atomic_json(self.journal, {
            "version": 2,
            "attachments": [a.to_dict() for a in self.attachments],
            "messages": self.messages, "decisions": self.decisions, "word_sources": self.word_sources,
            "results": self.results,
            "recovery": str(self.session.recovery.out_dir) if self.session.recovery else
                        str(self.session.recovery_checkpoint) if self.session.recovery_checkpoint else self._resume_recovery,
            "pending_layouts": [str(x) for x in self.session.pending_layout_recovery],
            "working": dict(self.working), "generation": self.generation, "history": list(self.history),
            "budget": self._budget.to_dict(), "in_progress": in_progress})

    def restore(self):
        if not self.journal or not self.journal.is_file():
            return False
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") not in (1, 2):
            raise ValueError("This saved conversation uses an unsupported version.")
        if not isinstance(data.get("decisions"), list) or not all(isinstance(x, str) for x in data["decisions"]):
            raise ValueError("The saved conversation could not be read.")
        if (not isinstance(data.get("messages"), list)
                or not all(isinstance(m, dict) and isinstance(m.get("text"), str) and
                           m.get("role") in {"app", "user", "assistant"} for m in data["messages"])
                or not isinstance(data.get("results"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["results"].items())
                or not isinstance(data.get("budget"), dict)):
            raise ValueError("The saved conversation could not be read.")
        if data.get("version") == 1:
            documents = data.get("documents")
            if not isinstance(documents, list) or not all(isinstance(p, str) for p in documents):
                raise ValueError("The saved conversation could not be read.")
            template = data.get("template")
            if template is not None and not isinstance(template, str):
                raise ValueError("The saved conversation could not be read.")
            entries = [{"path": p, "template": False} for p in documents]
            if template:
                entries.append({"path": template, "template": True})
        else:
            entries = data.get("attachments")
            if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("path"), str)
                                                        for e in entries):
                raise ValueError("The saved conversation could not be read.")
        # Attachments are triaged again: a file may have changed, moved or been fixed since.
        self.attachments = []
        for entry in entries:
            path = Path(entry["path"])
            template, reference = bool(entry.get("template")), bool(entry.get("reference"))
            if path.is_file():
                self.attachments.append(triage_reference(path) if reference else triage(path, template=template))
            else:
                self.attachments.append(Attachment(path, template=template, reference=reference, state="refused",
                                                   reason=f"{path.name} is no longer at {path.parent}."))
        self._sync()
        self.messages = data.get("messages", [])
        self.decisions = data["decisions"]
        self.word_sources = data.get("word_sources", [])
        if not isinstance(self.word_sources, list) or not all(isinstance(x, str) for x in self.word_sources):
            raise ValueError("The saved wording approvals could not be read.")
        self.results = data.get("results", {})
        working = data.get("working", {})
        history = data.get("history", [])
        if (isinstance(working, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in working.items())
                and isinstance(history, list) and all(isinstance(h, str) for h in history)
                and all(Path(v).is_file() for v in working.values())):
            self.working, self.history = dict(working), list(history)
            self.generation = int(data.get("generation", 0) or 0) if type(data.get("generation", 0)) is int else 0
        else:
            # a copy is gone: the originals are the set again, the vanished copies are no longer results, and the
            # user is told before their next request builds on the wrong files
            lost = isinstance(working, dict) and bool(working)
            self.working, self.history, self.generation = {}, [], 0
            self.results = {k: v for k, v in self.results.items() if Path(v).is_file()}
            if lost:
                self.say("The edited copies from your last run could not be found where they were saved, so the next "
                         "change starts from your originals.")
        self._sync()
        self._resume_recovery = data.get("recovery")
        layouts = data.get("pending_layouts", [])
        if not isinstance(layouts, list) or not all(isinstance(x, str) for x in layouts):
            raise ValueError("The saved conversation could not be read.")
        self._resume_layouts = list(layouts)
        budget = data.get("budget", {})
        # A journal from a version with a spending allowance keeps its call count and spending, never its ceilings.
        max_calls = budget.get("max_calls", STEPS_PER_PAUSE)
        self._budget = self._new_budget(max_calls if type(max_calls) is int and max_calls > 0 else STEPS_PER_PAUSE)
        # Retain conservative accounting; never reset spending by reopening the app.
        self._budget.calls = int(budget.get("calls", 0))
        self._budget._reserved_tokens = int(budget.get("accounted_tokens", 0))
        cost = budget.get("estimated_cost_usd", 0)
        self._budget._reserved_cost_usd = float(cost or 0)
        exhausted = budget.get("exhausted_reason")
        self._budget.exhausted_reason = exhausted if exhausted and "call limit" in exhausted else None
        if data.get("in_progress") or cost is None:
            # The app closed while work ran: its last call may have cost more than was recorded; ask before going on.
            self._budget.exhausted_reason = "Previous work stopped before its final usage was recorded."
        self.stage = ("budget" if self._budget.exhausted_reason else "resume" if self.decisions else
                      "done" if self.results and self.generation else "empty")
        if self.decisions:
            self.say("Your conversation is restored. I’ll recheck the documents before making further changes.")
        elif self.generation:
            self.say("Your conversation is restored. The edited copies from your last run are what the next change "
                     "builds on; say “Start over from the originals” to go back to your files.")
        return True
