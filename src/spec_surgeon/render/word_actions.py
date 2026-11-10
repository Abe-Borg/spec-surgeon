"""The named Word actions, as Word's own commands (Obliterate mode only; docs/BACKLOG.md O5).

Each function takes the open document and returns ``(count, note)``: how many things Word says it changed, and — when
it left some as they were — why, in a few words. The functions are the whole menu (``spec_surgeon.word_actions.MENU``
names them, one for one); nothing here is composed at run time and nothing takes code or a script from outside.

They run inside ``WordSession.run_actions``: one private Word instance, macros disabled, on a private copy of the
document, saved under a new name. None of this can be run without Word, so the tests drive the app through a fake of
the worker's seam (``spec_surgeon.word_actions.run_word_actions``) and the real behaviour of each action is proven
on the owner's machine (docs/BACKLOG.md, "Waiting on the owner's machine").

Constants are Word's own enumerations, written as numbers because the instance is driven late-bound. A field's kind
is read from its own instruction text (``PAGE``, ``LINK``…), never from a numeric type.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, List, Tuple

WD_FORMAT_XML_DOCUMENT = 12              # wdFormatXMLDocument: a .docx
WD_NO_PROTECTION = -1                    # wdNoProtection
WD_COLLAPSE_START = 1                    # wdCollapseStart
WD_HEADER_FOOTER_PRIMARY = 1             # wdHeaderFooterPrimary
CURRENT_COMPATIBILITY = 15               # the mode of Word 2013 and every later version
# wdMainTextStory, wdFootnotesStory, wdEndnotesStory, wdTextFrameStory: what "unlink fields" reaches; headers and
# footers (their page numbers and running titles) are never unlinked
BODY_STORIES = (1, 2, 3, 5)
# msoChart, msoEmbeddedOLEObject, msoLinkedOLEObject, msoLinkedPicture, msoOLEControlObject, msoPicture: never deleted
KEPT_SHAPES = (3, 7, 10, 11, 12, 13)
# fields "update fields" leaves alone: they read another file, or stop to ask the user a question
OUTSIDE_FIELDS = ("LINK", "INCLUDETEXT", "INCLUDEPICTURE", "INCLUDE", "IMPORT", "DDE", "DDEAUTO", "DATABASE", "RD",
                  "ASK", "FILLIN")
NOTE_CHARS = 200


def _why(error: BaseException) -> str:
    from .word_com import com_error_text
    return " ".join((com_error_text(error) or f"{type(error).__name__}: {error}").split())[:NOTE_CHARS]


def _stories(doc, kinds=None) -> Iterator[Any]:
    """Every story of the document, each with the stories linked after it (the next header, the next text box);
    ``kinds`` keeps those story types alone."""
    for story in doc.StoryRanges:
        rng = story
        while rng is not None:
            if kinds is None or int(rng.StoryType) in kinds:
                yield rng
            rng = rng.NextStoryRange


def _shape_collections(doc) -> List[Any]:
    """The floating shapes of the body, and those of every header and footer (one collection holds them all)."""
    found = [doc.Shapes]
    try:
        found.append(doc.Sections(1).Headers(WD_HEADER_FOOTER_PRIMARY).Shapes)
    except Exception:  # noqa: BLE001 — a document with no section to read headers from has no header shapes
        pass
    return found


def _field_word(field) -> str:
    try:
        words = str(field.Code.Text or "").split()
    except Exception:  # noqa: BLE001 — a field whose instruction cannot be read is treated as an outside one
        return "?"
    return words[0].upper() if words else ""


def remove_protection(doc) -> Tuple[int, str]:
    n = 0
    if int(doc.ProtectionType) != WD_NO_PROTECTION:
        doc.Unprotect()                  # a restriction set with a password raises: Word's own words reach the user
        n += 1
    for name in ("ReadOnlyRecommended", "Final"):
        try:
            if bool(getattr(doc, name)):
                setattr(doc, name, False)
                n += 1
        except Exception:  # noqa: BLE001 — a Word that does not have the setting has nothing to turn off
            pass
    return n, ""


def accept_changes(doc) -> Tuple[int, str]:
    n = int(doc.Revisions.Count)
    doc.AcceptAllRevisions()
    return n, ""


def reject_changes(doc) -> Tuple[int, str]:
    n = int(doc.Revisions.Count)
    doc.RejectAllRevisions()
    return n, ""


def update_fields(doc) -> Tuple[int, str]:
    updated = skipped = 0
    for rng in _stories(doc):
        fields = rng.Fields
        i = 1
        while i <= int(fields.Count):            # a table of contents rebuilds the fields inside it as it updates
            field = fields.Item(i)
            if _field_word(field) in OUTSIDE_FIELDS + ("?",):
                skipped += 1
            else:
                field.Update()
                updated += 1
            i += 1
    note = (f"{skipped:,} left as {'it was' if skipped == 1 else 'they were'}: {'it reads' if skipped == 1 else 'they read'} "
            f"another file or ask a question") if skipped else ""
    return updated, note


def unlink_fields(doc) -> Tuple[int, str]:
    n = 0
    for rng in _stories(doc, BODY_STORIES):
        before = int(rng.Fields.Count)
        if before:
            rng.Fields.Unlink()
            n += before - int(rng.Fields.Count)
    return n, ""


def numbers_to_text(doc) -> Tuple[int, str]:
    before = int(doc.ListParagraphs.Count)
    if before:
        doc.ConvertNumbersToText()
    return before - int(doc.ListParagraphs.Count), ""


def upgrade_compatibility(doc) -> Tuple[int, str]:
    before = int(doc.CompatibilityMode)
    if before >= CURRENT_COMPATIBILITY:
        return 0, ""
    try:
        doc.Convert()
    except Exception:  # noqa: BLE001 — a Word without the Convert command: set the mode itself
        doc.SetCompatibilityMode(CURRENT_COMPATIBILITY)
    return (1 if int(doc.CompatibilityMode) != before else 0), ""


def _box_text(shape):
    """The text range of a shape that holds text, or None (a picture, a line, an empty box)."""
    try:
        frame = shape.TextFrame
        if not bool(frame.HasText):
            return None
        return frame.TextRange
    except Exception:  # noqa: BLE001 — a shape with no text frame
        return None


def _ungroup(shapes) -> None:
    """Groups taken apart, so the text boxes inside them are shapes of their own (a group inside a group too)."""
    for _ in range(8):
        groups = [shapes.Item(i) for i in range(int(shapes.Count), 0, -1) if int(shapes.Item(i).Type) == 6]
        if not groups:
            return
        for group in groups:
            try:
                group.Ungroup()
            except Exception:  # noqa: BLE001 — a group Word will not take apart stays; its boxes are counted as left
                return


def textboxes_to_text(doc) -> Tuple[int, str]:
    done = left = 0
    why = ""
    for shapes in _shape_collections(doc):
        _ungroup(shapes)
        for i in range(int(shapes.Count), 0, -1):
            shape = shapes.Item(i)
            text = _box_text(shape)
            if text is None:
                continue
            try:
                at = shape.Anchor.Paragraphs(1).Range
                at.Collapse(WD_COLLAPSE_START)
                at.FormattedText = text.FormattedText          # the box's paragraphs, before its anchor's
                if not str(at.Text or "").endswith("\r"):
                    at.InsertParagraphAfter()                   # the last line never runs into the anchor's words
                shape.Delete()
                done += 1
            except Exception as error:  # noqa: BLE001 — one box Word will not move stays as it is, and is counted
                left += 1
                why = why or _why(error)
    note = f"{left:,} left as {'it was' if left == 1 else 'they were'}: {why}" if left else ""
    return done, note


def delete_shapes(doc) -> Tuple[int, str]:
    done = left = 0
    why = ""
    for shapes in _shape_collections(doc):
        for i in range(int(shapes.Count), 0, -1):
            shape = shapes.Item(i)
            try:
                if int(shape.Type) in KEPT_SHAPES:
                    continue
                shape.Delete()
                done += 1
            except Exception as error:  # noqa: BLE001 — a shape Word will not delete stays, and is counted
                left += 1
                why = why or _why(error)
    note = f"{left:,} left as {'it was' if left == 1 else 'they were'}: {why}" if left else ""
    return done, note


def delete_comments(doc) -> Tuple[int, str]:
    """Every comment with its replies (Export anyway's clean PDF alone: never on the menu)."""
    n = int(doc.Comments.Count)
    if n:
        doc.DeleteAllComments()
    return n, ""


def _saved_again(doc) -> Tuple[int, str]:
    return 1, ""                         # Open and Repair, and Save as .docx: the opening and the saving are the action


COMMANDS: Dict[str, Callable[[Any], Tuple[int, str]]] = {
    "repair": _saved_again,
    "remove_protection": remove_protection,
    "accept_changes": accept_changes,
    "reject_changes": reject_changes,
    "update_fields": update_fields,
    "unlink_fields": unlink_fields,
    "numbers_to_text": numbers_to_text,
    "upgrade_compatibility": upgrade_compatibility,
    "textboxes_to_text": textboxes_to_text,
    "delete_shapes": delete_shapes,
    "convert": _saved_again,
    "delete_comments": delete_comments,
}
