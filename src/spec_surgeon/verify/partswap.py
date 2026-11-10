"""V10 kind ``part_bytes`` — replace_part, verified (Obliterate mode's hands-on tools only; docs/BACKLOG.md O4).

One member of the package replaced with a file of the user's, byte for byte (transform side: ops/replace_part.py).
Nothing here comes from ops/ (CLAUDE.md invariant 2): this module reads the two packages itself and hashes the
member itself.

The assertion says: ``part`` (a member the SOURCE has: a part is replaced, never added), ``sha256`` and ``size`` (of
the bytes that take its place).

The check:

1. the source has the member, and its bytes there are NOT the asserted ones (else the op had to refuse: nothing
   would change);
2. the output has the member, and its bytes — read from the output package and hashed here — are exactly ``size``
   long and hash to ``sha256``.

That is the whole of what the op predicts, and it is exact. The member is then one a kind compared whole
(``ctx.verified_parts`` for a text part, ``ctx.verified_members`` for any other), so the residual leaves it to this
kind; V1 holds every other member to the source's bytes. The suite reads ``verify.xmledit.raw_parts`` for the same
member: its text is not predicted block by block, so V2, V3, the counts of V4 and V5b leave it to this comparison and
the record lists what the new part did to the words instead (V2's entries for it, the text its reader does not read,
and D1).

The verifier never reads the user's file: it is not given it. That the asserted digest is the file's is the plan's
word, pinned when the user chose the file and checked again when the op applies; what this kind proves is that the
copy holds exactly the bytes the approved plan names.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from .props import Ctx


def part_bytes(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    part = a.get("part")
    if not isinstance(part, str) or part not in ctx.src.names:
        raise ValueError(f"the source has no part {part!r}: a part is replaced, never added")
    want, size = a.get("sha256"), a.get("size")
    if not isinstance(want, str) or len(want) != 64 or type(size) is not int or size < 0:
        raise ValueError("the assertion states no SHA-256 and size of the replacing bytes")
    # compared whole from here on, whatever is found
    (ctx.verified_parts if part in ctx.both else ctx.verified_members).add(part)
    what = f"part_bytes {part} ({size:,} bytes)"
    if hashlib.sha256(ctx.src.read(part)).hexdigest() == want.lower():
        errs.append(f"part_bytes: the source's {part} is already those bytes: the op had to refuse")
        return what
    if part not in ctx.out.names:
        errs.append(f"part_bytes: {part} is missing from the output")
        return what
    got = ctx.out.read(part)
    if len(got) != size or hashlib.sha256(got).hexdigest() != want.lower():
        errs.append(f"part_bytes: the output's {part} is not the bytes the plan names ({len(got):,} bytes, "
                    f"SHA-256 {hashlib.sha256(got).hexdigest()[:12]}…; the plan states {size:,} bytes, {want[:12]}…)")
    return what


PART_BYTES_KINDS = {"part_bytes": part_bytes}

PART_BYTES_CONTRACT = (
    "part_bytes — one member replaced with a file of the user's, byte for byte (the replace_part operation, "
    "Obliterate mode's hands-on tools only; never a custom method's). Fields: part (a member the source has); sha256 "
    "and size (of the bytes that take its place). Compared: the output member's own bytes, hashed by the verifier, "
    "with sha256 and size; the source's member must be other bytes (else the operation had to refuse). The part's "
    "text is not predicted block by block: V2, V3, V4's counts and V5b leave the part to this comparison, and the "
    "record lists what the new part did to the words (V2's entries for the part, the text its reader does not read "
    "under raw_text, and D1).")
PART_BYTES_EXAMPLE = {"kind": "part_bytes", "part": "word/document.xml", "sha256": "<the file's SHA-256>",
                      "size": 48211}
