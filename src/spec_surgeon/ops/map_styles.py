"""Op: map_styles — the architect's style A becomes your style B everywhere, your styles imported from a template
(built-in batch 4, item 31 — docs/BUILTIN_OPS_BATCH4_DESIGN.md §7).

Formatting mode: zero text diffs. Plain by default (a whole-document change by nature); ``tracked: true`` records each
paragraph's change as a ``w:pPrChange`` and each run's or paragraph mark's as a ``w:rPrChange``.

Parameters: ``map`` — ``[{from, to}]``, ids or names; ``template`` — where a ``to`` comes from (shared style import,
ops/style_import.py); ``existing`` — ``keep`` (default: a ``to`` this file already has keeps the file's definition) or
``replace`` (your definition replaces it; needs ``template``); ``allow_missing`` (default true: a pair whose ``from`` this
file lacks, or that nothing uses, is skipped and noted; false refuses ``not-found``); ``tracked`` (default false),
``author``. ``select``: optional ``scope`` (default every text part: document, headers, footers, footnotes, endnotes,
comments).

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's reading is
verify/restyle.py):

* **The pairs.** Two pairs with one ``from``, or a ``to`` that is another pair's ``from`` (as written), are
  ``bad-params``. With a template, every ``to`` must resolve in the template (``style_import.resolve_style``: id, else
  name casefolded; none → ``bad-params``, two → ``style-mismatch``). Per file: ``from`` resolves in the file (none →
  skipped, ``missing``); the style mapped to is — with ``existing: "replace"`` — the template's; with ``keep`` the file's
  own when ``to`` resolves in the file, else the template's (imported), else ``missing-style``. ``from`` = the style mapped
  to → skipped (``same``). ``from`` and the style mapped to must be of one type, paragraph, character or table; a default
  character or table style as ``from`` is ``style-mismatch`` (nothing references it); two pairs whose ``from`` resolve to
  one style, or a style mapped to that is another pair's ``from``, are ``style-mismatch``.
* **The elements** (``paragraph_style_of``, ``mapped_holders``, ``table_style_of``), over the parts in scope: paragraph
  style F — every ``w:p`` whose style (``w:pStyle``, else the default paragraph style) is F; character style F — every
  ``w:r`` (runs grouped by their nearest paragraph) and every paragraph mark (``w:pPr/w:rPr``) whose ``w:rStyle`` is F;
  table style F — every ``w:tbl`` whose ``w:tblPr/w:tblStyle`` is F. A pair with no element is skipped (``unused``) and
  imports nothing. A character F also used where this op does not restyle it — a content control's run properties
  (``w:sdtPr/w:rPr``, ``w:sdtEndPr/w:rPr``) or an equation run's (``m:r/w:rPr``) in a part in scope, a list level's
  number formatting (``w:lvl/w:rPr``) in the numbering part — refuses ``structure`` (``unmapped_uses``): never left in
  use while every other use changes.
* The template, when given, is pinned (rule 12): apply refuses a changed one (``internal``) whether or not anything is
  imported — the pairs were resolved through it.
* **The change**: a paragraph's ``w:pStyle/@w:val`` becomes the style mapped to (a new ``w:pStyle`` goes first in the pPr;
  a pPr is created first in the paragraph); a run's or mark's ``w:rStyle/@w:val``; a table's ``w:tblStyle/@w:val``.
  Nothing else: the ``from`` definition stays, ``basedOn`` / ``next`` / numbering links naming it stay. Tracked: one
  ``w:pPrChange`` per paragraph (snapshot = the source pPr without rPr, sectPr, pPrChange), one ``w:rPrChange`` per run or
  mark (snapshot = the source rPr without rPrChange); an existing change marker refuses tracked (``tracked-format``) and
  is flagged when plain; a tracked change of a mark that is itself a pending revision refuses (``tracked-format``); a
  table to map refuses tracked (``tracked-format``).
* Flagged: field instructions naming a ``from`` (STYLEREF, TOC ``\\t``: its id or name, casefolded, as a whole word, in
  any text part) — not updated; runs and marks using a mapped paragraph style's linked character style; existing change
  markers when plain; the import's flags (a ``to``'s basedOn ancestor this file defines differently from the template;
  word/stylesWithEffects.xml not updated; a list identifier shared with one of this file's lists).

Verified by V10's ``mapped_styles`` (verify/restyle.py: pairs, elements and each change re-derived; the requested
template styles cross-checked with the ``styles_imported`` assertion) and ``styles_imported`` (verify/styleimport.py).
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import (RevisionStamp, file_uses_date_utc, max_revision_id, paragraph_ppr, paragraphs, property_change)
from . import register
from .base import JobException, Op, PlanFragment
from .replace_text import default_author
from .selectors import SCOPE_KINDS, StyleSheet, mark_in_revision, runs_by_paragraph, scope_parts
from .style_import import (TemplateStyles, apply_import, check_template_unchanged, numbering_part, plan_import,
                           resolve_style, style_name, style_type)

M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MAPPABLE = ("paragraph", "character", "table")
REVISION_MARKS = (W + "ins", W + "del", W + "moveFrom", W + "moveTo")
SCOPE_SCHEMA = {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)}, "default": list(SCOPE_KINDS)}
REFLOW_REASON = "a paragraph, character or table style change re-lays the text it governs; the source render is no reference"


def clean_params(params: Dict[str, Any]) -> Tuple[List[Tuple[str, str]], Optional[str], str, bool, bool]:
    """(the pairs as written, the template path or None, existing, allow_missing, tracked) — validated."""
    raw = params.get("map")
    if not isinstance(raw, list) or not raw:
        raise JobException("'map' must be a non-empty list of pairs {from, to}", "bad-params")
    pairs: List[Tuple[str, str]] = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict) or set(m) != {"from", "to"} or not all(isinstance(m[k], str) and m[k].strip()
                                                                        for k in ("from", "to")):
            raise JobException(f"map[{i}] must be an object {{from, to}} naming two styles (ids or names)", "bad-params")
        pairs.append((m["from"], m["to"]))
    froms = [f for f, _ in pairs]
    if len(set(froms)) != len(froms):
        raise JobException(f"two pairs map the same style: {sorted({f for f in froms if froms.count(f) > 1})}",
                           "bad-params")
    chained = sorted({t for f, t in pairs if t in froms and t != f})
    if chained:
        raise JobException(f"{chained} is mapped to and from in one call; the order would decide the result — use two "
                           f"specs (a chain)", "bad-params")
    tpath = params.get("template")
    if tpath is not None and (not isinstance(tpath, str) or not tpath):
        raise JobException("'template' must be the path of a .docx", "bad-params")
    existing = params.get("existing", "keep")
    if existing not in ("keep", "replace"):
        raise JobException("existing must be 'keep' or 'replace'", "bad-params")
    if existing == "replace" and tpath is None:
        raise JobException("existing 'replace' takes the definitions from a template: give 'template'", "bad-params")
    allow = params.get("allow_missing", True)
    tracked = params.get("tracked", False)
    for k, v in (("allow_missing", allow), ("tracked", tracked)):
        if not isinstance(v, bool):
            raise JobException(f"{k} must be true or false", "bad-params")
    if "author" in params and not isinstance(params["author"], str):
        raise JobException("author must be text", "bad-params")
    return pairs, tpath, existing, allow, tracked


# ---- the elements -----------------------------------------------------------------------------------------------------

def paragraph_style_of(p, sheet: StyleSheet) -> Optional[str]:
    """The paragraph's style: its w:pStyle/@w:val, else (no pStyle, or one without a value) the default paragraph style."""
    ps = p.find(f"{W}pPr/{W}pStyle")
    return (ps.get(W + "val") if ps is not None else None) or sheet.defaults.get("paragraph")


