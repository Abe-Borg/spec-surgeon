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
# reference rules. A slot is one by its name (compared by _slot_key, so case, underscores, spacing and enclosing
# brackets do not matter) or, for the number, by its shape: six placeholder digits in CSI's three pairs ("XX XX XX",
# "00 00 00", "000000", "## ## ##"), with an optional ".00" level. Any other slot is a value the user types in the
# button's form, the same in every copy.
NUMBER_SLOTS = {"SECTION_NUMBER", "SECTION NUMBER", "SECTION NO", "SECTION NO.", "SECTION #", "SPEC NUMBER", "SPEC NO",
                "SPEC NO.", "SPEC SECTION NUMBER", "SPECIFICATION NUMBER", "SPECIFICATION SECTION NUMBER", "NUMBER"}
NUMBER_SHAPE = re.compile(r"[0X#]{2}[ .-]?[0X#]{2}[ .-]?[0X#]{2}(?:\.[0X#]{2})?")
TITLE_SLOTS = {"SECTION_TITLE", "SECTION TITLE", "SECTION NAME", "SPEC TITLE", "SPEC NAME", "SPEC SECTION TITLE",
               "SPEC SECTION NAME", "SPECIFICATION TITLE", "SPECIFICATION NAME", "SPECIFICATION SECTION TITLE",
               "SPECIFICATION SECTION NAME", "TITLE"}
SWAP_PICTURES = {".png", ".jpg", ".jpeg", ".gif"}      # what swap_image places (a BMP or SVG is refused there)
MIN_FONT_SHARE = 0.5                                   # below it, the template's most used font is not its body text
NAME_PATTERNS = ("{number} - {title}", "{number} {title}", "{number}")
DEFAULT_TITLE_PROPERTY = "SECTION {number} - {title}"


class RecipeError(ValueError):
    """A button's form cannot be used as it is: one sentence for the user, nothing previewed."""


@dataclass(frozen=True)
class Field:
    """One input of a button's form, as the panel renders it (``kind``: text, lines, pairs, choice, check, picture,
    table, slots, roles). Consecutive fields with the same ``row`` sit side by side (the page margins)."""
    name: str
    kind: str
    label: str
    required: bool = False
    default: Any = None
    options: Tuple[Tuple[str, str], ...] = ()          # choice: (value, label)
    help: str = ""
    placeholder: str = ""
    optional_choice: str = ""                          # picture: the label of the choice that names no file
    row: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"name": self.name, "kind": self.kind, "label": self.label, "required": self.required}
        if self.default is not None:
            out["default"] = self.default
        if self.options:
            out["options"] = [{"value": v, "label": label} for v, label in self.options]
        for key in ("help", "placeholder", "optional_choice", "row"):
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


def _lines(inputs: Dict[str, Any], name: str, *, label: str, limit: int = 255) -> List[str]:
    """A list of one-line wordings, each typed in its own row: empty rows dropped, none twice, at least one."""
    raw = inputs.get(name) or []
    if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
        raise RecipeError(f"{label}: type each wording on its own row.")
    out: List[str] = []
    for value in raw:
        value = value.strip()
        if not value:
            continue
        if "\n" in value or "\r" in value or "\t" in value:
            raise RecipeError(f"{label}: each wording must be one line.")
        if len(value) > limit:
            raise RecipeError(f"{label}: a wording is longer than {limit} characters.")
        if value in out:
            raise RecipeError(f"{label}: {_quote(value)} is listed twice.")
        out.append(value)
    if not out:
        raise RecipeError(f"Type {label[0].lower() + label[1:]}.")
    return out


def _names(raw: str, *, label: str) -> List[str]:
    """Names typed in one box, separated by commas: blanks dropped, each once, in the order typed."""
    out: List[str] = []
    for name in raw.split(","):
        name = name.strip()
        if name and name not in out:
            out.append(name)
    if any(len(n) > 255 for n in out):
        raise RecipeError(f"{label}: a name is longer than 255 characters.")
    return out


