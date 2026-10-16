"""Batch report and set-aside manifest — the durable audit artifacts at batch level. Per-file records live in records/."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .batch import BatchResult, FileResult

LOGS = "logs"

FAILURE_EXPLANATIONS: Dict[str, Tuple[str, str]] = {
    "not-found": (
        "Target Text or Anchor Not Found",
        "The document does not contain the exact text, section header, or part the operation looked for. "
        "In the next pass, inspect its paragraphs to use a matching phrase or selector.",
    ),
    "unfilled-slot": (
        "Template Slot Could Not Be Extracted",
        "A template placeholder could not be extracted from this file using the batch rule (e.g., the section "
        "number or title is formatted on a single line, in a table, or missing). Re-running on set-aside files "
        "lets the compiler use a fallback extraction rule for this layout.",
    ),
    "ambiguous": (
        "Ambiguous Match in Document",
        "The extraction pattern or anchor matched multiple conflicting values in this document. A narrower "
        "selector or section-scoped rule is needed.",
    ),
    "inheritance": (
        "Header/Footer Section Inheritance",
        "Removing or changing headers/footers on the selected sections alone would alter what an inheriting "
        "section displays. All linked sections must be updated together.",
    ),
    "even-odd": (
        "Different Odd & Even Pages Setting",
        "This document's evenAndOddHeaders setting differs from the template's.",
    ),
    "title-page": (
        "Different First Page Setting",
        "This document's first-page header/footer setting (titlePg) differs from the template's.",
    ),
    "spans-runs": (
        "Target Text Split Across Differently Formatted Runs",
        "The target text spans runs with distinct visual formatting (e.g., bold/italic/font change mid-phrase). "
        "Enable noise-tolerant coalescing or target the sub-phrases.",
    ),
    "tracked-format": (
        "Pending Tracked Formatting Change on Target",
        "The target sits inside a run or paragraph carrying an unaccepted formatting change (rPrChange/pPrChange).",
    ),
    "field-result": (
        "Target Inside a Word Field",
        "The matched text is inside a live field instruction or cached field result (such as a TOC or REF field) "
        "and is skipped to avoid corrupting field codes.",
    ),
    "nested-container": (
        "Target Inside Nested Container",
        "The target sits inside a tracked insertion wrapping a hyperlink or content control.",
    ),
    "expect": (
        "Expected Change Count Mismatch",
        "The file planned cleanly, but the number of changes differed from the spec's strict 'expect' count.",
    ),
    "verify-fail": (
        "Failed Verification (Not Delivered)",
        "The edit was applied to a copy, but one or more of the V1–V10 checks failed on the result, so the copy was "
        "moved to failed/ and nothing was delivered. Read the file's record under records/ for the failing check; "
        "this is the engine catching a transform it could not prove, not a problem with your file.",
    ),
    "section-break": (
        "Paragraph Carries a Section Break",
        "The selected paragraph holds a section break (w:sectPr); deleting it or inserting after it would change the "
        "document's section layout. Select a neighbouring paragraph or insert before it.",
    ),
    "existing-revisions": (
        "Paragraph Already Has Pending Revisions",
        "Tracked deletion of a paragraph that already carries someone's tracked changes would stack revisions. "
        "Accept or reject them in Word first, or use an untracked deletion.",
    ),
    "structure": (
        "Paragraph Holds Unsupported Content",
        "The paragraph contains elements the operation does not handle in this mode (a hyperlink, content control, "
        "comment anchor or similar). Bookmarks and proofing markers are fine.",
    ),
    "textbox": (
        "Target Inside a Text Box",
        "The selected paragraph lives inside a text box; the operation only edits body paragraphs.",
    ),
    "table-cell": (
        "Would Empty a Table Cell",
        "Deleting every paragraph of a table cell would leave invalid OOXML; keep one paragraph per cell.",
    ),
    "toggle-ambiguous": (
        "Bold/Italic/Caps Set at Several Style Levels",
        "The effective toggle property cannot be resolved without Word's XOR rule; the file needs a human look. For a "
        "style change, set the formatting directly on the selected paragraphs instead (direct formatting always wins).",
    ),
    "conflict-property": (
        "Another Setting Would Override the Requested Value",
        "A setting on the same paragraph or run wins over the requested value: spacing given in lines, an indent "
        "written as start/end, or — for a style change — direct formatting or a symbol-font run that would keep its own "
        "look. Set the property directly on the selected paragraphs instead, or clear the other setting in Word.",
    ),
    "bad-params": (
        "Operation Parameters Rejected",
        "The spec gave this operation a parameter or selector it refuses (wrong type, unknown key, missing value).",
    ),
    "conflict": (
        "Two Operations Touch the Same Thing",
        "Two operations in the spec would change the same paragraph, property or part; the engine never guesses an "
        "order. Split them into separate passes.",
    ),
    "template-slot": (
        "Template Spec Value Not Found",
        "A fill rule finds no usable value in the template spec itself (its own section number or title, say), so the "
        "engine cannot tell what to replace in its header/footer. Give the rule 'in_template' text, or a rule that also "
        "fits the template spec.",
    ),
    "template-value-left": (
        "Template Spec's Own Value Left in Another Form",
        "The template spec's header/footer shows its own section number or title in a form the engine cannot replace "
        "safely (written differently, e.g. '087100', or run into other text); copying it would put the architect's "
        "number or title into every spec. Fix that text in the template spec, or name it with 'in_template'.",
    ),
    "slot-in-field": (
        "Template Value Shown by a Field",
        "The template's header/footer shows the value through a Word field (e.g. STYLEREF or a document property); a "
        "replaced field result would come back when Word updates fields. Replace the field with plain text in the "
        "template.",
    ),
    "slot-in-textbox": (
        "Template Slot Inside a Text Box",
        "A template slot sits in a text box of the template's header/footer, which the templating op does not fill. "
        "Move that text out of the text box in the template.",
    ),
    "slot-structure": (
        "Template Slot Spans Unusual Content",
        "A template slot runs through content the op cannot cut safely (a run holding more than text, a hyperlink or "
        "content control boundary). Retype that placeholder as plain text in the template.",
    ),
    "slot-overlap": (
        "Template Slots Overlap",
        "Two slots of the template overlap (a highlighted span holding a {{NAME}} and other text, or two template "
        "values sharing words), so the engine cannot tell which fill goes where.",
    ),
    "unknown-slot": (
        "Fill Rule Without a Slot",
        "A fill rule names a slot the template's headers/footers in use do not have (for a template spec: its own "
        "value appears in none of them). Drop the rule or fix it.",
    ),
    "template-revisions": (
        "Template Has Pending Tracked Changes",
        "A header/footer part of the template carries tracked changes; they would be copied into every spec. Accept "
        "or reject them in the template first.",
    ),
    "pattern": (
        "Different Document Structure Pattern",
        "This file has a different structural pattern from the pattern(s) the EditSpec targeted.",
    ),
    "unreadable": (
        "Not a Readable Word Document",
        "The file could not be opened as a Word (.docx) document — it is damaged or empty, password-protected, an old "
        ".doc or another kind of file saved under a .docx name, or the disk would not give it back (a OneDrive file "
        "that is online only, say). It was left untouched. The reason says what its bytes look like; open it in Word "
        "and save it as a Word Document (*.docx), or make sure it is fully downloaded, then run again.",
    ),
    "internal-error": (
        "Spec Surgeon Hit an Internal Error",
        "Something unexpected went wrong inside Spec Surgeon while working on this file — a defect in the program, not "
        "a problem with your file. The file was left untouched. DIAGNOSTICS.md and the file's trace (logs/) show the "
        "stage and the line of code; please send the diagnostics bundle (window: Help → Save diagnostics bundle…).",
    ),
    "worker-died": (
        "The Process Working on This File Stopped",
        "The background process working on this file ended abruptly — out of memory, or a crash inside a library it "
        "uses — and did so again when the file was run alone. The file was left untouched; its trace (logs/) shows the "
        "last stage it reached and what Python's fault handler saw.",
    ),
    "io-error": (
        "A File or Folder Could Not Be Read or Written",
        "Spec Surgeon could not read a file the run needs (the template, say) or write to the results folder — "
        "permissions, a full disk, a path too long for Windows, or a file held open by another program. The reason "
        "names the path and the system's error.",
    ),
    "crash": (
        "Unexpected Error (recorded by an older version)",
        "Spec Surgeon 0.4.1 and earlier used this code both for files it could not open and for its own unexpected "
        "errors; the reason says which. Newer versions record 'unreadable' or 'internal-error' instead.",
    ),
    "unsupported-format": (
        "Strict Open XML Document",
        "The file is saved in Word's 'Strict Open XML Document' format, which Spec Surgeon does not read. Open it in "
        "Word and Save As 'Word Document (*.docx)', then run again.",
    ),
    "chain-drift": (
        "Changed Between Two Stages of the Chain",
        "The file a stage was to read is no longer the one the stage before it verified (edited, synced or replaced "
        "between the stages), so the later stage did not read it. Run the chain again on the current file.",
    ),
    "chain-deliver": (
        "The Final Copy Was Not the Verified One",
        "The last stage's output was changed after it was verified, so the chain did not copy it to outputs/. Run the "
        "chain again.",
    ),
}


def explain_failure(code: str, reason: str) -> Tuple[str, str]:
    cat, expl = FAILURE_EXPLANATIONS.get(
        code,
        ("Refused for Safety", "The engine refused to guess on this file's structure and left it untouched."),
    )
    return cat, f"{expl} (Engine diagnosis: {reason})"


def build_set_aside_manifest(batch: "BatchResult") -> Dict[str, Any]:
    """Structured manifest of every file that did not PASS (untouched exceptions and verification failures),
    ready for human review or the next iterative pass (`spec-surgeon retry`)."""
    items: List[Dict[str, Any]] = []
    by_code: Counter = Counter()
    by_pattern: Counter = Counter()
    for f in batch.files:
        if f.status == "PASS":
            continue
        pid = f.fingerprint.get("pattern_id", "?")
        stage = None
        if f.exception:
            code = f.exception.get("code", "exception")
            reason = f.exception.get("reason", "")
            details = f.exception.get("details", {})
            stage = f.exception.get("stage")
        elif f.record and not f.record.passed:
            code = "verify-fail"
            bad = [c for c in f.record.checks if c.status == "FAIL"]
            reason = "; ".join(f"{c.id}: {c.summary}" for c in bad)
            details = {"failed_checks": [c.id for c in bad]}
        else:
            code = "pending"
            reason = "processing did not complete"
            details = {}
        cat, expl = explain_failure(code, reason)
        by_code[code] += 1
        by_pattern[pid] += 1
        items.append({
            "source": str(f.source.resolve()),
            "name": f.source.name,
            "pattern_id": pid,
            "status": "SET_ASIDE",
            "code": code,
            "category": cat,
            "reason": reason,
            "explanation": expl,
            "details": details,
            "body_hygiene": f.fingerprint.get("body_hygiene", {}),
            **({"stage": stage} if stage else {}),
            **({"trace": f"logs/{Path(f.trace_path).name}"} if getattr(f, "trace_path", None) else {}),
        })
    templates = []
    if batch.spec:
        from .editspec import spec_templates
        templates = [str(t) for t in spec_templates(batch.spec)]
    return {
        "job": batch.job,
        "request": (batch.spec or {}).get("request") or batch.params.get("request") or "",
        "spec": batch.spec,
        "template": templates[0] if templates else None,
        "total_batch_files": len(batch.files),
        "passed_count": sum(1 for f in batch.files if f.status == "PASS"),
        "set_aside_count": len(items),
        "by_code": dict(by_code.most_common()),
        "by_pattern": dict(by_pattern.most_common()),
        "files": items,
    }


def write_batch_report(batch: "BatchResult") -> Path:
    out = batch.out_dir
    c = batch.counts()
    manifest = build_set_aside_manifest(batch)
    (out / "set_aside.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [f"# Spec Surgeon — batch report",
             "",
             (f"Job: `{batch.job}`    Params: `{json.dumps(batch.params)}`" if batch.spec is None else
              f"EditSpec: “{batch.spec.get('request')}” — mode `{batch.spec.get('mode')}`, "
              f"ops {[o.get('op') for o in batch.spec.get('ops', [])]}"),
             f"Files: {len(batch.files)}  —  PASS {c['PASS']}, FAIL {c['FAIL']}, EXCEPTION {c['EXCEPTION']} "
             f"(Set aside untouched: {manifest['set_aside_count']})",
             f"Renderer: {batch.renderer or 'none'}    Timings: " + ", ".join(f"{k} {v:.1f}s" for k, v in batch.timings.items()),
             ""]
    if batch.spec is not None:
        lines.append("## EditSpec")
        lines.append(f"- Summary: {batch.spec.get('summary')}")
        for a in batch.spec.get("assumptions", []):
            lines.append(f"- Assumption: {a}")
        for i, o in enumerate(batch.spec.get("ops", []), 1):
            lines.append(f"- Op #{i} `{o.get('op')}` select `{json.dumps(o.get('select', {}))}` params `{json.dumps(o.get('params', {}))}`")
        for a in batch.spec.get("assertions", []):
            lines.append(f"- Post-condition (V10): `{json.dumps(a)}`")
        for x in batch.skipped:
            lines.append(f"- Not processed (the spec's own template file): `{x}`")
        lines.append("")
    patterns = Counter(f.fingerprint.get("pattern_id", "?") for f in batch.files)
    lines.append("## Structure patterns (fingerprint)")
    for pid, n in patterns.most_common():
        statuses = Counter(f.status for f in batch.files if f.fingerprint.get("pattern_id") == pid)
        lines.append(f"- `{pid}`: {n} file(s) — " + ", ".join(f"{k} {v}" for k, v in statuses.items()))
    lines.append("")
    lines.append("## Files")
    lines.append("| File | Status | Diffs | Rev Δ | V1 | V2 | V3 | V4 | V5a | V5b | V5c | V6 | V7 | V8 | V9 | V10 | Delivered to |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for f in batch.files:
        if f.record:
            r = f.record
            cells = [r.get(cid).status[:4] if r.get(cid) else "-" for cid in
                     ("V1", "V2", "V3", "V4", "V5a", "V5b", "V5c", "V6", "V7", "V8", "V9", "V10")]
            rd = r.predicted_rev_delta
            lines.append(f"| {f.source.name} | **{f.status}** | {len(r.predicted_diffs)} | {rd.get('ins', 0):+d}/{rd.get('del', 0):+d} | "
                         + " | ".join(cells) + f" | {f.delivered_to or ''} |")
        else:
            code = (f.exception or {}).get("code", "?")
            lines.append(f"| {f.source.name} | **EXCEPTION** ({code}) | | | " + " | ".join(["-"] * 12) + " | |")
    lines.append("")
    if manifest["files"]:
        lines.append("## Set-Aside Files (Untouched — Ready for Next Pass or Manual Review)")
        lines.append(f"{manifest['set_aside_count']} file(s) were safely set aside without changes. "
                     f"Run `spec-surgeon retry \"{out}\"` (or click **Load set-aside files** in the GUI) to compile "
                     f"a pass specifically for these files.")
        lines.append("")
        for item in manifest["files"]:
            lines.append(f"- **{item['name']}** (pattern `{item['pattern_id']}`) — **{item['category']}** (`{item['code']}`)")
            lines.append(f"  - Why skipped: {item['reason']}")
            lines.append(f"  - Next step: {item['explanation']}")
        lines.append("")
        sa_lines = [
            "# Set-Aside Files Report",
            "",
            f"Request: `{manifest['request']}`",
            f"Passed in this pass: {manifest['passed_count']} / {manifest['total_batch_files']}",
            f"Set aside (untouched): {manifest['set_aside_count']}",
            "",
            "## Summary by Failure Reason",
        ]
        for code, cnt in manifest["by_code"].items():
            cat, _ = explain_failure(code, "")
            sa_lines.append(f"- `{code}` ({cat}): {cnt} file(s)")
        sa_lines += ["", "## Files Set Aside"]
        for item in manifest["files"]:
            sa_lines.append(f"### {item['name']} (pattern `{item['pattern_id']}`)")
            sa_lines.append(f"- **Category**: {item['category']} (`{item['code']}`)")
            sa_lines.append(f"- **Reason**: {item['reason']}")
            sa_lines.append(f"- **Guidance**: {item['explanation']}")
            sa_lines.append("")
        (out / "SET_ASIDE_REPORT.md").write_text("\n".join(sa_lines), encoding="utf-8")
    exc = [f for f in batch.files if f.exception]
    if exc:
        lines.append("## Exceptions (Tier 2 — route to a human, `spec-surgeon retry`, or `spec-surgeon advise`)")
        for f in exc:
            lines.append(f"- **{f.source.name}** — `{f.exception.get('code')}`: {f.exception.get('reason')}")
            lines.append(f"  - diagnosis: `exceptions/{getattr(f, 'record_stem', f.source.stem)}.exception.json`")
        lines.append("")
    failed = [f for f in batch.files if f.record and not f.record.passed]
    if failed:
        lines.append("## Failed verification (NOT delivered)")
        lines.append("The edited copies of these files were moved out of `outputs/` to `failed/`. They are kept for "
                     "diagnosis only — do not use them.")
        for f in failed:
            bad = [c for c in f.record.checks if c.status == "FAIL"]
            lines.append(f"- **{f.source.name}**: " + "; ".join(f"{c.id} {c.summary}" for c in bad))
        lines.append("")
    flagged = [(f, fl) for f in batch.files if f.record for fl in f.record.flags]
    if flagged:
        lines.append("## Flagged for review")
        for f, fl in flagged:
            lines.append(f"- {f.source.name}: {fl}")
        lines.append("")
    lines.append("## Where to look")
    lines.append("- **Why a file failed, where and how: `DIAGNOSTICS.md`** (every file that did not pass, with its stage, "
                 "the specifics, its trace and what to do next)")
    lines.append("- Everything the run did, timestamped: `logs/run.log`; one file's path through the pipeline: "
                 "`logs/<name>.trace.log`")
    lines.append(f"- Per-file verification records: `records/<name>.record.md` (the enumerated diff list and every check with its numbers)")
    if manifest["files"]:
        lines.append(f"- Set-aside manifest for next pass: `set_aside.json` and `SET_ASIDE_REPORT.md`")
    if batch.contact_sheet:
        lines.append(f"- First pages of every output: `{batch.contact_sheet.relative_to(out)}`")
    lines.append("- Rendered pages that changed: `render/pages/`")
    lines.append("- Outputs that passed every check: `outputs/` (or written back in place when run with `--in-place`)")
    if failed:
        lines.append("- Copies that FAILED verification (diagnosis only, never delivered): `failed/`")
    (out / "BATCH_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "batch.json").write_text(json.dumps({
        "job": batch.job, "params": batch.params, "spec": batch.spec, "renderer": batch.renderer, "timings": batch.timings,
        "counts": c,
        "set_aside_count": manifest["set_aside_count"],
        "started": getattr(batch, "started", ""), "finished": getattr(batch, "finished", ""),
        "environment": getattr(batch, "environment", {}), "log": f"{LOGS}/run.log",
        "files": [{"source": str(f.source), "status": f.status, "pattern_id": f.fingerprint.get("pattern_id"),
                   "output": str(f.output) if f.output else None, "delivered_to": f.delivered_to,
                   "exception": f.exception, "timings": f.timings,
                   "trace": f"{LOGS}/{Path(f.trace_path).name}" if getattr(f, "trace_path", None) else None}
                  for f in batch.files],
    }, indent=2, default=str), encoding="utf-8")
    return out / "BATCH_REPORT.md"


# ---- DIAGNOSTICS.md: why each file that did not pass failed, where, and how -------------------------------------------

STAGE_TEXT = {
    "acquire": "reading the file from disk",
    "open": "opening it as a Word package",
    "fingerprint": "reading its structure",
    "plan": "planning the edit (predict first, nothing changed yet)",
    "apply": "applying the edit to a copy",
    "verify": "verifying the edited copy",
    "write": "writing the verified copy to outputs/",
    "render": "rendering the pages and comparing them",
    "deliver": "delivering it (the overwrite guard)",
    "transform+verify": "editing and verifying",
}
KIND_TEXT = {
    "refusal": "the engine refused to guess on this file's structure and left it untouched",
    "unreadable": "the file could not be opened as a Word document; it was left untouched",
    "environment": "a file or folder the run needs could not be read or written; nothing was delivered for this file",
    "internal-error": "Spec Surgeon itself failed — a defect in the program, not a problem with the file; the file was "
                      "left untouched",
    "worker-died": "the process working on this file stopped abruptly, twice; the file was left untouched",
}
CHECK_STAGE = {"V6": "render", "V7": "render", "V9": "deliver"}


def _code(text: str, lang: str = "") -> List[str]:
    return [f"```{lang}", *str(text).rstrip("\n").splitlines(), "```"]


def _detail_lines(details: Dict[str, Any], skip=("op", "op_number", "traceback", "content")) -> List[str]:
    from .diagnostics import compact
    out = []
    for k, v in (details or {}).items():
        if k in skip or v in (None, "", [], {}):
            continue
        out.append(f"  - {k}: " + (str(v) if isinstance(v, (str, int, float)) else compact(v, 500)))
    return out


def exception_diagnosis(exc: Dict[str, Any], name: str, stem: Optional[str] = None,
                        trace: Sequence[str] = (), heading: str = "##", number: Optional[int] = None) -> str:
    """One set-aside file, in Markdown: what happened, where, why, what to do, what to read — and how it got there."""
    code = exc.get("code", "exception")
    category, explanation = FAILURE_EXPLANATIONS.get(code, ("Refused for Safety", "The engine refused to guess on "
                                                            "this file's structure and left it untouched."))
    kind = exc.get("kind") or ("internal-error" if code in ("crash",) else "refusal")
    stage = exc.get("stage")
    where = (f"{STAGE_TEXT.get(stage, stage)} (`{stage}`)" if stage else "before or while planning")
    if exc.get("op_number"):
        where += f", op #{exc['op_number']} `{exc.get('op')}`"
    if exc.get("where"):
        where += f" — at `{exc['where']}`"
    lines = [f"{heading} {f'{number}. ' if number else ''}{name} — {category} (`{code}`)", "",
             f"- **What happened:** {KIND_TEXT.get(kind, KIND_TEXT['refusal'])}.",
             f"- **Where it stopped:** {where}",
             f"- **Why:** {exc.get('reason', '')}"]
    if exc.get("hint"):
        lines.append(f"- **What the system said it means:** {exc['hint']}")
    det = _detail_lines(exc.get("details") or {})
    if det:
        lines += ["- **Details:**", *det]
    lines.append(f"- **What to do:** {explanation}")
    if stem:
        reads = [f"`exceptions/{stem}.exception.json`"] + ([f"`{exc['trace']}`"] if exc.get("trace") else [])
        lines.append("- **Read next:** " + " · ".join(reads))
    if exc.get("traceback") and kind in ("internal-error", "environment", "worker-died"):
        lines += ["", "**Traceback** (for whoever fixes it):", "", *_code(exc["traceback"])]
    if trace:
        lines += ["", "**How it got there** (the file's trace):", "", *_code("\n".join(trace))]
    return "\n".join(lines)


def file_diagnosis(f: "FileResult", heading: str = "##", number: Optional[int] = None) -> str:
    """One file that did not pass (or passed: then a short note), in Markdown — the section DIAGNOSTICS.md has for it
    and what the window shows when the file's row is opened."""
    name, stem = f.source.name, f.record_stem
    if f.exception:
        return exception_diagnosis(f.exception, name, stem, f.trace, heading, number)
    rec = f.record
    if rec is None:
        return "\n".join([f"{heading} {f'{number}. ' if number else ''}{name} — not finished", "",
                          "- **What happened:** the run stopped before this file was done.", *(
                              ["", "**How far it got** (the file's trace):", "", *_code("\n".join(f.trace))]
                              if f.trace else [])])
    failures = rec.failures()
    if not failures:
        where = f.delivered_to or (str(f.output) if f.output else "")
        return "\n".join([f"{heading} {f'{number}. ' if number else ''}{name} — PASS", "",
                          "- Every check passed" + (f"; delivered to `{where}`." if where else "."),
                          f"- Record: `records/{stem}.record.md`" + (f" · trace: `logs/{Path(f.trace_path).name}`"
                                                                    if f.trace_path else "")])
    ids = [c for c, *_ in failures]
    stages = sorted({CHECK_STAGE.get(c, "verify") for c in ids}, key=list(STAGE_TEXT).index)
    if ids == ["V9"]:
        happened = ("every check passed, but delivery was refused (the source changed during the run, or the file "
                    "could not be written back); nothing was delivered")
        meaning = ("Not an editing problem: close the file in Word, make sure nobody is changing it, and run again "
                   "(it is quick).")
    elif set(ids) <= {"V6", "V7"} and rec.get("V6") is not None and rec.get("V6").status == "FAIL":
        happened = ("the edit verified, but its pages could not be rendered or compared, so its layout could not be "
                    "checked; nothing was delivered")
        meaning = ("Usually the renderer, not the edit: the renderer's own words are below and in logs/run.log. Run "
                   "again (or without rendering if the layout check is not needed).")
    else:
        happened = (f"the edit was applied to a copy and the independent verifier rejected it ({', '.join(ids)}); "
                    "nothing was delivered")
        meaning = FAILURE_EXPLANATIONS["verify-fail"][1]
    out_rel = (f"`{Path(f.output).parent.name}/{Path(f.output).name}`" if f.output else "")
    lines = [f"{heading} {f'{number}. ' if number else ''}{name} — Failed verification (not delivered)", "",
             f"- **What happened:** {happened}.",
             "- **Where it stopped:** " + "; ".join(f"{STAGE_TEXT[s]} (`{s}`)" for s in stages),
             "- **Why:**"]
    for cid, cname, summary, details in failures:
        lines.append(f"  - **{cid} {cname}** — {summary if len(summary) <= 400 else summary[:399] + '…'}")
        lines += [f"    - {d}" for d in details if d != summary]
    lines.append(f"- **What this means:** {meaning}")
    reads = [f"`records/{stem}.record.md`", f"`plans/{stem}.plan.json`"]
    if f.trace_path:
        reads.append(f"`logs/{Path(f.trace_path).name}`")
    if out_rel:
        reads.append(f"the rejected copy {out_rel} (diagnosis only — never use it)")
    lines.append("- **Read next:** " + " · ".join(reads))
    if f.trace:
        lines += ["", "**How it got there** (the file's trace):", "", *_code("\n".join(f.trace))]
    return "\n".join(lines)


