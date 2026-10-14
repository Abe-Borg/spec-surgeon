"""Batch report and set-aside manifest — the durable audit artifacts at batch level. Per-file records live in records/."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .batch import BatchResult

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
    "pattern": (
        "Different Document Structure Pattern",
        "This file has a different structural pattern from the pattern(s) the EditSpec targeted.",
    ),
    "crash": (
        "Unreadable or Corrupt .docx File",
        "The file could not be opened as a valid Word document (corrupt zip or invalid XML).",
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
        if f.exception:
            code = f.exception.get("code", "exception")
            reason = f.exception.get("reason", "")
            details = f.exception.get("details", {})
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
            lines.append(f"  - diagnosis: `exceptions/{f.source.stem}.exception.json`")
        lines.append("")
    failed = [f for f in batch.files if f.record and not f.record.passed]
    if failed:
        lines.append("## Failed verification (NOT delivered)")
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
    lines.append(f"- Per-file verification records: `records/<name>.record.md` (the enumerated diff list and every check with its numbers)")
    if manifest["files"]:
        lines.append(f"- Set-aside manifest for next pass: `set_aside.json` and `SET_ASIDE_REPORT.md`")
    if batch.contact_sheet:
        lines.append(f"- First pages of every output: `{batch.contact_sheet.relative_to(out)}`")
    lines.append("- Rendered pages that changed: `render/pages/`")
    lines.append("- Outputs: `outputs/` (or written back in place when run with `--in-place`)")
    (out / "BATCH_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    (out / "batch.json").write_text(json.dumps({
        "job": batch.job, "params": batch.params, "spec": batch.spec, "renderer": batch.renderer, "timings": batch.timings,
        "counts": c,
        "set_aside_count": manifest["set_aside_count"],
        "files": [{"source": str(f.source), "status": f.status, "pattern_id": f.fingerprint.get("pattern_id"),
                   "output": str(f.output) if f.output else None, "delivered_to": f.delivered_to,
                   "exception": f.exception, "timings": f.timings} for f in batch.files],
    }, indent=2), encoding="utf-8")
    return out / "BATCH_REPORT.md"
