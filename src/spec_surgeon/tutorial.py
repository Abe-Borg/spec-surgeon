"""The guided tour's practice workspace (docs/CONVERSATIONAL_DESKTOP.md, "The guided tour").

The tour runs on the bundled showcase under ``examples/tutorial/`` — four specifications, a file triage refuses, a
prepared header/footer template, a logo and a table of pairs — in a workspace of its own: the window's ``DocumentTask``
is set aside untouched, a ``TutorialTask`` takes its place, and every chapter's state is built here from the showcase
through the production code paths (triage on arrival, a Safe-mode button through ``recipes.build`` and the gate, the
ordinary preview, the ordinary run that writes checked copies). No model call anywhere, no file of the user's, and
nothing kept: the practice folder under ``data_dir()/tutorial`` is replaced at the next tour.

Two things are recordings, and say so in the chat: the conversation chapters' messages (an ``app`` note at the top of
each says the words are a bundled example) and their question under the chat (``TutorialTask.scripted``). A ``send``
or ``choose`` in the practice workspace is refused before anything could reach Claude (``NO_CONVERSATION``); the page
holds those controls inert too. Every other control is live and acts on the practice copies alone.
"""
from __future__ import annotations

import copy
import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import diagnostics as diag
from .document_task import DocumentTask, LAUNCH_MODES
from .gui import Session
from .settings import data_dir

SHOWCASE_DIR = Path(__file__).resolve().parent / "examples" / "tutorial"
# The bundled files, by name (tests/make_tutorial_showcase.py builds them; tests/test_tutorial.py checks them).
DOCUMENTS = ("21 13 13 - WET-PIPE SPRINKLER SYSTEMS.docx", "21 13 16 - DRY-PIPE SPRINKLER SYSTEMS.docx",
             "21 05 00 - COMMON WORK RESULTS FOR FIRE SUPPRESSION.docx", "Clean Agent Systems (other firm).docx")
REFUSED = "21 00 00 - TABLE OF CONTENTS.docx"          # not a Word package: triage refuses it on arrival
TEMPLATE = "Office template.docx"                       # a prepared template with one placeholder the form asks for
REFERENCES = ("logo.png", "project values.csv")         # a picture and a table for the edit to use
OTHER_FIRM = DOCUMENTS[3]                               # no SECTION line: the template button leaves it untouched
HELD = DOCUMENTS[1]                                     # the document the tour marks with the red X
LEFT_OUT = DOCUMENTS[2]                                 # the document the Default-mode request leaves out
# The Safe-mode chapter's button and its form: the template's one typed placeholder (recipes.template_kind)
BUTTON_RECIPE = "headers_footers_template"
STAMP_VALUE = "ISSUED FOR CONSTRUCTION 2026-03-01"
BUTTON_INPUTS: Dict[str, Any] = {"slots": {"ISSUED FOR PERMIT": STAMP_VALUE}}
# The Default-mode chapters' request: the worked example change-all-font-arial, with one document left out
FONT_REQUEST = f"Change every font to Arial, headings included. Leave out {LEFT_OUT.split(' - ')[0]} for now."
FONT_EXAMPLE = Path(__file__).resolve().parent / "examples" / "specs" / "change-all-font-arial.json"
STAMP_REQUEST = f"Change the issue stamp in every header to {STAMP_VALUE}."
# The find-and-replace chapter (backlog F1): the button over the showcase, one occurrence of the second document
# unticked, so the list shows a kept and a left occurrence side by side.
FIND_RECIPE = "find_replace"
FIND_INPUTS: Dict[str, Any] = {"pairs": [{"find": "FM Global", "replace": "FM Approvals"}]}
# Every chapter state the page may ask for (frontend/src/lib/tour.ts names them; frontend/tests/tour.test.ts pins
# the two lists to each other). Safe mode: start, button, copies, find. Default mode: conversation, question, plan,
# todo.
SCENARIOS = ("start", "button", "copies", "find", "conversation", "question", "plan", "todo")
SAFE_SCENARIOS = ("start", "button", "copies", "find")
NO_CONVERSATION = ("The tour's conversation is a recording: nothing here is sent to Claude. End the tour to talk to "
                   "the assistant about your own documents.")
RECORDED = ("This conversation is a bundled example for the guided tour. Nothing here was sent to Claude, and nothing "
            "you click here sends anything; the plan and the pages beside it are real, made from the practice copies.")
PLAN_REPLY = ("Here is the plan. Every run that sets its own font, every style that sets one, and the document "
              "defaults go to Arial in the body, the headers and the footers; sizes, symbol fonts and list numbers "
              "stay as they are. 21 05 00 stays out of this request, as you asked, and is listed under Left "
              "untouched. The pages beside show two sample documents, before and after. If they look right, create "
              "the edited copies.")
