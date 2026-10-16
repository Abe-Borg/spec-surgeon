"""Op: make_values_agree — make the header/footer, the title line and the file name agree on the section's number and
title, from whichever one the user picks as correct (design note §7).

Definition (CLAUDE.md "Agreeing values", both sides):
* **places** are named finders ``{in, pattern, required?}``: ``in`` is ``document`` | ``headers`` | ``footers`` (the
  pattern is searched — ``finditer`` — in every paragraph's run text, ops/textedit.py) or ``filename`` (the pattern is
  searched once in the source file's name without .docx); the pattern's named groups (``number``, ``title`` …) are the
  values; each value is its group's text with surrounding whitespace left out;
* the **truth** is one place: all its matches must give each value the same text (whitespace collapsed), else
  ``ambiguous``; no match → ``not-found``;
* two values **agree** when their letters and digits, casefolded and run together, are the same ("211319" and
  "21 13 19"; "PREACTION SPRINKLER SYSTEMS" and "Preaction Sprinkler Systems") — such a place is left as it is: nothing
  is converted;
* in every other place, each value that does not agree is replaced by the truth's text exactly as written there (an
  edit of the core in a document place; in the file name, the output file gets the corrected name, checked like
  name_output's). A ``required`` place with no match is refused (``not-found``); otherwise noted.
The verifier derives all of it again (verify/textops.py, kind ``agreed_values``).
"""
from __future__ import annotations

import re
from pathlib import PureWindowsPath
from typing import Any, Dict, List, Optional, Tuple

from ..ooxml import RevisionStamp, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage
from . import register
from .base import JobException, Op, PlanFragment
from .naming import OUTPUT_NAME_TOUCH, check_name, with_suffix
from .replace_table import default_author, tracked_of
from .selectors import scope_parts
from .slots import field_runs, value_tokens
from .textedit import Edit, RunText, apply_part_edits, edits_to_data, nearest_p, plan_paragraph, shown

WHERE = ("document", "headers", "footers", "filename")


def agree(a: str, b: str) -> bool:
    return "".join(value_tokens(a)) == "".join(value_tokens(b))


def group_span(m, g: str) -> Optional[Tuple[int, int]]:
    """The group's span without surrounding whitespace (None when the group did not take part or is blank)."""
    a, b = m.span(g)
    if a < 0:
        return None
    text = m.string
    while a < b and text[a].isspace():
        a += 1
    while b > a and text[b - 1].isspace():
        b -= 1
    return (a, b) if b > a else None


def stem_of(name: str) -> str:
    base = PureWindowsPath(name).name
    return base[:-5] if base.lower().endswith(".docx") else base


def compile_places(raw: Any) -> Dict[str, Tuple[str, Any, bool]]:
    if not isinstance(raw, dict) or len(raw) < 2:
        raise JobException("'places' must name at least two places {in, pattern}", "bad-params")
    out = {}
    for name, place in raw.items():
        if not isinstance(place, dict) or set(place) - {"in", "pattern", "required"} or place.get("in") not in WHERE:
            raise JobException(f"place {name!r} must be {{in: {'|'.join(WHERE)}, pattern, required?}}", "bad-params")
        try:
            rx = re.compile(place.get("pattern") or "")
        except re.error as e:
            raise JobException(f"place {name!r}: bad pattern ({e})", "bad-params")
        if not rx.groupindex:
            raise JobException(f"place {name!r}: the pattern names no value (use (?P<number>…) / (?P<title>…))",
                               "bad-params")
        out[name] = (place["in"], rx, bool(place.get("required", False)))
    if sum(1 for w, _, _ in out.values() if w == "filename") > 1:
        raise JobException("at most one place can be the file name", "bad-params")
    return out


