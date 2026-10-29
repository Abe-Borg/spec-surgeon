"""The conversation — a request turned into an EditSpec by talking it through with Claude (decided 2026-10-15: "don't
give up on the user").

One ``Conversation`` per request. It starts like the old one-shot compile (the request, the batch's profiles, the op
catalog — see compiler.py) and then keeps going: when Claude needs something from the user (``ask_user``), when it has
submitted a spec the user wants corrected after the preview, or when no operation fits, the user replies — in their
own words or by picking one of the choices Claude offered — and the same conversation continues.

What makes it safe:

* **Append-only history.** ``messages`` is only ever appended to (the model's thinking blocks are tied to the exact
  history that produced them). A failed call appends nothing; the next reply simply adds the user's new text. The
  one removal: a reply Claude declined to answer leaves again (``_take_back``), while nothing yet follows it. An API
  error (``llm.is_api_error``) ends the turn as ``failed`` with its kind, HTTP status and request id; anything else is a
  defect and propagates — it appends nothing either, so the history still pairs.
* **Every tool call is answered.** Each ``tool_use`` gets exactly one ``tool_result`` in the very next user message
  (``check_pairing`` runs before every request). Results the app cannot give yet — the answer to ``ask_user``, what
  the preview of an accepted spec showed — wait in ``pending`` and go out first in the next user message.
* **Approved words only.** ``sources`` — the words a spec may place in the documents — are the request, the user's
  ``--answer`` lines and replies, and the exact ``wording`` of a choice the user picked — as offered, or as the user
  edited it before picking (``Choice.editable``: a choice of Claude's offering exactly one wording; the edited text
  replaces the draft, which is then never approved, and passes the same ``clean_wording`` check). Never Claude's own text: its
  understanding, messages, choice labels, notes or the custom-transform brief. A wording with a character the user
  cannot see (``invisible``) is never offered. conversation.json records the sources as ``approved_words`` (beside
  ``answers``), and the spec the app gets carries the user's ``request``, never the text Claude wrote there (the
  advisor and the next pass count the spec's request as approved words).
* **Choices are built by code.** Claude may offer ``clarify``/``alternative``/``rephrase`` choices about the
  document result; only the app adds the ``custom_transform`` choice, and only when Claude declared the request within
  ``CUSTOM_TRANSFORM_LIMITS``. Claude never offers a choice of implementation (a recipe, an operation, a snippet): the
  engine picks the operations itself and the user is asked only about the outcome.

``mode="compile"`` is the one-shot behaviour ``compiler.compile_request`` keeps (a plain-text answer gets one nudge,
then fails); ``mode="chat"`` (the window) treats Claude's plain text as a message to the
user and offers the ``view_pages`` tool.

Two records, never read back to rebuild the history: ``entries`` is what the user saw (the chat); ``exchange`` is the
model exchange for diagnostics (``llm.response_entry`` / ``results_entry`` / ``error_entry``: every request as sent,
every answer with its stop reason, serving model, tokens, time and request id, every tool result — page images left
out). Both go into conversation.json; ``compile_result`` hands ``exchange`` on as ``CompileResult.transcript``
(compile.json).
"""
from __future__ import annotations

import copy
import json
import re
import hashlib
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import compiler, diagnostics as diag, llm
from .resources import (DEFAULT_CONTEXT_TOKENS, DEFAULT_EFFORT, DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_OUTPUT_TOKENS,
                        DEFAULT_STALL_REPETITIONS, InvestigationProgress, positive_integer)

CHOICE_KINDS_FROM_CLAUDE = ("clarify", "alternative", "rephrase")   # built from Claude's own label and wording
APP_CHOICE_KINDS = ("custom_transform", "recovery_plan", "recovery_target")   # carried out by the app, never sent to
# Claude as a reply: a custom transform the app takes on its own, an outcome plan once the user picks it (its words
# beneath the choice), a recovery target by the window's numbered question
MAX_CALLS_PER_TURN = compiler.MAX_TURNS
NUDGE = "Finish by calling submit_editspec (or ask_user)."


def declined_text(resp, *, taken_back: bool) -> str:
    """What the window says when Claude declines to answer (``stop_reason`` refusal): in plain words, never the
    provider's policy text (that goes to the turn's errors and the records)."""
    category = getattr(getattr(resp, "stop_details", None), "category", None)
    why = ("Anthropic's checks read it as asking Claude to reveal its own reasoning. Ask about the documents or the "
           "plan instead, not about how Claude thinks." if category == "reasoning_extraction" else
           f"Anthropic's checks declined it ({category})." if category else "Anthropic's checks declined it.")
    if taken_back:
        return (f"Claude declined to answer that message, so it was taken back out of the conversation; nothing else "
                f"changed. {why} Rephrase it and send it again.")
    return f"Claude declined to go on with this request; nothing was changed. {why} Rephrase your last message."
# the tool result an accepted spec gets (the user's next message, if any, says what the preview found)
ACCEPTED_RESULT = ("accepted — the app shows it to the user; their next message (if any) follows with what the preview "
                   "found")


class ProtocolError(RuntimeError):
    """The history would break the tool_use / tool_result pairing — a bug in this module, never sent to the API."""




@dataclass
class Choice:
    id: str
    label: str
    kind: str
    wording: Optional[str] = None                 # a choice of Claude's: the one exact wording picking approves
    detail: str = ""
    words: List[str] = field(default_factory=list)   # EVERY word picking approves, each its own source: [wording] for
                                                     # Claude's choices, the placed words of a recipe
    item_hash: str = ""                           # an app choice: the hash of the snapshot it was offered with
    snapshot: Optional[Dict[str, Any]] = None     # a recipe choice: the spec as validated when offered; a snippet
                                                  # choice: the snippet as read when offered (neither is recorded)

    def __post_init__(self) -> None:
        if self.wording and not self.words:
            self.words = [self.wording]

    @property
    def editable(self) -> bool:
        """A choice of Claude's that offers exactly one wording: the user may edit it before picking it, and the edited
        text is then the approved wording (``edited_wording``)."""
        return self.kind in CHOICE_KINDS_FROM_CLAUDE and bool(self.wording) and self.words == [self.wording]

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "wording": self.wording, "detail": self.detail,
                "words": list(self.words), "item_hash": self.item_hash, "editable": self.editable}

    def exact_words(self) -> str:
        return ", ".join(f"«{w}»" for w in self.words)

    def describe(self) -> str:
        """The choice as CompileResult shows it — the exact words included."""
        return self.label + (f" — exact words: {self.exact_words()}" if self.words else "")


@dataclass
class Turn:
    status: str                                   # ok | questions | unsupported | failed
    message: str = ""                             # Claude's words to the user
    understanding: str = ""                       # what Claude understood the request to mean
    questions: List[str] = field(default_factory=list)
    choices: List[Choice] = field(default_factory=list)
    spec: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    images: List[str] = field(default_factory=list)  # pages Claude looked at during this turn
    custom_transform: str = ""                    # "possible" | "outside_limits" | "" (unsupported turns)
    custom_transform_reason: str = ""
    chips: List[str] = field(default_factory=list)  # suggested replies the user may tap (plain text, no approval)
    # the documents the user left out of this request (``leave_out``, absolute paths): the plan covers the others
    leave_out: List[str] = field(default_factory=list)

    def needs_user(self) -> bool:
        return self.status in ("questions", "unsupported", "failed")

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "message": self.message, "understanding": self.understanding,
                "questions": self.questions, "choices": [c.to_dict() for c in self.choices], "spec": self.spec,
                "errors": self.errors, "images": self.images, "custom_transform": self.custom_transform,
                "custom_transform_reason": self.custom_transform_reason, "chips": list(self.chips),
                "leave_out": list(self.leave_out)}


