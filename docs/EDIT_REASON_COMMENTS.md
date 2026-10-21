# Approved edit reasons in Word comments

`add_review_comments` puts an explicitly reviewed reason beside the affected paragraph in the delivered Word file. It takes literal comment text and an explicit author; an optional `source` string becomes a second comment paragraph. Both strings are inserted verbatim, including surrounding spaces. If a source label is wanted, include that label in the approved source string. This operation does not generate explanations or export a model's internal rationale.

In the conversation window, ask for the comment and give its exact wording; the compiler places only the text you supplied. [The supplied example](../examples/specs/add-review-comments.json) selects one provision and includes its exact reason and source. The operation is untracked: Word comments are separate annotations, not tracked insertions into document wording. Adding annotations never accepts or rejects existing revisions.

```json
{
  "op": "add_review_comments",
  "select": {
    "scope": ["document"],
    "paragraphs": {"text_regex": "^A\\. Provide listed sprinklers\\.$"}
  },
  "params": {
    "text": "Retain the approved project requirement.",
    "source": "Source: approved coordination memo, item 3.",
    "author": "Abraham Borg",
    "initials": "AB",
    "expected_count": 1
  }
}
```

The existing targeted paragraph selector grammar is available, including exact `index_range` intervals and style/text predicates. Every selected paragraph receives the same approved comment. `expected_count` is an optional exact safety check; no matches fail unless `allow_missing: true` is explicit. A permitted zero-match run changes no bytes and creates no comments part.

## Approval and outcome verification

Governed recovery can propose a `review_comments` document effect with `select`, literal `params`, and an explicit `expected_count` (a number or `"all"`). The reviewed plan carries the complete contract, binds the original source and matched count, and is approved as exactly that plan (the preview in the conversation window). Ordinary edits have no separate outcome review; the preview approval and the independent verifier govern them. `approved_goal_words` exposes only the approved `text` and optional `source`; review rationale is never another source of wording.

This first version requires a **standalone comment contract and a separate recipe/chain stage after other edits**. Its independent proof compares the complete original document and comment graph. A combined contract with wording/style/paragraph changes would need a separately qualified composition proof and is refused rather than weakened. When reasons differ, create separate reviewed comment stages for their distinct paragraphs.

An unchanged source cannot pass a comment outcome when comments were requested. Missing annotations, wrong anchors, modified wording, corrupted existing comments or altered side metadata all fail verification. There is no successful-output fallback that silently omits requested comments.

## Document support and explicit refusals

Supported anchors are nonempty main-document paragraphs directly under the body or a table cell. Anchors encompass the complete paragraph and are siblings of existing run-level insertion/deletion wrappers, so those original revisions remain unchanged in both accept and reject views. A revised paragraph mark or table row/cell can remove its paragraph during revision resolution; those targets are refused.

The operation also refuses textboxes, content-control contexts, paragraph-level revision containers, and overlap with an existing comment range, including a range beginning in an earlier paragraph. This intentionally bounds the initial anchor model. Existing comments elsewhere in the file remain intact.

Both classic comments and existing modern comment metadata are supported. The implementation appends corresponding entries to `commentsExtended`, `commentsIds`, and `commentsExtensible` when those parts already exist, preserves existing replies/resolution state, and refuses incomplete/ambiguous graphs. Main, comment and metadata parts are found through relationships, including nonstandard locations or filename extensions. New parts use available names next to the resolved main part; IDs reserve existing comments, anchors and XML paragraph/durable identities before allocation.

New comments have no invented date. Existing timestamps remain unchanged. The operation does not add a modern metadata subsystem to a document that only uses classic comments.

## Independent evidence

The planner predicts new comment paragraphs and their exact text, the added reference in each body paragraph, changed/new package members, and zero revision deltas before applying anything. It does not run the transform to discover those predictions.

`verify/comment_additions.py` independently resolves relationships and original paragraph selections, derives IDs and the expected additions from the raw approved parameters, and checks whole document/comment/metadata/relationship/content-type trees. Its source-to-final outcome path does not accept the execution plan as authority. All other package data remains covered by the normal preservation checks. Because adding markup can change Word's markup margin, this operation does not claim unchanged rendered geometry.

Regression coverage includes ordinary and modern comments, preserved existing threads, source references, multiple targets, explicit no-ops, reviewed-plan approval and compilation, unusual part names/extensions, absent relationship parts, tracked accept/reject views, table-cell anchors, and planted defects in anchors, wording, existing comments, relationships, metadata and a target-selection bug shared by plan/apply. Automated package/schema checks do not replace final visual qualification in Microsoft Word; this Linux environment does not perform that UI check.
