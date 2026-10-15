"""Why a check failed, in specifics — the lines under "WHY IT FAILED" in a verification record and in DIAGNOSTICS.md.

Reads only a check's own data and the record's predictions; like everything under verify/, nothing from the transform
side. ``text_change`` shows where two texts part, word-diff style: ``…kept text[-removed-]{+added+}kept text…`` with
tabs, line breaks and non-breaking spaces made visible (⇥ ⏎ ⍽), so an invisible difference is never reported as
"the same text".
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence

_VISIBLE = {"\t": "⇥", "\n": "⏎", "\r": "␍", " ": "⍽", "​": "[ZWSP]", "­": "[SHY]", "﻿": "[BOM]"}


def visible(s: str) -> str:
    """Whitespace and control characters a reader cannot see, shown."""
    out = []
    for ch in s:
        if ch in _VISIBLE:
            out.append(_VISIBLE[ch])
        elif ord(ch) < 32:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n // 2] + " … " + s[-(n // 2):]


def quote(text: Optional[str], n: int = 80) -> str:
    """A paragraph's text as a reader would search for it in Word."""
    t = " ".join((text or "").split())
    return "“" + (t if len(t) <= n else t[:n - 1] + "…") + "”"


def text_change(before: Optional[str], after: Optional[str], context: int = 40, limit: int = 160) -> str:
    """Where ``after`` departs from ``before``: the common text around one changed stretch, the removed part in
    ``[-…-]`` and the added part in ``{+…+}``."""
    if before is None and after is None:
        return "(no text)"
    if before is None:
        return "(new block) {+" + _cut(visible(after or ""), limit) + "+}"
    if after is None:
        return "(block removed) [-" + _cut(visible(before), limit) + "-]"
    if before == after:
        return "(no text change) " + _cut(visible(before), limit)
    n = min(len(before), len(after))
    p = 0
    while p < n and before[p] == after[p]:
        p += 1
    s = 0
    while s < n - p and before[len(before) - 1 - s] == after[len(after) - 1 - s]:
        s += 1
    removed, added = before[p:len(before) - s], after[p:len(after) - s]
    head = before[max(0, p - context):p]
    tail = before[len(before) - s:len(before) - s + context]
    return (("…" if p > context else "") + visible(head)
            + ("[-" + _cut(visible(removed), limit) + "-]" if removed else "")
            + ("{+" + _cut(visible(added), limit) + "+}" if added else "")
            + visible(tail) + ("…" if s > context else ""))


# ---- per check ------------------------------------------------------------------------------------------------------

def _v1(d: Dict[str, Any], rec: Any) -> List[str]:
    out = [f"{n} changed, but the plan predicted no change to it" for n in d.get("unexpected") or []]
    pa = set(getattr(rec, "predicted_members_added", None) or [])
    pr = set(getattr(rec, "predicted_members_removed", None) or [])
    added, removed = set(d.get("added") or []), set(d.get("removed") or [])
    out += [f"{n} was added, but the plan did not predict it" for n in sorted(added - pa)]
    out += [f"{n} should have been added (the plan predicted it) but is not in the output" for n in sorted(pa - added)]
    out += [f"{n} was removed, but the plan did not predict it" for n in sorted(removed - pr)]
    out += [f"{n} should have been removed (the plan predicted it) but is still there" for n in sorted(pr - removed)]
    o = d.get("order")
    if isinstance(o, dict):
        out.append(f"the order of the package's members changed at position {o.get('position')}: expected "
                   f"{o.get('expected')!r}, found {o.get('found')!r}")
    out += [f"package integrity: {p}" for p in d.get("integrity") or []]
    out += [f"{n} is still in the package, but nothing refers to it any more (orphaned)" for n in d.get("orphaned") or []]
    return out


def _v2(d: Dict[str, Any], rec: Any) -> List[str]:
    actual = d.get("actual") or []
    predicted = getattr(rec, "predicted_diffs", None) or []

    def predicted_index(part, before, after):
        for x in predicted:
            if x.get("part") == part and x.get("before") == before and x.get("after") == after:
                return x.get("block_index")
        return None

    def actual_index(part, before, after):
        for x in actual:
            if x.get("part") == part and x.get("before") == before and x.get("after") == after:
                return x.get("before_index") if x.get("before_index") is not None else x.get("after_index")
        return None

    missing = [tuple(x) for x in d.get("missing") or []]
    extra = [tuple(x) for x in d.get("unpredicted") or []]
    out: List[str] = []
    paired = set()
    for part, before, after in missing:
        where = f"{part} block {predicted_index(part, before, after)}"
        twin = next((u for u in extra if u not in paired and u[0] == part and u[1] == before and before is not None),
                    None)
        if twin is not None:
            paired.add(twin)
            out.append(f"{where}: the plan predicted {text_change(before, after)} — the output has something else; "
                       f"output against the prediction: {text_change(after, twin[2])}")
        else:
            out.append(f"{where}: a predicted change did not happen: {text_change(before, after)}")
    for u in extra:
        if u in paired:
            continue
        part, before, after = u
        out.append(f"{part} block {actual_index(part, before, after)}: a change the plan did not predict: "
                   f"{text_change(before, after)}")
    return out


def _v3(d: Dict[str, Any], rec: Any) -> List[str]:
    out = []
    for c in d.get("changes") or []:
        b, a = c.get("before") or {}, c.get("after") or {}
        keys = sorted(k for k in set(b) | set(a) if b.get(k, 0) != a.get(k, 0))
        what = ", ".join(f"{k} {b.get(k, 0)}→{a.get(k, 0)}" for k in keys) or "structure differs"
        text = c.get("text")
        out.append(f"{c.get('part')} block {c.get('block')}: {what}" + (f" (in paragraph {quote(text)})" if text else ""))
    return out


