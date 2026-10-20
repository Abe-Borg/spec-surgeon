"""Command-line access to persistent, project-scoped batch workspaces."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def add_resource_arguments(parser):
    from .resources import DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_CALLS, DEFAULT_MAX_COST_USD
    parser.set_defaults(_saved_resource_policy=True)
    parser.add_argument("--budget", type=float, default=None,
                        help=f"estimated model-spending allowance in USD (default {DEFAULT_MAX_COST_USD:g})")
    parser.add_argument("--max-attempts", type=int, default=None,
                        help=f"candidate allowance per problem group (default {DEFAULT_MAX_ATTEMPTS})")
    parser.add_argument("--max-calls", type=int, default=None,
                        help=f"model-call allowance per investigation (default {DEFAULT_MAX_CALLS})")
    parser.add_argument("--max-output-tokens", type=int, default=None,
                        help="maximum output tokens per model call")
    parser.add_argument("--max-total-tokens", type=int, default=None,
                        help="estimated total input/output token allowance")
    parser.add_argument("--max-context-tokens", type=int, default=None,
                        help="start a durable new working context before this estimated size")
    parser.add_argument("--model", default=None)
    parser.add_argument("--effort", choices=["low", "medium", "high", "max"], default=None)
    generated = parser.add_mutually_exclusive_group()
    generated.add_argument("--allow-codegen", dest="allow_codegen", action="store_true", default=None,
                           help="allow sandboxed custom methods within independent verification coverage")
    generated.add_argument("--no-codegen", dest="allow_codegen", action="store_false")
    rendering = parser.add_mutually_exclusive_group()
    rendering.add_argument("--no-render", dest="render", action="store_false", default=None)
    rendering.add_argument("--render", dest="render", action="store_true")
    parser.add_argument("--renderer", choices=["auto", "word", "libreoffice", "none"], default=None)
    memory = parser.add_mutually_exclusive_group()
    memory.add_argument("--no-memory", dest="use_memory", action="store_false", default=None,
                        help="do not consult or record local repair cases")
    memory.add_argument("--memory", dest="use_memory", action="store_true")


def recovery_options(args, saved=None):
    """Explicit flags override saved settings; a fresh investigation gets ample defaults."""
    from .recovery import RecoveryOptions
    from .resources import (DEFAULT_EFFORT, DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_CALLS,
                            DEFAULT_MAX_COST_USD, DEFAULT_MAX_OUTPUT_TOKENS,
                            DEFAULT_MAX_TOTAL_TOKENS, DEFAULT_CONTEXT_TOKENS)
    values = dict(max_attempts=DEFAULT_MAX_ATTEMPTS, max_calls=DEFAULT_MAX_CALLS,
                  max_cost_usd=DEFAULT_MAX_COST_USD, model=None, effort=DEFAULT_EFFORT,
                  max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=DEFAULT_MAX_TOTAL_TOKENS,
                  max_context_tokens=DEFAULT_CONTEXT_TOKENS,
                  render=True, renderer="auto", allow_codegen=None, use_memory=True)
    values.update(saved or {})
    for name in values:
        value = getattr(args, "budget" if name == "max_cost_usd" else name, None)
        if value is not None:
            values[name] = value
    return RecoveryOptions.from_snapshot(values)


def _chain(value):
    if value.startswith("@"):
        from .chain import load_chain_file
        return load_chain_file(Path(value[1:]))
    return _object(value)


def _object(value):
    """Read an explicit JSON object; no arbitrary expression evaluation."""
    if value.startswith("@"):
        value = Path(value[1:]).read_text(encoding="utf-8")
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError("expected a JSON object or @file.json")
    return result


def add_workspace_parser(sub):
    parser = sub.add_parser("workspace", help="create, resume, and review a persistent multi-project batch")
    actions = parser.add_subparsers(dest="workspace_action", required=True)
    create = actions.add_parser("create", help="inventory multiple folders recursively; no model calls")
    create.add_argument("inputs", nargs="+")
    create.add_argument("--out", required=True)
    create.add_argument("--request", default="", help="initial request for each project; can be changed per project")
    create.add_argument("--no-recursive", action="store_true")
    show = actions.add_parser("show", help="show projects, documents, questions, and retained progress")
    show.add_argument("workspace")
    show.add_argument("--json", action="store_true")
    assign = actions.add_parser("assign", help="assign specific documents to a project")
    assign.add_argument("workspace")
    assign.add_argument("--project", required=True)
    assign.add_argument("--documents", nargs="+", required=True, metavar="DOCUMENT_ID")
    context = actions.add_parser("context", help="set project-specific metadata")
    context.add_argument("workspace")
    context.add_argument("--project", required=True)
    context.add_argument("--context", required=True, help="JSON or @file.json")
    goal = actions.add_parser("goal", help="set the request and execution inputs for one project")
    goal.add_argument("workspace")
    goal.add_argument("--project", required=True)
    goal.add_argument("--request", required=True)
    methods = goal.add_mutually_exclusive_group()
    methods.add_argument("--spec", help="saved EditSpec file or JSON")
    methods.add_argument("--chain", help="chain JSON or @file.json")
    methods.add_argument("--adaptive", help="supported adaptive goal JSON or @file.json")
    goal.add_argument("--template", default=None)
    goal.add_argument("--answer", action="append", default=[], help="approved wording for this project only")
    plan = actions.add_parser("plan", help="investigate the request and propose an outcome plan for review")
    plan.add_argument("workspace")
    plan.add_argument("--project", default=None, help="plan one project; otherwise plan every project with a request")
    plan.add_argument("--request", default=None, help="propose a revised request while retaining the current approved work")
    plan.add_argument("--documents", nargs="+", default=None, metavar="DOCUMENT_ID",
                      help="limit the proposed plan to these documents")
    plan.add_argument("--template", default=None, help="template DOCX to inspect and pin in the proposed plan")
    add_resource_arguments(plan)
    approve = actions.add_parser("approve-plan", help="approve the exact reviewed plan; does not edit or export files")
    approve.add_argument("workspace")
    approve.add_argument("--project", required=True)
    approve.add_argument("--plan", required=True, metavar="PLAN_ID")
    refresh = actions.add_parser("refresh", help="reacquire changed or repaired sources and invalidate dependent results")
    refresh.add_argument("workspace")
    refresh.add_argument("--documents", nargs="+", default=None, metavar="DOCUMENT_ID",
                         help="refresh selected documents; otherwise inspect every workspace source")
    answer = actions.add_parser("answer", help="save an answer for one project's pending investigation")
    answer.add_argument("workspace")
    answer.add_argument("--project", required=True)
    answer.add_argument("--text", required=True)
    answer.add_argument("--question-id", default=None)
    answer.add_argument("--files", nargs="*", default=None, help="optional absolute source paths within the project")
    bind = actions.add_parser("bind", help="choose one of an ambiguous adaptive target's recorded candidates")
    bind.add_argument("workspace")
    bind.add_argument("--document", required=True, metavar="DOCUMENT_ID")
    choices = bind.add_mutually_exclusive_group(required=True)
    choices.add_argument("--candidate", metavar="CANDIDATE_ID")
    choices.add_argument("--candidates", nargs="+", metavar="CANDIDATE_ID",
                         help="select the exact approved cardinality for one effect")
    bind.add_argument("--effect", default=None, metavar="EFFECT_ID")
    run = actions.add_parser("run", help="investigate or resume; retain candidates for review")
    run.add_argument("workspace")
    add_resource_arguments(run)
    export = actions.add_parser("export", help="export reviewed verified copies; originals stay untouched")
    export.add_argument("workspace")
    export.add_argument("--out", required=True)
    export.add_argument("--yes", action="store_true", help="approve exporting ready copies")
    export.add_argument("--ack-review", action="store_true", help="acknowledge inspected blocking visual findings")
    for action in (create, show, assign, context, goal, plan, approve, refresh, answer, bind, run, export):
        action.set_defaults(fn=cmd_workspace)


def cmd_workspace(args):
    from .campaign import create_campaign, load_campaign
    from .cli import _confirm, _interactive, _load_valid_spec
    try:
        action = args.workspace_action
        if action == "create":
            campaign = create_campaign(args.inputs, Path(args.out), args.request,
                                       recursive=not args.no_recursive)
        else:
            campaign = load_campaign(Path(args.workspace))
            if action == "assign":
                campaign.set_project(args.documents, args.project)
            elif action == "context":
                campaign.set_project_context(args.project, _object(args.context))
            elif action == "goal":
                spec = _load_valid_spec(args.spec) if args.spec else None
                if args.spec and spec is None:
                    return 2
                campaign.set_goal(args.project, args.request, spec=spec,
                                  chain=_chain(args.chain) if args.chain else None,
                                  adaptive_contract=_object(args.adaptive) if args.adaptive else None,
                                  template=Path(args.template) if args.template else None,
                                  approved_words=args.answer)
            elif action == "answer":
                campaign.answer(args.project, args.text, question_id=args.question_id, files=args.files)
            elif action == "plan":
                saved = campaign.to_dict().get("resource_options")
                campaign.plan(args.project, args.request, doc_ids=args.documents, template=args.template,
                              options=recovery_options(args, saved),
                              log=lambda message: print(message, file=sys.stderr))
            elif action == "approve-plan":
                campaign.approve_plan(args.project, args.plan)
            elif action == "refresh":
                campaign.refresh(args.documents)
            elif action == "bind":
                campaign.bind(args.document, args.candidate, effect_id=args.effect, candidate_ids=args.candidates)
            elif action == "run":
                saved = campaign.to_dict().get("resource_options")
                campaign.run(options=recovery_options(args, saved), log=lambda message: print(message, file=sys.stderr))
            elif action == "export":
                approved = args.yes or (_interactive() and _confirm("Export the reviewed workspace copies? [y/N] "))
                if not approved:
                    print("Not exported. Ready candidates remain in the workspace for review.")
                    return 0
                destination = campaign.export(Path(args.out), acknowledge_review=args.ack_review)
                print(f"Workspace copies exported: {destination}")
                return 0
        if getattr(args, "json", False):
            print(json.dumps(campaign.to_dict(), indent=2, ensure_ascii=False, default=str))
        else:
            print(campaign.to_text())
        if action == "run":
            active = [p for p in campaign.projects.values()
                      if (p.get("goal") or p.get("document_goals")) and p.get("document_ids")]
            if any(p.get("status") not in ("READY", "EXPORTED") for p in active):
                return 2
        if action == "plan":
            workspace_request = campaign.to_dict().get("request")
            selected = [p for pid, p in campaign.projects.items()
                        if (args.project is None or pid == args.project) and p.get("document_ids")
                        and (args.documents is None or set(args.documents) & set(p["document_ids"]))
                        and (args.request or (p.get("planning_draft") or {}).get("request")
                             or (p.get("goal") or {}).get("request") or workspace_request)]
            if any(not p.get("proposed_plan") or p["proposed_plan"].get("status") != "PROPOSED" for p in selected):
                return 2
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"Workspace: {exc}", file=sys.stderr)
        return 2
