"""Op: set_list_level — change one level of a list: its number format, zero padding, start value or restart rule
(built-in batch 5, item 35 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §3).

Formatting mode: zero text diffs; the one part that changes is the numbering part (``numbering.xml``), in the
abstractNum's ``w:lvl`` of the level. Never tracked (Word's ``w:numberingChange`` is not written: ``tracked`` is
``bad-params``).

Parameters (``select`` takes no keys):

* ``style`` — a paragraph style of the document (id or name, ``style_import.resolve_style``); the level is the one its
  own numbering names (``lists.style_numbering``: the style chain alone, design §0.1 steps 2 and 4). ``level`` (optional,
  0–8) — another level of the same list (e.g. ``{"style": "ArchPart", "level": 1}`` is the articles' level of the PART
  list).
* the changes, at least one: ``format`` (``decimal``, ``decimalZero``, ``upperLetter``, ``lowerLetter``, ``upperRoman``,
  ``lowerRoman``); ``zero_pad`` (true → ``decimalZero`` where the level's format is ``decimal``; false → ``decimal`` where
  it is ``decimalZero``; any other format is left alone and noted; refused beside ``format``); ``start`` (a whole number
  ≥ 0; ≥ 1 for a letter or roman format); ``restart``: ``"after_higher"`` (no ``w:lvlRestart``: the level restarts after
  any higher level — "restart numbering per PART" for articles numbered with their PART), ``"never"``
  (``w:lvlRestart w:val="0"``), ``{"after_level": k}`` with k below the level (``w:lvlRestart w:val="k+1"``).
* ``allow_missing`` (default **true**): a file whose style is missing or not numbered is a no-op with a note; false
  refuses ``not-found``.

Definitions (the verifier's own reading is verify/listlevel.py):

* **The level** (``find_level``): the style resolved in the document (a style of another type: ``style-mismatch``), its
  numbering ``(numId, ilvl)``, ``level`` replacing the ilvl; the list's abstractNum (``ListDefs.abstract_of``). Refused
  (``numbering-unsupported``): the list cannot be read or is defined through a numbering style (``ListDefs.problem``),
  the style's own numbering id cannot be read, the abstractNum has no ``w:lvl`` of that ilvl, or the level is unsupported
  (``LevelDef.unsupported``); (``numbering-override``) a num of that abstractNum carries a FULL override level for it
  (``w:lvlOverride/w:lvl``: Word does not read those as the standard says — the change would not show there); with
  ``start`` or ``restart``: (``numbering-unsupported``) the abstractNum numbers counted paragraphs through more than one
  num (Word continues one count across them, LibreOffice keeps them apart — design §0.2). A format change does not change
  counts, so it plans there, flagged.
* **What changes** (``changes_for``), from the level's own fields: ``fmt`` when the asked format (or the zero-pad rule)
  differs from the level's; ``start`` when the asked start differs from the level's (default 0); ``restart`` when the
  asked rule differs from the level's **in effect** — ``restart_in_effect``, the rule's normal form: none, a value r ≥ 1
  that Word ignores (r − 1 ≥ the level) and r equal to the level ("after level r − 1", the nearest higher one: after any
  higher level) are all "after any higher level"; 0 is never; another r is after level r − 1 only. What is written is the
  normal form (``{"after_level": level − 1}`` writes no element, raises no LibreOffice flag). Nothing differs → "already
  as asked": a no-op with a note.
* **The written level** (``expected_level``): a copy of the source ``w:lvl`` with each changed element replaced by a new
  one — ``<w:start w:val=n/>``, ``<w:numFmt w:val=F/>`` (no ``w:format`` attribute is kept), ``<w:lvlRestart
  w:val=r/>`` (removed for "after any higher level") — inserted in CT_Lvl order (``LVL_ORDER``: start, numFmt,
  lvlRestart, pStyle, isLgl, suff, lvlText, lvlPicBulletId, legacy, lvlJc, pPr, rPr); a changed element present more than
  once refuses (``structure``). ``write_level`` puts it in place of the source level; nothing else of the part changes.
* **Refused when it cannot be shown** (``list-level``, file-specific): ``start`` 0 on a level whose format (after the
  change) is a letter or roman format; ``after_level`` not below the level; a change that leaves a counted paragraph that
  was evaluated before not evaluated after (a number its format cannot show, a level skipped — ``lists.count``).
* **Predicted numbers**: ``lists.count`` over the main part with the modified definitions — every counted numbered
  paragraph's counters and rendered number after the change (a format change keeps every counter).
* **Flagged**: a num's ``startOverride`` for the level beside ``start`` (the override still wins in that list); every
  other num sharing the abstractNum that numbers counted paragraphs (all their paragraphs change; an orphan num is not
  named); writing ``never`` / ``after_level`` (LibreOffice ignores ``w:lvlRestart``: the proof PDFs number the level as
  if it restarted after every higher level, Word follows it); a restart asked where no higher level of the list numbers
  a counted paragraph (the restart changes nothing — the PARTs are typed or in another list: convert the numbering
  first, ``convert_numbering`` / ``convert_typed_numbers``); **who the change reaches** (``_reach_flags``): no counted
  paragraph at the level ("this change reaches no paragraph"), or the style's own numbered paragraphs at another list or
  level — a direct numPr naming another list, what Word leaves after re-numbering through the ribbon — named by the list
  and level they are numbered by ("this change reaches no paragraph of style X" when none is at the level); paragraphs at
  the level the model does not evaluate (a shared definition …: the change reaches them, unpredicted — the note says how
  many); paragraphs of the list whose level is a LINKED level (``NumberedPar.linked``: LibreOffice shows them at level 0,
  ``linked_flag``); and what the renumbering leaves stale (``numbering_flags``, shared with convert_numbering):
  REF/PAGEREF/NOTEREF fields whose bookmark lies in a paragraph whose number changes, and written occurrences of an old
  number that changes in another paragraph's text — flagged, never edited.

Touches (rule 9; design §6): ``(numbering, "*", "lvl")`` — a whole-member change: any other op touching the numbering
part is a plan-time conflict —, ``(styles, "style:<id>", "verify")`` of the style, and ``lists.count_reads`` (the
numbering part whole, the main part's ``pStyle``/``numPr`` families, ``verify:numPr`` of every counted numbered paragraph,
``verify:pPr`` of the styles on their chains). A no-op (style missing, not numbered, already as asked) changes nothing and
declares the reads of its decision only (the style, the numbering part). Layout reference ``none``: numbers change
width.

Verified by V10's ``list_level`` kind (verify/listlevel.py): the level and its expected ``w:lvl`` re-derived from the
SOURCE, the WHOLE numbering member compared with its own expected tree (``verified_members``), and the numbers recounted.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import W, DocxPackage
from ..ooxml import insert_in_order
from . import register
from . import lists
from . import prose
from .base import JobException, Op, PlanFragment
from .selectors import StyleSheet
from .style_import import resolve_style, style_type
from .numbering_flags import stale_fields, stale_fields_flag, written_references, written_references_flag
from .textedit import run_text, shown

FORMAT_CHOICES = ("decimal", "decimalZero", "upperLetter", "lowerLetter", "upperRoman", "lowerRoman")
FORMAT_WORDS = {"decimal": "1, 2, 3", "decimalZero": "01, 02, 03", "upperLetter": "A, B, C", "lowerLetter": "a, b, c",
                "upperRoman": "I, II, III", "lowerRoman": "i, ii, iii"}       # the plan's prose for each format
NOT_FROM_ZERO = ("upperLetter", "lowerLetter", "upperRoman", "lowerRoman")
LVL_ORDER = ["start", "numFmt", "lvlRestart", "pStyle", "isLgl", "suff", "lvlText", "lvlPicBulletId", "legacy", "lvlJc",
             "pPr", "rPr"]
ELEMENT_OF = {"start": "start", "fmt": "numFmt", "restart": "lvlRestart"}
PARAM_KEYS = {"style", "level", "format", "zero_pad", "start", "restart", "allow_missing", "tracked"}
CHANGE_KEYS = ("format", "zero_pad", "start", "restart")
AFTER_HIGHER, NEVER = "after_higher", "never"
LIBREOFFICE_RESTART = ("LibreOffice ignores the restart rule: the proof PDFs number this level as if it restarted after "
                       "every higher level; Word follows it")
LAYOUT_REASON = "a list level's numbers change, and with them their width; the text after them may move"
EXAMPLES = 5


# ---- parameters ------------------------------------------------------------------------------------------------------

def clean_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """The user's words checked (``bad-params``) and normalised: ``style``, ``level`` (int or None), ``format``,
    ``zero_pad`` (bool or None), ``start`` (int or None), ``restart`` (None — not asked —, ``"after_higher"``, ``"never"``
    or ``("after_level", k)``), ``allow_missing`` (default true)."""
    unknown = set(params) - PARAM_KEYS
    if unknown:
        raise JobException(f"set_list_level: unknown parameter(s) {sorted(unknown)}; known: {sorted(PARAM_KEYS)}",
                           "bad-params")
    if params.get("tracked"):
        raise JobException("a list level's definition is not tracked (w:numberingChange is not written); omit "
                           "'tracked' or set it false", "bad-params")
    style = params.get("style")
    if not isinstance(style, str) or not style.strip():
        raise JobException("set_list_level needs 'style': the paragraph style (id or name) whose list level changes",
                           "bad-params")
    level = params.get("level")
    if level is not None and (isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 8):
        raise JobException("level must be a list level 0–8", "bad-params")
    if not any(k in params for k in CHANGE_KEYS):
        raise JobException(f"set_list_level needs at least one change: {list(CHANGE_KEYS)}", "bad-params")
    fmt = params.get("format")
    if "format" in params and fmt not in FORMAT_CHOICES:
        raise JobException(f"format must be one of {list(FORMAT_CHOICES)}", "bad-params")
    zero = params.get("zero_pad")
    if "zero_pad" in params and not isinstance(zero, bool):
        raise JobException("zero_pad must be true or false", "bad-params")
    if "zero_pad" in params and "format" in params:
        raise JobException("give 'format' or 'zero_pad', not both (zero padding is the format decimalZero)",
                           "bad-params")
    start = params.get("start")
    if "start" in params:
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise JobException("start must be a whole number ≥ 0", "bad-params")
        if start < 1 and fmt in NOT_FROM_ZERO:
            raise JobException(f"a {fmt} level starts at 1 or more", "bad-params")
    restart = None
    if "restart" in params:
        r = params["restart"]
        if r in (AFTER_HIGHER, NEVER):
            restart = r
        elif isinstance(r, dict) and set(r) == {"after_level"} and isinstance(r["after_level"], int) \
                and not isinstance(r["after_level"], bool) and 0 <= r["after_level"] <= 7:
            restart = ("after_level", r["after_level"])
            if level is not None and r["after_level"] >= level:
                raise JobException(f"restart after level {r['after_level']}: the level must be below level {level}",
                                   "bad-params")
        else:
            raise JobException("restart must be 'after_higher', 'never' or {'after_level': k} (k 0–7, below the level)",
                               "bad-params")
    allow = params.get("allow_missing", True)
    if not isinstance(allow, bool):
        raise JobException("allow_missing must be true or false", "bad-params")
    return {"style": style, "level": level, "format": fmt if "format" in params else None,
            "zero_pad": zero if "zero_pad" in params else None, "start": start if "start" in params else None,
            "restart": restart, "allow_missing": allow}


# ---- the level -------------------------------------------------------------------------------------------------------

class Target:
    """The level a call changes: the style, the style's list (``num_id``), the level (``ilvl``) and its abstractNum."""

    def __init__(self, style_id: str, num_id: int, ilvl: int, abstract_id: int):
        self.style_id, self.num_id, self.ilvl, self.abstract_id = style_id, num_id, ilvl, abstract_id


