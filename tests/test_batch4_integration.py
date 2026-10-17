"""Built-in batch 4 integrated (docs/BUILTIN_OPS_BATCH4_DESIGN.md): the worked examples plan and verify on the architect's
spec; every refusal code of the batch's operations is explained in plain words and none of the file-specific ones is a
spec-level (fatal) code; the operations place no words; the catalog, Claude's prompt and the profile know them; the
recommended workflow — map the architect's styles to yours, then remove what is left unused — runs as a chain."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from make_fixtures import build_style_template, build_styles
from spec_surgeon import compiler
from spec_surgeon.catalog import COMMON_ASSUMPTIONS, CUSTOM_TRANSFORM_LIMITS, op_catalog
from spec_surgeon.chain import chain_from_specs, run_chain
from spec_surgeon.editspec import load_spec, plan_spec, spec_input_files, validate_spec
from spec_surgeon.ops import get_op
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.profile import profile
from spec_surgeon.report import FAILURE_EXPLANATIONS
from spec_surgeon.verify import props as vprops
from spec_surgeon.verify import run_suite

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "specs"
OPS_DIR = ROOT / "src" / "spec_surgeon" / "ops"
NEW_OPS = ("set_borders_shading", "clear_direct_formatting", "set_language", "apply_style", "map_styles",
           "remove_unused_styles")
BATCH4_MODULES = ("set_borders_shading", "set_paragraph_props", "clear_formatting", "set_language", "apply_style",
                  "map_styles", "style_import", "remove_unused_styles")
WORKED = ("notes-boxed-and-shaded", "style-spacing", "clear-direct-formatting", "language-us-english",
          "bold-caps-to-heading", "architect-styles-to-office", "remove-unused-styles")


@pytest.fixture(scope="module")
def src() -> bytes:
    return build_styles()


@pytest.mark.parametrize("name", WORKED)
def test_the_worked_examples_plan_and_verify_on_the_architects_spec(src, name):
    spec = load_spec(EXAMPLES / f"{name}.json")
    validate_spec(spec)
    assert json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))["request"]
    pkg = DocxPackage(src)
    plan, frags = plan_spec(pkg, spec)
    work = pkg.copy()
    for f in frags:
        get_op(f.op).apply(work, f)
    rec = run_suite(src, work.output_bytes(), plan.to_dict(), "src.docx", "out.docx")
    assert rec.passed, rec.to_markdown()
    assert plan.property_assertions, name                     # every one of them asserts what it changed


def test_the_mapping_example_reads_the_office_template_as_an_input():
    """Rule 12: the example's relative template path resolves against the spec's folder, and the approval token's
    input files include it."""
    spec = load_spec(EXAMPLES / "architect-styles-to-office.json")
    tpl = (ROOT / "examples" / "templates" / "OFFICE STYLES.docx").resolve()
    assert Path(spec["ops"][0]["params"]["template"]) == tpl
    assert tpl in spec_input_files(spec)


def _refusal_codes(module: str):
    """(the code literals of every JobException the module raises — and of every call of style_import.stripped, which
    raises the code its caller passes —, the raises whose code is neither)."""
    tree = ast.parse((OPS_DIR / f"{module}.py").read_text(encoding="utf-8"))
    literal, other = set(), []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        if getattr(n.func, "id", None) == "stripped" and len(n.args) > 2 and isinstance(n.args[2], ast.Constant):
            literal.add(n.args[2].value)
        if getattr(n.func, "id", None) == "JobException":
            code = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "code"), None)
            if isinstance(code, ast.Constant):
                literal.add(code.value)
            else:
                other.append((module, n.lineno))
    return literal, other


def test_every_refusal_code_of_the_batch4_ops_is_explained_and_none_file_specific_is_fatal():
    codes, computed = set(), []
    for m in BATCH4_MODULES:
        literal, other = _refusal_codes(m)
        codes |= literal
        computed += other
    assert [m for m, _ in computed] == ["style_import"], computed      # stripped(): its callers' literals, read above
    codes.discard("internal")                                    # diagnostics.classify: an internal error
    assert {"style-mismatch", "style-import", "style-numbering", "missing-style", "tracked-format",
            "conflict-property"} <= codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []
    # a refusal one file earns must never reject the whole spec at compile time (batch 2 rule 5)
    assert not ({"style-mismatch", "style-import", "style-numbering", "missing-style"} & compiler.FATAL_SPEC_CODES)


def test_the_batch4_ops_place_no_words():
    for name in WORKED:
        assert compiler.placed_words(load_spec(EXAMPLES / f"{name}.json")) == [], name


def test_catalog_prompt_and_custom_transform_limits_know_the_batch():
    cat = op_catalog()
    for op in NEW_OPS:
        assert op in cat["ops"] and COMMON_ASSUMPTIONS[op], op
        assert op in compiler.SYSTEM, op
    assert "styles" in cat["ops"]["set_paragraph_props"]["select"]["properties"]
    assert CUSTOM_TRANSFORM_LIMITS["families"]["paragraph borders and shading of paragraphs or of paragraph styles"] \
        == "borders_shading"
    assert not any("borders" in c or "shading" in c for c in CUSTOM_TRANSFORM_LIMITS["cannot"])
    assert set(CUSTOM_TRANSFORM_LIMITS["families"].values()) <= set(vprops.KINDS)
    for kind in ("borders_shading", "cleared_formatting", "language", "applied_style", "mapped_styles",
                 "styles_imported", "removed_styles"):
        assert kind in vprops.KINDS, kind
    assert "ATTACHED TEMPLATE" in compiler.SYSTEM and "read_template lists its styles" in compiler.SYSTEM


def test_the_profile_counts_unused_styles(src):
    counts = profile(DocxPackage(src, name="spec.docx"))["counts"]
    assert counts["styles_unused"]["custom"] == 4                 # ArchUnused(+Char), ArchLinkedPara(+Char)
    assert counts["styles_unused"]["all"] > counts["styles_unused"]["custom"]


def test_map_then_remove_unused_runs_as_a_chain(tmp_path):
    """The workflow the prompt recommends: stage 1 maps the architect's ArchLines to the office CMT (imported from the
    template), stage 2 removes the custom styles nothing uses any more — ArchLines among them, which in a single spec
    would still count as used (it judges the file before the spec)."""
    tpl = tmp_path / "OFFICE STYLES.docx"
    tpl.write_bytes(build_style_template())
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "21 13 13 - WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build_styles())
    map_spec = {"request": "map", "summary": "map", "mode": "formatting",
                "ops": [{"op": "map_styles", "params": {"map": [{"from": "ArchLines", "to": "CMT"}],
                                                         "template": str(tpl)}}]}
    clean_spec = {"request": "clean", "summary": "clean", "mode": "formatting",
                  "ops": [{"op": "remove_unused_styles", "params": {"which": "custom"}}]}
    chain = chain_from_specs([("map", map_spec), ("remove unused", clean_spec)], name="office styles")
    result = run_chain([str(docs)], chain, tmp_path / "out", workers=1, do_render=False)
    f = result.files[0]
    assert f.status == "PASS" and f.stage_status == ["PASS", "PASS"], (f.status, f.stage_status, f.reason)
    out = DocxPackage(Path(f.delivered_to).read_bytes())
    ids = {s.get(W + "styleId") for s in out.xml("word/styles.xml").findall(W + "style")}
    assert {"CMT", "CMTBase"} <= ids and "ArchLines" not in ids and "ArchUnused" not in ids
    # in ONE spec the mapping's from style still counts as used: it stays
    pkg = DocxPackage(build_styles())
    plan, frags = plan_spec(pkg, {"request": "both", "summary": "both", "mode": "formatting",
                                  "ops": map_spec["ops"] + clean_spec["ops"]})
    removed = next(a for a in plan.property_assertions if a["kind"] == "removed_styles")["expected_removed"]
    assert "ArchLines" not in removed
