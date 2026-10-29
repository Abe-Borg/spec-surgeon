/**
 * The view the server sends (docs/WEB_API.md, "The view") and the frames of
 * the job stream. The page never derives state: every `done`/`error` and
 * `GET /api/state` carries the whole view, and the page renders it.
 */

export type Stage = "empty" | "question" | "preview" | "done" | "budget" | "resume";

export type AttachmentKind = "document" | "template" | "reference";
export type AttachmentState = "ok" | "note" | "refused";

export interface Attachment {
  path: string;
  name: string;
  kind: AttachmentKind;
  state: AttachmentState;
  notes: string[];
  reason: string;
  current: string;
}

export type Role = "user" | "assistant" | "app";

export interface Message {
  id: string;
  role: Role;
  text: string;
  at: number;
}

export interface Change {
  title: string;
  summary: string;
  details: string[];
}

export interface PlanGroup {
  id: string;
  label: string;
  count: number;
  files: string[];
  changes: Change[];
  assumptions: string[];
}

export interface Target {
  title: string;
  source: string;
  details: string[];
}

export interface SetAside {
  name: string;
  reason: string;
}

/** A document the last Safe-mode button left without a copy, and why; offered to Default mode. */
export interface Untouched {
  path: string;
  name: string;
  reason: string;
}

/** Safe: the buttons, no AI call. Default: the conversation. Obliterate: the conversation without the guardrails that
 *  protect a document's content, offered only when Settings → Advanced shows it (CLAUDE.md, Modes). */
export type Mode = "safe" | "default" | "obliterate";

/** What kind of header/footer template the attached template is (`recipes.template_kind`). */
export interface TemplateInfo {
  kind: "none" | "prepared" | "reference" | "unusable";
  /** A prepared template's placeholders the user types a value for. */
  typed_slots: string[];
  /** A prepared template's placeholders each document fills itself, by what it fills them with. */
  slot_roles?: Record<string, "number" | "title">;
  reason: string;
  /** What "Match body formatting to template" reads from the template (template_roles.detect). */
  body?: BodyRoles;
}

/** The template's style for each part of a section, read without a model; ``problems`` say why it cannot be used. */
export interface BodyRoles {
  roles: Record<string, string>;
  styles: { id: string; name: string }[];
  labels: Record<string, string>;
  order: string[];
  page: string;
  font: string;
  problems: string[];
}

export type RecipeFieldKind =
  | "text"
  | "lines"
  | "pairs"
  | "choice"
  | "check"
  | "picture"
  | "table"
  | "slots"
  | "roles";

export interface RecipeField {
  name: string;
  kind: RecipeFieldKind;
  label: string;
  required: boolean;
  default?: string | boolean;
  options?: { value: string; label: string }[];
  help?: string;
  placeholder?: string;
  /** picture: the label of the choice that names no file ("Every picture in the headers"). */
  optional_choice?: string;
  /** Consecutive fields with the same row sit side by side (the four page margins). */
  row?: string;
}

/** One Safe-mode button (GET /api/recipes). */
export interface Recipe {
  id: string;
  label: string;
  group: string;
  summary: string;
  /** What must be attached first: "template", "picture". */
  needs: string[];
  fields: RecipeField[];
}

export interface FindReplacePair {
  find: string;
  replace: string;
}

/** A button's form as the page sends it: field name → its value. */
export type RecipeInputs = Record<string, string | boolean | string[] | FindReplacePair[] | Record<string, string>>;

export interface Plan {
  groups: PlanGroup[];
  targets: Target[];
  set_aside: SetAside[];
  stages: number;
}

export interface Choice {
  id: string;
  label: string;
  words: string[];
  detail: string;
  /** Claude's one drafted wording: the user may edit it before picking, and the edit is then the approved wording. */
  editable?: boolean;
}

export interface Sample {
  source: string;
  name: string;
  images: string[];
  pages_before: number | null;
  pages_after: number | null;
  passed: boolean;
  /** Actual failed checks in plain language; absent from older servers. */
  failures?: { check: string; reason: string }[];
  /** It did not pass while another sample did: Create makes no copy of it and creates the others; it is listed
   *  under Left untouched with its failed checks. Absent from older servers. */
  set_aside?: boolean;
  /** The page-layout verdict (check V7) in plain words: checked on the rendered pages, not asserted and why, or
   *  checked when the copies are created; null before the pages are rendered, absent from older servers. */
  geometry?: { status: "PASS" | "FAIL" | "NA"; note: string; at?: "run" } | null;
  /** A custom method's sample: the words its copy adds and removes, as the independent checker read them (its V2
   *  record), one entry per changed paragraph; [] when the method changes no words; null for an ordinary plan's
   *  sample (the chat's sentences say what changes) and for a sample without a record; absent from older servers. */
  text_changes?: TextChange[] | null;
}

