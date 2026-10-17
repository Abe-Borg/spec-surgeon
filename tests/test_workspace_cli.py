"""The public workspace CLI preserves project scope and explicit delivery."""
import json
from pathlib import Path

from spec_surgeon.cli import main


def _jobs(tmp_path):
    from make_fixtures import build
    root = tmp_path / "jobs"
    for name in ("north", "south"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "PROJECT.json").write_text(json.dumps({"name": name, "stamp": name.upper()}))
        (folder / "spec.docx").write_bytes(build("21 13 13", "FIRE SUPPRESSION"))
    return root


def test_workspace_create_and_show_keep_two_project_contexts(tmp_path, capsys):
    root = _jobs(tmp_path)
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(root), "--out", str(out)]) == 0
    capsys.readouterr()
    assert main(["workspace", "show", str(out), "--json"]) == 0
    value = json.loads(capsys.readouterr().out)
    assert len(value["documents"]) == 2
    assert {p["context"]["stamp"] for p in value["projects"].values()} == {"NORTH", "SOUTH"}
    assert len({d["project_id"] for d in value["documents"].values()}) == 2


def test_workspace_goal_is_scoped_and_export_requires_explicit_approval(tmp_path, capsys):
    from spec_surgeon.campaign import load_campaign
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(_jobs(tmp_path)), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    project_id = next(iter(campaign.projects))
    assert main(["workspace", "goal", str(out), "--project", project_id,
                 "--request", "Use the approved north stamp", "--answer", "NORTH"]) == 0
    campaign = load_campaign(out)
    assert campaign.projects[project_id]["goal"]["request"] == "Use the approved north stamp"
    assert all(p.get("goal", {}).get("request", "") != "Use the approved north stamp"
               for pid, p in campaign.projects.items() if pid != project_id)
    target = tmp_path / "delivery"
    assert main(["workspace", "export", str(out), "--out", str(target)]) == 0
    assert not target.exists()
    assert "Not exported" in capsys.readouterr().out


def test_workspace_run_forwards_ample_policy_and_token_overrides(monkeypatch, tmp_path):
    from spec_surgeon import campaign as module
    from spec_surgeon.resources import DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_COST_USD, DEFAULT_MAX_CALLS
    seen = []

    class Workspace:
        projects = {}

        def to_dict(self):
            return {}

        def run(self, **kwargs):
            seen.append(kwargs["options"])

        def to_text(self):
            return "Retained for review"

    monkeypatch.setattr(module, "load_campaign", lambda _: Workspace())
    assert main(["workspace", "run", str(tmp_path), "--no-render",
                 "--max-output-tokens", "32000", "--max-total-tokens", "12000000"]) == 0
    options = seen[0]
    assert options.max_attempts == DEFAULT_MAX_ATTEMPTS
    assert options.max_calls == DEFAULT_MAX_CALLS
    assert options.max_cost_usd == DEFAULT_MAX_COST_USD
    assert options.max_output_tokens == 32000 and options.max_total_tokens == 12000000
    assert options.effort == "max" and not options.render


def test_workspace_rejects_nonobject_project_context(tmp_path, capsys):
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(_jobs(tmp_path)), "--out", str(out)]) == 0
    assert main(["workspace", "context", str(out), "--project", "missing", "--context", "[]"]) == 2
    assert "expected a JSON object" in capsys.readouterr().err


