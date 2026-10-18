# Office profiles and specification review: qualification record

Date: October 18, 2026.

The development corpus is synthetic. These results exercise actual generated Word
files, the real deterministic engine, independent verification, persisted workspaces
and report adapters. They do not establish client acceptance, Microsoft Word
appearance or live-model performance.

| Workload | Recorded result | Evidence limit |
| --- | --- | --- |
| Twelve-document corpus, ordinary batch, two independent runs | 12/12 PASS in each run; original acquired SHA-256 pins match; V1–V10 coverage retained with V6/V7 N/A | Both variants use the current implementation; no renderer, provider or human review |
| Two-stage margin chain over one corpus document | PASS; both stages retained; original source counted once | Deterministic engine qualification only |
| Saved-recipe campaign over one corpus document | READY; child recovery retained; source hash matches | No live model or Word review |
| Focused and integration regression suite | 177 tests passed in 59.72 seconds | Completed before the final evaluation-hardening edits |
| Final project-evidence, desktop-editor and workflow-evaluation tests | 36 tests passed in 3.13 seconds after environment reconnection | Covers the final hardening changes; consolidated repository qualification is recorded separately |
| Profile, desktop, comment, schema and selective-formatting integration checks | 202 tests passed in 27.68 seconds | Includes adversarial preservation and exact approval checks; no native Word automation |
| Adjacent paragraph grouping | 69 profile, CLI and settings tests passed in 19.99 seconds | Tracked and untracked executions retain exact targets across excluded and unmapped gaps |
| Distribution packaging | Source archive and wheel build PASS; twine checks PASS; fresh core-only wheel installation PASS with packaged example/prompt parity | Native Windows installer qualification runs in pull-request CI |
| Mixed valid/damaged document batch, two independent runs | One PASS and one EXCEPTION in each run; both sources stay in evaluation; the incomplete batch cannot count as accepted | Deliberately invalid synthetic DOCX input, no live provider |
| In-place delivery evidence | PASS; report retains the original acquired SHA-256 although delivered source bytes changed | Synthetic one-document margin edit |
| Office-profile LibreOffice render smoke | PASS across three stages and independent original-to-final approved-goal verification; source/template hashes unchanged; direct font/size removed while italic preserved; eight one-page PDFs | V6 PASS during execution, V7 N/A for expected reflow, V8 N/A for no revisions; preview summaries still record V6 N/A despite available render artifacts; no Microsoft Word or human acceptance |

Reproduce the corpus and project-evidence tests with:

```text
QT_QPA_PLATFORM=offscreen python -m pytest -q tests/test_workflow_evaluation.py tests/test_project_evidence.py tests/test_project_evidence_dialog.py
```

The synthetic corpus generator is `tests/workflow_corpus.py`. It covers ordinary,
typed and styled headings; direct formatting; automatic lists; tables;
headers/footers; Unicode; repeated numeric provisions; tracked insertion; multiple
sections; and blank paragraphs. The two compared variants use identical source
bytes. Human-review values in aggregation unit tests are explicitly synthetic
judgments, not acceptance of the generated documents.

The completed 177-test command additionally included:

```text
tests/test_campaign.py
tests/test_recovery_evaluation.py
tests/test_chain.py
tests/test_workspace_cli.py
tests/test_workspace_gui.py
tests/test_input_snapshot_privacy.py
tests/test_recovery_merge_inputs.py
```

The final additions cover actual damaged-document refusal, original-byte pins for
in-place delivery, incomplete campaign costs/outcome evidence, and equivalently
relocated template bindings. The 36-test run completed after connectivity was
restored and includes those final additions. The earlier 177-test total is recorded
separately rather than being presented as a rerun of the final source state.

Qualification on representative held-out client files still requires human review
in Microsoft Word and actual baseline/current revisions. Live-provider performance
requires recorded provider runs. Complete repository test results and native Windows
installer checks are retained by the pull-request CI jobs.

See [whole-workflow evaluation](../WORKFLOW_EVALUATION_V3.md) for artifact pinning,
cost accounting and comparison rules, and [project evidence](../PROJECT_EVIDENCE.md)
for scoped facts and the approved standards register.