def _inches(inputs: Dict[str, Any], name: str, *, label: str, required: bool = True) -> Optional[int]:
    """A length typed in inches (``1``, ``0.75``, ``1.25 in``, ``1"``), as twips; None for an empty optional box."""
    raw = _text(inputs, name, label=label, required=required, limit=12)
    if not raw:
        return None
    m = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)\s*(?:in|inch|inches|")?', raw, re.IGNORECASE)
    if not m:
        raise RecipeError(f"{label} must be a number of inches, such as 1 or 0.75.")
    value = float(m.group(1))
    if value > 22:
        raise RecipeError(f"{label} is more than 22 inches.")
    return int(round(value * 1440))


def _in(twips: int) -> str:
    return f"{twips / 1440:g} in"


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


def _and(items: Sequence[str]) -> str:
    items = list(items)
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


# ---- headers and footers ----
def _slot_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("_", " ").strip().strip("[](){}<>«»").strip()).upper()


def slot_role(name: str) -> str:
    """"number", "title" or "" (a value the user types) for a prepared template's slot."""
    key = _slot_key(name)
    if key in {_slot_key(n) for n in NUMBER_SLOTS} or NUMBER_SHAPE.fullmatch(key):
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
                    "filled_slots": [n for n in names if slot_role(n)],
                    "slot_roles": {n: slot_role(n) for n in names if slot_role(n)}, "reference_slots": [],
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
    """The button's sentence: the template's font (``template_name``), or the font typed in Change font (``""``)."""
    what = font + (f" {size:g} pt" if size else "")
    return f"Match the font to the template {template_name} ({what})" if template_name else f"Set the font to {what}"


def _font_origin(template_name: str, font: str, size: Optional[float]) -> str:
    """Where the font comes from, as the plan's first assumption."""
    what = font + (f" {size:g} pt" if size else "")
    return f"the template's body text is {what}" if template_name else f"the font you chose is {what}"


def _font_spec(font: str, size: Optional[float], size_from: List[float], template_name: str
               ) -> Tuple[Dict[str, Any], str]:
    """The font button's spec for the documents whose body text is in ``size_from`` (one size; empty when none could
    be measured, or no size is asked: then the font alone), and its one sentence: ``font`` on the runs that name a
    font, every style and the document defaults, and ``size`` only on the text in ``size_from``, so headings, notes
    and the other sizes keep theirs. ``template_name`` is the template's file name, or ``""`` for Change font."""
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
    assumptions = [_font_origin(template_name, font, size if sized else None),
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
    return _font_by_body_size(top[0]["font"], top[0]["size_pt"], Path(ctx.template).name, ctx)


FONT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &'+.()-]*")
FONT_SIZE = Field("size", "choice", "Size", default="keep",
                  options=(("keep", "Keep each document's sizes"), ("8", "8 pt"), ("9", "9 pt"), ("10", "10 pt"),
                           ("10.5", "10.5 pt"), ("11", "11 pt"), ("12", "12 pt")),
                  help="Only text in each document's own body size changes size; headings, notes and other sizes "
                       "keep theirs.")


def _set_font(inputs: Dict[str, Any], ctx: Context) -> Built:
    """Change font: the font the user typed, and the size chosen (or none), by the same rule as Match font to template
    (``_font_by_body_size``), with no template."""
    font = _text(inputs, "font", label="The font", limit=31)
    if not FONT_NAME.fullmatch(font):
        raise RecipeError("The font is a font's name, such as Arial or Times New Roman: letters, digits and spaces.")
    size = _choice(inputs, FONT_SIZE)
    return _font_by_body_size(font, None if size == "keep" else float(size), "", ctx)


def _font_by_body_size(font: str, size: Optional[float], name: str, ctx: Context) -> Built:
    """``font`` (and ``size``, when one is asked) on every document, each document's size changing only where it is
    that document's own body size; ``name`` is the template's file name, or ``""`` for Change font."""
    from .package import DocxPackage
    from .profile import body_text_fonts
    if not size:
        spec, request = _font_spec(font, None, [], name)          # the font alone: no document's size is read
        return Built(spec, [], request)
    own: Dict[str, Optional[float]] = {}           # str(resolved path) → its body size; None where none is measurable
    unread: Dict[str, str] = {}
    for path in ctx.documents:
        key = str(Path(path).resolve())
        try:
            found = body_text_fonts(DocxPackage.from_path(path))
        except Exception as e:  # noqa: BLE001 — an unreadable document is set aside by the preview, with its reason
            unread[key] = type(e).__name__
            continue
        own[key] = found[0]["size_pt"] if found and found[0]["size_pt"] else None
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
         "assumptions": [_font_origin(name, font, size),
                         "each document's body size is its own: the size most of its body text is in",
                         "Symbol and Wingdings text (bullets, special characters) keeps its font",
                         "the East Asian font slot is left as it is"],
         "layouts": layouts}, request)
    validate_layout_plan(plan)
    return Built(None, [], request, outcome=plan, left_out=left, gated=True)


