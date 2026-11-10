"""Obliterate's named Word actions (the owner's decision of 2026-11-10; docs/BACKLOG.md O5).

A fixed menu. Each action is code the app ships (``render/word_actions.py``: Word's own commands, called in the
private Word instance the renderer uses), run on a copy. The user picks actions in the window, or Claude names them
in a plan (the operation ``word_action``), and they run in the order given, in one opening of the copy, saved once.
No script is composed anywhere: a case the menu cannot express is first a new menu item.

Word writes the whole file again when it saves, so nothing is predicted — the second exception to invariant 1, with
an Obliterate custom method (CLAUDE.md, Modes). The path is that method's: every document's copy is made before
approval (``trial``), read by the verifier's own report (``verify.run_difference(rewritten=True)``: the words added
and removed, what else differs by content, and the checks of the package), shown, and — once approved — delivered as
those bytes, Word not run again (``codegen._kept_trial``). No such copy is called verified: each is UNPROVEN, and
UNVERIFIED when a check of its file failed.

Word opens a private copy of the document's bytes, never the user's file or the working copy itself; macros are
disabled, links are not updated at opening, and the copy is saved under a new name. What Word says it did (a count
per action) is Word's account, kept apart from the verifier's reading and labelled as Word's wherever it is shown.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diagnostics as diag
from . import guard, stopping

TIMEOUT_S = 300.0                # one document's actions, from Word starting to its copy saved (the trust copy quotes it)
OP = "word_action"               # the operation a plan names an action by (``ops/word_action.py``)


@dataclass(frozen=True)
class Action:
    id: str
    label: str                   # the menu's words
    does: str                    # exactly what it does: the menu's help and the plan's sentence
    one: str                     # Word's own account of what it did, for a count of one …
    many: str                    # … and of several ("{n}" is the count)
    offered: bool = True         # on the menu (``convert`` is the worker's alone until a file can be rescued at attach)


MENU: Tuple[Action, ...] = (
    Action("repair", "Open and repair, then save",
           "Word opens the copy with its Open and Repair command and saves it again.",
           "opened with Open and Repair and saved again", "opened with Open and Repair and saved again"),
    Action("remove_protection", "Remove protection",
           "Word turns off the copy's editing restrictions, its read-only recommendation and its Marked as Final "
           "state. A restriction set with a password is not removed: Word needs the password.",
           "{n} protection setting turned off", "{n} protection settings turned off"),
    Action("accept_changes", "Accept all tracked changes",
           "Word accepts every tracked change in the copy.",
           "{n} tracked change accepted", "{n} tracked changes accepted"),
    Action("reject_changes", "Reject all tracked changes",
           "Word rejects every tracked change in the copy.",
           "{n} tracked change rejected", "{n} tracked changes rejected"),
    Action("update_fields", "Update fields",
           "Word updates every field in the copy: tables of contents, cross-references, page numbers, dates. A field "
           "that reads another file or asks a question is left as it is.",
           "{n} field updated", "{n} fields updated"),
    Action("unlink_fields", "Unlink fields",
           "Word replaces every field in the body, the notes and the body's text boxes with the text it shows. "
           "Fields in headers and footers stay live: their page numbers and running titles.",
           "{n} field replaced with its text", "{n} fields replaced with their text"),
    Action("numbers_to_text", "Turn automatic numbers into typed text",
           "Word turns the copy's automatic list numbers into typed text.",
           "{n} numbered paragraph given a typed number", "{n} numbered paragraphs given typed numbers"),
    Action("upgrade_compatibility", "Leave compatibility mode",
           "Word converts the copy to its current file format (its Convert command), leaving compatibility mode.",
           "converted out of compatibility mode", "converted out of compatibility mode"),
    Action("textboxes_to_text", "Turn text boxes into plain text",
           "Word puts the text of each text box into the document as ordinary paragraphs, where the box is anchored, "
           "and deletes the box: in the body and in headers and footers.",
           "{n} text box turned into plain text", "{n} text boxes turned into plain text"),
    Action("delete_shapes", "Delete drawn shapes",
           "Word deletes every drawn shape, in the body and in headers and footers: text boxes with their text, "
           "lines, AutoShapes, WordArt, SmartArt, groups, canvases and watermarks. Pictures, charts and embedded "
           "objects stay.",
           "{n} drawn shape deleted", "{n} drawn shapes deleted"),
    Action("convert", "Save as a Word document (.docx)",
           "Word opens the file and saves it as a Word document (.docx).",
           "saved as a Word document", "saved as a Word document", offered=False),
)
ACTIONS: Dict[str, Action] = {a.id: a for a in MENU}
OFFERED: Tuple[str, ...] = tuple(a.id for a in MENU if a.offered)
ALWAYS_CHANGES = ("repair", "convert")       # the saving again is the change, whatever Word counts
NO_WORD = ("Microsoft Word is not installed on this computer (or the app cannot reach it), and the Word actions are "
           "Word's own commands: they run nowhere else.")
NOTHING_TO_DO = "Word found nothing to do in it"
UNPROVEN_MARK = "UNPROVEN (Word made it: what it changed was shown to you, not proven)"
PASSWORD_HINT = (" A restriction set with a password is not Word's to remove without it; in Obliterate mode a raw XML "
                 "edit can take the restriction itself out of the settings part.")


def check(actions: Any, *, offered_only: bool = True) -> List[str]:
    """``actions`` as a clean list of ids, or ``ValueError`` in plain words: at least one, each on the menu, none
    twice, Open and Repair first when it is there (it is how the copy is opened), and never both accepting and
    rejecting the tracked changes."""
    if not isinstance(actions, (list, tuple)) or not actions:
        raise ValueError("Choose at least one Word action.")
    known = OFFERED if offered_only else tuple(ACTIONS)
    clean: List[str] = []
    for value in actions:
        if not isinstance(value, str) or value not in known:
            raise ValueError(f"{value!r} is not a Word action on the menu ({', '.join(OFFERED)}).")
        if value in clean:
            raise ValueError(f"{ACTIONS[value].label} is listed twice; an action runs once.")
        clean.append(value)
    if "repair" in clean and clean[0] != "repair":
        raise ValueError(f"{ACTIONS['repair'].label} comes first: it is how Word opens the copy.")
    if "accept_changes" in clean and "reject_changes" in clean:
        raise ValueError("Accepting and rejecting every tracked change cannot both run: after one, none is left for "
                         "the other.")
    return clean


def menu() -> List[Dict[str, str]]:
    """The menu as the window shows it, in its order."""
    return [{"id": a.id, "label": a.label, "does": a.does} for a in MENU if a.offered]


def sentences(actions: Sequence[str]) -> List[str]:
    """One sentence per action, in order: what Word does to the copy (the plan's own words, and the history's)."""
    return [f"Word action, {ACTIONS[a].label}: {ACTIONS[a].does}" for a in actions]


def describe(actions: Sequence[str]) -> str:
    """The whole run in a few sentences, for the plan and the chat."""
    acts = [ACTIONS[a] for a in actions]
    if len(acts) == 1:
        what = acts[0].does
    else:
        what = "Word runs these on the copy, in this order: " + " ".join(
            f"({i}) {a.label}: {a.does}" for i, a in enumerate(acts, 1))
    return what + " Not tracked. Word saves the whole file again, so nothing is predicted."


def account(report: Sequence[Dict[str, Any]]) -> List[str]:
    """Word's own account of what it did, one line per action, from the worker's report (``{"action", "count",
    "note"}``): the count in the action's own words, then anything it left as it was."""
    lines: List[str] = []
    for entry in report or []:
        action = ACTIONS.get(str(entry.get("action")))
        if action is None:
            continue
        n = entry.get("count")
        n = n if type(n) is int and n >= 0 else 0
        if action.id in ALWAYS_CHANGES:
            said = action.one
        elif not n:
            said = f"{action.label}: nothing to do"
        else:
            said = (action.one if n == 1 else action.many).format(n=f"{n:,}")
        note = " ".join(str(entry.get("note") or "").split())[:300]
        lines.append(said + (f" ({note})" if note else ""))
    return lines


