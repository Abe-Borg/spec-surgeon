"""The roles of a template for Safe mode's "match body formatting to template":
which of the template's paragraph styles stands for each part of a specification, its page and its body font — read
from the template without a model.

**Detection** (``detect``): ``structure.role_summary`` over the template. A role gets a style only where the template
makes it unambiguous: the role's paragraphs share ONE style no paragraph read as something else uses
(``role_summary``'s ``style_in`` selector with one style), and that style is not the default paragraph style (Normal:
matching a role to it would restyle nothing and change Normal's look everywhere). The page is the section of the
template's first PART paragraph (the first ``w:sectPr`` at or after it, else the body's own): size, orientation and
every margin (header and footer distances included). The font is ``profile.body_text_fonts``' first, when at least half
of the template's body text is in it.

**The user's choice** (``resolve``): the form shows each role with the detected style pre-chosen; the user may change
any, or clear a section-number, title or END OF SECTION role. The numbered roles must start at PART and run without a
gap (PART, article, paragraph, …), their styles must form the template's numbering scheme (``lists.resolve_scheme``:
one list, each at its own level), no two roles may share a style, and no role may be the default paragraph style.
Anything else is refused with one sentence (``ValueError``), and the button previews nothing.

Nothing is written into the template or kept on disk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

NUMBERED = ("part", "article", "paragraph", "subparagraph", "subparagraph_2", "subparagraph_3", "subparagraph_4")
PLAIN = ("section_id", "section_title", "end_of_section")
ORDER = ("section_id", "section_title") + NUMBERED + ("end_of_section",)
LABELS = {"section_id": "Section number line", "section_title": "Section title", "part": "PART",
          "article": "Article (1.1)", "paragraph": "Paragraph (A.)", "subparagraph": "Subparagraph (1.)",
          "subparagraph_2": "Subparagraph (a.)", "subparagraph_3": "Subparagraph, level 6",
          "subparagraph_4": "Subparagraph, level 7", "end_of_section": "END OF SECTION"}
MIN_FONT_SHARE = 0.5
MARGINS = ("top", "right", "bottom", "left", "header", "footer", "gutter")


@dataclass
class TemplateRoles:
    """The template as the button uses it: its path, the style of each role, the numbering scheme's levels (styles,
    PART first), the page and the body font."""
    path: Path
    styles: Dict[str, str]
    levels: List[str]
    page: Optional[Dict[str, Any]] = None
    font: Optional[str] = None
    size: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    def level_of(self, role: str) -> Optional[int]:
        """The scheme level a numbered role takes (PART 0, article 1, …), None when the template has no such level."""
        style = self.styles.get(role)
        return self.levels.index(style) if style in self.levels else None


def _int(v: Optional[str]) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


def read_page(sect_pr) -> Optional[Dict[str, Any]]:
    """A section's page as set_page_setup takes it: ``size`` {w, h}, ``orientation`` and ``margins`` (twips); None
    without a page size."""
    from .package import W
    if sect_pr is None:
        return None
    sz = sect_pr.find(W + "pgSz")
    w, h = (_int(sz.get(W + "w")), _int(sz.get(W + "h"))) if sz is not None else (None, None)
    if not w or not h:
        return None
    mar = sect_pr.find(W + "pgMar")
    margins = {}
    for k in MARGINS:
        v = _int(mar.get(W + k)) if mar is not None else None
        if v is not None:
            margins[k] = v
    return {"size": {"w": w, "h": h}, "orientation": "landscape" if w > h else "portrait", "margins": margins}


def _page_of(pkg, part_style: Optional[str]) -> Optional[Dict[str, Any]]:
    from .package import W
    body = pkg.xml(pkg.main_part()).find(W + "body")
    if body is None:
        return None
    seen = part_style is None
    for el in body:
        if el.tag != W + "p":
            continue
        ps = el.find(W + "pPr/" + W + "pStyle")
        if not seen and ps is not None and ps.get(W + "val") == part_style:
            seen = True
        sp = el.find(W + "pPr/" + W + "sectPr")
        if seen and sp is not None:
            return read_page(sp)
    return read_page(body.find(W + "sectPr"))


def _paragraph_styles(pkg) -> List[Dict[str, str]]:
    from .ops.selectors import StyleSheet
    from .package import W
    sheet = StyleSheet(pkg)
    out = []
    for sid, st in sheet.styles.items():
        if (st.get(W + "type") or "paragraph") != "paragraph":
            continue
        name = st.find(W + "name")
        out.append({"id": sid, "name": name.get(W + "val") if name is not None and name.get(W + "val") else sid})
    return sorted(out, key=lambda s: s["name"].casefold())


def page_words(page: Optional[Dict[str, Any]]) -> str:
    """``8.5 × 11 in portrait, margins 1 in`` — the page in the words the form shows."""
    if not page:
        return ""
    inch = lambda v: f"{v / 1440:g}"  # noqa: E731
    size = f"{inch(page['size']['w'])} × {inch(page['size']['h'])} in {page['orientation']}"
    m = page["margins"]
    sides = [m.get(k) for k in ("top", "right", "bottom", "left")]
    if sides and None not in sides and len(set(sides)) == 1:
        return f"{size}, margins {inch(sides[0])} in"
    return size + ", margins " + ", ".join(f"{k} {inch(m[k])} in" for k in ("top", "right", "bottom", "left")
                                          if k in m)


def detect(template: Optional[Path]) -> Dict[str, Any]:
    """What the form shows for a template: ``roles`` (role → style id, detected), ``styles`` (the template's paragraph
    styles, {id, name}), ``labels``, ``page`` and ``font`` in words, and ``problems`` — sentences saying why the button
    cannot use the template as it is (empty when it can)."""
    empty = {"roles": {}, "styles": [], "labels": dict(LABELS), "order": list(ORDER), "page": "", "font": "",
             "problems": []}
    if template is None:
        return empty
    from .package import DocxPackage
    from .structure import role_summary
    name = Path(template).name
    try:
        pkg = DocxPackage.from_path(template)
        summary = role_summary(pkg)
    except Exception as e:  # noqa: BLE001 — a template that cannot be read is said plainly
        return {**empty, "problems": [f"{name} could not be read as a template ({type(e).__name__})."]}
    styles = _paragraph_styles(pkg)
    default = _default_style(pkg)
    roles: Dict[str, str] = {}
    for role in ORDER:
        sel = summary["selectors"].get(role) or {}
        own = sel.get("style_in") or []
        if len(own) == 1 and own[0] != default:
            roles[role] = own[0]
    out = {**empty, "roles": roles, "styles": styles}
    try:
        resolved = resolve(template, roles)
    except ValueError as e:
        out["problems"] = [str(e)]
        return out
    out["page"] = page_words(resolved.page)
    out["font"] = f"{resolved.font} {resolved.size:g} pt" if resolved.font and resolved.size else (resolved.font or "")
    return out


def _default_style(pkg) -> Optional[str]:
    from .ops.selectors import StyleSheet
    return StyleSheet(pkg).defaults.get("paragraph")


def resolve(template: Path, chosen: Dict[str, Any]) -> TemplateRoles:
    """The template with the roles the user chose (``chosen``: role → style id; "" or absent: none), or ValueError
    with the one sentence that says why it cannot be used (module docstring)."""
    from .ops.base import JobException
    from .ops.lists import ListDefs, resolve_scheme
    from .ops.selectors import StyleSheet
    from .package import DocxPackage
    from .profile import body_text_fonts
    name = Path(template).name
    if not isinstance(chosen, dict):
        raise ValueError("The template's roles must be chosen one per role.")
    try:
        pkg = DocxPackage.from_path(template)
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"{name} could not be read as a template ({type(e).__name__}).") from e
    known = {s["id"] for s in _paragraph_styles(pkg)}
    default = _default_style(pkg)
    styles: Dict[str, str] = {}
    for role, sid in chosen.items():
        if role not in LABELS:
            raise ValueError(f"There is no role “{role}”.")
        if sid in (None, ""):
            continue
        if not isinstance(sid, str) or sid not in known:
            raise ValueError(f"{LABELS[role]}: {name} has no paragraph style “{sid}”.")
        if sid == default:
            raise ValueError(f"{LABELS[role]}: the template's default paragraph style cannot stand for a role "
                             f"(every unstyled paragraph uses it).")
        styles[role] = sid
    used: Dict[str, str] = {}
    for role, sid in styles.items():
        if sid in used:
            raise ValueError(f"{LABELS[used[sid]]} and {LABELS[role]} have the same style; each role needs its own.")
        used[sid] = role
    present = [r for r in NUMBERED if r in styles]
    if not present or present[0] != "part":
        raise ValueError(f"{name}: no style stands for PART, so the template's outline numbering cannot be read. "
                         f"Choose its PART style, or use a template that shows its outline (PART, articles, "
                         f"paragraphs) in its own styles.")
    if present != list(NUMBERED[:len(present)]):
        gap = next(r for r in NUMBERED if r not in styles)
        raise ValueError(f"{name}: the outline skips {LABELS[gap]}; choose a style for it or for none of the levels "
                         f"below it.")
    levels = [styles[r] for r in present]
    try:
        resolve_scheme(levels, StyleSheet(pkg), ListDefs.load(pkg), where=name, code="bad-params")
    except JobException as e:
        raise ValueError(f"The outline styles do not form one numbering in the template: {e.reason}") from e
    page = _page_of(pkg, styles.get("part"))
    warnings: List[str] = []
    if page is None:
        warnings.append(f"{name} has no page size; the pages are left as they are.")
    font = size = None
    try:
        top = body_text_fonts(pkg)
    except Exception:  # noqa: BLE001
        top = []
    if top and top[0]["share"] >= MIN_FONT_SHARE and top[0]["font"]:
        font, size = top[0]["font"], top[0]["size_pt"]
    else:
        warnings.append(f"{name} has no one body font; the fonts are left as they are.")
    return TemplateRoles(Path(template).resolve(), styles, levels, page, font, size, warnings)


def chosen_roles(inputs: Dict[str, Any], detected: Dict[str, str]) -> Dict[str, str]:
    """The roles of the form: the user's choice for each role it names, the detected style for the others."""
    raw = inputs.get("roles")
    if raw is None:
        return dict(detected)
    if not isinstance(raw, dict):
        raise ValueError("The template's roles must be chosen one per role.")
    out = dict(detected)
    for role, sid in raw.items():
        out[str(role)] = sid
    return out


def describe(roles: TemplateRoles) -> List[str]:
    """The roles in words, for the plan's assumptions: ``PART: “PRT”``."""
    return [f"{LABELS[r]}: “{roles.styles[r]}”" for r in ORDER if r in roles.styles]


def numbered_present(roles: Sequence[str]) -> List[str]:
    return [r for r in NUMBERED if r in roles]
