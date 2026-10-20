"""Every call Spec Surgeon makes to Claude goes through this module (the compiler's chat, the custom-transform writer,
the advisor and the visual check of the preview samples).

* ``call`` **streams** (``client.beta.messages.stream`` + ``get_final_message``): the output cap is 128,000 tokens (the
  most Opus 5.5 writes), and the SDK refuses a non-streaming request that large. Effort ``max``; thinking is left to the
  model (Opus 5.5 always thinks — sending a ``thinking`` setting, a forced ``tool_choice`` or an assistant prefill is a
  400 there). The server-side refusal fallback is on (``fallbacks="default"``): a request the model's safety filters
  decline is re-run on another model inside the same call.
* ``call`` re-sends a call that failed transiently (``is_transient``: overloaded, rate-limited, a 5xx, a dropped
  connection or stream) up to ``RETRIES`` times after ``RETRY_DELAYS``; each attempt passes the same preflight and
  counts against the same limits. Nothing of a failed attempt was returned, so nothing of it was acted on.
* The kill switch: inside ``cancellation_scope`` a set event refuses the next call, and closes the stream of a call
  that is running (``_stop_on_cancel``), so the provider stops writing at once. That call raises ``UserCancelled``; its
  spending is settled from what the stream reported plus at most what it could have written in the time it ran.
* ``history_content`` is what a caller appends to its message history **and** reads its tool calls and text from: after
  a fallback, the blocks the abandoned attempt produced before the last ``fallback`` marker (thinking, tool calls) must
  not be echoed back or acted on — only its text is kept.
* ``served`` tells which model actually answered (``resp.model``) and whether a fallback ran.
* ``Usage`` adds up tokens per serving model and estimates the cost from ``PRICES``.
* ``budget_scope`` gives recovery and its nested callers shared call, token and estimated USD limits, with optional
  model/effort overrides. Calls outside a scope retain the ordinary defaults above.

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

import contextlib
import copy
import json
import math
import os
import socket
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import diagnostics as diag
from .resources import (DEFAULT_EFFORT, DEFAULT_MAX_CALLS, DEFAULT_MAX_COST_USD,
                        DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_MAX_TOTAL_TOKENS, positive_integer)

_CANCEL_EVENT = ContextVar("spec_surgeon_cancel_event", default=None)
_CALL_OBSERVER = ContextVar("spec_surgeon_call_observer", default=None)


class UserCancelled(RuntimeError):
    """A user cancelled between model calls; no further provider request is started."""


@contextmanager
def cancellation_scope(event):
    token = _CANCEL_EVENT.set(event)
    try:
        yield
    finally:
        _CANCEL_EVENT.reset(token)


_STREAM_OBSERVER: ContextVar = ContextVar("spec_surgeon_stream_observer", default=None)
_THINKING_DISPLAY = {"type": "adaptive", "display": "summarized"}   # the model's thinking summary, for the window
_thinking_display_refused = False                                    # latched off for the process after one 400


@contextmanager
def stream_observer_scope(observer):
    """Observe a call's stream as it arrives, for the window: ``observer(event)`` gets ``{"type": "started"}``,
    ``{"type": "text_delta", "text"}``, ``{"type": "thinking_delta", "text"}`` and ``{"type": "tool", "name"}``
    (content-free apart from the model's own words to the user). Never affects the call: an observer that raises is
    reported once and ignored."""
    token = _STREAM_OBSERVER.set(observer)
    try:
        yield
    finally:
        _STREAM_OBSERVER.reset(token)


def _observe(event: Dict[str, Any]) -> None:
    observer = _STREAM_OBSERVER.get()
    if observer is None:
        return
    try:
        observer(event)
    except Exception as error:  # noqa: BLE001 — the window's problem, never the call's
        diag.event("model.stream.observer_failed", level="warning", error_type=type(error).__name__)


@contextmanager
def model_progress_scope(observer):
    """Observe content-free call/usage updates throughout one job, including nested model work."""
    token = _CALL_OBSERVER.set(observer)
    try:
        yield
    finally:
        _CALL_OBSERVER.reset(token)


def _model_progress(phase, response=None, model=None):
    observer = _CALL_OBSERVER.get()
    if observer is None:
        return
    try:
        usage = None
        if response is not None:
            usage = Usage()
            usage.add(response, model)
        observer(phase, usage)
    except Exception as error:  # diagnostics must never affect request accounting or accepted responses
        diag.event("model.progress.unavailable", level="warning", error_type=type(error).__name__)

DEFAULT_MODEL = os.environ.get("SPEC_SURGEON_MODEL", "claude-opus-5-5")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = DEFAULT_MAX_OUTPUT_TOKENS
EFFORT = DEFAULT_EFFORT
RETRIES = 2                       # re-sends of a call that failed transiently (tests/conftest.py sets 0)
RETRY_DELAYS = (5.0, 20.0)        # seconds before each re-send; a longer retry-after from the provider wins
RETRY_AFTER_CAP = 60.0            # the longest a provider's retry-after is honoured before re-sending
STOP_POLL_SECONDS = 0.25          # how often a running call looks at the kill switch
STOPPED_OUTPUT_RATE = 150         # output tokens per second: an upper bound for what a stopped response wrote
STOPPED = "Stopped by you. The AI step that was running was cut off; your source files were not changed."
_TRANSIENT_TYPES = {"overloaded_error", "rate_limit_error", "api_error", "timeout_error"}
_TRANSPORT_ROOTS = ("httpx", "httpcore", "httpx2", "httpcore2")

# USD per million tokens: (input, output, cache read, 5-minute cache write). An estimate shown to the user, never a bill.
# Update when Anthropic's prices change. A model that is not listed is shown as "price unknown", never guessed.
PRICES: Dict[str, Tuple[float, float, float, float]] = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-opus-5": (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8": (5.00, 25.00, 0.50, 6.25),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
}
TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
_CALL_BUDGETS: ContextVar[tuple] = ContextVar("spec_surgeon_call_budgets", default=())


class ClaudeUnavailable(RuntimeError):
    """The Claude layer is not installed (no ``anthropic`` package): the user's setup, not a defect — an API error for
    ``is_api_error``, so the turn fails with what to install instead of a traceback."""


class IncompleteResponse(RuntimeError):
    """The provider did not finish a usable response; no partial tools may be acted on. ``transient``: the stream
    dropped before it finished, which a re-send can cure (a malformed or cut-off answer is not)."""

    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


def _stream_events(stream):
    try:
        yield from stream
    except (ValueError, IndexError) as error:
        # JSON decoding and invalid SSE block indices can fail in the SDK before
        # it yields an event. These are unusable provider responses, not tools.
        raise IncompleteResponse("the model response stream contained malformed content") from error


def _finished_message(stream, seen=None, cancel=None):
    """Check actual SSE completion rather than trusting the SDK's partial snapshot.

    The SDK deliberately parses incomplete JSON while streaming and returns its latest
    snapshot even on clean EOF. Keep the original tool JSON until the stream finishes,
    so an unfinished object cannot become a valid-looking tool argument dictionary.
    Non-iterable adapters still have to supply a completed response stop reason.
    ``seen`` collects the usage the stream reports on its way (what a stopped call is settled from); a set ``cancel``
    stops reading at the next event.
    """
    if callable(getattr(stream, "__iter__", None)):
        started, stopped, opened, tool_json = False, False, set(), {}
        for event in _stream_events(stream):
            if cancel is not None and cancel.is_set():
                raise UserCancelled(STOPPED)
            kind = getattr(event, "type", None)
            if seen is not None and kind in ("message_start", "message_delta"):
                usage = getattr(getattr(event, "message", None), "usage", None) or getattr(event, "usage", None)
                if usage is not None:
                    seen.setdefault("usage", {}).update(
                        {k: int(getattr(usage, k, 0) or 0) for k in TOKEN_KEYS if getattr(usage, k, None) is not None})
            if kind == "message_start":
                started = True
                _observe({"type": "started"})
            elif kind == "content_block_start":
                index = event.index
                opened.add(index)
                block = event.content_block
                if block_type(block) == "fallback":
                    # Abandoned tool calls before the last fallback are never acted on.
                    tool_json.clear()
                elif block_type(block) in ("tool_use", "server_tool_use", "mcp_tool_use"):
                    tool_json[index] = ""
                    _observe({"type": "tool", "name": str(getattr(block, "name", "") or "")})
            elif kind == "content_block_delta" and getattr(event.delta, "type", None) == "input_json_delta":
                if event.index in tool_json:
                    tool_json[event.index] += event.delta.partial_json
            elif kind == "content_block_delta" and getattr(event.delta, "type", None) == "text_delta":
                _observe({"type": "text_delta", "text": str(getattr(event.delta, "text", "") or "")})
            elif kind == "content_block_delta" and getattr(event.delta, "type", None) == "thinking_delta":
                _observe({"type": "thinking_delta", "text": str(getattr(event.delta, "thinking", "") or "")})
            elif kind == "content_block_stop":
                opened.discard(event.index)
            elif kind == "message_stop":
                stopped = True
        if not started or not stopped or opened:
            raise IncompleteResponse("the model response stream ended before its final message and content blocks "
                                     "completed", transient=True)
        for payload in tool_json.values():
            if payload:
                try:
                    def reject_constant(value):
                        raise ValueError(f"invalid JSON constant {value}")
                    value = json.loads(payload, parse_constant=reject_constant)
                    if not isinstance(value, dict):
                        raise ValueError()
                except (ValueError, TypeError) as error:
                    raise IncompleteResponse("the model response contained incomplete or invalid tool JSON") from error
    response = stream.get_final_message()
    stop = getattr(response, "stop_reason", None)
    if stop == "model_context_window_exceeded":
        raise IncompleteResponse("the model's answer was cut off (model_context_window_exceeded); no tools were accepted")
    if stop not in ("end_turn", "tool_use", "stop_sequence", "max_tokens", "pause_turn", "refusal"):
        raise IncompleteResponse("the model response did not include a recognized final stop reason")
    return response


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


def call(client, *, model: str, system: str, tools: Sequence[Dict[str, Any]], messages: List[Dict[str, Any]],
         max_output_tokens: Optional[int] = None, effort: Optional[str] = None):
    """One streamed request; returns the final message. A transient failure is re-sent (``RETRIES``); after the last
    attempt it raises whatever the SDK raised: the caller classifies it with ``is_api_error`` (an API error is a
    recorded failure, anything else a defect that propagates) and never half-appends a turn. Inside ``budget_scope``,
    every caller shares its limits, including nested transform and visual calls, and every attempt counts against them.
    A refused preflight raises ``BudgetExceeded`` before opening a stream; ordinary calls are unchanged. The kill switch
    (``cancellation_scope``) raises ``UserCancelled`` before a call, while one runs, or while waiting to re-send."""
    cancel = _CANCEL_EVENT.get()
    for attempt in range(RETRIES + 1):
        if cancel is not None and cancel.is_set():
            raise UserCancelled("Cancelled before the next model call. Your source files were not changed.")
        with diag.scope(model_call_id=diag.new_id()):
            with diag.span("model.call", requested_model=model, attempt=attempt + 1) as record:
                try:
                    return _call(client, model=model, system=system, tools=tools, messages=messages, record=record,
                                 max_output_tokens=max_output_tokens, effort=effort)
                except BudgetExceeded as exc:
                    diag.event("model.budget.exhausted", level="warning", reason=str(exc), budgets=_budget_records())
                    raise
                except Exception as exc:  # noqa: BLE001 — re-sent only when transient; everything else propagates
                    if attempt >= RETRIES or not is_transient(exc):
                        raise
                    delay = _retry_delay(exc, attempt)
                    record.update(retrying=True, error=_record_error(exc))
                    _observe({"type": "retry"})      # the window drops the abandoned attempt's words
                    diag.event("model.call.retry", level="warning", attempt=attempt + 1, delay_seconds=delay,
                               error=api_error_text(exc)[:300])
        if cancel is not None:
            if cancel.wait(delay):
                raise UserCancelled("Cancelled before the next model call. Your source files were not changed.")
        elif delay > 0:
            time.sleep(delay)
    raise AssertionError("unreachable: the last attempt returns or raises")


def is_transient(e: BaseException) -> bool:
    """A failure a re-send can cure: an overloaded or rate-limited service, a 5xx, a dropped connection, or a stream
    that ended early. Never a request the API refused (4xx), a stop, a paused allowance or missing credentials."""
    if isinstance(e, (UserCancelled, BudgetExceeded, ClaudeUnavailable)):
        return False
    if isinstance(e, IncompleteResponse):
        return e.transient
    status = getattr(e, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and status >= 400:
        return status in (408, 409, 429) or status >= 500
    body = getattr(e, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    kind = getattr(e, "type", None) or (error.get("type") if isinstance(error, dict) else None)
    if kind in _TRANSIENT_TYPES:                    # an error event in a stream that opened with 200
        return True
    if isinstance(e, (OSError, TimeoutError, ConnectionError)):
        return True
    root = (type(e).__module__ or "").split(".")[0]
    if root in _TRANSPORT_ROOTS:
        return True
    return root == "anthropic" and type(e).__name__ in ("APIConnectionError", "APITimeoutError")


def _retry_delay(e: BaseException, attempt: int) -> float:
    delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)] if RETRY_DELAYS else 0.0
    headers = getattr(getattr(e, "response", None), "headers", None)
    try:
        after = float(headers.get("retry-after")) if headers is not None and headers.get("retry-after") else None
    except (TypeError, ValueError, AttributeError):
        after = None
    if after is not None and delay > 0:
        delay = max(delay, min(after, RETRY_AFTER_CAP))
    return delay


def _sever(stream) -> None:
    """Cut a running stream off under the read that waits on it, and the provider sees the client gone and stops
    writing. No one call wakes a blocked read everywhere: a shutdown does on Linux, closing the socket does on Windows,
    so its socket gets both. A stream without a reachable socket (a test's stand-in) is closed instead."""
    try:
        network = getattr(getattr(stream, "response", None), "extensions", {}).get("network_stream")
        sock = network.get_extra_info("socket") if network is not None else None
    except Exception:  # noqa: BLE001 — no socket to reach; closing the stream below is the fallback
        sock = None
    if sock is not None:
        for cut in (lambda: sock.shutdown(socket.SHUT_RDWR), sock.close):
            try:
                cut()
            except OSError:                      # already shut down or closed by the other side
                pass
        return
    try:
        stream.close()
    except Exception:  # noqa: BLE001 — the read it interrupts reports the failure
        pass


@contextmanager
def _stop_on_cancel(stream, cancel):
    """The kill switch for a call that is running: when the user stops, cut its stream off at once (``_sever``). The
    provider stops writing, and the read waiting in this thread fails, instead of the response running to its end."""
    if cancel is None:
        yield
        return
    done = threading.Event()

    def watch():
        while not done.is_set():
            if cancel.wait(STOP_POLL_SECONDS):
                if not done.is_set():
                    _sever(stream)
                return

    watcher = threading.Thread(target=watch, name="spec-surgeon-stop", daemon=True)
    watcher.start()
    try:
        yield
    finally:
        done.set()
        watcher.join(timeout=STOP_POLL_SECONDS * 4)


@contextlib.contextmanager
def _open_stream(client, kw):
    """``with client.beta.messages.stream(**kw) as stream``; a request refused for its thinking display (a 400
    naming ``thinking.display``) is sent again without it, once, and the display stays off for the process. The
    SDK sends the request when the manager is ENTERED, not when it is built, so the manager is entered here, inside
    the catch; a 400 for anything else (a thinking block the history carries, say) propagates untouched."""
    global _thinking_display_refused
    manager = client.beta.messages.stream(**kw)
    try:
        stream = manager.__enter__()
    except Exception as e:  # noqa: BLE001 — classified below; anything else propagates untouched
        status = getattr(e, "status_code", None)
        if "thinking" not in kw or status != 400 or "thinking.display" not in str(e).lower():
            raise
        _thinking_display_refused = True
        diag.event("model.thinking_display.refused", level="warning", error=str(e)[:300])
        manager = client.beta.messages.stream(**{k: v for k, v in kw.items() if k != "thinking"})
        stream = manager.__enter__()
    try:
        yield stream
    finally:
        manager.__exit__(None, None, None)


def _budget_records() -> List[Dict[str, Any]]:
    """A budget snapshot is evidence only; malformed provider usage cannot break a call while logging it."""
    try:
        return [budget.to_dict() for budget in _CALL_BUDGETS.get()]
    except Exception as exc:  # noqa: BLE001 — diagnostics must not affect request accounting
        return [{"record_error": _record_error(exc)}]


def _response_metadata(resp, requested):
    """A content-free summary, including fallback usage; never reads response blocks."""
    try:
        usage = Usage()
        usage.add(resp, requested)
        model, fell_back = served(resp)
        return {"request_id": getattr(resp, "_request_id", None) or getattr(resp, "id", None),
                "stop_reason": getattr(resp, "stop_reason", None), "served_model": model,
                "fell_back": fell_back, "usage": usage.to_dict()}
    except Exception as exc:  # noqa: BLE001 — malformed diagnostic metadata cannot discard a response
        return {"record_error": _record_error(exc)}


def _call(client, *, model, system, tools, messages, record, max_output_tokens, effort):
    budgets = _CALL_BUDGETS.get()
    max_tokens = MAX_TOKENS if max_output_tokens is None else positive_integer("max_output_tokens", max_output_tokens)
    if max_tokens > MAX_TOKENS:
        raise ValueError(f"max_output_tokens must not exceed {MAX_TOKENS}")
    effort = EFFORT if effort is None else effort
    if effort not in ("low", "medium", "high", "max"):
        raise ValueError("effort must be low, medium, high or max")
    retries_disabled = False
    tools = list(tools)
    record.update(phase="preflight", message_count=len(messages), tool_count=len(tools), budgeted=bool(budgets))
    if budgets:
        model = next((b.model for b in reversed(budgets) if b.model is not None), model)
        max_tokens = min(max_tokens, *(b.max_output_tokens for b in budgets))
        effort = budgets[-1].effort
        record.update(model=model, max_output_tokens=max_tokens, effort=effort)
        try:
            estimated_input = estimate_input_tokens(system=system, tools=tools, messages=messages)
        except (TypeError, ValueError) as e:
            for budget in budgets:
                budget.exhausted_reason = f"cannot estimate this request's input tokens: {e}"
            raise BudgetExceeded(budgets[-1].exhausted_reason) from e
        reservations = [b._preflight(model, estimated_input, max_tokens) for b in budgets]
        # SDK retries are additional HTTP attempts invisible to this call site. ``call`` re-sends a transient failure
        # itself, so that every attempt passes the same preflight and counts against the same cap.
        options = getattr(client, "with_options", None)
        if callable(options):
            client = options(max_retries=0)
            retries_disabled = True
        for budget, reservation in zip(budgets, reservations):
            budget._reserve(reservation)
        diag.event("model.budget.reserved", estimated_input_tokens=estimated_input,
                   max_output_tokens=max_tokens, budgets=_budget_records())
    kw = dict(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages,
              output_config={"effort": effort}, cache_control={"type": "ephemeral"},
              betas=[FALLBACK_BETA], fallbacks="default")
    if not _thinking_display_refused and _STREAM_OBSERVER.get() is not None:
        kw["thinking"] = dict(_THINKING_DISPLAY)   # the summary the window shows; refused once → never sent again
    record.update(model=model, max_output_tokens=max_tokens, effort=effort, phase="stream.open")
    diag.event("model.request.prepared", model=model, max_output_tokens=max_tokens, effort=effort,
               message_count=len(messages), tool_count=len(tools), budgeted=bool(budgets),
               retries_disabled=retries_disabled)
    _model_progress("started")
    cancel = _CANCEL_EVENT.get()
    seen: Dict[str, Any] = {}
    started = time.monotonic()
    try:
        with _open_stream(client, kw) as stream:
            record["phase"] = "stream.receive"
            diag.event("model.stream.opened")
            with _stop_on_cancel(stream, cancel):
                resp = _finished_message(stream, seen, cancel)
    except BaseException as exc:
        stopped = cancel is not None and cancel.is_set()
        for budget, reservation in zip(budgets, reservations if budgets else ()):
            budget.failed_calls += 1
            if stopped:
                budget._settle_stopped(model, reservation, seen.get("usage"), max_tokens, time.monotonic() - started)
            else:
                # Keep the reservation: a dropped stream may have consumed tokens without reporting its usage.
                budget._abandon(reservation)
        if budgets:
            record["budgets"] = _budget_records()
        if stopped and isinstance(exc, Exception):
            diag.event("model.call.stopped", seconds=round(time.monotonic() - started, 2), usage_seen=bool(seen))
            if isinstance(exc, UserCancelled):
                raise
            raise UserCancelled(STOPPED) from exc
        raise
    _model_progress("completed", resp, model)
    record["phase"] = "usage.accounting"
    for budget, reservation in zip(budgets, reservations if budgets else ()):
        budget._complete(resp, model, reservation)
    # The transcript remains the source of response content. Events contain only metadata, never prompts,
    # tool payloads, page images or authentication headers.
    record.update(_response_metadata(resp, model))
    record["phase"] = "complete"
    if budgets:
        diag.event("model.budget.accounted", budgets=_budget_records())
    return resp


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
    if isinstance(e, (ClaudeUnavailable, IncompleteResponse, OSError, TimeoutError, ConnectionError)):
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
    try:
        return f"{type(e).__name__}: {str(e)[:300]}"
    except Exception:  # even an exception's broken __str__ must not replace the original result
        return type(e).__name__


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
    if stop is None:
        return "the model response ended without a final stop reason"
    if stop == "refusal":
        det = getattr(resp, "stop_details", None)
        return (f"the model declined the request ({getattr(det, 'category', None)}: "
                f"{getattr(det, 'explanation', '') or ''})")
    if stop == "max_tokens":
        return "the model's answer was cut off (max_tokens)"
    if stop == "model_context_window_exceeded":
        return "the model's answer was cut off (model_context_window_exceeded)"
    if stop == "pause_turn":
        return "the model paused its turn (pause_turn) — nothing was applied; try again"
    if stop not in ("end_turn", "tool_use", "stop_sequence"):
        return "the model response has an unrecognized final stop reason"
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


# ---- recovery's task-local spending and request limits -------------------------------------------------------------

class BudgetExceeded(RuntimeError):
    """A recovery request was stopped locally. This is not an API error or evidence of a document defect."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def estimate_input_tokens(*, system: str, tools: Sequence[Dict[str, Any]], messages: List[Dict[str, Any]]) -> int:
    """An intentionally high input estimate, not a tokenizer or a guarantee of provider billing.

    Text uses one token per UTF-8 byte plus envelope overhead (rather than the optimistic chars/4 heuristic).
    Rendered-page images use the 4,784-token ceiling of the supported models, never their base64 byte count. The
    provider's hidden prompt overhead, future model/image pricing, and server fallback attempts remain uncertain.
    Unknown document/audio content is refused rather than silently counted as a short source URL.
    """
    def count(value: Any) -> int:
        if isinstance(value, str):
            return len(value.encode("utf-8")) + 8
        if value is None or isinstance(value, (bool, int, float)):
            return len(str(value)) + 8
        if isinstance(value, (list, tuple)):
            return 16 + sum(count(v) for v in value)
        if isinstance(value, dict):
            kind = value.get("type")
            if kind == "image" and "source" in value:
                return 4784 + 64
            if kind in ("document", "audio", "video") and "source" in value:
                raise ValueError(f"{kind} input is not supported by the recovery token estimator")
            return 32 + sum(count(k) + count(v) for k, v in value.items())
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return count(dump(mode="json", exclude_none=True))
        if hasattr(value, "__dict__"):
            return count(vars(value))
        raise TypeError(f"unsupported request value {type(value).__name__}")

    return 512 + count(system) + count(tools) + count(messages)


class CallBudget:
    """Shared limits for one recovery task, installed with ``budget_scope(budget)``.

    Calls and output caps are enforced mechanically. Token and USD limits use conservative input/output preflight
    reservations, then the actual reported usage, including every server fallback iteration. They are estimated
    ceilings, NOT hard provider-billing caps: tokenization, unreported usage and server-side fallbacks may exceed a
    reservation. Such overruns stop subsequent calls, never discard the response or its usage diagnostics.

    Failed calls count and retain their reservations because a dropped stream can still have incurred a charge. A call
    the user stopped (the kill switch) is settled from the usage its stream reported plus at most what it could have
    written in the time it ran. ``spent_usd`` is what the window shows: finished and failed calls, never the
    reservation of a call still running.
    ``usage`` is this budget's independent ledger; callers continue adding the same response to their own Usage,
    but must not merge those ledgers back into this one. ``calls`` includes failed attempts; ``usage.calls`` counts
    returned responses only. The optional ``None`` limits disable token/USD checks, never the call/output caps.
    """

    def __init__(self, *, model: Optional[str] = None, effort: str = DEFAULT_EFFORT,
                 max_calls: int = DEFAULT_MAX_CALLS, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                 max_total_tokens: Optional[int] = DEFAULT_MAX_TOTAL_TOKENS,
                 max_cost_usd: Optional[float] = DEFAULT_MAX_COST_USD):
        if model is not None and (not isinstance(model, str) or not model.strip() or model != model.strip()):
            raise ValueError("model must be a non-empty model name without surrounding whitespace, or None")
        if not isinstance(effort, str) or effort not in ("low", "medium", "high", "max"):
            raise ValueError("effort must be low, medium, high or max")
        for name, value in (("max_calls", max_calls), ("max_output_tokens", max_output_tokens),
                            ("max_total_tokens", max_total_tokens)):
            if name == "max_total_tokens" and value is None:
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer (not a boolean)")
        if max_output_tokens > MAX_TOKENS:
            raise ValueError(f"max_output_tokens must not exceed {MAX_TOKENS}")
        if max_cost_usd is not None:
            try:
                valid_cost = (not isinstance(max_cost_usd, bool) and isinstance(max_cost_usd, (int, float))
                              and math.isfinite(max_cost_usd) and max_cost_usd > 0)
            except OverflowError:
                valid_cost = False
            if not valid_cost:
                raise ValueError("max_cost_usd must be a finite positive number (not a boolean), or None")
            max_cost_usd = float(max_cost_usd)
        self.model, self.effort = model, effort
        self.max_calls, self.max_output_tokens = max_calls, max_output_tokens
        self.max_total_tokens, self.max_cost_usd = max_total_tokens, max_cost_usd
        self.calls = 0
        self.failed_calls = 0
        self.unreported_calls = 0
        self.usage = Usage()
        self.exhausted_reason: Optional[str] = None
        self._reserved_tokens = 0
        self._reserved_cost_usd = 0.0
        self._inflight_cost_usd = 0.0             # the part of _reserved_cost_usd held by calls still running
        self._unpriced_reservations = 0
        self._unpriced_response = False

    @property
    def estimated_cost_usd(self) -> Optional[float]:
        cost = self.usage.cost_usd()
        return None if cost is None or self._unpriced_reservations or self._unpriced_response else cost + self._reserved_cost_usd

    @property
    def spent_usd(self) -> Optional[float]:
        """The estimated spending so far: the reported usage of finished calls plus the reservations kept for calls that
        failed without reporting theirs. A call still running counts once it ends. None when a model's price is unknown."""
        cost = self.estimated_cost_usd
        return None if cost is None else max(0.0, cost - self._inflight_cost_usd)

    @property
    def accounted_tokens(self) -> int:
        return self.usage.tokens() + self._reserved_tokens

    def _stop(self, reason: str):
        self.exhausted_reason = reason
        raise BudgetExceeded(reason)

    def _preflight(self, model: str, estimated_input: int, max_output: int) -> Tuple[int, Optional[float]]:
        if self.exhausted_reason:
            raise BudgetExceeded(self.exhausted_reason)
        if self.calls >= self.max_calls:
            self._stop(f"recovery call limit reached ({self.max_calls} calls)")
        token_reservation = estimated_input + max_output
        if self.max_total_tokens is not None and self.accounted_tokens + token_reservation > self.max_total_tokens:
            self._stop(f"recovery token budget cannot cover the next request: {token_reservation:,} estimated tokens "
                       f"plus {self.accounted_tokens:,} accounted, limit {self.max_total_tokens:,}")
        price = PRICES.get(model)
        if price is None:
            if self.max_cost_usd is not None:
                self._stop(f"price unknown for {model}; cannot enforce an estimated USD recovery budget")
            cost_reservation = None
        else:
            pin, pout, pread, pwrite = price
            # A new prompt may be a cache write, which is more expensive than ordinary input.
            cost_reservation = (estimated_input * max(pin, pread, pwrite) + max_output * pout) / 1e6
        if self.max_cost_usd is not None:
            cost = self.estimated_cost_usd
            if cost is None:
                self._stop("a serving or fallback model has an unknown price; further budgeted requests are stopped")
            if cost + cost_reservation > self.max_cost_usd:
                self._stop(f"estimated recovery spending ceiling cannot cover the next request: "
                           f"${cost_reservation:.4f} reserved plus ${cost:.4f} accounted, "
                           f"ceiling ${self.max_cost_usd:.4f} (an estimate, not a provider billing cap)")
        return token_reservation, cost_reservation

    def _reserve(self, reservation: Tuple[int, Optional[float]]) -> None:
        self.calls += 1
        self._reserved_tokens += reservation[0]
        if reservation[1] is None:
            self._unpriced_reservations += 1
        else:
            self._reserved_cost_usd += reservation[1]
            self._inflight_cost_usd += reservation[1]

    def _abandon(self, reservation: Tuple[int, Optional[float]]) -> None:
        """A call that failed without reporting usage: no longer running, its reservation kept as spent."""
        if reservation[1] is not None:
            self._inflight_cost_usd = max(0.0, self._inflight_cost_usd - reservation[1])

    def _settle_stopped(self, model: str, reservation: Tuple[int, Optional[float]], seen: Optional[Dict[str, int]],
                        max_output: int, seconds: float) -> None:
        """A call the user stopped: the input its stream reported when it opened (else the preflight estimate), and the
        larger of the output it reported and what it could have written in ``seconds`` — never more than ``max_output``."""
        self._abandon(reservation)
        seen = dict(seen or {})
        bound = min(max_output, math.ceil(max(0.0, seconds) * STOPPED_OUTPUT_RATE))
        if not any(seen.get(k) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
            seen["input_tokens"] = max(0, reservation[0] - max_output)
        seen["output_tokens"] = max(int(seen.get("output_tokens", 0) or 0), bound)
        self.usage._add(model, SimpleNamespace(**{k: int(seen.get(k, 0) or 0) for k in TOKEN_KEYS}))
        self._reserved_tokens -= reservation[0]
        if reservation[1] is None:
            self._unpriced_reservations -= 1
        else:
            self._reserved_cost_usd = max(0.0, self._reserved_cost_usd - reservation[1])

    def _complete(self, resp: Any, model: str, reservation: Tuple[int, Optional[float]]) -> None:
        self._abandon(reservation)                # no longer running, whatever it reported
        self.usage.add(resp, model)
        if getattr(resp, "usage", None) is None:
            self.unreported_calls += 1
            served_model, _ = served(resp)
            if served_model and served_model not in PRICES:
                self._unpriced_response = True
        else:
            self._reserved_tokens -= reservation[0]
            if reservation[1] is None:
                self._unpriced_reservations -= 1
            else:
                self._reserved_cost_usd = max(0.0, self._reserved_cost_usd - reservation[1])
        cost = self.estimated_cost_usd
        if self.max_cost_usd is not None and cost is None:
            self.exhausted_reason = "a serving or fallback model has an unknown price; further budgeted requests are stopped"
        elif self.max_cost_usd is not None and cost >= self.max_cost_usd:
            self.exhausted_reason = (f"estimated recovery spending ceiling reached (${cost:.4f} accounted, "
                                     f"${self.max_cost_usd:.4f} ceiling); provider billing may differ")
        elif self.max_total_tokens is not None and self.accounted_tokens >= self.max_total_tokens:
            self.exhausted_reason = f"recovery token budget reached ({self.accounted_tokens:,} tokens accounted)"

    def to_dict(self) -> Dict[str, Any]:
        return {"model": self.model, "effort": self.effort, "max_calls": self.max_calls,
                "max_output_tokens": self.max_output_tokens, "max_total_tokens": self.max_total_tokens,
                "max_cost_usd": self.max_cost_usd, "calls": self.calls, "failed_calls": self.failed_calls,
                "unreported_calls": self.unreported_calls, "usage": copy.deepcopy(self.usage.to_dict()),
                "accounted_tokens": self.accounted_tokens, "estimated_cost_usd": self.estimated_cost_usd,
                "spent_usd": self.spent_usd,
                "unreported_reserved_tokens": self._reserved_tokens,
                "unreported_reserved_cost_usd": None if self._unpriced_reservations else self._reserved_cost_usd,
                "exhausted_reason": self.exhausted_reason,
                "cost_basis": "Listed-price estimate, not a provider bill or guaranteed billing cap. Input preflight "
                              "uses a conservative token estimate; failed/unreported calls retain reservations. "
                              "Server fallback attempts count within one call and may exceed its reservation."}


@contextmanager
def budget_scope(budget: CallBudget):
    """Apply a task-local budget and restore the prior context even after an exception.

    Nested budgets retain every outer limit; the innermost model/effort override wins and the smallest output cap
    applies. Re-entering the same budget does not double charge. ContextVars isolate unrelated threads/tasks (a new
    OS thread must explicitly copy the context or enter its own scope).
    """
    if not isinstance(budget, CallBudget):
        raise TypeError("budget_scope requires a CallBudget")
    current = _CALL_BUDGETS.get()
    token = _CALL_BUDGETS.set(current if budget in current else (*current, budget))
    try:
        yield budget
    finally:
        _CALL_BUDGETS.reset(token)
