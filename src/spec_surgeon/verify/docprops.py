"""V10 kinds ``document_properties`` and ``document_state`` — built-in batch 2, items 20 and 21, verifier side
(docs/BUILTIN_OPS_BATCH2_DESIGN.md §11, §12), independent of ops/docprops.py (rule 1): this module's own property table,
its own reading of the custom properties and of the Track Changes setting.

Each kind reads the user's asks from the assertion (the values are the user's words), derives from the SOURCE which
properties and settings change (an asked value already in place changes nothing), checks every one of them in the
output — a set property: one element, exactly the value as text, the source element's attributes; a cleared one:
absent; a custom property: the source property with the new value, or a new one with the fmtid
``{D5CDD505-2E9C-101B-9397-08002B2CF9AE}``, the next pid and a ``vt:lpwstr`` value; Track Changes off: no
``w:trackRevisions`` left, on: exactly ``<w:trackRevisions/>`` — and registers each checked element in
``Ctx.member_strip``: V10's member residual compares everything else in ``settings.xml`` and the properties parts. A
custom properties part the op created is compared whole with this module's own expected part, and the package
relationships and ``[Content_Types].xml`` with the source plus exactly its one relationship and one override
(``Ctx.verified_members``). The changes it derives must be the plan's (``changed``).

``from_document`` (docs/PLAN-2026-10-22-batch-consistency.md C5): a property whose value is the user's words with
``{number}`` and ``{title}`` in place of the file's own section number and title. They are read here from the SOURCE
by this module's own copy of the reference rules (``_V_REFERENCE``) through the verifier's own fill reading
(``verify/template.fill_value``); the property must read exactly the words with them in place. Words with neither,
with one twice, or with any other brace: the op must refuse.
"""
from __future__ import annotations

import functools
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from .markup import check_content_types, check_relationships, tree_difference
from .props import Ctx, sig
from .reader import REL, W
from .template import fill_value

# A CSI spec's own section number and title, as fill rules: the verifier's own copy of the reference rules
# (tests/test_properties_from_document.py pins it equal to the transform side's).
_V_REFERENCE = {
    "number": {"rule": "regex", "source": "document", "case_insensitive": True,
               "pattern": r"^SECTION\s+(\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?)\b"},
    "title": {"rule": "first_match", "rules": [
        {"rule": "paragraph_after", "source": "document", "case_insensitive": True,
         "anchor_regex": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*$"},
        {"rule": "regex", "source": "document", "case_insensitive": True,
         "pattern": r"^SECTION\s+\d{2}[\s-]*\d{2}[\s-]*\d{2}(?:\.\d{2})?\s*[-–—:]\s*(.+)$"}]},
}
_V_PLACEHOLDER = re.compile(r"\{(number|title)\}")

_CP = "{http://schemas.openxmlformats.org/package/2006/metadata/core-properties}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_EP = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
_CU_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CU, _VT = "{%s}" % _CU_NS, "{%s}" % _VT_NS
_REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_RELS = {"core": "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
         "app": _REL_BASE + "extended-properties", "custom": _REL_BASE + "custom-properties"}
_SETTINGS_REL = _REL_BASE + "settings"
_CUSTOM_CT = "application/vnd.openxmlformats-officedocument.custom-properties+xml"
_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}"
_TEXT_VALUES = (_VT + "lpwstr", _VT + "lpstr")
_FALSY = ("0", "false", "off")
_V_PROPERTIES: Dict[str, Tuple[str, str]] = {
    "title": ("core", _DC + "title"), "subject": ("core", _DC + "subject"), "author": ("core", _DC + "creator"),
    "keywords": ("core", _CP + "keywords"), "comments": ("core", _DC + "description"),
    "category": ("core", _CP + "category"), "status": ("core", _CP + "contentStatus"),
    "last_modified_by": ("core", _CP + "lastModifiedBy"),
    "company": ("app", _EP + "Company"), "manager": ("app", _EP + "Manager"),
}


def _kids(root, tag: str) -> List[Any]:
    return [c for c in root if isinstance(c.tag, str) and c.tag == tag]


def _plain_text(el) -> Optional[str]:
    """An element's value as text — None when it holds elements (not a plain value)."""
    if el is None or any(isinstance(c.tag, str) for c in el):
        return None
    return el.text or ""