@register
class MakeValuesAgree(Op):
    name = "make_values_agree"
    mode = "content"
    description = ("Make the places that show the section's number and title — header/footer, the title line, the file "
                   "name — agree with the one the user picks as correct ('truth'): each place is a pattern with named "
                   "values ((?P<number>…), (?P<title>…)); a value that already agrees (same letters and digits, any case "
                   "or spacing) is left alone; one that does not is replaced by the truth's text exactly as written there "
                   "(tracked by default); a disagreeing file name gives the output copy the corrected name.")
    params_schema = {
        "type": "object",
        "properties": {
            "places": {"type": "object", "description": "name → {in: document|headers|footers|filename, pattern: a "
                                                        "Python regex with named groups, required?: bool}"},
            "truth": {"type": "string", "description": "the place that is correct"},
            "values": {"type": "array", "items": {"type": "string"},
                       "description": "the named values to align (default: every value the truth's pattern names)"},
            "tracked": {"type": "boolean", "default": True},
            "author": {"type": "string"},
            "date_convention": {"type": "string", "enum": ["word-local-z", "utc"], "default": "word-local-z"},
        },
        "required": ["places", "truth"],
    }
    select_schema = {"type": "object", "description": "none — the places say where to look"}
    verification_profile = {
        "V2": "one BlockDiff per corrected paragraph",
        "V10": "agreed_values re-derives the truth, every place's values and the edits (item model), and the output "
               "file's name when the file name is corrected",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("make_values_agree takes no selector", "bad-params")
        places = compile_places(params.get("places"))
        truth = params.get("truth")
        if truth not in places:
            raise JobException(f"'truth' must be one of the places {sorted(places)}", "bad-params")
        names = params.get("values") or sorted(places[truth][1].groupindex)
        if not isinstance(names, list) or set(names) - set(places[truth][1].groupindex):
            raise JobException(f"'values' must be values the truth's pattern names {sorted(places[truth][1].groupindex)}",
                               "bad-params")
        tracked = tracked_of(params)
        author = params.get("author") or default_author()
        stem = stem_of(pkg.name)
        found: Dict[str, List[Tuple[str, int, Any, List[Any]]]] = {}       # place → [(part, block, paragraph, matches)]
        for pname, (where, rx, _req) in places.items():
            if where == "filename":
                m = rx.search(stem)
                found[pname] = [("filename", -1, None, [m])] if m else []
                continue
            hits = []
            for part in scope_parts(pkg, [where]):
                for bi, p in enumerate(paragraphs(pkg.xml(part))):
                    ms = list(rx.finditer(RunText(p).text))
                    if ms:
                        hits.append((part, bi, p, ms))
            found[pname] = hits
        truths: Dict[str, str] = {}
        for g in names:
            vals = [m.string[s[0]:s[1]] for _, _, _, ms in found[truth] for m in ms
                    for s in [group_span(m, g)] if s is not None]
            if not vals:
                raise JobException(f"the truth ({truth}) shows no {g} in this file", "not-found", {"value": g})
            if len({" ".join(v.split()) for v in vals}) > 1:
                raise JobException(f"the truth ({truth}) shows {g} in different ways: {sorted(set(vals))}", "ambiguous",
                                   {"value": g, "found": sorted(set(vals))})
            truths[g] = vals[0]
        frag = self.fragment({}, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        edits: Dict[Tuple[str, int], List[Edit]] = {}
        pars: Dict[Tuple[str, int], Any] = {}
        name_edits: List[Tuple[int, int, str]] = []
        changed: List[str] = []
        for pname, (where, rx, required) in places.items():
            if pname == truth:
                continue
            if not found[pname]:
                if required:
                    raise JobException(f"{pname} shows nothing matching its pattern in this file", "not-found",
                                       {"place": pname})
                frag.notes.append(f"make_values_agree: {pname} — no match in this file; left as it is")
                continue
            for part, bi, p, ms in found[pname]:
                for m in ms:
                    for g in names:
                        if g not in rx.groupindex:
                            continue
                        span = group_span(m, g)
                        if span is None:
                            continue
                        old = m.string[span[0]:span[1]]
                        if agree(old, truths[g]):
                            continue
                        changed.append(f"{pname} {g}: {old!r} → {truths[g]!r}")
                        if where == "filename":
                            name_edits.append((span[0], span[1], truths[g]))
                        else:
                            edits.setdefault((part, bi), []).append(Edit(span[0], span[1], truths[g]))
                            pars[(part, bi)] = p
        data: Dict[str, Dict[str, List[List[Any]]]] = {}
        for (part, bi), es in sorted(edits.items()):
            p = pars[(part, bi)]
            where = f"{part} paragraph {bi}"
            if nearest_p(p) is not None:
                raise JobException(f"{where} is inside a text box (Word keeps two copies of it)", "textbox",
                                   {"where": where})
            done = plan_paragraph(frag, part, bi, p, es, tracked, field_runs(pkg.xml(part)), where,
                                  note="made to agree")
            if done:
                data.setdefault(part, {})[str(bi)] = edits_to_data(done)
        name = None
        new_stem = stem
        for a, b, v in sorted(name_edits, reverse=True):       # one match's groups never overlap: right to left
            new_stem = new_stem[:a] + v + new_stem[b:]
        if new_stem != stem:
            name = check_name(with_suffix(new_stem))
            frag.output_name = name
            frag.touches.append(OUTPUT_NAME_TOUCH)
        frag.property_assertions.append({
            "kind": "agreed_values", "places": params["places"], "truth": truth, "values": names, "tracked": tracked,
            "author": author, "expected_truth": truths, "expected_name": name,
            "expected_edits": sum(len(v) for b in data.values() for v in b.values()),
            "edited_blocks": {part: sorted(int(b) for b in blocks) for part, blocks in data.items()}})
        frag.notes.append(f"make_values_agree: the truth ({truth}) says "
                          + ", ".join(f"{g} = {v!r}" for g, v in truths.items())
                          + (f"; changed: {'; '.join(changed)}" if changed else "; every place already agrees"))
        if not data:
            return frag
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = frag.rev_delta.get("ins", 0) + frag.rev_delta.get("del", 0)
        frag.transform_data = {"edits": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        return frag

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

