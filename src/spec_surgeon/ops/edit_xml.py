"""Op: edit_xml — one stated change to the XML of one part. Obliterate mode only.

The owner's decision of 2026-11-10 (CLAUDE.md, "Standing decisions"; docs/BACKLOG.md, O3): Safe and Default have no
raw-XML operation and never will. Obliterate is the emergency lever for the one or two documents the other modes could
not take, and there a document is often stopped by one thing the built-in operations refuse to guess about: a content
control bound to data, a text box Word keeps two copies of, a field that will not resolve. This op clears exactly that
thing, by name, so the plan the other documents got can then run on this one as its next step (``editspec.stage_cuts``
puts it in a step of its own: it changes its part as a whole).

It is a built-in op, so it predicts first (invariant 1). What it predicts is the edit itself, exactly:

* ``part``: one XML part of the package, by its member name. Relationships (``.rels``) and ``[Content_Types].xml`` are
  the package's own bookkeeping and are refused.
* ``xpath``: an XPath 1.0 expression that selects ELEMENTS of that part. It is evaluated on the source, with a fixed
  table of prefixes (``NAMESPACES``; ``namespaces`` adds others) and nothing else: no variables, no extension
  functions. Inside a predicate it may not start a new search of the whole part (``//``, a path from the root), and
  nowhere may it use the ``following`` or ``preceding`` axis: those are what make an expression's cost grow with the
  square of the part, and no expression needs them to say which element is meant. Past ``XPATH_TIMEOUT_S`` the file
  is refused.
* ``count``: exactly how many elements the expression selects in this document. Any other number refuses the file
  (``xml-count``, with what was found): the plan never changes more or fewer elements than the user approved.
* ``action``: one of ``ACTIONS``, applied to every selected element — set or remove an attribute; remove the element;
  replace it with, or insert beside or inside it, the elements of ``xml``; set its text; or ``unwrap`` it (its
  children take its place, or those of its one ``keep`` child: a content control's ``w:sdtContent``, an
  ``mc:AlternateContent``'s ``mc:Fallback``).

The plan carries the edit as a V10 assertion (``xml_edit``) with a digest of the places of the selected elements
(``places_digest``: each element's position among its parent's children, from the root). The verifier
selects with its own evaluation of the same expression, makes the same edit with its own code on its own copy of the
source part, and compares that with the output part whole: exactly those elements changed as stated, and nothing else
in the part did. What the edit does to the words, the tracked changes and the pages is not predicted here — nothing
could, in general, without making the edit — so the verifier lists it instead (the Obliterate record's "what changed",
``verify/difference.py``) and the user reads that list before approving. The part's text checks (V2, V3, the delta of
V4, V5b) are the whole-part comparison's for this part; the checks of the file itself (the package, revision ids, the
schema, revision grammar) run as for any copy.

Words: the ``text`` of ``set_text`` and the text inside ``xml`` are words placed in a document, so they pass the same
gate as any Obliterate wording (``compiler.placed_words``): the user's own, or shown in the reply the user reads.

Refusals: ``xml-part`` (no such part, or not one this op changes), ``xml-count`` (the expression selects another
number of elements), ``xml-action`` (the action cannot be done to a selected element: the root removed, an element
inside another selected one, text set on an element that has children …), ``xpath-timeout``; a malformed parameter is
``bad-params``.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from ..package import CT_PART, W, XmlDoctype, parse_xml
from . import prose, register
from .base import JobException, Op, PlanFragment

XPATH_TIMEOUT_S = 2.0            # seconds one evaluation may take on one part (the trust surfaces quote it)
MAX_XPATH_CHARS = 1000           # a longer expression is refused before it is compiled
MAX_FRAGMENT_CHARS = 200_000     # of the xml a replace or an insert places
SHOWN_MATCHES = 8                # selected elements a refusal's details describe

ACTIONS = ("set_attribute", "remove_attribute", "remove", "replace", "insert", "set_text", "unwrap")
POSITIONS = ("before", "after", "first_child", "last_child")
XML_NS = "http://www.w3.org/XML/1998/namespace"
# The prefixes an expression, an attribute name and the xml of a fragment may use without declaring them.
NAMESPACES: Dict[str, str] = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "v": "urn:schemas-microsoft-com:vml",
    "o": "urn:schemas-microsoft-com:office:office",
    "w10": "urn:schemas-microsoft-com:office:word",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
    "w15": "http://schemas.microsoft.com/office/word/2012/wordml",
    "wp14": "http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "wpc": "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
    "a14": "http://schemas.microsoft.com/office/drawing/2010/main",
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "ep": "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties",
    "vt": "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes",
    "ds": "http://schemas.openxmlformats.org/officeDocument/2006/customXml",
}
# Word drops a text element's leading and trailing spaces unless it says to keep them
SPACED_TEXT = tuple(W + tag for tag in ("t", "delText", "instrText", "delInstrText"))
_NAME = re.compile(r"[A-Za-z_][\w.\-]*\Z")


def _bad(reason: str, **details: Any) -> JobException:
    return JobException(reason, "bad-params", details)


def namespaces_of(params: Dict[str, Any]) -> Dict[str, str]:
    """``NAMESPACES`` with the request's own additions; a prefix that is not a name, ``xml`` or ``xmlns``, or an
    empty address, is refused."""
    extra = params.get("namespaces")
    if extra is None:
        return dict(NAMESPACES)
    if not isinstance(extra, dict):
        raise _bad("namespaces must map each prefix to its namespace address")
    out = dict(NAMESPACES)
    for prefix, uri in extra.items():
        if not isinstance(prefix, str) or not _NAME.match(prefix) or prefix.lower() in ("xml", "xmlns"):
            raise _bad(f"{prefix!r} is not a prefix a namespace can be given")
        if not isinstance(uri, str) or not uri.strip():
            raise _bad(f"the namespace of the prefix {prefix!r} must be its address")
        out[prefix] = uri
    return out


def clark(name: Any, namespaces: Dict[str, str], what: str) -> str:
    """``w:val`` as lxml names it; a name without a prefix has no namespace (an attribute's usual case outside the
    main WordprocessingML namespace)."""
    if not isinstance(name, str) or not name.strip():
        raise _bad(f"{what} must be a name such as “w:val”")
    name = name.strip()
    prefix, colon, local = name.rpartition(":")
    if not _NAME.match(local) or (colon and not _NAME.match(prefix)):
        raise _bad(f"{what} {name!r} is not an XML name")
    if not colon:
        return local
    if prefix == "xml":
        return "{%s}%s" % (XML_NS, local)
    if prefix not in namespaces:
        raise _bad(f"{what} {name!r} uses the prefix {prefix!r}, which no namespace is known for; add it to "
                   f"namespaces", prefix=prefix)
    return "{%s}%s" % (namespaces[prefix], local)


# The tokens of an XPath 1.0 expression, as far as the check below reads them. A name is taken whole (the longest
# token, as XPath reads it: “w:a-b” is one name), so a slash or a bracket inside a quoted string is never the
# expression's own.
_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>"[^"]*"|'[^']*')
  | (?P<number>\d+(?:\.\d*)?|\.\d+)
  | (?P<slash>//|/)
  | (?P<axis>::)
  | (?P<dots>\.\.|\.)
  | (?P<name>[^\W\d][\w.\-]*(?::(?:[^\W\d][\w.\-]*|\*))?)
  | (?P<operator>!=|<=|>=|[=<>+\-|])
  | (?P<star>\*)
  | (?P<mark>[\[\]()@,$])
""", re.X)
_OPERATOR_NAMES = ("and", "or", "div", "mod")
_HEAVY_AXES = ("following", "preceding")


