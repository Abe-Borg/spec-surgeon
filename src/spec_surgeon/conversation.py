"""The conversation — a request turned into an EditSpec by talking it through with Claude (decided 2026-10-15: "don't
give up on the user").

One ``Conversation`` per request. It starts like the old one-shot compile (the request, the batch's profiles, the op
catalog — see compiler.py) and then keeps going: when Claude needs something from the user (``ask_user``), when it has
submitted a spec the user wants corrected after the preview, or when no operation fits, the user replies — in their
own words or by picking one of the choices Claude offered — and the same conversation continues.

What makes it safe:

* **Append-only history.** ``messages`` is only ever appended to (the model's thinking blocks are tied to the exact
  history that produced them). A failed call appends nothing; the next reply simply adds the user's new text. An API
  error (``llm.is_api_error``) ends the turn as ``failed`` with its kind, HTTP status and request id; anything else is a
  defect and propagates — it appends nothing either, so the history still pairs.
* **Every tool call is answered.** Each ``tool_use`` gets exactly one ``tool_result`` in the very next user message
  (``check_pairing`` runs before every request). Results the app cannot give yet — the answer to ``ask_user``, what
  the preview of an accepted spec showed — wait in ``pending`` and go out first in the next user message.
* **Approved words only.** ``sources`` — the words a spec may place in the documents — are the request, the user's
  ``--answer`` lines and replies, ``PROJECT.json``, and the exact ``wording`` of a choice the user picked. Never
  Claude's own text: its understanding, messages, choice labels, notes or the custom-transform brief. A wording with a
  character the user cannot see (``invisible``) is never offered. conversation.json records the sources as
  ``approved_words`` (beside ``answers``; never an empty one — without a PROJECT.json, or with an empty one, there is
  no memory source), and the spec the app gets carries the user's ``request``, never the text
  Claude wrote there (the advisor and the next pass count the spec's request as approved words).
* **Choices are built by code.** Claude may offer ``clarify``/``alternative``/``rephrase`` choices; only the app adds
  the ``custom_transform`` choice, and only when Claude declared the request within ``CUSTOM_TRANSFORM_LIMITS``.
  Claude may also NAME one of the user's library items (``recipe`` / ``operation``, chat mode only): the app builds
  that choice from the library alone (``_library_choice``) — the words a recipe places are shown on it and become
  approved only when the user picks it (``take_library_choice``), each its own source; Claude's label and wording never
  reach it; the item is pinned by its content hash, and a pick refuses one that changed (``LibraryChanged``). A
  ``snippet`` (a saved paragraph) is named and built the same way, but picking it is a reply: its words are approved
  (the hash checked first) and Claude writes the ``insert_paragraph`` spec that places them.

``mode="compile"`` is the one-shot behaviour ``compiler.compile_request`` keeps (a plain-text answer gets one nudge,
then fails); ``mode="chat"`` (the window, the interactive command line) treats Claude's plain text as a message to the
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
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import compiler, llm

CHOICE_KINDS_FROM_CLAUDE = ("clarify", "alternative", "rephrase")   # built from Claude's own label and wording
LIBRARY_CHOICE_KINDS = ("recipe", "operation")                       # named by Claude, built from the library (rule 13)
APP_CHOICE_KINDS = ("custom_transform",) + LIBRARY_CHOICE_KINDS      # carried out by the app, never sent as a reply
SNIPPET_CHOICE_KIND = "snippet"                                      # named by Claude, built from the library, picked
NAMED_LIBRARY_KINDS = LIBRARY_CHOICE_KINDS + (SNIPPET_CHOICE_KIND,)  # as a REPLY: Claude writes the insert_paragraph
MAX_CALLS_PER_TURN = compiler.MAX_TURNS
NUDGE = "Finish by calling submit_editspec (or ask_user)."
# the tool result an accepted spec gets (the user's next message, if any, says what the preview found)
ACCEPTED_RESULT = ("accepted — the app shows it to the user; their next message (if any) follows with what the preview "
                   "found")


class ProtocolError(RuntimeError):
    """The history would break the tool_use / tool_result pairing — a bug in this module, never sent to the API."""


class LibraryChanged(ValueError):
    """The library item a choice offers changed on disk between Claude's offer and the user's pick (its content hash
    differs — ``library.item_hash``): nothing was used and nothing approved; the user asks again, or uses Your
    library…"""


@dataclass
class Choice:
    id: str
    label: str
    kind: str
    wording: Optional[str] = None                 # a choice of Claude's: the one exact wording picking approves
    detail: str = ""
    words: List[str] = field(default_factory=list)   # EVERY word picking approves, each its own source: [wording] for
                                                     # Claude's choices, the placed words of a recipe
    name: str = ""                                # a library choice: the item's exact name …
    item_hash: str = ""                           # … and its content hash as offered (library.item_hash)
    snapshot: Optional[Dict[str, Any]] = None     # a recipe choice: the spec as validated when offered; a snippet
                                                  # choice: the snippet as read when offered (neither is recorded)

    def __post_init__(self) -> None:
        if self.wording and not self.words:
            self.words = [self.wording]

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "label": self.label, "kind": self.kind, "wording": self.wording, "detail": self.detail,
                "words": list(self.words), "name": self.name, "item_hash": self.item_hash}

    def exact_words(self) -> str:
        return ", ".join(f"«{w}»" for w in self.words)

    def describe(self) -> str:
        """The choice as the command line and CompileResult show it — the exact words included."""
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

    def needs_user(self) -> bool:
        return self.status in ("questions", "unsupported", "failed")

    def to_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "message": self.message, "understanding": self.understanding,
                "questions": self.questions, "choices": [c.to_dict() for c in self.choices], "spec": self.spec,
                "errors": self.errors, "images": self.images, "custom_transform": self.custom_transform,
                "custom_transform_reason": self.custom_transform_reason}


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

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "text": self.text, "status": self.status, "understanding": self.understanding,
                "choices": self.choices, "images": self.images, "at": self.at}


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
                 model: Optional[str] = None, memory: Optional[Dict[str, Any]] = None,
                 prior_failures: Optional[Sequence[Dict[str, Any]]] = None, answers: Sequence[str] = (),
                 work_dir: Optional[Path] = None, mode: str = "chat", log: Optional[Callable[[str], None]] = None):
        if mode not in ("chat", "compile"):
            raise ValueError(f"unknown conversation mode {mode!r}")
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.files = [Path(f) for f in files]
        self.template = Path(template) if template else None
        self.model = model or compiler.DEFAULT_MODEL
        self.mode = mode
        self.work_dir = Path(work_dir) if work_dir else None
        self.log = log or (lambda m: None)
        self._client = client
        self.batch = compiler._Batch(self.files, self.template)
        self.batch.work_dir = self.work_dir
        self.memory = memory if memory is not None else compiler.load_project_memory(self.files)
        self.answers = list(answers)
        self.prior_failures = list(prior_failures or [])
        # never an empty source (conversation.json's approved_words lists them): no PROJECT.json — or one holding
        # nothing — is no source (memory_source would be "{}", nobody's wording), nor is an empty request or answer
        has_memory = any(k != "_path" for k in (self.memory or {}))
        self.sources: List[str] = [w for w in (request, *self.answers,
                                               compiler.memory_source(self.memory) if has_memory else "")
                                   if isinstance(w, str) and w.strip()]
        # frozen for the whole conversation: rebuilding either between calls would invalidate the cache and the
        # model's earlier thinking
        self.tools = compiler.CHAT_TOOLS if mode == "chat" else compiler.TOOLS
        self.system = compiler.system_prompt(chat=(mode == "chat"))
        self.messages: List[Dict[str, Any]] = []
        self.entries: List[Entry] = []
        self.pending: List[Dict[str, Any]] = []   # tool_results owed for the last assistant turn, in call order
        self.notes: List[str] = []                 # app text for the next user message (never approved words)
        self._dropped: List[str] = []              # library choices not shown, said to the user with the next entry
        self.usage = llm.Usage()
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

    # ---- public ----
    def start(self) -> Turn:
        if self.started:
            raise RuntimeError("the conversation has already started")
        self.started = True
        library = compiler.library_index() if self.mode == "chat" else None   # the user's library: chat only
        self.messages = compiler.build_messages(self.request, self.batch, self.answers, self.memory,
                                                prior_failures=self.prior_failures or None, library=library)
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

    def reply(self, text: str = "", choice_id: Optional[str] = None, client=None) -> Turn:
        """The user's next message: their own words, a picked choice, or both. Picking a choice approves its exact
        wording (if it has any); typed words are approved as typed."""
        if not self.started:
            raise RuntimeError("start the conversation first")
        if client is not None:
            self._client = client
        text = (text or "").strip()
        chosen = self.choice(choice_id) if choice_id else None
        if chosen is not None and chosen.kind in APP_CHOICE_KINDS:
            raise ValueError("that choice is carried out by the app, not sent to Claude")
        if not text and chosen is None:
            raise ValueError("type a reply or pick one of the choices")
        if chosen is not None and chosen.kind == SNIPPET_CHOICE_KIND:
            self._check_item(chosen)          # LibraryChanged: nothing approved, nothing sent — the turn stays open
        if chosen is not None and chosen.words:
            self.sources += list(chosen.words)   # each its own source: never stitched together
        if text:
            self.sources.append(text)
        self.turn = None          # the turn being answered is over: its choices can no longer be picked
        said = []
        if chosen is not None:
            said.append(f"The user chose: «{chosen.label}»" +
                        (f" — approving these exact words: {chosen.exact_words()}" if chosen.words else ""))
            if chosen.kind == SNIPPET_CHOICE_KIND:
                sn = chosen.snapshot or {}
                said.append("Insert it with insert_paragraph: text exactly those words, style "
                            + (repr(sn["style"]) if sn.get("style") else "'inherit'")
                            + f", formatting {sn.get('formatting', 'inherit')!r}; ask the user which paragraph it goes "
                              f"after (or before) if that is not clear yet.")
        if text:
            said.append(f"The user replied: {text}")
        said_text = "\n".join(said)
        self.entries.append(Entry("user", "\n".join(([f"Chose: {chosen.describe()}"] if chosen else []) +
                                                    ([text] if text else []))))
        self._send_user(said_text)
        return self._advance()

    def take_app_choice(self, choice_id: str) -> Choice:
        """A choice the app carries out itself (the custom transform): recorded in the transcript and told to Claude
        with the next message; no request is made now. A library choice goes through ``take_library_choice``."""
        chosen = self.choice(choice_id)
        if chosen.kind not in APP_CHOICE_KINDS:
            raise ValueError("that choice is answered by Claude — send it as a reply")
        if chosen.kind in LIBRARY_CHOICE_KINDS:
            raise ValueError("a library choice is taken with take_library_choice (the item is checked first)")
        self.entries.append(Entry("user", f"Chose: {chosen.label}"))
        self.notes.append(f"The user chose: «{chosen.label}». The app carried it out; the outcome follows.")
        return chosen

    def take_library_choice(self, choice_id: str):
        """The user picked one of their library items Claude offered (a ``recipe`` / ``operation`` choice); the app
        carries it out. First the item is checked to be exactly what was offered (``library.item_hash`` equal to the
        choice's): a changed, renamed or missing item is ``LibraryChanged`` — nothing used, nothing approved, said to
        the user and to Claude. Only then are the words the choice showed appended to ``sources`` (the pick approves them,
        each its own source), the pick recorded, and Claude told the outcome follows. Returns ``(choice, spec)``: for a
        recipe the spec to use — a copy of the snapshot validated when it was offered (never a re-read file), its placed
        words asserted to be the words the choice showed, carrying THIS conversation's ``request`` (rule 13: the spec a
        conversation hands the app carries the user's request); None for an operation."""
        chosen = self.choice(choice_id)
        if chosen.kind == SNIPPET_CHOICE_KIND:
            raise ValueError("a snippet choice is sent as a reply (the pick approves its words; Claude writes the spec)")
        if chosen.kind not in LIBRARY_CHOICE_KINDS:
            raise ValueError("that choice is not one of your library items")
        self._check_item(chosen)
        spec = None
        if chosen.kind == "recipe":
            spec = copy.deepcopy(chosen.snapshot)
            if not isinstance(spec, dict) or list(dict.fromkeys(compiler.placed_words(spec))) != list(chosen.words):
                raise RuntimeError(f"the snapshot of your recipe «{chosen.name}» does not place the words its choice "
                                   f"showed — a defect; nothing was used")
            spec["request"] = self.request
        self.sources += list(chosen.words)
        self.entries.append(Entry("user", f"Chose: {chosen.describe()}"))
        self.notes.append(f"The user chose: «{chosen.label}»"
                          + (f" — approving these exact words: {chosen.exact_words()}" if chosen.words else "")
                          + ". The app carried it out; the outcome follows.")
        return chosen, spec

    def _check_item(self, chosen: Choice) -> None:
        """The library item a choice offers must be exactly what was offered (``library.item_hash`` equal to the
        choice's): a changed, renamed or missing item is ``LibraryChanged`` — nothing used, nothing approved, said to the
        user (an app entry) and to Claude (a note with the next message)."""
        from . import library
        try:
            now = library.item_hash(library.find(chosen.name, chosen.kind))
        except (library.LibraryError, OSError) as e:
            now = f"unreadable: {e}"
        if not chosen.item_hash or now != chosen.item_hash:
            why = (f"your {chosen.kind} «{chosen.name}» changed since Claude offered it — nothing was used; ask again, "
                   f"or use Your library…")
            self.entries.append(Entry("app", f"Not used: {why}"))
            self.notes.append(f"The user chose «{chosen.label}», but {why}")
            raise LibraryChanged(why)

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
                "template": str(self.template) if self.template else None, "model": self.model, "mode": self.mode,
                "approved_words": list(self.sources), "answers": list(self.answers),
                "entries": [e.to_dict() for e in self.entries], "turn": self.turn.to_dict() if self.turn else None,
                "unsupported": self.unsupported, "tool_calls": self.tool_calls, "usage": self.usage.to_dict(),
                "calls": self.calls, "seconds": round(self.seconds, 2), "exchange": self.exchange}

    def save(self, path: Path) -> Path:
        """conversation.json — a record for the user and the batch folder. Never read back to rebuild the history."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return path

    def compile_result(self, turn: Turn) -> "compiler.CompileResult":
        """The one-shot view of a turn (``compiler.compile_request``, the command line without a terminal)."""
        qs = list(turn.questions)
        if turn.status == "questions":
            if turn.message and turn.message not in qs:
                qs.append(turn.message)
            qs += [f"[choice] {c.describe()}" for c in turn.choices]
        status = turn.status
        res = compiler.CompileResult(status=status, spec=turn.spec, questions=qs, errors=list(turn.errors),
                                     tool_calls=list(self.tool_calls), model=self.model, usage=self.usage.as_dict(),
                                     served_models=sorted(set(self.usage.served)),
                                     custom_transform=turn.custom_transform,
                                     custom_transform_reason=turn.custom_transform_reason,
                                     transcript=list(self.exchange), seconds=self.seconds)
        return res

    # ---- the loop ----
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
        shown = [turn.message or ("" if turn.questions else str((turn.spec or {}).get("summary") or ""))]
        shown += [f"• {q}" for q in turn.questions]
        self.entries.append(Entry("claude", "\n".join(x for x in shown if x), status=turn.status,
                                  understanding=turn.understanding, choices=[c.to_dict() for c in turn.choices],
                                  images=list(turn.images)))
        if self._dropped:                          # a library choice that was not shown: the user hears why
            self.entries.append(Entry("app", "\n".join(self._dropped)))
            self._dropped = []
        return turn

    def _next_choice_id(self) -> str:
        self._choice_n += 1
        return f"c{self._choice_n}"

    def _dispatch(self, block, name: str, inp: Dict[str, Any], images: List[str]) -> Dict[str, Any]:
        tid = _bid(block)
        allowed = {t["name"] for t in self.tools} - {"ask_user", "submit_editspec"}
        try:
            if name not in allowed:
                raise KeyError(f"unknown tool {name}")
            out = getattr(self.batch, name)(**inp)
            if isinstance(out, dict) and out.get("_content_blocks"):
                images += [str(p) for p in out.get("_images", [])]
                return {"type": "tool_result", "tool_use_id": tid, "content": out["_content_blocks"]}
            return {"type": "tool_result", "tool_use_id": tid, "content": json.dumps(out, ensure_ascii=False)[:20000]}
        except Exception as e:  # noqa: BLE001 — every call gets an answer, or the next request is refused
            return {"type": "tool_result", "tool_use_id": tid, "is_error": True, "content": f"{type(e).__name__}: {e}"[:2000]}

    def _advance(self) -> Turn:
        t0 = time.perf_counter()
        try:
            return self._advance_calls()
        finally:
            self.seconds += time.perf_counter() - t0

    def _record_results(self, ordered: List[Dict[str, Any]], pending: bool) -> None:
        extra = {"pending": True} if pending else {}       # pending: sent first in the next user message
        self.exchange.append(llm.results_entry(self.calls, ordered, **extra))

    def _advance_calls(self) -> Turn:
        rejections: List[str] = []                # the errors of every rejected submission in this user turn
        rejected = 0
        images: List[str] = []
        nudged = False
        for call in range(MAX_CALLS_PER_TURN):
            check_pairing(self.messages)
            self.calls += 1
            t_call = time.perf_counter()
            try:
                resp = llm.call(self._client_now(), model=self.model, system=self.system, tools=self.tools,
                                messages=self.messages)
            except Exception as e:  # noqa: BLE001 — classified below
                if not llm.is_api_error(e):
                    raise                  # a defect (a wrong argument, a test's script running out) propagates
                # an API or network error, or Claude not installed: nothing is appended; the user can retry
                self.exchange.append(llm.error_entry(self.calls, e, time.perf_counter() - t_call))
                self.log(f"Claude: the call failed ({type(e).__name__})")
                return self._fail(llm.failure_text(e), rejections)
            self.usage.add(resp, self.model)
            self.exchange.append(llm.response_entry(self.calls, resp, time.perf_counter() - t_call))
            problem = llm.stop_problem(resp)
            if problem:
                return self._fail(problem, rejections)
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
                    asks.append((b, inp))
                    results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "content": "(waiting for the user)"}
                    continue
                if name == "submit_editspec":
                    if accepted is not None:
                        results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "is_error": True,
                                            "content": "one submission per turn — this one was not considered"}
                        continue
                    spec = inp.get("spec")
                    try:
                        errs = compiler.check_submission(spec, self.batch, self.sources)
                    except Exception as e:  # noqa: BLE001 — the call must be answered (rule 14): a spec the checks
                        # cannot even read goes back to Claude as rejected, with what went wrong
                        self.log(f"Claude: the submitted spec could not be checked ({type(e).__name__})")
                        errs = [f"[spec] the spec could not be checked ({type(e).__name__}: {e}) — fix its shape "
                                f"(see the EditSpec JSON schema in the catalog)"]
                    if errs:
                        rejected += 1
                        rejections += errs
                        results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "is_error": True,
                                            "content": "REJECTED — fix and call submit_editspec again:\n- " + "\n- ".join(errs)}
                        continue
                    accepted = (b, spec, inp)
                    results[_bid(b)] = {"type": "tool_result", "tool_use_id": _bid(b), "content": ACCEPTED_RESULT}
                    continue
                results[_bid(b)] = self._dispatch(b, name, inp, images)
            ordered = [dict(results[_bid(b)]) for b in uses]
            if asks:
                if accepted is not None:
                    for r in ordered:
                        if r["tool_use_id"] == _bid(accepted[0]):
                            r["content"] = "not taken: you asked the user in the same turn — resubmit after their reply"
                self.pending, self._ask_ids = ordered, [_bid(b) for b, _ in asks]
                self._record_results(ordered, pending=True)
                return self._finish(self._ask_turn(asks, rejections, images))
            if accepted is not None:
                self.pending = ordered
                self._record_results(ordered, pending=True)
                return self._finish(self._spec_turn(accepted[1], accepted[2], rejections, images))
            if rejected > 1:
                for r in ordered:
                    if str(r.get("content", "")).startswith("REJECTED"):
                        r["content"] += "\n(rejected twice — the user decides what happens next)"
                self.pending = ordered
                self._record_results(ordered, pending=True)
                return self._fail("the spec was rejected twice", rejections)
            if call == MAX_CALLS_PER_TURN - 1:
                self.pending = ordered
                self._record_results(ordered, pending=True)
                break
            self.messages.append({"role": "user", "content": ordered})
            self._record_results(ordered, pending=False)
        return self._fail(f"no spec after {MAX_CALLS_PER_TURN} calls", rejections)

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
            if kind in NAMED_LIBRARY_KINDS:
                if self.mode != "chat":               # the one-shot compile: nobody is there to pick it
                    errors.append(f"a choice was dropped (kind {kind!r} is offered in a conversation only)")
                    continue
                ch = self._library_choice(kind, c, errors)
                if ch is not None:
                    out.append(ch)
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

    def _library_choice(self, kind: str, c: Dict[str, Any], errors: List[str]) -> Optional[Choice]:
        """A choice naming one of the user's library items, built from the library alone (rule 13). The item must
        exist under that exact name and kind (``library.find``: never a path, never a fuzzy match); a recipe must
        load and validate, keep every template file it names, place only words the user can see (``clean_wording``'s
        rule) and plan on this batch (``compiler._dry_errors``, the gate every fresh spec passes); an operation must
        load with its code unchanged (``library.load_operation``). Its label comes from the item (name, description,
        when and from which request it was saved), its ``words`` from ``compiler.placed_words`` — the same function the
        traceability check uses — and its ``item_hash`` pins the files as read now. Claude's ``label`` and ``wording``
        never reach it (a wording is said to be ignored); its ``detail`` is kept as the detail. Whatever fails drops the
        choice, said three ways: in the errors, to the user (an app entry), to Claude (a note with the next message)."""
        from . import library
        from .editspec import SpecError, load_spec, spec_input_files, validate_spec
        name = str(c.get("name") or "").strip()
        if not name:
            errors.append(f"a {kind} choice without a name was dropped")
            return None
        if c.get("wording"):
            errors.append(f"the wording Claude put on the {kind} choice «{name}» was ignored: a library choice's words "
                          f"come from the library item itself")

        def drop(reason: str) -> None:
            errors.append(f"the {kind} choice «{name}» was dropped: {reason}")
            self._dropped.append(f"Claude offered your {kind} «{name}», but {reason} — the choice was not shown.")
            self.notes.append(f"The {kind} choice «{name}» was not shown to the user: {reason}")

        try:
            item = library.find(name, kind)
            item_hash = library.item_hash(item)
        except library.LibraryError as e:
            drop(str(e))
            return None
        except OSError as e:
            drop(f"it cannot be read ({e})")
            return None
        detail = str(c.get("detail") or c.get("label") or "")
        saved = (item.saved_at or "")[:10]
        origin = (f"saved {saved}" if saved else "saved earlier") + \
            (f" from the request «{compiler._cut(item.request, 120)}»" if item.request else "")
        verb = {"recipe": "Use your recipe", "operation": "Try your operation", "snippet": "Insert your snippet"}[kind]
        head = (f"{verb} “{item.name}”"
                + (f" — {compiler._cut(item.description, compiler.DESCRIPTION_CHARS)}" if item.description else ""))
        if kind == SNIPPET_CHOICE_KIND:
            try:
                sn = library.read_snippet(item)
                clean_wording(sn["text"])
            except library.LibraryError as e:
                drop(str(e))
                return None
            except (OSError, ValueError) as e:              # a file that is gone, not JSON, or unreadable words
                drop(f"it cannot be used ({e})")
                return None
            return Choice(self._next_choice_id(), f"{head} ({origin}; Claude then writes where it goes)", "snippet",
                          None, detail, words=[sn["text"]], name=item.name, item_hash=item_hash, snapshot=sn)
        if kind == "operation":
            try:
                library.load_operation(item.name)          # the manifest's sha256 and the whitelist
            except library.LibraryError as e:
                drop(str(e))
                return None
            except OSError as e:
                drop(f"it cannot be read ({e})")
                return None
            return Choice(self._next_choice_id(),
                          f"{head} ({origin}; tried on this batch's sample files first — edited copies only)",
                          "operation", None, detail, name=item.name, item_hash=item_hash)
        try:
            spec = load_spec(library.load_recipe(item.name))
            validate_spec(spec)
        except (library.LibraryError, SpecError) as e:
            drop(str(e))
            return None
        except (OSError, ValueError) as e:                  # a file that is gone, or that is not JSON
            drop(f"it cannot be read ({e})")
            return None
        missing = [t for t in spec_input_files(spec) if not t.is_file()]
        if missing:
            what = ("its template file" if all(t.suffix.lower() == ".docx" for t in missing) else
                    "a file it reads (its template, table or picture)")
            drop(f"{what} is no longer at {', '.join(map(str, missing))} — save the recipe again with the "
                 f"{'template' if what == 'its template file' else 'file'} where it is now")
            return None
        words = list(dict.fromkeys(compiler.placed_words(spec)))
        try:
            for w in words:
                clean_wording(w)
        except ValueError as e:
            drop(f"it places words with characters you cannot see ({e})")
            return None
        dry = compiler._dry_errors(spec, self.batch)
        if dry:
            drop("it plans on none of these files: " + " | ".join(dry[:3]))
            return None
        return Choice(self._next_choice_id(), f"{head} ({origin})", "recipe", None, detail, words=words,
                      name=item.name, item_hash=item_hash, snapshot=spec)

    def _ask_turn(self, asks, rejections, images) -> Turn:
        errors = list(rejections)
        questions, choices, messages, understanding = [], [], [], []
        for _b, inp in asks:
            questions += _texts(inp.get("questions"), "questions", errors)
            choices += self._choices_from(inp.get("choices"), errors)
            if isinstance(inp.get("message"), str) and inp["message"].strip():
                messages.append(inp["message"].strip())
            if isinstance(inp.get("understanding"), str) and inp["understanding"].strip():
                understanding.append(inp["understanding"].strip())
        if not (questions or choices or messages):
            questions = ["(the model asked an empty question)"]
        return Turn("questions", message="\n".join(messages), understanding=" ".join(understanding),
                    questions=questions, choices=choices, errors=errors, images=images)

    def _spec_turn(self, spec, inp, rejections, images) -> Turn:
        # The spec the app gets carries the USER's request, never the text Claude wrote in "request": the advisor and
        # the next pass (set_aside.json → retry) count it among the approved words. A copy — the history keeps the
        # tool call exactly as Claude made it.
        spec = copy.deepcopy(spec)
        spec["request"] = self.request
        summary = str(spec.get("summary") or "")
        if spec.get("unsupported"):
            self.unsupported = {"request": self.request, "unsupported": spec.get("unsupported"),
                                "proposal": spec.get("proposal") or ""}
            verdict = str(inp.get("custom_transform") or "")
            reason = str(inp.get("custom_transform_reason") or "")
            choices = []
            if verdict == "possible":
                choices.append(Choice(self._next_choice_id(), "Have Claude write a one-off custom transform for this "
                                      "(tested on sample files, edited copies only)", "custom_transform",
                                      detail=str(spec.get("proposal") or "")))
            msg = str(spec.get("unsupported"))
            if verdict == "outside_limits":
                msg += ("\nA custom transform can't do this safely yet" + (f": {reason}" if reason else ".") +
                        " It needs a new built-in capability.")
            return Turn("unsupported", message=msg, understanding=summary, choices=choices, spec=spec,
                        errors=list(rejections), images=images, custom_transform=verdict,
                        custom_transform_reason=reason)
        if spec.get("questions"):
            errors = list(rejections)
            questions = _texts(spec["questions"], "spec's questions", errors) or ["(the model asked an empty question)"]
            return Turn("questions", understanding=summary, questions=questions, spec=spec, errors=errors,
                        images=images)
        return Turn("ok", message=summary, understanding=summary, spec=spec, errors=list(rejections), images=images)
