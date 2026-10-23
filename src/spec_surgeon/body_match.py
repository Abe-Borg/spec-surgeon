"""Safe mode's "match body formatting to template" (docs/PLAN-2026-10-23-modes.md M3): one plan per document, built in
code from the document's own structure and the template's roles (``template_roles``) — never by a model.

**Per document** (``plan_file``, on the unmodified source; ``structure.role_summary`` and its rows):

* The outline: the numbered roles it holds (PART, article, paragraph, …). None → refused ("no outline"); a role deeper
  than the template's outline → refused. All AUTOMATIC → ``convert_numbering`` from the main list (``from: "main"``),
  its levels mapped role by role onto the template's (``level_map``; each role at ONE level of its list, every role in
  the main list — else refused), table cells left out (``include_tables: false``), every number keeping its counters
  (``counters: "keep"``: "1.01" becomes the template's "1.1"; a number that would count differently sets the document
  aside), the template's definitions replacing the document's own where the ids meet (``existing: "replace"``). All
  TYPED → ``convert_typed_numbers`` with ``match: "counters"`` (the typed values must be the list's: a typo, a skip or a
  duplicate sets the document aside), untracked. Typed and automatic mixed → refused.
* The section number line, the title and END OF SECTION: ``apply_style`` with the template's role style (imported,
  ``existing: "replace"``), selected by the selector ``role_summary`` makes unambiguous in this file (the title, when it
  has none, by its place: ``index_range``) — used only when it selects exactly the paragraphs read as that role; a role
  the document lacks, or the template has no style for, is left as it is.
* The page (the form's "page" box): ``set_page_setup`` with the template's size and margins on every section, when every
  section is in the template's orientation (a document with a landscape page keeps its pages).
* The paragraph formatting (the form's "clear" box): ``clear_direct_formatting`` of paragraphs outside tables — only
  ``CLEARED`` (spacing, indents, alignment), runs untouched: bold, italic and underlined words stay.
* A guard: a template role style the document already has (the same id) takes the template's definition; when
  paragraphs the plan does not restyle also use it (directly or through ``w:basedOn``) — in a table cell the
  conversion leaves out, a header, a footer, a note or a comment as much as in the body — the document is refused:
  their look would change unasked.
* The template's look on the outline (the form's "font" box): every role style the plan imports is pinned, after the
  import, to the font and size it resolves to in the template (``template_look``) — an imported style is based on the
  document's own Normal, whose font and size are the document's; and the body text outside tables the plan does not
  restyle (``FilePlan.plain_styles``) takes the body font on its runs in the same last step.

**Per layout** (``build``): the documents of one structure pattern (``fingerprint.pattern_id``) whose plans agree run
one plan; the largest agreeing group wins and each other document is left out with the reason. The font step (the form's
"font" box; ``font_ops``: the template's body font, and its size on the text in the documents' own body sizes) comes
first after the page and reaches the body outside tables only, as the button promises: ``set_font`` on its runs,
``set_style_font`` on the styles nothing else uses (``FilePlan.font_styles``: no table cell, header, footer, note or
comment uses them; every document of the layout has them), ``set_doc_defaults_font`` only when nothing outside the
body could inherit the defaults (``FilePlan.defaults_free``). Each layout's spec passes the one gate
(``compiler.compose_submission``) on its own documents — overlapping operations become ordered steps; a layout the gate
refuses leaves its documents out with the gate's reason. Every change is plain (no tracked changes), and no word is
placed.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .template_roles import LABELS, NUMBERED, PLAIN, TemplateRoles, read_page

CLEARED = ("spacing", "contextualSpacing", "ind", "mirrorIndents", "jc")


class Refused(ValueError):
    """A document the button cannot match as it is: one sentence."""


@dataclass
class FilePlan:
    path: Path
    ops: List[Dict[str, Any]]            # page, styles, numbering, clearing — the font step is added per layout
    kind: str                            # "automatic" or "typed"
    body_size: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    look: List[Dict[str, Any]] = field(default_factory=list)  # the template's look on its imported styles: last step
    font_styles: List[str] = field(default_factory=list)   # styles only the body outside tables uses (font step)
    plain_styles: List[str] = field(default_factory=list)  # the styles of body text outside tables it does not restyle
    defaults_free: bool = False                            # nothing outside the body outside tables has paragraphs

    @property
    def key(self) -> str:
        return json.dumps([self.kind, self.ops, self.look], sort_keys=True)


def _style_chain(sheet, sid: Optional[str]) -> List[str]:
    from .package import W
    out: List[str] = []
    while sid and sid in sheet.styles and sid not in out:
        out.append(sid)
        b = sheet.styles[sid].find(W + "basedOn")
        sid = b.get(W + "val") if b is not None else None
    return out


def _uses(pars, sheet) -> set:
    """Every style the paragraphs use: their paragraph styles and their runs' character styles, each with its
    ``w:basedOn`` chain."""
    from .ops.lists import paragraph_style
    from .package import W
    out: set = set()
    for p in pars:
        out.update(_style_chain(sheet, paragraph_style(p, sheet)))
        for rs in p.iter(W + "rStyle"):
            out.update(_style_chain(sheet, rs.get(W + "val")))
    return out


def _elsewhere(pkg, main: str) -> List[Tuple[str, Any]]:
    """``(part, paragraph)`` of every paragraph outside the main part's body text: the headers, footers, notes and
    comments (the main part's table cells are the caller's)."""
    from .ooxml import paragraphs
    return [(part, p) for part in pkg.text_parts() if part != main for p in paragraphs(pkg.xml(part))]


def template_look(roles: TemplateRoles, style_ids: List[str]) -> List[Tuple[str, Optional[float], List[str]]]:
    """``(font, size, style ids)`` groups: the font and size each of ``style_ids`` resolves to in the template (its
    ``w:basedOn`` chain, then the template's document defaults; a theme font through the template's theme). A style
    whose font the template leaves to a theme it cannot resolve is left out."""
    from lxml import etree
    from .ops.effective import Resolver, theme_fonts
    from .ops.selectors import StyleSheet
    from .package import DocxPackage, W
    pkg = DocxPackage.from_path(roles.path)
    resolver = Resolver(StyleSheet(pkg), theme_fonts(pkg))
    groups: Dict[Tuple[str, Optional[float]], List[str]] = {}
    for sid in style_ids:
        if sid not in resolver.styles:
            continue
        p = etree.fromstring(f'<w:p xmlns:w="{W[1:-1]}"><w:pPr><w:pStyle w:val="{sid}"/></w:pPr></w:p>')
        look = resolver.run(None, p)
        font = look.get("ascii")
        if not font or font.startswith("theme:"):
            continue
        size = int(look["sz"]) / 2 if look.get("sz") else None
        groups.setdefault((font, size), []).append(sid)
    return [(font, size, ids) for (font, size), ids in groups.items()]


def _clip(text: str, n: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def plan_file(path: Path, roles: TemplateRoles, *, page: bool = True, clear: bool = True,
              font: bool = False) -> FilePlan:
    """The document's own plan (module docstring), or ``Refused`` with the reason. ``font``: the outline takes the
    template's look (``template_look``); the font step itself is the layout's (``build_layouts``)."""
    from .ooxml import block_full_text, paragraphs
    from .ops.clear_formatting import ClearSettings
    from .ops.convert_numbering import in_table
    from .ops.lists import ListDefs, count, main_abstract, paragraph_style
    from .ops.selectors import StyleSheet, paragraph_selected
    from .package import DocxPackage
    from .profile import body_text_fonts
    from .structure import analyze_structure, role_summary
    pkg = DocxPackage.from_path(path)
    rows = analyze_structure(pkg)["paragraphs"]
    summary = role_summary(pkg)
    main_part = pkg.main_part()
    root = pkg.xml(main_part)
    pars = paragraphs(root)
    cells = {i for i, p in enumerate(pars) if in_table(p)}
    sheet = StyleSheet(pkg)
    template = str(roles.path)
    present = [r for r in NUMBERED if r in summary["roles"]]
    if not present:
        raise Refused("no outline the button can read: no PART, article or paragraph was recognized in it")
    deeper = [r for r in present if roles.level_of(r) is None]
    if deeper:
        raise Refused(f"its outline goes down to {LABELS[deeper[0]]}, which the template has no style for")
    kinds = {summary["roles"][r]["numbering"] for r in present}
    if kinds not in ({"automatic"}, {"typed"}):
        raise Refused("its outline mixes numbers Word writes with numbers typed as text")
    kind = kinds.pop()
    ops: List[Dict[str, Any]] = []
    notes: List[str] = []
    targeted: set = set()
    applied: List[str] = []                 # the template styles this plan imports, in order

    # ---- the page ----
    if page and roles.page:
        from .package import DocxPackage as _P
        pages = [read_page(sp) for sp in _P.sect_prs(root)]
        if pages and all(p is not None and p["orientation"] == roles.page["orientation"] for p in pages):
            ops.append({"op": "set_page_setup", "select": {"sections": "all"},
                        "params": {"size": dict(roles.page["size"]), "margins": dict(roles.page["margins"])}})
        else:
            notes.append("a page in another orientation: its pages are left as they are")

    # ---- the section number line, the title, END OF SECTION ----
    members_of = {r: sorted(row["paragraph"] for row in rows if row["role"] == r) for r in PLAIN}
    for role in PLAIN:
        style = roles.styles.get(role)
        members = members_of[role]
        if not style or not members:
            continue
        sel = (summary["selectors"].get(role) or None)
        if sel is None and role == "section_title":
            if set(members) & set(members_of["section_id"]) or members != list(range(members[0], members[-1] + 1)):
                notes.append(f"the {LABELS[role].lower()} keeps its style (it cannot be told apart by its place)")
                continue
            sel = {"index_range": [members[0], members[-1] + 1]}
        if sel is None:
            notes.append(f"the {LABELS[role].lower()} keeps its style (no one selector finds it)")
            continue
        picked = [i for i, p in enumerate(pars)
                  if paragraph_selected(sel, p, i, block_full_text(p), sheet, all_pars=pars)]
        if picked != members:
            notes.append(f"the {LABELS[role].lower()} keeps its style (its selector also finds other paragraphs)")
            continue
        targeted |= set(members)
        applied.append(style)
        ops.append({"op": "apply_style", "select": {"paragraphs": sel},
                    "params": {"style": style, "template": template, "existing": "replace",
                               "direct_formatting": "keep", "tracked": False}})

    # ---- the outline ----
    if kind == "automatic":
        defs = ListDefs.load(pkg)
        counted = count(root, sheet, defs)
        main = main_abstract(counted)
        level_map: Dict[int, int] = {}
        for role in present:
            numbered = [row["numbering"] for row in rows if row["role"] == role and row.get("numbering")]
            levels = {n["level"] for n in numbered}
            if len(levels) != 1:
                raise Refused(f"its {LABELS[role]} paragraphs sit at more than one level of their list")
            if any(defs.abstract_of(n["num_id"]) != main for n in numbered):
                raise Refused("its outline is numbered by more than one list")
            source, target = levels.pop(), roles.level_of(role)
            if level_map.get(source, target) != target:
                raise Refused(f"two parts of its outline share level {source + 1} of their list")
            level_map[source] = target
        # table cells are left out of the conversion (include_tables: false): they keep their look, and the guard
        # below holds them to it
        targeted |= {x.index for x in counted if x.abstract_id == main and x.index not in cells}
        params: Dict[str, Any] = {"levels": list(roles.levels), "template": template, "existing": "replace",
                                  "counters": "keep", "direct_formatting": "keep", "tracked": False,
                                  "allow_missing": False}
        moved = {str(k): v for k, v in sorted(level_map.items()) if k != v}
        if moved:
            params["level_map"] = moved
        ops.append({"op": "convert_numbering", "select": {"include_tables": False}, "params": params})
    else:
        targeted |= {row["paragraph"] for row in rows if row["role"] in present and row["paragraph"] not in cells}
        ops.append({"op": "convert_typed_numbers", "select": {"include_tables": False},
                    "params": {"levels": list(roles.levels), "template": template, "existing": "replace",
                               "match": "counters", "tracked": False, "allow_missing": False}})

    # ---- the guard: a style the template replaces that other paragraphs use ----
    applied += [sid for sid in roles.levels if sid not in applied]
    imported = set(roles.styles.values())
    others = [(p, _uses([p], sheet)) for p in [p for i, p in enumerate(pars) if i not in targeted]
              + [p for _, p in _elsewhere(pkg, main_part)]]
    for sid in sorted(imported & set(sheet.styles)):
        stray = [p for p, uses in others if sid in uses]
        if stray:
            raise Refused(f"its style “{sid}” would take the template's definition, and {len(stray)} paragraph(s) the "
                          f"button does not restyle use it (e.g. ‘{_clip(block_full_text(stray[0]))}’)")

    # ---- the template's look on the outline (the font box): a step of its own, after the import ----
    look: List[Dict[str, Any]] = []
    if font:
        for look_font, look_size, ids in template_look(roles, applied):
            look.append({"op": "set_style_font", "select": {"styles": {"ids": ids}},
                        "params": {"font": look_font, **({"size": look_size} if look_size else {}),
                                   "only_where_defined": False}})

    # ---- the paragraph formatting ----
    if clear:
        keep = sorted(set(ClearSettings().para_names) - set(CLEARED))
        ops.append({"op": "clear_direct_formatting", "select": {"paragraphs": {"in_table": False}},
                    "params": {"run": False, "paragraph": True, "keep": keep, "tracked": False}})
    try:
        top = body_text_fonts(pkg)
    except Exception:  # noqa: BLE001
        top = []
    # the font step's reach: the styles only the body outside tables uses (none the import replaces), and whether
    # anything outside it could inherit the document defaults
    outside = [p for i, p in enumerate(pars) if i in cells] + [p for _, p in _elsewhere(pkg, main_part)]
    body_styles = _uses([p for i, p in enumerate(pars) if i not in cells], sheet)
    font_styles = sorted(body_styles - _uses(outside, sheet) - imported)
    plain = {paragraph_style(p, sheet) for i, p in enumerate(pars) if i not in cells and i not in targeted}
    return FilePlan(Path(path), ops, kind, top[0]["size_pt"] if top and top[0]["size_pt"] else None, notes,
                    look=look, font_styles=font_styles, defaults_free=not outside,
                    plain_styles=sorted(x for x in plain - imported if x))


def font_ops(font: str, size: Optional[float], size_from: List[float], styles: List[str] = (),
             defaults: bool = False) -> List[Dict[str, Any]]:
    """The font step, on the body outside tables only: its runs that name a font, the ``styles`` only it uses and,
    with ``defaults``, the document defaults take ``font``; with ``size``, the text in the documents' own body sizes
    (``size_from``) takes it too."""
    sized = {"size": size, "size_from": size_from} if size and size_from else {}
    ops = [{"op": "set_font", "select": {"scope": ["document"], "runs": "all", "paragraphs": {"in_table": False}},
            "params": {"font": font, **sized, "only_where_defined": True, "tracked": False}}]
    if styles:
        ops.append({"op": "set_style_font", "select": {"styles": {"ids": list(styles)}},
                    "params": {"font": font, **sized}})
    if defaults:
        ops.append({"op": "set_doc_defaults_font", "params": {"font": font, **sized}})
    return ops


def _reason(errors: List[str]) -> str:
    """A gate's refusal as the sentence beside a document: the file's label, the op's number and the part's name
    dropped, the paragraph shown by its words."""
    import re
    lines = [e for e in errors if not e.endswith(":")] or errors
    text = "; ".join(lines[:2])
    text = re.sub(r"\[[a-z-]+\] ", "", text)
    text = re.sub(r"(^|; )[^;:]*?: op #\d+ \([a-z_]+\): ", r"\1", text)
    text = re.sub(r"word/[A-Za-z0-9_]+\.xml paragraph \d+ \((['\"])(.*?)\1\)", r"‘\2’", text)
    text = re.sub(r"word/[A-Za-z0-9_]+\.xml: ", "", text)
    return text[:600]


def build_layouts(roles: TemplateRoles, ctx, request: str, *, page: bool, font: bool, clear: bool,
                  assumptions: Optional[List[str]] = None
                  ) -> Tuple[Optional[Dict[str, Any]], Dict[str, str], List[str]]:
    """``(layout plan or None, {document: reason left out}, assumptions)`` — module docstring."""
    from . import compiler
    from .layouts import layout_spec, plan_from_submission, validate_layout_plan
    batch = ctx.batch()
    left: Dict[str, str] = {}
    layouts: List[Dict[str, Any]] = []
    for pat in batch.prof["patterns"]:
        plans: List[FilePlan] = []
        for f in batch.pattern_paths(pat):
            key = str(Path(f).resolve())
            try:
                plans.append(plan_file(Path(f), roles, page=page, clear=clear, font=font and bool(roles.font)))
            except Refused as e:
                left[key] = f"Not matched: {e}."
            except Exception as e:  # noqa: BLE001 — a document that cannot be read is left out, with why
                left[key] = f"Not matched: it could not be read ({type(e).__name__})."
        if not plans:
            continue
        tally = Counter(p.key for p in plans)
        winner = max(tally, key=lambda k: (tally[k], -[p.key for p in plans].index(k)))
        chosen = [p for p in plans if p.key == winner]
        for p in plans:
            if p.key != winner:
                left[str(p.path.resolve())] = ("Not matched with the others: it is built like "
                                               f"{chosen[0].path.name} but its outline or styles differ. Press the "
                                               "button with it alone, or try it in Default mode.")
        ops = list(chosen[0].ops)
        if font and roles.font:
            sizes = sorted({p.body_size for p in chosen if p.body_size})
            shared = sorted(set.intersection(*(set(p.font_styles) for p in chosen)))
            at = 1 if ops and ops[0]["op"] == "set_page_setup" else 0
            ops[at:at] = font_ops(roles.font, roles.size, sizes, shared, all(p.defaults_free for p in chosen))
        mode = "untracked" if chosen[0].kind == "typed" else "formatting"
        n = len(layouts) + 1
        what = "typed numbers" if chosen[0].kind == "typed" else "numbered by Word"
        layout = {"id": f"L{n}", "label": f"outline {what}: {', '.join(p.path.name for p in chosen[:3])}"
                                          + (" …" if len(chosen) > 3 else ""),
                  "pattern_ids": [pat["pattern_id"]],
                  "spec": {"request": request, "summary": "Match the body formatting to the template.", "mode": mode,
                           "ops": ops, "assumptions": list(assumptions or [])
                           + list(dict.fromkeys(n for p in chosen for n in p.notes))}}
        stub = {"kind": "layouts", "request": request, "layouts": [layout]}
        outcome, errors = compiler.compose_submission(layout_spec(stub, layout), batch, [request],
                                                      patterns=[pat["pattern_id"]], leave_out=list(left))
        look_ops = list(chosen[0].look)
        plain = sorted(set().union(*(p.plain_styles for p in chosen)))
        if font and roles.font and plain:
            # body text the plan does not restyle takes the body font on its runs (after the restyle, so the
            # template's own styles keep their fonts): its style may be one a table or a header shares (Normal), which
            # the font step leaves alone
            sized = {"size": roles.size, "size_from": sizes} if roles.size and sizes else {}
            look_ops.append({"op": "set_font", "select": {"scope": ["document"], "runs": "all",
                                                          "paragraphs": {"style_in": plain, "in_table": False}},
                             "params": {"font": roles.font, **sized, "only_where_defined": False, "tracked": False}})
        if not errors and look_ops:
            # the imported styles take the template's font and size in a step of their own, planned on the copy
            # that holds them (the composer orders only ops that overlap in the source)
            from .chain import chain_from_specs
            from .editspec import is_chain
            steps = ([(st["name"], st["spec"]) for st in outcome["stages"]] if is_chain(outcome)
                     else [("match", outcome)])
            look = {"request": request, "summary": "The template's font and size on the styles it brought.",
                    "mode": "formatting", "ops": look_ops}
            outcome, errors = compiler.compose_submission(chain_from_specs(steps + [("template look", look)]), batch,
                                                          [request], patterns=[pat["pattern_id"]],
                                                          leave_out=list(left))
        if errors:
            why = _reason(errors)
            for p in chosen:
                left[str(p.path.resolve())] = f"Not matched: {why}"
            continue
        layout["spec"] = outcome
        layouts.append(layout)
    if not layouts:
        return None, left, []
    plan = plan_from_submission({"summary": "Match the body formatting to the template.",
                                 "assumptions": list(assumptions or []), "layouts": layouts}, request)
    validate_layout_plan(plan)
    return plan, left, list(assumptions or [])