def find_level(sheet: StyleSheet, defs: lists.ListDefs, opts: Dict[str, Any]) -> Tuple[Optional[Target], str]:
    """(the target, None) or (None, why there is none — the style is missing or not numbered: ``not-found``). Raises
    ``style-mismatch`` / ``numbering-unsupported`` where the op must refuse."""
    ref = opts["style"]
    sid = resolve_style(sheet.styles, ref, "this file")
    if sid is None:
        return None, f"this file has no paragraph style {ref!r}"
    if style_type(sheet.styles[sid]) != "paragraph":
        raise JobException(f"style {ref!r} is a {style_type(sheet.styles[sid])} style in this file; set_list_level "
                           f"names a paragraph style", "style-mismatch", {"style": sid})
    nb = lists.style_numbering(sid, sheet, defs)
    if nb is None:
        return None, f"style {sid} is not numbered in this file"
    if nb.unreadable:
        raise JobException(f"style {sid}: {nb.unreadable}", "numbering-unsupported", {"style": sid})
    if nb.dangling:
        return None, f"style {sid} names list {nb.num_id}, which this file does not define"
    ilvl = nb.ilvl if opts["level"] is None else opts["level"]
    prob = defs.problem(nb.num_id)
    if prob:
        raise JobException(f"style {sid}'s list: {prob[1]}", "numbering-unsupported", {"style": sid,
                                                                                       "num_id": nb.num_id})
    return Target(sid, nb.num_id, ilvl, defs.abstract_of(nb.num_id)), None


