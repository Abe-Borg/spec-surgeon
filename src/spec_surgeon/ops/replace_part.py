"""Op: replace_part — one part of a document replaced with a file of the user's, byte for byte. Obliterate mode's
hands-on tools only.

The owner's decision of 2026-11-10 (CLAUDE.md, "Standing decisions"; docs/BACKLOG.md, O4): the cheaper hands-on
editor. The window shows a document's parts read-only and saves one out as it is; the user changes that file in an
editor of their own and puts it back with this op. Nothing in the window edits XML.

It is the user's own tool. No model is told of it (``Op.hands_on``: ``catalog.offered_ops`` never lists it), no plan
a model submits may name it, and the gate takes it only while the hands-on tools are on (``compiler._mode_errors``):
the window builds its one-op plan in code from the document, the part and the file the user chose
(``DocumentTask._replace_part``), and that plan is previewed, approved and checked like any other.

It predicts first (invariant 1), and what it predicts is exact: after it, the member ``part`` holds the bytes of
``file`` — pinned by their SHA-256 when the user chose the file, so a file that changed since is refused — and no
other member changes. The verifier's ``part_bytes`` (``verify/partswap.py``) hashes the output member itself. What
the new bytes do to the words is not predicted, as for a raw XML edit: the part is one a kind compares whole
(``verify.xmledit.raw_parts``), so the suite leaves its text to that comparison, lists the words and the other text
that changed, and adds D1 "What changed"; the user reads those lists before approving.

Any member the document has can be replaced, its relationship and content-type parts included: the bytes are placed
as they are, so there is nothing the app's XML writer could lose. Whether the result is a file Word opens is the
suite's to say (the package, revision ids, the schema), and in Obliterate mode a copy that fails is delivered marked
UNVERIFIED.

Refusals: ``part-missing`` (the document has no such member: this op replaces, it adds none), ``part-file`` (the file
cannot be read, is larger than ``MAX_PART_BYTES``, or is not the file that was chosen any more), ``part-same`` (the
file is the part as it is: nothing would change); a malformed parameter is ``bad-params``.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict

from . import prose, register
from .base import JobException, Op, PlanFragment

MAX_PART_BYTES = 64 * 1024 * 1024        # of the file that replaces a part (the trust surfaces quote it)


def read_replacement(file: Any) -> bytes:
    """The bytes of the file that replaces a part, or ``part-file``: not a path, unreadable, or too large."""
    if not isinstance(file, str) or not file.strip():
        raise JobException("file must be the path of the file that replaces the part", "bad-params")
    path = Path(file)
    try:
        size = path.stat().st_size
        if size > MAX_PART_BYTES:
            raise JobException(f"{path.name} is {size:,} bytes; a part is replaced with a file of at most "
                               f"{MAX_PART_BYTES:,}", "part-file",
                               {"file": str(path), "why": "too-big", "size": size, "limit": MAX_PART_BYTES})
        return path.read_bytes()
    except OSError as error:
        raise JobException(f"{path.name} cannot be read ({error})", "part-file",
                           {"file": str(path), "why": "unreadable"}) from None


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@register
class ReplacePart(Op):
    name = "replace_part"
    mode = "untracked"
    obliterate_only = True
    hands_on = True
    description = (
        "OBLITERATE MODE'S HANDS-ON TOOLS ONLY; built by the window from the user's own choice, never planned by a "
        "model. The member `part` of the document is replaced with the bytes of `file`, a file the user chose, exactly "
        "as they are; `sha256` pins the file as it was when chosen. The verifier hashes the output member itself; "
        "what the new part does to the words is listed for the user from the copy, not predicted. Never tracked.")
    params_schema = {"type": "object", "required": ["part", "file"], "properties": {
        "part": {"type": "string", "description": "the member to replace, by its name in the package "
                                                  "(word/document.xml, word/media/image1.png, …); it must exist"},
        "file": {"type": "string", "description": "the path of the file whose bytes take the part's place"},
        "sha256": {"type": "string", "description": "the SHA-256 of the file as the user chose it; a file that is no "
                                                    "longer those bytes is refused"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"},
    }, "additionalProperties": False}
    select_schema = {}
    verification_profile = {
        "V1": "the one member, nothing else; no new dangling relationship, reference or untyped member",
        "V10": "part_bytes: the output member's own SHA-256 and size are the plan's (the file's), and the source's "
               "member was other bytes",
        "V2": "the part's words are not predicted: they are listed from the copy for the user",
        "V4": "revision ids unique and readable; the part's count of tracked changes is listed, not predicted",
        "V5": "no new schema error, no revision markup outside Word's grammar",
        "V7": "none — a replaced part can move or change what the pages show",
    }

    @classmethod
    def describe(cls, select: Dict[str, Any], params: Dict[str, Any]) -> str:
        part = prose.typed(str(params.get("part") or "the part"))
        name = prose.typed(Path(str(params.get("file") or "the file")).name)
        return (f"Replace the part {part} with the file {name}, byte for byte. Not tracked; no other part of the "
                f"document changes.")

    def plan(self, pkg, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise JobException("replace_part takes no select: the part says where", "bad-params")
        if params.get("tracked") not in (None, False):
            raise JobException("a replaced part is never tracked", "bad-params")
        part = params.get("part")
        if not isinstance(part, str) or not part.strip():
            raise JobException("part must be the member to replace, by its name in the package", "bad-params")
        part = part.strip()
        pinned = params.get("sha256")
        if pinned is not None and (not isinstance(pinned, str) or len(pinned) != 64):
            raise JobException("sha256 must be the file's SHA-256, as 64 hexadecimal digits", "bad-params")
        data = read_replacement(params.get("file"))
        name = Path(params["file"]).name
        if not pkg.has(part):
            raise JobException(f"this document has no part named {part}; a part is replaced, never added",
                               "part-missing", {"part": part, "parts": list(pkg.member_names)[:60]})
        found = digest(data)
        if pinned is not None and found != pinned.lower():
            raise JobException(f"{name} is not the file that was chosen any more (its content changed); choose it "
                               f"again", "part-file", {"file": params["file"], "why": "changed"})
        if pkg.read(part) == data:
            raise JobException(f"{name} is the part {part} as it is: nothing would change", "part-same",
                               {"part": part, "file": params["file"]})
        frag = self.fragment({}, params, mode="untracked")
        frag.intended_parts = [part]
        frag.untracked_diffs_expected = True
        frag.touches.append((part, "*", "bytes"))                  # the part as a whole: nothing else in its step
        frag.geometry_reference = "none"
        frag.geometry_reason = "a replaced part can move or change what the pages show"
        frag.property_assertions.append({"kind": "part_bytes", "part": part, "sha256": found, "size": len(data)})
        frag.notes.append(f"{part} replaced with {name} ({len(data):,} bytes); the words it adds and removes are "
                          f"listed from the copy, not predicted")
        frag.transform_data = {"part": part, "file": params["file"], "sha256": found}
        return frag

    def apply(self, pkg, frag: PlanFragment) -> None:
        td = frag.transform_data
        data = read_replacement(td["file"])
        if digest(data) != td["sha256"]:
            raise JobException(f"{Path(td['file']).name} changed since the plan was made; choose it again",
                               "part-file", {"file": td["file"], "why": "changed"})
        pkg.write(td["part"], data)
