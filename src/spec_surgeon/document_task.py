"""One conversational document task, independent of the window.

The Session is a toolbox: compile, reply, preview, run, recover, each a verified engine step. This coordinator owns
the stages the user sees — empty, question, preview, done, budget, resume — and moves between them on the Session's
single decision table (``Session.outcome``), never by probing engine fields. It keeps the user's decisions together,
triages every attachment the moment it arrives, and persists a recoverable journal. Journals never restore delivery
approval or execute saved code.

Attachments are Word documents to edit, at most one template, and pictures or tables the user attached for the edit
to use (``reference``: a new logo, a sheet of project values); nothing beside them is discovered. Every model call
may write up to the model's maximum output (the conversation at ``CONVERSATION_EFFORT``, the custom-method writer at
``resources.CODEGEN_EFFORT``, recovery at ``RECOVERY_EFFORT``). The spending check is the user's (``spending_limit``,
Settings; None: no check): each request — a message, a pick, an approval, a button — gets its own ledger, and a call
that could take that request past the limit is not started; the work pauses with its progress saved until the user
says to go on, which allows the limit again. The window shows the estimated spending, and its Stop is the kill
switch: it cuts off the call that is running at once (``llm.cancellation_scope``). Continue after a pause, a stop or
a failed call picks the conversation up where it stopped.
"""
from __future__ import annotations

import hashlib
import contextlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .ui_feedback import failed_preview_checks, refusal_message

from . import llm, review_display
from .gui import Session
from .inputs import KINDS as REFERENCE_KINDS
from .recovery_state import atomic_json
from .resources import DEFAULT_MAX_CALLS, DEFAULT_MAX_OUTPUT_TOKENS
from .settings import spending_check

AUTO_STEPS = 6          # internal steps the coordinator takes on its own after one user action (bounded)
STAGES = ("empty", "question", "preview", "done", "budget", "resume")
# Why the work is paused (stage "budget"): the request reached the user's spending check; the price table cannot
# estimate the model that answers, so the check cannot be kept; or the app closed while work ran
PAUSES = ("limit", "unpriced", "interrupted")
INTERRUPTED = "Previous work stopped before its final usage was recorded."
# Safe: the buttons, no AI call; Default: the conversation; Obliterate: the conversation without the guardrails that
# protect a document's content (CLAUDE.md, Modes) — hidden unless Settings → Advanced shows it, chosen
# only after its warning, never restored at a launch
MODES = ("safe", "default", "obliterate")
LAUNCH_MODES = ("safe", "default")
OBLITERATE_ON = ("Obliterate mode is on. Claude may write wording and custom methods that change anything Word stores, "
                 "and a copy whose checks fail is delivered marked UNVERIFIED instead of being held back. Copies only; "
                 "your originals are never written. What was planned before is void.")
OBLITERATE_OFF = ("Obliterate mode is off: every placed word is yours again, custom methods are formatting only, and a "
                  "copy whose checks fail is held back. What was planned in Obliterate mode is void.")
REPLAN = " Continue plans your request again under these rules, or say what you want."
START_OVER = "Start over from the originals"   # the reply (a chip, a button) that drops the working copies
LEFT_OUT_REASON = "You asked to leave it out of this request; it stays as it is."
WORK_TODO = "Work on the documents I set aside"  # the reply (a chip, typed) that begins the pass over the to-do list
# the reply (a chip, typed) that ends a recovery by putting the documents it could not fix on the to-do list
SET_ASIDE_REST = "Set the rest aside for later"
# why a document is on the to-do list (``todo[...]["why"]``), and the sentence the window and the model are given
WHY_HELD, WHY_ASKED, WHY_REFUSED, WHY_FAILED = "held", "asked", "refused", "failed"
HELD_REASON = "You marked its preview as needing more work."
ASKED_REASON = "You asked to set it aside for later."
EARLIER_COPY = "in an earlier copy: "   # an UNVERIFIED mark a later copy builds on (``_mark_unverified``)
# Discard in Default and Obliterate (``discard_preview``): what the chat says once the preview, or the request still
# open, is withdrawn
DISCARDED = {"preview": "Preview discarded; nothing was created.",
             "request": "Request dropped; nothing was created."}
# said when the app takes a custom method on its own (``_collect_conversation``), Claude's proposal after it
CUSTOM_METHOD_WRITTEN = ("Claude is writing a one-off custom method for this and trying it on copies of your documents; "
                         "nothing is created until you have seen its preview and approved it.")
NO_PDF = "Its PDF could not be made"   # Save clean PDFs: a clean copy passed, its PDF did not (``_without_pdf``)
CONVERSATION_EFFORT = "medium"   # the model's own default: fewer tool rounds per turn than high, measured by the owner
RECOVERY_EFFORT = "high"         # the hard path keeps the depth
RECOVERY_ATTEMPTS = 8            # candidate attempts per problem group (docs/RECOVERY.md), on every recovery path
# A request Claude answers without a plan is finished with the answer (``_answered``), and one the user withdraws with
# Discard is finished with that (``_discard_request``, ``"discarded"``): the exchange is kept as context for the next
# request — the last EXCHANGES_KEPT of them, each with the first sentence of the answer, cut at ANSWER_LINE characters
# (``answer_line``)
EXCHANGES_KEPT = 5
ANSWER_LINE = 240

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
TELLING_TOOLS = ("ask_user", "submit_editspec")   # compiler.STREAMED_TOOLS: their input streams as it is written
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


SAFE_NO_CONVERSATION = ("Safe mode has no conversation: press a button to make a change, or switch to Default mode to "
                        "type a request.")

