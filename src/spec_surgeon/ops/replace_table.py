"""Ops: replace_table — a table of find → replace pairs (typed or from a file) in one pass — and set_issue_stamp — the
issue stamp wherever it sits — on the in-paragraph edit core (ops/textedit.py). Design note §2 and §5; the verifier
re-finds every match with its own code (verify/textops.py, kind ``replaced_text``).

Matching (CLAUDE.md "Replacement pairs", both sides): every pair is searched in the SOURCE run text of every paragraph
in scope (simultaneously — pairs never see each other's results, so "Owner" → "Client" and "Client" → "Owner" swap);
a pair is a literal (``re.escape``; with ``flexible_whitespace`` spaces match any run of spaces/tabs/no-break spaces and
hyphens any dash) or a Python regex (``regex: true``), case-sensitive unless ``match_case: false``; ``finditer``, empty
matches skipped; with ``whole_word`` (default true) a match whose first (last) character is a letter, digit or ``_``
must not have one right before (after) it. Matches of different pairs that overlap are refused (``ambiguous``). The
*reader view* (run text with tabs and line breaks as spaces) must not hold more matches of a pair than the run text —
an occurrence split by a tab or a line break is refused (``spans-structure``), never silently skipped.
"""
from __future__ import annotations

import getpass
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ooxml import block_full_text, file_uses_date_utc, flexible_regex_from_literal, max_revision_id, paragraphs
from ..ooxml import RevisionStamp
from ..package import DocxPackage
from . import register
from . import tables
from .base import JobException, Op, PlanFragment
from .selectors import SCOPE_KINDS, StyleSheet, check_paragraph_selector, paragraph_selected, scope_parts
from .slots import field_runs
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph, shown

PAIR_KEYS = ("find", "replace", "regex", "match_case", "whole_word", "flexible_whitespace")
DEFAULT_SCOPE = ["document", "headers", "footers"]


def default_author() -> str:
    return os.environ.get("SPEC_SURGEON_AUTHOR") or getpass.getuser()


def normalize_pair(raw: Any, where: str) -> Dict[str, Any]:
    """A pair with every option spelled out (the defaults are part of the definition)."""
    if not isinstance(raw, dict):
        raise JobException(f"{where}: a pair is an object {{find, replace, …}}", "bad-params")
    unknown = set(raw) - set(PAIR_KEYS)
    if unknown:
        raise JobException(f"{where}: unknown key(s) {sorted(unknown)} (known: {list(PAIR_KEYS)})", "bad-params")
    find, rep = raw.get("find"), raw.get("replace")
    if not isinstance(find, str) or not find:
        raise JobException(f"{where}: 'find' must be non-empty text", "bad-params")
    if not isinstance(rep, str):
        raise JobException(f"{where}: 'replace' must be text (\"\" deletes)", "bad-params")
    for k in ("regex", "match_case", "whole_word", "flexible_whitespace"):
        if k in raw and not isinstance(raw[k], bool):
            raise JobException(f"{where}: {k!r} must be true or false", "bad-params")
    if any(ord(c) < 32 or c == "\x7f" for c in find + rep):
        raise JobException(f"{where}: a control character (tab, line break …) in {find!r} → {rep!r}", "bad-params")
    pair = {"find": find, "replace": rep, "regex": bool(raw.get("regex", False)),
            "match_case": bool(raw.get("match_case", True)), "whole_word": bool(raw.get("whole_word", True)),
            "flexible_whitespace": bool(raw.get("flexible_whitespace", False))}
    try:
        compile_pair(pair)
    except re.error as e:
        raise JobException(f"{where}: bad regular expression {find!r} ({e})", "bad-params")
    return pair


def compile_pair(pair: Dict[str, Any]):
    if pair["regex"]:
        pattern = pair["find"]
    elif pair["flexible_whitespace"]:
        pattern = flexible_regex_from_literal(pair["find"])
    else:
        pattern = re.escape(pair["find"])
    return re.compile(pattern, 0 if pair["match_case"] else re.IGNORECASE)


