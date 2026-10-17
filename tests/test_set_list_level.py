"""set_list_level and V10's ``list_level`` kind (built-in batch 5, item 35 — docs/BUILTIN_OPS_BATCH5_DESIGN.md §3): end to
end through the full suite on the architect's outline (zero padding, formats, start, restart never / after a level /
after any higher level, already as asked, a missing or unnumbered style); the refusals (a full override level, a shared
definition with a start, an unsupported list or level, a change that leaves numbers unpredictable); wrong IMPLEMENTATIONS
shared by plan and apply (a writer that also touches another level, a wrong child order, ``w:format`` kept, the level
found through the wrong list, a count that ignores the restart rule); the numbering owner rule (apply_style beside it);
plan-time conflicts (rule 9); the batch pipeline; the catalog and the worked examples."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import List, Tuple

import pytest
from lxml import etree

from make_fixtures import build_numbering, build_style_template, patch_docx
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, op_catalog
from spec_surgeon.editspec import apply_spec, plan_spec, validate_spec
from spec_surgeon.ops import JobException, get_op
from spec_surgeon.ops import lists as tl
from spec_surgeon.ops import set_list_level as sll
from spec_surgeon.ops.selectors import StyleSheet
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import run_suite
from spec_surgeon.verify.styleimport import NUMBERING_OWNER_KINDS

MAIN = "word/document.xml"
NUMBERING = "word/numbering.xml"
STYLES = "word/styles.xml"
SPECS = Path(__file__).resolve().parents[1] / "examples" / "specs"
WD = f'xmlns:w="{W[1:-1]}"'


@pytest.fixture(scope="module")
def clean() -> bytes:
    return build_numbering(shared_restart=False)


@pytest.fixture(scope="module")
def shared() -> bytes:
    return build_numbering(shared_restart=True)


def _spec(*ops):
    return {"request": "r", "summary": "s", "mode": "formatting", "ops": list(ops)}


def run(data: bytes, *ops):
    """Plan (plan_spec: selection reads and conflicts included), apply, run the whole suite."""
    pkg = DocxPackage(data)
    plan, frags = plan_spec(pkg, _spec(*ops))
    out = apply_spec(pkg, frags).output_bytes()
    return plan, out, run_suite(data, out, plan.to_dict(), "src.docx", "out.docx")


def level(params) -> dict:
    return {"op": "set_list_level", "params": params}


def v10(rec) -> str:
    c = next(c for c in rec.checks if c.id == "V10")
    return c.summary + " | " + " | ".join(c.data.get("errors", []))


def failed(rec) -> List[str]:
    return [c.id for c in rec.checks if c.status == "FAIL"]


def numbers(data: bytes) -> dict:
    """paragraph text → the number the model shows (the output's own count)."""
    pkg = DocxPackage(data)
    root = pkg.xml(MAIN)
    pars = list(root.iter(W + "p"))
    out = {}
    for np_ in tl.count(root, StyleSheet(pkg), tl.ListDefs.load(pkg)):
        text = "".join(t.text or "" for t in pars[np_.index].iter(W + "t"))
        out.setdefault(text, np_.text)
    return out


def lvl_of(data: bytes, aid: int, ilvl: int):
    root = DocxPackage(data).xml(NUMBERING)
    return sll.level_element(root, aid, ilvl)


def kids(el) -> List[str]:
    return [etree.QName(c).localname for c in el]


def conflict(data: bytes, *ops) -> str:
    for order in (ops, tuple(reversed(ops))):
        with pytest.raises(JobException) as e:
            plan_spec(DocxPackage(data), _spec(*order))
        assert e.value.code == "conflict", (order, e.value.code, e.value.reason)
    return str(e.value)


def numbering_edit(data: bytes, fn) -> bytes:
    return patch_docx(data, {NUMBERING: fn})


def abstract(root, aid: int):
    return next(a for a in root.findall(W + "abstractNum") if a.get(W + "abstractNumId") == str(aid))


def num(root, nid: int):
    return next(n for n in root.findall(W + "num") if n.get(W + "numId") == str(nid))


# ---- end to end ------------------------------------------------------------------------------------------------------

def test_zero_padding_off_numbers_the_articles_1_1(clean):
    """The worked example: the level the Arch Article style numbers with loses its zero padding — 1.01 → 1.1 for every
    article, through the list definition; nothing else changes."""
    plan, out, rec = run(clean, level({"style": "Arch Article", "zero_pad": False}))
    assert rec.passed, rec.to_markdown()
    a = plan.property_assertions[0]
    assert (a["kind"], a["style_id"], a["num_id"], a["ilvl"], a["abstract_id"], a["changed"]) == \
        ("list_level", "ArchArt", 10, 1, 10, ["numFmt"])
    assert plan.intended_parts == [NUMBERING] and plan.mode == "formatting" and plan.geometry_reference == "none"
    lvl = lvl_of(out, 10, 1)
    assert lvl.find(W + "numFmt").attrib == {W + "val": "decimal"}
    assert kids(lvl) == kids(lvl_of(clean, 10, 1))                         # numFmt kept its place
    got = numbers(out)
    assert (got["SUMMARY"], got["REFERENCES"], got["MANUFACTURERS"], got["VALVES"], got["INSTALLATION"]) == \
        ("1.1", "1.2", "2.1", "2.4", "3.1")
    assert got["Tyco."] == "A." and got["GENERAL"] == "PART 1 -"
    assert any("8 paragraph(s) change number" in n and "‘SUMMARY’ 1.01 → 1.1" in n for n in plan.notes)
    frag = get_op("set_list_level").plan(DocxPackage(clean), {}, {"style": "ArchArt", "zero_pad": False})
    assert (NUMBERING, "*", "lvl") in frag.touches and (STYLES, "style:ArchArt", "verify") in frag.touches
    assert (NUMBERING, "*", "verify") in frag.touches and (MAIN, "*", "verify-props:numPr") in frag.touches
    assert (MAIN, "3", "verify:numPr") in frag.touches                     # SUMMARY, a counted numbered paragraph


def test_zero_padding_on_and_a_format_change(clean):
    plan, out, rec = run(clean, level({"style": "ArchSub", "zero_pad": True}))
    assert rec.passed, rec.to_markdown()
    got = numbers(out)
    assert got['Division 21 Section "Common Work Results for Fire Suppression".'] == "01."
    assert got["Product data."] == "01."
    plan, out, rec = run(clean, level({"style": "Arch Paragraph", "format": "upperRoman"}))
    assert rec.passed, rec.to_markdown()
    got = numbers(out)
    assert (got["Section includes wet-pipe sprinkler systems."], got["Deferred submittal."], got["UL listings."]) == \
        ("I.", "IV.", "III.")
    # zero padding on a letter level: nothing to change, said
    plan, out, rec = run(clean, level({"style": "ArchPara", "zero_pad": True}))
    assert rec.passed and out == clean and not plan.intended_parts
    assert any("zero padding applies to number levels only" in n for n in plan.notes)


def test_a_new_start_moves_every_count_of_the_level(clean):
    plan, out, rec = run(clean, level({"style": "ArchPart", "start": 3}))
    assert rec.passed, rec.to_markdown()
    got = numbers(out)
    assert (got["GENERAL"], got["SUMMARY"], got["PRODUCTS"], got["MANUFACTURERS"], got["EXECUTION"]) == \
        ("PART 3 -", "3.01", "PART 4 -", "4.01", "PART 5 -")
    assert lvl_of(out, 10, 0).find(W + "start").get(W + "val") == "3"
    assert plan.property_assertions[0]["changed"] == ["start"]


def test_restart_never_continues_the_articles_and_is_flagged_for_libreoffice(clean):
    plan, out, rec = run(clean, level({"style": "ArchArt", "restart": "never"}))
    assert rec.passed, rec.to_markdown()
    got = numbers(out)
    assert (got["MANUFACTURERS"], got["MATERIALS"], got["INSTALLATION"]) == ("2.04", "2.05", "3.08")
    lvl = lvl_of(out, 10, 1)
    assert lvl.find(W + "lvlRestart").get(W + "val") == "0" and kids(lvl).index("lvlRestart") == 2   # after numFmt
    assert any("LibreOffice ignores the restart rule" in f for f in plan.flags)
    assert any("5 paragraph(s) change number" in n for n in plan.notes)      # the letters under them do not


def test_restart_after_a_level(clean):
    """Paragraph letters that restart only after a PART: E. continues under the second article."""
    plan, out, rec = run(clean, level({"style": "ArchPara", "restart": {"after_level": 0}}))
    assert rec.passed, rec.to_markdown()
    assert lvl_of(out, 10, 2).find(W + "lvlRestart").get(W + "val") == "1"
    got = numbers(out)
    assert (got["NFPA 13."], got["UL listings."], got["Shop drawings."], got["Tyco."]) == ("E.", "G.", "H.", "A.")
    assert any("LibreOffice" in f for f in plan.flags)
    # back to restarting after any higher level: the element goes
    plan2, out2, rec2 = run(out, level({"style": "ArchPara", "restart": "after_higher"}))
    assert rec2.passed, rec2.to_markdown()
    assert lvl_of(out2, 10, 2).find(W + "lvlRestart") is None and numbers(out2) == numbers(clean)


def test_already_as_asked_is_a_no_op_with_a_note(clean):
    """Restart per PART on a list that already restarts: nothing changes, said; the reads of the decision only."""
    plan, out, rec = run(clean, level({"style": "Arch Article", "restart": "after_higher"}))
    assert rec.passed and out == clean and not plan.intended_parts
    assert any("already as asked" in n for n in plan.notes) and not plan.flags
    frag = get_op("set_list_level").plan(DocxPackage(clean), {}, {"style": "ArchArt", "start": 1})
    assert not any(a == "lvl" for _, _, a in frag.touches) and (NUMBERING, "*", "verify") in frag.touches


def test_a_missing_or_unnumbered_style_is_a_noted_no_op(clean):
    for style in ("Normal", "No Such Style"):
        plan, out, rec = run(clean, level({"style": style, "zero_pad": True}))
        assert rec.passed and out == clean and any("nothing to change (allow_missing)" in n for n in plan.notes)
    with pytest.raises(JobException) as e:
        get_op("set_list_level").plan(DocxPackage(clean), {}, {"style": "Normal", "zero_pad": True,
                                                               "allow_missing": False})
    assert e.value.code == "not-found"


def test_restart_per_part_where_no_part_numbers_a_paragraph_says_it_changes_nothing(clean):
    """The PART level is never used: restarting the articles after it changes nothing — flagged, with the way on."""
    data = patch_docx(clean, {MAIN: lambda r: [p.find(W + "pPr").remove(p.find(W + "pPr/" + W + "pStyle"))
                                                for p in r.iter(W + "p") if p.find(W + "pPr/" + W + "pStyle") is not None
                                                and p.find(W + "pPr/" + W + "pStyle").get(W + "val") == "ArchPart"]})
    plan, out, rec = run(data, level({"style": "ArchArt", "restart": "after_higher"}))
    assert rec.passed and out == data
    assert any("no higher level of this list numbers a paragraph" in f and "convert the numbering first" in f
               for f in plan.flags)


# ---- refusals --------------------------------------------------------------------------------------------------------

def _plan(data: bytes, **params):
    return get_op("set_list_level").plan(DocxPackage(data), {}, params)


def _refused(data: bytes, code: str, **params) -> str:
    with pytest.raises(JobException) as e:
        _plan(data, **params)
    assert e.value.code == code, (e.value.code, e.value.reason)
    return e.value.reason


def test_a_full_override_level_refuses(clean):
    data = numbering_edit(clean, lambda r: num(r, 10).append(etree.fromstring(
        f'<w:lvlOverride {WD} w:ilvl="1"><w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        f'<w:lvlText w:val="%1.%2"/></w:lvl></w:lvlOverride>')))
    assert "override this level" in _refused(data, "numbering-override", style="ArchArt", zero_pad=False)
    _plan(data, style="ArchPart", start=2)                     # another level of the same definition still plans


def test_a_shared_definition_refuses_a_start_and_plans_a_format(shared):
    assert "shared by lists [10, 11]" in _refused(shared, "numbering-unsupported", style="ArchArt", start=2)
    _refused(shared, "numbering-unsupported", style="ArchArt", restart="never")
    plan, out, rec = run(shared, level({"style": "ArchArt", "zero_pad": False}))
    assert rec.passed, rec.to_markdown()
    assert any("shared with list(s) [11]" in f for f in plan.flags)


def test_unsupported_lists_and_levels_refuse(clean):
    linked = numbering_edit(clean, lambda r: abstract(r, 10).insert(1, etree.fromstring(
        f'<w:numStyleLink {WD} w:val="OutlineList"/>')))
    _refused(linked, "numbering-unsupported", style="ArchArt", zero_pad=False)
    odd = numbering_edit(clean, lambda r: abstract(r, 10).findall(W + "lvl")[2].find(W + "numFmt").set(
        W + "val", "ordinal"))
    assert "not supported" in _refused(odd, "numbering-unsupported", style="ArchPara", start=2)


def test_a_change_the_level_cannot_show_refuses(clean):
    assert "cannot start at 0" in _refused(clean, "list-level", style="ArchPara", start=0)
    assert "needs a level below 1" in _refused(clean, "list-level", style="ArchArt", restart={"after_level": 1})
    # a start override of 0 on the articles: letters cannot show it — the numbers would no longer be predicted
    data = numbering_edit(clean, lambda r: num(r, 10).append(etree.fromstring(
        f'<w:lvlOverride {WD} w:ilvl="1"><w:startOverride w:val="0"/></w:lvlOverride>')))
    assert "the model cannot predict" in _refused(data, "list-level", style="ArchArt", format="upperLetter")


def test_bad_parameters(clean):
    for params in ({"style": "ArchArt"}, {"style": "ArchArt", "zero_pad": True, "format": "decimal"},
                   {"style": "ArchArt", "start": -1}, {"style": "ArchArt", "format": "upperLetter", "start": 0},
                   {"style": "ArchArt", "restart": "sometimes"}, {"style": "ArchArt", "restart": {"after_level": "1"}},
                   {"style": "ArchArt", "level": 9, "start": 1}, {"style": "ArchArt", "zero_pad": True, "tracked": True},
                   {"style": "ArchArt", "level": 1, "restart": {"after_level": 1}}, {"zero_pad": True},
                   {"style": "ArchArt", "start": 1, "colour": "red"}):
        with pytest.raises(JobException) as e:
            _plan(clean, **params)
        assert e.value.code == "bad-params", params
    with pytest.raises(JobException) as e:
        get_op("set_list_level").plan(DocxPackage(clean), {"scope": ["document"]}, {"style": "ArchArt", "start": 1})
    assert e.value.code == "bad-params"


# ---- wrong outputs and wrong implementations shared by plan and apply ------------------------------------------------

def test_a_wrong_output_numbering_part_is_caught(clean):
    plan, out, rec = run(clean, level({"style": "ArchArt", "zero_pad": False}))
    assert rec.passed
    bad = numbering_edit(out, lambda r: abstract(r, 10).findall(W + "lvl")[2].find(W + "numFmt").set(
        W + "val", "lowerLetter"))
    rec = run_suite(clean, bad, plan.to_dict(), "src.docx", "out.docx")
    assert "V10" in failed(rec) and "not the source's with exactly the asked level change" in v10(rec)
    assert "the output's numbers are not the predicted ones" in v10(rec)


def test_a_shared_writer_that_also_touches_another_level_is_caught(clean, monkeypatch):
    real = sll.write_level

    def also_next(root, aid, ilvl, new):
        real(root, aid, ilvl, new)
        sll.level_element(root, aid, ilvl + 1).find(W + "lvlJc").set(W + "val", "right")
    monkeypatch.setattr(sll, "write_level", also_next)
    plan, out, rec = run(clean, level({"style": "ArchArt", "zero_pad": False}))
    assert lvl_of(out, 10, 2).find(W + "lvlJc").get(W + "val") == "right"
    assert "V10" in failed(rec) and "exactly the asked level change" in v10(rec)


def test_a_shared_writer_with_the_wrong_child_order_is_caught(clean, monkeypatch):
    monkeypatch.setattr(sll, "insert_in_order", lambda parent, child, order: parent.append(child))
    plan, out, rec = run(clean, level({"style": "ArchArt", "zero_pad": False}))
    assert kids(lvl_of(out, 10, 1))[-1] == "numFmt"
    assert "V10" in failed(rec) and "exactly the asked level change" in v10(rec)


def test_a_shared_writer_that_keeps_w_format_is_caught(clean, monkeypatch):
    data = numbering_edit(clean, lambda r: abstract(r, 10).findall(W + "lvl")[1].find(W + "numFmt").set(
        W + "format", "00"))

    def keep_attrs(lvl, changes):
        new = copy.deepcopy(lvl)
        if "fmt" in changes:
            new.find(W + "numFmt").set(W + "val", changes["fmt"])       # the old element, w:format and all
        return new
    plan, out, rec = run(data, level({"style": "ArchArt", "zero_pad": False}))
    assert rec.passed, rec.to_markdown()
    assert lvl_of(out, 10, 1).find(W + "numFmt").get(W + "format") is None   # the right writer drops it
    monkeypatch.setattr(sll, "expected_level", keep_attrs)
    plan, out, rec = run(data, level({"style": "ArchArt", "zero_pad": False}))
    assert lvl_of(out, 10, 1).find(W + "numFmt").get(W + "format") == "00"
    assert "V10" in failed(rec) and "exactly the asked level change" in v10(rec)


def test_a_shared_lookup_through_the_wrong_list_is_caught(clean, monkeypatch):
    """num 10 → abstractNum 30 (a copy); abstractNum 10 stays, unused. A lookup that takes the abstractNum id to be the
    numId changes the unused definition: no number changes — and the plan's own prediction agrees with it."""
    def renumber(r):
        a = abstract(r, 10)
        b = copy.deepcopy(a)
        b.set(W + "abstractNumId", "30")
        a.addnext(b)
        num(r, 10).find(W + "abstractNumId").set(W + "val", "30")
    data = numbering_edit(clean, renumber)
    plan, out, rec = run(data, level({"style": "ArchArt", "zero_pad": False}))
    assert rec.passed and plan.property_assertions[0]["abstract_id"] == 30
    real = sll.find_level

    def by_num_id(sheet, defs, opts):
        t, why = real(sheet, defs, opts)
        return (sll.Target(t.style_id, t.num_id, t.ilvl, t.num_id) if t else t), why
    monkeypatch.setattr(sll, "find_level", by_num_id)
    plan, out, rec = run(data, level({"style": "ArchArt", "zero_pad": False}))
    assert numbers(out)["SUMMARY"] == "1.01"                               # the wrong definition changed
    assert "V10" in failed(rec) and "abstractNum 30" in v10(rec) and "exactly the asked level change" in v10(rec)


def test_a_shared_count_that_ignores_the_restart_rule_is_caught(clean, monkeypatch):
    monkeypatch.setattr(tl, "restarts", lambda restart, L, k: True)
    plan, out, rec = run(clean, level({"style": "ArchArt", "restart": "never"}))
    assert "V10" in failed(rec) and "the plan's predicted numbers are not the verifier's" in v10(rec)


def test_a_shared_restart_rule_that_forgets_what_word_ignores_is_caught(clean, monkeypatch):
    """w:lvlRestart 2 on level 1 is ignored by Word (r − 1 ≥ the level): the level already restarts after any higher
    level, so "after_higher" changes nothing — a rule that reads the raw value removes the element."""
    data = numbering_edit(clean, lambda r: abstract(r, 10).findall(W + "lvl")[1].find(W + "numFmt").addnext(
        etree.fromstring(f'<w:lvlRestart {WD} w:val="2"/>')))
    plan, out, rec = run(data, level({"style": "ArchArt", "restart": "after_higher"}))
    assert rec.passed and out == data and any("already as asked" in n for n in plan.notes)
    monkeypatch.setattr(sll, "restart_in_effect", lambda restart, ilvl: restart)
    plan, out, rec = run(data, level({"style": "ArchArt", "restart": "after_higher"}))
    assert lvl_of(out, 10, 1).find(W + "lvlRestart") is None
    assert "V10" in failed(rec) and "are not the plan's ['lvlRestart']" in v10(rec)


def test_the_two_sides_derive_the_same_change_for_every_level_and_ask(clean, shared):
    """set_list_level.changes_for ↔ listlevel._v_changes on every level of the fixtures' lists, for a grid of asks."""
    from spec_surgeon.verify import listlevel as vll
    from spec_surgeon.verify.lists import v_list_defs
    from spec_surgeon.verify.reader import VPackage
    asks = [{"format": f} for f in sll.FORMAT_CHOICES] + [{"zero_pad": True}, {"zero_pad": False}] + \
        [{"start": n} for n in (0, 1, 2, 26)] + [{"restart": r} for r in ("after_higher", "never")] + \
        [{"restart": {"after_level": k}} for k in range(4)] + [{"format": "decimalZero", "start": 3, "restart": "never"}]
    seen = 0
    for data in (clean, shared):
        tdefs, vdefs = tl.ListDefs.load(DocxPackage(data)), v_list_defs(VPackage(data))
        for aid in tdefs.abstracts:
            for ilvl, lv in tdefs.abstracts[aid].levels.items():
                vlv = vdefs.abstracts[aid]["levels"][ilvl]
                for ask in asks:
                    params = {"style": "X", **ask}
                    try:
                        opts = sll.clean_params(params)
                    except JobException:
                        with pytest.raises(ValueError):
                            vll._v_asked(params)
                        continue
                    mine, _ = sll.changes_for(lv, opts, ilvl)
                    theirs = vll._v_changes(vlv, vll._v_asked(params), ilvl)
                    assert mine == theirs, (aid, ilvl, ask)
                    seen += 1
    assert seen > 300


# ---- the numbering owner rule and plan-time conflicts (rule 9) ---------------------------------------------------------

def _bold_summary(clean: bytes) -> bytes:
    def bold(r):
        p = next(p for p in r.iter(W + "p") if "".join(t.text or "" for t in p.iter(W + "t")) == "SUMMARY")
        run_ = p.find(W + "r")
        run_.insert(0, etree.fromstring(f"<w:rPr {WD}><w:b/></w:rPr>"))
    return patch_docx(clean, {MAIN: bold})


def test_apply_style_beside_set_list_level_plans_and_passes(clean):
    """apply_style imports nothing (its kind insists numbering.xml is unchanged); set_list_level changes numbering.xml
    and its list_level kind owns the member's comparison — the import check stands down, every check passes."""
    data = _bold_summary(clean)
    ops = ({"op": "apply_style", "select": {"paragraphs": {"style_in": ["ArchArt"]}},
            "params": {"style": "ArchArt", "direct_formatting": "clear", "tracked": False}},
           level({"style": "ArchArt", "zero_pad": False}))
    plan, out, rec = run(data, *ops)
    assert rec.passed, rec.to_markdown()
    assert [a["kind"] for a in plan.property_assertions] == ["applied_style", "list_level"]
    assert numbers(out)["SUMMARY"] == "1.1"
    assert "list_level" in NUMBERING_OWNER_KINDS
    # without the owner the change is caught by the import's own check (the rule is what lets it through)
    d = plan.to_dict()
    d["property_assertions"] = [a for a in d["property_assertions"] if a["kind"] != "list_level"]
    rec = run_suite(data, out, d, "src.docx", "out.docx")
    assert "V10" in failed(rec) and "changed although no list is copied" in v10(rec)


def test_an_import_with_lists_beside_set_list_level_is_a_conflict(clean, tmp_path):
    tpl = tmp_path / "OFFICE STYLES.docx"
    tpl.write_bytes(build_style_template())
    mapping = {"op": "map_styles",
               "params": {"map": [{"from": "ArchArt", "to": "ART"}], "template": str(tpl)}}
    msg = conflict(clean, mapping, level({"style": "ArchPart", "start": 2}))
    assert NUMBERING in msg


def test_restyling_beside_set_list_level_is_a_conflict_and_paragraph_spacing_plans(clean):
    conflict(clean, {"op": "apply_style", "select": {"paragraphs": {"text_regex": "^SUMMARY"}},
                     "params": {"style": "ArchPara"}}, level({"style": "ArchArt", "zero_pad": False}))
    conflict(clean, {"op": "set_paragraph_props", "select": {"styles": {"ids": ["ArchArt"]}},
                     "params": {"spacing": {"before": 240}}}, level({"style": "ArchArt", "zero_pad": False}))
    conflict(clean, {"op": "audit_numbering", "params": {}}, level({"style": "ArchArt", "zero_pad": False}))
    # spacing on numbered paragraphs reads and changes nothing the count reads: it plans, and both kinds pass
    plan, out, rec = run(clean, {"op": "set_paragraph_props", "select": {"paragraphs": {"style_in": ["ArchArt"]}},
                                 "params": {"spacing": {"after": 120}, "tracked": False}},
                         level({"style": "ArchArt", "zero_pad": False}))
    assert rec.passed, rec.to_markdown()


# ---- the pipeline, the catalog, the worked examples ------------------------------------------------------------------

def test_a_spec_runs_through_the_batch_pipeline(clean, tmp_path):
    from spec_surgeon.batch import run_batch
    path = tmp_path / "21 13 13 – NUMBERED.docx"
    path.write_bytes(clean)
    batch = run_batch([str(path)], "edit-spec", {}, tmp_path / "out", workers=1, renderer="none", do_render=False,
                      spec=_spec(level({"style": "ArchArt", "zero_pad": False})))
    f = batch.files[0]
    assert f.status == "PASS", f.record.to_markdown() if f.record else f.exception
    with pytest.raises(JobException) as e:                         # two levels of one list in one spec: refused
        plan_spec(DocxPackage(clean), _spec(level({"style": "ArchArt", "zero_pad": False}),
                                            level({"style": "ArchPart", "start": 2})))
    assert e.value.code == "conflict"


def test_catalog_explanations_and_worked_examples(clean):
    cat = op_catalog()
    assert "set_list_level" in cat["ops"] and COMMON_ASSUMPTIONS["set_list_level"]
    for code in ("numbering-override", "numbering-unsupported", "list-level"):
        assert code in FAILURE_EXPLANATIONS, code
    for name, expect_change in (("set-list-level-zero-pad.json", True), ("restart-articles-per-part.json", False)):
        spec = json.loads((SPECS / name).read_text(encoding="utf-8"))
        validate_spec(spec)
        pkg = DocxPackage(clean)
        plan, frags = plan_spec(pkg, spec)
        out = apply_spec(pkg, frags).output_bytes()
        rec = run_suite(clean, out, plan.to_dict(), "src.docx", "out.docx")
        assert rec.passed and bool(plan.intended_parts) == expect_change, name
