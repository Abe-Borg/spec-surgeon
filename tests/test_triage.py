"""Attach-time triage (``spec_surgeon.triage``): deterministic, model-free, milliseconds per file.

Pinned here: the clean fixture and every dirty kind (``make_fixtures.build_dirty``) triage to the expected state with
the expected sentence among the notes, and a defect's sentence appears only where the defect is; the refusals (not a
file, not a .docx, Word's ``~$`` lock file, a package that does not open, an empty body) each name the file and say
what to do; the facts carry the raw values; nothing in the module reaches the verifier or a model."""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from fx_comments import build_commented
from make_fixtures import DIRTY_KINDS, _drop_members, build, build_dirty, patch_docx
from spec_surgeon import llm
from spec_surgeon import triage as triage_module
from spec_surgeon.package import W
from spec_surgeon.triage import (NOTE_COMMENTS, NOTE_COMPAT_MODE, NOTE_EMPTY_BODY, NOTE_FRAGMENTED, NOTE_NORMAL_ONLY,
                                 NOTE_PROTECTED, NOTE_SPACING, NOTE_TEXTBOX_HF, NOTE_TYPED_NUMBERS, STATES, Triage,
                                 revisions_note, triage_file, triage_files)

CLEAN_BYTES = build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS")

# kind -> (state, the sentences that must be among the notes). The clean fixture is python-docx's template underneath
# (compatibilityMode 14), a Normal-only typed spec with two pending revisions: four notes, all true of the file.
EXPECTED = {
    "clean": ("note", {NOTE_COMPAT_MODE, NOTE_NORMAL_ONLY, NOTE_TYPED_NUMBERS, revisions_note(2)}),
    "fragmented_runs": ("note", {NOTE_FRAGMENTED}),
    "textbox_headers": ("note", {NOTE_TEXTBOX_HF}),
    "nbsp_spaces": ("note", {NOTE_SPACING}),
    "compat_mode": ("note", {NOTE_COMPAT_MODE}),
    "many_revisions": ("note", {"320 tracked changes are pending."}),
    "protected": ("note", {NOTE_PROTECTED}),
    "libreoffice_like": ("note", {NOTE_TYPED_NUMBERS}),
    "empty_body": ("note", {NOTE_EMPTY_BODY}),
    "styled": ("ok", set()),
}
KINDS = tuple(EXPECTED)


@pytest.fixture(scope="module")
def files(tmp_path_factory) -> dict:
    """{kind: path} — the clean fixture and every dirty kind, built once."""
    folder = tmp_path_factory.mktemp("triage")
    paths = {"clean": folder / "21 13 13 clean.docx"}
    paths["clean"].write_bytes(CLEAN_BYTES)
    for kind in DIRTY_KINDS:
        paths[kind] = folder / f"dirty-{kind}.docx"
        paths[kind].write_bytes(build_dirty(kind))
    return paths


@pytest.fixture(scope="module")
def results(files) -> dict:
    return {kind: triage_file(path) for kind, path in files.items()}


def test_every_kind_is_covered():
    assert set(KINDS) == {"clean", *DIRTY_KINDS}


# ---- the clean fixture and every dirty kind ------------------------------------------------------------------------

@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_triages_to_its_state_and_sentence(files, results, kind):
    state, sentences = EXPECTED[kind]
    found = results[kind]
    assert isinstance(found, Triage) and found.path == files[kind] and found.state == state and state in STATES
    assert sentences <= set(found.notes), found.notes
    assert found.pattern_id and found.pattern_id == found.facts["pattern_id"]   # the file opened: the pattern is known
    if state == "refused":
        assert found.refused and found.notes == [] and files[kind].name in found.reason
    else:
        assert not found.refused and found.reason == ""
    if state == "ok":
        assert found.notes == []
    assert len(found.notes) == len(set(found.notes))                            # no sentence twice


@pytest.mark.parametrize("kind", KINDS)
def test_notes_are_plain_single_sentences(results, kind):
    for note in results[kind].notes:
        assert note.endswith(".") and "\n" not in note and note.count(". ") == 0 and len(note) < 120, note


