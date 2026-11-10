"""Obliterate's rescue at attach (the owner's decision of 2026-11-10; docs/BACKLOG.md O6).

A file the attach-time check refuses (``triage.triage_file``) is, in Safe and Default, refused and nothing more. In
Obliterate mode a refused document offers **Try to rescue**: the app tries the one thing that could make it a file
the engine takes, and what comes out is attached as a NEW document — the user's file is read, never written.

What can be tried, by the refusal (``kind_of``):

* ``doctype`` — a part carries an XML document type declaration: the app writes the part again without it. A part
  that uses an entity the declaration defined is not rescued: that text exists nowhere but in the declaration.
* ``no-paragraph`` — the body has no paragraph: the app adds an empty one.
* ``format`` — a ``.doc`` or ``.docm`` file; ``strict`` — a Strict Open XML document: Word opens a private copy and
  saves it as a ``.docx`` (the worker's ``convert`` action; a ``.docm``'s macros are not kept).
* ``password`` — a document encrypted with a password: Word opens a private copy with the password the user types
  for this one attempt and saves it WITHOUT one. The password goes to Word and nowhere else: it is never stored,
  logged, journalled or sent to a model.
* ``unopenable`` — a file that is not a readable Word package: Word opens a private copy with Open and Repair and
  saves what it could read.
* ``too-large`` — a file past the archive limits (``package_limits``): nothing is written; the limits are raised for
  this window, to twice what this file measures and never past ``CEILING``, and the user's own file is attached.

A lock file, a folder, a missing file and any other kind of file are still refused: there is nothing to rescue.

**What a rescue proves: nothing about content.** The file could not be read as it was, so nothing can compare what
came out with what went in. A rescued document therefore carries a permanent mark, SALVAGED, on itself and on every
copy made from it; from then on it is a document like any other, and its copies are verified against the salvaged
file — which is the only baseline there is. (Raising the size limit changes no file and leaves no mark.)

The one seam to Word is ``word_actions.run_word_actions``; tests replace it. Nothing here calls a model.
"""
from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from lxml import etree

from . import diagnostics as diag
from . import word_actions
from .package import DocxPackage, W, serialize_xml
from .package_limits import ArchiveLimits

FORMATS = (".doc", ".docm")              # what Word converts; attachable in Obliterate mode alone, to be rescued
APP_KINDS = ("doctype", "no-paragraph", "too-large")       # the app's own, with no Word
WORD_KINDS = ("format", "strict", "password", "unopenable")
KINDS = APP_KINDS + WORD_KINDS
MAX_STEPS = 3                            # a rescued file that is still refused gets the next fitting step, this many
MARK = "SALVAGED"
# The archive limits a rescue may raise this window's to, and never past: a file beyond them is still refused.
CEILING_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024           # the file itself (the trust copy quotes it)
CEILING = ArchiveLimits(max_members=100_000, max_member_bytes=1024 * 1024 * 1024,
                        max_total_bytes=4 * 1024 * 1024 * 1024, max_archive_bytes=CEILING_ARCHIVE_BYTES)
HEADROOM = 2                             # the limits are raised to this many times what the file measures
# What Word is given to open a file that may or may not have a password, so it never stops to ask: Word ignores a
# password a file does not need, and answers a wrong one with an error of its own.
NO_PASSWORD = "spec-surgeon: no password was given"
WRONG_PASSWORD_SCODE = "0x800A1520"      # Word's "The password is incorrect" (5408), as the worker reports it
_CFB = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"      # an OLE compound file: an old .doc, or an encrypted Office document
_ENCRYPTED = "EncryptedPackage".encode("utf-16-le")

LABEL = "Try to rescue"
SAYS = {
    "doctype": "The app writes the part again without its document type declaration.",
    "no-paragraph": "The app adds the empty paragraph a document's body must have.",
    "format": "Word opens a private copy and saves it as a Word document (.docx).",
    "strict": "Word opens a private copy and saves it as an ordinary Word document (.docx).",
    "password": "Word opens a private copy with the password you type, and saves it without one.",
    "unopenable": "Word opens a private copy with its Open and Repair command and saves what it could read.",
    "too-large": "The app raises its size limits for this window so the file can be opened as it is.",
}
UNPROVEN = ("Nothing proves the rescue kept everything your file held: the file could not be read as it was, so there "
            "is nothing to compare the result with. Open both in Word and compare them before you rely on it.")
