"""Planning receives governed visual/analysis tools without obtaining edit authority (``campaign_planner.propose``,
called the way an ordinary conversation calls it when no built-in operation fits)."""
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, thinking, tool
from make_fixtures import build
from spec_surgeon import campaign_planner, vision
from spec_surgeon.inventory import digest
from spec_surgeon.recovery import RecoveryOptions


def planner_source(tmp_path, template=None):
    source = tmp_path / "source" / "spec.docx"
    source.parent.mkdir()
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    documents = [{"id": "0", "source": str(source.resolve()), "source_sha256": digest(source), "status": "UNRESOLVED"}]
    project = {"label": "this conversation", "context": {"request": "Inspect margins"},
               "goal": {"template": str(template.resolve())} if template else {}, "decisions": [],
               "planning_artifact_dir": str(tmp_path / "outcome-planning")}
    return project, documents, source


def plan(project, documents, request, options, client, prior=None):
    """One planning call; the state it returns goes through JSON, as a saved conversation keeps it."""
    draft, state = campaign_planner.propose(project, documents, request, options, client=client, prior=prior)
    return draft, json.loads(json.dumps(state))


def proposed():
    return resp(tool("propose_plan", {"contract": {"kind": "document_effects", "effects": [
        {"id": "page", "type": "page_setup", "sections": "all", "margins": {"left": 1440}}]},
        "rationale": "The inspected layout supports the requested one-inch margin."}))


def page_image(tmp_path):
    from spec_surgeon.verify.geometry import fitz
    pdf = tmp_path / "page.pdf"
    document = fitz.open()
    document.new_page(width=144, height=144).insert_text((10, 30), "Layout evidence")
    document.save(pdf)
    document.close()
    return vision.rasterize_pages(pdf, [1], tmp_path / "png")[0]


def image_sources(messages):
    return [block["source"] for message in messages if isinstance(message.get("content"), list)
            for result in message["content"] if result.get("type") == "tool_result" and isinstance(result.get("content"), list)
            for block in result["content"] if block.get("type") == "image"]


def test_visual_planner_uses_selected_renderer_scope_budget_and_persisted_multimodal_history(tmp_path, monkeypatch):
    template = tmp_path / "reference.docx"
    template.write_bytes(build("21 05 00", "REFERENCE"))
    project, documents, source = planner_source(tmp_path, template)
    png = page_image(tmp_path)
    renders = []
    def render(path, pages, directory, renderer="auto"):
        renders.append((path, pages, directory, renderer))
        assert path in (source, template)
        return [png]
    monkeypatch.setattr(vision, "page_pngs", render)
    first = FakeClient(resp(thinking("signed-layout-evidence"),
                       tool("view_pages", {"file": "foreign.docx", "pages": [1]}, "outside"),
                       tool("view_pages", {"file": source.name, "pages": [1]}, "source")),
                       resp(tool("view_pages", {"file": "template", "pages": [1]})),
                       resp(tool("need_user", {"question": "Apply the same margin as this reference?"})))
    options = RecoveryOptions(render=True, renderer="word", use_memory=False)
    draft, state = plan(project, documents, "Match the reference margin", options, first)
    assert draft["status"] == "NEEDS_INPUT", draft
    assert [Path(r[0]).resolve() for r in renders] == [source.resolve(), template.resolve()]
    assert all(r[3] == "word" and "visual/word" in Path(r[2]).as_posix() for r in renders)
    assert state["image_tokens"] == 2 * vision.est_tokens(png)
    assert len(state["visual_evidence"]) == 2
    assert any("foreign.docx" in attempt["error"] for attempt in state["attempts"])
    images = image_sources(state["messages"])
    assert len(images) == 2 and all(item["media_type"] == "image/png" for item in images)
    project["decisions"].append({"text": "Yes, one inch", "document_ids": ["0"]})
    second = FakeClient(proposed())
    draft, again = plan(project, documents, "Match the reference margin", options, second, prior=state)
    assert image_sources(second.requests[0]["messages"]) == images
    assert "signed-layout-evidence" in json.dumps(second.requests[0]["messages"])
    assert again["image_tokens"] == state["image_tokens"]
    assert draft["status"] == "PROPOSED", draft


@pytest.mark.parametrize("render,renderer", [(False, "word"), (True, "none")])
def test_disabled_planning_rendering_cannot_be_bypassed_by_a_tool_call(tmp_path, monkeypatch, render, renderer):
    project, documents, source = planner_source(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled rendering reached the renderer")
    monkeypatch.setattr(vision, "page_pngs", forbidden)
    draft, state = plan(project, documents, "Inspect margins", RecoveryOptions(render=render, renderer=renderer),
                        FakeClient(resp(tool("view_pages", {"file": source.name})), proposed()))
    assert "rendering is disabled" in state["attempts"][0]["error"]
    assert not state.get("visual_evidence")
    assert draft["status"] == "PROPOSED"


def test_image_allowance_cannot_reset_when_planning_reopens(tmp_path, monkeypatch):
    project, documents, source = planner_source(tmp_path)
    png = page_image(tmp_path)
    cost = vision.est_tokens(png)
    monkeypatch.setattr(vision, "VIEW_BUDGET_TOKENS", cost)
    monkeypatch.setattr(vision, "page_pngs", lambda *args, **kwargs: [png])
    options = RecoveryOptions(render=True, use_memory=False)
    _, state = plan(project, documents, "Inspect margins", options,
                    FakeClient(resp(tool("view_pages", {"file": source.name})),
                               resp(tool("need_user", {"question": "Confirm margin?"}))))
    _, state = plan(project, documents, "Inspect margins", options,
                    FakeClient(resp(tool("view_pages", {"file": source.name})), proposed()), prior=state)
    assert state["image_tokens"] == cost
    assert len(state["visual_evidence"]) == 1
    assert any("page budget" in attempt["error"] for attempt in state["attempts"])
    assert len(image_sources(state["messages"])) == 1


def test_planner_forwards_host_analysis_limits_to_real_read_only_runner(tmp_path):
    project, documents, source = planner_source(tmp_path)
    client = FakeClient(resp(tool("run_analysis", {"source": "def analyze(documents, inputs):\n    return {'ok': True}",
                                                   "files": [source.name]})), proposed())
    # The document cannot fit in this deliberately tiny authorized snapshot allowance.
    options = RecoveryOptions(render=False, use_memory=False, analysis_limits={"input_bytes": 1})
    draft, state = plan(project, documents, "Inspect margins", options, client)
    assert any(attempt["tool"] == "run_analysis" and "input allowance" in attempt["error"].lower() for attempt in state["attempts"])
    assert draft["status"] == "PROPOSED"
