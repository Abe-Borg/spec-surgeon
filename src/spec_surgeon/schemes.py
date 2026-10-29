"""The bundled numbering schemes (docs/SPEC_FORMATTER_MODES_DESIGN.md N5): two example files that ship with the app, for
a conversion the user wants with no template of their own — CSC PageFormat (the Specification Formatter's built-in
nine levels) and CSI SectionFormat.

The assistant offers one as an ``ask_user`` choice of kind ``scheme``; the label is written here, never by Claude
(``label``). It is an app choice (``conversation.APP_CHOICE_KINDS``): the window copies the file into its scratch
folder and attaches it as the template (``DocumentTask._take_scheme``), so the scheme's numbering text comes from a file
the user attached by their pick, and the ops and the gate see an ordinary template. Nothing here edits a file.

``tests/make_scheme_examples.py`` builds the files byte-stable; ``tests/test_schemes.py`` compares them with a fresh
build, so a stale file fails CI.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

SCHEMES_DIR = Path(__file__).resolve().parent / "examples" / "schemes"

# id → (file name, the scheme's name in the user's words, its level styles (ids, level 0 first), what each level shows)
SCHEMES: Dict[str, Tuple[str, str, Tuple[str, ...], Tuple[str, ...]]] = {
    "csc": ("CSC PageFormat scheme.docx", "CSC PageFormat",
            ("PageFormatPart", "PageFormatArticle") + tuple(f"PageFormatLevel{k}" for k in range(3, 10)),
            ("PART 1", "1.1", ".1", ".1", ".1", ".1", ".1", ".1", ".1")),
    "csi": ("CSI scheme.docx", "CSI SectionFormat",
            ("CSIPart", "CSIArticle", "CSIParagraph", "CSISubparagraph") + tuple(f"CSILevel{k}" for k in range(5, 10)),
            ("PART 1", "1.1", "A.", "1.", "a.", "1)", "a)", "(1)", "(a)")),
}


def label(scheme: str) -> str:
    """The choice's label, the app's own words."""
    return f"Use the built-in {SCHEMES[scheme][1]} scheme as the template"


def detail(scheme: str, replaces: bool) -> str:
    _, name, _, shows = SCHEMES[scheme]
    return (f"The {name} numbering ({', '.join(dict.fromkeys(shows))}) comes from a scheme file that ships with Spec "
            f"Surgeon; picking it attaches that file as the template"
            + (" in place of the template you attached" if replaces else "") + ".")


def scheme_file(scheme: str) -> Path:
    """The bundled file; ``FileNotFoundError`` when the package was built without it."""
    path = SCHEMES_DIR / SCHEMES[scheme][0]
    if not path.is_file():
        raise FileNotFoundError(f"the bundled {SCHEMES[scheme][1]} scheme is missing: {path}")
    return path


def levels(scheme: str) -> List[str]:
    return list(SCHEMES[scheme][2])


def prompt_note() -> str:
    """The assistant's guidance (compiler.CHAT_BASICS quotes it): when and how to offer a scheme."""
    return ("With no template attached, a conversion to Canadian CSC PageFormat or to standard CSI SectionFormat "
            "numbering needs a scheme: offer the bundled one through ask_user as a choice of kind scheme (scheme "
            "\"csc\" or \"csi\"; the app writes its label). Picking it attaches the scheme file as the template and "
            "the request is planned again; its level styles are "
            + "; ".join(f"{name}: {', '.join(styles)}" for _, name, styles, _ in SCHEMES.values()) + ".")
