# Unreleased

Changes since v1.10.0.

## Safe mode

- **Two modes.** The top bar's **Safe | Default** switch chooses how a change is asked for, and the window opens in
  the mode you last used (Safe the first time). Default mode is the conversation, as before.
- **Safe mode is buttons, with no AI.** Match headers and footers to template, remove headers and footers, add or
  remove a watermark, swap the logo, match font to template, find and replace, accept all tracked changes, remove
  comments, highlighting, hidden text or empty paragraphs, name the copies by section number and title, set the
  document title property, and turn off Track Changes. Each has a short form, then the same preview, checks and
  **Create edited copies** as Default mode. No model is called, so Safe mode needs no API key and spends nothing.
- **Clean copies unless you ask.** Find and replace, removing hidden text and removing empty paragraphs offer *Show
  my changes as tracked changes in Word*; it is off each time.
- **Both kinds of template.** The template buttons take a prepared template (yellow-highlighted or `{{NAME}}`
  placeholders) or an ordinary specification; the section number and title come from each document, any other
  placeholder's value is typed in the form.
- **What a button cannot do is said, and can go to Default mode.** A document a button cannot change exactly is left
  untouched with the reason, beside the copies; **Try these in Default mode** puts it on the to-do list there.

## Numbering conversions

- `convert_numbering` can leave numbered table cells as they are (`select.include_tables: false`) and can set aside a
  file where any converted number would count differently (`counters: "keep"`) instead of flagging it.
- `convert_typed_numbers` with `match: "counters"` converts typed numbers whose values are the list's at that place,
  in another look than the template's: "1.01" into a "1.1" list, "PART 1 –" with an en dash into "PART 1 -". A typo, a
  skip or a duplicate still sets the file aside, and each number the list shows otherwise than typed is flagged.

## Match body formatting to template

- A Safe-mode button, *Match body formatting to template*, makes each document look like your template without
  touching a word or a table: the template's styles for the section number line, the title, the outline (PART,
  articles, paragraphs, subparagraphs) and END OF SECTION, its numbering (every number keeps its value; "1.01" shows
  as "1.1"), its page size and margins, its font, and paragraph spacing, indents and alignment set by the styles. The
  form shows the template's style for each part, read from the template and changeable. Documents it cannot match are
  left untouched with the reason and can be handed to Default mode. No AI call.
- Fixed: creating the copies of a plan per layout whose layout ran as ordered steps stopped with an error.

## Obliterate mode (hidden)

- **A third mode, off the switch until you show it.** Settings → **Advanced** → *Show Obliterate mode* (a second
  click) adds **Obliterate** to the top bar. Choosing it shows a warning every time — what it grants, what it costs,
  what still holds — and turns on only after you tick *I understand*. The app never opens in it: a window closed in
  Obliterate opens in Default.
- **What it lifts.** Claude may write wording itself (it is in the plan you approve); a custom method may change
  anything Word stores, not only formatting; and a copy that fails a check is delivered anyway, marked
  **UNVERIFIED** with the checks it failed, instead of being held back. No automatic recovery runs in this mode.
- **What it keeps.** Copies only — your originals are never written; a source that changed is never delivered over;
  the custom method's sandbox and limits; and the independent checks on every copy, their results kept beside it.
- Entering or leaving it voids the plan on screen; Continue plans the request again under the new rules.
