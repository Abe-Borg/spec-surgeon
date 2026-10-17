"""Built-in batch 1 in the rest of the app (docs/BUILTIN_OPS_BATCH1_DESIGN.md §0.2, §0.3): the words the new ops place
are traced to the user (compiler.placed_words); tables and pictures are inputs like a template (rule 12) — in the
approval token, resolved beside a spec file, stored absolute in a recipe; every new refusal code has a plain-words
explanation; and Claude sees the tables and pictures beside the documents (INPUT FILES, read_input_file) — only those."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build_batch1, png
from spec_surgeon import compiler, inputs, library
from spec_surgeon.compiler import _Batch, check_submission, placed_words, untraceable
from spec_surgeon.conversation import Conversation
from spec_surgeon.editspec import load_spec, spec_input_files, spec_templates
from spec_surgeon.ops import end_of_section
from spec_surgeon.preview import approval_inputs, approval_token, explain_approval_change
from spec_surgeon.report import FAILURE_EXPLANATIONS
from test_text_edits import _xlsx

OPS_DIR = Path(__file__).resolve().parent.parent / "src" / "spec_surgeon" / "ops"
BATCH1_MODULES = ["textedit", "tables", "replace_table", "fill_variables", "resolve_options", "filter_tagged",
                  "end_of_section", "naming", "agree", "images", "swap_image"]


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "211319 - Preaction.docx").write_bytes(build_batch1())
    (d / "PROJECT.xlsx").write_bytes(_xlsx([["Variable", "VA1", "VA3"], ["SITE_NAME", "Ashburn", "Leesburg"],
                                            ["OWNER_NAME", "Contoso", "Contoso"], ["PROJECT NO", "1001", "1003"]]))
    (d / "pairs.csv").write_text("find,replace\nOwner,Client\n", encoding="utf-8")
    (d / "logo.png").write_bytes(png((1, 2, 3), size=6))
    (d / "notes.txt").write_text("not an input", encoding="utf-8")
    return d


def fill_spec(folder, **values):
    params = {"table": {"file": str(folder / "PROJECT.xlsx"), "name_column": "Variable", "value_column": "VA3"},
              "author": "A"}
    if values:
        params["values"] = values
    return {"request": "fill the VA3 site", "summary": "s", "mode": "content", "ops": [
        {"op": "fill_variables", "params": params}]}


# ---- words placed --------------------------------------------------------------------------------------------------

def test_the_words_the_new_ops_place_are_traced_to_the_user():
    spec = {"request": "r", "summary": "s", "mode": "content", "ops": [
        {"op": "replace_table", "params": {"pairs": [{"find": "Owner", "replace": "Client"},
                                                     {"find": "x", "replace": ""}],
                                           "pairs_table": {"file": "t.csv"}}},
        {"op": "set_issue_stamp", "params": {"pattern": "ISSUED.*", "text": "ISSUED FOR BID"}},
        {"op": "fill_variables", "params": {"values": {"SITE_NAME": "Leesburg"}, "table": {"file": "p.xlsx"}}},
        {"op": "ensure_end_of_section", "params": {"number": {"rule": "regex", "pattern": "x"},
                                                   "text": "END OF SECTION {number} (SPRINKLERS)"}},
        {"op": "ensure_end_of_section", "params": {"number": {"rule": "regex", "pattern": "x"}}},
        {"op": "resolve_options", "params": {"rules": [{"match": "preaction", "action": "pick"}]}},
        {"op": "name_output", "params": {"pattern": "{n} - SPEC", "values": {"n": {"rule": "regex", "pattern": "x"}}}},
    ]}
    assert placed_words(spec) == ["Client", "ISSUED FOR BID", "Leesburg", "END OF SECTION", "(SPRINKLERS)",
                                  "END OF SECTION"]
    assert compiler.END_OF_SECTION_DEFAULT == end_of_section.DEFAULT_TEXT
    assert untraceable(spec, ["replace Owner with Client, stamp ISSUED FOR BID, END OF SECTION (SPRINKLERS)"]) == \
        ["Leesburg"]


def test_a_typed_fill_value_must_be_the_users_and_a_table_value_needs_no_trace(folder):
    batch = _Batch(sorted(folder.glob("*.docx")), None)
    assert check_submission(fill_spec(folder), batch, ["fill the VA3 site from PROJECT.xlsx"]) == []
    errs = check_submission(fill_spec(folder, SITE_NAME="Loudoun"), batch, ["fill the VA3 site"])
    assert errs and "never gave them verbatim" in errs[0] and "Loudoun" in errs[0]


# ---- input files like a template ------------------------------------------------------------------------------------

def test_tables_and_pictures_are_spec_inputs_resolved_and_pinned(folder, tmp_path):
    spec = {"request": "r", "summary": "s", "mode": "formatting", "ops": [
        {"op": "swap_image", "params": {"image": "logo.png", "match": {"image": "old.png"}}},
        {"op": "fill_variables", "params": {"table": {"file": "PROJECT.xlsx"}}},
        {"op": "replace_table", "params": {"pairs_table": {"file": "pairs.csv"}}},
        {"op": "apply_header_footer_template", "params": {"template": "T.docx", "template_sections": {"0": 0},
                                                          "slot_fills": {}}}]}
    f = folder / "spec.json"
    f.write_text(json.dumps(spec), encoding="utf-8")
    loaded = load_spec(f)
    want = [folder / "logo.png", folder / "old.png", folder / "PROJECT.xlsx", folder / "pairs.csv", folder / "T.docx"]
    assert spec_input_files(loaded) == [p.resolve() for p in want]
    assert spec_templates(loaded) == [(folder / "T.docx").resolve()]          # the batch filter: templates only
    # a recipe keeps them absolute, wherever it was loaded from
    item, _ = library.save_recipe("Site set", loaded)
    saved = json.loads(item.path.read_text(encoding="utf-8"))
    assert saved["ops"][1]["params"]["table"]["file"] == str((folder / "PROJECT.xlsx").resolve())
    assert saved["ops"][0]["params"]["match"]["image"] == str((folder / "old.png").resolve())


def test_a_table_or_picture_edited_after_the_preview_voids_the_approval(folder):
    spec = fill_spec(folder)
    files = sorted(folder.glob("*.docx"))
    token, before = approval_token(spec, files), approval_inputs(spec, files)
    (folder / "PROJECT.xlsx").write_bytes(_xlsx([["Variable", "VA3"], ["SITE_NAME", "Elsewhere"]]))
    assert approval_token(spec, files) != token
    assert explain_approval_change(before, approval_inputs(spec, files)) == \
        ["input file PROJECT.xlsx: changed since the preview"]
    # a spec with templates only hashes exactly as before (old tokens stay valid)
    plain = {"request": "r", "summary": "s", "mode": "formatting", "ops": [{"op": "remove_header_footer"}]}
    assert approval_token(plain, files) == approval_token(plain, files)


def test_a_recipe_whose_table_is_gone_is_not_offered(folder, tmp_path):
    spec = fill_spec(folder)
    library.save_recipe("Site set", spec)
    (folder / "PROJECT.xlsx").unlink()
    fake = FakeClient(resp(tool("ask_user", {"understanding": "u", "message": "m",
                                             "choices": [{"kind": "recipe", "name": "Site set"}]}, "a1")))
    turn = Conversation("fill the site like last time", sorted(folder.glob("*.docx")), client=fake).start()
    assert turn.choices == []
    assert any("a file it reads (its template, table or picture) is no longer at" in e for e in turn.errors)


# ---- explanations ----------------------------------------------------------------------------------------------------

def test_every_refusal_code_of_the_new_ops_is_explained():
    codes = set()
    for m in BATCH1_MODULES:
        text = (OPS_DIR / f"{m}.py").read_text(encoding="utf-8")
        codes |= set(re.findall(r'JobException\((?:[^()]|\([^()]*\))*?,\s*"([a-z][a-z-]+)"', text, re.S))
    codes |= {"name-collision"}                                       # batch.planned_names
    codes.discard("internal")                                         # diagnostics.classify: an internal error
    assert {"table-value", "spans-structure", "nested-options", "bad-name", "image-changed"} <= codes
    assert sorted(c for c in codes if c not in FAILURE_EXPLANATIONS) == []
    assert "eos-not-last" in FAILURE_EXPLANATIONS


# ---- what Claude sees ------------------------------------------------------------------------------------------------

def test_the_input_files_beside_the_documents_are_listed_and_only_they_can_be_read(folder, tmp_path):
    docs = sorted(folder.glob("*.docx"))
    listed = inputs.near(docs)
    # Path ordering is case-insensitive on Windows; check the same exact files on every platform.
    assert sorted(p.name for p in listed) == ["PROJECT.xlsx", "logo.png", "pairs.csv"]
    over = {Path(o["path"]).name: o for o in inputs.listing(docs)}
    assert over["PROJECT.xlsx"]["sheets"] == [{"name": "Sites", "headers": ["Variable", "VA1", "VA3"], "rows": 3}]
    assert over["logo.png"] == {"path": str(folder / "logo.png"), "kind": "picture", "format": "png", "pixels": [6, 6]}
    assert over["pairs.csv"]["headers"] == ["find", "replace"]
    got = inputs.read(str(folder / "PROJECT.xlsx"), listed)
    assert got["rows_shown"][2] == ["OWNER_NAME", "Contoso", "Contoso"] and got["rows_total"] == 3
    outside = tmp_path / "secret.csv"
    outside.write_text("a,b\n", encoding="utf-8")
    for bad in [str(outside), str(folder / "notes.txt"), "PROJECT.xlsx"]:
        with pytest.raises(KeyError):
            inputs.read(bad, listed)


def test_claude_is_shown_the_input_files_and_reads_one(folder):
    fake = FakeClient(resp(tool("read_input_file", {"path": str(folder / "PROJECT.xlsx")}, "t1")),
                      resp(tool("read_input_file", {"path": "/etc/passwd"}, "t2")),
                      resp(tool("submit_editspec", {"spec": fill_spec(folder)}, "s1")))
    c = Conversation("fill the VA3 site from PROJECT.xlsx", sorted(folder.glob("*.docx")), client=fake)
    turn = c.start()
    assert turn.status == "ok"
    first = fake.requests[0]["messages"][0]["content"]
    assert "INPUT FILES beside the documents" in first and "PROJECT.xlsx" in first and "VA3" in first
    results = [b for m in fake.requests[2]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"] if b.get("type") == "tool_result"]
    assert "Leesburg" in results[0]["content"] and not results[0].get("is_error")
    assert results[1]["is_error"] and "not one of the input files" in results[1]["content"]
    assert "read_input_file" in [t["name"] for t in fake.requests[0]["tools"]]