def check_xpath(expr: Any) -> str:
    """The expression as text, or a refusal: not text, too long, one that uses the ``following`` or ``preceding``
    axis, or one that starts a path at the root of the part from inside a predicate (module docstring).

    Read token by token, by XPath's own rule for telling a name from an operator: after a value (a name test, ``.``,
    a closing bracket, a literal) ``and``/``or``/``div``/``mod`` and ``*`` are operators, anywhere else they are
    names. A slash continues a path only after a value; after anything else it starts one at the root. The check
    keeps the usual accidental square-of-the-part expressions out; the deadline, not this, is what bounds a wait."""
    if not isinstance(expr, str) or not expr.strip():
        raise _bad("xpath must be an XPath expression that selects elements")
    expr = expr.strip()
    if len(expr) > MAX_XPATH_CHARS:
        raise _bad(f"the XPath is {len(expr):,} characters long; at most {MAX_XPATH_CHARS:,} are taken",
                   limit=MAX_XPATH_CHARS)
    depth, at, value, name = 0, 0, False, ""    # ``value``: the token before this one is something a path continues
    while at < len(expr):
        token = _TOKEN.match(expr, at)
        if token is None:
            if expr[at] in "\"'":
                raise _bad("the XPath has a quote that is never closed")
            raise _bad(f"the XPath holds {expr[at]!r}, which no XPath this operation takes does", xpath=expr)
        kind, text, at = token.lastgroup, token.group(), token.end()
        if kind == "space":
            continue
        if kind == "slash":
            if depth and not value:
                raise _bad("inside [ ] the XPath may not search the whole part again (a path that starts with “/” "
                           "or “//”): test the element itself, its attributes, what it holds (“.//w:t”) or its "
                           "parents")
            value = False
        elif kind == "axis":
            if name in _HEAVY_AXES:
                raise _bad(f"the XPath may not use the {name} axis (it reads the rest of the part for every element "
                           f"it starts from); following-sibling and preceding-sibling are taken")
            value = False
        elif kind == "name":
            operator = value and text in _OPERATOR_NAMES
            value, name = not operator, text
            continue
        elif kind == "star":
            value = not value                   # a multiplication after a value; any element anywhere else
        elif kind == "mark":
            if text == "[":
                depth += 1
            elif text == "]":
                depth -= 1
                if depth < 0:
                    raise _bad("the XPath closes a bracket it never opened")
            value = text in "])"
        else:                                    # a string, a number, “.”, “..”: values; an operator: not one
            value = kind != "operator"
        name = ""
    if depth:
        raise _bad("the XPath opens a bracket it never closes")
    return expr