/** One paragraph a custom method's copy changes, from the checker's own comparison (docs/WEB_API.md,
 *  `samples[].text_changes[]`). */
export interface TextChange {
  /** The part as the record names it ("word/footer1.xml"). */
  part: string;
  /** The place in plain words: "footer", "body paragraph 12". */
  where: string;
  /** The paragraph's ordinal in its part, from 0 (the source's; the copy's for a new paragraph); null when unknown. */
  paragraph: number | null;
  /** The stretches of words removed, in order. */
  removed: string[];
  /** The stretches of words added, in order. */
  added: string[];
  /** Set when the whole paragraph is new or gone. */
  block?: "new" | "gone";
  /** Set when the words sit in the copy's tracked changes (the checker's accept-all and reject-all views differ). */
  tracked?: boolean;
}

/** One find-and-replace occurrence the waiting plan found (docs/WEB_API.md, `occurrences[].items[]`). */
export interface OccurrenceItem {
  /** What the page sends back as `skip` to leave the occurrence as it is; the server resolves it from its own record. */
  key: string;
  part: string;
  /** The paragraph's ordinal in its part, from 0; shown from 1. */
  paragraph: number;
  start: number;
  end: number;
  /** Which part, in words: "the body", "a header" … */
  where: string;
  /** The article the body paragraph sits under ("2.01"), or "". */
  heading: string;
  before: string;
  match: string;
  after: string;
  find: string;
  replace: string;
  /** Unticked in the previewed plan: left as it is. */
  skipped: boolean;
  op_index: number;
}

/** The occurrences of one document of the waiting plan. */
export interface OccurrenceDoc {
  source: string;
  name: string;
  /** `refused`: the plan set the document aside; its items are every occurrence found, the refused one included. */
  status: "planned" | "refused";
  reason: string;
  items: OccurrenceItem[];
}

/** A note worth reading before the copies are made, listed under Worth knowing: the same words a change leaves
 *  elsewhere, a fill shown in another case than the template, an input that changed under the preview. */
export interface PreviewNote {
  /** The document it is about; "" for a note about the whole plan. */
  name: string;
  text: string;
}

export interface Result {
  source: string;
  output: string;
  name: string;
  /** Obliterate mode: the checks this copy failed; it was delivered anyway. Empty for a verified copy. */
  unverified?: string[];
  /** Where Export → Word (Tracked Changes ON) saved this copy, while that file is still there; "" while the copy
   *  lives only in the window's scratch folder. Absent from an older server. */
  saved_to?: string;
  /** The copy's bytes moved since it was saved to `saved_to` (opened from the window and edited in Word): it counts
   *  as unsaved again, and its link opens the working copy, the newer one. */
  changed_since_saved?: boolean;
  /** The file at `saved_to` no longer holds the bytes Export wrote (written over, truncated or edited since): the
   *  copy is not saved as it is now, and its link opens the working copy. */
  saved_file_changed?: boolean;
}

/** Export: where the last export went (any kind), how many edited copies are not saved as Word with their tracked
 *  changes yet (only such a saved copy survives closing the window), and how many edited copies Export would save
 *  (the working set and the copies a run still checking other documents delivered). */
export interface ExportState {
  folder: string;
  unsaved: number;
  copies: number;
}

/** Export's three kinds (docs/WEB_API.md, `POST /api/export`): `word` — Word (Tracked Changes ON), the copies as they
 *  are; `accepted` — Word (Tracked Changes OFF), every tracked change accepted; `pdf` — PDF (Clean), comments removed
 *  and tracked changes accepted. */
export type ExportKind = "word" | "accepted" | "pdf";

/** Whether this computer can make PDFs (Export → PDF (Clean) needs Word or LibreOffice), and with which program. */
export interface PdfInfo {
  available: boolean;
  /** "Microsoft Word" or "LibreOffice"; "" when neither is found. */
  renderer: string;
  /** Why PDF (Clean) cannot be chosen; "" when it can. */
  reason: string;
}

