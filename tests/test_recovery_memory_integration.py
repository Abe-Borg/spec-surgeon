"""Independent recovery regressions: repeated memory, template identity, and goal retention."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from lxml import etree

from fake_claude import FakeClient, resp, tool
from make_fixtures import build, build_template
from spec_surgeon import recovery
from spec_surgeon.editspec import plan_spec
from spec_surgeon.fingerprint import fingerprint
from spec_surgeon.package import DocxPackage, W
from spec_surgeon.repair_memory import CaseStore
from test_codegen import GOOD_OP


SPECS = Path(__file__).resolve().parent.parent / "examples" / "specs"
REMOVE = json.loads((SPECS / "remove-all-headers-footers.json").read_text())


def options(**kwargs):
    return recovery.RecoveryOptions(render=False, max_cost_usd=20, **kwargs)


def margin_goal():
    return {"request": "Set the header distance to 900 twips and make the document Verdana",
            "summary": "Set header distance and font", "mode": "formatting", "ops": [
                {"op": "set_page_setup", "params": {"margins": {"header": 900}}},
                {"op": "set_font", "select": {"scope": ["document"]},
                 "params": {"ascii": "Verdana", "hAnsi": "Verdana", "cs": "Verdana"}},
            ]}


def test_custom_recovery_cannot_satisfy_only_one_of_multiple_original_goals(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = margin_goal()
    # This real sandboxed transform passes independent verification, but only implements
    # the header-distance goal. The unimplemented font change must prevent READY.
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=options(allow_codegen=True, use_memory=False, max_attempts=1), client=client)
    assert not result.ready, "A partial generated repair was marked READY despite omitting the requested font change"


def test_original_stage_assertion_cannot_be_dropped_when_final_checker_cannot_express_it(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = margin_goal()
    goal["ops"] = goal["ops"][:1]
    goal["assertions"] = [{"kind": "effective_font", "value": "Verdana", "scope": ["document"],
                           "preserve_fonts": ["Symbol"]}]
    candidate = copy.deepcopy(goal)
    del candidate["assertions"]
    client = FakeClient(resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": candidate}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=options(use_memory=False, max_attempts=1), client=client)
    assert not result.ready, "An original explicit assertion disappeared instead of being preserved or refused"


def test_custom_recovery_cannot_expand_changes_beyond_the_requested_properties(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = margin_goal()
    goal["request"] = "Set the header distance to 900 twips"
    goal["ops"] = goal["ops"][:1]
    extra_footer_change = GOOD_OP.replace('"set": {"header": str(twips)}',
                                         '"set": {"header": str(twips), "footer": str(twips)}').replace(
        'sp.find(W + "pgMar").set(W + "header", td["twips"])',
        'sp.find(W + "pgMar").set(W + "header", td["twips"])\n'
        '            sp.find(W + "pgMar").set(W + "footer", td["twips"])')
    # The generated plan honestly declares BOTH changes, so its own verifier passes.
    # Its plan must not grant authority to change the unrequested footer distance.
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                        resp(tool("submit_op", {"source": extra_footer_change, "params": {"twips": 900}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=options(allow_codegen=True, use_memory=False, max_attempts=1), client=client)
    assert not result.ready, "Generated declarations authorized an extra change outside the user's request"


def test_custom_trial_unexpected_review_failure_withdraws_ready_output(tmp_path, monkeypatch):
    from spec_surgeon import vision

    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = margin_goal()
    goal["request"] = "Set the header distance to 900 twips"
    goal["ops"] = goal["ops"][:1]

    def fail_to_save(*args, **kwargs):
        raise OSError("simulated failure saving the review record")

    monkeypatch.setattr(vision, "save_review", fail_to_save)
    client = FakeClient(resp(tool("custom_transform", {"proposal": "Set the header distance"})),
                        resp(tool("submit_op", {"source": GOOD_OP, "params": {"twips": 900}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal,
                              options=options(allow_codegen=True, use_memory=False, max_attempts=1), client=client)
    assert not result.ready, "A custom trial's unexpected exception left an exportable READY output"


def test_identical_template_bytes_at_different_input_paths_preserve_the_goal(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    first = tmp_path / "architect.docx"
    second = tmp_path / "architect-copy.docx"
    first.write_bytes(build_template())
    second.write_bytes(first.read_bytes())
    goal = json.loads((SPECS / "apply-header-footer-template.json").read_text())
    for op in goal["ops"]:
        if "template" in op.get("params", {}):
            op["params"]["template"] = str(first)
    # Both paths are current authorized inputs. Content deduplication must not make one
    # alias unauthorized, or require the repair to change the pinned operation's path.
    client = FakeClient(resp(tool("submit_repair", {"candidate": {"kind": "spec", "spec": goal}})))
    result = recovery.recover([source], goal["request"], tmp_path / "recovery", spec=goal, template=second,
                              options=options(use_memory=False, max_attempts=1), client=client)
    assert result.ready, result.to_text()


def test_duplicate_successful_memories_do_not_consume_retries_on_the_same_failure(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = copy.deepcopy(REMOVE)
    plan, _ = plan_spec(DocxPackage.from_path(source), goal)
    goal["ops"][0]["expect"] = {"text_diffs": len(plan.diffs)}
    candidate = {"kind": "spec", "spec": goal}
    for i in range(3):
        client = (FakeClient(resp(tool("submit_repair", {"candidate": candidate}))) if i == 0 else FakeClient())
        previous = recovery.recover([source], goal["request"], tmp_path / f"success-{i}", spec=goal,
                                    options=options(), client=client)
        assert previous.ready
    assert len(CaseStore().list_cases()) == 3
    pkg = DocxPackage.from_path(source)
    before_pattern = fingerprint(pkg)["pattern_id"]
    part = next(p for p in pkg.text_parts() if p.startswith("word/header"))
    root = pkg.xml(part)
    p = etree.SubElement(root, W + "p")
    etree.SubElement(etree.SubElement(p, W + "r"), W + "t").text = "An additional header line"
    pkg.write_xml(part, root)
    source.write_bytes(pkg.output_bytes())
    assert fingerprint(DocxPackage.from_path(source))["pattern_id"] == before_pattern
    repaired = copy.deepcopy(candidate)
    repaired["spec"]["ops"][0].pop("expect")
    client = FakeClient(resp(tool("submit_repair", {"candidate": repaired})))
    result = recovery.recover([source], goal["request"], tmp_path / "after-change", spec=goal,
                              options=options(), client=client)
    assert len(client.requests) == 1, "Duplicate memories exhausted all attempts before the model could investigate"
    assert result.ready and len(result.attempts) == 2


def test_rejecting_a_repair_blocks_older_duplicate_cases_for_the_same_repair(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 13 19", "SPRINKLERS"))
    goal = copy.deepcopy(REMOVE)
    candidate = {"kind": "spec", "spec": goal}
    for i in range(2):
        client = (FakeClient(resp(tool("submit_repair", {"candidate": candidate}))) if i == 0 else FakeClient())
        previous = recovery.recover([source], goal["request"], tmp_path / f"success-{i}", spec=goal,
                                    options=options(), client=client)
        assert previous.ready
    latest = previous.ready[0].case_id
    assert CaseStore().feedback(latest, False, "This repair was the wrong interpretation")
    client = FakeClient(resp(tool("submit_repair", {"candidate": candidate})),
                        resp(tool("need_user", {"question": "Which header content should remain?"})))
    result = recovery.recover([source], goal["request"], tmp_path / "after-correction", spec=goal,
                              options=options(), client=client)
    assert len(client.requests) == 2, "An older duplicate bypassed the user's correction"
    assert not result.attempts, "The model replayed the exact rejected repair as a new proposal"
    assert not result.ready and result.questions