def select_elements(root: etree._Element, expr: str, namespaces: Dict[str, str], part: str) -> List[etree._Element]:
    """The elements ``expr`` selects in ``root``, in document order, each once. A syntax or evaluation error, or a
    result that is not elements of this part, is ``bad-params``; past the deadline, ``xpath-timeout``."""
    try:
        compiled = etree.XPath(expr, namespaces=namespaces, regexp=False, smart_strings=False)
    except (etree.XPathSyntaxError, etree.XPathEvalError, TypeError, ValueError) as error:
        raise _bad(f"the XPath {expr!r} is not one that can be evaluated: {error}", xpath=expr) from None
    box: Dict[str, Any] = {}

    def evaluate() -> None:
        try:
            box["value"] = compiled(root)
        except Exception as error:  # noqa: BLE001 — carried to the caller below
            box["error"] = error
    # lxml evaluates without holding the interpreter, so the wait below can end before the evaluation does; the
    # expression check above keeps what an abandoned evaluation still has to do small
    worker = threading.Thread(target=evaluate, name="edit-xml-xpath", daemon=True)
    worker.start()
    worker.join(XPATH_TIMEOUT_S)
    if worker.is_alive():
        raise JobException(f"the XPath {expr!r} took too long on {part}; simplify it", "xpath-timeout",
                           {"xpath": expr, "part": part, "timeout_s": XPATH_TIMEOUT_S})
    if "error" in box:
        raise _bad(f"the XPath {expr!r} could not be evaluated on {part}: {box['error']}", xpath=expr, part=part)
    value = box.get("value")
    if not isinstance(value, list) or any(not isinstance(x, etree._Element) or not isinstance(x.tag, str)
                                          for x in value):
        raise _bad(f"the XPath {expr!r} must select elements (not attributes, text, a number or a yes/no)",
                   xpath=expr, part=part)
    seen, out = set(), []
    for el in value:                             # XPath gives a node-set in document order
        if id(el) not in seen:
            seen.add(id(el))
            out.append(el)
    return out


def paths_of(root: etree._Element, targets: Sequence[etree._Element]) -> List[List[int]]:
    """Each target's place in ``root``'s tree: its position among its parent's children, at every level from the root
    (comments and processing instructions count, as they do in ``list(parent)``). One walk of the tree however many
    targets there are; the caller holds the targets, so each is the same object the walk meets."""
    wanted = {id(el) for el in targets}
    found: Dict[int, List[int]] = {id(root): []} if id(root) in wanted else {}
    stack: List[Tuple[etree._Element, List[int]]] = [(root, [])]
    while stack and len(found) < len(wanted):
        el, path = stack.pop()
        for k, child in enumerate(el):
            if id(child) in wanted:
                found[id(child)] = path + [k]
            if isinstance(child.tag, str) and len(child):
                stack.append((child, path + [k]))
    return [found[id(el)] for el in targets]