/** Why a document waits on the to-do list: the red X on its preview, the user asked the assistant, the plan
 *  refused it, or its copy could not be made. */
export type TodoWhy = "held" | "asked" | "refused" | "failed";

/** A document waiting on the to-do list, out of every plan until the user works on it or puts it back. */
export interface TodoItem {
  path: string;
  name: string;
  /** Absent in a view from before the to-do list said why (read as the red X). */
  why?: TodoWhy;
  /** One sentence saying why, as the server words it. */
  reason?: string;
}

export type ActionKind = "approve" | "continue" | "save" | "none";

export interface Action {
  label: string;
  token: string;
  kind: ActionKind;
  /** Why the server refuses this action, even with a current approval token. */
  disabled_reason?: string;
  /** The previews can be marked: a document marked with the red X is set aside when the copies are created. */
  can_hold?: boolean;
}

export interface Cost {
  usd: number | null;
  text: string;
  calls: number;
  /** Fast mode is in effect (Settings → "Fast responses" on, the model served fast, not refused for this key): the next
   *  AI step is priced at the fast table. Absent from an older server. */
  fast?: boolean;
  /** This request's estimated spending (a message, a pick, an approval, a button), null when the price table cannot
   *  estimate it. Absent from an older server. */
  request?: number | null;
  /** The spending check (Settings): the estimated dollars one request may spend before the work pauses; null when it
   *  is off. Absent from an older server. */
  limit?: number | null;
  /** The context gauge beside the spending (a measurement, never spending); null before the conversation's first step,
   *  absent from an older server. */
  context?: ContextGauge | null;
}

/**
 * How much of the model's context window the conversation fills (DocumentTask._context_view): `tokens` as Anthropic
 * counted them at the conversation's last step (the whole prompt plus the answer it keeps), `window` the model's
 * (null for a model the app's table lacks), `rollover` the app's estimate past which the history is sealed and goes on
 * in a fresh conversation, `live` while that conversation is still the open request's; once the request ends, the next
 * message starts a new one and the reading stands for the conversation before.
 */
export interface ContextGauge {
  tokens: number;
  window: number | null;
  rollover: number | null;
  live: boolean;
}

export interface Connection {
  ready: boolean;
  origin: "saved" | "environment" | "session" | "none";
}

export interface Working {
  generation: number;
  from_originals: boolean;
  can_start_over: boolean;
  /** The request the last run that delivered copies carried out, which Undo goes back before; "" when nothing can be
   *  undone. Absent from an older server. */
  undo?: string;
  /** Why it cannot be undone now (a preview waits, a request is open); "" when it can. */
  undo_blocked?: string;
  /** What you've done so far: each run that delivered copies, oldest first. Absent from an older server. */
  done?: DoneStep[];
}

/** One run the copies carry (docs/WEB_API.md, `working.done`). */
export interface DoneStep {
  /** The run's id: what Save as a recipe names. Absent from an older server. */
  id?: string;
  /** The button's sentence, or the request the user typed. */
  request: string;
  /** The Safe-mode button pressed (a recipe id); "" for a request. */
  recipe: string;
  /** How many of the documents attached now carry it, of how many. */
  documents: number;
  of: number;
  /** Each other document attached now, and why it does not carry it ("left untouched", "set aside", "attached later"). */
  not: { name: string; why: string }[];
  /** The saved recipe this run ran; "" for a button or a request. */
  name?: string;
  /** What a recipe saved from this run keeps: one step, or a recipe run's steps. Empty when it cannot be kept. */
  steps?: RecipeStep[];
  /** The Safe-mode buttons this run carried, its own or its recipe's (their tiles carry the checkmark). */
  buttons?: string[];
  /** Why a recipe cannot keep this run; "" when it can. */
  why_not?: string;
}

/** The mode a saved recipe belongs to: only buttons (Safe), a plan (Default), a plan approved in Obliterate mode. */
export type RecipeMode = "safe" | "default" | "obliterate";

