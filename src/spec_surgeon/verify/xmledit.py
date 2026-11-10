"""V10 kind ``xml_edit`` — edit_xml, verified (Obliterate mode only; docs/BACKLOG.md O3).

This module's own reading of one stated change to the XML of one part (transform side: ops/edit_xml.py). Nothing here
comes from ops/ (CLAUDE.md invariant 2): the prefix table, the judgement of the expression, its evaluation, the edit
and the places of the elements are all written again here.

The assertion says: ``part`` (a member of the source that is XML and is neither a relationships member nor the
content types); ``xpath`` and ``namespaces`` (prefixes beyond this module's own table); ``count``; ``places_sha256``;
and the action — ``action`` with ``name`` (an attribute, in ``{namespace}local`` form), ``value``, ``text``, ``xml``,
``position`` or ``keep`` as the action uses them.

The check, on a fresh copy of the SOURCE part:

1. the expression is one this module accepts (not longer than ``V_MAX_XPATH_CHARS``; inside ``[ ]`` no path that
   starts at the root and no ``following`` or ``preceding`` axis) and evaluates within ``V_XPATH_TIMEOUT_S`` to
   elements only;
2. it selects exactly ``count`` elements, and their places — each element's position among its parent's children,
   from the root — hash to ``places_sha256`` (the plan chose these elements and no others);
3. the action can be done to every one of them (else the op had to refuse: the root removed, replaced, unwrapped or
   given a sibling; an element inside another selected one removed, replaced or unwrapped; an attribute removed that
   is not there; text set on an element that holds elements; an element unwrapped that lacks its one ``keep`` child or
   holds text of its own);
4. this module makes the edit itself, last element first: an attribute set or deleted; the element taken out (the
   text that followed it stays); the elements of ``xml`` — white space between elements dropped, an element's own
   text kept — placed before or after it, or as its first or last children, or in its place; its text set (a
   WordprocessingML text element given ``xml:space="preserve"`` when the text starts or ends with white space); or
   its children, or its ``keep`` child's, put in its place;
5. the result is compared WHOLE with the output part (``tree_difference``): exactly those elements changed as
   stated, and nothing else in the part did.

The part is then one a kind compared whole (``ctx.verified_parts`` for a text part, ``ctx.verified_members`` for any
other), so the residual leaves it to this kind. The suite reads ``raw_parts`` for the same parts: their text is not
predicted block by block (no plan could, without making the edit), so V2, V3, the counts of V4 and V5b leave them to
this comparison, and the record lists what the edit did to the words instead (``D1``, ``verify/difference.py``).
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from .markup import tree_difference
from .props import Ctx
from .reader import W

V_XPATH_TIMEOUT_S = 2.0
V_MAX_XPATH_CHARS = 1000
_V_XML = "http://www.w3.org/XML/1998/namespace"
_V_BOOKKEEPING = "[Content_Types].xml"
_V_ACTIONS = {"set_attribute", "remove_attribute", "remove", "replace", "insert", "set_text", "unwrap"}
_V_WHERE = {"before", "after", "first_child", "last_child"}
_V_KEEPS_SPACE = {W + "t", W + "delText", W + "instrText", W + "delInstrText"}
_V_PREFIXES = (
    ("w", "http://schemas.openxmlformats.org/wordprocessingml/2006/main"),
    ("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    ("m", "http://schemas.openxmlformats.org/officeDocument/2006/math"),
    ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
    ("wp", "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"),
    ("a", "http://schemas.openxmlformats.org/drawingml/2006/main"),
    ("pic", "http://schemas.openxmlformats.org/drawingml/2006/picture"),
    ("c", "http://schemas.openxmlformats.org/drawingml/2006/chart"),
    ("dgm", "http://schemas.openxmlformats.org/drawingml/2006/diagram"),
    ("v", "urn:schemas-microsoft-com:vml"),
    ("o", "urn:schemas-microsoft-com:office:office"),
    ("w10", "urn:schemas-microsoft-com:office:word"),
    ("w14", "http://schemas.microsoft.com/office/word/2010/wordml"),
    ("w15", "http://schemas.microsoft.com/office/word/2012/wordml"),
    ("wp14", "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"),
    ("wps", "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"),
    ("wpg", "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"),
    ("wpc", "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"),
    ("a14", "http://schemas.microsoft.com/office/drawing/2010/main"),
    ("cp", "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"),
    ("dc", "http://purl.org/dc/elements/1.1/"),
    ("dcterms", "http://purl.org/dc/terms/"),
    ("ep", "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"),
    ("vt", "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"),
    ("ds", "http://schemas.openxmlformats.org/officeDocument/2006/customXml"),
)


def raw_parts(plan: Dict[str, Any]) -> List[str]:
    """The parts a plan's ``xml_edit`` assertions name: the ones this kind compares whole."""
    return sorted({a["part"] for a in plan.get("property_assertions") or []
                   if isinstance(a, dict) and a.get("kind") == "xml_edit" and isinstance(a.get("part"), str)})


