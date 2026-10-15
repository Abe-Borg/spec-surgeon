"""spec-surgeon command line.

    spec-surgeon jobs
    spec-surgeon fingerprint FILES...                       [--json]
    spec-surgeon plan JOB FILES... --params JSON|@file      (dry run: predicted diff list, no outputs)
    spec-surgeon run  JOB FILES... --params JSON|@file --out DIR [--in-place] [--workers N]
                                   [--renderer auto|word|libreoffice|none] [--no-render]
    spec-surgeon ops [--catalog]                             (list the operation library; --catalog: JSON for the compiler)
    spec-surgeon profile FILES... [--json]                   (per-structure-pattern document profiles, no model)
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
    spec-surgeon gui                                         (the window; also: spec-surgeon-gui)
    spec-surgeon advise OUT_DIR [--file NAME]                (Claude: per exception a recommendation or a re-compiled spec)
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
from .batch import expand_inputs, load_set_aside, run_batch
from .fingerprint import fingerprint
from .jobs import REGISTRY, get_job, JobException
from .package import DocxPackage


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


def cmd_run_spec(args):
    from .editspec import SpecError, load_spec, validate_spec
    try:
        spec = load_spec(args.spec)
        validate_spec(spec)
    except SpecError as e:
        print(f"EditSpec refused [{e.code}]: {e.reason}", file=sys.stderr)
        for q in e.details.get("questions", []):
            print(f"  ? {q}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(f"cannot read the EditSpec: {e}", file=sys.stderr)
        return 2
    print(f"EditSpec: {spec['request']!r} — {spec['summary']}")
    for a in spec.get("assumptions", []):
        print(f"  assumption: {a}")
    if getattr(args, "approval", None):
        from .editspec import is_template_file, spec_templates
        from .preview import approval_token
        files = [f for f in expand_inputs(args.files) if not is_template_file(f, spec_templates(spec))]
        if approval_token(spec, files) != args.approval:
            print("approval token does not match: the spec or a file changed since the preview (or the file set "
                  "differs) — preview again", file=sys.stderr)
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
        print(f"EditSpec refused [{e.code}]: {e.reason}", file=sys.stderr)
        for q in e.details.get("questions", []):
            print(f"  ? {q}", file=sys.stderr)
    except (OSError, ValueError) as e:
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
    files = expand_inputs(args.files)
    out = []
    t0 = time.perf_counter()
    for f in files:
        fp = fingerprint(DocxPackage.from_path(f))
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
    for f in files:
        pkg = DocxPackage.from_path(f)
        try:
            plan = job.plan(pkg, params)
        except JobException as e:
            rc = 2
            print(f"\n== {f.name}: EXCEPTION [{e.code}] {e.reason}")
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
        elif f.record and not f.record.passed:
            line += "  " + "; ".join(f"{ch.id} FAIL" for ch in f.record.checks if ch.status == "FAIL")
        elif f.delivered_to:
            line += f"  → {f.delivered_to}"
        print(line)
    print(f"\nReport: {out_dir / 'BATCH_REPORT.md'}")
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
    from .editspec import is_template_file
    from .preview import make_preview
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
    out_dir.mkdir(parents=True, exist_ok=True)
    template = templates[0] if templates else None
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    if _interactive():
        outcome, value, conv = _chat(args, files, template, out_dir, prior_failures, log)
        if outcome == "stop":
            return value
        if outcome == "codegen":
            return _do_codegen(args, value, files, out_dir, brief=conv.brief())
        spec = value
    else:
        print(f"compiling {args.request!r} over {len(files)} file(s) with {args.model} …", file=sys.stderr)
        res = compile_request(args.request, files, template=template, answers=args.answer or [],
                              model=args.model, log=log, prior_failures=prior_failures)
        (out_dir / "compile.json").write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False, default=str),
                                              encoding="utf-8")
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
    pv = make_preview(spec, [str(f) for f in files], out_dir / "preview",
                      renderer=args.renderer, do_render=not args.no_render)
    if not args.no_render and not getattr(args, "no_review", False):
        _visual_check(pv, spec, out_dir / "preview", args)
    print(pv.to_text())
    print(f"\npreview: {out_dir / 'preview'}")
    blocking = pv.review.blocking() if pv.review is not None else []
    if blocking and not getattr(args, "ack_review", False):
        print(f"\nClaude's visual check flagged {len(blocking)} problem(s) it rates as blocking (see above).")
        if args.yes and not _interactive():
            print("Not run: look at the pages in the preview folder; re-run with --ack-review to run anyway.")
            return 2
    approved = args.yes and (not blocking or getattr(args, "ack_review", False))
    if not approved and _interactive():
        question = (f"\nYou have looked at Claude's findings and want to run this on {len(pv.batch_plan.files)} "
                    f"file(s)? [y/N] " if blocking else f"\nRun this on {len(pv.batch_plan.files)} file(s)? [y/N] ")
        approved = input(question).strip().lower() in ("y", "yes")
    if not approved:
        print(f"\nNot run. To run exactly this:\n  spec-surgeon run-spec \"{out_dir / 'preview' / 'spec.json'}\" "
              + " ".join(f'"{f}"' for f in files) + f" --approval {pv.token}"
              + (" --in-place" if args.in_place else ""))
        if blocking:
            print("(running that command means you have looked at Claude's visual-check findings above)")
        return 0
    args.out = str(out_dir / "run")
    args.files = [str(f) for f in files]          # exactly the previewed files (an unreferenced --template excluded)
    args.job = "edit-spec"
    args.params = None
    return cmd_run(args, spec=spec)


def _visual_check(pv, spec, folder, args) -> None:
    """Claude's visual check of the preview samples (advisory; never raises; can only add a reason to stop)."""
    from . import compiler, vision
    from .preview import write_preview_files
    intent = "\n".join([f"REQUEST: {spec.get('request') or args.request}", f"WHAT THE SPEC DOES: {spec.get('summary') or ''}"]
                       + [f"ASSUMPTION: {a}" for a in spec.get("assumptions") or []])
    pv.review = vision.review_samples(intent, pv.samples, folder, compiler._client, model=args.model,
                                      log=lambda m: print(m, file=sys.stderr))
    vision.save_review(pv.review, folder)
    write_preview_files(pv)


