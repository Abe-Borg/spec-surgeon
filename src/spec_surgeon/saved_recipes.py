"""Saved recipes: changes the user ran, saved under a name to run again on other documents.

A recipe is saved from What you've done so far (``DocumentTask.runs``): the runs the user ticks, oldest first, each
one step or (a recipe run itself) several. A step is

* a **button** (Safe mode): the button and what its form held — ``recipes.build`` builds it again from the documents,
  the template and the pictures and tables attached when the recipe runs, as the button would;
* a **plan** (Default and Obliterate): the user's words and the plan they approved (an EditSpec, a chain or a plan
  per layout), the sentences it was shown with, the words it places and the kinds of file it reads. A template,
  picture or table it reads is the one attached when it runs (``remap``).

A run builds every step it keeps from the documents attached now, passes each through the one gate, and composes them
in the user's order into ONE plan (``combine``): ordered steps (a chain, ``chain.py``), or a plan per layout whose
layouts are chains when a step plans per layout. That plan passes the gate again (``compiler.compose_submission``) and
is previewed, approved and run like any other: invariant 7's one description of the change is the composed plan.
Nothing in a recipe calls a model: no planning, no visual check, no recovery (``gui.Session.model_free``).

What a recipe cannot keep is refused when it is saved, with the reason (``portable_plan``): a custom method (it is not a
plan the engine runs as written), values read from particular documents (bindings, typed or bound slot fills), and
capitalisation approved occurrence by occurrence. A find-and-replace's unticked occurrences belong to the documents
they were found in and are not kept; the next preview lists the occurrences again.

A recipe is edited in Manage (``Store.edit``): its name, what it is for, and its steps removed, put in another order or,
for a button, given another form; and **Save as a recipe** can add the runs it keeps to the end of a recipe already
saved (``Store.append``). A step saved from a request is never rewritten: its words and plan are what the user approved
(invariant 7), so it is kept, moved or removed whole. Every run of an edited recipe passes the gate, the preview and the
approval as before; an edit is refused when the recipe changed since the editor opened (``revision``), and so is a run
form opened before an edit (its skips and forms are by position: ``choose_steps``, ``Store.update_inputs``).

The mode a recipe belongs to is its steps' (``recipe_mode``): only buttons — Safe; a plan — Default; a plan approved in
Obliterate mode (its words may be Claude's, shown in its reply) — Obliterate. A recipe runs in its own mode and in any
with fewer guardrails (``runs_in``): Safe recipes everywhere, Default ones in Default and Obliterate, Obliterate ones in
Obliterate only. Default recipes are never offered in Safe mode, whose plans are its own buttons'.

Kept in ``recipes.json`` beside ``settings.json`` (``settings.config_dir``): plain JSON the user can read and delete,
written atomically; each change reads the file again and writes it under ``recipes.json.lock``, which every window's
change takes (the OS releases it when a window ends), so two windows do not undo each other's saves. Never kept: a
document, a template, a picture or a table — only their kind and name — and nothing about the conversation beyond the
user's own words and the approved plan.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

RECIPES_FILE = "recipes.json"
FORMAT = 1
SAFE, DEFAULT, OBLITERATE = "safe", "default", "obliterate"
MODES = (SAFE, DEFAULT, OBLITERATE)
_RANK = {SAFE: 0, DEFAULT: 1, OBLITERATE: 2}
MAX_NAME = 120                 # characters in a recipe's name
MAX_NOTE = 300                 # characters in what it is for
MAX_STEPS = 40                 # steps in one recipe
MAX_RECIPES = 500              # recipes kept
LOCK_WAIT_S = 2.0              # seconds a change waits while another window changes the file

# Why a run cannot be saved (``DocumentTask``'s run entries, shown in the save dialog beside the run)
WHY_CUSTOM = ("Claude wrote a custom method for it. A recipe keeps only plans the engine runs as they are written; "
              "ask for it again when you need it.")
WHY_BOUND = ("It was planned with values read from particular documents, so it cannot be repeated on others.")
WHY_CAPITALS = ("Its capitals were approved occurrence by occurrence in particular documents, so it cannot be "
                "repeated on others.")
WHY_NOT_KEPT = "What it ran was not kept, so it cannot be saved."


class RecipeRunError(ValueError):
    """A recipe cannot run on what is attached now, or a recipe operation cannot be done: one sentence for the user."""


def now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _quote(words: str) -> str:
    return f"“{words}”"


def new_id() -> str:
    return "r" + uuid.uuid4().hex[:12]


# ---- the steps ------------------------------------------------------------------------------------------------------

def button_step(button: str, inputs: Dict[str, Any], request: str) -> Dict[str, Any]:
    return {"kind": "button", "button": str(button), "inputs": copy.deepcopy(inputs or {}), "request": str(request)}


def specs_of(plan: Any) -> Iterator[Dict[str, Any]]:
    """Every EditSpec inside a plan: a plan per layout's layouts, a chain's stages, or the spec itself."""
    from .editspec import is_chain
    from .layouts import is_layout_plan
    if is_layout_plan(plan):
        for layout in plan.get("layouts") or []:
            if isinstance(layout, dict) and isinstance(layout.get("spec"), dict):
                yield from specs_of(layout["spec"])
    elif is_chain(plan):
        for st in plan.get("stages") or []:
            if isinstance(st, dict) and isinstance(st.get("spec"), dict):
                yield st["spec"]
    elif isinstance(plan, dict):
        yield plan


def _file_bound(spec: Dict[str, Any]) -> bool:
    """A spec that carries values read from particular documents: bindings, or a slot filled with typed or bound
    wording pinned to a document's bytes (``ops.slots``)."""
    if spec.get("bindings"):
        return True
    for o in spec.get("ops") or []:
        fills = (o.get("params") or {}).get("slot_fills") if isinstance(o, dict) else None
        if isinstance(fills, dict) and any(isinstance(rule, dict) and rule.get("rule") in ("typed", "binding")
                                           for rule in _rules(fills.values())):
            return True
    return False


