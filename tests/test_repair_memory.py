"""Repair memory is local evidence, with exact eligibility and fresh checks required."""
from __future__ import annotations

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from spec_surgeon import __version__
from spec_surgeon import repair_memory as memory


def example(**changes):
    spec = {"request": "Set project name to Hospital A", "mode": "content",
            "ops": [{"op": "replace_text", "params": {"find": "Old name", "replace": "Hospital A"}}]}
    case = {"request": spec["request"], "pattern_id": "pattern-a", "failure_code": "conflict",
            "goal_fingerprint": memory.goal_fingerprint(spec), "template_hashes": ["template-a"],
            "candidate": {"kind": "spec", "spec": spec}, "status": "success", "structural_verified": True,
            "visual_review": {"skipped": "Rendering was not requested"}, "app_version": __version__,
            "usage": {"cost_usd": 0.25}, "notes": "Changed the selector; reran verification."}
    case.update(changes)
    return case


def eligible(case, **changes):
    args = {k: example()[k] for k in ("request", "pattern_id", "goal_fingerprint", "template_hashes", "app_version")}
    args.update(changes)
    return memory.applicable(case, **args)


def test_round_trip_and_control_apis_are_local_and_do_not_create_on_read(tmp_path, monkeypatch):
    monkeypatch.setenv("SPEC_SURGEON_CONFIG_DIR", str(tmp_path / "config"))
    store = memory.CaseStore()
    assert store.path == tmp_path / "config" / "repair-memory.sqlite3"
    assert not store.path.exists()
    assert store.list_cases() == store.search("something") == []
    assert store.get("missing") is None
    assert store.forget("missing") is store.feedback("missing", False) is False
    assert not store.path.exists()
    case = example()
    original = copy.deepcopy(case)
    case_id = store.record(case)
    saved = store.get(case_id)
    assert saved["candidate"] == case["candidate"] and case == original
    assert saved["structural_verified"] is True
    assert saved["visual_review"]["skipped"] == "Rendering was not requested"
    assert saved["feedback"] == [] and saved["reuse_blocked"] is False
    assert eligible(saved)
    assert memory.CaseStore(store.path).get(case_id) == saved
    assert store.forget(case_id) is True and store.get(case_id) is None
    assert store.forget(case_id) is False