def places_digest(paths: Sequence[Sequence[int]]) -> str:
    """The places as one value the plan and the verifier each compute (``xml_edit``'s ``places_sha256``)."""
    return hashlib.sha256(json.dumps([list(p) for p in paths], separators=(",", ":")).encode("ascii")).hexdigest()


def at_paths(root: etree._Element, paths: Sequence[Sequence[int]]) -> List[Optional[etree._Element]]:
    """The node at each of ``paths`` (``paths_of``), None where the tree has no such place. One walk for all of
    them: each parent's children are read once, however many of them are asked for."""
    trie: Dict[Any, Any] = {}
    for n, path in enumerate(paths):
        node = trie
        for i in path:
            node = node.setdefault(i, {})
        node[None] = n                           # a path ends here
    out: List[Optional[etree._Element]] = [None] * len(paths)
    if None in trie:
        out[trie[None]] = root
    stack = [(root, trie)]
    while stack:
        el, node = stack.pop()
        if not isinstance(el.tag, str) or not any(k is not None for k in node):
            continue
        for k, child in enumerate(el):
            below = node.get(k)
            if below is None:
                continue
            if None in below:
                out[below[None]] = child
            stack.append((child, below))
    return out


def _tidy(el: etree._Element) -> None:
    """A fragment written with line breaks and indentation between its elements places none of that: white space
    between elements goes; the text of an element that holds no other element stays exactly as written."""
    for node in el.iter():
        if not isinstance(node.tag, str):
            continue
        if len(node) and not (node.text or "").strip():
            node.text = None
        for child in node:
            if not (child.tail or "").strip():
                child.tail = None


def parse_fragment(xml: Any, namespaces: Dict[str, str]) -> List[etree._Element]:
    """The elements of ``xml``: one or more, well formed, with the known prefixes; no text outside an element."""
    if not isinstance(xml, str) or not xml.strip():
        raise _bad("xml must be the element(s) to place, as XML")
    if len(xml) > MAX_FRAGMENT_CHARS:
        raise _bad(f"xml is {len(xml):,} characters long; at most {MAX_FRAGMENT_CHARS:,} are taken",
                   limit=MAX_FRAGMENT_CHARS)
    declared = " ".join(f'xmlns:{prefix}="{uri.replace("&", "&amp;").replace(chr(34), "&quot;")}"'
                        for prefix, uri in sorted(namespaces.items()))
    try:
        holder = parse_xml(f"<fragment {declared}>{xml}</fragment>".encode("utf-8"), "the xml of edit_xml")
    except (etree.XMLSyntaxError, XmlDoctype, ValueError) as error:
        raise _bad(f"xml is not well-formed XML: {error}") from None
    if (holder.text or "").strip() or any((c.tail or "").strip() for c in holder):
        raise _bad("xml has text outside an element; text is placed with set_text, or inside an element such as w:t")
    if any(not isinstance(c.tag, str) for c in holder):
        raise _bad("xml may hold elements only (no comment or processing instruction beside them)")
    kids = list(holder)
    if not kids:
        raise _bad("xml holds no element")
    for kid in kids:
        kid.tail = None
        _tidy(kid)
    return kids


def fragment_text(xml: Any, namespaces: Optional[Dict[str, str]] = None) -> List[str]:
    """The text a fragment places, each element's own (``compiler.placed_words``); [] when it does not parse (the
    plan refuses it)."""
    try:
        kids = parse_fragment(xml, namespaces or dict(NAMESPACES))
    except JobException:
        return []
    return [node.text for kid in kids for node in kid.iter()
            if isinstance(node.tag, str) and not len(node) and (node.text or "").strip()]


def _own_text(el: etree._Element) -> bool:
    """Whether the element holds text of its own, between its children or before them."""
    return bool((el.text or "").strip() or any((c.tail or "").strip() for c in el))


