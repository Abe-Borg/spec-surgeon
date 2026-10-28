"""Safe mode's buttons: fixed recipes, built in code from what the user typed and attached — never by a model.

A recipe turns one button's form into an EditSpec (docs/CONVERSATIONAL_DESKTOP.md, Safe mode). The spec then passes
the gate every submission passes (``compiler.compose_submission``: validation, the attached-file check, the words a spec
places traced to the user's own, the dry plan, the composition of overlapping operations into ordered steps) and the
ordinary preview → approve → run → verify of the window, with no AI call anywhere: the visual check and the recovery
of set-aside files are model calls, and a recipe's outcome skips both (``Session.outcome_source``).

Every word a recipe places is one the user typed in its form (a find-and-replace pair, the watermark's words, a
template slot's value, the words around a title property) or read from a file they attached (a table of pairs, the
template). The button's own sentence (``request``) names the user's inputs and nothing else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .catalog import REFERENCE_FILLS
from .inputs import KINDS as INPUT_KINDS

# The groups the panel shows, in order.
GROUPS = ("Headers & footers", "Fonts & formatting", "Text", "Clean up", "Files & properties")

# Template slots that stand for the section number or title (decision 2 of the plan): filled from each document by the
# reference rules. A slot whose name is not here is a value the user types in the button's form.
NUMBER_SLOTS = {"SECTION_NUMBER", "SECTION NUMBER", "XX XX XX", "XXXXXX", "XX-XX-XX", "XX.XX.XX", "NUMBER"}
TITLE_SLOTS = {"SECTION_TITLE", "SECTION TITLE", "TITLE"}
SWAP_PICTURES = {".png", ".jpg", ".jpeg", ".gif"}      # what swap_image places (a BMP or SVG is refused there)
MIN_FONT_SHARE = 0.5                                   # below it, the template's most used font is not its body text
NAME_PATTERNS = ("{number} - {title}", "{number} {title}", "{number}")
DEFAULT_TITLE_PROPERTY = "SECTION {number} - {title}"


class RecipeError(ValueError):
    """A button's form cannot be used as it is: one sentence for the user, nothing previewed."""


@dataclass(frozen=True)
class Field:
    """One input of a button's form, as the panel renders it (``kind``: text, pairs, choice, check, picture, table,
    slots)."""
    name: str
    kind: str
    label: str
    required: bool = False
    default: Any = None
    options: Tuple[Tuple[str, str], ...] = ()          # choice: (value, label)
    help: str = ""
    placeholder: str = ""
    optional_choice: str = ""                          # picture: the label of the choice that names no file

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "kind": self.kind, "label": self.label, "required": self.required}
        if self.default is not None:
            out["default"] = self.default
        if self.options:
            out["options"] = [{"value": v, "label": label} for v, label in self.options]
        for key in ("help", "placeholder", "optional_choice"):
            if getattr(self, key):
                out[key] = getattr(self, key)
        return out


@dataclass
class Context:
    """What the button works with: the documents in scope, the attached template, the pictures and tables."""
    documents: List[Path]
    template: Optional[Path] = None
    references: List[Path] = field(default_factory=list)
    _batch: Any = field(default=None, init=False, repr=False)

    def batch(self):
        """The batch as the gate reads it (``compiler._Batch``: every document profiled once), made when first asked."""
        if self._batch is None:
            from .compiler import _Batch
            self._batch = _Batch(self.documents, self.template, input_files=self.references)
        return self._batch

    def pictures(self) -> List[Path]:
        return [p for p in self.references if INPUT_KINDS.get(p.suffix.lower()) == "picture"]

    def tables(self) -> List[Path]:
        return [p for p in self.references if INPUT_KINDS.get(p.suffix.lower()) == "table"]


@dataclass
class Built:
    """A button's spec, the words the user typed for it (the only words it may place), and its one sentence. A button
    that plans per layout (``body_match``) passes each layout through the gate itself: ``outcome`` is then the layout
    plan as the engine runs it (None when no document can take the change), and ``left_out`` the documents it leaves
    out, each with the sentence that says why."""
    spec: Optional[Dict[str, Any]]
    sources: List[str]
    request: str
    outcome: Any = None
    left_out: Dict[str, str] = field(default_factory=dict)
    gated: bool = False
    export: str = ""                   # "pdf": the run also saves a clean PDF of each verified copy (``pdf_export``)


@dataclass(frozen=True)
class Recipe:
    id: str
    label: str
    group: str
    summary: str                       # one sentence: what the button does
    needs: Tuple[str, ...]             # "template", "picture", "pdf" (a program that makes PDFs)
    fields: Tuple[Field, ...]
    build: Callable[[Dict[str, Any], Context], Built]

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "group": self.group, "summary": self.summary,
                "needs": list(self.needs), "fields": [f.to_dict() for f in self.fields]}


TRACKED = Field("tracked", "check", "Show my changes as tracked changes in Word", default=False,
                help="Off: clean copies. On: each change is a tracked change a reviewer can accept or reject.")


# ---- the inputs, read strictly ----
def _text(inputs: Dict[str, Any], name: str, *, label: str, required: bool = True, limit: int = 255) -> str:
    value = inputs.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise RecipeError(f"{label} must be text.")
    value = value.strip()
    if required and not value:
        raise RecipeError(f"Type {label[0].lower() + label[1:]}.")
    if "\n" in value or "\r" in value:
        raise RecipeError(f"{label} must be one line.")
    if len(value) > limit:
        raise RecipeError(f"{label} is longer than {limit} characters.")
    return value