def did_nothing(actions: Sequence[str], report: Sequence[Dict[str, Any]]) -> bool:
    """Every action found nothing to do (and none is one whose saving again is itself the change)."""
    if any(a in ALWAYS_CHANGES for a in actions):
        return False
    counts = {str(e.get("action")): e.get("count") for e in report or []}
    return all(not counts.get(a) for a in actions)


_AVAILABLE: Optional[bool] = None


def available(fresh: bool = False) -> bool:
    """Word can be reached from here (``render.word_com.available``), asked once per process."""
    global _AVAILABLE
    if _AVAILABLE is None or fresh:
        try:
            from .render import word_com
            _AVAILABLE = bool(word_com.available())
        except Exception:  # noqa: BLE001 — no Word, no pywin32, not Windows: the menu says so
            _AVAILABLE = False
    return _AVAILABLE


def run_word_actions(source: Path, actions: Sequence[str], output: Path,
                     timeout: Optional[float] = None) -> List[Dict[str, Any]]:
    """Word runs ``actions`` on ``source`` (a private copy) and saves the result as ``output``; the worker's report
    per action comes back. The one seam to Word: tests replace it."""
    from .render.word_worker import run_word
    result = run_word("actions", Path(source), actions=list(actions), output=Path(output),
                      timeout=timeout or TIMEOUT_S)
    return list(result.get("report") or [])


