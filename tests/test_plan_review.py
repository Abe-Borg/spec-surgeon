"""A readable proposal includes the full effects and exact wording, not just an agent summary."""
from spec_surgeon.ui.plan_text import plan_text


def test_plan_review_keeps_every_effect_and_exact_new_and_old_wording():
    plan = {"id": "p123", "status": "PROPOSED", "request": "Update the project wording and title",
            "document_ids": ["selected"], "rationale": "Two independent changes", "risks": ["Repeated title"],
            "evidence": ["Title is in a table"], "questions": [], "contract": {
                "kind": "document_effects", "effects": [
                    {"id": "stamp", "type": "replace_text", "target": {"scope": ["headers"], "all": True},
                     "find": "PROJECT  A", "replace": "PROJECT B", "expected_matches": 2, "tracked": True},
                    {"id": "title", "type": "format", "target": {"scope": ["document"],
                     "locators": [{"exact_text": "FIRE SUPPRESSION", "in_table": True}], "cardinality": 1},
                     "font": {"name": "Arial", "size": 24}, "run": {"b": True}},
                    {"id": "note", "type": "insert_paragraph", "target": {"scope": ["document"],
                     "locators": [{"exact_text": "END"}]}, "text": "USER APPROVED NOTE", "position": "before"}]}}
    review = plan_text(plan, {"selected": {"source": "/source/job-a/spec.docx"},
                              "outside": {"source": "/source/job-b/spec.docx"}})
    assert 'Exact text before: "PROJECT  A"' in review
    assert 'Exact text after:  "PROJECT B"' in review
    assert 'Exact inserted wording: "USER APPROVED NOTE"' in review
    assert "Required matches: 2" in review and '"cardinality": 1' in review
    assert "Title is in a table" in review and "Repeated title" in review
    assert '"font"' in review and '"size": 24' in review  # complete contract stays visible
    assert "Font: Arial" in review and "Font size: 24 pt" in review and "Bold: on" in review
    assert "/source/job-a/spec.docx" in review and "/source/job-b/spec.docx" not in review


def test_template_review_shows_pin_and_literal_wording_inside_fallback_rules():
    review = plan_text({"id": "template-plan", "contract": {"kind": "document_effects", "effects": [
        {"id": "header", "type": "header_footer_template", "template": "/approved/template.docx",
         "template_sha256": "a" * 64, "params": {"slot_fills": {
             "project": {"rule": "first_match", "rules": [
                 {"rule": "regex", "pattern": "PROJECT (.+)"},
                 {"rule": "literal", "value": "APPROVED  PROJECT"}]}}}}]}}, {})
    assert "Pinned template SHA-256: " + "a" * 64 in review
    assert 'Exact approved wording for slot "project": "APPROVED  PROJECT"' in review


def test_review_explains_effective_defaults_roles_and_real_page_units():
    review = plan_text({"contract": {"kind": "document_effects", "effects": [
        {"id": "title", "type": "format", "target": {"locators": [{"role": "section_title"}]},
         "run": {"i": False}, "paragraph": {"keepNext": True}},
        {"id": "insert", "type": "insert_paragraph", "target": {"locators": [{"exact_text": "END"}]},
         "text": "Approved wording"},
        {"id": "page", "type": "page_setup", "margins": {"left": 1440}}]}}, {})
    assert "Italic: off" in review and "Keep with next paragraph: on" in review
    assert "section title identified from this document's SECTION marker" in review
    assert "Position: after the selected paragraph" in review and "Tracked change: yes" in review
    assert "Left margin: 1 inch" in review


def test_review_exposes_built_in_scope_counts_inputs_and_template_pins():
    effects = [
        {"id": "styles", "type": "style_mapping", "expected_count": "all", "select": {},
         "params": {"map": [{"from": "ArchitectHeading", "to": "OfficeHeading"}]},
         "template": "/approved/office.docx", "template_sha256": "b" * 64},
        {"id": "tables", "type": "table_format", "expected_count": 2,
         "select": {"tables": {"index_range": [0, 2]}}, "params": {"repeat_header_rows": 1}},
        {"id": "language", "type": "proofing_language", "expected_count": 1,
         "params": {"language": "en-GB"}},
        {"id": "numbers", "type": "typed_numbering", "expected_count": 4,
         "params": {"levels": ["PART", "ARTICLE"]}},
        {"id": "first", "type": "page_setup", "different_first_page": False},
    ]
    review = plan_text({"contract": {"kind": "document_effects", "effects": effects}}, {})
    assert 'Map style "ArchitectHeading" to "OfficeHeading"' in review
    assert "Required scope: all matching style usages" in review
    assert "Required scope: 2 tables" in review and '"index_range"' in review
    assert "Pinned template SHA-256: " + "b" * 64 in review
    assert "Proofing language: en-GB" in review
    assert "preserves the visible numbers" in review
    assert "Different first page: off" in review
    assert '"repeat_header_rows": 1' in review
