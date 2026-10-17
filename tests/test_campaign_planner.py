"""Planning receives governed visual/analysis tools without obtaining edit authority."""
import json
from pathlib import Path

import pytest

from fake_claude import FakeClient, resp, thinking, tool
from make_fixtures import build
from spec_surgeon import vision
from spec_surgeon.campaign import create_campaign, load_campaign
from spec_surgeon.recovery import RecoveryOptions


def campaign_source(tmp_path):
    source = tmp_path / "source" / "spec.docx"
    source.parent.mkdir()
    source.write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    campaign = create_campaign([str(source)], tmp_path / "workspace")
    return campaign, next(iter(campaign.projects)), source


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
    campaign, pid, source = campaign_source(tmp_path)
    template = tmp_path / "reference.docx"
    template.write_bytes(build("21 05 00", "REFERENCE"))
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
    campaign.plan(pid, "Match the reference margin", template=template, options=options, client=first)
    state = campaign.projects[pid]["planning_state"]
    assert campaign.projects[pid]["proposed_plan"]["status"] == "NEEDS_INPUT", campaign.to_text()
    assert [r[0] for r in renders] == [source, template]
    assert all(r[3] == "word" and "visual/word" in Path(r[2]).as_posix() for r in renders)
    assert state["image_tokens"] == 2 * vision.est_tokens(png)
    assert len(state["visual_evidence"]) == 2
    assert any("foreign.docx" in attempt["error"] for attempt in state["attempts"])
    images = image_sources(state["messages"])
    assert len(images) == 2 and all(item["media_type"] == "image/png" for item in images)
    assert not campaign.projects[pid]["runs"]
    reopened = load_campaign(campaign.out_dir)
    reopened.answer(pid, "Yes, one inch", question_id=reopened.projects[pid]["questions"][0]["id"])
    second = FakeClient(proposed())
    reopened.plan(pid, client=second)
    assert image_sources(second.requests[0]["messages"]) == images
    assert "signed-layout-evidence" in json.dumps(second.requests[0]["messages"])
    assert reopened.projects[pid]["planning_state"]["image_tokens"] == state["image_tokens"]
    assert reopened.projects[pid]["proposed_plan"]["status"] == "PROPOSED", reopened.to_text()


@pytest.mark.parametrize("render,renderer", [(False, "word"), (True, "none")])
def test_disabled_planning_rendering_cannot_be_bypassed_by_a_tool_call(tmp_path, monkeypatch, render, renderer):
    campaign, pid, source = campaign_source(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled rendering reached the renderer")
    monkeypatch.setattr(vision, "page_pngs", forbidden)
    campaign.plan(pid, "Inspect margins", options=RecoveryOptions(render=render, renderer=renderer),
                  client=FakeClient(resp(tool("view_pages", {"file": source.name})), proposed()))
    state = campaign.projects[pid]["planning_state"]
    assert "rendering is disabled" in state["attempts"][0]["error"]
    assert not state.get("visual_evidence")
    assert campaign.projects[pid]["proposed_plan"]["status"] == "PROPOSED"


def test_image_allowance_cannot_reset_when_planning_reopens(tmp_path, monkeypatch):
    campaign, pid, source = campaign_source(tmp_path)
    png = page_image(tmp_path)
    cost = vision.est_tokens(png)
    monkeypatch.setattr(vision, "VIEW_BUDGET_TOKENS", cost)
    monkeypatch.setattr(vision, "page_pngs", lambda *args, **kwargs: [png])
    campaign.plan(pid, "Inspect margins", options=RecoveryOptions(render=True, use_memory=False),
                  client=FakeClient(resp(tool("view_pages", {"file": source.name})),
                                    resp(tool("need_user", {"question": "Confirm margin?"}))))
    reopened = load_campaign(campaign.out_dir)
    reopened.plan(pid, client=FakeClient(resp(tool("view_pages", {"file": source.name})), proposed()))
    state = reopened.projects[pid]["planning_state"]
    assert state["image_tokens"] == cost
    assert len(state["visual_evidence"]) == 1
    assert any("page budget" in attempt["error"] for attempt in state["attempts"])
    assert len(image_sources(state["messages"])) == 1


def test_planner_forwards_host_analysis_limits_to_real_read_only_runner(tmp_path):
    campaign, pid, source = campaign_source(tmp_path)
    client = FakeClient(resp(tool("run_analysis", {"source": "def analyze(documents, inputs):\n    return {'ok': True}",
                                                   "files": [source.name]})), proposed())
    # The document cannot fit in this deliberately tiny authorized snapshot allowance.
    options = RecoveryOptions(render=False, use_memory=False, analysis_limits={"input_bytes": 1})
    campaign.plan(pid, "Inspect margins", options=options, client=client)
    state = campaign.projects[pid]["planning_state"]
    assert any(attempt["tool"] == "run_analysis" and "input allowance" in attempt["error"].lower() for attempt in state["attempts"])
    assert campaign.projects[pid]["proposed_plan"]["status"] == "PROPOSED"
    assert not campaign.projects[pid]["runs"]


def test_resumed_legacy_planner_seals_old_signed_schema_before_upgrade(tmp_path, monkeypatch):
    import copy
    from spec_surgeon import campaign_planner
    from spec_surgeon.conversation import check_pairing

    campaign, pid, _ = campaign_source(tmp_path)
    old_tools = [t for t in campaign_planner.PLAN_TOOLS if t["name"] != "inspect_goal_effect"]
    monkeypatch.setattr(campaign_planner, "PLAN_TOOLS", old_tools)
    monkeypatch.setattr(campaign_planner, "SYSTEM", "Legacy planner system: planning only, no approval.")
    options = RecoveryOptions(render=False, use_memory=False)
    campaign.plan(pid, "Inspect margins", options=options,
                  client=FakeClient(resp(thinking("original-signed-planner-evidence"),
                       tool("need_user", {"question": "Confirm the margin?"}))))
    state = campaign.projects[pid]["planning_state"]
    old_system, old_provider_tools = state.pop("system"), state.pop("tools")
    old_messages = copy.deepcopy(state["messages"])
    # Simulate a saved pre-upgrade workspace: provider schemas existed only in
    # its immutable active manifest, not in the planning_state object.
    campaign._save("legacy_planning_fixture")
    monkeypatch.undo()
    reopened = load_campaign(campaign.out_dir)
    client = FakeClient(proposed())
    reopened.plan(pid, client=client)
    current = reopened.projects[pid]["planning_state"]
    assert reopened.projects[pid]["proposed_plan"]["status"] == "PROPOSED", reopened.to_text()
    assert current["context"]["epoch"] == 1
    artifact = current["context"]["archives"][0]["artifact_id"]
    sealed = json.loads((Path(current["context"]["folder"]) / f"{artifact}.json").read_text())
    assert sealed["system"] == old_system and sealed["tools"] == old_provider_tools
    assert sealed["messages"][:len(old_messages)] == old_messages
    assert "original-signed-planner-evidence" in json.dumps(sealed["messages"])
    assert "inspect_goal_effect" in {t["name"] for t in client.requests[0]["tools"]}
    assert client.requests[0]["system"] != old_system
    check_pairing(client.requests[0]["messages"])
    assert not reopened.projects[pid]["runs"]
