"""A scripted stand-in for the Anthropic client — no API calls, ever.

``FakeClient(*responses)`` answers ``client.beta.messages.stream(**kw)`` with the scripted responses in order and
records every request (a deep copy of its keyword arguments, so later appends to the message list do not leak into the
record). Every Claude call in the app streams (``llm.call``, max_tokens 128000: the SDK refuses a non-streaming request
that large), so ``create`` raises — a call site left on the old path fails here exactly as it would against the SDK.

A scripted item may be ``raise_on_enter(exc)`` (an HTTP error when the stream opens) or ``raise_on_final(exc)`` (the
stream drops mid-way); the request is still recorded, because it was sent. ``api_error(...)`` makes an exception that
is an API error the way ``llm.is_api_error`` sees one (the SDK's, or its HTTP stack's with ``module="httpx2"``) —
script failures with it; a plain ``RuntimeError``/``AssertionError`` is a defect and propagates.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace


def tool(name, inp, tid="t1"):
    return SimpleNamespace(type="tool_use", id=tid, name=name, input=inp)


def text(t):
    return SimpleNamespace(type="text", text=t)


def thinking(sig="sig", t=""):
    return SimpleNamespace(type="thinking", thinking=t, signature=sig)


def fallback(frm="claude-opus-5-5", to="claude-opus-4-8"):
    return SimpleNamespace(type="fallback", from_=SimpleNamespace(model=frm), to=SimpleNamespace(model=to), trigger=None)


def iteration(kind="message", model=None, inp=100, out=20, cache_read=0, cache_write=0):
    return SimpleNamespace(type=kind, model=model, input_tokens=inp, output_tokens=out,
                           cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)


def resp(*blocks, stop="tool_use", model="claude-opus-5-5", iterations=None, details=None, inp=100, out=20):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, stop_details=details, model=model,
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0, iterations=iterations))


class raise_on_enter:  # noqa: N801 — reads as a scripted event
    def __init__(self, exc):
        self.exc = exc


class raise_on_final:  # noqa: N801
    def __init__(self, exc):
        self.exc = exc


class _Stream:
    def __init__(self, item):
        self.item = item

    def __enter__(self):
        if isinstance(self.item, raise_on_enter):
            raise self.item.exc
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        if isinstance(self.item, raise_on_final):
            raise self.item.exc
        return self.item


class FakeClient:
    """client.beta.messages.stream(**kw) returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create, stream=self._stream))

    def _create(self, **kw):
        raise AssertionError("a non-streaming Claude call: every call must go through llm.call (streaming)")

    def _stream(self, **kw):
        self.requests.append(copy.deepcopy({k: v for k, v in kw.items() if k != "system"}) | {"system": kw.get("system")})
        if not self.responses:
            raise AssertionError("the code asked Claude for more turns than were scripted")
        return _Stream(self.responses.pop(0))


def api_error(message="Overloaded", status=None, request_id=None, module="anthropic._exceptions"):
    """An exception that is an API error the way ``llm.is_api_error`` sees one (its class lives in the SDK's module, or
    its HTTP stack's), with the SDK's ``status_code`` / ``request_id`` when given."""
    cls = type("APIStatusError" if status else "APIConnectionError", (Exception,), {"__module__": module})
    e = cls(message)
    if status:
        e.status_code = status
    if request_id:
        e.request_id = request_id
    return e
