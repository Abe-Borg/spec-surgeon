"""Your library — named, reusable solutions, kept on this computer (decided 2026-10-15; sharing comes later).

* A **recipe** is a saved EditSpec (built from the built-in operations): ``recipes/<slug>.json`` — an ordinary spec file,
  so it loads, previews and runs exactly like any spec (``Session.load_spec``, ``run-spec``) — plus
  ``<slug>.meta.json`` (name, description, when, the request). Template paths are stored absolute; structure-pattern
  pins (``applies_to``) are dropped (they would not match another project's files).
* An **operation** is a saved one-off custom transform (AI-written, passed verification when it was made):
  ``operations/<slug>/op.py`` + ``manifest.json`` (name, description, the request and conversation it came from, its
  select/params, the sha256 of op.py, the model, its attempts). It is never trusted more than the day it was made:
  using it re-runs it on the current batch's sample files (sandbox + the full suite) before its preview, and Run
  verifies every file again — edited copies only, never over your files. A manifest whose op.py changed is refused.

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

KINDS = {"recipe": "recipes", "operation": "operations"}
RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


class LibraryError(ValueError):
    pass


@dataclass
class Item:
    kind: str                                     # recipe | operation
    name: str
    slug: str
    path: Path                                    # the spec file / the operation's folder
    description: str = ""
    request: str = ""
    saved_at: str = ""

    def label(self) -> str:
        return f"{'Recipe' if self.kind == 'recipe' else 'My operation'}: {self.name}"


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
        metas = sorted(d.glob("*.meta.json")) if k == "recipe" else sorted(d.glob("*/manifest.json"))
        for m in metas:
            try:
                meta = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            s = m.name[:-len(".meta.json")] if k == "recipe" else m.parent.name
            path = d / f"{s}.json" if k == "recipe" else m.parent
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
        raise LibraryError(f"there is no {kind or 'recipe or operation'} named {name!r} in your library")
    if len(hits) > 1:
        raise LibraryError(f"{name!r} names both a recipe and an operation — say which (kind)")
    return hits[0]


def _a(kind: str) -> str:
    return f"{'an' if kind[:1] in 'aeiou' else 'a'} {kind}"


def other_kind_holding(kind: str, s: str) -> Optional[Item]:
    """The item of the OTHER kind whose name is ``s`` (a slug), if any — a name ``replace`` can never take: replacing
    works within one kind, and a recipe and an operation under one name would make every lookup by name ambiguous."""
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


def save_recipe(name: str, spec: Dict[str, Any], description: str = "", replace: bool = False) -> Tuple[Item, List[str]]:
    """Save a (validated) spec as a recipe. Returns the item and any notes (e.g. pattern pins dropped)."""
    from .editspec import validate_spec
    s = slug(name)
    _check_free("recipe", s, replace)
    spec = json.loads(json.dumps(spec))           # a copy
    notes: List[str] = []
    applies = spec.get("applies_to")
    if isinstance(applies, dict) and applies.get("pattern_ids"):
        rest = {k: v for k, v in applies.items() if k != "pattern_ids"}
        if rest:
            spec["applies_to"] = rest
        else:
            spec.pop("applies_to")
        notes.append("the spec was pinned to this batch's structure patterns; the pin was dropped so the recipe can "
                     "run on other projects' files (it is still planned file by file)")
    for o in spec.get("ops") or []:
        t = (o.get("params") or {}).get("template")
        if isinstance(t, str) and t:
            o["params"]["template"] = str(Path(t).resolve())
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
    if item.kind == "recipe":
        _inside(item.path, "recipe").unlink()
        meta = _inside(item.path.with_name(f"{item.slug}.meta.json"), "recipe")
        if meta.exists():
            meta.unlink()
    else:
        shutil.rmtree(_inside(item.path, "operation"))
    return item