def _flag(inputs: Dict[str, Any], name: str, default: bool = False) -> bool:
    value = inputs.get(name, default)
    if not isinstance(value, bool):
        raise RecipeError("A checkbox must be on or off.")
    return value


def _choice(inputs: Dict[str, Any], f: Field) -> str:
    value = inputs.get(f.name, f.default)
    if value not in {v for v, _ in f.options}:
        raise RecipeError(f"Choose one of the options for “{f.label}”.")
    return value


def _attached(path_value: Any, among: Sequence[Path], what: str) -> Path:
    """A file the form names must be one the user attached (``compose_submission`` checks it again)."""
    if not isinstance(path_value, str) or not path_value.strip():
        raise RecipeError(f"Choose the {what}.")
    wanted = Path(path_value).resolve()
    for p in among:
        if Path(p).resolve() == wanted:
            return Path(p).resolve()
    raise RecipeError(f"The {what} must be one of the files you attached.")


def _spec(request: str, summary: str, mode: str, ops: List[Dict[str, Any]], assumptions: Sequence[str],
          **extra: Any) -> Dict[str, Any]:
    return {"request": request, "summary": summary, "mode": mode, "ops": ops,
            "assumptions": list(assumptions), **extra}


def _quote(words: str) -> str:
    return f"“{words}”"


# ---- headers and footers ----
def _slot_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("_", " ").strip()).upper()


def slot_role(name: str) -> str:
    """"number", "title" or "" (a value the user types) for a prepared template's slot."""
    key = _slot_key(name)
    if key in {_slot_key(n) for n in NUMBER_SLOTS}:
        return "number"
    if key in {_slot_key(n) for n in TITLE_SLOTS}:
        return "title"
    return ""


def _template_sections(pkg) -> int:
    from .package import DocxPackage
    return len(DocxPackage.sect_prs(pkg.xml(pkg.main_part())))


def template_kind(template: Optional[Path]) -> Dict[str, Any]:
    """What kind of header/footer template the attached template is, told without a model (decision 2):
    ``prepared`` (yellow-highlighted or ``{{NAME}}`` placeholders: ``typed_slots`` are the ones the user types),
    ``reference`` (an ordinary specification whose own section number and title its headers or footers show:
    ``reference_slots`` are those names), or ``unusable`` with the reason. ``none`` when nothing is attached."""
    if template is None:
        return {"kind": "none", "typed_slots": [], "reference_slots": [], "sections": 0, "reason": ""}
    from .ops.base import JobException
    from .ops.hf_common import effective_refs
    from .ops.slots import extract_fill, field_runs, find_slots, value_tokens
    from .ooxml import paragraphs
    from .package import DocxPackage
    name = Path(template).name
    try:
        pkg = DocxPackage.from_path(template)
        secs = DocxPackage.sect_prs(pkg.xml(pkg.main_part()))
        parts: List[str] = []
        for shown in effective_refs(pkg, secs):
            for part in shown.values():
                if part and part not in parts and pkg.has(part):
                    parts.append(part)
        if not parts:
            return {"kind": "unusable", "typed_slots": [], "reference_slots": [], "sections": len(secs),
                    "reason": f"{name} has no header or footer to copy."}
        prepared: List[str] = []
        for part in parts:
            for p in paragraphs(pkg.xml(part)):
                try:
                    prepared += [s.name for s in find_slots(p, ("highlight", "braces"), ("yellow",), part)]
                except JobException as e:
                    return {"kind": "unusable", "typed_slots": [], "reference_slots": [], "sections": len(secs),
                            "reason": f"{name}: a placeholder in its header or footer cannot be used ({e.reason})."}
        if prepared:
            names = list(dict.fromkeys(prepared))
            return {"kind": "prepared", "typed_slots": [n for n in names if not slot_role(n)],
                    "filled_slots": [n for n in names if slot_role(n)], "reference_slots": [],
                    "sections": len(secs), "reason": ""}
        values: Dict[str, str] = {}
        for slot, rule in REFERENCE_FILLS.items():
            try:
                value = extract_fill(pkg, slot, rule)
            except JobException:
                continue
            if len("".join(value_tokens(value))) >= 3:
                values[slot] = value
        found: List[str] = []
        for part in parts:
            root = pkg.xml(part)
            in_field = field_runs(root)
            for p in paragraphs(root):
                try:
                    found += [s.name for s in find_slots(p, ["reference"], [], part, ref_values=values,
                                                         in_field=in_field)]
                except JobException as e:
                    return {"kind": "unusable", "typed_slots": [], "reference_slots": [], "sections": len(secs),
                            "reason": f"{name}: {e.reason}"}
        found = list(dict.fromkeys(found))
        if not found:
            return {"kind": "unusable", "typed_slots": [], "reference_slots": [], "sections": len(secs),
                    "reason": f"{name} has no highlighted or {{{{NAME}}}} placeholders, and its headers and footers do "
                              f"not show its own section number or title, so there is nothing to fill in."}
        return {"kind": "reference", "typed_slots": [], "reference_slots": found, "sections": len(secs),
                "reason": ""}
    except Exception as e:  # noqa: BLE001 — a template that cannot be read is said plainly, never raised
        return {"kind": "unusable", "typed_slots": [], "reference_slots": [], "sections": 0,
                "reason": f"{name} could not be read as a template ({type(e).__name__})."}


