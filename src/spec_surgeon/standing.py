"""Where each document stands: the record of every operation the user asked for and what became of each document
under it (the owner's decision of 2026-11-11; docs/CONVERSATIONAL_DESKTOP.md, "Where each document stands").

An **operation** is one thing the user asked for: a Safe-mode button, a request's plan, a saved recipe's run, one of
Obliterate's own actions. A **cell** is one document under one operation, and it keeps every **attempt** made on it,
oldest first: done (in which mode, and with which marks), failed (why, and the reason in the window's own words),
not run (left out, waiting out of the batch) or waived (the user said it is not needed on this one). A cell's state
is its last attempt's. A document with no attempt under an operation was attached later or was not one of the
documents it ran on: the view says which.

**The failed cells are the to-do list** (``owed``). A failed attempt keeps what working on it again needs — the
request it failed (``decisions``), the plan the user saw (``seen``) and, where the engine stopped on it, its case file
(``case``) — so a pass over the to-do list is built from the record alone.

The record itself decides nothing: nothing here plans, orders or changes a document. ``DocumentTask`` writes to it
wherever a document's outcome is decided, reads the failed cells for the to-do list and the pass over it, and the
window shows it as a grid. It is plain data so the journal keeps it as it is, and every operation and attempt carries
the number it was recorded at (``seq``, never reused): Undo takes back exactly what was recorded since a run's
approval (``rewind``) without the journal keeping a copy of the record per undo point.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

DONE, FAILED, NOT_RUN, WAIVED = "done", "failed", "not_run", "waived"
STATES = (DONE, FAILED, NOT_RUN, WAIVED)
WAIVED_REASON = "You said it is not needed on this one."
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
    def open(self, request: str, *, mode: str, recipe: str = "", name: str = "", inputs: str = "",
             documents: Sequence[str] = ()) -> str:
        """A new operation, newest last: what was asked (a button's sentence, or the user's words), the mode it was
        asked in, the Safe-mode button pressed (``recipe``, '' for none) with its form as one line of JSON
        (``inputs``: the same button with the same form is the same thing asked again, ``same_button``), the saved
        recipe run (``name``) and the documents attached then (one attached afterwards reads as attached later).
        Returns its id."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        op = {"id": uuid.uuid4().hex[:12], "seq": self._tick(), "request": str(request or "")[:REQUEST_CHARS],
              "mode": mode, "recipe": str(recipe or ""), "name": str(name or ""), "inputs": str(inputs or ""),
              "documents": list(dict.fromkeys(str(d) for d in documents)), "cells": {}}
        self.operations.append(op)
        return op["id"]

    def find(self, op_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return next((op for op in self.operations if op["id"] == op_id), None) if op_id else None

    def has(self, op_id: Optional[str]) -> bool:
        return self.find(op_id) is not None

    def record(self, op_id: str, document: str, state: str, *, mode: str, why: str = "", reason: str = "",
               marks: Sequence[str] = (), note: str = "", since: int = 0,
               decisions: Optional[Sequence[str]] = None, seen: Optional[Sequence[str]] = None,
               case: Optional[Dict[str, Any]] = None) -> None:
        """One document's outcome under one operation: ``state`` in the mode it happened in, ``why`` (the kind of
        reason: refused, failed, held, asked, left out, set aside), ``reason`` (the sentence), the ``marks`` its copy
        was delivered with (UNVERIFIED's failed checks, UNPROVEN) and a ``note`` on how it was done. A failed attempt
        also keeps what a pass over the to-do list needs to work on it again: the request it failed
        (``decisions``), the plan the user saw (``seen``) and the engine's case file (``case``).

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
        if state == FAILED:
            if decisions is not None:
                attempt["decisions"] = [str(d) for d in decisions]
            if seen is not None:
                attempt["seen"] = [str(s) for s in seen]
            if case:
                attempt["case"] = case
        cell = op["cells"].setdefault(document, [])
        if document not in op["documents"]:
            op["documents"].append(document)
        if cell and cell[-1]["seq"] > since:
            before = cell[-1]
            first = before.get("first") or (before["reason"] if before["state"] == FAILED else "")
            if state == DONE and first:
                attempt["first"] = first
            if state == FAILED:
                # the same approval's later word on a failure keeps what the earlier one knew and it does not
                for key in ("decisions", "seen", "case"):
                    if key not in attempt and key in before:
                        attempt[key] = before[key]
            cell[-1] = attempt
        else:
            cell.append(attempt)

    def waive(self, op_id: str, document: str, *, mode: str) -> bool:
        """Not needed on this one: a failed cell is waived at the user's word (its failed attempt stays under it).
        False when the cell is not a failed one."""
        cell = (self.find(op_id) or {}).get("cells", {}).get(str(document))
        if not cell or cell[-1]["state"] != FAILED:
            return False
        cell.append({"seq": self._tick(), "state": WAIVED, "mode": mode, "why": WAIVED, "reason": WAIVED_REASON,
                     "marks": [], "note": ""})
        return True

    def unwaive(self, op_id: str, document: str) -> bool:
        """A waived cell back on the to-do list: the waiver is taken off, and the failure under it stands again.
        False when the cell is not a waived one."""
        cell = (self.find(op_id) or {}).get("cells", {}).get(str(document))
        if not cell or cell[-1]["state"] != WAIVED:
            return False
        cell.pop()
        return True

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

    def last(self, op_id: Optional[str], document: str) -> Optional[Dict[str, Any]]:
        """The last attempt of one cell; None when the document holds no attempt under the operation."""
        cell = (self.find(op_id) or {}).get("cells", {}).get(str(document))
        return cell[-1] if cell else None

    def owed(self, documents: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
        """The to-do list: every cell whose last attempt failed, oldest operation first, ``(operation id,
        document)``, the documents in each operation in the order given. ``documents``: only those documents (the
        ones attached now)."""
        order = None if documents is None else [str(d) for d in documents]
        out: List[Tuple[str, str]] = []
        for op in self.operations:
            docs = [d for d in (op["cells"] if order is None else order) if d in op["cells"]]
            out.extend((op["id"], doc) for doc in docs if op["cells"][doc][-1]["state"] == FAILED)
        return out

    def same_button(self, op_id: str) -> List[str]:
        """The earlier operations that are the same Safe-mode button with the same form as ``op_id``: the same thing
        asked again (never two operations that are merely worded alike)."""
        op = self.find(op_id)
        if op is None or not op["recipe"]:
            return []
        return [o["id"] for o in self.operations
                if o["seq"] < op["seq"] and o["recipe"] == op["recipe"] and o["inputs"] == op["inputs"]]

    def view(self, documents: Sequence[Tuple[str, str]]) -> Dict[str, Any]:
        """The record as the window shows it, over the documents attached now (``(key, name)``, in the window's
        order): ``operations`` (oldest first, numbered from 1, each with how many of these documents it is done on
        and failed on), ``documents`` (one row each: a cell per operation, in the same order, and how many it still
        owes) and ``owed`` (the cells that failed and the documents that hold one). An operation none of these
        documents holds an attempt under is not listed. A cell is its last attempt (``state``, ``mode``, ``why``,
        ``reason``, ``marks``, ``note`` and ``first``, the reason it failed before a second check fixed it) with
        every attempt under ``attempts``; a document with none reads as not run, with why (``WHY_NOT``). What a
        failed attempt keeps for the pass (its request, the plan seen, its case file) is not part of the view."""
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
                "operations": [{**{k: op[k] for k in ("id", "seq", "request", "mode", "recipe", "name", "inputs")},
                                "documents": list(op["documents"]),
                                "cells": {doc: [dict(a, marks=list(a["marks"])) for a in cell]
                                          for doc, cell in op["cells"].items()}}
                               for op in self.operations]}

    @classmethod
    def from_json(cls, data: Any, case: Optional[Callable[[Any], Optional[Dict[str, Any]]]] = None) -> "Standing":
        """The record a journal kept, in the shape ``to_json`` wrote it. Anything else gives an empty record: a
        journal whose record does not read (or that is from before the record) restores the conversation without
        one. ``case``: reads a failed attempt's case file as the journal kept it, or gives None (the attempt then
        stands without one); without it no case file is kept."""
        out = cls()
        text = lambda value: isinstance(value, str)  # noqa: E731
        texts = lambda value: isinstance(value, list) and all(map(text, value))  # noqa: E731
        whole = lambda value: type(value) is int and value >= 0  # noqa: E731

        def attempt_ok(a: Any) -> bool:
            return (isinstance(a, dict) and whole(a.get("seq")) and a.get("state") in STATES
                    and a.get("mode") in MODES and all(text(a.get(k, "")) for k in ("why", "reason", "note", "first"))
                    and texts(a.get("marks", [])) and texts(a.get("decisions", [])) and texts(a.get("seen", [])))

        def op_ok(op: Any) -> bool:
            return (isinstance(op, dict) and text(op.get("id")) and bool(op["id"]) and whole(op.get("seq"))
                    and text(op.get("request")) and op.get("mode") in MODES
                    and text(op.get("recipe", "")) and text(op.get("name", "")) and text(op.get("inputs", ""))
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
            def kept(a: Dict[str, Any]) -> Dict[str, Any]:
                extra: Dict[str, Any] = {}
                if a["state"] == FAILED:
                    if "decisions" in a:
                        extra["decisions"] = list(a["decisions"])
                    if "seen" in a:
                        extra["seen"] = list(a["seen"])
                    read = case(a.get("case")) if case is not None and a.get("case") else None
                    if read:
                        extra["case"] = read
                return {"seq": a["seq"], "state": a["state"], "mode": a["mode"], "why": a.get("why", ""),
                        "reason": a.get("reason", "")[:REASON_CHARS], "marks": list(a.get("marks", [])),
                        "note": a.get("note", ""), **({"first": a["first"]} if a.get("first") else {}), **extra}

            cells = {doc: [kept(a) for a in sorted(cell, key=lambda a: a["seq"])]
                     for doc, cell in op["cells"].items()}
            out.operations.append({"id": op["id"], "seq": op["seq"], "request": op["request"][:REQUEST_CHARS],
                                   "mode": op["mode"], "recipe": op.get("recipe", ""), "name": op.get("name", ""),
                                   "inputs": op.get("inputs", ""),
                                   "documents": list(dict.fromkeys([*op["documents"], *cells])), "cells": cells})
        out.seq = max([data["seq"], *numbers])
        return out
