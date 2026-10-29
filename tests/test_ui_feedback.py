"""User feedback comes from real verdicts/refusals; the original diagnostic evidence is preserved."""
import copy
import io
from types import SimpleNamespace

from docx import Document

from make_fixtures import build_dirty, patch_docx
from spec_surgeon import verify
from spec_surgeon.settings import Settings
from spec_surgeon.document_task import DocumentTask
from spec_surgeon.gui import Session
from spec_surgeon.ops.slots import REFERENCE_FILLS
from spec_surgeon.package import W
from spec_surgeon.ui_feedback import failed_preview_checks, refusal_message
from spec_surgeon.web.server import WindowState
from test_ui_preview_regressions import fail_checks, task_for


def test_naming_refusal_is_readable_before_and_after_create_without_changing_evidence(tmp_path):
    task, _ = task_for(tmp_path)
    empty = tmp_path / "empty.docx"
    empty.write_bytes(build_dirty("empty_body"))
    task.attach([empty])
    view = task.perform("recipe", recipe="name_files", inputs={})
    exception = task.session.preview.batch_plan.exceptions[0].exception
    saved = copy.deepcopy(exception)
    assert "pattern" in exception["reason"] and exception["code"] == "not-found"
    reason = view["set_aside"][0]["reason"]
    # what was looked for, what for, and what the document holds instead (the owner's run of 2026-10-29: "this file
    # does not contain the text or location needed for this change" named none of it)
    assert reason == ("No paragraph of this document starts with SECTION and its section number (like “SECTION 21 13 "
                      "13”), so its own section number cannot be read for the file name. The document has no text.")
    assert "op #" not in reason and "pattern" not in reason
    view = task.perform("primary", token=view["token"])
    assert view["left_untouched"][0]["reason"] == reason
    assert exception == saved


def test_watermark_refusal_explains_the_missing_header_in_the_preview(tmp_path):
    task, _ = task_for(tmp_path)
    view = task.perform("recipe", recipe="remove_headers_footers", inputs={})
    task.perform("primary", token=view["token"])
    task.session.out_root = tmp_path / "next-run"
    view = task.perform("recipe", recipe="watermark", inputs={"text": "DRAFT"})
    reason = view["set_aside"][0]["reason"]
    assert "no header to hold a watermark" in reason and "Word" in reason
    assert "op #" not in reason and "add_watermark" not in reason
    assert task.session.preview.batch_plan.exceptions[0].exception["code"] == "no-header"


def test_failed_checks_have_the_same_plain_explanation_in_streams_and_refreshes(tmp_path, monkeypatch):
    task, _ = task_for(tmp_path)
    fail_checks(monkeypatch, verify)
    task.perform("recipe", recipe="remove_highlight", inputs={})
    state = WindowState(task, Settings(), token="feedback-test")
    sample = state.view()["samples"][0]
    assert sample["passed"] is False
    assert sample["failures"] == [{"check": "V2", "reason": "The copy's wording does not match the change predicted by the plan."}]
    assert task._samples_view()["samples"][0]["failures"] == sample["failures"]


def test_a_later_passing_stage_does_not_hide_an_earlier_failed_check():
    sample = SimpleNamespace(passed=False, checks={"V2": "PASS"},
                             stages=[{"checks": {"V2": "FAIL"}}, {"checks": {"V2": "PASS"}}])
    assert [failure["check"] for failure in failed_preview_checks(sample)] == ["V2"]


def test_a_missing_renderer_is_not_a_failed_verification():
    assert failed_preview_checks(SimpleNamespace(passed=True, checks={"V6": "NA"}, stages=[])) == []


def test_a_missing_fill_value_is_named_and_a_template_slot_keeps_its_own_sentence():
    fill = {"code": "unfilled-slot", "reason": "op #1 (fill_variables): body paragraph 3: no value for '{{SITE_ADDRESS}}' "
                                               "(SITE_ADDRESS) — add it to the values",
            "details": {"where": "body paragraph 3", "slot": "SITE_ADDRESS", "op": "fill_variables", "op_number": 1}}
    reason = refusal_message(fill)
    assert reason == ('No value was given for "SITE_ADDRESS". Add it to the values or the attached table, or ask to '
                      'leave it as it is.')
    assert "section number" not in reason and "op #" not in reason and "fill_variables" not in reason
    template = {"code": "unfilled-slot",
                "reason": "op #1 (apply_header_footer_template): template slot(s) ['PROJECT'] have no fill rule",
                "details": {"slots": ["PROJECT"], "op": "apply_header_footer_template", "op_number": 1}}
    assert refusal_message(template) == ("The template shows “PROJECT” where each document's own value goes, and no "
                                         "value was given or could be read for it. Fill it in the form, or use a "
                                         "template without it.")