def _headers_footers_template(inputs: Dict[str, Any], ctx: Context) -> Built:
    if ctx.template is None:
        raise RecipeError("Attach your template first.")
    kind = template_kind(ctx.template)
    if kind["kind"] == "unusable":
        raise RecipeError(kind["reason"] + " Try it in Default mode, or prepare the template with yellow "
                                           "highlighted placeholders.")
    template = str(Path(ctx.template).resolve())
    values = inputs.get("slots") or {}
    if not isinstance(values, dict):
        raise RecipeError("The template's values must be typed one per placeholder.")
    sources: List[str] = []
    typed: List[str] = []
    fills: Dict[str, Any] = {}
    params: Dict[str, Any] = {"template": template}
    if kind["kind"] == "prepared":
        for slot in kind["filled_slots"]:
            fills[slot] = REFERENCE_FILLS["SECTION_NUMBER" if slot_role(slot) == "number" else "SECTION_TITLE"]
        for slot in kind["typed_slots"]:
            value = _text(values, slot, label=f"The value for “{slot}”")
            fills[slot] = {"rule": "literal", "value": value}
            sources.append(value)
            typed.append(f"{slot} → {_quote(value)}")
        params["template_sections"] = {"cover": 0, "body": 1} if kind["sections"] >= 2 else {"all": 0}
        assumptions = ["the template's yellow-highlighted and {{NAME}} placeholders are its slots",
                       "the section number and title in each document come from its 'SECTION nn nn nn' line and the "
                       "title under it (or after a dash on the same line)",
                       "header/footer distances and the different-first-page setting follow the template; page "
                       "margins stay as they are"]
        if kind["sections"] >= 2:
            assumptions.append("a document's first section gets the template's cover section, the other sections "
                               "its second section")
    else:
        for slot in kind["reference_slots"]:
            fills[slot] = REFERENCE_FILLS[slot]
        params["template_sections"] = {"all": 0}
        params["slot_markers"] = ["reference"]
        assumptions = ["the template is an ordinary specification: wherever its headers and footers show its own "
                       "section number or title, each document's own goes",
                       "the section number and title in each document come from its 'SECTION nn nn nn' line and the "
                       "title under it (or after a dash on the same line)",
                       "header/footer distances and the different-first-page setting follow the template; page "
                       "margins stay as they are"]
    params["slot_fills"] = fills
    mode = "untracked" if typed else "formatting"
    request = f"Match headers and footers to the template {Path(ctx.template).name}" + \
              (": " + "; ".join(typed) if typed else "")
    op = {"op": "apply_header_footer_template", "select": {"sections": "all"}, "params": params}
    return Built(_spec(request, "Give every section the template's headers and footers, each document's own section "
                                "number and title filled in.", mode, [op], assumptions), sources, request)


def _remove_headers_footers(inputs: Dict[str, Any], ctx: Context) -> Built:
    which = _choice(inputs, REMOVE_WHICH)
    kinds = {"both": ["header", "footer"], "headers": ["header"], "footers": ["footer"]}[which]
    words = {"both": "headers and footers", "headers": "headers", "footers": "footers"}[which]
    request = f"Remove the {words}"
    op = {"op": "remove_header_footer", "select": {"sections": "all", "kinds": kinds,
                                                   "types": ["default", "first", "even"]}, "params": {}}
    return Built(_spec(request, f"Remove every section's {words}, first-page and even-page ones included.",
                       "formatting", [op],
                       ["every section is included, with its first-page and even-page variants",
                        "page margins stay as they are; the space they used closes up"]), [], request)


def _watermark(inputs: Dict[str, Any], ctx: Context) -> Built:
    action = _choice(inputs, WATERMARK_ACTION)
    if action == "remove":
        request = "Remove the watermark"
        op = {"op": "remove_watermark", "params": {}}
        return Built(_spec(request, "Take the watermark out of every header.", "formatting", [op],
                           ["a document without a watermark is left as it is"]), [], request)
    text = _text(inputs, "text", label="The watermark's words", limit=100)
    request = f"Add the watermark {_quote(text)}"
    op = {"op": "add_watermark", "params": {"text": text, "replace": True}}
    return Built(_spec(request, f"Draw {_quote(text)} faintly behind every page, as Word's watermark.", "untracked",
                       [op], [f"the watermark's words are exactly {text}, as you typed them",
                              "Word's usual look: diagonal, silver, half transparent, sized to fit the page",
                              "a watermark already there is replaced; a document without a header is left as it is "
                              "(Spec Surgeon never makes a header)"]), [text], request)


def _swap_logo(inputs: Dict[str, Any], ctx: Context) -> Built:
    pictures = ctx.pictures()
    if not pictures:
        raise RecipeError("Attach the new logo picture first.")
    new = _attached(inputs.get("image"), pictures, "new logo picture")
    if new.suffix.lower() not in SWAP_PICTURES:
        raise RecipeError(f"{new.name}: the new logo must be a PNG, JPEG or GIF picture.")
    old_value = inputs.get("old")
    if old_value in (None, "", "all"):
        match: Any = "all"
        which = "every picture in the headers"
    else:
        old = _attached(old_value, pictures, "old logo picture")
        if old == new:
            raise RecipeError("The old and the new logo are the same picture.")
        match = {"image": str(old)}
        which = f"the picture {old.name}"
    request = f"Swap the logo in the headers for {new.name} ({which})"
    op = {"op": "swap_image", "params": {"image": str(new), "match": match, "fit": "contain", "allow_missing": True}}
    return Built(_spec(request, f"Replace {which} with {new.name}, fitted to the same space.", "formatting", [op],
                       [f"the old logo is {which}", "the new logo keeps its proportions inside the old one's space",
                        "a document without that picture is left as it is"]), [], request)