def _short_why(f: "FileResult") -> str:
    if f.exception:
        return str(f.exception.get("reason", ""))
    if f.record is not None:
        bad = [c for c in f.record.checks if c.status == "FAIL"]
        return "; ".join(f"{c.id}: {c.summary}" for c in bad)
    return "not finished"


def _cell(text: str, n: int = 160) -> str:
    t = " ".join(str(text).split()).replace("|", "\\|")
    return t if len(t) <= n else t[:n - 1] + "…"


def write_diagnostics(batch: "BatchResult") -> Path:
    """``DIAGNOSTICS.md``: the run (when, what, which program on which computer, which renderer), and for every file
    that did not pass — what happened, where it stopped, why in specifics, what to do, what to read, and its trace."""
    from .diagnostics import environment_line
    from . import render as render_mod
    out = batch.out_dir
    c = batch.counts()
    problems = [f for f in batch.files if f.status != "PASS"]
    kinds = Counter((f.exception or {}).get("kind") or ("refusal" if f.exception else "verify") for f in problems)
    internal = [f for f in problems if (f.exception or {}).get("kind") in ("internal-error", "worker-died")
                or (f.exception or {}).get("code") == "crash"]
    total = (batch.timings or {}).get("total")
    request = (f"EditSpec “{batch.spec.get('request')}” — ops "
               + ", ".join(f"`{o.get('op')}`" for o in batch.spec.get("ops", []))
               if batch.spec is not None else f"job `{batch.job}` with `{json.dumps(batch.params, ensure_ascii=False)}`")
    split = ", ".join(f"{label} {kinds[k]}" for k, label in (
        ("refusal", "refused"), ("unreadable", "unreadable"), ("environment", "I/O problems"),
        ("internal-error", "internal errors"), ("worker-died", "workers died"), ("verify", "failed verification"))
        if kinds.get(k))
    lines = ["# Spec Surgeon — diagnostics", "",
             "Every file that did not pass: what happened, where it stopped, why (in specifics), what to do, what to "
             "read next, and how it got there. Everything the run did is in `logs/run.log`; each file's own path "
             "through the pipeline is in `logs/<name>.trace.log`.", "",
             f"- **Run:** {getattr(batch, 'started', '') or '?'} → {getattr(batch, 'finished', '') or '?'}"
             + (f" ({total:.1f} s)" if isinstance(total, (int, float)) else "") + f" — results in `{out}`",
             f"- **Request:** {request}",
             f"- **Result:** {len(batch.files)} file(s): PASS {c['PASS']} · FAIL {c['FAIL']} · EXCEPTION "
             f"{c['EXCEPTION']}" + (f" ({split})" if split else ""),
             f"- **Program:** {environment_line(getattr(batch, 'environment', None) or {})}",
             f"- **Renderer:** {render_mod.describe(batch.renderer) if batch.renderer else 'none (V6/V7 not run)'}"]
    if batch.skipped:
        lines.append("- **Not processed** (the spec's own template): " + ", ".join(f"`{Path(x).name}`" for x in batch.skipped))
    lines.append("")
    if getattr(batch, "stopped", None):
        lines += [f"> **The batch itself stopped** — {batch.stopped}. The files below are the ones it had reached; "
                  f"`logs/run.log` ends with the traceback. This is a defect in Spec Surgeon: please send the "
                  f"diagnostics bundle.", ""]
    if internal:
        lines += [f"> **Spec Surgeon itself failed on {len(internal)} file(s)** — a defect in the program, not in your "
                  f"files. Send the diagnostics bundle (window: Help → Save diagnostics bundle…; command line: "
                  f"`spec-surgeon diagnose \"{out}\" --bundle diagnostics.zip`) with a word about what you ran.", ""]
    if not problems and not getattr(batch, "stopped", None):
        lines += ["**No problems: every file passed every check.**", ""]
    if problems:
        lines += ["## Summary", "", "| # | File | Outcome | Stopped at | Why |", "|---|---|---|---|---|"]
        for i, f in enumerate(problems, 1):
            e = f.exception or {}
            outcome = (f"EXCEPTION `{e.get('code')}`" if f.exception else
                       f"FAIL {', '.join(c.id for c in f.record.checks if c.status == 'FAIL')}" if f.record else
                       "not finished")
            stage = e.get("stage") or ("—" if not f.record else
                                       ", ".join(sorted({CHECK_STAGE.get(c.id, "verify") for c in f.record.checks
                                                         if c.status == "FAIL"})))
            lines.append(f"| {i} | {_cell(f.source.name, 80)} | {outcome} | {stage} | {_cell(_short_why(f))} |")
        lines.append("")
        for i, f in enumerate(problems, 1):
            lines += [file_diagnosis(f, "##", i), ""]
    t = batch.timings or {}
    if t:
        lines += ["## Timing", "", "- " + " · ".join(f"{k} {v:.2f} s" for k, v in t.items())]
        slow = sorted((f for f in batch.files if f.timings.get("total")), key=lambda f: -f.timings["total"])[:5]
        if slow:
            lines.append("- Slowest files (editing and verifying): " + "; ".join(
                f"{f.source.name} {f.timings['total']:.2f} s ("
                + ", ".join(f"{k} {v:.2f}" for k, v in f.timings.items() if k != "total") + ")" for f in slow))
        lines.append("")
    path = out / "DIAGNOSTICS.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---- chains: several specs run one after another (chain.py) ------------------------------------------------------------

