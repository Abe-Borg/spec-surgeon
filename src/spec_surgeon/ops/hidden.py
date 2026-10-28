"""Ops: remove_hidden_text (built-in batch 2, item 14) and remove_notes (item 15) — docs/BUILTIN_OPS_BATCH2_DESIGN.md §5,
§6; the whole-run and paragraph removals of §1.4 (ops/removal.py), hidden and colour of §1.6 (ops/markup.py), fields of
§1.5 (ops/fields.py). The verifier derives every decision again with its own code (verify/removal.py, kinds
``removed_hidden_text`` and ``removed_notes``): keep the two identical (CLAUDE.md lists the definitions).

**Content runs** of a paragraph: its runs (nearest ``w:p`` is the paragraph, document order) that are live (in no
``w:del``/``w:moveFrom``) and hold something besides ``w:rPr``.

**Hidden text.** A content run is hidden when its effective ``w:vanish`` is on (``Look.hidden``; ambiguous →
``toggle-ambiguous``). A paragraph is a *hidden paragraph* when its mark is hidden (``Look.mark_hidden``) and every content
run is hidden (none at all counts) — it is deleted; otherwise its hidden content runs are removed (whole runs).

**Notes.** A paragraph is a note when one of the ``rules`` matches — within a rule every given test holds: ``style_in``
(the paragraph's style id, the default paragraph style when it names none), ``text_regex`` (``re.search`` in the
batch-1 run text), ``color_in`` (it has a content run holding visible text — a ``w:t`` with a non-space character — and
every such run's effective colour is listed), ``hidden`` (it has a content run and every one is hidden). A note paragraph
is deleted. ``runs`` picks inline notes in the other paragraphs: content runs whose effective colour is listed
(``color_in``) and/or that are hidden (``hidden``) — never a run inside a ``w:hyperlink`` (link text is often blue) or
one holding a picture (``w:drawing``, ``w:pict``, ``w:object``); they are removed (whole runs).

**What a deletion may not do** (both ops, ``keep_rules``; checked per container — the parent of the paragraph: body,
cell, header, footer, note, comment, block content control — over its blocks ``w:p``, ``w:tbl``, ``w:sdt``,
``w:customXml``, ``w:altChunk``): leave two tables side by side (Word would join them: the first paragraph between them
stays), delete the container's last block when tracked (Word cannot delete the final paragraph mark), or — untracked —
leave the container empty or ending in something other than a paragraph. A paragraph such a rule keeps, or (hidden
text only) one ``deletable`` will not delete for another reason than a text box, loses its content runs instead (its
hidden ones / all of them) and keeps its mark (flagged). In the body, once those are known, the *last section* — the
blocks after the last paragraph that carries a section break (``w:pPr/w:sectPr``) and stays (a hidden one kept because
``deletable`` will not delete it counts) — keeps a paragraph (``ops/deletion.last_section_keep``, batch 6's keep rule
4): when every one of its blocks is chosen and none of them kept, its last chosen paragraph is kept too, demoted the
same way, flagged either way (the section shows a blank page; the break's paragraph alone at the end of the body would
show one with nothing on it to remove). A note paragraph ``deletable`` refuses is refused — unless it is the one the
last section keeps (it is not deleted).

**Runs in fields.** A selected run inside a complex field's span (begin to end, document order over the part) goes only
when the outermost such field lies in this paragraph and every content run of its span is selected and live — then all
of them go; otherwise ``field-result`` (a ``w:fldSimple`` inside the span: ``field-result``). A selected run inside a
``w:fldSimple`` (the outermost) goes only when every content run inside it is selected and live: untracked the simple
field goes whole; tracked it is refused (``field-result`` — a simple field cannot be deleted as a tracked change). A
selected run that is not reachable through containers alone (``textedit.CONTAINERS``) is refused (``structure``); one in a
text box, or holding one, ``textbox``.

**Range marks** (untracked; ``removal.kept_marks``): a bookmark, permission, move or custom-XML range mark in a deleted
paragraph or a removed simple field, some mark of whose pair lies in what stays, stays — in the place of the paragraph
or field it was in (the plan records it as ``marks``: per paragraph, its indices among the paragraph's range marks), so
a bookmark keeps both ends and a REF field to it still finds it; flagged. A pair wholly removed goes. Tracked, nothing
moves: a deleted paragraph keeps its marks until the deletion is accepted.

Both ops list every removed piece as a flag (part, paragraph, the words) — the preview shows them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..ooxml import RevisionStamp, file_uses_date_utc, max_revision_id, paragraphs
from ..package import DocxPackage, W
from . import prose, register
from .base import JobException, Op, PlanFragment
from .patterns import compile_pattern
from .deletion import last_section_keep
from .fields import fields_of
from .markup import Look, color_in, color_names, valid_color
from .removal import (deletable, delete_paragraph, has_content, kept_marks, live, plan_paragraph_deletion,
                      plan_run_removal, range_marks, remove_in_place, remove_runs)
from .replace_table import COMMON_PARAMS, default_author, resolve_scope, tracked_of
from .selectors import SCOPE_KINDS, StyleSheet, scope_parts
from .textedit import CONTAINERS, RunText

BLOCK_TAGS = (W + "p", W + "tbl", W + "sdt", W + "customXml", W + "altChunk")
KEPT_BECAUSE = {"section-break": "it carries a section break", "field-result": "it holds a field",
                "existing-revisions": "it carries pending tracked changes",
                "structure": "it holds a hyperlink, a content control or a comment mark"}
PICTURE_TAGS = (W + "drawing", W + "pict", W + "object")
HIDDEN_SCOPE = ["document", "headers", "footers", "footnotes", "endnotes"]
NOTE_SCOPE = ["document"]
RULE_KEYS = ("style_in", "text_regex", "color_in", "hidden")
RUN_RULE_KEYS = ("color_in", "hidden")

# the batch-2 clean-up ops: a file with nothing to remove is a no-op by default (whole batches run them)
CLEANUP_PARAMS = {
    **{k: v for k, v in COMMON_PARAMS.items() if k != "scope"},
    "allow_missing": {"type": "boolean", "default": True,
                      "description": "true (the default) = a file with nothing to remove plans as a no-op; false = "
                                     "such a file is set aside (not-found)."},
}


# ---- the plan's prose (describe): what the clean-up ops share -------------------------------------------------------

def scope_phrase(select: Optional[Dict[str, Any]], params: Optional[Dict[str, Any]], default: Sequence[str]) -> str:
    """``in the document body, the headers and the footers``: the parts the op works in, the body named when alone."""
    scope = (select or {}).get("scope") or (params or {}).get("scope") or list(default)
    return prose.scope_words(scope) or "in the document body"


def tracked_words(params: Optional[Dict[str, Any]]) -> str:
    """``shown as tracked changes`` (``by “Jane”``, ``dated in UTC``) for the ops that track by default; ``without
    tracked changes`` when the user switched tracking off."""
    params = params or {}
    if params.get("tracked", True) is False:
        return "without tracked changes"
    text = "shown as tracked changes"
    if params.get("author"):
        text += f" by {prose.quote(params['author'])}"
    if params.get("date_convention") == "utc":
        text += " dated in UTC"
    return text


def set_aside_remark(params: Optional[Dict[str, Any]], what: str) -> str:
    """The non-default ``allow_missing``: a file with nothing to remove is set aside; empty for the default."""
    if (params or {}).get("allow_missing", True) is False:
        return f"a file with no {what} is set aside rather than passed through"
    return ""


def _note_paragraph_words(rule: Dict[str, Any]) -> str:
    """One note rule as the reader says it: ``those in the “Note” style whose text matches the pattern “^Retain” and
    is all coloured “0000FF”``."""
    head = "those"
    if isinstance(rule.get("style_in"), list) and rule["style_in"]:
        head += " in " + prose.styles(list(rule["style_in"]))
    tests: List[str] = []
    if rule.get("text_regex"):
        tests.append("matches the pattern " + prose.quote(rule["text_regex"]))
    if isinstance(rule.get("color_in"), list) and rule["color_in"]:
        tests.append("is all coloured " + prose.join((prose.colour(c) for c in rule["color_in"]), "or"))
    if rule.get("hidden") is True:
        tests.append("is all hidden")
    if tests:
        head += " whose text " + " and ".join(tests)
    return head if head != "those" else "any paragraph"


def _inline_note_words(rule: Any) -> str:
    """``hidden text coloured “0000FF” or “FF0000”``: the inline-note rule; empty when there is none."""
    if not isinstance(rule, dict):
        return ""
    colours = rule.get("color_in") if isinstance(rule.get("color_in"), list) else []
    hidden = rule.get("hidden") is True
    if colours:
        return ("hidden text" if hidden else "text") + " coloured " + prose.join((prose.colour(c) for c in colours), "or")
    return "hidden text" if hidden else ""


def _two_sentences(first: str, remarks: List[str]) -> str:
    remarks = [r for r in remarks if r]
    return first if not remarks else first + " " + prose.sentence("; ".join(remarks))


def nearest_p(el) -> Optional[Any]:
    q = el.getparent()
    while q is not None and q.tag != W + "p":
        q = q.getparent()
    return q


def own_runs(p) -> List[Any]:
    """The paragraph's runs: every w:r whose nearest w:p is ``p``, in document order."""
    return [r for r in p.iter(W + "r") if nearest_p(r) is p]


