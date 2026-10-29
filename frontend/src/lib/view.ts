/**
 * The page's state: the server's view plus what a running job streams.
 *
 * `GET /api/state`, and the `done`/`error` frame that ends every job, carry
 * the whole view, and the page renders it (docs/WEB_API.md). While a job
 * runs, the stream's presentation frames (status, activity, thinking and
 * text deltas) fill a live reply bubble that the `message` frame finalizes;
 * a `thinking` status starts a new model call, and the words of the call
 * before it are set aside among the reply's steps (the reply a turn ends
 * with is its last call's words);
 * `plan`/`samples`/`results`/`question`/`cost` update the workbench at once,
 * and the view that ends the job replaces everything.
 *
 * Pure: a reducer over plain objects, so `node --test` can drive every event
 * type without React or a DOM.
 */
import type {
  Choice,
  JobEvent,
  Message,
  PreviewNote,
  Status,
  View,
} from "../types.ts";

/** The Worth knowing notes from the wire: each with its text, its document's name or "". */
export function notesOf(value: unknown): PreviewNote[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((n) =>
    n && typeof n === "object" && typeof n.text === "string" && n.text
      ? [{ name: typeof n.name === "string" ? n.name : "", text: n.text }]
      : [],
  );
}

/** One step of a reply's making, oldest first: a plain sentence of model activity ("Reading the template's
 *  header"), or the words an earlier model call of the same turn wrote along the way. */
export interface Step {
  kind: "activity" | "said";
  text: string;
}

/** The assistant bubble a running job writes into. */
export interface LiveReply {
  /** The words of the model call being written: the reply, once the call ends the turn. */
  text: string;
  thinking: string;
  status: Status | null;
  steps: Step[];
  /** Where in `steps` the model call being written began: its words, set aside when the next call starts, go
   *  back there, before the activity of the tools it called. */
  call: number;
}

/** What a finalized reply keeps beside the server's message: its thinking
 *  summary and its steps, shown folded above the message. */
export interface Aside {
  thinking: string;
  steps: Step[];
}

export interface UiState {
  view: View;
  /** True once `GET /api/state` has answered at least once. */
  loaded: boolean;
  /** The streaming bubble while a job runs; null otherwise. */
  live: LiveReply | null;
  /** The last job's failure (shown as an error bubble until the next job). */
  error: string | null;
  /** Thinking and activity kept per finalized message id. */
  asides: Record<string, Aside>;
  /** A local counter for optimistic user bubbles, so ids never collide. */
  localSeq: number;
}

export type UiAction =
  | { type: "view"; view: View }
  /** Only the `update` block moved (a check, a download in progress). */
  | { type: "update"; update: View["update"] }
  | { type: "job_started"; userText: string | null }
  | { type: "event"; event: JobEvent }
  /** The stream ended without `done` or `error` (a dropped connection); the caller refetches the view. */
  | { type: "job_ended" }
  /** The job could not even start, or the stream broke with an exception. */
  | { type: "job_failed"; message: string };

export const EMPTY_VIEW: View = {
  version: "",
  busy: false,
  stage: "empty",
  connection: { ready: false, origin: "none" },
  cost: { usd: 0, text: "", calls: 0 },
  attachments: [],
  messages: [],
  plan: null,
  questions: [],
  choices: [],
  chips: [],
  samples: [],
  occurrences: [],
  warnings: [],
  worth_knowing: [],
  blocking: false,
  results: [],
  // an older server says nothing about PDFs: PDF (Clean) is not held back, the server answers at the choice
  pdf: { available: true, renderer: "", reason: "" },
  export: { folder: "", unsaved: 0, copies: 0 },
  action: { label: "", token: "", kind: "none", can_hold: false },
  todo: [],
  focus: [],
  working: { generation: 0, from_originals: true, can_start_over: false, undo: "", undo_blocked: "", done: [] },
  budget: { paused: false, reason: "" },
  discard: "",
  update: { state: "none", version: "", notes: "", progress: 0, error: "" },
  mode: "default",
  modes: ["safe", "default"],
  template: { kind: "none", typed_slots: [], reason: "" },
  left_untouched: [],
  tutorial: { active: false, id: null, scenario: null, finished_version: 0 },
  safe_note_hidden: false,
};

export const INITIAL_STATE: UiState = {
  view: EMPTY_VIEW,
  loaded: false,
  live: null,
  error: null,
  asides: {},
  localSeq: 0,
};

export function emptyLive(): LiveReply {
  return { text: "", thinking: "", status: null, steps: [], call: 0 };
}

/** The bubble a job starts with: the working line shows at once, before the first frame names the work. */
export function startingLive(): LiveReply {
  return { ...emptyLive(), status: { kind: "working", text: "" } };
}

/**
 * A view from the wire, with every field the page reads present. A server
 * one release ahead or behind never crashes the page over a missing list.
 */