# ---- Left untouched says what was looked for (the owner's run of 2026-10-29) ----------------------------------------

def specific_docs(tmp_path):
    """A specification the template can take, one whose SECTION line reads otherwise, one with two section numbers at
    the start of a paragraph, and one with odd and even page headers the template lacks."""
    from make_fixtures import build, build_rich

    def section_line_reads_otherwise(root):
        for t in root.iter(f"{W}t"):
            t.text = (t.text or "").replace("SECTION 28 46 00", "DIVISION 28 - 284600")
    good = tmp_path / "good.docx"
    good.write_bytes(build("21 13 19", "PREACTION SPRINKLER SYSTEMS"))
    other = tmp_path / "other layout.docx"
    other.write_bytes(patch_docx(build("28 46 00", "FIRE DETECTION AND ALARM"),
                                 {"word/document.xml": section_line_reads_otherwise}))
    doc = Document(io.BytesIO(build("28 31 02", "FIRE ALARM")))
    doc.add_paragraph("Section 26 05 00 - Common Work Results")
    two = tmp_path / "two numbers.docx"
    doc.save(two)
    even = tmp_path / "even odd.docx"
    even.write_bytes(build_rich("23 05 00", "COMMON WORK RESULTS FOR HVAC"))      # evenAndOddHeaders on
    template = tmp_path / "21 13 13 Wet-Pipe Sprinkler Systems.docx"
    template.write_bytes(build("21 13 13", "WET-PIPE SPRINKLER SYSTEMS"))
    return [good, other, two, even], template


def test_left_untouched_names_the_missing_section_line_the_competing_numbers_and_the_setting(tmp_path):
    docs, template = specific_docs(tmp_path)
    task = DocumentTask(Session(out_root=tmp_path / "runs", visual_check=False), render=False, mode="safe")
    task.attach(docs)
    task.attach([template], template=True)
    view = task.perform("recipe", recipe="headers_footers_template", inputs={})
    reasons = {item["name"]: item["reason"] for item in view["set_aside"]}
    assert set(reasons) == {"other layout.docx", "two numbers.docx", "even odd.docx"}
    assert reasons["other layout.docx"] == (
        "No paragraph of this document starts with SECTION and its section number (like “SECTION 21 13 13”), so its "
        "own section number cannot be read for the headers and footers. Its first line reads “DIVISION 28 - 284600”.")
    assert reasons["two numbers.docx"] == (
        "This document's own section number for the headers and footers is not clear: paragraphs starting with "
        "SECTION give 2 different numbers, “26 05 00” and “28 31 02”. Nothing is guessed.")
    assert reasons["even odd.docx"].startswith(
        "This document has different headers and footers on odd and even pages (Word's Different Odd & Even Pages), "
        "and the template does not.")
    assert not any("evenAndOdd" in r or "op #" in r or "pattern" in r for r in reasons.values())


def test_a_step_of_a_saved_recipe_is_named_on_the_line(tmp_path):
    from spec_surgeon import saved_recipes
    docs, template = specific_docs(tmp_path)
    store = saved_recipes.Store(tmp_path / "config" / "recipes.json")
    steps = [saved_recipes.button_step("headers_footers_template", {}, "Match headers and footers to template"),
             saved_recipes.button_step("find_replace", {"pairs": [{"find": "section", "replace": "YOYO"}]},
                                       "Find and replace")]
    record = store.add("DIV 21 IFP", "", steps)
    task = DocumentTask(Session(out_root=tmp_path / "runs", visual_check=False), render=False, mode="safe",
                        recipes=store)
    task.attach(docs)
    task.attach([template], template=True)
    view = task.perform("saved_recipe", recipe=record["id"], steps=None)
    reasons = {item["name"]: item["reason"] for item in view["set_aside"]}
    assert reasons["other layout.docx"].startswith(
        "Step 1 of 2 (Match headers and footers to template): No paragraph of this document starts with SECTION")
    assert reasons["even odd.docx"].startswith("Step 1 of 2 (Match headers and footers to template): This document "
                                               "has different headers and footers on odd and even pages")
    view = task.perform("primary", token=view["token"])
    after = {item["name"]: item["reason"] for item in view["left_untouched"]}
    assert after["other layout.docx"] == reasons["other layout.docx"]          # the same line once the copies exist