def abstract_level(defs: lists.ListDefs, t: Target) -> Optional[lists.LevelDef]:
    """The abstractNum's own definition of the level (never a num's override level)."""
    a = defs.abstracts.get(t.abstract_id)
    return a.levels.get(t.ilvl) if a is not None else None


def restart_in_effect(restart: Optional[int], ilvl: int) -> Optional[int]:
    """A level's ``w:lvlRestart`` as Word reads it, in its normal form: None (after any higher level) for none, for a
    value Word ignores (r − 1 ≥ the level) and for r = the level (after level r − 1 = after the nearest higher level:
    after any higher level); else the value (0 never, 1 ≤ r < the level: after level r − 1 only)."""
    if restart is None or (restart >= 1 and restart >= ilvl):
        return None
    return restart


def asked_restart(restart: Any) -> Optional[int]:
    """The ``w:lvlRestart`` value an asked rule writes (None: no element)."""
    if restart == AFTER_HIGHER:
        return None
    if restart == NEVER:
        return 0
    return restart[1] + 1


def changes_for(lv: lists.LevelDef, opts: Dict[str, Any], ilvl: int) -> Tuple[Dict[str, Any], List[str]]:
    """({"fmt": …, "start": …, "restart": …} — only what differs from the level, the restart None for "no element"},
    notes)."""
    out: Dict[str, Any] = {}
    notes: List[str] = []
    if opts["format"] is not None and opts["format"] != lv.fmt:
        out["fmt"] = opts["format"]
    if opts["zero_pad"] is not None:
        want = {(True, "decimal"): "decimalZero", (False, "decimalZero"): "decimal"}.get((opts["zero_pad"], lv.fmt))
        if want:
            out["fmt"] = want
        elif lv.fmt not in ("decimal", "decimalZero"):
            notes.append(f"zero padding applies to number levels only; this level's format is {lv.fmt}: left alone")
    if opts["start"] is not None and opts["start"] != lv.start:
        out["start"] = opts["start"]
    if opts["restart"] is not None:
        want = restart_in_effect(asked_restart(opts["restart"]), ilvl)       # the normal form is what is written
        if want != restart_in_effect(lv.restart, ilvl):
            out["restart"] = want
    return out, notes


