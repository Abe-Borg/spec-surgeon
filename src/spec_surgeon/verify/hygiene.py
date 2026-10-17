"""Independent V10 reader for character hygiene; imports no transform code."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .reader import W
from .edits import check_edited_paragraph, part_revision_ids, v_items, v_run_text
from .props import para_matches
from .template import _v_field_runs


def _derive(text, op, opts):
    edits = {}
    if op == "normalize_whitespace":
        i = 0
        while i < len(text):
            if text[i] != " ":
                i += 1
                continue
            j = i + 1
            while j < len(text) and text[j] == " ":
                j += 1
            new = "" if j == len(text) or text[j] == "\n" else " "
            if text[i:j] != new:
                edits[i, j] = new
            i = j
    elif op == "normalize_punctuation":
        separators = {"space": " ", "hyphen": "-", "en": "–", "em": "—"}
        protected = set()
        if opts.get("section_separator"):
            rx = re.compile(r"(?<![\w.])([0-9]{2})([ \xa0–—-]+)([0-9]{2})([ \xa0–—-]+)([0-9]{2})(?:\.[0-9]{2})?(?!\w|\.\d)")
            for m in rx.finditer(text):
                protected.update(range(m.start(), m.end()))
                for group in (2, 4):
                    edits[m.span(group)] = separators[opts["section_separator"]]
        if opts.get("dash"):
            for m in re.finditer(r"(?<!-)--(?!-)", text):
                if m.start() not in protected:
                    edits[m.span()] = separators[opts["dash"]]
        if opts.get("quotes"):
            for i, char in enumerate(text):
                if char not in "'‘’\"“”":
                    continue
                double = char in '\"“”'
                if opts["quotes"] == "straight":
                    new = '\"' if double else "'"
                elif char in "'\"" and i > 0 and text[i-1].isdigit():
                    continue
                else:
                    left = text[i-1:i] if i else ""
                    opening = left == "" or left.isspace() or left in "([{—–"
                    new = ("“”" if double else "‘’")[0 if opening else 1]
                edits[i, i+1] = new
    elif op == "protect_nonbreaking_spaces":
        ranges = []
        if opts.get("section_numbers", True):
            ranges.extend(m.span() for m in re.finditer(
                r"(?<![\w.])[0-9]{2}[ \xa0]+[0-9]{2}[ \xa0]+[0-9]{2}(?:\.[0-9]{2})?(?!\w|\.\d)", text))
        for unit in opts.get("units", ["psi"]):
            rx = r"(?<![\w.])[-+]?[0-9]+(?:[.,][0-9]+)*[ \xa0]+" + re.escape(unit) + r"(?!\w)"
            ranges.extend(m.span() for m in re.finditer(rx, text))
        for start, end in ranges:
            for i in range(start, end):
                if text[i] == " ":
                    edits[i, i+1] = "\xa0"
    else:
        raise ValueError("unknown character hygiene operation")
    return [(a, b, new, False) for (a, b), new in sorted(edits.items()) if text[a:b] != new]


def _capitalization(ctx, record, errs):
    try:
        core = {k: record[k] for k in ("file", "source_sha256", "part", "paragraph", "start", "before", "after")}
        digest = hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if record["id"] != digest or record["source_sha256"] != hashlib.sha256(ctx.src.data).hexdigest():
            raise ValueError("source or approval identity differs")
        before, after = record["before"], record["after"]
        if (not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", before)
                or not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", after)
                or before.lower() != after.lower() or before == after):
            raise ValueError("an approval must change case only")
        return record
    except (KeyError, TypeError, ValueError) as e:
        errs.append(f"character_hygiene: invalid capitalization approval ({e})")
        return None


def _character_hygiene(ctx, assertion, errs):
    op, opts = assertion["operation"], assertion["options"]
    select = assertion.get("select") or {}
    if any("scope" in source and not source["scope"] for source in (select, opts)):
        errs.append("character_hygiene: empty scope must be refused")
    scope = select.get("scope") or opts.get("scope") or ["document", "headers", "footers"]
    if isinstance(scope, str):
        scope = [scope]
    if list(scope) != assertion["scope"] or bool(opts.get("tracked", True)) != assertion["tracked"]:
        errs.append("character_hygiene: resolved scope or tracking differs from the declared options")
    raw_approvals = []
    if op == "capitalize_defined_terms":
        if ctx.source_name is None:
            errs.append("character_hygiene: source filename is required for capitalization verification")
        else:
            raw_approvals = [r for r in opts.get("approvals", []) if r.get("file") == str(Path(ctx.source_name).resolve())]
        if raw_approvals != assertion.get("approvals", []):
            errs.append("character_hygiene: selected approvals differ from the original declared approvals")
    approvals = [r for raw in raw_approvals if (r := _capitalization(ctx, raw, errs))]
    count, edited, used, precise = 0, {}, [], []
    for part in ctx.scope_parts(scope):
        if part not in ctx.both:
            errs.append(f"character_hygiene: {part} missing from output")
            continue
        source, output = ctx.view(part)
        if len(source.pars) != len(output.pars):
            errs.append(f"character_hygiene: paragraph count differs in {part}")
            continue
        fields, ids = _v_field_runs(source.root), part_revision_ids(source.root)
        for bi, p in enumerate(source.pars):
            if not para_matches(assertion.get("select", {}).get("paragraphs"), p, bi, source, ctx.src_styles):
                continue
            text = v_run_text(v_items(p))
            if op == "capitalize_defined_terms":
                changes = []
                for record in approvals:
                    if (record["part"], record["paragraph"]) != (part, bi):
                        continue
                    a, b = record["start"], record["start"]+len(record["before"])
                    if (text[a:b] != record["before"] or (a and re.match(r"\w", text[a-1]))
                            or (b < len(text) and re.match(r"\w", text[b]))):
                        errs.append("character_hygiene: approved occurrence differs from source")
                        continue
                    for offset in range(len(record["before"])):
                        if record["before"][offset] != record["after"][offset]:
                            changes.append((a+offset, a+offset+1, record["after"][offset], False))
                    used.append(record["id"])
            else:
                changes = _derive(text, op, opts)
            changes.sort(key=lambda e: e[0])
            if changes:
                if any(a.tag == W+"p" for a in p.iterancestors()):
                    errs.append("character_hygiene: edits in text boxes must be refused")
                    continue
                precise += [{"part": part, "paragraph": bi, "start": a, "before": text[a:b], "after": new}
                            for a, b, new, _ in changes]
                count += len(changes)
                edited.setdefault(part, []).append(bi)
                check_edited_paragraph(p, output.pars[bi], changes, assertion["tracked"], assertion["author"], fields, ids,
                                       f"character_hygiene {part} block {bi}", errs)
                ctx.verified_blocks.setdefault(part, {})[bi] = bi
    if op == "capitalize_defined_terms" and sorted(used) != sorted(r["id"] for r in approvals):
        errs.append("character_hygiene: not every approval was used exactly once")
    if count != assertion["expected_edits"] or edited != assertion["edited_blocks"]:
        errs.append("character_hygiene: independently derived edit count or paragraphs differ from prediction")
    if precise != assertion.get("changes"):
        errs.append("character_hygiene: exact predicted character occurrences differ from independent reading")
    return f"character_hygiene: {count} independently checked character change(s)"


HYGIENE_KINDS = {"character_hygiene": _character_hygiene}