def _wordch(c: str) -> bool:
    return c.isalnum() or c == "_"


def whole_word_ok(text: str, a: int, b: int) -> bool:
    if a > 0 and _wordch(text[a]) and _wordch(text[a - 1]):
        return False
    if b < len(text) and _wordch(text[b - 1]) and _wordch(text[b]):
        return False
    return True


def pair_matches(text: str, rx, pair: Dict[str, Any]) -> List[Tuple[int, int]]:
    return [(m.start(), m.end()) for m in rx.finditer(text)
            if m.end() > m.start() and (not pair["whole_word"] or whole_word_ok(text, m.start(), m.end()))]


def reader_view(text: str) -> str:
    return text.replace("\t", " ").replace("\n", " ")


def find_all(text: str, pairs: Sequence[Dict[str, Any]], compiled: Sequence[Any], where: str) -> List[Tuple[int, int, int]]:
    """[(start, end, pair index)] in run-text order, after the overlap and near-miss refusals."""
    found = []
    reader = reader_view(text)
    for k, (pair, rx) in enumerate(zip(pairs, compiled)):
        hits = pair_matches(text, rx, pair)
        if len(pair_matches(reader, rx, pair)) > len(hits):
            raise JobException(f"{where}: {pair['find']!r} is split by a tab or a line break in {shown(text)!r}; that "
                               f"occurrence cannot be replaced as text — change it in Word", "spans-structure",
                               {"where": where, "find": pair["find"], "text": shown(text)})
        found += [(a, b, k) for a, b in hits]
    found.sort()
    for (a1, b1, k1), (a2, b2, k2) in zip(found, found[1:]):
        if a2 < b1:
            raise JobException(f"{where}: {pairs[k1]['find']!r} and {pairs[k2]['find']!r} both match "
                               f"{shown(text[a1:max(b1, b2)])!r}; say which one applies", "ambiguous",
                               {"where": where, "text": shown(text)})
    return found


def resolve_scope(select: Dict[str, Any], params: Dict[str, Any], default: List[str]) -> List[str]:
    scope = select.get("scope") or params.get("scope") or default
    if isinstance(scope, str):
        scope = [scope]
    if select.get("scope") and params.get("scope") and list(select["scope"]) != list(params["scope"]):
        raise JobException("select.scope and params.scope disagree", "bad-params")
    return list(scope)


def tracked_of(params: Dict[str, Any]) -> bool:
    t = params.get("tracked", True)
    if not isinstance(t, bool):
        raise JobException("'tracked' must be true or false", "bad-params")
    return t


