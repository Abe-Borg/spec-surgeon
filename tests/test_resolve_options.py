"""resolve_options (docs/BUILTIN_OPS_BATCH1_DESIGN.md §3): MasterSpec [option] [option] groups resolved by pick/drop
rules, deletion only — end to end tracked and untracked, a group over two runs, the refusals, and planted defects."""
from __future__ import annotations

from typing import Any, Dict

import pytest

from make_fixtures import build_batch1
from spec_surgeon.ops import JobException, get_op, merge_fragments
from spec_surgeon.ops import resolve_options as options_module
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.verify import run_suite
from test_text_edits import accepted_text, failed, mutate, para, v10_errors

AUTHOR = "Abraham Borg"
PICK_PREACTION = [{"match": "preaction", "action": "pick"}]


@pytest.fixture(scope="module")
def doc() -> bytes:
    return build_batch1()


def run_op(src: bytes, params: Dict[str, Any], select: Dict[str, Any] = None):
    pkg = DocxPackage(src)
    frag = get_op("resolve_options").plan(pkg, select or {}, {"author": AUTHOR, **params})
    plan = merge_fragments([frag])
    work = pkg.copy()
    get_op("resolve_options").apply(work, frag)
    out = work.output_bytes()
    return plan, out, run_suite(src, out, plan.to_dict(), "src.docx", "out.docx")


@pytest.mark.parametrize("tracked", [True, False])
def test_pick_preaction_wherever_offered(doc, tracked):
    plan, out, rec = run_op(doc, {"rules": PICK_PREACTION, "tracked": tracked})
    assert rec.passed, rec.to_markdown()
    assert accepted_text(para(out, "C.")) == "C.\tProvide preaction sprinkler systems for the data halls."
    if tracked:          # deletion only: the block text keeps the deleted options as w:delText, the accepted view moves
        assert plan.diffs == [] and plan.accepted_views
        assert rec.get("V8").status == "PASS"
    assert any("2 group(s) no rule applies to" in n or "group(s) no rule applies to" in n for n in plan.notes)


def test_drop_a_whole_optional_group_with_its_space(doc):
    plan, out, rec = run_op(doc, {"rules": [{"match": "clean agent", "action": "drop"}], "tracked": False})
    assert rec.passed
    assert accepted_text(para(out, "D.")) == "D.\tInstall hangers per NFPA 13."


def test_a_group_over_two_runs(doc):
    plan, out, rec = run_op(doc, {"rules": [{"match": "^Schedule 40$", "action": "pick"}], "tracked": True})
    assert rec.passed, rec.to_markdown()
    assert accepted_text(para(out, "E.")) == "E.\tPipe shall be Schedule 40 black steel."


def test_drop_some_options_keeps_the_rest_bracketed(doc):
    plan, out, rec = run_op(doc, {"rules": [{"match": "^dry", "action": "drop"}], "tracked": False})
    assert rec.passed
    assert accepted_text(para(out, "C.")) == "C.\tProvide [wet-pipe] [preaction] sprinkler systems for the data halls."
    plan, out, rec = run_op(doc, {"rules": [{"match": "^(dry|preaction)", "action": "drop"}], "tracked": False})
    assert accepted_text(para(out, "C.")) == "C.\tProvide [wet-pipe] sprinkler systems for the data halls."


def test_the_first_applicable_rule_decides(doc):
    plan, out, rec = run_op(doc, {"rules": [{"match": "^dry", "action": "drop"}] + PICK_PREACTION, "tracked": False})
    assert rec.passed
    assert accepted_text(para(out, "C.")) == "C.\tProvide [wet-pipe] [preaction] sprinkler systems for the data halls."


def test_refusals(doc):
    with pytest.raises(JobException) as e:
        run_op(doc, {"rules": [{"match": "^8$", "action": "pick"}]})               # [Provide [6] [8] inch mains.]
    assert e.value.code == "nested-options"
    with pytest.raises(JobException) as e:
        run_op(doc, {"rules": [{"match": "pipe", "action": "pick"}]})              # wet-pipe AND dry-pipe
    assert e.value.code == "ambiguous"
    with pytest.raises(JobException) as e:
        run_op(doc, {"rules": [{"match": "deluge", "action": "pick"}]})
    assert e.value.code == "not-found"
    plan, out, rec = run_op(doc, {"rules": [{"match": "deluge", "action": "pick"}], "allow_missing": True})
    assert rec.passed and out == doc
    for bad in ([], [{"match": "x"}], [{"match": "(", "action": "pick"}], [{"match": "x", "action": "keep"}]):
        with pytest.raises(JobException) as e:
            run_op(doc, {"rules": bad})
        assert e.value.code == "bad-params"


def test_option_grammar():
    text = "A [a] [b]  [c] x [ ] [d][e] [f [g] h] [i]"
    got = [(text[s:e], [text[a:b] for a, b in o]) for s, e, o in options_module.groups_of(text)]
    assert got == [("[a] [b]  [c]", ["[a]", "[b]", "[c]"]), ("[d]", ["[d]"]), ("[e]", ["[e]"]),
                   ("[g]", ["[g]"]), ("[i]", ["[i]"])]
    depth = options_module.open_depth(text)
    assert depth[text.index("[g]")] == 1 and depth[text.index("[i]")] == 0


# ---- planted defects ---------------------------------------------------------------------------------------------

def test_defect_a_bracket_left_behind(doc):
    plan, out, rec = run_op(doc, {"rules": PICK_PREACTION, "tracked": False})

    def bracket(root):
        for t in root.iter(W + "t"):
            if t.text and "preaction sprinkler" in t.text:
                t.text = t.text.replace("preaction sprinkler", "preaction] sprinkler")
    rec = run_suite(doc, mutate(out, "word/document.xml", bracket), plan.to_dict(), "s", "o")
    assert "V10" in failed(rec)


def test_shared_defect_drop_leaves_the_space(doc, monkeypatch):
    real = options_module.group_deletions

    def keep_space(text, start, end, opts, rules, where, nested):
        dels, label = real(text, start, end, opts, rules, where, nested)
        return [(a, b - 1) if b - a > 1 and text[b - 1] == " " else (a, b) for a, b in dels], label
    monkeypatch.setattr(options_module, "group_deletions", keep_space)
    plan, out, rec = run_op(doc, {"rules": [{"match": "clean agent", "action": "drop"}], "tracked": False})
    assert accepted_text(para(out, "D.")) == "D.\t Install hangers per NFPA 13."      # the double space, consistently
    assert failed(rec) == ["V10"]


def test_shared_defect_nesting_ignored(doc, monkeypatch):
    monkeypatch.setattr(options_module, "open_depth", lambda text: [0] * (len(text) + 1))
    plan, out, rec = run_op(doc, {"rules": [{"match": "^8$", "action": "pick"}], "tracked": False})
    assert failed(rec) == ["V10"]
    assert any("nested" in e for e in v10_errors(rec))


def test_shared_defect_pick_keeps_the_wrong_option(doc, monkeypatch):
    real = options_module.group_deletions

    def wrong(text, start, end, opts, rules, where, nested):
        dels, label = real(text, start, end, opts, rules, where, nested)
        if label and label.startswith("pick") and len(opts) > 1:
            a, b = opts[0]                                   # keep the FIRST option instead
            return [(b - 1, end)] + [(start, a + 1)], label
        return dels, label
    monkeypatch.setattr(options_module, "group_deletions", wrong)
    plan, out, rec = run_op(doc, {"rules": PICK_PREACTION, "tracked": False})
    assert "Provide wet-pipe sprinkler" in accepted_text(para(out, "C."))
    assert failed(rec) == ["V10"]