def expected_level(lvl, changes: Dict[str, Any]):
    """A copy of the source ``w:lvl`` with each changed element replaced (CT_Lvl order; no ``w:format`` kept)."""
    new = copy.deepcopy(lvl)
    for key, local in ELEMENT_OF.items():
        if key not in changes:
            continue
        for old in new.findall(W + local):
            new.remove(old)
        value = changes[key]
        if value is None:
            continue                                   # "after any higher level": no w:lvlRestart
        el = etree.Element(W + local)
        el.set(W + "val", str(value))
        insert_in_order(new, el, LVL_ORDER)
    return new


def level_element(root, abstract_id: int, ilvl: int):
    """The ``w:lvl`` of level ``ilvl`` in the abstractNum ``abstract_id`` of a numbering root (ids read as ``lists``
    reads them), else None."""
    for a in root.findall(W + "abstractNum"):
        if lists.whole(a.get(W + "abstractNumId")) == abstract_id:
            for lvl in a.findall(W + "lvl"):
                if lists.whole(lvl.get(W + "ilvl")) == ilvl:
                    return lvl
    return None


def write_level(root, abstract_id: int, ilvl: int, new_lvl) -> None:
    """Put ``new_lvl`` in place of the level in ``root`` (in place)."""
    old = level_element(root, abstract_id, ilvl)
    old.getparent().replace(old, new_lvl)


