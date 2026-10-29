"""The trust surfaces quote the code: every value in frontend/src/lib/trustFacts.ts is pinned here to the constant it
comes from (docs/TRUST_CLAIMS.md names each), so changing a limit, a model, a host or a setting fails this test until
the copy that quotes it is updated. The constants are read from the source with ``ast`` — not imported — so a test's
own patches (``tests/conftest.py`` sets ``llm.RETRIES`` to 0, for one) cannot hide a drift.

The rule (CLAUDE.md, "The trust surfaces"): a change to a behaviour the trust surfaces describe updates them in the
same change."""
from __future__ import annotations

import ast
import json
import operator
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "spec_surgeon"
FRONTEND = ROOT / "frontend" / "src"
FACTS_FILE = FRONTEND / "lib" / "trustFacts.ts"
COPY_FILES = [FRONTEND / "components" / "WhyTrustIt.tsx", FRONTEND / "components" / "TrustDossier.tsx",
              FRONTEND / "lib" / "trustRuntime.ts", FRONTEND / "components" / "TrustDiagram.tsx"]
MIB = 2 ** 20

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Pow: operator.pow,
        ast.FloorDiv: operator.floordiv, ast.Div: operator.truediv}


def _value(node):
    """A literal or simple arithmetic over literals (``24 * 3600``, ``5 * 2**20``); anything else is a test defect."""
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_value(node.left), _value(node.right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args[1:]:
        return _value(node.args[1])                      # os.environ.get("NAME", default) → the default
    return ast.literal_eval(node)


def _tree(path: str) -> ast.Module:
    return ast.parse((SRC / path).read_text(encoding="utf-8"))


def const(path: str, name: str):
    """A module-level ``NAME = value`` (or ``NAME: type = value``)."""
    for node in _tree(path).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return _value(node.value)
    raise AssertionError(f"{name} not found in {path}")


def class_const(path: str, cls: str, name: str):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                targets = (item.targets if isinstance(item, ast.Assign) else
                           [item.target] if isinstance(item, ast.AnnAssign) else [])
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    return _value(item.value)
    raise AssertionError(f"{cls}.{name} not found in {path}")


def default(path: str, func: str, arg: str):
    """The default value of ``arg`` in ``def func(...)``."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.FunctionDef) and node.name == func:
            a = node.args
            positional = a.posonlyargs + a.args
            for argument, value in zip(positional[len(positional) - len(a.defaults):], a.defaults):
                if argument.arg == arg:
                    return _value(value)
            for argument, value in zip(a.kwonlyargs, a.kw_defaults):
                if argument.arg == arg and value is not None:
                    return _value(value)
    raise AssertionError(f"default of {arg} in {func} not found in {path}")


def source(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


def one(pattern: str, text: str, what: str) -> str:
    found = re.findall(pattern, text)
    assert len(found) == 1, f"{what}: expected one match of {pattern!r}, found {found}"
    return found[0]


def facts() -> dict:
    """``key: value,`` lines of TRUST_FACTS, each value JSON."""
    text = FACTS_FILE.read_text(encoding="utf-8")
    body = text[text.index("export const TRUST_FACTS = {"):text.index("} as const;")]
    out = {}
    for line in body.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        m = re.fullmatch(r"(\w+): (.+),", line)
        assert m, f"trustFacts.ts: cannot read the line {line!r} (one `key: JSON value,` per line)"
        out[m.group(1)] = json.loads(m.group(2))
    return out


def expected() -> dict:
    """Each fact, read from the code that makes it true."""
    llm_src = source("llm.py")
    return {
        "model": one(r'os\.environ\.get\("SPEC_SURGEON_MODEL", "([^"]+)"\)', llm_src, "llm.DEFAULT_MODEL"),
        "maxOutputTokens": const("resources.py", "DEFAULT_MAX_OUTPUT_TOKENS"),
        "contextWindowTokens": const("llm.py", "CONTEXT_WINDOWS")[
            one(r'os\.environ\.get\("SPEC_SURGEON_MODEL", "([^"]+)"\)', llm_src, "llm.DEFAULT_MODEL")],
        "contextRolloverTokens": const("resources.py", "DEFAULT_CONTEXT_TOKENS"),
        "conversationEffort": const("document_task.py", "CONVERSATION_EFFORT"),
        "recoveryEffort": const("document_task.py", "RECOVERY_EFFORT"),
        "codegenEffort": const("resources.py", "CODEGEN_EFFORT"),
        "outlineEffort": const("resources.py", "OUTLINE_EFFORT"),
        "outlineParagraphsPerCall": const("outline.py", "OUTLINE_CHUNK"),
        "outlineMaxParagraphs": const("outline.py", "OUTLINE_MAX"),
        "outlineTextChars": const("outline.py", "TEXT_CLIP"),
        "fastSpeedup": const("llm.py", "FAST_SPEEDUP"),
        "fastPriceFactor": const("llm.py", "FAST_PRICE_FACTOR"),
        "spendingCheckDefaultUsd": const("settings.py", "DEFAULT_SPENDING_LIMIT_USD"),
        "autoSteps": const("document_task.py", "AUTO_STEPS"),
        "retries": const("llm.py", "RETRIES"),
        "retryDelaysSeconds": [int(s) for s in const("llm.py", "RETRY_DELAYS")],
        "retryAfterCapSeconds": const("llm.py", "RETRY_AFTER_CAP"),
        "stopPollSeconds": const("llm.py", "STOP_POLL_SECONDS"),
        "stoppedOutputTokensPerSecond": const("llm.py", "STOPPED_OUTPUT_RATE"),
        "recoveryAttemptsPerGroup": const("document_task.py", "RECOVERY_ATTEMPTS"),
        "stallRepetitions": const("resources.py", "DEFAULT_STALL_REPETITIONS"),
        "sampleFiles": const("resources.py", "DEFAULT_SAMPLE_FILES"),
        "visualPagesPerSample": int(one(r'getattr\(s, "look_pages", None\) or \[1\]\)\[:(\d+)\]', source("vision.py"),
                                        "vision.review_samples pages per sample")),
        "viewPagesPerCall": int(one(r"for n in \(pages or \[1\]\) if int\(n\) >= 1\}\)\[:(\d+)\]", source("compiler.py"),
                                    "compiler._Batch.view_pages")),
        "pageImageLongEdgePx": const("vision.py", "LONG_EDGE_PX"),
        "pastRepairCasesSent": int(one(r"template_hashes=sorted\(ctx\[\"templates\"\]\), limit=(\d+),", source("recovery.py"),
                                       "recovery._investigate's repair-memory search")),
        "anthropicHost": "api.anthropic.com",            # checked against the SDK below
        "githubApiHost": re.match(r"https://([^/]+)/", const("updater.py", "API_LATEST")).group(1),
        "githubHost": re.match(r"https://([^/]+)/", const("updater.py", "RELEASES_PAGE")).group(1),
        "repo": const("about.py", "GITHUB_REPO"),
        "updateCheckHours": const("updater.py", "CHECK_INTERVAL") // 3600,
        "updateCheckTimeoutSeconds": int(default("updater.py", "check_for_update", "timeout")),
        "updateDownloadTimeoutSeconds": int(default("updater.py", "download_installer", "timeout")),
        "maxInstallerMB": const("updater.py", "MAX_INSTALLER_BYTES") // MIB,
        "noUpdateCheckEnv": const("updater.py", "NO_CHECK_ENV"),
        "apiAddressEnv": "ANTHROPIC_BASE_URL",           # checked against the SDK below
        "modelEnv": one(r'os\.environ\.get\("(SPEC_SURGEON_MODEL)"', llm_src, "the model's environment variable"),
        "loopback": one(r'host="([\d.]+)", port=port', source("web/shell.py"), "web.shell.serve's host"),
        "tokenHeader": const("web/server.py", "TOKEN_HEADER"),
        "customMethodSeconds": const("codegen.py", "SANDBOX_TIMEOUT"),
        "customMethodMemoryMB": const("process_limits.py", "DEFAULT_MEMORY_MB"),
        "customMethodAttempts": const("resources.py", "DEFAULT_MAX_ATTEMPTS"),
        "obliterateCheckRounds": const("codegen.py", "UNGUARDED_CHECK_ROUNDS"),
        "analysisSeconds": const("resources.py", "DEFAULT_ANALYSIS_SECONDS"),
        "analysisMemoryMB": const("resources.py", "DEFAULT_ANALYSIS_MEMORY_MB"),
        "regexTimeoutSeconds": const("ops/patterns.py", "REGEX_TIMEOUT_S"),
        "maxPatternChars": const("ops/patterns.py", "MAX_PATTERN_CHARS"),
        "maxTableOrPictureMB": const("inputs.py", "MAX_BYTES") // MIB,
        "maxDocxMB": class_const("package_limits.py", "ArchiveLimits", "max_archive_bytes") // MIB,
        "wordTimeoutSeconds": int(const("render/word_worker.py", "DEFAULT_TIMEOUT")),
        "wordInstances": const("render/word_worker.py", "WORD_INSTANCES"),
        "wordInstancesEnv": const("render/word_worker.py", "WORD_INSTANCES_ENV"),
        "processMinFiles": const("batch.py", "PROCESS_MIN_FILES"),
        "maxProcessWorkers": const("batch.py", "MAX_PROCESS_WORKERS"),
        "planProcessMinFiles": const("planner.py", "PLAN_PROCESS_MIN_FILES"),
        "libreOfficeTimeoutSeconds": default("render/libreoffice.py", "render_groups", "timeout"),
        "credentialTarget": const("credentials.py", "TARGET"),
        "appLogMB": int(one(r"maxBytes=(\d+) \* 2 \*\* 20", source("diagnostics.py"), "the app log's size")),
        "appLogBackups": int(one(r"maxBytes=\d+ \* 2 \*\* 20, backupCount=(\d+)", source("diagnostics.py"),
                                 "the app log's backups")),
        "eventJournalMB": int(one(r"maxBytes=(\d+) \* 2\*\*20", source("telemetry.py"), "the event journal's size")),
        "eventJournalProcesses": const("telemetry.py", "KEEP_EVENT_PROCESSES"),
        "crashLogsKept": class_const("diagnostics.py", "CrashWatch", "KEEP"),
        "renderCacheGB": const("render/cache.py", "DEFAULT_LIMIT_BYTES") // 1024 ** 3,
        "repairMemoryCases": const("repair_memory.py", "MAX_CASES"),
        "bundleRecentRuns": const("diagnostics.py", "BUNDLE_RECENT_RUNS"),
        "installLogsKept": default("updater.py", "cleanup_downloads", "keep_logs"),
        "pageBeatSeconds": const("web/watch.py", "PAGE_BEAT_S"),
        "pageSilentSeconds": const("web/watch.py", "PAGE_SILENT_S"),
        "pagePollSeconds": 2,                            # checked against App.tsx below
        "jobQuietSeconds": const("web/watch.py", "JOB_QUIET_S"),
        "stopGraceSeconds": const("web/watch.py", "STOP_GRACE_S"),
        "closeWaitSeconds": int(default("web/shell.py", "shutdown", "wait")),
    }


def test_every_trust_fact_is_the_code_s_value():
    have, want = facts(), expected()
    assert set(have) == set(want), ("trustFacts.ts and this test list different facts: every fact the copy quotes needs "
                                    f"its source here (only in trustFacts: {set(have) - set(want)}; only here: "
                                    f"{set(want) - set(have)})")
    wrong = {k: (have[k], want[k]) for k in want if have[k] != want[k]}
    assert not wrong, ("the trust copy quotes values the code no longer has — update frontend/src/lib/trustFacts.ts, the "
                       f"copy that reads it and docs/TRUST_CLAIMS.md: {wrong} (quoted, code)")


def test_secondary_sources_agree():
    """Facts with a second home in the code: the page's own copy of a constant, the update check's off switch."""
    f = facts()
    app = (FRONTEND / "App.tsx").read_text(encoding="utf-8")
    assert f"window.setInterval(() => void refresh(), {f['pagePollSeconds'] * 1000})" in app
    assert re.search(r"\}, " + str(f["pagePollSeconds"] * 1000) + r"\);\n    return stop;", app), "pollUpdate's period"
    beat = (FRONTEND / "lib" / "diagnostics.ts").read_text(encoding="utf-8")
    assert re.search(r"export const BEAT_MS = 10_000;", beat) and f["pageBeatSeconds"] == 10
    assert f'os.environ.get("{f["noUpdateCheckEnv"]}")' in source("gui.py"), "gui.main's own check of the off switch"
    assert "MAX_ITER = DEFAULT_MAX_ATTEMPTS" in source("codegen.py"), "a custom method's attempts"
    # the checker's own deadline and length limit, written independently, are the same numbers
    assert const("verify/patterns.py", "V_REGEX_TIMEOUT_S") == f["regexTimeoutSeconds"]
    assert const("verify/patterns.py", "V_MAX_PATTERN_CHARS") == f["maxPatternChars"]
    vp = re.search(r'"pages": \{"type": "array", "items": \{"type": "integer", "minimum": 1\}, "maxItems": (\d+)\}',
                   source("compiler.py"))
    assert vp and int(vp.group(1)) == f["viewPagesPerCall"], "view_pages' schema says the same page count"


def test_the_anthropic_host_is_the_sdk_s_default(monkeypatch):
    anthropic = pytest.importorskip("anthropic")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    client = anthropic.Anthropic(api_key="sk-ant-test-not-a-key")
    assert client.base_url.host == facts()["anthropicHost"]
    assert "base_url" not in source("llm.py").split("def client()")[1].split("def ")[0], "llm.client sets no address"
    # the one switch that moves it, which the copy names: the SDK reads it from the environment
    monkeypatch.setenv(facts()["apiAddressEnv"], "https://proxy.example.invalid")
    assert anthropic.Anthropic(api_key="sk-ant-test-not-a-key").base_url.host == "proxy.example.invalid"


def test_the_copy_s_cache_and_fallback_sentences_match_the_code():
    """Words, not digits, but still facts: the hour and the five minutes of prompt caching, and the refusal fallback."""
    dossier = (FRONTEND / "components" / "TrustDossier.tsx").read_text(encoding="utf-8")
    assert const("llm.py", "SYSTEM_CACHE") == {"type": "ephemeral", "ttl": "1h"}
    assert const("llm.py", "MESSAGES_CACHE") == {"type": "ephemeral"}          # no ttl: the default five minutes
    assert "cached by Anthropic for an hour" in dossier and "the default five minutes" in dossier
    assert 'fallbacks="default"' in source("llm.py")
    assert "Anthropic may answer it with another model" in dossier
    # The effort rule the copy states: inside a budget scope the innermost budget's effort wins. The visual check and
    # a custom method have no budget of their own, so they run at the conversation's; the outcome planner and the
    # recovery it starts have their own, at the conversation object's effort — resources.DEFAULT_EFFORT, since the
    # window builds the conversation without one — which the copy quotes as recoveryEffort.
    assert "effort = budgets[-1].effort" in source("llm.py")
    assert "with llm.budget_scope(budget):" in source("campaign_planner.py")
    window_conversation = re.search(r"self\.conversation = Conversation\((.*?)\)\n", source("gui.py"), re.S).group(1)
    assert "effort" not in window_conversation
    assert const("resources.py", "DEFAULT_EFFORT") == facts()["recoveryEffort"]
    assert "options.max_attempts = self.recovery_attempts" in source("gui.py"), "the outcome plan's recovery: 8 too"


def test_the_copy_types_no_number_of_its_own():
    """Every number in the trust copy comes from TRUST_FACTS: no digit in a string or JSX text of the copy files, beyond
    names that carry one (the checks V1–V10, SHA-256 and SHA256SUMS) and the cards' inventory ids (A1, B1…)."""
    allowed = re.compile(r"V1–V10|V\d+|SHA-?256(?:SUMS)?|x64")
    for path in COPY_FILES[:3]:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = "\n".join(line for line in text.splitlines()
                         if not line.strip().startswith(("//", "import ", "*", "/**")))
        text = re.sub(r"className=(\"[^\"]*\"|\{`[^`]*`\})", "", text)
        text = re.sub(r"\$\{[^}]*\}", "", text)
        strings = re.findall(r'"([^"\n]*)"|`([^`]*)`', text)
        jsx = re.findall(r">([^<>{}();=]+)<", text)
        pieces = [a or b for a, b in strings] + jsx
        offenders = [p for p in pieces if re.search(r"\d", allowed.sub("", p)) and not re.fullmatch(r"[AB]\d+", p)]
        assert not offenders, f"{path.name}: numbers typed into the copy instead of read from TRUST_FACTS: {offenders}"


def test_the_sample_count_is_a_target_not_a_minimum():
    """``representative_samples`` fills up to its target from the files there are: a batch of three has three samples."""
    from spec_surgeon.sampling import representative_samples
    assert len(representative_samples(["a", "b", "c"], pattern_key=lambda x: x)) == 3
    for path in COPY_FILES[:3]:
        assert not re.search(r"at least \$?\{F\.sampleFiles\}", path.read_text(encoding="utf-8")), path.name


def test_the_ledger_covers_the_facts_and_the_rule():
    ledger = (ROOT / "docs" / "TRUST_CLAIMS.md").read_text(encoding="utf-8")
    assert "The trust surfaces are a contract" in ledger
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "The trust surfaces are a contract" in claude
    for key in ("DEFAULT_SPENDING_LIMIT_USD", "AUTO_STEPS", "CHECK_INTERVAL", "SANDBOX_TIMEOUT", "DEFAULT_MEMORY_MB",
                "MAX_CASES", "STOPPED_OUTPUT_RATE", "RETRY_DELAYS", "CONVERSATION_EFFORT", "RECOVERY_EFFORT",
                "CODEGEN_EFFORT", "UNGUARDED_CHECK_ROUNDS", "STREAMED_TOOLS", "REGEX_TIMEOUT_S", "MAX_PATTERN_CHARS",
                "CONTEXT_WINDOWS", "DEFAULT_CONTEXT_TOKENS"):
        assert key in ledger, f"the ledger names no source for {key}"


def test_the_copy_names_the_exceptions_the_code_has():
    """Behaviours the copy states in words, each tied to the code that has it: a custom method under an outcome plan
    is not held to formatting only; a reply Claude declines leaves the conversation; two documents of one name are
    named in the chat."""
    level1 = (FRONTEND / "components" / "WhyTrustIt.tsx").read_text(encoding="utf-8")
    dossier = (FRONTEND / "components" / "TrustDossier.tsx").read_text(encoding="utf-8")
    runtime = (FRONTEND / "lib" / "trustRuntime.ts").read_text(encoding="utf-8")
    # the governed path: hard_limits returns before the formatting-only limits when an outcome contract governs
    hard = source("codegen.py").split("def hard_limits(")[1].split("\ndef ")[0]
    assert hard.index("if goal_contract is not None:") < hard.index("may not change text in surviving parts")
    assert "formatting-only custom_transform_limits do not apply to this governed path" in source("recovery.py")
    assert "formatting only, unless it carries\n                out an outcome plan" in level1
    assert "Formatting only, except under an outcome plan" in dossier
    assert "A custom method writes its own prediction." in dossier
    assert "sb.plan" in source("codegen.py").split("def _trial(")[1], "a custom method's trial checks its own plan"
    # a declined reply is taken back
    assert 'if problem and getattr(resp, "stop_reason", None) == "refusal":' in source("conversation.py")
    assert "def _take_back(self)" in source("conversation.py")
    assert "self._reply_mark = (self.messages[-1], before)" in source("conversation.py").split("def reply(")[1].split("\n    def ")[0]
    assert "self._reply_mark" not in source("conversation.py").split("def start(")[1].split("\n    def ")[0]
    assert "that reply is taken back" in runtime and "a first request, or a reply declined" in runtime
    # two attached documents of one name
    assert "def _say_same_names(" in source("document_task.py")
    assert "Two attached documents with the same name" in runtime
    # the browser fallback, with the token in the address
    shell = source("web/shell.py")
    assert "webbrowser.open(url)" in shell and '?token={state.token}' in shell
    assert "opens the page in\n                your default browser instead" in dossier


def test_fast_mode_is_off_until_turned_on_and_priced_from_the_table():
    """The words the copy says about Fast responses, each tied to the code: off by default (the setting and the
    module), turned on only by the window's setting, every fast price the factor times the standard one, a busy fast
    capacity falling back to standard speed, and the worker processes and Word instances the copy names."""
    assert const("llm.py", "FAST_MODE") is False
    fields = {n.target.id: n.value for n in ast.walk(_tree("settings.py"))
              if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    assert ast.literal_eval(fields["fast_mode"]) is False
    prices, fast = const("llm.py", "PRICES"), const("llm.py", "FAST_PRICES")
    factor = facts()["fastPriceFactor"]
    assert fast and all(fast[m] == tuple(factor * p for p in prices[m]) for m in fast)
    assert "llm.set_fast_mode(settings.fast_mode)" in source("web/server.py")
    assert "_fast_capacity(exc) or _speed_refused(exc)" in source("llm.py")
    assert const("batch.py", "DEFAULT_WORKER_MODE") == "auto"
    assert "(os.cpu_count() or 2) // 2" in source("render/word_worker.py"), "one Word per two cores at most"
    dossier = (FRONTEND / "components" / "TrustDossier.tsx").read_text(encoding="utf-8")
    level1 = (FRONTEND / "components" / "WhyTrustIt.tsx").read_text(encoding="utf-8")
    assert "Fast responses (Settings) is off until you turn it on" in dossier
    assert "Fast responses, off until you turn it on in Settings" in level1
    ledger = (ROOT / "docs" / "TRUST_CLAIMS.md").read_text(encoding="utf-8")
    for key in ("FAST_PRICES", "FAST_SPEEDUP", "FAST_PRICE_FACTOR", "PROCESS_MIN_FILES", "MAX_PROCESS_WORKERS",
                "PLAN_PROCESS_MIN_FILES", "WORD_INSTANCES"):
        assert key in ledger, f"the ledger names no source for {key}"
