"""Every call Spec Surgeon makes to Claude goes through this module (the compiler's chat, the custom-transform writer,
the advisor and the visual check of the preview samples).

* ``call`` **streams** (``client.beta.messages.stream`` + ``get_final_message``): the output cap is 64,000 tokens, and
  the SDK refuses a non-streaming request that large. Effort ``high``; thinking is left to the model (Opus 5.5 always
  thinks — sending a ``thinking`` setting, a forced ``tool_choice`` or an assistant prefill is a 400 there). The
  server-side refusal fallback is on (``fallbacks="default"``): a request the model's safety filters decline is re-run
  on another model inside the same call.
* ``history_content`` is what a caller appends to its message history **and** reads its tool calls and text from: after
  a fallback, the blocks the abandoned attempt produced before the last ``fallback`` marker (thinking, tool calls) must
  not be echoed back or acted on — only its text is kept.
* ``served`` tells which model actually answered (``resp.model``) and whether a fallback ran.
* ``Usage`` adds up tokens per serving model and estimates the cost from ``PRICES``.

``client()`` builds the real client; callers resolve it at call time through ``compiler._client`` (the advisor through
its own ``advisor._client``), which is what the tests replace with a scripted fake. Without the ``anthropic`` package it
raises ``ClaudeUnavailable``.

Diagnostics, one rule for every caller: ``is_api_error`` tells an error of the call itself (the SDK's, its HTTP stack's,
the network's, the SDK's missing-credentials error, ``ClaudeUnavailable``) from a defect in the caller. An API error ends
the caller's turn, attempt or advice as a recorded failure (``failure_text``: its kind, HTTP status and request id);
anything else propagates. ``response_entry`` / ``error_entry`` / ``results_entry`` / ``transcript_content`` turn the
exchange into plain data for the records (compile.json, conversation.json, codegen/history.json, the advice's
transcript, visual_review.json). They are total — a block they cannot read becomes a ``record_error``, never an
exception (a diagnostic never stops the work it describes) — and they never keep image data (page renders are document
content).
"""
from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_MODEL = os.environ.get("SPEC_SURGEON_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 64000
EFFORT = "high"

# USD per million tokens: (input, output, cache read, 5-minute cache write). An estimate shown to the user, never a bill.
# Update when Anthropic's prices change. A model that is not listed is shown as "price unknown", never guessed.
PRICES: Dict[str, Tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


class ClaudeUnavailable(RuntimeError):
    """The Claude layer is not installed (no ``anthropic`` package): the user's setup, not a defect — an API error for
    ``is_api_error``, so the turn fails with what to install instead of a traceback."""


def client():
    """The real Anthropic client (needs the ``anthropic`` package and a key: ``ANTHROPIC_API_KEY`` or ``ant auth login``)."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise ClaudeUnavailable('install the Claude layer (pip install "spec-surgeon[agent]") and set an API key to use '
                                'typed requests, AI-written transforms, advice and the visual check') from e
    return anthropic.Anthropic()


# ---- running without an API key -----------------------------------------------------------------------------------
# Everything that calls Claude, as the user reads it. Without credentials these are off; everything else — saved specs,
# your library's recipes and operations, the preview, the run and its verification, set-aside passes, diagnostics — runs
# without a key (README → "Running without an API key").
CLAUDE_FEATURES = ("describing a change in plain English (the conversation with Claude)",
                   "custom transforms Claude writes when no built-in operation fits",
                   "Claude's visual check of the preview pages",
                   "advice on set-aside files")
CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def credentials_found() -> bool:
    """Whether a call to Claude could authenticate — decided without calling: ``ANTHROPIC_API_KEY`` or
    ``ANTHROPIC_AUTH_TOKEN`` in the environment (the window puts the key from Settings or Credential Manager there), or
    a credentials profile the SDK finds on its own (``ant auth login``, ``ANTHROPIC_PROFILE``, workload identity). False
    means the app runs keyless: the features in ``CLAUDE_FEATURES`` are off and say so, nothing else changes. (The tests
    script their Claude clients: ``tests/conftest.py`` makes this True unless a test of keyless mode says otherwise.)"""
    if any(os.environ.get(v) for v in CREDENTIAL_ENV):
        return True
    return _sdk_credentials()


def _sdk_credentials() -> bool:
    """A credentials profile the ``anthropic`` SDK would use with no key in the environment. False without the SDK (or
    one too old to have profiles), or when its lookup fails — a profile that cannot be read cannot authenticate either.
    It reads files only; it never calls the API."""
    try:
        from anthropic.lib.credentials import default_credentials
    except Exception:  # noqa: BLE001 — no SDK, or one without credential profiles
        return False
    try:
        return default_credentials() is not None
    except Exception:  # noqa: BLE001 — an explicitly selected profile that is broken
        return False


def call(client, *, model: str, system: str, tools: Sequence[Dict[str, Any]], messages: List[Dict[str, Any]]):
    """One streamed request; returns the final message. Raises whatever the SDK raises: the caller classifies it with
    ``is_api_error`` (an API error is a recorded failure, anything else a defect that propagates) and never half-appends
    a turn."""
    kw = dict(model=model, max_tokens=MAX_TOKENS, system=system, tools=list(tools), messages=messages,
              output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
              betas=[FALLBACK_BETA], fallbacks="default")
    with client.beta.messages.stream(**kw) as stream:
        return stream.get_final_message()


# ---- diagnostics: what a failed call was, and the exchange as plain data ----------------------------------------------

# The SDK and its HTTP stack: anthropic 1.x streams over httpx2/httpcore2, and a stream that drops mid-way raises the
# transport's own error, unwrapped.
API_ERROR_ROOTS = ("anthropic", "httpx", "httpcore", "httpx2", "httpcore2")
_MISSING_CREDENTIALS = "Could not resolve authentication method"
RESULT_CAP = 6000                                  # characters of a tool result or user message kept in a record


def is_api_error(e: BaseException) -> bool:
    """An error of the API call itself — the SDK's (a bad key, a rate limit, an overloaded or unreachable service), its
    HTTP stack's, the network's (``OSError``/``TimeoutError``/``ConnectionError``), the SDK's missing-credentials
    ``TypeError`` or ``ClaudeUnavailable`` (the user's setup) — as opposed to a defect in the caller (a wrong argument,
    a test's script running out), which propagates."""
    if isinstance(e, (ClaudeUnavailable, OSError, TimeoutError, ConnectionError)):
        return True
    root = (type(e).__module__ or "").split(".")[0]
    if root in API_ERROR_ROOTS:
        return True
    return isinstance(e, TypeError) and _MISSING_CREDENTIALS in str(e)


def api_error_text(e: BaseException) -> str:
    """An API error as one line: its kind, HTTP status and request id when the SDK gives them."""
    bits = [f"{type(e).__name__}: {str(e)[:500]}"]
    status = getattr(e, "status_code", None)
    if status:
        bits.append(f"HTTP {status}")
    rid = getattr(e, "request_id", None)
    if not rid:
        headers = getattr(getattr(e, "response", None), "headers", None)
        try:
            rid = headers.get("request-id") if headers is not None else None
        except Exception:  # noqa: BLE001 — a header we cannot read is just not said
            rid = None
    if rid:
        bits.append(f"request id {rid}")
    return " · ".join(bits)


def failure_text(e: BaseException, failed: str = "the request to Claude failed") -> str:
    """What a caller tells the user about an API error: Claude missing, or ``<failed>: <api_error_text>``."""
    if isinstance(e, ClaudeUnavailable):
        return f"Claude is not available: {e}"
    return f"{failed}: {api_error_text(e)}"


def _record_error(e: BaseException) -> str:
    return f"{type(e).__name__}: {str(e)[:300]}"


def block_dict(b: Any) -> Dict[str, Any]:
    """A response content block as plain data for a transcript (the SDK's objects, a dict, or a test's stand-ins). A tool
    call's input is deep-copied (a caller may change it later — the advisor pins a spec's applies_to). Total: a block
    that cannot be read is recorded as a ``record_error``."""
    kind = "?"
    try:
        get = (lambda k: b.get(k)) if isinstance(b, dict) else (lambda k: getattr(b, k, None))
        kind = get("type") or type(b).__name__
        out: Dict[str, Any] = {"type": kind}
        for key in ("text", "id", "name"):
            v = get(key)
            if v is not None:
                out[key] = v
        v = get("input")
        if v is not None:
            out["input"] = copy.deepcopy(dict(v))
        if kind == "thinking":
            out["thinking"] = str(get("thinking") or "")[:8000]
        if kind == "fallback":
            out["from"] = getattr(get("from_") or get("from"), "model", None)
            out["to"] = getattr(get("to"), "model", None)
        return out
    except Exception as e:  # noqa: BLE001 — a diagnostic never stops the work it describes
        return {"type": str(kind), "record_error": _record_error(e)}


def _text_of(content: Any) -> str:
    """A tool result's content as text for a transcript: image data never goes in (pages are document content)."""
    if isinstance(content, (list, tuple)):
        parts = []
        for c in content:
            if block_type(c) == "image":
                parts.append("[image]")
            else:
                t = c.get("text") if isinstance(c, dict) else getattr(c, "text", "")
                parts.append(str(t or ""))
        return " | ".join(parts)
    return "" if content is None else str(content)


def transcript_content(content: Any, cap: int = RESULT_CAP) -> Any:
    """A user message's content as plain data: tool results' and texts' text capped, images reduced to their type.
    Total: what cannot be read is a ``record_error``."""
    try:
        if not isinstance(content, (list, tuple)):
            return str(content)[:cap]
        out: List[Dict[str, Any]] = []
        for c in content:
            try:
                kind = block_type(c)
                if kind == "tool_result":
                    out.append({"type": kind, "tool_use_id": c.get("tool_use_id"), "is_error": bool(c.get("is_error")),
                                "content": _text_of(c.get("content"))[:cap]})
                elif kind == "image":
                    out.append({"type": "image"})
                else:
                    out.append({"type": kind, "text": _text_of([c])[:cap]})
            except Exception as e:  # noqa: BLE001 — a diagnostic never stops the work it describes
                out.append({"record_error": _record_error(e)})
        return out
    except Exception as e:  # noqa: BLE001
        return [{"record_error": _record_error(e)}]


def response_entry(n: int, resp: Any, seconds: float) -> Dict[str, Any]:
    """One call's answer for a transcript: stop reason, time, request id, serving model, tokens and every content block
    (the raw content, a fallback's abandoned attempt included — a record, not the history). Total."""
    try:
        u = getattr(resp, "usage", None)
        model, fell_back = served(resp)
        return {"turn": n, "role": "assistant", "stop_reason": getattr(resp, "stop_reason", None),
                "seconds": round(seconds, 2),
                "request_id": getattr(resp, "_request_id", None) or getattr(resp, "id", None),
                "model": model, "fell_back": fell_back,
                "usage": {k: int(getattr(u, k, 0) or 0) for k in TOKEN_KEYS},
                "content": [block_dict(b) for b in (getattr(resp, "content", None) or [])]}
    except Exception as e:  # noqa: BLE001 — a diagnostic never stops the work it describes
        return {"turn": n, "role": "assistant", "record_error": _record_error(e)}


def error_entry(n: int, e: BaseException, seconds: float) -> Dict[str, Any]:
    """A call that failed, for a transcript. Total."""
    try:
        return {"turn": n, "error": api_error_text(e), "seconds": round(seconds, 2)}
    except Exception as x:  # noqa: BLE001 — a diagnostic never stops the work it describes
        return {"turn": n, "error": type(e).__name__, "record_error": _record_error(x)}


def results_entry(n: int, results: Sequence[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
    """The tool results of one turn, for a transcript (text capped, no image data). Total."""
    try:
        return {"turn": n, "role": "user", "tool_results": [
            {"tool_use_id": r.get("tool_use_id"), "is_error": bool(r.get("is_error")),
             "content": _text_of(r.get("content"))[:RESULT_CAP]} for r in results], **extra}
    except Exception as e:  # noqa: BLE001 — a diagnostic never stops the work it describes
        return {"turn": n, "role": "user", "record_error": _record_error(e), **extra}


def block_type(block) -> str:
    return getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else "") or ""


def history_content(resp) -> List[Any]:
    """The assistant content to append to the history — and the only content to act on. Without a ``fallback`` marker
    this is ``resp.content`` unchanged (thinking blocks and their signatures included). With one, the blocks before the
    last marker belong to a declined attempt: only their text is kept; the marker itself is dropped; everything after it
    is kept unchanged."""
    blocks = list(getattr(resp, "content", None) or [])
    marks = [i for i, b in enumerate(blocks) if block_type(b) == "fallback"]
    if not marks:
        return blocks
    last = marks[-1]
    return [b for b in blocks[:last] if block_type(b) == "text"] + blocks[last + 1:]


def tool_uses(content: Sequence[Any]) -> List[Any]:
    return [b for b in content if block_type(b) == "tool_use"]


def texts(content: Sequence[Any]) -> List[str]:
    return [str(getattr(b, "text", "") or "") for b in content if block_type(b) == "text"]


def served(resp) -> Tuple[Optional[str], bool]:
    """(the model that produced this message, whether a fallback model ran)."""
    usage = getattr(resp, "usage", None)
    iterations = getattr(usage, "iterations", None) or []
    fell_back = any(getattr(i, "type", None) == "fallback_message" for i in iterations)
    return getattr(resp, "model", None), fell_back


def stop_problem(resp) -> Optional[str]:
    """Why a response cannot be used, or None. ``pause_turn`` cannot be continued (that would send a trailing
    assistant message — a prefill, refused by the model), so it is a failure like the others."""
    stop = getattr(resp, "stop_reason", None)
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        return (f"the model declined the request ({getattr(det, 'category', None)}: "
                f"{getattr(det, 'explanation', '') or ''})")
    if stop == "max_tokens":
        return "the model's answer was cut off (max_tokens)"
    if stop == "pause_turn":
        return "the model paused its turn (pause_turn) — nothing was applied; try again"
    return None


class Usage:
    """Tokens per serving model. ``usage.iterations`` (one entry per attempt, each with its own model) is the source of
    truth when present — top-level usage then covers only the final attempt — so the two are never added together."""

    def __init__(self):
        self.by_model: Dict[str, Dict[str, int]] = {}
        self.calls = 0
        self.served: List[str] = []
        self.fallbacks = 0

    def add(self, resp, requested: str) -> None:
        self.calls += 1
        model, fell_back = served(resp)
        if model:
            self.served.append(model)
        self.fallbacks += int(fell_back)
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        entries = getattr(usage, "iterations", None) or None
        if entries:
            for e in entries:
                self._add(getattr(e, "model", None) or requested, e)
        else:
            self._add(model or requested, usage)

    def _add(self, model: str, src) -> None:
        row = self.by_model.setdefault(model, {k: 0 for k in TOKEN_KEYS})
        for k in TOKEN_KEYS:
            row[k] += int(getattr(src, k, 0) or 0)

    def merge(self, other: "Usage") -> None:
        for model, row in other.by_model.items():
            mine = self.by_model.setdefault(model, {k: 0 for k in TOKEN_KEYS})
            for k in TOKEN_KEYS:
                mine[k] += row.get(k, 0)
        self.calls += other.calls
        self.served += other.served
        self.fallbacks += other.fallbacks

    def as_dict(self) -> Dict[str, int]:
        out = {k: 0 for k in TOKEN_KEYS}
        for row in self.by_model.values():
            for k in TOKEN_KEYS:
                out[k] += row[k]
        return out

    def tokens(self) -> int:
        return sum(self.as_dict().values())

    def cost_usd(self) -> Optional[float]:
        """Estimated USD, or None when a model that did some of the work has no price in ``PRICES``."""
        total = 0.0
        for model, row in self.by_model.items():
            if not any(row.values()):
                continue
            price = PRICES.get(model)
            if price is None:
                return None
            pin, pout, pread, pwrite = price
            total += (row["input_tokens"] * pin + row["output_tokens"] * pout
                      + row["cache_read_input_tokens"] * pread + row["cache_creation_input_tokens"] * pwrite) / 1e6
        return total

    def line(self) -> str:
        """One line for the window and the command line: "12,345 tokens · ≈ $0.42"."""
        n = self.tokens()
        cost = self.cost_usd()
        money = f"≈ ${cost:.2f}" if cost is not None else "cost unknown (a model without a listed price answered)"
        return f"{n:,} tokens · {money}" + (f" · {self.fallbacks} fallback(s)" if self.fallbacks else "")

    def to_dict(self) -> Dict[str, Any]:
        return {**self.as_dict(), "by_model": self.by_model, "calls": self.calls, "served_models": sorted(set(self.served)),
                "fallbacks": self.fallbacks, "cost_usd": self.cost_usd()}
