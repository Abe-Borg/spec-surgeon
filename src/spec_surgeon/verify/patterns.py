"""The verifier's own bounded patterns — its twin of the transform side's, written on its own (CLAUDE.md
invariant 2: nothing here is imported from ``ops/``).

A plan carries patterns the user or the model wrote (a selector's ``text_regex``, a fill's ``pattern``, a table's
``first_row_regex``, a comment filter, a picture's ``name_regex`` …), and the verifier re-reads the source with them.
Python's ``re`` cannot be interrupted, so the verifier searches through the ``regex`` module in its default ``V0``
behaviour (``re``'s syntax and matching) with a deadline on every search: ``V_REGEX_TIMEOUT_S`` seconds on one text.
A search that runs past it, a pattern longer than ``V_MAX_PATTERN_CHARS``, a pattern that is not a string or one
that does not compile is a ``ValueError`` — the verifier's own way of saying "the op must refuse" — which every
V10 kind reports as a failed check with that reason (``props.check_properties`` turns a kind's ``ValueError`` into
an error line, and the suite turns anything else into a failed V10); nothing escapes the suite.

Flags come in ``re``'s numbering (the verifier writes ``re.I``) and are mapped name by name onto ``regex``'s, whose
numbers differ for some flags (``re.ASCII`` shares its value with ``regex.V1``): a number is never handed across.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, List, Optional

import regex

V_REGEX_TIMEOUT_S = 2.0
V_MAX_PATTERN_CHARS = 1000

_V_FLAG_NAMES = ("IGNORECASE", "MULTILINE", "DOTALL", "VERBOSE", "ASCII", "UNICODE", "LOCALE")


def _v_module_flags(flags: int) -> int:
    """``re``'s bits, by name, as ``regex``'s; a bit without a name is refused."""
    wanted, out = int(flags), 0
    for name in _V_FLAG_NAMES:
        bit = int(getattr(re, name))
        if wanted & bit:
            out |= int(getattr(regex, name))
            wanted ^= bit
    if wanted:
        raise ValueError(f"unsupported pattern flag bits {wanted:#x}")
    return out


def _v_re_flags(flags: int) -> int:
    """``regex``'s bits, by name, as ``re``'s; ``regex``'s own (its version, full case folding) do not come across."""
    return sum(int(getattr(re, name)) for name in _V_FLAG_NAMES if int(flags) & int(getattr(regex, name)))


class VPattern:
    """A compiled plan pattern; each search ends at the deadline with a ``ValueError`` naming the pattern."""

    def __init__(self, pattern: str, flags: int = 0, *, where: str = "the text",
                 max_chars: Optional[int] = V_MAX_PATTERN_CHARS):
        if not isinstance(pattern, str):
            raise ValueError(f"a pattern must be text, not {type(pattern).__name__} — the op must refuse")
        if max_chars is not None and len(pattern) > max_chars:
            raise ValueError(f"the pattern is {len(pattern)} characters long, over {max_chars} — the op must refuse")
        try:
            self._compiled = regex.compile(pattern, _v_module_flags(flags))
        except (regex.error, RecursionError, OverflowError) as e:
            raise ValueError(f"the pattern {pattern!r} does not compile ({e}) — the op must refuse") from None
        self.pattern = pattern
        self.where = where

    def __repr__(self) -> str:
        return f"VPattern({self.pattern!r})"

    @property
    def flags(self) -> int:
        return _v_re_flags(self._compiled.flags)

    @property
    def groups(self) -> int:
        return self._compiled.groups

    @property
    def groupindex(self):
        return self._compiled.groupindex

    def _past_deadline(self) -> ValueError:
        return ValueError(f"the pattern {self.pattern!r} ran past {V_REGEX_TIMEOUT_S:g} s on {self.where} — "
                          "the op must refuse (regex-timeout)")

    def _bounded(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self._compiled, name)(*args, timeout=V_REGEX_TIMEOUT_S)
        except TimeoutError:
            raise self._past_deadline() from None

    def search(self, text: str):
        return self._bounded("search", text)

    def match(self, text: str):
        return self._bounded("match", text)

    def fullmatch(self, text: str):
        return self._bounded("fullmatch", text)

    def findall(self, text: str) -> List[Any]:
        return self._bounded("findall", text)

    def finditer(self, text: str) -> Iterator[Any]:
        scanner = self._compiled.finditer(text, timeout=V_REGEX_TIMEOUT_S)
        while True:
            try:
                m = next(scanner)
            except StopIteration:
                return
            except TimeoutError:
                raise self._past_deadline() from None
            yield m

    def sub(self, repl: Any, text: str, count: int = 0) -> str:
        return self._bounded("sub", repl, text, count)

    def split(self, text: str, maxsplit: int = 0) -> List[Any]:
        return self._bounded("split", text, maxsplit)


def v_compile(pattern: Any, flags: int = 0, *, where: str = "the text",
              max_chars: Optional[int] = V_MAX_PATTERN_CHARS) -> VPattern:
    """A plan's pattern, bounded; ``max_chars`` None for a pattern the verifier builds from the user's own literal
    (an escaped find), whose length is the words'."""
    return VPattern(pattern, flags, where=where, max_chars=max_chars)


def v_search(pattern: Any, text: str, flags: int = 0, *, where: str = "the text") -> Optional[Any]:
    """``re.search(pattern, text)`` with the deadline (``regex`` keeps its own cache of compiled patterns)."""
    return VPattern(pattern, flags, where=where).search(text)