def test_a_prepared_templates_placeholder_and_a_title_with_two_candidates_are_named():
    title = {"code": "ambiguous", "op": "apply_header_footer_template",
             "reason": "op #1 (apply_header_footer_template): slot 'Specification Name': anchor … matches 2 paragraph(s)",
             "details": {"slot": "Specification Name", "values": ["FIRE ALARM", "OTHER"],
                         "rule": REFERENCE_FILLS["SECTION_TITLE"]["rules"][0], "op": "apply_header_footer_template"}}
    assert refusal_message(title) == (
        "This document's own section title for the headers and footers (where the template shows “Specification "
        "Name”) is not clear: its SECTION lines give 2 different titles, “FIRE ALARM” and “OTHER”. Nothing is guessed.")
    after = {"code": "not-found", "op": "set_document_properties",
             "reason": "op #1 (set_document_properties): from_document.title: this file's section title is not found "
                       "by the reference rules (slot 'SECTION_TITLE': nothing follows the anchor paragraph)",
             "details": {"slot": "title", "anchor": "SECTION 21 13 13", "op": "set_document_properties",
                         "property": "title", "rule": REFERENCE_FILLS["SECTION_TITLE"]["rules"][0]}}
    assert refusal_message(after) == ("The line “SECTION 21 13 13” has nothing after it, so its own section title "
                                      "cannot be read for the document's Title property.")


def test_words_not_found_and_a_composed_step_name_what_was_looked_for():
    words = {"code": "not-found", "op": "replace_text",
             "reason": "step 2: op #1 (replace_text): 'section' not found in scope ['document']",
             "details": {"scope": ["document"], "find": "section", "whole_word": True, "longer_words": ["subsection"],
                         "elsewhere": "the headers (2×)", "chain_stage": 2, "chain_steps": 3, "step_name": "step 2",
                         "op": "replace_text", "op_number": 1}}
    assert refusal_message(words) == (
        "Step 2 of 3: “section” does not occur in the document body as a whole word (only inside longer words, such "
        "as “subsection”); it occurs only in the headers (2×), which this change does not search.")
    asked = {**words, "details": {**words["details"], "step_name": "Your request",
                                  "step_request": "Replace section with part in the body"}}
    assert refusal_message(asked).startswith("Step 2 of 3 (“Replace section with part in the body”): “section”")
    assert refusal_message(asked, step=False).startswith("“section” does not occur")
    pairs = {"code": "not-found", "op": "replace_table", "reason": "op #1 (replace_table): none of ['IFR'] occurs",
             "details": {"scope": ["document", "headers", "footers"], "finds": ["IFR", "IFB"], "op": "replace_table"}}
    assert refusal_message(pairs) == ("None of the words to find occurs in the document body, the headers and the "
                                      "footers: “IFR” and “IFB”.")
    selection = {"code": "not-found", "op": "delete_paragraphs",
                 "reason": "op #1 (delete_paragraphs): no paragraphs matched selector {...} in ['headers']",
                 "details": {"scope": ["headers"], "paragraphs": {"text_regex": "^DRAFT"}, "op": "delete_paragraphs"}}
    assert refusal_message(selection) == ("This change needs paragraphs whose text matches the pattern “^DRAFT” in "
                                          "the headers, and this document has none.")
    other = {"code": "eos-not-last", "op": "ensure_end_of_section",
             "reason": "step 1: op #1 (ensure_end_of_section): an END OF SECTION line ('END OF SECTION') sits before "
                       "other content; move or delete what follows it in Word",
             "details": {"chain_stage": 1, "chain_steps": 2, "step_name": "Add END OF SECTION where missing"}}
    assert refusal_message(other) == ("Step 1 of 2 (Add END OF SECTION where missing): an END OF SECTION line ('END "
                                      "OF SECTION') sits before other content; move or delete what follows it in Word")
