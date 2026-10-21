# Spec Surgeon — Implementation plan: one conversation that delivers

Oct 20, 2026 · @Abraham (direction: "unfuck this mess; make it beautiful and intuitive; fix all of it") · agent
sessions execute

## Where this round starts

The analysis of 2026-10-20 (this session) compared the window with Build-a-Spec and traced a request end to end:

| What | Finding |
| --- | --- |
| The engine | Profiling, planning, applying and verifying four files take about two seconds without rendering |
| The window | A Qt form that rebuilt every card after each job and showed nothing while a job ran |
| The model contract | Every turn had to end in a submission or a question; the model could not talk; its prompt said "offer a chain" eleven times and the submission tool had no chain shape |
| The two-step message | A font change beside a header/footer template is refused inside one specification by design (`ops/base.py`); chains were the remedy but existed only on the command line; a follow-up message was a revision against the originals, where the two operations collide again |
| Rendering | One fresh Word instance per file, source and output, serially; the run re-rendered what the preview rendered |
| The model calls | Three to seven sequential Opus calls at high effort with a `describe_op` round trip per operation, plus a visual check with up to thirty-two images |

The owner's definition of done is unchanged: a person completes a real document task without learning a single
Spec Surgeon concept. This round adds: the user never performs a procedure (no steps, no re-attaching copies), sees
what the app is doing while it works, and gets a window that looks and feels like Build-a-Spec's.

## Principles for this round

1. **Composition is the engine's job.** Operations that overlap are cut into ordered steps by the engine and run as a
   chain, each step planned and verified on the step before it (`editspec.stage_cuts`, `compiler.compose_submission`).
   The model never splits a request and the user never hears the word "step" except as a quiet count.
2. **The next request builds on the result.** After a run the delivered copies are the working set; the originals
   stay untouched; "Start over from the originals" is one reply away.
3. **No dead air.** Every job streams what the model writes, what it is doing in plain words, the plan before any page
   renders, the pages as they render, and the copies as they are written (docs/WEB_API.md).
4. **Defaults first, questions last.** The model's posture is Build-a-Spec's: state assumptions and go ahead; at most
   three questions per turn, each with a recommended answer; suggested replies to tap.
5. **The window is a web page.** React and Tailwind in a native WebView2 window, copied from Build-a-Spec under the
   owner's copy-based reuse rule; the Qt window is deleted, not hidden.
6. **Render once.** One Word instance per batch; a content-hash render cache shared by preview and run
   (docs/RENDER_PIPELINE.md).
7. **The verifier stays independent** and invariants 1–7 of CLAUDE.md hold. No operation, review layer or planner is
   added.

## Workstreams

| Item | Goal | Expected outcome |
| --- | --- | --- |
| O — composition | One request, one plan, one approval | `compose_submission` turns a conflicting submission into a chain; the window previews, approves and runs it; a layout's spec composes the same way; the model may submit stages; the prompt stops teaching two specs |
| P — build on the result | Follow-ups compose on the copies | `DocumentTask.working`, `generation`, `history`; the next conversation is told what was already done; `START_OVER` chip and route |
| Q — live events | The window shows the work | `llm.stream_observer_scope`; `DocumentTask.perform(events=)` emits status, activity, text and thinking deltas, message, plan, samples, results, question, cost; the thinking summary is requested (`display: summarized`) with a one-time fallback |
| R — posture and economics | Fewer, better model calls | Plain text plus submission in one turn; `suggested_replies`; every operation's selectors and parameters in the cached prompt (`catalog.op_sheet`); conversation at medium effort, recovery at high |
| S — render pipeline | Render once | `render/cache.py`; `word_worker.run_word_batch`; LibreOffice profiles reused |
| T — the web window | Beautiful and intuitive | `web/server.py` (docs/WEB_API.md), `web/shell.py`, `web/updates.py`; `frontend/` built into `web/static/`; the Qt window, PySide6, its tests and its packaging gone; CI builds and checks the page |
| U — docs and release | One product described once | README, CLAUDE.md, CONVERSATIONAL_DESKTOP.md, screenshots, release notes |

## Do not, in this round

