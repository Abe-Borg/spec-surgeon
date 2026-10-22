"""What a custom method may format (docs/PLAN-2026-10-22-custom-envelope.md, Option A).

A1 — the limits say what the checker proves. ``CUSTOM_TRANSFORM_LIMITS`` (2026-10-17) listed highlight under "cannot",
though V10's ``properties`` kind has verified highlight, superscript and subscript, hidden text, character spacing and
double strikethrough since set_properties grew them (2026-10-21). A custom method picks its targets by its own logic
and names them (an ``index_range`` per paragraph); the kind checks those got exactly the asked property and nothing
else changed. Each scripted method here runs through the real trial: the whitelist, the sandbox, ``hard_limits`` and
the suite."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from make_fixtures import build
from spec_surgeon import codegen
from spec_surgeon.catalog import CUSTOM_TRANSFORM_LIMITS, op_catalog
from spec_surgeon.codegen import check_source, trial
from spec_surgeon.ops import get_op
from spec_surgeon.verify import props as vprops

HIGHLIGHT_OP = '''
from __future__ import annotations
from lxml import etree
from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.package import W
from spec_surgeon.ooxml import RPR_ORDER, insert_in_order, paragraphs, block_full_text, run_rpr, mark_rpr
from spec_surgeon.ops.selectors import DEFAULT_PRESERVE_FONTS


class HighlightInsurerParagraphs(Op):
    name = "highlight_insurer_paragraphs"
    mode = "formatting"
    description = "Highlight the paragraphs that name the insurer, but not one holding a field or a tracked change."
    params_schema = {"type": "object", "properties": {"color": {"type": "string"}, "words": {"type": "string"}}}
    select_schema = {}

    def plan(self, pkg, select, params):
        color, words = params.get("color", "yellow"), params.get("words", "FM Global")
        main = pkg.main_part()
        frag = self.fragment(select, params)
        chosen = []
        for bi, p in enumerate(paragraphs(pkg.xml(main))):
            if words not in block_full_text(p):
                continue
            if p.find(".//" + W + "fldChar") is not None or p.find(".//" + W + "ins") is not None \\
                    or p.find(".//" + W + "del") is not None:
                continue
            runs = [r for r in p.iter(W + "r") if any(c.tag != W + "rPr" for c in r)]
            frag.property_assertions.append({
                "kind": "properties", "target": "direct", "scope": ["document"], "parts": [main],
                "select": {"runs": "all", "paragraphs": {"index_range": [bi, bi + 1]}},
                "set": {"run": {"highlight": color}, "paragraph": {}}, "preserve_fonts": list(DEFAULT_PRESERVE_FONTS),
                "skip_sym_runs": True, "in_revisions": "include", "include_paragraph_marks": True, "tracked": False,
                "author": None, "expected_runs": len(runs), "expected_marks": 1, "expected_paragraphs": 0,
                "expected_shown_marks": 0})
            frag.touches.append((main, str(bi), "rPr"))
            chosen.append(bi)
        if not chosen:
            raise JobException("no paragraph names the insurer", "not-found")
        frag.intended_parts = [main]
        frag.transform_data = {"main": main, "paragraphs": chosen, "color": color}
        frag.geometry_reference = "source"
        return frag

    def apply(self, pkg, frag):
        td = frag.transform_data
        root = pkg.xml(td["main"])
        pars = paragraphs(root)
        for bi in td["paragraphs"]:
            p = pars[bi]
            targets = [run_rpr(r) for r in p.iter(W + "r") if any(c.tag != W + "rPr" for c in r)]
            targets.append(mark_rpr(p, create=True))
            for rpr in targets:
                h = rpr.find(W + "highlight")
                if h is None:
                    h = insert_in_order(rpr, etree.Element(W + "highlight"), RPR_ORDER)
                h.set(W + "val", td["color"])
        pkg.write_xml(td["main"], root)
'''

APPLY_LOOP = 'for bi in td["paragraphs"]:'
SET_VALUE = 'h.set(W + "val", td["color"])'
PLANTED = {
    # the colour written is not the one asserted
    "a wrong colour": HIGHLIGHT_OP.replace(SET_VALUE, 'h.set(W + "val", "green")'),
    # a paragraph the plan never named highlighted too
    "an unlisted paragraph": HIGHLIGHT_OP.replace(APPLY_LOOP, 'for bi in td["paragraphs"] + [td["paragraphs"][-1] + 1]:'),
    # a word changed beside the formatting
    "a changed word": HIGHLIGHT_OP.replace(
        SET_VALUE, SET_VALUE + '\n            for t in p.iter(W + "t"):\n'
                               '                t.text = (t.text or "").replace("FM Global", "FM Approvals")'),
}


@pytest.fixture
def spec(tmp_path) -> Path:
    f = tmp_path / "21 05 00 - COMMON WORK RESULTS.docx"
    f.write_bytes(build("21 05 00", "COMMON WORK RESULTS"))
    return f


def highlighted(docx: bytes):
    out = []
    for p in Document(io.BytesIO(docx)).paragraphs:
        colours = {r.font.highlight_color for r in p.runs if r.text}
        if colours and None not in colours:
            out.append(p.text[:2])
    return out


def test_a_custom_method_highlights_the_paragraphs_it_picks(spec, tmp_path):
    assert check_source(HIGHLIGHT_OP) == []
    before = spec.read_bytes()
    t = trial(HIGHLIGHT_OP, spec, {}, {"color": "yellow"}, tmp_path / "sb")
    assert t.passed, t.problems
    assert "properties" in t.record.get("V10").summary
    # A. and B. name the insurer; C. holds Jeremy's insertion and E. a field: the method's own logic left them
    assert highlighted(Path(t.output).read_bytes()) == ["A.", "B."]
    assert spec.read_bytes() == before


@pytest.mark.parametrize("case", sorted(PLANTED))
def test_a_wrong_custom_method_fails_the_trial(case, spec, tmp_path):
    t = trial(PLANTED[case], spec, {}, {"color": "yellow"}, tmp_path / "sb")
    assert not t.passed, case
    assert any(p.startswith(("V10 FAIL", "V2 FAIL")) for p in t.problems), t.problems


# what the `properties` family's words name, per property the kind takes (set_properties' own parameters)
WORDS = {"b": "bold", "i": "italic", "caps": "caps", "smallCaps": "small caps", "strike": "strikethrough",
         "dstrike": "double strikethrough", "u": "underline", "color": "text colour", "vertAlign": "superscript and "
         "subscript", "vanish": "hidden text", "spacing": "character spacing", "highlight": "highlight",
         "keepNext": "keep-with-next", "keepLines": "keep lines together", "widowControl": "widow control",
         "pageBreakBefore": "page break before"}
SAMPLES = {"u": "single", "color": "FF0000", "vertAlign": "superscript", "highlight": "yellow", "spacing": 1}


def test_the_properties_family_names_every_property_the_kind_verifies():
    family = next(words for words, kind in CUSTOM_TRANSFORM_LIMITS["families"].items() if kind == "properties")
    schema = get_op("set_properties").params_schema["properties"]
    run_keys, para_keys = list(schema["run"]["properties"]), list(schema["paragraph"]["properties"])
    assert set(WORDS) == set(run_keys) | set(para_keys), "a property was added: name it in the family and here"
    for k in run_keys:
        assert vprops._v_run_elements({k: SAMPLES.get(k, True)}), k          # the kind verifies it
        assert WORDS[k] in family, k
    for k in para_keys:
        assert vprops._v_para_elements({k: True}), k
        assert WORDS[k] in family, k
    assert set(CUSTOM_TRANSFORM_LIMITS["families"].values()) <= set(vprops.KINDS)


def test_highlight_is_no_longer_out_of_reach_and_the_writer_is_told():
    assert not any("highlight" in c for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert "highlight of selected text" in codegen.system_prompt()
    assert "highlight of selected text" in str(op_catalog(examples=False)["custom_transform_limits"])
