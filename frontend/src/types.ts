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

export type ActionKind = "approve" | "continue" | "save" | "none";

export interface Action {
  label: string;
  token: string;
  kind: ActionKind;
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

export type UpdateState = "none" | "available" | "downloading" | "ready" | "error";

export interface Update {
  state: UpdateState;
  version: string;
  notes: string;
  progress: number;
  error: string;
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
  working: Working;
  budget: Budget;
  update: Update;
}

/** The kinds the `status` frame names; anything else renders as "Working…". */
export type StatusKind =
  | "working"
  | "thinking"
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
  | { type: "error"; message: string; state: View };

export interface Settings {
  key_present: boolean;
  origin: Connection["origin"];
  can_remember: boolean;
  out_root: string;
  logs_dir: string;
}

export interface About {
  version: string;
  license: string;
  release_notes: string;
  credits: string[];
}