class _Check:
    """One assertion's checking: the changes found, the errors, the registrations."""

    def __init__(self, ctx: Ctx, kind: str, errs: List[str]):
        self.ctx, self.kind, self.errs = ctx, kind, errs
        self.changed: List[str] = []

    def err(self, msg: str) -> None:
        self.errs.append(f"{self.kind}: {msg}")

    def member(self, which: str) -> Optional[str]:
        return self.ctx.src.package_related(_RELS[which])

    def strip(self, member: str, key: str) -> None:
        self.ctx.member_strip.setdefault(member, set()).add(key)

    # ---- core / app ----

    def set_element(self, which: str, tag: str, value: str, label: str) -> None:
        m = self.member(which)
        if m is None:
            self.err(f"{label}: the file has no {which} properties part — the op must refuse")
            return
        if m not in self.ctx.out.names:
            self.err(f"{m} is missing from the output")
            return
        s_els = _kids(self.ctx.src.xml(m), tag)
        if len(s_els) > 1:
            self.err(f"{m} holds {len(s_els)} {etree.QName(tag).localname} — the op must refuse")
            return
        s_el = s_els[0] if s_els else None
        if s_el is not None and _plain_text(s_el) == value:
            return                                       # already as asked: the residual sees it unchanged
        self.changed.append(f"{m}|{tag}")
        self.strip(m, tag)
        o_els = _kids(self.ctx.out.xml(m), tag)
        if len(o_els) != 1:
            self.err(f"{label}: {len(o_els)} {etree.QName(tag).localname} element(s) in the output {m}, expected one")
            return
        o_el = o_els[0]
        want_attrs = dict(s_el.attrib) if s_el is not None else {}
        if _plain_text(o_el) != value:
            self.err(f"{label} reads {_plain_text(o_el)!r} in the output, the user asked {value!r}")
        if dict(o_el.attrib) != want_attrs:
            self.err(f"{label}: the element's attributes are {dict(o_el.attrib)}, expected {want_attrs}")

    def clear_element(self, which: str, tag: str, label: str) -> None:
        m = self.member(which)
        if m is None:
            self.err(f"{label}: the file has no {which} properties part — the op must refuse")
            return
        s_els = _kids(self.ctx.src.xml(m), tag)
        if not s_els or (len(s_els) == 1 and _plain_text(s_els[0]) == ""):
            return                                       # nothing to clear (absent, or an empty value): unchanged
        self.changed.append(f"{m}|{tag}")
        self.strip(m, tag)
        if m in self.ctx.out.names and _kids(self.ctx.out.xml(m), tag):
            self.err(f"{label} is still in the output {m}")

    # ---- custom ----

    def custom_sets(self, asks: Sequence[Tuple[str, str, str]]) -> None:
        """``asks``: (name, value element tag or "text", value) in the request's order."""
        m = self.member("custom")
        if m is None:
            if asks:
                self._created(asks)
            return
        s_root = self.ctx.src.xml(m)
        s_by, next_pid = {}, 1
        for p in s_root.findall(_CU + "property"):
            s_by.setdefault((p.get("name") or "").casefold(), []).append(p)
            try:
                next_pid = max(next_pid, int(p.get("pid") or "0"))
            except ValueError:
                self.err(f"{m}: a custom property has an unreadable pid — the op must refuse")
                return
        next_pid += 1
        o_root = self.ctx.out.xml(m) if m in self.ctx.out.names else None
        if o_root is None:
            self.err(f"{m} is missing from the output")
            return
        o_by: Dict[str, List[Any]] = {}
        for p in o_root.findall(_CU + "property"):
            o_by.setdefault((p.get("name") or "").casefold(), []).append(p)
        for name, vtag, value in asks:
            found = s_by.get(name.casefold(), [])
            if len(found) > 1:
                self.err(f"{m}: two custom properties named {name!r} — the op must refuse")
                continue
            if found:
                prop = found[0]
                kids = [c for c in prop if isinstance(c.tag, str)]
                ok_tags = _TEXT_VALUES if vtag == "text" else (vtag,)
                if len(kids) != 1 or kids[0].tag not in ok_tags:
                    self.err(f"custom property {name!r} does not hold {'text' if vtag == 'text' else vtag} — the op "
                             f"must refuse")
                    continue
                if (kids[0].text or "") == value:
                    continue
                expected = etree.fromstring(etree.tostring(prop))
                [c for c in expected if isinstance(c.tag, str)][0].text = value
                stored = prop.get("name")
            else:
                expected = self._new_property(name, _VT + "lpwstr" if vtag == "text" else vtag, value, next_pid)
                next_pid += 1
                stored = name
            self.changed.append(f"{m}|property:{stored}")
            self.strip(m, f"property:{stored}")
            got = o_by.get(name.casefold(), [])
            if len(got) != 1:
                self.err(f"custom property {name!r}: {len(got)} in the output, expected one")
            elif sig(got[0]) != sig(expected):
                self.err(f"custom property {name!r} is not as asked in the output: "
                         f"{etree.tostring(got[0], encoding='unicode')[:200]}")

    def custom_clears(self, names: Sequence[str]) -> None:
        m = self.member("custom")
        if m is None:
            return
        s_props = [p for p in self.ctx.src.xml(m).findall(_CU + "property")]
        o_props = [p for p in self.ctx.out.xml(m).findall(_CU + "property")] if m in self.ctx.out.names else []
        for name in names:
            found = [p for p in s_props if (p.get("name") or "").casefold() == name.casefold()]
            if not found:
                continue
            self.changed.append(f"{m}|property:{found[0].get('name')}")
            self.strip(m, f"property:{found[0].get('name')}")
            if any((p.get("name") or "").casefold() == name.casefold() for p in o_props):
                self.err(f"custom property {name!r} is still in the output")

    def _new_property(self, name: str, vtag: str, value: str, pid: int):
        prop = etree.Element(_CU + "property")
        prop.set("fmtid", _FMTID)
        prop.set("pid", str(pid))
        prop.set("name", name)
        etree.SubElement(prop, vtag).text = value
        return prop

    def _created(self, asks: Sequence[Tuple[str, str, str]]) -> None:
        """A file without a custom properties part: the op creates one holding exactly the asked properties."""
        o_member = self.ctx.out.package_related(_RELS["custom"])
        if o_member is None:
            self.err("no custom properties part in the output (the file had none: the op must create it)")
            return
        if o_member in self.ctx.src.names:
            self.err(f"{o_member} was already in the package, yet no relationship named it — the op must refuse")
            return
        expected = etree.Element(_CU + "Properties", nsmap={None: _CU_NS, "vt": _VT_NS})
        for k, (name, vtag, value) in enumerate(asks):
            expected.append(self._new_property(name, _VT + "lpwstr" if vtag == "text" else vtag, value, 2 + k))
        d = tree_difference(expected, self.ctx.out.xml(o_member))
        if d:
            self.err(f"the created {o_member} is not exactly the asked properties: {d}")
        self.ctx.verified_members.add(o_member)
        self.changed += [f"{o_member}|property:{name}" for name, _, _ in asks]
        # the package gained exactly its relationship and its content-type override
        s_ids = {r.get("Id") for r in self.ctx.src.xml("_rels/.rels").findall(REL + "Relationship")}
        new = [r for r in self.ctx.out.xml("_rels/.rels").findall(REL + "Relationship") if r.get("Id") not in s_ids]
        target = new[0].get("Target") if len(new) == 1 else None
        if target is None or target.lstrip("/") != o_member:
            self.err(f"the package relationships gained {len(new)} relationship(s); expected one naming {o_member}")
        else:
            check_relationships(self.ctx.src, self.ctx.out, "_rels/.rels", [],
                                [{"Type": _RELS["custom"], "Target": target}], self.kind, self.errs)
        check_content_types(self.ctx.src, self.ctx.out, [], {o_member: _CUSTOM_CT}, self.kind, self.errs)
        self.ctx.verified_members.update({"_rels/.rels", "[Content_Types].xml"})

    def compare_plan(self, a: Dict[str, Any]) -> None:
        if sorted(self.changed) != sorted(a.get("changed") or []):
            self.err(f"changes {sorted(self.changed)} found here, the plan predicted {sorted(a.get('changed') or [])}")