def failure(error: BaseException, actions: Sequence[str]) -> str:
    """Why Word made no copy, in words the window can show: Word's own message, the step it stopped in, and — for
    protection it could not remove — what can."""
    code = getattr(error, "code", "") or type(error).__name__
    reason = " ".join(str(getattr(error, "reason", "") or error).split())[:600] or "Word stopped"
    details = getattr(error, "details", None) or {}
    step = ACTIONS.get(str(details.get("stage") or "").replace("word_action_", ""))
    where = f" in “{step.label}”" if step is not None else ""
    hint = PASSWORD_HINT if step is not None and step.id == "remove_protection" else ""
    return f"Word could not finish{where} [{code}]: {reason.rstrip('.')}.{hint}"


def trial(actions: Sequence[str], f: Path, workdir: Path):
    """``f`` through Word and the independent verifier: a ``codegen.FileTrial`` as an Obliterate custom method's is.
    The source is acquired and checked for drift as any trial's; Word opens a private copy of its bytes; the copy it
    saves is read by ``verify.run_difference(rewritten=True)``. A copy passes with its failed checks listed
    (``unverified``) and ``unproven`` set; a document Word could not finish, or found nothing to do in, gets no copy
    and the reason."""
    from .codegen import FileTrial
    from .verify import CheckResult, run_difference
    f = Path(f).resolve()
    with diag.scope(trial_id=diag.new_id()), \
            diag.span("word_actions.trial", source=str(f), actions=list(actions)) as outcome:
        try:
            identity = guard.acquire(f)
            original = f.read_bytes()
            if hashlib.md5(original).hexdigest() != identity.md5 or guard.drifted(identity):
                raise guard.GuardError("source changed while acquiring the Word action's original bytes")
        except (OSError, guard.GuardError) as error:
            return FileTrial(f, False, [f"source acquisition refused: {error}"])

        def failed(problems, output=None):
            outcome.update(passed=False, problem_count=len(problems))
            return FileTrial(f, False, problems, output, identity=identity)

        workdir = Path(workdir)
        private = workdir / "in" / f.name
        output = workdir / "out.docx"
        try:
            private.parent.mkdir(parents=True, exist_ok=True)
            private.write_bytes(original)
            output.unlink(missing_ok=True)
            report = run_word_actions(private, actions, output)
        except stopping.Stopped:
            raise
        except Exception as error:  # noqa: BLE001 — Word's failure is this document's reason, never the job's
            diag.event("word_actions.failed", level="warning", source=str(f), actions=list(actions),
                       error=diag.exception_record(error, "word-actions"))
            return failed([failure(error, actions)])
        said = account(report)
        if not output.is_file():
            return failed(["Word reported no error and saved no copy."])
        if did_nothing(actions, report):
            return failed([f"{NOTHING_TO_DO}: " + "; ".join(said) + "." if said else f"{NOTHING_TO_DO}."])
        try:
            out_bytes = output.read_bytes()
            with diag.span("word_actions.trial.verify") as checked:
                rec = run_difference(original, out_bytes, str(f), str(output), rewritten=True)
                checked.update(passed=rec.passed, checks={c.id: c.status for c in rec.checks})
        except Exception as error:  # noqa: BLE001 — a file the verifier cannot open at all is no copy
            return failed([f"the verifier could not read the copy Word saved at all ({type(error).__name__}: "
                           f"{error})"], output)
        if said:
            rec.notes.append("Word's own account of what it did (Word's words, not the verifier's): " + "; ".join(said)
                             + ".")
        try:
            drift = guard.drifted(identity)
        except OSError as error:
            drift = str(error)
        if drift:
            rec.set(CheckResult("V9", "Source identity", "FAIL", f"source changed during the Word actions: {drift}"))
            return failed([f"V9 FAIL: source changed during the Word actions: {drift}"], output)
        outcome.update(passed=True, output_md5=hashlib.md5(out_bytes).hexdigest(),
                       failed_checks=[c.id for c in rec.checks if c.status == "FAIL"])
        return FileTrial(f, True, [], output, rec, hashlib.md5(out_bytes).hexdigest(), identity, None,
                         unverified=[f"{c.id} ({c.name})" for c in rec.checks if c.status == "FAIL"],
                         plan=shown_plan(actions), unproven=True, notes=said)