QUESTION_REPLY = ("Three of the four documents carry an issue stamp in the header of every section; Clean Agent "
                  "Systems (other firm).docx has no stamp at all. I can change the three and leave the fourth as it "
                  "is, or give the fourth the same stamp — that would be new wording in its header, so it is a "
                  "choice you pick.")
QUESTION = "What about Clean Agent Systems (other firm).docx, which has no stamp to change?"
QUESTION_CHOICES: List[Dict[str, Any]] = [
    {"id": "tour-1", "label": "Change the three that have a stamp; leave the fourth as it is", "words": [],
     "detail": "", "editable": False},
    {"id": "tour-2", "label": "Give the fourth the same stamp in its header", "words": [STAMP_VALUE],
     "detail": "Adds one header line to Clean Agent Systems (other firm).docx", "editable": True},
]
QUESTION_CHIPS = ["Leave out the fourth for now", "Set the fourth aside for later"]


class TutorialTask(DocumentTask):
    """The practice workspace's task: the window's own class over the bundled showcase. Two differences, both said
    in the chat: a ``send`` or ``choose`` is refused before anything could reach Claude (the conversation chapters
    are recordings), and a chapter may put a recorded question under the chat (``scripted``: the view's
    ``questions``, ``choices``, ``chips`` and ``stage``)."""

    def __init__(self, session, *, render: bool, mode: str, spending_limit=None):
        super().__init__(session, journal=None, render=render, mode=mode, spending_limit=spending_limit)
        self.scripted: Dict[str, Any] = {}

    def perform(self, action, **values):
        if action in ("send", "choose"):
            raise ValueError(NO_CONVERSATION)
        return super().perform(action, **values)

    def view(self):
        shown = super().view()
        if self.scripted:
            shown.update(copy.deepcopy(self.scripted))
        return shown


def showcase_files() -> Dict[str, Path]:
    """Every bundled file by name; ``FileNotFoundError`` when the package was built without one."""
    out: Dict[str, Path] = {}
    for name in (*DOCUMENTS, REFUSED, TEMPLATE, *REFERENCES):
        path = SHOWCASE_DIR / name
        if not path.is_file():
            raise FileNotFoundError(f"the guided tour's showcase file is missing: {path}")
        out[name] = path
    return out


def practice_root() -> Path:
    return data_dir() / "tutorial"


def _new_folder() -> Path:
    """A fresh practice folder; the previous tour's is removed first (nothing of the user's is ever in it)."""
    root = practice_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    folder = root / time.strftime("%Y%m%d-%H%M%S")
    (folder / "files").mkdir(parents=True, exist_ok=True)
    return folder


def _copy_showcase(folder: Path) -> Dict[str, Path]:
    copies: Dict[str, Path] = {}
    (folder / "files").mkdir(parents=True, exist_ok=True)
    for name, source in showcase_files().items():
        target = folder / "files" / name
        shutil.copyfile(source, target)
        copies[name] = target
    return copies


def _message(role: str, text: str, at: float) -> Dict[str, Any]:
    return {"role": role, "text": text, "at": at}