def _v_prefixes(extra: Any) -> Dict[str, str]:
    table = dict(_V_PREFIXES)
    if extra is None:
        return table
    if not isinstance(extra, dict):
        raise ValueError("the edit's namespaces are not a mapping")
    for prefix, address in extra.items():
        if (not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z_][\w.\-]*", prefix)
                or prefix.lower() in ("xml", "xmlns") or not isinstance(address, str) or not address.strip()):
            raise ValueError(f"the edit declares a prefix that cannot be one: {prefix!r}")
        table[prefix] = address
    return table


def _v_word_end(text: str, at: int) -> int:
    """Where the name that starts at ``at`` ends: letters, digits, ``_``, ``-`` and ``.``."""
    while at < len(text) and (text[at].isalnum() or text[at] in "_-."):
        at += 1
    return at


def _v_judge(expression: Any) -> str:
    """The expression, or a ValueError when this module does not evaluate it (the op had to refuse it): the
    ``following`` or ``preceding`` axis anywhere, or inside ``[ ]`` a path that starts at the root of the part.

    One pass, character by character. ``after`` is what the last token was: ``"value"`` when a slash after it goes
    on with a path (a name, ``.``, ``)``, ``]``, a literal), ``"gap"`` when a slash after it starts one at the root.
    ``and``, ``or``, ``div``, ``mod`` and ``*`` are operators after a value and names anywhere else: XPath's own
    rule."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("the edit has no XPath")
    text = expression.strip()
    if len(text) > V_MAX_XPATH_CHARS:
        raise ValueError(f"the XPath is longer than {V_MAX_XPATH_CHARS} characters")
    at, inside, after, word = 0, 0, "gap", ""
    while at < len(text):
        ch = text[at]
        if ch.isspace():
            at += 1
            continue
        this_word = ""
        if ch in "'\"":
            close = text.find(ch, at + 1)
            if close < 0:
                raise ValueError("the XPath has an unclosed quote")
            at, after = close + 1, "value"
        elif ch == "/":
            if inside and after != "value":
                raise ValueError("inside [ ] the XPath starts a path at the root of the part")
            at += 2 if text.startswith("//", at) else 1
            after = "gap"
        elif text.startswith("::", at):
            if word in ("following", "preceding"):
                raise ValueError(f"the XPath uses the {word} axis")
            at, after = at + 2, "gap"
        elif ch == "[":
            inside, at, after = inside + 1, at + 1, "gap"
        elif ch == "]":
            inside, at, after = inside - 1, at + 1, "value"
            if inside < 0:
                raise ValueError("the XPath closes a bracket it never opened")
        elif ch in "(,@$":
            at, after = at + 1, "gap"
        elif ch == ")":
            at, after = at + 1, "value"
        elif ch == "*":
            at, after = at + 1, ("gap" if after == "value" else "value")
        elif ch in "=<>+-|!":
            at += 2 if text[at:at + 2] in ("!=", "<=", ">=") else 1
            after = "gap"
        elif ch == "." and not text[at + 1:at + 2].isdigit():
            at += 2 if text.startswith("..", at) else 1
            after = "value"
        elif ch.isdigit() or ch == ".":
            while at < len(text) and (text[at].isdigit() or text[at] == "."):
                at += 1
            after = "value"
        elif ch.isalpha() or ch == "_":
            stop = _v_word_end(text, at)
            this_word = text[at:stop]
            prefixed = False
            if text[stop:stop + 1] == ":" and text[stop + 1:stop + 2] != ":":     # a prefix and its local name
                local = text[stop + 1:stop + 2]
                if local == "*":
                    stop, prefixed = stop + 2, True
                elif local.isalpha() or local == "_":
                    stop, prefixed = _v_word_end(text, stop + 1), True
            if after == "value" and not prefixed and this_word in ("and", "or", "div", "mod"):
                after = "gap"
            else:
                after = "value"
            if prefixed:
                this_word = ""
            at = stop
        else:
            raise ValueError(f"the XPath holds a character it cannot ({ch!r})")
        word = this_word
    if inside:
        raise ValueError("the XPath opens a bracket it never closes")
    return text


def _v_evaluate(root, expression: str, prefixes: Dict[str, str]) -> List[Any]:
    try:
        path = etree.XPath(expression, namespaces=prefixes, regexp=False, smart_strings=False)
    except Exception as error:  # noqa: BLE001 — an expression lxml will not take: the op had to refuse it
        raise ValueError(f"the XPath does not compile ({error})") from None
    result: Dict[str, Any] = {}

    def run() -> None:
        try:
            result["nodes"] = path(root)
        except Exception as error:  # noqa: BLE001 — reported below, on the caller's thread
            result["failed"] = str(error)
    thread = threading.Thread(target=run, name="xml-edit-check", daemon=True)
    thread.start()
    thread.join(V_XPATH_TIMEOUT_S)
    if thread.is_alive():
        raise ValueError(f"the XPath took longer than {V_XPATH_TIMEOUT_S:g} s to evaluate")
    if "failed" in result:
        raise ValueError(f"the XPath could not be evaluated ({result['failed']})")
    nodes = result.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("the XPath does not select elements")
    chosen, met = [], set()
    for node in nodes:
        if not isinstance(node, etree._Element) or not isinstance(node.tag, str):
            raise ValueError("the XPath selects something that is not an element")
        if id(node) not in met:
            met.add(id(node))
            chosen.append(node)
    return chosen


def _v_places(root, chosen: List[Any]) -> List[List[int]]:
    """Where each chosen element sits: its index among its parent's child nodes, level by level from the root. Read
    off one pass over the tree in document order (the chosen elements are held by the caller, so the pass meets the
    same objects)."""
    marks = {id(el): n for n, el in enumerate(chosen)}
    places: List[Optional[List[int]]] = [None] * len(chosen)
    if id(root) in marks:
        places[marks[id(root)]] = []

    def descend(parent, trail: Tuple[int, ...]) -> None:
        pending = [(parent, trail)]
        while pending:
            node, here = pending.pop()
            for index, child in enumerate(node):
                if id(child) in marks:
                    places[marks[id(child)]] = list(here) + [index]
                if isinstance(child.tag, str) and len(child):
                    pending.append((child, here + (index,)))
    descend(root, ())
    if any(place is None for place in places):
        raise ValueError("a selected element is not in the part")
    return [place for place in places if place is not None]


def _v_digest(places: List[List[int]]) -> str:
    return hashlib.sha256(json.dumps(places, separators=(",", ":")).encode("ascii")).hexdigest()


def _v_fragment(xml: Any, prefixes: Dict[str, str]) -> List[Any]:
    """The elements an ``xml`` value places, as this module reads them."""
    if not isinstance(xml, str) or not xml.strip():
        raise ValueError("the edit places no XML")
    opening = "<x " + " ".join(
        'xmlns:%s="%s"' % (prefix, address.replace("&", "&amp;").replace('"', "&quot;"))
        for prefix, address in sorted(prefixes.items())) + ">"
    parser = etree.XMLParser(remove_blank_text=False, resolve_entities=False, huge_tree=True)
    try:
        shell = etree.fromstring((opening + xml + "</x>").encode("utf-8"), parser)
    except etree.XMLSyntaxError as error:
        raise ValueError(f"the XML the edit places is not well formed ({error})") from None
    if (shell.text or "").strip():
        raise ValueError("the XML the edit places has text outside an element")
    placed = []
    for child in shell:
        if not isinstance(child.tag, str) or (child.tail or "").strip():
            raise ValueError("the XML the edit places holds something that is not an element")
        placed.append(child)
    if not placed:
        raise ValueError("the XML the edit places holds no element")
    for top in placed:
        top.tail = None
        for node in top.iter():
            if not isinstance(node.tag, str):
                continue
            if len(node) and not (node.text or "").strip():
                node.text = None
            for inner in node:
                if not (inner.tail or "").strip():
                    inner.tail = None
    return placed


def _v_has_text(el) -> bool:
    return bool((el.text or "").strip()) or any((child.tail or "").strip() for child in el)


def _v_source_of_children(el, keep: Optional[str]):
    if keep is None:
        return el
    named = [child for child in el if child.tag == keep]
    return named[0] if len(named) == 1 else None


def _v_must_refuse(chosen: List[Any], a: Dict[str, Any]) -> Optional[str]:
    """Why the op had to refuse this edit of these elements, or None."""
    action = a["action"]
    leaves_place = action in ("remove", "replace", "unwrap") or (action == "insert" and a.get("position") in
                                                                 ("before", "after"))
    ids = {id(el) for el in chosen}
    for el in chosen:
        if leaves_place and el.getparent() is None:
            return "the root element is selected"
        if action in ("remove", "replace", "unwrap"):
            above = el.getparent()
            while above is not None:
                if id(above) in ids:
                    return "an element inside another selected element is selected"
                above = above.getparent()
        if action == "remove_attribute" and a.get("name") not in el.attrib:
            return "a selected element lacks the attribute"
        if action == "set_text" and len(el):
            return "a selected element holds other elements"
        if action == "unwrap":
            inner = _v_source_of_children(el, a.get("keep"))
            if inner is None:
                return "a selected element does not have exactly one child of the kind to keep"
            if _v_has_text(inner) or (inner is not el and _v_has_text(el)):
                return "a selected element holds text of its own"
    return None


def _v_take_out(el) -> None:
    parent = el.getparent()
    if el.tail:
        earlier = el.getprevious()
        if earlier is None:
            parent.text = (parent.text or "") + el.tail
        else:
            earlier.tail = (earlier.tail or "") + el.tail
    parent.remove(el)


def _v_place(el, placed: List[Any], where: str) -> None:
    fresh = [copy.deepcopy(node) for node in placed]
    if where == "last_child":
        el.extend(fresh)
        return
    if where == "first_child":
        for offset, node in enumerate(fresh):
            el.insert(offset, node)
        return
    parent = el.getparent()
    start = parent.index(el)
    if where == "after":
        start += 1
        fresh[-1].tail, el.tail = el.tail, None
    for offset, node in enumerate(fresh):
        parent.insert(start + offset, node)


def _v_edit(chosen: List[Any], a: Dict[str, Any], prefixes: Dict[str, str]) -> None:
    action = a["action"]
    placed = _v_fragment(a.get("xml"), prefixes) if action in ("replace", "insert") else []
    for el in chosen[::-1]:
        if action == "set_attribute":
            el.attrib[a["name"]] = a["value"]
        elif action == "remove_attribute":
            el.attrib.pop(a["name"])
        elif action == "remove":
            _v_take_out(el)
        elif action == "set_text":
            el.text = a["text"]
            if el.tag in _V_KEEPS_SPACE and a["text"].strip() != a["text"]:
                el.attrib["{%s}space" % _V_XML] = "preserve"
        elif action == "insert":
            _v_place(el, placed, a["position"])
        elif action == "replace":
            _v_place(el, placed, "before")
            _v_take_out(el)
        else:                                    # unwrap
            parent = el.getparent()
            start = parent.index(el)
            for offset, child in enumerate(list(_v_source_of_children(el, a.get("keep")))):
                child.tail = None
                parent.insert(start + offset, child)
            _v_take_out(el)


def _v_shape(a: Dict[str, Any]) -> None:
    """The assertion's own fields are the ones its action uses, of the right kind; else a ValueError."""
    action = a.get("action")
    if action not in _V_ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    name = a.get("name")
    if action in ("set_attribute", "remove_attribute") and (not isinstance(name, str) or not name
                                                            or name.startswith("{}")):
        raise ValueError("the edit names no attribute")
    if action == "set_attribute" and not isinstance(a.get("value"), str):
        raise ValueError("the edit gives the attribute no value")
    if action == "set_text" and not isinstance(a.get("text"), str):
        raise ValueError("the edit gives no text")
    if action == "insert" and a.get("position") not in _V_WHERE:
        raise ValueError(f"unknown position {a.get('position')!r}")
    if action == "unwrap" and a.get("keep") is not None and not isinstance(a.get("keep"), str):
        raise ValueError("the edit's keep is not a name")