NO_PASSWORD_NOW = ("The rescued document has NO password: it sits in this window's scratch folder until the window "
                   "closes, and every copy made from it is unprotected too. Keep them as carefully as the original.")
NO_MACROS = "A .docx holds no macros: the ones in your file are not in the rescued document."


class RescueFailed(ValueError):
    """The rescue made no document; the message says why, in words the window can show."""


class NeedsPassword(RescueFailed):
    """Word needs the document's password (none was given, or the one given was wrong): the window asks for it."""

    def __init__(self, message: str, wrong: bool = False):
        super().__init__(message)
        self.wrong = wrong


@dataclass
class Rescued:
    kind: str                            # the first step's kind: what the file was refused for
    path: Optional[Path] = None          # the rescued .docx; None when nothing was written (the limits were raised)
    steps: List[str] = field(default_factory=list)      # what was done, one sentence each, in order
    limits: Dict[str, int] = field(default_factory=dict)        # the archive limits raised for this window
    notes: List[str] = field(default_factory=list)      # what the user must know of the result (no password, no macros)
    holds: str = ""                      # what the rescued document holds, in a sentence


def encrypted(path: Path) -> bool:
    """The file is an Office document encrypted with a password: an OLE compound file that holds an
    ``EncryptedPackage`` stream. Read from its first megabytes; a guess that only decides whether the password is
    asked for before Word is tried — Word's own answer is the fact."""
    try:
        with Path(path).open("rb") as stream:
            head = stream.read(8 * 1024 * 1024)
    except OSError:
        return False
    return head.startswith(_CFB) and _ENCRYPTED in head


def kind_of(path: Path, code: str) -> str:
    """What could be tried for a file refused with ``code`` (``triage.Triage.code``); '' when nothing can."""
    path = Path(path)
    if code == "not-docx":
        return "format" if path.suffix.lower() in FORMATS else ""
    if code == "unopenable":
        return "password" if encrypted(path) else "unopenable"
    return code if code in KINDS else ""


def needs_word(kind: str) -> bool:
    return kind in WORD_KINDS


# ---- the app's own rescues -------------------------------------------------------------------------------------------

def _without_doctype(data: bytes) -> Dict[str, Any]:
    """``{"data", "parts"}``: the package with every document type declaration taken out of its XML parts, and the
    parts that had one. A part that uses an entity cannot be written without the declaration that defines it."""
    marks = tuple("<!DOCTYPE".encode(e) for e in ("ascii", "utf-16-le", "utf-16-be"))
    pkg = DocxPackage(data)
    try:
        changed: List[str] = []
        for name in pkg.member_names:
            raw = pkg.read(name)
            if not any(m in raw for m in marks):
                continue
            parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True, load_dtd=False,
                                     no_network=True)
            try:
                root = etree.fromstring(raw, parser)
            except etree.XMLSyntaxError:
                continue                         # not XML at all: its bytes only happen to hold those characters
            info = root.getroottree().docinfo
            if info.internalDTD is None and info.externalDTD is None and not info.doctype:
                continue
            used = sum(1 for el in root.iter() if isinstance(el, etree._Entity))
            if used:
                raise RescueFailed(f"{name} uses {used:,} entit{'y' if used == 1 else 'ies'} that its document type "
                                   f"declaration defines, so its text exists nowhere but in that declaration; the "
                                   f"part cannot be written without it. Get the file again from where it came from.")
            pkg.write(name, serialize_xml(root))
            changed.append(name)
        if not changed:
            raise RescueFailed("No part of the file carries a document type declaration the app could take out.")
        return {"data": pkg.output_bytes(), "parts": changed}
    finally:
        pkg.close()


def _with_paragraph(data: bytes) -> bytes:
    """The package with one empty paragraph added to a body that has none, before its section properties."""
    pkg = DocxPackage(data)
    try:
        main = pkg.main_part()
        root = pkg.xml(main)
        body = root.find(f"{W}body")
        if body is None:
            raise RescueFailed("The main part has no body to add a paragraph to.")
        section = body.find(f"{W}sectPr")
        paragraph = etree.Element(f"{W}p")
        if section is not None:
            section.addprevious(paragraph)
        else:
            body.append(paragraph)
        pkg.write_xml(main, root)
        return pkg.output_bytes()
    finally:
        pkg.close()


