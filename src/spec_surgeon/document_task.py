"""One conversational document task, independent of Qt.

The existing Session remains responsible for source pins, approval and verified delivery.
This coordinator chooses internal steps, keeps user decisions together and persists a
recoverable journal. Journals never restore delivery approval or execute saved code.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import llm, review_display
from .gui import Session
from .recovery_state import atomic_json

ALLOWANCE_USD = 10.0


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class DocumentTask:
    def __init__(self, session=None, *, journal=None, render=True):
        self.session = session or Session()
        self.session.simple_conversation = True
        self.render = render
        self.journal = Path(journal) if journal else None
        self.messages = []
        self.decisions = []
        self.word_sources = []
        self.results = {}
        self.stage = "empty"
        self.problem = ""
        self._seen = 0
        self._conversation_id = None
        self._revision = 0
        self._resume_recovery = None
        self._budget = self._new_budget()
        if self.session.documents is None:
            if self.session.selected_files is not None:
                self.session.documents = list(self.session.selected_files)
                self.session.selected_files = None
            elif self.session.folder:
                self.session.documents = self.session.files(outcome=False)
            else:
                self.session.documents = []

    @staticmethod
    def _new_budget():
        return llm.CallBudget(max_cost_usd=ALLOWANCE_USD, max_calls=48, max_output_tokens=16000,
                              max_total_tokens=1_000_000, effort="high")

    def say(self, text, role="app"):
        if text:
            self.messages.append({"role": role, "text": str(text)})

    def _invalidate(self):
        self.session._set_outcome()
        self.session.end_conversation()
        self.session.selected_files = None
        self.session.pending_recovery_run = None
        self._resume_recovery = None
        self._revision += 1
        self.stage = "resume" if self.decisions else "empty"
        self.problem = ""

    def attach(self, paths, *, template=False):
        checked = []
        for value in paths:
            path = Path(value).resolve()
            if not path.is_file() or path.suffix.lower() != ".docx" or path.name.startswith("~$"):
                raise ValueError(f"Choose a Word .docx file: {path.name}")
            if path not in checked:
                checked.append(path)
        if not checked:
            return
        if template and len(checked) != 1:
            raise ValueError("Choose one template document.")
        before = (list(self.session.documents), self.session.template)
        if template:
            self.session.template = checked[0]
            self.session.documents = [p for p in self.session.documents if p != checked[0]]
        else:
            for path in checked:
                if path != self.session.template and path not in self.session.documents:
                    self.session.documents.append(path)
        if before != (self.session.documents, self.session.template):
            self._invalidate()
            if self.decisions:
                self.say("The attachments changed. I’ll check the updated documents against your request.")
        self.save()

    def remove(self, path, *, template=False):
        if template:
            self.session.template = None
        else:
            self.session.documents = [p for p in self.session.documents if p != Path(path)]
        self._invalidate()
        self.save()

    def _request(self):
        if not self.decisions:
            return ""
        return self.decisions[0] + ("\n\nUser decisions and revisions:\n" + "\n".join(self.decisions[1:])
                                    if len(self.decisions) > 1 else "")

    def _compile(self, log):
        self.session.selected_files = None
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
                    "candidates": candidates, "options": [f"Paragraph {c['paragraph_index'] + 1} "
                    f"in {review_display.readable(c.get('scope', 'document'))}: {c.get('text', '')}" for c in candidates],
                    "text": f"Which passage(s) in {Path(record['files'][0]).name} should change? "
                    "Reply with the number, or numbers separated by commas."}
        return None

    def perform(self, action, *, text="", token="", log=lambda _: None):
        """Worker entry point. One allowance covers every nested model call and every turn."""
        self.problem = ""
        self.save(in_progress=True)
        def checkpointed_log(message):
            self.save(in_progress=True)
            log(message)
        try:
            with llm.budget_scope(self._budget):
                if action == "send":
                    self._send(text, checkpointed_log)
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
            if self._budget.exhausted_reason and self.stage not in {"done", "ready", "preview"}:
                self.stage = "budget"
            self.save()
        return self.view()

    def _send(self, text, log):
        text = text.strip()
        if not text:
            return
        self.session.files(outcome=False)  # reject missing attachments before consuming an answer
        if not self.session.claude_ready():
            raise ValueError("Connect your Anthropic API key in Settings to start the conversation.")
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
                self._settle(log, preview=False)
                return
        choices = self._choices()
        picks = self._numbers(text, len(choices))
        if len(picks) == 1 and choices:
            chosen = choices[picks[0]]
            self.say(text, "user")
            self.decisions.append(chosen.label)
            self.decisions.extend(chosen.words)
            self.word_sources.extend(chosen.words)
            self.save(in_progress=True)
            self.session.reply(choice_id=chosen.id, render=self.render, log=log)
        else:
            self.say(text, "user")
            self.decisions.append(text)
            self.word_sources.append(text)
            self.save(in_progress=True)
            if self.session.conversation is not None and self.session.conversation_matches(self.session.conversation.request):
                self.session.reply(text, render=self.render, log=log)
            elif self.session.recovery is not None and self.stage == "question":
                self.session.resolve_remaining(log, request=text, options=self._recovery_options())
            else:
                self._compile(log)
        self._settle(log)

    @staticmethod
    def _numbers(text, count):
        if not re.fullmatch(r"\s*\d+(?:\s*[, ]\s*\d+)*\s*", text):
            return []
        values = list(dict.fromkeys(int(v) - 1 for v in re.findall(r"\d+", text)))
        return values if all(0 <= v < count for v in values) else []

    def _choices(self):
        return [choice for choice in getattr(self.session.turn, "choices", [])
                if choice.kind not in {"custom_transform", "recovery_plan"}]

    def _collect_conversation(self):
        c = self.session.conversation
        if c is None:
            return
        if c.id != self._conversation_id:
            self._conversation_id, self._seen = c.id, 0
        for entry in c.entries[self._seen:]:
            # App notes contain implementation telemetry; render our own state instead.
            if entry.role != "claude":
                continue
            if c.recovery_active or any(choice.get("kind") in {"custom_transform", "recovery_plan", "recovery_target"}
                                        for choice in entry.choices):
                continue
            self.say(entry.text, "assistant")
        self._seen = len(c.entries)

    def _settle(self, log, *, preview=True):
        s = self.session
        self._collect_conversation()
        # Preparing a custom method runs verified sample trials. Delivery still requires
        # the user's approval of the resulting preview, like an ordinary edit.
        custom = next((c for c in getattr(s.turn, "choices", []) if c.kind == "custom_transform"), None)
        if custom and s.pending_custom_transform is None and s.generated is None:
            s.reply(choice_id=custom.id, render=self.render, log=log)
        if preview and s.recovery is None and s.pending_custom_transform is None and (s.spec or s.generated or s.chain):
            log("Preparing and checking the preview…")
            s.make_preview(self.render, log)
        self._collect_conversation()
        self._revision += 1
        plan = self._recovery_plan()
        if plan or s.pending_custom_transform is not None:
            self.stage = "plan"
        elif s.recovery is not None:
            self.stage = "ready" if s.recovery.ready else "question"
        elif s.preview is not None or s.generated_preview is not None:
            self.stage = "preview"
        else:
            self.stage = "question"
        if self._budget.exhausted_reason and self.stage == "question":
            self.stage = "budget"

    def _recovery_plan(self):
        return next((c for c in getattr(self.session.turn, "choices", []) if c.kind == "recovery_plan"), None)

    def _recovery_options(self):
        from .recovery import RecoveryOptions
        return RecoveryOptions(max_cost_usd=self._budget.max_cost_usd, max_calls=self._budget.max_calls, max_attempts=8,
            max_output_tokens=16000, max_total_tokens=self._budget.max_total_tokens, effort="high", render=self.render,
            visual_review=self.session.visual_check, include_nearby_inputs=False)

    def action_token(self):
        s = self.session
        preview, preview_token = s._current_review()
        visual = getattr(preview, "review", None)
        plan = self._recovery_plan()
        return _fingerprint({"revision": self._revision, "stage": self.stage,
            "documents": s.documents, "template": s.template, "spec": s.spec, "chain": s.chain,
            "preview": preview_token, "recovery_plan": plan.snapshot if plan else None,
            "visual_findings": visual.to_dict() if visual else None,
            "warnings": getattr(preview, "warnings", [])})

    def _primary(self, token, log):
        if token != self.action_token():
            raise ValueError("The documents or plan changed. Review the updated plan first.")
        s = self.session
        if self.stage == "budget":
            # Extend the same ledger; do not reset accumulated usage or nested limits.
            self._budget.max_cost_usd += ALLOWANCE_USD
            self._budget.max_calls += 48
            self._budget.max_total_tokens += 1_000_000
            self._budget.exhausted_reason = None
            self.stage = "resume"
            self.say("Continuing with another estimated $10 allowance.")
            self._primary(self.action_token(), log)
            return
        if self.stage == "resume":
            if self._resume_recovery and (Path(self._resume_recovery) / "recovery.json").is_file():
                s.load_recovery(self._resume_recovery)
                self._resume_recovery = None
                self._settle(log, preview=False)
                return
            self._resume_recovery = None
            if s.recovery is not None:
                s.resolve_remaining(log, options=self._recovery_options())
                self._settle(log, preview=False)
                return
            self._compile(log)
            self._settle(log)
            return
        plan = self._recovery_plan()
        if plan:
            s.reply(choice_id=plan.id, render=self.render, log=log)
            self._settle(log, preview=False)
            return
        if self.stage not in {"plan", "preview", "ready"}:
            raise ValueError("Answer the open question before continuing.")
        if s.pending_custom_transform is not None:
            s.continue_custom_transform(log, self.render)
            self._settle(log, preview=False)
            return
        # A blocking visual finding is a separate, explicitly labelled decision on the displayed preview.
        pv, pin = s._current_review()
        visual = getattr(pv, "review", None)
        if visual is not None and visual.blocking():
            s.acknowledge_review(pin)
        log("Creating and checking your edited copies…")
        s.run(False, self.render, log)
        self._collect_results()
        if s.last_batch is not None and any(f.status != "PASS" for f in s.last_batch.files):
            log("Checking the files that still need work…")
            conversation = s.conversation
            s.resolve_remaining(log, options=self._recovery_options())
            if conversation is not None:
                from .continuous_recovery import result_turn
                conversation.recovery_active = True
                s.conversation = conversation
                s.turn = result_turn(conversation, s.recovery)
            self._settle(log, preview=False)
            return
        if s.last_trials is not None and any(not f.passed for f in s.last_trials):
            log("Checking the files that still need work…")
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log, preview=False)
            return
        if s.recovery is not None and len(s.recovery.ready) < len(s.recovery.files):
            self.stage = "question"
            self._revision += 1
            self.say("The ready copies are saved. The remaining documents still need attention.")
            return
        self.stage = "done"
        self._revision += 1
        self.say(f"{len(self.results)} document copies are ready. You can open them below or describe another change.")

    def _collect_results(self):
        s = self.session
        result = s.last_recovery or s.last_batch
        if result is not None:
            for item in result.files:
                if item.delivered_to:
                    self.results[str(item.source)] = str(item.delivered_to)
                elif (getattr(item, "noop", False) and item.status == "PASS" and item.output
                      and Path(item.output).is_file()):
                    # The engine verifies and saves identical copies too, but intentionally leaves
                    # delivered_to empty to distinguish them from changed documents in its reports.
                    self.results[str(item.source)] = str(item.output)
        for item in s.last_trials or []:
            if item.passed and item.output:
                self.results[str(item.source)] = str(item.output)

    def view(self):
        s = self.session
        plan = self._recovery_plan()
        contract = (plan.snapshot.get("contract") if plan else
                    (s.recovery.key.get("adaptive_contract") if s.recovery else None))
        question = self._target_question()
        choices = self._choices()
        questions = []
        if question:
            questions.append(question["text"] + "\n" + "\n".join(
                f"{i}. {value}" for i, value in enumerate(question["options"], 1)))
        elif choices:
            for i, choice in enumerate(choices, 1):
                words = "\nProposed wording:\n" + "\n".join(choice.words) if choice.words else ""
                questions.append(f"{i}. {choice.label}" + words)
            questions.append("Reply with a number to choose, or describe what you want in your own words.")
        elif self.stage == "question":
            for q in s.questions:
                questions.append(q.get("text", str(q)) if isinstance(q, dict) else str(q))
            if not questions:
                if s.recovery is not None:
                    questions.extend(s.recovery.questions)
                    if not questions:
                        questions.extend(f"{Path(f.source).name}: {f.reason}" for f in s.recovery.files if f.status != "READY")
                if not questions:
                    questions.append(getattr(s.turn, "message", "") or "Tell me what you’d like to change or clarify.")
        pv, _ = s._current_review()
        samples = getattr(pv, "samples", [])
        warnings = list(getattr(pv, "warnings", []) or [])
        from .settings import output_root_warnings
        warnings.extend(output_root_warnings(s.out_root))
        visual = getattr(pv, "review", None)
        blocking = bool(visual is not None and visual.blocking())
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
        labels = {"plan": "Prepare preview", "preview": "Create edited copies", "ready": "Save ready copies",
                  "resume": "Continue", "budget": "Allow another estimated $10"}
        if blocking and self.stage in {"preview", "ready"}:
            labels[self.stage] = "I reviewed the flagged pages — create copies"
        changes = review_display.effects(contract) if contract else review_display.spec_changes(s.spec, s.chain)
        if not changes and s.generated is not None:
            changes = [{"title": "Requested changes", "summary": s.generated.proposal,
                        "details": [s.generated.request, "The sample copies passed independent verification."]}]
        return {"stage": self.stage, "messages": list(self.messages), "effects": changes,
                "targets": review_display.recovery_targets(s.recovery) if s.recovery else review_display.spec_bindings(s.spec, s.chain),
                "document_count": len(s.recovery.files) if s.recovery else len(s.documents),
                "questions": questions, "samples": samples,
                "warnings": list(dict.fromkeys(warnings)), "results": dict(self.results),
                "action": labels.get(self.stage, ""), "token": self.action_token(),
                "budget_paused": self.stage == "budget", "cost": self._budget.estimated_cost_usd,
                "blocking": blocking}

    def save(self, *, in_progress=False):
        if not self.journal:
            return
        atomic_json(self.journal, {"version": 1, "documents": [str(p) for p in self.session.documents],
            "template": str(self.session.template) if self.session.template else None,
            "messages": self.messages, "decisions": self.decisions, "word_sources": self.word_sources, "results": self.results,
            "recovery": str(self.session.recovery.out_dir) if self.session.recovery else
                        str(self.session.recovery_checkpoint) if self.session.recovery_checkpoint else self._resume_recovery,
            "budget": self._budget.to_dict(), "in_progress": in_progress})

    def restore(self):
        if not self.journal or not self.journal.is_file():
            return False
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("This saved conversation uses an unsupported version.")
        if not isinstance(data.get("decisions"), list) or not all(isinstance(x, str) for x in data["decisions"]):
            raise ValueError("The saved conversation could not be read.")
        if (not isinstance(data.get("documents"), list) or not all(isinstance(p, str) for p in data["documents"])
                or data.get("template") is not None and not isinstance(data["template"], str)
                or not isinstance(data.get("messages"), list)
                or not all(isinstance(m, dict) and isinstance(m.get("text"), str) and
                           m.get("role") in {"app", "user", "assistant"} for m in data["messages"])
                or not isinstance(data.get("results"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["results"].items())
                or not isinstance(data.get("budget"), dict)):
            raise ValueError("The saved conversation could not be read.")
        self.session.documents = [Path(p) for p in data.get("documents", [])]
        self.session.template = Path(data["template"]) if data.get("template") else None
        self.messages = data.get("messages", [])
        self.decisions = data["decisions"]
        self.word_sources = data.get("word_sources", [])
        if not isinstance(self.word_sources, list) or not all(isinstance(x, str) for x in self.word_sources):
            raise ValueError("The saved wording approvals could not be read.")
        self.results = data.get("results", {})
        self._resume_recovery = data.get("recovery")
        budget = data.get("budget", {})
        self._budget = llm.CallBudget(max_cost_usd=budget.get("max_cost_usd", ALLOWANCE_USD),
            max_calls=budget.get("max_calls", 48), max_total_tokens=budget.get("max_total_tokens", 1_000_000),
            max_output_tokens=16000, effort="high")
        # Retain conservative accounting; never reset spending by reopening the app.
        self._budget.calls = int(budget.get("calls", 0))
        self._budget._reserved_tokens = int(budget.get("accounted_tokens", 0))
        cost = budget.get("estimated_cost_usd", 0)
        self._budget._reserved_cost_usd = float(cost or 0)
        self._budget.exhausted_reason = budget.get("exhausted_reason")
        if data.get("in_progress") or cost is None:
            self._budget.exhausted_reason = "Previous work stopped before its final usage was recorded."
            self._budget._reserved_cost_usd = max(self._budget.max_cost_usd, self._budget._reserved_cost_usd)
        self.stage = "budget" if self._budget.exhausted_reason else "resume" if self.decisions else "empty"
        if self.decisions:
            self.say("Your conversation is restored. I’ll recheck the documents before making further changes.")
        return True
