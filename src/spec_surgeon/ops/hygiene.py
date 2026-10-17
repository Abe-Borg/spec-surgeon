"""Character-only edits (items 41–44). Predictions come from source run text;
verify/hygiene.py independently derives the same edits. No regex replacement words.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..ooxml import RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id, paragraphs
from . import register
from .base import JobException, Op
from .replace_table import COMMON_PARAMS, DEFAULT_SCOPE, SELECT_SCHEMA, default_author, resolve_scope, tracked_of
from .selectors import StyleSheet, check_paragraph_selector, paragraph_selected, scope_parts
from .slots import field_runs
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph

SECTION = r"(?<![\w.])[0-9]{2}[ \u00a0–—-]+[0-9]{2}[ \u00a0–—-]+[0-9]{2}(?:\.[0-9]{2})?(?!\w|\.\d)"
SEPARATORS = {"space": " ", "hyphen": "-", "en": "–", "em": "—"}


def proposal_id(record):
    core = {k: record[k] for k in ("file", "source_sha256", "part", "paragraph", "start", "before", "after")}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def approval_source(record):
    return "approved-capitalization:" + proposal_id(record)


def propose_defined_terms(pkg, terms, scope=None):
    """Candidates, never approvals. Context is shown by the app before a user picks one."""
    if not isinstance(terms, list) or not terms or any(
            not isinstance(t, str) or not re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", t) for t in terms):
        raise JobException("terms must be a non-empty list of defined terms, e.g. Owner and Work", "bad-params")
    if len({t.casefold() for t in terms}) != len(terms):
        raise JobException("terms must not name the same term twice", "bad-params")
    out = []
    for part in scope_parts(pkg, scope or DEFAULT_SCOPE):
        for bi, p in enumerate(paragraphs(pkg.xml(part))):
            text = RunText(p).text
            if nearest_p(p) is not None:
                continue
            for term in terms:
                for m in re.finditer(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I):
                    if m.group() == term:
                        continue
                    record = {"file": str(Path(pkg.name).resolve()), "source_sha256": hashlib.sha256(pkg.data).hexdigest(),
                              "part": part, "paragraph": bi, "start": m.start(), "before": m.group(), "after": term}
                    record["id"] = proposal_id(record)
                    record["context"] = text[max(0, m.start()-70):m.end()+70]
                    out.append(record)
    return out


def character_edits(text, operation, options):
    """A list of disjoint source spans; only spaces, punctuation, or approved case changes."""
    changes = []
    if operation == "normalize_whitespace":
        for m in re.finditer(r" {2,}| +(?=\n|$)", text):
            trailing = m.end() == len(text) or text[m.end():m.end()+1] == "\n"
            changes.append(Edit(m.start(), m.end(), "" if trailing else " "))
    elif operation == "normalize_punctuation":
        section_spans = []
        if options.get("section_separator"):
            for m in re.finditer(SECTION, text):
                section_spans.append(m.span())
                for gap in re.finditer(r"[ \u00a0–—-]+", m.group()):
                    changes.append(Edit(m.start()+gap.start(), m.start()+gap.end(),
                                        SEPARATORS[options["section_separator"]]))
        if options.get("dash"):
            for m in re.finditer(r"(?<!-)--(?!-)", text):
                if not any(a <= m.start() < b for a, b in section_spans):
                    changes.append(Edit(m.start(), m.end(), SEPARATORS[options["dash"]]))
        quote_style = options.get("quotes")
        for i, ch in enumerate(text):
            if not quote_style or ch not in "\"'“”‘’":
                continue
            if quote_style == "straight":
                new = '"' if ch in '"“”' else "'"
            else:
                # Numeric inch/foot marks are left for review, rather than guessed to be quotations.
                if ch in "\"'" and i and text[i-1].isdigit():
                    continue
                prev = text[i-1] if i else ""
                opening = not prev or prev.isspace() or prev in "([{—–"
                new = ("“" if opening else "”") if ch in '"“”' else ("‘" if opening else "’")
            changes.append(Edit(i, i+1, new))
    elif operation == "protect_nonbreaking_spaces":
        spans = []
        if options.get("section_numbers", True):
            spans += [m.span() for m in re.finditer(
                r"(?<![\w.])[0-9]{2}[ \u00a0]+[0-9]{2}[ \u00a0]+[0-9]{2}(?:\.[0-9]{2})?(?!\w|\.\d)", text)]
        for unit in options.get("units", ["psi"]):
            spans += [m.span() for m in re.finditer(
                r"(?<![\w.])[-+]?[0-9]+(?:[.,][0-9]+)*[ \u00a0]+" + re.escape(unit) + r"(?!\w)", text)]
        spaces = {i for a, b in spans for i in range(a, b) if text[i] == " "}
        changes += [Edit(i, i+1, "\u00a0") for i in sorted(spaces)]
    return sorted([e for e in changes if text[e.start:e.end] != e.text], key=lambda e: e.start)


class _Hygiene(Op):
    mode = "content"
    select_schema = SELECT_SCHEMA
    verification_profile = {
        "V2": "Every changed paragraph and its exact resulting characters predicted from the source.",
        "V4/V5b/V8": "Tracked revision counts, reject-all identity and accept-all text, or exact untracked diffs.",
        "V10": "character_hygiene independently re-derives every edit and checks the whole paragraph item model.",
    }

    def plan(self, pkg, select, params):
        unknown = set(params) - set(self.params_schema["properties"])
        if unknown or set(select) - {"scope", "paragraphs"}:
            raise JobException(f"{self.name}: unknown parameter or selector {sorted(unknown | (set(select)-{'scope', 'paragraphs'}))}", "bad-params")
        if any("scope" in source and not source["scope"] for source in (select, params)):
            raise JobException("scope must name at least one part kind", "bad-params")
        check_paragraph_selector(select.get("paragraphs"))
        self.validate(params)
        scope = resolve_scope(select, params, DEFAULT_SCOPE)
        tracked, author = tracked_of(params), params.get("author") or default_author()
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("invalid date_convention", "bad-params")
        frag = self.fragment(select, params, mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        data, total, precise = {}, 0, []
        approvals = []
        if self.name == "capitalize_defined_terms":
            for record in params["approvals"]:
                if record["file"] != str(Path(pkg.name).resolve()):
                    continue
                if record["source_sha256"] != hashlib.sha256(pkg.data).hexdigest():
                    raise JobException("capitalization approval is stale; propose and review again", "bad-params")
                approvals.append(record)
        seen_approvals = set()
        sheet = StyleSheet(pkg)
        for part in scope_parts(pkg, scope):
            frag.touches.append((part, "scope", "verify"))
            root = pkg.xml(part)
            pars, fields = paragraphs(root), field_runs(root)
            for bi, p in enumerate(pars):
                if not paragraph_selected(select.get("paragraphs"), p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                text = RunText(p).text
                if self.name == "capitalize_defined_terms":
                    edits = []
                    for r in approvals:
                        if (r["part"], r["paragraph"]) == (part, bi):
                            a, b = r["start"], r["start"] + len(r["before"])
                            if text[a:b] != r["before"] or (a and (text[a-1].isalnum() or text[a-1] == "_")) or (
                                    b < len(text) and (text[b].isalnum() or text[b] == "_")):
                                raise JobException("capitalization approval does not match the exact source occurrence", "bad-params")
                            edits.extend(Edit(a+i, a+i+1, after) for i, (before, after) in enumerate(
                                zip(r["before"], r["after"])) if before != after)
                            seen_approvals.add(r["id"])
                else:
                    edits = character_edits(text, self.name, params)
                if not edits:
                    continue
                if nearest_p(p) is not None:
                    raise JobException("a hygiene edit is inside a text box; review it in Word", "textbox")
                done = plan_paragraph(frag, part, bi, p, edits, tracked, fields, f"{part} paragraph {bi}")
                data.setdefault(part, {})[str(bi)] = edits_to_data(done)
                precise += [{"part": part, "paragraph": bi, "start": e.start, "before": text[e.start:e.end],
                             "after": e.text} for e in done]
                total += len(done)
        if seen_approvals != {r["id"] for r in approvals}:
            raise JobException("an approved capitalization occurrence is outside the selected scope", "bad-params")
        if not total and not params.get("allow_missing", True):
            raise JobException("no character changes found", "not-found")
        frag.property_assertions.append({"kind": "character_hygiene", "operation": self.name, "options": params,
                                         "scope": scope, "select": select, "tracked": tracked, "author": author,
                                         "approvals": approvals, "expected_edits": total,
                                         "changes": precise,
                                         "edited_blocks": {p: sorted(map(int, bs)) for p, bs in data.items()}})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(frag.rev_delta.get(k, 0) for k in ("ins", "del"))
        frag.transform_data = {"edits": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z"), "character_changes": precise}
        frag.notes.append(f"{self.name}: {total} character change(s); exact before/after occurrences in character_changes.")
        if self.name == "normalize_punctuation" and params.get("quotes") == "curly":
            frag.notes.append("Straight quotes after digits are retained as possible inch/foot marks; review them manually.")
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td["date_convention"])
        next_id = [td["first_rev_id"]]
        for part, edits in td["edits"].items():
            root = pkg.xml(part)
            apply_part_edits(root, edits, td["tracked"], stamp, next_id)
            pkg.write_xml(part, root)

    def validate(self, params):
        pass


BASE_PARAMS = {**COMMON_PARAMS, "allow_missing": {"type": "boolean", "default": True}}


@register
class NormalizeWhitespace(_Hygiene):
    name = "normalize_whitespace"
    description = "Collapse repeated ordinary spaces and remove trailing ordinary spaces; no words, tabs or non-breaking spaces change."
    params_schema = {"type": "object", "properties": BASE_PARAMS}


@register
class NormalizePunctuation(_Hygiene):
    name = "normalize_punctuation"
    description = "Use straight or curly quotes, turn -- into a chosen dash, and standardize section-number separators."
    params_schema = {"type": "object", "properties": {
        **BASE_PARAMS, "quotes": {"enum": ["straight", "curly"]}, "dash": {"enum": ["en", "em"]},
        "section_separator": {"enum": list(SEPARATORS)}}}

    def validate(self, params):
        if not any(k in params for k in ("quotes", "dash", "section_separator")):
            raise JobException("choose quotes, dash and/or section_separator", "bad-params")
        for key, allowed in (("quotes", ("straight", "curly")), ("dash", ("en", "em")),
                             ("section_separator", SEPARATORS)):
            if key in params and params[key] not in allowed:
                raise JobException(f"invalid {key}", "bad-params")


@register
class ProtectNonbreakingSpaces(_Hygiene):
    name = "protect_nonbreaking_spaces"
    description = "Keep numbers and named units (default psi), and spaced section numbers, together using non-breaking spaces."
    params_schema = {"type": "object", "properties": {
        **BASE_PARAMS, "units": {"type": "array", "items": {"type": "string"}, "default": ["psi"]},
        "section_numbers": {"type": "boolean", "default": True}}}

    def validate(self, params):
        units = params.get("units", ["psi"])
        if not isinstance(units, list) or any(not isinstance(u, str) or not re.fullmatch(r"[A-Za-z°%]+(?:/[A-Za-z]+)?", u) for u in units):
            raise JobException("units must be a list of unit symbols", "bad-params")
        if type(params.get("section_numbers", True)) is not bool:
            raise JobException("section_numbers must be true or false", "bad-params")


@register
class CapitalizeDefinedTerms(_Hygiene):
    name = "capitalize_defined_terms"
    description = "Apply only individually reviewed, source-bound defined-term case changes; never a global capitalization guess."
    params_schema = {"type": "object", "properties": {
        **BASE_PARAMS, "approvals": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {
            **{key: {"type": "string"} for key in ("file", "source_sha256", "part", "before", "after", "id", "context")},
            "paragraph": {"type": "integer"}, "start": {"type": "integer"}}},
                                    "description": "Exact proposal records individually picked by the user; created by propose_defined_terms."}},
                     "required": ["approvals"]}

    def validate(self, params):
        records = params.get("approvals")
        if not isinstance(records, list) or not records:
            raise JobException("individual capitalization approvals are required", "bad-params")
        ids = []
        for r in records:
            try:
                valid = (isinstance(r, dict) and isinstance(r["file"], str) and isinstance(r["part"], str)
                         and re.fullmatch(r"[0-9a-f]{64}", r["source_sha256"])
                         and type(r["paragraph"]) is int and r["paragraph"] >= 0
                         and type(r["start"]) is int and r["start"] >= 0
                         and isinstance(r["before"], str) and isinstance(r["after"], str)
                         and re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", r["before"])
                         and re.fullmatch(r"[A-Za-z]+(?: [A-Za-z]+)*", r["after"])
                         and r["before"] != r["after"] and r["before"].lower() == r["after"].lower()
                         and r["id"] == proposal_id(r))
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise JobException("invalid source-bound capitalization approval", "bad-params")
            ids.append(r["id"])
        if len(ids) != len(set(ids)):
            raise JobException("duplicate capitalization approval", "bad-params")