def build_chain_set_aside_manifest(result: "Any") -> Dict[str, Any]:
    """The set-aside manifest of a chain (``chain.ChainResult``): every file that did not pass every stage, with the
    ORIGINAL source path (the next pass — ``retry``, *Load set-aside files* — works on the user's files), the stage it
    stopped at and why. The same keys as a batch's manifest, so the loaders read it as one."""
    from .chain import chain_name, chain_templates
    items: List[Dict[str, Any]] = []
    by_code: Counter = Counter()
    by_pattern: Counter = Counter()
    n = len(result.stages)
    for f in result.files:
        if f.status == "PASS":
            continue
        pid = f.pattern_id or "?"
        by_code[f.code] += 1
        by_pattern[pid] += 1
        stage = f.stopped_at
        final_failure = f.code == "chain-outcome"
        items.append({
            "source": str(f.source.resolve()), "name": f.source.name, "pattern_id": pid, "status": "SET_ASIDE",
            "code": f.code, "category": f.category,
            "reason": ("stopped at final requirements" if final_failure else f"stopped at stage {stage} of {n}")
                      + (f" — {f.reason}" if f.reason else ""),
            "explanation": f.explanation,
            "details": {**f.details, "chain_stage": stage,
                        "stage_name": result.stages[stage - 1].name if stage and stage <= n else None,
                        "stage_folder": str(result.stages[stage - 1].out_dir) if stage and stage <= n else None,
                        "stages": list(f.stage_status)},
            "body_hygiene": {},
            **({"stage": "chain final requirements" if final_failure else f"chain stage {stage}"} if stage else {}),
        })
    templates = [str(t) for t in chain_templates(result.chain)]
    return {
        "job": "chain", "request": chain_name(result.chain), "spec": None, "chain": result.chain,
        "template": templates[0] if templates else None,
        "total_batch_files": len(result.files),
        "passed_count": sum(1 for f in result.files if f.status == "PASS"),
        "set_aside_count": len(items),
        "by_code": dict(by_code.most_common()), "by_pattern": dict(by_pattern.most_common()),
        "files": items,
    }


