"""Op: resolve_options — choose among MasterSpec-style ``[option] [option]`` choices by the user's rules ("pick preaction
wherever offered") and drop the brackets (design note §3). Deletion only: the kept words are already in the document.

Grammar (CLAUDE.md "Option groups", both sides; over run text — ops/textedit.py):
* an **option** is ``[`` + its text + ``]``: the text holds no ``[``, ``]``, tab, line break or ``\\x00`` and at least
  one character that is not a space;
* a **group** is a maximal run of options separated only by spaces / no-break spaces;
* a group is **nested** when a ``[`` before it in the paragraph is still open where it starts.

Rules ``{match, action, match_case?}`` (``match``: a Python regex searched in an option's text, case-insensitive unless
``match_case``): for each group the FIRST rule whose ``match`` finds some option decides —
* ``pick``: exactly one option may match (else ``ambiguous``); the group becomes that option's text — everything else in
  the group (the other options, the separators, the brackets) is deleted;
* ``drop``: the matching options go. All of them: the group and the spaces right after it — or, when none follow it, the
  spaces right before it — are deleted. Some of them: each dropped option goes with the separator after it, or — when it
  comes after every kept option — with the separator before it; the kept options keep their brackets.
Groups no rule touches are left alone. A nested group a rule touches is refused (``nested-options``). The verifier
re-parses the groups and re-applies the rules (verify/textops.py, kind ``resolved_options``).
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ooxml import RevisionStamp, block_full_text, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage
from . import prose, register
from .base import JobException, Op, PlanFragment
from .patterns import compile_pattern
from .replace_table import COMMON_PARAMS, SELECT_SCHEMA, default_author, resolve_scope, tracked_of
from .selectors import StyleSheet, check_paragraph_selector, paragraph_selected, scope_parts
from .slots import field_runs
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph, shown

OPTION = r"\[[^\[\]\x00\t\n]*[^\[\]\x00\t\n\s ][^\[\]\x00\t\n]*\]"
GROUP = re.compile(OPTION + r"(?:[  ]+" + OPTION + r")*")
OPTION_RX = re.compile(OPTION)
SPACES = "  "
ACTIONS = ("pick", "drop")


def compile_rules(raw: Any) -> List[Tuple[Any, str, str]]:
    """[(regex, action, the rule's own text for messages)] — refused when malformed."""
    if not isinstance(raw, list) or not raw:
        raise JobException("'rules' must be a non-empty list of {match, action}", "bad-params")
    out = []
    for n, r in enumerate(raw, 1):
        if not isinstance(r, dict) or set(r) - {"match", "action", "match_case"}:
            raise JobException(f"rule {n} must be {{match, action, match_case?}}", "bad-params")
        if r.get("action") not in ACTIONS:
            raise JobException(f"rule {n}: action must be one of {list(ACTIONS)}", "bad-params")
        if not isinstance(r.get("match"), str) or not r["match"].strip():
            raise JobException(f"rule {n}: 'match' must be a regex", "bad-params")
        if "match_case" in r and not isinstance(r["match_case"], bool):
            raise JobException(f"rule {n}: match_case must be true or false", "bad-params")
        try:
            rx = compile_pattern(r["match"], 0 if r.get("match_case") else re.IGNORECASE, where="an option's text")
        except JobException as e:
            raise JobException(f"rule {n}: {e.reason}", e.code, e.details) from None
        out.append((rx, r["action"], f"{r['action']} {r['match']!r}"))
    return out


def open_depth(text: str) -> List[int]:
    """The number of '[' still open before each position (']' closes one; never below zero)."""
    out, depth = [], 0
    for ch in text:
        out.append(depth)
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
    out.append(depth)
    return out


def groups_of(text: str) -> List[Tuple[int, int, List[Tuple[int, int]]]]:
    """[(start, end, [(option start, option end)])] in run-text order."""
    out = []
    for g in GROUP.finditer(text):
        opts = [(g.start() + m.start(), g.start() + m.end()) for m in OPTION_RX.finditer(g.group(0))]
        out.append((g.start(), g.end(), opts))
    return out


def group_deletions(text: str, start: int, end: int, opts: List[Tuple[int, int]], rules, where: str,
                    nested: bool) -> Tuple[List[Tuple[int, int]], Optional[str]]:
    """(deletions, the rule that decided) for one group — ([], None) when no rule touches it."""
    for rx, action, label in rules:
        hits = [k for k, (a, b) in enumerate(opts) if rx.search(text[a + 1:b - 1])]
        if not hits:
            continue
        if nested:
            raise JobException(f"{where}: {shown(text[start:end])!r} sits inside another bracket — a nested choice is "
                               f"resolved in Word", "nested-options", {"where": where, "text": shown(text)})
        if action == "pick":
            if len(hits) != 1:
                raise JobException(f"{where}: {label} matches {len(hits)} options of "
                                   f"{shown(text[start:end])!r}; make the rule say which", "ambiguous",
                                   {"where": where, "text": shown(text)})
            a, b = opts[hits[0]]
            return [(start, a + 1), (b - 1, end)], label
        if len(hits) == len(opts):
            n_after = 0
            while end + n_after < len(text) and text[end + n_after] in SPACES:
                n_after += 1
            if n_after:
                return [(start, end + n_after)], label
            n_before = 0
            while start - n_before - 1 >= 0 and text[start - n_before - 1] in SPACES:
                n_before += 1
            return [(start - n_before, end)], label
        kept = [k for k in range(len(opts)) if k not in hits]
        last_kept = max(kept)
        dels = []
        for k in hits:
            if k < last_kept:
                dels.append((opts[k][0], opts[k + 1][0]))
            else:
                dels.append((opts[k - 1][1], opts[k][1]))
        return dels, label
    return [], None


@register
class ResolveOptions(Op):
    name = "resolve_options"
    mode = "content"
    description = ("Resolve MasterSpec-style [option] [option] choices by rules — 'pick' keeps the one option that "
                   "matches (wherever offered) and drops the rest and the brackets; 'drop' removes matching options "
                   "(a whole optional [text] with its space). Deletion only: the kept words are the document's own. "
                   "Tracked (default) or untracked.")
    params_schema = {
        "type": "object",
        "properties": {
            "rules": {"type": "array", "items": {"type": "object", "properties": {
                "match": {"type": "string", "description": "a Python regex searched in each option's text"},
                "action": {"type": "string", "enum": list(ACTIONS)},
                "match_case": {"type": "boolean", "default": False}}},
                "description": "in order; for each [..] [..] group the first rule matching one of its options decides"},
            **{k: v for k, v in COMMON_PARAMS.items()},
        },
        "required": ["rules"],
    }
    select_schema = SELECT_SCHEMA
    verification_profile = {
        "V2": "untracked: one BlockDiff per resolved paragraph; tracked: the block text is unchanged (the deleted options "
              "stay as w:delText) and the accepted view is predicted",
        "V4": "tracked: +1 del per run a deletion touches",
        "V10": "resolved_options re-parses every group and re-applies the rules; every resolved paragraph is checked item "
               "by item",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        rules = compile_rules(params.get("rules"))
        select = dict(select or {})
        if set(select) - {"scope", "paragraphs"}:
            raise JobException(f"unknown selector key(s) {sorted(set(select) - {'scope', 'paragraphs'})}", "bad-params")
        pred = select.get("paragraphs")
        check_paragraph_selector(pred)
        scope = resolve_scope(select, params, ["document"])
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        author = params.get("author") or default_author()
        sel = {"scope": scope, **({"paragraphs": pred} if pred not in (None, "all") else {})}
        frag = self.fragment(sel, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        sheet = StyleSheet(pkg) if pred not in (None, "all") else None
        data: Dict[str, Dict[str, List[List[Any]]]] = {}
        decided: Dict[str, int] = {}
        untouched = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            in_field = field_runs(root)
            for bi, p in enumerate(pars):
                if sheet is not None and not paragraph_selected(pred, p, bi, block_full_text(p), sheet, all_pars=pars):
                    continue
                rt = RunText(p)
                where = f"{part} paragraph {bi}"
                depth = open_depth(rt.text)
                dels: List[Tuple[int, int]] = []
                for start, end, opts in groups_of(rt.text):
                    d, label = group_deletions(rt.text, start, end, opts, rules, where, depth[start] > 0)
                    if label is None:
                        untouched += 1
                        continue
                    decided[label] = decided.get(label, 0) + 1
                    dels += d
                if not dels:
                    continue
                if nearest_p(p) is not None:
                    raise JobException(f"{where} is inside a text box (Word keeps two copies of it); not resolved",
                                       "textbox", {"where": where})
                done = plan_paragraph(frag, part, bi, p, [Edit(a, b, "") for a, b in dels], tracked, in_field, where)
                if done:
                    data.setdefault(part, {})[str(bi)] = edits_to_data(done)
        total = sum(len(v) for blocks in data.values() for v in blocks.values())
        frag.property_assertions.append({
            "kind": "resolved_options", "scope": scope, "select": sel, "rules": params["rules"], "tracked": tracked,
            "author": author, "expected_edits": total,
            "edited_blocks": {part: sorted(int(b) for b in blocks) for part, blocks in data.items()}})
        if total == 0:
            if params.get("allow_missing"):
                frag.notes.append("resolve_options: no option group any rule applies to; no change.")
                return frag
            raise JobException(f"resolve_options: no [option] group in {scope} that a rule applies to", "not-found",
                               {"scope": scope})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = frag.rev_delta.get("ins", 0) + frag.rev_delta.get("del", 0)
        frag.transform_data = {"edits": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        frag.notes.append(f"resolve_options ({'tracked' if tracked else 'untracked'}): "
                          + "; ".join(f"{label}: {n} group(s)" for label, n in decided.items())
                          + (f"; {untouched} group(s) no rule applies to, left as they are" if untouched else ""))
        if not tracked:
            frag.flags.append("UNTRACKED deletions at the user's request: no revision marks; V2's list is the proof.")
        return frag

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        scope = select.get("scope") or params.get("scope")
        if scope:
            select["scope"] = scope
        clauses: List[str] = []
        for r in (params.get("rules") if isinstance(params.get("rules"), list) else []):
            if not isinstance(r, dict) or not isinstance(r.get("match"), str) or not r["match"].strip():
                continue
            target = prose.quote(r["match"]) + (" (matching case exactly)" if r.get("match_case") else "")
            if r.get("action") == "drop":
                clauses.append(f"remove every option matching {target}, with its brackets and spacing")
            else:
                clauses.append(f"keep only the option matching {target}, dropping the alternatives and the brackets")
        if len(clauses) > 4:
            clauses = clauses[:3] + [f"{len(clauses) - 3} more rules"]
        if params.get("tracked", True) is False:
            tail = "without tracked changes"
        else:
            tail = "shown as tracked changes" + (f" by {prose.quote(params['author'])}" if params.get("author") else "")
            if params.get("date_convention") == "utc":
                tail += " dated in UTC"
        head = f"Resolve the choices offered in square brackets {prose.where(select)}"
        if clauses:
            first = prose.sentence(f"{head}, {tail}: " + "; ".join(clauses))
        else:
            first = prose.sentence(f"{head} by the rules given, {tail}")
        second = "The first rule that fits a choice decides it; choices no rule fits are left as they are" + (
            ", and files with no such choice are left unchanged" if params.get("allow_missing") else "")
        return first + " " + prose.sentence(second)

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("edits"):
            return
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, by_block in td["edits"].items():
            root = pkg.xml(part)
            apply_part_edits(root, by_block, td["tracked"], stamp, next_id)
            pkg.write_xml(part, root)