# ---- fonts ----
FONT_SCOPE = ("document", "headers", "footers", "footnotes", "endnotes")


def _pt(size: Optional[float]) -> str:
    return f"{size:g} pt" if size else "no one size"


def _font_request(template_name: str, font: str, size: Optional[float]) -> str:
    return f"Match the font to the template {template_name} ({font}" + (f" {size:g} pt" if size else "") + ")"


def _font_spec(font: str, size: Optional[float], size_from: List[float], template_name: str
               ) -> Tuple[Dict[str, Any], str]:
    """The font button's spec for the documents whose body text is in ``size_from`` (one size; empty when none could
    be measured, or the template has no size: then the font alone), and its one sentence: ``font`` on the runs that
    name a font, every style and the document defaults, and ``size`` only on the text in ``size_from``, so headings,
    notes and the other sizes keep theirs."""
    scope = list(FONT_SCOPE)
    sized = {"size": size, "size_from": size_from} if size and size_from else {}
    ops = [{"op": "set_font", "select": {"scope": scope, "runs": "all"},
            "params": {"font": font, **sized, "only_where_defined": True}},
           {"op": "set_style_font", "select": {"styles": "all"}, "params": {"font": font, **sized}},
           {"op": "set_doc_defaults_font", "params": {"font": font, **sized}}]
    assertion = {"kind": "effective_font", "value": font, "slots": ["ascii", "hAnsi", "cs"], **sized,
                 "scope": scope, "include_paragraph_marks": True}
    sizes_text = ", ".join(f"{s:g}" for s in size_from)
    request = _font_request(template_name, font, size if sized else None)
    assumptions = [f"the template's body text is {font}" + (f" {size:g} pt" if sized else ""),
                   "Symbol and Wingdings text (bullets, special characters) keeps its font",
                   "the East Asian font slot is left as it is"]
    if sized:
        assumptions.insert(1, f"only text in the documents' body size ({sizes_text} pt) changes size; headings, notes "
                              f"and other sizes keep theirs")
    return _spec(request, f"Set the text in {font}" + (f" {size:g} pt" if sized else "") +
                 ": runs that name a font, every style and the document defaults.", "formatting", ops, assumptions,
                 assertions=[assertion]), request


def _font_template(inputs: Dict[str, Any], ctx: Context) -> Built:
    """The template's body font and size on every document, each document's size changing only where it is that
    document's OWN body size (the size most of its body text is in, ``profile.body_text_fonts``). The documents are
    grouped by it: one body size in the batch, one spec, as always; two or more, a layout plan with one spec per size
    (``_font_layouts``), so a size that is the body in one document is never "the body" in another. A document with no
    measurable body size takes the font alone; one that cannot be read is left to the preview's refusal, with its
    reason."""
    if ctx.template is None:
        raise RecipeError("Attach your template first.")
    from .package import DocxPackage
    from .profile import body_text_fonts
    try:
        top = body_text_fonts(DocxPackage.from_path(ctx.template))
    except Exception as e:  # noqa: BLE001
        raise RecipeError(f"{Path(ctx.template).name} could not be read ({type(e).__name__}).") from e
    if not top or top[0]["share"] < MIN_FONT_SHARE or not top[0]["font"]:
        raise RecipeError(f"{Path(ctx.template).name} has no one body font (its text is too short or too mixed to "
                          f"tell). Try it in Default mode, or use a template with more body text.")
    font, size = top[0]["font"], top[0]["size_pt"]
    name = Path(ctx.template).name
    own: Dict[str, Optional[float]] = {}           # str(resolved path) → its body size; None where none is measurable
    unread: Dict[str, str] = {}
    for path in ctx.documents:
        key = str(Path(path).resolve())
        try:
            found = body_text_fonts(DocxPackage.from_path(path))
        except Exception as e:  # noqa: BLE001 — an unreadable document is set aside by the preview, with its reason
            unread[key] = type(e).__name__
            continue
        own[key] = found[0]["size_pt"] if found and found[0]["size_pt"] and size else None
    groups: Dict[Optional[float], List[str]] = {}
    for key, body in own.items():
        groups.setdefault(body, []).append(key)
    if len(groups) <= 1:
        body = next(iter(groups), None)
        spec, request = _font_spec(font, size, [body] if body else [], name)
        return Built(spec, [], request)
    return _font_layouts(font, size, name, groups, own, unread, ctx)


