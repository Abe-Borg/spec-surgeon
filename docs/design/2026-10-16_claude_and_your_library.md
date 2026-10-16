# Claude and your library — design note (2026-10-16)

**Status: approved by Abraham on 2026-10-16 ("let's go with your recommendations") and implemented in the same pull
request — `tests/test_library_choices.py` pins the rules of §7.**

Revised 2026-10-16 after Codex's review of this note (two findings on the pull request): the item Claude offers is
pinned by hash and re-checked at the pick, and the spec used is the snapshot the user saw (§2, §3, §4, test 10); a
saved operation that fails its trials at the terminal returns to the conversation instead of exiting (§5, test 11).

Today the library (`library.py`) works only by hand: *Save to library…* / *Your library…* in the window,
`spec-surgeon library` and `do --save-as` on the command line. Claude cannot see it. Phase 8 cut "Claude browsing
the library (`read_recipe`, recipe/operation choices)" because it complicates the approved-words rule.

Goal: when the request is something a saved item already does ("strip the headers and footers like last time"),
Claude finds the recipe or operation and offers it as a choice; picking it previews the saved item instead of a fresh
spec. The failure that matters most stays the same: no word reaches a client's document unless Abraham gave it or
picked the choice that shows it.

What follows are the decisions, each with a recommendation (**R**). Alternatives considered are listed so the choice
is a real one.

## 1. What Claude can read

**R: two read-only tools, chat only, plus a compact index of the library in the conversation's first message.**

- `list_library()` — every item: kind (recipe / operation), name, description, the request it was saved from, when
  it was saved; a recipe also: its op names, its mode, its template files (a missing one flagged) and *how many*
  words it places (never the words themselves here); an operation also: its op name and its proposal.
- `read_library_item(name)` — one item in full. A recipe: the spec exactly as saved (ops, params, mode, summary,
  assumptions, assertions), its `placed_words` listed under the sentence *"these words can be placed only if the user
  picks the choice that shows them"*, its templates with whether each exists, and a dry plan over THIS batch (the
  compiler's own gate, `compiler._dry_errors`: "plans cleanly on 2 of 2 structure patterns", or each pattern's
  refusal). An operation: its manifest without `op.py` (request, proposal, description, select/params, attempts, the
  brief it came from capped at 3,000 characters). Claude never sees generated code; it has nothing to do with it.
- **The index in the first message** (chat mode only): the `list_library` entries for up to 40 items under
  `YOUR LIBRARY:`, beside the batch profiles. Without it Claude must think to call a tool before it can say "you have
  a recipe for this"; with it the match is visible the moment it reads the request. Beyond 40 items the index says
  "N more — call list_library".
- **Sizes.** An index entry is 250–450 characters (description and request cut at 160 / 200); 40 entries ≈ 16,000
  characters (≈ 4,000 tokens) at worst, a typical library of ten ≈ 3,000. A tool result keeps the existing cap of
  20,000 characters (`Conversation._dispatch`); `list_library` says "M of N shown" when cut. Nothing is cached
  differently: the system prompt gains one fixed paragraph (`CHAT_NOTE`), the index lives in the per-conversation
  user message like the profiles.
- **Chat only.** The one-shot `do` (no terminal; `--answer`) has nobody to pick a choice, so `compiler.TOOLS` and
  `mode="compile"` stay exactly as they are (no index, no tools; a `recipe`/`operation` choice kind from Claude is
  dropped there like any kind it may not offer). The advisor's and the transform writer's tool lists are explicit and
  unchanged (`ADVISOR_TOOLS` is pinned by a test).

Alternative considered: tools only, no index (Claude rarely looks unprompted); index only, no tools (Claude would
offer a recipe without seeing what it does). Both rejected.

## 2. How a library item becomes a choice (rule 13)

**R: Claude only names an item; the app builds the choice from the library, and the words a recipe places are shown
on the choice — picking it is what approves them, exactly like a suggested wording today.**

