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
| R — posture and economics | implemented; live measurement pending | claude/jolly-goldberg-wrlwva | 2026-10-20: the compiler prompt's user-experience block rewritten; `suggested_replies` on both tools; `op_sheet` in the cached prompt (about 22k tokens) with `describe_op` kept for examples; conversation effort medium, recovery high; thinking summary requested with a one-time fallback. Needs the owner's machine: a live run to judge the model's sentences and the call count. |
| S — render pipeline | in progress | wip/render-pipeline (agent) | 2026-10-20: cache, batched Word worker and LibreOffice profiles being built against docs/RENDER_PIPELINE.md. |
| T — the web window | in progress | wip/web-frontend, wip/delete-qt (agents) | 2026-10-20: `web/server.py`, `web/shell.py`, `web/updates.py` and `gui.main` done and tested (`tests/test_web_server.py`); the React page and the Qt deletion with packaging/CI being built on their branches. Needs the owner's machine: the installed Windows app with WebView2 and Word. |
| U — docs and release | in progress | claude/jolly-goldberg-wrlwva | 2026-10-20: this plan; release notes drafted in `docs/releases/unreleased.md`. |