def own_simple_fields(p) -> List[Any]:
    return [s for s in p.iter(W + "fldSimple") if nearest_p(s) is p]


def content_runs(p) -> List[Any]:
    return [r for r in own_runs(p) if live(r, p) and has_content(r)]


def contained(r, p) -> bool:
    q = r.getparent()
    while q is not None and q is not p:
        if q.tag not in CONTAINERS:
            return False
        q = q.getparent()
    return q is p


def ancestor_below(el, p, tag: str) -> Optional[Any]:
    """The outermost ``tag`` ancestor of ``el`` below ``p``."""
    found, q = None, el.getparent()
    while q is not None and q is not p:
        if q.tag == tag:
            found = q
        q = q.getparent()
    return found


def visible_text(r) -> bool:
    return any((t.text or "").strip() for t in r.findall(W + "t"))


def holds_picture(r) -> bool:
    return any(c.tag in PICTURE_TAGS for c in r)


def blocks_of(container) -> List[Any]:
    return [c for c in container if c.tag in BLOCK_TAGS]


def keep_rules(blocks: Sequence[Any], chosen: Sequence[Any], tracked: bool) -> List[Any]:
    """The chosen paragraphs (of one container) a deletion must keep: the first paragraph between two tables the
    deletions would join, then the container's last block when tracked — or, untracked, when the rest would leave the
    container empty or ending in a non-paragraph."""
    sel = set(chosen)
    keep: List[Any] = []
    for i, b in enumerate(blocks):
        if b.tag != W + "tbl":
            continue
        j = i + 1
        while j < len(blocks) and blocks[j] in sel:
            j += 1
        if j < len(blocks) and j > i + 1 and blocks[j].tag == W + "tbl" and blocks[i + 1] not in keep:
            keep.append(blocks[i + 1])
    last = blocks[-1] if blocks else None
    if last is not None and last in sel and last not in keep:
        if tracked:
            keep.append(last)
        else:
            rest = [b for b in blocks if b not in sel or b in keep]
            if not rest or rest[-1].tag != W + "p":
                keep.append(last)
    return keep


