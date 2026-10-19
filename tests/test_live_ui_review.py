"""Independent UI review: per-job model activity stays truthful. The worker observes every model call through
``llm.model_progress_scope``; an observer that fails must never change what the model answered."""
from __future__ import annotations

from fake_claude import FakeClient, resp, text
from spec_surgeon import llm


def test_progress_observer_failure_never_changes_accepted_model_response():
    fake = FakeClient(resp(text("accepted"), stop="end_turn"))

    def broken(*args):
        raise RuntimeError("observer failed")

    with llm.model_progress_scope(broken):
        response = llm.call(fake, model=llm.DEFAULT_MODEL, system="s", tools=[], messages=[])
    assert response.content[0].text == "accepted" and len(fake.requests) == 1
