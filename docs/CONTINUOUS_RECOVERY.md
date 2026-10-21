# Continue a request and reuse what worked

The conversation window continues into outcome planning when
the compiler cannot offer a supported method. A failed ordinary preview or a failed
custom-method trial also feeds its evidence into that investigation. The
conversation keeps the original request, actual answers, template and source pins.

The plan is carried out internally and its result is shown as the ordinary plan view, with
the targets, any exact wording placed and before/after pages; **Create edited copies**
approves it. Changed input bytes or altered plans require a fresh plan. Recovery first
tries deterministic implementations, then investigates unresolved files with inspection,
analysis and generated methods. Single-target ambiguities become source-bound choices in
the same chat. Free-text answers to investigation and critic questions resume the existing
recovery in the conversation window, retaining its evidence and ready files. Target ambiguity still requires a bound choice or a freshly reviewed plan. A
revision after completion creates a new reviewed outcome and a separate recovery folder.
Verified copies are exported with that same approval; originals remain unchanged.
Model-proposed wording still needs a visible user choice.

## Reusable implementations

Successful governed generated methods can now be stored in the bounded local repair
database. The source has a protocol version, engine version, SHA256, a 64 KiB limit,
goal shape, parameters, assumptions and the existing verification/feedback record.
Compressed source is data in the database, not an installed operation. It can contain
literal project text; the memory store is not anonymized. The window has no view of
it: there is no surface to inspect cases, give feedback or forget them (the command line
that had one was removed on 2026-10-21).

A related goal can retrieve a method even when its title, paragraph position,
formatting values or attached template changes. Each trial receives fresh job-local
copies of `pkg.goal` and `pkg.binding`, plus the existing pinned `pkg.inputs`. Only
methods with empty static `select` and `params` are eligible for automatic trials;
parameterized methods remain examples the writer can adapt. Empty parameters do
not prove portability: a hardcoded old index or literal fails the new goal checks.

Every reuse goes through the current source whitelist, sandbox, per-file planning,
ordinary verification and independent original-to-final outcome checks. Failed
methods and their source can inform a new implementation. A user's rejection blocks
that implementation across projects, including duplicate successful records. Cached
success never establishes current correctness, current wording approval or visual
approval. The generated-code sandbox remains a prototype process/audit/whitelist
guard, not an operating-system security boundary.

## Broader independently checked outcomes

`document_effects` now includes `paragraph_borders`, backed by the existing
`set_borders_shading` operation. It selects original paragraphs with `scope` and
`paragraphs`, takes `borders`, `shading`, optional `tracked` and `author`, and requires
an exact `expected_count` or `"all"`. Count includes already-satisfied targets.
Tracking defaults to false for this outcome, independent of selector shape.

The verifier has its own parameter grammar, target counting and source-derived
border/shading expectations. It checks the whole relevant paragraph properties,
revision accounting, selected scope and original-to-final residual. It does not
trust a generated method's assertions. Style-definition decoration and composition
with paragraph insertion/deletion or header/footer graph changes are outside this
family. Existing supported text, font, page, template, table, numbering and style
outcomes retain their own checks and composition restrictions.

## Evidence and remaining limits

Run the reproducible comparison from the repository with development dependencies:

```sh
python -m pytest -q -s tests/test_repair_methods.py -k related_holdout \
  --basetemp /tmp/spec-surgeon-reuse-evaluation
```

Use a disposable test directory: pytest owns `--basetemp`. Each case retains
`reuse-measurements.json`, development/holdout input hashes, and complete cold/warm
recovery records. Three holdouts vary the title, paragraph position and structure:
ordinary paragraphs, a table cell and a styled heading. The fixture forces a
built-in planning failure, learns a generated method on the development document,
then compares the same untouched holdout with memory disabled and enabled.
Each comparison uses an isolated memory store and records the two author calls
used to learn the method separately from holdout execution.

Both runs must complete and pass fresh goal checks. Cold runs use two scripted
author calls; warm runs use zero. The harness records elapsed engine time and zero
manual interventions within the fixture. Provider cost and human acceptance remain
unknown. Renderer and model critic are disabled to isolate reuse. Timings are
observations, not performance assertions; these are scripted fixtures, not live
provider or Word-review benchmarks. Separate tests reject stale inputs, altered
plans/source hashes, old targets, old wording, changed sandbox goal data and rejected
implementations, and exercise adaptation after a remembered method fails.

This is an integrated increment, not arbitrary DOCX capability. Recovery still needs
a supported independent outcome contract, valid source bindings and sufficient
resources. Multi-target ambiguity can require a clarified plan; unknown effects
must be reported precisely. Learning is local method retrieval/adaptation, not model
training. The next evidence step is a representative real corpus with human and visual
review, measuring completion, interventions, end-to-end time and spending with and
without remembered methods.