def write_chain_report(result: "Any") -> Path:
    """``CHAIN_REPORT.md``, ``chain.json`` and ``set_aside.json`` of a chain run: the stages, every file's way through
    them (one column per stage), where each stopped and why, what was flagged, and where to look (each stage's own
    BATCH_REPORT.md, DIAGNOSTICS.md and records are in its folder)."""
    from .chain import chain_name
    from .editspec import spec_templates
    out = Path(result.out_dir)
    c = result.counts()
    manifest = build_chain_set_aside_manifest(result)
    (out / "set_aside.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    n = len(result.stages)
    lines = ["# Spec Surgeon — chain report", "",
             f"Chain: “{chain_name(result.chain)}” — {n} stage(s), run one after another on each file; every stage is "
             f"a full verified run (plan → apply → verify V1–V10 → render → deliver) on the files the stage before it "
             f"verified.",
             f"Files: {len(result.files)}  —  PASS {c['PASS']} (every stage), FAIL {c['FAIL']}, EXCEPTION "
             f"{c['EXCEPTION']} (set aside: {manifest['set_aside_count']})",
             f"Mode: {'written back in place, stage by stage (guarded)' if result.in_place else 'copies — the sources were not changed'}"
             f"    Renderer: {result.renderer or 'none'}    Timings: "
             + ", ".join(f"{k} {v:.1f}s" for k, v in (result.timings or {}).items()), ""]
    if result.chain.get("description"):
        lines += [f"> {result.chain['description']}", ""]
    if result.chain.get("assertions"):
        lines += ["Final requirements are checked independently on the exact verified bytes after all stages. "
                  "Only files that also satisfy every final requirement reach this chain's `outputs/`. "
                  "These checks do not replace visual review.", ""]
    lines.append("## Stages")
    for s in result.stages:
        spec = s.spec
        cc = s.batch.counts() if s.batch is not None else {}
        lines.append(f"{s.index}. **{s.name}** — “{spec.get('request')}” (mode `{spec.get('mode')}`, ops "
                     f"{[o.get('op') for o in spec.get('ops', [])]}) — folder `{s.out_dir.name}/`: "
                     + (f"{len(s.inputs)} file(s): PASS {cc.get('PASS', 0)}, FAIL {cc.get('FAIL', 0)}, EXCEPTION "
                        f"{cc.get('EXCEPTION', 0)}" if s.batch is not None else "not run (nothing left)"))
        for t in [str(x) for x in spec_templates(spec)]:
            lines.append(f"   - template: `{t}`")
    for x in result.skipped:
        lines.append(f"- Not processed (a stage's own template file): `{x}`")
    lines.append("")
    lines.append("## Files")
    head = "| File | Result | " + " | ".join(f"{s.index} {s.name}" for s in result.stages) + " | Stopped at | Delivered to |"
    lines.append(head)
    lines.append("|---|---|" + "---|" * n + "---|---|")
    for f in result.files:
        cells = [st if st != "NOT_RUN" else "—" for st in f.stage_status] + ["—"] * (n - len(f.stage_status))
        stopped = "final requirements" if f.code == "chain-outcome" else f"stage {f.stopped_at}" if f.stopped_at else ""
        lines.append(f"| {f.source.name} | **{f.status}** | " + " | ".join(cells) + f" | {stopped} | {f.delivered_to or ''} |")
    lines.append("")
    if result.chain.get("assertions"):
        lines += ["## Final requirements", "", "```json", json.dumps(result.chain["assertions"], indent=2), "```", ""]
        for f in result.files:
            if not f.outcomes:
                lines.append(f"- **{f.source.name}** — not checked (the file did not reach final verification)")
            for entry in f.outcomes:
                lines.append(f"- **{f.source.name}** — requirement {entry['index']}: **{entry['status']}** — {entry['summary']}")
        lines.append("")
    if manifest["files"]:
        lines.append("## Set-Aside Files (did not get through every stage)")
        lines.append(f"{manifest['set_aside_count']} file(s) stopped at a stage. In copies mode their sources are "
                     f"untouched; in place, the stages before the one that stopped were written back (said per file). "
                     f"Run `spec-surgeon retry \"{out}\"` (or **Load set-aside files** in the window) for a pass on "
                     f"only these files.")
        lines.append("")
        for item in manifest["files"]:
            lines.append(f"- **{item['name']}** (pattern `{item['pattern_id']}`) — **{item['category']}** (`{item['code']}`)")
            lines.append(f"  - Why: {item['reason']}")
            lines.append(f"  - Next step: {item['explanation']}")
            folder = item["details"].get("stage_folder")
            if folder:
                lines.append(f"  - Where to look: `{Path(folder).name}/DIAGNOSTICS.md`")
        lines.append("")
    flagged = [(f, fl) for f in result.files for fl in f.flags]
    if flagged:
        lines.append("## Flagged for review (the order of the stages)")
        lines.append("A later stage changed a part an earlier stage added from its template: inside one spec the engine "
                     "would refuse that; in a chain the order is yours. Look at the pages.")
        for f, fl in flagged:
            lines.append(f"- {f.source.name}: {fl}")
        lines.append("")
    lines.append("## Where to look")
    lines.append("- **Why a file stopped, where and how: `DIAGNOSTICS.md`** here (every file that did not get through, "
                 "with the stage), and `stage-NN/DIAGNOSTICS.md` for the stage's own specifics")
    lines.append("- Each stage's run: `stage-NN/BATCH_REPORT.md`, `stage-NN/logs/run.log`, `stage-NN/records/`, "
                 "`stage-NN/plans/`, `stage-NN/exceptions/`")
    lines.append("- Everything the chain did, stage by stage: `logs/chain.log`")
    if not result.in_place:
        lines.append("- Files that passed every stage: `outputs/` (each stage's own copies are in `stage-NN/outputs/`)")
    else:
        lines.append("- Files that passed every stage were written back over the originals, stage by stage")
    if manifest["files"]:
        lines.append("- Set-aside manifest for the next pass: `set_aside.json`")
    (out / "CHAIN_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "chain.json").write_text(json.dumps({
        "chain": result.chain, "name": chain_name(result.chain), "in_place": result.in_place,
        "renderer": result.renderer, "timings": result.timings, "counts": c,
        "set_aside_count": manifest["set_aside_count"], "skipped": result.skipped,
        "started": result.started, "finished": result.finished, "environment": result.environment,
        "log": f"{LOGS}/chain.log", "stopped": result.stopped,
        "stages": [s.to_dict() for s in result.stages], "files": [f.to_dict() for f in result.files],
    }, indent=2, default=str), encoding="utf-8")
    return out / "CHAIN_REPORT.md"


