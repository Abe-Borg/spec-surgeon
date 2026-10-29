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
  "header.stop",
  "header.connection",
  "header.export",
  "header.export-kinds",
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
  "documents.template",
  "documents.references",
  // Safe mode
  "safe.note",
  "safe.default",
  "safe.buttons",
  "safe.form",
  // the previews and the one button
  "preview.pages",
  "preview.marks",
  "preview.occurrences",
  "preview.left-untouched",
  "action.primary",
  "action.discard",
  "zoom.controls",
  // the copies and what comes after
  "results.copies",
  "results.folder",
  "results.start-over",
  "results.undo",
  "results.hand-off",
  "todo.card",
  "todo.put-back",
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
  "window.reload",
] as const;

export type EndUserCapabilityId = (typeof END_USER_CAPABILITIES)[number];

/** Typed helper for React's data-capability attribute. */
export const capability = <T extends EndUserCapabilityId>(id: T): T => id;
