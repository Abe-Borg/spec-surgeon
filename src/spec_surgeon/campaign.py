"""Durable coordination of independently scoped projects over the existing edit engine.

The campaign stores intent, decisions and artifact references. It never restores a human
approval from JSON or implements a second delivery path: recovery reloads revalidate evidence,
and every export uses deliver_recovery with the current source and output pins.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Sequence

from . import guard
from .inventory import digest, inspect_source, inventory_inputs, stable_id
from .project_evidence import approved_context_words, validate_context


SCHEMA_VERSION = 1
STATE_FILE = "campaign.json"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _hash(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _atomic(path: Path, value):
    fd, temporary = tempfile.mkstemp(prefix=".campaign-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Campaign:
    def __init__(self, out_dir: Path, state: Dict[str, Any]):
        self.out_dir = Path(out_dir).resolve()
        self._state = copy.deepcopy(state)
        self._revision = state["revision"]
        self._lock_owner = None

    @property
    def projects(self):
        return self._state["projects"]

    @property
    def documents(self):
        return self._state["documents"]

    def to_dict(self):
        result = copy.deepcopy(self._state)
        for project in result["projects"].values():
            ledgers = project.get("resource_history", []) + project.get("planning_resource_history", [])
            project["resource_totals"] = {"calls": sum(x.get("calls", 0) for x in ledgers),
                "accounted_tokens": sum(x.get("accounted_tokens", 0) for x in ledgers),
                "estimated_cost_usd": (sum(x.get("estimated_cost_usd", 0) for x in ledgers)
                    if all(x.get("estimated_cost_usd") is not None for x in ledgers) else None)}
        return result

    def to_text(self):
        lines = [f"Campaign: {len(self.documents)} document(s), {len(self.projects)} project group(s)."]
        view = self.to_dict()
        for p in self.projects.values():
            counts = {}
            for doc_id in p["document_ids"]:
                status = self.documents[doc_id]["status"]
                counts[status] = counts.get(status, 0) + 1
            lines.append(f"{p['label']} [{p['id']}]: {p['status']} — "
                         + ", ".join(f"{n} {status.lower()}" for status, n in sorted(counts.items())))
            if p.get("provisional"):
                lines.append("  Project boundary inferred from its folder; correct the grouping if needed.")
            for q in p.get("questions", []):
                lines.append(f"  Question [{q['id']}]: {q['text']}")
            if p.get("error"):
                lines.append("  " + p["error"])
            totals = view["projects"][p["id"]]["resource_totals"]
            if totals["calls"]:
                lines.append(f"  Resources across planning and execution: {totals['calls']} calls, "
                             f"{totals['accounted_tokens']:,} tokens accounted; estimated USD {totals['estimated_cost_usd']}.")
            proposal = p.get("proposed_plan")
            if proposal:
                lines += [f"  Plan {proposal['id']} — {proposal['status']}",
                          "  Request: " + proposal["request"],
                          "  Scope: " + ", ".join(self.documents[d]["source"] for d in proposal["document_ids"]),
                          "  Rationale: " + proposal.get("rationale", "")]
                lines += ["  Risk: " + r for r in proposal.get("risks", [])]
                lines += ["  Evidence: " + e for e in proposal.get("evidence", [])]
                if proposal.get("contract"):
                    lines += ["  Exact proposed outcomes and wording (review before approving):", _json(proposal["contract"])]
        lines.append(f"Saved workspace: {self.out_dir}")
        return "\n".join(lines)

    @contextmanager
    def _lock(self):
        owner = threading.get_ident()
        if self._lock_owner == owner:
            yield
            return
        from .recovery_state import session_lock
        with session_lock(self.out_dir, filename=".campaign.lock",
                          message="This campaign is already being changed by another worker."):
            self._lock_owner = owner
            try:
                yield
            finally:
                self._lock_owner = None

    def _save(self, action: str, details=None):
        with self._lock():
            path = self.out_dir / STATE_FILE
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if disk.get("revision") != self._revision:
                    raise ValueError("The campaign changed in another window; reload it before continuing.")
            self._revision += 1
            self._state["revision"] = self._revision
            self._state["updated_at"] = _now()
            event = {"revision": self._revision, "time": self._state["updated_at"], "action": action,
                     "details": details or {}}
            self._state["last_event"] = event
            _atomic(path, self._state)
            with (self.out_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def _project(self, project_id):
        if project_id not in self.projects:
            raise ValueError(f"Unknown project: {project_id}")
        return self.projects[project_id]

    def _invalidate(self, project, reason):
        project.setdefault("archived_runs", []).extend(project.get("runs", []))
        project["runs"], project["questions"] = [], []
        project["status"] = "PENDING" if project.get("goal") else "NEEDS_GOAL"
        project.pop("error", None)
        for doc_id in project["document_ids"]:
            doc = self.documents[doc_id]
            if doc["status"] != "UNREADABLE":
                doc["status"] = "PENDING"
            for key in ("output", "output_md5", "delivered_to", "binding", "adaptive_verification"):
                doc.pop(key, None)
            doc["reason"] = reason

    def set_project(self, doc_ids, project_id):
        ids = list(dict.fromkeys(doc_ids))
        if not ids or any(d not in self.documents for d in ids):
            raise ValueError("Choose existing document IDs to assign.")
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("A project ID must be nonempty text.")
        if project_id not in self.projects:
            self.projects[project_id] = {"id": project_id, "label": project_id, "context": {}, "context_source": None,
                                         "provisional": False, "evidence": [{"kind": "user_assignment"}],
                                         "unresolved": [], "document_ids": [], "goal": {}, "decisions": [],
                                         "runs": [], "questions": [], "status": "NEEDS_GOAL"}
        affected = set()
        target = self.projects[project_id]
        for doc_id in ids:
            doc = self.documents[doc_id]
            old = self.projects[doc["project_id"]]
            if old["id"] == project_id:
                continue
            old["document_ids"].remove(doc_id)
            old.get("document_goals", {}).pop(doc_id, None)
            if "review_required_docs" in old:
                old["review_required_docs"] = [d for d in old["review_required_docs"] if d != doc_id]
            affected.update((old["id"], project_id))
            target["document_ids"].append(doc_id)
            if "document_goals" in target:
                # An existing scoped approval never covers a newly assigned file,
                # even if that file belonged to this project before.
                target["document_goals"][doc_id] = {}
                target.setdefault("review_required_docs", []).append(doc_id)
            doc["project_id"] = project_id
            doc["evidence"].append({"kind": "user_project_assignment", "project_id": project_id})
        for pid in affected:
            self._invalidate(self.projects[pid], "Project membership changed; scope must be planned again.")
        self._save("assign_documents", {"document_ids": ids, "project_id": project_id})
        return self

    def set_project_context(self, project_id, context: dict):
        from .project_evidence import validate_context
        context = validate_context(context)
        _json(context)
        project = self._project(project_id)
        project["context"] = copy.deepcopy(context)
        project["context_source"] = "user"
        project["context_sha256"] = None
        project["provisional"] = False
        project["unresolved"] = []
        project["label"] = str(context.get("name") or context.get("project_name") or project["label"])
        if project.get("proposed_plan"):
            project["proposed_plan"]["status"] = "STALE"
        self._invalidate(project, "Project facts changed; the affected scope must be planned again.")
        self._save("set_project_context", {"project_id": project_id})
        return self

    def set_project_fact(self, project_id, fact):
        from .project_evidence import set_fact
        return self.set_project_context(project_id, set_fact(self._project(project_id)["context"], fact))

    def supersede_project_fact(self, project_id, old_id, replacement):
        from .project_evidence import supersede_fact
        return self.set_project_context(project_id, supersede_fact(self._project(project_id)["context"], old_id, replacement))

    def set_standards_register(self, project_id, entries):
        from .project_evidence import set_standards_register
        return self.set_project_context(project_id, set_standards_register(self._project(project_id)["context"], entries))

    def set_goal(self, project_id, request: str, *, spec=None, chain=None, template=None,
                 approved_words=(), adaptive_contract=None, doc_ids=None):
        from .chain import validate_chain
        from .editspec import validate_spec
        if not isinstance(request, str) or not request.strip():
            raise ValueError("A project goal needs the user's request.")
        if sum(x is not None for x in (spec, chain, adaptive_contract)) > 1:
            raise ValueError("Choose a spec, chain, or adaptive contract for this goal.")
        if spec is not None:
            validate_spec(spec)
        if chain is not None:
            validate_chain(chain)
        if adaptive_contract is not None:
            from .goals import validate_contract
            validate_contract(adaptive_contract)
        words = list(approved_words)
        if any(not isinstance(w, str) for w in words):
            raise ValueError("Approved words must be strings.")
        if template is not None:
            template = str(Path(template).resolve())
            if not Path(template).is_file():
                raise FileNotFoundError(template)
        project = self._project(project_id)
        ids = self._scope(project, doc_ids)
        goal = {"request": request.strip(), "spec": copy.deepcopy(spec), "chain": copy.deepcopy(chain),
                "template": template, "approved_words": words, "adaptive_contract": copy.deepcopy(adaptive_contract)}
        _json(goal)
        old = copy.deepcopy(project.get("goal") or {})
        project["goal"] = goal
        project["goal_scope"] = ids
        for field in ("planning_draft", "proposed_plan"):
            project.pop(field, None)
        if doc_ids is None:
            project.pop("document_goals", None)
            project.pop("review_required_docs", None)
            project["decisions"] = []
            self._invalidate(project, "The project goal changed.")
        else:
            scoped = project.setdefault("document_goals", {d: copy.deepcopy(old) for d in project["document_ids"]})
            for doc_id in ids:
                scoped[doc_id] = copy.deepcopy(goal)
            wanted = set(ids)
            decisions = []
            for decision in project["decisions"]:
                retained = [d for d in decision["document_ids"] if d not in wanted]
                if retained:
                    decision = copy.deepcopy(decision)
                    decision["document_ids"] = retained
                    if "applied_documents" in decision:
                        decision["applied_documents"] = [d for d in decision["applied_documents"] if d in retained]
                    decisions.append(decision)
            project["decisions"] = decisions
            project["review_required_docs"] = [d for d in project.get("review_required_docs", []) if d not in wanted]
            self._invalidate_documents(project, ids, "The approved goal changed for these documents.")
        self._save("set_goal", {"project_id": project_id, "document_ids": ids})
        return self

    def _scope(self, project, doc_ids=None):
        ids = list(dict.fromkeys(project["document_ids"] if doc_ids is None else doc_ids))
        if not ids or not set(ids).issubset(project["document_ids"]):
            raise ValueError("Choose a nonempty document scope within this project.")
        return ids

    def save_plan_draft(self, project_id, request, *, doc_ids=None, template=None):
        project = self._project(project_id)
        if not isinstance(request, str):
            raise ValueError("A planning request must be text.")
        ids = self._scope(project, doc_ids)
        attached = (project.get("goal") or {}).get("template") if template is None else str(Path(template).resolve()) if template else None
        if attached and not Path(attached).is_file():
            raise FileNotFoundError(attached)
        project["planning_draft"] = {"request": request, "document_ids": ids, "template": attached}
        self._save("planning_draft", {"project_id": project_id})
        return self

    def plan(self, project_id=None, request=None, *, doc_ids=None, template=None, options=None, client=None, log=None):
        from . import campaign_planner, recovery
        options = options or recovery.RecoveryOptions.from_snapshot(self._state.get("resource_options", {}))
        options.validate()
        selected = [self._project(project_id)] if project_id else list(self.projects.values())
        if doc_ids is not None and (not doc_ids or not set(doc_ids).issubset(self.documents)):
            raise ValueError("Choose a nonempty scope of known planning document IDs.")
        if project_id is not None and doc_ids is not None:
            self._scope(selected[0], doc_ids)
        notify = log or (lambda _: None)
        with self._lock():
            self._state["resource_options"] = asdict(options)
            for project in selected:
                draft_input = project.get("planning_draft") or {}
                chosen = doc_ids if doc_ids is not None else draft_input.get("document_ids", project["document_ids"])
                ids = [d for d in project["document_ids"] if d in chosen]
                if not ids:
                    continue
                words = request if request is not None else draft_input.get("request") or (project.get("goal") or {}).get("request") or self._state.get("request")
                if not isinstance(words, str) or not words.strip():
                    if project_id is None:
                        continue
                    raise ValueError(f"Give the requested change for {project['label']} before planning.")
                attached = template if template is not None else draft_input.get("template", (project.get("goal") or {}).get("template"))
                self.save_plan_draft(project["id"], words, doc_ids=ids, template=attached or "")
                failures = self._check_sources(project, [])
                if failures:
                    raise ValueError("; ".join(failures))
                attached = project["planning_draft"]["template"]
                source_pins = {d: self.documents[d]["source_sha256"] for d in ids}
                key = {"request": words, "source_pins": source_pins, "context_digest": _hash(project["context"]),
                       "template": attached, "template_sha256": digest(Path(attached)) if attached else None}
                for d in ids:
                    if self.documents[d]["source_sha256"] and digest(Path(self.documents[d]["source"])) != source_pins[d]:
                        raise ValueError("A selected source changed; refresh it before planning.")
                prior = project.get("planning_state") if project.get("planning_key") == _hash(key) else None
                plan_project = copy.deepcopy(project)
                plan_project["goal"] = {**(project.get("goal") or {}), "template": attached}
                plan_project["planning_artifact_dir"] = str(self.out_dir / "planning" / stable_id("group", project["id"]) / _hash(key))
                ledger_index = len(project.setdefault("planning_resource_history", []))
                project["planning_resource_history"].append({})

                def checkpoint(state):
                    project["planning_state"], project["planning_key"] = copy.deepcopy(state), _hash(key)
                    if state.get("budget"):
                        project["planning_resource_history"][ledger_index] = copy.deepcopy(state["budget"])
                    self._save("planning_checkpoint", {"project_id": project["id"]})

                try:
                    proposed, state = campaign_planner.propose(plan_project, [self.documents[d] for d in ids], words,
                                                              options, client=client, log=notify, prior=prior,
                                                              checkpoint=checkpoint)
                except Exception as exc:
                    proposed = {"status": "PAUSED", "request": words, "contract": None,
                                "rationale": f"{type(exc).__name__}: {exc}", "risks": [], "evidence": [], "questions": []}
                if project.get("proposed_plan"):
                    project.setdefault("plan_history", []).append(project["proposed_plan"])
                proposed.update(id=uuid.uuid4().hex, document_ids=ids, **key)
                proposed["digest"] = _hash(proposed)
                project["proposed_plan"] = proposed
                project["review_required_docs"] = list(set(project.get("review_required_docs", [])) | set(ids))
                project["questions"] = [q for q in project["questions"] if q.get("kind") != "planning"]
                for n, q in enumerate(proposed["questions"]):
                    q.update(id=f"plan-{proposed['id']}-{n}", kind="planning")
                    project["questions"].append(copy.deepcopy(q))
                # Questions have stable IDs too; pin the exact displayed proposal after adding them.
                proposed["digest"] = _hash({k: v for k, v in proposed.items() if k != "digest"})
                self._save("plan_proposed", {"project_id": project["id"], "plan_id": proposed["id"], "status": proposed["status"]})
        return self

    def _invalidate_documents(self, project, ids, reason):
        wanted = set(ids)
        runs, retained = [], []
        for run in project.get("runs", []):
            scope = {run["document_id"]} if run.get("document_id") else set(run.get("document_ids") or project["document_ids"])
            if scope & wanted:
                if not run.get("document_id") and scope - wanted:
                    # A historical batch token covers all its originals. Move forward
                    # with separate sessions, retaining the old artifacts as history;
                    # re-prove unaffected files under their already approved intent.
                    project.setdefault("document_goals", {d: copy.deepcopy(project.get("goal") or {})
                                                          for d in project["document_ids"]})
                    for doc_id in scope - wanted:
                        self.documents[doc_id]["requires_revalidation"] = True
                runs.append(run)
            else:
                retained.append(run)
        project.setdefault("archived_runs", []).extend(runs)
        project["runs"] = retained
        project["questions"] = [q for q in project["questions"] if not set(q.get("document_ids", [])) & wanted]
        for doc_id in wanted:
            doc = self.documents[doc_id]
            if doc["status"] != "UNREADABLE":
                doc["status"] = "PENDING"
            for field in ("output", "output_md5", "delivered_to", "binding", "adaptive_verification", "resolution"):
                doc.pop(field, None)
            doc["reason"] = reason
        self._refresh_status(project)

    def approve_plan(self, project_id, plan_id):
        from .goals import approved_goal_words, validate_contract
        project = self._project(project_id)
        proposed = project.get("proposed_plan") or {}
        if proposed.get("id") != plan_id or proposed.get("status") != "PROPOSED":
            raise ValueError("Review a current proposed plan before approving its exact ID.")
        if proposed["digest"] != _hash({k: v for k, v in proposed.items() if k != "digest"}):
            raise ValueError("The plan changed since it was proposed; generate and review it again.")
        self._scope(project, proposed["document_ids"])
        draft = project.get("planning_draft") or {}
        if (draft.get("request") != proposed["request"]
                or set(draft.get("document_ids", [])) != set(proposed["document_ids"])
                or draft.get("template") != proposed.get("template")):
            raise ValueError("The planning draft changed since this proposal; generate and review a new plan.")
        if set(proposed["source_pins"]) != set(proposed["document_ids"]):
            raise ValueError("The proposed plan's source pins differ from its reviewed document scope.")
        validate_contract(proposed["contract"])
        failures = self._check_sources(project, [])
        if failures:
            raise ValueError("; ".join(failures))
        if proposed["context_digest"] != _hash(project["context"]):
            raise ValueError("Project context changed since the plan; plan again.")
        for doc_id, pin in proposed["source_pins"].items():
            if pin is None or digest(Path(self.documents[doc_id]["source"])) != pin:
                raise ValueError("A source changed since the plan; refresh and plan again.")
        if proposed.get("template") and digest(Path(proposed["template"])) != proposed["template_sha256"]:
            raise ValueError("The template changed since the plan; plan again.")
        for effect in proposed["contract"].get("effects", []):
            if effect.get("type") == "header_footer_template" and digest(Path(effect["template"])) != effect["template_sha256"]:
                raise ValueError("A contract template changed since the reviewed plan; plan again.")
        old = copy.deepcopy(project.get("goal") or {})
        scoped = project.setdefault("document_goals", {d: copy.deepcopy(old) for d in project["document_ids"]})
        goal = {"request": proposed["request"], "spec": None, "chain": None, "template": proposed.get("template"),
                "approved_words": approved_goal_words(proposed["contract"]), "adaptive_contract": copy.deepcopy(proposed["contract"]),
                "plan_id": plan_id, "approval": {"plan_digest": proposed["digest"], "approved_at": _now(),
                                                "source_pins": copy.deepcopy(proposed["source_pins"])}}
        for doc_id in proposed["document_ids"]:
            scoped[doc_id] = copy.deepcopy(goal)
        project["goal"] = copy.deepcopy(goal)
        project["review_required_docs"] = [d for d in project.get("review_required_docs", []) if d not in proposed["document_ids"]]
        self._invalidate_documents(project, proposed["document_ids"], "The user approved this scoped outcome plan.")
        proposed["status"] = "APPROVED"
        project.setdefault("approvals", []).append({"plan_id": plan_id, **goal["approval"], "document_ids": proposed["document_ids"]})
        self._save("plan_approved", {"project_id": project_id, "plan_id": plan_id, "document_ids": proposed["document_ids"]})
        return self

    def refresh(self, doc_ids=None):
        ids = list(self.documents if doc_ids is None else dict.fromkeys(doc_ids))
        if not set(ids).issubset(self.documents):
            raise ValueError("Unknown source document IDs.")
        changed = []
        with self._lock():
            for doc_id in ids:
                old = self.documents[doc_id]
                fresh = inspect_source(Path(old["source"]), doc_id=doc_id, project_id=old["project_id"], input_root=old["input_root"])
                if (fresh["source_identity"] == old["source_identity"] and fresh["source_sha256"] == old["source_sha256"]
                        and old["status"] != "UNREADABLE"):
                    continue
                self.documents[doc_id] = fresh
                fresh["source_history"] = old.get("source_history", []) + [{"time": _now(), "source_sha256": old["source_sha256"],
                    "source_identity": old["source_identity"], "output": old.get("output"), "status": old["status"]}]
                project = self.projects[old["project_id"]]
                self._invalidate_documents(project, [doc_id], "The user refreshed this source; resolve and verify its new bytes.")
                project.pop("error", None)
                if project.get("proposed_plan") and doc_id in project["proposed_plan"]["document_ids"]:
                    project["proposed_plan"]["status"] = "STALE"
                changed.append(doc_id)
            self._save("sources_refreshed", {"document_ids": ids, "changed": changed})
        return self

    def answer(self, project_id, text: str, question_id=None, files=None, *, candidate_ids=None, effect_id=None):
        project = self._project(project_id)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("An answer must contain the user's words.")
        question = next((q for q in project["questions"] if q["id"] == question_id), None) if question_id else None
        if question_id is not None and question is None:
            raise ValueError("That question is not pending in this project.")
        default_ids = question["document_ids"] if question else project["document_ids"]
        ids = list(default_ids if files is None else files)
        # Accept either stable document IDs or their exact source paths.
        paths = {self.documents[d]["source"]: d for d in project["document_ids"]}
        ids = [paths.get(str(d), d) for d in ids]
        if not ids or any(d not in project["document_ids"] for d in ids):
            raise ValueError("An answer can apply only to documents in this project.")
        if question is not None and not set(ids).issubset(question["document_ids"]):
            raise ValueError("An answer to this question must stay within its affected document scope.")
        decision = {"id": uuid.uuid4().hex, "text": text.strip(), "question_id": question_id,
                    "document_ids": ids, "created_at": _now(), "applied": False}
        if question and question.get("kind") == "planning":
            decision["kind"] = "planning"
        if candidate_ids is not None:
            if not isinstance(candidate_ids, (list, tuple)) or not all(isinstance(x, str) for x in candidate_ids):
                raise ValueError("Candidate IDs must be a list of the user's selected candidates.")
            decision["candidate_ids"] = list(candidate_ids)
        if effect_id is not None:
            decision["effect_id"] = effect_id
        project["decisions"].append(decision)
        project["status"] = "PENDING"
        self._save("answer", {"project_id": project_id, "decision": decision})
        return self

    def _check_sources(self, project, doc_ids=None):
        failures = []
        metadata = project.get("context_source")
        if metadata and metadata != "user" and project.get("context_sha256"):
            try:
                if digest(Path(metadata)) != project["context_sha256"]:
                    failures.append("PROJECT.json changed since inventory; review and set the project context again.")
            except OSError as exc:
                failures.append(f"Project metadata is no longer readable: {exc}")
        for doc_id in project["document_ids"] if doc_ids is None else doc_ids:
            doc = self.documents[doc_id]
            if doc["status"] == "UNREADABLE":
                continue
            try:
                identity = guard.Identity(**doc["source_identity"])
                why = guard.drifted(identity)
                if not why and digest(Path(doc["source"])) != doc["source_sha256"]:
                    why = "Source content changed since inventory."
            except (OSError, TypeError, ValueError) as exc:
                why = str(exc)
            if why:
                doc["status"], doc["reason"] = "DRIFTED", why
                failures.append(f"{Path(doc['source']).name}: {why}")
        return failures

    def _result(self, project, result, *, update_questions=True):
        by_path = {self.documents[d]["source"]: self.documents[d] for d in project["document_ids"]}
        for f in result.files:
            doc = by_path.get(str(f.source.resolve()))
            if doc is None:
                raise ValueError("A recovery result contains a document outside its project scope.")
            doc.update(status="EXPORTED" if f.status == "READY" and f.delivered_to else f.status,
                       reason=f.reason, output=str(f.output) if f.output else None,
                       output_md5=f.output_md5, delivered_to=f.delivered_to)
            if f.status == "READY":
                doc.pop("requires_revalidation", None)
        if update_questions:
            records = getattr(result, "question_records", [])
            if records:
                project["questions"] = [{"id": q["id"], "text": q["question"],
                                          "document_ids": [by_path[path]["id"] for path in q["files"] if path in by_path]}
                                         for q in records if q.get("status") == "open"]
            else:
                project["questions"] = [{"id": stable_id("question", q), "text": q,
                                          "document_ids": list(project["document_ids"])} for q in result.questions]
        self._refresh_status(project)
        project.pop("error", None)

    def _refresh_status(self, project):
        statuses = {self.documents[d]["status"] for d in project["document_ids"]}
        project["status"] = ("READY" if statuses <= {"READY", "EXPORTED", "REFERENCE"}
                             else "NEEDS_INPUT" if project["questions"]
                             else "PARTIAL" if "READY" in statuses else "UNRESOLVED")

    def _artifact_path(self, path):
        resolved = Path(path).resolve()
        if self.out_dir not in resolved.parents:
            raise ValueError("A campaign artifact must stay inside this workspace.")
        return resolved

    def _decision_request(self, project, decision):
        answer = {"answer": decision["text"], "external_decision_id": decision["id"]}
        question = next((q for q in project["questions"] if q["id"] == decision.get("question_id")), None)
        if question and set(question["document_ids"]) == set(decision["document_ids"]):
            answer["question_id"] = question.get("recovery_question_id", question["id"])
        else:
            answer["files"] = [self.documents[d]["source"] for d in decision["document_ids"]]
        for key in ("candidate_ids", "effect_id"):
            if key in decision:
                answer[key] = decision[key]
        return answer

    def _goal_for(self, project, doc_id):
        if "document_goals" in project:
            return project["document_goals"].get(doc_id, {})
        return project.get("goal") or {}

    def _execution_files(self, project):
        from .chain import chain_templates
        from .editspec import is_template_file, spec_templates
        files = []
        for doc_id in project["document_ids"]:
            goal = self._goal_for(project, doc_id)
            templates = chain_templates(goal["chain"]) if goal.get("chain") else spec_templates(goal.get("spec") or {})
            if goal.get("template"):
                templates.append(Path(goal["template"]))
            doc = self.documents[doc_id]
            if doc["status"] == "UNREADABLE":
                continue
            source = Path(doc["source"])
            if is_template_file(source, templates):
                doc["status"], doc["reason"] = "REFERENCE", "This document is the project's template input."
            else:
                files.append(source)
        return files

    def run(self, *, options=None, client=None, log=None, allow_model=None):
        from . import llm, recovery
        options = options or recovery.RecoveryOptions.from_snapshot(self._state.get("resource_options", {}))
        options.validate()
        notify = log or (lambda message: None)
        model_allowed = allow_model is not False and (client is not None or llm.credentials_found())
        with self._lock():
            self._preparation_failures = set()
            self._slice_results = {}
            preparation_failed = set()
            self._state["resource_options"] = asdict(options)
            self._save("run_started")
            # Complete safe built-in plans throughout the workspace before opening
            # any model investigation. A messy first project cannot delay clean later
            # projects until it spends its (potentially generous) entire allowance.
            for project in self.projects.values():
                if not project["document_ids"] or not project.get("goal"):
                    continue
                per_document = bool("document_goals" in project or project.get("review_required_docs")
                                    or project["goal"].get("adaptive_contract"))
                if not per_document:
                    goal = project["goal"]
                    if not (goal.get("spec") or goal.get("chain")):
                        continue
                    failures = self._check_sources(project)
                    if failures:
                        project["status"], project["error"] = "DRIFTED", "\n".join(failures)
                        preparation_failed.add(project["id"])
                        self._save("project_drifted", {"project_id": project["id"]})
                        continue
                    if project["runs"] and (self._artifact_path(project["runs"][-1]["path"]) / "recovery.json").is_file():
                        continue
                    try:
                        files = self._execution_files(project)
                        if files:
                            result = self._prepare_saved_project(project, files, options, notify)
                            self._result(project, result)
                            project["runs"][-1]["status"] = project["status"]
                            self._save("project_prepared", {"project_id": project["id"]})
                    except Exception as exc:
                        project["status"], project["error"] = "ERROR", f"{type(exc).__name__}: {exc}"
                        preparation_failed.add(project["id"])
                        self._save("project_preparation_paused", {"project_id": project["id"]})
                    continue
                try:
                    failures = self._check_sources(project, [])
                    if failures:
                        raise ValueError("; ".join(failures))
                    self._execution_files(project)
                    self._prepare_project(project, options, notify)
                except Exception as exc:
                    project["error"] = f"{type(exc).__name__}: {exc}"
                    self._save("project_preparation_paused", {"project_id": project["id"]})
            for project in self.projects.values():
                if not project["document_ids"] or not project.get("goal"):
                    continue
                if project["id"] in preparation_failed:
                    continue
                per_document = bool("document_goals" in project or project.get("review_required_docs")
                                    or (project.get("goal") or {}).get("adaptive_contract"))
                failures = self._check_sources(project, [] if per_document else None)
                if failures:
                    project["status"], project["error"] = "DRIFTED", "\n".join(failures)
                    self._save("project_drifted", {"project_id": project["id"]})
                    continue
                goal = project["goal"]
                pending = [d for d in project["decisions"] if not d["applied"]]
                notify(f"Project {project['label']}: {len(project['document_ids'])} document(s).")
                try:
                    files = self._execution_files(project)
                    if per_document:
                        if model_allowed:
                            self._run_adaptive(project, options, notify, client=client)
                        else:
                            self._pause_without_model(project, notify)
                        self._save("project_finished", {"project_id": project["id"]})
                        continue
                    if project["runs"] and (self._artifact_path(project["runs"][-1]["path"]) / "recovery.json").is_file():
                        run_dir = self._artifact_path(project["runs"][-1]["path"])
                        result = recovery.load_recovery(run_dir)
                        if pending and model_allowed:
                            for decision in pending:
                                answer = self._decision_request(project, decision)
                                result = recovery.resume_recovery(run_dir, request=answer, options=options,
                                                                  client=client, log=notify)
                                decision["applied"] = True
                                self._save("decision_applied", {"project_id": project["id"], "decision_id": decision["id"]})
                        elif model_allowed and len(result.ready) < len(result.files) and not result.questions:
                            result = recovery.resume_recovery(run_dir, options=options, client=client, log=notify)
                    else:
                        if not files:
                            project["status"] = "UNRESOLVED"
                            self._save("project_unreadable", {"project_id": project["id"]})
                            continue
                        if not model_allowed and not (goal.get("spec") or goal.get("chain")):
                            self._pause_without_model(project, notify)
                            continue
                        if goal.get("spec") or goal.get("chain"):
                            result = self._prepare_saved_project(project, files, options, notify)
                            run_dir = result.out_dir
                            if model_allowed and len(result.ready) < len(result.files) and not result.questions:
                                result = recovery.resume_recovery(run_dir, options=options, client=client, log=notify)
                        else:
                            run_dir = self.out_dir / "projects" / stable_id("group", project["id"]) / uuid.uuid4().hex
                            project["runs"].append({"path": str(run_dir), "created_at": _now(), "status": "RUNNING"})
                            self._save("project_started", {"project_id": project["id"], "path": str(run_dir)})
                            words = list(goal["approved_words"])
                            if project["context"]:
                                words.append(approved_context_words(project["context"]))
                            result = recovery.recover(files, goal["request"], run_dir,
                                template=goal["template"], approved_words=words, options=options, client=client,
                                log=notify, project_context=project["context"], input_files=[])
                        for decision in pending if model_allowed else []:
                            answer = self._decision_request(project, decision)
                            result = recovery.resume_recovery(run_dir, request=answer,
                                options=options, client=client, log=notify)
                            decision["applied"] = True
                            self._save("decision_applied", {"project_id": project["id"], "decision_id": decision["id"]})
                    self._result(project, result)
                    if not model_allowed:
                        self._pause_without_model(project, notify)
                    project["runs"][-1]["status"] = project["status"]
                except Exception as exc:
                    project["status"] = "ERROR"
                    project["error"] = f"{type(exc).__name__}: {exc}"
                    notify(f"{project['label']}: {project['error']}")
                self._save("project_finished", {"project_id": project["id"], "status": project["status"]})
            self._save("run_finished")
        return self

    def _prepare_saved_project(self, project, files, options, log):
        from . import recovery
        goal = project["goal"]
        run_dir = self.out_dir / "projects" / stable_id("group", project["id"]) / uuid.uuid4().hex
        project["runs"].append({"path": str(run_dir), "created_at": _now(), "status": "RUNNING"})
        self._save("project_started", {"project_id": project["id"], "path": str(run_dir)})
        words = list(goal["approved_words"])
        if project["context"]:
            words.append(approved_context_words(project["context"]))
        log(f"Project {project['label']}: trying the approved saved work without model calls.")
        return recovery.prepare_recovery(files, goal["request"], run_dir, spec=goal.get("spec"), chain=goal.get("chain"),
            template=goal["template"], approved_words=words, options=options, project_context=project["context"], input_files=[])

    def _pause_without_model(self, project, log):
        awaiting_answers = {doc_id for decision in project["decisions"] if not decision["applied"]
                            for doc_id in decision["document_ids"]}
        unresolved = [self.documents[d] for d in project["document_ids"]
                      if self._goal_for(project, d) and (self.documents[d]["status"] in ("PENDING", "UNRESOLVED", "ERROR")
                          or (d in awaiting_answers and self.documents[d]["status"] == "NEEDS_INPUT"))]
        if unresolved:
            message = ("Further investigation needs an Anthropic API key in Settings. "
                       "Verified copies are retained; review the remaining failures or supply a revised saved spec.")
            for doc in unresolved:
                if doc["status"] != "NEEDS_INPUT":
                    doc["status"] = "UNRESOLVED"
                reason = doc.get("reason") or ""
                if message not in reason:
                    doc["reason"] = reason + ("\n" if reason else "") + message
            project["error"] = message
            log(message)
        self._refresh_status(project)

    def _run_adaptive(self, project, options, log, client=None):
        from . import llm
        budget = llm.CallBudget(model=options.model or llm.DEFAULT_MODEL, effort=options.effort,
                               max_calls=options.max_calls, max_output_tokens=options.max_output_tokens,
                               max_total_tokens=options.max_total_tokens, max_cost_usd=options.max_cost_usd)
        index = len(project.setdefault("resource_history", []))
        project["resource_history"].append(budget.to_dict())
        def checkpoint():
            project["resource_history"][index] = budget.to_dict()
        def report(message):
            checkpoint()
            self._save("project_resource_checkpoint", {"project_id": project["id"]})
            log(message)
        try:
            with llm.budget_scope(budget):
                self._run_document_queue(project, options, report, client, checkpoint, budget)
        finally:
            checkpoint()
            self._save("project_resource_checkpoint", {"project_id": project["id"]})

    def _prepare_project(self, project, options, log):
        # Every prepared output passes ordinary recovery checks plus the independent
        # original-to-final goal gate. This phase makes no model requests.
        for doc_id in project["document_ids"]:
            doc = self.documents[doc_id]
            goal = self._goal_for(project, doc_id)
            contract = goal.get("adaptive_contract")
            can_prepare = contract or goal.get("spec") or goal.get("chain")
            if (not can_prepare or doc["status"] in ("UNREADABLE", "REFERENCE")
                    or doc_id in project.get("review_required_docs", [])):
                continue
            if any(r.get("document_id") == doc_id and (Path(r["path"]) / "recovery.json").is_file()
                   for r in project["runs"]):
                continue
            try:
                failures = self._check_sources(project, [doc_id])
                if failures:
                    raise ValueError("; ".join(failures))
                found = self._adaptive_document(project, doc, options, log=log, prepare_only=True)
                additions = (found if isinstance(found, list) else [found]) if found else []
                project["questions"] = [q for q in project["questions"] if q.get("document_id") != doc_id] + additions
            except Exception as exc:
                if doc["status"] != "DRIFTED":
                    doc["status"] = "ERROR"
                doc["reason"] = f"{type(exc).__name__}: {exc}"
                self._preparation_failures.add(doc_id)
            self._refresh_status(project)
            self._save("initial_document_prepared", {"project_id": project["id"], "document_id": doc_id})

    def _run_document_queue(self, project, options, log, client, resource_checkpoint, budget):
        questions = {}
        for q in project["questions"]:
            if q.get("document_id"):
                questions.setdefault(q["document_id"], []).append(q)
        planning_questions = [q for q in project["questions"] if q.get("kind") == "planning"]
        ids = list(project["document_ids"])
        scheduler = project.setdefault("scheduler", {})
        cursor = scheduler.get("next_document_id")
        if cursor in ids:
            start = ids.index(cursor)
            ids = ids[start:] + ids[:start]
        pending = [d for d in ids if self.documents[d]["status"] not in ("UNREADABLE", "REFERENCE")
                   and d not in project.get("review_required_docs", []) and d not in self._preparation_failures
                   and self._goal_for(project, d)]
        while pending:
            if budget.exhausted_reason or budget.calls >= budget.max_calls:
                break
            doc_id = pending.pop(0)
            doc = self.documents[doc_id]
            before = budget.calls
            # Commit the next turn before making a potentially interrupted provider
            # call. A small allowance or restart must not always privilege file zero.
            next_id = pending[0] if pending else doc_id
            scheduler.update(next_document_id=next_id, last_serviced_document_id=doc_id,
                             slices=scheduler.get("slices", 0) + 1, updated_at=_now())
            self._save("refinement_slice_started", {"project_id": project["id"], "document_id": doc_id})
            try:
                failures = self._check_sources(project, [doc_id])
                if failures:
                    raise ValueError("; ".join(failures))
                found = self._adaptive_document(project, doc, options, client=client, log=log, single_round=True)
                if found:
                    questions[doc_id] = found if isinstance(found, list) else [found]
                else:
                    questions.pop(doc_id, None)
                decision_waiting = any(not d["applied"] and d.get("kind") != "planning"
                    and doc_id in d["document_ids"] and doc_id not in d.get("applied_documents", [])
                    for d in project["decisions"])
                if doc["status"] not in ("READY", "EXPORTED") and (
                        decision_waiting or (doc.get("refinement_status") == "active" and budget.calls > before)):
                    pending.append(doc_id)
            except Exception as exc:
                if doc["status"] != "DRIFTED":
                    doc["status"] = "ERROR"
                doc["reason"] = f"{type(exc).__name__}: {exc}"
                questions.pop(doc_id, None)
            if budget.exhausted_reason and budget.calls == before:
                scheduler["next_document_id"] = doc_id
            elif pending:
                scheduler["next_document_id"] = pending[0]
            project["questions"] = planning_questions + [q for values in questions.values() for q in values]
            self._refresh_status(project)
            resource_checkpoint()
            log(f"{Path(doc['source']).name}: {doc['status']} — {doc.get('reason', '')}")
            self._save("adaptive_trial_finished", {"project_id": project["id"], "document_id": doc_id})
        project.pop("error", None)

    def _adaptive_document(self, project, doc, options, client=None, log=None, prepare_only=False, single_round=False):
        from . import goals, recovery
        doc_id, source = doc["id"], Path(doc["source"])
        goal = self._goal_for(project, doc_id)
        contract = goal.get("adaptive_contract")
        general = bool(goal.get("plan_id")) or contract is None or contract.get("kind") != "format_section_title"
        previous = next((r for r in reversed(project["runs"]) if r.get("document_id") == doc_id), None)
        if previous and (self._artifact_path(previous["path"]) / "recovery.json").is_file():
            result = recovery.load_recovery(self._artifact_path(previous["path"]))
            pending = [d for d in project["decisions"] if not d["applied"] and d.get("kind") != "planning"
                       and doc_id in d["document_ids"] and doc_id not in d.get("applied_documents", [])]
            if general and not prepare_only:
                for decision in (pending[:1] if single_round else pending):
                    answer = self._decision_request(project, decision)
                    if "files" in answer:
                        answer["files"] = [str(source)]
                    result = recovery.resume_recovery(result.out_dir, request=answer, options=options, client=client, log=log,
                        _single_round=single_round, _continue_from=self._slice_results.get(doc_id) if single_round else None)
                    if single_round:
                        self._slice_results[doc_id] = result
                    decision.setdefault("applied_documents", []).append(doc_id)
                    decision["applied"] = set(decision["applied_documents"]) >= set(decision["document_ids"])
                    self._save("decision_applied", {"project_id": project["id"], "decision_id": decision["id"], "document_id": doc_id})
                if not pending and not result.ready and not result.questions:
                    result = recovery.resume_recovery(result.out_dir, options=options, client=client, log=log,
                        _single_round=single_round, _continue_from=self._slice_results.get(doc_id) if single_round else None)
                    if single_round:
                        self._slice_results[doc_id] = result
            self._cache_refinement(doc, result)
            self._cache_binding(doc, result)
            self._check_adaptive(project, result)
            # This capability compiles a deterministic binding. Repeating an unchanged
            # failed plan cannot add evidence; a revised goal/binding creates a new run.
            self._result(project, result, update_questions=False)
            previous["status"] = doc["status"]
            return self._document_questions(doc, result)
        if general:
            run_dir = self.out_dir / "projects" / stable_id("group", project["id"]) / uuid.uuid4().hex
            run = {"path": str(run_dir), "document_id": doc_id, "created_at": _now(), "status": "RUNNING",
                   "goal_digest": _hash(goal)}
            project["runs"].append(run)
            self._save("adaptive_trial_started", {"project_id": project["id"], "document_id": doc_id})
            words = list(goal.get("approved_words") or [])
            if project["context"]:
                scopes = [{"kind": "document", "value": str(source)}]
                if (doc.get("section_status") == "identified" and doc.get("section")
                        and any(e.get("kind") == "section_heading" and e.get("value") == doc["section"]
                                for e in doc.get("evidence", []))):
                    scopes.append({"kind": "section", "value": doc["section"]})
                words.append(approved_context_words(project["context"], scopes=scopes))
            kwargs = dict(spec=goal.get("spec"), chain=goal.get("chain"), template=goal.get("template"),
                          approved_words=words, options=options, project_context=project["context"],
                          input_files=[], adaptive_contract=contract)
            if prepare_only:
                result = recovery.prepare_recovery([source], goal["request"], run_dir, **kwargs)
            else:
                result = recovery.recover([source], goal["request"], run_dir, client=client, log=log,
                                          _single_round=single_round, **kwargs)
                if single_round:
                    self._slice_results[doc_id] = result
            self._cache_refinement(doc, result)
            self._cache_binding(doc, result)
            self._check_adaptive(project, result)
            self._result(project, result, update_questions=False)
            run["status"] = doc["status"]
            return self._document_questions(doc, result)
        resolution = goals.resolve_goal(source, contract)
        doc["resolution"] = resolution
        binding = doc.get("binding") or resolution["binding"]
        if binding is None:
            doc["status"], doc["reason"] = "NEEDS_INPUT", resolution["reason"]
            return {"id": "target-" + doc_id, "text": source.name + ": " + resolution["reason"],
                    "document_id": doc_id, "document_ids": [doc_id], "candidates": resolution["candidates"]}
        doc["binding"] = binding
        candidate = goals.compile_goal(source, contract, binding)
        run_dir = self.out_dir / "projects" / stable_id("group", project["id"]) / uuid.uuid4().hex
        run = {"path": str(run_dir), "document_id": doc_id, "created_at": _now(), "status": "RUNNING"}
        project["runs"].append(run)
        self._save("adaptive_trial_started", {"project_id": project["id"], "document_id": doc_id})
        result = recovery.prepare_recovery([source], goal["request"], run_dir, spec=candidate,
                                           options=options, project_context=project["context"], input_files=[],
                                           adaptive_contract=contract, adaptive_binding=binding)
        self._check_adaptive(project, result)
        self._result(project, result, update_questions=False)
        run["status"] = doc["status"]
        return None

    @staticmethod
    def _cache_refinement(doc, result):
        states = [g.get("status") for g in result.session_state.get("groups", {}).values()]
        doc["refinement_status"] = "active" if "active" in states else "needs_input" if result.questions else "paused"
        if result.ready:
            doc["refinement_status"] = "complete"

    @staticmethod
    def _cache_binding(doc, result):
        binding = result.key.get("adaptive_bindings", {}).get(doc["source"]) or result.key.get("adaptive_binding")
        if binding:
            doc["binding"] = copy.deepcopy(binding)

    @staticmethod
    def _document_questions(doc, result):
        questions = []
        for q in getattr(result, "question_records", []):
            if q.get("status") != "open" or doc["source"] not in q.get("files", []):
                continue
            questions.append({**copy.deepcopy(q), "id": doc["id"] + ":" + q["id"],
                              "recovery_question_id": q["id"], "text": q["question"],
                              "document_id": doc["id"], "document_ids": [doc["id"]], "run_dir": str(result.out_dir)})
        return questions

    def bind(self, doc_id: str, candidate_id=None, *, effect_id=None, candidate_ids=None):
        """Record a deliberate user choice for one ambiguous target, preserving other work."""
        from .goals import choose_goal
        if doc_id not in self.documents:
            raise ValueError("Unknown document ID.")
        doc = self.documents[doc_id]
        project = self._project(doc["project_id"])
        contract = self._goal_for(project, doc_id).get("adaptive_contract")
        if contract is None:
            raise ValueError("This project has no adaptive target goal.")
        if doc["status"] in ("READY", "EXPORTED"):
            raise ValueError("Change the project goal before rebinding an already verified document.")
        if candidate_ids is not None and candidate_id is not None:
            raise ValueError("Provide candidate_id or candidate_ids, not both.")
        selected = candidate_ids if candidate_ids is not None else [candidate_id]
        if not selected or not isinstance(selected, (list, tuple)) or not all(isinstance(x, str) for x in selected):
            raise ValueError("Select one or more current candidate IDs.")
        if contract.get("kind") == "document_effects" or self._goal_for(project, doc_id).get("plan_id"):
            # Validate the actual source choice before recording it; only recovery may
            # install it into a checkpoint's signed approved-goal evidence.
            choose_goal(Path(doc["source"]), contract, list(selected), effect_id=effect_id, binding=doc.get("binding"))
            question = next((q for q in project["questions"] if q.get("document_id") == doc_id
                             and q.get("kind") == "goal_binding"), None)
            if question is None:
                raise ValueError("Run this approved goal to inspect its current target-choice question first.")
            return self.answer(project["id"], "Use the selected target candidates for this approved outcome.",
                               question_id=question["id"], candidate_ids=list(selected), effect_id=effect_id)
        doc["binding"] = choose_goal(Path(doc["source"]), contract, list(selected))
        decision = {"id": uuid.uuid4().hex, "kind": "target_binding", "document_ids": [doc_id],
                    "candidate_id": selected[0], "binding": copy.deepcopy(doc["binding"]),
                    "created_at": _now(), "applied": True}
        project["decisions"].append(decision)
        project["questions"] = [q for q in project["questions"] if q.get("document_id") != doc_id]
        doc["status"] = project["status"] = "PENDING"
        self._save("bind_target", {"project_id": project["id"], "decision": decision})
        return self

    def export(self, out_dir, *, acknowledge_review=False):
        from . import recovery
        destination = Path(out_dir).resolve()
        all_sources = [Path(d["source"]) for d in self.documents.values()]
        from .batch import check_inputs_outside
        check_inputs_outside(all_sources, destination)
        if destination == self.out_dir or destination in self.out_dir.parents:
            raise ValueError("Choose a separate export folder, not the campaign workspace or its ancestor.")
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("Choose a new empty export folder.")
        prepared = []
        with self._lock():
            # Revalidate every ready artifact before the first project is exported.
            for project in self.projects.values():
                if not any(self.documents[d]["status"] == "READY" for d in project["document_ids"]):
                    continue
                if any(self.documents[d].get("requires_revalidation") for d in project["document_ids"]
                       if self.documents[d]["status"] == "READY"):
                    raise ValueError("A previous shared batch needs per-document revalidation after its scope changed; run the workspace first.")
                ready_ids = {d for d in project["document_ids"] if self.documents[d]["status"] == "READY"}
                failures = self._check_sources(project, ready_ids)
                if failures:
                    raise ValueError("Source changed; export refused: " + "; ".join(failures))
                # An interrupted preparation can leave an audited run entry without a
                # checkpoint. Only the current READY evidence belongs in this export.
                if any(run.get("document_id") for run in project["runs"]):
                    selected, selected_ids = [], set()
                    for run in reversed(project["runs"]):
                        doc_id = run.get("document_id")
                        if doc_id in ready_ids and doc_id not in selected_ids:
                            selected.append(run)
                            selected_ids.add(doc_id)
                    if selected_ids != ready_ids:
                        raise ValueError("Some ready documents have no current recovery evidence; run the workspace before exporting.")
                else:
                    selected = project["runs"][-1:]
                for run in selected:
                    result = recovery.load_recovery(self._artifact_path(run["path"]))
                    if not result.ready:
                        continue
                    for f in result.ready:
                        doc = next((self.documents[d] for d in project["document_ids"]
                                    if self.documents[d]["source"] == str(f.source.resolve())), None)
                        if doc is None or doc.get("output") != str(f.output) or doc.get("output_md5") != f.output_md5:
                            raise ValueError("The campaign's current candidate differs from the verified recovery artifact.")
                    if result.review is not None and result.review.blocking() and not acknowledge_review:
                        raise ValueError("Review and acknowledge the blocking visual findings before export.")
                    self._check_adaptive(project, result)
                    prepared.append((project, run, result))
            if not prepared:
                raise ValueError("No verified documents are ready to export.")
            manifest = {"campaign_id": self._state["id"], "exported_at": _now(), "projects": []}
            for project, run, result in prepared:
                label = re.sub(r"[^\w.-]+", "-", project["label"]).strip(".-")[:60] or "project"
                target = destination / (label + "-" + stable_id("", project["id"])[-8:])
                if len(project["runs"]) > 1:
                    target /= stable_id("part", run["path"])
                recovery.deliver_recovery(result, target, acknowledge_review=acknowledge_review)
                self._result(project, result, update_questions=not run.get("document_id"))
                for f in result.ready:
                    doc = next(self.documents[d] for d in project["document_ids"] if self.documents[d]["source"] == str(f.source))
                    doc["status"] = "EXPORTED"
                manifest["projects"].append({"project_id": project["id"], "path": str(target),
                                             "documents": [f.to_dict() for f in result.ready]})
            destination.mkdir(parents=True, exist_ok=True)
            _atomic(destination / "export.json", manifest)
            self._state.setdefault("exports", []).append({"path": str(destination), "time": _now()})
            self._save("exported", {"destination": str(destination)})
        return destination

    def _check_adaptive(self, project, result):
        from .goals import verify_goal
        for f in result.ready:
            doc = next((self.documents[d] for d in project["document_ids"]
                        if self.documents[d]["source"] == str(f.source.resolve())), None)
            if doc is None:
                raise ValueError("A prepared result lies outside this project.")
            goal = self._goal_for(project, doc["id"])
            if "document_goals" in project and not goal:
                raise ValueError("This document has no approved scoped goal; review a new plan before export.")
            contract = goal.get("adaptive_contract")
            if contract is None:
                continue
            if doc is None or not doc.get("binding"):
                raise ValueError("An adaptive output has no source-bound target evidence.")
            binding = doc["binding"]
            if contract.get("kind") == "format_section_title" and not goal.get("plan_id") and binding.get("choice") == "user" and not any(
                    d.get("kind") == "target_binding" and d.get("binding") == binding
                    and doc["id"] in d["document_ids"] for d in project["decisions"]):
                raise ValueError("The ambiguous adaptive target has no recorded user selection.")
            saved_binding = result.key.get("adaptive_bindings", {}).get(doc["source"]) or result.key.get("adaptive_binding")
            if result.key.get("adaptive_contract") != contract or saved_binding != binding:
                raise ValueError("The adaptive goal or target changed since this candidate was verified.")
            verdict = verify_goal(f.source, f.output, contract, binding)
            doc["adaptive_verification"] = verdict
            if not verdict["passed"]:
                raise ValueError("Independent adaptive goal verification failed: " + str(verdict.get("errors")))


def create_campaign(inputs: Sequence[str], out_dir: Path, request: str = "", *, recursive=True) -> Campaign:
    out_dir = Path(out_dir).resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError("Choose a new empty campaign workspace.")
    inventory = inventory_inputs(inputs, recursive=recursive, exclude=[out_dir])
    if not inventory["documents"]:
        raise ValueError("No input .docx documents were found outside result folders.")
    if str(out_dir) in inventory["roots"]:
        raise ValueError("The campaign workspace cannot be an input root.")
    for project in inventory["projects"].values():
        validate_context(project["context"])
        project.update(goal={"request": request, "spec": None, "chain": None, "template": None,
                             "approved_words": [], "adaptive_contract": None} if request.strip() else {},
                       decisions=[], runs=[], questions=[], status="PENDING" if request.strip() else "NEEDS_GOAL")
    state = {"schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex, "revision": 0,
             "created_at": _now(), "request": request, **inventory, "exports": []}
    out_dir.mkdir(parents=True, exist_ok=True)
    campaign = Campaign(out_dir, state)
    campaign._save("created", {"inputs": list(map(str, inputs)), "recursive": recursive})
    return campaign


def load_campaign(out_dir: Path) -> Campaign:
    out_dir = Path(out_dir).resolve()
    state = json.loads((out_dir / STATE_FILE).read_text(encoding="utf-8"))
    if not isinstance(state, dict) or state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported campaign schema version.")
    if (not isinstance(state.get("projects"), dict) or not isinstance(state.get("documents"), dict)
            or not isinstance(state.get("revision"), int)):
        raise ValueError("Malformed campaign state.")
    seen = set()
    for pid, project in state["projects"].items():
        validate_context(project["context"])
        for doc_id in project["document_ids"]:
            if doc_id in seen or doc_id not in state["documents"] or state["documents"][doc_id]["project_id"] != pid:
                raise ValueError("Conflicting campaign document identity or project membership.")
            seen.add(doc_id)
    if seen != set(state["documents"]):
        raise ValueError("A campaign document has no project assignment.")
    roots = [Path(p).resolve() for p in state["roots"]]
    paths = []
    for doc in state["documents"].values():
        path = Path(doc["source"]).resolve()
        if out_dir == path or out_dir in path.parents or not any(root in path.parents for root in roots):
            raise ValueError("A campaign source lies outside its input roots or inside its results.")
        if path in paths:
            raise ValueError("Duplicate campaign source identity.")
        paths.append(path)
    return Campaign(out_dir, state)
