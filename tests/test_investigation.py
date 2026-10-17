"""Generated diagnostic analyses are useful, durable, bounded and read-only."""
import hashlib
import json
import subprocess
import sys

import pytest

from fake_claude import FakeClient, resp, tool
from make_fixtures import build
from spec_surgeon.compiler import _Batch
from spec_surgeon.conversation import Conversation
from spec_surgeon.investigation import AnalysisLimits, check_analysis_source
from test_conversation import REMOVE


@pytest.fixture
def batch(tmp_path):
    source = tmp_path / "spec.docx"
    source.write_bytes(build("21 00 00", "FIRE SUPPRESSION"))
    result = _Batch([source], None)
    result.artifact_dir = tmp_path / "artifacts"
    return result


ANALYSIS = '''from lxml import etree
def analyze(documents, inputs):
    counts = []
    for doc in documents:
        root = etree.fromstring(doc["parts"]["word/document.xml"])
        counts.append({"file": doc["label"], "paragraphs": len(root.findall(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"))})
    return counts
'''


def test_real_analyzer_can_inspect_ooxml_without_changing_sources(batch):
    before = next(iter(batch.files.values())).read_bytes()
    result = batch.run_analysis(ANALYSIS)
    value = json.loads(result["result_json"])
    assert value["ok"] and value["result"][0]["paragraphs"] > 10
    assert result["source_sha256"] == hashlib.sha256(ANALYSIS.encode()).hexdigest()
    assert result["snapshots"][0]["sha256"] == hashlib.sha256(before).hexdigest()
    assert next(iter(batch.files.values())).read_bytes() == before
    assert "never approved wording" in result["authority"]


def test_result_pages_survive_new_batch_instance_and_reject_source_drift(batch):
    result = batch.run_analysis('def analyze(documents, inputs):\n    return "tail" * 30000')
    restored = _Batch(list(batch.files.values()), None)
    restored.artifact_dir = batch.artifact_dir
    content = result["result_json"]
    while result["next_offset"] is not None:
        result = restored.read_analysis_result(result["artifact_id"], result["next_offset"], chars=7000)
        content += result["result_json"]
    assert json.loads(content)["result"] == "tail" * 30000
    source = next(iter(batch.files.values()))
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="stale"):
        restored.read_analysis_result(result["artifact_id"])


@pytest.mark.parametrize("attack", [
    'import os\ndef analyze(documents, inputs): return os.listdir("/")',
    'def analyze(documents, inputs): return open("/etc/passwd").read()',
    'def analyze(documents, inputs): return documents.__class__',
    'import re\ndef analyze(documents, inputs): return re.enum.sys.modules',
    'from lxml import etree\ndef analyze(documents, inputs): return etree.parse("/etc/passwd")',
    'def analyze(documents, inputs): return "{0.__class__}".format(documents)',
])
def test_generated_analysis_escape_paths_are_rejected(batch, attack):
    assert check_analysis_source(attack)
    with pytest.raises(ValueError, match="source rejected"):
        batch.run_analysis(attack)


def test_external_entities_are_never_resolved(batch):
    source = '''from lxml import etree
def analyze(documents, inputs):
    root = etree.fromstring('<!DOCTYPE x [<!ENTITY leak SYSTEM "file:///etc/passwd">]><x>&leak;</x>')
    return etree.tostring(root).decode()
'''
    result = json.loads(batch.run_analysis(source)["result_json"])
    assert result["ok"] and "root:" not in result["result"] and "&leak;" in result["result"]


def test_output_and_wall_time_allowances_stop_runaway_diagnostics(batch):
    batch.analysis_limits = AnalysisLimits(seconds=1, output_bytes=1024)
    result = json.loads(batch.run_analysis('def analyze(documents, inputs): return "x" * 2000')["result_json"])
    assert not result["ok"] and "output exceeded" in result["error"]
    result = json.loads(batch.run_analysis('def analyze(documents, inputs):\n    while True: pass')["result_json"])
    assert not result["ok"] and ("timed out" in result["error"] or "stopped" in result["error"])


def test_audit_guard_denies_reads_and_writes_even_without_linux_seccomp():
    command = '''from spec_surgeon.analysis_runner import install_readonly_guard
install_readonly_guard()
try:
    open("/etc/passwd")
except PermissionError:
    print("read denied")
else:
    raise AssertionError("read escaped")
try:
    open("escape", "w")
except PermissionError:
    print("write denied")
else:
    raise AssertionError("write escaped")
'''
    result = subprocess.run([sys.executable, "-I", "-c", command], capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == "read denied\nwrite denied\n"


def test_analyzer_tool_is_connected_to_real_conversation_dispatch(batch):
    fake = FakeClient(resp(tool("run_analysis", {"source": ANALYSIS}, "analysis")),
                      resp(tool("submit_editspec", {"spec": REMOVE}, "spec")))
    chat = Conversation("remove all headers and footers", list(batch.files.values()),
                        client=fake, work_dir=batch.artifact_dir)
    assert chat.start().status == "ok"
    result = fake.requests[1]["messages"][-1]["content"][0]
    evidence = json.loads(result["content"])
    assert evidence["ok"] and evidence["artifact_id"]
    assert any((batch.artifact_dir / "investigations").glob("*.json"))
