"""Attach-time triage — deterministic, model-free, milliseconds per file.

The moment a file is attached the app must know whether the engine can work with it and what the user should hear
about it, before any plan and before any model call. ``triage_file`` answers with one of three states:

* ``refused`` — the engine will not take the file: not a file, not a .docx (.docm, .doc), Word's ``~$`` lock file, a
  package that does not open, or a body with no text at all. ``reason`` is one sentence naming the file and what to do.
* ``note``    — the file opens and will be worked on; ``notes`` lists what the user should know, one plain sentence
  each (protection, compatibility mode, Normal-only styling, typed numbers, fragmented runs, text-box headers,
  pending revisions, comments, irregular spacing). ``ok`` — none of the above.

``facts`` carries the raw values the notes were read from (the fingerprint's pattern and regimes, the counts, the
compatibility mode, the protection) so the planner plans with them instead of discovering them. Everything here reads
the package through ``DocxPackage`` and ``fingerprint``; nothing imports the verifier and nothing calls a model.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from lxml import etree

from .fingerprint import fingerprint
from .package import W, DocxPackage, UnsupportedFormat
from .package_limits import ArchiveLimitError

STATES = ("ok", "note", "refused")
CURRENT_COMPAT_MODE = 15        # Word 2013 and later; below it Word shows "[Compatibility Mode]" in the title bar
FRAGMENTATION_LIMIT = 2.5       # runs per non-empty body paragraph beyond which the text reads as heavily edited

# What the user hears — one plain sentence each, in the order the notes are given.
NOTE_PROTECTED = "Editing is restricted in Word; edited copies keep that restriction."
NOTE_COMPAT_MODE = "Saved in an older Word compatibility mode; page layout can differ from current Word."
NOTE_NORMAL_ONLY = "No paragraph styles are in use; changes will pick paragraphs by appearance and text."
NOTE_TYPED_NUMBERS = "Numbers are typed by hand, not automatic list numbering."
NOTE_FRAGMENTED = "The text is split into many small runs, a sign of heavy editing; edits still verify."
NOTE_TEXTBOX_HF = "Headers or footers contain text boxes; header and footer changes may be set aside."
NOTE_COMMENTS = "The file has comments."
NOTE_SPACING = "Spacing is irregular: non-breaking or doubled spaces."

# What stops a package from opening: not a zip, Strict Open XML, a missing member or element, an archive over its
# limits, an unreadable path, unparsable XML, a malformed relationship.
_OPEN_ERRORS = (zipfile.BadZipFile, UnsupportedFormat, KeyError, ValueError, OSError, etree.LxmlError, AttributeError)


def revisions_note(count: int) -> str:
    return f"{count} tracked change{'' if count == 1 else 's'} {'is' if count == 1 else 'are'} pending."


@dataclass
class Triage:
    path: Path
    state: str = "ok"                       # one of STATES
    notes: List[str] = field(default_factory=list)
    reason: str = ""                        # the refusal: one sentence naming the file and what to do
    pattern_id: str = ""                    # the fingerprint's structural pattern (empty when the file never opened)
    facts: Dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.state == "refused"

    def to_dict(self) -> Dict[str, Any]:
        return {"path": str(self.path), "state": self.state, "notes": list(self.notes), "reason": self.reason,
                "pattern_id": self.pattern_id, "facts": dict(self.facts)}


def _refusal(path: Path, reason: str, **kw) -> Triage:
    return Triage(path, "refused", reason=reason, **kw)


def _settings(pkg: DocxPackage, main: str) -> Optional[etree._Element]:
    """The settings part, resolved through the main part's relationships (never assumed); None when there is none."""
    for rel in pkg.rels(main).values():
        if rel.type.endswith("/settings") and rel.resolved and pkg.has(rel.resolved):
            return pkg.xml(rel.resolved)
    return None


def _compat_mode(settings: Optional[etree._Element]) -> Optional[int]:
    """``w:compat/w:compatSetting[@w:name="compatibilityMode"]`` as an integer; None when absent or not a number."""
    if settings is None:
        return None
    for cs in settings.iter(W + "compatSetting"):
        if cs.get(W + "name") == "compatibilityMode":
            try:
                return int(cs.get(W + "val") or "")
            except ValueError:
                return None
    return None


def _protection(settings: Optional[etree._Element]) -> Dict[str, Any]:
    """``w:documentProtection``: whether it is enforced and what it restricts (readOnly, comments, forms, ...)."""
    dp = settings.find(W + "documentProtection") if settings is not None else None
    if dp is None:
        return {"enforced": False, "edit": None}
    return {"enforced": dp.get(W + "enforcement") in ("1", "true", "on"), "edit": dp.get(W + "edit")}


