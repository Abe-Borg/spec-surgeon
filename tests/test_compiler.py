"""Phase 4: the compiler loop (scripted fake client — no API calls), the submission checks (validation, traceable
words, dry plan), the preview and the approval token."""
from __future__ import annotations

import copy
import io
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
import zipfile

from lxml import etree
import pytest

from make_fixtures import build, build_batch1, build_reference_spec, build_rich, png
from spec_surgeon import SKILL_DIR, compiler, compiler as C, vision
from spec_surgeon.catalog import describe_op, op_catalog
from spec_surgeon.compiler import (_Batch, build_messages, check_submission, compile_request, file_labels, placed_words,
                                   system_prompt, untraceable)
from spec_surgeon.conversation import Conversation
from spec_surgeon.editspec import load_spec
from spec_surgeon.ops import end_of_section, get_op
from spec_surgeon.package import W
from spec_surgeon.preview import approval_inputs, approval_token, explain_approval_change, make_preview
from test_adaptive_messy import _build_messy_inline_spec
from test_text_edits import _xlsx

SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text(encoding="utf-8"))


from fake_claude import FakeClient, resp, text, tool  # noqa: F401 — re-exported for the other test modules


def remove_outcome_proposal(*, kinds=None):
    """Script the independent planner, separately from the editing recipe."""
    effect = {"id": "headers-footers", "type": "remove_header_footer"}
    if kinds is not None:
        effect["kinds"] = kinds
    description = " and ".join(kind + "s" for kind in (kinds or ["header", "footer"]))
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [effect]},
        "rationale": f"Remove {description} from every original section."}))


def header_outcome_proposal():
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [
        {"id": "header-distance", "type": "page_setup", "sections": "all", "margins": {"header": 900}}]},
        "rationale": "Place every section's header 900 twips from the top."}))


def generate_reviewed_custom(session, *, choice_id=None, log=lambda _: None, render=False):
    """Choosing a custom method prepares it immediately; its preview is the consent decision."""
    choice_id = choice_id or session.turn.choices[0].id
    message = session.reply(choice_id=choice_id, log=log, render=render)
    assert session.generated is not None and session.pending_custom_transform is None
    return message


@pytest.fixture
def folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(3):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def files(d):
    return sorted(d.glob("*.docx"))