def _kept(el: etree._Element, keep: Optional[str]) -> Optional[etree._Element]:
    """The element whose children take ``el``'s place when it is unwrapped: ``el`` itself, or its one ``keep``
    child; None when it has none, or more than one."""
    if keep is None:
        return el
    found = [c for c in el if c.tag == keep]
    return found[0] if len(found) == 1 else None


def _name_of(el: etree._Element, namespaces: Dict[str, str]) -> str:
    """The element as a reader of XML names it: ``w:sdt``, with the prefix of the table where it has one."""
    q = etree.QName(el)
    for prefix, uri in namespaces.items():
        if uri == q.namespace:
            return f"{prefix}:{q.localname}"
    return q.localname


def _detach(el: etree._Element) -> None:
    """``el`` out of its parent, the text that followed it kept where it was."""
    parent = el.getparent()
    if el.tail:
        before = el.getprevious()
        if before is not None:
            before.tail = (before.tail or "") + el.tail
        else:
            parent.text = (parent.text or "") + el.tail
    parent.remove(el)


def _put(target: etree._Element, new: Sequence[etree._Element], position: str) -> None:
    """Copies of ``new`` placed at ``position`` of ``target``, in their order."""
    copies = [copy.deepcopy(n) for n in new]
    if position == "first_child":
        for k, node in enumerate(copies):        # (the element's own leading text stays before what it now holds)
            target.insert(k, node)
    elif position == "last_child":
        for node in copies:
            target.append(node)
    else:
        parent = target.getparent()
        at = parent.index(target) + (1 if position == "after" else 0)
        if position == "after" and target.tail:
            copies[-1].tail, target.tail = target.tail, None
        for k, node in enumerate(copies):
            parent.insert(at + k, node)


def check_action(params: Dict[str, Any], namespaces: Dict[str, str]) -> Dict[str, Any]:
    """The action as the transform and the assertion carry it: its name and exactly the values it uses, names
    resolved. A value the action does not use, or one it lacks, is ``bad-params``."""
    action = params.get("action")
    if action not in ACTIONS:
        raise _bad(f"action must be one of {list(ACTIONS)}, got {action!r}")
    uses = {"set_attribute": ("name", "value"), "remove_attribute": ("name",), "remove": (), "set_text": ("text",),
            "replace": ("xml",), "insert": ("xml", "position"), "unwrap": ("keep",)}[action]
    stray = sorted(k for k in ("name", "value", "xml", "position", "text", "keep") if k in params and k not in uses)
    if stray:
        raise _bad(f"{action} does not use {stray}; it uses {list(uses) or 'no other value'}")
    out: Dict[str, Any] = {"action": action}
    if "name" in uses:
        out["name"] = clark(params.get("name"), namespaces, "name")
    if action == "set_attribute":
        if not isinstance(params.get("value"), str):
            raise _bad("value must be the attribute's new value, as text")
        out["value"] = params["value"]
    if action == "set_text":
        if not isinstance(params.get("text"), str):
            raise _bad("text must be the element's new text")
        out["text"] = params["text"]
    if "xml" in uses:
        parse_fragment(params.get("xml"), namespaces)
        out["xml"] = params["xml"]
    if action == "insert":
        if params.get("position") not in POSITIONS:
            raise _bad(f"position must be one of {list(POSITIONS)}, got {params.get('position')!r}")
        out["position"] = params["position"]
    if action == "unwrap":
        out["keep"] = None if params.get("keep") is None else clark(params["keep"], namespaces, "keep")
    return out


