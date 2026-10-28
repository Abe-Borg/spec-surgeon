"""V7's template reference, verifier side (independent of ops/, CLAUDE.md invariant 2): the template's headers and
footers as the approved plan makes this document show them.

V7 template mode compares the copy's headers/footers, rendered alone, with a reference rendered alone
(verify/geometry.py: ``hf_only_copy``, ``compare_template_bands``). Compared with the template as it stands, a fill
that is shorter or longer than its placeholder lets a word after it change lines, and that word reads as moved by a
line height (live QA finding F08: "21 13 13" in place of ``{{SECTION_NUMBER}}`` let a hyphen fit on the header's first
line, an 11.50 pt "shift" that held a correct copy); so does a header line laid on a narrower or wider page. The
reference is therefore the template package with:

* a body of one section per (template section, text width) the output uses — output section k ↔ the template section
  ``template_sections`` maps it to (``geometry.template_section_map``) —, in the template's section order (each
  template section shows every header/footer an earlier one shows, so the reference's sections inherit nothing the
  template's do not), each a copy of that template section's own ``w:sectPr``: header/footer distances, titlePg, page
  height and top/bottom margins as the template has them (V7 still holds the copy to the template's vertical layout),
  the page width and side margins of the output section. A plan that leaves the distances as the document has them
  (the approved ``align_header_footer_distance: false``) is held to the document's instead: each reference section
  takes its header and footer distances from the SOURCE's section (read here, never from the plan or the copy), one
  reference section per distinct pair (the deep-dive stress test's D4: a request to keep each document's distances
  could never pass V7, and recovery spent its calls repairing correct copies) — a copy whose distances moved anyway
  is not where the reference's header is;
* for each of them, references to every header/footer of the templated kinds that template section shows
  (``template._refs_shown``), each to a copy of the template's part made here for that width:

  - every slot (``template.slots_of``, by the plan's ``slot_markers``) shows the fill the plan predicts,
    ``expected_fills`` — which V10's ``template_parts`` re-derives from the source and holds the copy to — in a run of
    its own where the slot starts, with that run's properties (a highlight slot's highlight gone), in capitals by
    formatting (``w:caps``) when ``caps_fills`` names it; the slot's pieces in later runs go;
  - outside tables, each top-level paragraph's effective tab stops rescaled from the template section's text width to
    the output section's by the plan's declared rule (``rescale_tabs``; ``template.rescaled``), written as the
    paragraph's own stops (a stop its styles set and the rescaled list lacks is cleared);
  - nothing else: colour and shading move no word, and the properties the op pins resolve in the copy as they do in
    the template (V10's ``template_parts`` proves that).

Every other member is the template's, byte for byte. A template whose mapped sections show none of the templated kinds
gives no part to copy (the plan has no ``template_parts``): the reference is then that template's blank regions. A
plan that names no template, a template file that changed since planning (the md5 a copied part records), a slot
this reading cannot place, or a plan that keeps the document's distances without the source to read them from (or
whose source has another number of sections than the copy) raises ``ValueError``: V7 then fails for want of its
reference and says so (``batch.geometry_check``)."""
from __future__ import annotations

import copy
import hashlib
import io
import posixpath
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from lxml import etree

from .props import sect_prs
from .reader import CT, R, REL, VPackage, W
from .template import _owner_p, _refs_shown, _run_display, _runs_of, _v_field_runs, _width, Eff, rescaled, slots_of

