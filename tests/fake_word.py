"""A stand-in for Microsoft Word behind ``spec_surgeon.word_actions.run_word_actions``, the one seam to Word
(docs/BACKLOG.md O5). No test machine has Word, so this does in Python what each named action does in the simplest
honest way, and then SAVES THE WHOLE FILE AGAIN the way Word does:

* every XML part is serialised again (no member keeps its bytes) and the members come in another order;
* Word's own save marks are written: revision-save ids on paragraphs, runs and section properties, paragraph ids, a
  spelling mark, the ``_GoBack`` bookmark, a run split in two, an ``rsid`` on every style and the ``rsids`` list in
  the settings;
* the header and footer parts are named again in another order, and the main part's relationship ids are given
  again (so a comparison by part name or by relationship id would say everything changed).

What it proves is the app around Word: the copies kept from before the approval, the report that reads a file Word
saved, the marks, the gate. What Word itself does with a real document is proven on the owner's machine.
"""
from __future__ import annotations

import copy
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
W, R, W14, REL = (f"{{{ns}}}" for ns in (W_NS, R_NS, W14_NS, REL_NS))
MAIN, MAIN_RELS = "word/document.xml", "word/_rels/document.xml.rels"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _unwrap(el) -> None:
    parent, at = el.getparent(), el.getparent().index(el)
    for child in list(el):
        parent.insert(at, child)
        at += 1
    parent.remove(el)


def _fields(root) -> int:
    return sum(1 for _ in root.iter(W + "fldSimple")) + sum(
        1 for c in root.iter(W + "fldChar") if c.get(W + "fldCharType") == "begin")


def _unlink(root) -> int:
    n = _fields(root)
    for simple in list(root.iter(W + "fldSimple")):
        _unwrap(simple)
    for run in list(root.iter(W + "r")):
        if run.find(W + "fldChar") is not None or run.find(W + "instrText") is not None:
            run.getparent().remove(run)
    return n


def _accept(root) -> int:
    n = 0
    for ins in list(root.iter(W + "ins")):
        if ins.getparent() is not None and ins.getparent().tag != W + "rPr":
            _unwrap(ins)
            n += 1
    for gone in list(root.iter(W + "del")):
        if gone.getparent() is not None and gone.getparent().tag != W + "rPr":
            gone.getparent().remove(gone)
            n += 1
    for change in list(root.iter(W + "rPrChange", W + "pPrChange")):
        change.getparent().remove(change)
        n += 1
    return n


def _reject(root) -> int:
    n = 0
    for ins in list(root.iter(W + "ins")):
        if ins.getparent() is not None and ins.getparent().tag != W + "rPr":
            ins.getparent().remove(ins)
            n += 1
    for kept in list(root.iter(W + "del")):
        if kept.getparent() is not None and kept.getparent().tag != W + "rPr":
            for text in kept.iter(W + "delText"):
                text.tag = W + "t"
            _unwrap(kept)
            n += 1
    return n


def _shape_runs(root) -> List[Any]:
    """The runs that hold a drawn object which is not a plain picture: a text box, a shape."""
    found = []
    for run in root.iter(W + "r"):
        drawn = [el for el in run.iter() if isinstance(el.tag, str)
                 and etree.QName(el).localname in ("drawing", "pict")]
        if not drawn or any(True for _ in run.iterancestors(W + "txbxContent")):
            continue
        holds_box = any(True for _ in run.iter(W + "txbxContent"))
        picture = any(isinstance(el.tag, str) and etree.QName(el).localname == "pic" for el in run.iter())
        if holds_box or not picture:
            found.append(run)
    return found


def _delete_shapes(root) -> int:
    runs = _shape_runs(root)
    for run in runs:
        run.getparent().remove(run)
    return len(runs)


def _boxes_to_text(root) -> int:
    n = 0
    for run in _shape_runs(root):
        box = next(run.iter(W + "txbxContent"), None)
        if box is None:
            continue
        anchor = next(run.iterancestors(W + "p"))
        for paragraph in box.findall(W + "p"):
            anchor.addprevious(copy.deepcopy(paragraph))
        run.getparent().remove(run)
        n += 1
    return n