class FieldSpans:
    """Where the part's complex fields run: their begin-to-end spans over the part's runs in document order."""

    def __init__(self, root):
        self.runs = list(root.iter(W + "r"))
        index = {id(r): k for k, r in enumerate(self.runs)}           # the list holds the proxies: ids stay
        self.spans: List[Tuple[int, int, Any]] = []
        for f in fields_of(root):
            if f.simple is not None or f.begin is None or f.end is None:
                continue
            rb, re_ = f.begin.getparent(), f.end.getparent()
            if id(rb) in index and id(re_) in index and index[id(rb)] <= index[id(re_)]:
                self.spans.append((index[id(rb)], index[id(re_)], f))
        self.index = index

    def outermost(self, r) -> Optional[Tuple[int, int, Any]]:
        k = self.index.get(id(r))
        if k is None:
            return None
        hits = [s for s in self.spans if s[0] <= k <= s[1]]
        return min(hits, key=lambda s: (s[0], -s[1])) if hits else None


def apply_field_rules(p, chosen: Sequence[Any], spans: FieldSpans, tracked: bool, where: str
                      ) -> Tuple[List[Any], List[Any]]:
    """The runs and simple fields that go for the chosen runs of ``p`` (the module docstring's rules), or a refusal."""
    sel = set(chosen)
    simples: List[Any] = []
    for r in chosen:
        if not contained(r, p):
            raise JobException(f"{where}: a run to remove sits in something other than a hyperlink, content control "
                               f"or tracked change (a ruby, an equation …); remove it in Word", "structure",
                               {"where": where})
        if r.find(f".//{W}p") is not None:
            raise JobException(f"{where}: a run to remove holds a text box (Word keeps two copies of it)", "textbox",
                               {"where": where})
        span = spans.outermost(r)
        if span is not None:
            b, e, f = span
            if f.paragraph() is not p:
                raise JobException(f"{where}: text to remove lies in a field that runs over more than one paragraph "
                                   f"(a table of contents …)", "field-result", {"where": where})
            in_span = [x for x in spans.runs[b:e + 1] if has_content(x)]
            if any(nearest_p(x) is not p or not live(x, p) or x not in sel for x in in_span):
                raise JobException(f"{where}: text to remove lies in a field ({f.type or 'a field'}) that is not "
                                   f"removed as a whole (part of it stays, or it holds a pending tracked change); the "
                                   f"field would show its result again when Word updates it", "field-result",
                                   {"where": where})
            span_ids = {id(x) for x in spans.runs[b:e + 1]}
            if any(any(id(x) in span_ids for x in s.iter(W + "r")) for s in own_simple_fields(p)):
                raise JobException(f"{where}: a simple field lies inside a field to remove", "field-result",
                                   {"where": where})
            continue
        s = ancestor_below(r, p, W + "fldSimple")
        if s is None:
            continue
        if tracked:
            raise JobException(f"{where}: text to remove is a field's result ({(s.get(W + 'instr') or '').split()[:1]}); "
                               f"a simple field cannot be deleted as a tracked change — remove it untracked or in "
                               f"Word", "field-result", {"where": where})
        inner = [x for x in s.iter(W + "r") if has_content(x)]
        if any(x not in sel or not live(x, p) for x in inner):
            raise JobException(f"{where}: text to remove is part of a field's result; the rest of the field stays, "
                               f"and Word would show the whole result again when it updates fields", "field-result",
                               {"where": where})
        if s not in simples:
            simples.append(s)
    runs = [r for r in chosen if not any(s in list(r.iterancestors(W + "fldSimple")) for s in simples)]
    return runs, simples