def test_same_pattern_does_not_approve_old_projects_words(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    old = store.get(store.record(example()))
    # Broader retrieval is intentionally useful, but cannot authorize the old literal.
    found = store.search("Set project name to Hospital B", pattern_id="pattern-a")
    assert found[0]["id"] == old["id"]
    assert not eligible(old, request="Set project name to Hospital B")
    goal_b = copy.deepcopy(example()["candidate"]["spec"])
    goal_b["ops"][0]["params"]["replace"] = "Hospital B"
    assert not eligible(old, goal_fingerprint=memory.goal_fingerprint(goal_b))
    assert not eligible(old, request="Set project name to hospital a")
    assert not eligible(old, request="Set project name to Hospital  A")


@pytest.mark.parametrize("changes", [
    {"pattern_id": "other"}, {"template_hashes": ["template-b"]}, {"template_hashes": []},
    {"app_version": "future-version"}, {"request": ""}, {"pattern_id": ""}, {"goal_fingerprint": ""},
])
def test_automatic_eligibility_requires_every_exact_condition(tmp_path, changes):
    store = memory.CaseStore(tmp_path / "cases.db")
    case = store.get(store.record(example()))
    assert not eligible(case, **changes)


def test_request_normalization_preserves_meaningful_wording_and_hash_preserves_literals():
    assert memory.normalized_request("\r\nSet A\r\nthen B  ") == "Set A\nthen B"
    assert memory.goal_fingerprint({"b": 2, "a": [1]}) == memory.goal_fingerprint({"a": [1], "b": 2})
    assert memory.goal_fingerprint({"literal": "Hospital A"}) != memory.goal_fingerprint({"literal": "Hospital a"})
    assert memory.goal_fingerprint({"literal": " A "}) != memory.goal_fingerprint({"literal": "A"})


def test_failures_are_searchable_and_visual_approval_does_not_substitute_for_verification(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    failed = store.record(example(status="failed", structural_verified=False))
    visual_only = store.record(example(structural_verified=False, visual_review={"summary": "Looks correct"}))
    cases = store.search(failure_code="conflict")
    assert {c["id"] for c in cases} == {failed, visual_only}
    assert not any(eligible(case) for case in cases)
    assert store.search(failure_code="unrelated-code") == []


def test_missing_applicability_metadata_cannot_acquire_the_current_version_by_default(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    case_id = store.record(example())
    case = store.get(case_id)
    del case["app_version"]
    assert not eligible(case)
    with sqlite3.connect(store.path) as con:
        payload = json.loads(con.execute("SELECT payload FROM cases WHERE id = ?", (case_id,)).fetchone()[0])
        del payload["app_version"]
        con.execute("UPDATE cases SET payload = ? WHERE id = ?", (json.dumps(payload), case_id))
    assert store.get(case_id) is None


def test_negative_correction_is_retained_and_permanently_blocks_automatic_reuse(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    case_id = store.record(example())
    assert store.feedback(case_id, False, "Wrong project name on the last page")
    assert store.feedback(case_id, True, "Some pages were fine")
    case = store.get(case_id)
    assert case["reuse_blocked"] is True and not eligible(case)
    assert case["feedback"][0]["note"] == "Wrong project name on the last page"
    assert store.search(pattern_id="pattern-a")[0]["id"] == case_id
    assert not eligible(dict(case, reuse_blocked=False))  # A negative record independently blocks it.


def test_negative_correction_follows_existing_and_future_duplicate_repairs(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    first = store.record(example())
    second = store.record(example(notes="Reused successfully on another file"))
    assert store.feedback(second, False, "The repair is wrong")
    assert store.get(first)["reuse_blocked"] and not eligible(store.get(first))
    third = store.record(example())
    assert store.get(third)["reuse_blocked"] and not eligible(store.get(third))
    assert store.feedback(third, True)
    assert store.get(third)["reuse_blocked"]
    # Different project wording, goals, templates, or software versions are separate repairs.
    for changes in ({"request": "Another project"}, {"goal_fingerprint": "a" * 64},
                    {"template_hashes": ["other"]}, {"app_version": "next"}):
        assert not store.get(store.record(example(**changes)))["reuse_blocked"]


def test_forgetting_all_occurrences_forgets_the_repair_correction(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    first = store.record(example())
    second = store.record(example())
    store.feedback(first, False)
    store.forget(first)
    assert store.get(second)["reuse_blocked"]
    store.forget(second)
    assert not store.get(store.record(example()))["reuse_blocked"]


def test_old_schema_corrections_migrate_and_propagate(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    first = store.record(example())
    second = store.record(example())
    store.feedback(first, False)
    with sqlite3.connect(store.path) as con:
        con.execute("DROP TABLE blocked_repairs")
        con.execute("UPDATE cases SET reuse_blocked = 0 WHERE id = ?", (second,))
        con.execute("PRAGMA user_version = 1")
    third = store.record(example())
    assert all(store.get(case_id)["reuse_blocked"] for case_id in (first, second, third))


def test_only_known_case_fields_are_saved_and_candidate_paths_are_redacted(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    token = "template:" + "a" * 64
    candidate = {"kind": "chain", "source_path": "/project/private/source.docx", "chain": {"stages": [
        {"spec": {"ops": [{"params": {"template": token,
                                       "slot_fills": {"TITLE": {"source": "document"}}}}]}}
    ]}, "absolute_reference": "C:\\Secret\\spec.docx", "relative_part": "word/document.xml"}
    case_id = store.record(example(candidate=candidate, source_path="/project/private/source.docx"))
    value = store.get(case_id)
    encoded = json.dumps(value)
    assert "Secret" not in encoded and "/project/private" not in encoded
    assert value["candidate"]["absolute_reference"] == "[local path omitted]"
    assert value["candidate"]["relative_part"] == "word/document.xml"
    assert token in encoded and '"source": "document"' in encoded


@pytest.mark.parametrize("bad", [
    [], {"status": "PASS"}, {"candidate": []}, {"visual_review": "PASS"}, {"usage": []},
    {"structural_verified": 1}, {"template_hashes": "hash"}, {"goal_fingerprint": "bad"},
    {"request": "a" * (memory.MAX_TEXT + 1)}, {"candidate": {"score": float("nan")}},
    {"candidate": {"x": object()}}, {"candidate": {1: "invalid key"}}, {"notes": "\ud800"},
])
def test_invalid_cases_are_rejected_without_creating_a_database(tmp_path, bad):
    store = memory.CaseStore(tmp_path / "cases.db")
    with pytest.raises(memory.RepairMemoryError):
        store.record(bad)
    assert not store.path.exists()


def test_deep_and_oversized_cases_are_rejected(tmp_path):
    deep = {}
    for _ in range(memory.MAX_DEPTH + 1):
        deep = {"next": deep}
    store = memory.CaseStore(tmp_path / "cases.db")
    with pytest.raises(memory.RepairMemoryError, match="deeply nested"):
        store.record(example(candidate=deep))
    with pytest.raises(memory.RepairMemoryError, match="size limit"):
        store.record(example(candidate={str(i): "x" * memory.MAX_TEXT for i in range(10)}))


def test_malformed_database_is_reported_and_preserved(tmp_path):
    path = tmp_path / "cases.db"
    original = b"This is not SQLite"
    path.write_bytes(original)
    store = memory.CaseStore(path)
    with pytest.raises(memory.RepairMemoryError, match="could not be read or saved"):
        store.search("header")
    with pytest.raises(memory.RepairMemoryError, match="could not be read or saved"):
        store.record(example())
    assert path.read_bytes() == original


@pytest.mark.parametrize("payload", ["{bad json", "[]", '{"status":"success","structural_verified":1}',
                                    '{"feedback":[{"accepted":"yes"}]}'])
def test_malformed_rows_are_skipped_and_cannot_be_reused(tmp_path, payload):
    store = memory.CaseStore(tmp_path / "cases.db")
    good = store.record(example())
    corrupt = store.record(example())
    with sqlite3.connect(store.path) as con:
        con.execute("UPDATE cases SET payload = ? WHERE id = ?", (payload, corrupt))
    assert [c["id"] for c in store.list_cases()] == [good]
    assert store.get(corrupt) is None
    assert not store.feedback(corrupt, True)
    assert store.warnings
    assert store.forget(corrupt)


def test_capacity_and_query_limits_are_bounded(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db", max_cases=2)
    first = store.record(example())
    second = store.record(example(request="Another task"))
    third = store.record(example(request="A third task"))
    assert store.get(first) is None
    assert {c["id"] for c in store.list_cases()} == {second, third}
    assert len(store.search(limit=1)) == 1
    for limit in (0, -1, 101, True):
        with pytest.raises(memory.RepairMemoryError):
            store.list_cases(limit)
    with pytest.raises(memory.RepairMemoryError):
        memory.CaseStore(max_cases=0)


def test_concurrent_writes_and_feedback_do_not_lose_records(tmp_path):
    path = tmp_path / "cases.db"

    def record(index):
        return memory.CaseStore(path).record(example(notes=f"attempt {index}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(record, range(32)))
    store = memory.CaseStore(path)
    assert len(set(ids)) == len(store.list_cases()) == 32

    def feedback(index):
        return memory.CaseStore(path).feedback(ids[0], index != 7, f"correction {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(feedback, range(16)))
    case = store.get(ids[0])
    assert len(case["feedback"]) == 16 and case["reuse_blocked"] is True


def test_sql_looking_user_text_is_data_not_a_query(tmp_path):
    store = memory.CaseStore(tmp_path / "cases.db")
    request = "'; DROP TABLE cases; --"
    case_id = store.record(example(request=request))
    assert store.search(request)[0]["request"] == request
    assert store.get(request) is None and not store.forget(request)
    assert store.get(case_id) is not None
