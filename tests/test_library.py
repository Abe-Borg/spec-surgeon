"""Your library (library.py): recipes (saved specs) and your own operations (saved custom transforms) — names that can
never leave the library folder, recipes that load and run like any spec, operations that are re-proven on every batch
and refused when their code changed, and the command line."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from make_fixtures import build
from spec_surgeon import compiler as compiler_module
from spec_surgeon import library
from spec_surgeon.cli import main as cli_main
from spec_surgeon.codegen import generate_op, pick_samples, run_generated, trial
from spec_surgeon.editspec import load_spec
from spec_surgeon.gui import Session
from spec_surgeon.library import LibraryError
from test_codegen import GOOD_OP
from test_compiler import FakeClient, resp, tool

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def passing_op(folder, tmp_path):
    fake = FakeClient(resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    return generate_op("put the header 0.625 in from the top", "set_header_distance", sorted(folder.glob("*.docx"))[:1],
                       tmp_path / "cg", client=fake, brief="REQUEST: header\nUSER: every section")


def test_the_library_lives_in_the_test_config_folder_not_the_users():
    assert str(library.library_dir()).startswith(str(Path(__import__("os").environ["SPEC_SURGEON_CONFIG_DIR"])))


@pytest.mark.parametrize("name, ok", [("Bold headings", "bold-headings"), ("..\\..\\windows", "windows"),
                                      ("C:/temp/x", "c-temp-x"), ("CON", None), ("lpt1", None), ("  ", None),
                                      ("Über Spec 21 13 19", "ber-spec-21-13-19")])
def test_names_never_leave_the_library(name, ok):
    if ok is None:
        with pytest.raises(LibraryError):
            library.slug(name)
    else:
        assert library.slug(name) == ok


def test_a_recipe_round_trips_and_runs_like_any_spec(folder, tmp_path):
    spec = dict(REMOVE, applies_to={"pattern_ids": ["abc"]})
    item, notes = library.save_recipe("Remove all headers", spec, "for issue sets")
    assert item.path.is_file() and "applies_to" not in json.loads(item.path.read_text()) and notes
    assert library.save_recipe("Keep the or-all", REMOVE)[1] == []              # {"or": "all"} is no pin
    library.delete("keep the or-all")
    with pytest.raises(LibraryError, match="already has"):
        library.save_recipe("remove ALL headers", REMOVE)                       # same name, other case: one file
    library.save_recipe("remove ALL headers", REMOVE, replace=True)
    assert [i.name for i in library.list_items()] == ["remove ALL headers"]
    path = library.load_recipe("Remove all headers")                            # the recipe's file: a spec like any
    assert path == item.path
    s = Session(documents=sorted(folder.glob("*.docx")), out_root=tmp_path / "out")
    s._set_outcome(spec=load_spec(path), source=str(path))
    assert "2 plan cleanly" in s.make_preview(render=False, log=lambda m: None)
    assert "PASS 2" in s.run(False, False, lambda m: None)
    library.delete("remove all headers")
    assert library.list_items() == [] and not item.path.exists()


def test_a_recipe_keeps_its_template_absolute(tmp_path, monkeypatch):
    from make_fixtures import build_template
    tpl = tmp_path / "tpl" / "00 00 00 TEMPLATE.docx"
    tpl.parent.mkdir()
    tpl.write_bytes(build_template())
    spec = json.loads((SPECS / "apply-header-footer-template.json").read_text(encoding="utf-8"))
    for o in spec["ops"]:
        if "template" in (o.get("params") or {}):
            o["params"]["template"] = "tpl/00 00 00 TEMPLATE.docx"
    monkeypatch.chdir(tmp_path)
    item, _ = library.save_recipe("Our headers", spec)
    saved = json.loads(item.path.read_text(encoding="utf-8"))
    assert all(Path(o["params"]["template"]).is_absolute() for o in saved["ops"] if "template" in o.get("params", {}))


def test_an_operation_is_proven_again_on_each_batch_and_refused_when_its_code_changed(folder, tmp_path):
    g = passing_op(folder, tmp_path)
    item = library.save_operation("Header distance", g, "0.625 in")
    manifest = json.loads((item.path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["op_name"] == "set_header_distance" and manifest["brief"].startswith("REQUEST")
    loaded = library.load_operation("header distance")
    # never trusted on its record: it comes back NOT passed and is tried on the files at hand first
    assert loaded.passed is False and loaded.source == g.source and loaded.name == "set_header_distance"
    assert loaded.params == {"twips": 900} and loaded.brief == g.brief
    files = sorted(folder.glob("*.docx"))
    trials = [trial(loaded.source, f, loaded.select, loaded.params, tmp_path / "trial" / f"{i:02d}")
              for i, f in enumerate(pick_samples(files))]
    assert trials and all(t.passed for t in trials), [t.problems for t in trials]
    loaded.passed = True
    results = run_generated(loaded, [str(f) for f in files], tmp_path / "out", lambda m: None)
    assert [t.passed for t in results] == [True, True]
    assert len(list((tmp_path / "out" / "outputs").glob("*.docx"))) == 2
    (item.path / "op.py").write_text(GOOD_OP + "\n# edited by hand\n", encoding="utf-8")
    with pytest.raises(LibraryError, match="changed since it was saved"):
        library.load_operation("Header distance")
    with pytest.raises(LibraryError, match="passed verification"):              # only a passing transform is saved
        library.save_operation("x", type(g)())


def test_the_command_line_library_and_save_as(folder, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(compiler_module, "_client", lambda: FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}))))
    assert cli_main(["do", "remove all headers and footers", str(folder), "--out", str(tmp_path / "o"), "--no-render",
                     "--save-as", "Strip headers"]) == 0
    assert "Saved to your library: Recipe: Strip headers" in capsys.readouterr().out
    assert cli_main(["do", "x", str(folder), "--out", str(tmp_path / "o2"), "--no-render", "--save-as",
                     "strip HEADERS"]) == 2                                    # taken: refused before any compile
    assert cli_main(["library", "list"]) == 0 and "Recipe: Strip headers" in capsys.readouterr().out
    assert cli_main(["library", "show", "strip headers"]) == 0 and "run-spec" in capsys.readouterr().out
    assert cli_main(["library", "delete", "strip headers"]) == 2                 # no terminal, no --yes
    assert cli_main(["library", "delete", "strip headers", "--yes"]) == 0
    assert cli_main(["library", "show", "strip headers"]) == 2