def describe(runs: Sequence[Any], limit: int = 80) -> str:
    """Runs as the preview names them: their words, ⇥ for a tab, ⏎ for a break, a field by its code in braces, a
    picture as [picture] — whitespace collapsed, clipped."""
    out = []
    for r in runs:
        for c in r:
            if c.tag in (W + "t", W + "delText"):
                out.append(c.text or "")
            elif c.tag in (W + "tab", W + "ptab"):
                out.append("⇥")
            elif c.tag in (W + "br", W + "cr"):
                out.append("⏎")
            elif c.tag in (W + "instrText", W + "delInstrText"):
                out.append("{" + " ".join((c.text or "").split()) + "}")
            elif c.tag in PICTURE_TAGS:
                out.append("[picture]")
    s = " ".join("".join(out).split())
    return s if len(s) <= limit else s[:limit - 1] + "…"


def pieces(p, runs: Sequence[Any]) -> List[str]:
    """The removed runs as the reader would name them: one piece per stretch of adjacent runs of the paragraph."""
    own = own_runs(p)
    chosen = set(runs)
    out, cur = [], []
    for r in own:
        if r in chosen:
            cur.append(r)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [describe(g) for g in out if describe(g)]


class _Removing(Op):
    """What remove_hidden_text and remove_notes share: choose paragraphs and runs, apply the container and field rules,
    predict, apply."""
    mode = "content"
    kind = ""
    default_scope: List[str] = HIDDEN_SCOPE
    what = "hidden text"
    undeletable = "demote"                    # hidden text: keep the paragraph, remove its runs; notes: "refuse"

    def choose(self, part: str, bi: int, p, look: Look, sheet: StyleSheet, params: Dict[str, Any]
               ) -> Tuple[bool, List[Any]]:  # pragma: no cover
        """(delete the paragraph, the runs to remove otherwise)."""
        raise NotImplementedError

    def demoted_runs(self, p, look: Look) -> List[Any]:  # pragma: no cover
        raise NotImplementedError

    def assertion(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        select = dict(select or {})
        if set(select) - {"scope"}:
            raise JobException(f"{self.name} selects by scope only; unknown {sorted(set(select) - {'scope'})}",
                               "bad-params")
        scope = resolve_scope(select, params, self.default_scope)
        parts = scope_parts(pkg, scope)
        tracked = tracked_of(params)
        if params.get("date_convention", "word-local-z") not in ("word-local-z", "utc"):
            raise JobException("date_convention must be 'word-local-z' or 'utc'", "bad-params")
        allow_missing = params.get("allow_missing", True)
        if not isinstance(allow_missing, bool):
            raise JobException("allow_missing must be true or false", "bad-params")
        self.check_params(params)
        author = params.get("author") or default_author()
        frag = self.fragment({"scope": scope}, {**params, "tracked": tracked, "author": author},
                             mode="content" if tracked else "untracked")
        frag.untracked_diffs_expected = not tracked
        sheet = StyleSheet(pkg)
        look = Look(sheet)
        data: Dict[str, Dict[str, Any]] = {}
        n_par = n_run = 0
        for part in parts:
            frag.touches.append((part, "scope", "verify"))          # V10 re-derives over every part in scope
            root = pkg.xml(part)
            got = self._plan_part(frag, part, root, look, sheet, params, tracked)
            if got["delete"] or got["runs"] or got["simple"]:
                data[part] = got
                n_par += len(got["delete"])
                n_run += sum(len(v) for v in got["runs"].values()) + sum(len(v) for v in got["simple"].values())
        # the user's own words (scope and tracked as asked; absent = the default): the verifier reads them itself
        frag.property_assertions.append({"kind": self.kind, "scope": select.get("scope") or params.get("scope"),
                                         **{k: params[k] for k in ("tracked",) if k in params}, "author": author,
                                         **self.assertion(params), "expected": data})
        if not data:
            if allow_missing:
                frag.notes.append(f"{self.name}: no {self.what} in {scope}: nothing to do.")
                return frag
            raise JobException(f"{self.name}: no {self.what} in {scope}", "not-found", {"scope": scope})
        frag.rev_id_base = max_revision_id(pkg) + 1
        frag.rev_ids_needed = sum(v for k, v in frag.rev_delta.items() if v > 0)
        frag.geometry_reference = self.geometry
        frag.geometry_reason = self.geometry_reason
        frag.transform_data = {"parts": data, "tracked": tracked, "author": author,
                               "use_date_utc": file_uses_date_utc(pkg), "first_rev_id": frag.rev_id_base,
                               "date_convention": params.get("date_convention", "word-local-z")}
        frag.notes.append(f"{self.name} ({'tracked' if tracked else 'untracked'}): {n_par} paragraph(s) deleted and "
                          f"{n_run} run(s) or field(s) removed in {len(data)} part(s); every piece is listed below.")
        if not tracked:
            frag.flags.append(f"UNTRACKED removal of {self.what} at the user's request: no revision marks.")
        return frag

    geometry = "source"
    geometry_reason = ""

    def check_params(self, params: Dict[str, Any]) -> None:
        return None

    def _plan_part(self, frag: PlanFragment, part: str, root, look: Look, sheet: StyleSheet, params: Dict[str, Any],
                   tracked: bool) -> Dict[str, Any]:
        pars = paragraphs(root)
        chosen_p: List[int] = []
        runs_of: Dict[int, List[Any]] = {}
        for bi, p in enumerate(pars):
            whole, runs = self.choose(part, bi, p, look, sheet, params)
            if (whole or runs) and nearest_p(p) is not None:
                raise JobException(f"{part} paragraph {bi}: {self.what} inside a text box (Word keeps two copies of "
                                   f"it); remove it in Word", "textbox", {"where": f"{part} paragraph {bi}"})
            if whole:
                chosen_p.append(bi)
            elif runs:
                runs_of[bi] = runs
        # the container rules, per container
        by_container: Dict[int, Tuple[Any, List[Any]]] = {}
        for bi in chosen_p:
            c = pars[bi].getparent()
            by_container.setdefault(id(c), (c, []))[1].append(pars[bi])
        index = {id(p): bi for bi, p in enumerate(pars)}
        kept: Dict[int, str] = {}
        for c, chosen in by_container.values():
            for p in keep_rules(blocks_of(c), chosen, tracked):
                kept[index[id(p)]] = "it ends its cell, document or header — or stands between two tables"
        refused: List[Tuple[int, JobException]] = []
        for bi in chosen_p:
            if bi in kept:
                continue
            p = pars[bi]
            if p.find(f".//{W}p") is not None:
                raise JobException(f"{part} paragraph {bi}: {self.what} holds a text box; remove it in Word", "textbox",
                                   {"where": f"{part} paragraph {bi}"})
            try:
                deletable(p, part, bi, tracked)
            except JobException as e:
                if self.undeletable == "refuse":
                    refused.append((bi, e))
                else:
                    kept[bi] = KEPT_BECAUSE.get(e.code, e.code)
        # the last section keeps a paragraph: after the paragraphs that stay as undeletable (a hidden section break
        # among them), before the refusals (the paragraph it keeps is not deleted)
        last_section: List[int] = []
        body = next((c for c, _ in by_container.values() if c.tag == W + "body"), None)
        if body is not None:
            keep = [pars[bi] for bi in kept]
            last_section = [index[id(p)] for p in last_section_keep(blocks_of(body), {pars[bi] for bi in chosen_p},
                                                                     keep, f"{part}: the document body")]
            for bi in last_section:
                kept.setdefault(bi, "the last section would be empty without it")
        for bi, e in refused:
            if bi not in kept:
                raise e
        delete = [bi for bi in chosen_p if bi not in kept]
        for bi, why in kept.items():
            runs = self.demoted_runs(pars[bi], look)
            if runs:
                runs_of[bi] = runs
            frag.flags.append(f"{part} paragraph {bi}: kept its paragraph mark ({why}); its {self.what} is removed")
        if last_section:
            stays = (f"paragraph {last_section[0]} stays" if len(last_section) == 1 else
                     f"paragraphs {prose.join(str(bi) for bi in last_section)} stay")
            frag.flags.append(f"the last section is now empty and shows a blank page ({stays}); remove the section "
                              f"break before it to drop the page")
        spans = FieldSpans(root)
        out: Dict[str, Any] = {"delete": sorted(delete), "runs": {}, "simple": {}}
        untracked_deletion = False
        removed: List[Any] = []                   # untracked: the paragraphs and simple fields that leave the part
        for bi in sorted(set(delete) | set(runs_of)):
            p = pars[bi]
            where = f"{part} paragraph {bi}"
            if bi in delete:
                text = describe(content_runs(p))
                plan_paragraph_deletion(frag, part, bi, p, tracked, f"{self.what} paragraph deleted")
                frag.flags.append(f"{where}: removed {self.piece_par} paragraph «{text}»" if text else
                                  f"{where}: removed an empty {self.piece_par} paragraph")
                untracked_deletion = untracked_deletion or not tracked
                removed.append(p)
                continue
            runs, simples = apply_field_rules(p, runs_of[bi], spans, tracked, where)
            if not runs and not simples:
                continue
            gone = runs + [x for s in simples for x in s.iter(W + "r")]
            plan_run_removal(frag, part, bi, p, runs, tracked, extra=simples, note=f"{self.what} removed")
            own, sims = own_runs(p), own_simple_fields(p)
            if runs:
                out["runs"][str(bi)] = [own.index(r) for r in runs]
            if simples:
                out["simple"][str(bi)] = [sims.index(s) for s in simples]
                removed += simples
            frag.flags.append(f"{where}: removed {self.piece} " + ", ".join(f"«{t}»" for t in pieces(p, gone)))
        if not tracked:
            # the bookmarks and range marks running out of what goes into what stays: each stays in its place
            for top, marks in kept_marks(root, removed).items():
                bi = index[id(top if top.tag == W + "p" else nearest_p(top))]
                ranges = range_marks(pars[bi])
                out.setdefault("marks", {}).setdefault(str(bi), []).extend(ranges.index(m) for m in marks)
                thing = "paragraph" if top.tag == W + "p" else "field"
                frag.flags.append(f"{part} paragraph {bi}: {len(marks)} bookmark or range mark(s) in the removed "
                                  f"{thing} run on into text that stays; each stays where the {thing} was, so its "
                                  f"bookmark keeps both ends")
            for v in out.get("marks", {}).values():
                v.sort()
        if untracked_deletion:
            frag.touches.append((part, "*", "text"))
        return out

    piece = "hidden text"                     # how the preview's list names a removed run …
    piece_par = "hidden"                      # … and a removed paragraph

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td or not td.get("parts"):
            return
        tracked = td["tracked"]
        stamp = RevisionStamp.now(td["author"], td["use_date_utc"], td.get("date_convention", "word-local-z"))
        next_id = [td["first_rev_id"]]
        for part, d in td["parts"].items():
            root = pkg.xml(part)
            pars = paragraphs(root)
            runs, simples = [], []
            for bi, ks in d["runs"].items():
                own = own_runs(pars[int(bi)])
                runs += [own[k] for k in ks]
            for bi, ks in d["simple"].items():
                sims = own_simple_fields(pars[int(bi)])
                simples += [sims[k] for k in ks]
            doomed = [pars[bi] for bi in d["delete"]]
            # the planned range marks that stay, by the paragraph or simple field whose place they take
            held: Dict[Any, List[Any]] = {}
            for bi, ks in d.get("marks", {}).items():
                p, ranges = pars[int(bi)], range_marks(pars[int(bi)])
                for k in ks:
                    top = p if int(bi) in d["delete"] else next(a for a in ranges[k].iterancestors() if a in simples)
                    held.setdefault(top, []).append(ranges[k])
            remove_runs(runs, tracked, stamp, next_id)
            for s in simples:
                remove_in_place(s, held.get(s, ()))
            for p in doomed:
                delete_paragraph(p, tracked, stamp, next_id, held.get(p, ()))
            pkg.write_xml(part, root)


def _hidden(look: Look, r, p, where: str) -> bool:
    h = look.hidden(r.find(W + "rPr"), p)
    if h is None:
        raise JobException(f"{where}: whether this text is hidden depends on 'hidden' being set at several style "
                           f"levels (Word XORs them — not modelled)", "toggle-ambiguous", {"where": where})
    return h


def _mark_hidden(look: Look, p, where: str) -> bool:
    h = look.mark_hidden(p)
    if h is None:
        raise JobException(f"{where}: whether the paragraph mark is hidden depends on 'hidden' being set at several "
                           f"style levels (Word XORs them — not modelled)", "toggle-ambiguous", {"where": where})
    return h


@register
class RemoveHiddenText(_Removing):
    name = "remove_hidden_text"
    kind = "removed_hidden_text"
    what = "hidden text"
    piece = "hidden text"
    default_scope = HIDDEN_SCOPE
    undeletable = "demote"
    geometry = "source"
    description = ("Remove hidden text (Word's 'Hidden' font setting, set directly or by a style): hidden runs are "
                   "removed and a paragraph whose text and paragraph mark are all hidden is deleted; every removed "
                   "piece is listed in the preview. Tracked (default) or untracked.")
    params_schema = {"type": "object", "properties": {
        **CLEANUP_PARAMS,
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document, headers, footers, footnotes, endnotes)"}}}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document, headers, footers, footnotes, endnotes)"}}}
    verification_profile = {
        "V2": "untracked: each edited paragraph's text without the removed runs (a structure-only declaration when "
              "only a tab or mark goes), each deleted paragraph listed; tracked: block texts unchanged",
        "V4": "tracked: +1 del per removed run, +1 del per direct run and +1 del_mark per deleted paragraph; untracked: "
              "−(revisions inside what goes)",
        "V8": "tracked: every edited paragraph's accepted view without the hidden text, a deleted one empty",
        "V10": "removed_hidden_text re-derives every hidden run and paragraph (its own reading of 'hidden') and checks "
               "each paragraph item by item; deleted paragraphs as a tracked deletion or gone; nothing else changes",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        first = prose.sentence("Remove the hidden text (Word's “Hidden” font setting, whether set directly or by a style)",
                               scope_phrase(select, params, HIDDEN_SCOPE) + f", {tracked_words(params)}")
        return _two_sentences(first, ["a paragraph whose text and paragraph mark are all hidden is deleted whole, and "
                                      "every removed piece is listed in the preview",
                                      set_aside_remark(params, "hidden text")])

    def choose(self, part, bi, p, look, sheet, params):
        where = f"{part} paragraph {bi}"
        content = content_runs(p)
        hidden = [r for r in content if _hidden(look, r, p, where)]
        if len(hidden) == len(content) and _mark_hidden(look, p, where):
            return True, []
        return False, hidden

    def demoted_runs(self, p, look):
        return [r for r in content_runs(p) if look.hidden(r.find(W + "rPr"), p)]


def _rule(raw: Any, where: str, keys: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not raw:
        raise JobException(f"{where}: a rule is an object with one or more of {list(keys)}", "bad-params")
    unknown = set(raw) - set(keys)
    if unknown:
        raise JobException(f"{where}: unknown key(s) {sorted(unknown)} (known: {list(keys)})", "bad-params")
    out: Dict[str, Any] = {}
    if "style_in" in raw:
        v = raw["style_in"]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x for x in v):
            raise JobException(f"{where}: style_in must be a list of style ids", "bad-params")
        out["style_in"] = list(v)
    if "text_regex" in raw:
        v = raw["text_regex"]
        try:
            compile_pattern(v, where="a paragraph's text")
        except JobException as e:
            raise JobException(f"{where}: bad text_regex {v!r} ({e.reason})", e.code, e.details) from None
        if not v:
            raise JobException(f"{where}: text_regex is empty", "bad-params")
        out["text_regex"] = v
    if "color_in" in raw:
        v = raw["color_in"]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and valid_color(x) for x in v):
            raise JobException(f"{where}: color_in must list colours as 6 hex digits (0000FF), a theme colour name "
                               f"(accent1 …) or auto, got {v!r}", "bad-params")
        out["color_in"] = list(v)
    if "hidden" in raw:
        if raw["hidden"] is not True:
            raise JobException(f"{where}: hidden can only be true (leave it out otherwise)", "bad-params")
        out["hidden"] = True
    return out