def measure(path: Path) -> Dict[str, int]:
    """What the archive limits measure of a file, read from the zip's own directory (nothing is inflated): its
    bytes, its members, its largest member and all of them together, as declared. ``RescueFailed`` when it is not a
    zip at all."""
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
    except (zipfile.BadZipFile, OSError) as error:
        raise RescueFailed(f"{path.name} is not a readable zip archive ({error}); raising the size limits cannot "
                           f"help it.") from None
    return {"max_archive_bytes": path.stat().st_size, "max_members": len(infos),
            "max_member_bytes": max((i.file_size for i in infos), default=0),
            "max_total_bytes": sum(i.file_size for i in infos)}


def raised_limits(path: Path) -> Dict[str, int]:
    """The archive limits this window needs to open ``path``: for each one the file is past, ``HEADROOM`` times what
    the file measures (a copy of it grows a little), never past ``CEILING``. ``RescueFailed`` for a file past the
    ceiling."""
    measured = measure(path)
    now = ArchiveLimits.from_environment()
    raised: Dict[str, int] = {}
    for name, value in measured.items():
        ceiling = getattr(CEILING, name)
        if value > ceiling:
            raise RescueFailed(f"{Path(path).name} is past what a rescue will open ({name.replace('max_', '')}: "
                               f"{value:,}; at most {ceiling:,}). Split the document in Word.")
        if value > getattr(now, name):
            raised[name] = min(ceiling, value * HEADROOM)
    if not raised:
        raise RescueFailed(f"{Path(path).name} is within the size limits; something else stops it.")
    return raised


def apply_limits(limits: Dict[str, int]) -> None:
    """Raise this process's archive limits (and its worker processes', which inherit the environment): each named
    limit to the value given, when that is more than it is now. Never lowers one."""
    now = ArchiveLimits.from_environment()
    for name, value in (limits or {}).items():
        if name in ArchiveLimits.__dataclass_fields__ and type(value) is int and value > getattr(now, name):
            os.environ["SPEC_SURGEON_ARCHIVE_" + name.upper()] = str(min(value, getattr(CEILING, name)))


# ---- Word's ----------------------------------------------------------------------------------------------------------

def wrong_password(error: BaseException) -> bool:
    """Word refused to open the file for its password (none it could use was given)."""
    said = " ".join(str(x) for x in (getattr(error, "reason", ""), error, getattr(error, "details", "")))
    return WRONG_PASSWORD_SCODE.lower() in said.lower() or "password" in said.lower()


def _by_word(source: Path, out: Path, kind: str, password: Optional[str]) -> List[Dict[str, Any]]:
    if not word_actions.available():
        raise RescueFailed(word_actions.NO_WORD)
    action = "repair" if kind == "unopenable" else "convert"
    try:
        # Word is always given a password, so it never stops to ask for one behind a hidden window
        return word_actions.run_word_actions(source, [action], out, password=password or NO_PASSWORD)
    except Exception as error:  # noqa: BLE001 — Word's failure is the rescue's reason, never the job's
        if wrong_password(error):
            raise NeedsPassword(f"Word needs the password of {Path(source).name} to open it." if not password else
                                f"Word says that is not the password of {Path(source).name}.",
                                wrong=bool(password)) from None
        raise RescueFailed(word_actions.failure(error, [action])) from None


# ---- one file, rescued -----------------------------------------------------------------------------------------------