STATUS_FOR_LOG = {"Preparing and checking the preview…": ("previewing", "Preparing and checking the preview…"),
                  "Looking at the rendered pages…": ("looking", "Looking at the rendered pages…"),
                  "Creating and checking your edited copies…": ("creating", "Creating and checking your edited copies…"),
                  "Creating and checking the clean copies…": ("creating", "Creating and checking the clean copies…"),
                  "Saving the clean PDFs…": ("rendering", "Saving the clean PDFs…"),
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


def _md5(data: bytes) -> str:
    """The fingerprint Export records of the bytes it wrote (``exported``), compared with a copy's bytes since."""
    return hashlib.md5(data).hexdigest()


def answer_line(text: str) -> str:
    """The one-line account of Claude's answer the next request is told (``exchanges``): the first sentence of its
    first paragraph (a leading list mark dropped), at most ``ANSWER_LINE`` characters, cut with an ellipsis."""
    first = next((p.strip() for p in str(text or "").splitlines() if p.strip()), "")
    first = re.sub(r"\s+", " ", first).lstrip("-•*#> ").strip()
    end = re.search(r"[.!?](?=\s|$)", first)
    if end:
        first = first[:end.end()]
    if len(first) > ANSWER_LINE:
        first = first[:ANSWER_LINE - 1].rstrip() + "…"
    return first


def _same_file_key(path) -> str:
    """One spelling of a path for comparing files: resolved when the file system can, the text otherwise."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


class DocumentTask:
    def __init__(self, session=None, *, journal=None, render=True, mode="default", obliterate_allowed=False,
                 spending_limit=None):
        if mode not in MODES or (mode == "obliterate" and not obliterate_allowed):
            raise ValueError(f"mode must be one of {LAUNCH_MODES}")
        self.spending_limit = spending_check(spending_limit)   # Settings → Spending check: USD per request, None off
        self.mode = mode
        self.obliterate_allowed = bool(obliterate_allowed)   # Settings → Advanced → "Show Obliterate mode"
        self.session = session or Session()
        self.session.unguarded = mode == "obliterate"
        self.session.recovery_attempts = RECOVERY_ATTEMPTS   # the recovery a reviewed outcome plan starts, too
        self.render = render
        self.journal = Path(journal) if journal else None
        self.messages: List[Dict[str, str]] = []
        self.decisions: List[str] = []
        # the requests Claude answered without a plan, finished with the answer (``_end_exchange``), and those the user
        # withdrew with Discard (``_discard_request``: ``"discarded"`` is "preview" or "request"): the user's words and
        # the first sentence of the answer, the last EXCHANGES_KEPT, told to the model with the next request
        self.exchanges: List[Dict[str, Any]] = []
        self.word_sources: List[str] = []
        self.results: Dict[str, str] = {}
        self.unverified: Dict[str, List[str]] = {}   # original → the failed checks its copy was delivered with
        # Save clean PDFs: original → the PDF of its clean copy, the last run's; never the working set (``_end_export``)
        self.pdfs: Dict[str, str] = {}
        self._pdf_info: Optional[Dict[str, Any]] = None   # whether this computer can make PDFs (``pdf_info``)
        # Export → Save the copies…: copy or PDF path → ``{"to": where it was saved, "md5": of the bytes written}``,
        # and the folder of the last export. A copy or a clean PDF is saved only while a file with its current bytes
        # sits where Export put it (``export_states``, ``unsaved_copies``): until then it is kept nowhere but the
        # window's scratch folder.
        self.exported: Dict[str, Dict[str, str]] = {}
        self.export_folder = ""
        self._digests: Dict[str, tuple] = {}   # copy path → (size, mtime_ns, md5): the last fingerprint taken
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
        # the documents attached beside copies since the last run, by attached original: they carry none of the
        # changes and missed them by arriving later, which the model is told apart from a document an earlier run left
        # out (``_fresh_documents``)
        self.fresh: List[str] = []
        # the to-do list: documents the user set aside at approval (the preview did not look right for them), keyed
        # by the attached original — with the decisions they were set aside from and the plan they saw; and the
        # focus: the pass over them, once the user begins it (its documents, the plan they saw)
        self.todo: Dict[str, Dict[str, Any]] = {}
        self.focus: Optional[Dict[str, Any]] = None
        # what the last Safe-mode button left untouched (its documents, why, and the button's sentence): offered to
        # Default mode, where the conversation can plan them (``hand_off``)
        self.left_untouched: List[Dict[str, str]] = []
        # what the window showed before a Safe-mode button cleared it for its preview (the last copies, what the last
        # button left untouched): Discard shows it again (``discard_preview``), and the undo point of that button's
        # run keeps it as what came before
        self._before_button: Optional[Dict[str, Any]] = None
        # the working set as it was before each run that delivered copies, newest last (``undo``), and the point
        # taken at the approval that is running, kept by ``_advance_generation`` once that run delivers
        self.undo_points: List[Dict[str, Any]] = []
        self._pending_undo: Optional[Dict[str, Any]] = None
        self._template_kind: Optional[tuple] = None    # (the template's path, size, mtime, recipes.template_kind)
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
        self._words_said_for = None         # the custom method's preview whose word changes the chat has said
        self._budget = self._new_budget()
        self._request_budget = self._new_request()
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
    def _new_budget():
        """The conversation's ledger: every model call and the spending the window shows; no limit of its own."""
        return llm.CallBudget(max_cost_usd=None, max_calls=llm.UNLIMITED_CALLS,
                              max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=None,
                              effort=CONVERSATION_EFFORT)

    def _new_request(self):
        """One request's ledger, inside the conversation's: the user's spending check. A call whose largest possible
        cost would take the request past ``spending_limit`` is refused before it starts (``CallBudget._preflight``);
        None leaves the request unchecked. Its effort is the conversation's, so the calls inside keep theirs."""
        return llm.CallBudget(max_cost_usd=self.spending_limit, max_calls=llm.UNLIMITED_CALLS,
                              max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, max_total_tokens=None,
                              effort=CONVERSATION_EFFORT)

    def set_spending_limit(self, limit) -> None:
        """Settings → Spending check: from the next request on. A request paused at the check is let go on at the new
        limit when the user continues it."""
        self.spending_limit = spending_check(limit)

    def pause_reason(self) -> str:
        """Why the work is paused (``PAUSES``), or '' when it is not."""
        if self.stage != "budget":
            return ""
        reason = self._request_budget.exhausted_reason or self._budget.exhausted_reason or ""
        if "price" in reason and "unknown" in reason:
            return "unpriced"
        return "limit" if "ceiling" in reason else "interrupted"     # llm.CallBudget._preflight's own words

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
        if self.mode == "safe":
            # nothing to continue without the conversation: a button press is planned again when it is pressed
            self.decisions = []
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
        had_button = self.session.model_free and self.stage == "preview"
        fresh: List[str] = []
        if template:
            found = triage(checked[0], template=True)
            if found.refused:
                raise ValueError(found.reason)
            self.attachments = [a for a in self.attachments if a.path != checked[0] and not a.template]
            self.attachments.append(found)
        else:
            known = {a.path for a in self.attachments}
            # Every file is triaged before any is attached: an exception from one (the stress test's F6, a malformed
            # zip beside a valid document) leaves the attachments, the fresh list, the pass and the session as they
            # were. A triage refusal is not an exception: the file is attached, refused, with its reason.
            found = [(path, triage(path) if path.suffix.lower() == ".docx" else triage_reference(path))
                     for path in checked if path not in known]
            for path, attachment in found:
                self.attachments.append(attachment)
                if self.working and path.suffix.lower() == ".docx":
                    fresh.append(path.name)
                    self.fresh.append(str(path))
                if self.focus and path.suffix.lower() == ".docx":
                    self.focus["documents"].append(str(path))      # attached during the pass: it joins the pass
        self._sync()
        if not template:
            self._say_same_names([p for p in checked if p.suffix.lower() == ".docx"])
        if before != (self.documents(), self.template(), self.references()):
            self._invalidate()
            if self.decisions:
                self.say("The attachments changed. I’ll check the updated documents against your request.")
            elif had_button:
                self.say("The attachments changed, so that preview no longer applies. Press the button again to "
                         "preview it on these documents.")
            if fresh:
                self.say(f"{', '.join(fresh)}: attached as an original. The changes already made to the other copies "
                         f"are not in it; the next change is made to it as it is. To give it every change, say "
                         f"“{START_OVER}” and ask again.")
        self.save()

    def attach_folder(self, folder) -> Dict[str, Any]:
        """**Add a folder of documents** (backlog F3): the folder's own ``.docx`` files, sorted by name, attached
        through ``attach`` like any other (Word's ``~$`` lock files skipped, subfolders not entered); the app message
        says how many documents its subfolders hold, to add those folders too. The app's own folders (the scratch
        tree, the run root, the config folder) are refused as a source, like Export refuses them as a destination.
        Returns ``{"folder", "attached", "subfolders"}``."""
        from .settings import config_dir, scratch_root
        folder = Path(folder)
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder.")
        folder = folder.resolve()
        for own in (self.session.out_root, scratch_root(), config_dir()):
            root = _same_file_key(own)
            if _same_file_key(folder) == root or _same_file_key(folder).startswith(root.rstrip("\\/") + os.sep):
                raise ValueError(f"{folder} is one of the app's own folders; choose a folder of your documents.")
        docs = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".docx"
                       and not p.name.startswith("~$")), key=lambda p: p.name.casefold())
        below = 0
        for sub in folder.iterdir():
            if sub.is_dir():
                below += sum(1 for p in sub.iterdir() if p.is_file() and p.suffix.lower() == ".docx"
                             and not p.name.startswith("~$"))
        if not docs:
            raise ValueError(f"{folder.name} holds no Word documents of its own"
                             + (f"; its subfolders hold {below}. Add those folders instead." if below else "."))
        known = {a.path for a in self.attachments}
        fresh = [p for p in docs if p.resolve() not in known]
        self.attach([str(p) for p in docs])
        note = (f"{len(fresh)} document{'s' if len(fresh) != 1 else ''} added from {folder.name}"
                + (f" ({len(docs) - len(fresh)} already attached)" if len(docs) != len(fresh) else "") + ".")
        if below:
            note += (f" Its subfolders hold {below} more Word document{'s' if below != 1 else ''}, not added; add "
                     f"those folders too if you want them.")
        self.say(note)
        self.save()
        return {"folder": str(folder), "attached": len(fresh), "subfolders": below}

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
        self.fresh = [k for k in self.fresh if k != str(path)]
        self.todo.pop(str(path), None)
        for shown in [*self.undo_points, self._before_button or {}]:
            # attached again later, it is an original: no undo or discard puts an old copy back in its place, or
            # shows one as its result
            for part in ("working", "applied", "results", "unverified", "pdfs"):
                if part in shown:
                    shown[part].pop(str(path), None)
            if "left_untouched" in shown:
                shown["left_untouched"] = [e for e in shown["left_untouched"]
                                           if _same_file_key(e["path"]) != _same_file_key(path)]
        self.left_untouched = [e for e in self.left_untouched if _same_file_key(e["path"]) != _same_file_key(path)]
        self.pdfs.pop(str(path), None)
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
            self.history, self.generation, self.results, self.applied, self.fresh = [], 0, {}, {}, []
            self.unverified = {}
            self.undo_points, self._before_button = [], None
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
                                 context=self._focus_context(), todo=self._todo_lines(),
                                 earlier=self._exchange_lines())

    def _history_in_scope(self) -> List[str]:
        """The changes the documents in scope carry, one line each, in the order they were made: a change every one
        carries reads as it is; one that some got and others did not (a run on part of the batch: documents left out,
        set aside or refused) says which, so the model never plans from a state a document is not in. A document with
        no copy carries nothing; one attached since the last run is told apart by the compiler (``fresh``) and is not
        counted here."""
        docs = [str(a.path) for a in self.attachments if self._in_scope(a)
                and (str(a.path) in self.working or str(a.path) not in self.fresh)]
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
        # a copy delivered UNVERIFIED (Obliterate mode), or built on one: the model plans on a copy whose content the
        # checks could not vouch for, and must hear so — the mark says which checks failed, and where
        for d in docs:
            marks = self.unverified.get(d)
            if marks:
                lines.append(f"{Path(d).name} was delivered UNVERIFIED (Obliterate mode): {'; '.join(marks)} failed, "
                             "so its copy may have lost or changed content; plan on it with that in mind")
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

    def _exchange_lines(self) -> List[str]:
        """The finished exchanges the model is told with a new request (``exchanges``), one line each, oldest first:
        the user's words in the order they came, and the one-line account of the answer."""
        lines: List[str] = []
        for entry in self.exchanges:
            asked = [a.strip() for a in entry.get("asked", []) if isinstance(a, str) and a.strip()]
            if not asked:
                continue
            said = "; then: ".join(f"“{a}”" for a in asked)
            withdrawn = entry.get("discarded")
            if withdrawn:
                lines.append(f"The user asked {said} — " + ("a plan was previewed and the user discarded it"
                                                           if withdrawn == "preview" else
                                                           "the user dropped the request") +
                             " before anything was created")
                continue
            lines.append(f"The user asked {said} — the assistant answered: “{entry.get('answered') or '(no words)'}”")
        return lines

    def _answered(self, outcome) -> bool:
        """Claude's reply ended the exchange with an answer and nothing pending: words to the user alone — no question,
        no choice, no suggested reply, no spec, no custom method being prepared, no recovery, and no submission the
        gate refused on the way (then the request stands: Claude has still to plan it). Such a reply finishes the
        request (``_end_exchange``); with anything pending the request stays open and the next message is a revision
        of it in the same conversation."""
        s = self.session
        turn = s.turn
        return (outcome.kind == "question" and bool(self.decisions) and s.conversation is not None
                and not getattr(s.conversation, "recovery_active", False)
                and s.recovery is None and s.pending_custom_transform is None
                and s.spec is None and s.generated is None
                and turn is not None and turn.status == "questions" and not turn.questions and not turn.choices
                and not turn.chips and not turn.errors and bool((turn.message or "").strip())
                and not self._request_budget.exhausted_reason)

    def _end_exchange(self, message: str) -> None:
        """Claude's answer finished the request (``_answered``). Its words stay in the chat as they are; the exchange
        — the user's words and the first sentence of the answer — is kept as context for the next request
        (``_exchange_lines``, the last ``EXCHANGES_KEPT``); the decisions are cleared, so a put-back, an attach or a
        mode change has nothing stale to revive; and the conversation is over: the next message is a new request."""
        self.exchanges.append({"asked": list(self.decisions), "answered": answer_line(message)})
        del self.exchanges[:-EXCHANGES_KEPT]
        self.decisions = []
        self.session._set_outcome()
        self.session.end_conversation()

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
        point, self._pending_undo = self._pending_undo, None
        if point is None:
            # a run whose approval took no point (it resumed after a restart): nothing before it can be gone back to
            # exactly, so no undo reaches past it
            self.undo_points = []
        else:
            before = set(point.pop("todo_before", []))
            point["todo_added"] = [key for key in self.todo if key not in before]
            self.undo_points.append(point)
        self._before_button = None
        done = [c["summary"] for g in self._groups() for c in g["changes"]] or self.decisions[:1]
        self.history.extend(done)
        for key in moved:
            self.applied.setdefault(key, []).extend(done)         # these copies carry the change; no other copy does
        self.fresh = []                                           # every document now attached was there for this run
        self.generation += 1
        self.decisions = []
        self.focus = None                                         # a pass over set-aside documents ends with its copies
        self._sync()
        self.session._set_outcome()
        self.session.end_conversation()

    def _fresh_documents(self) -> List[str]:
        """Documents attached after the last run: originals among working copies, carrying none of the changes the
        history lists (the model is told; the user was told when they attached them). An original an earlier run
        left out is not one of them: ``_history_in_scope`` names it beside each change it lacks."""
        if not self.working:
            return []
        return [a.path.name for a in self.attachments if self._in_scope(a) and str(a.path) not in self.working
                and str(a.path) in self.fresh]

    def start_over(self) -> None:
        """Back to the attached originals: the working copies are set aside (the files stay where they are), the
        plan is void, and the next request starts from the originals."""
        if not self.working and not self.history and not self.todo and not self.focus:
            return
        self.working, self.generation, self.history, self.decisions = {}, 0, [], []
        self.results, self.unverified, self.pdfs = {}, {}, {}
        self.applied, self.todo, self.focus, self._binned, self.fresh = {}, {}, None, [], []
        self.left_untouched = []
        self.undo_points, self._pending_undo, self._before_button = [], None, None
        self._sync()
        self._invalidate()
        self.stage = "empty"
        self.say("Starting over from the originals. " +
                 ("Pick a button to start." if self.mode == "safe" else "Tell me what you'd like to change."))
        self.save()

    # ---- going back ----
    def _undo_point(self) -> Dict[str, Any]:
        """The working set as it is before the run being approved, and what the window shows of it (a button's
        preview cleared that, so its ``_before_button``): what ``undo`` restores once the run has delivered."""
        shown = self._before_button or {"results": self.results, "unverified": self.unverified,
                                        "left_untouched": self.left_untouched, "pdfs": self.pdfs}
        return {"request": self.decisions[0] if self.decisions else "",
                "working": dict(self.working), "generation": self.generation, "history": list(self.history),
                "applied": {k: list(v) for k, v in self.applied.items()}, "fresh": list(self.fresh),
                "results": dict(shown["results"]), "pdfs": dict(shown.get("pdfs", {})),
                "unverified": {k: list(v) for k, v in shown["unverified"].items()},
                "left_untouched": [dict(e) for e in shown["left_untouched"]],
                "todo_before": list(self.todo)}

    def _undo_blocked(self) -> str:
        """Why the last change cannot be undone now; '' when it can."""
        if not self.undo_points:
            return "There is nothing to undo."
        if self.stage == "preview" and self.session.recovery is None:
            return "Discard the preview first."
        if self.stage not in ("done", "empty", "resume") or self.session.recovery is not None:
            return ("Drop the request that is open first, or finish it."
                    if self.discard_kind() == "request" else "Finish the request that is open first.")
        if self.focus:
            return "Finish the pass over your to-do list first."
        return ""

    def _shown_again(self, shown: Dict[str, Any]) -> None:
        """The copies and the Left untouched list a window showed before, for the documents still attached; a copy
        no longer where it was saved is not shown."""
        attached = {str(a.path) for a in self.attachments if not a.template and not a.reference}
        keys = {_same_file_key(p) for p in attached}
        self.results = {k: v for k, v in shown["results"].items() if k in attached and Path(v).is_file()}
        self.pdfs = {k: v for k, v in shown.get("pdfs", {}).items() if k in attached and Path(v).is_file()}
        # a mark belongs to the working copy it was delivered with, shown among the results or not
        self.unverified = {k: list(v) for k, v in shown["unverified"].items() if k in self.results or k in self.working}
        self.left_untouched = [dict(e) for e in shown["left_untouched"]
                               if _same_file_key(e["path"]) in keys and e["path"] not in self.todo]

    def discard_kind(self) -> str:
        """What Discard withdraws now: ``preview`` (a preview waiting for approval, in every mode), ``request`` (in
        Default and Obliterate, a request still open with nothing to approve: Claude's question, a reply to continue,
        a custom method being written, a pause at the spending check), or ``""``. Never while recovery checks files
        after a run: its copies are saved with their own button and the rest set aside (``SET_ASIDE_REST``)."""
        s = self.session
        if s.recovery is not None or getattr(s.conversation, "recovery_active", False):
            return ""
        if self.stage == "preview":
            return "preview"
        if (self.mode != "safe" and self.stage in ("question", "resume", "budget")
                and (self.decisions or s.pending_custom_transform is not None)):
            return "request"
        return ""

    def discard_preview(self) -> None:
        """Discard: the preview waiting for approval — or, in Default and Obliterate, the request still open — is
        withdrawn and nothing is created. In Safe mode the window shows again what it showed before the button was
        pressed (the last copies, what that button left untouched). In Default and Obliterate the request ends: the
        plan, its preview, Claude's open question or the method being written are void, the decisions are cleared and
        the next message is a new request, told that this one was withdrawn (``exchanges``); a pass over the to-do
        list ends with it, its documents back on the list as they were. The working set never changed. No file is
        written or deleted."""
        kind = self.discard_kind()
        if not kind:
            if self.session.recovery is not None:
                raise ValueError("The documents are being checked again: save the ready copies, or set the rest "
                                 "aside for later.")
            raise ValueError("There is no preview to discard." if self.mode == "safe" else
                             "There is nothing to discard: no preview or request is waiting.")
        if self.mode != "safe":
            self._discard_request(kind)
            return
        before, self._before_button = self._before_button, None
        self._invalidate()
        if before is not None:
            self._shown_again(before)
        self.stage = "done" if self.results and self.generation else "empty"
        self.say("Preview discarded; nothing was created. Change the form and preview again, or choose another "
                 "button.")
        self.save()

    def _discard_request(self, kind: str) -> None:
        """Default and Obliterate's Discard (``discard_preview``): the request ends with nothing created."""
        asked, focus = list(self.decisions), self.focus
        if focus:
            # the pass is withdrawn with its request: its documents wait on the to-do list again, as they did
            for key in focus["documents"]:
                why = focus.get("why", {}).get(key, {})
                self.todo[key] = {"name": Path(key).name, "decisions": list(focus.get("decisions") or asked),
                                  "seen": list(focus.get("seen", [])), "why": why.get("why", WHY_HELD),
                                  "reason": why.get("reason") or HELD_REASON}
            self.focus = None
        self._invalidate()
        if asked:
            self.exchanges.append({"asked": asked, "answered": "", "discarded": kind})
            del self.exchanges[:-EXCHANGES_KEPT]
        self.decisions = []
        self._request_budget = self._new_request()           # a pause at the spending check ends with the request
        self._budget.exhausted_reason = None
        self._sync()
        self.stage = "done" if self.results and self.generation else "empty"
        self._revision += 1
        self.say(DISCARDED[kind] + " Your next message starts a new request" +
                 ("; the copies you have stay as they are." if self.working else ".") +
                 (" The documents you were working on are back on your to-do list." if focus else ""))
        self.save()

    def undo(self) -> None:
        """Undo the last change: the working set goes back to what it was before the last run that delivered copies.
        The documents that run changed build on their copies from before it again (their originals, when it was the
        first), the history and each copy's changes are as they were, the copies and Left untouched list it replaced
        show again, and the documents its ✗ put on the to-do list come off it. One step per call, newest first. No
        file is written or deleted: the copies it made stay where they were saved."""
        blocked = self._undo_blocked()
        if blocked:
            raise ValueError(blocked)
        point = self.undo_points[-1]
        attached = {str(a.path) for a in self.attachments if not a.template and not a.reference}
        working = {k: v for k, v in point["working"].items() if k in attached}
        gone = [Path(v).name for v in working.values() if not Path(v).is_file()]
        if gone:
            raise ValueError(f"The copies from before the last change are no longer where they were saved "
                             f"({', '.join(gone[:3])}), so it cannot be undone. Start over from the originals "
                             f"instead.")
        self.undo_points.pop()
        self.working, self.generation = working, point["generation"]
        self.history = list(point["history"])
        self.applied = {k: list(v) for k, v in point["applied"].items() if k in working}
        # the documents attached before the undone run, and since it, arrived after the changes still made; a point
        # from before ``fresh`` counts every original among the copies so
        before = point.get("fresh")
        before = [str(a.path) for a in self.attachments] if before is None else before
        self.fresh = [k for k in dict.fromkeys([*before, *self.fresh]) if k in attached and k not in working] \
            if working else []
        for key in point.get("todo_added", []):
            self.todo.pop(key, None)
        self._shown_again(point)
        self._before_button = self._pending_undo = None
        self._sync()
        self._invalidate()
        if not self.decisions:
            self.stage = "done" if self.results and self.generation else "empty"
        request = str(point.get("request") or "").strip().rstrip(".")
        upcoming = "The next button" if self.mode == "safe" else "Your next request"
        self.say((f"Undid “{request}”. " if request else "Undid the last change. ") +
                 (f"{upcoming} builds on the copies from before it." if self.working else
                  f"{upcoming} works on your originals again.") +
                 " The copies it made stay where they were saved; nothing was deleted.")
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

    def perform(self, action, *, text="", token="", choice_id="", chip=False, hold=(), wording=None, recipe="",
                inputs=None, skip=(), log=lambda _: None, events=None):
        """Worker entry point. One ledger covers every nested model call and every turn. ``events`` is the window's
        live channel (docs/WEB_API.md: status, activity, text_delta, thinking_delta, message, plan, samples, results,
        question, cost); without one the job runs silently and the view says everything at the end. ``hold`` (the
        primary action) names the previewed documents the user set aside: they go on the to-do list, the others run.
        ``recipe`` and ``inputs`` (the ``recipe`` action) are a Safe-mode button and its form. ``skip`` (the
        ``occurrences`` action) names, by key, the find-and-replace occurrences the user unticked in the preview."""
        self.problem = ""
        self._events = events
        self.save(in_progress=True)

        def checkpointed_log(message):
            self.save(in_progress=True)
            status = STATUS_FOR_LOG.get(str(message))
            if status:
                self._emit({"type": "status", "kind": status[0], "text": status[1]})
            log(message)
        if not (action == "primary" and self.stage == "budget"):
            # a new request: the spending check starts again, and nothing stopped on the way carries over
            self._request_budget = self._new_request()
            self._budget.exhausted_reason = None
        try:
            with llm.budget_scope(self._budget), llm.budget_scope(self._request_budget), \
                    llm.stream_observer_scope(self._on_stream):
                if action == "send":
                    self._send(text, checkpointed_log, chip=chip)
                elif action == "choose":
                    self._choose(choice_id, checkpointed_log, wording=wording)
                elif action == "primary":
                    self._primary(token, checkpointed_log, hold=hold)
                elif action == "recipe":
                    self._recipe(recipe, inputs, checkpointed_log)
                elif action == "occurrences":
                    self._occurrences(skip, checkpointed_log)
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
            if self._request_budget.exhausted_reason and self.stage not in {"done", "preview"}:
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
        # setting is on, the model is served fast, and Anthropic has not refused fast mode for this key; ``request``:
        # this request's spending, ``limit`` the user's spending check (None: off)
        return {"usd": cost, "text": text, "calls": self._budget.calls, "fast": llm.fast_for(llm.DEFAULT_MODEL),
                "request": self._request_budget.spent_usd, "limit": self.spending_limit}

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
        if self.mode == "safe" and text.casefold() != START_OVER.casefold():
            raise ValueError(SAFE_NO_CONVERSATION)
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
        if self.mode == "safe":
            raise ValueError(SAFE_NO_CONVERSATION)
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

    # ---- Safe mode ----
    def set_mode(self, mode: str, acknowledged: bool = False) -> None:
        """Safe (the buttons, no AI call), Default (the conversation) or Obliterate (the conversation without the
        content guardrails: ``Session.unguarded``). The documents, copies and to-do list stay; between Safe and Default
        any preview stays too. Obliterate needs the setting that shows it (``obliterate_allowed``) and the user's
        acknowledgement of its warning each time; entering or leaving it voids what was planned under the other rules
        (a new conversation follows the new ones)."""
        if mode not in MODES:
            raise ValueError("There is no such mode.")
        if mode == "obliterate" and not self.obliterate_allowed:
            raise ValueError("Obliterate mode is hidden. Settings → Advanced shows it.")
        if mode == "obliterate" and acknowledged is not True:
            raise ValueError("Read the Obliterate warning and confirm it first.")
        crossing = (self.mode == "obliterate") != (mode == "obliterate")
        was_safe = self.mode == "safe"
        self.mode = mode
        self.session.unguarded = mode == "obliterate"
        if crossing:
            if was_safe:
                self.decisions = []                  # a button's sentence is not a request for a conversation
            self._invalidate()
            self.say((OBLITERATE_ON if mode == "obliterate" else OBLITERATE_OFF)
                     + (REPLAN if self.stage == "resume" else ""))
            self.save()

    def available_modes(self) -> List[str]:
        return list(MODES) if self.obliterate_allowed else list(LAUNCH_MODES)

    def _recipe(self, recipe_id, inputs, log):
        """A Safe-mode button: its fixed spec (``recipes.build``, from the form and the attachments) through the one
        gate every submission passes (``compiler.compose_submission``), then the ordinary preview. No AI call: the
        outcome is model-free (``Session.model_free``: no visual check, no recovery). A form that cannot be used, or
        a spec the gate refuses, is said in one message and nothing is previewed."""
        from . import compiler, recipes
        if self.mode != "safe":
            raise ValueError("The buttons are Safe mode's; switch to Safe mode to use them.")
        s = self.session
        recipe = recipes.BY_ID.get(str(recipe_id))
        if recipe is None:
            raise ValueError("There is no such button.")
        files = s.files(outcome=False)
        ctx = recipes.Context(files, self.template(), self.references())
        self._pdf_info = None                    # a renderer installed or removed since is seen at the press
        try:
            built = recipes.build(recipe.id, inputs, ctx)
        except recipes.RecipeError as e:
            self._revision += 1
            self.say(f"{recipe.label}: {e}")
            return
        self._emit({"type": "status", "kind": "checking", "text": "Checking the documents…"})
        if built.gated:
            # a button that plans per layout passed each layout through the gate itself (``body_match``)
            outcome, errors, nothing = built.outcome, [], built.outcome is None
        else:
            outcome, errors = compiler.compose_submission(built.spec, ctx.batch(), [built.request, *built.sources],
                                                          leave_out=list(built.left_out))
            nothing = bool(errors) and errors[0].startswith(compiler.PLANS_ON_NONE)
        if errors and not nothing:
            self._revision += 1
            self.say(f"{recipe.label} cannot be used on these documents: " + "; ".join(errors[:3]))
            return
        s.end_conversation()
        if self._before_button is None:
            # what the window shows now: Discard shows it again (a button pressed over another's preview keeps the
            # first one's, which is still what came before)
            self._before_button = {"results": dict(self.results),
                                   "unverified": {k: list(v) for k, v in self.unverified.items()},
                                   "left_untouched": [dict(e) for e in self.left_untouched],
                                   "pdfs": dict(self.pdfs)}
        # Results describe this button's copies. Earlier copies remain in the working set and on disk, but must
        # not be presented as if they carry a later change that could be refused or fail verification. An UNVERIFIED
        # mark is the working copy's, not the button's: it stays (``_mark_unverified``).
        self.results, self.pdfs = {}, {}
        self.say(built.request, "user")
        self.decisions = [built.request]
        self.word_sources.extend(w for w in built.sources if w not in self.word_sources)
        self.left_untouched = []
        if outcome is None:
            # no layout of the batch can take the change: nothing to preview; each document's reason beside it
            s._set_outcome()
            self.left_untouched = [{"path": str(self.origin_of(Path(p))), "name": Path(p).name, "why": WHY_REFUSED,
                                    "reason": refusal_message({"reason": r}), "request": built.request} for p, r in built.left_out.items()]
            self.stage = "empty"
            self._revision += 1
            self.say("None of the documents can take this change as they are; each one's reason is under Left "
                     "untouched beside the documents.")
            self.save()
            return
        s._set_outcome(spec=outcome, source="recipe", left_out=list(built.left_out),
                       left_out_reasons=built.left_out, export=built.export)
        self._settle(log)
        self._say_render_warnings()
        if nothing:
            self.say("None of the documents can take this change as they are; each one's reason is under Left "
                     "untouched.")

    def _say_render_warnings(self) -> None:
        """Safe mode has no conversation to say what the preview found: the render comparison's warning (words the
        original's pages show that the copy's do not, `preview.render_word_warnings`) is said here in the app's words,
        once per sample, before the button that creates the copies. Default mode says it with the preview's note
        (``Session._note_preview``)."""
        from .preview import render_word_warnings
        for line in render_word_warnings(self.session.outcome().preview):
            self.say(line + ".")

    def _occurrences_view(self, preview) -> List[Dict[str, Any]]:
        """The find-and-replace occurrences the waiting plan lists (backlog F1), per document of the plan — every
        planned document, not the sampled ones only — each item with the key the page sends back to leave it as it
        is. A document the plan refused lists every occurrence found, with the reason, so the user can
        untick the occurrence the refusal names. Empty when no plan lists any. A plan done in steps lists its first
        step's only."""
        from .compiler import skip_record_key
        bp = getattr(preview, "batch_plan", None)
        out: List[Dict[str, Any]] = []
        for f in getattr(bp, "files", None) or []:
            listed = f.occurrences()
            if not listed:
                continue
            source = str(f.source)
            refused = f.status != "planned"
            out.append({"source": source, "name": Path(source).name, "status": "refused" if refused else "planned",
                        "reason": refusal_message(f.exception) if refused else "",
                        "items": [{"key": skip_record_key({"file": source, **o}), "part": o.get("part", ""),
                                   "paragraph": o.get("paragraph", 0), "start": o.get("start", 0),
                                   "end": o.get("end", 0), "where": o.get("where", ""),
                                   "heading": o.get("heading", ""), "before": o.get("before", ""),
                                   "match": o.get("match", ""), "after": o.get("after", ""),
                                   "find": o.get("find", ""), "replace": o.get("replace", ""),
                                   "skipped": bool(o.get("skipped")), "op_index": o.get("op_index", 0)}
                                  for o in listed]})
        return out

    def _occurrences(self, keys, log):
        """The user's unticks taken into the waiting plan (backlog F1): every find-and-replace op of the previewed
        spec gets the unticked occurrences of its own as its skip list (an op's skip list is exactly the unticked
        set; a tick puts an occurrence back), the spec passes the gate again with those keys as the ones recorded
        from the user's unticks (``compiler.compose_submission(skips=)``), and the preview is made again on the same
        documents. Nothing is created; a preview the gate refuses stays as it was; in Safe mode Discard still shows
        what came before the button."""
        import copy
        from . import compiler
        from .editspec import is_chain
        from .layouts import is_layout_plan
        s = self.session
        if not isinstance(keys, (list, tuple)) or not all(isinstance(k, str) for k in keys):
            raise ValueError("The unticked occurrences could not be read.")
        preview = s.outcome().preview
        if self.stage != "preview" or s.spec is None or preview is None or s.recovery is not None:
            self.say("There is no preview to update.")          # said, as a button's unusable form is; nothing voided
            return
        listed = {item["key"]: (doc["source"], item) for doc in self._occurrences_view(preview) for item in doc["items"]}
        if any(k not in listed for k in keys):
            self.say("An occurrence to leave is not in the preview's list; look at the list again.")
            return                                              # the preview and its token stand as they were
        by_op: Dict[int, List[Dict[str, Any]]] = {}
        for key in dict.fromkeys(keys):
            source, item = listed[key]
            by_op.setdefault(int(item["op_index"]), []).append(
                {"file": source, "part": item["part"], "paragraph": int(item["paragraph"]), "start": int(item["start"]),
                 "end": int(item["end"]), "find": item["find"]})
        spec = copy.deepcopy(s.spec)
        if is_layout_plan(spec):
            targets = [layout["spec"] for layout in spec.get("layouts") or []]
        else:
            targets = [spec]
        ops_lists = [(t["stages"][0]["spec"] if is_chain(t) else t).get("ops") or [] for t in targets]
        for ops in ops_lists:
            for i, op in enumerate(ops):
                if op.get("op") != "replace_table":
                    continue
                params = op.setdefault("params", {})
                if by_op.get(i):
                    params["skip"] = [dict(e) for e in by_op[i]]
                else:
                    params.pop("skip", None)
        files = s.files(outcome=False)
        batch = compiler._Batch(files, self.template(), input_files=self.references())
        sources = [w for w in (*self.decisions, *self.word_sources) if isinstance(w, str) and w.strip()]
        shown = [m["text"] for m in self.messages if m.get("role") == "assistant" and isinstance(m.get("text"), str)]
        self._emit({"type": "status", "kind": "checking", "text": "Checking the documents…"})
        outcome, errors = compiler.compose_submission(spec, batch, sources, leave_out=list(s.left_out),
                                                      unguarded=s.unguarded, shown=shown if s.unguarded else (),
                                                      skips=[compiler.skip_record_key(e) for es in by_op.values() for e in es])
        if errors or outcome is None:
            self._revision += 1
            self.say("The preview could not be updated with those occurrences left as they are: " + "; ".join(errors[:3]))
            return
        n = sum(len(es) for es in by_op.values())
        s._set_outcome(spec=outcome, source=s.outcome_source, left_out=list(s.left_out),
                       left_out_reasons=dict(s.left_out_reasons), export=s.export)
        self.say(f"{n} occurrence{'s' if n != 1 else ''} will be left as {'they are' if n != 1 else 'it is'}; the "
                 f"preview is being made again." if n else "Every occurrence is back in; the preview is being made again.")
        self._settle(log)
        if self.mode == "safe":
            self._say_render_warnings()

    def hand_off(self, paths) -> None:
        """Safe mode's "Try these in Default mode": documents a button left untouched go on the to-do list with the
        button's sentence as their request, and the window turns to Default mode, where the conversation can plan
        them (``start_todo``). Only documents the last button left untouched; nothing while a preview waits."""
        if self.stage == "preview":
            raise ValueError("Create the copies, or press another button, before handing documents over.")
        wanted = {str(Path(str(v))) for v in paths}
        attached = {_same_file_key(a.path) for a in self.attachments if not a.template and not a.reference}
        chosen = [entry for entry in self.left_untouched
                  if entry["path"] in wanted and _same_file_key(entry["path"]) in attached]
        if not chosen or len(chosen) != len(wanted):
            raise ValueError("Those documents are not the ones the last button left untouched.")
        for entry in chosen:
            why = str(entry.get("reason") or "").strip().rstrip(".")
            refused = entry.get("why", WHY_REFUSED) == WHY_REFUSED
            self.todo[entry["path"]] = {
                "name": entry["name"], "decisions": [entry["request"]] if entry["request"] else [], "seen": [],
                "why": WHY_REFUSED if refused else WHY_FAILED,
                # a document whose clean copy was made but not its PDF (Save clean PDFs) says so as it is
                "reason": f"{why}." if why.startswith(NO_PDF) else
                ("The button could not change it" if refused else "Its copy could not be made")
                + (f": {why}." if why else ".")}
        self.left_untouched = [e for e in self.left_untouched if e["path"] not in wanted]
        self.mode = "default"
        self._sync()
        names = ", ".join(e["name"] for e in chosen)
        self.say(f"Handed over to Default mode: {names}. Say “{WORK_TODO}” and tell me what should be different; "
                 f"the conversation plans them with an AI model.")
        self._revision += 1
        self.save()

    def _refused_by_preview(self, preview, request: str) -> List[Dict[str, str]]:
        """The documents a button's preview refused (the plan, at any of its steps), with their reasons:
        Create does not run them (``Session._without_refused``), so the run's own results never name them."""
        from .preview import refusals
        out: List[Dict[str, str]] = []
        for path, reason in self.session.left_out_reasons.items():       # a button that left documents out, with why
            origin = str(self.origin_of(Path(path)))
            out.append({"path": origin, "name": Path(origin).name, "why": WHY_REFUSED, "reason": refusal_message({"reason": reason}),
                        "request": request})
        for source, exc in refusals(preview):
            origin = str(self.origin_of(source))
            out.append({"path": origin, "name": Path(origin).name, "why": WHY_REFUSED,
                        "reason": refusal_message(exc), "request": request})
        return out

    def _left_after_run(self, request: str, refused: Sequence[Dict[str, str]] = ()) -> List[Dict[str, str]]:
        """The documents a model-free run did not give a copy, with the reason the engine recorded: those its preview
        refused (never run), then those the run set aside or that failed a check."""
        last = self.session.last_batch
        out: List[Dict[str, str]] = list(refused)
        for item in getattr(last, "files", []) or []:
            if item.status == "PASS":
                continue
            exc = getattr(item, "exception", None) or {}
            reason = refusal_message(exc) if exc else ("A check failed on the edited copy, so it was not delivered; the run's "
                                           "DIAGNOSTICS.md says which.")
            origin = str(self.origin_of(Path(item.source)))
            if all(e["path"] != origin for e in out):
                out.append({"path": origin, "name": Path(origin).name, "why": WHY_FAILED, "reason": str(reason),
                            "request": request})
        return out

    def template_info(self) -> Dict[str, Any]:
        """What kind of header/footer template the attached template is (``recipes.template_kind``) and, under
        ``body``, the roles "match body formatting to template" reads from it (``template_roles.detect``) — read again
        only when the file changes."""
        from . import recipes, template_roles
        template = self.template()
        if template is None:
            return {**recipes.template_kind(None), "body": template_roles.detect(None)}
        try:
            st = Path(template).stat()
            key = (str(template), st.st_size, st.st_mtime_ns)
        except OSError:
            return {**recipes.template_kind(None), "body": template_roles.detect(None)}
        if self._template_kind is None or self._template_kind[0] != key:
            self._template_kind = (key, {**recipes.template_kind(Path(template)),
                                         "body": template_roles.detect(Path(template))})
        return dict(self._template_kind[1])

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
            kinds = {choice.get("kind") for choice in entry.choices}
            if c.recovery_active or kinds & {"recovery_plan", "recovery_target"}:
                continue
            if "custom_transform" in kinds:
                # the one choice the app takes on its own (``Session.outcome``): nobody picks it, so the chat says
                # Claude's reason and what the method is to do before it is written — never only in the plan's data,
                # which the window does not show (the stress test's F1: Create stood enabled over a method whose
                # proposal no one had read)
                self.say(entry.message, "assistant")
                proposal = next((str(choice.get("detail") or "").strip() for choice in entry.choices
                                 if choice.get("kind") == "custom_transform"), "")
                self.say(CUSTOM_METHOD_WRITTEN + (f" What it is to do, in Claude’s words: “{proposal}”" if proposal
                                                  else ""))
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
        self._say_text_changes()                 # a custom method's words, before the button that approves them
        self._revision += 1
        if self._answered(outcome):
            # an answer with nothing pending: the request is finished with it, and the window rests on the copies
            self._end_exchange(s.turn.message)
            self.stage = "done" if self.results and self.generation else "empty"
            return
        self.stage = {"question": "question", "preview": "preview", "resume": "resume", "auto": "question",
                      "empty": "resume" if self.decisions else "empty"}[outcome.kind]
        if self._request_budget.exhausted_reason and self.stage == "question":
            self.stage = "budget"
        if self.stage == "question":
            self._emit({"type": "question", "questions": list(outcome.questions),
                        "choices": [{"id": c.id, "label": c.label, "words": list(c.words), "detail": c.detail,
                                     "editable": getattr(c, "editable", False)}
                                    for c in outcome.choices], "chips": list(outcome.chips)})

    def _recovery_options(self):
        from .recovery import RecoveryOptions
        return RecoveryOptions(max_cost_usd=None, max_calls=DEFAULT_MAX_CALLS, max_attempts=RECOVERY_ATTEMPTS,
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
                              for k, v in group.items()},
                      "decisions": list(decisions)}       # what Discard puts back on the list with them
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
        if self.mode == "safe" and self.stage != "preview":
            raise ValueError("Press a button to preview a change first.")
        if self.stage == "budget":
            # The user's go-ahead: the same request goes on, its spending kept, with the limit allowed again
            why, spent, limit = self.pause_reason(), self._request_budget.spent_usd, self.spending_limit
            self._request_budget.max_cost_usd = None if why == "unpriced" or limit is None else (spent or 0.0) + limit
            if why == "unpriced":
                self.say("Continuing without a spending check for this request: the price table cannot estimate the "
                         "model that answers.")
            elif why == "limit" and limit is None:
                self.say("Continuing without a spending check for this request: it is off in Settings.")
            elif why == "limit":
                self.say(f"Continuing: this request may spend up to ${limit:.2f} more before the spending check "
                         "asks again.")
            else:
                self.say("Continuing where the work stopped.")
            pending = s.pending_custom_transform
            if pending is not None and pending.get("paused"):
                # the writer goes on from its checkpoint (codegen.generate_op(resume_from=)), not from the brief again
                self.say("The custom method continues where it stopped, with everything it had looked at.")
            self._request_budget.exhausted_reason = self._budget.exhausted_reason = None
            self.stage = "resume"
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
        blocked = s.run_blocked_reason()
        if blocked:
            raise ValueError(blocked)
        held = self._held(hold)
        if held and s.recovery is not None:
            raise ValueError("A document cannot be set aside while files are being checked again; save the ready "
                             "copies first.")
        if self.stage == "preview":
            # what Undo goes back to once this run has delivered (a later save of recovered copies keeps this point)
            self._pending_undo = self._undo_point()
        # the files the preview refused never run (Session._without_refused): once the others run they go on the to-do
        # list with the plan's reason, to come back to (the session keeps them until the request ends: the approval
        # was taken over them)
        refused = self._refused_now(held) if s.recovery is None else []
        # a Safe-mode button's, or any plan approved in Safe mode (a Default-mode preview still waiting when the mode
        # changed): no AI step follows, and what the run leaves untouched waits beside the copies, not on the list
        model_free = s.model_free or self.mode == "safe"
        # Obliterate: no automatic recovery either — a copy whose checks fail is delivered marked unverified, a
        # refused document goes on the to-do list as in Default mode
        no_recovery = model_free or self.mode == "obliterate"
        remaining = None
        if held:
            # the files the preview refused never run (Session._without_refused): a user who sets aside every
            # previewed document has set aside everything there is to create
            remaining = [f for f in s.runnable_files() if f not in held]
            seen = [c["summary"] for g in self._groups() for c in g["changes"]]   # the plan the user looked at
            for path in held:
                self._set_aside(path, seen)
            if not remaining and model_free:
                # a Safe-mode button: there is no conversation to plan them again; they wait for Default mode
                self._sync()
                self._invalidate()
                self.say("You set every document aside, so no copy was created. They wait on your to-do list; "
                         "Default mode can plan them with an AI model.")
                self.save()
                return
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
        request = self.decisions[0] if self.decisions else ""
        # a Safe-mode button's refused documents stay in scope for the next button; they are offered to Default mode
        untouched_before = self._refused_by_preview(outcome.preview, request) if model_free else []
        export = s.export                        # Save clean PDFs: the run's copies are made for their PDFs
        log("Creating and checking the clean copies…" if export else "Creating and checking your edited copies…")
        s.run(False, self.render, log, only=remaining)
        if not model_free:
            self._binned += [self._set_aside(path, seen_by, why=WHY_REFUSED, reason=reason)
                             for path, seen_by, reason in refused]
        if export:
            self._collect_pdfs()
            self.left_untouched = self._left_after_run(request, untouched_before) + self._without_pdf(request)
            self._end_export()
            return
        self.pdfs = {}                           # the last run's PDFs only: this run saved none
        self._collect_results()
        self._emit({"type": "results", "results": self._results_view()})
        if model_free:
            # a Safe-mode button: no recovery (an AI call); what it left untouched is offered to Default mode
            self.left_untouched = self._left_after_run(request, untouched_before)
        elif no_recovery and s.last_batch is not None:
            self._binned += self._bin_failed(s.last_batch.files)
        elif s.last_batch is not None and any(f.status != "PASS" for f in s.last_batch.files):
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
        if self.mode == "obliterate" and s.last_trials is not None:
            # a custom method's file whose run stopped before any copy (the sandbox refused it, the source moved)
            seen = [c["summary"] for g in self._groups() for c in g["changes"]]
            self._binned += [self._set_aside(Path(t.source), seen, why=WHY_FAILED,
                                             reason=f"Its copy could not be made: "
                                                    f"{'; '.join(t.problems[:2]).rstrip('.') or 'the method stopped'}.")
                             for t in s.last_trials if not t.passed]
        if not no_recovery and s.last_trials is not None and any(not f.passed for f in s.last_trials):
            log("Checking the files that still need work…")
            s.resolve_remaining(log, options=self._recovery_options())
            self._settle(log)
            return
        if s.recovery is not None and len(s.recovery.ready) < len(s.recovery.files):
            self.stage = "question"
            self._revision += 1
            self.say("The ready copies are saved. The remaining documents still need attention.")
            return
        if s.pending_layout_recovery and not no_recovery:
            # a plan per layout: the previous layout's copies are saved; the next layout's documents are checked now
            log("Checking the next layout's documents that still need work…")
            s.resolve_next_layout(log, options=self._recovery_options())
            self._settle(log)
            return
        self.stage = "done"
        self._revision += 1
        n = len(self.results)
        if model_free:
            ready = (f"{n} document {'copy is' if n == 1 else 'copies are'} ready. Open them, press another button "
                     "to change them further, or start over from the originals." if n else
                     "No copies were created. Choose another button or start over from the originals.")
            self.say(ready +
                     (f" Left untouched: {', '.join(e['name'] for e in self.left_untouched)} — each one's reason is "
                      "under Left untouched." if self.left_untouched else ""))
        else:
            shaky = sum(1 for k in self.results if k in self.unverified)
            self.say(f"{n} document {'copy is' if n == 1 else 'copies are'} ready. You can open them, describe "
                     f"another change to make on top of them, or start over from the originals." +
                     (f" {shaky} {'was' if shaky == 1 else 'were'} delivered UNVERIFIED: a check failed, and each "
                      f"one's failed checks are beside it." if shaky else ""))
        self._advance_generation()
        binned, self._binned = self._binned, []
        if not model_free:
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
        from .preview import refusals
        seen = [c["summary"] for g in self._groups() for c in g["changes"]]
        reasons = {source.resolve(): exc.get("reason", "") for source, exc in refusals(s.preview)}
        out = []
        for f in refused:
            why = str(reasons.get(Path(f).resolve(), "")).strip().rstrip(".")
            out.append((f, seen, "The plan could not be applied to it" + (f": {why}." if why else ".")))
        return out

    def _bin_failed(self, files) -> List[str]:
        """Run results that did not pass and that nothing will check again: on the to-do list with why (a copy
        delivered unverified in Obliterate mode is a result, not on the list)."""
        seen = [c["summary"] for g in self._groups() for c in g["changes"]]
        out: List[str] = []
        for item in files:
            if item.status == "PASS" or getattr(item, "delivered_to", None):
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
                    self._mark_unverified(origin, getattr(item, "unverified", None))
                elif (getattr(item, "noop", False) and item.status == "PASS" and item.output
                      and Path(item.output).is_file()):
                    # The engine verifies and saves identical copies too, but intentionally leaves delivered_to
                    # empty to distinguish them from changed documents in its reports.
                    self.results[origin] = str(item.output)
        for item in s.last_trials or []:
            if item.passed and item.output:
                self.results[str(self.origin_of(item.source))] = str(item.output)
                self._mark_unverified(str(self.origin_of(item.source)), getattr(item, "unverified", None))

    def _collect_pdfs(self) -> None:
        """The PDFs Save clean PDFs saved, keyed by the ATTACHED original, as the copies are (``_collect_results``)."""
        done = self.session.last_pdfs
        self.pdfs = {str(self.origin_of(source)): str(pdf) for source, pdf in (done.pdfs.items() if done else [])}

    def _pdf_marks(self) -> Dict[str, List[str]]:
        """The UNVERIFIED marks each PDF carries: its clean copy passed, but it was checked against a working copy
        delivered UNVERIFIED (Obliterate mode), so that copy's marks stay on the PDF as an earlier copy's, as they do
        on every later copy built on it (``_mark_unverified``)."""
        return {origin: [m if m.startswith(EARLIER_COPY) else EARLIER_COPY + m for m in self.unverified[origin]]
                for origin in self.pdfs if self.unverified.get(origin)}

    def _without_pdf(self, request: str) -> List[Dict[str, str]]:
        """The documents whose clean copy passed but whose PDF could not be made, with the renderer's reason."""
        done = self.session.last_pdfs
        out: List[Dict[str, str]] = []
        for source, why in (done.failed.items() if done else []):
            origin = str(self.origin_of(source))
            out.append({"path": origin, "name": Path(origin).name, "why": WHY_FAILED,
                        "reason": f"{NO_PDF}: {str(why).rstrip('.')}.", "request": request})
        return out

    def _end_export(self) -> None:
        """Save clean PDFs is done: the PDFs are the result, and the working set is as it was — the clean copies the
        PDFs were made from stay in the run folder, but the next request works on the documents as they were, their
        comments and tracked changes included. So nothing joins the history the model hears, no copy becomes a working
        copy, and no undo point is kept (there is nothing to undo)."""
        s = self.session
        self.stage = "done"
        self._revision += 1
        n, done = len(self.pdfs), s.last_pdfs
        made = f", made by {done.renderer_name}" if done is not None and done.renderer_name else ""
        shaky = len(self._pdf_marks())
        if n:
            ready = (f"{n} clean {'PDF is' if n == 1 else 'PDFs are'} ready{made}: every comment removed and every "
                     f"tracked change accepted, each from a copy the checks passed. Your Word documents are as they "
                     f"were; the next button works on them, comments and tracked changes included." +
                     (f" {shaky} {'is' if shaky == 1 else 'are'} marked UNVERIFIED: {'its' if shaky == 1 else 'their'}"
                      f" Word document was delivered UNVERIFIED earlier, and the checks that failed are beside "
                      f"{'it' if shaky == 1 else 'them'}." if shaky else ""))
        else:
            ready = "No PDF was saved. Choose another button or start over from the originals."
        self.say(ready + (f" Without a PDF: {', '.join(e['name'] for e in self.left_untouched)} — each one's reason is "
                          "under Left untouched." if self.left_untouched else ""))
        self._pending_undo, self._before_button = None, None
        self.decisions = []
        self._binned = []
        self._sync()                             # a document the ✗ put on the to-do list leaves the next button's scope
        s._set_outcome()
        s.end_conversation()

    def pdf_info(self) -> Dict[str, Any]:
        """Whether this computer can make PDFs (Save clean PDFs' ``needs``), and with what: asked once, again at each
        button press."""
        if self._pdf_info is None:
            from .pdf_export import RENDERER_NAMES, renderer
            backend, why = renderer()
            self._pdf_info = {"available": backend is not None, "renderer": RENDERER_NAMES.get(backend or "", ""),
                              "reason": "" if backend else
                              "Saving PDFs needs Microsoft Word or LibreOffice on this computer, and neither was found."}
        return dict(self._pdf_info)

    def _mark_unverified(self, origin: str, failed) -> None:
        """A copy delivered with failed checks (Obliterate mode) keeps them beside it, and so does every later copy
        that builds on it: a later PASS was checked against the unverified copy, not against the original, so from
        then on the mark says "in an earlier copy" and leaves only with Start over, Undo or the document itself."""
        earlier = [x if x.startswith(EARLIER_COPY) else EARLIER_COPY + x for x in self.unverified.get(origin, [])]
        fresh = [str(x) for x in failed or []]
        marks = fresh + [x for x in earlier if x not in fresh and x[len(EARLIER_COPY):] not in fresh]
        if marks:
            self.unverified[origin] = marks

    def _say_text_changes(self):
        """A custom method's preview has no plan sentence to say what it changes (the window shows no plan card, and
        the method's words are checked against its own plan only), so before the approval button the chat says the
        words its sample copies add and remove — the independent checker's reading of each copy (V2), never the
        method's or the model's (``preview.text_changes_of``) — or that no words change; once per preview. Before
        them, a method kept with failed checks, or with a check the writer kept with a reason (Obliterate), has its
        verdict said too (``codegen.samples_verdict``): its copies are delivered marked UNVERIFIED, and that is said
        where the user reads, not only in the notes the model hears."""
        from .codegen import SAMPLES_KEPT, SAMPLES_PASSED, SAMPLES_UNVERIFIED, kept_lines, samples_verdict, \
            unverified_lines
        from .preview import text_changes_lines
        s = self.session
        gp = s.generated_preview if s.generated is not None else None
        if gp is None or gp is self._words_said_for:
            return
        self._words_said_for = gp
        if unverified_lines(s.generated) or kept_lines(s.generated):
            heads = (SAMPLES_PASSED, SAMPLES_UNVERIFIED, SAMPLES_KEPT)
            self.say("\n\n".join(line if line in heads else f"• {line}" for line in samples_verdict(s.generated)))
        lines = text_changes_lines(getattr(gp, "samples", None) or [])
        if lines:
            self.say("\n\n".join(lines))         # a paragraph each: the page shows a note of several as a block

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
            # a method kept with failed checks (Obliterate) is never called passed: the details name the checks
            from .codegen import samples_verdict
            changes = [{"title": "Requested changes", "summary": s.generated.proposal,
                        "details": [s.generated.request, *samples_verdict(s.generated)]}]
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
        then those the preview refused (the plan, at any of its steps), each with its reason."""
        if preview is None:
            return []
        from .preview import refusals
        why = self.session.left_out_reasons
        items = [{"name": Path(path).name, "reason": refusal_message({"reason": why.get(str(path), LEFT_OUT_REASON)})} for path in self.session.left_out]
        items += [{"name": source.name, "reason": refusal_message(exc)} for source, exc in refusals(preview)]
        return items

    def _delivered(self) -> List[Path]:
        """The documents whose copies a partial save during recovery already delivered: their pages and findings leave
        while the conversation goes on about the rest."""
        recovery = self.session.recovery
        return [Path(f.source) for f in recovery.files if f.delivered_to] if recovery is not None else []

    def _undelivered_samples(self, preview) -> list:
        """The samples the pages show: not one whose copy a partial save delivered, nor one a later step refused (it
        waits under Left untouched, unchanged)."""
        from .preview import sample_refused
        delivered = {str(path) for path in self._delivered()}
        return [sv for sv in getattr(preview, "samples", []) or []
                if str(sv.source) not in delivered and not sample_refused(sv)]

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
                             "pages_before": sv.pages_before, "pages_after": sv.pages_after, "passed": sv.passed,
                             "failures": failed_preview_checks(sv), "geometry": sv.geometry,
                             "text_changes": getattr(sv, "text_changes", None)}
                            for sv in samples],
                "occurrences": self._occurrences_view(preview),
                "warnings": list(dict.fromkeys(getattr(preview, "warnings", []) or [])),
                "blocking": any(f.get("severity") == "block"
                                for f in self._undelivered_findings(getattr(preview, "review", None)))}

    def _results_view(self) -> List[Dict[str, Any]]:
        return [{"source": source, "output": output, "name": Path(output).name,
                 "unverified": list(self.unverified.get(source, []))}
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
            if not questions and (not choices or any(c.kind == "recovery_plan" for c in choices)):
                # A reply with nothing to pick (plain words, a failure) is the chat's, said there once; the card
                # carries it only when the chat did not say that reply (a recovery turn's), whatever an older
                # bubble said. An outcome plan's message (the outcomes in full, the planner's reasons and risks) is
                # the card's too: the user reads it above the choice that carries it out.
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
        batch_plan = getattr(preview, "batch_plan", None)
        labels = {"preview": "Create edited copies", "resume": "Continue", "budget": "Continue"}
        if self.mode == "safe":
            labels = {k: v for k, v in labels.items() if k == "preview"}   # Safe mode continues no conversation
        if blocking and self.stage == "preview":
            labels["preview"] = "I reviewed the flagged pages — create copies"
        elif s.export == "pdf" and "preview" in labels:
            labels["preview"] = "Save clean PDFs"
        planned = getattr(batch_plan, "files", None)
        if s.model_free and self.stage == "preview" and planned and len(set_aside) >= len(planned):
            labels.pop("preview", None)              # a button no document can take: there is nothing to create
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
        states = self.export_states()
        return {"stage": self.stage, "mode": self.mode, "messages": list(self.messages),
                "left_untouched": [{k: e[k] for k in ("path", "name", "reason")} for e in self.left_untouched]
                if self.stage != "preview" else [],
                "stages": stages if groups else 0,
                "chips": chips,
                "working": {"generation": self.generation, "from_originals": not self.working,
                            "can_start_over": can_start_over,
                            "undo": (self.undo_points[-1].get("request") or "the last change") if self.undo_points
                            else "",
                            "undo_blocked": self._undo_blocked() if self.undo_points else ""},
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
                "occurrences": self._occurrences_view(pages) if pages is not None else [],
                "warnings": list(dict.fromkeys(warnings)), "results": dict(self.results),
                "pdfs": dict(self.pdfs), "pdf_unverified": self._pdf_marks(),
                "export": s.export if self.stage == "preview" else "",
                "unverified": {k: list(v) for k, v in self.unverified.items()},
                "exported": {k: dict(v) for k, v in self.exported.items()}, "export_folder": self.export_folder,
                "export_state": states,
                "unsaved": [path for path, state in states.items() if state != "saved"],
                "copies": len(self.working) + len(self.pdfs),
                "action": labels.get(self.stage, ""), "token": self.action_token(),
                "blocked_reason": s.run_blocked_reason() if self.stage == "preview" else "",
                "budget_paused": self.stage == "budget", "pause_reason": self.pause_reason(),
                "discard": self.discard_kind(),
                "cost": self._budget.spent_usd,
                "blocking": blocking}

    # ---- Export: the one place a copy is written where the user chose ----
    def _digest(self, path: str, *, fresh: bool = False) -> Optional[str]:
        """The md5 of a file's bytes as they are now (a copy's, or the file Export saved it as); None when the file
        cannot be read. Taken again whenever the file's size or mtime moved since the last fingerprint, and always
        with ``fresh`` (the close question): a file rewritten to the same size and time is caught where it
        matters."""
        try:
            st = os.stat(path)
            cached = self._digests.get(path)
            if not fresh and cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
                return cached[2]
            md5 = _md5(Path(path).read_bytes())
        except OSError:
            self._digests.pop(path, None)
            return None
        self._digests[path] = (st.st_size, st.st_mtime_ns, md5)
        return md5

    def export_states(self, *, fresh: bool = False) -> Dict[str, str]:
        """Each working copy and clean PDF (its path) with whether it is saved: ``saved`` when a file with its
        current bytes sits where Export put it (the copy's bytes, the saved file's bytes and the fingerprint Export
        recorded all agree); ``changed`` when the copy's bytes moved since (a copy opened from the window and edited
        in Word) or cannot be read; ``replaced`` when the copy is as it was but the saved file no longer holds its
        bytes (written over, truncated or edited since Export); ``gone`` when the saved file is no longer there;
        ``unsaved`` when Export never saved it. Only ``saved`` survives the close. Read over copies of the dicts, so
        the close question can be asked while a job is still running; ``fresh`` reads every copy and every saved
        file again."""
        states: Dict[str, str] = {}
        for path in [*dict(self.working).values(), *dict(self.pdfs).values()]:
            entry = self.exported.get(path)
            if not entry:
                states[path] = "unsaved"
            elif not Path(entry["to"]).is_file():
                states[path] = "gone"
            elif not entry["md5"] or self._digest(path, fresh=fresh) != entry["md5"]:
                states[path] = "changed"
            elif self._digest(entry["to"], fresh=fresh) != entry["md5"]:
                states[path] = "replaced"
            else:
                states[path] = "saved"
        return states

    def unsaved_copies(self, *, fresh: bool = False) -> List[str]:
        """The working copies and the last run's clean PDFs that are not saved as they are now (their paths): never
        saved by Export, changed since, their saved file changed since, or gone — what closing the window would
        delete (``export_states``)."""
        return [path for path, state in self.export_states(fresh=fresh).items() if state != "saved"]

    def export(self, folder) -> Dict[str, Any]:
        """Export → Save the copies…: every copy the next request builds on (the working set, one per attached
        original) and every clean PDF of the last Save clean PDFs run, written into ``folder`` under its planned
        name, each through ``guard.write_new``. All or nothing:
        every copy is read and every destination settled before the first write. A destination that is one of the
        inputs (an attached file, a working copy) refuses the export, and so does a folder inside the app's scratch
        tree (the window's own scratch folder or any other: a copy saved there would go with it); a file already
        there with the same bytes is that copy, saved (nothing written); one with other bytes is left alone and the
        copy is numbered beside it (so a copy changed since it was saved lands beside the file saved before, which
        is the user's and never written over). Each copy's destination is recorded with the md5 of the bytes
        written: the copy stays saved while its bytes are those (``export_states``). Returns ``{"folder", "saved",
        "already"}``."""
        from . import guard
        from .settings import scratch_root
        folder = Path(folder)
        files = [*self.working.values(), *self.pdfs.values()]
        if not files:
            raise ValueError("There are no edited copies or clean PDFs to save yet.")
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder.")
        folder = folder.resolve()
        for disposable in (self.session.out_root, scratch_root()):
            root = _same_file_key(disposable)
            if _same_file_key(folder) == root or _same_file_key(folder).startswith(root.rstrip("\\/") + os.sep):
                raise ValueError(f"{folder} is inside the app's scratch folder, which is deleted when the window "
                                 "closes; nothing was saved. Choose a folder of your own.")
        inputs = set()
        for a in self.attachments:
            inputs.add(_same_file_key(a.path))
        for original, current in self.working.items():
            inputs.add(_same_file_key(original))
            inputs.add(_same_file_key(current))
        for pdf in self.pdfs.values():
            inputs.add(_same_file_key(pdf))
        plan: List[tuple] = []                       # (copy path, destination, bytes, already there)
        taken: set = set()
        for current in files:
            try:
                data = Path(current).read_bytes()
            except OSError as exc:
                raise ValueError(f"A copy is no longer where it was made ({Path(current).name}: {exc}); create the "
                                 "copies again before saving them.") from exc
            name = Path(current).name
            dest, n = folder / name, 2
            while True:
                key = _same_file_key(dest)
                if key in inputs:
                    raise ValueError(f"{dest} is one of the files this conversation works on; nothing was saved. "
                                     "Choose another folder.")
                if key in taken:
                    pass
                elif not dest.exists():
                    break
                elif dest.is_file() and dest.read_bytes() == data:
                    break                          # the same copy, saved already
                dest, n = folder / f"{Path(name).stem} ({n}){Path(name).suffix}", n + 1
            taken.add(_same_file_key(dest))
            plan.append((current, dest, data, dest.exists()))
        saved = already = 0
        for current, dest, data, there in plan:
            if there:
                already += 1
            else:
                try:
                    guard.write_new(dest, data)
                except OSError as exc:
                    self.save()
                    raise ValueError(f"{dest.name} could not be written to {folder} ({exc}); "
                                     f"{saved} cop{'y was' if saved == 1 else 'ies were'} saved before it.") from exc
                saved += 1
            self.exported[current] = {"to": str(dest), "md5": _md5(data)}
            self._digest(current, fresh=True)      # the fingerprint of what was just read, for the next view
        self.export_folder = str(folder)
        docx = sum(1 for current, *_ in plan if Path(current).suffix.lower() != ".pdf")
        pdfs = len(plan) - docx
        what = " and ".join(part for part in (f"{docx} cop{'y' if docx == 1 else 'ies'}" if docx else "",
                                              f"{pdfs} clean PDF{'' if pdfs == 1 else 's'}" if pdfs else "") if part)
        self.say(f"Saved {what} to {folder}" + (f" ({already} already there, unchanged)" if already else "") + ".")
        made = {"folder": str(folder), "saved": saved, "already": already}
        self.save()
        return made

    # ---- the journal ----
    def save(self, *, in_progress=False):
        if not self.journal:
            return
        atomic_json(self.journal, {
            "version": 2,
            "attachments": [a.to_dict() for a in self.attachments],
            "messages": self.messages, "decisions": self.decisions, "word_sources": self.word_sources,
            "exchanges": self.exchanges,
            "results": self.results, "unverified": self.unverified, "pdfs": self.pdfs,
            "recovery": str(self.session.recovery.out_dir) if self.session.recovery else
                        str(self.session.recovery_checkpoint) if self.session.recovery_checkpoint else self._resume_recovery,
            "pending_layouts": [str(x) for x in self.session.pending_layout_recovery],
            "working": dict(self.working), "generation": self.generation, "history": list(self.history),
            "applied": self.applied, "fresh": self.fresh, "todo": self.todo, "focus": self.focus,
            "undo": self.undo_points,
            "exported": self.exported, "export_folder": self.export_folder,
            "budget": self._budget.to_dict(), "request": self._request_budget.to_dict(), "in_progress": in_progress})

    def _restore_todo(self, data) -> None:
        """The changes each copy carries, the documents attached since the last run, the to-do list and the pass over
        it, for the documents still attached; a malformed record is refused. A journal from before per-copy histories
        had every copy carry every change, and one from before ``fresh`` counted every original among copies as
        attached since the last run."""
        strings = lambda value: isinstance(value, list) and all(isinstance(x, str) for x in value)  # noqa: E731
        applied = data.get("applied")
        if applied is None:
            self.applied = {k: list(self.history) for k in self.working}
        elif isinstance(applied, dict) and all(isinstance(k, str) and strings(v) for k, v in applied.items()):
            self.applied = {k: list(v) for k, v in applied.items() if k in self.working}
        else:
            raise ValueError("The saved conversation could not be read.")
        known = {str(a.path) for a in self.attachments if not a.template and not a.reference}
        fresh = data.get("fresh")
        if fresh is None:
            self.fresh = [str(a.path) for a in self.attachments if str(a.path) in known
                          and str(a.path) not in self.working] if self.working else []
        elif strings(fresh):
            self.fresh = [k for k in fresh if k in known and k not in self.working]
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
                                      and strings(focus.get("seen", [])) and strings(focus.get("decisions", []))
                                      and isinstance(focus.get("why", {}), dict)
                                      and all(isinstance(k, str) and isinstance(v, dict) and why_ok(v)
                                              for k, v in focus.get("why", {}).items())):
            raise ValueError("The saved conversation could not be read.")
        # a journal from before the to-do list said why holds only documents set aside with the red X
        self.todo = {k: {"name": v["name"], "decisions": list(v.get("decisions", [])), "seen": list(v.get("seen", [])),
                         "why": v.get("why", WHY_HELD), "reason": v.get("reason") or HELD_REASON}
                     for k, v in todo.items() if k in known}
        documents = [d for d in (focus or {}).get("documents", []) if d in known]
        self.focus = ({"documents": documents, "seen": list(focus.get("seen", [])),
                       "why": {k: {"why": v.get("why", WHY_HELD), "reason": v.get("reason") or HELD_REASON}
                               for k, v in focus.get("why", {}).items() if k in documents},
                       "decisions": list(focus.get("decisions", []))}
                      if focus and documents else None)

    @staticmethod
    def _restored_undo(points) -> List[Dict[str, Any]]:
        """The undo points a journal kept, in the shape ``_advance_generation`` wrote them; anything else gives none
        (nothing can be undone rather than something restored wrongly). A copy no longer on disk is found when the
        point is used (``undo``)."""
        text = lambda value: isinstance(value, str)  # noqa: E731
        texts = lambda value: isinstance(value, list) and all(map(text, value))  # noqa: E731
        paths = lambda value: isinstance(value, dict) and all(text(k) and text(v) for k, v in value.items())  # noqa: E731
        lists = lambda value: isinstance(value, dict) and all(text(k) and texts(v) for k, v in value.items())  # noqa: E731
        entry = lambda e: isinstance(e, dict) and all(text(e.get(k, "")) for k in  # noqa: E731
                                                      ("path", "name", "why", "reason", "request")) and text(e.get("path"))
        if not isinstance(points, list) or not all(
                isinstance(p, dict) and text(p.get("request", "")) and paths(p.get("working"))
                and type(p.get("generation")) is int and texts(p.get("history")) and lists(p.get("applied"))
                and paths(p.get("results")) and lists(p.get("unverified")) and texts(p.get("todo_added", []))
                and texts(p.get("fresh", []))
                and paths(p.get("pdfs", {}))
                and isinstance(p.get("left_untouched"), list) and all(map(entry, p["left_untouched"]))
                for p in points):
            return []
        return [{"request": p.get("request", ""), "working": dict(p["working"]), "generation": p["generation"],
                 "history": list(p["history"]), "applied": {k: list(v) for k, v in p["applied"].items()},
                 **({"fresh": list(p["fresh"])} if "fresh" in p else {}),
                 "results": dict(p["results"]), "unverified": {k: list(v) for k, v in p["unverified"].items()},
                 "pdfs": dict(p.get("pdfs", {})),
                 "left_untouched": [{"path": e["path"], "name": e.get("name") or Path(e["path"]).name,
                                     "why": e.get("why", WHY_REFUSED), "reason": e.get("reason", ""),
                                     "request": e.get("request", "")} for e in p["left_untouched"]],
                 "todo_added": list(p.get("todo_added", []))} for p in points]

    @staticmethod
    def _restored_exported(exported) -> Dict[str, Dict[str, str]]:
        """What Export saved where, from the journal: ``{copy: {"to", "md5"}}``. A journal from before the
        fingerprint kept the destination alone (``{copy: "C:/Saved/x.docx"}``): that copy was saved, its hash
        unknown, so the hash is taken from the saved file while it is still there (a saved file changed since, in
        Word, then reads as a copy changed since it was saved: asked about at the close, never deleted unasked); a
        saved file that is gone leaves the hash empty, and the copy is unsaved again either way. A malformed entry
        is dropped."""
        out: Dict[str, Dict[str, str]] = {}
        if not isinstance(exported, dict):
            return out
        for copy, entry in exported.items():
            if isinstance(entry, str):
                entry = {"to": entry, "md5": ""}
            if not (isinstance(copy, str) and isinstance(entry, dict) and isinstance(entry.get("to"), str)
                    and isinstance(entry.get("md5", ""), str)):
                continue
            to, md5 = entry["to"], entry.get("md5", "")
            if not md5:
                try:
                    md5 = _md5(Path(to).read_bytes())
                except OSError:
                    md5 = ""
            out[copy] = {"to": to, "md5": md5}
        return out

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
        # the requests answered without a plan (a journal from before them has none)
        exchanges = data.get("exchanges", [])
        if not (isinstance(exchanges, list)
                and all(isinstance(e, dict) and isinstance(e.get("asked"), list)
                        and all(isinstance(a, str) for a in e["asked"]) and isinstance(e.get("answered", ""), str)
                        and e.get("discarded", "") in ("", *DISCARDED)
                        for e in exchanges)):
            raise ValueError("The saved conversation could not be read.")
        self.exchanges = [{"asked": list(e["asked"]), "answered": str(e.get("answered", "")),
                           **({"discarded": e["discarded"]} if e.get("discarded") else {})}
                          for e in exchanges][-EXCHANGES_KEPT:]
        self.results = data.get("results", {})
        pdfs = data.get("pdfs", {})
        # the last run's PDFs, while they are where they were saved (a journal from before Save clean PDFs has none)
        self.pdfs = ({k: v for k, v in pdfs.items() if isinstance(k, str) and isinstance(v, str) and Path(v).is_file()}
                     if isinstance(pdfs, dict) else {})
        working = data.get("working", {})
        unverified = data.get("unverified", {})
        # a mark belongs to a working copy (an earlier copy's, carried on) or to a result of the last run
        self.unverified = ({k: [str(x) for x in v] for k, v in unverified.items()
                            if (k in self.results or (isinstance(working, dict) and k in working))
                            and isinstance(v, list) and all(isinstance(x, str) for x in v)}
                           if isinstance(unverified, dict) else {})
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
            self.unverified = {k: v for k, v in self.unverified.items() if k in self.results}
            if lost:
                self.say("The edited copies from your last run could not be found where they were saved, so the next "
                         "change starts from your originals.")
        self._restore_todo(data)
        self.undo_points = self._restored_undo(data.get("undo", [])) if self.working or self.history else []
        self.exported = self._restored_exported(data.get("exported", {}))
        self.export_folder = data.get("export_folder") if isinstance(data.get("export_folder"), str) else ""
        self._sync()
        self._resume_recovery = data.get("recovery")
        layouts = data.get("pending_layouts", [])
        if not isinstance(layouts, list) or not all(isinstance(x, str) for x in layouts):
            raise ValueError("The saved conversation could not be read.")
        self._resume_layouts = list(layouts)
        budget = data.get("budget", {})
        # Retain conservative accounting; never reset spending by reopening the app. A journal from a version with a
        # spending allowance or a call-count pause keeps its call count and spending, never its ceilings or pauses.
        self._budget = self._new_budget()
        self._budget.calls = int(budget.get("calls", 0))
        self._budget._reserved_tokens = int(budget.get("accounted_tokens", 0))
        cost = budget.get("estimated_cost_usd", 0)
        self._budget._reserved_cost_usd = float(cost or 0)
        # the request that was open: its spending, and a pause at the spending check, under today's limit
        request = data.get("request") if isinstance(data.get("request"), dict) else {}
        self._request_budget = self._new_request()
        number = lambda v: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else 0.0
        self._request_budget._reserved_cost_usd = number(request.get("estimated_cost_usd"))
        if self._request_budget.max_cost_usd is not None:   # a go-ahead given before the app closed still stands
            self._request_budget.max_cost_usd = max(self._request_budget.max_cost_usd, number(request.get("max_cost_usd")))
        exhausted = request.get("exhausted_reason")
        self._request_budget.exhausted_reason = exhausted if isinstance(exhausted, str) and exhausted else None
        if self.spending_limit is None and self._request_budget.exhausted_reason != INTERRUPTED:
            self._request_budget.exhausted_reason = None          # the check is off now: nothing to ask
        if data.get("in_progress") or cost is None:
            # The app closed while work ran: its last call may have cost more than was recorded; ask before going on.
            self._request_budget.exhausted_reason = INTERRUPTED
        self.stage = ("budget" if self._request_budget.exhausted_reason else "resume" if self.decisions else
                      "done" if self.results and self.generation else "empty")
        if self.decisions:
            self.say("Your conversation is restored. I’ll recheck the documents before making further changes.")
        elif self.generation:
            self.say("Your conversation is restored. The edited copies from your last run are what the next change "
                     "builds on; say “Start over from the originals” to go back to your files.")
        return True
