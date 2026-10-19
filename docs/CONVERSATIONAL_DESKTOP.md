# The conversational desktop

Spec Surgeon opens one conversation. Add the Word documents to edit, optionally attach a
reference template, and describe the desired result. Dropping a file attaches that file;
it never selects the other documents in its folder. Documents from different folders can
be used together, including documents with identical filenames.
Neighboring project files and saved library catalogs are not automatically added to the
conversation. The engine's editing and verification capabilities remain available internally.

The app inspects the documents, asks about meaningful ambiguities, prepares a plan and
checks sample edits. Review the proposed changes and available before/after pages. The
**Create edited copies** action approves the displayed preview and executes its specification using
the existing independent verification and source-change guards. Originals are preserved.
Describe corrections in the same conversation; each new result uses a separate output
directory, so earlier copies remain available.

![The conversation starts with Word documents, an optional template, and a request](conversation-start.png)

![A readable plan and rendered before-and-after pages, followed by one action to create copies](conversation-preview.png)

## Decisions, not implementation settings

Questions identify actual passages or conflicting requirements. Reply in plain language
or use the numbered answers in chat. For an ambiguous passage selection, multiple numbers
can be separated by commas; the engine still enforces the required number of targets.

The ordinary plan is rendered from the same EditSpec the preview executes. Exact replacement and
inserted wording is visible. Expand **See the exact changes and passages** for every
change, setting, and source-bound template fill. JSON, approval hashes, operation catalogs,
recipes, investigation settings, and recovery workspaces are not part of the default UI.

Ordinary editing has one specification, one preview decision, and the independent engine
checks; there is no second model-derived outcome plan or delivery gate. Custom methods
are prepared internally and tested on samples before the user approves their preview.
Governed recovery retains its existing outcome review: **Prepare preview** is followed
by review of the actual preview before copies can be delivered. Visual findings are shown
with their file and page as part of that same preview decision. Changed documents, templates,
plans, or visual findings invalidate the displayed action.

Recovery from failed batch edits starts automatically, retaining delivered copies and
the original request and edit specification for unresolved files. Ready recovery copies can be saved while
questions about other files remain in the same conversation. The independent checks and
generated-method restrictions are unchanged.

## Connection and resource use

Typed requests require an Anthropic API key, configured once in **Settings**. On Windows,
the key can be stored in Credential Manager. Without a connection the composer retains
the draft and explains the missing setup; the app does not switch into a recipe browser.
The CLI continues to support offline deterministic operations and saved recipes.

The desktop uses a shared initial allowance of **$10 estimated model spending**, 48 calls,
16,000 output tokens per call, and one million total estimated tokens. The same ledger
covers conversation, custom preparation, visual review and recovery across turns. These
limits are internal defaults. If more work is needed, a single action explicitly allows
another estimated $10 and continues with the existing ledger. These estimates are not
guaranteed provider billing caps. Unknown or interrupted usage is handled conservatively.

While work is active, the same action button can request **Stop after current step**.
Cancellation takes effect before the next model call; an in-flight response or deterministic
file operation finishes normally. Elapsed time and estimated spend appear in the status line.
Rendered samples show their before/after page counts, including page growth.

## Saved conversations

The local configuration directory contains `conversation.json`: attachment paths,
messages, user decisions, accepted wording, result locations, spending accounting, and
the current recovery checkpoint location. It can contain document text. It contains no
API key, restored execution approval, or persisted visual acknowledgement.

On reopening, the conversation and result links return. Ordinary requests are replanned
against the current documents before new approval; recovery evidence can be reopened
and revalidated through the existing checkpoint loader. If the app stopped in the middle
of work before final usage was saved, continuation requires a new explicit allowance.
**New conversation** archives the previous local journal under `conversations/`.

## Capability and verification limits

This redesign does not make arbitrary DOCX requests independently verifiable. The
supported operations, composition limits, template rules, and generated-code
isolation limits still apply. Requests outside that coverage must be explained or
clarified, not silently approximated.

For an explicit rewriting request, the conversation may offer exact draft wording for
the user's approval. The compiler still rejects unapproved generated wording. Draft
approval does not authorize invented project facts, technical requirements, or citations.

Rendering is requested automatically. If Word or LibreOffice cannot provide usable page
evidence, the UI explicitly says appearance was not checked. Automated checks do not
constitute human visual acceptance in Word.

The old four-step window remains as `ui.app.build_legacy_window` for regression coverage
of existing integrations. There is no legacy-mode switch in the product. Both desktop
entry points use the conversational window. CLI interfaces remain unchanged.

Validation uses real generated DOCX fixtures, scripted provider responses, actual engine
and verifier execution, and offscreen Qt interactions. These checks do not establish
live-provider success rates or customer acceptance of rendered Word layouts.
