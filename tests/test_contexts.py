import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon.compiler import _Batch
from spec_surgeon.contexts import ContextLimit, checkpoint_history, estimate_context_tokens, manage_context
from spec_surgeon.conversation import Conversation, check_pairing
from test_conversation import REMOVE


def history():
    return [{"role": "user", "content": "approved request"},
            {"role": "assistant", "content": [
                SimpleNamespace(type="thinking", thinking="signed original thought" * 2000,
                                signature="original-signature"),
                {"type": "tool_use", "id": "call", "name": "read_xml_page", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": "evidence"}]}]


def test_rollover_preserves_signed_history_and_opens_a_paired_new_epoch(tmp_path):
    messages = history()
    original = copy.deepcopy(messages)
    state = {}
    fresh = manage_context(messages, system="system", tools=[], folder=tmp_path, state=state,
                           authority={"request": "approved request"}, max_tokens=4000)
    assert messages == original and fresh is not messages
    check_pairing(fresh)
    assert len(fresh) == 1 and state["epoch"] == 1
    archived = json.loads((tmp_path / "context" / f"{state['archives'][0]['artifact_id']}.json").read_text())
    thought = archived["messages"][1]["content"][0]
    assert thought["signature"] == "original-signature"
    assert thought["thinking"] == "signed original thought" * 2000
    assert "signed original thought" not in fresh[0]["content"]
    assert "never" in json.loads(fresh[0]["content"])["notice"] or "do not approve" in fresh[0]["content"]


def test_an_epoch_cannot_change_old_thinking_or_tool_schema(tmp_path):
    messages, state = history(), {}
    arguments = dict(system="system", tools=[], folder=tmp_path, state=state, authority={})
    checkpoint_history(messages, **arguments)
    messages[1]["content"][0].signature = "changed"
    with pytest.raises(ContextLimit, match="history changed"):
        checkpoint_history(messages, **arguments)
    with pytest.raises(ContextLimit, match="history changed"):
        checkpoint_history(history(), **dict(arguments, tools=[{"name": "new_tool"}]))


def test_sdk_optional_defaults_do_not_break_epoch_prefix_after_recovery_reload(tmp_path):
    from spec_surgeon.recovery_state import json_value

    class ProviderBlock:
        def model_dump(self, *, mode, exclude_none=False):
            value = {"type": "thinking", "thinking": "unaltered", "signature": "signed", "optional": None}
            return {k: v for k, v in value.items() if v is not None} if exclude_none else value

    messages = [{"role": "user", "content": "request"},
                {"role": "assistant", "content": [ProviderBlock()]},
                {"role": "user", "content": "continue"}]
    state = {}
    arguments = dict(system="system", tools=[], folder=tmp_path, state=state, authority={})
    checkpoint_history(messages, **arguments)
    restored = json.loads(json.dumps(json_value(messages)))
    restored.append({"role": "assistant", "content": [{"type": "text", "text": "next response"}]})
    checkpoint_history(restored, **arguments)
    saved = json.loads((tmp_path / "context" / "active-0.json").read_text())
    assert saved["messages"][1]["content"][0]["signature"] == "signed"


def test_complete_archive_pages_restore_with_scope_and_source_pins(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    batch = _Batch([source], None)
    state = {}
    manage_context(history(), system="system", tools=[], folder=tmp_path / "work", state=state,
                   authority={"request": "approved"}, source_pins=batch.source_pins, max_tokens=4000)
    restored = _Batch([source], None)
    restored.context_dir = tmp_path / "work"
    restored.context_state = json.loads(json.dumps(state))
    index = restored.read_context_archive()
    artifact_id = json.loads(index["archive_json"])[0]["artifact_id"]
    offset, content = 0, ""
    while offset is not None:
        result = restored.read_context_archive(artifact_id, offset, chars=3000)
        content += result["archive_json"]
        offset = result["next_offset"]
    assert hashlib.sha256(content.encode()).hexdigest() == artifact_id
    source.write_bytes(source.read_bytes() + b"drift")
    with pytest.raises(ValueError, match="stale"):
        restored.read_context_archive(artifact_id)


def test_tool_calls_must_be_answered_before_an_epoch_rolls(tmp_path):
    from spec_surgeon.conversation import ProtocolError
    with pytest.raises(ProtocolError):
        manage_context(history()[:-1], system="system", tools=[], folder=tmp_path, state={}, authority={})


def test_tool_schema_upgrade_starts_a_separate_epoch(tmp_path):
    from spec_surgeon.contexts import start_epoch
    state = {}
    old = [{"role": "user", "content": "request"}]
    fresh = start_epoch(old, system="old", tools=[{"name": "old_tool"}], folder=tmp_path,
                        state=state, authority={"request": "request"},
                        next_system="new", next_tools=[{"name": "analyzer"}])
    sealed = json.loads((tmp_path / "context" / f"{state['archives'][0]['artifact_id']}.json").read_text())
    active = json.loads((tmp_path / "context" / "active-1.json").read_text())
    assert sealed["system"] == "old" and sealed["tools"] == [{"name": "old_tool"}]
    assert active["system"] == "new" and active["tools"] == [{"name": "analyzer"}]
    assert old == [{"role": "user", "content": "request"}]
    check_pairing(fresh)


def test_real_conversation_continues_after_multiple_context_rollovers(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    fake = FakeClient(*(resp(tool("read_xml_page", {"file": source.name, "chars": 16000}, f"read{i}"))
                        for i in range(14)), resp(tool("submit_editspec", {"spec": REMOVE}, "done")))
    chat = Conversation("remove all headers and footers", [source], client=fake, work_dir=tmp_path / "work")
    base = estimate_context_tokens(system=chat.system, tools=chat.tools, messages=[])
    chat.max_context_tokens = base + 12000
    result = chat.start()
    assert result.status == "ok" and len(fake.requests) == 15
    assert chat.context_state["epoch"] >= 2
    for request in fake.requests:
        check_pairing(request["messages"])
        assert estimate_context_tokens(system=request["system"], tools=request["tools"],
                                       messages=request["messages"]) <= chat.max_context_tokens + 100
    assert chat.sources == ["remove all headers and footers"]
    assert chat.to_dict()["context"]["archives"]


def test_the_default_allowance_leaves_a_conversation_room_for_two_page_images(tmp_path):
    """Headroom guard. Every conversation's system prompt carries the whole op catalog (ops, schema, worked
    examples), so each batch of operations shrinks what is left of the default context allowance before a first
    new epoch (built-in batch 5 took it from about 33,500 estimated tokens to about 15,000). The first request
    plus two page images (one ``view_pages`` call) must still fit; when this fails, compacting the catalog or
    raising ``resources.DEFAULT_CONTEXT_TOKENS`` is the owner's decision — never loosen this guard to pass."""
    from spec_surgeon.resources import DEFAULT_CONTEXT_TOKENS
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    fake = FakeClient(resp(tool("submit_editspec", {"spec": REMOVE}, "done")))
    chat = Conversation("remove all headers and footers", [source], client=fake, work_dir=tmp_path / "work")
    assert chat.max_context_tokens == DEFAULT_CONTEXT_TOKENS
    assert chat.start().status == "ok"
    first = fake.requests[0]
    used = estimate_context_tokens(system=first["system"], tools=first["tools"], messages=first["messages"])
    image = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}
    per_image = (estimate_context_tokens(system="", tools=[], messages=[{"role": "user", "content": [image]}])
                 - estimate_context_tokens(system="", tools=[], messages=[{"role": "user", "content": []}]))
    assert used + 2 * per_image <= DEFAULT_CONTEXT_TOKENS, (
        f"the first request is {used} estimated tokens; two page images ({2 * per_image}) would pass the default "
        f"allowance of {DEFAULT_CONTEXT_TOKENS} — compact the op catalog or raise the allowance")