@dataclass
class Entry:
    """One line of the transcript the user sees (the window's chat, conversation.json)."""
    role: str                                     # user | claude | app
    text: str
    status: str = ""
    understanding: str = ""
    choices: List[Dict[str, Any]] = field(default_factory=list)
    images: List[str] = field(default_factory=list)
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    chips: List[str] = field(default_factory=list)
    # Claude's words alone, without the questions ``text`` lists after them: the window's chat says these and shows
    # the questions once, in their own card (a question of several lines never leaks into the bubble).
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "text": self.text, "status": self.status, "understanding": self.understanding,
                "choices": self.choices, "images": self.images, "at": self.at, "chips": list(self.chips)}


def _bid(block) -> str:
    return getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else "") or ""


def _content_list(msg: Dict[str, Any]) -> List[Any]:
    c = msg.get("content")
    return c if isinstance(c, list) else []


def check_pairing(messages: Sequence[Dict[str, Any]]) -> None:
    """Raise ProtocolError unless the history is one the API accepts: it ends with a user message, and every assistant
    message with tool calls is followed by a user message that opens with exactly one tool_result per call."""
    if not messages or messages[-1].get("role") != "user":
        raise ProtocolError("the history must end with a user message (a trailing assistant turn is a prefill)")
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        ids = [_bid(b) for b in _content_list(m) if llm.block_type(b) == "tool_use"]
        if not ids:
            continue
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        if nxt is None or nxt.get("role") != "user":
            raise ProtocolError(f"tool calls {ids} are not answered by the next message")
        got: List[str] = []
        for b in _content_list(nxt):
            if llm.block_type(b) != "tool_result":
                break
            got.append(b.get("tool_use_id") if isinstance(b, dict) else getattr(b, "tool_use_id", ""))
        if sorted(got) != sorted(ids):
            raise ProtocolError(f"tool calls {ids} answered by {got}")


# Unicode's Default_Ignorable_Code_Point ranges (DerivedCoreProperties.txt, Unicode 15): characters that render as
# nothing — some are letters or marks (U+3164 HANGUL FILLER is Lo, U+FE0F VARIATION SELECTOR-16 and U+034F COMBINING
# GRAPHEME JOINER are Mn), so the category test alone misses them — plus three that render blank without being
# ignorable: U+2800 BRAILLE PATTERN BLANK (So), U+1D159 MUSICAL SYMBOL NULL NOTEHEAD (So) and U+16FE4 KHITAN SMALL
# SCRIPT FILLER (Mn). The Zs spaces (U+00A0 NO-BREAK SPACE, U+2000–U+200A, U+3000…) are not here: they show as a space,
# and spec text legitimately holds them.
INVISIBLE_RANGES = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
                    (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164),
                    (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3),
                    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
                    (0x2800, 0x2800), (0x1D159, 0x1D159), (0x16FE4, 0x16FE4))
# control, format, line/paragraph separators, surrogates, private use, unassigned: none shows reliably as itself
INVISIBLE_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp")


def invisible(c: str) -> bool:
    """A character the user cannot see as itself in the chat (tab aside: it shows as a gap and is ordinary text)."""
    if c == "\t":
        return False
    o = ord(c)
    return unicodedata.category(c) in INVISIBLE_CATEGORIES or any(a <= o <= b for a, b in INVISIBLE_RANGES)


def clean_wording(w: Any) -> Optional[str]:
    """Exact wording offered in a choice, or None when there is none. Control characters, line/paragraph separators and
    every character that renders as nothing or blank (zero-width spaces, bidi marks, Hangul fillers, variation
    selectors, the blank braille pattern, the null notehead, the Khitan filler…) are refused: the user must see every
    character they approve. Spaces that show as a space (tab, no-break space, the other Zs spaces) are kept."""
    if w is None:
        return None
    if not isinstance(w, str) or not w.strip():
        raise ValueError("wording must be non-empty text")
    bad = [c for c in w if invisible(c)]
    if bad:
        raise ValueError(f"wording contains invisible or control characters {[hex(ord(c)) for c in bad][:5]}")
    return w


WORDING_RULE = ("The wording must be one line of visible characters (no line breaks, no invisible characters) and "
                "cannot be empty.")


def edited_wording(choice: Choice, wording: Optional[str]) -> Optional[str]:
    """The words the user approves by picking ``choice`` with ``wording`` typed over its draft: None when there is no
    edit (``wording`` None, or the draft unchanged once the outer spaces go); else the edited text, which must pass the
    rule every offered wording passes (``clean_wording``: one line, nothing invisible). ValueError with a plain sentence
    for a choice that cannot be edited or a wording that breaks the rule."""
    if wording is None:
        return None
    if not choice.editable:
        raise ValueError("Only a choice that offers one wording can have its wording edited.")
    if not isinstance(wording, str):
        raise ValueError(WORDING_RULE)
    text = wording.strip(" ")
    try:
        text = clean_wording(text)
    except ValueError:
        raise ValueError(WORDING_RULE) from None
    return None if text == choice.wording else text


def _chips(raw: Any, errors: List[str]) -> List[str]:
    """Suggested replies as the model offered them: short plain strings, at most five, never empty; anything else
    is dropped with a note in the turn's errors (never a failed turn)."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        errors.append("the suggested replies were dropped: expected a list of short strings")
        return []
    out: List[str] = []
    for item in raw:
        text = str(item).strip() if isinstance(item, (str, int, float)) else ""
        if text and len(text) <= 120 and text not in out:
            out.append(text)
    return out[:5]


# What a suggested reply may say besides a choice's own words and still say nothing else (``restates``).
_FILLER = frozenset("a an and as at be by for from i i'd i'll i'm i've in is it it's its me my of on or so that the "
                    "then this to we we'll with you your".split())
_NEGATIONS = frozenset("no not don't dont never nothing none neither nor without isn't aren't won't can't".split())
_WORD = re.compile(r"[A-Za-z0-9]+(?:['’][A-Za-z]+)?")


def _reply_words(text: str):
    """A text's words in lower case, and its names: every number, and every capitalised word after the first that is
    not "I" ("Valves", "Word", "21", "05")."""
    tokens = _WORD.findall(text)
    words = [t.lower().replace("’", "'") for t in tokens]
    names = {w for i, (t, w) in enumerate(zip(tokens, words))
             if t[0].isdigit() or (i and t[0].isupper() and w.split("'")[0] != "i")}
    return words, names


def _in_order(said: List[str], offered: List[str]) -> int:
    """How many of ``said``'s words ``offered`` carries in the same order, with every negation of either matched by
    one of the other's in that order (a negation is never skipped); -1 when the negations cannot be matched."""
    best = [[-1] * (len(offered) + 1) for _ in range(len(said) + 1)]
    best[0][0] = 0
    for i in range(len(said) + 1):
        for j in range(len(offered) + 1):
            if i and j and said[i - 1] == offered[j - 1] and best[i - 1][j - 1] >= 0:
                best[i][j] = best[i - 1][j - 1] + 1
            if i and said[i - 1] not in _NEGATIONS:
                best[i][j] = max(best[i][j], best[i - 1][j])
            if j and offered[j - 1] not in _NEGATIONS:
                best[i][j] = max(best[i][j], best[i][j - 1])
    return best[-1][-1]