def _numbers_to_text(root) -> int:
    n = 0
    for paragraph in root.iter(W + "p"):
        props = paragraph.find(W + "pPr")
        number = props.find(W + "numPr") if props is not None else None
        if number is None:
            continue
        props.remove(number)
        n += 1
        run = etree.Element(W + "r")
        text = etree.SubElement(run, W + "t")
        text.text = f"{n}. "
        text.set(XML_SPACE, "preserve")
        props.addnext(run)
    return n


def _save_marks(root, body: bool) -> None:
    """What Word writes into a text part of its own accord when it saves."""
    paragraphs = list(root.iter(W + "p"))
    for i, paragraph in enumerate(paragraphs):
        paragraph.set(W + "rsidR", "00C0FFEE")
        paragraph.set(W + "rsidRDefault", "00C0FFEE")
        paragraph.set(W14 + "paraId", f"{0x1A000000 + i:08X}")
        paragraph.set(W14 + "textId", "77777777")
    for run in root.iter(W + "r"):
        run.set(W + "rsidRPr", "00BADA55")
    for section in root.iter(W + "sectPr"):
        section.set(W + "rsidR", "00C0FFEE")
        section.set(W + "rsidSect", "00C0FFEE")
    for run in list(root.iter(W + "r")):
        texts = [c for c in run if isinstance(c.tag, str) and c.tag != W + "rPr"]
        if len(texts) == 1 and texts[0].tag == W + "t" and len(texts[0].text or "") > 3:
            # one run written as two, with a spelling mark between them: the same words, the same formatting
            text = texts[0]
            second = copy.deepcopy(run)
            first, rest = text.text[:2], text.text[2:]
            text.text, second.find(W + "t").text = first, rest
            text.set(XML_SPACE, "preserve")
            second.find(W + "t").set(XML_SPACE, "preserve")
            run.addnext(second)
            run.addnext(etree.Element(W + "proofErr", {W + "type": "spellStart"}))
            break
    if body and paragraphs:
        last = paragraphs[-1]
        last.append(etree.Element(W + "bookmarkStart", {W + "id": "9001", W + "name": "_GoBack"}))
        last.append(etree.Element(W + "bookmarkEnd", {W + "id": "9001"}))