def run_style_of(rpr) -> Optional[str]:
    rs = rpr.find(W + "rStyle") if rpr is not None else None
    return rs.get(W + "val") if rs is not None else None


def table_style_of(tbl) -> Optional[str]:
    ts = tbl.find(f"{W}tblPr/{W}tblStyle")
    return ts.get(W + "val") if ts is not None else None


def mapped_holders(p, runs) -> List[Tuple[str, Optional[int], Any]]:
    """What a character style can be mapped on in one paragraph: ("run", index, rPr) per run, then ("mark", None, rPr)."""
    out: List[Tuple[str, Optional[int], Any]] = [("run", ri, r.find(W + "rPr")) for ri, r in enumerate(runs)]
    out.append(("mark", None, p.find(f"{W}pPr/{W}rPr")))
    return out


UNMAPPED_PLACES = {"sdtPr": "a content control's run properties (w:sdtPr/w:rPr)",
                   "sdtEndPr": "a content control's end-mark run properties (w:sdtEndPr/w:rPr)",
                   "r": "an equation's run properties (m:r/w:rPr)", "lvl": "a list level's number formatting (w:lvl/w:rPr)"}


def unmapped_uses(pkg: DocxPackage, parts: List[str], styles) -> List[Tuple[str, str, str]]:
    """(part, place, style id) of every use of one of ``styles`` (character styles) this op does not restyle — a
    content control's run properties (``w:sdtPr/w:rPr``, ``w:sdtEndPr/w:rPr``) and an equation run's (``m:r/w:rPr``) in
    the parts in scope, a list level's number formatting (``w:lvl/w:rPr``) in the numbering part. The op refuses them
    (``structure``): it never leaves a mapped style in use while claiming every use changed."""
    out: List[Tuple[str, str, str]] = []
    for part in parts:
        for holder in pkg.xml(part).iter(W + "sdtPr", W + "sdtEndPr", M + "r"):
            v = run_style_of(holder.find(W + "rPr"))
            if v in styles:
                out.append((part, UNMAPPED_PLACES[etree.QName(holder).localname], v))
    npart = numbering_part(pkg)
    if npart is not None:
        for lvl in pkg.xml(npart).iter(W + "lvl"):
            v = run_style_of(lvl.find(W + "rPr"))
            if v in styles:
                out.append((npart, UNMAPPED_PLACES["lvl"], v))
    return out