def xml_edit(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    part = a.get("part")
    if not isinstance(part, str) or part not in ctx.src.names:
        raise ValueError(f"the source has no part {part!r}")
    if part == _V_BOOKKEEPING or part.endswith(".rels"):
        raise ValueError(f"{part} is the package's own bookkeeping, which a raw XML edit never changes")
    _v_shape(a)
    count = a.get("count")
    if type(count) is not int or count < 1:
        raise ValueError("the edit states no count of elements")
    prefixes = _v_prefixes(a.get("namespaces"))
    expression = _v_judge(a.get("xpath"))
    # compared whole from here on, whatever is found: the residual would only say the same again, less exactly
    (ctx.verified_parts if part in ctx.both else ctx.verified_members).add(part)
    if part not in ctx.out.names:
        errs.append(f"xml_edit: {part} is missing from the output")
        return f"xml_edit {part}: missing"
    expected = ctx.src.xml(part)
    chosen = _v_evaluate(expected, expression, prefixes)
    what = f"xml_edit {a['action']} ×{count} in {part}"
    if len(chosen) != count:
        errs.append(f"xml_edit: the XPath selects {len(chosen)} element(s) in the source's {part}, and the plan "
                    f"states {count}: the op had to refuse")
        return what
    if _v_digest(_v_places(expected, chosen)) != a.get("places_sha256"):
        errs.append(f"xml_edit: the elements the XPath selects in {part} are not the ones the plan chose "
                    f"(their places differ)")
        return what
    refusal = _v_must_refuse(chosen, a)
    if refusal:
        errs.append(f"xml_edit: {a['action']} cannot be done in {part} ({refusal}): the op had to refuse")
        return what
    _v_edit(chosen, a, prefixes)
    difference = tree_difference(expected, ctx.out.xml(part))
    if difference:
        errs.append(f"xml_edit: {part} is not the source with exactly the stated edit: {difference}")
    return what


XML_EDIT_KINDS = {"xml_edit": xml_edit}
# The kinds only a built-in operation may assert: never offered to a custom method's writer, and refused in a custom
# method's plan (``codegen.hard_limits``) — a plan that could name ``xml_edit`` could take a part out of the text
# checks by saying so.
OP_ONLY_KINDS = frozenset(XML_EDIT_KINDS)

XML_EDIT_CONTRACT = (
    "xml_edit — one stated change to the XML of one part (the edit_xml operation, Obliterate mode only; never a "
    "custom method's). Fields: part; xpath; namespaces (prefixes beyond the built-in table); count; places_sha256 "
    "(the SHA-256 of the JSON list, without spaces, of each selected element's child-index path from the root, in "
    "document order); action (set_attribute | remove_attribute | remove | replace | insert | set_text | unwrap) with "
    "name ({namespace}local), value, text, xml, position (before | after | first_child | last_child) or keep "
    "({namespace}local, or null) as the action uses them. The verifier evaluates the XPath itself on the source "
    "part, requires exactly count elements at exactly those places and makes the edit itself. Compared: that edited "
    "copy of the source part, whole, with the output part. An edit the operation had to refuse (the root removed, nested selections removed, "
    "a missing attribute removed, text set on an element with children, an unwrap without its one keep child or "
    "over text) is a failure. The part's text is not predicted block by block: V2, V3, V4's counts and V5b leave "
    "the part to this comparison, and the record lists what the edit did to the words (D1).")
XML_EDIT_EXAMPLE = {
    "kind": "xml_edit", "part": "word/document.xml", "xpath": "//w:sdt[w:sdtPr/w:dataBinding]", "namespaces": {},
    "count": 1, "places_sha256": "<sha256 of [[0,4]]>", "action": "unwrap",
    "keep": "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sdtContent"}
