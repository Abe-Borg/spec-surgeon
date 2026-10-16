"""Built-in batch 2 in the rest of the app (docs/BUILTIN_OPS_BATCH2_DESIGN.md): the words the new ops place are traced to
the user (compiler.placed_words — rule 13), the profiles show Claude what each clean-up op would find (profile.markup),
and the method's three rules read as Abraham changed them."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_COLOR_INDEX
from docx.shared import RGBColor

from make_fixtures import build_rich, docx_bytes
from spec_surgeon import SKILL_DIR
from spec_surgeon.compiler import placed_words, system_prompt, untraceable
from spec_surgeon.package import DocxPackage
from spec_surgeon.profile import markup, profile


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


def _marked() -> bytes:
    doc = Document()
    p = doc.add_paragraph("Plain ")
    r = p.add_run("hidden note"); r.font.hidden = True
    r = p.add_run("yellow"); r.font.highlight_color = WD_COLOR_INDEX.YELLOW
    r = p.add_run("blue"); r.font.color.rgb = RGBColor(0, 0, 0xFF)
    doc.core_properties.title = "Spec title"
    doc.core_properties.author = "Jeremy"
    return docx_bytes(doc)


def test_the_profile_shows_what_the_clean_up_ops_would_find():
    m = markup(DocxPackage(_marked()))
    assert m["hidden_runs"] == 1 and m["highlight_colors"] == {"yellow": 1} and m["run_colors"] == {"0000FF": 1}
    assert m["properties"]["title"] == "Spec title" and m["properties"]["author"] == "Jeremy"
    rich = profile(DocxPackage(build_rich()))
    assert rich["markup"]["tracked_changes"]["Jeremy"]["ins"] == 2
    assert rich["markup"]["tracked_change_dates"] == ["2026-09-22", "2026-09-23"]


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
