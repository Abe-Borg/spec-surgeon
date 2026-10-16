"""Your library — named, reusable solutions, kept on this computer (decided 2026-10-15; sharing comes later).

* A **recipe** is a saved EditSpec (built from the built-in operations): ``recipes/<slug>.json`` — an ordinary spec file,
  so it loads, previews and runs exactly like any spec (``Session.load_spec``, ``run-spec``) — plus
  ``<slug>.meta.json`` (name, description, when, the request). Template paths are stored absolute; structure-pattern
  pins (``applies_to``) are dropped (they would not match another project's files).
* A **chain** (2026-10-16) is a saved sequence of specs run one after another (``chain.py``): ``chains/<slug>.json`` —
  the stages with their specs embedded as they were when the chain was saved (a recipe edited later does not change a
  chain that was built from it; the stage keeps the recipe's name) — plus ``<slug>.meta.json``. It previews and runs
  through ``preview.make_chain_preview`` / ``chain.run_chain`` (the window's *Chain recipes…*, ``spec-surgeon chain``,
  ``library use``). Claude never offers a chain as a choice (its words are its recipes' words, approved recipe by
  recipe — a later round).
* An **operation** is a saved one-off custom transform (AI-written, passed verification when it was made):
  ``operations/<slug>/op.py`` + ``manifest.json`` (name, description, the request and conversation it came from, its
  select/params, the sha256 of op.py, the model, its attempts). It is never trusted more than the day it was made:
  using it re-runs it on the current batch's sample files (sandbox + the full suite) before its preview, and Run
  verifies every file again — edited copies only, never over your files. A manifest whose op.py changed is refused.
* A **snippet** is a saved paragraph (built-in batch 1, item 9): ``snippets/<slug>.json`` (``text``, ``style``,
  ``formatting``) + ``<slug>.meta.json`` (name, description, when, where the text came from). Typed, or taken from a
  document's paragraph (``snippet_from_document``); one plain paragraph — no tab, line break or character the user
  cannot see. It is inserted by ``insert_paragraph`` (``snippet_spec``: after — or before — the paragraphs whose text
  matches an anchor, in every file that has one, at most one per file), or offered by Claude in a conversation (the
  pick approves its words).

Names become file names safely: casefolded letters, digits and hyphens (≤60), Windows device names refused, every
lookup an exact match against the listing — a name can never reach outside the library folder. A name belongs to one
item: ``replace`` replaces an item of the same kind only, and a name an item of the other kind holds is refused even
with it (``other_kind_holding``) — never a recipe and an operation that one name would leave ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .settings import config_dir

KINDS = {"recipe": "recipes", "operation": "operations", "chain": "chains", "snippet": "snippets"}
FILE_KINDS = ("recipe", "chain", "snippet")       # one JSON file + <slug>.meta.json; an operation is a folder
LABELS = {"recipe": "Recipe", "operation": "My operation", "chain": "Chain", "snippet": "Snippet"}
FORMATTINGS = ("inherit", "none")
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


class LibraryError(ValueError):
    pass


@dataclass
class Item:
    kind: str                                     # recipe | operation | chain
    name: str
    slug: str
    path: Path                                    # the spec file / the operation's folder / the chain file
    description: str = ""
    request: str = ""
    saved_at: str = ""

    def label(self) -> str:
        return f"{LABELS.get(self.kind, self.kind)}: {self.name}"


def library_dir() -> Path:
    return config_dir() / "library"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").casefold()).strip("-")[:60].strip("-")
    if not s:
        raise LibraryError("give it a name with letters or digits")
    if s in RESERVED:
        raise LibraryError(f"{name!r} is a reserved name on Windows — choose another")
    return s


def _kind_dir(kind: str) -> Path:
    if kind not in KINDS:
        raise LibraryError(f"unknown library kind {kind!r}")
    return library_dir() / KINDS[kind]


def _inside(path: Path, kind: str) -> Path:
    """Refuse anything that is not directly inside the kind's folder (belt and braces under the slug rule)."""
    root = _kind_dir(kind).resolve()
    if Path(path).resolve().parent != root:
        raise LibraryError(f"{path} is not in your library")
    return path


