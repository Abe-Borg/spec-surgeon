"""Op: remove_unused_styles — remove the style definitions nothing uses (built-in batch 4, item 32 —
docs/BUILTIN_OPS_BATCH4_DESIGN.md §8).

Formatting mode, the styles part only: zero text diffs, no change to any text part, to the numbering part or to the
settings part; never tracked (Word does not track style definitions either). Removing a definition nothing uses changes
nothing a page shows, so the source render stays the layout reference.

Parameters: ``keep`` — style ids or names never removed; ``which`` — ``all`` (default) or ``custom`` (only styles with
``w:customStyle`` on: the architect's own, never Word's built-ins); ``types`` — the style types considered (default all
four: paragraph, character, table, numbering); ``allow_missing`` (default true: a file with nothing to remove is a no-op
with a note; false sets it aside, ``not-found``).

Definitions (CLAUDE.md lists them among the definitions that must stay identical on both sides; the verifier's reading
is verify/unused.py):

* **Styles** — the top-level ``w:style`` children of the styles part (the main part's ``styles`` relationship). Two with
  one ``w:styleId`` refuse (``structure``). Type = ``w:type`` (default ``paragraph``); default = ``w:default`` on
  (1/true/on); custom = ``w:customStyle`` on; a style's **names** = its id, its ``w:name/@w:val`` and every
  comma-separated, stripped entry of ``w:aliases/@w:val`` — each casefolded, empty ones dropped.
* **Scanned parts** — every text part (main, headers, footers, footnotes, endnotes, comments: the main part's
  relationships) and every numbering and settings part the main part's relationships name.
* **Referenced** — the ``w:val`` of every ``w:pStyle``, ``w:rStyle``, ``w:tblStyle``, ``w:numStyleLink``,
  ``w:styleLink``, ``w:defaultTableStyle`` and ``w:clickAndTypeStyle`` anywhere in a scanned part — the snapshots of
  tracked property changes (a reject-all brings them back), ``w:sdtPr``, text boxes and ``mc:Fallback`` copies included.
* **Named by fields** — per text part, the casefolded concatenation, in document order, of every ``w:instrText`` /
  ``w:delInstrText`` text and every ``w:fldSimple/@w:instr``: a style is named when one of its names occurs in it
  (STYLEREF "Arch Field Only", TOC ``\\t "Arch Article,2"``). **Field-generated** — a text part whose instruction text
  holds ``toc`` keeps the styles named ``toc 1`` … ``toc 9`` and ``table of figures``; ``index``: ``index 1`` …
  ``index 9`` and ``index heading``; ``toa``: ``table of authorities`` and ``toa heading`` — the styles a field update
  applies (design decision 7: nothing a field update could bring back into use goes).
* **Kept by the user** — a style one of whose names equals a ``keep`` entry, stripped and casefolded.
* **Used** = the default styles ∪ referenced ∪ named by fields ∪ field-generated ∪ kept by the user.
* **Eligible** — a style with a ``w:styleId`` whose type is listed in ``types`` and, with ``which: custom``, custom.
* **Stays** — the used styles and every style that is not eligible (an id-less one included), closed under the
  ``w:val`` of ``w:basedOn``, ``w:next`` and ``w:link``: no style that stays names one that goes (with ``which:
  custom`` Word's built-in Heading 2 stays, so its linked "Heading 2 Char" stays too).
* **Removed** — every eligible style whose id does not stay, in the styles part's order.

Verified by V10's ``removed_styles`` kind (verify/unused.py), which re-derives all of the above on its own, registers the
removed ids for the styles residual (the output's styles part = the source's minus exactly those) and checks on the
OUTPUT that every style reference above, every ``w:basedOn``/``w:next``/``w:link`` and every style a field instruction
names still finds its definition.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from ..package import REL_TYPE_BASE, W, DocxPackage
from . import register
from .base import JobException, Op, PlanFragment
from .selectors import StyleSheet

STYLE_TYPES = ("paragraph", "character", "table", "numbering")
WHICH = ("all", "custom")
ON = ("1", "true", "on")
# the elements whose w:val names a style, wherever they stand in a scanned part
REF_NAMES = ("pStyle", "rStyle", "tblStyle", "numStyleLink", "styleLink", "defaultTableStyle", "clickAndTypeStyle")
REF_TAGS = frozenset(W + n for n in REF_NAMES)
CHAIN_TAGS = (W + "basedOn", W + "next", W + "link")
NUMBERING_REL = REL_TYPE_BASE + "numbering"
SETTINGS_REL = REL_TYPE_BASE + "settings"
# a field update applies these styles (by their built-in names, which Word stores in English whatever the language)
FIELD_STYLES = {
    "toc": tuple(f"toc {i}" for i in range(1, 10)) + ("table of figures",),
    "index": tuple(f"index {i}" for i in range(1, 10)) + ("index heading",),
    "toa": ("table of authorities", "toa heading"),
}
SHOWN = 30                                         # ids listed in a note before "… and N more"
PARAMS = ("keep", "which", "types", "allow_missing", "tracked")

Table = List[Tuple[Optional[str], Any]]           # (styleId or None, the w:style element), in the part's order


def clean_params(params: Dict[str, Any]) -> Tuple[List[str], str, Tuple[str, ...], bool]:
    """(keep entries, which, types, allow_missing) — validated (``bad-params``)."""
    unknown = sorted(set(params) - set(PARAMS))
    if unknown:
        raise JobException(f"remove_unused_styles: unknown parameter(s) {unknown}; known: {list(PARAMS)}", "bad-params")
    if params.get("tracked"):
        raise JobException("removing style definitions is not tracked (Word does not track style definitions either); "
                           "omit 'tracked' or set it false", "bad-params")
    keep = params.get("keep", [])
    if not isinstance(keep, list) or not all(isinstance(k, str) and k.strip() for k in keep):
        raise JobException("'keep' must be a list of style ids or names (non-empty strings)", "bad-params")
    which = params.get("which", "all")
    if which not in WHICH:
        raise JobException(f"'which' must be one of {list(WHICH)}, got {which!r}", "bad-params")
    types = params.get("types", list(STYLE_TYPES))
    if not isinstance(types, list) or not types or not all(isinstance(t, str) for t in types):
        raise JobException(f"'types' must be a non-empty list of style types {list(STYLE_TYPES)}", "bad-params")
    if set(types) - set(STYLE_TYPES):
        raise JobException(f"unknown style type(s) {sorted(set(types) - set(STYLE_TYPES))}; known: "
                           f"{list(STYLE_TYPES)}", "bad-params")
    allow = params.get("allow_missing", True)
    if not isinstance(allow, bool):
        raise JobException("'allow_missing' must be true or false", "bad-params")
    return list(keep), which, tuple(t for t in STYLE_TYPES if t in types), allow


# ---- reading the styles part --------------------------------------------------------------------------------------

def fold(s: str) -> str:
    return s.casefold()


def style_table(root) -> Table:
    """The top-level ``w:style`` children with their ids; ``structure`` for two with one id."""
    out: Table = []
    seen: Set[str] = set()
    for el in root:
        if el.tag != W + "style":
            continue
        sid = el.get(W + "styleId")
        if sid is not None:
            if sid in seen:
                raise JobException(f"the styles part defines style {sid!r} twice; which definition Word uses is not "
                                   f"certain", "structure", {"style": sid})
            seen.add(sid)
        out.append((sid, el))
    return out


def style_type(el) -> str:
    return el.get(W + "type") or "paragraph"


def is_custom(el) -> bool:
    return el.get(W + "customStyle") in ON


def is_default(el) -> bool:
    return el.get(W + "default") in ON


def style_names(sid: Optional[str], el) -> List[str]:
    """The style's names, casefolded: its id, its w:name and every alias (comma-separated, stripped); empty ones out."""
    raw = [sid or ""]
    name = el.find(W + "name")
    if name is not None:
        raw.append(name.get(W + "val") or "")
    aliases = el.find(W + "aliases")
    if aliases is not None:
        raw += (aliases.get(W + "val") or "").split(",")
    return [fold(n.strip()) for n in raw if n.strip()]