- Add operations, planners, critics or review layers.
- Keep the Qt window behind a flag or an "advanced" mode.
- Let a chip or a choice place words in a document that the user did not type or pick.
- Let the model's stream, the window's events or the journal carry anything the verifier reads.

## Status — agents update this at the end of every session

| Item | Status | Owner / branch | Last note |
| --- | --- | --- | --- |
| O — composition | implemented; live run pending | claude/jolly-goldberg-wrlwva | 2026-10-20: `editspec.stage_cuts`/`compose_chain`/`plan_chain`/`merge_cuts`, `compiler.compose_submission` (a conflicting spec becomes a chain; the model's own stages are accepted; layouts compose per layout), Session/DocumentTask/layouts/review_display chain-aware; the prompt and catalog no longer say "offer a chain"/"two specs". `tests/test_composition.py`: font + template in one request → one plan in two steps, one approval, verified copies with the template's header and the font. Needs the owner's machine: a live request on real specs. |
| P — build on the result | implemented; live run pending | claude/jolly-goldberg-wrlwva | 2026-10-20: working set, generation, history, `START_OVER`; the journal keeps them; results keyed by the attached original. `tests/test_live_window.py`. |
| Q — live events | implemented | claude/jolly-goldberg-wrlwva | 2026-10-20: `llm.stream_observer_scope`, `DocumentTask.perform(events=)`, the plan before the preview, the pages before the visual check; `FakeClient(live=True)` plays scripted responses back as events. `tests/test_live_window.py`, `tests/test_web_server.py`. |
| R — posture and economics | implemented; live measurement pending | claude/jolly-goldberg-wrlwva, claude/sharp-hypatia-iwiacv | 2026-10-21: the system prompt (about 40k tokens with the tools, identical for every conversation) carries its own 1-hour cache breakpoint (`llm.SYSTEM_CACHE`), so a follow-up after a run, a start over or a changed attachment reads it instead of writing it again (automatic caching alone never wrote an entry there); one-shot calls (visual check, critic: `one_shot=True`) drop the automatic tail; the spend estimate prices 1-hour writes at 2× input and the call records show them. `tests/test_llm.py`, `test_conversation.py`, `test_vision.py`, `test_recovery_adaptive.py`; the real SDK's request body and usage checked over a mock transport. Needs the owner's machine: `cache_read_input_tokens` on the first call of a follow-up request in `conversation.json`, and whether replies after a pause of more than 5 minutes still re-write the history (then decide on a 1-hour history TTL). 2026-10-20: the compiler prompt's user-experience block rewritten; `suggested_replies` on both tools; `op_sheet` in the cached prompt (about 22k tokens) with `describe_op` kept for examples; conversation effort medium, recovery high; thinking summary requested with a one-time fallback. Needs the owner's machine: a live run to judge the model's sentences and the call count. |
| S — render pipeline | implemented; Word path pending | claude/jolly-goldberg-wrlwva | 2026-10-20: `render/cache.py` (content-hash cache, 2 GB, oldest out), `word_worker.run_word_batch` (one Word instance per batch, a deadline per file), LibreOffice profiles under the config folder; the preview and the run share the cache (warm run 1.9 s against 5.9 s cold on the fixtures). `tests/test_render_cache.py`, `test_render_word_batch.py`, `test_render_libreoffice_profiles.py`. Needs the owner's machine: a batch rendered by Word. |
| T — the web window | implemented; installed app pending | claude/jolly-goldberg-wrlwva | 2026-10-20: the server, shell and update flow; the React page (`frontend/`, built into `web/static/`, committed); the Qt window, PySide6, its tests and packaging deleted; `app.spec`, the installer, `installed_smoke.py` (over HTTP) and CI (Node 22 build, byte-identical static check) ported. The built page was driven against the real server in headless Chromium (plan, question and template scenarios) and the mock walk (`frontend/tools/flows.mjs`) passes. The adversarial review's findings (composition modes and cuts, layouts, chips as decisions, the working set's paths, the live channel's ids, window close, proxy, updates, token) are fixed with tests. Needs the owner's machine: the installed Windows app with WebView2 and Word. 2026-10-21 (claude/brave-ramanujan-w4dwzt): the top bar follows Build-a-Spec's — a context line beside the title, the help topics How to use, Workflows, How it works and About in one tabbed dialog (Why trust it is the owner's next session), About taking over the Help menu and About box (Check for updates answers in place: `manual_result` is cleared when a manual check starts; `/api/about` carries `about.py`'s fields and the model), and a pill for an update set aside with Not now. `tests/help.test.ts`, `tests/test_gui.py`, the mock walk (`flows.mjs`, 46 checks) and About driven against the real server pass; the bar fits from 1000 to 1440 px in headless Chromium. Needs the owner's machine: the bar and the guide in WebView2 at Windows font scaling. |
| U — docs and release | implemented; live run pending | claude/jolly-goldberg-wrlwva | 2026-10-20: README, CLAUDE.md, docs/CONVERSATIONAL_DESKTOP.md, docs/WEB_API.md, docs/RENDER_PIPELINE.md, screenshots from the page, `docs/releases/unreleased.md`. Pending on the owner's machine: a live request over real specifications, the measured time and cost per request. 2026-10-21: Prepared v1.6.0 from latest master including merged PR #46; 59 frontend tests, 95 release/updater/web-server tests, byte-identical page rebuild, distribution checks and clean installed-wheel smoke passed; PR Linux/Windows and installed-app CI passed, with tag CI gating publication. Private-corpus replay, the installed WebView2/Word app and a live request with time, cost and diagnostics remain pending and disclosed in the release notes. |
| O — composition | implemented; live run pending | claude/jolly-goldberg-wrlwva | 2026-10-20 (review): the trial merge coalesces numbering fonts (the headline request is two steps on every real section, not three); a composed step declares the mode its plan gives it; a composed chain is never run in place; a layout's composed chain is what the accepted submission carries; a bound template op is listed first or refused, never reordered; a submission that is both steps and a question is refused. |
| — command line and test suite (owner request, outside O–U) | implemented; owner's machine pending | claude/serene-feynman-yemrv2 | 2026-10-21: Owner decision: the owner never uses the command line, so it goes. `cli.py` and the twelve modules only it reached (advisor, campaign, library, office profiles/QA/evidence, workflows, evaluations, workspace CLI) and `jobs/` deleted; `python -m spec_surgeon` opens the window; the installer ships one exe; reports, prompt and docs no longer name commands (removed-feature guides moved to `docs/history/`). Tests: 430 removed with that code, dead paths or duplicates (11 per-batch refusal-code tests became one over every operation, which found six codes with only the generic explanation); the review-round files folded into feature files (175 → 137 files, 4,114 → 3,684 tests); PR CI runs the Windows suite once. Suite green on Linux. Needs the owner's machine: the installed app without `spec-surgeon.exe`. |

## Resume here — 2026-10-20, session paused on the owner's usage limit

The branch `claude/jolly-goldberg-wrlwva` is pushed and PR #46 is open and mergeable at `257ef6f`. Every
workstream above is implemented, documented and tested; the owner wants the two unfinished checks completed
when the next session starts:

1. **The adversarial review of the fixes** (`review-the-fixes` workflow, six lenses over the review-fix
   commits f4811c2, d3d3e89, bce6733, 5172678, ed7ff58, e229d7a, fdcc062, 257ef6f, two skeptics per finding)
   was stopped before any reviewer reported. Re-run it (the script is saved under the session's workflows
   folder, or write it again from the plan in the PR body) and fix what survives verification, with tests.
2. **The first review's unfinished parts**: its test-integrity reviewer never reported, and its skeptic phase
   was stopped after two verdicts (both refuted findings the fixes already covered). Re-run the test-integrity
   lens alone; the other seven lenses' findings are fixed and need no second pass.
3. **CI on `257ef6f`** was running when the session paused (three jobs; GitGuardian green). The two earlier
   Windows failures were CRLF checkouts of the built page and its sources, fixed by `.gitattributes`; a red job
   on this head is this PR's to fix.
4. Then the owner's machine: the installed Windows app with WebView2 and Word, and one live request over real
   specifications (time and cost per request).
