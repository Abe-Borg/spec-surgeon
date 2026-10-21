/**
 * The page's state: the server's view plus what a running job streams.
 *
 * `GET /api/state`, and the `done`/`error` frame that ends every job, carry
 * the whole view, and the page renders it (docs/WEB_API.md). While a job
 * runs, the stream's presentation frames (status, activity, thinking and
 * text deltas) fill a live reply bubble that the `message` frame finalizes;
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
  Status,
  View,
} from "../types.ts";

/** The assistant bubble a running job writes into. */
export interface LiveReply {
  text: string;
  thinking: string;
  status: Status | null;
  /** Plain sentences of model activity, oldest first ("Reading the template's header"). */
  activities: string[];
}

/** What a finalized reply keeps beside the server's message: its thinking
 *  summary and activity lines, shown folded under the message. */
export interface Aside {
  thinking: string;
  activities: string[];
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
  warnings: [],
  blocking: false,
  results: [],
  action: { label: "", token: "", kind: "none" },
  working: { generation: 0, from_originals: true, can_start_over: false },
  budget: { paused: false, steps_per_pause: 48 },
  update: { state: "none", version: "", notes: "", progress: 0, error: "" },
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
  return { text: "", thinking: "", status: null, activities: [] };
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
    warnings: list(v.warnings),
    blocking: v.blocking === true,
    results: list(v.results),
    action: obj(v.action, EMPTY_VIEW.action),
    working: obj(v.working, EMPTY_VIEW.working),
    budget: obj(v.budget, EMPTY_VIEW.budget),
    update: obj(v.update, EMPTY_VIEW.update),
  };
}

function localMessage(state: UiState, role: Message["role"], text: string): [Message, number] {
  const seq = state.localSeq + 1;
  return [{ id: `local-${seq}`, role, text, at: Date.now() / 1000 }, seq];
}

/** The live reply's thinking and activities, kept under a message id. */
function keepAside(asides: Record<string, Aside>, id: string, live: LiveReply | null) {
  if (!live) return asides;
  if (!live.thinking && live.activities.length === 0) return asides;
  return { ...asides, [id]: { thinking: live.thinking, activities: [...live.activities] } };
}

export function reduce(state: UiState, action: UiAction): UiState {
  switch (action.type) {
    case "view": {
      const view = action.view;
      // A view that says busy while no stream is attached (the page was
      // reopened mid-job) still gets a live bubble, so the window shows Stop
      // and a status rather than an idle page that refuses every click.
      const live = view.busy ? (state.live ?? { ...emptyLive(), status: { kind: "working", text: "" } }) : null;
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
        live: emptyLive(),
        view: {
          ...state.view,
          busy: true,
          messages,
          // The chips and choices belonged to the question just answered.
          questions: [],
          choices: [],
          chips: [],
          ...(reply ? { plan: null, samples: [], warnings: [], blocking: false } : {}),
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

function reduceEvent(state: UiState, event: JobEvent): UiState {
  const live = state.live ?? emptyLive();
  switch (event.type) {
    case "status": {
      // `retrying`: the attempt whose words are in the bubble was abandoned and is sent again from the start.
      const abandoned = event.kind === "retrying" ? { text: "", thinking: "" } : {};
      return {
        ...state,
        live: { ...live, ...abandoned, status: { kind: event.kind, text: event.text ?? "" } },
      };
    }
    case "activity": {
      const text = (event.text ?? "").trim();
      if (!text) return state;
      return { ...state, live: { ...live, activities: [...live.activities, text] } };
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
      return { ...state, view: { ...state.view, plan: event.plan, samples: [], warnings: [], blocking: false } };
    case "samples":
      return {
        ...state,
        view: {
          ...state.view,
          samples: event.samples ?? [],
          warnings: event.warnings ?? [],
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
      return { ...state, view: normalizeView(event.state), live: null, error: null, loaded: true };
    case "error":
      return {
        ...state,
        view: normalizeView(event.state),
        live: null,
        error: event.message || "The work could not finish.",
        loaded: true,
      };
    default:
      // Unknown frames are ignored so an older/newer server never crashes the page.
      return state;
  }
}