def check_targets(targets: Sequence[etree._Element], action: Dict[str, Any], part: str,
                  namespaces: Dict[str, str]) -> None:
    """Every selected element can take the action, and the edit changes at least one of them; else ``xml-action``
    naming the first that cannot (or, for an edit that changes nothing, the first selected)."""
    def refuse(el: etree._Element, why: str) -> JobException:
        return JobException(f"{action['action']} cannot be done to the <{_name_of(el, namespaces)}> the XPath selects "
                            f"in {part}: {why}", "xml-action",
                            {"part": part, "action": action["action"], "element": _name_of(el, namespaces),
                             "place": paths_of(el.getroottree().getroot(), [el])[0], "why": why})
    kind = action["action"]
    outside = kind in ("remove", "replace", "unwrap") or (kind == "insert" and action["position"] in ("before", "after"))
    chosen = {id(el) for el in targets}
    for el in targets:
        if outside and el.getparent() is None:
            raise refuse(el, "it is the part's root element")
        if kind in ("remove", "replace", "unwrap"):
            up = el.getparent()
            while up is not None:
                if id(up) in chosen:
                    raise refuse(el, "it is inside another selected element; select the outer ones or the inner "
                                     "ones, not both")
                up = up.getparent()
        if kind == "remove_attribute" and action["name"] not in el.attrib:
            raise refuse(el, "it has no such attribute")
        if kind == "set_text" and len(el):
            raise refuse(el, "it holds other elements; text is set on an element that holds text only")
        if kind == "unwrap":
            kept = _kept(el, action["keep"])
            if kept is None:
                raise refuse(el, "it does not have exactly one child of the kind to keep")
            if _own_text(kept) or (kept is not el and _own_text(el)):
                raise refuse(el, "it holds text of its own, which unwrapping would leave where no text may stand")
    # an edit that would leave every selected element as it is is a mistake about which elements were meant
    first = targets[0]
    if kind == "set_attribute" and all(el.get(action["name"]) == action["value"] for el in targets):
        raise refuse(first, "every selected element already has that value: the edit would change nothing")
    if kind == "set_text" and all((el.text or "") == action["text"] for el in targets):
        raise refuse(first, "every selected element already holds that text: the edit would change nothing")


def apply_action(targets: Sequence[etree._Element], action: Dict[str, Any], namespaces: Dict[str, str]) -> None:
    """The action done to every target, last first (an earlier element's place never moves under a later one)."""
    kind = action["action"]
    new = parse_fragment(action["xml"], namespaces) if kind in ("replace", "insert") else []
    for el in reversed(list(targets)):
        if kind == "set_attribute":
            el.set(action["name"], action["value"])
        elif kind == "remove_attribute":
            del el.attrib[action["name"]]
        elif kind == "remove":
            _detach(el)
        elif kind == "set_text":
            el.text = action["text"]
            if el.tag in SPACED_TEXT and action["text"] != action["text"].strip():
                el.set("{%s}space" % XML_NS, "preserve")
        elif kind == "insert":
            _put(el, new, action["position"])
        elif kind == "replace":
            _put(el, new, "before")
            _detach(el)
        elif kind == "unwrap":
            parent = el.getparent()
            at = parent.index(el)
            for k, child in enumerate(list(_kept(el, action["keep"]))):
                child.tail = None
                parent.insert(at + k, child)
            _detach(el)


def _describe_match(el: etree._Element, namespaces: Dict[str, str]) -> str:
    """One selected element for a refusal's details: its name, its attributes and the start of its text."""
    attrs = " ".join(f'{etree.QName(k).localname}="{v[:40]}"' for k, v in list(el.attrib.items())[:4])
    text = " ".join("".join(el.itertext()).split())
    return (f"<{_name_of(el, namespaces)}{' ' + attrs if attrs else ''}>"
            + (f" “{text[:60]}{'…' if len(text) > 60 else ''}”" if text else ""))


ACTION_WORDS = {"set_attribute": "set an attribute on", "remove_attribute": "remove an attribute from",
                "remove": "remove", "replace": "replace", "insert": "insert XML at", "set_text": "set the text of",
                "unwrap": "unwrap"}