def field_text(root) -> str:
    """Every field instruction of a part, casefolded, one after another (w:instrText, w:delInstrText, w:fldSimple/@w:instr)."""
    parts = [e.text or "" for e in root.iter(W + "instrText", W + "delInstrText")]
    parts += [e.get(W + "instr") or "" for e in root.iter(W + "fldSimple")]
    return " ".join(parts).casefold()


def names_style(text: str, sid: str, name: Optional[str]) -> bool:
    for v in {sid, name or sid}:
        if v and re.search(r"(?<!\w)" + re.escape(v.casefold()) + r"(?!\w)", text):
            return True
    return False


@register
class MapStyles(Op):
    name = "map_styles"
    mode = "formatting"
    description = ("Map styles: every paragraph, run or table in the architect's style A gets your style B instead, "
                   "your styles (and their numbering) imported from a template when the file lacks them; the A "
                   "definitions stay. Adds no characters; plain by default, tracked on request.")
    params_schema = {"type": "object", "required": ["map"], "properties": {
        "map": {"type": "array", "description": "[{from, to}] — style ids or names (a name is matched case-insensitively)",
                "items": {"type": "object", "required": ["from", "to"],
                          "properties": {"from": {"type": "string"}, "to": {"type": "string"}}}},
        "template": {"type": "string", "description": "the .docx your styles come from (relative paths: the spec's "
                                                      "folder); a 'to' the file lacks is imported from it with the "
                                                      "styles it is based on / linked to / followed by and its list "
                                                      "numbering"},
        "existing": {"type": "string", "enum": ["keep", "replace"], "default": "keep",
                     "description": "a 'to' the file already has: keep the file's definition, or replace it with the "
                                    "template's (needs template)"},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "a pair whose 'from' the file lacks or nothing uses is skipped and noted"},
        "tracked": {"type": "boolean", "default": False,
                    "description": "w:pPrChange per paragraph, w:rPrChange per run/mark (a table style cannot be tracked)"},
        "author": {"type": "string"}}}
    select_schema = {"type": "object", "properties": {"scope": SCOPE_SCHEMA}}
    verification_profile = {
        "V2": "zero text diffs", "V4": "tracked: +1 w:pPrChange per paragraph, +1 w:rPrChange per run/mark",
        "V10": "mapped_styles: pairs, elements and each pStyle/rStyle/tblStyle change re-derived, counts matched, "
               "nothing else changed; styles_imported: closure, imported/replaced styles and list ids re-derived from the "
               "template, each copy compared whole, numbering.xml compared whole; residual identical",
        "V7": "none (a style change reflows the text)"}

    # ---- plan --------------------------------------------------------------------------------------------------------

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raw_select = dict(select or {})
        if set(raw_select) - {"scope"}:
            raise JobException(f"map_styles: unknown selector key(s) {sorted(set(raw_select) - {'scope'})}; it maps "
                               f"every use of a style (scope only)", "bad-params")
        pairs, tpath, existing, allow_missing, tracked = clean_params(params)
        scope = raw_select.get("scope", list(SCOPE_KINDS))
        parts = scope_parts(pkg, scope)
        author = params.get("author") or default_author()
        sheet = StyleSheet(pkg)
        tpl = TemplateStyles.load(tpath) if tpath is not None else None
        t_to: Dict[str, str] = {}
        if tpl is not None:
            for _, t in pairs:
                tid = resolve_style(tpl.sheet.styles, t, "the template")
                if tid is None:
                    raise JobException(f"the template has no style {t!r}", "bad-params", {"style": t})
                t_to[t] = tid

        # ---- the pairs in this file ----
        resolved: List[Dict[str, Any]] = []
        for f, t in pairs:
            entry: Dict[str, Any] = {"from": f, "to": t}
            resolved.append(entry)
            fid = resolve_style(sheet.styles, f, "this file")
            if fid is None:
                entry["status"] = "missing"
                continue
            if existing == "replace":
                to_id, source = t_to[t], "template"
            else:
                did = resolve_style(sheet.styles, t, "this file")
                if did is not None:
                    to_id, source = did, "document"
                elif tpl is not None:
                    to_id, source = t_to[t], "template"
                else:
                    raise JobException(f"this file has no style {t!r} to map {f!r} to, and no template to import it "
                                       f"from", "missing-style", {"style": t})
            entry.update(from_id=fid, to_id=to_id, source=source)
            if to_id == fid:
                entry["status"] = "same"
                continue
            ftype = style_type(sheet.styles[fid])
            ttype = style_type(sheet.styles[to_id] if source == "document" else tpl.sheet.styles[to_id])
            if ftype not in MAPPABLE:
                raise JobException(f"{f!r} is a {ftype} style; only paragraph, character and table styles are mapped",
                                   "style-mismatch", {"from": f})
            if ftype != ttype:
                raise JobException(f"{f!r} is a {ftype} style and {t!r} a {ttype} style; a style is mapped to one of "
                                   f"its own type", "style-mismatch", {"from": f, "to": t})
            if ftype in ("character", "table") and sheet.defaults.get(ftype) == fid:
                raise JobException(f"{f!r} is the default {ftype} style: text and tables without a style use it "
                                   f"implicitly, so there are no references to map", "style-mismatch", {"from": f})
            entry.update(type=ftype, status="mapped")
        live = [e for e in resolved if e["status"] == "mapped"]
        froms = [e["from_id"] for e in live]
        twice = sorted({x for x in froms if froms.count(x) > 1})
        if twice:
            raise JobException(f"two pairs resolve to the same style {twice} in this file", "style-mismatch",
                               {"styles": twice})
        chained = sorted({e["to_id"] for e in live if e["to_id"] in froms})
        if chained:
            raise JobException(f"style {chained} is mapped to by one pair and from by another in this file; the order "
                               f"would decide the result", "style-mismatch", {"styles": chained})
        pmap = {e["from_id"]: e["to_id"] for e in live if e["type"] == "paragraph"}
        cmap = {e["from_id"]: e["to_id"] for e in live if e["type"] == "character"}
        tmap = {e["from_id"]: e["to_id"] for e in live if e["type"] == "table"}
        left = unmapped_uses(pkg, parts, set(cmap)) if cmap else []
        if left:
            part, place, sid = left[0]
            raise JobException(f"character style {sid} is also used by {place} in {part}, which map_styles does not "
                               f"restyle; mapping every other use would leave it there — change that use in Word, or "
                               f"leave the pair out" + (f" (+{len(left) - 1} more)" if len(left) > 1 else ""),
                               "structure", {"style": sid, "part": part, "place": place})

        # ---- the elements ----
        frag = self.fragment(raw_select, {**params, "tracked": tracked, "author": author})
        targets: Dict[str, Dict[str, list]] = {}
        uses: Counter = Counter()
        older = 0
        for part in parts:
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            t_p: List[list] = []
            t_r: List[list] = []
            t_m: List[list] = []
            t_t: List[list] = []
            for bi, p in enumerate(pars):
                where = f"{part} paragraph {bi}"
                ps = paragraph_style_of(p, sheet)
                if ps in pmap:
                    ppr = p.find(W + "pPr")
                    if ppr is not None and ppr.find(W + "pPrChange") is not None:
                        if tracked:
                            raise JobException(f"{where} already carries a pending tracked paragraph change "
                                               f"(w:pPrChange)", "tracked-format", {"part": part, "paragraph": bi})
                        older += 1
                    t_p.append([bi, pmap[ps]])
                    uses[ps] += 1
                for kind, ri, rpr in mapped_holders(p, groups[bi]):
                    rs = run_style_of(rpr)
                    if rs not in cmap:
                        continue
                    if rpr.find(W + "rPrChange") is not None:
                        if tracked:
                            raise JobException(f"{where}: a {kind} to restyle already carries a pending tracked "
                                               f"formatting change (w:rPrChange)", "tracked-format",
                                               {"part": part, "paragraph": bi})
                        older += 1
                    if kind == "mark":
                        if tracked and mark_in_revision(p):
                            raise JobException(f"{where}: the paragraph mark is itself a pending revision; a tracked "
                                               f"formatting change on it would copy that revision into the snapshot",
                                               "tracked-format", {"part": part, "paragraph": bi})
                        t_m.append([bi, cmap[rs]])
                    else:
                        t_r.append([bi, ri, cmap[rs]])
                    uses[rs] += 1
            for k, tbl in enumerate(root.iter(W + "tbl")):
                ts = table_style_of(tbl)
                if ts in tmap:
                    if tracked:
                        raise JobException(f"{part}: table {k} would change its table style; a table style change "
                                           f"cannot be tracked here", "tracked-format", {"part": part, "table": k})
                    t_t.append([k, tmap[ts]])
                    uses[ts] += 1
            if t_p or t_r or t_m or t_t:
                targets[part] = {"paragraphs": t_p, "runs": t_r, "marks": t_m, "tables": t_t}
                frag.intended_parts.append(part)
                for bi, _ in t_p:
                    frag.touches += [(part, str(bi), "pPr"), (part, str(bi), "pStyle")]
                frag.touches += [(part, str(bi), "rPr") for bi in sorted({b for b, _, _ in t_r})]
                frag.touches += [(part, str(bi), "markRPr") for bi, _ in t_m]
                frag.touches += [(part, f"tbl:{k}", "tblPr") for k, _ in t_t]
            frag.touches.append((part, "scope", "verify"))      # V10 re-resolves the elements over this part
        for e in live:
            if not uses[e["from_id"]]:
                e["status"] = "unused"
        active = [e for e in resolved if e["status"] == "mapped"]
        idle = [e for e in resolved if e["status"] in ("missing", "unused")]
        if idle and not allow_missing:
            e = idle[0]
            raise JobException(f"style {e['from']!r} " + ("is not in this file" if e["status"] == "missing" else
                                                          "is used by nothing in scope") + " (allow_missing is false)",
                               "not-found", {"from": e["from"]})

        # ---- the import ----
        requested = list(dict.fromkeys(e["to_id"] for e in active if e["source"] == "template"))
        imp = plan_import(pkg, tpl, requested, existing) if requested else None
        if imp is not None and imp.changes:
            frag.intended_parts += [p for p in imp.intended_parts() if p not in frag.intended_parts]
            frag.touches += imp.touches()
            frag.property_assertions.append(imp.assertion())
            frag.notes += imp.notes
            frag.flags += imp.flags
        if sheet.part is not None:          # what the plan READ: each from and each to the file holds
            for e in resolved:
                if e["status"] in ("missing",):
                    continue
                frag.touches.append((sheet.part, f"style:{e['from_id']}", "verify"))
                if e["to_id"] in sheet.styles:
                    frag.touches.append((sheet.part, f"style:{e['to_id']}", "verify"))

        n_p = sum(len(v["paragraphs"]) for v in targets.values())
        n_r = sum(len(v["runs"]) for v in targets.values())
        n_m = sum(len(v["marks"]) for v in targets.values())
        n_t = sum(len(v["tables"]) for v in targets.values())
        frag.property_assertions.insert(0, {
            "kind": "mapped_styles", "parts": parts, "select": raw_select,
            "params": {k: params[k] for k in ("map", "template", "existing", "allow_missing", "tracked") if k in params},
            "template": tpl.path if tpl is not None else None, "template_md5": tpl.md5 if tpl is not None else None,
            "author": author,
            "pairs": [{k: e.get(k) for k in ("from", "to", "status", "from_id", "to_id", "type", "source")}
                      for e in resolved],
            "requested": requested,
            "expected": {"paragraphs": n_p, "runs": n_r, "marks": n_m, "tables": n_t},
            "changed": {part: {"paragraphs": [bi for bi, _ in v["paragraphs"]],
                               "runs": [[bi, ri] for bi, ri, _ in v["runs"]],
                               "marks": [bi for bi, _ in v["marks"]], "tables": [k for k, _ in v["tables"]]}
                        for part, v in targets.items()}})
        frag.transform_data = {"targets": targets, "tracked": tracked, "author": author,
                               "import": imp.transform_data() if imp is not None and imp.changes else None,
                               # rule 12: the pairs were resolved through the template — apply refuses a changed one
                               # even when nothing is imported
                               "template": tpl.path if tpl is not None else None,
                               "template_md5": tpl.md5 if tpl is not None else None}
        if tracked and (n_p or n_r or n_m):
            if n_p:
                frag.rev_delta["pPrChange"] = n_p
            if n_r + n_m:
                frag.rev_delta["rPrChange"] = n_r + n_m
            frag.rev_ids_needed = n_p + n_r + n_m
            frag.rev_id_base = max_revision_id(pkg) + 1
            frag.transform_data["use_date_utc"] = file_uses_date_utc(pkg)
        if targets:
            frag.geometry_reference, frag.geometry_reason = "none", REFLOW_REASON

        # ---- the record ----
        for e in resolved:
            label = f"{e['from']!r} → {e['to']!r}"
            if e["status"] == "missing":
                frag.notes.append(f"{label}: this file has no style {e['from']!r} — skipped")
            elif e["status"] == "same":
                frag.notes.append(f"{label}: both name style {e['from_id']} here — nothing to map, skipped")
            elif e["status"] == "unused":
                frag.notes.append(f"{label}: nothing in scope uses {e['from_id']} — skipped, nothing imported")
            else:
                frag.notes.append(f"{label}: {e['from_id']} → {e['to_id']} ({e['type']}; "
                                  + ("this file's own definition" if e["source"] == "document" else
                                     ("the template's, replacing this file's" if imp is not None and
                                      e["to_id"] in imp.replaced else
                                      "imported from the template" if imp is not None and e["to_id"] in imp.imported
                                      else "this file's own definition of the template's style"))
                                  + f") on {uses[e['from_id']]} "
                                  + {"paragraph": "paragraph(s)", "character": "run(s)/paragraph mark(s)",
                                     "table": "table(s)"}[e["type"]])
        if not targets:
            frag.notes.append("nothing to map in this file — no change")
        else:
            frag.notes.append(f"{n_p} paragraph(s), {n_r} run(s), {n_m} paragraph mark(s), {n_t} table(s) restyled; "
                              + ("tracked (w:pPrChange / w:rPrChange)" if tracked else "plain")
                              + "; the 'from' definitions stay in the styles (remove unused styles is its own op)")
        self._flags(pkg, sheet, frag, active, parts, older)
        return frag

    @staticmethod
    def _flags(pkg: DocxPackage, sheet: StyleSheet, frag: PlanFragment, active: List[Dict[str, Any]], parts: List[str],
               older: int) -> None:
        if not active:
            return
        texts = {part: field_text(pkg.xml(part)) for part in pkg.text_parts()}
        for e in active:
            st = sheet.styles[e["from_id"]]
            where = [part for part, text in texts.items() if names_style(text, e["from_id"], style_name(st))]
            if where:
                frag.flags.append(f"a field instruction in {where} names style {style_name(st) or e['from_id']!r} "
                                  f"(STYLEREF, TOC \\t …); it is not updated: after mapping it no longer finds these "
                                  f"paragraphs — edit the field code in Word (or map back) and update fields")
        froms = {e["from_id"] for e in active}
        for e in active:
            if e["type"] != "paragraph":
                continue
            link = sheet.styles[e["from_id"]].find(W + "link")
            lid = link.get(W + "val") if link is not None else None
            if not lid or lid in froms or lid not in sheet.styles or style_type(sheet.styles[lid]) != "character":
                continue
            n = 0
            for part in parts:
                root = pkg.xml(part)
                pars = paragraphs(root)
                groups = runs_by_paragraph(root, pars)
                for bi, p in enumerate(pars):
                    n += sum(1 for _, _, rpr in mapped_holders(p, groups[bi]) if run_style_of(rpr) == lid)
            if n:
                frag.flags.append(f"{n} run(s)/paragraph mark(s) use {lid}, the character style linked to "
                                  f"{e['from_id']}; they keep it — map it as a pair of its own if they should change")
        if older:
            frag.flags.append(f"{older} restyled paragraph(s)/run(s)/mark(s) already carried a pending tracked "
                              f"formatting change (w:pPrChange / w:rPrChange) from an earlier reviewer; reject-all would "
                              f"restore that older snapshot.")

    # ---- apply -------------------------------------------------------------------------------------------------------

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if td.get("template"):
            check_template_unchanged(td["template"], td["template_md5"])
        apply_import(pkg, td.get("import"))
        stamp = RevisionStamp.now(td["author"], td.get("use_date_utc", False)) if td["tracked"] else None
        next_id = td.get("first_rev_id", 0)
        for part, t in td["targets"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            groups = runs_by_paragraph(root, pars)
            tables = list(root.iter(W + "tbl"))
            for bi, to in t["paragraphs"]:
                next_id = set_paragraph_style(pars[bi], to, stamp, next_id)
            for bi, ri, to in t["runs"]:
                next_id = set_run_style(groups[bi][ri].find(W + "rPr"), to, stamp, next_id)
            for bi, to in t["marks"]:
                next_id = set_run_style(pars[bi].find(f"{W}pPr/{W}rPr"), to, stamp, next_id)
            for k, to in t["tables"]:
                tables[k].find(f"{W}tblPr/{W}tblStyle").set(W + "val", to)
            pkg.write_xml(part, root)


def set_paragraph_style(p, to: str, stamp: Optional[RevisionStamp], next_id: int) -> int:
    ppr = paragraph_ppr(p)
    chg = None
    if stamp is not None:
        snap = [c for c in ppr if c.tag not in (W + "rPr", W + "sectPr", W + "pPrChange")]
        chg = property_change("pPrChange", "pPr", snap, stamp, next_id)
        next_id += 1
    ps = ppr.find(W + "pStyle")
    if ps is None:
        ps = etree.Element(W + "pStyle")
        ppr.insert(0, ps)
    ps.set(W + "val", to)
    if chg is not None:
        ppr.append(chg)
    return next_id


def set_run_style(rpr, to: str, stamp: Optional[RevisionStamp], next_id: int) -> int:
    chg = None
    if stamp is not None:
        chg = property_change("rPrChange", "rPr", [c for c in rpr if c.tag != W + "rPrChange"], stamp, next_id)
        next_id += 1
    rpr.find(W + "rStyle").set(W + "val", to)
    if chg is not None:
        rpr.append(chg)
    return next_id