class _Replacing(Op):
    """What replace_table and set_issue_stamp share: find the pairs, plan each paragraph's edits, apply them."""
    mode = "content"
    verification_profile = {
        "V2": "one BlockDiff per edited paragraph (tracked: the old text stays as w:delText, the new follows)",
        "V4": "tracked: +1 del per run a match touches, +1 ins per non-empty replacement",
        "V5b": "tracked: reject-all view unchanged; untracked: enumerated-list mode",
        "V8": "tracked: accept-all view of every edited paragraph predicted",
        "V10": "replaced_text re-finds every match with the verifier's own matcher and checks every edited paragraph "
               "item by item (verify/edits.py); nothing else changes (residual identity)",
    }

    def _plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any], typed: List[Dict[str, Any]],
              table: Optional[Dict[str, Any]], table_pairs: List[Dict[str, Any]], require: str,
              default_scope: List[str]) -> PlanFragment:
        pairs = typed + table_pairs
        select = dict(select or {})
        unknown = set(select) - {"scope", "paragraphs"}
        if unknown:
            raise JobException(f"{self.name} selects by scope/paragraphs only; unknown {sorted(unknown)}", "bad-params")
        pred = select.get("paragraphs")
        check_paragraph_selector(pred)
        scope = resolve_scope(select, params, default_scope)
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        author = params.get("author") or default_author()
        seen = {}
        for p in pairs:
            key = (p["find"], p["regex"], p["match_case"])
            if key in seen:
                raise JobException(f"{p['find']!r} is listed twice", "bad-params", {"find": p["find"]})
            seen[key] = p
        if not pairs:
            raise JobException(f"{self.name}: nothing to find", "bad-params")
        sel = {"scope": scope, **({"paragraphs": pred} if pred not in (None, "all") else {})}
        frag = self.fragment(sel, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        compiled = [compile_pair(p) for p in pairs]
        sheet = StyleSheet(pkg) if pred not in (None, "all") else None
        data: Dict[str, Dict[str, List[List[Any]]]] = {}
        total, per_pair = 0, [0] * len(pairs)
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            in_field = field_runs(root)
            for bi, p in enumerate(pars):
                if sheet is not None and not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                where = f"{part} paragraph {bi}"
                rt = RunText(p)
                found = find_all(rt.text, pairs, compiled, where)
                if not found:
                    continue
                if nearest_p(p) is not None:
                    raise JobException(f"{where} is inside a text box (Word keeps two copies of it); not changed",
                                       "textbox", {"where": where, "text": shown(rt.text)})
                edits = [Edit(a, b, pairs[k]["replace"]) for a, b, k in found]
                done = plan_paragraph(frag, part, bi, p, edits, tracked, in_field, where)
                if done:
                    data.setdefault(part, {})[str(bi)] = edits_to_data(done)
                    total += len(done)
                    for a, b, k in found:
                        per_pair[k] += 1
        # the typed pairs and the table FILE (its md5): the verifier reads the table's rows again with its own reader
        assertion = {"kind": "replaced_text", "scope": scope, "select": sel, "pairs": [dict(p) for p in typed],
                     "table": table, "tracked": tracked, "author": author, "expected_edits": total,
                     "edited_blocks": {part: sorted(int(b) for b in blocks) for part, blocks in data.items()}}
        frag.property_assertions.append(assertion)
        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append(f"{self.name}: nothing to replace in {scope}; no change.")
                return frag
            raise JobException(f"{self.name}: none of {[p['find'] for p in pairs][:6]} occurs in {scope}", "not-found",
                               {"scope": scope, "finds": [p["find"] for p in pairs][:20]})
        if require == "each":
            missing = [p["find"] for p, n in zip(pairs, per_pair) if n == 0]
            if missing:
                frag.notes.append(f"not found in this file: {missing[:10]}")
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = frag.rev_delta.get("ins", 0) + frag.rev_delta.get("del", 0)
        frag.transform_data = {"edits": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z"), "table": table}
        frag.notes.append(f"{self.name}: {total} replacement(s) in {len(data)} part(s) "
                          f"({'tracked' if tracked else 'untracked'}): "
                          + "; ".join(f"{p['find']!r} → {p['replace']!r} ×{n}" for p, n in zip(pairs, per_pair) if n)[:900])
        if not tracked:
            frag.flags.append("UNTRACKED wording change at the user's request: no revision marks; V2's list is the proof.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("edits"):
            return
        tables.check_unchanged(td.get("table"), self.name)
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, by_block in td["edits"].items():
            root = pkg.xml(part)
            apply_part_edits(root, by_block, td["tracked"], stamp, next_id)
            pkg.write_xml(part, root)


COMMON_PARAMS = {
    "tracked": {"type": "boolean", "default": True,
                "description": "true = tracked change (content mode); false = untracked (clean copies)."},
    "author": {"type": "string", "description": "Tracked-change author (default: SPEC_SURGEON_AUTHOR or the OS user)."},
    "allow_missing": {"type": "boolean", "default": False,
                      "description": "true = a file with nothing to change is a no-op instead of an exception."},
    "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
    "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
              "description": "part kinds (default: document, headers, footers); same as select.scope"},
}
SELECT_SCHEMA = {
    "type": "object",
    "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document, headers, footers)"},
        "paragraphs": {"type": ["string", "object"], "description": "optional paragraph selector to narrow it"},
    },
}
PAIR_SCHEMA = {
    "type": "object",
    "properties": {
        "find": {"type": "string", "description": "the text (or, with regex, the Python regex) to find"},
        "replace": {"type": "string", "description": "the user's approved replacement, verbatim (\"\" deletes)"},
        "regex": {"type": "boolean", "default": False},
        "match_case": {"type": "boolean", "default": True},
        "whole_word": {"type": "boolean", "default": True,
                       "description": "a match may not start or end inside a word (Owner never hits Ownership)"},
        "flexible_whitespace": {"type": "boolean", "default": False,
                                "description": "spaces match any run of spaces/no-break spaces, hyphens any dash"},
    },
}
TABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {"type": "string", "description": "a .json, .csv or .xlsx file the user keeps (absolute path)"},
        "sheet": {"type": "string", "description": "xlsx: the sheet's name (default: the first)"},
        "key": {"type": "string", "description": "json: the dotted key of the object/list (default: the whole file)"},
        "find_column": {"type": "string", "description": "csv/xlsx: the header of the find column (default: column 1)"},
        "replace_column": {"type": "string",
                           "description": "csv/xlsx: the header of the replacement column (default: column 2)"},
    },
}


