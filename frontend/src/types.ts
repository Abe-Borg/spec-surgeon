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
 *  protect a document's content, offered only when Settings → Advanced shows it (docs/PLAN-2026-10-23-modes.md). */
export type Mode = "safe" | "default" | "obliterate";

/** What kind of header/footer template the attached template is (`recipes.template_kind`). */
export interface TemplateInfo {
  kind: "none" | "prepared" | "reference" | "unusable";
  /** A prepared template's placeholders the user types a value for. */
  typed_slots: string[];
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

export type RecipeFieldKind = "text" | "pairs" | "choice" | "check" | "picture" | "table" | "slots" | "roles";

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
export type RecipeInputs = Record<string, string | boolean | FindReplacePair[] | Record<string, string>>;

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
}

export interface Result {
  source: string;
  output: string;
  name: string;
  /** Obliterate mode: the checks this copy failed; it was delivered anyway. Empty for a verified copy. */
  unverified?: string[];
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
}

export interface Connection {
  ready: boolean;
  origin: "saved" | "environment" | "session" | "none";
}

export interface Working {
  generation: number;
  from_originals: boolean;
  can_start_over: boolean;
}

export interface Budget {
  paused: boolean;
  steps_per_pause: number;
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
  warnings: string[];
  blocking: boolean;
  results: Result[];
  action: Action;
  /** Documents set aside for more work, waiting on the to-do list (attached originals). */
  todo: TodoItem[];
  /** The documents of the pass over the to-do list, while the user works on them (attached originals); else empty. */
  focus: string[];
  working: Working;
  budget: Budget;
  update: Update;
  /** The window's mode and the modes it offers. */
  mode: Mode;
  modes: Mode[];
  /** The attached template, as a header/footer template. */
  template: TemplateInfo;
  /** What the last Safe-mode button left untouched (after its copies were created). */
  left_untouched: Untouched[];
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
  | { type: "samples"; samples: Sample[]; warnings: string[]; blocking: boolean }
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
  out_root: string;
  logs_dir: string;
  /** The colour scheme kept in settings.json: one of src/lib/theme.ts's ids. */
  theme: string;
  /** Claude's fast mode, kept in settings.json; off until the user turns it on. Absent from an older server. */
  fast_mode?: boolean;
  fast?: FastOffer;
  /** Settings → Advanced: the top bar offers Obliterate mode. Absent from an older server. */
  show_obliterate?: boolean;
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