def linked_flag(where: str, linked: Sequence[lists.NumberedPar], every) -> str:
    """Paragraphs whose level is a LINKED level (a style numPr without ilvl, the list's w:pStyle naming the style): Word
    reads the link, LibreOffice ignores it and shows them at level 0 — the proof PDFs differ."""
    return (f"{where}: {len(linked)} paragraph(s) of this list take their level from a LINKED level (the style names the "
            f"list without a level; the list's level names the style) — Word follows the link, LibreOffice shows them at "
            f"level 0, so the proof PDFs number them differently; first ‘{_label(every[linked[0].index])}’ (give the "
            f"style its w:ilvl in Word to make both agree).")


def _rows(pars: Sequence[lists.NumberedPar]) -> List[List[Any]]:
    return [[p.index, p.num_id, p.ilvl, list(p.counters) if p.counters is not None else None, p.text, p.issue]
            for p in pars]


def _label(p, limit: int = 50) -> str:
    t = shown(run_text(p))
    return ((t[:limit] + "…") if len(t) > limit else t) or "(empty)"


# ---- the op ----------------------------------------------------------------------------------------------------------

@register
class SetListLevel(Op):
    name = "set_list_level"
    mode = "formatting"
    description = ("Change one level of a list: its number format (1, 01, A, a, I, i), zero padding ('1.01' ↔ '1.1'), its "
                   "start value, or when it restarts ('restart per PART': after any higher level; never; after a given "
                   "level). The level is the one a paragraph style numbers with (or another level of the same list); "
                   "the change is made in the list's definition, so every paragraph of that level follows. Adds no "
                   "characters; never tracked.")
    params_schema = {"type": "object", "required": ["style"], "properties": {
        "style": {"type": "string", "description": "the paragraph style (id or name, as the profiles show it) whose "
                                                   "list level changes"},
        "level": {"type": "integer", "description": "another level (0–8) of the same list, instead of the style's own"},
        "format": {"type": "string", "enum": list(FORMAT_CHOICES),
                   "description": "the level's number format (decimalZero: 01, 02 …)"},
        "zero_pad": {"type": "boolean", "description": "true: 1 → 01 (decimal becomes decimalZero); false: 01 → 1; not "
                                                       "with format"},
        "start": {"type": "integer", "description": "the value the level starts at (≥ 0; ≥ 1 for letters and roman)"},
        "restart": {"type": ["string", "object"],
                    "description": "'after_higher' (restarts after any higher level: 'restart per PART'), 'never', "
                                   "or {after_level: k} (k below the level)",
                    "properties": {"after_level": {"type": "integer"}}},
        "allow_missing": {"type": "boolean", "default": True,
                          "description": "a file whose style is missing or not numbered is left alone and noted"},
        "tracked": {"type": "boolean", "description": "must be false: list definitions are not tracked"}}}
    select_schema = {"description": "none — the level is named by params.style (and params.level)"}
    verification_profile = {
        "V1": "only the numbering part changes", "V2": "zero text diffs",
        "V10": "list_level: the level, its expected w:lvl and the refusals re-derived from the source; the WHOLE "
               "numbering part compared with the source plus exactly that level's change; every counted paragraph's "
               "counters and number recounted on the output and equal to the plan's and the verifier's own prediction",
        "V7": "none — numbers change width"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})
        style, level = params.get("style"), params.get("level")
        if isinstance(style, str) and style.strip():
            owner = f"the list that {prose.style(style)} numbers with"
            target = (f"level {level + 1} of {owner}" if isinstance(level, int) and not isinstance(level, bool)
                      else f"the level that {prose.style(style)} numbers with")
        else:
            target = "one level of a list"
        changes: List[str] = []
        fmt = params.get("format")
        if isinstance(fmt, str):
            changes.append("its numbers are shown as " + (FORMAT_WORDS.get(fmt) or prose.quote(fmt)))
        if params.get("zero_pad") is True:
            changes.append("its numbers are zero-padded (1 becomes 01)")
        elif params.get("zero_pad") is False:
            changes.append("its numbers lose their zero padding (01 becomes 1)")
        start = params.get("start")
        if isinstance(start, int) and not isinstance(start, bool):
            changes.append(f"it starts at {start}")
        restart = params.get("restart")
        if restart == AFTER_HIGHER:
            changes.append("it restarts after any higher level")
        elif restart == NEVER:
            changes.append("it never restarts")
        elif isinstance(restart, dict) and isinstance(restart.get("after_level"), int) \
                and not isinstance(restart["after_level"], bool):
            changes.append(f"it restarts only after level {restart['after_level'] + 1}")
        elif restart is not None:
            changes.append("it restarts by the rule given")
        first = prose.sentence(f"In the list's definition, change {target}, so every paragraph at that level follows"
                               + (": " + prose.join(changes) if changes else ""))
        if params.get("allow_missing") is False:
            return first + " " + prose.sentence("a file whose style is missing or not numbered is set aside")
        return first

    def plan(self, pkg: DocxPackage, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException(f"set_list_level takes no selector keys, got {sorted(select)}", "bad-params")
        opts = clean_params(params)
        frag = self.fragment(select, params)
        sheet = StyleSheet(pkg)
        defs = lists.ListDefs.load(pkg)
        part = defs.part
        assertion: Dict[str, Any] = {
            "kind": "list_level", "numbering_part": part,
            "params": {k: params[k] for k in ("style", "level", "format", "zero_pad", "start", "restart",
                                              "allow_missing", "tracked") if k in params},
            "style_id": None, "num_id": None, "ilvl": None, "abstract_id": None, "changed": [], "predicted": None}
        frag.property_assertions.append(assertion)
        frag.transform_data = {"numbering_part": part, "abstract_id": None, "ilvl": None, "changes": {}}

        target, missing = find_level(sheet, defs, opts)
        if part is not None:
            frag.touches.append((part, "*", "verify"))           # the decision reads the list definitions
        sid = resolve_style(sheet.styles, opts["style"], "this file")
        if sid is not None and sheet.part is not None:
            frag.touches.append((sheet.part, f"style:{sid}", "verify"))
        if target is None:
            if not opts["allow_missing"]:
                raise JobException(missing, "not-found", {"style": opts["style"]})
            frag.notes.append(f"{missing}; nothing to change (allow_missing)")
            return frag
        assertion.update(style_id=target.style_id, num_id=target.num_id, ilvl=target.ilvl,
                         abstract_id=target.abstract_id)
        where = f"level {target.ilvl} of list {target.num_id} (style {target.style_id})"

        # ---- refusals -------------------------------------------------------------------------------------------
        main = pkg.main_part()
        root = pkg.xml(main)
        every = list(root.iter(W + "p"))
        before = lists.count(root, sheet, defs)
        sharing = [n for n in defs.nums_of_abstract(target.abstract_id) if n != target.num_id]
        over = [n for n in [target.num_id] + sharing if defs.is_override(n, target.ilvl)]
        if over:
            raise JobException(f"{where}: list(s) {over} override this level with a level of their own "
                               f"(w:lvlOverride/w:lvl); Word does not read those as the standard says, so the change "
                               f"would not show there", "numbering-override",
                               {"num_ids": over, "ilvl": target.ilvl})
        lv = abstract_level(defs, target)
        if lv is None:
            raise JobException(f"{where}: the list defines no level {target.ilvl}", "numbering-unsupported",
                               {"num_id": target.num_id, "ilvl": target.ilvl})
        if lv.unsupported:
            raise JobException(f"{where} {lv.unsupported}", "numbering-unsupported",
                               {"num_id": target.num_id, "ilvl": target.ilvl})
        users = sorted({p.num_id for p in before if p.abstract_id == target.abstract_id and p.num_id is not None})
        if (opts["start"] is not None or opts["restart"] is not None) and len(users) > 1:
            raise JobException(f"{where}: its definition is shared by lists {users}, which all number paragraphs — Word "
                               f"continues one count across them and LibreOffice keeps them apart, so a new start or "
                               f"restart cannot be predicted", "numbering-unsupported",
                               {"num_ids": users, "ilvl": target.ilvl})
        if opts["restart"] not in (None, AFTER_HIGHER, NEVER) and opts["restart"][1] >= target.ilvl:
            raise JobException(f"{where}: a restart after level {opts['restart'][1]} needs a level below "
                               f"{target.ilvl}", "list-level", {"ilvl": target.ilvl})
        changes, change_notes = changes_for(lv, opts, target.ilvl)
        frag.notes += [f"{where}: {n}" for n in change_notes]
        fmt_after = changes.get("fmt", lv.fmt)
        if fmt_after in NOT_FROM_ZERO and changes.get("start", lv.start) < 1:
            raise JobException(f"{where}: a {fmt_after} level cannot start at {changes.get('start', lv.start)}",
                               "list-level", {"ilvl": target.ilvl})
        if opts["restart"] is not None and opts["restart"] != NEVER:
            # "restart per PART" where the PARTs are not in this list: said whether or not the rule changes
            below = target.ilvl if opts["restart"] == AFTER_HIGHER else opts["restart"][1] + 1
            if not any(p.abstract_id == target.abstract_id and p.ilvl is not None and p.ilvl < below for p in before):
                frag.flags.append(f"{where}: no higher level of this list numbers a paragraph, so the restart changes "
                                  f"nothing — when the PART headings are typed or numbered by another list, convert the "
                                  f"numbering first (convert_numbering / convert_typed_numbers), then restart.")
        if not changes:
            frag.notes.append(f"{where}: already as asked; nothing to change")
            return frag

        # ---- the written level and the numbers after it ---------------------------------------------------------
        nroot = pkg.xml(part)
        lvl = level_element(nroot, target.abstract_id, target.ilvl)
        doubled = [ELEMENT_OF[k] for k in changes if len(lvl.findall(W + ELEMENT_OF[k])) > 1]
        if doubled:
            raise JobException(f"{where}: the level carries {doubled} more than once", "structure",
                               {"elements": doubled})
        new_root = copy.deepcopy(nroot)
        write_level(new_root, target.abstract_id, target.ilvl, expected_level(lvl, changes))
        after = lists.count(root, sheet, lists.ListDefs(new_root, part))
        was = {p.index: p for p in before}
        lost = [p for p in after if not p.evaluated and was.get(p.index) is not None and was[p.index].evaluated]
        if lost:
            raise JobException(f"{where}: after the change {len(lost)} paragraph(s) would show a number the model cannot "
                               f"predict — first ‘{_label(every[lost[0].index])}’: {lost[0].not_evaluated}",
                               "list-level", {"paragraphs": [p.index for p in lost[:10]]})

        # ---- the record -----------------------------------------------------------------------------------------
        assertion["changed"] = sorted(ELEMENT_OF[k] for k in changes)
        assertion["predicted"] = _rows(after)
        frag.transform_data.update(abstract_id=target.abstract_id, ilvl=target.ilvl, changes=changes)
        frag.intended_parts = [part]
        frag.touches.append((part, "*", "lvl"))
        frag.touches.append((main, "scope", "verify"))          # V10 recounts every paragraph of the main part
        frag.touches += lists.count_reads(pkg, sheet, before, extra_styles=[target.style_id])
        frag.geometry_reference, frag.geometry_reason = "none", LAYOUT_REASON

        said = []
        if "fmt" in changes:
            said.append(f"format {lv.fmt} → {changes['fmt']}")
        if "start" in changes:
            said.append(f"start {lv.start} → {changes['start']}")
        if "restart" in changes:
            said.append("restart " + {None: "after any higher level", 0: "never"}.get(
                changes["restart"], f"after level {(changes['restart'] or 1) - 1}"))
        moved = [(was[p.index], p) for p in after if p.index in was and was[p.index].text != p.text]
        examples = "; ".join(f"‘{_label(every[b.index], 30)}’ {b.text} → {a.text}" for b, a in moved[:EXAMPLES])
        at_level = [p for p in after if p.abstract_id == target.abstract_id and p.ilvl == target.ilvl]
        unknown = sum(1 for p in at_level if not p.evaluated)
        frag.notes.append(f"{where}: {', '.join(said)} (the list definition — every paragraph of the level follows); "
                          f"{len(at_level)} paragraph(s) at this level; {len(moved)} paragraph(s) change number"
                          + (f": {examples}" if moved else "")
                          + (f" … and {len(moved) - EXAMPLES} more" if len(moved) > EXAMPLES else "")
                          + (f"; {unknown} paragraph(s) at this level are not evaluated — their numbers change but are "
                             f"not predicted" if unknown else ""))

        # ---- flags ----------------------------------------------------------------------------------------------
        if "start" in changes:
            pinned = [n for n in [target.num_id] + sharing if defs.start_override(n, target.ilvl) is not None]
            if pinned:
                frag.flags.append(f"{where}: list(s) {pinned} set their own start for this level (w:startOverride); "
                                  f"the override still wins there.")
        numbering = sorted(n for n in sharing if n in users)    # only lists that number counted paragraphs
        if numbering:
            frag.flags.append(f"{where}: the definition is shared with list(s) {numbering}; every paragraph they number "
                              f"at this level changes too.")
        if "restart" in changes and changes["restart"] is not None:
            frag.flags.append(f"{where}: {LIBREOFFICE_RESTART}.")
        self._reach_flags(frag, where, target, sheet, every, before)
        at_level = [p for p in after if p.abstract_id == target.abstract_id and p.ilvl == target.ilvl]
        unknown = [p for p in at_level if not p.evaluated]
        if unknown:
            frag.flags.append(f"{where}: {len(unknown)} of the {len(at_level)} paragraph(s) at this level are not "
                              f"evaluated ({unknown[0].not_evaluated}) — the change reaches them, but their numbers are "
                              f"neither predicted nor checked; first ‘{_label(every[unknown[0].index])}’.")
        linked = [p for p in before if p.linked and p.abstract_id == target.abstract_id]
        if linked:
            frag.flags.append(linked_flag(where, linked, every))
        changed = [p.index for p in after if p.index in was and was[p.index].text != p.text]
        for flag in (stale_fields_flag(stale_fields(root, every, changed), "paragraphs whose number changes"),
                     written_references_flag(written_references(
                         every, [(i, was[i].text, next(a.text for a in after if a.index == i)) for i in changed]))):
            if flag:
                frag.flags.append(flag)
        return frag

    @staticmethod
    def _reach_flags(frag: PlanFragment, where: str, target: Target, sheet, every, before) -> None:
        """Who the change reaches. The style's own counted paragraphs numbered by another list or at another level (a
        direct numPr naming another list — what Word leaves after re-numbering through the ribbon) are not reached:
        flagged, naming those lists; when none of the style's paragraphs is at the level, "this change reaches no
        paragraph of style X"; when no counted paragraph at all is, "this change reaches no paragraph". Paragraphs of the
        style with numbering switched off (numId 0) are left out: they show no number either way."""
        at_level = [p for p in before if p.abstract_id == target.abstract_id and p.ilvl == target.ilvl]
        if not at_level:
            frag.flags.append(f"{where}: no counted paragraph is numbered at this level, so this change reaches no "
                              f"paragraph.")
            return
        if frag.params.get("level") is not None:
            return                                     # another level than the style's own: the style's are not at it
        by_index = {p.index: p for p in before}
        mine = [i for i, p in enumerate(every) if lists.counted(p) and lists.paragraph_style(p, sheet) == target.style_id
                and i in by_index]
        away: Dict[str, List[int]] = {}
        for i in mine:
            np_ = by_index[i]
            if np_.abstract_id == target.abstract_id and np_.ilvl == target.ilvl:
                continue
            key = f"list {np_.num_id}" + (f" level {np_.ilvl}" if np_.ilvl is not None else "")
            away.setdefault(key, []).append(i)
        if not away:
            return
        n_away = sum(len(v) for v in away.values())
        frag.flags.append(f"{where}: {n_away} of the {len(mine)} numbered paragraph(s) of style {target.style_id} are not "
                          f"numbered at this level — "
                          + "; ".join(f"{len(v)} by {k} (first ‘{_label(every[v[0]])}’)" for k, v in away.items())
                          + (f": this change reaches no paragraph of style {target.style_id}." if n_away == len(mine)
                             else ": the change does not reach them."))

    def apply(self, pkg: DocxPackage, frag: PlanFragment) -> None:
        td = frag.transform_data
        if not td.get("changes"):
            return
        root = pkg.xml(td["numbering_part"])
        lvl = level_element(root, td["abstract_id"], td["ilvl"])
        if lvl is None:
            raise JobException("the list level the plan changes is gone from the numbering part", "internal")
        write_level(root, td["abstract_id"], td["ilvl"], expected_level(lvl, td["changes"]))
        pkg.write_xml(td["numbering_part"], root)
