"""Reading the record of where each document stands (`DocumentTask.standing`, the view's `standing`) in tests: the
grid's cells, the to-do list (its failed cells), and what the newest operation could not do."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def grid(task) -> Dict[str, Any]:
    return task.view()["standing"]


def row(task, path) -> Dict[str, Any]:
    return next(r for r in grid(task)["documents"] if r["path"] == str(path))


def states(task, path) -> List[str]:
    return [cell["state"] for cell in row(task, path)["cells"]]


def owed(task) -> List[Dict[str, str]]:
    """The to-do list as the window shows it: every failed cell, oldest operation first — the document, the request
    it still owes, why (`refused`, `failed`, `held`, `asked`) and the reason."""
    view = grid(task)
    return [{"path": r["path"], "name": r["name"], "request": op["request"], "why": r["cells"][i]["why"],
             "reason": r["cells"][i]["reason"], "operation": op["id"]}
            for i, op in enumerate(view["operations"]) for r in view["documents"]
            if r["cells"][i]["state"] == "failed"]


def untouched(task) -> List[Dict[str, str]]:
    """What the newest operation could not do: its failed cells, each with the document and the reason (what Safe
    mode's Left untouched card listed, before the grid kept every one)."""
    view = grid(task)
    if not view["operations"]:
        return []
    newest = view["operations"][-1]["id"]
    return [{"path": e["path"], "name": e["name"], "reason": e["reason"]} for e in owed(task)
            if e["operation"] == newest]


def owing(task) -> List[str]:
    """The names of the documents with something on the to-do list, in the window's order."""
    return [r["name"] for r in grid(task)["documents"] if r["owed"]]


def waiting(task) -> List[str]:
    """The names of the documents the user set aside: out of the batch until worked on or put back."""
    return [Path(k).name for k in task.todo]


def owes(task, path, operation=None) -> Dict[str, Any]:
    """The failed attempt the record keeps for a document's oldest owed cell (or its cell under ``operation``), with
    what a pass works from: the request it failed (``decisions``), the plan seen (``seen``) and the engine's
    ``case`` file."""
    key = str(path)
    op = operation or next(o for o, doc in task.standing.owed() if doc == key)
    return task.standing.last(op, key)


def case_of(task, path, operation=None) -> Dict[str, Any]:
    """The case file of a document's failed cell: what Obliterate's hand-off reads."""
    return owes(task, path, operation)["case"]


def failed_cell(task, path) -> Dict[str, Any]:
    """The document's first failed cell as the window shows it (with Obliterate's hand-off fields when offered)."""
    return next(cell for cell in row(task, path)["cells"] if cell["state"] == "failed")


def owe(task, path, request, *, why="refused", reason="", seen=(), case=None, mode="default") -> str:
    """A failed cell put on the record by hand — a test's shortcut for a request that could not be done on the
    document, with what a pass works from. Returns the operation's id."""
    op = task.standing.open(request, mode=mode, documents=task._listed())
    task.standing.record(op, str(path), "failed", mode=mode, why=why, reason=reason,
                         decisions=[request] if request else [], seen=list(seen), case=case)
    return op