def restates(chip: str, label: str) -> bool:
    """True when a suggested reply says a choice's label again in other words, as "Do everything else now, I'll
    accept in Word" beside the choice "Do everything else on all four now; leave the tracked changes in the three
    files for me to accept in Word" did (the owner's report, 2026-10-29: every answer showed twice). Its words other
    than small ones (``_FILLER``) are the label's in the label's order, all but one in five, with the same negations
    in the same places (``_in_order``); and it names exactly what the label names (each number and capitalised name
    of either is a word of the other). A reply that names another document or number, says the opposite, or puts
    the same words to other targets ("Keep the headers and change the footers") is a different answer and stays."""
    words, names = _reply_words(chip)
    label_words, label_names = _reply_words(label)
    content = [w for w in words if w not in _FILLER]
    if not content or not names <= set(label_words) or not label_names <= set(words):
        return False
    return 5 * _in_order(content, [w for w in label_words if w not in _FILLER]) >= 4 * len(content)


def _texts(raw: Any, what: str, errors: List[str]) -> List[str]:
    """A list of non-empty texts from what Claude sent where the tool schema asks for an array of strings: a single
    string is one item (never one item per character), anything else is dropped — and the wrong shape is said."""
    if raw is None:
        return []
    if isinstance(raw, str):
        errors.append(f"the {what} came as one text, not a list — shown as one")
        raw = [raw]
    elif not isinstance(raw, list):
        errors.append(f"the {what} were dropped: expected a list of texts, got {type(raw).__name__}")
        return []
    out = [x.strip() for x in raw if isinstance(x, str) and x.strip()]
    if any(not isinstance(x, str) for x in raw):
        errors.append(f"{sum(not isinstance(x, str) for x in raw)} of the {what} were not text and were dropped")
    return out


