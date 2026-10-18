import copy
import json

import pytest

from spec_surgeon.project_evidence import (active_facts, approved_context_words, audit_standards_register,
    set_fact, set_standards_register, supersede_fact, validate_context)


def fact(ident="hazard", statement="Ordinary hazard group 1", status="confirmed", scope=None):
    value = {"id": ident, "statement": statement, "status": status, "scope": scope or {"kind": "project"},
             "source": {"kind": "user", "reference": "Owner review 2026-10-18"}}
    if status == "confirmed":
        value["confirmed_by"] = "Project reviewer"
    return value


def standard(ident="nfpa13", edition="2019", scope=None):
    return {"id": ident, "identifier": "NFPA 13", "edition": edition,
            "basis": "Project permit register, item 4", "scope": scope or {"kind": "project"},
            "source": {"kind": "user", "reference": "Reviewed project standards register"}, "confirmed_by": "A. Reviewer"}


def test_legacy_context_is_preserved_and_evidence_is_defensively_copied():
    legacy = {"name": "North", "stamp": "NORTH"}
    updated = set_fact(legacy, fact())
    assert legacy == {"name": "North", "stamp": "NORTH"}
    assert updated["name"] == "North"
    copy_of = validate_context(updated)
    copy_of["project_evidence"]["facts"][0]["statement"] = "Changed"
    assert updated["project_evidence"]["facts"][0]["statement"] == "Ordinary hazard group 1"


def test_supersession_keeps_history_and_assumptions_never_become_approved_words():
    context = set_fact({}, fact(status="assumed"))
    context = supersede_fact(context, "hazard", fact("hazard-reviewed", "Light hazard"))
    context = set_fact(context, fact("guess", "UNREVIEWED VALUE", status="assumed"))
    old, new, assumed = context["project_evidence"]["facts"]
    assert old["status"] == "superseded" and old["replaced_by"] == new["id"]
    assert new["supersedes"] == old["id"]
    assert active_facts(context) == [new]
    words = approved_context_words(context)
    assert "UNREVIEWED VALUE" not in words and "Ordinary hazard group 1" not in words
    assert "Light hazard" in words
    with pytest.raises(ValueError, match="human-confirmed"):
        supersede_fact(context, "hazard-reviewed", fact("second-guess", status="assumed"))


def test_fact_scope_does_not_bleed_and_confirmation_is_explicit():
    scope = {"kind": "section", "value": "21 13 13"}
    context = set_fact({}, fact(scope=scope))
    assert active_facts(context) == []
    assert len(active_facts(context, scope=scope)) == 1
    invalid = fact()
    invalid.pop("confirmed_by")
    with pytest.raises(ValueError, match="confirmed_by"):
        set_fact({}, invalid)
    with pytest.raises(ValueError, match="same scope"):
        supersede_fact(context, "hazard", fact("different", scope={"kind": "project"}))


def test_register_compares_only_explicit_choices_and_preserves_basis():
    context = set_standards_register({}, [standard()])
    rows = audit_standards_register([
        {"identifier": "NFPA 13", "edition": "2019", "location": {"paragraph": 3}},
        {"identifier": "nfpa  13", "edition": "2022"},
        {"identifier": "NFPA 13", "edition": None},
        {"identifier": "NFPA 25", "edition": "2023"}], context)
    assert [r["status"] for r in rows] == ["match", "mismatch", "unassessed", "unassessed"]
    assert rows[1]["register_entries"][0]["basis"] == "Project permit register, item 4"
    assert rows[0]["location"] == {"paragraph": 3}
    scoped = standard("conflict", "2022", {"kind": "discipline", "value": "fire"})
    context = set_standards_register(context, [standard(), scoped])
    assert audit_standards_register([{"identifier": "NFPA 13", "edition": "2019"}], context)[0]["status"] == "match"
    assert audit_standards_register([{"identifier": "NFPA 13", "edition": "2019"}], context,
                                   scope=scoped["scope"])[0]["status"] == "unassessed"


@pytest.mark.parametrize("mutation", [
    lambda f: f.update(status="certain"),
    lambda f: f.update(confirmed_by=" "),
    lambda f: f.update(scope={"kind": "section"}),
    lambda f: f.update(replaced_by="missing"),
    lambda f: f.update(source={"kind": "model", "reference": ""}),
])
def test_invalid_fact_shapes_are_rejected(mutation):
    value = fact()
    mutation(value)
    with pytest.raises(ValueError):
        set_fact({}, value)


def test_model_cannot_supply_a_standards_register_and_nonfinite_values_fail():
    value = standard()
    value["source"]["kind"] = "model"
    with pytest.raises(ValueError, match="user supplied"):
        set_standards_register({}, [value])
    with pytest.raises(ValueError, match="finite JSON"):
        validate_context({"invalid": float("nan")})


def test_scoped_fact_and_register_metadata_never_authorize_unrelated_replacement():
    from spec_surgeon.compiler import untraceable
    scope = {"kind": "document", "value": "/north/fire.docx"}
    context = set_fact({}, fact(statement="FIRE ONLY LITERAL", scope=scope))
    context = set_standards_register(context, [standard()])
    spec = {"ops": [{"op": "replace_text", "params": {"find": "OLD", "replace": "FIRE ONLY LITERAL"}}]}
    assert untraceable(spec, [approved_context_words(context)]) == ["FIRE ONLY LITERAL"]
    assert untraceable(spec, [approved_context_words(context, scopes=[{"kind": "document", "value": "/south/fire.docx"}])]) == ["FIRE ONLY LITERAL"]
    assert untraceable(spec, [approved_context_words(context, scopes=[scope])]) == []
    spec["ops"][0]["params"]["replace"] = "Project permit register, item 4"
    assert untraceable(spec, [approved_context_words(context, scopes=[scope])]) == ["Project permit register, item 4"]


def test_campaign_setters_invalidate_only_the_affected_project_and_cli_roundtrips(tmp_path, capsys):
    from make_fixtures import build
    from spec_surgeon.campaign import create_campaign, load_campaign
    from spec_surgeon.project_evidence_cli import main
    for name in ("north", "south"):
        folder = tmp_path / "inputs" / name
        folder.mkdir(parents=True)
        (folder / "21 13 13.docx").write_bytes(build("21 13 13", "SPRINKLER SYSTEMS"))
    campaign = create_campaign([str(tmp_path / "inputs")], tmp_path / "workspace")
    north, south = campaign.projects
    previous = copy.deepcopy(campaign.projects[south])
    campaign.projects[north]["proposed_plan"] = {"id": "old", "status": "READY"}
    campaign.set_project_fact(north, fact())
    loaded = load_campaign(campaign.out_dir)
    assert loaded.projects[south] == previous
    assert loaded.projects[north].get("proposed_plan", {}).get("status") != "READY"
    new = tmp_path / "fact.json"
    new.write_text(json.dumps(fact("revised", "Light hazard")))
    assert main([str(campaign.out_dir), north, "supersede", "hazard", str(new)]) == 0
    assert "Light hazard" in capsys.readouterr().out
    assert load_campaign(campaign.out_dir).projects[north]["context"]["project_evidence"]["facts"][0]["status"] == "superseded"