def list_items(kind: Optional[str] = None) -> List[Item]:
    out: List[Item] = []
    for k in ([kind] if kind else list(KINDS)):
        d = _kind_dir(k)
        if not d.is_dir():
            continue
        metas = sorted(d.glob("*.meta.json")) if k in FILE_KINDS else sorted(d.glob("*/manifest.json"))
        for m in metas:
            try:
                meta = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = m.name[:-len(".meta.json")] if k in FILE_KINDS else m.parent.name
            path = d / f"{s}.json" if k in FILE_KINDS else m.parent
            if not path.exists():
                continue
            out.append(Item(k, str(meta.get("name") or s), s, path, str(meta.get("description") or ""),
                            str(meta.get("request") or ""), str(meta.get("saved_at") or "")))
    return out


def find(name: str, kind: Optional[str] = None) -> Item:
    """The item called ``name`` (exactly, ignoring case) — never a path built from it."""
    want = (name or "").strip().casefold()
    hits = [i for i in list_items(kind) if i.name.casefold() == want or i.slug == want]
    if not hits:
        raise LibraryError(f"there is no {kind or 'recipe, operation, chain or snippet'} named {name!r} in your library")
    if len(hits) > 1:
        raise LibraryError(f"{name!r} names more than one item ({' and '.join(_a(h.kind) for h in hits)}) — say which "
                           f"(kind)")
    return hits[0]


def _a(kind: str) -> str:
    return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"


def other_kind_holding(kind: str, s: str) -> Optional[Item]:
    """The item of ANOTHER kind whose name is ``s`` (a slug), if any — a name ``replace`` can never take: replacing
    works within one kind, and a recipe, an operation or a chain under one name would make every lookup by name
    ambiguous."""
    return next((i for i in list_items() if i.slug == s and i.kind != kind), None)


def _check_free(kind: str, s: str, replace: bool) -> None:
    other = other_kind_holding(kind, s)
    if other is not None:
        raise LibraryError(f"your library already has {other.label()!r} — {_a(kind)} cannot take that name, not "
                           f"even with replace (it replaces only {_a(kind)} of the same name); choose another name, or "
                           f"delete that one first")
    taken = [i for i in list_items(kind) if i.slug == s]
    if taken and not replace:
        raise LibraryError(f"your library already has {_a(kind)} named {taken[0].name!r} — replace it, or choose "
                           f"another name")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _portable_spec(spec: Dict[str, Any], what: str = "the recipe") -> Tuple[Dict[str, Any], List[str]]:
    """A copy of ``spec`` as the library keeps it: every input-file path absolute (templates, tables, pictures:
    ``editspec.input_slots``), the pin to this batch's structure patterns dropped (with a note) so it can run on other
    projects' files — still planned file by file."""
    from .editspec import input_slots
    spec = json.loads(json.dumps(spec))           # a copy
    notes: List[str] = []
    applies = spec.get("applies_to")
    if isinstance(applies, dict) and applies.get("pattern_ids"):
        rest = {k: v for k, v in applies.items() if k != "pattern_ids"}
        if rest:
            spec["applies_to"] = rest
        else:
            spec.pop("applies_to")
        notes.append(f"the spec was pinned to this batch's structure patterns; the pin was dropped so {what} can "
                     "run on other projects' files (it is still planned file by file)")
    for o in spec.get("ops") or []:
        for d, k in input_slots(o):                # templates, tables, pictures: stored absolute
            d[k] = str(Path(d[k]).resolve())
    return spec, notes


def save_recipe(name: str, spec: Dict[str, Any], description: str = "", replace: bool = False) -> Tuple[Item, List[str]]:
    """Save a (validated) spec as a recipe. Returns the item and any notes (e.g. pattern pins dropped)."""
    from .editspec import validate_spec
    s = slug(name)
    _check_free("recipe", s, replace)
    spec, notes = _portable_spec(spec)
    validate_spec(spec)
    d = _kind_dir("recipe")
    d.mkdir(parents=True, exist_ok=True)
    path = _inside(d / f"{s}.json", "recipe")
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"name": name.strip(), "description": description.strip(), "saved_at": _now(),
            "request": str(spec.get("request") or "")}
    _inside(d / f"{s}.meta.json", "recipe").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return Item("recipe", meta["name"], s, path, meta["description"], meta["request"], meta["saved_at"]), notes


