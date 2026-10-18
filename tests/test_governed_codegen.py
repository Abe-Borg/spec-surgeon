"""Generated methods can change approved content; their own plan cannot enlarge intent."""
import copy

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon.codegen import generate_op, run_generated, trial
from spec_surgeon.goals import choose_goal, resolve_goal
from spec_surgeon.library import LibraryError, save_operation


CONTENT_OP = '''from spec_surgeon.ops.base import Op, JobException
from spec_surgeon.ooxml import paragraphs, RevisionStamp, max_revision_id, file_uses_date_utc
from spec_surgeon.ops.textedit import Edit, RunText, plan_paragraph, edits_to_data, apply_part_edits

class ReplaceTitle(Op):
    name = "replace_approved_title"
    mode = "content"
    description = "Change the approved literal in the bound title"
    params_schema = {}
    select_schema = {}
    def plan(self, pkg, select, params):
        frag = self.fragment(select, params)
        part = pkg.main_part()
        data = {}
        tracked = params.get("tracked", False)
        frag.mode = "content" if tracked else "untracked"
        for i, p in enumerate(paragraphs(pkg.xml(part))):
            content = RunText(p).text
            if content == "FIRE SUPPRESSION":
                edits = [Edit(0, 4, params["replacement"])]
                edits = plan_paragraph(frag, part, i, p, edits, tracked, set(), "title", author="Tester")
                data[str(i)] = edits_to_data(edits)
        if not data:
            raise JobException("title not found", "not-found")
        frag.geometry_reference = "none"
        frag.geometry_reason = "approved wording replacement"
        frag.transform_data = {"part": part, "edits": data, "tracked": tracked,
                               "next_id": max_revision_id(pkg) + 1,
                               "date_utc": file_uses_date_utc(pkg)}
        return frag
    def apply(self, pkg, frag):
        td = frag.transform_data
        root = pkg.xml(td["part"])
        stamp = RevisionStamp.now("Tester", td["date_utc"]) if td["tracked"] else None
        apply_part_edits(root, td["edits"], td["tracked"], stamp, [td["next_id"]])
        pkg.write_xml(td["part"], root)
'''


def contract(tracked=False):
    return {"kind": "document_effects", "effects": [{"id": "wording", "type": "replace_text",
            "target": {"locators": [{"exact_text": "FIRE SUPPRESSION"}], "cardinality": 1},
            "find": "FIRE", "replace": "WATER", "expected_matches": 1, "tracked": tracked, "author": "Tester"}]}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "source.docx"
    path.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    return path


@pytest.mark.parametrize("tracked", [False, True])
def test_generated_content_method_is_verified_against_original_goal(source, tmp_path, tracked):
    goal = contract(tracked)
    binding = resolve_goal(source, goal)["binding"]
    result = trial(CONTENT_OP, source, {}, {"replacement": "WATER", "tracked": tracked}, tmp_path / "trial",
                   goal_contract=goal, goal_binding=binding)
    assert result.passed, result.problems
    assert result.record.get("GOAL").status == "PASS"
    legacy = trial(CONTENT_OP, source, {}, {"replacement": "WATER"}, tmp_path / "legacy")
    assert not legacy.passed


def test_a_self_consistent_generated_plan_cannot_add_unapproved_words(source, tmp_path):
    goal = contract()
    result = trial(CONTENT_OP, source, {}, {"replacement": "INVENTED"}, tmp_path / "trial",
                   goal_contract=goal, goal_binding=resolve_goal(source, goal)["binding"])
    assert not result.passed
    assert result.record.get("GOAL").status == "FAIL"
    assert all(check.status != "FAIL" for check in result.record.checks if check.id != "GOAL")


def test_writer_repairs_goal_failure_and_verifies_every_full_batch_file(source, tmp_path):
    other = tmp_path / "second.docx"
    other.write_bytes(build("21 13 13", "FIRE SUPPRESSION"))
    fake = FakeClient(resp(tool("submit_op", {"source": CONTENT_OP, "params": {"replacement": "INVENTED"}}, "bad")),
                      resp(tool("submit_op", {"source": CONTENT_OP, "params": {"replacement": "WATER"}}, "good")))
    goal = contract()
    generated = generate_op("change FIRE to WATER, untracked", "custom extraction method", [source],
                            tmp_path / "writer", client=fake, goal_contract=goal,
                            goal_bindings={str(source): resolve_goal(source, goal)["binding"]})
    assert generated.passed and len(generated.attempts) == 2, generated.errors
    assert "GOAL FAIL" in str(generated.attempts[0]["problems"])
    goal["effects"][0]["replace"] = "mutated caller object"
    assert generated.goal_contract["effects"][0]["replace"] == "WATER"
    results = run_generated(generated, [str(source), str(other)], tmp_path / "delivery")
    assert len(results) == 2 and all(item.passed and item.record.get("GOAL").status == "PASS" for item in results)
    with pytest.raises(LibraryError, match="approved workspace goal"):
        save_operation("content method", generated)


def test_proposal_and_brief_cannot_approve_goal_wording(source, tmp_path):
    fake = FakeClient()
    with pytest.raises(ValueError, match="without user approval"):
        generate_op("change the title", "use WATER", [source], tmp_path / "writer", client=fake,
                    brief="assistant proposed WATER", goal_contract=contract())
    assert not fake.requests


def test_changed_source_binding_is_rejected_before_execution(source, tmp_path):
    goal = contract()
    binding = resolve_goal(source, goal)["binding"]
    source.write_bytes(source.read_bytes() + b"source drift")
    result = trial(CONTENT_OP, source, {}, {"replacement": "WATER"}, tmp_path / "trial",
                   goal_contract=goal, goal_binding=binding)
    assert not result.passed and "binding refused" in result.problems[0]
    assert not (tmp_path / "trial").exists()


def test_nonsampled_files_retain_explicit_user_target_choices(source, tmp_path):
    from spec_surgeon.ooxml import paragraphs, block_full_text
    from spec_surgeon.package import DocxPackage
    other = tmp_path / "ambiguous.docx"
    package = DocxPackage.from_path(source)
    main = package.main_part()
    root = package.xml(main)
    target = next(p for p in paragraphs(root) if block_full_text(p) == "FIRE SUPPRESSION")
    target.getparent().insert(target.getparent().index(target) + 1, copy.deepcopy(target))
    package.write_xml(main, root)
    other.write_bytes(package.output_bytes())
    goal = contract()
    resolution = resolve_goal(other, goal)
    assert resolution["status"] == "needs_input"
    binding = choose_goal(other, goal, [resolution["candidates"][0]["candidate_id"]], effect_id="wording")
    first_only = CONTENT_OP.replace('data[str(i)] = edits_to_data(edits)',
                                    'data[str(i)] = edits_to_data(edits)\n                break')
    fake = FakeClient(resp(tool("submit_op", {"source": first_only, "params": {"replacement": "WATER"}}, "good")))
    generated = generate_op("change FIRE to WATER untracked", "custom method", [source], tmp_path / "writer",
                            client=fake, goal_contract=goal, goal_bindings={str(other): binding})
    assert generated.passed and generated.goal_bindings[str(other)] == binding
    results = run_generated(generated, [str(other)], tmp_path / "delivery")
    assert results[0].passed, results[0].problems