def _body_has_text(doc: etree._Element) -> bool:
    """Any live text at all in the body (tables and text boxes included; deleted text is w:delText, not counted)."""
    scope = doc.find(W + "body") if doc.find(W + "body") is not None else doc
    return any((t.text or "").strip() for t in scope.iter(W + "t"))


def triage_file(path) -> Triage:
    """One file, the moment it is attached: refused with a reason, noted, or ok — with the facts either way."""
    path = Path(path)
    name = path.name
    if name.startswith("~$"):
        return _refusal(path, f"{name} is Word's lock file for an open document, not a document; close the document "
                              f"in Word and attach the .docx itself.")
    if not path.is_file():
        if path.exists():
            return _refusal(path, f"{name} is a folder, not a file; attach the .docx files inside it.")
        return _refusal(path, f"{name} was not found at {path.parent}; check that the file still exists and attach "
                              f"it again.")
    if path.suffix.lower() != ".docx":
        return _refusal(path, f"{name} is not a .docx file: .docm and .doc files are not supported; save it as .docx "
                              f"in Word.")
    try:
        pkg = DocxPackage.from_path(path)
        main = pkg.main_part()
        doc = pkg.xml(main)
        details = fingerprint(pkg)
        kinds = pkg.parts_by_kind()
        settings = _settings(pkg, main)
    except UnsupportedFormat as exc:
        return _refusal(path, f"{name} is a Strict Open XML document, which cannot be opened as a Word package; in "
                              f"Word, save it again as 'Word Document (*.docx)'.", facts={"error": str(exc)})
    except ArchiveLimitError as exc:
        return _refusal(path, f"{name} is larger than this app opens ({exc}); split the document or save a smaller "
                              f"copy in Word.", facts={"error": str(exc)})
    except _OPEN_ERRORS as exc:
        return _refusal(path, f"{name} cannot be opened as a Word package; open it in Word and save it again as "
                              f".docx.", facts={"error": f"{type(exc).__name__}: {exc}"[:300]})

    hygiene = details["body_hygiene"]
    parts = details["parts"]
    hf_parts = kinds["headers"] + kinds["footers"]
    revisions = sum(d["ins"] + d["del"] for d in parts.values())
    textboxes_hf = sum(parts.get(p, {}).get("textboxes", 0) for p in hf_parts)
    compat = _compat_mode(settings)
    protection = _protection(settings)
    has_text = _body_has_text(doc)
    facts: Dict[str, Any] = {
        "pattern_id": details["pattern_id"],
        "style_regime": hygiene["style_regime"],
        "dominant_style": hygiene["dominant_style"],
        "numbering_regime": hygiene["numbering_regime"],
        "section_anchor_shape": hygiene["section_anchor_shape"],
        "run_fragmentation_ratio": hygiene["run_fragmentation_ratio"],
        "has_nbsp": hygiene["has_nbsp"],
        "has_multi_space": hygiene["has_multi_space"],
        "sections": len(details["sections"]),
        "headers": list(kinds["headers"]),
        "footers": list(kinds["footers"]),
        "textboxes_in_headers_footers": textboxes_hf,
        "pending_revisions": revisions,
        "track_revisions": details["trackRevisions"],
        "has_comments": details["has_comments"],
        "has_footnotes": details["has_footnotes"],
        "compatibility_mode": compat,
        "protection_enforced": protection["enforced"],
        "protection_edit": protection["edit"],
        "body_has_text": has_text,
    }
    if not has_text:
        return _refusal(path, f"{name} has no text in its body, so there is nothing to edit; check that this is the "
                              f"right file.", pattern_id=details["pattern_id"], facts=facts)

    notes: List[str] = []
    if protection["enforced"]:
        notes.append(NOTE_PROTECTED)
    if compat is not None and compat < CURRENT_COMPAT_MODE:
        notes.append(NOTE_COMPAT_MODE)
    if hygiene["style_regime"] == "normal_only":
        notes.append(NOTE_NORMAL_ONLY)
    if hygiene["numbering_regime"] == "manual_text":
        notes.append(NOTE_TYPED_NUMBERS)
    if hygiene["run_fragmentation_ratio"] > FRAGMENTATION_LIMIT:
        notes.append(NOTE_FRAGMENTED)
    if textboxes_hf:
        notes.append(NOTE_TEXTBOX_HF)
    if revisions:
        notes.append(revisions_note(revisions))
    if details["has_comments"]:
        notes.append(NOTE_COMMENTS)
    if hygiene["has_nbsp"] or hygiene["has_multi_space"]:
        notes.append(NOTE_SPACING)
    return Triage(path, "note" if notes else "ok", notes=notes, pattern_id=details["pattern_id"], facts=facts)


def triage_files(paths: Iterable) -> List[Triage]:
    """Every path, in the order given — each on its own, so one refusal never hides another."""
    return [triage_file(p) for p in paths]