def save_again(data: bytes, actions: List[str], *, rename: bool = True) -> Tuple[bytes, List[Dict[str, Any]]]:
    """``data`` with ``actions`` done and the whole file saved again: ``(bytes, report)``."""
    members: Dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        for name in source.namelist():
            members[name] = source.read(name)
    rels = etree.fromstring(members[MAIN_RELS])
    related: Dict[str, List[str]] = {}
    for rel in rels.findall(REL + "Relationship"):
        related.setdefault(rel.get("Type").rsplit("/", 1)[-1], []).append("word/" + rel.get("Target"))
    text_parts = [MAIN] + [p for kind in ("header", "footer", "footnotes", "endnotes") for p in related.get(kind, [])
                           if p in members]
    body_parts = [MAIN] + [p for kind in ("footnotes", "endnotes") for p in related.get(kind, []) if p in members]
    roots = {name: etree.fromstring(members[name]) for name in text_parts}
    settings_name = (related.get("settings") or [None])[0]
    settings = etree.fromstring(members[settings_name]) if settings_name in members else None

    report: List[Dict[str, Any]] = []
    for action in actions:
        count, note = 0, ""
        if action == "unlink_fields":
            count = sum(_unlink(roots[p]) for p in body_parts)
        elif action == "update_fields":
            count = sum(_fields(root) for root in roots.values())
        elif action == "accept_changes":
            count = sum(_accept(root) for root in roots.values())
        elif action == "reject_changes":
            count = sum(_reject(root) for root in roots.values())
        elif action == "delete_shapes":
            count = sum(_delete_shapes(root) for root in roots.values())
        elif action == "textboxes_to_text":
            count = sum(_boxes_to_text(root) for root in roots.values())
        elif action == "numbers_to_text":
            count = sum(_numbers_to_text(roots[p]) for p in body_parts)
        elif action == "remove_protection" and settings is not None:
            for found in list(settings.iter(W + "documentProtection")):
                settings.remove(found)
                count += 1
        elif action == "upgrade_compatibility" and settings is not None:
            for found in settings.iter(W + "compatSetting"):
                if found.get(W + "name") == "compatibilityMode" and int(found.get(W + "val") or 0) < 15:
                    found.set(W + "val", "15")
                    count = 1
        elif action in ("repair", "convert"):
            count = 1
        report.append({"action": action, "count": count, "note": note})

    # ---- and Word saves the whole file again
    for name, root in roots.items():
        _save_marks(root, body=name == MAIN)
    if settings is not None:
        saves = etree.SubElement(settings, W + "rsids")
        etree.SubElement(saves, W + "rsidRoot", {W + "val": "00C0FFEE"})
        etree.SubElement(saves, W + "rsid", {W + "val": "00BADA55"})
    styles_name = (related.get("styles") or [None])[0]
    styles = etree.fromstring(members[styles_name]) if styles_name in members else None
    if styles is not None:
        for style in styles.iter(W + "style"):
            etree.SubElement(style, W + "rsid", {W + "val": "00C0FFEE"})

    def written(root) -> bytes:
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    for name, root in roots.items():
        members[name] = written(root)
    if settings is not None:
        members[settings_name] = written(settings)
    if styles is not None:
        members[styles_name] = written(styles)

    # relationship ids given again, in the main part and wherever it names one
    renumber = {rel.get("Id"): f"rId{100 + i}" for i, rel in enumerate(rels.findall(REL + "Relationship"))}
    for rel in rels.findall(REL + "Relationship"):
        rel.set("Id", renumber[rel.get("Id")])
    main = etree.fromstring(members[MAIN])
    for el in main.iter():
        for name, value in list(el.attrib.items()):
            if name.startswith(R) and value in renumber:
                el.set(name, renumber[value])
    members[MAIN] = written(main)

    # the header and footer parts named again, in another order
    names: Dict[str, str] = {}
    if rename:
        for kind in ("header", "footer"):
            parts = [p for p in related.get(kind, []) if p in members]
            names.update(zip(parts, reversed(parts)))
    for rel in rels.findall(REL + "Relationship"):
        target = "word/" + rel.get("Target")
        if target in names:
            rel.set("Target", names[target][len("word/"):])
    members[MAIN_RELS] = written(rels)
    renamed: Dict[str, bytes] = {}
    for name, content in members.items():
        own = re.match(r"word/_rels/(.+)\.rels$", name)
        if name in names:
            renamed[names[name]] = content
        elif own and "word/" + own.group(1) in names:
            renamed["word/_rels/" + names["word/" + own.group(1)][len("word/"):] + ".rels"] = content
        else:
            renamed[name] = content
    order = ["[Content_Types].xml"] + sorted(n for n in renamed if n != "[Content_Types].xml")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for name in order:
            content = renamed[name]
            if name.endswith(("app.xml", "core.xml", "fontTable.xml", "webSettings.xml")):
                content = written(etree.fromstring(content))        # written again, like everything else
            target.writestr(name, content)
    return out.getvalue(), report


class FakeWord:
    """In place of ``word_actions.run_word_actions``: ``calls`` keeps every run (the file's name, its actions);
    ``fail`` maps a file's name to the exception Word stops with; ``nothing`` names files Word finds nothing to do
    in; ``rename`` turns the renaming of header and footer parts off."""

    def __init__(self, fail: Optional[Dict[str, BaseException]] = None, nothing=(), rename: bool = True):
        self.calls: List[Tuple[str, List[str]]] = []
        self.fail = dict(fail or {})
        self.nothing = set(nothing)
        self.rename = rename

    def __call__(self, source, actions, output, timeout=None):
        name = Path(source).name
        self.calls.append((name, list(actions)))
        if name in self.fail:
            raise self.fail[name]
        data, report = save_again(Path(source).read_bytes(), list(actions), rename=self.rename)
        if name in self.nothing:
            report = [{**entry, "count": 0} for entry in report]
        Path(output).write_bytes(data)
        return report