/** One step of a saved recipe, or of a run a recipe could keep (src/spec_surgeon/saved_recipes.py, `step_view`). */
export interface RecipeStep {
  kind: "button" | "plan";
  /** The button's label; "Your request" for a plan. */
  label: string;
  /** The button's sentence, or the user's own words for a plan. */
  text: string;
  /** A plan's sentences, as the preview showed them; empty for a button. */
  plan: string[];
  /** What it needs attached beside the documents: "template", "picture", "table". */
  needs: string[];
  /** The words it places, as they were approved. */
  places: string[];
  /** A plan approved in Obliterate mode. */
  unguarded: boolean;
  /** A button step: the button and the form it runs with. */
  button?: string;
  inputs?: RecipeInputs;
  /** A plan step: the files it reads, by kind and name. */
  files?: { kind: string; name: string }[];
}

/** A saved recipe as the window lists it (docs/WEB_API.md, `saved_recipes`). */
export interface SavedRecipe {
  id: string;
  name: string;
  /** What it is for, as the user typed it; "" when nothing was. */
  note: string;
  mode: RecipeMode;
  /** When it was saved, and when it last ran (ISO 8601, UTC; "" never), on how many documents. */
  saved: string;
  last_run: string;
  last_documents: number;
  steps: RecipeStep[];
  /** What its steps need attached, each once. */
  needs: string[];
  /** It runs in this mode; else why not. */
  runs_here: boolean;
  why_not_here: string;
}

export type Discard = "" | "preview" | "request";

/** Why the work is paused: the request reached the spending check; the price table cannot estimate the model that
 *  answered, so the check cannot be kept; or the app closed while work ran. */
export type PauseReason = "limit" | "unpriced" | "interrupted" | "";

export interface Budget {
  paused: boolean;
  reason: PauseReason;
}

/** none · available · downloading · ready (downloaded while work ran; Restart to update) · installing (Setup
 * started; the app is closing) · updated (this start follows an update) · failed (an update that did not finish) ·
 * error. */
export type UpdateState = "none" | "available" | "downloading" | "ready" | "installing" | "updated" | "failed" | "error";

export interface Update {
  state: UpdateState;
  version: string;
  notes: string;
  progress: number;
  error: string;
  /** One sentence beside the state: the download's progress, the ready note, the hint after an update. */
  text?: string;
  /** `failed`: the install log that says why. */
  install_log?: string;
  /** The answer to the last check you asked for ("You're up to date — …", or why it could not check); "" while that
   *  check runs and after one that found an update, which `state` shows instead. */
  manual_result?: string;
}

/** The guided tour (docs/WEB_API.md): whether its practice workspace stands in for the user's conversation, the
 *  tour's id and the chapter state on screen, and the tour version this computer finished (settings.json). */
export interface Tutorial {
  active: boolean;
  id: string | null;
  scenario: string | null;
  finished_version: number;
}

export interface View {
  version: string;
  busy: boolean;
  stage: Stage;
  connection: Connection;
  cost: Cost;
  attachments: Attachment[];
  messages: Message[];
  plan: Plan | null;
  questions: string[];
  choices: Choice[];
  chips: string[];
  samples: Sample[];
  /** The find-and-replace occurrences of the waiting plan, per document (every document, not the sampled ones only);
   *  empty when no plan waits or it has no find-and-replace. Absent from an older server. */
  occurrences: OccurrenceDoc[];
  /** Every note of the checks; kept for diagnostics, listed on the page only under Please check (`blocking`). */
  warnings: string[];
  /** The notes worth reading before the copies are made: the one part of `warnings` the page lists, under Worth
   *  knowing. Absent from an older server. */
  worth_knowing: PreviewNote[];
  blocking: boolean;
  results: Result[];
  /** Whether Export → PDF (Clean) can run on this computer. */
  pdf: PdfInfo;
  export: ExportState;
  action: Action;
  /** Documents set aside for more work, waiting on the to-do list (attached originals). */
  todo: TodoItem[];
  /** The documents of the pass over the to-do list, while the user works on them (attached originals); else empty. */
  focus: string[];
  working: Working;
  budget: Budget;
  /** What Discard withdraws now: "preview" (a preview waiting for approval, in every mode), "request" (Default and
   *  Obliterate: a request still open with nothing to approve), or "" (nothing; work runs). */
  discard: Discard;
  update: Update;
  /** The window's mode and the modes it offers. */
  mode: Mode;
  modes: Mode[];
  /** The attached template, as a header/footer template. */
  template: TemplateInfo;
  /** What the last Safe-mode button left untouched (after its copies were created). */
  left_untouched: Untouched[];
  /** The guided tour's state; absent from an older server. */
  tutorial: Tutorial;
  /** The note about Safe mode above its buttons is folded to its one line (settings.json); false from an older
   *  server. */
  safe_note_hidden: boolean;
  /** The saved recipes (recipes.json), Obliterate's only while Obliterate mode is shown; empty from an older server. */
  saved_recipes: SavedRecipe[];
  /** Why a saved recipe cannot run now (a request is open, a preview waits in Default mode); "" when it can. */
  recipe_blocked: string;
}

