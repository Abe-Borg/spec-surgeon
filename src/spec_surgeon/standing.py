"""Where each document stands: the record of every operation the user asked for and what became of each document
under it (the owner's decision of 2026-11-11; docs/CONVERSATIONAL_DESKTOP.md, "Where each document stands").

An **operation** is one thing the user asked for: a Safe-mode button, a request's plan, a saved recipe's run, one of
Obliterate's own actions. A **cell** is one document under one operation, and it keeps every **attempt** made on it,
oldest first: done (in which mode, and with which marks), failed (why, and the reason in the window's own words) or
not run (left out, waiting on the to-do list). A cell's state is its last attempt's. A document with no attempt under
an operation was attached later or was not one of the documents it ran on: the view says which.

The record describes and never decides. Nothing here plans, orders, chooses or changes anything, and no model reads
it; ``DocumentTask`` writes to it wherever a document's outcome is decided and the window shows it as a grid. It is
plain data so the journal keeps it as it is, and every operation and attempt carries the number it was recorded at
(``seq``, never reused): Undo takes back exactly what was recorded since a run's approval (``rewind``) without the
journal keeping a copy of the record per undo point.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple

DONE, FAILED, NOT_RUN = "done", "failed", "not_run"
STATES = (DONE, FAILED, NOT_RUN)
MODES = ("safe", "default", "obliterate")

# why a document holds no attempt under an operation, and the sentence the window shows for it
ATTACHED_LATER, NOT_PART = "attached later", "not part of it"
WHY_NOT = {ATTACHED_LATER: "It was attached after this was done.", NOT_PART: "This was not run on it."}

REQUEST_CHARS = 4000        # characters of a request an operation keeps
REASON_CHARS = 2000         # characters of a reason an attempt keeps


class Standing:
    def __init__(self) -> None:
        self.operations: List[Dict[str, Any]] = []      # oldest first
        self.seq = 0                                    # the last number given to an operation or an attempt

    def _tick(self) -> int:
        self.seq += 1
        return self.seq

    # ---- writing ----
    def open(self, request: str, *, mode: str, recipe: str = "", name: str = "",
             documents: Sequence[str] = ()) -> str:
        """A new operation, newest last: what was asked (a button's sentence, or the user's words), the mode it was
        asked in, the Safe-mode button pressed (``recipe``, '' for none), the saved recipe run (``name``) and the
        documents attached then (one attached afterwards reads as attached later). Returns its id."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        op = {"id": uuid.uuid4().hex[:12], "seq": self._tick(), "request": str(request or "")[:REQUEST_CHARS],
              "mode": mode, "recipe": str(recipe or ""), "name": str(name or ""),
              "documents": list(dict.fromkeys(str(d) for d in documents)), "cells": {}}
        self.operations.append(op)
        return op["id"]

    def find(self, op_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((op for op in self.operations if op["id"] == op_id), None) if op_id else None

    def has(self, op_id: Optional[str]) -> bool:
        return self.find(op_id) is not None

    def record(self, op_id: str, document: str, state: str, *, mode: str, why: str = "", reason: str = "",
               marks: Sequence[str] = (), note: str = "", since: int = 0) -> None:
        """One document's outcome under one operation: ``state`` in the mode it happened in, ``why`` (the kind of
        reason: refused, failed, held, asked, left out, set aside), ``reason`` (the sentence), the ``marks`` its copy
        was delivered with (UNVERIFIED's failed checks, UNPROVEN) and a ``note`` on how it was done.

        ``since``: the record's number when the approval this outcome belongs to was given. An outcome recorded
        after it stands for that approval's whole work on the document, so a later word on the same document
        replaces it (a failed copy the second check then fixed is one attempt, done, that remembers the reason it
        failed first); an attempt from before it stays, and this one is added after it."""
        op = self.find(op_id)
        if op is None:
            raise KeyError(op_id)
        if state not in STATES:
            raise ValueError(f"state must be one of {STATES}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        document = str(document)
        attempt: Dict[str, Any] = {"seq": self._tick(), "state": state, "mode": mode, "why": str(why or ""),
                                   "reason": str(reason or "")[:REASON_CHARS],
                                   "marks": [str(m) for m in marks or []], "note": str(note or "")}
        cell = op["cells"].setdefault(document, [])
        if document not in op["documents"]:
            op["documents"].append(document)
        if cell and cell[-1]["seq"] > since:
            before = cell[-1]
            first = before.get("first") or (before["reason"] if before["state"] == FAILED else "")
            if state == DONE and first:
                attempt["first"] = first
            cell[-1] = attempt
        else:
            cell.append(attempt)

    def rewind(self, seq: int) -> None:
        """The record as it was when ``seq`` was the last number given: every attempt recorded since is taken back,
        and an operation left with none is gone. Numbers are never given again."""
        for op in self.operations:
            op["cells"] = {doc: kept for doc, cell in op["cells"].items()
                           if (kept := [a for a in cell if a["seq"] <= seq])}
        self.operations = [op for op in self.operations if op["cells"]]

    def forget(self, document: str) -> None:
        """A document removed from the conversation: its cells go with it (attached again it is an original, which
        nothing was done to), and an operation left with no document is gone."""
        document = str(document)
        for op in self.operations:
            op["cells"].pop(document, None)
            op["documents"] = [d for d in op["documents"] if d != document]
        self.operations = [op for op in self.operations if op["cells"]]

    def clear(self) -> None:
        self.operations = []

    # ---- reading ----
    def state(self, op_id: str, document: str) -> str:
        """The state of one cell (its last attempt's); '' when the document holds no attempt under the operation."""
        op = self.find(op_id)
        cell = (op or {}).get("cells", {}).get(str(document))
        return cell[-1]["state"] if cell else ""

    def owed(self, documents: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
        """Every cell whose last attempt failed, oldest operation first: ``(operation id, document)``. ``documents``:
        only those documents (the ones attached now)."""
        wanted = None if documents is None else {str(d) for d in documents}
        return [(op["id"], doc) for op in self.operations for doc, cell in op["cells"].items()
                if cell[-1]["state"] == FAILED and (wanted is None or doc in wanted)]

    def view(self, documents: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
        """The record as the window shows it, over the documents attached now (``(key, name)``, in the window's
        order): ``operations`` (oldest first, numbered from 1, each with how many of these documents it is done on
        and failed on), ``documents`` (one row each: a cell per operation, in the same order, and how many it still
        owes) and ``owed`` (the cells that failed and the documents that hold one). An operation none of these
        documents holds an attempt under is not listed. A cell is its last attempt (``state``, ``mode``, ``why``,
        ``reason``, ``marks``, ``note`` and ``first``, the reason it failed before a second check fixed it) with
        every attempt under ``attempts``; a document with none reads as not run, with why (``WHY_NOT``)."""
        keys = [str(k) for k, _name in documents]
        shown = [op for op in self.operations if any(k in op["cells"] for k in keys)]

        def attempt_view(a: Dict[str, Any]) -> Dict[str, Any]:
            return {"state": a["state"], "mode": a["mode"], "why": a["why"], "reason": a["reason"],
                    "marks": list(a["marks"]), "note": a["note"], "first": a.get("first", "")}

        def cell_view(op: Dict[str, Any], key: str) -> Dict[str, Any]:
            cell = op["cells"].get(key)
            if not cell:
                why = NOT_PART if key in op["documents"] else ATTACHED_LATER
                return {"state": NOT_RUN, "mode": "", "why": why, "reason": WHY_NOT[why], "marks": [], "note": "",
                        "first": "", "attempts": []}
            return {**attempt_view(cell[-1]), "attempts": [attempt_view(a) for a in cell]}

        rows = []
        for key, name in documents:
            cells = [cell_view(op, str(key)) for op in shown]
            rows.append({"path": str(key), "name": str(name), "cells": cells,
                         "owed": sum(1 for c in cells if c["state"] == FAILED)})
        operations = [{"id": op["id"], "number": number, "request": op["request"], "mode": op["mode"],
                       "recipe": op["recipe"], "name": op["name"],
                       "done": sum(1 for row in rows if row["cells"][number - 1]["state"] == DONE),
                       "failed": sum(1 for row in rows if row["cells"][number - 1]["state"] == FAILED)}
                      for number, op in enumerate(shown, 1)]
        return {"operations": operations, "documents": rows,
                "owed": {"cells": sum(row["owed"] for row in rows),
                         "documents": sum(1 for row in rows if row["owed"])}}

    # ---- the journal ----
    def to_json(self) -> Dict[str, Any]:
        return {"seq": self.seq,
                "operations": [{**{k: op[k] for k in ("id", "seq", "request", "mode", "recipe", "name")},
                                "documents": list(op["documents"]),
                                "cells": {doc: [dict(a, marks=list(a["marks"])) for a in cell]
                                          for doc, cell in op["cells"].items()}}
                               for op in self.operations]}

    @classmethod
    def from_json(cls, data: Any) -> "Standing":
        """The record a journal kept, in the shape ``to_json`` wrote it. Anything else gives an empty record: it is
        an account of what was done, not what the next request builds on, so a journal whose record does not read
        (or that is from before the record) restores the conversation without one."""
        out = cls()
        text = lambda value: isinstance(value, str)  # noqa: E731
        whole = lambda value: type(value) is int and value >= 0  # noqa: E731

        def attempt_ok(a: Any) -> bool:
            return (isinstance(a, dict) and whole(a.get("seq")) and a.get("state") in STATES
                    and a.get("mode") in MODES and all(text(a.get(k, "")) for k in ("why", "reason", "note", "first"))
                    and isinstance(a.get("marks", []), list) and all(map(text, a.get("marks", []))))

        def op_ok(op: Any) -> bool:
            return (isinstance(op, dict) and text(op.get("id")) and bool(op["id"]) and whole(op.get("seq"))
                    and text(op.get("request")) and op.get("mode") in MODES
                    and text(op.get("recipe", "")) and text(op.get("name", ""))
                    and isinstance(op.get("documents"), list) and all(map(text, op["documents"]))
                    and isinstance(op.get("cells"), dict)
                    and all(text(doc) and isinstance(cell, list) and cell and all(map(attempt_ok, cell))
                            for doc, cell in op["cells"].items()))

        if not (isinstance(data, dict) and whole(data.get("seq")) and isinstance(data.get("operations"), list)
                and all(map(op_ok, data["operations"]))):
            return out
        numbers = [n for op in data["operations"] for n in (op["seq"], *(a["seq"] for c in op["cells"].values()
                                                                         for a in c))]
        if len(set(numbers)) != len(numbers) or len({op["id"] for op in data["operations"]}) != len(data["operations"]):
            return out
        for op in sorted(data["operations"], key=lambda op: op["seq"]):
            cells = {doc: [{"seq": a["seq"], "state": a["state"], "mode": a["mode"], "why": a.get("why", ""),
                            "reason": a.get("reason", "")[:REASON_CHARS], "marks": list(a.get("marks", [])),
                            "note": a.get("note", ""), **({"first": a["first"]} if a.get("first") else {})}
                           for a in sorted(cell, key=lambda a: a["seq"])]
                     for doc, cell in op["cells"].items()}
            out.operations.append({"id": op["id"], "seq": op["seq"], "request": op["request"][:REQUEST_CHARS],
                                   "mode": op["mode"], "recipe": op.get("recipe", ""), "name": op.get("name", ""),
                                   "documents": list(dict.fromkeys([*op["documents"], *cells])), "cells": cells})
        out.seq = max([data["seq"], *numbers])
        return out
