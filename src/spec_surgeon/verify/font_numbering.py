"""Independent label-font selection and whole-member numbering proof (no transform helpers)."""
from __future__ import annotations

import copy

from lxml import etree

from .lists import V_NUMBERING_REL, _v_int, v_list_defs, v_paragraph_numbering
from .props import THEME_ATTR, effective_fonts, first_difference, sig, symbolic
from .reader import W
from .xsd import sequence_order

V_PRESERVE = {"symbol", "wingdings", "wingdings 2", "wingdings 3", "webdings", "marlett", "mt extra",
              "zapfdingbats", "zapf dingbats", "bookshelf symbol 7", "ms reference specialty", "ms outlook"}


def v_level_element(root, target):
    kind, owner, k = target
    attr = "abstractNumId" if kind == "abstractNum" else "numId"
    parents = [e for e in root.findall(W + kind) if _v_int(e.get(W + attr)) == owner]
    if len(parents) != 1:
        raise ValueError(f"list-label font: {kind} {owner} is not uniquely defined")
    parent = parents[0]
    if kind == "num":
        overrides = [e for e in parent.findall(W + "lvlOverride") if _v_int(e.get(W + "ilvl")) == k]
        if len(overrides) != 1:
            raise ValueError(f"list-label font: list {owner} override {k} is not unique")
        parent = overrides[0]
    levels = [e for e in parent.findall(W + "lvl") if _v_int(e.get(W + "ilvl")) == k]
    if len(levels) != 1:
        raise ValueError(f"list-label font: {kind} {owner} level {k} is not unique")
    return levels[0]


def v_label_rows(pkg, styles, preserve):
    """Every used, readable body level, with its paragraph; picture/symbol labels marked for preservation.

    Counter evaluation is unnecessary for fonts. Full override levels have their own rPr and can be covered even
    where the shared counter model cannot predict the label's number.
    """
    defs = v_list_defs(pkg)
    part = pkg.related(V_NUMBERING_REL)
    if part is None:
        return []
    rows = []
    for text_part in [pkg.main_part()]:
        for bi, p in enumerate(pkg.xml(text_part).iter(W + "p")):
            nb = v_paragraph_numbering(p, styles, defs)
            if nb is None:
                continue
            problem = defs.problem(nb.num_id)
            if nb.unreadable or nb.dangling or problem:
                why = nb.unreadable or ("dangling list reference" if nb.dangling else problem[1])
                raise ValueError(f"{text_part} paragraph {bi}: cannot resolve list-label font: {why}")
            if defs.level(nb.num_id, nb.ilvl) is None:
                continue
            kind, owner = ("num", nb.num_id) if defs.is_override(nb.num_id, nb.ilvl) else \
                ("abstractNum", defs.abstract_of(nb.num_id))
            target = [kind, owner, nb.ilvl]
            lvl = v_level_element(defs.root, target)
            preserved = lvl.find(W + "lvlPicBulletId") is not None or \
                symbolic(lvl.find(W + "rPr"), p, styles, preserve) or \
                symbolic(p.find(W + "pPr/" + W + "rPr"), p, styles, preserve)
            rows.append((text_part, bi, p, target, lvl, preserved))
    protected = {tuple(row[3]) for row in rows if row[5]}
    return [(part, bi, p, target, lvl, tuple(target) in protected)
            for part, bi, p, target, lvl, _ in rows]


