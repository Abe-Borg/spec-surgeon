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
its own ``advisor._client``), which is what the tests replace with a scripted fake.
"""
from __future__ import annotations

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


def client():
    """The real Anthropic client (needs the ``anthropic`` package and a key: ``ANTHROPIC_API_KEY`` or ``ant auth login``)."""
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise RuntimeError('install the Claude layer (pip install "spec-surgeon[agent]") and set an API key to use '
                           'typed requests, AI-written transforms, advice and the visual check') from e
    return anthropic.Anthropic()


def call(client, *, model: str, system: str, tools: Sequence[Dict[str, Any]], messages: List[Dict[str, Any]]):
    """One streamed request; returns the final message. Raises whatever the SDK raises (callers decide what a failed
    call means for their history — never half-append a turn)."""
    kw = dict(model=model, max_tokens=MAX_TOKENS, system=system, tools=list(tools), messages=messages,
              output_config={"effort": EFFORT}, cache_control={"type": "ephemeral"},
              betas=[FALLBACK_BETA], fallbacks="default")
    with client.beta.messages.stream(**kw) as stream:
        return stream.get_final_message()


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