def _document_properties(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    c = _Check(ctx, "document_properties", errs)
    sets, clears = a.get("set") or {}, a.get("clear") or []
    for k, v in sets.items():
        if k not in _V_PROPERTIES:
            c.err(f"unknown property {k!r}")
            continue
        which, tag = _V_PROPERTIES[k]
        c.set_element(which, tag, v, k)
    for k in clears:
        if k not in _V_PROPERTIES:
            c.err(f"unknown property {k!r}")
            continue
        which, tag = _V_PROPERTIES[k]
        c.clear_element(which, tag, k)
    for k, text in (a.get("from_document") or {}).items():
        if k not in _V_PROPERTIES:
            c.err(f"unknown property {k!r}")
            continue
        found = _V_PLACEHOLDER.findall(text) if isinstance(text, str) else []
        if not found or len(found) != len(set(found)) or "{" in _V_PLACEHOLDER.sub("", text) \
                or "}" in _V_PLACEHOLDER.sub("", text):
            c.err(f"{k}: {text!r} needs {{number}} and/or {{title}}, each once, and no other brace — the op must refuse")
            continue
        values = {}
        for key in sorted(set(found)):
            try:
                values[key] = fill_value(ctx.src, key, _V_REFERENCE[key])
            except (ValueError, KeyError, re.error) as e:
                c.err(f"{k}: this file's section {key} cannot be read ({e}) — the op must refuse")
        if len(values) != len(set(_V_PLACEHOLDER.findall(text))):
            continue
        which, tag = _V_PROPERTIES[k]
        c.set_element(which, tag, _V_PLACEHOLDER.sub(lambda m: values[m.group(1)], text), k)
    c.custom_sets([(n, "text", v) for n, v in a.get("custom") or []])
    c.custom_clears(a.get("clear_custom") or [])
    c.compare_plan(a)
    return f"document_properties: {len(c.changed)} propert{'y' if len(c.changed) == 1 else 'ies'} changed as asked"


@functools.lru_cache(maxsize=1)
def _settings_order() -> Tuple[str, ...]:
    """CT_Settings' children in schema order, read from the bundled schema itself (never a copy of the transform's
    table): V5a cannot judge their order — its filter drops every message that lists w:hyphenationZone among the
    expected elements, which an out-of-order settings element's always does."""
    from .xsd import SCHEMA_DIR
    xs = "{http://www.w3.org/2001/XMLSchema}"
    doc = etree.parse(str(SCHEMA_DIR / "ISO-IEC29500-4_2016" / "wml.xsd"))
    ct = next(c for c in doc.getroot().iter(xs + "complexType") if c.get("name") == "CT_Settings")
    out = []
    for el in ct.find(xs + "sequence").iter(xs + "element"):
        if el.get("name"):
            out.append(W + el.get("name"))
        elif el.get("ref"):
            prefix, local = el.get("ref").split(":", 1)
            out.append("{%s}%s" % (el.nsmap[prefix], local))
    return tuple(out)


def _order_rank(el) -> int:
    order = _settings_order()
    return order.index(el.tag) if el.tag in order else len(order)     # extensions (w14:…, w15:…) rank last


def _in_order(el) -> bool:
    """An inserted element stands after every sibling that ranks before it and before every one that ranks after it."""
    rank = _order_rank(el)
    sibs = [c for c in el.getparent() if isinstance(c.tag, str)]
    k = sibs.index(el)
    return all(_order_rank(c) <= rank for c in sibs[:k]) and all(_order_rank(c) >= rank for c in sibs[k + 1:])


def _locked(settings_root) -> bool:
    for dp in settings_root.findall(W + "documentProtection"):
        if dp.get(W + "edit") == "trackedChanges" and (dp.get(W + "enforcement") or "").lower() in ("1", "true", "on"):
            return True
    return False


def _document_state(ctx: Ctx, a: Dict[str, Any], errs: List[str]) -> str:
    c = _Check(ctx, "document_state", errs)
    track, final = a.get("track_changes"), a.get("final")
    said = []
    if track is not None:
        m = ctx.src.related(_SETTINGS_REL)
        if m is None:
            c.err("the file has no settings part — the op must refuse")
        else:
            s_root = ctx.src.xml(m)
            s_els = _kids(s_root, W + "trackRevisions")
            o_els = _kids(ctx.out.xml(m), W + "trackRevisions") if m in ctx.out.names else []
            on = any(e.get(W + "val") not in _FALSY for e in s_els)
            if len(s_els) > 1:
                c.err(f"{m} holds {len(s_els)} trackRevisions — the op must refuse")
            elif track == "off" and _locked(s_root):
                c.err("Track Changes is locked with a password in this file — the op must refuse")
            elif (track == "off") == on:
                key = W + "trackRevisions"
                c.changed.append(f"{m}|{key}")
                c.strip(m, key)
                if track == "off" and o_els:
                    c.err("w:trackRevisions is still in the output settings")
                if track == "on" and (len(o_els) != 1 or o_els[0].attrib or len(o_els[0])):
                    c.err(f"the output settings hold {len(o_els)} w:trackRevisions, expected exactly "
                          f"<w:trackRevisions/>")
                elif track == "on" and not s_els and not _in_order(o_els[0]):
                    c.err("the inserted w:trackRevisions is not where CT_Settings' schema order puts it")
            said.append(f"track changes {track}")
    if final is True:
        c.set_element("core", _CP + "contentStatus", "Final", "status")
        c.custom_sets([("_MarkAsFinal", _VT + "bool", "true")])
        said.append("marked as final")
    elif final is False:
        core = c.member("core")
        if core is not None:
            els = _kids(ctx.src.xml(core), _CP + "contentStatus")
            if len(els) == 1 and _plain_text(els[0]) == "Final":
                c.clear_element("core", _CP + "contentStatus", "status")
        c.custom_clears(["_MarkAsFinal"])
        said.append("not marked as final")
    c.compare_plan(a)
    return f"document_state: {', '.join(said) or 'nothing asked'} ({len(c.changed)} change(s))"


DOCPROP_KINDS: Dict[str, Any] = {
    "document_properties": _document_properties,
    "document_state": _document_state,
}