def test_each_defect_is_named_only_where_it_is(results):
    """A defect's sentence appears on the kind that carries it and not on the others — the pinned exception being the
    fragmentation note on ``many_revisions``: its four-run tracked paragraphs put the body at 3.67 runs per paragraph,
    above the 2.5 that reads as heavy editing (which is what hundreds of tracked changes are)."""
    def where(sentence):
        return {kind for kind, found in results.items() if sentence in found.notes}
    assert where(NOTE_PROTECTED) == {"protected"}
    assert where(NOTE_COMPAT_MODE) == {"compat_mode", "clean"}
    assert where(NOTE_TEXTBOX_HF) == {"textbox_headers"}
    assert where(NOTE_SPACING) == {"nbsp_spaces"}
    assert where(NOTE_FRAGMENTED) == {"fragmented_runs", "many_revisions"}
    assert where(NOTE_COMMENTS) == set()
    revised = {kind for kind, found in results.items() if any(n.endswith("pending.") for n in found.notes)}
    assert revised == {"many_revisions", "clean"}
    noted = {kind for kind, (state, _) in EXPECTED.items() if state == "note"}
    assert where(NOTE_NORMAL_ONLY) == noted - {"libreoffice_like", "empty_body"}   # LibreOffice styles its paragraphs
    assert where(NOTE_TYPED_NUMBERS) == noted - {"empty_body"}                  # every noted kind with text types its numbers
    assert where(NOTE_EMPTY_BODY) == {"empty_body"}


@pytest.mark.parametrize("kind", KINDS)
def test_facts_carry_the_raw_values(results, kind):
    f = results[kind].facts
    assert f["compatibility_mode"] == (14 if kind in ("compat_mode", "clean") else 15)
    assert f["protection_enforced"] == (kind == "protected")
    assert f["protection_edit"] == ("readOnly" if kind == "protected" else None)
    assert f["pending_revisions"] == {"many_revisions": 320, "clean": 2}.get(kind, 0)
    assert f["track_revisions"] == (kind in ("many_revisions", "clean"))        # both were saved with tracking on
    assert f["textboxes_in_headers_footers"] == (2 if kind == "textbox_headers" else 0)
    assert f["style_regime"] == ("styled" if kind in ("styled", "libreoffice_like") else "normal_only")
    assert f["numbering_regime"] == {"styled": "mixed", "empty_body": "none"}.get(kind, "manual_text")
    assert (f["run_fragmentation_ratio"] > 4) == (kind == "fragmented_runs")
    assert f["has_nbsp"] == f["has_multi_space"] == (kind == "nbsp_spaces")
    assert f["has_comments"] is False and f["body_has_text"] == (kind != "empty_body")
    assert f["headers"] and f["footers"] and all(h.startswith("word/") for h in f["headers"] + f["footers"])
    assert f["sections"] == (2 if kind == "clean" else 1)
    assert f["section_anchor_shape"] == ("missing" if kind == "empty_body" else "standalone")
    json.dumps(results[kind].to_dict())                                         # the record serializes as it is


def test_a_file_with_comments_is_noted(tmp_path):
    path = tmp_path / "commented.docx"
    path.write_bytes(build_commented())
    found = triage_file(path)
    assert found.state == "note" and NOTE_COMMENTS in found.notes and found.facts["has_comments"] is True
    assert revisions_note(1) == "1 tracked change is pending." and revisions_note(1) in found.notes
    assert revisions_note(2) == "2 tracked changes are pending."


# ---- refusals: one sentence naming the file and what to do ----------------------------------------------------------

def _refused(path, opened: bool = False) -> Triage:
    """``opened``: the package itself opened (an empty body), so its pattern is known; otherwise there is none."""
    found = triage_file(path)
    assert found.state == "refused" and found.refused and found.notes == [] and bool(found.pattern_id) == opened
    assert Path(path).name in found.reason and found.reason.endswith(".") and "\n" not in found.reason
    return found


def test_a_docm_or_doc_name_is_refused_before_it_is_opened(tmp_path):
    for name in ("21 13 13 macros.docm", "old spec.doc", "template.dotx", "notes.txt"):
        path = tmp_path / name
        path.write_bytes(CLEAN_BYTES)                                           # a real package under the wrong name
        found = _refused(path)
        assert ".docm and .doc files are not supported; save it as .docx in Word" in found.reason
        assert found.facts == {}
    upper = tmp_path / "UPPER.DOCX"                                             # Windows spells the suffix either way
    upper.write_bytes(CLEAN_BYTES)
    assert triage_file(upper).state == "note"


def test_a_text_file_renamed_docx_cannot_be_opened(tmp_path):
    path = tmp_path / "renamed.docx"
    path.write_text("This is a text file, not a Word package.\n", encoding="utf-8")
    found = _refused(path)
    assert "cannot be opened as a Word package" in found.reason and "save it again as .docx" in found.reason
    assert found.facts["error"].startswith("BadZipFile")