def _v4(d: Dict[str, Any], rec: Any) -> List[str]:
    out = []
    for kind, v in sorted((d.get("unpredicted") or {}).items()):
        try:
            out.append(f"{kind}: the count moved by {int(v[0]):+d}, the plan predicted {int(v[1]):+d}")
        except (TypeError, ValueError, IndexError):
            out.append(f"{kind}: {v}")
    dup = sorted(set(d.get("duplicate_ids") or []) | set(d.get("duplicate_ids_all_kinds") or []))
    if dup:
        out.append(f"revision ids used more often than in the source: {dup[:10]}")
    if d.get("colliding_new_ids"):
        out.append(f"new revision ids that collide with ids already in use: {list(d['colliding_new_ids'])[:10]}")
    return out


def _v5a(d: Dict[str, Any], rec: Any) -> List[str]:
    out = []
    for part, errs in (d.get("new_errors") or {}).items():
        out += [f"{part}: a schema error the source did not have: {e}" for e in errs or []]
    return out


def _v5b(d: Dict[str, Any], rec: Any) -> List[str]:
    out = []
    for x in d.get("differences") or []:
        if "block_count" in x:
            out.append(f"{x.get('part')}: the reject-all view has {x['block_count'][1]} block(s), the source's "
                       f"{x['block_count'][0]}")
        else:
            out.append(f"{x.get('part')} block {x.get('block')}: rejecting every revision does not give back the "
                       f"source — {text_change(x.get('source'), x.get('output'))}")
    ids = d.get("new_revision_ids") or []
    if ids:
        out.append(f"tracked revisions added by a job that adds none: ids {list(ids)[:10]}")
    if "predicted_property_changes" in d and d.get("new_property_changes") != d.get("predicted_property_changes"):
        out.append(f"{d.get('new_property_changes')} tracked property change(s) added; the plan predicted "
                   f"{d.get('predicted_property_changes')}")
    if d.get("v2_ok") is False:
        out.append("the text changes are not exactly the predicted list (see V2), so the list is not a complete account")
    return out


def _v5c(d: Dict[str, Any], rec: Any) -> List[str]:
    return [str(e) for e in d.get("errors") or []]


def _v6(d: Dict[str, Any], rec: Any) -> List[str]:
    out = [f"renderer: {m}" for m in d.get("messages") or []]
    if d.get("traceback"):
        out.append("the comparison itself failed; its traceback is in the file's trace (logs/<name>.trace.log)")
    return out


def _v7(d: Dict[str, Any], rec: Any) -> List[str]:
    from .suite import GEOM_TOL_PT
    out = []
    for p in d.get("pages") or []:
        for problem in p.get("problems") or []:                       # template mode
            out.append(f"page {p.get('page')} (template page {p.get('template_page')}): {problem}")
    if out:
        return out
    for p in d.get("pages") or []:                                    # source mode
        dy, dx = float(p.get("max_dy") or 0), float(p.get("max_dx") or 0)
        if not p.get("expected_change") and (dy > GEOM_TOL_PT or dx > GEOM_TOL_PT):
            out.append(f"page {p.get('page')}: words moved up to {dy:.2f} pt vertically and {dx:.2f} pt horizontally "
                       f"({p.get('matched_words')} word(s) compared; tolerance {GEOM_TOL_PT} pt)")
    if d.get("ref_pages") is not None and d.get("ref_pages") != d.get("out_pages"):
        out.append(f"the page count changed: {d.get('ref_pages')} → {d.get('out_pages')}")
    return out


def _v8(d: Dict[str, Any], rec: Any) -> List[str]:
    out = []
    for x in d.get("accept_mismatches") or []:
        if "block_count" in x:
            out.append(f"{x.get('part')}: the accept-all view has {x['block_count'][1]} block(s), "
                       f"{x['block_count'][0]} expected")
        else:
            out.append(f"{x.get('part')} block {x.get('block')}: accepting every revision does not give the predicted "
                       f"text — {text_change(x.get('expected'), x.get('actual'))}")
    return out


def _v10(d: Dict[str, Any], rec: Any) -> List[str]:
    return [str(e) for e in d.get("errors") or []]


EXPLAINERS: Dict[str, Callable[[Dict[str, Any], Any], List[str]]] = {
    "V1": _v1, "V2": _v2, "V3": _v3, "V4": _v4, "V5a": _v5a, "V5b": _v5b, "V5c": _v5c, "V6": _v6, "V7": _v7,
    "V8": _v8, "V10": _v10,
}


def failure_details(check_id: str, data: Optional[Dict[str, Any]], summary: str = "", rec: Any = None,
                    limit: int = 12) -> List[str]:
    """The specifics of one failed check: which part, block and text, what was predicted and what was found. Falls
    back to the check's summary when its data says nothing more; at most ``limit`` lines (the record's JSON has all)."""
    fn = EXPLAINERS.get(check_id)
    try:
        lines = fn(data or {}, rec) if fn else []
    except Exception as e:  # noqa: BLE001 — an explanation must never hide the failure it explains
        lines = [f"(the details could not be read: {type(e).__name__}: {e})"]
    if not lines:
        lines = [summary] if summary else []
    if len(lines) > limit:
        lines = lines[:limit] + [f"… and {len(lines) - limit} more (all of them are in the record's JSON)"]
    return lines


def explain_record(checks: Sequence[Any], rec: Any = None) -> List[tuple]:
    """``(id, name, summary, [specifics…])`` for every failed check of a record."""
    return [(c.id, c.name, c.summary, failure_details(c.id, c.data, c.summary, rec))
            for c in checks if c.status == "FAIL"]