def test_compile_inspects_then_submits(folder):
    fake = FakeClient(resp(tool("inspect_profile", {"pattern_id": "b20e6c1da8"})),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok" and res.spec == REMOVE, res.errors
    assert [c["tool"] for c in res.tool_calls] == ["inspect_profile", "submit_editspec"]
    first = fake.requests[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default" and first["max_tokens"] == 128000
    assert "OPERATIONS" in first["system"] and "apply_header_footer_template" in first["system"]
    assert "slot_fills" in first["system"]          # every op's parameters ride the prompt: no describe_op round trip
    assert "remove all headers and footers" in first["messages"][0]["content"]
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and "21 00 00" in result["content"]          # the profile came back
    assert res.usage["input_tokens"] == 200
    assert res.served_models == ["claude-opus-5-5"] and res.to_dict()["served_models"] == ["claude-opus-5-5"]


def test_traceable_words_come_from_one_source_each():
    spec = {"ops": [{"op": "replace_text", "params": {"find": "a", "replace": "ISSUED FOR PERMIT"}}]}
    assert untraceable(spec, ["ISSUED FOR", "PERMIT"]) == ["ISSUED FOR PERMIT"]      # not stitched across sources
    assert untraceable(spec, ["the stamp: ISSUED FOR  PERMIT"]) == []                  # whitespace-normalised


@pytest.mark.parametrize("placed", ["shall not",           # the end of "shall notify"
                                    "Contractor shall not",
                                    "50",                  # inside "500"
                                    "00 gpm at 1",         # cut out of "500 gpm at 120 ft"
                                    "Own",                 # the start of "Owner"
                                    "act"])                # inside "Contractor"
def test_words_are_never_lifted_from_inside_a_longer_word_or_number(placed):
    """Review of batch 6: tracing was a plain substring test, so a fragment of the user's words passed as theirs — and
    a typed table traces every cell on its own, so a row could be built from fragments."""
    request = ("In 01 23 00, change 'The Contractor shall notify the Owner' to read 'The Contractor shall notify the "
               "Architect' and set the pump to 500 gpm at 120 ft.")
    for spec in ({"ops": [{"op": "replace_text", "params": {"find": "x", "replace": placed}}]},
                 {"ops": [{"op": "insert_paragraph", "params": {"text": placed}}]},
                 {"ops": [{"op": "insert_table_rows", "params": {"rows": [["FP-1", placed]]}}]}):
        assert placed in untraceable(spec, [request]), (placed, spec)


def test_whole_words_still_trace_wherever_they_stand():
    request = "Change 'shall notify' to 'shall not delay'; add (SPRINKLERS) after FIRE-SUPPRESSION, 500 gpm."
    for placed in ("shall not",                 # a later occurrence stands alone though the first is inside a word
                   "shall not delay",
                   "Change",                    # at the start of the source
                   "500 gpm.",                  # at the end, ending with punctuation
                   "500",                       # a number before a space
                   "(SPRINKLERS)",              # bounded by its own punctuation
                   "FIRE",                      # a hyphenated word's part is a word
                   "SUPPRESSION, 500"):
        spec = {"ops": [{"op": "replace_text", "params": {"find": "x", "replace": placed}}]}
        assert untraceable(spec, [request]) == [], placed
    assert untraceable({"ops": [{"op": "replace_text", "params": {"find": "x", "replace": "Café"}}]},
                       ["the Cafés"]) == ["Café"]                                 # letters beyond ASCII are letters
    for placed, source in (("Cafe", "the Cafe\u0301 menu"),                     # a decomposed accent is the word's
                           ("\u0928\u092e\u0938", "\u0928\u092e\u0938\u094d\u0924\u0947")):  # an Indic vowel sign too
        assert untraceable({"ops": [{"op": "replace_text", "params": {"find": "x", "replace": placed}}]},
                           [source]) == [placed]
    assert untraceable({"ops": [{"op": "replace_text", "params": {"find": "x", "replace": "\t"}}]},
                       ["anything"]) == []                                        # whitespace alone places no words


def test_a_rejected_spec_goes_back_once(folder):
    bad = copy.deepcopy(REMOVE)
    bad["ops"][0]["op"] = "remove_headers"
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    err = fake.requests[1]["messages"][-1]["content"][0]
    assert err["is_error"] and "unknown op 'remove_headers'" in err["content"]
    fake = FakeClient(resp(tool("submit_editspec", {"spec": bad})), resp(tool("submit_editspec", {"spec": bad}, "t2")),
                      resp(tool("submit_editspec", {"spec": bad}, "t3")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "failed" and "stalled" in res.errors[0]


def test_dry_plan_catches_spec_level_mistakes(folder):
    wrong_mode = dict(copy.deepcopy(REMOVE), mode="content")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": wrong_mode})), resp(tool("submit_editspec", {"spec": REMOVE}, "t2")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    assert "spec-mode" in fake.requests[1]["messages"][-1]["content"][0]["content"]


def _replace_spec(words):
    return {"request": "r", "summary": "s", "mode": "content", "assumptions": [], "questions": [],
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": words, "tracked": True, "author": "Abraham Borg"}}]}


def test_words_the_user_never_gave_are_refused_and_the_model_must_ask(folder):
    invented = _replace_spec("Global Risk Consultants (GRC)")
    fake = FakeClient(resp(tool("submit_editspec", {"spec": invented})),
                      resp(tool("ask_user", {"questions": ["What exact wording should replace 'FM Global'?"]}, "t2")))
    res = compile_request("change FM Global to the GRC wording", files(folder), client=fake)
    assert res.status == "questions" and "exact wording" in res.questions[0]
    assert "never gave them verbatim" in fake.requests[1]["messages"][-1]["content"][0]["content"]
    # the same words, once the user supplied them, are fine
    fake = FakeClient(resp(tool("submit_editspec", {"spec": invented})))
    res = compile_request("change FM Global to the GRC wording", files(folder), client=fake,
                          answers=["use 'Global Risk Consultants (GRC)'"])
    assert res.status == "ok", res.errors
    assert untraceable(invented, ["Global  Risk Consultants\n(GRC)"]) == []           # whitespace-insensitive


def test_outcomes_questions_unsupported_refusal_and_silence(folder):
    q = dict(copy.deepcopy(REMOVE), questions=["Keep the cover footer?"])
    assert compile_request("remove headers", files(folder), client=FakeClient(resp(tool("submit_editspec", {"spec": q})))
                           ).questions == ["Keep the cover footer?"]
    u = {"request": "renumber", "summary": "s", "unsupported": "no numbering op yet", "proposal": "renumber_scheme"}
    res = compile_request("renumber", files(folder), client=FakeClient(resp(tool("submit_editspec", {"spec": u}))))
    assert res.status == "unsupported" and res.spec["proposal"] == "renumber_scheme"
    refusal = resp(stop="refusal")
    refusal.stop_details = SimpleNamespace(category="cyber", explanation="no")
    res = compile_request("x", files(folder), client=FakeClient(refusal))
    assert res.status == "failed" and "declined" in res.errors[0]
    res = compile_request("x", files(folder), client=FakeClient(resp(text("ok"), stop="end_turn"),
                                                                 resp(text("done"), stop="end_turn")))
    assert res.status == "failed" and "without submitting" in res.errors[0]


def test_tools_read_the_batch_and_bad_tool_input_is_an_error_result(folder):
    name = files(folder)[0].name
    fake = FakeClient(resp(tool("list_styles", {"file": name}), tool("sample_paragraphs", {"file": name, "regex": "^SECTION"}, "t2"),
                           tool("show_part_text", {"file": name, "part": "word/footer1.xml"}, "t3"),
                           tool("show_part_text", {"file": "nope.docx", "part": "x"}, "t4")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "t5")))
    res = compile_request("remove all headers and footers", files(folder), client=fake)
    assert res.status == "ok"
    results = fake.requests[1]["messages"][-1]["content"]
    assert len(results) == 4                                                       # all results in one message
    assert '"Normal"' in results[0]["content"] and "SECTION 21 00 00" in results[1]["content"]
    assert "21 00 00 - 1" in results[2]["content"] and results[3]["is_error"]


def test_preview_and_approval_token(folder, tmp_path):
    spec = load_spec(SPECS / "remove-all-headers-footers.json")
    pv = make_preview(spec, [str(folder)], tmp_path / "pv", do_render=False)
    assert len(pv.samples) == 2 and all(s.passed for s in pv.samples)
    assert (tmp_path / "pv" / "spec.json").is_file() and (tmp_path / "pv" / "PREVIEW.md").is_file()
    assert "rendering skipped" in pv.warnings[0] and pv.token in pv.to_text()
    assert pv.token == approval_token(spec, files(folder))
    changed = dict(spec, summary="different")
    assert approval_token(changed, files(folder)) != pv.token
    f = files(folder)[0]
    f.write_bytes(f.read_bytes() + b"")
    import os
    os.utime(f, ns=(f.stat().st_atime_ns, f.stat().st_mtime_ns + 10_000_000))
    assert approval_token(spec, files(folder)) != pv.token                          # a touched file voids it


@pytest.mark.render
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_preview_renders_side_by_side_images(folder, tmp_path):
    pv = make_preview(load_spec(SPECS / "remove-all-headers-footers.json"), [str(folder)], tmp_path / "pv")
    assert pv.renderer == "libreoffice" and any("Word's layout can differ" in w for w in pv.warnings)
    assert pv.samples[0].images and pv.samples[0].images[0].is_file()


# ---- words a built-in operation places, and the inputs the approval covers -------------------------------------------

@pytest.fixture
def inputs_folder(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    (d / "211319 - Preaction.docx").write_bytes(build_batch1())
    (d / "PROJECT.xlsx").write_bytes(_xlsx([["Variable", "VA1", "VA3"], ["SITE_NAME", "Ashburn", "Leesburg"],
                                            ["OWNER_NAME", "Contoso", "Contoso"], ["PROJECT NO", "1001", "1003"]]))
    (d / "pairs.csv").write_text("find,replace\nOwner,Client\n", encoding="utf-8")
    (d / "logo.png").write_bytes(png((1, 2, 3), size=6))
    return d


def fill_spec(inputs_folder, **values):
    params = {"table": {"file": str(inputs_folder / "PROJECT.xlsx"), "name_column": "Variable", "value_column": "VA3"},
              "author": "A"}
    if values:
        params["values"] = values
    return {"request": "fill the VA3 site", "summary": "s", "mode": "content", "ops": [
        {"op": "fill_variables", "params": params}]}


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


def test_a_table_or_picture_edited_after_the_preview_voids_the_approval(inputs_folder):
    spec = fill_spec(inputs_folder)
    files = sorted(inputs_folder.glob("*.docx"))
    token, before = approval_token(spec, files), approval_inputs(spec, files)
    (inputs_folder / "PROJECT.xlsx").write_bytes(_xlsx([["Variable", "VA3"], ["SITE_NAME", "Elsewhere"]]))
    assert approval_token(spec, files) != token
    assert explain_approval_change(before, approval_inputs(spec, files)) == \
        ["input file PROJECT.xlsx: changed since the preview"]
    # a spec with templates only hashes exactly as before (old tokens stay valid)
    plain = {"request": "r", "summary": "s", "mode": "formatting", "ops": [{"op": "remove_header_footer"}]}
    assert approval_token(plain, files) == approval_token(plain, files)


# ---- the clean-up operations in the prompt and the method ------------------------------------------------------------

def test_the_words_batch2_places_are_traced_to_the_user():
    spec = {"request": "r", "summary": "s", "mode": "untracked", "ops": [
        {"op": "add_watermark", "params": {"text": "NOT FOR CONSTRUCTION"}},
        {"op": "set_document_properties", "params": {"set": {"title": "Fire Suppression", "company": "ACME"},
                                                     "clear": ["author"], "custom": {"Project Number": "P-1003"}}},
        {"op": "remove_comments", "params": {"which": "all"}},
        {"op": "resolve_tracked_changes", "params": {"action": "accept", "authors": ["Jeremy"]}},
        {"op": "set_document_state", "params": {"final": True}},
    ]}
    assert placed_words(spec) == ["NOT FOR CONSTRUCTION", "Fire Suppression", "ACME", "P-1003"]
    assert untraceable(spec, ["watermark NOT FOR CONSTRUCTION, title Fire Suppression, company ACME"]) == ["P-1003"]


def test_claude_is_told_when_the_batch2_ops_may_be_used():
    prompt = system_prompt(chat=True)
    assert "resolve_tracked_changes accepts or rejects tracked changes ONLY when the user asks" in prompt
    assert "a watermark's words and a property's value are the user's exact" in prompt


def test_the_method_reads_as_abraham_changed_it():
    skill = (Path(SKILL_DIR) / "SKILL.md").read_text(encoding="utf-8")
    assert "on the model's own initiative" in skill and "resolve_tracked_changes" in skill
    assert "by the model. The user accepts and rejects in Word." not in skill
    elements = (Path(SKILL_DIR) / "references" / "element-types.md").read_text(encoding="utf-8")
    assert "never toggle it on your own" in elements and "Mark as Final" in elements


ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "specs"
WORKED = ("notes-boxed-and-shaded", "style-spacing", "clear-direct-formatting", "language-us-english",
          "bold-caps-to-heading", "architect-styles-to-office", "remove-unused-styles")


def test_the_batch4_ops_place_no_words():
    for name in WORKED:
        assert compiler.placed_words(load_spec(EXAMPLES / f"{name}.json")) == [], name


HF_SPEC = EXAMPLES / "match-template-spec-header-footer.json"


def _spec(request, mode, ops, **extra):
    return {"request": request, "summary": request, "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops, **extra}


@pytest.fixture
def project(tmp_path):
    """Two specs and the architect's spec as the template, with a spec file that names it."""
    folder = tmp_path / "specs"
    folder.mkdir()
    (folder / "21 13 13 – WET-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    (folder / "21 13 16 – DRY-PIPE SPRINKLER SYSTEMS.docx").write_bytes(build("21 13 16", "DRY-PIPE SPRINKLER SYSTEMS"))
    tpl = tmp_path / "ACME.docx"
    tpl.write_bytes(build_reference_spec())
    spec = load_spec(HF_SPEC)
    spec["ops"][0]["params"]["template"] = str(tpl)
    spec_file = tmp_path / "spec.json"
    spec_file.write_text(json.dumps(spec), encoding="utf-8")
    return folder, tpl, spec, spec_file


def test_approval_token_changes_when_the_template_changes(project):
    folder, tpl, spec, _ = project
    files = sorted(folder.glob("*.docx"))
    before = approval_token(spec, files)
    assert before == approval_token(spec, files)                       # stable
    st = tpl.stat()
    os.utime(tpl, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # touched (saved again, same bytes)
    touched = approval_token(spec, files)
    assert touched != before
    # different bytes of the same size with the mtime put back: the content hash still voids the token
    data = bytearray(tpl.read_bytes())
    data[-1] ^= 0xFF
    tpl.write_bytes(bytes(data))
    os.utime(tpl, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert approval_token(spec, files) not in (before, touched)
    # a spec without a template is unaffected by template files
    plain = _spec("x", "formatting", [{"op": "remove_header_footer", "select": {"sections": "all"}, "params": {}}])
    assert approval_token(plain, files) == approval_token(plain, files)


AUTHOR = "Abraham Borg"


def test_compiler_gate_rejects_a_spec_that_plans_on_no_file_whatever_the_refusal_code(tmp_path):
    only_messy = tmp_path / "messy"; only_messy.mkdir()
    (only_messy / "21 13 13 – MESSY.docx").write_bytes(_build_messy_inline_spec())
    spec = {"request": "replace FM Global with GRC", "summary": "s", "mode": "content",
            "ops": [{"op": "replace_text", "select": {"scope": ["document"]},
                     "params": {"find": "FM Global", "replace": "GRC", "author": AUTHOR}}]}
    errs = C.check_submission(spec, C._Batch(sorted(only_messy.glob("*.docx")), None), [spec["request"]])
    assert errs and "[spans-runs]" in " ".join(errs) and "MESSY" in " ".join(errs)
    rich = tmp_path / "rich"; rich.mkdir(); (rich / "rich.docx").write_bytes(build_rich())
    spec_tf = {"request": "replace Hangers with Supports", "summary": "s", "mode": "content",
               "ops": [{"op": "replace_text", "select": {},
                        "params": {"find": "Hangers", "replace": "Supports", "author": AUTHOR}}]}
    assert "[tracked-format]" in " ".join(C.check_submission(spec_tf, C._Batch([rich / "rich.docx"], None), [spec_tf["request"]]))
    assert C.check_submission(spec, C._Batch([], None), [spec["request"]]) == ["the batch has no .docx files to plan on"]


def _spec_with_mode(mode, ops):
    return {"request": "x", "summary": "x", "assumptions": [], "questions": [], "applies_to": {"or": "all"},
            "mode": mode, "ops": ops}


def _rewrite(data: bytes, part: str, fn) -> bytes:
    """``data`` with ``fn(root)`` applied to the XML of ``part``."""
    zin = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            b = zin.read(info.filename)
            if info.filename == part:
                root = etree.fromstring(b)
                fn(root)
                b = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, b)
    return out.getvalue()


def test_the_compile_gate_tries_every_file_of_a_pattern_before_rejecting(tmp_path):
    def nfpa14(root):
        for t in root.iter(W + "t"):
            if t.text and "NFPA 13" in t.text:
                t.text = t.text.replace("NFPA 13", "NFPA 14")
    (tmp_path / "A.docx").write_bytes(_rewrite(build("21 00 13", "HOTEL"), "word/document.xml", nfpa14))
    (tmp_path / "B.docx").write_bytes(build("21 00 14", "INDIA"))
    spec = _spec_with_mode("content", [{"op": "replace_text",
                                        "params": {"find": "NFPA 13", "replace": "NFPA 13 (2025)"}}])
    for order in (["A.docx", "B.docx"], ["B.docx", "A.docx"]):
        b = _Batch([tmp_path / n for n in order], None)
        assert len(b.prof["patterns"]) == 1
        assert check_submission(spec, b, ["NFPA 13 (2025)"]) == []
    nowhere = _spec_with_mode("content", [{"op": "replace_text",
                                           "params": {"find": "NFPA 99", "replace": "NFPA 13 (2025)"}}])
    errs = check_submission(nowhere, _Batch([tmp_path / "A.docx", tmp_path / "B.docx"], None), ["NFPA 13 (2025)"])
    assert errs and "none of this pattern's 2 files plans" in errs[1]
    # two folders, one file name: the gate goes by path, so the file the name map shadows is still tried
    for d in ("a", "b"):
        (tmp_path / d).mkdir()
    (tmp_path / "a" / "Spec.docx").write_bytes(build("21 00 14", "INDIA"))                        # has NFPA 13
    (tmp_path / "b" / "Spec.docx").write_bytes(_rewrite(build("21 00 13", "HOTEL"), "word/document.xml", nfpa14))
    b = _Batch([tmp_path / "a" / "Spec.docx", tmp_path / "b" / "Spec.docx"], None)
    assert b.prof["patterns"][0]["paths"] == [str(tmp_path / "a" / "Spec.docx"), str(tmp_path / "b" / "Spec.docx")]
    assert check_submission(spec, b, ["NFPA 13 (2025)"]) == []


# ---- files that share a name in different folders --------------------------------------------------------------------

@pytest.fixture
def two_specs(tmp_path) -> Path:
    d = tmp_path / "specs"
    d.mkdir()
    for i in range(2):
        (d / f"21 0{i} 00 – SPEC {i}.docx").write_bytes(build(f"21 0{i} 00", f"SPEC {i}"))
    return d


def _two_folders(tmp_path) -> list:
    for sub, title in (("a", "ALPHA"), ("b", "BRAVO")):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "Spec.docx").write_bytes(build("21 00 00", title))
    (tmp_path / "a" / "Other.docx").write_bytes(build("21 00 01", "OTHER"))
    return [tmp_path / "a" / "Spec.docx", tmp_path / "b" / "Spec.docx", tmp_path / "a" / "Other.docx"]


def test_file_labels_are_names_unless_two_files_share_one():
    assert file_labels([Path("p/A.docx"), Path("p/B.docx")]) == {str(Path("p/A.docx")): "A.docx",
                                                                  str(Path("p/B.docx")): "B.docx"}
    got = file_labels([Path("a/x/Spec.docx"), Path("b/x/Spec.docx"), Path("c/Spec.docx"), Path("c/Other.docx")])
    assert got == {str(Path("a/x/Spec.docx")): "a/x/Spec.docx", str(Path("b/x/Spec.docx")): "b/x/Spec.docx",
                   str(Path("c/Spec.docx")): "c/Spec.docx", str(Path("c/Other.docx")): "Other.docx"}
    assert len(set(got.values())) == len(got)
    assert file_labels([Path("p/A.docx"), Path("p/A.docx")]) == {str(Path("p/A.docx")): "A.docx"}   # one file


def test_every_tool_reads_the_file_its_label_names(tmp_path, monkeypatch):
    files = _two_folders(tmp_path)
    b = _Batch(files, None)
    assert list(b.files) == ["a/Spec.docx", "b/Spec.docx", "Other.docx"]
    assert [b.files[k] for k in b.files] == files
    for label, title in (("a/Spec.docx", "ALPHA"), ("b/Spec.docx", "BRAVO")):
        assert title in json.dumps(b.sample_paragraphs(label, n=3))
        assert title in json.dumps(b.show_part_text(label, "word/document.xml"))
        assert title in json.dumps(b.find_paragraphs(label, title))
        assert title in b.show_xml(label, start=0, count=3)["xml"]
        assert isinstance(b.list_styles(label), list)
    assert "BRAVO" not in json.dumps(b.sample_paragraphs("a/Spec.docx", n=3))
    with pytest.raises(KeyError, match=r"a/Spec\.docx"):
        b.sample_paragraphs("Spec.docx")                                    # the bare name is ambiguous: refused
    seen = []

    def fake_pngs(path, pages, work_dir, renderer="auto"):
        seen.append(Path(path))
        return []
    monkeypatch.setattr(vision, "page_pngs", fake_pngs)
    b.work_dir = tmp_path / "chat"
    with pytest.raises(ValueError):
        b.view_pages("b/Spec.docx")
    assert seen == [tmp_path / "b" / "Spec.docx"]


def test_the_summary_and_profiles_claude_sees_use_the_labels(tmp_path):
    files = _two_folders(tmp_path)
    b = _Batch(files, None)
    [pat] = b.summary()["patterns"]
    assert pat["files"] == ["a/Spec.docx", "b/Spec.docx", "Other.docx"] and pat["profile"]["file"] == "a/Spec.docx"
    assert b.inspect_profile(pat["pattern_id"])["files"] == pat["files"]
    msg = build_messages("remove all headers and footers", b, [], {})[0]["content"]
    assert '"a/Spec.docx"' in msg and '"b/Spec.docx"' in msg
    assert [str(p) for p in b.pattern_paths(b.prof["patterns"][0])] == [str(f) for f in files]


def test_a_single_folder_batch_reads_as_before(two_specs):
    files = sorted(two_specs.glob("*.docx"))
    b = _Batch(files, None)
    assert list(b.files) == [f.name for f in files]
    assert b.summary()["patterns"][0]["files"] == [f.name for f in files]
    assert b.summary()["patterns"][0]["profile"]["file"] == files[0].name


def test_unprofiled_and_unreadable_files_are_named_by_label(tmp_path):
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    def bad_margin(root):
        for m in root.iter(W + "pgMar"):
            m.set(W + "left", "abc")
    for sub in ("a", "b", "c"):
        (tmp_path / sub).mkdir()
    (tmp_path / "a" / "Spec.docx").write_bytes(_rewrite(build("21 00 15", "JULIET"), "word/document.xml", bad_margin))
    (tmp_path / "b" / "Spec.docx").write_bytes(build("21 00 16", "KILO"))
    (tmp_path / "c" / "Spec.docx").write_bytes(b"not a zip")
    b = _Batch([tmp_path / "a" / "Spec.docx", tmp_path / "b" / "Spec.docx", tmp_path / "c" / "Spec.docx"], None)
    [pat] = b.prof["patterns"]
    assert pat["files"] == ["b/Spec.docx", "a/Spec.docx"] and pat["profile"]["file"] == "b/Spec.docx"
    assert pat["unprofiled"][0].startswith("a/Spec.docx: ")
    assert b.prof["unreadable"][0]["file"] == "c/Spec.docx"
    alone = _Batch([tmp_path / "a" / "Spec.docx", tmp_path / "c" / "Spec.docx"], None)
    [pat] = alone.prof["patterns"]
    assert pat["profile"]["error"] and pat["profile"]["files"][0].startswith("a/Spec.docx: ")
    assert pat["unprofiled"][0].startswith("a/Spec.docx: ")


def test_the_gate_s_errors_name_files_by_label(tmp_path):
    files = _two_folders(tmp_path)
    spec = {"request": "r", "summary": "s", "mode": "content", "ops": [{
        "op": "replace_text", "select": {"scope": ["document"]},
        "params": {"find": "NFPA 99", "replace": "NFPA 13 (2025)", "tracked": True, "author": "x"}}]}
    errs = check_submission(spec, _Batch(files, None), ["NFPA 13 (2025)"])
    assert errs and errs[1].startswith("a/Spec.docx: [") and "e.g. b/Spec.docx: [" in errs[1]


def test_claude_addresses_the_second_same_named_file_in_a_conversation(tmp_path):
    files = _two_folders(tmp_path)
    fake = FakeClient(resp(tool("sample_paragraphs", {"file": "b/Spec.docx", "n": 3}, "t1")),
                      resp(text("BRAVO is the second file."), stop="end_turn"))
    c = Conversation("remove all headers and footers", files, client=fake)
    assert c.start().status == "questions"
    result = fake.requests[1]["messages"][-1]["content"][0]
    assert result["tool_use_id"] == "t1" and "BRAVO" in result["content"] and "ALPHA" not in result["content"]
    assert '"b/Spec.docx"' in fake.requests[0]["messages"][0]["content"]


def test_preview_samples_with_the_same_name_keep_their_own_outputs(tmp_path):
    from spec_surgeon.editspec import load_spec, plan_spec
    from spec_surgeon.fingerprint import fingerprint
    from spec_surgeon.package import DocxPackage
    from spec_surgeon.preview import make_preview
    from spec_surgeon.verify import run_suite
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    a, b = tmp_path / "A" / "spec.docx", tmp_path / "B" / "spec.docx"
    a.write_bytes(build("21 13 13", "WET-PIPE"))
    b.write_bytes(build_rich())                                           # another structure pattern: both sampled
    spec = load_spec(SPECS / "change-all-font-arial.json")
    pv = make_preview(spec, [str(a), str(b)], tmp_path / "pv", do_render=False)
    assert [s.source for s in pv.samples] == [a, b] and all(s.passed for s in pv.samples)
    assert [s.output.name for s in pv.samples] == ["spec.docx", "spec (1).docx"]
    for s in pv.samples:                                                  # each output is its own source's
        pkg = DocxPackage(s.source.read_bytes(), name=str(s.source))
        plan, _ = plan_spec(pkg, spec, fingerprint(pkg)["pattern_id"])
        assert run_suite(pkg.data, s.output.read_bytes(), plan.to_dict(), str(s.source), str(s.output)).passed


def test_compiler_carries_every_operations_sheet_and_reads_the_rest_on_demand(tmp_path):
    """Every operation's selectors and parameters ride the cached system prompt (the op sheet), so a plan needs no
    round trip per operation; describe_op still returns an operation's verification rules and worked examples."""
    from spec_surgeon.catalog import op_sheet
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 13", "SPRINKLERS"))
    spec = {"request": "remove headers", "summary": "remove headers", "mode": "formatting",
            "ops": [{"op": "remove_header_footer", "select": {"sections": "all", "kinds": ["header"]}, "params": {}}]}
    fake = FakeClient(resp(tool("describe_op", {"name": "remove_header_footer"})),
                      resp(tool("submit_editspec", {"spec": spec}, "submit")))
    result = compiler.compile_request("remove headers", [source], client=fake)
    assert result.status == "ok", result.errors
    prompt = fake.requests[0]["system"]
    sheet = op_sheet()
    assert json.dumps(sheet["ops"]["remove_header_footer"]["select"]) in prompt or "remove_header_footer" in prompt
    assert len(prompt) < len(json.dumps(op_catalog()))          # the sheet is the catalog without its examples
    docs = json.loads(fake.requests[1]["messages"][-1]["content"][0]["content"])
    assert docs["params"] == get_op("remove_header_footer").params_schema
    assert docs["verification"] and docs["worked_examples"]
    assert all(any(op["op"] == "remove_header_footer" for op in example["ops"])
               for example in docs["worked_examples"])
    with pytest.raises(ValueError, match="Unknown operation"):
        describe_op("not_an_op")


# ---- every file a spec reads is one the user attached ----------------------------------------------------------------

def test_a_spec_reads_only_files_the_user_attached(tmp_path):
    """Review of batch 6: a table or picture a spec named did not have to be attached, and a table file's cells are
    placed without passing the words gate — any table file on the disk could put words in the documents."""
    from fx_table_structure import build_structure
    doc = tmp_path / "docs" / "21 13 13.docx"
    doc.parent.mkdir()
    doc.write_bytes(build_structure())
    attached, elsewhere = tmp_path / "attached" / "pumps.csv", tmp_path / "elsewhere" / "pumps.csv"
    for path in (attached, elsewhere):
        path.parent.mkdir()
        path.write_text("Tag,Description\nFP-1,Fire pump\n", encoding="utf-8")

    def table(path):
        return {"request": "add the pump table after 1.2 NOTES", "summary": "s", "mode": "content", "ops": [
            {"op": "insert_table", "select": {"paragraphs": {"text_regex": "^1\\.2 NOTES"}},
             "params": {"table": {"file": str(path)}}}]}
    batch = _Batch([doc], None, input_files=[attached])
    sources = ["add the pump table after 1.2 NOTES"]
    assert check_submission(table(attached), batch, sources) == []
    errs = check_submission(table(elsewhere), batch, sources)
    assert len(errs) == 1 and errs[0].startswith("[input]") and str(elsewhere) in errs[0]
    stages = {"summary": "s", "stages": [{"name": "one", "spec": table(attached)}, {"name": "two", "spec": table(elsewhere)}]}
    assert check_submission(stages, batch, sources)[0].startswith("[input]")      # the model's own stages too
    picture = tmp_path / "elsewhere" / "logo.png"
    picture.write_bytes(png())
    image = {"request": "r", "summary": "s", "mode": "content", "ops": [
        {"op": "insert_image", "select": {"paragraphs": {"text_regex": "^1\\.2 NOTES"}}, "params": {"image": str(picture)}}]}
    assert check_submission(image, batch, sources)[0].startswith("[input]")
    garbled = copy.deepcopy(image)                                                # a path no file can have
    garbled["ops"][0]["params"]["image"] = "logo\x00.png"
    assert check_submission(garbled, batch, sources)[0].startswith("[input]")


def test_a_template_is_the_attached_one_or_one_of_the_documents(tmp_path):
    doc, template, stray = tmp_path / "a.docx", tmp_path / "template.docx", tmp_path / "other" / "template.docx"
    stray.parent.mkdir()
    for path in (doc, template, stray):
        path.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    reads = lambda path: {"ops": [{"op": "apply_header_footer_template", "params": {"template": str(path)}}]}  # noqa: E731
    assert C._attached_checks(reads(template), _Batch([doc], template)) == []
    assert C._attached_checks(reads(doc), _Batch([doc], None)) == []        # an attached document read as the template
    assert C._attached_checks(reads(stray), _Batch([doc], template))[0].startswith("[input]")
    assert C._attached_checks(reads(template), _Batch([doc], None, input_files=[template]))[0].startswith("[input]")


def test_the_opening_message_says_whether_a_template_is_attached(project):
    """The 1.8.0 live run had no template attached and Claude called read_template twice looking for one."""
    folder, tpl, _, _ = project
    files = sorted(folder.glob("*.docx"))
    without = build_messages("apply the template", _Batch(files, None), [], {})[0]["content"]
    assert "NO TEMPLATE FILE IS ATTACHED" in without and "ask the user before treating it as the template" in without
    with_template = build_messages("apply the template", _Batch(files, tpl), [], {})[0]["content"]
    assert "A TEMPLATE FILE IS ATTACHED" in with_template and "NO TEMPLATE" not in with_template
    with pytest.raises(ValueError, match="no template is attached"):
        _Batch(files, None).read_template()