def test_a_truncated_package_and_a_package_without_its_document_part_cannot_be_opened(tmp_path):
    truncated = tmp_path / "truncated.docx"
    truncated.write_bytes(CLEAN_BYTES[: len(CLEAN_BYTES) // 2])
    assert "cannot be opened as a Word package" in _refused(truncated).reason
    hollow = tmp_path / "hollow.docx"
    hollow.write_bytes(_drop_members(CLEAN_BYTES, ["word/document.xml"]))
    found = _refused(hollow)
    assert "cannot be opened as a Word package" in found.reason and found.facts["error"].startswith("KeyError")


def test_a_strict_open_xml_package_is_refused_with_the_save_as_remedy(tmp_path):
    strict_type = "http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument"

    def strict(root):
        for rel in root:
            if rel.get("Type", "").endswith("/officeDocument"):
                rel.set("Type", strict_type)
    path = tmp_path / "strict.docx"
    path.write_bytes(patch_docx(CLEAN_BYTES, {"_rels/.rels": strict}))
    found = _refused(path)
    assert "Strict Open XML" in found.reason and "Word Document (*.docx)" in found.reason


def test_a_missing_path_and_a_folder_are_refused(tmp_path):
    missing = tmp_path / "gone.docx"
    found = _refused(missing)
    assert "was not found" in found.reason and str(tmp_path) in found.reason
    folder = tmp_path / "folder.docx"
    folder.mkdir()
    assert "is a folder, not a file" in _refused(folder).reason


def test_words_lock_file_is_refused_whether_or_not_it_exists(tmp_path):
    present = tmp_path / "~$21 13 13.docx"
    present.write_bytes(b"\x00" * 162)                                          # what Word's lock file holds
    absent = tmp_path / "~$gone.docx"
    for path in (present, absent):
        found = _refused(path)
        assert "lock file" in found.reason and "attach the .docx itself" in found.reason


def test_an_empty_body_is_taken_with_a_note_and_its_facts(files):
    """A document with nothing in its body is still a document the user may build on (paragraphs, tables, pictures)
    or whose headers, footers and page setup they may change: noted, never refused (2026-10-21, full control)."""
    found = triage_file(files["empty_body"])
    assert found.state == "note" and found.notes == [NOTE_EMPTY_BODY] and not found.refused and found.reason == ""
    assert found.pattern_id and found.facts["body_has_text"] is False and found.facts["headers"]


def test_a_body_without_a_paragraph_is_refused(files, tmp_path):
    """A body of nothing but its sectPr (Word never writes one) has no paragraph an insertion could anchor on."""
    def drop_paragraphs(root):
        body = root.find(f"{W}body")
        for p in body.findall(f"{W}p"):
            body.remove(p)
    path = tmp_path / "blockless.docx"
    path.write_bytes(patch_docx(files["empty_body"].read_bytes(), {"word/document.xml": drop_paragraphs}))
    found = _refused(path, opened=True)
    assert found.reason == f"{path.name} has no paragraph in its body to add to; open it in Word and save it again."
    assert found.facts["body_has_text"] is False


# ---- the batch call, the record, and what the module never touches ------------------------------------------------

def test_triage_files_keeps_the_order_and_triages_each_on_its_own(files, tmp_path):
    missing = tmp_path / "missing.docx"
    paths = [files["styled"], missing, files["protected"], files["empty_body"]]
    found = triage_files(paths)
    assert [t.path for t in found] == paths
    assert [t.state for t in found] == ["ok", "refused", "note", "note"]
    assert found == [triage_file(p) for p in paths]                           # deterministic, path by path


def test_to_dict_is_the_whole_record(results):
    d = results["protected"].to_dict()
    assert set(d) == {"path", "state", "notes", "reason", "pattern_id", "facts"}
    assert d["state"] == "note" and NOTE_PROTECTED in d["notes"] and d["facts"]["protection_edit"] == "readOnly"
    assert isinstance(d["path"], str) and json.loads(json.dumps(d)) == d
    assert d["notes"] is not results["protected"].notes                         # a copy, not the live list


def test_no_model_call_and_no_verifier_import(files, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("triage must never call a model")
    monkeypatch.setattr(llm, "call", boom)
    assert [t.state for t in triage_files(files.values())].count("refused") == 0
    source = Path(triage_module.__file__).read_text(encoding="utf-8")
    assert len(source.splitlines()) < 200
    forbidden = ("verify", "llm", "compiler", "conversation", "codegen", "advisor", "preview", "gui", "ops",
                 "editspec", "document_task", "vision")
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.ImportFrom):                                    # relative and absolute alike
            names += (node.module or "").split(".")
        elif isinstance(node, ast.Import):
            names += [part for alias in node.names for part in alias.name.split(".")]
        assert not any(n in forbidden for n in names), ast.dump(node)


def test_triage_takes_milliseconds(files):
    for path in files.values():
        start = time.perf_counter()
        triage_file(path)
        assert time.perf_counter() - start < 1.0, path.name                    # 3–12 ms measured; a loose ceiling
