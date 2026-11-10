"""Op: word_action — one of Obliterate's named Word actions, as a plan names it. Obliterate mode only.

The owner's decision of 2026-11-10 (CLAUDE.md, "Standing decisions"; docs/BACKLOG.md, O5): Word may edit copies
through a fixed menu of named actions (``spec_surgeon.word_actions.MENU``), which the user picks in the window or
Claude names in a plan. No Word script is composed anywhere: a case the menu cannot express is first a new menu item.

This class is how a plan NAMES an action — the catalog entry Claude reads in Obliterate mode, the schema the gate
validates, the sentence the history keeps — and nothing more. It is the one built-in operation that predicts nothing
(the second exception to invariant 1, the owner's, with an Obliterate custom method): Word writes the whole file
again when it saves, so there is no analytic prediction to make. So it is never planned or applied by the engine's
pipeline. A plan of Word actions is a plan of its own (a flat list of these ops and nothing else: the gate refuses
any other, ``word_actions.plan_problem``), and the session turns it into copies before approval
(``gui.Session._word_copies``): Word runs the actions on a private copy of each document, the verifier's own report
reads each copy (``verify.run_difference(rewritten=True)``), the user is shown what each copy changes, and Create
delivers those bytes — marked UNPROVEN, or UNVERIFIED when a check of the file failed.

Refusals: ``word-alone`` (the engine was asked to plan it beside other operations, or outside the path above).
"""
from __future__ import annotations

from typing import Any, Dict

from .. import word_actions
from . import register
from .base import JobException, Op, PlanFragment


@register
class WordAction(Op):
    name = "word_action"
    mode = "untracked"
    obliterate_only = True
    description = (
        "OBLITERATE MODE ONLY. One named action that Microsoft Word itself runs on a copy of each document; the "
        "actions are a fixed menu (the `action` values below) and nothing else can be asked of Word. A plan of Word "
        "actions is a plan of its own: a flat list of ops, every one a word_action, run in the order listed in one "
        "opening of the copy, with no select, no stages, no layouts and no other operation beside them. Word saves "
        "the whole file again, so nothing is predicted: every document's copy is made before the user approves, the "
        "independent reader lists the words each copy adds and removes and what else it changes, and each copy is "
        "delivered marked UNPROVEN (UNVERIFIED when a check of its file fails). Never tracked. The actions — "
        + " ".join(f"{a.id}: {a.does}" for a in word_actions.MENU if a.offered))
    params_schema = {"type": "object", "required": ["action"], "properties": {
        "action": {"type": "string", "enum": list(word_actions.OFFERED),
                   "description": "the named Word action; repair, when listed, is first"},
    }, "additionalProperties": False}
    select_schema = {}
    verification_profile = {
        "V1": "the copy opens as a package: every part parses, every relationship has its part and its content type",
        "V2": "nothing is predicted: the words each copy adds and removes are listed from the copy for the user",
        "V4": "N/A — Word writes the copy's revision ids itself",
        "V5": "N/A — every part of the copy is Word's own writing",
        "V6": "the renderer opens the copy; its pages are shown beside the original's",
        "V7": "none — Word saved the whole file again; nothing was predicted to compare the pages with",
        "V10": "N/A — nothing is asserted; what else each copy changes is listed (D1), parts paired by their place in "
               "the document and compared without Word's own save marks",
    }

    @classmethod
    def describe(cls, select: Dict[str, Any], params: Dict[str, Any]) -> str:
        action = word_actions.ACTIONS.get(str(params.get("action")))
        if action is None:
            return "Word runs a named action on the copy. Not tracked; shown, not proven."
        return word_actions.sentences([action.id])[0] + " Not tracked; shown, not proven."

    def plan(self, pkg, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        raise JobException(
            "a Word action is never planned beside other operations: Word saves the whole file again, so nothing "
            "can be predicted for it or for a step after it. Its plan is a plan of its own, made into copies before "
            "approval", "word-alone", {"action": params.get("action")})

    def apply(self, pkg, frag: PlanFragment) -> None:
        raise JobException("a Word action is run by Word on a copy before approval, never applied by the engine",
                           "word-alone")