PAPER = Field("paper", "choice", "Paper", default="letter",
              options=(("letter", "Letter (8.5 × 11 in)"), ("legal", "Legal (8.5 × 14 in)"),
                       ("tabloid", "Tabloid (11 × 17 in)"), ("a4", "A4 (210 × 297 mm)"),
                       ("keep", "Keep each document's own")),
              help="A paper size is set upright: a document with landscape pages is left untouched, with the reason.")
PAPER_SIZES = {"letter": (12240, 15840), "legal": (12240, 20160), "tabloid": (15840, 24480), "a4": (11906, 16838)}
MARGIN_SIDES = ("top", "bottom", "left", "right")
MARGINS = tuple(Field(f"margin_{side}", "text", f"{side.capitalize()} margin (in)", required=True, default="1",
                      row="margins") for side in MARGIN_SIDES)
HEADER_FOOTER_DISTANCE = Field("header_footer", "text", "Header and footer distance from the page edge (in)",
                               placeholder="Keep each document's own",
                               help="Leave it empty to keep each document's own.")


def _page_setup(inputs: Dict[str, Any], ctx: Context) -> Built:
    """The paper size and the four margins on every section (``set_page_setup``). A paper size is set upright, so a
    document with a landscape page is left out with the reason rather than turned upright; margins alone go on every
    document, landscape pages included, as Word's own Page Setup does for the whole document."""
    from .package import DocxPackage
    from .template_roles import read_page
    paper = _choice(inputs, PAPER)
    margins = {side: _inches(inputs, f"margin_{side}", label=f"The {side} margin") for side in MARGIN_SIDES}
    distance = _inches(inputs, "header_footer", label="The header and footer distance", required=False)
    params: Dict[str, Any] = {"margins": dict(margins)}
    if distance is not None:
        params["margins"].update(header=distance, footer=distance)
    words = (f"the margins to {_in(margins['top'])} top, {_in(margins['bottom'])} bottom, {_in(margins['left'])} "
             f"left and {_in(margins['right'])} right")
    assumptions = ["every section of every document",
                   (f"the header and footer sit {_in(distance)} from the page edge" if distance is not None
                    else "each document keeps its header and footer distances"),
                   "the text reflows, so the page count can change"]
    left: Dict[str, str] = {}
    if paper != "keep":
        width, height = PAPER_SIZES[paper]
        params["size"] = {"w": width, "h": height}
        words = f"the paper to {dict(PAPER.options)[paper]} and " + words
        assumptions.insert(1, "a document with a landscape page is left untouched, so its landscape pages are never "
                              "turned upright")
        for path in ctx.documents:
            try:
                pkg = DocxPackage.from_path(path)
                pages = [read_page(sp) for sp in DocxPackage.sect_prs(pkg.xml(pkg.main_part()))]
            except Exception:  # noqa: BLE001 — an unreadable document is set aside by the preview, with its reason
                continue
            if any(p is not None and p["orientation"] == "landscape" for p in pages):
                left[str(Path(path).resolve())] = (
                    "Not changed: it has landscape pages, and a paper size set on every section would turn them "
                    "upright. Set its pages in Word, choose Keep each document's own paper, or try it in Default mode.")
    request = f"Set {words}"
    op = {"op": "set_page_setup", "select": {"sections": "all"}, "params": params}
    spec = _spec(request, f"Set {words} on every section.", "formatting", [op], assumptions)
    if left and len(left) == len(ctx.documents):
        return Built(None, [], request, left_out=left, gated=True)
    return Built(spec, [], request, left_out=left)


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


def _tracked_words(tracked: bool) -> str:
    return "tracked changes, so a reviewer can accept or reject each one" if tracked else "clean copies"


