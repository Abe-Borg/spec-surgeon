"""Continue an ordinary conversation through outcome planning and verified recovery.

Drafts are reviewable choices, never execution authority. Approval binds exactly
the displayed contract and original files; subsequent trials resolve fresh targets.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _add_usage(c, usage):
    from .llm import TOKEN_KEYS
    c.usage.calls += usage.get("calls", 0)
    c.usage.fallbacks += usage.get("fallbacks", 0)
    c.usage.served.extend(usage.get("served_models", []))
    for model, counts in usage.get("by_model", {}).items():
        row = c.usage.by_model.setdefault(model, {key: 0 for key in TOKEN_KEYS})
        for key, value in counts.items():
            row[key] = row.get(key, 0) + value


def options_for(conversation, *, render=False):
    from .recovery import RecoveryOptions
    return RecoveryOptions(model=conversation.model, max_calls=conversation.max_calls,
        max_attempts=conversation.max_attempts, effort=conversation.effort,
        max_output_tokens=conversation.max_output_tokens, max_context_tokens=conversation.max_context_tokens,
        analysis_limits=conversation.batch.analysis_limits, render=render)


def propose(conversation):
    from . import campaign_planner
    from .conversation import Choice, Turn, clean_wording
    from .goals import approved_goal_words
    from .inventory import digest

    c = conversation
    c.recovery_active = True
    paths = list(c.files) + ([c.template] if c.template else [])
    # A draft based on stale inspection must not be presented as current.
    for path in paths:
        c.batch.check_source_pin(path)
    pins = {str(p.resolve()): digest(p) for p in paths}
    documents = [{"id": str(i), "source": str(p.resolve()), "source_sha256": pins[str(p.resolve())],
                  "status": "UNRESOLVED"} for i, p in enumerate(c.files)]
    project = {"label": "this conversation", "context": {"request": c.request,
        "prior_failures": c.prior_failures, "built_in_refusal": c.unsupported,
        "previous_approved_goal": getattr(c, "approved_recovery_contract", None)},
        "goal": {"template": str(c.template.resolve())} if c.template else {},
        "decisions": [{"text": s, "document_ids": [d["id"] for d in documents]} for s in c.sources]}
    if c.work_dir:
        project["planning_artifact_dir"] = str(c.work_dir / "outcome-planning")
    draft, c.recovery_planning = campaign_planner.propose(project, documents, c.request, options_for(c),
        client=c._client_now(), log=c.log, prior=c.recovery_planning)
    budget = c.recovery_planning.get("budget", {})
    c.recovery_budgets.append(copy.deepcopy(budget))
    c.calls += budget.get("calls", 0)
    _add_usage(c, budget.get("usage", {}))
    if draft["status"] != "PROPOSED":
        return Turn("questions" if draft["status"] == "NEEDS_INPUT" else "failed",
            message=draft["rationale"] or "I need one detail to continue.",
            questions=[q["text"] for q in draft["questions"]])
    contract = copy.deepcopy(draft["contract"])
    contract["request"] = c.request
    snapshot = {"contract": contract, "pins": pins,
                "files": [str(p.resolve()) for p in c.files],
                "template": str(c.template.resolve()) if c.template else None}
    words = approved_goal_words(contract)
    try:
        for word in words:
            clean_wording(word)
    except ValueError as exc:
        return Turn("questions", message=f"The proposed wording cannot be offered for approval: {exc}",
                    questions=["Provide the exact visible replacement or insertion wording."])
    # The entire contract is shown, including target scopes, counts and wording;
    # a model-written summary alone cannot approve hidden effects.
    detail = json.dumps(contract, ensure_ascii=False, indent=2)
    choice = Choice(c._next_choice_id(), "Use these outcomes and continue", "recovery_plan",
        detail=detail, words=words, snapshot=snapshot, item_hash=fingerprint(snapshot))
    return Turn("questions", message="I can continue by checking the requested outcomes directly. "
        "Review the scope and wording below; I will try and verify methods on copies.\n\n"
        + draft["rationale"] + "\n\n" + detail
        + ("\n\n" + "\n".join(draft["risks"]) if draft["risks"] else ""), choices=[choice])


def approve(conversation, choice_id):
    from .goals import approved_goal_words, validate_contract
    from .inventory import digest
    c = conversation
    choice = c.choice(choice_id)
    if choice.kind != "recovery_plan" or fingerprint(choice.snapshot) != choice.item_hash:
        raise ValueError("The proposed outcomes changed; review a new plan before continuing.")
    snapshot = copy.deepcopy(choice.snapshot)
    validate_contract(snapshot["contract"])
    if approved_goal_words(snapshot["contract"]) != choice.words:
        raise ValueError("The displayed wording changed; review a new plan.")
    for name, pin in snapshot["pins"].items():
        if digest(Path(name)) != pin:
            raise ValueError("A source or template changed since planning; start again with the current files.")
    c.take_app_choice(choice_id)
    c.sources.extend(choice.words)
    c.approved_recovery_hash = fingerprint(snapshot)
    c.approved_recovery_contract = copy.deepcopy(snapshot["contract"])
    c.turn = None  # consume this exact choice
    return snapshot


def execute(conversation, snapshot, out_dir, *, render=False, options=None):
    from .inventory import digest
    from .recovery import prepare_recovery, resume_recovery
    if fingerprint(snapshot) != getattr(conversation, "approved_recovery_hash", None):
        raise ValueError("Recovery requires the exact outcomes reviewed in this conversation.")
    if ([str(p.resolve()) for p in conversation.files] != snapshot["files"]
            or (str(conversation.template.resolve()) if conversation.template else None) != snapshot["template"]):
        raise ValueError("The approved source scope or template role changed before recovery.")
    for name, pin in snapshot["pins"].items():
        if digest(Path(name)) != pin:
            raise ValueError("An approved input changed before recovery.")
    selected_options = options or options_for(conversation, render=render)
    result = prepare_recovery(conversation.files, conversation.request, out_dir,
        template=conversation.template, prior_failures=conversation.prior_failures,
        approved_words=list(conversation.sources), adaptive_contract=copy.deepcopy(snapshot["contract"]),
        options=selected_options, log=conversation.log)
    if any(f.status == "UNRESOLVED" for f in result.files):
        result = resume_recovery(result.out_dir, options=selected_options,
            client=conversation._client_now(), log=conversation.log)
    return result


def result_turn(c, result):
    """Expose source-bound single-target choices in the same conversation."""
    from .conversation import Choice, Turn
    choices = []
    contract = result.key.get("adaptive_contract", {})
    for q in result.question_records:
        if q.get("status") != "open" or q.get("kind") != "goal_binding":
            continue
        resolution = q.get("resolution", {})
        effects = resolution.get("effects", {})
        if not effects:  # legacy single-title goal
            effects = {None: resolution}
        for ident, resolved in effects.items():
            effect = next((e for e in contract.get("effects", []) if e["id"] == ident), {})
            if resolved.get("status") != "needs_input" or effect.get("target", {}).get("cardinality", 1) != 1:
                continue
            for target in resolved.get("candidates", []):
                label = (f"{Path(q['files'][0]).name}: paragraph {target['paragraph_index'] + 1} "
                         f"in {target.get('scope', 'document')} — {target['text']}")
                answer = {"question_id": q["id"], "answer": label, "candidate_ids": [target["candidate_id"]]}
                if ident is not None:
                    answer["effect_id"] = ident
                snapshot = {"answer": answer, "run_dir": str(result.out_dir), "pins": result.input_pins}
                choices.append(Choice(c._next_choice_id(), label, "recovery_target",
                    snapshot=copy.deepcopy(snapshot), item_hash=fingerprint(snapshot)))
            if choices:
                break  # resolve one effect, then offer the remaining questions afresh
        if choices:
            break
    if result.questions or choices:
        return c._finish(Turn("questions", message=result.to_text(), choices=choices,
                              questions=list(result.questions)))
    c.turn = None
    c.note(result.to_text())
    return None


def resume_choice(c, choice_id, *, options=None):
    from .inventory import digest
    from .recovery import resume_recovery
    choice = c.choice(choice_id)
    if choice.kind != "recovery_target" or fingerprint(choice.snapshot) != choice.item_hash:
        raise ValueError("The target choice changed; review the current question.")
    snapshot = copy.deepcopy(choice.snapshot)
    for name, pin in snapshot["pins"].items():
        if digest(Path(name)) != pin:
            raise ValueError("An input changed since the target question was shown.")
    c.take_app_choice(choice_id)
    c.turn = None
    return resume_recovery(Path(snapshot["run_dir"]), request=snapshot["answer"], options=options,
        client=c._client_now(), log=c.log)


def after_compile(request, files, template, result, *, answers=(), client=None, work_dir=None,
                  model=None, log=None, **limits):
    """Preserve a noninteractive compiler's refusal as evidence and continue read-only planning."""
    from .conversation import Conversation, Entry
    c = Conversation(request, files, template=template, client=client, answers=answers,
        work_dir=work_dir, model=model, log=log, continuous_recovery=True, **limits)
    c.started = c.recovery_active = True
    # CompileResult retains aggregate tokens, including fallback attempts. Its
    # transcript does not retain every attempt's usage: never reconstruct a
    # cheaper ledger from only the final response. Mixed serving-model prices
    # are unknown at this compatibility boundary.
    replies = [e for e in result.transcript if e.get("role") == "assistant"]
    models = result.served_models
    fallbacks = sum(bool(e.get("fell_back")) for e in replies)
    model = models[0] if len(models) == 1 and not fallbacks else "unattributed compile usage"
    _add_usage(c, {"calls": len(replies), "fallbacks": fallbacks,
                  "served_models": models, "by_model": {model: result.usage}})
    c.calls = max((e.get("turn", 0) for e in result.transcript), default=0)
    c.exchange = copy.deepcopy(result.transcript)
    c.seconds = result.seconds
    c.unsupported = copy.deepcopy(result.spec)
    c.entries.append(Entry("user", request))
    c._advance()
    return c