def write_chain_diagnostics(result: "Any") -> Path:
    """``DIAGNOSTICS.md`` of a chain: for every file that did not get through every stage — the stage it stopped at,
    and that stage's own diagnosis of it (``file_diagnosis`` of the stage's record; its trace lives in the stage's
    ``logs/``)."""
    from .diagnostics import environment_line
    from .chain import chain_name
    out = Path(result.out_dir)
    c = result.counts()
    n = len(result.stages)
    problems = [f for f in result.files if f.status != "PASS"]
    total = (result.timings or {}).get("total")
    lines = ["# Spec Surgeon — diagnostics (chain)", "",
             "Every file that did not get through every stage: which stage it stopped at, what happened there, why, "
             "what to do. Everything the chain did is in `logs/chain.log`; each stage's run is in its own folder "
             "(`stage-NN/logs/run.log`, one trace per file beside it).", "",
             f"- **Run:** {result.started or '?'} → {result.finished or '?'}"
             + (f" ({total:.1f} s)" if isinstance(total, (int, float)) else "") + f" — results in `{out}`",
             f"- **Chain:** “{chain_name(result.chain)}” — " + " → ".join(f"{s.index} {s.name}" for s in result.stages),
             f"- **Result:** {len(result.files)} file(s): PASS {c['PASS']} · FAIL {c['FAIL']} · EXCEPTION {c['EXCEPTION']}",
             f"- **Program:** {environment_line(result.environment or {})}", ""]
    if result.stopped:
        lines += [f"> **The chain itself stopped** — {result.stopped}. The files below are the ones it had reached; "
                  f"`logs/chain.log` ends with the traceback. This is a defect in Spec Surgeon: please send the "
                  f"diagnostics bundle.", ""]
    if not problems and not result.stopped:
        lines += ["**No problems: every file passed every stage.**", ""]
    if problems:
        lines += ["## Summary", "", "| # | File | Outcome | Stopped at | Why |", "|---|---|---|---|---|"]
        for i, f in enumerate(problems, 1):
            where = ("final requirements" if f.code == "chain-outcome" else
                     f"stage {f.stopped_at} of {n} ({result.stages[f.stopped_at - 1].name})"
                     if f.stopped_at and f.stopped_at <= n else "—")
            lines.append(f"| {i} | {_cell(f.source.name, 80)} | {f.status} `{f.code}` | {where} | {_cell(f.reason)} |")
        lines.append("")
        for i, f in enumerate(problems, 1):
            if f.code == "chain-outcome":
                lines += [f"## {i}. {f.source.name} — final requirements failed", "",
                          "Every individual stage passed its checks, but the resulting file did not satisfy "
                          "all explicit final requirements. No final copy was delivered.", "",
                          f"- **What to do:** {f.explanation}"]
                for entry in f.outcomes:
                    lines.append(f"- Requirement {entry['index']}: **{entry['status']}** — {entry['summary']}")
                    lines += [f"  - {error}" for error in entry["errors"]]
                lines.append("")
                continue
            fr = f.results[f.stopped_at - 1] if f.stopped_at and f.stopped_at <= len(f.results) else None
            if fr is not None:
                stage_dir = result.stages[f.stopped_at - 1].out_dir.name
                lines += [f"## {i}. {f.source.name} — stopped at stage {f.stopped_at} of {n} "
                          f"({result.stages[f.stopped_at - 1].name}); the stage's own records are in `{stage_dir}/`", "",
                          file_diagnosis(fr, "###"), ""]
            else:
                lines += [f"## {i}. {f.source.name} — {f.category} (`{f.code}`)", "", f"- **Why:** {f.reason}",
                          f"- **What to do:** {f.explanation}", ""]
    path = out / "DIAGNOSTICS.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