def _interactive() -> bool:
    """A person is at the terminal (tests replace this with False, so no prompt can block a test run)."""
    return bool(sys.stdin is not None and sys.stdin.isatty() and sys.stdout is not None and sys.stdout.isatty())


def _print_turn(turn) -> None:
    if turn.understanding:
        print(f"Claude understood: {turn.understanding}")
    if turn.message:
        print(f"Claude: {turn.message}")
    for q in turn.questions:
        print(f"  ? {q}")
    for i, c in enumerate(turn.choices, 1):
        print(f"  [#{i}] {c.label}" + (f"\n        exact words: «{c.wording}»" if c.wording else ""))


def _chat(args, files, template, out_dir, prior_failures, log):
    """Talk the request through with Claude at the terminal. Returns ("spec", spec, conv), ("codegen", the unsupported
    spec, conv) when the user picks the custom transform, or ("stop", exit code, conv)."""
    from .conversation import Conversation
    print(f"talking {args.request!r} through with Claude ({args.model}) over {len(files)} file(s) …", file=sys.stderr)
    conv = Conversation(args.request, files, template=template, model=args.model, prior_failures=prior_failures,
                        answers=args.answer or [], work_dir=out_dir / "chat", log=log)
    turn = conv.start()
    while True:
        conv.save(out_dir / "conversation.json")
        (out_dir / "compile.json").write_text(json.dumps(conv.compile_result(turn).to_dict(), indent=2,
                                                         ensure_ascii=False, default=str), encoding="utf-8")
        if turn.status == "ok":
            return "spec", turn.spec, conv
        _print_turn(turn)
        print(f"(Claude so far: {conv.usage.line()})")
        try:
            line = input("Your reply ('#n' picks a choice; an empty line stops): ").strip()
        except EOFError:
            line = ""
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
        if choice.wording:
            ok = input(f"Approve these exact words for your documents: «{choice.wording}»? [y/N] ").strip().lower()
            if ok not in ("y", "yes"):
                continue
        if choice.kind == "custom_transform":
            conv.take_app_choice(choice.id)
            conv.save(out_dir / "conversation.json")
            return "codegen", conv.unsupported, conv
        turn = conv.reply(choice_id=choice.id)


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