@register
class ReplaceTable(_Replacing):
    name = "replace_table"
    description = ("Apply a table of find → replace pairs in one pass (project name, number, owner, terminology): "
                   "typed pairs and/or a table file the user keeps (.json/.csv/.xlsx); all pairs found at once in the "
                   "source, whole words and exact case by default; tracked (default) or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "pairs": {"type": "array", "items": PAIR_SCHEMA,
                      "description": "typed pairs; each 'replace' must be the user's own words"},
            "pairs_table": TABLE_SCHEMA,
            **COMMON_PARAMS,
        },
    }
    select_schema = SELECT_SCHEMA

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw = params.get("pairs") or []
        if not isinstance(raw, list):
            raise JobException("'pairs' must be a list", "bad-params")
        typed = [normalize_pair(p, f"pair {n}") for n, p in enumerate(raw, 1)]
        table, from_table = None, []
        spec = params.get("pairs_table")
        if spec is not None:
            got, path, md5 = tables.pairs(spec)
            table = {**spec, "file": path, "md5": md5}
            from_table = [normalize_pair({"find": f, "replace": r}, f"table pair {n}") for n, (f, r) in enumerate(got, 1)]
        return self._plan(pkg, select, params, typed, table, from_table, "each", DEFAULT_SCOPE)


@register
class SetIssueStamp(_Replacing):
    name = "set_issue_stamp"
    description = ("Replace the issue stamp wherever it sits — the cover, a header or a footer, which varies by file — "
                   "with the user's new wording: the stamp is a Python regex ('pattern') or a list of exact phrases "
                   "('any_of'); at least one per file unless allow_missing.")
    params_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "a Python regex matching the stamp as it reads now"},
            "any_of": {"type": "array", "items": {"type": "string"},
                       "description": "the stamp's possible current wordings, exactly as they read"},
            "text": {"type": "string", "description": "the new stamp: the user's own words, verbatim"},
            "match_case": {"type": "boolean", "default": True},
            **COMMON_PARAMS,
        },
        "required": ["text"],
    }
    select_schema = SELECT_SCHEMA

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise JobException("'text' (the new stamp) must be non-empty", "bad-params")
        has_p, has_a = params.get("pattern") is not None, params.get("any_of") is not None
        if has_p == has_a:
            raise JobException("give the current stamp as 'pattern' (a regex) or 'any_of' (exact phrases), one of them",
                               "bad-params")
        mc = params.get("match_case", True)
        if has_p:
            raw = [{"find": params["pattern"], "replace": text, "regex": True, "match_case": mc}]
        else:
            if not isinstance(params["any_of"], list) or not params["any_of"]:
                raise JobException("'any_of' must be a non-empty list of phrases", "bad-params")
            raw = [{"find": s, "replace": text, "match_case": mc} for s in params["any_of"]]
        pairs = [normalize_pair(p, "stamp") for p in raw]
        return self._plan(pkg, select, params, pairs, None, [], "any", DEFAULT_SCOPE)
