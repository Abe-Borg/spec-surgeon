// Adapted from Build-a-Spec's frontend/src/lib/api.ts (copy-based reuse):
// the fetch helpers and `streamChat`'s shape, against Spec Surgeon's own
// contract (docs/WEB_API.md).
import type { About, BundleSaved, JobEvent, Mode, Recipe, RecipeInputs, Settings, Update, View } from "../types.ts";
import type { Report } from "./diagnostics.ts";
import {
  apiHeaders,
  apiUrl,
  imageSrc as imageSrcFor,
  resolveConnection,
  type ConnectionSetup,
} from "./connection.ts";
import { readSse } from "./sse.ts";
import { normalizeView } from "./view.ts";

/** One sentence for the user, with the HTTP status for the code that needs it. */
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

let setup: ConnectionSetup = { token: "", apiBase: "", shellVersion: "" };

/** Called once at startup with the shell's injection and the page's query string. */
export function configureApi(injected: unknown, search: string): ConnectionSetup {
  setup = resolveConnection(injected as Parameters<typeof resolveConnection>[0], search);
  return setup;
}

export function connectionSetup(): ConnectionSetup {
  return setup;
}

/** The URL of a preview image the view handed out, with the token appended. */
export function imageSrc(url: string): string {
  return imageSrcFor(setup, url);
}

const BUSY = "Spec Surgeon is busy with the current work. Wait for it to finish, or stop it.";

function messageFrom(data: unknown, status: number): string {
  if (data && typeof data === "object" && typeof (data as { error?: unknown }).error === "string") {
    const error = (data as { error: string }).error.trim();
    // The contract's one-word refusal (`{"error": "busy"}`) gets a sentence.
    if (error.toLowerCase() === "busy") return BUSY;
    if (error) return error;
  }
  if (status === 401) return "The page is not connected to its app. Close the window and open Spec Surgeon again.";
  if (status === 409) return BUSY;
  if (status === 404) return "That file is gone.";
  return `The app could not answer (${status}).`;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await fetch(apiUrl(setup, path), {
    ...init,
    headers: apiHeaders(setup, init.headers as Record<string, string> | undefined),
    cache: "no-store",
  });
  const data: unknown = await resp.json().catch(() => null);
  if (!resp.ok) throw new ApiError(resp.status, messageFrom(data, resp.status));
  return data as T;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

// --- The view --------------------------------------------------------------

export async function getState(): Promise<View> {
  return normalizeView(await request<unknown>("/api/state"));
}

export async function attach(paths: string[], template: boolean): Promise<View> {
  return normalizeView(await post<unknown>("/api/attach", { paths, template }));
}

export async function upload(files: File[], template: boolean): Promise<View> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  form.append("template", template ? "true" : "false");
  return normalizeView(await request<unknown>("/api/upload", { method: "POST", body: form }));
}

/** The native file dialog through the shell; null when there is none (501) — the caller then
 *  falls back to the browser's own file input and `upload`. */
export async function pick(kind: "documents" | "template" | "references"): Promise<string[] | null> {
  try {
    const answer = await post<{ paths?: unknown }>("/api/pick", { kind });
    return Array.isArray(answer.paths) ? answer.paths.filter((p): p is string => typeof p === "string") : [];
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 501) return null;
    throw failure;
  }
}

export async function remove(path: string, template: boolean): Promise<View> {
  return normalizeView(await post<unknown>("/api/remove", { path, template }));
}

/** A document off the to-do list without a pass over it: back among the documents the next request works on. */
export async function putBack(path: string): Promise<View> {
  return normalizeView(await post<unknown>("/api/put-back", { path }));
}

export async function startOver(): Promise<View> {
  return normalizeView(await post<unknown>("/api/start-over"));
}

export async function newConversation(): Promise<View> {
  return normalizeView(await post<unknown>("/api/new-conversation"));
}

/** Safe, Default or Obliterate (docs/WEB_API.md, `POST /api/mode`). Safe or Default is kept in settings.json for the
 *  next start; Obliterate never is, and needs `acknowledged`: the user confirmed its warning this time. */
export async function setMode(mode: Mode, acknowledged = false): Promise<View> {
  return normalizeView(await post<unknown>("/api/mode", acknowledged ? { mode, acknowledged } : { mode }));
}

/** Safe mode's "Try these in Default mode": the documents the last button left untouched go on the to-do list. */
export async function handOff(paths: string[]): Promise<View> {
  return normalizeView(await post<unknown>("/api/handoff", { paths }));
}