def test_cli_resume_preserves_saved_limits_and_only_overrides_explicit_flags(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from spec_surgeon import recovery
    saved = dict(max_cost_usd=1.5, max_attempts=7, max_calls=20, max_output_tokens=4000,
                 max_total_tokens=100000, effort="low", render=False, renderer="none",
                 allow_codegen=True, use_memory=False, model="chosen-model")
    (tmp_path / "recovery.json").write_text("{}")
    monkeypatch.setattr(recovery, "load_recovery", lambda _: SimpleNamespace(options_snapshot=saved))
    seen = []

    def resume(*args, **kwargs):
        seen.append(kwargs["options"])
        return SimpleNamespace(to_text=lambda: "Saved progress", review=None, ready=[])

    monkeypatch.setattr(recovery, "recover_from_run", resume)
    assert main(["resolve", str(tmp_path), "--max-calls", "77", "--render", "--no-codegen"]) == 2
    actual = seen[0]
    assert actual.max_cost_usd == 1.5 and actual.max_calls == 77
    assert actual.max_output_tokens == 4000 and actual.effort == "low"
    assert actual.model == "chosen-model" and actual.renderer == "none"
    assert actual.render and not actual.allow_codegen and not actual.use_memory


def test_workspace_resume_preserves_saved_resource_policy(monkeypatch, tmp_path):
    from spec_surgeon import campaign as module
    seen = []

    class Workspace:
        projects = {}

        def to_dict(self):
            return {"resource_options": {"max_cost_usd": 2.0, "max_calls": 9, "render": False}}

        def run(self, **kwargs):
            seen.append(kwargs["options"])

        def to_text(self):
            return "Saved progress"

    monkeypatch.setattr(module, "load_campaign", lambda _: Workspace())
    assert main(["workspace", "run", str(tmp_path)]) == 0
    assert seen[0].max_cost_usd == 2.0 and seen[0].max_calls == 9 and not seen[0].render


def test_workspace_chain_resolves_template_relative_to_chain_file(tmp_path):
    from spec_surgeon.campaign import load_campaign
    root = _jobs(tmp_path)
    workspace = tmp_path / "workspace"
    assert main(["workspace", "create", str(root), "--out", str(workspace)]) == 0
    campaign = load_campaign(workspace)
    project_id = next(iter(campaign.projects))
    examples = Path(__file__).resolve().parent.parent / "examples" / "specs"
    spec = json.loads((examples / "match-template-spec-header-footer.json").read_text())
    for operation in spec["ops"]:
        if "template" in operation.get("params", {}):
            operation["params"]["template"] = "local-template.docx"
    folder = tmp_path / "saved-chain"
    folder.mkdir()
    chain_file = folder / "chain.json"
    chain_file.write_text(json.dumps({"stages": [{"spec": spec}]}))
    assert main(["workspace", "goal", str(workspace), "--project", project_id,
                 "--request", "Apply this project's saved chain", "--chain", "@" + str(chain_file)]) == 0
    saved = load_campaign(workspace).projects[project_id]["goal"]["chain"]
    templates = [operation["params"]["template"] for operation in saved["stages"][0]["spec"]["ops"]
                 if "template" in operation.get("params", {})]
    assert templates == [str(folder / "local-template.docx")]


def test_workspace_adaptive_edit_survives_reopen_and_exports_only_its_project(tmp_path):
    from spec_surgeon.campaign import load_campaign
    from spec_surgeon.goals import verify_goal
    root = _jobs(tmp_path)
    originals = {str(p): p.read_bytes() for p in root.rglob("*.docx")}
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(root), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    project_id = next(pid for pid, p in campaign.projects.items() if p["context"]["stamp"] == "NORTH")
    contract = {"kind": "format_section_title", "target": {"title": "FIRE SUPPRESSION",
                "section_number": "21 13 13"}, "run": {"b": True}}
    assert main(["workspace", "goal", str(out), "--project", project_id,
                 "--request", "Make the FIRE SUPPRESSION section title bold", "--adaptive", json.dumps(contract)]) == 0
    assert main(["workspace", "run", str(out), "--no-render", "--no-memory"]) == 0
    restored = load_campaign(out)
    selected = [d for d in restored.documents.values() if d["project_id"] == project_id]
    assert all(d["status"] == "READY" for d in selected), restored.to_text()
    assert all(d["status"] == "PENDING" for d in restored.documents.values() if d["project_id"] != project_id)
    attempt_paths = [r["path"] for r in restored.projects[project_id]["runs"]]
    assert main(["workspace", "run", str(out), "--no-render", "--no-memory"]) == 0
    assert [r["path"] for r in load_campaign(out).projects[project_id]["runs"]] == attempt_paths
    destination = tmp_path / "export"
    assert main(["workspace", "export", str(out), "--out", str(destination), "--yes"]) == 0
    outputs = list(destination.rglob("*.docx"))
    assert len(outputs) == 1
    source_doc = selected[0]
    assert verify_goal(Path(source_doc["source"]), outputs[0], contract, source_doc["binding"])["passed"]
    assert {str(p): p.read_bytes() for p in root.rglob("*.docx")} == originals


def test_cli_planning_approval_execution_reopen_and_export_with_fake_model(tmp_path, monkeypatch, capsys):
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon import llm
    from spec_surgeon.campaign import load_campaign
    from spec_surgeon.goals import verify_goal
    root, out = _jobs(tmp_path), tmp_path / "workspace"
    originals = {str(p): p.read_bytes() for p in root.rglob("*.docx")}
    assert main(["workspace", "create", str(root), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    pid = next(pid for pid, p in campaign.projects.items() if p["context"]["stamp"] == "NORTH")
    doc_id = campaign.projects[pid]["document_ids"][0]
    contract = {"kind": "document_effects", "effects": [
        {"id": "title", "type": "format", "target": {"scope": ["document"],
         "locators": [{"exact_text": "FIRE SUPPRESSION"}], "cardinality": 1}, "run": {"b": True}},
        {"id": "wording", "type": "replace_text", "target": {"scope": ["document"],
         "locators": [{"exact_text": "FIRE SUPPRESSION"}], "cardinality": 1},
         "find": "FIRE SUPPRESSION", "replace": "NORTH FIRE SUPPRESSION", "expected_matches": 1}]}
    fake = FakeClient(resp(tool("propose_plan", {"contract": contract, "rationale": "Change the selected project title",
                                               "risks": ["The wording changes."], "evidence": ["One matching title."]})))
    monkeypatch.setattr(llm, "client", lambda: fake)
    assert main(["workspace", "plan", str(out), "--project", pid, "--documents", doc_id,
                 "--request", "Make the title bold and replace its wording with NORTH FIRE SUPPRESSION",
                 "--no-render", "--no-memory", "--budget", "4", "--max-calls", "12"]) == 0
    text = capsys.readouterr().out
    assert "NORTH FIRE SUPPRESSION" in text and "The wording changes." in text
    proposed = load_campaign(out).projects[pid]["proposed_plan"]
    assert proposed["document_ids"] == [doc_id] and proposed["status"] == "PROPOSED"
    assert not load_campaign(out).projects[pid]["runs"]
    assert main(["workspace", "approve-plan", str(out), "--project", pid, "--plan", proposed["id"]]) == 0
    assert not load_campaign(out).projects[pid]["runs"]
    assert main(["workspace", "run", str(out)]) == 0
    campaign = load_campaign(out)
    assert campaign.documents[doc_id]["status"] == "READY"
    assert campaign.to_dict()["resource_options"]["max_cost_usd"] == 4
    assert campaign.to_dict()["resource_options"]["max_calls"] == 12
    assert len(fake.requests) == 1  # independently checked builtin execution needs no more model calls
    destination = tmp_path / "delivery"
    assert main(["workspace", "export", str(out), "--out", str(destination), "--yes"]) == 0
    outputs = list(destination.rglob("*.docx"))
    assert len(outputs) == 1
    source_doc = campaign.documents[doc_id]
    assert verify_goal(Path(source_doc["source"]), outputs[0], contract, source_doc["binding"])["passed"]
    assert {str(p): p.read_bytes() for p in root.rglob("*.docx")} == originals


def test_cli_plan_returns_needs_input_only_for_requested_scope(tmp_path, monkeypatch):
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon import llm
    from spec_surgeon.campaign import load_campaign
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(_jobs(tmp_path)), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    first, second = campaign.projects
    first_doc = campaign.projects[first]["document_ids"][0]
    second_doc = campaign.projects[second]["document_ids"][0]
    fake = FakeClient(resp(tool("need_user", {"question": "Which exact project wording?", "document_ids": [first_doc]})),
                      resp(tool("propose_plan", {"contract": {"kind": "format_section_title", "target": {
                           "title": "FIRE SUPPRESSION"}, "run": {"b": True}}, "rationale": "Format the unique title"})))
    monkeypatch.setattr(llm, "client", lambda: fake)
    assert main(["workspace", "plan", str(out), "--project", first, "--documents", first_doc,
                 "--request", "Apply the project wording", "--no-render", "--no-memory"]) == 2
    campaign = load_campaign(out)
    assert campaign.projects[first]["proposed_plan"]["status"] == "NEEDS_INPUT"
    assert campaign.projects[first]["questions"][0]["document_ids"] == [first_doc]
    assert main(["workspace", "plan", str(out), "--project", second, "--documents", second_doc,
                 "--request", "Make FIRE SUPPRESSION bold"]) == 0
    assert load_campaign(out).projects[first]["proposed_plan"]["status"] == "NEEDS_INPUT"


def test_cli_bind_routes_multiple_candidates_to_one_effect(tmp_path):
    from docx import Document
    from spec_surgeon.campaign import load_campaign
    root = tmp_path / "inputs"
    root.mkdir()
    document = Document()
    for _ in range(3):
        document.add_paragraph("DUPLICATED HEADING")
    document.save(root / "spec.docx")
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(root), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    pid = next(iter(campaign.projects))
    doc_id = campaign.projects[pid]["document_ids"][0]
    contract = {"kind": "document_effects", "effects": [{"id": "headings", "type": "format",
        "target": {"scope": ["document"], "locators": [{"exact_text": "DUPLICATED HEADING"}],
                   "cardinality": 2}, "run": {"b": True}}]}
    assert main(["workspace", "goal", str(out), "--project", pid, "--request", "Make two intended headings bold",
                 "--adaptive", json.dumps(contract)]) == 0
    assert main(["workspace", "run", str(out), "--no-render", "--no-memory"]) == 2
    campaign = load_campaign(out)
    question = campaign.projects[pid]["questions"][0]
    choices = question["resolution"]["effects"]["headings"]["candidates"]
    picked = [choices[0]["candidate_id"], choices[2]["candidate_id"]]
    assert main(["workspace", "bind", str(out), "--document", doc_id, "--effect", "headings",
                 "--candidates", *picked]) == 0
    decision = load_campaign(out).projects[pid]["decisions"][-1]
    assert decision["effect_id"] == "headings" and decision["candidate_ids"] == picked
    assert decision["document_ids"] == [doc_id] and decision["question_id"] == question["id"]


def test_cli_plan_rejects_documents_outside_selected_project(tmp_path, monkeypatch):
    from spec_surgeon import llm
    from spec_surgeon.campaign import load_campaign
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(_jobs(tmp_path)), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    first, second = campaign.projects
    monkeypatch.setattr(llm, "client", lambda: (_ for _ in ()).throw(AssertionError("scope must reject before a model call")))
    assert main(["workspace", "plan", str(out), "--project", first, "--documents",
                 campaign.projects[second]["document_ids"][0], "--request", "Make the title bold"]) == 2
    assert all(not p.get("proposed_plan") for p in load_campaign(out).projects.values())


def test_cli_global_plan_skips_projects_without_a_request(tmp_path, monkeypatch):
    from fake_claude import FakeClient, resp, tool
    from spec_surgeon import llm
    from spec_surgeon.campaign import load_campaign
    out = tmp_path / "workspace"
    assert main(["workspace", "create", str(_jobs(tmp_path)), "--out", str(out)]) == 0
    campaign = load_campaign(out)
    selected, skipped = campaign.projects
    campaign.save_plan_draft(selected, "Make FIRE SUPPRESSION bold")
    fake = FakeClient(resp(tool("propose_plan", {"contract": {"kind": "format_section_title",
                      "target": {"title": "FIRE SUPPRESSION"}, "run": {"b": True}}, "rationale": "One exact title"})))
    monkeypatch.setattr(llm, "client", lambda: fake)
    assert main(["workspace", "plan", str(out), "--no-render", "--no-memory"]) == 0
    campaign = load_campaign(out)
    assert campaign.projects[selected]["proposed_plan"]["status"] == "PROPOSED"
    assert not campaign.projects[skipped].get("proposed_plan")
    assert len(fake.requests) == 1
