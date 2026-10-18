# Project facts and approved standards

Project evidence lives inside the existing campaign project context, under
`project_evidence`. Legacy `PROJECT.json` properties remain supported. There is no
separate facts database or automatically learned standards list.

The desktop workspace's **Project facts and standards** editor shows active facts,
assumptions and superseded history. Adding or replacing a fact, or changing the
standards register, uses the existing campaign setters: the affected project's
prepared results are invalidated and its proposed plan becomes stale. Other projects
remain unchanged. Changes are saved immediately; closing the editor does not undo them.

The command line exposes the same operations:

```text
spec-surgeon evidence /path/to/workspace PROJECT_ID inspect
spec-surgeon evidence /path/to/workspace PROJECT_ID add fact.json
spec-surgeon evidence /path/to/workspace PROJECT_ID supersede old-fact-id replacement.json
spec-surgeon evidence /path/to/workspace PROJECT_ID standards register.json
```

The standalone equivalent is `python -m spec_surgeon.project_evidence_cli` followed
by the same arguments. `register.json` is the complete replacement register array.
An empty array explicitly clears that project's register.

## Fact format

```json
{
  "id": "occupancy-reviewed",
  "statement": "The storage room is Ordinary Hazard Group 2.",
  "scope": {"kind": "section", "value": "21 13 13"},
  "status": "confirmed",
  "source": {"kind": "user", "reference": "Owner review, October 18, 2026"},
  "confirmed_by": "Project reviewer"
}
```

Scopes are `{"kind":"project"}` or `discipline`, `section`, or `document` with an
exact `value`. Use the full source path for document scope. Sources identify a
`user`, `document`, or `model`, a nonempty reference, and optionally a SHA-256 pin.

An `assumed` fact has no `confirmed_by`. A `confirmed` fact requires an explicit
human reviewer. Model-origin facts may be recorded as assumptions and subsequently
confirmed by a human; no code promotes them automatically. A replacement requires
a new fact ID and human confirmation in the same scope. The original becomes
`superseded` with a reciprocal replacement link. Cycles, missing replacements,
duplicate IDs and incomplete scopes are refused.

Only active confirmed statements can contribute approved literal wording. Project
batch execution includes only project-wide statements. A per-document campaign run
may additionally include that document's exact scope and an unambiguous observed
section scope. Inferred disciplines do not grant wording authority. Assumptions,
historical statements, source references, reviewer names and register metadata do
not become placement authority. Full structured context remains available as
labelled evidence; the reviewed request and outcome contract still govern edits.

## Standards register format

```json
[
  {
    "id": "sprinkler-standard",
    "identifier": "NFPA 13",
    "edition": "2019",
    "basis": "Project permit register, item 4",
    "scope": {"kind": "project"},
    "source": {"kind": "user", "reference": "Reviewed project standards register"},
    "confirmed_by": "Project reviewer"
  }
]
```

Every entry must be explicitly user supplied with an edition and its project basis.
The report-only standards audit compares observed citations to applicable entries:
`match`, `mismatch`, or `unassessed`. Missing editions, absent register entries and
conflicting applicable entries remain unassessed. A project entry applies throughout
the project; narrower entries require an exact matching scope. The helper accepts
multiple explicit applicable scopes without guessing precedence among them.

The register does not determine the newest publication or jurisdictional adoption,
and a mismatch never triggers an edit. An explicit subsequent request must approve
any wording change.

Python entry points in `project_evidence.py`: `validate_context`, `set_fact`,
`supersede_fact`, `active_facts`, `approved_context_words`,
`set_standards_register`, and `audit_standards_register`. Campaign wrappers are
`set_project_fact`, `supersede_project_fact`, and `set_standards_register`.