def eligible(sid: Optional[str], el, which: str, types: Sequence[str]) -> bool:
    return sid is not None and style_type(el) in types and (which == "all" or is_custom(el))


# ---- what uses a style --------------------------------------------------------------------------------------------

def scanned_parts(pkg: DocxPackage) -> Tuple[List[str], List[str]]:
    """(the text parts, the numbering and settings parts) the main part's relationships name."""
    main = pkg.main_part()
    others = []
    for r in pkg.rels(main).values():
        if r.type in (NUMBERING_REL, SETTINGS_REL) and r.resolved and pkg.has(r.resolved) and r.resolved not in others:
            others.append(r.resolved)
    return pkg.text_parts(), others


def references(root) -> Set[str]:
    """The w:val of every style-referencing element anywhere in the part (tracked-change snapshots included)."""
    out: Set[str] = set()
    for el in root.iter():
        if el.tag in REF_TAGS:
            v = el.get(W + "val")
            if v is not None:
                out.add(v)
    return out


def instruction_text(root) -> str:
    """The part's field instructions — w:instrText / w:delInstrText texts and w:fldSimple/@w:instr, in document order —
    run together and casefolded."""
    pieces: List[str] = []
    for el in root.iter():
        if el.tag in (W + "instrText", W + "delInstrText"):
            pieces.append(el.text or "")
        elif el.tag == W + "fldSimple":
            pieces.append(el.get(W + "instr") or "")
    return fold("".join(pieces))


