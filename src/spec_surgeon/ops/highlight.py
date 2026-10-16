"""Op: remove_highlight — take Word's highlighter off text: every colour, or only the listed ones (docs/
BUILTIN_OPS_BATCH2_DESIGN.md §3). The verifier re-selects the targets with its own code (verify/highlight.py, kind
``removed_highlight``).

**Targets** (CLAUDE.md "Highlight", both sides): every run (``w:r`` whose nearest paragraph is in the scope — any container,
deleted runs too, so a rejected deletion brings no highlight back) and every paragraph mark (``w:pPr/w:rPr``) whose own
``w:rPr`` has a ``w:highlight`` child whose ``w:val`` is one of the colours (default: every colour; ``none`` — no
highlight — never is one). Never a ``w:rPrChange`` snapshot (history) nor a style definition (a style in use that
highlights is flagged).

**Result**: the ``w:highlight`` goes; plain — an emptied run ``w:rPr`` goes, an emptied mark ``w:rPr`` goes and then an
emptied ``w:pPr``; tracked — a ``w:rPrChange`` (new id, author, date) holding a snapshot of the source ``w:rPr`` ends the
``w:rPr``, and a target already carrying one is refused (``tracked-format``: a second tracked change cannot be
expressed). Revision delta (tracked): ``rPrChange`` +1 per run and mark.
"""
from __future__ import annotations

from typing import Any, Dict, List

from ..ooxml import (RevisionStamp, file_uses_date_utc, max_revision_id, paragraphs, property_change)
from ..package import W, DocxPackage
from . import register
from .base import JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import SCOPE_KINDS, StyleSheet, runs_by_paragraph, scope_parts

COLORS = ("yellow", "green", "cyan", "magenta", "blue", "red", "darkBlue", "darkCyan", "darkGreen", "darkMagenta",
          "darkRed", "darkYellow", "darkGray", "lightGray", "black", "white")
DEFAULT_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]


def canonical_colors(raw: Any) -> List[str]:
    """The colour list in Word's spelling (case ignored); the default is every colour."""
    if raw is None:
        return list(COLORS)
    if not isinstance(raw, list) or not raw:
        raise JobException("'colors' must be a non-empty list of highlight colours", "bad-params")
    by_fold = {c.casefold(): c for c in COLORS}
    out: List[str] = []
    for c in raw:
        if not isinstance(c, str) or c.strip().casefold() not in by_fold:
            raise JobException(f"{c!r} is not a Word highlight colour (one of {list(COLORS)})", "bad-params")
        name = by_fold[c.strip().casefold()]
        if name not in out:
            out.append(name)
    return out


def _what(colors: List[str]) -> str:
    return "highlighting" if colors == list(COLORS) else f"{', '.join(colors)} highlighting"


def highlighted(rpr, colors: List[str]) -> bool:
    """The rPr's own w:highlight is one of ``colors``."""
    h = rpr.find(W + "highlight") if rpr is not None else None
    return h is not None and h.get(W + "val") in colors


def strip_highlight(rpr, stamp, rev_id: int) -> bool:
    """Take the highlight off one rPr; tracked when ``stamp`` is given. True when the rPr is left empty (plain)."""
    chg = None
    if stamp is not None:
        chg = property_change("rPrChange", "rPr", [c for c in rpr if c.tag != W + "rPrChange"], stamp, rev_id)
    for h in rpr.findall(W + "highlight"):
        rpr.remove(h)
    if chg is not None:
        rpr.append(chg)
    return not len(rpr) and not rpr.attrib