/** The Safe-mode buttons (GET /api/recipes), fetched once. */
export async function getRecipes(): Promise<Recipe[]> {
  const answer = await request<{ recipes?: unknown }>("/api/recipes");
  return Array.isArray(answer.recipes) ? (answer.recipes as Recipe[]) : [];
}

export async function openPath(path: string): Promise<void> {
  await post<unknown>("/api/open", { path });
}

/** Stop the running model call; the job's stream then ends with `done`. A
 *  409 (nothing running) means it likely already finished — safe to ignore. */
export async function stop(): Promise<void> {
  try {
    await post<unknown>("/api/stop");
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 409) return;
    throw failure;
  }
}

// --- The job stream --------------------------------------------------------

async function* streamJob(path: string, body: unknown): AsyncGenerator<JobEvent> {
  const resp = await fetch(apiUrl(setup, path), {
    method: "POST",
    headers: apiHeaders(setup, { "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const data: unknown = await resp.json().catch(() => null);
    throw new ApiError(resp.status, messageFrom(data, resp.status));
  }
  yield* readSse<JobEvent>(resp);
}

/** A typed message, or (`chip`) one of the model's suggested replies the user tapped: the user's decision, never
 * approved wording — the words are the model's (docs/WEB_API.md). */
export const send = (text: string, chip = false) => streamJob("/api/send", chip ? { text, chip: true } : { text });
/** A picked choice; ``wording``: the user's edit of its one drafted wording (the draft as offered sends none). */
export const choose = (choiceId: string, wording?: string) =>
  streamJob("/api/choose", wording === undefined ? { choice_id: choiceId } : { choice_id: choiceId, wording });
/** The one action. ``hold``: the previewed documents the user marked with the red X — they go on the to-do list and
 * the others get copies (docs/WEB_API.md). */
export const primary = (token: string, hold: string[] = []) =>
  streamJob("/api/primary", hold.length > 0 ? { token, hold } : { token });
/** A Safe-mode button and its form: the fixed spec is previewed, no AI call (docs/WEB_API.md). */
export const recipe = (id: string, inputs: RecipeInputs) => streamJob("/api/recipe", { recipe: id, inputs });

// --- Settings, about, updates, logs ---------------------------------------

export async function getSettings(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

export async function saveSettings(apiKey: string, remember: boolean): Promise<Settings> {
  return post<Settings>("/api/settings", { api_key: apiKey, remember });
}

export async function removeKey(): Promise<Settings> {
  return post<Settings>("/api/settings/remove-key");
}

/** Keep a colour scheme (src/lib/theme.ts) in settings.json for the next start. */
export async function saveTheme(theme: string): Promise<Settings> {
  return post<Settings>("/api/settings", { theme });
}

/** Settings → Advanced: show or hide Obliterate mode in the top bar; hiding it leaves the mode. */
export async function saveShowObliterate(on: boolean): Promise<Settings> {
  return post<Settings>("/api/settings", { show_obliterate: on });
}

/** Turn Claude's fast mode on or off: the next AI step uses it, and settings.json keeps it for the next start. */
export async function saveFastMode(on: boolean): Promise<Settings> {
  return post<Settings>("/api/settings", { fast_mode: on });
}

export async function getAbout(): Promise<About> {
  return request<About>("/api/about");
}

export async function getUpdate(): Promise<Update> {
  return request<Update>("/api/update");
}

export async function checkUpdate(): Promise<Update> {
  return post<Update>("/api/update/check");
}

export async function downloadUpdate(): Promise<Update> {
  return post<Update>("/api/update/download");
}

export async function installUpdate(): Promise<void> {
  await post<unknown>("/api/update/install");
}

export async function openLogs(): Promise<void> {
  await post<unknown>("/api/logs");
}

/** Zip the app's logs, conversations and recent run records (never a document) and show it in Explorer. */
export async function saveDiagnosticBundle(): Promise<BundleSaved> {
  return post<BundleSaved>("/api/diagnostics/bundle");
}

/** The page's own reports (lib/diagnostics.ts). `keepalive` lets the last one outlive the page. Never throws
 *  into the page: a failed report is dropped by the reporter. */
export async function postDiagnostics(reports: Report[], keepalive = false): Promise<void> {
  const resp = await fetch(apiUrl(setup, "/api/diagnostics"), {
    method: "POST",
    headers: apiHeaders(setup, { "Content-Type": "application/json" }),
    body: JSON.stringify({ reports }),
    cache: "no-store",
    keepalive,
  });
  if (!resp.ok) throw new ApiError(resp.status, "diagnostics refused");
}