def named_in_fields(table: Table, texts: Iterable[str]) -> Set[str]:
    texts = [t for t in texts if t]
    out: Set[str] = set()
    for sid, el in table:
        if sid is not None and any(n in t for n in style_names(sid, el) for t in texts):
            out.add(sid)
    return out


def field_generated(table: Table, texts: Iterable[str]) -> Set[str]:
    wanted: Set[str] = set()
    for t in texts:
        for key, names in FIELD_STYLES.items():
            if key in t:
                wanted.update(names)
    return {sid for sid, el in table if sid is not None and set(style_names(sid, el)) & wanted}


def kept_by(table: Table, keep: Sequence[str]) -> Tuple[Set[str], List[str]]:
    """(the ids the keep entries name, the entries that name no style)."""
    ids: Set[str] = set()
    unmatched: List[str] = []
    for k in keep:
        key = fold(k.strip())
        hit = {sid for sid, el in table if sid is not None and key in style_names(sid, el)}
        ids |= hit
        if not hit:
            unmatched.append(k)
    return ids, unmatched


def seed_styles(table: Table, by_id: Dict[str, Any], used: Set[str], which: str, types: Sequence[str]) -> List[Any]:
    """The styles the closure starts from: the used ones and every style that is not eligible (it stays whatever the
    op decides, so what it names must stay too)."""
    return [by_id[s] for s in sorted(used) if s in by_id] + [el for sid, el in table if not eligible(sid, el, which, types)]


def closure(seed: Sequence[Any], by_id: Dict[str, Any]) -> Set[str]:
    """The ids of the seed styles and of every style they reach through w:basedOn, w:next and w:link."""
    stays: Set[str] = set()
    queue = list(seed)
    for el in queue:
        sid = el.get(W + "styleId")
        if sid is not None:
            stays.add(sid)
    while queue:
        el = queue.pop()
        for tag in CHAIN_TAGS:
            ref = el.find(tag)
            v = ref.get(W + "val") if ref is not None else None
            if v is None or v in stays:
                continue
            stays.add(v)
            if v in by_id:
                queue.append(by_id[v])
    return stays


