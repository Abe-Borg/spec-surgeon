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
}

/** A document the user set aside at approval (the red X on its preview): it waits on the to-do list. */
export interface TodoItem {
  path: string;
  name: string;
}

export type ActionKind = "approve" | "continue" | "save" | "none";

export interface Action {
  label: string;
  token: string;
  kind: ActionKind;
  /** The previews can be marked: a document marked with the red X is set aside when the copies are created. */
  can_hold?: boolean;
}

export interface Cost {
  usd: number | null;
  text: string;
  calls: number;
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
