// Adapted from Build-a-Spec's frontend/src/lib/api.ts (copy-based reuse):
// the fetch helpers and `streamChat`'s shape, against Spec Surgeon's own
// contract (docs/WEB_API.md).
import type { About, JobEvent, Settings, Update, View } from "../types.ts";
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

export async function startOver(): Promise<View> {
  return normalizeView(await post<unknown>("/api/start-over"));
}

export async function newConversation(): Promise<View> {
  return normalizeView(await post<unknown>("/api/new-conversation"));
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

export const send = (text: string) => streamJob("/api/send", { text });
export const choose = (choiceId: string) => streamJob("/api/choose", { choice_id: choiceId });
export const primary = (token: string) => streamJob("/api/primary", { token });

// --- Settings, about, updates, logs ---------------------------------------

export async function getSettings(): Promise<Settings> {
  return request<Settings>("/api/settings");
}

export async function saveSettings(apiKey: string, remember: boolean): Promise<Settings> {
  return post<Settings>("/api/settings", { api_key: apiKey, remember });
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