def analyze(pkg: DocxPackage, styles_part: str, keep: Sequence[str], which: str,
            types: Sequence[str]) -> Dict[str, Any]:
    """Every decision of the op on this package (module docstring), from the SOURCE."""
    table = style_table(pkg.xml(styles_part))
    by_id = {sid: el for sid, el in table if sid is not None}
    text_parts, others = scanned_parts(pkg)
    referenced: Set[str] = set()
    texts: List[str] = []
    for part in text_parts + others:
        root = pkg.xml(part)
        referenced |= references(root)
        if part in text_parts:
            texts.append(instruction_text(root))
    defaults = {sid for sid, el in table if sid is not None and is_default(el)}
    named = named_in_fields(table, texts)
    generated = field_generated(table, texts)
    kept, unmatched = kept_by(table, keep)
    used = defaults | referenced | named | generated | kept
    stays = closure(seed_styles(table, by_id, used, which, types), by_id)
    removed = [sid for sid, el in table if eligible(sid, el, which, types) and sid not in stays]
    return {"removed": removed, "table": table, "named": named, "generated": generated, "kept": kept,
            "unmatched": unmatched, "stays": stays}


def _kind(which: str, types: Sequence[str]) -> str:
    """'style', 'custom style', 'paragraph or character style' … — what the op considered."""
    what = " or ".join(types) + " style" if len(types) < len(STYLE_TYPES) else "style"
    return ("custom " if which == "custom" else "") + what


def _listed(ids: Sequence[str]) -> str:
    shown = ", ".join(ids[:SHOWN])
    return shown + (f", … and {len(ids) - SHOWN} more" if len(ids) > SHOWN else "")


