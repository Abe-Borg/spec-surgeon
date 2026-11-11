/**
 * The window's end-user capabilities, one stable id each (adapted from Build-a-Spec's
 * frontend/src/lib/capabilities.ts, copy-based reuse).
 *
 * A control in the page declares the capability it gives the user through `data-capability`; the guided tour's
 * manifest (lib/tour.ts) names the same ids on its steps. `tests/tour.test.ts` holds the two sets equal in both
 * directions, so a control cannot ship untaught and a step cannot describe a control the page does not have.
 */
export const END_USER_CAPABILITIES = [
  // the tour itself
  "tour.start",
  "tour.controls",
  "tour.finish",
  // the top bar
  "help.topics",
  "header.modes",
  "header.spending",
  "header.context",
  "header.stop",
  "header.connection",
  "header.export",
  "header.export-kinds",
  // Obliterate's Export anyway: a clean kind made by Word, unchecked, for a marked copy the checked way left out
  "header.export-anyway",
  "header.new-conversation",
  "header.settings",
  // Settings
  "settings.api-key",
  "settings.spending-check",
  "settings.fast-responses",
  "settings.appearance",
  "settings.advanced",
  // the attachments
  "documents.add",
  "documents.folder",
  "documents.remove",
  "documents.remove-all",
  "documents.template",
  "documents.references",
  // Safe mode
  "safe.note",
  "safe.default",
  "safe.buttons",
  "safe.form",
  // saved recipes
  "recipes.save",
  "recipes.mine",
  "recipes.form",
  "recipes.manage",
  "recipes.edit",
  "recipes.add",
  // the previews and the one button
  "preview.pages",
  "preview.marks",
  "preview.occurrences",
  "preview.occurrences-all",
  "preview.left-untouched",
  "action.primary",
  "action.include-all",
  "action.discard",
  "action.stop",
  "zoom.controls",
  // the copies and what comes after
  "results.copies",
  "results.folder",
  "results.start-over",
  "results.undo",
  "results.done",
  "standing.cell",
  "standing.filter",
  "todo.card",
  "todo.work",
  "todo.waive",
  "todo.put-back",
  "todo.obliterate",
  "todo.failed-copy",
  // the conversation
  "chat.starters",
  "chat.composer",
  "chat.stop",
  "chat.choices",
  "chat.chips",
  "chat.pause",
  // help, about, updates and the rest
  "help.trust",
  "about.updates",
  "about.diagnostics",
  "update.banner",
  "obliterate.warning",
  // Obliterate's hands-on tools (off unless turned on with the mode)
  "handson.toggle",
  "handson.parts",
  "handson.save-part",
  "handson.replace-part",
  // Obliterate's named Word actions (a fixed menu Word itself runs on a copy; no AI when picked here)
  "word.actions",
  "word.run",
  "word.every",
  // Obliterate's rescue at attach (a refused document; no AI)
  "rescue.try",
  "rescue.password",
  "window.reload",
] as const;

export type EndUserCapabilityId = (typeof END_USER_CAPABILITIES)[number];

/** Typed helper for React's data-capability attribute. */
export const capability = <T extends EndUserCapabilityId>(id: T): T => id;
