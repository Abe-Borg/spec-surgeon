"""What the reference rules see at the top of each specification, printed for a person to read and send on (backlog
S4: the rules find no section number in some owners' layouts, and the layouts cannot be designed for unseen).

    py -3 tests\\section_lines.py "C:\\path\\to\\the folder of .docx files"  >  section_lines.txt

For each Word document in the folder (subfolders not entered) it prints, read with the app's own readers and nothing
else: what ``slots.REFERENCE_FILLS`` reads as the section number and title, or why not; the first body paragraphs up
to PART 1; every body paragraph that starts with "Section" or holds "END OF SECTION"; and the lines of the headers
and footers Word shows. Every line is clipped (``CLIP`` characters), so this is the top of each file and its
section references, never its content. **It reads and never writes**: no file is opened for writing, copied or
changed. Read what it printed before sending it to anyone: it quotes the documents' own first lines.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CLIP = 110            # characters of a line that are printed
TOP = 14              # body paragraphs with words printed from the top (fewer when PART 1 comes first)
REFERENCES = 12       # paragraphs starting with "Section" that are printed


def shown(text: str) -> str:
    """One paragraph on one line: a line break as ⏎, a tab as →, the rest clipped."""
    text = text.replace("\r", "").replace("\n", "⏎").replace("\t", "→")
    return text if len(text) <= CLIP else text[:CLIP - 1] + "…"


def describe(path: Path) -> list:
    from spec_surgeon.ops.base import JobException
    from spec_surgeon.ops.hf_common import effective_refs
    from spec_surgeon.ops.slots import REFERENCE_FILLS, display_text, extract_fill
    from spec_surgeon.ooxml import paragraphs
    from spec_surgeon.package import W, DocxPackage
    out = [f"=== {path.name}"]
    try:
        pkg = DocxPackage.from_path(path)
        body = paragraphs(pkg.xml(pkg.main_part()))
    except Exception as error:  # noqa: BLE001 — a file the app cannot open is said, and the next one is read
        return out + [f"    cannot be read: {type(error).__name__}: {shown(str(error))}"]
    for name, rule in REFERENCE_FILLS.items():
        try:
            out.append(f"    {name}: {shown(extract_fill(pkg, name, rule))!r}")
        except JobException as refused:
            values = (refused.details or {}).get("values")
            out.append(f"    {name}: NOT READ [{refused.code}]" + (f" values {values}" if values else ""))
    out.append("    top of the body (paragraph number, style, where, text):")
    listed = 0
    for i, p in enumerate(body):
        text = display_text(p)
        if not text.strip():
            continue
        style = p.find(f"{W}pPr/{W}pStyle")
        where = "table" if any(True for _ in p.iterancestors(W + "tbl")) else \
            "text box" if any(True for _ in p.iterancestors(W + "txbxContent")) else "body"
        out.append(f"      {i:>4}  {(style.get(W + 'val') if style is not None else '-'):<18} {where:<8} {shown(text)}")
        listed += 1
        if listed >= TOP or re.match(r"\s*PART\s*1\b", text, re.IGNORECASE):
            break
    refs = [(i, display_text(p)) for i, p in enumerate(body)
            if re.match(r"\s*section\b", display_text(p), re.IGNORECASE) or "END OF SECTION" in display_text(p).upper()]
    out.append(f"    paragraphs that start with \"Section\" or hold END OF SECTION: {len(refs)}")
    out += [f"      {i:>4}  {shown(text)}" for i, text in refs[:REFERENCES]]
    if len(refs) > REFERENCES:
        out.append(f"      … and {len(refs) - REFERENCES} more; the last: {refs[-1][0]}  {shown(refs[-1][1])}")
    try:
        first = (effective_refs(pkg) or [{}])[0]
    except Exception as error:  # noqa: BLE001
        first = {}
        out.append(f"    headers and footers cannot be read: {type(error).__name__}")
    for (kind, typ), part in sorted(first.items()):
        if not part:
            continue
        lines = [shown(display_text(p)) for p in paragraphs(pkg.xml(part)) if display_text(p).strip()]
        out.append(f"    {typ} {kind}: " + (" | ".join(lines) if lines else "(no words)"))
    return out


def main(argv: list) -> int:
    if len(argv) != 2 or not Path(argv[1]).is_dir():
        print(__doc__)
        return 2
    files = sorted(p for p in Path(argv[1]).iterdir() if p.suffix.lower() == ".docx" and not p.name.startswith("~$"))
    print(f"{len(files)} document(s) in the folder")
    for path in files:
        print("\n".join(describe(path)))
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 — an older console keeps its own encoding
        pass
    raise SystemExit(main(sys.argv))