@register
class RemoveHighlight(Op):
    name = "remove_highlight"
    mode = "formatting"
    description = ("Remove Word's text highlighting (the highlighter pen): every colour, or only the listed ones. The "
                   "text and every other formatting stay; plain by default, tracked on request.")
    params_schema = {
        "type": "object",
        "properties": {
            "colors": {"type": "array", "items": {"type": "string", "enum": list(COLORS)},
                       "description": "highlight colours to remove (default: every colour)"},
            "tracked": {"type": "boolean", "default": False,
                        "description": "record each change as a tracked formatting change (w:rPrChange)"},
            "author": {"type": "string"},
            "allow_missing": {"type": "boolean", "default": True,
                              "description": "default true: a file without such highlighting is left as it is; false "
                                             "sets it aside"},
        },
    }
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document, headers, footers, footnotes, endnotes)"}}}
    verification_profile = {
        "V2": "zero text diffs",
        "V4": "tracked: +1 w:rPrChange per run and paragraph mark changed",
        "V10": "removed_highlight re-selects every highlighted run and mark and checks each output w:rPr is the "
               "source's without the highlight (tracked: plus a w:rPrChange whose snapshot is the source); the residual "
               "proves nothing else changed",
    }

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException("remove_highlight selects by scope only", "bad-params")
        scope = select.get("scope") or DEFAULT_SCOPE
        if isinstance(scope, str):
            scope = [scope]
        colors = canonical_colors(params.get("colors"))
        tracked = params.get("tracked", False)
        if not isinstance(tracked, bool):
            raise JobException("'tracked' must be true or false", "bad-params")
        author = params.get("author") or default_author()
        parts = scope_parts(pkg, scope)
        frag = self.fragment({"scope": scope}, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, Dict[str, List[Any]]] = {}
        n_runs = n_marks = 0
        by_color: Dict[str, int] = {}
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            t_runs: List[List[int]] = []
            t_marks: List[int] = []
            for bi, p in enumerate(pars):
                hit = False
                for ri, r in enumerate(groups[bi]):
                    rpr = r.find(W + "rPr")
                    if not highlighted(rpr, colors):
                        continue
                    self._check_tracked(rpr, tracked, f"{part} paragraph {bi} run {ri}")
                    t_runs.append([bi, ri])
                    c = rpr.find(W + "highlight").get(W + "val")
                    by_color[c] = by_color.get(c, 0) + 1
                    hit = True
                if hit:
                    frag.touches.append((part, str(bi), "rPr"))
                mrpr = p.find(W + "pPr/" + W + "rPr")
                if highlighted(mrpr, colors):
                    self._check_tracked(mrpr, tracked, f"{part} paragraph {bi} paragraph mark")
                    t_marks.append(bi)
                    frag.touches.append((part, str(bi), "markRPr"))
            if t_runs or t_marks:
                targets[part] = {"runs": t_runs, "marks": t_marks}
                frag.intended_parts.append(part)
                n_runs += len(t_runs)
                n_marks += len(t_marks)
        self._flag_styles(pkg, frag, parts, colors)
        # the user's own words (colours and scope as asked, None = the default): the verifier reads them itself
        frag.property_assertions.append({"kind": "removed_highlight", "asked_colors": params.get("colors"),
                                         "asked_scope": select.get("scope"), "parts": parts, "colors": colors,
                                         "tracked": tracked, "author": author,
                                         "expected_runs": n_runs, "expected_marks": n_marks})
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author}
        if not targets:
            if params.get("allow_missing", True):
                frag.notes.append(f"remove_highlight: no {_what(colors)} in {scope} — nothing to do.")
                return frag
            raise JobException(f"remove_highlight: no {_what(colors)} in {scope}", "not-found", {"scope": scope})
        if tracked:
            n = n_runs + n_marks
            frag.rev_delta["rPrChange"] = n
            frag.rev_ids_needed = n
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        frag.notes.append(f"remove_highlight: {n_runs} run(s) and {n_marks} paragraph mark(s) lose their highlight ("
                          + ", ".join(f"{c} {n}" for c, n in sorted(by_color.items())) + f"), "
                          f"{'tracked (w:rPrChange)' if tracked else 'plain'}; the text and other formatting stay")
        return frag

    @staticmethod
    def _check_tracked(rpr, tracked: bool, where: str) -> None:
        if tracked and rpr.find(W + "rPrChange") is not None:
            raise JobException(f"{where} already carries a pending tracked formatting change (w:rPrChange); a second "
                               f"tracked change cannot be expressed — accept or reject it first, or remove the "
                               f"highlight untracked", "tracked-format", {"where": where})

    @staticmethod
    def _flag_styles(pkg: DocxPackage, frag: PlanFragment, parts: List[str], colors: List[str]) -> None:
        """A style that highlights its text is left as it is (flagged when the scope uses it)."""
        sheet = StyleSheet(pkg)
        lit = {sid for sid, st in sheet.styles.items() if highlighted(st.find(W + "rPr"), colors)}
        if not lit:
            return
        used: Dict[str, int] = {}
        for part in parts:
            root = pkg.xml(part)
            for el in root.iter(W + "pStyle", W + "rStyle"):
                v = el.get(W + "val")
                if v in lit:
                    used[v] = used.get(v, 0) + 1
        for sid, n in sorted(used.items()):
            frag.flags.append(f"style {sid!r} highlights its text ({n} use(s) in scope); a style is not changed — remove "
                              f"the highlight from the style in Word")

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("targets"):
            return
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, t in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            for bi, ri in t["runs"]:
                r = groups[bi][ri]
                rpr = r.find(W + "rPr")
                if strip_highlight(rpr, stamp, next_id):
                    r.remove(rpr)
                next_id += stamp is not None
            for bi in t["marks"]:
                ppr = pars[bi].find(W + "pPr")
                rpr = ppr.find(W + "rPr")
                if strip_highlight(rpr, stamp, next_id):
                    ppr.remove(rpr)
                    if not len(ppr) and not ppr.attrib:
                        pars[bi].remove(ppr)
                next_id += stamp is not None
            pkg.write_xml(part, root)