@register
class EditXml(Op):
    name = "edit_xml"
    mode = "untracked"
    obliterate_only = True
    description = (
        "OBLITERATE MODE ONLY. One stated change to the raw XML of one part: the elements an XPath selects — exactly "
        "`count` of them, or the file is refused — get one action: set_attribute {name, value}, remove_attribute "
        "{name}, remove, replace {xml}, insert {xml, position: before|after|first_child|last_child}, set_text {text} "
        "(an element that holds text only), or unwrap {keep?} (the element's children take its place; with keep, the "
        "children of its one child of that name: w:sdtContent of a content control, mc:Fallback or mc:Choice of an "
        "mc:AlternateContent). The verifier makes the same edit itself and compares the part whole, so exactly those "
        "elements change and nothing else in the part; what the edit does to the words, tracked changes and pages is "
        "listed for the user from the copy, not predicted. Use it to clear the one thing that stopped a document "
        "(read the XML first: read_xml_page, run_analysis), and give the plan that follows as the NEXT STAGE of the "
        "submission (stages, never one list of ops: every op of one spec is planned on the document as it is, so "
        "the plan would be refused again). An edit that would leave every selected element as it already is is "
        "refused. Never tracked. Text placed by set_text or inside xml is wording: it must be the user's or in the "
        "reply the user reads.")
    params_schema = {"type": "object", "required": ["part", "xpath", "count", "action"], "properties": {
        "part": {"type": "string", "description": "the package member to change, as list_parts names it "
                                                  "(word/document.xml, word/header2.xml, word/styles.xml …); not a "
                                                  ".rels member and not [Content_Types].xml"},
        "xpath": {"type": "string", "description": "XPath 1.0 selecting the ELEMENTS to change, evaluated on the "
                                                   "part. Prefixes known without declaring them: "
                                                   + ", ".join(sorted(NAMESPACES)) + " (and xml). Inside [ ] test "
                                                   "the element, its attributes, children and parents (.//w:t, "
                                                   "../w:tbl); a path that starts with “/” or “//” is refused there, "
                                                   "and the following and preceding axes anywhere (the -sibling "
                                                   "ones are taken). At most " + f"{MAX_XPATH_CHARS:,}"
                                                   + " characters"},
        "count": {"type": "integer", "description": "exactly how many elements the XPath selects in this document "
                                                    "(at least 1); another number refuses the file and says how "
                                                    "many it found"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "name": {"type": "string", "description": "set_attribute, remove_attribute: the attribute, e.g. w:val (no "
                                                  "prefix: an attribute without a namespace)"},
        "value": {"type": "string", "description": "set_attribute: the new value"},
        "xml": {"type": "string", "description": "replace, insert: one or more elements, well formed, written with "
                                                 "the known prefixes (no declarations needed)"},
        "position": {"type": "string", "enum": list(POSITIONS), "description": "insert: where, relative to each "
                                                                               "selected element"},
        "text": {"type": "string", "description": "set_text: the element's new text, exactly"},
        "keep": {"type": "string", "description": "unwrap: the one child whose children take the element's place, "
                                                  "e.g. w:sdtContent; omit to keep the element's own children"},
        "namespaces": {"type": "object", "description": "prefixes beyond the known ones: {prefix: namespace address}"},
        "tracked": {"type": "boolean", "enum": [False], "description": "not tracked (only false is accepted)"}}}
    select_schema: Dict[str, Any] = {}
    verification_profile = {
        "V1": "the one part, nothing else; no new dangling relationship, reference or untyped member",
        "V10": "xml_edit: the verifier selects with its own evaluation of the XPath (the count and the places must "
               "be the plan's), makes the same edit on its own copy of the source part and compares it with the "
               "output part whole",
        "V2": "the part's words are not predicted: they are listed from the copy (D1, what changed) for the user",
        "V4": "revision ids unique and readable; the part's count of tracked changes is listed, not predicted",
        "V5": "no new schema error, no revision markup outside Word's grammar",
        "V7": "none — a raw XML edit can move or change what the pages show"}

    def describe(self, select: Optional[Dict[str, Any]], params: Dict[str, Any]) -> str:
        params = dict(params or {})

        def shown(value: Any, most: int = 160) -> str:
            """A value quoted for a plan line: whole when short, else its start and how long it is."""
            text = "" if value is None else str(value)
            return prose.quote(text) if len(text) <= most else f"{prose.quote(text[:most] + '…')} ({len(text):,} characters)"
        part, xpath, count = params.get("part"), params.get("xpath"), params.get("count")
        action = params.get("action")
        n = count if type(count) is int else 0
        which = (f"the {prose.count(n, 'element')} " if n else "the elements ") + (
            f"that the XPath {shown(xpath)} matches" if isinstance(xpath, str) and xpath else "it matches")
        where = f"in the part {prose.quote(part)}" if isinstance(part, str) and part else "in one part"
        if action == "set_attribute":
            did = f"set the attribute {shown(params.get('name'))} to {shown(params.get('value'))} on {which}"
        elif action == "remove_attribute":
            did = f"remove the attribute {shown(params.get('name'))} from {which}"
        elif action == "remove":
            did = f"remove {which}, with everything inside them"
        elif action == "replace":
            did = f"replace {which} with the XML {shown(params.get('xml'))}"
        elif action == "insert":
            place = {"before": "before", "after": "after", "first_child": "at the start of",
                     "last_child": "at the end of"}.get(params.get("position"), "at")
            did = f"insert the XML {shown(params.get('xml'))} {place} {which}"
        elif action == "set_text":
            text = str(params.get("text", ""))
            did = f"set the text of {which} to {prose.typed(text) if len(text) <= 160 else shown(text)}"
        elif action == "unwrap":
            keep = params.get("keep")
            did = (f"unwrap {which}: what each holds inside its {shown(keep)} takes its place" if keep else
                   f"unwrap {which}: what each holds takes its place")
        else:
            did = f"change {which}"
        return (f"Raw XML edit {where}: {did}. Not tracked; exactly these elements change and nothing else in the "
                f"part.")

    def plan(self, pkg, select: Dict[str, Any], params: Dict[str, Any]) -> PlanFragment:
        if select:
            raise _bad("edit_xml takes no select: the part and the XPath say where")
        if params.get("tracked") not in (None, False):
            raise _bad("a raw XML edit is never tracked (Word tracks what it changes itself)")
        part, count = params.get("part"), params.get("count")
        if not isinstance(part, str) or not part.strip():
            raise _bad("part must be the member to change, as list_parts names it")
        if type(count) is not int or count < 1:
            raise _bad("count must be how many elements the XPath selects: a whole number, at least 1")
        namespaces = namespaces_of(params)
        expr = check_xpath(params.get("xpath"))
        action = check_action(params, namespaces)
        part = part.strip()
        if part == CT_PART or part.endswith(".rels"):
            raise JobException(f"{part} is the package's own bookkeeping (its relationships and content types), which "
                               f"a raw XML edit does not change", "xml-part", {"part": part, "why": "bookkeeping"})
        if not pkg.has(part):
            raise JobException(f"this document has no part named {part}", "xml-part",
                               {"part": part, "why": "missing", "parts": [n for n in pkg.member_names
                                                                           if n.endswith(".xml")][:40]})
        try:
            root = pkg.xml(part)
        except (etree.XMLSyntaxError, XmlDoctype, ValueError) as error:
            raise JobException(f"{part} is not XML this op can read: {error}", "xml-part",
                               {"part": part, "why": "not-xml"}) from None
        targets = select_elements(root, expr, namespaces, part)
        if len(targets) != count:
            found = [_describe_match(el, namespaces) for el in targets[:SHOWN_MATCHES]]
            raise JobException(
                f"the XPath {expr!r} selects {len(targets)} element(s) in {part}, not the {count} the plan states; "
                f"nothing is changed unless the number is exact", "xml-count",
                {"part": part, "xpath": expr, "expected": count, "found": len(targets), "matches": found})
        check_targets(targets, action, part, namespaces)
        paths = paths_of(root, targets)
        frag = self.fragment({}, params, mode="untracked")
        frag.intended_parts = [part]
        frag.untracked_diffs_expected = True
        frag.touches.append((part, "*", "xml"))                    # the part as a whole: nothing else in its step
        frag.geometry_reference = "none"
        frag.geometry_reason = "a raw XML edit can move or change what the pages show"
        assertion = {"kind": "xml_edit", "part": part, "xpath": expr, "count": count,
                     "places_sha256": places_digest(paths),
                     "namespaces": {k: v for k, v in namespaces.items() if NAMESPACES.get(k) != v}, **action}
        frag.property_assertions.append(assertion)
        frag.notes.append(f"raw XML edit of {part}: {ACTION_WORDS[action['action']]} {count} element(s); the words "
                          f"it adds and removes are listed from the copy, not predicted")
        frag.transform_data = {"part": part, "paths": paths, "action": action, "namespaces": namespaces}
        return frag

    def apply(self, pkg, frag: PlanFragment) -> None:
        td = frag.transform_data
        root = pkg.xml(td["part"])
        targets = at_paths(root, td["paths"])
        if any(el is None or not isinstance(el.tag, str) for el in targets):
            raise JobException(f"{td['part']} is no longer the part the edit was planned on", "structure",
                               {"part": td["part"]})
        apply_action(targets, td["action"], td["namespaces"])
        pkg.write_xml(td["part"], root)