/** The kinds the `status` frame names; anything else renders as "Working…". */
export type StatusKind =
  | "working"
  | "thinking"
  | "retrying"
  | "reading"
  | "planning"
  | "checking"
  | "previewing"
  | "rendering"
  | "looking"
  | "creating"
  | "recovering"
  | "resuming";

export interface Status {
  kind: StatusKind | string;
  text: string;
}

export type JobEvent =
  | { type: "status"; kind: string; text?: string }
  | { type: "activity"; text: string }
  | { type: "thinking_delta"; text: string }
  | { type: "text_delta"; text: string }
  | { type: "message"; message: Message }
  | { type: "plan"; plan: Plan }
  | { type: "samples"; samples: Sample[]; occurrences?: OccurrenceDoc[]; warnings: string[]; worth_knowing?: PreviewNote[]; blocking: boolean }
  | { type: "results"; results: Result[] }
  | { type: "question"; questions: string[]; choices: Choice[]; chips: string[] }
  | { type: "cost"; cost: Cost }
  | { type: "done"; state: View }
  /** `resend`: a request refused before it was recorded (no API key yet); the composer takes it back. */
  | { type: "error"; message: string; state: View; resend?: string };

export interface Settings {
  key_present: boolean;
  origin: Connection["origin"];
  can_remember: boolean;
  /** An app-managed session/saved key can be removed; external credentials are managed outside the app. */
  can_remove_key?: boolean;
  /** Where Export last saved (any of its kinds), and where its folder dialog opens; "" before the first export. */
  export_folder: string;
  logs_dir: string;
  /** The colour scheme kept in settings.json: one of src/lib/theme.ts's ids. */
  theme: string;
  /** Claude's fast mode, kept in settings.json; off until the user turns it on. Absent from an older server. */
  fast_mode?: boolean;
  fast?: FastOffer;
  /** Settings → Advanced: the top bar offers Obliterate mode. Absent from an older server. */
  show_obliterate?: boolean;
  /** The spending check: the estimated dollars one request may spend before the work pauses; null when it is off.
   *  Absent from an older server. */
  spending_limit_usd?: number | null;
  /** The amount the check starts at until the user changes it. */
  spending_default_usd?: number;
  /** The guided tour's version this computer finished (0: never). Absent from an older server. */
  tutorial_version?: number;
  /** The note about Safe mode above its buttons is folded to its one line. Absent from an older server. */
  safe_note_hidden?: boolean;
}

/** What Settings says about fast mode, from the server's own price table (llm.PRICES / llm.FAST_PRICES): the model
 *  the AI steps use, whether Anthropic serves it fast, prices in USD per million tokens as [input, output], the
 *  speed-up, and why the API refused fast mode for this key (null unless it did). */
export interface FastOffer {
  model: string;
  available: boolean;
  speedup: number;
  price_factor: number;
  standard_usd_per_mtok: [number, number] | null;
  fast_usd_per_mtok: [number, number] | null;
  refused: string | null;
}

/** `/api/diagnostics/bundle`: the zip saved (and shown in Explorer). */
export interface BundleSaved {
  path: string;
  name: string;
  files: number;
  skipped: number;
}

export interface Component {
  name: string;
  license: string;
  url: string;
}

/** GET /api/about. Everything but `version`, `license`, `release_notes` and `credits` came later: a server that
 *  predates a field leaves it out, and the About page reads what is there. */
export interface About {
  version: string;
  license: string;
  release_notes: string;
  credits: string[];
  name?: string;
  tagline?: string;
  github?: string;
  author?: string;
  email?: string;
  linkedin?: string;
  copyright?: string;
  license_name?: string;
  license_summary?: string[];
  /** The model id every AI step uses. */
  model?: string;
  components?: Component[];
}