def _rules(rules) -> Iterator[Dict[str, Any]]:
    for rule in rules:
        if isinstance(rule, dict):
            yield rule
            if rule.get("rule") == "first_match":
                yield from _rules(rule.get("rules") or [])


def portable_plan(plan: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """``(the plan as a recipe keeps it, "")`` or ``(None, why it cannot be kept)``. Kept: the approved plan with each
    find-and-replace's unticked occurrences dropped (they name the documents they were found in)."""
    if not isinstance(plan, dict):
        return None, WHY_NOT_KEPT
    out = copy.deepcopy(plan)
    for spec in specs_of(out):
        if _file_bound(spec):
            return None, WHY_BOUND
        for o in spec.get("ops") or []:
            if not isinstance(o, dict):
                continue
            if o.get("op") == "capitalize_defined_terms":
                return None, WHY_CAPITALS
            if o.get("op") == "replace_table" and isinstance(o.get("params"), dict):
                o["params"].pop("skip", None)
    return out, ""


def files_of(plan: Any) -> List[Dict[str, str]]:
    """The files a plan reads beside the documents, by kind and name, each once: ``{"kind": "template" | "picture" |
    "table", "name"}``."""
    from .editspec import input_slots
    from .inputs import KINDS
    out: List[Dict[str, str]] = []
    for spec in specs_of(plan):
        for o in spec.get("ops") or []:
            for d, k in input_slots(o):
                kind = "template" if k == "template" else KINDS.get(Path(d[k]).suffix.lower(), "file")
                entry = {"kind": kind, "name": Path(d[k]).name}
                if entry not in out:
                    out.append(entry)
    return out


def plan_step(request: str, plan: Dict[str, Any], sentences: Sequence[str], unguarded: bool) -> Dict[str, Any]:
    """A Default or Obliterate step from a plan ``portable_plan`` kept: the words it places are the ones the gate
    traced when it was approved — the user's own, or in Obliterate mode the reply's — and are what it may place when it
    runs again."""
    from .compiler import placed_words
    places: List[str] = []
    for spec in specs_of(plan):
        for word in placed_words(spec):
            if word not in places:
                places.append(word)
    return {"kind": "plan", "request": str(request), "plan": plan, "sentences": [str(s) for s in sentences],
            "places": places, "files": files_of(plan), "unguarded": bool(unguarded)}


def recipe_mode(steps: Sequence[Dict[str, Any]]) -> str:
    """Safe when every step is a button, Obliterate when a plan was approved in Obliterate mode, else Default."""
    if any(s.get("kind") == "plan" and s.get("unguarded") for s in steps):
        return OBLITERATE
    if any(s.get("kind") == "plan" for s in steps):
        return DEFAULT
    return SAFE


def runs_in(recipe_mode_: str, mode: str) -> bool:
    """A recipe runs in its own mode and in any with fewer guardrails; never in one with more."""
    return _RANK.get(mode, -1) >= _RANK.get(recipe_mode_, 99)


def not_here(recipe_mode_: str, mode: str) -> str:
    """Why a recipe does not run in ``mode``; "" when it does."""
    if runs_in(recipe_mode_, mode):
        return ""
    if recipe_mode_ == OBLITERATE:
        return "It was saved in Obliterate mode and runs only there."
    return "It was saved in Default mode: Safe mode runs only recipes made of its own buttons."


def _label(step: Dict[str, Any]) -> str:
    if step.get("kind") == "button":
        from .recipes import BY_ID
        recipe = BY_ID.get(str(step.get("button")))
        return recipe.label if recipe else str(step.get("button"))
    return "Your request"


def step_needs(step: Dict[str, Any]) -> List[str]:
    """What a step needs attached beside the documents: ``template``, ``picture``, ``table``."""
    if step.get("kind") == "button":
        from .recipes import BY_ID
        recipe = BY_ID.get(str(step.get("button")))
        needs = list(recipe.needs) if recipe else []
        table = (step.get("inputs") or {}).get("table")
        if isinstance(table, str) and table.strip() and "table" not in needs:
            needs.append("table")
        return needs
    return list(dict.fromkeys(f["kind"] for f in step.get("files") or [] if f.get("kind") in ("template", "picture",
                                                                                                "table")))


def step_view(step: Dict[str, Any]) -> Dict[str, Any]:
    """A step as the window shows it: its label, its sentence (a button's) or the user's words (a plan's), the
    plan's sentences, what it needs attached, the words it places, and a button's form to open it with."""
    out: Dict[str, Any] = {"kind": step.get("kind", ""), "label": _label(step), "text": str(step.get("request") or ""),
                           "plan": [str(s) for s in step.get("sentences") or []], "needs": step_needs(step),
                           "places": [str(w) for w in step.get("places") or []],
                           "unguarded": bool(step.get("unguarded"))}
    if step.get("kind") == "button":
        out["button"] = str(step.get("button"))
        out["inputs"] = copy.deepcopy(step.get("inputs") or {})
    else:
        out["files"] = [dict(f) for f in step.get("files") or []]
    return out


# ---- what a recipe keeps on disk -------------------------------------------------------------------------------------

def _text(value: Any, limit: int) -> Optional[str]:
    return value if isinstance(value, str) and len(value) <= limit else None


def _valid_step(step: Any) -> bool:
    if not isinstance(step, dict) or not isinstance(step.get("request"), str):
        return False
    if step.get("kind") == "button":
        return isinstance(step.get("button"), str) and isinstance(step.get("inputs"), dict)
    if step.get("kind") == "plan":
        strings = lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)  # noqa: E731
        return (isinstance(step.get("plan"), dict) and strings(step.get("sentences", []))
                and strings(step.get("places", [])) and isinstance(step.get("unguarded", False), bool)
                and isinstance(step.get("files", []), list)
                and all(isinstance(f, dict) and isinstance(f.get("kind"), str) and isinstance(f.get("name"), str)
                        for f in step.get("files", [])))
    return False