def shown_plan(actions: Sequence[str]) -> Dict[str, Any]:
    """The plan of a run that predicts nothing: no diff, part, revision or assertion, and no layout reference — the
    pages are shown beside the copy, never compared with a prediction."""
    return {"job": "word:" + "+".join(actions), "mode": "shown", "ops": [], "diffs": [], "intended_parts": [],
            "rev_delta": {}, "members_added": [], "members_removed": [], "property_assertions": [], "notes": [],
            "flags": [], "geometry_reference": "none",
            "geometry_reason": "Word saved the whole file again: its pages are shown, not compared with a prediction"}


def is_word_op(op: Any) -> bool:
    return isinstance(op, dict) and op.get("op") == OP


def plan_actions(spec: Any) -> Optional[List[str]]:
    """The actions of a plan that is Word actions and nothing else — a flat list of ``word_action`` ops — in its
    order; None for any other plan (one that names the operation among others is the gate's to refuse,
    ``plan_problem``)."""
    if not isinstance(spec, dict) or "stages" in spec or "layouts" in spec:
        return None
    ops = spec.get("ops")
    if not isinstance(ops, list) or not ops or not all(is_word_op(o) for o in ops):
        return None
    return [str((o.get("params") or {}).get("action")) for o in ops]


def names_word(spec: Any) -> bool:
    """The plan names a Word action anywhere: in its ops, its own steps or its layouts."""
    if not isinstance(spec, dict):
        return False
    if any(is_word_op(o) for o in (spec.get("ops") if isinstance(spec.get("ops"), list) else [])):
        return True
    return any(names_word(inner.get("spec")) for key in ("stages", "layouts")
               for inner in (spec.get(key) if isinstance(spec.get(key), list) else []) if isinstance(inner, dict))


ALONE = ("Word actions are a plan of their own: a flat list of ops, every one a word_action, with no select, no "
         "stages and no layouts. Word saves the whole file again, so no other operation can be planned beside them or "
         "after them in the same plan. Submit the Word actions alone; once their copies are created, plan the rest on "
         "those copies as the next request.")


def plan_problem(spec: Any) -> str:
    """Why a plan that names a Word action cannot be used; '' when it can (or names none)."""
    if not names_word(spec):
        return ""
    actions = plan_actions(spec)
    if actions is None:
        return ALONE
    for o in spec["ops"]:
        if o.get("select") or set(o.get("params") or {}) - {"action"} or o.get("expect") is not None:
            return "A word_action takes one parameter, action, and nothing else: no select, no expect."
    if spec.get("bindings") or spec.get("assertions"):
        return "A plan of Word actions takes no bindings and no assertions: nothing is predicted for it."
    try:
        check(actions)
    except ValueError as error:
        return str(error)
    return ""