@register
class RemoveUnusedStyles(Op):
    name = "remove_unused_styles"
    mode = "formatting"
    description = ("Remove the style definitions nothing in the document uses (styles.xml only). A style counts as used "
                   "when a paragraph, run or table names it (in the body, headers, footers, footnotes, endnotes or "
                   "comments — tracked-change snapshots included), when the numbering or the settings name it, when a "
                   "field instruction names it (STYLEREF, TOC \\t), when it is a default style, when it is kept by "
                   "'keep', or when a style that stays is based on it, follows with it or links to it. Adds no "
                   "characters; never tracked.")
    params_schema = {"type": "object", "properties": {
        "keep": {"type": "array", "items": {"type": "string"},
                 "description": "style ids or names never removed (names and aliases matched without regard to case)"},
        "which": {"type": "string", "enum": list(WHICH), "default": "all",
                  "description": "all = every unused style, Word's built-ins included; custom = only styles marked "
                                 "custom (w:customStyle), the document's own"},
        "types": {"type": "array", "items": {"type": "string", "enum": list(STYLE_TYPES)},
                  "description": "the style types considered (default all four)"},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "true (the default) = a file with nothing to remove plans as a no-op; false = "
                                         "such a file is set aside (not-found)"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema: Dict[str, Any] = {}
    verification_profile = {
        "V1": "styles.xml only", "V2": "zero text diffs", "V4": "no revision added",
        "V10": "removed_styles: the used styles and the removable list re-derived from the source and matched with the "
               "plan; the output's styles part = the source's minus exactly those; every style reference, "
               "basedOn/next/link and field-named style in the output still finds its definition",
        "V7": "source — an unused definition shows on no page"}

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException(f"remove_unused_styles takes no selector (got {sorted(select)}); use 'keep', 'which' "
                               f"and 'types'", "bad-params")
        keep, which, types, allow = clean_params(params)
        sheet = StyleSheet(pkg)
        frag = self.fragment({}, params)
        a = {"kind": "removed_styles", "styles_part": sheet.part,
             "params": {k: params[k] for k in ("keep", "which", "types") if k in params}, "expected_removed": []}
        frag.property_assertions.append(a)
        frag.transform_data = {"styles_part": sheet.part, "removed": []}
        if sheet.part is None:
            if not allow:
                raise JobException("this file has no styles part: there are no styles to remove", "not-found")
            frag.notes.append("remove_unused_styles: this file has no styles part — nothing to do.")
            return frag
        found = analyze(pkg, sheet.part, keep, which, types)
        removed = found["removed"]
        table = dict((sid, el) for sid, el in found["table"] if sid is not None)
        if found["unmatched"]:
            frag.notes.append(f"keep: {found['unmatched']} name(s) no style of this file")
        if not removed:
            if not allow:
                raise JobException(f"no unused {_kind(which, types)} to remove in this file", "not-found",
                                   {"which": which, "types": list(types)})
            frag.notes.append(f"remove_unused_styles: no unused {_kind(which, types)} in this file — nothing to do.")
            return frag
        a["expected_removed"] = removed
        frag.transform_data["removed"] = removed
        frag.intended_parts = [sheet.part]
        # each removal is every aspect of that style: an op in the same spec applying it (apply_style), mapping to it,
        # inserting with it (they read it, "verify") or changing its definition is a plan-time conflict, never a
        # dangling reference
        frag.touches = [(sheet.part, f"style:{sid}", aspect) for sid in removed
                        for aspect in ("remove", "rPr", "pPr", "tblPr")]
        by_type: Dict[str, int] = {}
        for sid in removed:
            by_type[style_type(table[sid])] = by_type.get(style_type(table[sid]), 0) + 1
        frag.notes.append(f"{len(removed)} unused style(s) removed ("
                          + ", ".join(f"{n} {t}" for t, n in sorted(by_type.items())) + f"): {_listed(removed)}")
        frag.notes.append(f"{len(found['stays'] & set(table))} style(s) stay: the default styles, those the text, the "
                          f"numbering or the settings name (tracked-change snapshots included), those a field "
                          f"instruction names" + (", those kept by 'keep'" if keep else "")
                          + (", every style not considered (which/types)" if which != "all" or len(types) < 4 else "")
                          + ", and every style one of those is based on, followed by or linked to")
        if found["named"]:
            frag.notes.append(f"named by field instructions (kept): {_listed(sorted(found['named']))}")
        if found["generated"]:
            frag.notes.append(f"applied by a field update (TOC, INDEX, TOA — kept): {_listed(sorted(found['generated']))}")
        builtin = [sid for sid in removed if not is_custom(table[sid])]
        if builtin:
            frag.notes.append(f"{len(builtin)} of them are Word built-in styles; Word brings back its own definition "
                              f"when one is used later ('which: custom' keeps them)")
        frag.notes.append("used means used in this file as it is before this spec: a style another op in the same spec "
                          "stops using stays — remove unused styles as the last stage of a chain to catch those")
        if pkg.has("word/stylesWithEffects.xml"):
            frag.flags.append("word/stylesWithEffects.xml (Word 2010's compatibility copy of the styles) is not "
                              "updated; Word 2013+ ignores it.")
        return frag

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td.get("removed"):
            return
        root = pkg.xml(td["styles_part"])
        gone = set(td["removed"])
        found: Set[str] = set()
        for el in list(root):
            if el.tag == W + "style" and el.get(W + "styleId") in gone:
                found.add(el.get(W + "styleId"))
                root.remove(el)
        if found != gone:
            raise JobException(f"remove_unused_styles: styles {sorted(gone - found)} are no longer in the styles part "
                               f"(an earlier op changed it)", "internal")
        pkg.write_xml(td["styles_part"], root)


__all__ = ["RemoveUnusedStyles", "analyze", "clean_params", "closure", "eligible", "field_generated", "instruction_text",
           "kept_by", "named_in_fields", "references", "scanned_parts", "seed_styles", "style_names", "style_table",
           "FIELD_STYLES", "REF_NAMES"]