def save_operation(name: str, g: Any, description: str = "", replace: bool = False) -> Item:
    """Save a passing custom transform (codegen.GeneratedOp) as one of your operations."""
    if not getattr(g, "passed", False) or not getattr(g, "source", ""):
        raise LibraryError("only a custom transform that passed verification can be saved")
    s = slug(name)
    _check_free("operation", s, replace)
    d = _kind_dir("operation")
    d.mkdir(parents=True, exist_ok=True)
    folder = _inside(d / s, "operation")
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir()
    (folder / "op.py").write_text(g.source, encoding="utf-8")
    manifest = {"name": name.strip(), "description": description.strip(), "saved_at": _now(),
                "op_name": g.name, "request": g.request, "proposal": g.proposal, "brief": g.brief,
                "select": g.select, "params": g.params, "model": g.model,
                "sha256": hashlib.sha256(g.source.encode("utf-8")).hexdigest(),
                "attempts": [{"attempt": a.get("attempt"), "problems": a.get("problems", [])[:5]} for a in g.attempts]}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return Item("operation", manifest["name"], s, folder, manifest["description"], manifest["request"],
                manifest["saved_at"])


def save_chain(name: str, chain: Dict[str, Any], description: str = "", replace: bool = False) -> Tuple[Item, List[str]]:
    """Save a (validated) chain: ``chains/<slug>.json`` holds its stages with their specs embedded — each made portable
    like a recipe (``_portable_spec``: template paths absolute, pattern pins dropped and noted per stage) — and
    ``<slug>.meta.json`` the name, description, when, and the stages' names as its ``request``. A name a recipe or an
    operation holds is refused even with ``replace`` (``_check_free``)."""
    from .chain import stage_name, validate_chain
    validate_chain(chain)
    s = slug(name)
    _check_free("chain", s, replace)
    stages: List[Dict[str, Any]] = []
    notes: List[str] = []
    for i, st in enumerate(chain["stages"], 1):
        label = stage_name(st, i)
        spec, ns = _portable_spec(st["spec"], f"stage {i} ({label})")
        notes += [f"stage {i} ({label}): {n}" for n in ns]
        new_st = {"name": label, "spec": spec}
        if st.get("recipe"):
            new_st["recipe"] = str(st["recipe"])
        stages.append(new_st)
    saved = {"name": name.strip(), "stages": stages}
    if description.strip() or chain.get("description"):
        saved["description"] = (description or chain.get("description") or "").strip()
    validate_chain(saved)
    d = _kind_dir("chain")
    d.mkdir(parents=True, exist_ok=True)
    path = _inside(d / f"{s}.json", "chain")
    path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"name": name.strip(), "description": saved.get("description", ""), "saved_at": _now(),
            "request": " → ".join(st["name"] for st in stages), "stages": [st["name"] for st in stages]}
    _inside(d / f"{s}.meta.json", "chain").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return Item("chain", meta["name"], s, path, meta["description"], meta["request"], meta["saved_at"]), notes


def chain_file(item: Item) -> Path:
    """A listed chain's file, checked to lie directly inside the library (``_inside``)."""
    return _inside(item.path, "chain")


def load_chain(name: str) -> Dict[str, Any]:
    """The saved chain, validated (``chain.load_chain_file``) — the stages' specs as they were saved."""
    from .chain import load_chain_file
    return load_chain_file(_inside(find(name, "chain").path, "chain"))


def item_hash(item: Item) -> str:
    """A content hash of the item as it is on disk right now — a recipe: its spec file; an operation: ``op.py`` and
    ``manifest.json``. A choice Claude offers in the conversation keeps it, and the user's pick refuses an item whose
    hash differs (``conversation.LibraryChanged``): the item as offered is the item used — the words shown on the
    choice are the words a recipe places, and the operation tried is the one described (CLAUDE.md rules 13 and 17)."""
    if item.kind in FILE_KINDS:
        files = [_inside(item.path, item.kind)]
    else:
        folder = _inside(item.path, "operation")
        files = [folder / "op.py", folder / "manifest.json"]
    h = hashlib.sha256()
    for f in files:
        h.update(f.name.encode("utf-8") + b"\0" + f.read_bytes() + b"\0")
    return h.hexdigest()


def recipe_file(item: Item) -> Path:
    """A listed recipe's spec file, checked to lie directly inside the library (``_inside``) — the one way to read it
    (a symlink that leads outside is refused, as ``load_recipe`` refuses it)."""
    return _inside(item.path, "recipe")


def read_manifest(item: Item) -> Dict[str, Any]:
    """A listed operation's manifest, read from a folder checked to lie directly inside the library (``_inside``)."""
    folder = _inside(item.path, "operation")
    return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))