def _holds(path: Path) -> str:
    """What a rescued document holds, counted by the app from the file itself: a sentence for the user to hold
    against the original."""
    pkg = DocxPackage.from_path(path)
    try:
        body = pkg.xml(pkg.main_part())
        paragraphs = sum(1 for _ in body.iter(f"{W}p"))
        tables = sum(1 for _ in body.iter(f"{W}tbl"))
        pictures = sum(1 for _ in body.iter(f"{W}drawing")) + sum(1 for _ in body.iter(f"{W}pict"))
        kinds = pkg.parts_by_kind()
        return (f"It holds {paragraphs:,} paragraph{'' if paragraphs == 1 else 's'}, {tables:,} "
                f"table{'' if tables == 1 else 's'} and {pictures:,} picture{'' if pictures == 1 else 's'} or drawn "
                f"object{'' if pictures == 1 else 's'} in its body, with {len(kinds['headers']):,} header "
                f"part{'' if len(kinds['headers']) == 1 else 's'} and {len(kinds['footers']):,} footer "
                f"part{'' if len(kinds['footers']) == 1 else 's'}.")
    finally:
        pkg.close()


def rescue_file(path: Path, kind: str, workdir: Path, password: Optional[str] = None) -> Rescued:
    """Try to make ``path`` (refused for ``kind``) a document the engine takes. The rescued ``.docx`` is written
    under ``workdir`` (never beside or over ``path``), then checked as any attached file is; while it is still
    refused for something the app itself can mend, that is mended too (``MAX_STEPS`` in all). ``RescueFailed`` when
    no document comes of it, ``NeedsPassword`` when Word wants one. ``too-large`` writes nothing: it raises this
    window's limits (``Rescued.limits``) and the file is attached as it is."""
    from .triage import triage_file
    path, workdir = Path(path), Path(workdir)
    if kind not in KINDS:
        raise RescueFailed(f"There is nothing to try for {path.name}.")
    name = path.stem + ".docx"
    done = Rescued(kind)
    with diag.span("rescue.file", source=str(path), kind=kind) as outcome:
        current = path
        for step in range(1, MAX_STEPS + 1):
            if kind == "too-large":
                done.limits = raised_limits(current)
                apply_limits(done.limits)
                done.steps.append("The app raised its size limits for this window to open the file: "
                                  + ", ".join(f"{k.replace('max_', '').replace('_', ' ')} to {v:,}"
                                              for k, v in done.limits.items()) + ".")
            else:
                target = workdir / f"step-{step}" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if kind == "doctype":
                    made = _without_doctype(current.read_bytes())
                    target.write_bytes(made["data"])
                    done.steps.append("The app wrote " + ", ".join(made["parts"][:6])
                                      + (f" and {len(made['parts']) - 6} more" if len(made["parts"]) > 6 else "")
                                      + " again without the document type declaration; every other part is your "
                                        "file's, byte for byte.")
                elif kind == "no-paragraph":
                    target.write_bytes(_with_paragraph(current.read_bytes()))
                    done.steps.append("The app added one empty paragraph to the body, which had none; every other "
                                      "part is your file's, byte for byte.")
                else:
                    report = _by_word(current, target, kind, password)
                    said = "; ".join(word_actions.account(report))
                    done.steps.append({"format": f"Word opened a private copy of the {path.suffix.lower()} file and "
                                                 f"saved it as a .docx",
                                       "strict": "Word opened a private copy of the Strict Open XML document and "
                                                 "saved it as an ordinary .docx",
                                       "password": "Word opened a private copy with the password you typed and saved "
                                                   "it as a .docx without one",
                                       "unopenable": "Word opened a private copy with Open and Repair and saved what "
                                                     "it could read as a .docx"}[kind]
                                      + (f" (Word's own account: {said})." if said else "."))
                    if kind == "password" and NO_PASSWORD_NOW not in done.notes:
                        done.notes.append(NO_PASSWORD_NOW)
                    if path.suffix.lower() == ".docm" and NO_MACROS not in done.notes:
                        done.notes.append(NO_MACROS)
                    if not target.is_file():
                        raise RescueFailed("Word reported no error and saved no document.")
                current = target
            found = triage_file(current)
            if not found.refused:
                break
            following = kind_of(current, found.code)
            # what is still wrong must be something else, and the app's own to mend: Word is asked once
            if step == MAX_STEPS or following not in APP_KINDS or following == kind:
                raise RescueFailed(f"After that the file is still one the app cannot take: {found.reason}")
            kind = following
        if current != path:
            final = workdir / name
            final.write_bytes(current.read_bytes())
            done.path = final
            done.holds = _holds(final)
        outcome.update(steps=len(done.steps), written=done.path is not None, limits=sorted(done.limits))
    return done