def _issue_stamp(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``set_issue_stamp``: the stamp found by the wordings the user typed, wherever it sits, replaced by the new one."""
    current = _lines(inputs, "current", label="The stamp as it reads now")
    text = _text(inputs, "text", label="The new stamp")
    match_case = _flag(inputs, "match_case", True)
    for old in current:
        if old == text:
            raise RecipeError(f"{_quote(old)} already reads as the new stamp.")
        if old.casefold() == text.casefold():
            raise RecipeError(f"{_quote(old)} → {_quote(text)} changes only capitals. A change of capitals is not a "
                              f"stamp change here; ask for it in Default mode.")
    allow_missing = _flag(inputs, "allow_missing", True)
    tracked = _flag(inputs, "tracked")
    request = "Change the issue stamp " + " or ".join(_quote(s) for s in current) + f" to {_quote(text)}"
    op = {"op": "set_issue_stamp", "params": {"any_of": current, "text": text, "match_case": match_case,
                                               "allow_missing": allow_missing, "tracked": tracked}}
    assumptions = ["the stamp is found wherever it sits: the cover, a header or a footer",
                   "exact capitals" if match_case else "any capitals",
                   ("a document without the stamp is left as it is" if allow_missing
                    else "a document without the stamp is left untouched, with the reason"),
                   _tracked_words(tracked)]
    return Built(_spec(request, f"Replace the issue stamp with {_quote(text)} wherever it reads as you typed it.",
                       "content" if tracked else "untracked", [op], assumptions), [text], request)


SECTION_LINE = r"^\s*SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\b"   # capitals: never a "Section 230513" reference
END_LINE = re.compile(r"END\s+OF\s+SECTION\b", re.IGNORECASE)   # what ensure_end_of_section's detect_regex finds
EOS_LOOK = Field("look", "choice", "Its look", default="section_line",
                 options=(("section_line", "Like the SECTION line at the top"),
                          ("plain", "The document's plain text style")))


def _end_of_section(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``ensure_end_of_section``: the line the user typed, with each document's own number where ``{number}`` stands
    (read from its SECTION line by the reference rule), after the last content; one that reads otherwise is
    corrected. The line starts with END OF SECTION, the words the operation recognizes an end line by
    (``end_of_section.DEFAULT_DETECT``): a line in other words would not be found again in a document that already
    ends with it, and a second press would add another."""
    text = _text(inputs, "text", label="The line", limit=100)
    if text.count("{number}") != 1:
        raise RecipeError("The line needs {number} once, where each document's own section number goes.")
    if re.search(r"\{(?!number\})[^}]*\}", text):
        raise RecipeError("Only {number} can stand for each document's own value.")
    if not END_LINE.match(text):
        raise RecipeError("The line starts with END OF SECTION, so a document that already ends with it is "
                          "recognized and never gets a second one.")
    look = _choice(inputs, EOS_LOOK)
    tracked = _flag(inputs, "tracked")
    params: Dict[str, Any] = {"number": REFERENCE_FILLS["SECTION_NUMBER"], "text": text, "tracked": tracked}
    if look == "section_line":
        params.update(formatting="copy", copy_from_regex=SECTION_LINE)
    request = f"End every document with {_quote(text)}, each with its own section number"
    op = {"op": "ensure_end_of_section", "params": params}
    assumptions = ["{number} is each document's own, from its 'SECTION nn nn nn' line",
                   "a missing line is added after the last content; one that reads otherwise is corrected",
                   ("the new line is formatted like the SECTION line (a document where that line, in capitals, is "
                    "not found exactly once is left untouched)" if look == "section_line"
                    else "the new line is in the document's plain text style"),
                   "a document with content after its END OF SECTION line is left untouched",
                   _tracked_words(tracked)]
    return Built(_spec(request, "Make every document end with your line and its own section number.",
                       "content" if tracked else "untracked", [op], assumptions), [text], request)


UNIT = re.compile(r"[A-Za-z°%]+(?:/[A-Za-z]+)?")


def _nonbreaking_spaces(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``protect_nonbreaking_spaces``: a number and its unit, and a spaced section number, kept on one line."""
    units = _names(_text(inputs, "units", label="The units", required=False), label="The units")
    for unit in units:
        if not UNIT.fullmatch(unit):
            raise RecipeError(f"{_quote(unit)} is not a unit symbol: letters, such as psi or gpm (°, % and one / "
                              f"allowed).")
    section_numbers = _flag(inputs, "section_numbers", True)
    if not units and not section_numbers:
        raise RecipeError("Type at least one unit, or tick section numbers.")
    tracked = _flag(inputs, "tracked")
    kept = ([f"numbers with {_and(units)}"] if units else []) + \
           (["the parts of section numbers (21 13 13)"] if section_numbers else [])
    request = "Keep " + ", and ".join(kept) + (", on one line" if len(kept) > 1 else " on one line")
    op = {"op": "protect_nonbreaking_spaces",
          "params": {"units": units, "section_numbers": section_numbers, "tracked": tracked}}
    return Built(_spec(request, "Put non-breaking spaces where a line must not break; no words change.",
                       "content" if tracked else "untracked", [op],
                       ["the body, headers and footers", "only ordinary spaces become non-breaking ones",
                        _tracked_words(tracked)]), [], request)


# ---- clean up ----
CHANGES_ACTION = Field("action", "choice", "Every tracked change", default="accept",
                       options=(("accept", "Accept it"), ("reject", "Reject it")),
                       help="Reject: insertions go, deletions come back, moves and formatting changes are undone.")


def _accept_changes(inputs: Dict[str, Any], ctx: Context) -> Built:
    action = _choice(inputs, CHANGES_ACTION)
    request = "Accept all tracked changes" if action == "accept" else "Reject all tracked changes"
    summary = ("Accept every tracked change: insertions, deletions, moves and formatting changes." if action == "accept"
               else "Reject every tracked change: insertions go, deletions come back, moves and formatting changes "
                    "are undone.")
    op = {"op": "resolve_tracked_changes", "params": {"action": action}}
    return Built(_spec(request, summary, "untracked", [op],
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


def _clean_spaces(inputs: Dict[str, Any], ctx: Context) -> Built:
    return _cleanup("normalize_whitespace", "Clean up spaces",
                    "Turn two or more spaces in a row into one, and remove spaces at the end of a line.",
                    ["the body, headers and footers", "words, tabs and non-breaking spaces stay as they are"], {},
                    _flag(inputs, "tracked"))


QUOTES = Field("quotes", "choice", "Quotation marks", default="straight",
               options=(("straight", "Straight ( \" ' )"), ("curly", "Curly ( “ ” ‘ ’ )"),
                        ("keep", "Leave them as they are")))
DASHES = Field("dash", "choice", "Two hyphens (--)", default="keep",
               options=(("keep", "Leave them as they are"), ("en", "An en dash (–)"), ("em", "An em dash (—)")))
SEPARATOR = Field("section_separator", "choice", "Between the parts of a section number", default="keep",
                  options=(("keep", "Leave them as they are"), ("space", "A space (21 13 13)"),
                           ("hyphen", "A hyphen (21-13-13)")))
QUOTE_WORDS = {"straight": "straight quotation marks", "curly": "curly quotation marks"}
DASH_WORDS = {"en": "an en dash for each “--”", "em": "an em dash for each “--”"}
SEPARATOR_WORDS = {"space": "a space between the parts of a section number",
                   "hyphen": "a hyphen between the parts of a section number"}


def _quotes_dashes(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``normalize_punctuation`` with the choices the user made; at least one."""
    quotes, dash, separator = _choice(inputs, QUOTES), _choice(inputs, DASHES), _choice(inputs, SEPARATOR)
    params: Dict[str, Any] = {}
    asked: List[str] = []
    if quotes != "keep":
        params["quotes"] = quotes
        asked.append(QUOTE_WORDS[quotes])
    if dash != "keep":
        params["dash"] = dash
        asked.append(DASH_WORDS[dash])
    if separator != "keep":
        params["section_separator"] = separator
        asked.append(SEPARATOR_WORDS[separator])
    if not params:
        raise RecipeError("Choose at least one change: the quotation marks, the dashes or the section numbers.")
    assumptions = ["the body, headers and footers"]
    if quotes == "curly":
        assumptions.append("a quotation mark right after a digit stays straight, as an inch or foot mark")
    return _cleanup("normalize_punctuation", "Use " + _and(asked), "Use " + _and(asked) + ".", assumptions, params,
                    _flag(inputs, "tracked"))


LINKS = Field("which", "choice", "Which links", default="external",
              options=(("external", "Web and e-mail links"),
                       ("internal", "Links to a place in the document (a table of contents)"),
                       ("all", "Every link")))
LINK_WORDS = {"external": "the web and e-mail links", "internal": "the links to places in the document",
              "all": "every link"}


def _remove_hyperlinks(inputs: Dict[str, Any], ctx: Context) -> Built:
    which = _choice(inputs, LINKS)
    keep_style = _flag(inputs, "keep_style")
    request = f"Remove {LINK_WORDS[which]}"
    op = {"op": "remove_hyperlinks", "params": {"which": which, "keep_style": keep_style}}
    return Built(_spec(request, f"Take the link off {LINK_WORDS[which]}; the words stay.", "formatting", [op],
                       ["the linked words stay exactly as they are",
                        ("they keep the blue underline" if keep_style
                         else "they lose the blue underline and look like the text around them"),
                        "a picture's link stays", "a document with no such link is left as it is"]), [], request)


FIELDS_WHICH = Field("which", "choice", "Which fields", default="all",
                     options=(("all", "Every field but page numbers"),
                              ("dates", "Dates (DATE, SAVEDATE, PRINTDATE, CREATEDATE)"),
                              ("references", "Cross-references (REF, NOTEREF)"),
                              ("properties", "Document properties (DOCPROPERTY, TITLE, AUTHOR, FILENAME …)")))
FIELD_TYPES: Dict[str, Any] = {
    "all": "all",
    "dates": ["DATE", "TIME", "SAVEDATE", "PRINTDATE", "CREATEDATE", "EDITTIME"],
    "references": ["REF", "NOTEREF"],
    "properties": ["DOCPROPERTY", "DOCVARIABLE", "TITLE", "SUBJECT", "AUTHOR", "KEYWORDS", "COMMENTS",
                   "LASTSAVEDBY", "FILENAME"],
}
FIELD_WORDS = {"all": "every field but page numbers", "dates": "the date fields",
               "references": "the cross-references", "properties": "the document-property fields"}


def _freeze_fields(inputs: Dict[str, Any], ctx: Context) -> Built:
    which = _choice(inputs, FIELDS_WHICH)
    request = f"Freeze {FIELD_WORDS[which]} as plain text"
    op = {"op": "unlink_fields", "params": {"types": FIELD_TYPES[which]}}
    return Built(_spec(request, f"Turn {FIELD_WORDS[which]} into the text they show now; Word no longer updates it.",
                       "formatting", [op],
                       ["page numbers are never frozen", "what each field shows now stays, with its formatting",
                        "a field over two paragraphs (a table of contents) sets the document aside",
                        "a document with no such field is left as it is"]), [], request)


STYLES_WHICH = Field("which", "choice", "Which styles", default="custom",
                     options=(("custom", "Only the document's own styles"), ("all", "Word's built-in styles too")))


def _remove_unused_styles(inputs: Dict[str, Any], ctx: Context) -> Built:
    which = _choice(inputs, STYLES_WHICH)
    keep = _names(_text(inputs, "keep", label="The styles to keep", required=False, limit=1000),
                  label="The styles to keep")
    params: Dict[str, Any] = {"which": which}
    if keep:
        params["keep"] = keep
    request = "Remove the unused styles" + (" (Word's built-in ones too)" if which == "all" else "") + \
              (f", keeping {_and([_quote(k) for k in keep])}" if keep else "")
    op = {"op": "remove_unused_styles", "params": params}
    return Built(_spec(request, "Delete the styles nothing in the document uses.", "formatting", [op],
                       ["a style a used style is based on, followed by or linked to stays",
                        "a style a field or a table of contents would use stays",
                        "Word brings back its own built-in style if it is used later",
                        "a document with nothing unused is left as it is"]), [], request)


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


PROPERTY_FIELDS = (("author", "Author"), ("company", "Company"), ("subject", "Subject"))


def _author_company(inputs: Dict[str, Any], ctx: Context) -> Built:
    """``set_document_properties``: Author, Company and Subject to the user's words, or the author names cleared."""
    values = {key: _text(inputs, key, label=label, required=False) for key, label in PROPERTY_FIELDS}
    values = {k: v for k, v in values.items() if v}
    clear = _flag(inputs, "clear_authors")
    if clear and "author" in values:
        raise RecipeError("Type an author or clear the author names, not both.")
    if not values and not clear:
        raise RecipeError("Type an author, a company or a subject, or tick Clear the author names.")
    params: Dict[str, Any] = {}
    said: List[str] = []
    if values:
        params["set"] = values
        said += [f"{dict(PROPERTY_FIELDS)[k]} to {_quote(v)}" for k, v in values.items()]
    if clear:
        params["clear"] = ["author", "last_modified_by"]
    request = ("Set " + _and(said) if said else "") + \
              ((" and clear" if said else "Clear") + " the author names" if clear else "")
    op = {"op": "set_document_properties", "params": params}
    assumptions = ["File → Info: only the properties named change; one already as asked is left alone",
                   "tracked changes and comments keep their authors' names"]
    if clear:
        assumptions.insert(1, "clearing the author names removes Author and Last Modified By")
    return Built(_spec(request, request + " in File → Info.", "untracked", [op], assumptions),
                 list(values.values()), request)


LANGUAGE = Field("language", "choice", "Language", default="en-US",
                 options=(("en-US", "English (United States)"), ("en-GB", "English (United Kingdom)"),
                          ("en-CA", "English (Canada)"), ("fr-CA", "French (Canada)"),
                          ("es-US", "Spanish (United States)")))
LANGUAGE_SCOPE = Field("only", "choice", "Change", default="every",
                       options=(("every", "Every language to this one"),
                                ("english", "Only other kinds of English to this one")),
                       help="Only other kinds of English: French or Spanish quotations keep their language.")
ENGLISH = ("en-US", "en-GB", "en-CA", "en-AU", "en-NZ", "en-IE", "en-ZA", "en-IN")


def _proofing_language(inputs: Dict[str, Any], ctx: Context) -> Built:
    language, only = _choice(inputs, LANGUAGE), _choice(inputs, LANGUAGE_SCOPE)
    name = dict(LANGUAGE.options)[language]
    params: Dict[str, Any] = {"language": language}
    if only == "english":
        if not language.startswith("en-"):
            raise RecipeError("“Only other kinds of English” turns them into an English; choose one, or change every "
                              "language.")
        params["replace_only"] = [tag for tag in ENGLISH if tag != language]
    request = f"Set the proofing language to {name}" + (" (other kinds of English only)" if only == "english" else "")
    op = {"op": "set_language", "params": params}
    return Built(_spec(request, f"Spelling and grammar check the text as {name}.", "formatting", [op],
                       ["every style, run and paragraph mark that names a language, and the document default",
                        ("only text in another kind of English changes; other languages stay" if only == "english"
                         else "text in every other language changes too"),
                        "no text changes"]), [], request)


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
    Recipe("set_font", "Change font", "Fonts & formatting",
           "Sets the text in the font and size you choose, no template needed; headings, notes and other sizes "
           "keep theirs.", (),
           (Field("font", "text", "Font", required=True, placeholder="Arial",
                  help="The font's name exactly as Word lists it."), FONT_SIZE), _set_font),
    Recipe("page_setup", "Page size and margins", "Fonts & formatting",
           "Sets the paper size and the four margins on every section of every document.", (),
           (PAPER, *MARGINS, HEADER_FOOTER_DISTANCE), _page_setup),
    Recipe("find_replace", "Find and replace", "Text",
           "Replaces words you list with your replacements in the body, headers and footers.", (),
           (Field("pairs", "pairs", "Find → replace with"),
            Field("table", "table", "Or a table of pairs (first column: find; second: replace)"),
            Field("match_case", "check", "Match capitals exactly", default=True),
            Field("whole_word", "check", "Whole words only", default=True,
                  help="On: “NFPA 13” leaves “NFPA 13R” alone."),
            TRACKED), _find_replace),
    Recipe("issue_stamp", "Change the issue stamp", "Text",
           "Finds the issue stamp wherever it sits, on the cover, in a header or in a footer, and replaces every one "
           "with your new wording.", (),
           (Field("current", "lines", "The stamp as it reads now", required=True, placeholder="ISSUED FOR BID",
                  help="Each wording exactly as it reads; any of them is replaced."),
            Field("text", "text", "The new stamp", required=True, placeholder="ISSUED FOR CONSTRUCTION",
                  help="Exactly as it should appear; one line."),
            Field("match_case", "check", "Match capitals exactly", default=True),
            Field("allow_missing", "check", "Leave a document without the stamp as it is", default=True,
                  help="Off: such a document waits under Left untouched, with the reason."),
            TRACKED), _issue_stamp),
    Recipe("end_of_section", "Add END OF SECTION where missing", "Text",
           "Ends every document with your closing line and its own section number, and corrects one that reads "
           "otherwise.", (),
           (Field("text", "text", "The line", required=True, default="END OF SECTION {number}",
                  help="It starts with END OF SECTION; {number} is each document's own section number, from its "
                       "SECTION line, and appears once."),
            EOS_LOOK, TRACKED), _end_of_section),
    Recipe("nonbreaking_spaces", "Keep numbers with their units", "Text",
           "Puts non-breaking spaces in “150 psi” and “21 13 13” so Word never splits them across two lines; no "
           "words change.", (),
           (Field("units", "text", "Units", default="psi, gpm",
                  help="Unit symbols separated by commas, such as psi, gpm, ft, in or °F."),
            Field("section_numbers", "check", "Section numbers too (21 13 13)", default=True), TRACKED),
           _nonbreaking_spaces),
    Recipe("accept_changes", "Accept or reject all tracked changes", "Clean up",
           "Accepts every pending tracked change, so the copies are clean, or rejects every one.", (),
           (CHANGES_ACTION,), _accept_changes),
    Recipe("remove_comments", "Remove all comments", "Clean up",
           "Removes every comment and its replies.", (), (), _remove_comments),
    Recipe("remove_highlight", "Remove highlighting", "Clean up",
           "Removes Word's highlighter colour from all text.", (), (), _remove_highlight),
    Recipe("remove_hidden_text", "Remove hidden text", "Clean up",
           "Removes text Word hides with its Hidden font setting.", (), (TRACKED,), _remove_hidden_text),
    Recipe("remove_empty_paragraphs", "Remove empty paragraphs", "Clean up",
           "Removes blank lines from the body.", (), (EMPTY_HOW, TRACKED), _remove_empty_paragraphs),
    Recipe("clean_spaces", "Clean up spaces", "Clean up",
           "Turns two or more spaces in a row into one and removes spaces at the end of a line; tabs stay.", (),
           (TRACKED,), _clean_spaces),
    Recipe("quotes_dashes", "Standardize quotes and dashes", "Clean up",
           "Makes every quotation mark straight or curly, turns “--” into a dash, and writes section numbers one way.",
           (), (QUOTES, DASHES, SEPARATOR, TRACKED), _quotes_dashes),
    Recipe("remove_hyperlinks", "Remove hyperlinks", "Clean up",
           "Takes the links off web and e-mail addresses; the words stay and look like the text around them.", (),
           (LINKS, Field("keep_style", "check", "Keep the blue underline", default=False)), _remove_hyperlinks),
    Recipe("freeze_fields", "Freeze fields as plain text", "Clean up",
           "Turns fields into the text they show now, so dates and cross-references stop changing; page numbers "
           "are never frozen.", (), (FIELDS_WHICH,), _freeze_fields),
    Recipe("remove_unused_styles", "Remove unused styles", "Clean up",
           "Deletes the styles nothing in the document uses, so the Styles pane lists only what is in use.", (),
           (STYLES_WHICH, Field("keep", "text", "Styles to keep (optional)", placeholder="CMT, SpecifierNote",
                                help="Style names separated by commas; these stay even when unused.")),
           _remove_unused_styles),
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
    Recipe("author_company", "Set author and company", "Files & properties",
           "Sets File → Info → Author, Company and Subject to your words, or clears the author names before issue.",
           (), (*(Field(key, "text", label) for key, label in PROPERTY_FIELDS),
                Field("clear_authors", "check", "Clear the author names (Author and Last Modified By)", default=False,
                      help="Tracked changes and comments keep their authors' names.")), _author_company),
    Recipe("proofing_language", "Set proofing language", "Files & properties",
           "Sets the language Word checks spelling and grammar in; no text changes.", (),
           (LANGUAGE, LANGUAGE_SCOPE), _proofing_language),
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