_REL_TYPE = {"header": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/header",
             "footer": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer"}
_PPR_BEFORE_TABS = ("pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
                    "suppressLineNumbers", "pBdr", "shd")
_RPR_BEFORE_CAPS = ("rStyle", "rFonts", "b", "bCs", "i", "iCs")


def _insert_after(parent, el, before: Sequence[str]) -> None:
    """``el`` into ``parent`` after its last child named in ``before`` (schema order), else first."""
    at = 0
    for k, c in enumerate(parent):
        if isinstance(c.tag, str) and c.tag.startswith(W) and c.tag[len(W):] in before:
            at = k + 1
    parent.insert(at, el)


def _text_run(rpr, text: str, caps: bool):
    r = etree.Element(W + "r")
    if rpr is not None or caps:
        rp = copy.deepcopy(rpr) if rpr is not None else etree.Element(W + "rPr")
        if caps:
            for old in rp.findall(W + "caps"):
                rp.remove(old)
            _insert_after(rp, etree.Element(W + "caps"), _RPR_BEFORE_CAPS)
        r.append(rp)
    t = etree.SubElement(r, W + "t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    return r


def _fill_paragraph(p, slots: List[Dict[str, Any]], fills: Dict[str, str], caps: Sequence[str]) -> None:
    """The paragraph as the copy shows it: each run a slot touches is cut at the slots' edges — each stretch of its
    other content stays in a run of its own with the run's properties, in order — and each slot becomes one run
    holding only its fill, where the slot starts."""
    pos = 0
    for r in _runs_of(p):
        t = _run_display(r, p)
        a, b = pos, pos + len(t)
        pos = b
        hit = [s for s in slots if s["start"] < b and a < s["end"]] if t else []
        if not hit:
            continue
        rpr = r.find(W + "rPr")
        groups: List[List[Any]] = []                 # [slot or None, [children]]
        off = a
        for c in list(r):
            if c.tag == W + "rPr":
                continue
            r.remove(c)
            if c.tag == W + "t":
                txt = c.text or ""
                ca, cb = off, off + len(txt)
                off = cb
                cuts = sorted({ca, cb} | {x for s in hit for x in (s["start"], s["end"]) if ca < x < cb})
                pieces = [(next((s for s in hit if s["start"] <= x and y <= s["end"]), None), txt[x - ca:y - ca])
                          for x, y in zip(cuts, cuts[1:])]
            else:
                pieces = [(None, c)]
            for lab, item in pieces:
                if not groups or groups[-1][0] is not lab:
                    groups.append([lab, []])
                groups[-1][1].append(item)
        out = []
        for lab, items in groups:
            if lab is None:
                run = etree.Element(W + "r")
                if rpr is not None:
                    run.append(copy.deepcopy(rpr))
                for item in items:
                    if isinstance(item, str):
                        last = run[-1] if len(run) else None
                        if last is not None and last.tag == W + "t":
                            last.text = (last.text or "") + item
                        else:
                            te = etree.SubElement(run, W + "t")
                            te.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                            te.text = item
                    else:
                        run.append(item)
                out.append(run)
            elif lab["start"] >= a:
                fill_rpr = rpr
                if lab["highlight"] and rpr is not None and rpr.find(W + "highlight") is not None:
                    fill_rpr = copy.deepcopy(rpr)
                    for h in fill_rpr.findall(W + "highlight"):
                        fill_rpr.remove(h)
                out.append(_text_run(fill_rpr, fills[lab["name"]], lab["name"] in caps))
        parent, at = r.getparent(), r.getparent().index(r)
        parent.remove(r)
        for k, run in enumerate(out):
            parent.insert(at + k, run)


def _set_tabs(p, want, base) -> None:
    """``want`` as the paragraph's own stops: its old ones go, a stop of its styles' (``base``) at a position ``want``
    does not use is cleared."""
    ppr = p.find(W + "pPr")
    if ppr is None:
        ppr = etree.Element(W + "pPr")
        p.insert(0, ppr)
    for old in ppr.findall(W + "tabs"):
        ppr.remove(old)
    used = {pos for pos, _, _ in want}
    stops = [(pos, "clear", None) for pos, _, _ in base if pos not in used] + list(want)
    if not stops:
        return
    tabs = etree.Element(W + "tabs")
    for pos, kind, leader in sorted(stops, key=lambda s: s[0]):
        tab = etree.SubElement(tabs, W + "tab")
        tab.set(W + "val", kind)
        if leader:
            tab.set(W + "leader", leader)
        tab.set(W + "pos", str(pos))
    _insert_after(ppr, tabs, _PPR_BEFORE_TABS)


def _part_copy(tpl: VPackage, eff: Eff, part: str, a: Optional[Dict[str, Any]], w_from: Optional[int],
               w_to: Optional[int]) -> bytes:
    """The template part ``part`` filled by the plan's assertion ``a`` (None: no slot to fill) and its tabs rescaled
    ``w_from`` → ``w_to``."""
    root = tpl.xml(part)
    if a is not None:
        fills = a.get("expected_fills") or {}
        markers = a.get("slot_markers", ["highlight", "braces"])
        colors = a.get("highlight_colors", ["yellow"])
        ref_values = a.get("template_values") if "reference" in markers else None
        in_field = _v_field_runs(root)
        found = []
        for k, p in enumerate(root.iter(W + "p")):
            sl = slots_of(p, markers, colors, ref_values, in_field)
            if not sl:
                continue
            if _owner_p(p) is not None:
                raise ValueError(f"{part} block {k}: a slot inside a text box paragraph")
            missing = sorted({s["name"] for s in sl} - set(fills))
            if missing:
                raise ValueError(f"{part} block {k}: no fill for slot(s) {missing}")
            found.append((p, sl))
        if a.get("rescale_tabs", True) and w_from and w_to and w_from != w_to:
            for p in root.iter(W + "p"):
                if _owner_p(p) is None and not any(q.tag == W + "tbl" for q in p.iterancestors()):
                    _set_tabs(p, rescaled(eff.para(p)["tabs"], w_from, w_to), eff.para(p, with_direct=False)["tabs"])
        for p, sl in found:
            _fill_paragraph(p, sl, fills, a.get("caps_fills") or [])
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def keeps_distances(plan: Dict[str, Any]) -> bool:
    """Whether the approved templating op leaves each section's header and footer distances as the document has them
    (its params' ``align_header_footer_distance`` is false): the user's approved spec, as the plan carries it."""
    for o in plan.get("ops", []) or []:
        if o.get("op") == "apply_header_footer_template" and isinstance(o.get("params"), dict):
            return o["params"].get("align_header_footer_distance", True) is False
    return False


def _distances(sp) -> Tuple[Optional[str], Optional[str]]:
    """A section's header and footer distances as written (None where it states none: Word's default)."""
    mar = sp.find(W + "pgMar")
    return (None, None) if mar is None else (mar.get(W + "header"), mar.get(W + "footer"))


def template_reference(plan: Dict[str, Any], output: bytes, section_map: Sequence[int],
                       kinds: Sequence[str], source: Optional[bytes] = None) -> Tuple[bytes, List[int]]:
    """The reference document (see the module docstring) for an output whose sections ``section_map`` maps to the
    template's (``geometry.template_section_map`` of the op's ``template_sections``), with the templated ``kinds``,
    and the map of the output's sections to the reference's: ``(bytes, [reference section of output section k])``.
    ``source``: the document the plan was made from, whose header/footer distances the reference takes when the plan
    keeps them (``keeps_distances``)."""
    parts = [a for a in plan.get("property_assertions", []) or [] if a.get("kind") == "template_parts"]
    # a template whose mapped sections show none of the templated kinds has no part to copy: the reference is then
    # the template's blank regions, from the template the plan names
    template = parts[0]["template"] if parts else plan.get("geometry_template")
    if not template:
        raise ValueError("the plan names no template")
    data = Path(template).read_bytes()
    if any(a.get("template_md5") != hashlib.md5(data).hexdigest() for a in parts):
        raise ValueError(f"the template {Path(template).name} changed since the plan was made")
    by_part = {a["template_part"]: a for a in parts}
    tpl, out = VPackage(data), VPackage(output)
    main = tpl.main_part()
    t_secs, o_secs = sect_prs(tpl.xml(main)), sect_prs(out.xml(out.main_part()))
    if len(o_secs) != len(section_map):
        raise ValueError(f"the output has {len(o_secs)} section(s), the map {len(section_map)}")
    if any(j >= len(t_secs) for j in section_map):
        raise ValueError(f"the section map {list(section_map)} names a section the template lacks "
                         f"({len(t_secs)} section(s))")
    shown = _refs_shown(tpl)
    widths = [_width(sp) for sp in o_secs]
    distances: List[Optional[Tuple[Optional[str], Optional[str]]]] = [None] * len(o_secs)
    if keeps_distances(plan):
        if source is None:
            raise ValueError("the plan keeps the document's header/footer distances, and no source was given to read "
                             "them from")
        src = VPackage(source)
        s_secs = sect_prs(src.xml(src.main_part()))
        if len(s_secs) != len(o_secs):
            raise ValueError(f"the source has {len(s_secs)} section(s), the output {len(o_secs)}")
        distances = [_distances(sp) for sp in s_secs]
    # (template section, output text width, the source's distances when the plan keeps them)
    groups: List[Tuple[int, Optional[int], Optional[Tuple[Optional[str], Optional[str]]]]] = []
    for k, j in enumerate(section_map):
        if (j, widths[k], distances[k]) not in groups:
            groups.append((j, widths[k], distances[k]))
    groups.sort(key=lambda g: g[0])                    # template order (stable: first use within a section)
    smap = [groups.index((j, widths[k], distances[k])) for k, j in enumerate(section_map)]

    eff = Eff(tpl)
    defaults, overrides = tpl.content_types()
    rels_member = posixpath.join(posixpath.dirname(main), "_rels", posixpath.basename(main) + ".rels")
    rels_root = tpl.xml(rels_member) if rels_member in tpl.names else etree.Element(REL + "Relationships",
                                                                                    nsmap={None: REL[1:-1]})
    ids = {rel.get("Id") for rel in rels_root}
    ct_root = tpl.xml("[Content_Types].xml")
    added: Dict[str, bytes] = {}
    copies: Dict[Tuple[str, Optional[int], Optional[int]], str] = {}

    def reference_to(kind: str, target: str, w_from, w_to) -> str:
        key = (target, w_from, w_to)
        if key not in copies:
            n = len(copies)
            folder, base = posixpath.split(target)
            name = posixpath.join(folder, f"v7-reference-{n}-{base}")
            while name in tpl.names or name in added:
                n += 1
                name = posixpath.join(folder, f"v7-reference-{n}-{base}")
            added[name] = _part_copy(tpl, eff, target, by_part.get(target), w_from, w_to)
            t_rels = posixpath.join(folder, "_rels", base + ".rels")
            if t_rels in tpl.names:
                added[posixpath.join(folder, "_rels", posixpath.basename(name) + ".rels")] = tpl.read(t_rels)
            ctype = overrides.get(target) or defaults.get(posixpath.splitext(target)[1].lstrip(".").lower())
            if ctype:
                etree.SubElement(ct_root, CT + "Override", PartName="/" + name, ContentType=ctype)
            rid = f"rIdV7Reference{len(copies)}"
            while rid in ids:
                rid += "x"
            ids.add(rid)
            etree.SubElement(rels_root, REL + "Relationship", Id=rid, Type=_REL_TYPE[kind],
                             Target=posixpath.relpath(name, posixpath.dirname(main) or "."))
            copies[key] = rid
        return copies[key]

    root = tpl.xml(main)
    body = root.find(W + "body")
    for ch in list(body):
        body.remove(ch)
    for g, (j, w_to, dist) in enumerate(groups):
        sp = copy.deepcopy(t_secs[j])
        for el in sp.findall(W + "headerReference") + sp.findall(W + "footerReference"):
            sp.remove(el)
        w_from = _width(t_secs[j])
        k0 = smap.index(g)
        if w_to is not None and w_from is not None and w_to != w_from:
            o_sz, o_mar = o_secs[k0].find(W + "pgSz"), o_secs[k0].find(W + "pgMar")
            sz, mar = sp.find(W + "pgSz"), sp.find(W + "pgMar")
            sz.set(W + "w", o_sz.get(W + "w"))
            for side in ("left", "right", "gutter"):
                mar.set(W + side, o_mar.get(W + side) or "0")
        if dist is not None:
            # the document's own distances, where the plan keeps them (a distance it does not state: Word's default)
            mar = sp.find(W + "pgMar")
            if mar is None:
                mar = etree.Element(W + "pgMar")
                _insert_after(sp, mar, ("headerReference", "footerReference", "footnotePr", "endnotePr", "type",
                                        "pgSz"))
            for attr, value in zip(("header", "footer"), dist):
                if value is None:
                    mar.attrib.pop(W + attr, None)
                else:
                    mar.set(W + attr, value)
        refs = []
        # headers before footers, each in the template's own order: LibreOffice shows a titlePg section's default
        # footer on its first page when the footer's reference comes first
        for (kind, typ), target in sorted(shown[j].items(), key=lambda kv: kv[0][0] != "header"):
            if kind in kinds and target is not None and target in tpl.names:
                el = etree.Element(W + f"{kind}Reference")
                el.set(W + "type", typ)
                el.set(R + "id", reference_to(kind, target, w_from, w_to))
                refs.append(el)
        for k, el in enumerate(refs):
            sp.insert(k, el)
        if g < len(groups) - 1:
            p = etree.SubElement(body, W + "p")
            etree.SubElement(p, W + "pPr").append(sp)
        else:
            body.append(sp)

    changed = {main: etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True),
               rels_member: etree.tostring(rels_root, xml_declaration=True, encoding="UTF-8", standalone=True),
               "[Content_Types].xml": etree.tostring(ct_root, xml_declaration=True, encoding="UTF-8", standalone=True)}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for info in tpl.infos:
            z.writestr(info, changed[info.filename] if info.filename in changed else tpl.read(info.filename))
        if rels_member not in tpl.names:
            z.writestr(rels_member, changed[rels_member])
        for name, payload in added.items():
            z.writestr(name, payload)
    return buf.getvalue(), smap