- In `ask_user.choices` Claude may add `{"kind": "recipe" | "operation", "name": "<the exact name>", "detail":
  "<why it fits, optional>"}`. Two new kinds, `LIBRARY_CHOICE_KINDS`; they join `APP_CHOICE_KINDS` (carried out by
  the app, never sent to Claude as a reply) and are never in `CHOICE_KINDS_FROM_CLAUDE` (built from Claude's own
  label and wording).
- `conversation._choices_from` → `_library_choice(kind, name)`: the name is looked up **exactly** (casefolded) in
  the listing, within the named kind (`library.find`) — never a path, never a fuzzy match (rule 17). Then:
  - a **recipe** must load and validate (`library.load_recipe` → `editspec.load_spec` → `validate_spec`); every
    template it names must exist; every word it places must be visible (`clean_wording`'s rule — a recipe saved from
    a hand-written spec file could hold characters nobody can see); and it must dry-plan on this batch (at least one
    structure pattern plans, no fatal code — the gate every fresh spec passes);
  - an **operation** must load (`library.load_operation`: the sha256 of `op.py` equals its manifest's, the source
    passes the whitelist). The trials on the samples wait for the pick (seconds; see §4).
  - Any failure → **no choice**. It is said three ways: in the turn's `errors` (the record), to the user as an app
    line in the chat ("Claude offered a recipe called «X», but your library has none by that name — the choice was
    not shown" / "…its template file is no longer at C:\… — save the recipe again" / "…it plans on none of these
    files: [not-found] …" / "…it changed since it was saved"), and to Claude as an app note with the next message, so
    it does not believe it offered something it did not.
- **The item as offered is the item used.** The choice keeps a content hash of the item as it was read when the
  choice was built (a recipe: its spec file; an operation: `op.py` and `manifest.json`) and, for a recipe, the
  validated spec itself — the snapshot whose words the user saw. The pick re-reads the item and refuses when the
  hash differs ("your recipe «X» changed since Claude offered it — nothing was used; ask again, or use Your
  library…"), said to the user and to Claude, nothing changed. Even with the hash equal, the spec used is the
  snapshot, and `compiler.placed_words(spec) == Choice.words` is asserted before `_set_outcome` (false would be a
  defect: it propagates, nothing runs). So no replacement or insertion text can reach a preview or a run without
  having been shown on the choice that was picked. (Codex's first finding on this note.)
- **Built by code, from the library only.** Label: `Use your recipe “Permit stamp” — <description> (saved 2026-10-10
  from the request «stamp the cover ISSUED FOR PERMIT»)`; `Try your operation “Header distance” — … (tried on this
  batch's sample files first; edited copies only)`. `words` = `compiler.placed_words(recipe spec)` (deduplicated, in
  order) — the same function the traceability check uses, so the choice shows exactly what the check will look for.
  A `wording` Claude puts on a library choice is ignored and said. Claude's `detail` (or label) becomes the tooltip,
  shown as plain text, never the row.
- **`Choice.words`** (a list) replaces the single `wording` as the truth of what a pick approves: Claude's choices
  have one word, `words == [wording]`; a recipe has as many as it places (a stamp and a replacement are two). Picking
  appends **each as its own source** — a phrase can never be stitched across two of them (`untraceable` searches each
  source on its own). `wording` stays in the records and the existing tests. `conversation.json`'s `approved_words`
  lists them in order, as today.
- **Claude can see a recipe's words and still cannot use them.** `read_library_item` returns them, but `sources`
  gain them only when the user picks the choice; a spec Claude submits that places them before that is refused by
  `check_submission` → `untraceable` ("never gave them verbatim"), exactly as a suggested wording is refused until
  picked. This is the planted test of §7.

## 3. Using a recipe

**R: previewed as saved (its ops, params, mode, summary and assumptions untouched); the `request` field carries THIS
conversation's request; the conversation stays open, so "adapt it" is one reply away and Claude's revision goes
through every check a fresh spec does.**

- The pick is an app choice (`take_app_choice`: recorded in the chat, "the app carried it out; the outcome follows"
  queued for Claude; the recipe's words appended to `sources`). The app re-reads the recipe and refuses a file whose hash
  differs from the choice's (§2), takes the validated snapshot the choice holds, asserts its placed words are exactly
  the choice's `words`, makes it the session's outcome (`_set_outcome(spec=…, source=<the recipe's path>)` — not `Session.load_spec`, which
  ends the conversation), previews it, and tells Claude what the preview found (`_note_preview`, headed *THE PREVIEW
  OF YOUR RECIPE «NAME» (used as saved)*). The window and the terminal do what they do after any preview.
- **Why `request` is the current request:** CLAUDE.md rule 13 — the spec a conversation hands the app carries
  `Conversation.request`; the advisor and the next pass (`set_aside.json` → `retry`) count a spec's `request` as
  approved words. With the recipe's old request there, words typed for another project would count as approved here
  without being shown as exact words. The recipe file on disk is untouched; the request it was saved from is on the
  choice and in `read_library_item`.
- **May Claude adapt it?** Not before the preview: the first preview is the recipe. After it, the user may reply
  ("only the body sections", "the same but on these files") and Claude submits a **new** spec — validated,
  traceability-checked (the recipe's words are approved now, because the user picked the choice that showed them),
  dry-planned. Claude reading the recipe (`read_library_item`) is what lets it adapt well; reading never approves.
- **Template path gone:** the index and `read_library_item` flag it; the choice is not built (§2), and the user and
  Claude are told. (From *Your library…* outside a conversation the behaviour is unchanged: every file's preview
  shows `[bad-params] template file not found`.)
- **Does not plan on these files:** a recipe that plans on none of the batch's patterns is not offered (dry plan at
  choice time, the compiler's own gate, with the refusals said to Claude — it can offer to adapt instead: its own
  spec, with the recipe's words as a normal `wording` choice the user approves by picking). One that plans on some
  files is previewed like any spec: the preview lists the files set aside, Claude is told, a reply corrects.

Alternative considered: Claude adapts the recipe before the first preview (submits its own spec after reading it).
Rejected: the first preview would be Claude's version, not what the user saved, and the words would need a wording
choice anyway. Also: use the recipe's own `request`. Rejected for the reason above.

## 4. Using one of my operations

**R: the pick runs exactly `use_operation`'s trial-first rule (rule 17), with one difference — the conversation is
kept, because Claude offered it and must hear the outcome.** Shared code (`Session._use_saved_operation`), used by
*Your library…* (which still ends the conversation first) and by the chat.

- The pick voids the previous outcome (every reply and choice does — the window's Run key counts user turns), records
  the choice, then: the operation's folder hashed against the choice's (a changed `op.py` or manifest refuses, §2),
  `library.load_operation` (the manifest's sha256 and the whitelist checked again at pick time),
  `codegen.pick_samples` over the batch, `_prove` in a **new** session folder (`library-trial/NN`; the approval token and inputs taken BEFORE
  the trials), and only when every sample passes: `_use_folder`, `_show_generated` (preview, Claude's visual check
  when rendering, the token set last) and a note to Claude — *"Your operation «NAME» passed verification on the sample
  files; the user reviews its preview"* + the preview text. Run writes **edited copies only** (`run_generated`).
- A sample that fails: nothing becomes runnable, the session has no outcome, the conversation continues, Claude is
  told the refusal text (*"your operation «NAME» does not pass on these files — <sample>: V10 FAIL …"*) through a
  recorded outcome, `gui.OperationFailed`, handled like `TransformFailed` (the message, never a traceback; a defect
  stays a defect and is never told to Claude as a result).
- A tampered `op.py` never gets this far: it is refused at choice time (§2) and again by `load_operation` at pick time.

## 5. Window and terminal

- **Window** — a choice row built by `ui/chat.py` from the choice's `words` (never from Claude's text):

      Use your recipe “Permit stamp” — replaces the cover stamp (saved 2026-10-10 from the request «stamp the cover»)
      Exact words: «ISSUED FOR PERMIT 2026-11-02» — choosing approves these exact words for your documents.   [Use it]

  one `Exact words:` line per word; a formatting recipe shows none; an operation row ends with "tried on this batch's
  sample files first; edited copies only" and a **Try it** button. Claude's detail is the button's tooltip (escaped,
  plain text). After the pick: the Preview step's title reads *Your recipe “Permit stamp” (as saved)* / *Your
  operation “Header distance”* instead of the request / "AI-written transform"; the rail says *Recipe: Permit stamp*
  / *Your operation: Header distance*; *Save to library…* stays enabled (saving a recipe again under another name is
  a copy). The "Saved spec" bar is not shown for a recipe picked in the chat (the chat is where it is).
- **Terminal** (`do` / `retry` with a terminal): `_print_turn` prints `[#n] Use your recipe “Permit stamp” — …` and
  one `exact words: «…»` line per word. `#n` asks **"Approve these exact words for your documents: «A»; «B»? [y/N]"**
  (one prompt; a typed `n` declines and the conversation goes on; Ctrl-C / end of input stops, as today) — a recipe
  without words asks nothing. A recipe then takes the normal path: preview, visual check, "Run this on N file(s)?
  [y/N]", the token re-check, the run. An operation is tried **inside the conversation loop** (`cli._talk`):
  "Trying your operation 'NAME' on [samples]…"; a failing sample prints the refusal, notes it to Claude and returns to
  the reply prompt — the conversation continues, as in the window (§4; Codex's second finding on this note: exiting
  there lost the context) — and only a passing one ends the loop, for `cmd_do` to preview it: "Run it on N file(s)
  (edited copies only)? [y/N]", the token re-check, `run_generated` — the second half of `_do_codegen`, shared
  (`cli._do_saved_operation`). A recipe's preview, like a Claude-submitted spec's, is where a terminal conversation
  ends: the command line has no reply-after-preview loop today (the window has). `--save-as` keeps working (a recipe
  copied under a new name; an operation that passed here saved again).
- Records: `conversation.json` entries show `Chose: Use your recipe “…” — exact words: «…»`, the app's lines (a
  dropped choice, the outcome), `approved_words` with the recipe's words; `compile.json` is unchanged in shape; the
  session folders follow the existing rules (a used folder's records are frozen; a trial opens a new one).

## 6. System prompt, README, CLAUDE.md

- `compiler.CHAT_NOTE` (chat only) gains: *YOUR LIBRARY lists the user's saved recipes (specs they previewed before)
  and operations (custom transforms that passed). When the request matches one — the same change, by its name, its
  description or the request it was saved from ("like last time") — read it (read_library_item) and offer it:
  {"kind": "recipe"|"operation", "name": <the exact name>}. The app builds that choice and shows the user the exact
  words a recipe places; only the user's pick approves them. Never copy a recipe's words into a spec of your own
  before that pick. Offer your own reading beside it when both fit; a picked recipe is previewed as saved, and the
  user may then ask you for changes.* The `ask_user` schema: kinds `recipe` / `operation`, a `name` property.
- **README**: "Your library" → a new part *Claude and your library* (how an item is offered, what approves the words,
  a missing template, a recipe that does not plan, a failing operation, the window row and the terminal prompt);
  "Talk it through with Claude" (the two kinds); "How Spec Surgeon calls Claude → What Claude can see and do" (the
  two tools); the records; Tests. Nothing shortened.
- **CLAUDE.md**: rule 13 (library choices: named by Claude, built by code from the library, `Choice.words` each a
  source when picked, never Claude's wording; the recipe's `request` is the conversation's); rule 17 (a chat-picked
  operation follows the same trial-first rule, conversation kept; `OperationFailed`); "Where things are"
  (`library.index` / `describe`, `compiler._Batch.list_library` / `read_library_item`, `conversation._library_choice`,
  `gui.Session._use_saved_operation`, `cli._do_saved_operation`); the plan's Phase 8 status ("cut" → done, with the
  rule); the test count.

## 7. Tests (step 3; scripted `tests/fake_claude.py` only, no test calls Claude)

Planted, each failing without the code it pins:
1. **Words without the pick are refused.** Claude reads a recipe that places a stamp, submits a spec placing it →
   `REJECTED … never gave them verbatim`; it then offers the recipe; the user picks it → the recipe is previewed as
   saved, `approved_words` holds the stamp; a reply, and a revised spec placing the stamp is accepted.
2. **An invented name is no choice**: `{"kind":"recipe","name":"Nope"}` and `{"kind":"operation","name":<a recipe's
   name>}` → `turn.choices == []`, the errors say so, the user sees the app line, the next message to Claude carries
   the note.
3. **A tampered operation is refused** at choice time ("changed since it was saved"), nothing changes; **a saved
   operation that fails on this batch** (a monkeypatched `codegen.trial`) → `OperationFailed`, no outcome, no
   `outputs/`, Claude told, the conversation continues.
4. **Claude's `wording` on a library choice is ignored**: `{"kind":"recipe","name":…,"wording":"EVIL"}` → the choice's
   words are the recipe's; "EVIL" never reaches `sources`.
5. **Each word its own source**: a recipe placing «A» and «B», picked; a spec placing «A B» is still refused.
6. **Not offered, and said**: a recipe whose template file is gone; one that plans on none of these files; one whose
   words hold an invisible character.
7. **Chat only**: `mode="compile"` sends no index and no library tool; `TOOLS`, `ADVISOR_TOOLS`, `CODEGEN_TOOLS`
   unchanged; chat mode has both; the `request` of a picked recipe is the conversation's.
8. **The window**: the row shows the words with "choosing approves these exact words"; *Use it* previews the recipe
   and Run is enabled; *Try it* runs the trials first.
9. **The terminal**: `#n` asks for the words; `n` keeps the conversation going; `y` previews and reaches the Run
   question; an operation choice runs the trials, then its preview.
10. **A recipe edited between the offer and the pick is refused**: the words shown are the old ones; the pick re-reads
    the file, the hash differs, nothing changes, the user and Claude are told; and a snapshot whose placed words
    differ from `Choice.words` never reaches `_set_outcome` (planted: a snapshot swapped under the choice trips the
    assertion). The same for an operation whose `op.py` or manifest changed after it was offered.
11. **A terminal operation that fails its trials returns to the conversation**: `#n`, the trials fail, the refusal is
    printed and noted to Claude, the reply prompt comes back, and a typed reply reaches Claude with the note.

## 8. Not in this change

The one-shot compile (no picker); `run-op` / `run-recipe` commands (a saved item runs through the chat's choice, the
window's *Your library…* or `run-spec`); Claude saving, renaming or deleting library items (read-only); sharing.

## 9. Decisions to confirm

1. §1 — the index in the first message (R) rather than tools only.
2. §3 — a picked recipe is previewed as saved, with THIS request in `request` (R), rather than its saved request.
3. §3 — picking the recipe approves the words it places for this conversation (R: it is what lets "adapt it" work),
   shown on the choice as exact words.
4. §4 — the pick voids the previous preview before the trials, as every reply does (R), rather than keeping an old
   preview runnable while a saved operation fails.
5. §2 — a recipe that plans on none of these files is not offered (R), rather than offered and failed in the preview.
6. §2 — the item Claude offers is pinned by hash and the pick uses the snapshot the user saw, refusing a changed file
   (R), rather than re-reading the file at the pick and re-running every check on whatever it holds then (also safe,
   but the words shown and the words used would come from two reads).
