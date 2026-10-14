# Reporting

The scripts evaporate with the session, so the report is the durable audit artifact. The user reads these carefully.

## Style

Prose, concise, no fluff: what changed, the proof it's only that, judgment calls the user can veto, pointers for their review. When the user asks "how do I know you didn't hallucinate," the answer is the verification suite — be ready to explain it concretely. End with where to look: location and page for each change (note the LibreOffice render's page numbers can differ from Word's by one), what each spot should show in All Markup, and the one Ctrl+F that proves the class of defect is gone.

## Structure

Use this order; omit a section only when it is empty, and say so if it is empty because a check was N/A.

1. **What changed** — the enumerated diff list, verbatim from the prediction (before → after per block, part names, revision deltas). This is the list the verifier asserted, not a re-derivation from the output.
2. **What provably didn't** — the filled-in verification record from `verification-checklist.md`, every check named with its result and numbers. Pre-existing source defects (validator baseline errors, inert junk preserved byte-identically) are listed here, not fixed.
3. **Judgment calls** — anything decided without asking (file chosen as "latest", a collision renamed, a fill trimmed) and the default taken for anything the user can veto.
4. **Flagged for review** — stale cross-references, numbering shifts, template typos, pending tracked changes that a part replacement would have baked in, anything the contract forbade touching.
5. **Where to look** — per change: part/section, article or heading, page (with the renderer named), what it should show in All Markup; plus the single Ctrl+F.
6. **Delivery state** — path written, the identity written against (size/mtime), lock-file result, and the instruction to reopen fresh.

## Asking mid-job

Ask ONE question, lead with it, include a recommendation and the exact before → after text, then proceed on the answer. An inventory with the question buried at the bottom reads as stalling.
