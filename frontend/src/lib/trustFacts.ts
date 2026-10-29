/**
 * Every number, name, host and limit the trust surfaces quote ("Why trust it?" and the "I'm not convinced" dossier),
 * in one place. The copy reads them from here and never types them itself; tests/test_trust_facts.py pins each value
 * to the constant in the Python code it comes from, so changing that constant fails a test until this file — and the
 * copy that quotes it — is updated. The claims ledger (docs/TRUST_CLAIMS.md) names the source of each.
 *
 * Keep one value per line, `key: value,` with a JSON value (a number, a "string" or an array of numbers): the Python
 * test reads this file as text.
 */
export const TRUST_FACTS = {
  // The model and its settings (src/spec_surgeon/llm.py, resources.py, document_task.py; fast mode: llm.py)
  model: "claude-opus-5-5",
  maxOutputTokens: 128000,
  contextWindowTokens: 1000000,
  contextRolloverTokens: 900000,
  conversationEffort: "medium",
  recoveryEffort: "high",
  codegenEffort: "xhigh",
  fastSpeedup: 2.5,
  fastPriceFactor: 2,
  spendingCheckDefaultUsd: 20,
  autoSteps: 6,
  retries: 2,
  retryDelaysSeconds: [5, 20],
  retryAfterCapSeconds: 60,
  stopPollSeconds: 0.25,
  safeStopPollSeconds: 0.25,
  stoppedOutputTokensPerSecond: 150,
  recoveryAttemptsPerGroup: 8,
  stallRepetitions: 3,
  sampleFiles: 8,
  visualPagesPerSample: 2,
  viewPagesPerCall: 3,
  pageImageLongEdgePx: 2000,
  pastRepairCasesSent: 5,
  // The network (updater.py, llm.py and the anthropic SDK's default address)
  anthropicHost: "api.anthropic.com",
  githubApiHost: "api.github.com",
  githubHost: "github.com",
  repo: "Abe-Borg/spec-surgeon",
  updateCheckHours: 24,
  updateCheckTimeoutSeconds: 10,
  updateDownloadTimeoutSeconds: 30,
  maxInstallerMB: 500,
  noUpdateCheckEnv: "SPEC_SURGEON_NO_UPDATE_CHECK",
  modelEnv: "SPEC_SURGEON_MODEL",
  apiAddressEnv: "ANTHROPIC_BASE_URL",
  // The window's local server (web/shell.py, web/server.py)
  loopback: "127.0.0.1",
  tokenHeader: "X-Spec-Surgeon-Token",
  // Sandboxes (codegen.py, process_limits.py, resources.py)
  customMethodSeconds: 60,
  customMethodMemoryMB: 2048,
  customMethodAttempts: 50,
  obliterateCheckRounds: 3,
  analysisSeconds: 120,
  analysisMemoryMB: 1024,
  // Text patterns in a plan (ops/patterns.py; the checker's twin verify/patterns.py)
  regexTimeoutSeconds: 2,
  maxPatternChars: 1000,
  // Attachments (inputs.py, package_limits.py)
  maxTableOrPictureMB: 20,
  maxDocxMB: 256,
  // Renderers (render/word_worker.py, render/libreoffice.py)
  wordTimeoutSeconds: 120,
  wordInstances: 3,
  wordInstancesEnv: "SPEC_SURGEON_WORD_INSTANCES",
  // Worker processes (batch.py, planner.py)
  processMinFiles: 3,
  maxProcessWorkers: 8,
  planProcessMinFiles: 12,
  libreOfficeTimeoutSeconds: 600,
  // Data kept on your computer (settings.py, credentials.py, diagnostics.py, telemetry.py, repair_memory.py, render/cache.py)
  credentialTarget: "Spec Surgeon/ANTHROPIC_API_KEY",
  appLogMB: 2,
  appLogBackups: 3,
  eventJournalMB: 5,
  eventJournalProcesses: 5,
  crashLogsKept: 10,
  renderCacheGB: 2,
  repairMemoryCases: 2000,
  bundleRecentRuns: 5,
  installLogsKept: 3,
  // The watchdog (web/watch.py, frontend/src/lib/diagnostics.ts, web/shell.py)
  pageBeatSeconds: 10,
  pageSilentSeconds: 45,
  pagePollSeconds: 2,
  jobQuietSeconds: 300,
  stopGraceSeconds: 30,
  closeWaitSeconds: 60,
} as const;

/** "claude-opus-5-5" → "Claude Opus 5.5" (the same rule as lib/help.modelLabel, kept here so the copy needs no view). */
export const MODEL_NAME = "Claude Opus 5.5";

/** 128000 → "128,000". */
export function grouped(n: number): string {
  return n.toLocaleString("en-US");
}