def valid_recipe(r: Any) -> bool:
    return (isinstance(r, dict) and isinstance(r.get("id"), str) and bool(r["id"])
            and _text(r.get("name"), MAX_NAME) is not None and bool(r["name"].strip())
            and _text(r.get("note", ""), MAX_NOTE) is not None
            and isinstance(r.get("saved", ""), str) and isinstance(r.get("last_run", ""), str)
            and isinstance(r.get("steps"), list) and 0 < len(r["steps"]) <= MAX_STEPS
            and all(_valid_step(s) for s in r["steps"]))


def _clean(r: Dict[str, Any]) -> Dict[str, Any]:
    steps = copy.deepcopy(r["steps"])
    return {"id": r["id"], "name": r["name"].strip(), "note": str(r.get("note", "")).strip(),
            "mode": recipe_mode(steps), "saved": str(r.get("saved", "")), "last_run": str(r.get("last_run", "")),
            "last_documents": int(r["last_documents"]) if type(r.get("last_documents")) is int else 0,
            "steps": steps}


def revision(r: Dict[str, Any]) -> str:
    """What an editor opened on (``recipe_view``'s ``rev``): its name, what it is for and its steps, never when it last
    ran — an edit made after another window changed any of them is refused instead of writing over that change."""
    kept = {"name": r.get("name", ""), "note": r.get("note", ""), "steps": r.get("steps", [])}
    return hashlib.sha256(json.dumps(kept, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def name_of(value: Any) -> str:
    """A recipe's name as typed: one line, not empty, at most ``MAX_NAME`` characters."""
    if not isinstance(value, str) or not value.strip():
        raise RecipeRunError("Type a name for the recipe.")
    value = " ".join(value.split())
    if len(value) > MAX_NAME:
        raise RecipeRunError(f"A recipe's name is at most {MAX_NAME} characters.")
    return value


def note_of(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RecipeRunError("What the recipe is for must be text.")
    value = " ".join(value.split())
    if len(value) > MAX_NOTE:
        raise RecipeRunError(f"What the recipe is for is at most {MAX_NOTE} characters.")
    return value


class Store:
    """The recipes in ``recipes.json``. Read again whenever the file changed; every change reads, changes and writes
    it atomically under one lock every window takes (``recipes.json.lock``, ``_changing``). An unreadable file is
    renamed ``recipes.json.bad`` (kept for inspection) and the list starts empty; a recipe that does not read is left
    out of the list and kept in the file until a change rewrites it."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from .settings import config_dir
            path = config_dir() / RECIPES_FILE
        self.path = Path(path)
        self._seen: Optional[Tuple[int, int]] = None
        self._recipes: List[Dict[str, Any]] = []

    def _stamp(self) -> Optional[Tuple[int, int]]:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _read(self, fresh: bool = False) -> List[Dict[str, Any]]:
        stamp = self._stamp()
        if stamp is not None and stamp == self._seen and not fresh:
            return self._recipes
        recipes: List[Dict[str, Any]] = []
        if stamp is not None:
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError as e:
                # held by another program for a moment (an antivirus scan, a sync client): the list as last read, and
                # the file is read again next time; never set aside for a read that failed. A change (``fresh``) is
                # refused instead: the list as last read may lack what another window saved since
                if fresh:
                    raise RecipeRunError(f"The recipes could not be read from {self.path.parent} "
                                         f"({e.strerror or e}); try again in a moment.") from e
                return self._recipes
            try:
                data = json.loads(text)
                if not isinstance(data, dict) or not isinstance(data.get("recipes"), list):
                    raise ValueError("not a recipes file")
                recipes = [_clean(r) for r in data["recipes"] if valid_recipe(r)]
            except ValueError:
                try:
                    self.path.replace(self.path.with_name(self.path.name + ".bad"))
                except OSError:
                    pass
                recipes, stamp = [], None
        self._recipes, self._seen = recipes, stamp
        return recipes

    def _write(self, recipes: List[Dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"format": FORMAT, "recipes": recipes}, indent=2, ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            raise RecipeRunError(f"The recipes could not be saved in {self.path.parent} ({e.strerror or e}).") from e
        self._recipes, self._seen = recipes, self._stamp()

    @contextmanager
    def _changing(self) -> Iterator[List[Dict[str, Any]]]:
        """The recipes as the file holds them now, to change and ``_write``, under the lock every window's changes take
        (released by the OS when a window ends, ``recovery_state.session_lock``): a save another window makes meanwhile
        waits for this one and then reads it, so neither is written over."""
        from .recovery_state import session_lock
        deadline = time.monotonic() + LOCK_WAIT_S
        with ExitStack() as held:
            while True:
                try:
                    held.enter_context(session_lock(self.path.parent, create=True, filename=self.path.name + ".lock"))
                    break
                except OSError as e:
                    raise RecipeRunError(f"The recipes could not be saved in {self.path.parent} "
                                         f"({e.strerror or e}).") from e
                except ValueError:
                    if time.monotonic() >= deadline:
                        raise RecipeRunError("Another window is changing the recipes; try again in a moment.") from None
                    time.sleep(0.02)
            yield copy.deepcopy(self._read(fresh=True))

    def all(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._read())

    def get(self, recipe_id: str) -> Dict[str, Any]:
        for r in self._read():
            if r["id"] == recipe_id:
                return copy.deepcopy(r)
        raise RecipeRunError("There is no such recipe; it may have been deleted in another window.")

    def _change(self, recipe_id: str, change) -> Dict[str, Any]:
        with self._changing() as recipes:
            for i, r in enumerate(recipes):
                if r["id"] == recipe_id:
                    recipes[i] = change(r)
                    self._write(recipes)
                    return copy.deepcopy(recipes[i])
        raise RecipeRunError("There is no such recipe; it may have been deleted in another window.")

    def add(self, name: str, note: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not steps:
            raise RecipeRunError("Tick at least one change to save.")
        if len(steps) > MAX_STEPS:
            raise RecipeRunError(f"A recipe holds at most {MAX_STEPS} steps.")
        record = _clean({"id": new_id(), "name": name_of(name), "note": note_of(note), "saved": now_text(),
                         "last_run": "", "steps": copy.deepcopy(steps)})
        with self._changing() as recipes:
            if len(recipes) >= MAX_RECIPES:
                raise RecipeRunError(f"You have {MAX_RECIPES} recipes; delete one before saving another.")
            recipes.append(record)
            self._write(recipes)
        return copy.deepcopy(record)

    def rename(self, recipe_id: str, name: str, note: Optional[str] = None) -> Dict[str, Any]:
        new_name = name_of(name)
        new_note = None if note is None else note_of(note)
        return self._change(recipe_id, lambda r: {**r, "name": new_name,
                                                  **({"note": new_note} if new_note is not None else {})})

    def duplicate(self, recipe_id: str) -> Dict[str, Any]:
        original = self.get(recipe_id)
        name = original["name"]
        copy_name = (name + " (copy)") if len(name) + 7 <= MAX_NAME else name[:MAX_NAME - 7] + " (copy)"
        return self.add(copy_name, original.get("note", ""), original["steps"])

    def delete(self, recipe_id: str) -> None:
        with self._changing() as recipes:
            kept = [r for r in recipes if r["id"] != recipe_id]
            if len(kept) == len(recipes):
                raise RecipeRunError("There is no such recipe; it may have been deleted in another window.")
            self._write(kept)

    def append(self, recipe_id: str, steps: List[Dict[str, Any]]) -> Dict[str, Any]:
        """**Save as a recipe**, added to the end of a recipe already saved: its steps, then these, in the order run."""
        if not steps:
            raise RecipeRunError("Tick at least one change to save.")

        def change(r):
            if len(r["steps"]) + len(steps) > MAX_STEPS:
                raise RecipeRunError(f"A recipe holds at most {MAX_STEPS} steps; “{r['name']}” has "
                                     f"{len(r['steps'])}.")
            return _clean({**r, "steps": [*r["steps"], *copy.deepcopy(steps)]})
        return self._change(recipe_id, change)

    def edit(self, recipe_id: str, rev: str, name: Any, note: Any, steps: Any) -> Dict[str, Any]:
        """**Edit** in Manage: its name, what it is for, and its steps — kept, removed or put in another order, a button
        step's form changed. ``steps`` lists the steps it keeps in their new order, each ``{"step": its number as saved,
        counted from 0, "inputs"?: the button's form}``; a step left out is removed. A step saved from a request runs
        as it was approved and is never rewritten here (its words and plan are what was approved; ask again and save
        that run instead). ``rev`` is the ``revision`` the editor opened on: a recipe changed since is refused."""
        new_name, new_note = name_of(name), note_of(note)
        if not isinstance(steps, list) or not all(isinstance(e, dict) for e in steps):
            raise RecipeRunError("The recipe's steps could not be read.")
        if not steps:
            raise RecipeRunError("Keep at least one step, or delete the recipe.")

        def change(r):
            if not isinstance(rev, str) or rev != revision(r):
                raise RecipeRunError("The recipe changed since you opened it, perhaps in another window; open it again.")
            out: List[Dict[str, Any]] = []
            seen = set()
            for entry in steps:
                n = entry.get("step")
                if type(n) is not int or not 0 <= n < len(r["steps"]) or n in seen:
                    raise RecipeRunError("The recipe's steps could not be read.")
                seen.add(n)
                step = copy.deepcopy(r["steps"][n])
                if entry.get("inputs") is not None:
                    if step["kind"] != "button" or not isinstance(entry["inputs"], dict):
                        raise RecipeRunError(f"Step {n + 1} was saved from a request and runs as you approved it; "
                                             f"only a button's form can be changed.")
                    step["inputs"] = copy.deepcopy(entry["inputs"])
                out.append(step)
            return _clean({**r, "name": new_name, "note": new_note, "steps": out})
        return self._change(recipe_id, change)

    def update_inputs(self, recipe_id: str, inputs: Sequence[Any], rev: Optional[str] = None) -> Dict[str, Any]:
        """A Safe recipe's buttons take the forms the run form holds (``Save these changes to the recipe``): one entry
        per step, a form for each button step, ``None`` to leave one as it is. ``rev``: the ``revision`` the run form
        opened on — the forms are by position, so a recipe whose steps were edited since is refused."""
        def change(r):
            if (rev is not None and rev != revision(r)) or not isinstance(inputs, (list, tuple)) \
                    or len(inputs) != len(r["steps"]):
                raise RecipeRunError("The recipe changed since its form was opened; open it again.")
            steps = copy.deepcopy(r["steps"])
            for step, form in zip(steps, inputs):
                if form is None:
                    continue
                if step["kind"] != "button" or not isinstance(form, dict):
                    raise RecipeRunError("Only a button's form can be changed in a recipe.")
                step["inputs"] = copy.deepcopy(form)
            return {**r, "steps": steps}
        return self._change(recipe_id, change)

    def ran(self, recipe_id: str, documents: int) -> None:
        """When it last ran, and on how many documents."""
        try:
            self._change(recipe_id, lambda r: {**r, "last_run": now_text(), "last_documents": int(documents)})
        except RecipeRunError:
            pass                                   # deleted in another window meanwhile: nothing to note


def recipe_view(r: Dict[str, Any], mode: str) -> Dict[str, Any]:
    """A recipe as the window lists it in ``mode``: its steps, what they need attached, and whether it runs here."""
    from .recipes import BY_ID
    steps = [step_view(s) for s in r["steps"]]
    missing = next((n for n, s in enumerate(r["steps"], 1) if s["kind"] == "button" and s["button"] not in BY_ID), 0)
    why = (f"Step {missing} uses a button this version of the app does not have." if missing
           else not_here(r["mode"], mode))
    return {"id": r["id"], "rev": revision(r), "name": r["name"], "note": r.get("note", ""), "mode": r["mode"],
            "saved": r.get("saved", ""), "last_run": r.get("last_run", ""),
            "last_documents": r.get("last_documents", 0), "steps": steps,
            "needs": list(dict.fromkeys(n for s in steps for n in s["needs"])),
            "runs_here": not why, "why_not_here": why}


# ---- running one ----------------------------------------------------------------------------------------------------

@dataclass
class RunBuild:
    """A recipe's run as the engine will execute it: the composed plan the gate accepted (None when no document can
    take it), the words it may place (``sources``, and in Obliterate ``shown``), the line the chat shows, the documents
    it leaves out with why, and the steps as they ran (what saving this run again keeps)."""
    outcome: Any
    sources: List[str]
    shown: List[str]
    request: str
    left_out: Dict[str, str] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    labels: List[str] = field(default_factory=list)
    nothing: bool = False


def choose_steps(record: Dict[str, Any], picks: Any, rev: Optional[str] = None) -> List[Tuple[int, Dict[str, Any]]]:
    """The steps this run keeps, in order, ``(number, step)``: ``picks`` names each saved step once — ``{"skip": bool,
    "inputs": {…}}``, the form a button step runs with this time (none: as saved). A plan step runs as saved. ``rev``:
    the ``revision`` the run form opened on — picks are by position, so a recipe whose steps were edited since (moved,
    removed, added) is refused rather than given a form or a skip meant for another step."""
    steps = record["steps"]
    if rev is not None and rev != revision(record):
        raise RecipeRunError("The recipe changed since its form was opened; open it again.")
    if picks is None:
        picks = [{} for _ in steps]
    if not isinstance(picks, list) or len(picks) != len(steps) or not all(isinstance(p, dict) for p in picks):
        raise RecipeRunError("The recipe changed since its form was opened; open it again.")
    out: List[Tuple[int, Dict[str, Any]]] = []
    for n, (step, pick) in enumerate(zip(steps, picks), 1):
        if pick.get("skip") is True:
            continue
        own = copy.deepcopy(step)
        if "inputs" in pick and pick["inputs"] is not None:
            if step["kind"] != "button" or not isinstance(pick["inputs"], dict):
                raise RecipeRunError(f"Step {n} has no form to change.")
            own["inputs"] = copy.deepcopy(pick["inputs"])
        out.append((n, own))
    if not out:
        raise RecipeRunError("Every step is skipped; keep at least one.")
    return out


def remap(plan: Dict[str, Any], template: Optional[Path], references: Sequence[Path], n: int) -> Dict[str, Any]:
    """The plan reading the files attached now (the owner's decision: a recipe uses whatever is attached when it
    runs): its template the attached template; a picture or table the attached one of the same name, else the only one
    of its kind. Raises ``RecipeRunError`` when the step's file is not there to read."""
    from .editspec import input_slots
    from .inputs import KINDS
    out = copy.deepcopy(plan)
    refs = [Path(p).resolve() for p in references]
    for spec in specs_of(out):
        for o in spec.get("ops") or []:
            for d, k in input_slots(o):
                saved = Path(d[k])
                if k == "template":
                    if template is None:
                        raise RecipeRunError(f"Step {n} uses a template ({saved.name}): attach one first.")
                    d[k] = str(Path(template).resolve())
                    continue
                kind = KINDS.get(saved.suffix.lower(), "file")
                same_kind = [p for p in refs if KINDS.get(p.suffix.lower()) == kind]
                named = [p for p in same_kind if p.name.casefold() == saved.name.casefold()]
                pick = named[0] if named else same_kind[0] if len(same_kind) == 1 else None
                if pick is None:
                    raise RecipeRunError(
                        f"Step {n} uses a {kind} ({saved.name}): attach it" +
                        (", or only one." if same_kind else " first."))
                d[k] = str(pick)
    return out


def _short(words: str, limit: int = 60) -> str:
    words = " ".join(str(words).split())
    return words if len(words) <= limit else words[:limit - 1] + "…"


def _narrowed(plan: Dict[str, Any], batch, n: int) -> Dict[str, Any]:
    """A plan per layout made for other documents, keeping in each layout only the structure patterns these documents
    have (the gate refuses a pattern the batch does not list) and only the layouts left with one."""
    have = {pat["pattern_id"] for pat in batch.prof.get("patterns") or []}
    out = copy.deepcopy(plan)
    layouts = []
    for layout in out.get("layouts") or []:
        pids = [p for p in layout.get("pattern_ids") or [] if p in have]
        if pids:
            layouts.append({**layout, "pattern_ids": pids})
    if not layouts:
        raise RecipeRunError(f"Step {n} was planned for documents built differently, and none of these is built like "
                             f"them.")
    out["layouts"] = layouts
    return out


def _pins(o: Any) -> Optional[List[str]]:
    """The structure patterns a spec or chain is pinned to (``applies_to``), or None when it plans on any."""
    from .editspec import is_chain
    spec = (o.get("stages") or [{}])[0].get("spec") if is_chain(o) else o
    applies = (spec or {}).get("applies_to") or {}
    if applies.get("pattern_ids") and applies.get("or") != "all":
        return [str(p) for p in applies["pattern_ids"]]
    return None


def _unpinned(spec: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(spec)
    out.pop("applies_to", None)
    return out


def _stages(o: Dict[str, Any], label: str, request: str) -> List[Dict[str, Any]]:
    """A step's plan as chain stages, each named by the step; the pin a layout's own spec carried is dropped (the
    combined layout pins its first stage again, ``layouts.layout_spec``)."""
    from .editspec import is_chain
    if is_chain(o):
        specs = [st["spec"] for st in o.get("stages") or []]
    else:
        specs = [o]
    out = []
    for spec in specs:
        own = _unpinned(spec)
        if not own.get("request"):
            own["request"] = request
        out.append({"name": label, "spec": own})
    return out


def combine(outcomes: Sequence[Tuple[Any, str, str]], batch, name: str) -> Tuple[Any, Dict[str, str]]:
    """The steps' gated plans ``(plan, label, request)`` as one plan in their order, and the documents it leaves out
    with why. One step: its own plan. Steps that plan on every document: one chain. A step that plans per layout (or
    is pinned to some structure patterns): a plan per layout over the batch's patterns, each layout the chain of the
    plans its documents take — a pattern a step does not cover is left out, with the step that does not cover it."""
    from .layouts import is_layout_plan, layout_spec
    if len(outcomes) == 1:
        return outcomes[0][0], {}
    coverage: List[Optional[Dict[str, Tuple[str, Dict[str, Any]]]]] = []
    for plan, label, request in outcomes:
        if is_layout_plan(plan):
            cov = {}
            for layout in plan.get("layouts") or []:
                spec = layout_spec(plan, layout)
                for pid in layout["pattern_ids"]:
                    cov[pid] = (str(layout["id"]), spec)
            coverage.append(cov)
        else:
            pins = _pins(plan)
            coverage.append(None if pins is None else {pid: ("pinned", plan) for pid in pins})
    if all(c is None for c in coverage):
        stages = [st for (plan, label, request) in outcomes for st in _stages(plan, label, request)]
        return {"summary": name, "stages": stages}, {}
    left: Dict[str, str] = {}
    groups: Dict[Tuple[str, ...], List[str]] = {}
    for pat in batch.prof.get("patterns") or []:
        pid = pat["pattern_id"]
        key: List[str] = []
        for (plan, label, request), cov in zip(outcomes, coverage):
            if cov is None:
                key.append("")
            elif pid in cov:
                key.append(cov[pid][0])
            else:
                for path in batch.pattern_paths(pat):
                    left.setdefault(str(Path(path).resolve()),
                                    f"Not changed: the step “{label}” plans for documents built differently, and "
                                    f"this one is not built like them.")
                break
        else:
            groups.setdefault(tuple(key), []).append(pid)
    layouts = []
    for n, (key, pids) in enumerate(groups.items(), 1):
        stages: List[Dict[str, Any]] = []
        for (plan, label, request), cov, part in zip(outcomes, coverage, key):
            own = plan if cov is None else cov[pids[0]][1]
            stages += _stages(own, label, request)
        layouts.append({"id": f"L{n}", "label": f"layout {n}", "pattern_ids": pids,
                        "spec": {"name": name, "description": name, "stages": stages}})
    if not layouts:
        return None, left
    return {"kind": "layouts", "request": name, "summary": name, "assumptions": [], "layouts": layouts}, left


def _gate_step(plan: Any, own: List[str], named: Tuple[str, str], earlier: Sequence[Tuple[Any, str, str]], batch,
               name: str, sources: Sequence[str], *, leave_out, unguarded: bool, shown: Sequence[str]
               ) -> Tuple[Any, List[str]]:
    """One step through the gate, on the documents as they are, its placed words traced to its own words (``own``).
    A step that plans on none of them (``PLANS_ON_NONE``: its words and attachments already passed) is planned again as
    the stage after the ``earlier`` steps, on the copies they make — a step that changes what an earlier one makes, as
    the run it was saved from did; it is then kept as it is, and the recipe's one plan is gated again whole."""
    from . import compiler
    out = [Path(p) for p in leave_out]
    gated, errors = compiler.compose_submission(plan, batch, own, leave_out=out, unguarded=unguarded, shown=shown)
    if not (errors and earlier and errors[0].startswith(compiler.PLANS_ON_NONE)):
        return gated, errors
    after, _ = combine([*earlier, (plan, *named)], batch, name)
    if after is None:
        return gated, errors
    _, later = compiler.compose_submission(after, batch, [*sources, *own], leave_out=out, unguarded=unguarded,
                                           shown=shown)
    return (plan, []) if not later else (gated, later)


def build_run(record: Dict[str, Any], chosen: Sequence[Tuple[int, Dict[str, Any]]], ctx, *,
              unguarded: bool = False) -> RunBuild:
    """Every kept step built from the documents attached now (``recipes.Context``), each through the gate on its own
    (as its button or its request would be), composed in order (``combine``) and through the gate again as the one plan
    the preview shows. A step that plans on none of the documents as they are (it changes what an earlier step makes:
    words an earlier step placed) is planned as the stage after the earlier steps, on the copies they make, as the run
    it was saved from was (``_gate_step``). Raises ``RecipeRunError`` naming the step that cannot be used."""
    from . import compiler, recipes
    from .layouts import is_layout_plan
    name = record["name"]
    request = f"Run the recipe {_quote(name)}"
    batch = ctx.batch()
    sources: List[str] = [request]
    shown: List[str] = []
    left_out: Dict[str, str] = {}
    outcomes: List[Tuple[Any, str, str]] = []
    steps: List[Dict[str, Any]] = []
    labels: List[str] = []
    for n, step in chosen:
        label = _label(step)
        if step["kind"] == "button":
            recipe = recipes.BY_ID.get(step["button"])
            if recipe is None:
                raise RecipeRunError(f"Step {n} uses a button this version of the app does not have.")
            try:
                built = recipes.build(recipe.id, step["inputs"], ctx)
            except recipes.RecipeError as e:
                raise RecipeRunError(f"Step {n}, {recipe.label}: {e}") from e
            left_out.update(built.left_out)
            sources += [built.request, *built.sources]
            if built.gated:
                plan, errors = built.outcome, []
                if plan is None:
                    raise RecipeRunError(f"Step {n}, {recipe.label}: none of the documents can take it as they are.")
            else:
                plan, errors = _gate_step(built.spec, [built.request, *built.sources], (recipe.label, built.request),
                                          outcomes, batch, name, sources, leave_out=built.left_out,
                                          unguarded=unguarded, shown=())
            if errors:
                raise RecipeRunError(f"Step {n}, {recipe.label}, cannot be used on these documents: "
                                     + "; ".join(errors[:3]))
            outcomes.append((plan, recipe.label, built.request))
            steps.append(button_step(recipe.id, step["inputs"], built.request))
            labels.append(built.request)
        else:
            plan = remap(step["plan"], ctx.template, ctx.references, n)
            if step.get("unguarded"):
                shown += list(step.get("places") or [])
            else:
                sources += list(step.get("places") or [])
            sources.append(step["request"])
            if is_layout_plan(plan):
                # a plan per layout made for other documents: its layouts keep the structure patterns these documents
                # have, and a document of a pattern none of them covers is left out
                plan = _narrowed(plan, batch, n)
                known = {pid for layout in plan["layouts"] for pid in layout["pattern_ids"]}
                for pat in batch.prof.get("patterns") or []:
                    if pat["pattern_id"] not in known:
                        for path in batch.pattern_paths(pat):
                            left_out.setdefault(str(Path(path).resolve()),
                                                f"Not changed: step {n} was planned for documents built differently, "
                                                f"and this one is not built like them.")
            plan, errors = _gate_step(plan, [step["request"], *step.get("places", [])],
                                      (_short(step["request"]) or f"Step {n}", step["request"]), outcomes, batch,
                                      name, sources, leave_out=left_out, unguarded=unguarded, shown=shown)
            if errors:
                raise RecipeRunError(f"Step {n} cannot be used on these documents: " + "; ".join(errors[:3]))
            outcomes.append((plan, _short(step["request"]) or f"Step {n}", step["request"]))
            steps.append(copy.deepcopy(step))
            labels.append(step["request"])
    combined, more = combine(outcomes, batch, name)
    for path, why in more.items():
        left_out.setdefault(path, why)
    if combined is None:
        return RunBuild(None, sources, shown, request, left_out, steps, labels, nothing=True)
    outcome, errors = compiler.compose_submission(combined, batch, sources, leave_out=[Path(p) for p in left_out],
                                                  unguarded=unguarded, shown=shown)
    nothing = bool(errors) and errors[0].startswith(compiler.PLANS_ON_NONE)
    if errors and not nothing:
        raise RecipeRunError("it cannot be used on these documents: " + "; ".join(errors[:3]))
    return RunBuild(outcome, sources, shown, request, left_out, steps, labels, nothing=nothing)