def _do_codegen(args, spec, files, out_dir, brief=None):
    """Tier B: generate an op for an unsupported request, prove it on one sample per pattern, run it after
    approval. Outputs only — never in place (a generated op becomes permanent only after human review)."""
    from .codegen import generate_op, pick_samples, run_generated
    samples = pick_samples(files)
    print(f"Tier B: generating a transform, tested on {[s.name for s in samples]} …", file=sys.stderr)
    g = generate_op(args.request, spec.get("proposal") or spec.get("unsupported") or "", samples, out_dir / "codegen",
                    model=args.model, log=lambda m: print(m, file=sys.stderr), brief=brief)
    for a in g.attempts:
        print(f"  attempt {a['attempt']}: " + ("PASS" if not a["problems"] else f"{len(a['problems'])} problem(s): "
                                                + "; ".join(a["problems"][:3])))
    if not g.passed:
        print("No generated transform passed verification: " + "; ".join(g.errors))
        return 2
    print(f"generated op {g.name!r} passed on the samples: {g.path} (select {g.select}, params {g.params})")
    if args.in_place:
        print("note: generated transforms are never delivered in place; outputs go to the output folder")
    approved = args.yes
    if not approved and _interactive():
        approved = input(f"\nRun it on {len(files)} file(s) (outputs only)? [y/N] ").strip().lower() in ("y", "yes")
    if not approved:
        print("Not run. Review the generated module before running it.")
        return 0
    results = run_generated(g, [str(f) for f in files], out_dir / "run", log=lambda m: print(m, file=sys.stderr))
    ok = sum(1 for r in results if r.passed)
    print(f"{ok} of {len(results)} file(s) PASS; outputs in {out_dir / 'run' / 'outputs'}")
    for r in results:
        if not r.passed:
            print(f"  FAIL {r.source.name}: " + "; ".join(r.problems[:2]))
    return 0 if ok == len(results) else 1


def cmd_gui(args):
    from .gui import main as gui_main
    return gui_main()


def cmd_advise(args):
    from .advisor import advise_batch
    res = advise_batch(Path(args.out_dir), only=args.file, model=args.model,
                       log=lambda m: print(m, file=sys.stderr))
    if not res:
        print("no exceptions to advise on")
        return 0
    for a in res:
        print(f"  {a.kind:<14} {Path(a.source).name}" + (f"  ({'; '.join(a.errors)})" if a.errors else ""))
    print(f"\nAdvice: {Path(args.out_dir) / 'advice'}")
    return 0


def cmd_verify(args):
    from .verify import run_suite
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    rec = run_suite(Path(args.source).read_bytes(), Path(args.output).read_bytes(), plan, args.source, args.output)
    print(rec.to_markdown())
    return 0 if rec.passed else 1


def main(argv=None) -> int:
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

    p = sub.add_parser("profile", help="document profiles per structure pattern (what the compiler sees)")
    p.add_argument("files", nargs="+"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_profile)

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
    p.add_argument("--allow-codegen", action="store_true",
                   help="without a terminal: accept Claude's offer of a one-off custom transform when no operation "
                        "fits (sandboxed, verified on samples first, edited copies only); in a terminal pick it from "
                        "the choices instead")
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

    p = sub.add_parser("verify", help="re-run the verification suite on a source/output pair")
    p.add_argument("source"); p.add_argument("output"); p.add_argument("--plan", required=True); p.set_defaults(fn=cmd_verify)

    sub.add_parser("gui", help="open the Spec Surgeon window").set_defaults(fn=cmd_gui)

    p = sub.add_parser("advise", help="for each exception of a batch: a recommendation or a re-compiled spec (Claude)")
    p.add_argument("out_dir", help="a batch output folder (with exceptions/)"); p.add_argument("--file", default=None)
    p.add_argument("--model", default=None); p.set_defaults(fn=cmd_advise)



    args = ap.parse_args(argv)
    if getattr(args, "model", None) is None and hasattr(args, "model"):
        from .compiler import DEFAULT_MODEL
        args.model = DEFAULT_MODEL
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