export function normalizeView(raw: unknown): View {
  const v = (raw && typeof raw === "object" ? raw : {}) as Partial<View> &
    Record<string, unknown>;
  const list = <T>(value: unknown): T[] => (Array.isArray(value) ? (value as T[]) : []);
  const obj = <T extends object>(value: unknown, fallback: T): T =>
    value && typeof value === "object" ? { ...fallback, ...(value as T) } : fallback;
  const plan = v.plan && typeof v.plan === "object" ? v.plan : null;
  return {
    version: typeof v.version === "string" ? v.version : "",
    busy: v.busy === true,
    stage: (typeof v.stage === "string" ? v.stage : "empty") as View["stage"],
    connection: obj(v.connection, EMPTY_VIEW.connection),
    cost: obj(v.cost, EMPTY_VIEW.cost),
    attachments: list(v.attachments),
    messages: list(v.messages),
    plan: plan
      ? {
          groups: list(plan.groups),
          targets: list(plan.targets),
          set_aside: list(plan.set_aside),
          stages: typeof plan.stages === "number" ? plan.stages : 1,
        }
      : null,
    questions: list(v.questions),
    choices: list<Choice>(v.choices),
    chips: list<string>(v.chips),
    samples: list(v.samples),
    occurrences: list(v.occurrences),
    warnings: list(v.warnings),
    worth_knowing: notesOf(v.worth_knowing),
    blocking: v.blocking === true,
    results: list(v.results),
    pdf: obj(v.pdf, EMPTY_VIEW.pdf),
    export: obj(v.export, EMPTY_VIEW.export),
    action: obj(v.action, EMPTY_VIEW.action),
    todo: list(v.todo),
    focus: list<string>(v.focus).filter((p) => typeof p === "string"),
    working: obj(v.working, EMPTY_VIEW.working),
    budget: obj(v.budget, EMPTY_VIEW.budget),
    discard: v.discard === "preview" || v.discard === "request" ? v.discard : "",
    update: obj(v.update, EMPTY_VIEW.update),
    mode: v.mode === "safe" || v.mode === "obliterate" ? v.mode : "default",
    modes: list<string>(v.modes).filter((m): m is View["mode"] => m === "safe" || m === "default" || m === "obliterate"),
    template: obj(v.template, EMPTY_VIEW.template),
    left_untouched: list(v.left_untouched),
    tutorial: normalizeTutorial(v.tutorial),
    safe_note_hidden: v.safe_note_hidden === true,
  };
}

/** The tour block as the page reads it: a server without one, or a malformed one, means no tour and never finished. */
export function normalizeTutorial(raw: unknown): View["tutorial"] {
  const t = (raw && typeof raw === "object" ? raw : {}) as Partial<View["tutorial"]> & Record<string, unknown>;
  return {
    active: t.active === true,
    id: typeof t.id === "string" && t.id ? t.id : null,
    scenario: typeof t.scenario === "string" && t.scenario ? t.scenario : null,
    finished_version:
      typeof t.finished_version === "number" && Number.isInteger(t.finished_version) && t.finished_version >= 0
        ? t.finished_version
        : 0,
  };
}

function localMessage(state: UiState, role: Message["role"], text: string): [Message, number] {
  const seq = state.localSeq + 1;
  return [{ id: `local-${seq}`, role, text, at: Date.now() / 1000 }, seq];
}

/** The live reply's thinking and steps, kept under a message id. */
function keepAside(asides: Record<string, Aside>, id: string, live: LiveReply | null) {
  if (!live) return asides;
  if (!live.thinking && live.steps.length === 0) return asides;
  return { ...asides, [id]: { thinking: live.thinking, steps: [...live.steps] } };
}

/**
 * `next` with every unchanged message (same id, role, text and time) replaced
 * by the object already on the page: each view arrives as fresh JSON, and a
 * memoized bubble given an equal but new object would parse its markdown again.
 */
export function keepShownMessages(shown: Message[], next: Message[]): Message[] {
  if (shown.length === 0) return next;
  const byId = new Map(shown.map((m) => [m.id, m]));
  return next.map((m) => {
    const old = byId.get(m.id);
    return old && old.role === m.role && old.text === m.text && old.at === m.at ? old : m;
  });
}

function withShownMessages(state: UiState, view: View): View {
  return { ...view, messages: keepShownMessages(state.view.messages, view.messages) };
}

