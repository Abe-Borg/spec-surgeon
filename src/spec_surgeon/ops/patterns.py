"""Bounded regular expressions, transform side: every pattern a request or the model writes is compiled here and
searched with a deadline.

Python's ``re`` cannot be interrupted, planning has no deadline and the sandbox's timeout covers custom methods only
(stress-test finding F8): a catastrophic pattern — ``(a|aa)+$`` on thirty letters and a stop — would hold the engine
for minutes on one paragraph. The owner's decision (2026-10-28): the ``regex`` module, whose default ``V0`` behaviour
is ``re``'s (the same syntax, flags, match objects and zero-width rules; a pattern that matches differently under
the two is not expected — ``regex`` only accepts a little more, such as a variable-width lookbehind or ``\\p{L}``,
and the verifier's twin accepts the same), and whose ``search``, ``match``, ``fullmatch``, ``finditer``, ``findall``, ``sub``,
``subn`` and ``split`` take ``timeout=`` seconds. A search past ``REGEX_TIMEOUT_S`` is a refusal with the code
``regex-timeout`` that sets the file aside; a pattern that is not a string, is longer than ``MAX_PATTERN_CHARS`` or
does not compile is ``bad-params``. An exact-text pattern (``exact_text_only``: ``\\A``, literal text or alternatives of
literal text, ``\\Z``) has no length limit, only the deadline: it is how the app selects paragraphs by their own whole
text (an approved outcome's bound paragraphs, ``goal_contracts._selection``), and a long paragraph's text is long.

``compile_pattern`` is the one way a user- or model-supplied pattern is compiled under ``ops/`` (a selector's
``text_regex``, ``after_heading_regex`` and ``between_paragraphs``, a table's ``first_row_regex``, ``text_regex`` and
``header_regex``, a fill's ``pattern`` and ``anchor_regex``, a picture's ``name_regex``, a comment or note filter, a
tag or option rule, an end-line or place pattern, a find with ``regex`` true); the app's own constant patterns and
the ones it builds from escaped literals stay on ``re``. The verifier has its own twin, ``verify/patterns.py``,
written independently (CLAUDE.md invariant 2).

Flags are ``re``'s (callers write ``re.IGNORECASE``) and are translated bit by bit: ``regex`` numbers some of its
flags differently (``re.ASCII``'s value is ``regex.V1``'s; ``re.DEBUG``'s is ``regex.ASCII``'s), so a flag is never
passed through as a number, and ``BoundedPattern.flags`` reads back in ``re``'s numbering.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Iterator, List, Optional, Tuple, Union

import regex

from .base import JobException

REGEX_TIMEOUT_S = 2.0           # seconds one search may take on one text (the trust surfaces quote it)
MAX_PATTERN_CHARS = 1000        # a longer pattern is refused before it is compiled

# (re's flag, regex's flag): the only flags a pattern may carry, translated one by one in both directions.
_FLAGS: Tuple[Tuple[int, int], ...] = (
    (int(re.IGNORECASE), int(regex.IGNORECASE)), (int(re.MULTILINE), int(regex.MULTILINE)),
    (int(re.DOTALL), int(regex.DOTALL)), (int(re.VERBOSE), int(regex.VERBOSE)), (int(re.ASCII), int(regex.ASCII)),
    (int(re.UNICODE), int(regex.UNICODE)), (int(re.LOCALE), int(regex.LOCALE)),
)


def _to_regex_flags(flags: int) -> int:
    """``re``'s flag bits as ``regex``'s; an unknown bit is a programming error, never passed through."""
    rest, out = int(flags), 0
    for ours, theirs in _FLAGS:
        if rest & ours:
            out |= theirs
            rest &= ~ours
    if rest:
        raise ValueError(f"unknown regex flag bits {rest:#x}")
    return out


def _to_re_flags(flags: int) -> int:
    """``regex``'s flag bits as ``re``'s; the bits only ``regex`` has (its version, full case folding) are dropped."""
    out = 0
    for ours, theirs in _FLAGS:
        if int(flags) & theirs:
            out |= ours
    return out


class BoundedPattern:
    """A compiled pattern whose every search stops at ``REGEX_TIMEOUT_S``: the ``regex`` object's methods, each
    with the deadline, a timeout translated into the ``regex-timeout`` refusal."""

    __slots__ = ("_rx", "pattern", "where")

    def __init__(self, compiled: Any, pattern: str, where: str):
        self._rx = compiled
        self.pattern = pattern
        self.where = where

    def __repr__(self) -> str:
        return f"BoundedPattern({self.pattern!r})"

    @property
    def flags(self) -> int:
        return _to_re_flags(self._rx.flags)

    @property
    def groups(self) -> int:
        return self._rx.groups

    @property
    def groupindex(self):
        return self._rx.groupindex

    def _timeout(self) -> JobException:
        return JobException(f"the pattern {self.pattern!r} took too long on {self.where}; simplify it", "regex-timeout",
                            {"pattern": self.pattern, "where": self.where, "timeout_s": REGEX_TIMEOUT_S})

    def _run(self, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return getattr(self._rx, method)(*args, timeout=REGEX_TIMEOUT_S, **kwargs)
        except TimeoutError:
            raise self._timeout() from None

    def search(self, string: str, pos: int = 0, endpos: Optional[int] = None):
        return self._run("search", string, pos, endpos if endpos is not None else len(string))

    def match(self, string: str, pos: int = 0, endpos: Optional[int] = None):
        return self._run("match", string, pos, endpos if endpos is not None else len(string))

    def fullmatch(self, string: str, pos: int = 0, endpos: Optional[int] = None):
        return self._run("fullmatch", string, pos, endpos if endpos is not None else len(string))

    def findall(self, string: str, pos: int = 0, endpos: Optional[int] = None) -> List[Any]:
        return self._run("findall", string, pos, endpos if endpos is not None else len(string))

    def finditer(self, string: str, pos: int = 0, endpos: Optional[int] = None) -> Iterator[Any]:
        # regex's scanner raises on the step that runs out of time, so the deadline is checked on every step here
        try:
            for m in self._rx.finditer(string, pos, endpos if endpos is not None else len(string),
                                       timeout=REGEX_TIMEOUT_S):
                yield m
        except TimeoutError:
            raise self._timeout() from None

    def sub(self, repl: Union[str, Callable[[Any], str]], string: str, count: int = 0) -> str:
        return self._run("sub", repl, string, count)

    def subn(self, repl: Union[str, Callable[[Any], str]], string: str, count: int = 0) -> Tuple[str, int]:
        return self._run("subn", repl, string, count)

    def split(self, string: str, maxsplit: int = 0) -> List[Any]:
        return self._run("split", string, maxsplit)


_SPECIAL = frozenset(".^$*+?{}[]|()\\")


def exact_text_only(pattern: str) -> bool:
    """``pattern`` is ``\\A``, then literal text — or alternatives of literal text in one ``(?:…)`` group — then
    ``\\Z``: every character either not special or escaped (a backslash before a character that is not an ASCII
    letter or digit, as ``re.escape`` writes it), so it holds no repetition, class, group, reference or escape
    sequence and matching it is linear in its length. The app builds these from a paragraph's own words
    (``r"\\A" + re.escape(text) + r"\\Z"``); a model may write one too, and it costs no more."""
    tokens, i = [], 0
    while i < len(pattern):
        step = 2 if pattern[i] == "\\" and i + 1 < len(pattern) else 1
        tokens.append(pattern[i:i + step])
        i += step
    if len(tokens) < 2 or tokens[0] != "\\A" or tokens[-1] != "\\Z":
        return False
    tokens = tokens[1:-1]
    if tokens[:3] == ["(", "?", ":"] and tokens[-1:] == [")"]:
        tokens = [t if t != "|" else "" for t in tokens[3:-1]]        # the group's alternatives
    for t in tokens:
        if len(t) == 2 and not (t[1].isascii() and t[1].isalnum()):
            continue                                                  # an escaped character
        if t == "" or (len(t) == 1 and t not in _SPECIAL):
            continue                                                  # a plain character, or a "|" of the group
        return False
    return True


def compile_pattern(pattern: Any, flags: int = 0, *, where: str, label: Optional[str] = None,
                    max_chars: Optional[int] = MAX_PATTERN_CHARS) -> BoundedPattern:
    """``pattern`` as a ``BoundedPattern``, every search on ``where`` (words: "a paragraph's text") bounded by
    ``REGEX_TIMEOUT_S``. Not a string, over ``max_chars`` characters (``None``: a pattern the app built from an
    escaped literal, whose length is the user's words; never for an exact-text pattern, ``exact_text_only``) or not
    compiling: ``JobException`` with ``bad-params``,
    its reason opening with ``label`` (the parameter's name: "rows.text_regex") when one is given — so no caller
    re-raises with a code of its own (``tests/test_diagnostics.py`` reads every code as a literal)."""
    head = f"{label}: " if label else ""
    if not isinstance(pattern, str):
        raise JobException(f"{head}a pattern must be text (a Python regular expression), got "
                           f"{type(pattern).__name__}", "bad-params")
    if max_chars is not None and len(pattern) > max_chars and not exact_text_only(pattern):
        raise JobException(f"{head}the pattern is {len(pattern)} characters long; at most {max_chars} are accepted",
                           "bad-params", {"length": len(pattern), "max": max_chars})
    try:
        compiled = regex.compile(pattern, _to_regex_flags(flags))
    except (regex.error, RecursionError, OverflowError) as e:
        raise JobException(f"{head}the pattern {pattern!r} is not a regular expression ({e})",
                           "bad-params") from None
    return BoundedPattern(compiled, pattern, where)