def build_scenario(kind: str, folder: Path, *, render: bool, spending_limit=None) -> TutorialTask:
    """The chapter state ``kind`` on a fresh task over the showcase copies in ``folder``: every step a production path
    and no model call (``tests/test_tutorial.py`` runs each with the model client refusing)."""
    if kind not in SCENARIOS:
        raise ValueError(f"There is no tutorial chapter state named {kind!r}.")
    files = _copy_showcase(folder)
    mode = "safe" if kind in SAFE_SCENARIOS else "default"
    assert mode in LAUNCH_MODES
    session = Session(out_root=folder / "runs", visual_check=False)
    task = TutorialTask(session, render=render, mode=mode, spending_limit=spending_limit)
    task.attach([str(files[name]) for name in (*DOCUMENTS, REFUSED)])
    task.attach([str(files[TEMPLATE])], template=True)
    task.attach([str(files[name]) for name in REFERENCES])
    quiet = lambda _m: None  # noqa: E731
    held = str(files[HELD].resolve())
    if kind == "start":
        return task
    if kind in ("button", "copies"):
        # the Safe-mode button, exactly as the panel sends it (recipes.build → compose_submission → the preview)
        view = task.perform("recipe", recipe=BUTTON_RECIPE, inputs=copy.deepcopy(BUTTON_INPUTS), log=quiet)
        if kind == "button":
            return task
        task.perform("primary", token=view["token"], hold=[held], log=quiet)
        return task
    if kind == "find":
        # the find-and-replace button, then one occurrence unticked as the page sends it (the occurrences action)
        view = task.perform("recipe", recipe=FIND_RECIPE, inputs=copy.deepcopy(FIND_INPUTS), log=quiet)
        listed = view["occurrences"]
        key = listed[1]["items"][0]["key"] if len(listed) > 1 and listed[1]["items"] else listed[0]["items"][0]["key"]
        task.perform("occurrences", skip=[key], log=quiet)
        return task
    if kind == "conversation":
        return task
    now = time.time()
    if kind == "question":
        task.messages = [_message("app", RECORDED, now - 60), _message("user", STAMP_REQUEST, now - 50),
                         _message("assistant", QUESTION_REPLY, now - 40)]
        task.scripted = {"stage": "question", "questions": [QUESTION], "choices": copy.deepcopy(QUESTION_CHOICES),
                         "chips": list(QUESTION_CHIPS), "action": "", "token": ""}
        task.stage = "question"
        return task
    # plan, todo: the worked example's font plan through the gate and the ordinary preview, one document left out
    from . import compiler
    spec = json.loads(FONT_EXAMPLE.read_text(encoding="utf-8"))
    spec["request"] = FONT_REQUEST
    batch_files = session.files(outcome=False)
    outcome, errors = compiler.compose_submission(spec, compiler._Batch(batch_files, session.template),
                                                  [FONT_REQUEST])
    if errors or outcome is None:
        raise RuntimeError("the tour's font plan did not pass the gate: " + "; ".join(errors))
    # source "recipe": a plan built in code, model-free — no visual check, no recovery (Session.model_free)
    session._set_outcome(spec=outcome, source="recipe", left_out=[files[LEFT_OUT].resolve()])
    task.messages = [_message("app", RECORDED, now - 60), _message("user", FONT_REQUEST, now - 50)]
    task.decisions = [FONT_REQUEST]
    session.make_preview(render, quiet)
    task.messages.append(_message("assistant", PLAN_REPLY, now - 40))
    task.stage = "preview"
    if kind == "plan":
        return task
    task.perform("primary", token=task.action_token(), hold=[held], log=quiet)
    return task


class Tutorial:
    """The tour's lease on the window: which practice task stands in for the user's, and the chapter it shows. One at
    a time; ``begin`` with the same ``request_id`` twice is one tour (a repeated click joins it)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.id: Optional[str] = None
        self.scenario: Optional[str] = None
        self.folder: Optional[Path] = None
        self.request_id: Optional[str] = None
        self.task: Optional[TutorialTask] = None

    @property
    def active(self) -> bool:
        return self.id is not None

    def status(self) -> Dict[str, Any]:
        return {"active": self.active, "id": self.id, "scenario": self.scenario}

    def begin(self, *, render: bool, spending_limit=None, request_id: Optional[str] = None) -> TutorialTask:
        with self.lock:
            if self.active:
                if request_id and request_id == self.request_id and self.task is not None:
                    return self.task
                raise RuntimeError("A tour is already running; end it before starting another.")
            self.folder = _new_folder()
            self.task = build_scenario("start", self.folder, render=render, spending_limit=spending_limit)
            self.id = uuid.uuid4().hex
            self.scenario = "start"
            self.request_id = request_id
            diag.event("tutorial.started", tutorial=self.id)
            return self.task

    def show(self, tutorial_id: str, kind: str, *, render: bool, spending_limit=None) -> TutorialTask:
        """The chapter state ``kind``, built afresh from the showcase (a state is never carried from one chapter to
        the next, so a click in the last chapter changes nothing in this one)."""
        with self.lock:
            if not self.active or tutorial_id != self.id or self.folder is None:
                raise LookupError("This tour is no longer running.")
            if kind not in SCENARIOS:
                raise ValueError(f"There is no tutorial chapter state named {kind!r}.")
            if kind == self.scenario and self.task is not None:
                return self.task
            folder = self.folder / kind
            if folder.exists():
                shutil.rmtree(folder, ignore_errors=True)
            folder.mkdir(parents=True, exist_ok=True)
            self.task = build_scenario(kind, folder, render=render, spending_limit=spending_limit)
            self.scenario = kind
            diag.event("tutorial.scenario", tutorial=self.id, scenario=kind)
            return self.task

    def end(self, tutorial_id: Optional[str] = None) -> bool:
        """Ends the tour: the practice folder is removed (it holds nothing of the user's). False when no tour runs,
        or another one does."""
        with self.lock:
            if not self.active or (tutorial_id is not None and tutorial_id != self.id):
                return False
            ended = self.id
            self.id, self.scenario, self.folder, self.request_id, self.task = None, None, None, None, None
        shutil.rmtree(practice_root(), ignore_errors=True)
        diag.event("tutorial.ended", tutorial=ended)
        return True