def _font_layouts(font: str, size: float, name: str, groups: Dict[Optional[float], List[str]],
                  own: Dict[str, Optional[float]], unread: Dict[str, str], ctx: Context) -> Built:
    """Two or more body sizes in the batch: a layout plan with one spec per body size, each on the documents whose
    body text is that size. A layout covers structure patterns (``layouts.route``), so documents built alike that
    differ in body size take the size most of them have (the first attached decides a tie) and the others are left
    out with the reason, to be pressed alone. Each layout passes the one gate on its own documents
    (``compiler.compose_submission``, as ``body_match``'s do); a layout the gate refuses leaves its documents out with
    the gate's reason. One body size left once the rest are left out is the ordinary single spec."""
    from collections import Counter
    from . import compiler
    from .body_match import gate_reason
    from .layouts import layout_spec, plan_from_submission, validate_layout_plan
    batch = ctx.batch()
    order = {str(Path(p).resolve()): i for i, p in enumerate(ctx.documents)}
    left: Dict[str, str] = {}
    patterns: Dict[Optional[float], List[str]] = {}           # body size → the patterns whose documents take it
    for pat in batch.prof["patterns"]:
        paths = [str(Path(p).resolve()) for p in batch.pattern_paths(pat)]
        for p in paths:
            if p in unread:   # the profiler read it, the size reading could not: never planned under another's size
                left[p] = f"Not matched: it could not be read ({unread[p]})."
        sized = [p for p in paths if p in own]
        if not sized:
            continue
        tally = Counter(own[p] for p in sized)
        winner = max(tally, key=lambda b: (tally[b], -min(order[p] for p in sized if own[p] == b)))
        first = min((p for p in sized if own[p] == winner), key=order.__getitem__)
        for p in sized:
            if own[p] != winner:
                left[p] = (f"Not matched with the others: it is built like {Path(first).name}, whose body text is "
                           f"{_pt(winner)} where its own is {_pt(own[p])}, and documents built alike take one plan. "
                           f"Press the button with it alone, or try it in Default mode.")
        patterns.setdefault(winner, []).append(pat["pattern_id"])
    live = {body: [p for p in docs if p not in left] for body, docs in groups.items()}
    live = {body: docs for body, docs in live.items() if docs}
    if len(live) <= 1:
        body = next(iter(live), None)
        spec, request = _font_spec(font, size, [body] if body else [], name)
        return Built(spec, [], request, left_out=left)
    request = _font_request(name, font, size)
    layouts: List[Dict[str, Any]] = []
    for body in sorted(live, key=lambda b: min(order[p] for p in live[b])):
        docs, pids = live[body], patterns.get(body) or []
        if not pids:
            continue           # the profiler lists no pattern for these: the preview sets them aside, with why
        spec, _ = _font_spec(font, size, [body] if body else [], name)
        layout = {"id": f"L{len(layouts) + 1}",
                  "label": ("body text " + _pt(body) if body else "no one body size") + ": "
                           + ", ".join(Path(p).name for p in docs[:3]) + (" …" if len(docs) > 3 else ""),
                  "pattern_ids": pids, "spec": spec}
        stub = {"kind": "layouts", "request": request, "layouts": [layout]}
        outcome, errors = compiler.compose_submission(layout_spec(stub, layout), batch, [request], patterns=pids,
                                                      leave_out=list(left))
        if errors:
            why = gate_reason(errors)
            for p in docs:
                left[p] = f"Not matched: {why}"
            continue
        layout["spec"] = outcome
        layouts.append(layout)
    if not layouts:
        return Built(None, [], request, left_out=left, gated=True)
    plan = plan_from_submission(
        {"summary": f"Set the text in {font} {size:g} pt: runs that name a font, every style and the document "
                    f"defaults; each document's size changes only where it is that document's own body size.",
         "assumptions": [f"the template's body text is {font} {size:g} pt",
                         "each document's body size is its own: the size most of its body text is in",
                         "Symbol and Wingdings text (bullets, special characters) keeps its font",
                         "the East Asian font slot is left as it is"],
         "layouts": layouts}, request)
    validate_layout_plan(plan)
    return Built(None, [], request, outcome=plan, left_out=left, gated=True)