def load_recipe(name: str) -> Path:
    """The recipe's spec file (load it with ``Session.load_spec`` / ``run-spec``)."""
    return _inside(find(name, "recipe").path, "recipe")


def load_operation(name: str):
    """The saved operation as a codegen.GeneratedOp that has NOT passed yet (``passed`` False): it must be tried on
    the files at hand first. Refused when op.py changed since it was saved or no longer passes the whitelist."""
    from .codegen import GeneratedOp, check_source
    item = find(name, "operation")
    folder = _inside(item.path, "operation")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    source = (folder / "op.py").read_text(encoding="utf-8")
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != manifest.get("sha256"):
        raise LibraryError(f"your operation {item.name!r} changed since it was saved (op.py does not match its "
                           f"manifest) — it will not run; save it again from a fresh custom transform")
    problems = check_source(source)
    if problems:
        raise LibraryError(f"your operation {item.name!r} no longer passes the safety whitelist: {problems[:3]}")
    return GeneratedOp(source=source, name=str(manifest.get("op_name") or item.slug), passed=False,
                       path=folder / "op.py", select=manifest.get("select") or {}, params=manifest.get("params") or {},
                       request=str(manifest.get("request") or ""), proposal=str(manifest.get("proposal") or ""),
                       brief=str(manifest.get("brief") or ""), model=str(manifest.get("model") or ""))


def delete(name: str, kind: Optional[str] = None) -> Item:
    item = find(name, kind)
    if item.kind in FILE_KINDS:
        _inside(item.path, item.kind).unlink()
        meta = _inside(item.path.with_name(f"{item.slug}.meta.json"), item.kind)
        if meta.exists():
            meta.unlink()
    else:
        shutil.rmtree(_inside(item.path, "operation"))
    return item


# ---- snippets (built-in batch 1, item 9) ---------------------------------------------------------------------------

def snippet_text_problem(text: Any) -> Optional[str]:
    """Why ``text`` cannot be a snippet (None when it can): one plain paragraph the user can read, character by
    character — insert_paragraph places it verbatim in one run, so a tab or a line break would not be one."""
    from .conversation import invisible
    if not isinstance(text, str) or not text.strip():
        return "a snippet needs some text"
    if any(c in "\t\n\r\v\f\x00" for c in text):
        return ("it holds a tab, a line break or a field/picture — a snippet is one plain paragraph (number it with the "
                "document's own list numbering, not a typed tab)")
    bad = [c for c in text if invisible(c)]
    if bad:
        return f"it holds characters you cannot see {[hex(ord(c)) for c in bad][:5]}"
    return None


def save_snippet(name: str, text: str, style: Optional[str] = None, formatting: str = "inherit",
                 description: str = "", source: str = "typed", replace: bool = False) -> Item:
    """Save a paragraph as a snippet (its text stripped of surrounding spaces; exactly what will be inserted)."""
    why = snippet_text_problem(text)
    if why:
        raise LibraryError(f"cannot save this snippet: {why}")
    if style is not None and (not isinstance(style, str) or not style.strip()):
        raise LibraryError("the snippet's style must be a style id (or none: the anchor's)")
    if formatting not in FORMATTINGS:
        raise LibraryError(f"the snippet's formatting must be one of {list(FORMATTINGS)}")
    s = slug(name)
    _check_free("snippet", s, replace)
    d = _kind_dir("snippet")
    d.mkdir(parents=True, exist_ok=True)
    body = {"text": text.strip(), "style": style.strip() if style else None, "formatting": formatting}
    path = _inside(d / f"{s}.json", "snippet")
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    meta = {"name": name.strip(), "description": description.strip(), "saved_at": _now(), "source": source,
            "request": ""}
    _inside(d / f"{s}.meta.json", "snippet").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                                         encoding="utf-8")
    return Item("snippet", meta["name"], s, path, meta["description"], "", meta["saved_at"])