class Conversation:
    def __init__(self, request: str, files: Sequence[Path], template: Optional[Path] = None, client=None,
                 model: Optional[str] = None,
                 prior_failures: Optional[Sequence[Dict[str, Any]]] = None, answers: Sequence[str] = (),
                 work_dir: Optional[Path] = None, mode: str = "chat", log: Optional[Callable[[str], None]] = None,
                 max_calls: Optional[int] = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS,
                 max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS, effort: str = DEFAULT_EFFORT,
                 stall_repetitions: int = DEFAULT_STALL_REPETITIONS, max_image_tokens: Optional[int] = None,
                 max_context_tokens: int = DEFAULT_CONTEXT_TOKENS, analysis_limits=None,
                 continuous_recovery: bool = False, word_sources=None, input_files: Sequence[Path] = (),
                 history: Sequence[str] = (), fresh: Sequence[str] = (), context: str = "",
                 todo: Sequence[str] = (), earlier: Sequence[str] = (), unguarded: bool = False,
                 on_context: Optional[Callable[["Conversation"], None]] = None):
        if mode not in ("chat", "compile"):
            raise ValueError(f"unknown conversation mode {mode!r}")
        self.max_calls = positive_integer("max_calls", MAX_CALLS_PER_TURN if max_calls is None else max_calls)
        self.max_attempts = positive_integer("max_attempts", max_attempts)
        self.stall_repetitions = positive_integer("stall_repetitions", stall_repetitions)
        self.max_context_tokens = positive_integer("max_context_tokens", max_context_tokens)
        self.context_state = {}
        if max_image_tokens is not None:
            positive_integer("max_image_tokens", max_image_tokens)
        # Reuse the call policy's validation, without installing a second spending ledger.
        policy = llm.CallBudget(effort=effort, max_output_tokens=max_output_tokens)
        self.max_output_tokens, self.effort = policy.max_output_tokens, policy.effort
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.files = [Path(f) for f in files]
        self.template = Path(template) if template else None
        self.model = model or compiler.DEFAULT_MODEL
        self.mode = mode
        self.work_dir = Path(work_dir) if work_dir else None
        self.log = log or (lambda m: None)
        self._client = client
        self._client_given = client is not None
        # Pictures and tables the user attached for the edit to use (INPUT FILES); nothing beside them is discovered.
        self.batch = compiler._Batch(self.files, self.template, input_files=[Path(p) for p in input_files])
        self.batch.work_dir = self.work_dir
        self.batch.max_image_tokens = max_image_tokens
        self.batch.analysis_limits = analysis_limits
        self.batch.context_state = self.context_state
        self.answers = list(answers)
        self.history = [str(h) for h in history if str(h).strip()]   # changes already made to the files (continue-on-result)
        self.fresh: List[str] = list(fresh)
        self.context = str(context or "")          # the app's account of how these documents come to be planned
        self.todo = [str(t) for t in todo if str(t).strip()]   # the user's to-do list (not in THE BATCH), one line each
        # the exchanges the window finished with an answer before this request (what was asked, the answer's first
        # sentence), one line each: context the request may refer to, answered already
        self.earlier = [str(e) for e in earlier if str(e).strip()]
        # the documents the user set aside during this conversation (``set_aside``, resolved paths, in order): they
        # left ``files`` at once, and no later submission plans them; with the reason given for each
        self.set_aside: List[str] = []
        self.set_aside_reasons: Dict[str, str] = {}
        # the documents the user left out of the last plan Claude submitted (``leave_out``, resolved paths): an
        # outcome plan that follows a failed preview covers the others alone (``continuous_recovery.propose``)
        self.left_out: List[str] = []
        self.prior_failures = list(prior_failures or [])
        # The words a spec may place: the request, the user's answers, and (on a replay of a saved conversation) the
        # words the user actually typed or accepted — never a choice's label; never an empty source.
        self.sources: List[str] = [w for w in (request, *self.answers) if isinstance(w, str) and w.strip()]
        if word_sources is not None:
            self.sources = list(word_sources)
        # frozen for the whole conversation: rebuilding either between calls would invalidate the cache and the
        # model's earlier thinking
        self.tools = compiler.CHAT_TOOLS if mode == "chat" else compiler.TOOLS
        # Obliterate mode (CLAUDE.md, Modes), fixed for the whole conversation: Claude may write wording
        # (no placed-words trace at the gate) and a custom method is offered whenever one could do the change at all
        self.unguarded = bool(unguarded)
        self.system = compiler.system_prompt(chat=(mode == "chat"), unguarded=self.unguarded)
        self.messages: List[Dict[str, Any]] = []
        self.entries: List[Entry] = []
        self.pending: List[Dict[str, Any]] = []   # tool_results owed for the last assistant turn, in call order
        self.notes: List[str] = []                 # app text for the next user message (never approved words)
        self._reply_mark: Optional[tuple] = None   # (the user message a reply sent, the state before it): see _take_back
        self.usage = llm.Usage()
        # the context gauge (``_measure_context``): {"tokens", "window", "rollover"} at the last answer that joined the
        # history, None before one; ``on_context`` (the window) hears of each. Never recorded, never sent to Claude.
        # Not ``context``: that is the app's account of how the documents come to be planned, which the request carries
        self.context_reading: Optional[Dict[str, Any]] = None
        self.on_context = on_context
        self.tool_calls: List[Dict[str, Any]] = []
        self.turn: Optional[Turn] = None
        self.unsupported: Optional[Dict[str, Any]] = None
        self._choice_n = 0
        self._ask_ids: List[str] = []
        self.started = False
        # diagnostics: every request, answer, tool result and failed call as it happened (conversation.json, and
        # CompileResult.transcript → compile.json); time spent with Claude and the tools — never waiting for the user
        self.exchange: List[Dict[str, Any]] = []
        self.seconds = 0.0
        self.calls = 0
        self.continuous_recovery = continuous_recovery
        self.recovery_active = False
        self.recovery_planning = None
        self.recovery_budgets = []

    # ---- public ----
    def start(self) -> Turn:
        if self.started:
            raise RuntimeError("the conversation has already started")
        self.started = True
        self.messages = compiler.build_messages(self.request, self.batch, self.answers,
                                                prior_failures=self.prior_failures or None, history=self.history or None,
                                                fresh=self.fresh or None, context=self.context or None,
                                                todo=self.todo or None, earlier=self.earlier or None)
        self.entries.append(Entry("user", self.request))
        self.exchange.append({"turn": 0, "role": "user", "content": self.messages[0]["content"],
                              "files": [str(f) for f in self.files],
                              "template": str(self.template) if self.template else None})
        return self._advance()

    def choice(self, choice_id: str) -> Choice:
        for c in (self.turn.choices if self.turn else []):
            if c.id == choice_id:
                return c
        raise ValueError("that choice is not offered any more — pick one of the current choices or type a reply")

    def reply(self, text: str = "", choice_id: Optional[str] = None, client=None, approve: bool = True,
              wording: Optional[str] = None) -> Turn:
        """The user's next message: their own words, a picked choice, or both. Picking a choice approves its exact
        wording (if it has any) — or, with ``wording``, the user's edit of it (``edited_wording``), and the draft is not
        approved; typed words are approved as typed. ``approve=False`` sends text the user did not
        write — a tapped suggested reply, which is the model's wording — as the user's decision but never as a
        source of words the documents may carry (CLAUDE.md invariant 6: Claude's words are never the user's)."""
        if not self.started:
            raise RuntimeError("start the conversation first")
        if client is not None:
            self._client = client
            self._client_given = True
        text = (text or "").strip()
        chosen = self.choice(choice_id) if choice_id else None
        if chosen is not None and chosen.kind in APP_CHOICE_KINDS:
            raise ValueError("that choice is carried out by the app, not sent to Claude")
        if not text and chosen is None:
            raise ValueError("type a reply or pick one of the choices")
        edited = edited_wording(chosen, wording) if chosen is not None else None
        if wording is not None and chosen is None:
            raise ValueError("an edited wording belongs to a picked choice")
        before = {"pending": copy.deepcopy(self.pending), "notes": list(self.notes), "ask_ids": list(self._ask_ids),
                  "sources": len(self.sources)}
        if chosen is not None and chosen.kind == "capitalization":
            from .ops.hygiene import approval_source
            record = chosen.snapshot
            self.batch.check_source_pin(Path(record["file"]))
            self.sources.append(approval_source(record))
        if edited is not None:
            self.sources.append(edited)          # the user's words: the draft they replaced is never approved
        elif chosen is not None and chosen.words:
            self.sources += list(chosen.words)   # each its own source: never stitched together
        if text and approve:
            self.sources.append(text)
        self.turn = None          # the turn being answered is over: its choices can no longer be picked
        said = []
        if chosen is not None and edited is not None:
            said.append(f"The user chose: «{chosen.label}» and changed its wording first — approving these exact words "
                        f"instead of the offered ones: «{edited}»")
        elif chosen is not None:
            said.append(f"The user chose: «{chosen.label}»" +
                        (f" — approving these exact words: {chosen.exact_words()}" if chosen.words else ""))
            if chosen.kind == "capitalization":
                said.append("Individually approved capitalize_defined_terms record: " + json.dumps(chosen.snapshot, ensure_ascii=False))
        if text:
            said.append(f"The user replied: {text}")
        said_text = "\n".join(said)
        shown = (f"{chosen.label} — exact words, as you edited them: «{edited}»" if edited is not None
                 else chosen.describe() if chosen else "")
        self.entries.append(Entry("user", "\n".join(([f"Chose: {shown}"] if chosen else []) +
                                                    ([text] if text else []))))
        self._send_user(said_text)
        self._reply_mark = (self.messages[-1], before)
        return self._advance()

    def _take_back(self) -> bool:
        """A reply Claude declined to answer (``stop_reason`` refusal on the first call after it) leaves the history:
        otherwise it would go out again with every later message and be answered as part of the next one (the 1.8.0
        live run re-sent a refused reply with the user's next). The state from before it returns: the tool results
        it carried are owed again, the app's notes wait again, its words are no longer approved. Only that reply,
        and only while it is still the last message; the transcript keeps what the user typed."""
        mark, self._reply_mark = self._reply_mark, None
        if mark is None or not self.messages or self.messages[-1] is not mark[0]:
            return False
        message, before = mark
        self.messages.pop()
        from .contexts import checkpoint_history
        checkpoint_history(self.messages, **self._context_arguments(), withdrawn=message)
        self.pending, self.notes, self._ask_ids = before["pending"], before["notes"], before["ask_ids"]
        del self.sources[before["sources"]:]
        self.exchange.append({"turn": self.calls, "role": "app",
                              "content": "the declined reply was taken back out of the history"})
        return True

    def resumable(self) -> bool:
        """The last turn stopped before Claude answered (the allowance paused it, the user stopped it, or the call
        failed): the history ends with the app's own message and nothing is owed, so it can be sent again as it is."""
        return (self.started and not self.recovery_active and bool(self.messages) and not self.pending
                and self.messages[-1].get("role") == "user" and (self.turn is None or self.turn.status == "failed"))

    def resume(self) -> Turn:
        """Continue a turn that stopped before Claude answered, from the history it had: the investigation so far is
        kept, nothing is said on the user's behalf and no wording is approved."""
        if not self.resumable():
            raise ValueError("this conversation is waiting for the user's reply, not for a continuation")
        self.turn = None
        return self._advance()

    def take_app_choice(self, choice_id: str) -> Choice:
        """A choice the app carries out itself (the custom transform, a recovery plan or target): recorded in the
        transcript and told to Claude with the next message; no request is made now."""
        chosen = self.choice(choice_id)
        if chosen.kind not in APP_CHOICE_KINDS:
            raise ValueError("that choice is answered by Claude — send it as a reply")
        self.entries.append(Entry("user", f"Chose: {chosen.label}"))
        self.notes.append(f"The user chose: «{chosen.label}». The app carried it out; the outcome follows.")
        return chosen

    def investigate_failure(self, evidence) -> Turn:
        """A failed preview/method continues the same request under a reviewed outcome."""
        self.prior_failures.append(copy.deepcopy(evidence))
        self.recovery_active = True
        return self._advance()

    def note(self, text: str, display: Optional[str] = None) -> None:
        """Tell Claude something with the next user message (what a preview found, what a custom transform did).
        Shown in the transcript as the app's (``display``, when the full text is too long to show); never a source of
        approved words."""
        text = (text or "").strip()
        if text:
            self.notes.append(text)
            self.entries.append(Entry("app", (display or text).strip()))

    def brief(self) -> str:
        """The conversation as a brief for the custom-transform writer: the request, what Claude understood, every
        user reply and choice, and why no operation fits."""
        out = [f"REQUEST: {self.request}"]
        for e in self.entries[1:]:
            if e.role == "user":
                out.append(f"USER: {e.text}")
            elif e.role == "claude":
                if e.understanding:
                    out.append(f"CLAUDE UNDERSTOOD: {e.understanding}")
                if e.text:
                    out.append(f"CLAUDE: {e.text}")
        if self.unsupported:
            out.append(f"WHY NO OPERATION FITS: {self.unsupported.get('unsupported', '')}")
            out.append(f"PROPOSAL: {self.unsupported.get('proposal', '')}")
        return "\n".join(out)

    def user_turns(self) -> int:
        return sum(1 for e in self.entries if e.role == "user")

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "request": self.request, "files": [str(f) for f in self.files],
                "template": str(self.template) if self.template else None,
                "input_files": [str(p) for p in self.batch.inputs], "set_aside": list(self.set_aside),
                "left_out": list(self.left_out),
                "model": self.model, "mode": self.mode,
                "approved_words": list(self.sources), "answers": list(self.answers),
                "entries": [e.to_dict() for e in self.entries], "turn": self.turn.to_dict() if self.turn else None,
                "unsupported": self.unsupported, "tool_calls": self.tool_calls, "usage": self.usage.to_dict(),
                "resource_policy": {"max_calls_per_turn": self.max_calls, "max_attempts_per_turn": self.max_attempts,
                                    "max_output_tokens": self.max_output_tokens, "effort": self.effort,
                                    "stall_repetitions": self.stall_repetitions,
                                    "max_context_tokens": self.max_context_tokens,
                                    "max_image_tokens": self.batch.max_image_tokens},
                "context": self.context_state,
                "recovery_planning": self.recovery_planning,
                "recovery_budgets": self.recovery_budgets,
                "calls": self.calls, "seconds": round(self.seconds, 2), "exchange": self.exchange}

    def save(self, path: Path) -> Path:
        """conversation.json — a record for the user and the batch folder. Never read back to rebuild the history."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def compile_result(self, turn: Turn) -> "compiler.CompileResult":
        """The one-shot view of a turn (``compiler.compile_request``)."""
        qs = list(turn.questions)
        if turn.status == "questions":
            if turn.message and turn.message not in qs:
                qs.append(turn.message)
            qs += [f"[choice] {c.describe()}" for c in turn.choices]
        status = turn.status
        res = compiler.CompileResult(status=status, spec=turn.spec, questions=qs, errors=list(turn.errors),
                                     tool_calls=list(self.tool_calls), model=self.model, usage=self.usage.as_record(),
                                     served_models=sorted(set(self.usage.served)),
                                     custom_transform=turn.custom_transform,
                                     custom_transform_reason=turn.custom_transform_reason,
                                     transcript=list(self.exchange), seconds=self.seconds)
        return res

    # ---- the loop ----
    def forget_cached_credentials(self):
        """Forget a client built here from old app credentials. Explicitly supplied clients belong to the caller."""
        if not self._client_given:
            self._client = None

    def _client_now(self):
        if self._client is None:
            self._client = compiler._client()
        return self._client

    def _send_user(self, text: str) -> None:
        """Append the next user message in one step: the owed tool_results (an ask_user result carries the user's
        words), the app's notes, then the user's words when no ask_user result carried them."""
        blocks: List[Dict[str, Any]] = []
        carried = False
        for r in self.pending:
            r = dict(r)
            if r["tool_use_id"] in self._ask_ids:
                r["content"] = text or "The user did not answer in words."
                carried = True
            blocks.append(r)
        blocks += [{"type": "text", "text": f"[note from the app] {n}"} for n in self.notes]
        if text and not carried:
            blocks.append({"type": "text", "text": text})
        if not blocks:
            raise ValueError("nothing to send")
        self.messages.append({"role": "user", "content": blocks})
        self.exchange.append({"turn": self.calls, "role": "user", "content": llm.transcript_content(blocks)})
        self.pending, self.notes, self._ask_ids = [], [], []

    def _fail(self, why: str, errors: Optional[List[str]] = None) -> Turn:
        turn = Turn("failed", message=why, errors=[why] + list(errors or []))
        return self._finish(turn)

    def _finish(self, turn: Turn) -> Turn:
        self.turn = turn
        diag.event("conversation.turn.outcome", status=turn.status, model_calls=self.calls,
                   question_count=len(turn.questions), choice_count=len(turn.choices), errors=turn.errors)
        said = turn.message or ("" if turn.questions else str((turn.spec or {}).get("summary") or ""))
        shown = [said] + [f"• {q}" for q in turn.questions]
        self.entries.append(Entry("claude", "\n".join(x for x in shown if x), status=turn.status,
                                  understanding=turn.understanding, choices=[c.to_dict() for c in turn.choices],
                                  images=list(turn.images), chips=list(turn.chips), message=said.strip()))
        return turn

    def _next_choice_id(self) -> str:
        self._choice_n += 1
        return f"c{self._choice_n}"

    def _dispatch(self, block, name: str, inp: Dict[str, Any], images: List[str]) -> Dict[str, Any]:
        tid = _bid(block)
        allowed = {t["name"] for t in self.tools} - {"ask_user", "submit_editspec"}
        try:
            with diag.span("conversation.tool", tool=name, tool_use_id=tid, model_turn=self.calls):
                if name not in allowed:
                    raise KeyError(f"unknown tool {name}")
                out = self._read_outline(**inp) if name == "read_outline" else getattr(self.batch, name)(**inp)
                if isinstance(out, dict) and out.get("_content_blocks"):
                    images += [str(p) for p in out.get("_images", [])]
                    return {"type": "tool_result", "tool_use_id": tid, "content": out["_content_blocks"]}
                return {"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001 — every call gets an answer, or the next request is refused
            return {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": f"{type(e).__name__}: {e}"[:2000]}

    def _read_outline(self, file: str) -> Dict[str, Any]:
        """The chat's read_outline (outline.py, docs/SPEC_FORMATTER_MODES_DESIGN.md N1): the structure reader's roles
        and, for what it leaves, Claude's reading in one-shot calls on this conversation's model at
        ``resources.OUTLINE_EFFORT``, inside the request's spending check and Stop; their usage joins the
        conversation's and their exchange its record. An API error ends Claude's reading with what it read; Stop or
        the spending check propagates (the tool call is answered with it, and the next call stops the turn). The answer
        is kept by the file's SHA-256: asking again for the same bytes makes no call."""
        from . import outline
        from .resources import OUTLINE_EFFORT
        pkg = self.batch.pkg(file)
        digest = hashlib.sha256(pkg.data).hexdigest()
        kept = self.batch.outlines.get(digest)
        if kept is not None:
            return {**kept, "claude_calls": 0, "note": "Read earlier in this conversation: no new call. " + kept["note"]}

        def call(message: str):
            t0 = time.perf_counter()
            with llm.effort_scope(OUTLINE_EFFORT):
                resp = llm.call(self._client_now(), model=self.model, system=outline.CLASSIFY_SYSTEM,
                                tools=outline.CLASSIFY_TOOLS, messages=[{"role": "user", "content": message}],
                                max_output_tokens=outline.OUTLINE_MAX_OUTPUT_TOKENS, effort=OUTLINE_EFFORT,
                                one_shot=True)
            self.usage.add(resp, self.model)
            self.exchange.append({"outline": file, "request": llm.transcript_content(message),
                                  **llm.response_entry(self.calls, resp, time.perf_counter() - t0)})
            return resp

        def failed(e: BaseException) -> Optional[str]:
            if not llm.is_api_error(e):
                return None
            self.exchange.append({"outline": file, **llm.error_entry(self.calls, e, 0.0)})
            return llm.failure_text(e)

        answer = outline.result(outline.classify(file, pkg, call, failed=failed))
        if not answer["unplaced"] or all("could not be made" not in u["why"] for u in answer["unplaced"]):
            self.batch.outlines[digest] = answer
        return answer

    def _advance(self) -> Turn:
        t0 = time.perf_counter()
        try:
            path = self.work_dir / "logs" / "events.jsonl" if self.work_dir else None
            with diag.scope(path, conversation_id=self.id, user_turn=self.user_turns()):
                with diag.span("conversation.turn", mode=self.mode, file_count=len(self.files)) as record:
                    if self.recovery_active:
                        from .continuous_recovery import propose
                        turn = self._finish(propose(self))
                    else:
                        turn = self._advance_calls()
                        if (self.continuous_recovery and turn.status == "unsupported"
                                and turn.custom_transform != "possible"):
                            from .continuous_recovery import propose
                            self.log("The built-in route stopped; investigating the requested outcomes.")
                            turn = self._finish(propose(self))
                    record.update(status=turn.status, model_calls=self.calls)
                    return turn
        finally:
            self.seconds += time.perf_counter() - t0
            if self.messages:
                from .contexts import checkpoint_history
                checkpoint_history(self.messages, **self._context_arguments())

    def _context_arguments(self):
        from .investigation import artifact_root
        folder = artifact_root(self.batch).parent
        self.batch.context_dir = folder
        return {"system": self.system, "tools": self.tools, "folder": folder, "state": self.context_state,
                "authority": {"request": self.request, "approved_word_sources": list(self.sources),
                              "files": self.batch.labels, "user_decisions": list(self.answers)},
                "source_pins": self.batch.source_pins}

    def _record_results(self, ordered: List[Dict[str, Any]], pending: bool) -> None:
        extra = {"pending": True} if pending else {}       # pending: sent first in the next user message
        self.exchange.append(llm.results_entry(self.calls, ordered, **extra))

    def _advance_calls(self) -> Turn:
        rejections: List[str] = []                # the errors of every rejected submission in this user turn
        rejected = 0
        progress = InvestigationProgress(self.stall_repetitions)
        stalled = False
        images: List[str] = []
        nudged = False
        for call in range(self.max_calls):
            check_pairing(self.messages)
            from .contexts import ContextLimit, manage_context
            try:
                self.messages = manage_context(self.messages, max_tokens=self.max_context_tokens,
                                               **self._context_arguments())
            except ContextLimit as error:
                return self._fail(str(error), rejections)
            self.calls += 1
            t_call = time.perf_counter()
            try:
                with diag.scope(model_turn=self.calls), diag.span("conversation.request"):
                    resp = llm.call(self._client_now(), model=self.model, system=self.system, tools=self.tools,
                                    messages=self.messages, max_output_tokens=self.max_output_tokens, effort=self.effort)
            except Exception as e:  # noqa: BLE001 — classified below
                if not llm.is_api_error(e) and not isinstance(e, llm.BudgetExceeded):
                    raise                  # a defect (a wrong argument, a test's script running out) propagates
                # an API or network error, or Claude not installed: nothing is appended; the user can retry
                self.exchange.append(llm.error_entry(self.calls, e, time.perf_counter() - t_call))
                self.log(f"Claude: the call failed ({type(e).__name__})")
                return self._fail(llm.failure_text(e), rejections)
            self.usage.add(resp, self.model)
            self.exchange.append(llm.response_entry(self.calls, resp, time.perf_counter() - t_call))
            problem = llm.stop_problem(resp)
            if problem and getattr(resp, "stop_reason", None) == "refusal":
                return self._fail(declined_text(resp, taken_back=self._take_back()), [problem] + rejections)
            if problem:
                return self._fail(problem, rejections)
            self._measure_context(resp)              # the answer joins the history from here on
            content = llm.history_content(resp)
            uses = llm.tool_uses(content)
            said = "\n".join(t.strip() for t in llm.texts(content) if t.strip()).strip()
            if not uses:
                if not content:
                    return self._fail("Claude returned an empty answer", rejections)
                self.messages.append({"role": "assistant", "content": content})
                if said and self.mode == "chat":
                    return self._finish(Turn("questions", message=said, errors=rejections, images=images))
                if nudged:
                    why = "the model ended without submitting a spec or asking a question"
                    return self._fail(why + (f": {said[:500]}" if said else ""), rejections)
                nudged = True
                self.messages.append({"role": "user", "content": NUDGE})
                self.exchange.append({"turn": self.calls, "role": "user", "content": NUDGE})
                continue
            self.messages.append({"role": "assistant", "content": content})
            results: Dict[str, Dict[str, Any]] = {}
            asks: List[Any] = []
            accepted = None
            for b in uses:
                name, inp = b.name, dict(b.input or {})
                self.tool_calls.append({"tool": name, "input": inp if name != "submit_editspec" else "(spec)"})
                self.log(f"Claude: {name}")
                if name == "ask_user":
                    diag.event("conversation.tool.waiting", tool=name, tool_use_id=_bid(b), model_turn=self.calls)
                    asks.append((b, inp))
                    results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "content": "(waiting for the user)"}
                    continue
                if name == "set_aside" and any(t["name"] == "set_aside" for t in self.tools):
                    results[_bid(b)] = self._take_set_aside(b, inp)
                    continue
                if name == "submit_editspec":
                    if accepted is not None:
                        diag.event("conversation.submission.rejected", tool_use_id=_bid(b), model_turn=self.calls,
                                   reason="one submission per turn; an earlier submission was already accepted")
                        results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "is_error": True,
                                            "content": "one submission per turn — this one was not considered"}
                        continue
                    if rejected >= self.max_attempts:
                        results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "is_error": True,
                                            "content": "the configured attempt limit was reached; this candidate was not tried"}
                        continue
                    spec = inp.get("spec")
                    outcome = spec
                    # the documents the user left out of the request: nothing plans them, nothing judges the spec there
                    # (and the documents set aside during this conversation, which no plan covers either)
                    parked = [Path(p) for p in self.set_aside]
                    left_out, errs = compiler.resolve_leave_out(inp.get("leave_out"), self.batch, set_aside=parked)
                    try:
                        with diag.span("conversation.submission.validate", tool_use_id=_bid(b), model_turn=self.calls) as record:
                            if not errs:
                                outcome, errs = compiler.compose_submission(spec, self.batch, self.sources,
                                                                            leave_out=left_out + parked,
                                                                            unguarded=self.unguarded,
                                                                            shown=[self.shown_text(said, inp, spec)])
                            record.update(accepted=not bool(errs), errors=errs,
                                          composed=bool(errs) is False and outcome is not spec)
                    except Exception as e:  # noqa: BLE001 — the call must be answered (rule 14): a spec the checks
                        # cannot even read goes back to Claude as rejected, with what went wrong
                        self.log(f"Claude: the submitted spec could not be checked ({type(e).__name__})")
                        errs = [f"[spec] the spec could not be checked ({type(e).__name__}: {e}) — fix its shape "
                                f"(see the EditSpec JSON schema in the catalog)"]
                    if errs:
                        diag.event("conversation.submission.rejected", tool_use_id=_bid(b), model_turn=self.calls,
                                   errors=errs)
                        rejected += 1
                        rejections += errs
                        candidate = ({k: v for k, v in spec.items() if k not in ("request", "summary", "assumptions")}
                                     if isinstance(spec, dict) else spec)
                        stalled = progress.rejected(candidate, errs) or stalled
                        results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "is_error": True,
                                            "content": "REJECTED — fix and call submit_editspec again:\n- " + "\n- ".join(errs)}
                        continue
                    accepted = (b, outcome, inp, left_out)
                    diag.event("conversation.submission.accepted", tool_use_id=_bid(b), model_turn=self.calls,
                               composed=outcome is not spec)
                    results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "content": ACCEPTED_RESULT}
                    continue
                results[_bid(b)] = self._dispatch(b, name, inp, images)
                result = results[_bid(b)]
                if not result.get("is_error") and progress.observe({"tool": name, "content": result["content"]}):
                    stalled = False
            ordered = [dict(results[_bid(b)]) for b in uses]
            if asks:
                if accepted is not None:
                    diag.event("conversation.submission.deferred", tool_use_id=_bid(accepted[0]), model_turn=self.calls,
                               reason="user clarification requested in the same turn; submission must be repeated")
                    for r in ordered:
                        if r["tool_use_id"] == _bid(accepted[0]):
                            r["content"] = "not taken: you asked the user in the same turn — resubmit after their reply"
                self.pending, self._ask_ids = ordered, [_bid(b) for b, _ in asks]
                self._record_results(ordered, pending=True)
                return self._finish(self._ask_turn(asks, rejections, images, said))
            if accepted is not None:
                self.pending = ordered
                self._record_results(ordered, pending=True)
                return self._finish(self._spec_turn(accepted[1], accepted[2], rejections, images, said,
                                                    leave_out=accepted[3]))
            if stalled or rejected >= self.max_attempts:
                why = (f"investigation stalled after {self.stall_repetitions} equivalent rejected candidates "
                       "without new evidence" if stalled else f"no spec after {rejected} rejected attempts")
                for r in ordered:
                    if str(r.get("content", "")).startswith("REJECTED"):
                        r["content"] += f"\n({why}; new evidence or a different approach is needed)"
                self.pending = ordered
                self._record_results(ordered, pending=True)
                return self._fail(why, rejections)
            if call == self.max_calls - 1:
                self.pending = ordered
                self._record_results(ordered, pending=True)
                break
            self.messages.append({"role": "user", "content": ordered})
            self._record_results(ordered, pending=False)
        return self._fail(f"no spec after {self.max_calls} calls", rejections)

    def _measure_context(self, resp) -> None:
        """The context gauge in the window's top bar: how much of the model's context window the conversation fills
        once this answer joins it (``llm.context_tokens``, Anthropic's count), beside that window (None for a model
        ``llm.CONTEXT_WINDOWS`` lacks) and ``max_context_tokens``, past which ``contexts.manage_context`` seals the
        history and goes on in a fresh epoch (by its own estimate, not this count). A response without usage keeps the
        last reading; a declined or failed one never gets here, since its answer joins nothing. ``on_context`` can
        never affect the conversation."""
        tokens = llm.context_tokens(resp)
        if tokens is None:
            return
        self.context_reading = {"tokens": tokens, "window": llm.CONTEXT_WINDOWS.get(self.model),
                        "rollover": self.max_context_tokens}
        if self.on_context is None:
            return
        try:
            self.on_context(self)
        except Exception as error:  # noqa: BLE001 — the window's problem, never the conversation's
            diag.event("conversation.context.observer_failed", level="warning", error_type=type(error).__name__)

    def _take_set_aside(self, block, inp: Dict[str, Any]) -> Dict[str, Any]:
        """A ``set_aside`` call: the documents the user asked to put on their to-do list leave this conversation's
        documents at once (``files``), and every later submission leaves them out. The app moves them to the to-do
        list when the turn ends (``DocumentTask``); nothing in them changes."""
        tid = _bid(block)
        paths, errs = compiler.resolve_set_aside(inp.get("documents"), self.batch, [Path(p) for p in self.set_aside])
        if errs:
            diag.event("conversation.set_aside.refused", tool_use_id=tid, model_turn=self.calls, errors=errs)
            return {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": "\n".join(errs)}
        reason = " ".join(str(inp.get("reason") or "").split())[:200]
        for path in paths:
            key = str(path.resolve())
            self.set_aside.append(key)
            self.set_aside_reasons[key] = reason
        parked = set(self.set_aside)
        self.files = [f for f in self.files if str(Path(f).resolve()) not in parked]
        diag.event("conversation.set_aside", tool_use_id=tid, model_turn=self.calls, count=len(paths),
                   remaining=len(self.files))
        names = ", ".join(self.batch.label(p) for p in paths) or "nothing new (already set aside)"
        if not self.files:
            rest = ("Every document is now set aside: nothing is left to plan in this request. Tell the user in plain "
                    "words, and do not submit a plan.")
        else:
            rest = ("From now on a plan covers only the others: " + ", ".join(self.batch.label(f) for f in self.files)
                    + ". Tell the user in plain words, and go on with whatever else they asked.")
        return {"type": "tool_result", "tool_use_id": tid,
                "content": f"Set aside: {names}. The app puts them on the user's to-do list, out of every plan until "
                           f"the user works on them or puts them back; nothing in them changes. {rest}"}

    # ---- turns ----
    def _choices_from(self, raw: Any, errors: List[str]) -> List[Choice]:
        out: List[Choice] = []
        if raw is not None and not isinstance(raw, list):
            errors.append(f"the choices were dropped: expected a list of choices, got {type(raw).__name__}")
            raw = []
        for c in raw or []:
            if not isinstance(c, dict):
                errors.append("a choice that was not an object was dropped")
                continue
            label, kind = str(c.get("label") or "").strip(), str(c.get("kind") or "").strip()
            if kind == "capitalization":
                record = self.batch.capitalization_proposals.get(c.get("proposal_id", ""))
                if self.mode != "chat" or record is None:
                    errors.append("capitalization choice dropped: first inspect the exact occurrence with propose_defined_terms")
                    continue
                label = (f"{Path(record['file']).name}, {record['part']}, paragraph {record['paragraph']+1}, "
                         f"character {record['start']+1}: {record['before']!r} → {record['after']!r}\n"
                         f"Context: {record['context']}")
                out.append(Choice(self._next_choice_id(), label, kind, wording=record["after"],
                                  detail="Approves only this occurrence in this source file.", snapshot=copy.deepcopy(record)))
                continue
            if kind in ("recipe", "operation", "snippet"):
                errors.append("Choose the implementation internally; ask the user only about the document result.")
                continue
            if not label or kind not in CHOICE_KINDS_FROM_CLAUDE:
                errors.append(f"a choice was dropped (kind {kind!r} is not one Claude may offer)" if label else
                              "a choice without a label was dropped")
                continue
            try:
                wording = clean_wording(c.get("wording"))
            except ValueError as e:
                errors.append(f"the choice «{label}» was dropped: {e}")
                continue
            out.append(Choice(self._next_choice_id(), label, kind, wording, str(c.get("detail") or "")))
        return out

    def _ask_turn(self, asks, rejections, images, said: str = "") -> Turn:
        errors = list(rejections)
        questions, choices, messages, understanding, chips = [], [], [], [], []
        if said:
            messages.append(said)
        for _b, inp in asks:
            questions += _texts(inp.get("questions"), "questions", errors)
            choices += self._choices_from(inp.get("choices"), errors)
            chips += _chips(inp.get("suggested_replies"), errors)
            if isinstance(inp.get("message"), str) and inp["message"].strip():
                messages.append(inp["message"].strip())
            if isinstance(inp.get("understanding"), str) and inp["understanding"].strip():
                understanding.append(inp["understanding"].strip())
        if not (questions or choices or messages):
            questions = ["(the model asked an empty question)"]
        # the choices are the answers: a suggested reply that only says one of them again is not offered beside it
        restating = [chip for chip in chips if any(restates(chip, c.label) for c in choices)]
        if restating:
            errors.append(f"{len(restating)} suggested repl{'y' if len(restating) == 1 else 'ies'} restating a choice "
                          f"dropped: {restating}")
            chips = [chip for chip in chips if chip not in restating]
        return Turn("questions", message="\n\n".join(dict.fromkeys(messages)), understanding=" ".join(understanding),
                    questions=questions, choices=choices, errors=errors, images=images, chips=chips)

    @staticmethod
    def shown_text(said: str, inp: Dict[str, Any], spec: Any) -> str:
        """What the user reads of a submission in the chat: Claude's plain text of the turn and the tool's message
        (the spec's summary stands in for a model that wrote neither). The window shows no plan card, so this is the
        one place the user can read what a plan will place — the text Obliterate's gate traces placed words to
        (``compiler.compose_submission(shown=)``)."""
        parts = [p for p in (str(said or "").strip(), str((inp or {}).get("message") or "").strip()) if p]
        summary = ""
        if isinstance(spec, dict):
            summary = str(spec.get("summary") or spec.get("description") or spec.get("name") or "")
        return "\n\n".join(dict.fromkeys(parts)) or summary

    def _spec_turn(self, spec, inp, rejections, images, said: str = "", leave_out=()) -> Turn:
        # The spec the app gets carries the USER's request, never the text Claude wrote in "request": the advisor and
        # the next pass (set_aside.json → retry) count it among the approved words. A copy — the history keeps the
        # tool call exactly as Claude made it. A chain (the app's composition of overlapping ops, or the model's own
        # stages) carries it on every stage.
        from .editspec import is_chain
        spec = copy.deepcopy(spec)
        errors = list(rejections)
        chips = _chips(inp.get("suggested_replies"), errors)
        if is_chain(spec):
            for st in spec["stages"]:
                st["spec"]["request"] = self.request
            summary = str(spec.get("summary") or spec.get("description") or spec.get("name") or "")
        else:
            spec["request"] = self.request
            summary = str(spec.get("summary") or "")
        # The user reads the model's words: its plain text of the turn and the tool's message (the summary stands in
        # for a model that wrote neither); the summary is the record's.
        message = self.shown_text(said, inp, spec)
        left_out = [str(Path(p).resolve()) for p in leave_out]
        self.left_out = list(left_out)
        # a question or an unsupported outcome is answered before any plan the submission also carries could run
        # (compose_submission refuses the two together; this order is the second guard)
        if isinstance(spec.get("layouts"), list) and not (spec.get("unsupported") or spec.get("questions")):
            from .layouts import plan_from_submission
            plan = plan_from_submission(spec, self.request)
            return Turn("ok", message=message, understanding=summary, spec=plan, errors=errors, images=images,
                        chips=chips, leave_out=left_out)
        if is_chain(spec) and not (spec.get("unsupported") or spec.get("questions")):
            return Turn("ok", message=message, understanding=summary, spec=spec, errors=errors, images=images,
                        chips=chips, leave_out=left_out)
        if spec.get("unsupported"):
            self.unsupported = {"request": self.request, "unsupported": spec.get("unsupported"),
                                "proposal": spec.get("proposal") or ""}
            verdict = str(inp.get("custom_transform") or "")
            reason = str(inp.get("custom_transform_reason") or "")
            if self.unguarded and verdict == "outside_limits":
                verdict = "possible"              # Obliterate: a custom method has no formatting-only limits
            choices = []
            if verdict == "possible":
                choices.append(Choice(self._next_choice_id(), "Have Claude write a one-off custom transform for this "
                                      "(tested on sample files, edited copies only)", "custom_transform",
                                      detail=str(spec.get("proposal") or "")))
            msg = str(spec.get("unsupported"))
            if verdict == "outside_limits":
                msg += ("\nThis chat's formatting-only custom fallback cannot cover that change"
                        + (f": {reason}" if reason else ".")
                        + " Project recovery can use broader custom methods for approved outcomes with independent "
                          "verification; this fallback limit alone does not establish that a new built-in is needed.")
            return Turn("unsupported", message=msg, understanding=summary, choices=choices, spec=spec,
                        errors=list(rejections), images=images, custom_transform=verdict,
                        custom_transform_reason=reason, leave_out=left_out)
        if spec.get("questions"):
            errors = list(rejections)
            questions = _texts(spec["questions"], "spec's questions", errors) or ["(the model asked an empty question)"]
            return Turn("questions", understanding=summary, questions=questions, spec=spec, errors=errors,
                        images=images)
        return Turn("ok", message=message, understanding=summary, spec=spec, errors=errors, images=images, chips=chips,
                    leave_out=left_out)
