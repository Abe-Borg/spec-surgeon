"""One conversational document task, independent of Qt.

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
import json
import re
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
                              max_total_tokens=None, effort="high")

    # ---- attachments ----
    def documents(self) -> List[Path]:
        return [a.path for a in self.attachments if not a.template and not a.reference and not a.refused]

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
            self.messages.append({"role": role, "text": str(text)})

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
                                 f"Choose Word .docx documents, pictures (.png, .jpg, .gif) or tables (.xlsx, .csv, "
                                 f".json): {path.name}")
            if path not in checked:
                checked.append(path)
        if not checked:
            return
        if template and len(checked) != 1:
            raise ValueError("Choose one template document.")
        before = (self.documents(), self.template(), self.references())
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
        self._sync()
        if before != (self.documents(), self.template(), self.references()):
            self._invalidate()
            if self.decisions:
                self.say("The attachments changed. I’ll check the updated documents against your request.")
        self.save()

    def remove(self, path, *, template=False):
        path = Path(path)
        self.attachments = [a for a in self.attachments if not (a.path == path and a.template == template)]
        self._sync()
        self._invalidate()
        self.save()

    # ---- the request ----
    def _request(self):
        if not self.decisions:
            return ""
        return self.decisions[0] + ("\n\nUser decisions and revisions:\n" + "\n".join(self.decisions[1:])
                                    if len(self.decisions) > 1 else "")

    def _compile(self, log):
        self.session.replay_word_sources = list(self.word_sources)
        self.session.compile(self._request(), log)

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

    def perform(self, action, *, text="", token="", choice_id="", log=lambda _: None):
        """Worker entry point. One ledger covers every nested model call and every turn."""
        self.problem = ""
        self.save(in_progress=True)

        def checkpointed_log(message):
            self.save(in_progress=True)
            log(message)
        try:
            with llm.budget_scope(self._budget):
                if action == "send":
                    self._send(text, checkpointed_log)
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
            self.save()
        return self.view()

    def _ready_to_talk(self):
        self.session.files(outcome=False)  # reject missing attachments before consuming an answer
        if not self.session.claude_ready():
            raise ValueError("Connect your Anthropic API key in Settings to start the conversation.")

    def _send(self, text, log):
        text = text.strip()
        if not text:
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
            self.word_sources.append(text)
            self.save(in_progress=True)
            s = self.session
            if s.conversation is not None and s.conversation_matches(s.conversation.request):
                s.reply(text, render=self.render, log=log)
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
                log("Preparing and checking the preview…")
                s.make_preview(self.render, log)
            else:
                s.reply(choice_id=outcome.auto_choice, render=self.render, log=log)
            self._collect_conversation()
            outcome = s.outcome()
        self._revision += 1
        self.stage = {"question": "question", "preview": "preview", "resume": "resume", "auto": "question",
                      "empty": "resume" if self.decisions else "empty"}[outcome.kind]
        if self._budget.exhausted_reason and self.stage == "question":
            self.stage = "budget"

    def _recovery_options(self):
        from .recovery import RecoveryOptions
        return RecoveryOptions(max_cost_usd=None, max_calls=self._budget.max_calls, max_attempts=8,
                               max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=None,
                               effort="high", render=self.render, visual_review=self.session.visual_check)

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
        self.say(f"{len(self.results)} document copies are ready. You can open them below or describe another change.")

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
            s.resume_conversation(log)
        else:
            self._compile(log)
        self._settle(log)

    def _collect_results(self):
        s = self.session
        result = s.last_recovery or s.last_batch
        if result is not None:
            for item in result.files:
                if item.delivered_to:
                    self.results[str(item.source)] = str(item.delivered_to)
                elif (getattr(item, "noop", False) and item.status == "PASS" and item.output
                      and Path(item.output).is_file()):
                    # The engine verifies and saves identical copies too, but intentionally leaves delivered_to
                    # empty to distinguish them from changed documents in its reports.
                    self.results[str(item.source)] = str(item.output)
        for item in s.last_trials or []:
            if item.passed and item.output:
                self.results[str(item.source)] = str(item.output)

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
        return {"stage": self.stage, "messages": list(self.messages),
                "attachments": [a.to_dict() for a in self.attachments],
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
        self.stage = "budget" if self._budget.exhausted_reason else "resume" if self.decisions else "empty"
        if self.decisions:
            self.say("Your conversation is restored. I’ll recheck the documents before making further changes.")
        return True