export function reduce(state: UiState, action: UiAction): UiState {
  switch (action.type) {
    case "view": {
      const view = withShownMessages(state, action.view);
      // A view that says busy while no stream is attached (the page was
      // reopened mid-job) still gets a live bubble, so the window shows Stop
      // and a status rather than an idle page that refuses every click.
      const live = view.busy ? (state.live ?? startingLive()) : null;
      // An empty conversation (a new one) carries no reply to keep an aside for.
      const asides = view.messages.length === 0 ? {} : state.asides;
      // A stream cut mid-job left a failure note; the server says the job still runs, so the note was wrong.
      const error = view.busy ? null : state.error;
      return { ...state, view, loaded: true, live, asides, error };
    }
    case "job_started": {
      let messages = state.view.messages;
      let seq = state.localSeq;
      // A message or a choice voids whatever plan and pages were on screen (the server sets the old outcome
      // aside the moment it reads the reply); creating copies or continuing keeps them until the job's view.
      const reply = action.userText !== null;
      if (action.userText !== null) {
        const [message, next] = localMessage(state, "user", action.userText);
        messages = [...messages, message];
        seq = next;
      }
      return {
        ...state,
        localSeq: seq,
        error: null,
        live: startingLive(),
        view: {
          ...state.view,
          busy: true,
          messages,
          // The chips and choices belonged to the question just answered.
          questions: [],
          choices: [],
          chips: [],
          ...(reply ? { plan: null, samples: [], warnings: [], worth_knowing: [], blocking: false } : {}),
        },
      };
    }
    case "update":
      return { ...state, view: { ...state.view, update: { ...state.view.update, ...action.update } } };
    case "job_ended":
      return { ...state, live: null, view: { ...state.view, busy: false } };
    case "job_failed":
      return {
        ...state,
        live: null,
        error: action.message || "The work could not finish.",
        view: { ...state.view, busy: false },
      };
    case "event":
      return reduceEvent(state, action.event);
    default:
      return state;
  }
}

/** The live bubble as a new model call finds it: the words the last call wrote become a step, in the place that
 *  call began. */
function newCall(live: LiveReply): LiveReply {
  const said = live.text.trim();
  const thinking = live.thinking && !live.thinking.endsWith("\n\n") ? `${live.thinking.trimEnd()}\n\n` : live.thinking;
  const at = Math.min(live.call, live.steps.length);
  const steps = said ? [...live.steps.slice(0, at), { kind: "said" as const, text: said }, ...live.steps.slice(at)] : live.steps;
  return { ...live, text: "", thinking, steps, call: steps.length };
}

function reduceEvent(state: UiState, event: JobEvent): UiState {
  const live = state.live ?? emptyLive();
  switch (event.type) {
    case "status": {
      const status = { kind: event.kind, text: event.text ?? "" };
      // `retrying`: the attempt whose words are in the bubble was abandoned and is sent again from the start.
      if (event.kind === "retrying") return { ...state, live: { ...live, text: "", thinking: "", status } };
      // `thinking`: a new model call starts. The words of the call before it are not the reply the turn ends
      // with; they stay in the bubble as a step, and the new call's thinking starts a paragraph of its own.
      if (event.kind === "thinking") return { ...state, live: { ...newCall(live), status } };
      return { ...state, live: { ...live, status } };
    }
    case "activity": {
      const text = (event.text ?? "").trim();
      if (!text) return state;
      // The working line names the activity until the next frame says otherwise.
      return {
        ...state,
        live: { ...live, steps: [...live.steps, { kind: "activity", text }], status: { kind: "working", text } },
      };
    }
    case "thinking_delta":
      return { ...state, live: { ...live, thinking: live.thinking + (event.text ?? "") } };
    case "text_delta":
      // Text clears the status strip: the reply is being written now.
      return { ...state, live: { ...live, text: live.text + (event.text ?? ""), status: null } };
    case "message": {
      const message = event.message;
      if (!message || typeof message.id !== "string") return state;
      const messages = state.view.messages.some((m) => m.id === message.id)
        ? state.view.messages.map((m) => (m.id === message.id ? message : m))
        : [...state.view.messages, message];
      return {
        ...state,
        asides: keepAside(state.asides, message.id, state.live),
        view: { ...state.view, messages },
        // The bubble is finalized; a fresh one waits for whatever follows.
        live: emptyLive(),
      };
    }
    case "plan":
      // The pages on screen belong to the plan before this one; this plan's pages follow in `samples`.
      return { ...state, view: { ...state.view, plan: event.plan, samples: [], occurrences: [], warnings: [], worth_knowing: [], blocking: false } };
    case "samples":
      return {
        ...state,
        view: {
          ...state.view,
          samples: event.samples ?? [],
          occurrences: event.occurrences ?? [],
          warnings: event.warnings ?? [],
          worth_knowing: notesOf(event.worth_knowing),
          blocking: event.blocking === true,
        },
      };
    case "results":
      return { ...state, view: { ...state.view, results: event.results ?? [] } };
    case "question":
      return {
        ...state,
        view: {
          ...state.view,
          stage: "question",
          questions: event.questions ?? [],
          choices: event.choices ?? [],
          chips: event.chips ?? [],
        },
      };
    case "cost":
      return { ...state, view: { ...state.view, cost: event.cost } };
    case "done":
      return {
        ...state,
        view: withShownMessages(state, normalizeView(event.state)),
        live: null,
        error: null,
        loaded: true,
      };
    case "error":
      return {
        ...state,
        view: withShownMessages(state, normalizeView(event.state)),
        live: null,
        error: event.message || "The work could not finish.",
        loaded: true,
      };
    default:
      // Unknown frames are ignored so an older/newer server never crashes the page.
      return state;
  }
}
