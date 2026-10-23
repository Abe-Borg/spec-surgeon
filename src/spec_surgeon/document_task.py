"""One conversational document task, independent of the window.

The Session is a toolbox: compile, reply, preview, run, recover, each a verified engine step. This coordinator owns
the stages the user sees — empty, question, preview, done, budget, resume — and moves between them on the Session's
single decision table (``Session.outcome``), never by probing engine fields. It keeps the user's decisions together,
triages every attachment the moment it arrives, and persists a recoverable journal. Journals never restore delivery
approval or execute saved code.

Attachments are Word documents to edit, at most one template, and pictures or tables the user attached for the edit
to use (``reference``: a new logo, a sheet of project values); nothing beside them is discovered. There is no spending
ceiling: every model call writes up to the model's maximum output at high effort, the work pauses after
``STEPS_PER_PAUSE`` model calls until the user says to go on, the window shows the estimated spending, and its Stop
cuts off the call that is running (``llm.cancellation_scope``). Continue after a pause, a stop or a failed call picks
the conversation up where it stopped.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import llm, review_display
from .gui import Session
from .inputs import KINDS as REFERENCE_KINDS
from .recovery_state import atomic_json
from .resources import DEFAULT_MAX_OUTPUT_TOKENS

STEPS_PER_PAUSE = 48    # model calls between the pauses where the user says to go on
AUTO_STEPS = 6          # internal steps the coordinator takes on its own after one user action (bounded)
STAGES = ("empty", "question", "preview", "done", "budget", "resume")
START_OVER = "Start over from the originals"   # the reply (a chip, a button) that drops the working copies
LEFT_OUT_REASON = "You asked to leave it out of this request; it stays as it is."
WORK_TODO = "Work on the documents I set aside"  # the reply (a chip, typed) that begins the pass over the to-do list
# the reply (a chip, typed) that ends a recovery by putting the documents it could not fix on the to-do list
SET_ASIDE_REST = "Set the rest aside for later"
# why a document is on the to-do list (``todo[...]["why"]``), and the sentence the window and the model are given
WHY_HELD, WHY_ASKED, WHY_REFUSED, WHY_FAILED = "held", "asked", "refused", "failed"
HELD_REASON = "You marked its preview as needing more work."
ASKED_REASON = "You asked to set it aside for later."
CONVERSATION_EFFORT = "medium"   # the model's own default: fewer tool rounds per turn than high, measured by the owner
RECOVERY_EFFORT = "high"         # the hard path keeps the depth
RECOVERY_ATTEMPTS = 8            # candidate attempts per problem group (docs/RECOVERY.md), on every recovery path

# What the window says while the model uses a tool: one plain sentence per tool, never the tool's name.
TOOL_ACTIVITY = {
    "inspect_profile": "Looking at how the documents are built", "list_styles": "Reading the styles",
    "compare_files": "Comparing the documents side by side",
    "sample_paragraphs": "Reading sample paragraphs", "show_part_text": "Reading a header or footer",
    "read_template": "Reading the template", "read_input_file": "Reading the attached table",
    "view_pages": "Looking at the rendered pages", "describe_op": "Checking what an edit can do",
    "inspect_template_styles": "Checking the template's styles", "list_parts": "Listing the document's parts",
    "read_paragraphs": "Reading the documents", "read_xml_page": "Reading the document's structure",
    "run_analysis": "Analysing the documents", "read_analysis_result": "Reading the analysis",
    "propose_defined_terms": "Finding defined terms", "read_context_archive": "Looking back at the conversation",
    "submit_editspec": "Writing the plan", "ask_user": "", "set_aside": "Setting documents aside",
}
# The tools whose input carries the model's words to the user in a top-level "message": the reply joins them to its
# plain text (``conversation._ask_turn``, ``_spec_turn``), so the window shows them as they are written.
TELLING_TOOLS = ("ask_user", "submit_editspec")
_JSON_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class MessageWords:
    """The words of a tool call's top-level ``"message"`` string, decoded, as the call's input JSON streams in.

    Every other part of the input (the spec, the questions, the choices) is read past and never returned. Whitespace
    before the first word and after the last is held back, as the reply drops it (``inp["message"].strip()``). So is a
    message that may repeat a part of the reply already written (``known``: the call's plain text, an earlier
    question's message): the reply keeps each part once (``dict.fromkeys``), so the words are held while they match
    one, returned once they part from it, and dropped when they turn out the same."""

    def __init__(self, known: Sequence[str] = ()) -> None:
        self.depth = 0
        self.expect_key = False
        self.key: Optional[str] = None      # the last key read at the top level
        self.in_string = False
        self.reading = ""                   # the string being read: "key", "message" or "skip"
        self.chars: List[str] = []
        self.escape = ""
        self.high = 0                       # a high surrogate waiting for its pair
        self.known = [part for part in known if part]
        self.matching = bool(self.known)    # the words so far could still be a part already written
        self.words = ""                     # the message's words read so far
        self.told = False                   # a word was returned
        self.done = False                   # the message's string has ended
        self.held = ""

    def feed(self, piece: str) -> str:
        out: List[str] = []
        for ch in piece:
            if self.in_string:
                self._string_char(ch, out)
            elif ch == '"':
                self.in_string, self.chars = True, []
                self.reading = ("key" if self.depth == 1 and self.expect_key else
                                "message" if self.depth == 1 and self.key == "message" else "skip")
            elif ch in "{[":
                self.depth += 1
                if self.depth == 1:
                    self.expect_key = ch == "{"
            elif ch in "}]":
                self.depth -= 1
            elif self.depth == 1 and ch == ",":
                self.expect_key = True
            elif self.depth == 1 and ch == ":":
                self.expect_key = False
        return "".join(out)

    def _string_char(self, ch: str, out: List[str]) -> None:
        if self.escape:
            self.escape += ch
            if self.escape.startswith("\\u") and len(self.escape) < 6:
                return
            text, self.escape = self._unescape(self.escape), ""
        elif ch == "\\":
            self.escape = ch
            return
        elif ch == '"':
            self.in_string = False
            if self.reading == "key":
                self.key = "".join(self.chars)
            elif self.reading == "message":
                self.key, self.held, self.done = None, "", True
                if self.matching and self.words and self.words not in self.known:
                    self._return(self.words, out)       # it began like an earlier part, and is not one
                self.matching = False
            return
        else:
            text = ch
        if self.reading == "key":
            self.chars.append(text)
        elif self.reading == "message" and text:
            if text.isspace():
                self.held += text if self.words else ""
                return
            piece, self.held = self.held + text, ""
            self.words += piece
            if self.matching:
                if any(part.startswith(self.words) for part in self.known):
                    return
                self.matching, piece = False, self.words
            self._return(piece, out)

    def _return(self, piece: str, out: List[str]) -> None:
        out.append(piece)
        self.told = True

    def _unescape(self, escape: str) -> str:
        if not escape.startswith("\\u"):
            return _JSON_ESCAPES.get(escape[1:], "")
        try:
            code = int(escape[2:], 16)
        except ValueError:
            return ""
        if 0xD800 <= code < 0xDC00:
            self.high = code
            return ""
        high, self.high = self.high, 0
        if 0xDC00 <= code < 0xE000:
            return chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)) if high else ""
        return chr(code)


STATUS_FOR_LOG = {"Preparing and checking the preview…": ("previewing", "Preparing and checking the preview…"),
                  "Looking at the rendered pages…": ("looking", "Looking at the rendered pages…"),
                  "Creating and checking your edited copies…": ("creating", "Creating and checking your edited copies…"),
                  "Checking the files that still need work…": ("recovering", "Checking the files that still need work…"),
                  "Continuing where the conversation stopped…": ("resuming", "Continuing where the conversation stopped…")}


@dataclass
class Attachment:
    """A file the user attached, with what triage found the moment it arrived: a document to edit, the template, or a
    picture or table for the edit to use (``reference``)."""
    path: Path
    template: bool = False
    reference: bool = False
    state: str = "ok"                       # ok | note | refused
    notes: List[str] = field(default_factory=list)
    reason: str = ""                        # the refusal, one sentence
    pattern_id: str = ""

    @property
    def refused(self) -> bool:
        return self.state == "refused"

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "template": self.template, "reference": self.reference, "state": self.state,
                "notes": list(self.notes), "reason": self.reason, "pattern_id": self.pattern_id}


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def triage_reference(path: Path) -> Attachment:
    """A picture or table attached for the edit to use: model-free, milliseconds — it opens, it is within the size the
    conversation reads, and what it is."""
    from .inputs import overview
    path = Path(path)
    found = overview(path)
    if found.get("problem"):
        return Attachment(path, reference=True, state="refused", reason=f"{path.name} {found['problem']}.")
    if found["kind"] == "picture":
        width, height = found.get("pixels") or (0, 0)
        note = f"A {str(found.get('format') or 'picture').upper()} picture, {width} × {height} pixels, for the edit to use."
    elif found.get("sheets") is not None:
        note = f"A spreadsheet with {len(found['sheets'])} sheet(s), for the edit to use."
    elif found.get("headers") is not None:
        note = f"A table with {found.get('rows', 0)} row(s), for the edit to use."
    else:
        note = "A data file, for the edit to use."
    return Attachment(path, reference=True, state="note", notes=[note])


def triage(path: Path, *, template: bool = False) -> Attachment:
    """Deterministic, model-free, milliseconds: can the engine work with this file, and what should the user know."""
    try:
        from .triage import triage_file
    except ImportError:                     # the triage module is optional until it ships with the package
        return Attachment(Path(path), template=template)
    found = triage_file(path)
    return Attachment(Path(path), template=template, state=found.state, notes=list(found.notes),
                      reason=found.reason, pattern_id=getattr(found, "pattern_id", "") or "")


def _same_file_key(path) -> str:
    """One spelling of a path for comparing files: resolved when the file system can, the text otherwise."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


class DocumentTask:
    def __init__(self, session=None, *, journal=None, render=True):
        self.session = session or Session()
        self.session.recovery_attempts = RECOVERY_ATTEMPTS   # the recovery a reviewed outcome plan starts, too
        self.render = render
        self.journal = Path(journal) if journal else None
        self.messages: List[Dict[str, str]] = []
        self.decisions: List[str] = []
        self.word_sources: List[str] = []
        self.results: Dict[str, str] = {}
        self.attachments: List[Attachment] = []
        self.stage = "empty"
        self.problem = ""
        # continue on the result: the delivered copies become what the next request works on (original → current),
        # a generation counter, and what was already done, told to the model with the next request
        self.working: Dict[str, str] = {}
        self.generation = 0
        self.history: List[str] = []
        # the changes each copy carries, by attached original: a run on part of the batch (the user set documents
        # aside) gives its copies changes the others never got, and the model is told per document
        self.applied: Dict[str, List[str]] = {}
        # the to-do list: documents the user set aside at approval (the preview did not look right for them), keyed
        # by the attached original — with the decisions they were set aside from and the plan they saw; and the
        # focus: the pass over them, once the user begins it (its documents, the plan they saw)
        self.todo: Dict[str, Dict[str, Any]] = {}
        self.focus: Optional[Dict[str, Any]] = None
        self._binned: List[str] = []        # documents of this request that could not take the change, on the list
        self._parked = ("", 0)              # (conversation id, how many of its set-aside documents were taken)
        self._events = None                 # the window's live channel for the job that is running (perform)
        self._talking = False               # inside a call of the conversation itself: its words are the reply
        self._said = False                  # the model call that is streaming has written words to the user
        self._plain = ""                    # its plain text so far
        self._telling: Dict[int, MessageWords] = {}   # its tool calls that carry a message, by content block
        self._seen = 0
        self._conversation_id = None
        # Claude's latest reply as the chat said it; None when the chat did not say it (a recovery turn's): the
        # clarification card carries those words instead (``view``)
        self._replied: Optional[str] = None
        self._revision = 0
        self._resume_recovery = None
        self._resume_layouts = []           # layout batch folders still to recover, from the journal
        self._budget = self._new_budget()
        # Files a caller handed the session (tests, the installed smoke test) become attachments.
        given: List[Path] = []
        given = list(self.session.documents or [])
        template = self.session.template
        references = list(self.session.inputs or [])
        self.session.documents, self.session.template, self.session.inputs = [], None, []
        for path in given:
            self.attachments.append(triage(Path(path)))
        if template:
            self.attachments.append(triage(Path(template), template=True))
        for path in references:
            self.attachments.append(triage_reference(Path(path)))
        self._sync()

    @staticmethod
    def _new_budget(max_calls=STEPS_PER_PAUSE):
        """No spending or token ceiling; the call count pauses the work for the user's go-ahead."""
        return llm.CallBudget(max_cost_usd=None, max_calls=max_calls, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                              max_total_tokens=None, effort=CONVERSATION_EFFORT)

    # ---- attachments ----
    def documents(self) -> List[Path]:
        """The documents the next request works on: each attached document, or the copy the last run made of it —
        without the documents on the to-do list, or only the set-aside documents while the user works on them."""
        return [self.current_of(a.path) for a in self.attachments if self._in_scope(a)]

    def _in_scope(self, attachment) -> bool:
        if attachment.template or attachment.reference or attachment.refused:
            return False
        key = str(attachment.path)
        if self.focus:
            return key in self.focus["documents"]
        return key not in self.todo

    def current_of(self, original) -> Path:
        return Path(self.working.get(str(Path(original)), str(Path(original))))

    def origin_of(self, current) -> Path:
        """The attached original a working copy stands for (itself when it is an original). Compared as the files
        on disk: the engine resolves the paths it reads (a results folder behind a junction, a mapped drive or
        another spelling of the case would otherwise never match the copy it was given)."""
        wanted = _same_file_key(current)
        for original, path in self.working.items():
            if path == str(Path(current)) or _same_file_key(path) == wanted:
                return Path(original)
        return Path(current)

    # ---- the window's live channel ----
    @contextlib.contextmanager
    def _conversation(self):
        """Around a call of the conversation itself (compile, a reply, a resumed turn): its words are the reply."""
        self._talking = True
        try:
            yield
        finally:
            self._talking = False

    def _emit(self, event: Dict[str, Any]) -> None:
        if self._events is None:
            return
        try:
            self._events(event)
        except Exception:  # noqa: BLE001 — the window's problem, never the job's
            pass

    def _on_stream(self, event: Dict[str, Any]) -> None:
        """A model call's stream, as the window shows it: the model's words as they are written (its plain text, then
        the ``message`` of a question or a submission, a paragraph apart, as the reply joins them), its thinking
        summary, and one plain sentence per tool it uses. Words and thinking are relayed from the conversation's
        own calls alone (``_talking``): the visual reviewer, recovery and a custom method also call the model, and
        their text is not the assistant's reply to the user. ``thinking`` marks the start of each call: the words
        of an earlier call in the same turn are not part of the reply the turn ends with."""
        kind = event.get("type")
        if kind == "started":
            self._said, self._plain, self._telling = False, "", {}
            self._emit({"type": "status", "kind": "thinking", "text": "Thinking…"})
        elif kind == "retry":
            # the attempt is abandoned and sent again: the page drops the words it had from it
            self._said, self._plain, self._telling = False, "", {}
            self._emit({"type": "status", "kind": "retrying", "text": "Sending again…"})
        elif kind in ("text_delta", "thinking_delta"):
            if self._talking:
                text = event.get("text", "")
                if kind == "text_delta":
                    self._plain += str(text)
                    self._said = self._said or bool(str(text).strip())
                self._emit({"type": kind, "text": text})
        elif kind == "tool":
            name = str(event.get("name") or "")
            if self._talking and name in TELLING_TOOLS and isinstance(event.get("index"), int):
                # the parts of the reply already written, which the reply would not repeat
                known = [self._plain.strip()] + [words.words for words in self._telling.values() if words.done]
                self._telling[event["index"]] = MessageWords(known)
            if name == "submit_editspec":
                self._emit({"type": "status", "kind": "planning", "text": "Writing the plan…"})
            else:
                text = TOOL_ACTIVITY.get(name, "")
                if text:
                    self._emit({"type": "activity", "text": text})
        elif kind == "tool_input":
            words = self._telling.get(event.get("index"))
            if words is None or not self._talking:
                return
            first = not words.told
            text = words.feed(str(event.get("json") or ""))
            if text:
                self._emit({"type": "text_delta", "text": ("\n\n" if first and self._said else "") + text})
                self._said = True

    def template(self) -> Optional[Path]:
        return next((a.path for a in self.attachments if a.template and not a.refused), None)

    def references(self) -> List[Path]:
        return [a.path for a in self.attachments if a.reference and not a.refused]

    def _sync(self) -> None:
        self.session.documents = self.documents()
        self.session.template = self.template()
        self.session.inputs = self.references()

    def say(self, text, role="app"):
        if text:
            message = {"role": role, "text": str(text), "at": time.time()}
            self.messages.append(message)
            if role != "user":
                # the id the view numbers this message by (docs/WEB_API.md: ``m<index>``), so the page can finalize
                # the streaming bubble under it and keep its aside
                self._emit({"type": "message", "message": {"id": f"m{len(self.messages) - 1}", **message}})

    def _invalidate(self):
        self.session._set_outcome()
        self.session.end_conversation()
        self._resume_recovery = None
        self._revision += 1
        self.stage = "resume" if self.decisions else "empty"
        self.problem = ""

    def attach(self, paths, *, template=False):
        """Word documents to edit, or the template; pictures and tables (``REFERENCE_KINDS``) are attached for the edit
        to use. Anything else is refused before anything changes."""
        checked: List[Path] = []
        for value in paths:
            path = Path(value).resolve()
            suffix = path.suffix.lower()
            usable = suffix == ".docx" or (not template and suffix in REFERENCE_KINDS)
            if not path.is_file() or not usable or path.name.startswith("~$"):
                raise ValueError(f"Choose a Word .docx file: {path.name}" if template else
                                 f"Choose Word .docx documents, pictures (.png, .jpg, .gif, .bmp) or tables (.xlsx, .csv, "
                                 f".json): {path.name}")
            if path not in checked:
                checked.append(path)
        if not checked:
            return
        if template and len(checked) != 1:
            raise ValueError("Choose one template document.")
        before = (self.documents(), self.template(), self.references())
        fresh: List[str] = []
        if template:
            found = triage(checked[0], template=True)
            if found.refused:
                raise ValueError(found.reason)
            self.attachments = [a for a in self.attachments if a.path != checked[0] and not a.template]
            self.attachments.append(found)
        else:
            known = {a.path for a in self.attachments}
            for path in checked:
                if path in known:
                    continue
                self.attachments.append(triage(path) if path.suffix.lower() == ".docx" else triage_reference(path))
                if self.working and path.suffix.lower() == ".docx":
                    fresh.append(path.name)
                if self.focus and path.suffix.lower() == ".docx":
                    self.focus["documents"].append(str(path))      # attached during the pass: it joins the pass
        self._sync()
        if not template:
            self._say_same_names([p for p in checked if p.suffix.lower() == ".docx"])
        if before != (self.documents(), self.template(), self.references()):
            self._invalidate()
            if self.decisions:
                self.say("The attachments changed. I’ll check the updated documents against your request.")
            if fresh:
                self.say(f"{', '.join(fresh)}: attached as an original. The changes already made to the other copies "
                         f"are not in it; the next change is made to it as it is. To give it every change, say "
                         f"“{START_OVER}” and ask again.")
        self.save()

    def _say_same_names(self, fresh: List[Path]) -> None:
        """A document attached beside another of the same name (the 1.8.0 live run: a template's folder copy of
        21 13 13 attached as a 13th document) is named once, with both folders, so a template attached as a document
        is caught before it is edited like the others."""
        attached = [a.path for a in self.attachments
                    if not a.template and not a.reference and a.path.suffix.lower() == ".docx"]
        for name in dict.fromkeys(p.name.lower() for p in fresh):
            same = [p for p in attached if p.name.lower() == name]
            if len(same) > 1:
                folders = ", ".join(f"“{p.parent.name or p.parent}”" for p in same)
                self.say(f"{len(same)} attached documents are named “{same[0].name}” (in {folders}); each is edited "
                         f"as a separate document. If one of them is meant as the template, remove it and attach it "
                         f"as the template instead.")

    def remove(self, path, *, template=False):
        path = Path(path)
        self.attachments = [a for a in self.attachments if not (a.path == path and a.template == template)]
        had_copies = bool(self.working)
        self.working.pop(str(path), None)
        self.applied.pop(str(path), None)
        self.todo.pop(str(path), None)
        pass_over = False
        if self.focus:
            self.focus["documents"] = [d for d in self.focus["documents"] if d != str(path)]
            if not self.focus["documents"]:
                # the pass is over with its last document: its request leaves with it, the other documents are back
                self.focus, self.decisions, pass_over = None, [], True
        self._sync()
        self._invalidate()
        if pass_over:
            self.say("The document you were working on is no longer attached, so that pass is over; the other copies "
                     "are back as they are.")
        if had_copies and not self.working:
            # the last working copy left with its original: the attached documents are originals again, and the
            # changes the history lists are in none of them
            self.history, self.generation, self.results, self.applied = [], 0, {}, {}
            self.stage = "resume" if self.decisions else "empty"
            self.say("No edited copy is attached any more, so the next change starts from your originals.")
        self.save()

    # ---- the request ----
    def _request(self):
        if not self.decisions:
            return ""
        return self.decisions[0] + ("\n\nUser decisions and revisions:\n" + "\n".join(self.decisions[1:])
                                    if len(self.decisions) > 1 else "")

    def _compile(self, log):
        self.session.replay_word_sources = list(self.word_sources)
        self._emit({"type": "status", "kind": "reading", "text": "Reading your documents…"})
        with self._conversation():
            self.session.compile(self._request(), log, history=self._history_in_scope(), fresh=self._fresh_documents(),
                                 context=self._focus_context(), todo=self._todo_lines())

    def _history_in_scope(self) -> List[str]:
        """The changes the copies in scope carry, one line each, in the order they were made: a change every copy
        carries reads as it is; one that some copies got and others did not (a run on part of the batch) says which,
        so the model never plans from a state a document is not in. A document with no copy yet (an original among
        the copies, a set-aside document) carries nothing; the compiler tells those apart itself (``fresh``)."""
        docs = [str(a.path) for a in self.attachments if self._in_scope(a) and str(a.path) in self.working]
        lines: List[str] = []
        for summary in dict.fromkeys(self.history):
            carrying = [d for d in docs if summary in self.applied.get(d, [])]
            if not carrying:
                continue
            if len(carrying) == len(docs):
                lines.append(summary)
                continue
            missing = [Path(d).name for d in docs if d not in carrying]
            lines.append(f"{summary} (not in {', '.join(missing)})" if len(missing) <= len(carrying) else
                         f"{summary} (only in {', '.join(Path(d).name for d in carrying)})")
        return lines

    def _focus_context(self) -> str:
        """What the model is told about a pass over the to-do list: why these documents are planned alone (each one's
        reason for being on the list), and the plan the user saw (the changes the other copies got). The app's
        account, never the user's words."""
        if not self.focus:
            return ""
        seen = self.focus.get("seen", [])
        plan = "\nTHE PLAN THEY SAW:\n" + "\n".join(f"- {s}" for s in seen) if seen else ""
        why = self.focus.get("why") or {}
        if all((why.get(d) or {}).get("why", WHY_HELD) == WHY_HELD for d in self.focus["documents"]):
            return ("SET ASIDE AFTER A PREVIEW: the user looked at the before/after pages of the plan below for these "
                    "documents and said they need more work. Their request stands; their latest words say what should "
                    "be different. Plan for these documents only." + plan)
        lines = [f"- {Path(d).name}: {(why.get(d) or {}).get('reason') or HELD_REASON}"
                 for d in self.focus["documents"]]
        return ("FROM THE USER'S TO-DO LIST: these documents waited there and the user is working on them now. The "
                "request below was the one under way when they went on the list; it stands unless the user's latest "
                "words say otherwise. The set-aside is done: do not set them aside again unless the user asks. Plan for "
                "these documents only. Why each one was on the list:\n" + "\n".join(lines) + plan)

    def _todo_lines(self) -> List[str]:
        """The to-do list as the model is told it at the start of a request: one line per waiting document."""
        return [f"{entry['name']}: {entry.get('reason') or HELD_REASON}" for entry in self.todo.values()]

    # ---- continue on the result ----
    def _advance_generation(self) -> None:
        """The delivered copies become the working set: the next request builds on them, the originals stay as they
        are, and the model is told what was already done. The conversation that led here is over (its files are
        not the next request's)."""
        moved: List[str] = []                                     # the originals whose copy this run delivered
        for original, output in self.results.items():
            if Path(output).is_file():
                key, copy_path = str(Path(original)), str(Path(output).resolve())
                if self.working.get(key) != copy_path:
                    moved.append(key)
                self.working[key] = copy_path
        if not moved:
            return
        done = [c["summary"] for g in self._groups() for c in g["changes"]] or self.decisions[:1]
        self.history.extend(done)
        for key in moved:
            self.applied.setdefault(key, []).extend(done)         # these copies carry the change; no other copy does
        self.generation += 1
        self.decisions = []
        self.focus = None                                         # a pass over set-aside documents ends with its copies
        self._sync()
        self.session._set_outcome()
        self.session.end_conversation()

    def _fresh_documents(self) -> List[str]:
        """Documents attached after the last run: originals among working copies, carrying none of the changes the
        history lists (the model is told; the user was told when they attached them)."""
        if not self.working:
            return []
        return [a.path.name for a in self.attachments if self._in_scope(a) and str(a.path) not in self.working]

    def start_over(self) -> None:
        """Back to the attached originals: the working copies are set aside (the files stay where they are), the
        plan is void, and the next request starts from the originals."""
        if not self.working and not self.history and not self.todo and not self.focus:
            return
        self.working, self.generation, self.history, self.decisions = {}, 0, [], []
        self.results = {}
        self.applied, self.todo, self.focus, self._binned = {}, {}, None, []
        self._sync()
        self._invalidate()
        self.stage = "empty"
        self.say("Starting over from the originals. Tell me what you'd like to change.")
        self.save()

    def _target_question(self):
        if self.session.recovery is None:
            return None
        for record in self.session.recovery.question_records:
            if record.get("status") != "open" or record.get("kind") != "goal_binding":
                continue
            resolution = record.get("resolution", {})
            for ident, effect in resolution.get("effects", {None: resolution}).items():
                if effect.get("status") != "needs_input" or not effect.get("candidates"):
                    continue
                candidates = effect["candidates"]
                return {"question_id": record["id"], "effect_id": ident,
                        "candidates": candidates,
                        "options": [f"Paragraph {c['paragraph_index'] + 1} in "
                                    f"{review_display.readable(c.get('scope', 'document'))}: {c.get('text', '')}"
                                    for c in candidates],
                        "text": f"Which passage(s) in {Path(record['files'][0]).name} should change? "
                                "Reply with the number, or numbers separated by commas."}
        return None

    def perform(self, action, *, text="", token="", choice_id="", chip=False, hold=(), wording=None,
                log=lambda _: None, events=None):
        """Worker entry point. One ledger covers every nested model call and every turn. ``events`` is the window's
        live channel (docs/WEB_API.md: status, activity, text_delta, thinking_delta, message, plan, samples, results,
        question, cost); without one the job runs silently and the view says everything at the end. ``hold`` (the
        primary action) names the previewed documents the user set aside: they go on the to-do list, the others run."""
        self.problem = ""
        self._events = events
        self.save(in_progress=True)

        def checkpointed_log(message):
            self.save(in_progress=True)
            status = STATUS_FOR_LOG.get(str(message))
            if status:
                self._emit({"type": "status", "kind": status[0], "text": status[1]})
            log(message)
        try:
            with llm.budget_scope(self._budget), llm.stream_observer_scope(self._on_stream):
                if action == "send":
                    self._send(text, checkpointed_log, chip=chip)
                elif action == "choose":
                    self._choose(choice_id, checkpointed_log, wording=wording)
                elif action == "primary":
                    self._primary(token, checkpointed_log, hold=hold)
                else:
                    raise ValueError("Unknown task action")
        except llm.BudgetExceeded:
            self.stage = "budget"
        except Exception:
            # Invalidate the exposed action immediately, but retain evidence for diagnostics/replanning.
            self.stage = "question" if self.stage == "question" else "resume" if self.decisions else "empty"
            self._revision += 1
            raise
        finally:
            if self._budget.exhausted_reason and self.stage not in {"done", "preview"}:
                self.stage = "budget"
            self._emit({"type": "cost", "cost": self._cost()})
            self._events = None
            self._talking = False
            self.save()
        return self.view()

    def _cost(self) -> Dict[str, Any]:
        cost = self._budget.spent_usd
        text = ("an amount the price table cannot estimate" if cost is None else
                "less than $0.01" if cost < 0.005 else f"about ${cost:.2f}")
        # ``fast``: the next AI step will ask for fast mode and be priced at the fast table (llm.FAST_PRICES) — the
        # setting is on, the model is served fast, and Anthropic has not refused fast mode for this key
        return {"usd": cost, "text": text, "calls": self._budget.calls, "fast": llm.fast_for(llm.DEFAULT_MODEL)}

    def _ready_to_talk(self):
        self.session.files(outcome=False)  # reject missing attachments before consuming an answer
        if not self.session.claude_ready():
            raise ValueError("Connect your Anthropic API key in Settings to start the conversation.")

    def _send(self, text, log, chip=False):
        """The user's message. ``chip``: the text is one of the model's suggested replies, tapped — the user's
        decision, carried to the model as their reply, but never approved wording (the words are Claude's)."""
        text = text.strip()
        if not text:
            return
        if text.casefold() == START_OVER.casefold():
            self.say(text, "user")
            self.start_over()
            return
        if text.casefold() == WORK_TODO.casefold():
            self.say(text, "user")
            if self.todo:
                self.start_todo()
            else:
                self.say("Nothing is set aside right now; every document is in the plan.")
            return
        if text.casefold() == SET_ASIDE_REST.casefold():
            self.say(text, "user")
            self.set_rest_aside()
            return
        if self.todo and not self.focus and not self.documents():
            self.say(text, "user")
            self.say(f"Every document is on your to-do list. Say “{WORK_TODO}”, or put one back, to work on it.")
            return
        self._ready_to_talk()
        question = self._target_question()
        if question:
            picks = self._numbers(text, len(question["candidates"]))
            if picks:
                ids = [question["candidates"][i]["candidate_id"] for i in picks]
                answer = {"question_id": question["question_id"], "candidate_ids": ids, "answer": text}
                if question["effect_id"] is not None:
                    answer["effect_id"] = question["effect_id"]
                self.session.resolve_remaining(log, request=answer, options=self._recovery_options())
                self.say(text, "user")
                self._revision += 1
                self._settle(log)
                return
        choices = self._choices()
        picks = self._numbers(text, len(choices))
        if len(picks) == 1 and choices:
            self.say(text, "user")
            self._take(choices[picks[0]], log)
        else:
            self.say(text, "user")
            self.decisions.append(text)
            if not chip:
                self.word_sources.append(text)
            self.save(in_progress=True)
            s = self.session
            if s.conversation is not None and s.conversation_matches(s.conversation.request):
                with self._conversation():
                    s.reply(text, render=self.render, log=log, approve=not chip)
            elif s.recovery is not None and any(f.status != "READY" for f in s.recovery.files):
                s.resolve_remaining(log, request=text, options=self._recovery_options())
            else:
                self._compile(log)
        self._settle(log)

    def _choose(self, choice_id, log, wording=None):
        """A picked choice; ``wording``: the user's edit of its one wording (``conversation.edited_wording``), checked
        before anything is said or kept."""
        from .conversation import edited_wording
        self._ready_to_talk()
        chosen = next((c for c in self._choices() if c.id == choice_id), None)
        if chosen is None:
            raise ValueError("That choice is no longer available. Review the updated plan first.")
        edited = edited_wording(chosen, wording)
        self.say(f"Chose: {chosen.label}" + (f" — “{edited}”" if edited is not None else ""), "user")
        self._take(chosen, log, edited=edited)
        self._settle(log)

    def _take(self, chosen, log, edited=None):
        """A choice the user picked: its label is a decision, its exact words are approved wording — or, ``edited``,
        the user's own edit of them, and the draft is not."""
        words = [edited] if edited is not None else list(chosen.words)
        self.decisions.append(chosen.label)
        self.decisions.extend(words)
        self.word_sources.extend(words)
        self.save(in_progress=True)
        with self._conversation():
            self.session.reply(choice_id=chosen.id, render=self.render, log=log, wording=edited)

    @staticmethod
    def _numbers(text, count):
        if not re.fullmatch(r"\s*\d+(?:\s*[, ]\s*\d+)*\s*", text):
            return []
        values = list(dict.fromkeys(int(v) - 1 for v in re.findall(r"\d+", text)))
        return values if all(0 <= v < count for v in values) else []

    def _choices(self):
        return list(self.session.outcome().choices)

    def _collect_conversation(self):
        c = self.session.conversation
        if c is None:
            return
        if c.id != self._conversation_id:
            self._conversation_id, self._seen, self._replied = c.id, 0, None
        for entry in c.entries[self._seen:]:
            # App notes carry implementation telemetry; the coordinator renders its own state instead.
            if entry.role != "claude":
                continue
            self._replied = None
            if c.recovery_active or any(choice.get("kind") in {"custom_transform", "recovery_plan", "recovery_target"}
                                        for choice in entry.choices):
                continue
            # Questions and choices are shown once, in the clarification card, never twice: the chat says Claude's
            # words alone (its own "• " lines included; a question of several lines stays whole in the card).
            self.say(entry.message, "assistant")
            self._replied = entry.message
        self._seen = len(c.entries)

    def _settle(self, log):
        """Take every internal step the engine needs (prepare a custom method, carry out a recovery plan, make the
        preview) until the user is needed, then set the stage from the one decision table."""
        s = self.session
        self._collect_conversation()
        self._take_set_aside()
        outcome = s.outcome()
        for _ in range(AUTO_STEPS):
            if outcome.kind != "auto":
                break
            if outcome.needs_preview:
                self._emit({"type": "plan", "plan": self._plan_view()})    # the plan shows before any page renders
                log("Preparing and checking the preview…")
                s.make_preview(self.render, log,
                               on_preview=lambda: self._emit({"type": "samples", **self._samples_view()}))
                self._emit({"type": "samples", **self._samples_view()})     # again with Claude's visual findings
            else:
                with self._conversation():
                    s.reply(choice_id=outcome.auto_choice, render=self.render, log=log)
            self._collect_conversation()
            self._take_set_aside()
            outcome = s.outcome()
        self._revision += 1
        self.stage = {"question": "question", "preview": "preview", "resume": "resume", "auto": "question",
                      "empty": "resume" if self.decisions else "empty"}[outcome.kind]
        if self._budget.exhausted_reason and self.stage == "question":
            self.stage = "budget"
        if self.stage == "question":
            self._emit({"type": "question", "questions": list(outcome.questions),
                        "choices": [{"id": c.id, "label": c.label, "words": list(c.words), "detail": c.detail,
                                     "editable": getattr(c, "editable", False)}
                                    for c in outcome.choices], "chips": list(outcome.chips)})

    def _recovery_options(self):
        from .recovery import RecoveryOptions
        return RecoveryOptions(max_cost_usd=None, max_calls=self._budget.max_calls, max_attempts=RECOVERY_ATTEMPTS,
                               max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=None,
                               effort=RECOVERY_EFFORT, render=self.render, visual_review=self.session.visual_check)

    def action_token(self):
        s = self.session
        outcome = s.outcome()
        preview = outcome.preview
        visual = getattr(preview, "review", None)
        return _fingerprint({"revision": self._revision, "stage": self.stage,
                             "documents": s.documents, "template": s.template, "inputs": s.inputs, "spec": s.spec,
                             "todo": sorted(self.todo), "focus": sorted(self.focus["documents"]) if self.focus else None,
                             "left_out": [str(p) for p in s.left_out],
                             "preview": outcome.token,
                             "visual_findings": visual.to_dict() if visual else None,
                             "warnings": getattr(preview, "warnings", [])})

    def _held(self, hold) -> List[Path]:
        """The previewed documents the user set aside, as the files they are now; a path that is not one of the
        documents in the plan means the page is out of date, and is refused, never guessed."""
        if not hold:
            return []
        files = self.session.planned_files()
        held: List[Path] = []
        for value in hold:
            path = Path(str(value)).resolve()
            if path not in files:
                raise ValueError("The documents or plan changed. Review the updated plan first.")
            if path not in held:
                held.append(path)
        return held

    def _set_aside(self, current: Path, seen: List[str], why: str = WHY_HELD, reason: str = HELD_REASON) -> str:
        """A document onto the to-do list, keyed by its attached original, with the decisions it was set aside from,
        the plan the user looked at (``seen``), why (a previewed document the user marked as needing more work, one
        the user asked to set aside, one the plan refused, one whose copy did not pass) and the sentence that says so,
        until the user works on it (``start_todo``) or puts it back (``put_back``). Returns the original's key."""
        origin = str(self.origin_of(current))
        self.todo[origin] = {"name": Path(origin).name, "decisions": list(self.decisions), "seen": list(seen),
                             "why": why, "reason": reason}
        return origin

    def _take_set_aside(self) -> None:
        """The documents the user asked the assistant to set aside during the last turn (``Conversation.set_aside``)
        go on the to-do list at once: out of this request and every later one, the conversation going on about the
        others. A pass over the to-do list whose every document is set aside again is over; when nothing is left in
        scope the request ends with them."""
        c = self.session.conversation
        if c is None or not getattr(c, "set_aside", None):
            return
        done = self._parked[1] if self._parked[0] == c.id else 0
        fresh, self._parked = c.set_aside[done:], (c.id, len(c.set_aside))
        taken: List[str] = []
        for path in fresh:
            origin = str(self.origin_of(path))
            if origin in self.todo or not any(str(a.path) == origin for a in self.attachments):
                continue
            given = c.set_aside_reasons.get(path, "")
            reason = ASKED_REASON[:-1] + (f": {given.rstrip('.')}." if given else ".")
            self._set_aside(Path(path), [], why=WHY_ASKED, reason=reason)
            taken.append(origin)
            if self.focus and origin in self.focus["documents"]:
                self.focus["documents"] = [d for d in self.focus["documents"] if d != origin]
        if not taken:
            return
        names = ", ".join(Path(o).name for o in taken)
        several = len(taken) > 1
        ended = ""
        if self.focus is not None and not self.focus["documents"]:
            self.focus, self.decisions = None, []          # the pass is over: its documents are back on the list
            ended = " The pass over your to-do list is over; the other documents are back as they are."
        self._sync()
        if not self.documents():
            self.decisions = []                            # nothing left in scope: the request ends with them
        if not self.decisions:
            self.session._set_outcome()
            self.session.end_conversation()
        self.say(f"Set aside for later: {names}. {'They are' if several else 'It is'} on your to-do list, out of every "
                 f"plan until you work on {'them' if several else 'it'} or put {'them' if several else 'it'} back; "
                 f"nothing in {'them' if several else 'it'} changes." + ended)
        self.save(in_progress=True)

    def put_back(self, path) -> None:
        """A document off the to-do list, without a pass over it: it is among the documents the next request works
        on again, as it is (its copy, if it has one). During a pass it joins the others when the pass ends."""
        key = str(Path(path))
        if key not in self.todo:
            raise ValueError("That document is not on your to-do list.")
        before = self.documents()
        entry = self.todo.pop(key)
        self._sync()
        if self.focus:
            self.say(f"{entry['name']} is off your to-do list; it joins the other documents when this pass ends.")
        else:
            self.say(f"{entry['name']} is off your to-do list and back among the documents your next request works on.")
        if self.documents() != before:
            self._invalidate()
            if self.decisions:
                self.say("The documents changed. I’ll check them against your request again.")
        self.save()

    def start_todo(self) -> None:
        """The pass over the to-do list, one request at a time: the documents set aside from the same decisions (the
        oldest first) become the only documents in scope, those decisions are the request again, and the model is
        told what each copy carries, why each was set aside and what the user saw. Documents set aside from another
        request stay on the list for the next pass. The other documents' copies stay as they are and come back when
        this pass delivers its copies."""
        if not self.todo:
            return
        decisions = list(next(iter(self.todo.values())).get("decisions") or [])
        group = {k: v for k, v in self.todo.items() if list(v.get("decisions") or []) == decisions}
        rest = {k: v for k, v in self.todo.items() if k not in group}
        entries = list(group.values())
        self.focus = {"documents": list(group),
                      "seen": list(dict.fromkeys(s for e in entries for s in e.get("seen", []))),
                      "why": {k: {"why": v.get("why", WHY_HELD), "reason": v.get("reason") or HELD_REASON}
                              for k, v in group.items()}}
        self.decisions = decisions
        self.todo = rest
        self._sync()
        self._invalidate()
        names = ", ".join(e["name"] for e in entries)
        several = len(entries) > 1
        waiting = ", ".join(e["name"] for e in rest.values())
        self.say((f"Working on the {len(entries)} documents from your to-do list: {names}. " if several else
                  f"Working on {names}, from your to-do list. ") +
                 f"Tell me what should be different for {'them' if several else 'it'}, or continue to plan "
                 f"{'them' if several else 'it'} again as before. The other copies stay as they are." +
                 (f" {waiting}: set aside from a different request; {'they wait' if len(rest) > 1 else 'it waits'} "
                  f"for the next pass." if rest else ""))
        self.save()

    def _offer_todo(self, binned: Sequence[str] = ()) -> None:
        """Say what waits on the to-do list once a request ends; ``binned``: the documents of this request that could
        not take the change (the plan refused them, or their copy did not pass) and went on the list with the reason."""
        if not self.todo:
            return
        binned = [k for k in binned if k in self.todo]
        if binned:
            several = len(binned) > 1
            self.say(f"{', '.join(self.todo[k]['name'] for k in binned)} could not take this change, so "
                     f"{'they wait' if several else 'it waits'} on your to-do list with the reason.")
        names = ", ".join(e["name"] for e in self.todo.values())
        several = len(self.todo) > 1
        self.say(f"On your to-do list: {names}. When you’re ready to work on {'them' if several else 'it'}, say "
                 f"“{WORK_TODO}” and tell me what should be different.")

    def _primary(self, token, log, hold=()):
        if token != self.action_token():
            raise ValueError("The documents or plan changed. Review the updated plan first.")
        s = self.session
        if self.stage == "budget":
            # Extend the same ledger; do not reset accumulated usage or nested limits.
            self._budget.max_calls = max(self._budget.max_calls, self._budget.calls) + STEPS_PER_PAUSE
            self._budget.exhausted_reason = None
            self.stage = "resume"
            self.say(f"Continuing for up to {STEPS_PER_PAUSE} more AI steps.")
            self._primary(self.action_token(), log, hold=hold)
            return
        if self.stage == "resume":
            self._resume(log)
            return
        outcome = s.outcome()
        partial = self.stage == "question" and outcome.kind == "question" and outcome.partial
        if self.stage != "preview" and not partial:
            raise ValueError("Answer the open question before continuing.")
        if outcome.kind != "preview" and not partial:
            raise ValueError("The documents or plan changed. Review the updated plan first.")
        held = self._held(hold)
        if held and s.recovery is not None:
            raise ValueError("A document cannot be set aside while files are being checked again; save the ready "
                             "copies first.")
        # the files the preview refused never run (Session._without_refused): once the others run they go on the to-do
        # list with the plan's reason, to come back to (the session keeps them until the request ends: the approval
        # was taken over them)
        refused = self._refused_now(held) if s.recovery is None else []
        remaining = None
        if held:
            # the files the preview refused never run (Session._without_refused): a user who sets aside every
            # previewed document has set aside everything there is to create
            remaining = [f for f in s.runnable_files() if f not in held]
            seen = [c["summary"] for g in self._groups() for c in g["changes"]]   # the plan the user looked at
            for path in held:
                self._set_aside(path, seen)
            if not remaining:
                # every previewed document set aside: nothing to create; the pass over them begins at once
                for path, seen_by, reason in refused:
                    self._set_aside(path, seen_by, why=WHY_REFUSED, reason=reason)
                self.say("You set every document aside, so no copy was created. Tell me what should be different, "
                         "and I’ll plan them again.")
                self.start_todo()
                return
        if outcome.blocking:
            # A blocking visual finding is a separate, explicitly labelled decision on the displayed preview.
            s.acknowledge_review(outcome.token)
        log("Creating and checking your edited copies…")
        s.run(False, self.render, log, only=remaining)
        self._binned += [self._set_aside(path, seen_by, why=WHY_REFUSED, reason=reason)
                         for path, seen_by, reason in refused]
        self._collect_results()
        self._emit({"type": "results", "results": self._results_view()})
        if s.last_batch is not None and any(f.status != "PASS" for f in s.last_batch.files):
            log("Checking the files that still need work…")
            conversation = s.conversation
            s.resolve_remaining(log, options=self._recovery_options())
            if s.recovery is not None:
                if conversation is not None:
                    from .continuous_recovery import result_turn
                    conversation.recovery_active = True
                    s.conversation = conversation
                    s.turn = result_turn(conversation, s.recovery)
                self._settle(log)
                return
            # nothing a plan can recover: a document no layout covered stays as it was, named under "Left untouched",
            # and goes on the to-do list with why
            self._binned += self._bin_failed(s.last_batch.files)
        if s.last_trials is not None and any(not f.passed for f in s.last_trials):
            log("Checking the files that still need work…")
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        if s.recovery is not None and len(s.recovery.ready) < len(s.recovery.files):
            self.stage = "question"
            self._revision += 1
            self.say("The ready copies are saved. The remaining documents still need attention.")
            return
        if s.pending_layout_recovery:
            # a plan per layout: the previous layout's copies are saved; the next layout's documents are checked now
            log("Checking the next layout's documents that still need work…")
            s.resolve_next_layout(log, options=self._recovery_options())
            self._settle(log)
            return
        self.stage = "done"
        self._revision += 1
        n = len(self.results)
        self.say(f"{n} document {'copy is' if n == 1 else 'copies are'} ready. You can open them, describe another "
                 f"change to make on top of them, or start over from the originals.")
        self._advance_generation()
        binned, self._binned = self._binned, []
        self._offer_todo(binned)

    def _refused_now(self, held: Sequence[Path] = ()) -> List[tuple]:
        """At Create: the documents of the displayed plan its preview refused while another planned, each with the
        plan the user saw and the plan's reason, ``(path, seen, reason)``: they go on the to-do list once the others
        run. The ones the user set aside with the red X are theirs already."""
        s = self.session
        planned, runnable = s.planned_files(), s.runnable_files()
        refused = [f for f in planned if f not in runnable and f not in held]
        if not refused:
            return []
        seen = [c["summary"] for g in self._groups() for c in g["changes"]]
        reasons = {Path(item.source).resolve(): (item.exception or {}).get("reason", "")
                   for item in getattr(getattr(s.preview, "batch_plan", None), "exceptions", None) or []}
        out = []
        for f in refused:
            why = str(reasons.get(Path(f).resolve(), "")).strip().rstrip(".")
            out.append((f, seen, "The plan could not be applied to it" + (f": {why}." if why else ".")))
        return out

    def _bin_failed(self, files) -> List[str]:
        """Run results that did not pass and that nothing will check again: on the to-do list with why."""
        seen = [c["summary"] for g in self._groups() for c in g["changes"]]
        out: List[str] = []
        for item in files:
            if item.status == "PASS":
                continue
            exc = getattr(item, "exception", None) or {}
            why = exc.get("reason") or getattr(item, "reason", "") or "it did not pass the independent checks"
            out.append(self._set_aside(Path(item.source), seen, why=WHY_FAILED,
                                       reason=f"Its copy could not be made: {str(why).rstrip('.')}."))
        return out

    def set_rest_aside(self) -> None:
        """During recovery, at the user's word (``SET_ASIDE_REST``): the documents it has not fixed go on the to-do
        list with the reason, and so do those of the layouts still waiting for theirs; the copies already saved are
        the working set and the request ends. Ready copies not saved yet are saved first (their own button)."""
        s = self.session
        r = s.recovery
        if r is None or all(f.status == "READY" for f in r.files):
            self.say("Nothing is waiting on a check right now.")
            return
        if any(f.status == "READY" and not f.delivered_to for f in r.files):
            self.say("Save the ready copies first; then the rest can be set aside for later.")
            return
        binned = [self._set_aside(Path(f.source), [], why=WHY_FAILED,
                                  reason=f"It still needed work when you set it aside: {str(f.reason).rstrip('.')}.")
                  for f in r.files if f.status != "READY"]
        from .batch import load_set_aside
        for folder in s.pending_layout_recovery:
            try:
                items = load_set_aside(folder).get("files") or []
            except (OSError, ValueError):
                items = []
            for item in items:
                if item.get("source"):
                    reason = item.get("reason") or "it did not pass the independent checks"
                    binned.append(self._set_aside(Path(item["source"]), [], why=WHY_FAILED,
                                                  reason=f"Its copy could not be made: {str(reason).rstrip('.')}."))
        self._advance_generation()
        # the request ends here, whether or not a copy was saved: nothing of it is left to check
        self.decisions, self.focus, self._binned = [], None, []
        self._resume_recovery, self._resume_layouts = None, []
        s.pending_layout_recovery = []
        s._set_outcome()
        s.end_conversation()
        self._sync()
        self._revision += 1
        self.stage = "done" if self.results else "empty"
        names = ", ".join(self.todo[k]["name"] for k in dict.fromkeys(binned))
        self.say(f"Set aside for later: {names}. The copies already saved stay as they are.")
        self._offer_todo()

    def _resume(self, log):
        s = self.session
        if s.pending_custom_transform is not None:
            s.continue_custom_transform(log, self.render)
            self._settle(log)
            return
        checkpoint = self._resume_recovery or s.recovery_checkpoint
        if checkpoint:
            # Loading validates saved evidence and source pins. Never discard a pending checkpoint (including a
            # missing/changed one) by recompiling the request.
            s.load_recovery(checkpoint)
            self._resume_recovery = None
            s.pending_layout_recovery = [Path(x) for x in self._resume_layouts]
            self._resume_layouts = []
            groups = s.recovery.session_state.get("groups", {}).values()
            if any(group.get("status") in {"active", "paused"} for group in groups):
                s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        self._resume_recovery = None
        if s.recovery is not None:
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        c = s.conversation
        if c is not None and c.resumable() and s.conversation_matches(c.request):
            # A pause, a stop or a failed call: the conversation goes on with what it had investigated.
            with self._conversation():
                s.resume_conversation(log)
        else:
            self._compile(log)
        self._settle(log)

    def _collect_results(self):
        """The copies a run delivered, keyed by the ATTACHED original (a run on working copies reads the copies, but
        the user thinks in their own files)."""
        s = self.session
        result = s.last_recovery or s.last_batch
        if result is not None:
            for item in result.files:
                origin = str(self.origin_of(item.source))
                if item.delivered_to:
                    self.results[origin] = str(item.delivered_to)
                elif (getattr(item, "noop", False) and item.status == "PASS" and item.output
                      and Path(item.output).is_file()):
                    # The engine verifies and saves identical copies too, but intentionally leaves delivered_to
                    # empty to distinguish them from changed documents in its reports.
                    self.results[origin] = str(item.output)
        for item in s.last_trials or []:
            if item.passed and item.output:
                self.results[str(self.origin_of(item.source))] = str(item.output)

    # ---- what the window shows ----
    def _groups(self):
        """What will change, grouped by layout: one group for an ordinary spec, one per layout for a plan."""
        from .layouts import is_layout_plan, route
        s = self.session
        preview = s.outcome().preview
        contract = s.recovery.key.get("adaptive_contract") if s.recovery else None
        count = len(s.recovery.files) if s.recovery else len(s.documents)
        if contract:
            return [{"id": "", "label": "", "count": count, "files": [], "changes": review_display.effects(contract),
                     "assumptions": []}]
        if is_layout_plan(s.spec):
            routing = getattr(preview, "routing", None)
            if routing is None:
                try:
                    routing = route(s.documents, s.spec)
                except Exception:  # noqa: BLE001 — a routing that cannot be read shows counts of zero
                    routing = None
            return review_display.layout_changes(s.spec, routing)
        changes = review_display.spec_changes(s.spec)
        assumptions = review_display.spec_assumptions(s.spec)
        if not changes and s.generated is not None:
            changes = [{"title": "Requested changes", "summary": s.generated.proposal,
                        "details": [s.generated.request, "The sample copies passed independent verification."]}]
        if not changes:
            return []
        return [{"id": "", "label": "", "count": count, "files": [], "changes": changes, "assumptions": assumptions}]

    def _plan_view(self) -> Dict[str, Any]:
        """The plan as the window shows it: groups of change sentences with their assumptions, the passages each
        document contributes, the documents left untouched, and how many ordered steps the engine composed."""
        from .editspec import is_chain
        from .layouts import is_layout_plan
        s = self.session
        groups = self._groups()
        if is_layout_plan(s.spec):
            stages = max([len(layout["spec"].get("stages") or []) if is_chain(layout["spec"]) else 1
                          for layout in s.spec.get("layouts") or []] or [1])
        else:
            stages = len(s.spec.get("stages") or []) if is_chain(s.spec) else 1
        set_aside = self._left_untouched(s.outcome().preview)
        return {"groups": groups,
                "targets": review_display.recovery_targets(s.recovery) if s.recovery else review_display.spec_bindings(s.spec),
                "set_aside": set_aside, "stages": stages if groups else 0}

    def _left_untouched(self, preview) -> List[Dict[str, str]]:
        """Left untouched beside a preview: the documents the user left out of the request (no plan covers them),
        then those the plan refused, each with its reason."""
        if preview is None:
            return []
        items = [{"name": Path(path).name, "reason": LEFT_OUT_REASON} for path in self.session.left_out]
        for item in getattr(getattr(preview, "batch_plan", None), "exceptions", []) or []:
            exc = item.exception or {}
            items.append({"name": Path(item.source).name, "reason": exc.get("reason", "")})
        return items

    def _delivered(self) -> List[Path]:
        """The documents whose copies a partial save during recovery already delivered: their pages and findings leave
        while the conversation goes on about the rest."""
        recovery = self.session.recovery
        return [Path(f.source) for f in recovery.files if f.delivered_to] if recovery is not None else []

    def _undelivered_samples(self, preview) -> list:
        delivered = {str(path) for path in self._delivered()}
        return [sv for sv in getattr(preview, "samples", []) or [] if str(sv.source) not in delivered]

    def _undelivered_findings(self, review) -> list:
        """The visual check's findings less those about a delivered document. A finding names its file by the label
        the check was shown (the name, or the trailing folders that tell two same-named files apart); one that names
        no file, or whose label also fits a document still in play, stays."""
        findings = list(getattr(review, "findings", []) or [])
        delivered = self._delivered()
        if not delivered:
            return findings
        pending = [Path(f.source) for f in self.session.recovery.files if not f.delivered_to]

        def names(label, path):
            return label == str(path) or ("/" + path.as_posix()).endswith("/" + label)
        return [f for f in findings
                if not (f.get("file") and any(names(f["file"], path) for path in delivered)
                        and not any(names(f["file"], path) for path in pending))]

    def _samples_view(self) -> Dict[str, Any]:
        preview = self.session.outcome().preview
        samples = self._undelivered_samples(preview)
        return {"samples": [{"source": str(sv.source), "name": Path(sv.source).name,
                             "images": [str(p) for p in sv.images if Path(p).is_file()],
                             "pages_before": sv.pages_before, "pages_after": sv.pages_after, "passed": sv.passed}
                            for sv in samples],
                "warnings": list(dict.fromkeys(getattr(preview, "warnings", []) or [])),
                "blocking": any(f.get("severity") == "block"
                                for f in self._undelivered_findings(getattr(preview, "review", None)))}

    def _results_view(self) -> List[Dict[str, Any]]:
        return [{"source": source, "output": output, "name": Path(output).name}
                for source, output in self.results.items()]

    def view(self):
        s = self.session
        outcome = s.outcome()
        question = self._target_question()
        choices = [] if question else outcome.choices
        questions: List[str] = []
        if question:
            questions.append(question["text"] + "\n" + "\n".join(
                f"{i}. {value}" for i, value in enumerate(question["options"], 1)))
        elif self.stage == "question":
            questions.extend(outcome.questions)
            if not questions and not choices:
                # A reply with nothing to pick (plain words, a failure) is the chat's, said there once; the card
                # carries it only when the chat did not say that reply (a recovery turn's), whatever an older
                # bubble said.
                message = (outcome.message or "").strip()
                if not message:
                    questions.append("Tell me what you’d like to change or clarify.")
                elif message != self._replied:
                    questions.append(message)
        preview = outcome.preview
        # The pages and their findings show beside the plan they belong to, while it waits for the user; a run that
        # finished takes them along.
        pages = preview if self.stage in {"preview", "question"} else None
        samples = self._undelivered_samples(pages)
        warnings = list(getattr(pages, "warnings", []) or [])
        from .settings import output_root_warnings
        warnings.extend(output_root_warnings(s.out_root))
        visual = getattr(pages, "review", None)
        findings = self._undelivered_findings(visual)
        blocking = any(f.get("severity") == "block" for f in findings)
        if visual is not None:
            if visual.skipped:
                warnings.append("Automated page review was unavailable: " + visual.skipped)
            for finding in findings:
                location = " · ".join(str(v) for v in (finding.get("file"),
                                           f"page {finding['page']}" if finding.get("page") else None) if v)
                warnings.append((location + ": " if location else "") + finding.get("text", ""))
        if samples and not any(sample.images for sample in samples):
            warnings.append("Page appearance was not checked: a rendered preview is unavailable. "
                            "Check the edited copies in Word before using them.")
        set_aside = self._left_untouched(preview)
        labels = {"preview": "Create edited copies", "resume": "Continue", "budget": "Continue"}
        if blocking and self.stage == "preview":
            labels["preview"] = "I reviewed the flagged pages — create copies"
        if self.stage == "question" and outcome.partial:
            ready = len(s.recovery.ready)
            labels["question"] = f"Save {ready} ready {'copy' if ready == 1 else 'copies'}"
        groups = self._groups() if self.stage in {"preview", "question"} else []
        from .editspec import is_chain
        from .layouts import is_layout_plan
        if is_layout_plan(s.spec):
            stages = max([len(layout["spec"].get("stages") or []) if is_chain(layout["spec"]) else 1
                          for layout in s.spec.get("layouts") or []] or [1])
        else:
            stages = len(s.spec.get("stages") or []) if is_chain(s.spec) else 1
        chips = list(outcome.chips) if not question else []
        r = s.recovery
        if (self.stage == "question" and r is not None and any(f.status != "READY" for f in r.files)
                and all(f.delivered_to for f in r.ready) and SET_ASIDE_REST not in chips):
            chips.append(SET_ASIDE_REST)        # what recovery has not fixed can wait on the to-do list
        resting = self.stage in {"done", "empty", "resume"}
        if self.todo and not self.focus and resting and WORK_TODO not in chips:
            chips.insert(0, WORK_TODO)
        can_start_over = bool(self.working or self.history or self.todo or self.focus)
        if can_start_over and resting and START_OVER not in chips:
            chips.append(START_OVER)
        return {"stage": self.stage, "messages": list(self.messages),
                "stages": stages if groups else 0,
                "chips": chips,
                "working": {"generation": self.generation, "from_originals": not self.working,
                            "can_start_over": can_start_over},
                "todo": [{"path": path, "name": entry["name"], "why": entry.get("why", WHY_HELD),
                          "reason": entry.get("reason") or HELD_REASON} for path, entry in self.todo.items()],
                "focus": list(self.focus["documents"]) if self.focus else [],
                "can_hold": self.stage == "preview" and s.recovery is None,
                "attachments": [{**a.to_dict(), "current": str(self.current_of(a.path))} for a in self.attachments],
                "groups": groups,
                "effects": [c for g in groups for c in g["changes"]],
                "assumptions": list(dict.fromkeys(a for g in groups for a in g["assumptions"])),
                "targets": review_display.recovery_targets(s.recovery) if s.recovery else review_display.spec_bindings(s.spec),
                "document_count": len(s.recovery.files) if s.recovery else len(s.documents),
                "questions": questions,
                "choices": [{"id": c.id, "label": c.label, "words": list(c.words), "detail": c.detail,
                             "editable": getattr(c, "editable", False)} for c in choices],
                "samples": samples, "set_aside": set_aside,
                "warnings": list(dict.fromkeys(warnings)), "results": dict(self.results),
                "action": labels.get(self.stage, ""), "token": self.action_token(),
                "budget_paused": self.stage == "budget", "steps_per_pause": STEPS_PER_PAUSE,
                "cost": self._budget.spent_usd,
                "blocking": blocking}

    # ---- the journal ----
    def archive(self) -> Optional[Path]:
        """Set the journal aside under ``conversations/`` beside it, kept for inspection and never reopened; returns
        where it went (None when there was none). The window starts every launch and every New conversation clean."""
        if not self.journal or not self.journal.exists():
            return None
        folder = self.journal.parent / "conversations"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{time.time_ns()}.json"
        self.journal.replace(target)
        return target

    def save(self, *, in_progress=False):
        if not self.journal:
            return
        atomic_json(self.journal, {
            "version": 2,
            "attachments": [a.to_dict() for a in self.attachments],
            "messages": self.messages, "decisions": self.decisions, "word_sources": self.word_sources,
            "results": self.results,
            "recovery": str(self.session.recovery.out_dir) if self.session.recovery else
                        str(self.session.recovery_checkpoint) if self.session.recovery_checkpoint else self._resume_recovery,
            "pending_layouts": [str(x) for x in self.session.pending_layout_recovery],
            "working": dict(self.working), "generation": self.generation, "history": list(self.history),
            "applied": self.applied, "todo": self.todo, "focus": self.focus,
            "budget": self._budget.to_dict(), "in_progress": in_progress})

    def _restore_todo(self, data) -> None:
        """The changes each copy carries, the to-do list and the pass over it, for the documents still attached; a
        malformed record is refused. A journal from before per-copy histories had every copy carry every change."""
        strings = lambda value: isinstance(value, list) and all(isinstance(x, str) for x in value)  # noqa: E731
        applied = data.get("applied")
        if applied is None:
            self.applied = {k: list(self.history) for k in self.working}
        elif isinstance(applied, dict) and all(isinstance(k, str) and strings(v) for k, v in applied.items()):
            self.applied = {k: list(v) for k, v in applied.items() if k in self.working}
        else:
            raise ValueError("The saved conversation could not be read.")
        todo = data.get("todo", {})
        focus = data.get("focus")
        text = lambda value: isinstance(value, str)  # noqa: E731
        why_ok = lambda v: text(v.get("why", WHY_HELD)) and text(v.get("reason", ""))  # noqa: E731
        if not (isinstance(todo, dict)
                and all(isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("name"), str)
                        and strings(v.get("decisions", [])) and strings(v.get("seen", [])) and why_ok(v)
                        for k, v in todo.items())):
            raise ValueError("The saved conversation could not be read.")
        if focus is not None and not (isinstance(focus, dict) and strings(focus.get("documents"))
                                      and strings(focus.get("seen", []))
                                      and isinstance(focus.get("why", {}), dict)
                                      and all(isinstance(k, str) and isinstance(v, dict) and why_ok(v)
                                              for k, v in focus.get("why", {}).items())):
            raise ValueError("The saved conversation could not be read.")
        known = {str(a.path) for a in self.attachments if not a.template and not a.reference}
        # a journal from before the to-do list said why holds only documents set aside with the red X
        self.todo = {k: {"name": v["name"], "decisions": list(v.get("decisions", [])), "seen": list(v.get("seen", [])),
                         "why": v.get("why", WHY_HELD), "reason": v.get("reason") or HELD_REASON}
                     for k, v in todo.items() if k in known}
        documents = [d for d in (focus or {}).get("documents", []) if d in known]
        self.focus = ({"documents": documents, "seen": list(focus.get("seen", [])),
                       "why": {k: {"why": v.get("why", WHY_HELD), "reason": v.get("reason") or HELD_REASON}
                               for k, v in focus.get("why", {}).items() if k in documents}}
                      if focus and documents else None)

    def restore(self):
        if not self.journal or not self.journal.is_file():
            return False
        data = json.loads(self.journal.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") not in (1, 2):
            raise ValueError("This saved conversation uses an unsupported version.")
        if not isinstance(data.get("decisions"), list) or not all(isinstance(x, str) for x in data["decisions"]):
            raise ValueError("The saved conversation could not be read.")
        if (not isinstance(data.get("messages"), list)
                or not all(isinstance(m, dict) and isinstance(m.get("text"), str) and
                           m.get("role") in {"app", "user", "assistant"} for m in data["messages"])
                or not isinstance(data.get("results"), dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in data["results"].items())
                or not isinstance(data.get("budget"), dict)):
            raise ValueError("The saved conversation could not be read.")
        if data.get("version") == 1:
            documents = data.get("documents")
            if not isinstance(documents, list) or not all(isinstance(p, str) for p in documents):
                raise ValueError("The saved conversation could not be read.")
            template = data.get("template")
            if template is not None and not isinstance(template, str):
                raise ValueError("The saved conversation could not be read.")
            entries = [{"path": p, "template": False} for p in documents]
            if template:
                entries.append({"path": template, "template": True})
        else:
            entries = data.get("attachments")
            if not isinstance(entries, list) or not all(isinstance(e, dict) and isinstance(e.get("path"), str)
                                                        for e in entries):
                raise ValueError("The saved conversation could not be read.")
        # Attachments are triaged again: a file may have changed, moved or been fixed since.
        self.attachments = []
        for entry in entries:
            path = Path(entry["path"])
            template, reference = bool(entry.get("template")), bool(entry.get("reference"))
            if path.is_file():
                self.attachments.append(triage_reference(path) if reference else triage(path, template=template))
            else:
                self.attachments.append(Attachment(path, template=template, reference=reference, state="refused",
                                                   reason=f"{path.name} is no longer at {path.parent}."))
        self._sync()
        self.messages = data.get("messages", [])
        self.decisions = data["decisions"]
        self.word_sources = data.get("word_sources", [])
        if not isinstance(self.word_sources, list) or not all(isinstance(x, str) for x in self.word_sources):
            raise ValueError("The saved wording approvals could not be read.")
        self.results = data.get("results", {})
        working = data.get("working", {})
        history = data.get("history", [])
        if (isinstance(working, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in working.items())
                and isinstance(history, list) and all(isinstance(h, str) for h in history)
                and all(Path(v).is_file() for v in working.values())):
            self.working, self.history = dict(working), list(history)
            self.generation = int(data.get("generation", 0) or 0) if type(data.get("generation", 0)) is int else 0
        else:
            # a copy is gone: the originals are the set again, the vanished copies are no longer results, and the
            # user is told before their next request builds on the wrong files
            lost = isinstance(working, dict) and bool(working)
            self.working, self.history, self.generation = {}, [], 0
            self.results = {k: v for k, v in self.results.items() if Path(v).is_file()}
            if lost:
                self.say("The edited copies from your last run could not be found where they were saved, so the next "
                         "change starts from your originals.")
        self._restore_todo(data)
        self._sync()
        self._resume_recovery = data.get("recovery")
        layouts = data.get("pending_layouts", [])
        if not isinstance(layouts, list) or not all(isinstance(x, str) for x in layouts):
            raise ValueError("The saved conversation could not be read.")
        self._resume_layouts = list(layouts)
        budget = data.get("budget", {})
        # A journal from a version with a spending allowance keeps its call count and spending, never its ceilings.
        max_calls = budget.get("max_calls", STEPS_PER_PAUSE)
        self._budget = self._new_budget(max_calls if type(max_calls) is int and max_calls > 0 else STEPS_PER_PAUSE)
        # Retain conservative accounting; never reset spending by reopening the app.
        self._budget.calls = int(budget.get("calls", 0))
        self._budget._reserved_tokens = int(budget.get("accounted_tokens", 0))
        cost = budget.get("estimated_cost_usd", 0)
        self._budget._reserved_cost_usd = float(cost or 0)
        exhausted = budget.get("exhausted_reason")
        self._budget.exhausted_reason = exhausted if exhausted and "call limit" in exhausted else None
        if data.get("in_progress") or cost is None:
            # The app closed while work ran: its last call may have cost more than was recorded; ask before going on.
            self._budget.exhausted_reason = "Previous work stopped before its final usage was recorded."
        self.stage = ("budget" if self._budget.exhausted_reason else "resume" if self.decisions else
                      "done" if self.results and self.generation else "empty")
        if self.decisions:
            self.say("Your conversation is restored. I’ll recheck the documents before making further changes.")
        elif self.generation:
            self.say("Your conversation is restored. The edited copies from your last run are what the next change "
                     "builds on; say “Start over from the originals” to go back to your files.")
        return True