def note_rules(params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    rules = params.get("rules") or []
    if not isinstance(rules, list):
        raise JobException("rules must be a list of paragraph rules", "bad-params")
    out = [_rule(r, f"rules[{i}]", RULE_KEYS) for i, r in enumerate(rules)]
    runs = params.get("runs")
    run_rule = _rule(runs, "runs", RUN_RULE_KEYS) if runs is not None else None
    if not out and run_rule is None:
        raise JobException("say what a note is: 'rules' (note paragraphs) and/or 'runs' (inline notes)", "bad-params")
    return out, run_rule


@register
class RemoveNotes(_Removing):
    name = "remove_notes"
    kind = "removed_notes"
    what = "notes"
    piece = "note"
    piece_par = "note"
    default_scope = NOTE_SCOPE
    undeletable = "refuse"
    geometry = "none"
    geometry_reason = "deleting note paragraphs reflows the content after them"
    description = ("Remove specifier/editor notes: paragraphs matching any of 'rules' (each rule ANDs style_in, "
                   "text_regex, color_in, hidden) are deleted, and inline note runs ('runs': color_in and/or hidden) "
                   "are removed from the other paragraphs; every removed note is listed in the preview. Tracked "
                   "(default) or untracked.")
    params_schema = {"type": "object", "properties": {
        "rules": {"type": "array", "items": {"type": "object", "properties": {
            "style_in": {"type": "array", "items": {"type": "string"},
                         "description": "style ids (no pStyle = the default paragraph style)"},
            "text_regex": {"type": "string", "description": "Python regex searched in the paragraph's text, e.g. "
                                                            "^(Retain|Delete|Revise|Coordinate)\\b"},
            "color_in": {"type": "array", "items": {"type": "string"},
                         "description": "every run with visible text has one of these colours: 6 hex digits "
                                        "(0000FF), a theme colour name (accent1 …) or auto"},
            "hidden": {"type": "boolean", "description": "true: every run with content is hidden"}}},
            "description": "a paragraph is a note when any rule matches; within a rule every given test holds"},
        "runs": {"type": "object", "properties": {
            "color_in": {"type": "array", "items": {"type": "string"}},
            "hidden": {"type": "boolean"}},
            "description": "inline notes: runs of these colours (and/or hidden) in the paragraphs that stay — never "
                           "hyperlink text or pictures"},
        **CLEANUP_PARAMS,
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document)"}}}
    select_schema = {"type": "object", "properties": {
        "scope": {"type": "array", "items": {"type": "string", "enum": list(SCOPE_KINDS)},
                  "description": "part kinds (default: document)"}}}
    verification_profile = {
        "V2": "untracked: each deleted note paragraph listed, each paragraph that lost inline notes changed; tracked: "
              "block texts unchanged",
        "V4": "tracked: +1 del per removed run, +1 del per direct run and +1 del_mark per deleted paragraph; untracked: "
              "−(revisions inside what goes)",
        "V8": "tracked: accepted views without the notes",
        "V10": "removed_notes re-derives every note paragraph and inline note (its own style, text, colour and hidden "
               "readings) and checks each paragraph item by item; nothing else changes",
    }

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        select, params = dict(select or {}), dict(params or {})
        rules = [r for r in params["rules"] if isinstance(r, dict) and r] if isinstance(params.get("rules"), list) else []
        inline = _inline_note_words(params.get("runs"))
        where = scope_phrase(select, params, NOTE_SCOPE)
        note_paragraphs = "Delete the note paragraphs" + (
            " (" + prose.join((_note_paragraph_words(r) for r in rules), "or") + ")" if rules else "")
        if rules and inline:
            head = f"{note_paragraphs} and remove the inline notes ({inline}) from the other paragraphs {where}"
        elif rules:
            head = f"{note_paragraphs} {where}"
        elif inline:
            head = f"Remove the inline notes ({inline}) from the paragraphs {where}"
        else:
            head = f"Delete the note paragraphs and remove the inline notes {where}"
        first = prose.sentence(f"{head}, {tracked_words(params)}")
        return _two_sentences(first, ["hyperlink text and pictures are never taken as notes, and every removed note is "
                                      "listed in the preview", set_aside_remark(params, "such note")])

    def check_params(self, params):
        self._rules = note_rules(params)

    def assertion(self, params):
        # the user's rules as given: the verifier reads them with its own code, never through this op's normalisation
        return {"rules": params.get("rules") or [], "runs": params.get("runs")}

    def choose(self, part, bi, p, look, sheet, params):
        rules, run_rule = self._rules
        where = f"{part} paragraph {bi}"
        content = content_runs(p)
        for rule in rules:
            if self._matches(rule, p, content, look, sheet, where):
                return True, []
        if run_rule is None:
            return False, []
        wanted = color_names(run_rule.get("color_in") or [])
        out = []
        for r in content:
            if ancestor_below(r, p, W + "hyperlink") is not None or holds_picture(r):
                continue
            if "color_in" in run_rule and not color_in(look.color(r.find(W + "rPr"), p), wanted):
                continue
            if run_rule.get("hidden") and not _hidden(look, r, p, where):
                continue
            out.append(r)
        return False, out

    @staticmethod
    def _matches(rule, p, content, look, sheet, where) -> bool:
        if "style_in" in rule and sheet.paragraph_style_id(p) not in rule["style_in"]:
            return False
        if "text_regex" in rule and compile_pattern(rule["text_regex"], where="a paragraph's text").search(
                RunText(p).text) is None:
            return False
        if "color_in" in rule:
            wanted = color_names(rule["color_in"])
            vis = [r for r in content if visible_text(r)]
            if not vis or not all(color_in(look.color(r.find(W + "rPr"), p), wanted) for r in vis):
                return False
        if rule.get("hidden"):
            if not content or not all(_hidden(look, r, p, where) for r in content):
                return False
        return True

    def demoted_runs(self, p, look):
        return content_runs(p)
