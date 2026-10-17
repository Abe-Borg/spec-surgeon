"""spec-surgeon command line.

    spec-surgeon jobs
    spec-surgeon fingerprint FILES...                       [--json]
    spec-surgeon plan JOB FILES... --params JSON|@file      (dry run: predicted diff list, no outputs)
    spec-surgeon run  JOB FILES... --params JSON|@file --out DIR [--in-place] [--workers N]
                                   [--renderer auto|word|libreoffice|none] [--no-render]
    spec-surgeon ops [--catalog]                             (list the operation library; --catalog: JSON for the compiler)
    spec-surgeon workflows list|show ID [--json]             (discover built-in workflows and required inputs)
    spec-surgeon workflows use ID FILES... --inputs JSON|@file [--out DIR] [--yes] [--no-render]
                                                             (keyless workflow → preview → approve → verified copies)
    spec-surgeon profile FILES... [--json]                   (per-structure-pattern document profiles, no model)
    spec-surgeon audit FILES... [--audit NAME]... [--identity JSON|@file] [--json] [--out REPORT.md|REPORT.json]
                                                             (read-only specification QA across the batch)
    spec-surgeon plan-spec SPEC.json FILES... [--json] [--per-file]   (dry run of an EditSpec: nothing written)
    spec-surgeon run-spec SPEC.json FILES... [--out DIR] [--in-place] [--workers N] [--renderer …] [--no-render]
                                                             (execute a hand-written EditSpec end to end)
    spec-surgeon verify SOURCE OUTPUT --plan plan.json       (re-run the suite standalone)
    spec-surgeon do "request" [FILES...] [--from-set-aside DIR] [--template T] [--answer A]... [--out DIR] [--yes]
                                         [--no-render] [--no-review] [--ack-review] [--in-place] [--allow-codegen]
                                                             (natural language: talk it through with Claude in a
                                                              terminal → preview (+ Claude's visual check) → approve → run)
    spec-surgeon retry OUT_DIR [--request "..."] [--template T] [--answer A]... [--out DIR] [--yes] [--no-render]
                               [--no-review] [--ack-review] [--in-place] [--allow-codegen]
                                                             (rinse & repeat: run next pass on set-aside files only)
    spec-surgeon library list|show NAME|delete NAME [--kind recipe|operation|chain|snippet] [--yes]
                                                             (your saved recipes, operations, chains and snippets)
    spec-surgeon library use NAME FILES... [--kind recipe|operation|chain|snippet] [--out DIR] [--yes] [--in-place]
                                           [--no-render] [--after REGEX | --before REGEX] [--untracked]
                                                             (one of them on these files — no API key needed:
                                                              preview → approve → verified run; a snippet goes after
                                                              (or before) the paragraph matching REGEX)
    spec-surgeon library add-snippet NAME (--text "…" | --from FILE --paragraph REGEX) [--style ID]
                                          [--formatting inherit|none] [--description "…"] [--replace]
                                                             (save a paragraph to insert later)
    spec-surgeon chain --stage SPEC.json|recipe:NAME|chain:NAME [--stage ...] FILES... [--name N] [--out DIR] [--yes]
                       [--in-place] [--no-render] [--no-review] [--ack-review] [--save-as NAME [--replace]]
                                                             (several specs one after another: each stage a full
                                                              verified run on the stage before it — preview of the
                                                              whole chain on samples → approve → run; no API key needed)
    spec-surgeon gui                                         (the window; also: spec-surgeon-gui)
    spec-surgeon advise OUT_DIR [--file NAME]                (Claude: per exception a recommendation or a re-compiled spec)
    spec-surgeon diagnose OUT_DIR [--file NAME] [--bundle ZIP] (why each file failed, where and how; a zip to send)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from . import __version__
from . import diagnostics as diag
from .batch import expand_inputs, load_set_aside, run_batch
from .fingerprint import fingerprint
from .jobs import REGISTRY, get_job
from .package import DocxPackage

# Running without an API key (``llm.credentials_found`` False): what needs Claude says so; everything else runs.
NEEDS_KEY_DO = ("Talking a request through with Claude needs an Anthropic API key: set ANTHROPIC_API_KEY (or sign in "
                "with `ant auth login`). Without one, use your library — spec-surgeon library use NAME FILES… — or a "
                "saved spec — spec-surgeon run-spec SPEC.json FILES…; both run without a key.")
NEEDS_KEY_ADVISE = ("Advice comes from Claude and needs an Anthropic API key: set ANTHROPIC_API_KEY (or sign in with "
                    "`ant auth login`). Without one, `spec-surgeon diagnose OUT_DIR` says why each file was set aside.")
NO_KEY_REVIEW = "no Anthropic API key — look at the before/after pages in the preview folder yourself"


def _params(arg: str | None) -> dict:
    if not arg:
        return {}
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    return json.loads(arg)


def cmd_jobs(args):
    for name, cls in REGISTRY.items():
        print(f"{name}  [{cls.mode}]  — {cls.description}")
        for p, spec in cls.params_schema.get("properties", {}).items():
            req = " (required)" if p in cls.params_schema.get("required", []) else ""
            print(f"    {p}{req}: {spec.get('description', spec.get('type', ''))}"
                  + (f"  options: {spec['enum']}" if "enum" in spec else "")
                  + (f"  default: {spec['default']!r}" if "default" in spec else ""))
    return 0


def cmd_ops(args):
    from .ops import OPS
    if getattr(args, "catalog", False):
        from .catalog import op_catalog
        print(json.dumps(op_catalog(), indent=2, ensure_ascii=False))
        return 0
    for name, cls in sorted(OPS.items()):
        print(f"{name}  [{cls.mode}]  — {cls.description}")
        sel = cls.select_schema.get("properties", {})
        if sel:
            print("    select: " + "; ".join(f"{k}: {v.get('description', v.get('type', ''))}" for k, v in sel.items()))
        elif cls.select_schema.get("description"):
            print(f"    select: {cls.select_schema['description']}")
        for p, spec in cls.params_schema.get("properties", {}).items():
            req = " (required)" if p in cls.params_schema.get("required", []) else ""
            print(f"    {p}{req}: {spec.get('description', spec.get('type', ''))}"
                  + (f"  options: {spec['enum']}" if "enum" in spec else "")
                  + (f"  default: {spec['default']!r}" if "default" in spec else ""))
        for check, how in cls.verification_profile.items():
            print(f"    {check}: {how}")
    return 0


def cmd_workflows(args):
    """Discover and execute built-in workflows through the existing chain approval path."""
    from .chain import chain_templates
    from .editspec import SpecError, is_template_file
    from .workflows import build_workflow, get_workflow, load_workflow_inputs, workflow_catalog

    try:
        if args.action == "list":
            catalog = workflow_catalog()
            if args.json:
                print(json.dumps(catalog, indent=2, ensure_ascii=False))
            else:
                for entry in catalog:
                    print(f"{entry['id']} — {entry['name']}\n  {entry['description']}")
                print("\nInspect inputs: spec-surgeon workflows show ID")
            return 0
        if not args.name:
            raise ValueError(f"workflows {args.action} requires a workflow ID; use workflows list")
        entry = get_workflow(args.name)
        if args.action == "show":
            if args.json:
                print(json.dumps(entry, indent=2, ensure_ascii=False))
            else:
                print(f"{entry['name']}\n{entry['description']}")
                for field in entry["inputs"]:
                    default = f" (default: {field['default']!r})" if "default" in field else ""
                    print(f"  {field['name']}: {field['description']}{default}")
                print(f"\nPreview: spec-surgeon workflows use {args.name} FILES... --inputs @inputs.json")
                print("Add --yes after reviewing the preview to run; --save-as NAME retains the configured chain.")
            return 0
        if not args.files:
            raise ValueError("workflows use requires DOCX files or folders")
        inputs, base_dir = load_workflow_inputs(args.inputs)
        chain = build_workflow(args.name, inputs, base_dir=base_dir)
        templates = chain_templates(chain)
        files = [f for f in expand_inputs(args.files) if not is_template_file(f, templates)]
        if not files:
            raise ValueError("no input DOCX files remain after excluding workflow templates")
        out_dir = Path(args.out) if args.out else Path.cwd() / "SpecSurgeon_out" / datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, SpecError) as exc:
        print(f"Workflow refused: {exc}", file=sys.stderr)
        return 2
    print(f"Workflow: {entry['name']} — {len(files)} file(s)")
    return _preview_and_run_chain(args, chain, files, out_dir)


def cmd_run_spec(args):
    from .editspec import SpecError, load_spec, validate_spec
    try:
        spec = load_spec(args.spec)
        validate_spec(spec)
    except SpecError as e:
        diag.event("cli.spec.refused", level="warning", code=e.code, reason=e.reason)
        print(f"EditSpec refused [{e.code}]: {e.reason}", file=sys.stderr)
        for q in e.details.get("questions", []):
            print(f"  ? {q}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        diag.event("cli.spec.unreadable", level="warning", **diag.error_fields(e))
        print(f"cannot read the EditSpec: {e}", file=sys.stderr)
        return 2
    print(f"EditSpec: {spec['request']!r} — {spec['summary']}")
    for a in spec.get("assumptions", []):
        print(f"  assumption: {a}")
    if getattr(args, "approval", None):
        from .editspec import is_template_file, spec_templates
        from .preview import approval_inputs, approval_token, explain_approval_change
        files = [f for f in expand_inputs(args.files) if not is_template_file(f, spec_templates(spec))]
        if approval_token(spec, files) != args.approval:
            diag.event("cli.approval.refused", level="warning", gate="approval_digest",
                       reason="preview_inputs_changed")
            print("approval token does not match: the spec or a file changed since the preview (or the file set "
                  "differs) — preview again", file=sys.stderr)
            beside = Path(args.spec).parent / "preview.json" if not str(args.spec).lstrip().startswith("{") else None
            try:
                before = json.loads(beside.read_text(encoding="utf-8")).get("approval_inputs") if beside else None
            except (OSError, ValueError, AttributeError):
                before = None
            if isinstance(before, dict):
                for w in explain_approval_change(before, approval_inputs(spec, files)):
                    print(f"  • {w}", file=sys.stderr)
            return 2
    args.job = "edit-spec"
    args.params = None
    return cmd_run(args, spec=spec)


def _load_valid_spec(arg):
    from .editspec import SpecError, load_spec, validate_spec
    try:
        spec = load_spec(arg)
        validate_spec(spec)
        return spec
    except SpecError as e:
        diag.event("cli.spec.refused", level="warning", code=e.code, reason=e.reason)
        print(f"EditSpec refused [{e.code}]: {e.reason}", file=sys.stderr)
        for q in e.details.get("questions", []):
            print(f"  ? {q}", file=sys.stderr)
    except (OSError, ValueError) as e:
        diag.event("cli.spec.unreadable", level="warning", **diag.error_fields(e))
        print(f"cannot read the EditSpec: {e}", file=sys.stderr)
    return None


def cmd_profile(args):
    from .profile import profile_batch
    files = expand_inputs(args.files)
    t0 = time.perf_counter()
    prof = profile_batch(files)
    if args.json:
        print(json.dumps(prof, indent=2, ensure_ascii=False))
        return 0
    print(f"{prof['files']} file(s), {len(prof['patterns'])} structure pattern(s) in {time.perf_counter() - t0:.2f}s")
    for pat in prof["patterns"]:
        p = pat["profile"]
        print(f"\n-- pattern {pat['pattern_id']}: {pat['count']} file(s), e.g. {pat['files'][0]}")
        for s in p["sections"]:
            shows = "; ".join(f"{k[:-1]} {t}: {v['part'].rsplit('/', 1)[-1]}" for k in ("headers", "footers")
                              for t, v in s[k].items())
            print(f"   section {s['index']}: text width {s['text_width_twips']} twips, titlePg {s['titlePg']}"
                  + (f", numbering {s['page_numbering']}" if s.get("page_numbering") else "") + (f" — {shows}" if shows else ""))
        for part, v in p["header_footer_parts"].items():
            print(f"   {part.rsplit('/', 1)[-1]}: {v['text']}" + (f"  slots {v['slots']}" if v.get("slots") else "")
                  + (f"  fields {v['fields']}" if v.get("fields") else ""))
        print("   styles: " + ", ".join(f"{s['id']}×{s['count']} ({s.get('font')}, {s.get('size_pt')}pt)"
                                     for s in p["styles_in_use"][:8]))
        if p["fonts"]["direct_in_runs"]:
            print(f"   direct fonts in runs: {p['fonts']['direct_in_runs']}")
    return 0


def cmd_audit(args):
    from .spec_qa import audit_batch, audit_markdown
    try:
        files = expand_inputs(args.files)
    except OSError as e:
        print(f"Audit input could not be read: {e}", file=sys.stderr)
        return 2
    if not files:
        print("No DOCX files found to audit.", file=sys.stderr)
        return 2
    try:
        identity = _params(args.identity)
        if not isinstance(identity, dict):
            raise ValueError("identity must be a JSON object")
        report = audit_batch(files, args.audit, identity)
    except (OSError, ValueError) as e:
        print(f"Audit refused: {e}", file=sys.stderr)
        return 2
    as_json = args.json or (args.out and Path(args.out).suffix.lower() == ".json")
    rendered = json.dumps(report, indent=2, ensure_ascii=False) if as_json else audit_markdown(report)
    if args.out:
        target = Path(args.out).resolve()
        if target.suffix.lower() not in (".md", ".json") or target in {Path(f).resolve() for f in files}:
            print("Audit output must be a .md or .json report, separate from the source files.", file=sys.stderr)
            return 2
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        except OSError as e:
            print(f"Audit report could not be saved: {e}", file=sys.stderr)
            return 2
        print(f"Audit report: {target}")
    else:
        print(rendered)
    return 0 if report["complete"] else 1


def cmd_plan_spec(args):
    from .planner import plan_batch
    spec = _load_valid_spec(args.spec)
    if spec is None:
        return 2
    bp = plan_batch(args.files, spec)
    if args.json:
        print(json.dumps(bp.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(bp.to_text(per_file=args.per_file))
    return 0 if not bp.exceptions else 1


def cmd_fingerprint(args):
    from .diagnostics import exception_record
    files = expand_inputs(args.files)
    out = []
    t0 = time.perf_counter()
    for f in files:
        try:
            fp = fingerprint(DocxPackage.from_path(f))
        except Exception as e:  # noqa: BLE001 — one unreadable file is reported, the others still fingerprinted
            exc = exception_record(e, "fingerprint")
            print(f"{f.name}: {exc['code']} — {exc['reason']}", file=sys.stderr)
            continue
        out.append(fp)
        if not args.json:
            secs = fp["sections"]
            print(f"{f.name}: pattern {fp['pattern_id']}  sections {len(secs)}  "
                  f"trackRevisions {'on' if fp['trackRevisions'] else 'off'}  "
                  f"parts {len(fp['parts'])}  pending ins/del "
                  f"{sum(p['ins'] for p in fp['parts'].values())}/{sum(p['del'] for p in fp['parts'].values())}")
    if args.json:
        print(json.dumps(out, indent=2))
    else:
        from collections import Counter
        c = Counter(fp["pattern_id"] for fp in out)
        print(f"\n{len(files)} file(s), {len(c)} structure pattern(s) in {time.perf_counter() - t0:.2f}s: "
              + ", ".join(f"{k}×{v}" for k, v in c.most_common()))
    return 0


def cmd_plan(args):
    params = _params(args.params)
    job = get_job(args.job)
    files = expand_inputs(args.files)
    rc = 0
    from .diagnostics import exception_record
    for f in files:
        stage = "open"
        try:
            pkg = DocxPackage.from_path(f)
            stage = "plan"
            plan = job.plan(pkg, params)
        except Exception as e:  # noqa: BLE001 — a refusal, an unreadable file or the engine's own error: said per file
            rc = 2
            exc = exception_record(e, stage)
            print(f"\n== {f.name}: EXCEPTION [{exc['code']}] {exc['reason']}"
                  + (f" (at {exc['where']})" if exc.get("where") else ""))
            if exc.get("kind") == "internal-error":
                print(exc.get("traceback", ""), file=sys.stderr)
            continue
        print(f"\n== {f.name}: {plan.mode} job, parts {plan.intended_parts}, revision Δ ins {plan.rev_delta['ins']:+d} / del {plan.rev_delta['del']:+d}")
        for i, d in enumerate(plan.diffs, 1):
            b = "∅" if d.before is None else repr(d.before)
            a = "∅" if d.after is None else repr(d.after)
            print(f"   D{i}. {d.part} / block {d.block_index}: {b} → {a}")
            if d.accepted_view is not None:
                print(f"        accepted view: {d.accepted_view!r}")
        for n in plan.notes:
            print(f"   note: {n}")
        for fl in plan.flags:
            print(f"   FLAG: {fl}")
        if args.json:
            print(json.dumps(plan.to_dict(), indent=2))
    return rc


def cmd_run(args, spec=None):
    params = _params(args.params)
    out_dir = Path(args.out) if args.out else Path.cwd() / "SpecSurgeon_out" / datetime.now().strftime("%Y%m%d-%H%M%S")
    def progress(msg):
        print(msg, file=sys.stderr, flush=True)
    try:
        batch = run_batch(args.files, args.job, params, out_dir, workers=args.workers, renderer=args.renderer,
                          do_render=not args.no_render, in_place=args.in_place, progress=progress, spec=spec)
    except ValueError as e:          # refused before anything was written (an input inside the results folder)
        diag.event("cli.run.refused", level="warning", reason=str(e), out_dir=str(out_dir))
        print(f"refused: {e}", file=sys.stderr)
        return 2
    c = batch.counts()
    print(f"\n{len(batch.files)} file(s): PASS {c['PASS']}  FAIL {c['FAIL']}  EXCEPTION {c['EXCEPTION']}   "
          f"({batch.timings['total']:.1f}s total: " + ", ".join(f"{k} {v:.2f}s" for k, v in batch.timings.items() if k != "total")
          + f"; renderer {batch.renderer or 'none'})")
    for f in batch.files:
        line = f"  {f.status:<9} {f.source.name}"
        if f.exception:
            line += f"  [{f.exception.get('code')}] {f.exception.get('reason')}"
            if f.exception.get("kind") not in (None, "refusal"):
                line += f" (at {f.exception.get('stage')})"
        elif f.record and not f.record.passed:
            line += "  " + "; ".join(f"{ch.id} FAIL" for ch in f.record.checks if ch.status == "FAIL")
        elif f.delivered_to:
            line += f"  → {f.delivered_to}"
        print(line)
    print(f"\nReport: {out_dir / 'BATCH_REPORT.md'}")
    print(f"Log: {out_dir / 'logs' / 'run.log'} (one trace per file beside it)")
    if c["FAIL"] or c["EXCEPTION"]:
        print(f"Diagnostics — why each file failed, where and how: {out_dir / 'DIAGNOSTICS.md'}")
    if c["FAIL"]:
        print(f"Copies that FAILED verification were moved to {out_dir / 'failed'} (diagnosis only — not delivered)")
    if c["EXCEPTION"] or c["FAIL"]:
        print(f"Set-aside manifest: {out_dir / 'set_aside.json'} "
              f"(run `spec-surgeon retry \"{out_dir}\"` for the next pass on only these files)")
    return 0 if c["FAIL"] == 0 and c["EXCEPTION"] == 0 else 1


def cmd_do(args):
    """Natural language end to end: compile (Claude) → preview (dry plan + sample apply/verify/render) → approval →
    the verified batch. Nothing touches a source before approval; --in-place delivery is guarded as always."""
    from .compiler import compile_request
    from .diagnostics import write_json
    from .editspec import is_template_file
    out_dir = Path(args.out) if args.out else Path.cwd() / "SpecSurgeon_out" / datetime.now().strftime("%Y%m%d-%H%M%S")
    prior_failures = getattr(args, "prior_failures", None)
    input_files = list(getattr(args, "files", None) or [])
    if getattr(args, "from_set_aside", None):
        try:
            manifest = load_set_aside(args.from_set_aside)
        except (OSError, ValueError) as e:
            print(f"cannot load set_aside.json: {e}", file=sys.stderr)
            return 2
        if not manifest.get("files"):
            print("All files already passed in that run — 0 set-aside files remaining.")
            return 0
        input_files = [item["source"] for item in manifest["files"]]
        prior_failures = manifest["files"]
        if not args.template and manifest.get("template"):
            args.template = manifest["template"]
    if not input_files:
        print("no .docx files to work on (pass FILES... or --from-set-aside DIR)", file=sys.stderr)
        return 2
    templates = [Path(args.template).resolve()] if args.template else []
    files = [f for f in expand_inputs(input_files) if not is_template_file(f, templates)]
    if not files:
        print("no .docx files to work on", file=sys.stderr)
        return 2
    from . import llm
    if not llm.credentials_found():               # keyless: say what works without a key, before anything is written
        diag.event("cli.command.refused", level="warning", reason="model_authentication_missing", feature="conversation")
        print(NEEDS_KEY_DO, file=sys.stderr)
        return 2
    if getattr(args, "save_as", None):
        # before any compile: without a terminal and without --allow-codegen only a recipe can come of it
        could_be = "recipe" if not _interactive() and not getattr(args, "allow_codegen", False) else None
        why = _save_as_refusal(args.save_as, could_be, getattr(args, "replace", False))
        if why:
            print(f"--save-as: {why}", file=sys.stderr)
            return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    template = templates[0] if templates else None
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    conv = None
    if _interactive():
        outcome, value, conv = _chat(args, files, template, out_dir, prior_failures, log)
        if outcome == "stop":
            return value
        if outcome == "codegen":
            return _do_codegen(args, value, files, out_dir, brief=conv.brief())
        if outcome == "operation":                # one of your operations, passed its trials in the conversation
            return _run_saved_operation(args, value, files, out_dir, brief=conv.brief())
        spec = value                              # Claude's spec, or one of your recipes as saved
    else:
        print(f"compiling {args.request!r} over {len(files)} file(s) with {args.model} …", file=sys.stderr)
        res = compile_request(args.request, files, template=template, answers=args.answer or [],
                              model=args.model, log=log, prior_failures=prior_failures)
        if write_json(out_dir / "compile.json", {"request": args.request, "answers": args.answer or [],
                                                 **res.to_dict()}):
            print(f"compile record (the model's whole exchange): {out_dir / 'compile.json'}", file=sys.stderr)
        if res.status == "questions":
            print("The request needs answers before anything runs:")
            for q in res.questions:
                print(f"  ? {q}")
            print('Re-run with --answer "…" for each (answers are passed to the compiler verbatim), or run the '
                  'command in a terminal to talk it through with Claude.')
            return 2
        if res.status == "unsupported":
            print(f"No operation can do this: {res.spec.get('unsupported')}")
            if res.spec.get("proposal"):
                print(f"Proposal: {res.spec['proposal']}")
            if res.custom_transform == "outside_limits":
                print("A one-off custom transform cannot do this safely yet"
                      + (f" ({res.custom_transform_reason})" if res.custom_transform_reason else "")
                      + " — it needs a new built-in operation.")
                return 2
            if res.custom_transform != "possible":
                # only Claude's "possible" is an offer (rule 13): --allow-codegen accepts an offer, never makes one
                print("No custom transform is offered: Claude did not judge whether a one-off custom transform could "
                      "do this safely" + (" (--allow-codegen accepts only an offer Claude made)"
                                          if args.allow_codegen else "")
                      + ". Run the command in a terminal to talk it through with Claude, or run it again.")
                return 2
            if not args.allow_codegen:
                print("(No custom transform was written: run the command in a terminal and pick Claude's custom-"
                      "transform choice, or re-run with --allow-codegen to accept the offer — it is sandboxed, "
                      "verified on sample files first, and writes edited copies only.)")
                return 2
            return _do_codegen(args, res.spec, files, out_dir)
        if res.status != "ok":
            print("Compile failed: " + "; ".join(res.errors), file=sys.stderr)
            return 2
        spec = res.spec
    return _preview_and_run(args, spec, files, out_dir, brief=conv.brief() if conv is not None else None)


def _preview_and_run(args, spec, files, out_dir, brief=None):
    """The tail a spec takes, compiled by Claude (``cmd_do``) or one of your recipes (``library use``): --save-as
    checked, the preview, Claude's visual check (with a key, rendering on and no --no-review), --save-as saved, the y/N
    question (a blocking finding needs a look; --yes), the approval token checked again, then the verified batch."""
    from .preview import make_preview
    if getattr(args, "save_as", None):
        why = _save_as_refusal(args.save_as, "recipe", getattr(args, "replace", False))
        if why:
            print(f"--save-as: {why}", file=sys.stderr)
            return 2
    pv = make_preview(spec, [str(f) for f in files], out_dir / "preview",
                      renderer=args.renderer, do_render=not args.no_render)
    if not args.no_render and not getattr(args, "no_review", False):
        _visual_check(pv, spec, out_dir / "preview", args, brief=brief)
    print(pv.to_text())
    print(f"\npreview: {out_dir / 'preview'}")
    if getattr(args, "save_as", None):
        from .library import LibraryError, save_recipe
        try:
            item, notes = save_recipe(args.save_as, spec, replace=getattr(args, "replace", False))
        except LibraryError as e:
            print(f"--save-as: {e} — nothing was saved or run", file=sys.stderr)
            return 2
        print(f"Saved to your library: {item.label()} ({item.path})" + "".join(f"\nnote: {n}" for n in notes))
    blocking = pv.review.blocking() if pv.review is not None else []
    if blocking and not getattr(args, "ack_review", False):
        print(f"\nClaude's visual check flagged {len(blocking)} problem(s) it rates as blocking (see above).")
        if args.yes and not _interactive():
            diag.event("cli.approval.refused", level="warning", gate="visual_review",
                       blocking_count=len(blocking), reason="review_not_acknowledged")
            print("Not run: look at the pages in the preview folder; re-run with --ack-review to run anyway.")
            return 2
    approved = args.yes and (not blocking or getattr(args, "ack_review", False))
    if not approved and _interactive():
        question = (f"\nYou have looked at Claude's findings and want to run this on {len(pv.batch_plan.files)} "
                    f"file(s)? [y/N] " if blocking else f"\nRun this on {len(pv.batch_plan.files)} file(s)? [y/N] ")
        approved = bool(_confirm(question))
    diag.event("cli.approval.decision", approved=bool(approved), source="flag" if args.yes else "prompt",
               blocking_count=len(blocking), review_acknowledged=bool(getattr(args, "ack_review", False)))
    if not approved:
        print(f"\nNot run. To run exactly this:\n  spec-surgeon run-spec \"{out_dir / 'preview' / 'spec.json'}\" "
              + " ".join(f'"{f}"' for f in files) + f" --approval {pv.token}"
              + (" --in-place" if args.in_place else ""))
        if blocking:
            print("(running that command means you have looked at Claude's visual-check findings above)")
        return 0
    # what was approved is what was previewed: a file or the template edited since (while the preview, the visual
    # check or the question waited) refuses, exactly as run-spec --approval and the window's Run do (rule 12)
    from .preview import changed_since_preview
    changed = changed_since_preview(spec, [f.source for f in pv.batch_plan.files], pv.token, pv.token_inputs)
    if changed is not None:
        diag.event("cli.approval.refused", level="warning", gate="approval_digest",
                   reason="preview_inputs_changed", changes=changed)
        print("Not run: a file or the template changed since the preview — run the command again for a new preview."
              + "".join(f"\n  • {w}" for w in changed), file=sys.stderr)
        return 2
    args.out = str(out_dir / "run")
    args.files = [str(f) for f in files]          # exactly the previewed files (an unreferenced --template excluded)
    args.job = "edit-spec"
    args.params = None
    return cmd_run(args, spec=spec)


def _preview_and_run_chain(args, chain, files, out_dir):
    """The tail a chain takes (``chain`` and ``library use`` of a chain): --save-as checked (kind chain), the preview of
    the whole chain on the samples, Claude's visual check of the final pages (with a key, rendering on and no
    --no-review), --save-as saved, the y/N question, the approval token (every stage's spec and templates) checked
    again, then ``chain.run_chain``."""
    from .chain import chain_intent, chain_key, chain_name, run_chain
    from .preview import changed_since_preview, make_chain_preview
    if getattr(args, "save_as", None):
        why = _save_as_refusal(args.save_as, "chain", getattr(args, "replace", False))
        if why:
            print(f"--save-as: {why}", file=sys.stderr)
            return 2
    pv = make_chain_preview(chain, [str(f) for f in files], out_dir / "preview",
                            renderer=args.renderer, do_render=not args.no_render)
    if not args.no_render and not getattr(args, "no_review", False):
        from . import vision
        from .preview import write_preview_files
        pv.review = _review(chain_intent(chain), pv.samples, out_dir / "preview", args)
        vision.save_review(pv.review, out_dir / "preview")
        write_preview_files(pv)
    print(pv.to_text())
    print(f"\npreview: {out_dir / 'preview'}")
    if getattr(args, "save_as", None):
        from .library import LibraryError, save_chain
        try:
            item, notes = save_chain(args.save_as, chain, replace=getattr(args, "replace", False))
        except LibraryError as e:
            print(f"--save-as: {e} — nothing was saved or run", file=sys.stderr)
            return 2
        print(f"Saved to your library: {item.label()} ({item.path})" + "".join(f"\nnote: {n}" for n in notes))
    blocking = pv.review.blocking() if pv.review is not None else []
    if blocking and not getattr(args, "ack_review", False):
        print(f"\nClaude's visual check flagged {len(blocking)} problem(s) it rates as blocking (see above).")
        if args.yes and not _interactive():
            diag.event("cli.approval.refused", level="warning", gate="visual_review",
                       blocking_count=len(blocking), reason="review_not_acknowledged")
            print("Not run: look at the pages in the preview folder; re-run with --ack-review to run anyway.")
            return 2
    approved = args.yes and (not blocking or getattr(args, "ack_review", False))
    n_files = len(pv.batch_plan.files)
    if not approved and _interactive():
        question = (f"\nYou have looked at Claude's findings and want to run this chain on {n_files} file(s)? [y/N] "
                    if blocking else f"\nRun this chain ({len(chain['stages'])} stage(s)) on {n_files} file(s)? [y/N] ")
        approved = bool(_confirm(question))
    diag.event("cli.approval.decision", approved=bool(approved), source="flag" if args.yes else "prompt",
               blocking_count=len(blocking), review_acknowledged=bool(getattr(args, "ack_review", False)))
    if not approved:
        print(f"\nNot run. The chain is saved as {out_dir / 'preview' / 'chain.json'}; to run exactly this, run the "
              f"command again (or `spec-surgeon library use` once it is saved with --save-as).")
        return 0
    changed = changed_since_preview(chain_key(chain), [f.source for f in pv.batch_plan.files], pv.token, pv.token_inputs)
    if changed is not None:
        diag.event("cli.approval.refused", level="warning", gate="approval_digest",
                   reason="preview_inputs_changed", changes=changed)
        print("Not run: a file or a template changed since the preview — run the command again for a new preview."
              + "".join(f"\n  • {w}" for w in changed), file=sys.stderr)
        return 2
    out = out_dir / "run"

    def progress(msg):
        print(msg, file=sys.stderr, flush=True)
    try:
        result = run_chain([str(f) for f in files], chain, out, workers=args.workers, renderer=args.renderer,
                           do_render=not args.no_render, in_place=args.in_place, progress=progress)
    except ValueError as e:          # refused before anything was written (an input inside the results folder)
        print(f"refused: {e}", file=sys.stderr)
        return 2
    c = result.counts()
    n = len(result.stages)
    print(f"\nChain «{chain_name(chain)}» — {n} stage(s): " + " → ".join(st.name for st in result.stages))
    print(f"{len(result.files)} file(s): PASS {c['PASS']} (every stage)  FAIL {c['FAIL']}  EXCEPTION {c['EXCEPTION']}   "
          f"({result.timings.get('total', 0):.1f}s; renderer {result.renderer or 'none'})")
    for f in result.files:
        line = f"  {f.status:<9} {f.source.name}  [" + " · ".join(f.stage_status) + "]"
        if f.stopped_at:
            line += f"  stopped at stage {f.stopped_at}: [{f.code}] {f.reason}"
        elif f.delivered_to:
            line += f"  → {f.delivered_to}"
        print(line)
        for fl in f.flags:
            print(f"            FLAG: {fl}")
    print(f"\nReport: {out / 'CHAIN_REPORT.md'} (each stage: {out}/stage-NN/BATCH_REPORT.md)")
    print(f"Log: {out / 'logs' / 'chain.log'} (each stage's run.log and traces in its folder)")
    if c["FAIL"] or c["EXCEPTION"]:
        print(f"Diagnostics — which stage, why, where and how: {out / 'DIAGNOSTICS.md'}")
        print(f"Set-aside manifest: {out / 'set_aside.json'} (the files that did not get through every stage)")
    return 0 if c["FAIL"] == 0 and c["EXCEPTION"] == 0 else 1


def cmd_chain(args):
    """``spec-surgeon chain --stage A --stage B FILES…``: several specs one after another. A stage is an EditSpec file
    (or inline JSON), ``recipe:NAME`` (one of your recipes) or ``chain:NAME`` (a saved chain's stages, spliced in).
    No API key needed; Claude's visual check of the final pages runs when there is one (rendering on)."""
    from .chain import ChainError, chain_from_specs, chain_templates
    from .editspec import SpecError, is_template_file, load_spec, validate_spec
    from .library import LibraryError, load_chain, load_recipe
    stages = []
    assertions = []
    for ref in args.stage or []:
        try:
            if ref.startswith("recipe:"):
                name = ref[len("recipe:"):].strip()
                spec = load_spec(load_recipe(name))
                validate_spec(spec)
                stages.append((name, spec, name))
            elif ref.startswith("chain:"):
                name = ref[len("chain:"):].strip()
                saved = load_chain(name)
                assertions.extend(saved.get("assertions", []))
                for st in saved["stages"]:
                    stages.append((st.get("name") or "", st["spec"], st.get("recipe")))
            else:
                spec = load_spec(ref)
                validate_spec(spec)
                label = spec.get("request") if ref.lstrip().startswith("{") else Path(ref).stem
                stages.append((label, spec, None))
        except SpecError as e:
            print(f"stage {ref!r} refused [{e.code}]: {e.reason}", file=sys.stderr)
            return 2
        except (LibraryError, OSError, ValueError) as e:
            print(f"stage {ref!r}: {e}", file=sys.stderr)
            return 2
    if not stages:
        print("chain: give at least one --stage (an EditSpec .json, recipe:NAME or chain:NAME), in the order they run",
              file=sys.stderr)
        return 2
    try:
        chain = chain_from_specs(stages, name=getattr(args, "name", None) or "", assertions=assertions or None)
    except ChainError as e:
        print(f"chain refused [{e.code}]: {e.reason}", file=sys.stderr)
        return 2
    files = [f for f in expand_inputs(args.files) if not is_template_file(f, chain_templates(chain))]
    if not files:
        print("no .docx files to work on", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else Path.cwd() / "SpecSurgeon_out" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Chain of {len(chain['stages'])} stage(s) on {len(files)} file(s): "
          + " → ".join(st["name"] for st in chain["stages"]), file=sys.stderr)
    return _preview_and_run_chain(args, chain, files, out_dir)


def _visual_check(pv, spec, folder, args, brief=None) -> None:
    """Claude's visual check of the preview samples (advisory; never raises; can only add a reason to stop). ``brief``:
    the terminal conversation the spec came from (``Conversation.brief()``) — what the user narrowed or added there is
    what the AFTER pages are judged against, as in the window."""
    from . import vision
    from .preview import write_preview_files
    intent = "\n".join([f"REQUEST: {spec.get('request') or getattr(args, 'request', '')}",
                        f"WHAT THE SPEC DOES: {spec.get('summary') or ''}"]
                       + [f"ASSUMPTION: {a}" for a in spec.get("assumptions") or []]
                       + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
    pv.review = _review(intent, pv.samples, folder, args)
    vision.save_review(pv.review, folder)
    write_preview_files(pv)


def _review(intent, samples, folder, args):
    """Claude's visual check of the sample pages — or, without credentials (``llm.credentials_found``), a skipped
    review that says why: nothing is prepared or sent, and nothing is blocked (it can stop a run, never pass one)."""
    from . import compiler, llm, vision
    if not llm.credentials_found():
        diag.event("cli.review.skipped", reason="model_authentication_missing")
        print(f"Claude's visual check skipped: {NO_KEY_REVIEW}", file=sys.stderr)
        return vision.VisualReview(skipped=NO_KEY_REVIEW)
    return vision.review_samples(intent, samples, folder, compiler._client,
                                 model=getattr(args, "model", None) or compiler.DEFAULT_MODEL,
                                 log=lambda m: print(m, file=sys.stderr))


def _interactive() -> bool:
    """A person is at the terminal (tests replace this with False, so no prompt can block a test run)."""
    return bool(sys.stdin is not None and sys.stdin.isatty() and sys.stdout is not None and sys.stdout.isatty())


def _ask(question: str):
    """One line typed at the terminal, or None when the input ended (Ctrl-D; Ctrl-Z Enter on Windows) or was
    interrupted (Ctrl-C) — a stop, never a traceback."""
    try:
        return input(question)
    except (EOFError, KeyboardInterrupt):
        print()                                   # the next line starts on its own line, not after the prompt
        return None


def _confirm(question: str):
    """A y/N question: True for y/yes, False for anything else, None when the input ended or was interrupted (``_ask``)
    — falsy, so every y/N prompt reads it as no; the wording approval reads it as a stop."""
    answer = _ask(question)
    if answer is None:
        return None
    return answer.strip().lower() in ("y", "yes")


def _save_as_refusal(name: str, kind, replace: bool) -> str:
    """Why ``--save-as NAME`` cannot be saved as a ``kind`` ('recipe' | 'operation'; None while the kind is not known
    yet) — '' when it can. --replace replaces an item of the same kind only: a name an item of the other kind holds is
    refused even with it (``library._check_free`` refuses the same), so a recipe and an operation never share a name."""
    from .library import LibraryError, list_items, slug
    try:
        s = slug(name)
        taken = [i for i in list_items() if i.slug == s]
    except LibraryError as e:
        return str(e)
    if kind is not None:
        other = [i for i in taken if i.kind != kind]
        if other:
            o = other[0]
            return (f"your library already has {o.label()!r} — --replace replaces only an item of the same kind, "
                    f"so {'an' if kind == 'operation' else 'a'} {kind} cannot take that name; choose another name, "
                    f"or delete that one first "
                    f"(spec-surgeon library delete \"{o.name}\" --kind {o.kind})")
    elif len({i.kind for i in taken}) > 1:        # held by both kinds: whatever comes of it, the other one stays
        return (f"your library already has {' and '.join(repr(i.label()) for i in taken)} — --replace replaces only "
                f"an item of the same kind; choose another name, or delete one of them first (spec-surgeon library "
                f"delete \"{taken[0].name}\" --kind {taken[0].kind})")
    if taken and not replace:
        return (f"your library already has {taken[0].label()!r} — choose another name or pass --replace (it replaces "
                f"an item of the same kind only)")
    return ""


def _print_turn(turn) -> None:
    if turn.understanding:
        print(f"Claude understood: {turn.understanding}")
    if turn.message:
        print(f"Claude: {turn.message}")
    for q in turn.questions:
        print(f"  ? {q}")
    for i, c in enumerate(turn.choices, 1):
        print(f"  [#{i}] {c.label}" + "".join(f"\n        exact words: «{w}»" for w in c.words))


def _chat(args, files, template, out_dir, prior_failures, log):
    """Talk the request through with Claude at the terminal. Returns ("spec", spec, conv), ("codegen", the unsupported
    spec, conv) when the user picks the custom transform, or ("stop", exit code, conv). OUT/conversation.json and
    OUT/compile.json are re-written after every turn (a record that cannot be written never stops the conversation),
    and saved before a defect propagates."""
    from .conversation import Conversation
    from .diagnostics import write_json
    print(f"talking {args.request!r} through with Claude ({args.model}) over {len(files)} file(s) …", file=sys.stderr)
    conv = Conversation(args.request, files, template=template, model=args.model, prior_failures=prior_failures,
                        answers=args.answer or [], work_dir=out_dir / "chat", log=log)

    def save(turn=None):
        try:
            conv.save(out_dir / "conversation.json")
        except OSError:
            pass
        if turn is not None:
            return write_json(out_dir / "compile.json", {"request": args.request, "answers": args.answer or [],
                                                         **conv.compile_result(turn).to_dict()})
        return None

    try:
        return _talk(conv, save, out_dir, files)
    except Exception:
        save()                                    # what was said before a defect stopped it, for the diagnosis
        raise


def _talk(conv, save, out_dir, files):
    """_chat's loop: one turn of Claude's, then the user's reply or choice, until a spec, a custom transform, one of
    your recipes ("recipe": its spec as saved, with this request) or operations ("operation": passed its trials here —
    one that fails returns to the prompt, the refusal noted to Claude) or stop."""
    from .conversation import LIBRARY_CHOICE_KINDS, LibraryChanged
    turn = conv.start()
    told = False
    while True:
        if save(turn) and not told:
            print(f"compile record (the model's whole exchange): {out_dir / 'compile.json'}; the conversation: "
                  f"{out_dir / 'conversation.json'}", file=sys.stderr)
            told = True
        if turn.status == "ok":
            return "spec", turn.spec, conv
        _print_turn(turn)
        print(f"(Claude so far: {conv.usage.line()})")
        line = (_ask("Your reply ('#n' picks a choice; an empty line stops): ") or "").strip()
        if not line:
            print("Stopped — nothing was run.")
            return "stop", 2, conv
        m = re.fullmatch(r"#(\d+)", line)
        if m is None:
            turn = conv.reply(line)
            continue
        i = int(m.group(1))
        if not 1 <= i <= len(turn.choices):
            print(f"There is no choice #{i}.")
            continue
        choice = turn.choices[i - 1]
        if choice.words:
            ok = _confirm("Approve these exact words for your documents: "
                          + "; ".join(f"«{w}»" for w in choice.words) + "? [y/N] ")
            if ok is None:                        # the input ended or Ctrl-C: a stop, like an empty reply
                print("Stopped — nothing was run.")
                return "stop", 2, conv
            if not ok:                            # a typed no: the words are not approved; the conversation goes on
                continue
        if choice.kind == "custom_transform":
            conv.take_app_choice(choice.id)
            save()
            return "codegen", conv.unsupported, conv
        if choice.kind in LIBRARY_CHOICE_KINDS:
            try:
                chosen, spec = conv.take_library_choice(choice.id)
            except LibraryChanged as e:           # the item changed since Claude offered it: nothing used
                print(f"Not used: {e}")
                save()
                continue
            if chosen.kind == "recipe":
                save()
                print(f"Using your recipe {chosen.name!r} as saved.")
                return "recipe", spec, conv
            picked = _try_saved_operation(conv, chosen.name, files, out_dir)
            save()
            if picked is None:                    # its trials failed: said, noted to Claude, the conversation goes on
                continue
            return "operation", picked, conv
        try:
            turn = conv.reply(choice_id=choice.id)
        except LibraryChanged as e:               # a snippet that changed since Claude offered it: nothing approved
            print(f"Not used: {e}")
            save()


def _try_saved_operation(conv, name, files, out_dir):
    """One of your operations, picked in the terminal conversation: its trials on the batch's samples (sandbox + the
    full suite) under OUT/library-trial, the approval token taken BEFORE them (CLAUDE.md rule 17, as the window's
    ``Session._prove``). A failing sample — or an operation that no longer loads — is printed and noted to Claude, and
    None returned: the conversation goes on. ``conv`` None: ``library use`` (no conversation, nothing to note). A
    passing one returns (g, key, token, inputs) for ``_run_saved_operation``."""
    from .codegen import pick_samples, trial
    from .library import LibraryError, load_operation
    from .preview import approval_inputs, approval_token, generated_key
    try:
        g = load_operation(name)
    except LibraryError as e:
        print(f"Not used: {e}")
        if conv is not None:
            conv.note(f"Your operation {name!r} could not be used: {e}")
        return None
    samples = pick_samples(files)
    print(f"Trying your operation {name!r} on {[s.name for s in samples]} …", file=sys.stderr)
    key = generated_key(g)
    token, inputs = approval_token(key, files), approval_inputs(key, files)
    n = 1
    while (out_dir / "library-trial" / f"try-{n}").exists():
        n += 1
    folder = out_dir / "library-trial" / f"try-{n}"
    trials = [trial(g.source, f, g.select, g.params, folder / f"{i:02d}") for i, f in enumerate(samples)]
    bad = [f"{t.source.name}: {'; '.join(t.problems[:2])}" for t in trials if not t.passed]
    if bad:
        why = f"your operation {name!r} does not pass on these files — " + " | ".join(bad)
        print(f"Not used: {why}")
        if conv is not None:
            conv.note(why)
        return None
    g.passed, g.trials = True, trials
    print(f"your operation {name!r} passed on the samples")
    return g, key, token, inputs


def _run_saved_operation(args, picked, files, out_dir, brief=None):
    """One of your operations that passed its trials in the terminal conversation: --save-as checked first (a copy
    under another name), its preview, then the shared tail of a passing generated op (``_approve_and_run_generated``):
    the visual check, the y/N question, the token re-check, the run — edited copies only."""
    g, key, token, inputs = picked
    if getattr(args, "save_as", None):
        why = _save_as_refusal(args.save_as, "operation", getattr(args, "replace", False))
        if why:
            print(f"--save-as: {why}", file=sys.stderr)
            return 2
    from .preview import preview_generated
    gp = preview_generated(g, out_dir / "library-trial" / "preview", renderer=args.renderer,
                           do_render=not args.no_render)
    intent = "\n".join([f"YOUR SAVED OPERATION ({g.name}) for: {g.request}", f"WHAT IT DOES: {g.proposal}"]
                       + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
    return _approve_and_run_generated(args, g, gp, files, out_dir, key, token, inputs, intent,
                                      "a file changed since your operation was tried and previewed")


def cmd_retry(args):
    """Rinse & repeat: load set_aside.json from a previous batch output directory and run the next pass on only
    the set-aside files, feeding their failure reasons to the compiler."""
    try:
        manifest = load_set_aside(args.out_dir)
    except (OSError, ValueError) as e:
        print(f"cannot load set_aside.json from {args.out_dir}: {e}", file=sys.stderr)
        return 2
    if not manifest.get("files"):
        print("All files already passed in that run — 0 set-aside files remaining.")
        return 0
    req = getattr(args, "request", None) or manifest.get("request")
    if not req:
        print("set_aside.json has no request; pass --request \"...\"", file=sys.stderr)
        return 2
    args.request = req
    args.from_set_aside = args.out_dir
    args.files = []
    return cmd_do(args)


def cmd_resolve(args):
    """Investigate or resume copies; exporting is a separate approval."""
    from .recovery import deliver_recovery, recover_from_run, load_recovery
    from .workspace_cli import recovery_options
    out = Path(args.out) if args.out else Path("SpecSurgeon_out") / datetime.now().strftime("%Y%m%d-%H%M%S-%f-recovery")
    try:
        saved = load_recovery(Path(args.run_dir)).options_snapshot if (Path(args.run_dir) / "recovery.json").is_file() else None
        options = recovery_options(args, saved)
        result = recover_from_run(Path(args.run_dir), out, request=args.request, options=options,
                                  log=lambda m: print(m, file=sys.stderr))
        print(result.to_text())
        if result.review is not None:
            print(result.review.to_text())
        if not result.ready:
            return 2
        blocking = bool(result.review and result.review.blocking())
        acknowledged = args.ack_review
        if blocking and not acknowledged:
            if _interactive():
                acknowledged = bool(_confirm("\nYou have inspected the blocking visual findings and want to allow export? [y/N] "))
            if not acknowledged:
                print("Not exported: inspect the blocking findings; --yes does not acknowledge them. "
                      "Use --ack-review only after inspecting them.", file=sys.stderr)
                return 2
        approved = args.yes or (_interactive() and _confirm("\nExport the verified recovery copies? [y/N] "))
        if not approved:
            print(f"Not exported. Investigation and review: {result.out_dir}")
            return 0
        destination = deliver_recovery(result, acknowledge_review=acknowledged)
        print(f"Recovery copies exported: {destination}. Original files were not changed.")
        return 0 if len(result.ready) == len(result.files) else 2
    except (ValueError, OSError) as e:
        print(f"Recovery stopped: {e}", file=sys.stderr)
        return 2


def cmd_memory(args):
    """Local repair evidence and user feedback; accepting a case never bypasses verification."""
    from .repair_memory import CaseStore
    store = CaseStore()
    try:
        if args.action == "list":
            print(json.dumps(store.list_cases(), ensure_ascii=False, indent=2, default=str))
            return 0
        if not args.case_id:
            print(f"memory {args.action} needs a case ID", file=sys.stderr)
            return 2
        if store.get(args.case_id) is None:
            raise ValueError(f"no repair case {args.case_id!r}")
        if args.action == "show":
            case = store.get(args.case_id)
            print(json.dumps(case, ensure_ascii=False, indent=2, default=str))
        elif args.action == "forget":
            store.forget(args.case_id)
            print(f"Forgot repair case {args.case_id}.")
        else:
            store.feedback(args.case_id, args.action == "accept", note=args.note or "")
            print(f"Recorded {args.action} feedback for {args.case_id}; future reuse still requires verification.")
        return 0
    except (ValueError, OSError) as e:
        print(f"Repair memory: {e}", file=sys.stderr)
        return 2


def _do_codegen(args, spec, files, out_dir, brief=None):
    """Tier B: generate an op for an unsupported request, prove it on one sample per pattern, run it after
    approval. Outputs only — never in place (a generated op becomes permanent only after human review)."""
    from .codegen import generate_op, pick_samples, run_generated
    from .preview import approval_inputs, approval_token, changed_since_preview, generated_key
    if getattr(args, "save_as", None):
        why = _save_as_refusal(args.save_as, "operation", getattr(args, "replace", False))
        if why:
            print(f"--save-as: {why}", file=sys.stderr)
            return 2
    samples = pick_samples(files)
    # the files as they are BEFORE the transform is written and tried on them (several calls to Claude): the approval
    # taken after it must be of these very files, or the preview would pair a source edited meanwhile with a trial
    # output made from its old content — refused below, once the preview is made, naming the file
    before_writing = approval_inputs({}, files)
    print(f"Tier B: generating a transform, tested on {[s.name for s in samples]} …", file=sys.stderr)
    g = generate_op(args.request, spec.get("proposal") or spec.get("unsupported") or "", samples, out_dir / "codegen",
                    model=args.model, log=lambda m: print(m, file=sys.stderr), brief=brief)
    for a in g.attempts:
        print(f"  attempt {a['attempt']}: " + ("PASS" if not a["problems"] else f"{len(a['problems'])} problem(s): "
                                                + "; ".join(a["problems"][:3])))
    if (out_dir / "codegen" / "history.json").is_file():
        print(f"Tier B history (every attempt, the model's exchange): {out_dir / 'codegen' / 'history.json'}",
              file=sys.stderr)
    if not g.passed:
        print("No generated transform passed verification: " + "; ".join(g.errors))
        return 2
    print(f"generated op {g.name!r} passed on the samples: {g.path} (select {g.select}, params {g.params})")
    # the approval is of this transform on the files as they were before it was written (as the window's, whose token is
    # taken before the trials): the token is taken here, its files compared with `before_writing` once the preview is
    # made, and the token re-checked before the run — a file edited while the transform was written, the preview, the
    # visual check or the question waited refuses
    key = generated_key(g)
    try:
        token = approval_token(key, files)
    except OSError:
        token = ""                                # a file vanished while the transform was written: Run refuses
    inputs = approval_inputs(key, files)
    from .preview import preview_generated
    gp = preview_generated(g, out_dir / "codegen" / "preview", renderer=args.renderer, do_render=not args.no_render)
    drift = _changed_while_writing(before_writing, inputs, approval_inputs({}, files))
    if drift:
        print("Not run: a file changed while the custom transform was being written and tested — its preview would "
              "show a result made from the old content. Run the command again."
              + "".join(f"\n  • {w}" for w in drift), file=sys.stderr)
        return 2
    intent = "\n".join([f"A ONE-OFF CUSTOM TRANSFORM ({g.name}) for: {args.request}",
                        f"WHAT IT SHOULD DO: {spec.get('proposal') or ''}"]
                       + ([f"THE CONVERSATION:\n{brief}"] if brief else []))
    return _approve_and_run_generated(args, g, gp, files, out_dir, key, token, inputs, intent,
                                      "a file changed since the custom transform was tested and previewed")


def _approve_and_run_generated(args, g, gp, files, out_dir, key, token, inputs, intent, changed_msg):
    """The tail a passing generated op shares, fresh (``_do_codegen``) or saved (``_run_saved_operation``): Claude's
    visual check of its preview ``gp``, the preview text, --save-as, the y/N question (a blocking finding needs a look),
    the approval token checked again right before the run, and ``run_generated`` — edited copies only."""
    from . import vision
    from .codegen import run_generated
    from .preview import changed_since_preview, write_preview_files
    if not args.no_render and not getattr(args, "no_review", False):
        gp.review = _review(intent, gp.samples, gp.out_dir, args)
        vision.save_review(gp.review, gp.out_dir)
        write_preview_files(gp)
    print(gp.to_text())
    print(f"\npreview: {gp.out_dir}")
    if getattr(args, "save_as", None):
        from .library import LibraryError, save_operation
        try:
            item = save_operation(args.save_as, g, replace=getattr(args, "replace", False))
        except LibraryError as e:
            print(f"--save-as: {e} — nothing was saved or run", file=sys.stderr)
            return 2
        print(f"Saved to your library: {item.label()} ({item.path})")
    if args.in_place:
        print("note: generated transforms are never delivered in place; outputs go to the output folder")
    blocking = gp.review.blocking() if gp.review is not None else []
    if blocking and not getattr(args, "ack_review", False) and args.yes and not _interactive():
        diag.event("cli.approval.refused", level="warning", gate="visual_review",
                   blocking_count=len(blocking), reason="review_not_acknowledged")
        print(f"\nNot run: Claude's visual check flagged {len(blocking)} blocking problem(s); look at the pages, then "
              f"re-run with --ack-review to run anyway.")
        return 2
    approved = args.yes and (not blocking or getattr(args, "ack_review", False))
    if not approved and _interactive():
        q = (f"\nYou have looked at Claude's findings and want to run it on {len(files)} file(s) (edited copies "
             f"only)? [y/N] " if blocking else f"\nRun it on {len(files)} file(s) (edited copies only)? [y/N] ")
        approved = bool(_confirm(q))
    diag.event("cli.approval.decision", approved=bool(approved), source="flag" if args.yes else "prompt",
               blocking_count=len(blocking), review_acknowledged=bool(getattr(args, "ack_review", False)))
    if not approved:
        print("Not run. Review the generated module and its preview before running it.")
        return 0
    changed = changed_since_preview(key, files, token, inputs)
    if changed is not None:
        diag.event("cli.approval.refused", level="warning", gate="approval_digest",
                   reason="preview_inputs_changed", changes=changed)
        print(f"Not run: {changed_msg} — run the command again."
              + "".join(f"\n  • {w}" for w in changed), file=sys.stderr)
        return 2
    results = run_generated(g, [str(f) for f in files], out_dir / "run", log=lambda m: print(m, file=sys.stderr))
    ok = sum(1 for r in results if r.passed)
    print(f"{ok} of {len(results)} file(s) PASS; outputs in {out_dir / 'run' / 'outputs'}")
    for r in results:
        if not r.passed:
            print(f"  FAIL {r.source.name}: " + "; ".join(r.problems[:2]))
    return 0 if ok == len(results) else 1


def _changed_while_writing(before: dict, *later: dict) -> list:
    """What changed in the files between ``before`` (``preview.approval_inputs`` taken before the custom transform was
    written) and each of ``later`` (the inputs its approval token was taken over; the files once its preview is made),
    one line per file — [] when every one matches. Only the files are compared (the key is not known before)."""
    from .preview import explain_approval_change
    for now in later:
        lines = explain_approval_change({"files": before.get("files") or {}}, {"files": now.get("files") or {}},
                                        since="the custom transform was started")
        if lines:
            return lines
    return []


def cmd_library(args):
    """Your library: list it, show one item, delete one, add a snippet (recipes, your own operations, chains and
    snippets; local to this computer)."""
    from .library import LibraryError, delete, find, library_dir, list_items, read_snippet
    if args.action == "list":
        items = list_items(args.kind)
        if not items:
            print("Your library is empty — save a previewed spec or a custom transform (window: Save to library…; "
                  "command line: do … --save-as NAME), or a paragraph (library add-snippet NAME --text …).")
        for i in items:
            print(f"{i.label():<52} saved {i.saved_at or '?'}" + (f"  — {i.description}" if i.description else ""))
        print(f"\n(library folder: {library_dir()})")
        return 0
    if not args.name:
        print(f"library {args.action}: which one? (spec-surgeon library list)", file=sys.stderr)
        return 2
    if args.action == "use":
        return _use_library_item(args)
    if args.action == "add-snippet":
        return _add_snippet(args)
    try:
        item = find(args.name, args.kind)
        if args.action == "show" and item.kind == "snippet":
            sn = read_snippet(item)
            print(f"{item.label()}\n  saved:   {item.saved_at}" + (f"  ({sn['source']})" if sn["source"] else "")
                  + f"\n  text:    «{sn['text']}»\n  style:   {sn['style'] or 'the anchor’s'}\n  formatting: "
                  f"{sn['formatting']}\n  path:    {item.path}" + (f"\n  about:   {item.description}"
                                                                     if item.description else ""))
            print(f"\nuse it:   spec-surgeon library use \"{item.name}\" FILES… --after REGEX   (no API key needed; "
                  f"after — or --before — the paragraph whose text matches REGEX, in every file that has it)")
            return 0
        if args.action == "show":
            print(f"{item.label()}\n  saved:   {item.saved_at}\n  request: {item.request}\n  path:    {item.path}")
            if item.description:
                print(f"  about:   {item.description}")
            print(f"\nuse it:   spec-surgeon library use \"{item.name}\" FILES…   (no API key needed)")
            if item.kind == "recipe":
                print(f"run it:   spec-surgeon run-spec \"{item.path}\" FILES…   (straight to the run, no preview)")
            elif item.kind == "chain":
                from .library import load_chain
                chain = load_chain(item.name)
                print("stages (run one after another, each a full verified run on the stage before it):")
                for i, st in enumerate(chain["stages"], 1):
                    print(f"  {i}. {st.get('name')}" + (f" (recipe {st['recipe']!r})" if st.get("recipe") else "")
                          + f" — {st['spec'].get('request')!r}")
                print(f"as stages: spec-surgeon chain --stage \"chain:{item.name}\" FILES…   (add --stage to extend it)")
            else:
                print("it is tried on the files' samples first, then previewed; the run writes edited copies only "
                      "(the window: Your library…)")
            return 0
        if not args.yes:
            if not _interactive():
                print(f"Pass --yes to delete {item.label()!r}.", file=sys.stderr)
                return 2
            if not _confirm(f"Delete {item.label()!r}? [y/N] "):
                return 2
        delete(item.name, item.kind)
        print(f"Deleted {item.label()}.")
        return 0
    except LibraryError as e:
        print(str(e), file=sys.stderr)
        return 2


def _add_snippet(args):
    """``library add-snippet NAME``: a paragraph saved to insert later — typed (``--text``) or the one paragraph of a
    document whose text matches ``--paragraph`` (``--from``)."""
    from .library import LibraryError, save_snippet, snippet_from_document
    if bool(args.text) == bool(args.source_file):
        print("library add-snippet: give the paragraph as --text \"…\" or as --from FILE --paragraph REGEX",
              file=sys.stderr)
        return 2
    try:
        if args.source_file:
            if not args.paragraph:
                print("library add-snippet: --from needs --paragraph REGEX (the paragraph's text)", file=sys.stderr)
                return 2
            text = snippet_from_document(args.source_file, args.paragraph)
            source = f"{Path(args.source_file).name}: the paragraph matching {args.paragraph!r}"
        else:
            text, source = args.text, "typed"
        item = save_snippet(args.name, text, style=args.style, formatting=args.formatting,
                            description=args.description or "", source=source, replace=args.replace)
    except LibraryError as e:
        print(f"library add-snippet: {e} — nothing was saved", file=sys.stderr)
        return 2
    print(f"Saved to your library: {item.label()} ({item.path})\n  text: «{text.strip()}»\nuse it: spec-surgeon "
          f"library use \"{item.name}\" FILES… --after REGEX")
    return 0


def _use_library_item(args):
    """``library use NAME FILES…``: one of your recipes, operations, chains or snippets on these files, with no API key
    needed (Claude's visual check runs only when there is one). A recipe — and a snippet, as the ``insert_paragraph``
    spec ``library.snippet_spec`` builds (after/before the paragraph matching --after/--before, in every file that has
    it, at most once per file) — takes ``do``'s path after the compile (``_preview_and_run``: preview, approval — y/N,
    or --yes —, the token re-checked, the verified batch; --in-place guarded as always); a chain takes
    ``_preview_and_run_chain``. An operation is tried on the files' samples first (CLAUDE.md rule 17: a failing one
    changes nothing), then previewed, approved and run like a custom transform (``_run_saved_operation``) — edited
    copies only."""
    from .chain import ChainError, chain_templates
    from .editspec import is_template_file, spec_templates
    from .library import LibraryError, find, load_chain, load_recipe, read_snippet, snippet_spec
    if not args.files:
        print("library use: which files? (spec-surgeon library use NAME FILES…)", file=sys.stderr)
        return 2
    try:
        item = find(args.name, args.kind)
        spec = chain = None
        if item.kind == "recipe":
            spec = _load_valid_spec(str(load_recipe(item.name)))
            if spec is None:
                return 2
        elif item.kind == "snippet":
            if bool(args.after) == bool(args.before):
                print("library use: say where the snippet goes — --after REGEX or --before REGEX (the text of the "
                      "paragraph it follows or precedes)", file=sys.stderr)
                return 2
            spec = snippet_spec(item.name, read_snippet(item), args.after or args.before,
                                "after" if args.after else "before", tracked=not args.untracked)
        elif item.kind == "chain":
            chain = load_chain(item.name)
    except ChainError as e:
        print(f"your chain {args.name!r} cannot be used [{e.code}]: {e.reason}", file=sys.stderr)
        return 2
    except LibraryError as e:
        print(str(e), file=sys.stderr)
        return 2
    templates = spec_templates(spec) if spec is not None else (chain_templates(chain) if chain is not None else [])
    files = [f for f in expand_inputs(args.files) if not is_template_file(f, templates)]
    if not files:
        print("no .docx files to work on", file=sys.stderr)
        return 2
    out_dir = Path(args.out) if args.out else Path.cwd() / "SpecSurgeon_out" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using your {item.kind} {item.name!r} on {len(files)} file(s).", file=sys.stderr)
    if spec is not None:
        return _preview_and_run(args, spec, files, out_dir)
    if chain is not None:
        return _preview_and_run_chain(args, chain, files, out_dir)
    picked = _try_saved_operation(None, item.name, files, out_dir)
    if picked is None:                            # its trials failed (said above): nothing previewed or run
        return 2
    return _run_saved_operation(args, picked, files, out_dir)


def cmd_gui(args):
    from .gui import main as gui_main
    return gui_main()


def cmd_advise(args):
    from . import llm
    from .advisor import advise_batch
    if not llm.credentials_found():
        diag.event("cli.command.refused", level="warning", reason="model_authentication_missing", feature="advice")
        print(NEEDS_KEY_ADVISE, file=sys.stderr)
        return 2
    res = advise_batch(Path(args.out_dir), only=args.file, model=args.model,
                       log=lambda m: print(m, file=sys.stderr))
    if not res:
        print("no exceptions to advise on")
        return 0
    for a in res:
        print(f"  {a.kind:<14} {Path(a.source).name}" + (f"  ({'; '.join(a.errors)})" if a.errors else ""))
    print(f"\nAdvice: {Path(args.out_dir) / 'advice'}")
    return 0


def _run_folder(path: Path) -> Path:
    """A results folder, or a `do`/window folder whose run is in run/."""
    p = Path(path)
    # Conversation journals also create logs/ at the session level. Only actual run evidence keeps diagnosis here.
    own_run = any((p / name).is_file() for name in
                  ("batch.json", "BATCH_REPORT.md", "DIAGNOSTICS.md", "logs/run.log"))
    if not own_run and (p / "run").is_dir():
        return p / "run"
    return p


def cmd_diagnose(args):
    """Print why each file of a run failed (DIAGNOSTICS.md, or one file's section of it); --bundle zips the run's
    diagnostics with the app's logs — never the documents."""
    from . import diagnostics as diag
    from .settings import logs_dir
    folder = _run_folder(Path(args.out_dir))
    if not folder.is_dir():
        print(f"no such folder: {folder}", file=sys.stderr)
        return 2
    text = (folder / "DIAGNOSTICS.md").read_text(encoding="utf-8") if (folder / "DIAGNOSTICS.md").exists() else None
    if text is None:
        print(f"{folder} has no DIAGNOSTICS.md (the run did not finish, or it is not a results folder).")
        log = folder / diag.LOGS / diag.RUN_LOG
        if log.exists():
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"\nThe end of {log}:\n" + "\n".join(lines[-60:]))
        unfinished = [t for t in sorted((folder / diag.LOGS).glob("*.trace.log"))
                      if diag.last_stage(diag.read_trace(t)) != "result"]
        for t in unfinished[:10]:
            print(f"\n{t.name} stops at '{diag.last_stage(diag.read_trace(t))}':")
            print("\n".join(diag.read_trace(t)[-12:]))
    elif args.file:
        sections = text.split("\n## ")
        hits = [sec for sec in sections[1:] if args.file.lower() in sec.splitlines()[0].lower()]
        if not hits:
            print(f"no file matching {args.file!r} among the problems in {folder / 'DIAGNOSTICS.md'} (did it pass?)")
            trace = [t for t in (folder / diag.LOGS).glob("*.trace.log") if args.file.lower() in t.name.lower()]
            for t in trace[:3]:
                print(f"\n{t.name}:\n" + "\n".join(diag.read_trace(t)))
        for sec in hits:
            print("## " + sec)
    else:
        print(text)
    # Event journals remain useful even when a process stopped before writing the human-readable report.
    root = diag.bundle_root(folder)
    journals = sorted(root.rglob("*.jsonl"))
    if journals:
        print("\nStructured diagnostic journals (UTC times, correlation IDs, stage durations and failures):")
        for journal in journals[:30]:
            print(f"  {journal}")
        if len(journals) > 30:
            print(f"  … {len(journals) - 30} more journals under {root}")
    app_journals = sorted(logs_dir().glob("app-events*.jsonl"))
    if app_journals:
        print(f"Application and command events: {logs_dir()} (app-events*.jsonl)")
    if args.bundle:
        res = diag.make_bundle(Path(args.bundle), folder, logs_dir(), note="spec-surgeon diagnose")
        print(f"\nDiagnostics bundle: {res['path']} — {len(res['included'])} file(s); "
              f"{res['left_out']} document(s) and image(s) left out (never bundled)")
    return 0


def cmd_verify(args):
    from .verify import run_suite
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    rec = run_suite(Path(args.source).read_bytes(), Path(args.output).read_bytes(), plan, args.source, args.output)
    print(rec.to_markdown())
    return 0 if rec.passed else 1


def main(argv=None) -> int:
    from .settings import logs_dir
    diag.setup_app_logging(logs_dir())
    ap = argparse.ArgumentParser(prog="spec-surgeon", description=f"Spec Surgeon {__version__} — surgical, verified .docx edits at any scale.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("jobs", help="list jobs and their parameters").set_defaults(fn=cmd_jobs)

    p = sub.add_parser("fingerprint", help="structure fingerprint (milliseconds per file)")
    p.add_argument("files", nargs="+"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_fingerprint)

    p = sub.add_parser("plan", help="dry run: predicted diff list per file, no outputs written")
    p.add_argument("job"); p.add_argument("files", nargs="+"); p.add_argument("--params", default=None, help="JSON or @file.json")
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_plan)

    p = sub.add_parser("run", help="plan → apply → verify → render → deliver, for 1 file or 100")
    p.add_argument("job"); p.add_argument("files", nargs="+"); p.add_argument("--params", default=None, help="JSON or @file.json")
    p.add_argument("--out", default=None, help="output directory (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--in-place", action="store_true", help="write verified outputs back over the sources (guarded)")
    p.add_argument("--workers", type=int, default=0, help="parallel workers (default: CPU count, max 8)")
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true", help="skip rendering (V6/V7 recorded as N/A)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("ops", help="list the operation library (EditSpec building blocks)")
    p.add_argument("--catalog", action="store_true", help="the machine-readable op catalog + EditSpec schema (JSON)")
    p.set_defaults(fn=cmd_ops)

    p = sub.add_parser("workflows", help="discover and use built-in workflows without an API key")
    p.add_argument("action", choices=["list", "show", "use"])
    p.add_argument("name", nargs="?", help="workflow ID from workflows list")
    p.add_argument("files", nargs="*", help="use: DOCX files or folders")
    p.add_argument("--json", action="store_true", help="list/show: machine-readable workflow descriptions")
    p.add_argument("--inputs", default="{}", help="use: JSON object or @file.json; relative template paths resolve beside the JSON file")
    p.add_argument("--out", default=None)
    p.add_argument("--yes", action="store_true", help="run after preview without an interactive confirmation")
    p.add_argument("--in-place", action="store_true", help="guarded write-back after preview approval; default is copies")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--no-review", action="store_true", help="skip the optional model visual review")
    p.add_argument("--ack-review", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--save-as", default=None, help="save the configured workflow as a chain in Your library")
    p.add_argument("--replace", action="store_true", help="replace a saved chain of the same name")
    p.set_defaults(fn=cmd_workflows)

    p = sub.add_parser("profile", help="document profiles per structure pattern (what the compiler sees)")
    p.add_argument("files", nargs="+"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_profile)

    from .spec_qa import AUDITS
    p = sub.add_parser("audit", help="report-only spec QA: placeholders, section references, standards, readiness and identity")
    p.add_argument("files", nargs="+")
    p.add_argument("--audit", action="append", choices=AUDITS, help="repeat to select audits; default: all five")
    p.add_argument("--identity", help="JSON or @file with canonical project_name, project_number and date")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--out", help="save a .md or .json report; DOCX files are never written")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("plan-spec", help="dry run of an EditSpec over files: per-file plan summary or exception")
    p.add_argument("spec", help="EditSpec JSON file (or inline JSON)"); p.add_argument("files", nargs="+")
    p.add_argument("--json", action="store_true")
    p.add_argument("--per-file", action="store_true", help="show every file (default: one per structure pattern)")
    p.set_defaults(fn=cmd_plan_spec)

    p = sub.add_parser("run-spec", help="execute an EditSpec (JSON) — plan → apply → verify → render → deliver")
    p.add_argument("spec", help="EditSpec JSON file (or inline JSON)"); p.add_argument("files", nargs="+")
    p.add_argument("--out", default=None, help="output directory (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--in-place", action="store_true", help="write verified outputs back over the sources (guarded)")
    p.add_argument("--workers", type=int, default=0, help="parallel workers (default: CPU count, max 8)")
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true", help="skip rendering (V6/V7 recorded as N/A)")
    p.add_argument("--approval", default=None, help="token from a preview: refuse if the spec or a file changed since")
    p.set_defaults(fn=cmd_run_spec)

    p = sub.add_parser("do", help="natural-language request → EditSpec (Claude) → preview → approve → verified run")
    p.add_argument("request"); p.add_argument("files", nargs="*")
    p.add_argument("--from-set-aside", default=None, help="load set-aside files and diagnostics from a prior output dir")
    p.add_argument("--template", default=None,
                   help="template spec (e.g. the architect's) or prepared header/footer template .docx the request "
                        "refers to — \"make the header and footer match the template spec\", \"match the font of "
                        "the template spec\"; left out of the batch")
    p.add_argument("--answer", action="append", help="answer to a question the compiler asked (repeatable)")
    p.add_argument("--out", default=None, help="output directory (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--yes", action="store_true", help="approve without asking (after the preview is written)")
    p.add_argument("--in-place", action="store_true", help="write verified outputs back over the sources (guarded)")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--no-review", action="store_true",
                   help="skip Claude's visual check of the rendered sample pages (--no-render skips it too)")
    p.add_argument("--ack-review", action="store_true",
                   help="run even when Claude's visual check flagged a blocking problem (you have looked at it)")
    p.add_argument("--save-as", default=None, metavar="NAME",
                   help="save the previewed spec as a recipe (or the passing custom transform as one of your "
                        "operations) in your library under NAME")
    p.add_argument("--replace", action="store_true",
                   help="with --save-as: replace an item of the same name and kind (a name the other kind holds is "
                        "refused even with it)")
    p.add_argument("--allow-codegen", action="store_true",
                   help="without a terminal: accept Claude's offer of a one-off custom transform when no operation "
                        "fits — made only when Claude judged it \"possible\" (sandboxed, verified on samples first, "
                        "edited copies only); in a terminal pick it from the choices instead")
    p.set_defaults(fn=cmd_do)

    p = sub.add_parser("retry", help="rinse & repeat: run next pass on set-aside files from a previous output folder")
    p.add_argument("out_dir", help="a previous batch output folder containing set_aside.json")
    p.add_argument("--request", default=None, help="override the original natural-language request")
    p.add_argument("--template", default=None, help="override the template spec / template .docx")
    p.add_argument("--answer", action="append", help="answer to a question the compiler asked (repeatable)")
    p.add_argument("--out", default=None, help="output directory for this pass (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--yes", action="store_true", help="approve without asking (after the preview is written)")
    p.add_argument("--in-place", action="store_true", help="write verified outputs back over the sources (guarded)")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--no-review", action="store_true", help="skip Claude's visual check of the sample pages")
    p.add_argument("--ack-review", action="store_true", help="run even when Claude's visual check flagged a problem")
    p.add_argument("--allow-codegen", action="store_true")
    p.set_defaults(fn=cmd_retry)

    p = sub.add_parser("resolve", help="investigate remaining files on copies within a budget, review, then export")
    p.add_argument("run_dir", help="previous batch or chain output folder containing set_aside.json")
    p.add_argument("--request", default=None, help="append a clarification or answer to the original request")
    p.add_argument("--out", default=None)
    from .workspace_cli import add_resource_arguments, add_workspace_parser
    add_resource_arguments(p)
    p.add_argument("--yes", action="store_true", help="approve exporting ready copies after investigation")
    p.add_argument("--ack-review", action="store_true", help="acknowledge blocking visual findings you inspected; separate from --yes")
    p.set_defaults(fn=cmd_resolve)

    add_workspace_parser(sub)

    p = sub.add_parser("memory", help="inspect, correct, or forget local repair evidence")
    p.add_argument("action", choices=["list", "show", "accept", "reject", "forget"])
    p.add_argument("case_id", nargs="?", default=None)
    p.add_argument("--note", default="", help="your explanation or correction for accept/reject feedback")
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("chain", help="several specs one after another: each stage a full verified run on the stage "
                                     "before it — preview of the whole chain on samples → approve → run (no API key needed)")
    p.add_argument("--stage", action="append", metavar="STAGE",
                   help="an EditSpec .json (or inline JSON), recipe:NAME or chain:NAME — repeat, in the order they run")
    p.add_argument("files", nargs="+", help="the .docx files or folders to run it on")
    p.add_argument("--name", default=None, help="what to call the chain (reports, --save-as)")
    p.add_argument("--out", default=None, help="output directory (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--yes", action="store_true", help="approve without asking (after the preview is written)")
    p.add_argument("--in-place", action="store_true",
                   help="write each stage's verified outputs back over the sources (guarded, stage by stage)")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--no-review", action="store_true", help="skip Claude's visual check of the final sample pages")
    p.add_argument("--ack-review", action="store_true", help="run even when Claude's visual check flagged a problem")
    p.add_argument("--save-as", default=None, metavar="NAME", help="save the previewed chain in your library")
    p.add_argument("--replace", action="store_true", help="with --save-as: replace a chain of that name")
    p.add_argument("--model", default=None)
    p.set_defaults(fn=cmd_chain)

    p = sub.add_parser("library", help="your library of saved recipes (specs), chains, operations (custom transforms) "
                                       "and snippets (paragraphs); `library use NAME FILES…` runs one without an API "
                                       "key")
    p.add_argument("action", choices=["list", "show", "delete", "use", "add-snippet"])
    p.add_argument("name", nargs="?", default=None)
    p.add_argument("files", nargs="*", help="use: the .docx files or folders to run it on")
    p.add_argument("--kind", choices=["recipe", "operation", "chain", "snippet"], default=None)
    p.add_argument("--after", default=None, help="use, a snippet: insert it after the paragraph whose text matches "
                                                 "this regular expression (in every file that has one)")
    p.add_argument("--before", default=None, help="use, a snippet: insert it before that paragraph instead")
    p.add_argument("--untracked", action="store_true", help="use, a snippet: insert it as plain text, not as a "
                                                            "tracked insertion")
    p.add_argument("--text", default=None, help="add-snippet: the paragraph's exact text")
    p.add_argument("--from", dest="source_file", default=None,
                   help="add-snippet: take the paragraph from this .docx (with --paragraph)")
    p.add_argument("--paragraph", default=None, help="add-snippet: the regular expression that finds the ONE "
                                                     "paragraph to take (--from)")
    p.add_argument("--style", default=None, help="add-snippet: a paragraph style id for the inserted paragraph "
                                                 "(default: the anchor's)")
    p.add_argument("--formatting", default="inherit", choices=["inherit", "none"],
                   help="add-snippet: inherit = look like the anchor paragraph (default); none = only the style")
    p.add_argument("--description", default=None, help="add-snippet: a few words about it")
    p.add_argument("--replace", action="store_true", help="add-snippet: replace a snippet of the same name")
    p.add_argument("--yes", action="store_true",
                   help="delete: without asking; use: run after the preview without asking")
    p.add_argument("--out", default=None, help="use: output directory (default ./SpecSurgeon_out/<timestamp>)")
    p.add_argument("--in-place", action="store_true",
                   help="use, a recipe: write verified outputs back over the sources (guarded); an operation always "
                        "writes edited copies")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--renderer", default="auto", choices=["auto", "word", "libreoffice", "none"])
    p.add_argument("--no-render", action="store_true")
    p.add_argument("--no-review", action="store_true",
                   help="use: skip Claude's visual check (it runs only with an API key and rendering on)")
    p.add_argument("--ack-review", action="store_true",
                   help="use: run even when Claude's visual check flagged a blocking problem (you have looked at it)")
    p.set_defaults(fn=cmd_library)

    p = sub.add_parser("verify", help="re-run the verification suite on a source/output pair")
    p.add_argument("source"); p.add_argument("output"); p.add_argument("--plan", required=True); p.set_defaults(fn=cmd_verify)

    sub.add_parser("gui", help="open the Spec Surgeon window").set_defaults(fn=cmd_gui)

    p = sub.add_parser("advise", help="for each exception of a batch: a recommendation or a re-compiled spec (Claude)")
    p.add_argument("out_dir", help="a batch output folder (with exceptions/)"); p.add_argument("--file", default=None)
    p.add_argument("--model", default=None); p.set_defaults(fn=cmd_advise)

    p = sub.add_parser("diagnose", help="why each file of a run failed — where it stopped, why, how; --bundle: a zip "
                                        "of the run's diagnostics and the app's logs (no documents) to send")
    p.add_argument("out_dir", help="a results folder (or a `do` or window session folder with run/ in it)")
    p.add_argument("--file", default=None, help="only the file whose name contains this")
    p.add_argument("--bundle", default=None, help="also write a diagnostics zip here")
    p.set_defaults(fn=cmd_diagnose)

    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        diag.event("cli.arguments.finished", level="warning" if exc.code else "info", exit_code=exc.code)
        raise
    if (getattr(args, "model", None) is None and hasattr(args, "model")
            and not getattr(args, "_saved_resource_policy", False)):
        from .compiler import DEFAULT_MODEL
        args.model = DEFAULT_MODEL
    # Metadata only: raw argv contains requests, document wording, inline specs and potentially credentials.
    options = {key: getattr(args, key) for key in
               ("in_place", "no_render", "no_review", "ack_review", "yes", "workers", "renderer", "model")
               if hasattr(args, key)}
    with diag.scope(component="cli", command_id=diag.new_id(), command=args.cmd):
        with diag.span("cli.command", options=options,
                       input_argument_count=len(getattr(args, "files", None) or [])) as completion:
            code = args.fn(args)
            completion.update(exit_code=code, outcome="ok" if not code else "refused" if code == 2 else "failed",
                              out_dir=getattr(args, "out", None))
            return code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