def _numbering_fonts(ctx, a, errs):
    params, select = a.get("font_params", {}), a.get("font_select", {})
    if a.get("font_op") == "set_style_font":
        default = select.get("styles", "all") in (None, "all", "all-paragraph-and-character")
    elif a.get("font_op") == "set_font":
        scope = select.get("scope", ["document"])
        scope = [scope] if isinstance(scope, str) else scope
        # Unfiltered runs and paragraphs are the whole-document selection; an empty predicate narrows neither.
        pred = select.get("paragraphs")
        narrowed = bool(set(pred) - {"case_insensitive", "flexible_whitespace"}) if isinstance(pred, dict) else \
            pred not in (None, "all")
        default = "document" in scope and select.get("runs", "all") in (None, "all", {}) and \
            not narrowed
    else:
        raise ValueError("numbering_fonts: missing source operation identity")
    if not params.get("numbering_levels", default):
        raise ValueError("numbering_fonts: this selection did not request a label-font change")
    source_part, output_part = ctx.src.related(V_NUMBERING_REL), ctx.out.related(V_NUMBERING_REL)
    if source_part != output_part or source_part != a.get("numbering_part"):
        errs.append(f"numbering_fonts: numbering part {source_part!r} → {output_part!r}; "
                    f"plan: {a.get('numbering_part')!r}")
        return "numbering_fonts: numbering part mismatch"
    preserve = {f.casefold() for f in params["preserve_fonts"]} if "preserve_fonts" in params else V_PRESERVE
    if preserve != {f.casefold() for f in a.get("preserve_fonts", [])}:
        errs.append("numbering_fonts: planned symbol-font exclusions disagree with the requested exclusions")
    targets = sorted({tuple(row[3]) for row in v_label_rows(ctx.src, ctx.src_styles, preserve) if not row[5]})
    if [list(t) for t in targets] != a.get("expected_levels"):
        errs.append(f"numbering_fonts: {len(targets)} used level(s) selected here; "
                    f"plan predicted {len(a.get('expected_levels', []))} with different identities")
    if source_part is None:
        return "numbering_fonts: no numbering member"
    expected = copy.deepcopy(ctx.src.xml(source_part))
    for target in targets:
        lvl = v_level_element(expected, target)
        rpr = lvl.find(W + "rPr")
        if rpr is None:
            rpr = etree.Element(W + "rPr")
            lvl_order = sequence_order("ISO-IEC29500-4_2016/wml.xsd", "CT_Lvl")
            insert = next((i for i, c in enumerate(lvl) if c.tag in lvl_order and
                           lvl_order.index(c.tag) > lvl_order.index(rpr.tag)), len(lvl))
            lvl.insert(insert, rpr)
        rf = rpr.find(W + "rFonts")
        if rf is None:
            rf = etree.Element(W + "rFonts")
            # CT_RPr permits a choice; use Word's conventional rStyle-before-rFonts layout while retaining
            # every existing child in order. The paragraph revision markers also precede these properties.
            preceding = {W + n for n in ("ins", "del", "moveFrom", "moveTo", "rStyle")}
            insert = next((i for i, c in enumerate(rpr) if c.tag not in preceding), len(rpr))
            rpr.insert(insert, rf)
        for slot, font in a.get("set", {}).items():
            rf.set(W + slot, font)
            if a.get("clear_theme", True):
                rf.attrib.pop(W + THEME_ATTR[slot], None)
            elif rf.get(W + THEME_ATTR[slot]):
                raise ValueError("numbering_fonts: a theme attribute overrides a selected label font")
    got = ctx.out.xml(output_part)
    if sig(expected) != sig(got):
        errs.append(f"numbering_fonts: {output_part} differs beyond the requested label fonts: "
                    f"{first_difference(expected, got)}")
    ctx.verified_members.add(output_part)
    return f"numbering_fonts: {len(targets)} used list-label level(s) → {a.get('set')}"


def check_effective_labels(ctx, a, slots, preserve, bad_examples):
    """Postcondition over label runs; return (checked labels, preserved slots, mismatches)."""
    rows = v_label_rows(ctx.src, ctx.src_styles, preserve)
    source_part, output_part = ctx.src.related(V_NUMBERING_REL), ctx.out.related(V_NUMBERING_REL)
    if source_part is None:
        return 0, 0, 0
    if output_part is None:
        raise ValueError("effective_font: numbering member disappeared")
    checked = preserved = bad = 0
    for part, bi, sp, target, slvl, keep in rows:
        # Limit this postcondition to the same text-part scope as the run checks.
        if part not in ctx.scope_parts(a.get("scope", ["document"])):
            continue
        olvl = v_level_element(ctx.out.xml(output_part), target)
        sv, ov = ctx.view(part)
        oi = ctx.maps[part].get(bi)
        if oi is None:
            raise ValueError(f"effective_font: list-label paragraph {bi} in {part} cannot be aligned")
        es = effective_fonts(slvl.find(W + "rPr"), sp, ctx.src_styles, ctx.src_theme)
        eo = effective_fonts(olvl.find(W + "rPr"), ov.pars[oi], ctx.out_styles, ctx.out_theme)
        checked += 1
        for slot in slots:
            # Preserved glyph fonts must remain equal to the original, not merely be exempt from the assertion.
            if keep:
                preserved += 1
                # Other slots can inherit an ordinary style-font change; the glyph's symbol slot must survive.
                if not es.get(slot) or es[slot].casefold() not in preserve:
                    continue
            if eo.get(slot) != (es.get(slot) if keep else a.get("value")):
                bad += 1
                if len(bad_examples) < 3:
                    bad_examples.append(f"{part} block {bi} label {slot}: {es.get(slot)!r}→{eo.get(slot)!r}")
    return checked, preserved, bad


FONT_NUMBERING_KINDS = {"numbering_fonts": _numbering_fonts}