def snippet_from_document(path: Any, paragraph_regex: str) -> str:
    """The text of the ONE paragraph of a document's body whose text (as a reader sees it: pending deletions left out)
    matches ``paragraph_regex`` — refused when none or several match, or when it is not one plain paragraph."""
    import re as _re
    from .ooxml import paragraphs
    from .ops.textedit import RunText
    from .package import DocxPackage
    try:
        rx = _re.compile(paragraph_regex)
    except _re.error as e:
        raise LibraryError(f"bad paragraph pattern ({e})")
    try:
        pkg = DocxPackage.from_path(Path(path))
        root = pkg.xml(pkg.main_part())
    except Exception as e:  # noqa: BLE001 — any file that does not open is said as such
        raise LibraryError(f"{path} cannot be read ({type(e).__name__}: {e})")
    hits = [t for t in (RunText(p).text for p in paragraphs(root)) if rx.search(t)]
    if not hits:
        raise LibraryError(f"no paragraph of {Path(path).name} matches {paragraph_regex!r}")
    if len(hits) > 1:
        raise LibraryError(f"{len(hits)} paragraphs of {Path(path).name} match {paragraph_regex!r} — make the pattern "
                           f"narrower: {[h[:60] for h in hits[:3]]}")
    why = snippet_text_problem(hits[0])
    if why:
        raise LibraryError(f"that paragraph cannot be a snippet: {why}")
    return hits[0].strip()


def snippet_file(item: Item) -> Path:
    """A listed snippet's file, checked to lie directly inside the library (``_inside``)."""
    return _inside(item.path, "snippet")


def read_snippet(item: Item) -> Dict[str, Any]:
    """A listed snippet: {text, style, formatting, source} — read through the containment check, and refused when the
    file no longer holds a usable snippet (edited by hand, say)."""
    body = json.loads(snippet_file(item).read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise LibraryError(f"your snippet {item.name!r} is not a snippet file any more")
    why = snippet_text_problem(body.get("text"))
    if why:
        raise LibraryError(f"your snippet {item.name!r} cannot be used: {why}")
    if body.get("formatting", "inherit") not in FORMATTINGS:
        raise LibraryError(f"your snippet {item.name!r} has an unknown formatting {body.get('formatting')!r}")
    meta = {}
    try:
        meta = json.loads(_inside(item.path.with_name(f"{item.slug}.meta.json"), "snippet").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"text": body["text"], "style": body.get("style") or None, "formatting": body.get("formatting", "inherit"),
            "source": str(meta.get("source") or "")}


def load_snippet(name: str) -> Tuple[Item, Dict[str, Any]]:
    item = find(name, "snippet")
    return item, read_snippet(item)


def snippet_spec(name: str, snippet: Dict[str, Any], anchor: str, position: str = "after",
                 tracked: bool = True) -> Dict[str, Any]:
    """The EditSpec that inserts a snippet: ``insert_paragraph`` with its text verbatim, ``position`` every body
    paragraph whose text matches ``anchor`` (a regex), in every file that has one (``allow_missing``) — and at most one
    per file (``expect``: a file where the anchor matches twice is set aside, never given two copies)."""
    import re as _re
    from .editspec import validate_spec
    if position not in ("after", "before"):
        raise LibraryError("a snippet goes 'after' or 'before' its anchor")
    if not isinstance(anchor, str) or not anchor.strip():
        raise LibraryError("say where the snippet goes: the text (a regular expression) of the paragraph it follows")
    try:
        _re.compile(anchor)
    except _re.error as e:
        raise LibraryError(f"bad anchor pattern ({e})")
    text = snippet["text"]
    spec = {
        "request": f"Insert my snippet «{name}» {position} the paragraph matching {anchor!r} in every file that has it",
        "summary": f"insert_paragraph: the snippet «{name}» ({len(text)} characters, exactly as saved) {position} the "
                   f"body paragraph whose text matches {anchor!r}; files without it pass unchanged",
        "mode": "content" if tracked else "untracked",
        "assumptions": [
            "the snippet's text is inserted exactly as saved",
            "a file without a matching paragraph passes unchanged; a file where it matches more than once is set "
            "aside (never given two copies)",
            "the new paragraph copies the anchor's formatting" if snippet.get("formatting", "inherit") == "inherit"
            else "the new paragraph takes only the paragraph style",
        ],
        "ops": [{"op": "insert_paragraph", "select": {"scope": ["document"], "paragraphs": {"text_regex": anchor}},
                 "params": {"text": text, "position": position, "style": snippet.get("style") or "inherit",
                            "formatting": snippet.get("formatting", "inherit"), "tracked": tracked,
                            "allow_missing": True},
                 "expect": {"text_diffs": {"max": 1}}}],
    }
    validate_spec(spec)
    return spec