def _body_formatting(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``body_match``: one plan per layout of the batch, each through the gate; documents it cannot match are left
    out with their reasons."""
    if ctx.template is None:
        raise RecipeError("Attach your template first.")
    from . import body_match, template_roles
    detected = template_roles.detect(ctx.template)
    try:
        roles = template_roles.resolve(ctx.template, template_roles.chosen_roles(inputs, detected["roles"]))
    except ValueError as e:
        raise RecipeError(f"{e} Try it in Default mode.") from e
    page = _flag(inputs, "page", True) and roles.page is not None
    font = _flag(inputs, "font", True) and roles.font is not None
    clear = _flag(inputs, "clear", True)
    parts = ["outline styles and numbering"] + (["page"] if page else []) + (["font"] if font else []) + \
            (["paragraph spacing, indents and alignment"] if clear else [])
    request = f"Match the body formatting to the template {Path(ctx.template).name} ({', '.join(parts)})"
    assumptions = ["each part of a section takes the template's style: " + "; ".join(template_roles.describe(roles)),
                   "outline numbers keep their values in the template's numbering (1.01 shows as 1.1); a number "
                   "that would change sets the document aside"]
    if page:
        assumptions.append(f"pages: {template_roles.page_words(roles.page)} (a document with a landscape page keeps "
                           f"its pages)")
    if font:
        assumptions.append("the outline, the section line, the title and END OF SECTION in the template's own fonts "
                           f"and sizes; other text of the body outside tables in {roles.font}"
                           + (f" ({roles.size:g} pt where it is in the documents' body size)" if roles.size else ""))
    if clear:
        assumptions.append("paragraphs follow their styles' spacing, indents and alignment; bold, italic and "
                           "underlined words stay")
    assumptions += ["words, tables and headers and footers stay as they are", "plain changes, not tracked"]
    plan, left, _ = body_match.build_layouts(roles, ctx, request, page=page, font=font, clear=clear,
                                             assumptions=assumptions)
    return Built(spec=None, sources=[], request=request, outcome=plan, left_out=left, gated=True)


# ---- text ----
def _pairs(inputs: Dict[str, Any]) -> List[Dict[str, str]]:
    raw = inputs.get("pairs") or []
    if not isinstance(raw, list):
        raise RecipeError("Type each find-and-replace pair on its own row.")
    pairs: List[Dict[str, str]] = []
    seen = set()
    for row in raw:
        if not isinstance(row, dict):
            raise RecipeError("Type each find-and-replace pair on its own row.")
        find = row.get("find") if isinstance(row.get("find"), str) else ""
        replace = row.get("replace") if isinstance(row.get("replace"), str) else ""
        if not find.strip() and not replace.strip():
            continue                                   # an empty row the form left
        if not find.strip():
            raise RecipeError("Each row needs the words to find.")
        if any(ch in find + replace for ch in "\r\n\t"):
            raise RecipeError("The words to find and their replacement must be one line each.")
        if find == replace:
            raise RecipeError(f"{_quote(find)} would be replaced by the same words.")
        if find.casefold() == replace.casefold():
            raise RecipeError(f"{_quote(find)} → {_quote(replace)} changes only capitals. A change of capitals is "
                              f"not a find-and-replace here; ask for it in Default mode.")
        if find in seen:
            raise RecipeError(f"{_quote(find)} is listed twice.")
        seen.add(find)
        pairs.append({"find": find, "replace": replace})
    return pairs


def _find_replace(inputs: Dict[str, Any], ctx: Context) -> Built:
    pairs = _pairs(inputs)
    table_value = inputs.get("table")
    table = _attached(table_value, ctx.tables(), "table of pairs") if table_value not in (None, "") else None
    if not pairs and table is None:
        raise RecipeError("Type at least one pair of words to find and replace, or choose an attached table.")
    tracked = _flag(inputs, "tracked")
    match_case = _flag(inputs, "match_case", True)
    whole_word = _flag(inputs, "whole_word", True)
    params: Dict[str, Any] = {"allow_missing": True, "tracked": tracked}
    if pairs:
        params["pairs"] = [{**p, "match_case": match_case, "whole_word": whole_word} for p in pairs]
    if table is not None:
        params["pairs_table"] = {"file": str(table)}
    listed = "; ".join(f"{_quote(p['find'])} → {_quote(p['replace'])}" for p in pairs)
    request = "Find and replace" + (f": {listed}" if listed else "") + \
              (f" (and the pairs in {table.name})" if table is not None else "")
    assumptions = ["the body, headers and footers are searched",
                   "whole words only" if whole_word else "matches inside longer words too",
                   "exact capitals" if match_case else "any capitals",
                   "a document with none of the words is left as it is",
                   "tracked changes, so a reviewer can accept or reject each one" if tracked else "clean copies"]
    op = {"op": "replace_table", "params": params}
    return Built(_spec(request, "Replace the words you listed with your replacements, in one pass.",
                       "content" if tracked else "untracked", [op], assumptions),
                 [p["replace"] for p in pairs if p["replace"]], request)


# ---- clean up ----
def _accept_changes(inputs: Dict[str, Any], ctx: Context) -> Built:
    request = "Accept all tracked changes"
    op = {"op": "resolve_tracked_changes", "params": {"action": "accept"}}
    return Built(_spec(request, "Accept every tracked change: insertions, deletions, moves and formatting changes.",
                       "untracked", [op],
                       ["the body, headers, footers, footnotes, endnotes and comments",
                        "a change Word must settle itself (a table cell inserted or merged, a change inside an "
                        "equation) sets the document aside"]), [], request)


def _remove_comments(inputs: Dict[str, Any], ctx: Context) -> Built:
    request = "Remove all comments"
    op = {"op": "remove_comments", "params": {"which": "all"}}
    return Built(_spec(request, "Remove every comment with its replies.", "formatting", [op],
                       ["a document without comments is left as it is"]), [], request)


def _remove_highlight(inputs: Dict[str, Any], ctx: Context) -> Built:
    request = "Remove highlighting"
    op = {"op": "remove_highlight", "params": {}}
    return Built(_spec(request, "Remove Word's text highlighting in every colour.", "formatting", [op],
                       ["highlighting is Word's highlighter pen, not shading",
                        "a document without highlighting is left as it is"]), [], request)


def _cleanup(op_name: str, request: str, summary: str, assumptions: List[str], params: Dict[str, Any],
             tracked: bool) -> Built:
    op = {"op": op_name, "params": {**params, "tracked": tracked}}
    return Built(_spec(request, summary, "content" if tracked else "untracked", [op],
                       assumptions + ["tracked changes, so a reviewer can accept or reject each one" if tracked
                                      else "clean copies"]), [], request)


def _remove_hidden_text(inputs: Dict[str, Any], ctx: Context) -> Built:
    return _cleanup("remove_hidden_text", "Remove hidden text",
                    "Remove the text Word hides (its Hidden font setting).",
                    ["hidden text is text with Word's Hidden setting; white or tiny text is not hidden",
                     "the body, headers, footers, footnotes and endnotes"], {}, _flag(inputs, "tracked"))


def _remove_empty_paragraphs(inputs: Dict[str, Any], ctx: Context) -> Built:
    how = _choice(inputs, EMPTY_HOW)
    summary = ("Where several blank lines stand together, keep one." if how == "collapse" else
               "Remove every blank paragraph.")
    return _cleanup("remove_empty_paragraphs", "Remove empty paragraphs" + (" (collapse runs of blank lines)"
                                                                             if how == "collapse" else " (all)"),
                    summary, ["the body only", "a numbered, bordered or bookmarked empty line is not blank"],
                    {"mode": how}, _flag(inputs, "tracked"))


# ---- files and properties ----
def _name_files(inputs: Dict[str, Any], ctx: Context) -> Built:
    pattern = _choice(inputs, NAME_PATTERN)
    number = _choice(inputs, NAME_NUMBER)
    title = _choice(inputs, NAME_TITLE)
    values = {"number": REFERENCE_FILLS["SECTION_NUMBER"]}
    transforms = {"number": number}
    if "{title}" in pattern:
        values["title"] = REFERENCE_FILLS["SECTION_TITLE"]
        transforms["title"] = title
    request = f"Name the copies {pattern}.docx"
    op = {"op": "name_output", "params": {"pattern": pattern, "values": values, "transforms": transforms}}
    return Built(_spec(request, f"Name each edited copy {pattern}.docx from its own section number and title.",
                       "formatting", [op],
                       ["the number and title come from each document's 'SECTION nn nn nn' line and its title",
                        "only the copies get the names; your files keep theirs",
                        "two documents that would get the same name are left untouched"]), [], request)


def _track_changes_off(inputs: Dict[str, Any], ctx: Context) -> Built:
    request = "Turn off the Track Changes setting"
    op = {"op": "set_document_state", "params": {"track_changes": "off"}}
    return Built(_spec(request, "The documents open with Track Changes off.", "formatting", [op],
                       ["pending tracked changes are neither accepted nor rejected (that is its own button)",
                        "a document whose tracking is locked with a password is left untouched"]), [], request)


def _clean_pdf(inputs: Dict[str, Any], ctx: Context) -> Built:
    """Two ordered steps, in this order: the comments go first, so a comment anchored in text a tracked deletion takes
    away no longer stops the tracked changes from being accepted; then every tracked change is accepted. The run saves
    a PDF of each verified copy (``export``); the copies are not the working set (``DocumentTask._end_export``)."""
    from .pdf_export import renderer
    backend, why = renderer()
    if backend is None:
        raise RecipeError(f"Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found "
                          f"({why}).")
    request = "Save clean PDFs: every comment removed, every tracked change accepted"
    stages = [
        {"name": "Remove every comment",
         "spec": _spec(request, "Remove every comment with its replies.", "formatting",
                       [{"op": "remove_comments", "params": {"which": "all"}}],
                       ["a document without comments is left as it is"])},
        {"name": "Accept every tracked change",
         "spec": _spec(request, "Accept every tracked change: insertions, deletions, moves and formatting changes.",
                       "untracked", [{"op": "resolve_tracked_changes", "params": {"action": "accept"}}],
                       ["the body, headers, footers, footnotes and endnotes",
                        "a change Word must settle itself (a table cell inserted or merged, a change inside an "
                        "equation) leaves the document without a PDF",
                        "the PDFs are saved beside the clean copies; the Word documents the next button works on "
                        "keep their comments and tracked changes"])},
    ]
    return Built({"summary": "Remove every comment, then accept every tracked change, then save a PDF of each copy.",
                  "stages": stages}, [], request, export="pdf")


def _title_property(inputs: Dict[str, Any], ctx: Context) -> Built:
    pattern = _text(inputs, "pattern", label="The title")
    if "{number}" not in pattern and "{title}" not in pattern:
        raise RecipeError("The title needs {number} or {title} where each document's own goes.")
    for key in ("{number}", "{title}"):
        if pattern.count(key) > 1:
            raise RecipeError(f"{key} may appear once.")
    if re.search(r"\{(?!number\}|title\})[^}]*\}", pattern):
        raise RecipeError("Only {number} and {title} can stand for each document's own values.")
    request = f"Set the document title property to {_quote(pattern)}"
    op = {"op": "set_document_properties", "params": {"from_document": {"title": pattern}}}
    return Built(_spec(request, "Set File → Info → Title from each document's own section number and title.",
                       "untracked", [op],
                       ["{number} and {title} are each document's own, from its 'SECTION nn nn nn' line and its title",
                        "a document whose number or title cannot be found is left untouched"]), [pattern], request)


REMOVE_WHICH = Field("which", "choice", "Remove", default="both",
                     options=(("both", "Headers and footers"), ("headers", "Headers only"), ("footers", "Footers only")))
WATERMARK_ACTION = Field("action", "choice", "Watermark", default="add",
                         options=(("add", "Add a watermark"), ("remove", "Remove the watermark")))
EMPTY_HOW = Field("how", "choice", "Which blank lines", default="collapse",
                  options=(("collapse", "Keep one where several stand together"), ("all", "Remove every one")))
NAME_PATTERN = Field("pattern", "choice", "Name", default=NAME_PATTERNS[0],
                     options=tuple((p, p.replace("{number}", "21 13 13").replace("{title}", "Wet-Pipe Sprinkler "
                                                                                             "Systems") + ".docx")
                                   for p in NAME_PATTERNS))
NAME_NUMBER = Field("number", "choice", "Section number", default="as_is",
                    options=(("as_is", "As written (21 13 13)"), ("digits", "Digits only (211313)")))
NAME_TITLE = Field("title", "choice", "Title", default="as_is",
                   options=(("as_is", "As written"), ("title", "Title Case"), ("upper", "UPPER CASE")))

RECIPES: Tuple[Recipe, ...] = (
    Recipe("headers_footers_template", "Match headers and footers to template", "Headers & footers",
           "Gives every document your template's headers and footers, with each document's own section number and "
           "title filled in.", ("template",), (Field("slots", "slots", "Values for the template's placeholders"),),
           _headers_footers_template),
    Recipe("remove_headers_footers", "Remove headers and footers", "Headers & footers",
           "Removes the headers, the footers or both from every section.", (), (REMOVE_WHICH,),
           _remove_headers_footers),
    Recipe("watermark", "Add or remove a watermark", "Headers & footers",
           "Draws your words faintly behind every page, or takes a watermark out.", (),
           (WATERMARK_ACTION, Field("text", "text", "Watermark words", placeholder="NOT FOR CONSTRUCTION",
                                    help="Exactly as they should appear; one line.")), _watermark),
    Recipe("swap_logo", "Swap the logo", "Headers & footers",
           "Replaces the logo in the headers with a picture you attach, fitted to the same space.", ("picture",),
           (Field("image", "picture", "New logo", required=True),
            Field("old", "picture", "Old logo", optional_choice="Every picture in the headers",
                  help="Attach the old logo too to swap only that picture.")), _swap_logo),
    Recipe("body_formatting", "Match body formatting to template", "Fonts & formatting",
           "Gives each document your template's outline styles and numbering, its page, its font, and lets the styles "
           "set paragraph spacing, indents and alignment. Words, tables, headers and footers stay as they are.",
           ("template",),
           (Field("roles", "roles", "Your template's style for each part of a section",
                  help="Read from your template; change any that is wrong."),
            Field("page", "check", "Page size and margins", default=True),
            Field("font", "check", "Font", default=True),
            Field("clear", "check", "Let the styles set paragraph spacing, indents and alignment", default=True,
                  help="Clears those three where a paragraph sets its own. Bold, italic and underlined words stay.")),
           _body_formatting),
    Recipe("font_template", "Match font to template", "Fonts & formatting",
           "Sets the text in your template's body font and size; headings, notes and other sizes keep theirs.",
           ("template",), (), _font_template),
    Recipe("find_replace", "Find and replace", "Text",
           "Replaces words you list with your replacements in the body, headers and footers.", (),
           (Field("pairs", "pairs", "Find → replace with"),
            Field("table", "table", "Or a table of pairs (first column: find; second: replace)"),
            Field("match_case", "check", "Match capitals exactly", default=True),
            Field("whole_word", "check", "Whole words only", default=True,
                  help="On: “NFPA 13” leaves “NFPA 13R” alone."),
            TRACKED), _find_replace),
    Recipe("accept_changes", "Accept all tracked changes", "Clean up",
           "Accepts every pending tracked change, so the copies are clean.", (), (), _accept_changes),
    Recipe("remove_comments", "Remove all comments", "Clean up",
           "Removes every comment and its replies.", (), (), _remove_comments),
    Recipe("remove_highlight", "Remove highlighting", "Clean up",
           "Removes Word's highlighter colour from all text.", (), (), _remove_highlight),
    Recipe("remove_hidden_text", "Remove hidden text", "Clean up",
           "Removes text Word hides with its Hidden font setting.", (), (TRACKED,), _remove_hidden_text),
    Recipe("remove_empty_paragraphs", "Remove empty paragraphs", "Clean up",
           "Removes blank lines from the body.", (), (EMPTY_HOW, TRACKED), _remove_empty_paragraphs),
    Recipe("name_files", "Name files by section number and title", "Files & properties",
           "Names each edited copy from its own section number and title.", (),
           (NAME_PATTERN, NAME_NUMBER, NAME_TITLE), _name_files),
    Recipe("title_property", "Set the document title property", "Files & properties",
           "Sets File → Info → Title from each document's own section number and title.", (),
           (Field("pattern", "text", "Title", required=True, default=DEFAULT_TITLE_PROPERTY,
                  help="{number} and {title} stand for each document's own; the other words are yours."),),
           _title_property),
    Recipe("track_changes_off", "Turn off Track Changes", "Files & properties",
           "The copies open with Word's Track Changes setting off.", (), (), _track_changes_off),
    Recipe("clean_pdf", "Save clean PDFs", "Files & properties",
           "Saves a PDF of every document with its comments removed and its tracked changes accepted; your Word "
           "documents stay as they are.", ("pdf",), (), _clean_pdf),
)
BY_ID: Dict[str, Recipe] = {r.id: r for r in RECIPES}


def catalog() -> List[Dict[str, Any]]:
    """The buttons as the panel shows them, in group order."""
    return [r.to_dict() for g in GROUPS for r in RECIPES if r.group == g]


def build(recipe_id: str, inputs: Optional[Dict[str, Any]], ctx: Context) -> Built:
    """The spec of one button press, or ``RecipeError`` with one sentence for the user."""
    recipe = BY_ID.get(recipe_id)
    if recipe is None:
        raise RecipeError("There is no such button.")
    if inputs is None:
        inputs = {}
    if not isinstance(inputs, dict):
        raise RecipeError("The button's form could not be read.")
    if not ctx.documents:
        raise RecipeError("Add the Word documents you want to edit.")
    return recipe.build(inputs, ctx)
